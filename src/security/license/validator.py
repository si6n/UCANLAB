"""Ed25519 Asymmetric License Ticketing & 7-Day Offline Grace Period Validator.

Complies with MASTER_PLAN.md Section 3.2 (ADR-003).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.contracts.ports import ClockProvider, SystemClockProvider
from src.core.errors import LicenseError
from src.core.logging import get_logger
from src.safety.secret_provider import SecretProvider, get_default_secret_provider
from src.security.hwid.collector import INDETERMINATE_FINGERPRINT, generate_hardware_fingerprint
from src.security.hwm_format import (
    HWM_SECRET_NAME,
    LEGACY_HWM_SECRET_NAMES,
    parse_hwm_text,
    seal_hwm,
)
from src.security.license.claims import parse_license_claims, sha256_prefix

logger = get_logger("security.license")


@dataclass(slots=True, frozen=True)
class LicensePayload:
    """Decoded and verified license parameters."""

    user_id: str
    tier: str  # "FREE" | "PRO" | "ENTERPRISE" | "READ_ONLY"
    hardware_fingerprint: str
    issued_at: int
    expires_at: int
    features: tuple[str, ...] = field(default_factory=tuple)
    is_read_only: bool = False


class LicenseValidator:
    """Validates Ed25519 signed license tokens and manages offline grace period."""

    MAX_OFFLINE_GRACE_SEC: ClassVar[int] = 7 * 24 * 3600  # 7 Days (604,800 s)
    # SEC-02 (Batch B): the vault secret name is owned by
    # `src/security/hwm_format.py` and shared with `LicenseFlow` — the two used
    # to use different names ("LICENSE_HWM_HMAC_KEY" vs "LICENSE_HWM_KEY"), so
    # an HWM sealed by one class was quarantined as a lost key by the other.
    _HWM_KEY_NAME: ClassVar[str] = HWM_SECRET_NAME
    # FAZ 4 / review-#9 residual: `issued_at` was parsed but never compared to
    # the current time, so a token dated *in the future* was accepted (a
    # backend clock bug or a forged/rolled-forward issue date would sail
    # through). A tolerance absorbs legitimate clock skew between the issuing
    # server and this machine without admitting a meaningful future-dating.
    ISSUED_AT_SKEW_TOLERANCE_SEC: ClassVar[int] = 300  # 5 minutes

    def __init__(
        self,
        public_key: ed25519.Ed25519PublicKey,
        hardware_fingerprint: str | None = None,
        last_online_sync_ts: int | None = None,
        last_known_clock_ts: int | None = None,
        high_water_mark_path: Path | str | None = None,
        boot_realtime: int | None = None,
        boot_monotonic: float | None = None,
        allow_wildcard_license: bool | None = None,
        secret_provider: SecretProvider | None = None,
        clock: ClockProvider | None = None,
        require_hwm_persistence: bool = False,
        degrade_to_read_only_on_offline: bool = False,
    ) -> None:
        self.public_key = public_key
        self.degrade_to_read_only_on_offline = bool(degrade_to_read_only_on_offline)
        self.hardware_fingerprint = (
            hardware_fingerprint if hardware_fingerprint is not None else generate_hardware_fingerprint()
        )
        self.boot_realtime = boot_realtime
        self.boot_monotonic = boot_monotonic
        self.high_water_mark_path = Path(high_water_mark_path) if high_water_mark_path is not None else None
        # SEC-04 (Batch B): the anti-rollback anchor used to degrade silently
        # when no HWM path was wired ("session-only" rollback detection, plus a
        # CRITICAL log nobody reads). Callers that cannot tolerate that
        # degradation opt in here and get a hard failure instead.
        self.require_hwm_persistence = bool(require_hwm_persistence)
        #: HWM integrity keys tried when reading a persisted file, in order:
        #: canonical first, then the legacy pre-SEC-02 name. A legacy file
        #: therefore still verifies instead of being quarantined as "lost key".
        self._hwm_key_names: tuple[str, ...] = (HWM_SECRET_NAME, *LEGACY_HWM_SECRET_NAMES)
        # G3: all time readings flow through the injected clock; callers can
        # no longer hand verify_token an arbitrary timestamp (anti-rollback
        # bypass vector). Defaults to the real system clock.
        self.clock: ClockProvider = clock or SystemClockProvider()
        now_wall = self.clock.now_wall_ns() // 1_000_000_000
        self.last_known_clock_ts = last_known_clock_ts or now_wall
        # G2: grace-period anchor. Defaults to "now" only when nothing is
        # persisted; the HWM file below restores the real last-sync time so a
        # restart no longer resets the 7-day offline window.
        self.last_online_sync_ts = last_online_sync_ts or now_wall

        # Wildcard ("*") hardware licenses only via explicit constructor
        # opt-in (test fixtures). The former environment-variable fallback
        # let process environment loosen license binding and is removed (G4).
        # T57-B / L-4: the opt-in is a TEST-ONLY affordance — a frozen
        # (production) build must never accept a wildcard that unlocks every
        # machine, so refuse the constructor rather than rely on a comment.
        if allow_wildcard_license is True and getattr(sys, "frozen", False):
            logger.critical(
                "Wildcard license opt-in rejected in a frozen build (fail-closed)"
            )
            raise LicenseError(
                "Wildcard licenses are disabled in frozen builds.",
                code="WILDCARD_FORBIDDEN",
            )
        self._allow_wildcard = allow_wildcard_license is True

        # SEC-T40-1: sentinels the collector substitutes when a hardware read
        # fails. They are identical across hosts, so a fingerprint built from
        # them cannot identify a machine — see verify_token()'s fail-closed gate.
        self._indeterminate_markers = (
            "UNKNOWN_CPU",
            "UNKNOWN_DISK",
            "UNKNOWN_BIOS",
            "UNKNOWN_MAC",
            "FALLBACK-",
            "NON_WIN32-",
            # SEC-01 defense-in-depth: the collector's own "every component was
            # a sentinel" literal. Harmless before (the local/token gates use
            # exact-equality on the other markers, so it was never *accepted*),
            # but naming it here keeps the marker list complete.
            INDETERMINATE_FINGERPRINT,
        )

        # HWM HMAC key comes from the SecretProvider vault, never hardcoded (F-04)
        self._secret_provider = secret_provider or get_default_secret_provider()
        self._hwm_key = self._load_hwm_key()
        # T57-B / V-1: track whether the missing-persistence warning has been
        # emitted so the CRITICAL is logged once per instance, not per verify.
        self._hwm_persistence_warned = False
        # M-03: serializes the rollback check + HWM anchor update so
        # concurrent verify_token() calls cannot interleave (TOCTOU).
        self._clock_lock = threading.Lock()

        # Load persisted High-Water Mark from disk if present — fail closed on
        # anything that is not a valid HMAC'd timestamp (F-04).
        # SEC-02 (Batch B): parsing lives in `src.security.hwm_format`, the one
        # implementation shared with `LicenseFlow`. Canonical format:
        # "<hwm_ts>:<last_online_sync_ts>.<hmac_hex>" (HMAC over
        # "<hwm_ts>:<last_online_sync_ts>"). Legacy single-field "<ts>.<hmac>"
        # files are still accepted read-only (HMAC over "<ts>"), under either
        # the canonical or the legacy vault key name.
        if self.high_water_mark_path and self.high_water_mark_path.exists():
            try:
                # Read as BYTES and decode strictly. Opening with
                # `encoding="utf-8"` (what `read_text` does) leaves
                # binary/corrupted content raising UnicodeDecodeError, which
                # escaped this read path as an uncaught traceback instead of
                # the fail-closed `HWM_CORRUPT` the contract requires — a
                # corrupted HWM file must be refused, never crashed on, and
                # never silently ignored.
                content = self.high_water_mark_path.read_bytes().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise LicenseError(
                    "Corrupted HWM file (not valid UTF-8)",
                    code="HWM_CORRUPT",
                    cause=exc,
                ) from exc
            try:
                record = parse_hwm_text(content, self._verified_hwm_keys())
            except LicenseError as exc:
                if exc.code != "HWM_MAC_MISMATCH":
                    raise
                self._recover_lost_hwm_key()
                # H4 (3FABLE): adopt NOTHING from the unverifiable file —
                # including the sync anchor. The old "grace-period courtesy"
                # let an attacker who rolled the clock back and planted
                # `<hwm>:<sync>.<garbage>` keep a chosen offline grace anchor
                # past a license expiry. The grace window now restarts from a
                # conservative floor: now minus the maximum allowed offline
                # grace (or zero when unknown). The legacy single-field branch
                # previously missed this and re-anchored to "now" — both
                # layouts now share the conservative floor.
                self.last_online_sync_ts = self._conservative_grace_floor()
                return

            if record.sync_ts is not None and record.sync_ts > 0:
                self.last_online_sync_ts = record.sync_ts
            if record.hwm_ts > self.last_known_clock_ts:
                self.last_known_clock_ts = record.hwm_ts

    def _verified_hwm_keys(self) -> list[bytes]:
        """Every vault key an existing HWM file may legitimately verify under.

        SEC-02: the canonical key is tried first; the legacy pre-SEC-02 name
        (``LICENSE_HWM_KEY``) is tried when present so an installation whose
        HWM was sealed by the old `LicenseFlow` keeps working instead of being
        quarantined as a lost key.
        """
        keys: list[bytes] = [self._hwm_key]
        for legacy_name in LEGACY_HWM_SECRET_NAMES:
            if legacy_name == self._HWM_KEY_NAME:
                continue
            try:
                if self._secret_provider.has_secret(legacy_name):
                    keys.append(self._secret_provider.get_secret(legacy_name))
            except Exception:  # noqa: BLE001 — an unreadable legacy key just isn't tried
                continue
        return keys

    def _conservative_grace_floor(self) -> int:
        """Grace anchor adopted when an HWM file cannot be trusted.

        "Now minus the maximum allowed offline grace" (0 when the grace window
        is disabled) — never "now", which is exactly the untrusted value an
        attacker rolling the clock back wants adopted.
        """
        if self.MAX_OFFLINE_GRACE_SEC <= 0:
            return 0
        wall_now_s = self.clock.now_wall_ns() // 1_000_000_000
        return max(0, wall_now_s - self.MAX_OFFLINE_GRACE_SEC)

    def _recover_lost_hwm_key(self) -> None:
        """G5: handle an HWM file whose HMAC no longer verifies.

        This is the key-loss path: the vault key that signed the file is gone
        (reset/reinstall). Hard-failing would permanently lock a legitimate
        user out. Instead the unverifiable file is quarantined (renamed, never
        deleted — evidence preserved) and the in-memory HWM stays anchored to
        the machine clock; the file's unverifiable values are NOT adopted.
        """
        path = self.high_water_mark_path
        if path is None:
            raise LicenseError("High water mark tampered", code="HWM_TAMPERED")
        quarantine = path.with_suffix(path.suffix + f".lostkey-{int(time.time())}")
        try:
            path.replace(quarantine)
        except OSError as exc:
            logger.error("Could not quarantine unverifiable HWM file", extra={"error": str(exc)})
            raise LicenseError("High water mark tampered", code="HWM_TAMPERED") from exc
        logger.critical(
            "HWM HMAC failed (vault key lost); file quarantined, HWM re-anchored to machine clock",
            extra={"quarantine": str(quarantine)},
        )

    def _load_hwm_key(self) -> bytes:
        """Fetch (or mint) the canonical HWM integrity key from the vault.

        SEC-02: creates the canonical name only. The legacy name is never
        written — it exists purely as a read-compatibility fallback for files
        sealed by older builds.
        """
        if not self._secret_provider.has_secret(self._HWM_KEY_NAME):
            self._secret_provider.store_secret(self._HWM_KEY_NAME, os.urandom(32))
        return self._secret_provider.get_secret(self._HWM_KEY_NAME)

    def _is_indeterminate_fingerprint(self, fingerprint: str | None, *, allow_exact_sentinel: bool = False) -> bool:
        """True when a fingerprint is a fixed collector sentinel, not a device id.

        SEC-T40-1: ``src.security.hwid.collector`` substitutes constants
        (``UNKNOWN_CPU``, ``UNKNOWN_DISK``, ``UNKNOWN_BIOS``, ``UNKNOWN_MAC``) and
        a ``FALLBACK-<hostname>-<mac>`` string whenever a WMI read fails. None of
        those identify a specific machine: two different hosts that both failed
        the same read produce byte-identical fingerprints. Accepting one would
        let a single token unlock every machine in that state, so the caller
        must refuse instead of comparing.

        Matching is case-insensitive on purpose (review T41): the collector
        emits uppercase today, but nothing enforces that, and a lowercase
        ``unknown_cpu`` would otherwise slip past this gate while still naming
        no device.

        ``INDETERMINATE_FINGERPRINT`` ("INDETERMINATE_HARDWARE") is the
        collector's *own* explicit "no identity could be derived" value. It is
        detected by exact equality and is NOT treated as an indeterminate
        marker: the existing contract (``test_license_default_hwid_wiring``)
        requires the token/local equality comparison to decide that case, and
        widening the marker list to a value the collector legitimately returns
        would convert that control into a hard error. Synthetic test
        fingerprints that merely *embed* the literal (e.g.
        "UNKNOWN_CPU-INDETERMINATE_HARDWARE") are still refused — pass
        ``allow_exact_sentinel=True`` for the one value the collector emits
        verbatim.

        Wildcard (``*``) is handled separately by ``_allow_wildcard`` and is
        deliberately NOT treated as indeterminate — it is an explicit opt-in.
        """
        if not fingerprint:
            return True
        fp = fingerprint.strip().lower()
        if not fp or fp == "*":
            return False
        if allow_exact_sentinel and fp == INDETERMINATE_FINGERPRINT.lower():
            return False
        return any(marker.lower() in fp for marker in self._indeterminate_markers)

    def verify_token(self, token_str: str) -> LicensePayload:
        """Verify Ed25519 token signature, hardware fingerprint, and expiration.

        G3: the current time is read from the injected ClockProvider — the
        caller-side `current_ts` parameter is gone, so anti-rollback checks
        can no longer be handed an arbitrary timestamp.
        """
        # M-03: rollback check, drift cross-check, HWM persist, and the
        # in-memory anchor advance form ONE atomic critical section —
        # concurrent verify_token() calls must never interleave a stale
        # anchor write between the check and the update.
        with self._clock_lock:
            now = self.clock.now_wall_ns() // 1_000_000_000
            # Anti-Clock Rollback Check
            if now < self.last_known_clock_ts:
                logger.critical(
                    "System clock rollback detected!",
                    extra={"now": now, "last": self.last_known_clock_ts},
                )
                raise LicenseError(
                    "System clock manipulation detected. License validation halted.",
                    code="CLOCK_ROLLBACK_DETECTED",
                )

            # Monotonic counter drift / freeze cross-check (two-way, F-04)
            if self.boot_realtime is not None and self.boot_monotonic is not None:
                curr_mono = time.monotonic()
                mono_elapsed = curr_mono - self.boot_monotonic
                real_elapsed = now - self.boot_realtime
                if abs(mono_elapsed - real_elapsed) > 60.0:
                    logger.critical(
                        "Monotonic counter mismatch detected!",
                        extra={"mono_elapsed": mono_elapsed, "real_elapsed": real_elapsed},
                    )
                    raise LicenseError(
                        "System clock manipulation detected (monotonic counter mismatch).",
                        code="CLOCK_MONOTONIC_MISMATCH",
                    )

            # SEC-REVIEW: parse + Ed25519-verify BEFORE the seal/anchor
            # advance. The old order persisted `seal_hwm(...)` and rolled the
            # in-memory anchor forward BEFORE `public_key.verify`, so a forged
            # (bad-signature) token still advanced the anti-rollback floor —
            # an attacker could raise the HWM at will, permanently bricking a
            # legitimate older token. Every decode/verify error path now skips
            # the seal entirely.
            # Parse token: <payload_b64>.<sig_b64>
            parts = token_str.strip().split(".")
            if len(parts) != 2:
                raise LicenseError("Invalid license token format", code="INVALID_TOKEN_FORMAT")

            payload_b64, sig_b64 = parts[0], parts[1]

            try:
                payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
                sig_bytes = base64.urlsafe_b64decode(sig_b64.encode("ascii"))
            except Exception as exc:
                raise LicenseError(
                    f"Failed to decode license base64: {exc}",
                    code="TOKEN_DECODE_ERROR",
                    cause=exc,
                ) from exc

            # Cryptographic Signature Verification
            try:
                self.public_key.verify(sig_bytes, payload_bytes)
            except InvalidSignature as exc:
                # RFC 8785 (JCS) canonical verification fallback.
                # SEC-14: decode through the SHARED parser (never a local
                # `json.loads`), so the non-finite-constant rejection and the
                # object-shape gate are identical in both verifiers.
                verified_jcs = False
                try:
                    from src.security.license.claims import parse_license_json
                    from src.security.license.jcs import canonicalize
                    raw_obj = parse_license_json(payload_bytes, origin="JCS fallback payload")
                    jcs_bytes = canonicalize(raw_obj)
                    self.public_key.verify(sig_bytes, jcs_bytes)
                    verified_jcs = True
                except Exception:
                    pass

                if not verified_jcs:
                    logger.error("License token Ed25519 signature verification failed!")
                    raise LicenseError(
                        "License token signature is invalid or has been tampered with.",
                        code="INVALID_SIGNATURE",
                        cause=exc,
                    ) from exc

            # Persist high water mark to disk (G2: two-field format keeps the
            # grace-period anchor stable across restarts; HMAC covers both
            # fields; G6: temp+replace so a crash mid-write never truncates the
            # HWM).
            # SEC-C-005 / SEC-04: the in-memory anchor is only advanced AFTER a
            # successful persist — otherwise a failed write would silently roll
            # the anchor forward and mask a real clock-rollback on the next
            # restart. The old code logged the OSError and advanced anyway,
            # directly contradicting this comment; it now fails closed with
            # HWM_PERSIST_FAILED.
            if self.high_water_mark_path:
                # SEC-16 / SEC-02: one shared writer (owner-only 0o600 temp
                # file, fsync before the atomic replace). Raises
                # LicenseError(HWM_PERSIST_FAILED) on any I/O failure.
                seal_hwm(self.high_water_mark_path, now, self.last_online_sync_ts, self._hwm_key)
                self.last_known_clock_ts = now
            elif self.require_hwm_persistence:
                # SEC-04: the caller declared that a session-only anchor is not
                # acceptable — refuse instead of degrading anti-rollback to
                # in-memory-only detection.
                raise LicenseError(
                    "License anti-rollback requires a persistent high-water mark, "
                    "but no high_water_mark_path is configured (fail-closed).",
                    code="HWM_PERSIST_FAILED",
                )
            else:
                # No persistence configured: in-memory anchor advances directly.
                # T57-B / V-1: this silently degrades anti-rollback protection to
                # session-only — a restart re-anchors to the machine clock and a
                # rollback performed while the app was closed goes undetected.
                # That must never ship unnoticed, so make it loud (once), and
                # let callers opt into `require_hwm_persistence=True` for a hard
                # failure instead.
                if not self._hwm_persistence_warned:
                    self._hwm_persistence_warned = True
                    logger.critical(
                        "License anti-rollback high-water mark has NO persistence "
                        "path configured — clock-rollback detection is session-only "
                        "and resets on every restart. Pass high_water_mark_path in "
                        "production wiring (or require_hwm_persistence=True to fail closed).",
                    )
                self.last_known_clock_ts = now


        # Parse + validate JSON payload.
        # SEC-14 (Batch B): this used to be a key-presence check only, with
        # `features` coerced by `tuple(data.get("features", []))` — a JSON
        # string became a tuple of characters. SEC-03: `json.loads` ran with no
        # `parse_constant`, so a signed ticket carrying `NaN`/`Infinity`
        # reached the comparisons below, where `int > nan` is False and the
        # offline-grace branch (`now - anchor > MAX_GRACE`) also evaluated
        # False — an eternally valid license. Both verifiers now share
        # `parse_license_claims`, which rejects non-finite constants at parse
        # time and type-checks every field.
        data = parse_license_claims(
            payload_bytes,
            origin="license JSON payload",
            required_strings=("user_id", "tier", "hardware_fingerprint", "issued_at", "expires_at"),
        )
        payload = LicensePayload(
            user_id=data["user_id"],
            tier=data["tier"],
            hardware_fingerprint=data["hardware_fingerprint"],
            issued_at=data["issued_at"],
            expires_at=data["expires_at"],
            features=data["features"],
        )

        # Hardware Fingerprint Check (F-05: wildcard only in explicit test mode)
        #
        # SEC-T40-1 (fail-closed): the collector falls back to fixed sentinels
        # (UNKNOWN_CPU / UNKNOWN_DISK / UNKNOWN_BIOS / FALLBACK-*) whenever a WMI
        # read fails. Those strings are identical on every affected machine, so a
        # token carrying one of them would verify on ANY host — the hardware lock
        # would be dead. Refuse rather than compare a value that cannot identify
        # the machine.
        if self._is_indeterminate_fingerprint(self.hardware_fingerprint, allow_exact_sentinel=True):
            raise LicenseError(
                "Hardware identity could not be determined on this machine; "
                "license cannot be verified.",
                code="HARDWARE_INDETERMINATE",
            )

        # T57-B / L-5: apply the SAME fail-closed gate to the fingerprint the
        # TOKEN carries. Previously only the local side was inspected, so a
        # token bearing a collector sentinel (UNKNOWN_*/FALLBACK-*/NON_WIN32-*)
        # reached the `==` comparison — and two hosts that both failed the same
        # hardware read produce byte-identical sentinels, letting one cloned
        # machine's token unlock every machine in that state. `*` is an explicit
        # wildcard, not a sentinel, so `_is_indeterminate_fingerprint` returns
        # False for it and the wildcard branch below still works.
        if self._is_indeterminate_fingerprint(payload.hardware_fingerprint, allow_exact_sentinel=True):
            logger.warning(
                "License token carries an indeterminate hardware fingerprint",
                extra={"token_prefix": (payload.hardware_fingerprint or "")[:8]},
            )
            raise LicenseError(
                "License token carries an indeterminate hardware id; cannot verify.",
                code="HARDWARE_INDETERMINATE",
            )

        if payload.hardware_fingerprint == self.hardware_fingerprint:
            pass
        elif payload.hardware_fingerprint == "*" and self._allow_wildcard:
            logger.warning("Wildcard license accepted (TEST MODE ONLY)")
        else:
            logger.warning(
                "Hardware fingerprint mismatch",
                extra={
                    # SEC-C-004: only a truncated prefix is logged — the full
                    # fingerprint is a secret-grade device identity and must
                    # not be recoverable from log files.
                    "expected_prefix": self.hardware_fingerprint[:8],
                    "token_prefix": payload.hardware_fingerprint[:8],
                },
            )
            raise LicenseError(
                "License is locked to a different machine hardware ID.",
                code="HARDWARE_MISMATCH",
            )

        # Issue-date sanity (FAZ 4): a token issued in the future beyond the
        # allowed clock skew is corrupt or forged — fail closed rather than
        # honour an `expires_at` window that has not even started yet.
        if payload.issued_at > now + self.ISSUED_AT_SKEW_TOLERANCE_SEC:
            raise LicenseError(
                "License issue date is in the future beyond the permitted clock skew; "
                "refusing a not-yet-valid license (fail-closed).",
                code="LICENSE_FUTURE_DATED",
            )

        # Expiration Check
        if now > payload.expires_at:
            raise LicenseError(
                f"License expired at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(payload.expires_at))}.",
                code="LICENSE_EXPIRED",
            )

        # 7-Day Offline Grace Period Check
        offline_elapsed = now - self.last_online_sync_ts
        if offline_elapsed > self.MAX_OFFLINE_GRACE_SEC:
            logger.warning("Offline grace period expired", extra={"elapsed_days": offline_elapsed / 86400})
            if self.degrade_to_read_only_on_offline:
                # Aksiyon 33: Offline usage degrades to read-only diagnostics
                degraded_features = tuple(
                    f for f in payload.features
                    if not any(crit in f.lower() for crit in ("flash", "write", "tx", "actuator", "calibration"))
                )
                logger.warning(
                    "License degraded to READ_ONLY mode due to offline grace expiration",
                    extra={"user_prefix": sha256_prefix(payload.user_id)},
                )
                return LicensePayload(
                    user_id=payload.user_id,
                    tier="READ_ONLY",
                    hardware_fingerprint=payload.hardware_fingerprint,
                    issued_at=payload.issued_at,
                    expires_at=payload.expires_at,
                    features=degraded_features,
                    is_read_only=True,
                )
            raise LicenseError(
                "7-day offline grace period has expired. Please connect to the internet to re-validate.",
                code="OFFLINE_GRACE_EXPIRED",
            )

        # SEC-18 (Batch B): `user_id` is personal data. The raw value used to
        # be written into the INFO record; log the same truncated SHA-256
        # prefix convention the mismatch path uses (SEC-C-004) instead.
        logger.info(
            "License token verified successfully",
            extra={"user_prefix": sha256_prefix(payload.user_id), "tier": payload.tier},
        )
        return payload

    @classmethod
    def generate_signed_token(
        cls,
        private_key: ed25519.Ed25519PrivateKey,
        payload_dict: dict[str, object],
    ) -> str:
        """Helper to create signed license token for testing or backend licensing servers."""
        payload_json = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
        sig_bytes = private_key.sign(payload_json)

        b64_payload = base64.urlsafe_b64encode(payload_json).decode("ascii")
        b64_sig = base64.urlsafe_b64encode(sig_bytes).decode("ascii")

        return f"{b64_payload}.{b64_sig}"

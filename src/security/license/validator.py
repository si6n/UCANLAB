"""Ed25519 Asymmetric License Ticketing & 7-Day Offline Grace Period Validator.

Complies with MASTER_PLAN.md Section 3.2 (ADR-003).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
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
from src.security.hwid.collector import generate_hardware_fingerprint

logger = get_logger("security.license")


@dataclass(slots=True, frozen=True)
class LicensePayload:
    """Decoded and verified license parameters."""

    user_id: str
    tier: str  # "FREE" | "PRO" | "ENTERPRISE"
    hardware_fingerprint: str
    issued_at: int
    expires_at: int
    features: tuple[str, ...] = field(default_factory=tuple)


class LicenseValidator:
    """Validates Ed25519 signed license tokens and manages offline grace period."""

    MAX_OFFLINE_GRACE_SEC: ClassVar[int] = 7 * 24 * 3600  # 7 Days (604,800 s)
    _HWM_KEY_NAME: ClassVar[str] = "LICENSE_HWM_HMAC_KEY"

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
    ) -> None:
        self.public_key = public_key
        self.hardware_fingerprint = (
            hardware_fingerprint if hardware_fingerprint is not None else generate_hardware_fingerprint()
        )
        self.boot_realtime = boot_realtime
        self.boot_monotonic = boot_monotonic
        self.high_water_mark_path = Path(high_water_mark_path) if high_water_mark_path is not None else None
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
        self._allow_wildcard = allow_wildcard_license is True

        # HWM HMAC key comes from the SecretProvider vault, never hardcoded (F-04)
        self._secret_provider = secret_provider or get_default_secret_provider()
        self._hwm_key = self._load_hwm_key()
        # M-03: serializes the rollback check + HWM anchor update so
        # concurrent verify_token() calls cannot interleave (TOCTOU).
        self._clock_lock = threading.Lock()

        # Load persisted High-Water Mark from disk if present — fail closed on
        # anything that is not a valid HMAC'd timestamp (F-04).
        # G2 format: "<hwm_ts>:<last_online_sync_ts>.<hmac_hex>" with the HMAC
        # covering "<hwm_ts>:<last_online_sync_ts>". Legacy single-field files
        # ("<ts>.<hmac>") are still accepted read-only (HMAC over "<ts>").
        if self.high_water_mark_path and self.high_water_mark_path.exists():
            try:
                content = self.high_water_mark_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
                raise LicenseError(
                    "Corrupted HWM file (unreadable)",
                    code="HWM_CORRUPT",
                    cause=exc,
                ) from exc
            if "." not in content:
                raise LicenseError("Corrupted HWM file (missing HMAC)", code="HWM_CORRUPT")
            ts_part, hmac_str = content.rsplit(".", 1)
            if len(hmac_str) != 64:
                raise LicenseError("Corrupted HWM file (invalid format)", code="HWM_CORRUPT")

            if ":" in ts_part:
                # G2 two-field format
                hwm_str, sync_str = ts_part.split(":", 1)
                if not (hwm_str.isdigit() and sync_str.isdigit()):
                    raise LicenseError("Corrupted HWM file (invalid format)", code="HWM_CORRUPT")
                expected_mac = hmac.new(self._hwm_key, ts_part.encode("utf-8"), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(hmac_str, expected_mac):
                    self._recover_lost_hwm_key()
                    # H4 (3FABLE): adopt NOTHING from the unverifiable file —
                    # including the sync anchor. The old "grace-period
                    # courtesy" let an attacker who rolled the clock back and
                    # planted `<hwm>:<sync>.<garbage>` keep a chosen offline
                    # grace anchor past a license expiry. The grace window
                    # now restarts from a conservative floor: now minus the
                    # maximum allowed offline grace (or zero when unknown).
                    conservative_floor = 0
                    if self.MAX_OFFLINE_GRACE_SEC > 0:
                        wall_now_s = self.clock.now_wall_ns() // 1_000_000_000
                        conservative_floor = max(
                            0, wall_now_s - self.MAX_OFFLINE_GRACE_SEC
                        )
                    self.last_online_sync_ts = conservative_floor
                    return
                ts_val = int(hwm_str)
                sync_val = int(sync_str)
                if sync_val > 0:
                    self.last_online_sync_ts = sync_val
            else:
                # Legacy single-field format
                if not ts_part.isdigit():
                    raise LicenseError("Corrupted HWM file (invalid format)", code="HWM_CORRUPT")
                expected_mac = hmac.new(self._hwm_key, ts_part.encode("utf-8"), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(hmac_str, expected_mac):
                    self._recover_lost_hwm_key()
                    return
                ts_val = int(ts_part)

            if ts_val > self.last_known_clock_ts:
                self.last_known_clock_ts = ts_val

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
        if not self._secret_provider.has_secret(self._HWM_KEY_NAME):
            self._secret_provider.store_secret(self._HWM_KEY_NAME, os.urandom(32))
        return self._secret_provider.get_secret(self._HWM_KEY_NAME)

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

            # Persist high water mark to disk (G2: two-field format keeps the
            # grace-period anchor stable across restarts; HMAC covers both fields;
            # G6: temp+replace so a crash mid-write never truncates the HWM).
            # SEC-C-005: the in-memory anchor is only advanced AFTER a successful
            # persist — otherwise a failed write would silently roll the anchor
            # forward and mask a real clock-rollback on the next restart.
            if self.high_water_mark_path:
                try:
                    self.high_water_mark_path.parent.mkdir(parents=True, exist_ok=True)
                    ts_part = f"{now}:{self.last_online_sync_ts}"
                    mac = hmac.new(self._hwm_key, ts_part.encode("utf-8"), hashlib.sha256).hexdigest()
                    # L-14 (P3-5): unique temp name — the fixed ".tmp"
                    # suffix let a concurrent verification interleave/torn
                    # the two writers' partial files before either rename.
                    tmp_path = self.high_water_mark_path.with_suffix(
                        self.high_water_mark_path.suffix
                        + f".tmp-{os.getpid()}-{time.monotonic_ns()}"
                    )
                    tmp_path.write_text(f"{ts_part}.{mac}", encoding="utf-8")
                    tmp_path.replace(self.high_water_mark_path)
                    self.last_known_clock_ts = now
                except OSError as exc:
                    logger.warning(
                        "Failed to persist high water mark to disk", extra={"error": str(exc)}
                    )
                    # Advance in-memory anchor so subsequent verification in this session detects rollbacks
                    self.last_known_clock_ts = max(self.last_known_clock_ts, now)
            else:
                # No persistence configured: in-memory anchor advances directly.
                self.last_known_clock_ts = now

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
            logger.error("License token Ed25519 signature verification failed!")
            raise LicenseError(
                "License token signature is invalid or has been tampered with.",
                code="INVALID_SIGNATURE",
                cause=exc,
            ) from exc

        # Parse JSON payload
        try:
            data = json.loads(payload_bytes.decode("utf-8"))
            if not isinstance(data, dict) or not {
                "user_id",
                "tier",
                "hardware_fingerprint",
                "issued_at",
                "expires_at",
            }.issubset(data.keys()):
                raise ValueError("Incomplete license payload schema")
            payload = LicensePayload(
                user_id=data["user_id"],
                tier=data["tier"],
                hardware_fingerprint=data["hardware_fingerprint"],
                issued_at=data["issued_at"],
                expires_at=data["expires_at"],
                features=tuple(data.get("features", [])),
            )
        except Exception as exc:
            raise LicenseError(
                f"Malformed license JSON payload: {exc}",
                code="MALFORMED_PAYLOAD",
                cause=exc,
            ) from exc

        # Hardware Fingerprint Check (F-05: wildcard only in explicit test mode)
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
            raise LicenseError(
                "7-day offline grace period has expired. Please connect to the internet to re-validate.",
                code="OFFLINE_GRACE_EXPIRED",
            )

        logger.info("License token verified successfully", extra={"user": payload.user_id, "tier": payload.tier})
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

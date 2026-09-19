"""Device registration & Ed25519 license activation against the cloud (Task 5.3 client side).

 Flow (MASTER_PLAN §3.2):
   1. Desktop sends its CIM HWID fingerprint to POST /devices/register.
   2. Cloud responds with a device_token — stored under Windows DPAPI.
   3. Desktop exchanges device_token + license_ref for an Ed25519-signed
      ticket via POST /licenses/activate.
   4. The ticket is verified locally with the EMBEDDED public key before it
      is ever trusted; the canonical cloud schema (license_id, device_id,
      features, offline_until, nonce — MASTER_PLAN §3.1) is checked fully.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets as pysecrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError
from src.core.logging import get_logger
from src.security.cloud.client import CloudClient
from src.security.hwid.collector import generate_hardware_fingerprint

logger = get_logger("security.cloud.license_flow")

DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64 = "eX3vJQWpo/pKrkpi5Y+f7m5ooUCRbCyY201DTnAjz/Q="

# SEC-C-006: trusted key ring — the ticket's `kid` selects the verification
# key so the cloud can rotate its signing key without rebuilding clients.
# New keys are appended here (never remove an old one until all tickets
# carrying it have expired).
TRUSTED_CLOUD_PUBLIC_KEYS_B64: dict[str, str] = {
    "v1": DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64,
}
# Tickets issued before the `kid` scheme carry no recognizable key id.
LEGACY_KEY_ID: str = "v1"


def default_license_hwm_path() -> Path:
    """Return the canonical persistent anti-rollback HWM path (T57-B / L-2).

    Both the desktop app and the launcher must seal/read the SAME file — a
    per-process HWM resets on every restart and defeats rollback detection on
    the launcher's offline license path. `desktop_app._app_data_root()` is the
    single source of truth; it is imported lazily to avoid a circular import
    (desktop_app imports this module at import time). The fallback mirrors the
    same anchor so the two never diverge.
    """
    try:
        from src.ui.desktop_app import _app_data_root

        root = _app_data_root()
    except Exception:  # noqa: BLE001 — desktop_app unavailable (headless/test): mirror its anchor
        # src/security/cloud/license_flow.py -> parents[3] is the repo root.
        root = Path(__file__).resolve().parents[3]
    return root / "logs" / "license_hwm.txt"


@dataclass(slots=True, frozen=True)
class CloudLicenseClaims:
    """Fully verified canonical license ticket claims (MASTER_PLAN §3.1 SSOT)."""

    license_id: str
    organization_id: str
    device_id: str
    tier: str
    features: tuple[str, ...]
    issued_at: int
    expires_at: int
    offline_until: int
    key_id: str
    nonce: str


@dataclass(slots=True, frozen=True)
class DeviceRegistration:
    """Result of POST /devices/register."""

    device_id: str
    device_token: str
    hwid_resets_remaining: int


class LicenseFlow:
    """Orchestrates device registration + license activation against the cloud."""

    def __init__(
        self,
        client: CloudClient,
        public_key: ed25519.Ed25519PublicKey,
        app_version: str = "13.0.0",
        trusted_keys: dict[str, ed25519.Ed25519PublicKey] | None = None,
        boot_realtime: float | None = None,
        boot_monotonic: float | None = None,
        hwm_path: Path | None = None,
    ) -> None:
        self.client = client
        self.public_key = public_key
        self.app_version = app_version
        # SEC-C-006: verification keys resolve through the key ring by the
        # ticket's `kid`; the constructor key remains the default/fallback
        # so existing wiring keeps working.
        self._trusted_keys: dict[str, ed25519.Ed25519PublicKey] = dict(trusted_keys) if trusted_keys else {"v1": public_key}
        import time
        self.boot_realtime: float = boot_realtime if boot_realtime is not None else time.time()
        self.boot_monotonic: float = boot_monotonic if boot_monotonic is not None else time.monotonic()
        # M-19 (P2-13): persistent anti-rollback high-water mark. The old
        # in-memory anchor reset on every restart — a clock rollback performed
        # while the app was closed defeated rollback detection entirely.
        # hwm_path=None keeps the legacy in-memory-only behavior (tests).
        self._hwm_path: Path | None = hwm_path
        self.last_known_clock_ts: float = self._load_persistent_hwm(self.boot_realtime)

    def _resolve_key(self, kid: str) -> ed25519.Ed25519PublicKey:
        """Select the verification key for a ticket's key id (kid).

        An unknown kid fails closed — a ticket signed by an untrusted key
        must never fall back to another key in the ring.
        """
        key = self._trusted_keys.get(kid)
        if key is None:
            raise LicenseError(
                f"Cloud ticket references unknown signing key id '{kid}' — "
                "update the application to trust this key.",
                code="UNKNOWN_KEY_ID",
            )
        return key

    # ------------------------------------------------------------------
    # M-19 (P2-13): persistent anti-rollback high-water mark
    # ------------------------------------------------------------------

    def _hmac_key(self) -> bytes:
        """Derive the HWM integrity key from the client's secret provider.

        A random per-machine key stored in the OS-backed vault (DPAPI /
        machine-seed AES-GCM) — tampering with the HWM file requires
        compromising the vault itself.

        T57-B / L-3 (fail-closed): the old code swallowed a `store_secret`
        failure and returned a fresh random key on EVERY process. A HWM sealed
        under process A could then never be verified by process B, so the
        persistent HWM silently collapsed to boot time on each restart —
        exactly the rollback-reset the HWM exists to prevent, and one an
        attacker could trigger on purpose by making the vault unwritable.
        When the key cannot be *persisted* we refuse to treat the HWM as
        trustworthy instead of minting an unstable key.
        """
        secrets = getattr(self.client, "_secrets", None)
        if secrets is not None:
            try:
                if secrets.has_secret("LICENSE_HWM_KEY"):
                    return secrets.get_secret("LICENSE_HWM_KEY")
            except KeyError:
                pass

        derived = hashlib.sha256(b"ucanlab-license-hwm" + os.urandom(32)).digest()
        if secrets is None:
            logger.critical(
                "License HWM integrity key store is unavailable — refusing to "
                "seal the HWM with an unstable key (fail-closed)",
            )
            raise LicenseError(
                "License HWM integrity key unavailable; refusing to treat HWM as trustworthy.",
                code="HWM_KEY_UNAVAILABLE",
            )
        try:
            secrets.store_secret("LICENSE_HWM_KEY", derived)
        except Exception as exc:  # noqa: BLE001 — any vault failure means the key is not durable
            logger.critical(
                "License HWM integrity key could not be persisted — a new key "
                "each restart would reset rollback detection (fail-closed)",
                extra={"error": str(exc)},
            )
            raise LicenseError(
                "License HWM integrity key unavailable; refusing to treat HWM as trustworthy.",
                code="HWM_KEY_UNAVAILABLE",
                cause=exc,
            ) from exc

        # Confirm the key actually round-trips; some backends accept a write
        # but drop it (or transform it), which would break the next seal.
        try:
            stored = secrets.get_secret("LICENSE_HWM_KEY")
        except Exception as exc:  # noqa: BLE001 — key vanished right after storing
            raise LicenseError(
                "License HWM integrity key unavailable; refusing to treat HWM as trustworthy.",
                code="HWM_KEY_UNAVAILABLE",
                cause=exc,
            ) from exc
        if stored != derived:
            raise LicenseError(
                "License HWM integrity key is not stably persisted; refusing to treat HWM as trustworthy.",
                code="HWM_KEY_UNAVAILABLE",
            )
        return stored

    def _load_persistent_hwm(self, fallback: float) -> float:
        """Load the last persisted wall-clock HWM (T62-U3 / fail-closed anti-rollback).

        If the HWM was previously initialized, a missing, unreadable, or
        corrupted HWM file must fail closed with HWM_UNAVAILABLE / LicenseError
        instead of quietly resetting rollback detection to boot time.
        """
        if self._hwm_path is None:
            return fallback

        secrets = getattr(self.client, "_secrets", None)
        was_initialized = False
        if secrets is not None:
            try:
                was_initialized = secrets.has_secret("LICENSE_HWM_INITIALIZED")
            except Exception:
                was_initialized = False

        if not self._hwm_path.exists():
            if was_initialized:
                logger.critical(
                    "License HWM file is missing after previous initialization — "
                    "refusing to reset anti-rollback to boot time (fail-closed)"
                )
                raise LicenseError(
                    "License HWM file is missing after previous initialization (fail-closed)",
                    code="HWM_UNAVAILABLE",
                )
            return fallback

        try:
            import hashlib
            import hmac as _hmac

            text = self._hwm_path.read_text(encoding="utf-8").strip()
            ts_part, _, mac = text.rpartition(".")
            if not ts_part or not mac:
                logger.critical("License HWM file malformed — fail-closed")
                raise LicenseError("License HWM file malformed", code="HWM_UNAVAILABLE")
            expected = _hmac.new(self._hmac_key(), ts_part.encode("utf-8"), hashlib.sha256).hexdigest()
            if not _hmac.compare_digest(expected, mac):
                logger.critical("License HWM file failed integrity check — refusing to reset to boot time (fail-closed)")
                raise LicenseError("License HWM file integrity check failed", code="HWM_UNAVAILABLE")
            hwm_ts = float(ts_part.split(":", 1)[0])
            if not math.isfinite(hwm_ts) or hwm_ts <= 0:
                logger.critical("License HWM timestamp invalid — fail-closed")
                raise LicenseError("License HWM timestamp invalid", code="HWM_UNAVAILABLE")
            return max(fallback, hwm_ts)
        except LicenseError:
            raise
        except (OSError, ValueError) as exc:
            if was_initialized or self._hwm_path.exists():
                logger.critical("License HWM file read/parse failed — fail-closed", extra={"error": str(exc)})
                raise LicenseError(
                    f"License HWM file could not be read or parsed: {exc}",
                    code="HWM_UNAVAILABLE",
                    cause=exc,
                ) from exc
            return fallback

    def _persist_hwm(self, ts: float) -> None:
        """Persist the HWM atomically with an HMAC integrity tag."""
        if self._hwm_path is None:
            return
        try:
            import hashlib
            import hmac as _hmac

            self._hwm_path.parent.mkdir(parents=True, exist_ok=True)
            key = self._hmac_key()
            ts_part = f"{ts}"
            mac = _hmac.new(key, ts_part.encode("utf-8"), hashlib.sha256).hexdigest()
            tmp = self._hwm_path.with_suffix(
                self._hwm_path.suffix + f".tmp-{os.getpid()}-{time.monotonic_ns()}"
            )
            tmp.write_text(f"{ts_part}.{mac}", encoding="utf-8")
            os.replace(tmp, self._hwm_path)

            secrets = getattr(self.client, "_secrets", None)
            if secrets is not None:
                try:
                    secrets.store_secret("LICENSE_HWM_INITIALIZED", b"1")
                except Exception as exc:
                    logger.warning("Failed to store HWM initialized marker in secret provider", extra={"error": str(exc)})
        except OSError as exc:
            logger.error("Failed to persist license HWM — fail-closed", extra={"error": str(exc)})
            raise LicenseError("Failed to persist license HWM (fail-closed)", code="HWM_UNAVAILABLE", cause=exc) from exc

    # ------------------------------------------------------------------
    # POST /api/v1/devices/register
    # ------------------------------------------------------------------
    def register_device(self, device_name: str, hwid: str | None = None) -> DeviceRegistration:
        payload = {
            "device_name": device_name,
            "hwid": hwid or generate_hardware_fingerprint(),
            "app_version": self.app_version,
        }
        resp = self.client.request("POST", "/devices/register", json_body=payload)
        if resp.status in (401, 403):
            raise LicenseError(
                "Device registration rejected — check your cloud session (login on the web portal).",
                code="REGISTRATION_UNAUTHORIZED",
            )
        if resp.status == 429:
            raise LicenseError("Too many registration attempts — try again later.", code="RATE_LIMITED")
        if resp.status != 201:
            raise LicenseError(
                f"Device registration failed (HTTP {resp.status})",
                code="REGISTRATION_FAILED",
            )

        data = resp.json()
        registration = DeviceRegistration(
            device_id=data["device_id"],
            device_token=data["device_token"],
            hwid_resets_remaining=data.get("hwid_resets_remaining", 0),
        )

        # Persist the device token and device ID under DPAPI (never plaintext on disk).
        self.client.store_device_token(registration.device_token)
        self.client.store_device_id(registration.device_id)
        logger.info(
            "Device registered with cloud",
            extra={"device_id": registration.device_id, "hwid": payload["hwid"][:8] + "…"},
        )
        return registration

    # ------------------------------------------------------------------
    # POST /api/v1/licenses/activate
    # ------------------------------------------------------------------
    def activate_license(self, license_ref: str) -> CloudLicenseClaims:
        device_token = self.client.get_device_token()
        if not device_token:
            raise LicenseError(
                "No device token on record — register the device first.",
                code="NO_DEVICE_TOKEN",
            )

        sent_nonce = pysecrets.token_hex(16)
        resp = self.client.request(
            "POST",
            "/licenses/activate",
            json_body={
                "device_token": device_token,
                "license_ref": license_ref,
                "nonce": sent_nonce,
            },
        )
        if resp.status in (401, 403):
            raise LicenseError(
                "License activation rejected — token/license mismatch or expired license.",
                code="ACTIVATION_REJECTED",
            )
        if resp.status != 200:
            raise LicenseError(f"License activation failed (HTTP {resp.status})", code="ACTIVATION_FAILED")

        data = resp.json()
        claims = self.verify_cloud_ticket(data["license_token"])
        if claims.nonce and claims.nonce != sent_nonce:
            raise LicenseError(
                f"Cloud license anti-replay nonce mismatch (expected {sent_nonce}, got {claims.nonce})",
                code="NONCE_MISMATCH",
            )

        # Persist the signed ticket for offline re-verification within grace.
        self.client.store_license_ticket(data["license_token"])
        logger.info(
            "Cloud license activated",
            extra={"license_id": claims.license_id, "tier": claims.tier},
        )
        return claims

    # ------------------------------------------------------------------
    # Local verification of the Ed25519 ticket (trust anchor: embedded key)
    # ------------------------------------------------------------------
    def verify_cloud_ticket(
        self,
        token: str,
        expected_device_id: str | None = None,
        is_offline: bool = False,
    ) -> CloudLicenseClaims:
        """Verify signature + canonical schema; raise LicenseError on any flaw.

        P1-6 (REVIEW H-5) / T57-B (L-6): `is_offline` states whether the
        CALLER is re-verifying a stored ticket without a network round-trip,
        but it NO LONGER controls whether the backend-granted grace window is
        enforced. Validity is bounded by `min(exp, offline_until)` for both
        modes, so forgetting to declare the offline mode can never extend a
        ticket past its grace deadline; `is_offline` only selects the error
        message/telemetry for the grace branch.
        """
        parts = token.strip().split(".")
        if len(parts) != 2:
            raise LicenseError("Invalid cloud ticket format", code="INVALID_TICKET_FORMAT")

        def _pad(s: str) -> str:
            return s + "=" * (-len(s) % 4)

        try:
            payload_bytes = base64.urlsafe_b64decode(_pad(parts[0]))
            sig_bytes = base64.urlsafe_b64decode(_pad(parts[1]))
        except Exception as exc:
            raise LicenseError("Cloud ticket base64 decode failed", code="TICKET_DECODE_ERROR", cause=exc) from exc

        try:
            # SEC-C-006: verify with the key the ticket names (kid), not
            # blindly with whatever single key was wired at construction.
            data: dict[str, Any] = json.loads(payload_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LicenseError("Malformed ticket payload", code="MALFORMED_PAYLOAD", cause=exc) from exc

        required = {
            "iss", "aud", "kid", "license_id", "organization_id", "device_id",
            "tier", "features", "iat", "exp", "offline_until", "schema_version", "nonce",
        }
        if not required.issubset(data.keys()):
            raise LicenseError("Incomplete cloud ticket schema", code="INCOMPLETE_SCHEMA")
        if data["iss"] != "universal-can-cloud" or data["aud"] != "diagnostic-desktop-app":
            raise LicenseError("Ticket issuer/audience mismatch", code="ISSUER_MISMATCH")
        # P1-6 (REVIEW H-5): an empty nonce skips the anti-replay comparison
        # downstream (`if claims.nonce and ...` is falsy) — require a
        # non-empty string here so a ticket issued without a nonce is
        # rejected at schema level instead of silently replayable.
        if not isinstance(data.get("nonce"), str) or not data["nonce"]:
            raise LicenseError("Cloud ticket carries no anti-replay nonce", code="MISSING_NONCE")

        kid = data["kid"]
        verification_key = self._resolve_key(kid)
        try:
            verification_key.verify(sig_bytes, payload_bytes)
        except InvalidSignature as exc:
            logger.error(
                "Cloud ticket Ed25519 signature verification FAILED",
                extra={"kid": kid},
            )
            raise LicenseError(
                "Cloud license ticket signature is invalid.",
                code="INVALID_SIGNATURE",
                cause=exc,
            ) from exc

        # Enforce H1 device binding check.
        # P1-6 (REVIEW H-5): the binding check is fail-closed — the old
        # `if target_device_id and ...` skipped the check entirely when no
        # device id was stored (fresh install, cleared vault, deleted DPAPI
        # blob), accepting tickets cloned from ANY other machine.
        target_device_id = expected_device_id or self.client.get_device_id()
        if not target_device_id:
            raise LicenseError(
                "No registered device id on record — register this device before "
                "verifying a cloud ticket (device binding cannot be skipped).",
                code="NO_DEVICE_ID",
            )
        if data["device_id"] != target_device_id:
            logger.error(
                "Cloud ticket device binding mismatch",
                extra={"ticket_device_id": data["device_id"], "registered_device_id": target_device_id},
            )
            raise LicenseError(
                f"Cloud license ticket device mismatch: ticket is bound to {data['device_id']}, but current device is {target_device_id}.",
                code="DEVICE_MISMATCH",
            )

        import time

        now = time.time()
        # Anti-Clock Rollback and monotonic drift cross-check (HIGH-4)
        if now < self.last_known_clock_ts - 1.0:
            logger.critical(
                "System clock rollback detected in cloud ticket verification!",
                extra={"now": now, "last": self.last_known_clock_ts},
            )
            raise LicenseError(
                "System clock manipulation detected. License validation halted.",
                code="CLOCK_ROLLBACK_DETECTED",
            )

        curr_mono = time.monotonic()
        mono_elapsed = curr_mono - self.boot_monotonic
        real_elapsed = now - self.boot_realtime
        if abs(mono_elapsed - real_elapsed) > 60.0:
            logger.critical(
                "Monotonic counter mismatch detected in cloud ticket verification!",
                extra={"mono_elapsed": mono_elapsed, "real_elapsed": real_elapsed},
            )
            raise LicenseError(
                "System clock manipulation detected (monotonic counter mismatch).",
                code="CLOCK_MONOTONIC_MISMATCH",
            )

        self.last_known_clock_ts = max(self.last_known_clock_ts, now)
        self._persist_hwm(self.last_known_clock_ts)

        # Check strict schema types
        if not isinstance(data.get("features"), (list, tuple, set)):
            raise LicenseError("Malformed features field in ticket", code="MALFORMED_SCHEMA")
        if not isinstance(data.get("iat"), (int, float)) or not isinstance(data.get("exp"), (int, float)):
            raise LicenseError("Malformed timestamp field in ticket", code="MALFORMED_SCHEMA")
        if not isinstance(data.get("offline_until"), (int, float)):
            raise LicenseError("Malformed offline_until field in ticket", code="MALFORMED_SCHEMA")

        if now > data["exp"]:
            raise LicenseError("Cloud license ticket has expired.", code="LICENSE_EXPIRED")

        # T57-B / L-6: the backend-granted offline window (`offline_until`) is
        # an absolute deadline that bounds the ticket EVEN when the caller
        # reports online. The old code evaluated `exp` and `offline_until`
        # independently and only applied the grace check when is_offline was
        # True, so a ticket with a long `exp` and a short grace stayed valid
        # forever whenever any caller forgot to declare its offline mode —
        # the grace policy was dead code. Join the two deadlines instead
        # (min semantics): a ticket is valid only until the EARLIER of the
        # licence expiry and the grace deadline. `is_offline` is kept only to
        # emit the more specific error/telemetry, never to widen validity.
        grace_deadline = min(data["exp"], data["offline_until"])
        if now > grace_deadline:
            if is_offline:
                raise LicenseError("Cloud offline grace period has expired.", code="OFFLINE_GRACE_EXPIRED")
            raise LicenseError(
                "Cloud license grace deadline has passed (offline_until exceeded).",
                code="OFFLINE_GRACE_EXPIRED",
            )

        return CloudLicenseClaims(
            license_id=data["license_id"],
            organization_id=data["organization_id"],
            device_id=data["device_id"],
            tier=data["tier"],
            features=tuple(data["features"]),
            issued_at=data["iat"],
            expires_at=data["exp"],
            offline_until=data["offline_until"],
            key_id=data["kid"],
            nonce=data["nonce"],
        )

# -*- coding: utf-8 -*-
"""Test suite for RFC 8785 JSON Canonicalization Scheme (JCS) & License Validation — Aksiyon 33 / Faz 6.2."""

import base64
import json
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError, SecurityError
from src.security.license.jcs import canonicalize, canonical_hash
from src.security.license.validator import LicenseValidator


def test_jcs_canonicalize_key_sorting() -> None:
    # Keys out of order
    obj = {"z": 1, "a": 2, "m": 3}
    res = canonicalize(obj)
    # UTF-8 bytes must have keys sorted: "a", "m", "z"
    assert res == b'{"a":2,"m":3,"z":1}'


def test_jcs_canonicalize_utf16_surrogate_order() -> None:
    # Characters inside and outside BMP
    obj = {"\u00e9": 1, "\U0001f600": 2, "a": 3}
    res = canonicalize(obj)
    decoded = res.decode("utf-8")
    assert decoded.startswith('{"a":3')


def test_jcs_rejects_non_finite_floats() -> None:
    with pytest.raises(SecurityError, match="RFC 8785 rejects non-finite number"):
        canonicalize({"val": float("nan")})

    with pytest.raises(SecurityError, match="RFC 8785 rejects non-finite number"):
        canonicalize({"val": float("inf")})


def test_jcs_rejects_surrogates() -> None:
    with pytest.raises(SecurityError, match="lone surrogates"):
        canonicalize({"invalid": "\ud800"})


class FixedClock:
    def __init__(self, ts_sec: int) -> None:
        self.ts_sec = ts_sec

    def now_wall_ns(self) -> int:
        return self.ts_sec * 1_000_000_000

    def now_monotonic_ns(self) -> int:
        return self.ts_sec * 1_000_000_000


def test_jcs_license_token_verification_and_offline_degrade() -> None:
    # Generate Ed25519 key pair
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    hwid = "TEST-HWID-12345678"
    now = 1700000000
    issued_at = now - 3600
    expires_at = now + 86400 * 30
    clock = FixedClock(now)

    payload_dict = {
        "user_id": "usr-test-01",
        "tier": "ENTERPRISE",
        "hardware_fingerprint": hwid,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "features": ["can_read", "flash_ecu", "uds_write"],
    }

    # Sign JCS canonicalized bytes
    jcs_bytes = canonicalize(payload_dict)
    sig = private_key.sign(jcs_bytes)

    # Encode token as <payload_b64>.<sig_b64>
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload_dict).encode("utf-8")).decode("ascii")
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii")
    token_str = f"{payload_b64}.{sig_b64}"

    # Verify with validator
    validator = LicenseValidator(
        public_key=public_key,
        hardware_fingerprint=hwid,
        last_online_sync_ts=now,
        last_known_clock_ts=now,
        clock=clock,
        degrade_to_read_only_on_offline=True,
    )

    verified = validator.verify_token(token_str)
    assert verified.user_id == "usr-test-01"
    assert verified.tier == "ENTERPRISE"
    assert verified.is_read_only is False
    assert "flash_ecu" in verified.features

    # Test Offline Grace Expiration with degrade_to_read_only_on_offline=True
    expired_sync_ts = now - (validator.MAX_OFFLINE_GRACE_SEC + 100)
    validator_degraded = LicenseValidator(
        public_key=public_key,
        hardware_fingerprint=hwid,
        last_online_sync_ts=expired_sync_ts,
        last_known_clock_ts=now,
        clock=clock,
        degrade_to_read_only_on_offline=True,
    )

    degraded_payload = validator_degraded.verify_token(token_str)
    assert degraded_payload.tier == "READ_ONLY"
    assert degraded_payload.is_read_only is True
    # Critical write features stripped
    assert "flash_ecu" not in degraded_payload.features
    assert "uds_write" not in degraded_payload.features
    assert "can_read" in degraded_payload.features

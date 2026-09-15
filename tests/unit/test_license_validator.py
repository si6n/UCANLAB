"""Unit tests for Ed25519 LicenseValidator, 7-day offline grace period, and Anti-Clock High-Water Mark."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError
from src.security.hwid.collector import generate_hardware_fingerprint
from src.security.license.validator import LicenseValidator


class FakeWallClock:
    """Deterministic wall-clock for G3: verify_token reads time from here."""

    def __init__(self, wall_ts: int) -> None:
        self._wall_ns = int(wall_ts) * 1_000_000_000

    def set(self, wall_ts: int) -> None:
        self._wall_ns = int(wall_ts) * 1_000_000_000

    def now_monotonic(self) -> float:
        return 0.0

    def now_monotonic_ns(self) -> int:
        return 0

    def now_wall_ns(self) -> int:
        return self._wall_ns



def test_license_valid_verification() -> None:

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    now = int(time.time())
    payload_dict = {
        "user_id": "usr_9988",
        "tier": "ENTERPRISE",
        "hardware_fingerprint": "HW_UUID_12345678",
        "issued_at": now - 3600,
        "expires_at": now + 86400 * 365,
        "features": ["CAN_SNIFFER", "J1939_DIAGNOSTICS", "UDS_ROUTINES"],
    }

    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    validator = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_UUID_12345678",
        last_online_sync_ts=now,
    )

    validator.clock = FakeWallClock(now)
    verified = validator.verify_token(token_str)
    assert verified.user_id == "usr_9988"
    assert verified.tier == "ENTERPRISE"
    assert "J1939_DIAGNOSTICS" in verified.features


def test_license_tampered_signature_rejected() -> None:
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = int(time.time())

    payload_dict = {
        "user_id": "usr_hacker",
        "tier": "FREE",
        "hardware_fingerprint": "HW_ANY",
        "issued_at": now,
        "expires_at": now + 3600,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    # Tamper token signature
    parts = token_str.split(".")
    tampered_sig = parts[1][:-4] + "AAAA"
    tampered_token = f"{parts[0]}.{tampered_sig}"

    validator = LicenseValidator(public_key=pub_key, hardware_fingerprint="HW_ANY")
    with pytest.raises(LicenseError, match="signature is invalid") as exc_info:
        validator.clock = FakeWallClock(now)
        validator.verify_token(tampered_token)
    assert exc_info.value.code == "INVALID_SIGNATURE"


def test_license_hardware_mismatch() -> None:
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = int(time.time())

    payload_dict = {
        "user_id": "usr_1",
        "tier": "PRO",
        "hardware_fingerprint": "PC_ALICE",
        "issued_at": now,
        "expires_at": now + 3600,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    validator = LicenseValidator(public_key=pub_key, hardware_fingerprint="PC_BOB")
    with pytest.raises(LicenseError, match="locked to a different machine") as exc_info:
        validator.clock = FakeWallClock(now)
        validator.verify_token(token_str)
    assert exc_info.value.code == "HARDWARE_MISMATCH"


def test_license_clock_rollback_detection() -> None:
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = int(time.time())

    payload_dict = {
        "user_id": "usr_1",
        "tier": "PRO",
        "hardware_fingerprint": "HW_1",
        "issued_at": now,
        "expires_at": now + 86400,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    validator = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_1",
        last_known_clock_ts=now + 5000,  # Future clock recorded
    )

    # Calling with earlier timestamp trips rollback
    with pytest.raises(LicenseError, match="clock manipulation detected") as exc_info:
        validator.clock = FakeWallClock(now)
        validator.verify_token(token_str)
    assert exc_info.value.code == "CLOCK_ROLLBACK_DETECTED"


def test_license_offline_grace_period_expired() -> None:
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = int(time.time())

    payload_dict = {
        "user_id": "usr_1",
        "tier": "PRO",
        "hardware_fingerprint": "HW_1",
        "issued_at": now - 86400 * 30,
        "expires_at": now + 86400 * 365,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    # Last online sync was 8 days ago (> 7 days max grace)
    validator = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_1",
        last_online_sync_ts=now - (8 * 86400),
    )

    with pytest.raises(LicenseError, match="7-day offline grace period has expired") as exc_info:
        validator.clock = FakeWallClock(now)
        validator.verify_token(token_str)
    assert exc_info.value.code == "OFFLINE_GRACE_EXPIRED"


def test_license_default_hwid_wiring() -> None:
    """Verify that LicenseValidator defaults to genuine generate_hardware_fingerprint()."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    expected_hwid = generate_hardware_fingerprint()

    # Instantiate validator without passing hardware_fingerprint
    validator = LicenseValidator(public_key=pub_key)
    assert validator.hardware_fingerprint == expected_hwid

    now = int(time.time())
    payload_dict = {
        "user_id": "usr_autohwid",
        "tier": "PRO",
        "hardware_fingerprint": expected_hwid,
        "issued_at": now - 60,
        "expires_at": now + 3600,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    validator.clock = FakeWallClock(now)
    verified = validator.verify_token(token_str)
    assert verified.hardware_fingerprint == expected_hwid


def test_license_wildcard_hwid() -> None:
    """Wildcard '*' only matches any machine when explicitly enabled (F-05)."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = int(time.time())

    payload_dict = {
        "user_id": "enterprise_fleet",
        "tier": "ENTERPRISE",
        "hardware_fingerprint": "*",
        "issued_at": now,
        "expires_at": now + 86400,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    # Production default: wildcard must be rejected
    validator = LicenseValidator(public_key=pub_key, hardware_fingerprint="ANY_RANDOM_HWID_STRING")
    with pytest.raises(LicenseError, match="different machine hardware") as exc_info:
        validator.clock = FakeWallClock(now)
        validator.verify_token(token_str)
    assert exc_info.value.code == "HARDWARE_MISMATCH"

    # Explicit test mode opt-in: wildcard accepted
    now2 = int(time.time())
    test_validator = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="ANY_RANDOM_HWID_STRING",
        allow_wildcard_license=True,
    )
    test_validator.clock = FakeWallClock(now2)
    verified = test_validator.verify_token(token_str)
    assert verified.user_id == "enterprise_fleet"
    assert verified.hardware_fingerprint == "*"


def test_license_expired() -> None:
    """Verify that expired license token raises LICENSE_EXPIRED."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = int(time.time())

    payload_dict = {
        "user_id": "usr_expired",
        "tier": "FREE",
        "hardware_fingerprint": "HW_EXP",
        "issued_at": now - 7200,
        "expires_at": now - 3600,  # Expired 1 hour ago
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    validator = LicenseValidator(public_key=pub_key, hardware_fingerprint="HW_EXP")
    with pytest.raises(LicenseError, match="License expired at") as exc_info:
        validator.clock = FakeWallClock(now)
        validator.verify_token(token_str)
    assert exc_info.value.code == "LICENSE_EXPIRED"


@pytest.mark.parametrize(
    "bad_token",
    [
        "",
        "not_a_valid_token",
        "part1.part2.part3",
        "singleword",
    ],
)
def test_license_invalid_token_format(bad_token: str) -> None:
    """Verify tokens not in <payload>.<signature> format raise INVALID_TOKEN_FORMAT."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    validator = LicenseValidator(public_key=priv_key.public_key(), hardware_fingerprint="HW_1")

    with pytest.raises(LicenseError, match="Invalid license token format") as exc_info:
        validator.verify_token(bad_token)
    assert exc_info.value.code == "INVALID_TOKEN_FORMAT"


def test_license_base64_decode_error() -> None:
    """Verify corrupted non-base64 tokens raise TOKEN_DECODE_ERROR."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    validator = LicenseValidator(public_key=priv_key.public_key(), hardware_fingerprint="HW_1")

    with pytest.raises(LicenseError, match="Failed to decode license base64") as exc_info:
        validator.verify_token("@@@bad_base64@@@.###bad_sig###")
    assert exc_info.value.code == "TOKEN_DECODE_ERROR"


def test_license_malformed_json_payload() -> None:
    """Verify validly signed but non-JSON or missing fields raise MALFORMED_PAYLOAD."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = int(time.time())

    # Raw bytes that are valid base64 but not valid license JSON
    bad_json_bytes = b'{"incomplete": true}'
    sig_bytes = priv_key.sign(bad_json_bytes)

    b64_payload = base64.urlsafe_b64encode(bad_json_bytes).decode("ascii")
    b64_sig = base64.urlsafe_b64encode(sig_bytes).decode("ascii")
    token_str = f"{b64_payload}.{b64_sig}"

    validator = LicenseValidator(public_key=pub_key, hardware_fingerprint="HW_1")
    with pytest.raises(LicenseError, match="Malformed license JSON payload") as exc_info:
        validator.clock = FakeWallClock(now)
        validator.verify_token(token_str)
    assert exc_info.value.code == "MALFORMED_PAYLOAD"


def test_license_monotonic_mismatch_detected() -> None:
    """Verify monotonic clock cross-check catches backward real-time drift."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    # Injected boot reference: 1000s realtime, 0s monotonic
    boot_realtime = 1000
    boot_monotonic = 0.0

    validator = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_1",
        last_online_sync_ts=boot_realtime,
        boot_realtime=boot_realtime,
        boot_monotonic=boot_monotonic,
        last_known_clock_ts=boot_realtime,
    )

    payload_dict = {
        "user_id": "usr_mono",
        "tier": "PRO",
        "hardware_fingerprint": "HW_1",
        "issued_at": 500,
        "expires_at": 50000,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    # If monotonic is 500s (expected realtime is 1500s), but now is 1200s (< 1500 - 60)
    with patch("time.monotonic", return_value=500.0):
        with pytest.raises(LicenseError, match="monotonic counter") as exc_info:
            validator.clock = FakeWallClock(1200)
            validator.verify_token(token_str)
        assert exc_info.value.code == "CLOCK_MONOTONIC_MISMATCH"


def test_persistent_high_water_mark_file_flow(tmp_path: Path) -> None:
    """Verify High-Water Mark is persisted to disk and prevents rollback on new validator instances."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    hwm_file = tmp_path / "subdir" / "hwm.dat"

    now = 2000000000
    payload_dict = {
        "user_id": "usr_hwm",
        "tier": "ENTERPRISE",
        "hardware_fingerprint": "HW_HWM",
        "issued_at": now - 1000,
        "expires_at": now + 100000,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    # 1. Run validator and verify token at now
    validator1 = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_HWM",
        high_water_mark_path=hwm_file,
        last_online_sync_ts=now,
        boot_realtime=now,
        boot_monotonic=0.0,
    )
    with patch("time.monotonic", return_value=0.0):
        validator1.clock = FakeWallClock(now)
        validator1.verify_token(token_str)

    assert hwm_file.exists()
    # G2 format: "<hwm_ts>:<last_online_sync_ts>.<hmac>"
    assert hwm_file.read_text(encoding="utf-8").strip().startswith(f"{now}:{now}.")

    # 2. Instantiate a second validator instance reading the same file
    validator2 = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_HWM",
        high_water_mark_path=hwm_file,
        last_online_sync_ts=now,
        boot_realtime=now,
        boot_monotonic=0.0,
    )
    assert validator2.last_known_clock_ts == now
    # G2: the grace-period anchor is restored from the file, not reset to now
    assert validator2.last_online_sync_ts == now

    # 3. An attacker sets system clock backward to now - 5000 -> must fail
    with patch("time.monotonic", return_value=0.0):
        with pytest.raises(LicenseError, match="clock manipulation detected") as exc_info:
            validator2.clock = FakeWallClock(now - 5000)
            validator2.verify_token(token_str)
        assert exc_info.value.code == "CLOCK_ROLLBACK_DETECTED"


def test_persistent_high_water_mark_corrupt_file(tmp_path: Path) -> None:
    """Corrupt plaintext HWM file (no HMAC) is rejected with HWM_CORRUPT (F-04)."""
    hwm_file = tmp_path / "corrupt_hwm.dat"
    hwm_file.write_text("CORRUPTED_NON_INTEGER_VALUE", encoding="utf-8")

    priv_key = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(LicenseError, match="Corrupted HWM") as exc_info:
        LicenseValidator(
            public_key=priv_key.public_key(),
            hardware_fingerprint="HW_1",
            high_water_mark_path=hwm_file,
        )
    assert exc_info.value.code == "HWM_CORRUPT"


def test_persistent_high_water_mark_tampered_file(tmp_path: Path) -> None:
    """HWM file with valid shape but unverifiable HMAC is quarantined (G5).

    An HMAC failure is indistinguishable between a forged file and a lost
    vault key, so the G5 policy is quarantine (file renamed, evidence kept)
    plus re-anchoring the in-memory HWM to the machine clock. The file's
    unverifiable timestamp — here a hostile future value — is NOT adopted.
    """
    hwm_file = tmp_path / "tampered_hwm.dat"
    hostile_future_ts = int(time.time()) + 999_999
    # Legacy single-field shape with a forged all-zero HMAC
    hwm_file.write_text(f"{hostile_future_ts}." + "0" * 64, encoding="utf-8")

    priv_key = ed25519.Ed25519PrivateKey.generate()
    validator = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint="HW_1",
        high_water_mark_path=hwm_file,
    )

    # Hostile future timestamp NOT adopted as the anti-rollback floor
    assert validator.last_known_clock_ts < hostile_future_ts
    # The unverifiable file was quarantined, not trusted and not deleted
    quarantined = list(tmp_path.glob("tampered_hwm.dat.lostkey-*"))
    assert len(quarantined) == 1
    assert not hwm_file.exists()


def test_persistent_high_water_mark_save_error(tmp_path: Path) -> None:
    """Verify that disk write error on saving HWM logs warning and does not crash validation."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = int(time.time())

    payload_dict = {
        "user_id": "usr_save_err",
        "tier": "PRO",
        "hardware_fingerprint": "HW_SAVE_ERR",
        "issued_at": now,
        "expires_at": now + 3600,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    hwm_file = tmp_path / "readonly_hwm.dat"
    validator = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_SAVE_ERR",
        high_water_mark_path=hwm_file,
    )

    with patch.object(Path, "write_text", side_effect=OSError("Disk write permission denied")):
        # Verification should succeed despite save error
        validator.clock = FakeWallClock(now)
        verified = validator.verify_token(token_str)
        assert verified.user_id == "usr_save_err"


def test_grace_period_survives_restart(tmp_path: Path) -> None:
    """G2: a restart must NOT reset the 7-day offline grace window.

    The old code re-defaulted last_online_sync_ts to time.time() in every
    constructor, so the grace period never elapsed across restarts.
    """
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    hwm_file = tmp_path / "hwm_restart.dat"

    sync_ts = 2_000_000_000
    issue_ts = sync_ts - 50
    payload_dict = {
        "user_id": "grace_user",
        "tier": "PRO",
        "hardware_fingerprint": "HW_GRACE",
        "issued_at": issue_ts,
        "expires_at": sync_ts + 10_000_000,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    # First run: last online sync at sync_ts, HWM persisted
    first = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_GRACE",
        high_water_mark_path=hwm_file,
        last_online_sync_ts=sync_ts,
        boot_realtime=sync_ts,
        boot_monotonic=0.0,
    )
    with patch("time.monotonic", return_value=0.0):
        first.clock = FakeWallClock(sync_ts)
        first.verify_token(token_str)

    # Second run DAYS later (but inside the token lifetime): the constructor
    # must restore last_online_sync_ts from the HWM file — a naive time.time()
    # default would claim we were online "now" and reset the window.
    days_later = sync_ts + 5 * 24 * 3600
    second = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_GRACE",
        high_water_mark_path=hwm_file,
        # deliberately NO last_online_sync_ts argument — this is the trap
        boot_realtime=days_later,
        boot_monotonic=5 * 24 * 3600.0,
    )
    assert second.last_online_sync_ts == sync_ts, "grace anchor was reset by restart"

    # The offline grace clock is therefore 5 days along, not 0
    offline_elapsed = days_later - second.last_online_sync_ts
    assert 5 * 24 * 3600 <= offline_elapsed < LicenseValidator.MAX_OFFLINE_GRACE_SEC


def test_legacy_single_field_hwm_still_accepted(tmp_path: Path) -> None:
    """G2 backward compatibility: old '<ts>.<hmac>' files load read-only."""
    import hashlib
    import hmac as hmac_mod

    from src.safety.secret_provider import InMemorySecretProvider

    provider = InMemorySecretProvider()
    # Use the validator's own key path: create a validator once to seed the key
    priv_key = ed25519.Ed25519PrivateKey.generate()
    seed = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint="HW_LEGACY",
        secret_provider=provider,
    )
    hwm_key = seed._hwm_key

    ts = 1_999_999_000
    mac = hmac_mod.new(hwm_key, str(ts).encode("utf-8"), hashlib.sha256).hexdigest()
    legacy_file = tmp_path / "legacy_hwm.dat"
    legacy_file.write_text(f"{ts}.{mac}", encoding="utf-8")

    restored = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint="HW_LEGACY",
        high_water_mark_path=legacy_file,
        last_known_clock_ts=ts - 1000,  # forces the HWM comparison branch
        secret_provider=provider,
    )
    assert restored.last_known_clock_ts == ts


def test_verify_token_reads_time_from_injected_clock() -> None:
    """G3: verify_token must take 'now' from the ClockProvider, not the caller.

    A validator constructed with a future-set fake clock must trip the
    rollback check when the clock is then rewound — proving the timestamp
    flows exclusively through the injected clock.
    """
    priv_key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    payload_dict = {
        "user_id": "usr_g3",
        "tier": "PRO",
        "hardware_fingerprint": "HW_G3",
        "issued_at": now - 60,
        "expires_at": now + 3600,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    clock = FakeWallClock(now)
    validator = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint="HW_G3",
        last_online_sync_ts=now,
        clock=clock,
    )
    # Forward time on the injected clock: verification succeeds
    clock.set(now + 60)
    verified = validator.verify_token(token_str)
    assert verified.user_id == "usr_g3"

    # Rewind the injected clock: rollback check trips
    clock.set(now - 3600)
    with pytest.raises(LicenseError, match="clock manipulation detected") as exc_info:
        validator.verify_token(token_str)
    assert exc_info.value.code == "CLOCK_ROLLBACK_DETECTED"


def test_verify_token_signature_has_no_current_ts() -> None:
    """G3 guard: the anti-rollback bypass parameter must stay removed."""
    import inspect

    sig = inspect.signature(LicenseValidator.verify_token)
    assert "current_ts" not in sig.parameters, "current_ts parameter was re-introduced"


def test_hwm_key_loss_quarantines_file_and_reanchors(tmp_path: Path) -> None:
    """G5: a lost vault key must not permanently lock the user out.

    An HWM file signed by a key that no longer exists gets quarantined
    (never deleted) and the in-memory HWM re-anchors to the machine clock —
    the file's unverifiable values are NOT adopted as an anti-rollback floor.
    """
    import hashlib
    import hmac as hmac_mod

    from src.safety.secret_provider import InMemorySecretProvider

    provider = InMemorySecretProvider()
    priv_key = ed25519.Ed25519PrivateKey.generate()

    # Seed a validator so the vault gets its HWM key, then drop the key
    seed = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint="HW_KEYLOSS",
        secret_provider=provider,
    )
    old_key = seed._hwm_key
    provider.delete_secret("LICENSE_HWM_HMAC_KEY")

    # Plant an HWM file signed with the now-dead key claiming a FUTURE ts
    hwm_file = tmp_path / "hwm_keyloss.dat"
    planted_ts = int(time.time()) + 999_999  # hostile future timestamp
    planted_mac = hmac_mod.new(old_key, f"{planted_ts}:{planted_ts}".encode(), hashlib.sha256).hexdigest()
    hwm_file.write_text(f"{planted_ts}:{planted_ts}.{planted_mac}", encoding="utf-8")

    validator = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint="HW_KEYLOSS",
        high_water_mark_path=hwm_file,
        secret_provider=provider,  # fresh key will be minted -> HMAC fails
    )

    # The hostile future ts must NOT be adopted
    assert validator.last_known_clock_ts < planted_ts

    # The unverifiable file was quarantined, not deleted
    quarantined = list(tmp_path.glob("hwm_keyloss.dat.lostkey-*"))
    assert len(quarantined) == 1
    assert not hwm_file.exists()


def test_hwm_write_is_atomic(tmp_path: Path) -> None:
    """G6: verify_token writes the HWM via temp+replace (no truncation window)."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    payload_dict = {
        "user_id": "usr_atomic",
        "tier": "PRO",
        "hardware_fingerprint": "HW_ATOMIC",
        "issued_at": now - 60,
        "expires_at": now + 3600,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)
    hwm_file = tmp_path / "hwm_atomic.dat"

    clock = FakeWallClock(now)
    validator = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint="HW_ATOMIC",
        high_water_mark_path=hwm_file,
        clock=clock,
    )
    validator.verify_token(token_str)

    # No .tmp leftovers; the final file is complete and parseable
    assert not list(tmp_path.glob("*.tmp"))
    content = hwm_file.read_text(encoding="utf-8").strip()
    hwm_part, mac = content.rsplit(".", 1)
    hwm_str, sync_str = hwm_part.split(":")
    assert hwm_str == str(now)
    assert len(mac) == 64


def test_indeterminate_fingerprint_is_refused_fail_closed() -> None:
    """SEC-T40-1: a collector sentinel must never satisfy the hardware lock.

    src/security/hwid/collector.py substitutes UNKNOWN_CPU / UNKNOWN_DISK /
    UNKNOWN_BIOS and FALLBACK-<host>-<mac> when a WMI read fails. Those strings
    are byte-identical across hosts, so a token carrying one would verify on ANY
    machine in that state — the hardware bind would be dead. The validator must
    refuse before comparing.
    """
    priv_key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())

    sentinels = [
        "UNKNOWN_CPU-UNKNOWN_DISK-UNKNOWN_BIOS",
        "FALLBACK-DESKTOP-ABC-AA:BB:CC:DD:EE:FF",
    ]

    for sentinel in sentinels:
        payload_dict = {
            "user_id": "usr_multi",
            "tier": "ENTERPRISE",
            "hardware_fingerprint": sentinel,
            "issued_at": now - 60,
            "expires_at": now + 86400 * 365,
        }
        token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

        # Both sides carry the SAME sentinel — the pre-fix code returned success.
        validator = LicenseValidator(
            public_key=priv_key.public_key(),
            hardware_fingerprint=sentinel,
            last_online_sync_ts=now,
        )
        validator.clock = FakeWallClock(now)

        with pytest.raises(LicenseError) as exc_info:
            validator.verify_token(token_str)
        assert exc_info.value.code == "HARDWARE_INDETERMINATE", (
            f"sentinel {sentinel!r} must be refused as indeterminate"
        )


def test_wildcard_is_not_treated_as_indeterminate() -> None:
    """The explicit opt-in wildcard path must keep working (guard against
    over-broad matching in _is_indeterminate_fingerprint)."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    payload_dict = {
        "user_id": "usr_test",
        "tier": "ENTERPRISE",
        "hardware_fingerprint": "*",
        "issued_at": now - 60,
        "expires_at": now + 86400,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    validator = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint="HW_REAL_DEVICE",
        last_online_sync_ts=now,
        allow_wildcard_license=True,
    )
    validator.clock = FakeWallClock(now)
    payload = validator.verify_token(token_str)
    assert payload.hardware_fingerprint == "*"


def test_real_fingerprint_still_verifies() -> None:
    """The normal path must not regress: a genuine device fingerprint verifies."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    fp = "a1b2c3d4e5f60718-random-real-device-uuid"
    payload_dict = {
        "user_id": "usr_real",
        "tier": "PRO",
        "hardware_fingerprint": fp,
        "issued_at": now - 60,
        "expires_at": now + 86400,
    }
    token_str = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    validator = LicenseValidator(
        public_key=priv_key.public_key(),
        hardware_fingerprint=fp,
        last_online_sync_ts=now,
    )
    validator.clock = FakeWallClock(now)
    assert validator.verify_token(token_str).hardware_fingerprint == fp

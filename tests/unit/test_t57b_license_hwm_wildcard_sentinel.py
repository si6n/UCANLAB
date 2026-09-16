"""T57-B regression tests for the YUKSEK license findings L-2..L-6 + V-1.

Each test was written BEFORE the corresponding fix (TDD red -> green).

  * L-2 (`src/launcher/auth.py`)   — launcher's `LicenseFlow` must share the
        SAME persistent anti-rollback HWM path as `desktop_app`.
  * L-3 (`license_flow.py`)        — `_hmac_key` must NOT swallow a
        `store_secret` failure and emit a fresh random key every process;
        it must fail closed with `HWM_KEY_UNAVAILABLE`.
  * L-4 (`validator.py`)           — wildcard licenses are forbidden in a
        frozen (production) build.
  * L-5 (`validator.py`)           — the sentinel-fingerprint gate must also
        inspect the fingerprint carried by the TOKEN, and `NON_WIN32-` is a
        sentinel marker.
  * L-6 (`license_flow.py`, `auth.py`) — `exp` and `offline_until` are joined
        with `min()` semantics (a long `exp` may not mask a short grace), and
        the offline callers always pass an explicit `expected_device_id`.
  * V-1 (`validator.py`)           — a missing `high_water_mark_path` emits a
        CRITICAL log instead of silently advancing the anchor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError
from src.safety.secret_provider import EphemeralSecretBackend, InMemorySecretProvider
from src.security.cloud.client import CloudClient, CloudConfig
from src.security.cloud.license_flow import LicenseFlow, default_license_hwm_path
from src.security.license.validator import LicenseValidator


class FakeWallClock:
    """Deterministic wall clock for LicenseValidator (mirrors test_license_validator)."""

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


# ---------------------------------------------------------------------------
# helpers: build signed cloud tickets
# ---------------------------------------------------------------------------

def _sign_ticket(key: ed25519.Ed25519PrivateKey, **overrides: object) -> str:
    now = int(time.time())
    body: dict[str, object] = {
        "iss": "universal-can-cloud",
        "aud": "diagnostic-desktop-app",
        "kid": "v1",
        "license_id": "lic_t57b",
        "organization_id": "org_t57b",
        "device_id": "dev_t57b",
        "tier": "marine_pro",
        "features": ["j1939"],
        "iat": now,
        "exp": now + 3600,
        "offline_until": now + 7 * 86400,
        "schema_version": 1,
        "nonce": "n" * 12,
    }
    body.update(overrides)
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    sig = key.sign(payload)
    return base64.urlsafe_b64encode(payload).decode() + "." + base64.urlsafe_b64encode(sig).decode()


def _flow_with_device(
    key: ed25519.Ed25519PrivateKey,
    *,
    device_id: str = "dev_t57b",
    hwm_path: Path | None = None,
    secrets: object | None = None,
) -> LicenseFlow:
    backend = secrets if secrets is not None else EphemeralSecretBackend()
    client = CloudClient(config=CloudConfig(), secret_provider=backend)
    client.store_device_id(device_id)
    return LicenseFlow(client, public_key=key.public_key(), hwm_path=hwm_path)


# ---------------------------------------------------------------------------
# L-2: launcher shares the persistent HWM path with desktop_app
# ---------------------------------------------------------------------------

def test_default_license_hwm_path_matches_desktop_app() -> None:
    """L-2: the canonical HWM path must be the SAME one desktop_app uses."""
    from src.ui.desktop_app import _app_data_root

    expected = _app_data_root() / "logs" / "license_hwm.txt"
    assert default_license_hwm_path() == expected


def test_launcher_flow_is_constructed_with_persistent_hwm() -> None:
    """L-2: LauncherAuthManager's LicenseFlow must NOT be in-memory-only."""
    from src.launcher.auth import LauncherAuthManager
    from src.ui.desktop_app import _app_data_root

    priv = ed25519.Ed25519PrivateKey.generate()
    secrets = EphemeralSecretBackend()
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    auth = LauncherAuthManager(cloud_client=client, secret_provider=secrets, public_key=priv.public_key())

    assert auth.flow._hwm_path is not None, "launcher LicenseFlow has no persistent HWM path"
    assert auth.flow._hwm_path == _app_data_root() / "logs" / "license_hwm.txt"


# ---------------------------------------------------------------------------
# L-3: _hmac_key fails closed when the vault cannot persist the key
# ---------------------------------------------------------------------------

class _UnstorableVault(InMemorySecretProvider):
    """Vault that refuses to persist the HWM key (simulates an unwritable DPAPI)."""

    def store_secret(self, name: str, value: bytes) -> None:  # type: ignore[override]
        if name == "LICENSE_HWM_KEY":
            raise OSError("vault is read-only")
        super().store_secret(name, value)


def test_hmac_key_fails_closed_when_vault_unwritable(tmp_path: Path) -> None:
    """L-3: a key that cannot be persisted must raise HWM_KEY_UNAVAILABLE, not
    silently mint a fresh per-process key (which would reset rollback detection
    on every restart)."""
    priv = ed25519.Ed25519PrivateKey.generate()
    vault = _UnstorableVault()
    client = CloudClient(config=CloudConfig(), secret_provider=vault)

    flow = LicenseFlow(client, public_key=priv.public_key(), hwm_path=tmp_path / "hwm.txt")
    with pytest.raises(LicenseError) as exc_info:
        flow._hmac_key()
    assert exc_info.value.code == "HWM_KEY_UNAVAILABLE"


def test_hmac_key_is_stable_across_processes(tmp_path: Path) -> None:
    """Control for L-3: with a working vault the key persists and a restart
    reuses it (the seal still verifies)."""
    priv = ed25519.Ed25519PrivateKey.generate()
    vault = InMemorySecretProvider()
    client = CloudClient(config=CloudConfig(), secret_provider=vault)
    hwm = tmp_path / "hwm.txt"

    first = LicenseFlow(client, public_key=priv.public_key(), hwm_path=hwm)
    key1 = first._hmac_key()
    # Second instance, same vault -> same key
    second = LicenseFlow(client, public_key=priv.public_key(), hwm_path=hwm)
    assert second._hmac_key() == key1


# ---------------------------------------------------------------------------
# L-4: wildcard forbidden in a frozen build
# ---------------------------------------------------------------------------

def test_wildcard_forbidden_in_frozen_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-4: `allow_wildcard_license=True` must be rejected when running frozen."""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    priv = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(LicenseError) as exc_info:
        LicenseValidator(
            public_key=priv.public_key(),
            hardware_fingerprint="HW_REAL",
            allow_wildcard_license=True,
        )
    assert exc_info.value.code == "WILDCARD_FORBIDDEN"


def test_wildcard_still_allowed_in_non_frozen_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control for L-4: non-frozen opt-in keeps working (test fixtures)."""
    monkeypatch.delattr("sys.frozen", raising=False)
    priv = ed25519.Ed25519PrivateKey.generate()
    validator = LicenseValidator(
        public_key=priv.public_key(),
        hardware_fingerprint="HW_REAL",
        allow_wildcard_license=True,
    )
    assert validator._allow_wildcard is True


# ---------------------------------------------------------------------------
# L-5: token-side sentinel gate + NON_WIN32- marker
# ---------------------------------------------------------------------------

def test_token_side_sentinel_fingerprint_is_refused() -> None:
    """L-5: a token carrying a collector sentinel must be refused even when the
    local fingerprint is a genuine device id."""
    priv = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    token = LicenseValidator.generate_signed_token(
        priv,
        {
            "user_id": "usr_sentinel",
            "tier": "PRO",
            "hardware_fingerprint": "FALLBACK-HOST-AA:BB:CC:DD:EE:FF",
            "issued_at": now - 60,
            "expires_at": now + 86400,
        },
    )
    validator = LicenseValidator(
        public_key=priv.public_key(),
        hardware_fingerprint="genuine-device-uuid-1234",
        last_online_sync_ts=now,
    )
    validator.clock = FakeWallClock(now)
    with pytest.raises(LicenseError) as exc_info:
        validator.verify_token(token)
    assert exc_info.value.code == "HARDWARE_INDETERMINATE"


def test_non_win32_sentinel_is_refused() -> None:
    """L-5: the collector's NON_WIN32- fallback prefix is a sentinel marker."""
    priv = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    sentinel = "NON_WIN32-host-x86_64-intel-aa:bb:cc:dd:ee:ff"
    token = LicenseValidator.generate_signed_token(
        priv,
        {
            "user_id": "usr_nonwin",
            "tier": "PRO",
            "hardware_fingerprint": sentinel,
            "issued_at": now - 60,
            "expires_at": now + 86400,
        },
    )
    validator = LicenseValidator(
        public_key=priv.public_key(),
        hardware_fingerprint=sentinel,
        last_online_sync_ts=now,
    )
    validator.clock = FakeWallClock(now)
    with pytest.raises(LicenseError) as exc_info:
        validator.verify_token(token)
    assert exc_info.value.code == "HARDWARE_INDETERMINATE"


def test_wildcard_not_broken_by_token_side_sentinel_gate() -> None:
    """L-5 control: '*' is an explicit opt-in, not an indeterminate sentinel."""
    priv = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    token = LicenseValidator.generate_signed_token(
        priv,
        {
            "user_id": "usr_wild",
            "tier": "ENTERPRISE",
            "hardware_fingerprint": "*",
            "issued_at": now - 60,
            "expires_at": now + 86400,
        },
    )
    validator = LicenseValidator(
        public_key=priv.public_key(),
        hardware_fingerprint="genuine-device-uuid",
        last_online_sync_ts=now,
        allow_wildcard_license=True,
    )
    validator.clock = FakeWallClock(now)
    assert validator.verify_token(token).hardware_fingerprint == "*"


# ---------------------------------------------------------------------------
# L-6: min(exp, offline_until) deadline + explicit expected_device_id
# ---------------------------------------------------------------------------

def test_short_grace_caps_long_exp_when_called_online() -> None:
    """L-6: a ticket with a long `exp` but a short `offline_until` must NOT be
    accepted past the grace deadline even when the caller reports online."""
    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    # exp far in the future, but offline grace already elapsed
    token = _sign_ticket(
        key,
        exp=now + 365 * 86400,
        offline_until=now - 10,
        nonce="n" * 12,
    )
    flow = _flow_with_device(key)
    with pytest.raises(LicenseError) as exc_info:
        flow.verify_cloud_ticket(token, is_offline=False)
    assert exc_info.value.code in {"LICENSE_EXPIRED", "OFFLINE_GRACE_EXPIRED"}


def test_grace_deadline_min_semantics() -> None:
    """L-6: `min(exp, offline_until)` — exp shorter than grace still expires."""
    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    token = _sign_ticket(
        key,
        exp=now - 10,
        offline_until=now + 7 * 86400,
        nonce="n" * 12,
    )
    flow = _flow_with_device(key)
    with pytest.raises(LicenseError) as exc_info:
        flow.verify_cloud_ticket(token, is_offline=True)
    assert exc_info.value.code == "LICENSE_EXPIRED"


def test_launcher_offline_check_passes_expected_device_id() -> None:
    """L-6: LauncherAuthManager's offline re-verification must always pass an
    explicit expected_device_id (not rely on the client fallback)."""
    from src.launcher.auth import LauncherAuthManager

    priv = ed25519.Ed25519PrivateKey.generate()
    secrets = EphemeralSecretBackend()
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    auth = LauncherAuthManager(cloud_client=client, secret_provider=secrets, public_key=priv.public_key())

    seen: dict[str, object] = {}

    def _spy(token: str, expected_device_id: str | None = None, is_offline: bool = False):
        seen["expected_device_id"] = expected_device_id
        seen["is_offline"] = is_offline
        raise LicenseError("stop", code="STOP")

    # Register a device id + a stored ticket so the offline branch is reached
    # with a concrete device id in the vault.
    client.store_device_id("dev_registered_001")
    secrets.store_secret("CLOUD_LICENSE_TICKET", b"x.y")
    auth.flow.verify_cloud_ticket = _spy  # type: ignore[assignment]
    auth.get_current_status()

    assert seen.get("expected_device_id") == "dev_registered_001", (
        "launcher must pass the registered device id explicitly"
    )
    assert seen.get("is_offline") is True


# ---------------------------------------------------------------------------
# V-1: missing HWM path logs CRITICAL instead of silently advancing
# ---------------------------------------------------------------------------

def test_missing_hwm_path_logs_critical(caplog: pytest.LogCaptureFixture) -> None:
    """V-1: with no persistence path the validator must emit a CRITICAL warning
    that rollback protection is session-only."""
    priv = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    token = LicenseValidator.generate_signed_token(
        priv,
        {
            "user_id": "usr_v1",
            "tier": "PRO",
            "hardware_fingerprint": "HW_V1",
            "issued_at": now - 60,
            "expires_at": now + 3600,
        },
    )
    validator = LicenseValidator(
        public_key=priv.public_key(),
        hardware_fingerprint="HW_V1",
        last_online_sync_ts=now,
        high_water_mark_path=None,
    )
    validator.clock = FakeWallClock(now)
    with caplog.at_level(logging.CRITICAL, logger="security.license"):
        validator.verify_token(token)

    critical = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical, "no CRITICAL emitted when HWM persistence is disabled"


# keep the hwm-module import meaningful for linters even if a test is skipped
_ = hashlib

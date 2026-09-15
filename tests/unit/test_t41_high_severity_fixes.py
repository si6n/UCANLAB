"""T41 regression tests — HIGH severity findings from the T40 security review.

Covers:
  Y-1  src/safety/gateway.py   — legacy boolean dual-confirm bypass (G-1)
  Y-2  src/safety/state_machine.py — arm_tx/activate_tx auth_token not verified (S-1)
  Y-3  src/launcher/auth.py    — silent fail-open on embedded public key decode (LA-1)

Each test first asserts the FIXED (fail-closed) behavior; before the fix the
corresponding test fails (proving the vulnerability), after the fix it passes.
"""

from __future__ import annotations

import base64
import logging
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError, SafetyError
from src.hal.virtual import VirtualBus
from src.launcher.auth import LauncherAuthManager
from src.safety.exceptions import DualConfirmationRequiredError
from src.safety.gateway import TxSafetyGateway
from src.safety.secret_provider import EphemeralSecretBackend
from src.safety.state_machine import SafetyState, SafetySupervisor

CONFIRM_SECRET = b"t41-confirmation-secret-32-bytes!!"


def _bus() -> VirtualBus:
    bus = VirtualBus(channel_id="t41_vbus")
    bus.connect()
    return bus


# ---------------------------------------------------------------------------
# Y-1 — gateway: configured secret + missing token must fail closed
# ---------------------------------------------------------------------------


def test_y1_configured_secret_without_token_fails_closed() -> None:
    """Secret configured + no token + user_confirmed=True must be REJECTED."""
    from src.core.models.can_frame import CanFrame

    gateway = TxSafetyGateway(bus=_bus(), whitelist_ids={0x7E0}, confirmation_secret=CONFIRM_SECRET)
    gateway.update_vehicle_speed(0.0)
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02")

    with pytest.raises(DualConfirmationRequiredError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)


def test_y1_configured_secret_with_valid_token_succeeds() -> None:
    """The HMAC token path still works after hardening."""
    from src.core.models.can_frame import CanFrame

    gateway = TxSafetyGateway(bus=_bus(), whitelist_ids={0x7E0}, confirmation_secret=CONFIRM_SECRET)
    gateway.update_vehicle_speed(0.0)
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x03")
    token = gateway.issue_confirmation_token(0x7E0)

    assert gateway.validate_and_transmit(
        frame, is_critical_command=True, user_confirmed=True, confirmation_token=token
    ) is True


def test_y1_legacy_flag_true_allows_boolean_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Explicit legacy opt-in keeps the boolean path alive (with WARNING)."""
    from src.core.models.can_frame import CanFrame

    gateway = TxSafetyGateway(
        bus=_bus(),
        whitelist_ids={0x7E0},
        confirmation_secret=CONFIRM_SECRET,
        allow_legacy_boolean_confirm=True,
    )
    gateway.update_vehicle_speed(0.0)
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x04")

    with caplog.at_level(logging.WARNING):
        assert gateway.validate_and_transmit(
            frame, is_critical_command=True, user_confirmed=True
        ) is True
    assert any("legacy boolean confirmation" in r.getMessage() for r in caplog.records)


def test_y1_unconfigured_secret_keeps_legacy_boolean() -> None:
    """No secret configured => dual-confirm is not enforced (backward compat)."""
    from src.core.models.can_frame import CanFrame

    gateway = TxSafetyGateway(bus=_bus(), whitelist_ids={0x7E0})
    gateway.update_vehicle_speed(0.0)
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x05")

    assert gateway.validate_and_transmit(
        frame, is_critical_command=True, user_confirmed=True
    ) is True


# ---------------------------------------------------------------------------
# Y-2 — state machine: ARM/ACTIVATE must verify auth_token when configured
# ---------------------------------------------------------------------------


def _passive_supervisor(**kwargs) -> SafetySupervisor:
    return SafetySupervisor(initial_state=SafetyState.PASSIVE, **kwargs)


def test_y2_configured_secret_missing_token_fails_closed() -> None:
    """auth_secret configured + auth_token None => fail-closed SafetyError."""
    supervisor = _passive_supervisor(auth_secret=b"t41-arm-secret-32-bytes-long!!!")
    with pytest.raises(SafetyError):
        supervisor.arm_tx("operator arm", auth_token=None)
    assert supervisor.current_state == SafetyState.PASSIVE


def test_y2_configured_secret_wrong_token_fails_closed() -> None:
    supervisor = _passive_supervisor(auth_secret=b"t41-arm-secret-32-bytes-long!!!")
    with pytest.raises(SafetyError):
        supervisor.arm_tx("operator arm", auth_token="deadbeef" * 8)
    assert supervisor.current_state == SafetyState.PASSIVE


def test_y2_valid_hmac_token_arms_and_activates() -> None:
    secret = b"t41-arm-secret-32-bytes-long!!!"
    supervisor = _passive_supervisor(auth_secret=secret)
    token = supervisor.issue_arm_token()
    supervisor.arm_tx("operator arm", auth_token=token)
    assert supervisor.current_state == SafetyState.ARMED_TX

    activate_token = supervisor.issue_arm_token()
    supervisor.activate_tx("stream", auth_token=activate_token)
    assert supervisor.current_state == SafetyState.ACTIVE


def test_y2_allow_unauthenticated_flag_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UCANLAB_TEST_MODE", "1")
    supervisor = _passive_supervisor(
        auth_secret=b"t41-arm-secret-32-bytes-long!!!",
        allow_unauthenticated_arm=True,
    )
    supervisor.arm_tx("operator arm", auth_token=None)
    assert supervisor.current_state == SafetyState.ARMED_TX


def test_y2_unconfigured_supervisor_stays_backward_compatible() -> None:
    """Bare supervisor (no secret) keeps legacy behavior — existing callers."""
    supervisor = _passive_supervisor()
    supervisor.arm_tx("Operator explicit authorization")
    assert supervisor.current_state == SafetyState.ARMED_TX
    supervisor.activate_tx("Active transmission stream")
    assert supervisor.current_state == SafetyState.ACTIVE


# ---------------------------------------------------------------------------
# Y-3 — launcher auth: embedded key decode failure must fail closed
# ---------------------------------------------------------------------------


def test_y3_corrupt_embedded_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupt embedded key must raise LicenseError, not silently disable licensing."""
    monkeypatch.setattr(
        "src.launcher.auth.DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64",
        "!!!not-valid-base64!!!",
    )
    secrets = EphemeralSecretBackend()
    with pytest.raises(LicenseError):
        LauncherAuthManager(secret_provider=secrets)


def test_y3_truncated_embedded_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A base64 blob of the wrong length must also fail closed."""
    monkeypatch.setattr(
        "src.launcher.auth.DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64",
        base64.b64encode(b"too-short").decode("ascii"),
    )
    secrets = EphemeralSecretBackend()
    with pytest.raises(LicenseError):
        LauncherAuthManager(secret_provider=secrets)


def test_y3_valid_explicit_key_still_works() -> None:
    secrets = EphemeralSecretBackend()
    pub = ed25519.Ed25519PrivateKey.generate().public_key()
    auth = LauncherAuthManager(secret_provider=secrets, public_key=pub)
    assert auth.flow is not None
    auth.logout()


# ---------------------------------------------------------------------------
# Y-1-related H-2 — collector must not log the PowerShell command text
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="_run_powershell is Windows-only; the logging assertion needs it to reach its except branch",
)
def test_h2_powershell_failure_does_not_log_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing PowerShell call must log a fixed label, never the command text."""
    import logging as _logging

    from src.security.hwid import collector as _collector

    records: list[str] = []

    class _Capture(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Capture()
    _collector.logger.addHandler(handler)
    try:
        def _boom(*_a, **_k):
            raise OSError("no powershell")

        monkeypatch.setattr(_collector.subprocess, "run", _boom)
        out = _collector._run_powershell("SECRET-SENTINEL-COMMAND")
    finally:
        _collector.logger.removeHandler(handler)

    assert out == ""
    assert not any("SECRET-SENTINEL-COMMAND" in msg for msg in records)
    assert any("PowerShell command failed" in msg for msg in records)


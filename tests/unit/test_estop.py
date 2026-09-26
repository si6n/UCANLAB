"""Unit tests for EmergencyStopSystem, 10 trigger sources, and replay-protected HMAC reset."""

from __future__ import annotations

import os
import threading
import time

import pytest

from src.core.errors import SafetyError
from src.safety.estop import EmergencyStopSystem, EStopEvent, EStopTriggerSource


def test_estop_initial_state() -> None:
    """Verify default clean unengaged state upon instantiation."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    assert not estop.is_engaged
    assert estop.last_event is None
    assert estop.get_reset_nonce() == b""


@pytest.mark.parametrize("trigger_src", list(EStopTriggerSource))
def test_estop_10_trigger_sources(trigger_src: EStopTriggerSource) -> None:
    """Verify all 10 E-Stop trigger sources engage the system and generate event records."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    reason = f"Test trigger {trigger_src.name}"
    speed = 42.5

    estop.trigger(trigger_src, reason=reason, vehicle_speed_kmh=speed)

    assert estop.is_engaged
    event = estop.last_event
    assert event is not None
    assert isinstance(event, EStopEvent)
    assert event.trigger == trigger_src
    assert event.reason == reason
    assert event.system_speed_kmh == speed
    assert isinstance(event.timestamp_ns, int)
    assert event.timestamp_ns > 0

    nonce = estop.get_reset_nonce()
    assert isinstance(nonce, bytes)
    assert len(nonce) == 16


def test_estop_hmac_reset_success() -> None:
    """Verify proper challenge-response reset flow with structured token."""
    secret = b"test_secret_key_32_bytes_long!!!"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)

    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Operator hit red button")
    assert estop.is_engaged

    # Operator gets challenge nonce
    nonce = estop.get_reset_nonce()
    assert len(nonce) == 16

    # Authorized party computes reset token
    token = estop.compute_reset_token(nonce)
    assert token != ""

    # Submit token to reset
    estop.reset(token)

    # Verify disengaged
    assert not estop.is_engaged
    # B10: the engagement audit record survives the reset â€” only the
    # challenge state is cleared, the "why did we stop" evidence remains.
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.USER_UI_BUTTON
    assert estop.get_reset_nonce() == b""


def test_estop_hmac_reset_denied_invalid_token() -> None:
    """Verify rejection when an invalid token is provided."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.RATE_LIMIT_OVERFLOW, reason="Tx rate limit exceeded")

    with pytest.raises(SafetyError, match="Invalid E-Stop reset token") as exc_info:
        estop.reset("0000000000000000000000000000000000000000000000000000000000000000")

    assert exc_info.value.code == "ESTOP_RESET_DENIED"
    # System must remain engaged
    assert estop.is_engaged


def test_estop_nonce_cleared_prevents_replay() -> None:
    """Verify that once reset, the old token cannot be reused after a second trigger."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.SPEED_INTERLOCK_BREACH, reason="Speed breach", vehicle_speed_kmh=120.0)

    token1 = estop.compute_reset_token()
    estop.reset(token1)
    assert not estop.is_engaged

    # Trigger second time
    estop.trigger(EStopTriggerSource.TEMPERATURE_OVERHEAT, reason="Inverter over-temp")
    assert estop.is_engaged

    # Replay token1 -> Must fail because epoch and nonce changed
    with pytest.raises(SafetyError):
        estop.reset(token1)

    assert estop.is_engaged


def test_estop_custom_reset_secret() -> None:
    """Verify custom reset secret provided in constructor is honored."""
    custom_secret = os.urandom(64)
    estop = EmergencyStopSystem(reset_secret=custom_secret, allow_self_reset=True)
    estop.trigger(EStopTriggerSource.HARDWARE_DISCONNECT, reason="CAN adapter unplugged")

    token = estop.compute_reset_token()
    assert token != ""

    estop.reset(token)
    assert not estop.is_engaged


def test_estop_callbacks_and_error_isolation() -> None:
    """Verify all callbacks are executed and exceptions in one callback do not abort others."""
    estop = EmergencyStopSystem(allow_self_reset=True)

    events_received: list[EStopEvent] = []

    def cb1(event: EStopEvent) -> None:
        events_received.append(event)

    def cb_failing(event: EStopEvent) -> None:
        raise RuntimeError("Callback exploded!")

    def cb2(event: EStopEvent) -> None:
        events_received.append(event)

    estop.register_callback(cb1)
    estop.register_callback(cb_failing)
    estop.register_callback(cb2)

    estop.trigger(EStopTriggerSource.BUS_OFF_DETECTED, reason="Bus-off controller fault")

    assert len(events_received) == 2
    assert events_received[0].trigger == EStopTriggerSource.BUS_OFF_DETECTED
    assert events_received[1].trigger == EStopTriggerSource.BUS_OFF_DETECTED


def test_estop_reset_when_not_engaged_is_noop() -> None:
    """Verify calling reset when not engaged returns cleanly without error."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    assert not estop.is_engaged
    # Should not raise exception
    estop.reset("dummy_token")
    assert not estop.is_engaged


def test_estop_concurrent_triggers_thread_safety() -> None:
    """Verify thread safety under concurrent triggers and queries."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            for _ in range(50):
                estop.trigger(EStopTriggerSource.COMMUNICATION_TIMEOUT, reason=f"Thread {idx}")
                _ = estop.is_engaged
                _ = estop.last_event
                _ = estop.get_reset_nonce()
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert estop.is_engaged


def test_estop_concurrent_trigger_and_reset_toctou_safety() -> None:
    """Stress test E-Stop TOCTOU safety under rapid interleaved concurrent triggers and resets."""
    secret = b"toctou_verification_secret_32b!"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)
    errors: list[Exception] = []
    iterations = 200

    def trigger_loop() -> None:
        try:
            for i in range(iterations):
                estop.trigger(
                    EStopTriggerSource.SPEED_INTERLOCK_BREACH,
                    reason=f"Interlock cycle {i}",
                )
        except Exception as exc:
            errors.append(exc)

    def reset_loop() -> None:
        try:
            for _ in range(iterations):
                nonce = estop.get_reset_nonce()
                if nonce:
                    token = estop.compute_reset_token(nonce)
                    try:
                        estop.reset(token)
                    except SafetyError:
                        pass  # Invalid token due to interleaved re-trigger is expected
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=trigger_loop)
    t2 = threading.Thread(target=reset_loop)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors


def test_estop_secret_store_failure_fails_closed() -> None:
    """F1 (Aksiyon 18): verify store_secret failure immediately triggers E-Stop fail-closed."""
    from unittest.mock import MagicMock

    from src.core.exceptions import SafetyError
    from src.safety.secret_provider import SecretProvider

    mock_provider = MagicMock(spec=SecretProvider)
    mock_provider.has_secret.return_value = False
    mock_provider.store_secret.side_effect = OSError("Disk storage unavailable / permission denied")
    mock_provider.get_secret.return_value = b"\x00" * 32
    mock_provider.protection_downgraded = False

    estop = EmergencyStopSystem(secret_provider=mock_provider)

    # Must be engaged fail-closed immediately upon initialization
    assert estop.is_engaged is True
    assert estop.protection_downgraded is True
    assert estop.last_event is not None
    assert "fail-closed" in estop.last_event.reason

    # Arming must be refused fail-closed
    with pytest.raises(SafetyError) as exc_info:
        estop.assert_arm_permitted("arm_tx")
    assert exc_info.value.code == "ESTOP_PROTECTION_DOWNGRADED"


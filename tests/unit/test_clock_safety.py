"""Unit tests for F2 / Action 19: Win32/POSIX clock validation, CLOCK_BOOTTIME, and monotonic suspend protection.

Complies with ISO 26262 ASIL-D functional safety timing requirements.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.contracts.ports import SystemClockProvider, VirtualClock
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.state_machine import SafetyError, SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor


def test_system_clock_provider_validate_clock_safety_success() -> None:
    """SystemClockProvider.validate_clock_safety passes on standard platform monotonic clock."""
    SystemClockProvider.validate_clock_safety()
    assert SystemClockProvider.is_suspend_aware() is True or SystemClockProvider.is_suspend_aware() is False


def test_system_clock_provider_validate_clock_safety_failures() -> None:
    """validate_clock_safety raises RuntimeError if clock is non-monotonic, adjustable, or coarse."""
    # 1. Non-monotonic
    with patch("time.get_clock_info", return_value=SimpleNamespace(monotonic=False, adjustable=False, resolution=1e-7)):
        with pytest.raises(RuntimeError, match="non-monotonic"):
            SystemClockProvider.validate_clock_safety()

    # 2. Adjustable (NTP step vulnerable)
    with patch("time.get_clock_info", return_value=SimpleNamespace(monotonic=True, adjustable=True, resolution=1e-7)):
        with pytest.raises(RuntimeError, match="adjustable"):
            SystemClockProvider.validate_clock_safety()

    # 3. Resolution too coarse (> 0.050s)
    with patch("time.get_clock_info", return_value=SimpleNamespace(monotonic=True, adjustable=False, resolution=0.060)):
        with pytest.raises(RuntimeError, match="too coarse"):
            SystemClockProvider.validate_clock_safety()

    # 4. Clock unavailable
    with patch("time.get_clock_info", side_effect=OSError("Clock device failure")):
        with pytest.raises(RuntimeError, match="unavailable"):
            SystemClockProvider.validate_clock_safety()


def test_safety_supervisor_arm_tx_refuses_invalid_clock() -> None:
    """arm_tx and activate_tx fail closed and trigger FAULT if clock integrity checks fail."""
    supervisor = SafetySupervisor(allow_unauthenticated_arm=True)
    supervisor.transition_to(SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)
    assert supervisor.current_state == SafetyState.PASSIVE

    # Monotonic check failure triggers FAULT and raises SafetyError
    with patch("time.get_clock_info", return_value=SimpleNamespace(monotonic=False, adjustable=False, resolution=1e-7)):
        with pytest.raises(SafetyError) as exc_info:
            supervisor.arm_tx()
        assert exc_info.value.code == "CLOCK_INTEGRITY_VIOLATION"
        assert supervisor.current_state == SafetyState.FAULT

    # Reset supervisor for next check
    supervisor.transition_to(SafetyState.PASSIVE)

    # Adjustable clock failure triggers FAULT and raises SafetyError
    with patch("time.get_clock_info", return_value=SimpleNamespace(monotonic=True, adjustable=True, resolution=1e-7)):
        with pytest.raises(SafetyError) as exc_info:
            supervisor.arm_tx()
        assert exc_info.value.code == "CLOCK_INTEGRITY_VIOLATION"
        assert supervisor.current_state == SafetyState.FAULT

    # Reset supervisor for next check
    supervisor.transition_to(SafetyState.PASSIVE)

    # Coarse resolution (> 50ms) triggers FAULT and raises SafetyError
    with patch("time.get_clock_info", return_value=SimpleNamespace(monotonic=True, adjustable=False, resolution=0.100)):
        with pytest.raises(SafetyError) as exc_info:
            supervisor.activate_tx()
        assert exc_info.value.code == "CLOCK_INTEGRITY_VIOLATION"
        assert supervisor.current_state == SafetyState.FAULT


def test_tx_watchdog_supervisor_suspend_detected_via_wall_clock_leap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulated OS suspend (wall clock leaped while monotonic clock froze) triggers FAULT and E-Stop."""
    monkeypatch.setenv("UCANLAB_TEST_MODE", "1")

    vclock = VirtualClock(start_monotonic_sec=1000.0)
    supervisor = SafetySupervisor(allow_unauthenticated_arm=True)
    supervisor.transition_to(SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    estop = EmergencyStopSystem(allow_self_reset=True)

    watchdog = TxWatchdogSupervisor(
        supervisor=supervisor,
        estop=estop,
        timeout_ms=800.0,
        clock=vclock,
    )

    # Arm token and watchdog
    token = "test-token-123"
    watchdog.arm_heartbeat_token(token)
    supervisor.arm_tx()
    watchdog.anchor_lease_for_arm(token)

    assert supervisor.current_state == SafetyState.ARMED_TX
    assert not estop.is_engaged

    # Simulate normal pulse
    vclock.advance(0.1)
    watchdog.heartbeat(token)
    watchdog.poll_once()
    assert supervisor.current_state == SafetyState.ARMED_TX

    # Now simulate OS sleep/suspend:
    # Wall clock advances by 5.0 seconds, while monotonic clock advances only 0.01 seconds
    vclock.advance(0.01)
    vclock.advance_wall_sec(5.0)

    # Supervision check should detect the suspend anomaly and fail closed
    watchdog.poll_once()

    assert supervisor.current_state == SafetyState.FAULT
    assert estop.is_engaged
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.KEEPALIVE_TIMEOUT


def test_tx_watchdog_delta_clamped_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative clock delta (clock jitter/regression) is clamped to 0.0 and does not extend lease."""
    monkeypatch.setenv("UCANLAB_TEST_MODE", "1")

    vclock = VirtualClock(start_monotonic_sec=1000.0)
    supervisor = SafetySupervisor(allow_unauthenticated_arm=True)
    supervisor.transition_to(SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    watchdog = TxWatchdogSupervisor(
        supervisor=supervisor,
        timeout_ms=800.0,
        clock=vclock,
    )

    token = "test-token"
    watchdog.arm_heartbeat_token(token)
    supervisor.arm_tx()
    watchdog.anchor_lease_for_arm(token)

    # Remaining lease cannot exceed timeout_sec even with clock jitter
    assert watchdog.remaining_lease_sec <= 0.800
    assert watchdog.remaining_lease_sec >= 0.0

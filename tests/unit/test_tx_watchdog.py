"""Unit tests for TX Watchdog and Heartbeat Lease Supervisor."""

from __future__ import annotations

import time

from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.state_machine import SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor


def test_tx_watchdog_lease_and_heartbeat() -> None:
    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)
    supervisor.arm_tx()

    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=200.0)
    assert watchdog.is_lease_valid is True
    assert watchdog.remaining_lease_sec > 0.1

    # Keep lease alive with heartbeat
    time.sleep(0.05)
    watchdog.heartbeat()
    assert watchdog.is_lease_valid is True


def test_tx_watchdog_timeout_revokes_tx_and_triggers_estop() -> None:
    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)
    supervisor.arm_tx()
    assert supervisor.is_tx_permitted is True

    estop = EmergencyStopSystem()
    assert estop.is_engaged is False

    # Short timeout for fast unit testing (100ms)
    watchdog = TxWatchdogSupervisor(supervisor=supervisor, estop=estop, timeout_ms=100.0)
    watchdog.start()

    try:
        # Wait 200ms without heartbeat
        time.sleep(0.20)

        # Verify that supervisor entered FAULT state
        assert supervisor.current_state == SafetyState.FAULT
        assert supervisor.is_tx_permitted is False
        assert "WATCHDOG_TIMEOUT" in supervisor.fault_reason

        # Verify that E-Stop engaged
        assert estop.is_engaged is True
        assert estop.last_event is not None
        assert estop.last_event.trigger == EStopTriggerSource.KEEPALIVE_TIMEOUT
    finally:
        watchdog.stop()


# ============================================================================
# F-16 / E-11: UI-freeze simulation DoD tests
# The production watchdog runs at 800ms with a 250ms rAF-driven UI pulse
# (550ms tolerance). A genuinely frozen UI stops pulsing — the lease must
# expire. A live UI pulsing at 250ms must never expire.
# ============================================================================


def test_ui_freeze_expires_watchdog_and_triggers_estop() -> None:
    """F-16 DoD: main-thread freeze of 900ms (>800ms timeout) expires the lease."""
    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)
    supervisor.arm_tx()

    estop = EmergencyStopSystem()
    watchdog = TxWatchdogSupervisor(supervisor=supervisor, estop=estop, timeout_ms=800.0)
    watchdog.start()

    try:
        # Simulate a live UI: pulse at 250ms intervals for ~500ms...
        for _ in range(2):
            time.sleep(0.25)
            watchdog.heartbeat()
        assert watchdog.is_lease_valid is True
        assert supervisor.current_state != SafetyState.FAULT

        # ...then the UI freezes: 900ms main-thread block, no pulse.
        # 900ms > 800ms timeout; previous pulse was ~500ms ago, margin exhausted.
        time.sleep(0.9)

        assert watchdog.is_lease_valid is False
        assert supervisor.current_state == SafetyState.FAULT
        assert "WATCHDOG_TIMEOUT" in supervisor.fault_reason
        assert estop.is_engaged is True
        assert estop.last_event is not None
        assert estop.last_event.trigger == EStopTriggerSource.KEEPALIVE_TIMEOUT
    finally:
        watchdog.stop()


def test_live_ui_pulse_never_expires_watchdog() -> None:
    """F-16 DoD counterpart: a UI pulsing at 250ms holds the lease indefinitely."""
    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)
    supervisor.arm_tx()

    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)
    watchdog.start()

    try:
        # 6 pulses at 250ms = 1.5s of continuous "render activity"
        for _ in range(6):
            time.sleep(0.25)
            watchdog.heartbeat()
            assert watchdog.is_lease_valid is True, "250ms pulse must always be within the 800ms lease"
        assert supervisor.current_state != SafetyState.FAULT
    finally:
        watchdog.stop()


# ============================================================================
# P0-4 (REVIEW H-4): monitor thread lifecycle regressions
# The old loop ran `while True:` with a plain time.sleep(0.050) — stop()
# always hit the 1s join timeout, the thread never died, and a stop()/start()
# cycle produced TWO concurrent monitors both able to trigger_fault/estop.
# ============================================================================


def test_stop_actually_terminates_monitor_thread() -> None:
    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)
    watchdog.start()
    thread = watchdog._thread
    assert thread is not None and thread.is_alive()

    watchdog.stop()
    # The monitor must exit promptly (well under the old 1s join timeout).
    assert not thread.is_alive()


def test_stop_start_cycle_leaves_exactly_one_monitor() -> None:
    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)
    watchdog.start()
    watchdog.stop()
    watchdog.start()

    try:
        # Exactly one live monitor thread after the cycle — no leaked
        # immortal pre-P0-4 monitor supervising alongside the new one.
        live = [t for t in __import__("threading").enumerate() if t.name == "tx_watchdog_supervisor"]
        assert len(live) == 1
    finally:
        watchdog.stop()


def test_orderly_stop_does_not_fire_monitor_died_fault() -> None:
    """stop() is an orderly teardown — it must not trigger the
    WATCHDOG_MONITOR_DIED last-resort fault that used to fire on every
    shutdown because the thread could never exit before the join timeout."""
    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)
    supervisor.arm_tx()

    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)
    watchdog.start()
    time.sleep(0.15)
    watchdog.stop()

    assert supervisor.current_state != SafetyState.FAULT
    assert "WATCHDOG_MONITOR_DIED" not in (supervisor.fault_reason or "")

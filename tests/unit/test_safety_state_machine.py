"""Unit tests for Formal Safety State Machine, Reentrancy, Deadlock-Freedom, and Clock Monotonicity.

Complies with CAN-12 (Snapshot-Then-Release) and CAN-25 (Monotonic Clock Specialization).
"""

from __future__ import annotations

import concurrent.futures
import time
from datetime import datetime, timezone

import pytest

from src.core.errors import SafetyError
from src.safety.state_machine import SafetyState, SafetySupervisor, StateTransitionRecord


def test_safety_state_machine_initial_state_and_safe_transitions() -> None:
    """Verify normal lifecycle state transitions and permission flags."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    assert supervisor.get_state() == SafetyState.STARTUP
    assert supervisor.epoch == 0
    assert supervisor.get_epoch() == 0
    assert supervisor.is_tx_permitted is False
    assert supervisor.is_passive is False
    assert supervisor.is_fault is False

    # STARTUP -> SAFE
    supervisor.transition_to(SafetyState.SAFE, "Hardware initialized")
    assert supervisor.get_state() == SafetyState.SAFE
    assert supervisor.epoch == 1
    assert supervisor.is_tx_permitted is False

    # SAFE -> PASSIVE
    supervisor.transition_to(SafetyState.PASSIVE, "Default Listen-Only mode")
    assert supervisor.get_state() == SafetyState.PASSIVE
    assert supervisor.epoch == 2
    assert supervisor.is_passive is True
    assert supervisor.is_tx_permitted is False

    # PASSIVE -> ARMED_TX
    supervisor.arm_tx("Operator explicit authorization")
    assert supervisor.get_state() == SafetyState.ARMED_TX
    assert supervisor.epoch == 3
    assert supervisor.is_tx_permitted is True

    # ARMED_TX -> ACTIVE
    supervisor.activate_tx("Active transmission stream")
    assert supervisor.get_state() == SafetyState.ACTIVE
    assert supervisor.epoch == 4
    assert supervisor.is_tx_permitted is True

    # ACTIVE -> FAULT
    supervisor.trigger_fault("Watchdog timeout event")
    assert supervisor.get_state() == SafetyState.FAULT
    assert supervisor.epoch == 5
    assert supervisor.is_fault is True
    assert supervisor.is_tx_permitted is False
    assert "Watchdog timeout event" in supervisor.fault_reason
    assert "Watchdog timeout event" in supervisor.get_fault_reason()

    # FAULT -> PASSIVE (Allowed recovery)
    supervisor.enter_passive_mode("Operator resolved fault in listen-only mode")
    assert supervisor.get_state() == SafetyState.PASSIVE
    assert supervisor.epoch == 6
    assert supervisor.is_tx_permitted is False
    assert supervisor.fault_reason == ""


def test_safety_state_machine_transition_to_same_state_is_noop() -> None:
    """Transitioning to the current state should be a no-op and not increment epoch."""
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE)
    assert supervisor.epoch == 0

    supervisor.transition_to(SafetyState.PASSIVE, "No change")
    assert supervisor.current_state == SafetyState.PASSIVE
    assert supervisor.epoch == 0
    assert len(supervisor.get_history()) == 0


def test_safety_state_machine_illegal_transition_triggers_emergency_fault() -> None:
    """Illegal transition must transition to FAULT, increment epoch, and raise SafetyError."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)

    with pytest.raises(SafetyError, match="Illegal Safety State transition") as exc_info:
        supervisor.transition_to(SafetyState.ACTIVE, "Direct jump attempt")

    assert exc_info.value.code == "ILLEGAL_SAFETY_TRANSITION"
    assert supervisor.current_state == SafetyState.FAULT
    assert supervisor.epoch == 1
    assert supervisor.is_tx_permitted is False
    assert "ILLEGAL_STATE_TRANSITION" in supervisor.fault_reason


def test_safety_state_machine_cannot_jump_from_fault_to_active() -> None:
    """Direct transition from FAULT to ACTIVE/ARMED_TX is prohibited."""
    supervisor = SafetySupervisor(initial_state=SafetyState.FAULT)
    assert supervisor.current_state == SafetyState.FAULT
    assert supervisor.epoch == 0

    with pytest.raises(SafetyError, match="Illegal Safety State transition"):
        supervisor.transition_to(SafetyState.ACTIVE)

    assert supervisor.current_state == SafetyState.FAULT
    # Forced fault on illegal transition increments epoch
    assert supervisor.epoch == 1


def test_safety_state_machine_direct_force_fault() -> None:
    """_force_fault() forces FAULT state, increments epoch, and notifies callbacks."""
    events: list[tuple[SafetyState, SafetyState, str]] = []

    def _cb(old: SafetyState, new: SafetyState, reason: str) -> None:
        events.append((old, new, reason))

    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    supervisor.register_callback(_cb)

    supervisor._force_fault("Emergency hardware line tripped")
    assert supervisor.current_state == SafetyState.FAULT
    assert supervisor.epoch == 1
    assert supervisor.fault_reason == "Emergency hardware line tripped"
    assert len(events) == 1
    assert events[0] == (SafetyState.STARTUP, SafetyState.FAULT, "Emergency hardware line tripped")

    # Second _force_fault with identical reason is a no-op
    supervisor._force_fault("Emergency hardware line tripped")
    assert supervisor.epoch == 1
    assert len(events) == 1


def test_safety_state_machine_reentrant_callback_deadlock_free() -> None:
    """CAN-12: Reentrant callback calling get_state() and query properties must not deadlock."""
    reentrant_state_observed: list[SafetyState] = []
    reentrant_epoch_observed: list[int] = []

    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)

    def reentrant_observer_cb(old: SafetyState, new: SafetyState, reason: str) -> None:
        # Reentrantly query state, epoch, and flags while inside callback
        state = supervisor.get_state()
        epoch = supervisor.get_epoch()
        _ = supervisor.is_tx_permitted
        _ = supervisor.fault_reason
        _ = supervisor.get_state_duration_ns()
        _ = supervisor.get_history()
        reentrant_state_observed.append(state)
        reentrant_epoch_observed.append(epoch)

    supervisor.register_callback(reentrant_observer_cb)

    # Transition sequentially
    supervisor.transition_to(SafetyState.SAFE, "init")
    supervisor.transition_to(SafetyState.PASSIVE, "listen-only")

    assert reentrant_state_observed == [SafetyState.SAFE, SafetyState.PASSIVE]
    assert reentrant_epoch_observed == [1, 2]


def test_safety_state_machine_reentrant_state_transition_in_callback() -> None:
    """CAN-12: A callback triggering another valid state transition must not deadlock."""
    transition_trail: list[SafetyState] = []

    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)

    def cascading_cb(old: SafetyState, new: SafetyState, reason: str) -> None:
        transition_trail.append(new)
        # When entering SAFE, automatically request PASSIVE transition
        if new == SafetyState.SAFE:
            supervisor.transition_to(SafetyState.PASSIVE, "Cascaded from SAFE")

    supervisor.register_callback(cascading_cb)

    supervisor.transition_to(SafetyState.SAFE, "Boot complete")

    assert supervisor.current_state == SafetyState.PASSIVE
    assert supervisor.epoch == 2
    assert SafetyState.SAFE in transition_trail
    assert SafetyState.PASSIVE in transition_trail


def test_safety_state_machine_callback_exception_isolation() -> None:
    """CAN-12: A failing callback must not crash state machine or prevent other callbacks from executing."""
    executed_callbacks: list[str] = []

    def buggy_callback_1(old: SafetyState, new: SafetyState, reason: str) -> None:
        executed_callbacks.append("cb1_start")
        raise RuntimeError("Unexpected failure in safety telemetry listener")

    def buggy_callback_2(old: SafetyState, new: SafetyState, reason: str) -> None:
        executed_callbacks.append("cb2_start")
        raise ZeroDivisionError("Math error in callback")

    def healthy_callback(old: SafetyState, new: SafetyState, reason: str) -> None:
        executed_callbacks.append("healthy_cb")

    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    supervisor.register_callback(buggy_callback_1)
    supervisor.register_callback(healthy_callback)
    supervisor.register_callback(buggy_callback_2)

    # Transition must succeed despite buggy callbacks
    supervisor.transition_to(SafetyState.SAFE, "init")

    assert supervisor.get_state() == SafetyState.SAFE
    assert "cb1_start" in executed_callbacks
    assert "healthy_cb" in executed_callbacks
    assert "cb2_start" in executed_callbacks

    # Test exception isolation during _force_fault as well
    supervisor._force_fault("Hardware line fault")
    assert supervisor.get_state() == SafetyState.FAULT
    assert executed_callbacks.count("healthy_cb") == 2


def test_safety_state_machine_unregister_callback() -> None:
    """Callbacks can be registered and unregistered cleanly."""
    events: list[str] = []

    def my_cb(old: SafetyState, new: SafetyState, reason: str) -> None:
        events.append(f"{old.value}->{new.value}")

    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    supervisor.register_callback(my_cb)
    supervisor.transition_to(SafetyState.SAFE, "init")
    assert len(events) == 1

    supervisor.unregister_callback(my_cb)
    supervisor.transition_to(SafetyState.PASSIVE, "passive")
    assert len(events) == 1  # No new event added


def test_safety_state_machine_epoch_monotonicity() -> None:
    """CAN-12: Epoch counter must strictly monotonically increase on every state change."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    epochs: list[int] = [supervisor.epoch]

    supervisor.transition_to(SafetyState.SAFE, "1")
    epochs.append(supervisor.epoch)

    supervisor.transition_to(SafetyState.PASSIVE, "2")
    epochs.append(supervisor.epoch)

    supervisor.arm_tx("3")
    epochs.append(supervisor.epoch)

    supervisor.activate_tx("4")
    epochs.append(supervisor.epoch)

    supervisor.trigger_fault("5")
    epochs.append(supervisor.epoch)

    supervisor.enter_passive_mode("6")
    epochs.append(supervisor.epoch)

    assert epochs == [0, 1, 2, 3, 4, 5, 6]
    # Invariant: strictly monotonic
    for i in range(len(epochs) - 1):
        assert epochs[i + 1] > epochs[i]


def test_safety_state_machine_monotonic_time_duration_accuracy() -> None:
    """CAN-25: Duration calculations must use monotonic time accurately."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)

    t0_mono = supervisor.state_change_timestamp_ns
    assert t0_mono > 0

    # Sleep a short controlled interval
    time.sleep(0.05)  # 50ms

    duration_ns = supervisor.state_duration_ns
    duration_sec = supervisor.state_duration_sec
    assert duration_ns >= 20_000_000  # At least 20ms
    assert duration_sec >= 0.020

    # Transition resets the duration timer
    supervisor.transition_to(SafetyState.SAFE, "Reset duration")
    t1_mono = supervisor.state_change_timestamp_ns
    assert t1_mono >= t0_mono

    duration_after_reset_ns = supervisor.get_state_duration_ns()
    assert duration_after_reset_ns < duration_ns


def test_safety_state_machine_history_and_utc_audit_log() -> None:
    """CAN-25: History records must store monotonic duration and UTC timestamp."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)

    before_utc = datetime.now(timezone.utc)
    time.sleep(0.05)
    supervisor.transition_to(SafetyState.SAFE, "Step 1")
    time.sleep(0.05)
    supervisor.transition_to(SafetyState.PASSIVE, "Step 2")
    after_utc = datetime.now(timezone.utc)

    history = supervisor.get_history()
    assert len(history) == 2

    rec1 = history[0]
    assert isinstance(rec1, StateTransitionRecord)
    assert rec1.from_state == SafetyState.STARTUP
    assert rec1.to_state == SafetyState.SAFE
    assert rec1.reason == "Step 1"
    assert rec1.epoch == 1
    assert rec1.duration_ns >= 20_000_000  # At least 20ms
    assert before_utc <= rec1.wall_time_utc <= after_utc

    dict_repr = rec1.to_dict()
    assert dict_repr["from_state"] == "STARTUP"
    assert dict_repr["to_state"] == "SAFE"
    assert dict_repr["reason"] == "Step 1"
    assert dict_repr["epoch"] == 1
    assert isinstance(dict_repr["wall_time_utc"], str)

    rec2 = history[1]
    assert rec2.from_state == SafetyState.SAFE
    assert rec2.to_state == SafetyState.PASSIVE
    assert rec2.reason == "Step 2"
    assert rec2.epoch == 2


def test_safety_state_machine_multithreaded_stress() -> None:
    """Multi-threaded stress test ensuring no deadlocks and consistent epoch progression."""
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE)
    num_threads = 8
    iterations_per_thread = 50

    def worker(worker_id: int) -> None:
        for i in range(iterations_per_thread):
            try:
                # Cycle between allowed states
                supervisor.arm_tx(f"Worker {worker_id} iter {i}")
                _ = supervisor.get_state()
                _ = supervisor.get_epoch()
                supervisor.activate_tx(f"Worker {worker_id} iter {i}")
                _ = supervisor.get_state_duration_ns()
                supervisor.enter_passive_mode(f"Worker {worker_id} iter {i}")
            except SafetyError:
                # Concurrent state changes might cause transition collision, recover safely
                supervisor._force_fault(f"Worker {worker_id} collision")
                supervisor.enter_passive_mode("Recovery")

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, idx) for idx in range(num_threads)]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()

    # Final state must be queryable and valid
    final_state = supervisor.get_state()
    assert final_state in {SafetyState.PASSIVE, SafetyState.ARMED_TX, SafetyState.ACTIVE, SafetyState.FAULT}
    assert supervisor.epoch > 0
    assert len(supervisor.get_history()) == supervisor.epoch

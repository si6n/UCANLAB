"""Link-fault → E-Stop wiring regression suite (Kontrol §3/§5 #21/#23/#44).

Previously `BUS_OFF_DETECTED`, `HARDWARE_DISCONNECT` and
`COMMUNICATION_TIMEOUT` existed as `EStopTriggerSource` members but had ZERO
production trigger sites: a hot-unplugged adapter, a bus-off controller or a
dead link while TX-armed never engaged the E-Stop. These tests lock the
`TxSafetyGateway.notify_*` choke-point behavior:

* armed (ARMED_TX/ACTIVE) + link fault → E-Stop engaged + supervisor FAULT +
  TX fence bump + further TX blocked;
* edge-triggered: one engagement per E-Stop epoch (no fence churn / log spam
  at 50 ms tick rate);
* disarmed (PASSIVE) → suppressed (no TX hazard; the arm path fail-closes
  independently on a dead driver / stale speed);
* operator reset (epoch bump) re-arms edge detection;
* standalone gateway (supervisor None) → fail-closed engagement.
"""

from __future__ import annotations

import pytest

from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.hal.virtual import VirtualBus
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor


def _make_armed_stack() -> tuple[VirtualBus, EmergencyStopSystem, SafetySupervisor, TxSafetyGateway]:
    bus = VirtualBus(channel_id="linkfault_vbus")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE)
    gateway = TxSafetyGateway(
        bus=bus, estop=estop, supervisor=supervisor, whitelist_ids={0x7E0}
    )
    supervisor.arm_tx(reason="test arm")
    assert supervisor.is_tx_permitted
    return bus, estop, supervisor, gateway


def _make_frame() -> CanFrame:
    return CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")


def _reset_estop(estop: EmergencyStopSystem) -> None:
    nonce = estop.get_reset_nonce()
    estop.reset(estop.compute_reset_token(nonce))
    assert not estop.is_engaged


def test_bus_off_engages_estop_and_faults_supervisor_when_armed() -> None:
    _, estop, supervisor, gateway = _make_armed_stack()
    fence_before = estop.tx_fence

    assert gateway.notify_bus_off("test bus-off") is True

    assert estop.is_engaged
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.BUS_OFF_DETECTED
    assert supervisor.is_fault
    assert estop.tx_fence > fence_before
    # Stage-2 order: supervisor FAULT trips first, E-Stop stage second —
    # either refusal proves TX is blocked fail-closed.
    with pytest.raises(SafetyError, match="TX not permitted|Emergency Stop is currently ENGAGED"):
        gateway.validate_and_transmit(_make_frame())


def test_hardware_disconnect_engages_estop_when_armed() -> None:
    _, estop, supervisor, gateway = _make_armed_stack()

    assert gateway.notify_hardware_disconnect("test unplug") is True

    assert estop.is_engaged
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.HARDWARE_DISCONNECT
    assert supervisor.is_fault


def test_communication_timeout_engages_estop_when_armed() -> None:
    _, estop, supervisor, gateway = _make_armed_stack()

    assert gateway.notify_communication_timeout("test silence") is True

    assert estop.is_engaged
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.COMMUNICATION_TIMEOUT
    assert supervisor.is_fault


def test_link_fault_edge_triggered_single_fence_bump() -> None:
    _, estop, _, gateway = _make_armed_stack()

    assert gateway.notify_bus_off("first") is True
    fence_after_first = estop.tx_fence

    # Persistent fault at 50 ms tick rate: latched, no re-engagement.
    assert gateway.notify_bus_off("second") is False
    assert gateway.notify_bus_off("third") is False
    assert estop.tx_fence == fence_after_first


def test_link_fault_suppressed_when_disarmed() -> None:
    bus = VirtualBus(channel_id="linkfault_disarmed")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE)
    gateway = TxSafetyGateway(
        bus=bus, estop=estop, supervisor=supervisor, whitelist_ids={0x7E0}
    )
    assert not supervisor.is_tx_permitted

    assert gateway.notify_bus_off("disarmed bus-off") is False
    assert gateway.notify_hardware_disconnect("disarmed unplug") is False
    assert gateway.notify_communication_timeout("disarmed silence") is False

    assert not estop.is_engaged
    assert not supervisor.is_fault
    # Sniffer-mode TX path itself stays governed by the normal stages.
    bus.disconnect()


def test_operator_reset_rearms_edge_detection() -> None:
    _, estop, supervisor, gateway = _make_armed_stack()

    assert gateway.notify_bus_off("still broken") is True
    _reset_estop(estop)
    supervisor.transition_to(SafetyState.PASSIVE, reason="test reset recovery")
    supervisor.arm_tx(reason="test re-arm")

    # Epoch bumped by the reset → the STILL-broken link re-latches.
    assert gateway.notify_bus_off("still broken after reset") is True
    assert estop.is_engaged
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.BUS_OFF_DETECTED


def test_recovery_clears_latch_without_reset() -> None:
    _, estop, _, gateway = _make_armed_stack()

    assert gateway.notify_hardware_disconnect("glitch") is True
    # NOTE: estop is engaged here; clear + re-notify stays suppressed until
    # the operator reset bumps the epoch (already-safe, no fence churn).
    gateway.clear_link_fault(TxSafetyGateway.LINK_FAULT_DISCONNECT)
    assert gateway.notify_hardware_disconnect("glitch again") is False
    assert estop.is_engaged


def test_standalone_gateway_without_supervisor_triggers_fail_closed() -> None:
    bus = VirtualBus(channel_id="linkfault_standalone")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    assert gateway.notify_bus_off("no supervisor to consult") is True
    assert estop.is_engaged
    bus.disconnect()


def test_distinct_kinds_latch_independently() -> None:
    _, estop, _, gateway = _make_armed_stack()

    assert gateway.notify_bus_off("bus off") is True
    # Different kind, same epoch, estop already engaged → latched + suppressed.
    assert gateway.notify_hardware_disconnect("unplug too") is False
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.BUS_OFF_DETECTED

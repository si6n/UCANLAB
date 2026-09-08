"""End-to-End Safety Wiring Harness: SafeMultiplexedBus, FrameRouter, TxWatchdogSupervisor, TxSafetyGateway.

Verifies:
1. Composition root wiring in UniversalCanDesktopApp (Mock WebView2 + mock bus).
2. Direct integration wiring harness:
   - SafeMultiplexedBus routes TX through TxSafetyGateway and RX through FrameRouter.
   - FrameRouter distributes incoming CAN frames without frame-stealing or starvation.
   - Centralized 6-stage TxSafetyGateway enforces safety state, watchdog lease, whitelist, and E-Stop.
3. Complete E-Stop -> Watchdog expire -> Gateway block -> TX cutoff automated safety cascade:
   - Initial normal operation (ARMED_TX / ACTIVE, valid watchdog lease, unengaged E-Stop, whitelisted ID)
     allows successful frame transmission.
   - Triggering E-Stop (via UI button / API bridge) transitions state machine to FAULT, engages E-Stop,
     and causes TxSafetyGateway / SafeMultiplexedBus to immediately block all TX.
   - Watchdog lease expiration (due to UI heartbeat loss) triggers watchdog supervisor,
     firing FAULT transition, engaging E-Stop (KEEPALIVE_TIMEOUT), and cutting off all TX.
   - SafeMultiplexedBus rejects send/send_sync/send_async calls under FAULT / expired watchdog / engaged E-Stop.
   - Isolated RX verification: While TX is strictly cut off, FrameRouter RX and SafeMultiplexedBus.recv()
     remain fully functional (Listen-Only / Passive fail-safe behavior).
"""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.core.contracts.ports import VirtualClock
from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.engine.router import FrameRouter
from src.hal.base import AbstractBus, BusState
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.gateway import TxSafetyGateway
from src.safety.multiplexer import SafeMultiplexedBus
from src.safety.state_machine import SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor
from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp

# ==============================================================================
# Mock Bus and Mock WebView2 Window for Composition Root Testing
# ==============================================================================


class MockCanBus(AbstractBus):
    """Controllable in-memory CAN Bus for composition root and wiring tests."""

    def __init__(self, channel_id: str = "mock_can0", bitrate: int = 250000) -> None:
        super().__init__(channel_id=channel_id, bitrate=bitrate)
        self.sent_frames: list[CanFrame] = []
        self._rx_queue: queue.Queue[CanFrame] = queue.Queue()
        self.connect_count = 0
        self.disconnect_count = 0

    def connect(self) -> None:
        self.is_connected = True
        self.connect_count += 1
        self.metrics.state = BusState.ACTIVE

    def disconnect(self) -> None:
        self.is_connected = False
        self.disconnect_count += 1
        self.metrics.state = BusState.DISCONNECTED

    def send(self, frame: CanFrame) -> None:
        if not self.is_connected:
            raise SafetyError("Bus not connected", code="BUS_DISCONNECTED")
        self.sent_frames.append(frame)
        self.metrics.tx_frames += 1

    def recv(self, timeout_s: float | None = 0.05) -> CanFrame | None:
        if not self.is_connected:
            return None
        try:
            frame = self._rx_queue.get(timeout=timeout_s if timeout_s is not None else 0.05)
            self.metrics.rx_frames += 1
            return frame
        except queue.Empty:
            return None

    def inject_rx(self, frame: CanFrame) -> None:
        self._rx_queue.put(frame)


class MockWebView2Window:
    """Mock pywebview Window implementing evaluate_js and capturing frontend calls."""

    def __init__(self) -> None:
        self.evaluated_js: list[str] = []
        self.events: list[tuple[str, Any]] = []

    def evaluate_js(self, script: str) -> None:
        self.evaluated_js.append(script)


# ==============================================================================
# Test Fixtures & Harness
# ==============================================================================


class SafetyWiringHarness:
    """Integrated test harness bringing together SafeMultiplexedBus, FrameRouter,

    TxWatchdogSupervisor, and TxSafetyGateway in a unified composition.
    """

    def __init__(
        self,
        watchdog_timeout_ms: float = 100.0,
        whitelist_ids: set[int] | None = None,
    ) -> None:
        self.bus = MockCanBus(channel_id="vcan_test", bitrate=500000)
        self.bus.connect()

        self.router = FrameRouter()
        self.estop = EmergencyStopSystem(allow_self_reset=True)
        self.supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
        # Transition to PASSIVE then ARMED_TX for nominal TX testing
        self.supervisor.transition_to(SafetyState.PASSIVE, reason="Initialization")
        self.supervisor.arm_tx()

        # docs/ai_context/05 Â§4: the lease is driven by an injected
        # VirtualClock â€” timeout tests advance the clock instead of sleeping.
        self.clock = VirtualClock(start_monotonic_sec=1000.0)
        self.watchdog = TxWatchdogSupervisor(
            supervisor=self.supervisor,
            estop=self.estop,
            timeout_ms=watchdog_timeout_ms,
            clock=self.clock,
        )

        effective_whitelist = whitelist_ids if whitelist_ids is not None else {0x7DF, 0x7E0, 0x18DA00F9}
        self.gateway = TxSafetyGateway(
            bus=self.bus,
            estop=self.estop,
            supervisor=self.supervisor,
            watchdog=self.watchdog,
            whitelist_ids=effective_whitelist,
        )

        # Wire SafeMultiplexedBus over hardware, gateway, and router
        self.safe_bus = SafeMultiplexedBus(
            physical_bus=self.bus,
            gateway=self.gateway,
            router=self.router,
        )

    def start_watchdog(self) -> None:
        self.watchdog.start()

    def stop_watchdog(self) -> None:
        self.watchdog.stop()


# ==============================================================================
# 1. Composition Root Verification (Desktop App + Mock WebView2 + Mock Bus)
# ==============================================================================


def test_composition_root_wiring_with_mock_bus_and_webview2() -> None:
    """Verify UniversalCanDesktopApp acts as the single composition root correctly wiring

    SafeMultiplexedBus, FrameRouter, TxWatchdogSupervisor, and TxSafetyGateway.
    """
    mock_bus = MockCanBus(channel_id="vcan_mock", bitrate=250000)

    # Instantiate desktop app with injected mock bus
    app = UniversalCanDesktopApp(channel="vcan_mock", bitrate=250000, bus=mock_bus)
    mock_window = MockWebView2Window()
    app._window = mock_window  # type: ignore[assignment]
    bridge = DesktopApiBridge(app)

    # 1. Check composition root ownership
    assert app.bus is mock_bus
    assert isinstance(app.router, FrameRouter)
    assert isinstance(app.watchdog, TxWatchdogSupervisor)
    assert isinstance(app.gateway, TxSafetyGateway)
    assert isinstance(app.estop, EmergencyStopSystem)
    assert isinstance(app.supervisor, SafetySupervisor)

    # 2. Check Gateway interconnects
    assert app.gateway.bus is mock_bus
    assert app.gateway.estop is app.estop
    assert app.gateway.supervisor is app.supervisor
    assert app.gateway.watchdog is app.watchdog

    # 3. Check SafeMultiplexedBus creation from desktop app helper
    uds_client = app.create_uds_client(tx_id=0x7E0, rx_id=0x7E8)
    assert isinstance(uds_client.bus, SafeMultiplexedBus)
    # B1 (REVIEW): the multiplexer resolves the physical bus dynamically via
    # its provider — it must track the app's live bus instance and survive a
    # `_reconnect_bus` swap without going stale.
    assert uds_client.bus._live_bus is mock_bus
    assert uds_client.bus.gateway is app.gateway
    assert uds_client.bus.router is app.router

    # 4. Verify initial Safety State is PASSIVE
    assert app.supervisor.current_state == SafetyState.PASSIVE
    assert bridge.get_safety_state() == "PASSIVE"

    # 5. Verify UI heartbeat keeps watchdog alive via bridge
    assert bridge.heartbeat() is True
    assert app.watchdog.is_lease_valid is True

    # 6. Test App lifecycle execution with mock webview
    with (
        patch.object(app, "_resolve_dist_html", return_value=Path(__file__)),
        patch("webview.create_window", return_value=mock_window),
        patch("webview.start", side_effect=lambda **kwargs: None),
    ):
        app.run()

    # L-13 (P3-9): the exit path now releases the physical bus FIRST —
    # after run() returns, the driver handle must be CLOSED (the old exit
    # path left the transceiver/DLL pinned), the loop stopped, and the
    # watchdog stopped.
    assert mock_bus.is_connected is False
    assert app._running is False
    assert app.watchdog._is_running is False


# ==============================================================================
# 2. End-to-End Safety Cascade: E-Stop -> Watchdog Expire -> Gateway Block -> TX Cutoff
# ==============================================================================


def test_safety_wiring_nominal_transmission_allowed() -> None:
    """Verify that under nominal conditions (ARMED_TX, active heartbeat, valid whitelist),

    SafeMultiplexedBus transmits successfully to the physical bus.
    """
    harness = SafetyWiringHarness(whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="vcan_test", arbitration_id=0x7E0, data=b"\x02\x10\x01\x00\x00\x00\x00\x00")

    # Send synchronously via SafeMultiplexedBus
    harness.safe_bus.send(frame)

    # Frame should have reached physical bus
    assert len(harness.bus.sent_frames) == 1
    assert harness.bus.sent_frames[0].arbitration_id == 0x7E0
    assert bytes(harness.bus.sent_frames[0].data) == bytes(frame.data)


def test_safety_wiring_estop_trigger_blocks_gateway_and_cuts_off_tx() -> None:
    """Verify that triggering E-Stop immediately transitions supervisor to FAULT,

    blocks TxSafetyGateway, and cuts off SafeMultiplexedBus transmissions.
    """
    harness = SafetyWiringHarness(whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="vcan_test", arbitration_id=0x7E0, data=b"\x02\x10\x01")

    # Baseline: 1 frame passes
    harness.safe_bus.send_sync(frame)
    assert len(harness.bus.sent_frames) == 1

    # Trigger E-Stop
    harness.estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "Operator Pressed E-STOP")

    # Verify E-Stop state
    assert harness.estop.is_engaged is True
    # Gateway registered callback transitions supervisor to FAULT
    assert harness.supervisor.is_fault is True
    assert harness.supervisor.is_tx_permitted is False

    # Attempt to transmit through SafeMultiplexedBus -> MUST BE BLOCKED
    with pytest.raises(SafetyError) as exc_info:
        harness.safe_bus.send(frame)
    assert exc_info.value.code in {"SAFETY_STATE_BLOCKED", "ESTOP_ACTIVE"}

    with pytest.raises(SafetyError) as exc_info_sync:
        harness.safe_bus.send_sync(frame)
    assert exc_info_sync.value.code in {"SAFETY_STATE_BLOCKED", "ESTOP_ACTIVE"}

    # No new frames leaked to physical bus
    assert len(harness.bus.sent_frames) == 1


@pytest.mark.asyncio
async def test_safety_wiring_async_send_blocked_by_estop() -> None:
    """Verify asynchronous send_async on SafeMultiplexedBus is cut off when E-Stop is triggered."""
    harness = SafetyWiringHarness(whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="vcan_test", arbitration_id=0x7E0, data=b"\x02\x10\x01")

    # Baseline: async send succeeds
    await harness.safe_bus.send_async(frame)
    assert len(harness.bus.sent_frames) == 1

    # Trigger E-Stop
    harness.estop.trigger(EStopTriggerSource.HARDWARE_DISCONNECT, "Hardware disconnected")

    with pytest.raises(SafetyError):
        await harness.safe_bus.send_async(frame)

    assert len(harness.bus.sent_frames) == 1


def test_safety_wiring_watchdog_expiration_cascade() -> None:
    """Verify complete cascade:

    Watchdog lease expiration -> Supervisor FAULT -> E-Stop triggered -> Gateway blocks -> TX cutoff.

    Deterministic: the lease runs on an injected VirtualClock â€” no real sleeps.
    """
    # Fast 80ms watchdog timeout for test responsiveness
    harness = SafetyWiringHarness(watchdog_timeout_ms=80.0, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="vcan_test", arbitration_id=0x7E0, data=b"\x02\x10\x01")

    try:
        # Keep alive with heartbeats across virtual time
        for _ in range(2):
            harness.clock.advance(0.04)
            harness.watchdog.heartbeat()

        # Confirm TX still permitted
        assert harness.watchdog.is_lease_valid is True
        harness.safe_bus.send(frame)
        assert len(harness.bus.sent_frames) == 1

        # Now simulate UI freeze / heartbeat stop: advance virtual time past
        # the 80ms lease and run one monitor iteration deterministically.
        harness.clock.advance(0.18)
        harness.watchdog.poll_once()

        # 1. Lease must be expired
        assert harness.watchdog.is_lease_valid is False

        # 2. Supervisor must have entered FAULT
        assert harness.supervisor.current_state == SafetyState.FAULT
        assert harness.supervisor.is_tx_permitted is False
        assert "WATCHDOG_TIMEOUT" in harness.supervisor.fault_reason

        # 3. E-Stop must be engaged with KEEPALIVE_TIMEOUT trigger
        assert harness.estop.is_engaged is True
        assert harness.estop.last_event is not None
        assert harness.estop.last_event.trigger == EStopTriggerSource.KEEPALIVE_TIMEOUT

        # 4. SafeMultiplexedBus TX must be strictly cut off
        with pytest.raises(SafetyError) as exc_info:
            harness.safe_bus.send(frame)
        assert exc_info.value.code in {"SAFETY_STATE_BLOCKED", "WATCHDOG_LEASE_EXPIRED", "ESTOP_ACTIVE"}

        # 5. Bus sent_frames must not have grown
        assert len(harness.bus.sent_frames) == 1

    finally:
        harness.stop_watchdog()


def test_safety_wiring_rx_continues_during_tx_cutoff() -> None:
    """Verify Fail-Closed & Listen-Only principle:

    Even after E-Stop and Watchdog expiration have cut off TX,
    RX frames distributed via FrameRouter to SafeMultiplexedBus.recv() are NOT lost.
    """
    harness = SafetyWiringHarness(watchdog_timeout_ms=60.0, whitelist_ids={0x7E0})

    try:
        # Advance virtual time past the lease and run one monitor iteration â€”
        # watchdog latches FAULT + E-Stop deterministically (no real sleep).
        harness.clock.advance(0.15)
        harness.watchdog.poll_once()
        assert harness.supervisor.is_fault is True
        assert harness.estop.is_engaged is True

        # Confirm TX is blocked
        frame_tx = CanFrame.create(channel_id="vcan_test", arbitration_id=0x7E0, data=b"\x02\x10\x01")
        with pytest.raises(SafetyError):
            harness.safe_bus.send(frame_tx)

        # Ingest incoming RX frame through the router
        rx_frame = CanFrame.create(channel_id="vcan_test", arbitration_id=0x7E8, data=b"\x06\x50\x01\x00\x32\x01\xF4")
        harness.router.route_frame(rx_frame)

        # SafeMultiplexedBus must receive the frame from its dedicated router subscription queue
        received = harness.safe_bus.recv(timeout_s=0.1)
        assert received is not None
        assert received.arbitration_id == 0x7E8
        assert bytes(received.data) == bytes(rx_frame.data)

    finally:
        harness.stop_watchdog()


def test_safety_wiring_desktop_api_bridge_estop_and_recovery_flow() -> None:
    """Verify the full desktop UI bridge flow:

    1. Trigger E-Stop from UI bridge.
    2. Verify watchdog/supervisor/gateway all reflect FAULT & E-Stop state.
    3. Verify SafeMultiplexedBus blocks TX.
    4. Recover via the challenge/response reset flow (P0-1: the local
       minted-token shortcut was removed from the bridge — recovery must go
       through estop_request_challenge + estop_submit_reset_token).
    5. Transition to ARMED_TX and resume transmission.
    """
    mock_bus = MockCanBus(channel_id="vcan_bridge", bitrate=500000)
    mock_bus.connect()
    app = UniversalCanDesktopApp(channel="vcan_bridge", bitrate=500000, bus=mock_bus)
    bridge = DesktopApiBridge(app)

    # Arm transmission for test
    app.supervisor.transition_to(SafetyState.ARMED_TX, reason="Operator authorized")
    safe_bus = SafeMultiplexedBus(physical_bus=mock_bus, gateway=app.gateway, router=app.router)

    frame = CanFrame.create(channel_id="vcan_bridge", arbitration_id=0x7E0, data=b"\x02\x10\x01")

    # 1. Baseline TX succeeds
    safe_bus.send(frame)
    assert len(mock_bus.sent_frames) == 1

    # 2. Trigger E-Stop from UI Bridge
    bridge.trigger_estop()

    assert app.estop.is_engaged is True
    assert app.supervisor.is_fault is True
    assert bridge.get_safety_state() == "FAULT"

    # 3. Verify Gateway and SafeMultiplexedBus block TX
    with pytest.raises(SafetyError):
        safe_bus.send(frame)
    assert len(mock_bus.sent_frames) == 1

    # P0-1 regression: the removed local shortcut must stay gone. The bridge
    # exposes no estop_reset_local, and the simulator toggle / scenario
    # switch may NOT clear a latched E-Stop.
    assert not hasattr(bridge, "estop_reset_local")
    assert not hasattr(app, "reset_estop_local")
    before_toggle = app._is_simulating
    assert bridge.toggle_simulator() == before_toggle  # refused while latched
    bridge.select_scenario("misfire_p0300")
    assert app._active_scenario == "misfire_p0300"
    assert app.estop.is_engaged is True  # scenario switch did not clear it

    # 4. Recover via the cryptographic challenge/response flow
    challenge_res = bridge.estop_request_challenge()
    assert challenge_res.get("success") is True
    # Independently mint the authorized token from the challenge (models the
    # out-of-band authorization operator, e.g. a second station signing it).
    from src.safety.estop import EStopResetAuthority

    authority = EStopResetAuthority(app.estop)
    token = authority.mint_reset_token()
    assert token is not None
    reset_res = bridge.estop_submit_reset_token(token.to_token_string())
    assert reset_res.get("success") is True
    assert app.estop.is_engaged is False
    assert app.supervisor.current_state == SafetyState.PASSIVE

    # In PASSIVE state, TX is still blocked by default (safe by default)
    with pytest.raises(SafetyError) as exc_info:
        safe_bus.send(frame)
    assert exc_info.value.code == "SAFETY_STATE_BLOCKED"

    # 5. Explicitly transition to ARMED_TX after recovery
    app.supervisor.arm_tx()
    assert app.supervisor.is_tx_permitted is True

    # 6. TX resumes successfully
    safe_bus.send(frame)
    assert len(mock_bus.sent_frames) == 2


def test_safety_wiring_non_whitelisted_id_trips_estop_and_cuts_off_tx() -> None:
    """Verify that transmitting a non-whitelisted frame through SafeMultiplexedBus

    triggers WhitelistViolation, immediately trips E-Stop, and locks down subsequent TX.
    """
    harness = SafetyWiringHarness(whitelist_ids={0x7E0})  # 0x123 is not whitelisted

    bad_frame = CanFrame.create(channel_id="vcan_test", arbitration_id=0x123, data=b"\xDE\xAD\xBE\xEF")
    good_frame = CanFrame.create(channel_id="vcan_test", arbitration_id=0x7E0, data=b"\x02\x10\x01")

    # Attempt to transmit unauthorized frame
    with pytest.raises(SafetyError):
        harness.safe_bus.send(bad_frame)

    # Verify unauthorized payload tripped E-Stop and supervisor FAULT
    assert harness.estop.is_engaged is True
    assert harness.estop.last_event is not None
    assert harness.estop.last_event.trigger == EStopTriggerSource.UNAUTHORIZED_PAYLOAD
    assert harness.supervisor.is_fault is True

    # Subsequent valid whitelisted frame MUST now also be blocked
    with pytest.raises(SafetyError):
        harness.safe_bus.send(good_frame)

    assert len(harness.bus.sent_frames) == 0

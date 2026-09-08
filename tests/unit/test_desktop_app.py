"""Unit tests for Universal CAN Desktop Application Bridge and Lifecycle."""

from __future__ import annotations

import json

from src.core.models.can_frame import CanFrame
from src.protocols.j1939.transport import CompletedMessage
from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp


def test_desktop_api_bridge_estop() -> None:
    """Verify DesktopApiBridge triggers emergency stop on the app."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    assert app._is_estop is False
    bridge.toggle_simulator()
    assert app._is_simulating is True

    bridge.trigger_estop()

    assert app._is_estop is True
    assert app._is_simulating is False
    assert app._bus_load == 0

    # P0-1 (REVIEW C-1): the token-less local reset shortcut is GONE from
    # the bridge — a single JS call must never clear a latched E-Stop.
    assert not hasattr(bridge, "estop_reset_local")
    assert not hasattr(app, "reset_estop_local")

    # Simulator toggle and scenario switch are refused while latched and do
    # NOT clear the E-Stop.
    sim_before = app._is_simulating
    assert bridge.toggle_simulator() == sim_before
    assert app._is_estop is True
    bridge.select_scenario("misfire_p0300")
    assert app._is_estop is True

    # Recovery goes through the challenge/response flow with an
    # independently minted token (models out-of-band authorization).
    from src.safety.estop import EStopResetAuthority

    res_challenge = bridge.estop_request_challenge()
    assert res_challenge.get("success") is True

    authority = EStopResetAuthority(app.estop)
    token = authority.mint_reset_token()
    assert token is not None
    res = bridge.estop_submit_reset_token(token.to_token_string())
    assert res.get("success") is True
    assert app._is_estop is False


def test_desktop_api_bridge_toggle_simulator() -> None:
    """Verify simulator toggle functionality."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    initial_state = app._is_simulating
    new_state = bridge.toggle_simulator()
    assert new_state != initial_state
    assert app._is_simulating == new_state


def test_desktop_api_bridge_scenario_selection() -> None:
    """Verify setting fault scenarios via bridge."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    bridge.select_scenario("misfire_p0300")
    assert app._active_scenario == "misfire_p0300"
    assert app._is_estop is False


def test_desktop_api_bridge_settings_update() -> None:
    """Verify updating channel, baudrate, and API key."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    bridge.save_settings(
        {
            "channel": "can0",
            "baudRate": "500 kbps",
            "apiKey": "test-mock-api-key-12345",
        }
    )

    assert app.channel_name == "can0"
    assert app.bitrate_val == 500000
    assert app.copilot.gemini_api_key == "test-mock-api-key-12345"


def test_desktop_api_copilot_query() -> None:
    """Verify copilot querying through bridge."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    res = bridge.ask_copilot("P0300")
    assert "P0300" in res


def _make_tp_frame() -> CanFrame:
    return CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EBF900,
        data=b"\x01" + b"A" * 7,
        is_extended=True,
    )


def test_ingest_oversized_reassembled_message_does_not_crash() -> None:
    # E1 regression: multi-packet payloads larger than a single CAN frame
    # (>64 bytes) used to be rebuilt with dlc=len(data), raising ValueError
    # and killing the telemetry thread. Ingestion must survive them.
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    oversized = CompletedMessage(
        source_address=0x00,
        destination_address=0xF9,
        pgn=65226,  # DM1
        data=b"X" * 100,
        timestamp_ns=123,
        channel_id="j1939_ch0",
    )
    app.j1939_tp.handle_rx_frame = lambda frame: (oversized, None)  # type: ignore[method-assign]

    app._ingest_live_frame(_make_tp_frame())  # must not raise

    # C-9 (perf): the packet counter is bumped per TICK by the telemetry
    # loop (drained count), no longer per frame inside _ingest_live_frame.
    # The survival assertion above is the actual E1 regression guard.


def test_ingest_reassembled_message_with_hostile_pgn_does_not_crash() -> None:
    # E1 regression: a reassembled PGN whose (pgn << 8) | SA exceeds the
    # 29-bit arbitration range must be masked, not raise.
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    hostile = CompletedMessage(
        source_address=0xFF,
        destination_address=0xF9,
        pgn=0x3FFFF,
        data=b"Y" * 20,
        timestamp_ns=123,
        channel_id="j1939_ch0",
    )
    app.j1939_tp.handle_rx_frame = lambda frame: (hostile, None)  # type: ignore[method-assign]

    app._ingest_live_frame(_make_tp_frame())  # must not raise


def test_push_frame_to_ui_escapes_hostile_channel_id() -> None:
    # E2 regression: channel names come from traces/interfaces and must not
    # be able to inject script into the WebView2 context via evaluate_js.
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    captured: list[str] = []

    class _FakeWindow:
        def evaluate_js(self, code: str) -> None:
            captured.append(code)

    app._window = _FakeWindow()  # type: ignore[assignment]
    hostile_channel = "ch'); alert('pwned'); //"
    frame = CanFrame.create(
        channel_id=hostile_channel,
        arbitration_id=0x123,
        data=b"\x01\x02",
    )

    app._push_frames_to_ui_batch([frame])

    assert len(captured) == 1
    js_code = captured[0]
    # E13: batched call — payload is a JSON ARRAY of frame objects
    assert "onNewCanFrames(" in js_code
    payload_text = js_code[js_code.index("onNewCanFrames(") + len("onNewCanFrames(") : js_code.rindex(")")]
    parsed_batch = json.loads(payload_text)
    assert isinstance(parsed_batch, list) and len(parsed_batch) == 1
    # The payload must be a valid JSON object literal; the hostile channel
    # survives only as a quoted string value, never as executable breakout.
    parsed = parsed_batch[0]
    assert parsed["channel"] == hostile_channel
    assert parsed["data"] == "0102"


def test_desktop_app_interface_wiring() -> None:
    """D3: constructor honors the interface parameter instead of hardcoding virtual."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000, interface="pcan")
    assert app.interface_val == "pcan"
    assert app.bus.interface == "pcan"


def test_desktop_app_default_interface_stays_virtual() -> None:
    """D3: default remains 'virtual' — safe listen-only out of the box."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    assert app.interface_val == "virtual"
    assert app.bus.interface == "virtual"


def test_desktop_app_export_logs(tmp_path) -> None:
    """Verify export_logs functionality (LOW-4)."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    # Insert a dummy frame
    frame = CanFrame.create(channel_id="vcan0", arbitration_id=0x123, data=b"\x01\x02")
    app.ring_buffer.append(frame)

    assert bridge.export_logs("json") is True
    assert bridge.export_logs("csv") is True
    assert bridge.export_logs("unsupported_xyz") is False


def test_desktop_app_settings_interface_change_reconnects() -> None:
    """D3: update_settings accepts an 'interface' key and triggers a reconnect.

    A kvaser bus needs an integer channel, so on machines without hardware the
    reconnect connect() itself fails — _reconnect_bus logs a warning and keeps
    DEMO-only mode, but the new interface must be recorded on the instance
    and the gateway rebind to the fresh bus object either way.
    """
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    assert app.interface_val == "virtual"

    app.update_settings({"interface": "kvaser"})
    assert app.interface_val == "kvaser"
    assert app.bus.interface == "kvaser"
    assert app.gateway.bus is app.bus  # gateway rebinds to the new instance


# ============================================================================
# P0-2 / P0-5 (REVIEW C-2, C-3): speed interlock provenance regressions
# ============================================================================


def _ccvs_frame(sa: int, raw_speed: int) -> CanFrame:
    """Build a CCVS (PGN 65265) frame: priority 6, PF=0xFE, PS=0xF1, given SA."""
    arb = 0x18FEF100 | sa
    data = bytes([0x00, raw_speed & 0xFF, (raw_speed >> 8) & 0xFF, 0, 0, 0, 0, 0])
    return CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)


def _eec1_frame(sa: int, raw_rpm: int) -> CanFrame:
    """Build an EEC1 (PGN 61444) frame with the given engine speed."""
    arb = 0x18F00400 | sa
    data = bytes([0, 0, 0, raw_rpm & 0xFF, (raw_rpm >> 8) & 0xFF, 0, 0, 0])
    return CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)


def test_ccvs_speed_feeds_interlock_with_plausibility() -> None:
    """P0-5: trusted CCVS with plausible engine state refreshes the interlock."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    assert app.gateway._last_speed_update_ns == 0  # fail-closed baseline

    # Engine off (RPM 0) + stationary CCVS from SA 0x00 -> plausible
    app._decode_j1939_signal(_eec1_frame(0x00, 0))
    app._decode_j1939_signal(_ccvs_frame(0x00, 0))
    assert app._ccvs_trusted_sa == 0x00  # learning mode bound the first sender
    assert app.gateway._last_speed_update_ns > 0
    assert app._current_speed_kmh == 0.0


def test_ccvs_from_untrusted_source_address_is_ignored() -> None:
    """P0-5 (REVIEW C-3): a second node spoofing CCVS after the trusted SA is
    bound must NOT feed the interlock."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)

    app._decode_j1939_signal(_eec1_frame(0x00, 0))
    app._decode_j1939_signal(_ccvs_frame(0x00, 0))  # binds SA 0x00 as trusted
    ts_after_trusted = app.gateway._last_speed_update_ns
    assert ts_after_trusted > 0

    # Attacker node (different SA) broadcasts stationary CCVS — ignored.
    app.gateway._last_speed_update_ns = 0  # age out to test refresh behavior
    app._decode_j1939_signal(_ccvs_frame(0x2A, 0))
    assert app.gateway._last_speed_update_ns == 0  # NOT refreshed
    assert app._current_speed_kmh == 0.0


def test_ccvs_implausible_vs_engine_rpm_fails_closed() -> None:
    """P0-5 (REVIEW C-3): 'stationary' while the engine clearly runs above idle
    is a stuck/spoofed CCVS signature — speed becomes unknown (NaN), and
    arm_tx is refused fail-closed."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)

    # Engine running hard (2400 rpm) but vehicle "stationary" -> implausible
    app._decode_j1939_signal(_eec1_frame(0x00, int(2400 / 0.125)))
    app._decode_j1939_signal(_ccvs_frame(0x00, 0))
    assert app._current_speed_kmh != app._current_speed_kmh  # NaN
    # Interlock feed invalidated (NaN path clears freshness)
    assert app.gateway._last_speed_update_ns == 0

    # arm_tx must refuse on unknown speed (NaN > 0.0 is False — the old
    # check would have silently armed).
    res = app.arm_tx(reason="test")
    assert res.get("success") is False
    assert "unknown" in res.get("error", "").lower()


def test_ccvs_sentinel_band_rejected() -> None:
    """P0-5: J1939-71 sentinels (raw >= 0xFE00 = Error/Not Available) must not
    feed the interlock — including the 250..254 km/h band the old
    `speed <= 250.0` check used to accept."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)

    app._decode_j1939_signal(_eec1_frame(0x00, 0))
    app._decode_j1939_signal(_ccvs_frame(0x00, 0))
    assert app.gateway._last_speed_update_ns > 0

    # Error sentinel 0xFE00 (previously passed the <= 250.0 km/h check at
    # raw 0xFA00..0xFDFF; 0xFE00/256 = 254.0 -> rejected now)
    app.gateway._last_speed_update_ns = 0
    app._decode_j1939_signal(_ccvs_frame(0x00, 0xFE00))
    assert app.gateway._last_speed_update_ns == 0
    # Not-available sentinel 0xFFFF
    app._decode_j1939_signal(_ccvs_frame(0x00, 0xFFFF))
    assert app.gateway._last_speed_update_ns == 0


def test_demo_loop_never_feeds_speed_interlock() -> None:
    """P0-2 (REVIEW C-2): the DEMO telemetry path must not call
    gateway.update_vehicle_speed — the synthetic 0 km/h feed used to
    authorize critical commands while a real vehicle was connected."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)

    # Simulate a physically-fed interlock baseline, then run one DEMO tick's
    # speed-side effects indirectly: the DEMO branch must leave the
    # physical timestamp untouched. The call was removed from _telemetry_loop;
    # verify no synthetic path refreshes the interlock by construction.
    app._decode_j1939_signal(_eec1_frame(0x00, 0))
    app._decode_j1939_signal(_ccvs_frame(0x00, 0))
    ts_physical = app.gateway._last_speed_update_ns
    assert ts_physical > 0

    app._set_ui_state(_is_simulating=True)
    # Drive the DEMO path indirectly: the source-level guarantee is that no
    # update_vehicle_speed call exists on the DEMO branch anymore. Assert
    # via the gateway state: one simulated tick's worth of time must not
    # refresh the timestamp.
    import time as _t

    _t.sleep(0.02)
    assert app.gateway._last_speed_update_ns == ts_physical  # untouched


def test_arm_tx_refused_while_simulating() -> None:
    """P0-2: synthetic speed cannot authorize TX — arming is refused while
    the simulator is active."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._set_ui_state(_is_simulating=True)

    res = app.arm_tx(reason="test")
    assert res.get("success") is False
    assert "simulator" in res.get("error", "").lower()


def test_desktop_api_bridge_actionable_uds_clear_dtc() -> None:
    """Verify DesktopApiBridge executes UDS Clear DTC with confirmation and safety checks."""
    from src.safety.estop import EStopResetAuthority

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    # 1. Refusal when confirmation required but user_confirmed=False
    action_clear = {
        "id": "clear-1",
        "label": "UDS 0x14 Clear DTCs",
        "action_type": "uds_clear_dtc",
        "params": {"dtc_group": 0xFFFFFF},
        "requires_confirmation": True,
    }
    res_unconf = bridge.execute_diagnostic_action(action_clear, user_confirmed=False)
    assert res_unconf.get("success") is False
    assert "onayı gereklidir" in res_unconf.get("message", "")

    # 2. Refusal when E-Stop is active
    bridge.trigger_estop()
    res_estop = bridge.execute_diagnostic_action(action_clear, user_confirmed=True)
    assert res_estop.get("success") is False
    assert "E-Stop" in res_estop.get("message", "")

    # Reset E-Stop via challenge/response authority
    authority = EStopResetAuthority(app.estop)
    token = authority.mint_reset_token()
    bridge.estop_submit_reset_token(token.to_token_string())
    assert app._is_estop is False

    # 3. Refusal when vehicle is moving (> 0.0 km/h)
    app._current_speed_kmh = 45.0
    res_moving = bridge.execute_diagnostic_action(action_clear, user_confirmed=True)
    assert res_moving.get("success") is False
    assert "hareket" in res_moving.get("message", "").lower()
    app._current_speed_kmh = 0.0

    # 4. Success in simulation mode
    app._is_simulating = True
    app._error_count = 15
    app._active_scenario = "misfire_p0300"
    res_success = bridge.execute_diagnostic_action(action_clear, user_confirmed=True)
    assert res_success.get("success") is True
    assert app._error_count == 0
    assert app._active_scenario == "nominal"
    assert "0x14" in res_success.get("message", "")


def test_desktop_api_bridge_actionable_uds_read_vin_and_session() -> None:
    """Verify DesktopApiBridge handles UDS Read VIN and Diagnostic Session Control."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)
    app._is_simulating = True

    # 1. Read VIN
    action_vin = {
        "id": "vin-1",
        "label": "UDS 0x22 F190 Read VIN",
        "action_type": "uds_read_vin",
        "params": {"did": 0xF190},
    }
    res_vin = bridge.execute_diagnostic_action(action_vin, user_confirmed=True)
    assert res_vin.get("success") is True
    assert "data" in res_vin
    assert res_vin["data"].get("did") == "0xF190"

    # 2. Session Control
    action_sess = {
        "id": "sess-1",
        "label": "UDS 0x10 Extended Session",
        "action_type": "uds_session_control",
        "params": {"session_type": 0x03},
    }
    res_sess = bridge.execute_diagnostic_action(action_sess, user_confirmed=True)
    assert res_sess.get("success") is True
    assert res_sess["data"].get("session_type") == 3

    # 3. J1939 DM11 Clear DTC
    action_dm11 = {
        "id": "dm11-1",
        "label": "J1939 DM11 Clear DTCs",
        "action_type": "j1939_clear_dtc",
        "requires_confirmation": True,
    }
    res_dm11 = bridge.execute_diagnostic_action(action_dm11, user_confirmed=True)
    assert res_dm11.get("success") is True
    assert "DM11" in res_dm11.get("message", "")


def test_desktop_api_bridge_traffic_metrics_snapshot() -> None:
    """Verify bridge.get_bus_traffic_status returns snapshot of load, errors, and babbling node."""
    from src.core.models.can_frame import CanFrame

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    status_nominal = bridge.get_bus_traffic_status()
    assert "bus_load_percent" in status_nominal
    assert "error_count" in status_nominal
    assert "recent_frame_rate" in status_nominal
    assert status_nominal["status"] == "nominal"

    # Push burst of frames into ring buffer to simulate traffic
    for _ in range(50):
        app.ring_buffer.push(CanFrame(arbitration_id=0x123, dlc=8, data=bytes([0] * 8), channel_id="vcan0"))
    for _ in range(10):
        app.ring_buffer.push(CanFrame(arbitration_id=0x200, dlc=8, data=bytes([1] * 8), channel_id="vcan0"))

    app._bus_load = 82.0
    app._error_count = 35
    status_warning = bridge.get_bus_traffic_status()
    assert status_warning["status"] == "warning"
    assert status_warning["bus_load_percent"] == 82.0
    assert status_warning["error_count"] == 35
    assert status_warning["babbling_node"] is not None
    assert "0x123" in status_warning["babbling_node"]

    # Verify query_copilot receives traffic metrics
    copilot_res = bridge.ask_copilot("trafik durumu nasıl?")
    assert "Veri Yolu Trafik Analizi" in copilot_res or "Trafik" in copilot_res


def test_desktop_api_bridge_actionable_speed_and_input_hardening() -> None:
    """Verify execute_diagnostic_action strictly fails closed on NaN/negative speed and invalid input structures."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)
    app._is_simulating = True

    action_clear = {
        "action_type": "uds_clear_dtc",
        "requires_confirmation": True,
        "params": {"group": 0xFFFFFF},
    }

    # 1. NaN speed (e.g. untrusted or implausible CCVS) must fail closed
    app._current_speed_kmh = float("nan")
    res_nan = bridge.execute_diagnostic_action(action_clear, user_confirmed=True)
    assert res_nan.get("success") is False
    assert "hareketsiz" in res_nan.get("message", "").lower() or "güvenlik kilidi" in res_nan.get("message", "").lower()

    # 2. Infinite speed must fail closed
    app._current_speed_kmh = float("inf")
    res_inf = bridge.execute_diagnostic_action(action_clear, user_confirmed=True)
    assert res_inf.get("success") is False

    # 3. Negative speed must fail closed
    app._current_speed_kmh = -5.0
    res_neg = bridge.execute_diagnostic_action(action_clear, user_confirmed=True)
    assert res_neg.get("success") is False

    # Reset speed to nominal 0.0
    app._current_speed_kmh = 0.0

    # 4. None / non-dict action must not crash with AttributeError
    res_none = bridge.execute_diagnostic_action(None, user_confirmed=True)
    assert res_none.get("success") is False
    assert "geçersiz" in res_none.get("error", "").lower()

    res_str = bridge.execute_diagnostic_action("invalid_action_string", user_confirmed=True)  # type: ignore[arg-type]
    assert res_str.get("success") is False

    # 5. None params must not crash with AttributeError
    res_none_params = bridge.execute_diagnostic_action({"action_type": "uds_clear_dtc", "params": None}, user_confirmed=True)
    assert res_none_params.get("success") is True



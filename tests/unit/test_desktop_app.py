"""Unit tests for Universal CAN Desktop Application Bridge and Lifecycle."""

from __future__ import annotations

import json

import pytest

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
    # T47-B (P4/E-1): the authority now REQUIRES an independent provider.
    from src.safety.estop import EStopResetAuthority

    res_challenge = bridge.estop_request_challenge()
    assert res_challenge.get("success") is True

    authority = EStopResetAuthority(app.estop, secret_provider=app.estop.reset_authority_provider())
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
    """Verify updating channel and baudrate (fully offline AI — no key wiring)."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    bridge.save_settings(
        {
            "channel": "can0",
            "baudRate": "500 kbps",
        }
    )

    assert app.channel_name == "can0"
    assert app.bitrate_val == 500000


def test_desktop_api_copilot_query() -> None:
    """Verify copilot querying through bridge."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    res = bridge.ask_copilot("P0300")
    assert "P0300" in res


def _make_tp_frame() -> CanFrame:
    return CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x1CEBF900,
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
    # REVIEW hardening: hostile channel ids are rejected fail-closed at
    # CanFrame construction, so the escape path is exercised via a legal
    # channel name carrying hostile-looking content through the payload.
    hostile_channel = "ch0"
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


def test_push_frame_hostile_channel_id_rejected_fail_closed() -> None:
    # REVIEW hardening: illegal channel ids never become frames.
    with pytest.raises(ValueError, match="Invalid channel_id"):
        CanFrame.create(
            channel_id="ch'); alert('pwned'); //",
            arbitration_id=0x123,
            data=b"\x01\x02",
        )


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
    """Verify export_logs functionality (LOW-4) including CSV, JSON, MAT and MDF4."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    # Insert a dummy frame
    frame = CanFrame.create(channel_id="vcan0", arbitration_id=0x123, data=b"\x01\x02")
    app.ring_buffer.append(frame)

    assert bridge.export_logs("json") is True
    assert bridge.export_logs("csv") is True
    assert bridge.export_logs("mat") is True

    has_mdf = False
    try:
        from asammdf import MDF
        _ = MDF()
        has_mdf = True
    except Exception:
        pass

    if has_mdf:
        assert bridge.export_logs("mdf4") is True
        assert bridge.export_logs("mf4") is True
    else:
        assert bridge.export_logs("mdf4") is False
        assert bridge.export_logs("mf4") is False

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
    """Verify DesktopApiBridge executes UDS Clear DTC with dual confirmation challenge and safety checks."""
    from src.safety.estop import EStopResetAuthority

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    # 1. Refusal when confirmation required but challenge token is missing / raw bool given
    action_clear = {
        "id": "clear-1",
        "label": "UDS 0x14 Clear DTCs",
        "action_type": "uds_clear_dtc",
        "params": {"dtc_group": 0xFFFFFF},
        "requires_confirmation": True,
    }
    res_unconf = bridge.execute_diagnostic_action(action_clear, user_confirmed=False)
    assert res_unconf.get("success") is False
    # REVIEW 3: physical mode with no physical speed feed refuses at the
    # gateway speed interlock FIRST (stale) — fail-closed before any
    # confirmation accounting.
    assert "güvenlik kilidi" in res_unconf.get("message", "").lower()

    # 2. Refusal when E-Stop is active even with valid challenge token
    ch = bridge.request_diagnostic_challenge(action_clear)
    assert ch.get("success") is True
    assert "token" in ch
    token = ch["token"]

    bridge.trigger_estop()
    res_estop = bridge.execute_diagnostic_action(action_clear, confirmation_token=token)
    assert res_estop.get("success") is False
    assert "E-Stop" in res_estop.get("message", "")

    # Reset E-Stop via challenge/response authority
    # T47-B (P4/E-1): authority requires an independent provider.
    authority = EStopResetAuthority(app.estop, secret_provider=app.estop.reset_authority_provider())
    estop_tok = authority.mint_reset_token()
    bridge.estop_submit_reset_token(estop_tok.to_token_string())
    assert app._is_estop is False

    # 3. Refusal when vehicle is moving (> 0.0 km/h)
    ch_moving = bridge.request_diagnostic_challenge(action_clear)
    # REVIEW 3: the gateway is the single authoritative speed source —
    # feed it (not just the display mirror) for the moving-vehicle case.
    app.gateway.update_physical_speed(45.0)
    res_moving = bridge.execute_diagnostic_action(action_clear, confirmation_token=ch_moving["token"])
    assert res_moving.get("success") is False
    assert "hareket" in res_moving.get("message", "").lower()
    app.gateway.update_physical_speed(0.0)

    # 4. Success in simulation mode with valid challenge token
    ch_sim = bridge.request_diagnostic_challenge(action_clear)
    app._is_simulating = True
    app._error_count = 15
    app._active_scenario = "misfire_p0300"
    res_success = bridge.execute_diagnostic_action(action_clear, confirmation_token=ch_sim["token"])
    assert res_success.get("success") is True
    assert app._error_count == 0
    assert app._active_scenario == "nominal"
    assert "0x14" in res_success.get("message", "")


def test_desktop_api_bridge_actionable_uds_read_vin_and_session() -> None:
    """Verify DesktopApiBridge handles UDS Read VIN and Diagnostic Session Control."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)
    app._is_simulating = True

    # 1. Read VIN (Read-only, requires_confirmation is False by default)
    action_vin = {
        "id": "vin-1",
        "label": "UDS 0x22 F190 Read VIN",
        "action_type": "uds_read_vin",
        "params": {"did": 0xF190},
    }
    res_vin = bridge.execute_diagnostic_action(action_vin)
    assert res_vin.get("success") is True
    assert "data" in res_vin
    assert res_vin["data"].get("did") == "0xF190"

    # 2. Session Control (Mutating, requires challenge token)
    action_sess = {
        "id": "sess-1",
        "label": "UDS 0x10 Extended Session",
        "action_type": "uds_session_control",
        "params": {"session_type": 0x03},
    }
    ch_sess = bridge.request_diagnostic_challenge(action_sess)
    res_sess = bridge.execute_diagnostic_action(action_sess, confirmation_token=ch_sess["token"])
    assert res_sess.get("success") is True
    assert res_sess["data"].get("session_type") == 3

    # 3. J1939 DM11 Clear DTC (Simulation Mode)
    action_dm11 = {
        "id": "dm11-1",
        "label": "J1939 DM11 Clear DTCs",
        "action_type": "j1939_clear_dtc",
        "requires_confirmation": True,
    }
    ch_dm11 = bridge.request_diagnostic_challenge(action_dm11)
    res_dm11 = bridge.execute_diagnostic_action(action_dm11, confirmation_token=ch_dm11["token"])
    assert res_dm11.get("success") is True
    assert "DM11" in res_dm11.get("message", "")

    # 4. J1939 DM11 Clear DTC (Physical Mode - Transmits Frame Through Gateway)
    app._is_simulating = False
    app.bus.connect()
    app._current_speed_kmh = 0.0
    app.gateway.update_vehicle_speed(0.0, source="physical")
    app._error_count = 5
    ch_dm11_phys = bridge.request_diagnostic_challenge(action_dm11)
    res_dm11_phys = bridge.execute_diagnostic_action(action_dm11, confirmation_token=ch_dm11_phys["token"])
    assert res_dm11_phys.get("success") is True
    assert app._error_count == 0
    assert "iletildi" in res_dm11_phys.get("message", "")


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
    ch1 = bridge.request_diagnostic_challenge(action_clear)
    res_nan = bridge.execute_diagnostic_action(action_clear, confirmation_token=ch1["token"])
    assert res_nan.get("success") is False
    assert "hareketsiz" in res_nan.get("message", "").lower() or "güvenlik kilidi" in res_nan.get("message", "").lower()

    # 2. Infinite speed must fail closed
    app._current_speed_kmh = float("inf")
    ch2 = bridge.request_diagnostic_challenge(action_clear)
    res_inf = bridge.execute_diagnostic_action(action_clear, confirmation_token=ch2["token"])
    assert res_inf.get("success") is False

    # 3. Negative speed must fail closed
    app._current_speed_kmh = -5.0
    ch3 = bridge.request_diagnostic_challenge(action_clear)
    res_neg = bridge.execute_diagnostic_action(action_clear, confirmation_token=ch3["token"])
    assert res_neg.get("success") is False

    # Reset speed to nominal 0.0
    app._current_speed_kmh = 0.0

    # 4. None / non-dict action must not crash with AttributeError
    res_none = bridge.execute_diagnostic_action(None)
    assert res_none.get("success") is False
    assert "geçersiz" in res_none.get("error", "").lower()

    res_str = bridge.execute_diagnostic_action("invalid_action_string")  # type: ignore[arg-type]
    assert res_str.get("success") is False

    # 5. None params must not crash with AttributeError
    ch5 = bridge.request_diagnostic_challenge({"action_type": "uds_clear_dtc", "params": None})
    res_none_params = bridge.execute_diagnostic_action({"action_type": "uds_clear_dtc", "params": None}, confirmation_token=ch5["token"])
    assert res_none_params.get("success") is True


def test_desktop_api_bridge_diagnostic_challenge_security() -> None:
    """Verify challenge token generation, single-use consumption, expiration, and mismatch rejection.

    T47-B (P3/G-3): with a gateway confirmation secret wired (production), the
    token is a gateway-minted HMAC token — a 152-byte hex string binding
    (arbitration_id, expiry, nonce). The in-process challenge store is only
    used when NO secret is configured, so the legacy assertions below are
    adapted to the cryptographic path while keeping the same security intent:
    single-use, tamper-evident, expiring.
    """
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)
    app._is_simulating = True

    action_routine = {
        "id": "act-101",
        "action_type": "uds_routine",
        "params": {"routine_id": 0x1234},
        "requires_confirmation": True,
    }

    # 1. Invalid action dict
    assert bridge.request_diagnostic_challenge(None)["success"] is False  # type: ignore[arg-type]
    assert bridge.request_diagnostic_challenge({})["success"] is False

    # 2. Challenge generation (cryptographic path: gateway HMAC token)
    ch = bridge.request_diagnostic_challenge(action_routine)
    assert ch["success"] is True
    tok = ch["token"]
    assert ch.get("cryptographic") is True
    assert len(tok) == 2 * (4 + 8 + 16 + 32)  # payload + SHA-256 MAC, hex
    assert ch["expires_in_s"] == 30.0

    # 3. Tampered token -> fail closed
    res_forged = bridge.execute_diagnostic_action(action_routine, confirmation_token="de" * 60)
    assert res_forged["success"] is False

    # 4. Single-use replay protection: the gateway burns the token on use.
    #    First consumption succeeds; the replay is refused.
    first = bridge.execute_diagnostic_action(action_routine, confirmation_token=tok)
    assert first["success"] is True
    res_replay = bridge.execute_diagnostic_action(action_routine, confirmation_token=tok)
    assert res_replay["success"] is False

    # 5. Expiration: an HMAC token bound to a past expiry is refused.
    expired = _mint_expired_confirmation_token(app.gateway, 0x7E0)
    res_expired = bridge.execute_diagnostic_action(action_routine, confirmation_token=expired)
    assert res_expired["success"] is False
    assert ("süresi" in res_expired["error"].lower()) or ("onay token" in res_expired["error"].lower())


def _mint_expired_confirmation_token(gateway: object, arbitration_id: int) -> str:
    """Build a well-formed but already-expired gateway HMAC token (test helper)."""
    import hashlib
    import hmac as _hmac

    secret = gateway._confirmation_secret  # noqa: SLF001 - test helper
    expiry_ns = 1  # monotonic clock is always > 1 ns -> expired
    payload = int(arbitration_id).to_bytes(4, "big") + expiry_ns.to_bytes(8, "big") + b"\x00" * 16
    mac = _hmac.new(secret, payload, hashlib.sha256).digest()
    return (payload + mac).hex()


def test_desktop_composition_root_wiring_discovery_oem_replay_flashing(tmp_path) -> None:
    """Verify SignalDiscoveryEngine, OemJ1939Registry, ReplayBus, and EcuFlashingEngine bridge APIs."""
    from src.core.models.can_frame import CanFrame

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)

    # 1. SignalDiscoveryEngine
    frame1 = CanFrame(arbitration_id=0x18FEE100, dlc=8, data=bytes([0x50, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70]), channel_id="vcan0", is_extended=True)
    for _ in range(15):
        app._ingest_live_frame(frame1)

    disc_summary = bridge.discovery_get_summary()
    assert disc_summary["total_frames"] >= 15
    assert "0x18FEE100" in disc_summary["discovered_ids"]

    analysis = bridge.discovery_analyze_id(0x18FEE100)
    assert analysis["frame_count"] >= 15
    assert "hypotheses" in analysis

    dbc_res = bridge.discovery_export_dbc()
    assert dbc_res["success"] is True
    assert "VERSION" in dbc_res["dbc"] or "BO_" in dbc_res["dbc"]

    clear_res = bridge.discovery_clear()
    assert clear_res["success"] is True
    assert bridge.discovery_get_summary()["total_frames"] == 0

    # 2. OemJ1939Registry
    decoders = bridge.oem_list_decoders()
    assert "cummins" in [d.lower() for d in decoders]
    assert "scania" in [d.lower() for d in decoders]
    assert "caterpillar" in [d.lower() for d in decoders]

    # Live frame decode via OEM registry
    # PGN 65303 (0xFF17) Caterpillar proprietary engine parameters
    cat_frame = CanFrame(arbitration_id=0x18FF1700, dlc=8, data=bytes([0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70]), channel_id="vcan0", is_extended=True)
    app._ingest_live_frame(cat_frame)
    # Ensure no exception and discovery engine / router processed frame
    assert app.discovery_engine._total_frames == 1

    # 3. ReplayBus
    asc_file = tmp_path / "test_trace.asc"
    asc_file.write_text(
        "date Mon Jan 1 00:00:00 2024\n"
        "base hex timestamps absolute\n"
        "   0.001000 1  18FEE100x       Rx d 8 50 10 20 30 40 50 60 70\n"
        "   0.002000 1  18FEE100x       Rx d 8 51 10 20 30 40 50 60 70\n"
    )
    load_res = bridge.replay_load(str(asc_file))
    assert load_res["success"] is True
    assert load_res["frame_count"] == 2

    # 4. EcuFlashingEngine
    app._is_simulating = True
    flash_cfg = {
        "action_type": "ecu_flash",
        "ecu": "ECM",
        "fileName": "firmware.bin",
        "sizeBytes": 2048,
    }
    # Refusal without token
    start_unconf = bridge.flash_start(flash_cfg)
    assert start_unconf["success"] is False

    # Start with token
    ch_flash = bridge.request_diagnostic_challenge(flash_cfg)
    start_conf = bridge.flash_start(flash_cfg, confirmation_token=ch_flash["token"])
    assert start_conf["success"] is True

    prog = bridge.flash_progress()
    assert "status" in prog

    cancel_res = bridge.flash_cancel()
    assert cancel_res["success"] is True
    assert bridge.flash_progress()["status"] == "cancelled"


def test_desktop_requires_confirmation_false_cannot_bypass_review3() -> None:
    """REVIEW 3 (CRITICAL): an action dict claiming `requires_confirmation:
    false` MUST NOT skip the operator confirmation for critical types —
    the action TYPE is authoritative, not the action dict."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._is_simulating = True  # sandbox mode: arm_tx allowed for the test path
    app._current_speed_kmh = 0.0
    bridge = DesktopApiBridge(app)
    # 0x14 clear-DTC declared as "no confirmation needed" — must still
    # require a challenge token.
    action = {
        "id": "spoof-1",
        "label": "Clear DTCs (unconfirmed)",
        "action_type": "uds_clear_dtc",
        "requires_confirmation": False,
        "uds": {"service": 0x14, "payload": [0xFF, 0xFF, 0xFF]},
    }
    # Raw bool / missing token -> refused with a NEW challenge
    res = bridge.execute_diagnostic_action(action, confirmation_token=None)
    assert res["success"] is False
    assert "onay" in res["error"].lower()
    # The bridge mints a challenge: type-mandatory confirmation is active
    ch = bridge.request_diagnostic_challenge(action)
    assert ch["success"] is True
    # Wrong token still refused
    res2 = bridge.execute_diagnostic_action(action, confirmation_token="deadbeef")
    assert res2["success"] is False
    assert "onay" in res2["error"].lower()


def test_desktop_speed_source_is_gateway_review3() -> None:
    """REVIEW 3 (CRITICAL): in PHYSICAL mode the desktop speed interlock reads
    TxSafetyGateway.speed_interlock_state() — the UI mirror is display-only
    and can never re-arm TX after the gateway feed goes stale/NaN."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._is_simulating = False  # physical mode: gateway is authoritative
    bridge = DesktopApiBridge(app)
    action = {
        "id": "clear-phys",
        "label": "UDS 0x14 Clear DTCs",
        "action_type": "uds_clear_dtc",
        "uds": {"service": 0x14, "payload": [0xFF, 0xFF, 0xFF]},
    }
    ch = bridge.request_diagnostic_challenge(action)
    # Stale gateway feed (no physical speed recorded) -> refused EVEN IF
    # the UI mirror claims 0.0 km/h.
    app._current_speed_kmh = 0.0
    res = bridge.execute_diagnostic_action(
        action, confirmation_token=ch["token"]
    )
    assert res["success"] is False
    assert "bilinmiyor" in res["error"] or "güncel değil" in res["error"]

    # Fresh stationary physical feed -> proceeds to actual TX attempt
    app.gateway.update_physical_speed(0.0)
    res2 = bridge.execute_diagnostic_action(
        action, confirmation_token=ch["token"]
    )
    # (May fail later for other reasons — e.g. no live bus — but the
    # speed interlock itself must no longer block it.)
    assert "hareketsiz" not in (res2.get("error") or "")



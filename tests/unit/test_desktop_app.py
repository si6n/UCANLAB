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

    # Test CRIT-1 & CRIT-2 reset
    res = bridge.estop_reset_local()
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

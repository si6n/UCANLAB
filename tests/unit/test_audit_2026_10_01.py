"""Regression tests for the 2026-10-01 full audit (docs/audit/AUDIT_REPORT.md).

Each test names the finding it pins. They exercise the real objects (no mock
standing in for the code under test) so a regression shows up as a failure,
not as a green test over a stub.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.engine.pipeline.reassembly_pipeline import ReassembledMessage
from src.protocols.j1939.diagnostics import J1939DiagnosticService
from src.protocols.obd.models import ObdPidResult


def _app() -> Any:
    from src.ui.desktop_app import UniversalCanDesktopApp

    return UniversalCanDesktopApp(channel="vcan0", bitrate=500000)


# ---------------------------------------------------------------------------
# AUD-01: a reassembled J1939 VIN (PGN 65260) crashed the handler with
# AttributeError ('ReassembledMessage' has no attribute 'bytes'), so the VIN
# was never detected and vehicle-identity checks ran without it.
# ---------------------------------------------------------------------------


def test_aud01_reassembled_vin_is_detected_and_logged_masked(caplog: pytest.LogCaptureFixture) -> None:
    app = _app()
    vin = "1M8GDM9AXKP042788"
    msg = ReassembledMessage(
        protocol="J1939",
        data=(vin + "*").encode("ascii"),
        timestamp_ns=1,
        channel_id="vcan0",
        pgn=65260,
    )

    app._handle_reassembled_message(msg)

    assert app._detected_vin == vin
    # The full VIN must never reach the log (project rule: masked VIN only).
    assert vin not in caplog.text


# ---------------------------------------------------------------------------
# AUD-02: start_obd_polling referenced a non-existent `self.safe_bus`, raised
# AttributeError on every call and leaked the router subscription it had just
# registered.
# ---------------------------------------------------------------------------


def test_aud02_obd_polling_starts_through_the_gateway_and_stops_cleanly() -> None:
    app = _app()
    try:
        result = app.start_obd_polling(pids=[0x0C], rate_hz=1.0)
        assert result["success"] is True, result
        # TX authority is the safety gateway (single choke-point), never the bus.
        assert app.obd_poller is not None
        assert app.obd_poller.tx_port is app.gateway
    finally:
        stopped = app.stop_obd_polling()
    assert stopped["success"] is True
    assert app.obd_poller is None
    assert app._obd_router_sub_id is None
    assert app._obd_poller_rx_sub is None


def test_aud02_failed_obd_start_releases_the_router_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    before = len(app.router._subscriptions) if hasattr(app.router, "_subscriptions") else None

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("poller construction failed")

    monkeypatch.setattr("src.ui.desktop_app.ActiveDiagnosticPoller", _boom)
    result = app.start_obd_polling(pids=[0x0C], rate_hz=1.0)

    assert result["success"] is False
    assert app.obd_poller is None
    assert app._obd_router_sub_id is None
    if before is not None:
        assert len(app.router._subscriptions) == before


# ---------------------------------------------------------------------------
# AUD-03: _handle_obd_pid_result read `result.success`, a field ObdPidResult
# does not have, so every decoded PID raised and live RPM / speed / coolant
# telemetry was never recorded.
# ---------------------------------------------------------------------------


def test_aud03_valid_pid_result_updates_live_telemetry() -> None:
    app = _app()
    app._handle_obd_pid_result(
        ObdPidResult(pid=0x0C, name="ENGINE_RPM", raw_bytes=b"\x0f\xa0", value=1000.0, unit="rpm")
    )
    assert app._current_rpm == 1000.0


def test_aud03_invalid_pid_result_is_ignored() -> None:
    app = _app()
    before = app._current_rpm
    app._handle_obd_pid_result(
        ObdPidResult(pid=0x0C, name="ENGINE_RPM", raw_bytes=b"", value=9999.0, unit="rpm", is_valid=False)
    )
    assert app._current_rpm == before


# ---------------------------------------------------------------------------
# AUD-04: a standard single-frame DM1 is 8 bytes (lamp, flash lamp, one 4-byte
# DTC, two 0xFF fill bytes, SAE J1939-73 §5.7.1). The 0xFF fill was counted as a
# ragged DTC tail, so every normal DM1 was flagged MALFORMED and logged a warning.
# ---------------------------------------------------------------------------


def test_aud04_single_frame_dm1_with_ff_fill_is_not_malformed() -> None:
    # SPN 100 (0x64), FMI 1, OC 3 -> 64 00 01 03, then 0xFF 0xFF fill.
    data = bytes([0x04, 0xFF, 0x64, 0x00, 0x01, 0x03, 0xFF, 0xFF])
    msg = J1939DiagnosticService.parse_dm1_or_dm2(data, pgn=65226, source_address=0x00)
    assert msg is not None
    assert msg.malformed is False
    assert [(d.spn, d.fmi, d.occurrence_count) for d in msg.dtcs] == [(100, 1, 3)]


def test_aud04_no_fault_dm1_is_not_malformed() -> None:
    data = bytes([0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF])
    msg = J1939DiagnosticService.parse_dm1_or_dm2(data, pgn=65226)
    assert msg is not None
    assert msg.malformed is False
    assert msg.dtcs == []


def test_aud04_a_real_ragged_tail_is_still_malformed() -> None:
    # Two meaningful bytes after a full DTC record are not 0xFF fill.
    data = bytes([0x04, 0xFF, 0x64, 0x00, 0x01, 0x03, 0x12, 0x34])
    msg = J1939DiagnosticService.parse_dm1_or_dm2(data, pgn=65226)
    assert msg is not None
    assert msg.malformed is True

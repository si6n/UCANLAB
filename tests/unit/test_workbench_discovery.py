"""Workbench B3 (Sinyal keşfi): stream-keyed discovery surface over the Python engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cantools
import pytest

from src.core.models.can_frame import CanFrame
from src.engine.discovery.engine import SignalDiscoveryEngine

EEC1 = 0x0CF00400


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import src.ui.desktop_app as da

    monkeypatch.setattr(da, "_app_data_root", lambda: tmp_path)
    application = da.UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    application._reconnect_bus("virtual", "disc_test", 250000)
    return application


def _pump(app: Any, n: int) -> None:
    for _ in range(n):
        app._ingest_live_frame(app.bus.recv(timeout_s=0.0))


def _sim_truck(app: Any, n: int = 2100) -> str:
    assert app.start_simulated_vehicle("truck")["success"]
    _pump(app, n)
    return f"sim_vehicle|1|{EEC1}"


def test_list_marks_simulator_streams(app: Any) -> None:
    _sim_truck(app)
    out = app.discovery_list()
    keys = {s["key"]: s for s in out["streams"]}
    assert f"sim_vehicle|1|{EEC1}" in keys
    assert all(s["simulated"] and s["analyzable"] and not s["replay"] for s in out["streams"])


def test_report_shows_the_moving_rpm_bits(app: Any) -> None:
    key = _sim_truck(app)
    rep = app.discovery_report(key)
    assert rep["success"] and rep["simulated"] and rep["dlc"] == 8
    assert len(rep["bit_classes"]) == 64 and len(rep["entropy"]) == 8
    # EEC1 bytes 3..4 carry engine speed, which the workbench simulator varies.
    assert any(rep["bit_classes"][b] != "CONST" for b in range(24, 32))
    assert all(rep["bit_classes"][b] == "CONST" for b in range(0, 16))
    types = [h["type"] for h in rep["hypotheses"]]
    assert types == sorted(types, key=lambda t: {"COUNTER": 0, "CHECKSUM": 1, "SIGNAL": 2, "CONSTANT": 3}[t])
    assert any(h["type"] == "SIGNAL" and h["start_bit"] <= 24 < h["start_bit"] + h["length"] for h in rep["hypotheses"])


@pytest.mark.parametrize("bad", [None, 5, "x", "a|2|5", "a|1|-1", "a|1|zz", "a|1|" + str(0x20000000), "a" * 200])
def test_report_rejects_malformed_keys(app: Any, bad: Any) -> None:
    assert app.discovery_report(bad) == {"success": False, "error_code": "INVALID_KEY"}


def test_report_of_unknown_stream(app: Any) -> None:
    assert app.discovery_report("can0|0|291")["error_code"] == "UNKNOWN_STREAM"


def test_approval_survives_new_traffic_and_reaches_the_dbc(app: Any, tmp_path: Path) -> None:
    key = _sim_truck(app)
    signal = next(h for h in app.discovery_report(key)["hypotheses"] if h["type"] == "SIGNAL")
    assert app.discovery_set_approval(key, signal["start_bit"], signal["length"], True) == {"success": True, "approved": True}
    _pump(app, 700)  # new frames drop the cached report; the approval must stay
    again = next(h for h in app.discovery_report(key)["hypotheses"]
                 if h["start_bit"] == signal["start_bit"] and h["length"] == signal["length"])
    assert again["status"] == "approved"

    saved = app.discovery_save_dbc(True)
    assert saved["success"] and saved["signals"] == 1 and saved["simulated"] is True
    path = Path(saved["path"])
    assert path.name.endswith("_SIMULATOR.dbc") and path.is_file()
    assert path.resolve().is_relative_to((tmp_path / "exports").resolve())
    db = cantools.database.load_file(str(path))
    assert sum(len(m.signals) for m in db.messages) == 1

    assert app.discovery_set_approval(key, signal["start_bit"], signal["length"], False)["approved"] is False
    assert app.discovery_save_dbc(True) == {"success": False, "error_code": "NOTHING_TO_EXPORT"}


def test_approval_input_validation(app: Any) -> None:
    key = _sim_truck(app, 700)
    assert app.discovery_set_approval(key, "0", 8, True)["error_code"] == "INVALID_INPUT"
    assert app.discovery_set_approval(key, 0, 8, "yes")["error_code"] == "INVALID_INPUT"
    assert app.discovery_set_approval("bad", 0, 8, True)["error_code"] == "INVALID_INPUT"
    assert app.discovery_set_approval(key, 63, 1, True)["error_code"] == "NO_SUCH_HYPOTHESIS"
    assert app.discovery_save_dbc("yes")["error_code"] == "INVALID_INPUT"


def test_bus_switch_clears_discovery(app: Any) -> None:
    _sim_truck(app, 700)
    assert app.discovery_list()["streams"]
    app.stop_simulated_vehicle()
    assert app.discovery_list()["streams"] == []


def test_engine_approval_is_not_lost_when_the_cache_is_rebuilt() -> None:
    """Regression: approvals lived only on cached Hypothesis objects."""
    engine = SignalDiscoveryEngine()

    def frames(start: int, n: int) -> list[CanFrame]:
        return [CanFrame(channel_id="can0", arbitration_id=0x300, dlc=8,
                         data=bytes([i % 16, (i * 7) % 256, 0, 0, 0, 0, 0, 0]),
                         timestamp_ns=(start + i) * 10_000_000) for i in range(start, start + n)]

    engine.ingest_frames(frames(0, 200))
    hyp = next(h for h in engine.analyze_key(("can0", False, 0x300)).hypotheses if h.htype != "CONSTANT")
    assert engine.set_approval(("can0", False, 0x300), hyp.start_bit, hyp.length, True)
    engine.ingest_frames(frames(200, 50))  # invalidates the cached report
    statuses = {(h.start_bit, h.length): h.status for h in engine.analyze_key(("can0", False, 0x300)).hypotheses}
    assert statuses[(hyp.start_bit, hyp.length)] == "approved"
    assert sum(len(m.signals) for m in engine.build_dbc(approved_only=True).messages) >= 1
    engine.clear()
    assert engine.build_dbc(approved_only=True).messages == []


def test_bridge_risk_classes() -> None:
    from src.ui.desktop_app import DesktopApiBridge

    m = DesktopApiBridge.BRIDGE_RISK_MANIFEST
    assert (m["discovery_list"], m["discovery_report"], m["discovery_set_approval"], m["discovery_save_dbc"]) == (
        "read", "read", "config", "data")

"""Workbench B7 (Kayıtlar): records by opaque id, honest replay, approved upload."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.core.models.can_frame import CanFrame

CCVS_STOPPED = "18FEF100x       Rx d 8 F0 00 00 C0 00 00 00 FF"  # PGN 65265, SA 0, 0 km/h
ET1_85C = "18FEEE00x       Rx d 8 7D FF FF FF FF FF FF FF"  # PGN 65262, 85 °C
DM1_ACTIVE = "18FECA00x       Rx d 8 04 FF B3 0C 00 01 FF FF"  # SPN 3251 FMI 0


def _asc(lines: list[str]) -> str:
    body = "".join(f"   {0.001 * (i + 1):.6f} 1  {line}\n" for i, line in enumerate(lines))
    return "date Mon Jan 1 00:00:00 2024\nbase hex timestamps absolute\n" + body


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import src.ui.desktop_app as da

    monkeypatch.setattr(da, "_app_data_root", lambda: tmp_path)
    monkeypatch.setattr(
        da.DesktopApiBridge,
        "ALLOWED_UPLOAD_ROOTS",
        ((tmp_path / "exports").resolve(), (tmp_path / "logs").resolve(), (tmp_path / "data" / "traces").resolve()),
    )
    app = da.UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._reconnect_bus("virtual", "records_test", 250000)
    (tmp_path / "exports").mkdir()
    (tmp_path / "data" / "traces").mkdir(parents=True)
    (tmp_path / "exports" / "truck.asc").write_text(_asc([CCVS_STOPPED, ET1_85C, DM1_ACTIVE] * 20))
    (tmp_path / "exports" / "report.json").write_text("{}")
    (tmp_path / "exports" / ".upload_stage").mkdir()
    (tmp_path / "exports" / ".upload_stage" / "raw.bin").write_bytes(b"x")
    (tmp_path / "data" / "traces" / "bench.csv").write_text("x")
    return app, da.DesktopApiBridge(app), tmp_path


def _play(app: Any, record_id: str = "exports/truck.asc") -> dict[str, Any]:
    res = app.records_replay_start(record_id, 10.0)
    assert res["success"] is True, res
    app._replay_thread.join(10)
    return app.records_replay_status()


def test_list_gives_ids_not_paths_and_skips_hidden_folders(env: Any) -> None:
    app, _bridge, tmp = env
    records = app.records_list()["records"]
    ids = {r["id"] for r in records}
    assert ids == {"exports/truck.asc", "exports/report.json", "traces/bench.csv"}
    assert all(str(tmp) not in str(r) for r in records)
    trace = next(r for r in records if r["id"] == "exports/truck.asc")
    assert trace["replayable"] is True and trace["kind"] == "trace" and trace["uploadable"] is True


@pytest.mark.parametrize(
    "record_id",
    ["exports/../../etc/passwd", "exports/.upload_stage/raw.bin", "secret/x.asc", "/etc/passwd", "exports\\truck.asc",
     "exports/", "", None, 7, "exports/missing.asc", "x" * 600],
)
def test_resolve_refuses_anything_but_a_listed_file(env: Any, record_id: Any) -> None:
    app, _bridge, _tmp = env
    assert app._resolve_record(record_id) is None


def test_symlinks_are_not_followed(env: Any) -> None:
    app, _bridge, tmp = env
    outside = tmp / "outside.asc"
    outside.write_text(_asc([CCVS_STOPPED]))
    (tmp / "exports" / "link.asc").symlink_to(outside)
    assert app._resolve_record("exports/link.asc") is None
    assert "exports/link.asc" not in {r["id"] for r in app.records_list()["records"]}


def test_replay_is_labelled_and_never_becomes_evidence(env: Any) -> None:
    app, _bridge, _tmp = env
    status = _play(app)
    assert status["running"] is False and status["position"] == status["frame_count"] == 60
    assert app._diag_session.samples == [] and app._diag_session.events == []
    series = app.plot_signal_list()["signals"]
    assert series and all(s["origin"] == "replay" for s in series)


def test_replayed_speed_cannot_satisfy_the_interlock(env: Any) -> None:
    app, _bridge, _tmp = env
    app.approve_ccvs_source(0x00, reason="test")
    assert app.gateway.speed_interlock_state()[0] == "stale"
    _play(app)
    assert app.gateway.speed_interlock_state()[0] == "stale"
    # Control: the same CCVS from the wire does feed the interlock.
    wire = CanFrame(channel_id="vcan0", arbitration_id=0x18FEF100, dlc=8,
                    data=bytes.fromhex("F00000C0000000FF"), is_extended=True, source="physical")
    app._ingest_live_frame(wire)
    assert app.gateway.speed_interlock_state()[0] == "ok"


def test_simulated_speed_cannot_satisfy_the_interlock(env: Any) -> None:
    app, _bridge, _tmp = env
    app.approve_ccvs_source(0x00, reason="test")
    synthetic = CanFrame(channel_id="vcan0", arbitration_id=0x18FEF100, dlc=8,
                         data=bytes.fromhex("F00000C0000000FF"), is_extended=True, source="synthetic")
    app._ingest_live_frame(synthetic)
    assert app.gateway.speed_interlock_state()[0] == "stale"


def test_replay_input_validation(env: Any) -> None:
    app, _bridge, _tmp = env
    assert app.records_replay_start("exports/report.json", 1.0)["error_code"] == "RECORD_UNKNOWN"
    assert app.records_replay_start("exports/truck.asc", 3.0)["error_code"] == "INVALID_SPEED"
    assert app.records_replay_start("exports/truck.asc", True)["error_code"] == "INVALID_SPEED"
    assert app.records_replay_start("traces/bench.csv", 1.0)["error_code"] == "RECORD_UNREADABLE"
    assert app.records_replay_start("exports/truck.asc", 0.5)["success"] is True
    assert app.records_replay_start("exports/truck.asc", 1.0)["error_code"] == "REPLAY_RUNNING"
    app.stop_replay()
    app._replay_thread.join(5)


def test_upload_needs_sign_in_and_a_file_specific_native_approval(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    app, bridge, _tmp = env
    sent: list[dict[str, Any]] = []

    class _Result:
        session_id, status = "s-1", "complete"

    monkeypatch.setattr(app.telemetry_uploader, "upload_file", lambda **kw: sent.append(kw) or _Result())
    monkeypatch.setattr(app.cloud_client, "has_session_token", lambda: False)
    assert bridge.records_upload("exports/truck.asc")["error_code"] == "NOT_SIGNED_IN"

    monkeypatch.setattr(app.cloud_client, "has_session_token", lambda: True)
    monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
    asked: list[str] = []

    class _Window:
        def create_confirmation_dialog(self, title: str, message: str) -> bool:
            asked.append(message)
            return False

    app._window = _Window()
    assert bridge.records_upload("exports/truck.asc")["error_code"] == "NOT_CONFIRMED"
    assert "truck.asc" in asked[0] and "VIN" in asked[0]
    assert sent == []

    _Window.create_confirmation_dialog = lambda self, title, message: True  # type: ignore[method-assign]
    res = bridge.records_upload("exports/truck.asc")
    assert res == {"success": True, "session_id": "s-1", "status": "complete"}
    assert sent[0]["vehicle_vin"] is None and sent[0]["user_consented"] is False
    assert bridge.records_upload("exports/.upload_stage/raw.bin")["error_code"] == "RECORD_UNKNOWN"


def test_open_folder_opens_only_the_exports_folder(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.ui.desktop_app as da

    app, _bridge, tmp = env
    calls: list[list[str]] = []
    monkeypatch.setattr(da.sys, "platform", "linux")
    monkeypatch.setattr(da.subprocess, "Popen", lambda args, **kw: calls.append(args))
    assert app.records_open_folder() == {"success": True}
    assert calls == [["xdg-open", str(tmp / "exports")]]


def test_records_bridge_manifest() -> None:
    from src.ui.desktop_app import DesktopApiBridge

    m = DesktopApiBridge.BRIDGE_RISK_MANIFEST
    assert m["records_list"] == "read" and m["records_replay_status"] == "read"
    assert m["records_replay_start"] == "safety" and m["records_replay_stop"] == "safety"
    assert m["records_upload"] == "data" and m["records_open_folder"] == "data"


def test_replay_reaches_the_traffic_screen_but_not_the_capture_buffer(env: Any) -> None:
    app, _bridge, _tmp = env
    pushed: list[str] = []

    class _Window:
        def evaluate_js(self, js: str) -> None:
            pushed.append(js)

    app._window = _Window()
    before = app.ring_buffer.current_size
    _play(app)
    batches = [js for js in pushed if "onNewCanFrames" in js]
    assert batches and all('"source": "replay"' in js for js in batches)
    assert sum(js.count('"id"') for js in batches) == 60
    assert app.ring_buffer.current_size == before

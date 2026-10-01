"""Workbench B4 (Teşhis asistanı): honest inputs to the analysis pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.models.can_frame import CanFrame
from src.engine.connection.simulated_vehicle import SimulatedVehicleBus


@pytest.fixture
def app() -> Any:
    from src.ui.desktop_app import UniversalCanDesktopApp

    application = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    application._reconnect_bus("virtual", "assist_test", 250000)
    return application


def _pump(app: Any, n: int) -> None:
    for _ in range(n):
        app._ingest_live_frame(app.bus.recv(timeout_s=0.0))


def _physical_truck_frames(n: int) -> list[CanFrame]:
    sim = SimulatedVehicleBus("truck", 250000, sleep=False)
    sim.connect()
    out = []
    for _ in range(n):
        f = sim.recv(timeout_s=0.0)
        out.append(CanFrame(channel_id="vcan0", arbitration_id=f.arbitration_id, dlc=f.dlc, data=f.data,
                            is_extended=f.is_extended, source="physical"))
    return out


def test_simulator_analysis_is_labelled_and_kept_apart(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    _pump(app, 2000)
    res = app.get_diagnostic_analysis()
    assert res["success"] and res["simulated"] is True
    assert "SPN 3251 FMI 0" in res["user_card"]["technical"]["dtcs"]
    assert app._diag_session.samples == [] and app._diag_session.events == []
    assert app.get_session_evidence_summary()["is_simulating"] is True
    # The simulator repeats its DM1 ~270 times here; the simulated session keeps one event.
    assert len(app._sim_diag_session.events) == 1


def test_answers_in_simulation_stay_in_the_simulated_session(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    _pump(app, 700)
    q = app.get_dialogue_state()["current_question"]
    assert q is not None
    res = app.record_operator_answer(q["id"], None, q["kind"], q.get("unit"), True)
    assert res["success"]
    assert all(not s.name.startswith("OP:") for s in app._diag_session.samples)


def test_bus_switch_resets_simulated_session_and_dialogue(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    _pump(app, 500)
    app.get_dialogue_state()
    app.stop_simulated_vehicle()
    assert app._sim_diag_session is None and app._dialogue_session is None
    res = app.get_diagnostic_analysis()
    assert res.get("simulated") is not True


def test_real_bus_analysis_is_not_marked_simulated(app: Any) -> None:
    for f in _physical_truck_frames(800):
        app._ingest_live_frame(f)
    res = app.get_diagnostic_analysis()
    assert res["success"] and "simulated" not in res
    assert len(app._diag_session.events) >= 1


def test_unmeasured_boost_never_reaches_the_analysis(app: Any) -> None:
    app._current_rpm = 900.0
    snap = app._live_telemetry_snapshot()
    assert "BoostPressure" not in snap and snap["EngineSpeed"] == 900.0
    app._current_boost = 1.4
    app._boost_measured = True
    assert app._live_telemetry_snapshot()["BoostPressure"] == 1.4


def test_simulated_values_never_reach_the_analysis_telemetry(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    _pump(app, 500)
    assert app._current_rpm > 0  # the simulator's decoded engine speed
    assert app._live_telemetry_snapshot() == {}


def test_dm1_count_is_not_an_error_frame_count(app: Any) -> None:
    for f in _physical_truck_frames(400):
        app._ingest_live_frame(f)
    assert app._active_dtc_count == 1
    assert app._error_count == 0
    snapshot = app.get_bus_traffic_snapshot()
    assert snapshot["error_count"] == 0
    assert not any("Hata karesi" in a for a in snapshot["anomalies"])


def test_measured_bus_load_from_frame_bits(app: Any) -> None:
    frames = [CanFrame(channel_id="vcan0", arbitration_id=0x18FEF100, dlc=8, data=bytes(8), is_extended=True)] * 400
    assert app._measured_bus_load(frames, 1_000_000_000) == app._bus_load  # first call starts the clock
    # 400 × (67 + 64) bits in 1 s on a 250 kbit/s bus = 20.96 %
    assert app._measured_bus_load(frames, 2_000_000_000) == 21
    assert app._measured_bus_load(frames * 10, 2_100_000_000) == 100  # clamped
    assert app._measured_bus_load([], 3_100_000_000) == 0


def test_simulated_session_is_bounded(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    app._SIM_SESSION_SAMPLES_MAX = 50
    _pump(app, 600)
    assert len(app._sim_diag_session.samples) == 50

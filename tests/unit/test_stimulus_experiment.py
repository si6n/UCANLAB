"""B3b: press-and-release stimulus experiment (engine + app endpoints)."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.models.can_frame import CanFrame
from src.engine.discovery.stimulus import CLEAN_STEP_SCORE, StimulusExperiment

PEDAL_ID = 0x0CF00300  # EEC2 in the workbench simulator
RPM_ID = 0x0CF00400  # EEC1


def _frame(arb: int, data: list[int], ext: bool = True) -> CanFrame:
    return CanFrame(channel_id="can0", arbitration_id=arb, dlc=len(data), data=bytes(data), is_extended=ext)


def test_ranks_the_byte_that_follows_the_stimulus_first() -> None:
    exp = StimulusExperiment()
    for cycle in range(3):
        for i in range(20):
            exp.observe(_frame(0x100, [0, (i * 37) % 256, 7]))  # byte 1 is noise in both phases
        exp.set_phase("active")
        for i in range(20):
            exp.observe(_frame(0x100, [150, (i * 37 + cycle) % 256, 7]))  # byte 0: the control
        exp.set_phase("rest")
    ranked = exp.analyze()
    assert ranked[0].key == ("can0", True, 0x100) and ranked[0].kind == "byte" and ranked[0].index == 0
    assert ranked[0].rest_value == 0 and ranked[0].active_value == 150 and ranked[0].score == CLEAN_STEP_SCORE
    assert not any(c.kind == "byte" and c.index in (1, 2) for c in ranked)  # noise and constants never score
    assert exp.switches == 6


def test_needs_frames_in_both_phases() -> None:
    exp = StimulusExperiment()
    for _ in range(50):
        exp.observe(_frame(0x200, [1, 2]))
    assert exp.analyze() == []
    exp.set_phase("active")
    for _ in range(StimulusExperiment.MIN_FRAMES_PER_PHASE - 1):
        exp.observe(_frame(0x200, [9, 2]))
    assert exp.analyze() == []  # too few active frames to say anything


def test_phase_validation_and_buffer_cap() -> None:
    exp = StimulusExperiment()
    with pytest.raises(ValueError):
        exp.set_phase("pressed")  # type: ignore[arg-type]
    for _ in range(StimulusExperiment.MAX_FRAMES_PER_PHASE + 100):
        exp.observe(_frame(0x300, [0]))
    assert exp.frame_counts() == {"rest": StimulusExperiment.MAX_FRAMES_PER_PHASE, "active": 0}


@pytest.fixture
def app() -> Any:
    from src.ui.desktop_app import UniversalCanDesktopApp

    application = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    application._reconnect_bus("virtual", "stim_test", 250000)
    return application


def _pump(app: Any, n: int) -> None:
    for _ in range(n):
        app._ingest_live_frame(app.bus.recv(timeout_s=0.0))


def test_simulated_pedal_experiment_end_to_end(app: Any) -> None:
    assert app.start_simulated_vehicle("truck")["success"]
    assert app.stimulus_result()["error_code"] == "NOT_RUNNING"
    assert app.stimulus_start()["phase"] == "rest"
    for _ in range(2):
        _pump(app, 900)
        assert app.stimulus_set_phase("active")["phase"] == "active"
        assert app.sim_vehicle_pedal(True)["pressed"] is True
        _pump(app, 900)
        app.stimulus_set_phase("rest")
        app.sim_vehicle_pedal(False)
    res = app.stimulus_result()
    assert res["success"] and res["switches"] == 4
    top = res["candidates"][0]
    assert (top["arbitration_id"], top["kind"], top["index"]) == (PEDAL_ID, "byte", 1)
    assert top["rest"] == 0 and top["active"] == 150 and top["simulated"] is True  # 60 % / 0.4
    # The engine's reaction (EEC1 rpm high byte) is found too, as an indirect effect.
    assert any(c["arbitration_id"] == RPM_ID and c["kind"] == "byte" and c["index"] == 4 for c in res["candidates"])
    assert app.stimulus_stop() == {"success": True}
    assert app.stimulus_status() == {"running": False}


def test_endpoint_validation(app: Any) -> None:
    assert app.stimulus_set_phase("active")["error_code"] == "NOT_RUNNING"
    app.stimulus_start()
    assert app.stimulus_set_phase("pressed")["error_code"] == "INVALID_PHASE"
    assert app.sim_vehicle_pedal("yes")["error_code"] == "INVALID_INPUT"
    assert app.sim_vehicle_pedal(True)["error_code"] == "NOT_SIMULATED"  # real/virtual bus: no pedal to press


def test_bus_switch_ends_the_experiment(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    app.stimulus_start()
    app.stop_simulated_vehicle()
    assert app.stimulus_status() == {"running": False}


def test_wizard_simulator_has_no_pedal_frame() -> None:
    """The connection test's expected-PGN logic sees the same traffic as before."""
    from src.engine.connection.simulated_vehicle import SimulatedVehicleBus

    static = SimulatedVehicleBus("truck", 250000, sleep=False)
    static.connect()
    assert all(static.recv(timeout_s=0.0).arbitration_id != PEDAL_ID for _ in range(100))


def test_risk_classes() -> None:
    from src.ui.desktop_app import DesktopApiBridge

    m = DesktopApiBridge.BRIDGE_RISK_MANIFEST
    assert m["stimulus_status"] == "read" and m["stimulus_result"] == "read"
    assert {m["stimulus_start"], m["stimulus_set_phase"], m["stimulus_stop"], m["sim_vehicle_pedal"]} == {"config"}

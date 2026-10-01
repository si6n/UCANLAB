"""Workbench B2 (Grafik): decoded-signal plot rings and their read endpoints."""

from __future__ import annotations

import math
from typing import Any

import pytest

from src.engine.connection.simulated_vehicle import SimulatedVehicleBus


def _app() -> Any:
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._reconnect_bus("virtual", "plot_test", 250000)
    return app


def _pump(app: Any, n: int) -> None:
    for _ in range(n):
        app._ingest_live_frame(app.bus.recv(timeout_s=0.0))


def test_simulated_truck_signals_are_plottable_and_marked_simulated() -> None:
    app = _app()
    assert app.start_simulated_vehicle("truck")["success"]
    _pump(app, 1200)
    listed = {s["name"]: s for s in app.plot_signal_list()["signals"]}
    assert {"EngineSpeed", "EngineCoolantTemp"} <= set(listed)
    assert all(s["simulated"] for s in listed.values())
    assert listed["EngineSpeed"]["unit"] == "rpm"
    series = app.plot_signal_series(["EngineSpeed"], 30)["series"]["EngineSpeed"]
    assert series["simulated"] is True and len(series["t"]) == len(series["v"]) > 10
    assert all(t <= 0 for t in series["t"])
    # The animated simulator idles around 650 rpm (~±52): the plot is not a flat line.
    assert 590 <= min(series["v"]) < max(series["v"]) <= 710
    # And none of it became diagnostic evidence.
    assert app._diag_session.samples == [] and app._diag_session.events == []


def test_real_bus_values_are_not_marked_simulated() -> None:
    app = _app()
    app._record_signal_sample("EngineSpeed", 4800, 600.0, "rpm")
    (sig,) = app.plot_signal_list()["signals"]
    assert sig["simulated"] is False and sig["last"] == 600.0


def test_non_numeric_and_non_finite_values_are_not_plotted() -> None:
    app = _app()
    app._record_plot_point("TransmissionGear_0", "forward", "enum", 1.0)
    app._record_plot_point("FluidLevel_fuel_0", None, "percent", 1.0)
    app._record_plot_point("Bad", math.nan, "", 1.0)
    app._record_plot_point("Inf", math.inf, "", 1.0)
    assert app.plot_signal_list()["signals"] == []


def test_series_is_bounded_and_keeps_the_newest_point() -> None:
    app = _app()
    for i in range(5000):
        app._record_plot_point("EngineSpeed", float(i), "rpm", 1.0)
    out = app.plot_signal_series(["EngineSpeed"], 120)["series"]["EngineSpeed"]
    assert len(out["v"]) <= app._PLOT_SERIES_MAX_POINTS + 1
    assert out["v"][-1] == 4999.0


def test_ring_and_signal_count_are_capped() -> None:
    app = _app()
    for i in range(app._PLOT_RING_MAX + 50):
        app._record_plot_point("A", float(i), "", 1.0)
    assert len(app._plot_rings["A"]) == app._PLOT_RING_MAX
    for i in range(app._PLOT_SIGNALS_MAX + 10):
        app._record_plot_point(f"S{i}", 1.0, "", 1.0)
    assert len(app._plot_rings) == app._PLOT_SIGNALS_MAX


@pytest.mark.parametrize(
    ("names", "window", "code"),
    [("EngineSpeed", 30, "INVALID_NAMES"), ([1, 2], 30, "INVALID_NAMES"), (["A"], "x", "INVALID_WINDOW"), (["A"], math.nan, "INVALID_WINDOW")],
)
def test_series_rejects_bad_input(names: Any, window: Any, code: str) -> None:
    assert _app().plot_signal_series(names, window) == {"success": False, "error_code": code}


def test_series_clamps_window_and_name_count() -> None:
    app = _app()
    for i in range(12):
        app._record_plot_point(f"S{i}", 1.0, "", 1.0)
    out = app.plot_signal_series([f"S{i}" for i in range(12)], 10_000)
    assert out["window_s"] == 120.0 and len(out["series"]) == app._PLOT_SERIES_MAX_NAMES
    assert app.plot_signal_series(["unknown"], 30)["series"] == {}


def test_bus_switch_clears_the_plot() -> None:
    """Simulated and real curves never share a chart: a channel switch starts empty."""
    app = _app()
    app.start_simulated_vehicle("truck")
    _pump(app, 300)
    assert app.plot_signal_list()["signals"]
    app.stop_simulated_vehicle()
    assert app.plot_signal_list()["signals"] == []


def test_connection_wizard_simulator_stays_static() -> None:
    """Only the workbench animates; the wizard's expectations see fixed bytes."""
    static = SimulatedVehicleBus("truck", 250000, sleep=False)
    static.connect()
    payloads: dict[int, set[bytes]] = {}
    for _ in range(700):
        frame = static.recv(timeout_s=0.0)
        payloads.setdefault(frame.arbitration_id, set()).add(bytes(frame.data))
    assert payloads and all(len(p) == 1 for p in payloads.values())


def test_bridge_exposes_plot_endpoints_as_read() -> None:
    from src.ui.desktop_app import DesktopApiBridge

    manifest = DesktopApiBridge.BRIDGE_RISK_MANIFEST
    assert manifest["plot_signal_list"] == "read" and manifest["plot_signal_series"] == "read"
    bridge = DesktopApiBridge(_app())
    assert bridge.plot_signal_list() == {"success": True, "signals": []}
    assert bridge.plot_signal_series(["x"], 30)["success"] is True

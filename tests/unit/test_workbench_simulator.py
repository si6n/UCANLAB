"""Engineer workbench (B1): the simulated vehicle as the app bus, and bus info.

The old engineer screen generated packets in the browser. The workbench
instead listens to the listen-only simulated vehicle through the real ingest
path, so every frame it shows carries ``source="synthetic"`` and nothing is
ever transmitted.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.engine.connection.simulated_vehicle import NATIVE_BITRATE, SimulatedVehicleBus


def _app() -> Any:
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._reconnect_bus("virtual", "wb_test", 250000)
    return app


@pytest.mark.parametrize("vehicle_type", sorted(NATIVE_BITRATE))
def test_start_binds_listen_only_simulated_vehicle(vehicle_type: str) -> None:
    app = _app()
    res = app.start_simulated_vehicle(vehicle_type)
    assert res["success"] is True, res
    assert isinstance(app.bus, SimulatedVehicleBus)
    assert res["simulated"] is True and res["vehicle_type"] == vehicle_type
    assert res["bitrate"] == NATIVE_BITRATE[vehicle_type] and res["listen_only"] is True
    assert app.gateway._bus is app.bus  # gateway rebound, same swap as a reconnect
    frame = app.bus.recv(timeout_s=0.0)
    assert frame is not None and frame.source == "synthetic"
    with pytest.raises(SafetyError):
        app.bus.send(CanFrame(channel_id="sim_vehicle", arbitration_id=0x7DF, dlc=8, data=bytes(8)))
    assert not app.supervisor.is_tx_permitted


def test_stop_restores_the_previous_adapter() -> None:
    app = _app()
    before = (app.interface_val, app.channel_name, app.bitrate_val)
    assert app.start_simulated_vehicle("car")["success"]
    res = app.stop_simulated_vehicle()
    assert res["success"] is True and res["simulated"] is False
    assert (app.interface_val, app.channel_name, app.bitrate_val) == before
    assert not isinstance(app.bus, SimulatedVehicleBus)
    # Stopping twice is a no-op, not an error.
    assert app.stop_simulated_vehicle()["simulated"] is False


def test_start_twice_keeps_the_running_simulator() -> None:
    app = _app()
    assert app.start_simulated_vehicle("truck")["success"]
    bus = app.bus
    res = app.start_simulated_vehicle("car")
    assert res["success"] is True and app.bus is bus and res["vehicle_type"] == "truck"


def test_unknown_vehicle_type_is_refused() -> None:
    app = _app()
    bus = app.bus
    assert app.start_simulated_vehicle("spaceship") == {"success": False, "error_code": "INVALID_VEHICLE_TYPE"}
    assert app.bus is bus


def test_refused_while_estop_latched_so_the_real_bus_keeps_recording() -> None:
    app = _app()
    bus = app.bus
    app.trigger_estop()
    assert app.start_simulated_vehicle("car") == {"success": False, "error_code": "ESTOP_ENGAGED"}
    assert app.bus is bus


def test_refused_while_tx_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    bus = app.bus
    monkeypatch.setattr(type(app.supervisor), "is_tx_permitted", property(lambda self: True))
    assert app.start_simulated_vehicle("car") == {"success": False, "error_code": "TX_ARMED"}
    assert app.bus is bus


def test_bus_info_reports_driver_counters_not_dtc_count() -> None:
    app = _app()
    app._error_count = 3  # DM1 DTC count lands here; must not read as error frames
    info = app.bus_info()
    assert info["simulated"] is False and info["vehicle_type"] is None
    assert info["error_frames"] == 0
    app.bus.metrics.error_frames = 5
    assert app.bus_info()["error_frames"] == 5


def test_bus_short_scenario_surfaces_bus_off_in_bus_info() -> None:
    app = _app()
    app.start_simulated_vehicle("truck")
    sim = app.bus
    sim.scenario = "bus_short"
    for _ in range(40):
        sim.recv(timeout_s=0.0)
    info = app.bus_info()
    assert info["error_frames"] > 32 and info["bus_state"] == "bus_off"


def test_bridge_methods_and_risk_classes() -> None:
    from src.ui.desktop_app import DesktopApiBridge

    manifest = DesktopApiBridge.BRIDGE_RISK_MANIFEST
    assert manifest["sim_vehicle_start"] == "config"
    assert manifest["sim_vehicle_stop"] == "config"
    assert manifest["bus_get_info"] == "read"
    bridge = DesktopApiBridge(_app())
    assert bridge.sim_vehicle_start(123) == {"success": False, "error_code": "INVALID_VEHICLE_TYPE"}  # type: ignore[arg-type]
    started = bridge.sim_vehicle_start(" Car ")
    assert started["success"] and started["vehicle_type"] == "car"
    assert bridge.bus_get_info()["simulated"] is True
    assert bridge.sim_vehicle_stop()["simulated"] is False


def test_failed_install_keeps_the_old_bus() -> None:
    app = _app()
    old = app.bus

    class Broken(SimulatedVehicleBus):
        def connect(self) -> None:
            raise OSError("adapter vanished")

    old.connect()
    with app._bus_lock:
        assert app._install_bus_locked(Broken("car", 500000), "simulator", "sim_vehicle", 500000) is False
    assert app.bus is old

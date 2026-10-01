"""Workbench B6 (Ayarlar → Bağlantı): adapter binding stays listen-only and fail-safe."""

from __future__ import annotations

from typing import Any

import pytest

from src.engine.connection.adapters import READY, SIMULATOR, AdapterInfo
from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp

# A "physical" adapter backed by python-can's in-process virtual interface.
FAKE = AdapterInfo(kind="pcan", interface="virtual", channel="wb_settings_hw", label="PCAN-USB", status=READY)


@pytest.fixture
def app() -> Any:
    application = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    application._reconnect_bus("virtual", "wb_settings_idle", 250000)
    application._last_adapters = {"pcan:wb_settings_hw": FAKE, "simulator:sim0": SIMULATOR}
    application.connection_wizard._window_s = 0.2
    return application


def test_manual_connect_binds_listen_only_at_the_chosen_bitrate(app: Any) -> None:
    res = app.workbench_bus_connect("pcan:wb_settings_hw", 500_000)
    assert res["success"] is True, res
    assert res["interface"] == "virtual" and res["channel"] == "wb_settings_hw" and res["bitrate"] == 500_000
    assert res["listen_only"] is True and app.bus.listen_only is True
    assert not app.supervisor.is_tx_permitted


def test_manual_connect_replaces_the_simulator_cleanly(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    assert app.bus_info()["simulated"] is True
    assert app.workbench_bus_connect("pcan:wb_settings_hw", 250_000)["success"] is True
    assert app.bus_info()["simulated"] is False
    assert app._sim_diag_session is None and app._pre_simulator_bus is None


@pytest.mark.parametrize(
    ("adapter_id", "bitrate", "code"),
    [
        ("pcan:unknown", 250_000, "ADAPTER_UNKNOWN"),
        ("simulator:sim0", 250_000, "ADAPTER_UNKNOWN"),  # the workbench has its own simulator switch
        (None, 250_000, "ADAPTER_UNKNOWN"),
        ("pcan:wb_settings_hw", 333_000, "INVALID_BITRATE"),
        ("pcan:wb_settings_hw", True, "INVALID_BITRATE"),
        ("pcan:wb_settings_hw", "500000", "INVALID_BITRATE"),
    ],
)
def test_manual_connect_validates_inputs(app: Any, adapter_id: Any, bitrate: Any, code: str) -> None:
    before = app.bus_info()
    res = app.workbench_bus_connect(adapter_id, bitrate)
    assert res == {"success": False, "error_code": code}
    assert app.bus_info() == before


def test_channel_is_not_switched_under_estop_or_armed_tx(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    app._is_estop = True
    assert app.workbench_bus_connect("pcan:wb_settings_hw", 250_000)["error_code"] == "ESTOP_ENGAGED"
    assert app.workbench_connection_test_start("pcan:wb_settings_hw", "truck")["error_code"] == "ESTOP_ENGAGED"
    app._is_estop = False
    monkeypatch.setattr(type(app.supervisor), "is_tx_permitted", property(lambda self: True))
    assert app.workbench_bus_connect("pcan:wb_settings_hw", 250_000)["error_code"] == "TX_ARMED"
    assert app.bus_info()["channel"] == "wb_settings_idle"


def test_failed_connect_is_reported_not_claimed(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_reconnect_bus", lambda *a, **k: None)  # driver rejected the channel
    res = app.workbench_bus_connect("pcan:wb_settings_hw", 250_000)
    assert res["success"] is False and res["error_code"] == "CONNECT_FAILED"
    assert res["channel"] == "wb_settings_idle"


def test_listen_test_uses_the_chosen_vehicle_type(app: Any) -> None:
    assert app.workbench_connection_test_start("pcan:wb_settings_hw", "spaceship")["error_code"] == "INVALID_VEHICLE_TYPE"
    assert app.workbench_connection_test_start("pcan:wb_settings_hw", 7)["error_code"] == "INVALID_VEHICLE_TYPE"
    res = app.workbench_connection_test_start("pcan:wb_settings_hw", "boat")
    assert res["success"] is True
    assert app.connection_wizard.status()["vehicle_type"] == "boat"
    app.connection_wizard.wait(10)
    status = app.connection_wizard.status()
    # Nothing talks on the virtual channel: honest "no traffic", nothing committed.
    assert status["state"] == "done" and status["result"]["usable"] is False
    assert app.bus_info()["channel"] != "wb_settings_hw"
    assert not app.supervisor.is_tx_permitted


def test_bridge_methods_are_config_risk() -> None:
    manifest = DesktopApiBridge.BRIDGE_RISK_MANIFEST
    assert manifest["workbench_connection_test_start"] == "config"
    assert manifest["workbench_bus_connect"] == "config"


def test_listen_test_commits_the_bus_when_the_vehicle_talks(app: Any) -> None:
    import threading
    import time

    import can

    from src.engine.connection.simulated_vehicle import SimulatedVehicleBus

    sim = SimulatedVehicleBus("truck", 250_000, sleep=False)
    sim.connect()
    talker = can.Bus(interface="virtual", channel="wb_settings_hw", receive_own_messages=False)
    stop = threading.Event()

    def talk() -> None:
        while not stop.is_set():
            f = sim.recv(timeout_s=0.0)
            talker.send(can.Message(arbitration_id=f.arbitration_id, data=f.data, is_extended_id=f.is_extended))
            time.sleep(0.001)

    t = threading.Thread(target=talk, daemon=True)
    t.start()
    try:
        assert app.workbench_connection_test_start("pcan:wb_settings_hw", "truck")["success"] is True
        app.connection_wizard.wait(15)
    finally:
        stop.set()
        t.join(2)
        talker.shutdown()
    result = app.connection_wizard.status()["result"]
    assert result["usable"] is True and result["bitrate"] == 250_000, result
    info = app.bus_info()
    assert info["channel"] == "wb_settings_hw" and info["bitrate"] == 250_000 and info["listen_only"] is True
    assert not app.supervisor.is_tx_permitted


def test_simulator_does_not_start_while_a_listen_test_runs(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.connection_wizard, "status", lambda: {"state": "running"})
    assert app.start_simulated_vehicle("truck") == {"success": False, "error_code": "TEST_RUNNING"}
    assert app.workbench_bus_connect("pcan:wb_settings_hw", 250_000)["error_code"] == "TEST_RUNNING"
    assert app.bus_info()["simulated"] is False

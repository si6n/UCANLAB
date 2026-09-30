"""Aşama 5: adapter discovery, listen-only connection test, simulator, wizard, bridge."""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.core.errors import HardwareError, SafetyError
from src.core.models.can_frame import CanFrame
from src.engine.connection import adapters as ad
from src.engine.connection.listen_test import (
    MESSAGES,
    BusListenChannel,
    ExpectedPgn,
    battery_is_weak,
    battery_volts_from_vep1,
    run_listen_test,
)
from src.engine.connection.simulated_vehicle import SCENARIOS, SimulatedVehicleBus
from src.engine.connection.wizard import ConnectionWizard
from src.engine.vehicle.profiles import default_catalog
from src.hal.base import AbstractBus, BusState
from src.ui.mechanic_prefs import MechanicPrefsStore

WINDOW = 0.03


def _vtype(type_id: str):
    vtype = default_catalog().type_by_id(type_id)
    assert vtype is not None
    return vtype


def _expected(type_id: str) -> list[ExpectedPgn]:
    return [ExpectedPgn(e.pgn, e.name_tr, e.name_en) for e in _vtype(type_id).expected_traffic]


def _sim_channel(type_id: str, scenario: str = "ok") -> BusListenChannel:
    return BusListenChannel(lambda bitrate: SimulatedVehicleBus(type_id, bitrate, scenario, sleep=False))


# ---------------------------------------------------------------------------
# Adapter discovery
# ---------------------------------------------------------------------------


def test_discovery_orders_usable_first_and_simulator_last() -> None:
    probes = (
        ("kvaser", lambda: [ad._driver_missing("kvaser", "Kvaser")]),
        ("pcan", lambda: [ad.AdapterInfo("pcan", "pcan", "PCAN_USBBUS1", "PCAN-USB", ad.READY)]),
        ("rp1210", lambda: [ad.AdapterInfo("rp1210", "rp1210", "1", "RP1210", ad.DRIVER_READY)]),
    )
    found = ad.discover_adapters(probes)
    assert [a.kind for a in found] == ["pcan", "rp1210", "kvaser", "simulator"]
    assert found[2].message_tr.startswith("Kvaser adaptörü için sürücü kurulu değil")
    assert [a.usable for a in found] == [True, True, False, True]


def test_discovery_isolates_a_crashing_probe() -> None:
    def boom() -> list[ad.AdapterInfo]:
        raise RuntimeError("vendor dll crashed")

    found = ad.discover_adapters((("pcan", boom),))
    assert found[0].status == ad.PROBE_FAILED and found[0].message_tr
    assert found[-1] is ad.SIMULATOR


def test_pcan_driver_missing_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import can.interfaces.pcan.basic as basic

    def no_dll() -> None:
        raise OSError("PCANBasic.dll not found")

    monkeypatch.setattr(basic, "PCANBasic", no_dll)
    monkeypatch.setattr(ad.sys, "platform", "win32")
    [missing] = ad.probe_pcan()
    assert missing.status == ad.DRIVER_MISSING and "peak-system.com" in missing.message_tr
    monkeypatch.setattr(ad.sys, "platform", "linux")
    assert ad.probe_pcan() == []


def test_kvaser_driver_missing_and_virtual_channels_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    from can.interfaces.kvaser import canlib

    monkeypatch.setattr(ad.sys, "platform", "win32")
    monkeypatch.setattr(canlib, "__canlib", None, raising=False)
    [missing] = ad.probe_kvaser()
    assert missing.status == ad.DRIVER_MISSING and "kvaser.com" in missing.message_tr

    monkeypatch.setattr(canlib, "__canlib", object(), raising=False)
    monkeypatch.setattr(canlib.KvaserBus, "_detect_available_configs", staticmethod(lambda: [
        {"channel": 0, "device_name": "Kvaser Leaf Light v2"},
        {"channel": 1, "device_name": "Kvaser Virtual CAN Driver"},
    ]))
    assert [a.label for a in ad.probe_kvaser()] == ["Kvaser Leaf Light v2"]


def test_rp1210_lists_ini_vendors_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.hal.rp1210.client as client

    monkeypatch.setattr(client, "_declared_vendor_dlls", lambda: frozenset({"NULN2R32", "DGDPA5MA"}))
    monkeypatch.setattr(ad.sys, "platform", "win32")
    [vci] = ad.probe_rp1210()
    assert vci.status == ad.DRIVER_READY and vci.channel == "1" and "NULN2R32" in vci.label
    monkeypatch.setattr(client, "_declared_vendor_dlls", lambda: frozenset())
    assert ad.probe_rp1210() == []


# ---------------------------------------------------------------------------
# Listen-only test on the simulated vehicle (every §4 error path)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_id", "scenario", "code", "bitrate"),
    [
        ("truck", "ok", "READY", 250_000),
        ("truck", "ignition_off", "NO_TRAFFIC", None),
        ("truck", "wrong_socket", "EXPECTED_MISSING", 250_000),
        ("truck", "bus_short", "BUS_ERROR", None),
        ("truck", "no_listen_only", "LISTEN_ONLY_UNAVAILABLE", None),
        ("boat", "ok", "READY", 250_000),
        ("construction", "ok", "READY", 250_000),
        ("car", "ok", "READY", 500_000),
        ("car", "ignition_off", "QUIET_VEHICLE", 500_000),
    ],
)
def test_listen_test_outcomes(type_id: str, scenario: str, code: str, bitrate: int | None) -> None:
    result = run_listen_test(_sim_channel(type_id, scenario), _vtype(type_id).bitrate_candidates,
                             _expected(type_id), window_s=WINDOW)
    assert (result.code, result.bitrate) == (code, bitrate)
    payload = result.as_dict()
    assert payload["message_tr"] and payload["message_en"]


def test_wrong_bitrate_is_scanned_past_then_reported() -> None:
    # Truck candidates are [250k, 500k]; a simulated car (500k) is found on the second try.
    result = run_listen_test(_sim_channel("car"), [250_000, 500_000], window_s=WINDOW)
    assert result.code == "READY" and result.bitrate == 500_000
    assert result.attempts[0].error_frames > 0 and result.attempts[0].frames == 0
    only_wrong = run_listen_test(_sim_channel("car"), [250_000], window_s=WINDOW)
    assert only_wrong.code == "WRONG_BITRATE"


def test_truck_reports_ecus_and_battery() -> None:
    ok = run_listen_test(_sim_channel("truck"), [250_000], _expected("truck"), window_s=WINDOW)
    assert ok.ecu_count == 3 and ok.battery_volts == 27.6 and not ok.battery_warning
    assert all(e["seen"] for e in ok.expected)
    weak = run_listen_test(_sim_channel("truck", "weak_battery"), [250_000], _expected("truck"), window_s=WINDOW)
    assert weak.battery_warning and "22.9" in weak.as_dict()["battery_message_tr"]


def test_battery_decoding_and_thresholds() -> None:
    assert battery_volts_from_vep1(bytes([0xFF] * 4 + [0x28, 0x02, 0xFF, 0xFF])) == 27.6
    assert battery_volts_from_vep1(bytes([0xFF] * 8)) is None  # not available
    assert battery_volts_from_vep1(bytes([0xFF] * 4 + [0x10, 0x00])) is None  # 0.8 V: implausible
    assert battery_volts_from_vep1(b"\x00") is None
    assert battery_is_weak(11.2) and not battery_is_weak(12.4)
    assert battery_is_weak(23.0) and not battery_is_weak(25.2)


def test_every_outcome_has_both_languages() -> None:
    assert all(tr and en for tr, en in MESSAGES.values())


def test_cancel_stops_the_scan() -> None:
    result = run_listen_test(_sim_channel("truck"), [250_000, 500_000], window_s=WINDOW, cancelled=lambda: True)
    assert result.code == "CANCELLED"


# ---------------------------------------------------------------------------
# Safety: nothing is ever transmitted, non-listen-only buses are refused
# ---------------------------------------------------------------------------


class _SpyBus(AbstractBus):
    def __init__(self, *, listen_only: bool = True, state: BusState = BusState.PASSIVE,
                 connect_error: Exception | None = None) -> None:
        super().__init__(channel_id="spy", bitrate=250_000)
        self.listen_only = listen_only
        self._state = state
        self._connect_error = connect_error
        self.sent: list[CanFrame] = []
        self.connected = self.disconnected = 0
        self._ids = itertools.count()

    def connect(self) -> None:
        if self._connect_error is not None:
            raise self._connect_error
        self.connected += 1
        self.is_connected = True
        self.metrics.state = self._state

    def disconnect(self) -> None:
        self.disconnected += 1
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:
        self.sent.append(frame)

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        return CanFrame(channel_id="spy", arbitration_id=0x18FEF100 | (next(self._ids) % 3), dlc=8,
                        data=bytes(8), is_extended=True)


def test_listen_test_never_transmits() -> None:
    bus = _SpyBus()
    result = run_listen_test(BusListenChannel(lambda b: bus), [250_000], window_s=WINDOW)
    assert result.code == "READY" and bus.sent == [] and bus.disconnected == 1


def test_bus_without_listen_only_flag_is_never_opened() -> None:
    bus = _SpyBus(listen_only=False)
    result = run_listen_test(BusListenChannel(lambda b: bus), [250_000], window_s=WINDOW)
    assert result.code == "LISTEN_ONLY_UNAVAILABLE" and bus.connected == 0


def test_bus_that_connects_active_is_closed_and_refused() -> None:
    bus = _SpyBus(state=BusState.ACTIVE)
    result = run_listen_test(BusListenChannel(lambda b: bus), [250_000], window_s=WINDOW)
    assert result.code == "LISTEN_ONLY_UNAVAILABLE" and bus.disconnected == 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (HardwareError("no listen only", code="HARDWARE_LISTEN_ONLY_UNSUPPORTED"), "LISTEN_ONLY_UNAVAILABLE"),
        (HardwareError("PCAN_ERROR_INITIALIZE"), "ADAPTER_ERROR"),
        (OSError("device gone"), "ADAPTER_ERROR"),
    ],
)
def test_adapter_open_errors_map_to_plain_messages(error: Exception, code: str) -> None:
    bus = _SpyBus(connect_error=error)
    assert run_listen_test(BusListenChannel(lambda b: bus), [250_000], window_s=WINDOW).code == code


def test_simulated_vehicle_refuses_to_send() -> None:
    bus = SimulatedVehicleBus("truck", 250_000)
    with pytest.raises(SafetyError):
        bus.send(CanFrame(channel_id="x", arbitration_id=0x7DF, dlc=8, data=bytes(8)))
    with pytest.raises(ValueError):
        SimulatedVehicleBus("truck", 250_000, "exploding")
    assert set(SCENARIOS) >= {"ok", "ignition_off", "wrong_socket", "weak_battery", "bus_short"}


def test_simulated_frames_are_marked_synthetic() -> None:
    bus = SimulatedVehicleBus("truck", 250_000, sleep=False)
    bus.connect()
    frame = bus.recv()
    assert frame is not None and frame.source == "synthetic"


# ---------------------------------------------------------------------------
# Wizard runner
# ---------------------------------------------------------------------------


def _wizard(factory: Any = None, **kwargs: Any) -> tuple[ConnectionWizard, list[Any]]:
    calls: list[Any] = []
    wizard = ConnectionWizard(
        factory or (lambda i, c, b: _SpyBus()),
        park_app_bus=lambda: calls.append("park"),
        commit_app_bus=lambda adapter, bitrate: calls.append(("commit", adapter.kind, bitrate)),
        window_s=WINDOW, simulator_sleep=False, **kwargs,
    )
    return wizard, calls


def test_wizard_simulator_leaves_app_bus_alone() -> None:
    wizard, calls = _wizard()
    wizard.start(ad.SIMULATOR, _vtype("truck"))
    wizard.wait()
    status = wizard.status()
    assert status["state"] == "done" and status["result"]["code"] == "READY"
    assert calls == [] and wizard.connection is not None and wizard.connection.simulator


def test_wizard_real_adapter_parks_then_commits() -> None:
    wizard, calls = _wizard()
    pcan = ad.AdapterInfo("pcan", "pcan", "PCAN_USBBUS1", "PCAN-USB", ad.READY)
    wizard.start(pcan, _vtype("truck"))
    wizard.wait()
    assert calls == ["park", ("commit", "pcan", 250_000)]


def test_wizard_failed_test_does_not_commit() -> None:
    wizard, calls = _wizard(lambda i, c, b: _SpyBus(connect_error=HardwareError("gone")))
    pcan = ad.AdapterInfo("pcan", "pcan", "PCAN_USBBUS1", "PCAN-USB", ad.READY)
    wizard.start(pcan, _vtype("truck"))
    wizard.wait()
    assert calls == ["park"] and wizard.status()["result"]["code"] == "ADAPTER_ERROR"
    assert wizard.connection is None


def test_wizard_rejects_a_second_concurrent_test() -> None:
    wizard = ConnectionWizard(lambda i, c, b: _SpyBus(), window_s=0.5, simulator_sleep=False)
    wizard.start(ad.SIMULATOR, _vtype("truck"))
    with pytest.raises(RuntimeError):
        wizard.start(ad.SIMULATOR, _vtype("truck"))
    wizard.cancel()
    wizard.wait()
    assert wizard.status()["result"]["code"] == "CANCELLED"


def test_wizard_unexpected_error_is_reported_not_raised() -> None:
    def broken(i: str, c: str, b: int) -> AbstractBus:
        raise KeyError("boom")

    wizard, _ = _wizard(broken)
    pcan = ad.AdapterInfo("pcan", "pcan", "PCAN_USBBUS1", "PCAN-USB", ad.READY)
    wizard.start(pcan, _vtype("truck"))
    wizard.wait()
    assert wizard.status()["result"]["code"] == "ADAPTER_ERROR"


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


def _bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import src.ui.desktop_app as da

    monkeypatch.setattr(da, "discover_adapters", lambda: [
        ad.AdapterInfo("pcan", "pcan", "", "PCAN-USB", ad.DRIVER_MISSING), ad.SIMULATOR,
    ])
    app = SimpleNamespace(
        mechanic_prefs=MechanicPrefsStore(tmp_path / "prefs.json"),
        connection_wizard=ConnectionWizard(lambda i, c, b: _SpyBus(), window_s=WINDOW, simulator_sleep=False),
    )
    return da.DesktopApiBridge(app), app  # type: ignore[arg-type]


def test_bridge_scan_and_simulator_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge, app = _bridge(tmp_path, monkeypatch)
    scan = bridge.adapter_scan()
    assert [a["id"] for a in scan["adapters"]] == ["pcan:", "simulator:sim0"]

    assert bridge.connection_test_start("simulator:sim0")["error_code"] == "NO_VEHICLE_SELECTED"
    app.mechanic_prefs.update(vehicle_profile_id="truck_scania")
    assert bridge.connection_test_start("pcan:")["error_code"] == "ADAPTER_UNKNOWN"  # driver missing
    assert bridge.connection_test_start("nope")["error_code"] == "ADAPTER_UNKNOWN"
    assert bridge.connection_test_start("simulator:sim0", "melt")["error_code"] == "INVALID_SCENARIO"

    started = bridge.connection_test_start("simulator:sim0", "wrong_socket")
    assert started["success"]
    app.connection_wizard.wait()
    status = bridge.connection_test_status()
    assert status["state"] == "done" and status["result"]["code"] == "EXPECTED_MISSING"
    assert bridge.connection_test_cancel() == {"success": True}

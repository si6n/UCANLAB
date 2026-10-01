"""Aşama 7: the mechanic flow end to end on the real app object, simulator only.

Drives ``DesktopApiBridge`` exactly as the React screens do — mode → vehicle →
adapter → listen-only connection test → scan → result → customer report —
against the real ``UniversalCanDesktopApp`` (real gateway, supervisor, analysis
pipeline, knowledge bases). No hardware, no Windows.

Safety invariant checked on every path: the simulator flow never arms TX and
never puts a frame through the TX gateway.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from src.safety.state_machine import SafetyState
from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp
from src.ui.mechanic_prefs import MechanicPrefsStore


@pytest.fixture
def flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import src.ui.desktop_app as da

    monkeypatch.setattr(da, "_app_data_root", lambda: tmp_path)
    monkeypatch.setattr(DesktopApiBridge, "SCAN_LISTEN_S_SIMULATOR", 0.5)
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app.mechanic_prefs = MechanicPrefsStore(tmp_path / "mechanic_prefs.json")
    app.connection_wizard._window_s = 0.2
    sent: list[Any] = []
    original = app.gateway.validate_and_transmit

    def spy(frame: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(frame)
        return original(frame, *args, **kwargs)

    monkeypatch.setattr(app.gateway, "validate_and_transmit", spy)
    bridge = DesktopApiBridge(app)
    yield bridge, app, sent
    assert app.supervisor.current_state != SafetyState.ARMED_TX
    assert not app.supervisor.is_tx_permitted


def _wait(fn: Any, timeout_s: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = fn()
        if status.get("state") == "done":
            return status
        time.sleep(0.05)
    raise AssertionError(f"timed out: {status}")


def _connect(bridge: DesktopApiBridge, profile_id: str, scenario: str | None = None) -> dict[str, Any]:
    assert bridge.mechanic_set_mode("mechanic")["success"]
    assert bridge.vehicle_select(profile_id)["success"]
    adapters = bridge.adapter_scan()["adapters"]
    assert adapters[-1]["id"] == "simulator:sim0"
    assert bridge.connection_test_start("simulator:sim0", scenario)["success"]
    return _wait(bridge.connection_test_status)["result"]


def _scan(bridge: DesktopApiBridge, allow_read: bool = False) -> dict[str, Any]:
    started = bridge.scan_start(allow_read)
    assert started["success"], started
    status = _wait(bridge.scan_status, timeout_s=60.0)
    assert status["step"] == "done", status
    return status


def test_truck_happy_path(flow: Any) -> None:
    bridge, app, sent = flow
    connection = _connect(bridge, "truck_scania")
    assert connection["code"] == "READY" and connection["ecu_count"] == 3 and connection["bitrate"] == 250000
    assert bridge.vehicle_check_identity()["status"] in ("unknown", "match")

    status = _scan(bridge)
    result = status["result"]
    assert [c["code"] for c in result["technical"]["codes"]] == ["SPN 3251 FMI 0"]
    assert result["headline_tr"] and result["urgency_tr"] in ("Hemen", "Bu hafta", "Bir sonraki bakımda", "Bilinmiyor")
    assert result["steps"] and result["steps"][0]["action_tr"]
    assert any("simülatör" in line for line in result["missing_tr"])
    assert "kesin" not in status["report_text"].lower()

    saved = bridge.scan_save_report("Usta Oto")
    assert saved["success"] and Path(saved["path"]).is_file()
    assert sent == []  # listening only: nothing went to the TX gateway


def test_car_read_with_consent_uses_simulated_ecu(flow: Any) -> None:
    bridge, _app, sent = flow
    connection = _connect(bridge, "car_toyota")
    assert connection["code"] == "READY" and connection["bitrate"] == 500000
    status = _scan(bridge, allow_read=True)
    codes = {(c["code"], c["kind"]) for c in status["result"]["technical"]["codes"]}
    assert codes == {("P0301", "stored"), ("P0420", "stored"), ("P0171", "pending")}
    assert sent == []  # the simulator car answers without the app's TX gateway


def test_car_listen_only_says_codes_were_not_read(flow: Any) -> None:
    bridge, _app, sent = flow
    _connect(bridge, "car_generic")
    result = _scan(bridge, allow_read=False)["result"]
    assert result["technical"]["codes"] == []
    assert result["headline_tr"] == "Kayıtlı arıza kodu bulunamadı"
    assert any("okuma izni verilmedi" in line for line in result["missing_tr"])
    assert sent == []


def test_boat_has_no_codes_and_says_so(flow: Any) -> None:
    bridge, _app, _sent = flow
    assert _connect(bridge, "boat_n2k")["code"] == "READY"
    result = _scan(bridge)["result"]
    assert result["risk_level"] == "GRAY" and "anlamına gelmez" in result["summary_tr"]


@pytest.mark.parametrize(
    ("profile", "scenario", "code", "usable"),
    [
        ("truck_scania", "ignition_off", "NO_TRAFFIC", False),
        ("truck_scania", "wrong_socket", "EXPECTED_MISSING", True),
        ("truck_volvo", "bus_short", "BUS_ERROR", False),
        ("truck_generic", "no_listen_only", "LISTEN_ONLY_UNAVAILABLE", False),
        ("car_generic", "ignition_off", "QUIET_VEHICLE", True),
        ("construction_caterpillar", "weak_battery", "READY", True),
    ],
)
def test_connection_error_paths(flow: Any, profile: str, scenario: str, code: str, usable: bool) -> None:
    bridge, _app, sent = flow
    result = _connect(bridge, profile, scenario)
    assert (result["code"], result["usable"]) == (code, usable)
    assert result["message_tr"]
    if scenario == "weak_battery":
        assert result["battery_warning"] and "Akü zayıf" in result["battery_message_tr"]
    if not usable:
        assert bridge.scan_start()["error_code"] == "NOT_CONNECTED"
    assert sent == []


def test_weak_battery_warning_reaches_the_result_first(flow: Any) -> None:
    bridge, _app, _sent = flow
    _connect(bridge, "construction_caterpillar", "weak_battery")
    result = _scan(bridge)["result"]
    assert result["safety_tr"] and "Akü zayıf" in result["safety_tr"][0]


def test_high_voltage_vehicle_warning(flow: Any) -> None:
    bridge, _app, _sent = flow
    _connect(bridge, "car_tesla")
    result = _scan(bridge, allow_read=True)["result"]
    assert result["safety_tr"][0].startswith("Yüksek voltajlı araç")


def test_unsupported_vehicle_cannot_start_the_flow(flow: Any) -> None:
    bridge, _app, _sent = flow
    assert bridge.vehicle_select("car_pre_obd")["error_code"] == "PROFILE_UNSUPPORTED"
    assert bridge.connection_test_start("simulator:sim0")["error_code"] in ("ADAPTER_UNKNOWN", "NO_VEHICLE_SELECTED")


def test_engineer_mode_needs_the_license(flow: Any) -> None:
    bridge, _app, _sent = flow
    assert bridge.mechanic_set_mode("engineer")["error_code"] == "ENGINEER_NOT_ALLOWED"

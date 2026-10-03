"""Operating-state scenario layer: the same reading means different things in different states."""

from __future__ import annotations

import pytest

from src.engine.ai.copilot_answer import answer_query
from src.engine.ai.operating_state import infer_state
from src.engine.ai.query_understanding import parse_query


@pytest.mark.parametrize("text,telemetry,engine,thermal", [
    ("", {"EngineSpeed": 0}, "off", "unknown"),
    ("", {"EngineSpeed": 250}, "cranking", "unknown"),
    ("", {"EngineSpeed": 750}, "idle", "unknown"),
    ("", {"EngineSpeed": 2500, "EngineLoad": 85}, "load", "unknown"),
    ("rölantide titriyor", {}, "idle", "unknown"),
    ("kontak kapalıyken ölçtüm", {}, "off", "unknown"),
    ("yokuşta gaza basınca çekmiyor", {}, "load", "unknown"),
    ("sabah ilk çalıştırmada", {}, "unknown", "cold"),
    ("", {"CoolantTemp": 90}, "unknown", "warm"),
    ("rölantide ama", {"EngineSpeed": 3000}, "running", "unknown"),   # a measured rpm beats the words
])
def test_state_from_readings_then_words(text: str, telemetry: dict, engine: str, thermal: str) -> None:
    st = infer_state(parse_query(text, telemetry=telemetry))
    assert (st.engine, st.thermal) == (engine, thermal)
    assert all(":" in s for s in st.sources)


def test_system_voltage_from_reading_or_text() -> None:
    assert infer_state(parse_query("", telemetry={"BatteryVoltage": 27.9})).system_voltage == 24
    assert infer_state(parse_query("", telemetry={"BatteryVoltage": 12.6})).system_voltage == 12
    assert infer_state(parse_query("24V sistem kamyon")).system_voltage == 24


@pytest.mark.parametrize("text,status", [
    ("motor çalışırken akü voltajı 12.3 V", "low"),          # alternator not charging
    ("kontak kapalı akü voltajı 12.3 V", "below_nominal"),   # weak but plausible at rest
    ("kontak kapalı akü voltajı 12.6 V", "normal"),
    ("motor çalışırken akü voltajı 14.1 V", "normal"),
    ("motor çalışırken akü voltajı 15.8 V", "critical_high"),
    ("marşta akü voltajı 9.2 V", "low"),
    ("akü voltajı 12.5 V", "needs_context"),                  # state unknown, value ambiguous
    ("akü voltajı 14.0 V", "normal"),                         # charging level proves the engine runs
])
def test_same_battery_voltage_is_judged_by_engine_state(text: str, status: str) -> None:
    d = answer_query(text, language="tr").to_dict()
    assert [(t["signal"], t["status"]) for t in d["technical"]["telemetry"]] == [("BatteryVoltage", status)]


def test_24v_system_uses_24v_bands() -> None:
    low = answer_query("", telemetry={"BatteryVoltage": 25.0, "EngineSpeed": 700}, language="tr").to_dict()
    ok = answer_query("", telemetry={"BatteryVoltage": 28.2, "EngineSpeed": 700}, language="tr").to_dict()
    assert low["technical"]["telemetry"][0]["status"] == "low"
    assert ok["technical"]["telemetry"][0]["status"] == "normal"
    assert "24 V" in ok["technical"]["telemetry"][0]["reference"]


def test_same_dpf_pressure_means_clogged_at_idle_but_not_at_full_load() -> None:
    idle = answer_query("rölantide dpf fark basıncı 8 kPa", language="tr").to_dict()
    load = answer_query("tam gazda dpf fark basıncı 8 kPa", language="tr").to_dict()
    assert idle["causes"][0]["kind"] == "scenario" and idle["causes"][0]["title"].startswith("DPF dolu")
    assert "scenario" not in {c["kind"] for c in load["causes"]}
    assert idle["technical"]["state"]["text"] == "rölantide"


def test_low_boost_is_a_fault_only_under_load() -> None:
    idle = answer_query("", telemetry={"BoostPressure": 1.1, "EngineSpeed": 750}, language="tr").to_dict()
    load = answer_query("yükte turbo basıncı 1.6 bar", language="tr").to_dict()
    assert idle["technical"]["telemetry"][0]["status"] == "normal"
    assert load["causes"][0]["title"].startswith("Yükte turbo basıncı düşük")


def test_overheating_scenario_depends_on_idle_or_load() -> None:
    idle = answer_query("rölantide su sıcaklığı 106 derece", language="tr").to_dict()
    load = answer_query("yükte su sıcaklığı 106 derece", language="tr").to_dict()
    assert idle["causes"][0]["title"].startswith("Rölantide hararet")
    assert load["causes"][0]["title"].startswith("Yükte hararet")


def test_cool_reading_contradicts_an_overheating_word() -> None:
    d = answer_query("motor sıcakken yolda giderken su sıcaklığı 62 derece", language="tr").to_dict()
    assert d["urgency"]["level"] != "RED"
    assert d["causes"][0]["title"].startswith("Termostat açık kalmış")


def test_surface_charge_is_a_note_not_a_verdict() -> None:
    d = answer_query("kontak kapalı akü 13.1 V", language="tr").to_dict()
    assert "scenario:surface-charge-reading" in [m["key"] for m in d["missing_data"]]
    assert d["causes"] == []


def test_oil_pressure_at_idle_uses_the_idle_band_without_rpm() -> None:
    d = answer_query("rölantide yağ basıncı 0.8 bar", language="tr").to_dict()
    assert d["technical"]["telemetry"][0]["status"] == "low"
    ok = answer_query("rölantide yağ basıncı 1.6 bar", language="tr").to_dict()
    assert ok["technical"]["telemetry"][0]["status"] == "normal"


def test_stored_code_is_intermittent_evidence_not_present_danger() -> None:
    active = answer_query("", dtcs=["SPN 100 FMI 1"], language="tr").to_dict()
    stored = answer_query("", dtcs=[{"code": "SPN 100 FMI 1", "status": "HISTORY"}], language="tr").to_dict()
    assert active["urgency"]["level"] == "RED" and stored["urgency"]["level"] != "RED"
    assert "[geçmiş]" in stored["summary"]
    assert any("Aralıklı arıza" in s["text"] for s in stored["steps"])
    assert stored["causes"][0]["confidence"] == "low"


def test_pending_code_never_raises_red_and_asks_for_a_drive_cycle() -> None:
    d = answer_query("", dtcs=[{"code": "SPN 100 FMI 1", "status": "PENDING"}], language="tr").to_dict()
    assert d["urgency"]["level"] == "YELLOW"
    assert any(s["refs"] == ["template:pending"] for s in d["steps"])


def test_repeated_electrical_code_is_intermittent_but_repeated_real_condition_is_not() -> None:
    wire = answer_query("", dtcs=[{"spn": 110, "fmi": 4, "oc": 9}], language="tr").to_dict()
    real = answer_query("", dtcs=[{"spn": 110, "fmi": 0, "oc": 9}], language="tr").to_dict()
    assert any(s["refs"] == ["template:intermittent"] for s in wire["steps"])
    assert not any(s["refs"] == ["template:intermittent"] for s in real["steps"])


def test_every_code_answer_ends_with_repair_verification_in_the_fault_state() -> None:
    d = answer_query("rölantide P0300", language="tr").to_dict()
    assert d["steps"][-1]["refs"] == ["template:verify_repair"]
    assert "(rölantide)" in d["steps"][-1]["text"]
    assert answer_query("akü bitiyor", language="tr").to_dict()["steps"][-1]["refs"] != ["template:verify_repair"]

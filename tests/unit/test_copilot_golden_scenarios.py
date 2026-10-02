"""Golden scenarios for the structured offline copilot (copilot upgrade).

Each scenario pins the BEHAVIOUR a mechanic relies on — urgency colour,
safety banner, what was understood, which cause leads and how confident the
copilot is allowed to be — not the exact wording. Categories (README design
principles): DTC-only, SPN/FMI, DM1, symptom-only, telemetry + code,
contradicting evidence, no data, EV/HV safety, brakes/steering, multi-code,
typos, Turkish/English input, NHTSA recall section, PGN lookup.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.engine.ai.copilot_answer import answer_query
from src.engine.ai.knowledge_base import fold_text

# (id, kwargs, expectations)
SCENARIOS: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    # ---- DTC only -------------------------------------------------------
    ("dtc_p0101", {"text": "P0101"}, {"risk": "YELLOW", "codes": ["P0101"], "top": "maf", "conf": {"medium"}}),
    ("dtc_p0217_red", {"text": "P0217"}, {"risk": "RED", "codes": ["P0217"], "has_causes": True}),
    ("dtc_lowercase_tr", {"text": "p0300 kodu var"}, {"risk": "YELLOW", "codes": ["P0300"], "lang": "tr"}),
    ("dtc_p0087_rail", {"text": "P0087"}, {"risk": "RED", "top": "yakit"}),
    ("dtc_letter_o_typo", {"text": "PO101"}, {"codes": ["P0101"], "top": "maf"}),
    # ---- SPN / FMI / DM1 -------------------------------------------------
    ("spn_oil_fmi1", {"text": "SPN 100 FMI 1"}, {"risk": "RED", "spns": [100], "top": "yag pompasi"}),
    ("spn_compact_form", {"text": "spn 3251 fmi 16"}, {"spns": [3251], "top": "dpf"}),
    ("dm1_payload_spn110", {"dm1": ["44 FF 6E 00 00 05 FF FF"]}, {"risk": "RED", "spns": [110], "top": "termostat"}),
    ("dm1_no_dtc", {"dm1": ["00 FF 00 00 00 00 FF FF"]}, {"risk": "GRAY", "no_causes": True, "missing": ["codes"]}),
    # ---- symptom only ----------------------------------------------------
    ("sym_overheat_tr", {"text": "motor ısınıyor"}, {"risk": "RED", "symptoms": ["engine-overheating"], "missing": ["codes"], "conf": {"low"}}),
    ("sym_dpf_lamp", {"text": "DPF lambası yandı"}, {"symptoms": ["dpf-regeneration-failed"], "top": "dpf", "conf": {"low"}}),
    ("sym_no_start_en", {"text": "engine cranks but will not start"}, {"lang": "en", "symptoms": ["crank-no-start"]}),
    ("sym_black_smoke", {"text": "siyah duman atıyor"}, {"symptoms": ["black-smoke"], "missing": ["codes"]}),
    ("sym_oil_lamp", {"text": "yağ lambası yandı"}, {"risk": "RED", "symptoms": ["low-oil-pressure"]}),
    ("sym_two_complaints", {"text": "motor ısınıyor, DPF lambası yandı"},
     {"symptoms": ["engine-overheating", "dpf-regeneration-failed"], "risk": "RED"}),
    ("sym_no_crank_tr", {"text": "marş basmıyor tık tık ses geliyor"},
     {"symptoms": ["starter-relay-circuit-open", "battery-drain-parasitic"], "not_symptoms": ["crank-no-start"]}),
    ("sym_negation_kept", {"text": "araba çalışmıyor marş dönüyor"},
     {"symptoms": ["crank-no-start"], "not_symptoms": ["starter-relay-circuit-open"]}),
    ("sym_numbered_cylinder", {"text": "3. silindir tekleme yapıyor"},
     {"symptoms": ["misfire-cylinder-3"], "not_symptoms": ["misfire-random-multiple", "rough-idle-vibration"]}),
    # ---- everyday phrasings: the specific phrase wins, generic words do not pull ----
    ("sym_battery_not_ev", {"text": "akü şarj olmuyor"},
     {"symptoms": ["battery-drain-parasitic"], "not_symptoms": ["ev-charging-interlock-fault", "ev-hv-isolation-warning"]}),
    ("sym_ac_not_no_start", {"text": "klima çalışmıyor"},
     {"symptoms": ["ac-refrigerant-pressure-low"], "not_symptoms": ["crank-no-start"]}),
    ("sym_stall_driving", {"text": "araç seyir halinde stop etti"},
     {"symptoms": ["crank-sensor-signal-missing", "fuel-pump-driver-module-offline"]}),
    # ---- answers to the symptom checks narrow the complaint ------------
    ("check_sleep_current_text", {"text": "akü bitiyor, uyku akımı 320 mA ölçtüm"},
     {"top": "bcm", "conf": {"medium"}, "summary": "Cevaplarınıza göre"}),
    ("check_spare_key_input", {"text": "anahtar ışığı yanıp sönüyor, çalışıp stop ediyor",
                               "answers": {"immobilizer-key-transponder-missing.q2": "evet"}},
     {"top": "anahtar rfid", "conf": {"medium"}}),
    ("check_no_crank_bridge", {"text": "marş tık demiyor", "answers": {"starter-relay-circuit-open.q1": "hayır"}},
     {"top": "mars selenoidi"}),
    # ---- complaint with a gauge value (no signal name in the text) -------
    ("sym_overheat_gauge_value", {"text": "motor hararet yapıyor, göstergede 112 derece"},
     {"risk": "RED", "finding": ("CoolantTemp", "critical_high")}),
    ("sym_overheat_gauge_normal", {"text": "motor hararet yapıyor ama gösterge 88 derece"},
     {"risk": "GRAY", "finding": ("CoolantTemp", "normal")}),
    # ---- telemetry + code ------------------------------------------------
    ("tel_coolant_critical", {"dtcs": ["SPN 110 FMI 0"], "telemetry": {"CoolantTemp": 112}},
     {"risk": "RED", "top": "termostat", "conf": {"high"}, "finding": ("CoolantTemp", "critical_high")}),
    ("tel_oil_low_idle", {"dtcs": ["SPN 100 FMI 1"], "telemetry": {"EngineOilPressure": 0.6, "EngineSpeed": 700}},
     {"risk": "RED", "top": "yag pompasi", "conf": {"high"}, "finding": ("EngineOilPressure", "low")}),
    ("tel_overboost_text", {"text": "P0234 turbo basıncı 3.9 bar"},
     {"risk": "RED", "top": "vgt", "finding": ("BoostPressure", "critical_high")}),
    ("tel_spn_without_fmi", {"text": "SPN 110 aktif, su sıcaklığı 98 derece"},
     {"finding": ("CoolantTemp", "above_nominal"), "missing": ["fmi:SPN 110"]}),
    ("tel_psi_conversion", {"dtcs": ["P0234"], "telemetry": {"BoostPressure": {"value": 58, "unit": "psi"}}},
     {"finding": ("BoostPressure", "critical_high"), "risk": "RED"}),
    # ---- contradicting evidence -----------------------------------------
    ("contra_sensor_vs_thermostat", {"dtcs": ["SPN 110 FMI 4"], "telemetry": {"CoolantTemp": -40}},
     {"top": "sensor", "conf": {"high"}, "finding": ("CoolantTemp", "at_limit")}),
    ("contra_normal_coolant", {"dtcs": ["SPN 110 FMI 0"], "telemetry": {"CoolantTemp": 90}},
     {"top": "termostat", "top_has_against": True, "conf": {"low", "medium"}}),
    ("contra_normal_oil_pressure", {"dtcs": ["SPN 100 FMI 1"], "telemetry": {"EngineOilPressure": 3.5, "EngineSpeed": 2000}},
     {"top_has_against": True, "conf": {"low", "medium"}}),
    # ---- no data / unknown ----------------------------------------------
    ("nodata_empty", {"text": ""}, {"risk": "GRAY", "no_causes": True, "missing": ["codes"], "summary": "bulunamadi"}),
    ("nodata_gibberish", {"text": "asdf qwerty"}, {"risk": "GRAY", "no_causes": True}),
    ("nodata_unknown_dtc", {"text": "B3FFF"}, {"risk": "GRAY", "no_causes": True, "summary": "veritabaninda yok"}),
    ("nodata_unknown_signal", {"text": "", "telemetry": {"FluxCapacitor": 1.21}}, {"no_causes": True, "missing": ["unknown:FluxCapacitor"]}),
    ("nodata_battery_no_threshold", {"text": "akü voltajı 11.8V"},
     {"finding": ("BatteryVoltage", "no_threshold"), "no_causes": True, "summary": "11.8"}),
    # ---- EV / HV safety --------------------------------------------------
    ("ev_p0aa6_code", {"dtcs": ["P0AA6"]}, {"risk": "RED", "safety": ["high_voltage"], "top": "izolasyon"}),
    ("ev_isolation_complaint", {"text": "izolasyon arızası"}, {"safety": ["high_voltage"], "risk": "YELLOW"}),
    ("ev_battery_smoke", {"text": "batarya duman çıkıyor"}, {"risk": "RED", "safety": ["fire", "high_voltage"], "first_banner": "fire"}),
    ("ev_isolation_measured_low", {"dtcs": ["P0AA6"], "telemetry": {"IsolationResistance": 20, "HVPackVoltage": 400}},
     {"risk": "RED", "finding": ("IsolationResistance/HVPackVoltage", "critical_low")}),
    ("ev_isolation_measured_ok_en", {"text": "HV pack voltage 400 V, isolation resistance 60 kohm"},
     {"safety": ["high_voltage"], "finding": ("IsolationResistance/HVPackVoltage", "normal")}),
    ("ev_isolation_without_voltage", {"text": "isolation resistance 60 kohm"}, {"missing": ["signal:HVPackVoltage"]}),
    # ---- brakes / steering ----------------------------------------------
    ("brake_abs_lamp", {"text": "abs ışığı yandı"}, {"safety": ["brakes"], "risk": "YELLOW", "symptoms": ["abs-esp-traction-fault"]}),
    ("steering_heavy", {"text": "direksiyon ağırlaştı"}, {"safety": ["steering"], "risk": "RED"}),
    ("steering_lamp_only", {"text": "direksiyon lambası yandı"},
     {"safety": ["steering"], "risk": "YELLOW", "symptoms": ["steering-angle-sensor-uncalibrated"]}),
    ("brake_pedal_sinks", {"text": "fren pedalı boşa gidiyor"}, {"safety": ["brakes"], "risk": "RED"}),
    # ---- multi-code -----------------------------------------------------
    ("multi_lean_banks", {"text": "P0171 P0174"}, {"codes": ["P0171", "P0174"], "top": "iki bank"}),
    ("multi_three_codes", {"text": "P0217 P0300 P0171"}, {"risk": "RED", "codes": ["P0217", "P0300", "P0171"], "min_causes": 3}),
    # ---- typos ----------------------------------------------------------
    ("typo_tr", {"text": "mtor isniyor"}, {"symptoms": ["engine-overheating"], "corrected": True}),
    ("typo_en", {"text": "engine overheting"}, {"lang": "en", "symptoms": ["engine-overheating"], "corrected": True}),
    # ---- Turkish free text with a value ---------------------------------
    ("tr_power_loss_boost", {"text": "motor çekmiyor, turbo basıncı 1.2 bar"},
     {"symptoms": ["turbo-underboost-power-loss"], "finding": ("BoostPressure", "below_nominal")}),
    # ---- recall section / PGN -------------------------------------------
    ("recall_ford_brakes", {"text": "Ford Escape fren tutmuyor"}, {"safety": ["brakes"], "recalls": True}),
    ("pgn_lookup", {"text": "PGN 65262 nedir"}, {"pgn": 65262}),
]


@pytest.fixture(scope="module")
def answers() -> dict[str, Any]:
    return {sid: answer_query(**kwargs).to_dict() for sid, kwargs, _ in SCENARIOS}


def test_scenario_count_meets_the_bar() -> None:
    assert len(SCENARIOS) >= 30


@pytest.mark.parametrize("sid,kwargs,exp", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_golden_scenario(sid: str, kwargs: dict[str, Any], exp: dict[str, Any], answers: dict[str, Any]) -> None:
    d = answers[sid]
    understood = d["understood"]
    causes = d["causes"]
    if "lang" in exp:
        assert d["language"] == exp["lang"]
    if "risk" in exp:
        assert d["urgency"]["level"] == exp["risk"], d["urgency"]
    if "codes" in exp:
        assert [c["code"] for c in understood["dtcs"]] == exp["codes"]
    if "spns" in exp:
        assert [s["spn"] for s in understood["spns"]] == exp["spns"]
    if "symptoms" in exp:
        got = [s["id"] for s in understood["symptoms"]]
        for sym in exp["symptoms"]:
            assert sym in got, got
    if "not_symptoms" in exp:
        got = [s["id"] for s in understood["symptoms"]]
        assert not set(exp["not_symptoms"]) & set(got), got
    if "safety" in exp:
        cats = [b["category"] for b in d["safety_banners"]]
        assert set(exp["safety"]) <= set(cats), cats
    if "first_banner" in exp:
        assert d["safety_banners"][0]["category"] == exp["first_banner"]
    if exp.get("no_causes"):
        assert causes == []
    if exp.get("has_causes"):
        assert causes
    if "min_causes" in exp:
        assert len(causes) >= exp["min_causes"]
    if "top" in exp:
        assert causes, "expected a leading cause"
        assert exp["top"] in fold_text(causes[0]["title"]), causes[0]["title"]
    if "conf" in exp:
        assert causes and causes[0]["confidence"] in exp["conf"], causes[0]["confidence"] if causes else None
    if exp.get("top_has_against"):
        assert causes and causes[0]["against"], "contradicting evidence must be shown"
    if "finding" in exp:
        sig, status = exp["finding"]
        assert (sig, status) in [(t["signal"], t["status"]) for t in d["technical"]["telemetry"]], d["technical"]["telemetry"]
    if "missing" in exp:
        keys = [m["key"] for m in d["missing_data"]]
        for k in exp["missing"]:
            assert k in keys, keys
    if "summary" in exp:
        assert exp["summary"] in fold_text(d["summary"]) or exp["summary"] in d["summary"], d["summary"]
    if exp.get("corrected"):
        assert understood["corrections"]
    if exp.get("recalls"):
        assert d["recalls"].get("items"), "NHTSA section expected"
        assert d["recalls"]["note"]
    if "pgn" in exp:
        assert any(p["pgn"] == exp["pgn"] and p["ref"] for p in d["technical"]["pgns"])


@pytest.mark.parametrize("sid,kwargs,exp", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_every_answer_has_the_six_sections(sid: str, kwargs: dict[str, Any], exp: dict[str, Any], answers: dict[str, Any]) -> None:
    d = answers[sid]
    assert d["summary"].strip()
    assert d["urgency"]["level"] in {"RED", "YELLOW", "GREEN", "GRAY"} and d["urgency"]["advice"]
    assert isinstance(d["causes"], list)
    assert d["steps"], "there is always at least one next step"
    assert isinstance(d["missing_data"], list)
    assert "codes" in d["technical"] and "sources" in d["technical"]
    md = answer_query(**kwargs).to_markdown()
    for heading in ("1.", "2.", "3.", "4.", "5.", "6."):
        assert f"## {heading}" in md or f"<summary>{heading}" in md
    if d["safety_banners"]:
        assert md.startswith("**⚠"), "safety warning must be the first line"

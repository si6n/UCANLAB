"""KnowledgeBase access layer + input understanding (copilot upgrade)."""

from __future__ import annotations

import pytest

from src.engine.ai.knowledge_base import SOURCE_FILES, KnowledgeBase, fold_text, get_knowledge_base, normalize_dtc_code
from src.engine.ai.query_understanding import decode_dm1_payload, detect_language, parse_query


# ------------------------------------------------------------ knowledge base
def test_construction_is_lazy() -> None:
    kb = KnowledgeBase()
    assert kb.loaded_sources() == []
    kb.symptom("engine-overheating")
    assert "canonical_symptoms" in kb.loaded_sources()
    assert "dtc_database" not in kb.loaded_sources(), "a symptom lookup must not load the 50 MB DTC database"


def test_every_registered_source_is_shipped() -> None:
    inventory = get_knowledge_base().source_inventory()
    assert {row["source"] for row in inventory} == set(SOURCE_FILES)
    missing = [row["path"] for row in inventory if not row["present"]]
    assert not missing, missing


@pytest.mark.parametrize("raw,expected", [
    ("P0101", "P0101"), ("p0101", "P0101"), ("P 0101", "P0101"), ("PO101", "P0101"), ("u0100", "U0100"),
    ("P01", None), ("X0101", None), ("P01011", None), ("", None), ("C6500", None),
])
def test_normalize_dtc_code(raw: str, expected: str | None) -> None:
    assert normalize_dtc_code(raw) == expected


def test_lookups_report_found_and_misses_explicitly() -> None:
    kb = get_knowledge_base()
    hit = kb.dtc("P0101")
    assert hit.found and hit.ref == "dtc_database#P0101" and hit.record["title"]
    miss = kb.dtc("B3FFF")
    assert not miss.found and miss.reason == "not_found" and miss.record is None
    bad = kb.dtc("not-a-code")
    assert not bad.found and bad.reason == "invalid_key"
    spn = kb.spn(110, 0)
    assert spn.found and spn.record["fmi_source"] == "fault_matrix"
    assert kb.spn(600000).reason == "invalid_key"
    assert kb.spn(110, 40).reason == "invalid_key"
    assert kb.fmi_definition(31).found


def test_indexes_cover_every_key_type() -> None:
    kb = get_knowledge_base()
    assert kb.pid("05").found and kb.pid("05").record[0]["confidence"] == "standard"
    assert kb.pid("0C").record[0]["name"] == "Engine RPM"
    assert kb.pgn(65262).found and 110 in kb.pgn(65262).record["spns"]
    assert kb.pgn(127488).found and kb.pgn(127488).record["canboat"]
    assert kb.system("engine-cooling-thermal").found
    assert kb.system_for_label("Motor Soğutma & Termal Yönetim") == "engine-cooling-thermal"
    assert kb.canonical_signal("coolant_temp") == "CoolantTemp"
    assert kb.canonical_signal("TurboBoost") == "BoostPressure"
    assert kb.telemetry_threshold("CoolantTemp").found
    assert kb.hv_threshold("min_isolation_resistance_dc").record["value"] == 100.0
    assert kb.graph_nodes_for_code("SPN 110")
    assert kb.symptom_phrases()
    assert kb.dtc_oem("B0081").found and "AUDI" in kb.dtc_oem("B0081").record["makes"]
    assert kb.recalls("Ford", terms=["brake"])
    assert kb.complaint_count("AUDI").found
    assert "DTC" in kb.glossary()
    assert kb.procedure("P0101").found
    assert kb.mode06(1).found
    assert kb.uds_did(0xF190).found


def test_resolve_ref_rejects_invented_citations() -> None:
    kb = get_knowledge_base()
    assert kb.resolve_ref("dtc_database#P0101")
    assert kb.resolve_ref("j1939_spn_fmi#SPN_110.FMI_0")
    assert not kb.resolve_ref("dtc_database#B3FFF")
    assert not kb.resolve_ref("root_cause_graph#does-not-exist")
    assert not kb.resolve_ref("made_up_source#x")
    assert not kb.resolve_ref("dtc_database")


def test_broken_source_degrades_to_source_unavailable(tmp_path) -> None:
    diag = tmp_path / "diagnostics"
    diag.mkdir()
    (diag / "canonical_symptoms.json").write_text("{broken", encoding="utf-8")
    kb = KnowledgeBase(tmp_path)
    assert kb.symptoms() == {}
    assert kb.symptom("engine-overheating").found is False
    assert kb.dtc("P0101").reason == "source_unavailable"
    row = next(r for r in kb.source_inventory() if r["source"] == "canonical_symptoms")
    assert row["present"] and not row["loaded"]


def test_fold_text_handles_turkish() -> None:
    assert fold_text("Motor ISINIYOR, DPF lambası!") == "motor isiniyor dpf lambasi"
    assert fold_text("Şanzıman Çalışmıyor") == "sanziman calismiyor"


# ------------------------------------------------------------------- parser
@pytest.mark.parametrize("text,lang", [
    ("motor ısınıyor", "tr"), ("araba çalışmıyor", "tr"), ("motor hararet yapiyor", "tr"),
    ("the engine is overheating", "en"), ("my truck has low oil pressure", "en"),
])
def test_detect_language(text: str, lang: str) -> None:
    assert detect_language(text) == lang


@pytest.mark.parametrize("text,spn,fmi", [
    ("SPN 110 FMI 0", 110, 0), ("spn110/0", 110, 0), ("SPN: 3251 - 16", 3251, 16), ("spn 100 fmi:1", 100, 1),
    ("SPN 190", 190, None),
])
def test_spn_spellings(text: str, spn: int, fmi: int | None) -> None:
    pq = parse_query(text)
    assert [(s.spn, s.fmi) for s in pq.spns] == [(spn, fmi)]


def test_invalid_fmi_is_dropped_not_guessed() -> None:
    pq = parse_query("SPN 110 FMI 45")
    assert pq.spns[0].fmi is None
    assert "invalid_fmi:45" in pq.notes


def test_words_never_become_codes() -> None:
    assert parse_query("BOOOO çok ses var, CAFE önünde").dtcs == []


def test_dm1_decoding_lamps_and_multiple_dtcs() -> None:
    lamps, spns, notes = decode_dm1_payload([0x44, 0xFF, 0x6E, 0x00, 0x00, 0x05, 0x64, 0x00, 0x01, 0x02])
    assert lamps is not None and lamps.mil and lamps.amber_warning and not lamps.red_stop
    assert [(s.spn, s.fmi, s.occurrence_count) for s in spns] == [(110, 0, 5), (100, 1, 2)]
    assert notes == []


def test_dm1_legacy_conversion_method_is_flagged() -> None:
    _lamps, spns, notes = decode_dm1_payload("04 FF 6E 00 00 85")
    assert spns and spns[0].note
    assert any("conversion_method_1" in n for n in notes)


@pytest.mark.parametrize("payload", ["zz", [1, 2], b"\x00", "0x"])
def test_dm1_garbage_is_unreadable_not_guessed(payload: object) -> None:
    lamps, spns, notes = decode_dm1_payload(payload)
    assert lamps is None and spns == [] and notes == ["dm1_unreadable_payload"]


@pytest.mark.parametrize("text,signal,value,unit", [
    ("su sıcaklığı 112 derece", "CoolantTemp", 112.0, "°C"),
    ("coolant temperature 230 F", "CoolantTemp", 110.0, "°C"),
    ("akü voltajı 11,8 V", "BatteryVoltage", 11.8, "V"),
    ("turbo basıncı 250 kPa", "BoostPressure", 2.5, "bar"),
    ("isolation resistance 2 MOhm", "IsolationResistance", 2000.0, "kΩ"),
])
def test_values_from_text_with_units(text: str, signal: str, value: float, unit: str) -> None:
    readings = parse_query(text).readings
    assert len(readings) == 1
    r = readings[0]
    assert (r.canonical, r.unit) == (signal, unit)
    assert r.value == pytest.approx(value, rel=1e-3)
    assert r.unit_assumed is False


def test_symptom_matching_tr_en_and_typos() -> None:
    assert [s.symptom_id for s in parse_query("Motor hararet yaptı").symptoms][:1] == ["engine-overheating"]
    assert "dpf-regeneration-failed" in [s.symptom_id for s in parse_query("dpf ışığı yanıyor").symptoms]
    typo = parse_query("motor isniyor")
    assert typo.symptoms[0].symptom_id == "engine-overheating" and typo.symptoms[0].fuzzy
    assert parse_query("bir defa sürede absürt").symptoms == [], "short keywords must not fire inside words"


def test_safety_terms_are_detected() -> None:
    pq = parse_query("fren tutmuyor, yanık kokusu var, turuncu kablo eridi, direksiyon kilitlendi")
    assert set(pq.safety_terms) == {"brakes", "fire", "high_voltage", "steering"}


@pytest.mark.parametrize("text,signal,value,unit", [
    ("motor hararet yapıyor, göstergede 112 derece", "CoolantTemp", 112.0, "°C"),
    ("yağ lambası yandı, basınç 0,3 bar", "EngineOilPressure", 0.3, "bar"),
    ("akü bitti, voltaj 11.9 V", "BatteryVoltage", 11.9, "V"),
])
def test_complaint_gauge_value_is_tied_to_its_signal(text: str, signal: str, value: float, unit: str) -> None:
    pq = parse_query(text)
    got = [(r.canonical, r.value, r.unit, r.origin) for r in pq.readings]
    assert (signal, value, unit, "text_context") in got, got
    assert any(n.startswith(f"context_reading:{signal}<-") for n in pq.notes)


@pytest.mark.parametrize("text", [
    "motor hararet yapıyor 112",                    # no unit: never assumed
    "motor hararet yapıyor 90 km/h giderken",       # incompatible unit
    "motor hararet yapıyor önce 95 derece sonra 120 derece",  # two candidates: ambiguous
    "112 derece",                                   # no complaint about a gauge
])
def test_gauge_value_is_not_guessed(text: str) -> None:
    assert not [r for r in parse_query(text).readings if r.origin == "text_context"]


@pytest.mark.parametrize("text,expected,excluded", [
    ("marş basmıyor", "starter-relay-circuit-open", "crank-no-start"),
    ("mars basmyor", "starter-relay-circuit-open", "crank-no-start"),     # typo inside the negation
    ("araba çalışmıyor marş dönüyor", "crank-no-start", "starter-relay-circuit-open"),
])
def test_typo_tolerance_never_flips_negation(text: str, expected: str, excluded: str) -> None:
    got = [s.symptom_id for s in parse_query(text).symptoms]
    assert expected in got and excluded not in got, got


@pytest.mark.parametrize("cause", [
    "1. 20-Way TCM Vehicle Harness Connector 2. Transmission Control Module (TCM)",
    "Corroded or loose power supply to",
    "Inspect the batteries and supplies to the TECU. Key off. Notice Allow TECU to power down",
    "Note: For component location refer to OEM service literature",
    "Vehicle charging/battery system failure FMI 1, 4, 17, 18: Vehicle Harness Wiring shorted to power",
])
def test_harvest_residue_is_not_a_cause(cause: str) -> None:
    from src.engine.ai.copilot_reasoner import _clean_cause

    assert _clean_cause(cause) is None


@pytest.mark.parametrize("cause", [
    "Short to power in the power supply circuit to the wastegate/boost pressure control solenoid A",
    "Unplugged connector — ECT connector not plugged in",
    "Low coolant due to leak in cooling system",
])
def test_genuine_causes_survive_the_residue_filter(cause: str) -> None:
    from src.engine.ai.copilot_reasoner import _clean_cause

    assert _clean_cause(cause) == cause


# ------------------------------------------------- numbered symptoms / areas
@pytest.mark.parametrize("text,expected", [
    ("3. silindir tekleme yapıyor", "misfire-cylinder-3"),
    ("silindir 1 ateşlemiyor", "misfire-cylinder-1"),
    ("cylinder 3 misfire", "misfire-cylinder-3"),
    ("bank 2 fakir", "lean-condition-bank2"),
])
def test_numbers_select_the_numbered_symptom(text: str, expected: str) -> None:
    assert [s.symptom_id for s in parse_query(text).symptoms] == [expected]


def test_every_shipped_keyword_matches_its_own_symptom() -> None:
    import re

    kb = get_knowledge_base()
    code_like = re.compile(r"\b[pbcu][0-9a-f]{4}\b|spn|iso \d|pgn", re.IGNORECASE)
    bad = []
    for sid, rec in kb.symptoms().items():
        for kw in list(rec.get("keywords_tr") or []) + list(rec.get("keywords_en") or []):
            if code_like.search(kw):
                continue  # code mentions go through the code parser, not the symptom matcher
            if sid not in [s.symptom_id for s in parse_query(kw, kb=kb).symptoms]:
                bad.append((sid, kw))
    assert not bad, bad[:10]


def test_complaint_without_known_cause_names_subsystems_not_causes() -> None:
    from src.engine.ai.copilot_answer import answer_query

    d = answer_query("akü bitiyor", language="tr").to_dict()
    assert d["causes"], "a matched complaint must still give the mechanic somewhere to look"
    assert {c["kind"] for c in d["causes"]} == {"area"}
    assert all(c["likelihood"] == 0.0 and c["confidence"] == "low" for c in d["causes"])
    assert all(c["refs"] == ["canonical_symptoms#battery-drain-parasitic"] for c in d["causes"])
    assert "Kayıtlı kök neden yok" in d["summary"] and "%" not in answer_query("akü bitiyor", language="tr").to_markdown()


def test_area_never_outranks_a_real_cause() -> None:
    from src.engine.ai.copilot_answer import answer_query

    d = answer_query("motor hararet yapıyor", dtcs=["SPN 110 FMI 0"], language="tr").to_dict()
    assert d["causes"] and "area" not in {c["kind"] for c in d["causes"]}


@pytest.mark.parametrize("text,excluded", [
    ("egzozdan mavi duman çıkıyor", "black-smoke"),       # blue smoke is not black smoke
    ("motordan vuruntu sesi geliyor", "transmission-slip-limp"),
    ("fan çalışmıyor", "crank-no-start"),
])
def test_generic_word_does_not_pull_an_unrelated_symptom(text: str, excluded: str) -> None:
    assert excluded not in [s.symptom_id for s in parse_query(text).symptoms]


def test_specific_phrase_explains_its_sub_phrase() -> None:
    from src.engine.ai.copilot_answer import answer_query

    d = answer_query("akü şarj olmuyor", language="tr").to_dict()
    assert [s["id"] for s in d["understood"]["symptoms"]] == ["battery-drain-parasitic"]
    assert "high_voltage" not in [b["category"] for b in d["safety_banners"]], "a 12 V battery is not an HV hazard"


def test_exhaust_smoke_is_not_a_fire_but_engine_smoke_is() -> None:
    assert "fire" not in parse_query("egzozdan mavi duman çıkıyor").safety_terms
    assert "fire" in parse_query("motordan duman çıkıyor").safety_terms


def test_safety_only_complaint_summary_says_what_was_recognised() -> None:
    from src.engine.ai.copilot_answer import answer_query

    d = answer_query("fren pedalı boşa gidiyor", language="tr").to_dict()
    assert d["summary"].startswith("Güvenlik açısından kritik bir şikâyet tanındı (fren)")
    assert d["urgency"]["level"] == "RED" and d["causes"] == []


@pytest.mark.parametrize("text,expected", [
    ("coolant keeps dropping", "engine-overheating"),
    ("car dies while driving", "crank-sensor-signal-missing"),
    ("nothing happens when I turn the key", "starter-relay-circuit-open"),
    ("gearbox won't shift", "transmission-slip-limp"),
    ("ac not blowing cold", "ac-refrigerant-pressure-low"),
    ("airbag light on", "airbag-squib-resistance-high-driver"),
    ("parking brake stuck", "electronic-parking-brake-stuck"),
])
def test_everyday_english_phrasings(text: str, expected: str) -> None:
    assert expected in [s.symptom_id for s in parse_query(text).symptoms]


@pytest.mark.parametrize("cause", [
    "O2 Sensor : Measures the oxygen level in the exhaust gases.",
    "A properly functioning O2 sensor heater is important for reducing emissions and ensuring the engine runs efficiently.",
    "A faulty MAF sensor can lead to incorrect air-fuel mixture.",
    "FMI 4: Motor Yağ Sıcaklığı - Voltaj normalin altında. → Sinyal pini ile şasi arasındaki direnci ölçün",
    "x" * 230,
])
def test_descriptions_and_procedures_are_not_causes(cause: str) -> None:
    from src.engine.ai.copilot_reasoner import _is_harvest_residue

    assert _is_harvest_residue(cause)


def test_graph_titles_are_shown_in_the_answer_language() -> None:
    from src.engine.ai.copilot_answer import answer_query

    tr = [c["title"] for c in answer_query("motor hararet yapıyor", language="tr").to_dict()["causes"]]
    en = [c["title"] for c in answer_query("engine overheating", language="en").to_dict()["causes"]]
    assert "Soğutma sistemindeki kaçak nedeniyle soğutma suyu eksik" in tr and "Low coolant due to leak in cooling system" not in tr
    assert "Engine overheating" in en and "Motor aşırı sıcaklık" not in en


@pytest.mark.parametrize("telemetry,status,risk", [
    ({"DPFDiffPressure": 30}, "critical_high", "RED"),       # clogged limit >25 kPa
    ({"DPFDiffPressure": 15}, "above_nominal", "GRAY"),      # above the 12 kPa full-load band
    ({"DPFDiffPressure": 3}, "normal", "GRAY"),
    ({"AirPressureCircuit1": 420}, "low", "RED"),            # below the 5.5 bar red warning
    ({"AirPressureCircuit2": 800}, "below_nominal", "GRAY"),
    ({"AirPressureCircuit1": 1100}, "normal", "GRAY"),
])
def test_sourced_dpf_and_brake_air_thresholds(telemetry: dict, status: str, risk: str) -> None:
    from src.engine.ai.copilot_answer import answer_query

    d = answer_query("", telemetry=telemetry, language="tr").to_dict()
    assert d["technical"]["telemetry"][0]["status"] == status
    assert d["urgency"]["level"] == risk
    if status == "low":
        assert "brakes" in [b["category"] for b in d["safety_banners"]]

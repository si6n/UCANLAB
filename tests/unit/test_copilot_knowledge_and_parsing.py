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

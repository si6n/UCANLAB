"""OEM codes whose meaning differs per make: the copilot uses the vehicle make's meaning."""

from __future__ import annotations

from src.engine.ai.copilot_answer import answer_query
from src.engine.ai.knowledge_base import KnowledgeBase

KB = KnowledgeBase()


def test_meaning_is_picked_by_make_with_gm_fallback_and_brand_groups_refused() -> None:
    assert KB.dtc_oem_meaning("P1106", "Honda").record["text"] == "BARO Circuit Range Performance Malfunction"
    assert KB.dtc_oem_meaning("P1106", "chevrolet").record["make"] == "CHEVY"
    assert KB.dtc_oem_meaning("P1106", "Pontiac").record["make"] == "GM", "a GM division falls back to the GM list"
    assert KB.dtc_oem_meaning("P1106", "Volkswagen Group").record["make"] == "VOLKSWAGEN"
    assert not KB.dtc_oem_meaning("P1106", "Hyundai-Kia").found, "a brand group is not one make"
    assert not KB.dtc_oem_meaning("P1106", None).found
    assert KB.dtc_oem_meanings("P0101") == {}, "generic codes have one meaning"


def test_conflicting_generic_record_is_replaced_by_the_make_meaning() -> None:
    d = answer_query("", dtcs=["P1106"], vehicle_make="Honda", language="tr").to_dict()
    (code,) = d["technical"]["codes"]
    assert code["oem_conflict"] and code["title"] == "BARO Circuit Range Performance Malfunction (Honda)"
    assert "farklı bir arızayı" in d["summary"]
    cited = {r for c in d["causes"] for r in c["refs"]}
    assert not any(r.startswith("dtc_database#P1106") for r in cited), "another make's causes are not served"
    assert any(s["refs"] == ["dtc_oem_meanings#P1106.HONDA"] for s in d["steps"])
    assert all(KB.resolve_ref(r) for r in d["technical"]["sources"])


def test_same_meaning_keeps_the_generic_record() -> None:
    d = answer_query("", dtcs=["P1106"], vehicle_make="Chevrolet", language="tr").to_dict()
    (code,) = d["technical"]["codes"]
    assert not code["oem_conflict"] and code["oem_text"].startswith("Chevy — MAP Sensor")


def test_unknown_make_lists_the_meanings_and_asks_for_the_make() -> None:
    d = answer_query("", dtcs=["P1106"], language="tr").to_dict()
    ask = next(m for m in d["missing_data"] if m["key"] == "make:P1106")
    assert "BARO Circuit Range Performance Malfunction" in ask["what"] and "MAP Sensor" in ask["what"]
    assert "Dikkat: P1106 üreticiye özeldir" in d["summary"]
    assert all(KB.resolve_ref(r) for r in ask["refs"])


def test_vin_make_selects_the_meaning() -> None:
    d = answer_query("", dtcs=["P1101"], identity={"vin": "WVWZZZ1KZS1M00001"}, language="en").to_dict()
    (code,) = d["technical"]["codes"]
    assert code["oem_text"].startswith("Volkswagen — Oxygen Sensor")

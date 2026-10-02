"""Symptom checks: answers to the copilot's questions narrow the complaint (curated effects only)."""

from __future__ import annotations

import pytest

from src.engine.ai.copilot_answer import answer_query
from src.engine.ai.knowledge_base import get_knowledge_base
from src.engine.ai.query_understanding import parse_query


def _titles(d: dict) -> list[str]:
    return [c["title"] for c in d["causes"]]


def test_every_check_targets_only_its_own_symptom() -> None:
    kb = get_knowledge_base()
    data = kb._json_source("symptom_checks")  # noqa: SLF001
    assert data["symptoms"], "the curated checks file must not be empty"
    for sid, rec in data["symptoms"].items():
        sym = kb.symptom(sid).record
        assert sym, sid
        for chk in rec["checks"]:
            assert chk["id"] == f"q{chk['q']}" and chk["q"] < len(sym["initial_questions"]), (sid, chk["id"])
            assert chk["question_en"], (sid, chk["id"])
            effects = list(chk.get("outcomes", {}).values()) + chk.get("bands", [])
            assert effects, (sid, chk["id"])
            for eff in effects:
                assert bool(eff["note_tr"]) == bool(eff["note_en"]), (sid, chk["id"])
                for t in eff["favor"] + eff["rule_out"]:
                    assert (t < len(sym["subsystems"])) if isinstance(t, int) else t in sym["candidate_dtcs"], (sid, t)


def test_measurement_bands_do_not_overlap_or_leave_gaps() -> None:
    data = get_knowledge_base()._json_source("symptom_checks")  # noqa: SLF001
    for sid, rec in data["symptoms"].items():
        for chk in rec["checks"]:
            bands = chk.get("bands") or []
            if not bands:
                continue
            assert bands[0]["min"] is None and bands[-1]["max"] is None, (sid, chk["id"])
            for a, b in zip(bands, bands[1:], strict=False):
                assert a["max"] == b["min"], (sid, chk["id"])


def test_unanswered_questions_are_offered() -> None:
    d = answer_query("akü bitiyor", language="tr").to_dict()
    keys = [c["key"] for c in d["checks"]]
    assert keys and all(k.startswith("battery-drain-parasitic.") for k in keys)
    assert all(c["answer"] is None and not c["result"] for c in d["checks"])
    assert all(c["refs"] == [f"symptom_checks#{c['key']}"] for c in d["checks"])


def test_answer_moves_subsystem_up_with_its_reason() -> None:
    d = answer_query("akü bitiyor", language="tr", answers={"battery-drain-parasitic.q0": 320}).to_dict()
    top = d["causes"][0]
    assert top["title"] == "Gövde Kontrol Modülü (BCM)" and top["confidence"] == "medium"
    assert any("50 mA" in e["text"] and e["ref"] == "symptom_checks#battery-drain-parasitic.q0" for e in top["support"])


def test_answer_moves_subsystem_down_and_shows_why() -> None:
    d = answer_query("akü bitiyor", language="tr", answers={"battery-drain-parasitic.q2": "14,1"}).to_dict()
    alt = next(c for c in d["causes"] if c["title"].startswith("Alternatör"))
    assert alt["against"] and alt["rank"] == len(d["causes"])
    assert "Alternatör" not in d["summary"].split("kontrol edin:")[-1]


def test_unknown_and_out_of_scope_answers_change_nothing() -> None:
    base = _titles(answer_query("akü bitiyor", language="tr").to_dict())
    d = answer_query("akü bitiyor", language="tr", answers={
        "battery-drain-parasitic.q0": "bilmiyorum",       # unknown
        "engine-overheating.q0": "evet",                    # symptom not in this complaint
        "battery-drain-parasitic.q9": "evet",               # no such check
    }).to_dict()
    assert _titles(d) == base
    assert "answer_ignored:engine-overheating.q0" in d["understood"]["notes"]


@pytest.mark.parametrize("raw,expected", [
    ("evet", "yes"), ("Evet", "yes"), ("yes", "yes"), (True, "yes"), ("hayır", "no"), ("HAYIR", "no"),
    (False, "no"), ("belki", "unknown"), ("", "unknown"),
])
def test_yes_no_answers_are_normalised(raw: object, expected: str) -> None:
    pq = parse_query("akü bitiyor", answers={"battery-drain-parasitic.q1": raw})
    assert [a.value for a in pq.answers] == [expected]


def test_measurement_in_text_needs_its_keyword_and_one_number() -> None:
    assert [a.value for a in parse_query("akü bitiyor uyku akımı 320 mA").answers] == [320.0]
    assert parse_query("akü bitiyor 320 mA").answers == []                          # no keyword
    assert parse_query("akü bitiyor uyku akımı 320 mA sonra 40 mA").answers == []   # ambiguous


def test_answer_on_a_graph_backed_complaint_adds_the_area() -> None:
    d = answer_query("motor hararet yapıyor", language="tr", answers={"engine-overheating.q1": "hayır"}).to_dict()
    assert _titles(d)[0] == "Termostat & Radyatör", _titles(d)
    assert d["urgency"]["level"] == "RED", "an answer never lowers the stop-now urgency of the complaint"


def test_english_answer_uses_english_question_and_note() -> None:
    d = answer_query("dead battery", language="en", answers={"battery-drain-parasitic.q0": 20}).to_dict()
    chk = next(c for c in d["checks"] if c["key"] == "battery-drain-parasitic.q0")
    assert chk["question"].startswith("What is the sleep current") and chk["answer_text"] == "20 mA"
    assert chk["result"].startswith("Sleep current under 50 mA")

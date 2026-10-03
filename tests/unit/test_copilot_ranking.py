"""Complaint-only ranking: no fake percentages, the dangerous physical fault first."""

from __future__ import annotations

from src.engine.ai.copilot_answer import answer_query


def test_tied_candidates_show_no_percentage_and_say_so() -> None:
    a = answer_query("dpf lambası yandı", language="tr")
    d = a.to_dict()
    assert len(d["causes"]) >= 2 and all(c["likelihood"] == 0.0 for c in d["causes"])
    assert "Eşit ağırlıklı adaylar" in d["summary"] and "En olası neden" not in d["summary"]
    assert "%" not in a.to_markdown().split("## 4.")[0].split("## 3.")[1]


def test_complaint_reading_puts_the_physical_fault_before_the_sensor_fault() -> None:
    d = answer_query("yağ lambası yandı", language="tr").to_dict()
    top = d["causes"][0]
    assert "pompas" in top["title"].lower(), [c["title"] for c in d["causes"]]
    assert any("önce bunu eleyin" in e["text"] for e in top["support"])
    assert top["likelihood"] > d["causes"][1]["likelihood"]


def test_overheating_complaint_lists_physical_causes_first() -> None:
    d = answer_query("motor hararet yapıyor", language="tr").to_dict()
    physical = {"soğutma sistemindeki kaçak nedeniyle soğutma suyu eksik", "motor aşırı sıcaklık",
                "termostat kapalı kalması / viskoz fan kilitlemez / radyatör tıkalı"}
    assert {c["title"].lower() for c in d["causes"][:3]} == physical
    assert all("P0128" not in c["codes"] for c in d["causes"]), "P0128 means the engine runs too COLD"


def test_no_start_complaint_never_leads_with_oil_pressure() -> None:
    d = answer_query("marş basıyor ama çalışmıyor", language="tr").to_dict()
    assert d["causes"] and all("yağ" not in c["title"].lower() for c in d["causes"])
    assert [c["kind"] for c in d["causes"]] == ["area"] * len(d["causes"])


def test_leading_tie_is_named_as_a_tie() -> None:
    d = answer_query("motor hararet yapıyor", language="tr").to_dict()
    assert "Önde, eşit ağırlıkta" in d["summary"] and "En olası neden" not in d["summary"]


def test_summary_points_to_the_most_useful_unanswered_question() -> None:
    first = answer_query("akü bitiyor", language="tr").summary
    assert first.endswith("Önce şu soruyu cevaplayın: Kontak kapalı ve araç kilitliyken çekilen uyku akımı kaç mA?")
    done = answer_query("akü bitiyor", language="tr", answers={
        "battery-drain-parasitic.q0": 320, "battery-drain-parasitic.q1": "hayır", "battery-drain-parasitic.q2": 14}).summary
    assert "Önce şu soruyu" not in done


def test_the_named_next_question_is_listed_first() -> None:
    d = answer_query("motor hararet yapıyor", language="tr").to_dict()
    assert d["summary"].endswith(d["checks"][0]["question"])


def test_ties_follow_the_best_matched_complaint() -> None:
    d = answer_query("dpf doldu güç kısıtlaması var", language="tr").to_dict()
    assert d["causes"][0]["title"].startswith("DPF"), [c["title"] for c in d["causes"]]

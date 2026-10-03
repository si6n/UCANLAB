"""Readings keyed by a protocol identifier (SPN / OBD PID) land on the canonical signal."""

from __future__ import annotations

from src.engine.ai.copilot_answer import answer_query
from src.engine.ai.knowledge_base import KnowledgeBase
from src.engine.ai.query_understanding import parse_query


def test_spn_and_pid_keys_resolve_to_the_same_canonical_signal() -> None:
    kb = KnowledgeBase()
    for key in ("SPN 190", "SPN_190", "spn190", "J1939 SPN 190", "PID 0C", "PID 0x0C", "pid_c", "01 PID 0C"):
        assert kb.canonical_signal(key) == "EngineSpeed", key
    assert kb.canonical_signal("SPN 110") == "CoolantTemp"
    assert kb.protocol_signal("SPN 100") == ("EngineOilPressure", "kPa")
    assert kb.protocol_signal("SPN 999999") is None
    assert kb.protocol_signal("EngineSpeed") is None
    assert kb.canonical_signal("coolant_temp") == "CoolantTemp", "name aliases still work"


def test_unitless_spn_value_is_in_the_protocol_unit() -> None:
    pq = parse_query("", telemetry={"SPN 100": 350, "SPN 110": 104, "PID 0C": 1450})
    by = {r.canonical: r for r in pq.readings}
    assert by["EngineOilPressure"].value == 3.5 and by["EngineOilPressure"].unit == "bar", "350 kPa, not 350 bar"
    assert by["CoolantTemp"].value == 104
    assert by["EngineSpeed"].value == 1450


def test_spn_without_a_unit_conversion_stays_unknown() -> None:
    pq = parse_query("", telemetry={"SPN 27": 40})
    assert not pq.readings


def test_spn_keyed_reading_is_judged_like_a_named_one() -> None:
    named = answer_query("motor hararet yapıyor", telemetry={"CoolantTemp": 112}, language="tr").to_dict()
    keyed = answer_query("motor hararet yapıyor", telemetry={"SPN 110": 112}, language="tr").to_dict()
    assert named["urgency"]["level"] == keyed["urgency"]["level"]
    assert [c["title"] for c in named["causes"]] == [c["title"] for c in keyed["causes"]]

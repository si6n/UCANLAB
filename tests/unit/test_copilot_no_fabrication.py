"""The copilot never fabricates measurements, values, codes or sources.

AGENTS.md §2.3 / README principle 3. These tests are the lock on the
copilot-upgrade answer path:

* every citation printed in an answer resolves to a real record;
* every number shown to the user comes from the user's input (or a unit
  conversion / ratio of it) or from a cited record / fixed template;
* telemetry findings exist only for signals the user actually supplied;
* unknown codes get no invented meaning, causes or severity;
* NaN/Inf values are rejected, never evaluated;
* answers are deterministic and the new modules have no write/TX path.
"""

from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from src.engine.ai.copilot_answer import StructuredAnswer, answer_query
from src.engine.ai.knowledge_base import get_knowledge_base
from tests.unit.test_copilot_golden_scenarios import SCENARIOS

AI_DIR = Path(__file__).resolve().parents[2] / "src" / "engine" / "ai"
NEW_MODULES = ("knowledge_base.py", "query_understanding.py", "copilot_reasoner.py", "copilot_answer.py", "local_search.py")
_NUM_RE = re.compile(r"(?<![A-Za-z_])-?\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    for m in _NUM_RE.finditer(text):
        try:
            out.add(round(float(m.group(0).replace(",", ".")), 3))
        except ValueError:
            continue
    return out


def _record_text(ref: str) -> str:
    """Raw JSON text of the record a citation points at ('' for rule refs)."""
    kb = get_knowledge_base()
    source, _, key = ref.partition("#")
    if source == "dtc_database":
        look = kb.dtc(key.split(".")[0])
        return json.dumps(look.record, ensure_ascii=False) if look.found else ""
    if source == "j1939_spn_fmi":
        if key.startswith("PGN_"):
            return json.dumps(kb.pgn(int(key[4:])).record, ensure_ascii=False)
        if key.startswith("fmi_definitions."):
            return json.dumps(kb.fmi_definition(int(key.split(".")[1])).record, ensure_ascii=False)
        m = re.match(r"SPN_(\d+)(?:\.FMI_(\d+))?", key)
        assert m, ref
        look = kb.spn(int(m.group(1)), int(m.group(2)) if m.group(2) else None)
        return json.dumps(look.record, ensure_ascii=False)
    if source == "root_cause_graph":
        return " ".join(n.title for nodes in kb._graph_index().values() for n in nodes if n.id == key)  # noqa: SLF001
    if source == "canonical_symptoms":
        return json.dumps(kb.symptom(key).record, ensure_ascii=False)
    if source == "telemetry_thresholds":
        return json.dumps(kb.telemetry_threshold(key).record, ensure_ascii=False)
    if source == "hv_safety_thresholds":
        return json.dumps(kb.hv_threshold(key).record, ensure_ascii=False)
    if source == "signal_measurement_map":
        return json.dumps(kb.signal_measurement(key).record, ensure_ascii=False)
    if source == "extended_pid":
        service, _, pid = key.partition(":")
        return json.dumps(kb.pid(pid, service).record, ensure_ascii=False)
    if source == "canboat_pgn":
        return json.dumps(kb.pgn(int(key[4:])).record, ensure_ascii=False)
    if source == "nhtsa_recalls":
        return json.dumps(kb._json_source("nhtsa_recalls").get(key), ensure_ascii=False)  # noqa: SLF001
    if source == "nhtsa_complaints":
        make, _, model = key.partition("/")
        return json.dumps(kb.complaint_count(make, None if model == "*" else model).record)
    if source == "dtc_oem_layer":
        return json.dumps(kb.dtc_oem(key).record, ensure_ascii=False)
    if source == "copilot_glossary":
        return json.dumps(kb.glossary().get(key), ensure_ascii=False)
    if source == "symptom_checks":
        sid, _, cid = key.partition(".")
        check = next((c for c in kb.symptom_checks(sid) if c.get("id") == cid), None)
        return json.dumps(check, ensure_ascii=False) + json.dumps(kb.symptom(sid).record, ensure_ascii=False)
    return ""


def _allowed_numbers(kwargs: dict[str, Any], answer: StructuredAnswer) -> set[float]:
    allowed: set[float] = set()
    allowed |= _numbers(str(kwargs.get("text", "")))
    allowed |= _numbers(json.dumps(kwargs.get("dtcs", []), default=str))
    allowed |= _numbers(json.dumps(kwargs.get("telemetry", {}), default=str))
    allowed |= _numbers(json.dumps(kwargs.get("answers", {}), default=str))
    # values the parser derived from the input (unit conversion, HV Ω/V ratio)
    for r in answer.understood.get("readings", []):
        allowed.add(round(float(r["value"]), 3))
    for t in answer.technical.get("telemetry", []):
        if t["origin"] in ("input", "text", "computed"):
            allowed.add(round(float(t["value"]), 3))
    for s in answer.understood.get("spns", []):
        allowed |= {float(s["spn"])} | ({float(s["fmi"])} if s["fmi"] is not None else set())
    for ref in answer.all_refs() + [g["ref"] for g in answer.technical.get("glossary", [])]:
        allowed |= _numbers(_record_text(ref))
    # fixed templates (headings, banners, standard names such as J1939 / Mode 03)
    for name in ("copilot_answer.py", "copilot_reasoner.py"):
        allowed |= _numbers((AI_DIR / name).read_text(encoding="utf-8"))
    allowed |= _numbers((AI_DIR / "drive_safety_policy.py").read_text(encoding="utf-8"))
    return allowed


def _shown_text(d: dict[str, Any]) -> str:
    parts = [d["summary"]]
    parts += [r["text"] for r in d["urgency"]["reasons"]]
    for c in d["causes"]:
        parts.append(c["title"])
        parts += [e["text"] for e in c["support"] + c["against"]]
    parts += [s["text"] for s in d["steps"]]
    parts += [m["what"] + " " + m["how"] for m in d["missing_data"]]
    for code in d["technical"]["codes"]:
        parts += [code["title"], code["fmi_text"], code["pgn_text"], code["reference_values"], code["description"]]
    for t in d["technical"]["telemetry"]:
        parts.append(f"{t['value']} {t['reference']}")
    for chk in d.get("checks", []):
        parts += [chk["question"], chk["answer_text"], chk["result"]]
    return "\n".join(p for p in parts if p)


@pytest.fixture(scope="module")
def golden_answers() -> list[tuple[str, dict[str, Any], StructuredAnswer]]:
    return [(sid, kwargs, answer_query(**kwargs)) for sid, kwargs, _ in SCENARIOS]


def test_every_citation_resolves_to_real_data(golden_answers: list[tuple[str, dict[str, Any], StructuredAnswer]]) -> None:
    kb = get_knowledge_base()
    unresolved = [(sid, ref) for sid, _, a in golden_answers for ref in a.all_refs() if not kb.resolve_ref(ref)]
    assert not unresolved, unresolved[:10]


def test_every_number_shown_comes_from_input_or_a_cited_record(
    golden_answers: list[tuple[str, dict[str, Any], StructuredAnswer]],
) -> None:
    problems: list[tuple[str, list[float]]] = []
    for sid, kwargs, answer in golden_answers:
        shown = _numbers(_shown_text(answer.to_dict()))
        extra = sorted(n for n in shown - _allowed_numbers(kwargs, answer))
        if extra:
            problems.append((sid, extra))
    assert not problems, f"numbers with no source: {problems[:5]}"


def test_findings_only_for_supplied_signals(golden_answers: list[tuple[str, dict[str, Any], StructuredAnswer]]) -> None:
    for sid, _kwargs, answer in golden_answers:
        supplied = {r["signal"] for r in answer.understood["readings"]}
        for t in answer.technical["telemetry"]:
            if t["origin"] == "computed":
                assert set(t["signal"].split("/")) <= supplied, sid
            else:
                assert t["signal"] in supplied, (sid, t)


def test_missing_measurements_never_carry_a_value() -> None:
    a = answer_query(dtcs=["SPN 110 FMI 0"])
    keys = [m["key"] for m in a.missing_data]
    assert "signal:CoolantTemp" in keys
    assert a.technical["telemetry"] == []
    assert not any("CoolantTemp =" in e["text"] for c in a.causes for e in c["support"] + c["against"])


def test_unknown_code_gets_no_invented_meaning() -> None:
    a = answer_query("B3FFF").to_dict()
    code = a["technical"]["codes"][0]
    assert code["found"] is False
    assert code["title"] == "" and code["severity"] == "" and code["fmi_text"] == ""
    assert a["causes"] == []
    assert "B3FFF" in a["summary"]
    assert a["urgency"]["level"] == "GRAY", "an unknown code must never look safe (GREEN) or urgent (RED)"


def test_unknown_spn_reports_generic_fmi_only() -> None:
    kb = get_knowledge_base()
    spn = next(n for n in range(523000, 524287) if not kb.spn(n).found)
    a = answer_query(f"SPN {spn} FMI 3").to_dict()
    code = a["technical"]["codes"][0]
    assert code["found"] is False
    assert a["causes"] == []
    assert code["title"] == ""


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "112", None])
def test_non_numeric_telemetry_is_rejected(bad: Any) -> None:
    a = answer_query(dtcs=["SPN 110 FMI 0"], telemetry={"CoolantTemp": bad})
    assert a.technical["telemetry"] == []
    assert "CoolantTemp" in a.understood["unknown_telemetry"]


def test_value_without_unit_is_flagged_as_assumed() -> None:
    a = answer_query("SPN 110 FMI 0 su sıcaklığı 112")
    reading = a.understood["readings"][0]
    assert reading["unit_assumed"] is True
    assert any(m["key"] == "unit:CoolantTemp" for m in a.missing_data)


def test_incompatible_unit_is_not_coerced() -> None:
    a = answer_query(dtcs=["SPN 110 FMI 0"], telemetry={"CoolantTemp": {"value": 3, "unit": "bar"}})
    assert a.technical["telemetry"] == []
    assert "CoolantTemp" in a.understood["unknown_telemetry"]


def test_likelihood_is_relative_and_labelled() -> None:
    a = answer_query("P0171 P0174")
    total = sum(c["likelihood"] for c in a.causes)
    assert math.isclose(total, 1.0, abs_tol=0.01)
    assert "göreli" in a.to_markdown()


@pytest.mark.parametrize("sid,kwargs,_exp", SCENARIOS[:12], ids=[s[0] for s in SCENARIOS[:12]])
def test_answers_are_deterministic(sid: str, kwargs: dict[str, Any], _exp: Any) -> None:
    assert answer_query(**kwargs).to_dict() == answer_query(**kwargs).to_dict()


def test_answers_carry_no_action_triggers() -> None:
    for text in ("DTC temizle P0101", "clear codes P0300", "ECU reset yap"):
        md = answer_query(text).to_markdown()
        assert "<!--ACTIONS" not in md


def test_new_modules_have_no_write_or_tx_path() -> None:
    forbidden_calls = {"write_text", "write_bytes", "transmit", "send", "send_frame", "send_sync", "unlink", "remove"}
    for name in NEW_MODULES:
        tree = ast.parse((AI_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                attr = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
                assert attr not in forbidden_calls, f"{name} calls {attr}"
                if attr == "open":
                    mode = node.args[1] if len(node.args) > 1 else None
                    assert not (isinstance(mode, ast.Constant) and any(c in str(mode.value) for c in "wax+")), name

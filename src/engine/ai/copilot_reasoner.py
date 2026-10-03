"""Deterministic evidence reasoning for the offline copilot.

Takes a :class:`~src.engine.ai.query_understanding.ParsedQuery` and the
:class:`~src.engine.ai.knowledge_base.KnowledgeBase` and produces a
:class:`Reasoning` record: resolved code facts, telemetry findings judged
against the shipped thresholds, ranked root-cause hypotheses (with supporting
and contradicting evidence), urgency, safety categories and the measurements
that would change the ranking.

Scoring (documented, deterministic, no learning at runtime)
----------------------------------------------------------
Each candidate root cause collects additive log-odds style points:

====================================================  ======
evidence                                               points
====================================================  ======
active code (DTC / SPN) declared by the graph node      +3.0 each
node's declared FMI equals the active FMI               +0.5
node only reached through a complaint's candidate code  +1.0
complaint also points at the node's code (corroborates) +0.5
evidence signal measured ABNORMAL                       +1.5
evidence signal measured NORMAL                         -1.0
contradicting signal measured plausible                 -1.5
record-cause fallback (no graph node for the code)      +2.0 - 0.05*i
====================================================  ======

The points are turned into a *relative* likelihood across the listed
candidates with a softmax. That number is explicitly NOT an absolute
probability (the golden-case calibration in ``calibration.py`` measures the
graph ranker, not this view), and the answer says so.

No value is ever invented: telemetry findings exist only for readings the
user/bus supplied, and every hypothesis carries the ``source#key`` refs it
came from.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from src.engine.ai.knowledge_base import KnowledgeBase, fold_text
from src.engine.ai.operating_state import OperatingState, infer_state, infer_state_from
from src.engine.ai.query_understanding import _SYMPTOM_SIGNAL, ParsedQuery, Reading, code_check_symptoms

__all__ = [
    "CodeFact",
    "Hypothesis",
    "MissingData",
    "Reasoning",
    "TelemetryFinding",
    "reason",
]

# Same filler signature the graph derivation rejects (scripts/derive_root_cause_graph.py
# GENERIC_TEMPLATE_RE + _OEM_DUMP_RE + _SOURCE_CITE_RE): copy-paste template
# "causes" that name no fault mechanism must never be shown as a root cause.
_FILLER_RE = re.compile(
    r"devresinde sinyal aral|Kablo demetinde ezilme|Kontrol ünitesi \(ECU/ECM/BCM\) dahili"
    r"|^İlgili kontrol ünitesinde|^İlgili elemanın|^İlgili modülün"
    r"|^Kablo demeti/konnektörde|^Kablo demetinde izolasyon hasar|^VAG ECU/ECM"
    r"|OEM Teshis Kodu|https?://|www\.|^Causes for this code may include|^Possible causes"
    r"|^This (fault|code|DTC|condition) (is|may|can)|^The following (are|is)|^[A-Z][0-9A-Z]{4,6} devresi",
    re.IGNORECASE,
)

# Harvest residue that is not a cause sentence at all (measured over the J1939 +
# DTC cause lists, 2026-10): a numbered parts/figure legend ("1. 20-Way TCM
# Vehicle Harness Connector 2. Transmission Control Module ..."), procedure text
# ("Key off.", "Note:", "refer to ...") and fragments cut mid-sentence ("Corroded
# or loose power supply to"). Shown as a root cause they read as nonsense.
_PARTS_LEGEND_RE = re.compile(r"^\s*\d+[.)]\s+\S.*\s\d+[.)]\s+\S")
_FMI_TABLE_RE = re.compile(r"\S\s+FMI\s+\d+(?:\s*,\s*\d+)*\s*:")  # "... failure FMI 1, 4, 17, 18: ..." table dump
_PROCEDURE_RE = re.compile(r"^(?:note|notice)\s*:|\bkey (?:on|off)\b|\brefer to\b|\breference image\b", re.IGNORECASE)
# A component description or a benefit statement, not a cause:
# "O2 Sensor : Measures the oxygen level...", "... heater is important for ...",
# "A faulty MAF sensor can lead to ...". An FMI table row carrying a procedure
# after an arrow ("FMI 4: ... → measure the resistance") is a test step.
_DESCRIPTION_RE = re.compile(
    r"^[\w /()-]{2,40}\s:\s*(?:ensures|measures|monitors|provides)\b|\bis important for\b|"
    r"^a (?:faulty|properly functioning) .{3,60} (?:can lead to|is important)|^FMI\s+\d+\s*:.*→",
    re.IGNORECASE,
)
_MAX_TITLE_CHARS = 220  # longer than this is several sentences pasted together, not a cause title
# Lower-case "a"/"an" only: a trailing capital "A" is a circuit/bank label
# ("... boost pressure control solenoid A"), not a cut-off article.
_TRUNCATED_RE = re.compile(
    r"\b(?:(?i:to|of|the|and|or|with|for|from|by|at|into|between|that|which|ve|veya|ile)|an?)\s*[,:;-]?$"
)

_SEVERITY_ORDER = ("UNKNOWN", "INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL", "CRITICAL_STOP")
_RISK_ORDER = ("GRAY", "GREEN", "YELLOW", "RED")

BRAKE_SYSTEMS = frozenset({"brakes-air-system", "abs-ebs-stability"})
STEERING_SYSTEMS = frozenset({"steering"})
BRAKE_SYMPTOMS = frozenset({
    "ebs-air-brake-leak", "abs-esp-traction-fault", "electronic-parking-brake-stuck", "hd-spring-brake-drag",
    "hd-service-brake-treadle-valve-leak", "hd-air-dryer-purge-valve-stuck", "hd-pneumatic-compressor-unloader-leak",
    "abs-hydraulic-pump-motor-circuit", "brake-light-switch-rationality",
})
STEERING_SYMPTOMS = frozenset({"steering-angle-sensor-uncalibrated", "marine-hydraulic-steering-air-ingress"})
FIRE_SYMPTOMS = frozenset({"ev-thermal-runaway-early-warning"})
# Complaints that mean "stop the engine now" even without a code or a reading
# (canonical_symptoms#low-oil-pressure asks "Motoru DERHAL durdurdunuz mu?";
# running an overheated engine destroys the head gasket). Overridden only by a
# reading of the same signal that is measured normal.
STOP_SYMPTOMS = frozenset({"engine-overheating", "low-oil-pressure"})
# Brake/steering complaints that are only a warning lamp or a sensor: the
# system itself still works, so they stay YELLOW. Any other brake/steering
# complaint ("fren pedalı boşa gidiyor", "direksiyon ağırlaştı") is RED.
_SAFETY_LAMP_SYMPTOMS: dict[str, frozenset[str]] = {
    "brakes": frozenset({"abs-esp-traction-fault", "brake-light-switch-rationality"}),
    "steering": frozenset({"steering-angle-sensor-uncalibrated"}),
}
_SAFETY_LAMP_TERMS = frozenset({"abs"})
HV_SIGNALS = frozenset({"IsolationResistance", "HVPackVoltage"})
# Service-brake air below the red warning threshold: the spring brakes may apply.
BRAKE_AIR_SIGNALS = frozenset({"AirPressureCircuit1", "AirPressureCircuit2"})


@dataclass(slots=True)
class CodeFact:
    key: str                      # "P0101" | "SPN 110 FMI 0" | "SPN 110"
    kind: str                     # dtc | spn
    origin: str
    found: bool
    title_tr: str = ""
    title_en: str = ""
    description: str = ""
    subsystem: str = ""
    system_id: str = ""
    severity: str = "UNKNOWN"
    severity_ref: str = ""
    fmi: int | None = None
    fmi_text_tr: str = ""
    fmi_text_en: str = ""
    fmi_ref: str = ""
    fmi_generic: bool = False
    pgn: int | None = None
    pgn_acronym: str = ""
    pgn_label: str = ""
    reference_values: str = ""
    record_symptoms: list[str] = field(default_factory=list)
    clean_causes: list[tuple[str, str]] = field(default_factory=list)   # (text, ref)
    steps: list[tuple[str, str, str]] = field(default_factory=list)     # (action, difficulty, ref)
    oem_makes: list[str] = field(default_factory=list)
    oem_text: str = ""
    # OEM code whose meaning differs per make (dtc_oem_meanings): the meaning for this
    # vehicle's make; a conflict with the generic record drops that record's causes/steps.
    oem_meaning: str = ""
    oem_meaning_make: str = ""
    oem_meaning_ref: str = ""
    oem_conflict: bool = False
    # Make unknown (or not listed): the distinct meanings, [(text, [makes], ref)]
    oem_variants: list[tuple[str, list[str], str]] = field(default_factory=list)
    occurrence_count: int | None = None
    notice: str = ""
    refs: list[str] = field(default_factory=list)
    status: str = "ACTIVE"        # ACTIVE | HISTORY (stored, not present now) | PENDING (seen once)

    @property
    def graph_key(self) -> str:
        return self.key if self.kind == "dtc" else self.key.split(" FMI")[0]


@dataclass(slots=True)
class TelemetryFinding:
    signal: str
    value: float
    unit: str
    status: str          # critical_high | high | above_nominal | normal | low | implausible | no_threshold | needs_context
    origin: str
    reference: str = ""  # human-readable threshold summary
    ref: str = ""
    unit_assumed: bool = False

    @property
    def abnormal(self) -> bool:
        return self.status in ("critical_high", "high", "low", "critical_low", "implausible")


@dataclass(slots=True)
class Hypothesis:
    id: str
    title: str
    score: float
    kind: str                       # graph | record | suspected | area | pattern | scenario | monitor
    codes: list[str] = field(default_factory=list)
    support: list[tuple[str, str]] = field(default_factory=list)       # (text, ref)
    against: list[tuple[str, str]] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
    falsifiable: bool = False
    severity_rank: int = 0
    likelihood: float = 0.0
    confidence: str = "low"         # high | medium | low
    refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MissingData:
    key: str
    what_tr: str
    what_en: str
    how_tr: str
    how_en: str
    refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CheckResult:
    """One answered symptom check and the curated effect it had."""

    key: str                        # "<symptom_id>.<check_id>"
    value: str | float
    origin: str
    favor: list[str] = field(default_factory=list)      # hypothesis titles moved up
    rule_out: list[str] = field(default_factory=list)   # hypothesis titles moved down
    note_tr: str = ""
    note_en: str = ""
    ref: str = ""


@dataclass(slots=True)
class Reasoning:
    parsed: ParsedQuery
    codes: list[CodeFact] = field(default_factory=list)
    findings: list[TelemetryFinding] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    risk: str = "GRAY"
    risk_reasons: list[tuple[str, str]] = field(default_factory=list)   # (text, ref)
    safety: list[str] = field(default_factory=list)                      # high_voltage | fire | brakes | steering
    missing: list[MissingData] = field(default_factory=list)
    symptom_records: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    recalls: list[dict[str, Any]] = field(default_factory=list)
    complaints: dict[str, Any] | None = None
    similar_records: list[tuple[str, str, float]] = field(default_factory=list)
    check_results: list[CheckResult] = field(default_factory=list)
    # symptoms an active code points at, kept only for their questions (not "understood" complaints)
    code_symptoms: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    state: OperatingState = field(default_factory=OperatingState)
    scenario_notes: list[str] = field(default_factory=list)   # note-only scenario ids that fired
    # Mode 02 freeze frame: the state and readings at the moment the code was stored
    fault_state: OperatingState = field(default_factory=OperatingState)
    freeze_findings: list[TelemetryFinding] = field(default_factory=list)
    # Mode 06: the ECU's own test verdicts (failed tests become candidates)
    monitor_findings: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------- helpers
def _code_marker(fact: CodeFact) -> str:
    """Evidence marker of a code: an active code is "code:", a stored/pending one says so
    (and does not count as a present fault for confidence)."""
    return {"HISTORY": "code_history", "PENDING": "code_pending"}.get(fact.status, "code") + f":{fact.key}"


def _is_harvest_residue(text: str) -> bool:
    return bool(len(text) > _MAX_TITLE_CHARS or _DESCRIPTION_RE.search(text)
                or _PARTS_LEGEND_RE.search(text) or _FMI_TABLE_RE.search(text) or _PROCEDURE_RE.search(text)
                or _TRUNCATED_RE.search(text))


def _clean_cause(text: Any) -> str | None:
    s = " ".join(str(text or "").split())
    if len(s) < 6 or len(s) > 260 or _FILLER_RE.search(s):
        return None
    if _is_harvest_residue(s):
        return None
    try:
        from src.engine.ai.junk_content_signatures import classify_cause

        if classify_cause(s).is_junk:
            return None
    except Exception:  # noqa: BLE001 — classifier is a filter, never a source
        pass
    return s


def _max_severity(a: str, b: str) -> str:
    ia = _SEVERITY_ORDER.index(a) if a in _SEVERITY_ORDER else 0
    ib = _SEVERITY_ORDER.index(b) if b in _SEVERITY_ORDER else 0
    return a if ia >= ib else b


def _raise_risk(current: str, candidate: str) -> str:
    return candidate if _RISK_ORDER.index(candidate) > _RISK_ORDER.index(current) else current


def _severity_to_risk(severity: str) -> str:
    from src.engine.ai.drive_safety_policy import decide_risk

    return str(decide_risk(severity))


def _difficulty_rank(label: str) -> int:
    f = fold_text(label)
    if "kolay" in f or "easy" in f or "gorsel" in f:
        return 0
    if "orta" in f or "medium" in f:
        return 1
    return 2


# ------------------------------------------------------------ code facts
_OEM_STOP = frozenset({"sensor", "circuit", "malfunction", "the", "and", "for", "fault", "signal", "system",
                       "bank", "with", "not", "too"})


def _content_tokens(text: str) -> set[str]:
    return {t for t in fold_text(text).split() if len(t) > 2 and t not in _OEM_STOP}


def _same_meaning(meaning: str, titles: Iterable[str]) -> bool:
    """True when the make's meaning is the generic record's meaning reworded (token containment >= 0.5)."""
    a = _content_tokens(meaning)
    if not a:
        return True  # nothing to compare: no evidence of a conflict
    for title in titles:
        b = _content_tokens(title)
        if b and len(a & b) / min(len(a), len(b)) >= 0.5:
            return True
    return False


def _apply_oem_meaning(kb: KnowledgeBase, fact: CodeFact, code: str, make: str | None,
                       titles: list[str]) -> None:
    """Pick the vehicle make's meaning of an OEM code whose meaning differs per make.

    Same meaning as the generic record: only noted. Different meaning: the
    generic record describes another make's fault, so its causes, steps,
    symptoms, reference and severity are dropped rather than served for the
    wrong component, and the code is kept out of the root-cause graph. Make
    unknown or not listed: the distinct meanings are listed and the make is
    asked for.
    """
    meanings = kb.dtc_oem_meanings(code)
    if not meanings:
        return
    look = kb.dtc_oem_meaning(code, make)
    if look.found:
        fact.oem_meaning, fact.oem_meaning_make = str(look.record["text"]), str(look.record["make"])
        fact.oem_meaning_ref = look.ref
        fact.refs.append(look.ref)
        if fact.found and not _same_meaning(fact.oem_meaning, titles):
            fact.oem_conflict = True
            fact.title_tr = fact.title_en = f"{fact.oem_meaning} ({fact.oem_meaning_make.title()})"
            fact.description = fact.reference_values = ""
            fact.clean_causes, fact.steps, fact.record_symptoms = [], [], []
            fact.severity, fact.severity_ref = "UNKNOWN", ""
        return
    groups: dict[str, tuple[str, list[str]]] = {}
    for name, text in sorted(meanings.items()):
        key = " ".join(sorted(_content_tokens(text))) or fold_text(text)
        groups.setdefault(key, (text, []))[1].append(name)
    if len(groups) >= 2:
        ranked = sorted(groups.values(), key=lambda g: (-len(g[1]), g[1][0]))
        fact.oem_variants = [(text, names, f"dtc_oem_meanings#{code}.{names[0]}") for text, names in ranked[:4]]


def _dtc_fact(kb: KnowledgeBase, code: str, origin: str, make: str | None = None) -> CodeFact:
    look = kb.dtc(code)
    fact = CodeFact(code, "dtc", origin, look.found)
    if not look.found:
        oem = kb.dtc_oem(code)
        if oem.found:
            entry = oem.record.get("entry") or {}
            fact.oem_text = str((entry.get("description") or {}).get("en") or "")
            fact.oem_makes = list(oem.record.get("makes") or [])
            fact.refs.append(oem.ref)
        try:
            from src.engine.ai.diagnostic_copilot import get_reserved_code_notice

            fact.notice = get_reserved_code_notice(code) or ""
        except Exception:  # noqa: BLE001
            fact.notice = ""
        return fact
    rec: dict[str, Any] = look.record
    fact.refs.append(look.ref)
    try:
        from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

        title = CausalBayesianInferenceEngine.display_title(rec, code)
    except Exception:  # noqa: BLE001
        title = str(rec.get("title") or "")
    fact.title_tr = str(rec.get("title_tr") or title or "")
    fact.title_en = str(rec.get("title_en") or "")
    if not fact.title_en and re.search(r"\(([A-Za-z][^()]{4,})\)\s*$", title):
        fact.title_en = re.search(r"\(([A-Za-z][^()]{4,})\)\s*$", title).group(1)  # type: ignore[union-attr]
    fact.description = str(rec.get("description_en") or "")
    fact.subsystem = str(rec.get("subsystem") or "")
    fact.system_id = kb.system_for_label(fact.subsystem) or ""
    sev = kb.dtc_severity(code, str(rec.get("title") or ""), fact.subsystem)
    if sev.found and sev.record not in (None, "UNKNOWN"):
        fact.severity, fact.severity_ref = str(sev.record), sev.ref
    elif str(rec.get("severity") or "").upper() in _SEVERITY_ORDER:
        fact.severity, fact.severity_ref = str(rec.get("severity")).upper(), f"{look.ref}.severity"
    fact.reference_values = str(rec.get("measurement") or "")
    fact.record_symptoms = [str(s) for s in (rec.get("symptoms") or []) if str(s).strip()][:6]
    for i, cause in enumerate(rec.get("causes") or []):
        clean = _clean_cause(cause)
        if clean:
            fact.clean_causes.append((clean, f"{look.ref}.causes[{i}]"))
    for i, step in enumerate(rec.get("steps") or []):
        if isinstance(step, (list, tuple)) and step and str(step[0]).strip():
            difficulty = str(step[2]) if len(step) > 2 else ""
            action = str(step[0]).strip()
            if len(step) > 1 and str(step[1]).strip():
                action = f"{action} ({str(step[1]).strip()})"
            fact.steps.append((action, difficulty, f"{look.ref}.steps[{i}]"))
    oem = kb.dtc_oem(code)
    if oem.found:
        fact.oem_makes = list(oem.record.get("makes") or [])
    _apply_oem_meaning(kb, fact, code, make, [str(rec.get("title") or ""), fact.title_en, title])
    return fact


def _spn_fact(kb: KnowledgeBase, spn: int, fmi: int | None, origin: str, oc: int | None) -> CodeFact:
    key = f"SPN {spn}" + (f" FMI {fmi}" if fmi is not None else "")
    look = kb.spn(spn, fmi)
    fact = CodeFact(key, "spn", origin, look.found, fmi=fmi,
                    occurrence_count=oc)
    rec: dict[str, Any] | None
    if fmi is None:
        rec = look.record if look.found else None
    else:
        rec = (look.record or {}).get("spn") if look.record else None
        fmi_entry = (look.record or {}).get("fmi") if look.record else None
        if fmi_entry is None:
            generic = kb.fmi_definition(fmi)
            if generic.found:
                fmi_entry = generic.record
                fact.fmi_ref = generic.ref
                fact.fmi_generic = True
        elif (look.record or {}).get("fmi_source") == "fmi_definitions":
            fact.fmi_ref = f"j1939_spn_fmi#fmi_definitions.{fmi}"
            fact.fmi_generic = True
        else:
            fact.fmi_ref = look.ref
        if isinstance(fmi_entry, dict):
            fact.fmi_text_en = str(fmi_entry.get("fmi_name") or fmi_entry.get("name") or "")
            fact.fmi_text_tr = str(fmi_entry.get("fault_title") or fmi_entry.get("description_tr") or "")
            if not fact.fmi_generic:
                sev = str(fmi_entry.get("severity") or "").upper()
                if sev in _SEVERITY_ORDER:
                    fact.severity, fact.severity_ref = sev, f"{look.ref}.severity"
                for i, cause in enumerate(str(fmi_entry.get("causes") or "").split(";")):
                    clean = _clean_cause(cause)
                    if clean:
                        fact.clean_causes.append((clean, f"{look.ref}.causes[{i}]"))
            action = str(fmi_entry.get("diagnostic_action") or "").strip()
            if action:
                fact.steps.append((action, "", fact.fmi_ref or look.ref))
    if rec is None:
        if fact.fmi_ref:
            fact.refs.append(fact.fmi_ref)
        return fact
    spn_ref = f"j1939_spn_fmi#SPN_{spn}"
    fact.refs.append(spn_ref)
    if fact.fmi_ref and fact.fmi_ref not in fact.refs:
        fact.refs.append(fact.fmi_ref)
    fact.title_tr = str(rec.get("title_tr") or "")
    fact.title_en = str(rec.get("name") or "")
    fact.description = str(rec.get("description_en") or rec.get("description") or "")
    fact.subsystem = str(rec.get("subsystem") or "")
    fact.system_id = kb.system_for_label(fact.subsystem) or ""
    if isinstance(rec.get("associated_pgn"), int):
        fact.pgn = int(rec["associated_pgn"])
    fact.pgn_acronym = str(rec.get("pgn_acronym") or "")
    fact.pgn_label = str(rec.get("pgn_label") or "")
    if rec.get("range") or rec.get("unit"):
        rng, unit = str(rec.get("range") or ""), str(rec.get("unit") or "")
        fact.reference_values = rng if unit and unit in rng else f"{rng} {unit}".strip()
    raw_causes = rec.get("causes")
    if not fact.clean_causes and isinstance(raw_causes, list):
        for i, cause in enumerate(raw_causes):
            clean = _clean_cause(cause)
            if clean:
                fact.clean_causes.append((clean, f"{spn_ref}.causes[{i}]"))
    raw_steps = rec.get("steps")
    if isinstance(raw_steps, list):
        for i, step in enumerate(raw_steps[:6]):
            if isinstance(step, (list, tuple)) and step and str(step[0]).strip():
                fact.steps.append((str(step[0]).strip(), str(step[2]) if len(step) > 2 else "", f"{spn_ref}.steps[{i}]"))
            elif isinstance(step, str) and step.strip():
                fact.steps.append((step.strip(), "", f"{spn_ref}.steps[{i}]"))
    return fact


# ------------------------------------------------------- telemetry findings
def _fmt(v: float) -> str:
    return f"{v:g}"


_BATTERY_STATE = {"idle": "running", "running": "running", "load": "running", "off": "off", "cranking": "cranking"}
_STATE_TR = {"running": "motor çalışırken", "off": "motor dururken", "cranking": "marş sırasında"}
_STATE_EN = {"running": "engine running", "off": "engine off", "cranking": "while cranking"}


def _judge_battery(kb: KnowledgeBase, reading: Reading, state: OperatingState) -> TelemetryFinding:
    """BatteryVoltage judged by system voltage and engine state (operator-approved bands)."""
    base = TelemetryFinding(reading.canonical, reading.value, reading.unit, "no_threshold", reading.origin,
                            unit_assumed=reading.unit_assumed)
    spec = kb.operating_scenarios().get("battery_voltage") or {}
    system = state.system_voltage or (24 if reading.value > 18 else 12)
    bands_all = spec.get("bands_24v" if system == 24 else "bands_12v") or {}
    if not bands_all or reading.unit not in ("V", ""):
        return base
    factor = 2.0 if system == 24 else 1.0
    v12 = reading.value / factor  # only for the state guess below; bands are compared in the system's own volts
    mode = _BATTERY_STATE.get(state.engine)
    if mode is None:
        # Engine state unknown: a charging-level voltage proves the engine runs; a very
        # low one is low in every state; anything between needs the engine state.
        if v12 >= 13.2:
            mode = "running"
        elif v12 < 12.0:
            mode = "off"
        else:
            base.status, base.ref = "needs_context", "operating_scenarios#battery_voltage"
            base.reference = f"{system} V: motor durumu bilinmiyor"
            return base
    bands = bands_all.get(mode) or []
    v = reading.value
    for lo, hi, status in bands:
        if (lo is None or v >= lo) and (hi is None or v < hi):
            base.status = status
            break
    normal = next(((lo, hi) for lo, hi, st in bands if st == "normal"), None)
    rng = ""
    if normal:
        lo, hi = normal
        rng = (f"normal {lo:g}–{hi:g} V" if hi is not None else f"normal ≥{lo:g} V")
    base.ref = "operating_scenarios#battery_voltage"
    base.reference = f"{system} V, {_STATE_TR[mode]}: {rng}".strip()
    return base


def _judge_reading(kb: KnowledgeBase, reading: Reading, rpm: float | None,
                   state: OperatingState | None = None) -> TelemetryFinding:
    state = state or OperatingState()
    if reading.canonical == "BatteryVoltage":
        return _judge_battery(kb, reading, state)
    if rpm is None and state.engine == "idle" and reading.canonical == "EngineOilPressure":
        rpm = 750.0  # "at idle" in the text selects the idle band (0-800 rpm) of the oil pressure table
    base = TelemetryFinding(reading.canonical, reading.value, reading.unit, "no_threshold", reading.origin,
                            unit_assumed=reading.unit_assumed)
    mapping = kb.signal_measurement(reading.canonical).record or {}
    tkey = mapping.get("threshold_key") if isinstance(mapping, dict) else None
    look = kb.telemetry_threshold(str(tkey)) if tkey else kb.telemetry_threshold(reading.canonical)
    if not look.found or not isinstance(look.record, dict):
        return base
    rec = look.record
    if str(rec.get("unit") or "").replace("°", "").upper() not in ("", str(reading.unit).replace("°", "").upper()) and not (
        rec.get("unit") == "percent" and reading.unit == "%"
    ):
        return base  # unit mismatch -> never compare
    base.ref = look.ref
    v = reading.value
    crit = rec.get("critical_max")
    warn = rec.get("warning_max")
    nmin, nmax = rec.get("nominal_min"), rec.get("nominal_max")
    parts = []
    if nmin is not None and nmax is not None:
        parts.append(f"nominal {_fmt(float(nmin))}–{_fmt(float(nmax))}")
    if warn is not None:
        parts.append(f"warning >{_fmt(float(warn))}")
    if crit is not None:
        parts.append(f"critical >{_fmt(float(crit))}")
    ranges = [r for r in rec.get("ranges") or [] if isinstance(r, dict)]
    if crit is not None and v > float(crit):
        base.status = "critical_high"
    elif warn is not None and v > float(warn):
        base.status = "high"
    elif nmax is not None and v > float(nmax) and crit is not None:
        base.status = "above_nominal"
    else:
        mins = [r for r in ranges if r.get("min") is not None and r.get("max") is None]
        if mins:
            parts.extend(
                f"min {_fmt(float(r['min']))} @ {r.get('min_rpm')}–{r.get('max_rpm') if r.get('max_rpm') is not None else '∞'} rpm"
                for r in mins)
            lowest = min(float(r["min"]) for r in mins)
            all_rpm = all((r.get("min_rpm") or 0) == 0 and r.get("max_rpm") is None for r in mins)
            if v < lowest:
                base.status = "low"
            elif all_rpm:  # one floor for every engine speed: no rpm context needed
                base.status = "below_nominal" if nmin is not None and v < float(nmin) else "normal"
            elif rpm is None:
                base.status = "needs_context"
            else:
                band = next((r for r in mins if (r.get("min_rpm") or 0) <= rpm <= (r.get("max_rpm") or math.inf)), None)
                base.status = "low" if band is not None and v < float(band["min"]) else "normal"
        else:
            env = next((r for r in ranges if r.get("min") is not None and r.get("max") is not None), None)
            if env is not None and not float(env["min"]) <= v <= float(env["max"]):
                base.status = "implausible"
                parts.append(f"valid {_fmt(float(env['min']))}–{_fmt(float(env['max']))}")
            elif env is not None and v == float(env["min"]) and crit is not None:
                # A reading pinned at the bottom of the signal's range is the
                # classic open/short sensor default (e.g. ECT -40 °C), not a
                # physical temperature: flag it, never call it "normal".
                base.status = "at_limit"
                parts.append(f"range {_fmt(float(env['min']))}–{_fmt(float(env['max']))}")
            elif nmin is not None and v < float(nmin):
                base.status = "below_nominal"
            else:
                base.status = "normal"
    base.reference = "; ".join(parts) + (f" {rec.get('unit')}" if rec.get("unit") else "")
    # Full-load boost bands say nothing at idle: below them is low only under load.
    if reading.canonical == "BoostPressure" and base.status == "below_nominal" and state.engine != "load":
        base.status = "needs_context" if state.engine == "unknown" else "normal"
    return base


def _hv_isolation_finding(kb: KnowledgeBase, readings: dict[str, Reading]) -> TelemetryFinding | None:
    iso = readings.get("IsolationResistance")
    pack = readings.get("HVPackVoltage")
    if iso is None or pack is None or pack.value <= 0:
        return None
    look = kb.hv_threshold("min_isolation_resistance_dc")
    if not look.found:
        return None
    limit = float(look.record["value"])
    ohm_per_volt = iso.value * 1000.0 / pack.value
    status = "critical_low" if ohm_per_volt < limit else "normal"
    return TelemetryFinding(
        "IsolationResistance/HVPackVoltage", round(ohm_per_volt, 1), "Ω/V", status, "computed",
        reference=f"≥ {_fmt(limit)} Ω/V ({look.record.get('authority', '')} {look.record.get('clause', '')})".strip(),
        ref=look.ref,
    )


# ------------------------------------------------------------- hypotheses
def _node_fmi_ok(node: Any, code_key: str, fmi: int | None) -> tuple[bool, bool]:
    """(compatible, exact_fmi_match) for a node against one active code."""
    from src.engine.ai.hypothesis_engine import _split_code_qualifier

    declared = [q for c, q in (_split_code_qualifier(x) for x in node.expected_dtcs) if c == code_key]
    if not declared or all(q is None for q in declared):
        return True, False
    if fmi is None:
        return True, False
    return (fmi in declared), (fmi in declared)


def _signal_evidence(kb: KnowledgeBase, hyp: Hypothesis, node: Any, by_signal: dict[str, TelemetryFinding]) -> None:
    done: set[str] = set()
    for raw in node.evidence_signals:
        canon = kb.canonical_signal(raw)
        if canon in done:
            continue  # several graph spellings of one physical signal count once
        done.add(canon)
        f = by_signal.get(canon)
        if f is None:
            if canon not in hyp.missing_signals:
                hyp.missing_signals.append(canon)
            continue
        if f.abnormal:
            hyp.score += 1.5
            hyp.support.append((f"signal:{canon}|{_fmt(f.value)}|{f.unit}|{f.status}", f.ref or "input"))
        elif f.status == "normal":
            hyp.score -= 1.0
            hyp.against.append((f"signal:{canon}|{_fmt(f.value)}|{f.unit}|normal", f.ref or "input"))
    done_contra: set[str] = set()
    for raw in node.contradicting_signals:
        canon = kb.canonical_signal(raw)
        f = by_signal.get(canon)
        if f is None or canon in done_contra:
            continue
        done_contra.add(canon)
        if f.status in ("implausible", "at_limit"):
            hyp.score += 1.0
            hyp.support.append((f"signal:{canon}|{_fmt(f.value)}|{f.unit}|{f.status}", f.ref or "input"))
        elif f.status in ("normal", "above_nominal", "high", "critical_high", "low"):
            hyp.score -= 1.5
            hyp.against.append((f"signal:{canon}|{_fmt(f.value)}|{f.unit}|plausible", f.ref or "input"))


# SAE J1939-73 FMI families: "data valid but out of range" means the sensor
# works and the value is real; the electrical families mean the reading itself
# is not trustworthy. Used only to re-order graph nodes that are clearly of one
# kind (sensor-fault nodes declare a contradicting signal, physical-fault nodes
# declare evidence signals only).
VALID_DATA_FMIS = frozenset({0, 1, 15, 16, 17, 18})
ELECTRICAL_FMIS = frozenset({3, 4, 5, 6})


def _fmi_semantics(h: Hypothesis, node: Any, fact: CodeFact) -> None:
    if fact.fmi is None:
        return
    sensor_node = bool(node.contradicting_signals)
    physical_node = bool(node.evidence_signals) and not sensor_node
    ref = fact.fmi_ref or (fact.refs[0] if fact.refs else "")
    if fact.fmi in ELECTRICAL_FMIS:
        if sensor_node:
            h.score += 1.0
            h.support.append((f"fmi:{fact.fmi}|electrical", ref))
        elif physical_node:
            h.score -= 1.0
            h.against.append((f"fmi:{fact.fmi}|electrical", ref))
    elif fact.fmi in VALID_DATA_FMIS:
        if physical_node:
            h.score += 1.0
            h.support.append((f"fmi:{fact.fmi}|valid_data", ref))
        elif sensor_node:
            h.score -= 1.0
            h.against.append((f"fmi:{fact.fmi}|valid_data", ref))


def _area(r: Reasoning, hyps: dict[str, Hypothesis], sid: str, i: int, create: bool) -> Hypothesis | None:
    """Area hypothesis for subsystem ``i`` of symptom ``sid`` (made on demand when ``create``)."""
    hid = f"canonical_symptoms#{sid}.subsystems[{i}]"
    if hid in hyps or not create:
        return hyps.get(hid)
    rec = next((rec for s, rec in r.symptom_records + r.code_symptoms if s == sid), None)
    labels = [str(x).strip() for x in (rec or {}).get("subsystems") or []]
    if i >= len(labels) or not labels[i]:
        return None
    hyps[hid] = Hypothesis(hid, labels[i], 0.5 - 0.1 * i, "area",
                           support=[(f"complaint:{sid}", f"canonical_symptoms#{sid}")],
                           refs=[f"canonical_symptoms#{sid}"])
    return hyps[hid]


def _complaint_areas(r: Reasoning, hyps: dict[str, Hypothesis]) -> None:
    """Complaint named no cause the graph knows: list the subsystems the symptom record points at."""
    for sid, rec in r.symptom_records:
        for i in range(min(3, len(rec.get("subsystems") or []))):
            _area(r, hyps, sid, i, create=True)


def _check_effect(check: dict[str, Any], value: str | float) -> dict[str, Any] | None:
    if check.get("kind") == "measurement":
        if not isinstance(value, float):
            return None
        for band in check.get("bands") or []:
            lo, hi = band.get("min"), band.get("max")
            if (lo is None or value >= lo) and (hi is None or value < hi):
                return dict(band)
        return None
    outcome = (check.get("outcomes") or {}).get(value) if isinstance(value, str) else None
    return dict(outcome) if isinstance(outcome, dict) else None


def _apply_answers(kb: KnowledgeBase, r: Reasoning, hyps: dict[str, Hypothesis]) -> None:
    """Operator answers move the symptom's own subsystems/codes up or down (curated effects only)."""
    for a in r.parsed.answers:
        check = next((c for c in kb.symptom_checks(a.symptom_id) if c.get("id") == a.check_id), None)
        if check is None:
            continue
        ref = f"symptom_checks#{a.key}"
        result = CheckResult(a.key, a.value, a.origin, ref=ref)
        r.check_results.append(result)
        effect = _check_effect(check, a.value)
        if effect is None:
            continue
        result.note_tr, result.note_en = str(effect.get("note_tr") or ""), str(effect.get("note_en") or "")
        marker = f"check:{a.key}|{a.value:g}" if isinstance(a.value, float) else f"check:{a.key}|{a.value}"
        for bucket, sign in (("favor", 1.0), ("rule_out", -1.0)):
            for target in effect.get(bucket) or []:
                if isinstance(target, int):
                    found = [h for h in [_area(r, hyps, a.symptom_id, target, create=sign > 0)] if h is not None]
                else:
                    found = [h for h in hyps.values() if str(target) in h.codes]
                for h in found:
                    h.score += 1.5 * sign
                    (h.support if sign > 0 else h.against).append((marker, ref))
                    (result.favor if sign > 0 else result.rule_out).append(h.title)


def _common_causes(kb: KnowledgeBase, r: Reasoning, active: dict[str, CodeFact],
                   hyps: dict[str, Hypothesis]) -> None:
    """Several active codes, one shared fault: a curated rule adds the candidate
    that explains them all (parsimony). Its score grows with every code it
    explains, so it outranks the per-code causes only when it explains more."""
    facts = list(active.values())
    by_signal = {f.signal: f for f in r.findings}
    for rule in kb.reasoning_rules():
        spec = rule.get("match") or {}
        codes_re = re.compile(str(spec["codes_regex"])) if spec.get("codes_regex") else None
        title_re = re.compile(str(spec["title_regex"])) if spec.get("title_regex") else None
        matched = [f.key for f in facts
                   if (codes_re is None or codes_re.search(f.key))
                   and (title_re is None or title_re.search(fold_text(f"{f.title_tr} {f.title_en}")))]
        required = [str(c) for c in spec.get("required") or []]
        low_sig = by_signal.get(str(spec.get("signal_low") or ""))
        signal_hit = low_sig is not None and low_sig.status in ("low", "critical_low")
        if required and not set(required) <= set(active) and not signal_hit:
            continue
        if required and not spec.get("explains_all") and not (codes_re or title_re):
            matched = [c for c in matched if c in required]
        units = len(set(matched)) + (1 if signal_hit else 0)
        if units < int(spec.get("min_codes") or 2) or not matched:
            continue
        ref = f"reasoning_rules#{rule['id']}"
        # A graph node linked to each of these codes collects their points one by
        # one ("plug/coil" for three misfiring cylinders); the shared cause that
        # explains the same codes is checked before it.
        joint = [g.score for g in hyps.values() if g.kind == "graph" and len(set(g.codes) & set(matched)) >= 2]
        score = max([3.0 + 1.0 * (units - 1)] + [x + 0.5 for x in joint])
        h = Hypothesis(ref, str(rule.get("title_tr") or rule["id"]), score, "pattern",
                       codes=sorted(set(matched)), refs=[ref])
        h.support.append((f"pattern:{rule['id']}|{units}", ref))
        h.support += [(_code_marker(active[c]), active[c].refs[0] if active[c].refs else ref) for c in sorted(set(matched))]
        if signal_hit and low_sig is not None:
            h.support.append((f"signal:{low_sig.signal}|{_fmt(low_sig.value)}|{low_sig.unit}|{low_sig.status}",
                              low_sig.ref or "input"))
        h.severity_rank = max((_SEVERITY_ORDER.index(active[c].severity) for c in matched
                               if active[c].severity in _SEVERITY_ORDER), default=0)
        hyps[ref] = h


def _scenarios(kb: KnowledgeBase, r: Reasoning, hyps: dict[str, Hypothesis], st: OperatingState,
               findings: list[TelemetryFinding], origin: str = "live") -> None:
    """State-aware rules: in state S, signal X above/below V means Y (operating_scenarios.json).

    Run once for the live readings in the live state and once for the freeze
    frame in the fault-moment state (``origin="freeze_frame"``)."""
    if not st.known:
        return
    by_signal = {f.signal: f for f in findings}
    for sc in kb.operating_scenarios().get("scenarios") or []:
        when = sc.get("when") or {}
        if when.get("engine") and st.engine not in when["engine"]:
            continue
        if when.get("thermal") and st.thermal not in when["thermal"]:
            continue
        f: TelemetryFinding | None
        if when.get("sum"):  # a combined value, e.g. STFT + LTFT
            parts = [by_signal.get(str(name)) for name in when["sum"]]
            found = [p for p in parts if p is not None and p.status != "implausible"]
            if len(found) != len(parts):
                continue
            f = TelemetryFinding("+".join(str(n) for n in when["sum"]), round(sum(p.value for p in found), 3),
                                 found[0].unit, "normal", origin)
        else:
            f = by_signal.get(str(when.get("signal") or ""))
        if f is None or f.status == "implausible":
            continue
        value = f.value
        if when.get("per_12v") and (st.system_voltage or (24 if value > 18 else 12)) == 24:
            value = value / 2.0
        if ("lt" in when and not value < float(when["lt"])) or ("gt" in when and not value > float(when["gt"])):
            continue
        ref = f"operating_scenarios#{sc['id']}"
        if sc.get("note_only"):
            if origin == "live":
                r.scenario_notes.append(str(sc["id"]))
            continue
        marker = f"scenario:{sc['id']}|{st.engine}|{st.thermal}|{f.signal}|{_fmt(f.value)}|{f.unit}|{origin}"
        confirms = set(sc.get("codes") or [])
        for g in hyps.values():  # the causes of the ACTIVE codes this reading confirms gain it as evidence
            active_code = any(m.startswith("code:") for m, _ in g.support)
            if g.kind != "scenario" and active_code and confirms & {c.split(" FMI")[0] for c in g.codes}:
                g.score += 1.0
                g.support.append((marker, ref))
        if ref in hyps:
            hyps[ref].support.append((marker, ref))
            continue
        h = Hypothesis(ref, str(sc.get("title_tr") or sc["id"]), 3.0 + (0.5 if f.abnormal else 0.0), "scenario",
                       support=[(marker, ref)], refs=[ref])
        hyps[ref] = h


# SAE J1979 OBDMID -> the codes the same monitor sets when it fails (for linking a
# failed test to the graph's causes; standard OBDMID assignments).
_MID_CODES: dict[int, tuple[str, ...]] = {
    0x01: ("P0133",), 0x02: ("P0139",), 0x05: ("P0153",), 0x06: ("P0159",),
    0x21: ("P0420",), 0x22: ("P0430",), 0x31: ("P0401",), 0x32: ("P0401",),
    0x39: ("P0455",), 0x3A: ("P0456",), 0x3B: ("P0442",), 0x3C: ("P0456",), 0x3D: ("P0496",),
    0x41: ("P0135",), 0x42: ("P0141",), 0x45: ("P0155",), 0x46: ("P0161",),
    0x71: ("P0410",), 0x81: ("P0171", "P0172"), 0x82: ("P0174", "P0175"), 0xA1: ("P0300",),
    **{0xA1 + n: (f"P03{n:02d}",) for n in range(1, 13)},
}


def monitor_names(kb: KnowledgeBase, mid: int, tid: int) -> tuple[str, str, str, str]:
    """(monitor TR, monitor EN, test TR, test EN) from obd_mode06_database.json ('' when unknown)."""
    db = kb._json_source("obd_mode06")  # noqa: SLF001 — same package
    mon = ((db or {}).get("monitors") or {}).get(f"0x{mid:02X}") if isinstance(db, dict) else None
    if not isinstance(mon, dict):
        return "", "", "", ""
    test: dict[str, Any] = next((t for t in mon.get("tests") or []
                                 if str(t.get("tid_hex", "")).upper() == f"0X{tid:02X}"), {})
    return (str(mon.get("name_tr") or mon.get("name") or ""), str(mon.get("name") or ""),
            str(test.get("name_tr") or test.get("name") or ""), str(test.get("name") or ""))


def j1939_test_names(kb: KnowledgeBase, spn: int) -> tuple[str, str, str]:
    """(SPN title TR, SPN title EN, ref) from the J1939 SPN database; ('', '', '') when the SPN is unknown."""
    look = kb.spn(spn)
    rec = look.record if look.found and isinstance(look.record, dict) else None
    if rec is None:
        return "", "", ""
    return str(rec.get("title_tr") or rec.get("name") or ""), str(rec.get("name") or ""), look.ref


def monitor_ref(kb: KnowledgeBase, m: dict[str, Any]) -> str:
    """Citation for one monitor record: the OBDMID record (Mode 06) or the SPN record (J1939 DM30)."""
    if "spn" in m:
        return j1939_test_names(kb, m["spn"])[2]
    return f"obd_mode06#0x{m['mid']:02X}"


def monitor_marker(m: dict[str, Any]) -> str:
    lo = "" if m["min"] is None else _fmt(m["min"])
    hi = "" if m["max"] is None else _fmt(m["max"])
    if "spn" in m:
        return f"monitor_j1939:{m['spn']}|{m['fmi']}|{m['tid']}|{_fmt(m['value'])}|{lo}|{hi}|{m['unit']}"
    return f"monitor:{m['mid']}|{m['tid']}|{_fmt(m['value'])}|{lo}|{hi}|{m['unit']}"


def _monitor_candidates(kb: KnowledgeBase, r: Reasoning, hyps: dict[str, Hypothesis]) -> None:
    """A failed Mode 06 / DM30 test is the ECU saying 'this system fails MY limit' — even before a code."""
    for m in r.parsed.monitors:
        margin = None
        if m["passed"] and m["min"] is not None and m["max"] is not None and m["max"] - m["min"] > 0:
            span = m["max"] - m["min"]
            margin = round(1.0 - max((m["value"] - m["min"]) / span, (m["max"] - m["value"]) / span), 3)
        r.monitor_findings.append(dict(m, margin=margin))
        if m["passed"]:
            continue
        ref = monitor_ref(kb, m)
        if not ref:
            continue  # unknown SPN: shown as a row, but nothing to cite for a candidate
        marker = monitor_marker(m)
        if "spn" in m:
            codes = [f"SPN {m['spn']}"]
            mon_tr = j1939_test_names(kb, m["spn"])[0]
        else:
            codes = list(_MID_CODES.get(m["mid"], ()))
            mon_tr = monitor_names(kb, m["mid"], m["tid"])[0]
        h = hyps.get(ref) or Hypothesis(ref, f"ECU testi başarısız: {mon_tr or ref}", 4.0, "monitor",
                                        codes=codes, refs=[ref])
        h.support.append((marker, ref))
        hyps[ref] = h
        # The graph's causes for the code this monitor sets are now ECU-confirmed suspects.
        for code in codes:
            for node in [n for n in kb.graph_nodes_for_code(code) if not _is_harvest_residue(n.title)][:3]:
                g = hyps.get(node.id)
                if g is None:
                    g = Hypothesis(node.id, node.title, 0.0, "suspected", refs=[f"root_cause_graph#{node.id}"],
                                   falsifiable=bool(node.evidence_signals or node.contradicting_signals))
                    hyps[node.id] = g
                g.score += 2.5
                if code not in g.codes:
                    g.codes.append(code)
                g.support.append((marker, ref))


def _build_hypotheses(kb: KnowledgeBase, r: Reasoning) -> None:
    by_signal = {f.signal: f for f in r.findings}
    hyps: dict[str, Hypothesis] = {}
    # A code whose generic record means another make's fault has no graph causes for this vehicle.
    active: dict[str, CodeFact] = {c.graph_key: c for c in r.codes if c.found and not c.oem_conflict}
    complaint_codes: dict[str, str] = {}
    for sid, rec in r.symptom_records:
        for code in rec.get("candidate_dtcs") or []:
            from src.engine.ai.hypothesis_engine import _split_code_qualifier

            complaint_codes.setdefault(_split_code_qualifier(str(code))[0], sid)

    # (1) graph nodes for active codes
    for gkey, fact in active.items():
        for node in kb.graph_nodes_for_code(gkey):
            ok, exact = _node_fmi_ok(node, gkey, fact.fmi)
            if not ok or _is_harvest_residue(node.title):
                continue
            h = hyps.get(node.id)
            if h is None:
                h = Hypothesis(node.id, node.title, 0.0, "graph", refs=[f"root_cause_graph#{node.id}"],
                               falsifiable=bool(node.evidence_signals or node.contradicting_signals))
                hyps[node.id] = h
                _signal_evidence(kb, h, node, by_signal)
            # a stored (HISTORY) or unconfirmed (PENDING) code is weaker evidence than a present one
            h.score += (3.0 if fact.status == "ACTIVE" else 2.0) + (0.5 if exact else 0.0)
            h.codes.append(fact.key)
            h.severity_rank = max(h.severity_rank, _SEVERITY_ORDER.index(fact.severity) if fact.severity in _SEVERITY_ORDER else 0)
            _fmi_semantics(h, node, fact)
            h.support.append((_code_marker(fact), fact.refs[0] if fact.refs else f"root_cause_graph#{node.id}"))
            if gkey in complaint_codes:
                h.score += 0.5
                sid = complaint_codes[gkey]
                h.support.append((f"complaint:{sid}", f"canonical_symptoms#{sid}"))

    # (2) record-cause fallback for active codes the graph does not cover
    for fact in active.values():
        if any(fact.key in h.codes for h in hyps.values()):
            continue
        for i, (text, ref) in enumerate(fact.clean_causes[:4]):
            hid = f"{ref}"
            if hid in hyps:
                continue
            hyps[hid] = Hypothesis(hid, text, 2.0 - 0.05 * i, "record", codes=[fact.key],
                                   support=[(_code_marker(fact), fact.refs[0] if fact.refs else ref)], refs=[ref])

    # (3) complaint-only candidates (codes suggested by the symptom, not active)
    for code, sid in complaint_codes.items():
        if code in active:
            continue
        for node in [n for n in kb.graph_nodes_for_code(code) if not _is_harvest_residue(n.title)][:3]:
            h = hyps.get(node.id)
            if h is None:
                h = Hypothesis(node.id, node.title, 0.0, "suspected", refs=[f"root_cause_graph#{node.id}"],
                               falsifiable=bool(node.evidence_signals or node.contradicting_signals))
                hyps[node.id] = h
                _signal_evidence(kb, h, node, by_signal)
            h.score += 1.0
            if code not in h.codes:
                h.codes.append(code)
            h.support.append((f"complaint_code:{sid}|{code}", f"canonical_symptoms#{sid}"))
            # The complaint describes a physical reading ("hararet", "yağ lambası").
            # A node that declares that signal as its evidence is the real fault,
            # the dangerous one; it is checked before the sensor-fault nodes.
            implied = _SYMPTOM_SIGNAL.get(sid)
            if implied and not any(m.startswith("complaint_signal:") for m, _ in h.support) and \
                    kb.canonical_signal(implied) in {kb.canonical_signal(x) for x in node.evidence_signals}:
                h.score += 0.5
                h.support.append((f"complaint_signal:{sid}|{implied}", f"root_cause_graph#{node.id}"))

    # (3a) what the readings mean in the operating state they were taken in
    _scenarios(kb, r, hyps, r.state, r.findings)
    _scenarios(kb, r, hyps, r.fault_state, r.freeze_findings, origin="freeze_frame")
    # (3c) the ECU's own failed monitor tests (Mode 06)
    _monitor_candidates(kb, r, hyps)

    # (3b) several active codes explained by one shared fault
    if len(active) >= 1:
        _common_causes(kb, r, active, hyps)

    # (4) no cause the graph knows: name the subsystems to inspect, never a cause.
    # A complaint with no code read and fewer than three candidates also lists
    # the symptom's own subsystems, so a lone off-target node never stands alone.
    if r.symptom_records and (not hyps or (not active and not r.findings and len(hyps) < 3)):
        _complaint_areas(r, hyps)
    # (5) the operator's answers to the symptom checks
    _apply_answers(kb, r, hyps)

    for h in hyps.values():
        if h.kind == "record":
            rec_fact = active.get(h.codes[0].split(" FMI")[0]) if h.codes else None
            if rec_fact is not None and rec_fact.severity in _SEVERITY_ORDER:
                h.severity_rank = _SEVERITY_ORDER.index(rec_fact.severity)
    # Ties are broken by the severity of the code behind the cause, so the
    # cause of the most dangerous active code is read first.
    # ... then by the complaint that named them: the best-matched symptom first.
    sym_rank = {sid: i for i, (sid, _) in enumerate(r.symptom_records)}

    def complaint_rank(h: Hypothesis) -> int:
        ranks = [sym_rank.get(m.split(":", 1)[1].split("|")[0], 99) for m, _ in h.support
                 if m.startswith(("complaint:", "complaint_code:"))]
        return min(ranks, default=99)

    ranked = sorted(hyps.values(), key=lambda h: (-h.score, -h.severity_rank, complaint_rank(h), h.title))
    # de-duplicate identical titles (same cause text reached through two codes)
    seen: set[str] = set()
    unique: list[Hypothesis] = []
    for h in ranked:
        t = fold_text(h.title)
        if t in seen:
            continue
        seen.add(t)
        unique.append(h)
    top = unique[:5]
    if top:
        m = max(h.score for h in top)
        exps = [math.exp(h.score - m) for h in top]
        total = sum(exps)
        # Identical scores carry no ranking information: show no percentage
        # rather than an even split that reads like a measured probability.
        ranked_scores = {round(h.score, 6) for h in top if h.kind != "area"}
        informative = len(ranked_scores) > 1 or sum(h.kind != "area" for h in top) == 1
        for h, e in zip(top, exps, strict=True):
            h.likelihood = round(e / total, 3) if h.kind != "area" and informative else 0.0
            has_code = any(s.startswith("code:") for s, _ in h.support)
            has_signal = any(s.startswith("signal:") for s, _ in h.support)
            has_check = any(s.startswith(("check:", "scenario:", "monitor:")) for s, _ in h.support)
            if has_code and has_signal and not h.against:
                h.confidence = "high"
            elif (has_code and h.kind in ("graph", "pattern") and not h.against) or ((has_signal or has_check) and not h.against):
                h.confidence = "medium"
            else:
                h.confidence = "low"
    r.hypotheses = top


# ----------------------------------------------------------------- missing
def _missing(kb: KnowledgeBase, r: Reasoning) -> None:
    pq = r.parsed
    out: dict[str, MissingData] = {}
    if not pq.dtcs and not pq.spns:
        out["codes"] = MissingData(
            "codes", "Arıza kodları okunmadı.", "No fault codes were read.",
            "Tarama ekranından kodları okuyun (binek: OBD-II Mode 03; ağır vasıta: J1939 DM1).",
            "Read codes from the scan screen (cars: OBD-II Mode 03; trucks: J1939 DM1).",
            ["copilot_glossary#DTC", "copilot_glossary#DM1"])
    wanted: list[str] = []
    for h in r.hypotheses[:3]:
        for s in h.missing_signals:
            if s not in wanted:
                wanted.append(s)
    measured = {f.signal for f in r.findings}
    for f in r.findings:
        if f.status == "needs_context" and "EngineSpeed" not in measured and "EngineSpeed" not in wanted:
            wanted.insert(0, "EngineSpeed")
    readings = {x.canonical for x in pq.readings}
    if "IsolationResistance" in readings and "HVPackVoltage" not in readings:
        wanted.insert(0, "HVPackVoltage")
    for sig in wanted[:4]:
        look = kb.signal_measurement(sig)
        if not look.found:
            continue
        rec = look.record
        how_tr, how_en = _how_to(rec)
        out[f"signal:{sig}"] = MissingData(
            f"signal:{sig}",
            f"{rec.get('label_tr') or sig} ölçümü yok ({sig}).",
            f"No {rec.get('label_en') or sig} reading ({sig}).",
            how_tr, how_en, [look.ref])
    for rd in pq.readings:
        if rd.unit_assumed:
            out[f"unit:{rd.canonical}"] = MissingData(
                f"unit:{rd.canonical}",
                f"{rd.canonical} değeri birimsiz yazıldı; {rd.unit} varsayıldı.",
                f"{rd.canonical} was given without a unit; {rd.unit} was assumed.",
                "Değeri birimiyle yazın (ör. 112 °C, 2,5 bar).", "Give the value with its unit (e.g. 112 °C, 2.5 bar).",
                [f"signal_measurement_map#{rd.canonical}"])
    for key in pq.unknown_telemetry:
        out[f"unknown:{key}"] = MissingData(
            f"unknown:{key}", f"'{key}' adlı sinyal tanınmadı; değerlendirilmedi.",
            f"Signal '{key}' is not recognised and was not evaluated.",
            "Sinyali bilinen bir adla gönderin (ör. CoolantTemp, BatteryVoltage).",
            "Send it under a known name (e.g. CoolantTemp, BatteryVoltage).", [])
    for c in r.codes:
        if c.oem_variants:
            listing = "; ".join(f"{'/'.join(n.title() for n in names[:3])}: {text}" for text, names, _ref in c.oem_variants)
            out[f"make:{c.key}"] = MissingData(
                f"make:{c.key}",
                f"{c.key} üreticiye özel bir koddur ve anlamı markaya göre değişir — {listing}.",
                f"{c.key} is a manufacturer code whose meaning depends on the make — {listing}.",
                "Aracın markasını seçin ya da VIN okutun; copilot o markanın anlamını kullanır.",
                "Select the vehicle's make or read the VIN; the copilot then uses that make's meaning.",
                [ref for _t, _n, ref in c.oem_variants])
        if c.kind == "spn" and c.fmi is None and c.found:
            out[f"fmi:{c.key}"] = MissingData(
                f"fmi:{c.key}", f"{c.key} için FMI (arıza tipi) bilinmiyor.", f"FMI (failure mode) of {c.key} is unknown.",
                "DM1 ekranındaki FMI numarasını da girin; neden listesi ona göre daralır.",
                "Also enter the FMI from the DM1 screen; it narrows the causes.", ["copilot_glossary#FMI"])
    r.missing = list(out.values())


def _how_to(rec: dict[str, Any]) -> tuple[str, str]:
    if rec.get("how_to_tr"):
        return str(rec["how_to_tr"]), str(rec.get("how_to_en") or rec["how_to_tr"])
    tr: list[str] = []
    en: list[str] = []
    j = rec.get("j1939") or {}
    if j.get("spn") is not None:
        pgn = f"PGN {j.get('pgn')}" + (f" ({j.get('pgn_acronym')})" if j.get("pgn_acronym") else "")
        tr.append(f"Ağır vasıta (J1939): {pgn} içinde SPN {j['spn']} canlı okunur")
        en.append(f"Heavy-duty (J1939): live SPN {j['spn']} in {pgn}")
    o = rec.get("obd") or {}
    if o.get("pid"):
        tr.append(f"binek (OBD-II): Mode {o.get('service')} PID {o.get('pid')} ({o.get('name')})")
        en.append(f"car (OBD-II): Mode {o.get('service')} PID {o.get('pid')} ({o.get('name')})")
    if not tr:
        return ("Bu sinyal için standart bir parametre kaydı yok; üretici servis aracı gerekir.",
                "No standard parameter record for this signal; an OEM service tool is needed.")
    return "; ".join(tr) + ".", "; ".join(en) + "."


# -------------------------------------------------------------- top level
def reason(parsed: ParsedQuery, kb: KnowledgeBase, *, include_recalls: bool = True) -> Reasoning:
    r = Reasoning(parsed=parsed)
    for dm in parsed.dtcs:
        r.codes.append(_dtc_fact(kb, dm.code, dm.origin, parsed.vehicle_make))
        r.codes[-1].status = dm.status
    for sm in parsed.spns:
        r.codes.append(_spn_fact(kb, sm.spn, sm.fmi, sm.origin, sm.occurrence_count))
        r.codes[-1].status = sm.status
    for sym in parsed.symptoms:
        look = kb.symptom(sym.symptom_id)
        if look.found:
            r.symptom_records.append((sym.symptom_id, look.record))
    known = {sid for sid, _ in r.symptom_records}
    for sid in code_check_symptoms([c.key.split(" FMI")[0] for c in r.codes if c.found and not c.oem_conflict], kb):
        if sid not in known:
            r.code_symptoms.append((sid, kb.symptom(sid).record))

    # telemetry
    rpm_reading = next((x for x in parsed.readings if x.canonical == "EngineSpeed"), None)
    rpm = rpm_reading.value if rpm_reading else None
    r.state = infer_state(parsed)
    for reading in parsed.readings:
        if reading.canonical in HV_SIGNALS:
            r.findings.append(TelemetryFinding(reading.canonical, reading.value, reading.unit, "no_threshold",
                                               reading.origin, unit_assumed=reading.unit_assumed))
            continue
        r.findings.append(_judge_reading(kb, reading, rpm, r.state))
    if parsed.freeze_readings:
        r.fault_state = infer_state_from(parsed.freeze_readings)
        ff_rpm = next((x.value for x in parsed.freeze_readings if x.canonical == "EngineSpeed"), None)
        r.freeze_findings = [_judge_reading(kb, x, ff_rpm, r.fault_state) for x in parsed.freeze_readings
                             if x.canonical not in HV_SIGNALS]
    hv = _hv_isolation_finding(kb, {x.canonical: x for x in parsed.readings})
    if hv is not None:
        r.findings.append(hv)

    _build_hypotheses(kb, r)

    # urgency
    risk = "GRAY"
    for cf in r.codes:
        if not cf.found:
            continue
        if cf.status == "HISTORY":
            # not present now: it explains, it does not make the vehicle unsafe this minute
            r.risk_reasons.append((f"history:{cf.key}", cf.refs[0] if cf.refs else ""))
            continue
        cr = _severity_to_risk(cf.severity)
        if cf.status == "PENDING" and cr == "RED":
            cr = "YELLOW"  # seen once, not confirmed by the ECU yet
        if cr != "GRAY":
            r.risk_reasons.append((f"severity:{cf.key}|{cf.severity}", cf.severity_ref or (cf.refs[0] if cf.refs else "")))
        risk = _raise_risk(risk, cr)
        if cf.kind == "dtc" and cf.key.startswith("P0A"):
            risk = "RED"
            r.risk_reasons.append((f"ev:{cf.key}", cf.refs[0] if cf.refs else ""))
    for f in r.findings:
        if f.signal in BRAKE_AIR_SIGNALS and f.status in ("low", "critical_low"):
            risk = "RED"
            r.risk_reasons.append((f"signal:{f.signal}|{_fmt(f.value)}|{f.unit}|{f.status}", f.ref))
        elif f.signal == "BatteryVoltage" and f.abnormal:
            # a battery/charging fault strands the vehicle but is not a stop-now hazard by itself
            risk = _raise_risk(risk, "YELLOW")
            r.risk_reasons.append((f"signal:{f.signal}|{_fmt(f.value)}|{f.unit}|{f.status}", f.ref))
        elif f.status in ("critical_high", "critical_low"):
            risk = "RED"
            r.risk_reasons.append((f"signal:{f.signal}|{_fmt(f.value)}|{f.unit}|{f.status}", f.ref))
        elif f.status in ("high", "low"):
            risk = _raise_risk(risk, "YELLOW")
            r.risk_reasons.append((f"signal:{f.signal}|{_fmt(f.value)}|{f.unit}|{f.status}", f.ref))
    for m in r.monitor_findings:
        if not m["passed"]:
            risk = _raise_risk(risk, "YELLOW")
            r.risk_reasons.append((monitor_marker(m), monitor_ref(kb, m) or "template:j1939_dm30"))
    for lamps in parsed.dm1_lamps:
        if lamps.red_stop:
            risk = "RED"
            r.risk_reasons.append(("lamp:red_stop", "canboat_pgn#PGN_65226"))
        elif lamps.amber_warning or lamps.protect or lamps.mil:
            risk = _raise_risk(risk, "YELLOW")
            r.risk_reasons.append(("lamp:warning", "canboat_pgn#PGN_65226"))

    # safety categories
    safety: list[str] = []

    def add(cat: str) -> None:
        if cat not in safety:
            safety.append(cat)

    for cat in parsed.safety_terms:
        add(cat)
    for cf in r.codes:
        if cf.kind == "dtc" and cf.key.startswith("P0A"):
            add("high_voltage")
        if cf.system_id in BRAKE_SYSTEMS:
            add("brakes")
        if cf.system_id in STEERING_SYSTEMS:
            add("steering")
    for sid, rec in r.symptom_records:
        if rec.get("domain") == "EV_HV":
            add("high_voltage")
        if sid in FIRE_SYMPTOMS:
            add("fire")
        if sid in BRAKE_SYMPTOMS:
            add("brakes")
        if sid in STEERING_SYMPTOMS:
            add("steering")
    if any(x.canonical in HV_SIGNALS for x in parsed.readings):
        add("high_voltage")
    if any(f.signal in BRAKE_AIR_SIGNALS and f.abnormal for f in r.findings):
        add("brakes")
    order = ("fire", "high_voltage", "brakes", "steering")
    r.safety = sorted(safety, key=lambda s: order.index(s) if s in order else 99)
    if "fire" in r.safety or ("high_voltage" in r.safety and any(sid in FIRE_SYMPTOMS for sid, _ in r.symptom_records)):
        risk = "RED"
        r.risk_reasons.append(("safety:fire", "symptom_lexicon#safety_terms"))
    elif r.safety and risk in ("GRAY", "GREEN"):
        risk = "YELLOW"
        r.risk_reasons.append((f"safety:{','.join(r.safety)}", "symptom_lexicon#safety_terms"))
    sids = {sid for sid, _ in r.symptom_records}
    for cat in ("brakes", "steering"):
        stop_terms = set(parsed.safety_terms.get(cat, ())) - _SAFETY_LAMP_TERMS
        if stop_terms and not sids & _SAFETY_LAMP_SYMPTOMS[cat]:
            risk = "RED"
            r.risk_reasons.append((f"stop_safety:{cat}", "symptom_lexicon#safety_terms"))
    measured = {f.signal: f.status for f in r.findings}
    for sid in sorted(sids & STOP_SYMPTOMS):
        status = measured.get(_SYMPTOM_SIGNAL.get(sid, ""))
        # a measured value that contradicts the complaint (a cool engine said to be overheating) wins
        if status in ("normal", "above_nominal") or (sid == "engine-overheating" and status in ("below_nominal", "low")):
            continue
        risk = "RED"
        r.risk_reasons.append((f"stop_complaint:{sid}", f"canonical_symptoms#{sid}"))
    r.risk = risk

    _missing(kb, r)

    if include_recalls and parsed.vehicle_make and (r.codes or r.symptom_records or r.safety):
        terms: list[str] = [w for cat in r.safety for w in _SAFETY_RECALL_TERMS.get(cat, ())]
        for cf in r.codes:
            for tok in fold_text(cf.title_en).split():
                if len(tok) >= 5 and tok not in _RECALL_STOPWORDS and tok not in terms:
                    terms.append(tok)
        for _sid, rec in r.symptom_records:
            for tok in fold_text(str(rec.get("name_en") or "")).split():
                if len(tok) >= 5 and tok not in _RECALL_STOPWORDS and tok not in terms:
                    terms.append(tok)
        if terms:
            for look in kb.recalls(parsed.vehicle_make, parsed.vehicle_model, terms[:8], limit=3):
                rec = look.record
                r.recalls.append({
                    "campaign": look.key, "ref": look.ref, "component": rec.get("component", ""),
                    "summary": rec.get("summary", ""), "remedy": rec.get("remedy", ""),
                    "report_date": rec.get("report_date", ""),
                })
        cc = kb.complaint_count(parsed.vehicle_make, parsed.vehicle_model)
        if cc.found:
            r.complaints = {**cc.record, "ref": cc.ref}
    return r


_SAFETY_RECALL_TERMS: dict[str, tuple[str, ...]] = {
    "brakes": ("brake", "brakes", "braking"),
    "steering": ("steering",),
    "fire": ("fire",),
    "high_voltage": ("high voltage", "battery"),
}

_RECALL_STOPWORDS = frozenset({
    "circuit", "sensor", "range", "performance", "malfunction", "system", "signal", "control", "module",
    "voltage", "input", "output", "switch", "engine", "fault", "error", "above", "below", "normal", "level",
    "intermittent", "erratic", "correlation", "high", "low", "status", "bank",
})

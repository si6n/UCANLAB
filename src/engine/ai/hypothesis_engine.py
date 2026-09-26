"""Root-cause graph + hypothesis ranking engine (FAZ 4).

Loads ``data/diagnostics/root_cause_graph.json`` (fail-closed validation,
golden_cases.py pattern) and ranks candidate hypotheses against session
evidence, anomalies and similar verified cases. Fully deterministic: sorted
intersections everywhere, lexicographic id tie-break.

Scoring (plan §FAZ 4):
- ``expected_dtcs`` ∩ active session DTCs        -> positive
- anomaly finding matching an ``evidence_signal`` -> positive
- similar verified case with the same fault       -> positive (capped)
- ``contradicting_signal`` observed nominal       -> penalty
- expected evidence signal never recorded        -> 0 ("veri yok" ≠ evidence)

``compute_root_cause_confidence`` stays the single public confidence label
API — this engine feeds it without changing its signature.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.models.diagnostics import VehicleSession
from src.engine.ai.anomaly_detector import AnomalyFinding
from src.engine.ai.evidence_gate import active_dtc_events
from src.engine.ai.golden_similarity import CaseMatch, _normalize_code, _normalize_signal

# Score weights (kept explicit; identical to plan §FAZ 4 proportions).
WEIGHT_DTC_MATCH: float = 0.40
WEIGHT_SIGNAL_MATCH: float = 0.40
WEIGHT_CASE_MATCH: float = 0.20
CONTRADICTION_PENALTY: float = 0.25
# T2-8: the observed signal NAMES of a golden case ("this channel was looked at")
# are weaker evidence than a recorded anomaly, so they weigh half. They are the
# ONLY justification a golden case can carry for a signal whose sole record is
# prose (`observed_behavior`) — no value exists, so none is invented (§2.3).
OBSERVED_SIGNAL_FACTOR: float = 0.5
# Operator-declared anomalies weigh half (plan §FAZ 3.2 synthetic rule).
SYNTHETIC_EVIDENCE_FACTOR: float = 0.5

#: Prefix the dialogue engine uses for operator-entered evidence
#: (``SignalSample`` / ``DiagnosticEvent`` names and ``AnomalyFinding.signal``).
OPERATOR_SIGNAL_PREFIX: str = "OP:"
#: The dialogue engine names an operator measurement sample ``OP:Q_SIG_<sig>``
#: and its answer event ``OP:<question_id>:<YES|NO>``.
_OPERATOR_QUESTION_PREFIX: str = "Q_SIG_"


def _canonical_signal_name(name: str) -> str:
    """Reduce an evidence name to ONE canonical signal name (P1-2).

    ``dialogue_engine`` records a technician's measurement as
    ``"OP:Q_SIG_<sig>"`` (and an answer as ``"OP:<qid>:<YES|NO>"``).
    ``anomaly_detector`` emits findings under the SAMPLE name, so the raw
    operator-prefixed name never intersected the BARE signal a root-cause node
    declares — the operator's own out-of-range evidence was silently dropped
    and ``SYNTHETIC_EVIDENCE_FACTOR`` could never fire.

    Both the ``OP:`` marker and the ``Q_SIG_`` question wrapper are removed
    here, ONCE, so every consumer (signal match, synthetic factor,
    contradiction check, observed-signal set) agrees on one name.
    """
    out = name
    if out.startswith(OPERATOR_SIGNAL_PREFIX):
        out = out[len(OPERATOR_SIGNAL_PREFIX):]
    if out.startswith(_OPERATOR_QUESTION_PREFIX):
        out = out[len(_OPERATOR_QUESTION_PREFIX):]
    return out

MAX_HYPOTHESES: int = 10

# ---------------------------------------------------------------------------
# T2-8 — SIGNAL NAME ALIAS RESOLUTION (measured root cause)
#
# Measured before this change (``scripts/_t28_probe_measure.py``):
#
#     golden signal names   : 22   (CoolantTemp, BoostPressure, CamPhaseAngle, …)
#     graph evidence signals: 25   (EngineCoolantTemp, EngineOilPressure, …)
#     NORMALIZED OVERLAP    :  3   -> 13.6 %
#
# ``_normalize_signal`` normalizes FORM (``CoolantTemp`` -> ``coolanttemp``) but
# not MEANING: ``coolanttemp`` != ``enginecoolanttemp``. A golden case that
# looked at ``CoolantTemp`` therefore produced ``samples=[]``, every SPN 110 node
# scored exactly 1.0, and the lexicographic id tie-break in ``rank_hypotheses``
# picked the WRONG node (``coolant-sensor-open`` over ``thermostat-stuck``) —
# the engine was deciding by alphabet, not by evidence.
#
# The map lives in ``data/diagnostics/signal_aliases.json``: 12 records, each
# pairing two REAL signal names (J1939 SPN / OBD-II PID) with a protocol
# rationale. No alias was invented (§2.3).
# ---------------------------------------------------------------------------

_ALIASES_PATH = Path(__file__).resolve().parents[3] / "data" / "diagnostics" / "signal_aliases.json"

# mtime/size-keyed, fail-closed cache — same pattern as ``_GRAPH_CACHE``. A
# missing/corrupt alias file yields an EMPTY map (today's behaviour preserved),
# never an exception: the engine must keep diagnosing without its alias data.
_ALIAS_CACHE: dict[tuple[str, int, int], dict[str, str]] = {}
_ALIAS_CACHE_LOCK = threading.Lock()


def _resolve_aliases_path() -> Path:
    if getattr(sys, "frozen", False):
        frozen = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve() / "data" / "diagnostics" / "signal_aliases.json"
        if frozen.is_file():
            return frozen
    return _ALIASES_PATH


def _validate_aliases_payload(payload: Any) -> dict[str, str]:
    """Build the normalized-alias -> canonical map, dropping malformed records.

    Fail-closed per RECORD (not per file): a record without a usable
    ``canonical`` or ``source_forms`` list is skipped, so one bad entry cannot
    disable the whole map. A payload that is not the expected shape yields an
    empty map.
    """
    if not isinstance(payload, dict):
        return {}
    records = payload.get("aliases")
    if not isinstance(records, list):
        return {}
    mapping: dict[str, str] = {}
    seen_canonicals: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        canonical = record.get("canonical")
        if not isinstance(canonical, str) or not canonical.strip():
            continue
        canonical_norm = _normalize_signal(canonical)
        if not canonical_norm:
            continue
        # ``node_side`` exists for documentation/diffing; ``source_forms`` is the
        # authoritative alias list and must cover BOTH sides (protocol rationale
        # in the record explains which real signal each form names).
        forms = record.get("source_forms")
        if not isinstance(forms, list):
            continue
        node_side = record.get("node_side")
        if isinstance(node_side, list):
            forms = list(forms) + [f for f in node_side if f not in forms]
        # Deterministic: the first record claiming a canonical owns that name.
        # A later duplicate must not silently re-point an established alias.
        aliases: list[str] = []
        for form in forms:
            if not isinstance(form, str) or not form.strip():
                continue
            form_norm = _normalize_signal(form)
            if not form_norm or form_norm == canonical_norm:
                continue
            aliases.append(form_norm)
        for form_norm in sorted(set(aliases)):
            if form_norm in mapping and mapping[form_norm] != canonical_norm:
                continue  # first record wins (conflict resolved deterministically)
            mapping[form_norm] = canonical_norm
        seen_canonicals.add(canonical_norm)
    # The canonical name must always resolve to ITSELF, so an alias target never
    # depends on which spelling a caller happened to use.
    for canonical_norm in sorted(seen_canonicals):
        mapping.setdefault(canonical_norm, canonical_norm)
    return mapping


def load_signal_aliases(data_path: Path | None = None) -> dict[str, str]:
    """Load the signal alias map (fail-closed; empty map on any problem).

    Returns a mapping of ``_normalize_signal`` form -> canonical
    ``_normalize_signal`` form. A missing, unreadable, non-JSON or wrongly
    shaped file yields an EMPTY map, which makes ``_canonicalize_signal`` the
    identity function — exactly the pre-T2-8 behaviour. The engine never crashes
    because its alias data is absent (fail-closed, not fail-loud, because the
    alias file is an enhancement, not a safety input).
    """
    target = data_path or _resolve_aliases_path()
    try:
        stat = target.stat()
        cache_key: tuple[str, int, int] | None = (str(target.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        cache_key = None

    if cache_key is not None:
        with _ALIAS_CACHE_LOCK:
            cached = _ALIAS_CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    mapping = _validate_aliases_payload(payload)

    if cache_key is not None:
        with _ALIAS_CACHE_LOCK:
            _ALIAS_CACHE.clear()
            _ALIAS_CACHE[cache_key] = mapping
    return mapping


def canonicalize_signal(name: str, aliases: dict[str, str] | None = None) -> str:
    """Reduce a raw signal name to ONE canonical normalized name (T2-8).

    Two-step, order-independent:
      1. ``_canonical_signal_name`` strips the dialogue engine's ``OP:`` /
         ``OP:Q_SIG_`` wrappers (P1-2 — unchanged).
      2. ``_normalize_signal`` folds case and separators
         (``"DPF Differential Pressure"`` -> ``"dpf-differential-pressure"``).
      3. the alias map resolves equivalent REAL signal names to one canonical
         form (``coolanttemp`` / ``enginecoolanttemp`` -> ``coolanttemp``).

    When ``aliases`` is ``None`` the shipped map is loaded. A name that is in no
    alias record keeps its normalized form — **backwards compatible**: an
    unaliased name behaves exactly as it did before T2-8.
    """
    normalized = _normalize_signal(_canonical_signal_name(name))
    if not normalized:
        return normalized
    table = load_signal_aliases() if aliases is None else aliases
    return table.get(normalized, normalized)


def canonical_signal_set(names: tuple[str, ...] | list[str] | set[str], aliases: dict[str, str] | None = None) -> set[str]:
    """Canonicalize a whole signal-name collection (same map for both sides)."""
    return {canonicalize_signal(n, aliases) for n in names}


def _display_signal(name: str) -> str:
    """Human-readable spelling of a canonical signal name for the ledger.

    Canonical names are normalized (``enginespeed``), which reads badly in a
    technician-facing evidence line. The spelling is recovered WITHOUT guessing:
    a name that is a known alias is shown under the canonical FORM recorded in
    the alias file — but only when that pre-image really maps to it, so an
    unaliased name is echoed verbatim and nothing is invented.
    """
    if not name:
        return name
    table = load_signal_aliases()
    if table.get(name) == name:
        # Prefer the most readable original form recorded for this canonical.
        for candidate in _alias_display_forms():
            if _normalize_signal(candidate) == name:
                return candidate
    return name


def _alias_display_forms() -> tuple[str, ...]:
    """Documentation-order ``source_forms`` from the alias file (stable)."""
    try:
        payload = json.loads(_resolve_aliases_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ()
    if not isinstance(payload, dict):
        return ()
    records = payload.get("aliases")
    if not isinstance(records, list):
        return ()
    forms: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        canonical = record.get("canonical")
        if not isinstance(canonical, str):
            continue
        if isinstance(record.get("source_forms"), list):
            forms.extend(f for f in record["source_forms"] if isinstance(f, str))
        if isinstance(record.get("node_side"), list):
            forms.extend(f for f in record["node_side"] if isinstance(f, str))
        forms.append(canonical)
    return tuple(forms)

# Flat prior for every root-cause hypothesis, INDEPENDENT of graph size.
#
# Deriving the prior from ``1.0 / len(graph)`` was a defect (T2-1): the prior
# probability of a fault mechanism cannot depend on how many nodes happen to be
# in the knowledge graph. When T2-1 grew the graph from 53 to ~10k nodes that
# formula drove the prior from 0.019 to 0.0001, so every graph expansion
# silently crushed reported confidence — punishing exactly the coverage work
# that fixes the golden-set top-1. A flat 0.5 says "before evidence, a candidate
# cause is no more likely than not", which is the neutral, graph-size-free claim.
# Locked by tests/unit/test_hypothesis_engine.py::test_prior_is_graph_size_independent.
DEFAULT_PRIOR_PROBABILITY: float = 0.5


class RootCauseGraphError(ValueError):
    """Raised when the root-cause graph violates the schema (fail-closed)."""


# ---------------------------------------------------------------------------
# T2-8 — FMI QUALIFIER GATING (explicit, deliberately one-sided)
#
# The golden corpus carries NO FMI in `dtcs[].code`: every J1939 case is the bare
# ``"SPN 110"`` / ``"SPN 190"`` form, and the FMI lives only in
# ``symptom`` / ``actual_fault`` PROSE. A naive FMI gate (parse "SPN 190 FMI 0"
# from `actual_fault`, require the node's FMI to equal it) would therefore make
# EVERY such case a MISS — measured, not assumed (``_t28_probe_measure``:
# 10 golden cases carry FMI prose, 0 carry it in the code).
#
# The rule implemented here is intentionally asymmetric:
#
#   * A node that declares NO qualifier matches any event with the same SPN —
#     "SPN 110" and "SPN 110 FMI 5" both activate it. Backwards compatible with
#     all shipped nodes (none declared a qualifier before T2-8).
#   * A node that DOES declare a qualifier matches ONLY when the event carries
#     the same FMI, in either documented spelling (``.n`` or ``FMI n``). An
#     event with no FMI can never satisfy a declared qualifier, so a qualified
#     node stays SILENT rather than firing on an unproven mechanism.
#   * When an event and a node agree on the SPN and the qualifier was satisfied,
#     the match earns FMI_SPECIFICITY_BONUS: explaining the same code with the
#     precise FMI is a strictly stronger claim than naming the bare SPN, and
#     without the bonus the two tie and the lexicographic id breaks it — the
#     exact failure mode T2-8 removes.
#
# MEASURED CONSEQUENCE FOR case-spn190-verified (reported honestly, not hidden):
# that case's event is the bare ``"SPN 190"`` and its FMI 0 exists ONLY as
# recorded PROSE in ``symptom`` / ``actual_fault``. Threading that prose into the
# engine would mean feeding it the case's own verified ANSWER text
# (``actual_fault`` is the ground truth the metric scores against), which is
# leakage, not evidence. The qualified ``engine-overrev`` node is therefore NOT
# reachable for that case and SPN 190 stays a MISS. The node is still shipped
# because it is real, provenance-backed knowledge and it fires correctly for any
# event that DOES carry ``FMI 0`` — which is how a live J1939 DM1 frame actually
# arrives (``SPN 190 FMI 0``). The gap is in the FIXTURE, not the engine.
# ---------------------------------------------------------------------------

#: Score added when a node's DECLARED FMI qualifier was actually satisfied.
#: Bounded by WEIGHT_CASE_MATCH so a qualifier hit can break a bare-SPN tie but
#: never outrank measured anomaly evidence.
FMI_SPECIFICITY_BONUS: float = 0.10

# ``"SPN 190.2"`` / ``"SPN 190.02"`` — the J1939 FMI-suffix spelling.
_SPN_FMI_SUFFIX_RE = re.compile(r"^SPN\s*(?P<spn>\d+)\s*\.\s*(?P<fmi>\d+)$", re.IGNORECASE)
# ``"SPN 190 FMI 2"`` / ``"SPN 190 fmi2"`` — the explicit spelling.
_SPN_FMI_EXPLICIT_RE = re.compile(r"^SPN\s*(?P<spn>\d+)\s*FMI\s*(?P<fmi>\d+)$", re.IGNORECASE)


def _split_code_qualifier(code: str) -> tuple[str, int | None]:
    """Split one node/event code into ``(normalized "SPN n" code, fmi | None)``.

    Only the two DOCUMENTED J1939 spellings are recognized; anything else keeps
    its plain ``_normalize_code`` form and declares no qualifier. This is a
    strict superset of the old behaviour: ``"SPN 110"`` still normalizes to
    ``"SPN 110"``.
    """
    cleaned = " ".join(str(code).strip().upper().split())
    for pattern in (_SPN_FMI_EXPLICIT_RE, _SPN_FMI_SUFFIX_RE):
        match = pattern.match(cleaned)
        if match:
            return f"SPN {int(match.group('spn'))}", int(match.group("fmi"))
    return _normalize_code(str(code)), None


# mtime/size-keyed cache (see ``load_root_cause_graph``). One entry: the last
# generation actually read from disk, keyed so a rewrite always invalidates.
_GRAPH_CACHE: dict[tuple[str, int, int], list["GraphNode"]] = {}
_GRAPH_CACHE_LOCK = threading.Lock()


@dataclass(slots=True, frozen=True)
class GraphNode:
    """One KB-derived root-cause node."""

    id: str
    title: str
    evidence_signals: tuple[str, ...]
    expected_dtcs: tuple[str, ...]
    contradicting_signals: tuple[str, ...]
    source_ref: str

    @property
    def norm_dtcs(self) -> frozenset[str]:
        return frozenset(_normalize_code(c) for c in self.expected_dtcs)

    def norm_dtcs_with_qualifiers(self) -> frozenset[tuple[str, int | None]]:
        """``(code, fmi)`` pairs the node claims, FMI ``None`` when undeclared.

        Nodes that declare no qualifier keep their bare normalized code and can
        still match an event with or without an FMI (see the FMI gating note).
        """
        return frozenset(_split_code_qualifier(c) for c in self.expected_dtcs)

    def canonical_evidence_signals(self, aliases: dict[str, str] | None = None) -> frozenset[str]:
        """Evidence signals in canonical form (T2-8 alias resolution)."""
        return frozenset(canonicalize_signal(s, aliases) for s in self.evidence_signals)

    def canonical_contradicting_signals(self, aliases: dict[str, str] | None = None) -> frozenset[str]:
        """Contradicting signals in canonical form (T2-8 alias resolution)."""
        return frozenset(canonicalize_signal(s, aliases) for s in self.contradicting_signals)


@dataclass(slots=True, frozen=True)
class Hypothesis:
    """One ranked candidate fault with its evidence ledger.

    P3-5: a frozen dataclass holding ``list`` fields is only superficially
    immutable — ``hyp.supporting_evidence.append(...)`` mutates frozen state
    and defeats hashing/caching downstream. The evidence ledger is recorded
    fact, so it is stored as an immutable ``tuple`` (``to_dict`` still emits
    JSON lists for the bridge).
    """

    id: str
    fault: str
    score: float  # normalized [0, 1]
    confidence_interval: tuple[float, float] = (0.0, 1.0)
    prior_probability: float = 0.5
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    discriminating_tests: tuple[str, ...] = ()
    # Codes this hypothesis claims (graph node `expected_dtcs`). Carried so the
    # calibration metric can enforce its code precondition without re-reading
    # the graph (T2-1).
    expected_dtcs: tuple[str, ...] = ()
    # P0-6 (2026-09-21 audit, finding 6): the graph node declares at least one
    # measurable signal, so the hypothesis can in principle be CORROBORATED or
    # REFUTED by telemetry. Measured at HEAD: 6,167 of 8,884 nodes declare NO
    # evidence signal and 8,882 declare NO contradicting signal — such a node
    # can only ever match by DTC code, so its printed position in the ranking
    # implies a diagnostic confidence the evidence base cannot support. This
    # flag lets the report say so explicitly instead of presenting an
    # unfalsifiable candidate as an equally-ranked finding.
    falsifiable: bool = True          # carries at least one observable signal
    refutable: bool = True            # carries at least one contradicting signal

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fault": self.fault,
            "score": round(self.score, 3),
            "confidence_interval": [round(self.confidence_interval[0], 3), round(self.confidence_interval[1], 3)],
            "prior_probability": round(self.prior_probability, 3),
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "discriminating_tests": list(self.discriminating_tests),
            "expected_dtcs": list(self.expected_dtcs),
            "falsifiable": self.falsifiable,
            "refutable": self.refutable,
        }


def _resolve_graph_path() -> Path:
    if getattr(sys, "frozen", False):
        frozen = (
            Path(getattr(sys, "_MEIPASS", sys.executable)).resolve() / "data" / "diagnostics" / "root_cause_graph.json"
        )
        if frozen.is_file():
            return frozen
    return Path(__file__).resolve().parents[3] / "data" / "diagnostics" / "root_cause_graph.json"


_NODE_FIELDS = frozenset(
    {
        "id",
        "title",
        "evidence_signals",
        "expected_dtcs",
        "contradicting_signals",
        "source_ref",
        # T2-1 addition: canonical system id from data/diagnostics/system_taxonomy.json.
        "system",
    }
)
# Optional node fields: present-or-absent is fine, but an absent field is not a
# validation error. The six original fields stay MANDATORY (fail-closed).
_NODE_OPTIONAL_FIELDS = frozenset({"system"})


def _validate_graph_payload(payload: dict[str, Any]) -> None:
    allowed_top = {"schema_version", "_derivation_rule", "system_taxonomy_ref", "nodes"}
    extra = set(payload) - allowed_top
    if extra:
        raise RootCauseGraphError(f"unknown top-level field(s): {sorted(extra)}")
    if payload.get("schema_version") != 1:
        raise RootCauseGraphError(f"unsupported schema_version: {payload.get('schema_version')!r}")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise RootCauseGraphError("nodes must be an array")
    seen_ids: set[str] = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise RootCauseGraphError(f"nodes[{i}] must be an object")
        extra_n = set(node) - _NODE_FIELDS
        if extra_n:
            raise RootCauseGraphError(f"nodes[{i}] unknown field(s): {sorted(extra_n)}")
        missing = _NODE_FIELDS - _NODE_OPTIONAL_FIELDS - set(node)
        if missing:
            raise RootCauseGraphError(f"nodes[{i}] missing field(s): {sorted(missing)}")
        # Optional fields must still be well-typed when present (never loosened:
        # a present-but-invalid value is a schema violation, not a skip).
        for opt_field in sorted(_NODE_OPTIONAL_FIELDS & set(node)):
            if not isinstance(node[opt_field], str) or not node[opt_field].strip():
                raise RootCauseGraphError(f"nodes[{i}].{opt_field} must be a non-empty string")
        nid = node.get("id")
        if not isinstance(nid, str) or not nid.strip():
            raise RootCauseGraphError(f"nodes[{i}].id must be a non-empty string")
        if nid in seen_ids:
            raise RootCauseGraphError(f"duplicate node id: {nid}")
        seen_ids.add(nid)
        if not str(node.get("title", "")).strip():
            raise RootCauseGraphError(f"nodes[{i}].title must be non-empty")
        if not str(node.get("source_ref", "")).strip():
            raise RootCauseGraphError(f"nodes[{i}].source_ref is mandatory (no fabricated nodes)")
        for list_field in ("evidence_signals", "expected_dtcs", "contradicting_signals"):
            val = node.get(list_field)
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                raise RootCauseGraphError(f"nodes[{i}].{list_field} must be an array of strings")


def load_root_cause_graph(data_path: Path | None = None) -> list[GraphNode]:
    """Load + validate the root-cause graph (fail-closed, mtime-keyed cache).

    The graph shipped in the working tree is ~9000 nodes / ~1 MB, and
    ``rank_hypotheses`` calls this on EVERY ``analyze_session`` — an uncached
    re-read + full schema re-validation cost 90-136 ms/session and blew the
    analyze-session latency budget by 2-2.7x.

    Caching is keyed on ``(resolved path, st_mtime_ns, st_size)`` — NEVER on
    the path alone: any rewrite of the file changes at least one of those
    values, so the payload is re-read AND re-validated (fail-closed
    ``_validate_graph_payload``) on every real change. When the file cannot be
    stat'ed the cache is bypassed entirely and the normal read path (which
    raises ``RootCauseGraphError``) runs.

    The returned list is shared between callers and MUST be treated as
    read-only; ``GraphNode`` is frozen.
    """
    target = data_path or _resolve_graph_path()
    try:
        stat = target.stat()
        cache_key: tuple[str, int, int] | None = (str(target.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        cache_key = None

    if cache_key is not None:
        with _GRAPH_CACHE_LOCK:
            cached = _GRAPH_CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RootCauseGraphError(f"cannot read/parse {target.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RootCauseGraphError("graph payload must be a JSON object")
    _validate_graph_payload(payload)
    nodes = [
        GraphNode(
            id=n["id"],
            title=n["title"],
            evidence_signals=tuple(n["evidence_signals"]),
            expected_dtcs=tuple(n["expected_dtcs"]),
            contradicting_signals=tuple(n["contradicting_signals"]),
            source_ref=n["source_ref"],
        )
        for n in payload["nodes"]
    ]
    if cache_key is not None:
        with _GRAPH_CACHE_LOCK:
            # Single-entry cache: a stale generation is dropped, never served.
            _GRAPH_CACHE.clear()
            _GRAPH_CACHE[cache_key] = nodes
    return nodes


def rank_hypotheses(
    session: VehicleSession,
    anomalies: list[AnomalyFinding],
    similar_cases: list[CaseMatch] | None = None,
    graph: list[GraphNode] | None = None,
    observed_signal_names: set[str] | None = None,
) -> list[Hypothesis]:
    """Rank candidate root causes for the session (deterministic).

    Empty session evidence -> empty list (fail-closed: no evidence, no
    hypotheses; the gate already reported the gaps).

    ``observed_signal_names`` (T2-8, additive and optional) names signals a
    caller KNOWS were observed but for which no sample value exists — the golden
    case loader's ``signals_of_interest[].name`` is the motivating source: 47 of
    54 golden signals carry their only metric as PROSE in ``observed_behavior``,
    so no ``SignalSample`` can be built from them without inventing a value
    (§2.3). The NAME alone is real evidence ("this channel was looked at"), so
    it is passed in as a name and scored at ``OBSERVED_SIGNAL_FACTOR`` — it can
    support a node that already carries independent evidence, but on its own it
    separates nothing. Omitted/empty -> identical behaviour to before T2-8.
    """
    if graph is None:
        graph = load_root_cause_graph()
    aliases = load_signal_aliases()
    dtcs = active_dtc_events(session)
    # (code, fmi) pairs, so a node's DECLARED qualifier can be honoured without
    # breaking the FMI-less nodes that are the overwhelming majority.
    active_qualified = frozenset(_split_code_qualifier(e.code) for e in dtcs)
    active_codes = frozenset(code for code, _ in active_qualified)
    # P1-2 (verified): the dialogue engine records operator-entered measurements
    # as ``SignalSample(name="OP:Q_SIG_<sig>")``, and ``anomaly_detector``
    # reports the finding under that FULL prefixed name. Root-cause nodes name
    # the BARE signal, so the intersection below was ALWAYS empty: the
    # operator's out-of-range measurement could never corroborate any
    # hypothesis (and ``SYNTHETIC_EVIDENCE_FACTOR`` never fired). The prefix is
    # stripped ONCE, here, so every consumer (signal match, synthetic factor,
    # contradiction check) agrees on one canonical signal name.
    #
    # T2-8: the SAME canonicalization (prefix strip + normalize + alias) is
    # applied to node evidence/contradicting signals, so ``CoolantTemp`` in a
    # golden case and ``EngineCoolantTemp`` in a graph node are one name.
    anomaly_signals = {canonicalize_signal(a.signal, aliases) for a in anomalies}
    observed_signals = {canonicalize_signal(s.name, aliases) for s in session.samples}
    name_only_signals = {canonicalize_signal(n, aliases) for n in (observed_signal_names or ())}
    # DISPLAY: canonical key -> the recognisable spelling a human wrote
    # ("EngineCoolantTemp"), never the folded MATCHING key ("enginecoolanttemp").
    # A technician must recognise the signal named in an evidence line, so the
    # ledger renders `_display_signal`; this map supplies the graph/operator
    # pre-images when the alias table has none.
    display_names: dict[str, str] = {}
    for _raw in (
        [a.signal for a in anomalies]
        + [s.name for s in session.samples]
        + list(observed_signal_names or ())
    ):
        display_names.setdefault(canonicalize_signal(_raw, aliases), _raw)
    # Fault text from similar VERIFIED cases contributes (capped) support.
    case_faults = {m.case_id: m for m in (similar_cases or [])}

    def _display(canonical: str) -> str:
        """Recognisable ledger spelling: recorded pre-image, else alias form.

        ``display_names`` wins because it holds the EXACT string the graph or
        the operator used; ``_display_signal`` falls back to the alias file's
        documented source form. Neither invents a name — an unknown canonical
        key is echoed unchanged.
        """
        return display_names.get(canonical) or _display_signal(canonical)

    if not active_codes and not anomalies:
        return []

    raw: list[tuple[float, str, Hypothesis]] = []
    # P0-6: id -> node, so the post-sort pass can read each node's declared
    # observable/contradicting signals without a second graph scan.
    node_lookup = {n.id: n for n in graph}
    for node in graph:
        support: list[str] = []
        contradict: list[str] = []
        score = 0.0

        node_qualified = node.norm_dtcs_with_qualifiers()
        dtc_hits = frozenset(code for code, _ in node_qualified) & active_codes
        # FMI GATE (T2-8, one-sided by design — see the module-level note): a
        # node that DECLARES a qualifier fires only when the event carries the
        # same FMI. An event with no FMI satisfies no declared qualifier, so a
        # qualified node stays silent rather than firing on an unproven
        # mechanism. Unqualified nodes are untouched.
        qualified_hits: set[tuple[str, int]] = set()
        if dtc_hits:
            declared_fmis = {fmi for _, fmi in node_qualified if fmi is not None}
            if declared_fmis:
                # Code AND FMI must both match THIS node: dtc_hits is the
                # node's own declared codes ∩ active codes, so a foreign
                # active code's FMI can never satisfy this node's qualifier
                # (contract: a qualifier qualifies the node's own DTC only).
                explicit = {
                    (code, fmi)
                    for code, fmi in active_qualified
                    if code in dtc_hits and fmi is not None and fmi in declared_fmis
                }
                if explicit:
                    qualified_hits = explicit
                else:
                    dtc_hits = frozenset()
        if dtc_hits:
            score += WEIGHT_DTC_MATCH
            support.append(f"beklenen DTC eşleşmesi: {', '.join(sorted(dtc_hits))}")
            # T2-8 SPECIFICITY BONUS. A node whose declared FMI qualifier was
            # SATISFIED has explained the SAME code more precisely than a node
            # that only names the bare SPN: FMI 0 ("above normal operating
            # range") is a strictly stronger claim than "SPN 190, some fault".
            # Without this bonus the two nodes tie at WEIGHT_DTC_MATCH and the
            # lexicographic id tie-break decides — which is precisely the defect
            # T2-8 exists to remove (`crank-sync-loss` < `engine-overrev`
            # alphabetically, so the WRONG node won). The bonus is small and
            # bounded by WEIGHT_CASE_MATCH, so it can never let an
            # FMI-qualified node outrank one carrying a real measured anomaly.
            if qualified_hits:
                score += FMI_SPECIFICITY_BONUS
            for code, fmi in sorted(qualified_hits):
                support.append(f"FMI nitelik eşleşmesi: {code} FMI {fmi}")

        node_evidence = node.canonical_evidence_signals(aliases)
        signal_hits = sorted(node_evidence & anomaly_signals)
        if signal_hits:
            # Synthetic (operator-declared) anomalies weigh half (FAZ 3.2).
            # ``synth`` is canonicalised too, so an ``OP:`` finding matches the
            # BARE node evidence entry (P1-2) AND still carries its
            # synthetic flag into the discount.
            synth = {canonicalize_signal(a.signal, aliases) for a in anomalies if a.synthetic}
            factor = SYNTHETIC_EVIDENCE_FACTOR if set(signal_hits) <= synth else 1.0
            support.append(
                f"anomali kanıtı: {', '.join(_display(s) for s in signal_hits)}"
                + (" (operatör beyanı)" if factor < 1.0 else "")
            )
            # FAZ 3.2 acceptance invariant
            # (tests/unit/test_anomaly_detector.py::test_operator_declared_oil_pressure_scores_lower):
            # a DECLARED anomaly may not SEPARATE nodes — the half-weight rule
            # exists so an unverified operator claim cannot outrank an
            # evidence-backed tie. A synthetic-only hit is therefore recorded in
            # the evidence LEDGER (above) but contributes NO score: the node
            # keeps whatever measured evidence (its DTC hit) it independently
            # earned, exactly like a node that declares no such signal at all.
            # Adding even the discounted +0.2 lifted it to 0.4 + 0.2 = 0.6
            # against the DTC-only node's 0.4, which normalized to
            # 1.0 : 0.667 — the operator's claim BEATING the tie, the inversion
            # this rule prevents. The declared run's top score (0.4) still sits
            # below the measured run's (0.8), which is the "scores lower"
            # property the test also asserts.
            if factor >= 1.0:
                score += WEIGHT_SIGNAL_MATCH

        # T2-8 (b): signals whose only record is "was observed" — a golden case's
        # `signals_of_interest[].name`, with no numerical value anywhere. Named
        # evidence, half weight, and scored ONLY for a node that already carries
        # independent evidence, so a name can corroborate but never separate.
        name_hits = sorted(node_evidence & name_only_signals)
        if name_hits and (dtc_hits or signal_hits):
            score += WEIGHT_SIGNAL_MATCH * OBSERVED_SIGNAL_FACTOR
            support.append(
                f"gözlemlenen sinyal: {', '.join(_display(s) for s in name_hits)} (değer kaydı yok)"
            )

        for sig in sorted(node_evidence & observed_signals - anomaly_signals):
            # Expected evidence signal recorded but nominal: neither support
            # nor contradiction — recorded as neutral context. (Kept explicit
            # for the discriminating-tests diff; no score effect.)
            _ = sig

        contradicting = sorted(node.canonical_contradicting_signals(aliases) & observed_signals - anomaly_signals)
        if contradicting:
            score -= CONTRADICTION_PENALTY
            contradict.append(
                f"çelişen sinyal normal: {', '.join(_display(s) for s in contradicting)}"
            )

        # Similar verified case with the same fault title keyword overlap is
        # weak COMPLEMENTARY evidence; it is only added when the node already
        # carries independent evidence (a DTC or anomaly-signal hit). Without
        # this threshold every node matched the golden corpus and unrelated
        # hypotheses leaked into the table (A3-3). Capped by WEIGHT_CASE_MATCH.
        if dtc_hits or signal_hits or name_hits:
            for case_id, match in sorted(case_faults.items()):
                score += WEIGHT_CASE_MATCH * min(1.0, match.similarity)
                support.append(f"benzer doğrulanmış vaka: {case_id} (%{match.similarity * 100:.0f})")
                break  # one case is enough complementary signal

        if score <= 0.0:
            continue
        raw.append(
            (
                max(0.0, score),
                node.id,
                Hypothesis(
                    id=node.id,
                    fault=node.title,
                    score=0.0,  # absolute score computed below
                    supporting_evidence=tuple(support),
                    contradicting_evidence=tuple(contradict),
                    expected_dtcs=tuple(node.expected_dtcs),
                ),
            ),
        )

    if not raw:
        return []
    # Graph-size-independent flat prior (see DEFAULT_PRIOR_PROBABILITY).
    prior_prob = DEFAULT_PRIOR_PROBABILITY
    import math

    out: list[Hypothesis] = []
    for r, nid, hyp in sorted(raw, key=lambda t: (-t[0], t[1])):
        # P0-5 (2026-09-21 audit, finding 5): this used to be `r / best`, a
        # RELATIVE score, so the top candidate was ALWAYS exactly 1.0 (%100)
        # no matter how little evidence it had — a single weak DTC match read
        # as absolute certainty, and adding an unrelated competing hypothesis
        # silently RAISED the leader's displayed confidence. The component
        # weights (WEIGHT_DTC_MATCH + WEIGHT_SIGNAL_MATCH + WEIGHT_CASE_MATCH)
        # sum to 1.0, so the raw score is already an ABSOLUTE [0, 1] quantity;
        # the case term can push it marginally above 1.0, hence the clamp.
        abs_score = min(1.0, max(0.0, r))
        n_ev = len(hyp.supporting_evidence) + len(hyp.contradicting_evidence)
        # Wilson-like conservative confidence interval. The previous `+ 0.05`
        # was an undisclosed pseudo-count inflating the margin; use the
        # Laplace (add-one-smoothing) form instead, which is the documented
        # statistical basis for a small-sample proportion interval.
        _p = abs_score
        margin = 1.96 * math.sqrt(max(0.0, (_p * (1.0 - _p)) / (n_ev + 4)) + (1.0 / (4.0 * (n_ev + 4) ** 2)))
        ci_lower = max(0.0, round(abs_score - margin, 3))
        ci_upper = min(1.0, round(abs_score + margin, 3))

        # P0-6: record whether this node's claim can even be tested. The
        # falsifiability is a property of the GRAPH NODE (does it declare any
        # observable signal?), not of this particular session, so it is read
        # from the node rather than inferred from the evidence ledger.
        _node = node_lookup.get(nid)
        _falsifiable = bool(_node and _node.canonical_evidence_signals(aliases))
        _refutable = bool(_node and _node.canonical_contradicting_signals(aliases))
        _support = list(hyp.supporting_evidence)
        if not _falsifiable and not _refutable:
            # Honest annotation (AGENTS.md §2.3): state the evidential limit
            # rather than letting the ranking imply corroboration it lacks.
            _support.append(
                "kanıt sınırı: düğüm ölçülebilir/çürütülebilir sinyal tanımlamıyor — "
                "yalnızca DTC kodu eşleşmesiyle sıralandı, telemetriyle doğrulanamaz"
            )
        elif not _refutable:
            _support.append(
                "kanıt sınırı: düğüm çürütücü sinyal tanımlamıyor — yanlışlanamaz, "
                "yalnızca doğrulayıcı kanıtla desteklenebilir"
            )
        elif not _falsifiable:
            _support.append(
                "kanıt sınırı: düğüm doğrulayıcı sinyal tanımlamıyor — telemetriyle "
                "DOĞRULANAMAZ, yalnızca çürütücü kanıtla elenebilir"
            )

        out.append(
            Hypothesis(
                id=nid,
                fault=hyp.fault,
                score=abs_score,
                confidence_interval=(ci_lower, ci_upper),
                prior_probability=prior_prob,
                supporting_evidence=tuple(_support),
                contradicting_evidence=hyp.contradicting_evidence,
                expected_dtcs=hyp.expected_dtcs,
                falsifiable=_falsifiable,
                refutable=_refutable,
            )
        )
    return out[:MAX_HYPOTHESES]


__all__ = [
    "DEFAULT_PRIOR_PROBABILITY",
    "OBSERVED_SIGNAL_FACTOR",
    "GraphNode",
    "Hypothesis",
    "RootCauseGraphError",
    "canonical_signal_set",
    "canonicalize_signal",
    "load_root_cause_graph",
    "load_signal_aliases",
    "rank_hypotheses",
]

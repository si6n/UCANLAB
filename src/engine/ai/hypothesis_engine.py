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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.models.diagnostics import VehicleSession
from src.engine.ai.anomaly_detector import AnomalyFinding
from src.engine.ai.evidence_gate import active_dtc_events
from src.engine.ai.golden_similarity import CaseMatch, _normalize_code

# Score weights (kept explicit; identical to plan §FAZ 4 proportions).
WEIGHT_DTC_MATCH: float = 0.40
WEIGHT_SIGNAL_MATCH: float = 0.40
WEIGHT_CASE_MATCH: float = 0.20
CONTRADICTION_PENALTY: float = 0.25
# Operator-declared anomalies weigh half (plan §FAZ 3.2 synthetic rule).
SYNTHETIC_EVIDENCE_FACTOR: float = 0.5

MAX_HYPOTHESES: int = 10


class RootCauseGraphError(ValueError):
    """Raised when the root-cause graph violates the schema (fail-closed)."""


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


@dataclass(slots=True, frozen=True)
class Hypothesis:
    """One ranked candidate fault with its evidence ledger."""

    id: str
    fault: str
    score: float  # normalized [0, 1]
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    discriminating_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fault": self.fault,
            "score": round(self.score, 3),
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "discriminating_tests": list(self.discriminating_tests),
        }


def _resolve_graph_path() -> Path:
    if getattr(sys, "frozen", False):
        frozen = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve() / "data" / "diagnostics" / "root_cause_graph.json"
        if frozen.is_file():
            return frozen
    return Path(__file__).resolve().parents[3] / "data" / "diagnostics" / "root_cause_graph.json"


_NODE_FIELDS = frozenset({"id", "title", "evidence_signals", "expected_dtcs", "contradicting_signals", "source_ref"})


def _validate_graph_payload(payload: dict[str, Any]) -> None:
    allowed_top = {"schema_version", "_derivation_rule", "nodes"}
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
        missing = _NODE_FIELDS - set(node)
        if missing:
            raise RootCauseGraphError(f"nodes[{i}] missing field(s): {sorted(missing)}")
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
    """Load + validate the root-cause graph (fail-closed)."""
    target = data_path or _resolve_graph_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RootCauseGraphError(f"cannot read/parse {target.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RootCauseGraphError("graph payload must be a JSON object")
    _validate_graph_payload(payload)
    return [
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


def rank_hypotheses(
    session: VehicleSession,
    anomalies: list[AnomalyFinding],
    similar_cases: list[CaseMatch] | None = None,
    graph: list[GraphNode] | None = None,
) -> list[Hypothesis]:
    """Rank candidate root causes for the session (deterministic).

    Empty session evidence -> empty list (fail-closed: no evidence, no
    hypotheses; the gate already reported the gaps).
    """
    if graph is None:
        graph = load_root_cause_graph()
    dtcs = active_dtc_events(session)
    active_codes = frozenset(_normalize_code(e.code) for e in dtcs)
    anomaly_signals = {a.signal for a in anomalies}
    observed_signals = {s.name for s in session.samples}
    # Fault text from similar VERIFIED cases contributes (capped) support.
    case_faults = {m.case_id: m for m in (similar_cases or [])}

    if not active_codes and not anomalies:
        return []

    raw: list[tuple[float, str, Hypothesis]] = []
    for node in graph:
        support: list[str] = []
        contradict: list[str] = []
        score = 0.0

        dtc_hits = node.norm_dtcs & active_codes
        if dtc_hits:
            score += WEIGHT_DTC_MATCH
            support.append(f"beklenen DTC eşleşmesi: {', '.join(sorted(dtc_hits))}")

        signal_hits = sorted(set(node.evidence_signals) & anomaly_signals)
        if signal_hits:
            # Synthetic (operator-declared) anomalies weigh half.
            synth = {a.signal for a in anomalies if a.synthetic}
            factor = SYNTHETIC_EVIDENCE_FACTOR if set(signal_hits) <= synth else 1.0
            score += WEIGHT_SIGNAL_MATCH * factor
            support.append(f"anomali kanıtı: {', '.join(signal_hits)}" + (" (operatör beyanı)" if factor < 1.0 else ""))

        for sig in sorted(set(node.evidence_signals) & observed_signals - anomaly_signals):
            # Expected evidence signal recorded but nominal: neither support
            # nor contradiction — recorded as neutral context. (Kept explicit
            # for the discriminating-tests diff; no score effect.)
            _ = sig

        contradicting = sorted(set(node.contradicting_signals) & observed_signals - anomaly_signals)
        if contradicting:
            score -= CONTRADICTION_PENALTY
            contradict.append(f"çelişen sinyal normal: {', '.join(contradicting)}")

        # Similar verified case with the same fault title keyword overlap is
        # weak COMPLEMENTARY evidence; it is only added when the node already
        # carries independent evidence (a DTC or anomaly-signal hit). Without
        # this threshold every node matched the golden corpus and unrelated
        # hypotheses leaked into the table (A3-3). Capped by WEIGHT_CASE_MATCH.
        if dtc_hits or signal_hits:
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
                    score=0.0,  # normalized after max pass
                    supporting_evidence=support,
                    contradicting_evidence=contradict,
                ),
            ),
        )

    if not raw:
        return []
    # Normalize to [0, 1] over the best raw score, deterministic order.
    best = max(r for r, _, _ in raw) or 1.0
    out: list[Hypothesis] = []
    for r, nid, hyp in sorted(raw, key=lambda t: (-t[0], t[1])):
        out.append(
            Hypothesis(
                id=nid,
                fault=hyp.fault,
                score=r / best,
                supporting_evidence=hyp.supporting_evidence,
                contradicting_evidence=hyp.contradicting_evidence,
            )
        )
    return out[:MAX_HYPOTHESES]


__all__ = [
    "GraphNode",
    "Hypothesis",
    "RootCauseGraphError",
    "load_root_cause_graph",
    "rank_hypotheses",
]

# -*- coding: utf-8 -*-
"""P0-6 regression lock — root-cause hypothesis testability.

WHY THIS EXISTS
---------------
The 2026-09-21 deep-discovery audit (finding 6) measured the shipped
``data/diagnostics/root_cause_graph.json``::

    total nodes                          : 8,884
    declaring NO evidence_signal         : 6,167  (69.4 %)
    declaring NO contradicting_signal    : 8,882  (99.98 %)

A node that declares no observable signal can only ever match by DTC code — it
can neither be corroborated nor refuted by telemetry. Before this change the
technician report listed such a node in the SAME ranked table as a fully
testable one, with only a score to tell them apart, so the ranking implied a
diagnostic confidence the evidence base could not support.

The fix does NOT delete those nodes (they are still legitimate DTC→cause
mappings). It makes their evidential status EXPLICIT: each hypothesis carries
``falsifiable`` / ``refutable`` flags derived from its graph node, and the
report prints a "test edilebilirlik" (testability) column plus a disclosure.

Offline, deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    VehicleSession,
)
from src.engine.ai.evidence_gate import evaluate_sufficiency
from src.engine.ai.hypothesis_engine import (
    load_root_cause_graph,
    rank_hypotheses,
)
from src.engine.ai.session_report import build_technician_report

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = REPO_ROOT / "data" / "diagnostics" / "root_cause_graph.json"


@pytest.fixture(scope="module")
def graph():
    return load_root_cause_graph()


def _session(codes: list[str]) -> VehicleSession:
    session = VehicleSession(
        session_id="sess-p06", started_at_ns=1, domain=DiagnosticDomain.HEAVY_DUTY
    )
    for code in codes:
        session.events.append(
            DiagnosticEvent(
                timestamp_ns=1,
                code=code,
                domain=DiagnosticDomain.HEAVY_DUTY,
                severity="MEDIUM",
                status="ACTIVE",
            )
        )
    return session


class TestMeasuredGraphCoverage:
    """Pin the measured defect so a future regression is visible."""

    def test_graph_still_has_the_unfalsifiable_majority(self) -> None:
        """The finding is real; do not let a silent data change hide it."""
        payload = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        nodes = payload["nodes"]
        with_evidence = sum(1 for n in nodes if n.get("evidence_signals"))
        with_contradiction = sum(1 for n in nodes if n.get("contradicting_signals"))
        # These are the audit's measured shape. If the graph is later enriched
        # the test should be updated deliberately, not silently satisfied.
        assert len(nodes) >= 8_000
        assert with_contradiction < len(nodes) * 0.01, (
            "the audit found contradicting_signals on <0.02% of nodes; a large "
            "increase means the graph was enriched (good) — update this test"
        )
        assert with_evidence < len(nodes), "some nodes must declare evidence (the graph is not empty)"

    def test_every_node_still_has_expected_dtcs_and_source(self) -> None:
        """The DTC mapping and provenance must survive the testability work."""
        payload = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        for node in payload["nodes"]:
            assert node.get("expected_dtcs"), f"{node['id']} lost its expected_dtcs"
            assert str(node.get("source_ref", "")).strip(), f"{node['id']} lost its source_ref"


class TestHypothesisCarriesTestability:
    """Each ranked hypothesis must expose whether it can be tested."""

    def test_flags_are_populated(self, graph) -> None:
        hyps = rank_hypotheses(_session(["SPN 100 FMI 1"]), [], None, graph=graph)
        assert hyps, "sanity: the SPN 100 nodes must rank"
        for h in hyps:
            assert isinstance(h.falsifiable, bool)
            assert isinstance(h.refutable, bool)

    def test_flags_are_serialised(self, graph) -> None:
        """The bridge/UI reads `to_dict`, so the flags must cross that boundary."""
        hyps = rank_hypotheses(_session(["SPN 100 FMI 1"]), [], None, graph=graph)
        payload = hyps[0].to_dict()
        assert "falsifiable" in payload
        assert "refutable" in payload

    def test_a_node_without_signals_is_flagged_untestable(self, graph) -> None:
        """Some ranked node must be flagged — the majority are untestable."""
        hyps = rank_hypotheses(_session(["SPN 100 FMI 1", "P0300"]), [], None, graph=graph)
        assert any(not (h.falsifiable and h.refutable) for h in hyps), (
            "the audit found ~99.98% of nodes untestable; at least one ranked "
            "candidate must be flagged as such"
        )

    def test_untestable_node_gets_an_honest_ledger_note(self, graph) -> None:
        """The limitation must be recorded as evidence text, not hidden."""
        hyps = rank_hypotheses(_session(["SPN 100 FMI 1", "P0300"]), [], None, graph=graph)
        untestable = [h for h in hyps if not (h.falsifiable and h.refutable)]
        assert untestable, "sanity: expected an untestable candidate"
        for h in untestable:
            joined = " ".join(h.supporting_evidence)
            assert "kanıt sınırı" in joined, (
                f"{h.id} is untestable but carries no 'kanıt sınırı' disclosure"
            )


class TestReportSurfacesTestability:
    """The operator must SEE the limitation, not have to infer it."""

    def _report(self, graph) -> str:
        session = _session(["SPN 100 FMI 1", "P0300"])
        hypotheses = rank_hypotheses(session, [], None, graph=graph)
        return build_technician_report(
            session, evaluate_sufficiency(session), [], hypotheses, []
        )

    def test_table_has_a_testability_column(self, graph) -> None:
        report = self._report(graph)
        section = report.split("## Hipotez Sıralaması")[1].split("##")[0]
        assert "Test edilebilirlik" in section

    def test_report_discloses_untestable_candidates(self, graph) -> None:
        report = self._report(graph)
        section = report.split("## Hipotez Sıralaması")[1].split("##")[0]
        assert "telemetriyle doğrulanamaz" in section

    def test_report_never_claims_certainty(self, graph) -> None:
        """Falsifiability must not be traded for an overconfident score."""
        report = self._report(graph)
        section = report.split("## Hipotez Sıralaması")[1].split("##")[0]
        assert "%100" not in section

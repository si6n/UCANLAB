# -*- coding: utf-8 -*-
"""P0-5 regression lock — absolute hypothesis scoring.

WHY THIS EXISTS
---------------
The 2026-09-21 deep-discovery audit (finding 5) measured that the hypothesis
table reported the TOP CANDIDATE at exactly ``%100`` for every session::

    norm_score = r / best

``best`` is the highest raw score in the CURRENT result set, so the leader is
always divided by itself. Two consequences:

1. A single weak DTC match rendered as **absolute certainty**, because the
   component weights (0.40 DTC + 0.40 signal + 0.20 case) had fired only the
   first term — a raw 0.40 — yet the table showed ``%100``.
2. The score was **non-monotonic in evidence**: adding an UNRELATED competing
   hypothesis lowered every raw score's denominator proportionally but the
   leader *stayed* at 1.0, so no amount of contradictory context could reduce
   the displayed confidence of the top row.

The fix replaces the relative ratio with the raw score CLAMPED to [0, 1]. The
weights already sum to 1.0, so the raw value is an absolute confidence; only the
optional case term can push it past 1.0, hence the clamp.

Offline, deterministic — no network, no LLM.
"""

from __future__ import annotations

import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    VehicleSession,
)
from src.engine.ai.hypothesis_engine import (
    WEIGHT_CASE_MATCH,
    WEIGHT_DTC_MATCH,
    WEIGHT_SIGNAL_MATCH,
    load_root_cause_graph,
    rank_hypotheses,
)


def _session(dtcs=None) -> VehicleSession:
    """Minimal heavy-duty session carrying the given DTC codes."""
    session = VehicleSession(
        session_id="sess-abs", started_at_ns=1, domain=DiagnosticDomain.HEAVY_DUTY
    )
    for code in dtcs or []:
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


@pytest.fixture(scope="module")
def graph():
    return load_root_cause_graph()


class TestWeightsDefineAnAbsoluteScale:
    """The fix is only valid because the component weights sum to 1.0."""

    def test_weights_sum_to_one(self) -> None:
        total = WEIGHT_DTC_MATCH + WEIGHT_SIGNAL_MATCH + WEIGHT_CASE_MATCH
        assert abs(total - 1.0) < 1e-9, (
            "the absolute-scale fix assumes the component weights sum to 1.0; "
            f"they now sum to {total}. Re-derive the normalisation if this changes."
        )


class TestScoresAreAbsoluteNotRelative:
    """The exact non-monotonicity the audit flagged."""

    def _scores(self, dtcs: list[str], graph) -> dict[str, float]:
        return {
            h.id: h.score
            for h in rank_hypotheses(_session(dtcs), [], None, graph=graph)
        }

    def test_single_dtc_does_not_report_certainty(self, graph) -> None:
        """One DTC match fires only the DTC weight — it is not %100."""
        scores = self._scores(["SPN 100 FMI 1"], graph)
        assert scores, "sanity: the SPN 100 node must rank"
        for node_id, score in scores.items():
            assert score <= WEIGHT_DTC_MATCH + WEIGHT_CASE_MATCH + 1e-9, (
                f"{node_id} scored {score} from a single DTC match; a lone DTC hit "
                "can only earn the DTC weight (plus an optional case term)"
            )

    def test_adding_competing_evidence_never_raises_the_leader(self, graph) -> None:
        """The non-monotonicity: a new unrelated DTC must not inflate the leader."""
        base = self._scores(["SPN 100 FMI 1"], graph)
        base_leader = max(base.values())
        with_competitor = self._scores(["SPN 100 FMI 1", "P0300"], graph)
        competitor_leader = max(with_competitor.values())
        assert competitor_leader <= base_leader + 1e-9, (
            "adding an unrelated competing hypothesis RAISED the leader's confidence "
            f"({base_leader} -> {competitor_leader}); the score is still relative"
        )

    def test_node_score_is_stable_across_session_context(self, graph) -> None:
        """The SAME node must score the same regardless of what else is present."""
        alone = self._scores(["SPN 100 FMI 1"], graph)
        together = self._scores(["SPN 100 FMI 1", "P0300", "SPN 110 FMI 0"], graph)
        for node_id, score in alone.items():
            if node_id in together:
                assert together[node_id] == pytest.approx(score, abs=1e-9), (
                    f"{node_id} scored {score} alone but {together[node_id]} with other "
                    "codes present — the score depends on the result set (relative)"
                )

    def test_scores_are_clamped_to_unit_interval(self, graph) -> None:
        for dtcs in (
            ["SPN 100 FMI 1"],
            ["SPN 100 FMI 1", "SPN 110 FMI 0"],
            ["P0300", "P0301"],
        ):
            for h in rank_hypotheses(_session(dtcs), [], None, graph=graph):
                assert 0.0 < h.score <= 1.0, f"{h.id} score {h.score} outside (0, 1]"

    def test_ordering_by_score_is_preserved(self, graph) -> None:
        """The absolute scale must not disturb the ranking order."""
        hyps = rank_hypotheses(_session(["SPN 100 FMI 1", "SPN 110 FMI 0"]), [], None, graph=graph)
        scores = [h.score for h in hyps]
        assert scores == sorted(scores, reverse=True)


class TestConfidenceInterval:
    """The undisclosed `+0.05` pseudo-count was replaced by a documented form."""

    def test_interval_brackets_the_score(self, graph) -> None:
        for h in rank_hypotheses(_session(["SPN 100 FMI 1"]), [], None, graph=graph):
            lower, upper = h.confidence_interval
            assert 0.0 <= lower <= h.score <= upper <= 1.0

    def test_interval_is_wider_with_less_evidence(self, graph) -> None:
        """A confidence interval must narrow as evidence accrues, not widen."""
        few = rank_hypotheses(_session(["SPN 100 FMI 1"]), [], None, graph=graph)
        if not few:
            pytest.skip("no hypothesis produced")
        widest = max(h.confidence_interval[1] - h.confidence_interval[0] for h in few)
        # Many codes at once should not produce an interval wider than the
        # single-code case: more evidence must not mean less certainty.
        many = rank_hypotheses(
            _session(["SPN 100 FMI 1", "SPN 110 FMI 0", "SPN 102 FMI 0", "P0300"]),
            [],
            None,
            graph=graph,
        )
        if not many:
            pytest.skip("no hypothesis produced")
        widest_many = max(h.confidence_interval[1] - h.confidence_interval[0] for h in many)
        assert widest_many <= widest + 1e-9

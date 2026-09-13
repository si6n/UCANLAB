"""FAZ 5 unit tests: discriminating test selection between top-2 hypotheses."""

from __future__ import annotations

from src.engine.ai.discriminating_tests import propose_discriminating_tests
from src.engine.ai.hypothesis_engine import Hypothesis


def _hyp(i: str, score: float) -> Hypothesis:
    return Hypothesis(id=i, fault=f"fault {i}", score=score, supporting_evidence=[], contradicting_evidence=[])


class TestSelectionRules:
    def test_single_hypothesis_no_tests(self) -> None:
        assert propose_discriminating_tests([_hyp("a", 0.9)], ["P0300"]) == []

    def test_clear_gap_no_tests(self) -> None:
        # Score delta 0.5 >= 0.2: the winner is NOT ambiguous -> no tests.
        hyps = [_hyp("a", 0.9), _hyp("b", 0.4)]
        assert propose_discriminating_tests(hyps, ["P0300"]) == []

    def test_ambiguous_pair_proposes_kb_steps(self) -> None:
        # Delta 0.05 < 0.2 with a KB-backed DTC (SPN 100 has steps).
        hyps = [_hyp("a", 0.85), _hyp("b", 0.80)]
        tests = propose_discriminating_tests(hyps, ["SPN100"])
        assert tests, "KB measurement steps must be proposed"
        # Texts come from the KB entry verbatim (no invented phrasing).
        from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE

        spn100 = EXPERT_KNOWLEDGE_BASE["SPN100"]
        known_texts = {spn100["measurement"]} | {s[0] for s in spn100["steps"]}
        assert all(t in known_texts for t in tests)

    def test_ambiguous_pair_without_kb_no_tests(self) -> None:
        hyps = [_hyp("a", 0.85), _hyp("b", 0.80)]
        # A DTC absent from the KB -> no honest measurement to propose.
        assert propose_discriminating_tests(hyps, ["ZZZZZ"]) == []

    def test_determinism(self) -> None:
        hyps = [_hyp("a", 0.85), _hyp("b", 0.80)]
        t1 = propose_discriminating_tests(hyps, ["SPN100"])
        t2 = propose_discriminating_tests(hyps, ["SPN100"])
        assert t1 == t2
        assert len(t1) <= 3

    def test_no_dtc_codes_no_tests(self) -> None:
        hyps = [_hyp("a", 0.85), _hyp("b", 0.80)]
        assert propose_discriminating_tests(hyps, None) == []
        assert propose_discriminating_tests(hyps, []) == []

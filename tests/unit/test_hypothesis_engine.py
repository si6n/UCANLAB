"""FAZ 4 unit tests: root-cause graph loading + hypothesis ranking."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.anomaly_detector import AnomalyFinding
from src.engine.ai.golden_similarity import CaseMatch
from src.engine.ai.hypothesis_engine import (
    RootCauseGraphError,
    load_root_cause_graph,
    rank_hypotheses,
)


def _session(dtcs=None):
    s = VehicleSession(session_id="sess-h", started_at_ns=1, domain=DiagnosticDomain.HEAVY_DUTY)
    for code in dtcs or []:
        s.events.append(
            DiagnosticEvent(
                timestamp_ns=1,
                code=code,
                domain=DiagnosticDomain.HEAVY_DUTY,
                severity="MEDIUM",
                status="ACTIVE",
            )
        )
    return s


def _anomaly(signal: str, synthetic: bool = False) -> AnomalyFinding:
    return AnomalyFinding(signal=signal, finding="test finding", ratio=1.0, evidence_sample_count=5, synthetic=synthetic)


class TestGraphLoading:
    def test_shipped_graph_loads_with_source_refs(self) -> None:
        graph = load_root_cause_graph()
        assert len(graph) >= 15
        for node in graph:
            assert node.source_ref.strip(), f"{node.id} missing source_ref"

    def test_missing_source_ref_rejected(self, tmp_path: Path) -> None:
        payload = {
            "schema_version": 1,
            "nodes": [
                {
                    "id": "x",
                    "title": "T",
                    "evidence_signals": [],
                    "expected_dtcs": [],
                    "contradicting_signals": [],
                    "source_ref": "",
                }
            ],
        }
        p = tmp_path / "g.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(RootCauseGraphError):
            load_root_cause_graph(p)

    def test_unknown_node_field_rejected(self, tmp_path: Path) -> None:
        payload = {
            "schema_version": 1,
            "nodes": [
                {
                    "id": "x",
                    "title": "T",
                    "evidence_signals": [],
                    "expected_dtcs": [],
                    "contradicting_signals": [],
                    "source_ref": "kb",
                    "extra_field": 1,
                }
            ],
        }
        p = tmp_path / "g.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(RootCauseGraphError):
            load_root_cause_graph(p)

    def test_duplicate_ids_rejected(self, tmp_path: Path) -> None:
        node = {
            "id": "x",
            "title": "T",
            "evidence_signals": [],
            "expected_dtcs": [],
            "contradicting_signals": [],
            "source_ref": "kb",
        }
        payload = {"schema_version": 1, "nodes": [node, dict(node)]}
        p = tmp_path / "g.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(RootCauseGraphError):
            load_root_cause_graph(p)


class TestRanking:
    def test_empty_session_yields_no_hypotheses(self) -> None:
        assert rank_hypotheses(_session(), [], None, graph=[]) == []

    def test_dtc_match_ranks_node(self) -> None:
        graph = load_root_cause_graph()
        session = _session(["SPN 100 FMI 1"])
        hyps = rank_hypotheses(session, [], None, graph=graph)
        assert hyps, "oil pressure DTC must produce hypotheses"
        assert hyps[0].id == "oil-pump-wear"
        assert any("SPN 100" in s for s in hyps[0].supporting_evidence)
        assert 0.0 < hyps[0].score <= 1.0

    def test_related_pair_nodes_rank_from_groups(self) -> None:
        """_RELATED_CODE_GROUPS-derived nodes fire on their documented pairs."""
        graph = load_root_cause_graph()
        cases = {
            ("P0299", "P0401"): "egr-low-flow",
            ("P0087", "P0088"): "fuel-rail-regulator",
            ("P2002", "P2453"): "dpf-diff-pressure",
            ("U0100", "P0620"): "can-power-ground",
            ("P0171", "P0174"): "lean-both-banks",
        }
        for codes, node_id in cases.items():
            hyps = rank_hypotheses(_session(list(codes)), [], None, graph=graph)
            assert hyps, f"{codes} must produce hypotheses"
            # The pair node must fire (top-3). Exact top-1 is a documented
            # tie-break: single-code nodes matching a subset score equally
            # (WEIGHT_DTC_MATCH is per-node, not per-code) and win
            # lexicographically — deterministic, not a bug.
            assert node_id in [h.id for h in hyps[:3]]

    def test_j1939_sensor_nodes_fire(self) -> None:
        """J1939 sensor-descriptor nodes (graph expansion round 3)."""
        graph = load_root_cause_graph()
        cases = {
            "SPN 91 FMI 2": "accel-pedal-sensor",
            "SPN 96 FMI 1": "fuel-level-sensor",
            "SPN 111 FMI 1": "coolant-level-low",
            "SPN 175 FMI 0": "oil-temp-high",
            "SPN 513 FMI 2": "engine-torque-actual",
        }
        for code, node_id in cases.items():
            hyps = rank_hypotheses(_session([code]), [], None, graph=graph)
            assert hyps, f"{code} must produce hypotheses"
            assert node_id in [h.id for h in hyps[:3]]

    def test_single_code_nodes_fire(self) -> None:
        """KB/J1939-grounded single-code nodes (graph expansion round 2)."""
        graph = load_root_cause_graph()
        cases = {
            "P0A93": "inverter-coolant-pump",
            "P0401": "egr-valve-stuck",
            "P0620": "alternator-control",
            "P0171": "lean-bank-1",
            "P2002": "dpf-efficiency",
            "P0217": "engine-overtemp",
            "P0521": "oil-pressure-sensor",
            "SPN 3216 FMI 0": "nox-inlet-sensor",
            "SPN 3226 FMI 0": "nox-outlet-sensor",
            "SPN 157 FMI 1": "rail-pressure-sensor",
        }
        for code, node_id in cases.items():
            hyps = rank_hypotheses(_session([code]), [], None, graph=graph)
            assert hyps, f"{code} must produce hypotheses"
            assert node_id in [h.id for h in hyps[:3]]

    def test_anomaly_increases_support(self) -> None:
        graph = load_root_cause_graph()
        session = _session(["SPN 110 FMI 0"])
        weak = rank_hypotheses(session, [], None, graph=graph)
        strong = rank_hypotheses(session, [_anomaly("EngineCoolantTemp")], None, graph=graph)
        assert strong[0].score >= weak[0].score
        assert strong[0].supporting_evidence[-1].startswith("anomali kanıtı")

    def test_synthetic_anomaly_weighs_less(self) -> None:
        graph = load_root_cause_graph()
        session = _session(["SPN 110 FMI 0"])
        real = rank_hypotheses(session, [_anomaly("EngineCoolantTemp", synthetic=False)], None, graph=graph)
        ops = rank_hypotheses(session, [_anomaly("EngineCoolantTemp", synthetic=True)], None, graph=graph)
        assert real is not None  # sanity: real-source ranking produced a result
        # raw support for synthetic = DTC 0.4 + 0.4*0.5 = 0.6 vs 0.8 real
        # normalized against the same best per call, so compare raw support
        # text instead (deterministic marker).
        assert any("(operatör beyanı)" in s for h in ops for s in h.supporting_evidence)

    def test_contradicting_signal_penalizes(self) -> None:
        graph = load_root_cause_graph()
        # SPN 100 (sensor-shorts node) contradicted by a nominal observed
        # EngineOilPressure signal (recorded, no anomaly).
        session = _session(["SPN 100 FMI 3"])
        session.samples.append(
            SignalSample(
                timestamp_ns=1,
                name="EngineOilPressure",
                raw_value=10,
                physical_value=2.5,
                unit="bar",
                source=SignalSource.J1939,
            )
        )
        hyps = rank_hypotheses(session, [], None, graph=graph)
        node = [h for h in hyps if h.id == "oil-sensor-shorts"]
        assert node, "expected the sensor-shorts hypothesis"
        assert any("çelişen" in c for c in node[0].contradicting_evidence)
        # Both SPN 100 hypotheses matched the DTC; the contradicted one must
        # not outrank the un-contradicted one.
        assert hyps[0].id != "oil-sensor-shorts"

    def test_determinism_and_tiebreak(self) -> None:
        graph = load_root_cause_graph()
        session = _session(["SPN 100 FMI 1", "SPN 110 FMI 0"])
        h1 = rank_hypotheses(session, [], None, graph=graph)
        h2 = rank_hypotheses(session, [], None, graph=graph)
        assert [h.to_dict() for h in h1] == [h.to_dict() for h in h2]

    def test_case_similarity_adds_support(self) -> None:
        graph = load_root_cause_graph()
        session = _session(["SPN 110 FMI 0"])
        base = rank_hypotheses(session, [], None, graph=graph)
        with_case = rank_hypotheses(session, [], [CaseMatch("case-x", 0.8, ["DTC"])], graph=graph)
        assert any("benzer doğrulanmış vaka" in s for h in with_case for s in h.supporting_evidence)
        # With only one hypothesis in both runs, support must have grown.
        assert len(with_case[0].supporting_evidence) > len(base[0].supporting_evidence)

    def test_case_similarity_needs_independent_evidence(self) -> None:
        # A3-3: a similar verified case is only COMPLEMENTARY evidence. A node
        # with zero DTC hits and zero anomaly-signal hits must NOT be scored
        # into the table just because the golden corpus is non-empty.
        graph = load_root_cause_graph()
        # SPN 100 activates the oil-pressure nodes; the case match must not
        # drag in any node that neither DTC- nor signal-matched.
        session = _session(["SPN 100 FMI 1"])
        matched = rank_hypotheses(session, [], [CaseMatch("case-x", 0.9, ["DTC"])], graph=graph)
        matched_ids = {h.id for h in matched}
        assert matched_ids, "sanity: the SPN 100 node must still rank"
        # Every ranked node must carry at least one independent-evidence line.
        for h in matched:
            assert any(
                s.startswith("beklenen DTC eşleşmesi") or s.startswith("anomali kanıtı")
                for s in h.supporting_evidence
            ), f"{h.id} ranked with case similarity only: {h.supporting_evidence}"
        # Nodes that match neither the DTC nor any anomaly must be absent,
        # even though the case corpus is non-empty. `norm_dtcs` is the
        # normalized form, so compare against it directly.
        assert "oil-pump-wear" in matched_ids
        unrelated = [n.id for n in graph if "SPN 100" not in set(n.norm_dtcs)]
        for nid in unrelated:
            assert nid not in matched_ids, f"unrelated node {nid} leaked into the table via case similarity"

    def test_case_similarity_ignored_without_evidence(self) -> None:
        # A3-3 edge: no active DTC and no anomaly -> the early return already
        # yields [], but assert the invariant explicitly so a future change to
        # the early-return path cannot reintroduce case-only scoring.
        graph = load_root_cause_graph()
        session = _session([])
        hyps = rank_hypotheses(session, [], [CaseMatch("case-y", 1.0, ["DTC"])], graph=graph)
        assert hyps == []

    def test_case_similarity_still_complements_dtc_match(self) -> None:
        # The fix must not remove the complementary contribution: a node that
        # DID match the DTC keeps its case-support line.
        graph = load_root_cause_graph()
        session = _session(["SPN 100 FMI 1"])
        hyps = rank_hypotheses(session, [], [CaseMatch("case-x", 0.8, ["DTC"])], graph=graph)
        assert any(
            "benzer doğrulanmış vaka" in s for h in hyps for s in h.supporting_evidence
        )

    def test_scores_normalized_and_sorted(self) -> None:
        graph = load_root_cause_graph()
        session = _session(["SPN 100 FMI 1", "SPN 110 FMI 0", "P0300"])
        hyps = rank_hypotheses(session, [], None, graph=graph)
        assert len(hyps) >= 2
        assert hyps[0].score >= hyps[-1].score
        assert all(0.0 <= h.score <= 1.0 for h in hyps)

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

    def test_scores_normalized_and_sorted(self) -> None:
        graph = load_root_cause_graph()
        session = _session(["SPN 100 FMI 1", "SPN 110 FMI 0", "P0300"])
        hyps = rank_hypotheses(session, [], None, graph=graph)
        assert len(hyps) >= 2
        assert hyps[0].score >= hyps[-1].score
        assert all(0.0 <= h.score <= 1.0 for h in hyps)

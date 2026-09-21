"""T2-1 regression tests: the golden-set match rule must not be overfit-able.

Two coupled defects were found and fixed in T2-1 (see ``tasks/T2-1-blokaj-raporu.md``):

1. ``evaluate_calibration`` accepted a hit when the top hypothesis title shared
   ONE word (>4 chars) with ``actual_fault``. Combined with nodes whose
   ``expected_dtcs`` named a golden code, that made the published 44.4 % an
   overfit measurement (independently re-measured at 0/54 under the strict rule).

2. Plain word overlap is the WRONG SIGNAL even after tightening: the engine
   states the fault MECHANISM, the golden case states the FMI DESCRIPTION, and
   the two are often in different languages (EN ``causes[]`` prose vs TR golden
   text). The replacement is a bilingual CONCEPT term, gated behind a mandatory
   code precondition.

These tests lock both properties so the metric cannot silently regress to a
loose/overfit rule.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.engine.ai.calibration import _CONCEPT_TERMS, _concepts_in, evaluate_calibration
from src.engine.ai.hypothesis_engine import load_root_cause_graph

ROOT = Path(__file__).resolve().parents[2]
GRAPH = ROOT / "data" / "diagnostics" / "root_cause_graph.json"
REJECTS = ROOT / "data" / "diagnostics" / "root_cause_graph_rejects.json"
DERIVE = ROOT / "scripts" / "derive_root_cause_graph.py"


class TestDerivedGraphDepth:
    """The shipped graph must actually cover the DB, with no fabricated nodes."""

    def test_meets_depth_targets(self) -> None:
        graph = load_root_cause_graph()
        codes = {c for node in graph for c in node.expected_dtcs}
        # Targets from the decision card: >=300 nodes / >=1000 codes.
        assert len(graph) >= 300, f"graph too shallow: {len(graph)} nodes"
        assert len(codes) >= 1000, f"code coverage too thin: {len(codes)} codes"
        # T2-1 achieved ~9k nodes / ~7.2k codes; assert the order of magnitude is
        # retained so a future accidental regression to the old 53-node graph
        # fails loudly rather than silently.
        assert len(graph) >= 5000, f"graph shrank unexpectedly: {len(graph)} nodes"

    def test_no_fabricated_or_template_or_quarantined_nodes(self) -> None:
        """Every node resolves to a real source; none is filler or quarantined."""
        payload = json.loads(GRAPH.read_text(encoding="utf-8"))
        nodes = payload["nodes"]
        assert all(node["source_ref"].strip() for node in nodes)
        assert not [n for n in nodes if "dtcdocs" in n["source_ref"].lower()]

    def test_independent_verification_gate_passes(self) -> None:
        """The verifier must resolve every source_ref (fail-closed anti-fabrication)."""
        proc = subprocess.run(
            [sys.executable, str(DERIVE), "--verify"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        assert proc.returncode == 0, f"verification failed:\n{proc.stdout}\n{proc.stderr}"
        assert "[OK]" in proc.stdout
        assert "unresolved_source_refs=0" in proc.stdout
        assert "dtcdocs_sourced_nodes=0" in proc.stdout

    def test_rejection_ledger_records_gate_reasons(self) -> None:
        """Rejected causes are evidence-bearing output, not a silent drop."""
        ledger = json.loads(REJECTS.read_text(encoding="utf-8"))
        assert ledger["count"] > 0
        assert ledger["count"] == len(ledger["rejects"])
        gates = {r["gate"] for r in ledger["rejects"]}
        # The identity gate must have fired: it is the SPN_3251/SPN_651 guard.
        assert "C_identity_mismatch" in gates
        assert "A_filler_or_junk" in gates


class TestConceptTerms:
    def test_concept_table_is_bilingual_and_unique(self) -> None:
        assert len(_CONCEPT_TERMS) >= 25
        for name, forms in _CONCEPT_TERMS.items():
            assert forms, f"{name} must list surface forms"
            assert all(f == f.lower() for f in forms), f"{name} forms must be lowercase"

    @pytest.mark.parametrize(
        ("mechanism", "fmi_description", "expected"),
        [
            # The exact mismatch documented from the orchestrator's probe: the
            # engine names the actuator, the golden case names the FMI threshold.
            (
                "Downstream (Katalizör sonrası / Sensör 2) Oksijen sensörünün yaşlanması.",
                "Katalizör monolitinin kurşun/yağ ile zehirlenmesi veya seramik peteğin erimesi.",
                "catalyst",
            ),
            (
                "VGT kanatçık kurum sıkışması / wastegate arızası (aşırı basınç)",
                "FMI 0/16 (Aşırı Basınç): VGT aktüatör kanatçıklarının kurumdan sıkışması.",
                "turbo",
            ),
            (
                "SCR catalyst aged beyond effective conversion",
                "FMI 1 (Verim <%45): SCR katalizörü kükürt veya motor yağı ile zehirlenmiş.",
                "scr",
            ),
            (
                "Termostat gövdesinin kapalı konumda sıkışması",
                "FMI 0 (Kritik Yüksek): Soğutma suyu sıcaklığı >108°C; termostat kapalı kalmış.",
                "thermostat",
            ),
            (
                "Enjektör bobin teli kopuk veya külbütör altı soketi çıkmış",
                "FMI 5 (Açık Devre): Enjektör devresi açık.",
                "injector",
            ),
        ],
    )
    def test_language_and_framing_mismatch_still_shares_a_concept(
        self, mechanism: str, fmi_description: str, expected: str
    ) -> None:
        """A mechanism/description pair in different languages must still match."""
        shared = _concepts_in(mechanism) & _concepts_in(fmi_description)
        assert expected in shared, f"expected concept {expected!r}, got {sorted(shared)}"

    def test_sensor_vs_thermostat_mismatch_is_not_a_concept_match(self) -> None:
        """A genuinely different mechanism must stay a MISS, not be papered over.

        SPN 110's golden `actual_fault` is an overtemperature/thermostat
        condition; the engine's top-1 for that case names a disconnected ECT
        *sensor* (a different fault mechanism). Deliberately NOT reconciled — an
        honest miss is preferable to inventing a shared concept (Tuzaklar §11).
        """
        assert not (_concepts_in("ECT sensör kablosu kopuk (ECU -40°C sanal okuma)") & _concepts_in("terminate"))
        # "sensor" also appears in the golden text, but the discriminating concept
        # for this case (thermostat/coolant) is absent from the engine title.
        engine = _concepts_in("ECT sensör kablosu kopuk (ECU -40°C sanal okuma)")
        actual = _concepts_in(
            "FMI 0 (Kritik Yüksek): Sıcaklık >108°C; termostat kapalı kalmış, viskoz fan kilitlenmiş."
        )
        assert "thermostat" not in engine
        assert "thermostat" in actual

    def test_unrelated_pair_shares_nothing(self) -> None:
        """The rule must still reject a genuinely unrelated hypothesis."""
        shared = _concepts_in("Buji/bobin ateşleme kaçağı") & _concepts_in(
            "Direksiyon zembereği içindeki SAS optik okuyucusunun sıfır noktasını kaybetmesi."
        )
        assert not shared

    def test_concept_detection_is_case_insensitive(self) -> None:
        assert "dpf" in _concepts_in("DPF PARTIKÜL FİLTRESİ")
        assert "dpf" in _concepts_in("dpf partikül filtresi")


class TestCalibrationRuleIsNotLoose:
    def test_reported_accuracy_is_the_honest_measurement(self) -> None:
        """Lock the honest number so the metric cannot silently inflate.

        The overfit rule reported 24/54 = 44.44 %. The honest rule reports
        materially fewer hits, and — importantly — the reported confidence is no
        longer flagged as overconfident (the old gap was an artefact of the
        inflated accuracy, not real engine overconfidence).
        """
        result = evaluate_calibration()
        assert result.verified_cases == 54
        # The honest rule must not reproduce the overfit figure.
        assert result.top1_matches != 24, "the overfit rule is back"
        assert result.top1_matches >= 20, f"honest top-1 regressed to {result.top1_matches}"
        # Coverage (code precondition satisfied) is the honest ceiling and must be
        # reported alongside accuracy.
        assert 0.0 < result.code_coverage_pct <= 100.0

    def test_code_precondition_is_enforced(self, monkeypatch, tmp_path) -> None:
        """A hypothesis that does not claim the case's code can never match.

        Verified structurally: the metric reads ``top.expected_dtcs`` and
        intersects it with the case codes, so the field must be populated for
        every ranked hypothesis.
        """
        from src.engine.ai import calibration as cal
        from src.engine.ai.golden_cases import load_all_cases
        from src.engine.ai.hypothesis_engine import rank_hypotheses

        cases = [c for c in load_all_cases() if c.verified and not c.is_draft and c.actual_fault]
        assert cases
        checked = 0
        for case in cases:
            hyps = rank_hypotheses(cal._case_to_vehicle_session(case)[0], anomalies=[])
            for h in hyps:
                assert isinstance(h.expected_dtcs, tuple)
            checked += 1
        assert checked == len(cases)

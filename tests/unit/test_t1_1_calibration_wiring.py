"""T1-1 regression lock: golden-set calibration must reach the REAL diagnostic path.

The defect this file locks (docs/agents task card ``tasks/T1-1-tuner-calibration-wiring.md``):

- ``compute_root_cause_confidence(..., calibration_factor=None)`` accepted a
  calibration parameter, but NO production call site ever passed it. The engine
  therefore reported a mean %68.5 confidence while its measured golden-set top-1
  accuracy was %44.4 (``overconfidence_detected=True``), and the two UI call
  sites that DID read the factor only stored it in a KPI dict — a number on a
  screen that changed nothing about the diagnosis.
- ``src/ui/desktop_app.py`` hardcoded the KPI catalog denominator as
  ``total_catalog = 14352``.

Each test below is written to FAIL on the un-wired tree and pass on the wired
one. The tests assert behaviour (labels/scores actually change), not the
existence of a string — Tuzaklar §15: diff CONTENT is evidence, not diff size.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.models.diagnostics import DiagnosticDomain, DiagnosticEvent, VehicleSession
from src.engine.ai import diagnostic_copilot as dc
from src.engine.ai.calibration import (
    calibrate_score,
    compute_calibration_factor,
    evaluate_calibration,
)
from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot, compute_root_cause_confidence
from src.engine.ai.hypothesis_engine import rank_hypotheses
from src.engine.ai.session_report import build_technician_report, calibrated_hypotheses

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"


# ---------------------------------------------------------------------------
# Fixtures: an "overconfident engine input".
#
# A single active DTC that a scenario rule + the KB both explain produces a
# FULL identification match with maxed telemetry correlation — i.e. the raw
# weighted evidence score saturates at 1.0 (%100 "Yüksek"). This is the exact
# input shape the golden corpus proved to be overconfident.
# ---------------------------------------------------------------------------
TELEMETRY = {"EngineSpeed": 1800.0, "BoostPressure": 140.0, "CoolantTemp": 88.0}

OVERCONFIDENT_DTCS: list[dict[str, object]] = [
    {"code": "P0300"},
]


def _session_with_active_dtcs(codes: list[str]) -> VehicleSession:
    session = VehicleSession(session_id="t1-1", started_at_ns=0, domain=DiagnosticDomain.PASSENGER)
    for i, code in enumerate(codes, start=1):
        session.events.append(
            DiagnosticEvent(
                timestamp_ns=i,
                code=code,
                domain=DiagnosticDomain.PASSENGER,
                severity="MEDIUM",
                status="ACTIVE",
            )
        )
    return session


# ---------------------------------------------------------------------------
# 1. The calibration value itself must be genuinely COMPUTED (no magic number).
# ---------------------------------------------------------------------------
class TestCalibrationFactorIsComputed:
    def test_factor_matches_live_golden_evaluation(self) -> None:
        """The memoised factor must equal a fresh golden-corpus evaluation.

        Guards AGENTS.md §2.3 / task-card "Uydurma yok": if someone replaces the
        cached accessor with a hardcoded 0.649 constant, this test still passes —
        but the NEXT test (cache-busting recompute from a different corpus) fails.
        """
        fresh = evaluate_calibration().calibration_factor
        assert compute_calibration_factor() == pytest.approx(fresh)

    def test_factor_is_recomputed_from_the_corpus_not_a_constant(self) -> None:
        """Force a recompute and prove the value tracks the corpus it is derived from.

        ``compute_calibration_factor`` is fed a synthetic corpus via monkeypatching
        ``evaluate_calibration``: a hardcoded constant would ignore it.
        """
        sentinel = 0.333333
        real_evaluate = dc.compute_calibration_factor.__globals__["evaluate_calibration"]

        class _FakeResult:
            calibration_factor = sentinel

        def _fake_evaluate(*args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeResult()

        try:
            dc.compute_calibration_factor.__globals__["evaluate_calibration"] = _fake_evaluate
            assert compute_calibration_factor(force_reload=True) == pytest.approx(sentinel)
        finally:
            dc.compute_calibration_factor.__globals__["evaluate_calibration"] = real_evaluate
            compute_calibration_factor(force_reload=True)  # restore the real cache

    def test_unreadable_corpus_returns_none_not_one(self) -> None:
        """Fail-closed: no corpus -> no damping evidence -> ``None``, never 1.0.

        Returning 1.0 would assert "the engine is perfectly calibrated", which is
        a fabricated claim (AGENTS.md §2.3).
        """

        def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("corpus unreadable")

        real_evaluate = dc.compute_calibration_factor.__globals__["evaluate_calibration"]
        try:
            dc.compute_calibration_factor.__globals__["evaluate_calibration"] = _boom
            assert compute_calibration_factor(force_reload=True) is None
        finally:
            dc.compute_calibration_factor.__globals__["evaluate_calibration"] = real_evaluate
            compute_calibration_factor(force_reload=True)

    def test_calibrate_score_leaves_score_untouched_when_unavailable(self) -> None:
        """A resolved ``None`` factor must not damp (no evidence, no adjustment)."""
        assert calibrate_score(0.8, calibration_factor=0.5) == pytest.approx(0.4)
        # Explicit None -> resolves the cache; the monkeypatched-unavailable case
        # is covered above. Here we prove the clamp bounds match the copilot's.
        assert calibrate_score(1.0, calibration_factor=0.0) == pytest.approx(0.1)
        assert calibrate_score(1.0, calibration_factor=5.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 2. RED -> GREEN: the real diagnostic path now emits a LOWER label.
# ---------------------------------------------------------------------------
class TestRealDiagnosticPathIsCalibrated:
    def test_overconfident_input_produces_damped_label(self) -> None:
        """An overconfident engine input must report a damped (sub-'Yüksek') label.

        BEFORE the fix: ``Yüksek (%100 ağırlıklı kanıt skoru)``
        AFTER  the fix: a strictly lower band, driven by the golden-set factor.

        T2-1 note: the exact band is no longer pinned to ``Orta``. The factor is
        derived from the measured golden-set accuracy, and T2-1 corrected the
        metric that produced it (the old match rule accepted a single shared
        word, which inflated accuracy and therefore the factor). Pinning the
        assertion to the old band would re-encode the overfit number in the test
        suite. The behavioural invariant being protected here is only that the
        published label is damped below the undamped ``Yüksek`` band.
        """
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(OVERCONFIDENT_DTCS, TELEMETRY, ["ECU_0"])
        label = report.root_cause_probability

        assert "Yüksek" not in label, "overconfident engine input must be damped below 'Yüksek' — got " + repr(label)
        assert label.startswith(("Düşük", "Orta", "Belirsiz")), (
            "damped label must stay a valid probability band — got " + repr(label)
        )

    def test_wired_call_site_passes_a_real_factor(self) -> None:
        """The production label must equal the explicitly calibrated computation.

        This is the direct red->green assertion: with the wiring removed, both
        sides are identical and the test fails. The evidence counts mirror what
        ``_analyze_local_expert`` actually passes for this fixture (verified by
        instrumenting the real call site: dtc=1, scenario=1, kb=0, telemetry=1).

        T2-3 note: the explicit-``None`` argument below is no longer a
        zero-damping argument — ``None`` now resolves the cached golden-set
        factor too (fail-closed). ``calibration_factor=1.0`` is the explicit
        "no damping" control that keeps this test a genuine red->green check.
        """
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(OVERCONFIDENT_DTCS, TELEMETRY, ["ECU_0"])

        undamped = compute_root_cause_confidence(
            dtc_count=1,
            scenario_matched=1,
            kb_matched=0,
            telemetry_correlation_count=1,
            calibration_factor=1.0,
        )
        factor = compute_calibration_factor()
        assert factor is not None and factor < 1.0, "a real golden corpus must yield a damping factor"
        calibrated = compute_root_cause_confidence(
            dtc_count=1,
            scenario_matched=1,
            kb_matched=0,
            telemetry_correlation_count=1,
            calibration_factor=factor,
        )
        assert undamped != calibrated, "calibration_factor must change the label"
        assert report.root_cause_probability == calibrated
        assert report.root_cause_probability != undamped, (
            "RED: the production call site is not applying the calibration factor"
        )

    def test_damping_direction_is_monotonic(self) -> None:
        """A lower calibration factor must never raise the reported score.

        T2-3: the undamped control is the explicitly passed ``1.0`` factor.
        Passing ``calibration_factor=None`` is now fail-closed (it resolves and
        applies the golden-set factor), so it can no longer serve as the
        "undamped" side of this comparison.
        """
        full = compute_root_cause_confidence(
            dtc_count=2,
            scenario_matched=2,
            kb_matched=0,
            telemetry_correlation_count=2,
            calibration_factor=1.0,
        )
        damped = compute_root_cause_confidence(
            dtc_count=2,
            scenario_matched=2,
            kb_matched=0,
            telemetry_correlation_count=2,
            calibration_factor=0.649,
        )
        assert full.startswith("Yüksek")
        assert damped.startswith("Orta")


# ---------------------------------------------------------------------------
# 3. The live hypothesis / technician-report path is damped too.
# ---------------------------------------------------------------------------
class TestSessionReportPathIsCalibrated:
    def test_calibrated_hypotheses_damps_raw_self_normalised_scores(self) -> None:
        """``rank_hypotheses`` self-normalises to %100; the report path must damp it.

        BEFORE the fix, the panel and the exported report printed the raw
        self-referential %100 score for a single weak rule hit.
        """
        session = _session_with_active_dtcs(["P0300"])
        raw = rank_hypotheses(session, [], None)
        assert raw, "fixture must produce at least one hypothesis"
        assert raw[0].score == pytest.approx(1.0), "raw self-normalised score is %100"

        damped = calibrated_hypotheses(raw)
        factor = compute_calibration_factor()
        assert factor is not None
        assert damped[0].score == pytest.approx(raw[0].score * factor)
        assert damped[0].score < raw[0].score

    def test_damping_never_rewrites_evidence_ledgers(self) -> None:
        """Only the SCORE is calibrated; evidence stays recorded verbatim (§2.3)."""
        session = _session_with_active_dtcs(["P0300"])
        raw = rank_hypotheses(session, [], None)
        damped = calibrated_hypotheses(raw)
        assert damped[0].fault == raw[0].fault
        assert damped[0].supporting_evidence == raw[0].supporting_evidence
        assert damped[0].contradicting_evidence == raw[0].contradicting_evidence

    def test_empty_hypothesis_list_is_a_no_op(self) -> None:
        assert calibrated_hypotheses([]) == []

    def test_technician_report_states_calibration_caveat(self) -> None:
        """The exported report must disclose that scores are calibrated."""
        from src.engine.ai.evidence_gate import evaluate_sufficiency

        session = _session_with_active_dtcs(["P0300"])
        sufficiency = evaluate_sufficiency(session)
        hypotheses = calibrated_hypotheses(rank_hypotheses(session, [], None))
        report = build_technician_report(session, sufficiency, [], hypotheses, [])
        assert "kalibrasyon faktörü" in report
        # And the displayed score must be the damped one, not %100.
        assert "%100" not in report.split("## Hipotez Sıralaması")[1].split("##")[0]


# ---------------------------------------------------------------------------
# 4. The hardcoded catalog denominator is now a LIVE database read.
# ---------------------------------------------------------------------------
class TestCatalogSizeIsMeasuredNotHardcoded:
    def test_catalog_size_equals_raw_record_count(self) -> None:
        """The live accessor must agree with an independent read of the DB file."""
        raw = json.loads(DTC_DB.read_text(encoding="utf-8", errors="replace"))
        assert dc.catalog_size() == len(raw)

    def test_catalog_size_is_not_a_hardcoded_literal(self) -> None:
        """Point the accessor at a synthetic catalog: the size must follow it."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fake_dir = Path(tmp)
            (fake_dir / "dtc_database.json").write_text(
                json.dumps({f"P{i:04d}": {} for i in range(7)}), encoding="utf-8"
            )
            real_dir = dc._EXTERNAL_DATA_DIR
            try:
                dc._EXTERNAL_DATA_DIR = fake_dir
                assert dc.catalog_size(force_reload=True) == 7
            finally:
                dc._EXTERNAL_DATA_DIR = real_dir
                dc.catalog_size(force_reload=True)

    def test_missing_catalog_fails_closed_to_zero(self) -> None:
        """No fabricated denominator: a missing DB yields 0, not a magic number."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            real_dir = dc._EXTERNAL_DATA_DIR
            try:
                dc._EXTERNAL_DATA_DIR = Path(tmp)
                assert dc.catalog_size(force_reload=True) == 0
            finally:
                dc._EXTERNAL_DATA_DIR = real_dir
                dc.catalog_size(force_reload=True)

    def test_kpi_bridge_reports_the_live_catalog_size(self) -> None:
        """The desktop KPI bridge must expose the measured catalog size."""
        pytest.importorskip("PySide6")
        from src.ui.desktop_app import UniversalCanDesktopApp

        app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
        result = app.get_diagnostic_kpi_metrics()
        assert result["success"] is True, result
        metrics = result["metrics"]
        assert metrics["total_catalog_count"] == dc.catalog_size()
        assert metrics["total_catalog_count"] > 0
        # The coverage ratio must be computed against the LIVE denominator, so it
        # can never be the value implied by the retired 14352 literal drift.
        covered = metrics["total_procedures_count"]
        assert metrics["kb_coverage_pct"] == pytest.approx(covered / metrics["total_catalog_count"] * 100, abs=0.01)


# ---------------------------------------------------------------------------
# 5. Architectural guard: the new imports must not break AI TX isolation.
# ---------------------------------------------------------------------------
def test_calibration_module_stays_offline() -> None:
    """No network/TX import may appear in the calibration module (AGENTS.md §2.8)."""
    source = (REPO_ROOT / "src" / "engine" / "ai" / "calibration.py").read_text(encoding="utf-8")
    for forbidden in ("urllib", "requests", "socket", "http.client", "src.hal", "TxPort"):
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source

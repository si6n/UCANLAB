"""Unit tests for AI Engine Roadmap FAZ 3 (Inference Power, Intervals & Local Learning)."""

import time
from pathlib import Path

from src.core.models.diagnostics import DiagnosticDomain, DiagnosticEvent, SignalSample, SignalSource, VehicleSession
from src.engine.ai.discriminating_tests import propose_actionable_discriminating_tests
from src.engine.ai.hypothesis_engine import Hypothesis, rank_hypotheses
from src.engine.ai.user_kb import load_operator_feedback, record_operator_feedback


def test_hypothesis_confidence_intervals_and_prior() -> None:
    session = VehicleSession(
        session_id="test-ci-sess",
        started_at_ns=time.monotonic_ns(),
        domain=DiagnosticDomain.HEAVY_DUTY,
    )
    session.events.append(
        DiagnosticEvent(
            timestamp_ns=time.monotonic_ns(),
            code="SPN 100",
            status="ACTIVE",
            severity="HIGH",
            domain=DiagnosticDomain.HEAVY_DUTY,
        )
    )
    # Add samples
    for i in range(12):
        session.samples.append(
            SignalSample(
                timestamp_ns=time.monotonic_ns() + (i * 10_000_000_000),
                name="EngineOilPressure",
                raw_value=40,
                physical_value=40.0,
                unit="kPa",
                source=SignalSource.J1939,
                confidence=1.0,
            )
        )

    hypotheses = rank_hypotheses(session, anomalies=[])
    assert len(hypotheses) > 0
    top = hypotheses[0]
    assert 0.0 <= top.confidence_interval[0] <= top.confidence_interval[1] <= 1.0
    assert 0.0 < top.prior_probability <= 1.0
    d = top.to_dict()
    assert "confidence_interval" in d
    assert "prior_probability" in d


def test_actionable_discriminating_tests_structure() -> None:
    h1 = Hypothesis(id="oil-pump-wear", fault="Oil pump wear", score=0.8)
    h2 = Hypothesis(id="oil-sensor-shorts", fault="Oil sensor short", score=0.75)
    tests = propose_actionable_discriminating_tests([h1, h2], dtc_codes=["SPN 100"])
    assert len(tests) > 0
    t1 = tests[0]
    assert len(t1.expected_value) > 0
    assert len(t1.measurement_point) > 0
    assert len(t1.safety_note) > 0


def test_local_operator_feedback() -> None:
    feedback_file = Path("data/temp_test_feedback.json")
    try:
        res1 = record_operator_feedback(
            dtc="P0101",
            resolved=True,
            notes="Hava emiş körüğü kelepçesi gevşekti, sıkıldı ve çözüldü. VIN: WVWZZZ1KZAW999999",
            feedback_path=feedback_file,
        )
        assert res1["status"] == "saved"
        assert res1["resolved"] is True

        records = load_operator_feedback(feedback_file)
        assert len(records) == 1
        rec = records[0]
        assert rec["dtc"] == "P0101"
        assert rec["resolved"] is True
        # VIN must be masked!
        assert "WVWZZZ1KZAW999999" not in rec["notes"]
        assert "***********" in rec["notes"]
    finally:
        if feedback_file.exists():
            feedback_file.unlink()


def test_golden_set_calibration_evaluation() -> None:
    from src.engine.ai.calibration import evaluate_calibration

    res = evaluate_calibration()
    assert res.total_cases >= 30
    assert res.verified_cases >= 30
    assert 0.0 <= res.mean_confidence <= 1.0
    assert 0.0 <= res.calibration_factor <= 1.0
    d = res.to_dict()
    assert "accuracy_pct" in d
    assert "calibration_factor" in d

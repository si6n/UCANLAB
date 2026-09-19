"""Unit tests for AI Engine Roadmap FAZ 0 (Safety Charter & Metrics)."""


from src.engine.ai.drive_safety_policy import (
    MUTATING_AI_ACTIONS,
    READ_ONLY_AI_ACTIONS,
    validate_ai_dialogue_action,
)
from src.engine.ai.metrics import (
    compute_golden_accuracy,
    compute_kb_coverage,
    compute_metrics_dashboard,
)


def test_read_only_actions_permitted_unconditionally() -> None:
    for action in READ_ONLY_AI_ACTIONS:
        allowed, msg = validate_ai_dialogue_action(action, confirmed_by_operator=False, vehicle_speed_kmh=50.0)
        assert allowed is True
        assert "onaylandı" in msg or "izin verildi" in msg


def test_mutating_actions_fail_closed_without_operator_confirmation() -> None:
    for action in MUTATING_AI_ACTIONS:
        allowed, msg = validate_ai_dialogue_action(action, confirmed_by_operator=False, vehicle_speed_kmh=0.0)
        assert allowed is False
        assert "çift onayı olmadan" in msg


def test_mutating_actions_fail_closed_with_missing_or_moving_speed() -> None:
    # Speed is None
    allowed, msg = validate_ai_dialogue_action("PROPOSE_WRITE", confirmed_by_operator=True, vehicle_speed_kmh=None)
    assert allowed is False
    assert "telemetrisi eksik" in msg.lower()

    # Vehicle is moving
    allowed, msg = validate_ai_dialogue_action("PROPOSE_DTC_CLEAR", confirmed_by_operator=True, vehicle_speed_kmh=12.5)
    assert allowed is False
    assert "hareketsiz değil" in msg.lower()


def test_mutating_actions_permitted_when_confirmed_and_stationary() -> None:
    for action in MUTATING_AI_ACTIONS:
        allowed, msg = validate_ai_dialogue_action(action, confirmed_by_operator=True, vehicle_speed_kmh=0.0)
        assert allowed is True
        assert "izin verildi" in msg


def test_unknown_action_fails_closed() -> None:
    allowed, msg = validate_ai_dialogue_action("ARBITRARY_HACK", confirmed_by_operator=True, vehicle_speed_kmh=0.0)
    assert allowed is False
    assert "yetkisiz eylem" in msg or "Bilinmeyen" in msg


def test_diagnostic_metrics_calculations() -> None:
    assert compute_kb_coverage(25, 100) == 25.0
    assert compute_kb_coverage(0, 100) == 0.0
    assert compute_kb_coverage(10, 0) == 0.0

    assert compute_golden_accuracy(18, 20) == 90.0
    assert compute_golden_accuracy(0, 20) == 0.0
    assert compute_golden_accuracy(5, 0) == 0.0

    metrics = compute_metrics_dashboard(
        covered_dtcs=50,
        total_dtcs=200,
        golden_correct=27,
        golden_total=30,
        total_diagnoses=10,
        total_questions=35,
        false_diagnoses=1,
    )
    assert metrics.kb_coverage_pct == 25.0
    assert metrics.golden_set_accuracy_pct == 90.0
    assert metrics.avg_questions_per_diagnosis == 3.5
    assert metrics.false_diagnosis_rate_pct == 10.0
    assert metrics.correct_diagnoses == 9
    assert metrics.false_diagnoses == 1
    d = metrics.to_dict()
    assert d["kb_coverage_pct"] == 25.0

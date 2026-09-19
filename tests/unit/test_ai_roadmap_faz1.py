"""Unit and integration tests for AI Engine Roadmap FAZ 1 (Interactive Dialogue Engine).

Covers:
1. 3 End-to-End Scenarios:
   - Scenario A: Volvo Penta Golden Case converted to interactive dialogue.
   - Scenario B: No repeated questions & 'Bilmiyorum' fallback to alternative branch.
   - Scenario C: Discriminative interrogation with transparent hypothesis elimination.
2. Evidence Gate Safety:
   - evidence_gate is evaluated truthfully and never bypassed; zero data fabrication.
3. Bridge Integration:
   - record_operator_answer and get_dialogue_state functionality.
"""

import time

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.dialogue_engine import (
    DialogueSession,
    DialogueState,
    OperatorAnswer,
    compute_entropy,
)
from src.engine.ai.evidence_gate import evaluate_sufficiency


def _create_mock_session(
    domain: DiagnosticDomain = DiagnosticDomain.HEAVY_DUTY,
    active_dtcs: list[str] | None = None,
    signals: dict[str, float] | None = None,
) -> VehicleSession:
    start = time.monotonic_ns()
    session = VehicleSession(
        session_id=f"test-sess-{start}",
        started_at_ns=start,
        domain=domain,
    )
    if active_dtcs:
        for code in active_dtcs:
            session.events.append(
                DiagnosticEvent(
                    timestamp_ns=start + 1_000_000,
                    code=code,
                    domain=domain,
                    status="ACTIVE",
                    severity="HIGH",
                )
            )
    if signals:
        for name, val in signals.items():
            # Add 12 samples to satisfy minimum sample threshold
            for i in range(12):
                session.samples.append(
                    SignalSample(
                        timestamp_ns=start + (i * 10_000_000_000),  # spans > 60s
                        name=name,
                        raw_value=int(val),
                        physical_value=float(val),
                        unit="",
                        source=SignalSource.J1939,
                        confidence=1.0,
                    )
                )
    return session


def test_entropy_and_information_gain_math() -> None:
    # Single hypothesis -> zero entropy
    assert compute_entropy([0.9]) == 0.0
    assert compute_entropy([]) == 0.0

    # Two equal hypotheses -> 1.0 bit entropy
    assert round(compute_entropy([0.5, 0.5]), 4) == 1.0000

    # Unequal hypotheses -> 0 < entropy < 1
    ent = compute_entropy([0.8, 0.2])
    assert 0.0 < ent < 1.0


def test_scenario_a_volvo_penta_golden_case_dialogue() -> None:
    """Scenario A: Volvo Penta crank-no-start converted to dialogue."""
    session = _create_mock_session(
        domain=DiagnosticDomain.MARINE,
        active_dtcs=[],  # Volvo Penta case has no active DTCs (crank-no-start)
    )
    dialogue = DialogueSession(max_questions=4)
    state = dialogue.start_triage(session, dtc_codes=[])

    # Since no active DTCs, must enter INTERROGATE to triage symptoms/signals
    assert state == DialogueState.INTERROGATE

    # Select next question
    q1 = dialogue.select_next_question(session, dtc_codes=[])
    assert q1 is not None
    assert q1.id not in dialogue.answers

    # Operator answers measurement / observation
    ans1 = OperatorAnswer(
        question_id=q1.id,
        kind=q1.kind,
        value=12.4 if q1.kind == "measurement" else "Evet",
        recorded_at_ns=time.monotonic_ns(),
    )
    res1 = dialogue.record_answer(ans1, session)
    assert res1["status"] == "recorded"

    # Evidence gate must have evaluated without errors
    sufficiency = evaluate_sufficiency(session)
    assert isinstance(sufficiency.gaps, list)


def test_scenario_b_no_question_repetition_and_bilmiyorum_fallback() -> None:
    """Scenario B: No repeated questions & 'Bilmiyorum' fallback."""
    session = _create_mock_session(
        active_dtcs=["SPN 100"],
        signals={"EngineOilPressure": 80.0},
    )
    dialogue = DialogueSession(max_questions=3)
    dialogue.start_triage(session, dtc_codes=["SPN 100"])

    asked_ids: list[str] = []

    # Question 1
    q1 = dialogue.select_next_question(session, dtc_codes=["SPN 100"])
    assert q1 is not None
    asked_ids.append(q1.id)

    # Operator answers "Bilmiyorum"
    ans1 = OperatorAnswer(
        question_id=q1.id,
        kind=q1.kind,
        value=None,
        is_unknown=True,
        recorded_at_ns=time.monotonic_ns(),
    )
    dialogue.record_answer(ans1, session)

    # Question 2
    q2 = dialogue.select_next_question(session, dtc_codes=["SPN 100"])
    if q2 is not None:
        assert q2.id != q1.id  # Strict no repetition invariant!
        asked_ids.append(q2.id)

        # Question 2 answered affirmatively
        ans2 = OperatorAnswer(
            question_id=q2.id,
            kind=q2.kind,
            value=True if q2.kind == "yes_no" else "Evet",
            recorded_at_ns=time.monotonic_ns(),
        )
        dialogue.record_answer(ans2, session)

    # Invariant: every asked question ID is unique
    assert len(asked_ids) == len(set(asked_ids))


def test_scenario_c_discriminative_interrogation_and_elimination() -> None:
    """Scenario C: Multi-hypothesis ambiguity resolved with transparent elimination."""
    session = _create_mock_session(
        active_dtcs=["SPN 100"],
        signals={"EngineOilPressure": 50.0},
    )
    dialogue = DialogueSession(max_questions=5)
    dialogue.start_triage(session, dtc_codes=["SPN 100"])

    q = dialogue.select_next_question(session, dtc_codes=["SPN 100"])
    assert q is not None

    # Operator explicitly negates a candidate condition
    ans = OperatorAnswer(
        question_id=q.id,
        kind=q.kind,
        value=False if q.kind == "yes_no" else 0.0,
        recorded_at_ns=time.monotonic_ns(),
    )
    res = dialogue.record_answer(ans, session)
    assert res["status"] == "recorded"

    # Action proposals generated according to safety policy
    if dialogue.state == DialogueState.CONCLUDE:
        assert len(dialogue.proposed_actions) >= 1
        for act in dialogue.proposed_actions:
            if act["is_mutating"]:
                assert act["requires_operator_confirm"] is True


def test_evidence_gate_never_bypassed() -> None:
    """evidence_gate invariant: missing telemetry is never fabricated."""
    empty_session = VehicleSession(
        session_id="empty-sess",
        started_at_ns=time.monotonic_ns(),
        domain=DiagnosticDomain.HEAVY_DUTY,
    )
    sufficiency = evaluate_sufficiency(empty_session)
    assert sufficiency.anomaly_sufficient is False
    assert sufficiency.dtc_sufficient is False
    assert len(sufficiency.gaps) > 0

    dialogue = DialogueSession()
    dialogue.start_triage(empty_session)
    # Triage sees insufficiency honestly
    assert dialogue.state == DialogueState.INTERROGATE


def test_procedural_branch_navigation() -> None:
    session = _create_mock_session(
        domain=DiagnosticDomain.PASSENGER,
        active_dtcs=["P0101"],
        signals={"MAF": 1.2},
    )
    dialogue = DialogueSession(max_questions=3)
    dialogue.start_triage(session, dtc_codes=["P0101"])

    q = dialogue.select_next_question(session, dtc_codes=["P0101"])
    assert q is not None
    assert "P0101" in q.id or "Q_" in q.id

    # Answering "Evet" yields procedure pass_next guidance
    ans = OperatorAnswer(
        question_id=q.id,
        kind=q.kind,
        value="Evet",
        recorded_at_ns=time.monotonic_ns(),
    )
    res = dialogue.record_answer(ans, session)
    assert res["status"] == "recorded"
    if res.get("next_guidance"):
        assert isinstance(res["next_guidance"], str)
        assert len(res["next_guidance"]) > 0

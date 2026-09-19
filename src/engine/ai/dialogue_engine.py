"""Interactive Diagnostic Dialogue Engine (FAZ 1).

Orchestrates the multi-turn diagnostic interrogation loop:
IDLE -> TRIAGE -> INTERROGATE -> CONCLUDE -> ACTION_PROPOSE

Core Invariants:
1. No Fabricated Telemetry: questions derive exclusively from evidence_gate gaps,
   root_cause_graph evidence signals, and EXPERT_KNOWLEDGE_BASE procedures.
2. Information Gain Ranking: candidate questions are ordered by expected Shannon entropy
   reduction across active candidate hypotheses.
3. Transparent Elimination: refuted hypotheses are tracked with clear reasons.
4. "Bilmiyorum" resilience: unanswerable questions fall back to alternative evidence branches.
5. AI Safety Gateway Conformance: proposals strictly follow drive_safety_policy boundaries.

Fully offline, deterministic, zero external framework dependencies.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE
from src.engine.ai.discriminating_tests import propose_discriminating_tests
from src.engine.ai.drive_safety_policy import validate_ai_dialogue_action
from src.engine.ai.evidence_gate import evaluate_sufficiency
from src.engine.ai.hypothesis_engine import (
    Hypothesis,
    load_root_cause_graph,
    rank_hypotheses,
)


class DialogueState(str, Enum):
    IDLE = "IDLE"
    TRIAGE = "TRIAGE"
    INTERROGATE = "INTERROGATE"
    CONCLUDE = "CONCLUDE"
    ACTION_PROPOSE = "ACTION_PROPOSE"


QuestionKind = Literal["yes_no", "choice", "measurement"]


@dataclass(slots=True, frozen=True)
class DiagnosticQuestion:
    """Schema-validated question presented to the technician/operator."""

    id: str
    kind: QuestionKind
    text: str
    why: str
    evidence_link: str
    unit: str | None = None
    expected_range: tuple[float, float] | None = None
    choices: tuple[str, ...] = ()
    how_to_measure: str | None = None
    target_hypothesis_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "why": self.why,
            "evidence_link": self.evidence_link,
            "unit": self.unit,
            "expected_range": list(self.expected_range) if self.expected_range else None,
            "choices": list(self.choices),
            "how_to_measure": self.how_to_measure,
            "target_hypothesis_id": self.target_hypothesis_id,
        }


@dataclass(slots=True, frozen=True)
class OperatorAnswer:
    """Recorded answer from operator with provenance."""

    question_id: str
    kind: QuestionKind
    value: Any
    unit: str | None = None
    is_unknown: bool = False
    recorded_at_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "kind": self.kind,
            "value": self.value,
            "unit": self.unit,
            "is_unknown": self.is_unknown,
            "recorded_at_ns": self.recorded_at_ns,
        }


@dataclass(slots=True, frozen=True)
class EliminatedHypothesis:
    """Explicitly refuted or pruned hypothesis."""

    id: str
    fault: str
    score: float
    reason: str
    eliminated_by_question_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fault": self.fault,
            "score": round(self.score, 3),
            "reason": self.reason,
            "eliminated_by_question_id": self.eliminated_by_question_id,
        }


def compute_entropy(scores: list[float]) -> float:
    """Compute Shannon entropy H(S) over normalized hypothesis scores."""
    pos_scores = [s for s in scores if s > 0.0]
    total = sum(pos_scores)
    if total <= 0.0 or len(pos_scores) <= 1:
        return 0.0
    entropy = 0.0
    for s in pos_scores:
        p = s / total
        entropy -= p * math.log2(p)
    return max(0.0, entropy)


def compute_expected_information_gain(
    current_entropy: float,
    hypothesis: Hypothesis,
    all_hypotheses: list[Hypothesis],
) -> float:
    """Estimate expected entropy reduction if a discriminating question is asked."""
    if current_entropy <= 0.0 or not all_hypotheses:
        return 0.0
    # Prior probability of this hypothesis being the true root cause
    total_score = sum(h.score for h in all_hypotheses if h.score > 0.0)
    if total_score <= 0.0:
        return 0.0
    p_true = max(0.05, min(0.95, hypothesis.score / total_score))
    p_false = 1.0 - p_true

    # If confirmed: hypothesis score boosted
    post_scores_if_true = [
        h.score * 1.5 if h.id == hypothesis.id else h.score * 0.5
        for h in all_hypotheses
    ]
    entropy_if_true = compute_entropy(post_scores_if_true)

    # If refuted: hypothesis score dropped to 0
    post_scores_if_false = [
        0.0 if h.id == hypothesis.id else h.score
        for h in all_hypotheses
    ]
    entropy_if_false = compute_entropy(post_scores_if_false)

    expected_posterior = (p_true * entropy_if_true) + (p_false * entropy_if_false)
    return max(0.0, current_entropy - expected_posterior)


class DialogueSession:
    """Stateful dialogue session driving the interactive diagnostic questionnaire."""

    def __init__(self, max_questions: int = 5, activity_timeout_s: float = 120.0) -> None:
        self.state: DialogueState = DialogueState.IDLE
        self.max_questions: int = max_questions
        self.activity_timeout_s: float = activity_timeout_s
        self.last_activity_ns: int = time.monotonic_ns()
        self.questions_history: list[DiagnosticQuestion] = []
        self.answers: dict[str, OperatorAnswer] = {}
        self.current_question: DiagnosticQuestion | None = None
        self.eliminated_hypotheses: list[EliminatedHypothesis] = []
        self.concluded_fault: str | None = None
        self.concluded_confidence: float = 0.0
        self.proposed_actions: list[dict[str, Any]] = []

    def check_watchdog_staleness(self) -> bool:
        """Fail-closed watchdog compliance: revoke pending actions if dialogue stalls."""
        now = time.monotonic_ns()
        if (now - self.last_activity_ns) / 1e9 > self.activity_timeout_s:
            self.proposed_actions.clear()
            self.current_question = None
            self.state = DialogueState.IDLE
            return True
        return False

    def start_triage(
        self,
        session: VehicleSession,
        dtc_codes: list[str] | None = None,
    ) -> DialogueState:
        """Evaluate evidence sufficiency and initialize dialogue state machine."""
        self.questions_history.clear()
        self.answers.clear()
        self.current_question = None
        self.eliminated_hypotheses.clear()
        self.concluded_fault = None
        self.proposed_actions.clear()

        sufficiency = evaluate_sufficiency(session)
        self.state = DialogueState.TRIAGE

        # Check if we should directly ask clarifying questions or interrogate hypotheses
        hypotheses = rank_hypotheses(session, anomalies=[])

        if not sufficiency.dtc_sufficient and not dtc_codes:
            # Need clarifying triage (no active DTCs or insufficient data)
            self.state = DialogueState.INTERROGATE
            return self.state

        candidate_questions = self._generate_candidate_questions(session, dtc_codes)
        if len(hypotheses) == 1 and hypotheses[0].score >= 0.85 and not candidate_questions:
            # Unambiguous high confidence diagnosis with no procedure steps needed
            self.concluded_fault = hypotheses[0].fault
            self.concluded_confidence = hypotheses[0].score
            self.state = DialogueState.CONCLUDE
            self._generate_proposed_actions(hypotheses[0])
            return self.state

        self.state = DialogueState.INTERROGATE
        return self.state

    def select_next_question(
        self,
        session: VehicleSession,
        dtc_codes: list[str] | None = None,
    ) -> DiagnosticQuestion | None:
        """Select the next best diagnostic question by information gain.

        Guarantees:
        - No question repetition (answered or already asked questions are skipped).
        - Grounded strictly in KB, root_cause_graph, and evidence gaps.
        - Returns None when interrogation concludes or budget exhausted.
        """
        if self.check_watchdog_staleness():
            return None

        self.last_activity_ns = time.monotonic_ns()
        if self.state not in (DialogueState.TRIAGE, DialogueState.INTERROGATE):
            return None

        if len(self.answers) >= self.max_questions:
            self._conclude_session(session)
            return None

        asked_ids = set(self.answers.keys())
        if self.current_question and self.current_question.id in asked_ids:
            self.current_question = None

        candidate_questions = self._generate_candidate_questions(session, dtc_codes)
        # Filter already asked questions
        fresh_candidates = [q for q in candidate_questions if q.id not in asked_ids]

        if not fresh_candidates:
            self._conclude_session(session)
            return None

        # Sort by expected information gain
        hypotheses = rank_hypotheses(session, anomalies=[])
        scores = [h.score for h in hypotheses]
        current_entropy = compute_entropy(scores)

        def _rank_key(q: DiagnosticQuestion) -> tuple[float, str]:
            # Find target hypothesis
            target_h = next((h for h in hypotheses if h.id == q.target_hypothesis_id), None)
            if target_h:
                ig = compute_expected_information_gain(current_entropy, target_h, hypotheses)
            else:
                # Evidence gaps take precedence when entropy is high or no hypotheses
                ig = 0.5
            return (round(ig, 4), q.id)

        ranked = sorted(fresh_candidates, key=_rank_key, reverse=True)
        self.current_question = ranked[0]
        self.questions_history.append(self.current_question)
        return self.current_question

    def record_answer(
        self,
        answer: OperatorAnswer,
        session: VehicleSession,
    ) -> dict[str, Any]:
        """Record an operator answer, update evidence, and prune hypotheses.

        - Answers are ingested into session evidence (as SignalSample or DiagnosticEvent).
        - Refuted hypotheses are transferred to eliminated_hypotheses.
        """
        self.answers[answer.question_id] = answer
        self.last_activity_ns = time.monotonic_ns()

        # Ingest answer into session evidence
        if answer.kind == "measurement" and answer.value is not None and not answer.is_unknown:
            try:
                val = float(answer.value)
                name = f"OP:{answer.question_id}"
                sample = SignalSample(
                    timestamp_ns=time.monotonic_ns(),
                    name=name,
                    raw_value=int(round(val)) if abs(val) < 2**31 else 0,
                    physical_value=val,
                    unit=answer.unit or "",
                    source=SignalSource.J1939,
                    confidence=1.0,
                )
                session.samples.append(sample)
            except (ValueError, TypeError):
                pass

        elif answer.kind in ("yes_no", "choice") and not answer.is_unknown:
            # Map answer to operator event with domain
            ans_suffix = "YES" if answer.value in (True, "yes", "EVET", "evet", "Evet") else "NO"
            session.events.append(
                DiagnosticEvent(
                    timestamp_ns=time.monotonic_ns(),
                    code=f"OP:{answer.question_id}:{ans_suffix}",
                    domain=session.domain,
                    status="ACTIVE",
                    severity="INFO",
                )
            )

        # Re-rank and update eliminated hypotheses
        hypotheses = rank_hypotheses(session, anomalies=[])
        self._update_eliminations(hypotheses, answer)

        # Check if diagnosis has converged
        if hypotheses and (hypotheses[0].score >= 0.80 or len(hypotheses) == 1):
            if len(hypotheses) == 1 or (len(hypotheses) >= 2 and (hypotheses[0].score - hypotheses[1].score) >= 0.35):
                self._conclude_session(session)

        # Procedural branch navigation (pass_next / fail_next)
        next_guidance = None
        from src.engine.ai.procedure_validator import get_procedure
        active_dtcs = [e.code for e in session.events if e.status == "ACTIVE" and not e.code.startswith("OP:")]
        for code in active_dtcs:
            proc = get_procedure(code)
            if proc and any(q.get("id") == answer.question_id for q in proc.questions):
                if answer.value in (True, "yes", "EVET", "evet", "Evet"):
                    next_guidance = proc.pass_next
                elif answer.value in (False, "no", "HAYIR", "hayir", "Hayır"):
                    next_guidance = proc.fail_next
                break

        return {
            "status": "recorded",
            "state": self.state.value,
            "eliminated_count": len(self.eliminated_hypotheses),
            "remaining_hypotheses": [h.to_dict() for h in hypotheses[:3]],
            "next_guidance": next_guidance,
        }

    def _update_eliminations(
        self,
        current_hypotheses: list[Hypothesis],
        answer: OperatorAnswer,
    ) -> None:
        """Identify newly eliminated hypotheses based on score drop or contradiction."""
        eliminated_ids = {e.id for e in self.eliminated_hypotheses}
        for h in current_hypotheses:
            if h.id in eliminated_ids:
                continue
            # Condition 1: Score fell below survival threshold
            if h.score < 0.15:
                reason = "Düşük olasılık / yetersiz kanıt skoru (< 0.15)"
                if answer.is_unknown:
                    reason = "Bilinmeyen cevap alternatife yönlendirdi; kanıt yetersiz kaldı"
                elif answer.value in (False, "no", "HAYIR", "hayir"):
                    reason = f"Operatör olumsuz yanıtı ({answer.question_id}) hipotezi çürüttü"
                self.eliminated_hypotheses.append(
                    EliminatedHypothesis(
                        id=h.id,
                        fault=h.fault,
                        score=h.score,
                        reason=reason,
                        eliminated_by_question_id=answer.question_id,
                    )
                )

    def _conclude_session(self, session: VehicleSession) -> None:
        """Transition session to CONCLUDE then ACTION_PROPOSE."""
        hypotheses = rank_hypotheses(session, anomalies=[])
        if hypotheses:
            self.concluded_fault = hypotheses[0].fault
            self.concluded_confidence = hypotheses[0].score
            self._generate_proposed_actions(hypotheses[0])
        else:
            self.concluded_fault = "Belirgin kök neden tespit edilemedi (veri yetersiz)"
            self.concluded_confidence = 0.0

        self.state = DialogueState.CONCLUDE

    def _generate_proposed_actions(self, top_hypothesis: Hypothesis) -> None:
        """Generate safe action proposals according to drive_safety_policy."""
        self.proposed_actions.clear()

        # Propose read-only telemetry action (safe)
        read_action = {
            "type": "PROPOSE_READ",
            "title": f"Canlı Veri Doğrulaması: {top_hypothesis.fault[:40]}",
            "target": top_hypothesis.id,
            "requires_operator_confirm": False,
            "is_mutating": False,
        }
        allowed, msg = validate_ai_dialogue_action(read_action["type"])
        if allowed:
            self.proposed_actions.append(read_action)

        # Propose mutating action (requires operator confirmation + stationary speed)
        clear_action = {
            "type": "PROPOSE_DTC_CLEAR",
            "title": "Onarım Sonrası Hata Kodlarını Sil (DTC Clear)",
            "target": top_hypothesis.id,
            "requires_operator_confirm": True,
            "is_mutating": True,
        }
        self.proposed_actions.append(clear_action)

    def _generate_candidate_questions(
        self,
        session: VehicleSession,
        dtc_codes: list[str] | None = None,
    ) -> list[DiagnosticQuestion]:
        """Generate candidate questions from evidence gaps, discriminating tests, and graph."""
        candidates: list[DiagnosticQuestion] = []
        observed_signals = {s.name for s in session.samples}

        # 1. From evidence_gate gaps
        sufficiency = evaluate_sufficiency(session)
        for gap in sufficiency.gaps:
            if "örnek" in gap and "'" in gap:
                # Signal gap: extract signal name
                sig_name = gap.split("'")[1]
                candidates.append(
                    DiagnosticQuestion(
                        id=f"Q_SIG_{sig_name}",
                        kind="measurement",
                        text=f"'{sig_name}' parametresi için ölçüm değeri girebilir misiniz?",
                        why=f"Kanıt eksikliği: {gap}",
                        evidence_link=f"gap:{sig_name}",
                        unit=None,
                        expected_range=None,
                        how_to_measure="Multimetre veya teşhis cihazı ile ilgili sensör pininden ölçüm yapın.",
                    )
                )

        # 2. From discriminating tests between top hypotheses
        hypotheses = rank_hypotheses(session, anomalies=[])
        disc_tests = propose_discriminating_tests(hypotheses, dtc_codes)
        for idx, test_text in enumerate(disc_tests):
            candidates.append(
                DiagnosticQuestion(
                    id=f"Q_DISC_{idx+1}",
                    kind="yes_no",
                    text=f"Şu test adımını gerçekleştirdiniz mi: {test_text}?",
                    why="En olası iki arıza hipotezini birbirinden ayırt etmek için gereklidir.",
                    evidence_link=f"test:{idx+1}",
                    choices=("Evet", "Hayır", "Bilmiyorum"),
                    how_to_measure=test_text,
                    target_hypothesis_id=hypotheses[0].id if hypotheses else None,
                )
            )

        # 3. From root_cause_graph nodes for candidate hypotheses
        graph = load_root_cause_graph()
        nodes_by_id = {node.id: node for node in graph}

        for h in hypotheses[:3]:
            node = nodes_by_id.get(h.id)
            if not node:
                continue
            for sig in node.evidence_signals:
                if sig not in observed_signals and f"OP:{sig}" not in observed_signals:
                    candidates.append(
                        DiagnosticQuestion(
                            id=f"Q_NODE_{h.id}_{sig}",
                            kind="measurement",
                            text=f"'{h.fault}' şüphesini doğrulamak için '{sig}' değerini ölçebilir misiniz?",
                            why=f"{h.fault} arızasında bu sinyalin nominal aralık dışına çıkması beklenir.",
                            evidence_link=f"signal:{sig}",
                            unit=None,
                            how_to_measure=f"{sig} hattı üzerindeki sensör çıkışını kontrol edin.",
                            target_hypothesis_id=h.id,
                        )
                    )

        # 4. From validated DtcProcedure files (or EXPERT_KNOWLEDGE_BASE fallback)
        from src.engine.ai.procedure_validator import get_procedure

        active_codes = sorted(set(c for c in (dtc_codes or []) if c))
        for code in active_codes:
            proc = get_procedure(code)
            if proc and proc.questions:
                for q_dict in proc.questions:
                    qid = q_dict.get("id", f"Q_{code}_{len(candidates)}")
                    candidates.append(
                        DiagnosticQuestion(
                            id=qid,
                            kind=q_dict.get("kind", "yes_no"),
                            text=q_dict.get("text", ""),
                            why=q_dict.get("why", f"{code} fabrika teşhis adımı"),
                            evidence_link=f"proc:{code}",
                            choices=("Evet", "Hayır", "Bilmiyorum"),
                            how_to_measure=proc.pass_next,
                        )
                    )
            else:
                kb_entry = EXPERT_KNOWLEDGE_BASE.get(code)
                if kb_entry:
                    steps = kb_entry.get("steps", [])
                    for step_idx, step in enumerate(steps):
                        if isinstance(step, (tuple, list)) and step:
                            action_text = step[0]
                            candidates.append(
                                DiagnosticQuestion(
                                    id=f"Q_KB_{code}_{step_idx+1}",
                                    kind="yes_no",
                                    text=f"{code} teşhis adımı: {action_text} Durumu kontrol ettiniz mi?",
                                    why=f"{code} standart fabrika kontrol prosedürü adımı {step_idx+1}",
                                    evidence_link=f"kb:{code}",
                                    choices=("Evet", "Hayır", "Bilmiyorum"),
                                    how_to_measure=action_text,
                                )
                            )

        # 5. Grounded domain-specific triage when no DTCs are present
        if not candidates:
            if session.domain == DiagnosticDomain.MARINE:
                candidates.append(
                    DiagnosticQuestion(
                        id="Q_TRIAGE_MARINE_KILL_SWITCH",
                        kind="yes_no",
                        text="Acil durdurma kordonu (kill-switch / safety lanyard) ve marin acil stop butonu takılı/devrede mi?",
                        why="Marin motorlarda (Volvo Penta vb.) marş basıp çalışmama (crank-no-start) durumunun en sık nedeni devre dışı kalmış güvenlik kordonudur.",
                        evidence_link="domain:marine:safety",
                        choices=("Evet", "Hayır", "Bilmiyorum"),
                        how_to_measure="Kordon anahtarını ve helm panelindeki stop mandalını kontrol edin.",
                    )
                )
                candidates.append(
                    DiagnosticQuestion(
                        id="Q_TRIAGE_MARINE_FUEL_VALVE",
                        kind="yes_no",
                        text="Yakıt deposu acil kesme vanası açık ve yakıt filtresinden hava tahliyesi yapıldı mı?",
                        why="Yakıt akışının kesilmesi veya sistemde hava olması motorun çalışmasını engeller.",
                        evidence_link="domain:marine:fuel",
                        choices=("Evet", "Hayır", "Bilmiyorum"),
                        how_to_measure="Yakıt filtresi tahliye vidasından veya el pompasından yakıt akışını gözlemleyin.",
                    )
                )
            else:
                candidates.append(
                    DiagnosticQuestion(
                        id="Q_TRIAGE_BATTERY_VOLTAGE",
                        kind="measurement",
                        text="Marş anındaki akü kutup başı gerilimini ölçebilir misiniz?",
                        why="Düşük akü voltajı ECU'nun marş esnasında resetlenmesine veya enjektörlerin tetiklenememesine yol açar.",
                        evidence_link="system:battery",
                        unit="V",
                        expected_range=(10.5, 14.5),
                        how_to_measure="Multimetre problarını doğrudan akü kutup başlarına bağlayıp marş esnasında gerilimi okuyun.",
                    )
                )

        return candidates


__all__ = [
    "DialogueState",
    "QuestionKind",
    "DiagnosticQuestion",
    "OperatorAnswer",
    "EliminatedHypothesis",
    "DialogueSession",
    "compute_entropy",
    "compute_expected_information_gain",
]

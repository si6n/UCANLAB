"""Evidence scoring and confidence reporting for the Signal Discovery Engine.

Implements the signal_discovery_spec §1 contract: `evidence.py` — Evidence,
ConfidenceReport; weighted score + human-readable explanations.

Weighting scheme (spec §4 thresholds):
- crc_match_ratio >= 0.99 is "definitive", >= 0.90 "candidate", >= 0.80 "hint".
- monotonic +1 ratio >= 0.90 is strong counter evidence.
- entropy ~0 marks CONST; uniform ~8 bits marks NOISY (CRC candidate).

Complies with MASTER_PLAN.md Section 7 and docs/specs/signal_discovery_spec.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.discovery.hypotheses import Hypothesis

# Spec §4 evidence kind weights: how strongly each kind of evidence should
# pull the aggregate confidence toward (or away from) a hypothesis.
EVIDENCE_KIND_WEIGHTS: dict[str, float] = {
    "crc_match_ratio": 0.35,
    "monotonicity": 0.30,
    "flip_rate": 0.15,
    "entropy": 0.10,
    "correlation": 0.10,
}

# Spec §4 verdict thresholds on the aggregate weighted score.
CONFIDENCE_DEFINITIVE: float = 0.99
CONFIDENCE_CANDIDATE: float = 0.90
CONFIDENCE_HINT: float = 0.80


@dataclass(slots=True)
class ConfidenceReport:
    """Weighted confidence verdict for a single hypothesis.

    The aggregate score is the evidence-weighted mean of kind scores
    (each evidence value clamped to 0..1), with the sum of the kind weights
    normalising the result. Human-readable explanations accompany every
    contributing evidence item for technician review.
    """

    hypothesis: Hypothesis
    weighted_score: float
    verdict: str  # "definitive" | "candidate" | "hint" | "weak"
    explanations: list[str] = field(default_factory=list)

    @property
    def is_exportable(self) -> bool:
        """True when the hypothesis reached at least candidate confidence."""
        return self.weighted_score >= CONFIDENCE_CANDIDATE


def score_hypothesis(hypothesis: Hypothesis) -> ConfidenceReport:
    """Compute the weighted confidence report for a hypothesis.

    Each evidence item contributes `value` weighted by its kind's weight;
    unrecognised kinds contribute with a small default weight so new
    detectors still surface. The weighted mean is normalised by the total
    weight actually present, so a hypothesis with a single strong evidence
    kind is not penalised for the others' absence.
    """
    explanations: list[str] = []
    if not hypothesis.evidence:
        return ConfidenceReport(
            hypothesis=hypothesis,
            weighted_score=0.0,
            verdict="weak",
            explanations=["No evidence collected for this hypothesis."],
        )

    total_weight = 0.0
    weighted_sum = 0.0
    for evidence in hypothesis.evidence:
        weight = EVIDENCE_KIND_WEIGHTS.get(evidence.kind, 0.05)
        value = min(1.0, max(0.0, evidence.value))
        weighted_sum += weight * value
        total_weight += weight
        explanations.append(f"[{evidence.kind}] {evidence.detail} (value={value:.3f}, weight={weight:.2f})")

    weighted_score = weighted_sum / total_weight if total_weight > 0 else 0.0

    if weighted_score >= CONFIDENCE_DEFINITIVE:
        verdict = "definitive"
    elif weighted_score >= CONFIDENCE_CANDIDATE:
        verdict = "candidate"
    elif weighted_score >= CONFIDENCE_HINT:
        verdict = "hint"
    else:
        verdict = "weak"

    return ConfidenceReport(
        hypothesis=hypothesis,
        weighted_score=weighted_score,
        verdict=verdict,
        explanations=explanations,
    )


def evidence_report(hypothesis: Hypothesis) -> str:
    """Render a human-readable, evidence-weighted verdict for one hypothesis.

    Spec §3: `evidence_report()` returns a human-readable evidence report;
    here it is scoped per hypothesis so IdReport-level rendering can compose
    them (the engine's generate_evidence_markdown does exactly that).
    """
    report = score_hypothesis(hypothesis)
    lines = [
        f"Hypothesis {hypothesis.htype} @ bits {hypothesis.start_bit}.."
        f"{hypothesis.start_bit + hypothesis.length - 1} (len={hypothesis.length})",
        f"Verdict: {report.verdict} (weighted score {report.weighted_score:.3f})",
        "Evidence:",
    ]
    lines.extend(f"  - {line}" for line in report.explanations)
    return "\n".join(lines)

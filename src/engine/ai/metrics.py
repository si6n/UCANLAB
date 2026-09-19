"""Diagnostic Quality Metrics and KPI Tracking (FAZ 0).

Tracks the 4 core quality metrics defined in the AI Engine Roadmap:
1. KB Coverage % (derinlikli prosedür kapsamı)
2. Golden-Set Accuracy % (kalibrasyon / vaka doğrulama başarısı)
3. Average Questions per Diagnosis (teşhis başına ortalama soru sayısı)
4. False Diagnosis Rate % (yanlış / çelişkili teşhis oranı)

Fully offline, deterministic, zero external framework dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class DiagnosticMetrics:
    """Frozen snapshot of AI diagnostic engine performance metrics."""

    kb_coverage_pct: float
    golden_set_accuracy_pct: float
    avg_questions_per_diagnosis: float
    false_diagnosis_rate_pct: float
    total_diagnoses: int = 0
    total_questions_asked: int = 0
    correct_diagnoses: int = 0
    false_diagnoses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kb_coverage_pct": round(self.kb_coverage_pct, 2),
            "golden_set_accuracy_pct": round(self.golden_set_accuracy_pct, 2),
            "avg_questions_per_diagnosis": round(self.avg_questions_per_diagnosis, 2),
            "false_diagnosis_rate_pct": round(self.false_diagnosis_rate_pct, 2),
            "total_diagnoses": self.total_diagnoses,
            "total_questions_asked": self.total_questions_asked,
            "correct_diagnoses": self.correct_diagnoses,
            "false_diagnoses": self.false_diagnoses,
        }


def compute_kb_coverage(covered_count: int, total_catalog_count: int) -> float:
    """Compute KB procedure coverage percentage (0.0 to 100.0)."""
    if total_catalog_count <= 0 or covered_count <= 0:
        return 0.0
    return min(100.0, max(0.0, (covered_count / total_catalog_count) * 100.0))


def compute_golden_accuracy(correct_matches: int, total_evaluated: int) -> float:
    """Compute verified golden-set diagnostic accuracy percentage (0.0 to 100.0)."""
    if total_evaluated <= 0 or correct_matches <= 0:
        return 0.0
    return min(100.0, max(0.0, (correct_matches / total_evaluated) * 100.0))


def compute_metrics_dashboard(
    covered_dtcs: int,
    total_dtcs: int,
    golden_correct: int,
    golden_total: int,
    total_diagnoses: int,
    total_questions: int,
    false_diagnoses: int,
) -> DiagnosticMetrics:
    """Compute full diagnostic KPI dashboard snapshot (fail-safe)."""
    kb_cov = compute_kb_coverage(covered_dtcs, total_dtcs)
    golden_acc = compute_golden_accuracy(golden_correct, golden_total)

    avg_q = (
        float(total_questions / total_diagnoses)
        if total_diagnoses > 0
        else 0.0
    )

    false_rate = (
        min(100.0, max(0.0, (false_diagnoses / total_diagnoses) * 100.0))
        if total_diagnoses > 0
        else 0.0
    )

    correct_count = max(0, total_diagnoses - false_diagnoses)

    return DiagnosticMetrics(
        kb_coverage_pct=kb_cov,
        golden_set_accuracy_pct=golden_acc,
        avg_questions_per_diagnosis=avg_q,
        false_diagnosis_rate_pct=false_rate,
        total_diagnoses=total_diagnoses,
        total_questions_asked=total_questions,
        correct_diagnoses=correct_count,
        false_diagnoses=false_diagnoses,
    )


__all__ = [
    "DiagnosticMetrics",
    "compute_kb_coverage",
    "compute_golden_accuracy",
    "compute_metrics_dashboard",
]

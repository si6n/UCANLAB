"""Golden-Set Calibration & Confidence Evaluation Engine (FAZ 3).

Evaluates the diagnostic engine across all verified golden cases:
- Measures empirical accuracy and confidence calibration.
- Calibrates compute_root_cause_confidence to prevent overconfidence.
- Completely offline, deterministic, zero external framework dependencies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from src.core.models.diagnostics import DiagnosticEvent, SignalSample, SignalSource, VehicleSession
from src.engine.ai.golden_cases import GoldenCase, load_all_cases
from src.engine.ai.hypothesis_engine import rank_hypotheses


@dataclass(slots=True, frozen=True)
class CalibrationResult:
    """Outcome of evaluating golden cases calibration."""

    total_cases: int
    verified_cases: int
    top1_matches: int
    accuracy_pct: float
    mean_confidence: float
    overconfidence_detected: bool
    calibration_factor: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "verified_cases": self.verified_cases,
            "top1_matches": self.top1_matches,
            "accuracy_pct": round(self.accuracy_pct, 2),
            "mean_confidence": round(self.mean_confidence, 3),
            "overconfidence_detected": self.overconfidence_detected,
            "calibration_factor": round(self.calibration_factor, 3),
        }


def _case_to_vehicle_session(case: GoldenCase) -> VehicleSession:
    """Convert a GoldenCase into a VehicleSession for diagnostic evaluation."""
    session = VehicleSession(
        session_id=f"eval-{case.case_id}",
        started_at_ns=time.monotonic_ns(),
        domain=case.domain,
        make=case.make,
        model=case.model,
        vin_masked=None,
    )
    # Populate events from case DTCs
    for dtc in case.dtcs:
        code_str = dtc.get("code") if isinstance(dtc, dict) else getattr(dtc, "code", "")
        status_str = dtc.get("status", "ACTIVE") if isinstance(dtc, dict) else getattr(dtc, "status", "ACTIVE")
        session.events.append(
            DiagnosticEvent(
                timestamp_ns=session.started_at_ns + 1000,
                code=code_str,
                domain=case.domain,
                severity="HIGH",
                status=status_str,
            )
        )
    # Populate samples from signals of interest
    for i, sig in enumerate(case.signals_of_interest):
        sig_name = sig.get("name") if isinstance(sig, dict) else getattr(sig, "name", "Signal")
        session.samples.append(
            SignalSample(
                timestamp_ns=session.started_at_ns + ((i + 1) * 1_000_000),
                name=sig_name,
                raw_value=100,
                physical_value=100.0,
                unit="",
                source=SignalSource.J1939,
                confidence=1.0,
            )
        )
    return session


def evaluate_calibration(cases: list[GoldenCase] | None = None) -> CalibrationResult:
    """Run empirical calibration evaluation over verified golden cases."""
    all_cases = cases if cases is not None else load_all_cases()
    verified = [c for c in all_cases if c.verified and not c.is_draft and c.actual_fault]

    if not verified:
        return CalibrationResult(
            total_cases=len(all_cases),
            verified_cases=0,
            top1_matches=0,
            accuracy_pct=0.0,
            mean_confidence=0.0,
            overconfidence_detected=False,
            calibration_factor=1.0,
        )

    matches = 0
    conf_scores: list[float] = []

    for case in verified:
        session = _case_to_vehicle_session(case)
        hyps = rank_hypotheses(session, anomalies=[])
        if hyps:
            top = hyps[0]
            conf_scores.append(top.score)
            # Check if top hypothesis fault overlaps with actual fault
            clean_actual = (case.actual_fault or "").lower()
            clean_top = top.fault.lower()
            if any(w in clean_actual for w in clean_top.split() if len(w) > 4):
                matches += 1
        else:
            conf_scores.append(0.0)

    acc = (matches / len(verified)) * 100.0
    mean_conf = (sum(conf_scores) / len(conf_scores)) if conf_scores else 0.0

    # Overconfidence check: if mean reported confidence is significantly higher than empirical accuracy
    overconfident = (mean_conf * 100.0) > (acc + 15.0)
    cal_factor = min(1.0, (acc / (mean_conf * 100.0))) if mean_conf > 0 else 1.0

    return CalibrationResult(
        total_cases=len(all_cases),
        verified_cases=len(verified),
        top1_matches=matches,
        accuracy_pct=acc,
        mean_confidence=mean_conf,
        overconfidence_detected=overconfident,
        calibration_factor=cal_factor,
    )


__all__ = ["CalibrationResult", "evaluate_calibration"]

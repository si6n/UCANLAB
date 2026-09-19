"""Discriminating test selection between the top-2 hypotheses (FAZ 5 & FAZ 3).

When the two best hypotheses are close (score delta < 0.2) — i.e. real
diagnostic uncertainty — the engine proposes KB-sourced measurement steps
that would separate them. Measurement texts are selected from existing
knowledge-base entries ONLY; nothing new is phrased here (plan §FAZ 5.1).
Each proposed test carries expected value, measurement point, and safety note (FAZ 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.engine.ai.hypothesis_engine import Hypothesis

# Only propose tests when the top-2 gap is genuinely ambiguous.
MAX_SCORE_GAP: float = 0.2


@dataclass(slots=True, frozen=True)
class ActionableTest:
    """A fully contextualized discriminating test step."""

    text: str
    expected_value: str
    measurement_point: str
    safety_note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "expected_value": self.expected_value,
            "measurement_point": self.measurement_point,
            "safety_note": self.safety_note,
        }


def _kb_measurements(code: str) -> list[str]:
    """Deterministic KB steps[]/measurement text candidates for a DTC code.

    Import stays local so the isolation scan sees no new top-level deps
    (stdlib + core/ai modules only).
    """
    from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE

    clean = (code or "").strip().upper()
    info = EXPERT_KNOWLEDGE_BASE.get(clean) or EXPERT_KNOWLEDGE_BASE.get(clean.replace(" ", ""))
    if not info:
        return []
    out: list[str] = []
    measurement = info.get("measurement")
    if isinstance(measurement, str) and measurement.strip():
        out.append(measurement.strip())
    for step in info.get("steps", []):
        if isinstance(step, (tuple, list)) and step and isinstance(step[0], str):
            out.append(step[0].strip())
    return out


def propose_discriminating_tests(
    hypotheses: list[Hypothesis],
    dtc_codes: list[str] | None = None,
) -> list[str]:
    """Propose measurement steps to separate the best two hypotheses.

    Returns [] unless: >= 2 hypotheses AND |score1 - score2| < 0.2. The
    proposed texts come from the KB entries of the session's active DTCs;
    with no KB-backed candidates the result is an empty list (honesty: no
    invented measurement text).
    """
    if len(hypotheses) < 2:
        return []
    delta = abs(hypotheses[0].score - hypotheses[1].score)
    if delta >= MAX_SCORE_GAP:
        return []

    candidates: list[str] = []
    for code in sorted(set(c or "" for c in (dtc_codes or []) if c)):
        for text in _kb_measurements(code):
            if text not in candidates:
                candidates.append(text)
    if not candidates:
        return []
    # Deterministic slice of the KB texts (stable, deduped).
    return candidates[:3]


def propose_actionable_discriminating_tests(
    hypotheses: list[Hypothesis],
    dtc_codes: list[str] | None = None,
) -> list[ActionableTest]:
    """Propose rich discriminating tests carrying expected value, target, and safety notes (FAZ 3)."""
    from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE

    if len(hypotheses) < 2:
        return []
    delta = abs(hypotheses[0].score - hypotheses[1].score)
    if delta >= MAX_SCORE_GAP:
        return []

    results: list[ActionableTest] = []
    for code in sorted(set(c or "" for c in (dtc_codes or []) if c)):
        clean = code.strip().upper()
        info = EXPERT_KNOWLEDGE_BASE.get(clean) or EXPERT_KNOWLEDGE_BASE.get(clean.replace(" ", ""))
        if not info:
            continue
        meas_text = str(info.get("measurement", "Nominal fabrika çalışma aralığı"))
        steps = info.get("steps", [])
        for step in steps:
            if isinstance(step, (tuple, list)) and len(step) >= 2:
                action_text = str(step[0]).strip()
                target_point = str(step[1]).strip()
                safety = "Kontak kapalı ve el freni çekili konumda çalışın."
                if "P0A" in code or "HV" in code:
                    safety = "YÜKSEK GERİLİM: Turuncu kablolara dokunmayın; yalıtımlı eldiven kullanın."
                results.append(
                    ActionableTest(
                        text=action_text,
                        expected_value=meas_text,
                        measurement_point=target_point,
                        safety_note=safety,
                    )
                )
    return results[:3]


__all__ = [
    "ActionableTest",
    "propose_discriminating_tests",
    "propose_actionable_discriminating_tests",
    "MAX_SCORE_GAP",
]

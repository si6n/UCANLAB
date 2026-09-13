"""Discriminating test selection between the top-2 hypotheses (FAZ 5).

When the two best hypotheses are close (score delta < 0.2) — i.e. real
diagnostic uncertainty — the engine proposes KB-sourced measurement steps
that would separate them. Measurement texts are selected from existing
knowledge-base entries ONLY; nothing new is phrased here (plan §FAZ 5.1).
"""

from __future__ import annotations

from src.engine.ai.hypothesis_engine import Hypothesis

# Only propose tests when the top-2 gap is genuinely ambiguous.
MAX_SCORE_GAP: float = 0.2


def _kb_measurements(code: str) -> list[str]:
    """Deterministic KB steps[]/measurement text candidates for a DTC code.

    Import stays local so the isolation scan sees no new top-level deps
    (stdlib + core/ai modules only).
    """
    from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE

    info = EXPERT_KNOWLEDGE_BASE.get(code)
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


__all__ = ["propose_discriminating_tests", "MAX_SCORE_GAP"]

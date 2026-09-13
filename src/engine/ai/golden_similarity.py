"""Golden-Case similarity search over the calibration-eligible corpus (FAZ 2).

Deterministic Jaccard/domain/make/signal scoring against verified golden
cases (U1). Only ``calibration_eligible_cases()`` output participates —
drafts and unverified cases can never influence a similarity claim.

Conservative honesty rules (plan §FAZ 2):
- best score < 0.5  -> empty result list (no weak matches shown as evidence)
- best score < 0.65 -> matches are labelled low-confidence in the report
- corpus < 2 cases  -> "Yüksek benzerlik" wording is forbidden downstream

The similarity is a heuristic comparison score, NOT a probability: reports
must always carry the "%X benzerlik" phrasing with the weighted-evidence
caveat (over-trust risk, plan §Riskler).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.core.models.diagnostics import DiagnosticDomain, VehicleSession
from src.engine.ai.golden_cases import GoldenCase, calibration_eligible_cases

# Conservative thresholds (plan §FAZ 2).
MIN_REPORTABLE_SCORE: float = 0.5
HIGH_SIMILARITY_SCORE: float = 0.65
WEIGHTS = {"dtc_jaccard": 0.50, "domain": 0.15, "make": 0.10, "signals": 0.25}


@dataclass(slots=True, frozen=True)
class CaseMatch:
    """One similar verified golden case with deterministic scoring reasons."""

    case_id: str
    similarity: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"case_id": self.case_id, "similarity": round(self.similarity, 3), "reasons": list(self.reasons)}


def _normalize_code(code: str) -> str:
    """Canonicalize DTC code spellings for set comparison.

    SPN/FMI forms normalize to "SPN <n>" (with or without FMI); P-codes and
    friends are uppercased verbatim.
    """
    cleaned = " ".join(code.strip().upper().split())
    if cleaned.startswith("SPN"):
        parts = cleaned.split()
        if len(parts) >= 2:
            try:
                return f"SPN {int(parts[1])}"
            except ValueError:
                return cleaned
    return cleaned


def _normalize_signal(name: str) -> str:
    """lowercase; separators (space, dash, underscore) collapsed to a single '-'."""
    lowered = name.strip().lower()
    for sep in (" ", "_"):
        lowered = lowered.replace(sep, "-")
    while "--" in lowered:
        lowered = lowered.replace("--", "-")
    return lowered.strip("-")


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0  # two empty DTC sets share no diagnostic evidence
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _score_case(session: VehicleSession, case: GoldenCase) -> tuple[float, list[str]]:
    reasons: list[str] = []

    session_codes = frozenset(_normalize_code(e.code) for e in session.events)
    case_codes = frozenset(_normalize_code(d["code"]) for d in case.dtcs)
    dtc_sim = _jaccard(session_codes, case_codes)
    if dtc_sim > 0.0:
        reasons.append(f"DTC kesişimi (Jaccard {dtc_sim:.2f})")

    domain_sim = 1.0 if session.domain is case.domain else 0.0
    if domain_sim:
        reasons.append(f"domain eşleşmesi ({session.domain.value})")

    make_sim = 0.0
    if session.make and case.make:
        make_sim = 1.0 if _normalize_signal(session.make) == _normalize_signal(case.make) else 0.0
        if make_sim:
            reasons.append(f"marka eşleşmesi ({session.make})")

    session_signals = frozenset(_normalize_signal(s.name) for s in session.samples)
    case_signals = frozenset(
        _normalize_signal(str(sig["name"])) for sig in case.signals_of_interest if isinstance(sig.get("name"), str)
    )
    signal_sim = _jaccard(session_signals, case_signals)
    if signal_sim > 0.0:
        reasons.append(f"ilgi sinyali kesişimi ({signal_sim:.2f})")

    score = (
        WEIGHTS["dtc_jaccard"] * dtc_sim
        + WEIGHTS["domain"] * domain_sim
        + WEIGHTS["make"] * make_sim
        + WEIGHTS["signals"] * signal_sim
    )
    # Cap: a domain+make match without any DTC/signal overlap is metadata,
    # not diagnostic similarity.
    if dtc_sim == 0.0 and signal_sim == 0.0:
        score = min(score, 0.24)
        reasons.append("DTC/sinyal kanıtı yok — yalnız metadata eşleşmesi")
    return score, reasons


def find_similar_cases(
    session: VehicleSession,
    k: int = 3,
    cases_dir: "Path | None" = None,
) -> list[CaseMatch]:
    """Return up to k verified cases similar to the session, best first.

    Deterministic: identical session + corpus -> identical result list. Ties
    break on case_id (lexicographic) so dict/ordering never leaks in.
    """
    corpus = calibration_eligible_cases(cases_dir)
    scored: list[tuple[float, str, list[str]]] = []
    for case in corpus:
        score, reasons = _score_case(session, case)
        if score >= MIN_REPORTABLE_SCORE:
            scored.append((score, case.case_id, reasons))

    scored.sort(key=lambda t: (-t[0], t[1]))
    top = scored[: max(0, k)]
    return [CaseMatch(case_id=cid, similarity=s, reasons=r) for s, cid, r in top]


def similarity_confidence_label(matches: list[CaseMatch]) -> str:
    """Honesty label for reports (conservative thresholds, plan §FAZ 2).

    ""  -> no matches worth reporting.
    "düşük benzerlik — doğrulanmamış" -> best < 0.65.
    "benzer" -> best >= 0.65 AND >= 2 eligible corpus cases (the corpus size
    is re-checked here; with < 2 verified cases the "Yüksek benzerlik"
    wording stays forbidden).
    """
    corpus_size = len(calibration_eligible_cases())
    if not matches:
        return ""
    best = matches[0].similarity
    if best < HIGH_SIMILARITY_SCORE:
        return "düşük benzerlik — doğrulanmamış"
    if corpus_size < 2:
        return "düşük benzerlik — doğrulanmamış (korpus < 2 vaka)"
    return "benzer"


def _domain_from_str(value: str) -> DiagnosticDomain:
    return DiagnosticDomain(value)


# Re-export for tests/consumers that build sessions programmatically.
__all__ = [
    "CaseMatch",
    "find_similar_cases",
    "similarity_confidence_label",
    "MIN_REPORTABLE_SCORE",
    "HIGH_SIMILARITY_SCORE",
]

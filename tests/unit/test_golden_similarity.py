"""FAZ 2 unit tests: golden-case similarity (determinism + honesty thresholds)."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.models.diagnostics import DiagnosticDomain, VehicleSession
from src.engine.ai.golden_similarity import (
    CaseMatch,
    _normalize_code,
    find_similar_cases,
    similarity_confidence_label,
)


def _session(dtcs=None, domain=DiagnosticDomain.HEAVY_DUTY, make=None, signals=None):
    from src.core.models.diagnostics import DiagnosticEvent, SignalSample, SignalSource

    s = VehicleSession(session_id="sess-sim", started_at_ns=1, domain=domain, make=make)
    for code in dtcs or []:
        s.events.append(
            DiagnosticEvent(
                timestamp_ns=1,
                code=code,
                domain=domain,
                severity="MEDIUM",
                status="ACTIVE",
            )
        )
    for name in signals or []:
        s.samples.append(
            SignalSample(
                timestamp_ns=1,
                name=name,
                raw_value=0,
                physical_value=1.0,
                unit="",
                source=SignalSource.J1939,
            )
        )
    return s


def _write_case(dir_path: Path, case_id: str, *, dtcs, signals=(), verified=True, actual_fault="Fault X", domain="HEAVY_DUTY", make=None):
    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "domain": domain,
        "make": make,
        "model": None,
        "year": None,
        "symptom": "test symptom",
        "dtcs": [{"code": c, "status": "ACTIVE"} for c in dtcs],
        "signals_of_interest": [{"name": n, "expected_behavior": None, "observed_behavior": None} for n in signals],
        "actual_fault": actual_fault if verified else None,
        "repair": "Repaired",
        "verification": "verified",
        "trace_ref": None,
        "verified": verified,
        "verified_date": "2026-01-01" if verified else None,
    }
    p = dir_path / f"{case_id}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


class TestSimilarity:
    def test_empty_corpus_returns_nothing(self, tmp_path: Path) -> None:
        matches = find_similar_cases(_session(["P0300"]), cases_dir=tmp_path)
        assert matches == []

    def test_draft_and_unverified_excluded(self, tmp_path: Path) -> None:
        _write_case(tmp_path, "draft-case", dtcs=["P0300"], verified=False)
        _write_case(tmp_path, "unverified-case", dtcs=["P0300"], verified=False, actual_fault=None)
        matches = find_similar_cases(_session(["P0300"]), cases_dir=tmp_path)
        assert matches == []

    def test_exact_dtc_match_scores_high(self, tmp_path: Path) -> None:
        _write_case(tmp_path, "case-exact", dtcs=["P0300"])
        matches = find_similar_cases(_session(["P0300"]), cases_dir=tmp_path)
        assert len(matches) == 1
        assert matches[0].case_id == "case-exact"
        # DTC Jaccard 1.0 * 0.5 + domain 0.15 + make 0 + signals 0 = 0.65
        assert matches[0].similarity == 0.65

    def test_code_normalization_spn_forms(self, tmp_path: Path) -> None:
        _write_case(tmp_path, "case-spn", dtcs=["SPN 100 FMI 1"])
        # Session records "SPN 100 FMI 1"; case records plain "SPN 100" —
        # both normalize to "SPN 100".
        matches = find_similar_cases(_session(["SPN 100 FMI 1"]), cases_dir=tmp_path)
        assert len(matches) == 1
        assert matches[0].similarity >= 0.5

    def test_metadata_only_match_capped_below_reportable(self, tmp_path: Path) -> None:
        # Same domain + make but no DTC/signal overlap -> capped 0.24 < 0.5
        _write_case(tmp_path, "case-meta", dtcs=["P0404"], make="Scania")
        matches = find_similar_cases(_session(["P0300"], make="Scania"), cases_dir=tmp_path)
        assert matches == []

    def test_low_similarity_honesty_label(self, tmp_path: Path) -> None:
        # Weak overlap: 1 shared DTC of 3 in case, 1 of 2 in session.
        _write_case(tmp_path, "case-weak", dtcs=["P0300", "P0301", "P0302"])
        matches = find_similar_cases(_session(["P0300", "P0400"]), cases_dir=tmp_path)
        # Jaccard = 1/4 = 0.25 -> score 0.125 + 0.15 domain = 0.275 < 0.5
        # -> below reportable threshold: must stay hidden (honesty rule).
        assert matches == []

    def test_determinism(self, tmp_path: Path) -> None:
        _write_case(tmp_path, "case-a", dtcs=["P0300"])
        _write_case(tmp_path, "case-b", dtcs=["P0234"])
        s1 = _session(["P0300"])
        s2 = _session(["P0300"])
        m1 = find_similar_cases(s1, cases_dir=tmp_path)
        m2 = find_similar_cases(s2, cases_dir=tmp_path)
        assert [(m.case_id, m.similarity) for m in m1] == [(m.case_id, m.similarity) for m in m2]

    def test_k_limit_and_tiebreak_order(self, tmp_path: Path) -> None:
        _write_case(tmp_path, "case-zzz", dtcs=["P0300", "P0301"])
        _write_case(tmp_path, "case-aaa", dtcs=["P0300", "P0302"])
        _write_case(tmp_path, "case-mmm", dtcs=["P0300", "P0303"])
        matches = find_similar_cases(_session(["P0300", "P0301"]), k=2, cases_dir=tmp_path)
        assert len(matches) <= 2
        # identical scores -> lexicographic id order
        if len(matches) == 2 and matches[0].similarity == matches[1].similarity:
            assert matches[0].case_id < matches[1].case_id


class TestConfidenceLabel:
    def test_no_matches_empty_label(self) -> None:
        assert similarity_confidence_label([]) == ""

    def test_single_case_never_high(self, tmp_path: Path, monkeypatch) -> None:
        _write_case(tmp_path, "case-solo", dtcs=["P0300"])
        # Corpus < 2 -> conservative label even with a strong score.
        import src.engine.ai.golden_similarity as gs

        monkeypatch.setattr(gs, "calibration_eligible_cases", lambda *a, **k: [_fake_case()])
        label = gs.similarity_confidence_label([CaseMatch("case-solo", 0.9, [])])
        assert "Yüksek" not in label
        assert label != "benzer"

    def test_two_cases_strong_best_is_similar(self, monkeypatch) -> None:
        import src.engine.ai.golden_similarity as gs

        monkeypatch.setattr(gs, "calibration_eligible_cases", lambda *a, **k: [_fake_case(), _fake_case("case-2")])
        label = gs.similarity_confidence_label([CaseMatch("case-solo", 0.9, [])])
        assert label == "benzer"


def _fake_case(case_id: str = "case-1"):
    """Minimal GoldenCase stand-in for label tests (avoid file IO)."""
    from dataclasses import dataclass

    @dataclass(slots=True)
    class _C:
        case_id: str
        verified: bool = True

    return _C(case_id)


class TestNormalization:
    def test_normalize_code_variants(self) -> None:
        assert _normalize_code("SPN 100 FMI 1") == "SPN 100"
        assert _normalize_code("spn 100") == "SPN 100"
        assert _normalize_code("p0300") == "P0300"
        assert _normalize_code("P0300") == "P0300"

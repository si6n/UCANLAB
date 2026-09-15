"""T42 regression tests — P2 upgrades of the offline AI engine.

Task t_7832dc63: two P2 items from the T40 audit
(spn_gap_hunter/output/t40_ai_engine_audit.md, section (e)):

  P2-1  Çoklu-DTC birleşik analiz: the query path used to handle only
        ``active_dtcs[0]``. Now every active DTC is evaluated together and
        clustered by common subsystem (DB ``subsystem``) and common J1939 PGN
        (DB ``associated_pgn``) — DB-derived instead of only the 10 hardcoded
        ``_RELATED_CODE_GROUPS`` pairs. The session path also emits these
        clusters as correlations.
  P2-6  ``is_reserved`` / ``reserved_note`` (390 DTC): a reserved (ISO/SAE
        manufacturer-assigned) code gets a "not standard, OEM-specific"
        warning so it can never be presented as a generic fault.

Locked invariants (T42 task rules):
  1. AI layer stays offline — tests/safety/test_ai_tx_isolation.py stays green.
  2. Determinism: same input -> same output.
  3. Severity only from EXPERT_KNOWLEDGE_BASE or scenario rules.
  4. No fabrication (fail-safe): a block is only produced when the DB field is
     actually populated.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from src.engine.ai.diagnostic_copilot import (
    EXPERT_KNOWLEDGE_BASE,
    AiDiagnosticCopilot,
    CausalBayesianInferenceEngine,
    analyze_active_dtc_clusters,
    ensure_external_dtc_database_loaded,
    get_reserved_code_notice,
)


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()


def _pair_sharing_subsystem() -> tuple[str, str]:
    """Deterministically return two real codes that share a DB subsystem."""
    by_sub: dict[str, list[str]] = defaultdict(list)
    for code, info in EXPERT_KNOWLEDGE_BASE.items():
        if isinstance(info, dict) and isinstance(info.get("subsystem"), str) and info["subsystem"].strip():
            by_sub[info["subsystem"]].append(code)
    for sub in sorted(by_sub):
        members = sorted(by_sub[sub])
        if len(members) >= 2 and not any(
            EXPERT_KNOWLEDGE_BASE[c].get("is_reserved") for c in members[:2]
        ):
            return members[0], members[1]
    pytest.skip("no subsystem with >=2 non-reserved codes")


def _reserved_code() -> str:
    for code in sorted(EXPERT_KNOWLEDGE_BASE):
        info = EXPERT_KNOWLEDGE_BASE.get(code)
        if isinstance(info, dict) and info.get("is_reserved"):
            return code
    pytest.skip("no reserved code in the database")


class TestP2_1MultiDtcClustering:
    """P2-1: active DTCs are clustered by DB-derived common fields."""

    def test_clusters_are_deterministic_and_ordered(self) -> None:
        a, b = _pair_sharing_subsystem()
        dtcs = [{"code": b}, {"code": a}]  # reversed input order
        first = analyze_active_dtc_clusters(dtcs)
        second = analyze_active_dtc_clusters(list(reversed(dtcs)))
        assert first == second  # input order must not matter
        assert first["codes"] == sorted([a, b])

    def test_common_subsystem_group_derived_from_db(self) -> None:
        a, b = _pair_sharing_subsystem()
        clusters = analyze_active_dtc_clusters([{"code": a}, {"code": b}])
        sub_of = EXPERT_KNOWLEDGE_BASE[a]["subsystem"]
        assert [sub_of, sorted([a, b])] in [
            [s, m] for s, m in clusters["subsystem_groups"]
        ]

    def test_clusters_empty_for_unknown_codes(self) -> None:
        """Fail-safe: codes with no DB record produce no fabricated groups."""
        clusters = analyze_active_dtc_clusters([{"code": "Z9999"}, {"code": "Z9998"}])
        assert clusters["subsystem_groups"] == []
        assert clusters["pgn_groups"] == []
        assert clusters["reserved_codes"] == []

    def test_query_path_renders_combined_report(self) -> None:
        a, b = _pair_sharing_subsystem()
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            "aktif arızaları analiz et", [{"code": a}, {"code": b}], {}
        )
        assert "ÇOKLU-DTC BİRLEŞİK ANALİZ" in report
        assert a in report and b in report

    def test_query_path_multi_is_deterministic(self) -> None:
        a, b = _pair_sharing_subsystem()
        args = ("aktif arızaları analiz et", [{"code": a}, {"code": b}], {})
        assert CausalBayesianInferenceEngine.evaluate_diagnostic_query(*args) == (
            CausalBayesianInferenceEngine.evaluate_diagnostic_query(*args)
        )

    def test_single_code_keeps_classic_report(self) -> None:
        """A single active DTC must NOT get the combined multi-DTC block."""
        a, _ = _pair_sharing_subsystem()
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            "ariza nedir", [{"code": a}], {}
        )
        assert "ÇOKLU-DTC BİRLEŞİK ANALİZ" not in report

    def test_session_path_emits_db_clusters(self) -> None:
        a, b = _pair_sharing_subsystem()
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session([{"code": a}, {"code": b}], {}, ["ECU_0"])
        assert any("Ortak alt-sistem" in c for c in report.telemetry_correlations)


class TestP2_6ReservedCode:
    """P2-6: reserved (OEM-assigned) codes get a distinct warning."""

    def test_notice_present_for_reserved_code(self) -> None:
        code = _reserved_code()
        notice = get_reserved_code_notice(code)
        assert notice is not None
        assert "REZERVE KOD" in notice
        assert "ÜRETİCİYE ÖZEL" in notice.upper() or "üretici" in notice.lower()

    def test_notice_none_for_normal_code(self) -> None:
        """Fail-safe: a normal code must not produce a reserved warning."""
        info = EXPERT_KNOWLEDGE_BASE.get("P0300", {})
        if info.get("is_reserved"):
            pytest.skip("P0300 unexpectedly reserved")
        assert get_reserved_code_notice("P0300") is None
        assert get_reserved_code_notice("NOT_A_CODE") is None

    def test_reserved_notice_in_single_report(self) -> None:
        code = _reserved_code()
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            "ariza analiz et", [{"code": code}], {}
        )
        assert "REZERVE KOD — STANDART DEĞİL" in report

    def test_reserved_warning_in_combined_report(self) -> None:
        res = _reserved_code()
        a, b = _pair_sharing_subsystem()
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            "aktif arızaları analiz et", [{"code": a}, {"code": res}], {}
        )
        assert "REZERVE KOD UYARISI" in report
        assert res in report

    def test_session_reports_reserved_correlation(self) -> None:
        res = _reserved_code()
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session([{"code": res}], {}, ["ECU_0"])
        assert any("Rezerve kod" in c for c in report.telemetry_correlations)

    def test_reserved_codes_flagged_in_cluster_result(self) -> None:
        res = _reserved_code()
        clusters = analyze_active_dtc_clusters([{"code": res}])
        assert res in clusters["reserved_codes"]

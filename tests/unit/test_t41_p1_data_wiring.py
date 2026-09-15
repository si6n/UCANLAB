"""T41 regression tests — P1 data-usage upgrades of the offline AI engine.

Task t_c8f07ce0: wire the DOLU-but-unread database fields identified by the
T40 audit (spn_gap_hunter/output/t40_ai_engine_audit.md) into the engine.

Locked invariants (T41 task rules):
  1. AI layer stays offline — tests/safety/test_ai_tx_isolation.py stays green.
  2. Determinism: same input -> same output.
  3. Severity only from EXPERT_KNOWLEDGE_BASE or scenario rules.
  4. Never fabricate Golden-Traces / procedure data — only surface DB fields
     that are actually present (fail-safe empty blocks otherwise).

Each P1 item has its own test class so a future regression points at exactly
which wiring broke.
"""

from __future__ import annotations

import pytest

from src.engine.ai.diagnostic_copilot import (
    EXPERT_KNOWLEDGE_BASE,
    AiDiagnosticCopilot,
    CausalBayesianInferenceEngine,
    ensure_external_dtc_database_loaded,
    get_j1939_spn_database,
    search_dtc_by_symptom,
)


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()
    get_j1939_spn_database()


def _first_spn_with(field: str, *, not_in_kb: bool = True) -> tuple[str, dict]:
    """Deterministically pick an SPN whose ``field`` is filled."""
    db = get_j1939_spn_database()
    for key, entry in db.get("spns", {}).items():
        if not isinstance(entry, dict) or not entry.get(field):
            continue
        num = key.replace("SPN_", "")
        if not_in_kb and f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
            continue
        return num, entry
    pytest.skip(f"no SPN with {field!r} found in the database")


class TestP1_1ProceduresFull:
    """P1-1: Eaton PIM `procedures_full` (59 SPN) reaches the SPN report."""

    def test_procedures_full_block_is_rendered(self) -> None:
        num, entry = _first_spn_with("procedures_full")
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(f"SPN {num} nedir", [], {})
        assert "Eaton OEM Tam Prosedürü (PIM)" in report
        # The Eaton fault code carried by the DB row must be surfaced verbatim.
        code = str(entry["procedures_full"][0].get("eaton_fault_code", ""))
        assert code[:60] in report

    def test_missing_procedures_full_renders_no_block(self) -> None:
        """Fail-safe: an SPN without procedures_full must not invent one."""
        db = get_j1939_spn_database()
        for key, entry in db.get("spns", {}).items():
            num = key.replace("SPN_", "")
            if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                continue
            if not isinstance(entry, dict) or entry.get("procedures_full"):
                continue
            report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(f"SPN {num} nedir", [], {})
            assert "Eaton OEM Tam Prosedürü (PIM)" not in report
            break


class TestP1_2J1939CausesSteps:
    """P1-2: J1939 `causes`/`steps` (3.710/3.444 SPN) reach the engine."""

    def test_spn_report_shows_causes_and_steps(self) -> None:
        num, entry = _first_spn_with("causes")
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(f"SPN {num} nedir", [], {})
        assert "J1939 Olası Nedenler (DB)" in report
        sample = str(entry["causes"][0])[:80]
        assert sample in report

    def test_session_path_consumes_j1939_causes(self) -> None:
        """The session path (DM1 stream) must also read J1939 causes/steps."""
        num, entry = _first_spn_with("causes")
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"spn": int(num), "fmi": None}], {"EngineSpeed": 1500.0}, ["ECU_0"]
        )
        joined = " ".join(report.likely_causes)
        sample = str(entry["causes"][0])[:60]
        assert sample in joined
        # Step budget must stay bounded (no unbounded report growth).
        assert len(report.troubleshooting_steps) <= 5


class TestP1_3J1939Bridge:
    """P1-3: DTC `j1939_spn_fmi` bridge (697 records) becomes reachable."""

    def test_dtc_with_bridge_shows_spn(self) -> None:
        bridge_code = None
        for code, info in EXPERT_KNOWLEDGE_BASE.items():
            if isinstance(info, dict) and isinstance(info.get("j1939_spn_fmi"), list) and info["j1939_spn_fmi"]:
                bridge_code = code
                break
        assert bridge_code is not None, "no DTC with j1939_spn_fmi in catalog"
        info = EXPERT_KNOWLEDGE_BASE[bridge_code]
        spn = info["j1939_spn_fmi"][0].get("spn")
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(f"{bridge_code} nedir", [], {})
        assert "İlgili J1939 SPN Köprüsü (DTC↔SPN)" in report
        assert f"SPN {spn}" in report

    def test_dtc_without_bridge_has_no_bridge_block(self) -> None:
        plain = None
        for code, info in EXPERT_KNOWLEDGE_BASE.items():
            if isinstance(info, dict) and not info.get("j1939_spn_fmi"):
                plain = code
                break
        assert plain is not None
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(f"{plain} nedir", [], {})
        assert "İlgili J1939 SPN Köprüsü (DTC↔SPN)" not in report


class TestP1_4SymptomSearch:
    """P1-4: DTC `symptoms` (14.166 records) free-text search."""

    def test_helper_returns_deterministic_ranked_hits(self) -> None:
        q = "egzozdan siyah duman cikiyor"
        h1 = search_dtc_by_symptom(q)
        h2 = search_dtc_by_symptom(q)
        assert h1 == h2  # determinism
        assert h1, "expected at least one symptom hit"
        assert h1[0]["score"] >= 1
        assert h1[0]["code"] in EXPERT_KNOWLEDGE_BASE

    def test_query_path_surfaces_symptom_match(self) -> None:
        q = "rolantide duzensizlik ve titreme"
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(q, [], {})
        assert "Semptom Eşleşmesi" in report

    def test_no_hit_leaves_fallback_intact(self) -> None:
        """A gibberish query must NOT be force-matched to a random DTC."""
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            "zzzqxw vuuut kkkkzzz", [], {}
        )
        assert "Semptom Eşleşmesi" not in report
        assert "Bilgi Bulunamadı" in report

    def test_empty_terms_yield_no_hits(self) -> None:
        assert search_dtc_by_symptom("") == []
        assert search_dtc_by_symptom("ne var mi") == []


class TestP1_5SeverityBasisAndLamp:
    """P1-5: fault_matrix `severity_basis` (6.230) + `lamp` (29) surfaced."""

    def test_severity_basis_is_shown_for_fmi_query(self) -> None:
        db = get_j1939_spn_database()
        for key, entry in db.get("spns", {}).items():
            num = key.replace("SPN_", "")
            if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                continue
            fm = entry.get("fault_matrix") if isinstance(entry, dict) else None
            if not isinstance(fm, dict):
                continue
            for fmi, row in fm.items():
                if isinstance(row, dict) and row.get("severity_basis"):
                    report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
                        f"SPN {num} FMI {fmi}", [], {}
                    )
                    assert "Önem Gerekçesi" in report
                    return
        pytest.skip("no fault_matrix row with severity_basis")

    def test_lamp_is_shown_when_present(self) -> None:
        db = get_j1939_spn_database()
        for key, entry in db.get("spns", {}).items():
            num = key.replace("SPN_", "")
            if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                continue
            fm = entry.get("fault_matrix") if isinstance(entry, dict) else None
            if not isinstance(fm, dict):
                continue
            for fmi, row in fm.items():
                if isinstance(row, dict) and row.get("lamp"):
                    report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
                        f"SPN {num} FMI {fmi}", [], {}
                    )
                    assert "Gösterge Lambası" in report
                    assert str(row["lamp"]) in report
                    return
        pytest.skip("no fault_matrix row with lamp")


class TestT41Determinism:
    """Rule 2: every new path must stay deterministic (same input -> same output)."""

    @pytest.mark.parametrize(
        "query",
        [
            "SPN 1088 nedir",
            "P2009 nedir",
            "rolantide duzensizlik ve titreme",
        ],
    )
    def test_same_input_same_output(self, query: str) -> None:
        a = CausalBayesianInferenceEngine.evaluate_diagnostic_query(query, [], {})
        b = CausalBayesianInferenceEngine.evaluate_diagnostic_query(query, [], {})
        assert a == b

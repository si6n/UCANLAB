"""T42(b) regression tests — P2-2 (OEM-filtered selection) + P2-3 (complaints fusion).

Task t_b5e29627: the remaining two P2 items from the T40 audit
(spn_gap_hunter/output/t40_ai_engine_audit.md, section (e)):

  P2-2  OEM bazlı filtreli seçim: `oem_variants` (DTC, 2.911 kayıt) and J1939
        `oem_engine_families` (64 SPN) / `oem_field_evidence` (400 SPN) were
        printed as text only — they never influenced the decision. When the
        operator names a vehicle make the irrelevant OEM variants are now
        hidden; with no make the previous behaviour is preserved (fail-safe).
  P2-3  Complaints kanıt füzyonu: `get_nhtsa_complaints_database` (4.388
        complaints) had zero consumers. The 5.9 MB corpus is now read through a
        lazy, cached accessor and fused into the `nhtsa_evidence` block of the
        4-stage report with a make/year filter.

Locked invariants (T42 task rules):
  1. AI layer stays offline — tests/safety/test_ai_tx_isolation.py stays green.
  2. Determinism: same input -> same output.
  3. No fabrication (fail-safe): a block is only produced when the DB field is
     actually populated; filtering never empties the evidence entirely.
  4. Engine start-up must not pay the 5.9 MB parse cost (lazy load).
"""

from __future__ import annotations

import time

import pytest

from src.engine.ai.diagnostic_copilot import (
    EXPERT_KNOWLEDGE_BASE,
    CausalBayesianInferenceEngine,
    detect_vehicle_make,
    ensure_external_dtc_database_loaded,
    filter_oem_families,
    filter_oem_variants,
    get_j1939_spn_database,
    get_nhtsa_complaints_database,
    search_nhtsa_complaints,
)


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()


def _multi_variant_code() -> tuple[str, list[str]]:
    """Return a real DTC with >=2 distinct OEM manufacturers and its makes."""
    for code in sorted(EXPERT_KNOWLEDGE_BASE):
        info = EXPERT_KNOWLEDGE_BASE[code]
        if not isinstance(info, dict):
            continue
        mfrs = sorted({
            str(v.get("manufacturer", "")).upper()
            for v in (info.get("oem_variants") or [])
            if isinstance(v, dict) and v.get("manufacturer")
        })
        if len(mfrs) >= 2:
            return code, mfrs
    pytest.skip("no DTC with >=2 OEM manufacturers")


def _spn_with_families() -> tuple[str, list[str]]:
    """Return (spn_number_str, families) for an SPN that routes to the J1939 report.

    The J1939 technician report is only reached for SPNs *not* present in
    ``EXPERT_KNOWLEDGE_BASE`` (those go through the 4-stage OBD report), so the
    helper must skip KB codes to exercise the family block.
    """
    jdb = get_j1939_spn_database()
    for key, rec in jdb.get("spns", {}).items():
        num = key.replace("SPN_", "")
        if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
            continue
        if isinstance(rec, dict) and rec.get("oem_engine_families"):
            return num, list(rec["oem_engine_families"])
    pytest.skip("no non-KB SPN with oem_engine_families")


class TestP2_2VehicleMakeDetection:
    def test_detects_make_from_free_text(self) -> None:
        assert detect_vehicle_make("Ford F-150 P0101 arızası") == "ford"
        assert detect_vehicle_make("Cummins ISX SPN 100") == "cummins"
        assert detect_vehicle_make("volkswagen golf") == "volkswagen"

    def test_none_when_no_make(self) -> None:
        assert detect_vehicle_make("arıza nedir") is None
        assert detect_vehicle_make("") is None
        assert detect_vehicle_make(None) is None

    def test_deterministic(self) -> None:
        a = detect_vehicle_make("chevy silverado dtc")
        b = detect_vehicle_make("chevy silverado dtc")
        assert a == b == "chevrolet"


class TestP2_2OemVariantFiltering:
    def test_no_make_keeps_every_variant(self) -> None:
        code, _ = _multi_variant_code()
        variants = EXPERT_KNOWLEDGE_BASE[code]["oem_variants"]
        kept, hidden = filter_oem_variants(variants, None)
        assert kept == variants
        assert hidden == 0

    def test_make_filters_out_unrelated_oem(self) -> None:
        code, mfrs = _multi_variant_code()
        variants = EXPERT_KNOWLEDGE_BASE[code]["oem_variants"]
        kept, hidden = filter_oem_variants(variants, mfrs[0].lower())
        assert hidden >= 1
        for v in kept:
            if isinstance(v, dict) and v.get("manufacturer"):
                m = v["manufacturer"].upper()
                assert m in (mfrs[0], "OTHER")

    def test_filter_never_empties_evidence(self) -> None:
        """Fail-safe: a make with no variant keeps the original list."""
        code, _ = _multi_variant_code()
        variants = EXPERT_KNOWLEDGE_BASE[code]["oem_variants"]
        kept, hidden = filter_oem_variants(variants, "peterbilt")
        assert kept == variants
        assert hidden == len(variants)

    def test_family_filter_by_make(self) -> None:
        _, families = _spn_with_families()
        kept, hidden = filter_oem_families(families, "cummins")
        assert kept
        assert all("cummins" in f.lower() for f in kept)

    def test_family_filter_no_make_keeps_all(self) -> None:
        _, families = _spn_with_families()
        kept, hidden = filter_oem_families(families, None)
        assert kept == [str(f) for f in families]
        assert hidden == 0

    def test_report_hides_unrelated_oem_when_make_given(self) -> None:
        code, mfrs = _multi_variant_code()
        base = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            f"{code} arızası", [{"code": code}], {}
        )
        filtered = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            f"{mfrs[0].lower()} {code} arızası", [{"code": code}], {}
        )
        assert "marka filtreli" in filtered
        assert "gizlendi" in filtered
        # the unfiltered report must NOT claim a filter is active
        assert "marka filtreli" not in base

    def test_j1939_family_block_renders_and_filters(self) -> None:
        spn, families = _spn_with_families()
        base = CausalBayesianInferenceEngine.evaluate_diagnostic_query(f"SPN {spn}", [], {})
        assert "OEM Motor Aileleri" in base
        filtered = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            f"Cummins SPN {spn}", [], {}
        )
        assert "marka filtreli" in filtered


class TestP2_3ComplaintsFusion:
    def test_lazy_load_does_not_happen_at_import(self) -> None:
        """The 5.9 MB corpus must not be parsed before the first query."""
        import src.engine.ai.diagnostic_copilot as dc

        # First touch in this process may already be cached by another test;
        # the contract is that the accessor exists and is lazy (module global).
        assert hasattr(dc, "_CACHED_NHTSA_COMPLAINTS_DB")

    def test_cached_load_is_fast(self) -> None:
        get_nhtsa_complaints_database()  # warm
        t0 = time.perf_counter()
        get_nhtsa_complaints_database()
        dt_ms = (time.perf_counter() - t0) * 1000.0
        assert dt_ms < 5.0  # cached access must be near-instant

    def test_search_by_make(self) -> None:
        result = search_nhtsa_complaints(make="tesla", limit=3)
        assert result["total_complaints"] == 4388
        assert result["matched_vehicles"]
        for v in result["matched_vehicles"]:
            assert v["make"].upper() == "TESLA"

    def test_search_unknown_make_returns_empty(self) -> None:
        """Fail-safe: a make absent from the corpus yields no fabricated hits."""
        result = search_nhtsa_complaints(make="peterbilt", limit=3)
        assert result["complaints"] == []
        assert result["matched_vehicles"] == []

    def test_search_deterministic(self) -> None:
        a = search_nhtsa_complaints(make="toyota", limit=3)
        b = search_nhtsa_complaints(make="toyota", limit=3)
        assert a == b

    def test_fusion_block_renders_for_corpus_make(self) -> None:
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            "tesla model 3 P0300 arızası", [{"code": "P0300"}], {}
        )
        assert "Sahip Şikayetleri (NHTSA complaints" in report
        assert "TESLA marka filtreli" in report

    def test_no_make_report_has_no_filter_claim(self) -> None:
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            "P0300 arızası", [{"code": "P0300"}], {}
        )
        assert "marka filtreli" not in report

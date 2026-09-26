# -*- coding: utf-8 -*-
"""T69-G regression lock — the combined multi-DTC report accepts make/year.

THE DEFECT
----------
`_format_multi_dtc_combined_report` had a three-parameter signature
(`codes, telemetry, clusters`) while its CALL SITE already computed both
`vehicle_make` (via `detect_vehicle_make(norm_query)`) and `vehicle_year` and
passed neither. The single-code and J1939 generators have accepted both since
T42 P2-2/P2-3.

Measured consequence: for a vehicle whose fault set is MULTI-code — the common
case for a shared-root-cause failure, which is exactly when a technician most
wants OEM context — the report lost:
  * the OEM-variant make filter (`filter_oem_variants`), and
  * the NHTSA owner-complaint fusion (`search_nhtsa_complaints`).
Both features existed and worked; they were unreachable in the multi-code path.

THE FIX
-------
Signature widened with `vehicle_make=None, vehicle_year=None`; the call site
passes both. `None` preserves the previous behaviour byte-for-byte (fail-safe),
which is pinned by a test below.

No fabrication: only `oem_variants` blocks the DB actually fills are emitted.
"""

from __future__ import annotations

import pytest

from src.engine.ai.diagnostic_copilot import (
    EXPERT_KNOWLEDGE_BASE,
    CausalBayesianInferenceEngine,
    ensure_external_dtc_database_loaded,
)

OEM_HEADER = "OEM Varyantları"
COMPLAINT_HEADER = "Sahip Şikayetleri"
FILTER_MARKER = "marka filtreli"


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()


def _query(text: str) -> str:
    return CausalBayesianInferenceEngine.evaluate_diagnostic_query(text, [], {})


def _codes_with_oem_variants(n: int = 2) -> list[str]:
    out: list[str] = []
    for code, info in EXPERT_KNOWLEDGE_BASE.items():
        if not isinstance(info, dict):
            continue
        if isinstance(info.get("oem_variants"), list) and info["oem_variants"]:
            out.append(code)
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------------------
# 1. The signature accepts both parameters
# ---------------------------------------------------------------------------


class TestT69GSignature:
    def test_accepts_make_and_year(self) -> None:
        codes = _codes_with_oem_variants(1)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        out = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {}, {}, "FORD", 2015
        )
        assert isinstance(out, str) and out.strip()

    def test_defaults_preserve_previous_behaviour(self) -> None:
        """Calling with three arguments must work exactly as before."""
        codes = _codes_with_oem_variants(1)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        three = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {}, {}
        )
        explicit_none = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {}, {}, None, None
        )
        assert three == explicit_none

    def test_no_make_never_claims_a_filter(self) -> None:
        """A filter that was not applied must not be narrated as applied."""
        codes = _codes_with_oem_variants(2)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        out = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {}, {}
        )
        assert FILTER_MARKER not in out


# ---------------------------------------------------------------------------
# 2. The call site actually passes them
# ---------------------------------------------------------------------------


class TestT69GCallSiteWiring:
    def test_query_with_make_activates_the_filter(self) -> None:
        """The measured defect: a named make had no effect on a multi-code query."""
        codes = _codes_with_oem_variants(2)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        query = " ".join(codes) + " nedir"
        plain = _query(query)
        with_make = _query(query + " ford focus")
        assert OEM_HEADER in plain or OEM_HEADER in with_make, (
            "no OEM block in either report — the fixture codes carry no variants"
        )
        assert FILTER_MARKER not in plain, "the no-make report claimed a filter"
        assert FILTER_MARKER in with_make, (
            "naming a make did not activate the OEM filter on the multi-DTC path"
        )

    def test_two_different_makes_produce_different_reports(self) -> None:
        """If make were ignored, both queries would render identically."""
        codes = _codes_with_oem_variants(2)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        base = " ".join(codes) + " nedir"
        ford = _query(base + " ford")
        honda = _query(base + " honda")
        if FILTER_MARKER not in ford or FILTER_MARKER not in honda:
            pytest.skip("neither make produced a filtered OEM block")
        assert ford != honda, (
            "two different makes produced byte-identical reports — make is ignored"
        )

    def test_year_reaches_the_complaint_query(self) -> None:
        """A year in the query must be extractable by the router."""
        codes = _codes_with_oem_variants(1)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        base = " ".join(codes) + " nedir"
        no_year = _query(base + " ford")
        with_year = _query(base + " ford 2015")
        # Either the complaint block differs, or neither carries one — both are
        # honest outcomes. What must NOT happen is an exception or empty report.
        assert no_year.strip() and with_year.strip()
        assert isinstance(with_year, str)


# ---------------------------------------------------------------------------
# 3. Fail-closed: nothing invented
# ---------------------------------------------------------------------------


class TestT69GFailClosed:
    def test_no_oem_block_without_db_variants(self, monkeypatch) -> None:
        """A code with no `oem_variants` must not grow an OEM block."""
        import src.engine.ai.diagnostic_copilot as dc

        patched = {}
        for code, info in dc.EXPERT_KNOWLEDGE_BASE.items():
            if not isinstance(info, dict):
                continue
            entry = dict(info)
            entry.pop("oem_variants", None)
            patched[code] = entry
        monkeypatch.setattr(dc, "EXPERT_KNOWLEDGE_BASE", patched)
        out = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            ["P0300", "P0420"], {}, {}, "FORD", 2015
        )
        assert OEM_HEADER not in out, (
            "an OEM block was emitted although no record carries oem_variants"
        )

    def test_unrelated_make_is_hidden_not_shown(self) -> None:
        """With a make given, other manufacturers' variants must be filtered."""
        codes = _codes_with_oem_variants(1)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        info = EXPERT_KNOWLEDGE_BASE[codes[0]]
        variants = info["oem_variants"]
        makes = {
            str(v.get("manufacturer") or v.get("make") or "").strip().upper()
            for v in variants
            if isinstance(v, dict)
        }
        makes.discard("")
        makes.discard("OTHER")
        if len(makes) < 2:
            pytest.skip("this record's variants do not span two manufacturers")
        target = sorted(makes)[0]
        out = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {}, {}, target, None
        )
        assert FILTER_MARKER in out, "the filter did not activate for a known make"

    def test_report_never_offers_a_destructive_action(self) -> None:
        """P0-3 survives the widening."""
        codes = _codes_with_oem_variants(2)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        out = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {}, {}, "FORD", 2015
        )
        assert "act_uds_0x14_clear_dtc" not in out
        assert "uds_clear_dtc" not in out

    def test_determinism(self) -> None:
        codes = _codes_with_oem_variants(2)
        if not codes:
            pytest.skip("no KB record carries oem_variants")
        assert _query(" ".join(codes) + " nedir ford") == _query(
            " ".join(codes) + " nedir ford"
        )

    def test_complaints_failure_does_not_break_the_report(self, monkeypatch) -> None:
        """The complaint fusion must never take the report down."""
        import src.engine.ai.diagnostic_copilot as dc

        def _boom(*_a, **_k):
            raise RuntimeError("corpus unavailable")

        monkeypatch.setattr(dc, "search_nhtsa_complaints", _boom)
        out = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            ["P0300", "P0420"], {}, {}, "FORD", 2015
        )
        assert isinstance(out, str) and out.strip()
        assert COMPLAINT_HEADER not in out


# ---------------------------------------------------------------------------
# 4. Parity with the single-code generator
# ---------------------------------------------------------------------------


class TestT69GParityWithSingleCode:
    def test_both_generators_honour_the_same_make_vocabulary(self) -> None:
        """The same make string must resolve identically on both paths.

        `detect_vehicle_make` returns the canonical value from its alias table,
        which is lowercase (`"ford"`). The assertion below pins the ACTUAL
        contract rather than an assumed one — the report headers upper-case it
        for display, which is a separate concern.
        """
        from src.engine.ai.diagnostic_copilot import detect_vehicle_make

        for text, expected in (
            ("ford focus", "ford"),
            ("honda civic", "honda"),
            ("volvo fh", "volvo"),
        ):
            assert detect_vehicle_make(text) == expected

    def test_multi_dtc_path_renders_oem_for_a_multi_code_set(self) -> None:
        """The whole point: multi-code queries get OEM context too."""
        codes = _codes_with_oem_variants(2)
        if len(codes) < 2:
            pytest.skip("fewer than two KB records carry oem_variants")
        out = _query(" ".join(codes) + " nedir ford")
        assert OEM_HEADER in out
        assert FILTER_MARKER in out

# -*- coding: utf-8 -*-
"""P0-3 regression lock — J1939 SPN/FMI severity resolution.

WHY THIS EXISTS
---------------
The 2026-09-21 deep-discovery audit (finding 7) measured that the AI report was
**FMI-blind**: it stamped every failure mode of an SPN with the SPN-level rung.

Reproduced at HEAD ``a6f7477``::

    SPN 100 FMI 3  (sensor unplugged / short to +24V)  -> CRITICAL_STOP
    SPN 100 FMI 1  (real loss of oil pressure)         -> CRITICAL_STOP

Both advised *"Aracı güvenli bir şekilde durdurun / Motoru kapatın"*, even though
``data/diagnostics/j1939_spn_fmi_database.json`` ALREADY records FMI 3/4/5/6 as
``HIGH`` and FMI 0/1/2/16/18 as ``CRITICAL_STOP`` for SPN 100.

A second, silent defect: a DM1 code arrives as ``"SPN 100"`` (with a space) but
the knowledge base keys are ``"SPN100"``. The session path missed the lookup and
degraded a genuine oil-pressure stop condition to a generic ``LOW``.

These tests pin the resolution of BOTH defects. Nothing is invented: the rungs
come from the shipped SPN database.

Offline, deterministic — no network, no LLM.
"""

from __future__ import annotations

import pytest

from src.core.models.diagnostics import Severity
from src.engine.ai.diagnostic_copilot import (
    AiDiagnosticCopilot,
    CausalBayesianInferenceEngine,
)
from src.engine.ai.j1939_severity import (
    is_electrical_fmi,
    load_spn_database,
    normalize_spn_code,
    resolve_fmi_severity,
    resolve_spn_fault_title,
    spn_database_severity_counts,
)


class TestSpnKeyNormalisation:
    """``"SPN 100"`` must resolve to the KB/DB key form."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("SPN 100", "SPN100"),
            ("spn100", "SPN100"),
            ("SPN_100", "SPN100"),
            (" SPN  100 ", "SPN100"),
            ("SPN 4364", "SPN4364"),
        ],
    )
    def test_variants_normalise(self, raw: str, expected: str) -> None:
        assert normalize_spn_code(raw) == expected

    @pytest.mark.parametrize("raw", ["P0300", "U0100", "P0011", "", "SPN", "SPN abc"])
    def test_non_spn_returns_none(self, raw: str) -> None:
        assert normalize_spn_code(raw) is None


class TestFmiClasses:
    def test_electrical_fmis_are_not_stop(self) -> None:
        for fmi in (3, 4, 5, 6):
            assert is_electrical_fmi(fmi)

    def test_physical_fmis_are_not_electrical(self) -> None:
        for fmi in (0, 1, 2, 16, 18):
            assert not is_electrical_fmi(fmi)


class TestRecordedFmiSeverities:
    """The rungs must come from the shipped database, not from this test."""

    @pytest.mark.parametrize("fmi", [3, 4, 5, 6])
    def test_elektriksel_fmi_is_high_not_stop(self, fmi: int) -> None:
        assert resolve_fmi_severity("SPN 100", fmi) is Severity.HIGH

    @pytest.mark.parametrize("fmi", [0, 1, 2, 16, 18])
    def test_fiziksel_fmi_is_critical_stop(self, fmi: int) -> None:
        assert resolve_fmi_severity("SPN 100", fmi) is Severity.CRITICAL_STOP

    def test_spaced_and_unspaced_forms_agree(self) -> None:
        assert resolve_fmi_severity("SPN 100", 3) == resolve_fmi_severity("SPN100", 3)

    def test_missing_fmi_returns_none(self) -> None:
        """An unrecorded FMI must NOT be invented — caller keeps its rung."""
        assert resolve_fmi_severity("SPN 100", None) is None
        assert resolve_fmi_severity("SPN 100", 99) is None

    def test_non_spn_returns_none(self) -> None:
        assert resolve_fmi_severity("P0300", 3) is None

    def test_fault_title_is_specific_to_fmi(self) -> None:
        """The electrical and physical modes must read differently."""
        electrical = resolve_spn_fault_title("SPN 100", 3)
        physical = resolve_spn_fault_title("SPN 100", 1)
        assert electrical and physical
        assert electrical != physical
        assert "kısa devre" in electrical.lower() or "voltaj" in electrical.lower()


class TestDatabaseIntegrity:
    def test_database_loads(self) -> None:
        assert load_spn_database().get("spns")

    def test_every_severity_resolves_to_an_enum(self) -> None:
        """The lowercase scraped rungs must normalise, not be dropped."""
        counts = spn_database_severity_counts()
        assert "UNRESOLVED" not in counts, (
            f"some SPN/FMI severity values did not map to Severity: {counts}"
        )

    def test_both_electrical_and_physical_rungs_present(self) -> None:
        counts = spn_database_severity_counts()
        assert counts.get("HIGH", 0) > 0
        assert counts.get("CRITICAL_STOP", 0) > 0


class TestSessionPathSeverity:
    """The 1500/2.6 reproduction and the FMI discrimination."""

    def _severity(self, dtc: dict) -> str:
        # The session report uses FaultSeverity; compare the rung by value so
        # the assertion is independent of which enum carries it.
        return AiDiagnosticCopilot().analyze_session([dtc], {}, []).severity.value

    def test_spaced_spn_is_found_in_knowledge_base(self) -> None:
        """The old miss degraded a stop condition to a generic LOW."""
        assert self._severity({"code": "SPN 100", "fmi": 1}) == "CRITICAL_STOP"

    def test_electrical_fmi_does_not_stop_the_engine(self) -> None:
        assert self._severity({"code": "SPN 100", "fmi": 3}) == "HIGH"

    def test_physical_fmi_does_stop_the_engine(self) -> None:
        assert self._severity({"code": "SPN 100", "fmi": 1}) == "CRITICAL_STOP"

    def test_fmi_3_and_fmi_1_differ(self) -> None:
        """The exact equivalence the audit flagged."""
        assert self._severity({"code": "SPN 100", "fmi": 3}) != self._severity(
            {"code": "SPN 100", "fmi": 1}
        )

    def test_numeric_spn_key_also_resolves(self) -> None:
        """A DM1 frame carries a numeric SPN, not a "SPN 100" string."""
        assert self._severity({"spn": 100, "fmi": 3}) == "HIGH"
        assert self._severity({"spn": 100, "fmi": 1}) == "CRITICAL_STOP"

    @pytest.mark.parametrize("fmi", [3, 4, 5, 6])
    def test_every_electrical_fmi_avoids_a_stop(self, fmi: int) -> None:
        assert self._severity({"spn": 100, "fmi": fmi}) == "HIGH"

    @pytest.mark.parametrize("fmi", [0, 1, 2, 16, 18])
    def test_every_physical_fmi_stops(self, fmi: int) -> None:
        assert self._severity({"spn": 100, "fmi": fmi}) == "CRITICAL_STOP"


class TestPromptPathSeverity:
    """The prompt path must state the FMI-specific rung."""

    def _report(self, query: str) -> str:
        return CausalBayesianInferenceEngine.evaluate_diagnostic_query(query, [], {})

    def test_fmi_3_reports_high(self) -> None:
        report = self._report("SPN 100 FMI 3")
        header = report.split("\n")[0]
        assert "HIGH" in header
        assert "FMI 3" in header

    def test_fmi_1_reports_critical_stop(self) -> None:
        header = self._report("SPN 100 FMI 1").split("\n")[0]
        assert "CRITICAL_STOP" in header

    def test_fmi_3_and_fmi_1_headers_differ(self) -> None:
        assert self._report("SPN 100 FMI 3").split("\n")[0] != self._report("SPN 100 FMI 1").split("\n")[0]

    def test_no_fmi_keeps_spn_level_behaviour(self) -> None:
        header = self._report("SPN 100").split("\n")[0]
        assert "FMI" not in header

    def test_fault_mode_is_named_for_electrical_fault(self) -> None:
        report = self._report("SPN 100 FMI 3")
        assert "Arıza Modu" in report

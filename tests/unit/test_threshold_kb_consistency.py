# -*- coding: utf-8 -*-
"""P0-2 regression lock — threshold database vs knowledge-base consistency.

WHY THIS EXISTS
---------------
The 2026-09-21 deep-discovery audit measured (finding 4) that
``data/diagnostics/telemetry_thresholds.json`` capped ``TurboBoost`` at
**2.5 bar** while the knowledge base's OWN ``SPN102`` scenario states:

    "Tam Yukte Nominal Boost: 2.2 - 3.2 Bar (Abs) | Maksimum Guvenlik Limiti: 3.6 Bar."

So a healthy Euro-VI engine at full load (2.6 bar) produced a false-positive
``P0234`` Overboost diagnosis — reproduced live through both the session path
(``analyze_session``) and the prompt path (``analyze_live_telemetry``).

A second, subtler defect: the prompt router selects a report from the
OPERATOR'S WORDS ("turbo basincı normal mi?"), never from the measurement, so
the healthy reading was shown an unexplained overboost work-order.

These tests pin both halves: the threshold file must agree with the KB text, and
the report must contextualise the captured value instead of asserting a fault
the telemetry does not support.

Offline, deterministic — no network, no LLM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.engine.ai.anomaly_detector import load_thresholds
from src.engine.ai.diagnostic_copilot import (
    AiDiagnosticCopilot,
    CausalBayesianInferenceEngine,
    _coolant_limits_c,
    _turbo_boost_limit_bar,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
THRESHOLDS = REPO_ROOT / "data" / "diagnostics" / "telemetry_thresholds.json"


@pytest.fixture(scope="module")
def raw_thresholds() -> dict:
    return json.loads(THRESHOLDS.read_text(encoding="utf-8"))


class TestThresholdFileMatchesKnowledgeBase:
    """The threshold DB must not contradict the KB text it claims to mirror."""

    def test_turbo_limit_matches_kb_stated_limit(self, raw_thresholds) -> None:
        """KB SPN102 states a 3.6 bar safety limit — the trigger must agree."""
        bands = raw_thresholds["signals"]["TurboBoost"]
        limit = bands.get("critical_max") or bands["ranges"][0].get("max")
        assert limit == 3.6, (
            f"TurboBoost limit is {limit}; the KB's SPN102 measurement states "
            "'Maksimum Guvenlik Limiti: 3.6 Bar'. The old 2.5 value produced a "
            "false-positive overboost on a healthy engine (audit finding 4)."
        )

    def test_turbo_nominal_band_matches_kb(self, raw_thresholds) -> None:
        bands = raw_thresholds["signals"]["TurboBoost"]
        assert bands.get("nominal_min") == 2.2
        assert bands.get("nominal_max") == 3.2

    def test_coolant_bands_match_kb(self, raw_thresholds) -> None:
        """KB SPN110: warning >103C, red lamp >108C."""
        bands = raw_thresholds["signals"]["EngineCoolantTemp"]
        assert bands.get("warning_max") == 103.0
        assert bands.get("critical_max") == 108.0

    def test_healthy_full_load_boost_is_inside_nominal_band(self, raw_thresholds) -> None:
        """The exact value that triggered the false positive must be nominal."""
        bands = raw_thresholds["signals"]["TurboBoost"]
        assert bands["nominal_min"] <= 2.6 <= bands["nominal_max"]

    def test_every_signal_keeps_a_source_ref(self, raw_thresholds) -> None:
        """No fabricated thresholds: each entry must cite its KB origin."""
        for name, entry in raw_thresholds["signals"].items():
            assert str(entry.get("source_ref", "")).strip(), f"{name} has no source_ref"


class TestThresholdDatabaseLoads:
    """The extended schema (bands) must still pass the fail-closed validator."""

    def test_loads_without_error(self) -> None:
        thresholds = load_thresholds()
        assert "TurboBoost" in thresholds

    def test_bands_survive_validation(self) -> None:
        thresholds = load_thresholds()
        assert thresholds["TurboBoost"].get("critical_max") == 3.6
        assert thresholds["EngineCoolantTemp"].get("warning_max") == 103.0

    def test_unknown_top_level_field_still_rejected(self, tmp_path) -> None:
        """The fail-closed guard must NOT have been weakened by the extension."""
        from src.engine.ai.anomaly_detector import ThresholdDatabaseError

        payload = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
        payload["bogus_field"] = 1
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ThresholdDatabaseError):
            load_thresholds(bad)

    def test_unknown_signal_field_still_rejected(self, tmp_path) -> None:
        from src.engine.ai.anomaly_detector import ThresholdDatabaseError

        payload = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
        payload["signals"]["TurboBoost"]["whoops"] = 9
        bad = tmp_path / "bad2.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ThresholdDatabaseError):
            load_thresholds(bad)


class TestAccessorsReadTheDatabase:
    """The scenario triggers must read the DB, not carry magic numbers."""

    def test_turbo_limit_accessor_matches_db(self) -> None:
        assert _turbo_boost_limit_bar() == 3.6

    def test_coolant_accessor_matches_db(self) -> None:
        assert _coolant_limits_c() == (103.0, 108.0)


class TestFalsePositiveIsGone:
    """The exact reproductions recorded in the audit."""

    def test_healthy_full_load_does_not_raise_overboost(self) -> None:
        """1500 rpm / 2.6 bar / 92C must NOT be diagnosed as overboost."""
        report = AiDiagnosticCopilot().analyze_session(
            [], {"EngineSpeed": 1500.0, "BoostPressure": 2.6, "CoolantTemp": 92.0}, []
        )
        assert "Aşırı Doldurma & Turboşarj" not in report.affected_subsystems, (
            "a nominal 2.6 bar reading was diagnosed as overboost (audit finding 4)"
        )

    def test_genuine_overboost_is_still_detected(self) -> None:
        """3.8 bar is above the 3.6 limit — it must still be flagged."""
        report = AiDiagnosticCopilot().analyze_session(
            [], {"EngineSpeed": 1500.0, "BoostPressure": 3.8, "CoolantTemp": 92.0}, []
        )
        assert "Aşırı Doldurma & Turboşarj" in report.affected_subsystems


class TestReportContextualisesMeasurement:
    """The prompt path must state whether telemetry supports the diagnosis."""

    def test_nominal_boost_is_reported_as_nominal(self) -> None:
        out = CausalBayesianInferenceEngine._measured_value_consistency(
            "P0234", {"BoostPressure": 2.6}
        )
        assert "nominal bantta" in out
        assert "DESTEKLEMİYOR" in out

    def test_over_limit_boost_is_reported_as_supporting(self) -> None:
        out = CausalBayesianInferenceEngine._measured_value_consistency(
            "P0234", {"BoostPressure": 3.8}
        )
        assert "limit üstü" in out
        assert "DESTEKLİYOR" in out

    def test_no_measurement_emits_no_block(self) -> None:
        """F-02: an absent channel must never be narrated as a reading."""
        assert CausalBayesianInferenceEngine._measured_value_consistency("P0234", {}) == ""

    def test_prompt_path_shows_consistency_note(self) -> None:
        out = AiDiagnosticCopilot().analyze_live_telemetry(
            1500.0, 2.6, 92.0, [], "turbo basıncı normal mi?"
        )
        assert "Ölçüm Tutarlılığı" in out
        match = re.search(r"2\.60 bar.*?nominal bantta", out, re.S)
        assert match, "the healthy reading was not contextualised in the prompt-path report"

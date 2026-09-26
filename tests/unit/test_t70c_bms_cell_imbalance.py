"""T70-C: BMS cell-imbalance cross-check in the DTC diagnosis path.

MEASURED DEFECT (T70-C): the cell-voltage spread was computed in exactly ONE
place — the CAN-frame forensics branch (``analyze_can_frame``, 0x1808E5F4) — and
the DTC diagnosis path never touched it. An operator asking "P0A80 nedir" with
both cell-extreme channels live received the KB text "Arıza / Değişim Eşiği:
>150 mV" as PROSE, never compared against the measurement in hand.

The thresholds are DERIVED FROM THE KNOWLEDGE BASE. ``test_thresholds_trace_to_
knowledge_base`` re-reads P0A80's own ``measurement`` string and asserts every
number still appears in it, so the table cannot silently drift from its source
(AGENTS.md §2.3 — no invented numbers).
"""

from __future__ import annotations

import pytest

from src.engine.ai.diagnostic_copilot import (
    _BMS_CELL_MAX_KEY,
    _BMS_CELL_MIN_KEY,
    _BMS_DELTA_THRESHOLDS,
    EXPERT_KNOWLEDGE_BASE,
    CausalBayesianInferenceEngine,
    bms_cell_imbalance_block,
    ensure_external_dtc_database_loaded,
)

MARKER = "Hücre Dengesi"


@pytest.fixture(scope="module", autouse=True)
def _load() -> None:
    ensure_external_dtc_database_loaded()


def _telemetry(lo: float, hi: float) -> dict[str, float]:
    return {_BMS_CELL_MIN_KEY: lo, _BMS_CELL_MAX_KEY: hi}


class TestThresholdProvenance:
    """Every number must be traceable to the KB record it came from."""

    def test_thresholds_trace_to_knowledge_base(self) -> None:
        info = EXPERT_KNOWLEDGE_BASE.get("P0A80")
        assert isinstance(info, dict), "fixture assumption: P0A80 in the KB"
        measurement = str(info.get("measurement", ""))
        assert measurement, "fixture assumption: P0A80 carries a measurement"
        nominal, rest, load, _src = _BMS_DELTA_THRESHOLDS["P0A80"]
        for value in (nominal, rest, load):
            assert f"{value:g}" in measurement, (
                f"threshold {value:g} mV is not present in the KB measurement text: "
                f"{measurement!r}"
            )

    def test_every_thresholded_code_exists_in_knowledge_base(self) -> None:
        for code in _BMS_DELTA_THRESHOLDS:
            assert code in EXPERT_KNOWLEDGE_BASE, f"{code} not in the KB"

    def test_source_note_names_the_kb_field(self) -> None:
        _n, _r, _l, source = _BMS_DELTA_THRESHOLDS["P0A80"]
        assert "KB" in source


class TestFailSafe:
    """No block unless a real, coherent measurement exists."""

    def test_code_without_threshold_emits_nothing(self) -> None:
        assert bms_cell_imbalance_block("P0300", _telemetry(3.7, 3.9)) == ""

    def test_missing_min_channel_emits_nothing(self) -> None:
        assert bms_cell_imbalance_block("P0A80", {_BMS_CELL_MAX_KEY: 3.9}) == ""

    def test_missing_max_channel_emits_nothing(self) -> None:
        assert bms_cell_imbalance_block("P0A80", {_BMS_CELL_MIN_KEY: 3.7}) == ""

    def test_no_telemetry_emits_nothing(self) -> None:
        assert bms_cell_imbalance_block("P0A80", {}) == ""

    def test_non_numeric_channels_emit_nothing(self) -> None:
        assert bms_cell_imbalance_block(
            "P0A80", {_BMS_CELL_MIN_KEY: "low", _BMS_CELL_MAX_KEY: "high"}
        ) == ""

    def test_boolean_channel_is_not_a_measurement(self) -> None:
        assert bms_cell_imbalance_block(
            "P0A80", {_BMS_CELL_MIN_KEY: True, _BMS_CELL_MAX_KEY: 3.9}
        ) == ""


class TestVerdicts:
    """Each verdict band must be stated honestly, with its threshold."""

    def test_nominal_spread_does_not_support_the_diagnosis(self) -> None:
        block = bms_cell_imbalance_block("P0A80", _telemetry(3.700, 3.710))
        assert MARKER in block
        assert "10.0 mV" in block
        assert "DESTEKLEMİYOR" in block

    def test_rest_threshold_exceeded_is_partial_support(self) -> None:
        block = bms_cell_imbalance_block("P0A80", _telemetry(3.700, 3.760))
        assert "60.0 mV" in block
        assert "KISMEN" in block
        assert "50" in block, "the rest threshold must be shown"

    def test_load_threshold_exceeded_supports_the_diagnosis(self) -> None:
        block = bms_cell_imbalance_block("P0A80", _telemetry(3.700, 3.900))
        assert "200.0 mV" in block
        assert "DESTEKLİYOR" in block
        assert "150" in block, "the load threshold must be shown"

    def test_boundary_rest_threshold_is_not_exceeded(self) -> None:
        """Exactly 50 mV is AT the threshold, not above it."""
        block = bms_cell_imbalance_block("P0A80", _telemetry(3.700, 3.750))
        assert "KISMEN" not in block

    def test_boundary_nominal_is_inside(self) -> None:
        """Exactly 30 mV is the nominal limit — inside, not outside."""
        block = bms_cell_imbalance_block("P0A80", _telemetry(3.700, 3.730))
        assert "DESTEKLEMİYOR" in block


class TestIncoherentInput:
    """min > max must never be silently turned into a spread."""

    def test_reversed_channels_are_reported_as_incoherent(self) -> None:
        block = bms_cell_imbalance_block("P0A80", _telemetry(3.900, 3.700))
        assert MARKER in block
        assert "tutarsız" in block
        # No spread may be asserted from incoherent input.
        assert "DESTEKLİYOR" not in block
        assert "KISMEN" not in block

    def test_equal_channels_yield_zero_spread(self) -> None:
        block = bms_cell_imbalance_block("P0A80", _telemetry(3.700, 3.700))
        assert "0.0 mV" in block
        assert "DESTEKLEMİYOR" in block


class TestReportWiring:
    """The DTC report path must carry the cross-check."""

    def test_4stage_report_contains_cross_check(self) -> None:
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            "P0A80", _telemetry(3.700, 3.900)
        )
        assert MARKER in report
        assert "200.0 mV" in report

    def test_4stage_report_silent_without_cell_channels(self) -> None:
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            "P0A80", {"EngineSpeed": 1800.0}
        )
        assert MARKER not in report

    def test_unrelated_code_report_has_no_cross_check(self) -> None:
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            "P0300", _telemetry(3.700, 3.900)
        )
        assert MARKER not in report

    def test_cross_check_never_names_a_part_to_replace(self) -> None:
        """It compares a measurement; it does not prescribe a repair."""
        block = bms_cell_imbalance_block("P0A80", _telemetry(3.700, 3.900)).lower()
        for forbidden in ("değiştirin", "paketi değiştir", "arızalı hücre"):
            assert forbidden not in block

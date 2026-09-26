"""T70-B: physical-envelope sweep over supplied measurements.

MEASURED DEFECT (T70-B): plausibility checking existed in two places and
neither saw the copilot's measurements.
  * ``procedure_validator.PHYSICAL_BOUNDS`` runs only while LOADING a
    procedure JSON — it validates authored data, never live telemetry.
  * ``anomaly_detector.detect_anomalies`` scans a ``VehicleSession``'s recorded
    SAMPLE STREAM; the reports take a ``telemetry`` SNAPSHOT dict and never
    called it.
A physically impossible reading therefore passed through every report
unflagged. T70-B adds ``telemetry_plausibility_block`` and wires it into all
three deep-dive report paths.

These tests pin behaviour, not wording:
  * inside-envelope values emit NOTHING (no noise on a normal session)
  * outside-envelope values ARE flagged, with the limit and unit
  * unmeasured / private / non-numeric keys are never judged
  * the derived tables stay consistent with ``_THRESHOLD_SIGNAL_FOR_CODE``
  * a broken threshold DB degrades to "no block", never to an exception
"""

from __future__ import annotations

import pytest

from src.engine.ai.anomaly_detector import load_thresholds
from src.engine.ai.diagnostic_copilot import (
    _TELEMETRY_SIGNAL_ALIASES,
    _TELEMETRY_TO_THRESHOLD_KEY,
    CausalBayesianInferenceEngine,
    analyze_active_dtc_clusters,
    ensure_external_dtc_database_loaded,
    telemetry_plausibility_block,
)

MARKER = "Fiziksel Sınır Denetimi"


@pytest.fixture(scope="module")
def thresholds() -> dict:
    return load_thresholds()


class TestTableConsistency:
    """The derived tables must stay in step with the existing source of truth."""

    def test_every_mapped_key_exists_in_threshold_db(self, thresholds: dict) -> None:
        for signal, db_key in _TELEMETRY_TO_THRESHOLD_KEY.items():
            assert db_key in thresholds, (
                f"{signal} maps to '{db_key}', which is not in telemetry_thresholds.json"
            )

    def test_table_matches_threshold_signal_for_code(self) -> None:
        """Every pair in the derived table must appear in the per-code table."""
        pairs_in_code_table = {
            (signal, db_key)
            for signal, db_key, _label, _unit in (
                CausalBayesianInferenceEngine._THRESHOLD_SIGNAL_FOR_CODE.values()
            )
        }
        derived = set(_TELEMETRY_TO_THRESHOLD_KEY.items())
        missing = derived - pairs_in_code_table
        assert not missing, f"derived pairs absent from _THRESHOLD_SIGNAL_FOR_CODE: {missing}"

    def test_every_threshold_signal_is_reachable(self, thresholds: dict) -> None:
        """All seven DB signals must be coverable by some telemetry spelling."""
        reachable = set(_TELEMETRY_TO_THRESHOLD_KEY.values()) | set(
            _TELEMETRY_SIGNAL_ALIASES.values()
        )
        unreachable = set(thresholds) - reachable
        assert not unreachable, f"threshold signals unreachable: {unreachable}"


class TestSilenceOnHealthyData:
    """A normal session must produce no noise."""

    def test_nominal_values_emit_nothing(self) -> None:
        assert telemetry_plausibility_block(
            {"EngineSpeed": 1800.0, "CoolantTemp": 88.0, "VehicleSpeed": 90.0}
        ) == ""

    def test_empty_telemetry_emits_nothing(self) -> None:
        assert telemetry_plausibility_block({}) == ""

    def test_unknown_signal_is_not_judged(self) -> None:
        """No recorded envelope -> no judgement (never a fabricated limit)."""
        assert telemetry_plausibility_block({"NotARecordedSignal": 99999}) == ""

    def test_private_context_keys_are_skipped(self) -> None:
        """`_query_fmi` and friends are engine context, not measurements."""
        assert telemetry_plausibility_block({"_query_fmi": 3, "_other": 99999}) == ""

    def test_non_numeric_values_are_skipped(self) -> None:
        assert telemetry_plausibility_block({"VehicleSpeed": "fast"}) == ""
        assert telemetry_plausibility_block({"VehicleSpeed": None}) == ""

    def test_boolean_is_not_a_measurement(self) -> None:
        """`True` is an int in Python; it must not be compared as 1."""
        assert telemetry_plausibility_block({"VehicleSpeed": True}) == ""

    def test_boundary_values_are_inside(self) -> None:
        """The envelope is inclusive — the sensor floor is not a violation."""
        assert telemetry_plausibility_block({"CoolantTemp": -40.0}) == ""
        assert telemetry_plausibility_block({"CoolantTemp": 108.0}) == ""
        assert telemetry_plausibility_block({"VehicleSpeed": 250.0}) == ""


class TestFlagging:
    """Outside-envelope values must be flagged with limit and unit."""

    def test_impossible_speed_is_flagged(self) -> None:
        block = telemetry_plausibility_block({"VehicleSpeed": 350.0})
        assert MARKER in block
        assert "250" in block, "the recorded limit must be shown"
        assert "km/h" in block, "the unit must be shown"

    def test_impossible_rpm_is_flagged(self) -> None:
        block = telemetry_plausibility_block({"EngineSpeed": 9000.0})
        assert MARKER in block
        assert "rpm" in block

    def test_below_floor_is_flagged(self) -> None:
        block = telemetry_plausibility_block({"CoolantTemp": -40.1})
        assert MARKER in block
        assert "altında" in block

    def test_above_ceiling_is_flagged(self) -> None:
        block = telemetry_plausibility_block({"CoolantTemp": 108.1})
        assert MARKER in block
        assert "üstünde" in block

    def test_multiple_violations_all_reported(self) -> None:
        block = telemetry_plausibility_block({"VehicleSpeed": 350.0, "EngineSpeed": 9000.0})
        assert block.count("•") == 2

    def test_output_is_deterministic(self) -> None:
        data = {"VehicleSpeed": 350.0, "EngineSpeed": 9000.0}
        assert telemetry_plausibility_block(data) == telemetry_plausibility_block(data)

    def test_flag_does_not_assert_a_diagnosis(self) -> None:
        """The block flags the READING, never names a failed component."""
        block = telemetry_plausibility_block({"VehicleSpeed": 350.0}).lower()
        for forbidden in ("sensör arızalı", "değiştirin", "arıza tespit edildi"):
            assert forbidden not in block


class TestAliasResolution:
    """Documented alternative spellings must resolve to the same envelope."""

    @pytest.mark.parametrize(
        "spelling",
        ["RPM", "engine_speed", "VSS", "vehicle_speed", "CoolantTemperature"],
    )
    def test_alias_spellings_are_judged(self, spelling: str) -> None:
        assert telemetry_plausibility_block({spelling: 99999}) != ""

    def test_unknown_alias_is_silent(self) -> None:
        assert telemetry_plausibility_block({"EngineSpeedRPMUnknown": 99999}) == ""


class TestFailSafe:
    """A broken threshold DB must degrade to silence, never to an exception."""

    def test_broken_threshold_db_degrades_silently(self, monkeypatch) -> None:
        import src.engine.ai.anomaly_detector as ad

        def _boom(*_args, **_kwargs):
            raise ad.ThresholdDatabaseError("simulated corrupt DB")

        monkeypatch.setattr(ad, "load_thresholds", _boom)
        assert telemetry_plausibility_block({"VehicleSpeed": 350.0}) == ""


class TestReportWiring:
    """All three deep-dive report paths must carry the sweep."""

    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        ensure_external_dtc_database_loaded()

    def test_4stage_report_flags_impossible_value(self) -> None:
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            "P0300", {"VehicleSpeed": 350.0}
        )
        assert MARKER in report

    def test_4stage_report_silent_on_healthy_values(self) -> None:
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            "P0300", {"EngineSpeed": 1800.0, "CoolantTemp": 88.0}
        )
        assert MARKER not in report

    def test_multi_dtc_report_flags_impossible_value(self) -> None:
        codes = ["P0300", "P0420"]
        report = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {"VehicleSpeed": 350.0}, analyze_active_dtc_clusters(codes)
        )
        assert MARKER in report

    def test_j1939_report_flags_impossible_value(self) -> None:
        from src.engine.ai.diagnostic_copilot import get_j1939_spn_database

        row = (get_j1939_spn_database().get("spns") or {}).get("SPN_100")
        assert isinstance(row, dict), "fixture assumption"
        report = CausalBayesianInferenceEngine._format_j1939_technician_report(
            row, "SPN 100 FMI 3", {"VehicleSpeed": 350.0}, None
        )
        assert MARKER in report

    def test_j1939_report_silent_on_healthy_values(self) -> None:
        from src.engine.ai.diagnostic_copilot import get_j1939_spn_database

        row = (get_j1939_spn_database().get("spns") or {}).get("SPN_100")
        report = CausalBayesianInferenceEngine._format_j1939_technician_report(
            row, "SPN 100 FMI 3", {"EngineSpeed": 1800.0}, None
        )
        assert MARKER not in report

"""FAZ 3 unit tests: threshold DB validation + generic anomaly detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.anomaly_detector import (
    ThresholdDatabaseError,
    detect_anomalies,
    load_thresholds,
)


def _session(samples):
    s = VehicleSession(session_id="sess-anom", started_at_ns=1, domain=DiagnosticDomain.HEAVY_DUTY)
    s.samples.extend(samples)
    return s


def _sample(name: str, value: float, ts_ns: int = 1_000_000_000, source=SignalSource.J1939, confidence=1.0):
    return SignalSample(
        timestamp_ns=ts_ns,
        name=name,
        raw_value=int(value) if abs(value) < 2**31 else 0,
        physical_value=value,
        unit="",
        source=source,
        confidence=confidence,
    )


def _session_with_dtc(codes):
    s = _session([])
    for code in codes:
        s.events.append(
            DiagnosticEvent(
                timestamp_ns=1,
                code=code,
                domain=DiagnosticDomain.HEAVY_DUTY,
                severity="UNKNOWN",
                status="ACTIVE",
            )
        )
    return s


class TestThresholdDbValidation:
    def test_shipped_db_loads(self) -> None:
        thresholds = load_thresholds()
        assert "EngineCoolantTemp" in thresholds
        assert "EngineOilPressure" in thresholds
        for name, entry in thresholds.items():
            assert str(entry.get("source_ref", "")).strip(), f"{name} missing source_ref"

    def test_missing_source_ref_rejected(self, tmp_path: Path) -> None:
        payload = {
            "schema_version": 1,
            "signals": {
                "X": {"unit": "v", "ranges": [{"min": 0.0, "max": 1.0}], "source_ref": ""}
            },
        }
        p = tmp_path / "t.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ThresholdDatabaseError):
            load_thresholds(p)

    def test_unknown_field_rejected(self, tmp_path: Path) -> None:
        payload = {
            "schema_version": 1,
            "signals": {
                "X": {"unit": "v", "ranges": [{"min": 0.0, "max": 1.0}], "source_ref": "kb", "evil": 1}
            },
        }
        p = tmp_path / "t.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ThresholdDatabaseError):
            load_thresholds(p)

    def test_bad_schema_version_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"schema_version": 2, "signals": {}}), encoding="utf-8")
        with pytest.raises(ThresholdDatabaseError):
            load_thresholds(p)


class TestAnomalyDetection:
    def test_overheat_flagged(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("EngineCoolantTemp", 110.0 + i) for i in range(4)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert len(findings) == 1
        assert findings[0].signal == "EngineCoolantTemp"
        assert findings[0].ratio == 1.0
        assert findings[0].synthetic is False

    def test_nominal_samples_no_finding(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("EngineCoolantTemp", 85.0) for _ in range(10)]
        assert detect_anomalies(_session(samples), thresholds) == []

    def test_unknown_signal_silent_pass(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("SomeUnmappedSignal", 99999.0) for _ in range(10)]
        # No threshold entry -> no finding, no fabrication.
        assert detect_anomalies(_session(samples), thresholds) == []

    def test_ratio_below_threshold_no_finding(self) -> None:
        thresholds = load_thresholds()
        # 1 of 10 out of range (10% < 20% default) -> not an anomaly
        samples = [_sample("EngineCoolantTemp", 90.0) for _ in range(9)]
        samples.append(_sample("EngineCoolantTemp", 200.0))
        assert detect_anomalies(_session(samples), thresholds) == []

    def test_operator_measurement_marked_synthetic(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("OP:EngineOilPressure", 0.2) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        # T56-B / A3-2: the "OP:" prefix is resolved to the base signal name
        # before the threshold lookup, so an operator-declared measurement
        # produces a finding AND carries the synthetic flag (half weight in
        # hypothesis_engine).
        assert len(findings) == 1
        assert findings[0].signal == "OP:EngineOilPressure"
        assert findings[0].synthetic is True
        assert "operatör beyanı" in findings[0].finding

    def test_operator_prefix_resolves_to_threshold_key(self) -> None:
        """An unknown signal is still an honest silent pass, even with OP:."""
        thresholds = load_thresholds()
        samples = [_sample("OP:SomeUnmappedSignal", 99999.0) for _ in range(5)]
        assert detect_anomalies(_session(samples), thresholds) == []

    def test_operator_declared_oil_pressure_scores_lower(self) -> None:
        """FAZ 3.2 half-weight: a declared anomaly cannot outrank a real one.

        Both recordings contain the same SPN 110 DTC and the same 112 C
        coolant reading. ``EngineCoolantTemp`` (direct) feeds the
        thermostat node a full-weight (+0.40) signal hit, so only
        ``thermostat-stuck`` reaches 1.0 and the sensor node stays at 0.5.
        ``OP:EngineCoolantTemp`` (operator-declared) is marked synthetic and
        weighs half (+0.20), which is no longer enough to separate the two
        nodes — both normalize to 1.0. Before the fix the prefixed sample
        produced NO finding at all, so the declared run degenerated to the
        bare-DTC case.
        """
        from src.engine.ai.hypothesis_engine import load_root_cause_graph, rank_hypotheses

        thresholds = load_thresholds()
        graph = load_root_cause_graph()
        scores = {}
        for name in ("EngineCoolantTemp", "OP:EngineCoolantTemp"):
            session = _session_with_dtc(["SPN 110 FMI 0"])
            session.samples.extend(_sample(name, 112.0) for _ in range(5))
            findings = detect_anomalies(session, thresholds)
            assert len(findings) == 1, "operator declaration produced no finding"
            assert findings[0].signal == name
            assert findings[0].synthetic is (name.startswith("OP:"))
            hyps = rank_hypotheses(session, findings, [], graph=graph)
            assert hyps, "no hypothesis produced"
            scores[name] = {h.id: h.score for h in hyps}
        # Direct declaration: the signal hit lifts (and separates) the top node.
        assert scores["EngineCoolantTemp"]["thermostat-stuck"] > scores["EngineCoolantTemp"][
            "coolant-sensor-open"
        ]
        # Operator declaration: half weight removes that lift entirely.
        declared = scores["OP:EngineCoolantTemp"]
        assert declared["thermostat-stuck"] == declared["coolant-sensor-open"]
        assert declared["thermostat-stuck"] <= scores["EngineCoolantTemp"]["thermostat-stuck"]

    def test_determinism(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("EngineCoolantTemp", 110.0) for _ in range(5)]
        s1, s2 = _session(samples), _session(list(samples))
        assert detect_anomalies(s1, thresholds) == detect_anomalies(s2, thresholds)

    def test_camelize_map_links_j1939_names(self) -> None:
        thresholds = load_thresholds()
        # "engine_coolant_temp" (J1939 evidence naming) maps to the DB key.
        samples = [_sample("engine_coolant_temp", 112.0) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert len(findings) == 1
        assert findings[0].signal == "engine_coolant_temp"


class TestOilPressureBands:
    def test_rpm_band_selection_low_band(self) -> None:
        thresholds = load_thresholds()
        # RPM reference recorded at idle -> oil pressure min 1.0 bar band.
        samples = [_sample("EngineSpeed", 600.0)] + [_sample("EngineOilPressure", 0.4) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert any(f.signal == "EngineOilPressure" for f in findings)

    def test_no_rpm_reference_uses_first_band(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("EngineOilPressure", 0.4) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert any(f.signal == "EngineOilPressure" for f in findings)


class TestIntegrityBands:
    """SPN-DB-derived integrity bands (flag corrupt data, never false-alarm)."""

    def test_corrupt_engine_speed_flagged(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("EngineSpeed", 9000.0) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert any(f.signal == "EngineSpeed" for f in findings)

    def test_nominal_engine_speed_passes(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("EngineSpeed", 1500.0) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert not any(f.signal == "EngineSpeed" for f in findings)

    def test_corrupt_vehicle_speed_flagged(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("VehicleSpeed", 300.0) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert any(f.signal == "VehicleSpeed" for f in findings)

    def test_engine_load_bounds(self) -> None:
        thresholds = load_thresholds()
        bad = [_sample("EngineLoad", 300.0) for _ in range(5)]
        assert any(f.signal == "EngineLoad" for f in detect_anomalies(_session(bad), thresholds))
        good = [_sample("EngineLoad", 65.0) for _ in range(5)]
        assert not any(f.signal == "EngineLoad" for f in detect_anomalies(_session(good), thresholds))

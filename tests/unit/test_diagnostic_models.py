"""Unit tests for the P1 diagnostic session data model (Görev 3).

Covers frozen immutability, sentinel/boundary rejection (NaN/Inf
physical_value), DISCOVERED confidence gating, VIN mask enforcement
(fail-closed on a raw 17-char VIN), and status validation.
"""

from __future__ import annotations

import math

import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.diagnostic_copilot import mask_vin_in_text


class TestSignalSample:
    def test_frozen_immutability(self) -> None:
        sample = SignalSample(
            timestamp_ns=1,
            name="EngineSpeed",
            raw_value=1500,
            physical_value=1500.0,
            unit="rpm",
            source=SignalSource.DBC,
        )
        with pytest.raises(AttributeError):
            sample.physical_value = 9999.0  # type: ignore[misc]

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
    def test_non_finite_physical_value_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError, match="finite"):
            SignalSample(
                timestamp_ns=1,
                name="BoostPressure",
                raw_value=0,
                physical_value=bad,
                unit="bar",
                source=SignalSource.J1939,
            )

    def test_discovered_requires_confidence_below_one(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            SignalSample(
                timestamp_ns=1,
                name="Candidate_0x18FEF1",
                raw_value=10,
                physical_value=10.0,
                unit="-",
                source=SignalSource.DISCOVERED,
                confidence=1.0,
            )
        # Below 1.0 is accepted (confidence-gated discovery):
        ok = SignalSample(
            timestamp_ns=1,
            name="Candidate_0x18FEF1",
            raw_value=10,
            physical_value=10.0,
            unit="-",
            source=SignalSource.DISCOVERED,
            confidence=0.72,
        )
        assert ok.confidence == 0.72

    def test_confidence_bounds(self) -> None:
        for bad in (-0.01, 1.01):
            with pytest.raises(ValueError, match="confidence"):
                SignalSample(
                    timestamp_ns=1,
                    name="X",
                    raw_value=0,
                    physical_value=0.0,
                    unit="-",
                    source=SignalSource.OBD2,
                    confidence=bad,
                )

    def test_empty_name_and_negative_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="name"):
            SignalSample(
                timestamp_ns=1, name="  ", raw_value=0, physical_value=0.0, unit="", source=SignalSource.UDS
            )
        with pytest.raises(ValueError, match="timestamp"):
            SignalSample(
                timestamp_ns=-1, name="X", raw_value=0, physical_value=0.0, unit="", source=SignalSource.UDS
            )


class TestDiagnosticEvent:
    def test_frozen_immutability(self) -> None:
        event = DiagnosticEvent(
            timestamp_ns=42,
            code="P0201",
            domain=DiagnosticDomain.PASSENGER,
            severity="MEDIUM",
            status="ACTIVE",
            related_signals=("EngineSpeed",),
        )
        with pytest.raises(AttributeError):
            event.status = "HISTORY"  # type: ignore[misc]

    @pytest.mark.parametrize("status", ["active", "", "CONFIRMED", None])
    def test_invalid_status_rejected(self, status: str) -> None:
        with pytest.raises(ValueError, match="status"):
            DiagnosticEvent(
                timestamp_ns=42,
                code="P0201",
                domain=DiagnosticDomain.PASSENGER,
                severity="MEDIUM",
                status=status,
            )

    def test_empty_code_rejected(self) -> None:
        with pytest.raises(ValueError, match="code"):
            DiagnosticEvent(
                timestamp_ns=42,
                code="",
                domain=DiagnosticDomain.MARINE,
                severity="LOW",
                status="ACTIVE",
            )


class TestVehicleSession:
    def test_raw_vin_rejected_fail_closed(self) -> None:
        # A structurally valid raw VIN (no I/O/Q) must NEVER be accepted.
        with pytest.raises(ValueError, match="RAW VIN"):
            VehicleSession(
                session_id="sess-1",
                started_at_ns=0,
                domain=DiagnosticDomain.MARINE,
                vin_masked="WVWZZZ1KZAW123456",
            )

    def test_masked_vin_accepted(self) -> None:
        masked = mask_vin_in_text("WVWZZZ1KZAW123456")
        assert masked == "***********123456"
        session = VehicleSession(
            session_id="sess-1",
            started_at_ns=0,
            domain=DiagnosticDomain.MARINE,
            vin_masked=masked,
        )
        assert session.vin_masked == "***********123456"

    def test_partial_mask_rejected(self) -> None:
        with pytest.raises(ValueError, match="mask_vin_in_text"):
            VehicleSession(
                session_id="sess-1",
                started_at_ns=0,
                domain=DiagnosticDomain.HEAVY_DUTY,
                vin_masked="123456",  # not a mask_vin_in_text output
            )

    def test_empty_session_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="session_id"):
            VehicleSession(session_id=" ", started_at_ns=0, domain=DiagnosticDomain.INDUSTRIAL)

    def test_session_accumulates_frozen_records(self) -> None:
        session = VehicleSession(
            session_id="volvo-penta-d4-300-001",
            started_at_ns=1_000,
            domain=DiagnosticDomain.MARINE,
            make="Volvo Penta",
            model="D4-300",
            trace_ref="volvo_penta_d4_300_nonstart.json",
        )
        sample = SignalSample(
            timestamp_ns=1_500, name="EngineSpeed", raw_value=0, physical_value=0.0, unit="rpm", source=SignalSource.J1939
        )
        event = DiagnosticEvent(
            timestamp_ns=1_600,
            code="SPN 84 FMI 4",
            domain=DiagnosticDomain.MARINE,
            severity="MEDIUM",
            status="ACTIVE",
        )
        session.samples.append(sample)
        session.events.append(event)
        assert session.samples[0].physical_value == 0.0
        assert math.isfinite(session.samples[0].physical_value)
        assert session.events[0].status == "ACTIVE"

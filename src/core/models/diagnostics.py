"""P1 diagnostic session data model (Golden-Traces foundation).

Hexagonal core layer: zero framework dependencies, stdlib only. Frozen
dataclasses keep telemetry evidence immutable — an AI narrative layer can
never rewrite recorded samples or events (AGENTS.md §2.3 No Fabricated
Telemetry).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum

# 17-char VIN pattern (ISO 3779): excludes I, O, Q. Used fail-closed: a raw
# VIN must NEVER enter a VehicleSession — only mask_vin_in_text output.
_RAW_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_MASKED_VIN_RE = re.compile(r"^\*{11}[A-HJ-NPR-Z0-9]{6}$")


class SignalSource(Enum):
    """Origin of a decoded signal sample."""

    DBC = "DBC"
    J1939 = "J1939"
    UDS = "UDS"
    OBD2 = "OBD2"
    NMEA2000 = "NMEA2000"
    DISCOVERED = "DISCOVERED"  # auto-discovery output; confidence < 1.0


class DiagnosticDomain(Enum):
    """Vehicle/equipment domain of a diagnostic session."""

    HEAVY_DUTY = "HEAVY_DUTY"
    PASSENGER = "PASSENGER"
    MARINE = "MARINE"
    INDUSTRIAL = "INDUSTRIAL"


@dataclass(slots=True, frozen=True)
class SignalSample:
    """One immutable decoded signal value at a monotonic timestamp."""

    timestamp_ns: int  # monotonic, clock_provider sourced
    name: str  # DBC signal name / SPN-N / PID hex
    raw_value: int
    physical_value: float
    unit: str
    source: SignalSource
    confidence: float = 1.0  # DISCOVERED signals must carry < 1.0

    def __post_init__(self) -> None:
        # Trust-boundary validation: sentinel/non-finite physical values are
        # rejected at construction — a fabricated NaN must never enter the
        # evidence base (fail-closed, AGENTS.md §2.3).
        if not math.isfinite(self.physical_value):
            raise ValueError(f"SignalSample physical_value must be finite (got {self.physical_value!r})")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0] (got {self.confidence!r})")
        if self.source is SignalSource.DISCOVERED and self.confidence >= 1.0:
            raise ValueError("DISCOVERED signals must carry confidence < 1.0 (confidence-gated)")
        if not self.name or not self.name.strip():
            raise ValueError("SignalSample name must be non-empty")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")


@dataclass(slots=True, frozen=True)
class DiagnosticEvent:
    """One immutable diagnostic trouble-code event."""

    timestamp_ns: int
    code: str  # "P0201" | "SPN 84 FMI 4" | "UDS DID 0xF190 okundu"
    domain: DiagnosticDomain
    severity: str  # knowledge-base sourced; AI never produces this
    status: str  # ACTIVE | HISTORY | PENDING
    related_signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("DiagnosticEvent code must be non-empty")
        if self.status not in ("ACTIVE", "HISTORY", "PENDING"):
            raise ValueError(f"status must be ACTIVE|HISTORY|PENDING (got {self.status!r})")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")


@dataclass(slots=True)
class VehicleSession:
    """A diagnostic recording session bound to one vehicle/equipment.

    Mutable container (events/samples accumulate over the session lifetime)
    but every contained record is frozen. ``vin_masked`` accepts ONLY
    mask_vin_in_text output — a raw 17-char VIN fails construction.
    """

    session_id: str
    started_at_ns: int
    domain: DiagnosticDomain
    make: str | None = None
    model: str | None = None
    vin_masked: str | None = None  # mask_vin_in_text output; NEVER a raw VIN
    trace_ref: str | None = None  # Golden-Traces archive file reference
    events: list[DiagnosticEvent] = field(default_factory=list)
    samples: list[SignalSample] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.session_id or not self.session_id.strip():
            raise ValueError("session_id must be non-empty")
        if self.started_at_ns < 0:
            raise ValueError("started_at_ns must be non-negative")
        if self.vin_masked is not None:
            if _RAW_VIN_RE.fullmatch(self.vin_masked):
                raise ValueError("vin_masked contains a RAW VIN — mask it with mask_vin_in_text first")
            if not _MASKED_VIN_RE.fullmatch(self.vin_masked):
                raise ValueError(
                    "vin_masked must be mask_vin_in_text output (11 '*' + last 6 VIN chars)"
                )

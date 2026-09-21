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
#
# P2-2: the masking primitive itself now lives in core. It was previously
# defined only in `src.engine.ai.diagnostic_copilot`, which made the core
# layer depend (for a privacy control) on the engine layer that consumes it.
# `diagnostic_copilot` must import it from here — `from
# src.core.models.diagnostics import mask_vin_in_text` — instead of keeping a
# second copy; core must never import engine. The engine-side name is kept as a
# re-export so existing importers/tests keep working.
_RAW_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
RAW_VIN_FULLMATCH_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_MASKED_VIN_RE = re.compile(r"^\*{11}[A-HJ-NPR-Z0-9]{6}$")
_MASKED_VIN_UNANCHORED_RE = re.compile(r"\*{11}[A-HJ-NPR-Z0-9]{6}")

VIN_MASK_PLACEHOLDER: str = "*" * 11


def mask_vin_in_text(text: object) -> str:
    """Mask all but the last 6 chars of any 17-char VIN in free text.

    Canonical VIN privacy primitive (ISO 3779, `[A-HJ-NPR-Z0-9]{17}`, I/O/Q
    excluded). Case-insensitive: a lowercase VIN used to slip through unmasked
    and leak into logs / session summaries / exported reports.

    Already-masked VINs (``***********123456``) are left untouched — the 6
    trailing characters and the 11 ``*`` are not VIN-alphabet material, so the
    pattern cannot match them again (idempotent).

    This is the *only* VIN masking implementation. `VehicleSession.vin_masked`
    is fail-closed against raw VINs by construction (see the property setter).
    """
    return _RAW_VIN_RE.sub(lambda m: VIN_MASK_PLACEHOLDER + m.group(0)[-6:], str(text))


def _validate_vin_masked(value: str) -> str:
    """Fail-closed validation shared by the VehicleSession constructor and setter.

    A raw 17-char VIN is rejected outright (never silently masked — the caller
    must acknowledge the privacy boundary), and anything that is not
    `mask_vin_in_text` output is rejected too, so the contract cannot be
    weakened by later assignment.
    """
    if RAW_VIN_FULLMATCH_RE.fullmatch(value):
        raise ValueError("vin_masked contains a RAW VIN — mask it with mask_vin_in_text first")
    if not _MASKED_VIN_RE.fullmatch(value):
        raise ValueError("vin_masked must be mask_vin_in_text output (11 '*' + last 6 VIN chars)")
    return value


class SignalSource(Enum):
    """Origin of a decoded signal sample."""

    DBC = "DBC"
    J1939 = "J1939"
    UDS = "UDS"
    OBD2 = "OBD2"
    NMEA2000 = "NMEA2000"
    DISCOVERED = "DISCOVERED"  # auto-discovery output; confidence < 1.0


class DiagnosticDomain(Enum):
    """Vehicle/equipment domain of a diagnostic session.

    P2-9: AGRICULTURE covers ISOBUS / ISO 11783 implements and SAE J1939
    agricultural equipment. ISOBUS is a J1939 derivative, so it belongs to the
    same diagnostic domain family rather than a separate protocol stack; it is
    kept as its own member so the domain never has to be guessed from the
    make/model strings.
    """

    HEAVY_DUTY = "HEAVY_DUTY"
    PASSENGER = "PASSENGER"
    MARINE = "MARINE"
    INDUSTRIAL = "INDUSTRIAL"
    AGRICULTURE = "AGRICULTURE"


class Severity(str, Enum):
    """Allowlisted diagnostic severity rungs (knowledge-base sourced).

    P2-9: `DiagnosticEvent.severity` used to be a free string, so a typo
    ("HIHG") or an AI-invented rung entered the evidence base unnoticed. The
    rungs mirror the deterministic expert engine's `FaultSeverity`
    (`src/engine/ai/diagnostic_copilot.py`) 1:1 so evidence stays comparable
    with analysis output.

    `str` mixin, not a bare `Enum`: severity is a serialization value — it is
    compared with plain strings and written straight into JSON/YAML reports —
    so the members must *be* strings (`Severity("MEDIUM") == "MEDIUM"`, and
    `json.dumps` emits `"MEDIUM"` rather than `"Severity.MEDIUM"`). That also
    lets the in-scope producers keep passing the documented literal
    (`severity=Severity.MEDIUM` or `severity="MEDIUM"`; the engine's
    `dialogue_engine.py`/`calibration.py` construct events with the literal).

    Validation still bites: a bare `str` is NOT a `Severity`, and any value
    outside this allowlist raises at construction. Core must not import engine
    — the two enums are kept in sync by value, and the engine's `FaultSeverity`
    should become a re-export of this class.
    """

    #: Deliberately-modelled "we do not know". The desktop evidence bridge
    #: (`src/ui/desktop_app.py`) and the engine emit this for a DTC with no
    #: knowledge-base entry — it is the honest, non-fabricated answer
    #: (AGENTS.md §2.3), NOT a placeholder to be defaulted away, so it is a
    #: first-class allowlisted rung.
    UNKNOWN = "UNKNOWN"
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL_STOP = "CRITICAL_STOP"


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
    severity: Severity  # knowledge-base sourced; AI never produces this
    status: str  # ACTIVE | HISTORY | PENDING
    related_signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("DiagnosticEvent code must be non-empty")
        if self.status not in ("ACTIVE", "HISTORY", "PENDING"):
            raise ValueError(f"status must be ACTIVE|HISTORY|PENDING (got {self.status!r})")
        if not isinstance(self.severity, Severity):
            # Accept the equivalent documented literal by normalising it to the
            # enum member, then fail closed on anything outside the allowlist.
            # (`Severity` mixes in `str`, so `Severity.MEDIUM == "MEDIUM"` — but
            # an arbitrary string is NOT a member and must never slip through.)
            try:
                object.__setattr__(self, "severity", Severity(self.severity))
            except ValueError as exc:
                raise ValueError(
                    "severity must be one of "
                    f"{sorted(s.value for s in Severity)} "
                    f"(got {type(self.severity).__name__} {self.severity!r})"
                ) from exc
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")


@dataclass(slots=True)
class VehicleSession:
    """A diagnostic recording session bound to one vehicle/equipment.

    Mutable container (events/samples accumulate over the session lifetime)
    but every contained record is frozen. ``vin_masked`` accepts ONLY
    mask_vin_in_text output — a raw 17-char VIN fails construction AND fails
    any later assignment (P0-2: the check used to run in ``__post_init__``
    only, so a raw VIN could be attached after construction).
    """

    session_id: str
    started_at_ns: int
    domain: DiagnosticDomain
    make: str | None = None
    model: str | None = None
    #: Masked VIN ONLY (`mask_vin_in_text` output). Public accessor is the
    #: `vin_masked` property; the custom `__init__` below keeps `vin_masked=`
    #: as the constructor keyword callers already use.
    vin: str | None = field(default=None, repr=False)
    trace_ref: str | None = None  # Golden-Traces archive file reference
    events: list[DiagnosticEvent] = field(default_factory=list)
    samples: list[SignalSample] = field(default_factory=list)

    def __init__(
        self,
        session_id: str,
        started_at_ns: int,
        domain: DiagnosticDomain,
        make: str | None = None,
        model: str | None = None,
        vin_masked: str | None = None,
        trace_ref: str | None = None,
        events: list[DiagnosticEvent] | None = None,
        samples: list[SignalSample] | None = None,
        *,
        vin: str | None = None,
    ) -> None:
        # P0-2: construction and every later assignment share ONE fail-closed
        # validation path (`_validate_vin_masked`). A raw 17-char VIN is rejected
        # in both, not just at construction time.
        if vin is not None:
            if vin_masked is not None and vin != vin_masked:
                raise ValueError("vin and vin_masked disagree — pass only one of them")
            vin_masked = vin
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "started_at_ns", started_at_ns)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "make", make)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "trace_ref", trace_ref)
        object.__setattr__(self, "events", list(events) if events is not None else [])
        object.__setattr__(self, "samples", list(samples) if samples is not None else [])
        self.vin_masked = vin_masked
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.session_id or not self.session_id.strip():
            raise ValueError("session_id must be non-empty")
        if self.started_at_ns < 0:
            raise ValueError("started_at_ns must be non-negative")
        # Any path that bypasses the custom __init__ (e.g. the dataclass
        # machinery behind `dataclasses.replace`) re-runs the same VIN check,
        # so no route into this container can carry an unvalidated value.
        if self.vin is not None:
            self.vin = _validate_vin_masked(self.vin)

    @property
    def vin_masked(self) -> str | None:
        """The masked-only VIN (mask_vin_in_text output, or None)."""
        return self.vin

    @vin_masked.setter
    def vin_masked(self, value: str | None) -> None:
        if value is None:
            self.vin = None
            return
        if not isinstance(value, str):
            raise ValueError(f"vin_masked must be a str or None (got {type(value).__name__})")
        self.vin = _validate_vin_masked(value)


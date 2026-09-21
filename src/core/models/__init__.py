"""Canonical domain models.

P2-8: the export surface used to expose `CanFrame` + the DLC helpers only, so
the P1 diagnostic data model (the evidence base for Golden-Traces) was not
reachable through the package. `TelemetrySignal`, named in AGENTS.md §1, does
not exist anywhere in the tree (1 grep hit, in AGENTS.md itself) and is
deliberately NOT invented here — that is an AGENTS.md documentation drift.
"""

from __future__ import annotations

from src.core.models.can_frame import (
    DLC_TO_LENGTH,
    CanFrame,
    dlc_to_length,
    get_hardware_crc_type,
    length_to_dlc,
    pad_payload,
)
from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    Severity,
    SignalSample,
    SignalSource,
    VehicleSession,
    mask_vin_in_text,
)

__all__ = [
    "DLC_TO_LENGTH",
    "CanFrame",
    "DiagnosticDomain",
    "DiagnosticEvent",
    "Severity",
    "SignalSample",
    "SignalSource",
    "VehicleSession",
    "dlc_to_length",
    "get_hardware_crc_type",
    "length_to_dlc",
    "mask_vin_in_text",
    "pad_payload",
]

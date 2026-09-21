"""Core Infrastructure, Error Handling, Logging, and Data Models.

P2-8: the public surface used to expose only `CanFrame` + the error hierarchy +
the logging helpers. The protocol exception taxonomy (`exceptions`), the P1
diagnostic data model (`models.diagnostics`) and the port contracts
(`contracts`) are all part of core's published API and are re-exported here so
consumers do not have to reach into private module paths.

AGENTS.md §1 names a `TelemetrySignal` class that does not exist anywhere in
the tree (repo-wide grep: 1 hit, in AGENTS.md itself). It is deliberately NOT
invented here — the actual P1 telemetry record is `SignalSample`
(plus `DiagnosticEvent` / `VehicleSession`). That is an AGENTS.md drift.
"""

from __future__ import annotations

from src.core import contracts, errors, exceptions, models
from src.core.contracts import (
    ClockProvider,
    RxSubscription,
    SecretProvider,
    SystemClockProvider,
    TxPort,
    VirtualClock,
)
from src.core.errors import (
    HardwareError,
    LicenseError,
    PlatformError,
    ProtocolError,
    SafetyError,
    SecurityError,
    TransportError,
)
from src.core.exceptions import (
    IsoTpBufferOverflowError,
    IsoTpError,
    IsoTpFlowControlError,
    IsoTpInvalidPduError,
    IsoTpSequenceError,
    IsoTpTimeoutError,
    J1939SequenceError,
    J1939SessionCollisionError,
    J1939TpAbortError,
    J1939TpError,
    J1939TpTimeoutError,
)
from src.core.logging import get_logger, setup_logging
from src.core.models.can_frame import CanFrame, dlc_to_length, length_to_dlc, pad_payload
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
    "CanFrame",
    "ClockProvider",
    "DiagnosticDomain",
    "DiagnosticEvent",
    "HardwareError",
    "IsoTpBufferOverflowError",
    "IsoTpError",
    "IsoTpFlowControlError",
    "IsoTpInvalidPduError",
    "IsoTpSequenceError",
    "IsoTpTimeoutError",
    "J1939SequenceError",
    "J1939SessionCollisionError",
    "J1939TpAbortError",
    "J1939TpError",
    "J1939TpTimeoutError",
    "LicenseError",
    "PlatformError",
    "ProtocolError",
    "RxSubscription",
    "SafetyError",
    "SecretProvider",
    "SecurityError",
    "Severity",
    "SignalSample",
    "SignalSource",
    "SystemClockProvider",
    "TransportError",
    "TxPort",
    "VehicleSession",
    "VirtualClock",
    "contracts",
    "dlc_to_length",
    "errors",
    "exceptions",
    "get_logger",
    "length_to_dlc",
    "mask_vin_in_text",
    "models",
    "pad_payload",
    "setup_logging",
]

"""Safety Layer and TX Interlock Controllers.

Lazy re-exports (AGENTS.md §8 / T62-M10)
----------------------------------------
The safety package exposes its public surface through PEP 562 module-level
``__getattr__`` instead of eager top-level imports.

Rationale: ``src/safety`` contains leaf modules that are legitimately imported
from NON-transmitting layers — ``src.engine.buffer.rolling_disk`` uses
``src.safety.secret_provider`` for HMAC key material. With eager imports,
merely requesting that leaf executed this file and pulled in
``src.safety.gateway`` / ``src.safety.estop`` (the TX choke-point modules),
which transitively imported the HAL and network surfaces. That broke the
fully-offline AI-layer invariant (``tests/safety/test_ai_tx_isolation.py``)
for any code path that reached ``src.engine``.

Deferring resolution to attribute access keeps ``from src.safety import
TxSafetyGateway`` working exactly as before while ensuring a leaf-module
import no longer drags the TX choke-point (or HAL/socket) into the process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cost
    from src.safety.estop import (
        EmergencyStopSystem,
        EStopEvent,
        EStopResetAuthority,
        EStopTriggerSource,
    )
    from src.safety.exceptions import (
        DualConfirmationRequiredError,
        FrameSanityError,
        RateLimitExceededError,
        SpeedDataStaleError,
        SpeedInterlockError,
        WhitelistFailClosedError,
        WhitelistViolationError,
    )
    from src.safety.gateway import TxSafetyGateway
    from src.safety.state_machine import SafetyState, SafetySupervisor
    from src.safety.watchdog import TxWatchdogSupervisor

# Maps each public name to the submodule that defines it. Resolution is lazy:
# nothing here is imported until the attribute is actually touched.
_EXPORTS: dict[str, str] = {
    "EmergencyStopSystem": "src.safety.estop",
    "EStopEvent": "src.safety.estop",
    "EStopResetAuthority": "src.safety.estop",
    "EStopTriggerSource": "src.safety.estop",
    "DualConfirmationRequiredError": "src.safety.exceptions",
    "FrameSanityError": "src.safety.exceptions",
    "RateLimitExceededError": "src.safety.exceptions",
    "SpeedDataStaleError": "src.safety.exceptions",
    "SpeedInterlockError": "src.safety.exceptions",
    "WhitelistFailClosedError": "src.safety.exceptions",
    "WhitelistViolationError": "src.safety.exceptions",
    "TxSafetyGateway": "src.safety.gateway",
    "SafetyState": "src.safety.state_machine",
    "SafetySupervisor": "src.safety.state_machine",
    "TxWatchdogSupervisor": "src.safety.watchdog",
}

__all__ = [
    "DualConfirmationRequiredError",
    "EStopEvent",
    "EStopResetAuthority",
    "EStopTriggerSource",
    "EmergencyStopSystem",
    "FrameSanityError",
    "RateLimitExceededError",
    "SafetyState",
    "SafetySupervisor",
    "SpeedDataStaleError",
    "SpeedInterlockError",
    "TxSafetyGateway",
    "TxWatchdogSupervisor",
    "WhitelistFailClosedError",
    "WhitelistViolationError",
]


def __getattr__(name: str) -> Any:
    """Resolve a public safety export on first access (PEP 562)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    # Cache on the package so subsequent lookups skip __getattr__ entirely.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the lazy exports to dir()/introspection tooling."""
    return sorted(set(globals()) | set(__all__))

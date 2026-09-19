"""SAE J1939 Heavy-Duty Vehicle Diagnostic Protocol Stack.

Lazy re-exports (AGENTS.md §8 / T62-M10)
----------------------------------------
Exports resolve through PEP 562 ``__getattr__`` rather than eager imports.

Rationale: ``src.engine.decoder.dbc_decoder`` legitimately imports the leaf
module ``src.protocols.j1939.sentinel`` for its sentinel filter. With eager
imports, that request executed this file and dragged in
``src.protocols.j1939.transport`` (a TX-bearing module named in the AI-layer
forbidden-import list) along with ``address_claim``/``diagnostics``. Deferring
resolution keeps ``from src.protocols.j1939 import J1939TransportProtocol``
working while a leaf import stays free of the transport machinery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cost
    from src.protocols.j1939.address_claim import (
        AddressClaimEngine,
        AddressClaimState,
        J1939Name,
    )
    from src.protocols.j1939.diagnostics import (
        DiagnosticTroubleCode,
        DMMessage,
        J1939DiagnosticService,
        LampStatus,
    )
    from src.protocols.j1939.sentinel import J1939SentinelFilter, SignalQuality
    from src.protocols.j1939.transport import (
        CompletedMessage,
        J1939TransportProtocol,
    )

_EXPORTS: dict[str, str] = {
    "AddressClaimEngine": "src.protocols.j1939.address_claim",
    "AddressClaimState": "src.protocols.j1939.address_claim",
    "J1939Name": "src.protocols.j1939.address_claim",
    "DiagnosticTroubleCode": "src.protocols.j1939.diagnostics",
    "DMMessage": "src.protocols.j1939.diagnostics",
    "J1939DiagnosticService": "src.protocols.j1939.diagnostics",
    "LampStatus": "src.protocols.j1939.diagnostics",
    "J1939SentinelFilter": "src.protocols.j1939.sentinel",
    "SignalQuality": "src.protocols.j1939.sentinel",
    "CompletedMessage": "src.protocols.j1939.transport",
    "J1939TransportProtocol": "src.protocols.j1939.transport",
}

__all__ = [
    "AddressClaimEngine",
    "AddressClaimState",
    "CompletedMessage",
    "DMMessage",
    "DiagnosticTroubleCode",
    "J1939DiagnosticService",
    "J1939Name",
    "J1939SentinelFilter",
    "J1939TransportProtocol",
    "LampStatus",
    "SignalQuality",
]


def __getattr__(name: str) -> Any:
    """Resolve a public J1939 export on first access (PEP 562)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the lazy exports to dir()/introspection tooling."""
    return sorted(set(globals()) | set(__all__))

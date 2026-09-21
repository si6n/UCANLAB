"""Architectural gate: outbound CAN transmissions must go through the gateway.

REVIEW Aşama 5 (A5-6). AGENTS.md §2.1 requires that ALL outbound CAN
transmissions pass through `TxSafetyGateway`. At the time of the review the
rule was enforced only by a source comment: `AbstractBus.send()` is public,
there is no distinct `PrivilegedTxPort` type, and nothing failed if a new
module sent a frame directly.

Verification showed NO production bypass existed — every `.send(` in `src/`
was either a call on the gateway port, an ISO-TP/OBD client that receives
`tx_port=self.gateway`, or the driver's own python-can backend. That is exactly
why this gate is cheap to add and valuable: it keeps a future refactor from
introducing the bypass the review feared.

What this checks
----------------
For every module under `src/` (excluding a small, justified allowlist) it
scans for raw transmission calls — `.send(`, `._send_raw(`, `.send_raw(`,
`.transmit(` — and asserts that the enclosing module either:

* is on the allowlist (HAL drivers, the gateway itself, the bus port
  definition, virtual/test buses), or
* obtains its transmit port from the gateway (the file mentions `gateway`
  and/or `tx_port`).

The gate is deliberately *lexical and conservative*: it fails loudly and a
human resolves it, rather than trying to prove absence of bypass with a type
system the codebase does not yet have.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

#: Raw transmit primitives. `send` is the one people reach for;
#: `privileged_send` is the driver-level primitive the gateway itself calls.
_TRANSMIT_CALL_RE = re.compile(r"\.(?:privileged_send|send|send_raw|_send_raw|transmit)\s*\(")

#: Modules legitimately allowed to touch a raw transmit primitive.
#: Every entry needs a one-line reason; keep this list short and specific.
_ALLOWLIST: dict[str, str] = {
    # The audited choke-point itself.
    "src/safety/gateway.py": "the TxSafetyGateway — the single sanctioned sender",
    "src/safety/multiplexer.py": "TX scheduling multiplexer; delegates to the gateway port",
    # HAL: the actual bus port definition and its implementations. These are
    # the layer the gateway ultimately calls; they must be able to send.
    "src/hal/base.py": "AbstractBus port definition (defines the primitive)",
    "src/hal/virtual.py": "VirtualBus: software bus, not a physical transmission",
    "src/hal/rp1210/bus.py": "RP1210 driver bus",
    "src/hal/drivers/pcan_kvaser.py": "python-can PCAN/Kvaser backend",
    "src/hal/drivers/__init__.py": "driver package re-exports",
}

#: A module that mentions these has routed its transmission through the
#: gateway (composition-root injection) rather than talking to a driver.
_GATEWAY_MENTION_RE = re.compile(r"\b(gateway|tx_port|TxPort|TxSafetyGateway)\b")


def _iter_python_modules() -> list[Path]:
    return sorted(path for path in SRC_ROOT.rglob("*.py") if path.is_file())


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_transmit_primitives_are_confined_to_the_gateway_layer() -> None:
    """No module outside the allowlist may call a raw transmit primitive
    without routing through the gateway."""
    violations: list[str] = []
    for module in _iter_python_modules():
        rel = _relative(module)
        if rel in _ALLOWLIST:
            continue
        try:
            source = module.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable file is a different problem
            continue
        if not _TRANSMIT_CALL_RE.search(source):
            continue
        if _GATEWAY_MENTION_RE.search(source):
            # Routed through the gateway port — acceptable.
            continue
        hits = [m.group(0) for m in _TRANSMIT_CALL_RE.finditer(source)]
        violations.append(f"{rel}: {sorted(set(hits))}")

    assert not violations, (
        "Raw CAN transmit primitive(s) used without routing through "
        "TxSafetyGateway (AGENTS.md §2.1). Either send via the gateway port, or "
        "add the module to _ALLOWLIST in this test with a written justification:\n  "
        + "\n  ".join(violations)
    )


def test_allowlist_entries_still_exist() -> None:
    """A stale allowlist silently widens the gate; keep it honest."""
    missing = [rel for rel in _ALLOWLIST if not (REPO_ROOT / rel).is_file()]
    assert not missing, f"allowlist references non-existent module(s): {missing}"


def test_the_gateway_is_the_primary_sender() -> None:
    """The gateway module itself must actually perform the transmission."""
    gateway = (SRC_ROOT / "safety" / "gateway.py").read_text(encoding="utf-8")
    assert _TRANSMIT_CALL_RE.search(gateway), "the gateway must be the sender"


def test_desktop_app_wires_protocol_clients_through_the_gateway() -> None:
    """The composition root must hand protocol clients the gateway as tx_port."""
    desktop = (SRC_ROOT / "ui" / "desktop_app.py").read_text(encoding="utf-8")
    assert "tx_port=self.gateway" in desktop, (
        "protocol clients must receive the gateway as their transmit port"
    )


@pytest.mark.parametrize(
    ("snippet", "should_match"),
    [
        ("bus.send(frame)", True),
        ("self._bus._send_raw(frame)", True),
        ("driver.transmit(frame)", True),
        ("gateway.send(frame)", True),  # matching is lexical; routing is checked separately
        ("router.route(frame)", False),
        ("sender_name = 'x'", False),
    ],
)
def test_transmit_pattern_matches_expected_forms(snippet: str, should_match: bool) -> None:
    """Guard the detector itself against a silent regression."""
    assert bool(_TRANSMIT_CALL_RE.search(snippet)) is should_match

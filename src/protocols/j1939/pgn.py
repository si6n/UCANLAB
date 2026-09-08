"""Shared J1939 PGN parsing helpers (M-12 / P2-6).

PGN derivation was previously hand-rolled at six call sites as
`(pf << 8) | ps`-style bit math. Every one of them dropped the EDP bit
(bit 25) from the PGN, and the PDU1 branches additionally lost DP (bit 24)
— a frame with EDP set (J1939-22 reserved expansion / NMEA 2000 fast
packet interworking) produced a PGN that matched the wrong parameter group
or silently mis-routed diagnostics.

This module is the single authoritative parser. It is a re-export of the
battle-tested `parse_j1939_id` in the OEM registry (kept there for
backwards compatibility) plus a lightweight `pgn_from_id` convenience for
call sites that only need the PGN.

Usage:
    from src.protocols.j1939.pgn import pgn_from_id, parse_j1939_id
"""

from __future__ import annotations

__all__ = ["build_j1939_id", "parse_j1939_id", "pgn_from_id", "PGN_PDU1_BOUND"]

# PF < 240 (0xF0) selects PDU1 (destination-specific); PF >= 240 is PDU2.
PGN_PDU1_BOUND: int = 240


def _impl():
    """Lazily import the registry implementation (breaks the import cycle).

    The j1939 package __init__ imports address_claim (and more), which
    imports this module at module scope — importing oem.registry eagerly
    from here would re-enter the package __init__ while it is still
    initializing. The registry only depends on plain constants, so the
    deferred import is cheap after the first call.
    """
    from src.protocols.j1939.oem.registry import build_j1939_id, parse_j1939_id

    return build_j1939_id, parse_j1939_id


def parse_j1939_id(arbitration_id: int) -> tuple[int, int, int | None, int]:
    """Parse a 29-bit J1939 CAN ID into (pgn, sa, da, priority) with full EDP/DP preservation.

    Signature-compatible with src.protocols.j1939.oem.registry.parse_j1939_id.
    """
    _build, parse = _impl()
    return parse(arbitration_id)  # type: ignore[no-any-return]


def build_j1939_id(
    pgn: int,
    sa: int,
    da: int | None = None,
    priority: int = 6,
) -> int:
    """Construct a 29-bit CAN arbitration identifier from J1939 components.

    Signature-compatible with src.protocols.j1939.oem.registry.build_j1939_id.
    """
    build, _parse = _impl()
    return build(pgn=pgn, sa=sa, da=da, priority=priority)  # type: ignore[no-any-return]


def pgn_from_id(arbitration_id: int) -> int:
    """Return the 18-bit PGN (EDP+DP+PF+PS as applicable) for a 29-bit ID.

    Handles both PDU formats with full EDP/DP preservation — use this
    instead of manual `(pf << 8)` bit math anywhere in the codebase.
    """
    pgn, _sa, _da, _priority = parse_j1939_id(arbitration_id)
    return pgn

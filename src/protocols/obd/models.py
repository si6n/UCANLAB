"""Universal CAN-Bus Diagnostic & Telemetry Platform - OBD Data Models.

Provides canonical dataclasses for SAE J1979 OBD-II Parameter Identifiers (PIDs)
and ISO 14229 Diagnostic Data Identifiers (DIDs).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class ObdPidDefinition:
    """Definition and decoding metadata for a SAE J1979 Mode 01 PID."""

    pid: int
    name: str
    description: str
    bytes_length: int
    unit: str
    min_value: float | None = None
    max_value: float | None = None
    is_bitmask: bool = False
    scaling: float = 1.0
    offset: float = 0.0
    decoder: Callable[[bytes], Any] | None = None
    category: str = "general"

    def decode(self, raw_bytes: bytes) -> Any:
        """Decode raw payload bytes into physical value according to definition."""
        if len(raw_bytes) < self.bytes_length:
            raise ValueError(
                f"PID 0x{self.pid:02X} ({self.name}) requires at least {self.bytes_length} bytes, "
                f"got {len(raw_bytes)}"
            )
        if self.decoder is not None:
            return self.decoder(raw_bytes[: self.bytes_length])

        # Default numeric decoding if no custom decoder provided
        if self.bytes_length == 1:
            raw_val = raw_bytes[0]
        elif self.bytes_length == 2:
            raw_val = (raw_bytes[0] << 8) | raw_bytes[1]
        elif self.bytes_length == 4:
            raw_val = (raw_bytes[0] << 24) | (raw_bytes[1] << 16) | (raw_bytes[2] << 8) | raw_bytes[3]
        else:
            raw_val = int.from_bytes(raw_bytes[: self.bytes_length], byteorder="big")

        return (raw_val * self.scaling) + self.offset


@dataclass(slots=True, frozen=True)
class ObdPidResult:
    """Decoded result of an OBD-II Mode 01 PID query."""

    pid: int
    name: str
    raw_bytes: bytes
    value: Any
    unit: str
    timestamp_ns: int = field(default_factory=time.time_ns)
    is_valid: bool = True
    error_message: str | None = None


@dataclass(slots=True, frozen=True)
class UdsDidDefinition:
    """Definition and decoding metadata for an ISO 14229 UDS Data Identifier (DID)."""

    did: int
    name: str
    description: str
    length: int | None
    unit: str
    data_format: str = "numeric"  # "ascii", "bcd", "numeric", "bitfield", "raw_hex"
    min_value: float | None = None
    max_value: float | None = None
    scaling: float = 1.0
    offset: float = 0.0
    decoder: Callable[[bytes], Any] | None = None
    category: str = "identification"

    def decode(self, raw_bytes: bytes) -> Any:
        """Decode raw payload bytes into physical value according to definition."""
        # L-7 (P3-2): an empty payload carries no signal — reject it. The old
        # numeric branch turned `int.from_bytes(b"") == 0` into
        # `0 * scaling + offset` (e.g. silently -40.0 °C), a fabricated
        # measurement presented as valid telemetry.
        if not raw_bytes:
            raise ValueError(
                f"DID 0x{self.did:04X} ({self.name}) received an empty payload — "
                "no measurement can be decoded"
            )
        if self.length is not None and len(raw_bytes) < self.length:
            raise ValueError(
                f"DID 0x{self.did:04X} ({self.name}) requires at least {self.length} bytes, "
                f"got {len(raw_bytes)}"
            )
        if self.decoder is not None:
            return self.decoder(raw_bytes if self.length is None else raw_bytes[: self.length])

        if self.data_format == "ascii":
            # Strip trailing nulls/spaces and replace non-printable characters
            try:
                return raw_bytes.decode("ascii", errors="replace").strip("\x00 \t\r\n")
            except Exception:
                return raw_bytes.hex()
        elif self.data_format == "raw_hex":
            return raw_bytes.hex().upper()
        elif self.data_format == "bcd":
            # Format BCD bytes as hex string or formatted string
            return "".join(f"{b:02X}" for b in raw_bytes)
        else:
            # Default big-endian numeric decoding.
            # L-7b (micro): only well-defined integer widths may decode as
            # numeric — the old `else: int.from_bytes(...)` happily turned a
            # 3-byte payload into 66011-ish values with no physical meaning.
            # Unsupported widths fail closed instead of fabricating telemetry.
            if self.length is None and len(raw_bytes) not in (1, 2, 4):
                raise ValueError(
                    f"DID 0x{self.did:04X} ({self.name}) numeric decode requires a "
                    f"1/2/4-byte payload, got {len(raw_bytes)} bytes — declare an "
                    "explicit length or a custom decoder"
                )
            if len(raw_bytes) == 1:
                raw_val = raw_bytes[0]
            elif len(raw_bytes) == 2:
                raw_val = (raw_bytes[0] << 8) | raw_bytes[1]
            elif len(raw_bytes) == 4:
                raw_val = (raw_bytes[0] << 24) | (raw_bytes[1] << 16) | (raw_bytes[2] << 8) | raw_bytes[3]
            else:
                # Explicit-length DIDs with exotic widths keep the generic
                # big-endian decode (declared by the DID author, not inferred).
                raw_val = int.from_bytes(raw_bytes[: self.length] if self.length else raw_bytes, byteorder="big")
            return (raw_val * self.scaling) + self.offset


@dataclass(slots=True, frozen=True)
class UdsDidResult:
    """Decoded result of an ISO 14229 UDS DID (Service 0x22) query."""

    did: int
    name: str
    raw_bytes: bytes
    value: Any
    unit: str
    timestamp_ns: int = field(default_factory=time.time_ns)
    is_valid: bool = True
    error_message: str | None = None

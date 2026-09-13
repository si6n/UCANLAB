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
    # REVIEW hardening: raw payloads above MAX_RAW_BYTES are cut and the
    # result is flagged (hex-bloat / memory DoS protection).
    truncated: bool = False
    # REVIEW 1-M5: ECU provenance — functional (0x7DF) requests are answered
    # by every compliant ECU from its own physical ID; without this field
    # the answer could not be attributed to the source ECU.
    source_rx_id: int | None = None


@dataclass(slots=True, frozen=True)
class ObdDtcResult:
    """Decoded result of an OBD-II DTC readback (Mode 03 / 07 / 0A).

    SAE J1979: Mode 03 = stored DTCs, Mode 07 = pending DTCs,
    Mode 0A = permanent (clear-resistant) DTCs. dtcs holds the decoded
    SAE J2012 5-character codes (e.g. "P0123") — never fabricated: only
    pairs physically present in the payload are decoded, and a payload
    shorter than the declared DTC count is flagged is_valid=False.
    """

    mode: int  # 0x03 | 0x07 | 0x0A
    dtc_count: int  # DTC count declared by the ECU in byte 1
    dtcs: tuple[str, ...]
    raw_bytes: bytes
    timestamp_ns: int = field(default_factory=time.time_ns)
    is_valid: bool = True
    error_message: str | None = None
    source_rx_id: int | None = None


def decode_dtc_pair(a: int, b: int) -> str:
    """Decode one SAE J2012 2-byte DTC into its 5-character text form.

    Byte A: bits 7..6 system letter (P/C/B/U), bits 5..4 second char,
    bits 3..0 third char (hex). Byte B: bits 7..4 fourth char, 3..0 fifth.
    E.g. (0x01, 0x23) -> "P0123", (0x52, 0x34) -> "C1234", (0xC1, 0x00) -> "U0100".
    """
    letters = "PCBU"
    letter = letters[(a >> 6) & 0x03]
    second = str((a >> 4) & 0x03)
    third = format(a & 0x0F, "X")
    fourth = format((b >> 4) & 0x0F, "X")
    fifth = format(b & 0x0F, "X")
    return f"{letter}{second}{third}{fourth}{fifth}"


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
    # REVIEW hardening: raw payloads above MAX_RAW_BYTES are cut and the
    # result is flagged (hex-bloat / memory DoS protection).
    truncated: bool = False


# REVIEW hardening: raw-payload ceiling shared by the DID/PID unknown-ID
# paths (an attacker reflecting megabytes would otherwise double memory
# via .hex() and land as "valid" telemetry).
MAX_RAW_BYTES: int = 256

# Fixed machine-readable decode-failure code (no str(exc) reflection —
# internal exception text must not reach telemetry/log as an oracle).
DECODE_FAILURE_CODE: str = "DID_TRUNCATED"

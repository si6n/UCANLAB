"""Universal CAN-Bus Diagnostic & Telemetry Platform - Canonical CanFrame Data Model.

Complies with ISO 11898-1:2015/2024, SAE J1939, and MASTER_PLAN.md Section 9.2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import ClassVar

# ISO 11898-1:2015 DLC to payload length mapping table
DLC_TO_LENGTH: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)

# Length to minimal valid DLC mapping lookup (tuple indexed by 0..64)
LENGTH_TO_DLC: tuple[int, ...] = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    9,
    9,
    9,
    10,
    10,
    10,
    10,
    11,
    11,
    11,
    11,
    12,
    12,
    12,
    12,
    13,
    13,
    13,
    13,
    13,
    13,
    13,
    13,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    14,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
    15,
)


def dlc_to_length(dlc: int) -> int:
    """Convert a 0..15 DLC code to the expected payload byte length."""
    if 0 <= dlc <= 15:
        return DLC_TO_LENGTH[dlc]
    raise ValueError(f"DLC must be between 0 and 15, got {dlc}")


def length_to_dlc(length: int) -> int:
    """Convert payload byte length (0..64) to the matching CAN-FD DLC code."""
    if 0 <= length <= 64:
        return LENGTH_TO_DLC[length]
    raise ValueError(f"Payload length must be between 0 and 64 bytes, got {length}")


def pad_payload(data: bytes, dlc: int, pad_byte: int = 0xCC) -> bytes:
    """Pad payload data up to the full expected DLC length with the standard padding byte."""
    expected_len = dlc_to_length(dlc)
    data_len = len(data)
    if data_len > expected_len:
        raise ValueError(f"Data length ({data_len}) exceeds DLC {dlc} capacity ({expected_len})")
    if data_len == expected_len:
        return data
    return data + bytes([pad_byte] * (expected_len - data_len))


def get_hardware_crc_type(is_fd: bool, payload_len: int) -> str:
    """Determine the hardware CRC type according to ISO 11898-1:2015."""
    if not is_fd:
        return "CRC-15"
    if payload_len <= 16:
        return "CRC-17"
    return "CRC-21"


@dataclass(slots=True, frozen=True)
class CanFrame:
    """Canonical multi-bus CAN/CAN-FD frame data contract.

    Matches MASTER_PLAN.md Section 9.2 with full 15-attribute specification.
    """

    channel_id: str
    arbitration_id: int
    dlc: int
    data: bytes
    is_extended: bool = False
    is_fd: bool = False
    brs: bool = False
    esi: bool = False
    direction: str = "rx"  # "rx" | "tx"
    timestamp_ns: int = field(default_factory=time.time_ns)
    hardware_timestamp_ns: int | None = None
    host_timestamp_ns: int | None = None
    sequence: int = 0
    error_state: str = "active"  # "active" | "passive" | "bus_off"
    source: str = "physical"  # "physical" | "replay" | "virtual" | "injected"

    VALID_DIRECTIONS: ClassVar[frozenset[str]] = frozenset({"rx", "tx"})
    VALID_ERROR_STATES: ClassVar[frozenset[str]] = frozenset({"active", "passive", "bus_off"})
    VALID_SOURCES: ClassVar[frozenset[str]] = frozenset({"physical", "replay", "virtual", "injected", "synthetic"})

    def __post_init__(self) -> None:
        """Validate all invariant invariants at instantiation time."""
        # Validate DLC code first
        if not (0 <= self.dlc <= 15):
            raise ValueError(f"Invalid DLC: {self.dlc} (must be 0..15)")

        # Validate arbitration ID range
        max_id = 0x1FFFFFFF if self.is_extended else 0x7FF
        if not (0 <= self.arbitration_id <= max_id):
            raise ValueError(
                f"Invalid arbitration_id: 0x{self.arbitration_id:X} (max: 0x{max_id:X}, extended={self.is_extended})"
            )

        # Validate Classic CAN restrictions
        if not self.is_fd and self.dlc > 8:
            raise ValueError(f"Classic CAN DLC cannot exceed 8, got {self.dlc}")

        # Validate data byte length against DLC (CORE-C-001: exact invariant).
        # Classic CAN (DLC 0..8): len(data) must equal the DLC exactly — a
        # frame claiming DLC=8 with 2 payload bytes is ambiguous downstream
        # (len(data) vs dlc disagree; pad via CanFrame.create()/padded_data).
        # CAN-FD DLC 9..15 encode capacities 12..64; the payload must fit
        # within the DLC's capacity (padding covers the remainder on wire).
        expected_len = DLC_TO_LENGTH[self.dlc]
        if self.is_fd and self.dlc >= 9:
            if not (0 < len(self.data) <= expected_len):
                raise ValueError(
                    f"CAN-FD data length ({len(self.data)}) exceeds DLC {self.dlc} capacity ({expected_len} bytes)"
                )
        elif len(self.data) != expected_len:
            raise ValueError(
                f"Data length ({len(self.data)}) does not match DLC {self.dlc} "
                f"(expected exactly {expected_len} bytes; use CanFrame.create() "
                "or padded_data for canonical construction)"
            )

        # Validate enumerated attributes
        if self.direction not in self.VALID_DIRECTIONS:
            raise ValueError(f"Invalid direction '{self.direction}', must be one of {self.VALID_DIRECTIONS}")

        if self.error_state not in self.VALID_ERROR_STATES:
            raise ValueError(f"Invalid error_state '{self.error_state}', must be one of {self.VALID_ERROR_STATES}")

        if self.source not in self.VALID_SOURCES:
            raise ValueError(f"Invalid source '{self.source}', must be one of {self.VALID_SOURCES}")

    @property
    def crc_type(self) -> str:
        """Return the hardware CRC type for this frame."""
        return get_hardware_crc_type(self.is_fd, len(self.data))

    @property
    def padded_data(self) -> bytes:
        """Return data padded up to full DLC expected bytes."""
        return pad_payload(self.data, self.dlc)

    @classmethod
    def create(
        cls,
        channel_id: str,
        arbitration_id: int,
        data: bytes,
        is_extended: bool | None = None,
        is_fd: bool = False,
        brs: bool = False,
        dlc: int | None = None,
        direction: str = "rx",
        timestamp_ns: int | None = None,
        source: str = "physical",
    ) -> CanFrame:
        """Convenience factory method with automatic parameter derivation.

        B2 (REVIEW): payloads above 8 bytes are only representable in CAN-FD.
        Fail closed with an explicit, actionable message instead of the
        cryptic "Classic CAN DLC cannot exceed 8" from __post_init__ — an
        accidental is_fd omission must never silently produce a valid-looking
        classic frame either.
        """
        if is_extended is None:
            is_extended = arbitration_id > 0x7FF

        if not is_fd and len(data) > 8:
            raise ValueError(
                f"Payload of {len(data)} bytes exceeds the 8-byte Classic CAN "
                "limit — pass is_fd=True for CAN-FD frames"
            )

        if dlc is None:
            dlc = length_to_dlc(len(data))

        ts = timestamp_ns if timestamp_ns is not None else time.time_ns()

        return cls(
            channel_id=channel_id,
            arbitration_id=arbitration_id,
            dlc=dlc,
            data=data,
            is_extended=is_extended,
            is_fd=is_fd,
            brs=brs,
            direction=direction,
            timestamp_ns=ts,
            source=source,
        )

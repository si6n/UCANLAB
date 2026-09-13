"""SAE J1939-71 MSB-Based Sentinel Range Bounds and Error Validator.

Complies with SAE J1939-71, SAE J1939-21, and MASTER_PLAN.md Section 4.3.
Provides:
1. SignalDefinition metadata dataclass
2. SignalQuality enum
3. DecodedSignal dataclass
4. J1939SentinelFilter & J1939SignalDecoder with 3-stage decoding pipeline:
   - Stage 1: Raw sentinel evaluation (VALID, ERROR, NOT_AVAILABLE, RESERVED, PARAMETER_SPECIFIC)
   - Stage 2: Two's complement signed integer conversion when is_signed=True
   - Stage 3: Linear physical scaling: physical_value = (converted_raw * scale) + offset
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SignalQuality(str, Enum):
    """Quality and validity classification of a decoded J1939 signal."""

    VALID = "VALID"
    PARAMETER_SPECIFIC = "PARAMETER_SPECIFIC"
    RESERVED = "RESERVED"
    ERROR = "ERROR"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(slots=True, frozen=True)
class SignalDefinition:
    """Metadata defining a SAE J1939-71 signal."""

    name: str
    spn: int
    start_bit: int
    length_bits: int
    byte_order: str = "little_endian"
    is_signed: bool = False
    scale: float = 1.0
    offset: float = 0.0
    min_val: float | None = None
    max_val: float | None = None
    unit: str = ""


@dataclass(slots=True, frozen=True)
class DecodedSignal:
    """Container for decoded J1939 signal result."""

    quality: SignalQuality
    raw_value: int
    physical_value: float | None


class J1939SentinelFilter:
    """Validator for MSB-encoded sentinel ranges and complete 3-stage signal decoding."""

    @classmethod
    def check_uint8(cls, raw_val: int) -> SignalQuality:
        """Evaluate 8-bit unsigned raw integer."""
        if not (0 <= raw_val <= 0xFF):
            return SignalQuality.ERROR
        if raw_val <= 0xFA:
            return SignalQuality.VALID
        if raw_val == 0xFB:
            return SignalQuality.PARAMETER_SPECIFIC
        if 0xFC <= raw_val <= 0xFD:
            return SignalQuality.RESERVED
        if raw_val == 0xFE:
            return SignalQuality.ERROR
        return SignalQuality.NOT_AVAILABLE

    @classmethod
    def check_uint16(cls, raw_val: int) -> SignalQuality:
        """Evaluate 16-bit unsigned raw integer."""
        if not (0 <= raw_val <= 0xFFFF):
            return SignalQuality.ERROR
        msb = (raw_val >> 8) & 0xFF
        if msb <= 0xFA:
            return SignalQuality.VALID
        if msb == 0xFB:
            return SignalQuality.PARAMETER_SPECIFIC
        if 0xFC <= msb <= 0xFD:
            return SignalQuality.RESERVED
        if msb == 0xFE:
            return SignalQuality.ERROR
        return SignalQuality.NOT_AVAILABLE

    @classmethod
    def check_uint24(cls, raw_val: int) -> SignalQuality:
        """Evaluate 24-bit unsigned raw integer."""
        if not (0 <= raw_val <= 0xFFFFFF):
            return SignalQuality.ERROR
        msb = (raw_val >> 16) & 0xFF
        if msb <= 0xFA:
            return SignalQuality.VALID
        if msb == 0xFB:
            return SignalQuality.PARAMETER_SPECIFIC
        if 0xFC <= msb <= 0xFD:
            return SignalQuality.RESERVED
        if msb == 0xFE:
            return SignalQuality.ERROR
        return SignalQuality.NOT_AVAILABLE

    @classmethod
    def check_uint32(cls, raw_val: int) -> SignalQuality:
        """Evaluate 32-bit unsigned raw integer."""
        if not (0 <= raw_val <= 0xFFFFFFFF):
            return SignalQuality.ERROR
        msb = (raw_val >> 24) & 0xFF
        if msb <= 0xFA:
            return SignalQuality.VALID
        if msb == 0xFB:
            return SignalQuality.PARAMETER_SPECIFIC
        if 0xFC <= msb <= 0xFD:
            return SignalQuality.RESERVED
        if msb == 0xFE:
            return SignalQuality.ERROR
        return SignalQuality.NOT_AVAILABLE

    @classmethod
    def check_discrete_2bit(cls, raw_val: int) -> SignalQuality:
        """Evaluate 2-bit discrete state."""
        val = raw_val & 0x03
        if val in (0b00, 0b01):
            return SignalQuality.VALID
        if val == 0b10:
            return SignalQuality.ERROR
        return SignalQuality.NOT_AVAILABLE

    @classmethod
    def check_nibble_4bit(cls, raw_val: int) -> SignalQuality:
        """Evaluate 4-bit discrete/nibble state."""
        val = raw_val & 0x0F
        if val <= 0x0D:
            return SignalQuality.VALID
        if val == 0x0E:
            return SignalQuality.ERROR
        return SignalQuality.NOT_AVAILABLE

    @classmethod
    def check_raw_value(cls, raw_val: int, length_bits: int, is_signed: bool = False) -> SignalQuality:
        """Evaluate raw integer against J1939 sentinel rules based on length and sign."""
        if length_bits <= 0:
            return SignalQuality.ERROR

        max_uint = (1 << length_bits) - 1
        if not (0 <= raw_val <= max_uint):
            return SignalQuality.ERROR

        if is_signed:
            # For signed signals, all-ones is NOT_AVAILABLE, all-ones minus 1 is ERROR
            error_sentinel = max_uint - 1
            if raw_val == max_uint:
                return SignalQuality.NOT_AVAILABLE
            if raw_val == error_sentinel:
                return SignalQuality.ERROR
            return SignalQuality.VALID

        # Unsigned signals:
        if length_bits == 1:
            return SignalQuality.VALID
        if length_bits == 2:
            return cls.check_discrete_2bit(raw_val)
        if length_bits == 4:
            return cls.check_nibble_4bit(raw_val)
        if length_bits == 8:
            return cls.check_uint8(raw_val)
        if length_bits == 16:
            return cls.check_uint16(raw_val)
        if length_bits == 24:
            return cls.check_uint24(raw_val)
        if length_bits == 32:
            return cls.check_uint32(raw_val)

        # Arbitrary bit length unsigned sentinel rules:
        error_sentinel = max_uint - 1
        if raw_val == max_uint:
            return SignalQuality.NOT_AVAILABLE
        if raw_val == error_sentinel:
            return SignalQuality.ERROR
        if length_bits >= 4 and raw_val >= (max_uint - 3):
            return SignalQuality.RESERVED
        return SignalQuality.VALID

    @classmethod
    def convert_to_signed(cls, raw_uint: int, length_bits: int) -> int:
        """Convert unsigned bitfield to signed two's complement integer."""
        if length_bits <= 0:
            return raw_uint
        sign_bit = 1 << (length_bits - 1)
        if raw_uint & sign_bit:
            return raw_uint - (1 << length_bits)
        return raw_uint

    @classmethod
    def extract_raw_bits(
        cls,
        payload: bytes,
        start_bit: int,
        length_bits: int,
        byte_order: str = "little_endian",
    ) -> int:
        """Extract raw unsigned integer bits from payload bytes according to endianness."""
        if length_bits <= 0:
            raise ValueError(f"Invalid length_bits: {length_bits}, must be > 0")
        if start_bit < 0:
            raise ValueError(f"Invalid start_bit: {start_bit}, must be >= 0")
        if not payload:
            raise ValueError("Payload cannot be empty")

        total_bits = len(payload) * 8
        if start_bit + length_bits > total_bits:
            raise ValueError(
                f"Signal requires {start_bit + length_bits} bits, but payload only contains {total_bits} bits"
            )

        if byte_order == "little_endian":
            payload_int = int.from_bytes(payload, byteorder="little", signed=False)
            mask = (1 << length_bits) - 1
            return (payload_int >> start_bit) & mask
        elif byte_order == "big_endian":
            payload_int = int.from_bytes(payload, byteorder="big", signed=False)
            shift = total_bits - start_bit - length_bits
            mask = (1 << length_bits) - 1
            return (payload_int >> shift) & mask
        else:
            raise ValueError(
                f"Unsupported byte_order: '{byte_order}'. Expected 'little_endian' or 'big_endian'"
            )

    @classmethod
    def decode_raw_value(cls, raw_val: int, sig_def: SignalDefinition) -> DecodedSignal:
        """Execute 3-stage J1939-71 decoding pipeline on raw integer:

        Stage 1: Raw sentinel check against SignalQuality.
        Stage 2: Two's complement signed integer conversion when is_signed=True.
        Stage 3: Linear physical scaling: physical_value = (converted_raw * scale) + offset.
        """
        # Stage 1: Sentinel evaluation
        quality = cls.check_raw_value(
            raw_val=raw_val,
            length_bits=sig_def.length_bits,
            is_signed=sig_def.is_signed,
        )

        # Stage 2: Signed conversion
        if sig_def.is_signed and quality == SignalQuality.VALID:
            raw_numeric = cls.convert_to_signed(raw_val, sig_def.length_bits)
        else:
            raw_numeric = raw_val

        # Stage 3: Physical scaling
        if quality == SignalQuality.VALID:
            physical_val = (raw_numeric * sig_def.scale) + sig_def.offset
        else:
            physical_val = None

        return DecodedSignal(
            quality=quality,
            raw_value=raw_numeric,
            physical_value=physical_val,
        )

    @classmethod
    def decode_signal(cls, payload: bytes, sig_def: SignalDefinition) -> DecodedSignal:
        """Extract raw bits from payload bytes and decode signal through 3-stage pipeline."""
        raw_val = cls.extract_raw_bits(
            payload=payload,
            start_bit=sig_def.start_bit,
            length_bits=sig_def.length_bits,
            byte_order=sig_def.byte_order,
        )
        return cls.decode_raw_value(raw_val, sig_def)


def is_value_plausible(
    value: float | int | None,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> bool:
    """Plausibility helper (REVIEW hardening).

    Returns True only when the value is a finite number inside the
    inclusive [min_value, max_value] window. None, NaN/Inf, and
    out-of-range physics all fail — a sentinel-masked or corrupt signal
    must never pass as plausible telemetry.
    """
    import math

    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(number):
        return False
    if min_value is not None and number < min_value:
        return False
    if max_value is not None and number > max_value:
        return False
    return True


# Alias and functional helpers for convenience and compatibility
J1939SignalDecoder = J1939SentinelFilter
decode_signal = J1939SentinelFilter.decode_signal
decode_raw_value = J1939SentinelFilter.decode_raw_value
decode_j1939_signal = J1939SentinelFilter.decode_raw_value

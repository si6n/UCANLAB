"""Universal CAN-Bus Diagnostic & Telemetry Platform - E2E Safety Profiles.

Defines standardized and OEM-specific End-to-End (E2E) protection profiles:
- AUTOSAR Profile 1 (Variants 1A, 1B, 1C with Polynomial 0x1D)
- AUTOSAR Profile 2 (16-entry Data ID list with Polynomial 0x2F)
- SAE J1850 CRC-8
- OEM Profiles:
  - Toyota (Modulo-256 Additive Checksum + 4/8-bit Rolling Counter)
  - VAG MQB (AUTOSAR CRC-8 0x2F + 16-bit Data ID Key + 4-bit Counter)
  - Volvo (8-bit Ones-Complement Sum + Rolling Counter)

Complies with ISO 26262 ASIL-B/D functional safety invariants and AUTOSAR CP R4.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Sequence

from src.safety.e2e.crc import (
    DEFAULT_CRC_INIT,
    DEFAULT_CRC_XOR,
    POLYNOMIAL_0X1D,
    calculate_crc8,
    calculate_crc8_0x1d,
    calculate_crc8_0x2f,
    calculate_crc8_sae_j1850,
)


@unique
class E2EProfileType(str, Enum):
    """Supported E2E Profile Types."""

    AUTOSAR_PROFILE_1 = "AUTOSAR_PROFILE_1"
    AUTOSAR_PROFILE_1A = "AUTOSAR_PROFILE_1A"
    AUTOSAR_PROFILE_1B = "AUTOSAR_PROFILE_1B"
    AUTOSAR_PROFILE_1C = "AUTOSAR_PROFILE_1C"
    AUTOSAR_PROFILE_2 = "AUTOSAR_PROFILE_2"
    SAE_J1850 = "SAE_J1850"
    TOYOTA = "TOYOTA"
    VAG_MQB = "VAG_MQB"
    VOLVO = "VOLVO"
    CUSTOM = "CUSTOM"


@unique
class E2EStatus(str, Enum):
    """E2E Verification Status Verdicts."""

    OK = "OK"
    REPEATED = "REPEATED"
    SOME_LOST = "SOME_LOST"
    WRONG_SEQUENCE = "WRONG_SEQUENCE"
    CRC_ERROR = "CRC_ERROR"
    INITIAL = "INITIAL"
    # H-3 (P1-10): payload shorter than the profile's offsets is a
    # malformed/runt frame — reported as a formal verdict instead of raising
    # ValueError out of the RX validation path.
    LENGTH_ERROR = "LENGTH_ERROR"


@dataclass(slots=True, frozen=True)
class E2EProfileConfig:
    """Configuration descriptor for E2E frame protection and verification."""

    profile_type: E2EProfileType
    crc_byte_offset: int = 0
    counter_byte_offset: int = 1
    counter_bit_mask: int = 0x0F
    counter_bit_shift: int = 0
    counter_modulo: int = 16
    max_delta_counter: int = 2
    data_id: int = 0
    data_id_mode: str = "1C"
    data_id_list: tuple[int, ...] | None = None
    include_can_id_in_crc: bool = False
    include_dlc_in_crc: bool = True
    custom_polynomial: int = POLYNOMIAL_0X1D
    custom_init: int = DEFAULT_CRC_INIT
    custom_final_xor: int = DEFAULT_CRC_XOR

    def __post_init__(self) -> None:
        """Validate profile configuration parameters."""
        if self.counter_modulo <= 0:
            raise ValueError(f"counter_modulo must be positive, got {self.counter_modulo}")
        if self.max_delta_counter <= 0:
            raise ValueError(f"max_delta_counter must be positive, got {self.max_delta_counter}")
        if self.crc_byte_offset < 0:
            raise ValueError(f"crc_byte_offset must be non-negative, got {self.crc_byte_offset}")
        if self.counter_byte_offset < 0:
            raise ValueError(f"counter_byte_offset must be non-negative, got {self.counter_byte_offset}")
        # M-4 (P1-10): classic-CAN E2E profiles must keep both offsets inside
        # the 8-byte payload — a profile with crc_byte_offset >= 8 silently
        # lengthened frames in the packager and then raised a raw ValueError
        # out of CanFrame.create instead of a configuration error.
        if max(self.crc_byte_offset, self.counter_byte_offset) >= 8:
            raise ValueError(
                f"E2E profile offsets exceed the 8-byte classic CAN payload "
                f"(crc_byte_offset={self.crc_byte_offset}, "
                f"counter_byte_offset={self.counter_byte_offset})"
            )
        # B14: the packager writes the counter first and the CRC second —
        # with both offsets equal the CRC silently overwrites the counter and
        # every receiver flags the stream as a sequence jump.
        if self.crc_byte_offset == self.counter_byte_offset:
            raise ValueError(
                f"crc_byte_offset ({self.crc_byte_offset}) must differ from "
                f"counter_byte_offset ({self.counter_byte_offset}) — equal offsets "
                f"would have the CRC overwrite the rolling counter"
            )

    @classmethod
    def create_autosar_profile_1(
        cls,
        data_id: int,
        variant: str = "1C",
        counter_byte_offset: int = 1,
        crc_byte_offset: int = 0,
        counter_bit_mask: int = 0x0F,
        counter_bit_shift: int = 0,
        max_delta_counter: int = 2,
    ) -> E2EProfileConfig:
        """Factory for AUTOSAR Profile 1 (1A, 1B, 1C)."""
        variant_upper = variant.upper()
        if variant_upper == "1A":
            p_type = E2EProfileType.AUTOSAR_PROFILE_1A
            counter_mod = 15
        elif variant_upper == "1B":
            p_type = E2EProfileType.AUTOSAR_PROFILE_1B
            counter_mod = 16
        else:
            p_type = E2EProfileType.AUTOSAR_PROFILE_1C
            counter_mod = 16

        return cls(
            profile_type=p_type,
            crc_byte_offset=crc_byte_offset,
            counter_byte_offset=counter_byte_offset,
            counter_bit_mask=counter_bit_mask,
            counter_bit_shift=counter_bit_shift,
            counter_modulo=counter_mod,
            max_delta_counter=max_delta_counter,
            data_id=data_id & 0xFFFF,
            data_id_mode=variant_upper,
        )

    @classmethod
    def create_autosar_profile_2(
        cls,
        data_id_list: Sequence[int],
        counter_byte_offset: int = 1,
        crc_byte_offset: int = 0,
        counter_bit_mask: int = 0x0F,
        counter_bit_shift: int = 0,
        max_delta_counter: int = 2,
    ) -> E2EProfileConfig:
        """Factory for AUTOSAR Profile 2 (16-entry Data ID list)."""
        if len(data_id_list) != 16:
            raise ValueError(f"AUTOSAR Profile 2 requires exactly 16 Data IDs, got {len(data_id_list)}")

        return cls(
            profile_type=E2EProfileType.AUTOSAR_PROFILE_2,
            crc_byte_offset=crc_byte_offset,
            counter_byte_offset=counter_byte_offset,
            counter_bit_mask=counter_bit_mask,
            counter_bit_shift=counter_bit_shift,
            counter_modulo=16,
            max_delta_counter=max_delta_counter,
            data_id_list=tuple(d & 0xFF for d in data_id_list),
        )

    @classmethod
    def create_sae_j1850(
        cls,
        crc_byte_offset: int = 0,
        counter_byte_offset: int = 1,
        counter_bit_mask: int = 0x0F,
        counter_bit_shift: int = 0,
        counter_modulo: int = 16,
        max_delta_counter: int = 2,
    ) -> E2EProfileConfig:
        """Factory for SAE J1850 CRC-8 profile."""
        return cls(
            profile_type=E2EProfileType.SAE_J1850,
            crc_byte_offset=crc_byte_offset,
            counter_byte_offset=counter_byte_offset,
            counter_bit_mask=counter_bit_mask,
            counter_bit_shift=counter_bit_shift,
            counter_modulo=counter_modulo,
            max_delta_counter=max_delta_counter,
        )

    @classmethod
    def create_toyota(
        cls,
        crc_byte_offset: int = 7,
        counter_byte_offset: int = 6,
        counter_bit_mask: int = 0x0F,
        counter_bit_shift: int = 0,
        counter_modulo: int = 16,
        include_can_id: bool = True,
        include_dlc: bool = True,
        max_delta_counter: int = 2,
    ) -> E2EProfileConfig:
        """Factory for Toyota modulo-256 additive checksum profile."""
        return cls(
            profile_type=E2EProfileType.TOYOTA,
            crc_byte_offset=crc_byte_offset,
            counter_byte_offset=counter_byte_offset,
            counter_bit_mask=counter_bit_mask,
            counter_bit_shift=counter_bit_shift,
            counter_modulo=counter_modulo,
            include_can_id_in_crc=include_can_id,
            include_dlc_in_crc=include_dlc,
            max_delta_counter=max_delta_counter,
        )

    @classmethod
    def create_vag_mqb(
        cls,
        data_id: int,
        crc_byte_offset: int = 0,
        counter_byte_offset: int = 1,
        counter_bit_mask: int = 0x0F,
        counter_bit_shift: int = 0,
        max_delta_counter: int = 2,
    ) -> E2EProfileConfig:
        """Factory for VAG MQB CRC-8 0x2F profile."""
        return cls(
            profile_type=E2EProfileType.VAG_MQB,
            crc_byte_offset=crc_byte_offset,
            counter_byte_offset=counter_byte_offset,
            counter_bit_mask=counter_bit_mask,
            counter_bit_shift=counter_bit_shift,
            counter_modulo=16,
            max_delta_counter=max_delta_counter,
            data_id=data_id & 0xFFFF,
        )

    @classmethod
    def create_volvo(
        cls,
        crc_byte_offset: int = 7,
        counter_byte_offset: int = 1,
        counter_bit_mask: int = 0x0F,
        counter_bit_shift: int = 0,
        counter_modulo: int = 16,
        max_delta_counter: int = 2,
    ) -> E2EProfileConfig:
        """Factory for Volvo ones-complement checksum profile."""
        return cls(
            profile_type=E2EProfileType.VOLVO,
            crc_byte_offset=crc_byte_offset,
            counter_byte_offset=counter_byte_offset,
            counter_bit_mask=counter_bit_mask,
            counter_bit_shift=counter_bit_shift,
            counter_modulo=counter_modulo,
            max_delta_counter=max_delta_counter,
        )


def extract_counter(data: bytes | bytearray | Sequence[int], config: E2EProfileConfig) -> int:
    """Extract rolling sequence counter value from payload according to profile configuration."""
    if len(data) <= config.counter_byte_offset:
        raise ValueError(
            f"Payload length ({len(data)}) is too short for counter offset {config.counter_byte_offset}"
        )
    raw_byte = data[config.counter_byte_offset] & 0xFF
    return (raw_byte & config.counter_bit_mask) >> config.counter_bit_shift


def inject_counter(data: bytearray, counter: int, config: E2EProfileConfig) -> None:
    """Inject rolling sequence counter value into mutable payload."""
    if len(data) <= config.counter_byte_offset:
        raise ValueError(
            f"Payload length ({len(data)}) is too short for counter offset {config.counter_byte_offset}"
        )
    bounded_counter = counter % config.counter_modulo
    current_byte = data[config.counter_byte_offset]
    cleared = current_byte & (~config.counter_bit_mask & 0xFF)
    shifted = (bounded_counter << config.counter_bit_shift) & config.counter_bit_mask
    data[config.counter_byte_offset] = cleared | shifted


def extract_crc(data: bytes | bytearray | Sequence[int], config: E2EProfileConfig) -> int:
    """Extract CRC/checksum byte from payload."""
    if len(data) <= config.crc_byte_offset:
        raise ValueError(
            f"Payload length ({len(data)}) is too short for CRC offset {config.crc_byte_offset}"
        )
    return data[config.crc_byte_offset] & 0xFF


def inject_crc(data: bytearray, crc: int, config: E2EProfileConfig) -> None:
    """Inject CRC/checksum byte into mutable payload."""
    if len(data) <= config.crc_byte_offset:
        raise ValueError(
            f"Payload length ({len(data)}) is too short for CRC offset {config.crc_byte_offset}"
        )
    data[config.crc_byte_offset] = crc & 0xFF


def compute_checksum(
    data: bytes | bytearray | Sequence[int],
    config: E2EProfileConfig,
    arbitration_id: int = 0,
    dlc: int | None = None,
) -> int:
    """Compute expected CRC / Checksum byte for the given payload according to profile specifications."""
    data_len = len(data)
    if data_len == 0:
        raise ValueError("Cannot compute checksum on empty payload")

    crc_offset = config.crc_byte_offset
    # Protected data bytes excluding the CRC slot itself
    protected_data = bytes(data[i] for i in range(data_len) if i != crc_offset)

    p_type = config.profile_type

    if p_type in (
        E2EProfileType.AUTOSAR_PROFILE_1,
        E2EProfileType.AUTOSAR_PROFILE_1A,
        E2EProfileType.AUTOSAR_PROFILE_1B,
        E2EProfileType.AUTOSAR_PROFILE_1C,
    ):
        data_id = config.data_id
        data_id_low = data_id & 0xFF
        data_id_high = (data_id >> 8) & 0xFF
        mode = config.data_id_mode.upper() if config.data_id_mode else "1C"

        if mode == "1A":
            calc_buf = protected_data + bytes([data_id_low])
            return calculate_crc8_0x1d(calc_buf)
        elif mode == "1B":
            calc_buf = bytes([data_id_low]) + protected_data
            crc = calculate_crc8_0x1d(calc_buf)
            return (crc ^ data_id_high) & 0xFF
        else:  # "1C" default
            calc_buf = bytes([data_id_low, data_id_high]) + protected_data
            return calculate_crc8_0x1d(calc_buf)

    elif p_type == E2EProfileType.AUTOSAR_PROFILE_2:
        if not config.data_id_list or len(config.data_id_list) != 16:
            raise ValueError("AUTOSAR Profile 2 requires 16-entry Data ID list")
        counter = extract_counter(data, config)
        selected_data_id = config.data_id_list[counter % 16] & 0xFF
        calc_buf = protected_data + bytes([selected_data_id])
        return calculate_crc8_0x2f(calc_buf)

    elif p_type == E2EProfileType.SAE_J1850:
        return calculate_crc8_sae_j1850(protected_data)

    elif p_type == E2EProfileType.TOYOTA:
        total = sum(protected_data)
        if config.include_can_id_in_crc:
            total += (arbitration_id >> 8) & 0xFF
            total += arbitration_id & 0xFF
        if config.include_dlc_in_crc:
            effective_dlc = dlc if dlc is not None else data_len
            total += effective_dlc & 0xFF
        return total & 0xFF

    elif p_type == E2EProfileType.VAG_MQB:
        data_id_bytes = config.data_id.to_bytes(2, byteorder="little")
        calc_buf = protected_data + data_id_bytes
        return calculate_crc8_0x2f(calc_buf)

    elif p_type == E2EProfileType.VOLVO:
        total = sum(protected_data) & 0xFF
        return (~total) & 0xFF

    elif p_type == E2EProfileType.CUSTOM:
        return calculate_crc8(
            protected_data,
            poly=config.custom_polynomial,
            init_val=config.custom_init,
            final_xor=config.custom_final_xor,
        )

    else:
        raise NotImplementedError(f"Unsupported E2E profile: {p_type}")

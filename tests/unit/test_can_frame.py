"""Unit and Property-Based tests for CanFrame and DLC conversion utilities."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.core.models.can_frame import (
    DLC_TO_LENGTH,
    CanFrame,
    dlc_to_length,
    get_hardware_crc_type,
    length_to_dlc,
)


def test_classic_can_frame_creation() -> None:
    frame = CanFrame.create(
        channel_id="engine0",
        arbitration_id=0x18FEEE00,
        data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
    )
    assert frame.channel_id == "engine0"
    assert frame.arbitration_id == 0x18FEEE00
    assert frame.is_extended is True
    assert frame.is_fd is False
    assert frame.dlc == 8
    assert len(frame.data) == 8
    assert frame.direction == "rx"
    assert frame.crc_type == "CRC-15"
    assert frame.padded_data == b"\x01\x02\x03\x04\x05\x06\x07\x08"


def test_standard_11bit_frame_creation() -> None:
    frame = CanFrame.create(
        channel_id="obd0",
        arbitration_id=0x7DF,
        data=b"\x02\x01\x05\x00\x00\x00\x00\x00",
    )
    assert frame.arbitration_id == 0x7DF
    assert frame.is_extended is False
    assert frame.dlc == 8
    assert frame.crc_type == "CRC-15"


def test_can_fd_frame_creation_and_padding() -> None:
    # 20-byte payload requires DLC 11 and CRC-21
    raw_data = bytes(range(20))
    frame = CanFrame.create(
        channel_id="canfd0",
        arbitration_id=0x123,
        data=raw_data,
        is_fd=True,
        brs=True,
    )
    assert frame.is_fd is True
    assert frame.brs is True
    assert frame.dlc == 11
    assert len(frame.data) == 20
    assert frame.crc_type == "CRC-21"

    # 12-byte payload requires DLC 9 and CRC-17
    frame_12 = CanFrame.create(
        channel_id="canfd0",
        arbitration_id=0x123,
        data=bytes(range(10)),  # 10 bytes -> DLC 9 (capacity 12)
        is_fd=True,
    )
    assert frame_12.dlc == 9
    assert len(frame_12.data) == 10
    assert frame_12.crc_type == "CRC-17"
    assert len(frame_12.padded_data) == 12
    assert frame_12.padded_data[10:] == b"\xcc\xcc"


def test_invalid_arbitration_id() -> None:
    # Standard ID exceeds 11-bit
    with pytest.raises(ValueError, match="Invalid arbitration_id"):
        CanFrame(
            channel_id="c0",
            arbitration_id=0x800,  # 0x800 > 0x7FF
            dlc=0,
            data=b"",
            is_extended=False,
        )

    # Extended ID exceeds 29-bit
    with pytest.raises(ValueError, match="Invalid arbitration_id"):
        CanFrame(
            channel_id="c0",
            arbitration_id=0x20000000,  # > 0x1FFFFFFF
            dlc=0,
            data=b"",
            is_extended=True,
        )


def test_classic_can_dlc_limit() -> None:
    # Classic CAN with DLC > 8 must raise ValueError
    with pytest.raises(ValueError, match="Classic CAN DLC cannot exceed 8"):
        CanFrame(
            channel_id="c0",
            arbitration_id=0x100,
            dlc=9,
            data=bytes(12),
            is_fd=False,
        )


def test_create_rejects_gt8_byte_payload_without_fd_with_actionable_error() -> None:
    # B2 (REVIEW): CanFrame.create() must fail closed with an explicit,
    # actionable message when a >8-byte payload arrives without is_fd=True —
    # never a cryptic DLC-capacity error and never a silent classic frame.
    with pytest.raises(ValueError, match="is_fd=True"):
        CanFrame.create(
            channel_id="c0",
            arbitration_id=0x123,
            data=bytes(9),
        )
    with pytest.raises(ValueError, match="8-byte Classic CAN limit"):
        CanFrame.create(
            channel_id="c0",
            arbitration_id=0x123,
            data=bytes(64),
        )
    # The same payload with the FD flag is valid and derives DLC 15.
    fd_frame = CanFrame.create(
        channel_id="c0",
        arbitration_id=0x123,
        data=bytes(64),
        is_fd=True,
    )
    assert fd_frame.is_fd is True
    assert fd_frame.dlc == 15


def test_create_exactly_8_bytes_classic_still_valid() -> None:
    frame = CanFrame.create(
        channel_id="c0",
        arbitration_id=0x123,
        data=bytes(8),
    )
    assert frame.is_fd is False
    assert frame.dlc == 8


def test_dlc_conversion_tables() -> None:
    for dlc_val in range(16):
        expected_len = DLC_TO_LENGTH[dlc_val]
        assert dlc_to_length(dlc_val) == expected_len

    with pytest.raises(ValueError):
        dlc_to_length(16)

    assert length_to_dlc(0) == 0
    assert length_to_dlc(8) == 8
    assert length_to_dlc(12) == 9
    assert length_to_dlc(16) == 10
    assert length_to_dlc(20) == 11
    assert length_to_dlc(24) == 12
    assert length_to_dlc(32) == 13
    assert length_to_dlc(48) == 14
    assert length_to_dlc(64) == 15


def test_crc_calculation() -> None:
    assert get_hardware_crc_type(is_fd=False, payload_len=8) == "CRC-15"
    assert get_hardware_crc_type(is_fd=True, payload_len=8) == "CRC-17"
    assert get_hardware_crc_type(is_fd=True, payload_len=16) == "CRC-17"
    assert get_hardware_crc_type(is_fd=True, payload_len=20) == "CRC-21"
    assert get_hardware_crc_type(is_fd=True, payload_len=64) == "CRC-21"


# --- Hypothesis Property-Based Tests ---


@given(
    st.integers(min_value=0, max_value=0x7FF),
    st.binary(min_size=0, max_size=8),
)
def test_hypothesis_standard_frames(arb_id: int, payload: bytes) -> None:
    frame = CanFrame.create(
        channel_id="test",
        arbitration_id=arb_id,
        data=payload,
    )
    assert frame.arbitration_id == arb_id
    assert frame.is_extended is False
    assert frame.is_fd is False
    assert len(frame.data) <= 8
    assert frame.dlc <= 8


@given(
    st.integers(min_value=0x800, max_value=0x1FFFFFFF),
    st.binary(min_size=0, max_size=64),
)
def test_hypothesis_can_fd_frames(arb_id: int, payload: bytes) -> None:
    frame = CanFrame.create(
        channel_id="test_fd",
        arbitration_id=arb_id,
        data=payload,
        is_fd=True,
    )
    assert frame.arbitration_id == arb_id
    assert frame.is_extended is True
    assert frame.is_fd is True
    assert len(frame.padded_data) == dlc_to_length(frame.dlc)

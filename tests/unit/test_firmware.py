"""Unit tests for Firmware Parser (Intel HEX and Motorola S-Record)."""

from __future__ import annotations

import pytest

from src.core.errors import ProtocolError
from src.protocols.uds.firmware import (
    FirmwareContainer,
    IntelHexParser,
    MemorySegment,
    SRecordParser,
    load_firmware,
)


def _make_ihex_line(data: bytes, address: int = 0, record_type: int = 0) -> str:
    length = len(data)
    addr_hi = (address >> 8) & 0xFF
    addr_lo = address & 0xFF
    raw = bytes([length, addr_hi, addr_lo, record_type]) + data
    cc = ((~sum(raw) + 1) & 0xFF)
    return f":{raw.hex().upper()}{cc:02X}\n"


def _make_srec_line(rec_type: str, address: int, data: bytes) -> str:
    addr_len = 2 if rec_type in ("0", "1", "5", "9") else (3 if rec_type in ("2", "8") else 4)
    addr_bytes = address.to_bytes(addr_len, byteorder="big")
    count = len(addr_bytes) + len(data) + 1
    raw = bytes([count]) + addr_bytes + data
    cc = ((~sum(raw)) & 0xFF)
    return f"S{rec_type}{raw.hex().upper()}{cc:02X}\n"


def test_intel_hex_parser_happy_path() -> None:
    # Extended Linear Address: Upper 16 bits = 0x0800
    ext_line = _make_ihex_line(b"\x08\x00", address=0x0000, record_type=4)
    rec1 = _make_ihex_line(b"\x11\x22\x33\x44", address=0x0000, record_type=0)
    rec2 = _make_ihex_line(b"\x55\x66\x77\x88", address=0x0004, record_type=0)
    eof = _make_ihex_line(b"", address=0x0000, record_type=1)

    hex_data = ext_line + rec1 + rec2 + eof

    container = IntelHexParser.parse_string(hex_data)
    assert container.file_format == "intel_hex"
    assert len(container.segments) == 1
    seg = container.segments[0]
    assert seg.address == 0x08000000
    assert seg.data == b"\x11\x22\x33\x44\x55\x66\x77\x88"
    assert container.total_bytes == 8


def test_intel_hex_parser_checksum_mismatch() -> None:
    rec1 = _make_ihex_line(b"\x11\x22\x33\x44", address=0x0000, record_type=0)
    # Corrupt checksum
    corrupt = rec1[:-3] + "00\n"
    with pytest.raises(ProtocolError, match="checksum mismatch"):
        IntelHexParser.parse_string(corrupt)


def test_intel_hex_parser_overlap_detected() -> None:
    rec1 = _make_ihex_line(b"\x11\x22\x33\x44", address=0x0000, record_type=0)
    rec2 = _make_ihex_line(b"\x55\x66\x77\x88", address=0x0002, record_type=0)
    eof = _make_ihex_line(b"", address=0x0000, record_type=1)

    hex_data = rec1 + rec2 + eof
    with pytest.raises(ProtocolError, match="overlap detected"):
        IntelHexParser.parse_string(hex_data)


def test_s_record_parser_happy_path() -> None:
    s0 = _make_srec_line("0", 0x0000, b"HDR")
    s3 = _make_srec_line("3", 0x08000000, b"\x11\x22\x33\x44")
    s5 = _make_srec_line("5", 1, b"")
    s7 = _make_srec_line("7", 0x08000000, b"")

    srec_data = s0 + s3 + s5 + s7

    container = SRecordParser.parse_string(srec_data)
    assert container.file_format == "s_record"
    assert len(container.segments) == 1
    assert container.segments[0].address == 0x08000000
    assert container.segments[0].data == b"\x11\x22\x33\x44"
    assert container.entry_point == 0x08000000


def test_s_record_checksum_error() -> None:
    s3 = _make_srec_line("3", 0x08000000, b"\x11\x22\x33\x44")
    corrupt = s3[:-3] + "00\n"
    with pytest.raises(ProtocolError, match="checksum mismatch"):
        SRecordParser.parse_string(corrupt)


def test_firmware_container_gap_fill_and_vector_table() -> None:
    seg1 = MemorySegment(address=0x08000000, data=b"\x00\x10\x00\x20\x09\x00\x00\x08")  # SP=0x20001000, Reset=0x08000009 (Thumb bit 1)
    seg2 = MemorySegment(address=0x08000010, data=b"\xAA\xBB\xCC\xDD")

    container = FirmwareContainer(segments=[seg1, seg2])
    base_addr, continuous = container.get_continuous_binary(fill_byte=0xFF)
    assert base_addr == 0x08000000
    assert len(continuous) == 20
    assert continuous[8:16] == b"\xFF" * 8
    assert continuous[16:] == b"\xAA\xBB\xCC\xDD"

    # Vector table valid
    assert container.validate_vector_table(is_arm_cortex=True) is True


def test_load_firmware_auto_detect() -> None:
    hex_str = _make_ihex_line(b"\x11\x22\x33\x44", address=0x0000) + _make_ihex_line(b"", address=0x0000, record_type=1)
    c_hex = load_firmware(hex_str)
    assert c_hex.file_format == "intel_hex"

    srec_str = _make_srec_line("3", 0x08000000, b"\x11\x22\x33\x44")
    c_srec = load_firmware(srec_str)
    assert c_srec.file_format == "s_record"

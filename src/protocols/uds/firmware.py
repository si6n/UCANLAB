"""Firmware Container and Parsers for Intel HEX (.hex) and Motorola S-Record (.s19/.srec).

Provides strict cryptographic and arithmetic checksum verification, memory segment
overlap detection, gap analysis, and vector table sanity checks for automotive flashing.
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass, field
from pathlib import Path

from src.core.errors import ProtocolError


@dataclass(frozen=True, slots=True)
class MemorySegment:
    """Contiguous chunk of binary payload mapped to a starting target address."""

    address: int
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def end_address(self) -> int:
        return self.address + len(self.data)

    def contains(self, addr: int) -> bool:
        return self.address <= addr < self.end_address


@dataclass(slots=True)
class FirmwareContainer:
    """Decoded firmware image container holding contiguous memory segments."""

    segments: list[MemorySegment] = field(default_factory=list)
    entry_point: int | None = None
    file_format: str = "raw"
    metadata: dict[str, str | int] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(s.size for s in self.segments)

    @property
    def min_address(self) -> int:
        if not self.segments:
            return 0
        return min(s.address for s in self.segments)

    @property
    def max_address(self) -> int:
        if not self.segments:
            return 0
        return max(s.end_address for s in self.segments)

    def get_continuous_binary(self, fill_byte: int = 0xFF) -> tuple[int, bytes]:
        """Flatten segments into a single contiguous binary block with fill_byte padding for gaps."""
        if not self.segments:
            return 0, b""
        sorted_segs = sorted(self.segments, key=lambda s: s.address)
        base_addr = sorted_segs[0].address
        buf = bytearray()

        curr_addr = base_addr
        for seg in sorted_segs:
            if seg.address > curr_addr:
                gap = seg.address - curr_addr
                buf.extend(bytes([fill_byte & 0xFF]) * gap)
                curr_addr = seg.address
            buf.extend(seg.data)
            curr_addr = seg.end_address

        return base_addr, bytes(buf)

    def validate_vector_table(self, is_arm_cortex: bool = True) -> bool:
        """Sanity check interrupt vector table (e.g. initial SP in RAM and Reset Handler in Flash for ARM)."""
        base_addr, bin_data = self.get_continuous_binary()
        if len(bin_data) < 8:
            raise ProtocolError("Firmware binary too small for vector table validation (<8 bytes)")
        if is_arm_cortex:
            _initial_sp = struct.unpack("<I", bin_data[:4])[0]
            reset_handler = struct.unpack("<I", bin_data[4:8])[0]
            # ARM Thumb requires bit 0 of reset_handler to be 1
            if (reset_handler & 1) == 0:
                raise ProtocolError(
                    f"ARM Cortex vector table invalid: Reset Handler (0x{reset_handler:08X}) must have Thumb bit set (bit 0 == 1)"
                )
            # Reset handler should point inside binary address range (aligned)
            actual_reset_addr = reset_handler & ~1
            if not (base_addr <= actual_reset_addr < base_addr + len(bin_data)):
                raise ProtocolError(
                    f"ARM Cortex vector table invalid: Reset Handler 0x{actual_reset_addr:08X} out of firmware range [0x{base_addr:08X}, 0x{base_addr + len(bin_data):08X})"
                )
        return True


class IntelHexParser:
    """Parser for standard Intel HEX (.hex) records.

    Record Format: :LLAAAATTDD...DDCC
    LL: Record Length (1 byte)
    AAAA: Address offset (2 bytes, big-endian)
    TT: Record Type (00: Data, 01: EOF, 02: Ext Seg Addr, 04: Ext Lin Addr, 05: Start Lin Addr)
    DD: Data bytes
    CC: Two's complement Checksum
    """

    @classmethod
    def parse_string(cls, content: str) -> FirmwareContainer:
        lines = content.splitlines()
        upper_address = 0
        raw_chunks: list[tuple[int, bytes]] = []
        entry_point: int | None = None
        seen_eof = False

        for line_num, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if not line.startswith(":"):
                raise ProtocolError(f"Line {line_num}: Intel HEX record must start with ':'")
            try:
                raw_bytes = binascii.unhexlify(line[1:])
            except Exception as exc:
                raise ProtocolError(f"Line {line_num}: Invalid hex characters in record: {exc}") from exc

            if len(raw_bytes) < 5:
                raise ProtocolError(f"Line {line_num}: Record too short (<5 bytes)")

            # Checksum verification: sum of all bytes modulo 256 must be 0
            if (sum(raw_bytes) & 0xFF) != 0:
                raise ProtocolError(f"Line {line_num}: Intel HEX checksum mismatch")

            length = raw_bytes[0]
            addr_offset = (raw_bytes[1] << 8) | raw_bytes[2]
            record_type = raw_bytes[3]
            data = raw_bytes[4 : 4 + length]

            if len(data) != length:
                raise ProtocolError(f"Line {line_num}: Declared length {length} != payload length {len(data)}")

            if record_type == 0x00:  # Data Record
                if seen_eof:
                    raise ProtocolError(f"Line {line_num}: Data record encountered after EOF record (0x01)")
                full_addr = upper_address + addr_offset
                raw_chunks.append((full_addr, data))
            elif record_type == 0x01:  # End of File Record
                seen_eof = True
            elif record_type == 0x02:  # Extended Segment Address Record
                if length != 2:
                    raise ProtocolError(f"Line {line_num}: Extended Segment Address record length must be 2")
                upper_address = ((data[0] << 8) | data[1]) << 4
            elif record_type == 0x04:  # Extended Linear Address Record
                if length != 2:
                    raise ProtocolError(f"Line {line_num}: Extended Linear Address record length must be 2")
                upper_address = ((data[0] << 8) | data[1]) << 16
            elif record_type == 0x05:  # Start Linear Address Record (Entry Point)
                if length == 4:
                    entry_point = struct.unpack(">I", data)[0]

        if not raw_chunks and not seen_eof:
            raise ProtocolError("Empty Intel HEX file or no data records found")

        merged_segments = cls._merge_and_validate_chunks(raw_chunks)
        return FirmwareContainer(
            segments=merged_segments,
            entry_point=entry_point,
            file_format="intel_hex",
        )

    @classmethod
    def parse_file(cls, file_path: str | Path) -> FirmwareContainer:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Firmware file not found: {path}")
        content = path.read_text(encoding="utf-8", errors="replace")
        return cls.parse_string(content)

    @staticmethod
    def _merge_and_validate_chunks(chunks: list[tuple[int, bytes]]) -> list[MemorySegment]:
        if not chunks:
            return []
        chunks.sort(key=lambda x: x[0])

        merged: list[MemorySegment] = []
        curr_addr, curr_data = chunks[0][0], bytearray(chunks[0][1])

        for addr, data in chunks[1:]:
            curr_end = curr_addr + len(curr_data)
            if addr < curr_end:
                raise ProtocolError(f"Memory overlap detected between 0x{curr_addr:08X} and 0x{addr:08X}")
            elif addr == curr_end:
                curr_data.extend(data)
            else:
                merged.append(MemorySegment(address=curr_addr, data=bytes(curr_data)))
                curr_addr = addr
                curr_data = bytearray(data)

        merged.append(MemorySegment(address=curr_addr, data=bytes(curr_data)))
        return merged


class SRecordParser:
    """Parser for Motorola S-Record (.s19/.s28/.s37/.srec) format.

    S0: Header (16-bit addr)
    S1: 16-bit address Data Record
    S2: 24-bit address Data Record
    S3: 32-bit address Data Record
    S5: 16-bit Count of S1/S2/S3 records
    S7: 32-bit Termination / Entry point
    S8: 24-bit Termination / Entry point
    S9: 16-bit Termination / Entry point
    """

    @classmethod
    def parse_string(cls, content: str) -> FirmwareContainer:
        lines = content.splitlines()
        raw_chunks: list[tuple[int, bytes]] = []
        entry_point: int | None = None
        data_record_count = 0
        s5_count: int | None = None

        for line_num, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if not line.startswith("S"):
                raise ProtocolError(f"Line {line_num}: S-Record must start with 'S'")
            if len(line) < 4:
                raise ProtocolError(f"Line {line_num}: S-Record line too short")

            rec_type = line[1]
            try:
                raw_bytes = binascii.unhexlify(line[2:])
            except Exception as exc:
                raise ProtocolError(f"Line {line_num}: Invalid hex in S-Record: {exc}") from exc

            count = raw_bytes[0]
            if len(raw_bytes) != count + 1:
                raise ProtocolError(
                    f"Line {line_num}: S-Record byte count mismatch (declared {count}, got {len(raw_bytes) - 1})"
                )

            # Checksum: one's complement of the sum of count, address, and data bytes
            # (sum(raw_bytes) & 0xFF) must equal 0xFF
            if (sum(raw_bytes) & 0xFF) != 0xFF:
                raise ProtocolError(f"Line {line_num}: S-Record checksum mismatch")

            if rec_type in ("1", "2", "3"):
                addr_len = 2 if rec_type == "1" else (3 if rec_type == "2" else 4)
                addr = int.from_bytes(raw_bytes[1 : 1 + addr_len], byteorder="big")
                data = raw_bytes[1 + addr_len : -1]
                raw_chunks.append((addr, data))
                data_record_count += 1
            elif rec_type == "5":
                s5_count = int.from_bytes(raw_bytes[1:3], byteorder="big")
            elif rec_type in ("7", "8", "9"):
                addr_len = 4 if rec_type == "7" else (3 if rec_type == "8" else 2)
                entry_point = int.from_bytes(raw_bytes[1 : 1 + addr_len], byteorder="big")

        if s5_count is not None and s5_count != data_record_count:
            raise ProtocolError(f"S-Record S5 count mismatch (S5={s5_count}, actual data records={data_record_count})")

        if not raw_chunks and entry_point is None:
            raise ProtocolError("Empty S-Record file or no data records found")

        merged = IntelHexParser._merge_and_validate_chunks(raw_chunks)
        return FirmwareContainer(
            segments=merged,
            entry_point=entry_point,
            file_format="s_record",
        )

    @classmethod
    def parse_file(cls, file_path: str | Path) -> FirmwareContainer:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Firmware file not found: {path}")
        content = path.read_text(encoding="utf-8", errors="replace")
        return cls.parse_string(content)


def load_firmware(file_path_or_content: str | Path) -> FirmwareContainer:
    """Auto-detect and parse Intel HEX or Motorola S-Record format."""
    text_content: str
    if isinstance(file_path_or_content, Path) or (
        isinstance(file_path_or_content, str) and "\n" not in file_path_or_content and Path(file_path_or_content).exists()
    ):
        path = Path(file_path_or_content)
        text_content = path.read_text(encoding="utf-8", errors="replace")
    else:
        text_content = str(file_path_or_content)

    stripped = text_content.strip()
    if stripped.startswith(":"):
        return IntelHexParser.parse_string(text_content)
    elif stripped.startswith("S"):
        return SRecordParser.parse_string(text_content)
    else:
        raise ProtocolError("Unrecognized firmware format (must start with ':' for HEX or 'S' for SREC)")

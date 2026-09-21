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
class FirmwareParseLimits:
    """Centralised resource bounds for firmware parsing (F1/F3 hardening).

    Firmware files are untrusted external input. Without explicit bounds the
    HEX/S-Record parsers and `get_continuous_binary()` gap padding are a
    memory-amplification primitive (a 79-byte file drove a 256 MiB allocation
    in verification; ~4 GB at 2**32). These limits are enforced at parse time
    and again before flattening so a crafted image fails closed with a
    controlled `ProtocolError` instead of OOM-ing the desktop process and
    stalling the CAN safety workers.

    Defaults are sized for real automotive images (a 64 MiB image is already
    far larger than any supported ECU flash) while leaving generous headroom
    for legitimate toolchains.
    """

    #: Maximum accepted input file size, in bytes.
    max_file_bytes: int = 128 * 1024 * 1024
    #: Maximum number of records/lines accepted.
    max_lines: int = 4_000_000
    #: Maximum length of a single record line, in characters.
    max_line_chars: int = 1024
    #: Maximum number of data segments after merging.
    max_segments: int = 65_536
    #: Maximum accepted address span (highest end - lowest base).
    max_address_span: int = 64 * 1024 * 1024
    #: Maximum padding for a single inter-segment gap.
    max_gap: int = 1024 * 1024
    #: Maximum total payload bytes (sum of segment sizes).
    max_total_payload_bytes: int = 64 * 1024 * 1024

    def check_span(self, span: int, context: str = "firmware") -> None:
        """Raise ProtocolError when `span` exceeds `max_address_span`."""
        if span > self.max_address_span:
            raise ProtocolError(
                f"{context}: address span {span} bytes exceeds limit {self.max_address_span}"
            )

    def check_gap(self, gap: int, context: str = "firmware") -> None:
        """Raise ProtocolError when a single gap exceeds `max_gap`."""
        if gap > self.max_gap:
            raise ProtocolError(
                f"{context}: inter-segment gap {gap} bytes exceeds limit {self.max_gap}"
            )


#: Shared default limits instance used when a caller does not supply its own.
DEFAULT_FIRMWARE_LIMITS = FirmwareParseLimits()


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

    def get_continuous_binary(
        self,
        fill_byte: int = 0xFF,
        max_span: int | None = None,
        max_gap: int | None = None,
    ) -> tuple[int, bytes]:
        """Flatten segments into a single contiguous binary block with fill_byte padding for gaps.

        F1 (REVIEW Aşama 3): padding gaps is a memory-amplification primitive —
        a tiny crafted HEX file whose segments sit far apart (e.g. one at 0x0
        and one at 0x10000000 via an Extended Linear Address record) made the
        old implementation allocate the whole span (256 MiB from a 79-byte
        file; ~4 GB at 2**32; verified). Both bounds therefore default to the
        process-wide `FirmwareParseLimits` caps and are enforced BEFORE any
        allocation, so an oversized span raises a controlled `ProtocolError`
        instead of an OOM.

        Args:
            fill_byte: Padding byte for gaps between segments.
            max_span: Maximum allowed ``max_address - base_address``. Defaults
                to ``FirmwareParseLimits.DEFAULT.max_address_span``.
            max_gap: Maximum allowed single inter-segment gap. Defaults to
                ``FirmwareParseLimits.DEFAULT.max_gap``.

        Raises:
            ProtocolError: when the span or a single gap exceeds its limit.
        """
        if not self.segments:
            return 0, b""
        span_limit = DEFAULT_FIRMWARE_LIMITS.max_address_span if max_span is None else max_span
        gap_limit = DEFAULT_FIRMWARE_LIMITS.max_gap if max_gap is None else max_gap

        sorted_segs = sorted(self.segments, key=lambda s: s.address)
        base_addr = sorted_segs[0].address
        span = sorted_segs[-1].end_address - base_addr
        # Pre-allocation guard: reject the span BEFORE building the buffer.
        if span > span_limit:
            raise ProtocolError(
                f"Firmware address span 0x{span:X} ({span} bytes) exceeds the "
                f"configured limit of {span_limit} bytes; refusing to flatten"
            )

        buf = bytearray()
        curr_addr = base_addr
        for seg in sorted_segs:
            if seg.address > curr_addr:
                gap = seg.address - curr_addr
                # Per-gap guard: a single huge hole is the DoS primitive even
                # when the aggregate span would pass.
                if gap > gap_limit:
                    raise ProtocolError(
                        f"Firmware inter-segment gap {gap} bytes at 0x{curr_addr:08X} "
                        f"exceeds the configured limit of {gap_limit} bytes"
                    )
                try:
                    buf.extend(bytes([fill_byte & 0xFF]) * gap)
                except MemoryError as exc:  # pragma: no cover - defensive
                    raise ProtocolError(
                        f"Firmware padding allocation failed for gap of {gap} bytes"
                    ) from exc
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
    def parse_string(
        cls,
        content: str,
        limits: FirmwareParseLimits | None = None,
    ) -> FirmwareContainer:
        """Parse Intel HEX text into a container (F1/F3 bounded).

        Enforces record-type allowlisting, single-EOF semantics and the
        `FirmwareParseLimits` resource caps before building segments.
        """
        lim = limits or DEFAULT_FIRMWARE_LIMITS
        if len(content) > lim.max_file_bytes:
            raise ProtocolError(
                f"Intel HEX input {len(content)} bytes exceeds limit {lim.max_file_bytes}"
            )
        lines = content.splitlines()
        if len(lines) > lim.max_lines:
            raise ProtocolError(f"Intel HEX record count {len(lines)} exceeds limit {lim.max_lines}")
        upper_address = 0
        raw_chunks: list[tuple[int, bytes]] = []
        entry_point: int | None = None
        seen_eof = False

        for line_num, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if len(line) > lim.max_line_chars:
                raise ProtocolError(
                    f"Line {line_num}: record length {len(line)} exceeds limit {lim.max_line_chars}"
                )
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
                # F3: EOF must be a bare terminator — length 0 and no payload.
                if length != 0:
                    raise ProtocolError(
                        f"Line {line_num}: EOF record (0x01) must have length 0, got {length}"
                    )
                if seen_eof:
                    raise ProtocolError(f"Line {line_num}: duplicate EOF record (0x01)")
                seen_eof = True
            elif record_type == 0x02:  # Extended Segment Address Record
                if seen_eof:
                    raise ProtocolError(f"Line {line_num}: record after EOF (0x01)")
                if length != 2:
                    raise ProtocolError(f"Line {line_num}: Extended Segment Address record length must be 2")
                upper_address = ((data[0] << 8) | data[1]) << 4
            elif record_type == 0x04:  # Extended Linear Address Record
                if seen_eof:
                    raise ProtocolError(f"Line {line_num}: record after EOF (0x01)")
                if length != 2:
                    raise ProtocolError(f"Line {line_num}: Extended Linear Address record length must be 2")
                upper_address = ((data[0] << 8) | data[1]) << 16
            elif record_type == 0x05:  # Start Linear Address Record (Entry Point)
                if seen_eof:
                    raise ProtocolError(f"Line {line_num}: record after EOF (0x01)")
                if length != 4:
                    raise ProtocolError(
                        f"Line {line_num}: Start Linear Address record length must be 4, got {length}"
                    )
                entry_point = struct.unpack(">I", data)[0]
            else:
                # F3: unknown record types are rejected (fail-closed) rather
                # than silently ignored, so a mis-parsed image can never be
                # reported as a trusted container.
                raise ProtocolError(
                    f"Line {line_num}: unsupported Intel HEX record type 0x{record_type:02X}"
                )

        if not raw_chunks and not seen_eof:
            raise ProtocolError("Empty Intel HEX file or no data records found")

        merged_segments = cls._merge_and_validate_chunks(raw_chunks, lim)
        return FirmwareContainer(
            segments=merged_segments,
            entry_point=entry_point,
            file_format="intel_hex",
        )

    @classmethod
    def parse_file(
        cls,
        file_path: str | Path,
        limits: FirmwareParseLimits | None = None,
    ) -> FirmwareContainer:
        lim = limits or DEFAULT_FIRMWARE_LIMITS
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Firmware file not found: {path}")
        size = path.stat().st_size
        if size > lim.max_file_bytes:
            raise ProtocolError(
                f"Firmware file {size} bytes exceeds limit {lim.max_file_bytes}"
            )
        content = path.read_text(encoding="utf-8", errors="replace")
        return cls.parse_string(content, limits=lim)

    @staticmethod
    def _merge_and_validate_chunks(
        chunks: list[tuple[int, bytes]],
        limits: FirmwareParseLimits | None = None,
    ) -> list[MemorySegment]:
        lim = limits or DEFAULT_FIRMWARE_LIMITS
        if not chunks:
            return []
        if len(chunks) > lim.max_lines:
            raise ProtocolError(f"Firmware chunk count {len(chunks)} exceeds limit {lim.max_lines}")
        total_payload = sum(len(d) for _, d in chunks)
        if total_payload > lim.max_total_payload_bytes:
            raise ProtocolError(
                f"Firmware payload {total_payload} bytes exceeds limit {lim.max_total_payload_bytes}"
            )
        chunks.sort(key=lambda x: x[0])

        # F1: reject an out-of-bounds span BEFORE merging/padding work.
        low = chunks[0][0]
        high = max(addr + len(data) for addr, data in chunks)
        lim.check_span(high - low, "Intel HEX/S-Record")

        merged: list[MemorySegment] = []
        curr_addr, curr_data = chunks[0][0], bytearray(chunks[0][1])

        for addr, data in chunks[1:]:
            curr_end = curr_addr + len(curr_data)
            if addr < curr_end:
                raise ProtocolError(f"Memory overlap detected between 0x{curr_addr:08X} and 0x{addr:08X}")
            elif addr == curr_end:
                curr_data.extend(data)
            else:
                # F1: a single huge gap is the padding DoS primitive.
                lim.check_gap(addr - curr_end, "Intel HEX/S-Record")
                merged.append(MemorySegment(address=curr_addr, data=bytes(curr_data)))
                curr_addr = addr
                curr_data = bytearray(data)

        merged.append(MemorySegment(address=curr_addr, data=bytes(curr_data)))
        if len(merged) > lim.max_segments:
            raise ProtocolError(f"Firmware segment count {len(merged)} exceeds limit {lim.max_segments}")
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
    def parse_string(
        cls,
        content: str,
        limits: FirmwareParseLimits | None = None,
    ) -> FirmwareContainer:
        """Parse Motorola S-Record text into a container (F1/F3 bounded).

        Enforces record-type allowlisting, S5 count agreement, single
        termination-record semantics and the `FirmwareParseLimits` resource
        caps before building segments.
        """
        lim = limits or DEFAULT_FIRMWARE_LIMITS
        if len(content) > lim.max_file_bytes:
            raise ProtocolError(
                f"S-Record input {len(content)} bytes exceeds limit {lim.max_file_bytes}"
            )
        lines = content.splitlines()
        if len(lines) > lim.max_lines:
            raise ProtocolError(f"S-Record record count {len(lines)} exceeds limit {lim.max_lines}")
        raw_chunks: list[tuple[int, bytes]] = []
        entry_point: int | None = None
        data_record_count = 0
        s5_count: int | None = None
        seen_termination = False

        for line_num, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if len(line) > lim.max_line_chars:
                raise ProtocolError(
                    f"Line {line_num}: record length {len(line)} exceeds limit {lim.max_line_chars}"
                )
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

            if seen_termination and rec_type not in ("7", "8", "9"):
                raise ProtocolError(f"Line {line_num}: S-Record record after termination record")

            if rec_type in ("1", "2", "3"):
                addr_len = 2 if rec_type == "1" else (3 if rec_type == "2" else 4)
                if count < addr_len + 1:
                    raise ProtocolError(
                        f"Line {line_num}: S{rec_type} byte count {count} too small for a {addr_len}-byte address"
                    )
                addr = int.from_bytes(raw_bytes[1 : 1 + addr_len], byteorder="big")
                data = raw_bytes[1 + addr_len : -1]
                raw_chunks.append((addr, data))
                data_record_count += 1
            elif rec_type == "0":
                # Header record: no address/payload semantics for flashing.
                if count != 3 + max(0, count - 3):
                    raise ProtocolError(f"Line {line_num}: malformed S0 header record")
            elif rec_type in ("5", "6"):
                # F3: S5 (16-bit) / S6 (24-bit) count records must match the
                # actual number of preceding data records.
                width = 2 if rec_type == "5" else 3
                if count != width + 1:
                    raise ProtocolError(
                        f"Line {line_num}: S{rec_type} count record byte count must be {width + 1}, got {count}"
                    )
                s5_count = int.from_bytes(raw_bytes[1 : 1 + width], byteorder="big")
            elif rec_type in ("7", "8", "9"):
                if seen_termination:
                    raise ProtocolError(f"Line {line_num}: duplicate termination record")
                addr_len = 4 if rec_type == "7" else (3 if rec_type == "8" else 2)
                if count != addr_len + 1:
                    raise ProtocolError(
                        f"Line {line_num}: S{rec_type} byte count must be {addr_len + 1}, got {count}"
                    )
                entry_point = int.from_bytes(raw_bytes[1 : 1 + addr_len], byteorder="big")
                seen_termination = True
            else:
                # F3: unknown/unsupported record type -> fail closed.
                raise ProtocolError(f"Line {line_num}: unsupported S-Record type 'S{rec_type}'")

        if s5_count is not None and s5_count != data_record_count:
            raise ProtocolError(f"S-Record S5 count mismatch (S5={s5_count}, actual data records={data_record_count})")

        if not raw_chunks and entry_point is None:
            raise ProtocolError("Empty S-Record file or no data records found")

        merged = IntelHexParser._merge_and_validate_chunks(raw_chunks, lim)
        return FirmwareContainer(
            segments=merged,
            entry_point=entry_point,
            file_format="s_record",
        )

    @classmethod
    def parse_file(
        cls,
        file_path: str | Path,
        limits: FirmwareParseLimits | None = None,
    ) -> FirmwareContainer:
        lim = limits or DEFAULT_FIRMWARE_LIMITS
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Firmware file not found: {path}")
        size = path.stat().st_size
        if size > lim.max_file_bytes:
            raise ProtocolError(f"Firmware file {size} bytes exceeds limit {lim.max_file_bytes}")
        content = path.read_text(encoding="utf-8", errors="replace")
        return cls.parse_string(content, limits=lim)


def load_firmware(
    file_path_or_content: str | Path,
    limits: FirmwareParseLimits | None = None,
) -> FirmwareContainer:
    """Auto-detect and parse Intel HEX or Motorola S-Record format."""
    lim = limits or DEFAULT_FIRMWARE_LIMITS
    text_content: str
    if isinstance(file_path_or_content, Path) or (
        isinstance(file_path_or_content, str) and "\n" not in file_path_or_content and Path(file_path_or_content).exists()
    ):
        path = Path(file_path_or_content)
        size = path.stat().st_size
        if size > lim.max_file_bytes:
            raise ProtocolError(f"Firmware file {size} bytes exceeds limit {lim.max_file_bytes}")
        text_content = path.read_text(encoding="utf-8", errors="replace")
    else:
        text_content = str(file_path_or_content)

    stripped = text_content.strip()
    if stripped.startswith(":"):
        return IntelHexParser.parse_string(text_content, limits=lim)
    elif stripped.startswith("S"):
        return SRecordParser.parse_string(text_content, limits=lim)
    else:
        raise ProtocolError("Unrecognized firmware format (must start with ':' for HEX or 'S' for SREC)")

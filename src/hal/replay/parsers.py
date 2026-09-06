"""Vector ASCII (.asc), timestamped CSV (.csv), and Vector Binary Logging (.blf) Trace Parsers.

CSV format (K3-a): header-based RFC-4180 files with flexible column names.
Recognized headers (case-insensitive) — `time`/`timestamp`, `id`/`identifier`
(hex `0x1F4` or bare `1F4`), `dlc`/`length`, `data`/`payload` (hex string,
optionally space-separated), `channel`, `dir`/`direction` (rx/tx),
`extended`/`is_extended` (0/1/true/false). Unparseable rows are logged and
skipped — a malformed trace line never aborts the replay load.

BLF format (K3-b): Vector binary logging format parsed via python-can BLFReader.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import can

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame, dlc_to_length, length_to_dlc

logger = get_logger("hal.replay.parsers")

# Standard Vector ASCII log line regex
# Example: "   0.001250 1  18FEEE00x       Rx   d 8 01 02 03 04 05 06 07 08"
CLASSIC_ASC_REGEX = re.compile(
    r"^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)(?P<ext>x)?\s+(?P<dir>Rx|Tx)\s+d\s+(?P<dlc>\d+)(?:\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)*))?"
)

# CAN-FD Vector ASCII log line regex
# Example: "   0.002500 CANFD 1 Rx 123 1 0 12 12 01 02 03 04 05 06 07 08 09 0A 0B 0C"
FD_ASC_REGEX = re.compile(
    r"^\s*(?P<time>\d+\.\d+)\s+CANFD\s+(?P<channel>\d+)\s+(?P<dir>Rx|Tx)\s+(?P<id>[0-9A-Fa-f]+)(?P<ext>x)?\s+(?P<brs>[01])\s+(?P<esi>[01])\s+(?P<dlc>[0-9A-Fa-f]+)\s+(?P<len>\d+)(?:\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)*))?"
)


class VectorAscParser:
    """Parser for Vector CANoe/CANalyzer ASCII (.asc) trace log files."""

    @classmethod
    def parse_file(cls, file_path: str | Path, channel_prefix: str = "ch") -> list[CanFrame]:
        """Parse complete .asc file into chronological CanFrame list.

        Y-06: a single malformed line (bad hex, DLC/payload mismatch, giant
        timestamp) must never abort the whole load — the line is logged and
        skipped, matching the CSV parser's resilience contract.
        """
        return list(cls.parse_file_iter(file_path, channel_prefix=channel_prefix))

    @classmethod
    def parse_file_iter(
        cls, file_path: str | Path, channel_prefix: str = "ch"
    ) -> Iterator[CanFrame]:
        """Stream-parse .asc frames lazily (memory-friendly for huge traces)."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Trace file not found: {path}")

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, 1):
                try:
                    frame = cls.parse_line(line, line_no, channel_prefix)
                except ValueError as exc:
                    logger.warning(
                        "Skipping malformed ASC line",
                        extra={"line_no": line_no, "error": str(exc)},
                    )
                    continue
                if frame is not None:
                    yield frame

    @classmethod
    def parse_line(cls, line: str, line_no: int = 1, channel_prefix: str = "ch") -> CanFrame | None:
        """Parse single ASCII line. Returns None for comments and header lines."""
        line = line.strip()
        if not line or line.startswith(("//", "date", "base")):
            return None

        # Check Classic CAN format
        match_classic = CLASSIC_ASC_REGEX.match(line)
        if match_classic:
            time_sec = float(match_classic.group("time"))
            channel_num = match_classic.group("channel")
            raw_id = match_classic.group("id")
            is_extended = match_classic.group("ext") == "x"
            direction = match_classic.group("dir").lower()
            dlc = int(match_classic.group("dlc"))
            raw_data = match_classic.group("data") or ""
            data_hex = "".join(raw_data.split())
            data_bytes = bytes.fromhex(data_hex)

            arb_id = int(raw_id, 16)
            timestamp_ns = int(time_sec * 1_000_000_000)

            return CanFrame(
                channel_id=f"{channel_prefix}{channel_num}",
                arbitration_id=arb_id,
                dlc=dlc,
                data=data_bytes,
                is_extended=is_extended,
                is_fd=False,
                direction=direction,
                timestamp_ns=timestamp_ns,
                source="replay",
            )

        # Check CAN-FD format
        match_fd = FD_ASC_REGEX.match(line)
        if match_fd:
            time_sec = float(match_fd.group("time"))
            channel_num = match_fd.group("channel")
            raw_id = match_fd.group("id")
            is_extended = match_fd.group("ext") == "x"
            direction = match_fd.group("dir").lower()
            brs = match_fd.group("brs") == "1"
            esi = match_fd.group("esi") == "1"
            raw_dlc_str = match_fd.group("dlc")
            try:
                dlc_val = int(raw_dlc_str, 10)
                if dlc_val > 15:
                    dlc_val = int(raw_dlc_str, 16)
            except ValueError:
                dlc_val = int(raw_dlc_str, 16)
            dlc = dlc_val
            decl_len = int(match_fd.group("len"))
            raw_data = match_fd.group("data") or ""
            data_hex = "".join(raw_data.split())
            data_bytes = bytes.fromhex(data_hex)
            if len(data_bytes) != decl_len:
                raise ValueError(f"CAN-FD declared length {decl_len} does not match actual data {len(data_bytes)} bytes")

            arb_id = int(raw_id, 16)
            timestamp_ns = int(time_sec * 1_000_000_000)

            return CanFrame(
                channel_id=f"{channel_prefix}{channel_num}",
                arbitration_id=arb_id,
                dlc=dlc,
                data=data_bytes,
                is_extended=is_extended,
                is_fd=True,
                brs=brs,
                esi=esi,
                direction=direction,
                timestamp_ns=timestamp_ns,
                source="replay",
            )

        return None


class CsvParser:
    """Header-based CSV trace parser (K3-a).

    Accepts RFC-4180 CSV files with a mandatory header row. Column names are
    matched case-insensitively with common synonyms:

    - time: `time` | `timestamp` (seconds; ISO-8601 timestamps rejected)
    - id: `id` | `identifier` (hex, `0x` prefix optional)
    - dlc: `dlc` | `length` (defaults to the payload byte count)
    - data: `data` | `payload` (hex string, spaces allowed)
    - channel: `channel` (optional, defaults to the file stem)
    - direction: `dir` | `direction` (optional, defaults to rx)
    - extended: `extended` | `is_extended` (optional; defaults from ID width)

    Malformed rows are logged and skipped — never abort the load.
    """

    _COL_ALIASES: ClassVar[dict[str, tuple[str, ...]]] = {
        "time": ("time", "timestamp"),
        "id": ("id", "identifier", "can_id"),
        "dlc": ("dlc", "length"),
        "data": ("data", "payload", "bytes"),
        "channel": ("channel", "ch"),
        "direction": ("dir", "direction"),
        "extended": ("extended", "is_extended", "ext"),
    }

    @classmethod
    def parse_file(cls, file_path: str | Path) -> list[CanFrame]:
        """Parse a CSV trace file into a chronological CanFrame list."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Trace file not found: {path}")

        frames: list[CanFrame] = []
        with open(path, "r", encoding="utf-8", newline="", errors="ignore") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                logger.warning("CSV trace has no header row; nothing parsed", extra={"file": str(path)})
                return frames

            col = cls._resolve_columns(reader.fieldnames)
            if col.get("time") is None or col.get("id") is None or col.get("data") is None:
                logger.warning(
                    "CSV trace missing mandatory columns (time/id/data)",
                    extra={"file": str(path), "header": reader.fieldnames},
                )
                return frames

            for row_no, row in enumerate(reader, 2):  # row 1 is the header
                frame = cls._parse_row(row, col, path.stem, row_no)
                if frame is not None:
                    frames.append(frame)

        logger.info(
            "Loaded CSV trace",
            extra={"file": str(path), "frame_count": len(frames)},
        )
        return frames

    @classmethod
    def _resolve_columns(cls, fieldnames: list[str]) -> dict[str, str | None]:
        """Map canonical field -> actual CSV column via alias sets."""
        normalized = {name.strip().lower(): name for name in fieldnames}
        resolved: dict[str, str | None] = {}
        for canonical, aliases in cls._COL_ALIASES.items():
            resolved[canonical] = next(
                (normalized[a] for a in aliases if a in normalized), None
            )
        return resolved

    @classmethod
    def _parse_row(
        cls,
        row: dict[str, str | None],
        col: dict[str, str | None],
        default_channel: str,
        row_no: int,
    ) -> CanFrame | None:
        """Parse one CSV row; returns None (and logs) on malformed content."""
        raw_time = (row.get(col["time"]) or "").strip() if col.get("time") else ""
        raw_id = (row.get(col["id"]) or "").strip() if col.get("id") else ""
        raw_data = (row.get(col["data"]) or "").strip() if col.get("data") else ""
        if not raw_time or not raw_id or not raw_data:
            logger.debug("Skipping empty CSV row", extra={"row": row_no})
            return None

        try:
            time_sec = float(raw_time)
            arb_id = int(raw_id.removeprefix("0x").removeprefix("0X"), 16)
            data_bytes = bytes.fromhex("".join(raw_data.split()))
        except ValueError as exc:
            logger.warning("Skipping malformed CSV row", extra={"row": row_no, "error": str(exc)})
            return None

        if time_sec < 0 or arb_id < 0 or arb_id > 0x1FFFFFFF or len(data_bytes) > 64:
            logger.warning("Skipping out-of-range CSV row", extra={"row": row_no})
            return None

        raw_dlc = (row.get(col["dlc"]) or "").strip() if col.get("dlc") else ""
        try:
            dlc = int(raw_dlc) if raw_dlc else len(data_bytes)
        except ValueError:
            dlc = len(data_bytes)

        raw_ext = (row.get(col["extended"]) or "").strip().lower() if col.get("extended") else ""
        if raw_ext in ("1", "true", "yes"):
            is_extended = True
        elif raw_ext in ("0", "false", "no"):
            is_extended = False
        else:
            is_extended = arb_id > 0x7FF

        raw_dir = (row.get(col["direction"]) or "rx").strip().lower() if col.get("direction") else "rx"
        direction = "tx" if raw_dir in ("tx", "t", "sent") else "rx"

        channel = (
            (row.get(col["channel"]) or "").strip() if col.get("channel") and (row.get(col["channel"]) or "").strip() else default_channel
        )

        return CanFrame(
            channel_id=channel,
            arbitration_id=arb_id,
            dlc=dlc,
            data=data_bytes,
            is_extended=is_extended,
            is_fd=False,
            direction=direction,
            timestamp_ns=int(time_sec * 1_000_000_000),
            source="replay",
        )


class VectorBlfParser:
    """Parser for Vector Binary Logging Format (.blf) trace files using python-can (K3-b)."""

    @classmethod
    def parse_file(cls, file_path: str | Path, channel_prefix: str = "ch") -> list[CanFrame]:
        """Parse complete .blf file into chronological CanFrame list."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Trace file not found: {path}")

        frames: list[CanFrame] = []
        try:
            reader = can.BLFReader(str(path))
            for msg in reader:
                try:
                    frame = cls._convert_message(msg, channel_prefix=channel_prefix)
                    if frame is not None:
                        frames.append(frame)
                except (ValueError, can.CanError) as rec_exc:
                    logger.debug("Skipping malformed BLF message", extra={"error": str(rec_exc)})
                    continue
        except Exception as exc:
            logger.warning(
                "Error reading BLF trace file or corrupted content",
                extra={"file": str(path), "error": str(exc)},
            )

        logger.info(
            "Loaded BLF trace",
            extra={"file": str(path), "frame_count": len(frames)},
        )
        return frames

    @classmethod
    def _convert_message(cls, msg: Any, channel_prefix: str = "ch") -> CanFrame | None:
        """Convert a python-can Message to canonical CanFrame."""
        if getattr(msg, "is_error_frame", False) or getattr(msg, "is_remote_frame", False):
            return None

        arb_id = int(getattr(msg, "arbitration_id", 0))
        if arb_id < 0 or arb_id > 0x1FFFFFFF:
            return None

        data_bytes = bytes(getattr(msg, "data", b"") or b"")
        if len(data_bytes) > 64:
            return None

        is_fd = bool(getattr(msg, "is_fd", False))
        is_extended = bool(getattr(msg, "is_extended_id", arb_id > 0x7FF))
        brs = bool(getattr(msg, "bitrate_switch", False))
        esi = bool(getattr(msg, "error_state_indicator", False))
        is_rx = getattr(msg, "is_rx", True)
        direction = "rx" if is_rx else "tx"

        raw_dlc = getattr(msg, "dlc", None)
        if raw_dlc is not None and 0 <= raw_dlc <= 15:
            if dlc_to_length(raw_dlc) >= len(data_bytes):
                dlc = raw_dlc
            else:
                dlc = length_to_dlc(len(data_bytes))
        else:
            dlc = length_to_dlc(len(data_bytes))

        ch = getattr(msg, "channel", None)
        if ch is None:
            channel_id = f"{channel_prefix}1"
        elif isinstance(ch, int):
            channel_id = f"{channel_prefix}{ch}"
        else:
            ch_str = str(ch).strip()
            if ch_str.isdigit():
                channel_id = f"{channel_prefix}{ch_str}"
            elif ch_str:
                channel_id = ch_str
            else:
                channel_id = f"{channel_prefix}1"

        ts_sec = getattr(msg, "timestamp", 0.0) or 0.0
        timestamp_ns = max(0, int(ts_sec * 1_000_000_000))

        return CanFrame(
            channel_id=channel_id,
            arbitration_id=arb_id,
            dlc=dlc,
            data=data_bytes,
            is_extended=is_extended,
            is_fd=is_fd,
            brs=brs,
            esi=esi,
            direction=direction,
            timestamp_ns=timestamp_ns,
            source="replay",
        )

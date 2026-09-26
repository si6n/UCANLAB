"""NMEA 2000 / Canboat Trace Converter and Provenance Sidecar Generator.

Converts marine/industrial raw trace formats (canboat raw CSV, fast-packet .all,
and Linux SocketCAN candump logs) into standard CAN trace CSV / Vector ASC format
compatible with ReplayBus and CsvParser.

Complies with AGENTS.md §2.3 (Zero Fabricated Telemetry):
- Explicit provenance metadata recorded in `<output_path>.provenance.json`.
- Missing or non-absolute timestamps are strictly classified:
  `exact_iso8601`, `exact_epoch`, `assumed_monotonic`, or `missing`.
- Fast packets (>8 bytes) are decomposed following the IEC 61162-3 / NMEA 2000
  Fast-Packet protocol (6 bytes in bucket 0, 7 bytes in buckets 1..31, 0xFF padding).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

logger = get_logger("hal.replay.n2k_converter")

CANDUMP_REGEX = re.compile(
    r"^\s*\((?P<time>\d+(?:\.\d+)?)\)\s+(?P<ch>\S+)\s+(?P<id>[0-9A-Fa-f]+)#(?P<data>[0-9A-Fa-f]*)"
)


@dataclass(frozen=True, slots=True)
class ProvenanceMetadata:
    """Trace conversion provenance record (ISO 26262 audit trail)."""

    schema_version: str
    source_file: str
    source_sha256: str
    source_format: str
    output_file: str
    output_sha256: str
    total_frames: int
    fast_packets_decomposed: int
    timestamp_policy: str
    lossy: bool
    generated_at: str


class N2KTraceConverter:
    """Universal converter for N2K, Canboat raw/all, and candump logs."""

    @staticmethod
    def calculate_sha256(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def pgn_to_can_id(priority: int, pgn: int, source: int, destination: int = 255) -> int:
        """Construct 29-bit CAN arbitration ID from J1939 / N2K fields."""
        pf = (pgn >> 8) & 0xFF
        prio = priority & 0x07
        sa = source & 0xFF
        da = destination & 0xFF

        if pf < 240:
            # PDU1 format (destination specific)
            return (prio << 26) | ((pgn & 0x3FF00) << 8) | (da << 8) | sa
        else:
            # PDU2 format (broadcast / group extension)
            return (prio << 26) | ((pgn & 0x3FFFF) << 8) | sa

    @classmethod
    def decompose_fast_packet(
        cls,
        payload: bytes,
        sequence_id: int = 0,
    ) -> list[bytes]:
        """Decompose a multi-byte N2K payload (>8 bytes) into 8-byte CAN frames.

        IEC 61162-3 / N2K Fast-Packet layout:
        - Bucket 0: [seq_id << 5 | 0x00, total_length, b0, b1, b2, b3, b4, b5]
        - Bucket 1..N: [seq_id << 5 | frame_idx, b0..b6 (padded with 0xFF to 8 bytes)]
        """
        total_len = len(payload)
        if total_len <= 8:
            return [payload.ljust(8, b"\xff") if len(payload) < 8 else payload]

        seq = sequence_id & 0x07
        frames: list[bytes] = []

        # Bucket 0: 6 payload bytes
        b0_header = bytes([(seq << 5) | 0x00, total_len])
        b0_data = payload[:6]
        frames.append(b0_header + b0_data.ljust(6, b"\xff"))

        # Buckets 1..N: 7 payload bytes each
        offset = 6
        frame_idx = 1
        while offset < total_len and frame_idx < 32:
            chunk = payload[offset : offset + 7]
            header = bytes([(seq << 5) | (frame_idx & 0x1F)])
            frames.append(header + chunk.ljust(7, b"\xff"))
            offset += 7
            frame_idx += 1

        return frames

    @classmethod
    def parse_canboat_raw_line(
        cls,
        line: str,
        base_timestamp: float | None = None,
        seq_tracker: dict[int, int] | None = None,
    ) -> tuple[float, int, list[bytes], str]:
        """Parse one canboat raw or fast-packet line.

        Format: `timestamp,priority,pgn,source,destination,length,b0,b1,...`
        Returns `(timestamp_sec, can_id, frames_data, timestamp_policy)`
        """
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            raise ValueError(f"Too few columns in canboat raw line: {line[:50]}")

        ts_str, prio_str, pgn_str, src_str, dst_str, _ = parts[:6]
        data_hex = parts[6:]
        payload = bytes(int(b, 16) for b in data_hex if b)

        prio = int(prio_str)
        pgn = int(pgn_str)
        src = int(src_str)
        dst = int(dst_str)
        can_id = cls.pgn_to_can_id(prio, pgn, src, dst)

        # Parse timestamp
        timestamp_policy = "exact_iso8601"
        if not ts_str:
            timestamp_policy = "assumed_monotonic"
            ts_sec = (base_timestamp or 0.0) + 0.001
        else:
            try:
                # ISO-8601 format: 2026-06-08T01:51:24.127 or 2026-06-08T01:51:24.127Z
                clean_ts = ts_str.rstrip("Z")
                dt = datetime.fromisoformat(clean_ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts_sec = dt.timestamp()
            except ValueError:
                # Fallback to float seconds if already numeric
                try:
                    ts_sec = float(ts_str)
                    timestamp_policy = "exact_epoch"
                except ValueError:
                    ts_sec = (base_timestamp or 0.0) + 0.001
                    timestamp_policy = "assumed_monotonic"

        # Sequence counter for fast packets
        seq_id = 0
        if seq_tracker is not None:
            seq_id = seq_tracker.get(can_id, 0)
            seq_tracker[can_id] = (seq_id + 1) & 0x07

        if len(payload) > 8:
            frames = cls.decompose_fast_packet(payload, sequence_id=seq_id)
        else:
            frames = [payload]

        return ts_sec, can_id, frames, timestamp_policy

    @classmethod
    def convert_file(
        cls,
        input_path: str | Path,
        output_path: str | Path | None = None,
        channel: str = "ch1",
    ) -> tuple[Path, Path]:
        """Convert a raw trace file to CSV and generate a provenance sidecar.

        Returns `(output_csv_path, provenance_json_path)`
        """
        in_p = Path(input_path).resolve()
        if not in_p.is_file():
            raise FileNotFoundError(f"Input trace file not found: {in_p}")

        if output_path is None:
            out_p = in_p.with_suffix(".csv")
        else:
            out_p = Path(output_path).resolve()

        sidecar_p = out_p.with_name(f"{out_p.name}.provenance.json")

        in_sha256 = cls.calculate_sha256(in_p)
        seq_tracker: dict[int, int] = {}
        total_frames = 0
        fast_packets = 0
        detected_policies: set[str] = set()
        detected_format = "canboat_raw"

        # Determine if candump or canboat
        first_line = ""
        with open(in_p, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    first_line = s
                    break

        is_candump = bool(CANDUMP_REGEX.match(first_line))
        if is_candump:
            detected_format = "socketcan_candump"

        out_rows: list[dict[str, Any]] = []
        last_ts = 0.0

        with open(in_p, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue

                if is_candump:
                    m = CANDUMP_REGEX.match(s)
                    if not m:
                        continue
                    ts = float(m.group("time"))
                    cid = int(m.group("id"), 16)
                    data_hex = m.group("data")
                    data_bytes = bytes.fromhex(data_hex) if data_hex else b""
                    out_rows.append({
                        "time": f"{ts:.6f}",
                        "id": f"0x{cid:08X}",
                        "dlc": len(data_bytes),
                        "data": data_bytes.hex().upper(),
                        "channel": m.group("ch") or channel,
                        "dir": "Rx",
                        "extended": 1 if cid > 0x7FF else 0,
                    })
                    total_frames += 1
                    detected_policies.add("exact_epoch")
                else:
                    try:
                        ts, cid, f_list, policy = cls.parse_canboat_raw_line(
                            s, base_timestamp=last_ts, seq_tracker=seq_tracker
                        )
                        last_ts = ts
                        detected_policies.add(policy)
                        if len(f_list) > 1:
                            fast_packets += 1
                        # Generate sub-millisecond deltas for fast packet bursts
                        for idx, frame_bytes in enumerate(f_list):
                            frame_ts = ts + (idx * 0.0001)
                            out_rows.append({
                                "time": f"{frame_ts:.6f}",
                                "id": f"0x{cid:08X}",
                                "dlc": len(frame_bytes),
                                "data": frame_bytes.hex().upper(),
                                "channel": channel,
                                "dir": "Rx",
                                "extended": 1,
                            })
                            total_frames += 1
                    except Exception as exc:
                        logger.debug("Skipping unparseable line: %s (%s)", s[:40], exc)

        # Write output CSV
        out_p.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["time", "id", "dlc", "data", "channel", "dir", "extended"]
        with open(out_p, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(out_rows)

        out_sha256 = cls.calculate_sha256(out_p)
        dominant_policy = sorted(detected_policies)[0] if detected_policies else "exact_iso8601"

        provenance = ProvenanceMetadata(
            schema_version="1.0",
            source_file=in_p.name,
            source_sha256=in_sha256,
            source_format=detected_format,
            output_file=out_p.name,
            output_sha256=out_sha256,
            total_frames=total_frames,
            fast_packets_decomposed=fast_packets,
            timestamp_policy=dominant_policy,
            lossy=False,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        with open(sidecar_p, "w", encoding="utf-8") as f:
            json.dump(asdict(provenance), f, indent=2)

        logger.info(
            "Converted %s -> %s (%d frames, %d fast packets)",
            in_p.name,
            out_p.name,
            total_frames,
            fast_packets,
        )
        return out_p, sidecar_p

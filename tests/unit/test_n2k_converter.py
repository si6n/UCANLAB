import json
import tempfile
from pathlib import Path

import pytest

from src.hal.replay.n2k_converter import N2KTraceConverter, ProvenanceMetadata
from src.hal.replay.parsers import CsvParser


def test_pgn_to_can_id() -> None:
    # PDU2 broadcast (PF >= 240): e.g. PGN 127257 (0x01F119, PF=0xF1=241)
    # Priority=3, Source=52 (0x34)
    cid = N2KTraceConverter.pgn_to_can_id(priority=3, pgn=127257, source=52)
    # Expected: 0x0DF11934
    assert cid == 0x0DF11934

    # PDU1 addressed (PF < 240): e.g. PGN 60928 (0x0EE00, PF=0xEE=238)
    # Priority=6, Source=10 (0x0A), Destination=255 (0xFF)
    cid_pdu1 = N2KTraceConverter.pgn_to_can_id(priority=6, pgn=60928, source=10, destination=255)
    # Expected: (6 << 26) | (0xEE << 16) | (0xFF << 8) | 0x0A = 0x18EEFF0A
    assert cid_pdu1 == 0x18EEFF0A


def test_decompose_fast_packet() -> None:
    # Single frame payload (<= 8 bytes)
    payload_short = b"\x01\x02\x03\x04"
    frames = N2KTraceConverter.decompose_fast_packet(payload_short)
    assert len(frames) == 1
    assert frames[0] == b"\x01\x02\x03\x04\xff\xff\xff\xff"

    # Fast packet payload (14 bytes)
    # Bucket 0: 6 bytes + [header, len]
    # Bucket 1: 7 bytes + [header]
    # Bucket 2: 1 byte + 6 padding + [header]
    payload_long = bytes(range(14))
    frames = N2KTraceConverter.decompose_fast_packet(payload_long, sequence_id=2)
    assert len(frames) == 3

    # Bucket 0: seq=2, idx=0 => (2 << 5) | 0 = 0x40. Len = 14 = 0x0E.
    assert frames[0][0] == 0x40
    assert frames[0][1] == 14
    assert frames[0][2:8] == bytes(range(6))

    # Bucket 1: seq=2, idx=1 => (2 << 5) | 1 = 0x41. 7 bytes.
    assert frames[1][0] == 0x41
    assert frames[1][1:8] == bytes(range(6, 13))

    # Bucket 2: seq=2, idx=2 => (2 << 5) | 2 = 0x42. 1 byte + 6 padding.
    assert frames[2][0] == 0x42
    assert frames[2][1] == 13
    assert frames[2][2:8] == b"\xff" * 6


def test_convert_canboat_raw_to_csv_and_replay() -> None:
    sample_raw = """# format=FAST
2026-06-08T01:51:24.127,3,127257,52,255,8,ff,e1,a6,4c,00,a2,fe,ff
2026-06-08T01:51:24.232,3,129029,52,255,14,01,02,03,04,05,06,07,08,09,0a,0b,0c,0d,0e
"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        in_file = Path(tmp_dir) / "test.raw"
        in_file.write_text(sample_raw, encoding="utf-8")

        out_csv, sidecar = N2KTraceConverter.convert_file(in_file)
        assert out_csv.is_file()
        assert sidecar.is_file()

        # Check sidecar content
        sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
        assert sidecar_data["source_file"] == "test.raw"
        assert sidecar_data["source_format"] == "canboat_raw"
        assert sidecar_data["fast_packets_decomposed"] == 1
        # 1 standard frame + 3 fast packet sub-frames = 4 total frames
        assert sidecar_data["total_frames"] == 4
        assert sidecar_data["timestamp_policy"] == "exact_iso8601"

        # Verify output CSV is readable by existing CsvParser
        parsed_frames = CsvParser.parse_file(out_csv)
        assert len(parsed_frames) == 4
        assert parsed_frames[0].is_extended is True
        assert parsed_frames[0].dlc == 8

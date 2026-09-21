"""Unit tests for authenticated RollingDiskBuffer chunks."""

from __future__ import annotations

import hashlib
import hmac
import pickle
import struct
import time
from pathlib import Path

import pytest
import zstandard as zstd

from src.core.errors import SecurityError
from src.core.models.can_frame import CanFrame
from src.engine.buffer.rolling_disk import (
    CHUNK_MAGIC,
    CHUNK_VERSION,
    FRAME_FMT,
    FRAME_SIZE,
    HEADER_FMT,
    HEADER_PREFIX_FMT,
    HEADER_SIZE,
    RollingDiskBuffer,
    _deserialize_chunk,
    _serialize_chunk,
)
from src.safety.secret_provider import EphemeralSecretBackend

KEY = b"k" * 32


def _frame(index: int = 1) -> CanFrame:
    return CanFrame(
        channel_id="can0",
        arbitration_id=0x18FF0000 + index,
        dlc=9,
        data=bytes(range(12)),
        is_extended=True,
        is_fd=True,
        brs=True,
        esi=True,
        direction="tx",
        timestamp_ns=1_000_000 + index,
        hardware_timestamp_ns=2_000_000 + index,
        host_timestamp_ns=3_000_000 + index,
        sequence=-index,
        error_state="passive",
        source="replay",
    )


def _provider() -> EphemeralSecretBackend:
    return EphemeralSecretBackend({"ROLLING_DISK_HMAC_KEY": KEY})


def _rebuild_chunk(body: bytes, count: int = 1) -> bytes:
    prefix = struct.pack(
        HEADER_PREFIX_FMT,
        CHUNK_MAGIC,
        CHUNK_VERSION,
        0,
        count,
        FRAME_SIZE,
    )
    mac = hmac.new(KEY, prefix + body, hashlib.sha256).digest()
    return prefix + mac + body


def test_wire_sizes_are_self_consistent() -> None:
    assert HEADER_SIZE == 48
    assert struct.calcsize(HEADER_FMT) == HEADER_SIZE
    assert struct.calcsize(FRAME_FMT) == FRAME_SIZE
    assert len(_serialize_chunk([_frame()], KEY)) == HEADER_SIZE + FRAME_SIZE


def test_roundtrip_preserves_all_can_frame_fields() -> None:
    frames = [_frame(1), _frame(2)]
    assert _deserialize_chunk(_serialize_chunk(frames, KEY), KEY) == frames


def test_rolling_disk_append_flush_read(tmp_path: Path) -> None:
    disk_buf = RollingDiskBuffer(
        storage_dir=tmp_path,
        chunk_frame_threshold=5,
        secret_provider=_provider(),
    )

    frames = [_frame(i + 1) for i in range(12)]
    for frame in frames:
        disk_buf.append(frame)

    stored_frames = disk_buf.read_all_stored_frames()
    assert stored_frames == frames
    assert len(list(tmp_path.glob("chunk_*.bin.zst"))) == 3

    disk_buf.clear()
    assert disk_buf.read_all_stored_frames() == []


def test_body_tamper_is_rejected() -> None:
    raw = bytearray(_serialize_chunk([_frame()], KEY))
    raw[-1] ^= 0x01
    with pytest.raises(SecurityError, match="HMAC mismatch"):
        _deserialize_chunk(bytes(raw), KEY)


def test_authenticated_header_tamper_is_rejected() -> None:
    raw = bytearray(_serialize_chunk([_frame()], KEY))
    raw[12] ^= 0x01
    with pytest.raises(SecurityError):
        _deserialize_chunk(bytes(raw), KEY)


def test_bad_magic_is_rejected() -> None:
    raw = bytearray(_serialize_chunk([_frame()], KEY))
    raw[0] ^= 0x01
    with pytest.raises(SecurityError, match="magic"):
        _deserialize_chunk(bytes(raw), KEY)


def test_truncated_and_trailing_data_are_rejected() -> None:
    raw = _serialize_chunk([_frame()], KEY)
    with pytest.raises(SecurityError, match="length"):
        _deserialize_chunk(raw[:-1], KEY)
    with pytest.raises(SecurityError, match="length"):
        _deserialize_chunk(raw + b"x", KEY)


def test_reserved_header_flags_are_rejected() -> None:
    raw = bytearray(_serialize_chunk([_frame()], KEY))
    raw[9] = 1
    with pytest.raises(SecurityError, match="header flags"):
        _deserialize_chunk(bytes(raw), KEY)


def test_reserved_frame_flags_are_rejected_even_with_valid_hmac() -> None:
    raw = _serialize_chunk([_frame()], KEY)
    values = list(struct.unpack(FRAME_FMT, raw[HEADER_SIZE:]))
    values[5] |= 1 << 15
    body = struct.pack(FRAME_FMT, *values)

    with pytest.raises(SecurityError, match="Reserved frame flag"):
        _deserialize_chunk(_rebuild_chunk(body), KEY)


def test_invalid_hmac_key_length_is_rejected() -> None:
    with pytest.raises(SecurityError, match="key"):
        _serialize_chunk([_frame()], b"short")


def test_legacy_chunk_is_moved_out_of_active_store(tmp_path: Path) -> None:
    legacy_frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=1,
        data=b"\x01",
        timestamp_ns=123,
    )
    chunk = tmp_path / "chunk_00000000_00000000000000000123.bin.zst"
    chunk.write_bytes(zstd.ZstdCompressor().compress(pickle.dumps([legacy_frame])))

    RollingDiskBuffer(tmp_path, secret_provider=_provider())

    assert not chunk.exists()
    assert (tmp_path / "legacy_pickle" / chunk.name).exists()


def test_chunk_naming_and_restart_index_are_preserved(tmp_path: Path) -> None:
    first = RollingDiskBuffer(tmp_path, chunk_frame_threshold=1, secret_provider=_provider())
    first.append(_frame(1))
    # B-02: append() hands the chunk to the async worker and returns without
    # waiting for disk I/O, so tests that inspect the directory must drain.
    first.flush(drain=True)
    second = RollingDiskBuffer(tmp_path, chunk_frame_threshold=1, secret_provider=_provider())
    second.append(_frame(2))
    second.flush(drain=True)

    names = sorted(path.name for path in tmp_path.glob("chunk_*.bin.zst"))
    assert names[0].startswith("chunk_00000000_")
    assert names[1].startswith("chunk_00000001_")


def test_retention_budget_is_enforced(tmp_path: Path) -> None:
    disk_buf = RollingDiskBuffer(
        tmp_path,
        max_disk_bytes=1,
        chunk_frame_threshold=1,
        secret_provider=_provider(),
    )
    disk_buf.append(_frame())
    disk_buf.flush(drain=True)
    assert list(tmp_path.glob("chunk_*.bin.zst")) == []


def test_retention_age_is_enforced(tmp_path: Path) -> None:
    disk_buf = RollingDiskBuffer(
        tmp_path,
        max_retention_sec=1,
        chunk_frame_threshold=1,
        secret_provider=_provider(),
    )
    disk_buf.append(_frame())
    disk_buf.flush(drain=True)
    chunk = next(tmp_path.glob("chunk_*.bin.zst"))
    old = time.time() - 60
    chunk.touch()
    import os

    os.utime(chunk, (old, old))
    # The periodic sweep is throttled; use the unthrottled variant the same way
    # close()/clear() do.
    disk_buf._enforce_retention_now()
    assert not chunk.exists()


def test_decompression_output_limit_is_enforced(tmp_path: Path) -> None:
    disk_buf = RollingDiskBuffer(tmp_path, secret_provider=_provider())
    oversized = b"x" * (disk_buf._max_chunk_bytes + 1)
    chunk = tmp_path / "chunk_00000000_00000000000000000000.bin.zst"
    chunk.write_bytes(zstd.ZstdCompressor().compress(oversized))

    # RD-2: the default read path now isolates a bad chunk; strict
    # tamper-detection is an explicit opt-in (quarantine_corrupt=False).
    with pytest.raises(SecurityError, match="decompression limit"):
        disk_buf.read_all_stored_frames(quarantine_corrupt=False)


def test_malformed_frame_does_not_kill_recorder(tmp_path) -> None:
    """E10: an oversized/invalid frame must be rejected at append time without
    raising — one bad frame must never stop the blackbox ingestion loop."""
    rdb = RollingDiskBuffer(storage_dir=tmp_path / "rd", chunk_frame_threshold=3)

    good = CanFrame.create(
        channel_id="ch", arbitration_id=0x123, data=b"\x01\x02", is_extended=False
    )
    # Oversized channel_id (CHANNEL_ID_SIZE=32 limit) -> serialize would raise
    bad = CanFrame.create(
        channel_id="c" * 33, arbitration_id=0x123, data=b"\x01", is_extended=False
    )

    rdb.append(good)
    rdb.append(bad)   # must not raise, must not enter the chunk
    rdb.append(good)
    rdb.append(good)  # 3 GOOD frames reach threshold -> flush

    assert rdb._rejected_frames == 1
    assert len(rdb._current_chunk_frames) == 0  # flushed with good frames only
    # The good chunk was persisted and round-trips
    assert len(rdb.read_all_stored_frames()) == 3


def test_flush_survives_post_admission_mutation(tmp_path) -> None:
    """E10 defense-in-depth: a frame mutated AFTER admission is dropped at
    flush (whole pending chunk) instead of raising out of the ingest thread."""
    rdb = RollingDiskBuffer(storage_dir=tmp_path / "rd", chunk_frame_threshold=2)
    good = CanFrame.create(
        channel_id="ch", arbitration_id=0x1, data=b"\x01", is_extended=False
    )
    rdb.append(good)
    # Post-admission corruption: sequence beyond the signed-64 serialize range
    object.__setattr__(good, "sequence", 1 << 70)
    # append of second frame triggers flush of the mutated pending one
    rdb.append(CanFrame.create(channel_id="ch", arbitration_id=0x2, data=b"\x02", is_extended=False))
    # No exception; the recorder is alive and the bad chunk was dropped
    assert rdb._rejected_frames >= 1

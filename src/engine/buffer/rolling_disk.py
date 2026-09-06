"""Rolling compressed disk buffer with authenticated, bounded chunk storage."""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import queue
import struct
import threading
import time
from pathlib import Path
from typing import ClassVar

import zstandard as zstd

from src.core.errors import SecurityError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.safety.secret_provider import SecretProvider, get_default_secret_provider

logger = get_logger("engine.buffer.disk")

CHUNK_MAGIC = b"UCAN0001"
CHUNK_VERSION = 2
HEADER_FMT = "<8sBBIH32s"
HEADER_PREFIX_FMT = "<8sBBIH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# Version 2 preserves all 15 canonical CanFrame fields. The v13.0.2 plan's
# 113-byte record was rejected because it silently discarded frame metadata.
FRAME_FMT = "<IQQQqHBBBBH32sB64s"
FRAME_SIZE = struct.calcsize(FRAME_FMT)
CHANNEL_ID_SIZE = 32
DATA_SIZE = 64

FLAG_IS_EXTENDED = 1 << 0
FLAG_IS_FD = 1 << 1
FLAG_BRS = 1 << 2
FLAG_ESI = 1 << 3
FLAG_DIRECTION_TX = 1 << 4
FLAG_HAS_HARDWARE_TIMESTAMP = 1 << 5
FLAG_HAS_HOST_TIMESTAMP = 1 << 6
_FLAG_RESERVED_MASK = 0xFF80

_ERROR_STATE_TO_CODE = {"active": 0, "passive": 1, "bus_off": 2}
_CODE_TO_ERROR_STATE = {value: key for key, value in _ERROR_STATE_TO_CODE.items()}
_SOURCE_TO_CODE = {"physical": 0, "replay": 1, "virtual": 2, "injected": 3}
_CODE_TO_SOURCE = {value: key for key, value in _SOURCE_TO_CODE.items()}

HMAC_KEY_NAME = "ROLLING_DISK_HMAC_KEY"


def _get_hmac_key(secret_provider: SecretProvider) -> bytes:
    """Resolve the chunk-authentication HMAC key (E11 key-loss contract).

    Key loss semantics: if the vault key vanishes (reinstall/reset), a NEW key
    is minted here. Chunks signed by the old key then fail HMAC verification
    and are treated as unauthenticated — the legacy-file sweep at startup moves
    them out of the active store instead of serving them. Recorded telemetry is
    therefore never silently trusted across a key change: old chunks are kept
    on disk under a quarantine name, new chunks authenticate normally.
    """
    try:
        key = secret_provider.get_secret(HMAC_KEY_NAME)
    except (KeyError, SecurityError):
        key = os.urandom(32)
        try:
            secret_provider.store_secret(HMAC_KEY_NAME, key)
        except Exception as store_err:
            logger.warning("Failed to store rolling disk HMAC key", extra={"error": str(store_err)})
        logger.info("Initialized rolling disk HMAC key")

    if len(key) != 32:
        raise SecurityError("Rolling disk HMAC key has invalid length", code="SECURITY_ERROR")
    return key


def _checked_u64(value: int, field_name: str) -> int:
    if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"{field_name} must fit in an unsigned 64-bit integer")
    return value


def _serialize_frame(frame: CanFrame) -> bytes:
    channel = frame.channel_id.encode("utf-8")
    if len(channel) > CHANNEL_ID_SIZE:
        raise ValueError(f"channel_id exceeds {CHANNEL_ID_SIZE} UTF-8 bytes")
    if len(frame.data) > DATA_SIZE:
        raise ValueError(f"CAN payload exceeds {DATA_SIZE} bytes")
    if not -(1 << 63) <= frame.sequence < (1 << 63):
        raise ValueError("sequence must fit in a signed 64-bit integer")

    flags = 0
    if frame.is_extended:
        flags |= FLAG_IS_EXTENDED
    if frame.is_fd:
        flags |= FLAG_IS_FD
    if frame.brs:
        flags |= FLAG_BRS
    if frame.esi:
        flags |= FLAG_ESI
    if frame.direction == "tx":
        flags |= FLAG_DIRECTION_TX
    if frame.hardware_timestamp_ns is not None:
        flags |= FLAG_HAS_HARDWARE_TIMESTAMP
    if frame.host_timestamp_ns is not None:
        flags |= FLAG_HAS_HOST_TIMESTAMP

    hardware_timestamp_ns = 0
    if frame.hardware_timestamp_ns is not None:
        hardware_timestamp_ns = _checked_u64(frame.hardware_timestamp_ns, "hardware_timestamp_ns")
    host_timestamp_ns = 0
    if frame.host_timestamp_ns is not None:
        host_timestamp_ns = _checked_u64(frame.host_timestamp_ns, "host_timestamp_ns")

    return struct.pack(
        FRAME_FMT,
        frame.arbitration_id,
        _checked_u64(frame.timestamp_ns, "timestamp_ns"),
        hardware_timestamp_ns,
        host_timestamp_ns,
        frame.sequence,
        flags,
        frame.dlc,
        _ERROR_STATE_TO_CODE[frame.error_state],
        _SOURCE_TO_CODE[frame.source],
        len(channel),
        0,
        channel.ljust(CHANNEL_ID_SIZE, b"\x00"),
        len(frame.data),
        frame.data.ljust(DATA_SIZE, b"\xcc"),
    )


def _serialize_chunk(frames: list[CanFrame], key: bytes) -> bytes:
    if len(key) != 32:
        raise SecurityError("Rolling disk HMAC key has invalid length", code="SECURITY_ERROR")
    if len(frames) > 0xFFFFFFFF:
        raise ValueError("Chunk frame count exceeds format limit")

    body = b"".join(_serialize_frame(frame) for frame in frames)
    prefix = struct.pack(
        HEADER_PREFIX_FMT,
        CHUNK_MAGIC,
        CHUNK_VERSION,
        0,
        len(frames),
        FRAME_SIZE,
    )
    mac = hmac.new(key, prefix + body, hashlib.sha256).digest()
    return prefix + mac + body


def _deserialize_frame(raw: bytes) -> CanFrame:
    (
        arbitration_id,
        timestamp_ns,
        hardware_timestamp_ns,
        host_timestamp_ns,
        sequence,
        flags,
        dlc,
        error_state_code,
        source_code,
        channel_len,
        reserved,
        channel_raw,
        data_len,
        data_raw,
    ) = struct.unpack(FRAME_FMT, raw)

    if flags & _FLAG_RESERVED_MASK:
        raise SecurityError("Reserved frame flag bits are set", code="SECURITY_ERROR")
    if reserved != 0:
        raise SecurityError("Reserved frame field is non-zero", code="SECURITY_ERROR")
    if channel_len > CHANNEL_ID_SIZE:
        raise SecurityError("Invalid channel identifier length", code="SECURITY_ERROR")
    if data_len > DATA_SIZE:
        raise SecurityError("Invalid CAN payload length", code="SECURITY_ERROR")

    try:
        channel_id = channel_raw[:channel_len].decode("utf-8", errors="strict")
        error_state = _CODE_TO_ERROR_STATE[error_state_code]
        source = _CODE_TO_SOURCE[source_code]
        return CanFrame(
            channel_id=channel_id,
            arbitration_id=arbitration_id,
            dlc=dlc,
            data=bytes(data_raw[:data_len]),
            is_extended=bool(flags & FLAG_IS_EXTENDED),
            is_fd=bool(flags & FLAG_IS_FD),
            brs=bool(flags & FLAG_BRS),
            esi=bool(flags & FLAG_ESI),
            direction="tx" if flags & FLAG_DIRECTION_TX else "rx",
            timestamp_ns=timestamp_ns,
            hardware_timestamp_ns=(
                hardware_timestamp_ns if flags & FLAG_HAS_HARDWARE_TIMESTAMP else None
            ),
            host_timestamp_ns=host_timestamp_ns if flags & FLAG_HAS_HOST_TIMESTAMP else None,
            sequence=sequence,
            error_state=error_state,
            source=source,
        )
    except (KeyError, UnicodeDecodeError, ValueError) as exc:
        raise SecurityError("Invalid frame data in rolling disk chunk", code="SECURITY_ERROR", cause=exc) from exc


def _deserialize_chunk(raw: bytes, key: bytes) -> list[CanFrame]:
    if len(key) != 32:
        raise SecurityError("Rolling disk HMAC key has invalid length", code="SECURITY_ERROR")
    if len(raw) < HEADER_SIZE:
        raise SecurityError("Rolling disk chunk is too short", code="SECURITY_ERROR")

    magic, version, header_flags, count, record_size, stored_mac = struct.unpack(
        HEADER_FMT, raw[:HEADER_SIZE]
    )
    if magic != CHUNK_MAGIC:
        raise SecurityError("Invalid rolling disk chunk magic", code="SECURITY_ERROR")
    if version != CHUNK_VERSION:
        raise SecurityError(f"Unsupported rolling disk chunk version {version}", code="SECURITY_ERROR")
    if header_flags != 0:
        raise SecurityError("Reserved chunk header flags are set", code="SECURITY_ERROR")
    if record_size != FRAME_SIZE:
        raise SecurityError("Unexpected rolling disk frame size", code="SECURITY_ERROR")

    expected_size = HEADER_SIZE + count * FRAME_SIZE
    if len(raw) != expected_size:
        raise SecurityError("Rolling disk chunk length does not match its header", code="SECURITY_ERROR")

    prefix = raw[: struct.calcsize(HEADER_PREFIX_FMT)]
    body = raw[HEADER_SIZE:]
    calculated_mac = hmac.new(key, prefix + body, hashlib.sha256).digest()
    if not hmac.compare_digest(calculated_mac, stored_mac):
        raise SecurityError("Rolling disk chunk HMAC mismatch", code="SECURITY_ERROR")

    return [
        _deserialize_frame(body[offset : offset + FRAME_SIZE])
        for offset in range(0, len(body), FRAME_SIZE)
    ]


class RollingDiskBuffer:
    """Rolling compressed chunk buffer with time, size, and integrity bounds."""

    CHUNK_THRESHOLD_FRAMES: ClassVar[int] = 25_000
    MAX_RETENTION_SEC: ClassVar[int] = 600
    MAX_DISK_BYTES: ClassVar[int] = 100 * 1024 * 1024

    _serialize_chunk = staticmethod(_serialize_chunk)

    def __init__(
        self,
        storage_dir: str | Path,
        max_retention_sec: int = MAX_RETENTION_SEC,
        max_disk_bytes: int = MAX_DISK_BYTES,
        chunk_frame_threshold: int = CHUNK_THRESHOLD_FRAMES,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        if chunk_frame_threshold <= 0:
            raise ValueError("chunk_frame_threshold must be positive")

        self.storage_dir = Path(storage_dir)
        self.max_retention_sec = max_retention_sec
        self.max_disk_bytes = max_disk_bytes
        self.chunk_frame_threshold = chunk_frame_threshold
        self._secret_provider = secret_provider or get_default_secret_provider()

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tls = threading.local()
        self._closed = False
        self._current_chunk_frames: list[CanFrame] = []
        # E10: count of frames rejected at append-time for malformed content
        self._rejected_frames: int = 0
        self._cctx = zstd.ZstdCompressor(level=3)
        self._dctx = zstd.ZstdDecompressor()
        max_read_frames = max(chunk_frame_threshold, self.CHUNK_THRESHOLD_FRAMES)
        self._max_chunk_bytes = HEADER_SIZE + max_read_frames * FRAME_SIZE
        self._migrate_legacy_chunks()
        self._chunk_index = self._next_chunk_index()
        # F-34: bounded async write queue + worker thread. Serialize + HMAC stay
        # on the caller thread (single-writer ordering); only compression and
        # disk IO are offloaded. Queue full => caller writes synchronously
        # (documented drop-free fallback, preferable to losing telemetry).
        self._flush_queue: queue.Queue[tuple[Path, bytes] | None] = queue.Queue(maxsize=16)
        self._flush_worker = threading.Thread(
            target=self._flush_worker_loop, name="rolling_disk_flush", daemon=True
        )
        self._flush_worker.start()

    def _next_chunk_index(self) -> int:
        highest_index = -1
        for path in self.storage_dir.glob("chunk_*.bin.zst"):
            try:
                highest_index = max(highest_index, int(path.name.split("_", 2)[1]))
            except (IndexError, ValueError):
                continue
        return highest_index + 1

    def _read_uncompressed_prefix(self, path: Path, size: int) -> bytes:
        with path.open("rb") as compressed_file:
            with self._dctx.stream_reader(compressed_file) as reader:
                return reader.read(size)

    def _migrate_legacy_chunks(self) -> None:
        legacy_dir = self.storage_dir / "legacy_pickle"
        for path in sorted(self.storage_dir.glob("chunk_*.bin.zst")):
            try:
                prefix = self._read_uncompressed_prefix(path, 2)
            except (OSError, zstd.ZstdError):
                continue
            if len(prefix) != 2 or prefix[0] != 0x80 or prefix[1] > 5:
                continue

            legacy_dir.mkdir(parents=True, exist_ok=True)
            destination = legacy_dir / path.name
            collision_index = 1
            while destination.exists():
                destination = legacy_dir / f"{path.name}.{collision_index}"
                collision_index += 1
            path.replace(destination)
            logger.warning(
                "Moved unauthenticated legacy rolling disk chunk out of the active store",
                extra={"source": str(path), "destination": str(destination)},
            )

    def append(self, frame: CanFrame) -> None:
        """Add a frame and flush when the configured chunk threshold is reached.

        E10: a malformed frame (oversized channel/payload, out-of-range field)
        would raise ValueError from serialization at flush time and kill the
        ingestion loop. Reject it here, before it enters the chunk, and keep
        the blackbox recording — one bad frame must not stop the recorder.
        """
        try:
            _serialize_frame(frame)
        except ValueError as exc:
            with self._lock:
                self._rejected_frames += 1
            logger.warning(
                "Rolling disk rejected malformed frame",
                extra={"error": str(exc), "total_rejected": self._rejected_frames},
            )
            return

        with self._lock:
            self._current_chunk_frames.append(frame)
            should_flush = len(self._current_chunk_frames) >= self.chunk_frame_threshold
        if should_flush:
            self.flush(drain=(self.chunk_frame_threshold <= 10))

    def flush(self, drain: bool = True) -> Path | None:
        """Authenticate and enqueue the pending chunk for async disk write (F-34).

        Serialization + HMAC run on the caller thread (single-writer
        ordering); compression and disk IO are offloaded to the worker.
        Queue full => synchronous fallback write (documented in module doc).
        """
        with self._lock:
            if not self._current_chunk_frames:
                return None
            frames = self._current_chunk_frames
            self._current_chunk_frames = []
            chunk_idx = self._chunk_index
            self._chunk_index += 1

        key = _get_hmac_key(self._secret_provider)
        try:
            raw_bytes = self._serialize_chunk(frames, key)
        except ValueError as exc:
            # E10 defense-in-depth: append() already validates frames, but a
            # frame mutated after admission must not kill the ingestion loop —
            # drop the whole pending chunk (it cannot be partially trusted)
            # and keep recording the next one.
            with self._lock:
                self._rejected_frames += len(frames)
            logger.error(
                "Rolling disk dropped unserializable pending chunk",
                extra={"error": str(exc), "frames": len(frames)},
            )
            return None
        timestamp_ns = frames[0].timestamp_ns
        chunk_file = self.storage_dir / (
            f"chunk_{chunk_idx:08d}_{timestamp_ns:020d}.bin.zst"
        )

        try:
            self._flush_queue.put_nowait((chunk_file, raw_bytes))
        except queue.Full:
            # Bounded queue full: write synchronously rather than drop frames
            self._write_chunk(chunk_file, raw_bytes)
        else:
            if drain:
                self._drain_flush_queue(timeout_s=30.0)

        logger.debug(
            "Enqueued rolling disk chunk",
            extra={
                "file": chunk_file.name,
                "frames": len(frames),
                "uncompressed_kb": len(raw_bytes) // 1024,
            },
        )
        return chunk_file

    def _write_chunk(self, chunk_file: Path, raw_bytes: bytes) -> bytes:
        """Compress and atomically persist one serialized chunk; returns bytes written.

        E-C-003: durability sequence write -> flush -> fsync -> replace, so
        a power cut after the rename can never leave a truncated chunk
        shadowing the kara kutu (black-box) recording.
        """
        cctx = getattr(self._tls, "cctx", None)
        if cctx is None:
            cctx = zstd.ZstdCompressor(level=3)
            self._tls.cctx = cctx
        compressed_bytes = cctx.compress(raw_bytes)
        temporary_file = chunk_file.with_name(
            f"{chunk_file.name}.{os.getpid()}_{threading.get_ident()}_{time.monotonic_ns()}.tmp"
        )
        with open(temporary_file, "wb") as f:
            f.write(compressed_bytes)
            f.flush()
            os.fsync(f.fileno())
        temporary_file.replace(chunk_file)
        self._enforce_retention()
        return compressed_bytes

    def _flush_worker_loop(self) -> None:
        """Worker loop: drain the flush queue, compress, and write to disk (F-34).

        K-04: the loop must survive ANY exception — a dead worker silently
        strands every queued chunk (data loss + unbounded queue growth).
        IO/compression failures quarantine the chunk as ``.failed``; truly
        unexpected errors are logged with traceback and the worker keeps
        running after a short backoff.
        """
        while True:
            item = self._flush_queue.get()
            if item is None:
                self._flush_queue.task_done()
                return
            chunk_file, raw_bytes = item
            try:
                self._write_chunk(chunk_file, raw_bytes)
            except (OSError, zstd.ZstdError) as exc:
                logger.error(
                    "Async rolling disk chunk write failed",
                    extra={"file": str(chunk_file), "error": str(exc)},
                )
                self._quarantine_failed_chunk(chunk_file)
            except Exception as exc:  # noqa: BLE001 — worker must never die
                logger.critical(
                    "Unexpected error in rolling disk flush worker; continuing",
                    extra={"file": str(chunk_file), "error": str(exc)},
                    exc_info=True,
                )
                self._quarantine_failed_chunk(chunk_file)
                time.sleep(1.0)
            finally:
                self._flush_queue.task_done()

    @staticmethod
    def _quarantine_failed_chunk(chunk_file: Path) -> None:
        """Move an unwritable chunk aside so retention cannot re-select it."""
        try:
            quarantine_path = chunk_file.with_suffix(chunk_file.suffix + ".failed")
            if chunk_file.exists():
                chunk_file.replace(quarantine_path)
        except OSError:
            # Even quarantine failed (disk full?): leave the file in place —
            # retention and human operators will deal with it.
            pass

    def _drain_flush_queue(self, timeout_s: float) -> None:
        """Block until the worker catches up (used by read paths for consistency)."""
        deadline = time.monotonic() + timeout_s
        while self._flush_queue.unfinished_tasks > 0:
            if time.monotonic() > deadline:
                logger.warning(
                    "Rolling disk flush queue drain timed out",
                    extra={"pending": self._flush_queue.unfinished_tasks},
                )
                return
            time.sleep(0.005)

    def _enforce_retention(self) -> None:
        """Purge chunks exceeding max time retention or total disk byte budget."""
        cutoff_time = time.time() - self.max_retention_sec
        chunk_files = sorted(self.storage_dir.glob("chunk_*.bin.zst"))
        total_bytes = 0
        valid_files: list[Path] = []

        for file in chunk_files:
            try:
                stat = file.stat()
                if stat.st_mtime < cutoff_time:
                    file.unlink(missing_ok=True)
                else:
                    valid_files.append(file)
                    total_bytes += stat.st_size
            except OSError as exc:
                logger.warning(
                    "Unable to inspect rolling disk chunk during retention",
                    extra={"file": str(file), "error": str(exc)},
                )

        while total_bytes > self.max_disk_bytes and valid_files:
            oldest = valid_files.pop(0)
            try:
                size = oldest.stat().st_size
                oldest.unlink(missing_ok=True)
                total_bytes -= size
            except OSError as exc:
                logger.warning(
                    "Unable to remove rolling disk chunk during retention",
                    extra={"file": str(oldest), "error": str(exc)},
                )

    def _decompress_bounded(self, compressed_bytes: bytes) -> bytes:
        try:
            with self._dctx.stream_reader(io.BytesIO(compressed_bytes)) as reader:
                raw_bytes = reader.read(self._max_chunk_bytes + 1)
                if len(raw_bytes) > self._max_chunk_bytes or reader.read(1):
                    raise SecurityError(
                        "Rolling disk chunk exceeds decompression limit",
                        code="SECURITY_ERROR",
                    )
                return raw_bytes
        except zstd.ZstdError as exc:
            raise SecurityError(
                "Rolling disk chunk decompression failed",
                code="SECURITY_ERROR",
                cause=exc,
            ) from exc

    def read_all_stored_frames(self, quarantine_corrupt: bool = False) -> list[CanFrame]:
        """Read and authenticate all stored chunks in chronological order."""
        with self._lock:
            closed = self._closed
        if not closed:
            self.flush()
            # F-34: wait for async writes to land before reading the directory
            self._drain_flush_queue(timeout_s=30.0)
        key = _get_hmac_key(self._secret_provider)
        all_frames: list[CanFrame] = []

        for file in sorted(self.storage_dir.glob("chunk_*.bin.zst")):
            try:
                raw_bytes = self._decompress_bounded(file.read_bytes())
                all_frames.extend(_deserialize_chunk(raw_bytes, key))
            except SecurityError as sec_exc:
                if quarantine_corrupt:
                    logger.critical(
                        "Quarantining corrupt or untrusted rolling disk chunk",
                        extra={"file": str(file), "error": str(sec_exc)},
                    )
                    quarantine_target = file.with_suffix(file.suffix + ".corrupt")
                    try:
                        file.replace(quarantine_target)
                    except OSError:
                        pass
                    continue
                # Re-raise to ensure tamper detection tests pass, but preserve caller context
                raise
            except OSError as exc:
                logger.error(
                    "Failed to read rolling disk chunk",
                    extra={"file": str(file), "error": str(exc)},
                )

        return all_frames

    def read_all_stored_frames_after_close(self) -> list[CanFrame]:
        """Convenience method to read frames after buffer has been closed."""
        return self.read_all_stored_frames()

    def close(self, timeout_s: float = 10.0) -> None:
        """Flushes remaining frames, drains the queue, and shuts down the worker thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.flush()
        try:
            self._flush_queue.put(None, timeout=timeout_s)
        except (queue.Full, Exception):
            pass
        if self._flush_worker.is_alive():
            self._flush_worker.join(timeout=timeout_s)

    def clear(self) -> None:
        """Delete active authenticated chunks from the storage directory."""
        self._drain_flush_queue(timeout_s=10.0)
        with self._lock:
            self._current_chunk_frames = []
            self._chunk_index = 0
            self._rejected_frames = 0
        for file in self.storage_dir.glob("chunk_*.bin.zst"):
            file.unlink(missing_ok=True)
        for file in self.storage_dir.glob("*.tmp"):
            file.unlink(missing_ok=True)

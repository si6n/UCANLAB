"""High-Performance Bounded Binary Ring Buffer for CAN/CAN-FD frames.

Allocates contiguous memory for 300,000 frames (60s @ 5,000 msg/s ≈ 28 MB RAM).
Per-frame Python work is a fixed NumPy record view + payload slice write —
small but nonzero, so batching (append_batch) is preferred on hot paths.
"""

from __future__ import annotations

import threading
from typing import ClassVar

import numpy as np

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame

logger = get_logger("engine.buffer.ring_buffer")

# Pre-defined NumPy structured dtype for fixed 80-byte frame representation
CAN_RECORD_DTYPE = np.dtype(
    [
        ("timestamp_ns", np.uint64),
        ("arbitration_id", np.uint32),
        ("dlc", np.uint8),
        ("flags", np.uint8),  # bit0: is_extended, bit1: is_fd, bit2: brs, bit3: esi, bit4: is_tx
        ("data_len", np.uint8),
        ("reserved", np.uint8),
        ("channel_id_int", np.uint16),
        ("data", np.uint8, (64,)),
    ],
    align=True,
)


class BinaryRingBuffer:
    """Contiguous, pre-allocated in-memory circular buffer for high-speed telemetry."""

    DEFAULT_CAPACITY: ClassVar[int] = 300_000  # 60 seconds @ 5,000 msg/s

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError(f"Ring buffer capacity must be positive, got {capacity}")

        self.capacity = capacity
        self._buffer = np.zeros(capacity, dtype=CAN_RECORD_DTYPE)
        self._channel_map: dict[str, int] = {}
        self._rev_channel_map: dict[int, str] = {}
        self._head = 0  # Write pointer (modulo capacity)
        self._total_written = 0  # Monotonically increasing counter
        self._lock = threading.Lock()

    def _get_channel_int(self, channel_id: str) -> int:
        """Map channel string to 16-bit unsigned integer ID."""
        with self._lock:
            val = self._channel_map.get(channel_id)
            if val is None:
                return self._intern_channel_unlocked(channel_id)
            return val

    def _get_channel_str(self, channel_int: int) -> str:
        """Map 16-bit integer ID back to channel string."""
        return self._rev_channel_map.get(channel_int, f"ch_{channel_int}")

    def _store_frame_unlocked(self, frame: CanFrame) -> int:
        """Write one frame record at the head position and advance.

        Caller must hold self._lock. Returns the assigned sequence number.

        Perf: a single structured-array tuple assignment replaces eight
        per-field NumPy scalar writes (each a separate __setitem__ call);
        channel lookup is one dict access instead of a guarded method call.
        The data tail beyond data_len is deliberately NOT zeroed here —
        readers slice records with data_len (see get_latest_frames), so the
        stale tail bytes are never observable.
        """
        idx = self._head
        flags = (
            (1 if frame.is_extended else 0)
            | ((1 if frame.is_fd else 0) << 1)
            | ((1 if frame.brs else 0) << 2)
            | ((1 if frame.esi else 0) << 3)
            | ((1 if frame.direction == "tx" else 0) << 4)
        )

        data = frame.data
        data_len = len(data)
        if data_len > 64:
            data_len = 64

        channel_id = frame.channel_id
        ch_int = self._channel_map.get(channel_id)
        if ch_int is None:
            ch_int = self._intern_channel_unlocked(channel_id)

        padded = data.ljust(64, b"\x00") if data_len < 64 else data[:64]
        self._buffer[idx] = (
            frame.timestamp_ns,
            frame.arbitration_id,
            frame.dlc,
            flags,
            data_len,
            0,
            ch_int,
            np.frombuffer(padded, dtype=np.uint8),
        )

        seq = self._total_written
        self._head = (self._head + 1) % self.capacity
        self._total_written += 1
        return seq

    def _intern_channel_unlocked(self, channel_id: str) -> int:
        """Register a new channel string and return its 16-bit integer ID.

        Caller must hold self._lock.
        """
        if len(self._channel_map) >= 0xFFFF:
            logger.warning("RingBuffer channel map exceeded 16-bit capacity (65535 channels)")
        val = len(self._channel_map) & 0xFFFF
        self._channel_map[channel_id] = val
        self._rev_channel_map[val] = channel_id
        return val

    def append(self, frame: CanFrame) -> int:
        """Append a single CanFrame into contiguous memory. Returns write sequence."""
        with self._lock:
            return self._store_frame_unlocked(frame)

    def append_batch(self, frames: list[CanFrame]) -> int:
        """Append a batch under a single lock acquisition. Returns total written."""
        with self._lock:
            for frame in frames:
                self._store_frame_unlocked(frame)
            return self._total_written

    @property
    def total_written(self) -> int:
        with self._lock:
            return self._total_written

    @property
    def current_size(self) -> int:
        with self._lock:
            return min(self._total_written, self.capacity)

    def get_latest_view(
        self, count: int, *, copy: bool = True
    ) -> tuple[np.ndarray, np.ndarray]:
        """Wrap-around safe view access over the shared buffer storage.

        E-C-001 (TOCTOU): with the default ``copy=True`` the returned parts
        are detached copies — a concurrent writer can never mutate bytes a
        reader is holding after the lock was released. ``copy=False``
        preserves the genuine zero-copy views (F-33/E-13) for hot-path
        consumers that read under the same lock discipline.

        Returns (oldest_part, newest_part).
        """
        with self._lock:
            available = min(self._total_written, self.capacity)
            n = min(count, available)
            if n <= 0:
                return self._buffer[0:0], self._buffer[0:0]

            start_seq = self._total_written - n
            s0 = start_seq % self.capacity
            e0 = self._total_written % self.capacity

            if s0 < e0 or e0 == 0:
                # Contiguous range (includes the exactly-full wrap edge case)
                end = e0 if e0 != 0 else self.capacity
                old_part, new_part = self._buffer[s0:end], self._buffer[0:0]
            else:
                # Wrapped: oldest tail segment + newest head segment
                old_part, new_part = self._buffer[s0:], self._buffer[:e0]

            if copy:
                return old_part.copy(), new_part.copy()
            return old_part, new_part

    def get_latest_frames(self, count: int) -> list[CanFrame]:
        """Fetch latest N frames in chronological order."""
        with self._lock:
            available = min(self._total_written, self.capacity)
            n = min(count, available)
            if n <= 0:
                return []

            # Calculate slice indices
            start_seq = self._total_written - n
            frames: list[CanFrame] = []

            for seq in range(start_seq, self._total_written):
                idx = seq % self.capacity
                rec = self._buffer[idx]
                flags = int(rec["flags"])
                data_len = int(rec["data_len"])
                raw_data = rec["data"][:data_len].tobytes()

                frame = CanFrame(
                    channel_id=self._get_channel_str(int(rec["channel_id_int"])),
                    arbitration_id=int(rec["arbitration_id"]),
                    dlc=int(rec["dlc"]),
                    data=raw_data,
                    is_extended=bool(flags & 0x01),
                    is_fd=bool(flags & 0x02),
                    brs=bool(flags & 0x04),
                    esi=bool(flags & 0x08),
                    direction="tx" if bool(flags & 0x10) else "rx",
                    timestamp_ns=int(rec["timestamp_ns"]),
                    sequence=seq,
                )
                frames.append(frame)

            return frames

    def clear(self) -> None:
        """Reset ring buffer pointers and channel mappings."""
        with self._lock:
            self._head = 0
            self._total_written = 0
            self._buffer.fill(0)
            self._channel_map.clear()
            self._rev_channel_map.clear()

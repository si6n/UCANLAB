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
# REVIEW (provenance round-trip): the record used to drop source and
# error_state entirely — a replay/bus-off frame re-emerged as
# physical/active and the reconstructed `sequence` was the buffer write
# index, not the original. flags bits 5/6/7 now carry
# source (2 bits: physical/replay/virtual/injected|synthetic map below)
# and bit 7 error_state (0 active, 1 bus_off); sequence stays a buffer
# write index (documented) because the original per-frame sequence is not
# recoverable without widening the 80-byte record — the reconstruction
# now marks `source` and `error_state` faithfully.
_SOURCE_TO_FLAG_BITS = {"physical": 0, "replay": 1, "virtual": 2, "injected": 3, "synthetic": 3}
_FLAG_BITS_TO_SOURCE = {0: "physical", 1: "replay", 2: "virtual", 3: "injected"}
_CAN_RECORD_DTYPE_FIELDS = [
    ("timestamp_ns", np.uint64),
    ("arbitration_id", np.uint32),
    ("dlc", np.uint8),
    ("flags", np.uint8),  # bit0: is_extended, bit1: is_fd, bit2: brs, bit3: esi, bit4: is_tx, bit5-6: source, bit7: bus_off
    ("data_len", np.uint8),
    ("reserved", np.uint8),
    ("channel_id_int", np.uint16),
    ("data", np.uint8, (64,)),
]
CAN_RECORD_DTYPE = np.dtype(_CAN_RECORD_DTYPE_FIELDS, align=True)


class BinaryRingBuffer:
    """Contiguous, pre-allocated in-memory circular buffer for high-speed telemetry."""

    DEFAULT_CAPACITY: ClassVar[int] = 300_000  # 60 seconds @ 5,000 msg/s

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool) or not (1 <= capacity <= 1_000_000):
            raise ValueError(f"Ring buffer capacity must be in range 1..1000000, got {capacity}")

        self.capacity = capacity
        self._buffer = np.zeros(capacity, dtype=CAN_RECORD_DTYPE)
        self._channel_map: dict[str, int] = {}
        self._rev_channel_map: dict[int, str] = {}
        # RB-1: coherent (int -> name) snapshot refreshed under the lock on
        # every append; get_latest_frames materializes channel names from it
        # instead of a lock-free dict read that raced with clear().
        self._rev_channel_snapshot: dict[int, str] = {}
        self._head = 0  # Write pointer (modulo capacity)
        self._total_written = 0  # Monotonically increasing counter
        # REVIEW 3-MEDIUM: channels refused a 16-bit slot (aliased to 0xFFFF)
        self._channel_overflow_count = 0
        self._lock = threading.Lock()

    def _get_channel_int(self, channel_id: str) -> int:
        """Map channel string to 16-bit unsigned integer ID."""
        with self._lock:
            val = self._channel_map.get(channel_id)
            if val is None:
                return self._intern_channel_unlocked(channel_id)
            return val

    def _get_channel_str(self, channel_int: int) -> str:
        """Map 16-bit integer ID back to channel string (RB-1: snapshot variant).

        Callers that already hold a coherent ``_rev_channel_map`` snapshot
        (taken under ``_lock``) must use the snapshot directly — this lock-free
        helper is only for live/unsynchronised call sites.
        """
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

        RB-1: the reverse channel map is read INSIDE the same critical section
        as the record write, so the snapshot returned to get_latest_frames is
        coherent — a concurrent clear() can no longer blank the map between
        the record write and the string lookup, which used to relabel every
        frame `ch_<n>`.
        """
        idx = self._head
        flags = (
            (1 if frame.is_extended else 0)
            | ((1 if frame.is_fd else 0) << 1)
            | ((1 if frame.brs else 0) << 2)
            | ((1 if frame.esi else 0) << 3)
            | ((1 if frame.direction == "tx" else 0) << 4)
            # REVIEW (provenance): source + bus_off survive the round-trip
            # (physical/replay/virtual/injected; bus_off vs active).
            | (_SOURCE_TO_FLAG_BITS.get(frame.source, 0) << 5)
            | ((1 if frame.error_state == "bus_off" else 0) << 7)
        )

        data = frame.data
        data_len = len(data)
        if data_len > 64:
            data_len = 64

        channel_id = frame.channel_id
        ch_int = self._channel_map.get(channel_id)
        if ch_int is None:
            ch_int = self._intern_channel_unlocked(channel_id)

        # RB-1: detach a coherent reverse-map snapshot while still holding
        # the lock, and keep it for get_latest_frames' record materialization.
        self._rev_channel_snapshot = dict(self._rev_channel_map)

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

        L-13 (P3-10): the 16-bit ID space is CLOSED at 65535 channels — the
        old wrap (`len(map) & 0xFFFF`) silently aliased new channels onto
        existing IDs, corrupting every trace and export that referenced
        them. Excess channels now share the overflow slot but the condition
        is raised loudly instead of wrapping silently.
        """
        if len(self._channel_map) >= 0xFFFF:
            # Fail loud, not silent: log once per overflowing channel but
            # keep the buffer alive (a telemetry storm of unique channel
            # strings must not kill recording). All overflow channels alias
            # the 0xFFFF sentinel so corrupted exports are at least
            # detectable as channel 65535. REVIEW 3-MEDIUM: each overflow
            # increments the exposed counter (channel_map_stats) so the
            # operator gets a net capacity/overrun report.
            if channel_id not in self._channel_map:
                logger.error(
                    "RingBuffer channel map exceeded 16-bit capacity — aliasing to sentinel 0xFFFF",
                    extra={"channel_id": channel_id, "capacity": 0xFFFF},
                )
                self._channel_overflow_count += 1
            self._channel_map[channel_id] = 0xFFFF
            return 0xFFFF
        val = len(self._channel_map)
        self._channel_map[channel_id] = val
        self._rev_channel_map[val] = channel_id
        return val

    def append(self, frame: CanFrame) -> int:
        """Append a single CanFrame into contiguous memory. Returns write sequence."""
        with self._lock:
            return self._store_frame_unlocked(frame)

    # Friendly alias for compatibility with queue/buffer abstractions
    push = append

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
    ) -> tuple[np.ndarray, np.ndarray, int]:
        """Wrap-around safe view access over the shared buffer storage.

        E-C-001 (TOCTOU): with the default ``copy=True`` the returned parts
        are detached copies — a concurrent writer can never mutate bytes a
        reader is holding after the lock was released. ``copy=False``
        preserves the genuine zero-copy views (F-33/E-13) for hot-path
        consumers that read under the same lock discipline.

        REVIEW TOCTOU fix: returns ``(oldest_part, newest_part,
        total_written_snapshot)`` — the writer counter is captured in the
        SAME critical section as the parts so consumers can derive coherent
        sequence labels instead of re-reading it lock-free later (a
        concurrent append between the two reads shifted every exported
        frame's ``sequence``).
        """
        with self._lock:
            available = min(self._total_written, self.capacity)
            n = min(count, available)
            total_written_snapshot = self._total_written
            if n <= 0:
                return self._buffer[0:0], self._buffer[0:0], total_written_snapshot

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
                return old_part.copy(), new_part.copy(), total_written_snapshot
            return old_part, new_part, total_written_snapshot

    def get_latest_frames(self, count: int) -> list[CanFrame]:
        """Fetch latest N frames in chronological order.

        H-13 (P2-15): the CanFrame construction happens OUTSIDE the buffer
        lock. The old implementation materialized up to `capacity` (300k)
        Python objects under self._lock, stalling every telemetry append for
        seconds during exports; the snapshot is now taken via
        get_latest_view(copy=True) — two fast NumPy copies under the lock —
        and the object construction runs lock-free.

        REVIEW TOCTOU fix: ``n`` and ``base_seq`` are derived from the SAME
        snapshot's total_written, never from a later lock-free re-read.
        """
        # Lock scope: bounded, two-array snapshot + coherent writer counter.
        old_part, new_part, total_written_snapshot = self.get_latest_view(count, copy=True)

        # n is derived from the snapshot's writer state, not a fresh re-read.
        n = min(count, min(total_written_snapshot, self.capacity))
        if n <= 0:
            return []

        # Chronological order: old tail segment first, then new head segment.
        records = list(old_part) + list(new_part)
        records = records[-n:]

        # RB-1: the channel-name map is captured from the coherent append-time
        # snapshot, not by a lock-free read of the live dict (which a
        # concurrent clear() could blank mid-materialization, relabelling
        # every frame `ch_<n>`).
        rev_channel_snapshot = self._rev_channel_snapshot

        # Lock-free materialization, sequence labels coherent with the snapshot.
        frames: list[CanFrame] = []
        base_seq = total_written_snapshot - n
        for offset, rec in enumerate(records):
            flags = int(rec["flags"])
            data_len = int(rec["data_len"])
            raw_data = rec["data"][:data_len].tobytes()

            channel_int = int(rec["channel_id_int"])
            frame = CanFrame(
                channel_id=rev_channel_snapshot.get(channel_int, f"ch_{channel_int}"),
                arbitration_id=int(rec["arbitration_id"]),
                dlc=int(rec["dlc"]),
                data=raw_data,
                is_extended=bool(flags & 0x01),
                is_fd=bool(flags & 0x02),
                brs=bool(flags & 0x04),
                esi=bool(flags & 0x08),
                direction="tx" if bool(flags & 0x10) else "rx",
                # REVIEW (provenance): source + error_state are restored
                # from the flag bits instead of falling back to the
                # physical/active defaults.
                error_state="bus_off" if bool(flags & 0x80) else "active",
                source=_FLAG_BITS_TO_SOURCE.get((flags >> 5) & 0x03, "physical"),
                timestamp_ns=int(rec["timestamp_ns"]),
                sequence=base_seq + offset,
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
            # RB-1: keep the append-time snapshot coherent with the cleared
            # map instead of leaving a stale mapping behind.
            self._rev_channel_snapshot = {}
            self._channel_overflow_count = 0

    @property
    def channel_map_stats(self) -> dict[str, int]:
        """REVIEW 3-MEDIUM: explicit channel-map capacity report.

        The 16-bit map closes at 65535 entries (L-13); overflow channels are
        aliased to the 0xFFFF sentinel. Exposes the exact live/overflow split
        so operators and exports can detect aliasing instead of guessing.
        """
        with self._lock:
            live = sum(1 for v in self._channel_map.values() if v != 0xFFFF)
            return {
                "capacity": 0xFFFF,
                "live_channels": live,
                "overflow_channels": self._channel_overflow_count,
                "aliased_to_sentinel": len(self._channel_map) - live,
            }

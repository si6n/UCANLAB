"""ReplayBus Deterministic Playback Engine for Testing and Simulation."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.hal.replay.parsers import CsvParser, VectorAscParser, VectorBlfParser

logger = get_logger("hal.replay")


class ReplayBus:
    """Deterministic in-memory and file-based CAN traffic replay engine."""

    def __init__(self, frames: Sequence[CanFrame] | None = None) -> None:
        self._frames: list[CanFrame] = list(frames) if frames is not None else []
        self._index: int = 0
        # REVIEW 3: play()/step()/load_frames() are reached from both the
        # replay worker thread and the UI thread — an RLock makes every
        # state transition atomic so a concurrent load_frames can never
        # tear an in-flight iteration (IndexError mid-playback). RLock
        # because play() re-enters through reset()/step().
        self._lock = threading.RLock()
        # REVIEW 3: consumer callback failures surface as metrics instead
        # of silently killing the replay worker thread.
        self.callback_errors: int = 0
        self.dropped_frames: int = 0

    @classmethod
    def from_asc_file(cls, file_path: str | Path) -> ReplayBus:
        """Create ReplayBus instance loaded directly from a Vector ASCII trace file."""
        frames = VectorAscParser.parse_file(file_path)
        logger.info("Loaded trace into ReplayBus", extra={"file": str(file_path), "frame_count": len(frames)})
        return cls(frames)

    @classmethod
    def from_csv_file(cls, file_path: str | Path) -> ReplayBus:
        """Create ReplayBus instance loaded from a header-based CSV trace (K3-a)."""
        frames = CsvParser.parse_file(file_path)
        logger.info("Loaded CSV trace into ReplayBus", extra={"file": str(file_path), "frame_count": len(frames)})
        return cls(frames)

    @classmethod
    def from_blf_file(cls, file_path: str | Path) -> ReplayBus:
        """Create ReplayBus instance loaded from a Vector BLF binary trace file (K3-b)."""
        frames = VectorBlfParser.parse_file(file_path)
        logger.info("Loaded BLF trace into ReplayBus", extra={"file": str(file_path), "frame_count": len(frames)})
        return cls(frames)

    @classmethod
    def from_trace_file(cls, file_path: str | Path) -> ReplayBus:
        """Load a trace by file extension: .asc → Vector ASCII, .csv → CSV, .blf → Vector BLF.

        Unknown extensions raise ValueError.
        """
        suffix = Path(file_path).suffix.lower()
        if suffix == ".asc":
            return cls.from_asc_file(file_path)
        if suffix == ".csv":
            return cls.from_csv_file(file_path)
        if suffix == ".blf":
            return cls.from_blf_file(file_path)
        raise ValueError(
            f"Unsupported trace format '{suffix or '(none)'}' — supported: .asc, .csv, .blf"
        )

    def load_frames(self, frames: Sequence[CanFrame]) -> None:
        """Replace current frame sequence with new frames and reset pointer."""
        with self._lock:
            self._frames = list(frames)
            self._index = 0

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)

    @property
    def has_next(self) -> bool:
        with self._lock:
            return self._index < len(self._frames)

    def step(self) -> CanFrame | None:
        """Advance one frame deterministically. Returns None when end of trace is reached."""
        with self._lock:
            if not self.has_next:
                return None
            frame = self._frames[self._index]
            self._index += 1
            return frame

    def reset(self) -> None:
        """Reset playhead pointer to the beginning of the trace."""
        with self._lock:
            self._index = 0

    def play(
        self,
        callback: Callable[[CanFrame], None],
        speed: float = 1.0,
        stop_event: Any | None = None,
        loop: bool = False,
    ) -> None:
        """Play through frames with accurate inter-frame timing delta.

        Supports looping playback with timestamp re-anchoring on rewind (H4).
        REVIEW 3: shared state is only touched under ``_lock`` (never while
        sleeping) and callback exceptions are counted, never propagated.
        """
        if not self._frames:
            return

        if (
            not isinstance(speed, (int, float))
            or isinstance(speed, bool)
            or not (0.01 <= float(speed) <= 100.0)
        ):
            raise ValueError(f"Replay speed must be positive and in range 0.01..100, got {speed!r}")
        speed = float(speed)

        while True:
            with self._lock:
                self.reset()
                if not self._frames:  # concurrently emptied via load_frames([])
                    return
                t_base_ns = self._frames[0].timestamp_ns
            t_base = time.perf_counter()

            while self.has_next:
                if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                    return

                frame = self.step()
                if frame is None:
                    break

                # REVIEW3 #17: replay deltas are clamped to >= 0 — a
                # wall-clock backward jump in a capture (NTP step, hibernate
                # resume) used to produce negative offsets that fired bursts,
                # and forward jumps produced multi-second stalls.
                target_offset_s = max(0.0, (frame.timestamp_ns - t_base_ns) / (1_000_000_000.0 * speed))
                target_time = t_base + target_offset_s

                # High precision hybrid sleep loop
                # REVIEW3 #14: the sub-500µs band used to busy-wait (0%
                # -> 100% of one core at high replay rates, starving the
                # ingest/watchdog threads under the GIL). A short sleep
                # yield keeps the loop off the core while retaining sub-ms
                # precision; stop_event responsiveness also improves.
                while True:
                    now = time.perf_counter()
                    remaining = target_time - now
                    if remaining <= 0:
                        break
                    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                        return
                    if remaining > 0.005:
                        time.sleep(remaining - 0.003)
                    elif remaining > 0.0005:
                        time.sleep(0.0002)
                    else:
                        # Sub-500µs band: yield the GIL instead of spinning.
                        time.sleep(0)

                # REVIEW 3: a raising consumer must not silently kill the
                # replay worker thread — count the drop, log the traceback,
                # and keep the deterministic playback running.
                try:
                    callback(frame)
                except Exception:
                    with self._lock:
                        self.callback_errors += 1
                        self.dropped_frames += 1
                    logger.exception(
                        "Replay callback raised; frame dropped",
                        extra={
                            "arb_id": frame.arbitration_id,
                            "callback_errors": self.callback_errors,
                        },
                    )

            if not loop or (stop_event and hasattr(stop_event, "is_set") and stop_event.is_set()):
                break

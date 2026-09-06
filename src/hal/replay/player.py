"""ReplayBus Deterministic Playback Engine for Testing and Simulation."""

from __future__ import annotations

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
        self._frames = list(frames)
        self._index = 0

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def has_next(self) -> bool:
        return self._index < len(self._frames)

    def step(self) -> CanFrame | None:
        """Advance one frame deterministically. Returns None when end of trace is reached."""
        if not self.has_next:
            return None
        frame = self._frames[self._index]
        self._index += 1
        return frame

    def reset(self) -> None:
        """Reset playhead pointer to the beginning of the trace."""
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
        """
        if not self._frames:
            return

        if speed <= 0:
            raise ValueError(f"Replay speed must be positive, got {speed}")

        while True:
            self.reset()
            t_base = time.perf_counter()
            t_base_ns = self._frames[0].timestamp_ns

            while self.has_next:
                if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                    return

                frame = self.step()
                if frame is None:
                    break

                target_offset_s = (frame.timestamp_ns - t_base_ns) / (1_000_000_000.0 * speed)
                target_time = t_base + target_offset_s

                # High precision hybrid sleep loop
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

                callback(frame)

            if not loop or (stop_event and hasattr(stop_event, "is_set") and stop_event.is_set()):
                break

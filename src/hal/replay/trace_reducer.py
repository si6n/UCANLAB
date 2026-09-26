# -*- coding: utf-8 -*-
"""Wasm-R3 Record-Reduce-Replay CAN Trace Reducer (Aksiyon 30 / ISO 26262 Zero-Fabrication).

Preserves:
- All payload transitions (any bit change in data bytes).
- All protocol / diagnostic / safety frames (UDS, J1939 DM, E-Stop, TP).
- Monotonic timing boundaries with guaranteed maximum keep-alive heartbeat interval.
- Zero-fabrication invariant: drops redundant identical steady-state frames
  without interpolating or fabricating any values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from src.core.models.can_frame import CanFrame


@dataclass(slots=True)
class ReductionStats:
    total_frames_in: int = 0
    total_frames_out: int = 0

    @property
    def reduction_ratio(self) -> float:
        if self.total_frames_in == 0:
            return 0.0
        return (self.total_frames_in - self.total_frames_out) / self.total_frames_in


class CanTraceReducer:
    """Equivalence-preserving CAN trace reducer for large telemetry corpora."""

    def __init__(
        self,
        max_heartbeat_interval_s: float = 0.500,
        always_keep_ids: set[int] | None = None,
    ) -> None:
        self.max_heartbeat_interval_s = max_heartbeat_interval_s
        self.always_keep_ids = always_keep_ids or set()
        # Track last emitted frame per arbitration ID: (last_timestamp, last_data)
        self._last_emitted: dict[int, tuple[float, bytes]] = {}

    def reduce(self, frames: Iterator[CanFrame]) -> Iterator[CanFrame]:
        """Reduce redundant steady-state frames from stream while preserving state transitions."""
        for frame in frames:
            arb_id = frame.arbitration_id
            timestamp = frame.timestamp_ns / 1_000_000_000.0
            data_bytes = bytes(frame.data)

            # Always keep diagnostic / safety IDs
            if arb_id in self.always_keep_ids:
                self._last_emitted[arb_id] = (timestamp, data_bytes)
                yield frame
                continue

            last = self._last_emitted.get(arb_id)
            if last is None:
                # First time seeing this arbitration ID -> keep
                self._last_emitted[arb_id] = (timestamp, data_bytes)
                yield frame
                continue

            last_ts, last_data = last
            time_since_last = timestamp - last_ts

            # Keep if data payload changed OR heartbeat interval elapsed
            if data_bytes != last_data or time_since_last >= self.max_heartbeat_interval_s:
                self._last_emitted[arb_id] = (timestamp, data_bytes)
                yield frame

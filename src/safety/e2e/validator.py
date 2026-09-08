"""Universal CAN-Bus Diagnostic & Telemetry Platform - E2E Safety Rx Validator.

Stateful Rx validation engine tracking alive and sequence counter progression, verifying CRC integrity,
detecting dropped or duplicated frames, and emitting formal functional safety verdicts (ISO 26262 ASIL-D).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import ClassVar

from src.core.models.can_frame import CanFrame
from src.safety.e2e.profiles import (
    E2EProfileConfig,
    E2EStatus,
    compute_checksum,
    extract_counter,
    extract_crc,
)


@dataclass(slots=True, frozen=True)
class E2EValidationResult:
    """Immutable result object emitted upon frame verification."""

    verdict: E2EStatus
    expected_crc: int
    actual_crc: int
    counter: int
    previous_counter: int | None
    delta: int
    stream_key: tuple[str, int]
    timestamp_ns: int

    @property
    def is_ok(self) -> bool:
        """True strictly when frame is healthy and consecutive (verdict is OK)."""
        return self.verdict == E2EStatus.OK

    @property
    def is_valid(self) -> bool:
        """True when frame payload is authentic and usable (OK, INITIAL, or SOME_LOST)."""
        return self.verdict in (E2EStatus.OK, E2EStatus.INITIAL, E2EStatus.SOME_LOST)

    @property
    def is_crc_valid(self) -> bool:
        """True if CRC/checksum matched expected value."""
        return self.expected_crc == self.actual_crc

    @property
    def is_sequence_valid(self) -> bool:
        """True if sequence counter followed expected progression without drops."""
        return self.verdict in (E2EStatus.OK, E2EStatus.INITIAL)


@dataclass(slots=True)
class StreamRxState:
    """Tracks sequence continuity and error metrics for a unique (channel, CAN ID) stream."""

    channel_id: str
    arbitration_id: int
    last_counter: int | None = None
    last_verdict: E2EStatus | None = None
    total_frames: int = 0
    valid_frames: int = 0
    crc_errors: int = 0
    sequence_errors: int = 0
    repeated_frames: int = 0
    dropped_frames_estimated: int = 0
    last_timestamp_ns: int = 0


class E2ESafetyValidator:
    """Thread-safe, stateful E2E validation engine."""

    # P2-14: bounded stream table — an ID-scanning node on the bus can
    # otherwise grow the per-ID state dict without limit (GBs/hour).
    # Oldest-last-seen streams are evicted first.
    MAX_TRACKED_STREAMS: ClassVar[int] = 1024

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._streams: dict[tuple[str, int], StreamRxState] = {}

    def validate(self, frame: CanFrame, profile: E2EProfileConfig) -> E2EValidationResult:
        """Validate an incoming CanFrame against the given E2EProfileConfig."""
        return self.validate_raw(
            channel_id=frame.channel_id,
            arbitration_id=frame.arbitration_id,
            data=frame.data,
            profile=profile,
            dlc=frame.dlc,
            timestamp_ns=frame.timestamp_ns,
        )

    def validate_raw(
        self,
        channel_id: str,
        arbitration_id: int,
        data: bytes,
        profile: E2EProfileConfig,
        dlc: int | None = None,
        timestamp_ns: int | None = None,
    ) -> E2EValidationResult:
        """Validate raw CAN payload buffer against E2E profile with state tracking."""
        stream_key = (channel_id, arbitration_id)
        ts = timestamp_ns if timestamp_ns is not None else time.time_ns()

        with self._lock:
            if stream_key not in self._streams:
                # P2-14: enforce the stream-table ceiling; evict the least
                # recently seen stream to make room for the new one.
                if len(self._streams) >= self.MAX_TRACKED_STREAMS:
                    oldest_key = min(self._streams, key=lambda k: self._streams[k].last_timestamp_ns)
                    self._streams.pop(oldest_key, None)
                self._streams[stream_key] = StreamRxState(
                    channel_id=channel_id,
                    arbitration_id=arbitration_id,
                )
            state = self._streams[stream_key]
            state.total_frames += 1
            state.last_timestamp_ns = ts

            # H-3 (P1-10): a runt/short payload cannot satisfy the profile's
            # offset layout. extract_crc/extract_counter raise bare ValueError
            # on such payloads — previously the exception escaped validate_raw
            # and killed whichever RX path called it. Report a formal
            # LENGTH_ERROR verdict instead (fail-closed, no raise).
            min_len = max(profile.crc_byte_offset, profile.counter_byte_offset) + 1
            if len(data) < min_len:
                state.last_verdict = E2EStatus.LENGTH_ERROR
                return E2EValidationResult(
                    verdict=E2EStatus.LENGTH_ERROR,
                    expected_crc=-1,
                    actual_crc=-1,
                    counter=-1,
                    previous_counter=state.last_counter,
                    delta=-1,
                    stream_key=stream_key,
                    timestamp_ns=ts,
                )

            actual_crc = extract_crc(data, profile)
            counter = extract_counter(data, profile)
            expected_crc = compute_checksum(data, profile, arbitration_id=arbitration_id, dlc=dlc)

            # 1. Verify CRC integrity
            if actual_crc != expected_crc:
                state.crc_errors += 1
                state.last_verdict = E2EStatus.CRC_ERROR
                return E2EValidationResult(
                    verdict=E2EStatus.CRC_ERROR,
                    expected_crc=expected_crc,
                    actual_crc=actual_crc,
                    counter=counter,
                    previous_counter=state.last_counter,
                    delta=-1,
                    stream_key=stream_key,
                    timestamp_ns=ts,
                )

            # 2. Evaluate Rolling Counter Progression
            prev_counter = state.last_counter
            if prev_counter is None:
                state.last_counter = counter
                state.valid_frames += 1
                state.last_verdict = E2EStatus.INITIAL
                return E2EValidationResult(
                    verdict=E2EStatus.INITIAL,
                    expected_crc=expected_crc,
                    actual_crc=actual_crc,
                    counter=counter,
                    previous_counter=None,
                    delta=0,
                    stream_key=stream_key,
                    timestamp_ns=ts,
                )

            mod = profile.counter_modulo
            delta = (counter - prev_counter) % mod

            if delta == 1:
                verdict = E2EStatus.OK
                state.last_counter = counter
                state.valid_frames += 1
            elif delta == 0:
                verdict = E2EStatus.REPEATED
                state.repeated_frames += 1
                # Do not advance last_counter on repeated frame
            elif 2 <= delta <= profile.max_delta_counter:
                verdict = E2EStatus.SOME_LOST
                state.dropped_frames_estimated += delta - 1
                state.last_counter = counter
                state.valid_frames += 1
            else:
                verdict = E2EStatus.WRONG_SEQUENCE
                state.sequence_errors += 1
                state.last_counter = counter

            state.last_verdict = verdict
            return E2EValidationResult(
                verdict=verdict,
                expected_crc=expected_crc,
                actual_crc=actual_crc,
                counter=counter,
                previous_counter=prev_counter,
                delta=delta,
                stream_key=stream_key,
                timestamp_ns=ts,
            )

    def get_stream_state(self, channel_id: str, arbitration_id: int) -> StreamRxState | None:
        """Retrieve telemetry snapshot for a specific stream."""
        with self._lock:
            state = self._streams.get((channel_id, arbitration_id))
            if state is None:
                return None
            # Return a shallow copy to prevent external mutation
            return StreamRxState(
                channel_id=state.channel_id,
                arbitration_id=state.arbitration_id,
                last_counter=state.last_counter,
                last_verdict=state.last_verdict,
                total_frames=state.total_frames,
                valid_frames=state.valid_frames,
                crc_errors=state.crc_errors,
                sequence_errors=state.sequence_errors,
                repeated_frames=state.repeated_frames,
                dropped_frames_estimated=state.dropped_frames_estimated,
                last_timestamp_ns=state.last_timestamp_ns,
            )

    def reset(self, channel_id: str | None = None, arbitration_id: int | None = None) -> None:
        """Reset stream state(s). If no parameters given, resets all streams."""
        with self._lock:
            if channel_id is None and arbitration_id is None:
                self._streams.clear()
            elif channel_id is not None and arbitration_id is not None:
                self._streams.pop((channel_id, arbitration_id), None)
            elif channel_id is not None:
                keys_to_remove = [k for k in self._streams if k[0] == channel_id]
                for k in keys_to_remove:
                    self._streams.pop(k, None)
            elif arbitration_id is not None:
                keys_to_remove = [k for k in self._streams if k[1] == arbitration_id]
                for k in keys_to_remove:
                    self._streams.pop(k, None)

    def get_all_states(self) -> dict[tuple[str, int], StreamRxState]:
        """Retrieve copy of all active stream states."""
        with self._lock:
            return {k: self.get_stream_state(k[0], k[1]) for k in self._streams}  # type: ignore[misc]

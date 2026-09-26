"""Universal CAN-Bus Diagnostic & Telemetry Platform - E2E Safety Rx Validator.

Stateful Rx validation engine tracking alive and sequence counter progression, verifying CRC integrity,
detecting dropped or duplicated frames, and emitting formal functional safety verdicts (ISO 26262 ASIL-D).
"""

from __future__ import annotations

import collections
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
        """True when frame payload is authentic and usable (OK, INITIAL, or SOME_LOST).

        WARNING (trust-establishment, no authenticity): INITIAL means "first
        frame seen on this stream" — no continuity has been established yet.
        Keyless checksums (TOYOTA/VOLVO/SAE-J1850) are error-detection only;
        a bus attacker who knows the profile can forge a CRC-trivially. Do NOT
        treat INITIAL (or any verdict alone) as authenticity proof; active
        attacker models require AUTOSAR P1/P2 + SecOC/MAC.
        """
        return self.verdict in (E2EStatus.OK, E2EStatus.INITIAL, E2EStatus.SOME_LOST)

    @property
    def is_crc_valid(self) -> bool:
        """True if CRC/checksum matched expected value on a well-formed frame."""
        return self.verdict != E2EStatus.LENGTH_ERROR and self.expected_crc == self.actual_crc

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
    last_seen_monotonic_ns: int = 0


class E2ESafetyValidator:
    """Thread-safe, stateful E2E validation engine."""

    # P2-14: bounded stream table — an ID-scanning node on the bus can
    # otherwise grow the per-ID state dict without limit (GBs/hour).
    # Oldest-last-seen streams are evicted first.
    MAX_TRACKED_STREAMS: ClassVar[int] = 1024
    # A3-F1: the S-14 tombstone ledger is ALSO bounded. Every eviction adds
    # one key here and the set was never pruned (reset() aside), so an
    # ID-scanning flood grew it without limit — the same exhaustion P2-14
    # closed for _streams. Tombstones are a SECURITY state (they force
    # WRONG_SEQUENCE on the stream's return), so the cap is enforced with
    # the same oldest-eviction discipline: the ledger can never exceed twice
    # the stream table.
    MAX_TOMBSTONE_STREAMS: ClassVar[int] = 2048

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._streams: dict[tuple[str, int], StreamRxState] = {}
        # S-14: tombstone ledger of EVICTED stream keys. Without it, the first
        # frame of an evicted stream was re-created with `last_counter=None`
        # and reported INITIAL — a verdict `is_valid` counts as good — so an
        # ID-scanning flood could launder continuity gaps. A tombstoned key
        # yields WRONG_SEQUENCE (is_valid=False) on the frame that re-opens it,
        # seeding `last_counter` so the FOLLOWING frame is continuity-checked.
        self._evicted_streams: set[tuple[str, int]] = set()
        # A3-F1: insertion-ordered tombstone queue mirroring the set, so the
        # cap below evicts oldest-first (a set alone has no eviction order).
        self._evicted_order: collections.deque[tuple[str, int]] = collections.deque()

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
        now_mono_ns = time.monotonic_ns()

        with self._lock:
            tombstoned = stream_key in self._evicted_streams
            if stream_key not in self._streams:
                # P2-14 / T62-M7: enforce the stream-table ceiling; evict the least
                # recently seen stream by local arrival monotonic time (untrusted frame
                # timestamp is strictly forbidden for security state eviction decisions).
                if len(self._streams) >= self.MAX_TRACKED_STREAMS:
                    oldest_key = min(self._streams, key=lambda k: self._streams[k].last_seen_monotonic_ns)
                    self._streams.pop(oldest_key, None)
                    # S-14: record the tombstone so this ID cannot masquerade as
                    # a brand-new (INITIAL, "valid") stream when it returns.
                    self._record_tombstone(oldest_key)
                self._streams[stream_key] = StreamRxState(
                    channel_id=channel_id,
                    arbitration_id=arbitration_id,
                    last_seen_monotonic_ns=now_mono_ns,
                )
            state = self._streams[stream_key]
            state.total_frames += 1
            state.last_timestamp_ns = ts
            state.last_seen_monotonic_ns = now_mono_ns

            # S-14: an evicted stream's next frame must NOT be reported INITIAL
            # (which `is_valid` counts as good): continuity was lost across the
            # eviction. This frame stays WRONG_SEQUENCE, and when its payload
            # can yield a counter, that counter seeds `state.last_counter` so
            # the FOLLOWING frame is continuity-checked instead of resetting to
            # INITIAL. A payload too short to carry a counter keeps the
            # tombstone (still flagged). `reset()` is the sanctioned resync.
            if tombstoned:
                state.sequence_errors += 1
                state.last_verdict = E2EStatus.WRONG_SEQUENCE
                if len(data) > profile.counter_byte_offset:
                    self._evicted_streams.discard(stream_key)
                    self._drop_tombstone(stream_key)
                    state.last_counter = extract_counter(data, profile)
                return E2EValidationResult(
                    verdict=E2EStatus.WRONG_SEQUENCE,
                    expected_crc=-1,
                    actual_crc=-1,
                    counter=-1,
                    previous_counter=None,
                    delta=-1,
                    stream_key=stream_key,
                    timestamp_ns=ts,
                )

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
                last_seen_monotonic_ns=state.last_seen_monotonic_ns,
            )

    def _record_tombstone(self, stream_key: tuple[str, int]) -> None:
        """A3-F1: add an eviction tombstone, enforcing the ledger ceiling.

        Caller MUST hold ``self._lock``. Oldest-first eviction: beyond
        MAX_TOMBSTONE_STREAMS the oldest tombstone is dropped — that stream
        then re-establishes as INITIAL on return (documented residual: a
        >2048-ID scan flood eventually recycles tombstones, same discipline
        as the stream table itself).
        """
        if stream_key in self._evicted_streams:
            return
        self._evicted_streams.add(stream_key)
        self._evicted_order.append(stream_key)
        while len(self._evicted_streams) > self.MAX_TOMBSTONE_STREAMS:
            oldest = self._evicted_order.popleft()
            self._evicted_streams.discard(oldest)

    def _drop_tombstone(self, stream_key: tuple[str, int]) -> None:
        """Remove a consumed tombstone from both the set and the order queue."""
        self._evicted_streams.discard(stream_key)
        try:
            self._evicted_order.remove(stream_key)
        except ValueError:
            pass

    def reset(self, channel_id: str | None = None, arbitration_id: int | None = None) -> None:
        """Reset stream state(s). If no parameters given, resets all streams.

        S-14: an explicit reset ALSO clears the matching eviction tombstones —
        this is the sanctioned RESYNC path, so the stream may re-establish
        continuity (INITIAL) afterwards. A mere eviction never does.
        """
        with self._lock:
            if channel_id is None and arbitration_id is None:
                self._streams.clear()
                self._evicted_streams.clear()
                self._evicted_order.clear()
            elif channel_id is not None and arbitration_id is not None:
                self._streams.pop((channel_id, arbitration_id), None)
                self._drop_tombstone((channel_id, arbitration_id))
            elif channel_id is not None:
                keys_to_remove = [k for k in self._streams if k[0] == channel_id]
                for k in keys_to_remove:
                    self._streams.pop(k, None)
                for k in [k for k in self._evicted_streams if k[0] == channel_id]:
                    self._drop_tombstone(k)
            elif arbitration_id is not None:
                keys_to_remove = [k for k in self._streams if k[1] == arbitration_id]
                for k in keys_to_remove:
                    self._streams.pop(k, None)
                for k in [k for k in self._evicted_streams if k[1] == arbitration_id]:
                    self._drop_tombstone(k)

    def get_all_states(self) -> dict[tuple[str, int], StreamRxState]:
        """Retrieve copy of all active stream states."""
        with self._lock:
            return {k: self.get_stream_state(k[0], k[1]) for k in self._streams}  # type: ignore[misc]

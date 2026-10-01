"""Stimulus experiment: which bits move when the operator does something.

The operator alternates between *rest* (hands off) and *active* (pedal
pressed, steering turned, light switched on ...) while the bus is
listened to. Every frame is filed under the phase it arrived in. At the
end each stream's bytes and bits are compared between the two phases:

* a byte scores by the difference of its means between phases, in units
  of the pooled standard deviation (an effect size — a byte that is noisy
  in both phases does not score just because it moves);
* a bit scores by the difference of its "1" share between phases.

This is correlation, not proof: a byte can change because the engine
reacted to the pedal, not because it *is* the pedal. The result says so
and the operator decides. Nothing here transmits.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from src.core.models.can_frame import CanFrame

Phase = Literal["rest", "active"]
CLEAN_STEP_SCORE = 1000.0
StreamKey = tuple[str, bool, int]


@dataclass(frozen=True, slots=True)
class StimulusCandidate:
    key: StreamKey
    kind: Literal["byte", "bit"]
    index: int  # byte index, or LSB0 bit index (byte * 8 + bit)
    rest_value: float  # mean byte value / share of 1s at rest
    active_value: float  # same, while active
    score: float  # effect size (byte) or share difference (bit), >= 0
    rest_frames: int
    active_frames: int


class StimulusExperiment:
    """Phase-labelled capture plus the rest/active comparison."""

    MAX_FRAMES_PER_PHASE = 4000  # per stream; older frames are dropped
    MIN_FRAMES_PER_PHASE = 5
    MIN_BYTE_SCORE = 1.0  # effect size below this is not reported
    MIN_BIT_SCORE = 0.3  # share difference below this is not reported

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._phase: Phase = "rest"
        self._payloads: dict[Phase, dict[StreamKey, list[bytes]]] = {
            "rest": defaultdict(list),
            "active": defaultdict(list),
        }
        self._switches = 0

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def switches(self) -> int:
        """How many rest→active / active→rest changes the operator made."""
        return self._switches

    def set_phase(self, phase: Phase) -> None:
        if phase not in ("rest", "active"):
            raise ValueError(f"unknown phase {phase!r}")
        with self._lock:
            if phase != self._phase:
                self._switches += 1
            self._phase = phase

    def observe(self, frame: CanFrame) -> None:
        key = (frame.channel_id, bool(frame.is_extended), frame.arbitration_id)
        with self._lock:
            bucket = self._payloads[self._phase][key]
            bucket.append(bytes(frame.data))
            if len(bucket) > self.MAX_FRAMES_PER_PHASE:
                del bucket[: len(bucket) - self.MAX_FRAMES_PER_PHASE]

    def frame_counts(self) -> dict[Phase, int]:
        with self._lock:
            return {p: sum(len(v) for v in self._payloads[p].values()) for p in ("rest", "active")}

    def analyze(self, limit: int = 20) -> list[StimulusCandidate]:
        """Rank bytes and bits that differ most between rest and active."""
        with self._lock:
            rest = {k: list(v) for k, v in self._payloads["rest"].items()}
            active = {k: list(v) for k, v in self._payloads["active"].items()}
        out: list[StimulusCandidate] = []
        for key in rest.keys() & active.keys():
            r, a = rest[key], active[key]
            if len(r) < self.MIN_FRAMES_PER_PHASE or len(a) < self.MIN_FRAMES_PER_PHASE:
                continue
            width = min(min(len(p) for p in r), min(len(p) for p in a))
            for byte in range(width):
                rv = [p[byte] for p in r]
                av = [p[byte] for p in a]
                score = _effect_size(rv, av)
                if score >= self.MIN_BYTE_SCORE:
                    out.append(StimulusCandidate(key, "byte", byte, _mean(rv), _mean(av), round(score, 3), len(r), len(a)))
                for bit in range(8):
                    rs = sum((v >> bit) & 1 for v in rv) / len(rv)
                    as_ = sum((v >> bit) & 1 for v in av) / len(av)
                    diff = abs(as_ - rs)
                    if diff >= self.MIN_BIT_SCORE:
                        out.append(StimulusCandidate(key, "bit", byte * 8 + bit, round(rs, 3), round(as_, 3), round(diff, 3), len(r), len(a)))
        bytes_first = sorted((c for c in out if c.kind == "byte"), key=lambda c: -c.score)
        bits = sorted((c for c in out if c.kind == "bit"), key=lambda c: -c.score)
        return (bytes_first + bits)[: max(0, limit)]


def _mean(values: list[int]) -> float:
    return round(sum(values) / len(values), 3)


def _effect_size(rest: list[int], active: list[int]) -> float:
    """|mean difference| / pooled standard deviation; a constant pair that differs scores high."""
    mr = sum(rest) / len(rest)
    ma = sum(active) / len(active)
    vr = sum((x - mr) ** 2 for x in rest) / max(1, len(rest) - 1)
    va = sum((x - ma) ** 2 for x in active) / max(1, len(active) - 1)
    pooled = math.sqrt((vr + va) / 2)
    diff = abs(ma - mr)
    if pooled == 0:
        # Constant in each phase and different between them: the cleanest
        # possible step. Rank it above any noisy byte (finite for JSON).
        return 0.0 if diff == 0 else CLEAN_STEP_SCORE
    return min(diff / pooled, CLEAN_STEP_SCORE - 1)

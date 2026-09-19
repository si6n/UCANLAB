"""Data models and hypothesis contracts for the Signal Discovery & Evidence Engine.

Complies with MASTER_PLAN.md Section 7 and docs/specs/signal_discovery_spec.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Evidence:
    """A single piece of statistical or empirical evidence supporting a hypothesis."""

    kind: str  # "flip_rate" | "monotonicity" | "crc_match_ratio" | "correlation" | "entropy"
    value: float
    detail: str  # Human-readable explanation


@dataclass(slots=True)
class Hypothesis:
    """A candidate signal or protocol invariant hypothesis discovered within a CAN ID."""

    htype: str  # "COUNTER" | "CHECKSUM" | "SIGNAL" | "CONSTANT"
    start_bit: int
    length: int
    is_little_endian: bool = True
    is_signed: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0  # 0.0 to 1.0
    evidence: list[Evidence] = field(default_factory=list)
    status: str = "candidate"  # "candidate" | "approved" | "rejected"
    name: str = ""
    unit: str = ""
    factor: float = 1.0
    offset: float = 0.0
    min_value: float | None = None
    max_value: float | None = None


@dataclass(slots=True)
class IdReport:
    """Discovery analysis report for a single CAN Arbitration ID."""

    arbitration_id: int
    frame_count: int
    rate_hz: float
    dlc: int
    entropy: dict[int, float] = field(default_factory=dict)  # byte_index -> Shannon entropy
    # LSB0 bit index -> "CONST" | "INC" | "TOGGLE" | "NOISY" (per-bit flip-rate classification)
    bit_classes: list[str] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    channel_id: str = "can0"
    is_extended: bool = False

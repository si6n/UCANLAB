# -*- coding: utf-8 -*-
"""Unit tests for Wasm-R3 Trace Reducer, can-train-and-test Adapter, and Size Sentry.

Verifies:
- Aksiyon 30: CanTraceReducer preserves payload transitions, diagnostic IDs,
  and heartbeat intervals while eliminating steady-state duplicates.
- Aksiyon 29: CanTrainTestAdapter parses arXiv:2308.04972 benchmark traces into LabeledCanFrames.
- Aksiyon 37: check_corpus_sizes audits asset budgets against CI limits.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from src.core.models.can_frame import CanFrame
from src.hal.replay.trace_reducer import CanTraceReducer
from src.hal.replay.can_train_test_adapter import CanTrainTestAdapter
from scripts.check_corpus_size_sentry import check_corpus_sizes


class TestCanTraceReducer:
    """Verifies Wasm-R3 record-reduce-replay deduplication."""

    def test_deduplicates_redundant_steady_state_frames(self) -> None:
        reducer = CanTraceReducer(max_heartbeat_interval_s=0.500)

        # 10 identical frames within 100ms
        frames = [
            CanFrame.create(
                channel_id="can0",
                arbitration_id=0x100,
                data=b"\x01\x02\x03\x04",
                timestamp_ns=int(0.010 * i * 1_000_000_000),
            )
            for i in range(10)
        ]

        reduced = list(reducer.reduce(iter(frames)))
        # Only the very first frame should be emitted since data is identical and time < 0.5s
        assert len(reduced) == 1
        assert reduced[0].timestamp_ns == 0

    def test_preserves_payload_transitions(self) -> None:
        reducer = CanTraceReducer(max_heartbeat_interval_s=0.500)

        frames = [
            CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\x00\x00", timestamp_ns=0),
            CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\x00\x00", timestamp_ns=50_000_000),
            CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\x00\x01", timestamp_ns=100_000_000),  # bit flip!
            CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\x00\x01", timestamp_ns=150_000_000),
        ]

        reduced = list(reducer.reduce(iter(frames)))
        assert len(reduced) == 2
        assert reduced[0].data == b"\x00\x00"
        assert reduced[1].data == b"\x00\x01"

    def test_preserves_heartbeat_interval(self) -> None:
        reducer = CanTraceReducer(max_heartbeat_interval_s=0.200)

        frames = [
            CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\xaa\xbb", timestamp_ns=0),
            CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\xaa\xbb", timestamp_ns=100_000_000),
            CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\xaa\xbb", timestamp_ns=250_000_000),  # Delta > 0.2s
        ]

        reduced = list(reducer.reduce(iter(frames)))
        assert len(reduced) == 2
        assert reduced[0].timestamp_ns == 0
        assert reduced[1].timestamp_ns == 250_000_000

    def test_always_keeps_specified_ids(self) -> None:
        # e.g. UDS request / response or E-stop
        reducer = CanTraceReducer(always_keep_ids={0x7E0, 0x7E8})

        frames = [
            CanFrame.create(
                channel_id="can0",
                arbitration_id=0x7E0,
                data=b"\x02\x10\x01\x00\x00\x00\x00\x00",
                timestamp_ns=int(0.01 * i * 1_000_000_000),
            )
            for i in range(5)
        ]
        reduced = list(reducer.reduce(iter(frames)))
        assert len(reduced) == 5


class TestCanTrainTestAdapter:
    """Verifies parsing of arXiv:2308.04972 benchmark traces."""

    def test_parse_csv_benchmark(self, tmp_path: Path) -> None:
        sample_csv = tmp_path / "can_train_test_sample.csv"
        sample_csv.write_text(
            "timestamp,arbitration_id,dlc,data,label\n"
            "0.001000,0x18FEF100,8,0102030405060708,0\n"
            "0.002000,0x0CF00400,8,FFFFFFFFFFFFFFFF,1\n"
            "0.003000,0x18FEE000,4,AABBCCDD,attack\n",
            encoding="utf-8",
        )

        entries = list(CanTrainTestAdapter.parse_file(sample_csv))
        assert len(entries) == 3

        # First entry: normal
        assert entries[0].frame.arbitration_id == 0x18FEF100
        assert entries[0].frame.data == bytes.fromhex("0102030405060708")
        assert entries[0].is_anomaly is False

        # Second entry: anomaly
        assert entries[1].frame.arbitration_id == 0x0CF00400
        assert entries[1].is_anomaly is True

        # Third entry: attack
        assert entries[2].frame.arbitration_id == 0x18FEE000
        assert entries[2].frame.data == bytes.fromhex("AABBCCDD")
        assert entries[2].is_anomaly is True
        assert entries[2].label_name == "attack"


class TestCorpusSizeSentry:
    """Verifies size sentry gate."""

    def test_sentry_passes_clean_repository(self) -> None:
        repo_root = Path(".").resolve()
        passed, violations = check_corpus_sizes(repo_root)
        assert passed is True
        assert len(violations) == 0

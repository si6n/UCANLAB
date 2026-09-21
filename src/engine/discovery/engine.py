"""Signal Discovery & Evidence Engine Orchestrator.

Implements MASTER_PLAN.md Section 7, coordinating live and offline trace ingestion,
statistical bit profiling, invariant detection (Counter/CRC/Checksum), signal segmentation,
and automated DBC generation.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from pathlib import Path

from cantools.database.can.database import Database

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.engine.discovery.bitstats import BitStats
from src.engine.discovery.dbc_builder import DbcBuilder
from src.engine.discovery.detectors.checksum import ChecksumDetector
from src.engine.discovery.detectors.counter import CounterDetector
from src.engine.discovery.hypotheses import IdReport
from src.engine.discovery.segmenter import SignalSegmenter
from src.hal.replay.parsers import VectorAscParser

logger = get_logger("engine.discovery")


class SignalDiscoveryEngine:
    """Orchestrates reverse-engineering workflows to discover signals from raw CAN traffic."""

    MAX_FRAMES_PER_ID = 10_000
    MAX_TOTAL_FRAMES = 200_000
    MAX_ASC_FILE_BYTES = 256 * 1024 * 1024  # 256 MiB

    def __init__(self, min_frames: int = 10) -> None:
        self.min_frames = min_frames
        # REVIEW (cross-bus keying): frames are bucketed by
        # (channel_id, is_extended, arbitration_id) — the old id-only key
        # merged two different CAN networks' same-numbered messages into a
        # single time series (fake transitions, wrong DBC).
        #
        # P2-3: each bucket is a `deque(maxlen=MAX_FRAMES_PER_ID)` so the
        # per-ID ring no longer pays an O(n) `del bucket[0]` (a 10k-element
        # memmove on EVERY frame once the bucket was full — the ingest hot
        # path degraded to quadratic as a capture ran). `_lock` makes the
        # whole ingest/analyze surface safe: the bucket dict, the per-bucket
        # rings and `_total_frames` are all shared mutable state, and a
        # concurrent `analyze_key` used to read a bucket mid-mutation.
        self._frames_by_id: dict[tuple[str, bool, int], deque[CanFrame]] = defaultdict(
            lambda: deque(maxlen=self.MAX_FRAMES_PER_ID)
        )
        self._reports_cache: dict[tuple[str, bool, int], IdReport] = {}
        self._total_frames = 0
        self._lock = threading.RLock()

    def clear(self) -> None:
        """Clear all ingested frames and cached reports."""
        with self._lock:
            self._frames_by_id.clear()
            self._reports_cache.clear()
            self._total_frames = 0

    @staticmethod
    def _bucket_key(frame: CanFrame) -> tuple[str, bool, int]:
        """Session-stable discovery identity: channel + frame format + ID."""
        return (frame.channel_id, bool(frame.is_extended), frame.arbitration_id)

    def _append_bounded_unlocked(self, frame: CanFrame) -> bool:
        """Append with per-ID 10k ring + total cap. Caller must hold `_lock`."""
        if self._total_frames >= self.MAX_TOTAL_FRAMES:
            return False
        key = self._bucket_key(frame)
        bucket = self._frames_by_id[key]
        if len(bucket) >= self.MAX_FRAMES_PER_ID:
            # 10k ring: the deque's maxlen evicts the oldest slot in O(1).
            # NOTE: a deque with maxlen cannot be over-full, so this branch is
            # only reachable defensively; the drop is accounted explicitly.
            dropped = bucket.popleft()
            del dropped
            self._total_frames -= 1
        bucket.append(frame)
        self._total_frames += 1
        self._reports_cache.pop(key, None)
        return True

    def _append_bounded(self, frame: CanFrame) -> bool:
        """Append with per-ID 10k ring + total cap. Returns False if dropped."""
        with self._lock:
            return self._append_bounded_unlocked(frame)

    def ingest_frame(self, frame: CanFrame) -> None:
        """Ingest a single CAN frame (e.g. from live stream or router)."""
        self._append_bounded(frame)

    def ingest_frames(self, frames: Sequence[CanFrame]) -> None:
        """Ingest a batch of CAN frames.

        The whole batch shares ONE lock acquisition: 200k individual
        acquisitions on a bulk load is pure overhead, and the session is
        either mid-import (no concurrent reader of interest) or already
        being read under the same lock discipline.
        """
        dropped_any = False
        with self._lock:
            for f in frames:
                if not self._append_bounded_unlocked(f):
                    dropped_any = True
                    break
        if dropped_any:
            logger.warning("Discovery ingest total cap reached — dropping frames")

    def ingest_asc_file(self, file_path: str | Path, *, max_frames: int | None = None) -> int:
        """Stream-ingest a Vector ASCII (.asc) trace file.

        P3-6: the previous implementation called `VectorAscParser.parse_file`,
        which materialises EVERY frame in the file into one Python list before
        the engine sees any of them — a 256 MiB trace allocated hundreds of
        MB of `CanFrame` objects that the per-ID ring immediately discarded.
        `parse_file_iter` yields lazily instead, so peak memory is bounded by
        the ring (MAX_TOTAL_FRAMES ≈ 200k frames) and parsing stops at the cap
        rather than reading the tail of a file whose every bucket is full.

        Args:
            file_path: Path to the .asc trace.
            max_frames: Optional additional cap for this call (the engine's
                MAX_TOTAL_FRAMES still applies).

        Returns:
            Number of frames actually ingested (never the file's raw line
            count), so a capped import is reported honestly.
        """
        path = Path(file_path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise FileNotFoundError(f"Trace file not found: {path}") from exc
        if size > self.MAX_ASC_FILE_BYTES:
            raise ValueError(f"Trace file too large ({size} bytes, limit {self.MAX_ASC_FILE_BYTES})")
        if max_frames is not None and max_frames < 0:
            raise ValueError(f"max_frames must be non-negative, got {max_frames}")

        ingested = 0
        capped = False
        with self._lock:
            for frame in self._stream_asc_frames(path):
                if max_frames is not None and ingested >= max_frames:
                    capped = True
                    break
                if not self._append_bounded_unlocked(frame):
                    capped = True
                    break
                ingested += 1
        if capped:
            logger.warning(
                "Discovery ASC ingest stopped at frame cap",
                extra={"file": str(file_path), "ingested": ingested},
            )
        logger.info(
            "Ingested trace file into DiscoveryEngine",
            extra={
                "file": str(file_path),
                "frames": ingested,
                "unique_ids": len(self._frames_by_id),
                "capped": capped,
            },
        )
        return ingested

    @staticmethod
    def _stream_asc_frames(path: Path) -> Iterable[CanFrame]:
        """Lazy frame iterator over an .asc file (P3-6 streaming seam)."""
        return VectorAscParser.parse_file_iter(path)

    @property
    def discovered_keys(self) -> list[tuple[str, bool, int]]:
        """List of all unique (channel_id, is_extended, arbitration_id) keys."""
        with self._lock:
            return sorted(self._frames_by_id.keys())

    @property
    def discovered_ids(self) -> list[int]:
        """List of all unique arbitration IDs present in the ingested dataset."""
        with self._lock:
            return sorted({key[2] for key in self._frames_by_id})

    def get_frame_count(self, target: int | tuple[str, bool, int]) -> int:
        """Get the number of ingested frames for a specific CAN ID or stream key."""
        with self._lock:
            if isinstance(target, tuple):
                return len(self._frames_by_id.get(target, ()))
            return sum(len(bucket) for key, bucket in self._frames_by_id.items() if key[2] == target)

    def _snapshot_frames_locked(self, key: tuple[str, bool, int]) -> list[CanFrame]:
        """P2-3: copy a bucket under the lock so analysis runs lock-free.

        Caller must hold `_lock`. Returning a plain list (not the live deque)
        means the detector/segmenter pass cannot observe a concurrent
        eviction, and the report is computed from one coherent time series.
        """
        return list(self._frames_by_id.get(key, ()))

    def analyze_key(self, key: tuple[str, bool, int]) -> IdReport:
        """Run full evidence-based reverse engineering on a specific (channel, is_extended, arb_id) stream."""
        with self._lock:
            cached = self._reports_cache.get(key)
            if cached is not None:
                return cached
            frames = self._snapshot_frames_locked(key)
            channel_id, is_extended, arb_id = key
            if not frames:
                empty_report = IdReport(
                    arbitration_id=arb_id,
                    channel_id=channel_id,
                    is_extended=is_extended,
                    frame_count=0,
                    rate_hz=0.0,
                    dlc=0,
                )
                self._reports_cache[key] = empty_report
                return empty_report

        byte_len = max(len(f.data) for f in frames)
        payloads = [f.data for f in frames]
        frame_count = len(frames)

        # 1. Calculate Message Rate (Hz)
        rate_hz = 0.0
        if len(frames) >= 2:
            duration_s = (frames[-1].timestamp_ns - frames[0].timestamp_ns) / 1_000_000_000.0
            if duration_s > 0:
                rate_hz = round(len(frames) / duration_s, 2)

        # 2. Byte-Level Shannon Entropy
        entropy_by_byte: dict[int, float] = {}
        for byte_idx in range(byte_len):
            byte_vals = [p[byte_idx] for p in payloads if len(p) > byte_idx]
            entropy_by_byte[byte_idx] = BitStats.compute_shannon_entropy(byte_vals)

        # 2b. Per-Bit Transition Profile (flip rates + classification)
        flip_rates = BitStats.compute_flip_rates(payloads, byte_len)
        bit_classes = BitStats.classify_bits(flip_rates)

        # 3. Detect Rolling Counters
        counter_hypotheses = CounterDetector.detect(payloads, byte_len)

        # 4. Detect Checksums / CRCs
        checksum_hypotheses = ChecksumDetector.detect(payloads, byte_len)

        # 5. Determine occupied bit spans from confirmed counter / checksum hypotheses
        occupied_spans: list[tuple[int, int]] = []
        for h in counter_hypotheses:
            if h.confidence >= 0.85:
                occupied_spans.append((h.start_bit, h.length))
        for h in checksum_hypotheses:
            if h.confidence >= 0.85:
                occupied_spans.append((h.start_bit, h.length))

        # 6. Segment Remaining Payload into Physical Signal Candidates
        signal_hypotheses = SignalSegmenter.segment(payloads, byte_len, occupied_spans=occupied_spans)

        # Combine all hypotheses
        all_hypotheses = counter_hypotheses + checksum_hypotheses + signal_hypotheses

        report = IdReport(
            arbitration_id=arb_id,
            channel_id=channel_id,
            is_extended=is_extended,
            frame_count=frame_count,
            rate_hz=rate_hz,
            dlc=byte_len,
            entropy=entropy_by_byte,
            bit_classes=bit_classes,
            hypotheses=all_hypotheses,
        )
        with self._lock:
            self._reports_cache[key] = report
        return report

    def analyze_id(
        self,
        arb_id: int | tuple[str, bool, int],
        channel_id: str | None = None,
        is_extended: bool | None = None,
    ) -> IdReport:
        """Run full evidence-based reverse engineering on a specific CAN ID or stream key."""
        if isinstance(arb_id, tuple):
            return self.analyze_key(arb_id)
        if channel_id is not None and is_extended is not None:
            return self.analyze_key((channel_id, is_extended, arb_id))

        with self._lock:
            matching_keys = [k for k in self._frames_by_id if k[2] == arb_id]
            if channel_id is not None:
                matching_keys = [k for k in matching_keys if k[0] == channel_id]
            if is_extended is not None:
                matching_keys = [k for k in matching_keys if k[1] == is_extended]

        if matching_keys:
            return self.analyze_key(matching_keys[0])

        return self.analyze_key((channel_id or "can0", is_extended or False, arb_id))

    def analyze_all(self) -> dict[tuple[str, bool, int], IdReport]:
        """Run analysis on all (channel_id, is_extended, arbitration_id) streams with sufficient frame counts."""
        reports: dict[tuple[str, bool, int], IdReport] = {}
        for key in self.discovered_keys:
            if self.get_frame_count(key) >= self.min_frames:
                reports[key] = self.analyze_key(key)
        return reports

    def approve_hypothesis(
        self,
        arb_id: int | tuple[str, bool, int],
        start_bit: int,
        length: int,
        channel_id: str | None = None,
        is_extended: bool | None = None,
    ) -> bool:
        """Approve a specific hypothesis for DBC export."""
        report = self.analyze_id(arb_id, channel_id=channel_id, is_extended=is_extended)
        for hyp in report.hypotheses:
            if hyp.start_bit == start_bit and hyp.length == length:
                hyp.status = "approved"
                return True
        return False

    def generate_evidence_markdown(
        self,
        arb_id: int | tuple[str, bool, int],
        channel_id: str | None = None,
        is_extended: bool | None = None,
    ) -> str:
        """Generate a human-readable Markdown evidence report for a CAN ID or stream."""
        report = self.analyze_id(arb_id, channel_id=channel_id, is_extended=is_extended)
        lines = [
            f"# Signal Discovery Evidence Report: CAN ID 0x{report.arbitration_id:04X} [{report.channel_id}]",
            f"- **Frame Count:** {report.frame_count}",
            f"- **Estimated Rate:** {report.rate_hz} Hz",
            f"- **DLC:** {report.dlc} Bytes",
            "",
            "## Byte Entropy Profile",
            "| Byte Index | Shannon Entropy (bits) | Status |",
            "|:---:|:---:|:---|",
        ]
        for b_idx, ent in sorted(report.entropy.items()):
            status = "Const (0.0)" if ent == 0 else "Low Variance" if ent < 2.0 else "Dynamic" if ent < 6.0 else "High Entropy / Noise"
            lines.append(f"| {b_idx} | {ent:.4f} | {status} |")

        lines.extend([
            "",
            "## Discovered Hypotheses",
            "| Type | Bit Range | Length | Confidence | Status | Details |",
            "|:---:|:---:|:---:|:---:|:---:|:---|",
        ])

        if not report.hypotheses:
            lines.append("| - | - | - | - | - | *No strong hypotheses detected* |")
        else:
            for h in report.hypotheses:
                details = "; ".join(e.detail for e in h.evidence)
                lines.append(
                    f"| **{h.htype}** | {h.start_bit}..{h.start_bit + h.length - 1} | {h.length} | "
                    f"{h.confidence:.1%} | `{h.status}` | {details} |"
                )

        return "\n".join(lines)

    def build_dbc(self, approved_only: bool = False) -> Database:
        """Generate a cantools Database from discovered signal hypotheses."""
        reports = self.analyze_all()
        return DbcBuilder.build_database(reports, approved_only=approved_only)

    def export_dbc(
        self,
        file_path: str | Path,
        approved_only: bool = False,
        exports_root: str | Path | None = None,
    ) -> None:
        """Export discovered signals to a .dbc file.

        F7: `exports_root` is threaded through to the shared, fail-closed
        path guard so the target is confined to an explicit root.
        """
        db = self.build_dbc(approved_only=approved_only)
        DbcBuilder.export_dbc_file(db, file_path, exports_root=exports_root)

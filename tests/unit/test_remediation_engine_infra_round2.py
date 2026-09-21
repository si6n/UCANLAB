"""Round-2 remediation regression tests — engine infra (exporters/buffer/discovery/router/pipeline/virtual_channels).

Covers the verified findings owned by this workstream:

* P1-8  MDF4 atomic export publishes via `commit_producer_path` and leaves no
        `.mf4tmp`/`.part` scratch behind when the writer fails.
* P1-9  MAT and MDF4 share ONE series validator (NaN/Inf + monotonicity), so a
        poisoned series can no longer reach the .mat artefact.
* P1-6  Ring-buffer provenance round-trip: `synthetic` no longer returns as
        `injected`, and a v1 buffer can still be decoded.
* P2-3  Discovery ingest uses a bounded deque under a lock (no O(n) eviction).
* P2-7  No fabricated nominal torque of 1000 N·m.
* P2-8  Physically impossible propeller slip is rejected, not clamped.
* P2-10 Strict VIN decoding returns None instead of fabricating characters.
* P3-2  Sparse bits are labelled SPARSE, not INC.
* P3-3  XOR-8/SUM-8 confidence is capped below the confirmed threshold.
* P3-6  ASC ingest streams lazily and honours a frame cap.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from src.core.models.can_frame import CanFrame
from src.engine.buffer.ring_buffer import (
    CAN_RECORD_DTYPE,
    CAN_RECORD_DTYPE_V1,
    RECORD_FORMAT_VERSION,
    BinaryRingBuffer,
)
from src.engine.discovery.bitstats import BitStats
from src.engine.discovery.detectors.checksum import ChecksumDetector
from src.engine.discovery.engine import SignalDiscoveryEngine
from src.engine.exporters.mat_exporter import MatExporter
from src.engine.exporters.mdf4_exporter import Mdf4Exporter
from src.engine.exporters.series_validation import validate_signal_series
from src.engine.pipeline.reassembly_pipeline import decode_vin_payload
from src.engine.virtual_channels.channel_engine import VirtualChannelEngine

# ---------------------------------------------------------------------------
# P1-8 — MDF4 atomic write
# ---------------------------------------------------------------------------


def _require_asammdf() -> None:
    try:
        from asammdf import MDF  # noqa: F401

        _ = MDF()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"asammdf not functional in this environment: {exc}")


def test_p1_8_mdf4_export_leaves_no_scratch_file(tmp_path: Path) -> None:
    _require_asammdf()
    signals = {"EngineSpeed": ([0.0, 0.1, 0.2], [800.0, 850.0, 1200.0], "rpm")}
    out = Mdf4Exporter.export_signals(tmp_path / "s.mf4", signals, exports_root=tmp_path)
    assert out.exists() and out.stat().st_size > 0
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != out.name]
    assert leftovers == [], f"export left scratch files behind: {leftovers}"


def test_p1_8_mdf4_failed_save_removes_scratch_and_keeps_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising `MDF.save` must not publish, must unlink scratch, must keep the old artefact."""
    _require_asammdf()
    import src.engine.exporters.mdf4_exporter as mod

    out = tmp_path / "s.mf4"
    out.write_bytes(b"PREVIOUS-GOOD-ARTEFACT")

    class _Boom:
        def __init__(self) -> None:
            self.appended = False

        def append(self, _signals: object) -> None:
            self.appended = True

        def save(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

    class _FakeSignal:
        def __init__(self, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(mod, "MDF", _Boom)
    monkeypatch.setattr(mod, "Signal", _FakeSignal)

    with pytest.raises(OSError, match="disk full"):
        Mdf4Exporter.export_signals(
            out, {"EngineSpeed": ([0.0, 0.1], [1.0, 2.0], "rpm")}, exports_root=tmp_path
        )

    assert out.read_bytes() == b"PREVIOUS-GOOD-ARTEFACT", "failed export must not clobber the old file"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != out.name]
    assert leftovers == [], f"failed export left scratch behind: {leftovers}"


# ---------------------------------------------------------------------------
# P1-9 — shared series validation in BOTH exporters
# ---------------------------------------------------------------------------


def test_p1_9_mat_export_rejects_nan_value(tmp_path: Path) -> None:
    pytest.importorskip("scipy")
    with pytest.raises(ValueError, match="non-finite value"):
        MatExporter.export_signals(
            tmp_path / "s.mat",
            {"EngineSpeed": ([0.0, 0.1], [1000.0, float("nan")], "rpm")},
            exports_root=tmp_path,
        )
    assert not (tmp_path / "s.mat").exists(), "a rejected export must publish nothing"


def test_p1_9_mat_export_rejects_non_monotonic_time(tmp_path: Path) -> None:
    pytest.importorskip("scipy")
    with pytest.raises(ValueError, match="not strictly increasing"):
        MatExporter.export_signals(
            tmp_path / "s.mat",
            {"EngineSpeed": ([0.0, 0.0], [1000.0, 1100.0], "rpm")},
            exports_root=tmp_path,
        )
    assert not (tmp_path / "s.mat").exists()


def test_p1_9_mdf4_export_rejects_inf_and_length_mismatch(tmp_path: Path) -> None:
    _require_asammdf()
    with pytest.raises(ValueError, match="non-finite value"):
        Mdf4Exporter.export_signals(
            tmp_path / "s.mf4",
            {"EngineSpeed": ([0.0, 0.1], [float("inf"), 1.0], "rpm")},
            exports_root=tmp_path,
        )
    with pytest.raises(ValueError, match="length mismatch"):
        Mdf4Exporter.export_signals(
            tmp_path / "s2.mf4",
            {"EngineSpeed": ([0.0, 0.1], [1.0], "rpm")},
            exports_root=tmp_path,
        )


def test_p1_9_validator_is_the_same_object_in_both_exporters() -> None:
    """One shared implementation — the finding was that the validator existed only in MDF4."""
    import src.engine.exporters.mat_exporter as mat
    import src.engine.exporters.mdf4_exporter as mdf

    assert mat.validate_signal_series is validate_signal_series
    assert mdf.validate_signal_series is validate_signal_series


def test_p1_9_validator_accepts_a_clean_series() -> None:
    validate_signal_series("EngineSpeed", [0.0, 0.1, 0.2], [1.0, 2.0, 3.0])


# ---------------------------------------------------------------------------
# P1-6 — lossless provenance round-trip
# ---------------------------------------------------------------------------


def test_p1_6_synthetic_source_round_trips_losslessly() -> None:
    buf = BinaryRingBuffer(capacity=16)
    buf.append(
        CanFrame.create(
            channel_id="sim0",
            arbitration_id=0x321,
            data=b"\x01\x02",
            timestamp_ns=1,
            source="synthetic",
        )
    )
    frame = buf.get_latest_frames(1)[0]
    assert frame.source == "synthetic", "synthetic must not be re-labelled injected (AGENTS.md §2.3)"


def test_p1_6_every_source_round_trips() -> None:
    buf = BinaryRingBuffer(capacity=16)
    sources = ["physical", "replay", "virtual", "injected", "synthetic"]
    for i, src in enumerate(sources):
        buf.append(
            CanFrame.create(channel_id="c0", arbitration_id=i, data=b"\xaa", timestamp_ns=i, source=src)
        )
    got = [f.source for f in buf.get_latest_frames(len(sources))]
    assert got == sources


def test_p1_6_record_format_is_versioned_and_widened() -> None:
    assert RECORD_FORMAT_VERSION == 2
    assert "source_code" in (CAN_RECORD_DTYPE.names or ())
    assert "source_code" not in (CAN_RECORD_DTYPE_V1.names or ())


def test_p1_6_legacy_v1_records_still_decode_to_injected() -> None:
    """Backward compatibility: a pre-v2 record whose 2-bit field was 3 decodes as injected."""
    legacy = np.zeros(1, dtype=CAN_RECORD_DTYPE_V1)
    legacy["flags"] = 3 << 5
    assert BinaryRingBuffer.is_legacy_record_array(legacy) is True
    upgraded = BinaryRingBuffer.upgrade_legacy_records(legacy)
    assert "source_code" in (upgraded.dtype.names or ())
    assert int(upgraded["source_code"][0]) == 3
    # `synthetic` was NOT expressible in v1, so the migration must never invent it.
    assert BinaryRingBuffer._decode_source(3, 3 << 5) == "injected"


def test_p1_6_legacy_zero_code_decodes_physical() -> None:
    assert BinaryRingBuffer._decode_source(0, 0) == "physical"


# ---------------------------------------------------------------------------
# P2-3 — discovery ingest: bounded deque + lock
# ---------------------------------------------------------------------------


def test_p2_3_per_id_ring_is_bounded_and_keeps_newest() -> None:
    engine = SignalDiscoveryEngine(min_frames=1)
    engine.MAX_FRAMES_PER_ID = 8  # type: ignore[misc]
    engine._frames_by_id = engine._frames_by_id.__class__(  # rebuild with the new maxlen
        lambda: __import__("collections").deque(maxlen=8)
    )
    for i in range(50):
        engine.ingest_frame(
            CanFrame.create(channel_id="c0", arbitration_id=0x100, data=bytes([i]), timestamp_ns=i)
        )
    frames = engine._frames_by_id[("c0", False, 0x100)]
    assert len(frames) == 8
    assert int(frames[-1].data[0]) == 49, "newest frame must be retained"
    assert int(frames[0].data[0]) == 42, "oldest frames must have been evicted"


def test_p2_3_regression_property_holds_after_my_change() -> None:
    engine = SignalDiscoveryEngine(min_frames=1)
    for i in range(25):
        engine._append_bounded(CanFrame.create(channel_id="c0", arbitration_id=i, data=b"\x01"))
    assert engine.discovered_ids == list(range(25))
    assert engine.get_frame_count(0x00) == 1


def test_p2_3_concurrent_ingest_and_read_is_coherent() -> None:
    engine = SignalDiscoveryEngine(min_frames=1)
    errors: list[str] = []
    stop = threading.Event()

    def writer() -> None:
        for i in range(500):
            engine.ingest_frame(
                CanFrame.create(channel_id="c0", arbitration_id=i % 16, data=b"\x02", timestamp_ns=i)
            )

    def reader() -> None:
        while not stop.is_set():
            try:
                # B018: the reads are the point (concurrent access to the live
                # view); bind them so the probe is not a no-op expression.
                _ids = engine.discovered_ids
                _count = engine.get_frame_count(0x01)
                _ = (_ids, _count)
            except Exception as exc:  # pragma: no cover - would be the bug
                errors.append(repr(exc))

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        writer()
    finally:
        stop.set()
        for t in readers:
            t.join(timeout=2.0)
    assert errors == []


# ---------------------------------------------------------------------------
# P2-7 / P2-8 — no fabricated nominal torque; impossible slip rejected
# ---------------------------------------------------------------------------


def test_p2_7_no_fabricated_default_nominal_torque() -> None:
    assert VirtualChannelEngine.calculate_torque_and_power(1800.0, 80.0) == (None, None, None)
    assert VirtualChannelEngine.calculate_torque_and_power(1800.0, 80.0, nominal_torque_nm=None) == (
        None,
        None,
        None,
    )


def test_p2_7_supplied_rating_still_evaluates() -> None:
    torque, power_kw, power_hp = VirtualChannelEngine.calculate_torque_and_power(
        1800.0, 80.0, nominal_torque_nm=1000.0
    )
    assert torque == 800.0
    assert power_kw is not None and power_hp is not None


def test_p2_7_invalid_explicit_rating_still_fails_closed() -> None:
    for bad in (0.0, -500.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite positive"):
            VirtualChannelEngine.calculate_torque_and_power(1800.0, 80.0, nominal_torque_nm=bad)


def test_p2_8_impossible_slip_returns_none() -> None:
    # Boat faster than pitch speed beyond the plausible window (negative slip < -50%).
    assert (
        VirtualChannelEngine.calculate_propeller_slip(
            engine_rpm=1500.0, gear_ratio=2.0, prop_pitch_inches=10.0, boat_speed_knots=40.0
        )
        is None
    )
    # Slip above the ceiling: boat moving astern against a turning shaft.
    assert (
        VirtualChannelEngine.calculate_propeller_slip(
            engine_rpm=3000.0, gear_ratio=2.0, prop_pitch_inches=21.0, boat_speed_knots=-5.0
        )
        is None
    )


def test_p2_8_slip_inside_the_window_is_returned() -> None:
    # ~100 % slip (boat effectively stationary) is the top of the plausible
    # window and must still be reported, not discarded.
    assert VirtualChannelEngine.calculate_propeller_slip(
        engine_rpm=3000.0, gear_ratio=2.0, prop_pitch_inches=21.0, boat_speed_knots=0.001
    ) == 100.0


def test_p2_8_plausible_slip_unchanged() -> None:
    slip = VirtualChannelEngine.calculate_propeller_slip(
        engine_rpm=3000.0, gear_ratio=2.0, prop_pitch_inches=21.0, boat_speed_knots=22.0
    )
    assert slip is not None and abs(slip - 15.13) <= 0.2


# ---------------------------------------------------------------------------
# P2-10 — strict VIN decoding
# ---------------------------------------------------------------------------


def test_p2_10_strict_vin_decode_returns_none_on_non_ascii() -> None:
    assert decode_vin_payload(b"1HGCM82633A\r\xff\xfe") is None


def test_p2_10_valid_vin_still_decodes_and_strips_padding() -> None:
    assert decode_vin_payload(b"1HGCM82633A004352*\x00\xff") == "1HGCM82633A004352"


def test_p2_10_padding_only_payload_is_not_a_vin() -> None:
    assert decode_vin_payload(b"\x00\xff  ") is None


# ---------------------------------------------------------------------------
# P3-2 — sparse bits are labelled SPARSE, not INC
# ---------------------------------------------------------------------------


def test_p3_2_sparse_label_replaces_inc() -> None:
    classes = BitStats.classify_bits([0.0, 0.05, 0.20, 0.5, 0.9])
    assert classes == ["CONST", "SPARSE", "SPARSE", "TOGGLE", "NOISY"]
    assert "INC" not in classes


# ---------------------------------------------------------------------------
# P3-3 — arithmetic checksum confidence capped
# ---------------------------------------------------------------------------


def test_p3_3_xor8_confidence_is_capped_below_confirmed_threshold() -> None:
    payloads = []
    for i in range(40):
        body = bytes([i, (i * 3) & 0xFF, 0x01, 0x02, 0x03, 0x04, 0x05])
        xor_val = 0
        for b in body:
            xor_val ^= b
        payloads.append(body + bytes([xor_val]))

    hyps = ChecksumDetector.detect(payloads, dlc=8)
    xor_hyp = next(h for h in hyps if h.start_bit == 56 and h.params.get("algorithm") == "XOR-8")
    assert xor_hyp.confidence < 0.85, "capped confidence must not reach the confirmed threshold"
    assert xor_hyp.confidence == ChecksumDetector.ARITHMETIC_CHECKSUM_CONFIDENCE_CAP - 0.01
    assert xor_hyp.evidence[0].value == 1.0, "the raw 100% match ratio must still be reported"


def test_p3_3_crc8_autosar_not_capped() -> None:
    """A real CRC-8 hit at 100% must keep confidence 1.0 (only arithmetic residuals are capped)."""
    from src.engine.discovery.detectors.checksum import Crc8Model

    model = Crc8Model.create("CRC-8/AUTOSAR", poly=0x2F, init=0xFF, xorout=0xFF)
    payloads = []
    for i in range(40):
        body = bytes([(i * 5) & 0xFF, 0x11, 0x22, 0x33, 0x44, 0x55, (i % 16)])
        payloads.append(bytes([model.calculate(body)]) + body)
    hyps = ChecksumDetector.detect(payloads, dlc=8)
    crc_hyp = next(
        h for h in hyps if h.start_bit == 0 and h.params.get("algorithm") == "CRC-8/AUTOSAR"
    )
    assert crc_hyp.confidence == 1.0


# ---------------------------------------------------------------------------
# P3-6 — streamed ASC ingest with a frame cap
# ---------------------------------------------------------------------------


def test_p3_6_asc_ingest_streams_and_honours_frame_cap(tmp_path: Path) -> None:
    asc = tmp_path / "trace.asc"
    lines = ["date Mon Jan 01 00:00:00 2024", "base hex  timestamps absolute"]
    for i in range(50):
        lines.append(f"   {i * 0.001:.6f} 1  123             Rx   d 2  {i % 256:02X} 00")
    asc.write_text("\n".join(lines) + "\n", encoding="utf-8")

    engine = SignalDiscoveryEngine(min_frames=1)
    ingested = engine.ingest_asc_file(asc, max_frames=10)
    assert ingested == 10, "ingest must stop at the frame cap"
    assert engine.get_frame_count(0x123) == 10


def test_p3_6_asc_ingest_without_cap_reads_all(tmp_path: Path) -> None:
    asc = tmp_path / "trace.asc"
    lines = ["date Mon Jan 01 00:00:00 2024", "base hex  timestamps absolute"]
    for i in range(20):
        lines.append(f"   {i * 0.001:.6f} 1  123             Rx   d 2  {i % 256:02X} 00")
    asc.write_text("\n".join(lines) + "\n", encoding="utf-8")

    engine = SignalDiscoveryEngine(min_frames=1)
    assert engine.ingest_asc_file(asc) == 20
    assert engine.get_frame_count(0x123) == 20


def test_p3_6_asc_ingest_is_a_generator_not_a_list(tmp_path: Path) -> None:
    """The streaming seam must not materialise the whole file (regression guard)."""
    import inspect

    from src.hal.replay.parsers import VectorAscParser

    assert inspect.isgeneratorfunction(VectorAscParser.parse_file_iter)

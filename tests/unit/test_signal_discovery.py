"""Unit and integration tests for the Signal Discovery & Evidence Engine."""

from __future__ import annotations

import tempfile
from pathlib import Path

import cantools

from src.core.models.can_frame import CanFrame
from src.engine.discovery.bitstats import BitStats
from src.engine.discovery.detectors.checksum import ChecksumDetector, Crc8Model
from src.engine.discovery.detectors.counter import CounterDetector
from src.engine.discovery.engine import SignalDiscoveryEngine
from src.engine.discovery.segmenter import SignalSegmenter


def test_bitstats_shannon_entropy() -> None:
    # Constant byte sequence -> 0.0 entropy
    const_bytes = [0x55] * 100
    assert BitStats.compute_shannon_entropy(const_bytes) == 0.0

    # Two equally distributed values -> 1.0 bit entropy
    binary_bytes = [0x00, 0xFF] * 50
    assert BitStats.compute_shannon_entropy(binary_bytes) == 1.0

    # 256 distinct uniform bytes -> 8.0 bits entropy
    uniform_bytes = list(range(256))
    assert BitStats.compute_shannon_entropy(uniform_bytes) == 8.0


def test_bitstats_flip_rates_and_classification() -> None:
    # Payload sequence where byte 0 toggles bit 0 every frame, byte 1 is constant
    payloads = [
        bytes([i % 2, 0xAA, 0x00, 0x00]) for i in range(100)
    ]
    flip_rates = BitStats.compute_flip_rates(payloads, dlc=4)
    assert len(flip_rates) == 32

    # Bit 0 of byte 0 has 100% flip rate (NOISY / High toggle)
    assert flip_rates[0] == 1.0
    # Bit 1 of byte 0 has 0% flip rate (CONST)
    assert flip_rates[1] == 0.0

    classes = BitStats.classify_bits(flip_rates)
    assert classes[0] == "NOISY"
    assert classes[1] == "CONST"


def test_counter_detector_mod16_and_mod256() -> None:
    # Generate 50 frames with:
    # - Byte 0: Modulo-16 counter in low nibble (bits 0..3)
    # - Byte 1: Modulo-256 counter (bits 8..15)
    # - Bytes 2..7: Static data
    payloads = [
        bytes([i % 16, i % 256, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60])
        for i in range(50)
    ]

    hypotheses = CounterDetector.detect(payloads, dlc=8)
    assert len(hypotheses) >= 2

    # Check mod-256 detection at byte 1 (start_bit 8, length 8)
    mod256_hyp = next((h for h in hypotheses if h.start_bit == 8 and h.length == 8), None)
    assert mod256_hyp is not None
    assert mod256_hyp.params["modulus"] == 256
    assert mod256_hyp.confidence >= 0.95

    # Check mod-16 detection at byte 0 low nibble (start_bit 0, length 4)
    mod16_hyp = next((h for h in hypotheses if h.start_bit == 0 and h.length == 4), None)
    assert mod16_hyp is not None
    assert mod16_hyp.params["modulus"] == 16
    assert mod16_hyp.confidence >= 0.95


def test_checksum_detector_xor_and_crc8_autosar() -> None:
    crc_model = Crc8Model.create("CRC-8/AUTOSAR", poly=0x2F, init=0xFF, xorout=0xFF)

    # 1. Test XOR-8 Checksum at Byte 7 over Bytes 0..6
    xor_payloads: list[bytes] = []
    for i in range(40):
        body = bytes([i, (i * 3) & 0xFF, 0x01, 0x02, 0x03, 0x04, 0x05])
        xor_val = 0
        for b in body:
            xor_val ^= b
        xor_payloads.append(body + bytes([xor_val]))

    hypotheses_xor = ChecksumDetector.detect(xor_payloads, dlc=8)
    xor_hyp = next((h for h in hypotheses_xor if h.start_bit == 56 and h.params.get("algorithm") == "XOR-8"), None)
    assert xor_hyp is not None
    assert xor_hyp.confidence == 1.0

    # 2. Test CRC-8/AUTOSAR at Byte 0 over Bytes 1..7 (common VW/Audi layout)
    crc_payloads: list[bytes] = []
    for i in range(40):
        body = bytes([(i * 5) & 0xFF, 0x11, 0x22, 0x33, 0x44, 0x55, (i % 16)])
        crc_val = crc_model.calculate(body)
        crc_payloads.append(bytes([crc_val]) + body)

    hypotheses_crc = ChecksumDetector.detect(crc_payloads, dlc=8)
    crc_hyp = next(
        (h for h in hypotheses_crc if h.start_bit == 0 and h.params.get("algorithm") == "CRC-8/AUTOSAR"),
        None,
    )
    assert crc_hyp is not None
    assert crc_hyp.confidence == 1.0


def test_signal_segmenter_16bit_signal() -> None:
    # 16-bit dynamic RPM signal at bytes 2,3 (Little Endian: byte 2 LSB, byte 3 MSB)
    payloads = [
        bytes([0x00, 0x00, (800 + i * 20) & 0xFF, ((800 + i * 20) >> 8) & 0xFF, 0x55, 0x55])
        for i in range(40)
    ]
    occupied = [(0, 16)]  # bytes 0,1 are occupied

    hypotheses = SignalSegmenter.segment(payloads, dlc=6, occupied_spans=occupied)
    sig16 = next((h for h in hypotheses if h.start_bit == 16 and h.length == 16), None)

    assert sig16 is not None
    assert sig16.htype == "SIGNAL"
    assert sig16.is_little_endian is True
    assert sig16.min_value == 800.0
    assert sig16.max_value == 800.0 + 39 * 20


def test_dbc_builder_and_file_export() -> None:
    engine = SignalDiscoveryEngine()

    # Create synthetic frames for CAN ID 0x120
    # Byte 0: Counter mod 16
    # Byte 1..2: 16-bit Speed signal (Little Endian)
    # Byte 3: Checksum XOR-8
    frames = [
        CanFrame.create(
            channel_id="ch0",
            arbitration_id=0x120,
            data=bytes([i % 16, (i * 10) & 0xFF, ((i * 10) >> 8) & 0xFF, (i % 16) ^ ((i * 10) & 0xFF) ^ (((i * 10) >> 8) & 0xFF)]),
            timestamp_ns=i * 10_000_000,
        )
        for i in range(30)
    ]
    engine.ingest_frames(frames)
    report = engine.analyze_id(0x120)

    assert report.frame_count == 30
    assert len(report.hypotheses) > 0

    # Build cantools Database
    db = engine.build_dbc(approved_only=False)
    assert len(db.messages) == 1
    assert db.messages[0].frame_id == 0x120

    # Export to temp DBC file and load back with cantools
    with tempfile.NamedTemporaryFile(suffix=".dbc", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        engine.export_dbc(tmp_path, approved_only=False)
        loaded_db = cantools.database.load_file(tmp_path)
        assert len(loaded_db.messages) == 1
        assert loaded_db.messages[0].frame_id == 0x120
    finally:
        tmp_path.unlink(missing_ok=True)


def test_signal_discovery_engine_evidence_markdown() -> None:
    engine = SignalDiscoveryEngine()
    frames = [
        CanFrame.create(
            channel_id="ch0",
            arbitration_id=0x250,
            data=bytes([i % 16, 0x00, 0xAA, 0x55]),
            timestamp_ns=i * 20_000_000,
        )
        for i in range(25)
    ]
    engine.ingest_frames(frames)
    md_report = engine.generate_evidence_markdown(0x250)

    assert "# Signal Discovery Evidence Report: CAN ID 0x0250" in md_report
    assert "Byte Entropy Profile" in md_report
    assert "Discovered Hypotheses" in md_report


def test_signal_discovery_on_benchmark_trace_file() -> None:
    bench_trace = Path(__file__).resolve().parent.parent / "fixtures" / "benchmarks" / "vectors" / "road_correlated_signal.asc"
    assert bench_trace.exists(), f"Benchmark trace missing: {bench_trace}"

    engine = SignalDiscoveryEngine(min_frames=10)
    count = engine.ingest_asc_file(bench_trace)
    assert count > 0
    assert len(engine.discovered_ids) > 0

    reports = engine.analyze_all()
    assert len(reports) > 0

    # Build DBC from real trace discovery
    db = engine.build_dbc(approved_only=False)
    assert len(db.messages) > 0


def test_hypothesis_approval_workflow() -> None:
    engine = SignalDiscoveryEngine()
    frames = [
        CanFrame.create(
            channel_id="ch0",
            arbitration_id=0x300,
            data=bytes([i % 16, (i * 2) & 0xFF]),
            timestamp_ns=i * 10_000_000,
        )
        for i in range(20)
    ]
    engine.ingest_frames(frames)
    report = engine.analyze_id(0x300)

    # Initial status is 'candidate'
    assert any(h.status == "candidate" for h in report.hypotheses)

    # Approve the counter hypothesis
    approved = engine.approve_hypothesis(0x300, start_bit=0, length=4)
    assert approved is True

    # Database with approved_only=True should only include approved signals
    db_approved = engine.build_dbc(approved_only=True)
    assert len(db_approved.messages) == 1
    assert any(s.name.startswith("COUNTER") for s in db_approved.messages[0].signals)


# ============================================================================
# Evidence Scoring & Confidence Reports (spec §1 evidence.py contract)
# ============================================================================


def test_evidence_scoring_definitive_crc_hypothesis() -> None:
    """Positive: a hypothesis whose CRC evidence reaches 0.99+ scores definitive."""
    from src.engine.discovery.evidence import score_hypothesis
    from src.engine.discovery.hypotheses import Evidence, Hypothesis

    hyp = Hypothesis(
        htype="CHECKSUM",
        start_bit=0,
        length=8,
        evidence=[
            Evidence(kind="crc_match_ratio", value=0.995, detail="CRC-8 0x1D matched 99.5% of frames"),
        ],
    )
    report = score_hypothesis(hyp)
    assert report.verdict == "definitive"
    assert report.is_exportable is True
    assert any("CRC-8" in line for line in report.explanations)


def test_evidence_scoring_weak_without_evidence() -> None:
    """Negative: a hypothesis with no evidence scores weak and is not exportable."""
    from src.engine.discovery.evidence import score_hypothesis
    from src.engine.discovery.hypotheses import Hypothesis

    report = score_hypothesis(Hypothesis(htype="SIGNAL", start_bit=0, length=16))
    assert report.verdict == "weak"
    assert report.is_exportable is False
    assert report.weighted_score == 0.0


def test_evidence_scoring_boundary_thresholds() -> None:
    """Boundary: verdicts flip exactly at the spec §4 thresholds 0.90 / 0.80."""
    from src.engine.discovery.evidence import score_hypothesis
    from src.engine.discovery.hypotheses import Evidence, Hypothesis

    def _score(value: float) -> str:
        hyp = Hypothesis(htype="COUNTER", start_bit=0, length=4, evidence=[Evidence("monotonicity", value, "delta+1 ratio")])
        return score_hypothesis(hyp).verdict

    assert _score(1.0) == "definitive"
    assert _score(0.90) == "candidate"
    assert _score(0.80) == "hint"
    assert _score(0.5) == "weak"


def test_evidence_report_is_human_readable() -> None:
    """Spec §3: evidence_report() yields a human-readable multi-line verdict."""
    from src.engine.discovery.evidence import evidence_report
    from src.engine.discovery.hypotheses import Evidence, Hypothesis

    hyp = Hypothesis(
        htype="COUNTER",
        start_bit=8,
        length=4,
        evidence=[Evidence("monotonicity", 0.96, "Bayt1 karelerin %96'sında +1 artıyor (mod 16)")],
    )
    text = evidence_report(hyp)
    assert "COUNTER" in text
    assert "bits 8..11" in text
    assert "%96" in text
    assert "Verdict:" in text

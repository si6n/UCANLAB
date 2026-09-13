import tempfile
import threading
import time
from pathlib import Path

import pytest

from src.core.models.can_frame import CanFrame
from src.hal.replay.parsers import CsvParser, VectorAscParser, VectorBlfParser
from src.hal.replay.player import ReplayBus

SAMPLE_ASC_CONTENT = """date Mon Aug 24 12:00:00 2026
base hex  timestamps absolute
internal events logged
// Sample CAN traffic
   0.000000 1  18FEEE00x       Rx   d 8 01 02 03 04 05 06 07 08
   0.050000 1  18FEF200x       Rx   d 8 10 20 30 40 50 60 70 80
   0.100000 CANFD 1 Rx 123 1 0 12 12 01 02 03 04 05 06 07 08 09 0A 0B 0C
"""


def test_vector_asc_parser() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".asc", delete=False) as tmp:
        tmp.write(SAMPLE_ASC_CONTENT)
        tmp_path = Path(tmp.name)

    try:
        frames = VectorAscParser.parse_file(tmp_path)
        assert len(frames) == 3

        # Frame 1: Classic CAN J1939 (Engine Temp)
        f1 = frames[0]
        assert f1.channel_id == "ch1"
        assert f1.arbitration_id == 0x18FEEE00
        assert f1.is_extended is True
        assert f1.is_fd is False
        assert f1.dlc == 8
        assert f1.data == b"\x01\x02\x03\x04\x05\x06\x07\x08"
        assert f1.timestamp_ns == 0

        # Frame 2: Classic CAN J1939 (Fuel Economy)
        f2 = frames[1]
        assert f2.arbitration_id == 0x18FEF200
        assert f2.timestamp_ns == 50_000_000  # 50ms = 50,000,000ns

        # Frame 3: CAN-FD frame
        f3 = frames[2]
        assert f3.arbitration_id == 0x123
        assert f3.is_extended is False
        assert f3.is_fd is True
        assert f3.brs is True
        assert f3.dlc == 12
        assert len(f3.data) == 12
        assert f3.timestamp_ns == 100_000_000
    finally:
        tmp_path.unlink(missing_ok=True)


def test_replay_bus_step_and_reset() -> None:
    frames = [
        CanFrame.create(channel_id="ch1", arbitration_id=0x100, data=b"\x01", timestamp_ns=0),
        CanFrame.create(channel_id="ch1", arbitration_id=0x200, data=b"\x02", timestamp_ns=1000),
    ]
    bus = ReplayBus(frames)
    assert bus.frame_count == 2
    assert bus.has_next is True

    f1 = bus.step()
    assert f1 is not None
    assert f1.arbitration_id == 0x100

    f2 = bus.step()
    assert f2 is not None
    assert f2.arbitration_id == 0x200

    assert bus.step() is None
    assert bus.has_next is False

    bus.reset()
    assert bus.has_next is True
    assert bus.step() == f1


def test_replay_bus_play() -> None:
    frames = [
        CanFrame.create(channel_id="ch1", arbitration_id=0x100, data=b"\x01", timestamp_ns=0),
        CanFrame.create(channel_id="ch1", arbitration_id=0x200, data=b"\x02", timestamp_ns=10_000),
    ]
    bus = ReplayBus(frames)
    received: list[CanFrame] = []

    bus.play(callback=lambda f: received.append(f), speed=100.0)
    assert len(received) == 2
    assert received[0].arbitration_id == 0x100
    assert received[1].arbitration_id == 0x200


def test_replay_bus_timing_accuracy() -> None:
    """Verify master clock synchronization with 10ms intervals."""
    # 3 frames separated by 10ms: 0ms, 10ms, 20ms
    frames = [
        CanFrame.create(channel_id="ch1", arbitration_id=0x100, data=b"\x01", timestamp_ns=0),
        CanFrame.create(channel_id="ch1", arbitration_id=0x200, data=b"\x02", timestamp_ns=10_000_000),
        CanFrame.create(channel_id="ch1", arbitration_id=0x300, data=b"\x03", timestamp_ns=20_000_000),
    ]
    bus = ReplayBus(frames)
    received: list[tuple[CanFrame, float]] = []

    t_start = time.perf_counter()
    bus.play(callback=lambda f: received.append((f, time.perf_counter())), speed=1.0)
    t_elapsed = time.perf_counter() - t_start

    assert len(received) == 3
    # Total duration should be ~20ms (0.020s) with tight tolerance
    assert 0.015 <= t_elapsed <= 0.050


def test_replay_bus_stop_event_cancellation() -> None:
    """Verify playback terminates immediately when stop_event is triggered."""
    frames = [
        CanFrame.create(channel_id="ch1", arbitration_id=0x100 + i, data=b"\x00", timestamp_ns=i * 20_000_000)
        for i in range(10)
    ]
    bus = ReplayBus(frames)
    stop_event = threading.Event()
    received: list[CanFrame] = []

    def on_frame(f: CanFrame) -> None:
        received.append(f)
        if len(received) == 2:
            stop_event.set()

    bus.play(callback=on_frame, speed=1.0, stop_event=stop_event)
    assert len(received) == 2


def test_replay_bus_invalid_speed_and_empty() -> None:
    bus = ReplayBus([])
    bus.play(callback=lambda f: None)  # Empty play should return immediately

    bus_with_frames = ReplayBus([CanFrame.create(channel_id="ch0", arbitration_id=0x100, data=b"")])
    with pytest.raises(ValueError, match="Replay speed must be positive"):
        bus_with_frames.play(callback=lambda f: None, speed=0.0)

    with pytest.raises(ValueError, match="Replay speed must be positive"):
        bus_with_frames.play(callback=lambda f: None, speed=-1.0)


def test_replay_bus_load_frames() -> None:
    bus = ReplayBus()
    assert bus.frame_count == 0
    f1 = CanFrame.create(channel_id="ch1", arbitration_id=0x555, data=b"\x01")
    bus.load_frames([f1])
    assert bus.frame_count == 1
    assert bus.step() == f1
    assert bus.has_next is False


# ============================================================================
# CsvParser + from_trace_file routing (K3-a)
# ============================================================================

SAMPLE_CSV_CONTENT = """time,id,dlc,data,channel,dir,extended
0.000000,0x18FEEE00,8,01 02 03 04 05 06 07 08,1,rx,1
0.050000,1F4,2,DE AD,2,tx,0
0.100000,0x123,4,aa bb cc dd,,,,
not-a-time,0x1,1,FF,1,rx,0
0.200000,0x2,2,
"""


def test_csv_parser_full_header() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tmp:
        tmp.write(SAMPLE_CSV_CONTENT)
        tmp_path = Path(tmp.name)

    try:
        frames = CsvParser.parse_file(tmp_path)
        # 3 valid rows; malformed time row and empty-data row skipped
        assert len(frames) == 3

        f1 = frames[0]
        assert f1.arbitration_id == 0x18FEEE00
        assert f1.is_extended is True
        assert f1.dlc == 8
        assert f1.data == b"\x01\x02\x03\x04\x05\x06\x07\x08"
        assert f1.channel_id == "1"
        assert f1.direction == "rx"
        assert f1.timestamp_ns == 0
        assert f1.source == "replay"

        f2 = frames[1]
        assert f2.arbitration_id == 0x1F4  # bare hex, no 0x prefix
        assert f2.is_extended is False  # explicit 0
        assert f2.dlc == 2
        assert f2.data == b"\xDE\xAD"
        assert f2.direction == "tx"

        f3 = frames[2]
        assert f3.channel_id != ""  # empty channel falls back to file stem
        assert f3.is_extended is False  # 0x123 < 0x7FF auto-detected
    finally:
        tmp_path.unlink(missing_ok=True)


def test_csv_parser_alias_headers_and_dlc_default() -> None:
    content = """Timestamp,Identifier,Payload
0.100,0x7FF,AA BB CC
"""
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        frames = CsvParser.parse_file(tmp_path)
        assert len(frames) == 1
        f = frames[0]
        assert f.arbitration_id == 0x7FF
        assert f.dlc == 3  # dlc column absent -> payload byte count
        assert f.data == b"\xAA\xBB\xCC"
    finally:
        tmp_path.unlink(missing_ok=True)


def test_csv_parser_missing_mandatory_column_returns_empty() -> None:
    content = """time,channel
0.1,1
"""
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        assert CsvParser.parse_file(tmp_path) == []
    finally:
        tmp_path.unlink(missing_ok=True)


def test_csv_parser_empty_file_returns_empty() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tmp:
        tmp.write("")
        tmp_path = Path(tmp.name)
    try:
        assert CsvParser.parse_file(tmp_path) == []
    finally:
        tmp_path.unlink(missing_ok=True)


def test_replay_bus_from_csv_file_roundtrip() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tmp:
        tmp.write(SAMPLE_CSV_CONTENT)
        tmp_path = Path(tmp.name)
    try:
        bus = ReplayBus.from_csv_file(tmp_path)
        assert bus.frame_count == 3
        first = bus.step()
        assert first is not None
        assert first.arbitration_id == 0x18FEEE00
    finally:
        tmp_path.unlink(missing_ok=True)


def test_replay_bus_from_trace_file_routes_by_extension() -> None:
    # .asc routes to the Vector parser
    with tempfile.NamedTemporaryFile("w", suffix=".asc", delete=False) as tmp:
        tmp.write(SAMPLE_ASC_CONTENT)
        asc_path = Path(tmp.name)
    try:
        bus = ReplayBus.from_trace_file(asc_path)
        assert bus.frame_count == 3
    finally:
        asc_path.unlink(missing_ok=True)

    # .csv routes to the CSV parser
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tmp:
        tmp.write(SAMPLE_CSV_CONTENT)
        csv_path = Path(tmp.name)
    try:
        bus = ReplayBus.from_trace_file(csv_path)
        assert bus.frame_count == 3
    finally:
        csv_path.unlink(missing_ok=True)

    # Unknown extension (.xyz!) fails LOUDLY
    with tempfile.NamedTemporaryFile("w", suffix=".xyz", delete=False) as tmp:
        tmp.write("whatever")
        xyz_path = Path(tmp.name)
    try:
        with pytest.raises(ValueError, match="Unsupported trace format"):
            ReplayBus.from_trace_file(xyz_path)
    finally:
        xyz_path.unlink(missing_ok=True)


# ============================================================================
# VectorBlfParser + BLF replay routing (K3-b)
# ============================================================================

def test_blf_parser_classic_and_fd_roundtrip() -> None:
    import can

    with tempfile.NamedTemporaryFile(suffix=".blf", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        writer = can.BLFWriter(str(tmp_path))
        # Classic CAN J1939 frame
        msg1 = can.Message(
            arbitration_id=0x18FEEE00,
            is_extended_id=True,
            is_fd=False,
            is_rx=True,
            channel=1,
            data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
            timestamp=0.0,
        )
        # CAN-FD frame with BRS
        msg2 = can.Message(
            arbitration_id=0x123,
            is_extended_id=False,
            is_fd=True,
            bitrate_switch=True,
            error_state_indicator=False,
            is_rx=False,
            channel=2,
            data=b"\x10\x20\x30\x40\x50\x60\x70\x80\x90\xA0\xB0\xC0",
            timestamp=0.05,
        )
        writer.on_message_received(msg1)
        writer.on_message_received(msg2)
        writer.stop()

        frames = VectorBlfParser.parse_file(tmp_path)
        assert len(frames) == 2

        f1 = frames[0]
        assert f1.channel_id == "ch1"
        assert f1.arbitration_id == 0x18FEEE00
        assert f1.is_extended is True
        assert f1.is_fd is False
        assert f1.dlc == 8
        assert f1.data == b"\x01\x02\x03\x04\x05\x06\x07\x08"
        assert f1.direction == "rx"
        assert f1.timestamp_ns == 0
        assert f1.source == "replay"

        f2 = frames[1]
        assert f2.channel_id == "ch2"
        assert f2.arbitration_id == 0x123
        assert f2.is_extended is False
        assert f2.is_fd is True
        assert f2.brs is True
        assert f2.esi is False
        assert f2.dlc == 12
        assert f2.data == b"\x10\x20\x30\x40\x50\x60\x70\x80\x90\xA0\xB0\xC0"
        assert f2.direction == "tx"
        assert f2.timestamp_ns == 50_000_000

        # Also test via ReplayBus.from_blf_file and from_trace_file
        bus1 = ReplayBus.from_blf_file(tmp_path)
        assert bus1.frame_count == 2
        assert bus1.step() == f1

        bus2 = ReplayBus.from_trace_file(tmp_path)
        assert bus2.frame_count == 2
        assert bus2.step() == f1
    finally:
        tmp_path.unlink(missing_ok=True)


def test_blf_parser_corrupted_file_resilience() -> None:
    with tempfile.NamedTemporaryFile(suffix=".blf", delete=False) as tmp:
        tmp.write(b"CORRUPTED_BLF_HEADER_BYTES_RANDOM_NOISE_1234567890")
        tmp_path = Path(tmp.name)

    try:
        # Malformed BLF should return empty frame list gracefully without unhandled crash
        frames = VectorBlfParser.parse_file(tmp_path)
        assert frames == []
    finally:
        tmp_path.unlink(missing_ok=True)


def test_blf_parser_nonexistent_file_raises_filenotfound() -> None:
    with pytest.raises(FileNotFoundError, match="Trace file not found"):
        VectorBlfParser.parse_file("nonexistent_path_test_blf.blf")



def test_vector_asc_parser_skips_malformed_lines() -> None:
    """Y-06 regression: a single malformed ASC line must not abort the load."""
    content = """date Mon Aug 24 12:00:00 2026
base hex  timestamps absolute
// Good frame followed by a DLC/payload-mismatched frame, then a good one
   0.000000 1  123       Rx   d 8 01 02 03
   0.050000 1  456       Rx   d 2 AA BB
"""
    with tempfile.NamedTemporaryFile("w", suffix=".asc", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        frames = VectorAscParser.parse_file(tmp_path)
        # The DLC=8/3-byte frame is rejected by the CanFrame invariant,
        # but the DLC=2 frame survives — parse continues past the bad line.
        assert len(frames) == 1
        assert frames[0].arbitration_id == 0x456
        assert frames[0].data == b"\xAA\xBB"
    finally:
        tmp_path.unlink(missing_ok=True)


def test_vector_asc_parser_iter_streams_lazily() -> None:
    """Y-06: the iterator variant never materializes the whole trace."""
    content = """date Mon Aug 24 12:00:00 2026
base hex  timestamps absolute
   0.000000 1  123       Rx   d 2 AA BB
   0.050000 1  456       Rx   d 2 CC DD
"""
    with tempfile.NamedTemporaryFile("w", suffix=".asc", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        it = VectorAscParser.parse_file_iter(tmp_path)
        first = next(it)
        assert first.arbitration_id == 0x123
        second = next(it)
        assert second.arbitration_id == 0x456
        with pytest.raises(StopIteration):
            next(it)
    finally:
        tmp_path.unlink(missing_ok=True)


# ============================================================================
# REVIEW 3: thread-safety & callback fault tolerance
# ============================================================================


def test_replay_bus_callback_exception_does_not_kill_playback() -> None:
    """REVIEW 3 regression: a raising consumer must not silently kill the
    replay thread — the error is counted (callback_errors/dropped_frames),
    logged, and playback continues deterministically."""
    frames = [
        CanFrame.create(channel_id="ch1", arbitration_id=0x100 + i, data=b"\x01", timestamp_ns=i * 10_000)
        for i in range(5)
    ]
    bus = ReplayBus(frames)
    ok: list[CanFrame] = []

    def on_frame(f: CanFrame) -> None:
        if f.arbitration_id == 0x101:
            raise RuntimeError("consumer pipeline blowup")
        ok.append(f)

    bus.play(callback=on_frame, speed=100.0)  # must NOT raise

    assert len(ok) == 4  # all other frames delivered
    assert bus.callback_errors == 1
    assert bus.dropped_frames == 1


def test_replay_bus_concurrent_load_frames_and_step_are_safe() -> None:
    """REVIEW 3 regression: play/step/load_frames are serialized by a lock —
    a concurrent load_frames([]) during playback terminates cleanly instead
    of tearing the in-flight iteration (IndexError)."""
    frames = [
        CanFrame.create(channel_id="ch1", arbitration_id=0x100 + i, data=b"\x01", timestamp_ns=i * 1_000_000)
        for i in range(200)
    ]
    bus = ReplayBus(frames)
    received: list[CanFrame] = []
    stop = threading.Event()

    def player() -> None:
        # Errors must not escape the worker thread; track any crash.
        try:
            bus.play(callback=received.append, speed=50.0, stop_event=stop, loop=True)
        except Exception:
            pass

    t = threading.Thread(target=player, daemon=True)
    t.start()
    time.sleep(0.02)  # mid-playback...
    bus.load_frames([])  # ...swap the trace under the runner's feet
    time.sleep(0.02)
    stop.set()
    t.join(timeout=2.0)

    assert not t.is_alive()  # worker exited cleanly, never crashed mid-iteration

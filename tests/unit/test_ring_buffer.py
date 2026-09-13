import concurrent.futures

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from src.core.models.can_frame import CanFrame
from src.engine.buffer.ring_buffer import BinaryRingBuffer


def test_ring_buffer_append_and_retrieve() -> None:
    buf = BinaryRingBuffer(capacity=100)
    assert buf.capacity == 100
    assert buf.current_size == 0
    assert buf.total_written == 0

    frame = CanFrame.create(
        channel_id="engine0",
        arbitration_id=0x18FEEE00,
        data=b"\x01\x02\x03\x04",
        timestamp_ns=1000,
    )

    seq = buf.append(frame)
    assert seq == 0
    assert buf.total_written == 1
    assert buf.current_size == 1

    latest = buf.get_latest_frames(10)
    assert len(latest) == 1
    assert latest[0].arbitration_id == 0x18FEEE00
    assert latest[0].data == b"\x01\x02\x03\x04"
    assert latest[0].channel_id == "engine0"


def test_ring_buffer_wrapping() -> None:
    buf = BinaryRingBuffer(capacity=10)

    for i in range(25):
        frame = CanFrame.create(
            channel_id="test",
            arbitration_id=i,
            data=bytes([i % 256]),
            timestamp_ns=i * 1000,
        )
        buf.append(frame)

    assert buf.total_written == 25
    assert buf.current_size == 10

    # Retrieve last 5 frames (should be IDs 20, 21, 22, 23, 24)
    latest_5 = buf.get_latest_frames(5)
    assert len(latest_5) == 5
    assert [f.arbitration_id for f in latest_5] == [20, 21, 22, 23, 24]

    # Retrieve all available 10 frames (should be IDs 15..24)
    latest_10 = buf.get_latest_frames(20)
    assert len(latest_10) == 10
    assert [f.arbitration_id for f in latest_10] == list(range(15, 25))


def test_ring_buffer_batch_and_clear() -> None:
    buf = BinaryRingBuffer(capacity=50)
    batch = [CanFrame.create(channel_id="ch0", arbitration_id=x, data=b"\xaa") for x in range(30)]
    buf.append_batch(batch)
    assert buf.total_written == 30

    buf.clear()
    assert buf.total_written == 0
    assert buf.current_size == 0
    assert len(buf.get_latest_frames(10)) == 0


def test_ring_buffer_two_phase_read_wraparound_exact() -> None:
    """Test two-phase read exactly when slice spans wrap-around ring boundary."""
    buf = BinaryRingBuffer(capacity=8)

    for i in range(12):
        buf.append(
            CanFrame.create(
                channel_id="vcan0",
                arbitration_id=0x100 + i,
                data=bytes([i] * 8),
                timestamp_ns=i * 1_000_000,
            )
        )

    assert buf.total_written == 12
    assert buf.current_size == 8

    # Request latest 6 frames -> sequences 6, 7, 8, 9, 10, 11
    # Buffer slots: 6%8=6, 7%8=7 (part1), 8%8=0, 9%8=1, 10%8=2, 11%8=3 (part2)
    latest_6 = buf.get_latest_frames(6)
    assert len(latest_6) == 6
    assert [f.arbitration_id for f in latest_6] == [0x100 + i for i in range(6, 12)]
    assert [f.sequence for f in latest_6] == list(range(6, 12))


def test_ring_buffer_can_fd_flags_and_variable_payloads() -> None:
    """Verify in-place structured array writes for CAN-FD, BRS, ESI, TX, and 64-byte payloads."""
    buf = BinaryRingBuffer(capacity=10)

    # 64-byte CAN-FD frame
    fd_data = bytes(range(64))
    fd_frame = CanFrame(
        channel_id="canfd_ch1",
        arbitration_id=0x12345678,
        dlc=15,
        data=fd_data,
        is_extended=True,
        is_fd=True,
        brs=True,
        esi=True,
        direction="tx",
        timestamp_ns=50_000,
    )
    buf.append(fd_frame)

    # 0-byte frame
    empty_frame = CanFrame(
        channel_id="empty_ch",
        arbitration_id=0x700,
        dlc=0,
        data=b"",
        is_extended=False,
        direction="rx",
        timestamp_ns=60_000,
    )
    buf.append(empty_frame)

    frames = buf.get_latest_frames(2)
    assert len(frames) == 2

    # Verify FD frame
    f0 = frames[0]
    assert f0.arbitration_id == 0x12345678
    assert f0.is_extended is True
    assert f0.is_fd is True
    assert f0.brs is True
    assert f0.esi is True
    assert f0.direction == "tx"
    assert f0.data == fd_data
    assert f0.channel_id == "canfd_ch1"

    # Verify empty frame
    f1 = frames[1]
    assert f1.arbitration_id == 0x700
    assert f1.data == b""
    assert f1.channel_id == "empty_ch"
    assert f1.direction == "rx"


def test_ring_buffer_concurrent_append_and_read() -> None:
    """Verify thread safety during high-rate concurrent appends and reads."""
    buf = BinaryRingBuffer(capacity=500)
    total_appends = 200

    def writer_task(worker_id: int) -> None:
        for i in range(total_appends):
            frame = CanFrame.create(
                channel_id=f"worker_{worker_id}",
                arbitration_id=0x100 + worker_id,
                data=bytes([worker_id, i % 256]),
            )
            buf.append(frame)

    def reader_task() -> None:
        for _ in range(50):
            frames = buf.get_latest_frames(50)
            assert isinstance(frames, list)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        writer_futures = [executor.submit(writer_task, wid) for wid in range(4)]
        reader_futures = [executor.submit(reader_task) for _ in range(2)]

        for f in writer_futures + reader_futures:
            f.result()

    assert buf.total_written == 4 * total_appends
    assert buf.current_size == 500


@given(
    capacity=st.integers(min_value=5, max_value=50),
    append_count=st.integers(min_value=1, max_value=150),
    read_count=st.integers(min_value=1, max_value=100),
)
def test_ring_buffer_hypothesis_property_invariants(capacity: int, append_count: int, read_count: int) -> None:
    """Hypothesis property-based test verifying mathematical invariants on ring buffer wrap."""
    buf = BinaryRingBuffer(capacity=capacity)
    for i in range(append_count):
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=b"\x01\x02"))

    assert buf.total_written == append_count
    assert buf.current_size == min(append_count, capacity)

    latest = buf.get_latest_frames(read_count)
    expected_len = min(read_count, append_count, capacity)
    assert len(latest) == expected_len

    if expected_len > 0:
        expected_start_id = append_count - expected_len
        assert [f.arbitration_id for f in latest] == list(range(expected_start_id, append_count))
        assert [f.sequence for f in latest] == list(range(expected_start_id, append_count))


# ============================================================================
# F-33 / E-13: Genuine zero-copy get_latest_view DoD tests
# ============================================================================


def test_get_latest_view_is_true_zero_copy() -> None:
    """F-33 DoD: with copy=False both returned parts must share memory with
    the buffer storage (explicit zero-copy opt-in for lock-disciplined readers)."""
    buf = BinaryRingBuffer(capacity=8)
    for i in range(20):
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=bytes([i & 0xFF] * 4)))

    old_part, new_part, _ = buf.get_latest_view(8, copy=False)
    assert len(old_part) + len(new_part) == 8
    # E-13: genuine views — no concatenate, no copy
    assert np.shares_memory(old_part, buf._buffer)
    assert np.shares_memory(new_part, buf._buffer) or new_part.size == 0
    ids = list(old_part["arbitration_id"]) + list(new_part["arbitration_id"])
    assert ids == list(range(12, 20))


def test_get_latest_view_default_returns_detached_copy() -> None:
    """E-C-001 regression: the default return is a detached copy, so a
    concurrent writer cannot mutate data a reader still holds."""
    buf = BinaryRingBuffer(capacity=8)
    for i in range(8):
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=bytes([i & 0xFF] * 4)))

    old_part, new_part, _ = buf.get_latest_view(8)
    assert not np.shares_memory(old_part, buf._buffer) or old_part.size == 0
    snapshot_ids = list(old_part["arbitration_id"]) + list(new_part["arbitration_id"])

    # Overwrite the buffer with new frames — the held copy must be unaffected
    for i in range(100, 108):
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=bytes([i & 0xFF] * 4)))

    assert snapshot_ids == list(range(0, 8))


def test_get_latest_view_wraparound_ordering() -> None:
    """F-33: wrapped ranges return (oldest_segment, newest_segment) in order."""
    buf = BinaryRingBuffer(capacity=4)
    for i in range(10):
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=b""))

    old_part, new_part, _ = buf.get_latest_view(3, copy=False)
    # total_written=10, cap=4: last 3 frames are 7,8,9; wrap index = 10%4 = 2
    ids = list(old_part["arbitration_id"]) + list(new_part["arbitration_id"])
    assert ids == [7, 8, 9]
    # wrapped case: the tail segment (older, lower ids) precedes the
    # head segment (newer, higher ids written after the wrap point)
    if len(old_part) > 0 and len(new_part) > 0:
        assert old_part["arbitration_id"][-1] < new_part["arbitration_id"][0]


def test_get_latest_view_empty_request() -> None:
    """F-33: zero/negative requests return two empty views without error."""
    buf = BinaryRingBuffer(capacity=8)
    for i in range(5):
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=b""))

    old_part, new_part, _ = buf.get_latest_view(0)
    assert old_part.size == 0 and new_part.size == 0

    empty_buf = BinaryRingBuffer(capacity=8)
    o, n, _ = empty_buf.get_latest_view(10)
    assert o.size == 0 and n.size == 0

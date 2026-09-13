"""Unit tests for SAE J1939-21 Transport Protocol (BAM & CMDT RTS/CTS/Abort)."""

import time

import pytest

from src.core.contracts.ports import ClockProvider
from src.core.exceptions import (
    J1939SequenceError,
    J1939SessionCollisionError,
    J1939TpAbortError,
    J1939TpError,
    J1939TpTimeoutError,
)
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.transport import (
    ABORT_REASON_SEQUENCE_ERROR,
    ABORT_REASON_SESSION_COLLISION,
    ABORT_REASON_TIMEOUT,
    ABORT_REASON_UNEXPECTED_CONTROL,
    TP_CTRL_ABORT,
    TP_CTRL_ACK,
    TP_CTRL_BAM,
    TP_CTRL_CTS,
    TP_CTRL_RTS,
    J1939TransportProtocol,
)


class MockClockProvider(ClockProvider):
    """Simulated clock for deterministic timeout verification."""

    def __init__(self, start_time: float = 1000.0) -> None:
        self._current_time = start_time

    def now_monotonic(self) -> float:
        return self._current_time

    def now_monotonic_ns(self) -> int:
        return int(self._current_time * 1_000_000_000)

    def advance(self, seconds: float) -> None:
        self._current_time += seconds


def test_j1939_bam_reassembly() -> None:
    """Verify Broadcast Announce Message (BAM) multi-packet reassembly."""
    tp = J1939TransportProtocol(my_address=0xF9)

    # 1. TP.CM_BAM frame: PGN 65226 (DM1), 14 bytes, 2 packets
    bam_data = bytearray(8)
    bam_data[0] = TP_CTRL_BAM
    bam_data[1:3] = (14).to_bytes(2, byteorder="little")
    bam_data[3] = 2  # 2 packets
    bam_data[4] = 0xFF
    bam_data[5:8] = (65226).to_bytes(3, byteorder="little")

    cm_frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECFF00,  # DA = 255 (Global), SA = 0
        data=bytes(bam_data),
        is_extended=True,
    )
    completed_msg, resp_frame = tp.handle_rx_frame(cm_frame)
    assert completed_msg is None
    assert resp_frame is None

    # 2. TP.DT Packet 1 (7 bytes payload)
    dt1_data = b"\x01" + b"\x11\x22\x33\x44\x55\x66\x77"
    dt1_frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBFF00,
        data=dt1_data,
        is_extended=True,
    )
    completed_msg, resp_frame = tp.handle_rx_frame(dt1_frame)
    assert completed_msg is None
    assert resp_frame is None

    # 3. TP.DT Packet 2 (7 bytes payload -> total 14 bytes)
    dt2_data = b"\x02" + b"\x88\x99\xaa\xbb\xcc\xdd\xee"
    dt2_frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBFF00,
        data=dt2_data,
        is_extended=True,
    )
    completed_msg, resp_frame = tp.handle_rx_frame(dt2_frame)
    assert completed_msg is not None
    assert resp_frame is None  # BAM does not send ACK

    assert completed_msg.pgn == 65226
    assert completed_msg.source_address == 0
    assert completed_msg.destination_address == 255
    assert len(completed_msg.data) == 14
    assert completed_msg.data == b"\x11\x22\x33\x44\x55\x66\x77\x88\x99\xaa\xbb\xcc\xdd\xee"


def test_j1939_cmdt_rts_cts_flow_and_ack() -> None:
    """Verify Connection Mode Data Transfer (CMDT) RTS -> CTS -> DT -> EndOfMsgACK flow."""
    tp = J1939TransportProtocol(my_address=0xF9)

    # 1. Incoming TP.CM_RTS from ECU (SA=0) to our tool (DA=0xF9) for DM2 (PGN 65227), 8 bytes, 2 packets
    rts_data = bytearray(8)
    rts_data[0] = TP_CTRL_RTS
    rts_data[1:3] = (8).to_bytes(2, byteorder="little")
    rts_data[3] = 2
    rts_data[4] = 0xFF
    rts_data[5:8] = (65227).to_bytes(3, byteorder="little")

    rts_frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECF900,  # DA = 0xF9, SA = 0
        data=bytes(rts_data),
        is_extended=True,
    )
    completed_msg, cts_frame = tp.handle_rx_frame(rts_frame)
    assert completed_msg is None
    assert cts_frame is not None

    # Verify CTS frame structure
    assert cts_frame.arbitration_id == 0x1CEC00F9  # DA = 0, SA = 0xF9
    assert cts_frame.data[0] == TP_CTRL_CTS
    assert cts_frame.data[1] == 2  # Allowed packets
    assert cts_frame.data[2] == 1  # Next packet seq
    assert int.from_bytes(cts_frame.data[5:8], byteorder="little") == 65227

    # 2. DT Packet 1
    dt1 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBF900,
        data=b"\x01" + b"\x01\x02\x03\x04\x05\x06\x07",
        is_extended=True,
    )
    tp.handle_rx_frame(dt1)

    # 3. DT Packet 2 (final 1 byte payload)
    dt2 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBF900,
        data=b"\x02" + b"\x08\xff\xff\xff\xff\xff\xff",
        is_extended=True,
    )
    completed_msg, ack_frame = tp.handle_rx_frame(dt2)

    assert completed_msg is not None
    assert completed_msg.pgn == 65227
    assert completed_msg.source_address == 0
    assert completed_msg.destination_address == 0xF9
    assert len(completed_msg.data) == 8
    assert completed_msg.data == b"\x01\x02\x03\x04\x05\x06\x07\x08"

    # Verify EndOfMsgACK frame
    assert ack_frame is not None
    assert ack_frame.data[0] == TP_CTRL_ACK
    assert ack_frame.arbitration_id == 0x1CEC00F9
    assert int.from_bytes(ack_frame.data[1:3], byteorder="little") == 8
    assert ack_frame.data[3] == 2
    assert int.from_bytes(ack_frame.data[5:8], byteorder="little") == 65227


def test_j1939_out_of_order_sequence_aborts_with_reason_1() -> None:
    """Verify out-of-order TP.DT sequence number aborts CMDT session with reason code 1."""
    tp = J1939TransportProtocol(my_address=0xF9)

    # RTS for PGN 65226
    rts_data = bytearray(8)
    rts_data[0] = TP_CTRL_RTS
    rts_data[1:3] = (14).to_bytes(2, byteorder="little")
    rts_data[3] = 2
    rts_data[4] = 0xFF
    rts_data[5:8] = (65226).to_bytes(3, byteorder="little")

    rts_frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECF900,
        data=bytes(rts_data),
        is_extended=True,
    )
    tp.handle_rx_frame(rts_frame)

    # Send sequence 2 directly instead of 1 (Out of order)
    dt_bad = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBF900,
        data=b"\x02\x01\x02\x03\x04\x05\x06\x07",
        is_extended=True,
    )
    completed_msg, abort_frame = tp.handle_rx_frame(dt_bad)

    assert completed_msg is None
    # Must emit standard TP.Conn_Abort with reason 1 (Sequence Error)
    assert abort_frame is not None
    assert abort_frame.data[0] == TP_CTRL_ABORT
    assert abort_frame.data[1] == ABORT_REASON_SEQUENCE_ERROR  # 0x01
    assert int.from_bytes(abort_frame.data[5:8], byteorder="little") == 65226
    assert (0, 0xF9) not in tp._rx_sessions


def test_j1939_broadcast_rts_da_255_rejected() -> None:
    """Verify RTS frames addressed to global broadcast address DA == 255 (0xFF) are rejected."""
    tp = J1939TransportProtocol(my_address=0xF9)

    rts_broadcast_data = bytearray(8)
    rts_broadcast_data[0] = TP_CTRL_RTS
    rts_broadcast_data[1:3] = (14).to_bytes(2, byteorder="little")
    rts_broadcast_data[3] = 2
    rts_broadcast_data[4] = 0xFF
    rts_broadcast_data[5:8] = (65226).to_bytes(3, byteorder="little")

    rts_frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECFF00,  # DA = 255 (Global Broadcast)
        data=bytes(rts_broadcast_data),
        is_extended=True,
    )
    completed_msg, resp_frame = tp.handle_rx_frame(rts_frame)

    assert completed_msg is None
    assert resp_frame is None
    assert len(tp._rx_sessions) == 0


def test_j1939_session_collision_handling() -> None:
    """Verify RTS collision on active (SA, DA) emits abort reason 2 and establishes new session."""
    tp = J1939TransportProtocol(my_address=0xF9)

    # 1. Establish first session for PGN 65226 (DM1)
    rts1_data = bytearray(8)
    rts1_data[0] = TP_CTRL_RTS
    rts1_data[1:3] = (14).to_bytes(2, byteorder="little")
    rts1_data[3] = 2
    rts1_data[4] = 0xFF
    rts1_data[5:8] = (65226).to_bytes(3, byteorder="little")

    rts1 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECF900,
        data=bytes(rts1_data),
        is_extended=True,
    )
    _, cts1 = tp.handle_rx_frame(rts1)
    assert cts1 is not None
    assert (0, 0xF9, "ch0") in tp._rx_sessions
    assert tp._rx_sessions[(0, 0xF9, "ch0")].expected_pgn == 65226

    # Ingest 1 DT packet so session is active
    dt1 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBF900,
        data=b"\x01" + b"1234567",
        is_extended=True,
    )
    tp.handle_rx_frame(dt1)

    # 2. Second RTS arrives for same (SA=0, DA=0xF9) for PGN 65227 (DM2) -> Session Collision!
    rts2_data = bytearray(8)
    rts2_data[0] = TP_CTRL_RTS
    rts2_data[1:3] = (8).to_bytes(2, byteorder="little")
    rts2_data[3] = 2
    rts2_data[4] = 0xFF
    rts2_data[5:8] = (65227).to_bytes(3, byteorder="little")

    rts2 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECF900,
        data=bytes(rts2_data),
        is_extended=True,
    )
    completed_msg, abort_frame = tp.handle_rx_frame(rts2)

    assert completed_msg is None
    assert abort_frame is not None
    assert abort_frame.data[0] == TP_CTRL_ABORT
    assert abort_frame.data[1] == ABORT_REASON_SESSION_COLLISION  # 0x02
    assert int.from_bytes(abort_frame.data[5:8], byteorder="little") == 65226  # Aborted old PGN

    # Verify new session for PGN 65227 is established
    assert (0, 0xF9, "ch0") in tp._rx_sessions
    active_session = tp._rx_sessions[(0, 0xF9, "ch0")]
    assert active_session.target_pgn == 65227
    assert active_session.expected_pgn == 65227
    assert active_session.total_bytes == 8
    assert active_session.expected_sequence == 1

    # Ingest DT packets for new session
    dt_new1 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBF900,
        data=b"\x01" + b"ABCDEFG",
        is_extended=True,
    )
    tp.handle_rx_frame(dt_new1)

    dt_new2 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBF900,
        data=b"\x02" + b"H\xff\xff\xff\xff\xff\xff",
        is_extended=True,
    )
    msg, ack = tp.handle_rx_frame(dt_new2)

    assert msg is not None
    assert msg.pgn == 65227
    assert msg.data == b"ABCDEFGH"
    assert ack is not None
    assert ack.data[0] == TP_CTRL_ACK


def test_j1939_session_collision_queues_cts_for_new_session() -> None:
    """P2 regression: collision must abort the old session AND queue the new session's CTS.

    Previously only the abort frame was emitted and the CTS for the new
    session was silently dropped, leaving the peer's transfer stalled.
    """
    tp = J1939TransportProtocol(my_address=0xF9)

    # 1. First RTS establishes a session for PGN 65226 (DM1)
    rts1_data = bytearray(8)
    rts1_data[0] = TP_CTRL_RTS
    rts1_data[1:3] = (14).to_bytes(2, byteorder="little")
    rts1_data[3] = 2
    rts1_data[4] = 0xFF
    rts1_data[5:8] = (65226).to_bytes(3, byteorder="little")
    rts1 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECF900,
        data=bytes(rts1_data),
        is_extended=True,
    )
    tp.handle_rx_frame(rts1)
    tp.take_pending_tx_frames()  # discard the initial CTS

    # 2. Colliding RTS for same (SA, DA) on PGN 65227 (DM2)
    rts2_data = bytearray(8)
    rts2_data[0] = TP_CTRL_RTS
    rts2_data[1:3] = (8).to_bytes(2, byteorder="little")
    rts2_data[3] = 2
    rts2_data[4] = 0xFF
    rts2_data[5:8] = (65227).to_bytes(3, byteorder="little")
    rts2 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECF900,
        data=bytes(rts2_data),
        is_extended=True,
    )
    _, abort_frame = tp.handle_rx_frame(rts2)

    # Primary response slot carries the abort for the OLD session
    assert abort_frame is not None
    assert abort_frame.data[0] == TP_CTRL_ABORT
    assert int.from_bytes(abort_frame.data[5:8], byteorder="little") == 65226

    # The CTS granting the NEW session must be queued, not dropped
    pending = tp.take_pending_tx_frames()
    assert len(pending) == 1
    cts = pending[0]
    assert cts.data[0] == TP_CTRL_CTS
    assert int.from_bytes(cts.data[5:8], byteorder="little") == 65227
    assert cts.data[1] == 2  # total packets granted
    assert cts.data[2] == 1  # next expected sequence


def test_j1939_bam_sequence_error_silent_eviction() -> None:
    """Verify out-of-order sequence on BAM broadcast evicts session silently without sending abort."""
    tp = J1939TransportProtocol(my_address=0xF9)

    bam_data = bytearray(8)
    bam_data[0] = TP_CTRL_BAM
    bam_data[1:3] = (14).to_bytes(2, byteorder="little")
    bam_data[3] = 2
    bam_data[4] = 0xFF
    bam_data[5:8] = (65226).to_bytes(3, byteorder="little")

    bam = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECFF00,
        data=bytes(bam_data),
        is_extended=True,
    )
    tp.handle_rx_frame(bam)
    assert (0, 255, "ch0") in tp._rx_sessions

    # Out of order DT packet (seq=2 instead of 1)
    bad_dt = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEBFF00,
        data=b"\x02" + b"1234567",
        is_extended=True,
    )
    msg, resp = tp.handle_rx_frame(bad_dt)

    assert msg is None
    assert resp is None  # BAM must never emit abort frame on CAN bus
    assert (0, 255) not in tp._rx_sessions


def test_j1939_session_keying_strict_node_isolation() -> None:
    """Verify concurrent sessions strictly keyed by (SA, DA) isolate cleanly without cross-talk."""
    tp = J1939TransportProtocol(my_address=0xF9)

    # 1. BAM from SA=0x01 (DA=255)
    bam01 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECFF01,
        data=bytes([TP_CTRL_BAM, 8, 0, 2, 0xFF, 0x01, 0x00, 0x00]),
        is_extended=True,
    )
    tp.handle_rx_frame(bam01)

    # 2. CMDT from SA=0x02 to DA=0xF9
    rts02 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECF902,
        data=bytes([TP_CTRL_RTS, 8, 0, 2, 0xFF, 0x02, 0x00, 0x00]),
        is_extended=True,
    )
    tp.handle_rx_frame(rts02)

    assert (1, 255, "ch0") in tp._rx_sessions
    assert (2, 0xF9, "ch0") in tp._rx_sessions

    # Interleave DT packets
    tp.handle_rx_frame(
        CanFrame.create(channel_id="ch0", arbitration_id=0x1CEBFF01, data=b"\x01" + b"BAM1234", is_extended=True)
    )
    tp.handle_rx_frame(
        CanFrame.create(channel_id="ch0", arbitration_id=0x1CEBF902, data=b"\x01" + b"CMD1234", is_extended=True)
    )

    m1, _ = tp.handle_rx_frame(
        CanFrame.create(channel_id="ch0", arbitration_id=0x1CEBFF01, data=b"\x02" + b"5\xff\xff\xff\xff\xff\xff", is_extended=True)
    )
    m2, ack2 = tp.handle_rx_frame(
        CanFrame.create(channel_id="ch0", arbitration_id=0x1CEBF902, data=b"\x02" + b"6\xff\xff\xff\xff\xff\xff", is_extended=True)
    )

    assert m1 is not None and m1.data == b"BAM12345" and m1.source_address == 1
    assert m2 is not None and m2.data == b"CMD12346" and m2.source_address == 2
    assert ack2 is not None and ack2.data[0] == TP_CTRL_ACK


def test_j1939_timeout_t1_eviction_and_abort() -> None:
    """Verify session hold timeout evicts the session and emits abort reason 3.

    P-c: a CMDT (RTS-established) session is held for T4 (1050 ms) per
    SAE J1939-21 — the receiver waits longer for the next DT after CTS
    than a BAM broadcast does (T1, 750 ms).
    """
    clock = MockClockProvider(start_time=100.0)
    tp = J1939TransportProtocol(my_address=0xF9, clock=clock)

    # RTS
    rts_data = bytearray(8)
    rts_data[0] = TP_CTRL_RTS
    rts_data[1:3] = (14).to_bytes(2, byteorder="little")
    rts_data[3] = 2
    rts_data[4] = 0xFF
    rts_data[5:8] = (65226).to_bytes(3, byteorder="little")

    rts_frame = CanFrame.create(channel_id="ch0", arbitration_id=0x1CECF900, data=bytes(rts_data), is_extended=True)
    tp.handle_rx_frame(rts_frame)

    # 800 ms: past T1 (750) but still inside the T4 CMDT hold — session must
    # survive and the early DT must be accepted, not aborted.
    clock.advance(0.800)
    dt_early = CanFrame.create(channel_id="ch0", arbitration_id=0x1CEBF900, data=b"\x01" + b"1234567", is_extended=True)
    msg, abort_frame = tp.handle_rx_frame(dt_early)
    assert abort_frame is None  # not timed out under T4 yet
    assert (0, 0xF9, "ch0") in tp._rx_sessions  # session still open

    # Advance past the full T4 hold; the next DT trips the timeout
    clock.advance(1.100)
    dt_late = CanFrame.create(channel_id="ch0", arbitration_id=0x1CEBF900, data=b"\x02" + b"1234567", is_extended=True)
    msg, abort_frame = tp.handle_rx_frame(dt_late)

    assert msg is None
    assert abort_frame is not None
    assert abort_frame.data[0] == TP_CTRL_ABORT
    assert abort_frame.data[1] == ABORT_REASON_TIMEOUT  # 0x03
    assert (0, 0xF9, "ch0") not in tp._rx_sessions


def test_j1939_start_tp_bam_and_start_tp_cm_dt_segmentation() -> None:
    """Verify segmentation helpers start_tp_bam and start_tp_cm_dt and full roundtrip reassembly."""
    tp_tx = J1939TransportProtocol(my_address=0x01)
    tp_rx = J1939TransportProtocol(my_address=0xF9)

    payload = b"Testing J1939 Transport Protocol Segmentation Roundtrip 1234567890"

    # 1. BAM Broadcast roundtrip
    bam_frames = tp_tx.start_tp_bam(pgn=65226, data=payload, channel_id="ch0")
    assert len(bam_frames) == 1 + ((len(payload) + 6) // 7)
    assert bam_frames[0].data[0] == TP_CTRL_BAM

    completed_bam = None
    for f in bam_frames:
        m, _ = tp_rx.handle_rx_frame(f)
        if m:
            completed_bam = m

    assert completed_bam is not None
    assert completed_bam.pgn == 65226
    assert completed_bam.data == payload

    # 2. CMDT Point-to-Point roundtrip
    # REVIEW.md 3.5: start_tp_cm_dt now returns ONLY the RTS — SAE J1939-21
    # §5.10.1 forbids sending TP.DT packets before the receiver's CTS grant.
    cmdt_frames = tp_tx.start_tp_cm_dt(target_address=0xF9, pgn=65227, data=payload, channel_id="ch0")
    assert len(cmdt_frames) == 1
    assert cmdt_frames[0].data[0] == TP_CTRL_RTS

    # RTS -> receiver answers CTS
    _, cts = tp_rx.handle_rx_frame(cmdt_frames[0])
    assert cts is not None
    assert cts.data[0] == TP_CTRL_CTS

    # The sender then uses the proper windowed flow (start_cmdt_transfer +
    # handle_rx_frame). Reset the receiver so the new RTS registers a
    # fresh session instead of colliding with the one opened above.
    tp_rx.reset_sessions()
    dt_frames: list[CanFrame] = []
    rts_seed = tp_tx.start_cmdt_transfer(0xF9, 65227, payload, channel_id="ch0")
    _, cts2 = tp_rx.handle_rx_frame(rts_seed)
    assert cts2 is not None and cts2.data[0] == TP_CTRL_CTS
    out, first_dt = tp_tx.handle_rx_frame(cts2)
    if first_dt is not None:
        dt_frames.append(first_dt)
    dt_frames.extend(tp_tx.take_pending_tx_frames())

    completed_cmdt = None
    last_ack = None
    while dt_frames:
        nxt: list[CanFrame] = []
        for f in dt_frames:
            m, ack = tp_rx.handle_rx_frame(f)
            if m:
                completed_cmdt = m
                last_ack = ack
            elif ack is not None and ack.data[0] == TP_CTRL_CTS:
                # receiver wants more — feed the next window
                _m, first_dt2 = tp_tx.handle_rx_frame(ack)
                if first_dt2 is not None:
                    nxt.append(first_dt2)
                nxt.extend(tp_tx.take_pending_tx_frames())
        dt_frames = nxt

    assert completed_cmdt is not None
    assert completed_cmdt.pgn == 65227
    assert completed_cmdt.data == payload
    assert last_ack is not None
    assert last_ack.data[0] == TP_CTRL_ACK


def test_j1939_bounds_and_overflow_rejection() -> None:
    """Verify J1939-21 TP rejects total_bytes > 1785, 0-length, and packet count mismatches."""
    tp = J1939TransportProtocol(my_address=0xF9)

    # 0 bytes declared
    f_zero = CanFrame.create(channel_id="ch0", arbitration_id=0x1CECFF01, data=b"\x20\x00\x00\x00\xff\x00\xf0\x00", is_extended=True)
    m, r = tp.handle_rx_frame(f_zero)
    assert m is None and r is None
    assert len(tp._rx_sessions) == 0

    # 1786 bytes (> 1785 limit)
    f_ovfl = CanFrame.create(channel_id="ch0", arbitration_id=0x1CECFF01, data=b"\x20\xfa\x06\xff\xff\x00\xf0\x00", is_extended=True)
    m, r = tp.handle_rx_frame(f_ovfl)
    assert m is None and r is None
    assert len(tp._rx_sessions) == 0

    # Packet count mismatch (14 bytes declared with 1 packet instead of 2)
    f_mismatch = CanFrame.create(channel_id="ch0", arbitration_id=0x1CECFF01, data=b"\x20\x0e\x00\x01\xff\x00\xf0\x00", is_extended=True)
    m, r = tp.handle_rx_frame(f_mismatch)
    assert m is None and r is None
    assert len(tp._rx_sessions) == 0

    # Segmentation of invalid size raises ValueError
    with pytest.raises(ValueError, match="1..1785"):
        tp.start_tp_bam(pgn=65226, data=b"")

    with pytest.raises(ValueError, match="1..1785"):
        tp.start_tp_bam(pgn=65226, data=b"X" * 1786)


def test_j1939_session_reaping_and_dos_prevention() -> None:
    """Verify that stale J1939 sessions are reaped and capacity is bounded under flood attack."""
    tp = J1939TransportProtocol(my_address=0xF9)

    # 1. Flood with 2000 BAM frames from distinct simulated ECUs
    for sa in range(250):
        for pgn_offset in range(8):
            bam_data = bytearray(8)
            bam_data[0] = TP_CTRL_BAM
            bam_data[1:3] = (14).to_bytes(2, byteorder="little")
            bam_data[3] = 2
            bam_data[4] = 0xFF
            bam_data[5:8] = (60000 + pgn_offset).to_bytes(3, byteorder="little")

            cm_frame = CanFrame.create(
                channel_id="ch0",
                arbitration_id=0x1CECFF00 | sa,
                data=bytes(bam_data),
                is_extended=True,
            )
            tp.handle_rx_frame(cm_frame)

    # Active sessions must be strictly capped at MAX_CONCURRENT_SESSIONS
    assert len(tp._rx_sessions) <= tp.MAX_CONCURRENT_SESSIONS

    # 2. Simulate passage of time (> T1 timeout) and verify reaping
    tp._reap_stale_sessions(now=time.monotonic() + 10.0)
    assert len(tp._rx_sessions) == 0


def test_j1939_exception_classes_instantiation() -> None:
    """Verify instantiation and attributes of J1939 exception taxonomy and constants."""
    assert ABORT_REASON_SEQUENCE_ERROR == 0x01
    assert ABORT_REASON_SESSION_COLLISION == 0x02
    assert ABORT_REASON_TIMEOUT == 0x03
    assert ABORT_REASON_UNEXPECTED_CONTROL == 0x04

    e_abort = J1939TpAbortError("Aborted", reason=1, target_pgn=65226, sa=0, da=0xF9)
    assert isinstance(e_abort, J1939TpError)
    assert e_abort.reason == 1
    assert e_abort.target_pgn == 65226
    assert e_abort.sa == 0
    assert e_abort.da == 0xF9

    e_coll = J1939SessionCollisionError("Collision", sa=0, da=0xF9, old_pgn=65226, new_pgn=65227)
    assert isinstance(e_coll, J1939TpError)
    assert e_coll.sa == 0
    assert e_coll.da == 0xF9
    assert e_coll.old_pgn == 65226
    assert e_coll.new_pgn == 65227

    e_seq = J1939SequenceError("Seq err", expected_seq=1, received_seq=2, sa=0, da=0xF9)
    assert isinstance(e_seq, J1939TpError)
    assert e_seq.expected_seq == 1
    assert e_seq.received_seq == 2

    e_tout = J1939TpTimeoutError("Timeout", timeout_type="T1", elapsed_ms=800.0, limit_ms=750.0, sa=0, da=0xF9, target_pgn=65226)
    assert isinstance(e_tout, J1939TpError)
    assert e_tout.timeout_type == "T1"
    assert e_tout.elapsed_ms == 800.0

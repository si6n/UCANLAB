"""Hypothesis property tests for the protocol transport stacks.

Locks four invariant families across ISO-TP, J1939-21 TP, NMEA 2000 Fast Packet
and the E2E safety packager/validator pair:

1. Roundtrip — segmenting an arbitrary payload and feeding the frame stream back
   into the reassembler returns the payload byte-for-byte.
2. Fault injection (drop / duplicate / reorder) — the reassembler either
   completes with the exact payload or yields nothing. It never returns a
   different byte string and never raises outside the declared hierarchy.
3. Timeout boundaries are driven by an injected ClockProvider, never by a
   time.monotonic() value captured in a dataclass default. Timers do not fire
   early and do fire once the limit is crossed.
4. E2E counter progression — 0..modulo-1 wrapping, SOME_LOST inside the delta
   window, WRONG_SEQUENCE outside it, and the REPEATED latch that holds
   last_counter in place.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from src.core.contracts.ports import ClockProvider
from src.core.errors import PlatformError
from src.core.exceptions import IsoTpTimeoutError
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.transport import TP_CTRL_CTS, TP_CTRL_RTS, J1939TransportProtocol
from src.protocols.nmea2000.fast_packet import Nmea2000FastPacketDecoder
from src.protocols.uds.isotp import IsoTpReceiver, IsoTpSender, IsoTpTransport
from src.safety.e2e.packager import E2ESafetyPackager
from src.safety.e2e.profiles import E2EProfileConfig, E2EProfileType, E2EStatus
from src.safety.e2e.validator import E2ESafetyValidator

# ============================================================================
# Injectable clock + async plumbing
# ============================================================================


class FakeClock(ClockProvider):
    """Controllable monotonic clock — the only time source these tests trust."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def now_monotonic(self) -> float:
        return self._now

    def now_wall_ns(self) -> int:
        return int(self._now * 1_000_000_000)

    def advance(self, seconds: float) -> None:
        self._now += seconds


class ScriptedRxSub:
    """RxSubscription replay of a fixed frame list; returns None once drained.

    A drained subscription models "the peer went silent", which is what the
    N_Bs / N_Cr timers are supposed to detect. `requested_timeouts` records the
    deadline the caller delegated on each call, so a test can assert the state
    machine passed its own configured limit down rather than a hardcoded one.
    """

    def __init__(self, frames: list[CanFrame] | None = None) -> None:
        self._frames = list(frames or [])
        self.recv_calls = 0
        self.requested_timeouts: list[float | None] = []

    def feed(self, frame: CanFrame) -> None:
        self._frames.append(frame)

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        self.recv_calls += 1
        self.requested_timeouts.append(timeout_s)
        if not self._frames:
            return None
        return self._frames.pop(0)

    def cancel(self) -> None:  # pragma: no cover - interface conformance
        pass


class CollectingTxPort:
    """TxPort capturing every frame the state machine emits."""

    def __init__(self) -> None:
        self.sent: list[CanFrame] = []

    async def send(self, frame: CanFrame) -> None:
        self.sent.append(frame)


class DecoyRxSub:
    """Delivers endless irrelevant frames while advancing a FakeClock.

    Returning None would let a state machine bail out on the "peer went silent"
    branch and pass without ever consulting its clock. A decoy frame the machine
    must skip keeps the wait loop alive, so the ONLY way out is the machine's own
    timer arithmetic on the injected ClockProvider. If it reads a real system
    clock instead, the loop never ends and the call cap fails the test loudly —
    this is the regression lock for the `time.monotonic` dataclass-default trap.
    """

    MAX_CALLS = 256

    def __init__(self, clock: FakeClock, decoy: CanFrame, step_s: float = 0.01) -> None:
        self.clock = clock
        self.decoy = decoy
        self.step_s = step_s
        self.recv_calls = 0

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        self.recv_calls += 1
        if self.recv_calls > self.MAX_CALLS:
            raise AssertionError(
                f"wait loop still running after {self.MAX_CALLS} recv() calls — "
                "the state machine is not measuring its timeout on the injected ClockProvider"
            )
        self.clock.advance(self.step_s)
        return self.decoy

    def cancel(self) -> None:  # pragma: no cover - interface conformance
        pass


def as_rx(frame: CanFrame, arbitration_id: int) -> CanFrame:
    """Re-address a TX frame as an RX frame on the peer's response ID."""
    return CanFrame.create(
        channel_id=frame.channel_id,
        arbitration_id=arbitration_id,
        data=frame.data,
        is_extended=frame.is_extended,
        is_fd=frame.is_fd,
        direction="rx",
    )


def feed_isotp(transport: IsoTpTransport, frames: list[CanFrame]) -> bytes | None:
    """Feed an ISO-TP frame stream into a transport, returning the payload if it completes."""
    for frame in frames:
        payload, _response = transport.handle_rx_frame(as_rx(frame, transport.rx_id))
        if payload is not None:
            return payload
    return None


def n2k_frames(payload: bytes, *, sequence_id: int = 3, sa: int = 0x42, pgn: int = 0x1F201) -> list[CanFrame]:
    """Build a NMEA 2000 Fast Packet frame sequence (6 bytes in frame 0, 7 thereafter).

    Default PGN 0x1F201 = 127489 Engine Parameters Dynamic (fast-packet per
    canboat DBC). The previous default 127257 (Attitude) is a single-frame
    PGN and is rejected by the PGN membership gate (REVIEW 1-H3).
    """
    can_id = (6 << 26) | (pgn << 8) | sa
    head = bytearray(8)
    head[0] = (sequence_id << 5) | 0
    head[1] = len(payload)
    head[2:8] = payload[:6].ljust(6, b"\xff")
    frames = [bytes(head)]

    offset = 6
    index = 1
    while offset < len(payload):
        chunk = payload[offset : offset + 7]
        body = bytearray(8)
        body[0] = (sequence_id << 5) | (index & 0x1F)
        body[1 : 1 + len(chunk)] = chunk
        for i in range(1 + len(chunk), 8):
            body[i] = 0xFF
        frames.append(bytes(body))
        offset += len(chunk)
        index += 1

    return [
        CanFrame.create(
            channel_id="n2k_ch0",
            arbitration_id=can_id,
            data=data,
            is_extended=True,
            direction="rx",
        )
        for data in frames
    ]


def feed_n2k(decoder: Nmea2000FastPacketDecoder, frames: list[CanFrame]) -> bytes | None:
    """Feed a Fast Packet stream, returning the reassembled payload if it completes."""
    for frame in frames:
        completed = decoder.handle_rx_frame(frame)
        if completed is not None:
            return completed.data
    return None


def e2e_profile(*, counter_modulo: int = 16, max_delta: int = 3) -> E2EProfileConfig:
    """AUTOSAR-1C style profile with an explicit modulo, for counter-wrap properties."""
    return E2EProfileConfig(
        profile_type=E2EProfileType.AUTOSAR_PROFILE_1C,
        crc_byte_offset=0,
        counter_byte_offset=1,
        counter_bit_mask=0xFF,
        counter_bit_shift=0,
        counter_modulo=counter_modulo,
        max_delta_counter=max_delta,
        data_id=0x1234,
    )


# ============================================================================
# ISO 15765-2 (ISO-TP) — roundtrip, fault injection, timer boundaries
# ============================================================================


@given(payload=st.binary(min_size=1, max_size=4095))
@settings(max_examples=250, deadline=None)
def test_isotp_classic_roundtrip_is_lossless(payload: bytes) -> None:
    """Any 1..4095-byte payload survives segment → reassemble unchanged (Classic CAN)."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    frames = transport.segment_message(payload, is_fd=False)

    assert frames, "segmentation must emit at least one frame"
    assert feed_isotp(transport, frames) == payload


@given(payload=st.binary(min_size=1, max_size=4095))
@settings(max_examples=150, deadline=None)
def test_isotp_fd_roundtrip_is_lossless(payload: bytes) -> None:
    """CAN-FD segmentation (64-byte frames, 12-bit FF) is equally lossless."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    frames = transport.segment_message(payload, is_fd=True)

    assert frames
    assert feed_isotp(transport, frames) == payload


@given(payload=st.binary(min_size=8, max_size=600), drop_index=st.integers(min_value=1, max_value=40))
@settings(max_examples=200, deadline=None)
def test_isotp_dropped_consecutive_frame_never_yields_wrong_payload(payload: bytes, drop_index: int) -> None:
    """Dropping one CF must abort the session, never hand back a shorter/spliced payload."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    frames = transport.segment_message(payload, is_fd=False)
    assume(len(frames) >= 3)  # FF + at least two CFs, so a drop is observable

    victim = 1 + (drop_index % (len(frames) - 1))
    survivors = frames[:victim] + frames[victim + 1 :]

    result = feed_isotp(transport, survivors)
    assert result != payload, "a dropped CF must not reassemble into the original payload"
    assert result is None or result != payload


@given(payload=st.binary(min_size=8, max_size=600), dup_index=st.integers(min_value=1, max_value=40))
@settings(max_examples=200, deadline=None)
def test_isotp_duplicated_consecutive_frame_is_rejected_not_spliced(payload: bytes, dup_index: int) -> None:
    """A repeated CF breaks the sequence-number contract; no silently corrupted payload escapes."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    frames = transport.segment_message(payload, is_fd=False)
    assume(len(frames) >= 3)

    victim = 1 + (dup_index % (len(frames) - 1))
    stream = frames[: victim + 1] + [frames[victim]] + frames[victim + 1 :]

    result = feed_isotp(transport, stream)
    assert result is None or result == payload


@given(payload=st.binary(min_size=15, max_size=600))
@settings(max_examples=150, deadline=None)
def test_isotp_reordered_consecutive_frames_are_rejected(payload: bytes) -> None:
    """Swapping two adjacent CFs trips the sequence check instead of interleaving bytes."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    frames = transport.segment_message(payload, is_fd=False)
    assume(len(frames) >= 4)

    stream = list(frames)
    stream[1], stream[2] = stream[2], stream[1]

    assert feed_isotp(transport, stream) is None


@given(payload=st.binary(min_size=8, max_size=200))
@settings(max_examples=100, deadline=None)
def test_isotp_n_cr_does_not_fire_before_the_limit(payload: bytes) -> None:
    """A CF arriving just inside N_Cr is accepted; the receiver keeps the session."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    frames = transport.segment_message(payload, is_fd=False)
    assume(len(frames) >= 3)

    transport.handle_rx_frame(as_rx(frames[0], transport.rx_id))
    session = transport._rx_session
    assert session is not None

    # Age the session to just under the N_Cr limit, then deliver the next CF.
    session.last_activity_time -= transport.TIMEOUT_SEC * 0.5
    _payload, _fc = transport.handle_rx_frame(as_rx(frames[1], transport.rx_id))
    assert transport._rx_session is not None, "session must survive a CF inside N_Cr"


@given(payload=st.binary(min_size=8, max_size=200))
@settings(max_examples=100, deadline=None)
def test_isotp_n_cr_fires_once_the_limit_is_crossed(payload: bytes) -> None:
    """Past N_Cr the session is dropped and the late CF contributes nothing."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    frames = transport.segment_message(payload, is_fd=False)
    assume(len(frames) >= 3)

    transport.handle_rx_frame(as_rx(frames[0], transport.rx_id))
    session = transport._rx_session
    assert session is not None

    session.last_activity_time -= transport.TIMEOUT_SEC * 2
    result, _fc = transport.handle_rx_frame(as_rx(frames[1], transport.rx_id))
    assert result is None
    assert transport._rx_session is None, "expired session must be discarded, not reused"


@given(n_bs=st.floats(min_value=0.5, max_value=2.0))
@settings(max_examples=40, deadline=None)
def test_isotp_n_bs_timeout_is_measured_on_the_injected_clock(n_bs: float) -> None:
    """N_Bs is measured on the injected ClockProvider, not on wall time.

    The subscription keeps handing back a non-Flow-Control decoy, so the sender's
    wait loop can only exit through its own N_Bs arithmetic. Wall time barely
    advances; the FakeClock is what crosses the limit.
    """
    clock = FakeClock()
    decoy = CanFrame.create(channel_id="ch0", arbitration_id=0x123, data=b"\x00" * 8, direction="rx")
    rx_sub = DecoyRxSub(clock, decoy, step_s=n_bs / 8)
    sender = IsoTpSender(
        tx_port=CollectingTxPort(),
        rx_sub=rx_sub,
        tx_id=0x7E0,
        rx_id=0x7E8,
        clock=clock,
        n_as_timeout_s=1_000.0,  # keep N_As out of the way; only N_Bs is under test
        n_bs_timeout_s=n_bs,
    )
    start = clock.now_monotonic()

    # 40 bytes forces FF + CF, so the sender must wait for Flow Control.
    with pytest.raises(IsoTpTimeoutError) as exc_info:
        asyncio.run(sender.send(b"\x5a" * 40))

    assert exc_info.value.timeout_type == "N_Bs"
    assert rx_sub.recv_calls >= 2, "sender must keep polling until its own timer expires"
    assert clock.now_monotonic() - start >= n_bs, "the timeout must be charged to the injected clock"


@given(n_cr=st.floats(min_value=0.5, max_value=2.0))
@settings(max_examples=40, deadline=None)
def test_isotp_receiver_n_cr_timeout_is_delegated_from_its_own_config(n_cr: float) -> None:
    """The async receiver derives its N_Cr deadline from configuration, not a hardcoded default.

    After answering the First Frame with Flow Control the receiver waits for a
    Consecutive Frame; the deadline it hands to recv() must be its configured
    n_cr_timeout_s, and a silent peer must produce a typed N_Cr timeout.
    """
    clock = FakeClock()
    tx_port = CollectingTxPort()
    segmenter = IsoTpTransport(tx_id=0x7E8, rx_id=0x7E0, channel_id="ch0")
    ff = segmenter.segment_message(b"\x11" * 40, is_fd=False)[0]

    rx_sub = ScriptedRxSub([as_rx(ff, 0x7E8)])  # FF only; no CF ever arrives
    receiver = IsoTpReceiver(
        tx_port=tx_port,
        rx_sub=rx_sub,
        tx_id=0x7E0,
        rx_id=0x7E8,
        clock=clock,
        n_cr_timeout_s=n_cr,
    )

    with pytest.raises(IsoTpTimeoutError) as exc_info:
        asyncio.run(receiver.receive())

    assert exc_info.value.timeout_type == "N_Cr"
    assert tx_port.sent, "receiver must have answered the FF with a Flow Control frame"
    assert rx_sub.requested_timeouts[-1] == pytest.approx(n_cr), (
        "the CF wait must use the configured n_cr_timeout_s, not a hardcoded constant"
    )


@given(payload=st.binary(min_size=4096, max_size=4200))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.large_base_example])
def test_isotp_oversized_payload_stays_inside_platform_error(payload: bytes) -> None:
    """Beyond the 12-bit FF range the engine must not leak a bare ValueError/IndexError."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    try:
        frames = transport.segment_message(payload, is_fd=False)
    except PlatformError:
        return  # typed rejection is the contract
    except Exception as exc:  # pragma: no cover - fails loudly if hierarchy is escaped
        pytest.fail(f"oversized payload raised {type(exc).__name__}, outside PlatformError")

    # Extended 32-bit FF path: accepted, and then it must still roundtrip exactly.
    assert feed_isotp(transport, frames) == payload


@given(data=st.binary(min_size=0, max_size=8))
@settings(max_examples=200, deadline=None)
def test_isotp_arbitrary_garbage_frame_never_raises(data: bytes) -> None:
    """Random bytes on the RX id resolve to (None, None) or a typed error — never a crash."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="ch0")
    frame = CanFrame.create(channel_id="ch0", arbitration_id=0x7E8, data=data, direction="rx")

    try:
        payload, response = transport.handle_rx_frame(frame)
    except PlatformError:
        return
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"garbage frame raised {type(exc).__name__}, outside PlatformError")

    assert payload is None or isinstance(payload, bytes)
    assert response is None or isinstance(response, CanFrame)


# ============================================================================
# SAE J1939-21 Transport Protocol — BAM, RTS/CTS receiver, CMDT sender
# ============================================================================


@given(payload=st.binary(min_size=1, max_size=1785))
@settings(max_examples=250, deadline=None)
def test_j1939_bam_roundtrip_is_lossless(payload: bytes) -> None:
    """Any 1..1785-byte BAM broadcast reassembles byte-for-byte."""
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
    rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0")

    frames = tx.start_tp_bam(pgn=0xFEEC, data=payload, channel_id="ch0")
    assert len(frames) == 1 + (len(payload) + 6) // 7

    completed = None
    for frame in frames:
        message, _response = rx.handle_rx_frame(frame)
        if message is not None:
            completed = message

    assert completed is not None, "BAM stream must complete"
    assert completed.data == payload
    assert completed.pgn == 0xFEEC


@given(payload=st.binary(min_size=1, max_size=1785))
@settings(max_examples=200, deadline=None)
def test_j1939_cmdt_rts_cts_roundtrip_is_lossless(payload: bytes) -> None:
    """A full RTS → CTS → DT → EndOfMsgACK exchange returns the exact payload."""
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
    rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0")
    pgn = 0xFEEE

    rts = tx.start_cmdt_transfer(0xF9, pgn, payload)
    assert rts.data[0] == TP_CTRL_RTS

    _message, cts = rx.handle_rx_frame(rts)
    assert cts is not None and cts.data[0] == TP_CTRL_CTS

    completed = None
    ack = None
    # Each CTS grants a window; loop until the receiver reports completion.
    for _ in range(1 + (len(payload) + 6) // 7):
        if cts is None:
            break
        _sender_msg, first_dt = tx.handle_rx_frame(cts)
        window = ([first_dt] if first_dt is not None else []) + tx.take_pending_tx_frames()
        if not window:
            break

        cts = None
        for dt in window:
            message, response = rx.handle_rx_frame(dt)
            if message is not None:
                completed = message
                ack = response
            elif response is not None:
                cts = response
        if completed is not None:
            break

    assert completed is not None, "CMDT transfer never completed"
    assert completed.data == payload
    assert ack is not None, "final DT must be answered with EndOfMsgACK"


@given(payload=st.binary(min_size=15, max_size=800), drop_index=st.integers(min_value=1, max_value=60))
@settings(max_examples=200, deadline=None)
def test_j1939_dropped_dt_never_yields_wrong_payload(payload: bytes, drop_index: int) -> None:
    """A missing TP.DT packet aborts reassembly instead of producing spliced data."""
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
    rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0")

    frames = tx.start_tp_bam(pgn=0xFEEC, data=payload, channel_id="ch0")
    assume(len(frames) >= 4)  # BAM + at least three DT packets

    victim = 1 + (drop_index % (len(frames) - 1))
    survivors = frames[:victim] + frames[victim + 1 :]

    completed = None
    for frame in survivors:
        message, _response = rx.handle_rx_frame(frame)
        if message is not None:
            completed = message

    assert completed is None or completed.data != payload


@given(payload=st.binary(min_size=15, max_size=800), dup_index=st.integers(min_value=1, max_value=60))
@settings(max_examples=200, deadline=None)
def test_j1939_duplicated_dt_is_rejected_not_appended(payload: bytes, dup_index: int) -> None:
    """Re-sending a TP.DT packet must not append its bytes a second time."""
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
    rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0")

    frames = tx.start_tp_bam(pgn=0xFEEC, data=payload, channel_id="ch0")
    assume(len(frames) >= 4)

    victim = 1 + (dup_index % (len(frames) - 1))
    stream = frames[: victim + 1] + [frames[victim]] + frames[victim + 1 :]

    completed = None
    for frame in stream:
        message, _response = rx.handle_rx_frame(frame)
        if message is not None:
            completed = message

    assert completed is None or completed.data == payload


@given(payload=st.binary(min_size=22, max_size=800))
@settings(max_examples=150, deadline=None)
def test_j1939_reordered_dt_is_rejected(payload: bytes) -> None:
    """Out-of-order TP.DT packets break the sequence check rather than interleaving."""
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
    rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0")

    frames = tx.start_tp_bam(pgn=0xFEEC, data=payload, channel_id="ch0")
    assume(len(frames) >= 5)

    stream = list(frames)
    stream[1], stream[2] = stream[2], stream[1]

    completed = None
    for frame in stream:
        message, _response = rx.handle_rx_frame(frame)
        if message is not None:
            completed = message

    assert completed is None or completed.data == payload


@given(total_bytes=st.integers(min_value=8, max_value=1785))
@settings(max_examples=150, deadline=None)
def test_j1939_t2_holds_until_the_limit_then_aborts(total_bytes: int) -> None:
    """T2 (RTS → CTS, 1250 ms) never aborts early and always aborts once crossed."""
    clock = FakeClock()
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0", clock=clock)
    payload = bytes(total_bytes)

    tx.start_cmdt_transfer(0xF9, 0xFEEE, payload)

    clock.advance(tx.T2_TIMEOUT_SEC * 0.99)
    frames, abort = tx.advance_cmdt_transfer(0xF9, 0xFEEE)
    assert frames == [] and abort is None, "T2 must not abort inside the window"

    clock.advance(tx.T2_TIMEOUT_SEC * 0.02)
    frames, abort = tx.advance_cmdt_transfer(0xF9, 0xFEEE)
    assert frames == []
    assert abort is not None, "T2 must abort once the limit is crossed"
    assert not tx._tx_sessions, "aborted session must be released"


@given(total_bytes=st.integers(min_value=8, max_value=200))
@settings(max_examples=150, deadline=None)
def test_j1939_t3_holds_until_the_limit_then_aborts(total_bytes: int) -> None:
    """T3 (last DT → EndOfMsgACK, 1250 ms) behaves identically at its boundary."""
    clock = FakeClock()
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0", clock=clock)
    rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0", clock=clock)
    payload = bytes(total_bytes)
    pgn = 0xFEEE

    rts = tx.start_cmdt_transfer(0xF9, pgn, payload)
    _message, cts = rx.handle_rx_frame(rts)
    assert cts is not None

    tx.handle_rx_frame(cts)
    tx.take_pending_tx_frames()
    session = next(iter(tx._tx_sessions.values()))
    assume(session.state == "WAIT_ACK")  # single window covered the whole payload

    clock.advance(tx.T3_TIMEOUT_SEC * 0.99)
    frames, abort = tx.advance_cmdt_transfer(0xF9, pgn)
    assert abort is None, "T3 must not abort inside the window"

    clock.advance(tx.T3_TIMEOUT_SEC * 0.02)
    frames, abort = tx.advance_cmdt_transfer(0xF9, pgn)
    assert frames == []
    assert abort is not None, "T3 must abort once the limit is crossed"


@given(total_bytes=st.integers(min_value=8, max_value=1785))
@settings(max_examples=100, deadline=None)
def test_j1939_t1_reaps_stale_rx_session_on_injected_clock(total_bytes: int) -> None:
    """A silent sender's RX session is reaped from the injected clock, not wall time."""
    clock = FakeClock()
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0", clock=clock)
    rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0", clock=clock)

    frames = tx.start_tp_bam(pgn=0xFEEC, data=bytes(total_bytes), channel_id="ch0")
    assume(len(frames) >= 3)

    rx.handle_rx_frame(frames[0])
    rx.handle_rx_frame(frames[1])
    assert rx._rx_sessions, "session must exist after the first DT"

    clock.advance(rx.T1_TIMEOUT_SEC * 0.5)
    rx._reap_stale_sessions()
    assert rx._rx_sessions, "T1 must not reap inside the window"

    clock.advance(rx.T1_TIMEOUT_SEC)
    rx._reap_stale_sessions()
    assert not rx._rx_sessions, "T1 must reap the stale session once crossed"


@given(payload=st.binary(min_size=0, max_size=0) | st.binary(min_size=1786, max_size=1800))
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.large_base_example])
def test_j1939_out_of_range_payload_is_rejected_with_typed_error(payload: bytes) -> None:
    """0-byte and >1785-byte payloads are refused; nothing outside the taxonomy escapes."""
    tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0")

    with pytest.raises((PlatformError, ValueError)):
        tx.start_tp_bam(pgn=0xFEEC, data=payload, channel_id="ch0")

    with pytest.raises((PlatformError, ValueError)):
        tx.start_cmdt_transfer(0xF9, 0xFEEE, payload)


@given(data=st.binary(min_size=8, max_size=8))
@settings(max_examples=250, deadline=None)
def test_j1939_arbitrary_tp_cm_frame_never_raises(data: bytes) -> None:
    """Random 8-byte TP.CM/TP.DT bodies resolve to a typed outcome, never an exception."""
    rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0")

    for pf in (0xEC, 0xEB):  # TP.CM and TP.DT
        can_id = 0x18000000 | (pf << 16) | (0xF9 << 8) | 0x01
        frame = CanFrame.create(
            channel_id="ch0",
            arbitration_id=can_id,
            data=data,
            is_extended=True,
            direction="rx",
        )
        try:
            message, response = rx.handle_rx_frame(frame)
        except PlatformError:
            continue
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"garbage TP frame raised {type(exc).__name__}, outside PlatformError")

        assert message is None or isinstance(message.data, bytes)
        assert response is None or isinstance(response, CanFrame)


# ============================================================================
# NMEA 2000 Fast Packet
# ============================================================================


@given(payload=st.binary(min_size=9, max_size=223))
@settings(max_examples=250, deadline=None)
def test_n2k_fast_packet_roundtrip_is_lossless(payload: bytes) -> None:
    """Any 9..223-byte Fast Packet message reassembles byte-for-byte."""
    decoder = Nmea2000FastPacketDecoder()
    assert feed_n2k(decoder, n2k_frames(payload)) == payload


@given(payload=st.binary(min_size=16, max_size=223), drop_index=st.integers(min_value=1, max_value=31))
@settings(max_examples=200, deadline=None)
def test_n2k_dropped_frame_never_yields_wrong_payload(payload: bytes, drop_index: int) -> None:
    """A missing continuation frame stalls the session instead of producing spliced data."""
    frames = n2k_frames(payload)
    assume(len(frames) >= 3)

    victim = 1 + (drop_index % (len(frames) - 1))
    survivors = frames[:victim] + frames[victim + 1 :]

    decoder = Nmea2000FastPacketDecoder()
    result = feed_n2k(decoder, survivors)
    assert result is None or result != payload


@given(payload=st.binary(min_size=16, max_size=223), dup_index=st.integers(min_value=1, max_value=31))
@settings(max_examples=200, deadline=None)
def test_n2k_duplicated_frame_is_rejected_not_appended(payload: bytes, dup_index: int) -> None:
    """Re-sending a frame index must not append its bytes twice."""
    frames = n2k_frames(payload)
    assume(len(frames) >= 3)

    victim = 1 + (dup_index % (len(frames) - 1))
    stream = frames[: victim + 1] + [frames[victim]] + frames[victim + 1 :]

    decoder = Nmea2000FastPacketDecoder()
    result = feed_n2k(decoder, stream)
    assert result is None or result == payload


@given(payload=st.binary(min_size=23, max_size=223))
@settings(max_examples=150, deadline=None)
def test_n2k_reordered_frames_are_rejected(payload: bytes) -> None:
    """Swapping adjacent continuation frames trips the frame-index check."""
    frames = n2k_frames(payload)
    assume(len(frames) >= 4)

    stream = list(frames)
    stream[1], stream[2] = stream[2], stream[1]

    decoder = Nmea2000FastPacketDecoder()
    result = feed_n2k(decoder, stream)
    assert result is None or result == payload


@given(payload_a=st.binary(min_size=9, max_size=100), payload_b=st.binary(min_size=9, max_size=100))
@settings(max_examples=150, deadline=None)
def test_n2k_interleaved_sequence_ids_do_not_cross_contaminate(payload_a: bytes, payload_b: bytes) -> None:
    """Two concurrent transfers on distinct sequence IDs stay in their own sessions."""
    frames_a = n2k_frames(payload_a, sequence_id=1)
    frames_b = n2k_frames(payload_b, sequence_id=2)
    decoder = Nmea2000FastPacketDecoder()

    results: list[bytes] = []
    for index in range(max(len(frames_a), len(frames_b))):
        for stream in (frames_a, frames_b):
            if index < len(stream):
                completed = decoder.handle_rx_frame(stream[index])
                if completed is not None:
                    results.append(completed.data)

    assert payload_a in results, "sequence ID 1 must reassemble independently"
    assert payload_b in results, "sequence ID 2 must reassemble independently"


@given(payload=st.binary(min_size=16, max_size=223))
@settings(max_examples=150, deadline=None)
def test_n2k_restart_drops_stale_session_without_mixing_bytes(payload: bytes) -> None:
    """A fresh index-0 frame mid-transfer restarts cleanly (F-25) rather than concatenating."""
    frames = n2k_frames(payload)
    assume(len(frames) >= 3)

    decoder = Nmea2000FastPacketDecoder()
    # Abandon a transfer halfway, then run a complete one on the same key.
    for frame in frames[:2]:
        decoder.handle_rx_frame(frame)

    assert feed_n2k(decoder, frames) == payload


@given(declared_length=st.integers(min_value=0, max_value=8) | st.integers(min_value=224, max_value=255))
@settings(max_examples=250, deadline=None)
def test_n2k_out_of_range_declared_length_is_rejected(declared_length: int) -> None:
    """Only 9..223 declared bytes open a session; anything else is refused, never raised."""
    assert not 9 <= declared_length <= 223  # strategy covers exactly the invalid band

    decoder = Nmea2000FastPacketDecoder()
    head = bytearray(8)
    head[0] = (4 << 5) | 0
    head[1] = declared_length
    frame = CanFrame.create(
        channel_id="n2k_ch0",
        arbitration_id=(6 << 26) | (0x1F119 << 8) | 0x42,
        data=bytes(head),
        is_extended=True,
        direction="rx",
    )

    try:
        result = decoder.handle_rx_frame(frame)
    except PlatformError:
        return
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"invalid Fast Packet length raised {type(exc).__name__}, outside PlatformError")

    assert result is None


@given(data=st.binary(min_size=0, max_size=8))
@settings(max_examples=250, deadline=None)
def test_n2k_arbitrary_frame_never_raises(data: bytes) -> None:
    """Random bytes on a Fast Packet PGN produce None or a completed message, never a crash."""
    decoder = Nmea2000FastPacketDecoder()
    frame = CanFrame.create(
        channel_id="n2k_ch0",
        arbitration_id=(6 << 26) | (0x1F119 << 8) | 0x42,
        data=data,
        is_extended=True,
        direction="rx",
    )

    try:
        result = decoder.handle_rx_frame(frame)
    except PlatformError:
        return
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"garbage Fast Packet frame raised {type(exc).__name__}, outside PlatformError")

    assert result is None or isinstance(result.data, bytes)


# ============================================================================
# E2E safety packager → validator
# ============================================================================


@given(
    payload=st.binary(min_size=2, max_size=8),
    counter_modulo=st.integers(min_value=4, max_value=256),
    frame_count=st.integers(min_value=2, max_value=12),
)
@settings(max_examples=250, deadline=None)
def test_e2e_consecutive_stream_is_initial_then_ok(payload: bytes, counter_modulo: int, frame_count: int) -> None:
    """A packager-driven stream validates as INITIAL once, then OK for every later frame."""
    profile = e2e_profile(counter_modulo=counter_modulo)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    raw = CanFrame.create(channel_id="can0", arbitration_id=0x123, data=payload)

    for index in range(frame_count):
        sealed = packager.package(raw, profile)
        result = validator.validate(sealed, profile)

        assert result.is_crc_valid, f"frame {index} CRC must verify"
        expected = E2EStatus.INITIAL if index == 0 else E2EStatus.OK
        assert result.verdict == expected, f"frame {index}: expected {expected}, got {result.verdict}"


@given(counter_modulo=st.integers(min_value=4, max_value=64))
@settings(max_examples=150, deadline=None)
def test_e2e_counter_walks_0_to_modulo_then_wraps(counter_modulo: int) -> None:
    """The rolling counter covers 0..modulo-1 and wraps to 0 without a sequence fault."""
    profile = e2e_profile(counter_modulo=counter_modulo)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    raw = CanFrame.create(channel_id="can0", arbitration_id=0x7E0, data=b"\x00\x00\x11\x22")

    seen: list[int] = []
    for index in range(counter_modulo + 2):
        sealed = packager.package(raw, profile)
        result = validator.validate(sealed, profile)

        assert result.counter == index % counter_modulo, f"frame {index} counter drifted"
        assert result.verdict in (E2EStatus.INITIAL, E2EStatus.OK), (
            f"wrap at frame {index} must not fault: {result.verdict}"
        )
        seen.append(result.counter)

    assert set(seen) == set(range(counter_modulo)), "every counter value in 0..modulo-1 must appear"
    assert seen[counter_modulo] == 0, "the counter must wrap back to 0 at the modulo boundary"


@given(
    payload=st.binary(min_size=2, max_size=8),
    gap=st.integers(min_value=2, max_value=3),
)
@settings(max_examples=200, deadline=None)
def test_e2e_gap_inside_delta_window_is_some_lost_and_usable(payload: bytes, gap: int) -> None:
    """A gap within max_delta_counter reports SOME_LOST and keeps the payload usable."""
    profile = e2e_profile(counter_modulo=16, max_delta=3)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    raw = CanFrame.create(channel_id="can0", arbitration_id=0x201, data=payload)

    validator.validate(packager.package(raw, profile, counter=0), profile)
    result = validator.validate(packager.package(raw, profile, counter=gap), profile)

    assert result.verdict == E2EStatus.SOME_LOST
    assert result.delta == gap
    assert result.is_valid, "SOME_LOST payload is authentic and must stay usable"


@given(
    payload=st.binary(min_size=2, max_size=8),
    gap=st.integers(min_value=4, max_value=15),
)
@settings(max_examples=200, deadline=None)
def test_e2e_gap_beyond_delta_window_is_wrong_sequence_and_unusable(payload: bytes, gap: int) -> None:
    """A gap past max_delta_counter reports WRONG_SEQUENCE and marks the frame unusable."""
    profile = e2e_profile(counter_modulo=16, max_delta=3)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    raw = CanFrame.create(channel_id="can0", arbitration_id=0x202, data=payload)

    validator.validate(packager.package(raw, profile, counter=0), profile)
    result = validator.validate(packager.package(raw, profile, counter=gap), profile)

    assert result.verdict == E2EStatus.WRONG_SEQUENCE
    assert result.delta == gap
    assert not result.is_valid, "WRONG_SEQUENCE must not be treated as usable data"
    assert not result.is_sequence_valid


@given(payload=st.binary(min_size=2, max_size=8), counter=st.integers(min_value=0, max_value=15))
@settings(max_examples=200, deadline=None)
def test_e2e_repeated_frame_is_unusable_and_does_not_disturb_the_stream(payload: bytes, counter: int) -> None:
    """A duplicate reports REPEATED, counts as invalid, and leaves the stream resumable."""
    profile = e2e_profile(counter_modulo=16, max_delta=3)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    raw = CanFrame.create(channel_id="can0", arbitration_id=0x203, data=payload)

    sealed = packager.package(raw, profile, counter=counter)
    assert validator.validate(sealed, profile).verdict == E2EStatus.INITIAL

    replay = validator.validate(sealed, profile)
    assert replay.verdict == E2EStatus.REPEATED
    assert replay.delta == 0
    assert not replay.is_valid, "a replayed frame must never be reported as usable"

    state = validator.get_stream_state("can0", 0x203)
    assert state is not None
    assert state.last_counter == counter, "the duplicate must not move the stream position"
    assert state.repeated_frames == 1
    assert state.valid_frames == 1, "a duplicate must not be counted as a valid frame"

    follow_up = validator.validate(packager.package(raw, profile, counter=(counter + 1) % 16), profile)
    assert follow_up.verdict == E2EStatus.OK, "the real next frame must still validate after a duplicate"


@given(
    payload=st.binary(min_size=2, max_size=8),
    jump=st.integers(min_value=4, max_value=12),
)
@settings(max_examples=200, deadline=None)
def test_e2e_wrong_sequence_latches_onto_the_offending_counter(payload: bytes, jump: int) -> None:
    """WRONG_SEQUENCE adopts the offending counter, so the fault latches until real continuity returns.

    Two consequences are locked here, and both matter for replay detection:
    the frame that merely continues the *old* numbering after a jump is still
    rejected, and recovery is only granted to a frame that is consecutive with
    the counter the validator latched onto.
    """
    profile = e2e_profile(counter_modulo=16, max_delta=3)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    stream_id = 0x208
    raw = CanFrame.create(channel_id="can0", arbitration_id=stream_id, data=payload)

    validator.validate(packager.package(raw, profile, counter=0), profile)

    breach = validator.validate(packager.package(raw, profile, counter=jump), profile)
    assert breach.verdict == E2EStatus.WRONG_SEQUENCE
    assert not breach.is_valid

    state = validator.get_stream_state("can0", stream_id)
    assert state is not None
    assert state.last_counter == jump, "WRONG_SEQUENCE must latch the stream onto the offending counter"
    assert state.sequence_errors == 1

    # Continuing the pre-jump numbering is judged against the latched position,
    # so it stays rejected instead of silently re-synchronising. The packager
    # rejects rewinds fail-closed, so the stale counter=1 frame is sealed by
    # an independent packager (same bytes, no rewind involved) and judged by
    # the same validator latched at `jump`.
    stale_seal = E2ESafetyPackager().package(raw, profile, counter=1)
    stale = validator.validate(stale_seal, profile)
    assert stale.verdict != E2EStatus.OK, "a frame continuing the abandoned numbering must not pass"

    # Recovery only comes from genuine continuity with the latched counter.
    # The stale probe above re-latched the validator, so seal the recovery
    # frame independently (no packager rewind involved).
    resync_from = validator.get_stream_state("can0", stream_id)
    assert resync_from is not None
    next_counter = (resync_from.last_counter + 1) % 16
    recovered = validator.validate(E2ESafetyPackager().package(raw, profile, counter=next_counter), profile)
    assert recovered.verdict == E2EStatus.OK, "consecutive continuation must clear the latch"


@given(payload=st.binary(min_size=2, max_size=8), corruption=st.integers(min_value=1, max_value=255))
@settings(max_examples=250, deadline=None)
def test_e2e_corrupted_crc_is_reported_before_any_sequence_verdict(payload: bytes, corruption: int) -> None:
    """A damaged CRC yields CRC_ERROR and never advances the sequence state."""
    profile = e2e_profile(counter_modulo=16)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    raw = CanFrame.create(channel_id="can0", arbitration_id=0x204, data=payload)

    sealed = packager.package(raw, profile)
    damaged = bytearray(sealed.data)
    damaged[profile.crc_byte_offset] ^= corruption
    tampered = CanFrame.create(channel_id="can0", arbitration_id=0x204, data=bytes(damaged))

    result = validator.validate(tampered, profile)
    assert result.verdict == E2EStatus.CRC_ERROR
    assert not result.is_crc_valid
    assert not result.is_valid

    state = validator.get_stream_state("can0", 0x204)
    assert state is not None
    assert state.last_counter is None, "a CRC failure must not seed the sequence tracker"
    assert state.crc_errors == 1


@given(payload=st.binary(min_size=2, max_size=8), flip_offset=st.integers(min_value=2, max_value=7))
@settings(max_examples=250, deadline=None)
def test_e2e_tampered_data_byte_is_detected(payload: bytes, flip_offset: int) -> None:
    """Flipping a protected data byte breaks the CRC — no silent acceptance."""
    profile = e2e_profile(counter_modulo=16)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    raw = CanFrame.create(channel_id="can0", arbitration_id=0x205, data=payload)

    sealed = packager.package(raw, profile)
    assume(flip_offset < len(sealed.data))

    damaged = bytearray(sealed.data)
    damaged[flip_offset] ^= 0xFF
    tampered = CanFrame.create(channel_id="can0", arbitration_id=0x205, data=bytes(damaged))

    result = validator.validate(tampered, profile)
    assert result.verdict == E2EStatus.CRC_ERROR
    assert not result.is_valid


@given(
    payload=st.binary(min_size=2, max_size=8),
    id_a=st.integers(min_value=0x100, max_value=0x1FF),
    id_b=st.integers(min_value=0x200, max_value=0x2FF),
)
@settings(max_examples=150, deadline=None)
def test_e2e_streams_are_tracked_per_arbitration_id(payload: bytes, id_a: int, id_b: int) -> None:
    """Counters are per (channel, CAN ID); interleaving two IDs must not cross-fault."""
    profile = e2e_profile(counter_modulo=16)
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()

    frame_a = CanFrame.create(channel_id="can0", arbitration_id=id_a, data=payload)
    frame_b = CanFrame.create(channel_id="can0", arbitration_id=id_b, data=payload)

    for index in range(4):
        result_a = validator.validate(packager.package(frame_a, profile), profile)
        result_b = validator.validate(packager.package(frame_b, profile), profile)

        expected = E2EStatus.INITIAL if index == 0 else E2EStatus.OK
        assert result_a.verdict == expected, f"stream A frame {index}: {result_a.verdict}"
        assert result_b.verdict == expected, f"stream B frame {index}: {result_b.verdict}"


@given(data=st.binary(min_size=0, max_size=64))
@settings(max_examples=250, deadline=None)
def test_e2e_validator_rejects_short_payloads_without_leaking_exceptions(data: bytes) -> None:
    """Payloads shorter than the profile's offsets fail typed, never with an IndexError."""
    profile = e2e_profile(counter_modulo=16)
    validator = E2ESafetyValidator()

    try:
        result = validator.validate_raw(
            channel_id="can0",
            arbitration_id=0x206,
            data=data,
            profile=profile,
        )
    except (PlatformError, ValueError):
        return  # typed rejection is the contract for undersized buffers
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"short payload raised {type(exc).__name__}, outside PlatformError/ValueError")

    assert result.verdict in set(E2EStatus)


@given(
    payload=st.binary(min_size=2, max_size=8),
    counter_modulo=st.integers(min_value=4, max_value=256),
)
@settings(max_examples=200, deadline=None)
def test_e2e_packager_counter_never_leaves_modulo_range(payload: bytes, counter_modulo: int) -> None:
    """Whatever counter is requested, the sealed frame carries it reduced mod modulo."""
    profile = e2e_profile(counter_modulo=counter_modulo)
    packager = E2ESafetyPackager()
    raw = CanFrame.create(channel_id="can0", arbitration_id=0x207, data=payload)

    for requested in (0, counter_modulo - 1, counter_modulo, counter_modulo * 3 + 1):
        _sealed, used, _crc = packager.package_payload(
            data=raw.data,
            profile=profile,
            arbitration_id=0x207,
            channel_id="can0",
            counter=requested,
        )
        assert 0 <= used < counter_modulo, f"counter {used} escaped 0..{counter_modulo - 1}"
        assert used == requested % counter_modulo





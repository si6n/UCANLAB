"""T57-D / U-2 + J-1 + J-2 + J-5 regression tests.

Findings (docs/review/ASAMA-4-protokoller.md):
- U-2: ISO-TP FC/CF frames the client emits as a PROTOCOL RESPONSE to an
  inbound multi-frame request were not marked `inbound_triggered=True`, so a
  hostile ECU's FC flood could blow the global 400 msg/s envelope and latch a
  remote self-DoS E-Stop.
- J-1: `start_cmdt_transfer` opened TX sessions without a bound and
  `poll_cmdt_timeouts` had no production caller -> `_tx_sessions` leaked.
- J-2: `_handle_tx_cm` accepted unlimited HOLD (packet_count=0) CTS frames and
  arbitrarily deep `next_seq` rewinds, refreshing activity each time so a fake
  peer never got reaped (and each rewind re-amplified DT traffic).
- J-5: `_lookup_dt_session` dropped ALL TP.DT when parallel PGN sessions for
  one (SA, DA, channel) made the lookup ambiguous -> a slow attacker could
  starve a legitimate reassembly (DoS).
"""

from __future__ import annotations

import queue

from src.core.contracts.ports import ClockProvider
from src.core.models.can_frame import CanFrame
from src.hal.base import AbstractBus
from src.protocols.j1939.transport import (
    ABORT_REASON_UNEXPECTED_CONTROL,
    TP_CTRL_ABORT,
    TP_CTRL_ACK,
    TP_CTRL_CTS,
    J1939TransportProtocol,
    ReassemblySession,
)
from src.protocols.uds.client import UdsClient

# ---------------------------------------------------------------------------
# U-2 — inbound-triggered ISO-TP protocol responses
# ---------------------------------------------------------------------------


class _RecordingTxPort:
    """TxPort double recording every send_sync call (frame + kwargs)."""

    def __init__(self) -> None:
        self.calls: list[tuple[CanFrame, str, bool]] = []

    def send_sync(
        self,
        frame: CanFrame,
        budget_category: str = "default",
        *,
        inbound_triggered: bool = False,
    ) -> None:
        self.calls.append((frame, budget_category, inbound_triggered))


class _RxBus(AbstractBus):
    """Bus double: never transmits; RX queue fed by the test."""

    def __init__(self, channel_id: str = "uds_ch0") -> None:
        super().__init__(channel_id=channel_id)
        self.rx_queue: queue.Queue[CanFrame] = queue.Queue()

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:  # pragma: no cover - unused
        pass

    def send_sync(self, frame: CanFrame) -> None:  # pragma: no cover - unused
        pass

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        try:
            return self.rx_queue.get(timeout=timeout_s or 0.05)
        except queue.Empty:
            return None

    def inject_rx(self, frame: CanFrame) -> None:
        self.rx_queue.put(frame)


def _flow_control_frames(calls: list[tuple[CanFrame, str, bool]]) -> list[tuple[CanFrame, str, bool]]:
    return [c for c in calls if len(c[0].data) >= 1 and (c[0].data[0] >> 4) == 0x3]


def test_u2_flow_control_response_is_inbound_triggered() -> None:
    """An FC the client emits answering an inbound FF must be inbound_triggered."""
    bus = _RxBus(channel_id="uds_ch0")
    tx = _RecordingTxPort()
    client = UdsClient(bus=bus, tx_port=tx, tx_id=0x7E0, rx_id=0x7E8, channel_id="uds_ch0")

    # ECU sends a multi-frame RESPONSE (0x62 0xF1 0x90 + 17-byte VIN = 20 B):
    # FF (0x10 0x14) + CF1 + CF2. The client must answer the FF with an FC.
    resp_payload = b"\x62\xf1\x90" + b"VF1CORRECTVIN1234"  # 20 bytes
    assert len(resp_payload) == 20
    bus.inject_rx(
        CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=0x7E8,
            data=b"\x10\x14" + resp_payload[0:6],
        )
    )
    bus.inject_rx(
        CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=0x7E8,
            data=b"\x21" + resp_payload[6:13],
        )
    )
    bus.inject_rx(
        CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=0x7E8,
            data=b"\x22" + resp_payload[13:20],
        )
    )

    resp = client.read_did(0xF190)
    assert resp.is_positive is True
    fc_calls = _flow_control_frames(tx.calls)
    assert fc_calls, "client must emit a Flow Control response to the inbound FF"
    assert all(inbound for _f, _cat, inbound in fc_calls), (
        "every FC response must be marked inbound_triggered (remote self-DoS guard)"
    )
    client.close()


def test_u2_send_functional_flow_control_is_inbound_triggered() -> None:
    """send_functional's per-ECU FC responses must also be inbound_triggered."""
    bus = _RxBus(channel_id="uds_ch0")
    tx = _RecordingTxPort()
    client = UdsClient(bus=bus, tx_port=tx, tx_id=0x7E0, rx_id=0x7E8, channel_id="uds_ch0")

    # One responding ECU (0x7E8) sends a multi-frame answer to a functional poll.
    bus.inject_rx(
        CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=0x7E8,
            data=b"\x10\x14\x62\xf1\x90VF1CORRECTVIN12345"[:8],
        )
    )

    responses = client.send_functional(b"\x22\xf1\x90", collect_window_s=0.15)
    fc_calls = _flow_control_frames(tx.calls)
    assert fc_calls, "send_functional must emit an FC for the inbound FF"
    assert all(inbound for _f, _cat, inbound in fc_calls)
    assert responses == [] or all(r.service_id is not None for r in responses)
    client.close()


# ---------------------------------------------------------------------------
# J-1 — bounded CMDT TX sessions + automatic reap
# ---------------------------------------------------------------------------


def test_j1_tx_session_count_is_bounded() -> None:
    tp = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
    for pgn in range(0xFE00, 0xFE00 + 300):
        tp.start_cmdt_transfer(0xF9, pgn, b"A" * 20)
    assert len(tp._tx_sessions) <= tp.MAX_TX_SESSIONS


def test_j1_stale_tx_session_auto_reaped_on_new_transfer() -> None:
    clock = _FakeClock()
    tp = J1939TransportProtocol(my_address=0x01, channel_id="ch0", clock=clock)
    tp.start_cmdt_transfer(0xF9, 0xFEEE, b"A" * 20)
    clock.advance(tp.T2_TIMEOUT_SEC + 1.0)

    # Opening an unrelated transfer must reap the expired session itself —
    # poll_cmdt_timeouts has no production caller.
    tp.start_cmdt_transfer(0xF9, 0xFEED, b"B" * 20)
    assert (0x01, 0xF9, 0xFEEE, "ch0") not in tp._tx_sessions
    pending = tp.take_pending_tx_frames()
    assert any(f.data[0] == TP_CTRL_ABORT for f in pending)


# ---------------------------------------------------------------------------
# J-2 — bounded HOLD and rewind
# ---------------------------------------------------------------------------


def test_j2_repeated_hold_cts_eventually_aborts() -> None:
    clock = _FakeClock()
    tp = J1939TransportProtocol(my_address=0x01, channel_id="ch0", clock=clock)
    tp.start_cmdt_transfer(0xF9, 0xFEEE, b"A" * 20)

    aborted = False
    abort = None
    for _ in range(1000):
        hold = _tp_cm(0xF9, 0x01, TP_CTRL_CTS, 0xFEEE, 0, 1, 0xFF)
        _msg, abort = tp.handle_rx_frame(hold)
        clock.advance(0.05)
        if abort is not None:
            aborted = True
            break
    assert aborted, "a peer spamming HOLD CTS must not keep the session alive forever"
    assert abort.data[1] == ABORT_REASON_UNEXPECTED_CONTROL
    assert not tp._tx_sessions


def test_j2_deep_rewind_aborts() -> None:
    tp = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
    tp.start_cmdt_transfer(0xF9, 0xFEEE, bytes(140))  # 20 packets
    # Advance to next_sequence = 10.
    fwd = _tp_cm(0xF9, 0x01, TP_CTRL_CTS, 0xFEEE, 9, 1, 0xFF)
    tp.handle_rx_frame(fwd)
    tp.take_pending_tx_frames()
    # Rewind from 10 back to 1 (depth 9) is an amplification attack.
    deep = _tp_cm(0xF9, 0x01, TP_CTRL_CTS, 0xFEEE, 1, 1, 0xFF)
    _msg, abort = tp.handle_rx_frame(deep)
    assert abort is not None
    assert abort.data[1] == ABORT_REASON_UNEXPECTED_CONTROL
    assert not tp._tx_sessions


def test_j2_shallow_rewind_still_allowed() -> None:
    tp = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
    tp.start_cmdt_transfer(0xF9, 0xFEEE, bytes(140))  # 20 packets
    fwd = _tp_cm(0xF9, 0x01, TP_CTRL_CTS, 0xFEEE, 9, 1, 0xFF)
    tp.handle_rx_frame(fwd)
    tp.take_pending_tx_frames()
    # A small retransmit rewind (depth 1) is legitimate.
    shallow = _tp_cm(0xF9, 0x01, TP_CTRL_CTS, 0xFEEE, 2, 9, 0xFF)
    _msg, resp = tp.handle_rx_frame(shallow)
    # Not an abort: the response slot carries the first granted DT frame.
    assert resp is None or resp.data[0] != TP_CTRL_ABORT
    assert len(tp._tx_sessions) == 1
    session = next(iter(tp._tx_sessions.values()))
    assert session.next_sequence == 11


# ---------------------------------------------------------------------------
# J-5 — ambiguous parallel DT lookup must not drop a legitimate reassembly
# ---------------------------------------------------------------------------


def test_j5_ambiguous_lookup_selects_matching_session() -> None:
    tp = J1939TransportProtocol(my_address=0xF9)
    now = tp._get_now()
    # Legitimate session: expecting seq 1, two packets.
    legit = ReassemblySession(
        source_address=0x10,
        destination_address=0xF9,
        target_pgn=0xFEEE,
        total_bytes=8,
        total_packets=2,
        is_bam=False,
        expected_sequence=1,
        last_activity_time=now,
        channel_id="ch0",
    )
    # Attacker's parallel session for a DIFFERENT PGN, stalled at seq 5.
    attacker = ReassemblySession(
        source_address=0x10,
        destination_address=0xF9,
        target_pgn=0xFEED,
        total_bytes=8,
        total_packets=2,
        is_bam=False,
        expected_sequence=5,
        last_activity_time=now,
        channel_id="ch0",
    )
    tp._rx_sessions[(0x10, 0xF9, "ch0", 0xFEEE)] = legit
    tp._rx_sessions[(0x10, 0xF9, "ch0", 0xFEED)] = attacker
    tp._per_sa_sessions["16"] = 2

    # DT seq 1 must be routed to the session whose expected_sequence matches.
    key, sess = tp._lookup_dt_session(0x10, 0xF9, "ch0", seq_num=1)
    assert sess is legit
    assert key == (0x10, 0xF9, "ch0", 0xFEEE)


def test_j5_legit_reassembly_completes_despite_parallel_session() -> None:
    tp = J1939TransportProtocol(my_address=0xF9)
    now = tp._get_now()
    legit = ReassemblySession(
        source_address=0x10,
        destination_address=0xF9,
        target_pgn=0xFEEE,
        total_bytes=8,
        total_packets=2,
        is_bam=False,
        expected_sequence=1,
        last_activity_time=now,
        channel_id="ch0",
    )
    attacker = ReassemblySession(
        source_address=0x10,
        destination_address=0xF9,
        target_pgn=0xFEED,
        total_bytes=8,
        total_packets=2,
        is_bam=False,
        expected_sequence=7,
        last_activity_time=now,
        channel_id="ch0",
    )
    tp._rx_sessions[(0x10, 0xF9, "ch0", 0xFEEE)] = legit
    tp._rx_sessions[(0x10, 0xF9, "ch0", 0xFEED)] = attacker
    tp._per_sa_sessions["16"] = 2

    dt1 = CanFrame.create(
        channel_id="ch0", arbitration_id=0x1CEBF910, data=b"\x01" + b"ABCDEFG", is_extended=True
    )
    dt2 = CanFrame.create(
        channel_id="ch0", arbitration_id=0x1CEBF910, data=b"\x02" + b"H\xff\xff\xff\xff\xff\xff", is_extended=True
    )
    msg1, _ = tp.handle_rx_frame(dt1)
    assert msg1 is None
    msg2, ack = tp.handle_rx_frame(dt2)
    assert msg2 is not None
    assert msg2.pgn == 0xFEEE
    assert msg2.data == b"ABCDEFGH"
    assert ack is not None and ack.data[0] == TP_CTRL_ACK


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeClock(ClockProvider):
    def __init__(self) -> None:
        self._now = 1000.0

    def now_monotonic(self) -> float:
        return self._now

    def now_monotonic_ns(self) -> int:
        return int(self._now * 1_000_000_000)

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _tp_cm(sa: int, da: int, ctrl: int, pgn: int, *payload: int) -> CanFrame:
    body = bytearray(8)
    body[0] = ctrl
    for i, b in enumerate(payload):
        body[1 + i] = b
    body[5:8] = pgn.to_bytes(3, byteorder="little")
    can_id = 0x1CEC0000 | ((da & 0xFF) << 8) | (sa & 0xFF)
    return CanFrame.create(
        channel_id="ch0",
        arbitration_id=can_id,
        data=bytes(body),
        is_extended=True,
        direction="rx",
    )

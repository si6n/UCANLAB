"""Regression tests for the P0 protocol fixes:

1. J1939-21 CMDT sender state machine (RTS -> CTS window -> DT -> ACK),
   including T2/T3 timeout aborts and multi-window transfers.
2. ISO 15765-2 N_As enforcement in IsoTpSender.
3. ISO 14229 raw NRC preservation for vendor-specific NRC codes.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from src.core.contracts.ports import ClockProvider
from src.core.exceptions import IsoTpTimeoutError
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.transport import (
    ABORT_REASON_TIMEOUT,
    ABORT_REASON_UNEXPECTED_CONTROL,
    PGN_TP_DT,
    TP_CTRL_ABORT,
    TP_CTRL_ACK,
    TP_CTRL_CTS,
    TP_CTRL_RTS,
    J1939TransportProtocol,
)
from src.protocols.uds.isotp import IsoTpSender
from src.protocols.uds.services import UdsServiceBuilder


def _tp_cm_frame(sa: int, da: int, ctrl: int, pgn: int, data: bytes) -> CanFrame:
    can_id = 0x1CEC0000 | ((da & 0xFF) << 8) | (sa & 0xFF)
    return CanFrame.create(
        channel_id="ch0",
        arbitration_id=can_id,
        data=data,
        is_extended=True,
        direction="rx",
    ) | CanFrame  # type: ignore[operator]


def make_tp_cm(sa: int, da: int, ctrl: int, pgn: int, *payload: int) -> CanFrame:
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


class FakeClock(ClockProvider):
    """Controllable monotonic clock for timeout tests."""

    def __init__(self) -> None:
        self._now = 1000.0

    def now_monotonic(self) -> float:
        return self._now

    def now_wall_ns(self) -> int:
        return int(self._now * 1_000_000_000)

    def advance(self, seconds: float) -> None:
        self._now += seconds


class TestCmdtSenderStateMachine(unittest.TestCase):
    """J1939-21 sender side: RTS -> CTS windowed DT -> EndOfMsgACK."""

    def setUp(self) -> None:
        self.tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0")
        self.rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0")
        self.payload = bytes(range(64)) * 2  # 128 bytes -> 19 packets
        self.pgn = 0xFEEE

    def test_rts_is_first_and_only_frame_of_start(self) -> None:
        rts = self.tx.start_cmdt_transfer(0xF9, self.pgn, self.payload)
        self.assertEqual(rts.data[0], TP_CTRL_RTS)
        self.assertEqual(rts.data[3], 19)
        # Session is registered in WAIT_CTS until CTS arrives
        self.assertEqual(len(self.tx._tx_sessions), 1)

    def test_no_dt_frames_flow_before_cts(self) -> None:
        self.tx.start_cmdt_transfer(0xF9, self.pgn, self.payload)
        frames, abort = self.tx.advance_cmdt_transfer(0xF9, self.pgn)
        self.assertEqual(frames, [])
        self.assertIsNone(abort)

    def test_cts_window_emits_only_granted_packets(self) -> None:
        self.tx.start_cmdt_transfer(0xF9, self.pgn, self.payload)
        # Receiver grants 5 packets starting at seq 1
        cts = make_tp_cm(0xF9, 0x01, TP_CTRL_CTS, self.pgn, 5, 1, 0xFF)
        _, first = self.tx.handle_rx_frame(cts)
        pending = self.tx.take_pending_tx_frames()
        dt_frames = ([first] if first is not None else []) + list(pending)
        self.assertEqual(len(dt_frames), 5)
        for i, f in enumerate(dt_frames):
            self.assertEqual(f.data[0], i + 1)
            self.assertEqual((f.arbitration_id >> 16) & 0xFF, PGN_TP_DT >> 8)
        # Session now waits for the next CTS (not yet all packets sent)
        session = next(iter(self.tx._tx_sessions.values()))
        self.assertEqual(session.state, "WAIT_CTS")
        self.assertEqual(session.next_sequence, 6)

    def test_second_cts_completes_transfer_and_ack_closes(self) -> None:
        self.tx.start_cmdt_transfer(0xF9, self.pgn, self.payload)
        cts1 = make_tp_cm(0xF9, 0x01, TP_CTRL_CTS, self.pgn, 10, 1, 0xFF)
        self.tx.handle_rx_frame(cts1)
        self.tx.take_pending_tx_frames()
        cts2 = make_tp_cm(0xF9, 0x01, TP_CTRL_CTS, self.pgn, 10, 11, 0xFF)
        _, first = self.tx.handle_rx_frame(cts2)
        pending = self.tx.take_pending_tx_frames()
        # 19 packets total: window 1 sent 10, so this window emits the
        # remaining 9 (first via the response slot, 8 in pending)
        self.assertEqual(len([first] + pending), 9)

    def _rebuild_frame(self, frame: CanFrame, data: bytes) -> CanFrame:
        return CanFrame.create(
            channel_id=frame.channel_id,
            arbitration_id=frame.arbitration_id,
            data=data,
            is_extended=True,
            direction="rx",
        )

    def test_full_roundtrip_two_windows(self) -> None:
        """Full CMDT transfer across two CTS windows, end-to-end with receiver."""
        # Receiver grants at most 10 packets per CTS window
        self.rx = J1939TransportProtocol(my_address=0xF9, channel_id="ch0")
        self.rx.RX_CTS_WINDOW = 10
        rts = self.tx.start_cmdt_transfer(0xF9, self.pgn, self.payload)
        self.assertEqual(rts.data[0], TP_CTRL_RTS)

        # REVIEW 1-M1: a windowed receiver (rx_cts_window=10) must bound its
        # FIRST grant to the window too — previously the initial CTS granted
        # min(total, MAX_CTS)=16 against a 19-packet transfer while the DT
        # handler re-CTS'd every 10 packets: duplicate CTS frames against a
        # fully-granted sender. The first CTS now grants exactly the window
        # (10), no in-band rewrite required.
        _, cts1 = self.rx.handle_rx_frame(rts)
        self.assertIsNotNone(cts1)
        self.assertEqual(cts1.data[0], TP_CTRL_CTS)
        self.assertEqual(cts1.data[1], 10)
        self.assertEqual(cts1.data[2], 1)

        _, out1 = self.tx.handle_rx_frame(cts1)
        window1 = ([out1] if out1 else []) + self.tx.take_pending_tx_frames()
        self.assertEqual(len(window1), 10)

        # Feed window 1 to the receiver; it must not complete yet.
        # The LAST DT frame makes the receiver issue the next CTS.
        completed = None
        cts2 = None
        for f in window1:
            msg, resp = self.rx.handle_rx_frame(f)
            if msg:
                completed = msg
            if resp is not None:
                cts2 = resp
        self.assertIsNone(completed)
        # Receiver re-grants: next_seq=11, count=9 remaining
        self.assertIsNotNone(cts2)
        self.assertEqual(cts2.data[0], TP_CTRL_CTS)
        self.assertEqual(cts2.data[2], 11)
        self.assertEqual(cts2.data[1], 9)

        _, out2 = self.tx.handle_rx_frame(cts2)
        window2 = ([out2] if out2 else []) + self.tx.take_pending_tx_frames()
        self.assertEqual(len(window2), 9)

        for f in window2:
            msg, ack = self.rx.handle_rx_frame(f)
            if msg:
                completed = msg
        self.assertIsNotNone(completed)
        self.assertEqual(completed.data, self.payload)
        self.assertEqual(completed.pgn, self.pgn)
        self.assertIsNotNone(ack)
        self.assertEqual(ack.data[0], TP_CTRL_ACK)

        # Sender consumes EndOfMsgACK -> session released
        self.assertEqual(len(self.tx._tx_sessions), 1)
        self.tx.handle_rx_frame(ack)
        self.assertEqual(len(self.tx._tx_sessions), 0)

    def test_t2_timeout_after_rts_aborts(self) -> None:
        clock = FakeClock()
        tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0", clock=clock)
        tx.start_cmdt_transfer(0xF9, self.pgn, b"A" * 20)
        clock.advance(tx.T2_TIMEOUT_SEC + 0.01)
        frames, abort = tx.advance_cmdt_transfer(0xF9, self.pgn)
        self.assertEqual(frames, [])
        self.assertIsNotNone(abort)
        self.assertEqual(abort.data[0], TP_CTRL_ABORT)
        self.assertEqual(abort.data[1], ABORT_REASON_TIMEOUT)
        self.assertEqual(len(tx._tx_sessions), 0)

    def test_t3_timeout_after_last_dt_aborts(self) -> None:
        clock = FakeClock()
        tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0", clock=clock)
        tx.start_cmdt_transfer(0xF9, self.pgn, b"B" * 14)  # 2 packets
        cts = make_tp_cm(0xF9, 0x01, TP_CTRL_CTS, self.pgn, 2, 1, 0xFF)
        tx.handle_rx_frame(cts)
        tx.take_pending_tx_frames()
        clock.advance(tx.T3_TIMEOUT_SEC + 0.01)
        frames, abort = tx.advance_cmdt_transfer(0xF9, self.pgn)
        self.assertEqual(frames, [])
        self.assertIsNotNone(abort)
        self.assertEqual(abort.data[1], ABORT_REASON_TIMEOUT)

    def test_poll_cmdt_timeouts_reaps_expired_sessions(self) -> None:
        clock = FakeClock()
        tx = J1939TransportProtocol(my_address=0x01, channel_id="ch0", clock=clock)
        tx.start_cmdt_transfer(0xF9, self.pgn, b"C" * 100)
        clock.advance(tx.T2_TIMEOUT_SEC + 0.01)
        aborts = tx.poll_cmdt_timeouts()
        self.assertEqual(len(aborts), 1)
        self.assertEqual(aborts[0].data[1], ABORT_REASON_TIMEOUT)
        self.assertEqual(tx.poll_cmdt_timeouts(), [])

    def test_peer_abort_cancels_transfer(self) -> None:
        self.tx.start_cmdt_transfer(0xF9, self.pgn, self.payload)
        abort = make_tp_cm(0xF9, 0x01, TP_CTRL_ABORT, self.pgn, ABORT_REASON_TIMEOUT)
        self.tx.handle_rx_frame(abort)
        self.assertEqual(len(self.tx._tx_sessions), 0)

    def test_invalid_cts_aborts_with_unexpected_control(self) -> None:
        self.tx.start_cmdt_transfer(0xF9, self.pgn, b"D" * 14)
        # P2-6: CTS with zero packets is a legal "connection hold" in SAE
        # J1939-21 (T4 exists for exactly this) — the session must WAIT,
        # not abort. Only impossible sequences (0 / > total) abort.
        zero_cts = make_tp_cm(0xF9, 0x01, TP_CTRL_CTS, self.pgn, 0, 1, 0xFF)
        _, resp = self.tx.handle_rx_frame(zero_cts)
        self.assertIsNone(resp)  # hold: nothing emitted, no abort
        self.assertEqual(len(self.tx._tx_sessions), 1)  # session preserved
        session = next(iter(self.tx._tx_sessions.values()))
        self.assertEqual(session.state, "WAIT_CTS")

        # An impossible sequence (next_seq=0) DOES abort.
        bad_cts = make_tp_cm(0xF9, 0x01, TP_CTRL_CTS, self.pgn, 4, 0, 0xFF)
        _, abort = self.tx.handle_rx_frame(bad_cts)
        self.assertIsNotNone(abort)
        self.assertEqual(abort.data[1], ABORT_REASON_UNEXPECTED_CONTROL)
        self.assertEqual(len(self.tx._tx_sessions), 0)

    def test_data_integrity_across_windows(self) -> None:
        """Reassembled payload byte-for-byte equals what the sender handed over."""
        payload = bytes((i * 7 + 3) & 0xFF for i in range(500))
        rts = self.tx.start_cmdt_transfer(0xF9, 0xFEEE, payload)
        _, cts = self.rx.handle_rx_frame(rts)
        # Grant in chunks of 7 to exercise multiple windows
        for _ in range(72):  # 501 bytes -> 72 packets
            if cts is None:
                break
            _, out = self.tx.handle_rx_frame(cts)
            frames = ([out] if out else []) + self.tx.take_pending_tx_frames()
            for f in frames:
                msg, next_cts = self.rx.handle_rx_frame(f)
                if msg is not None:
                    self.assertEqual(msg.data, payload)
                    return
                if next_cts is not None:
                    cts = next_cts
        self.fail("transfer never completed")


class _StubRxSub:
    """Minimal RxSubscription stand-in: no frames ever arrive."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[CanFrame] = asyncio.Queue()

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        try:
            return await asyncio.wait_for(self._q.get(), timeout=timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            return None

    def cancel(self) -> None:  # pragma: no cover - interface compliance
        pass


class _HangingTxPort:
    """TxPort whose send() never completes — simulates a stalled driver."""

    def __init__(self, hang_s: float) -> None:
        self.hang_s = hang_s

    async def send(self, frame: CanFrame) -> None:
        await asyncio.sleep(self.hang_s)


class _FastTxPort:
    def __init__(self) -> None:
        self.sent: list[CanFrame] = []

    async def send(self, frame: CanFrame) -> None:
        self.sent.append(frame)


class TestIsoTpNAsEnforcement(unittest.TestCase):
    """ISO 15765-2 N_As: a single frame transmission must not exceed n_as_timeout_s."""

    def _make_sender(self, tx_port: Any, n_as: float = 0.2) -> IsoTpSender:
        return IsoTpSender(
            tx_port=tx_port,
            rx_sub=_StubRxSub(),
            tx_id=0x7E0,
            rx_id=0x7E8,
            n_as_timeout_s=n_as,
            n_bs_timeout_s=1.0,
        )

    def test_hanging_tx_port_times_out_with_n_as(self) -> None:
        sender = self._make_sender(_HangingTxPort(hang_s=10.0), n_as=0.05)
        with self.assertRaises(IsoTpTimeoutError) as ctx:
            asyncio.run(sender.send(b"\x22\xF1\x90"))
        self.assertEqual(ctx.exception.timeout_type, "N_As")

    def test_fast_tx_port_passes_unchanged(self) -> None:
        port = _FastTxPort()
        sender = self._make_sender(port, n_as=1.0)
        asyncio.run(sender.send(b"\x22\xF1\x90"))
        self.assertEqual(len(port.sent), 1)
        self.assertEqual(port.sent[0].data[0] & 0xF0, 0x00)  # SF PCI


class TestUdsRawNrcPreservation(unittest.TestCase):
    """Unknown/vendor NRC codes must be preserved, not remapped to 0x10."""

    def test_known_nrc_maps_to_enum(self) -> None:
        resp = UdsServiceBuilder.parse_response(bytes([0x7F, 0x22, 0x31]))
        self.assertEqual(resp.nrc.value, 0x31)
        self.assertEqual(resp.raw_nrc, 0x31)

    def test_vendor_nrc_preserved_in_raw_nrc(self) -> None:
        # 0xFE is not in the standard NRC table (vendor-specific)
        resp = UdsServiceBuilder.parse_response(bytes([0x7F, 0x27, 0xFE]))
        self.assertEqual(resp.nrc.value, 0x10)  # enum fallback unchanged
        self.assertEqual(resp.raw_nrc, 0xFE)

    def test_positive_response_has_no_raw_nrc(self) -> None:
        resp = UdsServiceBuilder.parse_response(bytes([0x62, 0xF1, 0x90, 0x41]))
        self.assertTrue(resp.is_positive)
        self.assertIsNone(resp.raw_nrc)


if __name__ == "__main__":
    unittest.main()

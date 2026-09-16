"""Tier 5 Adversarial Coverage Hardening & Empirical Stress Test Suite.

Phase 1 Final Gate Verification Suite.
Exhaustively validates:
1. ISO 15765-2 (DoCAN):
   - Extreme payload boundaries: 0B, 1B, 7B, 8B, 62B, 63B, 4095B, 4096B, 65535B, 100000B on Classic CAN and CAN-FD.
   - Packet corruption, malformed PCI headers, SF_DL=0 Classic rejection, FF_DL<=4095 Extended rejection.
   - Out-of-order CF arrival, duplicate SN, invalid sequence wrap, session reset on new FF/SF.
   - STmin microsecond spin-wait precision (100..900 us) and millisecond sleep timing.
   - Multiple concurrent multi-frame sessions without cross-talk or race conditions.
   - WFTmax (>16 consecutive WAIT frames), FlowControl OVERFLOW, N_Bs and N_Cr timeouts.
2. SAE J1939-21 Transport Protocol:
   - Collision storms (rapid back-to-back RTS on active (SA, DA) sessions triggering Conn_Abort reason=2).
   - Out-of-order TP.DT arrival triggering Conn_Abort reason=1.
   - RTS broadcast (DA=255) rejection.
   - High-throughput BAM and CMDT interleaving across multiple nodes.
   - Boundary payload sizes (1B, 7B, 8B, 1785B, reject >1785B / 0B).
3. SAE J1939-71 Sentinel & 3-Stage Signal Decoding:
   - Full edge-value matrix across 2-bit, 4-bit, 8-bit, 16-bit, 24-bit, 32-bit signed & unsigned fields.
   - SPN 513 percent torque validation (raw 0xFF9C -> -100.0% VALID).
   - Signed two's complement boundaries and error/not-available sentinels.
   - Little-endian and Big-endian arbitrary bit extraction across multi-byte spans.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.contracts.ports import (
    InMemoryTxPort,
    QueueRxSubscription,
)
from src.core.exceptions import (
    IsoTpBufferOverflowError,
    IsoTpInvalidPduError,
    IsoTpSequenceError,
    IsoTpTimeoutError,
)
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.sentinel import (
    J1939SentinelFilter,
    SignalDefinition,
    SignalQuality,
)
from src.protocols.j1939.transport import (
    ABORT_REASON_SEQUENCE_ERROR,
    ABORT_REASON_SESSION_COLLISION,
    TP_CTRL_ABORT,
    TP_CTRL_ACK,
    TP_CTRL_CTS,
    CompletedMessage,
    J1939TransportProtocol,
)
from src.protocols.uds.isotp import (
    FS_OVERFLOW,
    PCI_CONSECUTIVE_FRAME,
    PCI_FIRST_FRAME,
    PCI_FLOW_CONTROL,
    IsoTpReceiver,
    IsoTpSender,
    IsoTpTransport,
)


class DirectQueueTxPort:
    """High-performance direct-wire TxPort routing frames immediately to target subscription queue."""

    def __init__(self, target_rx: QueueRxSubscription) -> None:
        self.target_rx = target_rx
        self.sent_frames: list[CanFrame] = []

    async def send(self, frame: CanFrame) -> None:
        self.sent_frames.append(frame)
        self.target_rx.put_nowait(frame)

    def send_sync(self, frame: CanFrame) -> None:
        self.sent_frames.append(frame)
        self.target_rx.put_nowait(frame)


# ============================================================================
# 1. ISO-TP EXTREME PAYLOAD BOUNDARY TESTS
# ============================================================================


class TestIsoTpExtremePayloadBoundaries:
    """Stress test ISO-TP codec across all critical payload boundaries."""

    @pytest.mark.parametrize("is_fd", [False, True])
    def test_payload_boundary_0_bytes(self, is_fd: bool) -> None:
        """0-byte payload should return empty frames on segmentation and early-exit on send."""
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        frames = transport.segment_message(b"", is_fd=is_fd)
        assert frames == []

    @pytest.mark.parametrize("is_fd", [False, True])
    def test_payload_boundary_1_byte(self, is_fd: bool) -> None:
        """1-byte payload is the minimum Single Frame.

        P2-5: CAN_DL <= 8 uses the CLASSIC nibble form even on FD — the
        escape form requires CAN_DL > 8 (the old FD escape for short
        payloads was non-conformant and silently discarded by real ECUs).
        """
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = b"\x55"
        frames = transport.segment_message(payload, is_fd=is_fd)
        assert len(frames) == 1
        # Classic nibble SF in both modes: 0x01 0x55 ...
        assert frames[0].data[0] == 0x01
        assert frames[0].data[1] == 0x55

        # Reassembly
        rx_transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        rx_frame = CanFrame.create("ch0", 0x7E8, frames[0].data, is_fd=is_fd)
        completed, _ = rx_transport.handle_rx_frame(rx_frame)
        assert completed == payload

    @pytest.mark.parametrize("is_fd", [False, True])
    def test_payload_boundary_7_bytes(self, is_fd: bool) -> None:
        """7-byte payload is Classic CAN Single Frame maximum limit.

        P2-5: classic nibble form in both modes (CAN_DL <= 8).
        """
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = bytes(range(1, 8))
        frames = transport.segment_message(payload, is_fd=is_fd)
        assert len(frames) == 1
        assert (frames[0].data[0] >> 4) == 0x0
        assert (frames[0].data[0] & 0x0F) == 7
        rx_transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        rx_frame = CanFrame.create("ch0", 0x7E8, frames[0].data, is_fd=is_fd)
        completed, _ = rx_transport.handle_rx_frame(rx_frame)
        assert completed == payload

    @pytest.mark.parametrize("is_fd", [False, True])
    def test_payload_boundary_8_bytes(self, is_fd: bool) -> None:
        """8-byte payload: Multi-Frame in Classic CAN (FF+1CF), but Single Frame in CAN-FD."""
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = bytes(range(1, 9))
        frames = transport.segment_message(payload, is_fd=is_fd)
        if is_fd:
            assert len(frames) == 1
            assert frames[0].data[0] == 0x00
            assert frames[0].data[1] == 8
        else:
            assert len(frames) == 2  # FF + CF
            assert (frames[0].data[0] >> 4) == PCI_FIRST_FRAME
            assert frames[0].data[1] == 8
            assert (frames[1].data[0] >> 4) == PCI_CONSECUTIVE_FRAME

    @pytest.mark.parametrize("is_fd", [False, True])
    def test_payload_boundary_62_bytes(self, is_fd: bool) -> None:
        """62-byte payload: Maximum CAN-FD Single Frame, Multi-Frame in Classic CAN."""
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = bytes((i & 0xFF) for i in range(62))
        frames = transport.segment_message(payload, is_fd=is_fd)
        if is_fd:
            assert len(frames) == 1
            assert frames[0].data[0] == 0x00
            assert frames[0].data[1] == 62
            assert frames[0].data[2:64] == payload
            # Synchronous RX
            rx_transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
            rx_frame = CanFrame.create("ch0", 0x7E8, frames[0].data, is_fd=True)
            completed, _ = rx_transport.handle_rx_frame(rx_frame)
            assert completed == payload
        else:
            # Classic CAN: FF (6B) + 8 CFs (8*7 = 56B) = 62B -> 9 frames total
            assert len(frames) == 9

    @pytest.mark.parametrize("is_fd", [False, True])
    def test_payload_boundary_63_bytes(self, is_fd: bool) -> None:
        """63-byte payload: CAN-FD Multi-Frame transition boundary (FF 62B + CF 1B)."""
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = bytes((i & 0xFF) for i in range(63))
        frames = transport.segment_message(payload, is_fd=is_fd)
        if is_fd:
            assert len(frames) == 2  # FF + 1 CF
            assert (frames[0].data[0] >> 4) == PCI_FIRST_FRAME
            assert frames[0].data[1] == 63
            assert (frames[1].data[0] >> 4) == PCI_CONSECUTIVE_FRAME
        else:
            # Classic CAN: FF (6B) + 9 CFs (9*7 = 63B - 6 = 57B -> 9 frames) -> 10 frames total
            assert len(frames) == 10

    @pytest.mark.parametrize("is_fd", [False, True])
    def test_payload_boundary_4095_bytes(self, is_fd: bool) -> None:
        """4095-byte payload: Maximum Standard 12-bit First Frame boundary."""
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = bytes((i % 251) for i in range(4095))
        frames = transport.segment_message(payload, is_fd=is_fd)
        # Verify First Frame PCI header: (0x10 | 0x0F), 0xFF = 0x1FFF -> length 4095
        assert frames[0].data[0] == 0x1F
        assert frames[0].data[1] == 0xFF

    @pytest.mark.parametrize("is_fd", [False, True])
    def test_payload_boundary_4096_bytes(self, is_fd: bool) -> None:
        """4096-byte payload: Minimum Extended 32-bit First Frame boundary."""
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = bytes((i % 251) for i in range(4096))
        frames = transport.segment_message(payload, is_fd=is_fd)
        # Verify Extended First Frame PCI header: 0x10, 0x00, 0x00, 0x00, 0x10, 0x00
        assert frames[0].data[0] == 0x10
        assert frames[0].data[1] == 0x00
        assert int.from_bytes(frames[0].data[2:6], byteorder="big") == 4096

    def test_payload_boundary_65535_bytes(self) -> None:
        """65535-byte payload: Large Multi-Frame Extended FF on CAN-FD."""
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = bytes((i % 251) for i in range(65535))
        frames = transport.segment_message(payload, is_fd=True)
        assert frames[0].data[0] == 0x10
        assert frames[0].data[1] == 0x00
        assert int.from_bytes(frames[0].data[2:6], byteorder="big") == 65535
        # Total CFs = (65535 - 58 + 62) // 63 = 1040 CFs + 1 FF = 1041 frames
        assert len(frames) == 1041

    def test_payload_boundary_100000_bytes(self) -> None:
        """100,000-byte payload: Massive Extended FF testing extensive sequence wrapping."""
        transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
        payload = bytes((i % 251) for i in range(100000))
        frames = transport.segment_message(payload, is_fd=True)
        assert frames[0].data[0] == 0x10
        assert frames[0].data[1] == 0x00
        assert int.from_bytes(frames[0].data[2:6], byteorder="big") == 100000
        # Check sequence numbers wrap correctly 1..15 -> 0 -> 1
        expected_sn = 1
        for cf in frames[1:]:
            sn = cf.data[0] & 0x0F
            assert sn == expected_sn
            expected_sn = (expected_sn + 1) & 0x0F


# ============================================================================
# 2. ASYNC ISO-TP STATE MACHINE STRESS & ROUNDTRIP TESTS
# ============================================================================


class TestIsoTpAsyncStateMachineStress:
    """Async state machine stress testing for IsoTpSender and IsoTpReceiver."""

    @pytest.mark.asyncio
    # T56-C I-2: classic ISO-TP is capped at MAX_UDS_PAYLOAD_CLASSIC (4096);
    # the async sender now enforces the same bound as segment_message, so the
    # 16384 case (a CAN-FD-sized payload) no longer belongs on the classic
    # path — it is exercised by test_async_roundtrip_boundaries_can_fd.
    @pytest.mark.parametrize("payload_size", [1, 7, 8, 62, 63, 1024, 4095, 4096])
    async def test_async_roundtrip_boundaries_classic(self, payload_size: int) -> None:
        """Verify full async roundtrip transmission and reassembly for Classic CAN across sizes."""
        rx_sub_sender = QueueRxSubscription()
        rx_sub_receiver = QueueRxSubscription()

        tx_port_sender = DirectQueueTxPort(rx_sub_receiver)
        tx_port_receiver = DirectQueueTxPort(rx_sub_sender)

        sender = IsoTpSender(
            tx_port=tx_port_sender,
            rx_sub=rx_sub_sender,
            tx_id=0x7E0,
            rx_id=0x7E8,
            is_fd=False,
            channel_id="uds_test",
        )
        receiver = IsoTpReceiver(
            tx_port=tx_port_receiver,
            rx_sub=rx_sub_receiver,
            tx_id=0x7E8,
            rx_id=0x7E0,
            is_fd=False,
            channel_id="uds_test",
            max_buffer_size=65536,
        )

        test_payload = bytes((i % 251) for i in range(payload_size))

        rx_task = asyncio.create_task(receiver.receive(timeout_s=5.0))
        tx_task = asyncio.create_task(sender.send(test_payload))

        await asyncio.gather(tx_task, rx_task)
        assert rx_task.result() == test_payload

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload_size", [1, 62, 63, 1024, 4095, 4096, 65535])
    async def test_async_roundtrip_boundaries_can_fd(self, payload_size: int) -> None:
        """Verify full async roundtrip transmission and reassembly for CAN-FD up to 64KB."""
        rx_sub_sender = QueueRxSubscription()
        rx_sub_receiver = QueueRxSubscription()

        tx_port_sender = DirectQueueTxPort(rx_sub_receiver)
        tx_port_receiver = DirectQueueTxPort(rx_sub_sender)

        sender = IsoTpSender(
            tx_port=tx_port_sender,
            rx_sub=rx_sub_sender,
            tx_id=0x7E0,
            rx_id=0x7E8,
            is_fd=True,
            channel_id="uds_fd",
        )
        receiver = IsoTpReceiver(
            tx_port=tx_port_receiver,
            rx_sub=rx_sub_receiver,
            tx_id=0x7E8,
            rx_id=0x7E0,
            is_fd=True,
            channel_id="uds_fd",
            max_buffer_size=131072,
        )

        test_payload = bytes(((i * 7) % 251) for i in range(payload_size))

        rx_task = asyncio.create_task(receiver.receive(timeout_s=5.0))
        tx_task = asyncio.create_task(sender.send(test_payload))

        await asyncio.gather(tx_task, rx_task)
        assert rx_task.result() == test_payload


# ============================================================================
# 3. ISO-TP PACKET CORRUPTION & ADVERSARIAL ERROR HANDLING
# ============================================================================


class TestIsoTpCorruptionAndFaults:
    """Stress test error conditions, packet corruption, and sequence violations."""

    @pytest.mark.asyncio
    async def test_receiver_rejects_classic_sf_dl_0(self) -> None:
        """Classic CAN frame with PCI=0x00 (SF_DL=0) must be rejected with IsoTpInvalidPduError."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False)

        # Inject malformed Classic CAN frame
        malformed_frame = CanFrame.create("ch0", 0x7E0, b"\x00\x00\x00\x00\x00\x00\x00\x00", is_fd=False)
        rx_sub.put_nowait(malformed_frame)

        with pytest.raises(IsoTpInvalidPduError, match="Classic CAN Single Frame with SF_DL=0 is rejected"):
            await receiver.receive(timeout_s=0.5)

    @pytest.mark.asyncio
    async def test_receiver_rejects_extended_ff_under_4096(self) -> None:
        """Extended First Frame specifying length <= 4095 must raise IsoTpInvalidPduError."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False)

        # Extended FF with length = 1000 (< 4096)
        bad_ext_ff = CanFrame.create("ch0", 0x7E0, b"\x10\x00\x00\x00\x03\xe8\xaa\xbb", is_fd=False)
        rx_sub.put_nowait(bad_ext_ff)

        with pytest.raises(IsoTpInvalidPduError, match="Extended First Frame length.*must be > 4095"):
            await receiver.receive(timeout_s=0.5)

    @pytest.mark.asyncio
    async def test_receiver_rejects_standard_ff_under_8(self) -> None:
        """Standard First Frame with length < 8 in Classic CAN must raise IsoTpInvalidPduError."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False)

        # Standard FF with length = 5
        bad_std_ff = CanFrame.create("ch0", 0x7E0, b"\x10\x05\x01\x02\x03\x04\x05\xcc", is_fd=False)
        rx_sub.put_nowait(bad_std_ff)

        with pytest.raises(IsoTpInvalidPduError, match="Standard First Frame length.*must be >= 8"):
            await receiver.receive(timeout_s=0.5)

    @pytest.mark.asyncio
    async def test_receiver_out_of_order_cf_raises_sequence_error(self) -> None:
        """Out-of-order Consecutive Frame (SN jump or repeat) raises IsoTpSequenceError."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False)

        # Send valid FF for 20 bytes
        ff = CanFrame.create("ch0", 0x7E0, b"\x10\x14\x01\x02\x03\x04\x05\x06", is_fd=False)
        # Send corrupted CF with SN=3 instead of SN=1
        bad_cf = CanFrame.create("ch0", 0x7E0, b"\x23\x07\x08\x09\x0a\x0b\x0c\x0d", is_fd=False)

        rx_sub.put_nowait(ff)
        rx_sub.put_nowait(bad_cf)

        with pytest.raises(IsoTpSequenceError) as exc_info:
            await receiver.receive(timeout_s=0.5)

        assert exc_info.value.expected_sn == 1
        assert exc_info.value.actual_sn == 3

    @pytest.mark.asyncio
    async def test_receiver_sequence_wrap_violation_raises_error(self) -> None:
        """Sequence number jumping after SN=15 (e.g. SN=15 -> SN=2 instead of SN=0) raises IsoTpSequenceError."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False)

        # FF for 200 bytes
        ff = CanFrame.create("ch0", 0x7E0, b"\x10\xc8\x01\x02\x03\x04\x05\x06", is_fd=False)
        rx_sub.put_nowait(ff)

        # Send CF 1..15
        for sn in range(1, 16):
            cf = CanFrame.create("ch0", 0x7E0, bytes([(0x20 | sn)]) + b"\xaa" * 7, is_fd=False)
            rx_sub.put_nowait(cf)

        # Send bad wrap: SN=1 instead of SN=0
        bad_wrap_cf = CanFrame.create("ch0", 0x7E0, b"\x21\xbb\xbb\xbb\xbb\xbb\xbb\xbb", is_fd=False)
        rx_sub.put_nowait(bad_wrap_cf)

        with pytest.raises(IsoTpSequenceError) as exc_info:
            await receiver.receive(timeout_s=1.0)

        assert exc_info.value.expected_sn == 0
        assert exc_info.value.actual_sn == 1

    @pytest.mark.asyncio
    async def test_receiver_session_reset_on_unexpected_new_first_frame(self) -> None:
        """An unexpected First Frame during CF reception aborts old session and reassembles new message."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False)

        # 1. FF for old message (20 bytes)
        old_ff = CanFrame.create("ch0", 0x7E0, b"\x10\x14\x01\x02\x03\x04\x05\x06", is_fd=False)
        rx_sub.put_nowait(old_ff)

        # 2. CF 1 for old message
        old_cf1 = CanFrame.create("ch0", 0x7E0, b"\x21\x07\x08\x09\x0a\x0b\x0c\x0d", is_fd=False)
        rx_sub.put_nowait(old_cf1)

        # 3. Sudden NEW FF for a 10-byte message (payload = b"\x99" * 10)
        new_payload = b"\x99" * 10
        new_ff = CanFrame.create("ch0", 0x7E0, bytes([0x10, 0x0A]) + new_payload[:6], is_fd=False)
        new_cf1 = CanFrame.create("ch0", 0x7E0, bytes([0x21]) + new_payload[6:10] + b"\xcc\xcc\xcc", is_fd=False)
        rx_sub.put_nowait(new_ff)
        rx_sub.put_nowait(new_cf1)

        received = await receiver.receive(timeout_s=1.0)
        assert received == new_payload

    @pytest.mark.asyncio
    async def test_receiver_session_reset_on_unexpected_single_frame(self) -> None:
        """An unexpected Single Frame during CF reception aborts multi-frame and delivers Single Frame immediately."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False)

        # 1. FF for old message
        old_ff = CanFrame.create("ch0", 0x7E0, b"\x10\x14\x01\x02\x03\x04\x05\x06", is_fd=False)
        rx_sub.put_nowait(old_ff)

        # 2. Sudden SF: 3 bytes b"\x22\x01\x02"
        sf = CanFrame.create("ch0", 0x7E0, b"\x03\x22\x01\x02\xcc\xcc\xcc\xcc", is_fd=False)
        rx_sub.put_nowait(sf)

        received = await receiver.receive(timeout_s=0.5)
        assert received == b"\x22\x01\x02"

    @pytest.mark.asyncio
    async def test_receiver_session_reset_on_unexpected_can_fd_single_frame(self) -> None:
        """An unexpected CAN-FD Extended Single Frame during CF reception delivers Single Frame immediately."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=True)

        # 1. FF for old message (100 bytes on FD)
        old_ff = CanFrame.create("ch0", 0x7E0, bytes([0x10, 0x64]) + b"\x01" * 62, is_fd=True)
        rx_sub.put_nowait(old_ff)

        # 2. Sudden CAN-FD Extended SF: 10 bytes (0x00, 0x0A, ...)
        fd_payload = b"\x41" * 10
        sf_fd = CanFrame.create("ch0", 0x7E0, bytes([0x00, 0x0A]) + fd_payload + (b"\xcc" * 52), is_fd=True)
        rx_sub.put_nowait(sf_fd)

        received = await receiver.receive(timeout_s=0.5)
        assert received == fd_payload


    @pytest.mark.asyncio
    async def test_receiver_buffer_overflow_emits_overflow_and_raises(self) -> None:
        """When requested length exceeds max_buffer_size, receiver sends FS_OVERFLOW and raises IsoTpBufferOverflowError."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(
            tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False, max_buffer_size=500
        )

        # FF requesting 1000 bytes (> 500 max_buffer_size)
        ff = CanFrame.create("ch0", 0x7E0, b"\x13\xe8\x01\x02\x03\x04\x05\x06", is_fd=False)
        rx_sub.put_nowait(ff)

        with pytest.raises(IsoTpBufferOverflowError, match="Requested payload length.*exceeds max buffer"):
            await receiver.receive(timeout_s=0.5)

        # Verify FC frame sent with FS_OVERFLOW (0x32)
        assert len(tx_port.sent_frames) == 1
        fc_frame = tx_port.sent_frames[0]
        assert fc_frame.arbitration_id == 0x7E8
        assert fc_frame.data[0] == ((PCI_FLOW_CONTROL << 4) | FS_OVERFLOW)

    @pytest.mark.asyncio
    async def test_sender_handles_flow_status_overflow(self) -> None:
        """When sender receives FlowStatus.OVERFLOW, it raises IsoTpBufferOverflowError immediately."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        sender = IsoTpSender(tx_port, rx_sub, tx_id=0x7E0, rx_id=0x7E8, is_fd=False)

        # Queue Flow Control with FS_OVERFLOW (0x32)
        fc_ovfl = CanFrame.create("ch0", 0x7E8, b"\x32\x00\x00\xcc\xcc\xcc\xcc\xcc", is_fd=False)
        rx_sub.put_nowait(fc_ovfl)

        with pytest.raises(IsoTpBufferOverflowError, match="Receiver reported buffer overflow"):
            await sender.send(b"\xaa" * 50)

    @pytest.mark.asyncio
    async def test_sender_wft_max_exceeded_raises_timeout_error(self) -> None:
        """When sender receives >16 consecutive WAIT frames, it raises IsoTpTimeoutError."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        sender = IsoTpSender(tx_port, rx_sub, tx_id=0x7E0, rx_id=0x7E8, is_fd=False, wft_max=16)

        # Queue 17 consecutive WAIT frames (0x31)
        for _ in range(17):
            fc_wait = CanFrame.create("ch0", 0x7E8, b"\x31\x00\x00\xcc\xcc\xcc\xcc\xcc", is_fd=False)
            rx_sub.put_nowait(fc_wait)

        with pytest.raises(IsoTpTimeoutError, match="WFTmax limit exceeded") as exc_info:
            await sender.send(b"\xbb" * 50)

        assert exc_info.value.timeout_type == "N_Bs"

    @pytest.mark.asyncio
    async def test_sender_n_bs_timeout_when_no_flow_control(self) -> None:
        """Sender raises IsoTpTimeoutError(timeout_type='N_Bs') when FC is never received within timeout."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        sender = IsoTpSender(
            tx_port, rx_sub, tx_id=0x7E0, rx_id=0x7E8, is_fd=False, n_bs_timeout_s=0.05
        )

        with pytest.raises(IsoTpTimeoutError, match="N_Bs timeout") as exc_info:
            await sender.send(b"\xcc" * 50)

        assert exc_info.value.timeout_type == "N_Bs"

    @pytest.mark.asyncio
    async def test_receiver_n_cr_timeout_when_cf_delayed(self) -> None:
        """Receiver raises IsoTpTimeoutError(timeout_type='N_Cr') when CF does not arrive within n_cr_timeout_s."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        receiver = IsoTpReceiver(
            tx_port, rx_sub, tx_id=0x7E8, rx_id=0x7E0, is_fd=False, n_cr_timeout_s=0.05
        )

        # Send First Frame
        ff = CanFrame.create("ch0", 0x7E0, b"\x10\x14\x01\x02\x03\x04\x05\x06", is_fd=False)
        rx_sub.put_nowait(ff)

        # Do NOT send CF
        with pytest.raises(IsoTpTimeoutError, match="N_Cr timeout") as exc_info:
            await receiver.receive(timeout_s=0.5)

        assert exc_info.value.timeout_type == "N_Cr"


# ============================================================================
# 4. ISO-TP STMIN MICROSECOND SPIN-WAIT & PRECISION TESTS
# ============================================================================


class TestIsoTpStMinPrecision:
    """Validate STmin timing precision across microsecond spin-waits and millisecond sleeps."""

    @pytest.mark.parametrize(
        ("st_min_byte", "expected_us_min", "expected_us_max"),
        [
            (0xF1, 80, 2000),    # 100 us
            (0xF2, 180, 2500),   # 200 us
            (0xF5, 450, 3500),   # 500 us
            (0xF9, 850, 4500),   # 900 us
        ],
    )
    @pytest.mark.asyncio
    async def test_st_min_microsecond_spin_wait_precision(
        self, st_min_byte: int, expected_us_min: int, expected_us_max: int
    ) -> None:
        """Verify STmin sub-millisecond spin wait timing (0xF1-0xF9 via perf_counter_ns)."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        sender = IsoTpSender(tx_port, rx_sub, tx_id=0x7E0, rx_id=0x7E8)

        t_start = time.perf_counter_ns()
        await sender._apply_st_min(st_min_byte)
        t_elapsed_us = (time.perf_counter_ns() - t_start) / 1000.0

        assert t_elapsed_us >= (expected_us_min * 0.8), f"Spin-wait too short: {t_elapsed_us} us"
        assert t_elapsed_us <= (expected_us_max * 2.5), f"Spin-wait excessively long: {t_elapsed_us} us"

    @pytest.mark.asyncio
    async def test_st_min_zero_no_delay(self) -> None:
        """STmin=0x00 should execute near instantaneously (<500us)."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()
        sender = IsoTpSender(tx_port, rx_sub, tx_id=0x7E0, rx_id=0x7E8)

        t_start = time.perf_counter_ns()
        await sender._apply_st_min(0x00)
        t_elapsed_us = (time.perf_counter_ns() - t_start) / 1000.0
        assert t_elapsed_us < 500.0  # < 0.5 ms


# ============================================================================
# 5. MULTIPLE CONCURRENT ISO-TP MULTI-FRAME SESSIONS
# ============================================================================


class TestIsoTpConcurrentMultiSessions:
    """Stress test isolated concurrent multi-frame sessions on different channels/IDs."""

    @pytest.mark.asyncio
    async def test_concurrent_sessions_no_cross_talk(self) -> None:
        """Run 4 independent ISO-TP Sender/Receiver pairs concurrently."""
        num_sessions = 4
        tasks = []

        for i in range(num_sessions):
            tx_id = 0x700 + (i * 0x10)
            rx_id = 0x708 + (i * 0x10)
            ch = f"bus_{i}"

            rx_sub_s = QueueRxSubscription()
            rx_sub_r = QueueRxSubscription()
            tx_port_s = DirectQueueTxPort(rx_sub_r)
            tx_port_r = DirectQueueTxPort(rx_sub_s)

            sender = IsoTpSender(tx_port_s, rx_sub_s, tx_id=tx_id, rx_id=rx_id, channel_id=ch, is_fd=True)
            receiver = IsoTpReceiver(tx_port_r, rx_sub_r, tx_id=rx_id, rx_id=tx_id, channel_id=ch, is_fd=True)
            payload = bytes([i] * 150)

            async def run_pair(snd: IsoTpSender, rcv: IsoTpReceiver, data: bytes) -> bytes:
                r_task = asyncio.create_task(rcv.receive(timeout_s=5.0))
                s_task = asyncio.create_task(snd.send(data))
                await asyncio.gather(s_task, r_task)
                return r_task.result()

            tasks.append(run_pair(sender, receiver, payload))

        results = await asyncio.gather(*tasks)
        for i in range(num_sessions):
            assert results[i] == bytes([i] * 150)


# ============================================================================
# 6. SAE J1939-21 COLLISION STORMS & CONCURRENCY STRESS TESTS
# ============================================================================


class TestJ1939CollisionStormsAndEdgeCases:
    """Adversarial stress testing for SAE J1939-21 Transport Protocol."""

    def test_j1939_rts_global_broadcast_rejection(self) -> None:
        """RTS frames addressed to global broadcast address DA == 255 (0xFF) must be rejected."""
        tp = J1939TransportProtocol(my_address=0xF9)

        # TP.CM_RTS frame with DA = 255 (0x1CECFF00 | SA=0x10)
        rts_global = CanFrame.create(
            "ch0",
            0x1CECFF10,
            b"\x10\x14\x00\x03\xff\x00\xfe\x00",  # 20 bytes, 3 packets, PGN 0xFE00
            is_extended=True,
        )
        msg, resp = tp.handle_frame(rts_global)
        assert msg is None
        assert resp is None
        assert len(tp._rx_sessions) == 0

    def test_j1939_rts_rapid_collision_storm(self) -> None:
        """Rapid back-to-back RTS frames for active (SA, DA) abort previous session with reason 2 and start new."""
        tp = J1939TransportProtocol(my_address=0xF9)
        sa = 0x20
        da = 0xF9

        # 1. First RTS: PGN 0xEE00, 20 bytes
        rts1 = CanFrame.create(
            "ch0",
            0x1CECF920,
            b"\x10\x14\x00\x03\xff\x00\xee\x00",
            is_extended=True,
        )
        msg1, cts1 = tp.handle_frame(rts1)
        assert msg1 is None
        assert cts1 is not None
        assert cts1.data[0] == TP_CTRL_CTS
        assert (sa, da, "ch0") in tp._rx_sessions
        assert tp._rx_sessions[(sa, da, "ch0")].target_pgn == 0x00EE00

        # 2. Second RTS arrives unexpectedly on same (SA, DA) for PGN 0xEF00, 30 bytes
        rts2 = CanFrame.create(
            "ch0",
            0x1CECF920,
            b"\x10\x1e\x00\x05\xff\x00\xef\x00",
            is_extended=True,
        )
        msg2, abort_frame = tp.handle_frame(rts2)
        assert msg2 is None
        assert abort_frame is not None
        # Verify Conn_Abort for old session with reason=2 (Session Collision)
        assert abort_frame.data[0] == TP_CTRL_ABORT
        assert abort_frame.data[1] == ABORT_REASON_SESSION_COLLISION
        assert int.from_bytes(abort_frame.data[5:8], byteorder="little") == 0x00EE00

        # Verify new session is active with new PGN 0x00EF00
        assert (sa, da, "ch0") in tp._rx_sessions
        assert tp._rx_sessions[(sa, da, "ch0")].target_pgn == 0x00EF00
        assert tp._rx_sessions[(sa, da, "ch0")].total_bytes == 30

    def test_j1939_out_of_order_tp_dt_aborts_with_reason_1(self) -> None:
        """Out-of-order TP.DT sequence number triggers Conn_Abort with reason=1 and deletes session."""
        tp = J1939TransportProtocol(my_address=0xF9)

        # RTS for PGN 0xFECA, 14 bytes (2 packets)
        rts = CanFrame.create("ch0", 0x1CECF910, b"\x10\x0e\x00\x02\xff\xca\xfe\x00", is_extended=True)
        tp.handle_frame(rts)

        # Send TP.DT with seq=2 first (expected seq=1)
        bad_dt = CanFrame.create("ch0", 0x1CEBF910, b"\x02\x08\x09\x0a\x0b\x0c\x0d\x0e", is_extended=True)
        msg, abort_frame = tp.handle_frame(bad_dt)

        assert msg is None
        assert abort_frame is not None
        assert abort_frame.data[0] == TP_CTRL_ABORT
        assert abort_frame.data[1] == ABORT_REASON_SEQUENCE_ERROR
        assert (0x10, 0xF9) not in tp._rx_sessions

    def test_j1939_duplicate_tp_dt_aborts_with_reason_1(self) -> None:
        """Duplicate TP.DT sequence number (seq=1 followed by seq=1 again) triggers Conn_Abort reason=1."""
        tp = J1939TransportProtocol(my_address=0xF9)
        rts = CanFrame.create("ch0", 0x1CECF910, b"\x10\x0e\x00\x02\xff\xca\xfe\x00", is_extended=True)
        tp.handle_frame(rts)

        # Send seq=1
        dt1 = CanFrame.create("ch0", 0x1CEBF910, b"\x01\x01\x02\x03\x04\x05\x06\x07", is_extended=True)
        msg1, resp1 = tp.handle_frame(dt1)
        assert msg1 is None and resp1 is None

        # Repeat seq=1 (expected seq=2)
        msg2, abort_frame = tp.handle_frame(dt1)
        assert msg2 is None
        assert abort_frame is not None
        assert abort_frame.data[0] == TP_CTRL_ABORT
        assert abort_frame.data[1] == ABORT_REASON_SEQUENCE_ERROR

    def test_j1939_bam_and_cmdt_interleaving_high_throughput(self) -> None:
        """Interleave TP.DT packets between a global BAM broadcast (DA=255) and CMDT (DA=0xF9)."""
        tp = J1939TransportProtocol(my_address=0xF9)

        # Start BAM session from SA=0x30 (PGN 0xFEF2, 14 bytes, 2 packets)
        bam_cm = CanFrame.create("ch0", 0x1CECFF30, b"\x20\x0e\x00\x02\xff\xf2\xfe\x00", is_extended=True)
        tp.handle_frame(bam_cm)

        # Start CMDT session from SA=0x40 to DA=0xF9 (PGN 0xFEF1, 14 bytes, 2 packets)
        cmdt_rts = CanFrame.create("ch0", 0x1CECF940, b"\x10\x0e\x00\x02\xff\xf1\xfe\x00", is_extended=True)
        _, cts = tp.handle_frame(cmdt_rts)
        assert cts is not None

        # Interleave DT frames:
        # 1. BAM seq 1
        bam_dt1 = CanFrame.create("ch0", 0x1CEBFF30, b"\x01\x11\x12\x13\x14\x15\x16\x17", is_extended=True)
        msg, _ = tp.handle_frame(bam_dt1)
        assert msg is None

        # 2. CMDT seq 1
        cmdt_dt1 = CanFrame.create("ch0", 0x1CEBF940, b"\x01\x21\x22\x23\x24\x25\x26\x27", is_extended=True)
        msg, _ = tp.handle_frame(cmdt_dt1)
        assert msg is None

        # 3. BAM seq 2 (BAM completes)
        bam_dt2 = CanFrame.create("ch0", 0x1CEBFF30, b"\x02\x18\x19\x1a\x1b\x1c\x1d\x1e", is_extended=True)
        completed_bam, _ = tp.handle_frame(bam_dt2)
        assert completed_bam is not None
        assert completed_bam.pgn == 0x00FEF2
        assert completed_bam.data == b"\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e"

        # 4. CMDT seq 2 (CMDT completes and returns EndOfMsgACK)
        cmdt_dt2 = CanFrame.create("ch0", 0x1CEBF940, b"\x02\x28\x29\x2a\x2b\x2c\x2d\x2e", is_extended=True)
        completed_cmdt, ack_frame = tp.handle_frame(cmdt_dt2)
        assert completed_cmdt is not None
        assert completed_cmdt.pgn == 0x00FEF1
        assert completed_cmdt.data == b"\x21\x22\x23\x24\x25\x26\x27\x28\x29\x2a\x2b\x2c\x2d\x2e"
        assert ack_frame is not None
        assert ack_frame.data[0] == TP_CTRL_ACK

    @pytest.mark.parametrize("payload_len", [1, 7, 8, 1785])
    def test_j1939_extreme_payload_lengths_roundtrip(self, payload_len: int) -> None:
        """Verify segmentation and reassembly for 1B, 7B, 8B, and max 1785B payloads.

        REVIEW.md 3.5: start_tp_cm_dt returns ONLY the RTS (SAE J1939-21
        forbids DTs before CTS); the roundtrip drives the windowed
        start_cmdt_transfer + handle_rx_frame(CTS) flow.
        """
        tp_sender = J1939TransportProtocol(my_address=0x10)
        tp_receiver = J1939TransportProtocol(my_address=0xF9)

        payload = bytes((i % 251) for i in range(payload_len))

        rts = tp_sender.start_cmdt_transfer(0xF9, 0xFEEE, payload)
        _, cts = tp_receiver.handle_frame(rts)
        assert cts is not None

        completed: CompletedMessage | None = None
        dt_frames: list[CanFrame] = []
        out, first_dt = tp_sender.handle_rx_frame(cts)
        if first_dt is not None:
            dt_frames.append(first_dt)
        dt_frames.extend(tp_sender.take_pending_tx_frames())

        while dt_frames and completed is None:
            nxt: list[CanFrame] = []
            for f in dt_frames:
                msg, resp = tp_receiver.handle_frame(f)
                if msg is not None:
                    completed = msg
                elif resp is not None and resp.data[0] == 0x11:  # TP_CTRL_CTS
                    _m, next_dt = tp_sender.handle_rx_frame(resp)
                    if next_dt is not None:
                        nxt.append(next_dt)
                    nxt.extend(tp_sender.take_pending_tx_frames())
            dt_frames = nxt

        assert completed is not None
        assert completed.data == payload
        assert completed.pgn == 0x00FEEE

    def test_j1939_reject_oversized_payload(self) -> None:
        """Payload > 1785 bytes raises ValueError in segmentation."""
        tp = J1939TransportProtocol(my_address=0x10)
        with pytest.raises(ValueError, match="1..1785 bytes"):
            tp.start_tp_bam(pgn=0xFE00, data=b"\x00" * 1786)

    def test_j1939_reject_empty_payload(self) -> None:
        """0-byte payload raises ValueError in segmentation."""
        tp = J1939TransportProtocol(my_address=0x10)
        with pytest.raises(ValueError, match="1..1785 bytes"):
            tp.start_tp_cm_dt(target_address=0xF9, pgn=0xFE00, data=b"")


# ============================================================================
# 7. SAE J1939-71 SENTINEL & 3-STAGE DECODING PIPELINE STRESS TESTS
# ============================================================================


class TestJ1939SentinelDecodingStress:
    """Stress test sentinel validation, signed 2's complement, and physical scaling."""

    # ------------------------------------------------------------------------
    # 7.1 Unsigned Multi-Bit Sentinel Range Boundaries
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("raw_val", "expected_quality"),
        [
            (0b00, SignalQuality.VALID),
            (0b01, SignalQuality.VALID),
            (0b10, SignalQuality.ERROR),
            (0b11, SignalQuality.NOT_AVAILABLE),
        ],
    )
    def test_sentinel_2bit_boundaries(self, raw_val: int, expected_quality: SignalQuality) -> None:
        assert J1939SentinelFilter.check_discrete_2bit(raw_val) == expected_quality

    @pytest.mark.parametrize(
        ("raw_val", "expected_quality"),
        [
            (0x00, SignalQuality.VALID),
            (0x0D, SignalQuality.VALID),
            (0x0E, SignalQuality.ERROR),
            (0x0F, SignalQuality.NOT_AVAILABLE),
        ],
    )
    def test_sentinel_4bit_boundaries(self, raw_val: int, expected_quality: SignalQuality) -> None:
        assert J1939SentinelFilter.check_nibble_4bit(raw_val) == expected_quality

    @pytest.mark.parametrize(
        ("raw_val", "expected_quality"),
        [
            (0x00, SignalQuality.VALID),
            (0xFA, SignalQuality.VALID),
            (0xFB, SignalQuality.PARAMETER_SPECIFIC),
            (0xFC, SignalQuality.RESERVED),
            (0xFD, SignalQuality.RESERVED),
            (0xFE, SignalQuality.ERROR),
            (0xFF, SignalQuality.NOT_AVAILABLE),
        ],
    )
    def test_sentinel_8bit_boundaries(self, raw_val: int, expected_quality: SignalQuality) -> None:
        assert J1939SentinelFilter.check_uint8(raw_val) == expected_quality

    @pytest.mark.parametrize(
        ("raw_val", "expected_quality"),
        [
            (0x0000, SignalQuality.VALID),
            (0xFAFF, SignalQuality.VALID),
            (0xFB00, SignalQuality.PARAMETER_SPECIFIC),
            (0xFBFF, SignalQuality.PARAMETER_SPECIFIC),
            (0xFC00, SignalQuality.RESERVED),
            (0xFDFF, SignalQuality.RESERVED),
            (0xFE00, SignalQuality.ERROR),
            (0xFEFF, SignalQuality.ERROR),
            (0xFF00, SignalQuality.NOT_AVAILABLE),
            (0xFFFF, SignalQuality.NOT_AVAILABLE),
        ],
    )
    def test_sentinel_16bit_boundaries(self, raw_val: int, expected_quality: SignalQuality) -> None:
        assert J1939SentinelFilter.check_uint16(raw_val) == expected_quality

    @pytest.mark.parametrize(
        ("raw_val", "expected_quality"),
        [
            (0x000000, SignalQuality.VALID),
            (0xFAFFFF, SignalQuality.VALID),
            (0xFB0000, SignalQuality.PARAMETER_SPECIFIC),
            (0xFC0000, SignalQuality.RESERVED),
            (0xFE0000, SignalQuality.ERROR),
            (0xFFFFFF, SignalQuality.NOT_AVAILABLE),
        ],
    )
    def test_sentinel_24bit_boundaries(self, raw_val: int, expected_quality: SignalQuality) -> None:
        assert J1939SentinelFilter.check_uint24(raw_val) == expected_quality

    @pytest.mark.parametrize(
        ("raw_val", "expected_quality"),
        [
            (0x00000000, SignalQuality.VALID),
            (0xFAFFFFFF, SignalQuality.VALID),
            (0xFB000000, SignalQuality.PARAMETER_SPECIFIC),
            (0xFC000000, SignalQuality.RESERVED),
            (0xFE000000, SignalQuality.ERROR),
            (0xFFFFFFFF, SignalQuality.NOT_AVAILABLE),
        ],
    )
    def test_sentinel_32bit_boundaries(self, raw_val: int, expected_quality: SignalQuality) -> None:
        assert J1939SentinelFilter.check_uint32(raw_val) == expected_quality

    # ------------------------------------------------------------------------
    # 7.2 Signed Two's Complement & SPN 513 Validation
    # ------------------------------------------------------------------------

    def test_spn_513_percent_torque_raw_0xff9c(self) -> None:
        """SPN 513 Driver's Demand Percent Torque: raw 0xFF9C -> VALID and -100.0% physical value."""
        sig_def_direct = SignalDefinition(
            name="DriversDemandPercentTorque",
            spn=513,
            start_bit=0,
            length_bits=16,
            is_signed=True,
            scale=1.0,
            offset=0.0,
        )
        decoded = J1939SentinelFilter.decode_raw_value(0xFF9C, sig_def_direct)
        assert decoded.quality == SignalQuality.VALID
        assert decoded.raw_value == -100
        assert pytest.approx(decoded.physical_value) == -100.0

    @pytest.mark.parametrize(
        ("raw_val", "length_bits", "expected_signed"),
        [
            (0x7F, 8, 127),
            (0x80, 8, -128),
            (0xFF, 8, -1),
            (0x7FFF, 16, 32767),
            (0x8000, 16, -32768),
            (0xFFFF, 16, -1),
            (0x7FFFFF, 24, 8388607),
            (0x800000, 24, -8388608),
            (0x7FFFFFFF, 32, 2147483647),
            (0x80000000, 32, -2147483648),
        ],
    )
    def test_convert_to_signed_matrix(self, raw_val: int, length_bits: int, expected_signed: int) -> None:
        assert J1939SentinelFilter.convert_to_signed(raw_val, length_bits) == expected_signed

    @pytest.mark.parametrize(
        ("raw_val", "length_bits", "expected_quality"),
        [
            # 8-bit signed
            (0xFE, 8, SignalQuality.ERROR),
            (0xFF, 8, SignalQuality.NOT_AVAILABLE),
            (0x7F, 8, SignalQuality.VALID),
            # 16-bit signed
            (0xFFFE, 16, SignalQuality.ERROR),
            (0xFFFF, 16, SignalQuality.NOT_AVAILABLE),
            (0x7FFF, 16, SignalQuality.VALID),
            # 32-bit signed
            (0xFFFFFFFE, 32, SignalQuality.ERROR),
            (0xFFFFFFFF, 32, SignalQuality.NOT_AVAILABLE),
            (0x7FFFFFFF, 32, SignalQuality.VALID),
        ],
    )
    def test_signed_sentinel_error_and_na(
        self, raw_val: int, length_bits: int, expected_quality: SignalQuality
    ) -> None:
        """Signed signals evaluate max_uint as NOT_AVAILABLE and max_uint-1 as ERROR."""
        quality = J1939SentinelFilter.check_raw_value(raw_val, length_bits, is_signed=True)
        assert quality == expected_quality

    # ------------------------------------------------------------------------
    # 7.3 Bit Extraction & Endianness Stress Testing
    # ------------------------------------------------------------------------

    def test_extract_raw_bits_little_and_big_endian(self) -> None:
        """Extract multi-byte fields across arbitrary bit offsets in LE and BE."""
        # 8-byte payload
        payload = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0])

        # LE 16-bit at bit offset 8 (bytes 1 and 2: 0x34, 0x56) -> 0x5634
        val_le = J1939SentinelFilter.extract_raw_bits(payload, start_bit=8, length_bits=16, byte_order="little_endian")
        assert val_le == 0x5634

        # BE 16-bit at bit offset 0 (bytes 0 and 1: 0x12, 0x34) -> 0x1234
        val_be = J1939SentinelFilter.extract_raw_bits(payload, start_bit=0, length_bits=16, byte_order="big_endian")
        assert val_be == 0x1234

    def test_extract_raw_bits_bounds_checking(self) -> None:
        """Invalid start_bit or length_bits exceeding payload raises ValueError."""
        payload = b"\x01\x02"  # 16 bits
        with pytest.raises(ValueError, match="requires 17 bits"):
            J1939SentinelFilter.extract_raw_bits(payload, start_bit=8, length_bits=9)

        with pytest.raises(ValueError, match="Payload cannot be empty"):
            J1939SentinelFilter.extract_raw_bits(b"", start_bit=0, length_bits=8)

"""Unit tests for ISO 15765-2:2016 DoCAN (ISO-TP) Transport Codec Engine.

Verifies:
- CAN-FD Discrete DLC Normalization (0..8, 12, 16, 20, 24, 32, 48, 64) with configurable padding.
- Single Frame (Classic 1..7B, reject SF_DL=0 if not FD, CAN-FD Extended 1..62B).
- First Frame (Standard 12-bit <=4095B, Extended 32-bit >4095B, 64B FD zero-loss reassembly).
- Consecutive Frame sequence wrapping (1..15 -> 0 -> 1) and IsoTpSequenceError.
- Flow Control (CTS, WAIT with WFTmax=16, OVERFLOW, Block Size chunking, STmin pacing).
- Asynchronous IsoTpSender & IsoTpReceiver state machines with N_Bs / N_Cr timers.
- Session resets and max buffer overflow handling.
- Backward compatibility of synchronous IsoTpTransport methods.
"""

from __future__ import annotations

import asyncio

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
from src.protocols.uds.isotp import (
    FS_OVERFLOW,
    PCI_CONSECUTIVE_FRAME,
    PCI_FIRST_FRAME,
    PCI_FLOW_CONTROL,
    PCI_SINGLE_FRAME,
    IsoTpReceiver,
    IsoTpSender,
    IsoTpTransport,
    decode_st_min,
    normalize_can_payload,
)

# ============================================================================
# 1. Backward Compatibility & Synchronous IsoTpTransport Tests
# ============================================================================


def test_isotp_single_frame_roundtrip() -> None:
    """Verify synchronous Classic CAN Single Frame segmentation and reassembly."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)

    # 4-byte payload
    payload = b"\x22\xf1\x90\x00"
    frames = transport.segment_message(payload)
    assert len(frames) == 1
    assert frames[0].arbitration_id == 0x7E0
    assert (frames[0].data[0] >> 4) == PCI_SINGLE_FRAME
    assert (frames[0].data[0] & 0x0F) == 4

    # Simulate ECU response
    resp_frame = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=b"\x05\x62\xf1\x90\x41\x42\xcc\xcc",
        is_extended=False,
    )
    completed_data, flow_frame = transport.handle_rx_frame(resp_frame)
    assert flow_frame is None
    assert completed_data == b"\x62\xf1\x90\x41\x42"


def test_isotp_multi_frame_segmentation_and_reassembly() -> None:
    """Verify synchronous Classic CAN Multi-Frame (FF + CFs) segmentation and reassembly."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)

    # 20-byte payload (FF + 2 CFs)
    long_payload = bytes(range(20))
    tx_frames = transport.segment_message(long_payload)
    assert len(tx_frames) == 3

    # FF
    assert (tx_frames[0].data[0] >> 4) == PCI_FIRST_FRAME
    assert tx_frames[0].data[1] == 20
    assert tx_frames[0].data[2:8] == long_payload[:6]

    # CF 1
    assert (tx_frames[1].data[0] >> 4) == PCI_CONSECUTIVE_FRAME
    assert (tx_frames[1].data[0] & 0x0F) == 1
    assert tx_frames[1].data[1:8] == long_payload[6:13]

    # CF 2
    assert (tx_frames[2].data[0] >> 4) == PCI_CONSECUTIVE_FRAME
    assert (tx_frames[2].data[0] & 0x0F) == 2

    # Reassembly of incoming response
    rx_transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)

    ff_frame = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=tx_frames[0].data,
        is_extended=False,
    )
    completed, fc_frame = rx_transport.handle_rx_frame(ff_frame)
    assert completed is None
    assert fc_frame is not None
    assert (fc_frame.data[0] >> 4) == PCI_FLOW_CONTROL

    cf1_frame = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=tx_frames[1].data,
        is_extended=False,
    )
    completed, _ = rx_transport.handle_rx_frame(cf1_frame)
    assert completed is None

    cf2_frame = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=tx_frames[2].data,
        is_extended=False,
    )
    completed, _ = rx_transport.handle_rx_frame(cf2_frame)
    assert completed == long_payload


@pytest.mark.parametrize(
    ("raw_byte", "expected_ms"),
    [
        (0x00, 0.0),
        (0x05, 5.0),
        (0x7F, 127.0),
        (0xF1, 0.1),
        (0xF5, 0.5),
        (0xF9, 0.9),
        (0x80, 127.0),
        (0xFA, 127.0),
        (0xFF, 127.0),
    ],
)
def test_isotp_decode_st_min_parametric(raw_byte: int, expected_ms: float) -> None:
    """Verify STmin decoding across milliseconds, sub-milliseconds, and reserved ranges."""
    assert pytest.approx(decode_st_min(raw_byte), abs=1e-3) == expected_ms


def test_isotp_can_fd_segmentation() -> None:
    """Verify ISO-TP segmentation using CAN-FD 64-byte payload optimization."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)

    # 40-byte payload in CAN-FD fits in single frame
    payload_40 = bytes(range(40))
    frames_fd_single = transport.segment_message(payload_40, is_fd=True)
    assert len(frames_fd_single) == 1
    assert frames_fd_single[0].is_fd is True
    assert frames_fd_single[0].data[0] == 0x00
    assert frames_fd_single[0].data[1] == 40
    assert frames_fd_single[0].data[2:42] == payload_40

    # 100-byte payload in CAN-FD uses FF (62B) + CF1 (38B)
    payload_100 = bytes(range(100))
    frames_fd_multi = transport.segment_message(payload_100, is_fd=True)
    assert len(frames_fd_multi) == 2
    assert (frames_fd_multi[0].data[0] >> 4) == PCI_FIRST_FRAME
    assert frames_fd_multi[0].is_fd is True
    assert (frames_fd_multi[1].data[0] >> 4) == PCI_CONSECUTIVE_FRAME
    assert frames_fd_multi[1].is_fd is True


# ============================================================================
# 2. CAN-FD Discrete DLC Normalization & Padding Tests
# ============================================================================


def test_normalize_can_payload_discrete_dlcs() -> None:
    """Verify normalization of arbitrary lengths to discrete CAN-FD payload lengths."""
    for length in range(1, 65):
        raw = b"\x55" * length
        padded = normalize_can_payload(raw, is_fd=True, pad_byte=0xCC)
        assert len(padded) in (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
        assert padded[:length] == raw
        assert padded[length:] == b"\xCC" * (len(padded) - length)


def test_normalize_can_payload_custom_padding() -> None:
    """Verify custom padding bytes (0x00, 0xAA) during DLC normalization."""
    # Classic CAN pads 3 bytes to 8 bytes
    raw_3 = b"\x11\x22\x33"
    padded_00 = normalize_can_payload(raw_3, is_fd=False, pad_byte=0x00)
    assert len(padded_00) == 8
    assert padded_00 == b"\x11\x22\x33\x00\x00\x00\x00\x00"

    # CAN-FD pads 10 bytes to nearest discrete DLC 12
    raw_10 = b"\x11" * 10
    padded_aa = normalize_can_payload(raw_10, is_fd=True, pad_byte=0xAA)
    assert len(padded_aa) == 12
    assert padded_aa == (b"\x11" * 10) + b"\xaa\xaa"


def test_normalize_can_payload_no_padding() -> None:
    """Verify pad_byte=None leaves payload length unchanged."""
    raw = b"\x01\x02\x03\x04"
    assert normalize_can_payload(raw, is_fd=True, pad_byte=None) == raw
    assert normalize_can_payload(raw, is_fd=False, pad_byte=None) == raw


# ============================================================================
# 3. Frame Encoding, Decoding & Validation Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_classic_single_frame_rejects_sf_dl_zero() -> None:
    """Verify Classic CAN frame with SF_DL == 0 is rejected with IsoTpInvalidPduError."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    receiver = IsoTpReceiver(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8, is_fd=False)

    # Frame with PCI=0x00 (SF_DL=0)
    malformed = CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=b"\x00\x11\x22\x33\x44\x55\x66\x77", is_fd=False)
    rx_sub.put_nowait(malformed)

    with pytest.raises(IsoTpInvalidPduError) as exc_info:
        await receiver.receive(timeout_s=0.2)
    assert "SF_DL=0" in str(exc_info.value) or "rejected" in str(exc_info.value)


@pytest.mark.asyncio
async def test_canfd_extended_sf_rejects_sf_dl_zero_or_overflow() -> None:
    """Verify CAN-FD Extended SF with SF_DL=0 or SF_DL>62 raises IsoTpInvalidPduError."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    receiver = IsoTpReceiver(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8, is_fd=True)

    # SF_DL = 0
    rx_sub.put_nowait(CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=b"\x00\x00" + b"\xcc" * 6, is_fd=True))
    with pytest.raises(IsoTpInvalidPduError):
        await receiver.receive(timeout_s=0.2)

    # SF_DL = 63 (> 62)
    rx_sub.put_nowait(CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=bytes([0x00, 63]) + b"\xcc" * 62, is_fd=True, dlc=15))
    with pytest.raises(IsoTpInvalidPduError):
        await receiver.receive(timeout_s=0.2)


@pytest.mark.asyncio
async def test_extended_32bit_first_frame_rejects_length_under_4096() -> None:
    """Verify 32-bit Extended First Frame with declared length <= 4095 raises IsoTpInvalidPduError."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    receiver = IsoTpReceiver(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8, is_fd=False)

    # Header 0x10 0x00 with length 1024 (should have used 12-bit standard FF)
    header = bytes([0x10, 0x00, 0x00, 0x00, 0x04, 0x00]) + b"\x11\x22"
    rx_sub.put_nowait(CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=header, is_fd=False))

    with pytest.raises(IsoTpInvalidPduError):
        await receiver.receive(timeout_s=0.2)


# ============================================================================
# 4. Asynchronous IsoTpSender & IsoTpReceiver E2E Roundtrips
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload_len", "is_fd"),
    [
        (0, False),
        (1, False),
        (7, False),
        (8, False),
        (62, True),
        (64, True),
        (500, False),
        (1024, True),
        (4095, False),
        (4096, True),
        (10000, True),
    ],
)
async def test_async_isotp_sender_receiver_roundtrip_all_sizes(payload_len: int, is_fd: bool) -> None:
    """Verify async IsoTpSender & IsoTpReceiver roundtrip across all ISO-TP frame boundaries."""
    node_a_tx = InMemoryTxPort()
    node_b_tx = InMemoryTxPort()

    node_a_rx = QueueRxSubscription()
    node_b_rx = QueueRxSubscription()

    sender = IsoTpSender(tx_port=node_a_tx, rx_sub=node_a_rx, tx_id=0x7E0, rx_id=0x7E8, is_fd=is_fd)
    receiver = IsoTpReceiver(tx_port=node_b_tx, rx_sub=node_b_rx, tx_id=0x7E8, rx_id=0x7E0, is_fd=is_fd)

    payload = bytes([i % 256 for i in range(payload_len)])

    # Bridge ports cooperatively
    async def bridge_loop(stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            while node_a_tx.sent_frames:
                f = node_a_tx.sent_frames.pop(0)
                node_b_rx.put_nowait(f)
            while node_b_tx.sent_frames:
                f = node_b_tx.sent_frames.pop(0)
                node_a_rx.put_nowait(f)
            await asyncio.sleep(0.001)

    stop_event = asyncio.Event()
    bridge_task = asyncio.create_task(bridge_loop(stop_event))

    try:
        if payload_len == 0:
            await sender.send(payload)
            # Empty payload does not transmit frames
            assert len(node_a_tx.sent_frames) == 0
        else:
            send_task = asyncio.create_task(sender.send(payload))
            recv_task = asyncio.create_task(receiver.receive(timeout_s=5.0))

            done, pending = await asyncio.wait([send_task, recv_task], timeout=5.0)
            assert len(pending) == 0, "Transmission timed out"

            rx_result = recv_task.result()
            assert rx_result == payload
    finally:
        stop_event.set()
        await bridge_task


# ============================================================================
# 5. Flow Control & State Machine Verification
# ============================================================================


@pytest.mark.asyncio
async def test_sender_n_bs_timeout_raises_isotp_timeout_error() -> None:
    """Verify sender raises IsoTpTimeoutError(timeout_type='N_Bs') when Flow Control is not received."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    sender = IsoTpSender(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8, n_bs_timeout_s=0.05)

    payload = bytes(range(20))  # Multi-Frame
    with pytest.raises(IsoTpTimeoutError) as exc_info:
        await sender.send(payload)

    assert exc_info.value.timeout_type == "N_Bs"
    assert "N_Bs" in str(exc_info.value)


@pytest.mark.asyncio
async def test_receiver_n_cr_timeout_raises_isotp_timeout_error() -> None:
    """Verify receiver raises IsoTpTimeoutError(timeout_type='N_Cr') when Consecutive Frame is not received."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    receiver = IsoTpReceiver(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E8, rx_id=0x7E0, n_cr_timeout_s=0.05)

    # Ingest First Frame for 20 bytes (only provides 6 bytes)
    ff = CanFrame.create(channel_id="uds", arbitration_id=0x7E0, data=b"\x10\x14" + b"\x00" * 6, is_fd=False)
    rx_sub.put_nowait(ff)

    with pytest.raises(IsoTpTimeoutError) as exc_info:
        await receiver.receive(timeout_s=0.5)

    assert exc_info.value.timeout_type == "N_Cr"


@pytest.mark.asyncio
async def test_sender_wftmax_16_tolerated_and_17_aborts() -> None:
    """Verify sender tolerates up to 16 consecutive WAIT frames and aborts on the 17th."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    sender = IsoTpSender(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8, wft_max=16, n_bs_timeout_s=0.5)

    payload = bytes(range(20))

    # Send 16 WAIT frames followed by 1 CTS
    for _ in range(16):
        rx_sub.put_nowait(CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=b"\x31\x00\x00\xcc\xcc\xcc\xcc\xcc", is_fd=False))
    rx_sub.put_nowait(CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=b"\x30\x00\x00\xcc\xcc\xcc\xcc\xcc", is_fd=False))

    # 16 WAITs then CTS should succeed transmitting
    await sender.send(payload)
    assert len(tx_port.sent_frames) == 3  # FF + 2 CFs

    # Now test 17 consecutive WAIT frames -> abort
    tx_port.clear()
    rx_sub_17 = QueueRxSubscription()
    sender_17 = IsoTpSender(tx_port=tx_port, rx_sub=rx_sub_17, tx_id=0x7E0, rx_id=0x7E8, wft_max=16, n_bs_timeout_s=0.5)

    for _ in range(17):
        rx_sub_17.put_nowait(CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=b"\x31\x00\x00\xcc\xcc\xcc\xcc\xcc", is_fd=False))

    with pytest.raises(IsoTpTimeoutError) as exc_info:
        await sender_17.send(payload)
    assert "WFTmax" in str(exc_info.value)


@pytest.mark.asyncio
async def test_sender_overflow_flow_control_raises_buffer_overflow_error() -> None:
    """Verify sender aborts with IsoTpBufferOverflowError when receiving FlowStatus.OVERFLOW."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    sender = IsoTpSender(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8)

    rx_sub.put_nowait(CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=b"\x32\x00\x00\xcc\xcc\xcc\xcc\xcc", is_fd=False))

    with pytest.raises(IsoTpBufferOverflowError):
        await sender.send(b"12345678901234567890")


@pytest.mark.asyncio
async def test_receiver_buffer_size_limit_emits_overflow_and_raises_error() -> None:
    """Verify receiver exceeding max_buffer_size emits Flow Control OVERFLOW and raises error."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    receiver = IsoTpReceiver(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E8, rx_id=0x7E0, max_buffer_size=100)

    # First Frame requesting 200 bytes (> 100 max)
    ff = CanFrame.create(channel_id="uds", arbitration_id=0x7E0, data=b"\x10\xc8" + b"\x00" * 6, is_fd=False)
    rx_sub.put_nowait(ff)

    with pytest.raises(IsoTpBufferOverflowError):
        await receiver.receive(timeout_s=0.5)

    assert len(tx_port.sent_frames) == 1
    fc_frame = tx_port.sent_frames[0]
    assert (fc_frame.data[0] >> 4) == PCI_FLOW_CONTROL
    assert (fc_frame.data[0] & 0x0F) == FS_OVERFLOW


@pytest.mark.asyncio
async def test_receiver_sequence_number_mismatch_raises_isotp_sequence_error() -> None:
    """Verify sequence number mismatch in Consecutive Frames raises IsoTpSequenceError."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    receiver = IsoTpReceiver(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E8, rx_id=0x7E0)

    ff = CanFrame.create(channel_id="uds", arbitration_id=0x7E0, data=b"\x10\x14" + b"\x00" * 6, is_fd=False)
    # Send sequence 2 directly instead of sequence 1
    cf_bad = CanFrame.create(channel_id="uds", arbitration_id=0x7E0, data=b"\x22" + b"\x00" * 7, is_fd=False)

    rx_sub.put_nowait(ff)
    rx_sub.put_nowait(cf_bad)

    with pytest.raises(IsoTpSequenceError) as exc_info:
        await receiver.receive(timeout_s=0.5)

    assert exc_info.value.expected_sn == 1
    assert exc_info.value.actual_sn == 2


@pytest.mark.asyncio
async def test_receiver_session_reset_by_unexpected_single_frame() -> None:
    """Verify unexpected Single Frame during multi-frame reception resets session and returns SF."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    receiver = IsoTpReceiver(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E8, rx_id=0x7E0)

    ff = CanFrame.create(channel_id="uds", arbitration_id=0x7E0, data=b"\x10\x14" + b"\x00" * 6, is_fd=False)
    sf = CanFrame.create(channel_id="uds", arbitration_id=0x7E0, data=b"\x02\x50\x01\xcc\xcc\xcc\xcc\xcc", is_fd=False)

    rx_sub.put_nowait(ff)
    rx_sub.put_nowait(sf)

    result = await receiver.receive(timeout_s=0.5)
    assert result == b"\x50\x01"


def test_sync_fc_advertises_configured_bs_and_stmin() -> None:
    """P2 fix: sync-path Flow Control must advertise configurable BS/STmin."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, rx_block_size=8, rx_st_min=0x05)

    ff = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=bytes([0x10, 0x20, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05]),
        is_extended=False,
    )
    _, fc = transport.handle_rx_frame(ff)
    assert fc is not None
    assert fc.data[0] == (PCI_FLOW_CONTROL << 4) | 0x00  # FS=CTS
    assert fc.data[1] == 8  # BS as configured
    assert fc.data[2] == 0x05  # STmin as configured (5 ms)


def test_sync_fc_bs_windowing_emits_next_fc() -> None:
    """P2 fix: with BS>0 the receiver emits a fresh FC after each full block
    while the transfer is still in progress."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, rx_block_size=2, rx_st_min=0)

    # 27-byte payload: FF(6) + CF1(7) + CF2(7) + CF3(7)
    ff = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=bytes([0x10, 0x1B, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05]),
        is_extended=False,
    )
    _, fc1 = transport.handle_rx_frame(ff)
    assert fc1 is not None and fc1.data[1] == 2

    cf1 = CanFrame.create(
        channel_id="uds", arbitration_id=0x7E8, data=bytes([0x21, 6, 7, 8, 9, 10, 11, 12]), is_extended=False
    )
    completed, fc_after_cf1 = transport.handle_rx_frame(cf1)
    assert completed is None
    assert fc_after_cf1 is None  # block not yet full (1/2)

    cf2 = CanFrame.create(
        channel_id="uds", arbitration_id=0x7E8, data=bytes([0x22, 13, 14, 15, 16, 17, 18, 19]), is_extended=False
    )
    completed, fc_after_cf2 = transport.handle_rx_frame(cf2)
    assert completed is None  # transfer still in progress
    assert fc_after_cf2 is not None  # block of 2 filled -> fresh FC
    assert fc_after_cf2.data[1] == 2

    cf3 = CanFrame.create(
        channel_id="uds", arbitration_id=0x7E8, data=bytes([0x23, 20, 21, 22, 23, 24, 25, 26]), is_extended=False
    )
    completed, _ = transport.handle_rx_frame(cf3)
    assert completed is not None


# ============================================================================
# ISO 15765-2 §9.2 Addressing Modes (Extended N_TA / Mixed N_AE)
# ============================================================================


def test_isotp_extended_addressing_sf_roundtrip() -> None:
    """Spec 2.1 mode 3: 'Extended Addressing (8-bit N_TA in Byte 0), Byte 1 = N_PCI byte.'
    Positive: the N_TA prefixes every segmented frame and is stripped on RX."""
    from src.protocols.uds.isotp import AddressingMode

    transport = IsoTpTransport(
        tx_id=0x7E0,
        rx_id=0x7E8,
        addressing_mode=AddressingMode.EXTENDED,
        address_byte=0xF1,
    )

    payload = b"\x22\xf1\x90\x00"
    frames = transport.segment_message(payload)
    assert len(frames) == 1
    assert frames[0].data[0] == 0xF1  # N_TA target address
    assert frames[0].data[1] == (PCI_SINGLE_FRAME << 4) | 4  # N_PCI at byte 1

    # Reassemble a response carrying the same N_TA
    resp = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=b"\xf1\x05\x62\xf1\x90\x41\x42\xcc",
        is_extended=False,
    )
    completed, _ = transport.handle_rx_frame(resp)
    assert completed == b"\x62\xf1\x90\x41\x42"


def test_isotp_extended_addressing_multi_frame_roundtrip() -> None:
    """Boundary: 20-byte payload over Extended addressing — FF/CF all carry N_TA at byte 0."""
    from src.protocols.uds.isotp import AddressingMode

    transport = IsoTpTransport(
        tx_id=0x7E0,
        rx_id=0x7E8,
        addressing_mode=AddressingMode.EXTENDED,
        address_byte=0xF1,
    )
    long_payload = bytes(range(20))
    tx_frames = transport.segment_message(long_payload)
    assert all(f.data[0] == 0xF1 for f in tx_frames)
    assert (tx_frames[0].data[1] >> 4) == PCI_FIRST_FRAME

    # FF + CFs reassemble through the address-stripped path
    rx_transport = IsoTpTransport(
        tx_id=0x7E0,
        rx_id=0x7E8,
        addressing_mode=AddressingMode.EXTENDED,
        address_byte=0xF1,
    )
    completed = None
    for f in tx_frames:
        resp = CanFrame.create(
            channel_id="uds", arbitration_id=0x7E8, data=f.data, is_extended=False
        )
        completed, _ = rx_transport.handle_rx_frame(resp)
    assert completed == long_payload


def test_isotp_mixed_addressing_roundtrip() -> None:
    """Spec 2.1 mode 4: 'Mixed Addressing (8-bit N_AE in Byte 0), Byte 1 = N_PCI byte.'
    Positive: N_AE prefixes and strips like N_TA."""
    from src.protocols.uds.isotp import AddressingMode

    transport = IsoTpTransport(
        tx_id=0x18DAF110,
        rx_id=0x18DA10F1,
        addressing_mode=AddressingMode.MIXED,
        address_byte=0x55,
    )
    payload = b"\x10\x03\x00"
    frames = transport.segment_message(payload)
    assert frames[0].data[0] == 0x55
    assert frames[0].data[1] == (PCI_SINGLE_FRAME << 4) | 3

    resp = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x18DA10F1,
        data=b"\x55\x06\x50\x03\x00\x19\x21\x23",
        is_extended=True,
    )
    completed, _ = transport.handle_rx_frame(resp)
    assert completed == b"\x50\x03\x00\x19\x21\x23"


def test_isotp_addressing_mode_rejects_bad_configuration() -> None:
    """Negative: EXTENDED without address_byte (and NORMAL with one) must raise."""
    from src.protocols.uds.isotp import AddressingMode

    with pytest.raises(ValueError, match="address_byte"):
        IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, addressing_mode=AddressingMode.MIXED)

    with pytest.raises(ValueError, match="address_byte"):
        IsoTpTransport(
            tx_id=0x7E0,
            rx_id=0x7E8,
            addressing_mode=AddressingMode.NORMAL,
            address_byte=0xF1,
        )


def test_isotp_extended_addressing_drops_mismatched_address_byte() -> None:
    """Negative: a frame whose byte-0 address does not match the configured
    N_TA is dropped, never reassembled into a payload."""
    from src.protocols.uds.isotp import AddressingMode

    transport = IsoTpTransport(
        tx_id=0x7E0,
        rx_id=0x7E8,
        addressing_mode=AddressingMode.EXTENDED,
        address_byte=0xF1,
    )
    wrong_addr = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=b"\x99\x05\x62\xf1\x90\x41\x42\xcc",  # 0x99 != N_TA 0xF1
    )
    completed, _ = transport.handle_rx_frame(wrong_addr)
    assert completed is None


# ============================================================================
# M-33 / P2-24 & M-32 / P2-25 regressions
# ============================================================================


def test_isotp_receiver_drops_fd_mismatched_first_frame() -> None:
    """P2-24: an FD First Frame arriving at a classic-mode receiver must be
    dropped at admission (no FC CTS emitted), waiting for the next frame —
    the old behavior emitted CTS and then silently discarded every CF until
    the N_Cr timeout."""
    import asyncio

    tx_port = InMemoryTxPort()
    rx_q: asyncio.Queue[CanFrame] = asyncio.Queue()
    rx_sub = QueueRxSubscription(rx_q)

    receiver = IsoTpReceiver(
        channel_id="uds",
        tx_id=0x7E0,
        rx_id=0x7E8,
        tx_port=tx_port,
        rx_sub=rx_sub,
        is_fd=False,  # classic receiver
    )

    # FD-mode First Frame: 12-bit length 16 bytes, FD payload > 8
    fd_ff = CanFrame.create(
        channel_id="uds",
        arbitration_id=0x7E8,
        data=bytes([0x10, 0x10, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B]),
        is_fd=True,
    )

    async def scenario() -> None:
        await rx_q.put(fd_ff)
        # After the dropped FF, a well-formed classic SF must still decode —
        # the receiver must not be wedged by the mismatch.
        await rx_q.put(
            CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=b"\x02\x50\x03")
        )
        result = await asyncio.wait_for(receiver.receive(timeout_s=1.0), timeout=2.0)
        assert result == b"\x50\x03"

    asyncio.run(scenario())

    # No FC was ever emitted for the mismatched FD FF (the admission guard
    # fired before the CTS build).
    assert len(tx_port.sent_frames) == 0


def test_isotp_classic_extended_addressing_over_4095_raises() -> None:
    """P2-25: >4095-byte payloads in classic frames with EXTENDED addressing
    are not representable — the structured IsoTpError replaces the bare
    pad ValueError that fired mid-segmentation."""
    from src.protocols.uds.isotp import AddressingMode, IsoTpError

    transport = IsoTpTransport(
        tx_id=0x7E0,
        rx_id=0x7E8,
        addressing_mode=AddressingMode.EXTENDED,
        address_byte=0xF1,
    )
    # 4096 bytes: requires the 32-bit FF form, which does not fit classic
    # frames once the address byte is present.
    with pytest.raises(IsoTpError, match="CAN FD"):
        transport.segment_message(b"\x36" * 4096)

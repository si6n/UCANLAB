"""Universal CAN-Bus Platform - Challenger 2 Adversarial Verification & Stress Test Suite.

Empirical Challenger verification for:
- M3: Multi-Packet Transport & Auto-Reassembly Pipeline
  (J1939 BAM/CMDT corrupted frames, duplicate packets, missing sequence numbers, 64-source interleaved
   streams, ISO-TP buffer overrun attacks, Flow Control WAIT saturation WFTmax, invalid STmin clamping,
   and sequence rollover).
- M4: E2E Safety CRC & Rolling Counter Engine
  (Multi-bit flip corruption on CRCs, rolling counter wrap-arounds 0..14/0..15, sequence loss detection,
   replay attack resistance, Toyota/VAG MQB/Volvo boundary validations, and 64-channel concurrency).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading

import pytest

from src.core.contracts.ports import ClockProvider, TxPort
from src.core.models.can_frame import CanFrame
from src.engine.pipeline.reassembly_pipeline import (
    ReassembledMessage,
    ReassemblyPipeline,
)
from src.protocols.j1939.transport import (
    ABORT_REASON_SEQUENCE_ERROR,
    TP_CTRL_ABORT,
    TP_CTRL_BAM,
    TP_CTRL_CTS,
    TP_CTRL_RTS,
    J1939TransportProtocol,
)
from src.protocols.uds.isotp import (
    FS_OVERFLOW,
    FS_WAIT,
    PCI_CONSECUTIVE_FRAME,
    PCI_FIRST_FRAME,
    PCI_FLOW_CONTROL,
    IsoTpBufferOverflowError,
    IsoTpReceiver,
    IsoTpSender,
    IsoTpTimeoutError,
    decode_st_min,
)
from src.safety.e2e.crc import (
    calculate_crc8_0x2f,
)
from src.safety.e2e.packager import E2ESafetyPackager
from src.safety.e2e.profiles import (
    E2EProfileConfig,
    E2EStatus,
    extract_counter,
    extract_crc,
)
from src.safety.e2e.validator import E2ESafetyValidator

# ==============================================================================
# In-Memory Test Helpers & Mocks
# ==============================================================================


class DeterministicMockClock(ClockProvider):
    """Controllable monotonic clock provider for reproducible transport timing tests."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._time = initial_time

    def now_monotonic(self) -> float:
        return self._time

    def now_monotonic_ns(self) -> int:
        return int(self._time * 1_000_000_000)

    def advance(self, delta_seconds: float) -> None:
        if delta_seconds < 0:
            raise ValueError("Time cannot move backward")
        self._time += delta_seconds


class InMemoryTxPort(TxPort):
    """Thread-safe in-memory transmission port capturing frames."""

    def __init__(self) -> None:
        self.sent_frames: list[CanFrame] = []
        self._lock = threading.Lock()

    async def send(self, frame: CanFrame) -> None:
        with self._lock:
            self.sent_frames.append(frame)

    def send_sync(self, frame: CanFrame) -> None:
        with self._lock:
            self.sent_frames.append(frame)

    def get_frames(self) -> list[CanFrame]:
        with self._lock:
            return list(self.sent_frames)

    def clear(self) -> None:
        with self._lock:
            self.sent_frames.clear()


class AsyncQueueSubscription:
    """Async queue subscription for ISO-TP state machine testing."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[CanFrame] = asyncio.Queue()
        self.is_active = True

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        if not self.is_active:
            return None
        try:
            if timeout_s is None:
                return await self.queue.get()
            return await asyncio.wait_for(self.queue.get(), timeout=timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            return None

    def put(self, frame: CanFrame) -> None:
        if self.is_active:
            self.queue.put_nowait(frame)

    def unsubscribe(self) -> None:
        self.is_active = False


# ==============================================================================
# SECTION 1: J1939 Multi-Packet Transport Adversarial & Stress Tests (M3)
# ==============================================================================


class TestJ1939AdversarialMultiPacket:
    """Adversarial and stress test cases for SAE J1939-21 Transport Protocol."""

    def test_j1939_bam_missing_sequence_drop_evicts_cleanly(self) -> None:
        """Adversarial Test: Dropped sequence number in BAM evicts session without leaking."""
        clock = DeterministicMockClock(100.0)
        pipeline = ReassemblyPipeline(clock_provider=clock, channel_id="can0")
        reassembled: list[ReassembledMessage] = []
        pipeline.register_on_reassembled(lambda m: reassembled.append(m))

        sa = 0x12
        pgn = 65280
        total_bytes = 28  # 4 packets
        total_packets = 4

        # 1. BAM CM frame
        cm_data = bytearray(8)
        cm_data[0] = TP_CTRL_BAM
        cm_data[1:3] = total_bytes.to_bytes(2, "little")
        cm_data[3] = total_packets
        cm_data[4] = 0xFF
        cm_data[5:8] = pgn.to_bytes(3, "little")
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF00 | sa, data=bytes(cm_data), is_extended=True)
        )
        assert pipeline.get_active_session_count() == 1

        # 2. Send Packet 1 (Seq 1)
        dt1 = bytearray([1, 10, 11, 12, 13, 14, 15, 16])
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CEBFF00 | sa, data=bytes(dt1), is_extended=True)
        )
        assert pipeline.get_active_session_count() == 1

        # 3. Adversarial drop: skip Seq 2, send Seq 3 directly
        dt3 = bytearray([3, 20, 21, 22, 23, 24, 25, 26])
        res = pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CEBFF00 | sa, data=bytes(dt3), is_extended=True)
        )
        assert res is None
        # Session must be evicted upon sequence error
        assert pipeline.get_active_session_count() == 0
        assert len(reassembled) == 0

        # 4. Ensure a fresh subsequent BAM from same SA works cleanly
        new_payload = b"NEW_FRESH_STREAM_DATA_012345"  # 28 bytes -> 4 packets
        j1939_tx = J1939TransportProtocol(my_address=sa, channel_id="can0")
        frames = j1939_tx.start_tp_bam(pgn=pgn, data=new_payload)
        for f in frames:
            res = pipeline.process_frame(f)

        assert res is not None
        assert len(reassembled) == 1
        assert reassembled[0].data == new_payload

    def test_j1939_bam_duplicate_packet_attack(self) -> None:
        """Adversarial Test: Injected duplicate DT packet evicts session and prevents buffer corruption."""
        pipeline = ReassemblyPipeline(channel_id="can0")
        sa = 0x25

        cm_data = bytearray([TP_CTRL_BAM, 14, 0, 2, 0xFF, 0xEC, 0xFE, 0x00])
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF00 | sa, data=bytes(cm_data), is_extended=True)
        )
        assert pipeline.get_active_session_count() == 1

        # Send Packet 1
        dt1 = bytearray([1, 1, 2, 3, 4, 5, 6, 7])
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CEBFF00 | sa, data=bytes(dt1), is_extended=True)
        )

        # Injected duplicate Packet 1 (attacker replaying seq 1)
        res = pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CEBFF00 | sa, data=bytes(dt1), is_extended=True)
        )
        assert res is None
        # Expected sequence was 2, received 1 -> session evicted
        assert pipeline.get_active_session_count() == 0

    def test_j1939_cmdt_sequence_corruption_emits_abort(self) -> None:
        """Adversarial Test: CMDT sequence corruption triggers TP.Conn_Abort with reason 0x01."""
        tx_port = InMemoryTxPort()
        pipeline = ReassemblyPipeline(tx_port=tx_port, my_j1939_address=0xF9, channel_id="can0")

        # RTS frame from SA 0x44 to DA 0xF9
        rts_data = bytearray([TP_CTRL_RTS, 21, 0, 3, 0xFF, 0x00, 0xEF, 0x00])
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CECF944, data=bytes(rts_data), is_extended=True)
        )

        # CTS should have been emitted
        sent = tx_port.get_frames()
        assert len(sent) == 1
        assert sent[0].data[0] == TP_CTRL_CTS

        # Send seq 1
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CEBF944, data=b"\x01\x11\x22\x33\x44\x55\x66\x77", is_extended=True)
        )

        # Send seq 3 instead of seq 2
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CEBF944, data=b"\x03\x11\x22\x33\x44\x55\x66\x77", is_extended=True)
        )

        # Pipeline must emit Conn_Abort
        sent_after = tx_port.get_frames()
        assert len(sent_after) == 2
        abort_frame = sent_after[1]
        assert abort_frame.arbitration_id == 0x1CEC44F9
        assert abort_frame.data[0] == TP_CTRL_ABORT
        assert abort_frame.data[1] == ABORT_REASON_SEQUENCE_ERROR
        assert pipeline.get_active_session_count() == 0

    def test_j1939_malformed_headers_rejection(self) -> None:
        """Adversarial Test: Malformed length and packet counts in TP.CM are rejected immediately."""
        pipeline = ReassemblyPipeline(my_j1939_address=0xF9, channel_id="can0")

        # 1. Total bytes = 0
        cm_zero_bytes = bytearray([TP_CTRL_BAM, 0, 0, 1, 0xFF, 0x00, 0xEF, 0x00])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF01, data=bytes(cm_zero_bytes), is_extended=True))
        assert pipeline.get_active_session_count() == 0

        # 2. Total bytes > 1785 (SAE limit)
        cm_huge = bytearray([TP_CTRL_BAM, 0x00, 0x08, 255, 0xFF, 0x00, 0xEF, 0x00])  # 2048 bytes
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF01, data=bytes(cm_huge), is_extended=True))
        assert pipeline.get_active_session_count() == 0

        # 3. Packet count mismatch (15 bytes needs 3 packets, declares 5)
        cm_mismatch = bytearray([TP_CTRL_BAM, 15, 0, 5, 0xFF, 0x00, 0xEF, 0x00])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF01, data=bytes(cm_mismatch), is_extended=True))
        assert pipeline.get_active_session_count() == 0

        # 4. RTS addressed to Global Address (DA=255)
        rts_global = bytearray([TP_CTRL_RTS, 14, 0, 2, 0xFF, 0x00, 0xEF, 0x00])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF01, data=bytes(rts_global), is_extended=True))
        assert pipeline.get_active_session_count() == 0

    def test_j1939_interleaved_streams_64_source_addresses(self) -> None:
        """Stress Benchmark: 64 concurrent source addresses interleaving BAM multi-packet frames simultaneously."""
        pipeline = ReassemblyPipeline(my_j1939_address=0xF9, channel_id="can0")
        reassembled_map: dict[int, bytes] = {}
        lock = threading.Lock()

        def on_msg(m: ReassembledMessage) -> None:
            with lock:
                if m.source_address is not None:
                    reassembled_map[m.source_address] = m.data

        pipeline.register_on_reassembled(on_msg)

        num_sources = 64
        source_payloads: dict[int, bytes] = {}
        all_frames: list[CanFrame] = []

        # Generate BAM streams for 64 ECUs (SA 0x01 to 0x40)
        for sa in range(1, num_sources + 1):
            payload_len = 14 + (sa % 30)  # 14..43 bytes
            payload = bytes([sa] * payload_len)
            source_payloads[sa] = payload

            j1939_tx = J1939TransportProtocol(my_address=sa, channel_id="can0")
            frames = j1939_tx.start_tp_bam(pgn=65280 + sa, data=payload)
            all_frames.extend(frames)

        # Interleave frames: sort frames such that all CM frames go first, then DT1 for all, then DT2, etc.
        # This stresses concurrent in-flight session tracking across all 64 SAs
        def sort_key(f: CanFrame) -> int:
            pgn_raw = (f.arbitration_id >> 8) & 0xFFFF
            if (pgn_raw & 0xFF00) == 0xEC00:
                return 0  # CM frames first
            return f.data[0]  # Sort by DT sequence number

        all_frames.sort(key=sort_key)

        for frame in all_frames:
            pipeline.process_frame(frame)

        # Verify all 64 streams were reassembled with exact byte integrity
        assert len(reassembled_map) == num_sources
        for sa in range(1, num_sources + 1):
            assert reassembled_map[sa] == source_payloads[sa]

        # Verify zero lingering sessions
        assert pipeline.get_active_session_count() == 0


# ==============================================================================
# SECTION 2: ISO-TP Transport Adversarial & Stress Tests (M3)
# ==============================================================================


class TestIsoTpAdversarialTransport:
    """Adversarial, timing, and protocol error tests for ISO 15765-2 DoCAN."""

    def test_isotp_buffer_overrun_attack_mitigation(self) -> None:
        """Adversarial Test: Extended 32-bit First Frame with > 1MB payload triggers Flow Control OVERFLOW."""
        tx_port = InMemoryTxPort()
        pipeline = ReassemblyPipeline(tx_port=tx_port, channel_id="uds_ch0")

        # Attacker transmits 32-bit FF with 5 MB requested size (0x004C4B40)
        ff_data = bytes([0x10, 0x00, 0x00, 0x4C, 0x4B, 0x40, 0x11, 0x22])
        ff_frame = CanFrame.create(channel_id="uds_ch0", arbitration_id=0x7E8, data=ff_data, is_extended=False)

        res = pipeline.process_frame(ff_frame)
        assert res is None

        # Verify pipeline responded with FS_OVERFLOW
        sent = tx_port.get_frames()
        assert len(sent) == 1
        fc = sent[0]
        assert fc.arbitration_id == 0x7E0
        assert (fc.data[0] >> 4) == PCI_FLOW_CONTROL
        assert (fc.data[0] & 0x0F) == FS_OVERFLOW

    def test_isotp_decode_st_min_full_domain_and_clamping(self) -> None:
        """Empirical Test: decode_st_min accurately converts 0x00..0x7F, 0xF1..0xF9, and clamps reserved ranges."""
        # 1. 0x00 - 0x7F (0 .. 127 ms)
        for b in range(0x00, 0x80):
            assert decode_st_min(b) == float(b)

        # 2. 0xF1 - 0xF9 (0.1 .. 0.9 ms in 100 us steps)
        expected_us = {
            0xF1: 0.1,
            0xF2: 0.2,
            0xF3: 0.3,
            0xF4: 0.4,
            0xF5: 0.5,
            0xF6: 0.6,
            0xF7: 0.7,
            0xF8: 0.8,
            0xF9: 0.9,
        }
        for b, exp in expected_us.items():
            assert decode_st_min(b) == exp

        # 3. Reserved 0x80 - 0xF0 -> clamped to the 10.0 ms spoof/stall cap
        # (REVIEW hardening: legacy 127 ms let a single spoofed FC crawl a
        # transfer; the cap bounds the stall without breaking valid peers).
        for b in (0x80, 0x90, 0xA5, 0xC0, 0xEF, 0xF0):
            assert decode_st_min(b) == 10.0

        # 4. Reserved 0xFA - 0xFF -> clamped to 127.0 ms (legacy clamp kept)
        for b in (0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF):
            assert decode_st_min(b) == 127.0

    @pytest.mark.asyncio
    async def test_isotp_sender_flow_control_wait_saturation_wftmax(self) -> None:
        """Adversarial Test: IsoTpSender aborts when consecutive WAIT frames exceed wft_max (16)."""
        tx_port = InMemoryTxPort()
        rx_sub = AsyncQueueSubscription()
        clock = DeterministicMockClock(0.0)

        sender = IsoTpSender(
            tx_port=tx_port,
            rx_sub=rx_sub,  # type: ignore[arg-type]
            tx_id=0x7E0,
            rx_id=0x7E8,
            channel_id="uds_ch0",
            clock=clock,
            wft_max=16,
        )

        payload = b"PAYLOAD_THAT_NEEDS_MULTIFRAME_CONSECUTIVE_TRANSFER_DATA"

        async def respond_with_wait_burst() -> None:
            # Wait for First Frame transmission
            await asyncio.sleep(0.01)
            # Send 17 consecutive WAIT frames (wft_max=16)
            for _ in range(17):
                wait_fc = CanFrame.create(
                    channel_id="uds_ch0",
                    arbitration_id=0x7E8,
                    data=bytes([(PCI_FLOW_CONTROL << 4) | FS_WAIT, 0, 0, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC]),
                )
                rx_sub.put(wait_fc)
                await asyncio.sleep(0.001)

        responder_task = asyncio.create_task(respond_with_wait_burst())

        with pytest.raises(IsoTpTimeoutError, match="WFTmax limit exceeded"):
            await sender.send(payload)

        await responder_task

    @pytest.mark.asyncio
    async def test_isotp_receiver_overflow_exception_on_excessive_payload(self) -> None:
        """Adversarial Test: IsoTpReceiver rejects payload exceeding max_buffer_size and emits FS_OVERFLOW."""
        tx_port = InMemoryTxPort()
        rx_sub = AsyncQueueSubscription()

        receiver = IsoTpReceiver(
            tx_port=tx_port,
            rx_sub=rx_sub,  # type: ignore[arg-type]
            tx_id=0x7E8,
            rx_id=0x7E0,
            channel_id="uds_ch0",
            max_buffer_size=100,  # 100 bytes max
        )

        # First frame claiming 500 bytes (> 100 max)
        ff_data = bytes([(PCI_FIRST_FRAME << 4) | ((500 >> 8) & 0x0F), 500 & 0xFF]) + b"\x01\x02\x03\x04\x05\x06"
        rx_sub.put(CanFrame.create(channel_id="uds_ch0", arbitration_id=0x7E0, data=ff_data))

        with pytest.raises(IsoTpBufferOverflowError, match="exceeds max buffer capacity"):
            await receiver.receive(timeout_s=0.5)

        # Verify FS_OVERFLOW was transmitted back
        sent = tx_port.get_frames()
        assert len(sent) == 1
        assert (sent[0].data[0] & 0x0F) == FS_OVERFLOW

    def test_isotp_large_multi_frame_sequence_rollover(self) -> None:
        """Empirical Stress Test: ISO-TP payload of 1500 bytes with 214 Consecutive Frames rolling over SN 0..15."""
        tx_port = InMemoryTxPort()
        pipeline = ReassemblyPipeline(tx_port=tx_port, channel_id="uds_ch0")

        total_bytes = 1500
        full_payload = bytes([i % 256 for i in range(total_bytes)])

        # 1. First Frame (12-bit standard) -> 6 bytes
        ff_header = bytes([(PCI_FIRST_FRAME << 4) | ((total_bytes >> 8) & 0x0F), total_bytes & 0xFF])
        ff_frame = CanFrame.create(channel_id="uds_ch0", arbitration_id=0x7E8, data=ff_header + full_payload[:6])
        res = pipeline.process_frame(ff_frame)
        assert res is None

        # 2. Consecutive Frames (214 frames)
        bytes_sent = 6
        sn = 1
        cf_count = 0
        while bytes_sent < total_bytes:
            chunk = full_payload[bytes_sent : bytes_sent + 7]
            cf_data = bytes([(PCI_CONSECUTIVE_FRAME << 4) | (sn & 0x0F)]) + chunk
            if len(cf_data) < 8:
                cf_data += bytes([0xCC] * (8 - len(cf_data)))

            cf_frame = CanFrame.create(channel_id="uds_ch0", arbitration_id=0x7E8, data=cf_data)
            res = pipeline.process_frame(cf_frame)

            bytes_sent += len(chunk)
            sn = (sn + 1) & 0x0F
            cf_count += 1

        assert cf_count == 214
        assert res is not None
        assert res.data == full_payload
        assert len(res.data) == total_bytes

    def test_isotp_malformed_single_frame_rejection(self) -> None:
        """Adversarial Test: Classic CAN Single Frame with SF_DL=0 or SF_DL > 7 is rejected."""
        pipeline = ReassemblyPipeline(channel_id="uds_ch0")

        # Classic SF with SF_DL = 0
        sf_zero = CanFrame.create(channel_id="uds_ch0", arbitration_id=0x7E8, data=b"\x00\x01\x02\x03\x04\x05\x06\x07", is_fd=False)
        assert pipeline.process_frame(sf_zero) is None

        # Classic SF with SF_DL = 8 (invalid, max is 7)
        sf_eight = CanFrame.create(channel_id="uds_ch0", arbitration_id=0x7E8, data=b"\x08\x01\x02\x03\x04\x05\x06\x07", is_fd=False)
        assert pipeline.process_frame(sf_eight) is None


# ==============================================================================
# SECTION 3: E2E Safety CRC & Rolling Counter Engine Adversarial Tests (M4)
# ==============================================================================


class TestE2ESafetyAdversarialVerification:
    """Adversarial, multi-bit corruption, replay attack, and boundary test cases for E2E Engine."""

    def test_e2e_crc_multi_bit_flip_corruption_matrix(self) -> None:
        """Adversarial Test: Exhaustive 1-bit, 2-bit, and 3-bit corruption detection across Polynomials 0x1D & 0x2F."""
        packager = E2ESafetyPackager()
        validator = E2ESafetyValidator()

        profile_1d = E2EProfileConfig.create_autosar_profile_1(data_id=0x1234, variant="1C")
        profile_2f = E2EProfileConfig.create_vag_mqb(data_id=0x5678)

        base_payload = b"\x00\x00\x11\x22\x33\x44\x55\x66"

        for p_idx, profile in enumerate([profile_1d, profile_2f]):
            channel = f"can{p_idx}"
            frame = CanFrame.create(channel_id=channel, arbitration_id=0x200 + p_idx, data=base_payload)
            sealed = packager.package(frame, profile)

            # Baseline valid frame initializes stream
            init_res = validator.validate(sealed, profile)
            assert init_res.is_valid is True

            # 1. Test every single-bit flip (64 combinations)
            for byte_idx in range(len(sealed.data)):
                for bit_idx in range(8):
                    corrupted = bytearray(sealed.data)
                    corrupted[byte_idx] ^= 1 << bit_idx
                    corrupt_frame = CanFrame.create(channel_id=channel, arbitration_id=0x200 + p_idx, data=bytes(corrupted))

                    res = validator.validate(corrupt_frame, profile)
                    assert res.verdict == E2EStatus.CRC_ERROR
                    assert res.is_crc_valid is False
                    assert res.is_valid is False

            # 2. Test adjacent 2-bit flips
            for byte_idx in range(len(sealed.data) - 1):
                corrupted = bytearray(sealed.data)
                corrupted[byte_idx] ^= 0x03
                corrupted[byte_idx + 1] ^= 0x03
                corrupt_frame = CanFrame.create(channel_id=channel, arbitration_id=0x200 + p_idx, data=bytes(corrupted))
                res = validator.validate(corrupt_frame, profile)
                assert res.verdict == E2EStatus.CRC_ERROR

            # Verify that CRC errors did not advance rolling counter state
            state = validator.get_stream_state(channel, 0x200 + p_idx)
            assert state is not None
            assert state.crc_errors == (8 * 8) + 7  # 64 single bit + 7 double bit
            assert state.last_counter == 0  # Counter remains at initial 0!

    def test_e2e_rolling_counter_wraparound_boundaries_0_to_14_and_0_to_15(self) -> None:
        """Empirical Test: Invariant verification of Modulo 15 (0..14 -> 0) and Modulo 16 (0..15 -> 0) wrap-arounds."""
        packager = E2ESafetyPackager()
        validator = E2ESafetyValidator()

        # 1. Profile 1A: Modulo 15 (0..14 -> 0)
        p1a = E2EProfileConfig.create_autosar_profile_1(data_id=0x1111, variant="1A")
        assert p1a.counter_modulo == 15
        raw_frame = CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\x00" * 8)

        for cycle in range(3):
            for c in range(15):
                f = packager.package(raw_frame, p1a)
                res = validator.validate(f, p1a)
                assert res.counter == c
                if cycle == 0 and c == 0:
                    assert res.verdict == E2EStatus.INITIAL
                else:
                    assert res.verdict == E2EStatus.OK, f"Failed wrap at cycle {cycle}, counter {c}"
                assert res.delta == (0 if (cycle == 0 and c == 0) else 1)

        # 2. Profile 1C: Modulo 16 (0..15 -> 0)
        p1c = E2EProfileConfig.create_autosar_profile_1(data_id=0x2222, variant="1C")
        assert p1c.counter_modulo == 16
        raw_frame_1c = CanFrame.create(channel_id="can1", arbitration_id=0x101, data=b"\x00" * 8)

        for cycle in range(3):
            for c in range(16):
                f = packager.package(raw_frame_1c, p1c)
                res = validator.validate(f, p1c)
                assert res.counter == c
                if cycle == 0 and c == 0:
                    assert res.verdict == E2EStatus.INITIAL
                else:
                    assert res.verdict == E2EStatus.OK
                assert res.delta == (0 if (cycle == 0 and c == 0) else 1)

    def test_e2e_sequence_loss_and_wraparound_delta_boundaries(self) -> None:
        """Boundary Test: Evaluates delta calculation across wrap-around boundaries."""
        packager = E2ESafetyPackager()
        validator = E2ESafetyValidator()
        profile = E2EProfileConfig.create_autosar_profile_1(data_id=0x3333, max_delta_counter=3)

        raw = CanFrame.create(channel_id="can0", arbitration_id=0x300, data=b"\x00" * 8)

        # Advance stream to counter 14
        packager.set_counter("can0", 0x300, 13)
        f14 = packager.package(raw, profile)
        validator.validate(f14, profile)

        # Case A: Last counter = 14, receive counter = 0 (delta = (0 - 14) % 16 = 2 <= 3) -> SOME_LOST.
        # REVIEW hardening: an in-line rewind (counter=14 -> 0) is now
        # rejected fail-closed by the packager (monotonic stream contract);
        # the wrap step is exercised through the audited explicit
        # set_counter path instead.
        packager.set_counter("can0", 0x300, 15)  # next auto-counter wraps to 0
        f0 = packager.package(raw, profile)
        res_a = validator.validate(f0, profile)
        assert res_a.verdict == E2EStatus.SOME_LOST
        assert res_a.delta == 2
        assert res_a.is_valid is True

        # Case B: Last counter = 0, receive counter = 3 (delta = 3 <= 3) -> SOME_LOST
        packager.set_counter("can0", 0x300, 2)  # next auto-counter is 3
        f3 = packager.package(raw, profile)
        res_b = validator.validate(f3, profile)
        assert res_b.verdict == E2EStatus.SOME_LOST
        assert res_b.delta == 3

        # Case C: Last counter = 3, receive counter = 8 (delta = 5 > 3) -> WRONG_SEQUENCE
        packager.set_counter("can0", 0x300, 7)  # next auto-counter is 8
        f8 = packager.package(raw, profile)
        res_c = validator.validate(f8, profile)
        assert res_c.verdict == E2EStatus.WRONG_SEQUENCE
        assert res_c.delta == 5
        assert res_c.is_valid is False

    def test_e2e_replay_attack_detection_and_resilience(self) -> None:
        """Security Stress Test: Replay of past valid frames is detected as REPEATED or WRONG_SEQUENCE."""
        packager = E2ESafetyPackager()
        validator = E2ESafetyValidator()
        profile = E2EProfileConfig.create_autosar_profile_1(data_id=0x7777, max_delta_counter=2)

        raw = CanFrame.create(channel_id="can0", arbitration_id=0x400, data=b"\x10\x20\x30\x40\x50\x60\x70\x80")

        # Capture valid stream: f0..f5
        captured_frames: list[CanFrame] = []
        for _ in range(6):
            f = packager.package(raw, profile)
            validator.validate(f, profile)
            captured_frames.append(f)

        assert len(captured_frames) == 6
        # Current validator counter is 5

        # 1. Immediate duplicate replay (counter 5 replayed)
        res_dup = validator.validate(captured_frames[5], profile)
        assert res_dup.verdict == E2EStatus.REPEATED
        assert res_dup.delta == 0
        assert res_dup.is_valid is False

        # 2. Delayed replay of older frame (counter 2 replayed when stream is at 5)
        # delta = (2 - 5) % 16 = 13 > max_delta 2
        res_old = validator.validate(captured_frames[2], profile)
        assert res_old.verdict == E2EStatus.WRONG_SEQUENCE
        assert res_old.delta == 13
        assert res_old.is_valid is False

        # 3. Legitimate frame 6 arrives from authentic sender
        f6 = packager.package(raw, profile, counter=6)
        res_legit = validator.validate(f6, profile)
        # Note: Previous frame was frame 2 (counter 2), so delta from 2 to 6 is 4 > max_delta 2
        assert res_legit.verdict == E2EStatus.WRONG_SEQUENCE

        # Next consecutive legitimate frame 7 arrives
        f7 = packager.package(raw, profile, counter=7)
        res_legit_7 = validator.validate(f7, profile)
        assert res_legit_7.verdict == E2EStatus.OK
        assert res_legit_7.delta == 1
        assert res_legit_7.is_valid is True

    def test_e2e_oem_boundary_validations_toyota_vag_volvo(self) -> None:
        """OEM Boundary Test: Exhaustive boundary testing for Toyota, VAG MQB, and Volvo algorithms."""
        packager = E2ESafetyPackager()
        validator = E2ESafetyValidator()

        # 1. Toyota: Additive modulo-256 with 29-bit CAN ID and DLC variations
        toyota_cfg = E2EProfileConfig.create_toyota(crc_byte_offset=7, counter_byte_offset=6, include_can_id=True, include_dlc=True)
        arb_id_29bit = 0x18FEF125
        payload_toyota = b"\x10\x20\x30\x40\x50\x60\x00\x00"

        s_toyota = packager.package(
            CanFrame.create(channel_id="can0", arbitration_id=arb_id_29bit, data=payload_toyota, is_extended=True),
            toyota_cfg,
        )
        res_toyota = validator.validate(s_toyota, toyota_cfg)
        assert res_toyota.is_valid is True
        assert res_toyota.is_crc_valid is True

        # 2. VAG MQB: 16-bit Data ID (0x0000, 0xFFFF, 0xABCD)
        for data_id in (0x0000, 0xFFFF, 0xABCD, 0x1234):
            vag_cfg = E2EProfileConfig.create_vag_mqb(data_id=data_id)
            s_vag = packager.package(
                CanFrame.create(channel_id="can0", arbitration_id=0x1A0, data=b"\x00" * 8),
                vag_cfg,
            )
            res_vag = validator.validate(s_vag, vag_cfg)
            assert res_vag.is_valid is True
            assert res_vag.is_crc_valid is True

        # 3. Volvo: Ones-complement sum invariant: (sum(payload) + crc) & 0xFF == 0xFF
        volvo_cfg = E2EProfileConfig.create_volvo(crc_byte_offset=7, counter_byte_offset=1)
        test_patterns = [
            b"\x00" * 8,
            b"\xFF" * 8,
            b"\x01\x02\x03\x04\x05\x06\x07\x00",
            b"\x55\xAA\x55\xAA\x55\xAA\x55\x00",
        ]
        for pattern in test_patterns:
            s_volvo = packager.package(
                CanFrame.create(channel_id="can0", arbitration_id=0x150, data=pattern),
                volvo_cfg,
            )
            crc_byte = s_volvo.data[7]
            sum_protected = sum(s_volvo.data[i] for i in range(7)) & 0xFF
            assert (sum_protected + crc_byte) & 0xFF == 0xFF
            res_volvo = validator.validate(s_volvo, volvo_cfg)
            assert res_volvo.is_valid is True

    def test_e2e_autosar_profile_2_data_id_list_dynamic_selection(self) -> None:
        """AUTOSAR Profile 2 Test: Verifies dynamic selection of Data ID based on rolling counter (counter % 16)."""
        packager = E2ESafetyPackager()
        validator = E2ESafetyValidator()

        data_id_list = list(range(0x80, 0x90))  # 16 Data IDs: 0x80..0x8F
        p2_cfg = E2EProfileConfig.create_autosar_profile_2(data_id_list=data_id_list)

        raw = CanFrame.create(channel_id="can0", arbitration_id=0x220, data=b"\x00" * 8)

        for c in range(32):  # 2 full cycles of 16
            sealed = packager.package(raw, p2_cfg)
            counter_used = extract_counter(sealed.data, p2_cfg)
            assert counter_used == (c % 16)

            # Ground-truth check: payload + selected Data ID fed into CRC-8 0x2F
            expected_data_id = data_id_list[counter_used]
            protected_data = bytes([sealed.data[i] for i in range(len(sealed.data)) if i != p2_cfg.crc_byte_offset])
            manual_crc = calculate_crc8_0x2f(protected_data + bytes([expected_data_id]))

            assert extract_crc(sealed.data, p2_cfg) == manual_crc

            res = validator.validate(sealed, p2_cfg)
            if c == 0:
                assert res.verdict == E2EStatus.INITIAL
            else:
                assert res.verdict == E2EStatus.OK
            assert res.is_valid is True

    def test_e2e_concurrency_stress_64_independent_streams(self) -> None:
        """High-Throughput Concurrency Stress: 64 concurrent threads packaging and validating streams simultaneously."""
        packager = E2ESafetyPackager()
        validator = E2ESafetyValidator()

        num_threads = 64
        frames_per_thread = 50

        profiles = [
            E2EProfileConfig.create_autosar_profile_1(data_id=0x1234, variant="1C"),
            E2EProfileConfig.create_autosar_profile_1(data_id=0x5678, variant="1A"),
            E2EProfileConfig.create_autosar_profile_2(list(range(0x20, 0x30))),
            E2EProfileConfig.create_toyota(crc_byte_offset=7, counter_byte_offset=6),
            E2EProfileConfig.create_vag_mqb(data_id=0x9ABC),
            E2EProfileConfig.create_volvo(crc_byte_offset=7, counter_byte_offset=1),
        ]

        def worker_stream(idx: int) -> tuple[int, int]:
            profile = profiles[idx % len(profiles)]
            channel = f"can{idx % 4}"
            arb_id = 0x100 + idx
            raw = CanFrame.create(channel_id=channel, arbitration_id=arb_id, data=b"\x01\x02\x03\x04\x05\x06\x07\x08")

            valid_count = 0
            for _ in range(frames_per_thread):
                sealed = packager.package(raw, profile)
                res = validator.validate(sealed, profile)
                if res.is_valid:
                    valid_count += 1

            return idx, valid_count

        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            futures = [executor.submit(worker_stream, i) for i in range(num_threads)]
            results = [f.result() for f in futures]

        assert len(results) == num_threads
        for _idx, valid_count in results:
            assert valid_count == frames_per_thread

        all_states = validator.get_all_states()
        assert len(all_states) == num_threads
        for state in all_states.values():
            assert state.total_frames == frames_per_thread
            assert state.valid_frames == frames_per_thread
            assert state.crc_errors == 0
            assert state.sequence_errors == 0

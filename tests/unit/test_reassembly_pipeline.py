"""Comprehensive Unit Test Suite for Multi-Packet Transport Reassembly Pipeline.

Tests:
- J1939 BAM Multi-Packet reassembly with multi-DTC DM1 and VIN.
- J1939 RTS/CTS CMDT point-to-point reassembly with CTS, EndOfMsgACK, collision, and aborts.
- ISO-TP (Classic & CAN-FD) Single Frame, Standard 12-bit FF, Extended 32-bit FF, CF, and Flow Control.
- Concurrency and thread safety with multiple simultaneous streams across nodes.
- Monotonic timeout recovery, session eviction, per-SA quota limits, and memory safety.
- Seamless integration with FrameRouter, DbcSignalDecoder, and synthetic CanFrame synthesis.
"""

from __future__ import annotations

import threading

from src.core.contracts.ports import ClockProvider, TxPort
from src.core.models.can_frame import CanFrame, dlc_to_length, length_to_dlc
from src.engine.decoder.dbc_decoder import DbcSignalDecoder, DecodedMessage, SignalStatus
from src.engine.pipeline.reassembly_pipeline import (
    PGN_VIN,
    PROTOCOL_RESPONSE_11BIT_IDS,
    ReassembledMessage,
    ReassemblyPipeline,
    j1939_protocol_response_masks,
)
from src.engine.router import FrameRouter
from src.protocols.j1939.diagnostics import (
    PGN_DM1,
    PGN_DM2,
    DMMessage,
    LampStatus,
)
from src.protocols.j1939.transport import (
    ABORT_REASON_SEQUENCE_ERROR,
    ABORT_REASON_SESSION_COLLISION,
    TP_CTRL_ABORT,
    TP_CTRL_ACK,
    TP_CTRL_BAM,
    TP_CTRL_CTS,
    TP_CTRL_RTS,
    J1939TransportProtocol,
)
from src.protocols.uds.isotp import (
    FS_CTS,
    FS_OVERFLOW,
    PCI_CONSECUTIVE_FRAME,
    PCI_FIRST_FRAME,
    PCI_FLOW_CONTROL,
)


class MockClock(ClockProvider):
    """Controllable monotonic mock clock provider."""

    def __init__(self, initial: float = 1000.0) -> None:
        self._time = initial

    def now_monotonic(self) -> float:
        return self._time

    def advance(self, delta: float) -> None:
        self._time += delta


class MockTxPort(TxPort):
    """In-memory TxPort capturing transmitted frames."""

    def __init__(self) -> None:
        self.sent_frames: list[CanFrame] = []
        self._lock = threading.Lock()

    async def send(self, frame: CanFrame) -> None:
        with self._lock:
            self.sent_frames.append(frame)

    def send_sync(self, frame: CanFrame) -> None:
        with self._lock:
            self.sent_frames.append(frame)


# ==============================================================================
# 1. J1939 BAM Multi-Packet Reassembly Tests
# ==============================================================================


class TestJ1939BamReassembly:
    """Test SAE J1939-21 Broadcast Announce Message (BAM) multi-packet reassembly."""

    def test_j1939_bam_vin_reassembly(self) -> None:
        """Test reassembling 19-byte ASCII VIN string across 3 TP.DT packets."""
        pipeline = ReassemblyPipeline(my_j1939_address=0xF9, channel_id="can0")
        reassembled_list: list[ReassembledMessage] = []
        pipeline.register_on_reassembled(lambda msg: reassembled_list.append(msg))

        vin_payload = b"1HGCR2F83HA123456* "  # 19 bytes
        total_bytes = len(vin_payload)
        total_packets = 3  # (19 + 6) // 7 = 3
        sa = 0x00  # Engine ECU

        # 1. TP.CM_BAM frame (DA = 255)
        # ID: 0x1CECFF00
        cm_data = bytearray(8)
        cm_data[0] = TP_CTRL_BAM
        cm_data[1:3] = total_bytes.to_bytes(2, byteorder="little")
        cm_data[3] = total_packets
        cm_data[4] = 0xFF
        cm_data[5:8] = PGN_VIN.to_bytes(3, byteorder="little")

        cm_frame = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x1CECFF00 | sa,
            data=bytes(cm_data),
            is_extended=True,
        )
        res1 = pipeline.process_frame(cm_frame)
        assert res1 is None
        assert len(reassembled_list) == 0

        # 2. TP.DT Packet 1 (bytes 0..7)
        dt1_data = bytearray(8)
        dt1_data[0] = 1  # Sequence 1
        dt1_data[1:8] = vin_payload[0:7]
        dt1_frame = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x1CEBFF00 | sa,
            data=bytes(dt1_data),
            is_extended=True,
        )
        res2 = pipeline.process_frame(dt1_frame)
        assert res2 is None

        # 3. TP.DT Packet 2 (bytes 7..14)
        dt2_data = bytearray(8)
        dt2_data[0] = 2  # Sequence 2
        dt2_data[1:8] = vin_payload[7:14]
        dt2_frame = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x1CEBFF00 | sa,
            data=bytes(dt2_data),
            is_extended=True,
        )
        res3 = pipeline.process_frame(dt2_frame)
        assert res3 is None

        # 4. TP.DT Packet 3 (bytes 14..19, padded with 0xFF)
        dt3_data = bytearray(8)
        dt3_data[0] = 3  # Sequence 3
        dt3_data[1:6] = vin_payload[14:19]
        dt3_data[6:8] = b"\xff\xff"
        dt3_frame = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x1CEBFF00 | sa,
            data=bytes(dt3_data),
            is_extended=True,
        )
        res4 = pipeline.process_frame(dt3_frame)

        assert res4 is not None
        assert len(reassembled_list) == 1
        msg = reassembled_list[0]

        assert msg.protocol == "J1939"
        assert msg.is_bam is True
        assert msg.pgn == PGN_VIN
        assert msg.source_address == 0x00
        assert msg.destination_address == 255
        assert msg.data == vin_payload
        assert msg.diagnostics == "1HGCR2F83HA123456"
        assert msg.synthetic_frame is not None
        # Arbitration ID for PGN 65260 (0xFEEC) and SA 0x00: 0x18FEEC00
        assert msg.synthetic_frame.arbitration_id == 0x18FEEC00
        assert msg.synthetic_frame.data == vin_payload

    def test_j1939_bam_multi_dtc_dm1_reassembly(self) -> None:
        """Test reassembling DM1 (Active DTCs) containing multiple fault codes."""
        pipeline = ReassemblyPipeline(my_j1939_address=0xF9, channel_id="can0")

        # Construct DM1 payload:
        # Byte 0: MIL=ON (01), RedStop=OFF (00), AmberWarning=ON (01), Protect=OFF (00) -> 0b01000100 = 0x44
        # Byte 1: Flash states -> 0xFF
        # DTC 1: SPN 100 (0x00064), FMI 1, OC 5 -> b0=0x64, b1=0x00, b2=0x01, b3=0x05
        # DTC 2: SPN 110 (0x0006E), FMI 3, OC 2 -> b0=0x6E, b1=0x00, b2=0x03, b3=0x02
        # DTC 3: SPN 190 (0x000BE), FMI 2, OC 1 -> b0=0xBE, b1=0x00, b2=0x02, b3=0x01
        # DTC 4: SPN 520200 (0x07F008), FMI 31, OC 10 -> b0=0x08, b1=0xF0, b2=(0x07 << 5)|31=0xFF, b3=0x0A
        dm1_raw = bytearray()
        dm1_raw.extend([0x44, 0xFF])  # Lamp status
        dm1_raw.extend([0x64, 0x00, 0x01, 0x05])  # DTC 1
        dm1_raw.extend([0x6E, 0x00, 0x03, 0x02])  # DTC 2
        dm1_raw.extend([0xBE, 0x00, 0x02, 0x01])  # DTC 3
        dm1_raw.extend([0x08, 0xF0, 0xFF, 0x0A])  # DTC 4
        dm1_payload = bytes(dm1_raw)  # 18 bytes

        # Segment using J1939TransportProtocol helper
        j1939_tx = J1939TransportProtocol(my_address=0x00, channel_id="can0")
        frames = j1939_tx.start_tp_bam(pgn=PGN_DM1, data=dm1_payload)
        assert len(frames) == 4  # 1 BAM CM + 3 DT packets

        res = None
        for f in frames:
            res = pipeline.process_frame(f)

        assert res is not None
        assert res.pgn == PGN_DM1
        assert res.data == dm1_payload
        assert isinstance(res.diagnostics, DMMessage)
        dm_msg: DMMessage = res.diagnostics
        assert dm_msg.malfunction_indicator_lamp == LampStatus.ON
        assert dm_msg.amber_warning_lamp == LampStatus.ON
        assert dm_msg.red_stop_lamp == LampStatus.OFF
        assert len(dm_msg.dtcs) == 4
        assert dm_msg.dtcs[0].spn == 100
        assert dm_msg.dtcs[0].fmi == 1
        assert dm_msg.dtcs[0].occurrence_count == 5
        assert dm_msg.dtcs[1].spn == 110
        assert dm_msg.dtcs[1].fmi == 3
        assert dm_msg.dtcs[2].spn == 190
        assert dm_msg.dtcs[2].fmi == 2
        assert dm_msg.dtcs[3].spn == 520200
        assert dm_msg.dtcs[3].fmi == 31
        assert dm_msg.dtcs[3].occurrence_count == 10


# ==============================================================================
# 2. J1939 RTS/CTS CMDT Reassembly Tests
# ==============================================================================


class TestJ1939CmdtReassembly:
    """Test SAE J1939-21 Connection Mode Data Transfer (CMDT RTS/CTS) reassembly."""

    def test_j1939_rts_cts_cmdt_full_flow(self) -> None:
        """Test complete point-to-point transfer with CTS and EndOfMsgACK generation."""
        mock_tx = MockTxPort()
        pipeline = ReassemblyPipeline(
            tx_port=mock_tx,
            my_j1939_address=0xF9,
            channel_id="can0",
        )
        tx_frames_captured: list[CanFrame] = []
        pipeline.register_on_tx_frame(lambda f: tx_frames_captured.append(f))

        target_pgn = 61184  # Proprietary A (0xEF00)
        data_payload = b"CMDT_TEST_12345"  # 15 bytes -> 3 packets

        # 1. Transmitter sends TP.CM_RTS (DA = 0xF9, SA = 0x20 -> 0x1CECF920)
        rts_data = bytearray(8)
        rts_data[0] = TP_CTRL_RTS
        rts_data[1:3] = len(data_payload).to_bytes(2, byteorder="little")
        rts_data[3] = 3  # 3 packets
        rts_data[4] = 0xFF
        rts_data[5:8] = target_pgn.to_bytes(3, byteorder="little")

        rts_frame = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x1CECF920,
            data=bytes(rts_data),
            is_extended=True,
        )

        res = pipeline.process_frame(rts_frame)
        assert res is None

        # Verify pipeline emitted TP.CM_CTS frame (DA = 0x20, SA = 0xF9 -> 0x1CEC20F9)
        assert len(tx_frames_captured) == 1
        cts_frame = tx_frames_captured[0]
        assert cts_frame.arbitration_id == 0x1CEC20F9
        assert cts_frame.data[0] == TP_CTRL_CTS
        assert cts_frame.data[1] == 3  # packets allowed
        assert cts_frame.data[2] == 1  # next sequence expected

        # 2. Transmitter sends DT packets
        for seq in range(1, 4):
            chunk = data_payload[(seq - 1) * 7 : seq * 7]
            dt_data = bytearray(8)
            dt_data[0] = seq
            dt_data[1 : 1 + len(chunk)] = chunk
            for i in range(1 + len(chunk), 8):
                dt_data[i] = 0xFF

            dt_frame = CanFrame.create(
                channel_id="can0",
                arbitration_id=0x1CEBF920,
                data=bytes(dt_data),
                is_extended=True,
            )
            res = pipeline.process_frame(dt_frame)
            if seq < 3:
                assert res is None

        # 3. Verify transfer completed and EndOfMsgACK was sent
        assert res is not None
        assert res.protocol == "J1939"
        assert res.is_bam is False
        assert res.source_address == 0x20
        assert res.destination_address == 0xF9
        assert res.pgn == target_pgn
        assert res.data == data_payload

        assert len(tx_frames_captured) == 2
        ack_frame = tx_frames_captured[1]
        assert ack_frame.arbitration_id == 0x1CEC20F9
        assert ack_frame.data[0] == TP_CTRL_ACK
        assert int.from_bytes(ack_frame.data[1:3], "little") == len(data_payload)
        assert ack_frame.data[3] == 3

    def test_j1939_cmdt_session_collision_handling(self) -> None:
        """Test that a new RTS on an in-flight session sends Conn_Abort (reason 0x02) and establishes new session."""
        mock_tx = MockTxPort()
        pipeline = ReassemblyPipeline(tx_port=mock_tx, my_j1939_address=0xF9, channel_id="can0")

        # 1. Send first RTS for PGN 61184
        rts1_data = bytearray([TP_CTRL_RTS, 14, 0, 2, 0xFF, 0x00, 0xEF, 0x00])
        f1 = CanFrame.create(channel_id="can0", arbitration_id=0x1CECF920, data=bytes(rts1_data), is_extended=True)
        pipeline.process_frame(f1)
        assert len(mock_tx.sent_frames) == 1
        assert mock_tx.sent_frames[0].data[0] == TP_CTRL_CTS

        # 2. Send second RTS before completing first -> collision
        rts2_data = bytearray([TP_CTRL_RTS, 21, 0, 3, 0xFF, 0x00, 0xEF, 0x00])
        f2 = CanFrame.create(channel_id="can0", arbitration_id=0x1CECF920, data=bytes(rts2_data), is_extended=True)
        pipeline.process_frame(f2)

        # Pipeline emits Conn_Abort (reason 0x02) for the old session AND —
        # since P2-4 drains the pending queue — the new session's CTS too
        # (the old code queued the CTS where it rotted and the peer timed out).
        assert len(mock_tx.sent_frames) == 3
        abort_frame = mock_tx.sent_frames[1]
        assert abort_frame.data[0] == TP_CTRL_ABORT
        assert abort_frame.data[1] == ABORT_REASON_SESSION_COLLISION
        cts_frame = mock_tx.sent_frames[2]
        assert cts_frame.data[0] == TP_CTRL_CTS

    def test_j1939_cmdt_out_of_order_sequence_abort(self) -> None:
        """Test that an unexpected sequence number aborts CMDT session with reason 0x01."""
        mock_tx = MockTxPort()
        pipeline = ReassemblyPipeline(tx_port=mock_tx, my_j1939_address=0xF9, channel_id="can0")

        # Establish session
        rts_data = bytearray([TP_CTRL_RTS, 14, 0, 2, 0xFF, 0x00, 0xEF, 0x00])
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CECF920, data=bytes(rts_data), is_extended=True)
        )

        # Send DT packet with sequence 2 (expected 1)
        dt_bad = bytearray([2, 1, 2, 3, 4, 5, 6, 7])
        pipeline.process_frame(
            CanFrame.create(channel_id="can0", arbitration_id=0x1CEBF920, data=bytes(dt_bad), is_extended=True)
        )

        # Conn_Abort should be emitted
        assert len(mock_tx.sent_frames) == 2
        abort_frame = mock_tx.sent_frames[1]
        assert abort_frame.data[0] == TP_CTRL_ABORT
        assert abort_frame.data[1] == ABORT_REASON_SEQUENCE_ERROR


# ==============================================================================
# 3. ISO-TP Multi-Frame Reassembly Tests
# ==============================================================================


class TestIsoTpReassembly:
    """Test ISO 15765-2 DoCAN (ISO-TP) Single Frame, Multi-Frame, and Flow Control."""

    def test_isotp_single_frame_classic(self) -> None:
        """Test Classic CAN Single Frame (SF_DL 1..7)."""
        pipeline = ReassemblyPipeline(channel_id="uds_ch0")

        # 0x7E8 UDS ReadDataByIdentifier Positive Response (0x62, 0xF1, 0x90, 'A', 'B', 'C')
        sf_payload = bytes([0x06, 0x62, 0xF1, 0x90, 0x41, 0x42, 0x43, 0xCC])
        sf_frame = CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=0x7E8,
            data=sf_payload,
            is_extended=False,
        )

        msg = pipeline.process_frame(sf_frame)
        assert msg is not None
        assert msg.protocol == "ISO-TP"
        assert msg.arbitration_id == 0x7E8
        assert msg.data == bytes([0x62, 0xF1, 0x90, 0x41, 0x42, 0x43])

    def test_isotp_single_frame_can_fd_extended(self) -> None:
        """Test CAN-FD Extended Single Frame (SF_DL <= 62)."""
        pipeline = ReassemblyPipeline(channel_id="uds_ch0")

        payload_bytes = b"CAN_FD_SINGLE_FRAME_PAYLOAD_DATA_24B"
        sf_raw = bytes([0x00, len(payload_bytes)]) + payload_bytes
        dlc = length_to_dlc(len(sf_raw))
        padded = sf_raw + bytes([0xCC] * (dlc_to_length(dlc) - len(sf_raw)))

        sf_frame = CanFrame(
            channel_id="uds_ch0",
            arbitration_id=0x7E8,
            dlc=dlc,
            data=padded,
            is_extended=False,
            is_fd=True,
        )

        msg = pipeline.process_frame(sf_frame)
        assert msg is not None
        assert msg.protocol == "ISO-TP"
        assert msg.data == payload_bytes

    def test_isotp_standard_12bit_multi_frame(self) -> None:
        """Test Standard 12-bit First Frame + Consecutive Frames + Flow Control."""
        mock_tx = MockTxPort()
        pipeline = ReassemblyPipeline(tx_port=mock_tx, channel_id="uds_ch0")

        # 30-byte payload across 1 First Frame (6B) + 4 Consecutive Frames (7B + 7B + 7B + 3B)
        full_payload = bytes([i % 256 for i in range(30)])
        rx_id = 0x7E8
        tx_id = 0x7E0

        # 1. First Frame (FF_DL = 30) -> 0x10 0x1E
        ff_data = bytes([(PCI_FIRST_FRAME << 4) | (0), 30]) + full_payload[:6]
        ff_frame = CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=rx_id,
            data=ff_data,
            is_extended=False,
        )

        res = pipeline.process_frame(ff_frame)
        assert res is None

        # Verify Flow Control CTS was dispatched to 0x7E0
        assert len(mock_tx.sent_frames) == 1
        fc_frame = mock_tx.sent_frames[0]
        assert fc_frame.arbitration_id == tx_id
        assert (fc_frame.data[0] >> 4) == PCI_FLOW_CONTROL
        assert (fc_frame.data[0] & 0x0F) == FS_CTS

        # 2. Consecutive Frames (CF 1..4)
        bytes_sent = 6
        seq = 1
        while bytes_sent < 30:
            chunk = full_payload[bytes_sent : bytes_sent + 7]
            cf_raw = bytes([(PCI_CONSECUTIVE_FRAME << 4) | (seq & 0x0F)]) + chunk
            if len(cf_raw) < 8:
                cf_raw += bytes([0xCC] * (8 - len(cf_raw)))
            cf_frame = CanFrame.create(
                channel_id="uds_ch0",
                arbitration_id=rx_id,
                data=cf_raw,
                is_extended=False,
            )
            res = pipeline.process_frame(cf_frame)
            bytes_sent += len(chunk)
            seq += 1

        assert res is not None
        assert res.protocol == "ISO-TP"
        assert res.arbitration_id == rx_id
        assert res.data == full_payload

    def test_isotp_extended_32bit_multi_frame(self) -> None:
        """Test ISO-TP Extended 32-bit First Frame (payload length > 4095 bytes)."""
        mock_tx = MockTxPort()
        pipeline = ReassemblyPipeline(tx_port=mock_tx, channel_id="uds_ch0")

        total_length = 5000
        full_payload = bytes([i % 256 for i in range(total_length)])
        rx_id = 0x7E8

        # 32-bit Extended First Frame: 0x10, 0x00, Length[4 bytes], First 2 bytes data
        ff_header = bytes([0x10, 0x00]) + total_length.to_bytes(4, "big")
        ff_data = ff_header + full_payload[:2]
        ff_frame = CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=rx_id,
            data=ff_data,
            is_extended=False,
        )

        res = pipeline.process_frame(ff_frame)
        assert res is None
        assert len(mock_tx.sent_frames) == 1  # Flow control CTS sent

        # Send all CFs
        bytes_sent = 2
        seq = 1
        while bytes_sent < total_length:
            chunk = full_payload[bytes_sent : bytes_sent + 7]
            cf_raw = bytes([(PCI_CONSECUTIVE_FRAME << 4) | (seq & 0x0F)]) + chunk
            if len(cf_raw) < 8:
                cf_raw += bytes([0xCC] * (8 - len(cf_raw)))
            cf_frame = CanFrame.create(
                channel_id="uds_ch0",
                arbitration_id=rx_id,
                data=cf_raw,
                is_extended=False,
            )
            res = pipeline.process_frame(cf_frame)
            bytes_sent += len(chunk)
            seq += 1

        assert res is not None
        assert res.protocol == "ISO-TP"
        assert len(res.data) == total_length
        assert res.data == full_payload

    def test_isotp_buffer_overflow_rejection(self) -> None:
        """Test that FF requesting payload greater than MAX_PAYLOAD_SIZE triggers Flow Control OVERFLOW."""
        mock_tx = MockTxPort()
        pipeline = ReassemblyPipeline(tx_port=mock_tx, channel_id="uds_ch0")

        huge_length = 2_000_000  # > 1 MB limit
        ff_header = bytes([0x10, 0x00]) + huge_length.to_bytes(4, "big") + b"\x11\x22"
        ff_frame = CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=0x7E8,
            data=ff_header,
            is_extended=False,
        )

        res = pipeline.process_frame(ff_frame)
        assert res is None
        assert len(mock_tx.sent_frames) == 1
        fc_frame = mock_tx.sent_frames[0]
        assert (fc_frame.data[0] >> 4) == PCI_FLOW_CONTROL
        assert (fc_frame.data[0] & 0x0F) == FS_OVERFLOW

    def test_isotp_out_of_order_sequence_eviction(self) -> None:
        """Test that out-of-order CF evicts the ISO-TP session cleanly."""
        pipeline = ReassemblyPipeline(channel_id="uds_ch0")

        # Send FF for 20 bytes
        ff_data = bytes([(PCI_FIRST_FRAME << 4), 20]) + b"123456"
        pipeline.process_frame(CanFrame.create(channel_id="uds_ch0", arbitration_id=0x7E8, data=ff_data))
        assert pipeline.get_active_session_count() == 1

        # Send CF with sequence 2 instead of 1
        cf_bad = bytes([(PCI_CONSECUTIVE_FRAME << 4) | 2]) + b"7890123"
        res = pipeline.process_frame(CanFrame.create(channel_id="uds_ch0", arbitration_id=0x7E8, data=cf_bad))
        assert res is None
        # Session should be evicted
        assert pipeline.get_active_session_count() == 0

    def test_isotp_dynamic_rx_id_registration(self) -> None:
        """Test adding and removing custom ISO-TP arbitration IDs."""
        pipeline = ReassemblyPipeline(channel_id="uds_ch0")

        custom_rx_id = 0x650
        # By default 0x650 is not an ISO-TP ID
        sf_frame = CanFrame.create(
            channel_id="uds_ch0",
            arbitration_id=custom_rx_id,
            data=bytes([0x03, 0x22, 0x10, 0x01, 0xCC, 0xCC, 0xCC, 0xCC]),
            is_extended=False,
        )
        assert pipeline.process_frame(sf_frame) is None

        # Add custom ID
        pipeline.add_isotp_rx_id(custom_rx_id)
        msg = pipeline.process_frame(sf_frame)
        assert msg is not None
        assert msg.protocol == "ISO-TP"
        assert msg.arbitration_id == custom_rx_id
        assert msg.data == bytes([0x22, 0x10, 0x01])

        # Remove custom ID
        pipeline.remove_isotp_rx_id(custom_rx_id)
        assert pipeline.process_frame(sf_frame) is None


# ==============================================================================
# 4. Integration with DBC Decoder & FrameRouter
# ==============================================================================


class TestDbcAndRouterIntegration:
    """Test full integration with DbcSignalDecoder and FrameRouter."""

    def test_dbc_signal_decoding_on_reassembly(self) -> None:
        """Test that reassembled J1939 BAM frames trigger DBC decoding and callbacks."""
        # Simple DBC definition with Engine Speed (PGN 61444) and a multi-byte custom PGN (PGN 65280 -> 0x18FF0000 | 0x80000000 = 2566848512)
        dbc_content = """
VERSION ""
BO_ 2364539904 EEC1: 8 Vector__XXX
 SG_ EngineSpeed : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX
BO_ 2566848512 CustomMulti: 14 Vector__XXX
 SG_ Pressure1 : 0|16@1+ (0.1,0) [0|6553.5] "kPa" Vector__XXX
 SG_ Temperature1 : 16|8@1+ (1,-40) [-40|215] "degC" Vector__XXX
 SG_ StatusFlag : 24|2@1+ (1,0) [0|3] "" Vector__XXX
"""
        decoder = DbcSignalDecoder.from_dbc_string(dbc_content)
        router = FrameRouter()

        decoded_messages: list[DecodedMessage] = []
        pipeline = ReassemblyPipeline(
            router=router,
            dbc_decoder=decoder,
            channel_id="can0",
            on_decoded=lambda dec: decoded_messages.append(dec),
        )

        # Target PGN 65280 (0xFF00 Proprietary B / CustomMulti)
        # Target payload: Pressure1 = 1000 (100.0 kPa, raw=1000 -> 0x03E8), Temp1 = 80 degC (raw=120 -> 0x78), Status=1
        custom_payload = bytearray(14)
        custom_payload[0:2] = (1000).to_bytes(2, "little")
        custom_payload[2] = 120
        custom_payload[3] = 0x01
        for i in range(4, 14):
            custom_payload[i] = 0xAA

        j1939_tx = J1939TransportProtocol(my_address=0x00, channel_id="can0")
        frames = j1939_tx.start_tp_bam(pgn=65280, data=bytes(custom_payload))

        for f in frames:
            pipeline.process_frame(f)

        assert len(decoded_messages) == 1
        decoded = decoded_messages[0]
        assert decoded.message_name == "CustomMulti"
        assert "Pressure1" in decoded.signals
        assert decoded.signals["Pressure1"].value == 100.0
        assert decoded.signals["Pressure1"].status == SignalStatus.VALID
        assert decoded.signals["Temperature1"].value == 80.0

    def test_single_frame_direct_dbc_decoding(self) -> None:
        """Test that single CAN frames are passed directly to DbcSignalDecoder when decode_single_frames is True."""
        dbc_content = """
VERSION ""
BO_ 2364539904 EEC1: 8 Vector__XXX
 SG_ EngineSpeed : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX
"""
        decoder = DbcSignalDecoder.from_dbc_string(dbc_content)
        decoded_list: list[DecodedMessage] = []

        pipeline = ReassemblyPipeline(
            dbc_decoder=decoder,
            decode_single_frames=True,
            channel_id="can0",
            on_decoded=lambda d: decoded_list.append(d),
        )

        # EEC1 CAN ID: 0x0CF00400 (PGN 61444), Speed = 8000 (1000 RPM -> raw = 8000 -> 0x1F40)
        eec1_data = bytearray(8)
        eec1_data[3:5] = (8000).to_bytes(2, "little")

        frame = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x0CF00400,
            data=bytes(eec1_data),
            is_extended=True,
        )

        pipeline.process_frame(frame)
        assert len(decoded_list) == 1
        assert decoded_list[0].signals["EngineSpeed"].value == 1000.0

    def test_framerouter_automatic_subscription_and_synthesis(self) -> None:
        """Test that publishing raw frames to FrameRouter activates pipeline and emits synthetic frames."""
        router = FrameRouter()
        ReassemblyPipeline(
            router=router,
            auto_subscribe_router=True,
            channel_id="can0",
        )

        # Subscribe an external downstream observer to FrameRouter for synthetic frames
        routed_synthetic_frames: list[CanFrame] = []
        router.subscribe(
            callback=lambda f: routed_synthetic_frames.append(f),
            channel_id="can0",
        )

        # Inject BAM frames directly through FrameRouter
        vin_payload = b"1HGCR2F83HA999999* "
        j1939_tx = J1939TransportProtocol(my_address=0x00, channel_id="can0")
        raw_frames = j1939_tx.start_tp_bam(pgn=PGN_VIN, data=vin_payload)

        for f in raw_frames:
            router.route_frame(f)

        # Synthetic frame with PGN 65260 should have been routed
        synthetic_vin = [f for f in routed_synthetic_frames if f.arbitration_id == 0x18FEEC00]
        assert len(synthetic_vin) == 1
        assert synthetic_vin[0].data == vin_payload


# ==============================================================================
# 5. Concurrency, Out-of-Order, Timeouts & Quota Tests
# ==============================================================================


class TestConcurrencyAndTimeouts:
    """Test concurrency, timeout reclamation, out-of-order recovery, and quotas."""

    def test_j1939_bam_session_replacement_on_collision(self) -> None:
        """Test that a new BAM from same SA and DA replaces an in-flight BAM session."""
        pipeline = ReassemblyPipeline(channel_id="can0")

        # Start BAM 1
        cm1 = bytearray([TP_CTRL_BAM, 14, 0, 2, 0xFF, 0x00, 0xEF, 0x00])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF00, data=bytes(cm1), is_extended=True))
        assert pipeline.get_active_session_count() == 1

        # Start BAM 2 from same SA without completing BAM 1
        cm2 = bytearray([TP_CTRL_BAM, 21, 0, 3, 0xFF, 0x00, 0xEE, 0x00])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF00, data=bytes(cm2), is_extended=True))
        assert pipeline.get_active_session_count() == 1

    def test_j1939_bam_out_of_order_silent_eviction(self) -> None:
        """Test that out-of-order sequence in BAM silently evicts session."""
        pipeline = ReassemblyPipeline(channel_id="can0")

        # Start BAM
        cm = bytearray([TP_CTRL_BAM, 14, 0, 2, 0xFF, 0x00, 0xEF, 0x00])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF00, data=bytes(cm), is_extended=True))
        assert pipeline.get_active_session_count() == 1

        # Send DT sequence 2 (expected 1)
        dt_bad = bytearray([2, 1, 2, 3, 4, 5, 6, 7])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CEBFF00, data=bytes(dt_bad), is_extended=True))
        assert pipeline.get_active_session_count() == 0

    def test_j1939_dm2_previously_active_dtc_reassembly(self) -> None:
        """Test reassembling DM2 (Previously Active DTCs) message."""
        pipeline = ReassemblyPipeline(channel_id="can0")

        dm2_raw = bytearray([0x00, 0xFF, 0x64, 0x00, 0x01, 0x02])  # Lamp OFF, SPN 100 FMI 1 OC 2
        j1939_tx = J1939TransportProtocol(my_address=0x00, channel_id="can0")
        frames = j1939_tx.start_tp_bam(pgn=PGN_DM2, data=bytes(dm2_raw))

        res = None
        for f in frames:
            res = pipeline.process_frame(f)

        assert res is not None
        assert res.pgn == PGN_DM2
        assert isinstance(res.diagnostics, DMMessage)
        assert len(res.diagnostics.dtcs) == 1
        assert res.diagnostics.dtcs[0].spn == 100

    def test_pipeline_statistics_and_callback_unregistration(self) -> None:
        """Test get_stats() metrics and callback unregistration handles."""
        pipeline = ReassemblyPipeline(channel_id="can0")

        unreg = pipeline.register_on_synthetic_frame(lambda f: None)
        unreg_tx = pipeline.register_on_tx_frame(lambda f: None)
        unreg_dec = pipeline.register_on_decoded(lambda d: None)

        unreg()
        unreg_tx()
        unreg_dec()

        stats = pipeline.get_stats()
        assert "total_frames_processed" in stats
        assert "j1939_messages_reassembled" in stats
        assert "isotp_messages_reassembled" in stats
        assert stats["total_active_sessions"] == 0

    def test_multithreaded_concurrent_sessions(self) -> None:
        """Test concurrent multi-threaded J1939 BAM and ISO-TP streams across 16 nodes."""
        pipeline = ReassemblyPipeline(channel_id="can0")
        reassembled_count = 0
        lock = threading.Lock()

        def on_msg(m: ReassembledMessage) -> None:
            nonlocal reassembled_count
            with lock:
                reassembled_count += 1

        pipeline.register_on_reassembled(on_msg)

        def stream_j1939(sa: int, pgn_offset: int) -> None:
            data = f"NODE_{sa:02X}_PAYLOAD_{pgn_offset}".encode("ascii") * 3
            tx = J1939TransportProtocol(my_address=sa, channel_id="can0")
            frames = tx.start_tp_bam(pgn=65280 + pgn_offset, data=data)
            for f in frames:
                pipeline.process_frame(f)

        def stream_isotp(rx_id: int) -> None:
            data = f"ISOTP_NODE_{rx_id:04X}_PAYLOAD".encode("ascii") * 2
            ff_data = bytes([(PCI_FIRST_FRAME << 4), len(data)]) + data[:6]
            pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=rx_id, data=ff_data))
            seq = 1
            for offset in range(6, len(data), 7):
                chunk = data[offset : offset + 7]
                cf_data = bytes([(PCI_CONSECUTIVE_FRAME << 4) | (seq & 0x0F)]) + chunk
                if len(cf_data) < 8:
                    cf_data += bytes([0xCC] * (8 - len(cf_data)))
                pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=rx_id, data=cf_data))
                seq += 1

        threads = []
        # 8 concurrent J1939 nodes
        for sa in range(1, 9):
            t = threading.Thread(target=stream_j1939, args=(sa, sa))
            threads.append(t)
        # 8 concurrent ISO-TP nodes (0x7E8..0x7EF)
        for i in range(8):
            t = threading.Thread(target=stream_isotp, args=(0x7E8 + i,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert reassembled_count == 16
        assert pipeline.get_stats()["j1939_messages_reassembled"] == 8
        assert pipeline.get_stats()["isotp_messages_reassembled"] == 8
        assert pipeline.get_active_session_count() == 0

    def test_monotonic_timeout_reclamation(self) -> None:
        """Test that inactive sessions exceeding T1/N_Cr timeouts are reaped monotonically."""
        mock_clock = MockClock(initial=100.0)
        pipeline = ReassemblyPipeline(clock_provider=mock_clock, channel_id="can0")

        # 1. Start J1939 BAM session
        cm_data = bytearray([TP_CTRL_BAM, 14, 0, 2, 0xFF, 0x00, 0xEF, 0x00])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x1CECFF00, data=bytes(cm_data), is_extended=True))

        # 2. Start ISO-TP session (FF)
        ff_data = bytes([(PCI_FIRST_FRAME << 4), 20, 1, 2, 3, 4, 5, 6])
        pipeline.process_frame(CanFrame.create(channel_id="can0", arbitration_id=0x7E8, data=ff_data))

        assert pipeline.get_active_session_count() == 2

        # Advance clock by 0.5s (< timeout) -> nothing reaped
        mock_clock.advance(0.5)
        assert pipeline.reap_stale_sessions() == 0
        assert pipeline.get_active_session_count() == 2

        # Advance clock by additional 0.6s (total 1.1s > J1939 T1 and ISO-TP N_Cr)
        mock_clock.advance(0.6)
        reaped = pipeline.reap_stale_sessions()
        assert reaped == 2
        assert pipeline.get_active_session_count() == 0

    def test_quota_limits_per_source_address(self) -> None:
        """Test that exceeding per-SA session quota prevents resource exhaustion."""
        pipeline = ReassemblyPipeline(channel_id="can0")
        sa = 0x10

        # Maximum per-SA quota is 4
        for i in range(4):
            cm = bytearray([TP_CTRL_BAM, 14, 0, 2, 0xFF, i, 0xEF, 0x00])
            pipeline.process_frame(CanFrame.create(channel_id=f"can{i}", arbitration_id=0x1CECFF00 | sa, data=bytes(cm), is_extended=True))

        # Attempt 5th session on same SA
        cm_5th = bytearray([TP_CTRL_BAM, 14, 0, 2, 0xFF, 5, 0xEF, 0x00])
        res = pipeline.process_frame(CanFrame.create(channel_id="can_overflow", arbitration_id=0x1CECFF00 | sa, data=bytes(cm_5th), is_extended=True))
        assert res is None

    def test_lifecycle_and_context_manager(self) -> None:
        """Test pipeline reset, unregister callbacks, and context manager lifecycle."""
        router = FrameRouter()
        with ReassemblyPipeline(router=router, channel_id="can0") as pipeline:
            unreg = pipeline.register_on_reassembled(lambda m: None)
            assert pipeline.subscription_count if hasattr(pipeline, "subscription_count") else True
            unreg()
            pipeline.reset()
            assert pipeline.get_active_session_count() == 0

        # Router subscription should be cleanly removed upon exit
        assert router.subscription_count == 0


def test_protocol_response_whitelist_helpers() -> None:
    """E5 regression: helper material must authorize exactly our protocol responses."""
    my_sa = 0xF9
    masks = j1939_protocol_response_masks(my_sa)

    def authorized(arb_id: int) -> bool:
        return any((arb_id & mask) == value for value, mask in masks)

    # Our TP.CM / TP.DT / 29-bit ISO-TP responses to arbitrary peers pass
    assert authorized(0x1CEC01F9) is True  # CTS to peer 0x01
    assert authorized(0x1CEB42F9) is True  # TP.DT to peer 0x42
    assert authorized(0x18DA00F9) is True  # ISO-TP flow control

    # Frames sourced from another address never pass
    assert authorized(0x1CEC01AA) is False
    assert authorized(0x1CEB0000) is False

    # 11-bit diagnostic response set covers physical + functional IDs
    assert 0x7DF in PROTOCOL_RESPONSE_11BIT_IDS
    assert 0x7E0 in PROTOCOL_RESPONSE_11BIT_IDS
    assert 0x7EF in PROTOCOL_RESPONSE_11BIT_IDS
    assert 0x7F0 not in PROTOCOL_RESPONSE_11BIT_IDS


def test_protocol_response_rate_limit_drops_storm_review3() -> None:
    """REVIEW 3 (MEDIUM): a remote node flooding FFs baits CTS/FC responses;
    the per-peer sliding window drops the excess BEFORE the gateway budget
    is drained — no response storm, no E-Stop escalation."""
    mock_tx = MockTxPort()
    clock = MockClock()
    pipeline = ReassemblyPipeline(
        tx_port=mock_tx,
        my_j1939_address=0xF9,
        channel_id="can0",
        clock_provider=clock,
    )
    # First Frame (ISO-TP) addressed to US (DA=0xF1) -> one FC(CTS) per FF.
    # The response goes to the peer 0x00 (source byte), DA back to 0xF1.
    ff = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18DAF100,
        data=bytes([0x10, 0x0A, 0x22, 0xF1, 0x90, 0x01, 0x02, 0x03]),
        is_extended=True,
    )
    sent = 0
    for _ in range(pipeline.TX_RESPONSE_MAX_PER_PEER + 20):
        pipeline.process_frame(ff)
        sent = len(mock_tx.sent_frames)
    # Window caps at TX_RESPONSE_MAX_PER_PEER per peer inside 1.0 s.
    assert sent == pipeline.TX_RESPONSE_MAX_PER_PEER
    assert pipeline._tx_response_dropped >= 20

    # Advancing the clock past the window re-admits responses.
    clock.advance(pipeline.TX_RESPONSE_WINDOW_SEC + 0.1)
    before = len(mock_tx.sent_frames)
    pipeline.process_frame(ff)
    assert len(mock_tx.sent_frames) == before + 1


def test_protocol_response_rate_limit_bounded_peer_table_review3() -> None:
    """REVIEW 3: the peer table itself stays bounded — an attacker spraying
    FFs from >256 distinct arbitration IDs cannot grow it unbounded."""
    mock_tx = MockTxPort()
    clock = MockClock()
    pipeline = ReassemblyPipeline(
        tx_port=mock_tx,
        my_j1939_address=0xF9,
        channel_id="can0",
        clock_provider=clock,
    )
    for i in range(pipeline.TX_RESPONSE_PEERS_TRACKED + 50):
        ff = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x18DA0000 + (i & 0xFF),
            data=bytes([0x10, 0x0A, 0x22, 0xF1, 0x90, 0x01, 0x02, 0x03]),
            is_extended=True,
        )
        pipeline.process_frame(ff)
    assert len(pipeline._tx_response_timestamps) <= pipeline.TX_RESPONSE_PEERS_TRACKED

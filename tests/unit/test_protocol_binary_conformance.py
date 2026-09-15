"""Comprehensive Conformance & Binary Forensic Regression Test Suite.

Verifies:
1. ISO 15765-2:2016 CAN-FD Extended Single Frame (SF_DL > 7) RX header decoding.
2. NMEA 2000 PGN 127488 signed int8 Tilt/Trim decoding (-100% .. +100%).
3. ISO 14229-1 Service 0x36 TransferData blockSequenceCounter rollover (0xFF -> 0x00).
4. SAE J1939-81 Address Claim PDU1 Destination Address isolation (PGN 60928 canonical).
5. NMEA 2000 Fast Packet PDU1/PDU2 PGN isolation.
6. SAE J1939-21 TP.CM boundary rejection (1785B max, packet count consistency).
7. Volvo Penta EVC PGN 65361 (Trim & Rudder) parsing.
"""

from unittest.mock import MagicMock

import pytest

from src.core.errors import ProtocolError
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.address_claim import AddressClaimEngine, J1939Name
from src.protocols.j1939.transport import J1939TransportProtocol
from src.protocols.nmea2000.fast_packet import Nmea2000FastPacketDecoder
from src.protocols.nmea2000.pgn_library import Nmea2000PgnDecoder
from src.protocols.uds.flasher import EcuFlashingEngine, FlashingConfig, FlashingStep
from src.protocols.uds.isotp import IsoTpTransport
from src.protocols.volvo.volvo_decoder import VolvoPentaDecoder


def test_isotp_can_fd_extended_single_frame_rx() -> None:
    """Verify ISO 15765-2:2016 CAN-FD Single Frame with 16-byte payload (SF_DL > 7)."""
    transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
    raw_payload = b"\x62\xf1\x90" + b"ABCDEFGHIJKLM"  # 16 bytes
    assert len(raw_payload) == 16

    # CAN-FD SF Header: [0x00, 0x10] followed by 16 bytes payload and 0xCC padding to DLC 15 (64B)
    fd_data = bytes([0x00, 16]) + raw_payload + (b"\xcc" * 46)
    fd_frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x7E8,
        data=fd_data,
        is_fd=True,
        dlc=15,
    )

    rx_payload, resp_frame = transport.handle_rx_frame(fd_frame)
    assert rx_payload == raw_payload
    assert resp_frame is None


def test_n2k_signed_tilt_trim_decoding() -> None:
    """Verify NMEA 2000 PGN 127488 signed int8 Tilt/Trim decoding."""
    # Negative trim (-10%): Byte 5 = 0xF6 (246 in unsigned, -10 in two's-complement int8)
    data_neg = b"\x00\x40\x1f\xdc\x05\xf6\xff\xff"
    res_neg = Nmea2000PgnDecoder.decode_engine_rapid(data_neg)
    assert res_neg is not None
    assert res_neg.tilt_trim_percent == -10

    # Positive trim (+25%): Byte 5 = 0x19
    data_pos = b"\x00\x40\x1f\xdc\x05\x19\xff\xff"
    res_pos = Nmea2000PgnDecoder.decode_engine_rapid(data_pos)
    assert res_pos is not None
    assert res_pos.tilt_trim_percent == 25

    # Out of range (>100%): Byte 5 = 120 (0x78)
    data_oor = b"\x00\x40\x1f\xdc\x05\x78\xff\xff"
    res_oor = Nmea2000PgnDecoder.decode_engine_rapid(data_oor)
    assert res_oor is not None
    assert res_oor.tilt_trim_percent is None


def test_uds_transfer_data_block_sequence_wraparound_to_zero() -> None:
    """Verify ISO 14229-1 Service 0x36 blockSequenceCounter wraps from 0xFF to 0x00."""
    mock_client = MagicMock()
    mock_resp = MagicMock(is_positive=True, nrc=0, data=b"\x20\x10\x00")
    mock_client.change_session.return_value = mock_resp
    mock_client.request_download.return_value = mock_resp
    # P1-6: transfer_data echoes the block sequence counter in data[0].
    # REVIEW 2 CRITICAL-1: _call_transfer_data now sends the FIXED contract
    # (is_critical_command=True, user_confirmed) — the mock must accept it.
    mock_client.transfer_data.side_effect = (
        lambda block_sequence, data, is_critical_command=False, user_confirmed=False: MagicMock(
            is_positive=True, nrc=0, data=bytes([block_sequence & 0xFF])
        )
    )
    mock_client.request_transfer_exit.return_value = mock_resp
    mock_client.start_routine.return_value = MagicMock(is_positive=True, nrc=0, data=b"\x00")
    # REVIEW (routineControl echo): a real ECU replies
    # `71 03 <rid> <rid> <status>` for REQUEST_ROUTINE_RESULTS — the flasher
    # validates the controlType+routineId echo prefix before reading status.
    # checksum_routine_id default = 0x0202, status 0x00 = correctly completed.
    mock_client.request_routine_results.return_value = MagicMock(
        is_positive=True, nrc=0, data=b"\x03\x02\x02\x00"
    )
    mock_client.ecu_reset.return_value = mock_resp

    mock_gateway = MagicMock()
    mock_gateway.estop.is_engaged = False
    # P1-8: preflight fields surfaced to the flasher
    mock_gateway.supervisor.is_tx_permitted = True
    mock_gateway.watchdog.is_lease_valid = True
    mock_gateway.SPEED_NOISE_THRESHOLD_KMH = 0.5
    # T47-B (P1/G-1): interlock reads the PHYSICAL channel; the display
    # attribute is read-only on the real gateway.
    mock_gateway._physical_speed_kmh = 0.0
    mock_gateway._last_speed_update_ns = 1

    engine = EcuFlashingEngine(uds_client=mock_client, gateway=mock_gateway)

    # 300 blocks of 1 byte each (Block 1..255, then 256..300)
    config = FlashingConfig(
        memory_address=0x08000000,
        data=b"X" * 300,
        block_size=1,
        user_confirmed=True,
        verify_checksum=True,
        reset_after_flash=False,
    )

    success = engine.execute_flash(config)
    assert success is True
    assert mock_client.transfer_data.call_count == 300

    calls = mock_client.transfer_data.call_args_list

    # Block 1 (call index 0) -> block_sequence = 1
    assert calls[0].kwargs["block_sequence"] == 1
    # Block 255 (call index 254) -> block_sequence = 255 (0xFF)
    assert calls[254].kwargs["block_sequence"] == 255
    # Block 256 (call index 255) -> block_sequence = 0 (0x00) per ISO 14229-1
    assert calls[255].kwargs["block_sequence"] == 0
    # Block 257 (call index 256) -> block_sequence = 1 (0x01)
    assert calls[256].kwargs["block_sequence"] == 1


def test_uds_flashing_failure_triggers_best_effort_recovery() -> None:
    """P8: a failure after the diagnostic session opened attempts an ECU hard reset."""
    mock_client = MagicMock()
    ok_resp = MagicMock(is_positive=True, nrc=0, data=b"\x20\x10\x00")
    fail_resp = MagicMock(is_positive=False, nrc=0x72, nrc_description_tr="General Programming Failure")
    mock_client.change_session.return_value = ok_resp
    mock_client.request_download.return_value = ok_resp
    mock_client.transfer_data.return_value = fail_resp  # mid-transfer failure
    mock_client.ecu_reset.return_value = ok_resp

    mock_gateway = MagicMock()
    mock_gateway.estop.is_engaged = False
    # P1-8: preflight fields surfaced to the flasher
    mock_gateway.supervisor.is_tx_permitted = True
    mock_gateway.watchdog.is_lease_valid = True
    mock_gateway.SPEED_NOISE_THRESHOLD_KMH = 0.5
    # T47-B (P1/G-1): interlock reads the PHYSICAL channel; the display
    # attribute is read-only on the real gateway.
    mock_gateway._physical_speed_kmh = 0.0
    mock_gateway._last_speed_update_ns = 1
    engine = EcuFlashingEngine(uds_client=mock_client, gateway=mock_gateway)

    config = FlashingConfig(
        memory_address=0x08000000,
        data=b"X" * 4,
        block_size=2,
        user_confirmed=True,
        verify_checksum=True,
        reset_after_flash=False,  # success path would NOT reset — recovery must
    )

    with pytest.raises(ProtocolError):
        engine.execute_flash(config)

    assert engine.current_step == FlashingStep.FAILED
    # REVIEW.md 3.2: recovery NEVER hard-resets a partially flashed ECU —
    # it runs the 0x37 / default-session ladder instead.
    mock_client.ecu_reset.assert_not_called()
    mock_client.request_transfer_exit.assert_called()


def test_uds_flashing_pre_session_failure_skips_recovery() -> None:
    """P8: a failure before any session is opened must not attempt an ECU reset."""
    mock_client = MagicMock()
    mock_gateway = MagicMock()
    mock_gateway.estop.is_engaged = False
    engine = EcuFlashingEngine(uds_client=mock_client, gateway=mock_gateway)

    config = FlashingConfig(
        memory_address=0x08000000,
        data=b"X" * 4,
        user_confirmed=False,  # fails step 1, before any session
    )

    from src.core.errors import SafetyError

    with pytest.raises(SafetyError):
        engine.execute_flash(config)

    assert engine.current_step == FlashingStep.FAILED
    mock_client.ecu_reset.assert_not_called()


def test_j1939_address_claim_pdu1_canonical_pgn() -> None:
    """Verify AddressClaimEngine extracts canonical PGN 60928 from PDU1 CAN ID."""
    my_name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=0,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=0,
        function_instance=0,
        ecu_instance=0,
        manufacturer_code=100,
        identity_number=1,
    )
    engine = AddressClaimEngine(name=my_name, preferred_address=0xF9)

    # Remote Claim CAN ID: 0x18EEFF20 (PGN 60928 / 0xEE00, DA 0xFF, SA 0x20)
    remote_name = J1939Name(
        arbitrary_address_capable=False,
        industry_group=0,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=0,
        function_instance=0,
        ecu_instance=0,
        manufacturer_code=200,
        identity_number=999,
    )
    frame = CanFrame.create(
        channel_id="j1939",
        arbitration_id=0x18EEFF20,
        data=remote_name.to_bytes(),
        is_extended=True,
    )

    resp = engine.handle_rx_frame(frame)
    assert resp is None
    # Verify SA 0x20 is registered in address table
    assert 0x20 in engine._address_table


def test_n2k_fast_packet_pdu1_canonical_pgn() -> None:
    """Verify Fast Packet reassembly correctly isolates Destination Address in PDU1."""
    decoder = Nmea2000FastPacketDecoder()

    # Proprietary PDU1 Fast Packet: PGN 126720 (0x1EF00), DA 0x28, SA 0x05 -> CAN ID 0x19EF2805
    # Total 14 bytes: "ABCDEFGHIJKLMN"
    # Frame 0 (6 bytes payload): "ABCDEF"
    f0_data = b"\x00\x0e" + b"ABCDEF"
    f0 = CanFrame.create(
        channel_id="n2k",
        arbitration_id=0x19EF2805,
        data=f0_data,
        is_extended=True,
    )
    res0 = decoder.handle_rx_frame(f0)
    assert res0 is None

    # Frame 1 (7 bytes payload): "GHIJKLM"
    f1_data = b"\x01" + b"GHIJKLM"
    f1 = CanFrame.create(
        channel_id="n2k",
        arbitration_id=0x19EF2805,
        data=f1_data,
        is_extended=True,
    )
    res1 = decoder.handle_rx_frame(f1)
    assert res1 is None

    # Frame 2 (1 byte payload + padding): "N" + 6x 0xFF
    f2_data = b"\x02" + b"N" + (b"\xff" * 6)
    f2 = CanFrame.create(
        channel_id="n2k",
        arbitration_id=0x19EF2805,
        data=f2_data,
        is_extended=True,
    )
    res2 = decoder.handle_rx_frame(f2)
    assert res2 is not None
    assert res2.pgn == 126720  # Isolated 0x1EF00, not 0x1EF28!
    assert res2.data == b"ABCDEFGHIJKLMN"


def test_j1939_tp_cm_bounds_rejection() -> None:
    """Verify J1939-21 TP.CM rejects total_bytes > 1785, 0-length, or inconsistent packet count."""
    tp = J1939TransportProtocol()

    # 1. Total bytes = 0, Total packets = 0
    f_zero = CanFrame.create(
        channel_id="j1939",
        arbitration_id=0x1CECFF01,
        data=b"\x20\x00\x00\x00\xff\x00\xf0\x00",
        is_extended=True,
    )
    msg, resp = tp.handle_rx_frame(f_zero)
    assert msg is None and resp is None
    assert len(tp._rx_sessions) == 0

    # 2. Total bytes = 2000 (> 1785 limit)
    f_overflow = CanFrame.create(
        channel_id="j1939",
        arbitration_id=0x1CECFF01,
        data=b"\x20\xd0\x07\x00\xff\x00\xf0\x00",  # 2000 bytes (0x07D0)
        is_extended=True,
    )
    msg, resp = tp.handle_rx_frame(f_overflow)
    assert msg is None and resp is None
    assert len(tp._rx_sessions) == 0

    # 3. Packet count mismatch (14 bytes declared with 1 packet instead of 2)
    f_mismatch = CanFrame.create(
        channel_id="j1939",
        arbitration_id=0x1CECFF01,
        data=b"\x20\x0e\x00\x01\xff\x00\xf0\x00",  # 14 bytes, 1 packet
        is_extended=True,
    )
    msg, resp = tp.handle_rx_frame(f_mismatch)
    assert msg is None and resp is None
    assert len(tp._rx_sessions) == 0


def test_volvo_evc_trim_rudder_pgn_65361_decoding() -> None:
    """Verify Volvo Penta EVC PGN 65361 (Trim & Rudder) decoding."""
    # Powertrim: 60 deg -> (raw * 0.1) - 50 = 10 deg -> raw = 600 = 0x0258 -> 58 02
    # Rudder: 95 deg -> (raw * 0.1) - 90 = 5 deg -> raw = 950 = 0x03B6 -> B6 03
    # Station: 0x01
    data = bytes([0x58, 0x02, 0xB6, 0x03, 0x01, 0xFF, 0xFF, 0xFF])
    frame = CanFrame.create(
        channel_id="volvo",
        arbitration_id=0x18FF5100,  # PGN 65361 (0xFF51)
        data=data,
        is_extended=True,
    )

    state = VolvoPentaDecoder.decode_evc_can_frame(frame)
    assert state is not None
    assert round(state.trim_angle_deg, 1) == 10.0
    assert round(state.rudder_angle_deg, 1) == 5.0
    assert state.station_active is True

"""Tests for UDS 0x29 / 0x84 criticality authorization and CAN_ISOTP_WAIT_TX_DONE."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

import pytest

from src.core.models.can_frame import CanFrame, length_to_dlc
from src.hal.virtual import VirtualBus
from src.protocols.uds import (
    CAN_ISOTP_WAIT_TX_DONE,
    AuthenticationTask,
    SocketCanIsoTpGeneralOpts,
    UdsClient,
    UdsServiceBuilder,
    UdsServiceId,
    configure_socketcan_isotp_socket,
)
from src.protocols.uds.isotp import CAN_ISOTP_OPTS, SOL_CAN_ISOTP
from src.safety.gateway import DualConfirmationRequiredError, TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor


def make_frame(arb_id: int, data: bytes, is_extended: bool = False, is_fd: bool = False) -> CanFrame:
    return CanFrame(
        channel_id="can0",
        arbitration_id=arb_id,
        dlc=length_to_dlc(len(data)),
        data=data,
        is_extended=is_extended,
        is_fd=is_fd,
    )


@pytest.fixture
def test_gateway() -> TxSafetyGateway:
    sup = SafetySupervisor()
    for state in (SafetyState.SAFE, SafetyState.PASSIVE, SafetyState.ARMED_TX):
        sup.transition_to(state, "test")
    bus = VirtualBus()
    bus.connect()
    gw = TxSafetyGateway(
        bus=bus,
        supervisor=sup,
        whitelist_ids={0x7DF, *range(0x7E0, 0x7E8)},
        confirmation_secret=b"uds_criticality_test_secret_32b!",
        whitelist_superset_allowed=True,
    )
    # Feed zero physical speed to satisfy speed interlock
    gw.update_physical_speed(0.0)
    return gw


def test_uds_0x29_0x84_service_ids_and_enums() -> None:
    """Verify UdsServiceId values and AuthenticationTask sub-functions."""
    assert UdsServiceId.AUTHENTICATION == 0x29
    assert UdsServiceId.SECURED_DATA_TRANSMISSION == 0x84

    assert AuthenticationTask.DEAUTHENTICATE == 0x00
    assert AuthenticationTask.VERIFY_CERTIFICATE_UNIDIRECTIONAL == 0x01
    assert AuthenticationTask.VERIFY_CERTIFICATE_BIDIRECTIONAL == 0x02
    assert AuthenticationTask.PROOF_OF_OWNERSHIP == 0x03
    assert AuthenticationTask.TRANSMIT_CERTIFICATE == 0x04
    assert AuthenticationTask.REQUEST_CHALLENGE_FOR_AUTHENTICATION == 0x05
    assert AuthenticationTask.VERIFY_PROOF_OF_OWNERSHIP_UNIDIRECTIONAL == 0x06
    assert AuthenticationTask.VERIFY_PROOF_OF_OWNERSHIP_BIDIRECTIONAL == 0x07
    assert AuthenticationTask.AUTHENTICATION_CONFIGURATION == 0x08


def test_uds_service_builder_authentication() -> None:
    """Test UdsServiceBuilder.build_authentication."""
    req = UdsServiceBuilder.build_authentication(
        AuthenticationTask.PROOF_OF_OWNERSHIP,
        b"\x12\x34\x56",
    )
    assert req == b"\x29\x03\x12\x34\x56"

    # With integer subfunction
    req2 = UdsServiceBuilder.build_authentication(0x05)
    assert req2 == b"\x29\x05"

    # Out-of-range subfunction raises ValueError (fail-closed)
    with pytest.raises(ValueError, match="Invalid Authentication sub-function"):
        UdsServiceBuilder.build_authentication(0x99)


def test_uds_service_builder_secured_data_transmission() -> None:
    """Test UdsServiceBuilder.build_secured_data_transmission."""
    req = UdsServiceBuilder.build_secured_data_transmission(b"\xAA\xBB\xCC\xDD")
    assert req == b"\x84\xAA\xBB\xCC\xDD"

    # Empty payload raises ValueError (fail-closed)
    with pytest.raises(ValueError, match="requires non-empty"):
        UdsServiceBuilder.build_secured_data_transmission(b"")


@pytest.mark.parametrize("sid", [0x29, 0x84])
def test_gateway_identifies_11bit_uds_0x29_0x84_as_critical(test_gateway: TxSafetyGateway, sid: int) -> None:
    """Classic 11-bit ISO-TP SingleFrame with 0x29/0x84 is flagged critical."""
    frame = make_frame(
        0x7E0,
        bytes([0x03, sid, 0x01, 0x00]),
        is_extended=False,
    )
    assert test_gateway._frame_is_critical(frame) is True


@pytest.mark.parametrize("sid", [0x29, 0x84])
def test_gateway_identifies_29bit_uds_0x29_0x84_as_critical(test_gateway: TxSafetyGateway, sid: int) -> None:
    """Classic 29-bit ISO-TP SingleFrame with 0x29/0x84 is flagged critical."""
    frame = make_frame(
        0x18DA00F1,
        bytes([0x03, sid, 0x01, 0x00]),
        is_extended=True,
    )
    assert test_gateway._frame_is_critical(frame) is True


@pytest.mark.parametrize("sid", [0x29, 0x84])
def test_gateway_identifies_canfd_extended_sf_as_critical(test_gateway: TxSafetyGateway, sid: int) -> None:
    """CAN-FD Extended SingleFrame ([0x00, DL8, SID, ...]) correctly extracts SID and flags critical."""
    # Length = 10, byte 0 is 0x00, byte 1 is 10, byte 2 is SID. Padded to 12 bytes.
    payload = bytes([0x00, 10, sid]) + b"\x00" * 9
    frame = make_frame(
        0x18DA00F1,
        payload,
        is_extended=True,
        is_fd=True,
    )
    assert test_gateway._frame_is_critical(frame) is True


@pytest.mark.parametrize("sid", [0x29, 0x84])
def test_gateway_identifies_canfd_escape_sf_as_critical(test_gateway: TxSafetyGateway, sid: int) -> None:
    """CAN-FD Escape SingleFrame ([0x00, 0x00, DL32, SID, ...]) extracts SID at offset 6."""
    # 48 bytes total: 7 bytes header + 41 bytes data
    payload = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x20, sid]) + b"\x00" * 41
    frame = make_frame(
        0x18DA00F1,
        payload,
        is_extended=True,
        is_fd=True,
    )
    assert test_gateway._frame_is_critical(frame) is True


def test_gateway_blocks_critical_uds_without_dual_confirmation(test_gateway: TxSafetyGateway) -> None:
    """Outbound 0x29 is rejected without dual confirmation token."""
    frame = make_frame(
        0x7E0,
        bytes([0x02, 0x29, 0x00]),
        is_extended=False,
    )
    with pytest.raises(DualConfirmationRequiredError, match="dual-confirmation"):
        test_gateway.validate_and_transmit(
            frame,
            is_critical_command=True,
            user_confirmed=False,
        )


def test_gateway_authorizes_critical_uds_with_dual_confirmation(test_gateway: TxSafetyGateway) -> None:
    """Outbound 0x29 passes with valid HMAC dual confirmation token and vehicle stopped."""
    frame = make_frame(
        0x7E0,
        bytes([0x02, 0x29, 0x00]),
        is_extended=False,
    )
    token = test_gateway.issue_confirmation_token(frame.arbitration_id)
    passed = test_gateway.validate_and_transmit(
        frame,
        is_critical_command=True,
        user_confirmed=True,
        confirmation_token=token,
    )
    assert passed is True


def test_uds_client_authenticate_and_secured_data_transmission_methods() -> None:
    """Verify UdsClient.authenticate and secured_data_transmission route with is_critical_command=True."""
    mock_bus = MagicMock()
    # Mock bus recv returning a positive response
    # 0x29 + 0x40 = 0x69. Echoes sub-function 0x00.
    mock_bus.recv.return_value = make_frame(
        0x7E8,
        bytes([0x02, 0x69, 0x00]),
        is_extended=False,
    )
    mock_tx_port = MagicMock()

    client = UdsClient(
        bus=mock_bus,
        tx_port=mock_tx_port,
        tx_id=0x7E0,
        rx_id=0x7E8,
    )

    # 1. authenticate
    resp = client.authenticate(
        sub_function=AuthenticationTask.DEAUTHENTICATE,
        user_confirmed=True,
    )
    assert resp.is_positive is True
    assert resp.service_id == 0x29

    # Verify tx_port was called with critical frame
    assert mock_tx_port.validate_and_transmit.called
    sent_frame = mock_tx_port.validate_and_transmit.call_args[0][0]
    sent_kwargs = mock_tx_port.validate_and_transmit.call_args[1]
    assert sent_frame.data[1] == 0x29
    assert sent_kwargs["is_critical_command"] is True
    assert sent_kwargs["user_confirmed"] is True

    # 2. secured_data_transmission
    mock_bus.recv.return_value = make_frame(
        0x7E8,
        bytes([0x03, 0xC4, 0x01, 0x02]),  # 0x84 + 0x40 = 0xC4
        is_extended=False,
    )
    resp2 = client.secured_data_transmission(
        secured_data=b"\x01\x02\x03\x04",
        user_confirmed=True,
    )
    assert resp2.is_positive is True
    assert resp2.service_id == 0x84
    sent_frame2 = mock_tx_port.validate_and_transmit.call_args[0][0]
    sent_kwargs2 = mock_tx_port.validate_and_transmit.call_args[1]
    assert sent_frame2.data[1] == 0x84
    assert sent_kwargs2["is_critical_command"] is True
    assert sent_kwargs2["user_confirmed"] is True
    client.shutdown(wait=False)


def test_can_isotp_wait_tx_done_constant() -> None:
    """Verify CAN_ISOTP_WAIT_TX_DONE constant is 0x400 (1024)."""
    assert CAN_ISOTP_WAIT_TX_DONE == 0x400


def test_socketcan_isotp_general_opts_pack_unpack() -> None:
    """Verify SocketCanIsoTpGeneralOpts packs and unpacks struct can_isotp_options (=IIBBBB)."""
    opts = SocketCanIsoTpGeneralOpts(
        flags=CAN_ISOTP_WAIT_TX_DONE,
        frame_txtime=50,
        ext_address=0x11,
        txpad_content=0xAA,
        rxpad_content=0x55,
        rx_ext_address=0x22,
    )
    packed = opts.pack()
    assert len(packed) == 12

    unpacked = SocketCanIsoTpGeneralOpts.unpack(packed)
    assert unpacked.flags == CAN_ISOTP_WAIT_TX_DONE
    assert unpacked.frame_txtime == 50
    assert unpacked.ext_address == 0x11
    assert unpacked.txpad_content == 0xAA
    assert unpacked.rxpad_content == 0x55
    assert unpacked.rx_ext_address == 0x22

    # Test with_wait_tx_done
    cleared = unpacked.with_wait_tx_done(False)
    assert (cleared.flags & CAN_ISOTP_WAIT_TX_DONE) == 0
    re_enabled = cleared.with_wait_tx_done(True)
    assert (re_enabled.flags & CAN_ISOTP_WAIT_TX_DONE) == CAN_ISOTP_WAIT_TX_DONE


def test_configure_socketcan_isotp_socket_with_raw_socket() -> None:
    """Test configure_socketcan_isotp_socket with socket mock exposing setsockopt/getsockopt."""
    mock_sock = MagicMock(spec=socket.socket)
    # Simulate initial getsockopt returning all zeros
    mock_sock.getsockopt.return_value = bytes(12)

    opts = configure_socketcan_isotp_socket(
        mock_sock,
        wait_tx_done=True,
        tx_padding=True,
    )
    assert (opts.flags & CAN_ISOTP_WAIT_TX_DONE) == CAN_ISOTP_WAIT_TX_DONE
    assert mock_sock.setsockopt.called
    level, optname, val = mock_sock.setsockopt.call_args[0]
    assert level == SOL_CAN_ISOTP
    assert optname == CAN_ISOTP_OPTS
    unpacked = SocketCanIsoTpGeneralOpts.unpack(val)
    assert (unpacked.flags & CAN_ISOTP_WAIT_TX_DONE) == CAN_ISOTP_WAIT_TX_DONE


def test_configure_socketcan_isotp_socket_with_isotp_socket_wrapper() -> None:
    """Test configure_socketcan_isotp_socket with mock exposing set_opts/get_opts."""
    mock_isotp_sock = MagicMock()
    mock_opts = MagicMock()
    mock_opts.optflag = 0x004  # TX_PADDING
    mock_isotp_sock.get_opts.return_value = mock_opts

    configure_socketcan_isotp_socket(
        mock_isotp_sock,
        wait_tx_done=True,
    )
    assert mock_isotp_sock.set_opts.called
    kwargs = mock_isotp_sock.set_opts.call_args[1]
    assert "optflag" in kwargs
    assert (kwargs["optflag"] & CAN_ISOTP_WAIT_TX_DONE) == CAN_ISOTP_WAIT_TX_DONE
    assert (kwargs["optflag"] & 0x004) == 0x004

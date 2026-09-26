"""Regression tests for Stage 8 review remediation findings."""

from __future__ import annotations

import pytest

from src.core.exceptions import ProtocolError
from src.core.models.can_frame import CanFrame
from src.hal.replay.parsers import VectorBlfParser
from src.hal.virtual import VirtualBus
from src.protocols.j1939.oem.caterpillar import CaterpillarDecoder
from src.protocols.obd.poller import ActiveDiagnosticPoller


def test_virtual_bus_reconnect_drains_sentinels() -> None:
    bus = VirtualBus(channel_id="virtual:0")
    bus.connect()
    bus.disconnect()
    # Now reconnect
    bus.connect()
    assert bus.is_connected is True
    # Can inject and receive without receiving a leftover STOP sentinel
    frame = CanFrame(channel_id="virtual:0", arbitration_id=0x123, dlc=1, data=b"\xaa")
    bus.inject_rx(frame)
    rx = bus.recv(timeout_s=0.1)
    assert rx is not None
    assert rx.arbitration_id == 0x123
    bus.disconnect()


def test_virtual_bus_inject_when_disconnected() -> None:
    bus = VirtualBus(channel_id="virtual:0")
    frame = CanFrame(channel_id="virtual:0", arbitration_id=0x123, dlc=1, data=b"\xaa")
    bus.inject_rx(frame)
    assert bus.dropped_rx_frames == 1
    assert bus.metrics.dropped_frames == 1


@pytest.mark.asyncio
async def test_obd_poller_functional_poll_without_rx_subscription_fails_closed() -> None:
    class DummyTxPort:
        async def send(self, frame: CanFrame) -> None:
            pass

    poller = ActiveDiagnosticPoller(tx_port=DummyTxPort(), rx_subscription=None)
    with pytest.raises(ProtocolError) as exc_info:
        await poller.poll_pid_functional(pid=0x0D)
    assert exc_info.value.code == "OBD_NO_SUBSCRIPTION"


def test_vector_blf_parser_classic_can_dlc_invariant() -> None:
    class DummyBlfMsg:
        arbitration_id = 0x100
        is_extended_id = False
        is_fd = False
        data = b"\x01\x02\x03"
        dlc = 8  # Mismatch: 3 bytes data but dlc=8 in classic CAN
        channel = 1
        timestamp = 1.0

    msg = DummyBlfMsg()
    frame = VectorBlfParser._convert_message(msg)
    assert frame is not None
    assert frame.dlc == 3
    assert len(frame.data) == 3


def test_caterpillar_decoder_proprietary_a() -> None:
    decoder = CaterpillarDecoder()
    frame = CanFrame(
        channel_id="j1939:0",
        arbitration_id=0x18EF0000,
        dlc=4,
        data=b"\x20\x04\x12\x34",
        is_extended=True,
        timestamp_ns=1_000_000,
    )
    # PGN 61184 (0xEF00) is PGN_PROPRIETARY_A
    payload = decoder.decode(
        frame=frame,
        pgn=CaterpillarDecoder.PGN_PROPRIETARY_A,
        sa=0x00,
        da=0x00,
    )
    assert payload is not None
    assert payload.signals["service_command_id"].value == 0x20
    assert payload.signals["target_cylinder"].value == 4
    assert payload.signals["service_parameter"].value == 0x3412

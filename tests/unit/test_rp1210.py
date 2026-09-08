"""Unit tests for RP1210 client wrapper, error codes, and RP1210Bus adapter."""

import pytest

from src.core.errors import HardwareError
from src.core.models.can_frame import CanFrame
from src.hal.base import BusState
from src.hal.rp1210.bus import RP1210Bus
from src.hal.rp1210.client import RP1210Client
from src.hal.rp1210.types import RP1210ErrorCode


def test_rp1210_error_code_descriptions() -> None:
    assert RP1210ErrorCode.get_description(0) == "No Errors"
    assert RP1210ErrorCode.get_description(128) == "Dll Not Found"
    assert RP1210ErrorCode.get_description(145) == "Bus Off"
    assert RP1210ErrorCode.get_description(155) == "Address Lost"
    assert "Unknown" in RP1210ErrorCode.get_description(9999)


def test_rp1210_missing_dll_raises_hardware_error() -> None:
    # Attempting to load non-existent vendor DLL must raise structured HardwareError
    with pytest.raises(HardwareError) as exc_info:
        RP1210Client(dll_name="NON_EXISTENT_ADAPTER_12345.DLL")

    assert exc_info.value.code == "HARDWARE_DLL_NOT_FOUND"
    assert "NON_EXISTENT_ADAPTER_12345.DLL" in exc_info.value.message
    assert "dll_name" in exc_info.value.details


class _FakeRP1210Dll:
    """Test double exposing only RP1210_ReadMessage with a scripted return value."""

    def __init__(self, read_return_value: int) -> None:
        self._read_return_value = read_return_value

    def RP1210_ReadMessage(self, client_id: int, rx_buffer: object, buf_size: int, block: int) -> int:
        return self._read_return_value


def _make_client_with_fake_dll(read_return_value: int) -> RP1210Client:
    import ctypes
    import threading

    client = RP1210Client.__new__(RP1210Client)
    client.dll_name = "FAKE.DLL"
    client.device_id = 1
    client.protocol = "J1939"
    client.client_id = 1
    client._dll = _FakeRP1210Dll(read_return_value)  # type: ignore[assignment]
    client._lifecycle_lock = threading.Lock()  # H-H-002 lifecycle guard
    client._rx_scratch = ctypes.create_string_buffer(4096)  # K-06 pre-allocated RX buffer
    client._rx_scratch_size = 4096
    return client


def test_rp1210_read_error_code_is_not_returned_as_data() -> None:
    # D2 / REVIEW.md 2.1 regression: RP1210 error codes are NEGATIVE return
    # values. ret=-129 (INVALID_CLIENT_ID) must raise HardwareError, never
    # be mistaken for received data.
    client = _make_client_with_fake_dll(-129)
    with pytest.raises(HardwareError) as exc_info:
        client.read_message()
    assert exc_info.value.code == "HARDWARE_READ_FAILED"


def test_rp1210_read_rx_queue_full_returns_none() -> None:
    # D2 regression: ret=-136 (ERR_RX_QUEUE_FULL) is an error code, handled
    # gracefully as None (drop warning), not returned as 136 bytes of data.
    client = _make_client_with_fake_dll(-RP1210ErrorCode.ERR_RX_QUEUE_FULL)
    assert client.read_message() is None


def test_rp1210_read_empty_queue_returns_none() -> None:
    client = _make_client_with_fake_dll(0)
    assert client.read_message() is None


def test_rp1210_read_positive_byte_count_returns_data() -> None:
    # Genuine byte counts (1..127) still return payload bytes.
    client = _make_client_with_fake_dll(8)
    data = client.read_message()
    assert data is not None and len(data) == 8


def test_rp1210_read_large_packet_128_plus_bytes_returns_data() -> None:
    """REVIEW.md 2.1 regression: a 200-byte J1939 TP / ISO-TP response packet
    must be RETURNED AS DATA — the old `0 < ret < 128` guard misread any
    byte count >= 128 as an error code and crashed the diagnostic session."""
    client = _make_client_with_fake_dll(200)
    data = client.read_message()
    assert data is not None and len(data) == 200


# ============================================================================
# RP1210Bus — AbstractBus adapter lifecycle & wire format (K4-a)
# ============================================================================


class _MockRP1210Client:
    """In-memory RP1210Client double: records sent packets, serves RX queue."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.rx_queue: list[bytes] = []
        self.connected = False

    def connect(self, tx_buffer_size: int = 8000, rx_buffer_size: int = 8000) -> int:
        self.connected = True
        return 42

    def disconnect(self) -> None:
        self.connected = False

    def send_message(self, message_bytes: bytes, block: bool = False) -> None:
        if not self.connected:
            raise HardwareError("RP1210 client is not connected")
        self.sent.append(bytes(message_bytes))

    def read_message(self, buffer_size: int = 2048, block: bool = False) -> bytes | None:
        if not self.connected:
            raise HardwareError("RP1210 client is not connected")
        if self.rx_queue:
            return self.rx_queue.pop(0)
        return None


def _make_bus(mock: _MockRP1210Client | None = None, protocol: str = "J1939") -> RP1210Bus:
    return RP1210Bus(device_id=1, protocol=protocol, client=mock or _MockRP1210Client())


def test_rp1210_bus_lifecycle() -> None:
    bus = _make_bus()
    assert not bus.is_connected
    assert bus.metrics.state == BusState.DISCONNECTED

    # P0-3: default construction is listen-only — PASSIVE, never an
    # ACK-producing ACTIVE session against a live vehicle bus.
    assert bus.listen_only is True
    bus.connect()
    assert bus.is_connected
    assert bus.metrics.state == BusState.PASSIVE

    # Idempotent disconnect + reconnect
    bus.disconnect()
    assert not bus.is_connected
    assert bus.metrics.state == BusState.DISCONNECTED
    bus.disconnect()  # second disconnect must not raise
    bus.connect()
    assert bus.is_connected
    assert bus.metrics.state == BusState.PASSIVE


def _make_tx_bus(mock: _MockRP1210Client | None = None, protocol: str = "J1939") -> RP1210Bus:
    """TX-capable bus for wire-format tests (P0-3: default is listen-only)."""
    return RP1210Bus(device_id=1, protocol=protocol, client=mock or _MockRP1210Client(), listen_only=False)


def test_rp1210_bus_send_wire_format() -> None:
    mock = _MockRP1210Client()
    bus = _make_tx_bus(mock, protocol="CAN")  # classic 11-bit stack
    bus.connect()

    frame = CanFrame.create(
        channel_id=bus.channel_id, arbitration_id=0x123, data=b"\xDE\xAD\xBE\xEF", is_extended=False
    )
    bus.send(frame)

    assert len(mock.sent) == 1
    packet = mock.sent[0]
    header = int.from_bytes(packet[:2], "little")
    assert header & 0x0F == 4  # DLC nibble
    assert (header >> 4) & 0x7FF == 0x123  # 11-bit ID
    assert packet[2:] == b"\xDE\xAD\xBE\xEF"
    assert bus.metrics.tx_frames == 1


def test_rp1210_bus_send_29bit_j1939_wire_format() -> None:
    """H-C-001 regression: a 29-bit J1939 ID must not be truncated to 12 bits.

    0x18DAF110 is a PDU1 diagnostic request (priority 6, PGN 0xDA00,
    destination 0xF1, source 0x10) — the old wire packing collapsed it
    to 0x110, retargeting the frame at the wrong ECU.
    """
    mock = _MockRP1210Client()
    bus = _make_tx_bus(mock)
    bus.connect()

    frame = CanFrame.create(
        channel_id=bus.channel_id, arbitration_id=0x18DAF110, data=b"\x02\x10\x00", is_extended=True
    )
    bus.send(frame)

    assert len(mock.sent) == 1
    packet = mock.sent[0]
    # 4-byte LE identifier, 1-byte DLC, payload
    assert int.from_bytes(packet[0:4], "little") == 0x18DAF110
    assert packet[4] == 3
    assert packet[5:] == b"\x02\x10\x00"


def test_rp1210_bus_recv_29bit_j1939_roundtrip() -> None:
    """H-C-001 regression: decode must round-trip a 29-bit J1939 identifier."""
    mock = _MockRP1210Client()
    bus = _make_bus(mock)
    bus.connect()

    wire = 0x18DAF110 .to_bytes(4, "little") + bytes([3]) + b"\x02\x10\x00"
    mock.rx_queue.append(wire)

    frame = bus.recv(timeout_s=0.1)
    assert frame is not None
    assert frame.arbitration_id == 0x18DAF110
    assert frame.is_extended
    assert frame.data == b"\x02\x10\x00"
    assert frame.dlc == 3
    assert bus.metrics.rx_frames == 1


def test_rp1210_bus_send_rejects_11bit_on_29bit_stack() -> None:
    """An 11-bit frame on a J1939 stack is a wiring error — fail closed."""
    mock = _MockRP1210Client()
    bus = _make_tx_bus(mock)
    bus.connect()

    frame = CanFrame.create(
        channel_id=bus.channel_id, arbitration_id=0x123, data=b"\x01", is_extended=False
    )
    with pytest.raises(HardwareError, match="29-bit"):
        bus.send(frame)


def test_rp1210_bus_recv_rejects_truncated_29bit_packet() -> None:
    mock = _MockRP1210Client()
    bus = _make_bus(mock)
    bus.connect()

    # Header promises DLC=6 but only 2 payload bytes follow
    wire = 0x18EBFF10 .to_bytes(4, "little") + bytes([6]) + b"\x01\x02"
    mock.rx_queue.append(wire)

    assert bus.recv(timeout_s=0.05) is None
    assert bus.is_connected


def test_default_rp1210_dll_matches_process_bitness() -> None:
    """H-C-004 regression: 64-bit processes must pick RP121064.DLL."""
    import struct

    from src.hal.rp1210.bus import default_rp1210_dll_name

    expected = "RP121064.DLL" if struct.calcsize("P") * 8 == 64 else "RP121032.DLL"
    assert default_rp1210_dll_name() == expected


def test_rp1210_client_send_message_validates_payload() -> None:
    """H-H-005 regression: non-bytes and out-of-range payloads fail closed."""
    client = _make_client_with_fake_dll(0)
    client.client_id = None  # not connected

    with pytest.raises(HardwareError, match="bytes"):
        client.send_message("not-bytes")  # type: ignore[arg-type]

    # Buffer-range guard fires before the connection check for valid bytes
    client2 = _make_client_with_fake_dll(0)
    with pytest.raises(HardwareError, match="length out of range"):
        client2.send_message(b"\x00" * 5000)


def test_rp1210_client_read_message_validates_buffer_size() -> None:
    """H-H-003 regression: buffer_size above the c_short ABI is rejected."""
    client = _make_client_with_fake_dll(0)

    with pytest.raises(HardwareError, match="buffer_size out of range"):
        client.read_message(buffer_size=40000)


def test_rp1210_bus_send_requires_connection_and_classic_frames() -> None:
    mock = _MockRP1210Client()
    bus = _make_tx_bus(mock)

    # Not connected -> structured error, not raw client error
    with pytest.raises(HardwareError):
        bus.send(CanFrame.create(channel_id="c", arbitration_id=0x1, data=b"\x01"))

    bus.connect()
    # FD frames are unsupported on classic RP1210 adapters
    fd_frame = CanFrame.create(
        channel_id="c", arbitration_id=0x1, data=b"\x01" * 9, is_fd=True, dlc=9
    )
    with pytest.raises(HardwareError, match="CAN-FD"):
        bus.send(fd_frame)


def test_rp1210_bus_recv_decodes_and_times_out() -> None:
    mock = _MockRP1210Client()
    bus = _make_bus(mock, protocol="CAN")  # classic 11-bit stack
    bus.connect()

    # Queue a wire packet: header DLC=3, ID=0x1F5, payload
    header = (0x1F5 << 4) | 3
    mock.rx_queue.append(header.to_bytes(2, "little") + b"\xAA\xBB\xCC")

    frame = bus.recv(timeout_s=0.1)
    assert frame is not None
    assert frame.arbitration_id == 0x1F5
    assert frame.data == b"\xAA\xBB\xCC"
    assert frame.dlc == 3
    assert not frame.is_fd
    assert bus.metrics.rx_frames == 1

    # Empty queue -> clean None within timeout (not an exception)
    assert bus.recv(timeout_s=0.05) is None


def test_rp1210_bus_recv_rejects_truncated_packet() -> None:
    mock = _MockRP1210Client()
    bus = _make_bus(mock)
    bus.connect()

    header = (0x10 << 4) | 6  # claims DLC=6...
    mock.rx_queue.append(header.to_bytes(2, "little") + b"\x01\x02")  # ...only 2 bytes

    # Malformed packet is dropped as None; the bus stays alive for the next read
    assert bus.recv(timeout_s=0.05) is None
    assert bus.is_connected


def test_rp1210_bus_recv_read_error_does_not_kill_loop() -> None:
    class _FailingReadMock(_MockRP1210Client):
        def read_message(self, buffer_size: int = 2048, block: bool = False) -> bytes | None:
            raise HardwareError("transient vendor RX fault")

    bus = _make_bus(_FailingReadMock())
    bus.connect()
    assert bus.recv(timeout_s=0.05) is None  # logged-and-skipped, not raised


def test_build_bus_routes_rp1210_and_rejects_bad_device() -> None:
    """K4-a: the shared factory wires rp1210 to RP1210Bus and validates the
    numeric device id up front instead of failing deep in the driver."""
    from src.hal.rp1210.bus import RP1210Bus
    from src.main import build_bus

    # Bad device id fails fast with a clear message
    with pytest.raises(ValueError, match="numeric device id"):
        build_bus(interface="rp1210", channel="vcan0", bitrate=250000)

    # A numeric id yields the RP1210Bus adapter (with an unloaded real DLL —
    # constructing RP1210Bus loads the DLL; patch it to a mock)
    import unittest.mock as mock

    with mock.patch("src.hal.rp1210.bus.RP1210Client") as mock_client:
        bus = build_bus(interface="rp1210", channel="3", bitrate=500000)
    assert isinstance(bus, RP1210Bus)
    assert bus.device_id == 3
    assert bus.bitrate == 500000
    mock_client.assert_called_once()

    # P0-3 (REVIEW C-4): build_bus must FORWARD listen_only — the factory
    # previously dropped the flag so every RP1210 adapter opened ACTIVE.
    with mock.patch("src.hal.rp1210.bus.RP1210Client"):
        passive = build_bus(interface="rp1210", channel="3", bitrate=500000, listen_only=True)
        active = build_bus(interface="rp1210", channel="3", bitrate=500000, listen_only=False)
    assert passive.listen_only is True
    assert active.listen_only is False

    # Non-rp1210 interfaces still go through python-can
    from src.hal.drivers.pcan_kvaser import PythonCanBus

    classic = build_bus(interface="virtual", channel="vcan0", bitrate=250000)
    assert isinstance(classic, PythonCanBus)
    classic.disconnect()


def test_rp1210_bus_allows_11bit_on_iso15765_stack() -> None:
    """11-bit frames (such as OBD 0x7DF / UDS 0x7E0) must be allowed on ISO 15765."""
    mock = _MockRP1210Client()
    bus = _make_tx_bus(mock, protocol="iso15765")
    bus.connect()

    frame = CanFrame.create(
        channel_id=bus.channel_id, arbitration_id=0x7DF, data=b"\x02\x01\x00", is_extended=False
    )
    bus.send(frame)
    assert len(mock.sent) == 1
    packet = mock.sent[0]
    header = int.from_bytes(packet[:2], "little")
    assert (header >> 4) & 0x7FF == 0x7DF
    assert header & 0x0F == 3
    assert packet[2:] == b"\x02\x01\x00"


# ============================================================================
# P0-3 / H-1 / H-2 regressions (REVIEW C-4, RP1210 RX robustness)
# ============================================================================


def test_rp1210_listen_only_blocks_tx() -> None:
    """P0-3 (REVIEW C-4): a listen-only RP1210 session must refuse send().

    The RP1210 API has no portable listen-only connect flag; refusing TX is
    the only fail-closed guarantee that the adapter never produces ACKs
    against a live vehicle bus unless TX was explicitly armed.
    """
    mock = _MockRP1210Client()
    bus = _make_bus(mock)  # default listen_only=True
    bus.connect()

    frame = CanFrame.create(channel_id=bus.channel_id, arbitration_id=0x18DAF110, data=b"\x02\x10\x00", is_extended=True)
    with pytest.raises(HardwareError, match="Listen-Only"):
        bus.send(frame)
    assert mock.sent == []  # nothing reached the wire

    # An explicitly armed TX session (listen_only=False) transmits again.
    tx_bus = RP1210Bus(device_id=1, protocol="J1939", client=mock, listen_only=False)
    tx_bus.connect()
    tx_bus.send(frame)
    assert len(mock.sent) == 1


def test_rp1210_listen_only_state_is_passive() -> None:
    """P0-3: the metrics state must reflect the listen-only semantics."""
    mock = _MockRP1210Client()
    bus = _make_bus(mock)
    bus.connect()
    assert bus.metrics.state == BusState.PASSIVE

    tx_bus = RP1210Bus(device_id=1, protocol="J1939", client=_MockRP1210Client(), listen_only=False)
    tx_bus.connect()
    assert tx_bus.metrics.state == BusState.ACTIVE


def test_rp1210_recv_drops_dlc9_classic_invalid_packet() -> None:
    """H-1 regression: a wire DLC of 9..15 on the non-FD 29-bit layout must
    be dropped and counted, never raise ValueError out of recv() and kill
    the RX session."""
    mock = _MockRP1210Client()
    bus = _make_bus(mock)
    bus.connect()

    # Extended layout: 4-byte LE id + DLC=9 (classic-invalid) + 9 payload bytes
    wire = 0x18EBFF10 .to_bytes(4, "little") + bytes([9]) + b"\x01" * 9
    mock.rx_queue.append(wire)

    assert bus.recv(timeout_s=0.05) is None  # dropped, not raised
    assert bus.metrics.dropped_frames == 1
    assert bus.is_connected  # RX session survived

    # Next well-formed frame still decodes
    good = 0x18DAF110 .to_bytes(4, "little") + bytes([3]) + b"\x02\x10\x00"
    mock.rx_queue.append(good)
    frame = bus.recv(timeout_s=0.1)
    assert frame is not None and frame.arbitration_id == 0x18DAF110


def test_rp1210_recv_iso15765_runt_packets_dropped_not_misparsed() -> None:
    """H-2 regression: 2-4 byte vendor packets under iso15765/iso_tp must be
    dropped as runts — the old admission threshold let them fall through to
    the 11-bit decoder and fabricated telemetry from garbage bytes."""
    mock = _MockRP1210Client()
    bus = _make_bus(mock, protocol="iso15765")
    bus.connect()

    for runt in (b"\x10", b"\x10\x02", b"\x00\x00\x10"):
        mock.rx_queue.append(runt)
        assert bus.recv(timeout_s=0.05) is None  # dropped, never decoded

    assert bus.metrics.dropped_frames == 3
    assert bus.metrics.rx_frames == 0  # nothing was fabricated as an 11-bit frame

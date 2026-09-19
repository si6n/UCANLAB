"""Tests for Review T62 Chassis [HAL] fixes.

Validates:
- [T62-C1] PythonCanBus & RP1210Bus receive lifetime tracking and drain synchronization in disconnect().
- [T62-C2] UniversalCanDesktopApp.start_replay() routing playback frames through ReplaySafetyFilter.
- [T62-C3] ReplaySafetyFilter CAN-FD escape Single Frame 8 <= sf_dl <= 62 validation.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import can

from src.core.models.can_frame import CanFrame
from src.hal.drivers.pcan_kvaser import PythonCanBus
from src.hal.replay.safety_filter import ReplaySafetyFilter
from src.hal.rp1210.bus import RP1210Bus
from src.hal.rp1210.client import RP1210Client
from src.ui.desktop_app import UniversalCanDesktopApp

# ======================================================================
# [T62-C1] PythonCanBus receive-lifetime tracking & disconnect drain
# ======================================================================

def test_pcan_kvaser_recv_active_recvs_lifetime() -> None:
    """[T62-C1] PythonCanBus.recv() increments _active_recvs and decrements in finally."""
    bus = PythonCanBus(interface="virtual", channel="test_c1_recv", listen_only=False)
    bus.is_connected = True
    mock_can_bus = MagicMock()
    bus._bus = mock_can_bus

    active_during_recv = -1

    def fake_recv(timeout: float | None = None) -> can.Message | None:
        nonlocal active_during_recv
        active_during_recv = bus._active_recvs
        return can.Message(arbitration_id=0x123, is_extended_id=False, data=b"\x01\x02")

    mock_can_bus.recv.side_effect = fake_recv

    assert bus._active_recvs == 0
    frame = bus.recv(timeout_s=0.1)

    assert frame is not None
    assert frame.arbitration_id == 0x123
    assert active_during_recv == 1
    assert bus._active_recvs == 0


def test_pcan_kvaser_disconnect_drains_active_recvs() -> None:
    """[T62-C1] PythonCanBus.disconnect() waits for in-flight recv() to drain before shutdown."""
    bus = PythonCanBus(interface="virtual", channel="test_c1_drain", listen_only=False)
    bus.is_connected = True
    mock_can_bus = MagicMock()
    bus._bus = mock_can_bus

    recv_started = threading.Event()
    recv_release = threading.Event()
    shutdown_called_while_recv_active = False

    def fake_recv(timeout: float | None = None) -> can.Message | None:
        recv_started.set()
        recv_release.wait(timeout=1.0)
        return can.Message(arbitration_id=0x100, is_extended_id=False, data=b"\x00")

    def fake_shutdown() -> None:
        nonlocal shutdown_called_while_recv_active
        if bus._active_recvs > 0:
            shutdown_called_while_recv_active = True

    mock_can_bus.recv.side_effect = fake_recv
    mock_can_bus.shutdown.side_effect = fake_shutdown

    recv_thread = threading.Thread(target=bus.recv, kwargs={"timeout_s": 0.5})
    recv_thread.start()
    assert recv_started.wait(timeout=1.0)

    # Concurrently call disconnect() in separate thread
    disconnect_finished = threading.Event()

    def do_disconnect() -> None:
        bus.disconnect()
        disconnect_finished.set()

    disc_thread = threading.Thread(target=do_disconnect)
    disc_thread.start()

    # Give disconnect time to hit the wait loop
    time.sleep(0.05)
    assert not disconnect_finished.is_set(), "disconnect() should wait for active recv"
    assert bus._bus is not None, "_bus must not be cleared before recv drains"

    # Release recv
    recv_release.set()
    recv_thread.join(timeout=1.0)
    disc_thread.join(timeout=1.0)

    assert disconnect_finished.is_set()
    assert not shutdown_called_while_recv_active
    assert bus._bus is None
    assert bus._active_recvs == 0
    assert bus._active_sends == 0
    mock_can_bus.shutdown.assert_called_once()


def test_pcan_kvaser_disconnect_forces_shutdown_on_deadline() -> None:
    """[T62-C1] PythonCanBus.disconnect() forces shutdown when in-flight operations fail to drain."""
    bus = PythonCanBus(interface="virtual", channel="test_c1_force", listen_only=False)
    bus.is_connected = True
    bus.DISCONNECT_DRAIN_TIMEOUT_S = 0.05
    mock_can_bus = MagicMock()
    bus._bus = mock_can_bus

    # Simulate stranded recv
    with bus._lifecycle_lock:
        bus._active_recvs = 2
        bus._active_sends = 1

    t0 = time.monotonic()
    bus.disconnect()
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.04
    assert bus._active_recvs == 0
    assert bus._active_sends == 0
    assert bus._bus is None
    mock_can_bus.shutdown.assert_called_once()


# ======================================================================
# [T62-C1] RP1210Bus receive-lifetime tracking & disconnect drain
# ======================================================================

def test_rp1210_bus_recv_active_recvs_lifetime() -> None:
    """[T62-C1] RP1210Bus.recv() tracks _active_recvs during execution."""
    mock_client = MagicMock(spec=RP1210Client)
    bus = RP1210Bus(client=mock_client, listen_only=False, protocol="CAN")
    bus.is_connected = True

    active_during_recv = -1

    def fake_read_message(block: bool = False) -> bytes | None:
        nonlocal active_during_recv
        active_during_recv = bus._active_recvs
        # Return 11-bit frame: 2-byte header (dlc=1, id=0x100) + 1-byte payload
        header = (0x100 << 4) | 1
        return header.to_bytes(2, "little") + b"\x42"

    mock_client.read_message.side_effect = fake_read_message

    assert bus._active_recvs == 0
    frame = bus.recv(timeout_s=0.1)

    assert frame is not None
    assert frame.arbitration_id == 0x100
    assert active_during_recv == 1
    assert bus._active_recvs == 0


def test_rp1210_bus_disconnect_drains_active_recvs() -> None:
    """[T62-C1] RP1210Bus.disconnect() waits for in-flight recv() to complete."""
    mock_client = MagicMock(spec=RP1210Client)
    bus = RP1210Bus(client=mock_client, listen_only=False, protocol="CAN")
    bus.is_connected = True

    recv_entered = threading.Event()
    recv_release = threading.Event()
    client_disconnect_during_recv = False

    def fake_read_message(block: bool = False) -> bytes | None:
        recv_entered.set()
        recv_release.wait(timeout=1.0)
        return None

    def fake_disconnect() -> None:
        nonlocal client_disconnect_during_recv
        if bus._active_recvs > 0:
            client_disconnect_during_recv = True

    mock_client.read_message.side_effect = fake_read_message
    mock_client.disconnect.side_effect = fake_disconnect

    recv_thread = threading.Thread(target=bus.recv, kwargs={"timeout_s": 0.5})
    recv_thread.start()
    assert recv_entered.wait(timeout=1.0)

    disconnect_done = threading.Event()

    def do_disconnect() -> None:
        bus.disconnect()
        disconnect_done.set()

    disc_thread = threading.Thread(target=do_disconnect)
    disc_thread.start()

    time.sleep(0.05)
    assert not disconnect_done.is_set(), "RP1210 disconnect() must wait for active recvs"

    recv_release.set()
    recv_thread.join(timeout=1.0)
    disc_thread.join(timeout=1.0)

    assert disconnect_done.is_set()
    assert not client_disconnect_during_recv
    assert bus._active_recvs == 0
    assert bus._active_sends == 0
    mock_client.disconnect.assert_called_once()


def test_rp1210_bus_disconnect_forces_shutdown_on_deadline() -> None:
    """[T62-C1] RP1210Bus.disconnect() forces shutdown on drain deadline expiry."""
    mock_client = MagicMock(spec=RP1210Client)
    bus = RP1210Bus(client=mock_client, listen_only=False)
    bus.is_connected = True
    bus.DISCONNECT_DRAIN_TIMEOUT_S = 0.05

    with bus._lifecycle_lock:
        bus._active_recvs = 1
        bus._active_sends = 1

    t0 = time.monotonic()
    bus.disconnect()
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.04
    assert bus._active_recvs == 0
    assert bus._active_sends == 0
    mock_client.disconnect.assert_called_once()


# ======================================================================
# [T62-C2] ReplaySafetyFilter integrated in desktop_app.start_replay()
# ======================================================================

def test_desktop_app_start_replay_applies_safety_filter() -> None:
    """[T62-C2] start_replay() routes frames through ReplaySafetyFilter, dropping unsafe frames."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)

    # Ingest collector
    ingested_frames: list[CanFrame] = []
    app._ingest_live_frame = MagicMock(side_effect=lambda f: ingested_frames.append(f))

    # Mock ReplayBus
    captured_callback = None

    def fake_play(callback, speed=1.0, stop_event=None, loop=False):
        nonlocal captured_callback
        captured_callback = callback

    mock_bus = MagicMock()
    mock_bus.play.side_effect = fake_play
    mock_bus.frame_count = 5
    app.replay_bus = mock_bus

    res = app.start_replay(speed=1.0, loop=False)
    assert res["success"] is True

    # Allow worker thread to invoke play
    time.sleep(0.05)
    assert captured_callback is not None
    assert app.replay_safety_filter is not None
    assert isinstance(app.replay_safety_filter, ReplaySafetyFilter)

    # 1. Unsafe frame: UDS ECU Reset (SID 0x11) on 0x7E0
    unsafe_reset = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E0,
        data=b"\x02\x11\x01\x00\x00\x00\x00\x00",
        is_extended=False,
    )
    captured_callback(unsafe_reset)
    assert len(ingested_frames) == 0, "Unsafe ECU reset must be dropped by replay filter"

    # 2. Unsafe frame: J1939 TP.CM RTS tunnel
    unsafe_tp = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x1CECFF00,
        data=b"\x10\x0e\x00\x02\xff\xec\xfe\x00",
        is_extended=True,
    )
    captured_callback(unsafe_tp)
    assert len(ingested_frames) == 0, "Unsafe TP.CM tunnel must be dropped by replay filter"

    # 3. Unsafe frame: J1939 Address Claim
    unsafe_claim = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EEFF00,
        data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
        is_extended=True,
    )
    captured_callback(unsafe_claim)
    assert len(ingested_frames) == 0, "Unsafe Address Claim must be dropped by replay filter"

    # 4. Safe frame: harmless normal telemetry
    safe_telemetry = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x100,
        data=b"\x01\x02\x03\x04",
        is_extended=False,
    )
    captured_callback(safe_telemetry)
    assert len(ingested_frames) == 1
    assert ingested_frames[0].arbitration_id == 0x100

    app.stop_replay()


# ======================================================================
# [T62-C3] CAN-FD escape Single Frame 8 <= sf_dl <= 62 check
# ======================================================================

def test_can_fd_sf_escape_dl_bounds_enforced() -> None:
    """[T62-C3] CAN-FD escape SF requires 8 <= sf_dl <= 62; otherwise fail-closed."""
    f = ReplaySafetyFilter()

    # Case 1: sf_dl in 1..7 on FD escape SF (data[0]==0x00, len > 8) must be BLOCKED as UNKNOWN_SID
    for sf_dl in range(1, 8):
        # Even if data[2] is a benign SID (e.g. 0x22 ReadDataByIdentifier), sf_dl < 8 is malformed for escape SF
        payload = bytes([0x00, sf_dl, 0x22]) + b"\x00" * (sf_dl + 6)
        frame = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x7E0,
            data=payload,
            is_fd=True,
        )
        is_safe, reason = f.is_frame_safe(frame)
        assert not is_safe, f"sf_dl={sf_dl} should be blocked on CAN-FD escape SF"
        assert "PROHIBITED_11BIT_UDS_SID" in reason
        assert f.filter_frame(frame) is None

    # Case 2: sf_dl == 0 on FD escape SF
    frame_zero = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E0,
        data=bytes([0x00, 0x00, 0x22]) + b"\x00" * 8,
        is_fd=True,
    )
    is_safe, reason = f.is_frame_safe(frame_zero)
    assert not is_safe
    assert f.filter_frame(frame_zero) is None

    # Case 3: sf_dl > 62 (e.g. 63) in 64-byte frame must be BLOCKED
    frame_63 = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E0,
        data=bytes([0x00, 63]) + b"\x22" * 62,
        is_fd=True,
    )
    is_safe, reason = f.is_frame_safe(frame_63)
    assert not is_safe, "sf_dl=63 exceeds 62-byte limit and must be blocked"
    assert f.filter_frame(frame_63) is None

    # Case 4: Valid sf_dl == 8 with benign SID (0x22) -> PASS
    frame_valid_8 = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E0,
        data=bytes([0x00, 8, 0x22, 0xF1, 0x90, 0x01, 0x02, 0x03, 0x04, 0x05]),
        is_fd=True,
    )
    is_safe, reason = f.is_frame_safe(frame_valid_8)
    assert is_safe is True
    assert f.filter_frame(frame_valid_8) is not None

    # Case 5: Valid sf_dl == 10 with prohibited SID (0x2E WriteDataByIdentifier) -> BLOCKED with 0x2E
    frame_valid_10_prohibited = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E0,
        data=bytes([0x00, 10, 0x2E, 0xF1, 0x90, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]),
        is_fd=True,
    )
    is_safe, reason = f.is_frame_safe(frame_valid_10_prohibited)
    assert is_safe is False
    assert reason == "PROHIBITED_11BIT_UDS_SID: 0x2E"
    assert f.filter_frame(frame_valid_10_prohibited) is None

    # Case 6: Valid sf_dl == 62 with benign SID (0x22) -> PASS
    frame_valid_62 = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E0,
        data=bytes([0x00, 62, 0x22]) + b"\x00" * 61,
        is_fd=True,
    )
    is_safe, reason = f.is_frame_safe(frame_valid_62)
    assert is_safe is True
    assert f.filter_frame(frame_valid_62) is not None

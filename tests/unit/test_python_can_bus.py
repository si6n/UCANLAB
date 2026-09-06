"""Unit tests for python-can driver wrapper."""

import pytest

from src.core.errors import HardwareError
from src.core.models.can_frame import CanFrame
from src.hal.base import BusState
from src.hal.drivers.pcan_kvaser import PythonCanBus


def test_virtual_bus_connect_send_recv() -> None:
    # Safe-by-default regression (CONTRIBUTING.md: listen-only PASSIVE out of
    # the box): the TX-capable path must now be requested explicitly.
    bus1 = PythonCanBus(interface="virtual", channel="vchan0", listen_only=False)
    bus2 = PythonCanBus(interface="virtual", channel="vchan0", listen_only=False)

    bus1.connect()
    bus2.connect()

    try:
        assert bus1.is_connected is True
        assert bus2.is_connected is True

        tx_frame = CanFrame.create(
            channel_id="vchan0",
            arbitration_id=0x18FEEE00,
            data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
        )

        bus1.send(tx_frame)
        rx_frame = bus2.recv(timeout_s=0.5)

        assert rx_frame is not None
        assert rx_frame.arbitration_id == 0x18FEEE00
        assert rx_frame.data == b"\x01\x02\x03\x04\x05\x06\x07\x08"
        assert rx_frame.is_extended is True

        # Check metrics
        assert bus1.metrics.tx_frames == 1
        assert bus2.metrics.rx_frames == 1
    finally:
        bus1.disconnect()
        bus2.disconnect()


def test_listen_only_mode_blocks_tx() -> None:
    bus = PythonCanBus(interface="virtual", channel="vchan_passive", listen_only=True)
    bus.connect()

    try:
        frame = CanFrame.create(channel_id="vchan", arbitration_id=0x123, data=b"\x00")
        with pytest.raises(HardwareError, match="Listen-Only"):
            bus.send(frame)
    finally:
        bus.disconnect()


def test_listen_only_passes_busstate_enum_to_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    # D1 regression: vendor drivers check `state in [BusState.ACTIVE, BusState.PASSIVE]`;
    # BusState is a plain Enum, so the former string "PASSIVE" raised ValueError
    # and silently broke listen-only connections and bitrate scanning.
    import can as can_module

    captured: dict[str, object] = {}

    class _FakeBus:
        # REVIEW.md 2.2: the driver verifies the backend honoured PASSIVE —
        # expose the state the pcan driver would report.
        state = can_module.BusState.PASSIVE

        def shutdown(self) -> None:
            pass

    def _fake_bus_factory(**kwargs: object) -> _FakeBus:
        captured.update(kwargs)
        return _FakeBus()

    monkeypatch.setattr(can_module, "Bus", _fake_bus_factory)

    bus = PythonCanBus(interface="pcan", channel="PCAN_USBBUS1", listen_only=True)
    bus.connect()

    assert bus.is_connected is True
    assert captured["state"] is can_module.BusState.PASSIVE
    assert not isinstance(captured["state"], str)


def test_rtr_frames_counted_separately_and_never_trip_bus_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # B5 (REVIEW): RTR frames are protocol-legal Classic CAN traffic, not
    # hardware errors. Dropping them is required (data=b'' cannot satisfy the
    # DLC invariant), but they must land in rtr_frames — never error_frames —
    # so 128+ RTR polls on a healthy bus cannot fake a BUS_OFF state and
    # trigger the E-Stop path.
    import can as can_module

    class _FakeRtrMessage:
        is_error_frame = False
        is_remote_frame = True
        is_extended_id = False
        is_fd = False
        bitrate_switch = False
        error_state_indicator = False
        arbitration_id = 0x7E8
        dlc = 8
        data = b""
        timestamp = 0.0

    class _FakeRxBus:
        state = can_module.BusState.PASSIVE
        _rtr_msg = _FakeRtrMessage()

        def recv(self, timeout: object = None) -> "_FakeRtrMessage":
            return self._rtr_msg

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(can_module, "Bus", lambda **kwargs: _FakeRxBus())

    bus = PythonCanBus(interface="pcan", channel="PCAN_USBBUS1", listen_only=True)
    bus.connect()

    # Flood the driver with more frames than the BUS_OFF threshold
    flood = PythonCanBus.ERROR_FRAMES_BUS_OFF_THRESHOLD * 3
    for _ in range(flood):
        assert bus.recv(timeout_s=0.01) is None

    assert bus.metrics.rtr_frames == flood
    assert bus.metrics.error_frames == 0
    assert bus.metrics.state is not BusState.BUS_OFF
    bus.disconnect()

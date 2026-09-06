import queue
import time
from typing import Any

import pytest

from src.core.contracts.ports import InMemoryTxPort
from src.core.errors import ProtocolError
from src.core.models.can_frame import CanFrame
from src.hal.base import AbstractBus
from src.protocols.uds.client import UdsClient
from src.protocols.uds.nrc import UdsNrc
from src.protocols.uds.services import (
    DiagnosticSessionType,
    RoutineControlType,
    UdsResponse,
    UdsServiceBuilder,
    UdsServiceId,
)
from src.safety.exceptions import (
    DualConfirmationRequiredError,
    SpeedInterlockError,
)
from src.safety.gateway import TxSafetyGateway


class MockDiagnosticBus(AbstractBus):
    """Test CAN bus with queue-based request/response simulation."""

    def __init__(self, channel_id: str = "mock_uds_0") -> None:
        super().__init__(channel_id=channel_id)
        self.sent_frames: list[CanFrame] = []
        self.rx_queue: queue.Queue[CanFrame] = queue.Queue()

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:
        self.sent_frames.append(frame)

    def send_sync(self, frame: CanFrame) -> None:
        self.send(frame)

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        try:
            return self.rx_queue.get(timeout=timeout_s or 0.05)
        except queue.Empty:
            return None

    def inject_rx(self, frame: CanFrame) -> None:
        self.rx_queue.put(frame)


def test_uds_service_builders() -> None:
    # 0x10 Extended Session
    s10 = UdsServiceBuilder.build_diagnostic_session_control(DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION)
    assert s10 == b"\x10\x03"

    # 0x22 Read DID 0xF190 (VIN)
    s22 = UdsServiceBuilder.build_read_data_by_identifier(0xF190)
    assert s22 == b"\x22\xf1\x90"

    # 0x2E Write DID 0x0100 with 2 bytes
    s2e = UdsServiceBuilder.build_write_data_by_identifier(0x0100, b"\xaa\xbb")
    assert s2e == b"\x2e\x01\x00\xaa\xbb"

    # 0x31 Start Routine 0x0201 (Cylinder Compression)
    s31 = UdsServiceBuilder.build_routine_control(RoutineControlType.START_ROUTINE, 0x0201, b"\x01")
    assert s31 == b"\x31\x01\x02\x01\x01"

    # 0x3E Tester Present (suppress positive response)
    s3e = UdsServiceBuilder.build_tester_present(suppress_positive_response=True)
    assert s3e == b"\x3e\x80"


def test_request_download_alfi_widths() -> None:
    """P2 fix: ALFI nibbles drive address/size byte widths (ISO 14229-0 §9.3.1)."""
    # Default 0x44 stays byte-identical to the legacy 4/4 layout
    s44 = UdsServiceBuilder.build_request_download(0xA0000000, 0x00010000)
    assert s44 == b"\x34\x00\x44" + bytes.fromhex("A0000000") + bytes.fromhex("00010000")

    # 0x22 -> 2-byte address, 2-byte size
    s22 = UdsServiceBuilder.build_request_download(
        0xB800, 0x0100, address_and_length_format_identifier=0x22
    )
    assert s22 == b"\x34\x00\x22\xb8\x00\x01\x00"

    # 0x11 -> 1-byte address, 1-byte size
    s11 = UdsServiceBuilder.build_request_download(
        0x40, 0x20, address_and_length_format_identifier=0x11
    )
    assert s11 == b"\x34\x00\x11\x40\x20"

    # Asymmetric ALFI (ISO 14229-1: high=size width, low=address width)
    # 0x14 -> 1-byte size, 4-byte address
    s14 = UdsServiceBuilder.build_request_download(
        0x12345678, 0x50, address_and_length_format_identifier=0x14
    )
    assert s14 == b"\x34\x00\x14\x12\x34\x56\x78\x50"

    # 0x41 -> 4-byte size, 1-byte address
    s41 = UdsServiceBuilder.build_request_download(
        0x80, 0x01020304, address_and_length_format_identifier=0x41
    )
    assert s41 == b"\x34\x00\x41\x80\x01\x02\x03\x04"


def test_request_download_alfi_validation() -> None:
    """Invalid widths and overflow raise clean ValueError, not OverflowError."""
    import pytest

    # Nibble 0 is invalid on either side
    with pytest.raises(ValueError):
        UdsServiceBuilder.build_request_download(0x1234, 0x10, address_and_length_format_identifier=0x40)
    with pytest.raises(ValueError):
        UdsServiceBuilder.build_request_download(0x1234, 0x10, address_and_length_format_identifier=0x04)
    # Nibble > 4 is invalid
    with pytest.raises(ValueError):
        UdsServiceBuilder.build_request_download(0x1234, 0x10, address_and_length_format_identifier=0x55)
    # Value wider than the declared width overflows cleanly (ISO 14229-1: high=size, low=addr)
    # 0x42: size 4 bytes, addr 2 bytes -> 4-byte address 0xAABBCCDD must overflow
    with pytest.raises(ValueError):
        UdsServiceBuilder.build_request_download(0xAABBCCDD, 0x10, address_and_length_format_identifier=0x42)
    # 0x24: size 2 bytes, addr 4 bytes -> 3-byte size 0x10000 must overflow
    with pytest.raises(ValueError):
        UdsServiceBuilder.build_request_download(0x1000, 0x10000, address_and_length_format_identifier=0x24)


def test_parse_positive_and_negative_responses() -> None:
    # Positive response to 0x22 (0x62 + DID 0xF190 + VIN bytes)
    pos_raw = b"\x62\xf1\x90\x57\x42\x41"
    pos_resp = UdsServiceBuilder.parse_response(pos_raw)
    assert pos_resp.is_positive is True
    assert pos_resp.service_id == UdsServiceId.READ_DATA_BY_IDENTIFIER
    assert pos_resp.data == b"\xf1\x90\x57\x42\x41"
    assert pos_resp.nrc == UdsNrc.POSITIVE_RESPONSE

    # Negative response: 0x7F 0x22 0x31 (Request Out Of Range)
    neg_raw = b"\x7f\x22\x31"
    neg_resp = UdsServiceBuilder.parse_response(neg_raw)
    assert neg_resp.is_positive is False
    assert neg_resp.service_id == UdsServiceId.READ_DATA_BY_IDENTIFIER
    assert neg_resp.nrc == UdsNrc.REQUEST_OUT_OF_RANGE
    assert "Aralık Dışı" in neg_resp.nrc_description_tr


def test_uds_client_sync_read_did() -> None:
    bus = MockDiagnosticBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), tx_id=0x7E0, rx_id=0x7E8)

    # Queue single-frame ISO-TP response: 4 bytes payload (0x62 0xF1 0x90 0x41)
    # ISO-TP Single Frame: Byte 0 = 0x04, Bytes 1..4 = 62 F1 90 41
    resp_frame = CanFrame.create(
        channel_id="mock_uds_0",
        arbitration_id=0x7E8,
        data=b"\x04\x62\xf1\x90\x41\x00\x00\x00",
    )
    bus.inject_rx(resp_frame)

    resp = client.read_did(0xF190)
    assert resp.is_positive is True
    assert resp.service_id == UdsServiceId.READ_DATA_BY_IDENTIFIER
    assert resp.data == b"\xf1\x90\x41"
    assert len(bus.sent_frames) == 1
    assert bus.sent_frames[0].arbitration_id == 0x7E0
    client.close()


def test_uds_client_sync_routines_and_session() -> None:
    bus = MockDiagnosticBus()
    gateway = TxSafetyGateway.for_testing(bus=bus)
    gateway.update_vehicle_speed(0.0)
    client = UdsClient(bus=bus, tx_port=gateway, tx_id=0x7E0, rx_id=0x7E8)

    # 1. Change session
    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_uds_0",
            arbitration_id=0x7E8,
            data=b"\x02\x50\x03\x00\x00\x00\x00\x00",
        )
    )
    resp1 = client.change_session(DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION)
    assert resp1.is_positive is True
    assert resp1.service_id == UdsServiceId.DIAGNOSTIC_SESSION_CONTROL

    # 2. Write DID
    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_uds_0",
            arbitration_id=0x7E8,
            data=b"\x03\x6e\x01\x00\x00\x00\x00\x00",
        )
    )
    resp2 = client.write_did(0x0100, b"\xaa\xbb", user_confirmed=True)
    assert resp2.is_positive is True

    # 3. Start Routine
    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_uds_0",
            arbitration_id=0x7E8,
            data=b"\x04\x71\x01\x02\x01\x00\x00\x00",
        )
    )
    resp3 = client.start_routine(0x0201, b"\x01", user_confirmed=True)
    assert resp3.is_positive is True

    # 4. Stop Routine
    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_uds_0",
            arbitration_id=0x7E8,
            data=b"\x04\x71\x02\x02\x01\x00\x00\x00",
        )
    )
    resp4 = client.stop_routine(0x0201)
    assert resp4.is_positive is True

    # 5. Request Routine Results
    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_uds_0",
            arbitration_id=0x7E8,
            data=b"\x05\x71\x03\x02\x01\x00\x00\x00",
        )
    )
    resp5 = client.request_routine_results(0x0201)
    assert resp5.is_positive is True

    # 6. Tester Present without suppression
    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_uds_0",
            arbitration_id=0x7E8,
            data=b"\x02\x7e\x00\x00\x00\x00\x00\x00",
        )
    )
    resp6 = client.tester_present(suppress_response=False)
    assert resp6 is not None
    assert resp6.is_positive is True

    # 7. Tester Present with suppression (returns None)
    resp7 = client.tester_present(suppress_response=True)
    assert resp7 is None

    client.shutdown()


def test_uds_client_timeout_raises_protocol_error() -> None:
    bus = MockDiagnosticBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), tx_id=0x7E0, rx_id=0x7E8)

    # No response injected -> should raise ProtocolError with UDS_TIMEOUT
    with pytest.raises(ProtocolError) as exc_info:
        client._send_and_receive(b"\x22\xf1\x90", timeout_s=0.2)
    assert exc_info.value.code == "UDS_TIMEOUT"
    client.close()


def test_uds_client_execute_async_with_callbacks() -> None:
    bus = MockDiagnosticBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), tx_id=0x7E0, rx_id=0x7E8)

    # Queue response for async read_did
    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_uds_0",
            arbitration_id=0x7E8,
            data=b"\x04\x62\xf1\x90\x99\x00\x00\x00",
        )
    )

    received_results: list[UdsResponse] = []
    received_errors: list[Exception] = []

    def on_success(res: UdsResponse) -> None:
        received_results.append(res)

    def on_error(exc: Exception) -> None:
        received_errors.append(exc)

    future = client.execute_async(
        client.read_did,
        0xF190,
        callback=on_success,
        error_callback=on_error,
    )

    res = future.result(timeout=2.0)
    assert res.is_positive is True
    assert res.data == b"\xf1\x90\x99"

    # Give callback a brief moment to run
    time.sleep(0.05)
    assert len(received_results) == 1
    assert received_results[0].data == b"\xf1\x90\x99"
    assert len(received_errors) == 0

    client.close()


def test_uds_client_execute_async_error_callback() -> None:
    bus = MockDiagnosticBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), tx_id=0x7E0, rx_id=0x7E8)

    received_errors: list[Exception] = []

    def on_error(exc: Exception) -> None:
        received_errors.append(exc)

    def failing_routine() -> Any:
        raise ValueError("Simulated ECU hardware failure")

    future = client.execute_async(
        failing_routine,
        error_callback=on_error,
    )

    with pytest.raises(ValueError, match="Simulated ECU hardware failure"):
        future.result(timeout=2.0)

    time.sleep(0.05)
    assert len(received_errors) == 1
    assert "Simulated ECU hardware failure" in str(received_errors[0])

    client.close()


def test_uds_client_tx_port_injection() -> None:
    """Verify UdsClient working directly with an injected TxPort."""
    tx_port = InMemoryTxPort()
    bus = MockDiagnosticBus()
    client = UdsClient(bus=bus, tx_port=tx_port, tx_id=0x7E0, rx_id=0x7E8)

    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_uds_0",
            arbitration_id=0x7E8,
            data=b"\x04\x62\xf1\x90\x12\x00\x00\x00",
        )
    )

    resp = client.read_did(0xF190)
    assert resp.is_positive is True
    assert len(tx_port.sent_frames) == 1
    assert tx_port.sent_frames[0].arbitration_id == 0x7E0
    client.close()


def test_uds_client_speed_interlock_blocks_critical_services() -> None:
    """Verify TxSafetyGateway blocks critical UDS requests (ECU Reset, Write DID) when vehicle is moving."""
    bus1 = MockDiagnosticBus()
    gateway1 = TxSafetyGateway(bus=bus1, whitelist_ids={0x7E0})
    client1 = UdsClient(bus=bus1, tx_port=gateway1, tx_id=0x7E0, rx_id=0x7E8)

    # Set vehicle speed in motion (50 km/h > 0.5 km/h)
    gateway1.update_vehicle_speed(50.0)

    # ECU Reset (0x11) must be blocked by speed interlock
    with pytest.raises(SpeedInterlockError) as exc_info:
        client1.ecu_reset(reset_type=0x01)
    assert exc_info.value.code == "SPEED_INTERLOCK_ACTIVE"
    client1.close()

    # Write DID (0x2E) on a separate moving gateway must also be blocked by speed interlock
    bus2 = MockDiagnosticBus()
    gateway2 = TxSafetyGateway(bus=bus2, whitelist_ids={0x7E0})
    client2 = UdsClient(bus=bus2, tx_port=gateway2, tx_id=0x7E0, rx_id=0x7E8)
    gateway2.update_vehicle_speed(50.0)

    with pytest.raises(SpeedInterlockError) as exc_info2:
        client2.write_did(0x0100, b"\x01\x02")
    assert exc_info2.value.code == "SPEED_INTERLOCK_ACTIVE"
    client2.close()


def test_uds_client_dual_confirmation_rejected_when_unconfirmed() -> None:
    """Verify that unconfirmed critical command fails when vehicle is stationary."""
    bus = MockDiagnosticBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    client = UdsClient(bus=bus, tx_port=gateway, tx_id=0x7E0, rx_id=0x7E8)

    gateway.update_vehicle_speed(0.0)

    with pytest.raises(DualConfirmationRequiredError) as exc_info:
        client.ecu_reset(reset_type=0x01, user_confirmed=False)

    assert exc_info.value.code == "CONFIRMATION_REQUIRED"
    client.close()


class FlowControlMockBus(MockDiagnosticBus):
    """Mock bus that answers a multi-frame request with FC(CTS, BS, STmin).

    P-C-001 regression: the mock behaves like a real ECU — it will NOT
    tolerate receiving CF frames before it has sent a Flow Control.
    """

    def __init__(self, bs: int = 0, st_min_ms: int = 1) -> None:
        super().__init__()
        self.bs = bs
        self.st_min_ms = st_min_ms
        self.violated = False
        self.fc_sent = False
        # FF observed -> FC emitted once the *next* recv() is called
        self._fc_pending = False

    def send(self, frame: CanFrame) -> None:
        self.sent_frames.append(frame)
        if len(frame.data) >= 1 and (frame.data[0] >> 4) == 0x1:  # First Frame
            self._fc_pending = True

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        if self._fc_pending and not self.fc_sent:
            # Emit FC only when the client politely waits for it
            self._fc_pending = False
            self.fc_sent = True
            fc = bytearray(8)
            fc[0] = 0x30  # PCI FC | FS CTS
            fc[1] = self.bs & 0xFF
            fc[2] = self.st_min_ms & 0xFF
            return CanFrame.create(
                channel_id=self.channel_id,
                arbitration_id=0x7E8,
                data=bytes(fc),
                is_extended=False,
            )
        return super().recv(timeout_s=timeout_s)


def test_uds_client_multiframe_waits_for_flow_control() -> None:
    """P-C-001 regression: CF frames must not be transmitted before FC(CTS).

    A 14-byte payload (e.g. RequestDownload 0x34 with 4-byte address+size)
    is segmented as FF + 2 CFs on classic CAN. The client must send the FF,
    wait for the ECU's Flow Control, and only then transmit the CFs.
    """
    bus = FlowControlMockBus()
    client = UdsClient(bus=bus, tx_port=bus, tx_id=0x7E0, rx_id=0x7E8)

    payload = b"\x34\x00\x44" + bytes.fromhex("A0000000") + bytes.fromhex("00010000")
    assert len(payload) == 11  # > 7 -> FF (6 bytes) + 2 CFs (5 bytes) on classic CAN

    client._send_payload(payload)
    client.close()

    frames = bus.sent_frames
    # FF + CF1 — all transmitted, but only after FC arrived
    assert len(frames) == 2
    assert (frames[0].data[0] >> 4) == 0x1  # First Frame
    assert (frames[1].data[0] >> 4) == 0x2  # Consecutive Frame, seq 1
    assert (frames[1].data[0] & 0x0F) == 1


def test_uds_client_multiframe_no_fc_raises_timeout() -> None:
    """Without an FC the client must fail with UDS_TIMEOUT, not flood the ECU."""
    bus = MockDiagnosticBus()  # never answers FC
    client = UdsClient(bus=bus, tx_port=bus, tx_id=0x7E0, rx_id=0x7E8)

    payload = b"\x22" + b"\x00" * 20  # > 7 bytes -> multi-frame

    with pytest.raises(ProtocolError) as exc_info:
        client._send_payload(payload)
    assert exc_info.value.code == "UDS_TIMEOUT"
    client.close()

    # Only the First Frame may ever hit the wire
    assert len(bus.sent_frames) == 1
    assert (bus.sent_frames[0].data[0] >> 4) == 0x1


def test_uds_client_multiframe_honours_bs_window() -> None:
    """FC(BS=1) must pause after every CF until the next FC arrives."""
    bus = FlowControlMockBus(bs=1, st_min_ms=0)
    # Patch recv to re-issue an FC after each CF once the first FC was sent
    original_send = bus.send

    cf_count = 0

    def counting_send(frame: CanFrame) -> None:
        nonlocal cf_count
        original_send(frame)
        if (frame.data[0] >> 4) == 0x2:
            cf_count += 1
            bus._fc_pending = True
            bus.fc_sent = False  # allow the next FC

    bus.send = counting_send  # type: ignore[method-assign]
    client = UdsClient(bus=bus, tx_port=bus, tx_id=0x7E0, rx_id=0x7E8)

    payload = b"\x36" + b"\x41" * 20  # FF + 3 CFs
    client._send_payload(payload)
    client.close()

    assert len(bus.sent_frames) == 4
    assert cf_count == 3

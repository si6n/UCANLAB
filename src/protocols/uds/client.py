"""High-Level ISO 14229 UDS Diagnostic Client Engine."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from src.core.contracts.ports import RxSubscription, TxPort
from src.core.errors import ProtocolError
from src.core.logging import get_logger
from src.protocols.uds.isotp import IsoTpTransport, decode_st_min
from src.protocols.uds.nrc import UdsNrc
from src.protocols.uds.services import (
    DiagnosticSessionType,
    RoutineControlType,
    UdsResponse,
    UdsServiceBuilder,
)

if TYPE_CHECKING:
    from src.core.models.can_frame import CanFrame
    from src.hal.base import AbstractBus

logger = get_logger("protocols.uds.client")

# ISO 14229 P2* server: extended response window granted per NRC 0x78 (F-14)
P2_STAR_TIMEOUT_S = 5.0


class UdsClient:
    """Synchronous / Non-blocking ISO 14229 Diagnostic Client over ISO-TP.

    Routes all CAN frame transmissions through the TxPort / TxSafetyGateway choke-point.
    """

    def __init__(
        self,
        bus: AbstractBus | None = None,
        tx_port: TxPort | None = None,
        rx_sub: RxSubscription | None = None,
        tx_id: int = 0x7E0,
        rx_id: int = 0x7E8,
        channel_id: str = "uds_ch0",
        max_workers: int = 4,
    ) -> None:
        if tx_port is not None:
            self.tx_port: TxPort = tx_port
        elif bus is not None:
            if isinstance(bus, TxPort):
                self.tx_port = bus
            else:
                # Fail closed (F-21): an implicit gateway must not bypass the
                # whitelist — pass an explicit tx_port to customize policy.
                raise ValueError(
                    "tx_port is required — use TxSafetyGateway with an explicit whitelist "
                    "instead of an implicit permissive fallback"
                )
        else:
            raise ValueError("Either bus or tx_port must be provided to UdsClient")

        # P4: without any receive path every request/response exchange would
        # silently run into UDS_TIMEOUT — fail fast instead.
        if bus is None and rx_sub is None:
            raise ValueError(
                "UdsClient has no receive path — provide a bus (synchronous "
                "request/response) or an rx_sub (asynchronous one-shot queries)"
            )

        self.bus = bus
        self.rx_sub = rx_sub
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.channel_id = channel_id
        self.transport = IsoTpTransport(tx_id=tx_id, rx_id=rx_id, channel_id=channel_id)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="uds_client")
        # M-07: UDS exchanges are stateful (session type, security seed/key
        # ladder) — async and sync operations are serialized with an RLock so
        # concurrent calls cannot corrupt another exchange's request/response.
        self._operation_lock = threading.RLock()

    def execute_async(
        self,
        fn: Callable[..., Any],
        *args: Any,
        callback: Callable[[Any], None] | None = None,
        error_callback: Callable[[Exception], None] | None = None,
        **kwargs: Any,
    ) -> concurrent.futures.Future[Any]:
        """Execute a diagnostic routine asynchronously in the background thread pool.

        M-7: the wrapped routine runs under the client-wide operation lock —
        the UDS request/response dialogue (including any multi-frame FC
        handshakes) is a single serialized conversation with the ECU.
        """

        def _worker() -> Any:
            try:
                with self._operation_lock:
                    result = fn(*args, **kwargs)
                if callback is not None:
                    try:
                        callback(result)
                    except Exception as cb_err:
                        logger.error("Error in UdsClient async callback", extra={"error": str(cb_err)})
                return result
            except Exception as exc:
                if error_callback is not None:
                    try:
                        error_callback(exc)
                    except Exception as err_cb_err:
                        logger.error("Error in UdsClient async error_callback", extra={"error": str(err_cb_err)})
                raise

        return self._executor.submit(_worker)

    def close(self) -> None:
        """Close client and terminate worker threads."""
        self.shutdown(wait=True)

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown underlying thread pool executor."""
        self._executor.shutdown(wait=wait)

    def change_session(
        self,
        session_type: DiagnosticSessionType,
        user_confirmed: bool = False,
    ) -> UdsResponse:
        """Switch diagnostic session (0x10)."""
        is_critical = session_type in (
            DiagnosticSessionType.PROGRAMMING_SESSION,
            DiagnosticSessionType.SAFETY_SYSTEM_DIAGNOSTIC_SESSION,
        )
        req_payload = UdsServiceBuilder.build_diagnostic_session_control(session_type)
        return self._send_and_receive(
            req_payload, is_critical_command=is_critical, user_confirmed=user_confirmed
        )

    def security_access_request_seed(self, level: int = 1) -> UdsResponse:
        """Request Security Access Seed (0x27)."""
        req_payload = UdsServiceBuilder.build_security_access_request_seed(level=level)
        return self._send_and_receive(req_payload)

    def security_access_send_key(self, level: int, key: bytes) -> UdsResponse:
        """Send Security Access Key (0x27)."""
        req_payload = UdsServiceBuilder.build_security_access_send_key(level=level, key=key)
        return self._send_and_receive(req_payload)

    def read_did(self, did: int) -> UdsResponse:
        """Read Data Identifier (0x22)."""
        req_payload = UdsServiceBuilder.build_read_data_by_identifier(did)
        return self._send_and_receive(req_payload)

    def write_did(self, did: int, data: bytes, user_confirmed: bool = False) -> UdsResponse:
        """Write Data Identifier (0x2E) - Critical command.

        Requires explicit operator confirmation; dual confirmation is NOT
        granted by default.
        """
        req_payload = UdsServiceBuilder.build_write_data_by_identifier(did, data)
        return self._send_and_receive(req_payload, is_critical_command=True, user_confirmed=user_confirmed)

    def request_download(
        self,
        memory_address: int,
        memory_size: int,
        data_format_identifier: int = 0x00,
        address_and_length_format_identifier: int = 0x44,
        user_confirmed: bool = False,
    ) -> UdsResponse:
        """Request Download (0x34) - Critical command.

        Requires explicit operator confirmation; dual confirmation is NOT
        granted by default.
        """
        req_payload = UdsServiceBuilder.build_request_download(
            memory_address=memory_address,
            memory_size=memory_size,
            data_format_identifier=data_format_identifier,
            address_and_length_format_identifier=address_and_length_format_identifier,
        )
        return self._send_and_receive(req_payload, is_critical_command=True, user_confirmed=user_confirmed)

    def transfer_data(
        self,
        block_sequence: int,
        data: bytes,
        is_critical_command: bool = True,
        user_confirmed: bool = True,
    ) -> UdsResponse:
        """Transfer Data Block (0x36) - Memory write is safety-critical."""
        req_payload = UdsServiceBuilder.build_transfer_data(block_sequence=block_sequence, data=data)
        return self._send_and_receive(
            req_payload, is_critical_command=is_critical_command, user_confirmed=user_confirmed
        )

    def request_transfer_exit(
        self,
        is_critical_command: bool = True,
        user_confirmed: bool = True,
    ) -> UdsResponse:
        """Request Transfer Exit (0x37)."""
        req_payload = UdsServiceBuilder.build_request_transfer_exit()
        return self._send_and_receive(
            req_payload, is_critical_command=is_critical_command, user_confirmed=user_confirmed
        )

    def ecu_reset(self, reset_type: int = 0x01, user_confirmed: bool = False) -> UdsResponse:
        """ECU Reset (0x11) - Critical command.

        Requires explicit operator confirmation; dual confirmation is NOT
        granted by default.
        """
        req_payload = UdsServiceBuilder.build_ecu_reset(reset_type=reset_type)
        return self._send_and_receive(req_payload, is_critical_command=True, user_confirmed=user_confirmed)

    def start_routine(self, routine_id: int, options: bytes = b"", user_confirmed: bool = False) -> UdsResponse:
        """Start ECU Routine (0x31) - Critical command.

        Requires explicit operator confirmation; dual confirmation is NOT
        granted by default.
        """
        req_payload = UdsServiceBuilder.build_routine_control(RoutineControlType.START_ROUTINE, routine_id, options)
        return self._send_and_receive(req_payload, is_critical_command=True, user_confirmed=user_confirmed)

    def stop_routine(self, routine_id: int) -> UdsResponse:
        """Stop ECU Routine (0x31)."""
        req_payload = UdsServiceBuilder.build_routine_control(RoutineControlType.STOP_ROUTINE, routine_id)
        return self._send_and_receive(req_payload)

    def request_routine_results(self, routine_id: int) -> UdsResponse:
        """Query ECU Routine Results (0x31)."""
        req_payload = UdsServiceBuilder.build_routine_control(RoutineControlType.REQUEST_ROUTINE_RESULTS, routine_id)
        return self._send_and_receive(req_payload)

    def tester_present(self, suppress_response: bool = False) -> UdsResponse | None:
        """Send Tester Present keep-alive (0x3E)."""
        req_payload = UdsServiceBuilder.build_tester_present(suppress_response)
        if suppress_response:
            with self._operation_lock:
                self._send_payload(req_payload)
            return None
        return self._send_and_receive(req_payload)

    def _send_payload(
        self,
        payload: bytes,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
    ) -> None:
        """Transmit a UDS payload through the TxPort.

        P-C-001 fix: multi-frame payloads are sent Flow-Control aware —
        the First Frame goes out, then the sender waits (N_Bs) for the
        ECU's FC(CTS) before transmitting Consecutive Frames, honouring
        BS windowing and STmin pacing as ISO 15765-2 requires. Single
        frames go out unchanged.
        """
        frames = self.transport.segment_message(payload)

        if len(frames) <= 1:
            for frame in frames:
                self._tx_frame(frame, is_critical_command, user_confirmed)
            return

        # Multi-frame: FF first, then FC-gated CF transmission.
        self._tx_frame(frames[0], is_critical_command, user_confirmed)
        self._send_consecutive_frames_flow_controlled(
            frames[1:], payload, is_critical_command, user_confirmed
        )

    def _tx_frame(
        self,
        frame: CanFrame,
        is_critical_command: bool,
        user_confirmed: bool,
        budget_category: str = "protocol_burst",
    ) -> None:
        """Send one frame through the gateway TxPort choke-point.

        P1-9: ISO-TP frames (FF + CF trains) travel on the 'protocol_burst'
        lane by default — a 256-byte 0x36 block is ~37 CFs which would
        otherwise slam into the 100 msg/s default-lane wall and, pre-P1-9,
        engage a permanent E-Stop (self-DoS mid-flash).
        """
        if hasattr(self.tx_port, "validate_and_transmit"):
            self.tx_port.validate_and_transmit(
                frame,
                is_critical_command=is_critical_command,
                user_confirmed=user_confirmed,
                budget_category=budget_category,
            )
        elif hasattr(self.tx_port, "send_sync") and not hasattr(self.tx_port, "validate_and_transmit"):
            # TxSafetyGateway-shaped port: honour the category-aware lane.
            try:
                self.tx_port.send_sync(frame, budget_category=budget_category)  # type: ignore[call-arg]
            except TypeError:
                # Plain TxPort (send_sync(frame) only) — fall back.
                self.tx_port.send_sync(frame)
        else:
            self.tx_port.send_sync(frame)

    # N_Bs (ISO 15765-2 §4.6.2): max wait for FC after FF/before next CF
    N_BS_TIMEOUT_S: ClassVar[float] = 1.0

    def _await_flow_control_sync(self, timeout_s: float | None = None) -> CanFrame | None:
        """Block until a Flow Control frame from the ECU arrives (N_Bs bound)."""
        deadline_budget = self.N_BS_TIMEOUT_S if timeout_s is None else timeout_s
        start = time.monotonic()
        while (time.monotonic() - start) < deadline_budget:
            rx_frame = None
            if self.bus is not None:
                remaining = deadline_budget - (time.monotonic() - start)
                rx_frame = self.bus.recv(timeout_s=min(0.05, max(0.001, remaining)))
            if rx_frame is None:
                continue

            if rx_frame.arbitration_id != self.rx_id or len(rx_frame.data) < 3:
                continue
            if (rx_frame.data[0] >> 4) == 0x3:  # PCI_FLOW_CONTROL
                return rx_frame
        return None

    def _send_consecutive_frames_flow_controlled(
        self,
        cf_frames: list[CanFrame],
        payload: bytes,
        is_critical_command: bool,
        user_confirmed: bool,
    ) -> None:
        """Send CFs after FC(CTS), honouring BS windowing and STmin pacing."""
        total = len(cf_frames)
        idx = 0
        wft_count = 0
        WFT_MAX = 16

        while idx < total:
            fc = self._await_flow_control_sync()
            if fc is None:
                raise ProtocolError(
                    "ISO-TP N_Bs timeout waiting for Flow Control after First Frame",
                    code="UDS_TIMEOUT",
                    details={"tx_id": hex(self.tx_id), "rx_id": hex(self.rx_id)},
                )

            fs = fc.data[0] & 0x0F
            if fs == 0x1:  # FS_WAIT
                wft_count += 1
                if wft_count > WFT_MAX:
                    raise ProtocolError(
                        f"ISO-TP WFTmax exceeded ({wft_count} consecutive WAIT frames)",
                        code="UDS_TIMEOUT",
                        details={"wft_count": wft_count},
                    )
                continue
            if fs == 0x2:  # FS_OVERFLOW
                raise ProtocolError(
                    "ECU reported ISO-TP buffer overflow (FlowStatus.OVERFLOW)",
                    code="UDS_BUFFER_OVERFLOW",
                    details={"requested_length": len(payload)},
                )
            if fs != 0x0:  # must be CTS
                raise ProtocolError(
                    f"Invalid ISO-TP FlowStatus 0x{fs:X}",
                    code="UDS_PROTOCOL_ERROR",
                    details={"flow_status": fs},
                )

            wft_count = 0
            bs = fc.data[1]
            st_min = fc.data[2]

            block_sent = 0
            while idx < total:
                if bs > 0 and block_sent >= bs:
                    break  # block exhausted — wait for the next FC
                delay_ms = decode_st_min(st_min)
                if delay_ms > 0:
                    time.sleep(delay_ms / 1000.0)
                self._tx_frame(cf_frames[idx], is_critical_command, user_confirmed)
                idx += 1
                block_sent += 1

    def _send_and_receive(
        self,
        payload: bytes,
        timeout_s: float = 2.0,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
    ) -> UdsResponse:
        """Send segmented UDS request and wait for complete ISO-TP reassembled response.

        A negative response NRC 0x78 (Response Pending) extends the wait window
        by P2* (F-14) instead of surfacing as a timeout while the ECU is working.
        """
        # P4: the synchronous path reads frames from the bus; an rx_sub-only
        # client must use the asynchronous one-shot query API instead.
        if self.bus is None:
            raise ProtocolError(
                "Synchronous UDS request/response requires a bus; this client "
                "was constructed with an rx_sub only",
                code="UDS_NO_RX_PATH",
                details={"tx_id": hex(self.tx_id), "rx_id": hex(self.rx_id)},
            )

        with self._operation_lock:
            self._send_payload(payload, is_critical_command=is_critical_command, user_confirmed=user_confirmed)

            start_time = time.monotonic()
            deadline = start_time + timeout_s
            max_absolute_deadline = start_time + max(timeout_s, 30.0)
            nrc_78_count = 0

            while True:
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0 or now >= max_absolute_deadline:
                    raise ProtocolError(
                        f"UDS Request timed out waiting for response from ECU (0x{self.rx_id:03X})",
                        code="UDS_TIMEOUT",
                        details={"tx_id": hex(self.tx_id), "rx_id": hex(self.rx_id), "nrc_78_count": nrc_78_count},
                    )

                rx_frame = None
                if self.bus is not None:
                    rx_frame = self.bus.recv(timeout_s=min(0.1, max(0.001, remaining)))

                if rx_frame is not None and rx_frame.arbitration_id == self.rx_id:
                    completed_data, resp_frame = self.transport.handle_rx_frame(rx_frame)
                    if resp_frame is not None:
                        # Flow control frame response - cleanly routed through TxPort
                        self.tx_port.send_sync(resp_frame)
                    if completed_data is not None:
                        resp = UdsServiceBuilder.parse_response(completed_data)
                        expected_sid = payload[0]
                        if resp.service_id != expected_sid:
                            logger.warning(
                                "UDS response service ID mismatch (dropping mismatched/unsolicited response)",
                                extra={
                                    "expected_sid": hex(expected_sid),
                                    "actual_sid": hex(resp.service_id) if resp.service_id is not None else None,
                                    "rx_id": hex(self.rx_id),
                                },
                            )
                            continue

                        if (
                            not resp.is_positive
                            and resp.nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING
                            and time.monotonic() < max_absolute_deadline
                            and nrc_78_count < 20
                        ):
                            # P2* extension: ECU signalled pending; keep waiting within bounded cap (P6)
                            nrc_78_count += 1
                            deadline = min(time.monotonic() + P2_STAR_TIMEOUT_S, max_absolute_deadline)
                            continue
                        return resp

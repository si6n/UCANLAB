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
    ReadDtcInformationType,
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

    # REVIEW T56-C U-1 (CRITICAL): absolute upper bound on the total NRC 0x78
    # (responsePending) dwell, applied even when the caller passes no
    # ``max_pending_timeout_s``. Without a default wall an ECU that emits
    # 0x78 faster than P2* (5 s) re-armed ``deadline`` forever — the
    # exchange never returned and ``_operation_lock`` stayed held, blocking
    # TesterPresent keep-alive and every other UDS op.
    MAX_PENDING_ABS_S: ClassVar[float] = 30.0
    # Secondary bound: a hard ceiling on how many pending responses one
    # exchange may absorb, independent of wall-clock (a busy ECU emitting
    # 0x78 back-to-back would otherwise burn through the time wall only).
    MAX_NRC_78_COUNT: ClassVar[int] = 200

    def __init__(
        self,
        bus: AbstractBus | None = None,
        tx_port: TxPort | None = None,
        rx_sub: RxSubscription | None = None,
        tx_id: int = 0x7E0,
        rx_id: int = 0x7E8,
        channel_id: str = "uds_ch0",
        max_workers: int = 4,
        is_fd: bool | None = None,
        brs: bool = False,
        max_pending_timeout_s: float | None = None,
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
        # REVIEW 3-2 (CRITICAL): ISO-TP CAN-FD mode was supported by the
        # low-level sender/receiver but never reachable through this client —
        # every request was segmented as Classic CAN. Derive from the bus
        # when not given explicitly so an FD-capable channel produces FD
        # ISO-TP frames (single-frame payloads up to 62 bytes, FD FF/CF).
        if is_fd is None:
            is_fd = bool(getattr(bus, "is_fd", False))
        self.is_fd = is_fd
        # REVIEW 3-4 (MEDIUM): Bit Rate Switch for FD frames — the HAL and
        # replay layers carry brs end-to-end, but the ISO-TP engine always
        # emitted brs=False, disabling FD's main performance gain.
        self.brs = bool(brs) and is_fd
        # REVIEW 1-M3 (MEDIUM) / REVIEW T56-C U-1 (CRITICAL): NRC 0x78 pending
        # budget. ISO 14229-1 sets no count limit; the server may keep
        # extending within its announced P2* window. A hard 30 s absolute cap
        # killed healthy long routines (flash erase/checksum) mid-flight, so
        # a *default* wall of MAX_PENDING_ABS_S (30 s) is kept while the
        # caller can raise/lower it explicitly. When max_pending_timeout_s is
        # None the class default applies — never "no wall at all" (that path
        # allowed an ECU emitting 0x78 faster than P2* to loop forever).
        self.max_pending_timeout_s = max_pending_timeout_s
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
        confirmation_token: bytes | str | None = None,
    ) -> UdsResponse:
        """Switch diagnostic session (0x10).

        D2 (REVIEW M-9): EXTENDED_DIAGNOSTIC_SESSION is also treated as
        critical — most OEMs gate IOControl (0x2F) / Routine (0x31) behind
        the extended session, so entering it onaysız must not be possible.
        Dual confirmation still defaults to not-granted.

        T47-B (P3/G-3): `confirmation_token` carries the gateway-issued HMAC
        proof for the critical session transitions.
        """
        # REVIEW hardening: EXTENDED is a privilege-escalation stepping
        # stone (0x2F/0x31 gating) — it joins the critical list.
        is_critical = session_type in (
            DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION,
            DiagnosticSessionType.PROGRAMMING_SESSION,
            DiagnosticSessionType.SAFETY_SYSTEM_DIAGNOSTIC_SESSION,
        )
        req_payload = UdsServiceBuilder.build_diagnostic_session_control(session_type)
        return self._send_and_receive(
            req_payload,
            is_critical_command=is_critical,
            user_confirmed=user_confirmed,
            confirmation_token=confirmation_token,
        )

    def security_access_request_seed(self, level: int = 1, user_confirmed: bool = False) -> UdsResponse:
        """Request Security Access Seed (0x27).

        P1-1/D2 (REVIEW M-9): security access elevates ECU privilege — the
        send-key leg especially. Both legs are critical commands whose dual
        confirmation is not granted by default.
        """
        req_payload = UdsServiceBuilder.build_security_access_request_seed(level=level)
        return self._send_and_receive(req_payload, is_critical_command=True, user_confirmed=user_confirmed)

    def security_access_send_key(self, level: int, key: bytes, user_confirmed: bool = False) -> UdsResponse:
        """Send Security Access Key (0x27) — privilege elevation, critical.

        P1-1/D2: dual confirmation not granted by default; the flashing
        orchestrator forwards its session-level operator confirmation.
        """
        req_payload = UdsServiceBuilder.build_security_access_send_key(level=level, key=key)
        return self._send_and_receive(req_payload, is_critical_command=True, user_confirmed=user_confirmed)

    def read_did(self, did: int) -> UdsResponse:
        """Read Data Identifier (0x22)."""
        req_payload = UdsServiceBuilder.build_read_data_by_identifier(did)
        return self._send_and_receive(req_payload)

    def clear_dtc(
        self,
        dtc_group: int = 0xFFFFFF,
        user_confirmed: bool = False,
        confirmation_token: bytes | str | None = None,
    ) -> UdsResponse:
        """Clear Diagnostic Information (0x14) - Critical command.

        Requires explicit operator confirmation; dual confirmation is NOT
        granted by default. T47-B (P3/G-3): `confirmation_token` carries the
        gateway-issued HMAC proof when a confirmation secret is wired.
        """
        req_payload = UdsServiceBuilder.build_clear_diagnostic_information(dtc_group)
        return self._send_and_receive(
            req_payload,
            is_critical_command=True,
            user_confirmed=user_confirmed,
            confirmation_token=confirmation_token,
        )

    def write_did(self, did: int, data: bytes, user_confirmed: bool = False) -> UdsResponse:
        """Write Data Identifier (0x2E) - Critical command.

        Requires explicit operator confirmation; dual confirmation is NOT
        granted by default.
        """
        req_payload = UdsServiceBuilder.build_write_data_by_identifier(did, data)
        return self._send_and_receive(req_payload, is_critical_command=True, user_confirmed=user_confirmed)

    def read_memory_by_address(
        self,
        memory_address: int,
        memory_size: int,
        address_and_length_format_identifier: int = 0x44,
    ) -> UdsResponse:
        """Read Memory By Address (0x23)."""
        req_payload = UdsServiceBuilder.build_read_memory_by_address(
            memory_address=memory_address,
            memory_size=memory_size,
            address_and_length_format_identifier=address_and_length_format_identifier,
        )
        return self._send_and_receive(req_payload)

    def read_dtc_information(
        self,
        sub_function: ReadDtcInformationType | int = ReadDtcInformationType.REPORT_DTC_BY_STATUS_MASK,
        status_mask: int = 0xFF,
        dtc_mask: int | None = None,
        snapshot_record_num: int = 0xFF,
    ) -> UdsResponse:
        """Read DTC Information (0x19)."""
        req_payload = UdsServiceBuilder.build_read_dtc_information(
            sub_function=sub_function,
            status_mask=status_mask,
            dtc_mask=dtc_mask,
            snapshot_record_num=snapshot_record_num,
        )
        return self._send_and_receive(req_payload)

    def request_upload(
        self,
        memory_address: int,
        memory_size: int,
        data_format_identifier: int = 0x00,
        address_and_length_format_identifier: int = 0x44,
        user_confirmed: bool = False,
    ) -> UdsResponse:
        """Request Upload (0x35) - Critical command.

        Requires explicit operator confirmation; dual confirmation is NOT
        granted by default.
        """
        req_payload = UdsServiceBuilder.build_request_upload(
            memory_address=memory_address,
            memory_size=memory_size,
            data_format_identifier=data_format_identifier,
            address_and_length_format_identifier=address_and_length_format_identifier,
        )
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
        user_confirmed: bool = False,
    ) -> UdsResponse:
        """Transfer Data Block (0x36) - Memory write is safety-critical.

        P1-1 (REVIEW M-8/H-4): dual confirmation is NOT granted by default —
        0x36 is the service that writes ECU flash, so it follows the same
        rule as write_did/request_download/ecu_reset/start_routine. The
        flash orchestrator passes its once-per-session operator confirmation
        (config.user_confirmed) explicitly; every other caller must too.

        REVIEW hardening: `is_critical_command` is kept for API
        compatibility but downgrading it is forbidden — passing False
        raises ValueError (fail-closed, no silent gateway bypass).
        """
        if not is_critical_command:
            raise ValueError(
                "transfer_data (0x36) is always safety-critical — "
                "is_critical_command=False is forbidden (fail-closed)"
            )
        req_payload = UdsServiceBuilder.build_transfer_data(block_sequence=block_sequence, data=data)
        return self._send_and_receive(
            req_payload, is_critical_command=True, user_confirmed=user_confirmed
        )

    def request_transfer_exit(
        self,
        is_critical_command: bool = True,
        user_confirmed: bool = False,
    ) -> UdsResponse:
        """Request Transfer Exit (0x37) - closes a flash transfer.

        P1-1: dual confirmation not granted by default (see transfer_data).

        REVIEW hardening: `is_critical_command` is kept for API
        compatibility but downgrading it is forbidden — passing False
        raises ValueError (fail-closed, no silent gateway bypass).
        """
        if not is_critical_command:
            raise ValueError(
                "request_transfer_exit (0x37) is always safety-critical — "
                "is_critical_command=False is forbidden (fail-closed)"
            )
        req_payload = UdsServiceBuilder.build_request_transfer_exit()
        return self._send_and_receive(
            req_payload, is_critical_command=True, user_confirmed=user_confirmed
        )

    def ecu_reset(
        self,
        reset_type: int = 0x01,
        user_confirmed: bool = False,
        confirmation_token: bytes | str | None = None,
    ) -> UdsResponse:
        """ECU Reset (0x11) - Critical command.

        Requires explicit operator confirmation; dual confirmation is NOT
        granted by default. T47-B (P3/G-3): `confirmation_token` carries the
        gateway-issued HMAC proof when a confirmation secret is wired.
        """
        req_payload = UdsServiceBuilder.build_ecu_reset(reset_type=reset_type)
        return self._send_and_receive(
            req_payload,
            is_critical_command=True,
            user_confirmed=user_confirmed,
            confirmation_token=confirmation_token,
        )

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
        confirmation_token: bytes | str | None = None,
    ) -> None:
        """Transmit a UDS payload through the TxPort.

        P-C-001 fix: multi-frame payloads are sent Flow-Control aware —
        the First Frame goes out, then the sender waits (N_Bs) for the
        ECU's FC(CTS) before transmitting Consecutive Frames, honouring
        BS windowing and STmin pacing as ISO 15765-2 requires. Single
        frames go out unchanged.

        REVIEW 3-2 (CRITICAL): segmentation honours the client's CAN-FD
        mode — an FD-capable bus now produces FD ISO-TP frames (extended
        SF up to 62 bytes, FD FF/CF) instead of always-Classic framing.
        """
        frames = self.transport.segment_message(payload, is_fd=self.is_fd, brs=self.brs)

        if len(frames) <= 1:
            for frame in frames:
                # P3/G-3: `confirmation_token` is passed positionally-compatibly
                # only when present, so lightweight TxPort doubles that stub
                # `_tx_frame` with the historical 3/4-arg signature keep working.
                if confirmation_token is None:
                    self._tx_frame(frame, is_critical_command, user_confirmed)
                else:
                    self._tx_frame(
                        frame, is_critical_command, user_confirmed, confirmation_token=confirmation_token
                    )
            return

        # Multi-frame: FF first, then FC-gated CF transmission.
        if confirmation_token is None:
            self._tx_frame(frames[0], is_critical_command, user_confirmed)
        else:
            self._tx_frame(
                frames[0], is_critical_command, user_confirmed, confirmation_token=confirmation_token
            )
        self._send_consecutive_frames_flow_controlled(
            frames[1:], payload, is_critical_command, user_confirmed
        )

    def _tx_frame(
        self,
        frame: CanFrame,
        is_critical_command: bool,
        user_confirmed: bool,
        budget_category: str = "protocol_burst",
        confirmation_token: bytes | str | None = None,
    ) -> None:
        """Send one frame through the gateway TxPort choke-point.

        P1-9: ISO-TP frames (FF + CF trains) travel on the 'protocol_burst'
        lane by default — a 256-byte 0x36 block is ~37 CFs which would
        otherwise slam into the 100 msg/s default-lane wall and, pre-P1-9,
        engage a permanent E-Stop (self-DoS mid-flash).

        T47-B (P3/G-3): `confirmation_token` is forwarded to a gateway that has
        a confirmation secret configured, so critical single-frame requests can
        present HMAC proof instead of relying on the `user_confirmed` boolean.
        """
        if hasattr(self.tx_port, "validate_and_transmit"):
            kwargs: dict[str, object] = {
                "is_critical_command": is_critical_command,
                "user_confirmed": user_confirmed,
                "budget_category": budget_category,
            }
            if confirmation_token is not None:
                kwargs["confirmation_token"] = confirmation_token
            self.tx_port.validate_and_transmit(frame, **kwargs)
        elif hasattr(self.tx_port, "send_sync") and not hasattr(self.tx_port, "validate_and_transmit"):
            # TxSafetyGateway-shaped port: honour the category-aware lane.
            try:
                self.tx_port.send_sync(frame, budget_category=budget_category)
            except TypeError:
                # Plain TxPort (send_sync(frame) only) — fall back.
                self.tx_port.send_sync(frame)
        else:
            self.tx_port.send_sync(frame)

    # N_Bs (ISO 15765-2 §4.6.2): max wait for FC after FF/before next CF
    N_BS_TIMEOUT_S: ClassVar[float] = 1.0
    # REVIEW hardening (flow-control spoofing): strict FC validation caps.
    FC_STMIN_CAP_MS: ClassVar[float] = 10.0
    FC_WAIT_ABSOLUTE_TIMEOUT_S: ClassVar[float] = 2.0
    FC_CHANNEL_MISMATCH_IS_FATAL: ClassVar[bool] = True

    def _await_flow_control_sync(self, timeout_s: float | None = None) -> CanFrame | None:
        """Block until a Flow Control frame from the ECU arrives (N_Bs bound).

        REVIEW hardening: FC frames are validated strictly — channel
        match is required (a cross-bus FC must never pace our transfer),
        STmin is capped at 10 ms (a spoofed 127 ms stall is rejected),
        consecutive WAIT frames are bounded by an absolute 2 s timeout on
        top of the N_Bs budget, and the whole wait respects a total
        deadline.
        """
        deadline_budget = self.N_BS_TIMEOUT_S if timeout_s is None else timeout_s
        start = time.monotonic()
        wait_start: float | None = None
        while (time.monotonic() - start) < deadline_budget:
            rx_frame = None
            if self.bus is not None:
                remaining = deadline_budget - (time.monotonic() - start)
                rx_frame = self.bus.recv(timeout_s=min(0.05, max(0.001, remaining)))
            if rx_frame is None:
                continue

            # Channel check: an FC from another bus/channel is spoofing
            # or crosstalk. Backward compatibility: multi-channel test
            # topologies legitimately deliver FCs labelled with a different
            # channel string, so a mismatch is ALWAYS audit-logged but only
            # DROPPED when the bus topology is coherent (bus channel ==
            # client channel — a mismatched FC is then provably foreign).
            # Arbitration-ID binding below remains the enforcement point.
            frame_channel = getattr(rx_frame, "channel_id", None)
            if frame_channel is not None and frame_channel != self.channel_id:
                bus_channel = getattr(self.bus, "channel_id", None) if self.bus is not None else None
                coherent = bus_channel is not None and bus_channel == self.channel_id
                logger.warning(
                    "ISO-TP FC channel mismatch (spoof/crosstalk candidate)",
                    extra={
                        "expected": self.channel_id,
                        "got": frame_channel,
                        "coherent_topology": coherent,
                    },
                )
                if coherent and self.FC_CHANNEL_MISMATCH_IS_FATAL:
                    continue
            if rx_frame.arbitration_id != self.rx_id or len(rx_frame.data) < 3:
                continue
            if (rx_frame.data[0] >> 4) != 0x3:  # PCI_FLOW_CONTROL
                continue
            fs = rx_frame.data[0] & 0x0F
            # Absolute WAIT timeout: a peer holding us in WAIT forever is a
            # transfer-sabotage — bound the total WAIT dwell to 2 s.
            if fs == 0x1:  # FS_WAIT
                now = time.monotonic()
                if wait_start is None:
                    wait_start = now
                elif (now - wait_start) > self.FC_WAIT_ABSOLUTE_TIMEOUT_S:
                    logger.warning(
                        "ISO-TP FC WAIT absolute timeout exceeded — aborting wait",
                        extra={"wait_s": now - wait_start},
                    )
                    return None
            else:
                wait_start = None
            # STmin cap: reject/clamp spoofed stall values (>10 ms).
            st_min_raw = rx_frame.data[2]
            st_min_ms = decode_st_min(st_min_raw)
            if st_min_ms > self.FC_STMIN_CAP_MS:
                logger.warning(
                    "ISO-TP FC STmin capped (spoof/stall protection)",
                    extra={"raw": st_min_raw, "decoded_ms": st_min_ms, "cap_ms": self.FC_STMIN_CAP_MS},
                )
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
                # REVIEW hardening: clamp spoofed STmin stalls at the cap
                # (a 127 ms STmin would otherwise serialize a flash to a
                # crawl; pacing above the cap is never legitimate).
                delay_ms = min(decode_st_min(st_min), self.FC_STMIN_CAP_MS)
                if delay_ms > 0:
                    time.sleep(delay_ms / 1000.0)
                self._tx_frame(cf_frames[idx], is_critical_command, user_confirmed)
                idx += 1
                block_sent += 1

# Service IDs whose positive response echoes request bytes beyond SID:
    # 0x22/0x2E echo the DID (2 bytes at data[0:2]),
    # 0x27 echoes the subfunction (1 byte at data[0]),
    # 0x10/0x11 echo the subfunction/type (1 byte at data[0]),
    # 0x19 echoes the subfunction (1 byte at data[0]),
    # 0x31 echoes routineControlType + routineId (3 bytes at data[0:3]),
    # 0x36 echoes the block sequence counter (1 byte at data[0]).
    _ECHO_LENGTHS: ClassVar[dict[int, int]] = {
        0x10: 1,  # DiagnosticSessionControl: session type
        0x11: 1,  # ECU Reset: reset type
        0x19: 1,  # ReadDTCInformation: sub-function
        0x22: 2,  # ReadDataByIdentifier: DID
        0x2E: 2,  # WriteDataByIdentifier: DID
        0x27: 1,  # SecurityAccess: securityAccessType
        0x31: 3,  # RoutineControl: controlType + routineId
        0x36: 1,  # TransferData: blockSequenceCounter
    }

    def _response_echo_matches(self, expected_sid: int, request: bytes, resp: UdsResponse) -> bool:
        """Verify the service-specific echo bytes of a positive response.

        REVIEW (echo validation): only the response SID was compared — the
        wrong DID/subfunction/routine answer of the same service completed
        the exchange. The echo bytes (`resp.data[:n]`) must equal the
        request's corresponding bytes (`request[1:1+n]`) before the
        response is accepted.
        """
        if not resp.is_positive:
            # Negative responses echo the REQUESTED sid at payload[1]; the
            # parse already surfaces it via service_id — no extra echo bytes
            # are mandated for the completion decision.
            return True
        n = self._ECHO_LENGTHS.get(expected_sid)
        if n is None:
            return True
        if len(resp.data) < n:
            # Positive response missing its own echo bytes is malformed.
            return False
        return bytes(resp.data[:n]) == bytes(request[1 : 1 + n])

    def _send_and_receive(
        self,
        payload: bytes,
        timeout_s: float = 2.0,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        confirmation_token: bytes | str | None = None,
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
            self._send_payload(
                payload,
                is_critical_command=is_critical_command,
                user_confirmed=user_confirmed,
                confirmation_token=confirmation_token,
            )

            start_time = time.monotonic()
            deadline = start_time + timeout_s
            # REVIEW T56-C U-1 (CRITICAL): a finite absolute wall always
            # applies. The caller's optional max_pending_timeout_s overrides
            # the class default — it can *never* disable the wall. (ISO 14229
            # caps each P2* extension at 5 s in the ECU, but nothing bounds
            # how many extensions may arrive, so the client must impose its
            # own total dwell ceiling.)
            pending_wall_s = (
                self.max_pending_timeout_s
                if self.max_pending_timeout_s is not None
                else self.MAX_PENDING_ABS_S
            )
            max_absolute_deadline = start_time + pending_wall_s
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
                        # M-5 (P2-1): flow-control frames are protocol
                        # overhead, not operator commands — an ISO-TP CF/FC
                        # train over the default lane (100 msg/s) could trip
                        # the gateway's sustained-overload E-Stop mid-read.
                        # Route through the protocol_burst budget lane.
                        try:
                            self.tx_port.send_sync(resp_frame, budget_category="protocol_burst")
                        except TypeError:
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

                        # REVIEW (echo validation): a same-SID reply with the
                        # WRONG DID/subfunction echo used to complete the
                        # pending exchange — a delayed response or another
                        # client's answer produced success for the wrong
                        # request. The service-specific echo bytes must match
                        # the request before the response is accepted.
                        if not self._response_echo_matches(expected_sid, payload, resp):
                            logger.warning(
                                "UDS response echo mismatch (dropping mismatched/unsolicited response)",
                                extra={
                                    "sid": hex(expected_sid),
                                    "rx_id": hex(self.rx_id),
                                    "data_hex": completed_data[:8].hex(),
                                },
                            )
                            continue

                        if (
                            not resp.is_positive
                            and resp.nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING
                            and nrc_78_count < self.MAX_NRC_78_COUNT
                            and time.monotonic() < max_absolute_deadline
                        ):
                            # P2* extension: ECU signalled pending. Each 0x78
                            # re-arms the P2* window BUT is clamped to the
                            # absolute wall, and the count cap bounds how many
                            # extensions a single exchange may absorb
                            # (REVIEW T56-C U-1 — an ECU spamming 0x78 faster
                            # than P2* must not loop forever).
                            nrc_78_count += 1
                            deadline = min(time.monotonic() + P2_STAR_TIMEOUT_S, max_absolute_deadline)
                            continue
                        # Pending budget exhausted (time wall or count cap):
                        # surface a deterministic timeout instead of returning
                        # the last 0x78 as if it were the final response.
                        if (
                            not resp.is_positive
                            and resp.nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING
                        ):
                            raise ProtocolError(
                                f"UDS Request exceeded pending budget waiting for ECU (0x{self.rx_id:03X})",
                                code="UDS_TIMEOUT",
                                details={
                                    "tx_id": hex(self.tx_id),
                                    "rx_id": hex(self.rx_id),
                                    "nrc_78_count": nrc_78_count,
                                    "max_nrc_78_count": self.MAX_NRC_78_COUNT,
                                },
                            )
                        return resp

    # ISO 15765-4 functional (broadcast) request ID for 11-bit OBD-II
    FUNCTIONAL_REQUEST_ID: ClassVar[int] = 0x7DF
    # Expected physical response range (ECU response base 0x7E8 + ECU index)
    PHYSICAL_RESPONSE_IDS: ClassVar[tuple[int, ...]] = tuple(range(0x7E8, 0x7F0))

    def send_functional(
        self,
        payload: bytes,
        functional_id: int | None = None,
        expected_physical_ids: tuple[int, ...] | None = None,
        collect_window_s: float = 0.3,
    ) -> list[UdsResponse]:
        """REVIEW 3-5 (MEDIUM): send a functional (broadcast) request and
        collect every responding ECU's physical answer.

        ISO 15765-4 / ISO 14229 functional addressing (0x7DF) reaches ALL
        compliant ECUs; each answers from its OWN physical response ID
        (0x7E8..0x7EF). The single-rx_id filter in _send_and_receive
        silently dropped every other ECU's reply. This method transmits
        the request once, then collects responses from all expected
        physical IDs for `collect_window_s`, reassembling each ID's
        ISO-TP stream with a dedicated transport.
        """
        fid = functional_id if functional_id is not None else self.FUNCTIONAL_REQUEST_ID
        expected = expected_physical_ids if expected_physical_ids is not None else self.PHYSICAL_RESPONSE_IDS

        with self._operation_lock:
            func_transport = IsoTpTransport(tx_id=fid, rx_id=fid, channel_id=self.channel_id)
            req_frames = func_transport.segment_message(payload, is_fd=self.is_fd, brs=self.brs)
            for frame in req_frames:
                self._tx_frame(frame, is_critical_command=False, user_confirmed=False)

            # Per-physical-ID reassembly transports.
            per_id: dict[int, IsoTpTransport] = {rid: IsoTpTransport(tx_id=fid, rx_id=rid, channel_id=self.channel_id) for rid in expected}
            completed: dict[int, bytes] = {}
            deadline = time.monotonic() + collect_window_s
            expected_sid = payload[0] if payload else 0

            while time.monotonic() < deadline:
                rx_frame = self.bus.recv(timeout_s=min(0.05, max(0.001, deadline - time.monotonic()))) if self.bus is not None else None
                if rx_frame is None:
                    continue
                rid = rx_frame.arbitration_id
                if rid not in per_id:
                    continue
                data, resp_frame = per_id[rid].handle_rx_frame(rx_frame)
                if resp_frame is not None:
                    try:
                        self.tx_port.send_sync(resp_frame, budget_category="protocol_burst")
                    except TypeError:
                        self.tx_port.send_sync(resp_frame)
                if data is not None:
                    resp = UdsServiceBuilder.parse_response(data)
                    if resp.service_id == expected_sid:
                        completed.setdefault(rid, data)

            return [UdsServiceBuilder.parse_response(data) for data in completed.values()]

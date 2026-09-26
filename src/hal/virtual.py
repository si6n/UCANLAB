"""Virtual CAN Bus implementation for in-memory and simulated testing."""

from __future__ import annotations

import queue
from collections import deque
from typing import Any

from src.core.errors import HardwareError
from src.core.models.can_frame import CanFrame
from src.hal.base import AbstractBus, BusState
from src.protocols.uds.isotp import IsoTpTransport
from src.protocols.uds.nrc import UdsNrc
from src.protocols.uds.services import (
    DiagnosticSessionType,
    ReadDtcInformationType,
    RoutineControlType,
    UdsServiceId,
)


class VirtualBus(AbstractBus):
    """Thread-safe in-memory virtual CAN bus for unit testing and simulations."""

    # M-11 (3FABLE): bounded TX transcript — the old unbounded list grew for
    # the whole life of long simulations (GBs at 60 FPS sim rates).
    MAX_SENT_FRAMES: int = 10_000
    MAX_RX_QUEUE: int = 10_000

    def __init__(
        self,
        channel_id: str = "virtual_0",
        bitrate: int = 500000,
        is_fd: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(channel_id=channel_id, bitrate=bitrate, is_fd=is_fd)
        # M-11: deque with maxlen — assertions use indexing/membership which
        # behave identically; the oldest entries fall off beyond 10k frames.
        self.sent_frames: deque[CanFrame] = deque(maxlen=self.MAX_SENT_FRAMES)
        self._rx_queue: queue.Queue[CanFrame] = queue.Queue(maxsize=self.MAX_RX_QUEUE)
        self.dropped_rx_frames: int = 0
        # S1-P1-4: a virtual bus has no physical transceiver, so the software
        # listen-only flag IS the complete mode state. Declaring it here (and
        # exposing `set_listen_only`) keeps the desktop arming path honest:
        # without it the composition root fell through to the generic
        # "flag must already match" fallback and could never arm a virtual
        # session, even though there is nothing to verify.
        self.listen_only: bool = False
        # HAL-29: explicit, documented loopback contract. When True, `send()`
        # also delivers the frame back into THIS bus's RX queue (a modelled
        # single-channel echo). Default OFF: the virtual bus is a test double
        # for a real adapter, and silently echoing every TX would fabricate RX
        # traffic that no peer ever produced (AGENTS.md §2.3) and hide
        # missing `inject_rx` wiring in tests.
        self.loopback: bool = bool(kwargs.get("loopback", False))
        # HAL-10: disconnect() must be able to WAKE a blocked recv(None).
        # A sentinel object (never a CanFrame) is published onto the RX
        # queue; recv() returns None when it observes it.
        self._rx_stop_sentinel: object = object()

    def set_listen_only(self, listen_only: bool) -> bool:
        """Set the virtual bus TX gate (always verifiable — no hardware).

        Returns True unconditionally when connected: there is no transceiver
        to confirm, so the flag is the authoritative state and the mode change
        genuinely took effect. Returns False while disconnected so the caller
        still gets a truthful "not applied" result.
        """
        if not self.is_connected:
            return False
        self.listen_only = bool(listen_only)
        self.metrics.state = BusState.PASSIVE if self.listen_only else BusState.ACTIVE
        return True

    def connect(self) -> None:
        """Connect the virtual CAN bus.

        HAL-09: the reported state must honour the listen-only flag. The old
        body always set ACTIVE, so a passive virtual session advertised
        itself as ACTIVE — the composition root's TX-arming preconditions and
        every metrics consumer read a state the bus would immediately refuse
        to transmit under.
        """
        # Drain any leftover STOP sentinels from a prior disconnect
        while True:
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break
        self.is_connected = True
        self.metrics.state = BusState.PASSIVE if self.listen_only else BusState.ACTIVE

    def disconnect(self) -> None:
        """Disconnect the virtual CAN bus.

        HAL-10: the queue is drained and a STOP sentinel published so a
        thread blocked in `recv(timeout_s=None)` wakes up and returns None
        instead of hanging forever on a torn-down bus.
        """
        self.is_connected = False
        self.metrics.state = BusState.DISCONNECTED
        self._publish_stop_sentinel()

    def _publish_stop_sentinel(self) -> None:
        """Wake every blocked `recv(None)` consumer with the STOP sentinel.

        The queue is drained first (the frames belong to a dead session) and
        then as many sentinels as there is room are queued, so each blocked
        consumer finds one. `inject_rx` drops its own frames once the sentinel
        is queued, so no stale data can masquerade as post-disconnect traffic.
        """
        while True:
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break
        for _ in range(self.MAX_RX_QUEUE):
            try:
                self._rx_queue.put_nowait(self._rx_stop_sentinel)  # type: ignore[arg-type]
            except queue.Full:
                break

    def send(self, frame: CanFrame) -> None:
        """Transmit frame onto the virtual bus (canonical TX entry point, D8).

        HAL-29 loopback contract: when `self.loopback` is True the frame is
        ALSO appended to this bus's RX queue (via `inject_rx`) so a
        single-object test can exercise the full TX→RX round trip. Default
        is False — no echo — so RX traffic is only ever what a caller
        explicitly injected or what a real peer sent.
        """
        if not self.is_connected:
            raise HardwareError("Cannot send: Virtual CAN bus is not connected")
        if self.listen_only:
            # S1-P1-4: honor the listen-only gate so a virtual session opened
            # passive cannot transmit either — this keeps virtual-bus
            # behaviour aligned with the physical drivers instead of being a
            # silent TX hole in tests.
            raise HardwareError(
                "Cannot send: Virtual CAN bus is in Listen-Only (passive) mode",
                code="HARDWARE_LISTEN_ONLY_TX_BLOCKED",
            )
        self.sent_frames.append(frame)
        self.metrics.tx_frames += 1
        if self.loopback:
            self.inject_rx(frame)

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        """Receive next available CAN frame from the virtual queue.

        REVIEW LOW-1 / REVIEW3 #16: per the RxSubscription/AbstractBus
        contract (HAL-31), ``timeout_s=None`` waits INDEFINITELY (the old
        0.1 s clamp broke blocking consumers) and ``timeout_s=0`` is a
        non-blocking poll (get_nowait).

        HAL-10: a `disconnect()` publishes a STOP sentinel that wakes a
        blocked ``timeout_s=None`` call so it returns None instead of
        hanging forever on a torn-down bus.
        """
        if not self.is_connected:
            raise HardwareError("Cannot receive: Virtual CAN bus is not connected")
        if timeout_s is None:
            frame = self._rx_queue.get()  # block indefinitely
            if frame is self._rx_stop_sentinel:
                return None  # HAL-10: woken by disconnect()
            self.metrics.rx_frames += 1
            return frame
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not (0 <= timeout_s <= 60):
            raise ValueError(f"timeout_s must be None, 0, or in range (0, 60], got {timeout_s!r}")
        if timeout_s == 0:
            try:
                frame = self._rx_queue.get_nowait()
            except queue.Empty:
                return None
            if frame is self._rx_stop_sentinel:
                return None
            self.metrics.rx_frames += 1
            return frame
        try:
            frame = self._rx_queue.get(timeout=timeout_s)
        except queue.Empty:
            return None
        if frame is self._rx_stop_sentinel:
            return None
        self.metrics.rx_frames += 1
        return frame

    def inject_rx(self, frame: CanFrame) -> None:
        """Inject a CAN frame into the receive queue.

        HAL-10: once the bus is disconnected/torn down, newly injected frames
        are counted as dropped rather than queued behind the sentinel where
        a woken consumer would never see them.
        """
        if not self.is_connected:
            self.dropped_rx_frames += 1
            self.metrics.dropped_frames += 1
            return
        try:
            self._rx_queue.put_nowait(frame)
        except queue.Full:
            self.dropped_rx_frames += 1
            self.metrics.dropped_frames += 1


class UdsServerEcu:
    """Simulated ISO 14229 UDS Diagnostic Server / Bootloader ECU state machine.

    Attaches to a VirtualBus, listening on `rx_id` (requests) and responding on `tx_id`.
    Simulates Sessions, SecurityAccess (Seed-Key), Memory Read/Write/Download/Upload,
    Routines (Erase/Checksum), DTC Information, and NRC error generation.
    """

    def __init__(
        self,
        bus: VirtualBus,
        rx_id: int = 0x7E0,
        tx_id: int = 0x7E8,
        channel_id: str = "virtual_0",
        vin: str = "VF1ABCDEF12345678",
        serial: str = "ECU-SN-99887766",
        is_fd: bool = False,
    ) -> None:
        self.bus = bus
        self.rx_id = rx_id
        self.tx_id = tx_id
        self.channel_id = channel_id
        self.is_fd = is_fd

        self.transport = IsoTpTransport(tx_id=tx_id, rx_id=rx_id, channel_id=channel_id)

        self.session: DiagnosticSessionType = DiagnosticSessionType.DEFAULT_SESSION
        self.security_level: int = 0  # 0 = locked
        self.seed_challenge: bytes = b"\x11\x22\x33\x44"
        self.last_requested_seed_level: int = 0

        self.dids: dict[int, bytes] = {
            0xF190: vin.encode("ascii"),
            0xF18C: serial.encode("ascii"),
            0xF186: bytes([self.session]),
            0x0100: b"\x12\x34",
        }

        # Simulated 64KB flash/RAM buffer
        self.memory: bytearray = bytearray(b"\xFF" * 0x10000)
        self.base_address: int = 0x08000000

        # Flashing state
        self.download_active: bool = False
        self.download_address: int = 0
        self.download_size: int = 0
        self.download_bsc: int = 1
        self.downloaded_data: bytearray = bytearray()
        self.max_block_length: int = 256  # 0x0100 (including SID + BSC)

        self.upload_active: bool = False
        self.upload_address: int = 0
        self.upload_size: int = 0
        self.upload_bsc: int = 1

        self.checksum_routine_id: int = 0x0202
        self.erase_routine_id: int = 0xFF00
        self.routine_results: dict[int, int] = {0x0202: 0x00, 0xFF00: 0x00}  # 0x00 = Correctly Completed

        self.dtcs: list[tuple[int, int]] = [(0x013300, 0x2F), (0xC00100, 0x28)]  # (DTC, Status)

    def process_frame(self, frame: CanFrame) -> CanFrame | None:
        """Process incoming request frame, update state machine, and return response frame if ready."""
        if frame.arbitration_id != self.rx_id:
            return None

        completed_payload, fc_frame = self.transport.handle_rx_frame(frame)
        if fc_frame is not None:
            self.bus.inject_rx(fc_frame)

        if completed_payload is not None:
            resp_payload = self._handle_uds_request(completed_payload)
            if resp_payload is not None:
                resp_frames = self.transport.segment_message(resp_payload, is_fd=self.is_fd)
                # FIX 8: a suppressed positive response (b"") segments to []
                # and `resp_frames[0]` would raise IndexError.
                if not resp_frames:
                    return None
                for r_frame in resp_frames:
                    self.bus.inject_rx(r_frame)
                return resp_frames[0]
        return None

    def _make_negative_response(self, sid: int, nrc: UdsNrc) -> bytes:
        return bytes([UdsServiceId.NEGATIVE_RESPONSE, sid, nrc])

    def _handle_uds_request(self, payload: bytes) -> bytes:
        if not payload:
            return self._make_negative_response(0x00, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)

        sid = payload[0]

        # 0x10 Diagnostic Session Control
        if sid == UdsServiceId.DIAGNOSTIC_SESSION_CONTROL:
            if len(payload) < 2:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            sess_type = payload[1]
            try:
                self.session = DiagnosticSessionType(sess_type)
            except ValueError:
                return self._make_negative_response(sid, UdsNrc.SUB_FUNCTION_NOT_SUPPORTED)
            self.dids[0xF186] = bytes([self.session])
            # Session change re-locks security
            self.security_level = 0
            # P2 server timings: P2 max 50ms, P2* max 5000ms
            return bytes([0x50, sess_type, 0x00, 0x32, 0x01, 0xF4])

        # 0x3E Tester Present
        elif sid == UdsServiceId.TESTER_PRESENT:
            if len(payload) < 2:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            sub_fn = payload[1]
            if (sub_fn & 0x80) != 0:
                return b""  # Suppress positive response
            return bytes([0x7E, sub_fn & 0x7F])

        # 0x22 Read Data By Identifier
        elif sid == UdsServiceId.READ_DATA_BY_IDENTIFIER:
            if len(payload) < 3:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            did = (payload[1] << 8) | payload[2]
            if did in self.dids:
                return bytes([0x62, (did >> 8) & 0xFF, did & 0xFF]) + self.dids[did]
            return self._make_negative_response(sid, UdsNrc.REQUEST_OUT_OF_RANGE)

        # 0x2E Write Data By Identifier
        elif sid == UdsServiceId.WRITE_DATA_BY_IDENTIFIER:
            if len(payload) < 4:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            # HAL-16: 0x2E is a PERSISTENT write. The simulator used to accept
            # it in the DEFAULT session with security LOCKED, so any request
            # could overwrite any DID. Real ISO 14229 servers gate writes on
            # an extended/programming session plus an unlocked security level;
            # the simulator now models the same contract so a client tested
            # against this ECU cannot ship believing lock-free writes work.
            if self.session == DiagnosticSessionType.DEFAULT_SESSION:
                return self._make_negative_response(sid, UdsNrc.SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION)
            if self.security_level == 0:
                return self._make_negative_response(sid, UdsNrc.SECURITY_ACCESS_DENIED)
            did = (payload[1] << 8) | payload[2]
            if did not in self.dids:
                return self._make_negative_response(sid, UdsNrc.REQUEST_OUT_OF_RANGE)
            val = payload[3:]
            self.dids[did] = val
            return bytes([0x6E, (did >> 8) & 0xFF, did & 0xFF])

        # 0x23 Read Memory By Address
        elif sid == UdsServiceId.READ_MEMORY_BY_ADDRESS:
            if len(payload) < 3:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            alfi = payload[1]
            size_width = (alfi >> 4) & 0x0F
            addr_width = alfi & 0x0F
            if size_width == 0 or addr_width == 0:
                return self._make_negative_response(sid, UdsNrc.REQUEST_OUT_OF_RANGE)
            if len(payload) < 2 + addr_width + size_width:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            addr = int.from_bytes(payload[2 : 2 + addr_width], byteorder="big")
            size = int.from_bytes(payload[2 + addr_width : 2 + addr_width + size_width], byteorder="big")
            offset = addr - self.base_address
            if 0 <= offset < len(self.memory) and offset + size <= len(self.memory):
                return bytes([0x63]) + self.memory[offset : offset + size]
            # HAL-15: the old fallback returned a FAKE positive response of
            # `bytes([0xAA] * size)` for ANY out-of-range address — with
            # size_width == 4 that was a 4 GiB allocation from a single ~7-byte
            # request (a trivial memory-exhaustion primitive) and it told the
            # client it had read real data that never existed (AGENTS.md §2.3).
            # Out-of-range now fails closed with NRC REQUEST_OUT_OF_RANGE.
            return self._make_negative_response(sid, UdsNrc.REQUEST_OUT_OF_RANGE)

        # 0x27 Security Access
        elif sid == UdsServiceId.SECURITY_ACCESS:
            if len(payload) < 2:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            level = payload[1]
            if (level % 2) == 1:  # Request Seed
                self.last_requested_seed_level = level
                return bytes([0x67, level]) + self.seed_challenge
            else:  # Send Key
                expected_key_level = self.last_requested_seed_level + 1
                if level != expected_key_level:
                    return self._make_negative_response(sid, UdsNrc.REQUEST_SEQUENCE_ERROR)
                sent_key = payload[2:]
                # HAL-16: the deterministic simulator contract — the key is
                # DERIVED from the seed that was just handed out, so only a
                # tester that actually consumed the challenge can unlock. The
                # old check was `if sent_key:` (ANY non-empty key, e.g. b"\x00")
                # which granted the security level to every caller and made
                # every "security-gated" flashing test vacuous.
                expected_key = bytes(b ^ 0xFF for b in self.seed_challenge)
                if sent_key == expected_key:
                    self.security_level = self.last_requested_seed_level
                    return bytes([0x67, level])
                return self._make_negative_response(sid, UdsNrc.INVALID_KEY)

        # 0x34 Request Download
        elif sid == UdsServiceId.REQUEST_DOWNLOAD:
            if self.session not in (DiagnosticSessionType.PROGRAMMING_SESSION, DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION):
                return self._make_negative_response(sid, UdsNrc.SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION)
            # HAL-16: 0x34 opened a flash-download session with security still
            # LOCKED (only the session type was checked), so the full
            # 0x34→0x36→0x37 flashing sequence ran unauthenticated against the
            # simulator. Security level is now a precondition, matching 0x2E.
            if self.security_level == 0:
                return self._make_negative_response(sid, UdsNrc.SECURITY_ACCESS_DENIED)
            if len(payload) < 4:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            alfi = payload[2]
            size_width = (alfi >> 4) & 0x0F
            addr_width = alfi & 0x0F
            if size_width == 0 or addr_width == 0:
                return self._make_negative_response(sid, UdsNrc.REQUEST_OUT_OF_RANGE)
            if len(payload) < 3 + addr_width + size_width:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            self.download_address = int.from_bytes(payload[3 : 3 + addr_width], byteorder="big")
            self.download_size = int.from_bytes(payload[3 + addr_width : 3 + addr_width + size_width], byteorder="big")
            self.download_active = True
            self.download_bsc = 1
            self.downloaded_data = bytearray()
            # Response: 0x74 | lengthFormat (0x20 = 2 bytes) | maxNumberOfBlockLength (256 = 0x0100)
            return bytes([0x74, 0x20, 0x01, 0x00])

        # 0x35 Request Upload
        elif sid == UdsServiceId.REQUEST_UPLOAD:
            if len(payload) < 4:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            alfi = payload[2]
            size_width = (alfi >> 4) & 0x0F
            addr_width = alfi & 0x0F
            if len(payload) < 3 + addr_width + size_width:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            self.upload_address = int.from_bytes(payload[3 : 3 + addr_width], byteorder="big")
            self.upload_size = int.from_bytes(payload[3 + addr_width : 3 + addr_width + size_width], byteorder="big")
            self.upload_active = True
            self.upload_bsc = 1
            return bytes([0x75, 0x20, 0x01, 0x00])

        # 0x36 Transfer Data
        elif sid == UdsServiceId.TRANSFER_DATA:
            if not self.download_active and not self.upload_active:
                return self._make_negative_response(sid, UdsNrc.REQUEST_SEQUENCE_ERROR)
            if len(payload) < 2:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            bsc = payload[1]
            if self.download_active:
                if bsc != (self.download_bsc & 0xFF):
                    return self._make_negative_response(sid, UdsNrc.WRONG_BLOCK_SEQUENCE_COUNTER)
                block_data = payload[2:]
                self.downloaded_data.extend(block_data)
                self.download_bsc = (self.download_bsc + 1) & 0xFF
                return bytes([0x76, bsc])
            elif self.upload_active:
                if bsc != (self.upload_bsc & 0xFF):
                    return self._make_negative_response(sid, UdsNrc.WRONG_BLOCK_SEQUENCE_COUNTER)
                self.upload_bsc = (self.upload_bsc + 1) & 0xFF
                chunk = bytes([0x55] * 64)
                return bytes([0x76, bsc]) + chunk

        # 0x37 Request Transfer Exit
        elif sid == UdsServiceId.REQUEST_TRANSFER_EXIT:
            if not self.download_active and not self.upload_active:
                return self._make_negative_response(sid, UdsNrc.REQUEST_SEQUENCE_ERROR)
            self.download_active = False
            self.upload_active = False
            return bytes([0x77])

        # 0x31 Routine Control
        elif sid == UdsServiceId.ROUTINE_CONTROL:
            if len(payload) < 4:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            ctrl_type = payload[1]
            rid = (payload[2] << 8) | payload[3]
            if ctrl_type == RoutineControlType.START_ROUTINE:
                self.routine_results[rid] = 0x00
                return bytes([0x71, ctrl_type, (rid >> 8) & 0xFF, rid & 0xFF])
            elif ctrl_type == RoutineControlType.REQUEST_ROUTINE_RESULTS:
                status = self.routine_results.get(rid, 0x00)
                return bytes([0x71, ctrl_type, (rid >> 8) & 0xFF, rid & 0xFF, status])
            elif ctrl_type == RoutineControlType.STOP_ROUTINE:
                return bytes([0x71, ctrl_type, (rid >> 8) & 0xFF, rid & 0xFF])
            return self._make_negative_response(sid, UdsNrc.SUB_FUNCTION_NOT_SUPPORTED)

        # 0x19 Read DTC Information
        elif sid == UdsServiceId.READ_DTC_INFORMATION:
            if len(payload) < 2:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            sub_fn = payload[1]
            if sub_fn in (
                ReadDtcInformationType.REPORT_NUMBER_OF_DTC_BY_STATUS_MASK,
                ReadDtcInformationType.REPORT_DTC_BY_STATUS_MASK,
            ):
                # Format: 0x59 | sub_fn | DTCStatusAvailabilityMask (0xFF) | [DTC High, Mid, Low, Status]...
                dtc_bytes = bytearray([0x59, sub_fn, 0xFF])
                for dtc_val, dtc_st in self.dtcs:
                    dtc_bytes.extend([(dtc_val >> 16) & 0xFF, (dtc_val >> 8) & 0xFF, dtc_val & 0xFF, dtc_st & 0xFF])
                return bytes(dtc_bytes)
            elif sub_fn == ReadDtcInformationType.REPORT_DTC_SNAPSHOT_RECORD:
                return bytes([0x59, sub_fn, 0x01, 0x33, 0x00, 0x2F, 0x01, 0x01, 0x00, 0x00])
            return bytes([0x59, sub_fn, 0xFF])

        # 0x11 ECU Reset
        elif sid == UdsServiceId.ECU_RESET:
            if len(payload) < 2:
                return self._make_negative_response(sid, UdsNrc.INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
            reset_type = payload[1]
            self.session = DiagnosticSessionType.DEFAULT_SESSION
            self.security_level = 0
            self.download_active = False
            return bytes([0x51, reset_type])

        # 0x14 Clear Diagnostic Information
        elif sid == UdsServiceId.CLEAR_DIAGNOSTIC_INFORMATION:
            self.dtcs.clear()
            return bytes([0x54])

        return self._make_negative_response(sid, UdsNrc.SERVICE_NOT_SUPPORTED)


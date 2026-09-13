"""Phase 1 E2E Test Harness and Virtual CAN-Bus Infrastructure.

Provides:
1. VirtualCanBus: In-memory multi-channel CAN/CAN-FD bus with broadcast routing.
2. VirtualTxPort & VirtualRxSubscription: Asynchronous port abstractions adhering to TxPort & RxSubscription contracts.
3. DeterministicClockProvider: Deterministic high-precision monotonic clock adhering to ClockProvider.
4. SimulatedUdsEcu: In-memory UDS ECU supporting ISO-TP segmentation/reassembly and flow control.
5. SimulatedJ1939Ecu: In-memory J1939 ECU supporting BAM broadcast, RTS/CTS CMDT handshake, and cyclic telemetry.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Protocol, runtime_checkable

from src.core.models.can_frame import CanFrame, length_to_dlc, pad_payload

# ---------------------------------------------------------------------------
# Base Protocol Contracts (Compatible with src/core/contracts/ports.py)
# ---------------------------------------------------------------------------


@runtime_checkable
class TxPort(Protocol):
    """Abstract CAN frame transmission port."""

    async def send(self, frame: CanFrame) -> None:
        """Transmit a CAN frame asynchronously."""
        ...

    def send_sync(self, frame: CanFrame) -> None:
        """Transmit a CAN frame synchronously."""
        ...


@runtime_checkable
class RxSubscription(Protocol):
    """Abstract CAN frame subscription receiver."""

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        """Receive the next incoming CAN frame."""
        ...

    def unsubscribe(self) -> None:
        """Cancel and release subscription."""
        ...


@runtime_checkable
class ClockProvider(Protocol):
    """Monotonic time provider contract."""

    def now_monotonic(self) -> float:
        """Return current monotonic time in seconds."""
        ...

    def now_monotonic_ns(self) -> int:
        """Return current monotonic time in nanoseconds."""
        ...


@runtime_checkable
class SecretProvider(Protocol):
    """Cryptographic secret provider contract."""

    def get_secret(self, key_name: str) -> bytes:
        """Retrieve binary secret for key_name."""
        ...


# ---------------------------------------------------------------------------
# Structured Exception Taxonomy (Compatible with src/core/exceptions.py)
# ---------------------------------------------------------------------------


class PlatformError(Exception):
    """Root exception for Universal CAN-Bus Platform."""


class TransportError(PlatformError):
    """Base exception for transport layer failures."""


class IsoTpError(TransportError):
    """Base exception for all ISO 15765-2 DoCAN transport errors."""

    def __init__(self, message: str, code: str = "ISOTP_ERROR", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class IsoTpTimeoutError(IsoTpError):
    """ISO-TP protocol timing violation (N_As, N_Bs, N_Cr, N_Ar, N_Cs)."""

    def __init__(
        self,
        message: str,
        timeout_type: str = "N_Bs",
        elapsed_ms: float = 1000.0,
        limit_ms: float = 1000.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        d = {"timeout_type": timeout_type, "elapsed_ms": elapsed_ms, "limit_ms": limit_ms}
        if details:
            d.update(details)
        super().__init__(message, code=f"ISOTP_TIMEOUT_{timeout_type}", details=d)
        self.timeout_type = timeout_type
        self.elapsed_ms = elapsed_ms
        self.limit_ms = limit_ms


class IsoTpFlowControlError(IsoTpError):
    """ISO-TP Flow Control protocol error (WFTmax exceeded, invalid FlowStatus, malformed FC)."""

    def __init__(
        self,
        message: str,
        flow_status: int | None = None,
        wft_count: int | None = None,
        reason: str = "FLOW_CONTROL_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        d = {"flow_status": flow_status, "wft_count": wft_count, "reason": reason}
        if details:
            d.update(details)
        super().__init__(message, code="ISOTP_FLOW_CONTROL_ERROR", details=d)
        self.flow_status = flow_status
        self.wft_count = wft_count
        self.reason = reason


class IsoTpBufferOverflowError(IsoTpError):
    """ISO-TP Buffer Overflow error (FS=OVERFLOW received or FF_DL exceeds RX capacity)."""

    def __init__(
        self,
        message: str,
        requested_length: int = 0,
        max_buffer_size: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        d = {"requested_length": requested_length, "max_buffer_size": max_buffer_size}
        if details:
            d.update(details)
        super().__init__(message, code="ISOTP_BUFFER_OVERFLOW", details=d)
        self.requested_length = requested_length
        self.max_buffer_size = max_buffer_size


class IsoTpSequenceError(IsoTpError):
    """ISO-TP Consecutive Frame sequence number mismatch."""

    def __init__(
        self,
        expected_sn: int,
        actual_sn: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = f"ISO-TP Sequence Number mismatch: expected {expected_sn}, got {actual_sn}"
        d = {"expected_sn": expected_sn, "actual_sn": actual_sn}
        if details:
            d.update(details)
        super().__init__(message, code="ISOTP_SEQUENCE_ERROR", details=d)
        self.expected_sn = expected_sn
        self.actual_sn = actual_sn


class IsoTpInvalidPduError(IsoTpError):
    """ISO-TP malformed PDU header or invalid length specification."""

    def __init__(self, message: str, pci_type: int | None = None, raw_data: bytes | None = None) -> None:
        d = {"pci_type": pci_type, "raw_data_hex": raw_data.hex() if raw_data else None}
        super().__init__(message, code="ISOTP_INVALID_PDU", details=d)
        self.pci_type = pci_type
        self.raw_data = raw_data


class J1939TpError(TransportError):
    """Base exception for all SAE J1939 Transport Protocol failures."""


class J1939TpAbortError(J1939TpError):
    """Raised when a J1939 connection is explicitly aborted via TP.Conn_Abort."""

    def __init__(self, message: str, reason: int, target_pgn: int, sa: int, da: int) -> None:
        super().__init__(message)
        self.reason = reason
        self.target_pgn = target_pgn
        self.sa = sa
        self.da = da


class J1939SessionCollisionError(J1939TpError):
    """Raised when an RTS arrives for an active (SA, DA) session."""

    def __init__(self, message: str, sa: int, da: int, old_pgn: int, new_pgn: int) -> None:
        super().__init__(message)
        self.sa = sa
        self.da = da
        self.old_pgn = old_pgn
        self.new_pgn = new_pgn


class J1939SequenceError(J1939TpError):
    """Raised when an out-of-order TP.DT sequence number arrives."""

    def __init__(self, message: str, expected_seq: int, received_seq: int, sa: int, da: int) -> None:
        super().__init__(message)
        self.expected_seq = expected_seq
        self.received_seq = received_seq
        self.sa = sa
        self.da = da


class J1939TpTimeoutError(J1939TpError):
    """Raised when J1939 timing constraints (T1, T2, T3, T4) are violated."""


# ---------------------------------------------------------------------------
# Virtual CAN-Bus & Async Harness
# ---------------------------------------------------------------------------


class DeterministicClockProvider:
    """Deterministic monotonic clock provider for reproducible protocol tests."""

    def __init__(self, start_time: float = 1000.0) -> None:
        self._time: float = start_time

    def now_monotonic(self) -> float:
        return self._time

    def now_monotonic_ns(self) -> int:
        return int(self._time * 1_000_000_000)

    def advance(self, delta_seconds: float) -> None:
        """Advance the simulated clock forward."""
        if delta_seconds < 0:
            raise ValueError("Time cannot travel backwards")
        self._time += delta_seconds


class VirtualRxSubscriptionImpl:
    """Async queue subscription implementation."""

    def __init__(
        self,
        bus: VirtualCanBus,
        channel_id: str | None = None,
        arbitration_id: int | None = None,
        max_queue_size: int = 10000,
    ) -> None:
        self.bus = bus
        self.channel_id = channel_id
        self.arbitration_id = arbitration_id
        self.queue: asyncio.Queue[CanFrame] = asyncio.Queue(maxsize=max_queue_size)
        self.is_active: bool = True

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        if not self.is_active:
            return None
        try:
            if timeout_s is None:
                return await self.queue.get()
            return await asyncio.wait_for(self.queue.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None

    def put_nowait(self, frame: CanFrame) -> None:
        if not self.is_active:
            return
        if self.channel_id is not None and frame.channel_id != self.channel_id:
            return
        if self.arbitration_id is not None and frame.arbitration_id != self.arbitration_id:
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            pass  # Drop on overflow in test harness

    def unsubscribe(self) -> None:
        self.is_active = False
        self.bus.remove_subscription(self)


class VirtualTxPortImpl:
    """Virtual transmission port implementation."""

    def __init__(self, bus: VirtualCanBus, channel_id: str = "uds_ch0") -> None:
        self.bus = bus
        self.channel_id = channel_id

    async def send(self, frame: CanFrame) -> None:
        self.bus.broadcast_sync(frame)

    def send_sync(self, frame: CanFrame) -> None:
        self.bus.broadcast_sync(frame)


class VirtualCanBus:
    """In-memory multi-channel broadcast CAN bus for full protocol stack E2E testing."""

    def __init__(self, clock: ClockProvider | None = None) -> None:
        self.clock = clock or DeterministicClockProvider()
        self._subscriptions: list[VirtualRxSubscriptionImpl] = []
        self._history: list[CanFrame] = []
        self._listeners: list[Callable[[CanFrame], None]] = []

    def create_tx_port(self, channel_id: str = "uds_ch0") -> TxPort:
        """Create a new transmission port bound to this bus."""
        return VirtualTxPortImpl(self, channel_id=channel_id)

    def create_rx_subscription(
        self,
        channel_id: str | None = None,
        arbitration_id: int | None = None,
    ) -> RxSubscription:
        """Create a new asynchronous receive subscription."""
        sub = VirtualRxSubscriptionImpl(self, channel_id=channel_id, arbitration_id=arbitration_id)
        self._subscriptions.append(sub)
        return sub

    def add_listener(self, callback: Callable[[CanFrame], None]) -> None:
        """Register a synchronous frame tap/monitor callback."""
        self._listeners.append(callback)

    def remove_subscription(self, sub: VirtualRxSubscriptionImpl) -> None:
        """Unregister a subscription."""
        if sub in self._subscriptions:
            self._subscriptions.remove(sub)

    def broadcast_sync(self, frame: CanFrame) -> None:
        """Synchronously deliver frame to all matching subscribers and listeners."""
        self._history.append(frame)
        for listener in self._listeners:
            try:
                listener(frame)
            except Exception:
                pass
        for sub in list(self._subscriptions):
            sub.put_nowait(frame)

    @property
    def frame_history(self) -> list[CanFrame]:
        """Return full history of broadcast frames."""
        return list(self._history)

    def clear_history(self) -> None:
        """Clear recorded frames."""
        self._history.clear()


# ---------------------------------------------------------------------------
# Simulated UDS Diagnostic ECU Responder
# ---------------------------------------------------------------------------


class SimulatedUdsEcu:
    """Simulated ECU responding to UDS diagnostic requests over ISO-TP.

    Supports:
    - Service 0x10 (DiagnosticSessionControl)
    - Service 0x22 (ReadDataByIdentifier)
    - Service 0x27 (SecurityAccess Seed & Key)
    - Service 0x2E (WriteDataByIdentifier)
    - Service 0x31 (RoutineControl)
    - Service 0x34 / 0x36 / 0x37 (RequestDownload / TransferData / RequestTransferExit)
    - Configurable Flow Control responses (CTS, WAIT, OVERFLOW, Block Size, STmin)
    """

    def __init__(
        self,
        bus: VirtualCanBus,
        rx_id: int = 0x7E0,  # ECU listens to tester requests on 0x7E0
        tx_id: int = 0x7E8,  # ECU responds to tester on 0x7E8
        channel_id: str | None = None,
        is_fd: bool = False,
    ) -> None:
        self.bus = bus
        self.rx_id = rx_id
        self.tx_id = tx_id
        self.channel_id = channel_id or "uds_ch0"
        self.is_fd = is_fd

        self.rx_sub = bus.create_rx_subscription(channel_id=channel_id, arbitration_id=rx_id)
        self.tx_port = bus.create_tx_port(channel_id=self.channel_id)

        self.did_storage: dict[int, bytes] = {
            0xF190: b"WVWZZZ1KZAM000001",  # Standard 17-char VIN
            0xF189: b"SW_V02.10.04",  # Software Version
            0xF195: b"HW_REV_03A",  # Hardware Version
        }
        self.flashed_buffer = bytearray()
        self.session: int = 0x01  # Default session
        self.security_unlocked: bool = False
        self.seed_challenge: bytes = b"\x12\x34\x56\x78"

        # Protocol pacing controls
        self.fc_flow_status: int = 0  # 0=CTS, 1=WAIT, 2=OVERFLOW
        self.fc_block_size: int = 0  # 0 = continuous burst
        self.fc_st_min: int = 0  # 0 ms
        self.wait_frame_burst_count: int = 0  # Emit N wait frames before CTS
        self._is_running: bool = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start async ECU worker loop."""
        self._is_running = True
        self._task = asyncio.create_task(self._worker_loop())

    def stop(self) -> None:
        """Stop async ECU worker loop."""
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _worker_loop(self) -> None:
        """Continuously process incoming ISO-TP frames and generate UDS responses."""
        rx_buffer = bytearray()
        expected_len = 0
        expected_sn = 1

        while self._is_running:
            frame = await self.rx_sub.recv(timeout_s=0.1)
            if frame is None:
                continue

            data = frame.data
            if len(data) == 0:
                continue

            pci_type = data[0] >> 4

            if pci_type == 0x0:  # Single Frame
                if self.is_fd and (data[0] & 0x0F) == 0 and len(data) >= 2:
                    # CAN-FD Extended SF
                    sf_len = data[1]
                    req_payload = data[2 : 2 + sf_len]
                else:
                    # Classic SF
                    sf_len = data[0] & 0x0F
                    req_payload = data[1 : 1 + sf_len]

                resp = self._process_uds_request(req_payload)
                if resp:
                    await self._send_isotp_response(resp)

            elif pci_type == 0x1:  # First Frame
                if len(data) >= 6 and data[0] == 0x10 and data[1] == 0x00:
                    # Extended 32-bit First Frame
                    expected_len = int.from_bytes(data[2:6], byteorder="big")
                    header_len = 6
                else:
                    # Standard 12-bit First Frame
                    expected_len = ((data[0] & 0x0F) << 8) | data[1]
                    header_len = 2

                rx_buffer = bytearray(data[header_len:])
                expected_sn = 1

                # Send Flow Control
                if self.wait_frame_burst_count > 0:
                    for _ in range(self.wait_frame_burst_count):
                        await self._send_flow_control(fs=1, bs=self.fc_block_size, st_min=self.fc_st_min)
                        await asyncio.sleep(0.005)

                await self._send_flow_control(
                    fs=self.fc_flow_status,
                    bs=self.fc_block_size,
                    st_min=self.fc_st_min,
                )

                if self.fc_flow_status == 2:  # OVERFLOW -> abort
                    rx_buffer.clear()
                    expected_len = 0

            elif pci_type == 0x2:  # Consecutive Frame
                sn = data[0] & 0x0F
                if sn != expected_sn:
                    rx_buffer.clear()
                    expected_len = 0
                    continue

                rx_buffer.extend(data[1:])
                expected_sn = (expected_sn + 1) & 0x0F

                if len(rx_buffer) >= expected_len:
                    req_payload = bytes(rx_buffer[:expected_len])
                    rx_buffer.clear()
                    expected_len = 0
                    resp = self._process_uds_request(req_payload)
                    if resp:
                        await self._send_isotp_response(resp)

    def _process_uds_request(self, req: bytes) -> bytes | None:
        """Process UDS service request byte payload."""
        if not req:
            return None

        sid = req[0]

        # Service 0x10: DiagnosticSessionControl
        if sid == 0x10:
            sub_function = req[1] if len(req) > 1 else 0x01
            self.session = sub_function
            return bytes([0x50, sub_function, 0x00, 0x32, 0x01, 0xF4])  # P2=50ms, P2*=5000ms

        # Service 0x22: ReadDataByIdentifier
        elif sid == 0x22:
            if len(req) < 3:
                return bytes([0x7F, sid, 0x13])  # IncorrectMessageLengthOrInvalidFormat
            did = (req[1] << 8) | req[2]
            if did in self.did_storage:
                return bytes([0x62, req[1], req[2]]) + self.did_storage[did]
            return bytes([0x7F, sid, 0x31])  # RequestOutOfRange

        # Service 0x27: SecurityAccess
        elif sid == 0x27:
            sub_function = req[1] if len(req) > 1 else 0x01
            if sub_function == 0x01:  # Request Seed
                return bytes([0x67, 0x01]) + self.seed_challenge
            elif sub_function == 0x02:  # Send Key
                key = req[2:] if len(req) > 2 else b""
                # Key must be bitwise inverted seed
                expected_key = bytes([b ^ 0xFF for b in self.seed_challenge])
                if key == expected_key:
                    self.security_unlocked = True
                    return bytes([0x67, 0x02])
                return bytes([0x7F, sid, 0x35])  # InvalidKey

        # Service 0x2E: WriteDataByIdentifier
        elif sid == 0x2E:
            if len(req) < 4:
                return bytes([0x7F, sid, 0x13])
            did = (req[1] << 8) | req[2]
            value = req[3:]
            self.did_storage[did] = value
            return bytes([0x6E, req[1], req[2]])

        # Service 0x31: RoutineControl
        elif sid == 0x31:
            sub_function = req[1] if len(req) > 1 else 0x01
            routine_id = (req[2] << 8) | req[3] if len(req) > 3 else 0x0000
            return bytes([0x71, sub_function, (routine_id >> 8) & 0xFF, routine_id & 0xFF, 0x00])

        # Service 0x34: RequestDownload
        elif sid == 0x34:
            self.flashed_buffer.clear()
            # MaxNumberOfBlockLength = 4095 bytes (0x0F 0xFF)
            return bytes([0x74, 0x20, 0x0F, 0xFF])

        # Service 0x36: TransferData
        elif sid == 0x36:
            block_seq = req[1] if len(req) > 1 else 0x01
            chunk = req[2:] if len(req) > 2 else b""
            self.flashed_buffer.extend(chunk)
            return bytes([0x76, block_seq])

        # Service 0x37: RequestTransferExit
        elif sid == 0x37:
            return bytes([0x77])

        # Unknown / Generic Positive Echo
        return bytes([sid + 0x40]) + req[1:]

    async def _send_flow_control(self, fs: int, bs: int, st_min: int) -> None:
        """Transmit Flow Control frame to tester."""
        fc_payload = bytes([(0x3 << 4) | (fs & 0x0F), bs & 0xFF, st_min & 0xFF, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC])
        frame = CanFrame(
            channel_id=self.channel_id,
            arbitration_id=self.tx_id,
            dlc=8,
            data=fc_payload,
            is_extended=False,
            is_fd=self.is_fd,
            direction="tx",
        )
        await self.tx_port.send(frame)

    async def _send_isotp_response(self, data: bytes) -> None:
        """Segment and transmit UDS response payload."""
        data_len = len(data)
        if data_len == 0:
            return

        if data_len <= 7:
            # Classic Single Frame (P2-5: CAN_DL <= 8 uses the nibble form
            # on BOTH classic and FD — the escape form requires SF_DL >= 8)
            sf_payload = bytes([(0x0 << 4) | data_len]) + data + (b"\xcc" * (7 - data_len))
            if self.is_fd:
                # FD frames are padded to the 12-byte DLC minimum
                sf_payload = sf_payload + b"\xcc" * 4
                dlc = 9
                frame = CanFrame(
                    channel_id=self.channel_id,
                    arbitration_id=self.tx_id,
                    dlc=dlc,
                    data=sf_payload,
                    is_extended=False,
                    is_fd=True,
                    direction="tx",
                )
            else:
                frame = CanFrame(
                    channel_id=self.channel_id,
                    arbitration_id=self.tx_id,
                    dlc=8,
                    data=sf_payload,
                    is_extended=False,
                    is_fd=False,
                    direction="tx",
                )
            await self.tx_port.send(frame)
        elif self.is_fd and data_len <= 62:
            # CAN-FD Extended Single Frame
            sf_payload = bytes([0x00, data_len]) + data
            dlc = length_to_dlc(len(sf_payload))
            padded = pad_payload(sf_payload, dlc, pad_byte=0xCC)
            frame = CanFrame(
                channel_id=self.channel_id,
                arbitration_id=self.tx_id,
                dlc=dlc,
                data=padded,
                is_extended=False,
                is_fd=True,
                direction="tx",
            )
            await self.tx_port.send(frame)
        else:
            # Multi-Frame response (Standard 12-bit FF)
            max_cf_payload = 63 if self.is_fd else 7
            ff_payload_size = 62 if self.is_fd else 6

            ff_header = bytes([(0x1 << 4) | ((data_len >> 8) & 0x0F), data_len & 0xFF])
            ff_data = ff_header + data[:ff_payload_size]
            dlc = 15 if self.is_fd else 8
            padded_ff = pad_payload(ff_data, dlc, pad_byte=0xCC)

            frame = CanFrame(
                channel_id=self.channel_id,
                arbitration_id=self.tx_id,
                dlc=dlc,
                data=padded_ff,
                is_extended=False,
                is_fd=self.is_fd,
                direction="tx",
            )
            await self.tx_port.send(frame)

            # Transmit consecutive frames
            offset = ff_payload_size
            sn = 1
            while offset < data_len:
                chunk = data[offset : offset + max_cf_payload]
                cf_data = bytes([(0x2 << 4) | (sn & 0x0F)]) + chunk
                dlc = length_to_dlc(len(cf_data)) if self.is_fd else 8
                padded_cf = pad_payload(cf_data, dlc, pad_byte=0xCC)
                cf_frame = CanFrame(
                    channel_id=self.channel_id,
                    arbitration_id=self.tx_id,
                    dlc=dlc,
                    data=padded_cf,
                    is_extended=False,
                    is_fd=self.is_fd,
                    direction="tx",
                )
                await self.tx_port.send(cf_frame)
                offset += len(chunk)
                sn = (sn + 1) & 0x0F


# ---------------------------------------------------------------------------
# Simulated SAE J1939 ECU Node
# ---------------------------------------------------------------------------


class SimulatedJ1939Ecu:
    """Simulated SAE J1939 ECU supporting BAM broadcasts, CMDT handshakes, and cyclic telemetry."""

    def __init__(
        self,
        bus: VirtualCanBus,
        sa: int = 0x00,  # Engine ECU
        channel_id: str = "j1939_ch0",
    ) -> None:
        self.bus = bus
        self.sa = sa
        self.channel_id = channel_id
        self.tx_port = bus.create_tx_port(channel_id=channel_id)
        self.rx_sub = bus.create_rx_subscription(channel_id=channel_id)

    async def broadcast_bam(self, pgn: int, data: bytes) -> list[CanFrame]:
        """Broadcast multi-packet payload via BAM (DA=255) without waiting for CTS."""
        total_bytes = len(data)
        total_packets = (total_bytes + 6) // 7
        frames: list[CanFrame] = []

        # 1. TP.CM_BAM frame: PGN 60416 (0xEC00) -> CAN ID 0x1CECFF{SA:02X}
        bam_data = bytearray(8)
        bam_data[0] = 0x20  # TP_CTRL_BAM
        bam_data[1:3] = total_bytes.to_bytes(2, byteorder="little")
        bam_data[3] = total_packets
        bam_data[4] = 0xFF
        bam_data[5:8] = pgn.to_bytes(3, byteorder="little")

        cm_id = 0x1CECFF00 | (self.sa & 0xFF)
        cm_frame = CanFrame(
            channel_id=self.channel_id,
            arbitration_id=cm_id,
            dlc=8,
            data=bytes(bam_data),
            is_extended=True,
            direction="tx",
        )
        frames.append(cm_frame)
        await self.tx_port.send(cm_frame)

        # 2. TP.DT data frames: PGN 60160 (0xEB00) -> CAN ID 0x1CEBFF{SA:02X}
        dt_id = 0x1CEBFF00 | (self.sa & 0xFF)
        for seq in range(1, total_packets + 1):
            start = (seq - 1) * 7
            chunk = data[start : start + 7]
            dt_payload = bytes([seq]) + chunk
            if len(dt_payload) < 8:
                dt_payload = dt_payload + (b"\xff" * (8 - len(dt_payload)))
            dt_frame = CanFrame(
                channel_id=self.channel_id,
                arbitration_id=dt_id,
                dlc=8,
                data=dt_payload,
                is_extended=True,
                direction="tx",
            )
            frames.append(dt_frame)
            await self.tx_port.send(dt_frame)

        return frames

    async def send_cmdt_rts(self, target_da: int, pgn: int, data: bytes) -> list[CanFrame]:
        """Send Point-to-Point RTS frame."""
        total_bytes = len(data)
        total_packets = (total_bytes + 6) // 7

        rts_data = bytearray(8)
        rts_data[0] = 0x10  # TP_CTRL_RTS
        rts_data[1:3] = total_bytes.to_bytes(2, byteorder="little")
        rts_data[3] = total_packets
        rts_data[4] = 0xFF
        rts_data[5:8] = pgn.to_bytes(3, byteorder="little")

        cm_id = 0x1CEC0000 | ((target_da & 0xFF) << 8) | (self.sa & 0xFF)
        rts_frame = CanFrame(
            channel_id=self.channel_id,
            arbitration_id=cm_id,
            dlc=8,
            data=bytes(rts_data),
            is_extended=True,
            direction="tx",
        )
        await self.tx_port.send(rts_frame)
        return [rts_frame]

    async def send_cyclic_eec1(self, actual_torque_percent: float, speed_rpm: float) -> CanFrame:
        """Transmit Electronic Engine Controller 1 (EEC1, PGN 61444 / 0xF004) frame with SPN 513."""
        raw_torque = int(round(actual_torque_percent + 125))  # Standard 8-bit SPN 513 alternative
        raw_torque_16 = int(round(actual_torque_percent))  # Signed 16-bit SPN 513
        raw_speed = int(round(speed_rpm * 8.0))  # 0.125 rpm/bit

        payload = bytearray(8)
        payload[0] = 0x00  # Engine Torque Mode
        payload[1] = raw_torque & 0xFF  # Drivers Demand Engine - Percent Torque
        payload[2] = raw_torque & 0xFF  # Actual Engine - Percent Torque
        payload[3:5] = raw_speed.to_bytes(2, byteorder="little")  # Engine Speed
        payload[5] = 0xFF  # Source Address
        payload[6:8] = raw_torque_16.to_bytes(2, byteorder="little", signed=True)  # 16-bit torque representation

        can_id = 0x0CF00400 | (self.sa & 0xFF)
        frame = CanFrame(
            channel_id=self.channel_id,
            arbitration_id=can_id,
            dlc=8,
            data=bytes(payload),
            is_extended=True,
            direction="tx",
        )
        await self.tx_port.send(frame)
        return frame

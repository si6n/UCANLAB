"""Active Diagnostic Poller & Multi-Rate Scheduler Engine.

Provides deterministic, thread-safe, and asynchronous polling for SAE J1979 OBD-II Mode 01
PIDs and ISO 14229 UDS DIDs (Service 0x22) over CAN and ISO-TP.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, ClassVar

from src.core.contracts.ports import ClockProvider, RxSubscription, SystemClockProvider, TxPort
from src.core.errors import ProtocolError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.protocols.obd.models import ObdDtcResult, ObdPidResult, UdsDidResult
from src.protocols.obd.pids import OBD_PID_REGISTRY, ObdPidRegistry
from src.protocols.uds.did_database import UDS_DID_REGISTRY, UdsDidRegistry
from src.protocols.uds.isotp import IsoTpTransport
from src.protocols.uds.nrc import UdsNrc

logger = get_logger("protocols.obd.poller")

# REVIEW (LOW-3): SAE J1979 DTC readback modes supported by the poller.
#   0x03 = stored (confirmed) DTCs
#   0x07 = pending DTCs (detected during current/last cycle)
#   0x0A = permanent DTCs (clear-resistant, EPA-mandated)
OBD_DTC_MODES: frozenset[int] = frozenset({0x03, 0x07, 0x0A})
# Positive response SID = mode + 0x40.
OBD_DTC_RESPONSE_SIDS: dict[int, int] = {0x03: 0x43, 0x07: 0x47, 0x0A: 0x4A}

# Timing Constants (ISO 14229 / SAE J1979)
DEFAULT_P2_TIMEOUT_S: float = 2.0
P2_STAR_TIMEOUT_S: float = 5.0
DEFAULT_MAX_RATE_HZ: float = 40.0
DEFAULT_MAX_RETRIES: int = 3
BASE_BACKOFF_S: float = 0.050
# REVIEW hardening: hard ceiling for the aggregate poll rate (50 Hz OBD)
# and for every individual job — uncapped rates flood the bus / lock the
# ECU. Exceeding the ceiling raises ValueError (fail-closed).
MAX_RATE_HZ: float = 50.0


class PollerState(str, Enum):
    """Diagnostic transaction finite state machine states."""

    IDLE = "IDLE"
    ENQUEUED = "ENQUEUED"
    ISO_TP_TRANSMIT = "ISO_TP_TRANSMIT"
    WAITING_FOR_RESPONSE = "WAITING_FOR_RESPONSE"
    WAITING_P2_STAR = "WAITING_P2_STAR"
    PROCESSING_PAYLOAD = "PROCESSING_PAYLOAD"
    RETRY_BACKOFF = "RETRY_BACKOFF"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(slots=True)
class PollerJob:
    """Registered diagnostic polling job descriptor."""

    kind: str  # "obd_pid" | "uds_did" | "obd_dtc_mode"
    identifier: int  # PID (0x00..0xFF), DID (0x0000..0xFFFF), or Mode 03/07/0A
    rate_hz: float  # Polling rate in Hertz
    callback: Callable[[Any], None]
    priority: int = 5  # Higher number = higher execution priority (1..10)
    tx_id: int = 0x7DF
    rx_id: int = 0x7E8
    next_run_s: float = 0.0
    last_run_s: float = 0.0
    consecutive_failures: int = 0
    state: PollerState = PollerState.IDLE
    retry_count: int = 0
    response_deadline_s: float = 0.0
    # REVIEW 3-M (transaction identity): monotonically increasing id
    # assigned by step() at dispatch. A response only completes the job
    # whose transaction_id matches the dispatch that is still current —
    # a late frame from an aborted/retried request can never complete a
    # NEWER transaction on the same conversation (orphan conversation fix).
    transaction_id: int = 0

    @property
    def interval_s(self) -> float:
        """Sampling interval in fractional seconds."""
        return 1.0 / self.rate_hz if self.rate_hz > 0 else 1.0

    # P-e: the priority-heap __lt__ was removed — scheduling uses an explicit
    # sort key (priority, next_run_s) in step(), no heap ordering remains.


class ActiveDiagnosticPoller:
    """Multi-Rate Diagnostic Poller Scheduler & Request/Response State Machine.

    Schedules periodic OBD-II and UDS queries across Fast (50Hz), Medium (10Hz),
    and Slow (1Hz) telemetry bands while routing frames strictly through TxPort.
    """

    def __init__(
        self,
        tx_port: TxPort,
        rx_subscription: RxSubscription | None = None,
        isotp_transport: IsoTpTransport | None = None,
        clock_provider: ClockProvider | None = None,
        channel_id: str = "obd_ch0",
        tx_id: int = 0x7DF,
        rx_id: int = 0x7E8,
        max_rate_hz: float = DEFAULT_MAX_RATE_HZ,
        obd_registry: ObdPidRegistry | None = None,
        uds_registry: UdsDidRegistry | None = None,
    ) -> None:
        self.tx_port = tx_port
        self.rx_subscription = rx_subscription
        self.channel_id = channel_id
        self.default_tx_id = tx_id
        self.default_rx_id = rx_id
        # REVIEW hardening: aggregate rate ceiling (50 Hz); above the cap
        # is a configuration error, not a faster poller.
        if max_rate_hz > MAX_RATE_HZ:
            raise ValueError(
                f"max_rate_hz {max_rate_hz} exceeds ceiling {MAX_RATE_HZ} Hz (fail-closed)"
            )
        self.max_rate_hz = max(1.0, max_rate_hz)
        self.min_tx_interval_s = 1.0 / self.max_rate_hz

        self.clock: ClockProvider = clock_provider or SystemClockProvider()
        self.obd_registry: ObdPidRegistry = obd_registry or OBD_PID_REGISTRY
        self.uds_registry: UdsDidRegistry = uds_registry or UDS_DID_REGISTRY

        self.isotp = isotp_transport or IsoTpTransport(
            tx_id=tx_id,
            rx_id=rx_id,
            channel_id=channel_id,
        )
        # REVIEW (DID transport binding): UDS DID jobs default to the
        # PHYSICAL request id 0x7E0 (not the functional 0x7DF broadcast) —
        # but the old transport cache keyed only by RX id, so 0x7E8 already
        # mapped to the 0x7DF transport and multi-frame VIN replies sent
        # their Flow Control to 0x7DF. Key transports by (tx, rx) pairs so
        # the FC goes out on the same conversation the request did.
        self._isotp_transports: dict[tuple[int, int], IsoTpTransport] = {
            (tx_id, rx_id): self.isotp
        }

        self._lock = threading.Lock()
        self._jobs: dict[tuple[str, int], PollerJob] = {}
        self._last_tx_time_s: float = 0.0

        # REVIEW 3-M (transaction identity): monotonically increasing
        # transaction counter — every step() dispatch mints a fresh id so
        # stale/late responses cannot complete a newer transaction. The
        # CURRENT id is tracked PER CONVERSATION (tx_id, rx_id): a dispatch
        # to ECU B must not invalidate ECU A's in-flight transaction.
        self._transaction_counter: int = 0
        self._conversation_txn: dict[tuple[int, int], int] = {}

        # State tracking for active transaction
        self._active_job: PollerJob | None = None
        self._state: PollerState = PollerState.IDLE

        # Background async task management
        self._running: bool = False
        self._loop_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        """Return True if background async polling loop is active."""
        return self._running

    @property
    def current_state(self) -> PollerState:
        """Return the current diagnostic state machine state."""
        return self._state

    def register_pid(
        self,
        pid: int,
        rate_hz: float,
        callback: Callable[[ObdPidResult], None],
        priority: int = 5,
        tx_id: int | None = None,
        rx_id: int | None = None,
    ) -> None:
        """Register a periodic OBD-II Mode 01 PID polling job."""
        if not (0 <= pid <= 0xFF):
            raise ValueError(f"Invalid OBD PID 0x{pid:X}: must be in range 0x00..0xFF")
        # REVIEW hardening: per-job rate ceiling (bus-flood protection).
        if rate_hz > MAX_RATE_HZ:
            raise ValueError(
                f"PID 0x{pid:02X} rate_hz {rate_hz} exceeds ceiling {MAX_RATE_HZ} Hz (fail-closed)"
            )
        with self._lock:
            key = ("obd_pid", pid)
            now = self.clock.now_monotonic()
            effective_tx_id = tx_id if tx_id is not None else self.default_tx_id
            effective_rx_id = rx_id if rx_id is not None else self.default_rx_id
            job = PollerJob(
                kind="obd_pid",
                identifier=pid,
                rate_hz=rate_hz,
                callback=callback,
                priority=priority,
                tx_id=effective_tx_id,
                rx_id=effective_rx_id,
                next_run_s=now,
                state=PollerState.IDLE,
            )
            self._jobs[key] = job
            self._get_transport_for(effective_tx_id, effective_rx_id)
            logger.info("Registered OBD PID job", extra={"pid": hex(pid), "rate_hz": rate_hz})

    def register_did(
        self,
        did: int,
        rate_hz: float,
        callback: Callable[[UdsDidResult], None],
        priority: int = 5,
        tx_id: int | None = None,
        rx_id: int | None = None,
    ) -> None:
        """Register a periodic ISO 14229 UDS DID (Service 0x22) polling job."""
        if not (0 <= did <= 0xFFFF):
            raise ValueError(f"Invalid UDS DID 0x{did:X}: must be in range 0x0000..0xFFFF")
        # REVIEW hardening: per-job rate ceiling (bus-flood protection).
        if rate_hz > MAX_RATE_HZ:
            raise ValueError(
                f"DID 0x{did:04X} rate_hz {rate_hz} exceeds ceiling {MAX_RATE_HZ} Hz (fail-closed)"
            )
        with self._lock:
            key = ("uds_did", did)
            now = self.clock.now_monotonic()
            effective_tx_id = tx_id if tx_id is not None else 0x7E0
            effective_rx_id = rx_id if rx_id is not None else self.default_rx_id
            job = PollerJob(
                kind="uds_did",
                identifier=did,
                rate_hz=rate_hz,
                callback=callback,
                priority=priority,
                tx_id=effective_tx_id,
                rx_id=effective_rx_id,
                next_run_s=now,
                state=PollerState.IDLE,
            )
            self._jobs[key] = job
            self._get_transport_for(effective_tx_id, effective_rx_id)
            logger.info("Registered UDS DID job", extra={"did": hex(did), "rate_hz": rate_hz})

    def register_dtc_mode(
        self,
        mode: int,
        rate_hz: float,
        callback: Callable[[ObdDtcResult], None],
        priority: int = 5,
        tx_id: int | None = None,
        rx_id: int | None = None,
    ) -> None:
        """Register a periodic SAE J1979 DTC readback job (LOW-3).

        mode: 0x03 (stored DTCs), 0x07 (pending DTCs) or 0x0A (permanent
        DTCs). The request carries no PID byte — [0x01, mode] per SAE J1979.
        """
        if mode not in OBD_DTC_MODES:
            raise ValueError(f"Invalid OBD DTC mode 0x{mode:02X}: must be 0x03, 0x07 or 0x0A")
        if rate_hz > MAX_RATE_HZ:
            raise ValueError(
                f"DTC mode 0x{mode:02X} rate_hz {rate_hz} exceeds ceiling {MAX_RATE_HZ} Hz (fail-closed)"
            )
        with self._lock:
            key = ("obd_dtc_mode", mode)
            now = self.clock.now_monotonic()
            effective_tx_id = tx_id if tx_id is not None else self.default_tx_id
            effective_rx_id = rx_id if rx_id is not None else self.default_rx_id
            job = PollerJob(
                kind="obd_dtc_mode",
                identifier=mode,
                rate_hz=rate_hz,
                callback=callback,
                priority=priority,
                tx_id=effective_tx_id,
                rx_id=effective_rx_id,
                next_run_s=now,
                state=PollerState.IDLE,
            )
            self._jobs[key] = job
            self._get_transport_for(effective_tx_id, effective_rx_id)
            logger.info("Registered OBD DTC mode job", extra={"mode": hex(mode), "rate_hz": rate_hz})

    def unregister_dtc_mode(self, mode: int) -> bool:
        """Unregister a DTC readback job. Returns True if job was found and removed."""
        with self._lock:
            key = ("obd_dtc_mode", mode)
            if key in self._jobs:
                del self._jobs[key]
                return True
            return False

    def unregister_pid(self, pid: int) -> bool:
        """Unregister an OBD-II PID job. Returns True if job was found and removed."""
        with self._lock:
            key = ("obd_pid", pid)
            if key in self._jobs:
                del self._jobs[key]
                return True
            return False

    def unregister_did(self, did: int) -> bool:
        """Unregister a UDS DID job. Returns True if job was found and removed."""
        with self._lock:
            key = ("uds_did", did)
            if key in self._jobs:
                del self._jobs[key]
                return True
            return False

    def get_registered_jobs(self) -> list[PollerJob]:
        """Return list of all currently registered polling jobs."""
        with self._lock:
            return list(self._jobs.values())

    def build_request_frame(self, job: PollerJob) -> CanFrame:
        """Construct standard CAN request frame for an OBD PID or UDS DID job."""
        if job.kind == "obd_pid":
            # SAE J1979 Mode 01 Request: [0x02, 0x01, PID, 0x55, 0x55, 0x55, 0x55, 0x55]
            payload = bytes([0x02, 0x01, job.identifier, 0x55, 0x55, 0x55, 0x55, 0x55])
            return CanFrame.create(
                channel_id=self.channel_id,
                arbitration_id=job.tx_id,
                data=payload,
                is_extended=job.tx_id > 0x7FF,
                direction="tx",
            )
        elif job.kind == "obd_dtc_mode":
            # SAE J1979 Mode 03/07/0A request — no PID byte:
            # [0x01, mode, 0x55, ...]
            payload = bytes([0x01, job.identifier, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55])
            return CanFrame.create(
                channel_id=self.channel_id,
                arbitration_id=job.tx_id,
                data=payload,
                is_extended=job.tx_id > 0x7FF,
                direction="tx",
            )
        elif job.kind == "uds_did":
            # ISO 14229 Service 0x22 (ReadDataByIdentifier): [0x03, 0x22, DID_HI, DID_LO, 0x55, 0x55, 0x55, 0x55]
            did = job.identifier
            payload = bytes([0x03, 0x22, (did >> 8) & 0xFF, did & 0xFF, 0x55, 0x55, 0x55, 0x55])
            return CanFrame.create(
                channel_id=self.channel_id,
                arbitration_id=job.tx_id,
                data=payload,
                is_extended=job.tx_id > 0x7FF,
                direction="tx",
            )
        else:
            raise ValueError(f"Unknown job kind: {job.kind}")

    def _get_transport_for(self, tx_id: int, rx_id: int) -> IsoTpTransport:
        """Return (and lazily create) the transport for a (tx, rx) conversation.

        REVIEW (transport binding): FC frames travel on the transport's
        TX id; sharing one transport across conversations with different
        request ids sent the FC to the wrong CAN id (e.g. physical DID
        requests at 0x7E0 whose multi-frame replies were FC'd via 0x7DF).
        """
        key = (tx_id, rx_id)
        transport = self._isotp_transports.get(key)
        if transport is None:
            transport = IsoTpTransport(
                tx_id=tx_id,
                rx_id=rx_id,
                channel_id=self.channel_id,
            )
            self._isotp_transports[key] = transport
        return transport

    def process_rx_frame(
        self, frame: CanFrame
    ) -> tuple[ObdPidResult | ObdDtcResult | UdsDidResult | None, CanFrame | None]:
        """Process incoming CAN frame against the active job or registered listeners.

        Returns: (DecodedResult, OptionalFlowControlFrame)
        """
        with self._lock:
            active = self._active_job
            # REVIEW (conversation-bound reassembly): route the frame
            # through the transport of the ACTIVE job's conversation — the
            # old arbitration-id-only lookup (with a .get() default) fed a
            # response into whichever transport happened to hold the rx id,
            # and one-shot rx_id overrides were dropped by the default
            # transport entirely.
            #
            # REVIEW 3-M (orphan conversation): a frame arriving for a
            # conversation that is NOT the single active slot (multi-ECU
            # polling: ECU A responding while ECU B's request is the
            # "active" one) previously went through B's transport — where
            # it was dropped, orphaning A's in-flight transaction. Resolve
            # the owning job FIRST: any job WAITING on this rx id owns the
            # frame, active or not.
            owner: PollerJob | None = None
            for job in self._jobs.values():
                if (
                    job.state in (PollerState.WAITING_FOR_RESPONSE, PollerState.WAITING_P2_STAR)
                    and job.rx_id == frame.arbitration_id
                ):
                    owner = job
                    break
            if owner is None and active is not None:
                owner = active
            if owner is not None:
                self._active_job = owner
                active = owner
                isotp = self._get_transport_for(owner.tx_id, owner.rx_id)
            else:
                # No waiting job on this rx id: reassemble on every known
                # conversation that listens on this arbitration id (flow
                # control still flows).
                isotp = next(
                    (t for (tx, rx), t in self._isotp_transports.items() if rx == frame.arbitration_id),
                    self.isotp,
                )

        # Filter by channel if applicable
        if frame.channel_id != self.channel_id and self.channel_id:
            return None, None

        # Reassemble using appropriate IsoTpTransport
        completed_payload, fc_frame = isotp.handle_rx_frame(frame)

        if completed_payload is None:
            return None, fc_frame

        # We have a reassembled diagnostic payload
        decoded_result: ObdPidResult | ObdDtcResult | UdsDidResult | None = None
        now = self.clock.now_monotonic()

        if len(completed_payload) < 2:
            return None, fc_frame

        sid = completed_payload[0]
        cb_to_dispatch = None

        with self._lock:
            active = self._active_job

            # --------------------------------------------------------------------
            # 1. Negative Response Handling (SID 0x7F)
            # --------------------------------------------------------------------
            if sid == 0x7F and len(completed_payload) >= 3:
                rejected_sid = completed_payload[1]
                nrc = completed_payload[2]

                # The rejected SID must echo the request SID of the active
                # job's kind: Mode 01 PID -> 0x01, UDS 0x22 -> 0x22, DTC
                # readback -> the mode itself (0x03 / 0x07 / 0x0A).
                request_sid_by_kind = {"obd_pid": 0x01, "uds_did": 0x22}
                expected_sid = (
                    request_sid_by_kind.get(active.kind, active.identifier)
                    if active is not None
                    else None
                )
                if expected_sid is not None and rejected_sid != expected_sid:
                    # Unsolicited / mismatched negative response
                    return None, fc_frame

                if nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING:  # NRC 0x78
                    if active is not None:
                        active.state = PollerState.WAITING_P2_STAR
                        active.response_deadline_s = now + P2_STAR_TIMEOUT_S
                        self._state = PollerState.WAITING_P2_STAR
                        logger.info("Received NRC 0x78 (Response Pending) — extended P2* armed")
                    return None, fc_frame

                elif nrc == UdsNrc.BUSY_REPEAT_REQUEST:  # NRC 0x21
                    if active is not None:
                        active.state = PollerState.RETRY_BACKOFF
                        self._schedule_retry(active, now)
                    return None, fc_frame

                else:
                    # Other Negative Response
                    if active is not None:
                        active.state = PollerState.FAILED
                        active.consecutive_failures += 1
                        self._state = PollerState.FAILED
                    return None, fc_frame

            # --------------------------------------------------------------------
            # 2. Positive OBD Mode 01 Response (SID 0x41)
            # --------------------------------------------------------------------
            if sid == 0x41:
                # REVIEW (attribution): a positive response only completes
                # the ACTIVE job when it arrived on that job's own RX id —
                # ECU B's 0x7E9 reply must never complete ECU A's 0x7E8 poll.
                pid = completed_payload[1]
                raw_data = completed_payload[2:]
                result = self.obd_registry.decode(pid, raw_data)
                decoded_result = result

                if (
                    active is not None
                    and active.kind == "obd_pid"
                    and active.identifier == pid
                    and active.rx_id == frame.arbitration_id
                    and active.transaction_id
                    == self._conversation_txn.get((active.tx_id, active.rx_id))  # REVIEW 3-M
                ):
                    active.state = PollerState.COMPLETED
                    active.consecutive_failures = 0
                    active.retry_count = 0
                    self._state = PollerState.COMPLETED
                    self._active_job = None
                    cb_to_dispatch = (active.callback, result)

            # --------------------------------------------------------------------
            # 2b. Positive DTC Readback Response (SID 0x43 / 0x47 / 0x4A)
            # --------------------------------------------------------------------
            elif sid in (0x43, 0x47, 0x4A):
                # SAE J1979: byte 1 = DTC count, then 2-byte DTC pairs.
                # Zero-fabrication: only pairs physically present decode;
                # a payload shorter than the declared count is flagged.
                req_mode = sid - 0x40
                dtc_count = completed_payload[1]
                pair_bytes = bytes(completed_payload[2:])
                declared_available = len(pair_bytes) // 2
                pairs = [
                    (pair_bytes[2 * i], pair_bytes[2 * i + 1])
                    for i in range(min(dtc_count, declared_available))
                ]
                from src.protocols.obd.models import decode_dtc_pair

                dtcs = tuple(decode_dtc_pair(a, b) for a, b in pairs)
                result_dtc = ObdDtcResult(
                    mode=req_mode,
                    dtc_count=dtc_count,
                    dtcs=dtcs,
                    raw_bytes=bytes(completed_payload),
                    is_valid=(dtc_count <= declared_available),
                    error_message=(
                        None
                        if dtc_count <= declared_available
                        else f"payload carries {declared_available} DTC pairs, ECU declared {dtc_count}"
                    ),
                )
                decoded_result = result_dtc

                if (
                    active is not None
                    and active.kind == "obd_dtc_mode"
                    and active.identifier == req_mode
                    and active.rx_id == frame.arbitration_id
                    and active.transaction_id
                    == self._conversation_txn.get((active.tx_id, active.rx_id))  # REVIEW 3-M
                ):
                    active.state = PollerState.COMPLETED
                    active.consecutive_failures = 0
                    active.retry_count = 0
                    self._state = PollerState.COMPLETED
                    self._active_job = None
                    cb_to_dispatch = (active.callback, result_dtc)

            # --------------------------------------------------------------------
            # 3. Positive UDS Service 0x22 Response (SID 0x62)
            # --------------------------------------------------------------------
            elif sid == 0x62 and len(completed_payload) >= 3:
                # REVIEW (attribution): see the 0x41 branch — the RX id must
                # match the active job's endpoint before completion.
                did = (completed_payload[1] << 8) | completed_payload[2]
                raw_data = completed_payload[3:]
                result = self.uds_registry.decode(did, raw_data)
                decoded_result = result

                if (
                    active is not None
                    and active.kind == "uds_did"
                    and active.identifier == did
                    and active.rx_id == frame.arbitration_id
                    and active.transaction_id
                    == self._conversation_txn.get((active.tx_id, active.rx_id))  # REVIEW 3-M
                ):
                    active.state = PollerState.COMPLETED
                    active.consecutive_failures = 0
                    active.retry_count = 0
                    self._state = PollerState.COMPLETED
                    self._active_job = None
                    cb_to_dispatch = (active.callback, result)

        if cb_to_dispatch is not None:
            cb, res = cb_to_dispatch
            try:
                cb(res)
            except Exception as cb_err:
                logger.error("Error in poller callback", extra={"error": str(cb_err)})

        return decoded_result, fc_frame

    def _schedule_retry(self, job: PollerJob, now: float) -> None:
        """Schedule exponential backoff retry for a failed or busy job."""
        job.retry_count += 1
        if job.retry_count > DEFAULT_MAX_RETRIES:
            job.state = PollerState.FAILED
            job.consecutive_failures += 1
            job.next_run_s = now + job.interval_s
            self._state = PollerState.FAILED
            self._active_job = None
        else:
            backoff_delay = BASE_BACKOFF_S * (2 ** (job.retry_count - 1))
            job.next_run_s = now + backoff_delay
            job.state = PollerState.RETRY_BACKOFF
            self._state = PollerState.RETRY_BACKOFF
            # Release the active slot: while parked in backoff the job is not
            # "awaiting a response", and step() must be free to reselect it
            # (with a fresh response deadline) once the backoff elapses.
            self._active_job = None

    def step(self, current_time: float | None = None) -> PollerJob | None:
        """Perform a single deterministic scheduling and execution step.

        Selects the highest priority due job, enforces rate limits, constructs
        the request frame, and sends it synchronously/asynchronously via TxPort.
        """
        now = current_time if current_time is not None else self.clock.now_monotonic()

        with self._lock:
            # Check timeout on current active job
            if self._active_job is not None:
                if now >= self._active_job.response_deadline_s:
                    logger.warning("Diagnostic request timed out", extra={"id": hex(self._active_job.identifier)})
                    self._schedule_retry(self._active_job, now)
                    self._active_job = None

            # Check rate limiter
            if (now - self._last_tx_time_s) < self.min_tx_interval_s:
                return None

            # Single-flight rule per conversation (tx_id, rx_id): do not collide on the same ECU
            busy_endpoints = set()
            if self._active_job is not None and self._active_job.state in (
                PollerState.WAITING_FOR_RESPONSE,
                PollerState.WAITING_P2_STAR,
            ):
                busy_endpoints.add((self._active_job.tx_id, self._active_job.rx_id))

            # Find candidate jobs that are due
            due_jobs: list[PollerJob] = [
                j
                for j in self._jobs.values()
                if j.next_run_s <= now and j != self._active_job and (j.tx_id, j.rx_id) not in busy_endpoints
            ]

            if not due_jobs:
                return None

            # Sort by priority descending, then next_run_s ascending
            due_jobs.sort(key=lambda j: (-j.priority, j.next_run_s))
            selected_job = due_jobs[0]

            self._active_job = selected_job
            # REVIEW 3-M (transaction identity): mint a fresh monotonic
            # transaction id at dispatch and pin it to this conversation —
            # process_rx_frame only completes the job when this id is still
            # current for (tx_id, rx_id), so a late frame from a previous
            # (timed-out / retried) request on the same conversation can
            # never complete this transaction.
            self._transaction_counter += 1
            selected_job.transaction_id = self._transaction_counter
            self._conversation_txn[(selected_job.tx_id, selected_job.rx_id)] = self._transaction_counter
            selected_job.state = PollerState.ENQUEUED
            selected_job.last_run_s = now
            selected_job.next_run_s = now + selected_job.interval_s
            selected_job.response_deadline_s = now + DEFAULT_P2_TIMEOUT_S
            self._state = PollerState.ENQUEUED
            self._last_tx_time_s = now

        # Transmit request frame
        req_frame = self.build_request_frame(selected_job)
        selected_job.state = PollerState.ISO_TP_TRANSMIT
        self._state = PollerState.WAITING_FOR_RESPONSE

        # Send via TxPort
        try:
            self.tx_port.send_sync(req_frame)
        except Exception as exc:
            logger.error("Failed to send diagnostic frame", extra={"error": str(exc)})
            selected_job.state = PollerState.FAILED
            self._state = PollerState.FAILED
            self._active_job = None
            return selected_job

        selected_job.state = PollerState.WAITING_FOR_RESPONSE
        return selected_job

    async def _await_diagnostic_response(
        self,
        *,
        req_frame: CanFrame,
        target_rx_id: int | tuple[int, ...] | None = None,
        timeout_s: float,
        protocol_name: str,
        match_response: Callable[[bytes], Awaitable[object | None]],
    ) -> object:
        """Shared one-shot exchange: send request, pump RX until matched or timed out.

        The per-protocol response matching (positive service echo vs negative
        response with NRC) is supplied by the caller; a None return from the
        matcher means "not the final answer, keep waiting".

        REVIEW 1-M5: `target_rx_id` may be a TUPLE of IDs — a functional
        (0x7DF) request is answered by every compliant ECU from its own
        physical response ID (0x7E8..0x7EF); with a single target the
        poller silently discarded every other ECU's answer. Each source
        ID reassembles on its own ISO-TP transport.
        """
        if self.rx_subscription is None:
            raise ProtocolError(
                "RxSubscription is required for asynchronous one-shot diagnostic queries",
                code=f"{protocol_name}_NO_SUBSCRIPTION",
                details={"protocol": protocol_name},
            )

        await self.tx_port.send(req_frame)

        # REVIEW (one-shot transport): reassembly must run on the transport
        # of THIS request's (tx, rx) conversation — the shared default
        # transport dropped every response whose rx_id differed from the
        # constructor default.
        if isinstance(target_rx_id, tuple):
            rx_ids: tuple[int, ...] = target_rx_id
        else:
            rx_ids = (target_rx_id if target_rx_id is not None else self.default_rx_id,)
        isotp_by_rx = {rid: self._get_transport_for(req_frame.arbitration_id, rid) for rid in rx_ids}

        start_time = self.clock.now_monotonic()
        deadline = start_time + timeout_s

        while self.clock.now_monotonic() < deadline:
            remaining = deadline - self.clock.now_monotonic()
            if remaining <= 0:
                break
            frame = await self.rx_subscription.recv(timeout_s=remaining)
            if frame is None:
                break

            if frame.channel_id != self.channel_id:
                continue

            rid = frame.arbitration_id
            if rid not in isotp_by_rx or len(frame.data) < 3:
                continue

            # Process payload
            completed, fc = isotp_by_rx[rid].handle_rx_frame(frame)
            if fc is not None:
                await self.tx_port.send(fc)

            if completed is not None:
                result = await match_response(completed)
                if result is not None:
                    return result

        target_desc = "/".join(f"0x{r:03X}" for r in rx_ids)
        raise TimeoutError(
            f"Timeout ({timeout_s}s) waiting for response to {protocol_name} request "
            f"(tx 0x{req_frame.arbitration_id:03X} / rx {target_desc})"
        )

    async def poll_pid_once(
        self,
        pid: int,
        tx_id: int | None = None,
        rx_id: int | None = None,
        timeout_s: float = DEFAULT_P2_TIMEOUT_S,
    ) -> ObdPidResult:
        """Perform a one-shot asynchronous query for an OBD-II Mode 01 PID."""
        target_tx_id = tx_id if tx_id is not None else self.default_tx_id
        target_rx_id = rx_id if rx_id is not None else self.default_rx_id

        payload = bytes([0x02, 0x01, pid & 0xFF, 0x55, 0x55, 0x55, 0x55, 0x55])
        req_frame = CanFrame.create(
            channel_id=self.channel_id,
            arbitration_id=target_tx_id,
            data=payload,
            is_extended=target_tx_id > 0x7FF,
            direction="tx",
        )

        async def _match_pid(completed: bytes) -> object | None:
            if completed[0] == 0x41 and completed[1] == pid:
                return self.obd_registry.decode(pid, completed[2:])
            if completed[0] == 0x7F and completed[1] == 0x01:
                nrc = completed[2]
                if nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING:
                    # P2* extension: keep the outer deadline; keep waiting
                    return None
                raise ProtocolError(
                    f"OBD PID 0x{pid:02X} rejected with NRC 0x{nrc:02X}",
                    code="OBD_NEGATIVE_RESPONSE",
                    details={"pid": pid, "nrc": nrc},
                )
            return None

        result = await self._await_diagnostic_response(
            req_frame=req_frame,
            target_rx_id=target_rx_id if isinstance(target_rx_id, tuple) else target_rx_id,
            timeout_s=timeout_s,
            protocol_name="OBD",
            match_response=_match_pid,
        )
        return result  # type: ignore[return-value]

    # ISO 15765-4 functional (broadcast) request and the physical ECU
    # response range it fans out to.
    FUNCTIONAL_REQUEST_ID: ClassVar[int] = 0x7DF
    PHYSICAL_RESPONSE_RANGE: ClassVar[tuple[int, ...]] = tuple(range(0x7E8, 0x7F0))

    async def poll_pid_functional(
        self,
        pid: int,
        collect_window_s: float = DEFAULT_P2_TIMEOUT_S,
    ) -> list[ObdPidResult]:
        """REVIEW 1-M5: broadcast a Mode 01 PID functionally (0x7DF) and
        collect EVERY responding ECU's answer (0x7E8..0x7EF).

        SAE J1979/ISO 15765-4: a functional request reaches all compliant
        ECUs; each answers from its own physical response ID. The old
        single-rx_id listen silently ignored transmission (0x7E9), ABS
        (0x7EA) and other ECUs supporting the same PID.
        """
        payload = bytes([0x02, 0x01, pid & 0xFF, 0x55, 0x55, 0x55, 0x55, 0x55])
        req_frame = CanFrame.create(
            channel_id=self.channel_id,
            arbitration_id=self.FUNCTIONAL_REQUEST_ID,
            data=payload,
            is_extended=False,
            direction="tx",
        )

        isotp_by_rx = {
            rid: self._get_transport_for(self.FUNCTIONAL_REQUEST_ID, rid) for rid in self.PHYSICAL_RESPONSE_RANGE
        }
        results: list[ObdPidResult] = []
        seen: set[int] = set()

        await self.tx_port.send(req_frame)
        deadline = self.clock.now_monotonic() + collect_window_s
        while self.clock.now_monotonic() < deadline:
            remaining = deadline - self.clock.now_monotonic()
            if remaining <= 0:
                break
            frame = await self.rx_subscription.recv(timeout_s=remaining) if self.rx_subscription is not None else None
            if frame is None:
                break
            if frame.channel_id != self.channel_id:
                continue
            rid = frame.arbitration_id
            if rid not in isotp_by_rx or len(frame.data) < 3:
                continue
            completed, fc = isotp_by_rx[rid].handle_rx_frame(frame)
            if fc is not None:
                await self.tx_port.send(fc)
            if completed is not None and rid not in seen and completed[0] == 0x41 and completed[1] == pid:
                seen.add(rid)
                decoded = self.obd_registry.decode(pid, completed[2:])
                if isinstance(decoded, ObdPidResult):
                    # Attach ECU provenance (source_rx_id) to the copy.
                    decoded = replace(decoded, source_rx_id=rid)
                results.append(decoded)
        return results

    async def poll_did_once(
        self,
        did: int,
        tx_id: int | None = None,
        rx_id: int | None = None,
        timeout_s: float = DEFAULT_P2_TIMEOUT_S,
    ) -> UdsDidResult:
        """Perform a one-shot asynchronous query for an ISO 14229 UDS DID."""
        target_tx_id = tx_id if tx_id is not None else 0x7E0
        target_rx_id = rx_id if rx_id is not None else self.default_rx_id

        payload = bytes([0x03, 0x22, (did >> 8) & 0xFF, did & 0xFF, 0x55, 0x55, 0x55, 0x55])
        req_frame = CanFrame.create(
            channel_id=self.channel_id,
            arbitration_id=target_tx_id,
            data=payload,
            is_extended=target_tx_id > 0x7FF,
            direction="tx",
        )

        async def _match_did(completed: bytes) -> object | None:
            if completed[0] == 0x62 and len(completed) >= 3:
                resp_did = (completed[1] << 8) | completed[2]
                if resp_did == did:
                    return self.uds_registry.decode(did, completed[3:])
                return None
            if completed[0] == 0x7F and completed[1] == 0x22:
                nrc = completed[2]
                if nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING:
                    # P2* extension: keep the outer deadline; keep waiting
                    return None
                raise ProtocolError(
                    f"UDS DID 0x{did:04X} rejected with NRC 0x{nrc:02X}",
                    code="UDS_NEGATIVE_RESPONSE",
                    details={"did": did, "nrc": nrc},
                )
            return None

        result = await self._await_diagnostic_response(
            req_frame=req_frame,
            target_rx_id=target_rx_id,
            timeout_s=timeout_s,
            protocol_name="UDS",
            match_response=_match_did,
        )
        return result  # type: ignore[return-value]

    async def poll_dtc_once(
        self,
        mode: int,
        tx_id: int | None = None,
        rx_id: int | None = None,
        timeout_s: float = DEFAULT_P2_TIMEOUT_S,
    ) -> ObdDtcResult:
        """Perform a one-shot asynchronous DTC readback (Mode 03 / 07 / 0A)."""
        if mode not in OBD_DTC_MODES:
            raise ValueError(f"Invalid OBD DTC mode 0x{mode:02X}: must be 0x03, 0x07 or 0x0A")
        target_tx_id = tx_id if tx_id is not None else self.default_tx_id
        target_rx_id = rx_id if rx_id is not None else self.default_rx_id
        expected_sid = OBD_DTC_RESPONSE_SIDS[mode]

        payload = bytes([0x01, mode, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55])
        req_frame = CanFrame.create(
            channel_id=self.channel_id,
            arbitration_id=target_tx_id,
            data=payload,
            is_extended=target_tx_id > 0x7FF,
            direction="tx",
        )

        async def _match_dtc(completed: bytes) -> object | None:
            if completed[0] == expected_sid:
                dtc_count = completed[1]
                pair_bytes = bytes(completed[2:])
                declared_available = len(pair_bytes) // 2
                from src.protocols.obd.models import decode_dtc_pair

                dtcs = tuple(
                    decode_dtc_pair(pair_bytes[2 * i], pair_bytes[2 * i + 1])
                    for i in range(min(dtc_count, declared_available))
                )
                return ObdDtcResult(
                    mode=mode,
                    dtc_count=dtc_count,
                    dtcs=dtcs,
                    raw_bytes=bytes(completed),
                    is_valid=(dtc_count <= declared_available),
                    error_message=(
                        None
                        if dtc_count <= declared_available
                        else f"payload carries {declared_available} DTC pairs, ECU declared {dtc_count}"
                    ),
                )
            if completed[0] == 0x7F and completed[1] == mode:
                nrc = completed[2]
                if nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING:
                    return None  # P2* extension: keep the outer deadline
                raise ProtocolError(
                    f"OBD DTC mode 0x{mode:02X} rejected with NRC 0x{nrc:02X}",
                    code="OBD_NEGATIVE_RESPONSE",
                    details={"mode": mode, "nrc": nrc},
                )
            return None

        result = await self._await_diagnostic_response(
            req_frame=req_frame,
            target_rx_id=target_rx_id,
            timeout_s=timeout_s,
            protocol_name="OBD_DTC",
            match_response=_match_dtc,
        )
        return result  # type: ignore[return-value]
    async def _async_polling_loop(self) -> None:
        """Internal asynchronous worker loop running periodic polling steps."""
        while self._running:
            self.step()

            # If an Rx subscription is available, check for incoming frames
            if self.rx_subscription is not None:
                try:
                    frame = await self.rx_subscription.recv(timeout_s=0.005)
                    if frame is not None:
                        result, fc = self.process_rx_frame(frame)
                        if fc is not None:
                            await self.tx_port.send(fc)
                except Exception as rx_err:
                    logger.warning("Error receiving frame in poller loop", extra={"error": str(rx_err)})

            await asyncio.sleep(min(0.010, self.min_tx_interval_s))

    def start(self) -> None:
        """Start the active diagnostic poller background async task.

        REVIEW (restart): a single asyncio.Event shared across start/stop
        cycles binds to the FIRST loop that awaited it; a restart on the
        dedicated background thread created a fresh loop and the parked
        `stop_event.wait()` raised "bound to a different event loop",
        silently killing the daemon worker while is_running stayed True.
        Every loop-owned primitive is now per-run.
        """
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()
        stop_event = self._stop_event
        try:
            loop = asyncio.get_running_loop()
            self._loop_task = loop.create_task(self._async_polling_loop())
        except RuntimeError:
            # Own a dedicated background loop thread if no running event loop in caller thread
            def _runner() -> None:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    self._loop_task = new_loop.create_task(self._async_polling_loop())
                    new_loop.run_until_complete(stop_event.wait())
                finally:
                    new_loop.close()

            self._thread = threading.Thread(target=_runner, daemon=True, name="active_diag_poller")
            self._thread.start()

    def stop(self) -> None:
        """Stop the active diagnostic poller background task.

        M-31 (P2-22): thread-safe shutdown. asyncio.Event.set() and
        Task.cancel() are NOT thread-safe — calling them from another thread
        (the normal case: UI thread stops a poller running on the background
        loop) could leave the loop parked in `await self._stop_event.wait()`
        forever, leaking the loop AND its thread. All loop-owned objects are
        now touched via call_soon_threadsafe when a foreign thread stops us.
        """
        self._running = False

        loop: asyncio.AbstractEventLoop | None = None
        if self._loop_task is not None:
            loop = self._loop_task.get_loop()

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if loop is not None and loop.is_running():
            if running_loop is loop:
                # Same-loop caller: direct (already on the loop's thread).
                self._stop_event.set()
                if self._loop_task is not None:
                    self._loop_task.cancel()
            else:
                # Foreign thread: schedule the stop on the loop's own thread.
                loop.call_soon_threadsafe(self._stop_event.set)
                task = self._loop_task
                if task is not None:
                    loop.call_soon_threadsafe(task.cancel)
        else:
            # Loop already dead — no one to notify.
            self._stop_event.set()

        self._loop_task = None
        if hasattr(self, "_thread") and self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                logger.warning("Active diagnostic poller thread did not stop within 1.0s")
        self._thread = None

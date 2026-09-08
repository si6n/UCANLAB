"""Multi-Packet Transport Reassembly Pipeline Engine.

Connects SAE J1939-21 Transport Protocol (BAM & RTS/CTS CMDT) and ISO 15765-2 DoCAN (ISO-TP)
to FrameRouter and DbcSignalDecoder with deterministic, thread-safe session tracking,
monotonic timeout reclamation, quota handling, and synthetic frame synthesis.
"""

from __future__ import annotations

import collections
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from src.core.contracts.ports import ClockProvider, SystemClockProvider, TxPort
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame, length_to_dlc
from src.engine.decoder.dbc_decoder import DbcSignalDecoder, DecodedMessage
from src.engine.router import FrameRouter
from src.protocols.j1939.diagnostics import PGN_DM1, PGN_DM2, J1939DiagnosticService
from src.protocols.j1939.pgn import build_j1939_id, pgn_from_id
from src.protocols.j1939.transport import (
    PGN_TP_CM,
    PGN_TP_DT,
    J1939TransportProtocol,
)
from src.protocols.j1939.transport import (
    CompletedMessage as J1939CompletedMessage,
)
from src.protocols.uds.isotp import (
    FS_CTS,
    FS_OVERFLOW,
    PCI_CONSECUTIVE_FRAME,
    PCI_FIRST_FRAME,
    PCI_FLOW_CONTROL,
    PCI_SINGLE_FRAME,
    IsoTpTransport,
)

logger = get_logger("engine.pipeline.reassembly")

# Common Standard J1939 PGNs for Diagnostics & Identification
PGN_VIN: int = 65260  # 0xFEEC (Vehicle Identification Number - J1939-71 VI)
PGN_COMPONENT_ID: int = 65259  # 0xFEEB (Component Identification - J1939-71 CI)
PGN_SOFTWARE_ID: int = 65242  # 0xFEDA (Software Identification - J1939-71 SOFT)


@dataclass(slots=True)
class ReassembledMessage:
    """Canonical representation of a completed multi-packet or reassembled transport message."""

    protocol: str  # "J1939" | "ISO-TP"
    data: bytes
    timestamp_ns: int
    channel_id: str
    arbitration_id: int = 0
    source_address: int | None = None
    destination_address: int | None = None
    pgn: int | None = None
    is_bam: bool = False
    synthetic_frame: CanFrame | None = None
    decoded_message: DecodedMessage | None = None
    diagnostics: Any | None = None  # DMMessage for DM1/DM2, str for VIN, etc.


@dataclass(slots=True)
class IsoTpSession:
    """State tracking for an active ISO-TP multi-frame reception session."""

    rx_id: int
    tx_id: int
    channel_id: str
    total_bytes: int
    expected_sequence_number: int = 1
    received_bytes: bytearray = field(default_factory=bytearray)
    block_size: int = 0
    st_min: int = 0
    last_activity_time: float = field(default_factory=time.monotonic)
    is_fd: bool = False
    is_extended: bool = False
    source_address: int | None = None
    target_address: int | None = None
    # P2-3: explicit quota bucket key — set at session creation and reused
    # verbatim on release/reap. The old `source_address or key[0]` fallback
    # mis-bucketed SA=0 sessions (falsy 0 → raw arbitration ID) and
    # permanently leaked quota for engine ECUs at address 0x00.
    quota_key: str | None = None


def decode_vin_payload(data: bytes) -> str:
    """Decode ASCII Vehicle Identification Number (VIN) payload from J1939 PGN 65260."""
    clean = data.split(b"*")[0]
    return clean.decode("ascii", errors="replace").strip("\x00\xff ")


# 11-bit diagnostic IDs the protocol engines legitimately transmit (physical
# UDS responses 0x7E0..0x7EF and the OBD functional broadcast request 0x7DF).
PROTOCOL_RESPONSE_11BIT_IDS: frozenset[int] = frozenset(range(0x7E0, 0x7F0)) | {0x7DF}


def j1939_protocol_response_masks(my_address: int) -> tuple[tuple[int, int], ...]:
    """Build whitelist (value, mask) pairs authorizing our protocol responses.

    Covers J1939 TP.CM (0x18EC..), TP.DT (0x18EB..), 29-bit ISO-TP
    (0x18DA..) and Address Claiming (0x18EE..) frames sourced from
    `my_address` regardless of the peer they answer. Pass the result to
    TxSafetyGateway(whitelist_masks=...) when wiring this pipeline,
    otherwise every CTS/ACK/FC/Claim response trips the fail-closed
    whitelist stage.
    """
    sa = my_address & 0xFF
    return (
        (0x18EC0000 | sa, 0x18EC00FF),
        (0x18EB0000 | sa, 0x18EB00FF),
        (0x18DA0000 | sa, 0x18DA00FF),
        (0x18EE0000 | sa, 0x18EE00FF),
    )


class ReassemblyPipeline:
    """Deterministic, thread-safe Multi-Packet Transport Reassembly Pipeline.

    Subscribes to FrameRouter, intercepts J1939 TP (BAM / RTS-CTS) and ISO-TP streams,
    reassembles segmented payloads, synthesizes canonical CanFrames, and feeds decoded
    signals to DbcSignalDecoder and registered subscribers.
    """

    # Protocol Timers & Capacity Limits
    J1939_T1_TIMEOUT_SEC: ClassVar[float] = 0.750  # 750 ms (SAE J1939-21 T1)
    ISOTP_NCR_TIMEOUT_SEC: ClassVar[float] = 1.000  # 1000 ms (ISO 15765-2 N_Cr)
    MAX_CONCURRENT_SESSIONS: ClassVar[int] = 512
    MAX_SESSIONS_PER_SA: ClassVar[int] = 4
    MAX_PAYLOAD_SIZE: ClassVar[int] = 1_048_576  # 1 MB safety ceiling

    def __init__(
        self,
        router: FrameRouter | None = None,
        dbc_decoder: DbcSignalDecoder | None = None,
        j1939_transport: J1939TransportProtocol | None = None,
        isotp_transport: IsoTpTransport | None = None,
        clock_provider: ClockProvider | None = None,
        tx_port: TxPort | None = None,
        my_j1939_address: int = 0xF9,
        # P2-1: our J1939 ISO-TP target address (the DA byte in 0x18DA
        # frames). Only FFs addressed to this value are answered with FC.
        my_isotp_address: int = 0xF1,
        isotp_rx_ids: set[int] | list[int] | None = None,
        auto_subscribe_router: bool = True,
        channel_id: str | None = None,
        route_synthetic_frames: bool = True,
        decode_single_frames: bool = False,
        on_reassembled: Callable[[ReassembledMessage], None] | None = None,
        on_decoded: Callable[[DecodedMessage], None] | None = None,
        on_synthetic_frame: Callable[[CanFrame], None] | None = None,
        on_tx_frame: Callable[[CanFrame], None] | None = None,
    ) -> None:
        self.router = router
        self.dbc_decoder = dbc_decoder
        self.clock: ClockProvider = clock_provider or SystemClockProvider()
        self.tx_port = tx_port
        self.my_j1939_address = my_j1939_address
        self.my_isotp_address = my_isotp_address
        self.channel_id = channel_id
        self.route_synthetic_frames = route_synthetic_frames
        self.decode_single_frames = decode_single_frames

        # J1939 Transport Protocol Engine
        self.j1939_transport = (
            j1939_transport
            if j1939_transport is not None
            else J1939TransportProtocol(
                my_address=my_j1939_address,
                channel_id=channel_id or "j1939_ch0",
                clock=self.clock,
            )
        )

        # ISO-TP Static Transport Adapter (if provided)
        self.isotp_transport = isotp_transport

        # ISO-TP Recognized Arbitration IDs
        self._isotp_rx_ids: set[int] = set(isotp_rx_ids) if isotp_rx_ids is not None else set()
        if self.isotp_transport is not None:
            self._isotp_rx_ids.add(self.isotp_transport.rx_id)

        # Default 11-bit standard diagnostic IDs (UDS / OBD-II)
        for i in range(8):
            self._isotp_rx_ids.add(0x7E8 + i)  # Physical responses (0x7E8..0x7EF)
            self._isotp_rx_ids.add(0x7E0 + i)  # Physical requests (0x7E0..0x7E7)
        self._isotp_rx_ids.add(0x7DF)  # Broadcast request

        # Active ISO-TP Sessions: keyed by (rx_id, channel_id)
        self._isotp_sessions: dict[tuple[int, str], IsoTpSession] = {}
        self._isotp_per_sa_sessions: collections.Counter[str] = collections.Counter()

        # Thread Safety
        self._lock = threading.RLock()

        # Listener Callbacks
        self._on_reassembled_callbacks: list[Callable[[ReassembledMessage], None]] = []
        self._on_decoded_callbacks: list[Callable[[DecodedMessage], None]] = []
        self._on_synthetic_frame_callbacks: list[Callable[[CanFrame], None]] = []
        self._on_tx_frame_callbacks: list[Callable[[CanFrame], None]] = []

        if on_reassembled is not None:
            self._on_reassembled_callbacks.append(on_reassembled)
        if on_decoded is not None:
            self._on_decoded_callbacks.append(on_decoded)
        if on_synthetic_frame is not None:
            self._on_synthetic_frame_callbacks.append(on_synthetic_frame)
        if on_tx_frame is not None:
            self._on_tx_frame_callbacks.append(on_tx_frame)

        # Statistics
        self._total_frames_processed: int = 0
        self._j1939_messages_reassembled: int = 0
        self._isotp_messages_reassembled: int = 0
        self._synthetic_frames_generated: int = 0
        self._signals_decoded_count: int = 0
        self._dropped_or_timeout_count: int = 0

        # Auto-subscribe to FrameRouter if requested
        self._router_sub_id: int | None = None
        if self.router is not None and auto_subscribe_router:
            self._router_sub_id, _ = self.router.subscribe(
                callback=self.process_frame,
                channel_id=self.channel_id,
            )

    # --------------------------------------------------------------------------
    # Time & Lifecycle Management
    # --------------------------------------------------------------------------

    def _get_now(self) -> float:
        """Return current monotonic time in seconds."""
        return self.clock.now_monotonic()

    def reap_stale_sessions(self, now: float | None = None) -> int:
        """Reap inactive J1939 and ISO-TP sessions exceeding their respective timeout limits."""
        curr_time = now if now is not None else self._get_now()
        reaped_count = 0

        with self._lock:
            # 1. Reap J1939 Transport sessions
            # P2-8: go through the transport's own lock-holding public API —
            # reaching into `_reap_stale_sessions` under only the pipeline
            # lock races concurrent RX threads mutating the same dicts.
            if hasattr(self.j1939_transport, "reap_stale_sessions_public"):
                before_j1939 = len(getattr(self.j1939_transport, "_rx_sessions", {}))
                self.j1939_transport.reap_stale_sessions_public()
                after_j1939 = len(getattr(self.j1939_transport, "_rx_sessions", {}))
                reaped_count += max(0, before_j1939 - after_j1939)

            # 2. Reap ISO-TP Sessions
            expired_isotp_keys = [
                key
                for key, sess in self._isotp_sessions.items()
                if (curr_time - sess.last_activity_time) > self.ISOTP_NCR_TIMEOUT_SEC
            ]
            for key in expired_isotp_keys:
                sess = self._isotp_sessions.pop(key, None)
                if sess is not None:
                    reaped_count += 1
                    self._dropped_or_timeout_count += 1
                    self._decrement_sa_quota(sess, key)

        return reaped_count

    @staticmethod
    def _resolve_quota_key(sess: IsoTpSession, key: tuple[int, str]) -> str:
        """P2-3: canonical quota bucket for a session.

        Order of precedence: the explicit `quota_key` captured at session
        creation (the correct key even for SA=0), then a non-None
        source_address, then the session's rx arbitration ID. The old
        `source_address or key[0]` treated SA=0 as missing and debited a
        bogus bucket, permanently leaking quota for engine ECUs.
        """
        if sess.quota_key is not None:
            return sess.quota_key
        if sess.source_address is not None:
            return str(sess.source_address)
        return str(key[0])

    def _decrement_sa_quota(self, sess: IsoTpSession, key: tuple[int, str]) -> None:
        """Release one per-SA session slot (idempotent per session)."""
        sa_key = self._resolve_quota_key(sess, key)
        if self._isotp_per_sa_sessions[sa_key] <= 1:
            self._isotp_per_sa_sessions.pop(sa_key, None)
        else:
            self._isotp_per_sa_sessions[sa_key] -= 1

    # --------------------------------------------------------------------------
    # Frame Ingestion & Processing
    # --------------------------------------------------------------------------

    def process_frame(self, frame: CanFrame) -> ReassembledMessage | None:
        """Process incoming CAN frame.

        Intercepts J1939 TP and ISO-TP streams, reassembles complete payloads,
        synthesizes canonical CanFrames, decodes DBC signals, and dispatches to listeners.
        """
        # P2-11: frames synthesized by THIS pipeline and routed back through
        # the router must not be re-processed — that double-decodes signals
        # and corrupts time-series data.
        if getattr(frame, "source", None) == "synthetic":
            return None

        with self._lock:
            self._total_frames_processed += 1
            # Periodically reap stale sessions on high activity
            if self._total_frames_processed % 64 == 0:
                self.reap_stale_sessions()

        # 1. Try J1939 Transport Protocol Interception
        if frame.is_extended or frame.arbitration_id > 0x7FF:
            j1939_res = self._try_process_j1939(frame)
            if j1939_res is not None:
                return self._finalize_reassembled_message(j1939_res)

        # 2. Try ISO-TP Interception
        isotp_res = self._try_process_isotp(frame)
        if isotp_res is not None:
            return self._finalize_reassembled_message(isotp_res)

        # 3. Single Frame Direct DBC Decoding (if configured)
        if self.decode_single_frames and self.dbc_decoder is not None:
            decoded = self.dbc_decoder.decode_frame(frame)
            if decoded is not None:
                with self._lock:
                    self._signals_decoded_count += 1
                self._dispatch_decoded(decoded)

        return None

    def handle_frame(self, frame: CanFrame) -> ReassembledMessage | None:
        """Alias for process_frame."""
        return self.process_frame(frame)

    # --------------------------------------------------------------------------
    # J1939 Transport Reassembly Handling
    # --------------------------------------------------------------------------

    def _try_process_j1939(self, frame: CanFrame) -> ReassembledMessage | None:
        """Inspect and handle J1939 TP.CM (PGN 60416) or TP.DT (PGN 60160) frames."""
        # Extract 29-bit J1939 PGN — M-12 (P2-6): shared parser (EDP-aware).
        pgn = pgn_from_id(frame.arbitration_id)

        if pgn not in (PGN_TP_CM, PGN_TP_DT):
            return None

        # Feed to J1939 Transport Protocol engine
        completed_msg: J1939CompletedMessage | None
        resp_frame: CanFrame | None
        completed_msg, resp_frame = self.j1939_transport.handle_rx_frame(frame)

        # Dispatch response frame (e.g. CTS, EndOfMsgACK, or Conn_Abort)
        if resp_frame is not None:
            self._dispatch_tx_frame(resp_frame)

        # P2-4: drain the transport's overflow queue (collision CTS, DT
        # window batches) — without this, queued protocol responses rot in
        # the list and RTS spam grows it without bound.
        if hasattr(self.j1939_transport, "take_pending_tx_frames"):
            for extra_frame in self.j1939_transport.take_pending_tx_frames():
                self._dispatch_tx_frame(extra_frame)

        if completed_msg is None:
            return None

        with self._lock:
            self._j1939_messages_reassembled += 1

        is_bam = completed_msg.destination_address == 255
        target_pgn = completed_msg.pgn
        src_addr = completed_msg.source_address
        dst_addr = completed_msg.destination_address

        # Reconstruct canonical 29-bit CAN ID (B-06) — M-12 (P2-6): shared
        # builder preserves EDP/DP bits from the target PGN; the old
        # hand-rolled math dropped EDP entirely.
        arb_id = build_j1939_id(pgn=target_pgn, sa=src_addr, da=dst_addr, priority=6)

        # Diagnostics / Identification payload parsing
        diagnostics_parsed: Any = None
        if target_pgn in (PGN_DM1, PGN_DM2):
            try:
                diagnostics_parsed = J1939DiagnosticService.parse_dm1_or_dm2(
                    data=completed_msg.data,
                    pgn=target_pgn,
                    source_address=src_addr,
                    timestamp_ns=completed_msg.timestamp_ns,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed parsing DM diagnostic message", extra={"pgn": target_pgn, "error": str(exc)})
        elif target_pgn == PGN_VIN:
            try:
                diagnostics_parsed = decode_vin_payload(completed_msg.data)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed parsing VIN payload", extra={"error": str(exc)})

        return ReassembledMessage(
            protocol="J1939",
            data=completed_msg.data,
            timestamp_ns=completed_msg.timestamp_ns,
            channel_id=completed_msg.channel_id,
            arbitration_id=arb_id,
            source_address=src_addr,
            destination_address=dst_addr,
            pgn=target_pgn,
            is_bam=is_bam,
            diagnostics=diagnostics_parsed,
        )

    # --------------------------------------------------------------------------
    # ISO-TP Transport Reassembly Handling
    # --------------------------------------------------------------------------

    def _is_isotp_frame(self, frame: CanFrame) -> bool:
        """Determine if a frame belongs to ISO-TP (DoCAN)."""
        if frame.arbitration_id in self._isotp_rx_ids:
            return True
        if frame.is_extended:
            # 29-bit Normal Fixed Addressing: 0x18DA (Physical) or 0x18DB (Functional)
            prefix = (frame.arbitration_id >> 16) & 0xFFFF
            if prefix in (0x18DA, 0x18DB):
                return True
        return False

    def _try_process_isotp(self, frame: CanFrame) -> ReassembledMessage | None:
        """Inspect and handle ISO-TP Single Frame, First Frame, and Consecutive Frames."""
        if not self._is_isotp_frame(frame) or len(frame.data) < 2:
            return None

        # If static IsoTpTransport was provided and matches this frame ID
        if self.isotp_transport is not None and frame.arbitration_id == self.isotp_transport.rx_id:
            payload, resp_frame = self.isotp_transport.handle_rx_frame(frame)
            if resp_frame is not None:
                self._dispatch_tx_frame(resp_frame)
            if payload is not None:
                with self._lock:
                    self._isotp_messages_reassembled += 1
                return ReassembledMessage(
                    protocol="ISO-TP",
                    data=payload,
                    timestamp_ns=frame.timestamp_ns,
                    channel_id=frame.channel_id,
                    arbitration_id=frame.arbitration_id,
                )
            return None

        # Multi-session dynamic ISO-TP Engine
        return self._handle_dynamic_isotp(frame)

    def _handle_dynamic_isotp(self, frame: CanFrame) -> ReassembledMessage | None:
        """Stateful multi-session ISO 15765-2 reassembly across arbitrary arbitration IDs."""
        pci_type = (frame.data[0] >> 4) & 0x0F
        session_key = (frame.arbitration_id, frame.channel_id)
        now = self._get_now()

        # Derive matching TX ID for Flow Control.
        # P2-1: an OBSERVER must only ever answer frames ADDRESSED TO IT.
        # No tx_port = passive mode: no FC can be sent, so none is derived.
        # 11-bit: only diagnostic RESPONSE IDs (0x7E8..0x7EF) get an FC —
        # a request ID (0x7E0..0x7E7 / 0x7DF) is someone else's tester
        # traffic; answering it impersonates the ECU. 29-bit: the DA byte
        # must equal our configured J1939 ISO-TP address, otherwise this
        # FF belongs to a different tester pair and replying would emit an
        # unauthorized-ID TX that trips the gateway's E-Stop (self-DoS).
        if self.tx_port is None:
            tx_id = None
        elif frame.is_extended:
            target = (frame.arbitration_id >> 8) & 0xFF
            source = frame.arbitration_id & 0xFF
            if target != self.my_isotp_address:
                # Not addressed to us — passive reassembly only, no FC.
                tx_id = None
            else:
                tx_id = 0x18DA0000 | (source << 8) | target
        elif frame.arbitration_id in range(0x7E8, 0x7F0):
            # Response ID (ECU -> tester): we may flow-control as the tester.
            tx_id = frame.arbitration_id - 8
        else:
            # Request IDs and everything else: observe silently.
            tx_id = None

        # ----------------------------------------------------------------------
        # 1. Single Frame (SF)
        # ----------------------------------------------------------------------
        if pci_type == PCI_SINGLE_FRAME:
            sf_len_nibble = frame.data[0] & 0x0F
            if sf_len_nibble == 0:
                # CAN-FD Extended Single Frame
                if not frame.is_fd or len(frame.data) < 2:
                    return None
                sf_len = frame.data[1]
                if sf_len < 8 or sf_len > 62 or sf_len > (len(frame.data) - 2):
                    return None
                payload = bytes(frame.data[2 : 2 + sf_len])
            else:
                # Classic CAN Single Frame
                sf_len = sf_len_nibble
                if sf_len > 7 or sf_len > (len(frame.data) - 1):
                    return None
                payload = bytes(frame.data[1 : 1 + sf_len])

            with self._lock:
                # Evict any existing session on this key
                self._release_isotp_session(session_key)
                self._isotp_messages_reassembled += 1

            return ReassembledMessage(
                protocol="ISO-TP",
                data=payload,
                timestamp_ns=frame.timestamp_ns,
                channel_id=frame.channel_id,
                arbitration_id=frame.arbitration_id,
            )

        # ----------------------------------------------------------------------
        # 2. First Frame (FF)
        # ----------------------------------------------------------------------
        if pci_type == PCI_FIRST_FRAME:
            # P2-10: without a TX path we cannot answer this FF at all —
            # fall through to passive reassembly without creating a session
            # that would wait for CFs we have no way to grant.
            if len(frame.data) >= 6 and frame.data[0] == 0x10 and frame.data[1] == 0x00:
                # Extended 32-bit First Frame
                total_len = int.from_bytes(frame.data[2:6], byteorder="big")
                if total_len <= 4095:
                    return None
                header_len = 6
            else:
                # Standard 12-bit First Frame
                total_len = ((frame.data[0] & 0x0F) << 8) | frame.data[1]
                # P2-10: FF_DL < 8 is invalid per ISO 15765-2 — a malformed
                # 10 00 frame must not materialize as a fake "completed"
                # (even empty) message upstairs.
                if total_len < 8:
                    return None
                header_len = 2

            if total_len > self.MAX_PAYLOAD_SIZE:
                # Send Flow Control OVERFLOW
                if tx_id is not None:
                    fc_ovfl = self._build_fc_frame(frame, tx_id, FS_OVERFLOW)
                    self._dispatch_tx_frame(fc_ovfl)
                return None

            first_chunk = frame.data[header_len:]
            if len(first_chunk) > total_len:
                first_chunk = first_chunk[:total_len]

            # If First Frame already contains all bytes (e.g. 62B FD frame)
            if len(first_chunk) >= total_len:
                with self._lock:
                    self._release_isotp_session(session_key)
                    self._isotp_messages_reassembled += 1
                return ReassembledMessage(
                    protocol="ISO-TP",
                    data=bytes(first_chunk[:total_len]),
                    timestamp_ns=frame.timestamp_ns,
                    channel_id=frame.channel_id,
                    arbitration_id=frame.arbitration_id,
                )

            # P2-1: with no FC path (not addressed to us / passive mode) the
            # stream is reassembled PASSIVELY — sessions still track the CF
            # train (the ECU will retry with its own flow control), but no
            # FC is transmitted and per-SA quota still applies.
            with self._lock:
                # Session quota and collision management
                self._release_isotp_session(session_key)

                # P2-3: quota key derived ONCE here and stored explicitly on
                # the session — the release path reuses the same key instead
                # of recomputing from a falsy-able source_address (SA=0 used
                # to fall back to the raw arbitration ID and leak quota).
                sa_key = str(frame.arbitration_id & 0xFF if frame.is_extended else frame.arbitration_id)
                if self._isotp_per_sa_sessions[sa_key] >= self.MAX_SESSIONS_PER_SA:
                    logger.warning("ISO-TP per-SA session quota exceeded", extra={"sa": sa_key})
                    return None

                if len(self._isotp_sessions) >= self.MAX_CONCURRENT_SESSIONS:
                    oldest_k = min(self._isotp_sessions.keys(), key=lambda k: self._isotp_sessions[k].last_activity_time)
                    self._release_isotp_session(oldest_k)

                self._isotp_sessions[session_key] = IsoTpSession(
                    rx_id=frame.arbitration_id,
                    tx_id=tx_id,
                    channel_id=frame.channel_id,
                    total_bytes=total_len,
                    expected_sequence_number=1,
                    received_bytes=bytearray(first_chunk),
                    last_activity_time=now,
                    is_fd=frame.is_fd,
                    is_extended=frame.is_extended,
                    quota_key=sa_key,
                )
                self._isotp_per_sa_sessions[sa_key] += 1

            # Emit Flow Control CTS — only when an FC path exists (P2-1).
            if tx_id is not None:
                fc_cts = self._build_fc_frame(frame, tx_id, FS_CTS)
                self._dispatch_tx_frame(fc_cts)
            return None

        # ----------------------------------------------------------------------
        # 3. Consecutive Frame (CF)
        # ----------------------------------------------------------------------
        if pci_type == PCI_CONSECUTIVE_FRAME:
            with self._lock:
                session = self._isotp_sessions.get(session_key)
                if session is None:
                    return None

                # Check framing format consistency (CAN vs CAN-FD, standard vs extended ID)
                if frame.is_fd != session.is_fd or frame.is_extended != session.is_extended:
                    logger.warning(
                        "ISO-TP Consecutive Frame format mismatch with First Frame (dropping)",
                        extra={
                            "rx_id": hex(frame.arbitration_id),
                            "session_is_fd": session.is_fd,
                            "frame_is_fd": frame.is_fd,
                        },
                    )
                    self._release_isotp_session(session_key)
                    self._dropped_or_timeout_count += 1
                    return None

                # Check N_Cr timeout
                if (now - session.last_activity_time) > self.ISOTP_NCR_TIMEOUT_SEC:
                    logger.warning("ISO-TP Consecutive Frame timeout (N_Cr exceeded)", extra={"rx_id": hex(frame.arbitration_id)})
                    self._release_isotp_session(session_key)
                    self._dropped_or_timeout_count += 1
                    return None

                # Check Sequence Number
                seq_num = frame.data[0] & 0x0F
                if seq_num != session.expected_sequence_number:
                    logger.warning(
                        "ISO-TP sequence number mismatch",
                        extra={"expected": session.expected_sequence_number, "got": seq_num},
                    )
                    self._release_isotp_session(session_key)
                    self._dropped_or_timeout_count += 1
                    return None

                needed = session.total_bytes - len(session.received_bytes)
                available = len(frame.data) - 1
                chunk_len = min(needed, available)
                session.received_bytes.extend(frame.data[1 : 1 + chunk_len])
                session.expected_sequence_number = (session.expected_sequence_number + 1) & 0x0F
                session.last_activity_time = now

                if len(session.received_bytes) >= session.total_bytes:
                    completed_data = bytes(session.received_bytes[: session.total_bytes])
                    self._release_isotp_session(session_key)
                    self._isotp_messages_reassembled += 1
                    return ReassembledMessage(
                        protocol="ISO-TP",
                        data=completed_data,
                        timestamp_ns=frame.timestamp_ns,
                        channel_id=frame.channel_id,
                        arbitration_id=frame.arbitration_id,
                    )

            return None

        return None

    def _release_isotp_session(self, key: tuple[int, str]) -> None:
        """Remove an ISO-TP session and decrement its per-SA quota (P2-3)."""
        sess = self._isotp_sessions.pop(key, None)
        if sess is not None:
            self._decrement_sa_quota(sess, key)

    def _build_fc_frame(self, rx_frame: CanFrame, tx_id: int, flow_status: int) -> CanFrame:
        """Construct standard ISO-TP Flow Control frame."""
        fc_payload = bytearray(8)
        fc_payload[0] = (PCI_FLOW_CONTROL << 4) | (flow_status & 0x0F)
        fc_payload[1] = 0x00  # Block size = 0 (send all remaining)
        fc_payload[2] = 0x00  # STmin = 0 ms
        for i in range(3, 8):
            fc_payload[i] = 0xCC

        return CanFrame.create(
            channel_id=rx_frame.channel_id,
            arbitration_id=tx_id,
            data=bytes(fc_payload),
            is_extended=rx_frame.is_extended,
            is_fd=rx_frame.is_fd,
            direction="tx",
        )

    # --------------------------------------------------------------------------
    # Synthetic Frame Synthesis & DBC Signal Decoding
    # --------------------------------------------------------------------------

    def _finalize_reassembled_message(self, msg: ReassembledMessage) -> ReassembledMessage:
        """Synthesize canonical CanFrame, decode DBC signals, and invoke callbacks."""
        data_len = len(msg.data)

        # 1. Synthesize Canonical CanFrame
        # If payload length <= 64 bytes, create exact frame (Classic CAN or CAN-FD)
        # If payload length > 64 bytes, truncate to 64 bytes for CAN-FD compatibility
        if data_len <= 8:
            synth_data = msg.data
            synth_fd = False
            synth_dlc = length_to_dlc(data_len)
        elif data_len <= 64:
            synth_data = msg.data
            synth_fd = True
            synth_dlc = length_to_dlc(data_len)
        else:
            synth_data = msg.data[:64]
            synth_fd = True
            synth_dlc = 15

        synth_frame = CanFrame.create(
            channel_id=msg.channel_id,
            arbitration_id=msg.arbitration_id,
            data=synth_data,
            dlc=synth_dlc,
            is_extended=msg.arbitration_id > 0x7FF or (msg.source_address is not None),
            is_fd=synth_fd,
            direction="rx",
            timestamp_ns=msg.timestamp_ns,
            # P2-11: mark the frame as pipeline-synthesized so the router
            # round-trip cannot re-process it (double decode/dispatch).
            source="synthetic",
        )
        msg.synthetic_frame = synth_frame

        with self._lock:
            self._synthetic_frames_generated += 1

        # 2. Feed to DBC Signal Decoder
        if self.dbc_decoder is not None:
            # Decode using synthetic frame
            decoded = self.dbc_decoder.decode_frame(synth_frame)
            if decoded is not None:
                msg.decoded_message = decoded
                with self._lock:
                    self._signals_decoded_count += 1
                self._dispatch_decoded(decoded)

        # 3. Route synthetic frame on FrameRouter
        if self.router is not None and self.route_synthetic_frames:
            self.router.route_frame(synth_frame)

        # 4. Dispatch Callbacks
        self._dispatch_synthetic_frame(synth_frame)
        self._dispatch_reassembled(msg)

        return msg

    # --------------------------------------------------------------------------
    # Callbacks & Observer Dispatching
    # --------------------------------------------------------------------------

    def register_on_reassembled(self, callback: Callable[[ReassembledMessage], None]) -> Callable[[], None]:
        """Register listener for reassembled messages. Returns unregister callable."""
        with self._lock:
            self._on_reassembled_callbacks.append(callback)

        def unregister() -> None:
            with self._lock:
                if callback in self._on_reassembled_callbacks:
                    self._on_reassembled_callbacks.remove(callback)

        return unregister

    def register_on_decoded(self, callback: Callable[[DecodedMessage], None]) -> Callable[[], None]:
        """Register listener for DBC decoded messages. Returns unregister callable."""
        with self._lock:
            self._on_decoded_callbacks.append(callback)

        def unregister() -> None:
            with self._lock:
                if callback in self._on_decoded_callbacks:
                    self._on_decoded_callbacks.remove(callback)

        return unregister

    def register_on_synthetic_frame(self, callback: Callable[[CanFrame], None]) -> Callable[[], None]:
        """Register listener for synthetic CanFrames. Returns unregister callable."""
        with self._lock:
            self._on_synthetic_frame_callbacks.append(callback)

        def unregister() -> None:
            with self._lock:
                if callback in self._on_synthetic_frame_callbacks:
                    self._on_synthetic_frame_callbacks.remove(callback)

        return unregister

    def register_on_tx_frame(self, callback: Callable[[CanFrame], None]) -> Callable[[], None]:
        """Register listener for generated TX frames (CTS, ACK, Abort). Returns unregister callable."""
        with self._lock:
            self._on_tx_frame_callbacks.append(callback)

        def unregister() -> None:
            with self._lock:
                if callback in self._on_tx_frame_callbacks:
                    self._on_tx_frame_callbacks.remove(callback)

        return unregister

    def _dispatch_reassembled(self, msg: ReassembledMessage) -> None:
        with self._lock:
            callbacks = list(self._on_reassembled_callbacks)
        for cb in callbacks:
            try:
                cb(msg)
            except Exception as exc:  # noqa: BLE001
                logger.error("Error in on_reassembled callback", extra={"error": str(exc)})

    def _dispatch_decoded(self, decoded: DecodedMessage) -> None:
        with self._lock:
            callbacks = list(self._on_decoded_callbacks)
        for cb in callbacks:
            try:
                cb(decoded)
            except Exception as exc:  # noqa: BLE001
                logger.error("Error in on_decoded callback", extra={"error": str(exc)})

    def _dispatch_synthetic_frame(self, frame: CanFrame) -> None:
        with self._lock:
            callbacks = list(self._on_synthetic_frame_callbacks)
        for cb in callbacks:
            try:
                cb(frame)
            except Exception as exc:  # noqa: BLE001
                logger.error("Error in on_synthetic_frame callback", extra={"error": str(exc)})

    def _dispatch_tx_frame(self, frame: CanFrame) -> None:
        """Send a protocol-generated frame through the TX choke-point.

        P2-12: `on_tx_frame` observers fire ONLY after a successful
        gateway dispatch — a rejected frame (E-Stop, whitelist, budget)
        never enters the audit trail as "transmitted". `tx_port=None`
        means passive mode: no TX is possible, so no TX observer either.
        """
        if self.tx_port is None:
            # Passive observer mode — do not fabricate a TX trail.
            return

        try:
            self.tx_port.send_sync(frame)
        except Exception as exc:  # noqa: BLE001
            # E5: a rejected protocol response (whitelist miss, E-Stop,
            # rate budget) must be visible — debug-level swallowing hid
            # policy violations during normal protocol operation.
            logger.warning(
                "Protocol response frame rejected by TX policy",
                extra={
                    "arbitration_id": hex(frame.arbitration_id),
                    "channel_id": frame.channel_id,
                    "error": str(exc),
                },
            )
            return  # P2-12: rejected — not transmitted, observers not notified

        with self._lock:
            callbacks = list(self._on_tx_frame_callbacks)
        for cb in callbacks:
            try:
                cb(frame)
            except Exception as exc:  # noqa: BLE001
                logger.error("Error in on_tx_frame callback", extra={"error": str(exc)})

    # --------------------------------------------------------------------------
    # Dynamic Configuration & Inspection API
    # --------------------------------------------------------------------------

    def add_isotp_rx_id(self, rx_id: int) -> None:
        """Register an additional CAN arbitration ID to be handled as ISO-TP."""
        with self._lock:
            self._isotp_rx_ids.add(rx_id)

    def remove_isotp_rx_id(self, rx_id: int) -> None:
        """Unregister an ISO-TP CAN arbitration ID."""
        with self._lock:
            self._isotp_rx_ids.discard(rx_id)

    def set_dbc_decoder(self, decoder: DbcSignalDecoder | None) -> None:
        """Update or inject the DbcSignalDecoder instance."""
        with self._lock:
            self.dbc_decoder = decoder

    def set_tx_port(self, tx_port: TxPort | None) -> None:
        """Update or inject the TxPort instance."""
        with self._lock:
            self.tx_port = tx_port

    def get_active_session_count(self) -> int:
        """Return total active in-flight J1939 and ISO-TP sessions."""
        with self._lock:
            j1939_count = len(getattr(self.j1939_transport, "_rx_sessions", {}))
            isotp_count = len(self._isotp_sessions)
            return j1939_count + isotp_count

    def get_stats(self) -> dict[str, Any]:
        """Return complete execution statistics dictionary."""
        with self._lock:
            j1939_active = len(getattr(self.j1939_transport, "_rx_sessions", {}))
            isotp_active = len(self._isotp_sessions)
            return {
                "total_frames_processed": self._total_frames_processed,
                "j1939_messages_reassembled": self._j1939_messages_reassembled,
                "isotp_messages_reassembled": self._isotp_messages_reassembled,
                "synthetic_frames_generated": self._synthetic_frames_generated,
                "signals_decoded_count": self._signals_decoded_count,
                "dropped_or_timeout_count": self._dropped_or_timeout_count,
                "active_j1939_sessions": j1939_active,
                "active_isotp_sessions": isotp_active,
                "total_active_sessions": j1939_active + isotp_active,
            }

    def reset(self) -> None:
        """Reset all active sessions and metrics.

        P2-13: the J1939 transport state is cleared through its own
        lock-holding `reset_sessions()` — the previous direct `clear()` on
        `_rx_sessions`/`_per_sa_sessions` raced concurrent RX threads
        (RuntimeError during iteration / quota skew). The public API also
        clears TX sessions and the pending queue the old path missed.
        """
        with self._lock:
            if hasattr(self.j1939_transport, "reset_sessions"):
                self.j1939_transport.reset_sessions()
            self._isotp_sessions.clear()
            self._isotp_per_sa_sessions.clear()
            self._total_frames_processed = 0
            self._j1939_messages_reassembled = 0
            self._isotp_messages_reassembled = 0
            self._synthetic_frames_generated = 0
            self._signals_decoded_count = 0
            self._dropped_or_timeout_count = 0

    def close(self) -> None:
        """Cleanly shutdown pipeline and unsubscribe from FrameRouter."""
        with self._lock:
            if self.router is not None and self._router_sub_id is not None:
                self.router.unsubscribe(self._router_sub_id)
                self._router_sub_id = None
            self.reset()
            self._on_reassembled_callbacks.clear()
            self._on_decoded_callbacks.clear()
            self._on_synthetic_frame_callbacks.clear()
            self._on_tx_frame_callbacks.clear()

    def __enter__(self) -> ReassemblyPipeline:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


__all__ = [
    "IsoTpSession",
    "PGN_COMPONENT_ID",
    "PGN_SOFTWARE_ID",
    "PGN_VIN",
    "ReassembledMessage",
    "ReassemblyPipeline",
    "decode_vin_payload",
]

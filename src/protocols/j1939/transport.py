"""SAE J1939-21 Transport Protocol (BAM & RTS/CTS CMDT) Engine.

Complies with SAE J1939-21 and MASTER_PLAN.md Section 4.1.
Uses PGN 60416 (0xEC00) for TP.CM and PGN 60160 (0xEB00) for TP.DT.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from typing import ClassVar

from src.core.contracts.ports import ClockProvider
from src.core.exceptions import (
    J1939SequenceError,
    J1939SessionCollisionError,
    J1939TpAbortError,
    J1939TpError,
    J1939TpTimeoutError,
)
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame

logger = get_logger("protocols.j1939.transport")

PGN_TP_CM: int = 60416  # 0xEC00 (Connection Management)
PGN_TP_DT: int = 60160  # 0xEB00 (Data Transfer)

# TP.CM Control Bytes
TP_CTRL_RTS: int = 0x10
TP_CTRL_CTS: int = 0x11
TP_CTRL_ACK: int = 0x13
TP_CTRL_BAM: int = 0x20
TP_CTRL_ABORT: int = 0xFF


def ctrl_targets_sender(frame: CanFrame) -> bool:
    """True when a TP.CM frame's control byte addresses a sender role (CTS/ACK/Abort)."""
    return frame.data[0] in (TP_CTRL_CTS, TP_CTRL_ACK, TP_CTRL_ABORT)


def pgn_from_tp_cm(frame: CanFrame) -> int:
    """Extract the target PGN encoded in TP.CM bytes 5..7 (little-endian)."""
    if len(frame.data) < 8:
        return 0
    return int.from_bytes(bytes(frame.data[5:8]), byteorder="little")

# TP.Conn_Abort Reason Codes (SAE J1939-21 Section 5.10.3)
ABORT_REASON_SEQUENCE_ERROR: int = 0x01
ABORT_REASON_SESSION_COLLISION: int = 0x02
ABORT_REASON_TIMEOUT: int = 0x03
ABORT_REASON_UNEXPECTED_CONTROL: int = 0x04


@dataclass(slots=True)
class ReassemblySession:
    """Active multi-packet reassembly session."""

    source_address: int
    destination_address: int
    target_pgn: int
    total_bytes: int
    total_packets: int
    is_bam: bool
    expected_sequence: int = 1
    received_bytes: bytearray = field(default_factory=bytearray)
    last_activity_time: float = field(default_factory=time.monotonic)
    channel_id: str = "j1939_ch0"
    # Receiver-side CTS grant window for CMDT sessions (0 = grant all packets,
    # the J1939-21 default when buffer permits). Non-zero bounds the sender
    # to N packets per CTS exchange.
    rx_cts_window: int = 0
    max_packets_per_cts: int = 0xFF

    @property
    def expected_pgn(self) -> int:
        """Alias for target_pgn to conform with expected_pgn naming."""
        return self.target_pgn


@dataclass(slots=True)
class CompletedMessage:
    """Successfully reassembled multi-packet J1939 message."""

    source_address: int
    destination_address: int
    pgn: int
    data: bytes
    timestamp_ns: int
    channel_id: str


@dataclass(slots=True)
class CmdtSenderSession:
    """CMDT (RTS/CTS) sender-side state machine session.

    SAE J1939-21 transmitter flow: RTS -> (T2) -> CTS -> DT window ->
    (re-CTS for remaining packets) -> (T3) -> EndOfMsgACK.
    """

    source_address: int
    destination_address: int
    target_pgn: int
    total_bytes: int
    total_packets: int
    data: bytes
    channel_id: str = "j1939_ch0"
    state: str = "WAIT_CTS"  # WAIT_CTS -> GRANTED -> WAIT_ACK
    cts_window: int = 0
    next_sequence: int = 1
    last_activity_time: float = field(default_factory=time.monotonic)

    @property
    def key(self) -> tuple[int, int, int, str]:
        """Session identity: (SA=us, DA=peer, PGN, channel)."""
        return (self.source_address, self.destination_address, self.target_pgn, self.channel_id)


class J1939TransportProtocol:
    """SAE J1939-21 Transport Protocol Engine managing BAM & CMDT sessions."""

    # Timeouts in seconds (SAE J1939-21)
    T1_TIMEOUT_SEC: ClassVar[float] = 0.750  # 750 ms (Time between packets)
    T2_TIMEOUT_SEC: ClassVar[float] = 1.250  # 1250 ms (Time to CTS)
    T3_TIMEOUT_SEC: ClassVar[float] = 1.250  # 1250 ms (Time to EndOfMsgACK)
    T4_TIMEOUT_SEC: ClassVar[float] = 1.050  # 1050 ms (Time to hold connection)
    MAX_CONCURRENT_SESSIONS: ClassVar[int] = 512
    MAX_SESSIONS_PER_SA: ClassVar[int] = 4  # F-19: per source-address session quota
    # Receiver-side CTS grant window for CMDT sessions (0 = grant all packets
    # in one CTS, the simple-buffer default). Non-zero bounds each CTS to N
    # packets, requiring re-CTS exchanges for longer transfers.
    RX_CTS_WINDOW: ClassVar[int] = 0

    def __init__(
        self,
        my_address: int = 0xF9,
        channel_id: str = "j1939_ch0",
        clock: ClockProvider | None = None,
        rx_cts_window: int = 0,
    ) -> None:
        self.my_address = my_address
        self.channel_id = channel_id
        self.clock = clock
        self.rx_cts_window = rx_cts_window
        # Session storage strictly keyed by (source_address, destination_address, channel_id)
        self._rx_sessions: dict[tuple[int, int, str], ReassemblySession] = {}
        # CMDT sender sessions keyed by (my_address, peer_address, pgn, channel_id)
        self._tx_sessions: dict[tuple[int, int, int, str], CmdtSenderSession] = {}
        # Overflow outgoing frames when a single handle_rx_frame call yields
        # more than one response (CTS window batches); drained by callers via
        # take_pending_tx_frames().
        self._pending_tx_frames: list[CanFrame] = []
        self._sessions_lock = threading.RLock()
        self._per_sa_sessions: collections.Counter[str] = collections.Counter()

    def _get_now(self) -> float:
        """Return current monotonic time in seconds."""
        if self.clock is not None:
            return self.clock.now_monotonic()
        return time.monotonic()

    def _reap_stale_sessions(self, now: float | None = None) -> int:
        """Reap inactive reassembly sessions and return the number of reaped sessions (MED-3).

        P-c: per SAE J1939-21 the receiver holds a CMDT (point-to-point)
        session open for T4 (1050 ms) after CTS while awaiting the next DT,
        and a BAM broadcast for T1 (750 ms). The previous blanket T1 reaped
        CMDT transfers up to 300 ms too early.
        """
        curr_time = now if now is not None else self._get_now()
        expired = [
            key
            for key, sess in self._rx_sessions.items()
            if (curr_time - sess.last_activity_time) > (
                self.T4_TIMEOUT_SEC if not sess.is_bam else self.T1_TIMEOUT_SEC
            )
        ]
        for key in expired:
            self._release_session_slot(key, self._rx_sessions.get(key))
        return len(expired)

    def handle_rx_frame(self, frame: CanFrame) -> tuple[CompletedMessage | None, CanFrame | None]:
        """Process incoming frame according to SAE J1939-21 PDU format rules."""
        if not frame.is_extended or len(frame.data) < 8:
            return None, None

        # 29-bit CAN ID decomposition
        dp = (frame.arbitration_id >> 24) & 0x01
        pf = (frame.arbitration_id >> 16) & 0xFF
        ps = (frame.arbitration_id >> 8) & 0xFF
        sa = frame.arbitration_id & 0xFF

        if pf < 240:
            # PDU1 format (Point-to-Point): Destination Address is PS
            da = ps
            pgn = (dp << 16) | (pf << 8)
        else:
            # PDU2 format (Broadcast): DA is Global (255)
            da = 255
            pgn = (dp << 16) | (pf << 8) | ps

        # Check for TP.CM (PGN 60416 / 0xEC00)
        if pgn == PGN_TP_CM:
            # P2-4: route by SESSION EXISTENCE, not control byte alone. A
            # peer Abort (DA=us) that has no matching TX session is aimed at
            # our RECEIVER session — the old control-byte dispatch dropped
            # it, leaking the RX session until reaping.
            tx_key = (self.my_address, sa, pgn_from_tp_cm(frame), frame.channel_id) if ctrl_targets_sender(frame) else None
            if da == self.my_address and ctrl_targets_sender(frame):
                # Abort with no TX session → fall through to the RX handler
                # so the receiving side can drop its session.
                has_tx_session = False
                if tx_key is not None:
                    with self._sessions_lock:
                        probe_key = (self.my_address, sa, pgn_from_tp_cm(frame), frame.channel_id)
                        has_tx_session = probe_key in self._tx_sessions
                if has_tx_session or frame.data[0] != TP_CTRL_ABORT:
                    dt_frames, err_frame = self._handle_tx_cm(frame, sa, da)
                    if dt_frames:
                        # First frame rides the single response slot; the rest
                        # queue. M-6 (3FABLE): the queue mutation is under
                        # _sessions_lock — the RX thread and the
                        # take_pending_tx_frames drain must not interleave.
                        with self._sessions_lock:
                            self._pending_tx_frames.extend(dt_frames[1:])
                            if err_frame is not None:
                                self._pending_tx_frames.append(err_frame)
                        return None, dt_frames[0]
                    if err_frame is not None:
                        return None, err_frame
                    return None, None
            return self._handle_tp_cm(frame, sa, da)

        # Check for TP.DT (PGN 60160 / 0xEB00)
        if pgn == PGN_TP_DT:
            return self._handle_tp_dt(frame, sa, da)

        return None, None

    def handle_frame(self, frame: CanFrame) -> tuple[CompletedMessage | None, CanFrame | None]:
        """Alias for handle_rx_frame."""
        return self.handle_rx_frame(frame)

    def take_pending_tx_frames(self) -> list[CanFrame]:
        """Drain extra outgoing frames queued by the last handle_rx_frame call."""
        with self._sessions_lock:
            pending = self._pending_tx_frames
            self._pending_tx_frames = []
            return pending

    def reset_sessions(self) -> None:
        """Reset ALL transport state under the session lock (P2-13).

        Public, thread-safe replacement for callers reaching into
        `_rx_sessions`/`_per_sa_sessions` without `_sessions_lock` (a
        concurrent RX thread iterating during a clear() raises
        'dictionary changed size during iteration' or permanently skews
        the quota counters). Also clears sender-side sessions and the
        pending TX queue, which the pipeline-level reset used to miss.
        """
        with self._sessions_lock:
            self._rx_sessions.clear()
            self._tx_sessions.clear()
            self._per_sa_sessions.clear()
            self._pending_tx_frames.clear()

    def reap_stale_sessions_public(self) -> int:
        """Lock-holding public reaper (P2-8, H-5) — safe from any thread."""
        with self._sessions_lock:
            return self._reap_stale_sessions(now=self._get_now())

    def reap_stale_sessions(self, now: float | None = None) -> int:
        """Lock-holding public alias for reaping stale sessions."""
        with self._sessions_lock:
            return self._reap_stale_sessions(now=now)

    def _handle_tp_cm(
        self, frame: CanFrame, sa: int, da: int
    ) -> tuple[CompletedMessage | None, CanFrame | None]:
        ctrl_byte = frame.data[0]
        total_bytes = int.from_bytes(frame.data[1:3], byteorder="little")
        total_packets = frame.data[3]
        target_pgn = int.from_bytes(frame.data[5:8], byteorder="little")

        # Session key strictly by (source_address, destination_address, channel_id)
        session_key = (sa, da, frame.channel_id)

        # Validate SAE J1939-21 TP limits: max 1785 bytes, packets must match declared bytes
        if ctrl_byte in {TP_CTRL_BAM, TP_CTRL_RTS}:
            if not (1 <= total_bytes <= 1785) or total_packets == 0:
                logger.warning(
                    "Rejected malformed J1939 TP.CM length",
                    extra={"total_bytes": total_bytes, "total_packets": total_packets, "sa": sa},
                )
                return None, None
            expected_packets = (total_bytes + 6) // 7
            if total_packets != expected_packets:
                logger.warning(
                    "Rejected J1939 TP.CM packet count mismatch",
                    extra={"declared": total_packets, "expected": expected_packets, "sa": sa},
                )
                return None, None

        with self._sessions_lock:
            self._reap_stale_sessions()

            if ctrl_byte == TP_CTRL_BAM:
                # Broadcast Announce Message must be addressed to global broadcast address DA == 255 (0xFF)
                if da != 255:
                    logger.warning(
                        "Rejected J1939 TP.CM_BAM with non-broadcast destination address",
                        extra={"da": da, "sa": sa},
                    )
                    return None, None
                # B-03: If an in-flight BAM is replaced by another BAM from same SA for a DIFFERENT PGN,
                # record partial drop and log warning cleanly.
                old_bam = self._rx_sessions.get(session_key)
                if old_bam is not None:
                    if old_bam.target_pgn != target_pgn:
                        logger.warning(
                            "New BAM with different PGN replaces in-flight session (B-03)",
                            extra={"sa": sa, "old_pgn": hex(old_bam.target_pgn), "new_pgn": hex(target_pgn)},
                        )
                    else:
                        logger.warning(
                            "New BAM replaces in-flight session",
                            extra={"sa": sa, "old_pgn": hex(old_bam.target_pgn), "new_pgn": hex(target_pgn)},
                        )
                    self._release_session_slot(session_key, old_bam)

                # F-19: per source-address quota
                if (
                    session_key not in self._rx_sessions
                    and self._per_sa_sessions[str(sa)] >= self.MAX_SESSIONS_PER_SA
                ):
                    logger.warning(
                        "Rejected BAM: per-source session quota exceeded",
                        extra={"sa": sa, "quota": self.MAX_SESSIONS_PER_SA},
                    )
                    return None, None

                if len(self._rx_sessions) >= self.MAX_CONCURRENT_SESSIONS and session_key not in self._rx_sessions:
                    oldest_key = min(self._rx_sessions.keys(), key=lambda k: self._rx_sessions[k].last_activity_time)
                    self._release_session_slot(oldest_key, self._rx_sessions[oldest_key])

                logger.debug(
                    "Received J1939 TP.CM_BAM",
                    extra={"sa": sa, "target_pgn": hex(target_pgn), "bytes": total_bytes, "packets": total_packets},
                )
                self._rx_sessions[session_key] = ReassemblySession(
                    source_address=sa,
                    destination_address=da,
                    target_pgn=target_pgn,
                    total_bytes=total_bytes,
                    total_packets=total_packets,
                    is_bam=True,
                    expected_sequence=1,
                    last_activity_time=self._get_now(),
                    channel_id=frame.channel_id,
                )
                self._per_sa_sessions[str(sa)] += 1
                return None, None

        if ctrl_byte == TP_CTRL_RTS:
            # Request To Send (Point-to-Point CMDT)
            # 1. Reject RTS addressed to global broadcast address DA == 255 (0xFF)
            if da == 255 or da != self.my_address:
                logger.warning(
                    "Rejected J1939 TP.CM_RTS with invalid destination address",
                    extra={"da": da, "my_address": self.my_address, "sa": sa},
                )
                return None, None

            # P7: the entire RTS session-mutation path runs under the sessions
            # lock — collision check, quota, capacity and slot creation.
            with self._sessions_lock:
                # 2. Check for active session collision on (SA, DA, channel)
                existing_session = self._rx_sessions.get(session_key)
                abort_frame: CanFrame | None = None
                if existing_session is not None:
                    logger.warning(
                        "J1939 TP session collision detected on (SA, DA, channel)",
                        extra={
                            "sa": sa,
                            "da": da,
                            "old_pgn": hex(existing_session.target_pgn),
                            "new_pgn": hex(target_pgn),
                        },
                    )
                    # Abort existing session with reason=2 (Session Collision)
                    abort_frame = self._create_abort_frame(
                        existing_session, reason=ABORT_REASON_SESSION_COLLISION
                    )
                    self._release_session_slot(session_key, existing_session)

                # F-19: per source-address quota (RTS path)
                if (
                    session_key not in self._rx_sessions
                    and self._per_sa_sessions[str(sa)] >= self.MAX_SESSIONS_PER_SA
                ):
                    logger.warning(
                        "Rejected RTS: per-source session quota exceeded",
                        extra={"sa": sa, "quota": self.MAX_SESSIONS_PER_SA},
                    )
                    return None, abort_frame

                # Capacity management
                if len(self._rx_sessions) >= self.MAX_CONCURRENT_SESSIONS and session_key not in self._rx_sessions:
                    oldest_key = min(self._rx_sessions.keys(), key=lambda k: self._rx_sessions[k].last_activity_time)
                    self._release_session_slot(oldest_key, self._rx_sessions[oldest_key])

                rx_win = self.rx_cts_window or getattr(self, "RX_CTS_WINDOW", 0)
                max_packets_cts = frame.data[4] if len(frame.data) >= 5 and frame.data[4] > 0 else 0xFF
                # Establish new session with new expected_pgn
                new_session = ReassemblySession(
                    source_address=sa,
                    destination_address=da,
                    target_pgn=target_pgn,
                    total_bytes=total_bytes,
                    total_packets=total_packets,
                    is_bam=False,
                    expected_sequence=1,
                    last_activity_time=self._get_now(),
                    channel_id=frame.channel_id,
                    rx_cts_window=rx_win,
                    max_packets_per_cts=max_packets_cts,
                )
                self._rx_sessions[session_key] = new_session
                self._per_sa_sessions[str(sa)] += 1

                # If collision occurred, emit abort frame for the old session and
                # queue the new session's CTS so the peer is granted the transfer
                # (single response slot cannot carry both frames).
                if abort_frame is not None:
                    self._pending_tx_frames.append(self._create_cts_frame(new_session))
                    return None, abort_frame

                # Otherwise emit affirmative TP.CM_CTS for the new session
                cts_frame = self._create_cts_frame(new_session)
                return None, cts_frame

        if ctrl_byte == TP_CTRL_ABORT:
            # Peer Connection Abort (P7: slot release under the sessions lock)
            with self._sessions_lock:
                logger.warning(
                    "Received J1939 TP.Conn_Abort",
                    extra={"sa": sa, "da": da, "target_pgn": hex(target_pgn), "reason": frame.data[1]},
                )
                self._release_session_slot(session_key, self._rx_sessions.get(session_key))
            return None, None

        return None, None

    def _release_session_slot(
        self, key: tuple[int, int, str], session: ReassemblySession | None
    ) -> None:
        """Remove a session and decrement its per-SA quota slot."""
        if session is None:
            self._rx_sessions.pop(key, None)
            return
        if self._rx_sessions.pop(key, None) is not None:
            sa_key = str(session.source_address)
            if self._per_sa_sessions[sa_key] <= 1:
                self._per_sa_sessions.pop(sa_key, None)
            else:
                self._per_sa_sessions[sa_key] -= 1

    def _handle_tp_dt(
        self, frame: CanFrame, sa: int, da: int
    ) -> tuple[CompletedMessage | None, CanFrame | None]:
        seq_num = frame.data[0]
        payload = frame.data[1:8]

        # Session lookup strictly keyed by (source_address, destination_address, channel_id)
        # P-C-002 fix: the entire session mutation — sequence check, payload
        # append, completion, and slot release — runs under one lock hold so
        # concurrent TP.DT frames cannot interleave with torn state.
        session_key = (sa, da, frame.channel_id)
        with self._sessions_lock:
            session = self._rx_sessions.get(session_key)

            if session is None:
                return None, None

            now = self._get_now()

            # Session hold: T4 (1050 ms) for CMDT peer-to-peer sessions, T1
            # (750 ms) for BAM broadcasts (P-c — aligned with _reap_stale_sessions)
            hold_timeout = self.T4_TIMEOUT_SEC if not session.is_bam else self.T1_TIMEOUT_SEC
            if (now - session.last_activity_time) > hold_timeout:
                logger.warning(
                    "J1939 TP.DT session timeout (hold window exceeded)",
                    extra={
                        "sa": sa,
                        "da": da,
                        "target_pgn": hex(session.target_pgn),
                        "timeout_s": hold_timeout,
                        "is_bam": session.is_bam,
                    },
                )
                self._release_session_slot(session_key, session)
                abort_frame = self._create_abort_frame(session, reason=ABORT_REASON_TIMEOUT)
                return None, abort_frame

            # Check sequence order
            if seq_num != session.expected_sequence:
                logger.warning(
                    "J1939 TP.DT out of order sequence",
                    extra={
                        "expected": session.expected_sequence,
                        "got": seq_num,
                        "sa": sa,
                        "da": da,
                        "target_pgn": hex(session.target_pgn),
                    },
                )
                self._release_session_slot(session_key, session)
                abort_frame = self._create_abort_frame(session, reason=ABORT_REASON_SEQUENCE_ERROR)
                return None, abort_frame

            # Append payload data
            bytes_needed = session.total_bytes - len(session.received_bytes)
            chunk = payload[:bytes_needed]
            session.received_bytes.extend(chunk)
            session.expected_sequence += 1
            session.last_activity_time = now

            # Check if transfer is complete
            if session.expected_sequence > session.total_packets or len(session.received_bytes) >= session.total_bytes:
                completed_data = bytes(session.received_bytes[: session.total_bytes])
                completed_msg = CompletedMessage(
                    source_address=session.source_address,
                    destination_address=session.destination_address,
                    pgn=session.target_pgn,
                    data=completed_data,
                    timestamp_ns=frame.timestamp_ns,
                    channel_id=frame.channel_id,
                )

                resp_frame = None
                if not session.is_bam and session.destination_address == self.my_address:
                    # Send TP.CM_EndOfMsgACK
                    ack_data = bytearray(8)
                    ack_data[0] = TP_CTRL_ACK
                    ack_data[1:3] = session.total_bytes.to_bytes(2, byteorder="little")
                    ack_data[3] = session.total_packets
                    ack_data[4] = 0xFF
                    ack_data[5:8] = session.target_pgn.to_bytes(3, byteorder="little")

                    can_id = 0x18EC0000 | (session.source_address << 8) | (self.my_address & 0xFF)
                    resp_frame = CanFrame.create(
                        channel_id=frame.channel_id,
                        arbitration_id=can_id,
                        data=bytes(ack_data),
                        is_extended=True,
                        direction="tx",
                    )

                self._release_session_slot(session_key, session)
                return completed_msg, resp_frame

            # Partial transfer on a CMDT session: issue the next CTS window so
            # the sender can continue. The receiver's grant policy is bounded by
            # its remaining buffer, expressed via rx_cts_window (0 = grant all).
            if not session.is_bam and session.destination_address == self.my_address:
                rx_window = getattr(session, "rx_cts_window", 0) or getattr(self, "RX_CTS_WINDOW", 0)
                if rx_window > 0 and (session.expected_sequence - 1) % rx_window == 0:
                    remaining_packets = session.total_packets - (session.expected_sequence - 1)
                    grant = min(rx_window, remaining_packets)
                    cts_data = bytearray(8)
                    cts_data[0] = TP_CTRL_CTS
                    cts_data[1] = grant
                    cts_data[2] = session.expected_sequence
                    cts_data[3] = 0xFF
                    cts_data[4] = 0xFF
                    cts_data[5:8] = session.target_pgn.to_bytes(3, byteorder="little")
                    can_id = 0x18EC0000 | (session.source_address << 8) | (self.my_address & 0xFF)
                    return None, CanFrame.create(
                        channel_id=frame.channel_id,
                        arbitration_id=can_id,
                        data=bytes(cts_data),
                        is_extended=True,
                        direction="tx",
                    )

            return None, None

    def _create_cts_frame(self, session: ReassemblySession) -> CanFrame:
        """Construct standard J1939 TP.CM_CTS frame (PGN 60416 / 0xEC00 with Control Byte 0x11)."""
        grant = session.total_packets
        if session.max_packets_per_cts > 0 and session.max_packets_per_cts < session.total_packets:
            grant = min(grant, session.max_packets_per_cts)

        cts_data = bytearray(8)
        cts_data[0] = TP_CTRL_CTS
        cts_data[1] = max(1, grant)  # Number of packets allowed
        cts_data[2] = session.expected_sequence  # Next sequence number expected (1)
        cts_data[3] = 0xFF
        cts_data[4] = 0xFF
        cts_data[5:8] = session.target_pgn.to_bytes(3, byteorder="little")

        can_id = 0x18EC0000 | (session.source_address << 8) | (self.my_address & 0xFF)
        return CanFrame.create(
            channel_id=session.channel_id,
            arbitration_id=can_id,
            data=bytes(cts_data),
            is_extended=True,
            direction="tx",
        )

    def _create_abort_frame(self, session: ReassemblySession, reason: int = 0x01) -> CanFrame | None:
        """Construct standard J1939 TP.Conn_Abort frame (PGN 60416 / 0xEC00 with Control Byte 0xFF)."""
        if session.is_bam:
            return None  # Do not send abort for global broadcast

        abort_data = bytearray(8)
        abort_data[0] = TP_CTRL_ABORT
        abort_data[1] = reason
        abort_data[2:5] = b"\xff\xff\xff"
        abort_data[5:8] = session.target_pgn.to_bytes(3, byteorder="little")

        can_id = 0x18EC0000 | (session.source_address << 8) | (self.my_address & 0xFF)
        return CanFrame.create(
            channel_id=session.channel_id,
            arbitration_id=can_id,
            data=bytes(abort_data),
            is_extended=True,
            direction="tx",
        )

    def start_tp_bam(self, pgn: int, data: bytes, channel_id: str | None = None) -> list[CanFrame]:
        """Segment data into J1939 BAM broadcast frames (TP.CM_BAM followed by TP.DT packets)."""
        if not (1 <= len(data) <= 1785):
            raise ValueError(f"J1939 TP data length must be 1..1785 bytes, got {len(data)}")

        ch = channel_id or self.channel_id
        total_bytes = len(data)
        total_packets = (total_bytes + 6) // 7

        # 1. TP.CM_BAM frame (DA = 255, SA = my_address)
        bam_data = bytearray(8)
        bam_data[0] = TP_CTRL_BAM
        bam_data[1:3] = total_bytes.to_bytes(2, byteorder="little")
        bam_data[3] = total_packets
        bam_data[4] = 0xFF
        bam_data[5:8] = pgn.to_bytes(3, byteorder="little")

        can_id_cm = 0x18ECFF00 | (self.my_address & 0xFF)
        frames: list[CanFrame] = [
            CanFrame.create(
                channel_id=ch,
                arbitration_id=can_id_cm,
                data=bytes(bam_data),
                is_extended=True,
                direction="tx",
            )
        ]

        # 2. TP.DT packets
        can_id_dt = 0x18EBFF00 | (self.my_address & 0xFF)
        for seq in range(1, total_packets + 1):
            chunk = data[(seq - 1) * 7 : seq * 7]
            dt_data = bytearray(8)
            dt_data[0] = seq
            dt_data[1 : 1 + len(chunk)] = chunk
            for i in range(1 + len(chunk), 8):
                dt_data[i] = 0xFF
            frames.append(
                CanFrame.create(
                    channel_id=ch,
                    arbitration_id=can_id_dt,
                    data=bytes(dt_data),
                    is_extended=True,
                    direction="tx",
                )
            )

        return frames

    def start_tp_cm_dt(
        self, target_address: int, pgn: int, data: bytes, channel_id: str | None = None
    ) -> list[CanFrame]:
        """DEPRECATED (REVIEW.md 3.5): returns ONLY the TP.CM_RTS frame.

        SAE J1939-21 §5.10.1 forbids sending TP.DT packets before the
        receiver's TP.CM_CTS grant. The old behaviour emitted RTS + ALL DTs
        as one list; callers broadcasting that list bombarded the receiver
        before it allocated its buffer (instant Conn_Abort). Use
        `start_cmdt_transfer` + `handle_rx_frame(CTS)` for the proper
        windowed flow; this helper now returns just the RTS so existing
        callers cannot violate the protocol by accident.
        """
        if not (1 <= len(data) <= 1785):
            raise ValueError(f"J1939 TP data length must be 1..1785 bytes, got {len(data)}")

        ch = channel_id or self.channel_id
        total_bytes = len(data)
        total_packets = (total_bytes + 6) // 7

        rts_data = bytearray(8)
        rts_data[0] = TP_CTRL_RTS
        rts_data[1:3] = total_bytes.to_bytes(2, byteorder="little")
        rts_data[3] = total_packets
        rts_data[4] = 0xFF
        rts_data[5:8] = pgn.to_bytes(3, byteorder="little")

        can_id_cm = 0x18EC0000 | ((target_address & 0xFF) << 8) | (self.my_address & 0xFF)
        return [
            CanFrame.create(
                channel_id=ch,
                arbitration_id=can_id_cm,
                data=bytes(rts_data),
                is_extended=True,
                direction="tx",
            )
        ]

    def start_cmdt_transfer(
        self,
        target_address: int,
        pgn: int,
        data: bytes,
        channel_id: str | None = None,
    ) -> CanFrame:
        """Open a CMDT transfer: emit TP.CM_RTS and register a sender session.

        SAE J1939-21 requires the transmitter to wait for TP.CM_CTS before
        sending any TP.DT packet (T2), honour the CTS packet window, and wait
        for TP.CM_EndOfMsgACK after the last packet (T3). DT frames are
        produced incrementally via `advance_cmdt_transfer` as CTS frames
        arrive; the RTS frame itself is returned immediately.
        """
        if not (1 <= len(data) <= 1785):
            raise ValueError(f"J1939 TP data length must be 1..1785 bytes, got {len(data)}")

        ch = channel_id or self.channel_id
        total_bytes = len(data)
        total_packets = (total_bytes + 6) // 7

        session = CmdtSenderSession(
            source_address=self.my_address,
            destination_address=target_address,
            target_pgn=pgn,
            total_bytes=total_bytes,
            total_packets=total_packets,
            data=data,
            channel_id=ch,
            last_activity_time=self._get_now(),
        )
        with self._sessions_lock:
            self._tx_sessions[session.key] = session

        rts_data = bytearray(8)
        rts_data[0] = TP_CTRL_RTS
        rts_data[1:3] = total_bytes.to_bytes(2, byteorder="little")
        rts_data[3] = total_packets
        rts_data[4] = 0xFF
        rts_data[5:8] = pgn.to_bytes(3, byteorder="little")

        can_id_cm = 0x18EC0000 | ((target_address & 0xFF) << 8) | (self.my_address & 0xFF)
        return CanFrame.create(
            channel_id=ch,
            arbitration_id=can_id_cm,
            data=bytes(rts_data),
            is_extended=True,
            direction="tx",
        )

    def advance_cmdt_transfer(
        self, target_address: int, pgn: int, channel_id: str | None = None
    ) -> tuple[list[CanFrame], CanFrame | None]:
        """Produce the next TP.DT batch for a pending CMDT sender session.

        Called when TP.CM_CTS arrives. Returns (dt_frames, timeout_abort):
        - dt_frames: the packets granted by the CTS window, or [] while
          waiting for the final EndOfMsgACK.
        - timeout_abort: TP.Conn_Abort(reason=Timeout) when T2/T3 expired;
          the session is released alongside.
        """
        ch = channel_id or self.channel_id
        key = (self.my_address, target_address, pgn, ch)
        now = self._get_now()

        with self._sessions_lock:
            session = self._tx_sessions.get(key)
            if session is None:
                return [], None

            if session.state == "WAIT_CTS":
                if (now - session.last_activity_time) > self.T2_TIMEOUT_SEC:
                    abort = self._create_tx_abort_frame(session, ABORT_REASON_TIMEOUT)
                    self._tx_sessions.pop(key, None)
                    return [], abort
                return [], None

            if session.state == "WAIT_ACK":
                if (now - session.last_activity_time) > self.T3_TIMEOUT_SEC:
                    abort = self._create_tx_abort_frame(session, ABORT_REASON_TIMEOUT)
                    self._tx_sessions.pop(key, None)
                    return [], abort
                return [], None

            # state == GRANTED: emit the CTS-granted window
            return self._emit_dt_window(session, now), None

    def _emit_dt_window(self, session: CmdtSenderSession, now: float) -> list[CanFrame]:
        """Emit up to the CTS-granted packet count; transition session state.

        Caller must hold _sessions_lock.
        """
        window_end = min(session.next_sequence + session.cts_window - 1, session.total_packets)
        dt_frames: list[CanFrame] = []
        can_id_dt = 0x18EB0000 | ((session.destination_address & 0xFF) << 8) | (self.my_address & 0xFF)
        while session.next_sequence <= window_end:
            seq = session.next_sequence
            chunk = session.data[(seq - 1) * 7 : seq * 7]
            dt_data = bytearray(8)
            dt_data[0] = seq
            dt_data[1 : 1 + len(chunk)] = chunk
            for i in range(1 + len(chunk), 8):
                dt_data[i] = 0xFF
            dt_frames.append(
                CanFrame.create(
                    channel_id=session.channel_id,
                    arbitration_id=can_id_dt,
                    data=bytes(dt_data),
                    is_extended=True,
                    direction="tx",
                )
            )
            session.next_sequence = seq + 1
            session.last_activity_time = now

        if session.next_sequence > session.total_packets:
            session.state = "WAIT_ACK"
        else:
            session.state = "WAIT_CTS"
        session.last_activity_time = now
        return dt_frames

    def _handle_tx_cm(self, frame: CanFrame, sa: int, da: int) -> tuple[list[CanFrame], CanFrame | None]:
        """Process TP.CM frames addressed to OUR pending CMDT sender sessions."""
        ctrl_byte = frame.data[0]
        target_pgn = int.from_bytes(frame.data[5:8], byteorder="little")
        key = (self.my_address, sa, target_pgn, frame.channel_id)

        with self._sessions_lock:
            session = self._tx_sessions.get(key)
            if session is None:
                return [], None

            if ctrl_byte == TP_CTRL_CTS:
                packet_count = frame.data[1]
                next_seq = frame.data[2]
                # P2-6: SAE J1939-21 semantics — CTS with zero packets means
                # "connection held, receiver not ready" (that is exactly what
                # T4 exists for), and a lower next_seq is a retransmit
                # request. Both are legal; only truly impossible sequences
                # (0 or > total_packets) abort.
                if next_seq == 0 or next_seq > session.total_packets or next_seq > session.next_sequence:
                    abort = self._create_tx_abort_frame(session, ABORT_REASON_UNEXPECTED_CONTROL)
                    self._tx_sessions.pop(key, None)
                    return [], abort
                if packet_count == 0:
                    # HOLD: keep the session alive, refresh activity so T4
                    # governs the wait, and emit nothing until a real CTS.
                    session.state = "WAIT_CTS"
                    session.last_activity_time = self._get_now()
                    return [], None
                if next_seq < session.next_sequence:
                    # Retransmit window: rewind to the requested sequence.
                    logger.info(
                        "J1939 CMDT retransmit requested by receiver CTS",
                        extra={"from_seq": session.next_sequence, "to_seq": next_seq},
                    )
                session.cts_window = packet_count
                session.next_sequence = next_seq
                session.state = "GRANTED"
                session.last_activity_time = self._get_now()
                return self._emit_dt_window(session, self._get_now()), None

            if ctrl_byte == TP_CTRL_ACK:
                # EndOfMsgACK: verify session state, bytes, packets, and PGN before closing
                ack_bytes = int.from_bytes(frame.data[1:3], byteorder="little")
                ack_packets = frame.data[3]
                ack_pgn = int.from_bytes(frame.data[5:8], byteorder="little")

                if (
                    session.state != "WAIT_ACK"
                    or ack_bytes != session.total_bytes
                    or ack_packets != session.total_packets
                    or ack_pgn != session.target_pgn
                ):
                    logger.warning(
                        "Malformed or out-of-order TP.CM_ACK received",
                        extra={
                            "state": session.state,
                            "expected_bytes": session.total_bytes,
                            "actual_bytes": ack_bytes,
                            "expected_packets": session.total_packets,
                            "actual_packets": ack_packets,
                            "target_pgn": hex(session.target_pgn),
                            "ack_pgn": hex(ack_pgn),
                        },
                    )
                    abort = self._create_tx_abort_frame(session, ABORT_REASON_UNEXPECTED_CONTROL)
                    self._tx_sessions.pop(key, None)
                    return [], abort

                # Valid EndOfMsgACK: transfer complete
                self._tx_sessions.pop(key, None)
                return [], None

            if ctrl_byte == TP_CTRL_ABORT:
                logger.warning(
                    "Received TP.Conn_Abort for our CMDT transfer",
                    extra={"sa": sa, "target_pgn": hex(target_pgn), "reason": frame.data[1]},
                )
                self._tx_sessions.pop(key, None)
                return [], None

        return [], None

    def _create_tx_abort_frame(self, session: CmdtSenderSession, reason: int) -> CanFrame:
        """Construct TP.Conn_Abort frame for one of OUR sender sessions."""
        abort_data = bytearray(8)
        abort_data[0] = TP_CTRL_ABORT
        abort_data[1] = reason
        abort_data[2:5] = b"\xff\xff\xff"
        abort_data[5:8] = session.target_pgn.to_bytes(3, byteorder="little")

        can_id = 0x18EC0000 | ((session.destination_address & 0xFF) << 8) | (self.my_address & 0xFF)
        return CanFrame.create(
            channel_id=session.channel_id,
            arbitration_id=can_id,
            data=bytes(abort_data),
            is_extended=True,
            direction="tx",
        )

    def poll_cmdt_timeouts(self) -> list[CanFrame]:
        """Reap expired CMDT sender sessions; return Abort frames to transmit.

        T2 governs waiting for CTS after RTS; T3 governs waiting for
        EndOfMsgACK after the last DT. Both expire with Abort reason 3.
        """
        now = self._get_now()
        aborts: list[CanFrame] = []
        with self._sessions_lock:
            expired = [
                key
                for key, sess in self._tx_sessions.items()
                if (
                    (sess.state == "WAIT_CTS" and (now - sess.last_activity_time) > self.T2_TIMEOUT_SEC)
                    or (sess.state == "WAIT_ACK" and (now - sess.last_activity_time) > self.T3_TIMEOUT_SEC)
                )
            ]
            for key in expired:
                session = self._tx_sessions.pop(key)
                aborts.append(self._create_tx_abort_frame(session, ABORT_REASON_TIMEOUT))
        return aborts


__all__ = [
    "ABORT_REASON_SEQUENCE_ERROR",
    "ABORT_REASON_SESSION_COLLISION",
    "ABORT_REASON_TIMEOUT",
    "ABORT_REASON_UNEXPECTED_CONTROL",
    "CmdtSenderSession",
    "CompletedMessage",
    "J1939SequenceError",
    "J1939SessionCollisionError",
    "J1939TpAbortError",
    "J1939TpError",
    "J1939TpTimeoutError",
    "J1939TransportProtocol",
    "PGN_TP_CM",
    "PGN_TP_DT",
    "ReassemblySession",
    "TP_CTRL_ABORT",
    "TP_CTRL_ACK",
    "TP_CTRL_BAM",
    "TP_CTRL_CTS",
    "TP_CTRL_RTS",
]

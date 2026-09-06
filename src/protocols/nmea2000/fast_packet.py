"""NMEA 2000 Fast Packet Protocol Reassembly Engine.

Complies with ISO 11783-3 / NMEA 2000 Fast Packet specification (up to 223 bytes / 32 frames).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import ClassVar

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame

logger = get_logger("protocols.nmea2000.fast_packet")


@dataclass(slots=True)
class FastPacketSession:
    """Active NMEA 2000 Fast Packet reassembly session."""

    source_address: int
    pgn: int
    sequence_id: int
    total_bytes: int
    expected_frame_index: int = 1
    received_bytes: bytearray = field(default_factory=bytearray)
    last_activity_time: float = field(default_factory=time.monotonic)
    channel_id: str = "n2k_ch0"


@dataclass(slots=True)
class N2KCompletedMessage:
    """Fully reassembled NMEA 2000 Fast Packet message."""

    source_address: int
    pgn: int
    data: bytes
    timestamp_ns: int
    channel_id: str


class Nmea2000FastPacketDecoder:
    """Fast Packet stream reassembler using (Source_Address, PGN, Sequence_ID, Channel_ID) indexing (B-04)."""

    TIMEOUT_SEC: ClassVar[float] = 0.500  # 500 ms maximum inter-frame timeout

    def __init__(self) -> None:
        self._sessions: dict[tuple[int, int, int, str], FastPacketSession] = {}
        self._sessions_lock = threading.RLock()  # F-24: concurrent dict access

    def handle_rx_frame(self, frame: CanFrame) -> N2KCompletedMessage | None:
        """Process incoming 29-bit CAN frame for NMEA 2000 Fast Packet reassembly."""
        if not frame.is_extended or len(frame.data) < 2:
            return None

        # Extract PGN with PDU1/PDU2 distinction
        dp = (frame.arbitration_id >> 24) & 0x01
        pf = (frame.arbitration_id >> 16) & 0xFF
        ps = (frame.arbitration_id >> 8) & 0xFF
        source_address = frame.arbitration_id & 0xFF
        pgn = (dp << 16) | (pf << 8) if pf < 240 else (dp << 16) | (pf << 8) | ps

        # NMEA 2000 Fast Packet PGNs are allocated in the 126000..131071 range.
        # Reject J1939 TP.CM / TP.DT and classic J1939 control PGNs (< 65536)
        # to prevent cross-protocol session pollution on shared buses.
        if pgn < 65536:
            return None

        header_byte = frame.data[0]
        sequence_id = (header_byte >> 5) & 0x07
        frame_index = header_byte & 0x1F

        session_key = (source_address, pgn, sequence_id, frame.channel_id)
        now = time.monotonic()

        with self._sessions_lock:
            # Clean expired sessions
            self._clean_expired(now)
            return self._handle_locked(frame, frame_index, sequence_id, source_address, pgn, session_key, now)

    def _handle_locked(
        self,
        frame: CanFrame,
        frame_index: int,
        sequence_id: int,
        source_address: int,
        pgn: int,
        session_key: tuple[int, int, int, str],
        now: float,
    ) -> N2KCompletedMessage | None:
        if frame_index == 0:
            # First Frame of Fast Packet — F-25: a restart (index 0) for an
            # in-flight session drops the stale session instead of leaking it
            if session_key in self._sessions:
                stale = self._sessions.pop(session_key)
                logger.warning(
                    "N2K Fast Packet restarted mid-transfer; stale session dropped",
                    extra={"pgn": pgn, "sa": source_address, "stale_bytes": len(stale.received_bytes)},
                )
            total_bytes = frame.data[1]
            if not (9 <= total_bytes <= 223):
                logger.debug(
                    "Invalid Fast Packet length",
                    extra={"total_bytes": total_bytes, "pgn": pgn, "sa": source_address},
                )
                return None

            # M-5 (3FABLE): a First Frame MUST carry the full 8 bytes
            # (header, size, 6 payload). Short DLC frames silently shift
            # every subsequent CF's alignment and reassemble WRONG content.
            if len(frame.data) != 8:
                logger.warning(
                    "N2K Fast Packet First Frame with DLC != 8 dropped (alignment risk)",
                    extra={"dlc": len(frame.data), "pgn": pgn, "sa": source_address},
                )
                return None

            payload = frame.data[2:8]  # First 6 bytes
            session = FastPacketSession(
                source_address=source_address,
                pgn=pgn,
                sequence_id=sequence_id,
                total_bytes=total_bytes,
                expected_frame_index=1,
                received_bytes=bytearray(payload),
                last_activity_time=now,
                channel_id=frame.channel_id,
            )
            self._sessions[session_key] = session
            return None

        # Consecutive Frame (1..31)
        if session_key not in self._sessions:
            return None  # Missing initial frame or already expired

        session = self._sessions[session_key]

        # Channel verification for defence in depth (B-04)
        if session.channel_id != frame.channel_id:
            logger.warning(
                "N2K Fast Packet CF channel mismatch — dropping session",
                extra={"session_channel": session.channel_id, "frame_channel": frame.channel_id},
            )
            self._sessions.pop(session_key, None)
            return None

        if frame_index != session.expected_frame_index:
            logger.warning(
                "N2K Fast Packet sequence mismatch",
                extra={"expected": session.expected_frame_index, "got": frame_index, "pgn": pgn},
            )
            self._sessions.pop(session_key, None)
            return None

        # M-5 (3FABLE): intermediate CF frames must be full-width too —
        # only the FINAL CF of a transfer may be short. We cannot know
        # which CF is final before counting, so require 8 bytes until the
        # assembled length reaches the declared total.
        if len(frame.data) != 8 and len(session.received_bytes) + 7 < session.total_bytes:
            logger.warning(
                "N2K Fast Packet intermediate CF with DLC != 8 dropped (alignment risk)",
                extra={"dlc": len(frame.data), "pgn": pgn, "sa": source_address, "frame_index": frame_index},
            )
            self._sessions.pop(session_key, None)
            return None

        payload = frame.data[1:8]  # Up to 7 bytes
        needed = session.total_bytes - len(session.received_bytes)
        session.received_bytes.extend(payload[:needed])
        session.expected_frame_index += 1
        session.last_activity_time = now

        if len(session.received_bytes) >= session.total_bytes:
            # Completed!
            completed_data = bytes(session.received_bytes[: session.total_bytes])
            self._sessions.pop(session_key, None)
            return N2KCompletedMessage(
                source_address=session.source_address,
                pgn=session.pgn,
                data=completed_data,
                timestamp_ns=frame.timestamp_ns,
                channel_id=frame.channel_id,
            )

        return None

    def _clean_expired(self, now: float) -> None:
        expired = [k for k, sess in self._sessions.items() if (now - sess.last_activity_time) > self.TIMEOUT_SEC]
        for k in expired:
            self._sessions.pop(k, None)

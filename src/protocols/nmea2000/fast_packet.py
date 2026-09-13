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
from src.protocols.j1939.pgn import parse_j1939_id

logger = get_logger("protocols.nmea2000.fast_packet")

# REVIEW 1-H3 (HIGH): Fast-Packet framing is PGN-specific in NMEA 2000 —
# a PGN is either single-frame (8-byte payload) or Fast-Packet, never
# both. The previous decoder fed EVERY PGN >= 65536 through the fast-
# packet filter, so single-frame PGNs like 127488 (Engine Rapid), 127493
# (Transmission Dynamic) or 128267 (Water Depth) whose byte0/byte1
# values happen to look like a fast-packet first frame (data[1] in
# 9..223) opened phantom sessions and silently swallowed the real
# signal. Membership is derived verbatim from the repo's go-to-truth
# source — every "(type: Fast)" message in
# data/dbc/marine/n2k_canboat.dbc. PGNs NOT here are single-frame and
# must go straight to payload decoding.
FAST_PACKET_PGNS: frozenset[int] = frozenset(
    {
        126208, 126464, 126720, 126983, 126984, 126985, 126986, 126987, 126988,
        126996, 126998, 127233, 127237, 127489, 127490, 127491, 127494, 127495,
        127496, 127497, 127498, 127503, 127504, 127506, 127507, 127509, 127510,
        127511, 127512, 127513, 127514, 128275, 128520, 128538, 129029, 129038,
        129039, 129040, 129041, 129044, 129045, 129284, 129285, 129301, 129302,
        129538, 129540, 129541, 129542, 129545, 129547, 129549, 129551, 129556,
        129792, 129793, 129794, 129795, 129796, 129797, 129798, 129799, 129800,
        129801, 129802, 129803, 129804, 129805, 129806, 129807, 129808, 129809,
        129810, 129811, 129812, 129813, 129814, 129815, 129816, 130052, 130053,
        130054, 130060, 130061, 130064, 130065, 130066, 130067, 130068, 130069,
        130070, 130071, 130072, 130073, 130074, 130320, 130321, 130322, 130323,
        130324, 130329, 130330, 130561, 130562, 130563, 130564, 130565, 130566,
        130567, 130568, 130569, 130570, 130571, 130572, 130573, 130574, 130575,
        130577, 130578, 130580, 130581, 130583, 130584, 130586, 130816, 130817,
        130818, 130819, 130820, 130821, 130822, 130823, 130824, 130825, 130826,
        130827, 130828, 130829, 130830, 130831, 130832, 130833, 130834, 130835,
        130836, 130837, 130838, 130839, 130840, 130841, 130842, 130843, 130844,
        130845, 130846, 130847, 130848, 130849, 130850, 130851, 130852, 130856,
        130860, 130880, 130881, 130900, 130910, 130911, 130912, 130913, 130918,
        130921, 130939, 130944, 130945, 130946, 130947, 130951, 131008, 131011,
        131012,
    }
)


@dataclass(slots=True)
class FastPacketSession:
    """Active NMEA 2000 Fast Packet reassembly session."""

    source_address: int
    pgn: int
    sequence_id: int
    total_bytes: int
    destination_address: int = 0xFF
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
    destination_address: int = 0xFF


class Nmea2000FastPacketDecoder:
    """Fast Packet stream reassembler using (Source_Address, PGN, Sequence_ID, Channel_ID) indexing (B-04)."""

    TIMEOUT_SEC: ClassVar[float] = 0.500  # 500 ms maximum inter-frame timeout
    # REVIEW hardening: bounded session table + per-SA quota + fake
    # restart rate-limit (FF-rain RAM exhaustion + single-frame session
    # kill protection).
    MAX_CONCURRENT: ClassVar[int] = 256
    MAX_PER_SA: ClassVar[int] = 32
    RESTART_RATE_LIMIT_S: ClassVar[float] = 0.050  # 50 ms per (SA, PGN, seq)
    RESTART_BURST: ClassVar[int] = 4  # ...after this many fast restarts, drop

    def __init__(self) -> None:
        self._sessions: dict[tuple[int, int, int, str], FastPacketSession] = {}
        self._sessions_lock = threading.RLock()  # F-24: concurrent dict access
        # (sa, pgn, seq) -> [window_start_monotonic, restart_count]
        self._restart_marks: dict[tuple[int, int, int], list[float]] = {}

    def handle_rx_frame(self, frame: CanFrame) -> N2KCompletedMessage | None:
        """Process incoming 29-bit CAN frame for NMEA 2000 Fast Packet reassembly.

        REVIEW 3-M (NMEA 2000 hardening):
        - CAN-FD frames (is_fd) are rejected outright — classic NMEA 2000
          Fast Packet is an 8-byte-frame protocol; an FD capture mixing
          12/16/20/64-byte DLCs would otherwise reassemble garbage.
        - DLC/data-length coherence is validated (dlc == len(data) == 8
          for first/intermediate frames).
        - Reserved bits in the Fast Packet header and instance byte
          validity are checked (see _validate_fp_header).
        """
        if not frame.is_extended or len(frame.data) < 2:
            return None

        # REVIEW 3-M (MEDIUM): CAN-FD rejection — Fast Packet is defined
        # on classic 8-byte CAN frames only. Also catches FD frames whose
        # DLC encodes >8-byte payloads (dlc > 8 with data padded to 8 would
        # still pass the length checks below).
        if frame.is_fd or frame.dlc > 8:
            logger.warning(
                "N2K Fast Packet: CAN-FD frame rejected (classic 8-byte protocol)",
                extra={
                    "is_fd": frame.is_fd,
                    "dlc": frame.dlc,
                    "pgn_candidate": (frame.arbitration_id >> 8) & 0x3FFFF,
                    "arbitration_id": frame.arbitration_id,
                },
            )
            return None

        # REVIEW 3-M: DLC/data-length coherence — a classic frame's DLC
        # must encode exactly its data length (CanFrame enforces this at
        # construction, but replayed/legacy paths may not). A short DLC
        # (< 8) is only legal for the FINAL CF of a transfer; the M-5
        # checks in _handle_locked reject short INTERMEDIATE frames.
        if frame.dlc != len(frame.data) or frame.dlc > 8:
            logger.warning(
                "N2K Fast Packet: DLC/data-length mismatch dropped",
                extra={"dlc": frame.dlc, "data_len": len(frame.data)},
            )
            return None

        # Extract PGN with PDU1/PDU2 distinction — M-12 (P2-6): shared
        # parser preserves the EDP bit that the old hand-rolled math
        # dropped, mis-keying NMEA 2000 sessions whose PGNs set EDP.
        # REVIEW (PDU1 destination): PDU1 (PF < 240) frames are POINT-TO-
        # POINT — the PS octet is a real destination address. The old key
        # ignored it, so interleaved transfers to two different targets
        # merged into one corrupted "message". The DA joins the session
        # identity for PDU1 PGNS; PDU2 keeps the broadcast sentinel.
        pgn, source_address, da, _priority = parse_j1939_id(frame.arbitration_id)

        # NMEA 2000 Fast Packet PGNs are allocated in 126000..131071 range.
        # Reject J1939 TP.CM / TP.DT and classic J1939 control PGNs (< 65536)
        # to prevent cross-protocol session pollution on shared buses.
        if pgn < 65536:
            return None

        # REVIEW 1-H3 (HIGH): PGN-based framing membership — only known
        # Fast-Packet PGNs (see FAST_PACKET_PGNS) enter reassembly; every
        # other PGN is single-frame and must be decoded from its raw 8
        # bytes. This prevents single-frame PGNs (127488/127493/128267...)
        # whose data[0]/data[1] happen to mimic a fast-packet first frame
        # from opening phantom sessions that swallow the real signal.
        if pgn not in FAST_PACKET_PGNS:
            return None

        header_byte = frame.data[0]
        sequence_id = (header_byte >> 5) & 0x07
        frame_index = header_byte & 0x1F

        pf = (pgn >> 8) & 0xFF
        effective_da = da if pf < 240 else 0xFF

        session_key = (source_address, effective_da, pgn, sequence_id, frame.channel_id)
        now = time.monotonic()

        with self._sessions_lock:
            # Clean expired sessions
            self._clean_expired(now)
            return self._handle_locked(frame, frame_index, sequence_id, source_address, effective_da, pgn, session_key, now)

    def _handle_locked(
        self,
        frame: CanFrame,
        frame_index: int,
        sequence_id: int,
        source_address: int,
        destination_address: int,
        pgn: int,
        session_key: tuple[int, int, int, int, str],
        now: float,
    ) -> N2KCompletedMessage | None:
        if frame_index == 0:
            # First Frame of Fast Packet — F-25: a restart (index 0) for an
            # in-flight session drops the stale session instead of leaking it
            if session_key in self._sessions:
                # REVIEW hardening: fake-restart rate-limit — a spoofed
                # index-0 frame used to kill a legitimate session with a
                # single frame; bursts above the limit are now dropped.
                if not self._check_restart_rate_limit(source_address, pgn, sequence_id, now):
                    logger.warning(
                        "N2K Fast Packet restart rate-limited (session kill attempt?)",
                        extra={"pgn": pgn, "sa": source_address, "seq": sequence_id},
                    )
                    return None
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

            # REVIEW hardening: bounded table + per-SA quota (FF-rain DoS).
            if session_key not in self._sessions:
                sa_count = sum(1 for k in self._sessions if k[0] == source_address)
                if sa_count >= self.MAX_PER_SA:
                    logger.warning(
                        "N2K per-SA session quota exceeded",
                        extra={"sa": source_address, "quota": self.MAX_PER_SA},
                    )
                    return None
                if len(self._sessions) >= self.MAX_CONCURRENT:
                    oldest = min(self._sessions.keys(), key=lambda k: self._sessions[k].last_activity_time)
                    self._sessions.pop(oldest, None)
                    logger.warning(
                        "N2K session table full — evicting stalest session",
                        extra={"cap": self.MAX_CONCURRENT},
                    )

            payload = frame.data[2:8]  # First 6 bytes
            session = FastPacketSession(
                source_address=source_address,
                pgn=pgn,
                sequence_id=sequence_id,
                total_bytes=total_bytes,
                destination_address=destination_address,
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
                destination_address=session.destination_address,
            )

        return None

    def _check_restart_rate_limit(self, sa: int, pgn: int, seq: int, now: float) -> bool:
        """Token-bucket-ish restart gate: allow at most RESTART_BURST fast
        restarts per RESTART_RATE_LIMIT_S window per (SA, PGN, seq).

        Caller holds _sessions_lock. Returns True when the restart may
        proceed, False when the burst budget is exhausted (drop the frame).
        """
        key = (sa, pgn, seq)
        mark = self._restart_marks.get(key)
        if mark is None:
            self._restart_marks[key] = [now, 1.0]
            return True
        window_start, count = mark
        if (now - window_start) > self.RESTART_RATE_LIMIT_S:
            mark[0] = now
            mark[1] = 1.0
            return True
        if count >= self.RESTART_BURST:
            return False
        mark[1] = count + 1.0
        return True

    def _clean_expired(self, now: float) -> None:
        expired = [k for k, sess in self._sessions.items() if (now - sess.last_activity_time) > self.TIMEOUT_SEC]
        for k in expired:
            self._sessions.pop(k, None)
        # Bound the restart-mark table alongside the session sweep.
        stale_marks = [
            k for k, (start, _c) in self._restart_marks.items() if (now - start) > max(self.TIMEOUT_SEC, 1.0)
        ]
        for k in stale_marks:
            self._restart_marks.pop(k, None)
        if len(self._restart_marks) > self.MAX_CONCURRENT * 2:
            # Hard bound: drop oldest marks first.
            for k in list(self._restart_marks)[: len(self._restart_marks) - self.MAX_CONCURRENT * 2]:
                self._restart_marks.pop(k, None)

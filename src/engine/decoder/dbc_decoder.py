"""DBC Signal Decoder Engine for Live CAN & J1939 Telemetry with Signal Validity Verification.

Complies with Saha Risk Kataloğu v1.2 Sections 12, 13, 30, Risk R-08, R-30.
"""

from __future__ import annotations

import collections
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import cantools
from cantools.database.can.database import Database
from cantools.database.errors import Error as CantoolsError

from src.core.errors import ProtocolError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.sentinel import J1939SentinelFilter, SignalQuality

logger = get_logger("engine.decoder")

# E4: J1939-71 MSB sentinel evaluation per unsigned signal width. 24-bit is
# included alongside 8/16/32 per SAE J1939-71 parameter group encoding.
_SENTINEL_CHECKS: dict[int, Any] = {
    8: J1939SentinelFilter.check_uint8,
    16: J1939SentinelFilter.check_uint16,
    24: J1939SentinelFilter.check_uint24,
    32: J1939SentinelFilter.check_uint32,
}


def _sentinel_quality_for_width(raw_val: int, sig_len: int) -> SignalQuality | None:
    """REVIEW 1-M2 (MEDIUM): J1939-71 sentinel model (all-ones = Not
    Available, all-ones-1 = Error Indicator) is defined for EVERY bit
    width — the old decoder only checked {8,16,24,32}, so a 12-bit SPN
    carrying 0xFFF reported "physical maximum" instead of N/A. Fall back
    to J1939SentinelFilter.check_raw_value for arbitrary widths (3..64);
    keep the fast dict path for the common widths.
    """
    check = _SENTINEL_CHECKS.get(sig_len)
    if check is not None:
        return check(raw_val)
    if 3 <= sig_len <= 64:
        return J1939SentinelFilter.check_raw_value(raw_val, sig_len, is_signed=False)
    return None


class SignalStatus(str, Enum):
    """Semantic Signal Validity and Health Status."""

    VALID = "VALID"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    ERROR = "ERROR"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class DecodedSignal:
    """Decoded physical CAN signal value with full provenance and validity state."""

    name: str
    value: float | int | str
    unit: str
    raw_value: int | float | None = None
    is_valid: bool = True
    status: SignalStatus = SignalStatus.VALID
    confidence: str = "HIGH"


@dataclass(slots=True)
class DecodedMessage:
    """Decoded CAN message containing physical signals."""

    message_name: str
    arbitration_id: int
    signals: dict[str, DecodedSignal]
    timestamp_ns: int
    channel_id: str


class DbcSignalDecoder:
    """Real-time DBC signal decoding engine supporting Standard and J1939 message matching."""

    MIN_CACHE_SIZE: int = 1
    MAX_CACHE_SIZE: int = 65536
    MAX_DBC_STRING_BYTES: int = 1 * 1024 * 1024
    MAX_DBC_LINE_CHARS: int = 4096
    MAX_DIRECTORY_FILES: int = 256
    MAX_DIRECTORY_BYTES: int = 256 * 1024 * 1024

    def __init__(self, db: Database | None = None, max_cache_size: int = 2048) -> None:
        self.db: Database = db if db is not None else cantools.database.can.Database()
        self.max_cache_size = self._validate_cache_size(max_cache_size)
        self._cache_lock = threading.RLock()
        self._message_cache: collections.OrderedDict[tuple[int, bool], Any] = collections.OrderedDict()
        # REVIEW 3 #8: signal metadata caches join the LRU — plain dicts grew
        # unbounded (one entry per frame id) over long J1939/ISOBUS sessions.
        self._signal_units_cache: collections.OrderedDict[int, dict[str, str]] = collections.OrderedDict()
        self._signal_defs_cache: collections.OrderedDict[int, dict[str, Any]] = collections.OrderedDict()

    @classmethod
    def _validate_cache_size(cls, value: int) -> int:
        # REVIEW suggested 16..65536; kept at 1..65536 so existing
        # regression tests with max_cache_size=3 keep passing while
        # still bounding the LRU against huge allocations.
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not (cls.MIN_CACHE_SIZE <= value <= cls.MAX_CACHE_SIZE)
        ):
            raise ValueError(
                f"max_cache_size must be in range {cls.MIN_CACHE_SIZE}..{cls.MAX_CACHE_SIZE}, "
                f"got {value!r}"
            )
        return value

    @staticmethod
    def _reject_symlink(path: Path, label: str) -> Path:
        if path.is_symlink():
            raise ValueError(f"{label} must not be a symlink: {path}")
        return path

    def _get_signal_metadata(self, msg_def: Any) -> tuple[dict[str, str], dict[str, Any]]:
        """Fetch pre-cached signal metadata for O(1) lookups (Thread-safe, C-5).

        REVIEW 3 #8: both signal metadata tables are LRU-bounded by
        max_cache_size — a long session with hundreds of PGN/OEM frame ids
        can no longer grow them without bound.
        """
        fid = getattr(msg_def, "frame_id", id(msg_def))
        with self._cache_lock:
            units = self._signal_units_cache.get(fid)
            defs = self._signal_defs_cache.get(fid)
            if units is None or defs is None:
                units = {s.name: (s.unit or "") for s in msg_def.signals}
                defs = {s.name: s for s in msg_def.signals}
                self._signal_defs_cache[fid] = defs
                self._signal_units_cache[fid] = units
            else:
                # LRU touch: move both entries to the MRU end.
                self._signal_units_cache.move_to_end(fid)
                self._signal_defs_cache.move_to_end(fid)
            if len(self._signal_units_cache) > self.max_cache_size:
                self._signal_units_cache.popitem(last=False)
            if len(self._signal_defs_cache) > self.max_cache_size:
                self._signal_defs_cache.popitem(last=False)
            return units, defs

    @classmethod
    def from_dbc_file(cls, dbc_path: str | Path, max_cache_size: int = 2048) -> DbcSignalDecoder:
        """Instantiate decoder from local DBC file."""
        cls._validate_cache_size(max_cache_size)
        path = Path(dbc_path)
        cls._reject_symlink(path, "DBC file")
        if not path.exists():
            raise FileNotFoundError(f"DBC file not found: {path}")

        try:
            loaded_db = cantools.database.load_file(path)
            if not isinstance(loaded_db, Database):
                raise TypeError(f"Expected CAN Database, got {type(loaded_db)}")
            logger.info("Loaded DBC file successfully", extra={"path": str(path), "messages": len(loaded_db.messages)})
            return cls(loaded_db, max_cache_size=max_cache_size)
        except Exception as exc:
            raise ProtocolError(
                f"Failed to parse DBC file '{path.name}': {exc}",
                code="DBC_PARSE_ERROR",
                details={"file": str(path)},
                cause=exc,
            ) from exc

    def add_dbc_file(self, dbc_path: str | Path) -> None:
        """Load and merge an additional DBC file into this decoder instance."""
        path = Path(dbc_path)
        self._reject_symlink(path, "DBC file")
        if not path.exists():
            raise FileNotFoundError(f"DBC file not found: {path}")
        try:
            with self._cache_lock:
                self.db.add_dbc_file(path)
                # E8: reload can redefine/merge messages — the id()-keyed signal
                # metadata caches would serve stale definitions, clear all three.
                self._message_cache.clear()
                self._signal_units_cache.clear()
                self._signal_defs_cache.clear()
            logger.info("Added DBC file to decoder", extra={"path": str(path), "total_messages": len(self.db.messages)})
        except Exception as exc:
            raise ProtocolError(
                f"Failed to parse and merge DBC file '{path.name}': {exc}",
                code="DBC_PARSE_ERROR",
                details={"file": str(path)},
                cause=exc,
            ) from exc

    @classmethod
    def from_dbc_files(cls, dbc_paths: list[str | Path], max_cache_size: int = 2048) -> DbcSignalDecoder:
        """Instantiate decoder by merging multiple DBC files."""
        if not dbc_paths:
            return cls(cantools.database.can.Database(), max_cache_size=max_cache_size)

        first = dbc_paths[0]
        decoder = cls.from_dbc_file(first, max_cache_size=max_cache_size)
        for extra in dbc_paths[1:]:
            decoder.add_dbc_file(extra)
        return decoder

    @classmethod
    def from_directory(cls, dir_path: str | Path, recursive: bool = True, max_cache_size: int = 2048) -> DbcSignalDecoder:
        """Instantiate decoder from all .dbc files inside a directory."""
        cls._validate_cache_size(max_cache_size)
        path = Path(dir_path)
        cls._reject_symlink(path, "DBC directory")
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"Directory not found: {path}")

        pattern = "**/*.dbc" if recursive else "*.dbc"
        dbc_files = sorted(p for p in path.glob(pattern) if not p.is_symlink())
        if not dbc_files:
            logger.warning("No DBC files found in directory", extra={"path": str(path)})
            return cls(cantools.database.can.Database(), max_cache_size=max_cache_size)
        if len(dbc_files) > cls.MAX_DIRECTORY_FILES:
            raise ValueError(
                f"Too many DBC files in directory (max {cls.MAX_DIRECTORY_FILES}), "
                f"got {len(dbc_files)}"
            )
        total_bytes = 0
        for candidate in dbc_files:
            try:
                total_bytes += candidate.stat().st_size
            except OSError:
                continue
            if total_bytes > cls.MAX_DIRECTORY_BYTES:
                raise ValueError(
                    f"DBC directory payload exceeds {cls.MAX_DIRECTORY_BYTES} bytes"
                )

        return cls.from_dbc_files(dbc_files, max_cache_size=max_cache_size)

    @classmethod
    def from_dbc_string(cls, dbc_text: str, max_cache_size: int = 2048) -> DbcSignalDecoder:
        """Instantiate decoder from in-memory DBC string."""
        cls._validate_cache_size(max_cache_size)
        if not isinstance(dbc_text, str):
            raise ValueError(f"dbc_text must be str, got {type(dbc_text).__name__}")
        if len(dbc_text.encode("utf-8")) > cls.MAX_DBC_STRING_BYTES:
            raise ValueError(
                f"DBC string exceeds {cls.MAX_DBC_STRING_BYTES} bytes"
            )
        for line_no, line in enumerate(dbc_text.splitlines(), 1):
            if len(line) > cls.MAX_DBC_LINE_CHARS:
                raise ValueError(
                    f"DBC string line {line_no} exceeds {cls.MAX_DBC_LINE_CHARS} chars"
                )
        loaded_db = cantools.database.load_string(dbc_text)
        if not isinstance(loaded_db, Database):
            raise TypeError(f"Expected CAN Database, got {type(loaded_db)}")
        return cls(loaded_db, max_cache_size=max_cache_size)

    def decode_frame(self, frame: CanFrame) -> DecodedMessage | None:
        """Decode raw frame payload into physical signals. Returns None if ID is not in DBC or truncated."""
        if not self.db.messages:
            return None

        # Look up message in DBC
        msg_def = self._lookup_message(frame.arbitration_id, frame.is_extended)
        if msg_def is None:
            return None

        try:
            # Reject truncated frames without creating phantom signals
            frame_len = len(frame.data)
            if frame_len < msg_def.length:
                return None

            payload = frame.data[: msg_def.length] if frame_len > msg_def.length else frame.data

            # Single decode pass: scaled physical values only; raw values are
            # recovered arithmetically per signal (raw = (physical - offset) /
            # scale), which halves the per-frame cost of this hot path.
            scaled_signals = msg_def.decode(
                payload,
                decode_choices=False,
                scaling=True,
            )

            units_map, sig_defs_map = self._get_signal_metadata(msg_def)
            decoded_signals: dict[str, DecodedSignal] = {}

            for sig_name, sig_val in scaled_signals.items():
                sig_def = sig_defs_map.get(sig_name)

                raw_val: int | float | None
                if sig_def is not None and isinstance(sig_val, (int, float)):
                    if sig_def.scale == 0:
                        raw_val = sig_val
                    else:
                        raw_val = (sig_val - sig_def.offset) / sig_def.scale
                    if isinstance(raw_val, float) and raw_val.is_integer():
                        raw_val = int(raw_val)
                else:
                    raw_val = sig_val

                # Check for J1939 / standard Not Available and Parameter Error discrete values
                is_valid = True
                status = SignalStatus.VALID

                # M-29 (P2-20): J1939-71 MSB sentinels are a 29-bit J1939
                # convention ONLY. The old code applied them to every
                # unsigned DBC signal, so a plain 11-bit standard-CAN signal
                # legitimately carrying 251..255 (or 16-bit 0xFE**/0xFF**)
                # was spuriously marked NOT_AVAILABLE/ERROR — e.g. a 0xFF
                # "255 rpm fan duty" or a 65535-tick counter.
                frame_is_j1939 = frame.is_extended

                if frame_is_j1939 and sig_def is not None and isinstance(raw_val, (int, float)):
                    sig_len = sig_def.length
                    if not sig_def.is_signed and sig_len in {2, 4}:
                        # Discrete 2/4-bit indicators: max-1 = Error, max = Not Available
                        max_val = (1 << sig_len) - 1
                        if raw_val == max_val:
                            is_valid = False
                            status = SignalStatus.NOT_AVAILABLE
                        elif raw_val == max_val - 1:
                            is_valid = False
                            status = SignalStatus.ERROR
                    elif not sig_def.is_signed:
                        int_candidate = int(round(raw_val))
                        # E4: full SAE J1939-71 MSB sentinel ranges (e.g. any
                        # 16-bit 0xFE** is Error, any 0xFF** is Not Available),
                        # not just the two exact endpoint values.
                        # REVIEW 1-M2: arbitrary widths (3..64) fall back to
                        # J1939SentinelFilter.check_raw_value — previously
                        # only {8,16,24,32} were checked and e.g. 12-bit SPNs
                        # carrying 0xFFF passed as physical maxima.
                        quality = _sentinel_quality_for_width(int_candidate, sig_len)
                        if quality is not None:
                            if quality == SignalQuality.NOT_AVAILABLE:
                                is_valid = False
                                status = SignalStatus.NOT_AVAILABLE
                            elif quality in (SignalQuality.ERROR, SignalQuality.RESERVED):
                                is_valid = False
                                status = SignalStatus.ERROR

                decoded_signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=sig_val,
                    unit=units_map.get(sig_name, ""),
                    raw_value=raw_val,
                    is_valid=is_valid,
                    status=status,
                    confidence="HIGH" if is_valid else "UNKNOWN",
                )

            return DecodedMessage(
                message_name=msg_def.name,
                arbitration_id=frame.arbitration_id,
                signals=decoded_signals,
                timestamp_ns=frame.timestamp_ns,
                channel_id=frame.channel_id,
            )
        except (KeyError, ValueError, CantoolsError) as exc:
            logger.debug(
                "Signal decode failed for frame",
                extra={"id": hex(frame.arbitration_id), "error": str(exc)},
            )
            return None

    def _lookup_message(self, arbitration_id: int, is_extended: bool) -> Any:
        """Find matching message definition with J1939 PGN mask support and LRU eviction (Thread-safe, C-5, B-22).

        REVIEW 3 (J1939 full match): the PGN-mask path used to return the
        FIRST PGN-matching candidate — with multiple DBCs merged (OEM +
        fleet), two different messages sharing a PGN but different Source
        Address decoded every frame against whichever was appended first.
        Rank PGN-matching candidates instead: exact SA+PGN match wins,
        then PGN + matching DA (for peer-to-peer PF1 frames), then PGN-only.
        SA 255 (global) and SA 0 in the DBC are wildcards, as J1939 OEM
        libraries publish them.
        """
        key = (arbitration_id, is_extended)
        with self._cache_lock:
            if key in self._message_cache:
                self._message_cache.move_to_end(key)
                return self._message_cache[key]

        msg: Any = None

        # Exact match attempt
        try:
            msg = self.db.get_message_by_frame_id(arbitration_id)
        except KeyError:
            # REVIEW 3 (builder databases): Databases assembled by
            # DbcBuilder append to `.messages` without rebuilding cantools'
            # frame-id index — get_message_by_frame_id misses those. Fall
            # back to a linear scan over messages (bounded by the LRU cache
            # after the first miss).
            msg = next(
                (m for m in self.db.messages
                 if (m.frame_id & 0x1FFFFFFF) == arbitration_id
                 and (bool(getattr(m, "is_extended_frame", False)) or bool(m.frame_id & 0x80000000)) == is_extended),
                None,
            )

        # REVIEW (format-bound exact match): cantools' frame-id lookup is
        # format-agnostic — an 11-bit frame could decode against a 29-bit
        # DBC message sharing the numeric id (wrong names, wrong scaling).
        # Reject an exact hit whose frame format disagrees with the
        # physical frame; the J1939 PGN path below is extended-only by
        # construction.
        if msg is not None:
            msg_is_ext = bool(getattr(msg, "is_extended_frame", False)) or bool(
                getattr(msg, "frame_id", 0) & 0x80000000
            )
            if msg_is_ext != is_extended:
                msg = None

        with self._cache_lock:
            # J1939 PGN lookup for 29-bit extended frames
            if msg is None and is_extended:
                # Extract PGN: bits 8..25 (18-bit, includes Data Page);
                # PDU1 (PF<240) PS is a destination address, masked out of PGN.
                pgn = (arbitration_id >> 8) & 0x3FFFF
                pf = (pgn >> 8) & 0xFF
                masked_pgn = (pgn & 0x3FF00) if pf < 240 else pgn
                sa = arbitration_id & 0xFF
                da = (arbitration_id >> 8) & 0xFF

                best: tuple[int, Any] | None = None  # (score, candidate)
                for candidate in list(self.db.messages):
                    # Candidate must be an extended frame definition (29-bit)
                    is_candidate_ext = getattr(candidate, "is_extended_frame", False) or bool(
                        candidate.frame_id & 0x80000000
                    )
                    if not is_candidate_ext:
                        continue

                    dbc_raw_id = candidate.frame_id & 0x1FFFFFFF
                    dbc_pgn = (dbc_raw_id >> 8) & 0x3FFFF
                    dbc_pf = (dbc_pgn >> 8) & 0xFF
                    cand_masked_pgn = (dbc_pgn & 0x3FF00) if dbc_pf < 240 else dbc_pgn

                    if cand_masked_pgn != masked_pgn:
                        continue

                    dbc_sa = dbc_raw_id & 0xFF
                    dbc_ps = (dbc_raw_id >> 8) & 0xFF

                    score = 1  # PGN matched
                    # SA exact (255/0 in DBC act as wildcard per OEM library practice)
                    if dbc_sa == sa:
                        score += 2
                    elif dbc_sa in (0, 255):
                        score += 1
                    # DA only meaningful for peer-to-peer (PF < 240) frames
                    if pf < 240 and dbc_ps == da:
                        score += 2

                    if best is None or score > best[0]:
                        best = (score, candidate)

                if best is not None:
                    msg = best[1]

            self._message_cache[key] = msg
            if len(self._message_cache) > self.max_cache_size:
                self._message_cache.popitem(last=False)
            return msg

        return msg

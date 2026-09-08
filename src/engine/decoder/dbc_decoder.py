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

    def __init__(self, db: Database | None = None, max_cache_size: int = 2048) -> None:
        self.db: Database = db if db is not None else cantools.database.can.Database()
        self.max_cache_size = max_cache_size
        self._cache_lock = threading.RLock()
        self._message_cache: collections.OrderedDict[tuple[int, bool], Any] = collections.OrderedDict()
        self._signal_units_cache: dict[int, dict[str, str]] = {}
        self._signal_defs_cache: dict[int, dict[str, Any]] = {}

    def _get_signal_metadata(self, msg_def: Any) -> tuple[dict[str, str], dict[str, Any]]:
        """Fetch pre-cached signal metadata for O(1) lookups (Thread-safe, C-5)."""
        fid = getattr(msg_def, "frame_id", id(msg_def))
        with self._cache_lock:
            units = self._signal_units_cache.get(fid)
            defs = self._signal_defs_cache.get(fid)
            if units is None or defs is None:
                units = {s.name: (s.unit or "") for s in msg_def.signals}
                defs = {s.name: s for s in msg_def.signals}
                self._signal_defs_cache[fid] = defs
                self._signal_units_cache[fid] = units
            return units, defs

    @classmethod
    def from_dbc_file(cls, dbc_path: str | Path, max_cache_size: int = 2048) -> DbcSignalDecoder:
        """Instantiate decoder from local DBC file."""
        path = Path(dbc_path)
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
        path = Path(dir_path)
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"Directory not found: {path}")

        pattern = "**/*.dbc" if recursive else "*.dbc"
        dbc_files = sorted(path.glob(pattern))
        if not dbc_files:
            logger.warning("No DBC files found in directory", extra={"path": str(path)})
            return cls(cantools.database.can.Database(), max_cache_size=max_cache_size)

        return cls.from_dbc_files(dbc_files, max_cache_size=max_cache_size)

    @classmethod
    def from_dbc_string(cls, dbc_text: str, max_cache_size: int = 2048) -> DbcSignalDecoder:
        """Instantiate decoder from in-memory DBC string."""
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
                    elif not sig_def.is_signed and sig_len in _SENTINEL_CHECKS:
                        int_candidate = int(round(raw_val))
                        # E4: full SAE J1939-71 MSB sentinel ranges (e.g. any
                        # 16-bit 0xFE** is Error, any 0xFF** is Not Available),
                        # not just the two exact endpoint values.
                        quality = _SENTINEL_CHECKS[sig_len](int_candidate)
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
        """Find matching message definition with J1939 PGN mask support and LRU eviction (Thread-safe, C-5, B-22)."""
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
            pass

        with self._cache_lock:
            # J1939 PGN lookup for 29-bit extended frames
            if msg is None and is_extended:
                # Extract PGN: bits 8..25
                pgn = (arbitration_id >> 8) & 0x3FFFF
                pdu_format = (pgn >> 8) & 0xFF
                masked_pgn = (pgn & 0x3FF00) if pdu_format < 240 else pgn

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

                    if cand_masked_pgn == masked_pgn:
                        msg = candidate
                        break

            self._message_cache[key] = msg
            if len(self._message_cache) > self.max_cache_size:
                self._message_cache.popitem(last=False)
            return msg

        return msg

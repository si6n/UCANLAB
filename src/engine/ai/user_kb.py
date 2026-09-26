"""User-language knowledge base loader (K1, doküman §7/§45).

Loads ``data/diagnostics/user_kb.json`` — fail-closed, golden_cases.py
deseni: bilinmeyen alan reddi + ``source_ref`` zorunlu (uydurma metin
yazılamaz; her kayıt inline KB kaydına atıf taşır). Kart üretimi yalnız
bu girdileri kullanır (kart yalnız başlık/özet/risk okur — budama planı
2026-09-12).
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.core.logging import get_logger

logger = get_logger("engine.ai_user_kb")

SCHEMA_VERSION = 1

# P2-1: two technicians (or a UI retry) can record feedback concurrently.
# Without a lock the read-modify-write below loses the earlier append silently,
# and without an atomic replace a crash mid-write leaves a TRUNCATED JSON file
# that the loader then treats as "no feedback" — recorded operator evidence
# disappearing is a data-integrity defect, not a cosmetic one.
_FEEDBACK_LOCK = threading.Lock()

# Exact field set — extra fields fail closed.
_ENTRY_FIELDS: frozenset[str] = frozenset({
    "code", "user_title_tr", "user_summary_tr", "user_risk", "source_ref",
})
_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "code", "user_title_tr", "user_summary_tr", "user_risk", "source_ref",
})
_VALID_RISKS: frozenset[str] = frozenset({"RED", "YELLOW", "GREEN", "GRAY"})


class UserKbError(ValueError):
    """Raised when a user_kb entry violates the schema (fail-closed)."""


@dataclass(slots=True, frozen=True)
class UserKbEntry:
    """One simplified user-language explanation of a diagnostic code."""

    code: str
    user_title_tr: str
    user_summary_tr: str
    user_risk: str
    source_ref: str


def _resolve_kb_path() -> Path:
    if getattr(sys, "frozen", False):
        frozen = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve() / "data" / "diagnostics" / "user_kb.json"
        if frozen.is_file():
            return frozen
    return Path(__file__).resolve().parents[3] / "data" / "diagnostics" / "user_kb.json"


def _validate_entry(code: str, entry: dict[str, Any]) -> UserKbEntry:
    if not isinstance(entry, dict):
        raise UserKbError(f"{code}: entry must be an object")
    extra = set(entry) - _ENTRY_FIELDS
    if extra:
        raise UserKbError(f"{code}: unknown field(s): {sorted(extra)}")
    missing = _REQUIRED_FIELDS - set(entry)
    if missing:
        raise UserKbError(f"{code}: missing required field(s): {sorted(missing)}")
    for f in _REQUIRED_FIELDS - {"code"}:
        if not isinstance(entry[f], str) or not str(entry[f]).strip():
            raise UserKbError(f"{code}: field '{f}' must be a non-empty string")
    if entry["user_risk"] not in _VALID_RISKS:
        raise UserKbError(f"{code}: user_risk must be one of {sorted(_VALID_RISKS)}")
    return UserKbEntry(
        code=code,
        user_title_tr=entry["user_title_tr"],
        user_summary_tr=entry["user_summary_tr"],
        user_risk=entry["user_risk"],
        source_ref=entry["source_ref"],
    )


def load_user_kb(data_path: Path | None = None) -> dict[str, UserKbEntry]:
    """Load + validate the user KB (fail-closed; corrupt DB -> empty dict).

    A single invalid entry aborts the whole load — a partially trusted
    user-language KB must never be served (same policy as golden cases).
    """
    target = data_path or _resolve_kb_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("user_kb load failed: %s", exc)
        return {}
    if not isinstance(payload, dict):
        logger.warning("user_kb payload must be an object")
        return {}
    if payload.get("schema_version") != SCHEMA_VERSION:
        logger.warning("user_kb unsupported schema_version: %r", payload.get("schema_version"))
        return {}
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, dict):
        logger.warning("user_kb 'entries' must be an object")
        return {}
    out: dict[str, UserKbEntry] = {}
    for code, entry in sorted(raw_entries.items()):
        out[code] = _validate_entry(code, entry)
    return out


def get_entry(code: str, kb: dict[str, UserKbEntry] | None = None) -> UserKbEntry | None:
    """Look up one code (case-insensitive); None when not covered."""
    table = kb if kb is not None else load_user_kb()
    return table.get((code or "").strip().upper())


def _resolve_feedback_path() -> Path:
    """Resolve user feedback path, routing to persistent app_data_root when frozen."""
    if getattr(sys, "frozen", False):
        try:
            from src.launcher.paths import app_data_root

            return app_data_root() / "user_feedback.json"
        except Exception:
            try:
                from src.ui.desktop_app import _app_data_root

                return _app_data_root() / "user_feedback.json"
            except Exception:
                pass
    return _resolve_kb_path().parent / "user_feedback.json"


def record_operator_feedback(
    dtc: str,
    resolved: bool,
    notes: str = "",
    feedback_path: Path | None = None,
) -> dict[str, Any]:
    """Record technician resolution feedback ('çözdü / çözmedi') locally on-device (FAZ 3).

    Fully anonymous, on-device only; VINs are strictly rejected or masked.
    """
    import time

    from src.engine.ai.diagnostic_copilot import mask_vin_in_text

    target = feedback_path or _resolve_feedback_path()
    clean_code = (dtc or "").strip().upper()
    masked_notes = mask_vin_in_text(notes or "")

    record = {
        "timestamp_ns": time.monotonic_ns(),
        "dtc": clean_code,
        "resolved": bool(resolved),
        "notes": masked_notes,
    }

    with _FEEDBACK_LOCK:
        existing: list[dict[str, Any]] = []
        if target.is_file():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing = loaded
                else:
                    logger.warning("user_feedback payload is not a list — starting a new log")
            except (OSError, json.JSONDecodeError) as exc:
                # Narrowed (was bare `except Exception`): a corrupt feedback log
                # is reported, not silently discarded.
                logger.warning("user_feedback okunamadı, yeni kayıtla devam: %s", exc)
        existing.append(record)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, json.dumps(existing, ensure_ascii=False, indent=2))

    return {
        "status": "saved",
        "dtc": clean_code,
        "resolved": resolved,
        "total_records": len(existing),
    }


def _atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` via a same-directory temp file + atomic replace (P2-1).

    ``path_guard.atomic_write_text`` already implements exactly this dance but
    lives in ``src.engine.exporters``; the AI layer imports it LAZILY so the
    AI package keeps no top-level dependency on the exporters package.
    """
    try:
        from src.engine.exporters.path_guard import atomic_write_text

        write: Callable[[Path, str], Path] = atomic_write_text
    except Exception:  # noqa: BLE001 — exporters unavailable: degrade, never fail the write
        write = _fallback_atomic_write
    write(target, text)


def _fallback_atomic_write(target: Path, text: str) -> Path:
    """Local temp-file + ``os.replace`` fallback (same filesystem, atomic)."""
    import os
    import tempfile

    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def load_operator_feedback(feedback_path: Path | None = None) -> list[dict[str, Any]]:
    """Load local on-device resolution feedback records."""
    target = feedback_path or _resolve_feedback_path()
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("user_feedback okunamadı: %s", exc)
        return []


__all__ = [
    "UserKbEntry",
    "UserKbError",
    "load_user_kb",
    "get_entry",
    "record_operator_feedback",
    "load_operator_feedback",
    "SCHEMA_VERSION",
]

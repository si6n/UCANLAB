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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

logger = get_logger("engine.ai_user_kb")

SCHEMA_VERSION = 1

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

    existing: list[dict[str, Any]] = []
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    record = {
        "timestamp_ns": time.monotonic_ns(),
        "dtc": clean_code,
        "resolved": bool(resolved),
        "notes": masked_notes,
    }
    existing.append(record)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": "saved",
        "dtc": clean_code,
        "resolved": resolved,
        "total_records": len(existing),
    }


def load_operator_feedback(feedback_path: Path | None = None) -> list[dict[str, Any]]:
    """Load local on-device resolution feedback records."""
    target = feedback_path or _resolve_feedback_path()
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
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

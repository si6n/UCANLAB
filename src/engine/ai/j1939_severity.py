# -*- coding: utf-8 -*-
"""J1939 SPN/FMI severity resolution (P0-3, 2026-09-21 deep-discovery audit).

WHY THIS EXISTS
---------------
Two defects were measured at HEAD ``a6f7477``:

1. **SPN key normalisation.** The knowledge base stores J1939 entries as
   ``"SPN100"`` (no space) but a DM1 code naturally arrives as ``"SPN 100"``.
   The session path looked up the spaced form, missed, and silently degraded a
   genuine oil-pressure stop condition to a generic ``LOW``.

2. **FMI blindness.** ``data/diagnostics/j1939_spn_fmi_database.json`` already
   carries the CORRECT per-FMI rung — ``SPN_100`` FMI 3/4/5/6 (electrical)
   resolve to ``HIGH`` while FMI 0/1/2/16/18 (physical) resolve to
   ``CRITICAL_STOP``. The report ignored it and stamped every FMI of the SPN
   with the SPN-level rung, so ``SPN 100 FMI 3`` (an unplugged sensor) advised
   *"Motoru derhal durdurun"* just like a real loss of oil pressure.

This module reads the ALREADY-PRESENT per-FMI severity. It invents nothing: a
missing FMI recorded for the SPN returns ``None`` and the caller keeps its
SPN-level rung (fail-safe, never a fabricated downgrade).

ISOLATION (AGENTS.md 2.8)
-------------------------
Pure stdlib + `src.core.models.diagnostics` + the AI package — no HAL, no TX,
no network.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.core.models.diagnostics import Severity

#: SAE J1939-73 FMI classes. Electrical/communication faults do NOT stop an
#: engine; only physical/mechanical faults can.
_ELECTRICAL_FMIS: frozenset[int] = frozenset({3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14})
_PHYSICAL_FMIS: frozenset[int] = frozenset({0, 1, 2, 15, 16, 17, 18, 19, 20, 21, 31})

_SPN_PATTERN = re.compile(r"^\s*spn[\s_]*(\d+)\s*$", re.IGNORECASE)


def _spn_db_key(spn_number: int) -> str:
    """Return the SPN database's key form: ``SPN_<n>`` (underscore)."""
    return f"SPN_{spn_number}"


def _spn_db_path() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        frozen_root = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
        candidate = frozen_root / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
        if candidate.is_file():
            return candidate
    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "diagnostics"
        / "j1939_spn_fmi_database.json"
    )


@lru_cache(maxsize=1)
def load_spn_database() -> dict[str, Any]:
    """Load and cache the J1939 SPN/FMI database (empty dict on failure)."""
    try:
        payload = json.loads(_spn_db_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_spn_code(code: str) -> str | None:
    """Return the canonical ``SPN<n>`` key, or None when `code` is not an SPN.

    ``"SPN 100"``, ``"spn100"`` and ``"SPN_100"`` all normalise to ``"SPN100"``,
    which is the key the knowledge base and the SPN database actually use.
    """
    if not code or not isinstance(code, str):
        return None
    match = _SPN_PATTERN.match(code)
    if not match:
        return None
    # KB / copilot key form (no separator) -- used for EXPERT_KNOWLEDGE_BASE
    return f"SPN{int(match.group(1))}"


def is_electrical_fmi(fmi: int | None) -> bool:
    """True for a circuit/electrical FMI (3-14), which never stops an engine."""
    return isinstance(fmi, int) and fmi in _ELECTRICAL_FMIS


def _coerce_severity(raw: Any) -> Severity | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # The SPN database has some lowercase rungs (critical/medium/high) from its
    # scraper; map them case-insensitively so they are not silently dropped.
    upper = text.upper()
    if upper == "CRITICAL":
        upper = "CRITICAL_STOP"
    try:
        return Severity(upper)
    except ValueError:
        return None


def resolve_fmi_severity(spn_code: str, fmi: int | None) -> Severity | None:
    """Return the per-FMI severity recorded for an SPN, or None.

    ``None`` means "the database records nothing for this SPN/FMI pair" — the
    caller must keep its existing rung rather than inventing one.
    """
    normalized = normalize_spn_code(spn_code)
    if normalized is None or fmi is None:
        return None
    database = load_spn_database()
    spns = database.get("spns")
    if not isinstance(spns, dict):
        return None
    # The SPN database keys are `SPN_<n>` (underscore), unlike the KB's `SPN<n>`.
    record = spns.get(_spn_db_key(int(normalized[3:])))
    if not isinstance(record, dict):
        return None
    matrix = record.get("fault_matrix")
    if not isinstance(matrix, dict):
        return None
    entry = matrix.get(str(fmi))
    if not isinstance(entry, dict):
        return None
    return _coerce_severity(entry.get("severity"))


def resolve_spn_fault_title(spn_code: str, fmi: int | None) -> str | None:
    """Return the per-FMI fault title (TR preferred), or None.

    Used to describe the SPECIFIC failure mode instead of the SPN's generic
    text, so an FMI 3 report reads as a circuit fault rather than an
    oil-pressure-loss work-order.
    """
    normalized = normalize_spn_code(spn_code)
    if normalized is None or fmi is None:
        return None
    database = load_spn_database()
    spns = database.get("spns")
    if not isinstance(spns, dict):
        return None
    record = spns.get(_spn_db_key(int(normalized[3:])))
    if not isinstance(record, dict):
        return None
    matrix = record.get("fault_matrix")
    if not isinstance(matrix, dict):
        return None
    entry = matrix.get(str(fmi))
    if not isinstance(entry, dict):
        return None
    for key in ("fault_title", "fault_title_tr", "fmi_name"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def spn_database_severity_counts() -> dict[str, int]:
    """Rung histogram over every SPN/FMI entry (used by the regression test)."""
    counts: dict[str, int] = {}
    database = load_spn_database()
    spns = database.get("spns")
    if not isinstance(spns, dict):
        return counts
    for record in spns.values():
        if not isinstance(record, dict):
            continue
        matrix = record.get("fault_matrix")
        if not isinstance(matrix, dict):
            continue
        for entry in matrix.values():
            if not isinstance(entry, dict):
                continue
            severity = _coerce_severity(entry.get("severity"))
            key = severity.value if severity else "UNRESOLVED"
            counts[key] = counts.get(key, 0) + 1
    return counts

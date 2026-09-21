# -*- coding: utf-8 -*-
"""Deterministic DTC severity resolver (P0-1, 2026-09-21 deep-discovery finding).

WHY THIS EXISTS
---------------
`data/diagnostics/dtc_database.json` carried a `severity` field that was
inherited from whatever scraped source page happened to use the word
"critical". The measured result (verified at HEAD `a6f7477`):

* ``P0301``-``P0312`` (single-cylinder misfire) = ``CRITICAL_STOP`` while
  ``P0300`` (random / multiple misfire, the MORE severe case) = ``MEDIUM``.
* Wiper-relay, warning-lamp, aux-heater and trailer-brake codes = ``CRITICAL_STOP``.

Severity is not cosmetic: ``drive_safety_policy.decide_risk`` maps
``CRITICAL_STOP`` -> ``RED``, whose fixed advice is *"Aracı güvenli bir şekilde
durdurun. Motoru kapatın..."*. A comfort code scrawled as critical therefore
tells a driver to stop a healthy vehicle.

The fix is a SOURCE-INDEPENDENT rule table
(`data/diagnostics/dtc_severity_rules.json`): a code's rung is a function of
SAE J2012 *which subsystem* x *which failure mode*, never of scraped prose.

DESIGN INVARIANTS (AGENTS.md 2.3 — No Fabricated Telemetry)
-----------------------------------------------------------
1. A code that matches no rule resolves to ``UNKNOWN`` — never a convenient
   default. ``UNKNOWN`` -> ``GRAY`` in ``decide_risk`` (never GREEN), so the
   unknown case fails safe rather than fabricating a benign or alarmist rung.
2. ``CRITICAL_STOP`` is a CLOSED allowlist. The regression test fails if any
   code outside it is ever stamped with that rung.
3. Subsystem caps are hard: a ``B`` (body) code can never exceed ``LOW``.

ISOLATION (AGENTS.md 2.8 / tests/safety/test_ai_tx_isolation.py)
----------------------------------------------------------------
Pure stdlib + `src.core.models.diagnostics` — no HAL, no TX, no network.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.core.models.diagnostics import Severity

#: Ordered most-severe-first. The first family whose ceiling is reached wins.
_SEVERITY_ORDER: tuple[str, ...] = (
    "INFO",
    "LOW",
    "MEDIUM",
    "HIGH",
    "CRITICAL_STOP",
)

UNKNOWN = "UNKNOWN"


def _rules_path() -> Path:
    """Resolve the rule table, preferring a frozen PyInstaller bundle (H-9)."""
    import sys

    if getattr(sys, "frozen", False):
        frozen_root = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
        candidate = frozen_root / "data" / "diagnostics" / "dtc_severity_rules.json"
        if candidate.is_file():
            return candidate
    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "diagnostics"
        / "dtc_severity_rules.json"
    )


@lru_cache(maxsize=1)
def load_severity_rules() -> dict[str, Any]:
    """Load and cache the rule table.

    A missing / malformed table returns an EMPTY rule set, which makes every
    lookup resolve to ``UNKNOWN``. That is deliberate fail-safe behaviour: it
    never fabricates a rung, and the audit test asserts the file is present so
    a missing table is caught in CI rather than silently degrading.
    """
    try:
        payload = json.loads(_rules_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _cap_index(severity: str) -> int:
    try:
        return _SEVERITY_ORDER.index(severity)
    except ValueError:
        return len(_SEVERITY_ORDER) - 1


def _apply_cap(severity: str, cap: str | None) -> str:
    """Clamp `severity` so it never exceeds `cap`."""
    if cap is None:
        return severity
    if _cap_index(severity) > _cap_index(cap):
        return cap
    return severity


def _system_letter(code: str) -> str:
    return code[:1].upper() if code else ""


def _match_title_pattern(title: str, patterns: list[str]) -> str | None:
    """Return the canonical pattern key that first matches `title`.

    Matching is case-insensitive and anchored on word boundaries where the
    pattern is a single token; compound patterns are used as regular
    expressions so `fuel rail pressure.*(high|low)` works as written.
    """
    lowered = title.lower()
    for pattern in patterns:
        try:
            if re.search(pattern, lowered):
                return pattern
        except re.error:
            # A malformed pattern in the table must not crash a diagnostic
            # path — skip it (the audit test asserts patterns compile).
            continue
    return None


def _fault_type_for(title: str, rules: dict[str, Any]) -> str | None:
    """Map a DTC title to its most-severe fault-type rung, or None."""
    patterns = rules.get("fault_type_patterns") or {}
    for rung in _SEVERITY_ORDER:
        rung_patterns = patterns.get(rung)
        if isinstance(rung_patterns, list) and _match_title_pattern(title, rung_patterns):
            return rung
    return None


def _misfire_severity(code: str, rules: dict[str, Any]) -> str | None:
    """P0300-P0312 misfire classification (finding P0-1)."""
    table = rules.get("misfire_rule") or {}
    single = table.get("single_cylinder") or {}
    rng = single.get("range")
    if isinstance(rng, list) and len(rng) == 2:
        if rng[0] <= code <= rng[1]:
            return str(single.get("severity", UNKNOWN))
    random_multiple = table.get("random_multiple") or {}
    if code == random_multiple.get("exact"):
        return str(random_multiple.get("severity", UNKNOWN))
    return None


def _in_critical_allowlist(code: str, rules: dict[str, Any]) -> bool:
    allow = rules.get("critical_stop_allowlist") or {}
    exact = allow.get("exact_codes") or []
    if code in exact:
        return True
    for family in allow.get("family_rules") or []:
        if not isinstance(family, dict):
            continue
        prefix = family.get("code_prefix")
        if prefix and code.startswith(str(prefix)):
            # A family rule that DOWNGRADES (e.g. U0 -> HIGH) is not an allow.
            decision = family.get("decision")
            if decision and decision != "CRITICAL_STOP":
                return False
            return True
    return False


def is_obd_code(code: str) -> bool:
    """True when `code` is an OBD-II / SAE J2012 code this table governs.

    This table models OBD-II ``P/C/B/U`` codes only. J1939 SPN keys
    (``"SPN100"``) and proprietary/unknown keys carry their OWN severity
    semantics (the SPN database + FMI), so the reconciliation must SKIP them —
    applying an OBD-II fault-mode pattern to ``"SPN100"`` would downgrade a
    genuine oil-pressure stop condition to HIGH.

    The numeric body is HEX, not decimal: real SAE codes include ``C003F``,
    ``U010A`` and ``P1A2B``. Requiring digits rejected them and silently
    resolved valid chassis/network codes to UNKNOWN.
    """
    if not code or not isinstance(code, str):
        return False
    stripped = code.strip().upper()
    if len(stripped) < 3:
        return False
    if stripped[0] not in {"P", "C", "B", "U"}:
        return False
    return all(ch in "0123456789ABCDEF" for ch in stripped[1:])


def resolve_severity(code: str, title: str = "", subsystem: str = "") -> Severity:
    """Resolve the severity rung for one DTC, source-independently.

    `code` is the SAE code (``"P0312"``); `title`/`subsystem` are the scraped
    human text, used only for fault-mode classification. An unrecognisable
    code returns ``Severity.UNKNOWN`` (never an invented rung).

    A non-OBD key (``"SPN100"``) also returns ``UNKNOWN``: this table has no
    authority over J1939 SPN severity, and guessing would be fabrication.
    """
    if not code or not isinstance(code, str):
        return Severity.UNKNOWN
    if not is_obd_code(code):
        return Severity.UNKNOWN
    rules = load_severity_rules()
    if not rules:
        return Severity.UNKNOWN

    normalized = code.strip().upper()

    # 1. Misfire family has an explicit, evidence-backed rule.
    misfire = _misfire_severity(normalized, rules)
    if misfire is not None:
        return _coerce(misfire)

    # 2. Fault-mode classification from the title.
    fault_rung = _fault_type_for(title or "", rules)

    # 3. The critical allowlist is the ONLY path to CRITICAL_STOP, and an
    #    EXACT allowlisted code is authoritative: losing comms with the
    #    engine/transmission controller (U0100) removes engine control even
    #    when its scraped title does not happen to say "critical".
    allow = rules.get("critical_stop_allowlist") or {}
    if normalized in (allow.get("exact_codes") or []):
        return _coerce("CRITICAL_STOP")

    critical_ok = _in_critical_allowlist(normalized, rules)
    if fault_rung == "CRITICAL_STOP" and not critical_ok:
        # A severe-sounding title on a non-allowlisted code is capped at HIGH.
        fault_rung = "HIGH"
    if fault_rung == "CRITICAL_STOP":
        return _coerce("CRITICAL_STOP")

    # 4. Family rule decision (e.g. U01 -> stop, U0 -> HIGH).
    family_decision = _family_decision(normalized, rules)
    if family_decision is not None:
        return _coerce(family_decision)

    # 5. Structural SAE title grammar fallback (covers the long tail without
    #    an unbounded keyword list). Only consulted when nothing explicit hit.
    if fault_rung is None:
        fault_rung = _structural_fault_mode(title or "", rules)

    # 6. Subsystem cap + fault-mode rung, whichever is more restrictive.
    caps = rules.get("subsystem_caps") or {}
    cap = caps.get(_system_letter(normalized))
    candidate = fault_rung or UNKNOWN
    return _coerce(_apply_cap(candidate, str(cap) if cap else None))


def _structural_fault_mode(title: str, rules: dict[str, Any]) -> str | None:
    """Classify a title by SAE J2012's standard suffix grammar.

    Returns the rung of the first structural pattern that matches, or None.
    """
    modes = rules.get("structural_fault_modes") or {}
    if not title:
        return None
    for rung in _SEVERITY_ORDER:
        patterns = modes.get(rung)
        if isinstance(patterns, list) and _match_title_pattern(title, patterns):
            return rung
    return None


def _family_decision(code: str, rules: dict[str, Any]) -> str | None:
    allow = rules.get("critical_stop_allowlist") or {}
    for family in allow.get("family_rules") or []:
        if not isinstance(family, dict):
            continue
        prefix = family.get("code_prefix")
        if prefix and code.startswith(str(prefix)):
            decision = family.get("decision")
            if decision:
                return str(decision)
    return None


def _coerce(rung: str) -> Severity:
    """Convert a rule rung to the allowlisted ``Severity`` enum.

    A rung outside the allowlist resolves to ``UNKNOWN`` — the enum raises on
    unknown members, and a diagnostic path must not crash on a data typo.
    """
    try:
        return Severity(rung)
    except ValueError:
        return Severity.UNKNOWN


def critical_stop_allowlist() -> frozenset[str]:
    """Exact codes the rule table permits to be CRITICAL_STOP (for tests/UI)."""
    rules = load_severity_rules()
    allow = rules.get("critical_stop_allowlist") or {}
    return frozenset(allow.get("exact_codes") or [])


def audit_database_severities(
    database: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Return ``(code, current, proposed)`` for every record the table disagrees with.

    Non-mutating. Used by the data-integrity regression test and by the
    rebuild tool; keeping it pure means the test can assert the database
    matches the table without editing it.
    """
    mismatches: list[tuple[str, str, str]] = []
    for code, record in database.items():
        if not isinstance(record, dict):
            continue
        current = str(record.get("severity") or UNKNOWN)
        proposed = resolve_severity(
            code,
            str(record.get("title") or ""),
            str(record.get("subsystem") or ""),
        ).value
        if current != proposed:
            mismatches.append((code, current, proposed))
    return mismatches

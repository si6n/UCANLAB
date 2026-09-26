"""AI Diagnostic Copilot & Automated Telemetry Intelligence Engine.

Provides multi-domain root-cause analysis, dynamic fault correlation, offline Causal Bayesian
inference, Turkish/English automotive NLP tokenization, and deterministic expert analysis.

FULLY OFFLINE (operator decision, M7): the cloud-LLM narration layer (Gemini/
OpenAI, llm_narrator.py) was removed. This module is the entire AI: a
deterministic evidence engine. Severity and action triggers originate here
only. Offline guarantees: same input -> same output, no network calls, no
API keys, no data leaves the host.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, cast

from src.core.logging import get_logger
from src.engine.ai.calibration import compute_calibration_factor
from src.engine.ai.j1939_severity import (
    normalize_spn_code as _normalize_spn_code,
)
from src.engine.ai.j1939_severity import (
    resolve_fmi_severity as _resolve_fmi_severity,
)
from src.engine.ai.j1939_severity import (
    resolve_spn_fault_title as _resolve_fmi_fault_title,
)

# T1-1 (calibration wiring): ``compute_calibration_factor`` above provides the
# memoised golden-set calibration factor that damps root-cause confidence. It is
# imported from the module directly (not via ``src.engine.ai``), because the
# package ``__init__`` re-exports THIS module — going through it would re-enter
# ``ai/__init__`` and deadlock. ``calibration`` never imports this module, so the
# direct edge is acyclic. Offline, deterministic, no network.
logger = get_logger("engine.ai_copilot")

# REVIEW 4.2: re.IGNORECASE — a lowercase VIN (e.g. "1hgcr2f83ha123456")
# previously slipped through unmasked and leaked into logs/session summaries/
# exported reports, violating the P1 Data Model privacy rule.
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)

# T74 (measured defect): the SPN/FMI extractors demanded whitespace between the
# token and the number (`\bspn\s*([0-9]+)\b`), but `normalize_text` turns `:` into
# a SPACE while KEEPING `_` and `-` (its clean-up class is `[^\w\s\-\.]`, and `\w`
# includes the underscore). An operator writing the compact form the DM1 display
# shows — `SPN100FMI3`, `SPN 100FMI 3`, `SPN_100_FMI_3` — therefore produced
# "spn100fmi3" / "spn 100fmi 3", the SPN regex found nothing, and the query fell
# through to the J1939 keyword router, which answered a DIFFERENT fault entirely
# (`SPN4364` SCR efficiency, CRITICAL_STOP) for every one of those forms.
# Measured before the fix: `SPN100FMI3`, `SPN157FMI3`, `SPN 100FMI 3`,
# `SPN:100FMI:3`, `SPN_100_FMI_3`, `SPN100FMI03` -> all answered `SPN4364`.
# Two changes, both required by the same measurement:
#   * the separator class is exactly what `normalize_text` can leave between the
#     token and the digits (`[\s_\-\.]*`), so no emitted form is missed;
#   * the trailing `\b` is gone — in the compact form the digits are followed by
#     the NEXT token's letters ("100fmi"), and `\b` fails between two word
#     characters, which is precisely why the run never matched. The digit run is
#     still greedy, so `SPN1004` stays 1004 and never truncates to 100.
# The leading `\b` stays: "xspn100" is not an SPN.
_SPN_QUERY_RE = re.compile(r"\bspn[\s_\-\.]*([0-9]+)")

#: FMI is matched with the SAME separator tolerance, so a query that names its
#: failure mode without a space (`SPN100FMI3`) still resolves the per-FMI rung.
#: The left anchor is a negative LOOKBEHIND for a letter rather than `\b`, for
#: the same reason as above: in "spn100fmi3" the character before "fmi" is a
#: digit, so `\b` cannot match and the compact form would be invisible again.
#: `{1,2}` is kept (FMI is 0-31 by SAE J1939-73) with a `(?![0-9])` guard so a
#: 3-digit run is never truncated into a valid-looking FMI.
#: Optional "no"/"numarasi"/"numarali"/"#" wording is accepted after the token
#: because it names the same number the operator means; when no digits follow,
#: nothing is inferred and the FMI stays absent (fail-safe, unchanged).
_FMI_QUERY_RE = re.compile(
    r"(?<![a-z])fmi[\s_\-\.:=]*(?:no|numarasi|numarali|#)?[\s_\-\.:=]*([0-9]{1,2})(?![0-9])"
)


def query_spn_number(norm_query: str) -> int | None:
    """The SPN number a query names outright, or ``None``.

    T74: one extractor for the whole module. Returns the INT so a leading zero
    ("SPN 0100") and the plain form resolve the same record — the DB keys are
    ``SPN_<int>``. ``None`` means the query names no SPN; callers keep their
    existing behaviour rather than guessing one.
    """
    match = _SPN_QUERY_RE.search(str(norm_query or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def query_fmi_number(norm_query: str) -> int | None:
    """The FMI number a query names outright, or ``None``.

    T74: shared by the router (which threads it through ``telemetry`` as
    ``_query_fmi``) and by the J1939 report body (which re-extracts it from the
    same normalised query), so both agree by construction. The DB keys
    ``fault_matrix`` / ``fmi_definitions`` / ``fmi_map`` by UNPADDED decimal, so
    "FMI03" canonicalises to 3 instead of silently missing the recorded rung.
    ``None`` means no FMI was named — never a fabricated one.
    """
    match = _FMI_QUERY_RE.search(str(norm_query or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def mask_vin_in_text(text: str) -> str:
    """Mask all but the last 6 chars of any 17-char VIN in free text."""

    def _repl(m: re.Match[str]) -> str:
        vin = m.group(0)
        return "*" * 11 + vin[-6:]

    return _VIN_RE.sub(_repl, str(text))


class FaultSeverity(Enum):
    """AI Risk and Urgency Assessment."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    # Restored rung: 379 external-DB DTC records carry severity HIGH and the
    # drive-safety policy already maps HIGH->RED. Dropping the member
    # silently demoted them to MEDIUM via the parse fallback.
    HIGH = "HIGH"
    CRITICAL_STOP = "CRITICAL_STOP"


def map_severity_or_default(raw: Any) -> FaultSeverity:
    """Parse a severity string from an LLM response without raising (L-9).

    An LLM may emit an unmodeled severity word — failing the entire
    analysis on enum conversion would discard a otherwise valid report, so
    unknown values fall back to MEDIUM (visible urgency, not silent).
    """
    try:
        return FaultSeverity(str(raw))
    except ValueError:
        return FaultSeverity.MEDIUM


@dataclass(slots=True)
class TroubleshootingStep:
    """Actionable step recommended by the AI."""

    step_number: int
    action: str
    target_component: str
    difficulty: str  # "Kolay (Görsel)" | "Orta (Alet Gerekir)" | "İleri (Servis)"


@dataclass(slots=True)
class DiagnosticAnalysisReport:
    """Comprehensive AI-generated diagnostic analysis."""

    summary: str
    severity: FaultSeverity
    root_cause_probability: str
    likely_causes: list[str]
    troubleshooting_steps: list[TroubleshootingStep]
    affected_subsystems: list[str]
    raw_dtc_count: int
    telemetry_correlations: list[str]
    # P1-1: hypothesis lines are CONCLUSIONS, not measured telemetry. They
    # live in their own field so no consumer can mistake them for evidence
    # (``user_report_composer`` reads only ``telemetry_correlations``).
    hypothesis_candidates: list[str] = field(default_factory=list)
    # T70-D: the component view of `root_cause_probability`. The label is a
    # single opaque string; this carries the same numbers it was built from so
    # a consumer can show WHICH evidence kind carries the score. Empty for a
    # session with no active DTC (no score to explain).
    confidence_breakdown: dict[str, Any] = field(default_factory=dict)
    ai_model_used: str = "Yerel Otomotiv Uzman Motoru (Çevrimdışı)"
    timestamp_ns: int = field(default_factory=time.time_ns)


@dataclass(slots=True)
class CopilotActionTrigger:
    """Structured actionable diagnostic routine trigger metadata for UI buttons."""

    id: str
    label: str
    action_type: str  # "uds_clear_dtc", "uds_read_did", "uds_session_control", "uds_routine", "uds_ecu_reset", "j1939_clear_dtc", "j1939_dm1_query"
    params: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = True
    confirm_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "action_type": self.action_type,
            "params": self.params,
            "requires_confirmation": self.requires_confirmation,
            "confirm_text": self.confirm_text,
        }


def make_uds_clear_dtc_action(group: int = 0xFFFFFF) -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_uds_0x14_clear_dtc",
        label="▶️ UDS 0x14 DTC Temizle",
        action_type="uds_clear_dtc",
        params={"group": group},
        requires_confirmation=True,
        confirm_text="Aktif ve geçmiş tüm DTC arıza kodları ECU hafızasından silinecektir. Devam edilsin mi?",
    ).to_dict()


def make_uds_read_vin_action() -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_uds_0x22_f190_vin",
        label="▶️ UDS 0x22 F190 VIN Oku",
        action_type="uds_read_did",
        params={"did": 0xF190, "name": "VIN"},
        requires_confirmation=False,
    ).to_dict()


def make_uds_session_action(session_type: int = 3) -> dict[str, Any]:
    session_names = {1: "Default", 2: "Programming", 3: "Extended", 4: "Safety"}
    s_name = session_names.get(session_type, hex(session_type))
    return CopilotActionTrigger(
        id=f"act_uds_0x10_session_{session_type}",
        label=f"▶️ UDS 0x10 {s_name} Session",
        action_type="uds_session_control",
        params={"session_type": session_type},
        requires_confirmation=True,
        confirm_text=f"Teşhis oturumu '{s_name} (0x{session_type:02X})' moduna geçirilecektir. Onaylıyor musunuz?",
    ).to_dict()


def make_uds_routine_action(routine_id: int, name: str = "") -> dict[str, Any]:
    lbl = f"▶️ UDS 0x31 Rutin (0x{routine_id:04X})" if not name else f"▶️ UDS 0x31 {name}"
    return CopilotActionTrigger(
        id=f"act_uds_0x31_routine_{routine_id:04x}",
        label=lbl,
        action_type="uds_routine",
        params={"routine_id": routine_id, "name": name},
        requires_confirmation=True,
        confirm_text=f"0x{routine_id:04X} nolu diagnostik rutin çalıştırılacaktır. Onaylıyor musunuz?",
    ).to_dict()


def make_uds_ecu_reset_action(reset_type: int = 1) -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_uds_0x11_ecu_reset",
        label="▶️ UDS 0x11 ECU Reset",
        action_type="uds_ecu_reset",
        params={"reset_type": reset_type},
        requires_confirmation=True,
        confirm_text="ECU donanımsal olarak yeniden başlatılacaktır (Hard Reset). Onaylıyor musunuz?",
    ).to_dict()


def make_j1939_dm11_action() -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_j1939_dm11_clear",
        label="▶️ J1939 DM11 Arıza Temizle",
        action_type="j1939_clear_dtc",
        params={"pgn": 65235},
        requires_confirmation=True,
        confirm_text="Ağır vasıta J1939 aktif arıza kayıtları (DM11 PGN 65235) silinecektir. Onaylıyor musunuz?",
    ).to_dict()


def make_j1939_dm1_action() -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_j1939_dm1_query",
        label="▶️ J1939 DM1 Arıza Oku",
        action_type="j1939_dm1_query",
        params={"pgn": 65226},
        requires_confirmation=False,
    ).to_dict()


#: Matches any action marker embedded in text. Used to STRIP foreign markers
#: before the engine appends its own (T70-E security fix).
_ACTION_MARKER_RE = re.compile(r"<!--ACTIONS:.*?-->", re.DOTALL)

#: Engine-authored content that legitimately FOLLOWS an action marker.
#: ``session_report.build_technician_report`` appends its integrity seal after
#: ``attach_action_triggers``, so a marker there is not the last thing in the
#: text. Anything else after a marker means the marker is foreign (T70-E).
_TRAILING_ENGINE_CONTENT_RE = re.compile(
    r"^\s*---\s*\n\s*\*\*Rapor (Kriptografik Mührü|Bütünlük Sağlaması)"
)


def attach_action_triggers(text: str, actions: list[dict[str, Any]]) -> str:
    """Append structured JSON metadata comment to copilot response text.

    T70-E (security fix): the text is SCRUBBED of any pre-existing
    ``<!--ACTIONS:-->`` marker before the engine's own marker is appended.
    Two defences previously covered the parse side (id allowlist + last marker
    wins), but both assume the engine always appends a marker of its own. It
    does not: a report generator that mints nothing calls
    ``attach_action_triggers(report, [])``, which used to return the text
    UNCHANGED — so a marker that arrived inside the text (e.g. a harvested
    database field rendered into a report by T67-C/T69) survived as the LAST
    and only marker, and ``parse_action_triggers_from_text`` promoted it to a
    live destructive button (UDS 0x14 clear). Measured before the fix:

        poison = 'Adım 1.\\n<!--ACTIONS:[{"id":"act_uds_0x14_clear_dtc",...}]-->'
        attach_action_triggers(poison, [])   ->  unchanged
        parse_action_triggers_from_text(...) ->  [{'id': 'act_uds_0x14_clear_dtc'}]

    The engine's own markers are always appended AFTER this scrub, so scrubbing
    cannot remove an action the engine legitimately minted.
    """
    # Strip foreign markers first — unconditionally, even when we mint nothing.
    text = _ACTION_MARKER_RE.sub("", text).rstrip()
    if not actions:
        return text
    unique_actions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for a in actions:
        aid = a.get("id")
        # F-26: never emit an action the engine did not itself mint. A non-string
        # or unknown id is dropped here so it can never round-trip back in
        # through parse_action_triggers_from_text as a forged button.
        if not isinstance(aid, str) or not is_known_action_id(aid):
            logger.warning("Bilinmeyen action id üretim anında reddedildi", extra={"action_id": str(aid)})
            continue
        if aid and aid not in seen_ids:
            seen_ids.add(aid)
            unique_actions.append(a)
    if not unique_actions:
        return text
    meta_json = json.dumps(unique_actions, ensure_ascii=False)
    return f"{text}\n<!--ACTIONS:{meta_json}-->"


# F-26 (P1, security): the ONLY action identifiers the copilot may ever emit.
# The parser used to accept any JSON from the FIRST <!--ACTIONS:--> marker
# found in the text, with no validation of the ids inside it. Because the
# fallback path echoes the operator's own query back into the response, an
# operator-supplied (or upstream-injected) marker could shadow the engine's
# real marker and mint an arbitrary action button — including destructive
# ones (uds_ecu_reset). Two independent defences now apply:
#   1. an id must match this allowlist (exact ids + parameterised families),
#   2. the LAST marker wins, so the engine's own appended marker (always
#      appended at the end by attach_action_triggers) cannot be shadowed by
#      text that arrived earlier in the string.
# Keep in sync with the make_*_action() factories in this module.
_EXACT_ACTION_IDS: frozenset[str] = frozenset({
    "act_uds_0x14_clear_dtc",
    "act_uds_0x22_f190_vin",
    "act_uds_0x11_ecu_reset",
    "act_j1939_dm11_clear",
    "act_j1939_dm1_query",
})

_ACTION_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^act_uds_0x10_session_[1-4]$"),
    re.compile(r"^act_uds_0x31_routine_[0-9a-f]{4}$"),
)

# Action types the engine is allowed to expose, keyed by the id family they
# belong to. A valid id paired with a DIFFERENT type is still rejected: the
# type is what the UI dispatches on, so id and type must agree (F-26). Without
# this pairing check, a forged marker could keep a benign-looking id
# ("act_uds_0x14_clear_dtc") while swapping in a destructive type
# ("uds_ecu_reset") and the UI would dispatch the reset.
_ALLOWED_ACTION_TYPES: frozenset[str] = frozenset({
    "uds_clear_dtc",
    "uds_read_did",
    "uds_session_control",
    "uds_routine",
    "uds_ecu_reset",
    "j1939_clear_dtc",
    "j1939_dm1_query",
})

# id -> the single action_type that id is permitted to carry.
_ACTION_ID_TO_TYPE: dict[str, str] = {
    "act_uds_0x14_clear_dtc": "uds_clear_dtc",
    "act_uds_0x22_f190_vin": "uds_read_did",
    "act_uds_0x11_ecu_reset": "uds_ecu_reset",
    "act_j1939_dm11_clear": "j1939_clear_dtc",
    "act_j1939_dm1_query": "j1939_dm1_query",
}

# Parameterised families: (id pattern, fixed action_type).
_ACTION_FAMILY_TO_TYPE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^act_uds_0x10_session_[1-4]$"), "uds_session_control"),
    (re.compile(r"^act_uds_0x31_routine_[0-9a-f]{4}$"), "uds_routine"),
)


def is_known_action_id(action_id: Any) -> bool:
    """True when ``action_id`` is one the engine is allowed to mint (F-26)."""
    if not isinstance(action_id, str) or not action_id:
        return False
    if action_id in _EXACT_ACTION_IDS:
        return True
    return any(p.match(action_id) for p in _ACTION_ID_PATTERNS)


def _expected_action_type(action_id: str) -> str | None:
    """The one action_type ``action_id`` is allowed to carry, else None."""
    if action_id in _ACTION_ID_TO_TYPE:
        return _ACTION_ID_TO_TYPE[action_id]
    for pattern, expected in _ACTION_FAMILY_TO_TYPE:
        if pattern.match(action_id):
            return expected
    return None


def _filter_allowed_actions(raw: Any) -> list[dict[str, Any]]:
    """Keep only well-formed, allowlisted action dicts (fail-closed).

    An entry survives only when its id is known AND its action_type is exactly
    the one that id is permitted to carry. This blocks the "valid id, swapped
    type" forgery that a type-only membership check would let through.
    """
    if not isinstance(raw, list):
        return []
    allowed: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        aid = item.get("id")
        if not is_known_action_id(aid):
            logger.warning("İzinsiz action id reddedildi", extra={"action_id": str(aid)})
            continue
        expected = _expected_action_type(aid)
        if item.get("action_type") != expected:
            logger.warning(
                "action id/type uyuşmazlığı reddedildi",
                extra={"action_id": str(aid), "action_type": str(item.get("action_type"))},
            )
            continue
        allowed.append(item)
    return allowed


def parse_action_triggers_from_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse out structured action triggers from text comment, or extract if not found.

    F-26: uses the LAST ``<!--ACTIONS:-->`` marker (the engine always appends
    its own marker last via ``attach_action_triggers``) and validates every id
    and action_type against a closed allowlist. Anything off-list is dropped
    rather than surfaced as a button, so operator/injected text can never mint
    an action the engine did not authorise.

    T70-E (defense in depth): the last-marker rule alone is insufficient when
    the engine mints NOTHING — ``attach_action_triggers`` then appends no marker,
    so a foreign marker inside the text becomes the last (and only) marker. The
    parser therefore additionally requires the accepted marker to sit at the
    VERY END of the text, which is where ``attach_action_triggers`` always puts
    its own. A marker followed by visible content is foreign and is stripped
    without being promoted to a button.
    """
    matches = list(re.finditer(r"<!--ACTIONS:(.*?)-->", text, re.DOTALL))
    if matches:
        match = matches[-1]  # last-marker rule: the engine's own marker wins
        # T70-E: the engine's marker is normally appended at the very end. The
        # one engine-authored exception is the technician report's integrity
        # seal, which is added after the marker; anything ELSE after a marker
        # means the marker is foreign, so it is stripped without being promoted.
        _tail = text[match.end():]
        if _tail.strip() and not _TRAILING_ENGINE_CONTENT_RE.match(_tail):
            # Foreign marker: strip it and fall through to text extraction.
            clean = _ACTION_MARKER_RE.sub("", text).strip()
            return clean, extract_action_triggers(clean)
        clean_text = text[: match.start()].rstrip() + text[match.end() :]
        # Strip any earlier (shadowing) marker text so it cannot leak into the UI.
        clean_text = re.sub(r"<!--ACTIONS:.*?-->", "", clean_text, flags=re.DOTALL).strip()
        try:
            actions = json.loads(match.group(1).strip())
            allowed = _filter_allowed_actions(actions)
            if isinstance(actions, list):
                # A well-formed marker is authoritative even if every id inside
                # was rejected: fall back to text extraction only when the
                # payload itself is not a JSON list.
                if allowed:
                    return clean_text, allowed
                return clean_text, extract_action_triggers(clean_text)
        except json.JSONDecodeError as exc:
            # A3-6: a malformed/manually-edited <!--ACTIONS:--> payload used to
            # vanish silently and drop every structured action button. Narrow
            # to JSONDecodeError so real bugs still propagate, and log so the
            # corruption is observable (fail-open -> fail-loud).
            logger.warning(
                "bozuk <!--ACTIONS:--> JSON yok sayıldı, serbest-metin taramasına düşülüyor",
                extra={"error": str(exc)},
            )
    return text, extract_action_triggers(text)


# P1 fix: context-aware routine identifier patterns. The legacy scanner
# grabbed the FIRST 4-digit hex anywhere in the text, so a query like
# "CAN ID 0x18F0 rutin" produced a bogus routine button. Order: tight
# "routine/rutin id 0xNNNN" forms, then a bounded window after the keyword,
# then raw request bytes "0x31 0xNNNN", then bare hex after the keyword.
_ROUTINE_ID_PATTERNS: tuple[str, ...] = (
    r"(?:routine|rutin|rid)\s*(?:id)?\s*[:=]?\s*0x([0-9a-f]{4})\b",
    r"(?:routine|rutin)\s+(?:id)?\s*(?:0x)?([0-9a-f]{4})\b",
    r"(?:routine|rutin)[^0-9a-f]{0,30}?0x([0-9a-f]{4})\b",
    r"0x31\s+0x([0-9a-f]{4})\b",
)


def _extract_routine_id(combined_text: str) -> int | None:
    """Extract the routine identifier from lowered copilot text, honestly.

    P0-2 (AGENTS.md §2.3 no-fabrication): this function used to return the
    OEM constant ``0xD001`` ("HVIL Interlock Loopback") whenever the text
    stated NO routine identifier — i.e. it INVENTED a diagnostic routine id
    and minted a mutating UDS 0x31 action from it. A routine that the
    operator never named is not evidence, so the honest result is ``None``
    and the caller skips the action entirely. A routine id is only ever
    returned when the text states one explicitly.
    """
    for pattern in _ROUTINE_ID_PATTERNS:
        m = re.search(pattern, combined_text)
        if m:
            return int(m.group(1), 16)
    return None


def extract_action_triggers(text: str, user_query: str = "") -> list[dict[str, Any]]:
    """Mint actionable diagnostic recommendations from OPERATOR input only.

    AGENTS.md §2.8: ``CopilotActionTrigger`` may be minted ONLY from operator
    input or deterministic DTC mappings. The AI layer is read-only evidence
    with respect to the vehicle, and a stale latency window exists between
    this scan and any executor click, so ANY protocol mention in the model's
    own narrative (``text``) must never be promoted to an executable mutating
    button. Only ``user_query`` — the operator's own words, captured in the
    same turn — is scanned, and this function mints READ-ONLY triggers only
    (VIN/DID read, J1939 DM1 query).

    P0-1 remediation (no-fabrication / action-trigger provenance): mutating
    branches were deleted (UDS 0x14 DTC clear, UDS 0x10 0x03 session
    control, UDS 0x31 routine — including the bare ``"0x10"`` substring
    match — and the J1939 DM11 clear). This is the whitelist decision
    recommended by the marshal review: the engine is advisory read-only.

    ``text`` is retained in the signature for bridge/API compatibility. It is
    deliberately NOT scanned for the reasons above.
    """
    del text  # P0-1: model/foreign narrative is never scanned for actionable commands.
    combined = (user_query or "").strip().lower()
    actions: list[dict[str, Any]] = []
    seen_types: set[str] = set()

    if any(k in combined for k in ["f190", "vin oku", "şasi no", "read vin", "chassis number"]):
        actions.append(make_uds_read_vin_action())
        seen_types.add("uds_read_did")

    if "dm1" in combined or "pgn 65226" in combined:
        if "j1939_dm1_query" not in seen_types:
            actions.append(make_j1939_dm1_action())
            seen_types.add("j1939_dm1_query")

    return actions


def explain_traffic_metrics(bus_metrics: dict[str, Any], user_query: str = "") -> str:
    """Generate concise 2-3 line CAN traffic and anomaly diagnostic report."""
    bus_load = bus_metrics.get("bus_load_percent", 0)
    error_count = bus_metrics.get("error_count", 0)
    anomalies = list(bus_metrics.get("anomalies", []))
    total_pkts = bus_metrics.get("total_packets", 0)
    babbling = bus_metrics.get("babbling_node")
    if babbling and not any(str(babbling) in a for a in anomalies):
        anomalies.append(f"Babbling Node: {babbling}")

    # F-25 (P0, fail-open): with no bus telemetry every counter reads its
    # default 0/%0, which used to fall into the "else" branch and print
    # "%0 (Nominal) ... veri yolu nominal hız ve frekansta çalışıyor". Zero
    # packets is the ABSENCE of a measurement, not proof of health — asserting
    # "nominal" there invents a measurement (AGENTS.md §2.3). Report the data
    # gap instead. "Ölçüm yapıldı" requires an explicit packet count > 0 or a
    # provided load reading.
    measured = total_pkts > 0 or bool(bus_metrics.get("bus_load_percent") is not None and bus_load)

    if not measured:
        return (
            "📊 **Veri Yolu Trafik Analizi:**\n"
            "• **Veri Yolu Yükü:** Veri Yok | Hata Karesi: Veri Yok | Toplam: 0 paket\n"
            "• **Teşhis:** Ölçülmüş hat trafiği verisi yok. "
            "Sağlık/anomali beyanı üretilemez (nominal denemez)."
        )

    if bus_load > 75 or error_count > 5 or anomalies:
        status_tag = "⚠️ **KRİTİK ANOMALİ ALARMI**"
        line1 = f"📊 **Veri Yolu Trafik Analizi & Anomali Raporu ({status_tag}):**"
        line2 = f"• **Veri Yolu Yükü:** %{bus_load} (Eşik >%75) | Hata Karesi: {error_count} adet | Toplam: {total_pkts} paket"
        anom_desc = " • ".join(anomalies).rstrip(".") if anomalies else "Hat üzerinde yüksek yük veya hata karesi patlaması mevcut"
        line3 = f"• **Teşhis:** {anom_desc}. 120Ω sonlandırma direncini ve fiziksel katman voltajlarını (CAN-H/CAN-L) inceleyin."
    else:
        line1 = "📊 **Veri Yolu Trafik Analizi & Hat Durumu:**"
        line2 = f"• **Veri Yolu Yükü:** %{bus_load} (Nominal) | Hata Karesi: {error_count} adet | Toplam: {total_pkts} paket"
        line3 = "• **Teşhis:** CAN veri yolu nominal hız ve frekansta çalışıyor. Anomali veya babbling node tespit edilmedi."
    return f"{line1}\n{line2}\n{line3}"


# Weighted evidence kinds for local-expert root-cause confidence, mirroring
# engine/discovery/evidence.py methodology (P0 honesty fix: replaces the old
# hardcoded "%94 Belirlenimsiz Güvenilirlik" placeholder). Rule scenario and
# knowledge-base matches are complementary identification evidence — a DTC
# explained by a rule does not also require a KB hit.
ROOT_CAUSE_EVIDENCE_WEIGHTS: dict[str, float] = {
    "identification": 0.50,
    "telemetry_correlation": 0.30,
    "dtc_context": 0.20,
}


# T2-3 (fidelity): the confidence label carries its own calibration
# provenance. `compute_root_cause_confidence` returns ONE string, so a caller
# that logs/renders the label must be able to tell a calibrated score from an
# unresolvable-calibration one. Both markers are POSTFIXES; the label itself
# still starts with exactly one of {"Yüksek", "Orta", "Düşük", "Normal"} so the
# first word keeps working as a machine marker.
CALIBRATION_CONFIDENCE_SUFFIX = " · kalibre"
UNCALIBRATED_CONFIDENCE_SUFFIX = " · KALİBRE EDİLMEDİ (kalibrasyon kanıtı yok)"


def _resolve_calibration_factor(calibration_factor: float | None) -> float | None:
    """Fail-closed resolution of the golden-set calibration factor.

    ``None`` means "I do not know the factor" — NOT "no damping is needed".
    It is resolved internally from the memoised golden-set evaluation, so a
    caller that forgets the argument can no longer silently receive a
    full-confidence score.

    Returns a clamped factor, or ``None`` when no calibration evidence exists
    (the corpus could not be evaluated). The caller must then mark the result
    as uncalibrated instead of asserting full confidence — Tuzaklar §2: any
    path that fails toward the UNSAFE side is a bug.
    """
    if calibration_factor is not None:
        return max(0.1, min(1.0, float(calibration_factor)))
    try:
        resolved = compute_calibration_factor()
    except Exception as exc:  # noqa: BLE001 — calibration must never break diagnosis
        logger.warning("Calibration factor unresolvable (%s) — flagging confidence uncalibrated", exc)
        return None
    if resolved is None:
        return None
    return max(0.1, min(1.0, float(resolved)))


def root_cause_confidence_breakdown(
    dtc_count: int,
    scenario_matched: int,
    kb_matched: int,
    telemetry_correlation_count: int,
    calibration_factor: float | None = None,
) -> dict[str, Any]:
    """Component view of the confidence score (T70-D).

    MEASURED DEFECT (T70-D): ``compute_root_cause_confidence`` folds three
    weighted evidence kinds and a golden-set calibration factor into ONE
    string. The operator saw "Orta (%52 ağırlıklı kanıt skoru)" with no way to
    tell WHICH kind was carrying the score — an identification-only match and a
    telemetry-corroborated one rendered identically when their weighted means
    coincided.

    Returns the same numbers the label is built from, so the two can never
    disagree: ``{"score", "base_score", "label", "calibration", "calibrated",
    "components": {kind: {"value", "weight", "contribution"}}}``.

    ``label`` is byte-identical to what ``compute_root_cause_confidence``
    returns for the same arguments — this function is a view, not a second
    implementation. Deterministic and offline.
    """
    if dtc_count <= 0:
        return {
            "score": 0.0,
            "base_score": 0.0,
            "label": "Normal",
            "calibration": None,
            "calibrated": False,
            "components": {},
        }
    identified = scenario_matched + kb_matched
    entries = {
        "identification": min(1.0, identified / dtc_count),
        "telemetry_correlation": min(1.0, telemetry_correlation_count / 2.0),
        "dtc_context": min(1.0, dtc_count / 2.0),
    }
    total_weight = sum(ROOT_CAUSE_EVIDENCE_WEIGHTS.values())
    weighted_sum = sum(ROOT_CAUSE_EVIDENCE_WEIGHTS[kind] * value for kind, value in entries.items())
    base_score = weighted_sum / total_weight
    cal = _resolve_calibration_factor(calibration_factor)
    if cal is None:
        score = max(0.0, min(1.0, base_score))
        label_txt = "Yüksek" if score >= 0.65 else ("Orta" if score >= 0.35 else "Düşük")
        label = f"{label_txt} (%{score * 100:.0f} ağırlıklı kanıt skoru){UNCALIBRATED_CONFIDENCE_SUFFIX}"
    else:
        score = max(0.0, min(1.0, base_score * cal))
        label_txt = "Yüksek" if score >= 0.65 else ("Orta" if score >= 0.35 else "Düşük")
        label = f"{label_txt} (%{score * 100:.0f} ağırlıklı kanıt skoru){CALIBRATION_CONFIDENCE_SUFFIX}"
    components = {
        kind: {
            "value": value,
            "weight": ROOT_CAUSE_EVIDENCE_WEIGHTS[kind],
            "contribution": ROOT_CAUSE_EVIDENCE_WEIGHTS[kind] * value / total_weight,
        }
        for kind, value in entries.items()
    }
    return {
        "score": score,
        "base_score": base_score,
        "label": label,
        "calibration": cal,
        "calibrated": cal is not None,
        "components": components,
    }


#: Turkish labels for the evidence kinds, used when rendering the breakdown.
_EVIDENCE_KIND_LABELS: dict[str, str] = {
    "identification": "Kod tanıma (senaryo + bilgi tabanı)",
    "telemetry_correlation": "Canlı telemetri korelasyonu",
    "dtc_context": "Aktif kod bağlamı",
}


def format_confidence_breakdown(breakdown: dict[str, Any]) -> str:
    """Render the confidence breakdown for the operator (T70-D).

    Emits nothing when there is no score to explain (no active DTC), so a
    healthy session gains no noise. The calibration line states explicitly
    whether the golden-set factor was applied — an uncalibrated score must
    never be readable as a verified one.
    """
    if not isinstance(breakdown, dict) or not breakdown.get("components"):
        return ""
    lines = [
        f"  • **Skor:** %{float(breakdown.get('score', 0.0)) * 100:.0f} "
        f"— {breakdown.get('label', '')}"
    ]
    for kind, comp in breakdown["components"].items():
        if not isinstance(comp, dict):
            continue
        label_tr = _EVIDENCE_KIND_LABELS.get(kind, kind)
        lines.append(
            f"    – {label_tr}: %{float(comp.get('value', 0.0)) * 100:.0f} "
            f"(ağırlık {comp.get('weight', 0):g}, katkı "
            f"%{float(comp.get('contribution', 0.0)) * 100:.1f})"
        )
    cal = breakdown.get("calibration")
    if breakdown.get("calibrated") and isinstance(cal, (int, float)):
        lines.append(
            f"    – Golden-set kalibrasyon faktörü: {float(cal):.3f} "
            f"(uygulandı — skor ölçülmüş isabet oranına çekildi)"
        )
    else:
        lines.append(
            "    – ⚠️ Golden-set kalibrasyonu UYGULANAMADI: skor ham ağırlıklı "
            "ortalamadır, doğrulanmış isabet oranına çekilmemiştir."
        )
    return "\n📊 **Güven Skoru Kırılımı:**\n" + "\n".join(lines) + "\n"


def compute_root_cause_confidence(
    dtc_count: int,
    scenario_matched: int,
    kb_matched: int,
    telemetry_correlation_count: int,
    calibration_factor: float | None = None,
) -> str:
    """Compute an honest weighted root-cause confidence label for the offline expert.

    Each evidence kind is normalised to 0..1, weighted, and the weighted mean
    is taken over the total weight — so a full rule+telemetry match scores
    high while an unknown DTC with no corroboration scores near zero.

    ``calibration_factor`` (T2-3, fail-closed): ``None`` no longer means "no
    damping". It means "resolve the golden-set factor yourself" — see
    ``_resolve_calibration_factor``. A caller that silently forgets the
    argument therefore gets the CALIBRATED (damped) label, never the
    over-confident one. When no calibration evidence exists at all the label
    is returned with ``UNCALIBRATED_CONFIDENCE_SUFFIX`` so an uncalibrated
    score can never be mistaken for a verified one; when the factor was
    obtained (passed in or resolved) the label carries
    ``CALIBRATION_CONFIDENCE_SUFFIX``.

    T70-D: this is now a thin view over ``root_cause_confidence_breakdown``, so
    the label and the rendered breakdown are computed from the same numbers and
    cannot disagree.
    """
    return str(
        root_cause_confidence_breakdown(
            dtc_count=dtc_count,
            scenario_matched=scenario_matched,
            kb_matched=kb_matched,
            telemetry_correlation_count=telemetry_correlation_count,
            calibration_factor=calibration_factor,
        )["label"]
    )


def extract_hex_payload_from_query(query: str) -> list[int]:
    """Extract byte values from query string."""
    match = re.search(r"(?:Hex Payload|Payload|Data|Veri)\s*[:=]?\s*([0-9A-Fa-f\s]{2,})", query, re.IGNORECASE)
    candidate = match.group(1) if match else query
    cleaned = re.sub(r"\b0x[0-9A-Fa-f]{3,8}\b", "", candidate)
    tokens = re.findall(r"\b[0-9A-Fa-f]{2}\b", cleaned)
    return [int(t, 16) for t in tokens]


_FALLBACK_DBC_DECODER: Any = None
_FALLBACK_DBC_LOCK = threading.Lock()


def _get_fallback_dbc_decoder() -> Any:
    """Lazy-load DBC decoder for fallback signal decoding."""
    global _FALLBACK_DBC_DECODER
    if _FALLBACK_DBC_DECODER is not None:
        return _FALLBACK_DBC_DECODER
    with _FALLBACK_DBC_LOCK:
        if _FALLBACK_DBC_DECODER is not None:
            return _FALLBACK_DBC_DECODER
        try:
            from src.engine.decoder.dbc_decoder import DbcSignalDecoder
            cand = _DBC_DATA_DIR / "heavy_duty" / "j1939_canboat.dbc"
            if cand.exists():
                _FALLBACK_DBC_DECODER = DbcSignalDecoder.from_dbc_file(cand)
                return _FALLBACK_DBC_DECODER
        except Exception as exc:
            logger.debug("Fallback DBC decoder initialization skipped: %s", exc)
        return None


# ISO 15765-2 (ISO-TP) UDS service identifiers recognised by the packet
# explainer — requests and positive/negative responses.
# F-29: 0x46 is the SAE J1979 Mode $06 positive-response SID (the +0x40 echo
# of request 0x06). Without it a legitimate Mode $06 response frame fell past
# every decoder and printed only a bare CAN-ID line.
_UDS_KNOWN_SIDS: frozenset[int] = frozenset(
    {
        0x10, 0x11, 0x14, 0x19, 0x22, 0x27, 0x28, 0x2E, 0x31, 0x3E,
        0x50, 0x51, 0x54, 0x59, 0x62, 0x67, 0x71, 0x7F, 0x01,
        0x06, 0x46,
    }
)


def _resolve_uds_sid_index(payload_bytes: list[int]) -> int:
    """Resolve the byte offset of the UDS SID inside an ISO-TP framed payload.

    ISO 15765-2 PCI types (upper nibble of byte 0):
      0x0 = Single Frame (SF)  -> SID at byte 1
      0x1 = First Frame (FF)  -> SID at byte 2 (bytes 0-1 are PCI+length)
      0x2 = Consecutive Frame (CF) -> no reliable SID position
      0x3 = Flow Control (FC) -> not a service payload

    P1 fix: the legacy heuristic could misread a First Frame such as
    ``10 14 ...`` as SF with SID 0x14 (ClearDiagnosticInformation). With the
    explicit PCI nibble check, multi-frame requests are offset correctly and
    CF/FC fragments are never mistaken for fresh service requests.
    """
    if not payload_bytes:
        return 0
    pci_type = (payload_bytes[0] >> 4) & 0xF
    if pci_type == 0x0 and len(payload_bytes) > 1:
        return 1  # Single Frame: [0|len, SID, ...]
    if pci_type == 0x1 and len(payload_bytes) > 2:
        return 2  # First Frame: [0x1x, len_hi, len_lo, SID, ...]
    if pci_type in (0x2, 0x3):
        return 0  # CF fragment / FC frame — no reliable SID position
    # Unframed payload (e.g. raw physical request): SID at byte 0 unless
    # byte 0 is not a known SID while byte 1 is.
    if payload_bytes[0] not in _UDS_KNOWN_SIDS and len(payload_bytes) > 1 and payload_bytes[1] in _UDS_KNOWN_SIDS:
        return 1
    return 0


def _describe_ev_bms_frame(can_id: int) -> str | None:
    """Return a descriptive line for a recognised EV BMS frame, else None (F-28).

    Mirrors the id families that ``evaluate_diagnostic_query`` already
    recognises, so the frame explainer and the query engine agree. These are
    proprietary (non-standard-J1939) BMS ids, so matching is on the same
    documented byte prefix the query engine uses — not on a recomputed PGN.
    Only the frame's identity is described; no payload-derived value is
    invented (AGENTS.md §2.3).
    """
    # Low 4 hex digits of the arbitration id, e.g. 0x1808E5F4 -> "1808e5".
    prefix = f"{(can_id >> 8) & 0xFFFFFF:06x}"
    if prefix.startswith("1808e5"):
        return (
            f"⚡ **EV BMS Hücre Voltajları (0x{can_id:X} - PGN 61447):**\n"
            "• **Protokol:** ISO 11898-2 (EV Yüksek Voltaj BMS)\n"
            "• **Kaynak Düğüm:** Batarya Yönetim Sistemi (BMS ECU - 0xF4)"
        )
    if prefix.startswith("1807e5"):
        return (
            f"⚡ **EV BMS Şarj & Sağlık (0x{can_id:X} - PGN 61446):**\n"
            "• **Protokol:** ISO 11898-2 (BMS ECU 0xF4)\n"
            "• **Açıklama:** Batarya SOC (Şarj) ve SOH (Sağlık) durumu."
        )
    if prefix.startswith("1809e5"):
        return (
            f"⚡ **EV BMS Termal Yönetimi (0x{can_id:X} - PGN 61448):**\n"
            "• **Protokol:** ISO 11898-2 (BMS ECU 0xF4)\n"
            "• **Açıklama:** Batarya paketi ve hücre modülü sıcaklıkları."
        )
    if prefix.startswith("18f020"):
        return (
            f"⚡ **EV BMS Yüksek Voltaj İzolasyonu (0x{can_id:X}):**\n"
            "• **Protokol:** ISO 11898-2 (BMS ECU 0xF4)\n"
            "• **Açıklama:** HV izolasyon direnci ve kontaktör durumları."
        )
    return None


def explain_can_packet(
    can_id_hex_or_int: str | int,
    payload: bytes | list[int] | str = b"",
) -> tuple[str, list[dict[str, Any]]]:
    """Break down a CAN frame payload into a concise 2-3 line explanation with action triggers."""
    if isinstance(can_id_hex_or_int, str):
        cleaned_id = can_id_hex_or_int.strip().lower()
        if cleaned_id.startswith("0x"):
            can_id = int(cleaned_id, 16)
        else:
            can_id = int(cleaned_id, 16) if all(c in "0123456789abcdef" for c in cleaned_id) else 0
    else:
        can_id = int(can_id_hex_or_int)

    if isinstance(payload, str):
        payload_bytes = [int(t, 16) for t in re.findall(r"\b[0-9A-Fa-f]{2}\b", payload)]
    elif isinstance(payload, bytes):
        payload_bytes = list(payload)
    else:
        payload_bytes = list(payload)

    # 1. UDS Diagnostics (0x7DF or 0x7E0..0x7EF)
    if (0x7E0 <= can_id <= 0x7EF) or can_id == 0x7DF:
        if payload_bytes:
            sid_idx = _resolve_uds_sid_index(payload_bytes)

            sid = payload_bytes[sid_idx]

            # 0x10 Diagnostic Session Control
            if sid == 0x10:
                subfn = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                sub_names = {1: "Default", 2: "Programming", 3: "Extended", 4: "Safety System"}
                s_name = sub_names.get(subfn & 0x7F, f"0x{subfn:02X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x10):**"
                line2 = f"• **Servis:** `0x10 DiagnosticSessionControl` — {s_name} Session (0x{subfn:02X})"
                line3 = f"• **Anlam:** ECU'dan {s_name} oturumuna geçiş talep ediliyor."
                actions = [make_uds_session_action(subfn & 0x7F)]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x11 ECU Reset
            if sid == 0x11:
                rt = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                rt_names = {1: "Hard Reset", 2: "Key Off/On Reset", 3: "Soft Reset"}
                rt_name = rt_names.get(rt & 0x7F, f"Reset Tipi 0x{rt:02X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x11):**"
                line2 = f"• **Servis:** `0x11 ECUReset` — {rt_name} (0x{rt:02X})"
                line3 = "• **Anlam:** ECU işlemcisinin donanımsal/yazılımsal yeniden başlatılması talep ediliyor."
                actions = [make_uds_ecu_reset_action(rt & 0x7F)]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x14 Clear Diagnostic Information
            if sid == 0x14:
                dtc_grp = 0xFFFFFF
                if len(payload_bytes) >= sid_idx + 4:
                    dtc_grp = (payload_bytes[sid_idx + 1] << 16) | (payload_bytes[sid_idx + 2] << 8) | payload_bytes[sid_idx + 3]
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x14):**"
                line2 = f"• **Servis:** `0x14 ClearDiagnosticInformation` (DTC Grup: 0x{dtc_grp:06X})"
                line3 = "• **Anlam:** ECU hafızasındaki aktif ve geçmiş tüm DTC arıza kodlarının silinmesi talep ediliyor."
                actions = [make_uds_clear_dtc_action(dtc_grp)]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x19 Read DTC Information
            if sid == 0x19:
                subfn = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 2
                mask = payload_bytes[sid_idx + 2] if len(payload_bytes) > sid_idx + 2 else 0xFF
                sub_names = {
                    1: "reportNumberOfDTCByStatusMask",
                    2: "reportDTCByStatusMask",
                    4: "reportDTCSnapshotRecordByDTCNumber",
                    6: "reportDTCExtendedDataRecordByDTCNumber",
                }
                sub_name = sub_names.get(subfn, f"SubFunction 0x{subfn:02X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x19):**"
                line2 = f"• **Servis:** `0x19 ReadDTCInformation` — {sub_name} (Maske: 0x{mask:02X})"
                line3 = "• **Anlam:** ECU hata belleğindeki kayıtlı DTC arıza kodları ve durum maskesi sorgulanıyor."
                actions = [make_uds_clear_dtc_action()]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x22 Read Data By Identifier
            if sid == 0x22:
                did = (payload_bytes[sid_idx + 1] << 8 | payload_bytes[sid_idx + 2]) if len(payload_bytes) >= sid_idx + 3 else 0
                # T68: this used to be a hardcoded 9-entry dict, so a DID outside
                # it rendered as a bare hex number even though
                # `uds_did_database.json` carries 68 DIDs with name_tr,
                # subsystem, byte_length, data_type, scaling, offset and unit.
                # The DB lookup falls back to the builtin table when the file is
                # absent (fail-safe), and the builtin entries are kept as the
                # last resort so a stripped build still names the common ones.
                _did_row = _uds_did_row(did)
                if _did_row is not None:
                    did_name = _did_row.get("name_tr") or _did_row.get("name") or f"DID 0x{did:04X}"
                    _oem = str(_did_row.get("oem", "") or "").strip()
                    if _oem:
                        did_name = f"{did_name} ({_oem})"
                else:
                    known_dids = {
                        0xF190: "VIN (Araç Şasi Numarası)",
                        0xF187: "Yedek Parça Numarası",
                        0xF189: "ECU Yazılım Versiyonu",
                        0xF197: "Sistem Adı",
                        0x1102: "Common Rail Yakıt Basıncı",
                        0x4100: "EV Batarya Hücre Voltaj Haritası",
                        0x4101: "EV Min/Max Hücre Voltajı",
                        0x4102: "HVIL Sensör Voltajı",
                        0x4105: "Batarya Sıcaklık Dağılımı",
                    }
                    did_name = known_dids.get(did, f"DID 0x{did:04X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x22):**"
                line2 = f"• **Servis:** `0x22 ReadDataByIdentifier` — 0x{did:04X} ({did_name})"
                line3 = f"• **Anlam:** ECU'dan {did_name} parametresinin anlık telemetri değeri sorgulanıyor."
                # T68: surface the decode recipe so the operator can read the
                # answer bytes without a lookup table of their own.
                if _did_row is not None:
                    _recipe = _uds_did_decode_hint(_did_row)
                    if _recipe:
                        line3 += f"\n• **Çözümleme:** {_recipe}"
                    _sub = str(_did_row.get("subsystem", "") or "").strip()
                    if _sub:
                        line3 += f"\n• **Alt Sistem:** {_sub}"
                actions = [make_uds_read_vin_action()] if did == 0xF190 else []
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x27 Security Access
            if sid == 0x27:
                sec_sub = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                sec_mode = "Request Seed" if (sec_sub % 2 == 1) else "Send Key"
                line1 = f"📦 **UDS Güvenlik Paketi (ID: 0x{can_id:03X} / SID 0x27):**"
                line2 = f"• **Servis:** `0x27 SecurityAccess` — {sec_mode} (Seviye 0x{sec_sub:02X})"
                line3 = "• **Anlam:** ECU'nun korumalı teşhis ve programlama alanlarına erişim anahtarı doğrulanıyor."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x28 Communication Control
            if sid == 0x28:
                ctrl_type = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                c_names = {0: "enableRxAndTx", 1: "enableRxAndDisableTx", 3: "disableRxAndTx"}
                c_name = c_names.get(ctrl_type, f"0x{ctrl_type:02X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x28):**"
                line2 = f"• **Servis:** `0x28 CommunicationControl` — {c_name}"
                line3 = "• **Anlam:** CAN veri yolu üzerindeki normal mesaj iletimi geçici olarak durduruluyor/açılıyor."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x2E Write Data By Identifier
            if sid == 0x2E:
                did = (payload_bytes[sid_idx + 1] << 8 | payload_bytes[sid_idx + 2]) if len(payload_bytes) >= sid_idx + 3 else 0
                data_hex = " ".join(f"{b:02X}" for b in payload_bytes[sid_idx + 3:])
                line1 = f"📦 **UDS Yazma Paketi (ID: 0x{can_id:03X} / SID 0x2E):**"
                line2 = f"• **Servis:** `0x2E WriteDataByIdentifier` — DID 0x{did:04X}"
                line3 = f"• **Anlam:** ECU parametresi üzerine yeni değer yazılıyor (Veri: `{data_hex or 'Boş'}`)."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x31 Routine Control
            if sid == 0x31:
                ctrl_type = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                rid = (payload_bytes[sid_idx + 2] << 8 | payload_bytes[sid_idx + 3]) if len(payload_bytes) >= sid_idx + 4 else 0
                ctrl_names = {1: "Start Routine", 2: "Stop Routine", 3: "Request Routine Results"}
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x31):**"
                line2 = f"• **Servis:** `0x31 RoutineControl` — {ctrl_names.get(ctrl_type, 'Routine')} (ID: 0x{rid:04X})"
                line3 = f"• **Anlam:** ECU üzerinde 0x{rid:04X} nolu teşhis veya kalibrasyon rutini yürütülüyor."
                actions = [make_uds_routine_action(rid)]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x3E Tester Present
            if sid == 0x3E:
                subfn = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                suppress = bool(subfn & 0x80)
                line1 = f"📦 **UDS Keep-Alive Paketi (ID: 0x{can_id:03X} / SID 0x3E):**"
                line2 = f"• **Servis:** `0x3E TesterPresent` (Yanıt Bastırma={suppress})"
                line3 = "• **Anlam:** Tanı oturumunun zaman aşımına uğramasını önlemek için periyodik sinyal iletiliyor."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x7F Negative Response
            if sid == 0x7F:
                rej_sid = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                nrc = payload_bytes[sid_idx + 2] if len(payload_bytes) > sid_idx + 2 else 0
                nrc_hex = f"0x{nrc:02X}"
                nrc_info = UDS_NRC_CATALOG.get(nrc_hex, {"name": "Genel Red", "cause": "ECU işlemi reddetti", "action": "Ön koşulları kontrol edin"})
                line1 = f"🛑 **UDS Negatif Yanıt (ID: 0x{can_id:03X} / NRC {nrc_hex}):**"
                line2 = f"• **Reddedilen Servis:** `0x{rej_sid:02X}` | Hata: {nrc_info['name']}"
                line3 = f"• **Neden:** {nrc_info['cause']}. Çözüm: {nrc_info['action']}"
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x50 Positive Response (Diagnostic Session Control)
            if sid == 0x50:
                st = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x50):**"
                line2 = f"• **Servis:** `0x10 DiagnosticSessionControl` Onaylandı (Oturum: 0x{st:02X})"
                line3 = "• **Sonuç:** ECU talep edilen teşhis oturumuna başarıyla geçti."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x51 Positive Response (ECU Reset)
            if sid == 0x51:
                rt = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x51):**"
                line2 = f"• **Servis:** `0x11 ECUReset` Başarılı (Reset Tipi: 0x{rt:02X})"
                line3 = "• **Sonuç:** ECU yeniden başlatma komutunu kabul etti ve sıfırlanıyor."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x54 Positive Response (Clear DTC)
            if sid == 0x54:
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x54):**"
                line2 = "• **Servis:** `0x14 ClearDiagnosticInformation` Başarıyla Tamamlandı"
                line3 = "• **Sonuç:** ECU hata hafızası sıfırlandı. Arıza kodları başarıyla temizlendi."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x46 Positive Response (SAE J1979 Mode $06 — On-Board Monitoring)
            # F-29: Mode $06 reports non-continuous monitor results (misfire
            # counters, catalyst/O2 monitor values) as OBDMID/MID + TID + unit/
            # scale + measured min/max. Previously this SID was absent from
            # _UDS_KNOWN_SIDS, so the whole frame decoded to a bare CAN-ID line.
            if sid == 0x46:
                # ISO-TP single frame: [0x0N, 0x46, MID, TID, unit/scale, ...]
                mid = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                tid = payload_bytes[sid_idx + 2] if len(payload_bytes) > sid_idx + 2 else 0
                data_tail = payload_bytes[sid_idx + 3:]
                line1 = f"✅ **OBD-II Mode $06 Yanıtı (ID: 0x{can_id:03X} / SID 0x46):**"
                # F-28: route through the existing Mode $06 monitor catalog so a
                # known MID resolves to its real monitor name instead of only a
                # hex blob. This also gives `explain_can_frame_mode06` its first
                # production call site (it was a fully-implemented orphan). The
                # catalog tells us whether the MID matched; if it did not, we
                # keep the honest raw-byte description rather than inventing a
                # monitor name (AGENTS.md §2.3).
                monitor_text, matched = CausalBayesianInferenceEngine.explain_can_frame_mode06(
                    can_id, payload_bytes
                )
                if matched:
                    line2 = monitor_text.splitlines()[0] if monitor_text else ""
                    line3 = (
                        "• **Ölçüm:** Monitör kataloğunda eşleşti; "
                        f"ham test baytları `{' '.join(f'{b:02X}' for b in data_tail[:8])}`."
                        if data_tail
                        else "• **Ölçüm:** Monitör kimliği kataloğa eşleşti; ölçüm baytı gönderilmedi."
                    )
                    return (f"{line1}\n{line2}\n{line3}", [])
                line2 = (
                    f"• **Servis:** `Mode $06 On-Board Monitoring` — "
                    f"OBDMID/MID 0x{mid:02X}, TID 0x{tid:02X}"
                )
                if data_tail:
                    line3 = (
                        "• **Ölçüm:** Ham veri baytları "
                        f"`{' '.join(f'{b:02X}' for b in data_tail[:8])}` "
                        "(monitör test değeri / min / max)."
                    )
                else:
                    line3 = "• **Ölçüm:** Monitör kimliği raporlandı; ölçüm baytı gönderilmedi."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x59 Positive Response (Read DTC Information)
            if sid == 0x59:
                subfn = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 2
                mask = payload_bytes[sid_idx + 2] if len(payload_bytes) > sid_idx + 2 else 0
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x59):**"
                line2 = f"• **Servis:** `0x19 ReadDTCInformation` Başarılı Yanıt (Durum Maskesi: 0x{mask:02X})"
                line3 = "• **İçerik:** ECU arıza kodları raporlandı. Arıza temizleme için UDS 0x14 kullanılabilir."
                actions = [make_uds_clear_dtc_action()]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x62 Positive Response (Read DID)
            if sid == 0x62:
                did = (payload_bytes[sid_idx + 1] << 8 | payload_bytes[sid_idx + 2]) if len(payload_bytes) >= sid_idx + 3 else 0
                data_tail = payload_bytes[sid_idx + 3:]
                # T68: the response body used to be printed as raw hex unless the
                # DID happened to be 0xF190. The DID catalog states the byte
                # length, type, scale, offset and unit for 68 identifiers, so the
                # value is now decoded from the record's own recipe. An unknown
                # DID still prints its bytes — no invented interpretation.
                val_str = ""
                _did_row = _uds_did_row(did)
                if data_tail and _did_row is not None:
                    _decoded = _uds_did_decode_value(_did_row, bytes(data_tail))
                    if _decoded:
                        _name = str(_did_row.get("name_tr") or _did_row.get("name") or "").strip()
                        _label = f" ({_name})" if _name else ""
                        val_str = f" Değer{_label}: {_decoded} |"
                elif did == 0xF190 and data_tail:
                    ascii_str = "".join(chr(b) for b in data_tail if 32 <= b <= 126)
                    val_str = f" Araç VIN: `{ascii_str}` |" if ascii_str else ""
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x62):**"
                line2 = f"• **Servis:** `0x22 Read DID 0x{did:04X}` Başarılı Yanıt"
                line3 = f"• **İçerik:**{val_str} Veri baytları: `{' '.join(f'{b:02X}' for b in data_tail[:8])}`"
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x67 Positive Response (Security Access)
            if sid == 0x67:
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x67):**"
                line2 = "• **Servis:** `0x27 SecurityAccess` Kilit Açıldı (Security Unlocked)"
                line3 = "• **Sonuç:** Güvenlik katmanı doğrulandı. Korumalı rutin ve yazma işlemleri aktif."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x71 Positive Response (Routine Control)
            if sid == 0x71:
                rid = (payload_bytes[sid_idx + 2] << 8 | payload_bytes[sid_idx + 3]) if len(payload_bytes) >= sid_idx + 4 else 0
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x71):**"
                line2 = f"• **Servis:** `0x31 RoutineControl` Başarıyla Yürütüldü (RID: 0x{rid:04X})"
                line3 = "• **Sonuç:** Teşhis rutini başarıyla tamamlandı."
                return (f"{line1}\n{line2}\n{line3}", [])

            # OBD-II Mode 01
            if sid == 0x01:
                pid = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                pids: dict[int, tuple[str, Callable[[bytes], str]]] = {
                    0x04: ("Hesaplanan Yük Değeri", lambda b: f"%{b[0]*100/255:.1f}" if len(b) > 0 else ""),
                    0x05: ("Motor Soğutma Sıvısı Sıcaklığı (ECT)", lambda b: f"{b[0] - 40}°C" if len(b) > 0 else ""),
                    0x0B: ("Emme Manifoldu Basıncı (MAP)", lambda b: f"{b[0]} kPa" if len(b) > 0 else ""),
                    0x0C: ("Motor Devri (RPM)", lambda b: f"{(b[0]*256 + b[1])/4:.0f} RPM" if len(b) > 1 else ""),
                    0x0D: ("Araç Hızı (Speed)", lambda b: f"{b[0]} km/s" if len(b) > 0 else ""),
                    0x0F: ("Emme Havası Sıcaklığı (IAT)", lambda b: f"{b[0] - 40}°C" if len(b) > 0 else ""),
                    0x11: ("Gaz Kelebeği Pozisyonu", lambda b: f"%{b[0]*100/255:.1f}" if len(b) > 0 else ""),
                }
                p_name, calc = pids.get(pid, (f"PID 0x{pid:02X}", lambda b: ""))
                val_txt = calc(payload_bytes[sid_idx + 2:]) if len(payload_bytes) > sid_idx + 2 else ""
                val_s = f" — Değer: {val_txt}" if val_txt else ""
                line1 = f"📡 **OBD-II Canlı Telemetri Sorgusu (ID: 0x{can_id:03X}):**"
                line2 = f"• **Servis:** `Mode 01 PID 0x{pid:02X}` — {p_name}"
                line3 = f"• **Anlam:** Standart OBD-II canlı parametre talebi{val_s}."
                return (f"{line1}\n{line2}\n{line3}", [])

            # OBD-II Mode $06 — On-Board Monitoring Test Results (A3-13)
            # The 88 KB obd_mode06_database.json had NO consumption path before
            # this branch: it maps the request SID 0x06 payload (<MID> <TID>) to
            # the monitor/test description. Honest fallback when unknown.
            if sid == 0x06:
                if len(payload_bytes) > sid_idx + 1:
                    mid = payload_bytes[sid_idx + 1]
                    tid = payload_bytes[sid_idx + 2] if len(payload_bytes) > sid_idx + 2 else None
                    db = get_mode06_database()
                    monitors = db.get("monitors") if isinstance(db, dict) else None
                    known = bool(monitors) and f"0x{mid:02X}" in monitors
                    if known:
                        text = format_mode06_monitor(mid, tid)
                        return (f"📡 **OBD-II Mode $06 (ID: 0x{can_id:03X}):**\n{text}", [])
                    return (
                        f"📡 **OBD-II Mode $06 (ID: 0x{can_id:03X}):** İzleme testi MID 0x{mid:02X} katalogda yok (uydurma yok).",
                        [],
                    )
                return (
                    f"📡 **OBD-II Mode $06 (ID: 0x{can_id:03X}):** MID/TID eksik — izleme testi çözülemedi.",
                    [],
                )

    # 2. J1939 Extended 29-bit Frames
    if can_id > 0x7FF:
        pgn = (can_id >> 8) & 0x3FFFF
        pf = (can_id >> 16) & 0xFF
        if pf < 240:
            pgn = pgn & 0x3FF00

        # PGN 61444 EEC1
        if pgn == 61444:
            rpm_val = ((payload_bytes[4] << 8 | payload_bytes[3]) * 0.125) if len(payload_bytes) >= 5 else 0.0
            torque_val = (payload_bytes[2] - 125) if len(payload_bytes) >= 3 else 0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 61444 / EEC1):**"
            line2 = "• **Sistem:** Elektronik Motor Denetleyicisi 1 (Electronic Engine Controller 1)"
            line3 = f"• **Çözülen Sinyaller:** Motor Devri: `{rpm_val:.0f} RPM`, Aktüel Motor Torku: `%{torque_val}`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65265 CCVS
        if pgn == 65265:
            speed_kmh = ((payload_bytes[2] << 8 | payload_bytes[1]) / 256.0) if len(payload_bytes) >= 3 else 0.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65265 / CCVS):**"
            line2 = "• **Sistem:** Seyir Kontrolü & Araç Hızı (Cruise Control & Vehicle Speed)"
            line3 = f"• **Çözülen Sinyaller:** Tekerlek Tabanlı Araç Hızı: `{speed_kmh:.1f} km/s`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65226 DM1
        if pgn == 65226:
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65226 / DM1):**"
            line2 = "• **Sistem:** Aktif Diyagnostik Hata Kodları (Active Diagnostic Trouble Codes)"
            line3 = "• **Anlam:** Araçtaki aktif arıza lambası (MIL/AWL) ve mevcut SPN/FMI hata durumunu bildirir."
            actions = [make_j1939_dm1_action()]
            return (f"{line1}\n{line2}\n{line3}", actions)

        # PGN 65235 DM11
        if pgn == 65235:
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65235 / DM11):**"
            line2 = "• **Sistem:** Aktif Arıza Kodlarını Temizleme (Diagnostic Data Clear)"
            line3 = "• **Anlam:** ECU hafızasındaki aktif arıza kodlarının sıfırlanmasını talep eder."
            actions = [make_j1939_dm11_action()]
            return (f"{line1}\n{line2}\n{line3}", actions)

        # PGN 65249 Engine Hours, Revolutions (REVIEW3 #5: previously
        # mislabelled ET1/coolant — 0xFEE1 is Engine Hours per SAE
        # J1939-71; rendering its LSB as °C fabricated overheat readings).
        if pgn == 65249:
            hours_raw = (payload_bytes[0] | (payload_bytes[1] << 8) if len(payload_bytes) >= 2 else 0)
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65249 / Engine Hours):**"
            line2 = "• **Sistem:** Motor Çalışma Saatleri (Engine Hours, Revolutions)"
            line3 = f"• **Çözülen Sinyaller:** Toplam Motor Çalışma Süresi (SPN 237): `{hours_raw / 20.0:.1f} saat` (1/20 h/bit)."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65263 EFL_P1 (Engine Fluid Level/Pressure 1)
        if pgn == 65263:
            oil_press_kpa = (payload_bytes[3] * 4) if len(payload_bytes) >= 4 else 0
            oil_bar = oil_press_kpa / 100.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65263 / EFL_P1):**"
            line2 = "• **Sistem:** Motor Sıvı Seviye ve Basınçları 1 (Engine Fluid Level/Pressure 1)"
            line3 = f"• **Çözülen Sinyaller:** Motor Yağ Basıncı (SPN 100): `{oil_bar:.2f} Bar` ({oil_press_kpa} kPa)."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65262 ET1 (Engine Temperature 1) — REVIEW3 #5: this is the
        # real ET1 (0xFEEE, SPN 110 coolant, byte 0, -40 °C offset), which
        # the old table mislabelled as ET2/oil temperature.
        if pgn == 65262:
            coolant_t = (payload_bytes[0] - 40) if len(payload_bytes) >= 1 else 0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65262 / ET1):**"
            line2 = "• **Sistem:** Motor Sıcaklığı 1 (Engine Temperature 1)"
            line3 = f"• **Çözülen Sinyaller:** Motor Soğutma Sıvısı Sıcaklığı (SPN 110): `{coolant_t}°C`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65269 AMB (Ambient Conditions)
        if pgn == 65269:
            amb_temp = (((payload_bytes[4] << 8 | payload_bytes[3]) * 0.03125) - 273) if len(payload_bytes) >= 5 else 0.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65269 / AMB):**"
            line2 = "• **Sistem:** Ortam Çevre Koşulları (Ambient Conditions)"
            line3 = f"• **Çözülen Sinyaller:** Dış Ortam Hava Sıcaklığı (SPN 171): `{amb_temp:.1f}°C`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65257 LFE (Fuel Economy)
        if pgn == 65257:
            fuel_rate = ((payload_bytes[1] << 8 | payload_bytes[0]) * 0.05) if len(payload_bytes) >= 2 else 0.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65257 / LFE):**"
            line2 = "• **Sistem:** Yakıt Ekonomisi (Fuel Economy / Liquid Fuel Economy)"
            line3 = f"• **Çözülen Sinyaller:** Anlık Yakıt Tüketim Debisi (SPN 183): `{fuel_rate:.1f} L/h`."
            return (f"{line1}\n{line2}\n{line3}", [])

    # 3. Known Standard Diagnostic IDs without payload
    if can_id == 0x7DF:
        return ("📡 **CAN ID 0x7DF:** Standart OBD-II Fonksiyonel Yayın İsteği (Tüm bağlı ECU'lara eşzamanlı genel sorgu).", [])
    if 0x7E0 <= can_id <= 0x7E7:
        ecu_name = "Motor (ECM/PCM)" if can_id == 0x7E0 else ("Şanzıman (TCM)" if can_id == 0x7E1 else f"ECU_{can_id - 0x7E0}")
        return (f"📡 **CAN ID 0x{can_id:03X}:** ISO 15765-4 Standart OBD-II / UDS Fiziksel İstek Hattı ({ecu_name}).", [])
    if 0x7E8 <= can_id <= 0x7EF:
        ecu_name = "Motor (ECM/PCM)" if can_id == 0x7E8 else ("Şanzıman (TCM)" if can_id == 0x7E9 else f"ECU_{can_id - 0x7E8}")
        return (f"📡 **CAN ID 0x{can_id:03X}:** ISO 15765-4 Standart OBD-II / UDS Fiziksel Yanıt Hattı ({ecu_name}).", [])

    # 3.5 EV BMS & N2K recognised frames (F-28)
    # `evaluate_diagnostic_query` already decoded these J1939-PGN frames, but
    # `explain_can_packet` did not own the same table, so pasting an EV BMS or
    # N2K arbitration id into the frame explainer answered "CAN ID Tanımsız".
    # The two entry points must agree on the same closed id set; this branch
    # mirrors the PGN families the query engine recognises. Only the frame's
    # identity is described — no byte-level claim is invented from a payload
    # we cannot DBC-decode (AGENTS.md §2.3).
    ev_bms = _describe_ev_bms_frame(can_id)
    if ev_bms is not None:
        return (ev_bms, [])

    # 4. DBC Fallback Signal Decoding for any frame with payload
    if payload_bytes:
        decoder = _get_fallback_dbc_decoder()
        if decoder is not None:
            try:
                from src.core.models.can_frame import CanFrame
                is_ext = can_id > 0x7FF
                cf = CanFrame(
                    arbitration_id=can_id,
                    is_extended=is_ext,
                    dlc=len(payload_bytes),
                    data=bytes(payload_bytes[:8]),
                    channel_id="ch0",
                )
                decoded_msg = decoder.decode_frame(cf)
                if decoded_msg and decoded_msg.signals:
                    sig_strs = [
                        f"{s.name}: `{s.value}` {s.unit}".strip()
                        for s in list(decoded_msg.signals.values())[:3]
                    ]
                    line1 = f"📦 **DBC Çözümlenmiş Mesaj (ID: 0x{can_id:X} - {decoded_msg.message_name}):**"
                    line2 = f"• **Sinyaller:** {', '.join(sig_strs)}"
                    line3 = f"• **Detay:** Vector DBC veritabanı ile {len(decoded_msg.signals)} adet sinyal başarıyla çözümlendi."
                    return (f"{line1}\n{line2}\n{line3}", [])
            except Exception:
                pass

    # 5. Fallback for unrecognized frame
    hex_str = " ".join(f"{b:02X}" for b in payload_bytes) if payload_bytes else "Boş"
    return (
        f"⚠️ **CAN ID Tanımsız (0X{can_id:X}):**\n"
        f"Bu mesaj kimliği için yerel veritabanında veya protokol motorunda kayıtlı bir sinyal tanımı bulunamadı.\n"
        f"• Sniffer tablosundan canlı veri uzunluğunu (DLC={len(payload_bytes)}) ve bayt değişimlerini (`{hex_str}`) inceleyebilirsiniz.",
        [],
    )


# ============================================================================
# COMPREHENSIVE MULTI-DOMAIN AUTOMOTIVE KNOWLEDGE BASE (120+ CODES & PROTOCOLS)
# ============================================================================

EXPERT_KNOWLEDGE_BASE: dict[str, dict[str, Any]] = {
    # ------------------ EV, HIGH VOLTAGE & BMS ------------------
    "P0A0B": {
        "title": "Yüksek Voltaj Güvenlik Kilidi (HVIL) Devresi Açık (HVIL Circuit Open)",
        "subsystem": "EV Yüksek Voltaj Güvenlik & BMS",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Manuel Servis Şalteri (MSD) tam oturmamış veya pilot kontağı ayrılmış.",
            "İnverter, DC-DC veya klima kompresörü HV turuncu kapağındaki interlock köprüsü açık.",
            "HVIL 100 Hz PWM sinyal hattında kopukluk veya şasiye kısa devre (R_loop > 5 Ohm).",
        ],
        "steps": [
            ("MSD emniyet mandalını söküp kilit tırnağının yerine tam oturduğunu kontrol edin.", "Manuel Servis Şalteri (MSD)", "Kolay (Görsel)"),
            ("BMS HVIL çıkış pini ile dönüş pini arasındaki loop direncini ölçün (Kontak KAPALI: R < 5 Ω).", "HVIL Tesisat Döngüsü", "Orta (Alet Gerekir)"),
            ("Osiloskopta HVIL sinyalini gözlemleyin: 100 Hz ±5% kare dalga, %50 doluluk ve 12V/5V genlik olmalıdır.", "BMS Kontrol Ünitesi (BECM)", "İleri (Servis)"),
        ],
        "measurement": "Nominal HVIL Döngü Direnci: <5.0 Ω | PWM: 100 Hz, %50 Duty Cycle, V_high > 9.0V (12V sistem) / > 3.8V (5V sistem).",
        # P0-2: the leading "0x31 " is the UDS service id, NOT a routine
        # identifier. The OEM routine id (0xD001) is graph/DB-level knowledge
        # here, not per-case evidence, so the label must not present it as the
        # routine the operator asked for — see `_extract_routine_id`.
        "uds_routine": "UDS Service 0x31 (Rutin Kontrol) — OEM rutin kimliği vaka başına ayrıca doğrulanmalıdır",
    },
    "P0A0D": {
        "title": "HVIL Devresi Yüksek Voltaj Kısa Devre (HVIL Circuit High)",
        "subsystem": "EV Yüksek Voltaj Güvenlik & BMS",
        "severity": "CRITICAL_STOP",
        "causes": [
            "HVIL sinyal kablosu araç 12V/24V akü besleme hattına (KL30/KL15) ezilerek kısa devre yapmış.",
            "BMS dahili pull-up direnç katı arızalanmış.",
        ],
        "steps": [
            ("HVIL soketini BMS'ten ayırıp araç tesisatındaki voltajı şasiye göre ölçün (0V olmalıdır).", "HVIL Kablo Demeti", "Orta (Alet Gerekir)"),
            ("12V besleme kablo demetlerinde sürtünme ve ezilme kontrolü yapın.", "Kablo Tesisatı", "Kolay (Görsel)"),
        ],
        "measurement": "HVIL Sinyal Voltajı > 5.5V (5V loop) veya > 15.0V (12V loop) arıza eşiğidir.",
        "uds_routine": "UDS Service 0x22 (DID 0x4102: HVIL Sense ADC Raw Voltage)",
    },
    "P0AA6": {
        "title": "Yüksek Voltaj İzolasyon Direnci Düşüklüğü (HV Isolation Fault)",
        "subsystem": "EV Batarya Paketi & Yüksek Voltaj İzolasyonu",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Batarya muhafazası içine soğutma sıvısı (antifriz) veya nem sızması.",
            "Klima kompresörü stator sargı izolasyonunun kompresör yağı ile bozulması.",
            "İnverter IGBT güç modülü substratında dielektrik delinme.",
        ],
        "steps": [
            ("LOTO güvenlik prosedürünü uygulayın (MSD sök, 10 dk bekle, DC Bus < 5V sıfır enerji onayı).", "HV Batarya Paketi", "İleri (Servis)"),
            ("Fluke 1587 / Megger ile 500V/1000V DC test voltajında HV+ ve HV- hatlarının şasiye izolasyonunu ölçün.", "HV+ / HV- Hatları", "İleri (Servis)"),
            ("HV alt dallarını (Klima, PTC Isıtıcı, OBC, DC-DC) tek tek ayırarak arızalı komponenti izole edin.", "Yüksek Voltaj Dağıtım Kutusu (PDU)", "İleri (Servis)"),
        ],
        "measurement": "ISO 6469-1 / UNECE R100 Standardı: Min İzolasyon Direnci ≥ 500 Ω/V DC (400V için ≥ 200 kΩ, 800V için ≥ 400 kΩ). Sağlıklı sistem: > 50 MΩ.",
        "uds_routine": "UDS Routine 0x31 (ID 0xD010: Automated Isolation Self-Test Sequence)",
    },
    "P0A80": {
        "title": "Hibrit / Elektrikli Araç Batarya Paketi Değişimi (Replace EV Battery Pack)",
        "subsystem": "EV Batarya Paketi & Hücre Sağlığı (SOH)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Hücreler arası kapasite kaybı >%30 (SOH_C < %70) veya iç direnç sapması >%50.",
            "Hücre delta voltajının yük altında >150 mV ve beklemede >50 mV seviyesine açılması.",
            "Hücre içi lityum kaplanması (lithium plating) ve aktif katot kütle kaybı.",
        ],
        "steps": [
            ("Bataryayı %100 SOC'ye şarj edip hücre dengeleme (balancing) rutinini tamamlayın.", "BMS Hücre Dengeleme", "Orta (Alet Gerekir)"),
            ("0.5C - 1C yük darbesi uygulayarak her bir hücrenin iç direncini (Ri = ΔV/ΔI) loglayın.", "Hücre Denetim Devresi (CSC)", "İleri (Servis)"),
            ("Diverjans gösteren zayıf hücre modülünü veya tüm batarya paketini değiştirin.", "Batarya Modülü", "İleri (Servis)"),
        ],
        "measurement": "Nominal Hücre Delta Voltajı: <30 mV | Arıza / Değişim Eşiği: >150 mV (Yükte) veya >50 mV (Dengede).",
        "uds_routine": "UDS Service 0x22 (DID 0x4100: Individual Cell Voltages & SOH Map)",
    },
    "P0A93": {
        "title": "İnverter Soğutma Sistemi Performansı (Inverter Cooling Performance)",
        "subsystem": "Elektrik Motoru & İnverter Termal Yönetimi",
        "severity": "MEDIUM",
        "causes": [
            "Elektrikli inverter su pompasının (E-Pump) debi kaybetmesi veya sıkışması.",
            "İnverter soğutma ceketinde hava cebi kalması veya radyatör petek tıkanıklığı.",
            "İnverter IGBT güç modülü altındaki termal macun kuruması/bozulması.",
        ],
        "steps": [
            ("UDS Routine 0x31 ile inverter soğutma pompasını %100 PWM ile çalıştırıp debiyi kontrol edin.", "Elektrikli Su Pompası", "Orta (Alet Gerekir)"),
            ("Soğutma devresinde vakumlu hava alma prosedürünü uygulayın.", "Soğutma Sıvısı Devresi", "Orta (Alet Gerekir)"),
            ("İnverter giriş ve çıkış sıcaklık sensörleri arasındaki farkı kontrol edin (Normal ΔT < 10°C).", "İnverter Sıcaklık Sensörleri", "Kolay (Görsel)"),
        ],
        "measurement": "İnverter IGBT Kritik Sıcaklık Limiti: >110°C (Derate başlar), >125°C (Acil Kesinti).",
        "uds_routine": "UDS Routine 0x31 (ID 0xD012: Coolant Circuit Vacuum Bleeding Routine)",
    },
    "P0B24": {
        "title": "Batarya Hücre Kritik Düşük Voltaj (Cell Undervoltage)",
        "subsystem": "EV Batarya Hücre Koruma",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Hücrede aşırı kendi kendine deşarj (mikro kısa devre) veya hücre voltajı < 2.50V (NMC) / < 2.00V (LFP).",
            "CSC kartı gerilim örnekleme hattında lehim çatlağı veya kopukluk.",
        ],
        "steps": [
            ("Hücre voltajını CSC soket pinlerinden 6.5 dijit DMM ile doğrudan ölçün.", "Hücre Klemensleri", "İleri (Servis)"),
            ("Gerçekten 2.0V altına inmiş hücreyi ASLA şarj etmeyin (Bakır dendrit yangın riski) — modülü değiştirin.", "Batarya Hücre Modülü", "İleri (Servis)"),
        ],
        "measurement": "NMC/NCA Alt Kesme: 2.50V | LFP Alt Kesme: 2.00V | Sağlıklı Nominal: 3.20V - 4.20V.",
        "uds_routine": "UDS Service 0x22 (DID 0x4101: Cell Min/Max Voltage Tracking)",
    },
    "P0AC0": {
        "title": "Batarya Sıcaklık Sensörü Aralık / Performans (Battery Temp Sensor Range)",
        "subsystem": "EV Batarya Termal İzleme",
        "severity": "MEDIUM",
        "causes": [
            "Modül NTC termistöründe direnç kayması (>%10) veya soket gevşekliği.",
            "Komşu sensörler ile okuma farkının >5°C olması.",
        ],
        "steps": [
            ("Sensör direncini 25°C ortamda multimetre ile ölçün (10 kΩ ±%1 olmalıdır).", "10k NTC Termistör", "Orta (Alet Gerekir)"),
            ("Termistör kablo demetinde şasiye sürtünme ve ezilme kontrolü yapın.", "Termal Sensör Kablo Demeti", "Kolay (Görsel)"),
        ],
        "measurement": "10k NTC Değerleri: 25°C = 10.0 kΩ (2.50V), 0°C = 32.6 kΩ (3.82V), 60°C = 2.48 kΩ (0.99V).",
        "uds_routine": "UDS Service 0x22 (DID 0x4105: Battery Module Temperature Distribution)",
    },
    "P0AA1": {
        "title": "Pozitif Ana Kontaktör Kapalı Yapışık Kaldı (Positive Contactor Stuck Closed)",
        "subsystem": "EV Yüksek Voltaj Kontaktör Grubu",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Aşırı inrush akımı veya precharge direnci arızası nedeniyle kontaktör kontaklarının kaynak olması.",
            "BMS kontaktör bobin sürme transistörünün (Low-Side FET) kısa devre olması.",
        ],
        "steps": [
            ("LOTO uygulayın, MSD sökün. Kontaktör güç terminalleri arasındaki direnci ölçün (>100 MΩ olmalıdır).", "Pozitif Ana Kontaktör", "İleri (Servis)"),
            ("0.0 Ω okunuyorsa kontaktör kontakları mekanik olarak kaynamıştır — kontaktör grubunu yenileyin.", "HV Kontaktör Bloğu", "İleri (Servis)"),
        ],
        "measurement": "Kontaktör Açık Durum Direnci: >100 MΩ | Bobin Direnci: 24.0 Ω ±%10.",
        "uds_routine": "UDS Routine 0x31 (ID 0xD020: Contactor Weld Detection Self-Test)",
    },
    "P0AA2": {
        "title": "Pozitif Ana Kontaktör Açık Kaldı / Çekmiyor (Positive Contactor Stuck Open)",
        "subsystem": "EV Yüksek Voltaj Kontaktör Grubu",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Precharge voltajının 300 ms içinde %95 seviyesine ulaşamaması (Precharge zaman aşımı).",
            "Kontaktör bobin sargısının yanması/kopması veya soket gevşekliği.",
        ],
        "steps": [
            ("Kontaktör bobin direncini ölçün (20 - 30 Ω arası olmalıdır).", "Kontaktör Bobini", "Orta (Alet Gerekir)"),
            ("Precharge direncini ölçün (Nominal 33 Ω veya 47 Ω, açık devre olmamalıdır).", "Precharge Direnci", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Precharge Zaman Aşımı Eşiği: 300 ms (V_bus < %90 V_pack ise kontak açılır).",
        "uds_routine": "UDS Routine 0x31 (ID 0xD021: Precharge Relay & Resistor Health Check)",
    },

    # ------------------ HEAVY DUTY & SAE J1939 ------------------
    "SPN100": {
        "title": "Motor Yağ Basıncı Hatası (Engine Oil Pressure Fault)",
        "subsystem": "Ağır Vasıta Yağlama Sistemi (J1939)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 1 (Kritik Düşük): Yağ pompası aşınması, karterde yağ seviyesinin tükenmesi veya ana yatak aşınması.",
            "FMI 3 (Voltaj Yüksek): Sinyal kablosu 5V referansa veya 24V hatta kısa devre.",
            "FMI 4 (Voltaj Düşük): Sinyal kablosu şasiye kısa devre veya sensör kopuk.",
        ],
        "steps": [
            ("Motoru derhal durdurun ve yağ seviye çubuğunu kontrol edin.", "Motor Karteri", "Kolay (Görsel)"),
            ("Sensör soketinde 5.0V besleme (Pin 1), Şasi (Pin 2) ve Sinyal voltajını (Pin 3) ölçün (Rölantide 1.2 - 2.5V).", "Yağ Basınç Sensörü", "Orta (Alet Gerekir)"),
            ("Mekanik manometre bağlayarak gerçek yağ basıncını doğrulayın (Rölanti >1.0 bar, 1800 RPM >3.0 bar).", "Yağ Galerisi Test Portu", "İleri (Servis)"),
        ],
        "measurement": "Sensör Skalası: 0.5V = 0 kPa, 4.5V = 1000 kPa | Kritik Kırmızı Lamba Limiti: <70 kPa (Rölanti), <180 kPa (Devirde).",
        "uds_routine": "J1939 DM11 (PGN 65235 Clear Active) & DM4 (PGN 65229 Freeze Frame Oku)",
    },
    "SPN102": {
        "title": "Turbo Takviye Basıncı Hatası (Turbo Boost Pressure Fault)",
        "subsystem": "Ağır Vasıta Hava Emiş & Turboşarj (J1939)",
        "severity": "MEDIUM",
        "causes": [
            "FMI 0/16 (Aşırı Basınç): VGT aktüatör kanatçıklarının kurumdan sıkışması veya wastegate valf arızası.",
            "FMI 18 (Düşük Basınç): Intercooler hortum yırtığı, intercooler radyatör çatlağı veya kompresör çark hasarı.",
            "FMI 2 (Tutarsızlık): Kontak açıkken atmosfer basıncı (SPN 108) ile turbo basıncı farkı >15 kPa.",
        ],
        "steps": [
            ("Intercooler hortum kelepçelerini ve şarj hava borularını duman makinesi ile sızdırmazlık testine tabi tutun.", "Şarj Havası Boruları & CAC", "Orta (Alet Gerekir)"),
            ("VGT aktüatör kolunun hareketini teşhis cihazından %0 - %100 sürerek test edin.", "Elektronik VGT Aktüatörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Tam Yükte Nominal Boost: 2.2 - 3.2 Bar (Abs) | Maksimum Güvenlik Limiti: 3.6 Bar.",
        "uds_routine": "J1939 Routine: VGT Vane Position Calibration & End-Stop Learning",
    },
    "SPN110": {
        "title": "Motor Soğutma Sıvısı Sıcaklığı (Engine Coolant Temperature)",
        "subsystem": "Ağır Vasıta Termal & Soğutma Sistemi (J1939)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 0 (Kritik Yüksek): Sıcaklık >108°C; termostat kapalı kalmış, viskoz fan kilitlenmiyor veya radyatör tıkalı.",
            "FMI 3 (Açık Devre): Sensör kablosu kopuk (ECU -40°C algılar ve fanı %100 açar).",
            "FMI 4 (Şasiye Kısa Devre): Sensör sinyali şasiye kısa devre (ECU +140°C algılar ve torku %50 kısar).",
        ],
        "steps": [
            ("Radyatör alt ve üst hortum sıcaklıklarını infrared termometre ile karşılaştırın (ΔT > 15°C ise termostat açmıyor).", "Termostat & Radyatör", "Kolay (Görsel)"),
            ("ECT sensör direncini ölçün: 20°C'de ~2.5 kΩ, 80°C'de ~320 Ω, 100°C'de ~180 Ω olmalıdır.", "Soğutma Sıvısı Sıcaklık Sensörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Normal Çalışma Aralığı: 82°C - 95°C | Uyarı (AWL): >103°C | Kırmızı Lamba (RSL Derate): >108°C.",
        "uds_routine": "J1939 Actuator Test: Viscous Fan Clutch 100% Engagement Override",
    },
    "SPN190": {
        "title": "Motor Devri / Krank Sinyal Hatası (Engine Speed / Crank Phase Sync)",
        "subsystem": "Ağır Vasıta Motor Zamanlama & Krank",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 0 (Aşırı Devir): Motor devri >2450 RPM (Yokuş aşağı vites hatası).",
            "FMI 2 (Faz Senkronizasyon Kaybı): Krank ve kam mili sinyal desenleri arasında açısal kayma (Triger/dişli boşluğu).",
            "FMI 8 (Sinyal Paraziti): Marş dinamosu veya enjektör kablosundan krank sensör zırhına elektromanyetik girişim (EMI).",
        ],
        "steps": [
            ("Osiloskop ile Krank (VR sinüs) ve Kam (Hall 0-5V) sinyallerini eşzamanlı kaydedip diş eksiklerini karşılaştırın.", "Krank & Kam Sensörleri", "İleri (Servis)"),
            ("Krank sensörü hava boşluğunu (Air-gap) sentil ile ölçün (0.8 - 1.2 mm olmalıdır).", "Volan Dişli Çelengi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "VR Sensör Direnci: 800 - 1400 Ω | Marş Sırasında VR AC Genlik: >1.0 Vpp.",
        "uds_routine": "J1939 Engine Speed Calibration & Cylinder Cutout Test",
    },
    "SPN1761": {
        "title": "AdBlue (DEF) Tank Seviyesi Hatası (DEF Tank Level)",
        "subsystem": "Ağır Vasıta SCR & Emisyon Sistemi (J1939)",
        "severity": "MEDIUM",
        "causes": [
            "FMI 17 (Seviye <%10): Sarı ikaz lambası.",
            "FMI 18 (Seviye <%5): Seviye 1 Tork Kısıtlaması (%25 tork kaybı).",
            "FMI 1 (Seviye <%2.5 / Depo Boş): Kırmızı stop lambası, Seviye 2 Kısıtlama: 5 mph (20 km/s) hız sınırlaması.",
        ],
        "steps": [
            ("AdBlue deposuna ISO 22241 standardında temiz DEF sıvısı ekleyin.", "AdBlue Deposu", "Kolay (Görsel)"),
            ("Ultrasonik şamandıra sensörünün soket voltajını ve CAN hattı iletişimini kontrol edin.", "DEF Seviye & Kalite Sensörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "AdBlue Şamandıra Skalası: %0 - %100 (0.4%/bit) | Refraktometre Üre Yoğunluğu: %32.5 ±%0.7.",
        "uds_routine": "J1939 Routine: DEF Dosing System Priming & Inducement Reset",
    },
    "SPN3251": {
        "title": "DPF Fark Basıncı Hatası (DPF Differential Pressure Delta-P)",
        "subsystem": "Ağır Vasıta DPF & Egzoz Sonrası İşlem",
        "severity": "MEDIUM",
        "causes": [
            "FMI 0 (Aşırı Kurum Tıkanıklığı): DPF basınç farkı >35 kPa; partikül filtresi dolu, rejenerasyon kilitlenmiş.",
            "FMI 1 (Filtre Delik/Yok): Basınç farkı <0.2 kPa; DPF peteği çatlak, içi boşaltılmış veya sökülmüş.",
            "FMI 2 (Hortum Ters/Tıkalı): Basınç boruları ters takılmış veya donmuş kondensat ile tıkanmış.",
        ],
        "steps": [
            ("DPF fark basınç sensörü silikon hortumlarında delinme veya erime olup olmadığını kontrol edin.", "DPF Basınç Hortumları", "Kolay (Görsel)"),
            ("Kurum yükü <40g ise cihaz üzerinden Park Halinde Manuel Servis Rejenerasyonu (Stationary DPF Regen) başlatın.", "Dizel Partikül Filtresi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Temiz DPF Rölanti Basıncı: 0.5 - 2.0 kPa | Tam Yük: 5.0 - 12.0 kPa | Tıkalı Limit: >25.0 kPa.",
        "uds_routine": "J1939 Service Routine: Stationary DPF Service Regeneration (PGN 64892)",
    },
    "SPN3364": {
        "title": "AdBlue (DEF) Sıvı Kalitesi Uygunsuz (DEF Quality / Concentration)",
        "subsystem": "Ağır Vasıta SCR & AdBlue Kalite Kontrol",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 18 (Kalite Düşük): AdBlue tankına su, mazot veya cam suyu karıştırılmış (Konsantrasyon <%28 veya >%38).",
            "FMI 2: Ultrasonik kalite sensöründe hava kabarcığı veya kristalleşme.",
        ],
        "steps": [
            ("Optik refraktometre ile depodaki sıvının üre konsantrasyonunu ölçün (Tam %32.5 olmalıdır).", "AdBlue Sıvısı", "Kolay (Görsel)"),
            ("Hatalı sıvı tespit edilirse depoyu komple boşaltın, deiyonize su ile çalkalayıp orijinal AdBlue doldurun.", "AdBlue Depo & Filtresi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Standart Üre Oranı: %32.5 ±%0.7 (ISO 22241). İndükleme Sayacı: 10 saat sonra 20 km/s hız kilidi.",
        "uds_routine": "J1939 Routine: DEF Quality Tampering Counter Reset Routine",
    },
    "SPN4364": {
        "title": "SCR DeNOx Dönüşüm Verimliliği Düşük (SCR Conversion Efficiency Low)",
        "subsystem": "Ağır Vasıta SCR Katalizör Verimliliği",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 1 (Verim <%45): SCR katalizörü kükürt veya motor yağı ile zehirlenmiş.",
            "FMI 18 (Verim %45-%75): AdBlue dozaj enjektörü kristalleşerek tıkanmış, DEF pompa basıncı düşük (<8.5 bar) veya çıkış NOx sensörü kaymış.",
        ],
        "steps": [
            ("AdBlue dozajlama enjektörünü söküp temizleyin; UDS üzerinden 3 dakikalık dozaj testini çalıştırın (110 - 135 mL gelmelidir).", "AdBlue Dozaj Enjektörü", "Orta (Alet Gerekir)"),
            ("Giriş (SPN 3216) ve Çıkış (SPN 3226) NOx sensör değerlerini motor freninde (0 mg enjeksiyon) karşılaştırın (İkisi de 0 ppm olmalıdır).", "NOx Sensörleri", "İleri (Servis)"),
        ],
        "measurement": "Nominal SCR DeNOx Verimliliği: >%90 | DEF Çalışma Basıncı: 9.0 ±0.5 Bar.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0302: DEF Dosing Quantity Measurement Test)",
    },
    "SPN651": {
        "title": "Silindir 1 Enjektör Devresi / Mekanik Arıza (Cylinder 1 Injector)",
        "subsystem": "Ağır Vasıta Common Rail Enjeksiyon",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 5 (Açık Devre): Enjektör bobin teli kopuk veya külbütör altı soketi çıkmış.",
            "FMI 6 (Aşırı Akım): Bobin sargısı kısa devre yapmış (ECU koruma için 1-2-3 silindir bankasını kapatır).",
            "FMI 7 (Mekanik Tepkisizlik): Enjektör iğnesi kapalı sıkışmış veya geri dönüşe aşırı yakıt kaçırıyor.",
        ],
        "steps": [
            ("Külbütör kapağı altındaki 1. silindir enjektör bobin direncini hassas miliohmmetre ile ölçün (0.35 - 0.55 Ω).", "1. Silindir Enjektörü", "Orta (Alet Gerekir)"),
            ("500V Megger ile bobin terminallerinin motor gövdesine izolasyonunu ölçün (>100 MΩ olmalıdır).", "Enjektör İzolasyonu", "İleri (Servis)"),
            ("10 saniyelik marş sırasında 1. enjektörün geri dönüş kaçak miktarını ölçün (Maks ≤ 5.0 mL).", "Geri Dönüş Hattı", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Solenoid Bobin Direnci: 0.35 - 0.55 Ω | İzolasyon: >100 MΩ | Marş Geri Dönüş: ≤ 5.0 mL / 10s.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0205: Automated Cylinder Cutout Test)",
    },
    "SPN1087": {
        "title": "EBS Servis Fren Devresi 1 Hava Basıncı (EBS Brake Circuit 1 Air Pressure)",
        "subsystem": "Ağır Vasıta EBS & Pnömatik Fren Sistemi",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 1 (Düşük Hava): Devre 1 hava basıncı <5.5 bar; hava kompresörü arızası, dört yollu emniyet valfi veya pnömatik kaçak.",
            "FMI 2 (Tutarsızlık): Devre 1 ile Devre 2 arasında frenleme anında >2.0 bar fark olması.",
        ],
        "steps": [
            ("Hava kurutucu tahliyesini ve dört yollu emniyet dağıtım valfini kaçak spreyi ile test edin.", "Dört Yollu Emniyet Valfi", "Kolay (Görsel)"),
            ("Kompresörün 0'dan 12 bara dolum süresini kronometre ile ölçün (Maksimum < 4 dakika).", "Pnömatik Hava Kompresörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Nominal Devre Basıncı: 10.0 - 12.5 Bar | Kırmızı İkaz & İmdat Eşiği: <5.5 Bar.",
        "uds_routine": "EBS Modulator Routine: Brake Cylinder Pressure Imbalance Calibration",
    },

    # ------------------ POWERTRAIN, GASOLINE & DIESEL EURO 6 ------------------
    "P0300": {
        "title": "Rastgele / Çoklu Silindir Ateşleme Hatası (Random/Multiple Cylinder Misfire)",
        "subsystem": "Ateşleme & Yakıt Enjeksiyon Sistemi",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Buji elektrot aşınması veya tırnak aralığının fabrika toleransından sapması.",
            "Ateşleme bobini sekonder sargı izolasyon kaçağı veya bobin soket korozyonu.",
            "Enjektör püskürtme deseni tıkanıklığı veya yakıt rayı basınç düşüklüğü.",
            "Krank mili (CKP) veya Kam mili (CMP) sensör sinyalinde CAN gürültüsü ve tork dalgalanması.",
        ],
        "steps": [
            ("Osilatör ekranında silindir ateşleme dalga boyunu ve krank sinyalini kontrol ediniz.", "Krank & Ateşleme Bobinleri", "Orta (Alet Gerekir)"),
            ("Enjektör dengeleme oranlarını ve yakıt rayı basıncını (UDS 0x22 DID 0x1102) ölçün.", "Yakıt Dağıtım Rayı", "Orta (Alet Gerekir)"),
            ("Bujilerin primer/sekonder direnç değerlerini ve kompresyon basıncını test edin.", "Silindir Yanma Odası", "İleri (Servis)"),
        ],
        "measurement": "Primer Bobin Direnci: 0.5 - 1.5 Ω | Sekonder: 5.0 - 15.0 kΩ | Kompresyon: >11.0 Bar (Benzin), >24.0 Bar (Dizel).",
        "uds_routine": "UDS Routine 0x31 (ID 0x0201: Silindir Kompresyon & Balans Testi)",
    },
    "P0087": {
        "title": "Yakıt Dağıtım Borusu Basıncı Çok Düşük (Fuel Rail Pressure Too Low)",
        "subsystem": "Yüksek Basınçlı Yakıt Enjeksiyon Sistemi (Common Rail)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Yüksek basınç yakıt pompası (HPFP / CP4) iç eleman aşınması veya debi kontrol valfi (VCV) tutukluğu.",
            "Yakıt filtresi parafinleşmesi veya tıkanıklığı nedeniyle emiş hattında vakum oluşması.",
            "Enjektör geri dönüş valflerinin aşırı sızdırması (Back-leakage).",
            "Basınç regülatörü (DRV) veya basınç tahliye valfinin (PRV) açık kalması.",
        ],
        "steps": [
            ("Yakıt filtresini kontrol ediniz ve alçak basınç besleme pompasının basıncını (min 4.5 bar) ölçünüz.", "Yakıt Filtresi & Depo Pompası", "Kolay (Görsel)"),
            ("Enjektörlerin geri dönüş miktarlarını dereceli kaplar ile ölçün (10 sn marşta maks 5 mL/enjektör).", "Common Rail Enjektörleri", "Orta (Alet Gerekir)"),
            ("Yüksek basınç pompası çıkış debisini ve ray basınç sensörü (SPN 157) sinyal voltajını osiloskopta doğrulayın.", "HPFP & Ray Basınç Sensörü", "İleri (Servis)"),
        ],
        "measurement": "Marş İçin Minimum Gerekli Ray Basıncı: ≥ 250 Bar (3600 psi) | Tam Yük: 1600 - 2200 Bar.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0203: Yüksek Basınç Yakıt Pompası Sızdırmazlık Testi)",
    },
    "P0234": {
        "title": "Turboşarj / Süperşarj Aşırı Takviye Basıncı (Engine Overboost Condition)",
        "subsystem": "Aşırı Doldurma & Hava Emiş Sistemi",
        "severity": "MEDIUM",
        "causes": [
            "Wastegate aktüatör kolunun mekanik olarak kapalı konumda sıkışması.",
            "N75 Boost kontrol selenoid valfinin elektriksel olarak açık kalması veya tıkanması.",
            "MAP / Takviye basınç sensörü (SPN 102) kalibrasyon sapması.",
            "Vakum hatlarında delinme veya çekvalf arızası.",
        ],
        "steps": [
            ("Wastegate aktüatör kolunu vakum pompası (Mityvac) ile test edin (0.6 barda tam açılmalıdır).", "Wastegate Aktüatörü", "Orta (Alet Gerekir)"),
            ("N75 selenoid valf bobin direncini ölçün (25 - 35 Ω) ve PWM sürücü sinyalini osiloskopta izleyin.", "N75 Boost Selenoidi", "Orta (Alet Gerekir)"),
            ("MAP sensörü canlı verisini motor kapalıyken barometrik sensör ile karşılaştırın (fark <15 hPa).", "MAP Sensörü", "Kolay (Görsel)"),
        ],
        "measurement": "N75 Bobin Direnci: 25 - 35 Ω | Vakum Tutma: -0.8 Bar'da 1 dakika boyunca düşmemeli.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0204: VGT / Wastegate Aktüatör Histerezis Testi)",
    },
    "P0016": {
        "title": "Krank - Kam Mili Pozisyon Korelasyon Hatası (Crank/Cam Correlation Bank 1)",
        "subsystem": "Motor Mekanik & Zamanlama",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Triger kayışı/zincirinde uzama, senteden atlama veya gergide gevşeme.",
            "VVT değişken subap zamanlama selenoidinin yağ çamuru ile tıkanması.",
            "Krank kasnağı harmonik damper kauçuğunun sıyırması.",
        ],
        "steps": [
            ("Osiloskop ile Krank (CKP) ve Kam (CMP) sinyallerini eşzamanlı kaydedip faz açısını inceleyin.", "Zamanlama Sensörleri", "İleri (Servis)"),
            ("VVT selenoid valfini söküp mikro filtresindeki çapak ve yağ çamurunu temizleyin.", "VVT Selenoid Valfi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Faz Senkronizasyon Sapma Limiti: < ±4.0° Krank Açısı.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0150: VVT Camshaft Phase Angle Adaptation)",
    },
    "P0420": {
        "title": "Katalitik Konvertör Sistemi Verimliliği Eşik Altında (Catalyst System Efficiency)",
        "subsystem": "Egzoz Emisyon & Katalitik Konvertör",
        "severity": "LOW",
        "causes": [
            "Katalizör monolitinin kurşun/yağ ile zehirlenmesi veya seramik peteğin erimesi.",
            "Arka (Downstream) oksijen sensörünün (O2S Bank 1 Sensor 2) sinyal dalgalanması.",
            "Egzoz manifoldu veya esnek spiral boruda hava kaçağı.",
        ],
        "steps": [
            ("Canlı telemetride ön ve arka oksijen sensör voltajlarını karşılaştırın (Arka sensör 0.6 - 0.7V sabit kalmalıdır).", "O2 Sensörü 2", "Orta (Alet Gerekir)"),
            ("Egzoz hattında spiral ve flanş kaçaklarını duman testi ile kontrol edin.", "Egzoz Spiral Borusu", "Kolay (Görsel)"),
        ],
        "measurement": "Sağlıklı Arka Lambda Voltajı: 0.60V - 0.75V (Sabit) | Arızalı: Ön sensör gibi 0.1V - 0.9V salınım.",
        "uds_routine": "UDS Service 0x19 (Subfunction 0x04: Freeze Frame & Catalyst Bed Temp)",
    },

    # ------------------ ADAS, CAN-FD & CHASSIS ------------------
    "C1A00": {
        "title": "Ön Radar Sensörü Hizalama Hatası (Forward Radar Alignment Error)",
        "subsystem": "ADAS & Sürüş Destek Sistemleri (CAN-FD)",
        "severity": "MEDIUM",
        "causes": [
            "Ön tampon darbesi sonrası radar braketinin açısal olarak kayması (>1.5°).",
            "Radar önündeki amblem veya plastik radom üzerinde yoğun kar/çamur kaplaması.",
        ],
        "steps": [
            ("Radar kapağındaki yabancı cisim ve buz tabakasını temizleyin.", "Ön Radar Kapağı", "Kolay (Görsel)"),
            ("Lazer ve hedef reflektör panosu (Doppler Reflector) kullanarak statik radar kalibrasyonunu başlatın.", "Radar Braketi", "İleri (Servis)"),
        ],
        "measurement": "Maksimum İzin Verilen Açısal Sapma: Yatayda < ±0.8°, Düşeyde < ±0.5°.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0501: ADAS Front Radar Dynamic Alignment Routine)",
    },
    "U0100": {
        "title": "Motor Kontrol Ünitesi (ECM) ile İletişim Kaybı (Lost Communication With ECM)",
        "subsystem": "CAN-Bus Omurga İletişim Hatası",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Motor beyni ana besleme sigortası (KL30/KL15) veya ana güç rölesi yanmış.",
            "CAN_H veya CAN_L hatlarında kopukluk veya şasiye kısa devre.",
            "Motor beyni ana şasi kablosunun (KL31) gevşemesi/paslanması.",
        ],
        "steps": [
            ("ECM ana besleme sigortalarını ve motor kontrol rölesini (Main Relay) multimetre ile test edin.", "Motor Sigorta Kutusu", "Kolay (Görsel)"),
            ("OBD soketinde Pin 6 (CAN_H) ve Pin 14 (CAN_L) arasındaki direnci ölçün (60 Ω olmalıdır).", "OBD-II Portu (Pin 6/14)", "Orta (Alet Gerekir)"),
            ("Motor beyni gövde şasi pini ile akü eksi kutbu arasındaki voltaj düşümünü ölçün (<50 mV olmalıdır).", "ECM Şasi Bağlantısı", "Orta (Alet Gerekir)"),
        ],
        "measurement": "CAN Omurga Direnci: 60.0 Ω ±%5 | Şasi Voltaj Düşümü: <50 mV DC.",
        "uds_routine": "UDS Service 0x28 (CommunicationControl: EnableRxAndTx 0x00)",
    },
    "U0126": {
        "title": "Direksiyon Açı Sensörü (SAS) ile İletişim Kaybı (Lost Comm with SAS)",
        "subsystem": "Şasi, ESP & Direksiyon Açı Sensörü",
        "severity": "MEDIUM",
        "causes": [
            "Direksiyon zembereği içindeki SAS optik okuyucusunun sıfır noktasını kaybetmesi.",
            "Direksiyon kolonu CAN alt ağı kablo temassızlığı.",
        ],
        "steps": [
            ("Direksiyonu tam sol ve tam sağ yaparak sıfır noktası adaptasyonunu gerçekleştirin.", "Direksiyon Simidi", "Kolay (Görsel)"),
            ("SAS CAN besleme soketindeki 12V ve GND pinlerini ölçün.", "SAS Modül Soketi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Düz Konum Açı Toleransı: 0.0° ±1.5° | Besleme: 12.0 - 14.5V DC.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0402: Steering Angle Sensor Zero Calibration)",
    },
    "U0415": {
        "title": "ABS / Fren Kontrol Modülünden Geçersiz Veri Alındı (Invalid Data From ABS)",
        "subsystem": "Fren Kontrol (ABS/ESP) & Çekiş Kontrolü",
        "severity": "MEDIUM",
        "causes": [
            "Tekerlek hız sensörlerinden birinde (WSS) sinyal atlaması veya porya manyetik halkasında paslanma.",
            "Lastik ebatları veya yuvarlanma çapları arasında >%3 fark olması.",
        ],
        "steps": [
            ("Dört tekerleğin hız sensörü canlı sinyallerini osiloskop veya canlı grafikten izleyin.", "Tekerlek Hız Sensörleri", "Orta (Alet Gerekir)"),
            ("Porya bilyası üzerindeki manyetik enkoder halkasını temizleyin.", "Porya Enkoder Halkası", "Kolay (Görsel)"),
        ],
        "measurement": "Hall Hız Sensörü Akım Seviyeleri: Düşük = 7 mA, Yüksek = 14 mA.",
        "uds_routine": "UDS Service 0x22 (DID 0x0310: 4-Wheel Speed Synchronous Vector)",
    },

    # ------------------ MARINE & NMEA 2000 ------------------
    "N2K_IMPELLER": {
        "title": "Deniz Suyu Çark (İmpeller) Arızası & Anlık Hararet (Raw Water Impeller Failure)",
        "subsystem": "Marin Motor Çift Devreli Soğutma (NMEA 2000)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Lastik impeller kanatlarının kuru çalışma veya aşınma nedeniyle parçalanması (Su debisi sıfıra indi).",
            "Deniz suyu emiş filtresinin (Sea Strainer) poşet/deniz anası ile tamamen tıkanması.",
            "Kinseft vanasının (Seacock) kapalı unutulması.",
        ],
        "steps": [
            ("Motoru derhal stop edin! Kinseft vanasının açık olduğunu ve deniz suyu filtresini kontrol edin.", "Deniz Suyu Filtresi (Strainer)", "Kolay (Görsel)"),
            ("Deniz suyu pompası kapağını söküp kauçuk impeller kanatlarını kontrol edin; kopan parçaları eşanjör girişinde arayın.", "Deniz Suyu Pompası", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Termal Gradyan Eşiği: dT/dt > 1.5°C/saniye (Rölantide dahi saniyeler içinde 100°C üzerine fırlar).",
        "uds_routine": "NMEA 2000 PGN 127489 (Engine Dynamic) & PGN 130310 (Water Temp)",
    },
    "N2K_EXHAUST_ELBOW": {
        "title": "Islak Egzoz Karışım Dirseği Aşırı Sıcaklık (Wet Exhaust Mixing Elbow Overheat)",
        "subsystem": "Marin Egzoz & Yangın Güvenliği",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Egzoz dirseği su püskürtme deliklerinin (spray ring) kireç ve pas ile tıkanması.",
            "Ham su enjeksiyonunun kesilmesi nedeniyle 550°C'lik kuru egzoz gazının doğrudan susturucuya geçmesi.",
        ],
        "steps": [
            ("Egzoz dirseğine gelen su besleme hortumunu söküp su çıkışını test edin.", "Egzoz Karışım Dirseği", "Kolay (Görsel)"),
            ("Fiberglas susturucu ve kauçuk egzoz hortumunun sıcaklığını kontrol edin (85°C üzeri erime riski taşır).", "Fiberglas Susturucu (Waterlock)", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Güvenli Çalışma: 40°C - 65°C | Alarm: ≥75°C | Kritik Erime & Su Alma Tehlikesi: >105°C.",
        "uds_routine": "NMEA 2000 PGN 127489 (Exhaust Gas Temperature & Discrete Alarm)",
    },
    "N2K_HEAT_EXCHANGER": {
        "title": "Marin Eşanjör Kireçlenmesi & Yüksek Yükte Hararet (Heat Exchanger Scaling)",
        "subsystem": "Marin Isı Değiştirici & Termal Kapasite",
        "severity": "MEDIUM",
        "causes": [
            "Bakır-nikel eşanjör boru demetinin içinde kalsiyum karbonat (CaCO3) ve midye tabakası oluşması.",
            "Rölantide ve düşük devirde hararet yapmazken, %75 üzeri gazda (WOT) soğutma kapasitesinin yetersiz kalması.",
        ],
        "steps": [
            ("Eşanjör kapaklarını söküp boru demetini (tube bundle) özel asit/kireç çözücü solüsyon ile temizleyin (Rydlyme).", "Marin Eşanjör Boru Demeti", "İleri (Servis)"),
            ("Çinko tutyaları (Anodes) kontrol edip %50'den fazla erimişse yenileriyle değiştirin.", "Çinko Kurban Anotlar", "Kolay (Görsel)"),
        ],
        "measurement": "Eşanjör Sıcaklık Düşüşü: Sağlıklı ΔT = 8°C - 12°C | Kireçli Arızalı ΔT < 4°C.",
        "uds_routine": "NMEA 2000 PGN 127489 (Engine Load % vs Coolant Temp Delta)",
    },
    "N2K_PROP_SLIP": {
        "title": "Pervane Kavitasyonu / Yüksek Kayma Oranı (Propeller Slip & Cavitation)",
        "subsystem": "Marin Hidrodinamik & Sevk Sistemi",
        "severity": "MEDIUM",
        "causes": [
            "Pervane kanatlarında eğilme, çentik veya kauçuk göbek (hub) sıyırması.",
            "Yüksek torkta pervanenin su tutuşunu kaybetmesi (Slip >%35).",
            "Gövde altında yoğun kekamoz (marine growth) ve sürtünme direnci artışı.",
        ],
        "steps": [
            ("Pervaneyi dalgıç veya karada kontrol edin; kanat hatvesinde eğrilik ve kavitasyon korozyonunu inceleyin.", "Gemi Pervanesi & Şaftı", "Kolay (Görsel)"),
            ("SOG (GPS Hızı) ile Şaft Devri × Hatve teorik hızını karşılaştırıp dinamik kayma oranını hesaplayın.", "GPS & Şaft Hız Sensörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Pervane Slip Formülü: Slip% = (1 - (SOG × 1215.22 / (RPM/Ratio × Pitch))) × 100 | Normal Kayan Gövde: %10 - %18.",
        "uds_routine": "NMEA 2000 PGN 128259 (Speed Water Ref) & PGN 129026 (SOG Rapid)",
    },

    # ------------------ CAN PHYSICAL LAYER & OSCILLOSCOPE FORENSICS ------------------
    "CAN_TERM_60": {
        "title": "CAN-Bus 120Ω Sonlandırma Direnci Hatası (CAN Termination Fault)",
        "subsystem": "CAN Fiziksel Katman (ISO 11898-2)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "120 Ω okunuyorsa: Hat ucundaki iki adet 120Ω sonlandırma direncinden biri kopuk veya soketi çıkmış.",
            "0 - 10 Ω okunuyorsa: CAN_H ve CAN_L kabloları birbirine kısa devre.",
            "Sonsuz (Açık Devre): Hat üzerindeki iki sonlandırma direnci de kopuk veya ana omurga hattı kesik.",
            "30 - 40 Ω okunuyorsa: Hatta yanlışlıkla 3. veya 4. bir paralel 120Ω direnç takılmış.",
        ],
        "steps": [
            ("Akü kutup başını veya kontağı KAPATIN. OBD soketi Pin 6 (CAN_H) ile Pin 14 (CAN_L) arasını ohmmetre ile ölçün.", "OBD-II Portu (Pin 6/14)", "Kolay (Görsel)"),
            ("60.0 Ω okunmalıdır. 120 Ω ise hat sonundaki ECU'ların (Motor Beyni ve Gösterge/ABS) soketlerini kontrol edin.", "Omurga Sonlandırma Dirençleri", "Orta (Alet Gerekir)"),
            ("Osiloskopta kare dalga köşelerindeki çınlama (ringing/reflection) genliğini kontrol edin.", "CAN Diferansiyel Sinyali", "İleri (Servis)"),
        ],
        "measurement": "Standart Eşdeğer Direnç: 60.0 Ω ±%5 (120Ω // 120Ω) | Hata Toleransı: 55 Ω - 65 Ω.",
        "uds_routine": "ISO 11898-2 Physical Layer Multimeter Verification",
    },
    "CAN_VOLT_FAULT": {
        "title": "CAN Fiziksel Katman Voltaj Anomalisi (CAN Bias / Ground Offset Fault)",
        "subsystem": "CAN Fiziksel Katman Elektriksel Teşhis",
        "severity": "CRITICAL_STOP",
        "causes": [
            "CAN_H voltajı 3.5V yerine 12V/24V akü voltajına oturmuş (Artıya kısa devre).",
            "CAN_L voltajı 1.5V yerine 0V şasiye yapışmış (Şasiye kısa devre).",
            "Düğümler arası şasi potansiyel farkı (Ground Offset) >2.0V üzerine çıkmış.",
        ],
        "steps": [
            ("Kontak AÇIK durumdayken Pin 6 (CAN_H) ve Pin 14 (CAN_L) voltajlarını şasiye göre ayrı ayrı ölçün.", "CAN Hat Voltajları", "Orta (Alet Gerekir)"),
            ("Normal Resesif (Boşta): CAN_H = 2.5V, CAN_L = 2.5V (V_diff = 0.0V).", "Diferansiyel Denge", "Orta (Alet Gerekir)"),
            ("Normal Dominant (Veri Anı): CAN_H = 3.5V, CAN_L = 1.5V (V_diff = 2.0V).", "Veri İletim Seviyesi", "İleri (Servis)"),
        ],
        "measurement": "Resesif: 2.50V ±0.15V | Dominant CAN_H: 3.50V ±0.25V | Dominant CAN_L: 1.50V ±0.25V | V_diff: 2.00V ±0.30V.",
        "uds_routine": "Oscilloscope 10x Differential Probing Mode",
    },
}

# ============================================================================
# DYNAMIC DIAGNOSTIC DATABASE LOADER & TELEMETRY ACCESSORS
# ============================================================================

def _resolve_external_data_dir() -> Path:
    """Resolve the external diagnostics data directory (H-9 / P1-11).

    Frozen PyInstaller builds resolve `__file__` inside the _MEIPASS
    extraction directory, so a bare `Path(__file__).parents[3]` misses the
    bundled data — silently degrading the DTC knowledge base from ~1870 to
    34 hardcoded rules. Prefer the frozen bundle root first, then fall back
    to the repository layout.
    """
    import sys

    if getattr(sys, "frozen", False):
        frozen_root = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
        frozen_dir = frozen_root / "data" / "diagnostics"
        if frozen_dir.is_dir():
            return frozen_dir
    return Path(__file__).resolve().parents[3] / "data" / "diagnostics"


_EXTERNAL_DATA_DIR: Path = _resolve_external_data_dir()
_CACHED_J1939_DB: dict[str, Any] | None = None
_CACHED_NHTSA_COMPLAINTS_DB: dict[str, Any] | None = None
_CACHED_UDS_DID_DB: dict[str, Any] | None = None
_CACHED_MODE06_DB: dict[str, Any] | None = None
_CACHED_EXTENDED_PID_DB: dict[str, Any] | None = None

# ---------------------------------------------------------------------------
# T1-1: live DTC catalog size.
#
# The desktop KPI bridge used to hardcode the coverage denominator as
# ``total_catalog = 14352``. That literal is a fabricated measurement: it
# silently drifts as the DB grows/shrinks and it turns a missing catalog into
# an impressive-looking coverage ratio. Measured live instead, memoised per
# process because the JSON is large and read on every KPI refresh.
#
# Fail-closed contract: a missing/unreadable catalog returns 0, NOT a magic
# number (AGENTS.md §2.3 — never invent a measurement). 0 is an honest
# "unknown denominator" that callers must handle, not silently divide by.
# ---------------------------------------------------------------------------
_DTC_CATALOG_SIZE_LOCK = threading.Lock()
_DTC_CATALOG_SIZE_CACHE: dict[str, int] = {}


def catalog_size(*, force_reload: bool = False) -> int:
    """Return the number of records in the live DTC catalog (``dtc_database.json``).

    Reads ``_EXTERNAL_DATA_DIR / "dtc_database.json"`` — the same file the
    engine merges into ``EXPERT_KNOWLEDGE_BASE`` — so the reported catalog size
    is the real denominator, never a hardcoded literal. Returns 0 when the file
    is absent or unparseable (fail-closed, no fabricated value).
    """
    if not force_reload and "value" in _DTC_CATALOG_SIZE_CACHE:
        return _DTC_CATALOG_SIZE_CACHE["value"]
    with _DTC_CATALOG_SIZE_LOCK:
        if not force_reload and "value" in _DTC_CATALOG_SIZE_CACHE:
            return _DTC_CATALOG_SIZE_CACHE["value"]
        size = 0
        try:
            target = _EXTERNAL_DATA_DIR / "dtc_database.json"
            if target.exists():
                payload = json.loads(target.read_text(encoding="utf-8", errors="replace"))
                if isinstance(payload, dict):
                    size = len(payload)
                elif isinstance(payload, list):
                    size = len(payload)
        except Exception as exc:  # noqa: BLE001 — unreadable catalog must not break the UI
            logger.warning("DTC catalog size unreadable (%s) — reporting 0", exc)
            size = 0
        _DTC_CATALOG_SIZE_CACHE["value"] = size
        return size


def _validate_dtc_entry_shape(code: str, info: Any) -> bool:
    """Shape-validate one external DTC entry before merging (M-17 / P2-16).

    A hostile or corrupted external DB must not inject garbage into the
    expert base: `steps` must be a list of 2-3 element sequences of strings
    (a bare string used to be character-indexed by the consumer), `severity`
    must be a known level, and the required top-level fields must be present.
    """
    if not isinstance(info, dict):
        return False
    for required_field in ("title", "subsystem", "severity", "dtc_namespace", "dtc_class"):
        val = info.get(required_field)
        if not isinstance(val, str) or not val.strip():
            return False
    dtc_namespace = info.get("dtc_namespace")
    if dtc_namespace not in ("SAE_J2012", "SAE_J2012_4", "ISO_14229_1", "OBD", "WWH_OBD", "J1939", "OEM"):
        return False
    dtc_class = info.get("dtc_class")
    if dtc_class not in ("NoClass", "A", "B1", "B2", "C"):
        return False
    severity = info.get("severity")
    # P0-1: UNKNOWN is a first-class rung (Severity.UNKNOWN -> GRAY, never
    # GREEN). The severity rebuild emits it for codes the SAE J2012 rule
    # table cannot classify, so the validator must accept it.
    if severity not in ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL", "CRITICAL_STOP", "UNKNOWN"):
        return False
    steps = info.get("steps")
    if steps is not None:
        if not isinstance(steps, (list, tuple)):
            return False
        for s in steps:
            if not isinstance(s, (list, tuple)) or not 2 <= len(s) <= 3:
                return False
            if not all(isinstance(part, str) for part in s):
                return False
    causes = info.get("causes")
    if causes is not None and not isinstance(causes, (list, tuple)):
        return False
    prov = info.get("provenance")
    if prov is not None:
        if not isinstance(prov, (list, tuple)):
            return False
        for p in prov:
            if not isinstance(p, dict):
                return False
            if not p.get("provenance_id") or not str(p.get("provenance_id")).startswith("prv-"):
                return False
    _ = code  # validated by caller (dict key, always str from JSON)
    return True


def _derive_severity(code: str, info: dict[str, Any]) -> str | None:
    """Derive a DTC severity rung from the SAE J2012 rule table (P0-1).

    Measured at HEAD a6f7477 the scraped `severity` stamped CRITICAL_STOP on
    single-cylinder misfires (P0301-P0312 while P0300 was MEDIUM), HO2S heater
    circuits and wiper-relay codes; CRITICAL_STOP drives decide_risk -> RED ->
    "stop the vehicle and switch off the engine". The rung is now DERIVED from
    the source-independent rule table; an unclassifiable code resolves to
    UNKNOWN (-> GRAY, never GREEN) so it fails safe (AGENTS.md 2.3).
    """
    try:
        from src.engine.ai.severity_rules import load_severity_rules, resolve_severity

        if not load_severity_rules():
            return None
        return resolve_severity(
            code,
            str(info.get("title") or ""),
            str(info.get("subsystem") or ""),
        ).value
    except Exception as exc:  # noqa: BLE001 - a resolver fault must not kill the KB load
        logger.warning("Severity rule resolution failed for %s: %s", code, exc)
        return None


def _reconcile_knowledge_base_severities() -> int:
    """Apply the SAE J2012 rule table to every OBD-II KB entry (P0-1).

    Built-in entries win over the external DB, so the reconciliation must run
    over the merged base - otherwise the authoritative hand-crafted rungs
    (P0300=CRITICAL_STOP, P0234=MEDIUM) keep their inconsistency. The prior
    rung is preserved under `_source_severity` for auditability. J1939 SPN keys
    are skipped: the table has no authority over them.
    """
    try:
        from src.engine.ai.severity_rules import (
            is_obd_code,
            load_severity_rules,
            resolve_severity,
        )

        if not load_severity_rules():
            return 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("Severity reconciliation unavailable: %s", exc)
        return 0

    changed = 0
    for code, entry in list(EXPERT_KNOWLEDGE_BASE.items()):
        if not isinstance(entry, dict):
            continue
        if not is_obd_code(code):
            continue
        try:
            derived = resolve_severity(
                code,
                str(entry.get("title") or ""),
                str(entry.get("subsystem") or ""),
            ).value
        except Exception:  # noqa: BLE001 - one bad entry must not abort the sweep
            continue
        current = str(entry.get("severity") or "")
        if not current:
            continue
        if derived != current:
            if "_source_severity" not in entry:
                entry["_source_severity"] = current
            entry["severity"] = derived
            changed += 1
        if "dtc_namespace" not in entry:
            from src.core.models.diagnostics import classify_dtc_namespace
            entry["dtc_namespace"] = classify_dtc_namespace(code).value
        if "dtc_class" not in entry:
            from src.core.models.diagnostics import classify_dtc_class
            entry["dtc_class"] = classify_dtc_class(code, entry.get("severity", "UNKNOWN")).value
    return changed


def load_external_dtc_database(data_path: Path | str | None = None) -> int:
    """Dynamically load and merge external DTC catalog into EXPERT_KNOWLEDGE_BASE.

    Preserves hardcoded expert rules (existing entries are never overwritten).
    Returns the number of new DTC codes merged.
    """
    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "dtc_database.json"
    if not target.exists():
        # H-9 (P1-11): missing DB is a DEGRADED mode, not business as usual —
        # debug level hid the loss of ~98% of the knowledge base in frozen
        # builds. Surface it at WARNING so operators can notice.
        logger.warning("External DTC database not found at %s — knowledge base degraded to built-in rules", target)
        return 0

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if not isinstance(data, dict):
            logger.warning("External DTC database format invalid (expected dict, got %s)", type(data).__name__)
            return 0

        added = 0
        rejected = 0
        sanitized = 0
        enriched = 0
        # T67-D (measured 2026-09-22): `code not in EXPERT_KNOWLEDGE_BASE` was a
        # key-presence test, so every code hand-written in the literal was never
        # enriched by its 14k-record external counterpart. 17 builtin keys were
        # shadowed, each losing 6-10 populated fields — P0300 alone dropped
        # procedures_full, causes_en, description_en, symptoms_en, oem_variants,
        # gm_monitor, nhtsa_evidence, vag_code, evidence_url and title_en.
        #
        # The fix is FILL-IF-EMPTY, never overwrite: a hand-curated value wins,
        # and only fields the builtin leaves empty are taken from the DB. This
        # keeps the curated EV/HV and CAN-physical rules authoritative while
        # making the harvested evidence reachable. Quarantine still applies —
        # an enriched record passes the same gate as a fresh one.
        for code, info in data.items():
            # M-17 (P2-16): shape validation BEFORE merge — garbage entries
            # are counted and skipped, never merged.
            if not _validate_dtc_entry_shape(code, info):
                rejected += 1
                continue
            # Preserve existing rich hand-crafted rules
            if code not in EXPERT_KNOWLEDGE_BASE:
                # F-24 (P0, wiring gate): the quarantine gate existed but was
                # never on the production path, so scraped JavaScript/JSON-LD
                # artifacts ("p2032 }}") were merged verbatim into the live
                # knowledge base. Route every externally-sourced record through
                # QuarantineGatekeeper and merge ONLY the sanitized result. The
                # gate is read-only analysis; a record that fails quarantine is
                # dropped (fail-closed) rather than trusted.
                #
                # NOTE: the DB stores the DTC code as the dict KEY, not inside
                # the record — inject it so validate_dtc_record's DTC_PATTERN
                # check sees real input instead of rejecting all 14k entries.
                try:
                    from src.engine.ai.harvest_validator import validate_dtc_record

                    report = validate_dtc_record({**info, "code": code})
                except Exception as exc:  # noqa: BLE001 — never lose the KB to a validator bug
                    logger.warning("Quarantine gate error for %s: %s", code, exc)
                    report = None
                if report is None or not report.is_valid or not report.sanitized:
                    rejected += 1
                    continue
                clean = report.sanitized
                # Keep the hand-curated metadata that the gate does not model
                # (subsystem/severity/steps/title) — those were shape-validated
                # above — but take symptoms/causes ONLY from the sanitized set.
                merged = dict(info)
                merged["symptoms"] = clean.get("symptoms", [])
                merged["causes"] = clean.get("causes", info.get("causes", []))
                # P0-1: the scraped `severity` is NOT trusted - it is derived
                # from the SAE J2012 rule table (see _derive_severity).
                derived_severity = _derive_severity(code, info)
                if derived_severity is not None:
                    if merged.get("severity") != derived_severity and "_source_severity" not in merged:
                        merged["_source_severity"] = str(info.get("severity") or "")
                    merged["severity"] = derived_severity
                if merged["symptoms"] != info.get("symptoms") or merged["causes"] != info.get("causes"):
                    sanitized += 1
                EXPERT_KNOWLEDGE_BASE[code] = merged
                added += 1
                continue

            # T67-D: the code exists in the builtin literal — enrich it with
            # every field the builtin leaves empty, and never overwrite one it
            # fills. The same quarantine gate runs first so an enriched record
            # is exactly as trustworthy as a fresh one.
            _cur = EXPERT_KNOWLEDGE_BASE[code]
            if not isinstance(_cur, dict):
                continue
            try:
                from src.engine.ai.harvest_validator import validate_dtc_record

                _rep = validate_dtc_record({**info, "code": code})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Quarantine gate error for %s: %s", code, exc)
                _rep = None
            if _rep is None or not _rep.is_valid or not _rep.sanitized:
                rejected += 1
                continue
            _gained = 0
            for _field, _value in info.items():
                if _field in ("symptoms", "causes", "severity"):
                    # Sanitized values only, and still fill-if-empty.
                    if _field == "symptoms":
                        _value = _rep.sanitized.get("symptoms", [])
                    elif _field == "causes":
                        _value = _rep.sanitized.get("causes", _value)
                if _value in (None, "", [], {}):
                    continue
                if _cur.get(_field) in (None, "", [], {}):
                    _cur[_field] = _value
                    _gained += 1
            if _gained:
                enriched += 1

        # P0-1 (cont.): reconcile the WHOLE base, since built-in entries carry
        # the same defect and would otherwise never be corrected.
        reconciled = _reconcile_knowledge_base_severities()
        if reconciled:
            logger.info(
                "P0-1 severity reconciliation: %d knowledge-base rungs derived from the SAE J2012 rule table",
                reconciled,
            )

        if rejected:
            logger.warning(
                "External DTC database: %d entries rejected by shape validation/quarantine", rejected
            )
        if sanitized:
            logger.info("External DTC database: %d entries sanitized by quarantine gate", sanitized)
        if enriched:
            logger.info(
                "T67-D: %d builtin knowledge-base entries enriched from the external DB "
                "(fill-if-empty; curated values never overwritten)",
                enriched,
            )
        logger.info("Merged %d external DTC codes into EXPERT_KNOWLEDGE_BASE (total: %d)", added, len(EXPERT_KNOWLEDGE_BASE))
        return added
    except Exception as exc:
        logger.warning("Failed to load external DTC database from %s: %s", target, exc)
        return 0


def get_j1939_spn_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return SAE J1939 SPN & FMI fault knowledge base."""
    global _CACHED_J1939_DB
    if _CACHED_J1939_DB is not None and data_path is None:
        return _CACHED_J1939_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "j1939_spn_fmi_database.json"
    if not target.exists():
        logger.warning("J1939 SPN/FMI database not found at %s — J1939 fault decoding degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_J1939_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load J1939 database: %s", exc)
    return {}


def get_nhtsa_complaints_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return the NHTSA owner-complaint corpus (per-DTC field evidence).

    REVIEW (Tur-27 P2, @tuner AI plan Boguluk 7): the 5.9 MB complaint corpus was
    merged into data/diagnostics but had no accessor — no code could reach it.
    Read-only: no network/HAL imports (AI-TX isolation invariant preserved).
    """
    global _CACHED_NHTSA_COMPLAINTS_DB
    if _CACHED_NHTSA_COMPLAINTS_DB is not None and data_path is None:
        return _CACHED_NHTSA_COMPLAINTS_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "nhtsa_can_complaints_database.json"
    if not target.exists():
        logger.warning("NHTSA complaints database not found at %s", target)
        return {}

    try:
        data = json.loads(target.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_NHTSA_COMPLAINTS_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load NHTSA complaints database: %s", exc)
    return {}


def get_uds_did_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return OEM-specific UDS Data Identifier (DID) telemetry catalog."""
    global _CACHED_UDS_DID_DB
    if _CACHED_UDS_DID_DB is not None and data_path is None:
        return _CACHED_UDS_DID_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "uds_did_database.json"
    if not target.exists():
        logger.warning("UDS DID database not found at %s — DID telemetry catalog degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_UDS_DID_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load UDS DID database: %s", exc)
    return {}


# ----------------------------------------------------------------------------
# T68: UDS DID decode helpers.
#
# `uds_did_database.json` carried 68 DIDs with full decode metadata
# (byte_length / data_type / scaling / offset / unit) and had NO consumer
# anywhere in the engine — `desktop_app.py:642` only counted the rows. The two
# packet handlers below used a hardcoded 9-entry dict instead, so 59 DIDs were
# invisible and a positive 0x62 response was never decoded beyond VIN.
#
# Both helpers are read-only, offline and deterministic. A missing DB degrades
# to the builtin name table (fail-safe) — no fabricated decode is ever emitted.
# ----------------------------------------------------------------------------


def _uds_did_row(did_int: int) -> dict[str, Any] | None:
    """Return the DID record for ``did_int`` from the DID database, or None.

    The catalog mixes key casings — measured 2026-09-22: 36 keys are lowercase
    ``"0x1153"`` and 32 are uppercase ``"0XF180"``. A case-sensitive lookup
    therefore resolved only 36 of the 68 DIDs, silently degrading the other 32
    (every ISO 14229 universal identifier among them) back to bare hex. The
    lookup is case-insensitive on both the prefix and the digits.
    """
    try:
        db = get_uds_did_database()
    except Exception:  # noqa: BLE001 — a broken catalog must not break packet forensics
        return None
    dids = db.get("dids") if isinstance(db, dict) else None
    if not isinstance(dids, dict):
        return None
    wanted = {f"0x{did_int:04X}".lower(), str(did_int)}
    for key, row in dids.items():
        if not isinstance(row, dict):
            continue
        if str(key).strip().lower() in wanted:
            return row
    return None


def _uds_did_decode_hint(row: dict[str, Any]) -> str:
    """Build an honest decode recipe from a DID record's own fields.

    Only fields actually present are used. The output names the raw type, the
    byte count, and the scale/offset/unit when the record carries them, so the
    operator can convert the answer bytes themselves. Nothing is computed from
    a value the record does not state.
    """
    parts: list[str] = []
    dtype = str(row.get("data_type", "") or "").strip()
    blen = row.get("byte_length")
    if dtype or blen:
        _t = dtype or "raw"
        if isinstance(blen, int) and blen > 0:
            _t += f", {blen} bayt"
        parts.append(_t)
    scaling = row.get("scaling")
    offset = row.get("offset")
    unit = str(row.get("unit", "") or "").strip()
    if isinstance(scaling, (int, float)) and scaling not in (0, 1.0):
        _s = f"× {scaling}"
        if isinstance(offset, (int, float)) and offset:
            _s += f" {'+' if offset > 0 else '-'} {abs(offset)}"
        if unit:
            _s += f" {unit}"
        parts.append(_s)
    elif unit:
        parts.append(f"birim: {unit}")
    elif isinstance(offset, (int, float)) and offset:
        parts.append(f"offset {offset}")
    return " | ".join(parts)


def _uds_did_decode_value(row: dict[str, Any], data: bytes) -> str:
    """Decode a positive 0x62 response body using the DID record's own recipe.

    Returns a human-readable value, or "" when the record does not describe
    enough to decode honestly. Integer and ASCII payloads are supported — the
    two forms the catalog's `data_type` values actually name. An unsupported
    type yields "" rather than a guess (AGENTS.md §2.3).

    Identifier DIDs are ASCII by DEFINITION, not by their record's
    `data_type`: ISO 14229-1 Annex F specifies F180-F19F as textual
    identifiers (VIN, part number, ECU serial, system name). Measured
    2026-09-22: the catalog's 32 universal entries carry no `data_type` at
    all, so a byte-count-only decode turned the VIN fragment "WVW" into the
    integer 5723735. The identifier range is therefore treated as ASCII when
    the bytes are printable — and falls back to the numeric path when they
    are not, so a genuinely binary payload is never mis-rendered as text.
    """
    if not data:
        return ""
    dtype = str(row.get("data_type", "") or "").strip().lower()
    blen = row.get("byte_length")
    if not isinstance(blen, int) or blen <= 0:
        blen = len(data)
    body = data[:blen]
    scaling = row.get("scaling")
    offset = row.get("offset")
    unit = str(row.get("unit", "") or "").strip()

    _is_identifier_range = False
    _did_int = row.get("did_int")
    if isinstance(_did_int, int):
        _is_identifier_range = 0xF180 <= _did_int <= 0xF19F
    _printable = bool(body) and all(32 <= b <= 126 for b in body)

    if "ascii" in dtype or "string" in dtype or "vin" in dtype or (_is_identifier_range and _printable):
        text = "".join(chr(b) for b in body if 32 <= b <= 126).strip()
        return f"`{text}`" if text else ""

    if any(t in dtype for t in ("uint", "int", "byte", "word", "dword")) or dtype == "":
        if not body:
            return ""
        raw_int = int.from_bytes(body, byteorder="big", signed=dtype.startswith("int"))
        if isinstance(scaling, (int, float)) and scaling:
            val = raw_int * scaling
            if isinstance(offset, (int, float)):
                val += offset
            _v = f"{val:g}"
            return f"{_v} {unit}".strip() if unit else _v
        return f"{raw_int} (ham) {unit}".strip()
    return ""


def _uds_did_table_for_tests() -> dict[int, str]:
    """Name table used by packet forensics — exposed for regression tests."""
    out: dict[int, str] = {}
    try:
        db = get_uds_did_database()
    except Exception:  # noqa: BLE001
        return out
    dids = db.get("dids") if isinstance(db, dict) else None
    if not isinstance(dids, dict):
        return out
    for key, row in dids.items():
        if not isinstance(row, dict):
            continue
        did_int = row.get("did_int")
        if not isinstance(did_int, int):
            try:
                did_int = int(str(key), 16) if str(key).lower().startswith("0x") else int(key)
            except (TypeError, ValueError):
                continue
        name = str(row.get("name_tr") or row.get("name") or "").strip()
        if name:
            out[did_int] = name
    return out


def get_mode06_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return SAE J1979 Mode $06 On-Board Monitoring Tests database."""
    global _CACHED_MODE06_DB
    if _CACHED_MODE06_DB is not None and data_path is None:
        return _CACHED_MODE06_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "obd_mode06_database.json"
    if not target.exists():
        logger.warning("Mode $06 database not found at %s — monitor test data degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_MODE06_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load Mode 06 database: %s", exc)
    return {}


def _mode06_mid_from_can_id(can_id: int) -> int | None:
    """Extract an OBD-II Mode $06 MID from a CAN arbitration ID.

    SAE J1979 defines the 29-bit OBD response ID as
    ``0x18DA <target> <tool>`` where ``target`` is the ECU. The legacy
    diagnostic tool-response ID ``0x7E8`` carries no MID. The Mode $06
    ``0x46`` (On-Board Monitoring Test Results) request ID embeds the MID
    in the low byte: ``0x18DA_46_MID`` — the shape the reviewer's scenario
    describes. Also accepts a plain ``0x46xx``-style response where the MID
    is the low byte.
    """
    if can_id is None:
        return None
    # 29-bit OBD request: 0x18DA 46 <mid>
    if (can_id >> 16) & 0xFFFF == 0x18DA and ((can_id >> 8) & 0xFF) == 0x46:
        mid = can_id & 0xFF
        return mid if mid else None
    # Explicit Mode $06 request frame 0x46 <mid> in the low byte.
    if (can_id & 0xFF00) == 0x4600 and (can_id & 0xFF):
        return can_id & 0xFF
    return None


def format_mode06_monitor(mid: int, tid: int | None = None, data_path: Path | str | None = None) -> str:
    """Consume ``obd_mode06_database.json``: describe a Mode $06 MID/TID.

    This is the production consumption path for the Mode $06 accessor
    (A3-13) — previously ``get_mode06_database`` had no caller anywhere, so
    the 88 KB monitor catalog was never read. Returns a deterministic,
    KB-grounded description (no fabricated values); unknown MIDs degrade to
    an honest "not in catalog" note. Deterministic and offline.
    """
    db = get_mode06_database(data_path)
    monitors = db.get("monitors") if isinstance(db, dict) else None
    if not isinstance(monitors, dict) or not monitors:
        return f"Mode $06 MID 0x{mid:02X}: izleme kataloğu yüklenemedi."

    # MID keys are canonical hex ("0x01"); tolerate int or "0xNN"/"NN" forms.
    key_hex = f"0x{mid:02X}"
    monitor = monitors.get(key_hex)
    if monitor is None:
        for v in monitors.values():
            if isinstance(v, dict) and v.get("mid_int") == mid:
                monitor = v
                break
    if not isinstance(monitor, dict):
        return f"Mode $06 MID 0x{mid:02X}: katalogda kayıtlı değil (uydurma yapılmadı)."

    name_tr = str(monitor.get("name_tr") or monitor.get("name") or f"MID 0x{mid:02X}")
    subsystem = str(monitor.get("subsystem") or "—")
    lines = [
        f"📊 **Mode $06 İzleme Testi (MID 0x{mid:02X}):**",
        f"• **İzleme:** {name_tr}",
        f"• **Alt sistem:** {subsystem}",
    ]
    if tid is None:
        return "\n".join(lines)

    tests = monitor.get("tests") or []
    test = None
    tid_hex = f"0x{tid:02X}"
    for t in tests:
        if not isinstance(t, dict):
            continue
        if t.get("tid_hex") == tid_hex or t.get("tid") == tid:
            test = t
            break
    if not isinstance(test, dict):
        lines.append(f"• **TID 0x{tid:02X}:** bu MID altında kayıtlı değil.")
        return "\n".join(lines)

    t_name = str(test.get("name_tr") or test.get("name") or f"TID 0x{tid:02X}")
    lines.append(f"• **Test (TID 0x{tid:02X}):** {t_name}")
    unit = test.get("default_unit") or test.get("unit")
    if unit:
        lines.append(f"• **Birim:** {unit}")
    ideal = test.get("ideal_range")
    if ideal:
        lines.append(f"• **İdeal aralık:** {ideal}")
    return "\n".join(lines)


_CACHED_NHTSA_RECALLS_DB: dict[str, Any] | None = None


def get_extended_pid_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return Extended / Enhanced OBD-II PID Database (Mode $22 + Custom).

    Contains manufacturer-specific PIDs from Ford, GM, Toyota, VAG, BMW,
    Hyundai/Kia, Nissan with scaling formulas, ECU headers, and value ranges.
    """
    global _CACHED_EXTENDED_PID_DB
    if _CACHED_EXTENDED_PID_DB is not None and data_path is None:
        return _CACHED_EXTENDED_PID_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "extended_pid_database.json"
    if not target.exists():
        logger.warning("Extended PID database not found at %s — enhanced PID queries degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            # Drop genuinely empty PID rows (comment/blank rows from CSV imports).
            #
            # T2-6 fix: this filter used to be `if p.get("pid")`, a TRUTHINESS
            # test. ``0`` is a valid OBD-II PID (``0x00`` = "PIDs supported
            # [01-20]", the mandated first query of every Mode 01 scan) and is
            # falsy, so that record — and any other zero-valued row — was
            # silently deleted at load time. Measured effect: the shipped catalog
            # holds 226 records but only 223 survived, and
            # ``get_extended_pid_info("00")`` returned ``None`` for a PID the
            # file plainly contains. The twin audit check
            # (`csv_twins`) counts the JSON, so the loss was invisible to it.
            #
            # The test is now "is there any pid identifier at all": accept
            # ``0``/``"0"``/``"00"``, reject ``None``/``""``/missing. `pid_hex`
            # is accepted as a fallback identifier because the two harvests that
            # filled this file used different shapes (114 rows carry an int
            # ``pid``, 112 carry a str).
            def _has_pid(row: dict[str, Any]) -> bool:
                for field in ("pid", "pid_hex"):
                    raw = row.get(field)
                    if raw is None:
                        continue
                    if str(raw).strip() != "":
                        return True
                return False

            if "pids" in data:
                data["pids"] = [p for p in data["pids"] if _has_pid(p)]
                data.setdefault("metadata", {})["total_pids"] = len(data["pids"])
            if data_path is None:
                _CACHED_EXTENDED_PID_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load Extended PID database: %s", exc)
    return {}


def search_extended_pids(
    manufacturer: str | None = None,
    category: str | None = None,
    query: str | None = None,
    service: str | None = None,
    limit: int = 20,
    data_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Search extended PIDs by manufacturer, category, keyword, or OBD service mode.

    Examples:
        search_extended_pids(manufacturer="Ford", category="emission")
        search_extended_pids(query="DPF soot")
        search_extended_pids(manufacturer="Toyota", category="hv_battery")
    """
    db = get_extended_pid_database(data_path)
    if not db:
        return []

    pids = db.get("pids", [])
    mfr_clean = manufacturer.lower().strip() if manufacturer else ""
    cat_clean = category.lower().strip() if category else ""
    qry_clean = query.lower().strip() if query else ""
    svc_clean = service.strip() if service else ""

    results: list[dict[str, Any]] = []
    for p in pids:
        if mfr_clean and mfr_clean not in p.get("manufacturer", "").lower():
            continue
        if cat_clean and cat_clean not in p.get("category", "").lower():
            continue
        if svc_clean and svc_clean != p.get("service", ""):
            continue
        if qry_clean:
            searchable = f"{p.get('name', '')} {p.get('description', '')} {p.get('unit', '')} {p.get('vehicle_model', '')}".lower()
            if qry_clean not in searchable:
                continue
        results.append(p)
        if len(results) >= limit:
            break

    return results


def get_extended_pid_info(pid_hex: str, manufacturer: str | None = None) -> dict[str, Any] | None:
    """Lookup a specific extended PID by its hex code, optionally filtered by manufacturer.

    T2-6 fix: ``p.get("pid", "").upper()`` assumed ``pid`` was always a string.
    In the shipped catalog it is NOT — 114 of 226 records carry an ``int``
    (``"pid": 0``, ``"pid": 11``) while the other 112 carry a ``str``, because
    the two harvests that filled the file used different shapes. ``.upper()``
    on an ``int`` raises ``AttributeError``, which is NOT caught anywhere, so
    the lookup aborted instead of returning a record: every integer-keyed PID
    was unreachable and a caller saw a hard crash rather than a ``None``.

    ``str(...)`` normalises both shapes. Verified: ``get_extended_pid_info("00")``
    now returns the "PIDs supported [01-20]" record instead of raising, and the
    integer-pid half of the catalog is searchable again (226 records reachable,
    not 112).
    """
    db = get_extended_pid_database()
    if not db:
        return None

    pid_clean = str(pid_hex).upper().strip()
    mfr_clean = manufacturer.lower().strip() if manufacturer else ""

    for p in db.get("pids", []):
        # Both fields are normalised through str(): `pid` is mixed int/str in the
        # shipped catalog, so neither may be assumed to be a string.
        p_hex = str(p.get("pid_hex", "")).upper().strip()
        p_pid = str(p.get("pid", "")).upper().strip()
        if pid_clean in (p_hex, p_pid):
            if not mfr_clean or mfr_clean in str(p.get("manufacturer", "")).lower():
                return cast(dict[str, Any], p)
    return None


_CACHED_DBC_CATALOG: dict[str, Any] | None = None
_DBC_DATA_DIR: Path = Path(__file__).resolve().parents[3] / "data" / "dbc"


def get_dbc_catalog(catalog_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return DBC catalog metadata."""
    global _CACHED_DBC_CATALOG
    if _CACHED_DBC_CATALOG is not None and catalog_path is None:
        return _CACHED_DBC_CATALOG

    target = Path(catalog_path) if catalog_path else _DBC_DATA_DIR / "catalog.json"
    if not target.exists():
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if catalog_path is None:
                _CACHED_DBC_CATALOG = data
            return data
    except Exception as exc:
        logger.warning("Failed to load DBC catalog: %s", exc)
    return {}


def search_dbc_catalog(query: str, catalog_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Search available DBC files by keyword or model name."""
    cat = get_dbc_catalog(catalog_path)
    if not cat:
        return []

    norm = AutomotiveTokenizer.normalize_text(query).replace("dbc", "").strip()
    words = [
        w for w in norm.split()
        if len(w) > 1 and w not in ("can", "file", "dosya", "dosyasi", "var", "mi", "mu", "nedir", "hangi", "katalog", "hakkinda", "bilgi", "ver")
    ]
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for cat_name, cat_data in cat.get("categories", {}).items():
        title = cat_data.get("title", cat_name)
        protocol = cat_data.get("protocol", "CAN")
        for f in cat_data.get("files", []):
            fname = f.get("filename", "")
            fname_lower = fname.lower()
            if fname in seen:
                continue

            matched = False
            if norm and (norm in fname_lower or fname_lower in norm):
                matched = True
            elif words and all(w in fname_lower for w in words):
                matched = True

            if matched:
                seen.add(fname)
                results.append({
                    "filename": fname,
                    "category": title,
                    "protocol": protocol,
                    "messages_count": f.get("messages_count", 0),
                    "signals_count": f.get("signals_count", 0),
                })

    return results


def get_nhtsa_recalls_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return NHTSA CAN-Bus, Electrical & Software Recalls database."""
    global _CACHED_NHTSA_RECALLS_DB
    if _CACHED_NHTSA_RECALLS_DB is not None and data_path is None:
        return _CACHED_NHTSA_RECALLS_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "nhtsa_can_recalls_database.json"
    if not target.exists():
        logger.warning("NHTSA recalls database not found at %s — recall lookups degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_NHTSA_RECALLS_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load NHTSA recalls database: %s", exc)
    return {}


def search_nhtsa_recalls(
    make: str | None = None,
    model: str | None = None,
    year: int | None = None,
    query: str | None = None,
    category: str | None = None,
    limit: int = 10,
    data_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Search NHTSA recalls by vehicle specification, symptom keywords, or category."""
    db = get_nhtsa_recalls_database(data_path)
    if not db:
        return []

    make_clean = make.lower().strip() if make else ""
    model_clean = model.lower().strip() if model else ""
    query_clean = query.lower().strip() if query else ""
    cat_clean = category.lower().strip() if category else ""

    results: list[dict[str, Any]] = []

    for _campaign_id, rec in db.items():
        # Match vehicle make/model/year if specified
        if make_clean or model_clean or year:
            vehicle_match = False
            for v in rec.get("affected_vehicles", []):
                v_make = v.get("make", "").lower()
                v_model = v.get("model", "").lower()
                v_year = v.get("year")

                make_ok = not make_clean or make_clean in v_make
                model_ok = not model_clean or model_clean in v_model
                year_ok = not year or year == v_year

                if make_ok and model_ok and year_ok:
                    vehicle_match = True
                    break
            if not vehicle_match:
                continue

        # Match category if specified
        if cat_clean and cat_clean not in rec.get("category", "").lower():
            continue

        # Match text query in summary, component, consequence, or remedy
        if query_clean:
            searchable = f"{rec.get('component', '')} {rec.get('summary', '')} {rec.get('consequence', '')} {' '.join(rec.get('affected_systems', []))}".lower()
            if query_clean not in searchable:
                continue

        results.append(rec)
        if len(results) >= limit:
            break

    return results


# ----------------------------------------------------------------------------
# T42 P2-2: arac markasi tanima (tek kaynak) + OEM filtreleme.
# Marka hem DTC `oem_variants` (manufacturer) hem J1939 `oem_engine_families`
# (aile adi: "Cummins ...") hem de NHTSA katmanlarinda ayni sozlukle eslesir.
# Determinizm: sabit eslesme listesi, ilk eslesme kazanir; ayni girdi -> ayni
# cikti. Marka yoksa None doner ve cagiran taraf bugunku davranisi korur
# (fail-safe: filtre yok = hicbir OEM gizlenmez).
# ----------------------------------------------------------------------------
_KNOWN_VEHICLE_MAKES: tuple[tuple[str, str], ...] = (
    ("ford", "ford"),
    ("lincoln", "lincoln"),
    ("mercury", "mercury"),
    ("tesla", "tesla"),
    ("chevrolet", "chevrolet"),
    ("chevy", "chevrolet"),
    ("cadillac", "cadillac"),
    ("gmc", "gmc"),
    ("buick", "buick"),
    ("saturn", "saturn"),
    ("oldsmobile", "oldsmobile"),
    ("pontiac", "pontiac"),
    ("toyota", "toyota"),
    ("lexus", "lexus"),
    ("honda", "honda"),
    ("acura", "acura"),
    ("nissan", "nissan"),
    ("infiniti", "infiniti"),
    ("subaru", "subaru"),
    ("mazda", "mazda"),
    ("mitsubishi", "mitsubishi"),
    ("volkswagen", "volkswagen"),
    ("vw", "volkswagen"),
    ("audi", "audi"),
    ("bmw", "bmw"),
    ("mercedes", "mercedes"),
    ("hyundai", "hyundai"),
    ("kia", "kia"),
    ("ram", "ram"),
    ("dodge", "dodge"),
    ("jeep", "jeep"),
    ("chrysler", "chrysler"),
    ("volvo", "volvo"),
    ("cummins", "cummins"),
    ("detroit", "detroit"),
    ("paccar", "paccar"),
    ("cat", "caterpillar"),
    ("caterpillar", "caterpillar"),
    ("international", "international"),
    ("freightliner", "freightliner"),
    ("kenworth", "kenworth"),
    ("peterbilt", "peterbilt"),
)

# Marka etiketinin, serbest metinde ve DB alanlarinda eslesmesi icin ortak
# takma ad sozlugu (kanonik anahtar -> aranacak desenler).
_MAKE_ALIASES: dict[str, tuple[str, ...]] = {
    "chevrolet": ("chevrolet", "chevy"),
    "caterpillar": ("caterpillar", "cat"),
    "mercedes": ("mercedes",),
    "volkswagen": ("volkswagen", "vw"),
}


def detect_vehicle_make(text: str | None) -> str | None:
    """Return the canonical vehicle make mentioned in ``text``, else ``None``.

    Shared vocabulary for P2-2 (OEM-filtered selection) and P2-3 (complaint
    fusion). Deterministic: a fixed ordered alias table, first match wins.
    ``None`` means "no make given" — callers must then keep the previous
    unfiltered behaviour (fail-safe, no OEM is hidden).
    """
    if not text:
        return None
    norm = str(text).lower()
    for kw, canonical in _KNOWN_VEHICLE_MAKES:
        if re.search(rf"\b{re.escape(kw)}\b", norm):
            return canonical
    return None


def _make_matches(text: str, make: str) -> bool:
    """True if ``make`` (canonical or alias) appears as a word in ``text``."""
    low = str(text or "").lower()
    if not low or not make:
        return False
    patterns = _MAKE_ALIASES.get(make, (make,))
    return any(re.search(rf"\b{re.escape(p)}\b", low) for p in patterns)


def filter_oem_variants(variants: Any, vehicle_make: str | None) -> tuple[list[Any], int]:
    """OEM-filtered ``oem_variants`` selection (T42 P2-2).

    Returns ``(kept_variants, hidden_count)``.

    - ``vehicle_make`` is ``None`` -> every variant is returned, ``hidden=0``
      (today's behaviour, fail-safe).
    - a make is given -> only variants whose ``manufacturer`` matches the make
      (or the generic ``OTHER`` bucket) are kept; unrelated OEMs are hidden.
    - if the filter would hide *everything* (no variant belongs to the make)
      the original list is returned so the report never loses evidence —
      ``hidden`` then reports how many unrelated entries were still shown.
    """
    if not isinstance(variants, list) or not variants:
        return [], 0
    if not vehicle_make:
        return list(variants), 0

    kept: list[Any] = []
    hidden = 0
    for item in variants:
        if isinstance(item, dict):
            mfr = str(item.get("manufacturer") or item.get("make") or "").strip()
        else:
            mfr = ""
        if not mfr:
            kept.append(item)  # unknown OEM — never hide (fail-safe)
        elif mfr.upper() == "OTHER" or _make_matches(mfr, vehicle_make):
            kept.append(item)
        else:
            hidden += 1

    if not kept:
        # Filter matched nothing: keep the original evidence, report the miss.
        return list(variants), len(variants)
    return kept, hidden


def filter_oem_families(families: Any, vehicle_make: str | None) -> tuple[list[str], int]:
    """OEM-filtered J1939 ``oem_engine_families`` selection (T42 P2-2).

    Returns ``(kept_families, hidden_count)``. Same fail-safe contract as
    :func:`filter_oem_variants`: no make -> nothing filtered.
    """
    if not isinstance(families, list) or not families:
        return [], 0
    fams = [str(f) for f in families if str(f).strip()]
    if not vehicle_make:
        return fams, 0

    kept = [f for f in fams if _make_matches(f, vehicle_make)]
    hidden = len(fams) - len(kept)
    if not kept:
        return fams, len(fams)
    return kept, hidden


def search_nhtsa_complaints(
    make: str | None = None,
    year: int | None = None,
    component: str | None = None,
    query: str | None = None,
    limit: int = 5,
    data_path: Path | str | None = None,
) -> dict[str, Any]:
    """Search the NHTSA owner-complaint corpus by vehicle make/year (T42 P2-3).

    Returns ``{"matched_vehicles": [...], "complaints": [...], "total_vehicles":
    int, "total_complaints": int}``. The 5.9 MB corpus is read through the
    already-cached :func:`get_nhtsa_complaints_database` accessor (lazy load +
    singleton cache) so opening the engine never pays the parse cost.

    Deterministic: corpus order is preserved (no sorting/re-scoring), first
    ``limit`` matches win.
    """
    db = get_nhtsa_complaints_database(data_path)
    result: dict[str, Any] = {
        "matched_vehicles": [],
        "complaints": [],
        "total_vehicles": 0,
        "total_complaints": 0,
    }
    if not isinstance(db, dict):
        return result

    vehicles = db.get("vehicles") or []
    complaints = db.get("complaints") or []
    result["total_vehicles"] = len(vehicles) if isinstance(vehicles, list) else 0
    result["total_complaints"] = len(complaints) if isinstance(complaints, list) else 0

    if not isinstance(vehicles, list):
        return result

    make_clean = (make or "").lower().strip()
    comp_clean = (component or "").lower().strip()
    query_clean = (query or "").lower().strip()

    for v in vehicles:
        if not isinstance(v, dict):
            continue
        v_make = str(v.get("make", "")).strip()
        if make_clean and not _make_matches(v_make, make_clean):
            # _make_matches expects canonical make; also accept raw substring
            if make_clean not in v_make.lower():
                continue
        if year:
            try:
                if int(v.get("year", 0)) != int(year):
                    continue
            except (TypeError, ValueError):
                continue

        evidences = v.get("can_related") or []
        if not isinstance(evidences, list):
            evidences = []
        if comp_clean or query_clean:
            filtered = []
            for e in evidences:
                if not isinstance(e, dict):
                    continue
                blob = f"{e.get('components', '')} {e.get('summary', '')}".lower()
                if comp_clean and comp_clean not in blob:
                    continue
                if query_clean and query_clean not in blob:
                    continue
                filtered.append(e)
            if (comp_clean or query_clean) and not filtered:
                continue
            evidences = filtered

        result["matched_vehicles"].append({
            "make": v_make,
            "model": str(v.get("model", "")).strip(),
            "year": v.get("year"),
            "raw_count": v.get("raw_count", len(evidences)),
        })
        for e in evidences:
            if isinstance(e, dict):
                result["complaints"].append(e)
            if len(result["complaints"]) >= limit:
                break
        if len(result["complaints"]) >= limit:
            break

    return result


def format_nhtsa_recall_report(recall: dict[str, Any]) -> str:
    """Format an NHTSA safety recall into a concise, actionable summary."""
    camp = recall.get("campaign_number", "Bilinmiyor")
    mfr = recall.get("manufacturer", "-")
    comp = recall.get("component", "-")
    ota = "OTA Güncelleme" if recall.get("over_the_air_update") else "Servis Onarımı"
    summary = (recall.get("summary") or "-").strip()
    if len(summary) > 160:
        summary = summary[:157] + "..."
    remedy = (recall.get("remedy") or "-").strip()
    if len(remedy) > 140:
        remedy = remedy[:137] + "..."

    vehicles = recall.get("affected_vehicles", [])
    v_info = ""
    if vehicles:
        first = vehicles[0]
        v_info = f" | {first.get('make', '')} {first.get('model', '')} ({first.get('year', '')})"
        if len(vehicles) > 1:
            v_info += f" (+{len(vehicles) - 1} model)"

    return (
        f"🚨 **NHTSA Geri Çağırma (Recall): {camp}** ({mfr}{v_info})\n"
        f"• **Modül:** {comp} [{ota}]\n"
        f"• **📋 Sorun Özeti:** {summary}\n"
        f"• **🔧 Resmi Onarım:** {remedy}"
    )


# M-18 (P2-17): the external DTC database loads lazily on first use instead
# of at module import — the bare module-scope call parsed 2.3 MB of JSON
# (~0.4 s I/O) on EVERY process start, including CLI invocations that never
# touch diagnostics. ensure_external_dtc_database_loaded() is idempotent and
# thread-safe; direct load_external_dtc_database(data_path=...) callers
# (tests) bypass the cache deliberately.
_DTC_DB_LOAD_LOCK = threading.Lock()
_DTC_DB_LOADED = False


def ensure_external_dtc_database_loaded() -> None:
    """Idempotent, thread-safe lazy load of the external DTC database."""
    global _DTC_DB_LOADED
    if _DTC_DB_LOADED:
        return
    with _DTC_DB_LOAD_LOCK:
        if _DTC_DB_LOADED:
            return
        load_external_dtc_database()
        _DTC_DB_LOADED = True


# ----------------------------------------------------------------------------
# T41 P1-4: semptom -> kod aramasi (14.166 DTC kaydinda `symptoms` DOLU ama
# motorda HIC okunmuyordu). Serbest-metin sorgu, onceki teshis yollarinin
# hicbiriyle eslesmezse semptom/baslik metinlerinde deterministik olarak
# aranir. Determinizm: sabit kod sirasi (sozluk ekleme sirasi), saf string
# karsilastirma, rastgelelik/skor-esigi yan etkisi yok.
# ----------------------------------------------------------------------------
_SYMPTOM_SEARCH_STOPWORDS: frozenset[str] = frozenset({
    "ariza", "arizasi", "hata", "kodu", "nedir", "ne", "nasil", "neden",
    "var", "bir", "bu", "ve", "ile", "icin", "cok", "az", "mi", "mu",
    "fault", "code", "the", "and", "why", "what",
})


def _has_standalone_word(haystack: str, needle: str) -> bool:
    """True when ``needle`` occurs in ``haystack`` as a WHOLE word.

    F-01 (P0): the diagnosis tree matched intent keywords with bare
    ``"w" in norm_query`` substring tests. That produced wrong-path routing
    ("hat" matching inside "hata karesi") and — worse — fabricated diagnoses
    from unrelated prose: "hava durumu nedir" ("what is the weather") matched
    the pneumatic-brake keyword "hava" and returned a CRITICAL_STOP brake
    system report (Kanıt A). Whole-word matching is the minimal correct fix;
    it is deterministic and locale-independent (both sides are already
    normalised to lowercase ASCII by AutomotiveTokenizer.normalize_text).
    """
    if not needle:
        return False
    return re.search(rf"(?<![0-9a-z]){re.escape(needle)}(?![0-9a-z])", haystack) is not None


def _has_any_standalone_word(haystack: str, needles: "frozenset[str] | tuple[str, ...] | list[str]") -> bool:
    """True when ANY needle appears as a whole word in ``haystack``."""
    return any(_has_standalone_word(haystack, n) for n in needles)


# Kanıt A (P1): free-text words that carry no diagnostic specificity on their
# own. A query consisting ONLY of these (plus stopwords) must abstain rather
# than route to a code: "problem" previously produced a concrete P0500 report.
_GENERIC_NON_DIAGNOSTIC_TERMS: frozenset[str] = frozenset({
    "problem", "sorun", "sikinti", "sıkıntı", "arac", "araç", "araba",
    "motor", "ariza", "hata", "issue", "trouble", "error", "fault",
    "nedir", "ne", "nasil", "nasıl", "neden", "why", "what", "help",
    "yardim", "yardım", "merhaba", "hello", "hi", "selam", "test",
})


# T72 (2026-09-23): curated SHORT automotive acronyms admitted through the
# length>=4 gate. That gate is a typo-noise guard, but it also discarded whole
# diagnostic concepts, because the canonical operator term for them is shorter
# than four characters. Measured on the shipped DB: "DPF clogged" reduced to the
# single term ["clogged"], so the F-04 min-score gate (2) abstained and the query
# returned NO hit at all — while 182 records mention DPF and 28 mention both
# "dpf" and "clogged". These tokens are unambiguous industry acronyms, not
# English words, so admitting them adds signal without the substring noise the
# length gate exists to prevent. Ambiguous short tokens that ARE ordinary
# English/technical words ("can", "def", "air") are deliberately NOT listed.
_SYMPTOM_SEARCH_SHORT_ACRONYMS: frozenset[str] = frozenset({
    "dpf", "egr", "maf", "map", "o2", "scr", "tps", "abs", "ac", "ecu",
    "tcm", "pcm", "bcm", "evap", "mil", "hvil",
})


def _symptom_search_terms(norm_query: str) -> list[str]:
    """Deterministlik sorgu terimleri: normalize edilmis kelimeler.

    Yalniz uzunluk >= 4 (veya ``_SYMPTOM_SEARCH_SHORT_ACRONYMS`` uyesi) ve
    stopword olmayan kelimeler; en fazla 6 terim. Sira korunur (determinizm).

    Kanıt A / F-04: terms that carry no diagnostic specificity on their own
    (see ``_GENERIC_NON_DIAGNOSTIC_TERMS``) are EXCLUDED from the search. A
    generic word such as "hava" ("air"/"weather") or "durumu" otherwise matched
    a brake-pressure record's symptom prose by coincidence and produced a
    concrete CRITICAL_STOP report for an unrelated question.
    """
    terms: list[str] = []
    for raw in str(norm_query or "").split():
        w = raw.strip()
        if w in _SYMPTOM_SEARCH_STOPWORDS:
            continue
        if len(w) < 4 and w not in _SYMPTOM_SEARCH_SHORT_ACRONYMS:
            continue
        if w in _GENERIC_NON_DIAGNOSTIC_TERMS:
            continue
        if w not in terms:
            terms.append(w)
        if len(terms) >= 6:
            break
    return terms


def _symptom_term_forms(term: str) -> tuple[str, ...]:
    """Surface forms of ONE query term (deterministic, longest-first).

    T72: operator utterances are inflected ("engine stalls at idle", "no start
    cranks") while the DB prose is mostly the bare stem. Measured on the shipped
    DB: the plural token "stalls" occurs in 46 haystacks but "stall" in 108;
    "cranks" 54 vs "crank" 150; "slips" 4 vs "slip" 154; "drains" 14 vs "drain"
    140. Matching the singular form of a trailing-'s' query term recovers that
    recall. The REVERSE direction (expanding a singular QUERY term to a plural)
    was measured and rejected — it lowered top-1 relevance (T72 experiment 7).
    """
    if term.endswith("s") and len(term) - 1 >= 4:
        return (term, term[:-1])
    return (term,)


def _dtc_query_key(norm_query: str) -> str | None:
    """The catalog key a DTC-shaped query names outright, else ``None``.

    T72: a code query names its own record. Measured before this fix:
    ``search_dtc_by_symptom("p0301")`` returned an EMPTY list — the literal
    token "p0301" occurs in only 2 of 14,496 haystacks, so the F-04 min-score
    gate abstained and a caller asking "what is this code" got nothing. It also
    guards a future regression: as ``symptoms_en`` coverage grows, code-shaped
    tokens appear in more prose (already measurable: "p0301 misfire" ranks
    P0314 second, purely because its text mentions P0301), so identity must be
    resolved BEFORE text overlap. Deterministic, read-only, no fabrication —
    only a key the catalog actually carries is returned.
    """
    key = re.sub(r"\s+", "", str(norm_query or "")).upper()
    if re.fullmatch(r"(?:[PBUC][0-9A-F]{4}|SPN[0-9]+)", key):
        ensure_external_dtc_database_loaded()
        if key in EXPERT_KNOWLEDGE_BASE:
            return key
    return None


# F-04 (P0): minimum number of matched query terms required before a
# free-text symptom search is allowed to name a DTC. A single coincidental
# token overlap is not diagnostic evidence — with ~14k records spanning
# dozens of subsystems, one shared word matches almost anything. Requiring
# >= 2 independent terms makes the lookup selective; a query that cannot
# clear the bar abstains instead of inventing a code.
_SYMPTOM_SEARCH_MIN_SCORE = 2


def search_dtc_by_symptom(norm_query: str, limit: int = 1) -> list[dict[str, Any]]:
    """DTC `symptoms`/`title` alanlarinda serbest-metin aramasi (T41 P1-4).

    Returns a deterministic, ranked list of ``{"code", "score", "matched"}``
    dicts (best first). Ties break on insertion order of the catalog, so the
    same input always yields the same output. Read-only: no network/HAL.

    F-04 (P0): candidates must clear ``_SYMPTOM_SEARCH_MIN_SCORE`` matched
    terms. Previously ANY single matched word was enough, so generic or
    coincidental overlap produced a confident-looking diagnosis (Kanıt A).
    """
    terms = _symptom_search_terms(norm_query)
    if not terms:
        return []
    ensure_external_dtc_database_loaded()
    # T72: a query that names a catalog key outright resolves to that record
    # before any text overlap is considered (see ``_dtc_query_key``).
    exact_key = _dtc_query_key(norm_query)
    # T72: the haystack is scored in two evidence tiers. `symptoms` /
    # `symptoms_en` / titles are the record's own SYMPTOM claim; `causes_en` /
    # `description_en` are longer prose that mentions many unrelated concepts.
    # Measured on the shipped DB (T72): with one merged haystack, "coolant
    # temperature too high" ranked P0171 (lean fuel trim) first purely because
    # its cause prose contains the words "coolant" and "temperature"; ranking
    # the strong tier ahead of the weak tier moved the coolant-temperature
    # records (P0117/P0118/P2181) to the top. Both tiers still match, so recall
    # is unchanged — only the ORDER of equally-matching records changes.
    hits: list[tuple[int, int, int, str, list[str]]] = []
    for order, (code, info) in enumerate(EXPERT_KNOWLEDGE_BASE.items()):
        if not isinstance(info, dict):
            continue
        strong_parts: list[str] = []
        weak_parts: list[str] = []
        sym = info.get("symptoms")
        if isinstance(sym, list):
            strong_parts.extend(str(x) for x in sym)
        # T66 (2026-09-22): the T46/T63/T66 harvests write English payload to
        # `symptoms_en` / `causes_en` / `description_en` (never the Turkish
        # fields — tests/unit/test_t46_merge.py locks that). Measured: 6,228
        # records carry `symptoms_en` while the Turkish `symptoms` holds only
        # 1,275 — so the search was blind to the larger corpus, the same class
        # of defect T41 P1-1 fixed for `procedures_full`. English text is a
        # legitimate search surface for an English query term.
        # T80j (2026-09-26): a record whose `symptoms_en` is a KNOWN subject
        # misattribution must not be findable BY that misattributed text.
        # Measured: geekobd.com pages were about a different subject than the
        # record for 4,583 of 6,330 codes; 79 were reverted outright and 3,886
        # carry `geekobd_subject_conflict_t80e` because no independent source
        # could arbitrate. Measured search pollution before this guard: a
        # query about the PAGE's subject surfaced 13 records in 100 top-10
        # slots (e.g. "oil separator performance" surfaced P04E8, an EGR
        # temperature sensor). The record's own `title` / `symptoms` stay
        # searchable — only the flagged text is withheld from the haystack, so
        # the record is not hidden, just no longer findable by a claim the DB
        # itself marks as conflicting.
        _misattributed = bool(info.get("geekobd_subject_conflict_t80e"))
        for key in ("symptoms_en", "causes_en", "description_en"):
            if _misattributed and key in ("symptoms_en", "causes_en"):
                continue
            v = info.get(key)
            bucket = strong_parts if key == "symptoms_en" else weak_parts
            if isinstance(v, str):
                bucket.append(v)
            elif isinstance(v, list):
                bucket.extend(str(x) for x in v)
        for key in ("title", "title_tr", "title_en"):
            v = info.get(key)
            if isinstance(v, str):
                strong_parts.append(v)
        if not strong_parts and not weak_parts:
            continue
        hay_strong = AutomotiveTokenizer.normalize_text(" ".join(strong_parts))
        hay_weak = AutomotiveTokenizer.normalize_text(" ".join(weak_parts))
        matched: list[str] = []
        strong_matched = 0
        for t in terms:
            forms = _symptom_term_forms(t)
            if any(_has_standalone_word(hay_strong, f) for f in forms):
                matched.append(t)
                strong_matched += 1
            elif any(_has_standalone_word(hay_weak, f) for f in forms):
                matched.append(t)
        if matched:
            hits.append((len(matched), strong_matched, order, str(code), matched))
    # sort: more matched terms first, then more of them matched in the
    # symptom/title tier, then catalog insertion order (stable).
    hits.sort(key=lambda h: (-h[0], -h[1], h[2]))
    # F-04: drop candidates that do not clear the evidence bar. An abstention
    # (empty list) is the correct answer when the overlap is coincidental.
    qualified = [h for h in hits if h[0] >= _SYMPTOM_SEARCH_MIN_SCORE]
    if exact_key:
        # Identity, not coincidence: the operator named this record's own key,
        # so the F-04 min-score bar does not apply. Measured before this fix:
        # ``search_dtc_by_symptom("p0301")`` returned [] although the catalog
        # carries P0301 — the literal token occurs in only 2 of 14,496
        # haystacks, so the text path abstained on a query that is not
        # ambiguous at all.
        exact_hit = next((h for h in hits if h[3] == exact_key), None)
        if exact_hit is None:
            exact_hit = (1, 0, -1, exact_key, [exact_key.lower()])
        qualified = [exact_hit] + [h for h in qualified if h[3] != exact_key]
    return [
        {"code": c, "score": n, "matched": m}
        for (n, _strong, _order, c, m) in qualified[: max(1, limit)]
    ]


# ----------------------------------------------------------------------------
# T42 P2-6: rezerve (ISO/SAE reserved) DTC ayrimi. DB'de 390 kayit `is_reserved`
# + `reserved_note` tasir; bu kayitlar icin standart bir anlam YOKTUR (uretici
# atamasi bekler). Motor bunlari normal kod gibi sunarsa YANLIS teshis uretir.
# Fail-safe: yalnizca DB'de `is_reserved` DOLU ise blok uretilir; alan yoksa
# (kural: uydurma yok) hicbir sey basilmaz.
# ----------------------------------------------------------------------------


def get_reserved_code_notice(code: str) -> str | None:
    """Rezerve kod icin "standart degil, ureticiye ozel" uyari blogu dondur.

    ``EXPERT_KNOWLEDGE_BASE`` icindeki kayit ``is_reserved`` truthy ise,
    DB'deki ``reserved_note`` metni (varsa) ile birlikte deterministik bir
    uyari blogu uretir. Rezerve degilse / kayit yoksa ``None`` dondurur
    (fail-safe — uydurma blok yok).
    """
    ensure_external_dtc_database_loaded()
    info = EXPERT_KNOWLEDGE_BASE.get(str(code or "").strip().upper())
    if not isinstance(info, dict) or not info.get("is_reserved"):
        return None
    note = str(info.get("reserved_note") or "").strip()
    lines = [
        "⛔ **REZERVE KOD — STANDART DEĞİL (ÜRETİCİYE ÖZEL):**",
        "  • Bu arıza kodu ISO/SAE standart tablosunda **üretici atamasına bırakılmıştır**; "
        "genel geçer bir teşhis anlamı yoktur.",
    ]
    if note:
        lines.append(f"  • **Veritabanı notu:** {note[:220]}")
    lines.append(
        "  • **Öneri:** Aracın markasına özel servis kılavuzundan / üretici teşhis "
        "yazılımından (OEM scan tool) kod anlamını doğrulayın. Bu kodu genel "
        "arızaymış gibi yorumlayıp parça değişimi YAPMAYIN."
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# T42 P2-1: coklu-DTC birlesik analiz. Sorgu yolu bugun yalniz `active_dtcs[0]`
# isliyordu. Tum aktif kodlar birlikte kumelenir:
#   (1) ortak alt-sistem (DB `subsystem`) -> tekrarli arizali alan tespiti
#   (2) DB-turevli iliski: `_RELATED_CODE_GROUPS` (10 sabit kume) yerine aktif
#       kodlarin {subsystem, J1939 associated_pgn} ortakligindan turetilen
#       cografi/kok-neden gruplari (uydurma yok — alanlar DB'de dolu degilse
#       o boyut atlanir).
# Determinizm: kodlar normalize edilip siralanir; gruplar (uyelik sayisi desc,
# ilk uye asc) ile tie-break edilir. Ayni girdi -> ayni cikti.
# ----------------------------------------------------------------------------


def _t42_norm_code(raw: Any) -> str:
    """`code`/`spn` alanini kanonik anahtara cevir (SPN123 / P0300)."""
    s = str(raw or "").strip().upper()
    m = _CODE_ALIAS_RE.match(s)
    return f"SPN{m.group(1)}" if m else s


def _t42_dtc_key(dtc: Any) -> str:
    """Bir aktif-DTC kaydindan deterministik anahtar uret.

    Once ``code`` sonra ``SPN<spn>`` fallback'i denenir; ikisi de bossa
    bos string doner. Uydurma YOK.
    """
    if not isinstance(dtc, dict):
        return ""
    key = _t42_norm_code(dtc.get("code"))
    if not key and dtc.get("spn") is not None:
        key = f"SPN{dtc.get('spn')}"
    return key


def _t42_lookup_dtc_fields(code: str) -> dict[str, Any]:
    """Kod icin DB/knowledge-base teşhis alanlarini birlestir.

    Donen anahtarlar: ``subsystem``, ``pgn``. Eksik alan eklenmez
    (fail-safe). J1939 SPN girdilerinde ``associated_pgn`` DB-doldurulmus
    ise kullanilir.
    """
    out: dict[str, Any] = {}
    key = _t42_norm_code(code)
    if not key:
        return out
    ensure_external_dtc_database_loaded()
    info = EXPERT_KNOWLEDGE_BASE.get(key)
    if isinstance(info, dict):
        sub = info.get("subsystem")
        if isinstance(sub, str) and sub.strip():
            out["subsystem"] = sub.strip()
    m = _CODE_ALIAS_RE.match(key)
    if m:
        try:
            db = get_j1939_spn_database()
            entry = (db.get("spns") or {}).get(f"SPN_{m.group(1)}")
            if isinstance(entry, dict):
                if "subsystem" not in out:
                    sub = entry.get("subsystem")
                    if isinstance(sub, str) and sub.strip():
                        out["subsystem"] = sub.strip()
                pgn = entry.get("associated_pgn")
                if isinstance(pgn, int) and pgn > 0:
                    out["pgn"] = pgn
        except Exception:
            pass
    return out


def analyze_active_dtc_clusters(active_dtcs: list[Any]) -> dict[str, Any]:
    """Aktif DTC kumesi icin birlesik, deterministik kumeleme sonucu.

    Donen sozluk:
      - ``codes``: normalize edilmis, kodsuz kayitlar atilmis kod listesi (sirali)
      - ``reserved_codes``: rezerve (ureticiye ozel) kodlar (sirali)
      - ``subsystem_groups``: ``[(subsystem, [codes...])]`` — tekrarli alt-sistemler
      - ``pgn_groups``: ``[(pgn, [codes...])]`` — ortak J1939 PGN (paylasilan yol)
    Gruplama yalnizca DB'de DOLU alanlardan turetilir; uydurma yok. Bir boyut
    icin veri yoksa o boyut bos doner.
    """
    seen: list[str] = []
    for d in active_dtcs or []:
        k = _t42_dtc_key(d)
        if k and k not in seen:
            seen.append(k)
    seen.sort()
    if not seen:
        return {"codes": [], "reserved_codes": [], "subsystem_groups": [], "pgn_groups": []}

    ensure_external_dtc_database_loaded()
    reserved = [
        c for c in seen
        if isinstance(EXPERT_KNOWLEDGE_BASE.get(c), dict) and EXPERT_KNOWLEDGE_BASE[c].get("is_reserved")
    ]

    sub_map: dict[str, list[str]] = {}
    pgn_map: dict[int, list[str]] = {}
    for c in seen:
        fields = _t42_lookup_dtc_fields(c)
        sub = fields.get("subsystem")
        if sub:
            sub_map.setdefault(sub, []).append(c)
        pgn = fields.get("pgn")
        if pgn:
            pgn_map.setdefault(pgn, []).append(c)

    # yalniz >=2 uye tasiyan ortaklik gruplari ilgi cekicidir (tek kod zaten
    # tek-kod yolu ile islenir). Grup sirasi: uye sayisi desc, ad/pgn asc.
    sub_groups = sorted(
        ((s, sorted(cs)) for s, cs in sub_map.items() if len(cs) >= 2),
        key=lambda t: (-len(t[1]), t[0]),
    )
    pgn_groups = sorted(
        ((p, sorted(cs)) for p, cs in pgn_map.items() if len(cs) >= 2),
        key=lambda t: (-len(t[1]), t[0]),
    )
    return {
        "codes": seen,
        "reserved_codes": reserved,
        "subsystem_groups": sub_groups,
        "pgn_groups": pgn_groups,
    }


# ============================================================================
# COMPLETE ISO 14229 UDS NEGATIVE RESPONSE CODE (NRC) CATALOG
# ============================================================================


UDS_NRC_CATALOG: dict[str, dict[str, str]] = {
    "0x10": {"name": "generalReject", "cause": "ECU donanımsal meşguliyet veya dahili hata nedeniyle isteği reddetti.", "action": "İsteği 50 ms sonra tekrarlayın veya ECU'ya soft reset atın."},
    "0x11": {"name": "serviceNotSupported", "cause": "İstenen Servis ID (SID) bu ECU yazılımında tanımlı değil.", "action": "ECU yazılım versiyonunu ve desteklenen servis listesini (0x19 0x0A) kontrol edin."},
    "0x12": {"name": "subFunctionNotSupported", "cause": "İstenen alt fonksiyon (Subfunction) bu serviste desteklenmiyor.", "action": "Subfunction baytını kontrol edin (Örn: 0x10 0x02 yerine 0x10 0x03 deneyin)."},
    "0x13": {"name": "incorrectMessageLengthOrInvalidFormat", "cause": "İstek bayt uzunluğu veya çerçeve formatı hatalı.", "action": "ISO-TP çerçeve uzunluğunu ve parametre bayt sayısını doğrulayın."},
    "0x14": {"name": "responseTooLong", "cause": "Yanıt bayt uzunluğu taşıma tamponunu aşıyor.", "action": "Sorguyu daraltın (Tüm liste yerine tek tek DID veya DTC okuyun)."},
    "0x22": {"name": "conditionsNotCorrect", "cause": "Ön koşullar sağlanmadı (Örn: Motor çalışırken rutin başlatılamaz veya voltaj <11.0V).", "action": "Kontağı açın, motoru durdurun, akü besleme cihazı bağlayın (>12.5V) ve el frenini çekin."},
    "0x24": {"name": "requestSequenceError", "cause": "Sıralama hatası (Örn: Seed almadan Key gönderme veya 0x34 olmadan 0x36 çağırma).", "action": "Prosedürü en baştan sırasıyla işletin (0x10 0x02 -> 0x27 0x01 -> 0x27 0x02 -> 0x34)."},
    "0x31": {"name": "requestOutOfRange", "cause": "DID, Routine ID veya yazılmak istenen parametre değeri sınırların dışında.", "action": "Parametre sınırlarını ve DID hex adresini ODX/CDD veritabanından doğrulayın."},
    "0x33": {"name": "securityAccessDenied", "cause": "Güvenlik kilidi kapalı; bu işlem için Seed/Key açılması şart.", "action": "0x27 0x01 servisi ile Seed isteyip doğru Key algoritmasını hesaplayarak gönderin."},
    "0x35": {"name": "invalidKey", "cause": "Gönderilen güvenlik anahtarı (Key) yanlış.", "action": "DLL algoritmasını, gizli anahtarı ve byte endianness sırasını kontrol edin."},
    "0x36": {"name": "exceededNumberOfAttempts", "cause": "Üst üste 3 hatalı Key denemesi yapıldığı için güvenlik kilidi kilitlendi.", "action": "ECU gücünü kesmeyin; 10 dakikalık anti-brute-force ceza süresinin dolmasını bekleyin."},
    "0x37": {"name": "requiredTimeDelayNotExpired", "cause": "Ceza süresi dolmadan yeni bir güvenlik erişim isteği yapıldı.", "action": "Geri sayım süresinin (10 dk) tamamen sıfırlanmasını bekleyin."},
    "0x78": {"name": "requestCorrectlyReceived-ResponsePending", "cause": "ECU işlemi kabul etti, arka planda işliyor (Flash silme/kripto hesabı).", "action": "İsteği tekrarlamayın! P2* client zamanlayıcısını (5000 ms) bekleyin."},
    "0x7E": {"name": "subFunctionNotSupportedInActiveSession", "cause": "Bu alt fonksiyon mevcut oturumda yasak.", "action": "0x10 0x03 ile Extended Session'a geçiş yapın."},
    "0x7F": {"name": "serviceNotSupportedInActiveSession", "cause": "Bu servis mevcut oturumda çalıştırılamaz.", "action": "0x10 0x02 Programming Session veya 0x10 0x03 Extended Session açın."},
    "0x83": {"name": "engineIsRunning", "cause": "Test için motorun durdurulması şart.", "action": "Motoru stop edip sadece kontağı açık bırakın."},
    "0x88": {"name": "vehicleSpeedTooHigh", "cause": "Araç hızı >0 km/s olduğu için güvenlik gereği işlem engellendi.", "action": "Aracı tamamen durdurun ve el frenini çekin."},
    "0x92": {"name": "voltageTooHigh", "cause": "Akü/şebeke voltajı çok yüksek (>16.0V / >32.0V).", "action": "Harici şarj cihazını sökün veya regülatörü kontrol edin."},
    "0x93": {"name": "voltageTooLow", "cause": "Akü voltajı güvenli flash/rutin sınırının altında (<11.0V).", "action": "Harici akü destek ünitesi bağlayın (13.8V - 14.4V)."},
}

# ============================================================================
# BILINGUAL TURKISH/ENGLISH AUTOMOTIVE NLP TOKENIZER & SEMANTIC ONTOLOGY
# ============================================================================

AUTOMOTIVE_SEMANTIC_DICTIONARY: dict[str, list[str]] = {
    "MISFIRE": [
        "tekleme", "tekliyor", "misfire", "silkeleme", "sarsinti", "sarsintili", "3 silindir", "atesleme hatasi",
        "atesleme", "buji", "bobin", "enjektor", "avans", "vuruntu", "patlatma", "piston", "kompresyon"
    ],
    "TURBO_BOOST": [
        "turbo", "overboost", "underboost", "basinc", "boost", "wastegate", "intercooler", "n75", "vgt",
        "islik sesi", "hava kacagi", "hortum patlak", "hava akis", "maf", "map", "cekis dusuklugu", "bayilma",
        "kara duman", "siyah duman", "duman atiyor"
    ],
    "OVERHEAT_COOLING": [
        "hararet", "sicaklik", "sogutma", "termostat", "radyator", "fan", "antifriz", "su kaynatiyor", "su eksiltme",
        "hortum sisme", "devirdaim", "su pompasi", "expansion tank", "genlesme kabi", "conta yakma", "ust kapak contasi",
        "beyaz buhar", "tatli koku", "mayonez"
    ],
    "EV_HV_BATTERY": [
        "ev", "bms", "hvil", "izolasyon", "batarya", "pil", "hucre", "delta voltaj", "precharge", "kontaktor", "megger",
        "yuksek voltaj", "high voltage", "msd", "servis salteri", "turtle mode", "kapasite kaybi", "soh", "soc", "inverter",
        "termal kacak", "thermal runaway", "dc-dc", "igbt"
    ],
    "HEAVY_DUTY_J1939": [
        "j1939", "spn", "fmi", "dm1", "dm2", "dm4", "dm11", "adblue", "def", "dpf", "scr", "nox", "rejenerasyon",
        "kirmizi lamba", "sari lamba", "rsl", "awl", "tork kisitlama", "5 mph", "hiz limiti", "cummins", "detroit",
        "scania", "volvo truck", "paccar", "hava basinci", "ebs"
    ],
    "MARINE_NMEA2000": [
        "marine", "marin", "tekne", "yat", "gemi", "nmea", "nmea 2000", "n2k", "pgn", "impeller", "cark", "deniz suyu",
        "strainer", "esnjor", "esanchor", "egzoz dirsegi", "mixing elbow", "susturucu", "waterlock", "pervane", "slip",
        "kavitasyon", "dumen", "potansiyometre", "volvo penta", "evc", "yanmar"
    ],
    "CAN_PHYSICAL_LAYER": [
        "can bus", "haberlesme", "120 ohm", "sonlandirma", "60 ohm", "direnc", "kisa devre", "acik devre", "bus off",
        "error passive", "osiloskop", "voltaj", "pinout", "obd", "deutsch", "can_h", "can_l", "gurultu", "parazit",
        "ground offset", "topraklama"
    ],
    "UDS_PROTOCOL": [
        "uds", "servis", "service", "0x10", "0x11", "0x14", "0x19", "0x22", "0x27", "0x28", "0x2e", "0x2f", "0x31",
        "0x34", "0x36", "0x37", "0x85", "nrc", "seed", "key", "guvenlik", "oturum", "did", "routine", "flash"
    ],
    "ELECTRICAL_STARTING": [
        "mars", "mars basmiyor", "mars almiyor", "gec calisma", "aku", "alternator", "sarj dinamosu", "konjektor",
        "sigorta", "role", "tik sesi", "kutup basi", "voltaj dusuk", "akinti", "kacak"
    ],
}


class AutomotiveTokenizer:
    """Sub-millisecond, typo-tolerant bilingual morphological tokenizer."""

    TURKISH_CHAR_MAP = str.maketrans({
        "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i", "İ": "i",
        "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u"
    })

    COMMON_SUFFIXES = [
        "lerden", "lardan", "lerinden", "larindan", "lerinde", "larinda", "lerinin", "larinin",
        "lerdeki", "lardaki", "dan", "den", "tan", "ten", "nin", "nin", "nun", "nün", "in", "in", "un", "ün",
        "ler", "lar", "daki", "deki", "teki", "taki", "e", "a", "ye", "ya", "de", "da", "te", "ta",
        "ing", "ed", "s", "es", "tion", "tions", "ment"
    ]

    @classmethod
    def normalize_text(cls, text: str) -> str:
        """Strip accents, lowercase, and clean punctuation."""
        lowered = text.translate(cls.TURKISH_CHAR_MAP).lower()
        cleaned = re.sub(r"[^\w\s\-\.]", " ", lowered)
        return re.sub(r"\s+", " ", cleaned).strip()

    @classmethod
    def lemmatize_word(cls, word: str) -> str:
        """Deterministic stemmer stripping common automotive nominal suffixes."""
        if len(word) <= 4:
            return word
        for suffix in sorted(cls.COMMON_SUFFIXES, key=len, reverse=True):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                return word[:-len(suffix)]
        return word

    @classmethod
    def extract_semantic_intents(cls, text: str) -> dict[str, float]:
        """Extract matching domain intents with confidence score (0.0 to 1.0)."""
        norm_text = cls.normalize_text(text)
        tokens = [cls.lemmatize_word(w) for w in norm_text.split()]
        scores: dict[str, float] = {}

        for domain, keywords in AUTOMOTIVE_SEMANTIC_DICTIONARY.items():
            match_count = 0.0
            for kw in keywords:
                norm_kw = cls.normalize_text(kw)
                if " " in norm_kw:
                    if norm_kw in norm_text:
                        match_count += 2.5
                else:
                    lem_kw = cls.lemmatize_word(norm_kw)
                    if lem_kw in tokens or norm_kw in tokens:
                        match_count += 1.0
                    else:
                        for t in tokens:
                            # A3-10: the typo path accepts a single edit (<=1).
                            # `_levenshtein_distance` is Damerau (transposition
                            # costs 1), so "misfire"↔"misfrie" used to score 0.8
                            # on the wrong token. Typo tolerance now uses the
                            # CLASSIC Levenshtein distance, where a transposition
                            # costs 2 — it is only reachable at distance 0 (exact
                            # match), never as a near-match.
                            if len(t) >= 5 and cls._classic_levenshtein(t, lem_kw) <= 1:
                                match_count += 0.8
                                break
            if match_count > 0:
                scores[domain] = min(1.0, match_count / 3.0)

        return scores

    @staticmethod
    def _classic_levenshtein(s1: str, s2: str) -> int:
        """Plain Levenshtein edit distance (no Damerau transposition).

        A transposition (adjacent swap) costs 2 here, so it can never satisfy a
        ``<= 1`` typo threshold (A3-10). Early-exits at 2 when the lengths differ
        by more than one edit.
        """
        if abs(len(s1) - len(s2)) > 1:
            return 2
        if s1 == s2:
            return 0
        prev = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1, start=1):
            cur = [i]
            for j, c2 in enumerate(s2, start=1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (c1 != c2)))
            prev = cur
        return prev[-1]

    @staticmethod
    def _levenshtein_distance(s1: str, s2: str) -> int:
        if abs(len(s1) - len(s2)) > 1:
            return 2
        if s1 == s2:
            return 0
        d: dict[tuple[int, int], int] = {}
        len1, len2 = len(s1), len(s2)
        for i in range(-1, len1 + 1):
            d[(i, -1)] = i + 1
        for j in range(-1, len2 + 1):
            d[(-1, j)] = j + 1
        for i in range(len1):
            for j in range(len2):
                cost = 0 if s1[i] == s2[j] else 1
                d[(i, j)] = min(
                    d[(i - 1, j)] + 1,
                    d[(i, j - 1)] + 1,
                    d[(i - 1, j - 1)] + cost
                )
                if i > 0 and j > 0 and s1[i] == s2[j - 1] and s1[i - 1] == s2[j]:
                    d[(i, j)] = min(d[(i, j)], d[(i - 2, j - 2)] + 1)
        return d[(len1 - 1, len2 - 1)]


# ============================================================================
# T70-A: VALIDATED PROCEDURE CORPUS READER
# ============================================================================
#
# MEASURED DEFECT (T70-A): `data/knowledge/dtc_procedures/` holds 601
# schema-validated procedure files (B 15 / C 15 / P 169 / S 350 / U 52) with
# branching guidance (`pass_next` / `fail_next`), 1,084 yes/no questions, 601
# `measurement_steps` lists and 601 `expected_values` maps. The corpus had
# exactly TWO consumers in the whole tree — `desktop_app.get_dtc_info` and
# `dialogue_engine` question generation — and NEITHER deep-dive report
# (`_format_4stage_technician_report`, `_format_j1939_technician_report`) ever
# imported `procedure_validator`. An operator who asked "P0300 nedir" received
# the KB record but never the validated branch tree the corpus already carries
# for that same code.
#
# Coverage measured against the live keys: 261 EXPERT_KNOWLEDGE_BASE keys
# resolve a procedure after normalisation (251 DTC codes + 10 SPN keys); the
# remaining 340 `SPN_*` files are reached through the same resolver.
#
# NO FABRICATION (AGENTS.md §2.3): every line below is copied from the
# validated file. An absent field produces no line — never a placeholder.

#: Key spellings tried, in fixed order. Files are named with the human form
#: (`SPN 100.json`) while the engine's SPN keys are compact (`SPN100`), so both
#: spellings must resolve; `{n}` marks the numeric form.
_PROCEDURE_KEY_FORMS: tuple[str, ...] = ("{code}", "SPN {n}", "SPN_{n}")


def _procedure_for_code(code: str) -> Any | None:
    """Resolve the validated procedure for ``code`` across key spellings.

    Returns a ``DtcProcedure`` or ``None``. The order is fixed so the same
    input always resolves the same file; ``get_procedure``'s lazy cache is
    reused, so a hit costs one dict lookup.
    """
    _c = str(code or "").strip().upper()
    if not _c:
        return None
    # Imported lazily (M-18 precedent): the corpus must not be touched at
    # module import time.
    from src.engine.ai.procedure_validator import get_procedure

    _n = _c[3:] if _c.startswith("SPN") else ""
    for _form in _PROCEDURE_KEY_FORMS:
        if "{n}" in _form and not _n:
            continue
        _key = _form.format(code=_c, n=_n)
        proc = get_procedure(_key)
        if proc is not None:
            return proc
    return None


def procedure_corpus_block(code: str, *, include_header: bool = True) -> str:
    """Render the validated procedure-corpus entry for ``code`` (T70-A).

    Deterministic and offline: the corpus is local JSON validated against
    ``procedure_validator``'s schema. The block is emitted ONLY when a
    procedure exists — a code without one renders nothing at all (fail-safe,
    exactly like the T67-C readers).

    Rendered, in corpus order: ``system``, ``symptoms``, every ``questions``
    entry, every ``measurement_steps`` entry with its ``test_type`` and
    ``safety_note``, ``expected_values``, the ``pass_next`` / ``fail_next``
    conditional branches, and every ``safety_notes`` line.

    ``include_header=False`` drops the section title and the leading blank
    line — used by the multi-DTC report, which supplies its own per-code
    heading and would otherwise repeat the title once per code.
    """
    proc = _procedure_for_code(code)
    if proc is None:
        return ""
    lines: list[str] = []
    _sys = str(getattr(proc, "system", "") or "").strip()
    if _sys:
        lines.append(f"  • **Sistem:** {_sys}")
    _sym = [str(s).strip() for s in (getattr(proc, "symptoms", ()) or ()) if str(s).strip()]
    if _sym:
        lines.append("  • **Belirtiler:** " + "; ".join(_sym))
    _qs = list(getattr(proc, "questions", ()) or ())
    if _qs:
        lines.append("  • **Doğrulama Soruları (korpus):**")
        for _q in _qs:
            if not isinstance(_q, dict):
                continue
            _qt = str(_q.get("text", "") or "").strip()
            if not _qt:
                continue
            _qid = str(_q.get("id", "") or "").strip()
            _why = str(_q.get("why", "") or "").strip()
            lines.append(f"    – {_qt}" + (f" *(id: {_qid})*" if _qid else ""))
            if _why:
                lines.append(f"      *Gerekçe:* {_why}")
    _ms = list(getattr(proc, "measurement_steps", ()) or ())
    if _ms:
        lines.append("  • **Ölçüm Adımları (korpus):**")
        for _m in _ms:
            if not isinstance(_m, dict):
                continue
            _mt = str(_m.get("target", "") or "").strip()
            _tt = str(_m.get("test_type", "") or "").strip()
            _sn = str(_m.get("safety_note", "") or "").strip()
            _num = _m.get("step")
            if not (_mt or _tt):
                continue
            _prefix = f"{_num}. " if isinstance(_num, int) else ""
            lines.append(f"    – {_prefix}{_mt}" + (f" *({_tt})*" if _tt else ""))
            if _sn:
                lines.append(f"      ⚠️ {_sn}")
    _ev = getattr(proc, "expected_values", None)
    if isinstance(_ev, dict) and _ev:
        _ev_txt = ", ".join(f"`{k}` = {v}" for k, v in _ev.items())
        lines.append(f"  • **Beklenen Değerler (korpus):** {_ev_txt}")
    _pass = str(getattr(proc, "pass_next", "") or "").strip()
    _fail = str(getattr(proc, "fail_next", "") or "").strip()
    if _pass or _fail:
        lines.append("  • **Koşullu Dallanma (korpus):**")
        if _pass:
            lines.append(f"    – ✔️ *Geçti ise:* {_pass}")
        if _fail:
            lines.append(f"    – ❌ *Kaldı ise:* {_fail}")
    _safety = [str(s).strip() for s in (getattr(proc, "safety_notes", ()) or ()) if str(s).strip()]
    if _safety:
        lines.append("  • **Güvenlik Notları (korpus):** " + " | ".join(_safety))
    if not lines:
        return ""
    if not include_header:
        return "\n".join(lines)
    return (
        "\n\n📐 **Doğrulanmış Prosedür Kütüphanesi (korpus `dtc_procedures`):**\n"
        + "\n".join(lines)
    )


# ============================================================================
# T70-B: PHYSICAL-ENVELOPE SWEEP OVER SUPPLIED MEASUREMENTS
# ============================================================================
#
# MEASURED DEFECT (T70-B): `anomaly_detector.PHYSICAL_BOUNDS`-style plausibility
# checking exists in two places, and NEITHER sees the measurements the copilot
# renders:
#   * `procedure_validator.PHYSICAL_BOUNDS` runs only while LOADING a procedure
#     JSON — it validates authored data, never live telemetry.
#   * `anomaly_detector.detect_anomalies` scans a `VehicleSession`'s recorded
#     SAMPLE STREAM; the copilot's reports take a `telemetry` SNAPSHOT dict and
#     never call it.
# So a physically impossible reading (a 350 km/h road speed, a -40 °C coolant
# value on a running engine) passed straight through every report.
#
# The table below is DERIVED, not invented: every pair is copied from the
# existing `_THRESHOLD_SIGNAL_FOR_CODE` (telemetry key -> threshold-DB key),
# which already covers all seven signals in `telemetry_thresholds.json`.
# `test_t70b_telemetry_plausibility.py` pins the two tables against each other,
# so editing one without the other fails the suite.
_TELEMETRY_TO_THRESHOLD_KEY: dict[str, str] = {
    "BoostPressure": "TurboBoost",
    "CoolantTemp": "EngineCoolantTemp",
    "OilPressure": "EngineOilPressure",
    "EngineSpeed": "EngineSpeed",
    "VehicleSpeed": "VehicleSpeed",
    "EngineLoad": "EngineLoad",
    "EngineTorque": "EngineTorque",
}

#: Extra spellings accepted for the same physical quantity (source forms taken
#: from `data/diagnostics/signal_aliases.json`, which documents each pairing).
_TELEMETRY_SIGNAL_ALIASES: dict[str, str] = {
    "CoolantTemperature": "EngineCoolantTemp",
    "EngineCoolantTemperature": "EngineCoolantTemp",
    "coolant_temp": "EngineCoolantTemp",
    "engine_coolant_temp": "EngineCoolantTemp",
    "TurboBoost": "TurboBoost",
    "Boost": "TurboBoost",
    "boost_pressure": "TurboBoost",
    "EngineOilPressure": "EngineOilPressure",
    "oil_pressure": "EngineOilPressure",
    "RPM": "EngineSpeed",
    "engine_speed": "EngineSpeed",
    "VSS": "VehicleSpeed",
    "vehicle_speed": "VehicleSpeed",
    "EngineLoadPct": "EngineLoad",
    "engine_load": "EngineLoad",
    "EngineTorquePct": "EngineTorque",
    "engine_torque": "EngineTorque",
}


def _threshold_entry_for_signal(
    signal: str, thresholds: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Resolve a telemetry key to its threshold-DB entry, or ``None``.

    Resolution order is fixed: exact DB key, then the derived table, then the
    documented alias spellings. An unresolved signal yields ``None`` and is
    therefore NOT judged — the honest outcome, since no envelope is known.
    """
    if not isinstance(signal, str) or not signal:
        return None
    if signal in thresholds:
        return thresholds[signal]
    key = _TELEMETRY_TO_THRESHOLD_KEY.get(signal) or _TELEMETRY_SIGNAL_ALIASES.get(signal)
    if key is None:
        return None
    entry = thresholds.get(key)
    return entry if isinstance(entry, dict) else None


def telemetry_plausibility_block(telemetry: dict[str, Any]) -> str:
    """Flag measurements outside their recorded physical envelope (T70-B).

    Deterministic and offline. Reads only ``telemetry_thresholds.json`` — the
    same KB-derived DB the consistency line already uses — and compares each
    supplied measurement against the WIDEST envelope across the signal's
    recorded RPM bands (``_band_for(ranges, None)``). That envelope is the
    physical plausibility limit, not a diagnostic band: a value inside it is
    simply not flagged here, and the per-code consistency line remains the
    place where nominal-band judgement happens.

    Emits NOTHING when every supplied value is inside its envelope, when no
    supplied signal has a recorded envelope, or when the DB cannot be read
    (fail-safe — a broken threshold DB must never break a report). Private
    keys (leading ``_``, e.g. ``_query_fmi``) and non-numeric values are
    skipped: they are engine context, not measurements.
    """
    if not isinstance(telemetry, dict) or not telemetry:
        return ""
    try:
        from src.engine.ai.anomaly_detector import _band_for, load_thresholds

        thresholds = load_thresholds()
    except Exception:  # noqa: BLE001 — a broken threshold DB never breaks a report
        return ""

    violations: list[str] = []
    for signal in sorted(telemetry):
        if signal.startswith("_"):
            continue  # engine context (e.g. _query_fmi), not a measurement
        value = telemetry.get(signal)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue  # not measured -> say nothing (F-02 / P0-5)
        entry = _threshold_entry_for_signal(signal, thresholds)
        if entry is None:
            continue
        band = _band_for(entry.get("ranges", []), None)
        if band is None:
            continue
        lo, hi = band.get("min"), band.get("max")
        if lo is None and hi is None:
            continue
        unit = str(entry.get("unit", "") or "")
        _below = isinstance(lo, (int, float)) and value < lo
        _above = isinstance(hi, (int, float)) and value > hi
        if not (_below or _above):
            continue
        _lim = f"{lo:g}" if _below else f"{hi:g}"
        _dir = "altında" if _below else "üstünde"
        violations.append(
            f"  • **{signal}**: {float(value):g} {unit} — kayıtlı fiziksel zarfın "
            f"({_lim} {unit}) {_dir}. Bu değer FİZİKSEL OLARAK ŞÜPHELİ; ölçüm "
            f"hattını (sensör, tesisat, DBC ölçekleme) doğrulamadan teşhisi "
            f"bu okumaya dayandırmayın."
        )
    if not violations:
        return ""
    return (
        "\n🌡️ **Fiziksel Sınır Denetimi (ölçülen değerler):**\n"
        + "\n".join(violations)
        + "\n"
    )


# ============================================================================
# T70-C: BMS CELL-IMBALANCE CROSS-CHECK
# ============================================================================
#
# MEASURED DEFECT (T70-C): the cell-voltage spread was computed in exactly ONE
# place — the CAN-frame forensics branch (`analyze_can_frame`, 0x1808E5F4) —
# and the DTC diagnosis path never touched it. An operator who asked
# "P0A80 nedir" while both cell-extreme channels were live received the KB
# text "Arıza / Değişim Eşiği: >150 mV" as PROSE, with no comparison against
# the measurement actually in hand.
#
# Thresholds are DERIVED FROM THE KNOWLEDGE BASE, not invented. P0A80's own
# `measurement` field reads:
#
#   "Nominal Hücre Delta Voltajı: <30 mV | Arıza / Değişim Eşiği:
#    >150 mV (Yükte) veya >50 mV (Dengede)."
#
# `test_t70c_bms_cell_imbalance.py` re-reads that KB string and asserts every
# number below still appears in it, so the table cannot silently drift from
# its source.

#: code -> (nominal_max_mv, fault_rest_mv, fault_load_mv, source_note)
_BMS_DELTA_THRESHOLDS: dict[str, tuple[float, float, float, str]] = {
    "P0A80": (30.0, 50.0, 150.0, "KB `P0A80.measurement`"),
}

#: The two telemetry channels whose difference IS the cell spread. Both must be
#: present: a spread computed from one extreme is not a measurement.
_BMS_CELL_MIN_KEY = "bms_cell_voltage_min_v"
_BMS_CELL_MAX_KEY = "bms_cell_voltage_max_v"


def bms_cell_imbalance_block(code: str, telemetry: dict[str, Any]) -> str:
    """Cross-check the measured cell spread against the KB thresholds (T70-C).

    Emits NOTHING unless ALL of the following hold (fail-safe, AGENTS.md §2.3):
      * the code has a recorded ΔV threshold (``_BMS_DELTA_THRESHOLDS``)
      * BOTH cell-extreme channels were supplied as numbers
      * the two values are physically coherent (max >= min)

    The verdict is stated against the REST threshold (``fault_rest_mv``) and,
    when the spread also exceeds the LOAD threshold, against that too — the KB
    distinguishes them and the engine has no current channel with which to
    choose, so both are reported rather than one being guessed.
    """
    _c = str(code or "").strip().upper()
    entry = _BMS_DELTA_THRESHOLDS.get(_c)
    if entry is None:
        return ""
    nominal_max, fault_rest, fault_load, source = entry
    lo = telemetry.get(_BMS_CELL_MIN_KEY)
    hi = telemetry.get(_BMS_CELL_MAX_KEY)
    if isinstance(lo, bool) or isinstance(hi, bool):
        return ""
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return ""  # one extreme is not a measurement (F-02 / P0-5)
    if float(hi) < float(lo):
        # Incoherent input: report the inconsistency, never a spread from it.
        return (
            "\n🔋 **Hücre Dengesi (ΔV):**\n"
            f"  • ⚠️ Ölçüm tutarsız: min ({float(lo):.3f} V) > max "
            f"({float(hi):.3f} V). ΔV hesaplanmadı — kanalları doğrulayın.\n"
        )
    delta_mv = (float(hi) - float(lo)) * 1000.0
    lines = [
        f"  • **ΔV = {delta_mv:.1f} mV** "
        f"(min {float(lo):.3f} V / max {float(hi):.3f} V) — {source}"
    ]
    if delta_mv > fault_load:
        lines.append(
            f"  • ⛔ ΔV kayıtlı **yük altı değişim eşiğinin** ({fault_load:g} mV) "
            f"ÜZERİNDE — ölçüm bu tanıyı DESTEKLİYOR."
        )
    elif delta_mv > fault_rest:
        lines.append(
            f"  • ⚠️ ΔV kayıtlı **denge (yüksüz) eşiğinin** ({fault_rest:g} mV) "
            f"ÜZERİNDE, yük altı eşiğinin ({fault_load:g} mV) altında — ölçüm "
            f"bu tanıyı KISMEN destekliyor; ölçümü yük altında tekrarlayın."
        )
    elif delta_mv <= nominal_max:
        lines.append(
            f"  • ✔️ ΔV kayıtlı nominal sınırın ({nominal_max:g} mV) içinde — "
            f"ölçüm bu tanıyı DESTEKLEMİYOR; başka bir kök neden arayın."
        )
    else:
        lines.append(
            f"  • ΔV nominal sınırın ({nominal_max:g} mV) üzerinde ama kayıtlı "
            f"değişim eşiklerinin altında — sınırda; eğilimi izleyin."
        )
    return "\n🔋 **Hücre Dengesi (ΔV Çapraz Kontrolü):**\n" + "\n".join(lines) + "\n"


# ============================================================================
# CAUSAL BAYESIAN & DETERMINISTIC INFERENCE ENGINE
# ============================================================================

class CausalBayesianInferenceEngine:
    """Exact probabilistic inference calculating P(Fault_i | Evidence) and synthesizing 4-stage technician reports."""

    @staticmethod
    def explain_can_packet(
        can_id_hex_or_int: str | int,
        payload: bytes | list[int] | str = b"",
    ) -> str:
        text, actions = explain_can_packet(can_id_hex_or_int, payload)
        return attach_action_triggers(text, actions)

    @staticmethod
    def explain_traffic_metrics(
        bus_metrics: dict[str, Any],
        user_query: str = "",
    ) -> str:
        return explain_traffic_metrics(bus_metrics, user_query)

    @staticmethod
    def explain_can_frame_mode06(
        can_id_hex_or_int: str | int,
        payload: bytes | list[int] | str = b"",
    ) -> tuple[str, bool]:
        """Consume the Mode $06 monitor catalog for a CAN frame (A3-13).

        Parses the arbitration ID (or the payload's first bytes) for an
        OBD-II Mode $06 MID, then describes the matching on-board monitor and
        test from ``obd_mode06_database.json`` via :func:`format_mode06_monitor`.
        Returns ``(text, matched)`` where ``matched`` is ``True`` only when the
        MID was present in the catalog — callers can fall back honestly.
        """
        if isinstance(can_id_hex_or_int, str):
            cleaned = can_id_hex_or_int.strip().lower()
            try:
                if cleaned.startswith("0x"):
                    can_id = int(cleaned, 16)
                elif cleaned and all(c in "0123456789abcdef" for c in cleaned):
                    can_id = int(cleaned, 16)
                else:
                    can_id = 0
            except ValueError:
                can_id = 0
        else:
            can_id = int(can_id_hex_or_int)

        if isinstance(payload, str):
            payload_bytes = [int(t, 16) for t in re.findall(r"\b[0-9A-Fa-f]{2}\b", payload)]
        elif isinstance(payload, bytes):
            payload_bytes = list(payload)
        else:
            payload_bytes = list(payload)

        mid = _mode06_mid_from_can_id(can_id)
        tid: int | None = None
        # F-28: accept the ISO-TP single-frame shape as well as a raw payload.
        # A single frame prefixes the length in the high nibble of byte 0
        # (`0xN6 0x46 <MID> <TID> ...`), so the literal 0x46 is at index 1, not
        # 0. Without this the payload branch never matched a real overnight
        # frame and the catalog lookup was unreachable in production.
        payload_sid_idx = _resolve_uds_sid_index(payload_bytes) if payload_bytes else 0
        if (
            mid is None
            and len(payload_bytes) > payload_sid_idx
            and payload_bytes[payload_sid_idx] == 0x46
            and len(payload_bytes) > payload_sid_idx + 1
        ):
            # Mode $06 response: 0x46 <MID> <TID> <data...>
            mid = payload_bytes[payload_sid_idx + 1]
            tid = (
                payload_bytes[payload_sid_idx + 2]
                if len(payload_bytes) > payload_sid_idx + 2
                else None
            )
        elif mid is not None and len(payload_bytes) >= 1:
            tid = payload_bytes[0]

        if mid is None:
            return ("Mode $06 MID çözülemedi — bu çerçeve bir izleme testi taşımıyor.", False)

        db = get_mode06_database()
        monitors = db.get("monitors") if isinstance(db, dict) else None
        matched = bool(monitors) and (
            f"0x{mid:02X}" in monitors
            or any(isinstance(v, dict) and v.get("mid_int") == mid for v in (monitors or {}).values())
        )
        return (format_mode06_monitor(mid, tid), matched)

    @classmethod
    def analyze_can_frame(
        cls,
        user_query: str,
        norm_query: str,
        telemetry: dict[str, Any] | None = None,
    ) -> str | None:
        """Forensic decoding of a specific CAN frame (SAE J1939, OBD-II/UDS, or raw CAN)."""
        if "nrc" in norm_query or "negatif yanit" in norm_query:
            return None

        can_id_match = re.search(r"\b0x([0-9A-Fa-f]{1,8})\b", user_query)
        if not can_id_match:
            return None

        can_id_hex = f"0x{can_id_match.group(1).upper()}"
        can_id_int = int(can_id_match.group(1), 16)

        # 0. CAN Physical Layer Error Frame
        is_error_frame = "(ERR)" in user_query or "Error Frame" in user_query or "isErrorFrame" in user_query or "hata karesi" in norm_query or can_id_hex in {"0X00000000", "0X0000000", "0X0", "0X00"}
        if is_error_frame and not re.search(r"\b([PBUC][0-9A-Fa-f]{4})\b", user_query):
            return (
                "🔴 **CAN Hata Karesi (Error Frame / Bus Error):**\n"
                "• **Durum:** Fiziksel katman hatası (Bit Stuffing veya CRC hatası / Active Error Flag) nedeniyle çerçeve iletimi durduruldu.\n"
                "• **Olası Nedenler:** Hat paraziti, sonlandırma direnci eksikliği veya yanlış baudrate.\n"
                "• **Hızlı Test:** OBD Pin 6 (CAN-H) ve Pin 14 (CAN-L) arası direnci ölçün (Nominal: 60.0 Ω ±3Ω / 120Ω sonlandırma)."
            )

        # EV BMS Specific Frames (0x1808E5F4, 0x1807E5F4, etc.)
        if "1808E5" in can_id_hex or "0X1808E5F4" in can_id_hex:
            meas_str = "• **Canlı Ölçüm:** Canlı ölçüm yok (BMS telemetrisi bekleniyor)."
            if telemetry:
                min_v = telemetry.get("bms_cell_voltage_min_v")
                max_v = telemetry.get("bms_cell_voltage_max_v")
                if min_v is not None and max_v is not None:
                    delta_v = float(max_v) - float(min_v)
                    meas_str = (
                        f"• **Canlı Ölçüm:** Min: {float(min_v):.3f}V, Max: {float(max_v):.3f}V "
                        f"| Delta V: {delta_v * 1000:.1f} mV (ölçüm)"
                    )
                elif min_v is not None or max_v is not None:
                    parts = []
                    if min_v is not None:
                        parts.append(f"Min: {float(min_v):.3f}V")
                    if max_v is not None:
                        parts.append(f"Max: {float(max_v):.3f}V")
                    meas_str = f"• **Canlı Ölçüm:** {', '.join(parts)} (ölçüm)"
            return (
                f"⚡ **EV BMS Hücre Voltajları ({can_id_hex} - PGN 61447):**\n"
                f"• **Protokol:** ISO 11898-2 (EV Yüksek Voltaj BMS)\n"
                f"• **Kaynak Düğüm:** Batarya Yönetim Sistemi (BMS ECU - 0xF4)\n"
                f"{meas_str}\n"
                f"• **Hedef:** Hücre voltaj farkı <30 mV olmalıdır."
            )
        if "1807E5" in can_id_hex or "0X1807E5F4" in can_id_hex:
            return (
                "⚡ **EV BMS Şarj & Sağlık (0x1807E5F4 - PGN 61446):**\n"
                "• **Protokol:** ISO 11898-2 (BMS ECU 0xF4)\n"
                "• **Açıklama:** Batarya SOC (Şarj) ve SOH (Sağlık) durumu."
            )
        if "1809E5" in can_id_hex or "0X1809E5F4" in can_id_hex:
            return (
                "⚡ **EV BMS Termal Yönetimi (0x1809E5F4 - PGN 61448):**\n"
                "• **Protokol:** ISO 11898-2 (BMS ECU 0xF4)\n"
                "• **Açıklama:** Batarya paketi ve hücre modülü sıcaklıkları."
            )
        if "18F020" in can_id_hex or "0X18F020F4" in can_id_hex:
            return (
                "⚡ **EV BMS Yüksek Voltaj İzolasyonu & Kontaktör Güvenliği (0x18F020F4):**\n"
                "• **Protokol:** ISO 11898-2 (BMS ECU 0xF4)\n"
                "• **Açıklama:** HV izolasyon direnci ve kontaktör durumları."
            )

        # Extract payload bytes if present, e.g. "DATA: 00 EE 00" or "[00, EE, 00]"
        data_match = re.search(r"(?:data|veri|payload)[\s:=]+([0-9a-fA-F\s,]+)", user_query, re.IGNORECASE)
        data_bytes: list[int] = []
        if data_match:
            data_bytes = [int(b, 16) for b in re.findall(r"\b[0-9A-Fa-f]{2}\b", data_match.group(1))]

        chan_match = re.search(r"\b(vcan\d+|can\d+|PCAN\w+|kvaser\w+|rp1210\S*)\b", user_query, re.IGNORECASE)
        channel_str = chan_match.group(1) if chan_match else "CAN"

        is_extended = can_id_int > 0x7FF or len(can_id_match.group(1)) > 3

        if is_extended:
            priority = (can_id_int >> 26) & 0x07
            _edp = (can_id_int >> 25) & 0x01
            dp = (can_id_int >> 24) & 0x01
            pf = (can_id_int >> 16) & 0xFF
            ps = (can_id_int >> 8) & 0xFF
            sa = can_id_int & 0xFF

            if pf < 240:
                da = ps
                pgn = (dp << 16) | (pf << 8)
                pdu_str = f"PDU1 (Noktadan Noktaya / Hedef: 0x{da:02X})"
            else:
                da = 0xFF
                pgn = (dp << 16) | (pf << 8) | ps
                pdu_str = "PDU2 (Yayın / Broadcast)"

            # PGN 59904: ISO Request
            if pgn == 59904:
                req_pgn_str = "Belirtilmedi"
                if len(data_bytes) >= 3:
                    req_pgn_num = data_bytes[0] | (data_bytes[1] << 8) | (data_bytes[2] << 16)
                    if req_pgn_num == 60928:
                        req_pgn_str = "PGN 60928 (Address Claimed / Adres Bildirimi — 0x00EE00)"
                    elif req_pgn_num == 65226:
                        req_pgn_str = "PGN 65226 (DM1 Aktif Arıza Kodları — 0x00FECA)"
                    elif req_pgn_num == 65227:
                        req_pgn_str = "PGN 65227 (DM2 Geçmiş Arıza Kodları — 0x00FECB)"
                    else:
                        req_pgn_str = f"PGN {req_pgn_num} (0x{req_pgn_num:04X})"

                has_conflict = any(w in norm_query for w in ["çakışma", "cakisma", "red", "reddi", "ack", "anomali", "nack", "istem"])
                if has_conflict:
                    anomaly_sec = (
                        "⚠️ **Tespit Edilen Anomali & Teşhis:**\n"
                        "• **Durum:** Adres İsteme Çakışması / PGN 59904 ACK Reddi (NACK).\n"
                        "• **Açıklama:** Ağdaki bir kontrol ünitesi veya tanı cihazı (`SA: 0xFE`), ağdan adres beyanı (PGN 60928 - Address Claimed) talep etmiştir. "
                        "Ancak bu sorguya ağdaki bir düğüm tarafından PGN 59392 üzerinden Negatif Onay (NACK / ACK Reddi) dönülmüş veya "
                        "aynı kaynak adresi için çakışma meydana gelerek cihaz ağa katılamamıştır.\n\n"
                        "🛠️ **Usta Saha Kontrol Adımları:**\n"
                        "1. **Adresleme:** Ağda `0xFE` (Service Tool) adresini talep eden ikinci bir tanı cihazı veya gateway olup olmadığını doğrulayın.\n"
                        "2. **Address Claiming Protokolü:** Düğümün Dinamik Adres Yeteneğini (Arbitrary Address Capable) ve J1939 NAME kimlik önceliğini inceleyin.\n"
                        "3. **Fiziksel Hat & Direnç:** Veri yolunda 120Ω sonlandırma direncini ve ACK üretecek diğer düğümlerin aktifliğini doğrulayın."
                    )
                else:
                    anomaly_sec = (
                        "ℹ️ **İşlev Açıklaması:**\n"
                        f"Bu çerçeve, `SA: 0x{sa:02X}` adresindeki cihazın ağdaki tüm birimlerden `{req_pgn_str}` bilgisini talep ettiği bir sorgu mesajıdır."
                    )

                return (
                    f"🚛 **SAE J1939 Çerçeve Analizi: {can_id_hex} (PGN 59904 - ISO Request)**\n\n"
                    f"• **Protokol:** SAE J1939-21 Ağ Yönetimi / İstek Çerçevesi\n"
                    f"• **Öncelik:** {priority} | **Biçim:** {pdu_str}\n"
                    f"• **Kaynak Adres (SA):** `0x{sa:02X}` ({'Teşhis / Servis Cihazı' if sa == 0xFE else 'ECU Düğümü'})\n"
                    f"• **Hedef Adres (DA):** `0x{da:02X}` ({'Tüm Ağ (Broadcast)' if da == 0xFF else 'Özel Düğüm'})\n"
                    f"• **Talep Edilen PGN:** **{req_pgn_str}**\n"
                    f"• **Veri Yükü (Payload):** `{' '.join(f'{b:02X}' for b in data_bytes) if data_bytes else 'N/A'}`\n\n"
                    f"{anomaly_sec}"
                )

            elif pgn == 60928:
                return (
                    f"🚛 **SAE J1939 Adres Bildirimi: {can_id_hex} (PGN 60928 - Address Claimed)**\n\n"
                    f"• **Kaynak Düğüm:** `0x{sa:02X}` | **Öncelik:** {priority}\n"
                    f"• **Açıklama:** Düğüm kendi 64-bit NAME kimliğini yayınlayarak bu adresi talep etmiştir."
                )

            elif pgn == 59392:
                ack_type = "Bilinmiyor"
                if data_bytes:
                    ctrl = data_bytes[0]
                    ack_type = "Pozitif Onay (ACK)" if ctrl == 0 else "Negatif Onay / Red (NACK)" if ctrl == 1 else "Erişim Reddedildi"
                return (
                    f"🚛 **SAE J1939 Bildirim Onayı: {can_id_hex} (PGN 59392 - Acknowledgment)**\n\n"
                    f"• **Onay Durumu:** **{ack_type}**\n"
                    f"• **Kaynak:** `0x{sa:02X}` ➔ **Hedef:** `0x{da:02X}`"
                )

            elif pgn == 65226:
                return (
                    f"🚛 **SAE J1939 DM1 Aktif Arıza Kodu Karesi ({can_id_hex})**\n\n"
                    f"• **PGN:** 65226 (0xFECA) - Active Diagnostic Trouble Codes\n"
                    f"• **Kaynak Düğüm:** `0x{sa:02X}`\n"
                    f"• **Veri Yükü:** `{' '.join(f'{b:02X}' for b in data_bytes)}`\n"
                    f"• **Açıklama:** Ağır vasıta motor veya fren kontrol ünitesi aktif MIL ve SPN/FMI arızalarını yayınlıyor."
                )

            elif pgn == 61444:
                rpm_str = "Hesaplanamadı"
                if len(data_bytes) >= 5:
                    rpm_val = ((data_bytes[4] << 8) | data_bytes[3]) * 0.125
                    rpm_str = f"**{rpm_val:.1f} RPM**"
                return (
                    f"🚛 **SAE J1939 EEC1 Elektronik Motor Kontrolü ({can_id_hex})**\n\n"
                    f"• **PGN:** 61444 (0xF004) - Engine Speed & Torque\n"
                    f"• **Motor Devri (SPN 190):** {rpm_str}\n"
                    f"• **Kaynak ECU:** `0x{sa:02X}`"
                )

            return (
                f"📡 **29-Bit Genişletilmiş CAN / J1939 Çerçevesi: {can_id_hex}**\n\n"
                f"• **PGN:** **{pgn}** (0x{pgn:04X}) | **Öncelik:** {priority}\n"
                f"• **Kaynak Adres (SA):** `0x{sa:02X}` | **Hedef (DA):** `0x{da:02X}`\n"
                f"• **Veri Yolu:** {channel_str} | **DLC:** {len(data_bytes)} bayt\n"
                f"• **Veri (Hex):** `{' '.join(f'{b:02X}' for b in data_bytes) if data_bytes else 'N/A'}`\n\n"
                f"ℹ️ J1939 PDU2/PDU1 standart formatında telemetri yayını."
            )

        # 11-Bit Standard Frame
        if can_id_int == 0x7DF:
            return (
                f"🚗 **OBD-II / UDS Fonksiyonel Yayın İsteği ({can_id_hex})**\n\n"
                f"• **Protokol:** ISO 15765-4 / SAE J1979 OBD-II\n"
                f"• **İşlev:** Araçtaki tüm ECU'lara (ECM, TCM vb.) ortak yayın sorgusu (Broadcast Request).\n"
                f"• **Veri (Hex):** `{' '.join(f'{b:02X}' for b in data_bytes)}`"
            )

        if 0x7E0 <= can_id_int <= 0x7E7:
            ecu_idx = can_id_int - 0x7E0
            return (
                f"🚗 **UDS / ISO-TP Fiziksel ECU İstek Karesi ({can_id_hex})**\n\n"
                f"• **Hedef ECU:** ECU #{ecu_idx} (0x{can_id_int:03X})\n"
                f"• **Protokol:** ISO 14229 UDS / ISO 15765-2 DoCAN\n"
                f"• **Veri (Hex):** `{' '.join(f'{b:02X}' for b in data_bytes)}`"
            )

        if 0x7E8 <= can_id_int <= 0x7EF:
            ecu_name = "Motor Kontrol Ünitesi (ECM)" if can_id_int == 0x7E8 else "Şanzıman Kontrol Ünitesi (TCM)" if can_id_int == 0x7E9 else f"ECU 0x{can_id_int:03X}"
            return (
                f"🚗 **UDS / OBD-II Fiziksel ECU Yanıt Karesi ({can_id_hex})**\n\n"
                f"• **Yanıt Veren Düğüm:** **{ecu_name}**\n"
                f"• **Protokol:** ISO 14229 / ISO 15765-2 DoCAN\n"
                f"• **Veri (Hex):** `{' '.join(f'{b:02X}' for b in data_bytes)}`"
            )

        return (
            f"📡 **11-Bit Standart CAN Çerçevesi: {can_id_hex}**\n\n"
            f"• **Kanal:** {channel_str} | **DLC:** {len(data_bytes)} bayt\n"
            f"• **Veri (Hex):** `{' '.join(f'{b:02X}' for b in data_bytes) if data_bytes else 'N/A'}`\n\n"
            f"• **Açıklama:** Standart otomotiv/endüstriyel CAN 2.0B çerçevesi."
        )

    @classmethod
    def evaluate_diagnostic_query(
        cls,
        user_query: str,
        active_dtcs: list[dict[str, Any]],
        telemetry: dict[str, Any],
        bus_metrics: dict[str, Any] | None = None,
    ) -> str:
        """Generate comprehensive 4-stage master technician report."""
        # M-18 (P2-17): knowledge bases load lazily at first diagnostic use
        # (idempotent) instead of at module import.
        ensure_external_dtc_database_loaded()
        if bus_metrics:
            telemetry = {**telemetry, **bus_metrics}
        intents = AutomotiveTokenizer.extract_semantic_intents(user_query)
        norm_query = AutomotiveTokenizer.normalize_text(user_query)
        # T42 P2-2/P2-3: arac markasi (varsa) tek noktada tespit edilir ve
        # OEM varyant filtresi + complaints kanit füzyonuna gecirilir. Marka
        # yoksa None kalir; tum alt yollar eski (filtresiz) davranisi korur.
        vehicle_make = detect_vehicle_make(norm_query)
        # T42 P2-3: yil filtresi icin yil da cikarilir (1980-2026 araligi).
        # Yoksa None kalir ve complaints sorgusu yilsiz calisir (fail-safe).
        _ym = re.search(r"\b(19[89][0-9]|20[0-2][0-9])\b", user_query)
        vehicle_year = int(_ym.group(1)) if _ym else None

        # P0-3 (2026-09-21 audit, finding 7): the query may name an FMI
        # ("SPN 100 FMI 3"). The router keys off the SPN only, so the report
        # rendered the SPN-level rung for EVERY failure mode — an unplugged
        # sensor (FMI 3) was advised "stop the engine" exactly like a real loss
        # of oil pressure (FMI 1). Extract the FMI once and thread it through
        # `telemetry` (already passed to every formatter call) so the report can
        # state the FMI-specific rung. Absent FMI leaves behaviour unchanged.
        # T74: the extraction is the shared `query_fmi_number` helper so the
        # compact form ("SPN100FMI3") resolves here exactly as it does in the
        # report body, and a zero-padded "FMI03" canonicalises to 3.
        _q_fmi_num = query_fmi_number(norm_query)
        if _q_fmi_num is not None and "_query_fmi" not in telemetry:
            telemetry = {**telemetry, "_query_fmi": _q_fmi_num}

        # 0. Dedicated CAN Frame Forensics (e.g. from right-click context menu)
        #
        # T67-E (measured defect): this branch ran FIRST and matched on any
        # 1-8 digit `0x…` token, so a bare CAN id anywhere in the sentence
        # shadowed every diagnostic branch below it. Verified before the fix:
        #   "P0300 nedir (CAN ID 0x7E8)"  -> "🚗 UDS / OBD-II Fiziksel ECU Yanıt Karesi"
        #   "SPN 100 FMI 3 (0x18FEEE00)"  -> "📡 29-Bit Genişletilmiş CAN / J1939 Çerçevesi"
        #   "0x22 f190 vin oku"           -> "📡 11-Bit Standart CAN Çerçevesi: 0x22"
        # The operator asked about a FAULT; the id was context, not the subject.
        # Frame forensics now yields to a query that names a DTC, names an SPN,
        # or asks for a diagnostic action — those branches carry the answer.
        _act_kw = (
            "dtc temizle", "ariza sil", "arizalari sil", "hata kodlarini sil",
            "hafizayi sil", "hafizayi temizle", "clear dtc", "hata sil",
            "vin oku", "sasi no oku", "sasi numarasi oku", "read vin",
            "chassis number", "f190 oku", "dm1", "dm11", "oturum degistir",
            "extended session", "genisletilmis oturum", "session degistir",
            "ecu reset", "ecu sifirla",
        )
        _names_fault = bool(
            re.search(r"\b([PBUC][0-9A-F]{4})\b", user_query, re.IGNORECASE)
            or _SPN_QUERY_RE.search(norm_query)
        )
        _asks_action = any(w in norm_query for w in _act_kw)
        if not (_names_fault or _asks_action):
            frame_report = cls.analyze_can_frame(user_query, norm_query, telemetry)
            if frame_report is not None:
                actions = []
                if "j1939" in frame_report.lower() or "59904" in frame_report:
                    actions = [make_j1939_dm1_action()]
                return attach_action_triggers(frame_report, actions)

        # 0.1 CAN Traffic & Bus Load Anomaly Awareness (General Bus Questions Only)
        # F-01: whole-word matching. The qualifier list previously contained the
        # bare substrings "hat" and "hata", so "hata karesi" (an error-frame
        # question, handled by its own branch below) also satisfied the traffic
        # query and was hijacked into a bus-load report.
        is_traffic_query = any(
            _has_standalone_word(norm_query, w)
            for w in ["trafik", "hat yuku", "bus load", "error frame", "hata karesi", "patlama", "babbling"]
        )
        if is_traffic_query and any(
            _has_standalone_word(norm_query, w)
            for w in ["durum", "nasil", "yuku", "yuzde", "load", "rapor", "analiz", "hat", "hata"]
        ):
            traffic_rep = explain_traffic_metrics(telemetry, user_query)
            # F-25: the previous code unconditionally attached a UDS 0x14
            # "Clear DTC" action to EVERY traffic report — including an empty
            # one. A bus-load question is not a DTC-clear request, and offering
            # a destructive action with no diagnostic evidence is unsafe. The
            # trigger is now minted only when the report actually reflects a
            # measured anomaly (i.e. not the "no data" branch) AND the operator
            # explicitly asked to clear/act.
            actions: list[dict[str, Any]] = []
            if "Veri Yok" not in traffic_rep and any(
                w in norm_query for w in ["temizle", "sil", "clear", "reset"]
            ):
                actions = [make_uds_clear_dtc_action()]
            return attach_action_triggers(traffic_rep, actions)

        # 0.1 Direct Diagnostic Action Requests (UDS & J1939 Actionable Triggers)
        has_dtc_in_query = bool(re.search(r"\b([PBUC][0-9A-F]{4})\b", user_query, re.IGNORECASE))
        has_spn_in_query = bool(_SPN_QUERY_RE.search(norm_query))

        # J1939 DM11 Clear DTC request
        is_dm11_action = any(w in norm_query for w in ["dm11", "j1939 ariza sil", "agir vasita ariza sil", "j1939 temizle", "pgn 65235"])
        if is_dm11_action and not has_dtc_in_query:
            text = (
                "🚛 **SAE J1939 DM11 (PGN 65235 - Diagnostic Data Clear):**\n"
                "• **Protokol:** Ağır vasıta ticari araçlarda aktif ve geçmiş DM1 arıza kayıtlarını siler.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak DM11 silme komutunu gönderebilirsiniz."
            )
            return attach_action_triggers(text, [make_j1939_dm11_action()])

        # J1939 DM1 Query request
        is_dm1_action = any(w in norm_query for w in ["dm1 oku", "j1939 dm1", "agir vasita ariza oku", "aktif ariza oku", "dm1 sorgula"])
        if is_dm1_action and not has_spn_in_query:
            text = (
                "🚛 **SAE J1939 DM1 (PGN 65226 - Active Diagnostic Trouble Codes):**\n"
                "• **Protokol:** Ağır vasıta hattında aktif arıza lambaları (MIL, Red Stop, Amber) ve SPN/FMI kayıtlarını dinler.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak DM1 durumunu sorgulayabilirsiniz."
            )
            return attach_action_triggers(text, [make_j1939_dm1_action()])

        # VIN Read request
        is_vin_action = any(w in norm_query for w in ["vin oku", "sasi no oku", "sasi numarasi oku", "read vin", "chassis number", "f190 oku"]) or (
            ("vin" in norm_query or "sasi" in norm_query) and any(w in norm_query for w in ["nasil", "oku", "nereden", "ogren", "sorgula", "nedir"])
        )
        if is_vin_action and not has_dtc_in_query:
            text = (
                "📄 **UDS 0x22 ReadDataByIdentifier (DID 0xF190 - VIN):**\n"
                "• **Servis:** Araç Şasi Numarası (VIN) doğrudan motor veya gövde kontrol ünitesinden okunur.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak UDS 0x22 F190 sorgusunu yürütebilirsiniz."
            )
            return attach_action_triggers(text, [make_uds_read_vin_action()])

        # Clear DTC request (generic UDS)
        is_clear_action = any(w in norm_query for w in ["dtc temizle", "ariza sil", "arizalari sil", "hata kodlarini sil", "hafizayi sil", "hafizayi temizle", "clear dtc", "hata sil", "0x14"])
        if is_clear_action and not has_dtc_in_query and not has_spn_in_query and not is_dm11_action:
            text = (
                "🧹 **UDS 0x14 ClearDiagnosticInformation (DTC Temizle):**\n"
                "• **Servis:** ECU hata hafızasındaki aktif ve geçmiş tüm DTC arıza kayıtları sıfırlanır.\n"
                "• **Güvenlik:** Çift operatör onayı ve aracın duruyor olması (0.0 km/s) zorunludur.\n"
                "• **İşlem:** Aşağıdaki onaylı butona tıklayarak temizleme komutunu iletebilirsiniz."
            )
            return attach_action_triggers(text, [make_uds_clear_dtc_action()])

        # Diagnostic Session Control request
        is_session_action = any(w in norm_query for w in ["oturum degistir", "extended session", "genisletilmis oturum", "session degistir", "0x10"])
        if is_session_action and not has_dtc_in_query and not has_spn_in_query:
            text = (
                "🔄 **UDS 0x10 DiagnosticSessionControl (Extended Session):**\n"
                "• **Servis:** ECU teşhis oturumu Genişletilmiş Oturum (0x03 Extended) moduna geçirilir.\n"
                "• **Amaç:** Gelişmiş test rutinleri (0x31) ve yazma işlemleri için gereklidir.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak oturumu değiştirebilirsiniz."
            )
            return attach_action_triggers(text, [make_uds_session_action(3)])

        # ECU Reset request
        is_reset_action = any(w in norm_query for w in ["ecu reset", "beyin reset", "hard reset", "beyni sifirla", "0x11"])
        if is_reset_action and not has_dtc_in_query:
            text = (
                "⚡ **UDS 0x11 ECUReset (Hard Reset):**\n"
                "• **Servis:** ECU mikrodenetleyicisi donanımsal olarak baştan başlatılır.\n"
                "• **Güvenlik:** Araç duruyor olmalı ve kullanıcı onayı gereklidir.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak ECU Reset komutunu iletebilirsiniz."
            )
            return attach_action_triggers(text, [make_uds_ecu_reset_action(1)])

        # 0. CAN Frame Forensics (e.g. from right-click context menu or frame questions)
        is_error_frame = "(ERR)" in user_query or "Error Frame" in user_query or "isErrorFrame" in user_query or "hata karesi" in norm_query or "0x00000000" in user_query or "0x0000000" in user_query
        if is_error_frame and not re.search(r"\b([PBUC][0-9A-F]{4})\b", user_query, re.IGNORECASE):
            return (
                "🔴 **CAN Hata Karesi (Error Frame / Bus Error):**\n"
                "• **Durum:** Fiziksel katman hatası (Bit Stuffing veya CRC hatası / Active Error Flag) nedeniyle çerçeve iletimi durduruldu.\n"
                "• **Olası Nedenler:** Hat paraziti, sonlandırma direnci eksikliği veya yanlış baudrate.\n"
                "• **Hızlı Test:** OBD Pin 6 (CAN-H) ve Pin 14 (CAN-L) arası direnci ölçün (Nominal: 60.0 Ω ±3Ω / 120Ω sonlandırma)."
            )

        # 0.2 Specific CAN Packet / Hex Payload Explainer
        payload_bytes = extract_hex_payload_from_query(user_query)

        # 0.5. DBC File & Signal Map Queries
        is_dbc_query = any(w in norm_query for w in ["dbc", "sinyal haritasi", "can veritabani", "sinyal listesi"])
        if is_dbc_query:
            if any(w in norm_query for w in ["nedir", "ne demek", "nasil"]) and len(norm_query.split()) <= 4:
                return (
                    "📦 **DBC (CAN Database) Nedir?**\n"
                    "• CAN veri yolundaki ham bit/bayt mesajlarını fiziksel değerlere (RPM, Hız, Sıcaklık) çeviren sinyal haritasıdır.\n"
                    "• Projemizde 180+ hazır DBC (Binek, Ağır Vasıta J1939, Marin N2K, EV BMS) bulunmaktadır."
                )
            if any(w in norm_query for w in ["liste", "mevcut", "hangi", "neler var", "katalog"]) and len(norm_query.split()) <= 5:
                return (
                    "📦 **Kayıtlı DBC Kütüphanesi Özeti:**\n"
                    "• **Binek Araçlar:** 148 dosya (VW, BMW, Toyota, Ford, Honda, Hyundai vb.)\n"
                    "• **EV & Batarya (BMS):** 17 dosya (Tesla, Nissan Leaf, Kona EV, BYD vb.)\n"
                    "• **Ağır Vasıta (J1939):** 8 dosya (Actros, Scania, Volvo, Cummins, Cat)\n"
                    "• **Marin & Tarım:** 5 dosya (NMEA 2000, ISOBUS)\n"
                    "💡 Belirli bir model aramak için: *'golf dbc'*, *'bmw dbc'*, *'tesla dbc'*"
                )

            matched_dbcs = search_dbc_catalog(user_query)
            if matched_dbcs:
                res_lines = [f"📦 **Eşleşen DBC Dosyaları ({len(matched_dbcs)} adet):**"]
                for d in matched_dbcs[:4]:
                    res_lines.append(f"• **{d['filename']}** ({d['category']})\n  ↳ {d['messages_count']} Mesaj, {d['signals_count']} Sinyal [{d['protocol']}]")
                if len(matched_dbcs) > 4:
                    res_lines.append(f"*(+{len(matched_dbcs) - 4} diğer dosya)*")
                return "\n".join(res_lines)
            else:
                clean_term = re.sub(r"\b(dbc|can|dosyasi|var|mi|araniyor|icin|hakkinda|bilgi|ver)\b", "", norm_query).strip()
                return (
                    f"❌ **DBC Bulunamadı:** '{clean_term or user_query}' ile eşleşen bir DBC dosyası veritabanında mevcut değil.\n"
                    f"💡 Kütüphanemizde 180+ hazır DBC bulunmaktadır. Kendi .dbc dosyanızı `data/dbc/` klasörüne ekleyebilirsiniz."
                )

        can_id_match = re.search(r"(?:CAN ID|can_id|id)\s*[:=]?\s*(0x[0-9A-Fa-f]+)", user_query, re.IGNORECASE)
        if not can_id_match and not any(w in norm_query for w in ["nrc", "negatif", "dtc", "sid", "did", "servis"]):
            can_id_match = re.search(r"\b(0x[0-9A-Fa-f]{3,8})\b", user_query, re.IGNORECASE)
        can_id_hex = can_id_match.group(1).upper() if can_id_match else ""

        # Specific Packet Explainer for CAN frame with payload or diagnostic intent
        if can_id_hex and payload_bytes:
            if not any(k in can_id_hex for k in ["1808E5", "1807E5", "1809E5", "18F020"]):
                expl_text, actions = explain_can_packet(can_id_hex, payload_bytes)
                return attach_action_triggers(expl_text, actions)
        elif not can_id_hex and payload_bytes and len(payload_bytes) >= 2 and any(w in norm_query for w in ["payload", "paket", "veri", "byte", "bayt", "hex"]):
            expl_text, actions = explain_can_packet(0x7E0, payload_bytes)
            return attach_action_triggers(expl_text, actions)

        # EV BMS Specific Frames
        if "1808E5" in can_id_hex or "0x1808E5F4" in user_query:
            cell_min = telemetry.get("bms_cell_voltage_min_v")
            cell_max = telemetry.get("bms_cell_voltage_max_v")
            meas = f"Min={cell_min:.3f}V, Max={cell_max:.3f}V (Delta V={(cell_max-cell_min)*1000:.0f}mV) (ölçüm)" if (cell_min is not None and cell_max is not None) else "Canlı ölçüm yok"
            return (
                f"⚡ **EV BMS Hücre Voltajları (0x1808E5F4 - PGN 61447):**\n"
                f"• **Protokol:** ISO 11898-2 (EV Yüksek Voltaj BMS)\n"
                f"• **Ölçüm Durumu:** {meas}\n"
                f"• **Hedef:** Hücre voltaj farkı <30 mV olmalıdır."
            )

        if "1807E5" in can_id_hex or "0x1807E5F4" in user_query:
            soc = telemetry.get("bms_soc_percent")
            soh = telemetry.get("bms_soh_percent")
            meas = f"SOC=%{soc:.1f}, SOH=%{soh:.1f}" if soc is not None and soh is not None else "Canlı ölçüm bekleniyor"
            return (
                f"⚡ **EV BMS Şarj & Sağlık (0x1807E5F4 - PGN 61446):**\n"
                f"• **Protokol:** ISO 11898-2 (BMS ECU 0xF4)\n"
                f"• **Durum:** {meas}"
            )

        if "1809E5" in can_id_hex or "0x1809E5F4" in user_query:
            bat_temp = telemetry.get("bms_pack_temp_c")
            temp_str = f"{bat_temp:.1f}°C" if bat_temp is not None else "Canlı ölçüm bekleniyor"
            return (
                f"⚡ **EV BMS Termal Yönetimi (0x1809E5F4 - PGN 61448):**\n"
                f"• **Paket Sıcaklığı:** {temp_str}\n"
                f"• **Hedef:** Nominal çalışma aralığı 20°C - 35°C."
            )

        if "18F020" in can_id_hex or "0x18F020F4" in user_query:
            isolation = telemetry.get("bms_hv_isolation_mohm")
            iso_str = f"{isolation:.1f} MΩ (Nominal >500 Ω/V)" if isolation is not None else "Canlı ölçüm bekleniyor"
            return (
                f"⚡ **EV BMS Yüksek Voltaj İzolasyonu (0x18F020F4):**\n"
                f"• **İzolasyon Direnci:** {iso_str}\n"
                f"• **Kontrol:** Kontaktör durumları ve şasi kaçak izleme."
            )

        # 1. Direct DTC code match in prompt (P0xxx, C1xxx, U0xxx, B0xxx)
        dtc_match = re.search(r"\b([PBUC][0-9A-F]{4})\b", user_query, re.IGNORECASE)
        direct_dtc = dtc_match.group(1).upper() if dtc_match else None

        # 2. Check for SPN numbers (e.g. SPN 100, SPN 102, SPN 3251, SPN 641)
        # T74: the shared extractor accepts the compact/underscore/hyphen forms
        # too; `spn_num` is the canonical int so "SPN 0100" and "SPN100" resolve
        # the same `SPN_<n>` DB key.
        spn_match = query_spn_number(norm_query)
        if spn_match is not None:
            spn_num = str(spn_match)
            spn_key = f"SPN{spn_num}"
            if spn_key in EXPERT_KNOWLEDGE_BASE:
                direct_dtc = spn_key
            else:
                j1939_db = get_j1939_spn_database()
                spn_entry = j1939_db.get("spns", {}).get(f"SPN_{spn_num}")
                if spn_entry:
                    return cls._format_j1939_technician_report(spn_entry, norm_query, telemetry, vehicle_make)
                else:
                    return (
                        f"⚠️ **[SPN {spn_num}] Kaydı Bulunamadı:**\n"
                        f"Bu SPN parametresi yerel J1939 veritabanında kayıtlı değil.\n"
                        f"• Üreticiye özel (Proprietary) bir PGN/SPN olabilir. SAE J1939-71 kataloğundan teyit edin."
                    )

        # 3. Check for UDS NRC codes (e.g. NRC 0x22, NRC 0x33, NRC 0x78)
        nrc_match = re.search(r"\b(?:nrc|negatif yanit)\s*(?:0x)?([0-9a-f]{2})\b", norm_query)
        if nrc_match:
            nrc_hex = f"0x{nrc_match.group(1).upper()}"
            if nrc_hex in UDS_NRC_CATALOG:
                nrc_info = UDS_NRC_CATALOG[nrc_hex]
                return (
                    f"🛑 **UDS Negatif Yanıt ({nrc_hex} - {nrc_info['name']}):**\n"
                    f"• **Neden:** {nrc_info['cause']}\n"
                    f"• **Çözüm:** {nrc_info['action']}\n"
                    f"• **Ön Koşul:** `0x10 0x03` Extended Session, Kontak AÇIK/Motor KAPALI (Ignition ON, Engine OFF), Akü >12.5V."
                )
            else:
                return f"⚠️ **[NRC {nrc_hex}] Tanımsız:** Standart ISO 14229 kataloğunda bu negatif yanıt kodu tanımlı değil."

        # 3.5 Check for NHTSA Safety Recalls & TSB Queries
        is_recall_query = any(w in norm_query for w in ["recall", "geri cagirma", "tsb", "teknik bulten", "kampanya", "nhtsa"])
        if is_recall_query:
            found_year = None
            year_match = re.search(r"\b(201[8-9]|202[0-5])\b", user_query)
            if year_match:
                found_year = int(year_match.group(1))

            found_make = None
            known_makes = [
                ("ford", "ford"), ("lincoln", "lincoln"), ("tesla", "tesla"),
                ("chevrolet", "chevrolet"), ("chevy", "chevrolet"), ("gm", "chevrolet"),
                ("gmc", "gmc"), ("cadillac", "cadillac"), ("toyota", "toyota"),
                ("lexus", "lexus"), ("volkswagen", "volkswagen"), ("vw", "volkswagen"),
                ("audi", "audi"), ("bmw", "bmw"), ("hyundai", "hyundai"),
                ("kia", "kia"), ("ram", "ram"), ("jeep", "jeep"),
                ("mercedes", "mercedes-benz"), ("volvo", "volvo"),
            ]
            for kw, mname in known_makes:
                if kw in norm_query:
                    found_make = mname
                    break

            query_kw = None
            for kw in [
                "f-150", "f150", "mach-e", "mache", "explorer", "escape", "bronco",
                "model 3", "model y", "model s", "model x",
                "bolt", "silverado", "corvette", "lyriq", "sierra",
                "rav4", "prius", "camry", "corolla", "highlander", "tundra",
                "id.4", "id4", "tiguan", "atlas", "e-tron", "etron", "q5", "a4",
                "330i", "i4", "ix", "x5",
                "ioniq 5", "ioniq", "ev6", "telluride", "wrangler",
                "trailer brake", "gateway", "bms", "battery", "batarya",
                "contactor", "direksiyon", "steering", "fren", "brake",
                "software", "yazilim", "ota", "park"
            ]:
                if kw in norm_query:
                    query_kw = kw.replace("f150", "f-150").replace("mache", "mach-e").replace("id4", "id.4").replace("etron", "e-tron")
                    break

            recalls = search_nhtsa_recalls(
                make=found_make,
                year=found_year,
                query=query_kw or (None if found_make else norm_query.replace("recall", "").replace("geri cagirma", "").strip()),
                limit=3,
            )

            if recalls:
                reports = [format_nhtsa_recall_report(r) for r in recalls]
                header = (
                    f"📢 **NHTSA Resmi Güvenlik Geri Çağırma (Recall) & TSB Raporu:**\n"
                    f"🔎 **Kriter:** {found_make.upper() if found_make else 'Tümü'} | Yıl: {found_year or 'Tümü'} | Eşleşen Kampanya: {len(recalls)} adet\n\n"
                )
                return header + ("\n\n" + "─" * 40 + "\n\n").join(reports)
            else:
                return (
                    f"ℹ️ **NHTSA Geri Çağırma Arama Sonucu:**\n"
                    f"Belirtilen kriterlere uygun (`{user_query}`) geri çağırma kaydı bulunamadı.\n"
                    "Lütfen araç modeli (Örn: *'Ford F-150'*, *'Tesla Model 3'*) belirterek deneyin."
                )

        # 3.8 CAN ID Specific lookup if not matched above
        if can_id_hex and not direct_dtc:
            val = int(can_id_hex, 16)
            if val == 0x7DF:
                return "📡 **CAN ID 0x7DF:** Standart OBD-II Fonksiyonel Yayın İsteği (Tüm bağlı ECU'lara eşzamanlı genel sorgu)."
            elif 0x7E0 <= val <= 0x7E7:
                ecu_name = "Motor (ECM/PCM)" if val == 0x7E0 else ("Şanzıman (TCM)" if val == 0x7E1 else f"ECU_{val - 0x7E0}")
                return f"📡 **CAN ID {can_id_hex}:** ISO 15765-4 Standart OBD-II / UDS Fiziksel İstek Hattı ({ecu_name})."
            elif 0x7E8 <= val <= 0x7EF:
                ecu_name = "Motor (ECM/PCM)" if val == 0x7E8 else ("Şanzıman (TCM)" if val == 0x7E9 else f"ECU_{val - 0x7E8}")
                return f"📡 **CAN ID {can_id_hex}:** ISO 15765-4 Standart OBD-II / UDS Fiziksel Yanıt Hattı ({ecu_name})."
            else:
                return (
                    f"⚠️ **CAN ID Tanımsız ({can_id_hex}):**\n"
                    f"Bu mesaj kimliği için yerel veritabanında veya protokol motorunda kayıtlı bir sinyal tanımı bulunamadı.\n"
                    f"• Sniffer tablosundan canlı veri uzunluğunu (DLC) ve bayt değişimlerini inceleyebilirsiniz."
                )

        # 4. If direct DTC is identified, render structured 4-stage technician report
        target_code = direct_dtc
        is_general_fault_query = any(w in norm_query for w in ["ariza", "dtc", "hata kodu", "fault", "nedir", "analiz et", "neden"])
        if not target_code and not can_id_hex and is_general_fault_query and active_dtcs:
            # T42 P2-1: eskiden yalniz `active_dtcs[0]` isleniyordu. Artik TUM
            # aktif kodlar degerlendirilir: tek koda dusulebiliyorsa eski yol
            # (tek-DTC raporu, test kilidi korunur); birden fazla kod varsa
            # BIRLESIK analiz + ortak alt-sistem/PGN kumelemesi basilir.
            _clusters = analyze_active_dtc_clusters(active_dtcs)
            _codes = _clusters["codes"]
            _reserve_note = get_reserved_code_notice(_codes[0]) if _codes else None

            # tek-kod yolu: aktif kodlardan TAM OLARAK biri bildirilmisse
            if len(_codes) == 1:
                first_dtc = active_dtcs[0]
                if isinstance(first_dtc, dict):
                    spn = first_dtc.get("spn")
                    code_str = str(first_dtc.get("code", ""))
                    if spn and f"SPN{spn}" in EXPERT_KNOWLEDGE_BASE:
                        target_code = f"SPN{spn}"
                    elif code_str in EXPERT_KNOWLEDGE_BASE:
                        target_code = code_str
                if not target_code and _codes[0] in EXPERT_KNOWLEDGE_BASE:
                    target_code = _codes[0]
                # P2-6: rezerve kod uyarisi tek-kod raporuna eklenir (fail-safe).
                if target_code and target_code in EXPERT_KNOWLEDGE_BASE and _reserve_note:
                    return cls._format_4stage_technician_report(target_code, telemetry, vehicle_make, vehicle_year) + "\n\n" + _reserve_note

            # COKLU-DTC Birlesik Analiz (P2-1): birden fazla kod -> birlestir.
            if len(_codes) >= 2:
                known = [c for c in _codes if c in EXPERT_KNOWLEDGE_BASE]
                if known:
                    return cls._format_multi_dtc_combined_report(
                        known, telemetry, _clusters, vehicle_make, vehicle_year
                    )

        if target_code:
            if target_code in EXPERT_KNOWLEDGE_BASE:
                _single_report = cls._format_4stage_technician_report(target_code, telemetry, vehicle_make, vehicle_year)
                _res_note = get_reserved_code_notice(target_code)
                return _single_report + ("\n\n" + _res_note if _res_note else "")
            else:
                cat_char = target_code[0].upper()
                is_oem = len(target_code) > 1 and target_code[1] in ("1", "2")
                cat_desc = {
                    "P": "Güç Aktarımı (Powertrain)",
                    "C": "Şasi / ABS / ESP (Chassis)",
                    "B": "Gövde / Konfor (Body)",
                    "U": "Ağ / CAN İletişimi (Network)",
                }.get(cat_char, "Bilinmeyen")
                oem_note = "Üreticiye Özel (OEM-Specific)" if is_oem else "Standart SAE"
                return (
                    f"⚠️ **[{target_code}] Arıza Kodu Bulunamadı:**\n"
                    f"Bu kod yerel teşhis kütüphanesinde kayıtlı değil.\n"
                    f"• **Kategori:** {cat_desc} ({oem_note})\n"
                    f"• **Tavsiye:** Aracın yetkili servis kılavuzunu inceleyin veya UDS `0x19 0x02` servisi ile çevre koşullarını (Freeze Frame) okuyun."
                )

        # Kanıt A (P1, fabrication from generic text): a query whose content
        # words carry no diagnostic specificity must ABSTAIN here instead of
        # falling through to the intent router, where loose keyword matching
        # turned "problem" into a concrete P0500 report and "hava durumu nedir"
        # into a CRITICAL_STOP SPN1087 brake report. Reaching this point means
        # no hex payload, CAN ID, DTC/SPN, recall/TSB or bus-metric path
        # matched — so the only honest answer for non-specific text is the
        # "no data" fallback. Specific queries (tekleme, turbo, hararet, ...)
        # keep all their content words and pass straight through.
        if not target_code and not can_id_hex and not active_dtcs:
            _content_words = [
                w for w in norm_query.split()
                if w and w not in _SYMPTOM_SEARCH_STOPWORDS
            ]
            if not _content_words or all(
                w in _GENERIC_NON_DIAGNOSTIC_TERMS for w in _content_words
            ):
                _abstain = (
                    f"ℹ️ **Bilgi Bulunamadı:** '{user_query[:60]}' hakkında yerel teşhis "
                    f"veritabanında doğrudan bir eşleşme bulunamadı.\n\n"
                    f"⚠️ **Ayrımsız ifade:** Sorgu teşhise özgü bir terim içermiyor; "
                    f"uydurma arıza kodu üretilmedi. Belirti, arıza kodu (P0300), "
                    f"SPN/FMI veya CAN ID belirtin.\n\n"
                    f"💡 **Desteklenen Sorgu Formatları:**\n"
                    f"• **Arıza Kodları:** *P0300*, *U0100*, *C0035*, *P1260*\n"
                    f"• **Ağır Vasıta SPN:** *SPN 100 FMI 1*, *SPN 641*\n"
                    f"• **DBC Dosyaları:** *golf dbc*, *tesla dbc*, *j1939 dbc*\n"
                    f"• **Geri Çağırma / TSB:** *Ford F-150 recall*, *Tesla Model 3 kampanya*\n"
                    f"• **Fiziksel Katman:** *120 ohm testi*, *CAN hata karesi*"
                )
                _extracted = extract_action_triggers(user_query)
                if _extracted:
                    return attach_action_triggers(_abstain, _extracted)
                return _abstain

        # 5. Semantic Intent Matching using Causal Graph
        if intents.get("EV_HV_BATTERY", 0.0) >= 0.5 or any(w in norm_query for w in ["izolasyon", "hvil", "batarya", "megger", "precharge", "turtle"]):
            if "izolasyon" in norm_query or "megger" in norm_query or "kacak" in norm_query:
                return cls._format_4stage_technician_report("P0AA6", telemetry, vehicle_make, vehicle_year)
            if "hvil" in norm_query or "interlock" in norm_query or "salter" in norm_query:
                return cls._format_4stage_technician_report("P0A0B", telemetry, vehicle_make, vehicle_year)
            # A3-5: guard the hard-coded KB key — a missing entry must not
            # KeyError the whole report; fall through to the general path.
            if ("precharge" in norm_query or "kontaktor" in norm_query) and "P0AA1" in EXPERT_KNOWLEDGE_BASE:
                return cls._format_4stage_technician_report("P0AA1", telemetry, vehicle_make, vehicle_year)
            return cls._format_4stage_technician_report("P0A80", telemetry, vehicle_make, vehicle_year)

        if intents.get("HEAVY_DUTY_J1939", 0.0) >= 0.5 or any(w in norm_query for w in ["adblue", "def", "dpf", "scr", "yag basinci", "fmi", "derate"]):
            if "yag" in norm_query:
                return cls._format_4stage_technician_report("SPN100", telemetry, vehicle_make, vehicle_year)
            if "dpf" in norm_query or "rejenerasyon" in norm_query:
                return cls._format_4stage_technician_report("SPN3251", telemetry, vehicle_make, vehicle_year)
            if "adblue" in norm_query or "def" in norm_query or "kalite" in norm_query:
                return cls._format_4stage_technician_report("SPN3364", telemetry, vehicle_make, vehicle_year)
            if "enjektor" in norm_query:
                return cls._format_4stage_technician_report("SPN651", telemetry, vehicle_make, vehicle_year)
            # Kanıt A: "hava" must match the pneumatic-brake domain as a WHOLE
            # word — bare substring matching sent "hava durumu nedir" (weather)
            # to a CRITICAL_STOP SPN1087 brake report. Whole-word matching alone
            # is not enough ("hava durumu" really does contain the word "hava"),
            # so require an actual pneumatic/brake qualifier alongside it.
            # A bare "hava" (weather) is handled by the abstention gate above.
            if _has_standalone_word(norm_query, "fren") or any(
                _has_standalone_word(norm_query, w)
                for w in ("hava basinci", "hava kacagi", "hava kaçağı", "hava tanki", "hava tüpü", "kuru hava")
            ):
                return cls._format_4stage_technician_report("SPN1087", telemetry, vehicle_make, vehicle_year)
            return cls._format_4stage_technician_report("SPN4364", telemetry, vehicle_make, vehicle_year)

        if intents.get("MARINE_NMEA2000", 0.0) >= 0.5 or any(w in norm_query for w in ["impeller", "cark", "marin", "deniz suyu", "esnjor", "mixing elbow", "pervane", "slip"]):
            if "impeller" in norm_query or "cark" in norm_query or "deniz suyu" in norm_query:
                return cls._format_4stage_technician_report("N2K_IMPELLER", telemetry, vehicle_make, vehicle_year)
            if "egzoz" in norm_query or "dirsek" in norm_query or "elbow" in norm_query or "waterlock" in norm_query:
                return cls._format_4stage_technician_report("N2K_EXHAUST_ELBOW", telemetry, vehicle_make, vehicle_year)
            if "esnjor" in norm_query or "kirec" in norm_query or "yuksek yuk" in norm_query:
                return cls._format_4stage_technician_report("N2K_HEAT_EXCHANGER", telemetry, vehicle_make, vehicle_year)
            return cls._format_4stage_technician_report("N2K_PROP_SLIP", telemetry, vehicle_make, vehicle_year)

        if intents.get("CAN_PHYSICAL_LAYER", 0.0) >= 0.5 or any(w in norm_query for w in ["120 ohm", "60 ohm", "sonlandirma", "direnc", "can h", "can l", "kisa devre", "pinout"]):
            if "voltaj" in norm_query or "bias" in norm_query or "offset" in norm_query:
                return cls._format_4stage_technician_report("CAN_VOLT_FAULT", telemetry, vehicle_make, vehicle_year)
            return cls._format_4stage_technician_report("CAN_TERM_60", telemetry, vehicle_make, vehicle_year)

        if intents.get("MISFIRE", 0.0) >= 0.5 or any(w in norm_query for w in ["tekliyor", "tekleme", "sarsinti", "atesleme", "buji"]):
            return cls._format_4stage_technician_report("P0300", telemetry, vehicle_make, vehicle_year)

        if intents.get("TURBO_BOOST", 0.0) >= 0.5 or any(w in norm_query for w in ["turbo", "overboost", "underboost", "kara duman", "bayiliyor", "cekis"]):
            return cls._format_4stage_technician_report("P0234", telemetry, vehicle_make, vehicle_year)

        if intents.get("OVERHEAT_COOLING", 0.0) >= 0.5 or any(w in norm_query for w in ["hararet", "termostat", "radyator", "fan", "su kaynatiyor", "ust kapak contasi"]):
            return cls._format_4stage_technician_report("SPN110", telemetry, vehicle_make, vehicle_year)

        if "u0100" in norm_query or "iletisim koptu" in norm_query or "beyin cevap vermiyor" in norm_query:
            return cls._format_4stage_technician_report("U0100", telemetry, vehicle_make, vehicle_year)

        # 5.9 T41 P1-4: semptom -> kod aramasi (fallback oncesi). DTC DB'sindeki
        # 14.166 `symptoms` kaydi motorda hic okunmuyordu. Deterministik skor:
        # en fazla eslesen terim; esitlikte katalog sirasi. Yalnizca gercek
        # DB eslesmesi varsa rapor uretilir — uydurma kod onerilmez.
        symptom_hits = search_dtc_by_symptom(norm_query, limit=1)
        if symptom_hits:
            _best = symptom_hits[0]["code"]
            if _best in EXPERT_KNOWLEDGE_BASE:
                _matched = symptom_hits[0].get("matched") or []
                _hit_note = (
                    f"\n\n🔎 **Semptom Eşleşmesi:** \"{', '.join(str(m) for m in _matched[:4])}\" "
                    f"ifadeleri [{_best}] kaydının semptom/kategori metniyle eşleşti."
                )
                _report = cls._format_4stage_technician_report(_best, telemetry, vehicle_make, vehicle_year)
                return _report + _hit_note

        # 6. Fallback General Diagnosis (Honest about lack of data, concise and simplified)
        fallback_text = (
            f"ℹ️ **Bilgi Bulunamadı:** '{user_query[:60]}' hakkında yerel teşhis veritabanında doğrudan bir eşleşme bulunamadı.\n\n"
            f"💡 **Desteklenen Sorgu Formatları:**\n"
            f"• **Arıza Kodları:** *P0300*, *U0100*, *C0035*, *P1260*\n"
            f"• **Ağır Vasıta SPN:** *SPN 100 FMI 1*, *SPN 641*\n"
            f"• **DBC Dosyaları:** *golf dbc*, *tesla dbc*, *j1939 dbc*\n"
            f"• **Geri Çağırma / TSB:** *Ford F-150 recall*, *Tesla Model 3 kampanya*\n"
            f"• **Fiziksel Katman:** *120 ohm testi*, *CAN hata karesi*"
        )
        extracted = extract_action_triggers(user_query)
        if extracted:
            return attach_action_triggers(fallback_text, extracted)
        return fallback_text

    @classmethod
    def _format_multi_dtc_combined_report(
        cls,
        codes: list[str],
        telemetry: dict[str, float],
        clusters: dict[str, Any],
        vehicle_make: str | None = None,
        vehicle_year: int | None = None,
    ) -> str:
        """T42 P2-1: coklu-DTC BIRLESIK analiz raporu.

        Aktif tum kodlar birlikte degerlendirilir:
          • ortak alt-sistem gruplari (DB `subsystem`)
          • ortak J1939 PGN gruplari (paylasilan veri yolu) — DB-doldurulmus
            `associated_pgn` varsa
          • rezerve (ureticiye ozel) kodlar icin yanlis-teshis uyarisi (P2-6)
          • her kod icin kisa 1-satir ozet (ad + alt sistem + onem)
          • OEM varyantlari (marka verildiyse filtreli) — T69-G
          • NHTSA sahip-sikayetleri (marka/yil verildiyse filtreli) — T69-G

        ``vehicle_make`` / ``vehicle_year`` (T69-G): the single-code and J1939
        generators have accepted these since T42 P2-2/P2-3, but the combined
        report's signature omitted them while its CALL SITE already computed
        both. Measured consequence: for a vehicle whose fault set is multi-code
        (the common case for a shared-root-cause failure) the operator lost the
        OEM variant filter and the complaint fusion entirely — the two features
        T42 added were unreachable exactly when several codes fire at once.
        Both default to ``None``, which preserves the previous behaviour
        byte-for-byte (fail-safe).

        Determinizm: ``codes`` cagirandan SIRALI gelir; her grup icin uye listesi
        sirali basilir. Ayni girdi -> ayni cikti. Uydurma YOK — alan DB'de
        yoksa ilgili satir/blok uretilmez (fail-safe).
        """
        # T67-A (F-02 parity): same fabricated-coolant defect as the 4-stage
        # report — each measured value prints only when the caller supplied it.
        rpm = telemetry.get("EngineSpeed", 0.0)
        boost = telemetry.get("BoostPressure", 0.0)
        _t_parts: list[str] = []
        if rpm > 0:
            _t_parts.append(f"{rpm:.0f} RPM")
        if boost > 0:
            _t_parts.append(f"{boost:.2f} Bar")
        _t_coolant = telemetry.get("CoolantTemp")
        if isinstance(_t_coolant, (int, float)) and _t_coolant > 0:
            _t_parts.append(f"{float(_t_coolant):.1f}°C")
        telemetry_str = (" | " + ", ".join(_t_parts)) if _t_parts else ""

        lines: list[str] = [
            f"🧩 **ÇOKLU-DTC BİRLEŞİK ANALİZ ({len(codes)} aktif kod):**{telemetry_str}",
        ]

        # T70-B: a multi-code session supplies several measurements at once, so
        # the physical-envelope sweep matters most here. Empty when all values
        # are inside their recorded envelope (fail-safe).
        _multi_plaus = telemetry_plausibility_block(telemetry)
        if _multi_plaus:
            lines.append(_multi_plaus)

        # (1) ortak alt-sistem gruplari
        sub_groups = clusters.get("subsystem_groups") or []
        if sub_groups:
            lines.append("\n🔗 **Ortak Alt-Sistem Kümeleri (aynı alanda çoklu arıza → ortak kök neden şüphesi):**")
            for sub, members in sub_groups[:5]:
                lines.append(f"  • **{sub}** — {len(members)} kod: {', '.join(members)}")
        else:
            lines.append(
                "\n🔗 **Ortak Alt-Sistem:** Aktif kodlar farklı alt sistemlere dağılmış; "
                "tek bir ortak alan tespit edilmedi."
            )

        # (2) ortak J1939 PGN (paylasilan veri yolu) — yalniz DB'de dolu ise
        pgn_groups = clusters.get("pgn_groups") or []
        if pgn_groups:
            lines.append("\n📡 **Ortak J1939 PGN (paylaşılan veri yolu/çerçeve):**")
            for pgn, members in pgn_groups[:4]:
                lines.append(f"  • **PGN {pgn}** — {len(members)} kod: {', '.join(members)}")

        # (3) her kod icin deterministik 1-satir ozet
        lines.append("\n🧾 **Aktif Kod Özeti:**")
        for c in codes:
            info = EXPERT_KNOWLEDGE_BASE.get(c)
            if not isinstance(info, dict):
                continue
            title = str(info.get("title", c))
            subsys = str(info.get("subsystem", "Genel Teşhis"))
            sev = str(info.get("severity", "MEDIUM"))
            tag = " ⛔*(rezerve/üreticiye özel)*" if info.get("is_reserved") else ""
            lines.append(f"  • **[{c}]** {title[:90]} — {subsys} *(Öncelik: {sev})*{tag}")

        # (4) kok-neden onerisi: en kalabalik ortak alt-sistem
        if sub_groups:
            top_sub, top_members = sub_groups[0]
            lines.append(
                f"\n🎯 **Kök-Neden Önerisi:** En yoğun ortak alan **{top_sub}** "
                f"({len(top_members)} kod). Önce bu alt sistemin besleme/şase ve "
                f"ortak sensör hatlarını kontrol edin — çoklu kod genelde tek bir "
                f"ortak besleme/kablolama arızasından türer."
            )

        # (4b) T69-G: OEM varyantlari — her kod icin, marka verildiyse filtreli.
        # Only blocks the DB actually fills are emitted (no fabrication).
        oem_lines: list[str] = []
        _oem_hidden_total = 0
        for c in codes:
            info = EXPERT_KNOWLEDGE_BASE.get(c)
            if not isinstance(info, dict):
                continue
            ov = info.get("oem_variants")
            if not isinstance(ov, list) or not ov:
                continue
            kept, hidden = filter_oem_variants(ov, vehicle_make)
            _oem_hidden_total += hidden
            rendered: list[str] = []
            for item in kept:
                if isinstance(item, dict):
                    mk = item.get("manufacturer") or item.get("make") or "OEM"
                    ds = item.get("description") or item.get("meaning") or ""
                    if ds:
                        rendered.append(f"    • **{mk}:** {str(ds)[:160]}")
                elif isinstance(item, str) and item.strip():
                    rendered.append(f"    • {item[:160]}")
            if rendered:
                oem_lines.append(f"  • **[{c}]**")
                oem_lines.extend(rendered)
        if oem_lines:
            _hdr = "🏭 **OEM Varyantları (aynı kod, marka bazlı anlam):**"
            if vehicle_make:
                _hdr = f"🏭 **OEM Varyantları — {vehicle_make.upper()} (marka filtreli):**"
            lines.append("\n" + _hdr)
            lines.extend(oem_lines)
            if _oem_hidden_total:
                lines.append(
                    f"  *(+{_oem_hidden_total} farklı marka varyantı gizlendi — "
                    "marka filtresi aktif)*"
                )

        # (4c) T69-G: NHTSA sahip-sikayetleri — marka/yil verildiyse filtreli.
        # Fail-safe: the complaints corpus must never break the report.
        try:
            _cmp = search_nhtsa_complaints(
                make=vehicle_make, year=vehicle_year, limit=3
            )
            _cmp_hits = _cmp.get("complaints") or []
            if _cmp_hits:
                _cmp_lines: list[str] = []
                for _e in _cmp_hits[:3]:
                    _comp = str(_e.get("components", "")).split(",")[0].strip()
                    _sum = str(_e.get("summary", "")).strip()
                    if _comp or _sum:
                        _cmp_lines.append(
                            f"    • [{_comp}] {_sum[:180]}" if _comp else f"    • {_sum[:180]}"
                        )
                if _cmp_lines:
                    _scope = (
                        f"{len(_cmp.get('matched_vehicles', []))} araç / "
                        f"{len(_cmp_hits)} şikayet"
                    )
                    if vehicle_make:
                        _chdr = (
                            "🗣️ **Sahip Şikayetleri (NHTSA complaints — "
                            f"{vehicle_make.upper()} marka filtreli):**"
                        )
                    else:
                        _chdr = "🗣️ **Sahip Şikayetleri (NHTSA complaints):**"
                    lines.append(f"\n{_chdr} {_scope}")
                    lines.extend(_cmp_lines)
        except Exception as exc:  # noqa: BLE001 — complaints asla raporu düsürmez
            logger.debug("NHTSA complaints fusion skipped (multi-DTC): %s", exc)

        # (4d) T70-A: the validated procedure corpus, per code. The corpus had
        # no consumer in ANY report before T70-A; on a multi-code session the
        # per-code branch tree (pass_next/fail_next) is exactly what tells the
        # technician which code to chase first. Codes without a procedure
        # contribute nothing — no empty header is emitted (fail-safe).
        _corpus_lines: list[str] = []
        for _c in codes:
            _cblock = procedure_corpus_block(_c, include_header=False)
            if not _cblock:
                continue
            _corpus_lines.append(f"  • **[{_c}]**")
            _corpus_lines.extend("  " + _ln for _ln in _cblock.splitlines())
        if _corpus_lines:
            lines.append(
                f"\n📐 **Doğrulanmış Prosedür Kütüphanesi "
                f"(korpus `dtc_procedures`, {sum(1 for x in _corpus_lines if x.startswith('  • **['))} kod):**"
            )
            lines.extend(_corpus_lines)

        # (5) P2-6: rezerve kod uyarisi (yanlis teshisi engeller)
        reserved_codes = clusters.get("reserved_codes") or []
        if reserved_codes:
            lines.append(
                f"\n⛔ **REZERVE KOD UYARISI:** Aktif kodlardan {len(reserved_codes)} tanesi "
                f"ISO/SAE standart tablosunda **üreticiye bırakılmıştır** (rezerve): "
                f"{', '.join(reserved_codes[:8])}. Bu kodlar için genel geçer anlam YOKTUR; "
                f"OEM servis kılavuzundan doğrulanmadan parça değişimi YAPMAYIN."
            )

        lines.append(
            "\n💡 **Sonraki Adım:** Kodları canlı DM1 yayını ile karşılaştırıp freeze-frame "
            "(UDS `0x19 0x02`) çevre koşullarını okuyun; ortak alan grubundaki kodlar için "
            "tek bir kök neden onarımı yeterli olabilir."
        )

        report_text = "\n".join(lines)
        # P0-3 parity (session_report.py:217-224): a combined report is a record,
        # not a command surface — the unconditional UDS 0x14 clear button is gone.
        actions = extract_action_triggers("", "")
        return attach_action_triggers(report_text, actions)

    #: T68: which thresholded signal a diagnosis is ABOUT.
    #:
    #: Before T68 this mapping was three hardcoded `if` lines covering exactly
    #: three codes (P0234/SPN102, SPN110/P0115, SPN100). `telemetry_thresholds.
    #: json` carries SEVEN signals (EngineCoolantTemp, TurboBoost,
    #: EngineOilPressure, EngineSpeed, VehicleSpeed, EngineLoad, EngineTorque),
    #: so four of them could never be cross-checked against a measurement no
    #: matter what the operator asked.
    #:
    #: The table below is derived from the J1939 SPN definitions in the live DB,
    #: not invented: each entry names the SPN whose measured quantity the
    #: threshold describes. An OBD-II code is attached where the SAE J1979 PID
    #: measures the same physical quantity (documented in signal_aliases.json).
    #: A code absent from this table simply produces no consistency line — the
    #: honest outcome, since no threshold applies to it.
    _THRESHOLD_SIGNAL_FOR_CODE: dict[str, tuple[str, str, str, str]] = {
        # telemetry key, threshold-db key, Turkish label, unit
        "SPN102": ("BoostPressure", "TurboBoost", "Turbo basıncı", "bar"),
        "P0234": ("BoostPressure", "TurboBoost", "Turbo basıncı", "bar"),
        "P0299": ("BoostPressure", "TurboBoost", "Turbo basıncı", "bar"),
        "SPN110": ("CoolantTemp", "EngineCoolantTemp", "Soğutma suyu sıcaklığı", "°C"),
        "P0115": ("CoolantTemp", "EngineCoolantTemp", "Soğutma suyu sıcaklığı", "°C"),
        "P0116": ("CoolantTemp", "EngineCoolantTemp", "Soğutma suyu sıcaklığı", "°C"),
        "P0117": ("CoolantTemp", "EngineCoolantTemp", "Soğutma suyu sıcaklığı", "°C"),
        "P0118": ("CoolantTemp", "EngineCoolantTemp", "Soğutma suyu sıcaklığı", "°C"),
        "P0119": ("CoolantTemp", "EngineCoolantTemp", "Soğutma suyu sıcaklığı", "°C"),
        "SPN100": ("OilPressure", "EngineOilPressure", "Yağ basıncı", "bar"),
        "P0524": ("OilPressure", "EngineOilPressure", "Yağ basıncı", "bar"),
        "SPN190": ("EngineSpeed", "EngineSpeed", "Motor devri", "rpm"),
        "P0335": ("EngineSpeed", "EngineSpeed", "Motor devri", "rpm"),
        "P0336": ("EngineSpeed", "EngineSpeed", "Motor devri", "rpm"),
        "SPN84": ("VehicleSpeed", "VehicleSpeed", "Araç hızı", "km/h"),
        "P0500": ("VehicleSpeed", "VehicleSpeed", "Araç hızı", "km/h"),
        "P0501": ("VehicleSpeed", "VehicleSpeed", "Araç hızı", "km/h"),
        "SPN92": ("EngineLoad", "EngineLoad", "Motor yükü", "%"),
        "P0106": ("EngineLoad", "EngineLoad", "Motor yükü", "%"),
        "SPN513": ("EngineTorque", "EngineTorque", "Motor torku", "%"),
    }

    @classmethod
    def _measured_value_consistency(
        cls,
        code: str,
        telemetry: dict[str, float],
    ) -> str:
        """Cross-check measured telemetry against the threshold database.

        Returns a one-line note, or "" when the diagnosis has no thresholded
        signal or no measurement was captured. It NEVER suppresses guidance: it
        states whether the captured value is consistent with, above, or below
        the KB-derived band, so an operator who asks "is the turbo pressure
        normal?" at a nominal 2.6 bar is told the value is nominal instead of
        being shown an unexplained overboost work-order (P0-2, finding 4).

        T68: coverage widened from 3 codes to every code whose measured
        quantity has a threshold band (see `_THRESHOLD_SIGNAL_FOR_CODE`).
        Deterministic and offline: reads only telemetry_thresholds.json (itself
        derived from the KB measurement text).
        """
        checks: list[tuple[str, str, str, str]] = []
        # Accept both the SPN form ("SPN102") and the bare number the DM1 path
        # produces ("102"), so a session event and a query reach the same band.
        _norm = str(code or "").strip().upper().replace(" ", "")
        _entry = cls._THRESHOLD_SIGNAL_FOR_CODE.get(_norm)
        if _entry is None and _norm.isdigit():
            _entry = cls._THRESHOLD_SIGNAL_FOR_CODE.get(f"SPN{_norm}")
        if _entry is None and _norm.startswith("SPN") and _norm[3:].isdigit():
            _entry = cls._THRESHOLD_SIGNAL_FOR_CODE.get(_norm)
        if _entry is not None:
            checks.append(_entry)
        if not checks:
            return ""
        try:
            from src.engine.ai.anomaly_detector import load_thresholds

            thresholds = load_thresholds()
        except Exception:  # noqa: BLE001
            return ""

        lines: list[str] = []
        for signal_key, db_key, label, unit in checks:
            value = telemetry.get(signal_key)
            if not isinstance(value, (int, float)):
                continue  # not measured -> say nothing (F-02)
            bands = thresholds.get(db_key)
            if not isinstance(bands, dict):
                continue
            nominal_min = bands.get("nominal_min")
            nominal_max = bands.get("nominal_max")
            critical_max = bands.get("critical_max")
            if isinstance(critical_max, (int, float)) and value > critical_max:
                lines.append(
                    f"  • {label}: {value:.2f} {unit} — **limit üstü** (KB limiti "
                    f"{critical_max} {unit}); ölçüm tanıyı DESTEKLİYOR."
                )
            elif (
                isinstance(nominal_min, (int, float))
                and isinstance(nominal_max, (int, float))
                and nominal_min <= value <= nominal_max
            ):
                lines.append(
                    f"  • {label}: {value:.2f} {unit} — **nominal bantta** "
                    f"({nominal_min}-{nominal_max} {unit}); ölçüm bu tanıyı "
                    f"DESTEKLEMİYOR, eşik/bağlam kontrolü gerekir."
                )
            else:
                lines.append(
                    f"  • {label}: {value:.2f} {unit} — ölçülen değer kayıtlı "
                    f"(KB nominal {nominal_min}-{nominal_max} {unit})."
                )
        if not lines:
            return ""
        return "\n📐 **Ölçüm Tutarlılığı:**\n" + "\n".join(lines) + "\n"

    @classmethod
    def _format_4stage_technician_report(
        cls,
        code: str,
        telemetry: dict[str, float],
        vehicle_make: str | None = None,
        vehicle_year: int | None = None,
    ) -> str:
        """Format an industry-standard 4-stage master technician field guide in concise format.

        ``vehicle_make`` / ``vehicle_year`` (T42 P2-2/P2-3): when the operator
        named a vehicle make/year, OEM-variant evidence is filtered to that make
        and the complaint corpus is queried with make/year filters. When both are
        ``None`` the previous unfiltered behaviour is preserved (fail-safe).
        """
        # A3-5: symptom branches hard-code KB keys. If a key is missing from the
        # shipped KB (e.g. a stripped build / future DB edit) an unconditional
        # lookup raised KeyError and the whole report failed to render. Fall
        # back to an honest "not in the local library" notice instead.
        if code not in EXPERT_KNOWLEDGE_BASE:
            logger.warning(
                "4-aşama raporu için KB kaydı bulunamadı — fail-safe uyarı basılıyor",
                extra={"code": code},
            )
            cat_char = code[0].upper() if code else "?"
            cat_desc = {
                "P": "Güç Aktarımı (Powertrain)",
                "C": "Şasi / ABS / ESP (Chassis)",
                "B": "Gövde / Konfor (Body)",
                "U": "Ağ / CAN İletişimi (Network)",
            }.get(cat_char, "Bilinmeyen")
            return (
                f"⚠️ **[{code}] Arıza Kodu Bulunamadı:**\n"
                f"Bu kod yerel teşhis kütüphanesinde kayıtlı değil.\n"
                f"• **Kategori:** {cat_desc}\n"
                f"• **Tavsiye:** Aracın yetkili servis kılavuzunu inceleyin veya UDS `0x19 0x02` servisi "
                f"ile çevre koşullarını (Freeze Frame) okuyun."
            )
        info = EXPERT_KNOWLEDGE_BASE[code]
        rpm = telemetry.get("EngineSpeed", 0.0)
        boost = telemetry.get("BoostPressure", 0.0)

        # T67-A (F-02 parity, AGENTS.md §2.3): the query path defaulted the
        # coolant reading to a FABRICATED 85.0 °C and then printed it whenever
        # RPM *or* boost was measured. A session with only RPM rendered
        # "1800 RPM, 0.00 Bar, 85.0°C" — the temperature was never measured.
        # The session path has been strict since F-02; the query path is now
        # too: each value prints only when the caller actually supplied it.
        _t_parts: list[str] = []
        if rpm > 0:
            _t_parts.append(f"{rpm:.0f} RPM")
        if boost > 0:
            _t_parts.append(f"{boost:.2f} Bar")
        _t_coolant = telemetry.get("CoolantTemp")
        if isinstance(_t_coolant, (int, float)) and _t_coolant > 0:
            _t_parts.append(f"{float(_t_coolant):.1f}°C")
        telemetry_str = (" | " + ", ".join(_t_parts)) if _t_parts else ""

        # T67-B (exhaustive answers): the report rendered `causes[:2]` and
        # `steps[:2]`. Measured on the live DB: 1,388 records carry 4 causes,
        # 1,213 carry 5, 219 carry 6+; 13,805 records carry exactly 3 steps, so
        # the third step was dropped for ~96% of the catalog. Every cause and
        # every step the record holds is now rendered.
        causes = info.get("causes", [])
        top_causes = list(causes)
        causes_formatted = "\n".join(f"  • {c}" for c in top_causes)
        if not causes_formatted:
            # No fabrication: an empty field renders as an explicit gap, never
            # as an invented generic cause.
            causes_formatted = "  • (Kayıtlı neden yok — uydurma neden üretilmedi.)"

        steps = info.get("steps", [])
        steps_lines: list[str] = []
        for idx, s in enumerate(steps):
            if len(s) >= 2:
                steps_lines.append(f"  {idx + 1}. {s[0]} *(Hedef: {s[1]})*")
            elif len(s) == 1:
                steps_lines.append(f"  {idx + 1}. {s[0]}")
        steps_formatted = (
            "\n".join(steps_lines) if steps_lines else "  • (Kayıtlı adım yok — uydurma adım üretilmedi.)"
        )

        # T67-A (AGENTS.md §2.3): the missing-field fallbacks INVENTED a
        # tolerance ("Nominal voltaj ve şasi dirençlerini test edin.") and a
        # diagnostic routine ("UDS Service 0x14"). An invented measurement
        # tolerance is precisely the fabrication §2.3 forbids. An absent field
        # now renders as an explicit gap; the stage-2/3 headers stay so the
        # 4-stage structure is intact.
        measurement_block = str(info.get("measurement", "") or "").strip()
        routine_block = str(info.get("uds_routine", "") or "").strip()

        # Check if there are related NHTSA recalls for this code (max 1)
        nhtsa_block = ""
        related_recalls = search_nhtsa_recalls(query=code, limit=1)
        if not related_recalls:
            title_lower = info.get("title", "").lower()
            for kw in ["trailer brake", "contactor", "interlock", "theft", "pats", "purge"]:
                if kw in title_lower:
                    related_recalls = search_nhtsa_recalls(query=kw, limit=1)
                    break
        if related_recalls:
            r = related_recalls[0]
            nhtsa_block = f"\n📢 **NHTSA Geri Çağırma:** {r.get('campaign_number')} ({r.get('manufacturer')}) — {r.get('component')}"

        # REVIEW (Tur-27 P0, @tuner AI plan Boguluk 2): merge edilmis ama HIC okunmayan
        # OEM zenginlik katmanlari. Geriye-uyumlu: alan yoksa blok uretilmez.
        # T42 P2-2: arac markasi verildiyse OEM varyantlari markaya gore
        # filtrelenir; alakasiz OEM gizlenir. Marka yoksa eski davranis birebir.
        oem_block = ""
        ov = info.get("oem_variants")
        if isinstance(ov, list) and ov:
            kept, hidden = filter_oem_variants(ov, vehicle_make)
            lines = []
            for item in kept:
                if isinstance(item, dict):
                    mk = item.get("manufacturer") or item.get("make") or "OEM"
                    ds = item.get("description") or item.get("meaning") or ""
                    if ds:
                        lines.append(f"  • **{mk}:** {str(ds)[:160]}")
                elif isinstance(item, str):
                    lines.append(f"  • {item[:160]}")
            if lines:
                hdr = "🏭 **OEM Varyantları (aynı kod, marka bazlı anlam):**"
                if vehicle_make:
                    hdr = f"🏭 **OEM Varyantları — {vehicle_make.upper()} (marka filtreli):**"
                oem_block += f"\n\n{hdr}\n" + "\n".join(lines)
                if hidden:
                    oem_block += f"\n  *(+{hidden} farklı marka varyantı gizlendi — marka filtresi aktif)*"

        gm = info.get("gm_monitor")
        if isinstance(gm, dict) and (gm.get("parameter") or gm.get("monitor")):
            p = str(gm.get("parameter", ""))[:120]
            mo = str(gm.get("monitor", ""))[:220]
            oem_block += f"\n\n🔬 **GM Monitor Testi:** {p}" + (f"\n  *Koşul:* {mo}" if mo else "")

        ev = info.get("nhtsa_evidence")
        if isinstance(ev, list) and ev:
            e0 = ev[0] if isinstance(ev[0], dict) else {}
            mv = " ".join(str(e0.get(k, "")) for k in ("make", "model", "year") if e0.get(k)).strip()
            sym = str(e0.get("symptom", ""))[:200]
            if mv or sym:
                oem_block += f"\n\n📊 **Alan Kanıtı (NHTSA):** {mv}" + (f"\n  *Şikayet:* {sym}" if sym else "")
            # Tur-27 P2: kac araçta raporlanmis? nhtsa_evidence kendi kapsamini tasir
            # (complaints corpus'u DTC metni icermez — oradan sayim yaniltici olur).
            if len(ev) > 1:
                _makes = {str(x.get("make", "")).strip() for x in ev if isinstance(x, dict) and x.get("make")}
                if _makes:
                    oem_block += f"\n  *Alan kapsamı:* {len(ev)} vaka / {len(_makes)} marka"

        # T42 P2-3: NHTSA sahip-sikayeti korpusu (4.388 kayit) motorda HIC
        # okunmuyordu. Marka/yil verildiginde sikayetler o araca gore
        # filtrelenir (search_nhtsa_complaints — lazy load + cache). Uydurma yok:
        # yalnizca DB'de var olan ozetler basilir; marka yoksa yalnizca genel
        # kapsam satiri gosterilir (bugunku davranisa ek, filtre yok).
        try:
            _cmp = search_nhtsa_complaints(make=vehicle_make, year=vehicle_year, limit=2)
            _cmp_hits = _cmp.get("complaints") or []
            if _cmp_hits:
                _lines = []
                for _e in _cmp_hits[:2]:
                    _comp = str(_e.get("components", "")).split(",")[0].strip()
                    _sum = str(_e.get("summary", "")).strip()
                    if _comp or _sum:
                        _lines.append(
                            f"  • [{_comp}] {_sum[:180]}" if _comp else f"  • {_sum[:180]}"
                        )
                if _lines:
                    _scope = (
                        f"{len(_cmp.get('matched_vehicles', []))} araç / "
                        f"{len(_cmp_hits)} şikayet"
                    )
                    if vehicle_make:
                        _hdr = (
                            "🗣️ **Sahip Şikayetleri (NHTSA complaints — "
                            f"{vehicle_make.upper()} marka filtreli):**"
                        )
                    else:
                        _hdr = "🗣️ **Sahip Şikayetleri (NHTSA complaints):**"
                    oem_block += f"\n\n{_hdr} {_scope}\n" + "\n".join(_lines)
        except Exception as exc:  # fail-safe: complaints asla raporu düsürmez
            logger.debug("NHTSA complaints fusion skipped: %s", exc)

        # T41 P1-3: `j1939_spn_fmi` kopru alani (697 DTC kaydinda DOLU) hic
        # okunmuyordu. OBD DTC -> ilgili J1939 SPN/FMI gecisini kur; agir vasita
        # teshisinde kod cevirisi saglar. Uydurma yok — yalnizca DB'de dolu
        # kayitlar basilir.
        bridge_block = ""
        _bridge = info.get("j1939_spn_fmi")
        if isinstance(_bridge, list) and _bridge:
            _b_lines: list[str] = []
            for _b in _bridge:
                if not isinstance(_b, dict):
                    continue
                _bspn = _b.get("spn")
                if _bspn is None:
                    continue
                _bname = str(_b.get("name", "") or "").strip()
                _bfmi = _b.get("fm")
                _bfmi_str = ""
                if isinstance(_bfmi, list) and _bfmi:
                    _bfmi_str = f" | FMI: {', '.join(str(x) for x in _bfmi)}"
                _bsys = _b.get("systems")
                _bsys_str = ""
                if isinstance(_bsys, list) and _bsys:
                    _bsys_str = f" | Sistem: {', '.join(str(x) for x in _bsys)}"
                _resolved = ""
                try:
                    _jdb = get_j1939_spn_database()
                    _spn_row = (_jdb.get("spns") or {}).get(f"SPN_{int(str(_bspn))}")
                    if isinstance(_spn_row, dict):
                        _resolved = _spn_row.get("title_tr") or _spn_row.get("name") or ""
                except Exception:
                    _resolved = ""
                _label = _bname or _resolved
                _b_lines.append(
                    f"  • **SPN {_bspn}**" + (f" — {str(_label)[:120]}" if _label else "")
                    + f"{_bfmi_str}{_bsys_str}"
                )
            if _b_lines:
                bridge_block = "\n\n🔗 **İlgili J1939 SPN Köprüsü (DTC↔SPN):**\n" + "\n".join(_b_lines)

        # P0-2 (finding 4): the keyword router picks the report from the
        # OPERATOR'S WORDS, not the measurement. This block cross-checks the
        # captured value against the threshold DB and states the outcome
        # honestly instead of asserting an overboost at a nominal reading.
        consistency_block = cls._measured_value_consistency(code, telemetry)

        # T70-B: the consistency line judges only the ONE signal the code maps
        # to. Every OTHER supplied measurement was previously unexamined, so a
        # physically impossible reading rode along unflagged. This sweep covers
        # all of them and emits nothing when all values are inside envelope.
        plausibility_block = telemetry_plausibility_block(telemetry)

        # T70-C: the cell-spread cross-check. Until now the ΔV computation
        # existed only in the CAN-frame forensics branch, so a DTC diagnosis of
        # P0A80 showed the KB's ">150 mV" threshold as prose without ever
        # comparing it to the two cell channels the caller had supplied.
        bms_block = bms_cell_imbalance_block(code, telemetry)

        # P0-3: when the operator named an FMI, state the FMI-SPECIFIC rung next
        # to the SPN-level one. The per-FMI rung is the more specific evidence,
        # so an electrical fault (FMI 3) is not shown as "stop the engine".
        _q_fmi = telemetry.get("_query_fmi")
        _fmi_sev = _resolve_fmi_severity(code, _q_fmi) if isinstance(_q_fmi, int) else None
        if _fmi_sev is not None:
            _fmi_title = _resolve_fmi_fault_title(code, _q_fmi)
            priority_line = (
                f"*(Öncelik: {_fmi_sev.value} — SPN düzeyi {info.get('severity', 'MEDIUM')}, "
                f"FMI {_q_fmi} özelinde)*"
            )
            if _fmi_title:
                priority_line += f"\n🔎 **FMI {_q_fmi} Arıza Modu:** {_fmi_title}"
        else:
            priority_line = f"*(Öncelik: {info.get('severity', 'MEDIUM')})*"

        # T67-A: stage 2/3 render only what the record actually holds. An empty
        # field states the gap instead of substituting an invented tolerance or
        # routine (AGENTS.md §2.3).
        _stage2_body = (
            f"  • {measurement_block}"
            if measurement_block
            else "  • (Bu kod için kayıtlı ölçüm toleransı yok — araç servis kılavuzundaki "
            "değerleri kullanın; uydurma tolerans üretilmedi.)"
        )
        _stage3_body = (
            f"  • `{routine_block}`"
            if routine_block
            else "  • (Bu kod için kayıtlı UDS/J1939 rutini yok — uydurma rutin üretilmedi.)"
        )
        # T67-A: stage 4 was hardcoded boilerplate for every code. It is now
        # driven by the record's own fields: `uds_routine` is quoted only when
        # the DB actually carries one, and the code-specific closing note comes
        # from the record's subsystem. Nothing is asserted about a procedure the
        # record does not describe.
        _stage4_parts: list[str] = []
        if routine_block:
            _stage4_parts.append(
                f"  • Parça değişimi/adaptasyon sonrası kayıtlı rutini uygulayın: `{routine_block}`"
            )
        _subsystem_note = str(info.get("subsystem", "") or "").strip()
        if _subsystem_note:
            _stage4_parts.append(
                f"  • Onarım sonrası **{_subsystem_note}** alt sisteminde yeniden test sürüşü "
                "yapıp kodu yeniden okuyun (onarım doğrulaması)."
            )
        _stage4_body = (
            "\n".join(_stage4_parts)
            if _stage4_parts
            else "  • (Kayıtlı parça/adaptasyon prosedürü yok — uydurma prosedür üretilmedi.)"
        )

        # T67-C: the DTC-side `procedures_full` (6,087 records) had NO consumer.
        # It is a `list[str]` of ordered repair steps harvested from
        # obd2.com/obdfyi and friends — a different SHAPE from the J1939 bundle
        # list, which is why the dict-only reader never saw it. Rendered here in
        # full, deduplicated against the KB steps already printed above so the
        # operator is not shown the same line twice.
        proc_dtc_block = ""
        _dpf = info.get("procedures_full")
        if isinstance(_dpf, list):
            _dpf_lines: list[str] = []
            _seen_dpf: set[str] = {
                " ".join(str(s).split()).lower()
                for s in (steps if isinstance(steps, list) else [])
                for s in ([s[0]] if isinstance(s, (list, tuple)) and s else ([s] if isinstance(s, str) else []))
            }
            for _item in _dpf:
                if isinstance(_item, dict):
                    # A J1939-style bundle accidentally present on a DTC row.
                    _t = _item.get("overview") or _item.get("text") or ""
                else:
                    _t = _item
                _t = str(_t).strip()
                _k = " ".join(_t.split()).lower()
                if _t and _k not in _seen_dpf:
                    _seen_dpf.add(_k)
                    _dpf_lines.append(_t)
            if _dpf_lines:
                _dpf_src = str(info.get("procedures_source_url", "") or "").strip()
                proc_dtc_block = (
                    f"\n\n📚 **Ayrıntılı Onarım Prosedürü (DB `procedures_full`, "
                    f"{len(_dpf_lines)} adım):**\n"
                    + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(_dpf_lines))
                    + (f"\n  *Kaynak:* {_dpf_src}" if _dpf_src else "")
                )

        # T69: multi-source procedure bundles. `procedures_full` holds ONE
        # source's steps; `procedures_full_multi` holds every ADDITIONAL
        # independent source, each with its own URL. Measured: P0420 carried a
        # 7-step obd2.com summary while troubleshootmyvehicle.com publishes a
        # 77-step test procedure (labelled "[TEST 1: ...]") for the same code.
        # Both are shown, grouped by source, deduplicated against everything
        # already rendered above. Quarantined bundles are never rendered (T2-3).
        multi_block = ""
        _multi = info.get("procedures_full_multi")
        if isinstance(_multi, list):
            _multi_lines: list[str] = []
            _seen_multi: set[str] = set()
            _multi_sources = 0
            _multi_steps = 0
            for _b in _multi:
                if not isinstance(_b, dict) or _b.get("_quarantined"):
                    continue
                _b_steps = [
                    str(s).strip()
                    for s in (_b.get("steps") or [])
                    if isinstance(s, str) and str(s).strip()
                ]
                _b_causes = [
                    str(c).strip()
                    for c in (_b.get("causes") or [])
                    if isinstance(c, str) and str(c).strip()
                ]
                _b_syms = [
                    str(s).strip()
                    for s in (_b.get("symptoms") or [])
                    if isinstance(s, str) and str(s).strip()
                ]
                if not (_b_steps or _b_causes or _b_syms):
                    continue
                _site = str(_b.get("source_site") or "").strip()
                _url = str(_b.get("source_url") or "").strip()
                _hdr = _site or _url or "kaynak belirtilmemiş"
                _block: list[str] = []
                if _b_steps:
                    _block.append("  • **Adımlar:**")
                    _n = 0
                    for s in _b_steps:
                        k = " ".join(s.split()).lower()
                        if k in _seen_multi:
                            continue
                        _seen_multi.add(k)
                        _n += 1
                        _block.append(f"    {_n}. {s}")
                    _multi_steps += _n
                if _b_causes:
                    _kc = [c for c in _b_causes if " ".join(c.split()).lower() not in _seen_multi]
                    if _kc:
                        _block.append("  • **Olası Nedenler:**")
                        for c in _kc:
                            _seen_multi.add(" ".join(c.split()).lower())
                            _block.append(f"    - {c}")
                if _b_syms:
                    _ks = [s for s in _b_syms if " ".join(s.split()).lower() not in _seen_multi]
                    if _ks:
                        _block.append("  • **Belirtiler:**")
                        for s in _ks:
                            _seen_multi.add(" ".join(s.split()).lower())
                            _block.append(f"    - {s}")
                if not _block:
                    continue
                _multi_sources += 1
                _multi_lines.append(f"\n  ── **{_hdr}** ──")
                if _url:
                    _multi_lines.append(f"    *Kaynak:* {_url}")
                _multi_lines.extend(_block)
            if _multi_lines:
                multi_block = (
                    f"\n\n📖 **Ek Bağımsız Kaynak Prosedürleri "
                    f"({_multi_sources} kaynak, {_multi_steps} adım — "
                    "tekrarlar ayıklandı):**" + "\n".join(_multi_lines)
                )

        # T67-C: VAG-specific decode (2,102 records) — the same code means
        # something specific in the VAG (VW/Audi/Seat/Skoda) world, and the
        # numeric VAG code is what a VCDS reader shows. Rendered only when the
        # record carries it.
        vag_block = ""
        _vag = info.get("vag_code")
        _vag_desc = str(info.get("vag_desc_en", "") or "").strip()
        if _vag or _vag_desc:
            _vag_txt = f"  • **VAG Kodu:** {_vag}" if _vag else ""
            if _vag_desc:
                _vag_txt += f"\n  • **VAG Açıklaması (EN):** {_vag_desc}"
            vag_block = "\n\n🚗 **VAG (VW/Audi/Seat/Skoda) Karşılığı:**\n" + _vag_txt

        # T67-C: `evidence_url` (11,088 records) — the provenance link for the
        # record. A technician verifying a diagnosis needs the source.
        evidence_block = ""
        _ev_url = str(info.get("evidence_url", "") or "").strip()
        if _ev_url and _ev_url != str(info.get("procedures_source_url", "") or "").strip():
            evidence_block = f"\n\n🔖 **Kaynak Kanıt:** {_ev_url}"

        report_text = (
            f"🚨 **[{code}] — {info.get('title', code)}** {priority_line}\n"
            f"🏷️ **Alt Sistem:** {info.get('subsystem', 'Genel Teşhis')}{telemetry_str}\n"
            f"{consistency_block}\n"
            f"{plausibility_block}"
            f"{bms_block}"
            f"🔍 **Olası Nedenler:**\n{causes_formatted}\n\n"
            f"📋 **4-AŞAMALI USTA TEKNİSYEN SAHA ONARIM KILAVUZU:**\n"
            f"**Aşama 1: Görsel & Mekanik Kontrol:**\n{steps_formatted}\n"
            f"⚡ **Aşama 2: Kesin Multimetre & Osiloskop Toleransları:**\n{_stage2_body}\n"
            f"💻 **Aşama 3: UDS / J1939 Özel Teşhis Rutinleri:**\n{_stage3_body}\n"
            f"🔧 **Aşama 4: Parça Değişim & Adaptasyon Prosedürü:**\n{_stage4_body}"
            f"{procedure_corpus_block(code)}"
            f"{proc_dtc_block}"
            f"{multi_block}"
            f"{vag_block}"
            f"{evidence_block}"
            f"{bridge_block}"
            f"{nhtsa_block}"
            f"{oem_block}"
        )
        # P0-3 parity (session_report.py:217-224): a diagnostic REPORT is a
        # record, not a command surface. This generator used to mint a
        # destructive UDS 0x14 DTC-clear button unconditionally — on a report
        # the operator never asked to act on. The report is now read-only:
        # triggers come only from the operator's own words, exactly as
        # `extract_action_triggers` has enforced since P0-1.
        actions = extract_action_triggers("", "")
        return attach_action_triggers(report_text, actions)

    @classmethod
    def _format_j1939_technician_report(
        cls,
        spn_entry: dict[str, Any],
        query: str,
        telemetry: dict[str, float],
        vehicle_make: str | None = None,
    ) -> str:
        """Format a heavy-duty commercial vehicle J1939 SPN & FMI diagnostic guide in concise format.

        ``vehicle_make`` (T42 P2-2): when given, ``oem_engine_families`` /
        ``oem_field_evidence`` are filtered to that make so unrelated engine
        families are hidden. ``None`` preserves the previous behaviour.
        """
        spn = spn_entry.get("spn", 0)
        name = spn_entry.get("name", "Bilinmeyen SPN")
        title_tr = spn_entry.get("title_tr", name)
        subsystem = spn_entry.get("subsystem", "Ağır Vasıta J1939")
        pgn = spn_entry.get("associated_pgn", 0)
        # @scout Tur-33: `associated_pgn` bir SPN'in periyodik J1939-71 telemetri
        # PGN'idir. J1939-73 teşhis SPN'leri (DM1/DM2) ve OEM tescilli SPN'ler
        # boyle bir PGN tasimaz; onlara PGN uydurmak standart disidir. Raporu
        # PGN'i 0 gostermek yerine iletim kaynagini acikca belirt.
        pgn_source = str(spn_entry.get("pgn_source", "") or "")
        has_pgn = isinstance(pgn, int) and pgn > 0
        if has_pgn:
            pgn_line = f"**PGN:** {pgn}"
            step1_pgn = (
                f"CAN hattında PGN {pgn} periyodunu ve DM1 aktif arıza lambasını kontrol edin."
            )
        elif pgn_source == "j1939_73_diagnostic_only":
            pgn_line = "**İletim:** J1939-73 Teşhis Çerçevesi (DM1/PGN 65226)"
            step1_pgn = (
                "CAN hattında DM1 (PGN 65226) yayınını ve aktif arıza lambasını kontrol edin; "
                "bu SPN periyodik bir telemetri PGN'i taşımaz, yalnızca teşhis "
                "çerçevesiyle bildirilir."
            )
        elif pgn_source == "proprietary_oem":
            pgn_line = "**İletim:** OEM tescilli (standart PGN yok)"
            step1_pgn = (
                "CAN hattında DM1 (PGN 65226) ve OEM'e özgü teşhis çerçevelerini izleyin; "
                "bu SPN OEM tescilli aralıktadır, standart bir yayın PGN'i yoktur."
            )
        else:
            pgn_line = "**PGN:** bilinmiyor"
            step1_pgn = (
                "CAN hattında DM1 (PGN 65226) aktif arıza lambasını kontrol edin."
            )
        unit = spn_entry.get("unit", "-")
        desc = spn_entry.get("description", "")
        range_info = spn_entry.get("range", [spn_entry.get("range_min", 0), spn_entry.get("range_max", 0)])
        range_str = f"{range_info[0]}..{range_info[1]} {unit}" if isinstance(range_info, list) and len(range_info) >= 2 else f"{range_info} {unit}"

        # T74: same shared extractor as the router, so a compact form
        # ("SPN100FMI3") resolves the per-FMI rung here too. The DB keys
        # `fault_matrix` / `fmi_definitions` / `fmi_map` by UNPADDED decimal
        # ("3", never "03"), so the captured digits are canonicalised — a
        # zero-padded "FMI03" must not silently miss the recorded rung.
        _fmi_num_int = query_fmi_number(query)
        fmi_num = "" if _fmi_num_int is None else str(_fmi_num_int)
        fmi_tree: dict[str, Any] | None = None
        fmi_info_str = ""
        if _fmi_num_int is not None:
            fmi_tree = spn_entry.get("fault_matrix", {}).get(fmi_num)
            if not fmi_tree:
                j1939_db = get_j1939_spn_database()
                fmi_def = j1939_db.get("fmi_definitions", {}).get(fmi_num, {})
                if fmi_def:
                    fmi_tree = {
                        "fmi_name": fmi_def.get("name", ""),
                        "fault_title": fmi_def.get("description_tr", ""),
                        "diagnostic_action": fmi_def.get("diagnostic_action", ""),
                        "severity": "MEDIUM",
                    }
            if fmi_tree:
                fmi_info_str = (
                    f"⚡ **FMI {fmi_num} ({fmi_tree.get('fmi_name', '')}):** "
                    f"{fmi_tree.get('fault_title', fmi_tree.get('description_tr', ''))}\n"
                    f"• **Eylem:** {fmi_tree.get('diagnostic_action', fmi_tree.get('action', 'Sensör devresini kontrol edin.'))}\n"
                )
                # T41 P1-5: fault_matrix satirinda DOLU olan ama rapora hic
                # girmeyen `severity_basis` (onem gerekcesi) ve `lamp` (ariza
                # lambasi) alanlarini yuzeye cikar. Uydurma yok — alan yoksa
                # satir uretilmez.
                _sev_basis = str(fmi_tree.get("severity_basis", "") or "").strip()
                if _sev_basis:
                    fmi_info_str += f"• **Önem Gerekçesi:** {_sev_basis[:200]}\n"
                _lamp = str(fmi_tree.get("lamp", "") or "").strip()
                if _lamp:
                    fmi_info_str += f"• **Gösterge Lambası:** {_lamp[:80]}\n"
                fmi_info_str += "\n"

        # REVIEW (Tur-27 P0, @tuner AI plan Boguluk 2): SPN girdilerindeki OEM saha
        # kanıtları (dd_procedures, oem_field_evidence) daha once raporda HIC gorunmuyordu.
        oem_lines: list[str] = []
        _seen_lines: set[str] = set()

        def _add_line(txt: str, limit: int = 170) -> None:
            t = txt[:limit]
            if t and t not in _seen_lines:
                _seen_lines.add(t)
                oem_lines.append(f"  • {t}")

        dd = spn_entry.get("dd_procedures")
        if isinstance(dd, list) and dd:
            shown = 0
            for p in dd:
                if shown >= 3:
                    break
                if isinstance(p, dict):
                    t = p.get("title") or p.get("step") or p.get("description") or ""
                    if t:
                        before = len(oem_lines)
                        _add_line(str(t))
                        if len(oem_lines) > before:
                            shown += 1
                elif isinstance(p, str) and p.strip():
                    before = len(oem_lines)
                    _add_line(p)
                    if len(oem_lines) > before:
                        shown += 1
        fe = spn_entry.get("oem_field_evidence")
        if isinstance(fe, list) and fe:
            for e in fe:
                if isinstance(e, dict):
                    mk = e.get("manufacturer") or e.get("oem") or ""
                    tx = e.get("evidence") or e.get("note") or e.get("description") or ""
                    if tx:
                        _add_line(f"**{mk}:** {tx}" if mk else str(tx), 150)
                elif isinstance(e, str):
                    _add_line(e, 150)

        # T42 P2-2: `oem_engine_families` (64 SPN'de dolu liste) ve
        # `oem_field_evidence` (400 SPN'de dict: {family_count, families,
        # field_fmi_meanings}) motorda karar vermiyordu — yalnizca
        # `oem_field_evidence` list sanilyordu (aslinda dict), bu yuzden HIC
        # gorunmuyordu. Arac markasi verildiyse aileler markaya gore filtrelenir,
        # alakasiz motor aileleri gizlenir; marka yoksa hepsi basilir (fail-safe).
        _fam_hidden = 0
        _families: list[str] = []
        _fam_src = spn_entry.get("oem_engine_families")
        if not isinstance(_fam_src, list) or not _fam_src:
            if isinstance(fe, dict):
                _fam_src = fe.get("families")
        if isinstance(_fam_src, list) and _fam_src:
            _families, _fam_hidden = filter_oem_families(_fam_src, vehicle_make)
        if _families:
            _fam_hdr = "🚚 **OEM Motor Aileleri (DB):**"
            if vehicle_make:
                _fam_hdr = f"🚚 **OEM Motor Aileleri — {vehicle_make.upper()} (marka filtreli):**"
            _fam_txt = ", ".join(str(f) for f in _families)
            oem_lines.append(f"  • {_fam_hdr} {_fam_txt}")
            if _fam_hidden:
                oem_lines.append(f"  • *(+{_fam_hidden} farklı marka motor ailesi gizlendi)*")

        # T42 P2-2: saha kaniti FMI anlamlari (field_fmi_meanings) — FMI
        # sorgusu varsa o FMI'nin saha anlami DB'den basilir.
        if isinstance(fe, dict) and _fmi_num_int is not None:
            _ffm = fe.get("field_fmi_meanings")
            if isinstance(_ffm, dict):
                _meanings = _ffm.get(fmi_num)
                if isinstance(_meanings, list) and _meanings:
                    _uniq = []
                    for _m in _meanings:
                        _ms = str(_m).strip()
                        if _ms and _ms not in _uniq:
                            _uniq.append(_ms)
                    if _uniq:
                        oem_lines.append(
                            "  • **Saha FMI Anlamı:** " + " | ".join(_uniq)
                        )

        oem_block = ""
        if oem_lines:
            _fmi_hdr = ""
            if _fmi_num_int is not None:
                _fmi_hdr = f" (SPN {spn} seviyesinde)"
            oem_block = f"\n\n🔧 **OEM Saha Prosedürü / Kanıtı{_fmi_hdr}:**\n" + "\n".join(oem_lines)

        # FMI severity rapora tasi (Boguluk 3, P1)
        if _fmi_num_int is not None and fmi_tree:
            _sev = str(fmi_tree.get("severity", "")).upper()
            if _sev:
                oem_block += f"\n\n⚠️ **FMI Önem Derecesi:** {_sev}"
            # oem_occurrences de zengin ama raporda gorunmuyordu (Tur-27 P0)
            _occ = fmi_tree.get("oem_occurrences")
            if isinstance(_occ, list) and _occ:
                _show = [str(x) for x in _occ]
                oem_block += "\n\n🚚 **Görüldüğü Araçlar/Platformlar:** " + ", ".join(_show)

        # T41 P1-2: J1939 SPN girdisindeki `causes` (3.720 SPN dolu) ve `steps`
        # (3.457 SPN dolu) alanlari motorda HIC okunmuyordu. Uydurma yok —
        # yalnizca DB'de dolu olan liste elemanlari basilir (fail-safe).
        j1939_causes_block = ""
        _jc = spn_entry.get("causes")
        if isinstance(_jc, list):
            _jc_lines = [str(c).strip() for c in _jc if isinstance(c, str) and str(c).strip()]
            if _jc_lines:
                j1939_causes_block = "\n\n🔍 **J1939 Olası Nedenler (DB):**\n" + "\n".join(
                    f"  • {c}" for c in _jc_lines
                )
        j1939_steps_block = ""
        _js = spn_entry.get("steps")
        if isinstance(_js, list):
            _js_lines = [str(s).strip() for s in _js if isinstance(s, str) and str(s).strip()]
            if _js_lines:
                j1939_steps_block = "\n\n📋 **J1939 Onarım Adımları (DB):**\n" + "\n".join(
                    f"  {i + 1}. {s}" for i, s in enumerate(_js_lines)
                )

        # T41 P1-1: Eaton PIM tam prosedürleri (`procedures_full`, 59 SPN) motor
        # tarafindan HIC okunmuyordu (T36-T39 kurtarma emegi atildi). Yalnizca
        # DB'de var olan alanlar basilir — uydurma prosedur uretilmez.
        #
        # T67-B (exhaustive answers): this block used to render the TWO richest
        # CLEAN blocks. Measured on the live DB: 929 SPNs hold more than 2
        # blocks, SPN_157 holds 52 and SPN_100 holds 34 (731 steps across them).
        # Every clean block is now rendered — the operator asked for every
        # possibility — but blocks are GROUPED BY SOURCE SITE, because one SPN
        # is typically covered by a dozen sites and the same procedure text is
        # republished across them. Grouping makes the agreement between
        # independent sources visible instead of burying it in a flat list.
        #
        # Deduplication is measured, not assumed: across all SPN blocks the
        # 56,963 step strings collapse to 47,398 unique (83.2%), so identical
        # lines repeated across harvest bundles are dropped and the remaining
        # text is printed once. No information is lost, only repetition.
        procedures_block = ""
        _pf = spn_entry.get("procedures_full")
        if isinstance(_pf, list) and _pf:
            _pf_lines: list[str] = []
            _seen_proc: set[str] = set()
            _steps_rendered = 0
            _causes_rendered = 0
            _symptoms_rendered = 0

            def _richness(block: dict) -> int:
                score = len(str(block.get("overview") or ""))
                for key in ("steps", "first_moves", "causes", "symptoms"):
                    val = block.get(key)
                    if isinstance(val, list):
                        score += sum(len(str(x)) for x in val)
                return score

            def _norm(text: str) -> str:
                return " ".join(str(text).split()).lower()

            def _emit(line: str, text: str) -> bool:
                """Append ``line`` unless its underlying ``text`` was already shown.

                The dedupe key is the TEXT alone, never the numbered rendering:
                the same step listed as #1 in one harvest bundle and #3 in
                another is one piece of information, not two.
                """
                key = _norm(text)
                if not key or key in _seen_proc:
                    return False
                _seen_proc.add(key)
                _pf_lines.append(line)
                return True

            _clean = [
                b
                for b in _pf
                if isinstance(b, dict) and not b.get("_quarantined")
            ]
            # Group by source site (falling back to the harvest method), keeping
            # the richest block of each group first. Sites are ordered by their
            # best block's richness so the most detailed source leads.
            _groups: dict[str, list[dict]] = {}
            for _b in _clean:
                _key = str(_b.get("source_site") or _b.get("source") or _b.get("method") or "(kaynak belirtilmemiş)")
                _groups.setdefault(_key, []).append(_b)
            _ordered_groups = sorted(
                _groups.items(),
                key=lambda kv: (-max(_richness(b) for b in kv[1]), kv[0]),
            )
            _rendered = 0
            for _site, _blocks in _ordered_groups:
                _ranked = sorted(_blocks, key=_richness, reverse=True)
                _site_hdr_emitted = False
                for _proc in _ranked:
                    _block_lines: list[str] = []
                    _ec = str(_proc.get("eaton_fault_code", "") or "").strip()
                    if _ec:
                        _block_lines.append(f"  • **{_ec[:120]}**")
                    for _key, _label in (
                        ("overview", "Genel Bakış"),
                        ("detection", "Tespit"),
                        ("conditions_set_active", "Aktif Olma Koşulu"),
                        ("fallback", "Yedek Mod / Etki"),
                        ("possible_causes", "Olası Nedenler"),
                    ):
                        _val = str(_proc.get(_key, "") or "").strip()
                        if _val:
                            _block_lines.append(f"  • **{_label}:** {_val}")
                    # Non-Eaton schema (T63/T66 harvest bundles): steps / causes /
                    # symptoms / first_moves. Every item renders; duplicates across
                    # bundles are dropped by _emit (keyed on the text, not the number).
                    _bm = _proc.get("steps") or _proc.get("first_moves")
                    if isinstance(_bm, list):
                        _bm_lines = [
                            str(s).strip() for s in _bm if isinstance(s, str) and str(s).strip()
                        ]
                        if any(_norm(s) not in _seen_proc for s in _bm_lines):
                            _block_lines.append("  • **Adımlar:**")
                            _n = 0
                            for s in _bm_lines:
                                if _emit(f"    {_n + 1}. {s}", s):
                                    _n += 1
                                    _steps_rendered += 1
                    _bc = _proc.get("causes")
                    if isinstance(_bc, list):
                        _bc_lines = [
                            str(c).strip() for c in _bc if isinstance(c, str) and str(c).strip()
                        ]
                        if any(_norm(c) not in _seen_proc for c in _bc_lines):
                            _block_lines.append("  • **Olası Nedenler:**")
                            for c in _bc_lines:
                                if _emit(f"    - {c}", c):
                                    _causes_rendered += 1
                    _bs = _proc.get("symptoms")
                    if isinstance(_bs, list):
                        _bs_lines = [
                            str(s).strip() for s in _bs if isinstance(s, str) and str(s).strip()
                        ]
                        if any(_norm(s) not in _seen_proc for s in _bs_lines):
                            _block_lines.append("  • **Belirtiler:**")
                            for s in _bs_lines:
                                if _emit(f"    - {s}", s):
                                    _symptoms_rendered += 1
                    if not _block_lines:
                        # Nothing renderable here — keep looking instead of breaking.
                        continue
                    if not _site_hdr_emitted:
                        _pf_lines.append(f"\n  ── **{_site}** ──")
                        _site_hdr_emitted = True
                    _pf_lines.extend(_block_lines)
                    _rendered += 1
            if _pf_lines:
                _hdr = (
                    "📖 **OEM Tam Prosedürü (DB `procedures_full`):**"
                    f"\n  *({_rendered} blok / {len(_ordered_groups)} kaynak sitesi; "
                    f"{_steps_rendered} adım, {_causes_rendered} neden, "
                    f"{_symptoms_rendered} belirti — tümü gösterildi, "
                    "tekrarlar ayıklandı)*"
                )
                procedures_block = "\n\n" + _hdr + "\n" + "\n".join(_pf_lines)

        # T67-C: three rich SPN-level fields had NO consumer in the engine.
        #   * `diagnostic_steps` (546 SPNs) — a parallel, field-verified step
        #     list written by the T-verified harvests; SPN_100 alone carries 54.
        #   * `fmi_map` (170 SPNs) — per-FMI causes/actions, the most specific
        #     evidence in the record for the FMI the operator actually named.
        #   * `field_evidence` (244 SPNs) — real field articles {url,title,excerpt}.
        # All three are rendered when present and when the query names an FMI
        # for fmi_map. No fabrication: absent fields produce no block.
        diag_steps_block = ""
        _ds = spn_entry.get("diagnostic_steps")
        if isinstance(_ds, list):
            _ds_lines = [str(s).strip() for s in _ds if isinstance(s, str) and str(s).strip()]
            if _ds_lines:
                diag_steps_block = (
                    f"\n\n🧰 **Saha Doğrulamalı Teşhis Adımları (DB `diagnostic_steps`, "
                    f"{len(_ds_lines)} adım):**\n"
                    + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(_ds_lines))
                )

        fmi_map_block = ""
        _fm_map = spn_entry.get("fmi_map")
        if isinstance(_fm_map, dict) and _fmi_num_int is not None:
            _row = _fm_map.get(fmi_num)
            if not isinstance(_row, dict):
                # Some harvests key the map by int, others by "FMI 3".
                _row = _fm_map.get(_fmi_num_int)
            if isinstance(_row, dict):
                _parts: list[str] = []
                _mc = _row.get("causes")
                if isinstance(_mc, str) and _mc.strip():
                    _parts.append(
                        "  • **FMI'ye Özel Nedenler:** "
                        + "; ".join(x.strip() for x in _mc.split(";") if x.strip())
                    )
                elif isinstance(_mc, list):
                    _ml = [str(x).strip() for x in _mc if str(x).strip()]
                    if _ml:
                        _parts.append("  • **FMI'ye Özel Nedenler:** " + "; ".join(_ml))
                _ma = _row.get("actions")
                if isinstance(_ma, str) and _ma.strip():
                    _parts.append(
                        "  • **FMI'ye Özel Eylemler:** "
                        + "; ".join(x.strip() for x in _ma.split(";") if x.strip())
                    )
                elif isinstance(_ma, list):
                    _ml = [str(x).strip() for x in _ma if str(x).strip()]
                    if _ml:
                        _parts.append("  • **FMI'ye Özel Eylemler:** " + "; ".join(_ml))
                if _parts:
                    fmi_map_block = (
                        f"\n\n🎯 **FMI {fmi_num} — SPN'ye Özel Harita "
                        "(DB `fmi_map`):**\n" + "\n".join(_parts)
                    )

        field_evidence_block = ""
        _fev = spn_entry.get("field_evidence")
        if isinstance(_fev, list):
            _fev_lines: list[str] = []
            for _e in _fev:
                if not isinstance(_e, dict):
                    continue
                _et = str(_e.get("title", "") or "").strip()
                _ex = str(_e.get("excerpt", "") or "").strip()
                _eu = str(_e.get("url", "") or "").strip()
                if _et or _ex:
                    _fev_lines.append(
                        f"  • **{_et}**" + (f" — {_ex}" if _ex else "")
                        + (f"\n    *Kaynak:* {_eu}" if _eu else "")
                    )
            if _fev_lines:
                field_evidence_block = (
                    f"\n\n🗂️ **Saha Makaleleri (DB `field_evidence`, {len(_fev_lines)} kayıt):**\n"
                    + "\n".join(_fev_lines)
                )

        # T67-C: cross-reference to the passenger-car DTC codes the same fault
        # maps to (2,411 SPNs). Lets an OBD-II reader's code be tied to the
        # heavy-duty SPN under diagnosis.
        xref_block = ""
        _xref = spn_entry.get("sitrakin_dtc_codes")
        if isinstance(_xref, list):
            _xref_codes = [str(x).strip() for x in _xref if str(x).strip()]
            if _xref_codes:
                xref_block = (
                    "\n\n🔁 **İlgili OBD-II DTC Kodları (DB `sitrakin_dtc_codes`):** "
                    + ", ".join(f"`{c}`" for c in _xref_codes)
                )

        report_text = (
            f"🚛 **[SPN {spn}] — {title_tr} ({name})**\n"
            f"🏷️ **Alt Sistem:** {subsystem} | {pgn_line} | **Aralık:** {range_str}\n"
            f"📝 **Açıklama:** {desc}\n\n"
            f"{fmi_info_str}"
            f"{telemetry_plausibility_block(telemetry)}"
            f"📋 **SAE J1939-73 Saha Teşhis Adımları:**\n"
            f"1. {step1_pgn}\n"
            f"2. Sensör besleme voltajını (5V/12V) ve şasi hattını multimetre ile test edin."
            f"{j1939_causes_block}"
            f"{j1939_steps_block}"
            f"{diag_steps_block}"
            f"{fmi_map_block}"
            f"{procedures_block}"
            f"{procedure_corpus_block(f'SPN{spn}')}"
            f"{field_evidence_block}"
            f"{xref_block}"
            f"{oem_block}"
        )
        # P0-3 parity (session_report.py:217-224): the report is a record, not a
        # command surface — the unconditional DM11 clear button is gone. The
        # READ-ONLY DM1 query survives.
        actions = [make_j1939_dm1_action()]
        return attach_action_triggers(report_text, actions)




# ============================================================================
# MAIN AI DIAGNOSTIC COPILOT (DETERMINISTIC OFFLINE ENGINE)
# ============================================================================

# ----------------------------------------------------------------------------
# U4 (plan FAZ 3.3): the 7 hardcoded expert scenarios as module-level
# ScenarioRule data. Each body is the VERBATIM original block from
# _analyze_local_expert (code motion only — behavior must be preserved
# bit-for-bit; tests/unit/test_ai_copilot.py is the locked acceptance test).
# ----------------------------------------------------------------------------
@dataclass(slots=True)
class ScenarioContext:
    """Mutable accumulator shared by scenario bodies (original local vars).

    F-02 (P0, No Fabricated Telemetry): the ``has_*`` flags record whether a
    signal was ACTUALLY MEASURED in this session. Scenario bodies MUST consult
    them before emitting a telemetry correlation sentence, so a missing signal
    can never be narrated as a measured value (e.g. "motor 0 RPM devirde...",
    "soğutma sıvısı 85.0°C"). Raw values keep their neutral defaults only for
    arithmetic; the flags are the sole authority on measurement provenance.
    """

    dtcs: list[dict[str, object]]
    rpm: float
    boost_bar: float
    coolant_temp: float
    severity: "FaultSeverity"
    likely_causes: list[str]
    steps: list["TroubleshootingStep"]
    correlations: list[str]
    affected: list[str]
    # F-02 provenance flags — True ONLY when the key was present in the input
    # telemetry snapshot (i.e. the value is a real reading, not a default).
    has_rpm: bool = False
    has_boost: bool = False
    has_coolant: bool = False


@dataclass(slots=True, frozen=True)
class ScenarioRule:
    """One deterministic expert scenario: DTC pattern + telemetry trigger + body."""

    name: str
    matches: Any  # predicate (d: dict) -> bool — also defines handled_indices
    telemetry_trigger: Any  # predicate (rpm, boost, coolant) -> bool (fires w/o DTCs)
    body: Any  # (ctx: ScenarioContext) -> int  (matched DTC count)


def _oil_matches(d: dict[str, object]) -> bool:
    return d.get("spn") == 100


def _raise_severity(current: FaultSeverity, candidate: FaultSeverity) -> FaultSeverity:
    """Monotonic (raise-only) severity merge.

    REVIEW (severity downgrade): scenario bodies assigning MEDIUM
    unconditionally overwrote an earlier CRITICAL_STOP verdict — a vehicle
    with a critical oil-pressure fault PLUS an injector DTC reported only
    MEDIUM. Severity can only rise; order of scenario evaluation is then
    irrelevant.
    """
    # HIGH rung restored (379 external-DB DTC records carry it; drive-safety
    # policy maps HIGH->RED). Monotonic order: INFO < LOW < MEDIUM < HIGH <
    # CRITICAL_STOP.
    _order = {FaultSeverity.INFO: 0, FaultSeverity.LOW: 1, FaultSeverity.MEDIUM: 2, FaultSeverity.HIGH: 3, FaultSeverity.CRITICAL_STOP: 4}
    return candidate if _order[candidate] > _order[current] else current


def _oil_body(ctx: ScenarioContext) -> int:
    matched = sum(1 for d in ctx.dtcs if _oil_matches(d))
    if not any(_oil_matches(d) for d in ctx.dtcs):
        return 0
    # P0-3 (2026-09-21 audit, finding 7): this rule unconditionally forced
    # CRITICAL_STOP for SPN 100, regardless of FMI. An electrically-faulted
    # sensor (FMI 3/4/5/6) then advised "Motoru kapatın" exactly like a real
    # loss of oil pressure. The per-FMI rung recorded in the SPN database is
    # the more specific evidence and takes precedence when present.
    _fmi_sev = None
    for _d in ctx.dtcs:
        if not _oil_matches(_d):
            continue
        _fmi_sev = _resolve_fmi_severity("SPN 100", _d.get("fmi"))
        if _fmi_sev is not None:
            break
    ctx.severity = FaultSeverity(_fmi_sev.value) if _fmi_sev is not None else FaultSeverity.CRITICAL_STOP
    ctx.affected.append("Motor Yağlama & Yatak Sistemi")
    ctx.likely_causes.append(
        "Kritik düşük yağ basıncı (Yağ pompası aşınması, karterde yağ eksilmesi veya filtre tıkanıklığı)"
    )
    ctx.steps.append(
        TroubleshootingStep(
            1,
            "Motoru derhal durdurun ve yağ çubuğundan yağ seviyesini kontrol edin.",
            "Yağ Karteri / Çubuğu",
            "Kolay (Görsel)",
        )
    )
    ctx.steps.append(
        TroubleshootingStep(
            2,
            "Mekanik yağ basınç göstergesi ile karter basıncını ölçün (Rölantide min 1.0 bar, 2000 RPM'de 3.0 bar).",
            "Yağ Basınç Sensörü Portu",
            "Orta (Alet Gerekir)",
        )
    )
    ctx.correlations.append(
        f"Kritik yağ basıç arızası mevcutken motor devri {ctx.rpm:.0f} RPM seviyesinde; yatak sarma riski çok yüksek!"
    )
    return matched


def _ev_matches(d: dict[str, object]) -> bool:
    return str(d.get("code", "")).upper() in {"P0AA6", "P0A0B", "P0A80", "P0A93"}


def _ev_body(ctx: ScenarioContext) -> int:
    matched = sum(1 for d in ctx.dtcs if _ev_matches(d))
    if matched == 0:
        return 0
    ctx.severity = FaultSeverity.CRITICAL_STOP
    ctx.affected.append("EV Yüksek Voltaj Güvenlik & Batarya")
    ctx.likely_causes.append("Yüksek voltaj izolasyon direnci düşüklüğü veya HVIL interlock güvenlik hattı kesintisi.")
    ctx.steps.append(
        TroubleshootingStep(
            len(ctx.steps) + 1,
            "LOTO güvenlik protokolünü uygulayın: MSD şalterini çekin, 10 dk bekleyin, 1000V DMM ile sıfır enerji teyidi yapın.",
            "Manuel Servis Şalteri (MSD)",
            "İleri (Servis)",
        )
    )
    ctx.steps.append(
        TroubleshootingStep(
            len(ctx.steps) + 1,
            "Fluke 1587 / Megger ile 500V/1000V DC testinde HV+ ve HV- hatlarının şasiye izolasyon direncini ölçün (>50 MΩ olmalıdır).",
            "HV Güç Hatları & Kompresör",
            "İleri (Servis)",
        )
    )
    ctx.correlations.append("Yüksek voltaj güvenlik kilidi devrede; kontaktörler ark yapmadan otomatik açıldı.")
    return matched


def _injector_matches(d: dict[str, object]) -> bool:
    spn = d.get("spn")
    return isinstance(spn, int) and 651 <= spn <= 656


def _injector_body(ctx: ScenarioContext) -> int:
    count = sum(1 for d in ctx.dtcs if _injector_matches(d))
    for d in ctx.dtcs:
        spn = d.get("spn")
        if isinstance(spn, int) and 651 <= spn <= 656:
            cyl_idx = spn - 650
            ctx.severity = _raise_severity(ctx.severity, FaultSeverity.MEDIUM)
            ctx.affected.append(f"Silindir #{cyl_idx} Yakıt Enjeksiyonu")
            ctx.likely_causes.append(f"Silindir #{cyl_idx} enjektör devresi arızası (Açık devre, kısa devre veya geri dönüş kaçağı).")
            ctx.steps.append(
                TroubleshootingStep(
                    len(ctx.steps) + 1,
                    f"Silindir #{cyl_idx} enjektör bobin direncini (0.35 - 0.55 Ω) ölçün.",
                    f"{cyl_idx}. Silindir Enjektörü",
                    "Orta (Alet Gerekir)",
                )
            )
    return count


def _dpf_matches(d: dict[str, object]) -> bool:
    return d.get("spn") in {3251, 3719} or "DPF" in str(d.get("description", "")).upper()


def _dpf_body(ctx: ScenarioContext) -> int:
    matched = sum(1 for d in ctx.dtcs if _dpf_matches(d))
    if matched == 0:
        return 0
    ctx.severity = _raise_severity(ctx.severity, FaultSeverity.MEDIUM)
    ctx.affected.append("Egzoz & DPF Sistemi")
    ctx.likely_causes.append("DPF partikül filtresi aşırı kurum yükü veya fark basınç sensörü arızası.")
    ctx.steps.append(
        TroubleshootingStep(
            len(ctx.steps) + 1,
            "DPF fark basınç sensörü hortumlarını ve kurum yükünü kontrol edin.",
            "DPF Filtresi & Sensörü",
            "Kolay (Görsel)",
        )
    )
    return matched


def _misfire_matches(d: dict[str, object]) -> bool:
    return str(d.get("code", "")).upper().startswith("P030")


def _misfire_body(ctx: ScenarioContext) -> int:
    count = sum(1 for d in ctx.dtcs if _misfire_matches(d))
    if count == 0:
        return 0
    if ctx.severity != FaultSeverity.CRITICAL_STOP:
        ctx.severity = FaultSeverity.MEDIUM
    ctx.affected.append("Silindir Ateşleme & Enjeksiyon")
    ctx.likely_causes.append("Ateşleme bobini izolasyon kaçağı, buji elektrot aşınması veya enjektör tıkanıklığı.")
    ctx.steps.append(
        TroubleshootingStep(
            len(ctx.steps) + 1,
            "Osilatör ekranında ateşleme bobini sekonder dalga formunu ve krank devir çentiklerini izleyin.",
            "Ateşleme Bobinleri & Bujiler",
            "Orta (Alet Gerekir)",
        )
    )
    # F-02: only narrate a RPM figure when devir was actually measured.
    if ctx.has_rpm:
        ctx.correlations.append(
            f"Motor {ctx.rpm:.0f} RPM devirde silindir teklemesi nedeniyle tork dalgalanması yaşıyor."
        )
    return count


def _turbo_matches(d: dict[str, object]) -> bool:
    return str(d.get("code", "")).upper() in {"P0234", "P0299"} or d.get("spn") == 102


#: KB-mirrored boost limit (bar, absolute) used only when the threshold DB is
#: unavailable. `SPN102` measurement: "Maksimum Guvenlik Limiti: 3.6 Bar".
#: Previously this trigger was a bare 2.5 that contradicted the KB and produced
#: a false-positive overboost on a healthy engine at 2.6 bar (P0-2, finding 4).
_TURBO_CRITICAL_FALLBACK_BAR = 3.6


def _turbo_boost_limit_bar() -> float:
    """Read the overboost trigger from the threshold database (single source).

    The threshold file is derived from the KB measurement text, so this keeps
    the scenario trigger and the guidance from contradicting each other.
    """
    try:
        from src.engine.ai.anomaly_detector import load_thresholds

        bands = load_thresholds().get("TurboBoost") or {}
        if isinstance(bands, dict):
            critical = bands.get("critical_max")
            if isinstance(critical, (int, float)):
                return float(critical)
            for band in bands.get("ranges") or []:
                value = band.get("max")
                if isinstance(value, (int, float)):
                    return float(value)
    except Exception:  # noqa: BLE001 - a threshold fault must not kill analysis
        pass
    return _TURBO_CRITICAL_FALLBACK_BAR


def _turbo_body(ctx: ScenarioContext) -> int:
    turbo_count = sum(1 for d in ctx.dtcs if _turbo_matches(d))
    # A boost reading below the KB safety limit is NOT an overboost, even with
    # no DTC present; at/above the limit it is.
    if turbo_count == 0 and not (ctx.boost_bar > _turbo_boost_limit_bar()):
        return 0
    if ctx.severity != FaultSeverity.CRITICAL_STOP:
        ctx.severity = FaultSeverity.MEDIUM
    ctx.affected.append("Aşırı Doldurma & Turboşarj")
    ctx.likely_causes.append("Wastegate mekanik sıkışması, N75 selenoid arızası veya intercooler hortum kaçağı.")
    ctx.steps.append(
        TroubleshootingStep(
            len(ctx.steps) + 1,
            "Vakum pompası ile wastegate aktüatör kolunun hareketini test edin (0.6 barda tam açılmalıdır).",
            "Wastegate / VGT Aktüatörü",
            "Orta (Alet Gerekir)",
        )
    )
    # F-02: only narrate a boost figure when basınç was actually measured.
    if ctx.has_boost:
        ctx.correlations.append(
            f"Turbo basıncı {ctx.boost_bar:.2f} Bar seviyesinde; hedef basınç aralığından sapma var."
        )
    return turbo_count


def _overheat_matches(d: dict[str, object]) -> bool:
    return str(d.get("code", "")).upper() == "P0115" or d.get("spn") == 110


#: KB-mirrored coolant limits (C) used when the threshold DB is unavailable.
#: `SPN110` measurement: "Uyari (AWL): >103C | Kirmizi Lamba (RSL Derate): >108C".
_COOLANT_WARNING_FALLBACK_C = 103.0
_COOLANT_CRITICAL_FALLBACK_C = 108.0


def _coolant_limits_c() -> tuple[float, float]:
    """Return (warning_max, critical_max) from the threshold database.

    Single source of truth: the DB is derived from the KB measurement text, so
    the scenario trigger and the guidance cannot drift apart.
    """
    warning = _COOLANT_WARNING_FALLBACK_C
    critical = _COOLANT_CRITICAL_FALLBACK_C
    try:
        from src.engine.ai.anomaly_detector import load_thresholds

        bands = load_thresholds().get("EngineCoolantTemp") or {}
        if isinstance(bands, dict):
            w = bands.get("warning_max")
            c = bands.get("critical_max")
            if isinstance(w, (int, float)):
                warning = float(w)
            if isinstance(c, (int, float)):
                critical = float(c)
    except Exception:  # noqa: BLE001
        pass
    if warning > critical:
        warning = critical
    return warning, critical


def _overheat_body(ctx: ScenarioContext) -> int:
    heat_count = sum(1 for d in ctx.dtcs if _overheat_matches(d))
    warning_c, critical_c = _coolant_limits_c()
    if not (ctx.coolant_temp > warning_c or heat_count > 0):
        return 0
    if ctx.coolant_temp > critical_c or any(d.get("spn") == 110 and d.get("fmi") == 0 for d in ctx.dtcs):
        ctx.severity = FaultSeverity.CRITICAL_STOP
    elif ctx.severity != FaultSeverity.CRITICAL_STOP:
        ctx.severity = FaultSeverity.MEDIUM
    ctx.affected.append("Termal Yönetim & Soğutma")
    ctx.likely_causes.append("Termostat kapalı kalması, radyatör fan arızası veya soğutma sıvısı seviye düşüklüğü.")
    ctx.steps.append(
        TroubleshootingStep(
            len(ctx.steps) + 1,
            "Radyatör alt hortumunu kontrol edin; soğuksa termostat açmıyordur.",
            "Termostat & Radyatör Hortumu",
            "Kolay (Görsel)",
        )
    )
    # F-02: only narrate a temperature figure when sıcaklık was actually measured.
    if ctx.has_coolant:
        ctx.correlations.append(
            f"Motor soğutma sıvısı {ctx.coolant_temp:.1f}°C sıcaklıkta; kritik hararet eşiğinde!"
        )
    return heat_count


SCENARIO_RULES: tuple[ScenarioRule, ...] = (
    ScenarioRule(name="oil-pressure", matches=_oil_matches, telemetry_trigger=lambda r, b, t: False, body=_oil_body),
    ScenarioRule(name="ev-hv-safety", matches=_ev_matches, telemetry_trigger=lambda r, b, t: False, body=_ev_body),
    ScenarioRule(name="injector", matches=_injector_matches, telemetry_trigger=lambda r, b, t: False, body=_injector_body),
    ScenarioRule(name="dpf", matches=_dpf_matches, telemetry_trigger=lambda r, b, t: False, body=_dpf_body),
    ScenarioRule(name="misfire", matches=_misfire_matches, telemetry_trigger=lambda r, b, t: False, body=_misfire_body),
    ScenarioRule(name="turbo", matches=_turbo_matches, telemetry_trigger=lambda r, b, t: b > 2.5, body=_turbo_body),
    ScenarioRule(name="overheat", matches=_overheat_matches, telemetry_trigger=lambda r, b, t: t > 103.0, body=_overheat_body),
)

# REVIEW (Tur-27 P1, @tuner AI plan Boguluk 4): bilinen iliskili DTC kumeleri.
# Tek kod yerine KUME olarak degerlendirilirse kok neden daha isabetli cikar
# (orn. NOx cifti = SCR verim kaybi, misfire seti = atesleme/yakit sistemi).
# Uydurma yok: her cift SAE J1939 / SAE J2012 dokumante edilmis iliskidir.
_RELATED_CODE_GROUPS: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"SPN3216", "SPN3226"}), "NOx sensör çifti — SCR verim/kalibrasyon kaybı"),
    (frozenset({"SPN3226", "SPN3364"}), "NOx + SCR sistemi — dozaj/yakıt kalitesi"),
    (frozenset({"SPN3251", "SPN4364"}), "DPF + SCR aftertreatment zinciri"),
    (frozenset({"P0299", "P0401"}), "Turbo düşük basınç + EGR akış yetersizliği (ortak hava yolu)"),
    (frozenset({"P0171", "P0174"}), "Bank 1+2 fakir karışım — vakum kaçağı / MAF"),
    (frozenset({"P0300", "P0301"}), "Rastgele + silindir 1 teklemesi — ateşleme/enjektör"),
    (frozenset({"P0300", "P0302"}), "Rastgele + silindir 2 teklemesi — ateşleme/enjektör"),
    (frozenset({"U0100", "P0620"}), "CAN haberleşme kaybı + alternatör kontrol — besleme/şase"),
    (frozenset({"P0087", "P0088"}), "Yakıt rayı basıncı düşük+yüksek — basınç regülatörü/pompa"),
    (frozenset({"P2002", "P2453"}), "DPF verim + diferansiyel basınç sensörü — tıkanıklık"),
)

# SPN kodu -> "SPN<num>" metnine ceviren yardimci (kume eslesmesi icin)
_CODE_ALIAS_RE = re.compile(r"^SPN[_ ]?(\d+)$", re.I)


class AiDiagnosticCopilot:
    """Intelligent reasoning engine analyzing DTCs, telemetry signals, and ECU health.

    Fully offline (operator decision, M7): no cloud LLM, no API keys, no
    network calls. Severity and action triggers originate here only.
    Same input -> same output, auditable under ISO 26262.
    """

    def __init__(self, **_legacy_kwargs: object) -> None:
        """Legacy bridge/API kwargs (gemini_api_key, openai_api_key, provider,
        secret_provider, require_consent, set_key_provider callers) are
        accepted and silently ignored — the engine is fully offline."""
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()  # fully offline deterministic engine"

    def set_key_provider(self, _secret_provider: object) -> None:
        """Legacy API no-op: no cloud keys exist anymore (fully offline)."""
        pass

    def analyze_session(
        self,
        active_dtcs: list[dict[str, object]],
        telemetry_snapshot: dict[str, float],
        active_ecus: list[str],
        user_consented: bool = False,
    ) -> DiagnosticAnalysisReport:
        """Perform deterministic offline expert analysis.

        ``user_consented`` is accepted (and ignored) for bridge/API backward
        compatibility — no cloud call exists anymore.
        """
        return self._analyze_local_expert(active_dtcs, telemetry_snapshot, active_ecus)

    def _analyze_local_expert(
        self,
        active_dtcs: list[dict[str, object]],
        telemetry_snapshot: dict[str, float],
        active_ecus: list[str],
    ) -> DiagnosticAnalysisReport:
        # M-18 (P2-17): lazy knowledge-base load at first analysis use.
        ensure_external_dtc_database_loaded()
        dtc_count = len(active_dtcs)
        # F-02 (P0, No Fabricated Telemetry): a signal is "measured" ONLY when its
        # key is present in the snapshot. Absent keys keep a neutral numeric
        # default for arithmetic, but MUST NOT be narrated as a reading. The
        # previous code applied `CoolantTemp -> 85.0` unconditionally and printed
        # "motor devri 0 RPM" / "85.0°C" for sessions that carried no telemetry
        # at all, which is exactly the fabrication AGENTS.md §2.3 forbids.
        has_rpm = "EngineSpeed" in telemetry_snapshot
        has_boost = "BoostPressure" in telemetry_snapshot
        has_coolant = "CoolantTemp" in telemetry_snapshot
        rpm = telemetry_snapshot.get("EngineSpeed", 0.0)
        raw_boost = telemetry_snapshot.get("BoostPressure", 0.0)
        # Normalize boost: if > 10, it's in kPa (e.g. 120 kPa = 1.20 Bar)
        boost_bar = raw_boost / 100.0 if raw_boost > 10.0 else raw_boost
        coolant_temp = telemetry_snapshot.get("CoolantTemp", 0.0)
        telemetry_measured = has_rpm or has_boost or has_coolant

        likely_causes: list[str] = []
        steps: list[TroubleshootingStep] = []
        correlations: list[str] = []
        affected: list[str] = []
        severity = FaultSeverity.LOW
        # Evidence counters feeding the honest weighted confidence score (P0).
        scenario_matched_count = 0
        kb_matched_count = 0

        # U4 (plan FAZ 3.3): the 7 scenarios now run from SCENARIO_RULES
        # (module-level ScenarioRule bodies are the VERBATIM original blocks;
        # behavior identical — test_ai_copilot.py is the acceptance lock).
        ctx = ScenarioContext(
            dtcs=active_dtcs,
            rpm=rpm,
            boost_bar=boost_bar,
            coolant_temp=coolant_temp,
            severity=severity,
            likely_causes=likely_causes,
            steps=steps,
            correlations=correlations,
            affected=affected,
            has_rpm=has_rpm,
            has_boost=has_boost,
            has_coolant=has_coolant,
        )
        for rule in SCENARIO_RULES:
            scenario_matched_count += rule.body(ctx)
        severity = ctx.severity


        # Identify DTCs already covered by specific hardcoded scenarios above
        # (U4: derived from the same SCENARIO_RULES matchers — one source of
        # truth for what a "handled" DTC is).
        handled_indices: set[int] = set()
        for idx, d in enumerate(active_dtcs):
            if any(rule.matches(d) for rule in SCENARIO_RULES):
                handled_indices.add(idx)

        # REVIEW (Tur-27 P1, @tuner AI plan Boguluk 4): aktif DTC kumesini iliskili
        # grup tablosuyla karsilastir. Tek tek kodlar yerine KUME olarak
        # degerlendirilince kok neden isabeti artar. Uydurma yok — tablo sabit.
        try:
            def _norm_code(raw: Any) -> str:
                s = str(raw or "").strip().upper()
                m = _CODE_ALIAS_RE.match(s)
                return f"SPN{m.group(1)}" if m else s

            active_set = set()
            for d in active_dtcs:
                active_set.add(_norm_code(d.get("code")))
                if d.get("spn") is not None:
                    active_set.add(f"SPN{d.get('spn')}")
            for grp, desc in _RELATED_CODE_GROUPS:
                if grp <= active_set:
                    line = f"İlişkili kod kümesi [{', '.join(sorted(grp))}]: {desc}"
                    if line not in ctx.correlations:
                        ctx.correlations.append(line)

            # T42 P2-1: DB-turevli iliski kumeleri. Sabit 10'luk tabloya ek olarak
            # aktif kodlarin {subsystem, J1939 associated_pgn} ortakligi kumelenir.
            # Uydurma yok — alan DB'de dolu degilse o boyut atlanir.
            _clusters = analyze_active_dtc_clusters(active_dtcs)
            if len(_clusters["codes"]) >= 2:
                for _sub, _members in _clusters["subsystem_groups"][:5]:
                    line = f"Ortak alt-sistem [{_sub}] ({len(_members)} kod): {', '.join(_members)}"
                    if line not in ctx.correlations:
                        ctx.correlations.append(line)
                for _pgn, _members in _clusters["pgn_groups"][:4]:
                    line = f"Ortak J1939 PGN {_pgn} ({len(_members)} kod): {', '.join(_members)}"
                    if line not in ctx.correlations:
                        ctx.correlations.append(line)
            # T42 P2-6: rezerve (ureticiye ozel) kodlar -> yanlis teshis uyarisi.
            for _rc in _clusters["reserved_codes"][:8]:
                line = (
                    f"⛔ Rezerve kod [{_rc}]: ISO/SAE standart değil, üreticiye özel — "
                    f"OEM kılavuzundan doğrulanmadan teşhis/parça değişimi yapılmamalı."
                )
                if line not in ctx.correlations:
                    ctx.correlations.append(line)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            # A3-4: this block produces the shared-subsystem/PGN correlations
            # and the reserved-code warning. A broad `except Exception: pass`
            # swallowed any failure and the report then claimed "no related
            # cluster / no reserved code" (false negative, fail-open). Narrow
            # the catch set and log so the degradation is observable.
            logger.warning(
                "DTC küme/ilişki zenginleştirmesi atlandı — korelasyon ve rezerve-kod uyarısı eksik olabilir",
                extra={"error": str(exc)},
            )

        # Dynamic EXPERT_KNOWLEDGE_BASE lookup for active DTCs not matched by scenarios 1..7
        for idx, d in enumerate(active_dtcs):
            if idx in handled_indices:
                continue
            code_candidate = str(d.get("code", "")).upper()
            spn_candidate = f"SPN{d.get('spn')}" if d.get("spn") else ""
            match_key = code_candidate if code_candidate in EXPERT_KNOWLEDGE_BASE else (spn_candidate if spn_candidate in EXPERT_KNOWLEDGE_BASE else None)
            # P0-3: a DM1 code arrives as "SPN 100" (with a space) but the KB
            # keys are "SPN100". Without this normalisation the lookup missed
            # and a genuine oil-pressure stop degraded to a generic LOW.
            if match_key is None:
                _spn_norm = _normalize_spn_code(code_candidate)
                if _spn_norm and _spn_norm in EXPERT_KNOWLEDGE_BASE:
                    match_key = _spn_norm
            # REVIEW (Tur-27 P0, @tuner AI plan Boguluk 1): canli DM1 akisindan gelen
            # SPN'ler EXPERT_KB'de yoksa 4.253'luk J1939 DB'sine dus. Onceden bu yol
            # atlanip jenerik fallback'e gidiyordu; sorgu yolu ile oturum yolu asimetrikti.
            j1939_entry = None
            if match_key is None and d.get("spn"):
                try:
                    _jdb = get_j1939_spn_database()
                    spn_raw = d.get("spn")
                    spn_val = int(str(spn_raw)) if spn_raw is not None else 0
                    j1939_entry = (_jdb.get("spns") or {}).get(f"SPN_{spn_val}")
                except Exception:
                    j1939_entry = None
            if j1939_entry is not None:
                kb_matched_count += 1
                subsys = j1939_entry.get("subsystem") or "J1939 Ağır Vasıta"
                if subsys not in affected:
                    affected.append(subsys)
                # FMI'ya ozgu fault_matrix satirindan neden/adim sentezle
                fm = j1939_entry.get("fault_matrix") or {}
                fmi_row = fm.get(str(d.get("fmi"))) if d.get("fmi") is not None else None
                fmi_defs = (get_j1939_spn_database().get("fmi_definitions") or {})
                fmi_row = fmi_row or fmi_defs.get(str(d.get("fmi"))) or {}
                name = j1939_entry.get("title_tr") or j1939_entry.get("name") or f"SPN {d.get('spn')}"
                fault_title = fmi_row.get("fault_title") or fmi_row.get("name") or ""
                diag_action = fmi_row.get("diagnostic_action") or ""
                synth_cause = f"{name}" + (f" — {fault_title}" if fault_title else "")
                if synth_cause not in likely_causes:
                    likely_causes.append(synth_cause)
                # T41 P1-2: J1939 girdisindeki DOLU `causes`/`steps` alanlari
                # oturum yolunda HIC okunmuyordu (yalniz DTC DB tarafi okunuyordu).
                # Deterministik: liste sirasi korunur, dedupe edilir, uydurma yok.
                _j_causes = j1939_entry.get("causes")
                if isinstance(_j_causes, list):
                    for _c in _j_causes[:3]:
                        _cs = str(_c).strip()
                        if _cs and _cs not in likely_causes:
                            likely_causes.append(_cs[:200])
                if len(steps) < 5:
                    act = diag_action or f"{name} devresini/verisini kontrol edin"
                    steps.append(TroubleshootingStep(len(steps) + 1, act[:200], f"SPN {d.get('spn')}", "Orta (Alet Gerekir)"))
                _j_steps = j1939_entry.get("steps")
                if isinstance(_j_steps, list):
                    for _s in _j_steps:
                        if len(steps) >= 5:
                            break
                        _ss = str(_s).strip()
                        if _ss:
                            steps.append(TroubleshootingStep(len(steps) + 1, _ss[:200], f"SPN {d.get('spn')}", "Orta (Alet Gerekir)"))
                # P0-3 (2026-09-21 audit, finding 7): the per-FMI severity was
                # READ here but only the literal "CRITICAL_STOP" was honoured, so
                # SPN 100 FMI 3 (unplugged sensor -> HIGH in the SPN DB) was
                # rendered identical to FMI 1 (real oil-pressure loss ->
                # CRITICAL_STOP). Lowercase scraped rungs ("critical"/"medium")
                # were dropped as well. Resolve the FULL rung via the J1939
                # resolver, which normalises case and the database key form.
                fmi_sev = _resolve_fmi_severity(str(d.get("code") or ""), d.get("fmi"))
                if fmi_sev is None:
                    fmi_sev = _resolve_fmi_severity(f"SPN {d.get('spn')}", d.get("fmi"))
                if fmi_sev is not None:
                    severity = _raise_severity(severity, FaultSeverity(fmi_sev.value))
            if match_key:
                kb_matched_count += 1
                info = EXPERT_KNOWLEDGE_BASE[match_key]
                subsys = info.get("subsystem", "Genel Teşhis")
                if subsys not in affected:
                    affected.append(subsys)
                for cause in info.get("causes", []):
                    if cause not in likely_causes:
                        likely_causes.append(cause)
                for s in info.get("steps", []):
                    if len(steps) < 5:
                        act = s[0] if len(s) > 0 else "İnceleme yapın"
                        target_comp = s[1] if len(s) > 1 else "İlgili Komponent"
                        diff = s[2] if len(s) > 2 else "Orta (Alet Gerekir)"
                        steps.append(TroubleshootingStep(len(steps) + 1, act, target_comp, diff))
                # P0-3 (cont.): the KB stores ONE rung per SPN ("SPN100" ->
                # CRITICAL_STOP), but the SPN database distinguishes the FMI.
                # A per-FMI rung is the MORE SPECIFIC evidence, so it wins:
                # SPN 100 FMI 3 (unplugged sensor -> HIGH) must not be escalated
                # to "stop the engine" by the SPN-level rung, while FMI 1 (real
                # pressure loss -> CRITICAL_STOP) still must be.
                _kb_fmi_sev = _resolve_fmi_severity(match_key, d.get("fmi"))
                if _kb_fmi_sev is not None:
                    severity = _raise_severity(severity, FaultSeverity(_kb_fmi_sev.value))
                else:
                    sev_str = info.get("severity", "MEDIUM")
                    if sev_str == "CRITICAL_STOP":
                        severity = _raise_severity(severity, FaultSeverity.CRITICAL_STOP)
                    elif sev_str == "HIGH":
                        severity = _raise_severity(severity, FaultSeverity.HIGH)
                    elif sev_str == "MEDIUM":
                        severity = _raise_severity(severity, FaultSeverity.MEDIUM)


        # FAZ 4 wiring (AI plan Faz 3): rank graph hypotheses from the SAME
        # active-DTC evidence and surface the top-1 as a root-cause CANDIDATE.
        #
        # Kanıt C (P0, uncalibrated confidence inflation): this line used to be
        # appended into ``correlations``, which is fed to
        # ``compute_root_cause_confidence(telemetry_correlation_count=...)``.
        # That made the engine count ITS OWN hypothesis as independent
        # telemetry corroboration, doubling the evidence and pushing the score
        # from ~%60 to ~%90 with zero measured signals. The hypothesis is now
        # kept in a SEPARATE list and is explicitly excluded from the
        # telemetry-correlation count below. A hypothesis is a conclusion, not
        # evidence, and can never corroborate itself.
        hypothesis_candidates: list[str] = []
        try:
            from src.core.models.diagnostics import DiagnosticDomain, DiagnosticEvent, VehicleSession
            from src.engine.ai.hypothesis_engine import rank_hypotheses

            _evts: list[DiagnosticEvent] = []
            for d in active_dtcs:
                _code = str(d.get("code") or "").strip()
                if not _code and d.get("spn") is not None:
                    _code = f"SPN {d.get('spn')}" + (
                        f" FMI {d.get('fmi')}" if d.get("fmi") is not None else ""
                    )
                if not _code:
                    continue
                _evts.append(
                    DiagnosticEvent(
                        timestamp_ns=time.time_ns(),
                        code=_code,
                        domain=(
                            DiagnosticDomain.HEAVY_DUTY
                            if d.get("spn") is not None
                            else DiagnosticDomain.PASSENGER
                        ),
                        severity="MEDIUM",
                        status="ACTIVE",
                    )
                )
            if _evts:
                _mini = VehicleSession(
                    session_id="local-expert",
                    started_at_ns=time.time_ns(),
                    domain=(
                        DiagnosticDomain.HEAVY_DUTY
                        if any(d.get("spn") is not None for d in active_dtcs)
                        else DiagnosticDomain.PASSENGER
                    ),
                    events=_evts,
                )
                _hyps = rank_hypotheses(_mini, [], None)
                if _hyps:
                    # T67-B (exhaustive answers): only rank 1 was surfaced, so a
                    # technician saw a single root-cause candidate and never the
                    # competing ones the engine had already ranked and scored.
                    # Every ranked hypothesis is now listed with its evidence
                    # score, strongest first, plus the score gap to the next
                    # candidate — a narrow gap is exactly the signal that the
                    # diagnosis is not settled and further tests are needed.
                    _top = _hyps[0]
                    for _rank, _h in enumerate(_hyps):
                        _line = (
                            f"🎯 Kök neden adayı #{_rank + 1} [{_h.id}]: {_h.fault} "
                            f"(kanıt skoru %{_h.score * 100:.0f})"
                        )
                        if _rank == 0 and len(_hyps) > 1:
                            _gap = _top.score - _hyps[1].score
                            if _gap < 0.20:
                                _line += (
                                    f" — ⚠️ ikinci adayla fark yalnız %{_gap * 100:.0f}; "
                                    "ayırt edici test yapılmadan kesin onarım önerilmez"
                                )
                        if _line not in hypothesis_candidates:
                            hypothesis_candidates.append(_line)
        except Exception as exc:  # noqa: BLE001 — hypothesis layer must never break the expert report
            logger.warning(
                "Hipotez sıralama atlandı — kök neden adayı üretilmedi",
                extra={"error": str(exc)},
            )

        # Default fallback if no specific rule matched
        if not likely_causes:

            if dtc_count > 0:
                likely_causes.append("CAN veri yolunda aktif diagnostik hata kodları kaydedildi.")
                steps.append(
                    TroubleshootingStep(
                        1,
                        "Hata kodlarının detaylarını ve freeze frame verilerini UDS 0x19 servisi ile sorgulayın.",
                        "Elektronik Kontrol Üniteleri (ECU)",
                        "Kolay (Görsel)",
                    )
                )
            else:
                # F-02: an empty session has NO health evidence. Saying
                # "telemetri sinyalleri nominal aralıkta çalışıyor" asserts a
                # measurement that never happened. Claim health only when a
                # signal was actually measured, else state the data gap.
                if telemetry_measured:
                    likely_causes.append("Aktif hata tespit edilmedi. Telemetri sinyalleri nominal aralıkta çalışıyor.")
                else:
                    likely_causes.append("Ölçülmüş telemetri verisi ve aktif arıza kodu yok — sağlık beyanı üretilemez (veri yok).")
                steps.append(
                    TroubleshootingStep(
                        1,
                        "Rutin periyodik bakım ve CAN sinyal osiloskop kontrollerini sürdürün.",
                        "Genel Araç Sistemi",
                        "Kolay (Görsel)",
                    )
                )

        # F-08 (P1): `active_ecus` was a dead parameter — accepted, passed
        # through to this method, and never read (verified by AST: zero Load
        # references in the body). A caller could therefore never influence the
        # report through it, and the signature promised information the engine
        # ignored. It now has one narrow, non-fabricating use: when the active
        # DTC set contains U-codes (network/ECU communication faults), the
        # ECUs the caller actually reported are surfaced as affected
        # subsystems. Only names the caller supplied are echoed — never a
        # guessed or synthesized ECU name (AGENTS.md §2.3).
        if active_ecus and any(
            str(d.get("code", "")).strip().upper().startswith("U") for d in active_dtcs
        ):
            for ecu in active_ecus:
                if ecu and ecu not in affected:
                    affected.append(ecu)

        if dtc_count == 0 and not affected:
            # F-02: distinguish "measured and healthy" from "no data at all".
            if telemetry_measured:
                summary = (
                    f"Çevrimdışı AI Analizi: Nominal durum: Aktif arıza kodu tespit edilmedi. "
                    f"Telemetri sinyalleri nominal aralıkta çalışıyor. Sistem Durumu: {severity.value}."
                )
            else:
                summary = (
                    "Çevrimdışı AI Analizi: Aktif arıza kodu ve ölçülmüş telemetri verisi yok. "
                    f"Hüküm verilemedi (veri yok). Sistem Durumu: {severity.value}."
                )
        else:
            summary = (
                f"Çevrimdışı AI Analizi: Toplam {dtc_count} aktif arıza kodu tespit edildi. "
                f"Sistem Durumu: {severity.value}. Ana etki alanı: {', '.join(affected) if affected else 'Genel Sistem'}."
            )

        # T70-D: compute the confidence ONCE as a breakdown and read the label
        # off it, so the label the operator sees and the rendered components can
        # never disagree (they are the same numbers).
        _conf_breakdown = root_cause_confidence_breakdown(
            dtc_count=dtc_count,
            scenario_matched=scenario_matched_count,
            kb_matched=kb_matched_count,
            # Kanıt C: `hypothesis_candidates` is deliberately NOT counted —
            # only genuine signal-derived correlations may corroborate.
            telemetry_correlation_count=len(correlations),
            # T1-1 (calibration wiring): the parameter existed but NO
            # production call site ever passed it, so the engine kept
            # reporting ~%68.5 mean confidence against a measured %44.4
            # golden-set top-1 accuracy (overconfidence_detected=True).
            # The factor is COMPUTED from the verified golden corpus and
            # memoised per process (see calibration.compute_calibration_factor)
            # — never a hardcoded magic number. T2-3: a ``None`` result is
            # no longer "no damping"; ``_resolve_calibration_factor``
            # resolves it internally and, when no evidence exists at all,
            # marks the label KALİBRE EDİLMEDİ instead of granting full
            # confidence.
            calibration_factor=compute_calibration_factor(),
        )

        return DiagnosticAnalysisReport(
            summary=summary,
            severity=severity,
            root_cause_probability=str(_conf_breakdown["label"]),
            confidence_breakdown=_conf_breakdown,
            likely_causes=likely_causes,
            troubleshooting_steps=steps,
            affected_subsystems=affected if affected else ["CAN Veri Yolu & Genel Telemetri"],
            raw_dtc_count=dtc_count,
            telemetry_correlations=correlations,
            # P1-1: a hypothesis is a conclusion, never telemetry evidence —
            # it travels in its own field.
            hypothesis_candidates=hypothesis_candidates,
            ai_model_used="Yerel Otomotiv Uzman Motoru (Çevrimdışı)",
        )

    def analyze_live_telemetry(
        self,
        rpm: float | None,
        boost_bar: float | None,
        coolant_temp: float | None,
        dtc_codes: list[str],
        user_prompt: str,
        bus_metrics: dict[str, Any] | None = None,
        user_consented: bool = False,
    ) -> str:
        """Helper for live interactive prompt query with deep reasoning (fully offline).

        ``user_consented`` accepted (and ignored) for bridge/API backward
        compatibility. Action triggers derive from the OPERATOR prompt only.

        P0-5 (AGENTS.md §2.3): the three measurement parameters are
        ``float | None`` and a ``None`` means "this channel was NOT measured".
        The previous signature required ``float``, so a bridge that passed
        ``0.0`` for an absent channel injected a FABRICATED measured zero into
        the inference engine. Only channels that actually carry a value are
        inserted; an unmeasured channel stays ABSENT (the engine already
        renders absent as "veri yok", never as a reading).
        """
        # M-18 (P2-17): lazy knowledge-base load at first analysis use.
        ensure_external_dtc_database_loaded()
        telemetry: dict[str, Any] = dict(bus_metrics or {})
        if rpm is not None:
            telemetry["EngineSpeed"] = rpm
        if boost_bar is not None:
            telemetry["BoostPressure"] = boost_bar
        if coolant_temp is not None:
            telemetry["CoolantTemp"] = coolant_temp
        active_dtc_objs = [{"code": c} for c in dtc_codes]
        return CausalBayesianInferenceEngine.evaluate_diagnostic_query(user_prompt, active_dtc_objs, telemetry)

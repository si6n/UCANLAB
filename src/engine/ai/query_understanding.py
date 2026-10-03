"""Input understanding for the offline copilot (free text + codes + telemetry + DM1).

One function, :func:`parse_query`, turns everything a technician can give the
copilot into one deterministic :class:`ParsedQuery`:

* free-text complaint in Turkish or English, typo-tolerant
  ("motor ısınıyor, DPF lambası yandı" / "engine overheting");
* OBD/UDS DTCs written anywhere in the text or passed explicitly (``P0101``,
  ``p 0101``, ``PO101``);
* J1939 SPN/FMI in any common spelling (``SPN 110 FMI 0``, ``spn110/0``);
* PGN and OBD PID mentions;
* raw DM1 payloads (PGN 65226, already re-assembled) or decoded DM1 dicts;
* live telemetry as a dict, plus measurements typed in the text
  ("su sıcaklığı 112 derece", "battery 11.8 V").

Honesty rules (AGENTS.md §2.3):
* A value is a *reading* only if the user/bus supplied it. Nothing here ever
  creates a number. A value written without a unit keeps ``unit_assumed=True``
  so the answer can say which unit was assumed.
* Unrecognised telemetry keys are reported back (``unknown_telemetry``), never
  silently mapped to a guess.

Pure functions, standard library + the knowledge layer only.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.engine.ai.knowledge_base import KnowledgeBase, fold_text, get_knowledge_base, normalize_dtc_code

__all__ = [
    "CheckAnswer",
    "CodeMention",
    "Dm1Lamps",
    "ParsedQuery",
    "Reading",
    "SpnMention",
    "SymptomMatch",
    "decode_dm1_payload",
    "detect_language",
    "parse_query",
]


@dataclass(frozen=True, slots=True)
class CodeMention:
    code: str
    origin: str  # text | input | dm1
    raw: str = ""
    status: str = "ACTIVE"  # ACTIVE | HISTORY (stored, not present now) | PENDING (seen once, not confirmed)


@dataclass(frozen=True, slots=True)
class SpnMention:
    spn: int
    fmi: int | None
    origin: str  # text | input | dm1
    occurrence_count: int | None = None
    note: str = ""
    status: str = "ACTIVE"

    @property
    def key(self) -> str:
        return f"SPN {self.spn}" + (f" FMI {self.fmi}" if self.fmi is not None else "")


@dataclass(frozen=True, slots=True)
class SymptomMatch:
    symptom_id: str
    phrase: str
    score: float
    fuzzy: bool
    origin: str  # canonical_symptoms | symptom_lexicon


@dataclass(frozen=True, slots=True)
class Reading:
    canonical: str
    value: float
    unit: str
    raw_name: str
    origin: str  # input | text | text_context (number tied to a symptom's gauge)
    unit_assumed: bool = False
    raw_value: float | None = None
    raw_unit: str = ""


@dataclass(frozen=True, slots=True)
class CheckAnswer:
    """Answer to one curated symptom check (``symptom_checks.json``)."""

    symptom_id: str
    check_id: str
    value: str | float  # "yes" | "no" | "unknown" | measured number
    origin: str  # input | text

    @property
    def key(self) -> str:
        return f"{self.symptom_id}.{self.check_id}"


@dataclass(frozen=True, slots=True)
class Dm1Lamps:
    mil: bool
    red_stop: bool
    amber_warning: bool
    protect: bool


@dataclass(slots=True)
class ParsedQuery:
    text: str
    language: str
    dtcs: list[CodeMention] = field(default_factory=list)
    spns: list[SpnMention] = field(default_factory=list)
    pgns: list[int] = field(default_factory=list)
    pids: list[str] = field(default_factory=list)
    symptoms: list[SymptomMatch] = field(default_factory=list)
    readings: list[Reading] = field(default_factory=list)
    unknown_telemetry: list[str] = field(default_factory=list)
    dm1_lamps: list[Dm1Lamps] = field(default_factory=list)
    safety_terms: dict[str, list[str]] = field(default_factory=dict)
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    corrections: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    answers: list[CheckAnswer] = field(default_factory=list)
    # Mode 02 freeze frame: the conditions when ``freeze_dtc`` was stored (origin "freeze_frame")
    freeze_dtc: str = ""
    freeze_readings: list[Reading] = field(default_factory=list)
    # Mode 06 monitor results: the ECU's own test verdicts with its own limits
    monitors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.dtcs or self.spns or self.symptoms or self.readings or self.pgns or self.pids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "dtcs": [{"code": c.code, "origin": c.origin, "status": c.status} for c in self.dtcs],
            "spns": [{"spn": s.spn, "fmi": s.fmi, "origin": s.origin, "oc": s.occurrence_count, "status": s.status}
                     for s in self.spns],
            "pgns": list(self.pgns),
            "pids": list(self.pids),
            "symptoms": [{"id": s.symptom_id, "phrase": s.phrase, "fuzzy": s.fuzzy} for s in self.symptoms],
            "readings": [
                {"signal": r.canonical, "value": r.value, "unit": r.unit, "origin": r.origin,
                 "unit_assumed": r.unit_assumed}
                for r in self.readings
            ],
            "unknown_telemetry": list(self.unknown_telemetry),
            "safety_terms": dict(self.safety_terms),
            "vehicle_make": self.vehicle_make,
            "corrections": [list(c) for c in self.corrections],
            "notes": list(self.notes),
            "answers": [{"key": a.key, "value": a.value, "origin": a.origin} for a in self.answers],
            "freeze_frame": ({"dtc": self.freeze_dtc, "readings": [
                {"signal": r.canonical, "value": r.value, "unit": r.unit} for r in self.freeze_readings]}
                if self.freeze_dtc else None),
            "monitors": len(self.monitors),
        }


# ---------------------------------------------------------------- language
_TR_CHARS = set("ğüşıöçĞÜŞİÖÇ")
_TR_WORDS = frozenset({
    "ve", "bir", "bu", "ne", "mi", "mu", "var", "yok", "icin", "ile", "cok", "motor", "araba", "arac",
    "lamba", "lambasi", "yandi", "yaniyor", "calismiyor", "ariza", "sicaklik", "hararet", "isiniyor",
    "yapiyor", "neden", "nasil", "kod", "kodu", "duman", "fren", "aku", "basinci", "geliyor", "gitmiyor",
})
_EN_WORDS = frozenset({
    "the", "and", "is", "my", "engine", "car", "truck", "light", "on", "not", "what", "why", "how",
    "code", "warning", "start", "smoke", "brake", "battery", "pressure", "high", "low", "with", "after",
})


def detect_language(text: str, default: str = "tr") -> str:
    """``"tr"`` or ``"en"`` by Turkish letters and function-word votes."""
    if any(ch in _TR_CHARS for ch in text):
        return "tr"
    tokens = fold_text(text).split()
    tr = sum(1 for t in tokens if t in _TR_WORDS or t.endswith(("iyor", "miyor", "ndi", "lari", "leri")))
    en = sum(1 for t in tokens if t in _EN_WORDS)
    if tr == en:
        return default
    return "tr" if tr > en else "en"


# ------------------------------------------------------------- code parsing
_DTC_TEXT_RE = re.compile(r"(?<![0-9A-Za-z])([PBCUpbcu])[\s\-]?([0-9A-Fa-fOo]{4})(?![0-9A-Za-z])")
_SPN_TEXT_RE = re.compile(
    r"\bspn\s*[:#=]?\s*(\d{1,6})"
    r"(?:\s*(?:[/,\-]|\s)\s*(?:fmi\s*[:#=]?\s*)?(\d{1,2})(?!\d))?",
    re.IGNORECASE,
)
_FMI_ONLY_RE = re.compile(r"\bfmi\s*[:#=]?\s*(\d{1,2})\b", re.IGNORECASE)
_PGN_TEXT_RE = re.compile(r"\bpgn\s*[:#=]?\s*(0x[0-9a-f]{1,5}|\d{1,6})\b", re.IGNORECASE)
_PID_TEXT_RE = re.compile(r"\b(?:pid\s*[:#=]?\s*(?:0x)?([0-9a-f]{2})|01([0-9a-f]{2})\b(?=.*\bpid\b))", re.IGNORECASE)


def _dtc_mentions(text: str) -> list[CodeMention]:
    out: list[CodeMention] = []
    seen: set[str] = set()
    for m in _DTC_TEXT_RE.finditer(text):
        body = m.group(2)
        # A letter-O typo is only accepted when the rest is clearly numeric
        # ("PO101"), never for words like "BOOOO".
        if any(ch in "oO" for ch in body) and sum(ch.isdigit() for ch in body) < 2:
            continue
        code = normalize_dtc_code(m.group(1) + body)
        if code and code not in seen:
            seen.add(code)
            out.append(CodeMention(code, "text", m.group(0)))
    return out


def _spn_mentions(text: str) -> tuple[list[SpnMention], list[str]]:
    out: list[SpnMention] = []
    notes: list[str] = []
    for m in _SPN_TEXT_RE.finditer(text):
        spn = int(m.group(1))
        fmi = int(m.group(2)) if m.group(2) is not None else None
        if spn > 524287:
            notes.append(f"invalid_spn:{spn}")
            continue
        if fmi is not None and fmi > 31:
            notes.append(f"invalid_fmi:{fmi}")
            fmi = None
        if fmi is None:
            tail = text[m.end(): m.end() + 24]
            fm = _FMI_ONLY_RE.search(tail)
            if fm and int(fm.group(1)) <= 31:
                fmi = int(fm.group(1))
        out.append(SpnMention(spn, fmi, "text"))
    return out, notes


# ---------------------------------------------------------------- DM1 decode
def _payload_bytes(payload: Any) -> bytes | None:
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, (list, tuple)) and all(isinstance(b, int) and 0 <= b <= 255 for b in payload):
        return bytes(payload)
    if isinstance(payload, str):
        cleaned = re.sub(r"(0x|[\s,:\-])", "", payload, flags=re.IGNORECASE)
        if cleaned and len(cleaned) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", cleaned):
            return bytes.fromhex(cleaned)
    return None


def decode_dm1_payload(payload: Any) -> tuple[Dm1Lamps | None, list[SpnMention], list[str]]:
    """Decode one J1939-73 DM1 (PGN 65226) data field.

    Layout (SAE J1939-73 §5.7.1, canboat ``065226-activeTroubleCodes``):
    byte 0 lamp status (MIL/RSL/AWL/PL, 2 bits each, ``01`` = on), byte 1
    flash bits, then 4 bytes per DTC: SPN low 16 bits, SPN high 3 bits + FMI,
    CM bit + occurrence count. SPN 0 / all-ones entries mean "no DTC".
    Conversion method 1 (legacy) layouts are flagged, not re-interpreted.
    """
    raw = _payload_bytes(payload)
    if raw is None or len(raw) < 6:
        return None, [], ["dm1_unreadable_payload"]
    lamp = raw[0]
    lamps = Dm1Lamps(
        mil=((lamp >> 6) & 0x3) == 1,
        red_stop=((lamp >> 4) & 0x3) == 1,
        amber_warning=((lamp >> 2) & 0x3) == 1,
        protect=(lamp & 0x3) == 1,
    )
    out: list[SpnMention] = []
    notes: list[str] = []
    for i in range(2, len(raw) - 3, 4):
        b2, b3, b4, b5 = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        spn = b2 | (b3 << 8) | ((b4 & 0xE0) << 11)
        fmi = b4 & 0x1F
        cm = (b5 >> 7) & 0x1
        oc = b5 & 0x7F
        if spn == 0 or spn == 0x7FFFF:
            continue
        note = "dm1_conversion_method_1_legacy" if cm == 1 else ""
        if note:
            notes.append(f"{note}:SPN{spn}")
        out.append(SpnMention(spn, fmi, "dm1", oc if oc != 0x7F else None, note))
    return lamps, out, notes


# ----------------------------------------------------------- fuzzy matching
def _damerau(a: str, b: str, limit: int) -> int:
    """Optimal-string-alignment distance with an early exit above ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


# Turkish verb negation (folded): "donmuyor", "calismiyordu", "basmaz", "almadi".
# One edit turns "donuyor" (turns) into "donmuyor" (does not turn), so a typo
# match must never cross the negation boundary.
# Tolerates a typo inside the suffix itself ("basmyor", "calismiyo").
_TR_NEGATION_RE = re.compile(r"(?:m[iu]?yor?|m[ae]z|m[ae]d[iu])(?:du|lar|um|sun)?$")


def _same_polarity(a: str, b: str) -> bool:
    return bool(_TR_NEGATION_RE.search(a)) == bool(_TR_NEGATION_RE.search(b))


def _token_match(phrase_tok: str, query_tokens: list[str]) -> tuple[str | None, bool]:
    """Best query token for one phrase token -> (token, was_fuzzy)."""
    n = len(phrase_tok)
    if phrase_tok in query_tokens:
        return phrase_tok, False
    if n < 4:
        return None, False  # short words must match exactly ("def", "ure", "abs")
    for qt in query_tokens:
        # Turkish/English inflection: "hararet"->"hararetli", "calismiyor"->"calismiyordu"
        if len(qt) >= n and qt.startswith(phrase_tok):
            return qt, False
        if n >= 6 and len(qt) >= 5 and phrase_tok.startswith(qt) and n - len(qt) <= 3 and _same_polarity(phrase_tok, qt):
            return qt, False
    limit = 2 if n >= 9 else 1 if n >= 5 else 0
    if limit == 0:
        return None, False
    for qt in query_tokens:
        if not _same_polarity(phrase_tok, qt):
            continue
        if len(qt) >= 4 and _damerau(phrase_tok, qt, limit) <= limit:
            return qt, True
        # typo + inflection: compare the stem of the query token
        if len(qt) > n and _damerau(phrase_tok, qt[: n], limit) <= limit:
            return qt, True
    return None, False


def _match_symptoms(folded: str, kb: KnowledgeBase) -> tuple[list[SymptomMatch], list[tuple[str, str]]]:
    tokens = folded.split()
    if not tokens:
        return [], []
    best: dict[str, SymptomMatch] = {}
    fixes: dict[str, list[tuple[str, str]]] = {}
    used: dict[str, frozenset[str]] = {}  # query tokens each symptom's best phrase consumed
    for phrase, sid, origin in kb.symptom_phrases():
        p_tokens = phrase.split()
        if len(p_tokens) > len(tokens) + 1:
            continue
        matched: list[tuple[str, bool]] = []
        for pt in p_tokens:
            qt, fuzzy = _token_match(pt, tokens)
            if qt is None:
                break
            matched.append((qt, fuzzy))
        else:
            fuzzy_any = any(f for _, f in matched)
            score = len(p_tokens) + (0.5 if not fuzzy_any else 0.0)
            prev = best.get(sid)
            if prev is None or score > prev.score:
                best[sid] = SymptomMatch(sid, phrase, score, fuzzy_any, origin)
                fixes[sid] = [(qt, pt) for (qt, f), pt in zip(matched, p_tokens, strict=False) if f]
                used[sid] = frozenset(qt for qt, _ in matched)
    # A phrase whose words all sit inside a longer matched phrase of another
    # symptom is explained by that phrase: "akü şarj olmuyor" is the 12 V
    # battery, not the EV charge port that "şarj olmuyor" alone would name.
    best = {sid: m for sid, m in best.items()
            if not any(used[sid] < used[o] and best[o].score > m.score for o in best if o != sid)}
    ranked = sorted(best.values(), key=lambda s: (-s.score, s.symptom_id))
    if ranked:
        top = ranked[0].score
        # Keep only matches comparable to the strongest one: a single generic
        # word ("rolanti") must not pull in a dozen weak symptoms next to a
        # precise multi-word phrase.
        ranked = [s for s in ranked if s.score >= max(1.0, top - 1.5)][:4]
    corrections: list[tuple[str, str]] = []
    for s in ranked:
        for fix in fixes.get(s.symptom_id, []):
            if fix not in corrections:
                corrections.append(fix)
    return ranked, corrections


_EXHAUST_WORDS = ("egzoz", "exhaust", "tailpipe")
_SMOKE_WORDS = ("duman", "smoke")


def _safety_terms(folded: str, kb: KnowledgeBase) -> dict[str, list[str]]:
    lex = kb._json_source("symptom_lexicon")  # noqa: SLF001 — same package
    terms = (lex or {}).get("safety_terms") if isinstance(lex, dict) else None
    out: dict[str, list[str]] = {}
    if not isinstance(terms, dict):
        return out
    padded = f" {folded} "
    exhaust = any(f" {w}" in padded for w in _EXHAUST_WORDS)
    for category, spec in terms.items():
        if category.startswith("_") or not isinstance(spec, dict):
            continue
        for word in list(spec.get("tr") or []) + list(spec.get("en") or []):
            w = fold_text(str(word))
            if category == "fire" and exhaust and any(t in w for t in _SMOKE_WORDS):
                continue  # smoke from the exhaust is an engine symptom, not a fire
            hit = f" {w} " in padded or (len(w) >= 6 and f" {w}" in padded)
            if w and hit and w not in out.get(category, []):
                out.setdefault(category, []).append(w)
    return out


# --------------------------------------------------------- telemetry parsing
_UNIT_ALIASES: dict[str, str] = {
    "c": "°C", "°c": "°C", "derece": "°C", "degc": "°C", "f": "°F", "°f": "°F",
    "bar": "bar", "kpa": "kPa", "psi": "psi", "mbar": "mbar",
    "v": "V", "volt": "V", "vdc": "V", "rpm": "rpm", "d/dk": "rpm", "dev/dk": "rpm",
    "km/h": "km/h", "kmh": "km/h", "km/s": "km/h", "%": "%", "yuzde": "%", "percent": "%",
    "kohm": "kΩ", "kω": "kΩ", "k": "kΩ", "mohm": "MΩ", "mω": "MΩ", "ohm": "Ω", "ω": "Ω",
    "g/s": "g/s", "a": "A", "amp": "A", "mv": "mV",
}
_NUM_UNIT_RE = re.compile(
    r"^\s*(?:[:=]|is|=|degeri|olarak|su an|şu an)?\s*(-?\d+(?:[.,]\d+)?)\s*"
    r"(°c|°f|derece|degc|c|f|mbar|bar|kpa|psi|vdc|volt|v|rpm|d/dk|dev/dk|km/h|kmh|%|yuzde|percent|"
    r"kohm|kω|mohm|mω|ohm|ω|g/s|mv|amp|a|k)?(?![a-z0-9])"
)

_CONVERT: dict[tuple[str, str], Any] = {
    ("°F", "°C"): lambda v: (v - 32.0) * 5.0 / 9.0,
    ("kPa", "bar"): lambda v: v / 100.0,
    ("psi", "bar"): lambda v: v * 0.0689476,
    ("mbar", "bar"): lambda v: v / 1000.0,
    ("bar", "kPa"): lambda v: v * 100.0,
    ("psi", "kPa"): lambda v: v * 6.89476,
    ("MΩ", "kΩ"): lambda v: v * 1000.0,
    ("Ω", "kΩ"): lambda v: v / 1000.0,
    ("mV", "V"): lambda v: v / 1000.0,
}


def _light_fold(text: str) -> str:
    """Turkish-fold + lower-case but keep digits, decimal marks and units."""
    t = str(text).translate(str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")).lower()
    return re.sub(r"[^0-9a-z.,%°/ωΩ\s:=\-]", " ", t)


def _to_unit(value: float, unit: str, target: str) -> float | None:
    if not target or unit == target:
        return value
    fn = _CONVERT.get((unit, target))
    return float(fn(value)) if fn else None


def _canonical_unit(kb: KnowledgeBase, canonical: str) -> str:
    rec = kb.signal_measurement(canonical).record
    return str(rec.get("unit") or "") if isinstance(rec, dict) else ""


def _make_reading(kb: KnowledgeBase, canonical: str, raw_name: str, value: float, unit: str,
                  origin: str) -> Reading | None:
    target = _canonical_unit(kb, canonical)
    if not unit:
        return Reading(canonical, value, target, raw_name, origin, unit_assumed=bool(target),
                       raw_value=value, raw_unit="")
    converted = _to_unit(value, unit, target) if target else value
    if converted is None:
        return None  # incompatible unit: never coerce
    return Reading(canonical, round(converted, 4), target or unit, raw_name, origin, False, value, unit)


def _text_readings(text: str, kb: KnowledgeBase) -> list[Reading]:
    light = _light_fold(text)
    out: list[Reading] = []
    taken: set[str] = set()
    for phrase, canonical, _unit in kb.signal_phrases():
        if canonical in taken:
            continue
        for m in re.finditer(rf"(?<![a-z]){re.escape(phrase)}[a-z]*", light):
            nm = _NUM_UNIT_RE.match(light[m.end(): m.end() + 32])
            if not nm:
                continue
            value = float(nm.group(1).replace(",", "."))
            unit = _UNIT_ALIASES.get(nm.group(2) or "", "")
            reading = _make_reading(kb, canonical, phrase, value, unit, "text")
            if reading is not None:
                out.append(reading)
                taken.add(canonical)
                break
    return out


# A complaint names the gauge, not the signal: "motor hararet yapıyor,
# göstergede 112 derece". When a matched symptom is about exactly one measurable
# signal and the text holds exactly one number whose EXPLICIT unit converts to
# that signal's unit, the number is that signal's reading. Unit-less numbers,
# incompatible units and ambiguity (two candidate numbers) are never attached.
_SYMPTOM_SIGNAL: dict[str, str] = {
    "engine-overheating": "CoolantTemp",
    "coolant-thermostat-stuck-open": "CoolantTemp",
    "low-oil-pressure": "EngineOilPressure",
    "engine-oil-temperature-high": "EngineOilTemp",
    "battery-drain-parasitic": "BatteryVoltage",
    "alternator-overcharging": "BatteryVoltage",
}
_ANY_NUM_UNIT_RE = re.compile(
    r"(?<![a-z0-9.,])(-?\d+(?:[.,]\d+)?)\s*(°c|°f|derece|degc|c|f|mbar|bar|kpa|psi|vdc|volt|v|mv)(?![a-z0-9])"
)


def _context_readings(text: str, symptoms: list[SymptomMatch], have: set[str],
                      kb: KnowledgeBase) -> tuple[list[Reading], list[str]]:
    signals: list[tuple[str, str]] = []
    for s in symptoms:
        canonical = _SYMPTOM_SIGNAL.get(s.symptom_id)
        if canonical and canonical not in have and all(c != canonical for c, _ in signals):
            signals.append((canonical, s.symptom_id))
    if not signals:
        return [], []
    numbers = [(float(m.group(1).replace(",", ".")), _UNIT_ALIASES[m.group(2)])
               for m in _ANY_NUM_UNIT_RE.finditer(_light_fold(text))]
    out: list[Reading] = []
    notes: list[str] = []
    for canonical, sid in signals:
        target = _canonical_unit(kb, canonical)
        fits = [(v, u) for v, u in numbers if target and _to_unit(v, u, target) is not None]
        if len(fits) != 1:
            continue
        value, unit = fits[0]
        reading = _make_reading(kb, canonical, sid, value, unit, "text_context")
        if reading is not None:
            out.append(reading)
            notes.append(f"context_reading:{canonical}<-{sid}")
    return out, notes


def _input_readings(telemetry: Mapping[str, Any], kb: KnowledgeBase) -> tuple[list[Reading], list[str]]:
    out: list[Reading] = []
    unknown: list[str] = []
    for key in sorted(telemetry, key=str):
        raw = telemetry[key]
        unit = ""
        value: Any = raw
        if isinstance(raw, Mapping):
            value = raw.get("value")
            unit = _UNIT_ALIASES.get(str(raw.get("unit") or "").lower(), str(raw.get("unit") or ""))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            unknown.append(str(key))
            continue
        fval = float(value)
        if fval != fval or fval in (float("inf"), float("-inf")):
            unknown.append(str(key))  # NaN/Inf is not a measurement
            continue
        canonical = kb.canonical_signal(str(key))
        if not kb.signal_measurement(canonical).found and not kb.telemetry_threshold(canonical).found:
            unknown.append(str(key))
            continue
        reading = _make_reading(kb, canonical, str(key), fval, unit, "input")
        if reading is None:
            unknown.append(str(key))
            continue
        if not unit and reading.unit_assumed:
            # Bus/decoder values arrive in the canonical engineering unit.
            reading = Reading(reading.canonical, reading.value, reading.unit, reading.raw_name, "input",
                              False, fval, reading.unit)
        out.append(reading)
    return out, unknown


# ------------------------------------------------------------------- public
_CODE_STATUSES = frozenset({"ACTIVE", "HISTORY", "PENDING"})


def _explicit_codes(dtcs: Iterable[Any]) -> tuple[list[CodeMention], list[SpnMention]]:
    codes: list[CodeMention] = []
    spns: list[SpnMention] = []
    for item in dtcs:
        status = "ACTIVE"
        oc: int | None = None
        if isinstance(item, Mapping):
            raw_status = str(item.get("status") or "ACTIVE").upper()
            status = raw_status if raw_status in _CODE_STATUSES else "ACTIVE"
            oc = item.get("oc") if isinstance(item.get("oc"), int) and not isinstance(item.get("oc"), bool) else None
            spn = item.get("spn")
            fmi = item.get("fmi")
            if isinstance(spn, int) and not isinstance(spn, bool):
                spns.append(SpnMention(spn, fmi if isinstance(fmi, int) and 0 <= fmi <= 31 else None, "input",
                                       oc, status=status))
                continue
            item = item.get("code") or ""
        text = str(item)
        m = _SPN_TEXT_RE.search(text)
        if m:
            fmi_v = int(m.group(2)) if m.group(2) is not None and int(m.group(2)) <= 31 else None
            spns.append(SpnMention(int(m.group(1)), fmi_v, "input", oc, status=status))
            continue
        code = normalize_dtc_code(text)
        if code:
            codes.append(CodeMention(code, "input", text, status=status))
    return codes, spns


# ------------------------------------------------------------ check answers
_YES = frozenset({"yes", "y", "evet", "e", "var", "true", "1"})
_NO = frozenset({"no", "n", "hayir", "h", "yok", "false", "0"})
_CHECK_UNIT_RE: dict[str, str] = {
    "mA": r"ma|miliamper", "V": r"v|volt|vdc", "Ohm": r"ohm|ω|Ω", "bar": r"bar", "°C": r"°c|derece|c",
}


def _answer_value(kind: str, raw: Any) -> str | float:
    if kind == "measurement":
        if isinstance(raw, bool):
            return "unknown"
        if isinstance(raw, (int, float)):
            return float(raw)
        try:
            return float(str(raw).strip().replace(",", "."))
        except ValueError:
            return "unknown"
    if raw is True:
        return "yes"
    if raw is False:
        return "no"
    folded = fold_text(str(raw))
    return "yes" if folded in _YES else "no" if folded in _NO else "unknown"


_SPECIALISED_DOMAINS = frozenset({"MARINE", "EV_HV", "HEAVY_DUTY"})


def code_check_symptoms(codes: Iterable[str], kb: KnowledgeBase, limit: int = 2) -> list[str]:
    """Symptoms with questions that an active code points at (P0301 -> misfire-cylinder-1).

    A generic code is listed by many symptoms (U0100 by a dozen marine ones). For
    an OBD code (P/B/C/U) the general-domain symptoms are taken first and a
    marine / EV / heavy-duty symptom only when no general one exists, so a car's
    U0100 never asks about a boat's kill-switch lanyard.
    """
    out: list[str] = []
    for code in codes:
        sids = [sid for sid in kb.symptoms_for_code(code) if kb.symptom_checks(sid)]
        if not code.upper().startswith("SPN"):
            general = [sid for sid in sids
                       if str((kb.symptom(sid).record or {}).get("domain") or "") not in _SPECIALISED_DOMAINS]
            sids = general or sids
        for sid in sids:
            if sid not in out:
                out.append(sid)
    return out[:limit]


def _check_answers(answers: Mapping[str, Any], text: str, symptoms: list[SymptomMatch],
                   kb: KnowledgeBase, code_symptoms: Iterable[str] = ()) -> tuple[list[CheckAnswer], list[str]]:
    """Answers for the matched (or code-implied) symptoms' checks: explicit input
    first, then measurements written in the text next to the check's own keyword."""
    out: dict[str, CheckAnswer] = {}
    notes: list[str] = []
    matched = {s.symptom_id for s in symptoms} | set(code_symptoms)
    for raw_key, raw in list(answers.items())[:40]:
        sid, _, cid = str(raw_key).partition(".")
        check = next((c for c in kb.symptom_checks(sid) if c.get("id") == cid), None)
        if check is None or sid not in matched:
            notes.append(f"answer_ignored:{str(raw_key)[:80]}")
            continue
        out[f"{sid}.{cid}"] = CheckAnswer(sid, cid, _answer_value(str(check.get("kind")), raw), "input")
    light = _light_fold(text)
    folded = fold_text(text)
    for s in symptoms:
        for check in kb.symptom_checks(s.symptom_id):
            key = f"{s.symptom_id}.{check.get('id')}"
            unit_re = _CHECK_UNIT_RE.get(str(check.get("unit")))
            if key in out or check.get("kind") != "measurement" or not unit_re:
                continue
            if not any(fold_text(k) in folded for k in check.get("text_keys") or []):
                continue
            nums = re.findall(rf"(?<![a-z0-9.,])(-?\d+(?:[.,]\d+)?)\s*(?:{unit_re})(?![a-z0-9])", light)
            if len(nums) == 1:  # one candidate number only; ambiguity is never resolved by guessing
                out[key] = CheckAnswer(s.symptom_id, str(check.get("id")), float(nums[0].replace(",", ".")), "text")
    return list(out.values()), notes


def _monitor_results(monitors: Iterable[Any]) -> list[dict[str, Any]]:
    """Mode 06 records as given by the reader; anything malformed is dropped, never repaired."""
    out: list[dict[str, Any]] = []
    for m in list(monitors)[:200]:
        if not isinstance(m, Mapping):
            continue
        try:
            mid, tid = int(m["mid"]), int(m["tid"])
            value, lo, hi = float(m["value"]), float(m["min"]), float(m["max"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < mid <= 0xFF and 0 <= tid <= 0xFF and all(math.isfinite(x) for x in (value, lo, hi)):
            out.append({"mid": mid, "tid": tid, "value": value, "min": lo, "max": hi,
                        "unit": str(m.get("unit") or ""), "passed": bool(m.get("passed"))})
    return out


def parse_query(
    text: str = "",
    *,
    dtcs: Iterable[Any] = (),
    telemetry: Mapping[str, Any] | None = None,
    dm1: Iterable[Any] = (),
    vehicle_make: str | None = None,
    vehicle_model: str | None = None,
    language: str | None = None,
    answers: Mapping[str, Any] | None = None,
    kb: KnowledgeBase | None = None,
    freeze_frame: Mapping[str, Any] | None = None,
    monitors: Iterable[Any] = (),
) -> ParsedQuery:
    """Parse everything the copilot was given into one deterministic structure."""
    kb = kb or get_knowledge_base()
    text = str(text or "")[:4000]
    lang = language if language in ("tr", "en") else detect_language(text)
    pq = ParsedQuery(text=text, language=lang)

    # Codes: explicit input first (authoritative), then DM1, then text.
    codes, spns = _explicit_codes(dtcs)
    for payload in dm1:
        if isinstance(payload, Mapping) and isinstance(payload.get("spn"), int):
            c2, s2 = _explicit_codes([payload])
            spns.extend(SpnMention(s.spn, s.fmi, "dm1", s.occurrence_count) for s in s2)
            continue
        lamps, decoded, notes = decode_dm1_payload(payload)
        if lamps is not None:
            pq.dm1_lamps.append(lamps)
        spns.extend(decoded)
        pq.notes.extend(notes)
    codes.extend(_dtc_mentions(text))
    text_spns, notes = _spn_mentions(text)
    spns.extend(text_spns)
    pq.notes.extend(notes)

    seen_codes: set[str] = set()
    for c in codes:
        if c.code not in seen_codes:
            seen_codes.add(c.code)
            pq.dtcs.append(c)
    seen_spn: dict[tuple[int, int | None], int] = {}
    for s in spns:
        key = (s.spn, s.fmi)
        if (s.spn, None) in seen_spn and s.fmi is not None:
            # a later qualified mention replaces an unqualified one
            pq.spns[seen_spn.pop((s.spn, None))] = s
            seen_spn[key] = len(pq.spns) - 1
            continue
        if key in seen_spn or (s.fmi is None and any(k[0] == s.spn for k in seen_spn)):
            continue
        seen_spn[key] = len(pq.spns)
        pq.spns.append(s)

    for m in _PGN_TEXT_RE.finditer(text):
        raw = m.group(1)
        pgn = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
        if 0 <= pgn <= 0x3FFFF and pgn not in pq.pgns:
            pq.pgns.append(pgn)
    for m in _PID_TEXT_RE.finditer(text):
        pid = (m.group(1) or m.group(2) or "").upper()
        if pid and pid not in pq.pids:
            pq.pids.append(pid)

    folded = fold_text(text)
    # Codes are not complaint words: strip them before symptom matching so
    # "P0101" can never fuzzy-match a symptom keyword. Plain numbers stay:
    # "3. silindir tekleme" must reach the cylinder-3 symptom (numbers are
    # under 4 characters or exact-match only, so they never match fuzzily).
    folded_wo_codes = " ".join(t for t in folded.split() if not re.fullmatch(r"[pbcu][0-9a-f]{4}", t))
    pq.symptoms, pq.corrections = _match_symptoms(folded_wo_codes, kb)
    pq.safety_terms = _safety_terms(folded, kb)

    readings, unknown = _input_readings(telemetry or {}, kb)
    have = {r.canonical for r in readings}
    readings.extend(r for r in _text_readings(text, kb) if r.canonical not in have)
    context, notes = _context_readings(text, pq.symptoms, {r.canonical for r in readings}, kb)
    readings.extend(context)
    pq.notes.extend(notes)
    pq.readings = readings
    pq.unknown_telemetry = unknown
    codes_read = [c.code for c in pq.dtcs] + [f"SPN {x.spn}" for x in pq.spns]
    if isinstance(freeze_frame, Mapping):
        ff_code = normalize_dtc_code(str(freeze_frame.get("dtc") or ""))
        raw = freeze_frame.get("readings")
        if ff_code and isinstance(raw, Mapping):
            ff_readings, _unknown = _input_readings(raw, kb)
            pq.freeze_dtc = ff_code
            pq.freeze_readings = [Reading(x.canonical, x.value, x.unit, x.raw_name, "freeze_frame", x.unit_assumed,
                                          x.raw_value, x.raw_unit) for x in ff_readings]
    pq.monitors = _monitor_results(monitors)
    pq.answers, notes = _check_answers(answers or {}, text, pq.symptoms, kb, code_check_symptoms(codes_read, kb))
    pq.notes.extend(notes)

    pq.vehicle_make = vehicle_make or None
    pq.vehicle_model = vehicle_model or None
    if pq.vehicle_make is None and text:
        try:
            from src.engine.ai.diagnostic_copilot import detect_vehicle_make

            pq.vehicle_make = detect_vehicle_make(text)
        except Exception:  # noqa: BLE001 — make detection is optional context
            pq.vehicle_make = None
    return pq

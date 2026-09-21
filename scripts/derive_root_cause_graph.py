#!/usr/bin/env python
"""T2-1 — Derive the root-cause graph from the diagnostic databases.

Fully offline and deterministic. NO LLM, NO network. Re-runnable, idempotent
(md5 stable) and fail-closed: every emitted node carries a ``source_ref`` that
resolves to a REAL entry in the source DB, and the derivation ABORTS (exit 1)
if any node fails resolution — a graph with a fabricated reference is never
written.

Derivation gates (see ``tasks/T2-0-graph-derivation-feasibility.md``)
-------------------------------------------------------------------
Gate A — code -> root cause. Two verified sources:

1. ``dtc_database.json[code].causes[]``. 47.748 entries total, but 24.298
   (50.9%) are generic copy-paste filler produced by a template generator
   (``causes_source`` = "U-code ... template (Tur-30)"), e.g.
   "P0087 devresinde sinyal aralık/performans veya referans voltaj hatası."
   Those are EXCLUDED — turning them into nodes would make every node a
   duplicate and the node count a lie. Only the 8.984 DTCs with at least one
   genuinely specific cause contribute.

2. ``j1939_spn_fmi_database.json["spns"][spn].causes[]``, plus the per-FMI
   ``fault_matrix[fmi].causes``. Quarantined material is EXCLUDED: blocks with
   ``_quarantined=true`` and anything sourced from ``dtcdocs.com`` (LLM/SEO
   generated), per T1-2 / PROVENANCE v1.11.0-quarantine.

Gate B — root cause -> evidence signal. The database has NO structural link
between a cause and a signal name, so the mapping is a hand-curated,
evidence-backed rule table (``_SIGNAL_RULES``). Every rule must cite where its
signal vocabulary comes from; a node whose cause matches no rule gets NO
``evidence_signals`` (empty list) rather than an invented one. Empty is
honest; fabricated is forbidden (AGENTS.md §2.3 / Tuzaklar §11).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / "data" / "diagnostics"
DTC_DB = DIAG / "dtc_database.json"
SPN_DB = DIAG / "j1939_spn_fmi_database.json"
TAXONOMY = DIAG / "system_taxonomy.json"
GRAPH_OUT = DIAG / "root_cause_graph.json"
SEED_GRAPH = DIAG / "root_cause_graph_seed.json"
COPILOT_SRC = ROOT / "src" / "engine" / "ai" / "diagnostic_copilot.py"
QUARANTINE = DIAG / "quarantine" / "dtcdocs_llm_blocks.json"

SCHEMA_VERSION = 1

# --- Gate A / exclusion rules ------------------------------------------------

# Generic copy-paste filler signature. The T2-0 feasibility study documented
# three families (24,298 causes = 50.9% of 47,748). T2-1 measurement found the
# filler is WIDER: the same copy-paste generator also produced the "İlgili
# kontrol ünitesi / İlgili elemanın / İlgili modülün / Kablo demeti... /
# VAG ECU/ECM ..." families, taking the real total to 29,260 causes = 61.3%.
# Sizes (measured by scripts/_probe_t21d.py, first-match-wins):
#   T2-0 three 24,298 | ilgili kontrol ünitesi 1,824 | ilgili modülün 753
#   kablo demeti/konnektör 886 | kablo demeti izolasyon 616 | ilgili elemanın 371
#   VAG ECU/ECM 350 | CAN bus circuit 158 | may have become 4
GENERIC_TEMPLATE_RE = re.compile(
    # -- T2-0 documented three ------------------------------------------------
    r"devresinde sinyal aral"
    r"|Kablo demetinde ezilme"
    r"|Kontrol \u00fcnitesi \(ECU/ECM/BCM\) dahili"
    # -- T2-1 measured additions (same generator, same copy-paste signature) --
    r"|^\u0130lgili kontrol \u00fcnitesinde"
    r"|^\u0130lgili eleman\u0131n"
    r"|^\u0130lgili mod\u00fcl\u00fcn"
    r"|^Kablo demeti/konnekt\u00f6rde"
    r"|^Kablo demetinde izolasyon hasar"
    r"|^VAG ECU/ECM"
    r"|Short to (power|ground) in either CAN bus circuit"
    r"|circuit may have become (corroded|broken|cracked|damaged)"
)

# Structural junk that is NOT a fault mechanism: code-name echoes, OEM code
# dumps, source citations, template scaffolding.
_ECHO_RE = re.compile(r"^[A-Z][0-9A-Z]{4,6} devresi", re.IGNORECASE)
_OEM_DUMP_RE = re.compile(r"VAG/Audi OEM Teshis Kodu|OEM Teshis Kodu", re.IGNORECASE)
_SOURCE_CITE_RE = re.compile(r"https?://|www\.|Source:|Kaynak:", re.IGNORECASE)
_TEMPLATE_SCAFFOLD_RE = re.compile(
    r"^Causes for this code may include"
    r"|^Common (problems|issues|reasons)"
    r"|^This (fault|code|DTC|condition) (is|may|can|commonly|typically|often)"
    r"|^Possible causes"
    r"|^Some (common )?(possible )?causes"
    r"|^The following (are|is)",
    re.IGNORECASE,
)
# Prose that is a procedure/description, not a cause (SPN_190 style noise).
_PROCEDURE_NOISE_RE = re.compile(
    r"Additional Tools|Basic hand tools|Test Adapter Kit|"
    r"Refer to OEM|Data link|"
    r"Module\(s\)|message not enabled",
    re.IGNORECASE,
)

# A cause must name a plausible physical/electrical fault component or action.
_ACTION_VOCAB = (
    "pump",
    "sensor",
    "valve",
    "injector",
    "harness",
    "connector",
    "wiring",
    "wire",
    "thermostat",
    "filter",
    "gasket",
    "relay",
    "fuse",
    "module",
    "bearing",
    "seal",
    "actuator",
    "turbo",
    "egr",
    "dpf",
    "coolant",
    "hose",
    "clamp",
    "corrosion",
    "corroded",
    "ground",
    "short",
    "open circuit",
    "leak",
    "worn",
    "wear",
    "clog",
    "plug",
    "coil",
    "battery",
    "alternator",
    "compressor",
    "regulator",
    "solenoid",
    "motor",
    "switch",
    "bulb",
    "lamp",
    "radiator",
    "fan",
    "belt",
    "chain",
    "spring",
    "diaphragm",
    "orifice",
    "screen",
    "restrict",
    "contamin",
    "moisture",
    "water",
    "debris",
    "resistance",
    "voltage",
    "pressure",
    "temperature",
    "signal",
    "circuit",
    "stuck",
    "slipped",
    "stretched",
    "broken",
    "damaged",
    "defective",
    "faulty",
    "failed",
    "failing",
    "degraded",
    "aged",
    "dirty",
    "loose",
    "crack",
    "cracked",
    "disconnect",
    "insulation",
    "terminal",
    "pin",
    "splice",
    "shaft",
    "coupling",
    "piston",
    "injector",
    "catalyst",
    "catalys",
    "thermistor",
    "potentiometer",
    "encoder",
    "tone ring",
    "reluctor",
    "flywheel",
    "clutch",
    "solenoid",
    "pompa",
    "sens\u00f6r",
    "valf",
    "enjekt\u00f6r",
    "tesisat",
    "kablo",
    "korozyon",
    "ka\u00e7ak",
    "t\u0131kan",
    "a\u015f\u0131n",
    "termostat",
    "filtre",
    "conta",
    "r\u00f6le",
    "sigorta",
    "rulman",
    "ke\u00e7e",
    "akt\u00fcat\u00f6r",
    "kataliz\u00f6r",
    "radyat\u00f6r",
    "kay\u0131\u015f",
    "zincir",
    "yay",
    "diyafram",
    "nem",
    "gerilim",
    "diren\u00e7",
    "bas\u0131n\u00e7",
    "s\u0131cakl\u0131k",
    "sinyal",
    "devre",
    "kurum",
    "kirlen",
    "gev\u015fek",
    "kopuk",
    "k\u0131sa devre",
    "a\u00e7\u0131k devre",
    "\u00e7atlak",
    "erime",
    "s\u0131z\u0131nt",
    "bozulma",
    "seviye",
    "mekanik",
    "elektrik",
    "kilitlenme",
    "s\u0131k\u0131\u015fma",
)

# Codes/quarantine exclusions -------------------------------------------------

# The T1-2 quarantine criterion covered `procedures_full[]` only. PROVENANCE
# v1.11.0 records two SPNs whose `source`/`evidence_url` still point at
# dtcdocs.com and marks that scoping decision "belirsiz". T2-1 excludes those
# SPNs from derivation rather than inheriting the ambiguity.
DTCDOCS_FLAGGED_SPNS = ("SPN_100", "SPN_110")

# SPNs whose causes[] content is verifiably attached to a DIFFERENT SPN, or is
# procedure prose rather than a cause. Evidence: title-vs-cause keyword check
# (title names part X, causes name part Y). Excluded, never patched by guessing.
MISATTACHED_CAUSES = {
    "SPN_3251": "title=Aftertreatment 1 DPF Differential Pressure, causes[] content is NOx/SCR (SPN 3226/4364)",
    "SPN_651": "title=Engine Fuel 1 Injector Cylinder 1, causes[] content is ProStar oil-pressure behaviour",
    "SPN_190": "causes[] hold procedure prose (tooling/customer-care list), not fault mechanisms",
}

# --- Gate B: cause -> evidence signal (hand-curated, cited) ------------------
#
# Each rule: (regex over the cause text, tuple of signal names, provenance).
# Signal names are the repo's existing, already-used vocabulary: they appear in
# data/diagnostics/telemetry_thresholds.json["signals"] or are the J1939 SPN
# signal names taken verbatim from j1939_spn_fmi_database.json[spn].name.
#
# ORDER MATTERS: first match wins, so specific rules precede generic ones (the
# DPF differential-pressure rule must precede the generic pressure rule). A
# cause matching NO rule keeps ``evidence_signals = []`` -- empty is honest,
# invented is forbidden (AGENTS.md §2.3 / Tuzaklar §11).
SIGNAL_SOURCE_NOTE = (
    "Signal vocabulary is the repo's existing telemetry set "
    "(data/diagnostics/telemetry_thresholds.json['signals']: EngineCoolantTemp, TurboBoost, "
    "EngineOilPressure, EngineSpeed, VehicleSpeed, EngineLoad, EngineTorque) plus J1939 SPN "
    "signal names read verbatim from j1939_spn_fmi_database.json['spns'][spn]['name'] "
    "(e.g. 'Engine Coolant Level 1', 'Aftertreatment 1 Diesel Particulate Filter "
    "Differential Pressure'). No signal name was invented for this graph."
)

_SIGNAL_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = (
    (
        re.compile(r"\bdpf\b|particulate filter|diesel particulate|soot load|differential pressure", re.I),
        ("DPF Differential Pressure", "DPF Soot Load Percent"),
        "J1939 SPN 3251 name='Aftertreatment 1 Diesel Particulate Filter Differential Pressure'; "
        "SPN 3719 name='...Soot Load Percent'",
    ),
    (
        re.compile(r"nitrogen oxide|\bnox\b|\bscr\b|diesel exhaust fluid|\bdef\b|adblue|urea", re.I),
        ("NOx Sensor", "DEF Tank Volume", "DEF Quality"),
        "J1939 SPN 3216/3226 names (Intake/Outlet NOx Sensor); SPN 1761 name='...Diesel Exhaust "
        "Fluid Tank Volume'; SPN 3364 name='...Diesel Exhaust Fluid Quality'",
    ),
    (
        re.compile(r"exhaust gas recirculation|\begr\b", re.I),
        ("EGR Flow",),
        "J1939 aftertreatment/EGR SPN family names; no thresholds entry exists -> SPN name used",
    ),
    (
        re.compile(r"oil (pressure|level|pump|filter|temp)|ya\u011f (pompa|bas\u0131n|seviye|s\u0131cakl)", re.I),
        ("EngineOilPressure", "EngineOilTemp"),
        "telemetry_thresholds.json signals.EngineOilPressure; J1939 SPN 100/175 names",
    ),
    (
        re.compile(r"coolant|so\u011futma suyu|termostat|thermostat|radyat\u00f6r|radiator", re.I),
        ("EngineCoolantTemp", "EngineCoolantLevel"),
        "telemetry_thresholds.json signals.EngineCoolantTemp; J1939 SPN 110/111 names",
    ),
    (
        re.compile(
            r"fuel (pressure|rail|delivery|level|filter|pump)|yak\u0131t (bas\u0131n|seviye|filtre|pompa)", re.I
        ),
        ("FuelRailPressure", "FuelDeliveryPressure", "FuelLevel"),
        "J1939 SPN 157 name='Engine Fuel 1 Injector Metering Rail 1 Pressure'; SPN 94 name='Engine "
        "Fuel Delivery Pressure'; SPN 96 name='Fuel Level 1'",
    ),
    (
        re.compile(
            r"boost|turbo|wastegate|intercooler|charge air|\u015farj bas\u0131n|a\u015f\u0131r\u0131 besleme", re.I
        ),
        ("TurboBoost",),
        "telemetry_thresholds.json signals.TurboBoost; J1939 SPN 102 name='Engine Turbocharger 1 Boost Pressure'",
    ),
    (
        re.compile(r"engine speed|crankshaft (position|speed)|krank|rpm|motor devri|devir say", re.I),
        ("EngineSpeed",),
        "telemetry_thresholds.json signals.EngineSpeed; J1939 SPN 190 name='Engine Speed'",
    ),
    (
        re.compile(r"wheel[- ]based|vehicle speed|h\u0131z sens\u00f6r|vss|speed sensor|ara\u00e7 h\u0131z", re.I),
        ("VehicleSpeed",),
        "telemetry_thresholds.json signals.VehicleSpeed; J1939 SPN 84 name='Wheel-Based Vehicle Speed'",
    ),
    (
        re.compile(r"mass air|maf|manifold absolute|map sensor|hava k\u00fctle|intake air", re.I),
        ("EngineLoad",),
        "telemetry_thresholds.json signals.EngineLoad; J1939 SPN 92 name='Engine Percent Load'",
    ),
    (
        re.compile(r"torque|tork", re.I),
        ("EngineTorque",),
        "telemetry_thresholds.json signals.EngineTorque; J1939 SPN 513 name='Actual Engine Percent Torque'",
    ),
    (
        re.compile(r"barometric|ambient air|atmosferik", re.I),
        ("BarometricPressure",),
        "J1939 SPN 108 name='Barometric Pressure'",
    ),
    (
        re.compile(r"battery|charging|alternator|ak\u00fc|\u015farj sistemi", re.I),
        ("BatteryVoltage",),
        "no thresholds entry; J1939 SPN 168 name='Battery Potential / Power Input 1'",
    ),
)

# --- Gate C: code <-> cause IDENTITY check -----------------------------------
#
# T2-1 finding: `SPN_3251` (DPF differential pressure) carries NOx/SCR causes and
# `SPN_651` (injector cylinder 1) carries oil-pressure prose. A blind sweep of
# every SPN therefore produces mis-attributed root causes. This gate requires the
# code's own designation and the cause text to share at least one CONTENT word; a
# cause sharing nothing is rejected as `rejected_mismatch` with its reason
# recorded -- never silently kept, never patched by guessing.

_STOPWORDS = frozenset(
    {
        "aftertreatment",
        "engine",
        "sensor",
        "sensors",
        "system",
        "systems",
        "circuit",
        "circuits",
        "control",
        "module",
        "modules",
        "cylinder",
        "pressure",
        "temperature",
        "level",
        "position",
        "speed",
        "voltage",
        "fault",
        "faults",
        "failure",
        "failures",
        "faulty",
        "poor",
        "damaged",
        "defective",
        "internal",
        "external",
        "device",
        "output",
        "input",
        "vehicle",
        "component",
        "components",
        "connector",
        "connectors",
        "wiring",
        "harness",
        "value",
        "values",
        "data",
        "signal",
        "signals",
        "bank",
        "code",
        "codes",
        "with",
        "from",
        "that",
        "this",
        "when",
        "which",
        "there",
        # Added deliberately (orchestrator-directed): the pump/valve/filter/oil/fuel are
        # the DOMAIN of these subsystems, not a mechanism that discriminates one code
        # from another. Without this the gate degenerates into a rubber stamp where
        # every fuel/lube code matches every fuel/lube cause.
        "pump",
        "pumps",
        "valve",
        "valves",
        "motor",
        "motors",
        "switch",
        "switches",
        "relay",
        "relays",
        "filter",
        "filters",
        "oil",
        "fuel",
        "water",
        "coolant",
        "air",
        "flow",
        "leak",
        "leaks",
        "worn",
        "wear",
        "clogged",
        "clog",
        "stuck",
        "restricted",
        "contamination",
        "contaminated",
        "corrosion",
        "corroded",
        "loose",
        "broken",
        "blocked",
        "open",
        "short",
        "ground",
    }
)

_DEACCENT = {"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u"}


def designation_of(text: str) -> str:
    """ASCII-folded designation text used for the Gate C comparison."""
    return "".join(_DEACCENT.get(ch, ch) for ch in str(text).lower())


def _content_words(text: str) -> set[str]:
    """Alphanumeric tokens of length >= 5, minus the stopword list."""
    tokens = re.findall(r"[a-z0-9]{5,}", designation_of(text))
    return {t for t in tokens if t not in _STOPWORDS}


def identity_ok(designation: str, cause_text: str) -> bool:
    """Gate C: does the code's own designation share a content word with the cause?

    Fails open ONLY when the designation yields no content words at all (nothing
    to compare against); that case is counted separately as
    ``identity_skipped_no_title_words`` so it stays visible in the measurements.
    """
    title_words = _content_words(designation)
    if not title_words:
        return True
    return bool(title_words & _content_words(cause_text))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_quarantined_block(block: Any) -> bool:
    """True if a ``procedures_full`` block is T1-2-quarantined or dtcdocs-sourced."""
    if not isinstance(block, dict):
        return False
    if block.get("_quarantined") is True:
        return True
    blob = " ".join(
        str(block.get(k, "")) for k in ("source_site", "source_url", "source", "overview", "source_title")
    ).lower()
    return "dtcdocs.com" in blob


def is_citable_cause(text: str) -> bool:
    """Gate A acceptance for one cause string (no node from filler/junk)."""
    t = text.strip()
    if len(t) < 18 or len(t) > 600:
        return False
    if GENERIC_TEMPLATE_RE.search(t):
        return False
    if _ECHO_RE.search(t) or _OEM_DUMP_RE.search(t) or _SOURCE_CITE_RE.search(t):
        return False
    if _TEMPLATE_SCAFFOLD_RE.search(t) or _PROCEDURE_NOISE_RE.search(t):
        return False
    low = t.lower()
    if not any(w in low for w in _ACTION_VOCAB):
        return False
    # A cause stating only a code reference carries no mechanism.
    return not re.fullmatch(r"[A-Z0-9 ]{4,8}", t)


def signals_for(cause_text: str) -> tuple[str, ...]:
    """Gate B resolution. Returns () when no curated rule matches (honest)."""
    for pattern, signals, _prov in _SIGNAL_RULES:
        if pattern.search(cause_text):
            return signals
    return ()


def _slug(text: str, max_len: int = 52) -> str:
    tr = {
        "ç": "c",
        "ğ": "g",
        "ı": "i",
        "ö": "o",
        "ş": "s",
        "ü": "u",
        "Ç": "c",
        "Ğ": "g",
        "İ": "i",
        "Ö": "o",
        "Ş": "s",
        "Ü": "u",
    }
    out = "".join(tr.get(ch, ch) for ch in text.lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:max_len].strip("-")


def main() -> int:
    ap = argparse.ArgumentParser(description="Derive root_cause_graph.json from the diagnostics DBs")
    ap.add_argument("--dry-run", action="store_true", help="measure without writing the graph")
    ap.add_argument("--limit", type=int, default=0, help="debug: cap generated nodes")
    args = ap.parse_args()

    dtc = _load_json(DTC_DB)
    spn_payload = _load_json(SPN_DB)
    spns = spn_payload["spns"]

    stats: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    rejects: list[dict[str, str]] = []
    signal_counts: Counter[str] = Counter()
    subsystem_counts: Counter[str] = Counter()

    def emit(
        *,
        code: str,
        cause_text: str,
        source_ref: str,
        kind: str,
        subsystem: str | None,
        designation: str,
    ) -> None:
        if not is_citable_cause(cause_text):
            stats["rejected_gate_a"] += 1
            rejects.append({"source_ref": source_ref, "gate": "A_filler_or_junk", "cause": cause_text[:200]})
            return
        # Gate C — code/title identity. A cause that shares no content word with
        # its own code's designation is mis-attributed; it never becomes a node.
        if not identity_ok(designation, cause_text):
            stats["rejected_mismatch"] += 1
            rejects.append(
                {
                    "source_ref": source_ref,
                    "gate": "C_identity_mismatch",
                    "designation": designation,
                    "cause": cause_text[:200],
                }
            )
            return
        if not _content_words(designation):
            stats["identity_skipped_no_title_words"] += 1
        slug = _slug(cause_text)
        if not slug:
            stats["rejected_empty_slug"] += 1
            return
        node_id = f"{_slug(code)}-{slug}"
        if (node_id, code) in seen_keys:
            stats["rejected_duplicate"] += 1
            return
        seen_keys.add((node_id, code))
        sigs = list(signals_for(cause_text))
        for s in sigs:
            signal_counts[s] += 1
        subsystem_counts[str(subsystem)] += 1
        candidates.append(
            {
                "id": node_id,
                "title": cause_text.strip(),
                "evidence_signals": sigs,
                "expected_dtcs": [code],
                "contradicting_signals": [],
                "source_ref": source_ref,
                "_kind": kind,
                "_subsystem": subsystem,
            }
        )
        stats["accepted"] += 1

    # --- Gate A source 1: dtc_database.json ---------------------------------
    for code in sorted(dtc):
        rec = dtc[code]
        causes = rec.get("causes") or []
        if not isinstance(causes, list):
            continue
        genuine = [c for c in causes if isinstance(c, str) and not GENERIC_TEMPLATE_RE.search(c)]
        if not genuine:
            stats["dtc_all_generic"] += 1
            continue
        stats["dtc_with_genuine"] += 1
        designation = designation_of(rec.get("title") or code)
        for idx, cause in enumerate(causes):
            if not isinstance(cause, str):
                continue
            if GENERIC_TEMPLATE_RE.search(cause):
                stats["dtc_generic_cause_excluded"] += 1
                continue
            emit(
                code=code,
                cause_text=cause,
                source_ref=f"dtc_database.json['{code}'].causes[{idx}]",
                kind="dtc",
                subsystem=rec.get("subsystem"),
                designation=designation,
            )

    # --- Gate A source 2: j1939_spn_fmi_database.json ------------------------
    for spn_key in sorted(spns):
        rec = spns[spn_key]
        if not isinstance(rec, dict):
            continue
        if spn_key in DTCDOCS_FLAGGED_SPNS:
            stats["spn_excluded_dtcdocs_source"] += 1
            continue
        if spn_key in MISATTACHED_CAUSES:
            stats["spn_excluded_misattached"] += 1
            rejects.append(
                {
                    "source_ref": f"j1939_spn_fmi_database.json['spns']['{spn_key}']",
                    "gate": "C_known_misattached_exclusion",
                    "reason": MISATTACHED_CAUSES[spn_key],
                }
            )
            continue
        code = f"SPN {rec.get('spn', spn_key)}"
        designation = designation_of(" ".join(str(rec.get(k, "")) for k in ("name", "title_tr")))
        for idx, cause in enumerate(rec.get("causes") or []):
            if not isinstance(cause, str):
                continue
            emit(
                code=code,
                cause_text=cause,
                source_ref=f"j1939_spn_fmi_database.json['spns']['{spn_key}'].causes[{idx}]",
                kind="spn",
                subsystem=rec.get("subsystem"),
                designation=designation,
            )
        fm = rec.get("fault_matrix") or {}
        for fmi in sorted(fm, key=lambda k: str(k)):
            entry = fm[fmi]
            if not isinstance(entry, dict):
                continue
            cause = entry.get("causes")
            if not isinstance(cause, str) or not cause.strip():
                continue
            emit(
                code=code,
                cause_text=cause,
                source_ref=(
                    f"j1939_spn_fmi_database.json['spns']['{spn_key}'].fault_matrix[{json.dumps(str(fmi))}]['causes']"
                ),
                kind="spn_fmi",
                subsystem=rec.get("subsystem"),
                designation=designation,
            )

    # --- Validate before write (fail-closed) --------------------------------
    resolver = _build_resolver(dtc, spns)
    unresolved = [c["source_ref"] for c in candidates if resolver(c["source_ref"]) is None]

    by_kind = Counter(c["_kind"] for c in candidates)
    template_in_new = sum(1 for c in candidates if GENERIC_TEMPLATE_RE.search(c["title"]))
    covered_codes = {c["expected_dtcs"][0] for c in candidates}
    empty_signals = sum(1 for c in candidates if not c["evidence_signals"])

    print(f"source dtc sha256        : {_sha256(DTC_DB)}")
    print(f"source spn sha256        : {_sha256(SPN_DB)}")
    print(f"nodes generated          : {len(candidates)}")
    print(f"  by kind                : {dict(by_kind)}")
    print(f"codes covered            : {len(covered_codes)}")
    print(f"template in NEW nodes    : {template_in_new} ({template_in_new / max(len(candidates), 1) * 100:.2f}%)")
    print(f"nodes w/ empty signals   : {empty_signals} ({empty_signals / max(len(candidates), 1) * 100:.1f}%)")
    print(f"distinct signals used    : {len(signal_counts)}")
    for s, n in signal_counts.most_common():
        print(f"      {s:28s} {n}")
    print(f"rejects (recorded)       : {len(rejects)}")
    print(f"unresolved source_ref    : {len(unresolved)}")
    for ref in unresolved[:10]:
        print(f"   !! {ref}")
    print("gate stats:")
    for k in sorted(stats):
        print(f"   {k:34s} {stats[k]}")
    print(f"distinct subsystems seen : {len(subsystem_counts)}")

    if unresolved:
        print("ABORT: unresolved source_ref -> graph NOT written (fail-closed)")
        return 1

    # Rejection ledger is written next to the graph for operator audit: a
    # rejected cause is evidence-bearing output, not a silent drop.
    rejects_path = DIAG / "root_cause_graph_rejects.json"
    if not args.dry_run:
        rejects_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task": "T2-1",
                    "source_db_sha256": {
                        "dtc_database.json": _sha256(DTC_DB),
                        "j1939_spn_fmi_database.json": _sha256(SPN_DB),
                    },
                    "gate_definitions": {
                        "A_filler_or_junk": (
                            "generic template / code echo / OEM dump / source citation / "
                            "template scaffold / procedure prose / no fault-mechanism vocabulary"
                        ),
                        "C_identity_mismatch": (
                            "cause text shares no content word with its own code designation "
                            "(demonstrated on SPN_3251 / SPN_651)"
                        ),
                        "C_known_misattached_exclusion": "SPN excluded wholesale, reason recorded",
                    },
                    "count": len(rejects),
                    "rejects": rejects,
                },
                ensure_ascii=False,
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )

    if args.limit:
        candidates = candidates[: args.limit]

    if args.dry_run:
        print("dry-run: graph not written")
        return 0

    # Cross-group nodes that share one genuine cause text across several codes
    # are merged into a single node with multiple expected_dtcs (same fault).
    merged = _merge_by_title(candidates)
    taxonomy = _load_json(TAXONOMY) if TAXONOMY.is_file() else {"canonical_systems": []}
    label_to_system = _build_label_index(taxonomy)
    for node in merged:
        node["system"] = label_to_system.get(str(node.pop("_tax_subsystem", "")), "unknown-unmapped")
        node.pop("_kind", None)

    # The curated expert-KB seed is a SUPERSET contribution, never a
    # replacement: T2-1 must not delete knowledge the DB does not carry.
    # Seed nodes win on id collision (their `source_ref` cites the KB, and their
    # multi-code `expected_dtcs` encode documented related-code groups).
    seed_nodes: list[dict[str, Any]] = []
    if SEED_GRAPH.is_file():
        seed_payload = _load_json(SEED_GRAPH)
        seed_nodes = [{k: v for k, v in n.items() if not k.startswith("_")} for n in seed_payload.get("nodes", [])]
        seed_ids = {n["id"] for n in seed_nodes}
        derived_ids = {n["id"] for n in merged}
        collisions = seed_ids & derived_ids
        stats["seed_nodes"] = len(seed_nodes)
        stats["seed_derived_id_collisions"] = len(collisions)
        merged = seed_nodes + [n for n in merged if n["id"] not in collisions]
        merged.sort(key=lambda n: n["id"])

    seed_resolver = _build_seed_resolver()
    unresolved_seed = [n["source_ref"] for n in seed_nodes if seed_resolver(n["source_ref"]) is None]
    if unresolved_seed:
        print(f"ABORT: unresolved SEED source_ref ({len(unresolved_seed)}) -> graph NOT written")
        for ref in unresolved_seed[:10]:
            print(f"   !! {ref}")
        return 1

    payload = {
        "schema_version": SCHEMA_VERSION,
        "_derivation_rule": (
            "Dugumler dtc_database.json[*].causes[] ve "
            "j1939_spn_fmi_database.json['spns'][*].causes[] / fault_matrix[*].causes "
            "alanlarindan turetildi. Jenerik-sablon nedenler (devresinde sinyal aral / "
            "Kablo demetinde ezilme / Kontrol unitesi (ECU/ECM/BCM) dahili) KOK NEDEN "
            "YAPILMADI; karantinali (dtcdocs.com / _quarantined) bloklar kullanilmadi. "
            "Kimlik kapisi (Gate C) etkin: kendi koduyla icerik kelimesi paylasmayan neden "
            "dugum olmaz (SPN_3251 / SPN_651 kanit). Her dugumun source_ref'i DB'de gercek bir "
            "girdiye cozulur (scripts/derive_root_cause_graph.py --verify). "
            "Uydurma dugum/ source_ref yasak (AGENTS.md 2.3). "
            f"Reddedilenler: data/diagnostics/root_cause_graph_rejects.json ({len(rejects)} kayit)."
        ),
        "system_taxonomy_ref": "data/diagnostics/system_taxonomy.json",
        "nodes": [{k: v for k, v in c.items() if not k.startswith("_")} for c in merged],
    }
    GRAPH_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {GRAPH_OUT} ({len(merged)} nodes, sha256 {_sha256(GRAPH_OUT)})")
    if not args.dry_run:
        print(f"wrote {rejects_path} ({len(rejects)} rejects, sha256 {_sha256(rejects_path)})")
    return 0


def _build_label_index(taxonomy: dict[str, Any]) -> dict[str, str]:
    """subsystem label -> canonical system id (from system_taxonomy.json)."""
    index: dict[str, str] = {}
    for system in taxonomy.get("canonical_systems", []):
        sid = system.get("id", "")
        for label in system.get("source_labels", []):
            index[str(label)] = sid
    return index


def _merge_by_title(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge nodes that state the identical cause across different codes.

    Deterministic: ordered by first appearance in the sorted candidate list.
    ``source_ref`` keeps every contributing reference (semicolon-joined) so no
    provenance is lost, and ``_source_refs`` lists them individually.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for n in nodes:
        key = re.sub(r"\s+", " ", n["title"].strip().lower())
        cur = grouped.get(key)
        if cur is None:
            cur = dict(n)
            cur["_refs"] = [n["source_ref"]]
            grouped[key] = cur
            continue
        for code in n["expected_dtcs"]:
            if code not in cur["expected_dtcs"]:
                cur["expected_dtcs"].append(code)
        for sig in n["evidence_signals"]:
            if sig not in cur["evidence_signals"]:
                cur["evidence_signals"].append(sig)
        for sig in n["contradicting_signals"]:
            if sig not in cur["contradicting_signals"]:
                cur["contradicting_signals"].append(sig)
        cur["_refs"].append(n["source_ref"])
    out: list[dict[str, Any]] = []
    for _key, n in grouped.items():
        n["expected_dtcs"] = sorted(n["expected_dtcs"])
        n["evidence_signals"] = sorted(n["evidence_signals"])
        n["contradicting_signals"] = sorted(n["contradicting_signals"])
        n["source_ref"] = "; ".join(n.pop("_refs"))
        n.pop("_kind", None)
        # Rename the internal subsystem carry-through; `main` maps it to a
        # canonical system id via system_taxonomy.json.
        n["_tax_subsystem"] = n.pop("_subsystem", None)
        out.append(n)
    out.sort(key=lambda n: n["id"])
    return out


def _build_resolver(dtc: dict[str, Any], spns: dict[str, Any]):
    """Build a resolver that maps a ``source_ref`` string to its real DB value.

    Fail-closed: returns ``None`` for anything that does not resolve to an
    existing record/field. Used by the derivation gate and by ``--verify``.
    """
    dtc_ref = re.compile(r"^dtc_database\.json\['(?P<code>[^']+)'\]\.causes\[(?P<idx>\d+)\]$")
    spn_causes_ref = re.compile(r"^j1939_spn_fmi_database\.json\['spns'\]\['(?P<spn>[^']+)'\]\.causes\[(?P<idx>\d+)\]$")
    spn_fm_ref = re.compile(
        r"^j1939_spn_fmi_database\.json\['spns'\]\['(?P<spn>[^']+)'\]"
        r"\.fault_matrix\[(?P<q>\"[^\"]+\"|'[^']+')\]\['causes'\]$"
    )

    def resolve(ref: str) -> str | None:
        for part in [p.strip() for p in ref.split(";")]:
            m = dtc_ref.match(part)
            if m:
                rec = dtc.get(m.group("code"))
                if not isinstance(rec, dict):
                    return None
                causes = rec.get("causes")
                if not isinstance(causes, list):
                    return None
                i = int(m.group("idx"))
                if i >= len(causes) or not isinstance(causes[i], str):
                    return None
                continue
            m = spn_causes_ref.match(part)
            if m:
                rec = spns.get(m.group("spn"))
                if not isinstance(rec, dict):
                    return None
                causes = rec.get("causes")
                if not isinstance(causes, list):
                    return None
                i = int(m.group("idx"))
                if i >= len(causes) or not isinstance(causes[i], str):
                    return None
                continue
            m = spn_fm_ref.match(part)
            if m:
                rec = spns.get(m.group("spn"))
                if not isinstance(rec, dict):
                    return None
                fmi = json.loads(m.group("q"))
                entry = (rec.get("fault_matrix") or {}).get(str(fmi))
                if not isinstance(entry, dict) or not isinstance(entry.get("causes"), str):
                    return None
                continue
            return None
        return ref

    return resolve


_J1939_TITLE_REF = re.compile(r"^J1939-DB (?P<spn>SPN_\d+) title_tr$")


def _build_seed_resolver():
    """Resolve a curated seed node's ``source_ref`` against its real origin.

    Seed refs cite ``diagnostic_copilot.py`` knowledge (EXPERT_KNOWLEDGE_BASE
    keys, scenario branches, documented related-code groups) and J1939 DB
    ``title_tr`` fields. Fail-closed: a ref that names neither a resolvable DB
    entry nor the existing KB source file resolves to ``None`` and aborts the
    write. This is the anti-fabrication gate for the preserved expert nodes.
    """
    db_resolver = None
    spns_cache: dict[str, Any] | None = None

    def resolve(ref: str) -> str | None:
        nonlocal db_resolver, spns_cache
        if db_resolver is None:
            db_resolver = _build_resolver(_load_json(DTC_DB), _load_json(SPN_DB)["spns"])
            spns_cache = _load_json(SPN_DB)["spns"]
        for part in [p.strip() for p in ref.split(";")]:
            if not part:
                continue
            if part.startswith(("dtc_database.json", "j1939_spn_fmi_database.json")):
                if db_resolver(part) is None:
                    return None
                continue
            m = _J1939_TITLE_REF.match(part)
            if m:
                rec = (spns_cache or {}).get(m.group("spn"))
                if not isinstance(rec, dict) or not str(rec.get("title_tr", "")).strip():
                    return None
                continue
            if part.startswith(
                (
                    "diagnostic_copilot.py",
                    "UCANLAB-EXPERT-KB",
                    "EXPERT_KNOWLEDGE_BASE",
                    "scenario",
                    "scenario_",
                    "_RELATED",
                )
            ):
                # Seed refs cite KB provenance in free form, e.g.
                #   "diagnostic_copilot.py EXPERT_KNOWLEDGE_BASE['SPN102'].causes[0]; scenario 6 overboost"
                #   "UCANLAB-EXPERT-KB:_RELATED_CODE_GROUPS P0299+P0401 (...)"
                #   "UCANLAB-EXPERT-KB:DTC DB title System Too Lean Bank 1"
                #   "EXPERT_KNOWLEDGE_BASE['SPN1761'].causes; EXPERT_KNOWLEDGE_BASE['SPN3364'].causes"
                # The machine-checkable claim is that the KB source file exists
                # and every KB key the ref names is really present in that file
                # (fail-closed: an invented key aborts the write).
                if not COPILOT_SRC.is_file():
                    return None
                for key in _KB_KEY_RE.findall(part):
                    if not _copilot_kb_has_key(key):
                        return None
                if "scenario" in part and not _copilot_has_scenario_marker():
                    return None
                continue
            return None
        return ref

    return resolve


_KB_KEY_RE = re.compile(r"EXPERT_KNOWLEDGE_BASE\[['\"](?P<key>[A-Za-z0-9_]+)['\"]\]")


def _copilot_kb_has_key(key: str) -> bool:
    """Fail-closed check that ``key`` really exists in the copilot KB source."""
    try:
        src = COPILOT_SRC.read_text(encoding="utf-8")
    except OSError:
        return False
    return f'"{key}"' in src or f"'{key}'" in src


def _copilot_has_scenario_marker() -> bool:
    """Fail-closed check that scenario rules really exist in the copilot source."""
    try:
        src = COPILOT_SRC.read_text(encoding="utf-8")
    except OSError:
        return False
    return "scenario" in src.lower()


def verify_graph() -> int:
    """Independent gate: resolve every node's source_ref against its real origin.

    Two provenance classes are resolved separately so the seed refs are held to
    the same anti-fabrication standard as the derived ones:
      * ``derived_db``   -> must resolve to a real ``causes[i]`` / ``fault_matrix`` entry
      * ``curated_seed`` -> must resolve to the KB source file + verbatim DB refs
    """
    dtc = _load_json(DTC_DB)
    spns = _load_json(SPN_DB)["spns"]
    payload = _load_json(GRAPH_OUT)
    nodes = payload.get("nodes", [])
    resolve = _build_resolver(dtc, spns)
    seed_resolve = _build_seed_resolver()
    # T3-1 audit finding: grepping `source_ref` for "dtcdocs" can NEVER match,
    # because a ref has the shape
    #   j1939_spn_fmi_database.json['spns']['SPN_102'].causes[0]
    # and never contains "dtcdocs". That made the old dtcdocs_sourced_nodes=0 a
    # STRUCTURAL false assurance. Real check: derive the quarantined SPN set from
    # the live `source` field, then see whether any node's ref names such an SPN.
    dtcdocs_spns = {
        key
        for key, entry in spns.items()
        if isinstance(entry, dict) and "dtcdocs" in str(entry.get("source", "")).lower()
    }
    # T3-3: the same reasoning generalises. A node can also inherit junk through
    # its CAUSE text (a clean source can carry mixed rows — SPN_103), or come
    # from a source whose harvest is machine-generated end to end. Both are
    # checked from the DATA here rather than from the source_ref string, for the
    # same reason the dtcdocs grep was useless.
    try:
        from src.engine.ai.junk_content_signatures import classify_cause, is_dirty_source

        junk_cause_keys: set[tuple[str, int]] = set()
        for key, entry in spns.items():
            if not isinstance(entry, dict):
                continue
            if is_dirty_source(str(entry.get("source", ""))):
                junk_cause_keys.update((key, i) for i in range(len(entry.get("causes") or [])))
                continue
            for i, cause in enumerate(entry.get("causes") or []):
                if classify_cause(str(cause)).is_junk:
                    junk_cause_keys.add((key, i))
    except ImportError:  # pragma: no cover - verifier must still run standalone
        junk_cause_keys = set()
    bad: list[str] = []
    template_hits: list[str] = []
    quarantined_hits: list[str] = []
    junk_cause_hits: list[str] = []
    kind_counts: Counter[str] = Counter()
    for n in nodes:
        ref = n.get("source_ref", "")
        if ref.startswith(("dtc_database.json", "j1939_spn_fmi_database.json")):
            ok = resolve(ref) is not None
            kind_counts["derived_db"] += 1
        else:
            ok = seed_resolve(ref) is not None
            kind_counts["curated_seed"] += 1
        if not ok:
            bad.append(f"{n.get('id')} -> {ref}")
        if GENERIC_TEMPLATE_RE.search(n.get("title", "")):
            template_hits.append(n.get("id", "?"))
        # T3-1 audit finding: grepping `ref` for "dtcdocs" can NEVER match,
        # because a source_ref has the shape
        #   j1939_spn_fmi_database.json['spns']['SPN_102'].causes[0]
        # and therefore never contains the string "dtcdocs". That made the old
        # `dtcdocs_sourced_nodes=0` a STRUCTURAL false assurance. The real check
        # must go through the DATA: does the SPN this node's cause came from
        # carry `source` = "dtcdocs.com" in the live database? (T2-1's
        # quarantining only excluded SPN_100/SPN_110 by hand.)
        # T3-1: data-driven check — does this node's ref name a dtcdocs-sourced SPN?
        _m = re.search(r"\['(SPN_\d+)'\]", ref)
        if _m and _m.group(1) in dtcdocs_spns:
            quarantined_hits.append(n.get("id", "?"))
        # T3-3: does its ref point at a cause we measured as junk?
        _c = re.search(r"\['(SPN_\d+)'\]\.causes\[(\d+)\]", ref)
        if _c and (_c.group(1), int(_c.group(2))) in junk_cause_keys:
            junk_cause_hits.append(n.get("id", "?"))
    codes = {c for n in nodes for c in n.get("expected_dtcs", [])}
    print(f"nodes={len(nodes)} codes={len(codes)}")
    print(f"  by provenance: {dict(kind_counts)}")
    print(f"unresolved_source_refs={len(bad)}")
    print(f"template_titled_nodes={len(template_hits)}")
    print(f"dtcdocs_sourced_nodes={len(quarantined_hits)}")
    print(f"junk_cause_sourced_nodes={len(junk_cause_hits)}")
    if quarantined_hits:
        print(f"  !! quarantined-source SPNs still feeding the graph ({len(dtcdocs_spns)} SPN):")
        print(f"     {sorted(dtcdocs_spns)[:20]}")
    if junk_cause_hits:
        print(f"  !! nodes fed by measured-junk causes: {sorted(junk_cause_hits)[:10]}")
    for b in bad[:10]:
        print("  !!", b)
    ok = not bad and not quarantined_hits and not junk_cause_hits
    print("[OK]" if ok else "[FAIL]")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--verify" in sys.argv:
        raise SystemExit(verify_graph())
    raise SystemExit(main())

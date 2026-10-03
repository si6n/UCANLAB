# -*- coding: utf-8 -*-
"""Validate the ``data/intake/`` staging area (Copilot intake, "ccr-intake").

``data/intake/`` is a **raw intake queue**, not a knowledge source. Nothing in
here is bound to the knowledge base: the copilot loaders
(``src/engine/ai/diagnostic_copilot.py``) never read this directory, and this
script never writes to ``data/diagnostics`` or ``data/golden_traces``. The
promotion path from intake to knowledge base is manual and review-gated
(``data/intake/README.md`` § "Entegrasyon adımları").

Checks (all fail-closed, stdlib only — no jsonschema dependency, mirroring
``src/engine/ai/golden_cases.py``):

1. **Layout** — every file must live in a known intake subdirectory; unknown
   paths, stray backups and non-text containers are rejected.
2. **Record schema** — the intake envelope and the per-type ``payload`` are
   validated with *exact* field sets; unknown or missing fields are rejected.
3. **Provenance** — every record must carry ``source`` (title/path/type/
   publisher/licence/access_date) and a licence from a closed SPDX-ish
   allowlist. An unknown, blank or "TBD" licence is a FAIL: an ambiguous
   licence is a rejection, not a warning.
4. **PII / VIN** — raw 17-char VINs, e-mail addresses, phone numbers, IBANs
   are rejected anywhere in a record; IPv4 / card-like / IMEI-like digit runs
   are WARN. ``vin_masked`` must actually be masked.
5. **Trace policy** — traces larger than the inline ceiling must not be in Git:
   they are recorded in ``MANIFEST.md`` by ``sha256`` + location only. Frame
   files referenced by a trace are hash-checked against their ``.meta.json``.
6. **MANIFEST <-> file <-> hash** — every intake artefact (templates excluded)
   has exactly one MANIFEST row, and the recorded ``bytes``/``sha256``/
   ``intake_id``/``licence`` must match the file on disk. Drift is a FAIL.
7. **Draft rule** — ``data/intake/cases/*`` are always ``draft: true`` and can
   never carry ``verified``. Drafts are not calibration-eligible.
8. **Conflict report (report-only)** — intake DTCs / SPN-FMI pairs / case DTCs
   are compared against the *existing* ``data/diagnostics`` databases and the
   existing golden cases. Findings are INFO rows: this script never overwrites
   an existing record, it only says "this already exists" or "this looks new".

Usage::

    python scripts/validate_intake.py                    # report + exit code
    python scripts/validate_intake.py --report docs/audit/intake.md
    python scripts/validate_intake.py --max-inline-trace-bytes 262144

Exit code 1 when there is at least one FAIL; WARN/INFO are reported only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTAKE_DIRNAME = Path("data") / "intake"
DIAGNOSTICS = Path("data") / "diagnostics"

SCHEMA_VERSION = 1

# Files that are documentation/boilerplate, not intake records: they are not
# hashed, not MANIFEST-listed and not record-validated.
NON_RECORD_NAMES: frozenset[str] = frozenset({"README.md", "MANIFEST.md", ".gitkeep"})
TEMPLATE_DIR = "_templates"
# Licence notices are legal documents, not records: they are tracked in git but
# never hashed as intake artefacts.
NOTICE_RE = re.compile(r"^NOTICE[.-].*\.md$", re.IGNORECASE)

RECORD_DIRS: dict[str, str] = {
    "dtc": "dtc",
    "spn_fmi": "spn_fmi",
    "pgn_layout": "pgn",
    "oem_divergence": "oem",
    "kb_defect": "defects",
    "spn_reference": "spn_ref",
    "provenance_gap": "gaps",
    "trace": "traces",
    "case": "cases",
    "oem_note": "oem_notes",
}
RECORD_TYPES: frozenset[str] = frozenset(RECORD_DIRS)

# --- envelope ---------------------------------------------------------------
# Exact field set. Extra fields FAIL (a field the reviewer does not understand
# is a field nobody audited).
ENVELOPE_FIELDS: frozenset[str] = frozenset({
    "schema_version", "intake_id", "record_type", "submitted_at", "submitter",
    "source", "confidence", "draft", "knowledge_base", "payload", "notes",
})
SUBMITTER_FIELDS: frozenset[str] = frozenset({"type", "id", "role"})
SOURCE_FIELDS: frozenset[str] = frozenset({
    "title", "path", "type", "publisher", "revision", "licence", "access_date", "snapshot",
})
SNAPSHOT_FIELDS: frozenset[str] = frozenset({"archive_url", "sha256", "bytes", "pages_cited"})

SUBMITTER_TYPES: frozenset[str] = frozenset({"human", "curator", "organization", "automated"})
# Mirrors data/diagnostics/provenance_schema.json -> source.type
SOURCE_TYPES: frozenset[str] = frozenset({
    "standard", "oem_manual", "nhtsa", "academic", "web", "regulatory",
    "service_bulletin", "proprietary", "internal_kb",
})
CONFIDENCE_LEVELS: frozenset[str] = frozenset({"unverified", "single_source", "corroborated"})

# Closed allowlist. "unknown"/"tbd"/"" are deliberately absent: an
# unresolvable licence is a rejection, never a warning. "proprietary" is
# allowed as an *explicit* statement (OEM manual, TSB): such a record may be
# staged and read, but it can never be redistributed and promotion needs a
# licence-cleared rewrite.
ALLOWED_LICENCES: frozenset[str] = frozenset({
    "CC0-1.0", "CC-BY-3.0", "CC-BY-4.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0",
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "MPL-2.0",
    "LGPL-3.0-or-later", "GPL-3.0-or-later", "Unlicense",
    "project-internal", "public-domain", "proprietary",
})
LICENCE_AMBIGUOUS: frozenset[str] = frozenset({
    "", "-", "?", "n/a", "na", "none", "unknown", "tbd", "belirsiz", "unclear",
    "proprietary?", "free", "open",
})

# --- per-type payload field sets -------------------------------------------
PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "dtc": frozenset({
        "code", "system", "title_en", "title_tr", "description", "severity",
        "known_symptoms", "possible_causes", "status", "freeze_frame_ref",
        "oem_ref", "vin_masked",
    }),
    "spn_fmi": frozenset({
        "spn", "fmi", "system", "description", "typical_causes", "pgn", "oem_ref", "vin_masked",
    }),
    "pgn_layout": frozenset({
        "pgn", "pgn_id", "description", "pgn_type", "priority", "interval_ms",
        "fields", "upstream_keys", "source_file",
    }),
    "oem_divergence": frozenset({
        "make", "source_file", "source_rows", "divergence_count", "divergences",
    }),
    "kb_defect": frozenset({
        "defect_code", "severity", "summary", "why_it_matters", "target_file",
        "target_sha256", "detector_expression", "affected_count", "examples",
    }),
    "spn_reference": frozenset({
        "spn", "names_en", "units", "resolutions", "bit_lengths", "evidence_pgns",
        "evidence_text", "sources", "kb_state", "kb_name", "kb_unit", "dbc_signals",
    }),
    "provenance_gap": frozenset({
        "source_key", "occurrences", "files", "fields", "sample_values",
        "sample_record_keys", "documented_in", "licence_status",
    }),
    "case": frozenset({
        "case_id", "domain", "make", "model", "year", "symptom", "dtcs",
        "signals_of_interest", "actual_fault", "repair", "verification",
        "trace_refs", "vin_masked",
    }),
    "oem_note": frozenset({
        "make", "model", "year", "oem_code", "system", "evidence_refs",
        "related_dtcs", "vin_masked",
    }),
    "trace": frozenset({
        "frame_file", "frame_file_sha256", "frame_file_bytes", "format", "in_git",
        "external_location", "started_at", "duration_s", "channel_count",
        "frame_count", "bus", "vin_masked",
    }),
}

DTC_STATUSES: frozenset[str] = frozenset({"ACTIVE", "HISTORY", "PENDING", "STORED"})
SEVERITIES: frozenset[str] = frozenset({"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL_STOP", "UNKNOWN"})
CASE_DTC_STATUSES: frozenset[str] = frozenset({"ACTIVE", "HISTORY", "PENDING"})
TRACE_FORMATS: frozenset[str] = frozenset({
    "can_frames_json", "can_frames_jsonl", "obd_log_text", "nmea2000_jsonl",
    "blf", "mf4", "asc", "casc", "trc", "other",
})

# Same domain set as src.core.models.diagnostics.DiagnosticDomain. The
# golden_traces v1 schema currently lacks AGRICULTURE: such a case is legal
# here but cannot be promoted until that schema is extended (reported as WARN).
CASE_DOMAINS: frozenset[str] = frozenset({
    "HEAVY_DUTY", "PASSENGER", "MARINE", "INDUSTRIAL", "AGRICULTURE",
})
GOLDEN_V1_DOMAINS: frozenset[str] = frozenset({"HEAVY_DUTY", "PASSENGER", "MARINE", "INDUSTRIAL"})

# --- regexes ----------------------------------------------------------------
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
DTC_CODE_RE = re.compile(r"^[PBCU][0-9A-F]{4}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CAN_ID_RE = re.compile(r"^0x[0-9A-Fa-f]{1,8}$")
HEX_DATA_RE = re.compile(r"^([0-9A-Fa-f]{2})+$")
INTAKE_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# VIN: 17 chars from the real VIN alphabet, case-insensitive (a VIN is
# case-insensitive in the real world, so lowercase is rejected too).
RAW_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Turkish numbers are 10 digits (mobile, 05xx xxx xx xx) or 11 digits
# (landline, 0212 xxx xx xx), optionally written with spaces/dots/dashes and
# with a +90 country prefix. Anything shorter is a technical constant, not a
# phone number — measured: an 8-digit run inside a sha256 used to false-positive.
PHONE_RE = re.compile(
    r"(?<![\d.])(?:\+90[\s.-]?\d{10}"
    r"|0[2-5](?:[\s.-]?\d){8}"
    r"|0[2-5](?:[\s.-]?\d){9})(?![\d.])"
)
IBAN_RE = re.compile(r"\bTR\d{2}[A-Z0-9]{20,26}\b", re.IGNORECASE)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
CARDISH_RE = re.compile(r"(?<![\d.])\d{13,19}(?![\d.])")
IMEI_RE = re.compile(r"(?<![\d.])\d{15}(?![\d.])")
# Hex payload values of a CAN frame: technical noise, not PII candidates.
HEX_FIELD_RE = re.compile(r'"(?:data|payload|bytes|raw)"\s*:\s*"[0-9A-Fa-f]*"')
# sha256 digests are integrity evidence, never personal data (measured: an
# 8-digit run inside a digest looked like a phone number).
SHA256_HEX_RE = re.compile(r"\b[0-9a-f]{64}\b")

# Trace policy. check_corpus_size_sentry.py is the repository-level gate
# (100 MB per file, data/traces <= 350 MB); intake is deliberately far
# stricter so the staging area can never become a blob dump.
DEFAULT_MAX_INLINE_TRACE_BYTES = 1_048_576  # 1 MiB
DEFAULT_MAX_INTAKE_BYTES = 5_242_880  # 5 MiB for the whole directory

ARTEFACT_RE = re.compile(r"(\.bak|\.orig$|~$|\.tmp-|\.swp$)")
# A KB SPN "name" that is really a diagnosis sentence (defect class, see
# scripts/intake_kb_defects.py). Duplicated here on purpose: the conflict report
# must work even when the detector tool is not importable.
FMI_SENTENCE_IN_NAME = re.compile(r"^SPN\s*\d+\s*FMI\s*\d+", re.IGNORECASE)

# A J1939/NMEA-2000 message never exceeds 255 bytes; 4096 bits is a safe
# ceiling that still rejects garbage.
MAX_FIELD_BITS = 4096


class Report:
    """Findings collector (same shape as scripts/validate_copilot_data.py)."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.metrics: dict[str, Any] = {}

    def add(self, level: str, check: str, detail: str) -> None:
        self.rows.append((level, check, detail))

    def fail(self, check: str, detail: str) -> None:
        self.add("FAIL", check, detail)

    def count(self, level: str) -> int:
        return sum(1 for lv, _, _ in self.rows if lv == level)

    def bump(self, key: str, amount: int = 1) -> None:
        self.metrics[key] = self.metrics.get(key, 0) + amount


@dataclass(slots=True)
class Record:
    """A structurally valid intake record (validation already passed)."""

    intake_id: str
    record_type: str
    path: Path
    rel_path: str
    payload: dict[str, Any]
    source: dict[str, Any]
    draft: bool
    body: str = ""  # free text outside the envelope (oem_note markdown body)

    def manifest_paths(self) -> list[str]:
        """intake-relative paths this record owns (a trace also owns its frame file)."""
        paths = [self.rel_path]
        if self.record_type == "trace" and self.payload.get("in_git") is True:
            frame_file = self.payload.get("frame_file")
            if isinstance(frame_file, str) and frame_file.strip():
                paths.append(f"{RECORD_DIRS['trace']}/{frame_file}")
        return paths


@dataclass(slots=True)
class ManifestEntry:
    intake_id: str
    rel_path: str
    kind: str
    size: int
    sha256: str
    licence: str
    source: str


@dataclass(slots=True)
class ExternalTraceEntry:
    intake_id: str
    fmt: str
    size: int
    sha256: str
    location: str


@dataclass(slots=True)
class Manifest:
    entries: dict[str, ManifestEntry] = field(default_factory=dict)
    external: dict[str, ExternalTraceEntry] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# generic helpers
# --------------------------------------------------------------------------- #
def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_tracked(repo_root: Path, rel: str) -> bool:
    try:
        res = subprocess.run(
            ["git", "ls-files", "--", rel],
            cwd=str(repo_root), capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(res.stdout.strip())


def _strip_markup(value: str) -> str:
    return value.replace("`", "").replace("*", "").strip()


def _type_name(value: Any) -> str:
    return type(value).__name__


def _is_opt_str(payload: dict[str, Any], key: str) -> bool:
    return payload.get(key) is None or isinstance(payload.get(key), str)


def _str_list(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, list):
        return False
    return all(isinstance(item, str) and item.strip() for item in value)


# --------------------------------------------------------------------------- #
# PII / VIN scanning
# --------------------------------------------------------------------------- #
def scan_text(text: str, where: str, rep: Report, *, skip_hex_fields: bool = False) -> None:
    """Scan a blob for personal data. FAIL on VIN/e-mail/phone/IBAN."""
    blob = text
    if skip_hex_fields:
        blob = HEX_FIELD_RE.sub('"data":"<hex>"', blob)
    blob = SHA256_HEX_RE.sub("<sha256>", blob)
    for regex, level, label in (
        (RAW_VIN_RE, "FAIL", "raw_vin"),
        (EMAIL_RE, "FAIL", "email"),
        (PHONE_RE, "FAIL", "phone"),
        (IBAN_RE, "FAIL", "iban"),
        (IMEI_RE, "WARN", "imei_like"),
        (CARDISH_RE, "WARN", "card_like_digits"),
        (IPV4_RE, "WARN", "ipv4_like"),
    ):
        for match in regex.finditer(blob):
            rep.add(level, label, f"{where}: {label} candidate '{match.group(0)}' (mask or drop it)")
            if level == "FAIL":
                rep.bump("pii_findings")


def _check_masked_vin(value: Any, where: str, rep: Report) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        rep.fail("vin_masked", f"{where}: vin_masked must be a string or null")
        return
    if not value.strip():
        rep.fail("vin_masked", f"{where}: vin_masked is blank; use null when unknown")
        return
    if RAW_VIN_RE.search(value):
        rep.fail("raw_vin", f"{where}: vin_masked carries a raw 17-char VIN")
        return
    if not any(ch in value for ch in "*X•<>"):
        rep.fail("vin_masked", f"{where}: vin_masked '{value}' has no mask character (* X • <>)")
        return
    rep.bump("vin_masked_fields")


# --------------------------------------------------------------------------- #
# envelope + payload validation
# --------------------------------------------------------------------------- #
def _check_exact_fields(obj: dict[str, Any], allowed: frozenset[str], where: str,
                        required: frozenset[str], rep: Report) -> bool:
    extra = set(obj) - allowed
    if extra:
        rep.fail("schema", f"{where}: unknown field(s) {sorted(extra)}")
    missing = required - set(obj)
    if missing:
        rep.fail("schema", f"{where}: missing field(s) {sorted(missing)}")
    return not extra and not missing


def _validate_source(source: Any, where: str, rep: Report) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        rep.fail("provenance", f"{where}: source must be an object")
        return None
    if not _check_exact_fields(source, SOURCE_FIELDS, f"{where}.source",
                               frozenset({"title", "path", "type", "publisher", "licence", "access_date"}), rep):
        return None
    for key in ("title", "path", "publisher"):
        if not str(source.get(key) or "").strip():
            rep.fail("provenance", f"{where}.source.{key}: blank")
    if source.get("type") not in SOURCE_TYPES:
        rep.fail("provenance", f"{where}.source.type: {source.get('type')!r} not in {sorted(SOURCE_TYPES)}")
    licence = source.get("licence")
    if not isinstance(licence, str) or licence.strip().lower() in LICENCE_AMBIGUOUS:
        rep.fail("licence", f"{where}.source.licence: '{licence}' is ambiguous — a record with an "
                            f"unresolved licence is rejected, not deferred")
    elif licence not in ALLOWED_LICENCES:
        rep.fail("licence", f"{where}.source.licence: '{licence}' is not in the intake allowlist "
                            f"{sorted(ALLOWED_LICENCES)}")
    if not DATE_RE.fullmatch(str(source.get("access_date") or "")):
        rep.fail("provenance", f"{where}.source.access_date: must be YYYY-MM-DD")
    revision = source.get("revision")
    if revision is not None and not isinstance(revision, str):
        rep.fail("provenance", f"{where}.source.revision must be a string or null")
    snapshot = source.get("snapshot")
    if snapshot is not None:
        if not isinstance(snapshot, dict):
            rep.fail("provenance", f"{where}.source.snapshot must be an object or null")
        else:
            _check_exact_fields(snapshot, SNAPSHOT_FIELDS, f"{where}.source.snapshot", frozenset(), rep)
            snap_hash = snapshot.get("sha256")
            if snap_hash is not None and not (isinstance(snap_hash, str) and SHA256_RE.fullmatch(snap_hash)):
                rep.fail("provenance", f"{where}.source.snapshot.sha256 must be a 64-char lowercase hex digest")
            snap_bytes = snapshot.get("bytes")
            if snap_bytes is not None and (not isinstance(snap_bytes, int) or snap_bytes < 0):
                rep.fail("provenance", f"{where}.source.snapshot.bytes must be a non-negative integer")
            pages = snapshot.get("pages_cited")
            if pages is not None and (not isinstance(pages, list)
                                      or not all(isinstance(p, int) and p > 0 for p in pages)):
                rep.fail("provenance", f"{where}.source.snapshot.pages_cited must be a list of positive ints")
    return source


def _validate_payload_dtc(payload: dict[str, Any], where: str, rep: Report) -> None:
    _check_exact_fields(payload, PAYLOAD_FIELDS["dtc"], where, PAYLOAD_FIELDS["dtc"], rep)
    code = payload.get("code")
    if not isinstance(code, str) or not DTC_CODE_RE.fullmatch(code):
        rep.fail("schema", f"{where}.code: must be a SAE J2012 code like P0301 (got {code!r})")
    for key in ("system", "title_en", "title_tr", "description", "freeze_frame_ref", "oem_ref"):
        if not _is_opt_str(payload, key):
            rep.fail("schema", f"{where}.{key} must be a string or null")
    if payload.get("severity") is not None and payload.get("severity") not in SEVERITIES:
        rep.fail("schema", f"{where}.severity must be one of {sorted(SEVERITIES)} or null")
    if payload.get("status") not in DTC_STATUSES:
        rep.fail("schema", f"{where}.status must be one of {sorted(DTC_STATUSES)}")
    for key in ("known_symptoms", "possible_causes"):
        if not _str_list(payload, key):
            rep.fail("schema", f"{where}.{key} must be a list of non-empty strings (use [] if unknown)")
    _check_masked_vin(payload.get("vin_masked"), where, rep)


def _validate_payload_spn(payload: dict[str, Any], where: str, rep: Report) -> None:
    _check_exact_fields(payload, PAYLOAD_FIELDS["spn_fmi"], where, PAYLOAD_FIELDS["spn_fmi"], rep)
    spn = payload.get("spn")
    if not isinstance(spn, int) or isinstance(spn, bool) or not 0 <= spn <= 524_287:
        rep.fail("schema", f"{where}.spn must be an integer in [0, 524287]")
    fmi = payload.get("fmi")
    if not isinstance(fmi, int) or isinstance(fmi, bool) or not 0 <= fmi <= 31:
        rep.fail("schema", f"{where}.fmi must be an integer in [0, 31] (FMI is 5 bits)")
    if not _is_opt_str(payload, "system") or not _is_opt_str(payload, "description"):
        rep.fail("schema", f"{where}: system/description must be a string or null")
    if not _is_opt_str(payload, "oem_ref"):
        rep.fail("schema", f"{where}.oem_ref must be a string or null")
    if not _str_list(payload, "typical_causes"):
        rep.fail("schema", f"{where}.typical_causes must be a list of non-empty strings (use [] if unknown)")
    pgn = payload.get("pgn")
    if pgn is not None and (not isinstance(pgn, int) or isinstance(pgn, bool) or not 0 <= pgn <= 262_143):
        rep.fail("schema", f"{where}.pgn must be an integer in [0, 262143] or null")
    _check_masked_vin(payload.get("vin_masked"), where, rep)


def _validate_payload_case(payload: dict[str, Any], where: str, rep: Report) -> None:
    _check_exact_fields(payload, PAYLOAD_FIELDS["case"], where, PAYLOAD_FIELDS["case"], rep)
    case_id = payload.get("case_id")
    if not isinstance(case_id, str) or not INTAKE_ID_RE.fullmatch(case_id):
        rep.fail("schema", f"{where}.case_id must be kebab-case (got {case_id!r})")
    domain = payload.get("domain")
    if domain not in CASE_DOMAINS:
        rep.fail("schema", f"{where}.domain must be one of {sorted(CASE_DOMAINS)}")
    elif domain not in GOLDEN_V1_DOMAINS:
        rep.add("WARN", "promotion_blocked",
                f"{where}.domain={domain}: data/golden_traces/cases/schema.json v1 has no such domain — "
                f"the case can be staged but not promoted until that schema is extended")
    symptom = payload.get("symptom")
    if not isinstance(symptom, str) or not symptom.strip():
        rep.fail("schema", f"{where}.symptom must be a non-empty string")
    for key in ("make", "model", "actual_fault", "repair", "verification"):
        if not _is_opt_str(payload, key):
            rep.fail("schema", f"{where}.{key} must be a string or null")
    year = payload.get("year")
    if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2100):
        rep.fail("schema", f"{where}.year must be an integer in [1900, 2100] or null")
    dtcs = payload.get("dtcs")
    if not isinstance(dtcs, list):
        rep.fail("schema", f"{where}.dtcs must be an array (use [] when unknown)")
    else:
        for i, item in enumerate(dtcs):
            if not isinstance(item, dict) or set(item) != {"code", "status"}:
                rep.fail("schema", f"{where}.dtcs[{i}] must have exactly code and status")
                continue
            if not isinstance(item["code"], str) or not DTC_CODE_RE.fullmatch(item["code"]):
                rep.fail("schema", f"{where}.dtcs[{i}].code must be a SAE J2012 code")
            if item["status"] not in CASE_DTC_STATUSES:
                rep.fail("schema", f"{where}.dtcs[{i}].status must be one of {sorted(CASE_DTC_STATUSES)}")
    signals = payload.get("signals_of_interest")
    if not isinstance(signals, list):
        rep.fail("schema", f"{where}.signals_of_interest must be an array")
    else:
        for i, item in enumerate(signals):
            expected = {"name", "expected_behavior", "observed_behavior"}
            if not isinstance(item, dict) or set(item) != expected:
                rep.fail("schema", f"{where}.signals_of_interest[{i}] must have exactly {sorted(expected)}")
                continue
            if not isinstance(item["name"], str) or not item["name"].strip():
                rep.fail("schema", f"{where}.signals_of_interest[{i}].name must be a non-empty string")
            for key in ("expected_behavior", "observed_behavior"):
                if not _is_opt_str(item, key):
                    rep.fail("schema", f"{where}.signals_of_interest[{i}].{key} must be a string or null")
    if not _str_list(payload, "trace_refs"):
        rep.fail("schema", f"{where}.trace_refs must be a list of non-empty strings (use [] if unknown)")
    _check_masked_vin(payload.get("vin_masked"), where, rep)


def _validate_payload_oem_note(payload: dict[str, Any], where: str, rep: Report) -> None:
    _check_exact_fields(payload, PAYLOAD_FIELDS["oem_note"], where, PAYLOAD_FIELDS["oem_note"], rep)
    for key in ("make", "model", "oem_code", "system"):
        if not _is_opt_str(payload, key):
            rep.fail("schema", f"{where}.{key} must be a string or null")
    year = payload.get("year")
    if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2100):
        rep.fail("schema", f"{where}.year must be an integer in [1900, 2100] or null")
    for key in ("evidence_refs", "related_dtcs"):
        if not _str_list(payload, key):
            rep.fail("schema", f"{where}.{key} must be a list of non-empty strings (use [] if unknown)")
    for i, code in enumerate(payload.get("related_dtcs") or []):
        if isinstance(code, str) and not DTC_CODE_RE.fullmatch(code):
            rep.fail("schema", f"{where}.related_dtcs[{i}] must be a SAE J2012 code (got {code!r})")
    _check_masked_vin(payload.get("vin_masked"), where, rep)


def _validate_payload_trace(payload: dict[str, Any], where: str, rep: Report) -> None:
    _check_exact_fields(payload, PAYLOAD_FIELDS["trace"], where, PAYLOAD_FIELDS["trace"], rep)
    frame_file = payload.get("frame_file")
    if not isinstance(frame_file, str) or not frame_file.strip() or "/" in frame_file or "\\" in frame_file:
        rep.fail("schema", f"{where}.frame_file must be a bare file name inside traces/ (got {frame_file!r})")
    digest = payload.get("frame_file_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        rep.fail("trace", f"{where}.frame_file_sha256 must be a 64-char lowercase sha256 digest")
    size = payload.get("frame_file_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        rep.fail("trace", f"{where}.frame_file_bytes must be a positive integer")
    if payload.get("format") not in TRACE_FORMATS:
        rep.fail("schema", f"{where}.format must be one of {sorted(TRACE_FORMATS)}")
    if not isinstance(payload.get("in_git"), bool):
        rep.fail("trace", f"{where}.in_git must be a boolean")
    location = payload.get("external_location")
    if location is not None and not isinstance(location, str):
        rep.fail("trace", f"{where}.external_location must be a string or null")
    if payload.get("in_git") is False and not str(location or "").strip():
        rep.fail("trace", f"{where}: in_git=false requires external_location (archive/object-store path)")
    if payload.get("in_git") is True and str(location or "").strip():
        rep.add("WARN", "trace", f"{where}: in_git=true but external_location is set — record one, not both")
    if not DATETIME_RE.fullmatch(str(payload.get("started_at") or "")):
        rep.fail("trace", f"{where}.started_at must be an ISO-8601 timestamp (got {payload.get('started_at')!r})")
    duration = payload.get("duration_s")
    if duration is not None and (not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0):
        rep.fail("trace", f"{where}.duration_s must be a positive number or null")
    for key in ("channel_count", "frame_count"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            rep.fail("trace", f"{where}.{key} must be a non-negative integer")
    if not _is_opt_str(payload, "bus"):
        rep.fail("trace", f"{where}.bus must be a string or null")
    _check_masked_vin(payload.get("vin_masked"), where, rep)


PGN_LAYOUT_FIELD_FIELDS: frozenset[str] = frozenset({
    "field_id", "name", "spn", "bits", "unit", "resolution", "description",
})
PGN_TYPES: frozenset[str] = frozenset({"Single", "Fast", "Mixed", "ISO", "Proprietary", "Other"})


def _validate_payload_pgn_layout(payload: dict[str, Any], where: str, rep: Report) -> None:
    """J1939/NMEA-2000 PGN field layout (e.g. canboat ``database/j1939/pgns/*.yaml``)."""
    _check_exact_fields(payload, PAYLOAD_FIELDS["pgn_layout"], where, PAYLOAD_FIELDS["pgn_layout"], rep)
    pgn = payload.get("pgn")
    if not isinstance(pgn, int) or isinstance(pgn, bool) or not 0 <= pgn <= 262_143:
        rep.fail("schema", f"{where}.pgn must be an integer in [0, 262143]")
    pgn_id = payload.get("pgn_id")
    if not isinstance(pgn_id, str) or not pgn_id.strip():
        rep.fail("schema", f"{where}.pgn_id must be a non-empty string")
    for key in ("description", "source_file"):
        if not _is_opt_str(payload, key):
            rep.fail("schema", f"{where}.{key} must be a string or null")
    if payload.get("pgn_type") is not None and payload.get("pgn_type") not in PGN_TYPES:
        rep.fail("schema", f"{where}.pgn_type must be one of {sorted(PGN_TYPES)} or null")
    for key in ("priority", "interval_ms"):
        value = payload.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            rep.fail("schema", f"{where}.{key} must be a non-negative integer or null")
    upstream_keys = payload.get("upstream_keys")
    if not isinstance(upstream_keys, list) or not all(isinstance(k, str) and k.strip() for k in upstream_keys):
        rep.fail("schema", f"{where}.upstream_keys must be a list of non-empty strings")
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        rep.fail("schema", f"{where}.fields must be a non-empty array")
        return
    for i, item in enumerate(fields):
        if not isinstance(item, dict) or not _check_exact_fields(
            item, PGN_LAYOUT_FIELD_FIELDS, f"{where}.fields[{i}]", frozenset({"name"}), rep
        ):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            rep.fail("schema", f"{where}.fields[{i}].name must be a non-empty string")
        field_id = item.get("field_id")
        if field_id is not None and (not isinstance(field_id, str) or not field_id.strip()):
            rep.fail("schema", f"{where}.fields[{i}].field_id must be a non-empty string or null")
        spn = item.get("spn")
        if spn is not None and (not isinstance(spn, int) or isinstance(spn, bool) or not 0 <= spn <= 524_287):
            rep.fail("schema", f"{where}.fields[{i}].spn must be an integer in [0, 524287] or null")
        bits = item.get("bits")
        if bits is not None and (not isinstance(bits, int) or isinstance(bits, bool)
                                 or not 1 <= bits <= MAX_FIELD_BITS):
            rep.fail("schema", f"{where}.fields[{i}].bits must be an integer in [1, {MAX_FIELD_BITS}] or null")
        elif isinstance(bits, int) and bits > 64:
            # NMEA-2000 scalars max out at 64 bits; upstream uses a bulk "Data"
            # placeholder for not-yet-reverse-engineered fast packets. Kept
            # verbatim, flagged for the reviewer instead of rejected.
            rep.add("WARN", "pgn_bits_bulk",
                    f"{where}.fields[{i}]: bits={bits} exceeds the 64-bit scalar limit — upstream payload "
                    f"placeholder or unknown layout, not a scalar field")
        resolution = item.get("resolution")
        if resolution is not None and (isinstance(resolution, bool) or not isinstance(resolution, (int, float))):
            rep.fail("schema", f"{where}.fields[{i}].resolution must be a number or null")
        for key in ("unit", "description"):
            if item.get(key) is not None and not isinstance(item.get(key), str):
                rep.fail("schema", f"{where}.fields[{i}].{key} must be a string or null")
        # The SPN may only come from the copied upstream text, never from us.
        if isinstance(name, str) and isinstance(spn, int) and str(spn) not in f"{name} {item.get('description') or ''}":
            rep.add("WARN", "pgn_spn_source",
                    f"{where}.fields[{i}]: SPN {spn} is not mentioned in the copied upstream text — "
                    f"verify how it was derived")


OEM_DIVERGENCE_FIELDS: frozenset[str] = frozenset({
    "code", "source_description_en", "kb_description_en",
})


def _validate_payload_oem_divergence(payload: dict[str, Any], where: str, rep: Report) -> None:
    """Per-manufacturer DTC wording that the merged OEM layer does not carry.

    The layer stores one description per code; upstream publishes one per
    manufacturer. These records restore the per-make wording as *evidence*.
    """
    _check_exact_fields(payload, PAYLOAD_FIELDS["oem_divergence"], where,
                        PAYLOAD_FIELDS["oem_divergence"], rep)
    make = payload.get("make")
    if not isinstance(make, str) or not make.strip() or make != make.upper():
        rep.fail("schema", f"{where}.make must be a non-empty uppercase string")
    if not _is_opt_str(payload, "source_file") or not str(payload.get("source_file") or "").strip():
        rep.fail("schema", f"{where}.source_file must be a non-empty string")
    for key in ("source_rows", "divergence_count"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            rep.fail("schema", f"{where}.{key} must be a non-negative integer")
    rows = payload.get("divergences")
    if not isinstance(rows, list):
        rep.fail("schema", f"{where}.divergences must be an array")
        return
    if rows and payload.get("divergence_count") != len(rows):
        rep.fail("schema", f"{where}.divergence_count {payload.get('divergence_count')} != "
                           f"{len(rows)} rows")
    for i, item in enumerate(rows):
        if not isinstance(item, dict) or not _check_exact_fields(
            item, OEM_DIVERGENCE_FIELDS, f"{where}.divergences[{i}]",
            frozenset({"code", "source_description_en"}), rep
        ):
            continue
        code = item.get("code")
        if not isinstance(code, str) or not DTC_CODE_RE.fullmatch(code):
            rep.fail("schema", f"{where}.divergences[{i}].code must be a SAE J2012 code")
        for key in ("source_description_en", "kb_description_en"):
            value = item.get(key)
            if key == "source_description_en" and (not isinstance(value, str) or not value.strip()):
                rep.fail("schema", f"{where}.divergences[{i}].source_description_en must be non-empty")
            elif value is not None and not isinstance(value, str):
                rep.fail("schema", f"{where}.divergences[{i}].{key} must be a string or null")


DEFECT_SEVERITIES: frozenset[str] = frozenset({"high", "medium", "low"})


def _validate_payload_kb_defect(payload: dict[str, Any], where: str, rep: Report) -> None:
    """A *measured* defect in our own vendored data (re-checked on every run)."""
    _check_exact_fields(payload, PAYLOAD_FIELDS["kb_defect"], where, PAYLOAD_FIELDS["kb_defect"], rep)
    code = payload.get("defect_code")
    # snake_case: this is the detector's identifier (DETECTORS registry key),
    # not a filename. The record's intake_id is the kebab-case part.
    if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", code):
        rep.fail("schema", f"{where}.defect_code must be snake_case detector id (got {code!r})")
    if payload.get("severity") not in DEFECT_SEVERITIES:
        rep.fail("schema", f"{where}.severity must be one of {sorted(DEFECT_SEVERITIES)}")
    for key in ("summary", "why_it_matters", "detector_expression", "target_file"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            rep.fail("schema", f"{where}.{key} must be a non-empty string")
    digest = payload.get("target_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        rep.fail("schema", f"{where}.target_sha256 must be a 64-char lowercase sha256 digest")
    count = payload.get("affected_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        rep.fail("schema", f"{where}.affected_count must be a non-negative integer")
    examples = payload.get("examples")
    if not isinstance(examples, list):
        rep.fail("schema", f"{where}.examples must be an array")
    elif any(not isinstance(item, dict) or not item for item in examples):
        rep.fail("schema", f"{where}.examples must be a list of non-empty objects")
    if isinstance(count, int) and count > 0 and not examples:
        rep.add("WARN", "defect", f"{where}: affected_count={count} but no verbatim example is staged")


SPN_REF_SOURCE_FIELDS: frozenset[str] = frozenset({"source_file", "sha256", "bytes"})


def _validate_payload_spn_reference(payload: dict[str, Any], where: str, rep: Report,
                                    confidence: Any = None) -> None:
    """An SPN mentioned in a PGN layout, with the evidence it was read from."""
    _check_exact_fields(payload, PAYLOAD_FIELDS["spn_reference"], where,
                        PAYLOAD_FIELDS["spn_reference"], rep)
    spn = payload.get("spn")
    if not isinstance(spn, int) or isinstance(spn, bool) or not 0 <= spn <= 524_287:
        rep.fail("schema", f"{where}.spn must be an integer in [0, 524287]")
    # names_en / units / evidence_text are text; resolutions and bit_lengths are
    # numbers; evidence_pgns is a list of PGN integers (checked below).
    for key in ("names_en", "units", "evidence_text"):
        value = payload.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            rep.fail("schema", f"{where}.{key} must be a list of non-empty strings")
    for key in ("resolutions", "bit_lengths"):
        value = payload.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, (int, float)) and not isinstance(item, bool) and item > 0 for item in value
        ):
            rep.fail("schema", f"{where}.{key} must be a list of positive numbers")
    names = payload.get("names_en") or []
    if not names:
        rep.fail("schema", f"{where}.names_en must not be empty (an SPN without a parameter name is unusable)")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        rep.fail("schema", f"{where}.sources must be a non-empty array of cited files")
    else:
        for i, item in enumerate(sources):
            if not isinstance(item, dict) or not _check_exact_fields(
                item, SPN_REF_SOURCE_FIELDS, f"{where}.sources[{i}]",
                frozenset({"source_file", "sha256"}), rep
            ):
                continue
            digest = item.get("sha256")
            if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                rep.fail("schema", f"{where}.sources[{i}].sha256 must be a 64-char lowercase digest")
            size = item.get("bytes")
            if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
                rep.fail("schema", f"{where}.sources[{i}].bytes must be a non-negative integer")
    pgns = payload.get("evidence_pgns") or []
    for i, pgn in enumerate(pgns):
        if not isinstance(pgn, int) or isinstance(pgn, bool) or not 0 <= pgn <= 262_143:
            rep.fail("schema", f"{where}.evidence_pgns[{i}] must be an integer in [0, 262143]")
    dbc_signals = payload.get("dbc_signals")
    if not isinstance(dbc_signals, list) or not all(
        isinstance(item, str) and item.strip() for item in dbc_signals
    ):
        rep.fail("schema", f"{where}.dbc_signals must be a list of non-empty strings")
    for key in ("kb_state", "kb_name", "kb_unit"):
        value = payload.get(key)
        if key == "kb_state":
            if value not in {"present", "absent"}:
                rep.fail("schema", f"{where}.kb_state must be 'present' or 'absent'")
        elif value is not None and not isinstance(value, str):
            rep.fail("schema", f"{where}.{key} must be a string or null")
    # The number may only come from the cited evidence text.
    if isinstance(spn, int):
        mentioned = any(f"SPN {spn}" in text for text in (payload.get("evidence_text") or []))
        if not mentioned:
            rep.add("WARN", "spn_evidence",
                    f"{where}: SPN {spn} kaynak metinlerinde görünmüyor — türetilmiş olabilir")
    # Two cited sources are a cross-checked statement, so the envelope must say so.
    sources = payload.get("sources")
    if isinstance(sources, list) and len(sources) >= 2 and confidence != "corroborated":
        rep.fail("provenance", f"{where}: {len(sources)} kaynak alıntılanmış ama confidence "
                               f"{confidence!r} — iki temsilli kayıt 'corroborated' olmalı")


def _validate_payload_provenance_gap(payload: dict[str, Any], where: str, rep: Report) -> None:
    """A source that shipped data points at but no provenance document records."""
    _check_exact_fields(payload, PAYLOAD_FIELDS["provenance_gap"], where,
                        PAYLOAD_FIELDS["provenance_gap"], rep)
    key = payload.get("source_key")
    if not isinstance(key, str) or not key.strip():
        rep.fail("schema", f"{where}.source_key must be a non-empty string")
    count = payload.get("occurrences")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        rep.fail("schema", f"{where}.occurrences must be a positive integer")
    for field in ("files", "fields", "sample_values", "sample_record_keys", "documented_in"):
        value = payload.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            rep.fail("schema", f"{where}.{field} must be a list of non-empty strings")
    if not payload.get("files"):
        rep.fail("schema", f"{where}.files must not be empty (which KB file carries the gap?)")
    if payload.get("licence_status") != "unresolved":
        rep.fail("schema", f"{where}.licence_status must be 'unresolved' — a resolved licence means "
                           f"the gap is closed and the record should be archived")
    if payload.get("documented_in"):
        rep.add("WARN", "provenance_gap",
                f"{where}: documented_in boş değil — kaynak belgelenmiş, kayıt arşivlenebilir")


PAYLOAD_VALIDATORS = {
    "dtc": _validate_payload_dtc,
    "kb_defect": _validate_payload_kb_defect,
    "provenance_gap": _validate_payload_provenance_gap,
    "spn_reference": _validate_payload_spn_reference,
    "spn_fmi": _validate_payload_spn,
    "pgn_layout": _validate_payload_pgn_layout,
    "oem_divergence": _validate_payload_oem_divergence,
    "case": _validate_payload_case,
    "oem_note": _validate_payload_oem_note,
    "trace": _validate_payload_trace,
}


def validate_envelope(payload: Any, where: str, rep: Report) -> Record | None:
    """Validate the intake envelope. Returns None when the record is rejected."""
    if not isinstance(payload, dict):
        rep.fail("schema", f"{where}: intake record must be a JSON object")
        return None
    if not _check_exact_fields(payload, ENVELOPE_FIELDS, where, ENVELOPE_FIELDS, rep):
        return None
    if payload.get("schema_version") != SCHEMA_VERSION:
        rep.fail("schema", f"{where}: unsupported schema_version {payload.get('schema_version')!r}")
    intake_id = payload.get("intake_id")
    if not isinstance(intake_id, str) or not INTAKE_ID_RE.fullmatch(intake_id):
        rep.fail("schema", f"{where}: intake_id must be kebab-case (got {intake_id!r})")
    record_type = payload.get("record_type")
    if record_type not in RECORD_TYPES:
        rep.fail("schema", f"{where}: record_type must be one of {sorted(RECORD_TYPES)}")
        return None
    if not isinstance(payload.get("submitted_at"), str) or not DATE_RE.fullmatch(payload["submitted_at"]):
        rep.fail("schema", f"{where}: submitted_at must be YYYY-MM-DD")
    submitter = payload.get("submitter")
    if not isinstance(submitter, dict) or not _check_exact_fields(
        submitter, SUBMITTER_FIELDS, f"{where}.submitter", frozenset({"type", "id"}), rep
    ):
        rep.fail("schema", f"{where}.submitter must be an object with type and id")
    else:
        if submitter.get("type") not in SUBMITTER_TYPES:
            rep.fail("schema", f"{where}.submitter.type must be one of {sorted(SUBMITTER_TYPES)}")
        if not isinstance(submitter.get("id"), str) or not submitter["id"].strip():
            rep.fail("schema", f"{where}.submitter.id must be a non-empty string")
        role = submitter.get("role")
        if role is not None and not isinstance(role, str):
            rep.fail("schema", f"{where}.submitter.role must be a string or null")
    source = _validate_source(payload.get("source"), where, rep)
    if payload.get("confidence") not in CONFIDENCE_LEVELS:
        rep.fail("schema", f"{where}: confidence must be one of {sorted(CONFIDENCE_LEVELS)} "
                           f"(operator_verified is not an intake state — verification happens at promotion)")
    draft = payload.get("draft")
    if not isinstance(draft, bool):
        rep.fail("schema", f"{where}: draft must be a boolean")
    knowledge_base = payload.get("knowledge_base")
    if knowledge_base is not None:
        rep.fail("binding", f"{where}: knowledge_base must be null — intake is a staging area and is "
                            f"never bound to the knowledge base")
    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        rep.fail("schema", f"{where}: notes must be a string or null")
    body_payload = payload.get("payload")
    if not isinstance(body_payload, dict):
        rep.fail("schema", f"{where}: payload must be an object")
        return None
    if record_type == "case" and draft is not True:
        rep.fail("draft", f"{where}: data/intake/cases/* is always draft: true — an unverified case "
                          f"may not claim to be verified")
    validator = PAYLOAD_VALIDATORS[record_type]
    if record_type == "spn_reference":
        # The corroboration rule needs the envelope's confidence, not the payload's.
        validator(body_payload, f"{where}.payload", rep, confidence=payload.get("confidence"))
    else:
        validator(body_payload, f"{where}.payload", rep)
    scan_text(json.dumps(payload, ensure_ascii=False), where, rep)
    if source is None or not isinstance(intake_id, str) or not isinstance(draft, bool):
        return None
    return Record(
        intake_id=intake_id,
        record_type=record_type,
        path=Path(),
        rel_path=where,
        payload=body_payload,
        source=source,
        draft=draft,
    )


META_BLOCK_RE = re.compile(r"\A<!--\s*intake-meta\s*\n(.*?)\n-->\s*", re.DOTALL)


def split_oem_note(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split an OEM note into its JSON meta block and the markdown body."""
    match = META_BLOCK_RE.match(text)
    if not match:
        return None, text
    try:
        meta = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None, text
    return meta, text[match.end():]


# --------------------------------------------------------------------------- #
# trace frame files
# --------------------------------------------------------------------------- #
def validate_frame_file(trace: Record, traces_dir: Path, repo_root: Path,
                        manifest: Manifest, max_inline_bytes: int, rep: Report) -> None:
    """Verify the frame file a trace meta points at (hash, size, in-Git policy)."""
    payload = trace.payload
    frame_file = payload.get("frame_file")
    if not isinstance(frame_file, str) or not frame_file.strip():
        return
    frame_path = traces_dir / frame_file
    # MANIFEST paths are intake-relative, so the lookup key must be too.
    rel_path = frame_path.relative_to(traces_dir.parent).as_posix()
    declared_size = payload.get("frame_file_bytes")
    in_git = payload.get("in_git")

    if in_git is False:
        if frame_path.exists() and git_tracked(repo_root, rel_path):
            rep.fail("trace", f"{trace.rel_path}: in_git=false but {rel_path} is tracked by git — "
                              f"large traces never enter the repository")
        elif frame_path.exists():
            rep.add("WARN", "trace", f"{trace.rel_path}: {rel_path} is declared external but a local copy "
                                     f"exists — delete it or stage it as a proper in-git record")
        ext = manifest.external.get(trace.intake_id)
        if ext is None:
            rep.fail("manifest", f"{trace.rel_path}: external trace has no MANIFEST row in the "
                                 f"'Git'e girmeyen trace'ler' table (hash + location are mandatory)")
        else:
            if ext.sha256 != payload.get("frame_file_sha256"):
                rep.fail("manifest", f"{trace.rel_path}: MANIFEST sha256 {ext.sha256[:12]}… != "
                                     f"declared {str(payload.get('frame_file_sha256'))[:12]}…")
            if ext.size != declared_size:
                rep.fail("manifest", f"{trace.rel_path}: MANIFEST bytes {ext.size} != declared {declared_size}")
            if not ext.location.strip():
                rep.fail("manifest", f"{trace.rel_path}: MANIFEST location is blank")
        rep.add("INFO", "trace_external",
                f"{trace.rel_path}: {frame_file} lives outside git ({(declared_size or 0) / 1024:.0f} KiB) — "
                f"hash is unverified locally by design, verify it before promotion")
        rep.bump("traces_external")
        return

    if not frame_path.is_file():
        rep.fail("trace", f"{trace.rel_path}: frame_file '{frame_file}' is missing from {traces_dir.name}/")
        return
    actual_size = frame_path.stat().st_size
    if actual_size != declared_size:
        rep.fail("trace", f"{trace.rel_path}: frame_file_bytes {declared_size} != on-disk {actual_size}")
    if actual_size > max_inline_bytes:
        rep.fail("trace", f"{trace.rel_path}: {frame_file} is {actual_size} B > inline ceiling "
                          f"{max_inline_bytes} B — such a trace must not be committed; record it in "
                          f"MANIFEST.md by sha256 + location only")
    actual_hash = sha256_of(frame_path)
    if actual_hash != payload.get("frame_file_sha256"):
        rep.fail("trace", f"{trace.rel_path}: frame_file_sha256 does not match {frame_file}")
    if manifest.entries.get(rel_path) is None:
        rep.fail("manifest", f"{trace.rel_path}: {rel_path} has no MANIFEST row")
    elif manifest.entries[rel_path].sha256 != actual_hash:
        rep.fail("manifest", f"{trace.rel_path}: MANIFEST sha256 for {rel_path} does not match the file")
    _check_frame_content(trace, frame_path, rep)
    rep.bump("traces_inline")


def _check_frame_content(trace: Record, frame_path: Path, rep: Report) -> None:
    """Structural check of the small in-git frame containers (best effort)."""
    fmt = trace.payload.get("format")
    declared_frames = trace.payload.get("frame_count")
    where = trace.rel_path
    try:
        if fmt == "can_frames_json":
            blob = _load_json(frame_path)
            frames = blob.get("frames") if isinstance(blob, dict) else None
            if not isinstance(frames, list):
                rep.fail("trace", f"{where}: can_frames_json needs a top-level 'frames' array")
                return
            if isinstance(declared_frames, int) and len(frames) != declared_frames:
                rep.fail("trace", f"{where}: frame_count {declared_frames} != {len(frames)} frames in file")
            for i, frame in enumerate(frames[:200]):
                if not isinstance(frame, dict) or set(frame) != {"t", "channel", "id", "dlc", "data"}:
                    rep.fail("trace", f"{where}.frames[{i}]: must have exactly t/channel/id/dlc/data")
                    break
                if not CAN_ID_RE.fullmatch(str(frame.get("id"))):
                    rep.fail("trace", f"{where}.frames[{i}].id must look like 0x18FEF100")
                    break
                dlc, data = frame.get("dlc"), str(frame.get("data", ""))
                if not isinstance(dlc, int) or not HEX_DATA_RE.fullmatch(data) or len(data) != dlc * 2:
                    rep.fail("trace", f"{where}.frames[{i}]: dlc/data mismatch (data must be {dlc} hex bytes)")
                    break
        elif fmt in {"can_frames_jsonl", "nmea2000_jsonl"}:
            lines = [ln for ln in frame_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            for i, line in enumerate(lines):
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    rep.fail("trace", f"{where}: line {i + 1} is not valid JSON ({exc.msg})")
                    break
            if isinstance(declared_frames, int) and len(lines) != declared_frames:
                rep.fail("trace", f"{where}: frame_count {declared_frames} != {len(lines)} JSONL records")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        rep.fail("trace", f"{where}: cannot parse frame file ({exc})")
    scan_text(frame_path.read_text(encoding="utf-8", errors="replace"), f"{where} (frames)",
              rep, skip_hex_fields=True)


# --------------------------------------------------------------------------- #
# MANIFEST
# --------------------------------------------------------------------------- #
def parse_manifest(path: Path, rep: Report) -> Manifest:
    """Parse MANIFEST.md tables (stdlib only, no markdown dependency)."""
    manifest = Manifest()
    if not path.is_file():
        rep.fail("manifest", f"{path.name} is missing")
        return manifest
    in_external = False
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line.startswith("##"):
            in_external = "girmeyen" in line.lower() or "external" in line.lower()
            continue
        if not line.startswith("|"):
            continue
        cells = [_strip_markup(c) for c in line.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} for c in cells):  # separator / blank row
            continue
        if cells[0].lower() in {"intake_id", "intake id"}:  # table header
            continue
        if in_external:
            if len(cells) < 5:
                rep.fail("manifest", f"MANIFEST.md:{lineno}: external trace row needs 5 columns")
                continue
            intake_id, fmt, size, digest, location = cells[:5]
            try:
                size_int = int(size)
            except ValueError:
                rep.fail("manifest", f"MANIFEST.md:{lineno}: bytes '{size}' is not an integer")
                continue
            if not SHA256_RE.fullmatch(digest):
                rep.fail("manifest", f"MANIFEST.md:{lineno}: sha256 '{digest[:16]}…' is not a 64-char digest")
                continue
            if intake_id in manifest.external:
                rep.fail("manifest", f"MANIFEST.md:{lineno}: duplicate external row for {intake_id}")
            manifest.external[intake_id] = ExternalTraceEntry(intake_id, fmt, size_int, digest, location)
            continue
        if len(cells) < 7:
            rep.fail("manifest", f"MANIFEST.md:{lineno}: row needs 7 columns "
                                 f"(intake_id/path/kind/bytes/sha256/licence/source)")
            continue
        intake_id, rel_path, kind, size, digest, licence, source = cells[:7]
        if rel_path.startswith("data/intake/"):
            rel_path = rel_path[len("data/intake/"):]
        try:
            size_int = int(size)
        except ValueError:
            rep.fail("manifest", f"MANIFEST.md:{lineno}: bytes '{size}' is not an integer")
            continue
        if not SHA256_RE.fullmatch(digest):
            rep.fail("manifest", f"MANIFEST.md:{lineno}: sha256 '{digest[:16]}…' is not a 64-char digest")
            continue
        if rel_path in manifest.entries:
            rep.fail("manifest", f"MANIFEST.md:{lineno}: duplicate row for {rel_path}")
            continue
        # NOTE: one intake_id may own several rows (a trace record owns its
        # frame file too). Two *records* sharing an id is rejected earlier, in
        # load_records, where the record identity is known.
        manifest.entries[rel_path] = ManifestEntry(intake_id, rel_path, kind, size_int, digest, licence, source)
    return manifest


def check_manifest_against_files(records: list[Record], intake_dir: Path, manifest: Manifest,
                                 rep: Report) -> None:
    """MANIFEST <-> file <-> hash three-way check."""
    expected: dict[str, Record] = {}
    for record in records:
        for rel in record.manifest_paths():
            expected[rel] = record

    for rel, record in expected.items():
        entry = manifest.entries.get(rel)
        if entry is None:
            rep.fail("manifest", f"{rel} has no MANIFEST row (every intake artefact must be listed)")
            continue
        if entry.intake_id != record.intake_id:
            rep.fail("manifest", f"{rel}: MANIFEST intake_id '{entry.intake_id}' != record '{record.intake_id}'")
        path = intake_dir / rel
        if not path.is_file():
            rep.fail("manifest", f"{rel}: MANIFEST row points at a missing file")
            continue
        size = path.stat().st_size
        if entry.size != size:
            rep.fail("manifest", f"{rel}: MANIFEST bytes {entry.size} != on-disk {size}")
        digest = sha256_of(path)
        if entry.sha256 != digest:
            rep.fail("manifest", f"{rel}: MANIFEST sha256 {entry.sha256[:12]}… != on-disk {digest[:12]}…")
        licence = str(record.source.get("licence") or "")
        if entry.licence != licence:
            rep.fail("manifest", f"{rel}: MANIFEST licence '{entry.licence}' != record licence '{licence}'")

    for rel in manifest.entries:
        if rel not in expected:
            if (intake_dir / rel).exists():
                rep.fail("manifest", f"{rel} is listed in MANIFEST but no record produced it")
            else:
                rep.fail("manifest", f"{rel}: MANIFEST row points at a missing file")
    rep.metrics["manifest_rows"] = len(manifest.entries)
    rep.metrics["manifest_external_rows"] = len(manifest.external)


def _record_rel(record_path: Path, intake_dir: Path) -> str:
    return record_path.relative_to(intake_dir).as_posix()


# --------------------------------------------------------------------------- #
# walk + record loading
# --------------------------------------------------------------------------- #
def load_records(intake_dir: Path, rep: Report) -> tuple[list[Record], list[Path]]:
    """Parse and validate every intake record under ``intake_dir``."""
    records: list[Record] = []
    frame_files: list[Path] = []
    seen_ids: dict[str, str] = {}
    if not intake_dir.is_dir():
        rep.fail("layout", f"{intake_dir} does not exist")
        return records, frame_files

    for path in sorted(intake_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = _record_rel(path, intake_dir)
        if path.name == ".gitkeep":
            # Directory placeholder: not a record, not MANIFEST-listed.
            continue
        if path.name in NON_RECORD_NAMES and path.parent == intake_dir:
            continue
        if path.parent == intake_dir and NOTICE_RE.match(path.name):
            continue  # licence notice: tracked, never hashed as a record
        if TEMPLATE_DIR in path.parts:
            continue
        if ARTEFACT_RE.search(path.name):
            rep.fail("layout", f"{rel}: stray artefact/backup file — the intake area is append-only, clean it up")
            continue
        parent = path.parent.relative_to(intake_dir).as_posix()
        if parent not in RECORD_DIRS.values():
            rep.fail("layout", f"{rel}: '{parent}' is not an intake directory "
                               f"({sorted(set(RECORD_DIRS.values()))})")
            continue

        if parent == "traces" and path.name.endswith(".meta.json"):
            text = path.read_text(encoding="utf-8", errors="replace")
            record = _load_record(path, text, rel, "trace", rep)
            if record is None:
                continue
            _register(records, seen_ids, record, rep)
            continue
        if parent == "traces":
            if path.suffix in {".json", ".jsonl"}:
                frame_files.append(path)
            else:
                rep.fail("layout", f"{rel}: raw trace containers ({path.suffix}) must not be committed; "
                                   f"keep the file outside git and record sha256 + location in MANIFEST.md")
            continue
        if parent == "oem_notes":
            text = path.read_text(encoding="utf-8", errors="replace")
            meta, body = split_oem_note(text)
            if meta is None:
                rep.fail("schema", f"{rel}: an OEM note must start with a <!-- intake-meta ... --> JSON block")
                continue
            record = _load_record(path, meta, rel, "oem_note", rep)
            if record is None:
                continue
            record.body = body
            if not body.strip():
                rep.fail("schema", f"{rel}: OEM note body is empty")
            scan_text(body, f"{rel} (body)", rep)
            _register(records, seen_ids, record, rep)
            continue
        if path.suffix != ".json":
            rep.fail("layout", f"{rel}: only .json records are accepted in {parent}/ (trace frames: .json/.jsonl)")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        record = _load_record(path, text, rel, None, rep)
        if record is None:
            continue
        if RECORD_DIRS.get(record.record_type) != parent:
            rep.fail("layout", f"{rel}: record_type '{record.record_type}' does not belong in {parent}/")
        _register(records, seen_ids, record, rep)

    for frame in frame_files:
        meta = frame.with_suffix("").with_suffix(".meta.json")
        if not meta.is_file():
            rep.fail("layout", f"{_record_rel(frame, intake_dir)}: every frame file needs a "
                               f"{frame.stem}.meta.json sidecar")
    return records, frame_files


def _load_record(path: Path, text: Any, rel: str, forced_type: str | None, rep: Report) -> Record | None:
    if isinstance(text, str):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            rep.fail("schema", f"{rel}: invalid JSON ({exc.msg} at line {exc.lineno})")
            return None
    else:
        payload = text  # already parsed (the OEM-note meta block)
    record = validate_envelope(payload, rel, rep)
    if record is None:
        return None
    if forced_type is not None and record.record_type != forced_type:
        rep.fail("layout", f"{rel}: record_type must be '{forced_type}' in this directory")
        return None
    record.path = path
    record.rel_path = rel
    rep.bump(f"records_{record.record_type}")
    return record


def _register(records: list[Record], seen_ids: dict[str, str], record: Record, rep: Report) -> None:
    if record.intake_id in seen_ids:
        rep.fail("schema", f"{record.rel_path}: intake_id '{record.intake_id}' is already used by "
                           f"{seen_ids[record.intake_id]}")
        return
    seen_ids[record.intake_id] = record.rel_path
    records.append(record)


# --------------------------------------------------------------------------- #
# conflict report (read-only)
# --------------------------------------------------------------------------- #
def _provenance_gaps(repo_root: Path) -> dict[str, Any] | None:
    """Per-source traceability measurement, if the sibling tool is importable."""
    try:
        from scripts.intake_kb_defects import measure_provenance_gaps  # type: ignore[import-not-found]
    except ImportError:
        try:
            from intake_kb_defects import measure_provenance_gaps  # type: ignore[import-not-found]
        except ImportError:
            return None
    return measure_provenance_gaps(repo_root)


def _kb_detectors() -> dict[str, Any]:
    """The defect detectors, if the sibling tool is importable (else: none)."""
    try:
        from scripts.intake_kb_defects import DETECTORS  # type: ignore[import-not-found]
    except ImportError:
        try:
            from intake_kb_defects import DETECTORS  # type: ignore[import-not-found,no-redef]
        except ImportError:
            return {}
    return dict(DETECTORS)


def report_conflicts(records: list[Record], repo_root: Path, rep: Report) -> None:
    """Compare intake records with the existing knowledge base. Report only."""
    dtc_db = repo_root / DIAGNOSTICS / "dtc_database.json"
    oem_db = repo_root / DIAGNOSTICS / "dtc_database_oem_layer.json"
    j1939_db = repo_root / DIAGNOSTICS / "j1939_spn_fmi_database.json"
    oem_layer = repo_root / DIAGNOSTICS / "dtc_database_oem_layer.json"
    golden_dir = repo_root / "data" / "golden_traces" / "cases"
    if not dtc_db.is_file() or not j1939_db.is_file():
        rep.add("INFO", "conflict", "knowledge base databases not found — conflict report skipped")
        return

    dtc_records = _load_json(dtc_db)
    j1939 = _load_json(j1939_db)
    spns = j1939.get("spns", {})
    oem_codes: dict[str, Any] = {}
    if oem_db.is_file():
        oem = _load_json(oem_db)
        oem_codes = oem.get("codes", {}) if isinstance(oem, dict) else {}
    golden_ids = {p.stem for p in golden_dir.glob("*.json")} if golden_dir.is_dir() else set()
    oem_layer_payload = _load_json(oem_layer) if oem_layer.is_file() else {}
    # Measured once per run: this walks every KB JSON file, and the staged gap
    # records must not turn a single gate run into dozens of full scans.
    provenance_gaps = _provenance_gaps(repo_root)

    overlaps = 0
    candidates = 0

    def check_oem_divergence(record: Record) -> None:
        """Re-measure the staged divergence against the OEM layer (report only)."""
        nonlocal overlaps, candidates
        where = record.rel_path
        rows = record.payload.get("divergences") or []
        if not isinstance(rows, list):
            return
        rep.metrics["oem_divergence_rows"] = rep.metrics.get("oem_divergence_rows", 0) + len(rows)
        if not oem_layer.is_file():
            rep.add("INFO", "conflict", f"{where}: OEM layer not found, divergence not re-checked")
            return
        stored_codes = (oem_layer_payload.get("codes") or {})
        still_divergent = 0
        unknown_codes = 0
        for item in rows:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            rec = stored_codes.get(code)
            if rec is None:
                unknown_codes += 1
                continue
            current = str(((rec.get("description") or {}).get("en") or "")).strip().lower()
            source_text = str(item.get("source_description_en") or "").strip().lower()
            if current != source_text:
                still_divergent += 1
        if still_divergent:
            overlaps += still_divergent
            rep.add("INFO", "kb_divergence",
                    f"{where}: {still_divergent}/{len(rows)} per-manufacturer descriptions still disagree "
                    f"with dtc_database_oem_layer.json — evidence staged, nothing overwritten")
        if unknown_codes:
            candidates += unknown_codes
            rep.add("INFO", "kb_new", f"{where}: {unknown_codes} codes are absent from the OEM layer")
        rep.metrics["oem_divergence_open"] = rep.metrics.get("oem_divergence_open", 0) + still_divergent

    def check_spn_reference(record: Record) -> None:
        """Re-measure one SPN reference against the live J1939 database."""
        nonlocal overlaps, candidates
        spn = record.payload.get("spn")
        if not isinstance(spn, int):
            return
        where = record.rel_path
        current = spns.get(f"SPN_{spn}")
        staged_state = record.payload.get("kb_state")
        actual_state = "present" if current else "absent"
        rep.metrics[f"spn_ref_{actual_state}"] = rep.metrics.get(f"spn_ref_{actual_state}", 0) + 1
        if staged_state != actual_state:
            rep.add("WARN", "kb_drift",
                    f"{where}: SPN {spn} durumu değişti ({staged_state} → {actual_state}) — "
                    f"kaydı yeniden üret")
        if current is None:
            candidates += 1
            rep.add("INFO", "kb_new", f"{where}: SPN {spn} bilgi tabanında yok — terfi adayı "
                                      f"(kanıt: {len(record.payload.get('evidence_pgns') or [])} PGN, "
                                      f"{len(record.payload.get('names_en') or [])} ad)")
        else:
            overlaps += 1
            staged_names = {str(n).strip().lower() for n in (record.payload.get("names_en") or [])}
            kb_name = str(current.get("name") or "").strip().lower()
            if kb_name and kb_name not in staged_names:
                # An abbreviation difference is normal (canboat uses short field
                # names); only a *unit* conflict or a diagnosis sentence is news.
                rep.metrics["spn_name_variant"] = rep.metrics.get("spn_name_variant", 0) + 1
                staged_units = {str(u).strip().lower() for u in (record.payload.get("units") or [])}
                kb_unit = str(current.get("unit") or "").strip().lower()
                if staged_units and kb_unit and kb_unit not in staged_units:
                    rep.add("INFO", "kb_unit_conflict",
                            f"{where}: SPN {spn} birimi çelişiyor — upstream {sorted(staged_units)} "
                            f"vs KB {kb_unit!r}")
                if FMI_SENTENCE_IN_NAME.search(str(current.get("name") or "")):
                    rep.add("INFO", "kb_name_defect",
                            f"{where}: SPN {spn} KB adı bir tanım cümlesi (bkz. defects/ kaydı)")

    def check_provenance_gap(record: Record) -> None:
        """Re-measure one undocumented source key against the live data."""
        nonlocal overlaps, candidates
        where = record.rel_path
        key = record.payload.get("source_key")
        if not isinstance(key, str):
            return
        if provenance_gaps is None:
            rep.add("INFO", "provenance_gap", f"{where}: gap ölçüm aracı yok, yeniden ölçülemedi")
            return
        entry = provenance_gaps.get(key)
        staged = record.payload.get("occurrences")
        current = entry["occurrences"] if entry else 0
        rep.metrics["provenance_gap_occurrences"] = rep.metrics.get("provenance_gap_occurrences", 0) + current
        if current == 0:
            overlaps += 1
            rep.add("INFO", "provenance_closed",
                    f"{where}: '{key}' artık hiçbir provenance belgesinde olmayan bir alanda geçmiyor — "
                    f"kaynak belgelenmiş veya alan kaldırılmış, kayıt arşivlenebilir")
        elif current == staged:
            candidates += 1
            rep.add("INFO", "provenance_open",
                    f"{where}: '{key}' hâlâ {current} yerde belgelenmemiş olarak işaret ediliyor")
        else:
            rep.add("WARN", "provenance_drift",
                    f"{where}: '{key}' ölçümü {staged} → {current} (kaynak eklendi ya da alan kaldırıldı)")

    def check_kb_defect(record: Record) -> None:
        """Re-run the staged detector and compare with the recorded measurement."""
        nonlocal overlaps, candidates
        where = record.rel_path
        code = record.payload.get("defect_code")
        target = record.payload.get("target_file")
        if not isinstance(code, str) or not isinstance(target, str):
            return
        detectors = _kb_detectors()
        detector = detectors.get(code)
        if detector is None:
            rep.add("WARN", "defect", f"{where}: detector '{code}' is unknown to "
                                       f"scripts/intake_kb_defects.py — the finding cannot be re-checked")
            return
        staged_hash = record.payload.get("target_sha256")
        try:
            finding = detector(repo_root)
        except (OSError, ValueError, KeyError) as exc:
            rep.add("WARN", "defect", f"{where}: detector '{code}' failed on the current data ({exc})")
            return
        target_path = repo_root / target
        if not target_path.is_file():
            rep.add("WARN", "defect_target_missing",
                    f"{where}: ölçülen dosya yok ({target}) — dedektör çalıştırılamıyor")
            return
        current_hash = sha256_of(target_path)
        if current_hash != staged_hash:
            rep.add("WARN", "defect_stale", f"{where}: {target} sha256 değişti — ölçüm bayat, "
                                             f"kaydı yeniden üret (scripts/intake_kb_defects.py --stage --apply)")
        staged_count = record.payload.get("affected_count")
        current_count = finding.get("affected_count")
        rep.metrics[f"defect_{code}"] = current_count
        if current_count == staged_count:
            rep.add("INFO", "defect_open", f"{where}: '{code}' ölçümü hâlâ {current_count} — açık")
        elif current_count == 0:
            rep.add("INFO", "defect_closed", f"{where}: '{code}' artık 0 etkilenen kayıt — "
                                              f"kayıt arşivlenebilir")
        else:
            rep.add("WARN", "defect_drift", f"{where}: '{code}' ölçümü {staged_count} → {current_count} "
                                             f"(dedektör ya da veri değişti)")

    def check_pgn_layout(record: Record) -> None:
        """Compare a staged PGN layout with the vendored PGN catalogue (report only)."""
        nonlocal overlaps, candidates
        pgn = record.payload.get("pgn")
        where = record.rel_path
        if not isinstance(pgn, int):
            return
        catalogue = repo_root / DIAGNOSTICS / "canboat_pgn_reference.json"
        known_j1939: set[int] = set()
        known_any: set[int] = set()
        if catalogue.is_file():
            blob = _load_json(catalogue)
            known_j1939 = {int(k) for k in (blob.get("j1939") or {})}
            known_any = {int(k) for k in (blob.get("pgns_by_number") or {})}
        if pgn in known_j1939:
            overlaps += 1
            rep.add("INFO", "kb_overlap", f"{where}: PGN {pgn} is already vendored in "
                                          f"canboat_pgn_reference.json (j1939 block) — nothing is overwritten")
        elif pgn in known_any:
            overlaps += 1
            rep.add("INFO", "kb_overlap", f"{where}: PGN {pgn} already exists in the NMEA-2000 catalogue")
        else:
            candidates += 1
            rep.add("INFO", "kb_new", f"{where}: PGN {pgn} is unknown to canboat_pgn_reference.json "
                                      f"(promotion candidate, not merged)")
        hits = 0
        unknown_spns: set[int] = set()
        for item in record.payload.get("fields") or []:
            spn = item.get("spn") if isinstance(item, dict) else None
            if not isinstance(spn, int):
                continue
            hits += 1
            if f"SPN_{spn}" not in spns:
                unknown_spns.add(spn)
        if hits:
            rep.metrics["pgn_layout_spn_refs"] = rep.metrics.get("pgn_layout_spn_refs", 0) + hits
        if unknown_spns:
            listed = ", ".join(str(s) for s in sorted(unknown_spns)[:8])
            more = "" if len(unknown_spns) <= 8 else f" (+{len(unknown_spns) - 8} more)"
            rep.add("WARN", "pgn_spn_unknown", f"{where}: SPN referansları KB'de yok: {listed}{more}")

    def check_dtc(code: str, where: str) -> None:
        nonlocal overlaps, candidates
        if not isinstance(code, str) or not DTC_CODE_RE.fullmatch(code):
            return
        if code in dtc_records:
            overlaps += 1
            title = str(dtc_records[code].get("title") or "")[:60]
            rep.add("INFO", "kb_overlap", f"{where}: DTC {code} already exists in "
                                          f"data/diagnostics/dtc_database.json ('{title}') — nothing is overwritten")
        elif code in oem_codes:
            overlaps += 1
            rep.add("INFO", "kb_overlap", f"{where}: DTC {code} already exists in the OEM layer")
        else:
            candidates += 1
            rep.add("INFO", "kb_new", f"{where}: DTC {code} is unknown to the knowledge base "
                                      f"(promotion candidate, not merged)")

    for record in records:
        if record.record_type == "dtc":
            check_dtc(record.payload.get("code"), record.rel_path)
        elif record.record_type == "case":
            for item in record.payload.get("dtcs") or []:
                if isinstance(item, dict):
                    check_dtc(str(item.get("code")), record.rel_path)
            case_id = str(record.payload.get("case_id") or "")
            if case_id and case_id in golden_ids:
                overlaps += 1
                rep.add("INFO", "kb_overlap",
                        f"{record.rel_path}: case_id '{case_id}' already exists in data/golden_traces/cases/ — "
                        f"this intake record is a duplicate of a promoted case")
        elif record.record_type == "oem_note":
            for code in record.payload.get("related_dtcs") or []:
                check_dtc(str(code), record.rel_path)
        elif record.record_type == "spn_fmi":
            spn = record.payload.get("spn")
            fmi = record.payload.get("fmi")
            key = f"SPN_{spn}" if isinstance(spn, int) else None
            rec = spns.get(key) if key else None
            if rec is None:
                candidates += 1
                rep.add("INFO", "kb_new", f"{record.rel_path}: {key} is unknown to j1939_spn_fmi_database.json "
                                          f"(promotion candidate, not merged)")
            elif str(fmi) in (rec.get("fault_matrix") or {}):
                overlaps += 1
                rep.add("INFO", "kb_overlap", f"{record.rel_path}: SPN {spn} / FMI {fmi} already exists in the "
                                              f"J1939 fault matrix — nothing is overwritten")
            else:
                candidates += 1
                rep.add("INFO", "kb_new", f"{record.rel_path}: SPN {spn} exists but FMI {fmi} is not in its "
                                          f"fault matrix (promotion candidate, not merged)")
        elif record.record_type == "pgn_layout":
            check_pgn_layout(record)
        elif record.record_type == "oem_divergence":
            check_oem_divergence(record)
        elif record.record_type == "kb_defect":
            check_kb_defect(record)
        elif record.record_type == "spn_reference":
            check_spn_reference(record)
        elif record.record_type == "provenance_gap":
            check_provenance_gap(record)
    # A detector that found something but has no staged record would silently
    # vanish from the queue: report it so the register stays honest.
    detectors = _kb_detectors()
    staged_codes = {r.payload.get("defect_code") for r in records if r.record_type == "kb_defect"}
    for code in sorted(set(detectors) - {c for c in staged_codes if isinstance(c, str)}):
        rep.add("INFO", "defect_unstaged", f"'{code}' dedektörü var ama data/intake/defects/ içinde "
                                           f"kaydı yok — sahalayın (--stage --apply)")
    rep.metrics["kb_overlaps"] = overlaps
    rep.metrics["kb_new_candidates"] = candidates


# --------------------------------------------------------------------------- #
# trace authoring helper
# --------------------------------------------------------------------------- #
def trace_meta_fields(frame_path: Path) -> dict[str, Any]:
    """Compute the ``payload`` fields a trace ``.meta.json`` must carry.

    Used when staging a capture (``--print-trace-meta``) so the declared
    ``frame_file_sha256``/``frame_file_bytes`` are never typed by hand.
    """
    raw = frame_path.read_bytes()
    fmt = "can_frames_jsonl" if frame_path.suffix == ".jsonl" else "can_frames_json"
    frame_count = 0
    try:
        if fmt == "can_frames_jsonl":
            frame_count = sum(1 for line in raw.decode("utf-8").splitlines() if line.strip())
        else:
            blob = json.loads(raw.decode("utf-8"))
            frames = blob.get("frames") if isinstance(blob, dict) else None
            frame_count = len(frames) if isinstance(frames, list) else 0
    except (ValueError, UnicodeDecodeError):
        frame_count = 0
    return {
        "frame_file": frame_path.name,
        "frame_file_sha256": hashlib.sha256(raw).hexdigest(),
        "frame_file_bytes": len(raw),
        "format": fmt,
        "in_git": True,
        "external_location": None,
        "started_at": "1970-01-01T00:00:00Z",
        "duration_s": None,
        "channel_count": 1,
        "frame_count": frame_count,
        "bus": None,
        "vin_masked": None,
    }


# --------------------------------------------------------------------------- #
# quarantine self-consistency (protects previous cleanup work from regressing)
# --------------------------------------------------------------------------- #
QUARANTINE_DIR = DIAGNOSTICS / "quarantine"


def check_quarantine_invariants(repo_root: Path, rep: Report) -> None:
    """Re-verify the repo's own quarantine audits against the shipped data.

    ``data/diagnostics/quarantine/`` holds four audits whose claims are
    checkable: nodes that were recovered into the root-cause graph, SPN shell
    rows that were removed, and LLM-derived blocks whose sha256 must not appear
    in the shipped database. A later merge that reintroduces any of them is a
    regression nobody would notice by reading prose, so it is a gate.
    """
    quarantine = repo_root / QUARANTINE_DIR
    graph_file = repo_root / DIAGNOSTICS / "root_cause_graph.json"
    spn_file = repo_root / DIAGNOSTICS / "j1939_spn_fmi_database.json"
    if not quarantine.is_dir() or not graph_file.is_file() or not spn_file.is_file():
        rep.add("INFO", "quarantine", "karantina/graf/SPN dosyaları yok — değişmez denetimi atlandı")
        return
    graph_ids = {node.get("id") for node in (_load_json(graph_file).get("nodes") or [])
                 if isinstance(node, dict)}
    spns = set(_load_json(spn_file).get("spns") or {})
    checked = 0

    seed_audit = quarantine / "t21_seed_audit.json"
    if seed_audit.is_file():
        nodes = (_load_json(seed_audit).get("nodes") or [])
        expected = [n.get("id") for n in nodes if n.get("verdict") == "already_in_graph"]
        unexpected = [n.get("id") for n in nodes if n.get("verdict") == "not_recovered"]
        checked += len(nodes)
        missing = [i for i in expected if i not in graph_ids]
        leaked = [i for i in unexpected if i in graph_ids]
        if missing:
            rep.fail("quarantine", f"{len(missing)} tohum düğüm kayıtta 'already_in_graph' ama grafta yok: "
                                   f"{missing[:5]}")
        if leaked:
            rep.fail("quarantine", f"{len(leaked)} düğüm kayıtta 'not_recovered' ama grafta var: "
                                   f"{leaked[:5]}")

    shell = quarantine / "t2_4_sitrak_shell_rows.json"
    if shell.is_file():
        keys = list((_load_json(shell).get("records") or {}))
        checked += len(keys)
        leaked = [k for k in keys if k in spns]
        if leaked:
            rep.fail("quarantine", f"{len(leaked)} karantina edilen kabuk satırı anahtarı DB'de geri gelmiş: "
                                   f"{leaked[:5]}")

    blocks = quarantine / "dtcdocs_llm_blocks.json"
    if blocks.is_file():
        digests = [r.get("block_sha256") for r in (_load_json(blocks).get("records") or [])
                   if isinstance(r, dict) and r.get("block_sha256")]
        checked += len(digests)
        if digests:
            db_text = spn_file.read_text(encoding="utf-8", errors="replace")
            leaked = [d for d in digests if d in db_text]
            if leaked:
                rep.fail("quarantine", f"{len(leaked)} karantina edilen blok özeti DB metninde bulundu: "
                                       f"{leaked[:3]}")

    rep.metrics["quarantine_invariants_checked"] = checked
    rep.add("INFO", "quarantine", f"{checked} karantina iddiası yeniden doğrulandı — "
                                 f"geri gelen temizlik izi yok")


# --------------------------------------------------------------------------- #
# MANIFEST sync (the only writer inside data/intake/)
# --------------------------------------------------------------------------- #
MANIFEST_HEADER = """# MANIFEST — `data/intake/` kaynak kaydı

`data/intake/` altındaki **her** artefaktın kaynağı, lisansı, bayt sayısı ve
`sha256` özeti burada zorunludur. Doğrulayıcı
(`python scripts/validate_intake.py`) tabloyu diskle karşılaştırır: eksik
satır, yanlış hash, yanlış lisans veya iki kaydın aynı `intake_id`'yi
kullanması **FAIL**'dir.

Bu tablo elle yazılmaz: `python scripts/validate_intake.py --sync-manifest
--apply` tüm kayıtlardan yeniden üretir. Bu komut `data/intake/` içindeki tek
yazıcıdır; `data/diagnostics` ve `data/golden_traces` içine **asla** yazmaz.

Kurallar ve terfi adımları: `data/intake/README.md`.

- `kind` değerleri: `dtc`, `spn_fmi`, `pgn_layout`, `case`, `oem_note`,
  `trace`, `trace_frames` (bir trace'in kare dosyası).
- `licence` kaydın kendi `source.licence` değeriyle **birebir** aynı olmalıdır
  (kapalı liste: `ALLOWED_LICENCES`; belirsiz lisans reddedilir).
- `_templates/` altındaki şablonlar kayıt değildir; buraya yazılmaz.
- Satır biçimi örnekleri README § "MANIFEST satır biçimi" bölümündedir.

## Git'e giren kayıtlar

| intake_id | path | kind | bytes | sha256 | licence | source |
|---|---|---|---|---|---|---|
"""

MANIFEST_EXTERNAL_HEADER = """
## Git'e girmeyen trace'ler (yalnız hash + konum)

Büyük yakalamalar (>`1 MiB`) depoya girmez; yalnız `sha256` + konum burada
tutulur. `sha256` terfi öncesi mutlaka doğrulanır.

| intake_id | format | bytes | sha256 | location |
|---|---|---|---|---|
"""

MANIFEST_TAIL = """
## Değişiklik günlüğü

Kayda alınan her satır için: tarih, ekleyen, terfi kararı (ör. “P0301 üretici
metnine göre zaten mevcut — terfi yok”). Bu günlük bilgi tabanı değildir;
`data/PROVENANCE.md` ve `data/diagnostics/PROVENANCE.md` değiştirilmeden
burada bırakılır.
"""

KIND_BY_TYPE: dict[str, str] = {
    "dtc": "dtc", "spn_fmi": "spn_fmi", "pgn_layout": "pgn_layout",
    "oem_divergence": "oem_divergence", "kb_defect": "kb_defect",
    "spn_reference": "spn_reference", "provenance_gap": "provenance_gap",
    "case": "case", "oem_note": "oem_note", "trace": "trace",
}


def sync_manifest(intake_dir: Path) -> str:
    """Regenerate both MANIFEST tables from the records on disk (pure function)."""
    records, _frames = load_records(intake_dir, Report())
    rows: list[str] = []
    external: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=lambda r: r.rel_path):
        licence = str(record.source.get("licence") or "unknown")
        source_ref = str(record.source.get("path") or "")
        for rel in record.manifest_paths():
            path = intake_dir / rel
            if not path.is_file():
                continue
            kind = KIND_BY_TYPE.get(record.record_type, record.record_type)
            if record.record_type == "trace" and rel != record.rel_path:
                kind = "trace_frames"
            raw = path.read_bytes()
            rows.append(
                f"| {record.intake_id} | `data/intake/{rel}` | {kind} | {len(raw)} | "
                f"`{hashlib.sha256(raw).hexdigest()}` | {licence} | `{source_ref}` |"
            )
        if record.record_type == "trace" and record.payload.get("in_git") is False:
            payload = record.payload
            external[record.intake_id] = {
                "format": payload.get("format"),
                "bytes": payload.get("frame_file_bytes"),
                "sha256": payload.get("frame_file_sha256"),
                "location": payload.get("external_location") or "",
            }
    text = MANIFEST_HEADER + "\n".join(rows) + "\n" + MANIFEST_EXTERNAL_HEADER
    for intake_id, entry in sorted(external.items()):
        text += (f"| {intake_id} | {entry['format']} | {entry['bytes']} | "
                 f"`{entry['sha256']}` | `{entry['location']}` |\n")
    return text + MANIFEST_TAIL


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def run(root: Path | None = None, intake_dir: Path | None = None, report_path: str | None = None,
        quiet: bool = False, max_inline_trace_bytes: int = DEFAULT_MAX_INLINE_TRACE_BYTES) -> Report:
    repo_root = (root or ROOT).resolve()
    target = (intake_dir or repo_root / INTAKE_DIRNAME).resolve()
    t0 = time.perf_counter()
    rep = Report()

    records, _frames = load_records(target, rep)
    manifest = parse_manifest(target / "MANIFEST.md", rep)

    for record in records:
        if record.record_type != "trace":
            continue
        validate_frame_file(record, target / RECORD_DIRS["trace"], repo_root, manifest,
                            max_inline_trace_bytes, rep)

    check_manifest_against_files(records, target, manifest, rep)
    report_conflicts(records, repo_root, rep)
    check_quarantine_invariants(repo_root, rep)

    total_bytes = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    if total_bytes > DEFAULT_MAX_INTAKE_BYTES:
        rep.fail("trace", f"data/intake totals {total_bytes} B > {DEFAULT_MAX_INTAKE_BYTES} B — move bulk traces "
                          f"out of git and keep only their MANIFEST hash + location")
    rep.metrics["intake_files"] = sum(1 for p in target.rglob("*") if p.is_file())
    rep.metrics["intake_bytes"] = total_bytes
    rep.metrics["intake_records"] = len(records)
    rep.metrics["max_inline_trace_bytes"] = max_inline_trace_bytes
    rep.metrics["seconds"] = round(time.perf_counter() - t0, 2)

    text = render(rep, time.perf_counter() - t0)
    if report_path:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    if not quiet:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        for level, check, detail in rep.rows:
            if level == "FAIL":
                print(f"[FAIL] {check}: {detail}")
        print(f"FAIL={rep.count('FAIL')} WARN={rep.count('WARN')} INFO={rep.count('INFO')} "
              f"metrics={json.dumps(rep.metrics, ensure_ascii=False, sort_keys=True)}")
    return rep


def render(rep: Report, seconds: float) -> str:
    lines = [
        "# Intake doğrulama raporu — `data/intake/`",
        "",
        "> Bu rapor yalnızca **raporlar**; `data/diagnostics` ve `data/golden_traces` dosyalarına yazmaz.",
        "",
        f"- Süre: {seconds:.1f} s · FAIL **{rep.count('FAIL')}** · WARN {rep.count('WARN')} "
        f"· INFO {rep.count('INFO')}",
        "",
        "## Ölçümler",
        "",
    ]
    lines += [f"- `{k}`: {v}" for k, v in sorted(rep.metrics.items())]
    lines += ["", "## Bulgular", "", "| Seviye | Kontrol | Ayrıntı |", "|---|---|---|"]
    lines += [f"| {lv} | {ck} | {dt.replace('|', '/')} |" for lv, ck, dt in rep.rows]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--root", default=None, help="repository root (default: inferred from this file)")
    ap.add_argument("--intake-dir", default=None, help="intake directory (default: <root>/data/intake)")
    ap.add_argument("--report", help="write a markdown report to this path")
    ap.add_argument("--max-inline-trace-bytes", type=int, default=DEFAULT_MAX_INLINE_TRACE_BYTES,
                    help="largest trace frame file allowed inside git (default 1 MiB)")
    ap.add_argument("--print-trace-meta", metavar="FRAME_FILE",
                    help="print the payload fields a trace .meta.json needs for this frame file, then exit")
    ap.add_argument("--sync-manifest", action="store_true",
                    help="regenerate MANIFEST.md from the records on disk (needs --apply to write)")
    ap.add_argument("--apply", action="store_true", help="with --sync-manifest: write the file")
    ap.add_argument("--quiet", action="store_true", help="suppress console output")
    args = ap.parse_args()
    if args.sync_manifest:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        base = Path(args.root) if args.root else ROOT
        intake_dir = (Path(args.intake_dir) if args.intake_dir else base / INTAKE_DIRNAME).resolve()
        text = sync_manifest(intake_dir)
        target = intake_dir / "MANIFEST.md"
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if not args.apply:
            print("[*] MANIFEST is up to date" if current == text else "[!] MANIFEST differs — run with --apply")
            return 0 if current == text else 1
        target.write_text(text, encoding="utf-8")
        print(f"[*] MANIFEST.md rewritten ({text.count('| `data/intake/')} in-git rows)")
        return 0
    if args.print_trace_meta:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        print(json.dumps(trace_meta_fields(Path(args.print_trace_meta)), indent=2, ensure_ascii=False))
        return 0
    rep = run(root=Path(args.root) if args.root else None,
              intake_dir=Path(args.intake_dir) if args.intake_dir else None,
              report_path=args.report,
              quiet=args.quiet,
              max_inline_trace_bytes=args.max_inline_trace_bytes)
    return 1 if rep.count("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())

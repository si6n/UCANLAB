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

RECORD_DIRS: dict[str, str] = {
    "dtc": "dtc",
    "spn_fmi": "spn_fmi",
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

SUBMITTER_TYPES: frozenset[str] = frozenset({"human", "curator", "organization"})
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
# Turkish landline (0 + 10 digits) or mobile (+90 5xx …) in plain or grouped
# form: 8–11 digits behind a +90/0 prefix, optional spaces/dots/dashes.
PHONE_RE = re.compile(r"(?<![\d.])(?:\+90[\s.-]?|0)[2-5](?:[\s.-]?\d){6,9}(?![\d.])")
IBAN_RE = re.compile(r"\bTR\d{2}[A-Z0-9]{20,26}\b", re.IGNORECASE)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
CARDISH_RE = re.compile(r"(?<![\d.])\d{13,19}(?![\d.])")
IMEI_RE = re.compile(r"(?<![\d.])\d{15}(?![\d.])")
# Hex payload values of a CAN frame: technical noise, not PII candidates.
HEX_FIELD_RE = re.compile(r'"(?:data|payload|bytes|raw)"\s*:\s*"[0-9A-Fa-f]*"')

# Trace policy. check_corpus_size_sentry.py is the repository-level gate
# (100 MB per file, data/traces <= 350 MB); intake is deliberately far
# stricter so the staging area can never become a blob dump.
DEFAULT_MAX_INLINE_TRACE_BYTES = 1_048_576  # 1 MiB
DEFAULT_MAX_INTAKE_BYTES = 5_242_880  # 5 MiB for the whole directory

ARTEFACT_RE = re.compile(r"(\.bak|\.orig$|~$|\.tmp-|\.swp$)")


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
    blob = HEX_FIELD_RE.sub('"data":"<hex>"', text) if skip_hex_fields else text
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


PAYLOAD_VALIDATORS = {
    "dtc": _validate_payload_dtc,
    "spn_fmi": _validate_payload_spn,
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
    PAYLOAD_VALIDATORS[record_type](body_payload, f"{where}.payload", rep)
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
            rep.fail("manifest", f"MANIFEST.md:{lineno}: row needs 7 columns (intake_id/path/kind/bytes/sha256/licence/source)")
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
def report_conflicts(records: list[Record], repo_root: Path, rep: Report) -> None:
    """Compare intake records with the existing knowledge base. Report only."""
    dtc_db = repo_root / DIAGNOSTICS / "dtc_database.json"
    oem_db = repo_root / DIAGNOSTICS / "dtc_database_oem_layer.json"
    j1939_db = repo_root / DIAGNOSTICS / "j1939_spn_fmi_database.json"
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

    overlaps = 0
    candidates = 0

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
    ap.add_argument("--quiet", action="store_true", help="suppress console output")
    args = ap.parse_args()
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

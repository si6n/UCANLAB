"""Golden-Traces diagnostic case loader & validator (P1, Görev 4).

Loads repair-verified golden cases from ``data/golden_traces/cases/``.
Validation is STDLIB-ONLY (no jsonschema dependency — hand-rolled checks
mirroring schema.json v1). Fail-closed rules:

- Unknown top-level or nested fields -> rejection (schema is exact).
- Raw 17-char VIN embedded anywhere in the case -> rejection.
- ``actual_fault`` empty/null -> case is ``draft``; drafts can NEVER be
  marked ``verified`` and are excluded from calibration-eligible sets.
- ``verified: true`` without ``verified_date`` -> rejection.

The seed case (volvo_penta_d4_300_nonstart.json) is a DRAFT skeleton: real
field data is supplied by the user — the agent fabricates nothing
(AGENTS.md §2.3 extended: No Fabricated Telemetry / Evidence).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.core.logging import get_logger
from src.core.models.diagnostics import DiagnosticDomain

logger = get_logger("engine.ai_golden_cases")

# Resolve the cases directory for both repo runs and frozen PyInstaller
# builds (same pattern as diagnostic_copilot._resolve_external_data_dir).
def _resolve_cases_dir() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        frozen_dir = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve() / "data" / "golden_traces" / "cases"
        if frozen_dir.is_dir():
            return frozen_dir
    return Path(__file__).resolve().parents[3] / "data" / "golden_traces" / "cases"


CASES_DIR: Path = _resolve_cases_dir()

SCHEMA_VERSION = 1

# Exact field sets — extra fields fail closed (schema.json v1 parity).
_TOP_FIELDS: frozenset[str] = frozenset({
    "schema_version", "case_id", "domain", "make", "model", "year",
    "symptom", "dtcs", "signals_of_interest", "actual_fault", "repair",
    "verification", "trace_ref", "verified", "verified_date",
})
_DTC_FIELDS: frozenset[str] = frozenset({"code", "status"})
_SIGNAL_FIELDS: frozenset[str] = frozenset({"name", "expected_behavior", "observed_behavior"})

_VALID_DOMAINS: frozenset[str] = frozenset(d.value for d in DiagnosticDomain)
_VALID_STATUSES: frozenset[str] = frozenset({"ACTIVE", "HISTORY", "PENDING"})

_KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RAW_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


class GoldenCaseError(ValueError):
    """Raised when a golden case violates the v1 schema (fail-closed)."""


@dataclass(slots=True, frozen=True)
class GoldenCase:
    """A validated golden diagnostic case."""

    case_id: str
    domain: DiagnosticDomain
    symptom: str
    dtcs: tuple[dict[str, str], ...]
    signals_of_interest: tuple[dict[str, str | None], ...]
    actual_fault: str | None
    repair: str | None
    verification: str | None
    trace_ref: str | None
    verified: bool
    verified_date: str | None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    raw: dict[str, object] | None = None  # original parsed JSON (read-only evidence)

    @property
    def is_draft(self) -> bool:
        """A case without a confirmed actual_fault is a draft — not calibration-eligible."""
        return not (self.actual_fault and self.actual_fault.strip())


def _reject_raw_vin(case_id: str, payload: dict[str, object]) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    match = _RAW_VIN_RE.search(blob)
    if match:
        raise GoldenCaseError(
            f"case '{case_id}': raw 17-char VIN '{match.group(0)}' found — VINs must be masked"
        )


def validate_case_payload(payload: dict[str, object]) -> GoldenCase:
    """Validate one parsed case dict against schema v1 (stdlib checks, fail-closed)."""
    if not isinstance(payload, dict):
        raise GoldenCaseError("case payload must be a JSON object")

    extra = set(payload) - _TOP_FIELDS
    if extra:
        raise GoldenCaseError(f"unknown top-level field(s): {sorted(extra)}")
    missing = _TOP_FIELDS - set(payload)
    if missing:
        raise GoldenCaseError(f"missing required field(s): {sorted(missing)}")

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise GoldenCaseError(f"unsupported schema_version: {payload.get('schema_version')!r}")

    case_id = payload.get("case_id")
    if not isinstance(case_id, str) or not _KEBAB_CASE_RE.fullmatch(case_id):
        raise GoldenCaseError(f"case_id must be kebab-case (got {case_id!r})")

    domain_raw = payload.get("domain")
    if domain_raw not in _VALID_DOMAINS:
        raise GoldenCaseError(f"domain must be one of {sorted(_VALID_DOMAINS)} (got {domain_raw!r})")

    symptom = payload.get("symptom")
    if not isinstance(symptom, str) or not symptom.strip():
        raise GoldenCaseError("symptom must be a non-empty string")

    for str_field in ("make", "model", "actual_fault", "repair", "verification", "trace_ref"):
        val = payload.get(str_field)
        if val is not None and not isinstance(val, str):
            raise GoldenCaseError(f"{str_field} must be a string or null (got {type(val).__name__})")

    year = payload.get("year")
    if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2100):
        raise GoldenCaseError(f"year must be an integer in [1900, 2100] or null (got {year!r})")

    dtcs_raw = payload.get("dtcs")
    if not isinstance(dtcs_raw, list):
        raise GoldenCaseError("dtcs must be an array")
    dtcs: list[dict[str, str]] = []
    for i, d in enumerate(dtcs_raw):
        if not isinstance(d, dict):
            raise GoldenCaseError(f"dtcs[{i}] must be an object")
        extra_d = set(d) - _DTC_FIELDS
        if extra_d:
            raise GoldenCaseError(f"dtcs[{i}] unknown field(s): {sorted(extra_d)}")
        code, status = d.get("code"), d.get("status")
        if not isinstance(code, str) or not code.strip():
            raise GoldenCaseError(f"dtcs[{i}].code must be a non-empty string")
        if status not in _VALID_STATUSES:
            raise GoldenCaseError(f"dtcs[{i}].status must be one of {sorted(_VALID_STATUSES)}")
        dtcs.append({"code": code, "status": status})

    signals_raw = payload.get("signals_of_interest")
    if not isinstance(signals_raw, list):
        raise GoldenCaseError("signals_of_interest must be an array")
    signals: list[dict[str, str | None]] = []
    for i, s in enumerate(signals_raw):
        if not isinstance(s, dict):
            raise GoldenCaseError(f"signals_of_interest[{i}] must be an object")
        extra_s = set(s) - _SIGNAL_FIELDS
        if extra_s:
            raise GoldenCaseError(f"signals_of_interest[{i}] unknown field(s): {sorted(extra_s)}")
        name = s.get("name")
        if not isinstance(name, str) or not name.strip():
            raise GoldenCaseError(f"signals_of_interest[{i}].name must be a non-empty string")
        for behavior_field in ("expected_behavior", "observed_behavior"):
            val = s.get(behavior_field)
            if val is not None and not isinstance(val, str):
                raise GoldenCaseError(f"signals_of_interest[{i}].{behavior_field} must be string or null")
        signals.append({
            "name": name,
            "expected_behavior": s.get("expected_behavior"),
            "observed_behavior": s.get("observed_behavior"),
        })

    verified = payload.get("verified")
    if not isinstance(verified, bool):
        raise GoldenCaseError("verified must be a boolean")
    verified_date = payload.get("verified_date")
    if verified_date is not None and (not isinstance(verified_date, str) or not _DATE_RE.fullmatch(verified_date)):
        raise GoldenCaseError("verified_date must be YYYY-MM-DD or null")
    if verified and not verified_date:
        raise GoldenCaseError("verified: true requires verified_date")
    if verified and not (payload.get("actual_fault") or "").strip():
        raise GoldenCaseError("verified case must have a non-empty actual_fault")

    _reject_raw_vin(str(case_id), payload)

    return GoldenCase(
        case_id=case_id,
        domain=DiagnosticDomain(str(domain_raw)),
        symptom=symptom,
        dtcs=tuple(dtcs),
        signals_of_interest=tuple(signals),
        actual_fault=payload.get("actual_fault"),
        repair=payload.get("repair"),
        verification=payload.get("verification"),
        trace_ref=payload.get("trace_ref"),
        verified=verified,
        verified_date=verified_date,
        make=payload.get("make"),
        model=payload.get("model"),
        year=year,
        raw=payload,
    )


def load_case(path: Path) -> GoldenCase:
    """Load and validate one case file. Raises GoldenCaseError on violation."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenCaseError(f"cannot read/parse {path.name}: {exc}") from exc
    return validate_case_payload(payload)


def load_all_cases(cases_dir: Path | None = None) -> list[GoldenCase]:
    """Load and validate every case in the directory (schema.json excluded).

    Fail-closed walk: any invalid case file aborts the load — a corrupt
    calibration corpus must never be partially ingested.
    """
    target_dir = cases_dir or CASES_DIR
    cases: list[GoldenCase] = []
    for case_file in sorted(target_dir.glob("*.json")):
        if case_file.name == "schema.json":
            continue
        cases.append(load_case(case_file))
    return cases


def calibration_eligible_cases(cases_dir: Path | None = None) -> list[GoldenCase]:
    """Only verified, non-draft cases may feed AI calibration."""
    return [c for c in load_all_cases(cases_dir) if c.verified and not c.is_draft]

"""DTC Diagnostic Procedure Schema & Validator (FAZ 2).

Defines and validates structured diagnostic procedures for DTCs:
- Schema: {dtc, system, symptoms[], questions[], measurement_steps[], expected_values, pass_next, fail_next, safety_notes}
- Physical value range bounds check (e.g., voltage in [0, 48] V, resistance >= 0 Ohm).
- Fail-closed validation for procedure packs.

Fully offline, deterministic, zero external framework dependencies.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProcedureSchemaError(ValueError):
    """Raised when a DTC procedure file violates schema or physical value bounds."""


ALLOWED_TOP_FIELDS = frozenset({
    "schema_version",
    "dtc",
    "system",
    "symptoms",
    "questions",
    "measurement_steps",
    "expected_values",
    "pass_next",
    "fail_next",
    "safety_notes",
})

REQUIRED_TOP_FIELDS = frozenset({
    "schema_version",
    "dtc",
    "system",
    "symptoms",
    "questions",
    "measurement_steps",
    "expected_values",
    "safety_notes",
})

# Physical plausibility limits for automotive, heavy-duty and marine systems
PHYSICAL_BOUNDS: dict[str, tuple[float, float]] = {
    "voltage_v": (0.0, 48.0),
    "resistance_ohm": (0.0, 10_000_000.0),
    "pressure_kpa": (0.0, 300_000.0),  # up to common rail 3000 bar (300,000 kPa)
    "temperature_c": (-50.0, 1500.0),   # up to exhaust gas 1200 C
    "airflow_gps": (0.0, 1500.0),       # g/s MAF
}


@dataclass(slots=True, frozen=True)
class DtcProcedure:
    """Frozen diagnostic procedure for a single DTC or SPN/FMI."""

    schema_version: int
    dtc: str
    system: str
    symptoms: tuple[str, ...]
    questions: tuple[dict[str, Any], ...]
    measurement_steps: tuple[dict[str, Any], ...]
    expected_values: dict[str, Any]
    safety_notes: tuple[str, ...]
    pass_next: str | None = None
    fail_next: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dtc": self.dtc,
            "system": self.system,
            "symptoms": list(self.symptoms),
            "questions": [dict(q) for q in self.questions],
            "measurement_steps": [dict(m) for m in self.measurement_steps],
            "expected_values": dict(self.expected_values),
            "pass_next": self.pass_next,
            "fail_next": self.fail_next,
            "safety_notes": list(self.safety_notes),
        }


def _validate_physical_bounds(key: str, val: Any) -> None:
    """Check physical bounds on numeric expected values."""
    if not isinstance(val, (int, float)):
        return
    low_key = key.lower()
    for bound_key, (min_val, max_val) in PHYSICAL_BOUNDS.items():
        if bound_key.split("_")[0] in low_key:
            if not (min_val <= float(val) <= max_val):
                raise ProcedureSchemaError(
                    f"Physical value out of plausible bounds: '{key}'={val} "
                    f"(allowed: [{min_val}, {max_val}])"
                )


def validate_procedure_payload(payload: dict[str, Any]) -> DtcProcedure:
    """Validate a raw procedure JSON/dict against the schema (fail-closed)."""
    if not isinstance(payload, dict):
        raise ProcedureSchemaError("Procedure payload must be a JSON object/dict")

    extra = set(payload) - ALLOWED_TOP_FIELDS
    if extra:
        raise ProcedureSchemaError(f"Unknown top-level field(s): {sorted(extra)}")

    missing = REQUIRED_TOP_FIELDS - set(payload)
    if missing:
        raise ProcedureSchemaError(f"Missing required field(s): {sorted(missing)}")

    if payload["schema_version"] != 1:
        raise ProcedureSchemaError(f"Unsupported schema_version: {payload['schema_version']}")

    dtc = str(payload["dtc"]).strip().upper()
    if not dtc:
        raise ProcedureSchemaError("DTC identifier cannot be empty")

    system = str(payload["system"]).strip()
    if not system:
        raise ProcedureSchemaError("System identifier cannot be empty")

    symptoms = payload.get("symptoms", [])
    if not isinstance(symptoms, list):
        raise ProcedureSchemaError("symptoms must be a list of strings")

    questions = payload.get("questions", [])
    if not isinstance(questions, list):
        raise ProcedureSchemaError("questions must be a list of question dicts")

    for q in questions:
        if not isinstance(q, dict) or "id" not in q or "kind" not in q or "text" not in q:
            raise ProcedureSchemaError("Each question must contain 'id', 'kind', and 'text'")

    measurement_steps = payload.get("measurement_steps", [])
    if not isinstance(measurement_steps, list):
        raise ProcedureSchemaError("measurement_steps must be a list of dicts")

    expected_values = payload.get("expected_values", {})
    if not isinstance(expected_values, dict):
        raise ProcedureSchemaError("expected_values must be a dict")

    for k, v in expected_values.items():
        if isinstance(v, (int, float)):
            _validate_physical_bounds(k, v)
        elif isinstance(v, (list, tuple)) and len(v) == 2:
            _validate_physical_bounds(k, v[0])
            _validate_physical_bounds(k, v[1])

    safety_notes = payload.get("safety_notes", [])
    if not isinstance(safety_notes, list):
        raise ProcedureSchemaError("safety_notes must be a list of strings")

    return DtcProcedure(
        schema_version=payload["schema_version"],
        dtc=dtc,
        system=system,
        symptoms=tuple(str(s) for s in symptoms),
        questions=tuple(dict(q) for q in questions),
        measurement_steps=tuple(dict(m) for m in measurement_steps),
        expected_values=dict(expected_values),
        pass_next=payload.get("pass_next"),
        fail_next=payload.get("fail_next"),
        safety_notes=tuple(str(sn) for sn in safety_notes),
    )


def load_procedure_file(path: Path) -> DtcProcedure:
    """Load and validate a single procedure JSON file."""
    if not path.is_file():
        raise FileNotFoundError(f"Procedure file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return validate_procedure_payload(payload)


_PROCEDURE_CACHE: dict[str, DtcProcedure] = {}
_ALL_LOADED: bool = False
_CACHE_LOCK = threading.Lock()


def get_procedure(dtc: str, dir_path: Path | None = None) -> DtcProcedure | None:
    """O(1) lazy lookup of a single DTC/SPN procedure with memory caching."""
    clean = (dtc or "").strip().upper()
    if not clean:
        return None
    with _CACHE_LOCK:
        if clean in _PROCEDURE_CACHE:
            return _PROCEDURE_CACHE[clean]

    target_dir = dir_path or (
        Path(__file__).resolve().parents[3] / "data" / "knowledge" / "dtc_procedures"
    )
    if not target_dir.is_dir():
        return None

    filename = clean.replace(" ", "_") + ".json"
    file_path = target_dir / filename
    if file_path.is_file():
        try:
            proc = load_procedure_file(file_path)
            with _CACHE_LOCK:
                _PROCEDURE_CACHE[clean] = proc
            return proc
        except Exception:
            return None
    return None


def load_all_procedures(dir_path: Path | None = None, force_reload: bool = False) -> dict[str, DtcProcedure]:
    """Load and validate all procedure files in a directory (cached)."""
    global _ALL_LOADED
    with _CACHE_LOCK:
        if _ALL_LOADED and not force_reload and dir_path is None:
            return dict(_PROCEDURE_CACHE)

    target_dir = dir_path or (
        Path(__file__).resolve().parents[3] / "data" / "knowledge" / "dtc_procedures"
    )
    if not target_dir.is_dir():
        return {}
    procedures: dict[str, DtcProcedure] = {}
    for file_path in sorted(target_dir.glob("*.json")):
        try:
            proc = load_procedure_file(file_path)
            procedures[proc.dtc] = proc
        except Exception:
            continue

    with _CACHE_LOCK:
        if dir_path is None:
            _PROCEDURE_CACHE.clear()
            _PROCEDURE_CACHE.update(procedures)
            _ALL_LOADED = True
    return procedures


__all__ = [
    "DtcProcedure",
    "ProcedureSchemaError",
    "validate_procedure_payload",
    "load_procedure_file",
    "load_all_procedures",
    "get_procedure",
]

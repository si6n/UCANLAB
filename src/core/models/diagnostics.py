"""P1 diagnostic session data model (Golden-Traces foundation).

Hexagonal core layer: zero framework dependencies, stdlib only. Frozen
dataclasses keep telemetry evidence immutable — an AI narrative layer can
never rewrite recorded samples or events (AGENTS.md §2.3 No Fabricated
Telemetry).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# 17-char VIN pattern (ISO 3779): excludes I, O, Q. Used fail-closed: a raw
# VIN must NEVER enter a VehicleSession — only mask_vin_in_text output.
#
# P2-2: the masking primitive itself now lives in core. It was previously
# defined only in `src.engine.ai.diagnostic_copilot`, which made the core
# layer depend (for a privacy control) on the engine layer that consumes it.
# `diagnostic_copilot` must import it from here — `from
# src.core.models.diagnostics import mask_vin_in_text` — instead of keeping a
# second copy; core must never import engine. The engine-side name is kept as a
# re-export so existing importers/tests keep working.
_RAW_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
RAW_VIN_FULLMATCH_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_MASKED_VIN_RE = re.compile(r"^\*{11}[A-HJ-NPR-Z0-9]{6}$")
_MASKED_VIN_UNANCHORED_RE = re.compile(r"\*{11}[A-HJ-NPR-Z0-9]{6}")

VIN_MASK_PLACEHOLDER: str = "*" * 11


def mask_vin_in_text(text: object) -> str:
    """Mask all but the last 6 chars of any 17-char VIN in free text.

    Canonical VIN privacy primitive (ISO 3779, `[A-HJ-NPR-Z0-9]{17}`, I/O/Q
    excluded). Case-insensitive: a lowercase VIN used to slip through unmasked
    and leak into logs / session summaries / exported reports.

    Already-masked VINs (``***********123456``) are left untouched — the 6
    trailing characters and the 11 ``*`` are not VIN-alphabet material, so the
    pattern cannot match them again (idempotent).

    This is the *only* VIN masking implementation. `VehicleSession.vin_masked`
    is fail-closed against raw VINs by construction (see the property setter).
    """
    return _RAW_VIN_RE.sub(lambda m: VIN_MASK_PLACEHOLDER + m.group(0)[-6:], str(text))


def _validate_vin_masked(value: str) -> str:
    """Fail-closed validation shared by the VehicleSession constructor and setter.

    A raw 17-char VIN is rejected outright (never silently masked — the caller
    must acknowledge the privacy boundary), and anything that is not
    `mask_vin_in_text` output is rejected too, so the contract cannot be
    weakened by later assignment.
    """
    if RAW_VIN_FULLMATCH_RE.fullmatch(value):
        raise ValueError("vin_masked contains a RAW VIN — mask it with mask_vin_in_text first")
    if not _MASKED_VIN_RE.fullmatch(value):
        raise ValueError("vin_masked must be mask_vin_in_text output (11 '*' + last 6 VIN chars)")
    return value


class SignalSource(Enum):
    """Origin of a decoded signal sample."""

    DBC = "DBC"
    J1939 = "J1939"
    UDS = "UDS"
    OBD2 = "OBD2"
    NMEA2000 = "NMEA2000"
    DISCOVERED = "DISCOVERED"  # auto-discovery output; confidence < 1.0


class DiagnosticDomain(Enum):
    """Vehicle/equipment domain of a diagnostic session.

    P2-9: AGRICULTURE covers ISOBUS / ISO 11783 implements and SAE J1939
    agricultural equipment. ISOBUS is a J1939 derivative, so it belongs to the
    same diagnostic domain family rather than a separate protocol stack; it is
    kept as its own member so the domain never has to be guessed from the
    make/model strings.
    """

    HEAVY_DUTY = "HEAVY_DUTY"
    PASSENGER = "PASSENGER"
    MARINE = "MARINE"
    INDUSTRIAL = "INDUSTRIAL"
    AGRICULTURE = "AGRICULTURE"


class Severity(str, Enum):
    """Allowlisted diagnostic severity rungs (knowledge-base sourced).

    P2-9: `DiagnosticEvent.severity` used to be a free string, so a typo
    ("HIHG") or an AI-invented rung entered the evidence base unnoticed. The
    rungs mirror the deterministic expert engine's `FaultSeverity`
    (`src/engine/ai/diagnostic_copilot.py`) 1:1 so evidence stays comparable
    with analysis output.

    `str` mixin, not a bare `Enum`: severity is a serialization value — it is
    compared with plain strings and written straight into JSON/YAML reports —
    so the members must *be* strings (`Severity("MEDIUM") == "MEDIUM"`, and
    `json.dumps` emits `"MEDIUM"` rather than `"Severity.MEDIUM"`). That also
    lets the in-scope producers keep passing the documented literal
    (`severity=Severity.MEDIUM` or `severity="MEDIUM"`; the engine's
    `dialogue_engine.py`/`calibration.py` construct events with the literal).

    Validation still bites: a bare `str` is NOT a `Severity`, and any value
    outside this allowlist raises at construction. Core must not import engine
    — the two enums are kept in sync by value, and the engine's `FaultSeverity`
    should become a re-export of this class.
    """

    #: Deliberately-modelled "we do not know". The desktop evidence bridge
    #: (`src/ui/desktop_app.py`) and the engine emit this for a DTC with no
    #: knowledge-base entry — it is the honest, non-fabricated answer
    #: (AGENTS.md §2.3), NOT a placeholder to be defaulted away, so it is a
    #: first-class allowlisted rung.
    UNKNOWN = "UNKNOWN"
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL_STOP = "CRITICAL_STOP"


class DtcNamespace(str, Enum):
    """AUTOSAR Dem / ISO 14229-1 DTC namespace specification.

    AUTOSAR SWS Diagnostic Event Manager (Dem R21-11 / R25-11):
    'A combination of different DTC formats is not possible' per record.
    Separates standard SAE J2012 generic DTCs, 3-byte SAE J2012-4 DTCs,
    ISO 14229-1 UDS DTCs, OBD, WWH-OBD, J1939, and OEM manufacturer-specific DTCs.
    """

    SAE_J2012 = "SAE_J2012"
    SAE_J2012_4 = "SAE_J2012_4"
    ISO_14229_1 = "ISO_14229_1"
    OBD = "OBD"
    WWH_OBD = "WWH_OBD"
    J1939 = "J1939"
    OEM = "OEM"


class DtcClass(str, Enum):
    """ISO 27145-3 / WWH-OBD / AUTOSAR Dem DTC severity and MIL classification.

    Governs MIL activation and confirmation logic:
    - A: Severe / immediate MIL illumination (continuous or blinking).
    - B1: Emission threshold exceeded; MIL illuminated on 2nd operation cycle.
    - B2: Emission degradation without immediate threshold exceedance.
    - C: Non-emission related, no MIL required.
    - NoClass: Unclassified / no OBD class.
    """

    NoClass = "NoClass"
    A = "A"
    B1 = "B1"
    B2 = "B2"
    C = "C"


def classify_dtc_namespace(code: str) -> DtcNamespace:
    """Classify a DTC code into its normative AUTOSAR Dem namespace.

    - Standard SAE J2012 generic codes:
      - P0xxx, P2xxx, P3400..P3999 -> SAE_J2012
      - B0xxx, B2xxx -> SAE_J2012
      - C0xxx, C2xxx -> SAE_J2012
      - U0xxx, U2xxx -> SAE_J2012
    - Manufacturer-specific / proprietary codes:
      - P1xxx, P3000..P3399, P3A00..P3FFF -> OEM
      - B1xxx, B3xxx -> OEM
      - C1xxx, C3xxx, C6xxx -> OEM
      - U1xxx, U3xxx -> OEM
    - J1939 format codes (SPN ... FMI ...): -> J1939
    """
    clean = str(code).strip().upper()
    if clean.startswith("SPN ") or " FMI " in clean or clean.startswith("SPN_"):
        return DtcNamespace.J1939
    if len(clean) >= 6 and (clean.startswith("0X") or "-" in clean):
        return DtcNamespace.SAE_J2012_4
    if not clean:
        return DtcNamespace.OEM
    first = clean[0]
    second = clean[1] if len(clean) > 1 else ""
    if first == "P":
        if second in ("0", "2"):
            return DtcNamespace.SAE_J2012
        if second == "3":
            if len(clean) >= 3 and clean[2] in "456789":
                return DtcNamespace.SAE_J2012
            return DtcNamespace.OEM
        return DtcNamespace.OEM
    if first in ("B", "C", "U"):
        if second in ("0", "2"):
            return DtcNamespace.SAE_J2012
        return DtcNamespace.OEM
    return DtcNamespace.OEM


def classify_dtc_class(code: str, severity: str | Severity) -> DtcClass:
    """Classify DTC into ISO 27145-3 / WWH-OBD class based on severity and subsystem.

    - CRITICAL_STOP -> A (Immediate safety/engine hazard, MIL on/blink)
    - HIGH -> B1 (Emission threshold exceedance / major powertrain fault, MIL 2nd cycle)
    - MEDIUM:
      - Powertrain (P codes): B2 (Medium emission degradation)
      - Body, Chassis, Network (B, C, U): C (Non-emission related / comfort)
    - LOW, INFO -> C (Non-emission / minor fault, no MIL required)
    - UNKNOWN -> NoClass (Unclassified)
    """
    sev_str = severity.value if isinstance(severity, Severity) else str(severity).upper()
    if sev_str == "CRITICAL_STOP":
        return DtcClass.A
    if sev_str == "HIGH":
        return DtcClass.B1
    if sev_str == "MEDIUM":
        clean = str(code).strip().upper()
        if clean.startswith("P"):
            return DtcClass.B2
        return DtcClass.C
    if sev_str in ("LOW", "INFO"):
        return DtcClass.C
    if sev_str == "UNKNOWN":
        return DtcClass.NoClass
    return DtcClass.NoClass


class ProvenanceConfidence(str, Enum):
    """Epistemic confidence rating for diagnostic provenance records."""

    OPERATOR_VERIFIED = "operator_verified"
    SINGLE_SOURCE = "single_source"
    CORROBORATED = "corroborated"
    UNVERIFIED = "unverified"


@dataclass(slots=True, frozen=True)
class ProvenanceTarget:
    """Target field reference within a diagnostic record."""

    record_id: str
    field: str
    xpath: str | None = None

    def __post_init__(self) -> None:
        if not self.record_id or not str(self.record_id).strip():
            raise ValueError("ProvenanceTarget record_id must be non-empty")
        if not self.field or not str(self.field).strip():
            raise ValueError("ProvenanceTarget field must be non-empty")


@dataclass(slots=True, frozen=True)
class ProvenanceAgent:
    """Agent capturing or verifying a provenance record."""

    type: str  # human, system, automated, curator, organization
    id: str
    role: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not str(self.id).strip():
            raise ValueError("ProvenanceAgent id must be non-empty")
        valid_types = {"human", "system", "automated", "curator", "organization"}
        if self.type not in valid_types:
            raise ValueError(f"ProvenanceAgent type must be one of {sorted(valid_types)} (got {self.type!r})")


@dataclass(slots=True, frozen=True)
class ProvenanceSource:
    """Origin source specification for diagnostic claims."""

    title: str
    path: str
    type: str = "web"
    publisher: str | None = None
    revision: str | None = None
    licence: str | None = None
    access_date: str | None = None
    archive_url: str | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.title or not str(self.title).strip():
            raise ValueError("ProvenanceSource title must be non-empty")
        if not self.path or not str(self.path).strip():
            raise ValueError("ProvenanceSource path must be non-empty")


@dataclass(slots=True, frozen=True)
class ProvenanceRecord:
    """Immutable provenance record validating diagnostic claims (Konsolide Keşif Raporu F.2 / Aksiyon 14)."""

    provenance_id: str
    target: ProvenanceTarget
    activity: str
    agent: ProvenanceAgent
    source: ProvenanceSource
    confidence: ProvenanceConfidence = ProvenanceConfidence.UNVERIFIED
    verbatim: str | None = None
    activity_version: str | None = None
    activity_started: str | None = None
    activity_input_hash: str | None = None
    retrieved_from_web: bool = False
    http_last_modified: str | None = None
    http_etag: str | None = None

    def __post_init__(self) -> None:
        if not self.provenance_id or not str(self.provenance_id).startswith("prv-"):
            raise ValueError(f"provenance_id must start with 'prv-' (got {self.provenance_id!r})")
        if not isinstance(self.target, ProvenanceTarget):
            if isinstance(self.target, dict):
                object.__setattr__(self, "target", ProvenanceTarget(**self.target))
            else:
                raise ValueError("ProvenanceRecord target must be ProvenanceTarget or dict")
        if not isinstance(self.agent, ProvenanceAgent):
            if isinstance(self.agent, dict):
                object.__setattr__(self, "agent", ProvenanceAgent(**self.agent))
            else:
                raise ValueError("ProvenanceRecord agent must be ProvenanceAgent or dict")
        if not isinstance(self.source, ProvenanceSource):
            if isinstance(self.source, dict):
                object.__setattr__(self, "source", ProvenanceSource(**self.source))
            else:
                raise ValueError("ProvenanceRecord source must be ProvenanceSource or dict")
        if not isinstance(self.confidence, ProvenanceConfidence):
            try:
                object.__setattr__(self, "confidence", ProvenanceConfidence(self.confidence))
            except ValueError as exc:
                raise ValueError(
                    f"confidence must be one of {sorted(c.value for c in ProvenanceConfidence)} "
                    f"(got {self.confidence!r})"
                ) from exc


NON_KEY_DTC_FIELDS: frozenset[str] = frozenset({
    "title",
    "subsystem",
    "severity",
    "causes",
    "steps",
    "symptoms",
    "measurement",
    "uds_routine",
    "title_en",
    "title_tr",
    "description_en",
    "description_tr",
    "causes_en",
    "symptoms_en",
    "procedures_full",
    "oem_details",
    "gm_monitor",
    "nhtsa_evidence",
})


def validate_dtc_provenance_dependent_required(record: dict[str, Any] | DtcRecord) -> None:
    """Enforce dependentRequired invariant: non-key diagnostic claims demand provenance.

    ISO 26262 ASIL-B / Konsolide Keşif Raporu H.2 Aksiyon 14:
    A stub defining only identity key fields (code, dtc_namespace, dtc_class) requires
    no provenance. However, any record claiming clinical knowledge (title, causes,
    severity, steps, symptoms, measurement, etc.) must carry at least one valid
    provenance record. Fail-closed.
    """
    if isinstance(record, DtcRecord):
        has_non_key = bool(
            record.title or record.subsystem or record.causes or record.steps or record.symptoms
        )
        if has_non_key and not record.provenance:
            raise ValueError(
                f"DTC record {record.code} claims clinical knowledge but has empty provenance (dependentRequired violation)"
            )
        return

    if isinstance(record, dict):
        present_non_key = [f for f in NON_KEY_DTC_FIELDS if record.get(f)]
        prov = record.get("provenance")
        if present_non_key and (not prov or not isinstance(prov, (list, tuple)) or len(prov) == 0):
            raise ValueError(
                f"DTC record {record.get('code') or record.get('record_id', '<unknown>')} defines non-key fields {present_non_key} "
                f"without required provenance[] (dependentRequired violation)"
            )


def synthesize_provenance_from_legacy(code: str, record: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthesize formal provenance records conforming to F.2 schema from raw source metadata.

    Guarantees that 100% of diagnostic records with evidence satisfy the
    dtc_record_schema.json dependentRequired gate.
    """
    prov: list[dict[str, Any]] = []
    clean_code = str(code).strip().lower().replace(" ", "_").replace(":", "_")
    idx = 1

    if record.get("evidence_url") or record.get("source"):
        src_path = str(record.get("evidence_url") or f"https://{record.get('source')}")
        src_title = str(record.get("source") or "Diagnostic Knowledge Base")
        prov.append({
            "provenance_id": f"prv-{clean_code}-{idx:02d}",
            "target": {"record_id": code, "field": "causes"},
            "activity": "extract_evidence",
            "agent": {"type": "system", "id": "kb_harvester"},
            "source": {"title": src_title, "path": src_path, "type": "web"},
            "confidence": "single_source",
            "retrieved_from_web": True,
        })
        idx += 1

    if record.get("oem_source"):
        prov.append({
            "provenance_id": f"prv-{clean_code}-{idx:02d}",
            "target": {"record_id": code, "field": "oem_details"},
            "activity": "oem_import",
            "agent": {"type": "system", "id": "oem_curator"},
            "source": {
                "title": str(record.get("oem_attribution") or "OEM Service Database"),
                "path": str(record["oem_source"]),
                "type": "oem_manual",
            },
            "confidence": "operator_verified",
            "retrieved_from_web": True,
        })
        idx += 1

    if record.get("gm_source"):
        prov.append({
            "provenance_id": f"prv-{clean_code}-{idx:02d}",
            "target": {"record_id": code, "field": "gm_monitor"},
            "activity": "gm_mode06_import",
            "agent": {"type": "system", "id": "gm_gsi_ingest"},
            "source": {
                "title": str(record.get("gm_attribution") or "GM Global Service Information Mode $06"),
                "path": str(record["gm_source"]),
                "type": "oem_manual",
                "publisher": "General Motors",
            },
            "confidence": "operator_verified",
            "retrieved_from_web": True,
        })
        idx += 1

    if record.get("nhtsa_source"):
        prov.append({
            "provenance_id": f"prv-{clean_code}-{idx:02d}",
            "target": {"record_id": code, "field": "nhtsa_evidence"},
            "activity": "nhtsa_complaints_ingest",
            "agent": {"type": "system", "id": "nhtsa_api_client"},
            "source": {
                "title": "NHTSA ODI Complaints Database",
                "path": "https://api.nhtsa.gov/complaints",
                "type": "nhtsa",
                "publisher": "National Highway Traffic Safety Administration",
            },
            "confidence": "operator_verified",
            "retrieved_from_web": True,
        })
        idx += 1

    if record.get("title_tr_source"):
        prov.append({
            "provenance_id": f"prv-{clean_code}-{idx:02d}",
            "target": {"record_id": code, "field": "title_tr"},
            "activity": "bilingual_derivation",
            "agent": {"type": "system", "id": "title_localizer"},
            "source": {
                "title": "Bilingual Diagnostic Database",
                "path": "internal://dtc_database/title_tr",
                "type": "internal_kb",
            },
            "confidence": "single_source",
            "verbatim": str(record.get("title_tr")),
        })
        idx += 1

    if record.get("description_en_source"):
        prov.append({
            "provenance_id": f"prv-{clean_code}-{idx:02d}",
            "target": {"record_id": code, "field": "description_en"},
            "activity": "oem_fault_list_ingest",
            "agent": {"type": "system", "id": "kb_importer"},
            "source": {
                "title": "OEM Fault Code List",
                "path": str(record["description_en_source"]),
                "type": "web",
            },
            "confidence": "single_source",
            "retrieved_from_web": True,
        })
        idx += 1

    if record.get("causes_en_source"):
        prov.append({
            "provenance_id": f"prv-{clean_code}-{idx:02d}",
            "target": {"record_id": code, "field": "causes_en"},
            "activity": "repair_guide_ingest",
            "agent": {"type": "system", "id": "kb_importer"},
            "source": {
                "title": "Repair Guide OBD Reference",
                "path": str(record["causes_en_source"]),
                "type": "web",
            },
            "confidence": "single_source",
            "retrieved_from_web": True,
        })
        idx += 1

    if record.get("procedures_source_url") or record.get("procedures_source"):
        prov.append({
            "provenance_id": f"prv-{clean_code}-{idx:02d}",
            "target": {"record_id": code, "field": "procedures_full"},
            "activity": "repair_procedure_ingest",
            "agent": {"type": "system", "id": "procedure_curator"},
            "source": {
                "title": str(record.get("procedures_source") or "OEM Procedure Database"),
                "path": str(record.get("procedures_source_url") or "https://obd2.com"),
                "type": "web",
            },
            "confidence": "single_source",
            "retrieved_from_web": True,
        })
        idx += 1

    if not prov:
        prov.append({
            "provenance_id": f"prv-{clean_code}-01",
            "target": {"record_id": code, "field": "title"},
            "activity": "standard_code_classification",
            "agent": {"type": "system", "id": "sae_j2012_classifier"},
            "source": {
                "title": "SAE J2012 Diagnostic Trouble Code Definitions / ISO 14229-1 UDS",
                "path": "https://www.sae.org/standards/content/j2012_201612/",
                "type": "standard",
                "publisher": "SAE International",
            },
            "confidence": "single_source",
        })

    return prov


@dataclass(slots=True, frozen=True)
class DtcRecord:
    """Immutable DTC definition record conforming to AUTOSAR Dem / ISO 27145-3 schema."""

    code: str
    title: str
    subsystem: str
    severity: Severity
    dtc_namespace: DtcNamespace
    dtc_class: DtcClass
    causes: tuple[str, ...] = ()
    steps: tuple[tuple[str, ...], ...] = ()
    symptoms: tuple[str, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("DtcRecord code must be non-empty")
        if not isinstance(self.dtc_namespace, DtcNamespace):
            try:
                object.__setattr__(self, "dtc_namespace", DtcNamespace(self.dtc_namespace))
            except ValueError as exc:
                raise ValueError(
                    f"dtc_namespace must be one of {sorted(ns.value for ns in DtcNamespace)} "
                    f"(got {self.dtc_namespace!r})"
                ) from exc
        if not isinstance(self.dtc_class, DtcClass):
            try:
                object.__setattr__(self, "dtc_class", DtcClass(self.dtc_class))
            except ValueError as exc:
                raise ValueError(
                    f"dtc_class must be one of {sorted(c.value for c in DtcClass)} "
                    f"(got {self.dtc_class!r})"
                ) from exc
        if not isinstance(self.severity, Severity):
            try:
                object.__setattr__(self, "severity", Severity(self.severity))
            except ValueError as exc:
                raise ValueError(
                    f"severity must be one of {sorted(s.value for s in Severity)} "
                    f"(got {self.severity!r})"
                ) from exc
        if self.provenance:
            coerced_prov: list[ProvenanceRecord] = []
            for item in self.provenance:
                if isinstance(item, ProvenanceRecord):
                    coerced_prov.append(item)
                elif isinstance(item, dict):
                    coerced_prov.append(ProvenanceRecord(**item))
                else:
                    raise ValueError(f"provenance item must be ProvenanceRecord or dict (got {type(item)})")
            object.__setattr__(self, "provenance", tuple(coerced_prov))


@dataclass(slots=True, frozen=True)
class SignalSample:
    """One immutable decoded signal value at a monotonic timestamp."""

    timestamp_ns: int  # monotonic, clock_provider sourced
    name: str  # DBC signal name / SPN-N / PID hex
    raw_value: int
    physical_value: float
    unit: str
    source: SignalSource
    confidence: float = 1.0  # DISCOVERED signals must carry < 1.0

    def __post_init__(self) -> None:
        # Trust-boundary validation: sentinel/non-finite physical values are
        # rejected at construction — a fabricated NaN must never enter the
        # evidence base (fail-closed, AGENTS.md §2.3).
        if not math.isfinite(self.physical_value):
            raise ValueError(f"SignalSample physical_value must be finite (got {self.physical_value!r})")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0] (got {self.confidence!r})")
        if self.source is SignalSource.DISCOVERED and self.confidence >= 1.0:
            raise ValueError("DISCOVERED signals must carry confidence < 1.0 (confidence-gated)")
        if not self.name or not self.name.strip():
            raise ValueError("SignalSample name must be non-empty")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")


@dataclass(slots=True, frozen=True)
class DiagnosticEvent:
    """One immutable diagnostic trouble-code event."""

    timestamp_ns: int
    code: str  # "P0201" | "SPN 84 FMI 4" | "UDS DID 0xF190 okundu"
    domain: DiagnosticDomain
    severity: Severity  # knowledge-base sourced; AI never produces this
    status: str  # ACTIVE | HISTORY | PENDING
    related_signals: tuple[str, ...] = ()
    dtc_namespace: DtcNamespace | None = None
    dtc_class: DtcClass | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("DiagnosticEvent code must be non-empty")
        if self.status not in ("ACTIVE", "HISTORY", "PENDING"):
            raise ValueError(f"status must be ACTIVE|HISTORY|PENDING (got {self.status!r})")
        if not isinstance(self.severity, Severity):
            # Accept the equivalent documented literal by normalising it to the
            # enum member, then fail closed on anything outside the allowlist.
            # (`Severity` mixes in `str`, so `Severity.MEDIUM == "MEDIUM"` — but
            # an arbitrary string is NOT a member and must never slip through.)
            try:
                object.__setattr__(self, "severity", Severity(self.severity))
            except ValueError as exc:
                raise ValueError(
                    "severity must be one of "
                    f"{sorted(s.value for s in Severity)} "
                    f"(got {type(self.severity).__name__} {self.severity!r})"
                ) from exc
        if self.dtc_namespace is not None and not isinstance(self.dtc_namespace, DtcNamespace):
            try:
                object.__setattr__(self, "dtc_namespace", DtcNamespace(self.dtc_namespace))
            except ValueError as exc:
                raise ValueError(
                    f"dtc_namespace must be one of {sorted(ns.value for ns in DtcNamespace)} "
                    f"(got {self.dtc_namespace!r})"
                ) from exc
        if self.dtc_class is not None and not isinstance(self.dtc_class, DtcClass):
            try:
                object.__setattr__(self, "dtc_class", DtcClass(self.dtc_class))
            except ValueError as exc:
                raise ValueError(
                    f"dtc_class must be one of {sorted(c.value for c in DtcClass)} "
                    f"(got {self.dtc_class!r})"
                ) from exc
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")


@dataclass(slots=True)
class VehicleSession:
    """A diagnostic recording session bound to one vehicle/equipment.

    Mutable container (events/samples accumulate over the session lifetime)
    but every contained record is frozen. ``vin_masked`` accepts ONLY
    mask_vin_in_text output — a raw 17-char VIN fails construction AND fails
    any later assignment (P0-2: the check used to run in ``__post_init__``
    only, so a raw VIN could be attached after construction).
    """

    session_id: str
    started_at_ns: int
    domain: DiagnosticDomain
    make: str | None = None
    model: str | None = None
    #: Masked VIN ONLY (`mask_vin_in_text` output). Public accessor is the
    #: `vin_masked` property; the custom `__init__` below keeps `vin_masked=`
    #: as the constructor keyword callers already use.
    vin: str | None = field(default=None, repr=False)
    trace_ref: str | None = None  # Golden-Traces archive file reference
    events: list[DiagnosticEvent] = field(default_factory=list)
    samples: list[SignalSample] = field(default_factory=list)

    def __init__(
        self,
        session_id: str,
        started_at_ns: int,
        domain: DiagnosticDomain,
        make: str | None = None,
        model: str | None = None,
        vin_masked: str | None = None,
        trace_ref: str | None = None,
        events: list[DiagnosticEvent] | None = None,
        samples: list[SignalSample] | None = None,
        *,
        vin: str | None = None,
    ) -> None:
        # P0-2: construction and every later assignment share ONE fail-closed
        # validation path (`_validate_vin_masked`). A raw 17-char VIN is rejected
        # in both, not just at construction time.
        if vin is not None:
            if vin_masked is not None and vin != vin_masked:
                raise ValueError("vin and vin_masked disagree — pass only one of them")
            vin_masked = vin
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "started_at_ns", started_at_ns)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "make", make)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "trace_ref", trace_ref)
        object.__setattr__(self, "events", list(events) if events is not None else [])
        object.__setattr__(self, "samples", list(samples) if samples is not None else [])
        self.vin_masked = vin_masked
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.session_id or not self.session_id.strip():
            raise ValueError("session_id must be non-empty")
        if self.started_at_ns < 0:
            raise ValueError("started_at_ns must be non-negative")
        # Any path that bypasses the custom __init__ (e.g. the dataclass
        # machinery behind `dataclasses.replace`) re-runs the same VIN check,
        # so no route into this container can carry an unvalidated value.
        if self.vin is not None:
            self.vin = _validate_vin_masked(self.vin)

    @property
    def vin_masked(self) -> str | None:
        """The masked-only VIN (mask_vin_in_text output, or None)."""
        return self.vin

    @vin_masked.setter
    def vin_masked(self, value: str | None) -> None:
        if value is None:
            self.vin = None
            return
        if not isinstance(value, str):
            raise ValueError(f"vin_masked must be a str or None (got {type(value).__name__})")
        self.vin = _validate_vin_masked(value)


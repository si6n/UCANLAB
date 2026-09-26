# -*- coding: utf-8 -*-
"""Unit tests for Diagnostic Provenance Schema & dependentRequired enforcement (Aksiyon 14 / Faz 5.1).

Validates:
1. provenance_schema.json conforms to JSON Schema Draft 2020-12 (Konsolide Keşif Raporu F.2).
2. dtc_record_schema.json conforms to Draft 2020-12 and enforces dependentRequired:
   - Key-only stubs (code, dtc_namespace, dtc_class) pass without provenance.
   - Informational / clinical non-key fields (title, causes, severity, steps, symptoms, etc.)
     strictly REQUIRE provenance[].
   - Empty provenance: [] is rejected (minItems: 1).
   - Valid provenance records pass.
3. Provenance record field constraints (provenance_id pattern, confidence enum, agent, source).
4. Python ProvenanceRecord, ProvenanceTarget, ProvenanceSource, ProvenanceAgent dataclasses.
5. DtcRecord provenance integration & validate_dtc_provenance_dependent_required helper.
6. synthesize_provenance_from_legacy generates valid schema-compliant provenance entries.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import jsonschema
import referencing

from src.core.models.diagnostics import (
    DtcClass,
    DtcNamespace,
    DtcRecord,
    ProvenanceAgent,
    ProvenanceConfidence,
    ProvenanceRecord,
    ProvenanceSource,
    ProvenanceTarget,
    Severity,
    synthesize_provenance_from_legacy,
    validate_dtc_provenance_dependent_required,
)
from src.engine.ai.diagnostic_copilot import _validate_dtc_entry_shape

REPO_ROOT = Path(__file__).resolve().parents[2]
DIAG_DIR = REPO_ROOT / "data" / "diagnostics"
PROV_SCHEMA_PATH = DIAG_DIR / "provenance_schema.json"
DTC_SCHEMA_PATH = DIAG_DIR / "dtc_record_schema.json"


@pytest.fixture(scope="module")
def prov_schema() -> dict:
    return json.loads(PROV_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dtc_schema() -> dict:
    return json.loads(DTC_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dtc_validator(dtc_schema: dict, prov_schema: dict) -> jsonschema.Draft202012Validator:
    prov_resource = referencing.Resource.from_contents(prov_schema)
    registry = referencing.Registry().with_resource(
        "https://ucanlab.org/schemas/provenance.json", prov_resource
    )
    return jsonschema.Draft202012Validator(dtc_schema, registry=registry)


@pytest.fixture(scope="module")
def prov_validator(prov_schema: dict) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(prov_schema)


class TestSchemaMetaValidity:
    def test_provenance_schema_file_exists(self) -> None:
        assert PROV_SCHEMA_PATH.exists(), f"Schema file missing: {PROV_SCHEMA_PATH}"

    def test_dtc_record_schema_file_exists(self) -> None:
        assert DTC_SCHEMA_PATH.exists(), f"Schema file missing: {DTC_SCHEMA_PATH}"

    def test_provenance_schema_is_valid_draft202012(self, prov_schema: dict) -> None:
        jsonschema.Draft202012Validator.check_schema(prov_schema)

    def test_dtc_record_schema_is_valid_draft202012(self, dtc_schema: dict) -> None:
        jsonschema.Draft202012Validator.check_schema(dtc_schema)


class TestProvenanceRecordSchema:
    def test_valid_minimal_provenance_record(self, prov_validator: jsonschema.Draft202012Validator) -> None:
        record = {
            "provenance_id": "prv-p0101-01",
            "target": {"record_id": "P0101", "field": "title"},
            "activity": "normalize_from_source",
            "agent": {"type": "system", "id": "kb_harvester"},
            "source": {"title": "SAE J2012", "path": "https://sae.org/j2012"},
            "confidence": "operator_verified",
        }
        prov_validator.validate(record)

    def test_valid_full_provenance_record(self, prov_validator: jsonschema.Draft202012Validator) -> None:
        record = {
            "provenance_id": "prv-0001",
            "target": {"record_id": "P0101", "field": "description.en", "xpath": "/records/42/description/en"},
            "activity": "normalize_from_source",
            "activity_version": "extract-v3.2.1",
            "activity_started": "2026-03-14T09:12:04Z",
            "activity_input_hash": "sha256:9f2c1234567890abcdef",
            "agent": {"type": "human", "id": "kb-editor-07", "role": "author"},
            "source": {
                "title": "SAE J2012 Diagnostic Trouble Code Definitions",
                "path": "https://doi.org/10.4271/J2012_201612",
                "type": "standard",
                "publisher": "SAE International",
                "revision": "2007-12",
                "licence": "proprietary",
                "access_date": "2026-03-12",
                "snapshot": {
                    "archive_url": "https://archive.org/details/sae-j2012",
                    "sha256": "abcdef0123456789",
                    "bytes": 1234567,
                    "pages_cited": [4, 5],
                },
            },
            "locator": {"type": "page", "value": "Page 42, Table 3"},
            "verbatim": "Mass or Volume Air Flow A Circuit Range/Performance",
            "transform": {"rule_id": "SEV-R3", "rule_hash": "sha256:112233"},
            "confidence": "operator_verified",
            "corroboration": [{"provenance_id": "prv-0002", "agreement": "verbatim"}],
            "retrieved_from_web": True,
            "http_last_modified": "Sun, 12 Mar 2026 10:00:00 GMT",
            "http_etag": '"abcdef123456"',
        }
        prov_validator.validate(record)

    def test_rejects_invalid_provenance_id_pattern(self, prov_validator: jsonschema.Draft202012Validator) -> None:
        record = {
            "provenance_id": "invalid_id_without_prv_prefix",
            "target": {"record_id": "P0101", "field": "title"},
            "activity": "normalize_from_source",
            "agent": {"type": "system", "id": "kb_harvester"},
            "source": {"title": "SAE J2012", "path": "https://sae.org/j2012"},
            "confidence": "operator_verified",
        }
        with pytest.raises(jsonschema.ValidationError) as exc_info:
            prov_validator.validate(record)
        assert "provenance_id" in str(exc_info.value)

    def test_rejects_invalid_confidence_enum(self, prov_validator: jsonschema.Draft202012Validator) -> None:
        record = {
            "provenance_id": "prv-0001",
            "target": {"record_id": "P0101", "field": "title"},
            "activity": "normalize_from_source",
            "agent": {"type": "system", "id": "kb_harvester"},
            "source": {"title": "SAE J2012", "path": "https://sae.org/j2012"},
            "confidence": "super_high_confidence",  # invalid enum
        }
        with pytest.raises(jsonschema.ValidationError) as exc_info:
            prov_validator.validate(record)
        assert "confidence" in str(exc_info.value)

    def test_rejects_missing_source_path(self, prov_validator: jsonschema.Draft202012Validator) -> None:
        record = {
            "provenance_id": "prv-0001",
            "target": {"record_id": "P0101", "field": "title"},
            "activity": "normalize_from_source",
            "agent": {"type": "system", "id": "kb_harvester"},
            "source": {"title": "SAE J2012"},  # path missing
            "confidence": "operator_verified",
        }
        with pytest.raises(jsonschema.ValidationError) as exc_info:
            prov_validator.validate(record)
        assert "'path' is a required property" in str(exc_info.value)


class TestDependentRequiredEnforcement:
    def test_minimal_stub_without_provenance_passes(self, dtc_validator: jsonschema.Draft202012Validator) -> None:
        stub = {
            "code": "P9999",
            "dtc_namespace": "OEM",
            "dtc_class": "NoClass",
        }
        dtc_validator.validate(stub)

    @pytest.mark.parametrize(
        "non_key_field,value",
        [
            ("title", "Kütle Hava Akış Sensörü"),
            ("subsystem", "Yakıt & Hava Ölçümü"),
            ("severity", "MEDIUM"),
            ("causes", ["MAF sensör kirlenmesi"]),
            ("steps", [["Hava emiş borusunu inceleyin", "Görsel", "Kolay"]]),
            ("symptoms", ["Rölanti dalgalanması"]),
            ("measurement", "12V besleme"),
            ("uds_routine", "0x22 DID 0x0110"),
            ("title_en", "Mass Air Flow Sensor"),
            ("title_tr", "Kütle Hava Akış Sensörü"),
            ("description_en", "Air mass meter implausible signal"),
            ("causes_en", "Contaminated sensor element"),
            ("symptoms_en", ["Check Engine Light"]),
            ("procedures_full", ["Step 1", "Step 2"]),
            ("oem_details", {"Audi": {}}),
            ("gm_monitor", {"parameter": "MAF"}),
            ("nhtsa_evidence", [{"make": "NISSAN"}]),
        ],
    )
    def test_non_key_field_without_provenance_fails_dependent_required(
        self, dtc_validator: jsonschema.Draft202012Validator, non_key_field: str, value: object
    ) -> None:
        rec = {
            "code": "P0101",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "B2",
            non_key_field: value,
        }
        with pytest.raises(jsonschema.ValidationError) as exc_info:
            dtc_validator.validate(rec)
        assert "'provenance' is a dependency of" in str(exc_info.value)

    def test_empty_provenance_array_rejected(self, dtc_validator: jsonschema.Draft202012Validator) -> None:
        rec = {
            "code": "P0101",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "B2",
            "title": "MAF Sensor",
            "provenance": [],  # empty rejected by minItems: 1
        }
        with pytest.raises(jsonschema.ValidationError) as exc_info:
            dtc_validator.validate(rec)
        assert "[] should be non-empty" in str(exc_info.value) or "minItems" in str(exc_info.value)

    def test_valid_record_with_provenance_passes(self, dtc_validator: jsonschema.Draft202012Validator) -> None:
        rec = {
            "code": "P0101",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "B2",
            "title": "MAF Sensor",
            "severity": "MEDIUM",
            "causes": ["Kirlenme"],
            "provenance": [
                {
                    "provenance_id": "prv-p0101-01",
                    "target": {"record_id": "P0101", "field": "title"},
                    "activity": "normalize_from_source",
                    "agent": {"type": "system", "id": "kb_harvester"},
                    "source": {"title": "SAE J2012", "path": "https://sae.org/j2012", "type": "standard"},
                    "confidence": "operator_verified",
                }
            ],
        }
        dtc_validator.validate(rec)


class TestPythonModelProvenanceIntegration:
    def test_provenance_dataclass_construction(self) -> None:
        rec = ProvenanceRecord(
            provenance_id="prv-p0101-title",
            target=ProvenanceTarget(record_id="P0101", field="title"),
            activity="normalize_from_source",
            agent=ProvenanceAgent(type="system", id="kb_harvester"),
            source=ProvenanceSource(title="SAE J2012", path="https://sae.org/j2012", type="standard"),
            confidence=ProvenanceConfidence.OPERATOR_VERIFIED,
        )
        assert rec.provenance_id == "prv-p0101-title"
        assert rec.target.field == "title"
        assert rec.agent.type == "system"
        assert rec.source.type == "standard"
        assert rec.confidence == ProvenanceConfidence.OPERATOR_VERIFIED

    def test_provenance_dataclass_coerces_dicts(self) -> None:
        rec = ProvenanceRecord(
            provenance_id="prv-p0101-01",
            target={"record_id": "P0101", "field": "title"},
            activity="normalize_from_source",
            agent={"type": "human", "id": "curator_1"},
            source={"title": "OEM Manual", "path": "https://oem.com/manual"},
            confidence="corroborated",
        )
        assert isinstance(rec.target, ProvenanceTarget)
        assert isinstance(rec.agent, ProvenanceAgent)
        assert isinstance(rec.source, ProvenanceSource)
        assert rec.confidence == ProvenanceConfidence.CORROBORATED

    def test_dtc_record_with_provenance_coercion(self) -> None:
        dtc = DtcRecord(
            code="P0101",
            title="MAF",
            subsystem="Air/Fuel",
            severity=Severity.MEDIUM,
            dtc_namespace=DtcNamespace.SAE_J2012,
            dtc_class=DtcClass.B2,
            provenance=[
                {
                    "provenance_id": "prv-p0101-01",
                    "target": {"record_id": "P0101", "field": "title"},
                    "activity": "normalize_from_source",
                    "agent": {"type": "system", "id": "kb_harvester"},
                    "source": {"title": "SAE J2012", "path": "https://sae.org/j2012"},
                    "confidence": "operator_verified",
                }
            ],
        )
        assert len(dtc.provenance) == 1
        assert isinstance(dtc.provenance[0], ProvenanceRecord)
        assert dtc.provenance[0].provenance_id == "prv-p0101-01"

    def test_validate_dtc_provenance_dependent_required_helper(self) -> None:
        # Bare stub without non-key fields -> passes
        bare_stub = {"code": "P9999", "dtc_namespace": "OEM", "dtc_class": "NoClass"}
        validate_dtc_provenance_dependent_required(bare_stub)

        # Enriched dict without provenance -> raises ValueError
        enriched_no_prov = {"code": "P0101", "title": "MAF", "dtc_namespace": "SAE_J2012", "dtc_class": "B2"}
        with pytest.raises(ValueError, match="dependentRequired violation"):
            validate_dtc_provenance_dependent_required(enriched_no_prov)

        # Enriched dict with valid provenance -> passes
        enriched_with_prov = {
            "code": "P0101",
            "title": "MAF",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "B2",
            "provenance": [{"provenance_id": "prv-p0101-01"}],
        }
        validate_dtc_provenance_dependent_required(enriched_with_prov)

    def test_synthesize_provenance_from_legacy_produces_valid_entries(
        self, prov_validator: jsonschema.Draft202012Validator
    ) -> None:
        sample_legacy = {
            "title": "MAF Sensor",
            "source": "obd2.com",
            "evidence_url": "https://obd2.com/dtc/p0101",
            "oem_source": "https://github.com/Wal33D/dtc-database",
            "oem_attribution": "MIT - Wal33D",
            "gm_source": "https://gsi.ext.gm.com/gmspo/mode6/pdf/",
            "gm_attribution": "GM GSI",
            "nhtsa_source": "api.nhtsa.gov/complaints",
            "title_tr": "MAF Sensörü",
            "title_tr_source": "bilingual DB",
            "description_en": "Air mass meter signal",
            "description_en_source": "https://wholefleet.ca",
        }
        prov_list = synthesize_provenance_from_legacy("P0101", sample_legacy)
        assert len(prov_list) >= 5
        for item in prov_list:
            prov_validator.validate(item)

    def test_synthesize_provenance_fallback_for_pure_standards(
        self, prov_validator: jsonschema.Draft202012Validator
    ) -> None:
        sample_standard_code = {
            "title": "Manufacturer-specific network code",
            "subsystem": "CAN Network",
            "severity": "MEDIUM",
        }
        prov_list = synthesize_provenance_from_legacy("U20DB", sample_standard_code)
        assert len(prov_list) == 1
        assert prov_list[0]["source"]["type"] == "standard"
        assert prov_list[0]["source"]["publisher"] == "SAE International"
        prov_validator.validate(prov_list[0])


class TestCopilotShapeGateAcceptsProvenance:
    def test_copilot_shape_gate_accepts_valid_provenance(self) -> None:
        valid_entry = {
            "title": "MAF Sensor",
            "subsystem": "Air/Fuel",
            "severity": "MEDIUM",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "B2",
            "provenance": [
                {
                    "provenance_id": "prv-p0101-01",
                    "target": {"record_id": "P0101", "field": "title"},
                }
            ],
        }
        assert _validate_dtc_entry_shape("P0101", valid_entry) is True

    def test_copilot_shape_gate_rejects_malformed_provenance(self) -> None:
        bad_entry = {
            "title": "MAF Sensor",
            "subsystem": "Air/Fuel",
            "severity": "MEDIUM",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "B2",
            "provenance": "not-a-list",
        }
        assert _validate_dtc_entry_shape("P0101", bad_entry) is False

        bad_id_entry = {
            "title": "MAF Sensor",
            "subsystem": "Air/Fuel",
            "severity": "MEDIUM",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "B2",
            "provenance": [{"provenance_id": "invalid_no_prv_prefix"}],
        }
        assert _validate_dtc_entry_shape("P0101", bad_id_entry) is False

# -*- coding: utf-8 -*-
"""Unit tests for AUTOSAR Dem / ISO 27145-3 DTC schema compliance (Aksiyon 12 / Faz 4.2).

Validates:
1. DtcNamespace (7 allowed values): SAE_J2012, SAE_J2012_4, ISO_14229_1, OBD, WWH_OBD, J1939, OEM.
2. DtcClass (5 allowed values): NoClass, A, B1, B2, C.
3. Deterministic namespace & class classification rules.
4. DtcRecord and DiagnosticEvent schema enforcement.
5. Invariant: 100% of records in data/diagnostics/dtc_database.json carry valid mandatory fields.
6. Copilot entry shape validation gates.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    DtcClass,
    DtcNamespace,
    DtcRecord,
    Severity,
    classify_dtc_class,
    classify_dtc_namespace,
)
from src.engine.ai.diagnostic_copilot import _validate_dtc_entry_shape

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB_PATH = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"


class TestDtcSchemaEnums:
    def test_dtc_namespace_allowed_values(self) -> None:
        expected = {"SAE_J2012", "SAE_J2012_4", "ISO_14229_1", "OBD", "WWH_OBD", "J1939", "OEM"}
        assert {ns.value for ns in DtcNamespace} == expected
        assert len(DtcNamespace) == 7

    def test_dtc_class_allowed_values(self) -> None:
        expected = {"NoClass", "A", "B1", "B2", "C"}
        assert {c.value for c in DtcClass} == expected
        assert len(DtcClass) == 5

    def test_enum_string_compatibility(self) -> None:
        assert DtcNamespace.SAE_J2012 == "SAE_J2012"
        assert DtcClass.A == "A"
        assert DtcClass("B1") is DtcClass.B1
        with pytest.raises(ValueError):
            DtcNamespace("INVALID_NS")
        with pytest.raises(ValueError):
            DtcClass("INVALID_CLASS")


class TestDtcClassifiers:
    @pytest.mark.parametrize(
        ("code", "expected_ns"),
        [
            ("P0101", DtcNamespace.SAE_J2012),
            ("P0300", DtcNamespace.SAE_J2012),
            ("P2001", DtcNamespace.SAE_J2012),
            ("P3400", DtcNamespace.SAE_J2012),
            ("P3499", DtcNamespace.SAE_J2012),
            ("P1101", DtcNamespace.OEM),
            ("P3000", DtcNamespace.OEM),
            ("P3399", DtcNamespace.OEM),
            ("P3B19", DtcNamespace.OEM),
            ("B0001", DtcNamespace.SAE_J2012),
            ("B2000", DtcNamespace.SAE_J2012),
            ("B1001", DtcNamespace.OEM),
            ("B3001", DtcNamespace.OEM),
            ("C0001", DtcNamespace.SAE_J2012),
            ("C2001", DtcNamespace.SAE_J2012),
            ("C1001", DtcNamespace.OEM),
            ("C3001", DtcNamespace.OEM),
            ("C6500", DtcNamespace.OEM),
            ("U0100", DtcNamespace.SAE_J2012),
            ("U2000", DtcNamespace.SAE_J2012),
            ("U1001", DtcNamespace.OEM),
            ("U3001", DtcNamespace.OEM),
            ("SPN 100 FMI 4", DtcNamespace.J1939),
            ("SPN_3251", DtcNamespace.J1939),
        ],
    )
    def test_classify_dtc_namespace(self, code: str, expected_ns: DtcNamespace) -> None:
        assert classify_dtc_namespace(code) == expected_ns

    @pytest.mark.parametrize(
        ("code", "severity", "expected_class"),
        [
            ("P0101", "CRITICAL_STOP", DtcClass.A),
            ("U0100", "CRITICAL_STOP", DtcClass.A),
            ("P0011", "HIGH", DtcClass.B1),
            ("P0171", "MEDIUM", DtcClass.B2),
            ("B1000", "MEDIUM", DtcClass.C),
            ("C1000", "MEDIUM", DtcClass.C),
            ("U0001", "MEDIUM", DtcClass.C),
            ("P0030", "LOW", DtcClass.C),
            ("B1431", "LOW", DtcClass.C),
            ("P0999", "INFO", DtcClass.C),
            ("P0999", "UNKNOWN", DtcClass.NoClass),
        ],
    )
    def test_classify_dtc_class(self, code: str, severity: str, expected_class: DtcClass) -> None:
        assert classify_dtc_class(code, severity) == expected_class


class TestDtcRecordModel:
    def test_dtc_record_valid(self) -> None:
        rec = DtcRecord(
            code="P0101",
            title="MAF Circuit",
            subsystem="Fuel & Air",
            severity=Severity.MEDIUM,
            dtc_namespace=DtcNamespace.SAE_J2012,
            dtc_class=DtcClass.B2,
        )
        assert rec.code == "P0101"
        assert rec.dtc_namespace == DtcNamespace.SAE_J2012
        assert rec.dtc_class == DtcClass.B2

    def test_dtc_record_coerces_and_validates(self) -> None:
        rec = DtcRecord(
            code="P0101",
            title="MAF Circuit",
            subsystem="Fuel & Air",
            severity=Severity.MEDIUM,
            dtc_namespace="SAE_J2012",  # string literal coercion
            dtc_class="B2",  # string literal coercion
        )
        assert rec.dtc_namespace is DtcNamespace.SAE_J2012
        assert rec.dtc_class is DtcClass.B2

    def test_dtc_record_rejects_invalid_namespace(self) -> None:
        with pytest.raises(ValueError, match="dtc_namespace must be one of"):
            DtcRecord(
                code="P0101",
                title="MAF Circuit",
                subsystem="Fuel & Air",
                severity=Severity.MEDIUM,
                dtc_namespace="INVALID_NS",
                dtc_class=DtcClass.B2,
            )

    def test_dtc_record_rejects_invalid_class(self) -> None:
        with pytest.raises(ValueError, match="dtc_class must be one of"):
            DtcRecord(
                code="P0101",
                title="MAF Circuit",
                subsystem="Fuel & Air",
                severity=Severity.MEDIUM,
                dtc_namespace=DtcNamespace.SAE_J2012,
                dtc_class="INVALID_CLASS",
            )


class TestDiagnosticEventIntegration:
    def test_diagnostic_event_accepts_namespace_and_class(self) -> None:
        ev = DiagnosticEvent(
            timestamp_ns=1000,
            code="P0101",
            domain=DiagnosticDomain.PASSENGER,
            severity=Severity.MEDIUM,
            status="ACTIVE",
            dtc_namespace=DtcNamespace.SAE_J2012,
            dtc_class=DtcClass.B2,
        )
        assert ev.dtc_namespace is DtcNamespace.SAE_J2012
        assert ev.dtc_class is DtcClass.B2

    def test_diagnostic_event_rejects_invalid_namespace(self) -> None:
        with pytest.raises(ValueError, match="dtc_namespace must be one of"):
            DiagnosticEvent(
                timestamp_ns=1000,
                code="P0101",
                domain=DiagnosticDomain.PASSENGER,
                severity=Severity.MEDIUM,
                status="ACTIVE",
                dtc_namespace="INVALID_NS",
            )


class TestDtcDatabaseIntegrity:
    def test_all_production_records_have_mandatory_namespace_and_class(self) -> None:
        assert DTC_DB_PATH.is_file(), "dtc_database.json missing"
        data = json.loads(DTC_DB_PATH.read_text(encoding="utf-8"))
        assert len(data) >= 14000, f"DTC database unexpectedly small: {len(data)}"

        allowed_namespaces = {ns.value for ns in DtcNamespace}
        allowed_classes = {c.value for c in DtcClass}

        for code, rec in data.items():
            assert "dtc_namespace" in rec, f"{code}: missing mandatory 'dtc_namespace'"
            assert "dtc_class" in rec, f"{code}: missing mandatory 'dtc_class'"

            ns = rec["dtc_namespace"]
            assert ns in allowed_namespaces, f"{code}: invalid dtc_namespace '{ns}'"

            cls = rec["dtc_class"]
            assert cls in allowed_classes, f"{code}: invalid dtc_class '{cls}'"


class TestCopilotShapeValidation:
    def test_validator_accepts_valid_entry(self) -> None:
        entry = {
            "title": "Test Title",
            "subsystem": "Test Subsystem",
            "severity": "MEDIUM",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "B2",
            "steps": [["Step 1", "Target", "Easy"]],
        }
        assert _validate_dtc_entry_shape("P0101", entry) is True

    def test_validator_rejects_missing_namespace(self) -> None:
        entry = {
            "title": "Test Title",
            "subsystem": "Test Subsystem",
            "severity": "MEDIUM",
            "dtc_class": "B2",
        }
        assert _validate_dtc_entry_shape("P0101", entry) is False

    def test_validator_rejects_missing_class(self) -> None:
        entry = {
            "title": "Test Title",
            "subsystem": "Test Subsystem",
            "severity": "MEDIUM",
            "dtc_namespace": "SAE_J2012",
        }
        assert _validate_dtc_entry_shape("P0101", entry) is False

    def test_validator_rejects_invalid_namespace(self) -> None:
        entry = {
            "title": "Test Title",
            "subsystem": "Test Subsystem",
            "severity": "MEDIUM",
            "dtc_namespace": "BOGUS_NAMESPACE",
            "dtc_class": "B2",
        }
        assert _validate_dtc_entry_shape("P0101", entry) is False

    def test_validator_rejects_invalid_class(self) -> None:
        entry = {
            "title": "Test Title",
            "subsystem": "Test Subsystem",
            "severity": "MEDIUM",
            "dtc_namespace": "SAE_J2012",
            "dtc_class": "BOGUS_CLASS",
        }
        assert _validate_dtc_entry_shape("P0101", entry) is False

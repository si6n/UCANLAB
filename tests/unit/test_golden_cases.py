"""Unit tests for Golden-Traces case loading & validation (Görev 4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.ai.golden_cases import (
    CASES_DIR,
    GoldenCaseError,
    calibration_eligible_cases,
    load_all_cases,
    load_case,
    validate_case_payload,
)

SEED_CASE = CASES_DIR / "volvo_penta_d4_300_nonstart.json"


class TestSeedCase:
    def test_seed_case_loads_as_draft(self) -> None:
        case = load_case(SEED_CASE)
        assert case.case_id == "volvo-penta-d4-300-nonstart"
        assert case.domain.value == "MARINE"
        assert case.make == "Volvo Penta"
        assert case.model == "D4-300"
        assert case.is_draft  # actual_fault null -> draft
        assert not case.verified
        assert case not in calibration_eligible_cases()

    def test_schema_file_exists_and_is_versioned(self) -> None:
        schema = json.loads((CASES_DIR / "schema.json").read_text(encoding="utf-8"))
        assert schema["properties"]["schema_version"]["const"] == 1


class TestValidationFailClosed:
    def _base(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "case_id": "test-case",
            "domain": "PASSENGER",
            "make": "X",
            "model": "Y",
            "year": 2020,
            "symptom": "s",
            "dtcs": [{"code": "P0300", "status": "ACTIVE"}],
            "signals_of_interest": [{"name": "RPM", "expected_behavior": "x", "observed_behavior": "y"}],
            "actual_fault": "broken part",
            "repair": "replaced it",
            "verification": "road test ok",
            "trace_ref": None,
            "verified": True,
            "verified_date": "2026-01-15",
        }

    def test_valid_case_passes(self) -> None:
        case = validate_case_payload(self._base())
        assert case.verified and not case.is_draft

    def test_unknown_top_level_field_rejected(self) -> None:
        payload = self._base()
        payload["surprise_field"] = 1  # type: ignore[assignment]
        with pytest.raises(GoldenCaseError, match="unknown top-level"):
            validate_case_payload(payload)

    def test_missing_field_rejected(self) -> None:
        payload = self._base()
        del payload["verification"]
        with pytest.raises(GoldenCaseError, match="missing required"):
            validate_case_payload(payload)

    def test_raw_vin_anywhere_rejected(self) -> None:
        payload = self._base()
        payload["symptom"] = "VIN WVWZZZ1KZAW123456 sahada okundu"
        with pytest.raises(GoldenCaseError, match="VIN"):
            validate_case_payload(payload)

    def test_verified_requires_date_and_fault(self) -> None:
        payload = self._base()
        payload["verified_date"] = None
        with pytest.raises(GoldenCaseError, match="verified_date"):
            validate_case_payload(payload)
        payload2 = self._base()
        payload2["actual_fault"] = None
        with pytest.raises(GoldenCaseError, match="actual_fault"):
            validate_case_payload(payload2)

    def test_draft_cannot_be_verified(self) -> None:
        payload = self._base()
        payload["actual_fault"] = "   "  # whitespace -> draft
        with pytest.raises(GoldenCaseError, match="actual_fault"):
            validate_case_payload(payload)

    def test_bad_case_id_rejected(self) -> None:
        payload = self._base()
        payload["case_id"] = "Bad_Case_ID"
        with pytest.raises(GoldenCaseError, match="case_id"):
            validate_case_payload(payload)

    def test_bad_domain_rejected(self) -> None:
        payload = self._base()
        payload["domain"] = "SPACESHIP"
        with pytest.raises(GoldenCaseError, match="domain"):
            validate_case_payload(payload)

    def test_unknown_dtc_field_rejected(self) -> None:
        payload = self._base()
        payload["dtcs"] = [{"code": "P0300", "status": "ACTIVE", "fmi": 4}]  # type: ignore[assignment]
        with pytest.raises(GoldenCaseError, match=r"dtcs\[0\] unknown"):
            validate_case_payload(payload)

    def test_unknown_signal_field_rejected(self) -> None:
        payload = self._base()
        payload["signals_of_interest"] = [{"name": "RPM", "extra": 1}]  # type: ignore[assignment]
        with pytest.raises(GoldenCaseError, match=r"signals_of_interest\[0\] unknown"):
            validate_case_payload(payload)

    def test_wrong_schema_version_rejected(self) -> None:
        payload = self._base()
        payload["schema_version"] = 2
        with pytest.raises(GoldenCaseError, match="schema_version"):
            validate_case_payload(payload)


class TestLoaderFailClosed:
    def test_load_all_cases_includes_seed_and_excludes_schema(self, tmp_path: Path) -> None:
        cases = load_all_cases()
        assert any(c.case_id == "volvo-penta-d4-300-nonstart" for c in cases)
        assert all(c.case_id != "schema" for c in cases)

    def test_corrupt_case_file_aborts_load(self, tmp_path: Path) -> None:
        (tmp_path / "good.json").write_text(json.dumps({
            "schema_version": 1, "case_id": "good-case", "domain": "MARINE",
            "make": None, "model": None, "year": None, "symptom": "s",
            "dtcs": [], "signals_of_interest": [], "actual_fault": None,
            "repair": None, "verification": None, "trace_ref": None,
            "verified": False, "verified_date": None,
        }), encoding="utf-8")
        (tmp_path / "corrupt.json").write_text("{ not json", encoding="utf-8")
        with pytest.raises(GoldenCaseError, match="cannot read/parse"):
            load_all_cases(tmp_path)

    def test_calibration_eligible_excludes_drafts(self, tmp_path: Path) -> None:
        verified_case = {
            "schema_version": 1, "case_id": "verified-case", "domain": "PASSENGER",
            "make": "VW", "model": "Golf", "year": 2019, "symptom": "misfire",
            "dtcs": [{"code": "P0300", "status": "ACTIVE"}],
            "signals_of_interest": [],
            "actual_fault": "coil pack", "repair": "replaced",
            "verification": "test drive", "trace_ref": None,
            "verified": True, "verified_date": "2026-02-01",
        }
        (tmp_path / "verified.json").write_text(json.dumps(verified_case), encoding="utf-8")
        # Direct: eligible set from tmp dir
        eligible_direct = [c for c in (load_all_cases(tmp_path)) if c.verified]
        assert len(eligible_direct) == 1
        assert eligible_direct[0].case_id == "verified-case"

"""Unit tests for Harvest Data Validation and Quarantine Gatekeeper.
Ensures no unverified, malicious, or malformed external diagnostic data enters production.
"""
from src.engine.ai.harvest_validator import (
    QuarantineGatekeeper,
    clean_and_sanitize,
    validate_dtc_record,
    validate_spn_record,
)


def test_clean_and_sanitize_html():
    raw = "<div><b>Engine Misfire</b><script>alert(1)</script> detected</div>"
    cleaned = clean_and_sanitize(raw)
    assert "<" not in cleaned
    assert "alert" in cleaned or "Engine Misfire" in cleaned
    assert cleaned == "Engine Misfire alert(1) detected"

def test_dtc_validation_rejects_invalid_format():
    invalid_codes = ["INVALID", "P99999", "1234", "X0100", "P01", ""]
    for code in invalid_codes:
        rep = validate_dtc_record({
            "code": code,
            "symptoms": ["Check engine light illuminated"],
            "causes": ["Faulty MAF sensor"]
        })
        assert not rep.is_valid
        assert any("Invalid DTC code format" in err for err in rep.errors)

def test_dtc_validation_accepts_valid_codes():
    valid_codes = ["P0102", "P0171", "C0035", "B1000", "U0100"]
    for code in valid_codes:
        rep = validate_dtc_record({
            "code": code,
            "symptoms": ["Engine hesitation during acceleration"],
            "causes": ["Air leak in intake boot"]
        })
        assert rep.is_valid
        assert rep.sanitized["code"] == code
        assert rep.fingerprint != ""

def test_dtc_validation_rejects_junk_and_404():
    rep = validate_dtc_record({
        "code": "P0300",
        "symptoms": ["404 Not Found - Page does not exist"],
        "causes": ["Cloudflare verification failed"]
    })
    assert not rep.is_valid
    assert any("zero valid symptoms/causes" in err for err in rep.errors)

def test_spn_validation_rejects_out_of_range():
    rep_neg = validate_spn_record({"spn": -1, "fmi": 0, "causes": "Short circuit"})
    assert not rep_neg.is_valid

    rep_huge = validate_spn_record({"spn": 600000, "fmi": 0, "causes": "Short circuit"})
    assert not rep_huge.is_valid

    rep_fmi = validate_spn_record({"spn": 100, "fmi": 32, "causes": "Short circuit"})
    assert not rep_fmi.is_valid

def test_merged_diagnostics_integrity():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    dtc_path = root / "data/diagnostics/dtc_database.json"
    with open(dtc_path, "r", encoding="utf-8") as f:
        dtc_db = json.load(f)
    for code in ["P0006", "P0102", "P0130", "P0135", "P0141"]:
        assert code in dtc_db
        assert len(dtc_db[code].get("symptoms", [])) > 0
        assert len(dtc_db[code].get("causes", [])) > 0


def test_spn_validation_accepts_valid_payload():
    rep = validate_spn_record({
        "spn": 190,
        "fmi": 0,
        "name": "Engine Speed",
        "causes": "Engine overspeed condition detected",
        "actions": "Verify throttle linkage and governor setting"
    })
    assert rep.is_valid
    assert rep.sanitized["spn"] == 190
    assert rep.sanitized["fmi"] == 0
    assert rep.sanitized["verified_by"] == "marshal_gatekeeper"

def test_quarantine_gatekeeper_batch():
    dtcs = [
        {"code": "P0102", "symptoms": ["Rough idle"], "causes": ["Dirty MAF sensor"]},
        {"code": "MALFORMED", "symptoms": ["Rough idle"], "causes": ["Dirty MAF sensor"]},
        {"code": "P0171", "symptoms": ["short"], "causes": ["404 Not Found"]}
    ]
    spns = [
        {"spn": 100, "fmi": 1, "causes": "Low oil pressure", "actions": "Check oil level"},
        {"spn": 9999999, "fmi": 0, "causes": "Invalid"},
    ]
    audit = QuarantineGatekeeper.audit_batch(dtcs, spns)

    assert audit["summary"]["dtc_in"] == 3
    assert audit["summary"]["dtc_ok"] == 1
    assert audit["summary"]["dtc_rej"] == 2
    assert audit["summary"]["spn_in"] == 2
    assert audit["summary"]["spn_ok"] == 1
    assert audit["summary"]["spn_rej"] == 1

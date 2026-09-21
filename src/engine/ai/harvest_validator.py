"""Harvest Data Validation & Quarantine Gatekeeper.
Enforces ASIL-B/D zero-fabrication and data sanitization invariants.
Never blindly merges scraped external data into production databases.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

DTC_PATTERN = re.compile(r"^[PCBU][0-3][0-9A-Fa-f]{3}$")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
JUNK_TEXT_PATTERN = re.compile(
    r"(404|not found|access denied|captcha|cloudflare|enable javascript|robot|cookie policy|lorem ipsum)",
    re.IGNORECASE
)
# F-24 (P0): the pattern above only recognised HTTP/scraper boilerplate, so raw
# JavaScript / JSON-LD artifacts that leaked into harvested records (e.g.
# "p2032 }}", "function loadData(){return null;}", "JSON.parse(data)") sailed
# through the gate and were stamped `verified_by="marshal_gatekeeper"`.
#
# Each alternative below requires UNAMBIGUOUS code syntax, so ordinary English
# technical prose is never flagged: e.g. "sometimes a function of load" must
# NOT match, while "function(a){" and "function loadData()" must. Bare "=>" and
# bare "function <word>" were deliberately rejected as alternatives because
# they produce exactly that false positive.
CODE_ARTIFACT_PATTERN = re.compile(
    r"(\{\{|\}\}"
    r"|function\s*[\(a-zA-Z_$][\w$]*\s*\([^)]*\)\s*\{"
    r"|function\s*\([^)]*\)\s*\{"
    r"|\b(?:var|let|const)\s+[\w$]+\s*="
    r"|JSON\.(?:parse|stringify)\s*\("
    r"|\.push\s*\("
    r"|</?script\b"
    r"|__(?:NEXT_DATA|NUXT)__"
    r"|\bwindow\.[\w$]+\s*[=.]"
    r"|\bdocument\.(?:getElementById|querySelector|write)\b"
    r"|\"@type\"|'@type'|\"@context\")",
    re.IGNORECASE,
)

@dataclass
class ValidationReport:
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    sanitized: Optional[Dict[str, Any]] = None
    fingerprint: str = ""

def clean_and_sanitize(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    unescaped = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", HTML_TAG_PATTERN.sub(" ", unescaped)).strip()

def validate_text(text: str, field_name: str, min_len: int = 6) -> Tuple[bool, Optional[str]]:
    if len(text) < min_len:
        return False, f"{field_name} string length ({len(text)}) below threshold {min_len}"
    if JUNK_TEXT_PATTERN.search(text):
        return False, f"{field_name} contains junk/error text pattern"
    # F-24: quarantine raw code artifacts masquerading as prose.
    if CODE_ARTIFACT_PATTERN.search(text):
        return False, f"{field_name} contains scraped code/JS artifact"
    return True, None

def validate_dtc_record(record: Dict[str, Any]) -> ValidationReport:
    errors, warnings = [], []
    code = str(record.get("code", "")).strip().upper()
    if not DTC_PATTERN.match(code):
        return ValidationReport(False, [f"Invalid DTC code format '{code}'"])

    sanitized_symptoms = []
    for s in record.get("symptoms", []) if isinstance(record.get("symptoms"), list) else []:
        c = clean_and_sanitize(s)
        ok, err = validate_text(c, "symptom")
        if ok:
            sanitized_symptoms.append(c)
        elif err:
            warnings.append(err)

    sanitized_causes = []
    for c_raw in record.get("causes", []) if isinstance(record.get("causes"), list) else []:
        c = clean_and_sanitize(c_raw)
        ok, err = validate_text(c, "cause")
        if ok:
            sanitized_causes.append(c)
        elif err:
            warnings.append(err)

    if not sanitized_symptoms and not sanitized_causes:
        errors.append(f"DTC {code} has zero valid symptoms/causes after sanitization")

    if errors:
        return ValidationReport(False, errors, warnings)

    res = {
        "code": code,
        "symptoms": sanitized_symptoms,
        "causes": sanitized_causes,
        "source": clean_and_sanitize(record.get("source", "scout")),
        "source_url": str(record.get("url", "")).strip(),
        "verified_by": "marshal_gatekeeper"
    }
    fp = hashlib.sha256(repr(sorted(res.items())).encode("utf-8")).hexdigest()
    return ValidationReport(True, [], warnings, res, fp)

def validate_spn_record(record: Dict[str, Any]) -> ValidationReport:
    errors, warnings = [], []
    try:
        spn = int(record.get("spn"))
        if not (0 <= spn <= 524287):
            errors.append(f"SPN {spn} out of 19-bit range")
    except Exception:
        return ValidationReport(False, [f"Invalid SPN {record.get('spn')}"])

    fmi = record.get("fmi")
    sanitized_fmi = None
    if fmi is not None:
        try:
            fmi_val = int(fmi)
            if not (0 <= fmi_val <= 31):
                errors.append(f"FMI {fmi_val} out of range [0, 31]")
            else:
                sanitized_fmi = fmi_val
        except Exception:
            errors.append(f"Invalid FMI {fmi}")

    causes = clean_and_sanitize(record.get("causes"))
    actions = clean_and_sanitize(record.get("actions"))
    name = clean_and_sanitize(record.get("name"))

    if causes:
        ok, err = validate_text(causes, "causes")
        if not ok:
            errors.append(err)
    if actions:
        ok, err = validate_text(actions, "actions")
        if not ok:
            errors.append(err)

    if not causes and not actions:
        errors.append(f"SPN {spn} missing both causes and actions")

    if errors:
        return ValidationReport(False, errors, warnings)

    res = {
        "spn": spn, "fmi": sanitized_fmi, "name": name,
        "causes": causes, "actions": actions, "verified_by": "marshal_gatekeeper"
    }
    fp = hashlib.sha256(repr(sorted(res.items())).encode("utf-8")).hexdigest()
    return ValidationReport(True, [], warnings, res, fp)

class QuarantineGatekeeper:
    @classmethod
    def audit_batch(cls, dtc_records: List[Dict[str, Any]], spn_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        app_d, rej_d, app_s, rej_s = [], [], [], []
        for d in dtc_records:
            rep = validate_dtc_record(d)
            if rep.is_valid and rep.sanitized:
                app_d.append(rep.sanitized)
            else:
                rej_d.append({"raw": d, "reasons": rep.errors})
        for s in spn_records:
            rep = validate_spn_record(s)
            if rep.is_valid and rep.sanitized:
                app_s.append(rep.sanitized)
            else:
                rej_s.append({"raw": s, "reasons": rep.errors})
        return {
            "approved_dtcs": app_d, "quarantined_dtcs": rej_d,
            "approved_spns": app_s, "quarantined_spns": rej_s,
            "summary": {
                "dtc_in": len(dtc_records), "dtc_ok": len(app_d), "dtc_rej": len(rej_d),
                "spn_in": len(spn_records), "spn_ok": len(app_s), "spn_rej": len(rej_s)
            }
        }

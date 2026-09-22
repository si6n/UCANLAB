#!/usr/bin/env python3
"""Find which of the 15 NEW DTC codes are being rejected."""
import json
import sys

sys.path.insert(0, '.')

from src.engine.ai.harvest_validator import validate_dtc_record

with open("data/diagnostics/dtc_database.json", encoding="utf-8") as f:
    db = json.load(f)

# The 15 new codes added by T_cd363b33 merge (they have wholefleet in _source fields AND didn't exist before)
# Check by looking for codes that have "wholefleet.ca free harvest" as description_en_source AND are new
# Actually, let's find codes where description_en was FILLED (not just new codes)
# The 15 new codes should have "wholefleet" in the title/subsystem

# Let's check which codes have "wholefleet.ca free harvest" in their description_en_source
# AND also have it in their subsystem (which would indicate they're NEW)
new_codes = []
filled_codes = []
for code, entry in db.items():
    if not isinstance(entry, dict):
        continue
    src = str(entry.get("description_en_source", ""))
    if "wholefleet" in src.lower():
        subsys = str(entry.get("subsystem", ""))
        if "OEM-proprietary" in subsys and "wholefleet" in src.lower():
            new_codes.append(code)
        else:
            filled_codes.append(code)

print(f"New codes (OEM-proprietary): {len(new_codes)}")
print(f"Filled codes (existing): {len(filled_codes)}")
print(f"Total: {len(new_codes) + len(filled_codes)}")

# Now check which of the new codes pass validation

rejected_new = []
for code in new_codes:
    info = db[code]
    try:
        report = validate_dtc_record({**info, "code": code})
        if report is None or not report.is_valid or not report.sanitized:
            rejected_new.append(code)
            print(f"\n[REJECTED] {code}")
            print(f"  title: {info.get('title', '')[:60]}")
            print(f"  severity: {info.get('severity', '')}")
            print(f"  causes: {info.get('causes', 'MISSING')}")
            print(f"  symptoms: {info.get('symptoms', 'MISSING')}")
            print(f"  description_en: {str(info.get('description_en', ''))[:60]}")
            if report:
                print(f"  errors: {getattr(report, 'errors', [])[:3]}")
    except Exception as e:
        rejected_new.append(code)
        print(f"\n[ERROR] {code}: {e}")

print(f"\nNew codes rejected: {len(rejected_new)}/{len(new_codes)}")
print(f"Rejected: {rejected_new}")

# Also check filled codes
rejected_filled = []
for code in filled_codes:
    info = db[code]
    try:
        report = validate_dtc_record({**info, "code": code})
        if report is None or not report.is_valid or not report.sanitized:
            rejected_filled.append(code)
    except Exception:
        rejected_filled.append(code)

print(f"\nFilled codes rejected: {len(rejected_filled)}/{len(filled_codes)}")
print(f"Total rejected: {len(rejected_new) + len(rejected_filled)}")

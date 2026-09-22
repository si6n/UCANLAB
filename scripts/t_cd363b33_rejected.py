#!/usr/bin/env python3
"""Find which new DTC codes are being rejected by the KB loader."""
import json
import sys

sys.path.insert(0, '.')

from src.engine.ai.diagnostic_copilot import _validate_dtc_entry_shape
from src.engine.ai.harvest_validator import validate_dtc_record

# Load the DB
with open("data/diagnostics/dtc_database.json", encoding="utf-8") as f:
    db = json.load(f)

# The 15 new codes added by T_cd363b33
new_codes = [
    "P100_3", "P100_4", "P102_1", "P102_2", "P102_3", "P102_4",
    "P105_1", "P105_2", "P105_3", "P105_4", "P107_1",
    "P107_2", "P108_1", "P108_2", "P108_3"
]

# Find the actual new codes by checking which ones have wholefleet source
wholefleet_codes = []
for code, entry in db.items():
    if isinstance(entry, dict):
        src = entry.get("description_en_source", "")
        if "wholefleet" in str(src).lower():
            wholefleet_codes.append(code)

print(f"Total wholefleet-sourced DTC codes: {len(wholefleet_codes)}")
print(f"Codes: {sorted(wholefleet_codes)[:20]}...")

# Now validate each

rejected_shape = []
rejected_quarantine = []

for code in wholefleet_codes:
    info = db[code]
    # Check shape
    if not _validate_dtc_entry_shape(code, info):
        rejected_shape.append(code)
        print(f"\n[SHAPE FAIL] {code}")
        for field in ("title", "subsystem", "severity"):
            val = info.get(field)
            print(f"  {field}: {repr(val)[:100]}")
        steps = info.get("steps")
        print(f"  steps type: {type(steps).__name__}")
        if isinstance(steps, list) and steps:
            print(f"  steps[0] type: {type(steps[0]).__name__}, len={len(steps[0]) if hasattr(steps[0], '__len__') else 'N/A'}")
        causes = info.get("causes")
        print(f"  causes type: {type(causes).__name__}")
        continue

    # Check quarantine
    try:
        report = validate_dtc_record({**info, "code": code})
        if report is None or not report.is_valid or not report.sanitized:
            rejected_quarantine.append(code)
            print(f"\n[QUARANTINE FAIL] {code}")
            if report:
                print(f"  is_valid: {report.is_valid}")
                print(f"  errors: {getattr(report, 'errors', [])[:5]}")
    except Exception as e:
        rejected_quarantine.append(code)
        print(f"\n[QUARANTINE ERROR] {code}: {e}")

print("\nSummary:")
print(f"  Shape rejected: {len(rejected_shape)}")
print(f"  Quarantine rejected: {len(rejected_quarantine)}")
print(f"  Total rejected: {len(rejected_shape) + len(rejected_quarantine)}")
print(f"  Total would be added: {len(wholefleet_codes) - len(rejected_shape) - len(rejected_quarantine)}")

# Check what the KB count would be
# 14469 existing (pre-merge) + (15 - rejected) = expected KB count
# But 14469 already includes some entries that may be in EXPERT_KNOWLEDGE_BASE as hardcoded
# Let's just count the new codes that would pass
passing = [c for c in wholefleet_codes if c not in rejected_shape and c not in rejected_quarantine]
print(f"  Passing new codes: {len(passing)}")
print(f"  Expected KB total: 14469 + {len(passing)} = {14469 + len(passing)}")

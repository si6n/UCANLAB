"""Fix: restore symptoms_en that were overwritten by the merge fix script.
The T46 merge already had symptoms_en from troublecodes.net. Our fix script
wrongly overwrote some of them with obd-codes.com text. Restore from git.
"""
import json
import os
import subprocess

REPO = r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool"
DTC_DB_PATH = os.path.join(REPO, "data", "diagnostics", "dtc_database.json")

# Get the original DTC DB from git HEAD
result = subprocess.run(
    ["git", "show", "HEAD:data/diagnostics/dtc_database.json"],
    capture_output=True, text=True, encoding="utf-8",
    cwd=REPO,
)
orig_db = json.loads(result.stdout)

# Load current DB
with open(DTC_DB_PATH, "r", encoding="utf-8", errors="replace") as f:
    curr_db = json.load(f)

# Fields to restore if they were overwritten
RESTORE_FIELDS = [
    "symptoms_en", "symptoms_en_source",
    "causes_en", "causes_en_source",
    "description_en", "description_en_source",
    "title_en", "title_en_source",
]

restored = 0
removed_bad = 0

for code, orig_rec in orig_db.items():
    if code not in curr_db:
        continue
    curr_rec = curr_db[code]

    for field in RESTORE_FIELDS:
        if field in orig_rec and field in curr_rec:
            # If the original had a value and current is different, restore original
            orig_val = orig_rec[field]
            curr_val = curr_rec[field]
            if orig_val != curr_val:
                curr_rec[field] = orig_val
                restored += 1

# Remove the bad symptoms_source field (should be symptoms_en_source)
# and steps_source (should be steps_en_source)
for _code, curr_rec in curr_db.items():
    if "symptoms_source" in curr_rec:
        # This was added by our merge - if symptoms_en_source already exists, remove it
        if "symptoms_en_source" in curr_rec:
            del curr_rec["symptoms_source"]
            removed_bad += 1
        else:
            # Rename to symptoms_en_source
            curr_rec["symptoms_en_source"] = curr_rec.pop("symptoms_source")
            removed_bad += 1
    if "causes_source" in curr_rec and "causes_en_source" in curr_rec:
        del curr_rec["causes_source"]
        removed_bad += 1
    if "steps_source" in curr_rec and "steps_en_source" in curr_rec:
        del curr_rec["steps_source"]
        removed_bad += 1

# Now check which entries have steps_en from our merge that should be kept
# (only if steps_en was empty before)
kept_steps = 0
for code, orig_rec in orig_db.items():
    if code not in curr_db:
        continue
    curr_rec = curr_db[code]

    # If original had no steps_en but current does, and it has steps_en_source,
    # keep it (it's our new contribution)
    orig_had_steps_en = bool(orig_rec.get("steps_en"))
    curr_has_steps_en = bool(curr_rec.get("steps_en"))

    if not orig_had_steps_en and curr_has_steps_en:
        # Check if steps_en_source is from obd-codes.com (our merge)
        src = curr_rec.get("steps_en_source", "")
        if "obd-codes.com" in src:
            kept_steps += 1
        else:
            # This was from T46, shouldn't have been touched - restore
            pass

print(f"Restored {restored} fields to original values")
print(f"Removed/renamed {removed_bad} bad source fields")
print(f"Kept {kept_steps} new steps_en from obd-codes.com")

# Save
with open(DTC_DB_PATH, "w", encoding="utf-8", newline="\n") as f:
    f.write(json.dumps(curr_db, ensure_ascii=False, indent=1) + "\n")

print(f"Saved DTC DB: {len(curr_db)} codes")

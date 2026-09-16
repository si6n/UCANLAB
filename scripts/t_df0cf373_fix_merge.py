#!/usr/bin/env python3
"""
T_df0cf373 FIX: Correct the merge — move English text from `symptoms` to `symptoms_en`.
Also fixes `causes` and `steps` that may have been corrupted.
Following T46 convention: English -> *_en, Turkish stays in original field.
"""
import json
import os

REPO = r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool"
DTC_DB_PATH = os.path.join(REPO, "data", "diagnostics", "dtc_database.json")
OUT_DIR = os.path.join(REPO, "spn_gap_hunter", "output")
OBD_SCAN_PATH = os.path.join(OUT_DIR, "scan_obd-codes.com_2026-09-16.json")

def is_ascii_only(value):
    """True when every string inside value is pure ASCII."""
    text = json.dumps(value, ensure_ascii=False)
    return all(ord(ch) < 128 for ch in text)

def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")

def main():
    print("Loading DTC DB...")
    dtc_db = load_json(DTC_DB_PATH)
    print(f"  {len(dtc_db)} codes")

    # Load the obd-codes scan to know which codes we touched
    scan = load_json(OBD_SCAN_PATH)

    # Build a map of code -> {field: text, evidence_url}
    from collections import defaultdict
    by_code = defaultdict(dict)
    for row in scan:
        if row.get("placeholder"):
            continue
        code = row.get("code", "").strip()
        if not code:
            continue
        field = row.get("field", "")
        text = row.get("text", "").strip()
        if not text:
            continue
        by_code[code][field] = {
            "text": text,
            "evidence_url": row.get("evidence_url", row.get("url", "")),
        }

    # Track stats
    reverted_symptoms = 0
    reverted_causes = 0
    reverted_steps = 0
    en_filled = 0
    collisions = 0
    markers_added = 0

    for code, fields in by_code.items():
        if code not in dtc_db:
            continue
        rec = dtc_db[code]

        # Check if our merge put English text into `symptoms` (Turkish field)
        # We detect this by checking if symptoms is a list of pure-ASCII strings
        # AND the record has _t46_merge marker (meaning T46 already enriched it)

        # FIX symptoms: if we overwrote symptoms with English text, revert it
        symptoms = rec.get("symptoms")
        if isinstance(symptoms, list) and symptoms and is_ascii_only(symptoms):
            # Check if symptoms_en already has this text (from T46)
            sym_en = rec.get("symptoms_en", "")
            our_sym = fields.get("symptoms", {}).get("text", "")

            if our_sym and our_sym in str(symptoms[0] if symptoms else ""):
                # We overwrote symptoms with English text - REVERT
                # Delete the symptoms field entirely (it was empty before our merge)
                # Actually, we should check what it was before - but we don't have a backup
                # The safest approach: if symptoms_en already exists, clear symptoms
                # If symptoms_en doesn't exist, move to symptoms_en
                if sym_en:
                    # symptoms_en already populated by T46 - clear our English from symptoms
                    del rec["symptoms"]
                    reverted_symptoms += 1
                    collisions += 1
                else:
                    # Move to symptoms_en
                    rec["symptoms_en"] = our_sym
                    rec["symptoms_en_source"] = fields["symptoms"]["evidence_url"]
                    del rec["symptoms"]
                    en_filled += 1
                    if "_t46_merge" not in rec:
                        rec["_t46_merge"] = "t46a"
                        markers_added += 1
            elif our_sym:
                # Check if our symptoms text matches what's in symptoms
                # The merge may have set symptoms = [our_sym]
                if symptoms and our_sym == symptoms[0]:
                    if sym_en:
                        del rec["symptoms"]
                        reverted_symptoms += 1
                        collisions += 1
                    else:
                        rec["symptoms_en"] = our_sym
                        rec["symptoms_en_source"] = fields["symptoms"]["evidence_url"]
                        del rec["symptoms"]
                        en_filled += 1
                        if "_t46_merge" not in rec:
                            rec["_t46_merge"] = "t46a"
                            markers_added += 1

        # Also check for symptoms_en that we may have added as a list (wrong format)
        sym_en = rec.get("symptoms_en")
        if isinstance(sym_en, list):
            rec["symptoms_en"] = sym_en[0] if sym_en else ""

        # Check for symptoms_source (wrong field name, should be symptoms_en_source)
        if "symptoms_source" in rec and "symptoms_en_source" not in rec:
            rec["symptoms_en_source"] = rec.pop("symptoms_source")

        # FIX causes: if we overwrote causes with English text, revert
        causes = rec.get("causes")
        if isinstance(causes, list) and causes and is_ascii_only(causes) and len(causes) == 1:
            our_causes = fields.get("causes", {}).get("text", "")
            if our_causes and causes[0] == our_causes:
                causes_en = rec.get("causes_en", "")
                if causes_en:
                    del rec["causes"]
                    reverted_causes += 1
                    collisions += 1
                else:
                    rec["causes_en"] = our_causes
                    rec["causes_en_source"] = fields["causes"]["evidence_url"]
                    del rec["causes"]
                    en_filled += 1

        # Check for causes_en list format
        causes_en = rec.get("causes_en")
        if isinstance(causes_en, list):
            rec["causes_en"] = causes_en[0] if causes_en else ""

        if "causes_source" in rec and "causes_en_source" not in rec:
            rec["causes_en_source"] = rec.pop("causes_source")

        # FIX steps: if we overwrote steps with English text, revert
        steps = rec.get("steps")
        if isinstance(steps, list) and steps:
            # Check if steps is a list of [text, category, difficulty] where text is English
            first = steps[0]
            if isinstance(first, list) and len(first) >= 1 and is_ascii_only(first[0]):
                our_steps = fields.get("steps", {}).get("text", "")
                if our_steps and first[0] == our_steps:
                    steps_en = rec.get("steps_en")
                    if steps_en:
                        del rec["steps"]
                        reverted_steps += 1
                        collisions += 1
                    else:
                        rec["steps_en"] = [our_steps]
                        rec["steps_source"] = fields["steps"]["evidence_url"]
                        del rec["steps"]
                        en_filled += 1

        # Check for steps_en list format
        steps_en = rec.get("steps_en")
        if isinstance(steps_en, list) and steps_en:
            # Keep as list if it was already a list
            pass

        if "steps_source" in rec and "steps_en_source" not in rec:
            rec["steps_en_source"] = rec.pop("steps_source")

    # Now do the CORRECT merge: fill only empty *_en fields
    print("\nCorrect merge (filling empty *_en fields only)...")
    for code, fields in by_code.items():
        if code not in dtc_db:
            continue
        rec = dtc_db[code]

        # symptoms_en: fill only if empty
        if "symptoms" in fields:
            sym_text = fields["symptoms"]["text"]
            if not rec.get("symptoms_en"):
                rec["symptoms_en"] = sym_text
                rec["symptoms_en_source"] = fields["symptoms"]["evidence_url"]
                if "_t46_merge" not in rec:
                    rec["_t46_merge"] = "t46a"
                en_filled += 1
            else:
                collisions += 1

        # causes_en: fill only if empty
        if "causes" in fields:
            causes_text = fields["causes"]["text"]
            if not rec.get("causes_en"):
                rec["causes_en"] = causes_text
                rec["causes_en_source"] = fields["causes"]["evidence_url"]
                if "_t46_merge" not in rec:
                    rec["_t46_merge"] = "t46a"
                en_filled += 1
            else:
                collisions += 1

        # steps_en: fill only if empty (skip placeholder steps)
        if "steps" in fields:
            steps_text = fields["steps"]["text"]
            if not rec.get("steps_en"):
                rec["steps_en"] = [steps_text]
                rec["steps_en_source"] = fields["steps"]["evidence_url"]
                if "_t46_merge" not in rec:
                    rec["_t46_merge"] = "t46a"
                en_filled += 1
            else:
                collisions += 1

    print(f"\nReverted: symptoms={reverted_symptoms}, causes={reverted_causes}, steps={reverted_steps}")
    print(f"EN fields filled: {en_filled}")
    print(f"Collisions (preserved): {collisions}")
    print(f"Markers added: {markers_added}")

    save_json(DTC_DB_PATH, dtc_db)
    print(f"\nSaved DTC DB: {len(dtc_db)} codes")

if __name__ == "__main__":
    main()

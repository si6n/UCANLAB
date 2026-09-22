#!/usr/bin/env python3
"""Check the 166 fillable DTC records more carefully."""
import json
import re

REPO = r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool"
SCAN_PATH = REPO + r"\output\scan_t66_r2_wholefleet.ca.json"
DTC_DB_PATH = REPO + r"\data\diagnostics\dtc_database.json"

def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)

def main():
    scan = load_json(SCAN_PATH)
    records = scan.get("records", [])
    dtc_db = load_json(DTC_DB_PATH)

    pbcu_re = re.compile(r'^[PBCU]\d{4}$')

    # Group scan records by code (take first occurrence)
    by_code = {}
    for r in records:
        code = r.get("code", "").strip()
        if pbcu_re.match(code) and code not in by_code:
            by_code[code] = r

    # Check each PBCU code in DB
    truly_empty = 0
    has_desc = 0
    samples = []

    for code, r in by_code.items():
        if code not in dtc_db:
            continue
        entry = dtc_db[code]
        desc_en = entry.get("description_en", "")

        if not desc_en or desc_en.strip() == "":
            truly_empty += 1
            if len(samples) < 20:
                samples.append((code, r.get("description", ""), entry.get("title", "")[:50]))
        else:
            has_desc += 1

    print(f"Total PBCU codes in DB: {len([c for c in by_code if c in dtc_db])}")
    print(f"Has description_en: {has_desc}")
    print(f"Truly empty description_en: {truly_empty}")

    print("\nSamples of truly empty (code, scan_desc, db_title):")
    for code, desc, title in samples:
        print(f"  {code}: desc='{desc[:60]}' title='{title}'")

if __name__ == "__main__":
    main()

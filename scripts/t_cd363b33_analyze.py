#!/usr/bin/env python3
"""Analyze the wholefleet.ca scan output to plan the merge."""
import json
import re
from collections import Counter

REPO = r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool"
SCAN_PATH = REPO + r"\output\scan_t66_r2_wholefleet.ca.json"
SPN_DB_PATH = REPO + r"\data\diagnostics\j1939_spn_fmi_database.json"
DTC_DB_PATH = REPO + r"\data\diagnostics\dtc_database.json"

def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)

def main():
    scan = load_json(SCAN_PATH)
    records = scan.get("records", [])
    print("=== SCAN META ===")
    meta = scan.get("_meta", {})
    for k, v in meta.items():
        print(f"  {k}: {v}")
    print(f"\nTotal records: {len(records)}")

    # Analyze code shapes
    code_shapes = Counter()
    brands = Counter()
    fields_populated = Counter()
    for r in records:
        code = r.get("code", "").strip()
        # Classify code shape
        if re.match(r'^\d+-\d+$', code):  # 01-01, 02-01
            code_shapes['dash_format'] += 1
        elif re.match(r'^P\d{4}$', code):
            code_shapes['P_code'] += 1
        elif re.match(r'^[BCU]\d{4}$', code):
            code_shapes['BCU_code'] += 1
        elif re.match(r'^\d+$', code):
            code_shapes['bare_numeric'] += 1
        elif re.match(r'^\d+-\d+-\d+$', code):
            code_shapes['triple_dash'] += 1
        elif re.match(r'^[A-Z]\d{2,3}', code):
            code_shapes['alpha_numeric'] += 1
        else:
            code_shapes['other'] += 1

        brands[r.get("brand", "?")] += 1

        # Count which fields have data
        for f in ["title", "description", "severity", "causes", "steps", "symptoms"]:
            val = r.get(f)
            if val and val != "" and val != []:
                fields_populated[f] += 1

    print("\n=== CODE SHAPES ===")
    for shape, cnt in code_shapes.most_common():
        print(f"  {shape}: {cnt}")

    print("\n=== BRANDS ===")
    for brand, cnt in brands.most_common():
        print(f"  {brand}: {cnt}")

    print("\n=== FIELDS POPULATED (non-empty) ===")
    for f, cnt in fields_populated.most_common():
        print(f"  {f}: {cnt} / {len(records)}")

    # Check for SPN-FMI format codes
    spn_fmi_re = re.compile(r'^(\d+)-(\d+)$')
    spn_fmi_codes = []
    for r in records:
        code = r.get("code", "").strip()
        m = spn_fmi_re.match(code)
        if m:
            spn = int(m.group(1))
            fmi = int(m.group(2))
            if spn > 0 and spn < 600000:
                spn_fmi_codes.append((spn, fmi, r))

    print("\n=== SPN-FMI candidates (dash format with numeric spn<600000) ===")
    print(f"  Count: {len(spn_fmi_codes)}")
    if spn_fmi_codes:
        # Check against SPN DB
        spn_db = load_json(SPN_DB_PATH)
        spns = spn_db.get("spns", {})
        print(f"  SPN DB has {len(spns)} SPNs")
        in_db = 0
        not_in_db = 0
        for spn, _fmi, _r in spn_fmi_codes:
            spn_key = f"SPN_{spn}"
            if spn_key in spns:
                in_db += 1
            else:
                not_in_db += 1
        print(f"  In SPN DB: {in_db}")
        print(f"  NOT in SPN DB: {not_in_db}")

        # Show sample of those not in DB
        not_in_samples = [(s, f, r) for s, f, r in spn_fmi_codes if f"SPN_{s}" not in spns]
        print("\n  Sample NOT in DB (first 10):")
        for spn, fmi, r in not_in_samples[:10]:
            print(f"    SPN_{spn} FMI {fmi}: {r.get('title', '')[:80]}")

    # Check PBCU codes against DTC DB
    pbcu_re = re.compile(r'^[PBCU]\d{4}$')
    pbcu_codes = []
    for r in records:
        code = r.get("code", "").strip()
        if pbcu_re.match(code):
            pbcu_codes.append(r)

    print("\n=== PBCU codes ===")
    print(f"  Count: {len(pbcu_codes)}")
    if pbcu_codes:
        dtc_db = load_json(DTC_DB_PATH)
        print(f"  DTC DB has {len(dtc_db)} codes")
        # DTC DB keys
        in_dtc = 0
        not_in_dtc = 0
        for r in pbcu_codes:
            code = r.get("code", "").strip()
            if code in dtc_db:
                in_dtc += 1
            else:
                not_in_dtc += 1
        print(f"  In DTC DB: {in_dtc}")
        print(f"  NOT in DTC DB: {not_in_dtc}")

        # Check how many have description only vs richer data
        desc_only = 0
        with_causes = 0
        with_steps = 0
        with_symptoms = 0
        for r in pbcu_codes:
            if r.get("causes"):
                with_causes += 1
            if r.get("steps"):
                with_steps += 1
            if r.get("symptoms"):
                with_symptoms += 1
            if r.get("description") and not r.get("causes") and not r.get("steps") and not r.get("symptoms"):
                desc_only += 1
        print(f"  Description only: {desc_only}")
        print(f"  With causes: {with_causes}")
        print(f"  With steps: {with_steps}")
        print(f"  With symptoms: {with_symptoms}")

        # Sample of not-in-DTC codes
        not_in_dtc_samples = [r for r in pbcu_codes if r.get("code", "").strip() not in dtc_db]
        print("\n  Sample NOT in DTC DB (first 15):")
        for r in not_in_dtc_samples[:15]:
            print(f"    {r.get('code')}: {r.get('title', '')[:80]}")

    # Look for records with causes/steps/symptoms (rich data)
    rich = [r for r in records if r.get("causes") or r.get("steps") or r.get("symptoms")]
    print("\n=== RICH records (with causes/steps/symptoms) ===")
    print(f"  Count: {len(rich)}")
    for r in rich[:5]:
        print(f"\n  Code: {r.get('code')}, Brand: {r.get('brand')}")
        print(f"  Title: {r.get('title', '')[:100]}")
        print(f"  Causes: {r.get('causes', [])[:3]}")
        print(f"  Steps: {r.get('steps', [])[:3]}")
        print(f"  Symptoms: {r.get('symptoms', [])[:3]}")

    # Unique descriptions
    descs = [r.get("description", "") for r in records if r.get("description")]
    print("\n=== DESCRIPTIONS ===")
    print(f"  Total: {len(descs)}")
    print(f"  Unique: {len(set(descs))}")
    most_common = Counter(descs).most_common(5)
    print("  Most common:")
    for d, cnt in most_common:
        print(f"    {cnt}x: {d[:80]}")

if __name__ == "__main__":
    main()

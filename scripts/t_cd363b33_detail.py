#!/usr/bin/env python3
"""Detailed analysis of wholefleet.ca scan - what can actually be merged."""
import json
import re

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
    spn_db = load_json(SPN_DB_PATH)
    spns = spn_db.get("spns", {})
    dtc_db = load_json(DTC_DB_PATH)

    print("=== DASH-FORMAT CODES DETAIL ===")
    spn_fmi_re = re.compile(r'^(\d+)-(\d+)$')

    matched_in_db = []
    not_in_db = []

    for r in records:
        code = r.get("code", "").strip()
        m = spn_fmi_re.match(code)
        if not m:
            continue
        spn = int(m.group(1))
        fmi = int(m.group(2))
        spn_key = f"SPN_{spn}"

        if spn_key in spns:
            matched_in_db.append((spn, fmi, r))
        else:
            not_in_db.append((spn, fmi, r))

    print(f"Matched in SPN DB: {len(matched_in_db)}")
    print(f"NOT in SPN DB: {len(not_in_db)}")

    # For matched: check what fields could be filled
    fillable = 0
    new_fmi = 0
    collision = 0
    for spn, fmi, r in matched_in_db:
        spn_key = f"SPN_{spn}"
        entry = spns[spn_key]
        fm = entry.get("fault_matrix", {})

        # Does this FMI exist in fault_matrix?
        fmi_str = str(fmi)
        if fmi_str not in fm:
            new_fmi += 1
            # Check if the record has causes/steps to fill
            if r.get("causes") or r.get("steps") or r.get("symptoms"):
                fillable += 1
        else:
            # FMI exists - check if we can fill causes/steps
            fmi_entry = fm[fmi_str]
            has_causes = bool(fmi_entry.get("causes"))
            has_steps = bool(fmi_entry.get("steps")) or bool(entry.get("steps"))
            if (r.get("causes") and not has_causes) or (r.get("steps") and not has_steps):
                fillable += 1
            else:
                collision += 1

    print(f"  New FMI for existing SPN: {new_fmi}")
    print(f"  Fillable (causes/steps empty in DB): {fillable}")
    print(f"  Collisions (already have data): {collision}")

    # Show what new FMI entries look like
    print("\n  New FMI entries (first 15):")
    for spn, fmi, r in matched_in_db:
        spn_key = f"SPN_{spn}"
        entry = spns[spn_key]
        fm = entry.get("fault_matrix", {})
        fmi_str = str(fmi)
        if fmi_str not in fm:
            has_data = bool(r.get("causes") or r.get("steps") or r.get("symptoms"))
            print(f"    SPN_{spn} FMI {fmi}: {r.get('title','')[:60]} | data={'Y' if has_data else 'N'}")

    # Show what fillable records look like
    print("\n  Fillable records detail (first 10):")
    count = 0
    for spn, fmi, r in matched_in_db:
        spn_key = f"SPN_{spn}"
        entry = spns[spn_key]
        fm = entry.get("fault_matrix", {})
        fmi_str = str(fmi)
        if fmi_str in fm:
            fmi_entry = fm[fmi_str]
            has_causes_db = bool(fmi_entry.get("causes"))
            has_steps_db = bool(entry.get("steps"))
            if (r.get("causes") and not has_causes_db) or (r.get("steps") and not has_steps_db):
                print(f"    SPN_{spn} FMI {fmi}: title={r.get('title','')[:50]}")
                print(f"      DB causes: {'YES' if has_causes_db else 'NO'} | scan causes: {r.get('causes',[])[:2]}")
                print(f"      DB steps: {'YES' if has_steps_db else 'NO'} | scan steps: {r.get('steps',[])[:2]}")
                count += 1
                if count >= 10:
                    break

    # NOT in DB - these are low SPN numbers (Toyota forklift)
    print("\n  NOT in SPN DB (first 20) — low SPN = Toyota OEM, not J1939:")
    for spn, fmi, r in not_in_db[:20]:
        print(f"    SPN_{spn} FMI {fmi}: {r.get('title','')[:60]} | brand={r.get('brand','')}")

    # Check if any have SPN > 90 (real J1939 range)
    real_j1939 = [(s,f,r) for s,f,r in not_in_db if s >= 90]
    print(f"\n  NOT in DB but SPN>=90 (possible real J1939): {len(real_j1939)}")
    for spn, fmi, r in real_j1939:
        print(f"    SPN_{spn} FMI {fmi}: {r.get('title','')[:60]} | brand={r.get('brand','')}")

    # === PBCU codes ===
    print("\n\n=== PBCU CODES DETAIL ===")
    pbcu_re = re.compile(r'^[PBCU]\d{4}$')
    pbcu_in_db = []
    pbcu_new = []

    for r in records:
        code = r.get("code", "").strip()
        if not pbcu_re.match(code):
            continue
        if code in dtc_db:
            pbcu_in_db.append(r)
        else:
            pbcu_new.append(r)

    print(f"In DTC DB: {len(pbcu_in_db)}")
    print(f"NOT in DTC DB: {len(pbcu_new)}")

    # For in-DB: check what fields can be filled
    dtc_fillable = 0
    dtc_collision = 0
    for r in pbcu_in_db:
        code = r.get("code", "").strip()
        entry = dtc_db[code]

        # Check if description_en is empty
        desc_en = entry.get("description_en") or entry.get("description") or entry.get("title")
        if not desc_en and r.get("description"):
            dtc_fillable += 1
        elif r.get("causes") and not entry.get("causes"):
            dtc_fillable += 1
        elif r.get("steps") and not entry.get("steps"):
            dtc_fillable += 1
        else:
            dtc_collision += 1

    print(f"  Fillable: {dtc_fillable}")
    print(f"  Collision: {dtc_collision}")

    # Check actual DTC DB schema for a sample PBCU code
    sample_pbcu = pbcu_in_db[0] if pbcu_in_db else None
    if sample_pbcu:
        code = sample_pbcu["code"]
        entry = dtc_db[code]
        print(f"\n  Sample DTC entry for {code}:")
        print(f"    Keys: {list(entry.keys())[:15]}")
        for k in list(entry.keys())[:8]:
            v = entry[k]
            if isinstance(v, str):
                print(f"    {k}: {v[:80]}")
            elif isinstance(v, list):
                print(f"    {k}: [{len(v)} items]")
            elif isinstance(v, dict):
                print(f"    {k}: dict({len(v)} keys)")
            else:
                print(f"    {k}: {v}")

    # New PBCU codes
    print("\n  NEW PBCU codes not in DTC DB:")
    for r in pbcu_new:
        print(f"    {r.get('code')}: {r.get('title','')[:70]} | brand={r.get('brand','')}")

if __name__ == "__main__":
    main()

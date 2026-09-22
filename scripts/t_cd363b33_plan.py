#!/usr/bin/env python3
"""Final merge plan: identify exactly what to merge."""
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

    spn_fmi_re = re.compile(r'^(\d+)-(\d+)$')

    # === CATEGORY 1: Existing SPN, fillable causes/steps ===
    cat1_fillable = []
    # === CATEGORY 2: New FMI for existing SPN with data (SPN>=90) ===
    cat2_new_fmi = []
    # === CATEGORY 3: New SPN not in DB (SPN>=90, real J1939) ===
    cat3_new_spn = []
    # === CATEGORY 4: Low SPN numbers that are OEM-specific (skip) ===
    cat4_skip = []

    for r in records:
        code = r.get("code", "").strip()
        m = spn_fmi_re.match(code)
        if not m:
            continue
        spn = int(m.group(1))
        fmi = int(m.group(2))
        spn_key = f"SPN_{spn}"
        fmi_str = str(fmi)

        has_data = bool(r.get("causes") or r.get("steps") or r.get("symptoms"))

        if spn_key in spns:
            entry = spns[spn_key]
            fm = entry.get("fault_matrix", {})

            if fmi_str in fm:
                # FMI exists - can we fill causes/steps?
                fmi_entry = fm[fmi_str]
                db_has_causes = bool(fmi_entry.get("causes"))
                db_has_steps = bool(entry.get("steps"))
                can_fill = (r.get("causes") and not db_has_causes) or (r.get("steps") and not db_has_steps)
                if can_fill:
                    cat1_fillable.append((spn, fmi, r, entry, fmi_entry))
            else:
                # New FMI for existing SPN
                if has_data and spn >= 90:
                    cat2_new_fmi.append((spn, fmi, r, entry))
                elif spn < 90:
                    # Low SPN = OEM mismatch, skip
                    cat4_skip.append((spn, fmi, r))
                else:
                    # No data, skip
                    cat4_skip.append((spn, fmi, r))
        else:
            # New SPN
            if spn >= 90:
                cat3_new_spn.append((spn, fmi, r))
            else:
                cat4_skip.append((spn, fmi, r))

    print("=== MERGE PLAN ===")
    print(f"Cat 1 - Fill causes/steps for existing SPN/FMI: {len(cat1_fillable)}")
    for spn, fmi, r, _entry, _fmi_entry in cat1_fillable:
        print(f"  SPN_{spn} FMI {fmi}: {r.get('title','')[:50]}")
        print(f"    scan causes: {r.get('causes',[])[:3]}")
        print(f"    scan steps: {r.get('steps',[])[:3]}")

    print(f"\nCat 2 - New FMI for existing SPN (with data, SPN>=90): {len(cat2_new_fmi)}")
    for spn, fmi, r, entry in cat2_new_fmi:
        print(f"  SPN_{spn} FMI {fmi}: {r.get('title','')[:60]}")
        print(f"    DB name: {entry.get('name','')[:50]}")
        print(f"    scan causes: {r.get('causes',[])[:2]}")
        print(f"    scan steps: {r.get('steps',[])[:2]}")

    print(f"\nCat 3 - New SPN (not in DB, SPN>=90): {len(cat3_new_spn)}")
    for spn, fmi, r in cat3_new_spn:
        print(f"  SPN_{spn} FMI {fmi}: {r.get('title','')[:60]} | brand={r.get('brand','')}")
        print(f"    causes: {r.get('causes',[])[:2]}")
        print(f"    steps: {r.get('steps',[])[:2]}")

    print(f"\nCat 4 - Skip (OEM mismatch or no data): {len(cat4_skip)}")

    # === PBCU DTC codes ===
    pbcu_re = re.compile(r'^[PBCU]\d{4}$')
    pbcu_new = []
    pbcu_fillable = []
    for r in records:
        code = r.get("code", "").strip()
        if not pbcu_re.match(code):
            continue
        if code in dtc_db:
            entry = dtc_db[code]
            # Check if description_en is empty
            desc_en = entry.get("description_en", "")
            if not desc_en and r.get("description"):
                pbcu_fillable.append((code, r, entry))
        else:
            pbcu_new.append((code, r))

    print("\n=== PBCU DTC ===")
    print(f"  New codes: {len(pbcu_new)}")
    for code, r in pbcu_new:
        print(f"    {code}: {r.get('title','')[:70]} | brand={r.get('brand','')}")

    print(f"  Fillable (description_en empty): {len(pbcu_fillable)}")
    for code, r, _entry in pbcu_fillable[:10]:
        print(f"    {code}: scan desc={r.get('description','')[:50]}")

    # === Bare numeric codes (OEM specific - Bobcat, Peterbilt, etc.) ===
    bare_re = re.compile(r'^\d+$')
    bare_codes = []
    for r in records:
        code = r.get("code", "").strip()
        if bare_re.match(code) and not pbcu_re.match(code):
            bare_codes.append(r)

    print("\n=== Bare numeric OEM codes ===")
    print(f"  Count: {len(bare_codes)}")
    has_data = [r for r in bare_codes if r.get("causes") or r.get("steps")]
    print(f"  With data (causes/steps): {len(has_data)}")
    # These don't fit J1939 SPN schema (no SPN number) - skip
    print("  -> SKIP (don't fit J1939 or DTC schema)")

    # Summary
    print("\n\n=== FINAL MERGE SUMMARY ===")
    print("J1939 SPN DB:")
    print(f"  New SPNs: {len(cat3_new_spn)}")
    print(f"  New FMIs for existing SPNs: {len(cat2_new_fmi)}")
    print(f"  Fields filled (causes/steps): {len(cat1_fillable)}")
    print(f"  Skipped (OEM mismatch): {len(cat4_skip)}")
    print("DTC DB:")
    print(f"  New codes: {len(pbcu_new)}")
    print(f"  Fields filled: {len(pbcu_fillable)}")

if __name__ == "__main__":
    main()

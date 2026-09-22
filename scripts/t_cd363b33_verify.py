#!/usr/bin/env python3
"""Verify merge results - check title_tr and new SPN entries."""
import json

REPO = r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool"
SPN_DB_PATH = REPO + r"\data\diagnostics\j1939_spn_fmi_database.json"
DTC_DB_PATH = REPO + r"\data\diagnostics\dtc_database.json"

def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)

def main():
    spn_db = load_json(SPN_DB_PATH)
    spns = spn_db.get("spns", {})

    # Check the new SPNs
    new_spn_keys = ["SPN_2458", "SPN_3252", "SPN_3714", "SPN_522614", "SPN_524223", "SPN_524225", "SPN_524238", "SPN_524261", "SPN_524263"]

    print("=== NEW SPN ENTRIES ===")
    for key in new_spn_keys:
        if key in spns:
            entry = spns[key]
            print(f"\n{key}:")
            print(f"  spn: {entry.get('spn')}")
            print(f"  name: {entry.get('name', '')[:70]}")
            print(f"  title_tr: {entry.get('title_tr', '')[:70]}")
            print(f"  subsystem: {entry.get('subsystem', '')[:50]}")
            print(f"  source: {entry.get('source', '')[:60]}")
            fm = entry.get("fault_matrix", {})
            for fmi_str, fmi_entry in fm.items():
                print(f"  FMI {fmi_str}: {fmi_entry.get('fault_title','')[:60]}")
                if fmi_entry.get("causes"):
                    print(f"    causes: {fmi_entry['causes'][:80]}")
                if fmi_entry.get("steps"):
                    print(f"    steps: {fmi_entry['steps'][:80]}")

            # Check title_tr is not empty
            if not entry.get("title_tr"):
                print("  *** WARNING: title_tr is EMPTY!")
        else:
            print(f"\n{key}: NOT FOUND!")

    # Count total SPNs without title_tr
    no_title_tr = 0
    for _key, entry in spns.items():
        if not entry.get("title_tr"):
            no_title_tr += 1
    print("\n\n=== TITLE_TR CHECK ===")
    print(f"Total SPNs: {len(spns)}")
    print(f"SPNs with empty title_tr: {no_title_tr}")

    # Check the 9 new FMI entries for existing SPNs
    print("\n=== NEW FMI ENTRIES (existing SPNs) ===")
    # These should be the ones from Cat 2 analysis
    cat2_spns = ["SPN_1679", "SPN_2000", "SPN_2348", "SPN_342", "SPN_3720", "SPN_5298", "SPN_41", "SPN_110", "SPN_168"]
    for key in cat2_spns:
        if key in spns:
            entry = spns[key]
            fm = entry.get("fault_matrix", {})
            # Check for FMI entries with wholefleet source
            for fmi_str, fmi_entry in fm.items():
                if "wholefleet" in str(fmi_entry.get("source", "")):
                    print(f"  {key} FMI {fmi_str}: {fmi_entry.get('fault_title','')[:60]}")
                    print(f"    causes: {fmi_entry.get('causes','')[:60]}")

    # Check DTC new codes
    dtc_db = load_json(DTC_DB_PATH)
    new_dtc_codes = ["C3014", "C3016", "C3017", "C3018", "C3113", "C3121", "C3122", "C3123", "C3801", "C3803", "C3804", "C3805", "P3611", "U1661", "U1923"]

    print("\n=== NEW DTC CODES ===")
    for code in new_dtc_codes:
        if code in dtc_db:
            entry = dtc_db[code]
            print(f"  {code}: title={entry.get('title','')[:50]}")
            print(f"    desc_en={entry.get('description_en','')[:50]}")
            print(f"    subsystem={entry.get('subsystem','')[:40]}")
        else:
            print(f"  {code}: NOT FOUND!")

    # Verify format: indent=1
    with open(SPN_DB_PATH, "r", encoding="utf-8") as f:
        first_lines = [next(f) for _ in range(5)]
    print("\n=== FORMAT CHECK (first 5 lines of SPN DB) ===")
    for i, line in enumerate(first_lines):
        print(f"  {i+1}: {line.rstrip()}")

if __name__ == "__main__":
    main()

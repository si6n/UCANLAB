#!/usr/bin/env python3
"""Fix T_cd363b33 merge issues:
1. description_en_source should be URL (https://www.wholefleet.ca/...) not source tag
2. causes_en_source should be URL
3. severity UNRESOLVED -> UNKNOWN (which maps to enum)
4. FMI entries with non-enum severity -> fix
"""
import json

REPO = r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool"
SPN_DB_PATH = REPO + r"\data\diagnostics\j1939_spn_fmi_database.json"
DTC_DB_PATH = REPO + r"\data\diagnostics\dtc_database.json"
SCAN_PATH = REPO + r"\output\scan_t66_r2_wholefleet.ca.json"

def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")

def main():
    print("Fixing merge issues...")

    # Load scan to get evidence URLs by code
    scan = load_json(SCAN_PATH)
    records = scan.get("records", [])
    code_to_url = {}
    for r in records:
        code = r.get("code", "").strip()
        if code and code not in code_to_url:
            code_to_url[code] = r.get("url", "")

    # === Fix DTC DB: description_en_source should be URL ===
    dtc_db = load_json(DTC_DB_PATH)
    fixed_sources = 0
    for code, entry in dtc_db.items():
        # Check all *_source fields for non-URL values
        for key in list(entry.keys()):
            if key.endswith("_source") and isinstance(entry[key], str):
                val = entry[key]
                if val and not val.startswith("http"):
                    # It's a source tag, not URL - replace with evidence URL
                    if code in code_to_url:
                        entry[key] = code_to_url[code]
                        fixed_sources += 1
                    elif "wholefleet" in val:
                        entry[key] = "https://www.wholefleet.ca/news/"
                        fixed_sources += 1
    print(f"  DTC: fixed {fixed_sources} source fields to URLs")
    save_json(DTC_DB_PATH, dtc_db)

    # === Fix J1939 SPN DB: severity issues ===
    spn_db = load_json(SPN_DB_PATH)
    spns = spn_db.get("spns", {})
    fixed_severity = 0
    fixed_spn_sources = 0

    for _spn_key, entry in spns.items():
        # Check fault_matrix severities
        fm = entry.get("fault_matrix", {})
        for fmi_str, fmi_entry in fm.items():
            sev = fmi_entry.get("severity", "")
            if sev == "UNRESOLVED":
                fmi_entry["severity"] = "UNKNOWN"
                fixed_severity += 1

            # Fix source fields that aren't URLs
            for key in list(fmi_entry.keys()):
                if key.endswith("_source") and isinstance(fmi_entry[key], str):
                    val = fmi_entry[key]
                    if val and not val.startswith("http"):
                        spn_num = entry.get("spn", "")
                        code = f"{spn_num}-{fmi_str}"
                        if code in code_to_url:
                            fmi_entry[key] = code_to_url[code]
                            fixed_spn_sources += 1
                        elif "wholefleet" in val:
                            fmi_entry[key] = "https://www.wholefleet.ca/news/"
                            fixed_spn_sources += 1

        # Fix top-level source fields
        for key in list(entry.keys()):
            if key.endswith("_source") and isinstance(entry[key], str):
                val = entry[key]
                if val and not val.startswith("http"):
                    spn_num = entry.get("spn", "")
                    # Try to find a URL for this SPN
                    found = False
                    for code, url in code_to_url.items():
                        if code.startswith(str(spn_num) + "-"):
                            entry[key] = url
                            fixed_spn_sources += 1
                            found = True
                            break
                    if not found and "wholefleet" in val:
                        entry[key] = "https://www.wholefleet.ca/news/"
                        fixed_spn_sources += 1

    print(f"  J1939: fixed {fixed_severity} UNRESOLVED severities -> UNKNOWN")
    print(f"  J1939: fixed {fixed_spn_sources} source fields to URLs")
    save_json(SPN_DB_PATH, spn_db)

    # Verify
    print("\nVerification:")
    dtc_db2 = load_json(DTC_DB_PATH)
    bad = 0
    for code, entry in dtc_db2.items():
        for key in entry:
            if key.endswith("_source") and isinstance(entry[key], str):
                val = entry[key]
                if val and not val.startswith("http"):
                    bad += 1
                    if bad <= 5:
                        print(f"  BAD DTC source: {code}.{key} = {val[:50]}")
    print(f"  DTC bad source fields: {bad}")

    spn_db2 = load_json(SPN_DB_PATH)
    spns2 = spn_db2.get("spns", {})
    bad_sev = 0
    bad_src = 0
    for spn_key, entry in spns2.items():
        fm = entry.get("fault_matrix", {})
        for fmi_str, fmi_entry in fm.items():
            sev = fmi_entry.get("severity", "")
            valid_sevs = {"CRITICAL_STOP", "HIGH", "MEDIUM", "LOW", "UNKNOWN", "INFO", "WARNING", "ERROR", "CRITICAL"}
            if sev not in valid_sevs:
                bad_sev += 1
                if bad_sev <= 5:
                    print(f"  BAD severity: {spn_key} FMI {fmi_str} = {sev}")
            for key in fmi_entry:
                if key.endswith("_source") and isinstance(fmi_entry[key], str):
                    val = fmi_entry[key]
                    if val and not val.startswith("http"):
                        bad_src += 1
                        if bad_src <= 5:
                            print(f"  BAD SPN source: {spn_key} FMI {fmi_str} .{key} = {val[:50]}")
    print(f"  J1939 bad severity: {bad_sev}")
    print(f"  J1939 bad source fields: {bad_src}")

if __name__ == "__main__":
    main()

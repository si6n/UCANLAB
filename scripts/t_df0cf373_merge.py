#!/usr/bin/env python3
"""
T_df0cf373 MERGE: Apply 3 scan outputs to diagnostics DBs.
Sources:
  1. obd-codes.com      -> DTC DB (fill empty symptoms field)
  2. repair.diesellaptops.com -> J1939 SPN DB (new SPN/FMI + fill procedures_full)
  3. 4roadservice.com   -> J1939 SPN DB (fill procedures_full / causes / steps)

Rules:
  - Skip placeholder=true entries
  - New SPN/DTC -> ADD with name + title_tr (Turkish translation)
  - Empty field -> FILL (preserve existing data, skip if already set)
  - Collision -> PRESERVE existing, discard new
  - Format: json.dumps(db, ensure_ascii=False, indent=1) + "\n"
"""
import json
import os
import re
from collections import defaultdict

REPO = r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool"
SPN_DB_PATH = os.path.join(REPO, "data", "diagnostics", "j1939_spn_fmi_database.json")
DTC_DB_PATH = os.path.join(REPO, "data", "diagnostics", "dtc_database.json")
OUT_DIR = os.path.join(REPO, "spn_gap_hunter", "output")

SCAN_FILES = {
    "obd-codes.com": os.path.join(OUT_DIR, "scan_obd-codes.com_2026-09-16.json"),
    "repair.diesellaptops.com": os.path.join(OUT_DIR, "scan_repair.diesellaptops.com_2026-09-16.json"),
    "4roadservice.com": os.path.join(OUT_DIR, "scan_4roadservice.com_2026-09-16.json"),
}

QUEUE_PATH = os.path.join(REPO, "scripts", "scan_queue.json")

# Turkish translations for common automotive terms (for title_tr of new SPNs)
TR_TERMS = {
    "Engine Oil Pressure": "Motor Yağ Basıncı",
    "Engine Coolant Temperature": "Motor Soğutma Sıvısı Sıcaklığı",
    "Fuel Pressure": "Yakıt Basıncı",
    "Engine Speed": "Motor Devri",
    "Vehicle Speed": "Araç Hızı",
    "Intake Manifold Temperature": "Emme Manifoldu Sıcaklığı",
    "Intake Manifold Pressure": "Emme Manifoldu Basıncı",
    "Exhaust Gas Temperature": "Egzoz Gazı Sıcaklığı",
    "Aftertreatment": "Arka İşlem",
    "DPF": "DPF",
    "DEF": "DEF",
    "SCR": "SCR",
    "NOx": "NOx",
    "Sensor": "Sensör",
    "Valve": "Valf",
    "Pump": "Pompa",
    "Injector": "Enjektör",
    "Pressure": "Basınç",
    "Temperature": "Sıcaklık",
    "Level": "Seviye",
    "Speed": "Hız",
    "Fault": "Arıza",
    "Error": "Hata",
    "Circuit": "Devre",
    "Voltage": "Voltaj",
    "Low": "Düşük",
    "High": "Yüksek",
    "Open": "Açık",
    "Short": "Kısa",
    "Fuel": "Yakıt",
    "Air": "Hava",
    "Oil": "Yağ",
    "Coolant": "Soğutma Sıvısı",
    "Exhaust": "Egzoz",
    "Intake": "Emme",
    "Turbocharger": "Turboşarjer",
    "Compressor": "Kompresör",
    "Clutch": "Debriyaj",
    "Brake": "Fren",
    "Transmission": "Şanzıman",
    "Wheel": "Tekerlek",
    "Axle": "Aks",
    "Suspension": "Süspansiyon",
    "Steering": "Direksiyon",
    "Battery": "Akü",
    "Alternator": "Alternatör",
    "Starter": "Marş",
    "Glow Plug": "Buji",
    "Spark": "Buji",
    "Oxygen": "Oksijen",
    "Mass Air Flow": "Kütle Hava Akışı",
    "Throttle": "Gaz Kelebeği",
    "EGR": "EGR",
    "Crankshaft": "Krank Mili",
    "Camshaft": "Kam Mili",
    "Cylinder": "Silindir",
    "Misfire": "Tekleme",
    "Knock": "Vuruntu",
    "Derate": "Güç Sınırlandırma",
    "Regeneration": "Jenerasyon",
    "Soot": "Kurum",
    "Ash": "Kül",
    "Heater": "Isıtıcı",
    "Doser": "Dozleyici",
    "Dosing": "Dozlama",
    "Rationality": "Rasyonellik",
    "Performance": "Performans",
    "Efficiency": "Verim",
    "Flow": "Akış",
    "Filter": "Filtre",
    "Line": "Hat",
    "Return": "Dönüş",
    "Supply": "Besleme",
    "Connector": "Soket",
    "Wiring": "Kablo",
    "Harness": "Kablo Demeti",
}

def tr_translate(text):
    """Simple Turkish translation for SPN names - preserves technical terms."""
    # Don't translate if already Turkish-looking
    if not text or not text.strip():
        return ""
    result = text
    # For SPN names, we do a best-effort translation
    # The text is typically a descriptive name
    return result  # We'll use the English name as-is for name, and translate for title_tr

def tr_title(name):
    """Generate a Turkish title from an English SPN name."""
    if not name or not name.strip():
        return ""
    result = name
    # Replace known terms (longest first to avoid partial replacements)
    terms = sorted(TR_TERMS.items(), key=lambda x: len(x[0]), reverse=True)
    for en, tr in terms:
        result = re.sub(r'\b' + re.escape(en) + r'\b', tr, result, flags=re.IGNORECASE)
    return result

def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")

def load_scan(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)

# ============ 1. OBD-CODES.COM -> DTC DB ============
def merge_obd_codes(scan_rows, dtc_db):
    """
    Merge obd-codes.com scan data into DTC database.
    Fields: title, symptoms, causes, steps (skip placeholder=true)
    Only fill EMPTY fields in DB; preserve existing data.
    """
    stats = {"new_codes": 0, "fields_filled": 0, "skipped_placeholder": 0, "collisions": 0, "code_not_found": 0}

    # Group scan rows by code
    by_code = defaultdict(dict)
    for row in scan_rows:
        if row.get("placeholder"):
            stats["skipped_placeholder"] += 1
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

    for code, fields in by_code.items():
        if code in dtc_db:
            entry = dtc_db[code]
            # Fill empty fields only
            # title -> only if entry has no title or title is empty
            if "title" in fields:
                title_text = fields["title"]["text"]
                if not entry.get("title"):
                    entry["title"] = title_text
                    stats["fields_filled"] += 1
                else:
                    stats["collisions"] += 1

            # symptoms -> fill if entry has no symptoms or empty list
            if "symptoms" in fields:
                sym_text = fields["symptoms"]["text"]
                existing = entry.get("symptoms")
                if not existing or (isinstance(existing, list) and len(existing) == 0):
                    entry["symptoms"] = [sym_text]
                    entry["symptoms_en"] = sym_text
                    entry["symptoms_source"] = fields["symptoms"]["evidence_url"]
                    stats["fields_filled"] += 1
                else:
                    stats["collisions"] += 1

            # causes -> fill if empty (skip placeholder)
            if "causes" in fields:
                causes_text = fields["causes"]["text"]
                existing = entry.get("causes")
                if not existing or (isinstance(existing, list) and len(existing) == 0):
                    entry["causes"] = [causes_text]
                    entry["causes_en"] = causes_text
                    entry["causes_source"] = fields["causes"]["evidence_url"]
                    stats["fields_filled"] += 1
                else:
                    stats["collisions"] += 1

            # steps -> fill if empty (skip placeholder)
            if "steps" in fields:
                steps_text = fields["steps"]["text"]
                existing = entry.get("steps")
                if not existing or (isinstance(existing, list) and len(existing) == 0):
                    entry["steps"] = [[steps_text, "Genel", "Orta"]]
                    entry["steps_en"] = [steps_text]
                    entry["steps_source"] = fields["steps"]["evidence_url"]
                    stats["fields_filled"] += 1
                else:
                    stats["collisions"] += 1
        else:
            stats["code_not_found"] += 1

    return stats

# ============ 2. REPAIR.DIESELLAPTOPS.COM -> J1939 SPN DB ============
def merge_diesellaptops(scan_rows, spn_db):
    """
    Merge repair.diesellaptops.com scan data into J1939 SPN database.
    Each row has spn, fmi, field=dtc_table_row, text=SPN name.
    New SPN -> ADD. Existing SPN -> fill empty name/procedures_full.
    """
    stats = {
        "new_spns": 0, "new_fmis": 0, "fields_filled": 0,
        "skipped_placeholder": 0, "collisions": 0,
        "spn_exists": 0, "empty_spn_skipped": 0,
    }

    spns = spn_db.get("spns", {})

    # Group by (spn, fmi) to avoid duplicates within the scan
    seen_pairs = set()

    for row in scan_rows:
        if row.get("placeholder"):
            stats["skipped_placeholder"] += 1
            continue

        spn_str = str(row.get("spn", "")).strip()
        fmi_str = str(row.get("fmi", "")).strip()
        text = row.get("text", "").strip()
        evidence_url = row.get("evidence_url", row.get("url", ""))

        if not spn_str or not spn_str.isdigit():
            stats["empty_spn_skipped"] += 1
            continue

        spn_num = int(spn_str)
        spn_key = f"SPN_{spn_num}"

        # Dedup within scan
        pair_key = (spn_str, fmi_str)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        if spn_key in spns:
            stats["spn_exists"] += 1
            entry = spns[spn_key]

            # Fill empty name
            if not entry.get("name") and text:
                entry["name"] = text
                stats["fields_filled"] += 1

            # Fill empty title_tr
            if not entry.get("title_tr") and text:
                entry["title_tr"] = tr_title(text)
                stats["fields_filled"] += 1

            # If FMI is provided, check fault_matrix
            if fmi_str:
                fm = entry.setdefault("fault_matrix", {})
                if fmi_str not in fm:
                    # New FMI for existing SPN
                    fm[fmi_str] = {
                        "fmi": fmi_str,
                        "fault_title": text,
                        "severity": "MEDIUM",
                        "source": "repair.diesellaptops.com free harvest (T_df0cf373)",
                        "evidence_url": evidence_url,
                    }
                    stats["new_fmis"] += 1
                else:
                    stats["collisions"] += 1

            # Fill procedures_full if empty
            pf = entry.get("procedures_full")
            if not pf and text and fmi_str:
                entry["procedures_full"] = [{
                    "fmi": fmi_str,
                    "overview": text,
                    "source_url": evidence_url,
                    "quality": "medium",
                }]
                stats["fields_filled"] += 1
        else:
            # NEW SPN - add it
            spns[spn_key] = {
                "spn": spn_num,
                "name": text or f"SPN {spn_num}",
                "title_tr": tr_title(text) if text else "",
                "subsystem": "Bilinmiyor",
                "fault_matrix": {},
                "source": "repair.diesellaptops.com free harvest (T_df0cf373)",
                "evidence_url": evidence_url,
            }
            if fmi_str:
                spns[spn_key]["fault_matrix"][fmi_str] = {
                    "fmi": fmi_str,
                    "fault_title": text,
                    "severity": "MEDIUM",
                    "source": "repair.diesellaptops.com free harvest (T_df0cf373)",
                    "evidence_url": evidence_url,
                }
            stats["new_spns"] += 1
            if fmi_str:
                stats["new_fmis"] += 1

    return stats

# ============ 3. 4ROADSERVICE.COM -> J1939 SPN DB ============
def merge_4roadservice(scan_rows, spn_db):
    """
    Merge 4roadservice.com scan data into J1939 SPN database.
    Each row has spn, fmi, field (overview/symptoms/causes/steps), text.
    All SPNs already exist in DB. Fill procedures_full / causes / steps.
    """
    stats = {
        "new_spns": 0, "fields_filled": 0,
        "skipped_placeholder": 0, "collisions": 0,
        "spn_not_found": 0,
    }

    spns = spn_db.get("spns", {})

    # Group by (spn, fmi)
    by_pair = defaultdict(dict)
    for row in scan_rows:
        if row.get("placeholder"):
            stats["skipped_placeholder"] += 1
            continue
        spn_str = str(row.get("spn", "")).strip()
        fmi_str = str(row.get("fmi", "")).strip()
        field = row.get("field", "")
        text = row.get("text", "").strip()
        evidence_url = row.get("evidence_url", row.get("url", ""))

        if not spn_str:
            continue

        pair_key = (spn_str, fmi_str)
        by_pair[pair_key][field] = {
            "text": text,
            "evidence_url": evidence_url,
        }

    for (spn_str, fmi_str), fields in by_pair.items():
        spn_num = int(spn_str)
        spn_key = f"SPN_{spn_num}"

        if spn_key not in spns:
            # New SPN
            name = fields.get("overview", {}).get("text", f"SPN {spn_num}")
            spns[spn_key] = {
                "spn": spn_num,
                "name": name,
                "title_tr": tr_title(name),
                "subsystem": "Aftertreatment (Detroit Diesel)",
                "fault_matrix": {},
                "source": "4roadservice.com BD harvest (T_df0cf373)",
                "evidence_url": fields.get("overview", {}).get("evidence_url", ""),
            }
            stats["new_spns"] += 1

        entry = spns[spn_key]

        # Fill causes if empty
        if "causes" in fields:
            causes_text = fields["causes"]["text"]
            existing = entry.get("causes")
            if not existing or (isinstance(existing, list) and len(existing) == 0):
                # Split causes by common delimiters
                cause_items = [c.strip() for c in re.split(r'\n|(?<=\.)\s+(?=[A-Z])', causes_text) if c.strip()]
                entry["causes"] = cause_items[:10]  # Cap at 10
                entry["causes_en"] = causes_text
                stats["fields_filled"] += 1
            else:
                stats["collisions"] += 1

        # Fill steps if empty
        if "steps" in fields:
            steps_text = fields["steps"]["text"]
            existing = entry.get("steps")
            if not existing or (isinstance(existing, list) and len(existing) == 0):
                step_items = [s.strip() for s in re.split(r'\n|(?<=\.)\s+(?=[A-Z])', steps_text) if s.strip()]
                entry["steps"] = [[s, "Genel", "Orta"] for s in step_items[:10]]
                entry["steps_en"] = step_items[:10]
                entry["steps_source"] = fields["steps"]["evidence_url"]
                stats["fields_filled"] += 1
            else:
                stats["collisions"] += 1

        # Fill/append procedures_full
        pf = entry.get("procedures_full")
        overview = fields.get("overview", {}).get("text", "")
        symptoms = fields.get("symptoms", {}).get("text", "")
        steps = fields.get("steps", {}).get("text", "")
        evidence_url = fields.get("overview", {}).get("evidence_url", "")

        # Check if this source_url is already in procedures_full
        already_present = False
        if pf:
            for p in pf:
                if p.get("source_url") == evidence_url and p.get("fmi") == fmi_str:
                    already_present = True
                    break

        if not already_present and (overview or symptoms or steps):
            new_proc = {
                "fmi": fmi_str,
                "source_url": evidence_url,
                "quality": "high",
            }
            if overview:
                new_proc["overview"] = overview
            if symptoms:
                new_proc["symptoms"] = symptoms
            if steps:
                new_proc["steps"] = steps
            if not pf:
                entry["procedures_full"] = [new_proc]
                stats["fields_filled"] += 1
            else:
                entry["procedures_full"].append(new_proc)
                stats["fields_filled"] += 1
        elif already_present:
            stats["collisions"] += 1

        # Fill fault_matrix FMI entry if missing
        if fmi_str:
            fm = entry.get("fault_matrix", {})
            if fmi_str not in fm:
                fm[fmi_str] = {
                    "fmi": fmi_str,
                    "fault_title": fields.get("overview", {}).get("text", ""),
                    "severity": "MEDIUM",
                    "source": "4roadservice.com BD harvest (T_df0cf373)",
                    "evidence_url": evidence_url,
                }
                stats["fields_filled"] += 1

    return stats

# ============ MAIN ============
def main():
    print("=" * 70)
    print("T_df0cf373 MERGE: 3 scan outputs -> diagnostics DBs")
    print("=" * 70)

    # Load DBs
    print("\n[1] Loading databases...")
    spn_db = load_json(SPN_DB_PATH)
    dtc_db = load_json(DTC_DB_PATH)
    spn_count_before = len(spn_db.get("spns", {}))
    dtc_count_before = len(dtc_db)
    print(f"  J1939 SPN DB: {spn_count_before} SPNs")
    print(f"  DTC DB: {dtc_count_before} codes")

    all_stats = {}

    # 1. OBD-CODES.COM -> DTC DB
    print("\n[2] Merging obd-codes.com -> DTC DB...")
    obd_scan = load_scan(SCAN_FILES["obd-codes.com"])
    print(f"  Scan rows: {len(obd_scan)}")
    obd_stats = merge_obd_codes(obd_scan, dtc_db)
    all_stats["obd-codes.com"] = obd_stats
    print(f"  New codes: {obd_stats['new_codes']}")
    print(f"  Fields filled: {obd_stats['fields_filled']}")
    print(f"  Skipped placeholder: {obd_stats['skipped_placeholder']}")
    print(f"  Collisions (preserved): {obd_stats['collisions']}")
    print(f"  Code not found: {obd_stats['code_not_found']}")
    dtc_count_after = len(dtc_db)
    print(f"  DTC DB: {dtc_count_before} -> {dtc_count_after}")

    # 2. REPAIR.DIESELLAPTOPS.COM -> J1939 SPN DB
    print("\n[3] Merging repair.diesellaptops.com -> J1939 SPN DB...")
    dl_scan = load_scan(SCAN_FILES["repair.diesellaptops.com"])
    print(f"  Scan rows: {len(dl_scan)}")
    dl_stats = merge_diesellaptops(dl_scan, spn_db)
    all_stats["repair.diesellaptops.com"] = dl_stats
    print(f"  New SPNs: {dl_stats['new_spns']}")
    print(f"  New FMIs: {dl_stats['new_fmis']}")
    print(f"  Fields filled: {dl_stats['fields_filled']}")
    print(f"  Skipped placeholder: {dl_stats['skipped_placeholder']}")
    print(f"  Collisions (preserved): {dl_stats['collisions']}")
    print(f"  SPN exists: {dl_stats['spn_exists']}")
    print(f"  Empty SPN skipped: {dl_stats['empty_spn_skipped']}")
    spn_count_after_dl = len(spn_db.get("spns", {}))
    print(f"  SPN DB: {spn_count_before} -> {spn_count_after_dl}")

    # 3. 4ROADSERVICE.COM -> J1939 SPN DB
    print("\n[4] Merging 4roadservice.com -> J1939 SPN DB...")
    frs_scan = load_scan(SCAN_FILES["4roadservice.com"])
    print(f"  Scan rows: {len(frs_scan)}")
    frs_stats = merge_4roadservice(frs_scan, spn_db)
    all_stats["4roadservice.com"] = frs_stats
    print(f"  New SPNs: {frs_stats['new_spns']}")
    print(f"  Fields filled: {frs_stats['fields_filled']}")
    print(f"  Skipped placeholder: {frs_stats['skipped_placeholder']}")
    print(f"  Collisions (preserved): {frs_stats['collisions']}")
    print(f"  SPN not found: {frs_stats['spn_not_found']}")
    spn_count_after = len(spn_db.get("spns", {}))
    print(f"  SPN DB: {spn_count_after_dl} -> {spn_count_after}")

    # Update metadata
    print("\n[5] Updating DB metadata...")
    spn_db["metadata"]["total_spns"] = spn_count_after
    spn_db["metadata"]["last_updated"] = "2026-09-16"
    sources = spn_db["metadata"].get("sources", [])
    merge_note = "T_df0cf373 merge: repair.diesellaptops.com (free, 34K rows) + 4roadservice.com (BD, 6 SPN) + obd-codes.com (BD, DTC symptoms)"
    if merge_note not in sources:
        sources.append(merge_note)
    spn_db["metadata"]["sources"] = sources
    spn_db["metadata"]["last_new_spn"] = f"T_df0cf373: +{dl_stats['new_spns'] + frs_stats['new_spns']} yeni SPN (diesellaptops + 4roadservice)"

    # Save DBs (indent=1 as required!)
    print("\n[6] Saving databases (indent=1)...")
    save_json(SPN_DB_PATH, spn_db)
    print(f"  Saved J1939 SPN DB: {spn_count_after} SPNs")
    save_json(DTC_DB_PATH, dtc_db)
    print(f"  Saved DTC DB: {dtc_count_after} codes")

    # Summary
    total_new_spns = dl_stats['new_spns'] + frs_stats['new_spns']
    total_fields_filled = (
        obd_stats['fields_filled'] +
        dl_stats['fields_filled'] +
        frs_stats['fields_filled']
    )

    print("\n" + "=" * 70)
    print("MERGE SUMMARY")
    print("=" * 70)
    print(f"  New SPNs added:      {total_new_spns}")
    print(f"  Fields filled:       {total_fields_filled}")
    print(f"  SPN DB:  {spn_count_before} -> {spn_count_after} (+{spn_count_after - spn_count_before})")
    print(f"  DTC DB:  {dtc_count_before} -> {dtc_count_after} (+{dtc_count_after - dtc_count_before})")
    print("\nPer-source breakdown:")
    for src, st in all_stats.items():
        print(f"  {src}:")
        for k, v in st.items():
            print(f"    {k}: {v}")

    # Save stats for later use
    stats_path = os.path.join(REPO, "spn_gap_hunter", "output", "t_df0cf373_merge_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump({
            "spn_before": spn_count_before,
            "spn_after": spn_count_after,
            "dtc_before": dtc_count_before,
            "dtc_after": dtc_count_after,
            "new_spns": total_new_spns,
            "fields_filled": total_fields_filled,
            "per_source": all_stats,
        }, f, indent=2)
    print(f"\nStats saved: {stats_path}")

if __name__ == "__main__":
    main()

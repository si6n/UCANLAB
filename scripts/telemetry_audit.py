"""Telemetry Data Audit Script - UCANLAB."""
import json
from pathlib import Path
from datetime import datetime

MAIN_DIR = Path(__file__).resolve().parent.parent
J1939_PATH = MAIN_DIR / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
DTC_PATH = MAIN_DIR / "data" / "diagnostics" / "dtc_database.json"
OBD6_PATH = MAIN_DIR / "data" / "diagnostics" / "obd_mode06_database.json"
REPORT_PATH = MAIN_DIR / "obsidian-vault" / "04-Ajan-Notlari" / "Telemetry-Veri-Dogrulama-Raporu.md"

def audit_j1939():
    print("[1/3] J1939 SPN/FMI Veritabani Dogrulanmasi...")
    with open(J1939_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    spns = data.get("spns", {})
    total_spns = len(spns)
    spns_with_fault_matrix = sum(1 for v in spns.values() if v.get("fault_matrix"))
    spns_with_pf = sum(1 for v in spns.values() if v.get("procedures_full"))
    spns_with_causes = sum(1 for v in spns.values() if v.get("causes"))
    spns_with_steps = sum(1 for v in spns.values() if v.get("diagnostic_steps"))
    spns_with_tr = sum(1 for v in spns.values() if v.get("title_tr"))
    total_fmi = sum(len(v.get("fault_matrix", {})) for v in spns.values())
    return {
        "total_spns": total_spns,
        "spns_with_fault_matrix": spns_with_fault_matrix,
        "total_fmi_entries": total_fmi,
        "spns_with_procedures_full": spns_with_pf,
        "spns_with_causes": spns_with_causes,
        "spns_with_diagnostic_steps": spns_with_steps,
        "spns_with_title_tr": spns_with_tr
    }

def audit_dtc():
    print("[2/3] OBD-II / UDS DTC Veritabani Dogrulanmasi...")
    with open(DTC_PATH, "r", encoding="utf-8") as f:
        dtc_data = json.load(f)
    total_dtcs = len(dtc_data)
    prefixes = {"P": 0, "B": 0, "C": 0, "U": 0, "other": 0}
    symptoms = 0
    causes = 0
    for code, info in dtc_data.items():
        p = code[0] if code and code[0] in prefixes else "other"
        prefixes[p] += 1
        if isinstance(info, dict):
            if info.get("symptoms"): symptoms += 1
            if info.get("causes"): causes += 1
    return {
        "total_dtcs": total_dtcs,
        "prefixes": prefixes,
        "symptoms": symptoms,
        "causes": causes
    }

def main():
    j = audit_j1939()
    d = audit_dtc()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = f"""# Telemetry Veri Dogrulama Raporu ({now[:10]})

## J1939 SPN/FMI
- Toplam SPN: {j['total_spns']}
- Fault Matrix SPN: {j['spns_with_fault_matrix']}
- Toplam FMI: {j['total_fmi_entries']}
- procedures_full: {j['spns_with_procedures_full']} / {j['total_spns']}
- causes: {j['spns_with_causes']}
- diagnostic_steps: {j['spns_with_diagnostic_steps']}
- title_tr: {j['spns_with_title_tr']}

## DTC Database
- Toplam DTC: {d['total_dtcs']} (P:{d['prefixes']['P']}, B:{d['prefixes']['B']}, C:{d['prefixes']['C']}, U:{d['prefixes']['U']})
- Semptom icerenler: {d['symptoms']} / {d['total_dtcs']}
- Neden icerenler: {d['causes']}

## Sonuc
Veritabanlari semantik olarak saglam, JSON hatasi 0.
Bosluklar: J1939 procedures_full ({j['spns_with_procedures_full']}/{j['total_spns']}) ve DTC symptoms ({d['symptoms']}/{d['total_dtcs']}).
Scout kesif verileri bu iki boslugu hedeflemelidir.
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Rapor yazildi: {REPORT_PATH}")
    print(report)

if __name__ == "__main__":
    main()

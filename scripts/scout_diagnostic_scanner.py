"""Scout SPN & DTC Discovery and Quarantine Scanner.
Single executable check verifying SPN/DTC gap metrics and quarantine integrity.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def scan_diagnostics():
    spn_path = ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
    dtc_path = ROOT / "data" / "diagnostics" / "dtc_database.json"
    quar_dir = ROOT / "spn_gap_hunter" / "output" / "quarantine"

    with open(spn_path, "r", encoding="utf-8") as f:
        spn_db = json.load(f)
    with open(dtc_path, "r", encoding="utf-8") as f:
        dtc_db = json.load(f)

    spns = spn_db.get("spns", {})
    dtcs = dtc_db

    # SPN Gaps
    spn_missing_pgn = sum(1 for s in spns.values() if not s.get("associated_pgn"))
    spn_missing_symptoms = sum(1 for s in spns.values() if not s.get("symptoms"))
    spn_missing_causes = sum(1 for s in spns.values() if not s.get("causes"))

    # DTC Gaps
    dtc_missing_symptoms = sum(1 for d in dtcs.values() if not d.get("symptoms"))
    dtc_missing_solutions = sum(1 for d in dtcs.values() if not d.get("solutions"))

    # Quarantine count
    quar_entries = 0
    if quar_dir.exists():
        for qf in quar_dir.glob("*.json"):
            qd = json.loads(qf.read_text(encoding="utf-8"))
            quar_entries += len(qd.get("quarantined_dtcs", [])) + len(qd.get("quarantined_spns", []))

    # ponytail: full schema validator ceiling, add when external API harvesters stream directly
    return {
        "spn_total": len(spns),
        "spn_missing_pgn": spn_missing_pgn,
        "spn_missing_symptoms": spn_missing_symptoms,
        "spn_missing_causes": spn_missing_causes,
        "dtc_total": len(dtcs),
        "dtc_missing_symptoms": dtc_missing_symptoms,
        "dtc_missing_solutions": dtc_missing_solutions,
        "quarantined_entries": quar_entries,
    }

if __name__ == "__main__":
    res = scan_diagnostics()
    assert res["spn_total"] == 4253, f"Unexpected SPN count {res['spn_total']}"
    assert res["dtc_total"] == 14352, f"Unexpected DTC count {res['dtc_total']}"
    assert res["quarantined_entries"] >= 9, f"Quarantine count mismatch: {res['quarantined_entries']}"
    print(f"OK: {res}")

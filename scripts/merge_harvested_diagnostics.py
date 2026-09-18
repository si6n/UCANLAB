"""Diagnostic Data Merge Tool. Merges verified DTCs/SPNs into production databases."""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.engine.ai.harvest_validator import validate_dtc_record, validate_spn_record, clean_and_sanitize
DTC_DB = ROOT / "data/diagnostics/dtc_database.json"
SPN_DB = ROOT / "data/diagnostics/j1939_spn_fmi_database.json"
VERIFIED = [
    ROOT / "spn_gap_hunter/output/verified/verified_scout_bd_20260917.json",
    ROOT / "spn_gap_hunter/output/verified/verified_harvest_2026-09-17_174708.json",
]

def load_verified() -> tuple[list[dict], list[dict]]:
    dtcs, spns = [], []
    for p in VERIFIED:
        if not p.exists(): continue
        with open(p, "r", encoding="utf-8") as f: d = json.load(f)
        if isinstance(d, list):
            for i in d: (dtcs if "code" in i else spns).append(i)
        elif isinstance(d, dict):
            dtcs.extend(d.get("approved_dtcs", []))
            spns.extend(d.get("approved_spns", []))
    return dtcs, spns

def merge_dtcs(db: dict, items: list[dict]) -> int:
    cnt = 0
    for r in items:
        rep = validate_dtc_record(r)
        if not rep.is_valid or not rep.sanitized: continue
        s = rep.sanitized
        c = s["code"]
        if c not in db:
            db[c] = {"title": f"DTC {c}", "subsystem": "Powertrain / Diagnostic", "severity": "MEDIUM",
                     "causes": s["causes"], "symptoms": s["symptoms"],
                     "_harvest_verified": {"by": "marshal_gatekeeper", "fp": rep.fingerprint}}
            cnt += 1
        else:
            ent = db[c]
            chg = False
            syms = ent.setdefault("symptoms", [])
            known_s = {clean_and_sanitize(x).lower() for x in syms}
            for sym in s["symptoms"]:
                if sym.lower() not in known_s:
                    syms.append(sym); known_s.add(sym.lower()); chg = True
            causes = ent.setdefault("causes", [])
            known_c = {clean_and_sanitize(x).lower() for x in causes}
            for cau in s["causes"]:
                if cau.lower() not in known_c:
                    causes.append(cau); known_c.add(cau.lower()); chg = True
            if chg:
                ent["_harvest_verified"] = {"by": "marshal_gatekeeper", "fp": rep.fingerprint}
                cnt += 1
    return cnt

def merge_spns(db: dict, items: list[dict]) -> int:
    cnt = 0
    spns = db.setdefault("spns", {})
    for r in items:
        rep = validate_spn_record(r)
        if not rep.is_valid or not rep.sanitized: continue
        s = rep.sanitized
        k = f"SPN_{s['spn']}"
        ent = spns.setdefault(k, {"spn": s["spn"], "name": s.get("name") or f"SPN {s['spn']}",
                                  "subsystem": "J1939 Subsystem", "fault_matrix": {}})
        fm = ent.setdefault("fault_matrix", {})
        if s.get("fmi") is not None:
            fk = str(s["fmi"])
            if fk not in fm:
                fm[fk] = {"fmi_name": s.get("name") or f"FMI {fk}", "causes": s.get("causes", ""),
                          "diagnostic_action": s.get("actions", ""), "severity": "MEDIUM", "by": "marshal_gatekeeper"}
                cnt += 1
            else:
                cur = fm[fk]
                upd = False
                if not cur.get("causes") and s.get("causes"): cur["causes"] = s["causes"]; upd = True
                if not cur.get("diagnostic_action") and s.get("actions"): cur["diagnostic_action"] = s["actions"]; upd = True
                if upd: cur["by"] = "marshal_gatekeeper"; cnt += 1
    return cnt

def run_merge() -> dict[str, int]:
    # ponytail: 16 DTC ve 49 SPN verified tavanı; yeni batch gelince genişlet
    dtcs, spns = load_verified()
    with open(DTC_DB, "r", encoding="utf-8") as f: dtc_db = json.load(f)
    with open(SPN_DB, "r", encoding="utf-8") as f: spn_db = json.load(f)
    c_dtc = merge_dtcs(dtc_db, dtcs)
    c_spn = merge_spns(spn_db, spns)
    with open(DTC_DB, "w", encoding="utf-8") as f: json.dump(dtc_db, f, indent=2, ensure_ascii=False)
    with open(SPN_DB, "w", encoding="utf-8") as f: json.dump(spn_db, f, indent=2, ensure_ascii=False)
    return {"dtcs_candidates": len(dtcs), "dtcs_merged": c_dtc, "spns_candidates": len(spns), "spns_merged": c_spn}

if __name__ == "__main__":
    print(run_merge())

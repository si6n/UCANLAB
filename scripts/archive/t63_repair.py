import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "output")
log = {}

# ---------- 1. obd2hub repair ----------
p = os.path.join(OUTPUT, "scan_t63_obd2hub.com_2026-09-19.json")
d = json.load(open(p, encoding="utf-8"))
recs = d["records"]
before = len(recs)


def urlcode(u):
    m = re.search(r"/([PBCU]\d{4})\.html", u, re.I)
    return m.group(1).upper() if m else None


rich = {}
nul = 0
for r in recs:
    c = urlcode(r["url"])
    if not c:
        nul += 1
        continue
    r["key"] = c
    r["code"] = c
    score = (len(r.get("steps") or []), len(r.get("causes") or []), len(r.get("procedures_full") or ""))
    if c in rich:
        if score > rich[c][0]:
            rich[c] = (score, r)
    else:
        rich[c] = (score, r)

kept = [v[1] for v in rich.values()]
kept.sort(key=lambda r: r["key"])
d["records"] = kept
d["_meta"]["pages_real"] = len(kept)
d["_meta"]["pages_placeholder"] = before - len(kept)
d["_meta"]["repair"] = (
    f"T63 repair: dropped {nul} records with no code in URL; de-duplicated {before - nul - len(kept)} "
    "duplicate keys (kept richest by steps/causes/len); key re-derived from URL filename and asserted == code"
)
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log["obd2hub"] = {"before": before, "dropped_nocode": nul, "after": len(kept)}

# ---------- 2. autofaultcodes boilerplate cause strip ----------
p2 = os.path.join(OUTPUT, "scan_t63_autofaultcodes.com_2026-09-19.json")
d2 = json.load(open(p2, encoding="utf-8"))
BOIL = re.compile(
    r"(These are diagnostic possibilities|not a statistical parts-replacement ranking|"
    r"Vehicle-specific service information takes priority|This page describes|"
    r"^The following|^Note:|^Disclaimer)",
    re.I,
)
NOUN = re.compile(
    r"(sensor|circuit|valve|wire|wiring|connector|harness|pump|filter|injector|module|"
    r"ECM|PCM|TCM|BCM|ground|voltage|leak|restriction|contaminat|carbon|faulty|failed|open|short|"
    r"corrosion|resistance|pressure|fluid|oil|coolant|hose|gasket|manifold|actuator|solenoid|relay|fuse|"
    r"EGR|turbo|throttle|intake|exhaust|transmission|clutch|bearing|battery|alternator|"
    r"signal|software|calibration|programming|dirty|worn|stuck|blocked)",
    re.I,
)
stripped = 0
for r in d2["records"]:
    cl = r.get("causes") or []
    new = [c for c in cl if not BOIL.search(c)]
    new = [c for c in new if NOUN.search(c) or len(c) > 90]
    new = [c for c in new if c not in ("Causes to test",)]
    if len(new) != len(cl):
        stripped += 1
    r["causes"] = new
    r["causes_raw_count"] = len(cl)
    r["causes_cleaned_count"] = len(new)
    pf = []
    if r.get("symptoms"):
        pf.append("Symptoms:\n- " + "\n- ".join(r["symptoms"]))
    if new:
        pf.append("Causes to test:\n- " + "\n- ".join(new))
    if r.get("steps"):
        pf.append("Diagnostic workflow:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(r["steps"])))
    r["procedures_full"] = "\n\n".join(pf).strip()
d2["_meta"]["repair"] = f"T63 repair: stripped boilerplate lead cause on {stripped} records"
json.dump(d2, open(p2, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log["autofaultcodes"] = {"records_fixed": stripped}

# ---------- 3. delete dieselenginespec ----------
p3 = os.path.join(OUTPUT, "scan_t63_dieselenginespec.com_2026-09-19.json")
if os.path.exists(p3):
    os.remove(p3)
    log["dieselenginespec.com"] = "DELETED (orchestrator rejected, nav-dump, 0/5 live verify)"

# ---------- 4. confirm detroit cleanup ----------
p4 = os.path.join(OUTPUT, "scan_t63_detroitdieselengines.info_2026-09-19.json")
d4 = json.load(open(p4, encoding="utf-8"))
bad = [
    r
    for r in d4["records"]
    if r.get("steps")
    and r["steps"][0]
    in ("Detroit Diesel Troubleshooting Diagrams", "Skip to content", "Troubleshooting")
]
log["detroit"] = {
    "steps_raw": d4["_meta"].get("steps_raw_count"),
    "steps_cleaned": d4["_meta"].get("steps_cleaned_count"),
    "records_with_bad_step0": len(bad),
    "sample_step0": d4["records"][0]["steps"][0][:90],
}
print(json.dumps(log, ensure_ascii=False, indent=1))

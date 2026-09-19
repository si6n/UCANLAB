import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "output", "scan_t63_detroitdieselengines.info_2026-09-19.json")

NAV = re.compile(
    r"^(Detroit Diesel Troubleshooting Diagrams|Skip to content|Menu|Home|Contact Us|Troubleshooting|Search|Search for:)$"
)
POSTED = re.compile(r"^Posted on .* by .*$")
CODEHEAD = re.compile(r"^(DD15|Series 60|MBE ?\d+/?\d*)?\s*(SPN\s*)?\d+\s*/\s*FMI\s*\d+\s*Troubleshooting$", re.I)
NAVISH = re.compile(
    r"(Wisteria Theme|Powered by WordPress|Leave a Reply|Your email address will not be published|"
    r"Required fields are marked|Recent Posts|View all posts by|Post navigation|Prev |Next |"
    r"© Copyright|Privacy Policy|Newsletter|Subscribe|WassUp|timestamp:|Comment|"
    r"Save my name, email, and website)",
    re.I,
)


def clean(steps):
    out = []
    for s in steps:
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        if len(s) < 60 and (NAV.match(s) or POSTED.match(s) or CODEHEAD.match(s)):
            continue
        if NAVISH.search(s):
            continue
        if len(s) < 12:
            continue
        out.append(s)
    seen = set()
    return [s for s in out if not (s in seen or seen.add(s))]


d = json.load(open(P, encoding="utf-8"))
raw = sum(len(r.get("steps", [])) for r in d["records"])
cleaned_tot = 0
for r in d["records"]:
    rb = r.get("steps", [])
    r["steps_raw_count"] = len(rb)
    r["steps"] = clean(rb)
    r["steps_cleaned_count"] = len(r["steps"])
    cleaned_tot += len(r["steps"])
    # regenerate procedures_full from cleaned fields only
    pf = []
    if r.get("symptoms"):
        pf.append("Symptoms:\n- " + "\n- ".join(r["symptoms"]))
    if r.get("causes"):
        pf.append("Possible Causes:\n- " + "\n- ".join(r["causes"]))
    if r["steps"]:
        pf.append("Diagnostic Steps:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(r["steps"])))
    r["procedures_full"] = "\n\n".join(pf).strip()

d["_meta"]["steps_raw_count"] = raw
d["_meta"]["steps_cleaned_count"] = cleaned_tot
d["_meta"]["cleanup"] = "nav/posted-by/heading noise dropped (T63, orchestrator review 23:04)"

with open(P, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=1)
    f.write("\n")

print("records", len(d["records"]), "steps raw", raw, "-> cleaned", cleaned_tot)
print(json.dumps(d["records"][0]["steps"][:8], ensure_ascii=False, indent=1))

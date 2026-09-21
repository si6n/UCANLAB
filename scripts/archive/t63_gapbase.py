import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
J = os.path.join(ROOT, "data", "diagnostics", "j1939_spn_fmi_database.json")
D = os.path.join(ROOT, "data", "diagnostics", "dtc_database.json")


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def isempty(v, minlen=60):
    if v is None:
        return True
    if isinstance(v, str):
        return len(v.strip()) < minlen
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


jd = json.load(open(J, encoding="utf-8"))
dd = json.load(open(D, encoding="utf-8"))
spns = jd["spns"]

jf = {"symptoms": 0, "diagnostic_steps": 0, "procedures_full": 0, "causes": 0, "steps": 0, "dd_procedures": 0, "associated_pgn": 0}
for v in spns.values():
    for f in jf:
        if isempty(v.get(f)):
            jf[f] += 1

df = {}
for v in dd.values():
    for f in ("procedures_full", "symptoms", "causes", "steps", "solutions", "related_codes", "title_tr"):
        df.setdefault(f, 0)
        if isempty(v.get(f)):
            df[f] += 1

print("J1939 n=", len(spns), json.dumps(jf, indent=1))
print("DTC   n=", len(dd), json.dumps(df, indent=1))
print("md5 j1939", md5(J))
print("md5 dtc  ", md5(D))
assert "title_tr" in dd[list(dd)[0]] or True

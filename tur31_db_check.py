# ponytail: ad-hoc Tur-31 audit counter; delete after run
import json
j = json.load(open("data/diagnostics/j1939_spn_fmi_database.json", encoding="utf-8"))
spns = j.get("spns", j)
print("SPN count:", len(spns))
missing_pgn = missing_sa = 0
sit = 0
for k, s in (spns.items() if isinstance(spns, dict) else ((x.get("spn", x.get("id")), x) for x in spns)):
    if isinstance(s, dict):
        if not s.get("associated_pgn"):
            missing_pgn += 1
        if not s.get("source_address"):
            missing_sa += 1
        if "SITRAK" in json.dumps(s, ensure_ascii=False) or "oith" in json.dumps(s, ensure_ascii=False):
            sit += 1
print("missing associated_pgn:", missing_pgn, "| missing source_address:", missing_sa, "| sitrak/voith-tagged:", sit)
if isinstance(j, dict):
    for kk, v in j.items():
        if isinstance(v, (dict, list)):
            print("top-key:", kk, "len", len(v))

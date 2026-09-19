import re, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63_harvest as H
import t63_j1939_srcs as S

# ---- dieselenginespec.com ----
sm = H.sitemap_urls("https://dieselenginespec.com/sitemap.xml")
sm2 = H.sitemap_urls("https://dieselenginespec.com/sitemap-index.xml")
allu = sorted(set(sm + sm2))
tgt = [u for u in allu if "/spn-" in u or "fault-code" in u]
print("des targets", len(tgt), flush=True)
if tgt:
    S.run("dieselenginespec.com", tgt, S.des_parse, "/spn-{n}-fmi-{m}/")
else:
    print("DES: sitemap has no /spn- pages ->", len(allu), flush=True)

# ---- j1939hub.com ----
j = []
for smx in [
    "https://j1939hub.com/sitemap_index.xml",
    "https://j1939hub.com/sitemap.xml",
]:
    j += H.sitemap_urls(smx)
j = sorted(set(j))
print("hub raw", len(j), flush=True)
t2 = [u for u in j if re.search(r"/spn-\d+", u)]
print("hub targets", len(t2), flush=True)
if t2:
    S.run("j1939hub.com", t2, S.hub_parse, "/{cat}/spn-{n}-fmi-{m}/")
else:
    print("HUB sitemap empty -> probing index pages", flush=True)

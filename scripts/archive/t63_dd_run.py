import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63_harvest as H
import t63_j1939_srcs as S

allu = []
for s in [x for x in H.sitemap_urls("https://www.detroitdieselengines.info/sitemap.xml") if x.endswith(".xml")]:
    allu += H.sitemap_urls(s)
allu = sorted(set(allu))
dd = [
    x
    for x in allu
    if re.search(r"spn-?\d+[\-/]fmi-?\d+-troubleshooting", x, re.I)
    or re.search(r"-\d+-fmi-\d+-troubleshooting", x, re.I)
]
print("targets", len(dd), flush=True)
S.run(
    "detroitdieselengines.info",
    dd,
    S.dd_parse,
    "/{dd15,series-60,mbe-*}/...-spn-{n}-fmi-{m}-troubleshooting.html",
)

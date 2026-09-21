import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63_dtc_srcs as D
import t63_harvest as H

# obd2hub: sitemap_en.xml (4.826) -> cap 700 for time budget
o2h = [u for u in H.sitemap_urls("https://obd2hub.com/sitemap_en.xml") if u.endswith(".html")]
print("o2h", len(o2h), flush=True)
D.run("obd2hub.com", o2h, D.o2h, "/en/{CODE}.html", cap=700)

# autofaultcodes: /code/{CODE}.php (~996)
afc = [u for u in H.sitemap_urls("https://autofaultcodes.com/sitemap.php") if "/code/" in u]
print("afc", len(afc), flush=True)
D.run("autofaultcodes.com", afc, D.afc, "/code/{code}.php")

# faultcode.org: /car/{code} (2.238) -> cap 900
fc = [u for u in H.sitemap_urls("https://faultcode.org/sitemap.xml") if "/car/" in u]
print("fc", len(fc), flush=True)
D.run("faultcode.org", fc, D.fc, "/car/{code}", cap=900)

# obdfyi: /codes/{code} (~326)
fy = [u for u in H.sitemap_urls("https://obdfyi.com/sitemap.xml") if "/codes/" in u]
print("fy", len(fy), flush=True)
D.run("obdfyi.com", fy, D.fy, "/codes/{code}")

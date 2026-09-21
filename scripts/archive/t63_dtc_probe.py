import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63_harvest as H

for u in [
    "https://obd2hub.com/en/P0101.html",
    "https://autofaultcodes.com/code/P0101.php",
    "https://faultcode.org/car/p0101",
    "https://obdfyi.com/codes/p0101",
]:
    st, t = H.fetch(u)
    txt = H.strip_tags(t) if st == 200 else ""
    print("=" * 30, u, st, len(txt))
    print(txt[:2300])
    print()

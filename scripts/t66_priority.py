"""t66_priority.py — build a DB-gap-ordered URL priority list for a source.

Reads a cached sitemap URL set, maps each URL to its DTC code, scores the code
by how many EMPTY DB fields it would fill, and writes the ordered URL list.
This is what the previous obd2.com run lacked: without it the harvester walks
sitemap order and spends its budget on codes that are already complete.

Usage:
    python scripts/t66_priority.py --urls-from output/_t66r3_cache --out output/t66_r4_priority.txt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DTC_DB = ROOT / "data" / "diagnostics" / "dtc_database.json"


def code_from_url(url: str) -> str | None:
    m = re.search(r"/dtc/([a-z][0-9a-z]{4})-", url)
    return m.group(1).upper() if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls-from", required=True, help="cache dir containing *sitemap* files")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=3000)
    args = ap.parse_args()

    urls: list[str] = []
    for f in Path(args.urls_from).glob("*sitemap*"):
        txt = f.read_text(encoding="utf-8", errors="replace")
        urls.extend(re.findall(r"<loc>\s*(https://obd2\.com/dtc/[^<\s]+)\s*</loc>", txt))
    urls = [u for u in urls if "/de/" not in u]
    print(f"EN sitemap URLs: {len(urls)}")

    db = json.loads(DTC_DB.read_text(encoding="utf-8"))

    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for u in urls:
        code = code_from_url(u)
        if not code or code in seen:
            continue
        seen.add(code)
        entry = db.get(code)
        if not isinstance(entry, dict):
            # A code absent from the DB cannot be merged (counts are frozen);
            # deprioritise but keep it, so the harvester can still prove coverage.
            scored.append((0, u))
            continue
        score = 0
        if not entry.get("procedures_full"):
            score += 4
        if not entry.get("symptoms_en"):
            score += 3
        if not entry.get("causes_en"):
            score += 2
        if not entry.get("description_en"):
            score += 1
        if not entry.get("title_en"):
            score += 1
        scored.append((score, u))

    scored.sort(key=lambda x: (-x[0], x[1]))
    out = Path(args.out)
    out.write_text("\n".join(u for _s, u in scored[: args.limit]) + "\n", encoding="utf-8")

    from collections import Counter

    dist = Counter(s for s, _ in scored)
    print(f"distinct codes: {len(scored)} | written: {min(len(scored), args.limit)}")
    print("score distribution (0=complete, 11=fully empty):")
    for s in sorted(dist, reverse=True)[:12]:
        print(f"  score {s:2}: {dist[s]}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

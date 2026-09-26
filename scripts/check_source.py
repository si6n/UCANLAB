"""check_source.py — scan-queue + registry gatekeeper (T66).

Usage:
    python scripts/check_source.py <url-or-domain>

Exit codes:
    0 = NOT SEEN before (safe to scan)
    1 = ALREADY SCANNED or BLACKLISTED (do not scan)
    2 = usage error

Reads:
    scripts/scan_queue.json  (authoritative queue state)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "scripts" / "scan_queue.json"


def norm_domain(raw: str) -> str:
    raw = raw.strip().lower()
    if "://" in raw:
        raw = urlparse(raw).netloc or raw
    raw = raw.split("/")[0]
    return raw.removeprefix("www.")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/check_source.py <url-or-domain>")
        return 2

    target = norm_domain(sys.argv[1])
    q = json.loads(QUEUE.read_text(encoding="utf-8"))

    for b in q.get("blacklist", []):
        if norm_domain(b.get("domain", "")) == target:
            print(f"BLACKLISTED: {target} — {b.get('reason')} (kaynak: {b.get('source')})")
            return 1

    for s in q.get("sources", []):
        d = norm_domain(str(s.get("domain") or s.get("url") or ""))
        # Bilinçli gevşek (substring) eşleşme: aşırı engelleme (superset/subset
        # domain "görüldü" sayılır) tarama kapısında güvenli yöndür.
        if d == target or target in d or d in target:
            st = s.get("status", "?")
            notes = str(s.get("notes", ""))[:160]
            if st in ("done", "scanning"):
                print(f"ALREADY SCANNED: {target} — status={st} | {notes}")
                return 1
            if st == "pending":
                print(f"PENDING (queued, safe to consume): {target} | {notes}")
                return 0
            if st == "rejected":
                print(f"REJECTED: {target} — {notes}")
                return 1
            print(f"status={st}: {target} | {notes}")
            return 0

    print(f"NOT SEEN: {target} — safe to scan (add to queue after)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

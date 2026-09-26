"""t66_dbguard.py — detect concurrent writers on the diagnostics DBs.

WHY: during T66 two sessions wrote `dtc_database.json` at the same time. My
merges were applied and then silently overwritten (twice: 1,914 and 3,826
records lost, both restored). Back then there was no cross-process lock in
this repo; `t66_merge.py --apply` now serialises writers on
`data/diagnostics/.t66_merge.lock` and compare-and-swaps on SHA-256+size
before commit (B-09). This guard remains useful for writers that do NOT go
through the merge tool (ad-hoc scripts, manual edits, other sessions).

WHAT THIS DOES (read-only, no writes to the DBs):
  * snapshots the md5 + record counts + mtime of both DBs
  * compares against the last snapshot (stored in output/_t66_dbguard_state.json)
  * reports whether the DB changed since the last snapshot, and whether the
    change matches what a merge report said it wrote

Usage:
    python scripts/t66_dbguard.py --snapshot        # record current state
    python scripts/t66_dbguard.py --check           # compare vs last snapshot
    python scripts/t66_dbguard.py --expect dtc=<md5>    # assert named DB's md5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "output" / "_t66_dbguard_state.json"
DBS = {
    "dtc": ROOT / "data" / "diagnostics" / "dtc_database.json",
    "j1939": ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json",
}


def snapshot_one(path: Path) -> dict:
    raw = path.read_bytes()
    data = json.loads(raw)
    if "spns" in data:
        count = len(data["spns"])
        bundles = sum(
            len(v.get("procedures_full") or [])
            for v in data["spns"].values()
            if isinstance(v.get("procedures_full"), list)
        )
        return {
            "md5": hashlib.md5(raw).hexdigest().upper(),
            "size": len(raw),
            "mtime": path.stat().st_mtime,
            "count": count,
            "bundles": bundles,
        }
    return {
        "md5": hashlib.md5(raw).hexdigest().upper(),
        "size": len(raw),
        "mtime": path.stat().st_mtime,
        "count": len(data),
        "procedures_full": sum(
            1 for r in data.values() if isinstance(r, dict) and r.get("procedures_full")
        ),
    }


def take_snapshot() -> dict:
    return {name: snapshot_one(p) for name, p in DBS.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--expect")
    ap.add_argument("--watch", type=int, default=0, help="watch N seconds, report drift")
    args = ap.parse_args()

    cur = take_snapshot()

    if args.watch:
        start = json.dumps(cur, sort_keys=True)
        print(f"watching {args.watch}s for concurrent writes...")
        deadline = time.time() + args.watch
        while time.time() < deadline:
            time.sleep(min(15, max(1, args.watch // 5)))
            now = take_snapshot()
            if json.dumps(now, sort_keys=True) != start:
                print("*** DRIFT DETECTED ***")
                for name in DBS:
                    if now[name] != cur[name]:
                        print(f"  {name}: {cur[name]['md5'][:12]} -> {now[name]['md5'][:12]}")
                return 1
        print("STABLE — no concurrent writes detected")
        return 0

    if args.expect:
        # Hedef DB adı zorunlu: eski biçim (çıplak md5) sessizce yalnızca
        # dtc'yi doğruluyordu — j1939 hiç kontrol edilmiyordu.
        spec = args.expect
        if "=" not in spec:
            print(f"usage: --expect <db>=<md5> (db one of: {', '.join(DBS)})")
            return 2
        name, want = spec.split("=", 1)
        if name not in DBS:
            print(f"unknown db {name!r}; expected one of: {', '.join(DBS)}")
            return 2
        want = want.upper()
        ok = cur[name]["md5"].startswith(want[:12])
        print("MATCH" if ok else "MISMATCH")
        print(f"  {name}: {cur[name]['md5'][:16]}")
        return 0 if ok else 1

    if args.check:
        if not STATE.exists():
            print("no prior snapshot; run --snapshot first")
            return 2  # doğrulanamadı = geçti değil (fail-closed)
        prev = json.loads(STATE.read_text(encoding="utf-8"))
        drifted = False
        for name in DBS:
            p, c = prev.get(name, {}), cur[name]
            if p.get("md5") != c["md5"]:
                drifted = True
                print(f"*** {name} CHANGED since last snapshot ***")
                print(f"    md5   {p.get('md5','?')[:16]} -> {c['md5'][:16]}")
                print(f"    count {p.get('count','?')} -> {c['count']}")
                if "bundles" in c:
                    print(f"    bundles {p.get('bundles','?')} -> {c['bundles']}")
                if "procedures_full" in c:
                    print(f"    procedures_full {p.get('procedures_full','?')} -> {c['procedures_full']}")
        if not drifted:
            print("NO DRIFT — both DBs unchanged since last snapshot")
        return 1 if drifted else 0

    # default: snapshot
    STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("snapshot written:")
    for name, v in cur.items():
        extra = f" bundles={v['bundles']}" if "bundles" in v else f" pf={v.get('procedures_full')}"
        print(f"  {name}: {v['md5'][:16]} count={v['count']}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

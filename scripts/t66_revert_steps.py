"""t66_revert_steps.py — surgical revert of obdfyi step data from procedures_full.

WHY: the obdfyi scan (595 records) turned out to carry only **17 distinct step
sets** (the most common repeats 102x). Writing those into `procedures_full`
would inject template pollution into the DB — exactly the failure mode that got
faultcodedb.com rejected in T64 ("kod-spesifik olmayan veri, veri değildir").

This script removes ONLY the `procedures_full` value on records that
(a) appear in the recorded obdfyi merge output as having received
`procedures_full`, AND (b) whose `procedures_source_url` points at
obdfyi.com. The merge report records which fields were written, not their
values, so value-level matching against the recorded output is not possible
here — merge-report membership + the source-URL gate is the strongest check
available. It touches nothing else — the genuinely code-specific
`causes_en` / `symptoms_en` / `description_en` / `title_en` fields from the
same merge are KEPT.

Usage:
    python scripts/t66_revert_steps.py --report output/merge_t66_obdfyi.json [--apply]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DTC_DB = ROOT / "data" / "diagnostics" / "dtc_database.json"


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="output/merge_t66_obdfyi.json")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rep = json.loads(Path(args.report).read_text(encoding="utf-8"))
    targets = {
        a["key"]
        for a in rep["applied"]
        if "procedures_full" in a.get("fields", [])
    }
    print(f"records that received obdfyi procedures_full: {len(targets)}")

    db = json.loads(DTC_DB.read_text(encoding="utf-8"))
    removed = 0
    skipped_wrong_source = 0
    for code in sorted(targets):
        rec = db.get(code)
        if not isinstance(rec, dict):
            continue
        src = str(rec.get("procedures_source_url") or "")
        if "obdfyi.com" not in src:
            skipped_wrong_source += 1
            continue
        if "procedures_full" in rec:
            if args.apply:
                del rec["procedures_full"]
                rec.pop("procedures_source", None)
                rec.pop("procedures_source_url", None)
            removed += 1

    print(f"removed: {removed} | skipped (source is not obdfyi): {skipped_wrong_source}")

    if args.apply:
        shutil.copy2(DTC_DB, DTC_DB.with_suffix(DTC_DB.suffix + ".bak_t66b"))
        DTC_DB.write_text(json.dumps(db, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("new md5:", md5(DTC_DB).upper())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

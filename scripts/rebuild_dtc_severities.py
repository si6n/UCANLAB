#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild DTC severities in `data/diagnostics/dtc_database.json` from the
source-independent SAE J2012 rule table (P0-1, 2026-09-21 deep-discovery audit).

WHY
---
The stored `severity` was inherited from scraped source prose, which stamped
`CRITICAL_STOP` (-> drive_safety_policy RED -> "stop the vehicle and switch off
the engine") on single-cylinder misfires, HO2S heater circuits and wiper-relay
codes. The runtime already derives the rung from
`data/diagnostics/dtc_severity_rules.json` on load, so the cockpit is correct
today; this tool converges the ON-DISK data so the file, the audit and the
runtime agree.

WHAT IT DOES
------------
For every record, `severity` is replaced by the rule-table rung and the previous
value is preserved under `_source_severity` (auditable, reversible). Nothing else
is touched — no titles, no causes, no steps, no fabricated fields (AGENTS.md 2.3).

SAFETY
------
Dry-run by default. `--apply` writes an atomically-replaced file after making a
`.bak_p01` copy. Exit code 0 = no drift, 1 = drift found (dry run), 2 = error.

Usage:
    python scripts/rebuild_dtc_severities.py            # dry run, report drift
    python scripts/rebuild_dtc_severities.py --apply    # rewrite the file
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.engine.ai.severity_rules import is_obd_code, resolve_severity  # noqa: E402

DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--path", default=str(DTC_DB), help="path to dtc_database.json")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.is_file():
        print(f"ERROR: {target} not found", file=sys.stderr)
        return 2

    try:
        database = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {target}: {exc}", file=sys.stderr)
        return 2

    if not isinstance(database, dict):
        print("ERROR: dtc_database.json must be a JSON object", file=sys.stderr)
        return 2

    changed = 0
    before: dict[str, int] = {}
    after: dict[str, int] = {}

    for code, record in database.items():
        if not isinstance(record, dict):
            continue
        # Only OBD-II P/C/B/U codes are governed by this rule table; J1939 SPN
        # and proprietary keys keep their own severity semantics.
        if not is_obd_code(code):
            continue
        current = str(record.get("severity") or "")
        derived = resolve_severity(
            code,
            str(record.get("title") or ""),
            str(record.get("subsystem") or ""),
        ).value
        before[current] = before.get(current, 0) + 1
        after[derived] = after.get(derived, 0) + 1
        if derived != current:
            changed += 1
            if args.apply:
                if current:
                    record["_source_severity"] = current
                record["severity"] = derived

    print(f"records          : {len(database)}")
    print(f"changed          : {changed}")
    print(f"severity before  : {json.dumps(before, sort_keys=True)}")
    print(f"severity after   : {json.dumps(after, sort_keys=True)}")

    if not args.apply:
        print("\nDRY RUN — no file written. Re-run with --apply to converge the data.")
        return 1 if changed else 0

    backup = target.with_suffix(".json.bak_p01")
    backup.write_bytes(target.read_bytes())
    tmp = target.with_suffix(".json.tmp_p01")
    tmp.write_text(json.dumps(database, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)
    print(f"\nAPPLIED — backup at {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

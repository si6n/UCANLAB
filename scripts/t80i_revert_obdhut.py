"""T80i: revert the obdhut-misattributed `causes_en` content.

CONTEXT (measured 2026-09-26)
-----------------------------
obdhut.com is the cited source for `causes_en` on 4,179 codes. Of the 312
codes whose cached subtitle could be compared, 100 name a system DISJOINT from
the DB `title` (e.g. C0504: DB "Left Front Wheel Speed Sensor A
Intermittent/Erratic" vs obdhut "Steering Assist Control Solenoid Return
Circuit High").

Seven of those have an INDEPENDENT arbiter (the pinned OBDex corpus) that
confirms the DB `title` and contradicts obdhut. Zero have an arbiter
confirming obdhut. Those seven are reverted here.

REPAIR RULE (same as T80e): reverting means DELETING the obdhut-sourced
`causes_en` — an empty field is the correct state for unverified content
(AGENTS.md 2.3). The prior value's SHA-256 is preserved.

SAFETY: cross-process lock, SHA-256 + size CAS, `.bak_t80i` backup, count
frozen, idempotent, fail-closed.

Usage:
    python scripts/t80i_revert_obdhut.py            # dry-run
    python scripts/t80i_revert_obdhut.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DTC_DB = ROOT / "data" / "diagnostics" / "dtc_database.json"
LOCK_PATH = ROOT / "data" / "diagnostics" / ".t66_merge.lock"
LIST = ROOT / "output" / "t80_work" / "obdhut_arbitration.json"
REPORT = ROOT / "output" / "t80_work" / "obdhut_revert_report.json"
MARK = "obdhut_reverted_t80i"


def _load_merge_helpers():
    spec = importlib.util.spec_from_file_location(
        "t66_merge_helpers_i", ROOT / "scripts" / "t66_merge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256_text(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dtc-db", default=str(DTC_DB))
    ap.add_argument("--lock", default=str(LOCK_PATH))
    ap.add_argument("--list", default=str(LIST))
    ap.add_argument("--report", default=str(REPORT))
    args = ap.parse_args(argv)

    dtc_path = Path(args.dtc_db)
    merge_mod = _load_merge_helpers()
    data = json.loads(Path(args.list).read_text(encoding="utf-8"))
    candidates = data["confirm_db"]

    with merge_mod._file_lock(Path(args.lock)):
        raw_before = dtc_path.read_bytes()
        fingerprint = {"sha256": hashlib.sha256(raw_before).hexdigest(),
                       "size": len(raw_before)}
        dtc = json.loads(raw_before)
        count_before = len(dtc)

        applied, already, warnings = [], [], []
        for item in candidates:
            code = item["code"]
            entry = dtc.get(code)
            if not isinstance(entry, dict):
                warnings.append(f"{code}: not in DB — skipped")
                continue
            if entry.get(MARK):
                already.append(code)
                continue
            if "obdhut" not in str(entry.get("causes_en_source") or ""):
                warnings.append(f"{code}: causes_en no longer obdhut-sourced")
                continue
            removed = {"causes_en": entry.get("causes_en"),
                       "causes_en_source": entry.get("causes_en_source")}
            record = {"code": code, "votes": ["obdex_pinned"],
                      "arbiter": item.get("obdex"),
                      "backup_sha256": _sha256_text(removed)}
            if args.apply:
                entry.pop("causes_en", None)
                entry.pop("causes_en_source", None)
                entry[MARK] = True
                entry["obdhut_revert_evidence"] = "obdex_pinned (independent arbiter)"
                entry["obdhut_revert_backup"] = record["backup_sha256"]
                entry["obdhut_revert_reason"] = (
                    "subject conflict: obdhut page names a different system; "
                    "OBDex confirms the DB title")
            applied.append(record)

        if len(dtc) != count_before:
            raise SystemExit(
                f"RECORD COUNT CHANGED: {count_before}->{len(dtc)} — aborting")

        report = {
            "tur": "T80i",
            "dry_run": not args.apply,
            "db_sha256_before": fingerprint["sha256"],
            "db_count_before": count_before,
            "candidates": len(candidates),
            "applied": applied,
            "already_reverted": already,
            "warnings": warnings,
        }

        if args.apply and applied:
            merge_mod._verify_unchanged(dtc_path, fingerprint)
            backup = dtc_path.with_suffix(dtc_path.suffix + ".bak_t80i")
            shutil.copy2(dtc_path, backup)
            payload = json.dumps(dtc, ensure_ascii=False, indent=1) + "\n"
            tmp = dtc_path.with_suffix(dtc_path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(dtc_path)
            after = dtc_path.read_bytes()
            report["db_sha256_after"] = hashlib.sha256(after).hexdigest()
            report["db_count_after"] = len(json.loads(after))
            report["backup"] = str(backup)
        else:
            report["db_sha256_after"] = fingerprint["sha256"]

        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")

    print(json.dumps({
        "dry_run": report["dry_run"],
        "candidates": report["candidates"],
        "applied": len(report["applied"]),
        "already_reverted": len(report["already_reverted"]),
        "warnings": len(report["warnings"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

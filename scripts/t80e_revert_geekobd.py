"""T80e: revert the misattributed GeekOBD content from the DTC database.

THE DEFECT (measured 2026-09-26, T80d)
--------------------------------------
The T77b harvest merged GeekOBD `symptoms_en` into 6,330 codes and
`causes_en` into ~1,211. Its gate measured DISTINCTNESS only (0.9041 /
0.9995) — i.e. "every code got different text". It never asked whether the
text belongs to THAT code. Measured on the cached corpus:

  * 4,583 of 6,330 pages (72.4%) are about a DIFFERENT subject than the DB
    record (e.g. U011B: DB title "Lost Communication With Rocker Arm Control
    Module A", page "Lost Communication with Anti-lock Brake System (ABS)
    Control Module"); 99.7% of sampled pages are internally consistent, so the
    page is not garbled — it is consistently about another code.
  * 79 of those have an INDEPENDENT source (obdhut / icarsoft /
    theerrorcodes / openlaborproject, deconfounded against the DB's own
    citations) confirming the DB `title` layer.
  * 633 pages are merely VAGUE (no system named at all) — low value but not
    wrong; NOT touched.
  * 848 have a placeholder DB title — no anchor exists; NOT touched.

REPAIR RULE (no fabrication)
----------------------------
Reverting means DELETING the geekobd-sourced value, not replacing it. An
empty field is the correct state for unverified data (AGENTS.md 2.3: a
missing value stays empty). The prior value is preserved in `.bak_t80e`.

    symptoms_en            -> removed (only when geekobd-sourced)
    symptoms_en_source     -> removed
    causes_en              -> removed (only when geekobd-sourced)
    causes_en_source       -> removed
    geekobd_reverted_t80e  = True
    geekobd_revert_evidence= "<the confirming source(s)>"
    geekobd_revert_backup  = "<sha256 of the removed payload>"

SAFETY: same contract as t80_repair_title_en.py — cross-process lock, SHA-256
+ size CAS, `.bak_t80e` backup, count frozen, idempotent, fail-closed.

Usage:
    python scripts/t80e_revert_geekobd.py            # dry-run
    python scripts/t80e_revert_geekobd.py --apply
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
LIST = ROOT / "output" / "t80_work" / "geek_revert_list.json"
REPORT = ROOT / "output" / "t80_work" / "geek_revert_report.json"
MARK = "geekobd_reverted_t80e"


def _load_merge_helpers():
    spec = importlib.util.spec_from_file_location(
        "t66_merge_helpers_e", ROOT / "scripts" / "t66_merge.py")
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
    candidates = json.loads(Path(args.list).read_text(encoding="utf-8"))["revert"]

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
            fields = [f for f in item["fields"]
                      if "geekobd" in str(entry.get(f + "_source") or "")]
            if not fields:
                warnings.append(f"{code}: fields no longer geekobd-sourced — skipped")
                continue
            removed = {}
            for f in fields:
                removed[f] = entry.get(f)
                removed[f + "_source"] = entry.get(f + "_source")
            record = {"code": code, "fields": fields,
                      "votes": item["votes"],
                      "backup_sha256": _sha256_text(removed)}
            if args.apply:
                for f in fields:
                    entry.pop(f, None)
                    entry.pop(f + "_source", None)
                entry[MARK] = True
                entry["geekobd_revert_evidence"] = ", ".join(item["votes"])
                entry["geekobd_revert_backup"] = record["backup_sha256"]
            applied.append(record)

        if len(dtc) != count_before:
            raise SystemExit(
                f"RECORD COUNT CHANGED: {count_before}->{len(dtc)} — aborting")

        report = {
            "tur": "T80e",
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
            backup = dtc_path.with_suffix(dtc_path.suffix + ".bak_t80e")
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

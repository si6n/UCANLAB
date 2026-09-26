"""T80b: mark the remaining `title_en` shift suspects with a review flag.

CONTEXT
-------
`scripts/t80_repair_title_en.py` aligned 56 codes whose shifted `title_en` was
confirmed by >= 2 independent sources. 211 records still carry the shift
signature (`title_en` == another record's `title`, != its own) but lack that
evidence bar — mostly the P family whose cited pages were not reachable for
live verification.

This script applies the T79 report's recommendation #1: MARK them, do not
silently change them. The flag is additive only — no value is overwritten,
so the change is reversible and cannot lose data.

    title_en_shift_suspect      = True
    title_en_shift_other_code   = the code whose `title` this text matches
    title_en_shift_flagged_t80b = True

SAFETY: same contract as t80_repair_title_en.py — cross-process lock, SHA-256
+ size CAS, `.bak_t80b` backup, count frozen, idempotent, fail-closed.

Usage:
    python scripts/t80b_flag_shift_suspects.py            # dry-run
    python scripts/t80b_flag_shift_suspects.py --apply
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
REPORT = ROOT / "output" / "t80_work" / "flag_shift_report.json"


def _load_merge_helpers():
    spec = importlib.util.spec_from_file_location(
        "t66_merge_helpers_b", ROOT / "scripts" / "t66_merge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dtc-db", default=str(DTC_DB))
    ap.add_argument("--lock", default=str(LOCK_PATH))
    ap.add_argument("--report", default=str(REPORT))
    args = ap.parse_args(argv)

    dtc_path = Path(args.dtc_db)
    merge_mod = _load_merge_helpers()

    with merge_mod._file_lock(Path(args.lock)):
        raw_before = dtc_path.read_bytes()
        fingerprint = {"sha256": _sha256(raw_before), "size": len(raw_before)}
        dtc = json.loads(raw_before)
        count_before = len(dtc)

        # one normalized title index (same normalization the merge guard uses)
        index: dict[str, list[str]] = {}
        for code, entry in dtc.items():
            if not isinstance(entry, dict):
                continue
            k = merge_mod._title_norm_key(entry.get("title"))
            if k:
                index.setdefault(k, []).append(code)

        flagged: list[dict] = []
        already: list[str] = []
        for code, entry in dtc.items():
            if not isinstance(entry, dict):
                continue
            te = entry.get("title_en")
            if not te:
                continue
            kte = merge_mod._title_norm_key(te)
            if not kte or kte == merge_mod._title_norm_key(entry.get("title")):
                continue
            others = [c for c in index.get(kte, []) if c != code]
            if not others:
                continue
            if entry.get("title_en_shift_flagged_t80b"):
                already.append(code)
                continue
            if args.apply:
                entry["title_en_shift_suspect"] = True
                entry["title_en_shift_other_code"] = others[0]
                entry["title_en_shift_flagged_t80b"] = True
            flagged.append({"code": code, "matches": others[0],
                            "title_en": te, "title": entry.get("title")})

        if len(dtc) != count_before:
            raise SystemExit(
                f"RECORD COUNT CHANGED: {count_before}->{len(dtc)} — aborting")

        report = {
            "tur": "T80b",
            "dry_run": not args.apply,
            "db_sha256_before": fingerprint["sha256"],
            "db_count_before": count_before,
            "flagged_candidates": len(flagged),
            "already_flagged": len(already),
            "flagged": flagged,
        }

        if args.apply and flagged:
            merge_mod._verify_unchanged(dtc_path, fingerprint)
            backup = dtc_path.with_suffix(dtc_path.suffix + ".bak_t80b")
            shutil.copy2(dtc_path, backup)
            payload = json.dumps(dtc, ensure_ascii=False, indent=1) + "\n"
            tmp = dtc_path.with_suffix(dtc_path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(dtc_path)
            after_raw = dtc_path.read_bytes()
            report["db_sha256_after"] = _sha256(after_raw)
            report["backup"] = str(backup)
            report["db_count_after"] = len(json.loads(after_raw))
        else:
            report["db_sha256_after"] = fingerprint["sha256"]

        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")

    print(json.dumps({
        "dry_run": report["dry_run"],
        "flagged_candidates": report["flagged_candidates"],
        "already_flagged": report["already_flagged"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

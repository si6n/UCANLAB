"""T80k: close the J1939 `description_en_source` naming gap.

THE DEFECT (measured 2026-09-26)
--------------------------------
2,845 SPN records carry `description_en` but no `description_en_source`, so a
consumer following the field-name convention cannot find the provenance. The
provenance IS present — under the source-specific keys `_source_ref_sitrak`
and `_source_license_sitrak`:

    _source_ref_sitrak     = https://github.com/STAS63-bit/sitrak-error-codes/
                             blob/fdb0c0d9.../error...
    _source_license_sitrak = CC-BY-4.0
    evidence_url           = https://github.com/STAS63-bit/sitrak-error-codes

This is a NAMING inconsistency, not missing data. The fix copies the existing
reference into the conventional field so every reader finds it.

NO FABRICATION: the value written is the URL already on the record; nothing is
invented. Idempotent; count frozen.

Usage:
    python scripts/t80k_j1939_desc_source.py            # dry-run
    python scripts/t80k_j1939_desc_source.py --apply
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
J1939_DB = ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
LOCK_PATH = ROOT / "data" / "diagnostics" / ".t66_merge.lock"
REPORT = ROOT / "output" / "t80_work" / "j1939_desc_source_report.json"
MARK = "description_en_source_backfilled_t80k"


def _load_merge_helpers():
    spec = importlib.util.spec_from_file_location(
        "t66_merge_helpers_k", ROOT / "scripts" / "t66_merge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(J1939_DB))
    ap.add_argument("--lock", default=str(LOCK_PATH))
    ap.add_argument("--report", default=str(REPORT))
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    merge_mod = _load_merge_helpers()

    with merge_mod._file_lock(Path(args.lock)):
        raw_before = db_path.read_bytes()
        fingerprint = {"sha256": hashlib.sha256(raw_before).hexdigest(),
                       "size": len(raw_before)}
        data = json.loads(raw_before)
        spns = data.get("spns")
        if not isinstance(spns, dict):
            raise SystemExit("unexpected J1939 shape (spns is not a dict) — aborting")
        count_before = len(spns)

        applied, already, warnings = [], [], []
        for key, entry in spns.items():
            if not isinstance(entry, dict):
                continue
            if not entry.get("description_en"):
                continue
            if entry.get("description_en_source"):
                continue
            if entry.get(MARK):
                already.append(key)
                continue
            ref = (entry.get("_source_ref_sitrak")
                   or entry.get("evidence_url")
                   or entry.get("source_url"))
            if not ref:
                warnings.append(f"{key}: no provenance on the record — skipped")
                continue
            if args.apply:
                entry["description_en_source"] = ref
                if entry.get("_source_license_sitrak"):
                    entry["description_en_license"] = entry["_source_license_sitrak"]
                entry[MARK] = True
            applied.append({"key": key, "source": str(ref)[:120]})

        if len(spns) != count_before:
            raise SystemExit(
                f"RECORD COUNT CHANGED: {count_before}->{len(spns)} — aborting")

        report = {
            "tur": "T80k",
            "dry_run": not args.apply,
            "db_sha256_before": fingerprint["sha256"],
            "spn_count_before": count_before,
            "applied": applied,
            "already_backfilled": already,
            "warnings": warnings,
        }

        if args.apply and applied:
            merge_mod._verify_unchanged(db_path, fingerprint)
            backup = db_path.with_suffix(db_path.suffix + ".bak_t80k")
            shutil.copy2(db_path, backup)
            payload = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
            tmp = db_path.with_suffix(db_path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(db_path)
            after = db_path.read_bytes()
            report["db_sha256_after"] = hashlib.sha256(after).hexdigest()
            report["backup"] = str(backup)
        else:
            report["db_sha256_after"] = fingerprint["sha256"]

        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")

    print(json.dumps({
        "dry_run": report["dry_run"],
        "applied": len(report["applied"]),
        "already": len(report["already_backfilled"]),
        "warnings": len(report["warnings"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

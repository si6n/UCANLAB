"""T80: repair the shifted `title_en` layer of dtc_database.json.

THE DEFECT (measured 2026-09-26 on the shipped DB, 14,484 records)
------------------------------------------------------------------
217 codes carry a `title_en` that equals ANOTHER record's `title` and differs
from their own meaning — a source-side off-by-N shift reproduced by every
scraped source (openlaborproject 45, OBDex 92, obdhut 67, theerrorcodes 6,
troublecodes 4, carberry 3). Two independent evidence paths qualify a code for
repair (pre-registered in output/t80_work/verdict_framework_t80.md):

  (a) AUDIT-VERIFIED — output/t79_work/oem_audit/final_classification_v2.json
      classifies the record as "DB error (EN layer)" with >= 2 independent
      source votes for the `title` layer (icarsoft / theerrorcodes / obdhut /
      OBDex pinli). 56 codes.
  (b) LIVE-VERIFIED — the P-family live check (subagent) found the cited page
      itself shifted, or the page confirms the `title` layer.

REPAIR RULE (no fabrication)
----------------------------
The repaired value is the record's OWN `title` — already an English string,
confirmed by independent sources. Nothing is invented; the wrong layer is
aligned to the right one. Provenance is preserved:

    title_en            <- own `title`
    title_en_prev       <- the shifted value (audit trail)
    title_en_prev_source<- its cited URL
    title_en_repaired_t80 = True
    title_en_source     <- "T80 repair: DB title layer (votes: <sources>)"

SAFETY CONTRACT (mirrors scripts/t66_merge.py)
----------------------------------------------
  * cross-process exclusive lock (data/diagnostics/.t66_merge.lock) held over
    the whole read-modify-write — never races a concurrent merge;
  * SHA-256 + size compare-and-swap: abort if the file moved since it was read;
  * `.bak_t80` backup before the write, atomic tmp + replace;
  * record count FROZEN (abort if it would move);
  * idempotent: a second run finds every code already marked and writes nothing
    (byte-identical file);
  * fail-closed: any unexpected shape (record missing, title changed under us)
    skips that code with a warning and never writes a guessed value.

Usage:
    python scripts/t80_repair_title_en.py            # dry-run (default)
    python scripts/t80_repair_title_en.py --apply    # write
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DTC_DB = ROOT / "data" / "diagnostics" / "dtc_database.json"
LOCK_PATH = ROOT / "data" / "diagnostics" / ".t66_merge.lock"
AUDIT = ROOT / "output" / "t79_work" / "oem_audit" / "final_classification_v2.json"
LIVE = ROOT / "output" / "t80_work" / "p_live_verify.json"
REPORT = ROOT / "output" / "t80_work" / "repair_title_en_report.json"
MARK = "title_en_repaired_t80"


def _load_merge_helpers():
    """Reuse the audited lock / CAS helpers instead of re-implementing them."""
    spec = importlib.util.spec_from_file_location(
        "t66_merge_helpers", ROOT / "scripts" / "t66_merge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# T80g: a title that describes the CODE CLASS rather than the fault is not an
# authoritative meaning. Aligning title_en to it would replace real source
# content with boilerplate (measured: P0DFC/P0E78).
_PLACEHOLDER_TITLE = re.compile(
    r"generic \(sae|reserved|manufacturer-specific|body dtc code|"
    r"iso/sae reserved",
    re.I,
)


def _is_placeholder_title(title: str) -> bool:
    return bool(_PLACEHOLDER_TITLE.search(str(title or "")))


def build_repair_list(dtc: dict, audit: dict, live: list | None) -> tuple[list[dict], list[dict], list[str]]:
    """Return (repairs, flags, warnings).

    repairs: codes with >=2-vote audit evidence or live-verified evidence.
    flags:   codes with exactly 1 vote -> `title_en_unverified: true` only.
    """
    repairs: list[dict] = []
    flags: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()

    # ---- (a) audit-verified -------------------------------------------------
    for row in audit.get("rows", []):
        code = row.get("code")
        if not code or code in seen:
            continue
        cls = row.get("class")
        votes = list((row.get("votes_title") or {}).keys())
        entry = dtc.get(code)
        if not isinstance(entry, dict):
            warnings.append(f"{code}: not in DB — skipped")
            continue
        if cls == "DB error (TR layer)":
            # C0516/C0564: `title` is the placeholder, `title_en` is right.
            # Repairing title_en here would DESTROY correct data (K8).
            continue
        if cls != "DB error (EN layer)":
            continue
        own_title = str(entry.get("title") or "")
        if own_title != str(row.get("title") or ""):
            warnings.append(
                f"{code}: DB title changed since the audit "
                f"({own_title[:40]!r} != {row['title'][:40]!r}) — skipped")
            continue
        if _is_placeholder_title(own_title):
            # T80g: a placeholder DB title ("Generic (SAE-defined)...") is not
            # an authoritative meaning, so aligning title_en to it would
            # REPLACE a real source meaning with boilerplate. Fail closed.
            warnings.append(f"{code}: DB title is a placeholder — skipped")
            continue
        if len(votes) >= 2:
            repairs.append({"code": code, "evidence": "audit", "votes": votes})
            seen.add(code)
        elif len(votes) == 1:
            flags.append({"code": code, "votes": votes})
            seen.add(code)

    # ---- (b) live-verified --------------------------------------------------
    for row in (live or []):
        code = row.get("code")
        if not code or code in seen:
            continue
        if row.get("verdict") not in ("source_shifted", "db_title_correct"):
            continue
        entry = dtc.get(code)
        if not isinstance(entry, dict):
            warnings.append(f"{code}: not in DB — skipped (live)")
            continue
        repairs.append({"code": code, "evidence": "live",
                        "votes": [f"live:{row.get('cited_url', '')}"]})
        seen.add(code)

    return repairs, flags, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the repair (default: dry-run)")
    ap.add_argument("--dtc-db", default=str(DTC_DB))
    ap.add_argument("--lock", default=str(LOCK_PATH))
    ap.add_argument("--report", default=str(REPORT))
    args = ap.parse_args(argv)

    dtc_path = Path(args.dtc_db)
    merge_mod = _load_merge_helpers()

    audit = json.loads(AUDIT.read_text(encoding="utf-8")) if AUDIT.exists() else {}
    live = None
    if LIVE.exists():
        live = json.loads(LIVE.read_text(encoding="utf-8"))
        if isinstance(live, dict):
            live = live.get("rows") or live.get("records")

    with merge_mod._file_lock(Path(args.lock)):
        raw_before = dtc_path.read_bytes()
        fingerprint = {"sha256": _sha256(raw_before), "size": len(raw_before)}
        dtc = json.loads(raw_before)
        count_before = len(dtc)

        repairs, flags, warnings = build_repair_list(dtc, audit, live)

        applied: list[dict] = []
        already: list[str] = []
        for item in repairs:
            code = item["code"]
            entry = dtc[code]
            if entry.get(MARK):
                already.append(code)
                continue
            if entry.get("title_en_repair_skipped_t80g"):
                # T80g: this code was already adjudicated as unrepairable
                # (placeholder title). Re-running must not undo that decision.
                warnings.append(f"{code}: previously skipped (placeholder) — skipped")
                continue
            own_title = str(entry.get("title") or "")
            prev = entry.get("title_en")
            if not own_title:
                warnings.append(f"{code}: own title empty — skipped (fail-closed)")
                continue
            if _is_placeholder_title(own_title):
                warnings.append(f"{code}: DB title is a placeholder — skipped")
                continue
            if isinstance(prev, str) and prev.strip() == own_title.strip():
                warnings.append(f"{code}: title_en already equals title — skipped")
                continue
            record = {
                "code": code,
                "evidence": item["evidence"],
                "votes": item["votes"],
                "title_en_prev": prev,
                "title_en_prev_source": entry.get("title_en_source"),
                "title_en_new": own_title,
            }
            if args.apply:
                entry["title_en"] = own_title
                entry["title_en_prev"] = prev
                entry["title_en_prev_source"] = entry.get("title_en_source")
                entry[MARK] = True
                # T46 contract: `title_en_source` must stay an https:// URL
                # (tests/unit/test_t46_merge.py). The confirming votes go in
                # their own field instead of overwriting the source URL.
                entry["title_en_repair_votes"] = ", ".join(item["votes"])
            applied.append(record)

        flagged: list[str] = []
        for item in flags:
            code = item["code"]
            entry = dtc[code]
            if entry.get("title_en_unverified"):
                continue
            if args.apply:
                entry["title_en_unverified"] = True
                entry["title_en_unverified_reason"] = (
                    "T80: single independent source vote — flagged, not repaired"
                )
            flagged.append(code)

        # ---- integrity gates -------------------------------------------------
        if len(dtc) != count_before:
            raise SystemExit(
                f"RECORD COUNT CHANGED: {count_before}->{len(dtc)} — aborting")

        report = {
            "tur": "T80",
            "dry_run": not args.apply,
            "db_sha256_before": fingerprint["sha256"],
            "db_count_before": count_before,
            "repair_candidates": len(repairs),
            "flag_candidates": len(flags),
            "already_repaired": already,
            "applied": applied,
            "flagged": flagged,
            "warnings": warnings,
        }

        if args.apply and (applied or flagged):
            merge_mod._verify_unchanged(dtc_path, fingerprint)
            backup = dtc_path.with_suffix(dtc_path.suffix + ".bak_t80")
            shutil.copy2(dtc_path, backup)
            payload = json.dumps(dtc, ensure_ascii=False, indent=1) + "\n"
            tmp = dtc_path.with_suffix(dtc_path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(dtc_path)
            after_raw = dtc_path.read_bytes()
            report["db_sha256_after"] = _sha256(after_raw)
            report["backup"] = str(backup)
            report["db_count_after"] = len(json.loads(after_raw))
            if report["db_count_after"] != count_before:
                raise SystemExit("POST-WRITE COUNT MISMATCH — restoring")
        else:
            report["db_sha256_after"] = fingerprint["sha256"]

        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")

    print(json.dumps({
        "dry_run": report["dry_run"],
        "repair_candidates": report["repair_candidates"],
        "flag_candidates": report["flag_candidates"],
        "already_repaired": len(report["already_repaired"]),
        "applied": len(report["applied"]),
        "flagged": len(report["flagged"]),
        "warnings": len(report["warnings"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

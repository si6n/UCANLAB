#!/usr/bin/env python
"""TUR 63 — Merge harvested & verified diagnostics into the DBs.

Single-writer merge tool (orchestrator-owned). Applies ONLY records that
carry code-specific, evidence-backed content and targets a currently EMPTY
field. Existing data is never overwritten. Placeholders are always skipped.

Usage:
    python scripts/t63_merge.py --inputs output/scan_t63_a.json output/scan_t63_b.json \
        --report output/merge_t63_report.json [--apply]

Without --apply it runs in dry-run mode and only reports the impact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
J1939_DB = ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
DTC_DB = ROOT / "data" / "diagnostics" / "dtc_database.json"

# Only these DB fields may be filled by a merge (gap targets).
DTC_FILLABLE = ("symptoms", "procedures_full", "solutions", "related_codes")
J1939_FILLABLE = ("symptoms", "diagnostic_steps", "procedures_full")

MIN_TEXT = 30  # shorter than this is treated as noise/placeholder

# A field counts as "already populated" only if it holds real content; a value
# below the field's minimum is a stub and may be upgraded by a richer harvest.
FIELD_MIN_LEN = {
    "procedures_full": 1,     # list of structured bundles
    "diagnostic_steps": 1,    # list of step strings
    "symptoms": 1,            # list of symptom strings
    "solutions": 1,
    "related_codes": 1,
    "procedures_steps": 1,
}


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _field_filled(value, field: str) -> bool:
    """True when the existing value is substantive enough to protect."""
    if value is None:
        return False
    if isinstance(value, str):
        if not value.strip():
            return False
        return len(value.strip()) >= FIELD_MIN_LEN.get(field, MIN_TEXT)
    if isinstance(value, (list, tuple, dict)):
        return len(value) >= 1
    return True


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return len(value.strip()) < MIN_TEXT
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _normalize(value):
    """Coerce harvested value into the DB's expected shape."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        out = [str(x).strip() for x in value if str(x).strip()]
        return out
    return value


def _placeholder(rec: dict) -> bool:
    if rec.get("placeholder") is True:
        return True
    # A record needs at least one substantive field and an evidence string.
    has_content = any(
        not _is_blank(rec.get(f))
        for f in ("symptoms", "causes", "steps", "procedures_full", "procedures_steps")
    )
    return not has_content


def _evidence_ok(rec: dict) -> bool:
    ev = rec.get("evidence")
    if not ev:
        return False
    if isinstance(ev, str):
        return len(ev.strip()) >= 20
    return bool(ev)


def key_for_j1939(rec: dict) -> str | None:
    if rec.get("key"):
        key = str(rec["key"])
        # Harvests are per (SPN, FMI); the DB is keyed by SPN only.
        m = re.match(r"SPN_(\d+)(?:_FMI_\d+)?$", key)
        if m:
            return f"SPN_{m.group(1)}"
        return key
    spn = rec.get("spn")
    if spn is not None:
        return f"SPN_{int(spn)}"
    return None


def key_for_dtc(rec: dict) -> str | None:
    # The source URL filename is authoritative: a harvest whose `code` disagrees
    # with its own URL is corrupt, so recover the code from the URL when present.
    url = rec.get("url") or ""
    m = re.search(r"([pbcu][0-9a-f]{4})", url, re.I)
    if m:
        return m.group(1).upper()
    code = rec.get("key") or rec.get("code")
    return str(code).upper() if code else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--report", default="output/merge_t63_report.json")
    ap.add_argument("--apply", action="store_true", help="actually write the DBs")
    args = ap.parse_args()

    j1939 = json.loads(J1939_DB.read_text(encoding="utf-8"))
    dtc = json.loads(DTC_DB.read_text(encoding="utf-8"))
    spns: dict = j1939["spns"]

    report = {
        "tur": "T63",
        "dry_run": not args.apply,
        "db_md5_before": {"j1939": md5(J1939_DB), "dtc": md5(DTC_DB)},
        "inputs": [],
        "applied": [],
        "skipped": {"placeholder": 0, "no_evidence": 0, "unknown_key": 0, "already_full": 0, "no_fillable": 0},
        "totals": {"j1939_filled": 0, "dtc_filled": 0, "j1939_new": 0, "dtc_new": 0},
    }

    for inp in args.inputs:
        path = Path(inp)
        if not path.exists():
            report["inputs"].append({"path": inp, "error": "missing"})
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("records") if isinstance(data, dict) else data
        if not records:
            report["inputs"].append({"path": inp, "records": 0, "note": "no records array"})
            continue
        summary = {"path": inp, "records": len(records), "target_db": data.get("target_db", "auto"),
                   "filled": 0, "new": 0}
        for rec in records:
            if _placeholder(rec):
                report["skipped"]["placeholder"] += 1
                continue
            if not _evidence_ok(rec):
                report["skipped"]["no_evidence"] += 1
                continue

            # Decide target DB: per-record wins, then file-level, then key shape.
            jkey = key_for_j1939(rec)
            dkey = key_for_dtc(rec)
            target = rec.get("target_db") or data.get("target_db")
            if target in (None, "auto"):
                if jkey and rec.get("spn") is not None:
                    target = "j1939"
                elif dkey and dkey[:1] in "PBCU":
                    target = "dtc"
                else:
                    target = "j1939" if jkey else "dtc"

            if target == "j1939":
                if not jkey:
                    report["skipped"]["unknown_key"] += 1
                    continue
                if jkey not in spns:
                    report["skipped"]["unknown_key"] += 1
                    continue
                entry = spns[jkey]
                fillable = J1939_FILLABLE
            else:
                if not dkey or dkey not in dtc:
                    report["skipped"]["unknown_key"] += 1
                    continue
                entry = dtc[dkey]
                fillable = DTC_FILLABLE

            filled_fields = []
            replaced_stubs = {}
            appended = 0

            # 0) DTC harvests carry step lists as `procedures_steps`; the DTC DB
            #    has no `procedures_full` today, so persist it as `procedures_full`
            #    (a list of step strings) only when that field is absent/empty.
            if target == "dtc" and rec.get("procedures_steps"):
                steps = _normalize(rec["procedures_steps"])
                if not _is_blank(steps) and _is_blank(entry.get("procedures_full")):
                    if args.apply:
                        entry["procedures_full"] = steps
                        entry.setdefault("procedures_source", rec.get("source"))
                        entry.setdefault(
                            "procedures_source_url",
                            rec.get("evidence_url") or rec.get("url"),
                        )
                    filled_fields.append("procedures_full")

            # 1) Structured procedure bundles are APPENDED (per-FMI detail is
            #    deduplicated by source_url), never overwriting existing entries.
            bundle = rec.get("procedures_full_append")
            if isinstance(bundle, dict) and target == "j1939":
                existing = entry.get("procedures_full")
                if not isinstance(existing, list):
                    existing = []
                seen = {str(b.get("source_url")) for b in existing if isinstance(b, dict)}
                if str(bundle.get("source_url")) not in seen and str(bundle.get("source_url")) != "None":
                    if args.apply:
                        existing.append(bundle)
                        entry["procedures_full"] = existing
                        entry.setdefault("procedures_source", rec.get("source"))
                        entry.setdefault("procedures_license", "belirsiz")
                        entry.setdefault("procedures_match_key", "spn_fmi")
                    appended += 1

            # 2) Flat list/string fields: only fill when the target is empty,
            #    or the new value is strictly richer than an existing stub.
            for field in fillable:
                val = _normalize(rec.get(field))
                if _is_blank(val):
                    continue
                existing = entry.get(field)
                if isinstance(val, list) and isinstance(existing, list) and existing:
                    # Union of strings, preserving existing order.
                    if all(isinstance(x, str) for x in val + existing):
                        merged_list = existing + [x for x in val if x not in existing]
                        if len(merged_list) > len(existing):
                            if args.apply:
                                entry[field] = merged_list
                            filled_fields.append(field)
                            continue
                    continue
                if _field_filled(existing, field):
                    continue
                if existing is not None:
                    replaced_stubs[field] = existing
                if args.apply:
                    entry[field] = val
                    entry.setdefault("evidence_url", rec.get("url"))
                    entry.setdefault("source", rec.get("source"))
                filled_fields.append(field)

            if not filled_fields and not appended:
                report["skipped"]["already_full"] += 1
                continue
            summary["filled"] += 1
            if target == "j1939":
                report["totals"]["j1939_filled"] += 1
            else:
                report["totals"]["dtc_filled"] += 1
            report["applied"].append({"key": jkey or dkey, "db": target,
                                      "fields": filled_fields, "url": rec.get("url"),
                                      "procedures_appended": appended,
                                      "replaced_stubs": replaced_stubs})
        report["inputs"].append(summary)

    if args.apply:
        spns_meta = j1939.get("metadata", {})
        spns_meta["total_spns"] = len(spns)
        J1939_DB.write_text(
            json.dumps(j1939, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        DTC_DB.write_text(
            json.dumps(dtc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        report["db_md5_after"] = {"j1939": md5(J1939_DB), "dtc": md5(DTC_DB)}
        report["db_counts_after"] = {"j1939_spns": len(spns), "dtc": len(dtc)}

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))
    print("skipped:", report["skipped"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

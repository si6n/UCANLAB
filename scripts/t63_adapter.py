#!/usr/bin/env python
"""TUR 63 — convert raw harvests into merge-ready records for the DB schemas.

Two real DB shapes are produced here:

  J1939 `procedures_full` is a LIST of structured dicts, e.g.
      [{"overview": "...", "severity": "Serious", "severity_action": "...",
        "causes": [...], "first_moves": [...], "common_misdiagnoses": [...],
        "fmi": "0", "source_url": "...", "source_site": "...",
        "quality": "high", "method": "free"}]

  J1939 `symptoms` / `diagnostic_steps` / `causes` are flat lists of strings.
  DTC `symptoms` is a list of strings; DTC `procedures_full` is not yet in use,
  so DTC harvests write `symptoms` (+ `title_en` evidence).

Usage:
    python scripts/t63_adapter.py --in output/scan_t63_X.json --out output/merge_ready_X.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "data" / "diagnostics"


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    return [str(x).strip() for x in v if str(x).strip()]


def _join_steps(steps) -> str:
    parts = _as_list(steps)
    return " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.src).read_text(encoding="utf-8"))
    meta = data.get("_meta", {})
    src_site = meta.get("source", "unknown")
    quality = meta.get("quality", "high")
    raw = data.get("records") or []

    j1939 = json.loads((DB_DIR / "j1939_spn_fmi_database.json").read_text(encoding="utf-8"))["spns"]
    dtc = json.loads((DB_DIR / "dtc_database.json").read_text(encoding="utf-8"))

    out = []
    stats = {"input": len(raw), "no_key": 0, "j1939": 0, "j1939_unknown": 0,
             "dtc": 0, "dtc_unknown": 0, "accepted": 0, "thin": 0}

    for rec in raw:
        spn, code, url = rec.get("spn"), rec.get("code"), rec.get("url")
        if spn is None and not code:
            stats["no_key"] += 1
            continue

        symptoms = _as_list(rec.get("symptoms"))
        causes = _as_list(rec.get("causes"))
        steps = _as_list(rec.get("steps"))
        severity = rec.get("severity") or meta.get("severity")

        if spn is None and code:
            key = str(code).upper()
            if key not in dtc:
                stats["dtc_unknown"] += 1
                continue
            stats["dtc"] += 1
            if not symptoms and not steps:
                stats["thin"] += 1
                continue
            item = {"target_db": "dtc", "key": key, "code": key, "url": url,
                    "source": src_site, "placeholder": False,
                    "evidence": rec.get("evidence") or (symptoms or steps)[0][:200]}
            if symptoms:
                item["symptoms"] = symptoms
            if steps:
                item["procedures_steps"] = steps
            out.append(item)
            stats["accepted"] += 1
            continue

        key = f"SPN_{int(spn)}"
        if key not in j1939:
            stats["j1939_unknown"] += 1
            continue
        stats["j1939"] += 1

        item = {"target_db": "j1939", "key": key, "spn": int(spn), "fmi": rec.get("fmi"),
                "url": url, "source": src_site, "placeholder": False}
        wrote = False
        if symptoms:
            item["symptoms"] = symptoms
            wrote = True
        if steps:
            item["diagnostic_steps"] = steps
            wrote = True
        if causes:
            item["causes"] = causes
            wrote = True
        if steps or causes or symptoms:
            # Structured procedures_full bundle (append semantics, handled by merge tool).
            bundle = {
                "overview": (rec.get("title") or "").strip(),
                "severity": severity,
                "severity_action": rec.get("severity_action"),
                "causes": causes,
                "first_moves": steps,
                "common_misdiagnoses": _as_list(rec.get("common_misdiagnoses")),
                "fmi": str(rec.get("fmi")) if rec.get("fmi") is not None else None,
                "source_title": rec.get("title"),
                "source_url": url,
                "source_site": src_site,
                "quality": quality,
                "method": "free",
            }
            item["procedures_full_append"] = bundle
            wrote = True
        if not wrote:
            stats["thin"] += 1
            continue
        item["evidence"] = rec.get("evidence") or (steps or causes or symptoms)[0][:200]
        out.append(item)
        stats["accepted"] += 1

    payload = {"tur": "T63", "adapter": "t63_adapter.py", "source": src_site,
               "source_file": args.src, "stats": stats, "records": out}
    Path(args.dst).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    print(f"wrote {args.dst} ({len(out)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

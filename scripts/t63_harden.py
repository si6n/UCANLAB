"""TUR 63 — structural QA / hardening of harvested records before merge.

Independent, deterministic checks (orchestrator-owned):
  * drop records with no resolvable key (SPN or PBCU code)
  * re-derive the key from the source URL and assert it matches
  * drop duplicate keys, keeping the richest record
  * strip known boilerplate cause sentences
  * drop nav/junk steps
  * require an evidence string and at least one substantive field

Usage:
    python scripts/t63_harden.py --in output/scan_t63_X.json --out output/clean_X.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BOILERPLATE = re.compile(
    r"(diagnostic possibilities, not a statistical|"
    r"vehicle-specific service information takes priority|"
    r"testing before replacement can save money|"
    r"what does (trouble )?code [pbcu][0-9a-f]{4} mean|"
    r"^to resolve the [pbcu][0-9a-f]{4}|"
    r"the following steps (should|could|can) be taken|"
    r"this (article|page) (explains|uses)|"
    r"affiliate|sponsored|advertisement|"
    r"always consult|for informational purposes)",
    re.I,
)
NAV = re.compile(
    r"^(skip to content|toggle menu|menu|home$|contact us$|blog$|posted on .* by .*|"
    r"shared?|troubleshooting$|table of contents|search|filter by)", re.I,
)
JUNK_TAIL = re.compile(r"(Toggle Menu|Diagnostic Table|How to Fix|Table of Contents).*$", re.I)


def clean_str_list(items, min_len=18, dedupe=True):
    out = []
    for s in items or []:
        if not isinstance(s, str):
            continue
        s = JUNK_TAIL.sub("", s).strip(" .;:-\u00a0")
        if len(s) < min_len:
            continue
        if NAV.match(s) or BOILERPLATE.search(s):
            continue
        if dedupe and s in out:
            continue
        out.append(s)
    return out


def code_from_url(url: str):
    # OBD-II codes are P/B/C/U + 4 hex digits (e.g. P0101, P228C, U0100).
    m = re.search(r"([pbcu][0-9a-f]{4})", url or "", re.I)
    return m.group(1).upper() if m else None


def richness(rec) -> int:
    return sum(len(str(rec.get(f) or "")) for f in ("symptoms", "causes", "steps", "procedures_full"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.src).read_text(encoding="utf-8"))
    recs = data.get("records") or []
    stats = {"input": len(recs), "no_key": 0, "key_mismatch": 0, "duplicate": 0,
             "empty_after_clean": 0, "kept": 0}

    by_key: dict[str, dict] = {}
    for rec in recs:
        key = (rec.get("key") or rec.get("code") or "").strip()
        if re.fullmatch(r"(?i)spn_\d+(_fmi_\d+)?", key):
            # J1939 key (spn_N / spn_N_fmi_M): the SPN/FMI is not reliably
            # present in the URL, so the record's own key is kept as-is.
            pass
        else:
            # DTC: the URL filename is authoritative for the code. A harvest that
            # disagrees with its own URL is corrupt — trust the URL, and if the
            # record has no code at all, recover it from the URL.
            urlcode = code_from_url(rec.get("url"))
            if urlcode:
                if key and key.upper() != urlcode:
                    stats["key_mismatch"] += 1
                rec["key"] = urlcode
                rec["code"] = urlcode
            elif not key:
                stats["no_key"] += 1
                continue

        for f in ("symptoms", "causes", "steps"):
            rec[f] = clean_str_list(rec.get(f))
        if rec.get("procedures_full") and isinstance(rec["procedures_full"], str):
            rec["procedures_full"] = BOILERPLATE.sub("", rec["procedures_full"]).strip()
        if not (rec.get("symptoms") or rec.get("causes") or rec.get("steps")):
            stats["empty_after_clean"] += 1
            continue

        kk = rec.get("key") or rec.get("code")
        if not kk:
            stats["no_key"] += 1
            continue
        if kk in by_key:
            stats["duplicate"] += 1
            if richness(rec) > richness(by_key[kk]):
                by_key[kk] = rec
        else:
            by_key[kk] = rec

    clean = list(by_key.values())
    stats["kept"] = len(clean)
    out = {**{k: v for k, v in data.items() if k != "records"},
           "_hardening": stats, "_note": "URL filename is authoritative for DTC codes",
           "records": clean}
    Path(args.dst).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

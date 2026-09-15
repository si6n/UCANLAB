"""T46-A — Merge the T45-verified English DTC harvest into the local DB.

Task: kanban t_edda7bef (T46-A).

Inputs (validated in T45):
  * ``spn_gap_hunter/output/t45c_tcn_symptoms.json``  — troublecodes.net, 617
    records (336 unique codes, 281 duplicate rows). Real English symptoms,
    causes, meaning + page title.
  * ``spn_gap_hunter/output/t45c_dtc_symptoms.json``  — obdhut.com, 4515 records
    (4515 unique codes). ``symptoms`` was emptied upstream (it duplicated
    ``causes``); the usable payload is ``meaning`` + ``causes``.

Target DB: ``data/diagnostics/dtc_database.json`` (14,352 records, dict keyed
by DTC code).

LOCKED RULES (T46 task body — violation = task rejection)
---------------------------------------------------------
1. LANGUAGE: the existing ``symptoms`` / ``causes`` / ``description`` / ``title``
   fields are Turkish. Scraper output is ENGLISH and MUST NEVER be written into
   those fields. English payload goes to parallel ``*_en`` fields only:
   ``symptoms_en``, ``causes_en``, ``description_en``, ``title_en``.
   No translation is performed here (that is a separate task) and no Turkish
   text is fabricated.
2. NO OVERWRITE: an already-populated field is never overwritten. Only empty
   / missing fields are filled.
3. PROVENANCE IS MANDATORY: each injected field carries a source URL kept in
   ``symptoms_en_source`` / ``causes_en_source`` / ``description_en_source`` /
   ``title_en_source``. A record without a usable source URL is SKIPPED, never
   merged.
4. NO NEW CODES: the record count is frozen at 14,352. A harvest row whose code
   is absent from the DB is skipped (counted as ``skipped_unknown_code``).

The script is deterministic and idempotent: running it twice produces a
byte-identical DB, and a re-run never clobbers fields it already filled.

Usage::

    py -3.13 scripts/merge_t45_dtc.py            # merge in place
    py -3.13 scripts/merge_t45_dtc.py --dry-run  # report only, no write
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"

TCN_INPUT = REPO_ROOT / "spn_gap_hunter" / "output" / "t45c_tcn_symptoms.json"
OBDHUT_INPUT = REPO_ROOT / "spn_gap_hunter" / "output" / "t45c_dtc_symptoms.json"

# English payload fields emitted by the harvesters and their parallel DB
# counterpart. The harvest field is the source, the DB field is the target.
EN_FIELD_MAP = {
    "symptoms": "symptoms_en",
    "causes": "causes_en",
    "meaning": "description_en",
    "title": "title_en",
}

# One provenance key per injected ``*_en`` field (mandatory, rule 3).
EN_SOURCE_FIELDS = {
    "symptoms_en": "symptoms_en_source",
    "causes_en": "causes_en_source",
    "description_en": "description_en_source",
    "title_en": "title_en_source",
}

# Marker written once per enriched record so a later run can tell whether the
# row already carries T46 provenance (idempotency anchor).
MERGE_MARKER = "_t46_merge"

# A source URL must be a real http(s) link to be usable as provenance.
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

# Boilerplate that the harvesters scraped alongside the real payload. Stripping
# it keeps the English field informative instead of dumping site chrome.
_TCN_TITLE_SUFFIX = " – TroubleCodes.net"
_OBDHUT_TITLE_SUFFIX = " | OBDHut"

# obdhut prepends this phrase to ``causes``; it is site boilerplate, not content.
_OBDHUT_CAUSE_PREFIX_RE = re.compile(
    r"^The most common cause of\s+[A-Z0-9]+\s*\([^)]*\)\s+is:\s*", re.IGNORECASE
)


def _norm_code(code: Any) -> str:
    """Normalise a DTC code for lookup (uppercase, trimmed)."""
    return str(code).strip().upper()


def _clean_text(value: Any) -> str:
    """Return a stripped, whitespace-collapsed string (empty when not a str)."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _clean_title(code: str, title: Any) -> str:
    """Strip the scraper site chrome from a harvested title."""
    text = _clean_text(title)
    for suffix in (_TCN_TITLE_SUFFIX, _OBDHUT_TITLE_SUFFIX):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    # Drop a leading "<CODE> —" / "<CODE>  –" / "<CODE> -" prefix.
    text = re.sub(rf"^{re.escape(code)}\s*[—–-]\s*", "", text, flags=re.IGNORECASE).strip()
    return text


def _clean_causes(value: Any) -> str:
    """Strip the obdhut boilerplate prefix from a harvested ``causes`` string."""
    text = _clean_text(value)
    return _OBDHUT_CAUSE_PREFIX_RE.sub("", text).strip()


def _is_usable_source(value: Any) -> bool:
    """True when ``value`` is a real http(s) URL usable as provenance."""
    text = _clean_text(value)
    return bool(text) and bool(_URL_RE.match(text))


def _is_empty(value: Any) -> bool:
    """True when a DB field counts as empty / missing (safe to fill)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def has_turkish_text(value: Any) -> bool:
    """True when ``value`` carries a non-ASCII (Turkish) character.

    Used by the regression test to prove no English scrape leaked into the
    Turkish fields. Turkish letters (ç, ğ, ı, ö, ş, ü) are outside ASCII, so a
    pure-ASCII string cannot be Turkish-authored prose in this DB.
    """
    if isinstance(value, str):
        return any(ord(ch) > 127 for ch in value)
    if isinstance(value, (list, tuple, set)):
        return any(has_turkish_text(v) for v in value)
    if isinstance(value, dict):
        return any(has_turkish_text(v) for v in value.values())
    return False


def is_ascii_only(value: Any) -> bool:
    """True when every string inside ``value`` is pure ASCII."""
    text = json.dumps(value, ensure_ascii=False)
    return all(ord(ch) < 128 for ch in text)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _harvest_rows(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise raw harvester rows into ``{code, source, payload}`` dicts.

    Deterministic: rows are sorted by code so ties (troublecodes.net ships
    duplicate rows for a handful of codes) resolve identically on every run.
    """
    rows: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        code = _norm_code(raw.get("code", ""))
        if not code:
            continue
        rows.append(
            {
                "code": code,
                "source_url": _clean_text(raw.get("source_url")),
                "source": _clean_text(raw.get("source")),
                "title": _clean_title(code, raw.get("title")),
                "meaning": _clean_text(raw.get("meaning")),
                "symptoms": _clean_text(raw.get("symptoms")),
                "causes": _clean_causes(raw.get("causes")),
            }
        )
    rows.sort(key=lambda r: (r["code"], r["source"], r["source_url"]))
    return rows


def _collect_candidates(
    rows: Iterable[dict[str, Any]], db: dict[str, Any], stats: dict[str, int]
) -> dict[str, dict[str, Any]]:
    """Fold harvest rows into ``code -> {field: (text, source_url)}`` patches.

    Rows are processed in sorted order so the FIRST usable value for a given
    field wins deterministically (never overwritten by a later duplicate row).
    """
    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = row["code"]
        if code not in db:
            stats["skipped_unknown_code"] += 1
            continue
        if not _is_usable_source(row["source_url"]):
            # Rule 3: no source URL -> never merged.
            stats["skipped_no_source"] += 1
            continue

        patch = candidates.setdefault(code, {})
        for harvest_field, en_field in EN_FIELD_MAP.items():
            if en_field in patch:
                continue  # deterministic first-wins
            text = row.get(harvest_field, "")
            if not text:
                stats[f"empty_in_harvest_{harvest_field}"] += 1
                continue
            # Rule 2: never overwrite an already-populated DB field.
            if not _is_empty(db[code].get(en_field)):
                stats[f"already_present_{en_field}"] += 1
                continue
            patch[en_field] = (text, row["source_url"])
    return candidates


def merge(db_path: Path = DB_PATH, *, dry_run: bool = False) -> dict[str, int]:
    """Merge the T45 harvest into ``db_path``; return a stats dict.

    Idempotent: a second call finds every ``*_en`` field populated and writes
    the DB back unchanged.
    """
    db = _load_json(db_path)
    if not isinstance(db, dict):
        raise SystemExit(f"unexpected DB shape at {db_path}: {type(db).__name__}")

    stats: dict[str, int] = {
        "db_records_before": len(db),
        "harvest_rows": 0,
        "skipped_unknown_code": 0,
        "skipped_no_source": 0,
        "records_enriched": 0,
        "fields_written": 0,
    }
    for en_field in EN_FIELD_MAP.values():
        stats[f"enriched_{en_field}"] = 0
        stats[f"already_present_{en_field}"] = 0
    for harvest_field in EN_FIELD_MAP:
        stats[f"empty_in_harvest_{harvest_field}"] = 0

    rows = _harvest_rows(_load_json(TCN_INPUT)) + _harvest_rows(_load_json(OBDHUT_INPUT))
    stats["harvest_rows"] = len(rows)
    candidates = _collect_candidates(rows, db, stats)

    for code in sorted(candidates):
        patch = candidates[code]
        if not patch:
            continue
        rec = db[code]
        for en_field in sorted(patch):
            text, url = patch[en_field]
            rec[en_field] = text
            rec[EN_SOURCE_FIELDS[en_field]] = url
            stats[f"enriched_{en_field}"] += 1
            stats["fields_written"] += 1
        rec[MERGE_MARKER] = "t46a"
        stats["records_enriched"] += 1

    stats["db_records_after"] = len(db)
    if stats["db_records_after"] != stats["db_records_before"]:
        raise SystemExit(
            "record count changed during merge "
            f"({stats['db_records_before']} -> {stats['db_records_after']}) — new codes must not be added"
        )

    if not dry_run:
        db_path.write_text(
            json.dumps(db, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DB_PATH, help="target DB path")
    parser.add_argument("--dry-run", action="store_true", help="report only, do not write")
    args = parser.parse_args(argv)

    stats = merge(args.db, dry_run=args.dry_run)
    width = max(len(k) for k in stats)
    for key in sorted(stats):
        print(f"{key:<{width}} : {stats[key]}")
    if args.dry_run:
        print("\n[dry-run] DB not written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

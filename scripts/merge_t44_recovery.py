"""T48-A / IS 2 — merge the 6 T44-recovered clipped steps into the J1939 DB.

Task: kanban t_bc783d23 (T48-A, item 2).

Background
----------
T44 traced 77 clipped J1939 steps back to their sources and wrote
``UCANLAB-ARSIV/collector-final/output-final/t44_recovered_final.json``::

    {"recovered": [6 rows], "unresolved": [38 rows], "stats": {...}}

Each recovered row is ``{spn, path, old_clipped, full_text, source_url, ...}``
where ``path`` is a location inside the SPN record (e.g. ``SPN/steps[2]`` or
``SPN/field_articles[0]/snippets[0]``). Those 6 rows were accepted as candidates
but NEVER merged into ``data/diagnostics/j1939_spn_fmi_database.json``.

The 38 ``unresolved`` rows carry no ``full_text`` in the source, so they cannot
be merged and are deliberately ignored (accepted in the task body).

LOCKED RULES (task body — violation = task rejection)
----------------------------------------------------
1. ASSERT GATE (Tuzaklar-ve-Dersler #1): a row is merged only when its
   ``old_clipped`` text is genuinely a prefix of its ``full_text`` under a
   documented normalization. Otherwise the row is SKIPPED.
2. Record count is frozen (3,910 SPNs). No new SPN keys.
3. NO OVERWRITE: if the (spn, path) target no longer holds the stale clipped
   text, the row is skipped.
4. Provenance: the row's ``source_url`` is recorded alongside the merge.

The script is deterministic and idempotent: after the first run every target
already holds the full text (the stale clipped value is gone), so a second run
merges 0 rows and leaves the file byte-identical.

Usage::

    py -3.13 scripts/merge_t44_recovery.py            # merge in place
    py -3.13 scripts/merge_t44_recovery.py --dry-run  # report only, no write
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
SOURCE_PATH = (
    FIXTURES_DIR / "t44_recovered_final.json"
    if (FIXTURES_DIR / "t44_recovered_final.json").exists()
    else Path(
        r"C:\Users\canak\Desktop\UCANLAB-ARSIV\collector-final\output-final\t44_recovered_final.json"
    )
)

# Marker + source field stamped on every record this script touches.
MERGE_MARKER = "_t48_t44_merge"
MERGE_SOURCE_FIELD = "_t48_t44_source"

# Unicode dash variants normalized to ASCII '-' before comparison. The duaparts
# pages render an en-dash in the clipped capture and a hyphen in the live text.
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"

_PATH_RE = re.compile(r"^([A-Za-z_]+)(?:\[(\d+)\])?$")


def normalize(text: str) -> str:
    """Normalize text for the prefix gate.

    Applies, in order: HTML entity unescape, NFKC unicode normalization,
    removal of leftover ``&word;`` entities, dash folding, whitespace collapse
    and case folding. This is what lets the duaparts en-dash vs hyphen and the
    ServiceRanger letter-marker variants compare correctly.
    """
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"&\w+;", "", text)
    for dash in _DASHES:
        text = text.replace(dash, "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def gate_ok(old_clipped: str, full_text: str) -> bool:
    """ASSERT GATE: is ``old_clipped`` a normalized prefix/substring of ``full_text``?

    ``True`` only when both are non-empty and the normalized clipped text occurs
    inside the normalized full text (allowing the leading step letter marker to
    differ, as ServiceRanger renumbers steps ``A.`` -> ``V.``).
    """
    if not old_clipped or not full_text:
        return False
    old_n = normalize(old_clipped)
    full_n = normalize(full_text)
    if not old_n or not full_n:
        return False
    if old_n in full_n:
        return True
    # Allow a differing leading letter marker ("a. ..." vs "v. ...").
    stripped = re.sub(r"^[a-z][\.\)]\s*", "", old_n)
    return bool(stripped) and stripped in full_n


def resolve_path(rec: dict[str, Any], path: str) -> Any:
    """Resolve ``path`` (e.g. ``SPN/steps[2]``) inside an SPN record.

    Supports the two shapes present in the source: a top-level field with an
    optional list index, and one nested ``field_articles[i]/snippets[j]`` hop.
    Raises ``KeyError`` / ``IndexError`` when the path does not exist.
    """
    parts = path.split("/")
    if not parts or parts[0] != "SPN":
        raise KeyError(f"unsupported path root: {path}")
    cur: Any = rec
    for part in parts[1:]:
        m = _PATH_RE.match(part)
        if not m:
            raise KeyError(f"unsupported path segment: {part!r} in {path}")
        field, idx = m.group(1), m.group(2)
        cur = cur[field]
        if idx is not None:
            cur = cur[int(idx)]
    return cur


def _set_path(rec: dict[str, Any], path: str, value: str) -> None:
    """Set the (spn, path) target inside ``rec`` to ``value``."""
    parts = path.split("/")
    if not parts or parts[0] != "SPN":
        raise KeyError(f"unsupported path root: {path}")
    cur: Any = rec
    for part in parts[1:-1]:
        m = _PATH_RE.match(part)
        if not m:
            raise KeyError(f"unsupported path segment: {part!r} in {path}")
        field, idx = m.group(1), m.group(2)
        cur = cur[field]
        if idx is not None:
            cur = cur[int(idx)]
    last = parts[-1]
    m = _PATH_RE.match(last)
    if not m:
        raise KeyError(f"unsupported path segment: {last!r} in {path}")
    field, idx = m.group(1), m.group(2)
    if idx is not None:
        cur[field][int(idx)] = value
    else:
        cur[field] = value


def load_source(source_path: Path = SOURCE_PATH) -> dict[str, Any]:
    return json.loads(Path(source_path).read_text(encoding="utf-8"))


def merge(db_path: Path = DB_PATH, source_path: Path = SOURCE_PATH) -> dict[str, int]:
    """Merge the recovered rows into ``db_path``; return a stats dict."""
    db = json.loads(Path(db_path).read_text(encoding="utf-8"))
    if not isinstance(db, dict) or "spns" not in db:
        raise SystemExit(f"unexpected J1939 DB shape at {db_path}")

    spns = db["spns"]
    source = load_source(source_path)
    recovered = source.get("recovered", [])

    stats: dict[str, int] = {
        "source_recovered": len(recovered),
        "records_merged": 0,
        "skipped_gate": 0,
        "skipped_unknown_spn": 0,
        "skipped_bad_path": 0,
        "skipped_stale_target": 0,
        "skipped_no_change": 0,
        "spns_before": len(spns),
        "spns_after": 0,
    }

    for row in recovered:
        spn_key = row.get("spn", "")
        path = row.get("path", "")
        old_clipped = row.get("old_clipped", "")
        full_text = row.get("full_text", "")
        source_url = row.get("source_url", "")

        # Rule 1: assert gate — no fabricated merges.
        if not gate_ok(old_clipped, full_text):
            stats["skipped_gate"] += 1
            continue
        rec = spns.get(spn_key)
        if rec is None:
            stats["skipped_unknown_spn"] += 1
            continue
        try:
            current = resolve_path(rec, path)
        except (KeyError, IndexError, TypeError):
            stats["skipped_bad_path"] += 1
            continue
        # Rule 3: only replace a field that still holds the stale clipped text.
        if not isinstance(current, str) or current.strip() != old_clipped.strip():
            if current == full_text.strip():
                stats["skipped_no_change"] += 1  # already merged (idempotent)
            else:
                stats["skipped_stale_target"] += 1
            continue

        new_value = full_text.strip()
        if new_value == current:
            stats["skipped_no_change"] += 1
            continue
        if len(new_value) <= len(current):
            stats["skipped_gate"] += 1
            continue

        _set_path(rec, path, new_value)
        rec[MERGE_MARKER] = "t48a"
        if source_url:
            rec[MERGE_SOURCE_FIELD] = source_url
        stats["records_merged"] += 1

    stats["spns_after"] = len(spns)
    if stats["spns_after"] != stats["spns_before"]:
        raise SystemExit(
            "SPN count changed during merge "
            f"({stats['spns_before']} -> {stats['spns_after']}) — new SPNs must not be added"
        )

    Path(db_path).write_text(
        json.dumps(db, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DB_PATH, help="target J1939 DB")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH, help="T44 recovery JSON")
    parser.add_argument("--dry-run", action="store_true", help="report only, no write")
    args = parser.parse_args(argv)

    if args.dry_run:
        # Report without writing: account against an untouched in-memory load.
        db = json.loads(Path(args.db).read_text(encoding="utf-8"))
        source = load_source(args.source)
        stats = _report_only(db, source)
    else:
        stats = merge(args.db, args.source)

    width = max(len(k) for k in stats)
    for key in sorted(stats):
        print(f"{key:<{width}} : {stats[key]}")
    if args.dry_run:
        print("\n[dry-run] DB not written.")
    return 0


def _report_only(db: dict[str, Any], source: dict[str, Any]) -> dict[str, int]:
    """Mirror ``merge``'s accounting without writing (for --dry-run)."""
    stats = {
        "source_recovered": len(source.get("recovered", [])),
        "records_merged": 0,
        "skipped_gate": 0,
        "skipped_unknown_spn": 0,
        "skipped_bad_path": 0,
        "skipped_stale_target": 0,
        "skipped_no_change": 0,
        "spns_before": len(db["spns"]),
        "spns_after": len(db["spns"]),
    }
    for row in source.get("recovered", []):
        if not gate_ok(row.get("old_clipped", ""), row.get("full_text", "")):
            stats["skipped_gate"] += 1
            continue
        rec = db["spns"].get(row.get("spn", ""))
        if rec is None:
            stats["skipped_unknown_spn"] += 1
            continue
        try:
            current = resolve_path(rec, row["path"])
        except (KeyError, IndexError, TypeError):
            stats["skipped_bad_path"] += 1
            continue
        if current == row["full_text"].strip():
            stats["skipped_no_change"] += 1
        elif isinstance(current, str) and current.strip() == row["old_clipped"].strip():
            stats["records_merged"] += 1
        else:
            stats["skipped_stale_target"] += 1
    return stats


if __name__ == "__main__":
    sys.exit(main())

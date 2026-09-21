#!/usr/bin/env python
"""Validate `security-exceptions.yaml` and emit pip-audit ignore flags.

S6 (REVIEW Aşama 4): dependency-advisory exceptions were inlined in CI as
`pip-audit --ignore-vuln <ID>` with no expiry, owner or justification, so an
accepted advisory could stay suppressed forever. This script makes the
exceptions auditable:

  * every entry must declare ``id``, ``owner``, ``justification``, and a future
    ``expires`` date (ISO ``YYYY-MM-DD``) — CI FAILS on/after that date;
  * duplicate ids are rejected;
  * ``--print-flags`` emits the exact ``--ignore-vuln`` arguments for CI, so
    the pipeline never hand-maintains the list again.

Usage
-----
    python scripts/check_security_exceptions.py                 # validate
    python scripts/check_security_exceptions.py --print-flags   # emit flags

Exit codes
----------
    0  all exceptions are valid and unexpired
    1  a validation error occurred (malformed file, missing field, expired,
       or duplicate id)
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

try:  # PyYAML is a dev dependency; degrade gracefully if it is unavailable.
    import yaml
except ImportError:  # pragma: no cover - environment dependent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO_ROOT / "security-exceptions.yaml"
_REQUIRED_FIELDS = ("id", "owner", "justification", "expires")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def _parse_expiry(raw: object, index: int) -> dt.date:
    """Parse an expiry, rejecting both malformed and impossible dates."""
    text = str(raw)
    if not _DATE_RE.match(text):
        raise ValueError(
            f"exceptions[{index}] 'expires' must be an ISO date (YYYY-MM-DD), got {text!r}"
        )
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"exceptions[{index}] 'expires' is not a real calendar date: {text!r}"
        ) from exc


def load_exceptions(path: Path) -> list[dict[str, object]]:
    """Load and structurally validate the exceptions file."""
    if not path.is_file():
        raise ValueError(f"security exceptions file not found: {path}")
    if yaml is None:
        raise ValueError("PyYAML is required to parse the security exceptions file")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("top level of the exceptions file must be a mapping")
    entries = data.get("exceptions", [])
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError("'exceptions' must be a list")

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"exceptions[{index}] must be a mapping")
        for field in _REQUIRED_FIELDS:
            if field not in entry:
                raise ValueError(f"exceptions[{index}] is missing required field '{field}'")
            value = entry[field]
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"exceptions[{index}] is missing required field '{field}'")
        advisory_id = str(entry["id"])
        if advisory_id in seen:
            raise ValueError(f"duplicate exception id '{advisory_id}'")
        seen.add(advisory_id)
        _parse_expiry(entry["expires"], index)
    return entries


def expired_ids(entries: list[dict[str, object]], today: dt.date | None = None) -> list[str]:
    """Return ids whose expiry date is today or in the past."""
    today = today or dt.date.today()
    result: list[str] = []
    for index, entry in enumerate(entries):
        expires = _parse_expiry(entry["expires"], index)
        if expires <= today:
            result.append(str(entry["id"]))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH, help="path to the exceptions file")
    parser.add_argument(
        "--print-flags",
        action="store_true",
        help="print the pip-audit --ignore-vuln flags for the valid exceptions",
    )
    args = parser.parse_args(argv)

    try:
        entries = load_exceptions(args.path)
    except ValueError as exc:
        _fail(str(exc))
        return 1

    expired = expired_ids(entries)
    if expired:
        for advisory_id in expired:
            _fail(
                f"security exception '{advisory_id}' has EXPIRED — re-evaluate the advisory "
                "and either patch the dependency or extend 'expires' with a new justification"
            )
        return 1

    if args.print_flags:
        for entry in entries:
            print(f"--ignore-vuln {entry['id']}", end=" ")
        print()
    else:
        print(f"OK: {len(entries)} security exception(s) valid, none expired")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

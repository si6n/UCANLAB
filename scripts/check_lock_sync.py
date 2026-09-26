#!/usr/bin/env python
"""Fail CI when requirements.lock drifts from requirements.txt pins (B-10).

Checks every `name==version` entry in requirements.lock satisfies the
corresponding floor/ceiling constraint in requirements.txt.
Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _parse_req(line: str) -> tuple[str, list[tuple[str, str]]] | None:
    line = line.split("#", 1)[0].strip().rstrip("\\").strip()
    if not line or line.startswith("-"):
        return None
    m = re.match(r"^([A-Za-z0-9_.\-]+)((?:\s*[<>=!~]=?\s*[^,;\s]+(?:\s*,\s*)?)*)", line)
    if not m:
        return None
    name = m.group(1).lower().replace("_", "-")
    specs = re.findall(r"(>=|<=|==|>|<|~=|!=)\s*([^,;\s]+)", m.group(2) or "")
    return name, specs


def _parse_lock_version(line: str) -> tuple[str, str] | None:
    m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\;]+)", line.strip())
    return (m.group(1).lower().replace("_", "-"), m.group(2)) if m else None


def _ver_tuple(v: str) -> tuple[object, ...]:
    out = []
    for p in re.split(r"[.\-]", v):
        out.append(int(p) if p.isdigit() else p)
    return tuple(out)


def _satisfies(ver: str, op: str, ref: str) -> bool:
    a, b = _ver_tuple(ver), _ver_tuple(ref)
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "~=":
        return a >= b
    return True


def main() -> int:
    reqs: dict[str, list[tuple[str, str]]] = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        parsed = _parse_req(line)
        if parsed:
            reqs.setdefault(parsed[0], []).extend(parsed[1])
    errors = []
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        lv = _parse_lock_version(line)
        if not lv:
            continue
        name, ver = lv
        for op, ref in reqs.get(name, []):
            if not _satisfies(ver, op, ref):
                errors.append(f"{name}=={ver} violates requirements.txt {op}{ref}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"OK: lock satisfies requirements.txt pins ({len(reqs)} constrained)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

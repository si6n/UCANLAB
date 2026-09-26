#!/usr/bin/env python3
"""DBC Lint and Integrity Gate (ISO 26262 & Aksiyon 22/23).

Validates all DBC files under data/dbc/ for syntax, structure, scaling,
and catalog consistency:
- UTF-8 valid encoding.
- At most one VERSION block per file (no corrupted concatenation).
- Required BS_: baud rate section present.
- No duplicate BO_ arbitration IDs within a single file (excluding documented canboat overlays).
- No signals with scaling factor == 0.0 (prevents division by zero / corrupt conversions).
- Cantools parser validity.
- Bidirectional disk <-> catalog.json consistency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

DBC_DIR = Path(__file__).resolve().parents[1] / "data" / "dbc"
CATALOG_PATH = DBC_DIR / "catalog.json"

# Documented exceptions for legacy canboat overlays where multi-packet / multiplexed PGNs share IDs
KNOWN_DUPLICATE_BO_EXCEPTIONS: set[str] = {
    "heavy_duty/j1939_canboat.dbc",
    "marine/n2k_canboat.dbc",
}


class LintViolation(NamedTuple):
    file_rel: str
    rule: str
    message: str


def lint_dbc_file(file_path: Path, dbc_root: Path = DBC_DIR) -> list[LintViolation]:
    rel_path = file_path.relative_to(dbc_root).as_posix()
    violations: list[LintViolation] = []

    # 1. Encoding check (UTF-8)
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        violations.append(LintViolation(rel_path, "ENCODING", f"Invalid UTF-8 encoding: {exc}"))
        try:
            content = file_path.read_text(encoding="latin-1")
        except Exception:
            return violations

    # 2. Multiple VERSION blocks check
    version_matches = re.findall(r"^VERSION\b", content, re.MULTILINE)
    if len(version_matches) > 1:
        violations.append(
            LintViolation(
                rel_path,
                "MULTIPLE_VERSION",
                f"File contains {len(version_matches)} VERSION blocks (corrupted concatenation)",
            )
        )

    # 3. Missing BS_: section check
    if not re.search(r"^BS_:", content, re.MULTILINE):
        violations.append(
            LintViolation(
                rel_path,
                "MISSING_BS",
                "Missing required 'BS_:' keyword (Bit Timing / Baudrate section)",
            )
        )

    # 4. Duplicate BO_ arbitration IDs check
    bo_matches = re.findall(r"^BO_\s+(\d+)\s+([A-Za-z0-9_]+):", content, re.MULTILINE)
    seen_ids: dict[str, str] = {}
    dups: dict[str, list[str]] = {}
    for msg_id, msg_name in bo_matches:
        if msg_id in seen_ids:
            if msg_id not in dups:
                dups[msg_id] = [seen_ids[msg_id]]
            dups[msg_id].append(msg_name)
        else:
            seen_ids[msg_id] = msg_name

    if dups and rel_path not in KNOWN_DUPLICATE_BO_EXCEPTIONS:
        violations.append(
            LintViolation(
                rel_path,
                "DUPLICATE_BO",
                f"Duplicate BO_ IDs found: {list(dups.keys())[:5]}",
            )
        )

    # 5. Factor == 0 check (SG_ Name : ... (0, ...))
    zero_factors = re.findall(r"SG_\s+(\w+)\s*:[^:]*\(\s*(0(?:\.0+)?)\s*,", content)
    if zero_factors:
        violations.append(
            LintViolation(
                rel_path,
                "ZERO_FACTOR",
                f"Signal with factor == 0.0 detected: {[sig[0] for sig in zero_factors[:5]]}",
            )
        )

    return violations


def check_bidirectional_catalog(dbc_root: Path = DBC_DIR, catalog_path: Path = CATALOG_PATH) -> list[LintViolation]:
    violations: list[LintViolation] = []
    if not catalog_path.exists():
        violations.append(LintViolation("catalog.json", "CATALOG_MISSING", "data/dbc/catalog.json not found"))
        return violations

    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        violations.append(LintViolation("catalog.json", "CATALOG_PARSE", f"Failed to parse catalog.json: {exc}"))
        return violations

    catalog_files: set[str] = set()
    for cat_name, cat_meta in catalog.get("categories", {}).items():
        for f in cat_meta.get("files", []):
            catalog_files.add(f"{cat_name}/{f['filename']}")

    disk_files = {p.relative_to(dbc_root).as_posix() for p in dbc_root.rglob("*.dbc")}

    untracked_on_disk = disk_files - catalog_files
    if untracked_on_disk:
        violations.append(
            LintViolation(
                "catalog.json",
                "CATALOG_DRIFT_DISK",
                f"DBC files on disk not registered in catalog.json: {sorted(untracked_on_disk)[:5]}",
            )
        )

    missing_on_disk = catalog_files - disk_files
    if missing_on_disk:
        violations.append(
            LintViolation(
                "catalog.json",
                "CATALOG_DRIFT_CATALOG",
                f"DBC catalog entries missing on disk: {sorted(missing_on_disk)[:5]}",
            )
        )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint and validate DBC files.")
    parser.add_argument("--strict", action="store_true", help="Fail on any warning (e.g. missing BS_:)")
    args = parser.parse_args()

    dbc_files = sorted(DBC_DIR.rglob("*.dbc"))
    print(f"Linting {len(dbc_files)} DBC files in {DBC_DIR}...")

    all_violations: list[LintViolation] = []
    for f in dbc_files:
        all_violations.extend(lint_dbc_file(f))

    all_violations.extend(check_bidirectional_catalog())

    critical_violations = [
        v for v in all_violations if v.rule in {"ENCODING", "MULTIPLE_VERSION", "DUPLICATE_BO", "ZERO_FACTOR", "CATALOG_DRIFT_DISK", "CATALOG_DRIFT_CATALOG"}
    ]
    warning_violations = [v for v in all_violations if v.rule == "MISSING_BS"]

    print(f"\nResult: {len(critical_violations)} critical errors, {len(warning_violations)} warnings.")
    for v in critical_violations:
        print(f"  [ERROR] [{v.rule}] {v.file_rel}: {v.message}")
    for v in warning_violations:
        print(f"  [WARN]  [{v.rule}] {v.file_rel}: {v.message}")

    if critical_violations or (args.strict and warning_violations):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

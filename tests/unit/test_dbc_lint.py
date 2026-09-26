"""Unit tests for DBC Linter and Integrity Gate (ISO 26262 & Aksiyon 22/23)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.lint_dbc import (
    CATALOG_PATH,
    DBC_DIR,
    check_bidirectional_catalog,
    lint_dbc_file,
)


def test_production_dbc_files_have_zero_critical_lint_errors() -> None:
    """All DBC files in data/dbc/ must pass critical lint gates."""
    dbc_files = sorted(DBC_DIR.rglob("*.dbc"))
    assert len(dbc_files) >= 70, f"Expected >= 70 DBC files, got {len(dbc_files)}"

    critical_violations = []
    for f in dbc_files:
        violations = lint_dbc_file(f)
        for v in violations:
            if v.rule in {"ENCODING", "MULTIPLE_VERSION", "DUPLICATE_BO", "ZERO_FACTOR"}:
                critical_violations.append(v)

    assert not critical_violations, f"Found critical DBC lint errors: {critical_violations}"


def test_dbc_catalog_bidirectional_gate() -> None:
    """Aksiyon 23: DBC catalog.json and disk must be 100% synchronized in both directions."""
    violations = check_bidirectional_catalog()
    assert not violations, f"DBC catalog drift detected: {violations}"

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog_files = {
        f"{cat_name}/{f['filename']}"
        for cat_name, cat_meta in catalog.get("categories", {}).items()
        for f in cat_meta.get("files", [])
    }
    disk_files = {p.relative_to(DBC_DIR).as_posix() for p in DBC_DIR.rglob("*.dbc")}

    assert catalog_files == disk_files
    assert len(disk_files) == catalog["total_files"]


def test_linter_detects_multiple_version(tmp_path: Path) -> None:
    """Linter must catch duplicate VERSION headers from corrupted concatenations."""
    bad_dbc = tmp_path / "multi_ver.dbc"
    bad_dbc.write_text(
        'VERSION "1.0"\nBS_:\nBO_ 100 Msg1: 8 Vector__XXX\nVERSION "2.0"\nBO_ 200 Msg2: 8 Vector__XXX\n',
        encoding="utf-8",
    )
    violations = lint_dbc_file(bad_dbc, dbc_root=tmp_path)
    rules = [v.rule for v in violations]
    assert "MULTIPLE_VERSION" in rules


def test_linter_detects_duplicate_bo_ids(tmp_path: Path) -> None:
    """Linter must catch duplicate arbitration IDs in the same DBC."""
    bad_dbc = tmp_path / "dup_bo.dbc"
    bad_dbc.write_text(
        'VERSION ""\nBS_:\nBO_ 500 Msg1: 8 Vector__XXX\nBO_ 500 Msg2: 8 Vector__XXX\n',
        encoding="utf-8",
    )
    violations = lint_dbc_file(bad_dbc, dbc_root=tmp_path)
    rules = [v.rule for v in violations]
    assert "DUPLICATE_BO" in rules


def test_linter_detects_zero_factor_signal(tmp_path: Path) -> None:
    """Linter must catch corrupt signals with scaling factor == 0.0."""
    bad_dbc = tmp_path / "zero_factor.dbc"
    bad_dbc.write_text(
        'VERSION ""\nBS_:\nBO_ 100 Msg1: 8 Vector__XXX\n SG_ BadSig : 0|8@1+ (0,0) [0|255] "" Vector__XXX\n',
        encoding="utf-8",
    )
    violations = lint_dbc_file(bad_dbc, dbc_root=tmp_path)
    rules = [v.rule for v in violations]
    assert "ZERO_FACTOR" in rules


def test_linter_detects_missing_bs(tmp_path: Path) -> None:
    """Linter must warn on missing BS_: section."""
    bad_dbc = tmp_path / "no_bs.dbc"
    bad_dbc.write_text('VERSION ""\nBO_ 100 Msg1: 8 Vector__XXX\n', encoding="utf-8")
    violations = lint_dbc_file(bad_dbc, dbc_root=tmp_path)
    rules = [v.rule for v in violations]
    assert "MISSING_BS" in rules

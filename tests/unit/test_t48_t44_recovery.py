"""T48-A / IS 2 regression tests — merge the 6 T44-recovered clipped steps.

Task: kanban t_bc783d23 (T48-A, item 2).

Background
----------
T44 investigated 77 clipped J1939 steps and produced
``UCANLAB-ARSIV/collector-final/output-final/t44_recovered_final.json`` with
``{recovered: [6], unresolved: [38], stats}``. The 6 recovered rows were only
``candidates`` — never merged into ``data/diagnostics/j1939_spn_fmi_database.json``.

Each recovered row carries:
  * ``spn``        — target SPN key (e.g. ``SPN_84``)
  * ``path``       — path inside that SPN record (e.g. ``SPN/steps[2]``)
  * ``old_clipped``— the stale 400-char clipped string currently in the DB
  * ``full_text``  — the recovered full text scraped from the source
  * ``source_url`` — provenance

LOCKED RULES (task body)
-------------------------
1. ASSERT GATE: only merge when the old clipped text is genuinely a prefix of
   the recovered full text (under the documented normalization). A record whose
   gate fails is SKIPPED, never merged.  (Tuzaklar-ve-Dersler #1 — no inventing.)
2. Record count of the J1939 DB must not change (no new SPN keys).
3. Never overwrite a field that is no longer the stale clipped text.
4. Only the resolved (spn, path) target changes.

The tests exercise the production file and the archive source read-only, plus an
idempotency check on a temporary copy.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_DB_PATH = REPO_ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
SCRIPT_PATH = REPO_ROOT / "scripts" / "merge_t44_recovery.py"
SOURCE_PATH = REPO_ROOT / "tests" / "fixtures" / "t44_recovered_final.json"

EXPECTED_RECOVERED = 6
EXPECTED_SPNS = 4253


def _load_recover_module() -> Any:
    assert SCRIPT_PATH.exists(), "scripts/merge_t44_recovery.py is missing"
    spec = importlib.util.spec_from_file_location("merge_t44_recovery", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["merge_t44_recovery"] = module
    spec.loader.exec_module(module)
    module.SOURCE_PATH = SOURCE_PATH
    return module


recover_mod = _load_recover_module()


@pytest.fixture(scope="module")
def source() -> dict:
    assert SOURCE_PATH.exists(), f"archive source missing: {SOURCE_PATH}"
    return json.loads(SOURCE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def prod_db() -> dict:
    assert PROD_DB_PATH.exists(), "production J1939 DB missing"
    return json.loads(PROD_DB_PATH.read_text(encoding="utf-8"))


class TestSourceIntegrity:
    def test_source_has_six_recovered(self, source: dict) -> None:
        assert len(source["recovered"]) == EXPECTED_RECOVERED
        assert len(source.get("unresolved", [])) == 38

    def test_every_recovered_row_is_shape_valid(self, source: dict) -> None:
        for row in source["recovered"]:
            for key in ("spn", "path", "old_clipped", "full_text", "source_url"):
                assert key in row and row[key], f"missing {key} in {row.get('spn')}"
            assert row["source_url"].startswith("http")

    def test_every_recovered_row_passes_the_gate(self, source: dict) -> None:
        for row in source["recovered"]:
            assert recover_mod.gate_ok(row["old_clipped"], row["full_text"]), (
                f"gate failed for {row['spn']} {row['path']}"
            )

    def test_gate_rejects_unrelated_text(self) -> None:
        assert not recover_mod.gate_ok("Totally unrelated text", "Something else entirely")

    def test_gate_is_not_trivially_true(self) -> None:
        assert not recover_mod.gate_ok("", "anything")
        assert not recover_mod.gate_ok("abc", "")


class TestPathResolution:
    def test_resolves_step_index(self, prod_db: dict) -> None:
        rec = prod_db["spns"]["SPN_84"]
        value = recover_mod.resolve_path(rec, "SPN/steps[2]")
        assert isinstance(value, str)

    def test_resolves_nested_article_snippet(self, prod_db: dict) -> None:
        rec = prod_db["spns"]["SPN_3058"]
        value = recover_mod.resolve_path(rec, "SPN/field_articles[0]/snippets[0]")
        assert isinstance(value, str)


class TestProductionDatabase:
    def test_spn_count_frozen(self, prod_db: dict) -> None:
        assert len(prod_db["spns"]) == EXPECTED_SPNS

    def test_all_six_targets_are_resolved(self, prod_db: dict, source: dict) -> None:
        for row in source["recovered"]:
            rec = prod_db["spns"][row["spn"]]
            value = recover_mod.resolve_path(rec, row["path"])
            assert isinstance(value, str)
            assert value == row["full_text"].strip(), f"{row['spn']} {row['path']} not merged"
            assert len(value) > len(row["old_clipped"])

    def test_no_target_still_holds_the_clipped_text(self, prod_db: dict, source: dict) -> None:
        for row in source["recovered"]:
            rec = prod_db["spns"][row["spn"]]
            value = recover_mod.resolve_path(rec, row["path"])
            assert value != row["old_clipped"], (
                f"{row['spn']} {row['path']} still holds the clipped text"
            )

    def test_merge_marker_and_source_recorded(self, prod_db: dict, source: dict) -> None:
        for row in source["recovered"]:
            rec = prod_db["spns"][row["spn"]]
            assert rec.get(recover_mod.MERGE_MARKER) == "t48a"
            assert rec.get(recover_mod.MERGE_SOURCE_FIELD, "").startswith("http")

    def test_merge_is_idempotent(self, tmp_path: Path) -> None:
        """A run on an already-merged copy changes nothing."""
        copy = tmp_path / "j1939_copy.json"
        shutil.copyfile(PROD_DB_PATH, copy)
        kwargs = {"source_path": SOURCE_PATH} if _accepts_source() else {}
        digest1 = copy.read_bytes()
        stats = recover_mod.merge(copy, **kwargs)
        assert stats.get("records_merged", 0) == 0
        assert stats.get("skipped_no_change", 0) == EXPECTED_RECOVERED
        assert copy.read_bytes() == digest1, "an idempotent run must not rewrite the file"

    def test_merge_replaces_stale_clipped_text(self, tmp_path: Path, source: dict) -> None:
        """Reconstruct the pre-merge state: stale text -> full text, 6 rows."""
        copy = tmp_path / "j1939_stale.json"
        shutil.copyfile(PROD_DB_PATH, copy)
        payload = json.loads(copy.read_text(encoding="utf-8"))
        # Rewind the 6 targets to their stale clipped values and drop the marker.
        for row in source["recovered"]:
            rec = payload["spns"][row["spn"]]
            recover_mod._set_path(rec, row["path"], row["old_clipped"])
            rec.pop(recover_mod.MERGE_MARKER, None)
            rec.pop(recover_mod.MERGE_SOURCE_FIELD, None)
        copy.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

        kwargs = {"source_path": SOURCE_PATH} if _accepts_source() else {}
        stats = recover_mod.merge(copy, **kwargs)
        assert stats["records_merged"] == EXPECTED_RECOVERED
        after = json.loads(copy.read_text(encoding="utf-8"))
        for row in source["recovered"]:
            rec = after["spns"][row["spn"]]
            assert recover_mod.resolve_path(rec, row["path"]) == row["full_text"].strip()

    def test_no_new_keys_beyond_marker_and_source(self, tmp_path: Path) -> None:
        """Only the 6 target records gain the marker/source keys."""
        copy = tmp_path / "j1939_copy2.json"
        shutil.copyfile(PROD_DB_PATH, copy)
        before = json.loads(copy.read_text(encoding="utf-8"))["spns"]
        before_keys = set().union(*(set(r) for r in before.values()))
        kwargs = {"source_path": SOURCE_PATH} if _accepts_source() else {}
        recover_mod.merge(copy, **kwargs)
        after = json.loads(copy.read_text(encoding="utf-8"))["spns"]
        added = set().union(*(set(r) for r in after.values())) - before_keys
        assert added <= {recover_mod.MERGE_MARKER, recover_mod.MERGE_SOURCE_FIELD}


def _accepts_source() -> bool:
    return "source_path" in inspect.signature(recover_mod.merge).parameters

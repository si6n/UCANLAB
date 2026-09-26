# -*- coding: utf-8 -*-
"""T78 regression lock — a step payload written under the DB's own field name
must never be silently dropped by the merge.

THE DEFECT (measured 2026-09-25)
--------------------------------
Three adapters (T74b engine-codes, T74c wayback-hex, T76 obd2) put the step
list under `procedures_full` — the DB field name — because that is what the
harvest produced. `scripts/t66_merge.py` read only `procedures_steps` / `steps`
(plus the J1939-style `procedures_full_append` bundle), so:

  * 32 codes with a fully EMPTY `procedures_full` never received their steps;
  * 325 codes whose `procedures_full` was already populated (faultcode.org)
    discarded an INDEPENDENT second source — all 325 step sets measured fully
    disjoint from the stored text;
  * the merge report still counted these records as "applied" whenever any
    other field on the same record filled, so the drop had no visible signal.

LOCKED CONTRACT (what the tests below pin)
------------------------------------------
  * a flat `procedures_full` payload is readable exactly like `procedures_steps`
    when the DB field is empty (fill-if-empty);
  * when the DB field is already populated, the same payload is preserved as a
    second source in `procedures_full_multi` (never overwriting the original,
    never duplicating an existing source_url);
  * re-applying the same MR is a no-op (idempotent: 2nd apply byte-identical);
  * record counts stay frozen; unknown keys are still skipped.

All tests use tmp_path fixtures; the real data/diagnostics DBs are never read
or written here (paths are injected through `main(...)` kwargs).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MERGE_PATH = REPO_ROOT / "scripts" / "t66_merge.py"


def _load_merge_module():
    spec = importlib.util.spec_from_file_location("t66_merge_t78", MERGE_PATH)
    assert spec and spec.loader, "merge module must be loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def merge_mod():
    return _load_merge_module()


@pytest.fixture()
def dbs(tmp_path: Path):
    dtc_db = tmp_path / "dtc_database.json"
    j1939_db = tmp_path / "j1939_spn_fmi_database.json"
    dtc_db.write_text(
        json.dumps(
            {
                "P2851": {"title_en": "Shift Fork Position Sensor A/B Correlation"},
                "P2035": {
                    "title_en": "Exhaust Gas Temperature Sensor Circuit High",
                    "procedures_full": ["Inspect the exhaust gas temperature sensor wiring"],
                    "procedures_source_url": "https://faultcode.org/car/p2035",
                    "procedures_source": "faultcode.org",
                },
            },
            ensure_ascii=False, indent=1,
        ) + "\n",
        encoding="utf-8",
    )
    j1939_db.write_text(
        json.dumps({"metadata": {"total_spns": 0}, "spns": {}}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return dtc_db, j1939_db


def _run(merge_mod, tmp_path: Path, dbs, records, apply=True):
    inp = tmp_path / "mr_t78.json"
    inp.write_text(
        json.dumps({"target_db": "dtc", "records": records}, ensure_ascii=False),
        encoding="utf-8",
    )
    return merge_mod.main(
        ["--inputs", str(inp), "--report", str(tmp_path / "report.json")]
        + (["--apply"] if apply else []),
        dtc_db=dbs[0],
        j1939_db=dbs[1],
        lock_path=tmp_path / ".t66_merge.lock",
    )


def _steps_rec(code: str, steps: list[str], url: str, **over) -> dict:
    rec = {
        "code": code,
        "url": url,
        "source": url.split("/")[2],
        "evidence": "Shift fork position sensor correlation fault recorded " * 2,
        "procedures_full": steps,
    }
    rec.update(over)
    return rec


class TestFlatProceduresFullAlias:
    def test_empty_db_field_is_filled_from_flat_payload(self, merge_mod, tmp_path, dbs):
        steps = ["Verify the code with a scan tool", "Inspect the sensor wiring for damage",
                 "Test sensors A and B individually with a multimeter"]
        rc = _run(merge_mod, tmp_path, dbs, [_steps_rec("P2851", steps, "https://obd2.com/dtc/p2851")])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert db["P2851"]["procedures_full"] == steps
        assert db["P2851"]["procedures_source_url"] == "https://obd2.com/dtc/p2851"

    def test_dry_run_does_not_write(self, merge_mod, tmp_path, dbs):
        before = dbs[0].read_bytes()
        _run(merge_mod, tmp_path, dbs,
             [_steps_rec("P2851", ["A step that is long enough to pass"], "https://obd2.com/dtc/p2851")],
             apply=False)
        assert dbs[0].read_bytes() == before

    def test_already_full_keeps_second_source_as_multi(self, merge_mod, tmp_path, dbs):
        steps = ["Use a scan tool to confirm the presence of P2035",
                 "Locate the affected EGT sensor per OEM service information",
                 "Visually inspect the sensor and harness for heat damage"]
        rc = _run(merge_mod, tmp_path, dbs, [_steps_rec("P2035", steps, "https://obd2.com/dtc/p2035")])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        entry = db["P2035"]
        # original source untouched
        assert entry["procedures_full"] == ["Inspect the exhaust gas temperature sensor wiring"]
        assert entry["procedures_source_url"] == "https://faultcode.org/car/p2035"
        # second source preserved
        multi = entry["procedures_full_multi"]
        assert len(multi) == 1
        assert multi[0]["steps"] == steps
        assert multi[0]["source_url"] == "https://obd2.com/dtc/p2035"

    def test_second_apply_is_byte_identical(self, merge_mod, tmp_path, dbs):
        steps = ["Verify the code with a scan tool", "Inspect the sensor wiring for damage"]
        rec = _steps_rec("P2851", steps, "https://obd2.com/dtc/p2851")
        _run(merge_mod, tmp_path, dbs, [rec])
        after_first = dbs[0].read_bytes()
        _run(merge_mod, tmp_path, dbs, [rec])
        assert dbs[0].read_bytes() == after_first

    def test_second_apply_does_not_duplicate_multi(self, merge_mod, tmp_path, dbs):
        steps = ["Use a scan tool to confirm the presence of P2035",
                 "Locate the affected EGT sensor per OEM service information"]
        rec = _steps_rec("P2035", steps, "https://obd2.com/dtc/p2035")
        _run(merge_mod, tmp_path, dbs, [rec])
        _run(merge_mod, tmp_path, dbs, [rec])
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert len(db["P2035"]["procedures_full_multi"]) == 1

    def test_procedures_steps_key_still_wins(self, merge_mod, tmp_path, dbs):
        """The adapter-native key keeps priority when both are present."""
        rec = _steps_rec("P2851", ["flat payload step that should lose"],
                         "https://obd2.com/dtc/p2851")
        rec["procedures_steps"] = ["adapter native step that should win"]
        _run(merge_mod, tmp_path, dbs, [rec])
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert db["P2851"]["procedures_full"] == ["adapter native step that should win"]

    def test_record_count_frozen_and_unknown_key_skipped(self, merge_mod, tmp_path, dbs):
        _run(merge_mod, tmp_path, dbs,
             [_steps_rec("P9999", ["step for an unknown code"], "https://obd2.com/dtc/p9999")])
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert "P9999" not in db
        assert set(db) == {"P2851", "P2035"}


class TestShapeGuard:
    """The alias accepts DB-shaped input, so it must reject DB-shaped garbage.

    A list of dicts (J1939-style bundle rows that occasionally sit on DTC rows)
    would be stringified into "{'source_url': ...}" noise by _norm_list. The
    copilot's own reader guards for this shape; the merge must too.
    """

    def test_list_of_dicts_is_not_filled(self, merge_mod, tmp_path, dbs):
        rec = {
            "code": "P2851",
            "url": "https://obd2.com/dtc/p2851",
            "source": "obd2.com",
            "evidence": "Shift fork position sensor correlation fault recorded " * 2,
            "procedures_full": [
                {"source_url": "https://x.example/1", "steps": ["real step"]},
            ],
        }
        _run(merge_mod, tmp_path, dbs, [rec])
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert "procedures_full" not in db["P2851"]
        assert "procedures_full_multi" not in db["P2851"]

    def test_mixed_list_is_rejected_whole(self, merge_mod, tmp_path, dbs):
        rec = {
            "code": "P2851",
            "url": "https://obd2.com/dtc/p2851",
            "source": "obd2.com",
            "evidence": "Shift fork position sensor correlation fault recorded " * 2,
            "procedures_full": ["a real step", {"steps": ["nested"]}],
        }
        _run(merge_mod, tmp_path, dbs, [rec])
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert "procedures_full" not in db["P2851"]

    def test_bundle_path_unaffected_by_guard(self, merge_mod, tmp_path, dbs):
        """A proper procedures_full_append bundle is unaffected by the guard.

        T69 contract: a bundle is an ADDITIONAL source and lands in
        procedures_full_multi (never in the single-source field)."""
        rec = {
            "code": "P2851",
            "url": "https://obd2.com/dtc/p2851",
            "source": "obd2.com",
            "evidence": "Shift fork position sensor correlation fault recorded " * 2,
            "procedures_full_append": {"steps": ["bundle step one", "bundle step two"]},
        }
        _run(merge_mod, tmp_path, dbs, [rec])
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        multi = db["P2851"]["procedures_full_multi"]
        assert multi[0]["steps"] == ["bundle step one", "bundle step two"]
        assert "procedures_full" not in db["P2851"]

# -*- coding: utf-8 -*-
"""T80 regression lock — a shifted `title_en` must never re-enter the DB.

THE DEFECT (measured 2026-09-26, shipped DB 14,484 records)
-----------------------------------------------------------
`data/diagnostics/dtc_database.json` carries two meaning layers: a curated
`title` (Wal33D/dtc-database + TR translation) and a scraped `title_en`
(OBDex / openlaborproject / obdhut / theerrorcodes). On **217 codes** the
`title_en` is the `title` of a DIFFERENT record and differs from the code's
own meaning — a source-side off-by-N shift. Examples:

    U0116  title    : Lost Communication with Coolant Temperature Control Module
           title_en : Lost Communication With Fuel Injector Control Module
                      (== U0105's meaning)
    C0091  title    : 4WD/AWD Power Transfer Unit Position Sensor A
           title_en : Brake Booster Performance  (== C0021's title)

`scripts/t66_merge.py` only ever checked "is the DB field empty?", so the
SAME shifted text could be re-imported by any future harvest of the same
broken source — and a *correct* text could never replace it (fill-if-empty).

LOCKED CONTRACT (what the tests below pin)
------------------------------------------
  * a `title_en` payload that equals ANOTHER record's `title` while differing
    from this record's own `title` is REJECTED and counted under
    `skipped.title_en_shift`; every other field on the same record still
    merges (no all-or-nothing loss);
  * polarity is significant: "(+) Open" and "(-) Open" are DIFFERENT codes
    and must never be folded together by the comparison;
  * a payload that matches the record's OWN `title` (a legitimate reword /
    casing / punctuation variant) is still accepted;
  * a payload with no match anywhere is still accepted (normal path);
  * the guard is idempotent and the record count stays frozen.

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
    spec = importlib.util.spec_from_file_location("t66_merge_t80", MERGE_PATH)
    assert spec and spec.loader, "merge module must be loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.fixture(scope="module")
def merge_mod():
    return _load_merge_module()

@pytest.fixture()
def dbs(tmp_path: Path):
    """A miniature two-layer DB with the shipped-DB shift pairs.

    Both pairs are verbatim from the real DB:
      * C0091.title_en == C0021.title (exact)  — the 217-code class
      * U0012 / U0015 — the polarity pair that must NOT be folded together
    """
    dtc_db = tmp_path / "dtc_database.json"
    j1939_db = tmp_path / "j1939_spn_fmi_database.json"
    dtc_db.write_text(
        json.dumps(
            {
                "C0091": {"title": "4WD/AWD Power Transfer Unit Position Sensor A"},
                "C0021": {"title": "Brake Booster Performance"},
                "U0012": {"title": "Medium Speed CAN Communication Bus (+) Open"},
                "U0015": {"title": "Medium Speed CAN Communication Bus (-) Open"},
                "P0420": {"title": "Catalyst System Efficiency Below Threshold Bank 1"},
            },
            ensure_ascii=False, indent=1,
        ) + "\n",
        encoding="utf-8",
    )
    j1939_db.write_text(
        json.dumps({"metadata": {"total_spns": 0}, "spns": {}},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return dtc_db, j1939_db

def _run(merge_mod, tmp_path: Path, dbs, records, apply=True):
    inp = tmp_path / "mr_t80.json"
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

def _rec(code: str, url: str, **over) -> dict:
    rec = {
        "code": code,
        "url": url,
        "source": url.split("/")[2],
        "evidence": "Measured shift between the DB title layers " * 2,
    }
    rec.update(over)
    return rec

def _report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# 1. The shift is rejected
# ---------------------------------------------------------------------------

class TestForeignShiftRejected:
    def test_foreign_title_payload_is_dropped(self, merge_mod, tmp_path, dbs):
        # C0091 would receive C0021's meaning — the exact shipped-DB defect.
        rc = _run(merge_mod, tmp_path, dbs, [
            _rec("C0091", "https://example.test/c0091",
                 title_en="Brake Booster Performance"),
        ])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert "title_en" not in db["C0091"], "shifted payload must not land"
        assert _report(tmp_path)["skipped"]["title_en_shift"] == 1

    def test_other_fields_on_the_same_record_still_merge(self, merge_mod, tmp_path, dbs):
        """Rejecting title_en must not discard the rest of the payload."""
        rc = _run(merge_mod, tmp_path, dbs, [
            _rec("C0091", "https://example.test/c0091",
                 title_en="Brake Booster Performance",
                 causes_en="Open circuit in the transfer unit position sensor wiring"),
        ])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert "title_en" not in db["C0091"]
        assert (db["C0091"]["causes_en"]
                == "Open circuit in the transfer unit position sensor wiring")
        assert _report(tmp_path)["skipped"]["title_en_shift"] == 1

    def test_polarity_is_significant(self, merge_mod, tmp_path, dbs):
        """U0012's "(+) Open" must never be accepted as U0015's "(-) Open"."""
        rc = _run(merge_mod, tmp_path, dbs, [
            _rec("U0015", "https://example.test/u0015",
                 title_en="Medium Speed CAN Communication Bus (+) Open"),
        ])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert "title_en" not in db["U0015"]
        assert _report(tmp_path)["skipped"]["title_en_shift"] == 1

    def test_shift_with_case_and_punctuation_noise_is_still_caught(
            self, merge_mod, tmp_path, dbs):
        rc = _run(merge_mod, tmp_path, dbs, [
            _rec("C0091", "https://example.test/c0091",
                 title_en="  brake booster performance  "),
        ])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert "title_en" not in db["C0091"]
        assert _report(tmp_path)["skipped"]["title_en_shift"] == 1

# ---------------------------------------------------------------------------
# 2. Legitimate payloads still land
# ---------------------------------------------------------------------------

class TestLegitimatePayloadAccepted:
    def test_own_title_reword_is_accepted(self, merge_mod, tmp_path, dbs):
        """A casing/punctuation variant of the record's OWN title is legit."""
        rc = _run(merge_mod, tmp_path, dbs, [
            _rec("P0420", "https://example.test/p0420",
                 title_en="catalyst system efficiency below threshold - bank 1"),
        ])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert db["P0420"]["title_en"] == "catalyst system efficiency below threshold - bank 1"
        assert _report(tmp_path)["skipped"].get("title_en_shift", 0) == 0

    def test_unmatched_title_is_accepted(self, merge_mod, tmp_path, dbs):
        """A brand-new meaning that matches no other record's title passes."""
        rc = _run(merge_mod, tmp_path, dbs, [
            _rec("U0012", "https://example.test/u0012",
                 title_en="CAN Bus A (+) Open Circuit"),
        ])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert db["U0012"]["title_en"] == "CAN Bus A (+) Open Circuit"
        assert _report(tmp_path)["skipped"].get("title_en_shift", 0) == 0

    def test_empty_db_field_still_fills_normally(self, merge_mod, tmp_path, dbs):
        rc = _run(merge_mod, tmp_path, dbs, [
            _rec("U0012", "https://example.test/u0012",
                 title_en="Medium Speed CAN Bus A (+) Open",
                 description_en="The bus half is open."),
        ])
        assert rc == 0
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert db["U0012"]["title_en"] == "Medium Speed CAN Bus A (+) Open"
        assert db["U0012"]["title_en_source"] == "https://example.test/u0012"

# ---------------------------------------------------------------------------
# 3. Idempotency + count freeze
# ---------------------------------------------------------------------------

class TestIdempotencyAndCountFreeze:
    def test_second_apply_is_byte_identical(self, merge_mod, tmp_path, dbs):
        recs = [
            _rec("C0091", "https://example.test/c0091",
                 title_en="Brake Booster Performance",
                 causes_en="Open transfer unit position sensor wiring"),
            _rec("U0012", "https://example.test/u0012",
                 title_en="CAN Bus A (+) Open Circuit"),
        ]
        assert _run(merge_mod, tmp_path, dbs, recs) == 0
        after_first = dbs[0].read_bytes()
        assert _run(merge_mod, tmp_path, dbs, recs) == 0
        assert dbs[0].read_bytes() == after_first

    def test_dry_run_reports_the_same_shift_count(self, merge_mod, tmp_path, dbs):
        recs = [_rec("C0091", "https://example.test/c0091",
                     title_en="Brake Booster Performance")]
        assert _run(merge_mod, tmp_path, dbs, recs, apply=False) == 0
        dry = _report(tmp_path)["skipped"]["title_en_shift"]
        assert dry == 1
        assert _run(merge_mod, tmp_path, dbs, recs, apply=True) == 0
        assert _report(tmp_path)["skipped"]["title_en_shift"] == dry

    def test_record_count_frozen(self, merge_mod, tmp_path, dbs):
        before = len(json.loads(dbs[0].read_text(encoding="utf-8")))
        assert _run(merge_mod, tmp_path, dbs, [
            _rec("C0091", "https://example.test/c0091",
                 title_en="Brake Booster Performance"),
        ]) == 0
        after = len(json.loads(dbs[0].read_text(encoding="utf-8")))
        assert after == before == 5

# ---------------------------------------------------------------------------
# 4. The helper itself (unit-level, no merge run)
# ---------------------------------------------------------------------------

class TestTitleHelpers:
    def test_norm_key_keeps_polarity(self, merge_mod):
        k = merge_mod._title_norm_key
        assert k("Bus (+) Open") != k("Bus (-) Open")
        assert k("Bus (+) Open") == k("bus ( + ) open")

    def test_index_maps_every_title(self, merge_mod):
        idx = merge_mod._title_index({
            "A1": {"title": "Alpha Beta"},
            "A2": {"title": "alpha  beta"},   # same key after normalization
            "A3": {},
        })
        assert idx["alpha beta"] == {"A1", "A2"}

    def test_foreign_shift_predicate(self, merge_mod):
        idx = merge_mod._title_index({
            "C0091": {"title": "4WD/AWD Power Transfer Unit Position Sensor A"},
            "C0021": {"title": "Brake Booster Performance"},
        })
        f = merge_mod._title_en_is_foreign_shift
        assert f("Brake Booster Performance",
                 "4WD/AWD Power Transfer Unit Position Sensor A",
                 idx, "C0091") is True
        assert f("4WD/AWD Power Transfer Unit Position Sensor A",
                 "4WD/AWD Power Transfer Unit Position Sensor A",
                 idx, "C0091") is False
        assert f("Something Else Entirely",
                 "4WD/AWD Power Transfer Unit Position Sensor A",
                 idx, "C0091") is False

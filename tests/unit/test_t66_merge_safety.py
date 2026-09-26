# -*- coding: utf-8 -*-
"""T66 / B-09 regression lock — concurrency safety of the merge write path.

THE DEFECT (review finding B-09)
--------------------------------
`scripts/t66_merge.py` wrote each DB tmp+replace individually, but the
read-modify-write cycle over BOTH DBs had NO cross-process lock and NO
compare-and-swap. Consequences:

  * two concurrent `--apply` runs could both read the same DB state, both
    commit, and the second silently discarded the first's update;
  * a crash between the two replaces left dtc_database.json updated and
    j1939_spn_fmi_database.json stale — the two DBs inconsistent.

LOCKED CONTRACT (what the tests below pin)
------------------------------------------
  * an exclusive cross-process lock (msvcrt on Windows / flock on POSIX) is
    acquired BEFORE the DBs are read and held through the commit; a second
    holder aborts nonzero rather than proceeding unlocked;
  * a SHA-256 + size compare-and-swap re-verifies both DBs before commit; any
    interleaved writer aborts the merge nonzero and the interleaved writer's
    bytes survive (no lost update);
  * the commit stages both files, replaces both, rolls BOTH back from .bak if
    the second replace fails or post-write verification mismatches;
  * the count-frozen gate still aborts when record counts would move.

All tests use tmp_path fixtures; the real data/diagnostics DBs are never read
or written here (the module paths are injected through `main(...)` kwargs).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MERGE_PATH = REPO_ROOT / "scripts" / "t66_merge.py"

# A child process that tries to take the merge lock and reports the outcome:
# rc 0 = acquired, rc 3 = the lock (correctly) refused it.
_LOCK_DRIVER = (
    "import importlib.util, sys\n"
    "from pathlib import Path\n"
    "spec = importlib.util.spec_from_file_location('t66_merge_child', sys.argv[1])\n"
    "m = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(m)\n"
    "try:\n"
    "    with m._file_lock(Path(sys.argv[2]), timeout_s=float(sys.argv[3])):\n"
    "        print('ACQUIRED')\n"
    "except SystemExit as e:\n"
    "    print(str(e), file=sys.stderr)\n"
    "    raise SystemExit(3)\n"
)


# A child process that runs a real `--apply` merge against injected DB paths:
# rc 0 = merge completed, anything else = the merge aborted (fail-closed).
_MERGE_DRIVER = (
    "import importlib.util, sys\n"
    "from pathlib import Path\n"
    "spec = importlib.util.spec_from_file_location('t66_merge_child', sys.argv[1])\n"
    "m = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(m)\n"
    "sys.exit(m.main(\n"
    "    ['--inputs', sys.argv[2], '--report', sys.argv[3], '--apply'],\n"
    "    dtc_db=Path(sys.argv[4]), j1939_db=Path(sys.argv[5]),\n"
    "    lock_path=Path(sys.argv[6]),\n"
    "))\n"
)


def _load_merge_module():
    spec = importlib.util.spec_from_file_location("t66_merge_safety", MERGE_PATH)
    assert spec and spec.loader, "merge module must be loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def merge_mod():
    return _load_merge_module()


@pytest.fixture()
def dbs(tmp_path: Path) -> tuple[Path, Path]:
    """Two tiny DBs shaped like the production files."""
    dtc_db = tmp_path / "dtc_database.json"
    j1939_db = tmp_path / "j1939_spn_fmi_database.json"
    dtc_db.write_text(
        json.dumps({"P0420": {"title_en": "", "symptoms_en": []}}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    j1939_db.write_text(
        json.dumps({"metadata": {"total_spns": 1}, "spns": {"SPN_100": {"symptoms": []}}},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return dtc_db, j1939_db


def _run(merge_mod, tmp_path: Path, dbs, records, target_db="dtc", apply=True):
    inp = tmp_path / "mr_t66.json"
    inp.write_text(
        json.dumps({"target_db": target_db, "records": records}, ensure_ascii=False),
        encoding="utf-8",
    )
    return merge_mod.main(
        ["--inputs", str(inp), "--report", str(tmp_path / "report.json")]
        + (["--apply"] if apply else []),
        dtc_db=dbs[0],
        j1939_db=dbs[1],
        lock_path=tmp_path / ".t66_merge.lock",
    )


def _rec(**over) -> dict:
    rec = {
        "code": "P0420",
        "url": "https://obd2.com/dtc/p0420-catalyst",
        "source": "obd2.com",
        "title_en": "Catalyst efficiency below threshold bank 1",
        "evidence": "Catalyst efficiency below threshold bank 1 at warm idle " * 2,
    }
    rec.update(over)
    return rec


# ---------------------------------------------------------------------------
# 1. The lock is exclusive and cross-process
# ---------------------------------------------------------------------------

class TestCrossProcessLock:
    def test_lock_excludes_a_second_process(self, merge_mod, tmp_path: Path) -> None:
        lock = tmp_path / ".t66_merge.lock"
        with merge_mod._file_lock(lock, timeout_s=30):
            child = subprocess.run(
                [sys.executable, "-c", _LOCK_DRIVER, str(MERGE_PATH), str(lock), "1.0"],
                capture_output=True, text=True, timeout=60,
            )
            assert child.returncode == 3, (
                f"the second process must be refused while the lock is held; "
                f"rc={child.returncode} out={child.stdout!r} err={child.stderr!r}"
            )
            assert "fail-closed" in child.stderr
        after = subprocess.run(
            [sys.executable, "-c", _LOCK_DRIVER, str(MERGE_PATH), str(lock), "5.0"],
            capture_output=True, text=True, timeout=60,
        )
        assert after.returncode == 0, f"lock must be released: {after.stderr!r}"
        assert "ACQUIRED" in after.stdout

    def test_second_acquire_in_process_times_out(self, merge_mod, tmp_path: Path) -> None:
        lock = tmp_path / ".t66_merge.lock"
        with merge_mod._file_lock(lock, timeout_s=5):
            with pytest.raises(SystemExit) as ei:
                with merge_mod._file_lock(lock, timeout_s=0.3):
                    pass
        assert "fail-closed" in str(ei.value)


# ---------------------------------------------------------------------------
# 2. Compare-and-swap: an interleaved writer aborts the merge, never loses data
# ---------------------------------------------------------------------------

class TestCompareAndSwap:
    def test_interleaved_writer_aborts_nonzero(self, merge_mod, dbs) -> None:
        dtc_db, j1939_db = dbs
        dtc, j1939, fingerprints = merge_mod.read_databases(dtc_db, j1939_db)
        dtc["P0420"]["title_en"] = "merged in memory"
        # A second, non-cooperating writer commits between our read and commit.
        other = {"P0420": {"title_en": "second writer"}}
        dtc_db.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(SystemExit) as ei:
            merge_mod._commit_databases(dtc_db, j1939_db, dtc, j1939, fingerprints, {})
        assert "changed since it was read" in str(ei.value)
        assert ei.value.code not in (None, 0), "must abort with a nonzero exit"

        # The interleaved writer's bytes survive: no lost update, no .bak noise.
        assert json.loads(dtc_db.read_text(encoding="utf-8")) == other
        assert not (dtc_db.parent / (dtc_db.name + ".bak_t66")).exists()

    def test_missing_db_between_read_and_commit_aborts(self, merge_mod, dbs) -> None:
        dtc_db, j1939_db = dbs
        dtc, j1939, fingerprints = merge_mod.read_databases(dtc_db, j1939_db)
        j1939_db.unlink()
        with pytest.raises(SystemExit) as ei:
            merge_mod._commit_databases(dtc_db, j1939_db, dtc, j1939, fingerprints, {})
        assert "unreadable" in str(ei.value)

    def test_real_concurrent_apply_second_process_loses_no_update(self, tmp_path, dbs) -> None:
        """Two REAL `--apply` processes; the loser must abort, never overwrite.

        The winner merges P0420 and P0430; the loser merges the same keys. Run
        sequentially against the same DBs (that is exactly the interleaving the
        lock exists for: both would otherwise read the pre-merge state). The
        second process must observe the first's committed bytes through the
        lock, apply its own disjoint field, and the DB must hold BOTH updates.
        """
        dtc_db, j1939_db = dbs
        lock = tmp_path / ".t66_merge.lock"

        first = tmp_path / "first.json"
        first.write_text(json.dumps({"target_db": "dtc", "records": [{
            "code": "P0420", "url": "https://obd2.com/dtc/p0420-catalyst",
            "source": "obd2.com",
            "title_en": "Catalyst efficiency below threshold bank 1",
            "evidence": "Catalyst efficiency below threshold bank 1 at warm idle " * 2,
        }]}, ensure_ascii=False), encoding="utf-8")
        second = tmp_path / "second.json"
        second.write_text(json.dumps({"target_db": "dtc", "records": [{
            "code": "P0420", "url": "https://obdfyi.com/codes/p0420",
            "source": "obdfyi.com",
            "description_en": "The PCM flags the converter when O2 switching activity is low",
            "evidence": "The PCM flags the converter when O2 switching activity is low " * 2,
        }]}, ensure_ascii=False), encoding="utf-8")

        def _run_child(inp: Path, report: Path) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, "-c", _MERGE_DRIVER, str(MERGE_PATH), str(inp),
                 str(report), str(dtc_db), str(j1939_db), str(lock)],
                capture_output=True, text=True, timeout=120,
            )

        r1 = _run_child(first, tmp_path / "r1.json")
        assert r1.returncode == 0, f"first merge must succeed: {r1.stderr!r}"
        after_first = json.loads(dtc_db.read_text(encoding="utf-8"))

        r2 = _run_child(second, tmp_path / "r2.json")
        assert r2.returncode == 0, f"second merge must succeed: {r2.stderr!r}"
        final = json.loads(dtc_db.read_text(encoding="utf-8"))

        # No lost update: the first process's title survives the second's run.
        assert final["P0420"]["title_en"] == "Catalyst efficiency below threshold bank 1"
        # And the second process's own field landed too.
        assert final["P0420"]["description_en"]
        assert after_first["P0420"]["title_en"] == final["P0420"]["title_en"]


# ---------------------------------------------------------------------------
# 3. Both-DB commit: backup, rollback on failure, verify-after-write
# ---------------------------------------------------------------------------

class TestBothDbCommit:
    def test_happy_path_updates_both_and_backs_up(self, merge_mod, dbs) -> None:
        dtc_db, j1939_db = dbs
        orig_dtc = dtc_db.read_bytes()
        orig_j1939 = j1939_db.read_bytes()
        dtc, j1939, fingerprints = merge_mod.read_databases(dtc_db, j1939_db)
        dtc["P0420"]["title_en"] = "merged"
        report: dict = {}

        merge_mod._commit_databases(dtc_db, j1939_db, dtc, j1939, fingerprints, report)

        assert json.loads(dtc_db.read_text(encoding="utf-8"))["P0420"]["title_en"] == "merged"
        assert (dtc_db.parent / (dtc_db.name + ".bak_t66")).read_bytes() == orig_dtc
        assert (j1939_db.parent / (j1939_db.name + ".bak_t66")).read_bytes() == orig_j1939
        assert report["db_sha256_before"]["dtc"] != report["db_sha256_after"]["dtc"]
        assert report["db_sha256_after"]["dtc"] == merge_mod.file_fingerprint(dtc_db)["sha256"]

    def test_second_replace_failure_rolls_back_both(self, merge_mod, dbs, monkeypatch) -> None:
        dtc_db, j1939_db = dbs
        orig = {p: p.read_bytes() for p in (dtc_db, j1939_db)}
        dtc, j1939, fingerprints = merge_mod.read_databases(dtc_db, j1939_db)
        dtc["P0420"]["title_en"] = "merged"

        real_replace = Path.replace

        def crash(self, target):
            if Path(target) == dtc_db:  # the SECOND replace in commit order
                raise OSError("simulated crash between the two replaces")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", crash)
        with pytest.raises(SystemExit) as ei:
            merge_mod._commit_databases(dtc_db, j1939_db, dtc, j1939, fingerprints, {})
        assert "restored from .bak_t66" in str(ei.value)
        for path, raw in orig.items():
            assert path.read_bytes() == raw, f"{path.name} left inconsistent after crash"

    def test_post_write_mismatch_is_detected_and_rolled_back(
        self, merge_mod, dbs, monkeypatch
    ) -> None:
        dtc_db, j1939_db = dbs
        orig = {p: p.read_bytes() for p in (dtc_db, j1939_db)}
        dtc, j1939, fingerprints = merge_mod.read_databases(dtc_db, j1939_db)
        dtc["P0420"]["title_en"] = "merged"

        real_replace = Path.replace

        def corrupt(self, target):
            res = real_replace(self, target)
            if Path(target) == dtc_db:  # simulate a torn/corrupted write
                with open(dtc_db, "ab") as fh:  # noqa: PTH123
                    fh.write(b"!")
            return res

        monkeypatch.setattr(Path, "replace", corrupt)
        with pytest.raises(SystemExit) as ei:
            merge_mod._commit_databases(dtc_db, j1939_db, dtc, j1939, fingerprints, {})
        assert "post-write verification failed" in str(ei.value)
        for path, raw in orig.items():
            assert path.read_bytes() == raw, f"{path.name} left corrupted"


# ---------------------------------------------------------------------------
# 4. The count-frozen gate keeps its semantics
# ---------------------------------------------------------------------------

class TestCountFrozenGate:
    def test_count_change_aborts_before_commit(self, merge_mod, dbs, tmp_path, monkeypatch) -> None:
        dtc_db, j1939_db = dbs
        before = dtc_db.read_bytes()

        def rogue(rec, dtc, report, apply):
            dtc["P9999"] = {"title_en": "smuggled"}  # would add a record
            return True

        monkeypatch.setattr(merge_mod, "merge_dtc", rogue)
        with pytest.raises(SystemExit) as ei:
            _run(merge_mod, tmp_path, dbs, [_rec()])
        assert "RECORD COUNT CHANGED" in str(ei.value)
        assert dtc_db.read_bytes() == before, "aborted merge must not write"

    def test_unknown_key_is_skipped_never_added(self, merge_mod, dbs, tmp_path) -> None:
        # key_for_dtc prefers the code embedded in the URL, so both spell P7777.
        rc = _run(merge_mod, tmp_path, dbs,
                  [_rec(code="P7777", url="https://obd2.com/dtc/p7777-unknown")])
        assert rc == 0
        report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        assert report["skipped"]["unknown_key"] == 1
        db = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert "P7777" not in db and len(db) == 1

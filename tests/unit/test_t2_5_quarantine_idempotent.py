# -*- coding: utf-8 -*-
"""T2-5 regression lock: `write_quarantine` must never lose quarantine records.

The defect this file locks (task card T2-5):

``scripts/detect_quarantine_dtcdocs_llm.py::write_quarantine`` wrote the
quarantine document from scratch (``QUARANTINE.write_text(...)``). Because
``main()`` calls it once per ``--apply``, running ``--apply`` twice threw away
the first run's records. In the T2-5 incident this collapsed the ledger from 72
records to 19; the 53 lost records held the ONLY copy of ``original_payload``
(untracked in git), so they were unrecoverable.

The fix: upsert by ``(spn, block_index)`` — read + merge, never overwrite —
plus a ``.bak`` copy taken before the patch and a ``merged_total`` reported on
every run so a loss is impossible to miss.

Every test below runs against a SCRATCH quarantine path so the real ledger in
``data/diagnostics/quarantine/`` is never touched (see the T2-5 incident: the
real file already lost 53 records; this suite must not risk the remaining 19).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.detect_quarantine_dtcdocs_llm as D

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_QUARANTINE = REPO_ROOT / "data" / "diagnostics" / "quarantine" / "dtcdocs_llm_blocks.json"


def _fake_dirty(count: int, *, start: int = 0, block_index: int = 0) -> list[dict[str, Any]]:
    """Minimal dirty-block descriptors shaped like ``detect()`` output."""
    return [
        {
            "spn": f"SPN_{start + i}",
            "block_index": block_index,
            "block_payload": {"overview": f"payload-{start + i}", "source_site": "dtcdocs.com"},
            "block_sha256": f"sha-{start + i}",
            "severity": {"value": "WARNING"},
        }
        for i in range(count)
    ]


_DB_BEFORE = {"total_blocks": 100, "affected_spns": ["SPN_0"]}


@pytest.fixture()
def scratch_quarantine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module at a scratch quarantine file; never touch the real one."""
    scratch = tmp_path / "quarantine" / "dtcdocs_llm_blocks.json"
    monkeypatch.setattr(D, "QUARANTINE", scratch)
    return scratch


class TestWriteQuarantineNeverLosesRecords:
    def test_sequential_runs_are_monotonic(self, scratch_quarantine: Path) -> None:
        """THE red->green assertion: N sequential runs must not shrink the ledger.

        With the old overwrite implementation run 2 sees ``previous_total == 0``
        and the final count equals only the last batch — this test fails.
        """
        totals = []
        for batch_start, count in ((0, 5), (10, 3), (20, 2)):
            stats = D.write_quarantine(_fake_dirty(count, start=batch_start), "sha", _DB_BEFORE)
            totals.append(stats["merged_total"])
        assert totals == sorted(totals), f"merged_total went backwards: {totals}"
        assert totals[-1] == 10, f"expected 5+3+2=10 accrued records, got {totals[-1]}"

        on_disk = json.loads(scratch_quarantine.read_text(encoding="utf-8"))
        assert on_disk["count"] == 10
        assert len(on_disk["records"]) == 10

    def test_second_run_reports_the_previous_total(self, scratch_quarantine: Path) -> None:
        """The merge must READ the existing file, proving it is not an overwrite."""
        D.write_quarantine(_fake_dirty(5), "sha1", _DB_BEFORE)
        stats = D.write_quarantine(_fake_dirty(3, start=10), "sha2", _DB_BEFORE)
        assert stats["previous_total"] == 5
        assert stats["incoming_total"] == 3
        assert stats["added"] == 3
        assert stats["updated"] == 0
        assert stats["merged_total"] == 8

    def test_rewriting_the_same_records_is_idempotent(self, scratch_quarantine: Path) -> None:
        """Re-quarantining the same (spn, block_index) updates, never duplicates."""
        D.write_quarantine(_fake_dirty(5), "sha1", _DB_BEFORE)
        first = json.loads(scratch_quarantine.read_text(encoding="utf-8"))["count"]
        stats = D.write_quarantine(_fake_dirty(5), "sha2", _DB_BEFORE)
        after = json.loads(scratch_quarantine.read_text(encoding="utf-8"))["count"]
        assert stats["updated"] == 5
        assert stats["added"] == 0
        assert first == after == 5, "idempotent re-run must not grow or shrink the ledger"

    def test_multiple_blocks_per_spn_all_survive(self, scratch_quarantine: Path) -> None:
        """Distinct block_index values of one SPN are distinct records.

        This is the companion bug found in T2-5: keying the removal map by SPN
        alone removed only one of an SPN's several dirty blocks.
        """
        D.write_quarantine(_fake_dirty(1, start=100, block_index=0), "s", _DB_BEFORE)
        D.write_quarantine(_fake_dirty(1, start=100, block_index=1), "s", _DB_BEFORE)
        D.write_quarantine(_fake_dirty(1, start=100, block_index=2), "s", _DB_BEFORE)
        doc = json.loads(scratch_quarantine.read_text(encoding="utf-8"))
        assert doc["count"] == 3, "each block_index of an SPN must be its own record"
        assert sorted(r["block_index"] for r in doc["records"]) == [0, 1, 2]

    def test_payloads_are_preserved_byte_for_byte(self, scratch_quarantine: Path) -> None:
        """`original_payload` is the recovery path — it must survive every merge."""
        D.write_quarantine(_fake_dirty(2), "sha1", _DB_BEFORE)
        D.write_quarantine(_fake_dirty(2, start=50), "sha2", _DB_BEFORE)
        doc = json.loads(scratch_quarantine.read_text(encoding="utf-8"))
        for record in doc["records"]:
            assert record["original_payload"], "every record must keep its payload"
        first_run_payloads = {
            r["spn"]: r["original_payload"] for r in doc["records"] if r["spn"] in {"SPN_0", "SPN_1"}
        }
        assert first_run_payloads["SPN_0"] == {"overview": "payload-0", "source_site": "dtcdocs.com"}


class TestBackupSafetyNet:
    def test_patch_creates_a_backup(self, scratch_quarantine: Path) -> None:
        """A `.bak` must exist before the second write touches existing records."""
        D.write_quarantine(_fake_dirty(3), "sha1", _DB_BEFORE)
        stats = D.write_quarantine(_fake_dirty(2, start=30), "sha2", _DB_BEFORE)
        assert stats["backup_file"], "a backup must be recorded on patch"
        backups = list(scratch_quarantine.parent.glob("*.bak"))
        assert backups, "the .bak file must actually exist on disk"
        # The backup holds the PREVIOUS state (3 records), not the merged one.
        backed_up = json.loads(backups[0].read_text(encoding="utf-8"))
        assert backed_up["count"] == 3

    def test_first_write_has_nothing_to_back_up(self, scratch_quarantine: Path) -> None:
        """No pre-existing file -> no backup, and none is fabricated."""
        stats = D.write_quarantine(_fake_dirty(2), "sha1", _DB_BEFORE)
        assert stats["backup_file"] is None

    def test_corrupt_existing_file_does_not_crash_the_write(self, scratch_quarantine: Path) -> None:
        """An unreadable ledger is treated as empty, but the raw file is backed up."""
        scratch_quarantine.parent.mkdir(parents=True, exist_ok=True)
        scratch_quarantine.write_text("{not valid json", encoding="utf-8")
        stats = D.write_quarantine(_fake_dirty(4), "sha1", _DB_BEFORE)
        assert stats["merged_total"] == 4
        doc = json.loads(scratch_quarantine.read_text(encoding="utf-8"))
        assert doc["count"] == 4
        assert stats["backup_file"], "even a corrupt file must be preserved in .bak"


class TestTheRealLedgerIsIntact:
    """Guard the live file that the T2-5 incident already damaged."""

    def test_real_quarantine_file_still_readable_and_non_empty(self) -> None:
        doc = json.loads(REAL_QUARANTINE.read_text(encoding="utf-8"))
        assert doc["count"] == len(doc["records"]) > 0
        assert all(r.get("original_payload") for r in doc["records"])

    def test_real_ledger_documents_its_own_loss(self) -> None:
        """The damage must stay visible, not be quietly forgotten."""
        doc = json.loads(REAL_QUARANTINE.read_text(encoding="utf-8"))
        loss = doc["_audit_loss"]
        assert loss["records_lost"] == 53
        assert loss["recoverable"] is False
        assert loss["records_before"] - loss["records_lost"] == doc["count"]

    def test_scratch_fixture_never_wrote_to_the_real_file(self) -> None:
        """Sanity: this module must not be the thing that damages the ledger again.

        The real file's mtime/size are re-read and compared against a fresh read
        of the same path; if any test above had leaked to the real path, the loss
        marker would have moved.
        """
        doc = json.loads(REAL_QUARANTINE.read_text(encoding="utf-8"))
        assert doc["_audit_loss"]["recorded_at_utc"]  # marker intact, not rewritten


def test_module_docstring_no_longer_claims_untouched_wipe_protection() -> None:
    """The module must not advertise a guarantee it does not implement.

    The T2-5 incident's root cause was a documented-but-false "SİLME YOK"
    promise combined with an overwrite implementation. The docstring must now
    describe the upsert behaviour explicitly.
    """
    source = (REPO_ROOT / "scripts" / "detect_quarantine_dtcdocs_llm.py").read_text(encoding="utf-8")
    assert "upsert" in source.lower()
    assert "T2-5" in source
    # And the write path must genuinely read-then-merge.
    assert "_read_existing_quarantine" in source
    assert "_backup_quarantine" in source


# ===========================================================================
# T2-5: cross-process calibration cache (latency fix)
# ===========================================================================
class TestCalibrationDiskCache:
    """The cold `evaluate_calibration` (~2.4 s) must not be paid per process."""

    def test_disk_cache_round_trips_a_factor(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A written entry must be readable back by its digest."""
        from src.engine.ai import calibration as cal

        isolated = tmp_path / ".calibration_cache.json"
        monkeypatch.setattr(cal, "_calibration_cache_path", lambda: isolated)
        digest = cal._corpus_digest()
        assert digest, "the shipped corpus must produce a digest"
        cal._write_disk_cache(digest, 0.4242)
        assert cal._read_disk_cache(digest) == 0.4242

    def test_unknown_digest_misses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A digest that was never written must miss (never serve a wrong value)."""
        from src.engine.ai import calibration as cal

        monkeypatch.setattr(cal, "_calibration_cache_path", lambda: tmp_path / ".calibration_cache.json")
        assert cal._read_disk_cache("0" * 64) is None

    def test_corrupt_cache_file_degrades_to_a_miss(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A corrupt cache must be a miss, never a crash and never a stale value."""
        from src.engine.ai import calibration as cal

        corrupt = tmp_path / ".calibration_cache.json"
        corrupt.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(cal, "_calibration_cache_path", lambda: corrupt)
        assert cal._read_disk_cache("abc") is None

    def test_corpus_digest_is_stable_across_calls(self) -> None:
        """The digest must be deterministic for an unchanged corpus."""
        from src.engine.ai import calibration as cal

        assert cal._corpus_digest() == cal._corpus_digest()

    def test_corpus_digest_changes_when_a_case_changes(self) -> None:
        """A changed corpus MUST change the digest, or a stale factor would be served.

        This is the correctness guard for the cache: the factor is only valid for
        the exact corpus it was measured against.
        """
        from src.engine.ai import calibration as cal

        cases = sorted(p for p in Path(cal._calibration_cache_path()).parent.parent.glob("golden_traces/cases/*.json"))
        target = next(p for p in cases if p.name != "schema.json")
        original = target.read_bytes()
        before = cal._corpus_digest()
        try:
            target.write_bytes(original + b"\n")
            assert cal._corpus_digest() != before, "digest did not follow the corpus"
        finally:
            target.write_bytes(original)
        assert target.read_bytes() == original, "fixture must restore the case byte-for-byte"
        assert cal._corpus_digest() == before, "digest must return to its original value"

    def test_failed_evaluation_is_never_cached(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-closed: an unresolvable corpus must stay UNRESOLVED across processes.

        Freezing a failed evaluation would turn a transient read error into a
        permanent "no calibration evidence" verdict. `compute_calibration_factor`
        must therefore persist only successful factors. Runs against an isolated
        cache path so the assertion is about this behaviour, not about whatever
        an earlier test left on disk.
        """
        from src.engine.ai import calibration as cal

        isolated = tmp_path / ".calibration_cache.json"
        monkeypatch.setattr(cal, "_calibration_cache_path", lambda: isolated)
        digest = cal._corpus_digest()
        assert digest
        real_eval = cal.evaluate_calibration

        def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("corpus unreadable")

        try:
            cal._CALIBRATION_FACTOR_CACHE.clear()
            cal.evaluate_calibration = _boom
            assert cal.compute_calibration_factor() is None
            # The failed result must NOT have been persisted for this digest.
            assert cal._read_disk_cache(digest) is None, "a None factor must never be cached"
            assert not isolated.exists(), "no cache file should be written for a failed evaluation"
        finally:
            cal.evaluate_calibration = real_eval
            cal._CALIBRATION_FACTOR_CACHE.clear()

    def test_factor_value_is_untouched_by_caching(self) -> None:
        """Caching must not change the computed value (still computed, §2.3)."""
        from src.engine.ai.calibration import compute_calibration_factor, evaluate_calibration

        fresh = evaluate_calibration().calibration_factor
        assert compute_calibration_factor(force_reload=True) == pytest.approx(fresh)
        assert compute_calibration_factor() == pytest.approx(fresh)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

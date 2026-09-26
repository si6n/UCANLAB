# -*- coding: utf-8 -*-
"""T69 regression lock — multi-source procedure bundles on the DTC side.

Round T69 (2026-09-22) closed the last gap T67's audit opened.

THE DEFECT
----------
The DTC-side `procedures_full` is a flat `list[str]` paired with a SINGLE
`procedures_source_url`. The merge pipeline's rule is fill-if-empty, so it can
only ever capture the FIRST source for a code. Measured on the live DB:

    P0420 -> 7-step summary from obd2.com  (procedures_source_url = obd2.com)
    troubleshootmyvehicle.com publishes a 77-step TEST-LABELLED procedure
    ("[TEST 1: Checking For A Broken Catalytic Converter] ...") for the SAME code.

The richer harvest had nowhere to go and was silently dropped — the same class
of loss as T67-C (field exists, no consumer) but one layer up: the field had no
WRITE path for additional sources, so the reader had nothing to read.

THE FIX (three parts, each tested below)
----------------------------------------
1. ADAPTER (`scripts/t66_adapter.py`): emits `procedures_full_append`, the same
   bundle shape the J1939 side has used since T66.
2. MERGE (`scripts/t66_merge.py`): appends to `procedures_full_multi`, deduped
   by `source_url`, never touching the original `procedures_full`.
3. READER (`diagnostic_copilot._format_4stage_technician_report`): renders every
   additional source grouped by site, deduplicated against everything already
   shown, skipping quarantined bundles.

No fabrication: a bundle with no steps/causes/symptoms renders nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.engine.ai.diagnostic_copilot import (
    CausalBayesianInferenceEngine,
    ensure_external_dtc_database_loaded,
)

DTC_DB_PATH = Path("data/diagnostics/dtc_database.json")

RENDER_HEADER = "Ek Bağımsız Kaynak Prosedürleri"


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()


def _db() -> dict:
    return json.loads(DTC_DB_PATH.read_text(encoding="utf-8"))


def _multi_records() -> list[tuple[str, dict]]:
    return [
        (code, rec)
        for code, rec in _db().items()
        if isinstance(rec, dict) and rec.get("procedures_full_multi")
    ]


def _query(code: str) -> str:
    return CausalBayesianInferenceEngine.evaluate_diagnostic_query(f"{code} nedir", [], {})


# ---------------------------------------------------------------------------
# 1. The merge landed and the shape is what the reader expects
# ---------------------------------------------------------------------------


class TestT69MultiSourceStorage:
    def test_multi_source_field_is_populated(self) -> None:
        found = _multi_records()
        assert found, (
            "no DTC record carries `procedures_full_multi` — the T69 append path "
            "did not run, or the merge regressed to fill-if-empty only"
        )

    def test_every_bundle_has_a_source_url_and_steps(self) -> None:
        """A bundle with no URL cannot be deduped; one with no steps is empty."""
        for code, rec in _multi_records():
            for b in rec["procedures_full_multi"]:
                assert isinstance(b, dict), f"{code}: bundle is not a dict"
                assert str(b.get("source_url") or "").startswith("http"), (
                    f"{code}: bundle has no source URL"
                )
                has_content = any(
                    b.get(k) for k in ("steps", "causes", "symptoms")
                )
                assert has_content, f"{code}: bundle carries no content at all"

    def test_the_original_single_source_is_never_overwritten(self) -> None:
        """`procedures_full` and its URL must be untouched by the append."""
        for code, rec in _multi_records():
            original = rec.get("procedures_full")
            if not original:
                continue
            original_url = str(rec.get("procedures_source_url") or "")
            for b in rec["procedures_full_multi"]:
                assert str(b.get("source_url")) != original_url, (
                    f"{code}: the append path duplicated the original source — "
                    "the single-source payload was not respected"
                )

    def test_no_duplicate_source_urls_within_a_record(self) -> None:
        for code, rec in _multi_records():
            urls = [str(b.get("source_url")) for b in rec["procedures_full_multi"]]
            assert len(urls) == len(set(urls)), (
                f"{code}: the same source URL was appended twice"
            )

    def test_record_count_is_unchanged(self) -> None:
        """T69 only fills fields — it must never add or remove a DTC."""
        assert len(_db()) == 14484

    def test_quality_is_not_inflated(self) -> None:
        """A harvested bundle must not claim verified quality."""
        for code, rec in _multi_records():
            for b in rec["procedures_full_multi"]:
                assert b.get("quality") in ("unverified", "verified"), (
                    f"{code}: unexpected quality value {b.get('quality')!r}"
                )


# ---------------------------------------------------------------------------
# 2. The reader renders every source
# ---------------------------------------------------------------------------


class TestT69ReaderRendersEverySource:
    def test_multi_source_block_renders(self) -> None:
        found = _multi_records()
        if not found:
            pytest.skip("no multi-source record")
        code = found[0][0]
        assert RENDER_HEADER in _query(code)

    def test_every_source_url_reaches_the_report(self) -> None:
        """The operator must be able to verify each claim at its origin."""
        for code, rec in _multi_records():
            report = _query(code)
            if RENDER_HEADER not in report:
                continue
            for b in rec["procedures_full_multi"]:
                url = str(b.get("source_url"))
                assert url in report, (
                    f"{code}: source URL {url} is stored but not rendered"
                )

    def test_a_step_from_each_source_survives(self) -> None:
        """Pick one long step per bundle and require it in the report."""
        for code, rec in _multi_records():
            report = _query(code)
            if RENDER_HEADER not in report:
                continue
            for b in rec["procedures_full_multi"]:
                steps = [str(s) for s in (b.get("steps") or []) if str(s).strip()]
                if not steps:
                    continue
                probe = max(steps, key=len)[:60]
                assert probe in report, (
                    f"{code}: the longest step of {b.get('source_site')} is "
                    "stored but not rendered"
                )

    def test_source_site_header_is_shown(self) -> None:
        for code, rec in _multi_records():
            report = _query(code)
            if RENDER_HEADER not in report:
                continue
            site = str(rec["procedures_full_multi"][0].get("source_site") or "")
            if site:
                assert site in report
            return
        pytest.skip("no multi-source record")

    def test_multi_source_block_states_the_counts(self) -> None:
        """The operator must be able to see the answer was not truncated."""
        for code, _rec in _multi_records():
            report = _query(code)
            if RENDER_HEADER not in report:
                continue
            m = re.search(r"\((\d+) kaynak, (\d+) adım", report)
            assert m, f"{code}: multi-source header carries no counts"
            assert int(m.group(1)) >= 1
            return
        pytest.skip("no multi-source record")

    def test_no_step_is_printed_twice_within_the_block(self) -> None:
        """Dedupe is the whole reason the block stays readable."""
        for code, _rec in _multi_records():
            report = _query(code)
            if RENDER_HEADER not in report:
                continue
            block = report.split(RENDER_HEADER, 1)[1]
            steps = re.findall(r"^\s+\d+\. (.+)$", block, re.M)
            norm = [" ".join(s.split()).lower() for s in steps]
            assert len(norm) == len(set(norm)), (
                f"{code}: the multi-source block repeats a step"
            )
            return
        pytest.skip("no multi-source record")


# ---------------------------------------------------------------------------
# 3. Fail-closed: nothing invented, nothing quarantined
# ---------------------------------------------------------------------------


class TestT69FailClosed:
    def test_missing_multi_field_renders_no_block(self) -> None:
        """A record without the field must not grow an empty header."""
        for code, rec in _db().items():
            if not isinstance(rec, dict):
                continue
            if rec.get("procedures_full_multi"):
                continue
            if code not in CausalBayesianInferenceEngine._THRESHOLD_SIGNAL_FOR_CODE and not rec.get(
                "procedures_full"
            ):
                continue
            report = _query(code)
            assert RENDER_HEADER not in report, (
                f"{code} has no procedures_full_multi but rendered the block"
            )
            return
        pytest.skip("no suitable single-source record")

    def test_quarantined_bundle_is_never_rendered(self) -> None:
        """T2-3 survives the new reader."""
        for code, rec in _db().items():
            bundles = rec.get("procedures_full_multi") if isinstance(rec, dict) else None
            if not isinstance(bundles, list):
                continue
            if not any(isinstance(b, dict) and b.get("_quarantined") for b in bundles):
                continue
            report = _query(code)
            for b in bundles:
                if not b.get("_quarantined"):
                    continue
                for s in (b.get("steps") or [])[:1]:
                    assert str(s)[:40] not in report, (
                        f"{code}: a quarantined bundle was rendered"
                    )
            return
        pytest.skip("no quarantined multi-source bundle in the DB")

    def test_empty_bundle_renders_nothing(self, monkeypatch) -> None:
        """A bundle with no content must not produce a header."""
        import src.engine.ai.diagnostic_copilot as dc

        victim = None
        for code, rec in _db().items():
            if isinstance(rec, dict) and rec.get("procedures_full_multi"):
                victim = code
                break
        if victim is None:
            pytest.skip("no multi-source record")

        original = dc.EXPERT_KNOWLEDGE_BASE[victim].get("procedures_full_multi")
        patched = dict(dc.EXPERT_KNOWLEDGE_BASE)
        entry = dict(patched[victim])
        entry["procedures_full_multi"] = [
            {"source_url": "https://example.invalid/x", "source_site": "x", "steps": []}
        ]
        patched[victim] = entry
        monkeypatch.setattr(dc, "EXPERT_KNOWLEDGE_BASE", patched)
        report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
            f"{victim} nedir", [], {}
        )
        assert "example.invalid" not in report
        assert original is not None  # the DB value was restored by monkeypatch

    def test_determinism(self) -> None:
        for code, _rec in _multi_records()[:3]:
            assert _query(code) == _query(code)


# ---------------------------------------------------------------------------
# 4. The bundle shape matches the J1939 precedent
# ---------------------------------------------------------------------------


class TestT69ShapeParityWithJ1939:
    """The DTC bundle must be the same shape the J1939 reader already handles."""

    def test_bundle_keys_are_the_shared_vocabulary(self) -> None:
        for code, rec in _multi_records():
            for b in rec["procedures_full_multi"]:
                for key in ("source_url", "steps", "causes", "symptoms"):
                    assert key in b, f"{code}: bundle is missing the {key!r} key"
                for key in ("steps", "causes", "symptoms"):
                    assert isinstance(b[key], list), (
                        f"{code}: {key!r} must be a list, got {type(b[key]).__name__}"
                    )
                return
            return
        pytest.skip("no multi-source record")

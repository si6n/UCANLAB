# -*- coding: utf-8 -*-
"""T69-G regression lock — a richer re-harvest of the SAME URL must win.

THE DEFECT (measured, not hypothetical)
---------------------------------------
`scripts/t66_merge.py` deduped multi-source bundles by `source_url` with a
single rule: *URL already present -> skip*. That rule is correct for preventing
duplicates, but it silently keeps the POORER copy when the same URL is
re-harvested later and comes back richer.

Found while auditing the T69-F merge for content mismatches:

    honda/2200-2300/how-to-test-code-p0117
        DB bundle (earlier T69 round) : 40 steps
        T69-F re-harvest of SAME URL  : 75 steps
        -> 35 steps were dropped with no trace

The merge reported `no_fillable` for that record, so nothing looked wrong. The
loss was invisible because "already present" and "already complete" were
treated as the same condition.

THE FIX
-------
The rule is now richness-aware:
  * URL absent            -> append (unchanged)
  * URL present, richer   -> REPLACE that URL's own bundle, report `upgraded`
  * URL present, <= equal -> refuse, count `poorer_reharvest`

So a re-run can never shrink the DB (idempotence) but a genuine improvement is
never discarded.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MERGE_PATH = Path("scripts/t66_merge.py")
DTC_DB_PATH = Path("data/diagnostics/dtc_database.json")


def _load_merge_module():
    spec = importlib.util.spec_from_file_location("t66_merge_t69g", MERGE_PATH)
    assert spec and spec.loader, "merge module must be loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def merge_mod():
    return _load_merge_module()


def _rec(url: str, steps: list[str]) -> dict:
    return {
        "code": "P9999",
        "url": url,
        "source": "test-source",
        "procedures_full_append": {
            "source_url": url,
            "source_site": "test-source",
            "steps": steps,
            "causes": [],
            "symptoms": [],
            "quality": "unverified",
            "method": "free",
        },
        "evidence": "x" * 60,
    }


def _entry(url: str, steps: list[str]) -> dict:
    return {
        "procedures_full_multi": [
            {
                "source_url": url,
                "source_site": "test-source",
                "steps": steps,
                "causes": [],
                "symptoms": [],
                "quality": "unverified",
                "method": "free",
            }
        ]
    }


class TestRichnessRule:
    """The three-way rule: append / upgrade / refuse."""

    def test_richer_reharvest_replaces_its_own_bundle(self, merge_mod) -> None:
        url = "https://example.test/p9999"
        dtc = {"P9999": _entry(url, ["a", "b", "c"])}
        report = {"applied": [], "skipped": {"no_fillable": 0, "already_full": 0,
                                             "placeholder": 0, "no_evidence": 0,
                                             "unknown_key": 0, "no_source_url": 0}}
        rec = _rec(url, ["a", "b", "c", "d", "e"])
        assert merge_mod.merge_dtc(rec, dtc, report, apply=True) is True
        steps = dtc["P9999"]["procedures_full_multi"][0]["steps"]
        assert len(steps) == 5, "the richer bundle must replace the older one"
        assert report.get("upgraded"), "the upgrade must be reported"

    def test_poorer_reharvest_is_refused(self, merge_mod) -> None:
        url = "https://example.test/p9999"
        dtc = {"P9999": _entry(url, ["a", "b", "c", "d", "e"])}
        report = {"applied": [], "skipped": {"no_fillable": 0, "already_full": 0,
                                             "placeholder": 0, "no_evidence": 0,
                                             "unknown_key": 0, "no_source_url": 0}}
        rec = _rec(url, ["a", "b"])
        merge_mod.merge_dtc(rec, dtc, report, apply=True)
        steps = dtc["P9999"]["procedures_full_multi"][0]["steps"]
        assert len(steps) == 5, "a poorer re-harvest must never shrink the DB"
        assert report["skipped"].get("poorer_reharvest") == 1

    def test_equal_reharvest_is_refused(self, merge_mod) -> None:
        url = "https://example.test/p9999"
        dtc = {"P9999": _entry(url, ["a", "b", "c"])}
        report = {"applied": [], "skipped": {"no_fillable": 0, "already_full": 0,
                                             "placeholder": 0, "no_evidence": 0,
                                             "unknown_key": 0, "no_source_url": 0}}
        merge_mod.merge_dtc(_rec(url, ["a", "b", "c"]), dtc, report, apply=True)
        assert len(dtc["P9999"]["procedures_full_multi"]) == 1
        assert report["skipped"].get("poorer_reharvest") == 1

    def test_new_url_still_appends(self, merge_mod) -> None:
        url_a = "https://example.test/p9999/a"
        url_b = "https://example.test/p9999/b"
        dtc = {"P9999": _entry(url_a, ["a"])}
        report = {"applied": [], "skipped": {"no_fillable": 0, "already_full": 0,
                                             "placeholder": 0, "no_evidence": 0,
                                             "unknown_key": 0, "no_source_url": 0}}
        merge_mod.merge_dtc(_rec(url_b, ["b"]), dtc, report, apply=True)
        assert len(dtc["P9999"]["procedures_full_multi"]) == 2

    def test_upgrade_does_not_touch_other_sources(self, merge_mod) -> None:
        """Upgrading one URL must leave every other bundle byte-identical."""
        url_a = "https://example.test/p9999/a"
        url_b = "https://example.test/p9999/b"
        dtc = {"P9999": {"procedures_full_multi": [
            {"source_url": url_a, "steps": ["a1"], "causes": [], "symptoms": []},
            {"source_url": url_b, "steps": ["b1", "b2", "b3"], "causes": [], "symptoms": []},
        ]}}
        report = {"applied": [], "skipped": {"no_fillable": 0, "already_full": 0,
                                             "placeholder": 0, "no_evidence": 0,
                                             "unknown_key": 0, "no_source_url": 0}}
        merge_mod.merge_dtc(_rec(url_a, ["a1", "a2", "a3", "a4"]), dtc, report, apply=True)
        bundles = {b["source_url"]: b for b in dtc["P9999"]["procedures_full_multi"]}
        assert bundles[url_b]["steps"] == ["b1", "b2", "b3"], "unrelated bundle changed"
        assert len(bundles[url_a]["steps"]) == 4

    def test_dry_run_does_not_mutate(self, merge_mod) -> None:
        url = "https://example.test/p9999"
        dtc = {"P9999": _entry(url, ["a"])}
        report = {"applied": [], "skipped": {"no_fillable": 0, "already_full": 0,
                                             "placeholder": 0, "no_evidence": 0,
                                             "unknown_key": 0, "no_source_url": 0}}
        merge_mod.merge_dtc(_rec(url, ["a", "b"]), dtc, report, apply=False)
        assert dtc["P9999"]["procedures_full_multi"][0]["steps"] == ["a"]


class TestLiveDatabase:
    """The upgrade that motivated the fix must be present in the live DB."""

    def test_p0117_holds_the_richer_bundle(self) -> None:
        db = json.loads(DTC_DB_PATH.read_text(encoding="utf-8"))
        url = (
            "https://troubleshootmyvehicle.com/honda/2200-2300/"
            "how-to-test-code-p0117"
        )
        bundles = [
            b for b in (db["P0117"].get("procedures_full_multi") or [])
            if isinstance(b, dict) and b.get("source_url") == url
        ]
        assert bundles, "fixture assumption: the P0117 bundle exists"
        assert len(bundles[0]["steps"]) == 75, (
            "the 75-step re-harvest must have replaced the 40-step bundle"
        )

    def test_record_count_is_unchanged_by_an_upgrade(self) -> None:
        """An upgrade replaces a bundle; it never adds a record."""
        db = json.loads(DTC_DB_PATH.read_text(encoding="utf-8"))
        assert len(db) == 14484

    def test_no_duplicate_source_urls_within_a_record(self) -> None:
        """The dedup invariant must survive the upgrade path."""
        db = json.loads(DTC_DB_PATH.read_text(encoding="utf-8"))
        offenders: list[tuple[str, str]] = []
        for code, entry in db.items():
            if not isinstance(entry, dict):
                continue
            urls = [
                str(b.get("source_url"))
                for b in (entry.get("procedures_full_multi") or [])
                if isinstance(b, dict) and b.get("source_url")
            ]
            if len(urls) != len(set(urls)):
                offenders.append((code, str(urls)))
        assert not offenders, f"duplicate source_url within a record: {offenders[:5]}"

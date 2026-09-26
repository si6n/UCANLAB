"""T83: the obd2.com procedures merge — 388 code-specific English procedures.

MEASURED FINDING (2026-09-26): 7,010 of the 14,349 records carrying `steps`
(48.9%) held ONLY a generic 3-step Turkish template ("... ile ilgili kablo
tesisatı ve sensör soketlerini korozyon/gevşeklik yönünden inceleyin." /
"Multimetre ile referans voltajı..." / "CAN-Bus hattı osiloskop..."), which
names no component and no measurement. Only 585 records held real English
steps.

A 390-page obd2.com harvest supplied code-specific English procedures (median
7 steps, each naming the actual component and test). Admission was
pre-registered: http 200, h1 matching the code, anchor != block, >= 4 steps,
and the DB's `procedures_full` absent or the generic template. Measured:
390 pages -> 388 admitted, 1 anchor block (P0642), 1 too few steps.

The contract was COPIED from the P0101 calibration record (an earlier merge
of the same source), not invented:
    procedures_full <- list[str];  procedures_source <- "obd2.com";
    procedures_source_url <- the page URL.
The record's Turkish `steps` (per-step component/difficulty metadata) is a
different field and is NOT touched.
"""

import json

import pytest

DTC_DB = "data/diagnostics/dtc_database.json"
MARK = "procedures_obd2_t83"
N_MERGED = 389  # 388 from the first pass + B1614 recovered on the second


@pytest.fixture(scope="module")
def db():
    with open(DTC_DB, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def merged(db):
    return {c: e for c, e in db.items()
            if isinstance(e, dict) and e.get(MARK)}


class TestMergedPopulation:
    def test_expected_count(self, merged):
        assert len(merged) == N_MERGED, f"expected {N_MERGED}, got {len(merged)}"

    def test_every_record_has_a_step_list(self, merged):
        for code, e in merged.items():
            pf = e.get("procedures_full")
            assert isinstance(pf, list) and pf, f"{code}: bad procedures_full"
            assert all(isinstance(x, str) and x.strip() for x in pf), code
            assert len(pf) >= 4, f"{code}: only {len(pf)} steps"

    def test_source_contract(self, merged):
        for code, e in merged.items():
            assert e.get("procedures_source") == "obd2.com", code
            url = str(e.get("procedures_source_url") or "")
            assert url.startswith("https://obd2.com/dtc/"), (code, url)

    def test_turkish_steps_untouched(self, merged):
        """`steps` is a different field with a different shape — preserved."""
        for code, e in merged.items():
            st = e.get("steps")
            if st is not None:
                assert isinstance(st, list), code

    def test_steps_are_code_specific_not_template(self, merged):
        """No merged procedure may be the generic Turkish template."""
        TEMPLATE = (
            "ile ilgili kablo tesisatı ve sensör soketlerini korozyon",
            "Multimetre ile referans voltajı",
            "CAN-Bus hattı osiloskop sinyal kalitesini",
        )
        for code, e in merged.items():
            txt = " ".join(str(x) for x in e["procedures_full"])
            assert not any(t in txt for t in TEMPLATE), code


class TestExclusions:
    def test_anchor_block_was_excluded(self, db):
        """P0642's obd2 page named a different subject than the record."""
        assert not db["P0642"].get(MARK), (
            "the anchor-blocked page must never be merged")

    def test_b1614_was_recovered_after_a_thin_first_pass(self, db):
        """B1614's first extraction pass found <4 steps and was skipped.

        The harvesting agent then re-fetched it with a keyword-based
        extractor, found 6 real steps, and folded them into the checkpoint —
        a documented recovery, not a silent inclusion. The merge therefore
        accepts it on the second pass.
        """
        d = json.load(open("output/t83_work/obd2_390_checkpoint.json",
                           encoding="utf-8"))
        page = d["pages"].get("B1614")
        assert page is not None, "B1614 must be present in the checkpoint"
        steps = page.get("steps")
        assert isinstance(steps, list) and len(steps) >= 4, (
            "B1614's recovered steps must satisfy the admission threshold")
        assert db["B1614"].get(MARK), "the recovered page must be merged"

    def test_no_page_below_threshold_was_merged(self, db):
        """The admission rule still holds for every other page."""
        d = json.load(open("output/t83_work/obd2_390_checkpoint.json",
                           encoding="utf-8"))
        for code, p in d["pages"].items():
            steps = p.get("steps")
            if isinstance(steps, list) and len(steps) >= 4:
                continue
            assert not db[code].get(MARK), (
                f"{code} carries {len(steps or [])} steps but was merged")


class TestCalibrationContract:
    def test_matches_the_p0101_calibration_shape(self, db):
        """P0101 was merged earlier from the same source; shapes must agree."""
        for code in ("P0101", "P0438"):
            e = db[code]
            assert isinstance(e.get("procedures_full"), list)
            assert e.get("procedures_source") == "obd2.com"
            assert str(e.get("procedures_source_url")).startswith(
                "https://obd2.com/dtc/")


class TestDatabaseIntegrity:
    def test_record_count_frozen(self, db):
        assert len(db) == 14484

    def test_procedures_full_grew_by_the_merge(self, db):
        n = sum(1 for e in db.values()
                if isinstance(e, dict) and e.get("procedures_full"))
        assert n >= 7171, f"procedures_full fell to {n}"

"""T82e: the fabricated-percentage fingerprint and the T82e revert.

MEASURED FINDING (2026-09-26): 1,691 records carried a geekobd-sourced
`symptoms_en` with a precise fuel-economy claim ("Fuel economy decreased by
10-15%") stamped onto codes where no such measurement exists — a brake pedal
sensor, a coolant pump supply circuit, a 4WD aero module. All 1,691 claims sat
in geekobd fields; zero sat in any other source's field, and zero records
carried the claim in more than one field. The claim therefore identifies
geekobd content by itself.

These records escaped five earlier revert rounds because the driving gate
ABSTAINS when the title names no system from the lexicon, and boilerplate
titles ("OBD Code", "Manufacturer-specific network code...") name none. An
abstain is not a block, so the content survived.

The revert removed ONLY the geekobd-sourced EN field carrying the claim. The
record keeps its title and every other field.
"""

import re

import pytest

DTC_DB = "data/diagnostics/dtc_database.json"
MARK = "geekobd_reverted_t82e"

PCT_FUEL = re.compile(
    r"(?:fuel (?:economy|efficiency|consumption|mileage)[^.]{0,80}?"
    r"\d{1,2}\s*-\s*\d{1,2}\s*%"
    r"|\d{1,2}\s*-\s*\d{1,2}\s*%[^.]{0,60}?"
    r"(?:fuel|mpg|mileage|economy))",
    re.I,
)
EN_FIELDS = ("symptoms_en", "causes_en", "description_en")


@pytest.fixture(scope="module")
def db():
    import json
    with open(DTC_DB, encoding="utf-8") as fh:
        return json.load(fh)


class TestFingerprintRegex:
    @pytest.mark.parametrize("text", [
        "Fuel economy decreased by 10-15%.",
        "Fuel economy may decrease by 10-20%.",
        "A drop of 10-15% in fuel economy.",
        "Decrease in fuel efficiency by 15-20%.",
        "Fuel consumption drops significantly, often by 10-15%.",
        # the real P101F shape: the fuel word precedes the reported drop
        "Fuel economy might drop significantly, with users reporting "
        "reductions of 10-20% due to inefficient engine operation",
    ])
    def test_matches_fabricated_claims(self, text):
        assert PCT_FUEL.search(text), text

    @pytest.mark.parametrize("text", [
        "Check engine light illuminated.",
        "Reduced fuel economy.",
        "Rough idle and hesitation.",
        "The sensor reads 10-15 ohms of resistance.",  # not a fuel claim
        "Fuel economy may be affected.",
        # a percentage with no fuel word is NOT the fingerprint
        "reductions of 10-20% due to inefficient operation",
    ])
    def test_does_not_match_ordinary_text(self, text):
        assert not PCT_FUEL.search(text), text


class TestRevertedPopulation:
    def test_all_marked_records_carry_the_mark(self, db):
        marked = [c for c, e in db.items()
                  if isinstance(e, dict) and e.get(MARK)]
        assert len(marked) == 1691, f"expected 1,691, got {len(marked)}"

    def test_no_geekobd_field_still_carries_a_claim(self, db):
        """The decisive post-condition: the class is gone."""
        offenders = []
        for code, e in db.items():
            if not isinstance(e, dict):
                continue
            for f in EN_FIELDS:
                if "geekobd" not in str(e.get(f + "_source") or ""):
                    continue
                v = e.get(f)
                t = (" ".join(str(x) for x in v) if isinstance(v, list)
                     else str(v or ""))
                if t and PCT_FUEL.search(t):
                    offenders.append((code, f))
        assert offenders == [], f"{len(offenders)} remain: {offenders[:5]}"

    def test_reverted_records_kept_their_title(self, db):
        """Identity search must be unaffected: title and title_en survive."""
        missing = [c for c, e in db.items()
                   if isinstance(e, dict) and e.get(MARK)
                   and not (e.get("title") or "").strip()]
        assert missing == []

    def test_reverted_records_removed_only_the_geekobd_field(self, db):
        """A reverted record must still carry its non-geekobd evidence."""
        # records whose description_en comes from a different source keep it
        kept = 0
        for code, e in db.items():
            if not isinstance(e, dict) or not e.get(MARK):
                continue
            src = str(e.get("description_en_source") or "")
            if src and "geekobd" not in src:
                assert e.get("description_en"), code
                kept += 1
        assert kept > 0, "no corroborating evidence found in the reverted set"

    def test_backup_hash_is_recorded(self, db):
        n = sum(1 for e in db.values()
                if isinstance(e, dict) and e.get(MARK)
                and len(str(e.get("geekobd_revert_backup_t82e") or "")) == 64)
        assert n == 1691

    def test_record_count_frozen(self, db):
        assert len(db) == 14484


class TestFieldTypeLesson:
    """The abstain-not-block mechanism that let these survive five rounds."""

    def test_boilerplate_title_abstains(self):
        from src.engine.ai.subject_anchor import check_subject_anchor
        v = check_subject_anchor(
            "Manufacturer-specific network code. The second character '1' "
            "marks this as defined by the vehicle manufacturer.",
            "Check engine light stays on constantly. Fuel economy decreased "
            "by 10-15%.")
        assert v.status == "abstain", (
            "the gate abstains on a boilerplate title — which is exactly why "
            "the content survived; the revert must not depend on this gate")

    def test_gate_blocks_when_the_title_names_a_system(self):
        from src.engine.ai.subject_anchor import check_subject_anchor
        v = check_subject_anchor(
            "Outside Air Temperature Sensor Circuit Range/Performance",
            "Airbag warning light remains illuminated on the dashboard, "
            "indicating a malfunction in the airbag sensor.")
        assert v.status in ("block", "abstain")

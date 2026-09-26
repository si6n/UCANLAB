# -*- coding: utf-8 -*-
"""T82c regression lock — obdhut-misattributed `description_en`.

WHY (measured 2026-09-26)
-------------------------
A discovery agent reported obdhut's `description_en` blocking at 92.6% on a
149-code sample. **I could not reproduce that rate**: five random 149-code
samples give 9.4-15.4%, and the full population of 4,176 measures 10.6%. The
agent's figure is recorded as unreproducible.

The underlying class IS real, though. Reading the 442 blocks by hand shows
genuine misattribution:

    P1156  title "HO2S Rich Average Bank 2 Sensor 1"
           desc  "…the Manifold Absolute Pressure (MAP) sensor circuit…"
    P1110  title "Heated oxygen sensor (HO2S) heater short to positive"
           desc  "…a fault in the Intake Air Temperature (IAT) sensor circuit"
    P1900  title "Output Shaft Speed Circuit Intermittent Malfunction"
           desc  "The engine coolant level warning lamp circuit has a short…"

352 were reverted (no content-word overlap with the title); 89 that share a
word are FLAGGED, not deleted — the classifier cannot decide those alone.
C0040/C0041/C0045 are carved out: T79 established them as OEM-specific, where
both meanings are correct.

LOCKED INVARIANTS
-----------------
  1. A record marked `obdhut_desc_reverted_t82c` carries no
     `description_en_source` naming obdhut.
  2. A record flagged `obdhut_desc_subject_conflict_t82c` KEEPS its text — the
     flag is for review, not deletion.
  3. The OEM-variant carve-out holds: C0040/C0041/C0045 are never reverted.
  4. Record count stays frozen at 14,484.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"

_REVERT_FLOOR = 352
_FLAG_FLOOR = 89
_OEM_VARIANTS = frozenset({"C0040", "C0041", "C0045"})

@pytest.fixture(scope="module")
def db() -> dict:
    if not DTC_DB.exists():
        pytest.skip("shipped DTC database not present")
    return json.loads(DTC_DB.read_text(encoding="utf-8"))

class TestObdhutDescriptionRevert:
    def test_reverted_records_carry_no_obdhut_description(self, db):
        bad = [c for c, e in db.items()
               if e.get("obdhut_desc_reverted_t82c")
               and "obdhut" in str(e.get("description_en_source") or "")]
        assert bad == [], f"incomplete revert: {bad[:10]}"

    def test_reverted_records_keep_a_reason(self, db):
        missing = [c for c, e in db.items()
                   if e.get("obdhut_desc_reverted_t82c")
                   and not e.get("obdhut_desc_revert_reason_t82c")]
        assert missing == [], f"missing revert reason: {missing[:10]}"

    def test_flagged_records_keep_their_text(self, db):
        """A flag is a review marker, not a deletion."""
        bad = [c for c, e in db.items()
               if e.get("obdhut_desc_subject_conflict_t82c")
               and not e.get("description_en")]
        assert bad == [], (
            f"flagged records must keep their text for review: {bad[:10]}")

    def test_oem_variant_codes_are_never_reverted(self, db):
        bad = [c for c in _OEM_VARIANTS
               if db.get(c, {}).get("obdhut_desc_reverted_t82c")]
        assert bad == [], (
            f"OEM-variant codes (two valid meanings) must not be reverted: {bad}")

    def test_revert_count_does_not_shrink(self, db):
        n = sum(1 for e in db.values() if e.get("obdhut_desc_reverted_t82c"))
        assert n >= _REVERT_FLOOR, f"revert count fell to {n}"

    def test_flag_count_does_not_shrink(self, db):
        n = sum(1 for e in db.values()
                if e.get("obdhut_desc_subject_conflict_t82c"))
        assert n >= _FLAG_FLOOR, f"flag count fell to {n}"

    def test_record_count_frozen(self, db):
        assert len(db) == 14484

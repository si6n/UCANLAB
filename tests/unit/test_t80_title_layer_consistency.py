# -*- coding: utf-8 -*-
"""T80 data-quality lock — the `title_en` layer must not contradict `title`.

WHY (measured 2026-09-26 on the shipped DB)
-------------------------------------------
The DTC database carries two meaning layers: a curated `title` (Wal33D
database + TR translation) and a scraped `title_en` (OBDex /
openlaborproject / obdhut / theerrorcodes). A source-side off-by-N shift put
**another code's meaning** into 217 records' `title_en`:

    C0091  title    : 4WD/AWD Power Transfer Unit Position Sensor A
           title_en : Brake Booster Performance          (== C0021's title)

The T79p OEM audit verified 71 of them against independent sources; 56 had
>= 2 independent votes and were repaired by `scripts/t80_repair_title_en.py`
(the record's own `title` was copied down — no fabrication). The remaining
codes are flagged, not repaired.

LOCKED INVARIANTS
-----------------
  1. NO record's `title_en` may equal a DIFFERENT record's `title` unless it
     also equals its own `title`. This is the shift signature; the repair
     cleared the verified subset and the merge-side guard
     (`scripts/t66_merge.py`, T80 block) now blocks re-entry — so the count
     can only go DOWN, never up.
  2. Polarity is significant: "(+) Open" and "(-) Open" are different codes
     and must not be folded into one comparison key.
  3. The repaired records carry their audit trail: `title_en_prev`,
     `title_en_prev_source`, `title_en_repaired_t80`.
  4. `title_en` must not carry Turkish text (the language contract: an EN
     field receives English only).
  5. Record count stays frozen at 14,484.

The shipped-DB assertions are guarded by an existence check so the file can
be copied into a checkout without the DB present.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"
MERGE_PATH = REPO_ROOT / "scripts" / "t66_merge.py"

TR_CHARS = set("ığşöüçİĞŞÖÜÇ")

def _norm(s) -> str:
    s = str(s or "").lower()
    s = s.replace("(+)", " plus ").replace("(-)", " minus ")
    s = s.replace("+", " plus ").replace("-", " minus ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

@pytest.fixture(scope="module")
def db() -> dict:
    if not DTC_DB.exists():
        pytest.skip("shipped DTC database not present")
    return json.loads(DTC_DB.read_text(encoding="utf-8"))

@pytest.fixture(scope="module")
def merge_mod():
    spec = importlib.util.spec_from_file_location("t66_merge_t80_db", MERGE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# ---------------------------------------------------------------------------
# 1. The shift signature cannot grow
# ---------------------------------------------------------------------------

# Measured after the T80 repair (2026-09-26): 211 records still carry the
# signature — the P-family subset whose cited source was not reachable for
# live verification. They are FLAGGED (see test_flagged_are_marked), not
# repaired. This ceiling is a ratchet: it may be lowered, never raised.
_SHIFT_CEILING = 211

class TestShiftSignature:
    def test_shift_count_does_not_exceed_the_measured_ceiling(self, db):
        title_owners: dict[str, set[str]] = {}
        for code, entry in db.items():
            k = _norm(entry.get("title"))
            if k:
                title_owners.setdefault(k, set()).add(code)

        shifted = []
        for code, entry in db.items():
            te = entry.get("title_en")
            if not te:
                continue
            kte = _norm(te)
            if kte == _norm(entry.get("title")):
                continue
            if title_owners.get(kte, set()) - {code}:
                shifted.append(code)
        assert len(shifted) <= _SHIFT_CEILING, (
            f"{len(shifted)} records carry another code's meaning in title_en "
            f"(ceiling {_SHIFT_CEILING}) — the merge-side T80 guard should have "
            f"blocked this; new shifts: {sorted(set(shifted))[:10]}"
        )

    def test_no_record_has_turkish_text_in_title_en(self, db):
        bad = [c for c, r in db.items()
               if r.get("title_en") and any(ch in TR_CHARS for ch in str(r["title_en"]))]
        assert bad == [], f"EN field carries Turkish characters: {bad[:10]}"

    def test_polarity_pairs_are_not_folded(self, merge_mod):
        """(+) and (-) bus halves must normalize to different keys."""
        k = merge_mod._title_norm_key
        assert k("Bus (+) Open") != k("Bus (-) Open")

# ---------------------------------------------------------------------------
# 2. The repair is traceable and idempotent-shaped
# ---------------------------------------------------------------------------

class TestRepairTrail:
    def test_repaired_records_align_with_their_own_title(self, db):
        repaired = [c for c, r in db.items() if r.get("title_en_repaired_t80")]
        assert repaired, "the T80 repair must have left a trace"
        for code in repaired:
            entry = db[code]
            assert entry["title_en"] == entry["title"], (
                f"{code}: repaired title_en must equal the record's own title")
            assert entry.get("title_en_prev"), f"{code}: missing title_en_prev"
            assert entry.get("title_en_prev_source"), (
                f"{code}: missing title_en_prev_source")
            # T46 contract: *_en_source is a URL. The repair keeps that shape
            # and records the confirming votes in its own field.
            assert str(entry.get("title_en_source")).startswith("https://"), (
                f"{code}: title_en_source must stay a URL (T46 contract)")
            assert entry.get("title_en_repair_votes"), (
                f"{code}: the repair must name its confirming source(s)")

    def test_flag_only_records_are_marked_not_silently_changed(self, db):
        flagged = [c for c, r in db.items() if r.get("title_en_unverified")]
        assert flagged, "single-vote records must be flagged for review"
        for code in flagged:
            assert not db[code].get("title_en_repaired_t80"), (
                f"{code}: a flagged record must NOT be repaired in the same pass")

    def test_placeholder_titles_are_never_repaired(self, db):
        """T80g: a placeholder title is not an authoritative meaning.

        Aligning `title_en` to it would replace real source content with
        boilerplate. The repair script must skip these (fail-closed).
        """
        import re
        placeholder = re.compile(
            r"generic \(sae|reserved|manufacturer-specific|body dtc code", re.I)
        bad = [c for c, r in db.items()
               if r.get("title_en_repaired_t80")
               and placeholder.search(str(r.get("title") or ""))]
        assert bad == [], f"placeholder-title records were repaired: {bad[:10]}"
        # and the skip must be recorded, not silent
        skipped = [c for c, r in db.items()
                   if r.get("title_en_repair_skipped_t80g")]
        assert skipped, "the placeholder skip must leave a trace"

    def test_record_count_frozen(self, db):
        assert len(db) == 14484

# ---------------------------------------------------------------------------
# 3. The merge-side guard is present and active
# ---------------------------------------------------------------------------

class TestMergeGuard:
    def test_guard_helpers_exist(self, merge_mod):
        for name in ("_title_norm_key", "_title_index",
                     "_title_en_is_foreign_shift", "_cached_title_index"):
            assert hasattr(merge_mod, name), f"missing T80 helper {name}"

    def test_guard_flags_a_known_shift(self, merge_mod):
        idx = merge_mod._title_index({
            "C0091": {"title": "4WD/AWD Power Transfer Unit Position Sensor A"},
            "C0021": {"title": "Brake Booster Performance"},
        })
        assert merge_mod._title_en_is_foreign_shift(
            "Brake Booster Performance",
            "4WD/AWD Power Transfer Unit Position Sensor A", idx, "C0091")

    def test_guard_accepts_own_title_reword(self, merge_mod):
        idx = merge_mod._title_index({
            "C0091": {"title": "4WD/AWD Power Transfer Unit Position Sensor A"},
            "C0021": {"title": "Brake Booster Performance"},
        })
        assert not merge_mod._title_en_is_foreign_shift(
            "4wd awd power transfer unit position sensor a",
            "4WD/AWD Power Transfer Unit Position Sensor A", idx, "C0091")

# ---------------------------------------------------------------------------
# 4. The self-audit: a repair that meets the bar may still carry ambiguity
# ---------------------------------------------------------------------------

class TestVariantAmbiguity:
    """T80L: where a THIRD source names yet another meaning, the record must
    say so rather than presenting the repaired value as the only truth."""

    def test_variant_flagged_records_carry_their_competing_meaning(self, db):
        flagged = [c for c, r in db.items() if r.get("title_en_variant_meaning")]
        assert flagged, "the variant audit must have left a trace"
        for code in flagged:
            entry = db[code]
            assert entry.get("title_en_variant_source"), (
                f"{code}: a competing meaning must name its source")
            assert entry.get("title_en_variant_note"), (
                f"{code}: a competing meaning must carry its note")
            # the flag is additive: the repair itself is untouched
            assert entry.get("title_en_repaired_t80"), (
                f"{code}: variant flag implies a repaired record")

    def test_variant_meaning_differs_from_the_stored_value(self, db):
        bad = [c for c, r in db.items()
               if r.get("title_en_variant_meaning")
               and str(r["title_en_variant_meaning"]).strip()
               == str(r.get("title_en")).strip()]
        assert bad == [], f"a 'variant' equal to the stored value is noise: {bad[:5]}"

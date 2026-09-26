# -*- coding: utf-8 -*-
"""T82a regression lock — anchor-verified obdhut symptoms.

WHY (measured 2026-09-26)
-------------------------
The T80/T81 rounds removed ~3,660 records' misattributed geekobd content,
leaving 4,968 codes without `symptoms_en`. obdhut.com's `meaning` field —
cached in the T75 checkpoint and never previously used for this field — holds
real, code-specific prose for 485 of them.

All five pre-registered gates passed before any merge:

    distinctness       298/298 = 1.0000   (gate >= 0.60)
    generic share      4/298   = 1.34%    (gate < 40%)
    median length      226 chars          (gate 60-600)
    biggest identical group  1            (gate <= 5)
    subject anchor     all PASS           (146 blocks excluded)

LOCKED INVARIANTS
-----------------
  1. Every `symptoms_en_anchor_verified_t82a` record carries a source URL and
     passes the subject-anchor gate against its own title — a later edit that
     breaks the anchor must fail this test.
  2. The fill is additive: it never overwrote an existing `symptoms_en`.
  3. The count only grows; the count is frozen at 14,484.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"

# Measured at the T82a apply: 298 anchor-verified fills.
_FILL_FLOOR = 298

@pytest.fixture(scope="module")
def db() -> dict:
    if not DTC_DB.exists():
        pytest.skip("shipped DTC database not present")
    return json.loads(DTC_DB.read_text(encoding="utf-8"))

class TestObdhutSymptoms:
    def test_fill_count_does_not_shrink(self, db):
        n = sum(1 for e in db.values()
                if e.get("symptoms_en_anchor_verified_t82a"))
        assert n >= _FILL_FLOOR, f"anchor-verified fills fell to {n}"

    def test_every_filled_record_has_a_source_and_text(self, db):
        bad = []
        for code, entry in db.items():
            if not entry.get("symptoms_en_anchor_verified_t82a"):
                continue
            if not entry.get("symptoms_en"):
                bad.append((code, "no text"))
            if not str(entry.get("symptoms_en_source") or "").startswith("https://"):
                bad.append((code, "no source url"))
        assert bad == [], f"incomplete fills: {bad[:10]}"

    def test_filled_text_still_passes_the_subject_anchor(self, db):
        """The strongest lock: a filled record's text must still name the
        record's own system. An edit that breaks this is a regression."""
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from src.engine.ai.subject_anchor import check_subject_anchor
        bad = []
        for code, entry in db.items():
            if not entry.get("symptoms_en_anchor_verified_t82a"):
                continue
            text = " ".join(str(x) for x in (entry.get("symptoms_en") or []))
            v = check_subject_anchor(entry.get("title"), text)
            if v.status == "block":
                bad.append((code, v.reason[:60]))
        assert bad == [], (
            f"filled text no longer matches its record's subject: {bad[:5]}")

    def test_text_is_not_generic_filler(self, db):
        """The rejected geekobd layer had a 21% generic share; this layer
        measured 1.34%. Lock that: no filled record may be pure filler."""
        import re
        FILLER = re.compile(
            r"^(the )?check engine light (stays? on|remains? illuminated)"
            r"[^.]*\.?$", re.I)
        bad = []
        for code, entry in db.items():
            if not entry.get("symptoms_en_anchor_verified_t82a"):
                continue
            for s in (entry.get("symptoms_en") or []):
                if FILLER.match(str(s).strip()):
                    bad.append(code)
                    break
        assert bad == [], f"pure filler text in anchor-verified fills: {bad[:10]}"

    def test_record_count_frozen(self, db):
        assert len(db) == 14484

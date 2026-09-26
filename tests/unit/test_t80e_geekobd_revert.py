# -*- coding: utf-8 -*-
"""T80e data-quality lock — GeekOBD subject-conflict content must not spread.

WHY (measured 2026-09-26)
-------------------------
The T77b harvest merged GeekOBD `symptoms_en` into 6,330 codes. Its gate
measured DISTINCTNESS (every code got different text) but never asked whether
the text belongs to THAT code. Measured on the cached corpus:

    U011B  DB title : Lost Communication With Rocker Arm Control Module A
           page     : Lost Communication with Anti-lock Brake System (ABS)
                      Control Module
    P04CE  DB title : EGR Temperature Sensor C Circuit
           page     : "a fault in the PCV system" / "faulty PCV valve"

4,583 of 6,330 pages (72.4%) are about a different subject than the DB record;
99.7% of sampled pages are internally consistent, so the page is not garbled —
it is consistently about another code.

The T80e repair removed the geekobd-sourced fields on the 79 codes with an
INDEPENDENT confirming source and flagged the remaining 3,886.

LOCKED INVARIANTS
-----------------
  1. A record marked `geekobd_reverted_t80e` carries NO geekobd-sourced
     `symptoms_en` / `causes_en` — the revert is complete, not partial.
  2. Every reverted record keeps its audit trail: `geekobd_revert_evidence`
     and `geekobd_revert_backup` (sha256 of the removed payload).
  3. The revert count only grows: a future merge must not re-introduce
     geekobd content into a reverted record (the T80 merge guard + this
     ceiling).
  4. Record count stays frozen at 14,484.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"
LIST = REPO_ROOT / "output" / "t80_work" / "geek_revert_list.json"

# Measured at the T80e apply (2026-09-26): 79 evidence-backed reverts.
_REVERT_FLOOR = 79
# Ratchet: the flagged subject-conflict count. It may be lowered as codes gain
# an independent vote (and are then reverted), never raised.
_FLAG_CEILING = 3886

@pytest.fixture(scope="module")
def db() -> dict:
    if not DTC_DB.exists():
        pytest.skip("shipped DTC database not present")
    return json.loads(DTC_DB.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# 1. The revert is complete and traceable
# ---------------------------------------------------------------------------

class TestRevertIntegrity:
    def test_reverted_records_carry_no_geekobd_fields(self, db):
        bad = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t80e"):
                continue
            for f in ("symptoms_en_source", "causes_en_source"):
                if "geekobd" in str(entry.get(f) or ""):
                    bad.append((code, f))
        assert bad == [], f"incomplete revert: {bad[:10]}"

    def test_every_reverted_record_has_an_audit_trail(self, db):
        missing = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t80e"):
                continue
            if not entry.get("geekobd_revert_evidence"):
                missing.append((code, "evidence"))
            if not entry.get("geekobd_revert_backup"):
                missing.append((code, "backup"))
        assert missing == [], f"missing audit trail: {missing[:10]}"

    def test_backup_hash_is_a_sha256(self, db):
        bad = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t80e"):
                continue
            h = str(entry.get("geekobd_revert_backup") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", h):
                bad.append(code)
        assert bad == [], f"backup hash is not a sha256: {bad[:10]}"

    def test_revert_count_does_not_shrink(self, db):
        n = sum(1 for e in db.values() if e.get("geekobd_reverted_t80e"))
        assert n >= _REVERT_FLOOR, (
            f"revert count fell to {n} (floor {_REVERT_FLOOR}) — a reverted "
            f"record must never silently regain unverified content")

    def test_subject_conflict_flags_do_not_grow(self, db):
        n = sum(1 for e in db.values() if e.get("geekobd_subject_conflict_t80e"))
        assert n <= _FLAG_CEILING, (
            f"subject-conflict flags rose to {n} (ceiling {_FLAG_CEILING}) — "
            f"a merge introduced new unverified-subject content")

    def test_flagged_records_keep_their_subject_metadata(self, db):
        missing = [c for c, e in db.items()
                   if e.get("geekobd_subject_conflict_t80e")
                   and "geekobd_conflict_page_sys" not in e]
        assert missing == [], f"flag without subject metadata: {missing[:10]}"

# ---------------------------------------------------------------------------
# 2. The evidence list itself is well-formed
# ---------------------------------------------------------------------------

class TestRevertList:
    def test_list_exists_and_matches_the_db_floor(self):
        if not LIST.exists():
            pytest.skip("revert list not present")
        data = json.loads(LIST.read_text(encoding="utf-8"))
        assert len(data["revert"]) == _REVERT_FLOOR
        for item in data["revert"]:
            assert item["code"]
            assert item["votes"], "every revert needs a confirming source"
            assert item["fields"], "every revert needs at least one field"

    def test_vague_and_placeholder_codes_are_not_reverted(self):
        """The 633 vague and 848 placeholder codes must stay untouched."""
        if not LIST.exists():
            pytest.skip("revert list not present")
        data = json.loads(LIST.read_text(encoding="utf-8"))
        assert len(data["vague"]) > 0 and len(data["placeholder"]) > 0
        reverted = {x["code"] for x in data["revert"]}
        assert not (reverted & set(data["vague"]))
        assert not (reverted & set(data["placeholder"]))

    def test_record_count_frozen(self, db):
        assert len(db) == 14484

# ---------------------------------------------------------------------------
# 5. Round 2 (T80m): the locally-arbitrated reverts
# ---------------------------------------------------------------------------

class TestRound2Revert:
    """71 more codes were reverted from locally-available arbiters."""

    def test_round2_records_carry_no_geekobd_fields(self, db):
        bad = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t80m"):
                continue
            for f in ("symptoms_en_source", "causes_en_source",
                      "description_en_source"):
                if "geekobd" in str(entry.get(f) or ""):
                    bad.append((code, f))
        assert bad == [], f"incomplete round-2 revert: {bad[:10]}"

    def test_round2_records_keep_their_vote_evidence(self, db):
        missing = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t80m"):
                continue
            if not entry.get("geekobd_revert_evidence_t80m"):
                missing.append((code, "evidence"))
            if not re.fullmatch(r"[0-9a-f]{64}",
                                str(entry.get("geekobd_revert_backup_t80m") or "")):
                missing.append((code, "backup"))
        assert missing == [], f"missing round-2 audit trail: {missing[:10]}"

    def test_round2_count_does_not_shrink(self, db):
        n = sum(1 for e in db.values() if e.get("geekobd_reverted_t80m"))
        assert n >= 71, f"round-2 revert count fell to {n} (floor 71)"

    def test_total_reverted_geekobd_codes(self, db):
        """Both rounds together: 79 (T80e) + 71 (T80m) = 150."""
        n = sum(1 for e in db.values()
                if e.get("geekobd_reverted_t80e") or e.get("geekobd_reverted_t80m"))
        assert n >= 150, f"total geekobd reverts fell to {n} (floor 150)"

# ---------------------------------------------------------------------------
# 6. Round 3 (T80o): the corpus-arbitrated reverts
# ---------------------------------------------------------------------------

class TestRound3Revert:
    """1,508 more codes, each with an external corpus string equal to the DB
    title while the GeekOBD page named a disjoint system."""

    def test_round3_records_carry_no_geekobd_fields(self, db):
        bad = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t80o"):
                continue
            for f in ("symptoms_en_source", "causes_en_source",
                      "description_en_source"):
                if "geekobd" in str(entry.get(f) or ""):
                    bad.append((code, f))
        assert bad == [], f"incomplete round-3 revert: {bad[:10]}"

    def test_round3_records_keep_their_audit_trail(self, db):
        missing = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t80o"):
                continue
            if not entry.get("geekobd_revert_evidence_t80o"):
                missing.append((code, "evidence"))
            if not re.fullmatch(r"[0-9a-f]{64}",
                                str(entry.get("geekobd_revert_backup_t80o") or "")):
                missing.append((code, "backup"))
        assert missing == [], f"missing round-3 trail: {missing[:10]}"

    def test_round3_count_does_not_shrink(self, db):
        n = sum(1 for e in db.values() if e.get("geekobd_reverted_t80o"))
        assert n >= 1508, f"round-3 revert count fell to {n} (floor 1508)"

    def test_total_geekobd_reverts(self, db):
        """All three rounds: 79 + 71 + 1508 = 1658."""
        n = sum(1 for e in db.values()
                if e.get("geekobd_reverted_t80e")
                or e.get("geekobd_reverted_t80m")
                or e.get("geekobd_reverted_t80o"))
        assert n >= 1658, f"total geekobd reverts fell to {n} (floor 1658)"

    def test_weak_single_token_codes_are_NOT_reverted(self, db):
        """Tier C (a single shared token) must stay flagged, not reverted."""
        import json as _json
        from pathlib import Path as _P
        p = _P(__file__).resolve().parents[2] / "output" / "t80_work" / "verify" / "round2_tiered.json"
        if not p.exists():
            pytest.skip("tiered list not present")
        weak = {x["code"] for x in _json.loads(p.read_text(encoding="utf-8"))["weak_flagged"]}
        reverted = {c for c, e in db.items()
                    if e.get("geekobd_reverted_t80o")}
        overlap = weak & reverted
        assert overlap == set(), (
            f"single-token (weak) codes must not be reverted: {sorted(overlap)[:5]}")

# ---------------------------------------------------------------------------
# 7. T81b: the extended-lexicon round
# ---------------------------------------------------------------------------

class TestT81bRevert:
    """37 further codes, found once the subject-anchor lexicon grew to 110
    component classes (the gate's blocking power tripled)."""

    def test_t81b_records_carry_no_geekobd_fields(self, db):
        bad = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t81b"):
                continue
            for f in ("symptoms_en_source", "causes_en_source",
                      "description_en_source"):
                if "geekobd" in str(entry.get(f) or ""):
                    bad.append((code, f))
        assert bad == [], f"incomplete T81b revert: {bad[:10]}"

    def test_t81b_records_keep_their_audit_trail(self, db):
        missing = []
        for code, entry in db.items():
            if not entry.get("geekobd_reverted_t81b"):
                continue
            if not entry.get("geekobd_revert_evidence_t81b"):
                missing.append((code, "evidence"))
            if not re.fullmatch(r"[0-9a-f]{64}",
                                str(entry.get("geekobd_revert_backup_t81b") or "")):
                missing.append((code, "backup"))
        assert missing == [], f"missing T81b trail: {missing[:10]}"

    def test_total_geekobd_reverts_across_all_rounds(self, db):
        """79 + 71 + 1508 + 37 = 1695."""
        n = sum(1 for e in db.values()
                if any(e.get(k) for k in ("geekobd_reverted_t80e",
                                          "geekobd_reverted_t80m",
                                          "geekobd_reverted_t80o",
                                          "geekobd_reverted_t81b")))
        assert n >= 1695, f"total geekobd reverts fell to {n} (floor 1695)"

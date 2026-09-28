# -*- coding: utf-8 -*-
"""T80i regression lock — obdhut-misattributed `causes_en` must stay reverted.

WHY (measured 2026-09-26)
-------------------------
obdhut.com is the cited source for `causes_en` on 4,179 codes. Of the 312
whose cached subtitle could be compared, 100 name a system DISJOINT from the
DB `title`:

    C0504  DB title : Left Front Wheel Speed Sensor A Intermittent/Erratic
           obdhut   : Steering Assist Control Solenoid Return Circuit High
    C0663  DB title : Left Rear Wheel Speed Sensor B Intermittent/Erratic
           obdhut   : Level Control Exhaust Valve Circuit High

Seven of those have an INDEPENDENT arbiter (the pinned OBDex corpus) that
confirms the DB `title` and contradicts obdhut; zero have an arbiter
confirming obdhut. Those seven were reverted in the T80i apply (the one-shot
script has been removed; the audit trail below is the surviving evidence).

LOCKED INVARIANTS
-----------------
  1. A record marked `obdhut_reverted_t80i` carries NO obdhut-sourced
     `causes_en`.
  2. Every reverted record keeps its audit trail: `obdhut_revert_evidence`
     and `obdhut_revert_backup` (sha256 of the removed payload).
  3. The revert count only grows — a future merge must not re-introduce
     obdhut content into a reverted record.
  4. Record count stays frozen at 14,484.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"

# Measured at the T80i apply (2026-09-26): 7 arbiter-confirmed reverts.
_REVERT_FLOOR = 7

@pytest.fixture(scope="module")
def db() -> dict:
    if not DTC_DB.exists():
        pytest.skip("shipped DTC database not present")
    return json.loads(DTC_DB.read_text(encoding="utf-8"))

class TestObdhutRevert:
    def test_reverted_records_carry_no_obdhut_causes(self, db):
        bad = [c for c, e in db.items()
               if e.get("obdhut_reverted_t80i")
               and "obdhut" in str(e.get("causes_en_source") or "")]
        assert bad == [], f"incomplete obdhut revert: {bad[:10]}"

    def test_every_reverted_record_has_an_audit_trail(self, db):
        missing = []
        for code, entry in db.items():
            if not entry.get("obdhut_reverted_t80i"):
                continue
            if not entry.get("obdhut_revert_evidence"):
                missing.append((code, "evidence"))
            h = str(entry.get("obdhut_revert_backup") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", h):
                missing.append((code, "backup"))
            if not entry.get("obdhut_revert_reason"):
                missing.append((code, "reason"))
        assert missing == [], f"missing audit trail: {missing[:10]}"

    def test_revert_count_does_not_shrink(self, db):
        n = sum(1 for e in db.values() if e.get("obdhut_reverted_t80i"))
        assert n >= _REVERT_FLOOR, (
            f"obdhut revert count fell to {n} (floor {_REVERT_FLOOR}) — a "
            f"reverted record must not silently regain unverified content")

    def test_record_count_frozen(self, db):
        assert len(db) == 14484

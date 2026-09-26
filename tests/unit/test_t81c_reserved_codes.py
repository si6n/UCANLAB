# -*- coding: utf-8 -*-
"""T81c regression lock — reserved codes must not present fault content.

WHY (measured 2026-09-26)
-------------------------
416 records carry a title of the form "<CODE> - ISO/SAE Reserved (üretici
ataması yok)" — the standard leaves them unassigned. Yet 317 of them carried
geekobd-sourced `symptoms_en` as if the code had a meaning:

    C0006  title: C0006 - ISO/SAE Reserved
           symptoms_en: "Illuminated warning lights (ABS, traction control,
                         or check engine light)"

and they were findable: `search_dtc_by_symptom("brake pedal switch")` returned
C0116 and C0136 (both reserved). Presenting a reserved code as a diagnosis is
a fabrication failure — a reserved code has no value to report.

The T80 flagging pass missed these because it keyed on whether the title named
a system, and "ISO/SAE Reserved" names none.

LOCKED INVARIANTS
-----------------
  1. No reserved-title record carries a geekobd-sourced EN field.
  2. Non-geekobd content is preserved (a licensed or curated source may
     legitimately record what a manufacturer did with a reserved code).
  3. Every reserved record is flagged with its removal audit trail.
  4. Record count stays frozen at 14,484.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"

def _is_reserved(title) -> bool:
    t = str(title or "").lower()
    return "reserved" in t or "üretici ataması yok" in t

@pytest.fixture(scope="module")
def db() -> dict:
    if not DTC_DB.exists():
        pytest.skip("shipped DTC database not present")
    return json.loads(DTC_DB.read_text(encoding="utf-8"))

@pytest.fixture(scope="module")
def reserved(db) -> list[str]:
    return [c for c, e in db.items() if _is_reserved(e.get("title"))]

class TestReservedCodes:
    def test_reserved_group_exists(self, reserved):
        assert len(reserved) > 300, "the reserved set must be present"

    def test_no_reserved_code_carries_geekobd_content(self, db, reserved):
        bad = []
        for code in reserved:
            for f in ("symptoms_en_source", "causes_en_source",
                      "description_en_source"):
                if "geekobd" in str(db[code].get(f) or ""):
                    bad.append((code, f))
        assert bad == [], f"reserved codes still carry geekobd text: {bad[:10]}"

    def test_every_reserved_code_is_flagged(self, db, reserved):
        missing = [c for c in reserved
                   if not db[c].get("reserved_code_content_flagged_t81c")]
        assert missing == [], f"unflagged reserved codes: {missing[:10]}"

    def test_removal_trail_is_recorded(self, db, reserved):
        for code in reserved:
            entry = db[code]
            if not entry.get("reserved_code_fields_removed_t81c"):
                continue
            assert entry.get("reserved_code_reason_t81c"), code
            assert re.fullmatch(
                r"[0-9a-f]{64}",
                str(entry.get("reserved_code_removal_backup_t81c") or "")), code

    def test_non_geekobd_content_is_preserved(self, db, reserved):
        """At least some reserved codes kept legitimate non-geekobd content."""
        kept = [c for c in reserved
                if db[c].get("reserved_code_non_geekobd_kept_t81c")]
        assert kept, "the fix must preserve non-geekobd content, not blanket-clear"

    def test_reserved_codes_without_a_concrete_meaning_do_not_surface(self):
        """A reserved code that carries NO concrete meaning must not be
        offered as a diagnosis.

        Scope note (measured): some reserved codes DO carry a concrete
        meaning from a licensed/independent source (C0026 = "RF Wheel Speed
        Sensor Circuit Intermittent", openlaborproject.com). Those are
        legitimate — a manufacturer may assign a meaning the SAE table leaves
        blank — and are deliberately kept. This test covers only the codes
        whose `title_en` is still the bare reserved string.
        """
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from src.engine.ai.diagnostic_copilot import (
            EXPERT_KNOWLEDGE_BASE,
            ensure_external_dtc_database_loaded,
            search_dtc_by_symptom,
        )
        ensure_external_dtc_database_loaded()
        bare_reserved = {
            c for c, e in EXPERT_KNOWLEDGE_BASE.items()
            if _is_reserved(e.get("title"))
            and _is_reserved(e.get("title_en") or e.get("title"))
        }
        assert bare_reserved, "the bare-reserved set must be present"
        for q in ("brake pedal switch", "wheel speed sensor circuit",
                  "steering angle sensor"):
            hits = search_dtc_by_symptom(q, limit=5)
            bad = [h["code"] for h in hits if h["code"] in bare_reserved]
            assert bad == [], (
                f'meaningless reserved codes surfaced for "{q}": {bad}')

    def test_record_count_frozen(self, db):
        assert len(db) == 14484

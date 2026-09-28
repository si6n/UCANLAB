# -*- coding: utf-8 -*-
"""T80k regression lock — J1939 `description_en` must carry its provenance.

WHY (measured 2026-09-26)
-------------------------
2,845 SPN records carried `description_en` but no `description_en_source`, so
a consumer following the field-name convention could not find the provenance.
The provenance WAS on the record under source-specific keys
(`_source_ref_sitrak`, `_source_license_sitrak`, CC-BY-4.0) — a naming
inconsistency, not missing data. The T80k backfill copied
the existing reference into the conventional field (no fabrication).

LOCKED INVARIANTS
-----------------
  1. Every record with `description_en` also carries
     `description_en_source` — a reader must never have to know a
     source-specific key name to find the provenance.
  2. The backfilled records keep their original sitrak reference: the value
     written is the URL that was already on the record.
  3. The backfill count does not shrink (a later merge must not strip it).
  4. SPN count stays frozen at 4,291.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
J1939_DB = REPO_ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"

# Measured at the T80k apply: 2,845 backfilled (all remaining had none).
_BACKFILL_FLOOR = 2845

@pytest.fixture(scope="module")
def spns() -> dict:
    if not J1939_DB.exists():
        pytest.skip("shipped J1939 database not present")
    data = json.loads(J1939_DB.read_text(encoding="utf-8"))
    return data["spns"]

class TestDescriptionProvenance:
    def test_every_description_en_has_a_source(self, spns):
        missing = [k for k, e in spns.items()
                   if e.get("description_en") and not e.get("description_en_source")]
        assert missing == [], (
            f"{len(missing)} records carry description_en without "
            f"description_en_source: {missing[:10]}")

    def test_backfilled_records_keep_the_sitrak_reference(self, spns):
        bad = []
        for k, e in spns.items():
            if not e.get("description_en_source_backfilled_t80k"):
                continue
            ref = str(e.get("description_en_source") or "")
            if "STAS63-bit/sitrak-error-codes" not in ref:
                bad.append((k, ref[:60]))
        assert bad == [], f"backfilled source is not the original reference: {bad[:5]}"

    def test_backfill_count_does_not_shrink(self, spns):
        n = sum(1 for e in spns.values()
                if e.get("description_en_source_backfilled_t80k"))
        assert n >= _BACKFILL_FLOOR, (
            f"backfill count fell to {n} (floor {_BACKFILL_FLOOR})")

    def test_license_is_recorded_where_known(self, spns):
        bad = []
        for k, e in spns.items():
            if not e.get("description_en_source_backfilled_t80k"):
                continue
            if e.get("_source_license_sitrak") and not e.get("description_en_license"):
                bad.append(k)
        assert bad == [], f"license not carried to the conventional field: {bad[:5]}"

    def test_spn_count_frozen(self, spns):
        assert len(spns) == 4291

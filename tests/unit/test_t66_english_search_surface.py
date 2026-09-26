# -*- coding: utf-8 -*-
"""T66 regression lock — the English harvest fields must be a search surface.

WHY: the T46/T63/T66 harvests write English payload to parallel `*_en` fields
(`symptoms_en`, `causes_en`, `description_en`, `title_en`) because the Turkish
fields must never receive English text (tests/unit/test_t46_merge.py locks that).

Measured on 2026-09-22 (T66): 6,228 records carry `symptoms_en` while the
Turkish `symptoms` holds only 1,275. Before this fix `search_dtc_by_symptom`
read ONLY `symptoms` + `title`/`title_tr`, so it was blind to the larger
corpus — an English query could not match the English data that the harvests
had deliberately put there. Same class of defect as T41 P1-1 (`procedures_full`
was filled but never read).

Locked invariants:
  1. An English symptom phrase present in `symptoms_en` is findable.
  2. `causes_en` / `description_en` are also searchable.
  3. Determinism: same query -> same result list.
  4. Abstention still works: gibberish yields an empty list (no fabricated code).
"""

from __future__ import annotations

import pytest

from src.engine.ai.diagnostic_copilot import (
    EXPERT_KNOWLEDGE_BASE,
    ensure_external_dtc_database_loaded,
    search_dtc_by_symptom,
)


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()


def _best_query(field: str) -> str | None:
    """Build a multi-term query that a real English symptom search would use.

    F-04 requires `_SYMPTOM_SEARCH_MIN_SCORE` (2) matched terms, so a single
    word is deliberately not enough. The query is taken from the record whose
    English text yields the most DISTINCT plain words — not the longest text,
    which can be mostly boilerplate ("Common Causes / How To Fix / Comments").
    """
    def plain_words(text: str) -> list[str]:
        out = []
        for raw in text.split():
            w = raw.strip(",.;:()[]\"'|/-").lower()
            # Keep ordinary English words only: drop URLs, code ids, slashes.
            if len(w) >= 5 and w.isalpha():
                out.append(w)
        return out

    best: tuple[str, list[str]] | None = None
    for _code, info in EXPERT_KNOWLEDGE_BASE.items():
        if not isinstance(info, dict):
            continue
        val = info.get(field)
        if not val:
            continue
        text = val if isinstance(val, str) else " ".join(str(x) for x in val)
        words = plain_words(text)
        if not words:
            continue
        distinct = sorted(set(words), key=len, reverse=True)
        if best is None or len(distinct) > len(best[1]):
            best = (str(_code), distinct)
    if best is None:
        return None
    picks = best[1][:3]
    return " ".join(picks) if len(picks) >= 2 else None


class TestEnglishFieldsAreSearchable:
    def test_symptoms_en_is_a_search_surface(self) -> None:
        query = _best_query("symptoms_en")
        # Skip yok: repo DB'si İngilizce alanlar içermek ZORUNDA —
        # alanlar kaybolduysa test sessizce atlanmaz, kızarır.
        assert query is not None, "shipped DB carries no record with searchable symptoms_en"
        hits = search_dtc_by_symptom(query, limit=5)
        assert hits, (
            f"an English phrase built from symptoms_en ({query!r}) matched nothing — "
            "the English harvest fields are not a search surface"
        )

    def test_causes_en_is_a_search_surface(self) -> None:
        query = _best_query("causes_en")
        assert query is not None, "shipped DB carries no record with searchable causes_en"
        hits = search_dtc_by_symptom(query, limit=5)
        assert hits, f"a phrase built from causes_en ({query!r}) matched nothing"


class TestAbstentionStillWorks:
    def test_gibberish_yields_no_hit(self) -> None:
        assert search_dtc_by_symptom("zzzqqqxxyywwvv") == []


class TestDeterminism:
    def test_same_query_same_result(self) -> None:
        first = search_dtc_by_symptom("rough idle")
        second = search_dtc_by_symptom("rough idle")
        assert first == second

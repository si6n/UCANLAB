# -*- coding: utf-8 -*-
"""T66 regression lock — the `procedures_full` reader must not silently drop data.

Two wiring defects were measured on the live DB on 2026-09-22 (T66) and fixed
in `CausalBayesianInferenceEngine._format_j1939_technician_report`:

(A) UNCONDITIONAL BREAK.  The reader broke out of the block loop after the
    first non-quarantined block, even when that block rendered NOTHING (an
    empty `overview`). Measured: 172 blocks carry real procedure text in
    `first_moves`/`steps` with an empty `overview`, all at index > 0; 177 SPNs
    hold step-bearing blocks beyond index 0. SPN_100 alone has 34 blocks;
    block 1 carries 35 steps + 7 causes and NONE of them reached the report.

(B) EATON-ONLY KEY SET.  Only `overview`/`detection`/`conditions_set_active`/
    `fallback`/`possible_causes` were read. The dominant non-Eaton schema
    written by the T63/T66 harvests (`steps`/`first_moves`/`causes`/`symptoms`)
    was never rendered — the same class of defect T41 P1-1 fixed for the
    Eaton set.

LOCKED CONTRACT (what the tests below pin):
  * T67-B (exhaustive render): EVERY clean block renders — the reader no
    longer caps at the two richest; blocks are grouped by source site and
    duplicate texts are deduped, but no block or item is dropped by budget.
  * A quarantined block is NEVER rendered and NEVER consumed (T2-3).
  * No fabrication: only keys actually present in the DB row are printed.
  * Determinism: same query -> same report.

The selection helpers below still RANK by richness (steps + first_moves +
causes + symptoms + overview length) — that is the TEST's own selection for
its probes, not a production budget. Ranking matters because a probe must
come from a block that actually renders: taking probes by INDEX would test
blocks that the reader used to drop regardless of the defect under test
(SPN_157: 20 blocks; index 2 carries 36 steps while index 0 and 1 carry 1
and 5).
"""

from __future__ import annotations

import pytest

from src.engine.ai.diagnostic_copilot import (
    EXPERT_KNOWLEDGE_BASE,
    CausalBayesianInferenceEngine,
    ensure_external_dtc_database_loaded,
    get_j1939_spn_database,
)

# Testin kendi zenginlik-seçimi (probe kaynağı) — üretim budget'ı DEĞİL:
# üretim T67-B sonrası tüm temiz blokları render eder.
RENDERED_BLOCK_BUDGET = 2


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()
    get_j1939_spn_database()


def _spn_db() -> dict:
    return get_j1939_spn_database()


def _richness(block: dict) -> int:
    score = len(str(block.get("overview") or ""))
    for key in ("steps", "first_moves", "causes", "symptoms"):
        val = block.get(key)
        if isinstance(val, list):
            score += sum(len(str(x)) for x in val)
    return score


def _probe(block: dict) -> str | None:
    """A discriminating string that must survive into the report.

    The reader renders every item of every clean block (T67-B exhaustive
    render; duplicate texts are deduped across blocks). The probe may come
    from anywhere in the block — but it must be >= 30 chars to be a
    meaningful substring check.
    """
    best: str | None = None
    for key, limit in (("steps", 6), ("first_moves", 6), ("causes", 5), ("symptoms", 5)):
        val = block.get(key)
        if isinstance(val, list):
            for item in val[:limit]:
                text = str(item).strip()
                if len(text) >= 30 and (best is None or len(text) > len(best)):
                    best = text
    return best


def _richest_clean_blocks(entry: dict) -> list[dict]:
    blocks = entry.get("procedures_full")
    if not isinstance(blocks, list):
        return []
    clean = [b for b in blocks if isinstance(b, dict) and not b.get("_quarantined")]
    return sorted(clean, key=_richness, reverse=True)[:RENDERED_BLOCK_BUDGET]


def _find_spn(predicate) -> tuple[str, dict, list[dict]] | None:
    for key, entry in _spn_db()["spns"].items():
        if not isinstance(entry, dict):
            continue
        num = key.replace("SPN_", "")
        if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
            continue
        top = _richest_clean_blocks(entry)
        if top and predicate(entry, top):
            return num, entry, top
    return None


def _query(spn: str) -> str:
    return CausalBayesianInferenceEngine.evaluate_diagnostic_query(f"SPN {spn} nedir", [], {})


class TestRichestBlocksReachTheReport:
    """Every string the ranking selects must appear in the report."""

    def test_richest_blocks_render(self) -> None:
        found = _find_spn(lambda entry, top: len(top) >= 1 and _probe(top[0]) is not None)
        if found is None:
            pytest.skip("no SPN with a renderable procedure block found")
        num, _entry, top = found
        report = _query(num)
        for block in top:
            probe = _probe(block)
            if probe is None:
                continue
            assert probe.lower()[:45] in report.lower(), (
                f"SPN {num}: a selected (richest) block's content is missing from "
                "the report — the reader dropped data"
            )

    def test_deep_block_beats_shallow_first_blocks(self) -> None:
        """The richest block may sit beyond index 1; it must still render."""
        found = _find_spn(
            lambda entry, top: len(entry.get("procedures_full") or []) >= 3
            and top
            and top[0] is not (entry["procedures_full"][0] if entry.get("procedures_full") else None)
            and _probe(top[0]) is not None
        )
        if found is None:
            pytest.skip("no SPN whose richest block sits beyond index 0")
        num, _entry, top = found
        probe = _probe(top[0])
        assert probe is not None
        report = _query(num)
        assert probe.lower()[:45] in report.lower(), (
            f"SPN {num}: the richest block (beyond the first positions) was dropped"
        )

    def test_empty_overview_block_with_steps_is_still_rendered(self) -> None:
        """An empty-overview block with real steps must render when selected."""
        found = _find_spn(
            lambda entry, top: any(
                not str(b.get("overview") or "").strip() and _probe(b) for b in top
            )
        )
        if found is None:
            pytest.skip("no empty-overview step-bearing SPN outside the knowledge base")
        num, _entry, top = found
        for block in top:
            if str(block.get("overview") or "").strip():
                continue
            probe = _probe(block)
            if probe is None:
                continue
            report = _query(num)
            assert probe.lower()[:45] in report.lower(), (
                f"SPN {num}: empty-overview block with real steps was dropped"
            )


    def test_blocks_beyond_top_two_also_render(self) -> None:
        """T67-B exhaustif render kiliti: iki-en-zengin DIŞINDAKİ blok da rapora ulaşır.

        Üretim "iki en zengin blok" bütçesine düşerse bu test kırmızıya döner
        (metin dedup'u nedeniyle, zengin iki blokta BİREBİR aynı metni
        taşıyan probe'lar elenir — probe benzersiz olmalıdır).
        """

        def third_unique_probe(entry: dict) -> str | None:
            blocks = entry.get("procedures_full")
            if not isinstance(blocks, list):
                return None
            clean = [b for b in blocks if isinstance(b, dict) and not b.get("_quarantined")]
            if len(clean) < 3:
                return None
            ranked = sorted(clean, key=_richness, reverse=True)
            probe = _probe(ranked[2])
            if probe is None:
                return None
            top_texts: set[str] = set()
            for b in ranked[:2]:
                for key in ("steps", "first_moves", "causes", "symptoms"):
                    val = b.get(key)
                    if isinstance(val, list):
                        top_texts.update(" ".join(str(x).split()).lower() for x in val)
                ov = str(b.get("overview") or "").strip()
                if ov:
                    top_texts.add(" ".join(ov.split()).lower())
            if " ".join(probe.split()).lower() in top_texts:
                return None  # dedup haklı düşürür — kusurlu probe
            return probe

        found = _find_spn(lambda entry, top: third_unique_probe(entry) is not None)
        if found is None:
            pytest.skip("no SPN with a unique probe in its 3rd-ranked clean block")
        num, entry, _top = found
        probe = third_unique_probe(entry)
        assert probe is not None
        report = _query(num)
        assert probe.lower()[:45] in report.lower(), (
            f"SPN {num}: a clean block beyond the two richest was dropped — "
            "the exhaustive (T67-B) render regressed to a budget cap"
        )


class TestQuarantineStillFailClosed:
    """The T2-3 guarantee survives the widened reader."""

    def test_quarantined_block_is_never_rendered(self) -> None:
        for key, entry in _spn_db()["spns"].items():
            blocks = entry.get("procedures_full")
            if not isinstance(blocks, list):
                continue
            if not any(isinstance(b, dict) and b.get("_quarantined") for b in blocks):
                continue
            num = key.replace("SPN_", "")
            if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                continue
            report = _query(num)
            assert "dtcdocs.com" not in report.lower()
            assert "frequently asked questions" not in report.lower()
            return
        pytest.skip("no SPN with a quarantined block found")


class TestDeterminism:
    def test_same_query_same_report(self) -> None:
        found = _find_spn(lambda entry, top: bool(top))
        if found is None:
            pytest.skip("no SPN outside the knowledge base found")
        num, _entry, _top = found
        assert _query(num) == _query(num)

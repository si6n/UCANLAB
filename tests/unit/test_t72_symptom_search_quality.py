# -*- coding: utf-8 -*-
"""T72 regression lock — symptom-search retrieval quality (measured).

WHY (measured 2026-09-23 on the shipped DB, 14,496 records):
  * ``search_dtc_by_symptom("DPF clogged")`` returned an EMPTY list. The
    length>=4 token gate dropped the acronym "dpf", leaving the single term
    ["clogged"], so the F-04 min-score bar (2) abstained — although 182 records
    mention DPF and 28 mention both "dpf" and "clogged".
  * ``search_dtc_by_symptom("P0301")`` returned an EMPTY list. The literal
    token "p0301" occurs in only 2 of 14,496 haystacks, so a query that names
    its own catalog key could not resolve it.
  * Inflected operator utterances lost recall: "stalls" occurs in 46 haystacks
    vs "stall" 108; "cranks" 54 vs "crank" 150; "slips" 4 vs "slip" 154;
    "drains" 14 vs "drain" 140.
  * One merged haystack let long cause prose outrank the record's own symptom
    claim: "coolant temperature too high" ranked P0171 (lean fuel trim) first
    because its cause text happens to contain "coolant" and "temperature".

Locked invariants (all deterministic, no fabrication):
  1. Curated short acronyms are searchable; ambiguous English words are not.
  2. A trailing-'s' query term also matches its singular form.
  3. A DTC-shaped query resolves to its own catalog key when one exists.
  4. Symptom/title evidence outranks cause/description prose.
  5. Abstention and the F-04 min-score gate are unchanged.
  6. Same input -> same output.

Fixtures are SYNTHETIC (monkeypatched KB) — the shipped DB is never read here,
so the locks survive the forthcoming ``symptoms_en`` backfill.
"""

from __future__ import annotations

import pytest

import src.engine.ai.diagnostic_copilot as dc
from src.engine.ai.diagnostic_copilot import (
    EXPERT_KNOWLEDGE_BASE,
    AutomotiveTokenizer,
    _dtc_query_key,
    _symptom_search_terms,
    _symptom_term_forms,
    ensure_external_dtc_database_loaded,
    search_dtc_by_symptom,
)


@pytest.fixture(autouse=True)
def _loaded() -> None:
    """Load the real DB first so the lazy loader never touches the patch."""
    ensure_external_dtc_database_loaded()


@pytest.fixture
def synthetic_kb(monkeypatch):
    """A tiny hand-built catalog with known, controllable overlap."""
    kb = {
        # strong tier: symptom/title text
        "P9001": {
            "title": "Particulate Filter Restriction",
            "subsystem": "Aftertreatment",
            "symptoms": ["Exhaust backpressure high"],
            "symptoms_en": ["Diesel particulate filter (DPF) clogged", "Reduced engine power"],
        },
        # weak tier only: the phrase appears in cause prose, not in symptoms
        "P9002": {
            "title": "Fuel Trim Lean",
            "subsystem": "Fuel",
            "causes_en": "Restricted particulate filter | clogged converter",
        },
        # an inflected DB form for the plural test
        "P9003": {
            "title": "Engine Stop Control",
            "symptoms": ["Engine stall at idle", "Rough idle"],
        },
        "P9004": {
            "title": "Unrelated Circuit",
            "symptoms": ["Left turn signal lamp failure"],
        },
    }
    monkeypatch.setattr(dc, "EXPERT_KNOWLEDGE_BASE", kb)
    return kb


class TestShortAcronymsAreSearchable:
    """1. The length gate must not swallow canonical short acronyms."""

    def test_acronym_survives_the_token_gate(self) -> None:
        assert "dpf" in _symptom_search_terms("dpf clogged")
        assert "egr" in _symptom_search_terms("egr valve stuck")
        assert "o2" in _symptom_search_terms("o2 sensor slow")

    def test_acronym_alone_plus_stem_clears_the_min_score_bar(self, synthetic_kb) -> None:
        """The measured defect: 2 terms, not 1, so F-04 no longer abstains."""
        terms = _symptom_search_terms("dpf clogged")
        assert len(terms) >= dc._SYMPTOM_SEARCH_MIN_SCORE
        hits = search_dtc_by_symptom("dpf clogged", limit=5)
        assert hits, "an acronym+stem query must not abstain"
        assert hits[0]["code"] == "P9001"

    def test_ambiguous_english_short_words_are_still_rejected(self) -> None:
        """"can"/"def"/"air" are ordinary words — the gate keeps rejecting them."""
        assert _symptom_search_terms("can bus dropout") == ["dropout"]
        assert _symptom_search_terms("def level sensor") == ["level", "sensor"]
        assert _symptom_search_terms("air bag module") == ["module"]

    def test_short_acronym_set_excludes_ambiguous_words(self) -> None:
        assert "can" not in dc._SYMPTOM_SEARCH_SHORT_ACRONYMS
        assert "def" not in dc._SYMPTOM_SEARCH_SHORT_ACRONYMS
        assert "air" not in dc._SYMPTOM_SEARCH_SHORT_ACRONYMS


class TestPluralFolding:
    """2. A trailing-'s' query term also matches the singular DB form."""

    def test_term_forms_include_the_singular(self) -> None:
        assert _symptom_term_forms("stalls") == ("stalls", "stall")
        assert _symptom_term_forms("cranks") == ("cranks", "crank")
        assert _symptom_term_forms("slips") == ("slips", "slip")

    def test_singular_query_term_is_not_expanded_to_plural(self) -> None:
        """The reverse direction was measured and rejected (T72 experiment 7)."""
        assert _symptom_term_forms("stall") == ("stall",)
        assert _symptom_term_forms("dpf") == ("dpf",)

    def test_plural_query_matches_the_singular_db_text(self, synthetic_kb) -> None:
        """DB says "Engine stall at idle"; the operator typed "stalls"."""
        hits = search_dtc_by_symptom("engine stalls at idle", limit=5)
        assert hits
        assert hits[0]["code"] == "P9003"
        assert "stalls" in hits[0]["matched"]


class TestDtcShapedQueryResolution:
    """3. A query naming a catalog key resolves to that key."""

    def test_bare_code_query_resolves_to_its_own_record(self, synthetic_kb) -> None:
        hits = search_dtc_by_symptom("p9001", limit=1)
        assert hits, "a bare code query must not abstain"
        assert hits[0]["code"] == "P9001"

    def test_uppercase_and_spaced_forms_resolve_identically(self, synthetic_kb) -> None:
        for query in ("P9001", "p9001", "P 9001"):
            hits = search_dtc_by_symptom(AutomotiveTokenizer.normalize_text(query), limit=1)
            assert hits and hits[0]["code"] == "P9001", query

    def test_unknown_code_shape_does_not_fabricate(self, synthetic_kb) -> None:
        """A well-formed but absent key must NOT be invented."""
        assert _dtc_query_key("p7777") is None
        assert search_dtc_by_symptom("p7777", limit=3) == []

    def test_non_code_text_is_not_treated_as_a_key(self, synthetic_kb) -> None:
        assert _dtc_query_key("engine stalls") is None
        assert _dtc_query_key("p900") is None
        assert _dtc_query_key("") is None

    def test_exact_key_outranks_incidental_prose_mention(self, monkeypatch) -> None:
        """A record whose PROSE merely names another code must not outrank it."""
        kb = {
            "P9001": {
                "title": "Filter Restriction",
                "symptoms_en": ["Check companion code P9002 and P9001 for details"],
            },
            "P9002": {
                "title": "Fuel Trim Lean",
                "symptoms_en": ["Lean condition, see P9001", "Rough running, misfire"],
            },
        }
        monkeypatch.setattr(dc, "EXPERT_KNOWLEDGE_BASE", kb)
        hits = search_dtc_by_symptom("p9001", limit=5)
        assert hits[0]["code"] == "P9001", "identity must beat text overlap"


class TestEvidenceTierOrdering:
    """4. Symptom/title evidence outranks cause/description prose."""

    def test_strong_tier_outranks_weak_tier_at_equal_term_count(self, synthetic_kb) -> None:
        hits = search_dtc_by_symptom("clogged particulate", limit=5)
        codes = [h["code"] for h in hits]
        assert codes, "expected both synthetic records to match"
        # P9001 claims the symptom in its own symptom text; P9002 only mentions
        # it inside cause prose. Equal term count, so the strong tier decides.
        assert codes[0] == "P9001"
        assert "P9002" in codes

    def test_weak_tier_is_still_searchable(self, synthetic_kb) -> None:
        """Tiering reorders; it must never drop recall."""
        hits = search_dtc_by_symptom("restricted particulate", limit=5)
        assert "P9002" in [h["code"] for h in hits]


class TestAbstentionAndGateUnchanged:
    """5. F-04 and the Kanıt A abstentions survive the change."""

    def test_min_score_gate_is_still_two(self) -> None:
        assert dc._SYMPTOM_SEARCH_MIN_SCORE == 2

    def test_generic_terms_still_yield_no_hit(self) -> None:
        assert search_dtc_by_symptom("hava durumu nedir", limit=1) == []
        assert _symptom_search_terms("problem") == []

    def test_single_term_query_still_abstains(self, synthetic_kb) -> None:
        """One matching term is still not enough evidence."""
        assert search_dtc_by_symptom("clogged", limit=5) == []

    def test_gibberish_still_yields_no_hit(self) -> None:
        assert search_dtc_by_symptom("zzzqqqxxyywwvv") == []

    def test_empty_query_still_yields_no_hit(self) -> None:
        assert search_dtc_by_symptom("") == []


class TestDeterminismT72:
    """6. Same input -> same output, always."""

    @pytest.mark.parametrize(
        "query",
        ["dpf clogged", "engine stalls at idle", "p9001", "clogged particulate"],
    )
    def test_same_query_same_result(self, synthetic_kb, query: str) -> None:
        assert search_dtc_by_symptom(query, limit=5) == search_dtc_by_symptom(query, limit=5)

    def test_hit_shape_is_backward_compatible(self, synthetic_kb) -> None:
        """Callers unpack ``code``/``score``/``matched`` — the shape is unchanged."""
        hits = search_dtc_by_symptom("dpf clogged", limit=5)
        assert hits
        for hit in hits:
            assert set(hit) == {"code", "score", "matched"}
            assert isinstance(hit["code"], str)
            assert isinstance(hit["score"], int)
            assert isinstance(hit["matched"], list)

    def test_limit_is_respected(self, synthetic_kb) -> None:
        assert len(search_dtc_by_symptom("clogged particulate", limit=1)) == 1

    def test_default_limit_is_one(self, synthetic_kb) -> None:
        assert len(search_dtc_by_symptom("dpf clogged")) == 1


class TestRealCatalogSmoke:
    """A minimal smoke check against the shipped catalog (no assertions on rank)."""

    def test_dpf_query_no_longer_abstains(self) -> None:
        assert search_dtc_by_symptom("dpf clogged", limit=3), (
            "the shipped DB carries DPF records — this query must not abstain"
        )

    def test_a_known_code_resolves(self) -> None:
        if "P0301" not in EXPERT_KNOWLEDGE_BASE:
            pytest.skip("shipped catalog has no P0301")
        hits = search_dtc_by_symptom("P0301", limit=1)
        assert hits and hits[0]["code"] == "P0301"

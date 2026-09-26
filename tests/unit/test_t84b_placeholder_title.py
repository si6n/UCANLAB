"""T84b: placeholder-title resolution in the copilot renderer.

MEASURED FINDING (2026-09-26, gap-analysis agent, verified by me): 797
records carry a placeholder `title` while their `title_en` holds the real
meaning. The 4-stage report rendered only `title`, so the operator saw
"B0006 - ISO/SAE Reserved (üretici ataması yok)" while the record itself knew
the code means "Driver Frontal Deployment Loop Short to Ground". `title_en`
was used only as a search token (line ~3436), never displayed.

This is a WIRING gap, not a data gap: the meaning was already in the record.

Design rules locked here:
  * a non-placeholder title is ALWAYS returned unchanged (never overridden);
  * a placeholder title is replaced by `title_en` ONLY when `title_en` is
    itself meaningful, and the substitution is LABELLED;
  * when both are placeholders the record's own title is returned — no
    substitution is invented (AGENTS.md §2.3).
"""

import json

import pytest

from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

DTC_DB = "data/diagnostics/dtc_database.json"
E = CausalBayesianInferenceEngine


@pytest.fixture(scope="module")
def db():
    with open(DTC_DB, encoding="utf-8") as fh:
        return json.load(fh)


class TestPlaceholderDetection:
    @pytest.mark.parametrize("title", [
        "B0006 - ISO/SAE Reserved (üretici ataması yok)",
        "C0104 - ISO/SAE Reserved (üretici ataması yok)",
        "OBD Code",
        "Body Code DTC",
        "Arıza Teşhis Kaydı",
        "Manufacturer-specific network code. The second character '1' marks "
        "this as defined by the vehicle manufacturer.",
        "Generic (SAE-defined) powertrain code, covering engine, transmission "
        "and emission controls.",
        "P1024 Diagnostic Trouble Code (8 interpretations)",
        "",
    ])
    def test_recognised(self, title):
        assert E.is_placeholder_title(title), title

    @pytest.mark.parametrize("title", [
        "Cylinder #1 Misfire",
        "Kütle Hava Akış (MAF) Sensörü Devre Aralığı / Performansı",
        "Catalytic converter temperature sensor, bank 2 high input",
        "Driver Frontal Deployment Loop Short to Ground",
    ])
    def test_not_recognised(self, title):
        assert not E.is_placeholder_title(title), title


class TestDisplayTitle:
    def test_placeholder_title_with_real_en_shows_the_english_layer(self, db):
        got = E.display_title(db["B0006"], "B0006")
        assert got.startswith("Driver Frontal Deployment Loop Short to Ground")
        assert "EN katmanı" in got, "the substitution must be labelled"

    def test_real_title_is_never_overridden(self, db):
        for code in ("P0101", "P0301"):
            own = str(db[code].get("title"))
            assert E.display_title(db[code], code) == own, code

    def test_both_placeholder_returns_own_title(self, db):
        """No substitution is invented when the English layer is no better."""
        e = db["C0005"]
        assert E.display_title(e, "C0005") == str(e.get("title"))

    def test_absent_title_en_returns_own_title(self):
        info = {"title": "OBD Code"}
        assert E.display_title(info, "P9999") == "OBD Code"

    def test_missing_title_falls_back_to_the_code(self):
        assert E.display_title({}, "P9999") == "P9999"


class TestMeasuredPopulation:
    def test_the_gap_is_real_and_bounded(self, db):
        """The population this fix serves, re-measured against the live DB."""
        n = 0
        for code, e in db.items():
            if not isinstance(e, dict):
                continue
            if (E.is_placeholder_title(e.get("title"))
                    and not E.is_placeholder_title(e.get("title_en"))):
                n += 1
        assert 700 <= n <= 900, f"placeholder-title population moved: {n}"

    def test_every_resolved_record_gains_a_meaningful_title(self, db):
        """Spot-check: a resolved display title must not be a placeholder."""
        checked = 0
        for code, e in db.items():
            if not isinstance(e, dict) or not E.is_placeholder_title(
                    e.get("title")):
                continue
            got = E.display_title(e, code)
            if got != str(e.get("title") or ""):
                assert not E.is_placeholder_title(got), (code, got)
                checked += 1
            if checked >= 60:
                break
        assert checked > 0, "no resolved records found"


class TestNoFabrication:
    def test_display_never_invents_a_title(self):
        """An empty record renders as the code, not as invented text."""
        assert E.display_title({}, "U1234") == "U1234"
        assert E.display_title({"title": "", "title_en": ""}, "U1234") == "U1234"

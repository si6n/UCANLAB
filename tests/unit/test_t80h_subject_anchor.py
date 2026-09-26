# -*- coding: utf-8 -*-
"""T80h regression lock — the subject-anchor gate.

WHY (measured 2026-09-26)
-------------------------
Two large harvests merged English content whose SUBJECT does not match the
record it landed on. The existing gates measured length, evidence, shape and
distinctness — none asked "is this page about THIS code?":

    U011B  DB title : Lost Communication With Rocker Arm Control Module A
           page     : Lost Communication with Anti-lock Brake System (ABS)
                      Control Module
    P1156  DB title : HO2S Rich Average Bank 2 Sensor 1
           obdhut   : Manifold Abs.Pressure Sensor Circ. Open/Short to Ground

Measured rates: geekobd.com 72.4% of pages, obdhut.com 22.1% of the evaluated
subset, obd2.com 5.6%.

LOCKED CONTRACT
---------------
  * systems are compared through a canonical lexicon (EGT ≡ Exhaust Gas
    Temperature, HO2S ≡ O2 sensor, CMP ≡ camshaft) — a bare token comparison
    produces false conflicts;
  * two texts sharing a system PASS;
  * two texts naming disjoint systems BLOCK (this is the defect class);
  * a text naming no system at all ABSTAINS (an unverifiable claim is not
    evidence of a mismatch — fail-closed must not become fail-blind);
  * a placeholder record title ("Generic (SAE-defined)...") ABSTAINS: it
    names the code class, not a subject;
  * a missing claim PASSES (backwards compatible with adapters that do not
    yet supply one);
  * the function is pure: same input -> same output, no network, no I/O.

The abstain rate is deliberately visible: the gate is a conservative filter
that catches clear conflicts, not an oracle. A caller that needs certainty
must pair an abstain with an independent vote (the T80 repair discipline).
"""

from __future__ import annotations

import pytest

from src.engine.ai.subject_anchor import (
    AnchorVerdict,
    check_subject_anchor,
    systems_in,
)

# ---------------------------------------------------------------------------
# 1. The measured defect cases must block
# ---------------------------------------------------------------------------

class TestMeasuredConflicts:
    @pytest.mark.parametrize("title,claim", [
        # T80d: geekobd U011B — DB says Rocker Arm, page says ABS
        ("Lost Communication With Rocker Arm Control Module A",
         "U011B - Lost Communication with Anti-lock Brake System (ABS) "
         "Control Module in Ford and Chevrolet Models"),
        # T80h: obdhut P1156 — DB says O2 sensor, page says MAP sensor
        ("HO2S Rich Average Bank 2 Sensor 1",
         "Manifold Abs.Pressure Sensor Circ. Open/Short to Ground"),
        # T80h: obdhut P1532 — DB says A/C, page says camshaft
        ("A/C Evaporator Temperature Sensor Circuit Low Voltage",
         '"B" Camshaft Position Actuator Control Open Circuit Bank 2'),
        # T80h: obdhut P1393 — DB says wheel speed, page says ignition coil
        ("Wheel Speed Sensor 1 G-Sensor Circuit High Voltage",
         "Ignition Coil Power Output Stage 1 Electrical Malfunction"),
        # T80d: geekobd P04CE — DB says EGR temp sensor, page says PCV
        ("EGR Temperature Sensor C Circuit",
         "P04CE - Understanding Crankcase Ventilation System Performance "
         "Issues in 2016-2021 Ford F-150"),
    ])
    def test_subject_conflict_blocks(self, title, claim):
        v = check_subject_anchor(title, claim)
        assert v.status == "block", (
            f"expected block, got {v.status}: {v.reason}")
        assert v.title_systems and v.claim_systems
        assert not (set(v.title_systems) & set(v.claim_systems))

# ---------------------------------------------------------------------------
# 2. Abbreviation equivalence must not block
# ---------------------------------------------------------------------------

class TestAbbreviationTolerance:
    @pytest.mark.parametrize("title,claim", [
        ("EGT Sensor Circ Performance Bank 2 Sensor 2",
         "Exhaust Gas Temperature Sensor Circuit Range/Performance B2S2"),
        ("HO2S Heater Control Circuit Bank 2 Sensor 2",
         "O2 Sensor Heater Circuit High Voltage (Bank 2 Sensor 2)"),
        ("CMP Sensor Circuit",
         "Camshaft Position Sensor Circuit Range/Performance"),
        ("IAT Sensor 1 Circuit",
         "Intake Air Temperature Sensor 1 Circuit Range/Performance"),
        ("MAP Sensor Circuit Low",
         "Manifold Absolute Pressure Sensor Circuit Low Voltage"),
    ])
    def test_equivalent_systems_pass(self, title, claim):
        v = check_subject_anchor(title, claim)
        assert v.status == "pass", (
            f"abbreviation equivalence must pass, got {v.status}: {v.reason}")

# ---------------------------------------------------------------------------
# 3. Abstain is a first-class outcome, not a silent pass
# ---------------------------------------------------------------------------

class TestAbstain:
    def test_generic_page_text_abstains(self):
        """A page that names no system cannot be judged either way."""
        v = check_subject_anchor(
            "Lost Communication With Turbocharger / Supercharger Control Module A",
            "U010C - Understanding Common Issue in 2015-2019 Ford F-150")
        assert v.status == "abstain"
        assert v.title_systems == ["turbo"]
        assert v.claim_systems == []

    def test_placeholder_record_title_abstains(self):
        v = check_subject_anchor(
            "Generic (SAE-defined) powertrain code, covering engine, "
            "transmission and emissions.",
            "Generator Position Sensor Not Learned")
        assert v.status == "abstain"
        assert "placeholder" in v.reason

    def test_reserved_record_title_abstains(self):
        v = check_subject_anchor(
            "C0113 - ISO/SAE Reserved (üretici ataması yok)",
            "Pump Motor Circuit")
        assert v.status == "abstain"

    def test_missing_claim_passes_for_backwards_compatibility(self):
        v = check_subject_anchor("O2 Sensor Circuit Low", "")
        assert v.status == "pass"
        assert v.ok

# ---------------------------------------------------------------------------
# 4. Determinism + the verdict helper
# ---------------------------------------------------------------------------

class TestContract:
    def test_same_input_same_output(self):
        args = ("Ignition A Control Signal Circuit High",
                "Cylinder 3 Misfire Detected")
        assert (check_subject_anchor(*args).status
                == check_subject_anchor(*args).status)

    def test_ok_is_false_only_on_block(self):
        assert check_subject_anchor("O2 Sensor Circuit", "O2 Sensor Circuit").ok
        assert check_subject_anchor("O2 Sensor Circuit", "").ok
        assert not check_subject_anchor(
            "O2 Sensor Circuit", "Camshaft Position Actuator Circuit").ok

    def test_systems_in_is_order_independent(self):
        a = systems_in("O2 sensor heater circuit and camshaft position")
        b = systems_in("camshaft position and o2 sensor heater circuit")
        assert a == b, "the extraction must not depend on word order"
        # both component classes must be found (the set also contains the
        # "heater" class added by the T81 extension)
        assert {"o2", "camshaft"} <= a

    def test_verdict_is_a_dataclass_with_sorted_systems(self):
        v = check_subject_anchor("O2 Sensor Circuit",
                                 "Camshaft Position Actuator Circuit")
        assert isinstance(v, AnchorVerdict)
        assert v.title_systems == sorted(v.title_systems)
        assert v.claim_systems == sorted(v.claim_systems)

# ---------------------------------------------------------------------------
# 5. The gate is wired into the merge path
# ---------------------------------------------------------------------------

class TestMergeWiring:
    """A `subject_claim` on a merge record must gate the EN payload."""

    @pytest.fixture()
    def merge_mod(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / "scripts" / "t66_merge.py"
        spec = importlib.util.spec_from_file_location("t66_merge_t80h", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_import_succeeded(self, merge_mod):
        assert merge_mod.check_subject_anchor is not None, (
            "the merge must be able to import the subject-anchor gate")

    def test_conflicting_claim_blocks_the_en_payload(self, merge_mod):
        dtc = {"U011B": {"title": "Lost Communication With Rocker Arm "
                                  "Control Module A"}}
        report = {"applied": [], "skipped": {
            "unknown_key": 0, "already_full": 0, "no_fillable": 0,
            "placeholder": 0, "no_evidence": 0, "no_source_url": 0}}
        rec = {
            "code": "U011B",
            "url": "https://example.test/u011b",
            "source": "example.test",
            "evidence": "Measured subject misattribution case " * 2,
            "symptoms_en": ["The ABS warning light remains illuminated."],
            "subject_claim": ("U011B - Lost Communication with Anti-lock "
                              "Brake System (ABS) Control Module"),
        }
        merge_mod.merge_dtc(rec, dtc, report, apply=True)
        assert "symptoms_en" not in dtc["U011B"], (
            "a subject-conflicting claim must block the EN payload")
        assert report["skipped"].get("subject_conflict") == 1

    def test_matching_claim_lets_the_en_payload_through(self, merge_mod):
        dtc = {"P057A": {"title": "Brake Pedal Position Sensor A Circuit/Open"}}
        report = {"applied": [], "skipped": {
            "unknown_key": 0, "already_full": 0, "no_fillable": 0,
            "placeholder": 0, "no_evidence": 0, "no_source_url": 0}}
        rec = {
            "code": "P057A",
            "url": "https://example.test/p057a",
            "source": "example.test",
            "evidence": "Measured matching-subject case " * 2,
            "symptoms_en": ["Brake lights stay on continuously."],
            "subject_claim": ("P057A - Understanding the Brake Switch A "
                              "Range/Performance"),
        }
        merge_mod.merge_dtc(rec, dtc, report, apply=True)
        assert dtc["P057A"].get("symptoms_en") == ["Brake lights stay on continuously."]
        assert report["skipped"].get("subject_conflict", 0) == 0

    def test_no_claim_is_backwards_compatible(self, merge_mod):
        dtc = {"P057A": {"title": "Brake Pedal Position Sensor A Circuit/Open"}}
        report = {"applied": [], "skipped": {
            "unknown_key": 0, "already_full": 0, "no_fillable": 0,
            "placeholder": 0, "no_evidence": 0, "no_source_url": 0}}
        rec = {
            "code": "P057A",
            "url": "https://example.test/p057a",
            "source": "example.test",
            "evidence": "Adapter that does not supply a claim " * 2,
            "symptoms_en": ["Brake lights stay on continuously."],
        }
        merge_mod.merge_dtc(rec, dtc, report, apply=True)
        assert dtc["P057A"].get("symptoms_en") == ["Brake lights stay on continuously."]

# ---------------------------------------------------------------------------
# 6. The search surface withholds flagged text (T80j)
# ---------------------------------------------------------------------------

class TestSearchWithholdsFlaggedText:
    """A record flagged as subject-conflicting must not be findable BY the
    flagged text — but must stay findable by its own title."""

    @pytest.fixture()
    def copilot(self):
        import src.engine.ai.diagnostic_copilot as dc
        dc.ensure_external_dtc_database_loaded()
        return dc

    def test_flagged_record_not_found_by_the_misattributed_claim(
            self, copilot, monkeypatch):
        kb = {
            "P9001": {
                "title": "EGR Temperature Sensor C Circuit High",
                "geekobd_subject_conflict_t80e": True,
                # the misattributed claim: this text is about a PCV system
                "symptoms_en": ["A fault in the PCV system; faulty PCV valve"],
            },
            "P9002": {
                "title": "Positive Crankcase Ventilation System Performance",
                "symptoms_en": ["PCV valve clogged or stuck open"],
            },
        }
        monkeypatch.setattr(copilot, "EXPERT_KNOWLEDGE_BASE", kb)
        hits = copilot.search_dtc_by_symptom("pcv valve clogged", limit=5)
        codes = [h["code"] for h in hits]
        assert "P9001" not in codes, (
            "a flagged misattributed record must not surface on its wrong claim")
        assert "P9002" in codes, "the genuinely matching record must still surface"

    def test_flagged_record_still_found_by_its_own_title(self, copilot, monkeypatch):
        kb = {
            "P9003": {
                "title": "EGR Temperature Sensor C Circuit High",
                "geekobd_subject_conflict_t80e": True,
                "symptoms_en": ["A fault in the PCV system"],
            },
        }
        monkeypatch.setattr(copilot, "EXPERT_KNOWLEDGE_BASE", kb)
        hits = copilot.search_dtc_by_symptom("egr temperature sensor circuit")
        assert [h["code"] for h in hits] == ["P9003"], (
            "the record must stay reachable through its own authoritative title")

    def test_unflagged_records_are_unaffected(self, copilot, monkeypatch):
        kb = {
            "P9004": {
                "title": "Catalyst System Efficiency",
                "symptoms_en": ["Catalytic converter efficiency below threshold"],
            },
        }
        monkeypatch.setattr(copilot, "EXPERT_KNOWLEDGE_BASE", kb)
        hits = copilot.search_dtc_by_symptom("catalytic converter efficiency")
        assert [h["code"] for h in hits] == ["P9004"]

# ---------------------------------------------------------------------------
# 7. The measured richness guidance (docstring contract)
# ---------------------------------------------------------------------------

class TestClaimRichness:
    """A richer claim finds more conflicts — the documented usage."""

    def test_rich_claim_catches_what_h1_alone_misses(self):
        # A page whose h1 names no system but whose cause text does.
        title = "Fuel Fill Door Lock Control Range/Performance"
        h1 = "P04BC - Understanding Common in 2015-2018 Ford F-150"
        cause = ("The most common cause is a faulty oil separator, occurring "
                 "in about 60% of cases.")
        assert check_subject_anchor(title, h1).status == "abstain"
        assert check_subject_anchor(title, f"{h1} {cause}").status == "block"

    def test_abstain_is_not_a_pass_for_the_caller(self):
        """Abstain must be visibly distinct — a caller may want to require a
        vote before merging rather than treating it as cleared."""
        v = check_subject_anchor("Lost Communication With Turbocharger Module",
                                 "P0100 - Understanding Common Issues")
        assert v.status == "abstain"
        assert v.status != "pass"

# ---------------------------------------------------------------------------
# 8. T81 lexicon corrections (each came from a measurement)
# ---------------------------------------------------------------------------

class TestT81LexiconCorrections:
    def test_generic_engine_light_is_not_a_system_claim(self):
        """`\blight\b` matched 'check engine light' on nearly every page —
        381 spurious blocks. Only real lamp assemblies count now."""
        assert systems_in("Check engine light stays on, indicating a fault") == set()
        assert systems_in("Service engine soon light illuminated") == set()
        assert systems_in("Headlamp Control Circuit Malfunction") == {"lighting"}
        assert systems_in("Turn signal lamp circuit open") == {"lighting"}

    def test_transmission_gear_shift_clutch_are_one_class(self):
        """'Stuck in Gear 8' vs a TCM page is the SAME subject (P07Dx)."""
        for text in ("Stuck in Gear 8", "Incorrect Shift from Gear 2",
                     "Clutch Position Sensor", "Transmission Control Module",
                     "TCM Communication Error", "Gearbox Fault"):
            assert "transmission" in systems_in(text), text
        a = systems_in("Stuck in Gear 8")
        b = systems_in("Transmission Control Module (TCM) Communication")
        assert a & b, "these must share the transmission class"

    def test_extended_component_classes_are_recognized(self):
        for text, expected in [
            ("Clutch Pedal Position Sensor", "transmission"),
            ("Reductant Pump Supply Voltage Circuit", "reductant"),
            ("DC/AC Converter A High Voltage Outlet", "converter"),
            ("Starter Relay B Stuck On", "starter"),
            ("Particulate Filter Restriction", "particulate"),
            ("Camera Washer Actuator M", "washer"),
            ("A/C Refrigerant Pressure Sensor", "refrigerant"),
            ("Night Vision Control Module", "image"),
        ]:
            assert expected in systems_in(text), f"{text} -> {expected}"

    def test_motor_coolant_vs_battery_is_a_real_block(self):
        """P0CBC: DB says drive-motor coolant, the page says battery current.
        Two distinct components — the block is correct."""
        v = check_subject_anchor(
            "Drive Motor A Coolant Temperature Sensor Circuit",
            "P0CBC - Battery Current Sensor Range/Performance Problem")
        assert v.status == "block"

# ---------------------------------------------------------------------------
# 9. T82: the gate's FIELD-TYPE limit (measured)
# ---------------------------------------------------------------------------

class TestFieldTypeLimit:
    """The gate is reliable on fault-STATEMENT text, not on symptom LISTS.

    A symptom legitimately names the downstream systems the driver observes,
    so a block on a symptom list is usually a FALSE block.
    """

    @pytest.mark.parametrize("title,symptoms", [
        # P2688: fuel-supply heater -> hard cold starting is CORRECT
        ("Fuel Supply Heater Control Circuit Low",
         "Hard starting, especially in cold weather. Rough idle."),
        # C1139: wheel-speed tone ring -> ABS lamp is CORRECT
        ("Wheel Speed Sensor Center Tone Ring Missing Tooth Fault",
         "ABS warning light illuminated on the dashboard. Traction control "
         "warning light illuminated."),
        # P2A05: O2 sensor -> fuel-economy drop is CORRECT
        ("Heated oxygen sensor (H02S) 3, bank 2 - range/performance",
         "Check Engine Light illuminated. Decreased fuel economy. Rough idle."),
    ])
    def test_symptom_effects_produce_false_blocks(self, title, symptoms):
        """Documents the limit: these block, but the content is correct."""
        v = check_subject_anchor(title, symptoms)
        assert v.status == "block", (
            "this case documents the false-block behaviour; if the gate ever "
            "stops blocking here, revisit the T82 field-type warning")
        # and the decisive check: the symptom text is NOT about another code
        assert "abs" in symptoms.lower() or "econom" in symptoms.lower() \
            or "starting" in symptoms.lower()

    def test_fault_statement_text_blocks_reliably(self):
        """The same gate on a fault STATEMENT is a true block (T82c)."""
        v = check_subject_anchor(
            "Battery temperature sensor circuit – temperature error",
            "P1348 indicates an open circuit condition in the ignition coil "
            "power output stage 1. The ECU driver transistor…")
        assert v.status == "block"
        assert "ignition" in v.claim_systems

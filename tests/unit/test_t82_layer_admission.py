# -*- coding: utf-8 -*-
"""T82 regression lock — the two-test layer admission discipline.

WHY (measured 2026-09-26)
-------------------------
Two independent defect classes were found in harvested content. Each needs
its OWN test; passing one does not imply passing the other:

  * geekobd.com `symptoms_en` — well-written and unique per code (masked
    distinctness 1.0000) but bound to the WRONG code for 72.4% of records.
    The template test passed; the attribution test failed.
  * obdhut.com `diagnose` — bound to the right code, but 969 of 1,085 entries
    were byte-identical apart from the code name (masked distinctness 0.108).
    The template test failed.

LOCKED CONTRACT
---------------
  * `masked_distinctness` masks DTC/SPN tokens before comparing, so a source
    that substitutes the code into one sentence cannot pass by accident;
  * `check_layer_admission` requires BOTH tests and reports which one failed;
  * a layer with any `block` is inadmissible regardless of distinctness.
"""

from __future__ import annotations

import pytest

from src.engine.ai.subject_anchor import (
    check_layer_admission,
    masked_distinctness,
    masked_text,
)

# ---------------------------------------------------------------------------
# 1. The masked-distinctness measure
# ---------------------------------------------------------------------------

class TestMaskedDistinctness:
    def test_one_template_with_the_code_substituted_scores_low(self):
        """The measured obdhut `diagnose` shape."""
        tpl = ("Start by connecting an OBD2 scanner to read the code and any "
               "freeze frame data. Then follow the diagnostic steps specific "
               "to {code} to identify the root cause.")
        texts = [tpl.format(code=c) for c in ("P04AB", "P052F", "P059A",
                                              "U0100", "B0001", "C0223")]
        assert masked_distinctness(texts) == pytest.approx(1 / 6)

    def test_genuinely_distinct_texts_score_high(self):
        texts = [
            "The ABS module detected an inconsistent wheel speed signal.",
            "The ECM detected a lean condition on bank 1.",
            "Coolant temperature remained below the thermostat threshold.",
        ]
        assert masked_distinctness(texts) == 1.0

    def test_code_tokens_are_masked(self):
        assert masked_text("Fault P0301 on bank 1") == "Fault <C> on bank 1"
        assert masked_text("SPN 100 FMI 4") == "<C> FMI 4"
        assert masked_text("code U0100 lost comms") == "code <C> lost comms"

    def test_empty_input_scores_zero(self):
        assert masked_distinctness([]) == 0.0
        assert masked_distinctness(["", "  "]) == 0.0

# ---------------------------------------------------------------------------
# 2. The combined admission gate
# ---------------------------------------------------------------------------

class TestLayerAdmission:
    def test_template_layer_is_rejected(self):
        """obdhut `diagnose`: right codes, one sentence."""
        tpl = ("Start by connecting an OBD2 scanner to read the code and any "
               "freeze frame data. Then follow the diagnostic steps specific "
               "to {code} to identify the root cause.")
        records = [{"text": tpl.format(code=c), "title": "Any Title"}
                   for c in ("P04AB", "P052F", "P059A", "U0100", "B0001")]
        v = check_layer_admission(records)
        assert v.admissible is False
        assert v.template_ok is False
        assert "TEMPLATE FAIL" in v.reason

    def test_misattributed_layer_is_rejected(self):
        """geekobd shape: unique text, but bound to a different system."""
        records = [
            {"text": "The airbag warning light remains illuminated on the "
                     "dashboard, indicating a fault in the SRS system.",
             "title": "Left Rear Door Lock Actuator Circuit"},
            {"text": "The ABS warning light stays on, indicating a problem "
                     "with the braking system.",
             "title": "EGR Temperature Sensor C Circuit"},
        ]
        v = check_layer_admission(records)
        assert v.admissible is False
        assert v.template_ok is True, "the texts are unique"
        assert v.attribution_ok is False
        assert "ATTRIBUTION FAIL" in v.reason
        assert v.anchor_block == 2

    def test_a_good_layer_passes_both(self):
        records = [
            {"text": "The ABS module detected an inconsistent signal from the "
                     "left front wheel speed sensor.",
             "title": "Left Front Wheel Speed Sensor Circuit Open"},
            {"text": "The ECM measured coolant temperature below the "
                     "thermostat regulating threshold.",
             "title": "Coolant Thermostat Below Regulating Temperature"},
        ]
        v = check_layer_admission(records)
        assert v.admissible is True
        assert v.template_ok and v.attribution_ok
        assert v.anchor_block == 0

    def test_deterministic(self):
        records = [{"text": "The ABS module detected a wheel speed fault.",
                    "title": "Wheel Speed Sensor Circuit"}]
        a = check_layer_admission(records)
        b = check_layer_admission(records)
        assert (a.admissible, a.masked_distinctness) == (
            b.admissible, b.masked_distinctness)

    def test_threshold_is_configurable_but_default_is_measured(self):
        tpl = "Fault detected on {code} circuit"
        records = [{"text": tpl.format(code=c), "title": "Any"}
                   for c in ("P0001", "P0002", "P0003")]
        strict = check_layer_admission(records)
        loose = check_layer_admission(records, min_masked_distinctness=0.3)
        assert strict.admissible is False
        assert loose.admissible is True

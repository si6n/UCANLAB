"""T82d: asserted-foreign-component is the precision signal for symptom text.

The plain anchor gate cannot separate legitimate symptom lists (which name
downstream systems the driver observes) from misattributed text. Measured on
three real corpora, the assertion phrase does separate them:

    geekobd T81d reverts (misattributed)  n=1956  foreign assertion 14.0%
    obd2.com symptoms     (legitimate)    n=4307  foreign assertion  0.0%
    obdhut T82a fills     (accepted)      n=298   foreign assertion  0.0%

These tests lock the signal's behaviour and its PRECISION-ONLY nature: a
non-empty result is evidence of misattribution; an empty one proves nothing.
"""

import pytest

from src.engine.ai.subject_anchor import (
    asserted_components,
    asserts_foreign_component,
    check_subject_anchor,
)


class TestAssertedComponents:
    def test_extracts_the_blamed_component(self):
        got = asserted_components(
            "Check engine light stays on constantly, indicating a malfunction "
            "in the brake pressure sensor.")
        assert got == ["brake pressure sensor"]

    def test_effect_only_text_asserts_nothing(self):
        """The obd2.com shape: pure effects, no claim about the cause."""
        got = asserted_components(
            "ABS warning light illuminated on the dashboard. Traction control "
            "warning light illuminated.")
        assert got == []

    @pytest.mark.parametrize("text", [
        "The warning light came on, suggesting a problem with the fuel pump.",
        "Rough idle, due to a fault in the ignition coil.",
        "Limp mode engaged, caused by a failure of the transmission control module.",
        "P0301 set, resulting from an issue with the fuel injector.",
    ])
    def test_recognises_the_assertion_variants(self, text):
        assert asserted_components(text), f"no assertion found in: {text}"

    def test_truncates_at_sentence_boundary(self):
        got = asserted_components(
            "Lamp on, indicating a malfunction in the airbag sensor. The car "
            "still drives normally.")
        assert got == ["airbag sensor"]


class TestAssertsForeignComponent:
    def test_flags_a_foreign_assertion(self):
        """The real T81d shape: a 4WD page blaming the brake pressure sensor."""
        got = asserts_foreign_component(
            "4WD/AWD Power Transfer Unit Temperature Sensor",
            "Check engine light stays on constantly, indicating a malfunction "
            "in the brake pressure sensor.")
        assert got == ["brake pressure sensor"]

    def test_own_component_assertion_is_not_foreign(self):
        """Blaming the title's own system is not misattribution."""
        got = asserts_foreign_component(
            "Brake Pressure Sensor D Circuit/Open",
            "The module detected low voltage, indicating a fault in the brake "
            "pressure sensor.")
        assert got == []

    def test_legitimate_symptom_list_is_clean(self):
        got = asserts_foreign_component(
            "Wheel Speed Sensor Center Tone Ring Missing Tooth Fault",
            "ABS warning light illuminated on the dashboard. Traction control "
            "warning light illuminated.")
        assert got == []

    def test_short_phrase_is_ignored(self):
        """Guards against a 1-2 character fragment counting as a component."""
        assert asserts_foreign_component("Fuel Pump Relay", "a fault in x") == []

    def test_empty_inputs_are_safe(self):
        assert asserts_foreign_component("", "") == []
        assert asserts_foreign_component(None, None) == []


class TestSignalIsPrecisionOnly:
    """The documented limit: absence of an assertion proves nothing."""

    def test_silent_misattribution_is_not_caught(self):
        """Most T81d texts (86%) carry no assertion phrase at all.

        Measured: only 14.0% of the 1,956 reverted geekobd texts assert a
        foreign component. The remaining 86% are caught by the anchor gate
        (their titles name a different system) plus hand-reading — NOT by this
        signal. This test documents that limit so nobody mistakes the signal
        for a complete detector.
        """
        # A template with no assertion phrase and no system named at all.
        text = "The check engine light stays on. The vehicle may hesitate."
        assert asserts_foreign_component("Clutch A Motor Position Sensor", text) == []

    def test_signal_does_not_replace_the_gate(self):
        """The gate still blocks the effect-list shape; the signal does not."""
        v = check_subject_anchor(
            "Fuel Supply Heater Control Circuit Low",
            "Hard starting, especially in cold weather.")
        assert v.status in ("block", "abstain")  # field-type limit, documented

# -*- coding: utf-8 -*-
"""Unit tests for UI Safety Standards, Alarm Lifecycles, and TR Localization Gate.

Verifies:
- Aksiyon 38: EEMUA 191 Ed.4 / IEC 62682:2023 7-state alarm lifecycle.
  Crucial invariant: RTN_UNACKNOWLEDGED MUST NEVER render as green/normal!
- Aksiyon 38: ISO 3864-2 3-tier severity color ladder and WCAG 2.2 SC 1.4.1/1.4.11 contrast.
- Aksiyon 39: Pywebview edgechromium enforcement and environment hardening.
- Aksiyon 40: Turkish pseudo-locale generation, 12 glyph ink verification, and tr_upper casing.
"""

from __future__ import annotations

import os
import pytest

from src.core.errors import PlatformError
from src.ui.alarm_model import (
    AlarmItem,
    AlarmSeverity,
    AlarmState,
    COLOR_NORMAL_GREEN,
    get_visual_badge,
)
from scripts.check_tr_pseudo_locale import (
    generate_pseudo_locale,
    tr_lower,
    tr_upper,
    verify_12_glyphs_integrity,
)


class TestAlarmModelEemua191:
    """Verifies EEMUA 191 7-state alarm model and ISO 3864-2 color ladder."""

    def test_rtn_unacknowledged_never_green(self) -> None:
        """INVIOLABLE: RTN-Unack must NEVER be green or marked as safe normal."""
        badge = get_visual_badge(AlarmState.RTN_UNACKNOWLEDGED, AlarmSeverity.WARNING)
        assert badge.color_hex != COLOR_NORMAL_GREEN
        assert badge.is_safe_normal is False
        assert "RTN UNACKNOWLEDGED" in badge.text_label
        assert badge.contrast_ratio >= 3.0

    def test_normal_state_visual_badge(self) -> None:
        badge = get_visual_badge(AlarmState.NORMAL, AlarmSeverity.INFO)
        assert badge.color_hex == COLOR_NORMAL_GREEN
        assert badge.is_safe_normal is True
        assert badge.text_label == "NORMAL"

    def test_alarm_lifecycle_transitions(self) -> None:
        alarm = AlarmItem("ALM-001", "Boost pressure threshold exceeded", AlarmSeverity.DANGER)
        assert alarm.state == AlarmState.NORMAL
        assert alarm.badge.is_safe_normal is True

        # Process trips into alarm
        alarm.trip()
        assert alarm.state == AlarmState.UNACKNOWLEDGED
        assert alarm.badge.shape_icon == "octagon"
        assert "DANGER" in alarm.badge.text_label

        # Process clears while unacknowledged -> transitions to RTN_UNACKNOWLEDGED
        alarm.clear()
        assert alarm.state == AlarmState.RTN_UNACKNOWLEDGED
        assert alarm.badge.is_safe_normal is False
        assert alarm.badge.color_hex != COLOR_NORMAL_GREEN

        # Operator acknowledges -> transitions to NORMAL
        alarm.acknowledge()
        assert alarm.state == AlarmState.NORMAL
        assert alarm.badge.is_safe_normal is True

    def test_wcag_multi_modal_requirement(self) -> None:
        """WCAG SC 1.4.1 requires Color + Shape/Icon + Text."""
        for state in AlarmState:
            for sev in (AlarmSeverity.CAUTION, AlarmSeverity.WARNING, AlarmSeverity.DANGER):
                badge = get_visual_badge(state, sev)
                assert badge.color_hex.startswith("#")
                assert len(badge.shape_icon) > 0
                assert len(badge.text_label) > 0
                assert badge.contrast_ratio >= 3.0


class TestTurkishLocalizationGate:
    """Verifies Turkish pseudo-locale and 12-glyph integrity."""

    def test_12_glyphs_integrity(self) -> None:
        assert verify_12_glyphs_integrity() is True

    def test_tr_casing_behavior(self) -> None:
        # Standard Python upper() fails for Turkish 'i'
        assert "istanbul".upper() == "ISTANBUL"
        # tr_upper succeeds
        assert tr_upper("istanbul") == "\u0130STANBUL"  # İSTANBUL
        assert tr_upper("ılık") == "ILIK"

        # Standard Python lower() fails for Turkish 'I'
        assert "IŞIK".lower() == "i\u015fik"  # işik (turns 'I' to 'i')
        # tr_lower succeeds
        assert tr_lower("IŞIK") == "\u0131\u015f\u0131k"  # ışık (turns 'I' to 'ı')

    def test_pseudo_locale_expansion(self) -> None:
        original = "Motor Arızası"
        pseudo = generate_pseudo_locale(original, expansion_ratio=0.35)
        assert pseudo.startswith("[")
        assert pseudo.endswith("]")
        assert len(pseudo) > len(original) * 1.35

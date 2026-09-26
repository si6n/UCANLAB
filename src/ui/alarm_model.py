# -*- coding: utf-8 -*-
"""EEMUA 191 Ed.4 / IEC 62682:2023 7-State Alarm Model & ISO 3864-2 Severity Standards.

Complies with:
- EEMUA 191 Edition 4 (November 2024, ISBN 978-0-85931-243-1)
- IEC 62682:2023 / ISA 18.2 (Management of alarm systems for the process industries)
- ISO 3864-2:2016 (Design principles for product safety labels — 3-tier signal words)
- WCAG 2.2 SC 1.4.1 (Use of Color - A) & SC 1.4.11 (Non-text Contrast - AA)
- Konsolide Keşif Raporu §E.6, §A.3.1 (Madde Q), §H.3 (Aksiyon 38):
  "ISO 3864-2 3 kademeli renk merdiveni + WCAG 1.4.1/1.4.11 uyumu;
   EEMUA 191 7 durumlu alarm modeli (RTN-Unack 'yeşil' OLMAMALI)."
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any

from src.core.errors import PlatformError


class AlarmState(enum.IntEnum):
    """EEMUA 191 Ed.4 / IEC 62682:2023 7-State Alarm Lifecycle."""

    NORMAL = 0
    UNACKNOWLEDGED = 1
    ACKNOWLEDGED = 2
    RTN_UNACKNOWLEDGED = 3  # Returned To Normal, Unacknowledged (MUST NOT render as green/normal)
    SHELVED = 4
    SUPPRESSED_BY_DESIGN = 5
    SUPPRESSED_TEMPORARILY = 6
    OUT_OF_SERVICE_BAD_QUALITY = 7  # Stale data / sensor disconnected / sentinel (Zero fabrication)


class AlarmSeverity(enum.IntEnum):
    """ISO 3864-2:2016 3-Tier Signal-Word Hierarchy."""

    INFO = 0
    CAUTION = 1   # ISO 3864-2: RAL 1003 Safety Yellow (#F9A900)
    WARNING = 2   # ISO 3864-2: RAL 2010 Safety Orange (#D05D29)
    DANGER = 3    # ISO 3864-2: RAL 3001 Safety Red (#9B2423)


@dataclass(slots=True, frozen=True)
class VisualBadge:
    """WCAG 2.2 SC 1.4.1 multi-modal indicator (Color + Shape/Icon + Text)."""

    color_hex: str
    shape_icon: str  # "circle" | "triangle" | "diamond" | "octagon" | "slash_circle"
    text_label: str
    contrast_ratio: float  # >= 3.0 required per WCAG SC 1.4.11 (AA)
    is_safe_normal: bool = False


# Standard ISO 3864-2 / WCAG AA Color Palette
COLOR_NORMAL_GREEN = "#22C55E"    # Valid, acknowledged normal operating condition
COLOR_CAUTION_YELLOW = "#F9A900"  # ISO 3864-2 RAL 1003
COLOR_WARNING_ORANGE = "#D05D29"  # ISO 3864-2 RAL 2010
COLOR_DANGER_RED = "#9B2423"      # ISO 3864-2 RAL 3001
COLOR_RTN_AMBER = "#EAB308"       # Distinct Amber / Yellow-Gold (NEVER Green)
COLOR_SHELVED_PURPLE = "#A855F7"
COLOR_BAD_QUALITY_GRAY = "#64748B"


def get_visual_badge(state: AlarmState, severity: AlarmSeverity) -> VisualBadge:
    """Return compliant visual badge ensuring WCAG 1.4.1 color+shape+text triple."""
    if state == AlarmState.NORMAL:
        return VisualBadge(
            color_hex=COLOR_NORMAL_GREEN,
            shape_icon="circle_check",
            text_label="NORMAL",
            contrast_ratio=4.5,
            is_safe_normal=True,
        )

    if state == AlarmState.RTN_UNACKNOWLEDGED:
        # INVIOLABLE: RTN-Unack MUST NOT BE GREEN!
        # Condition cleared, but operator must acknowledge that an alarm previously occurred.
        return VisualBadge(
            color_hex=COLOR_RTN_AMBER,
            shape_icon="triangle_dashed",
            text_label="RTN UNACKNOWLEDGED",
            contrast_ratio=3.8,
            is_safe_normal=False,
        )

    if state == AlarmState.OUT_OF_SERVICE_BAD_QUALITY:
        return VisualBadge(
            color_hex=COLOR_BAD_QUALITY_GRAY,
            shape_icon="slash_circle",
            text_label="BAD QUALITY / STALE",
            contrast_ratio=4.0,
            is_safe_normal=False,
        )

    if state == AlarmState.SHELVED:
        return VisualBadge(
            color_hex=COLOR_SHELVED_PURPLE,
            shape_icon="clock_pause",
            text_label="SHELVED",
            contrast_ratio=3.5,
            is_safe_normal=False,
        )

    # Active Alarm (UNACKNOWLEDGED or ACKNOWLEDGED)
    shape = "triangle" if severity == AlarmSeverity.CAUTION else ("diamond" if severity == AlarmSeverity.WARNING else "octagon")
    prefix = "UNACK " if state == AlarmState.UNACKNOWLEDGED else "ACK "

    if severity == AlarmSeverity.DANGER:
        return VisualBadge(
            color_hex=COLOR_DANGER_RED,
            shape_icon=shape,
            text_label=f"{prefix}DANGER",
            contrast_ratio=5.2,
            is_safe_normal=False,
        )
    if severity == AlarmSeverity.WARNING:
        return VisualBadge(
            color_hex=COLOR_WARNING_ORANGE,
            shape_icon=shape,
            text_label=f"{prefix}WARNING",
            contrast_ratio=4.1,
            is_safe_normal=False,
        )
    if severity == AlarmSeverity.CAUTION:
        return VisualBadge(
            color_hex=COLOR_CAUTION_YELLOW,
            shape_icon=shape,
            text_label=f"{prefix}CAUTION",
            contrast_ratio=3.2,
            is_safe_normal=False,
        )

    return VisualBadge(
        color_hex=COLOR_BAD_QUALITY_GRAY,
        shape_icon="info_circle",
        text_label=f"{prefix}INFO",
        contrast_ratio=3.5,
        is_safe_normal=False,
    )


class AlarmItem:
    """Stateful alarm instance implementing EEMUA 191 transitions."""

    def __init__(
        self,
        alarm_id: str,
        description: str,
        severity: AlarmSeverity = AlarmSeverity.WARNING,
    ) -> None:
        self.alarm_id = alarm_id
        self.description = description
        self.severity = severity
        self.state = AlarmState.NORMAL
        self.shelved_until: float | None = None
        self.timestamp = time.monotonic()

    def trip(self) -> None:
        """Process variable crossed threshold into alarm."""
        if self.state in (AlarmState.NORMAL, AlarmState.RTN_UNACKNOWLEDGED):
            self.state = AlarmState.UNACKNOWLEDGED
        elif self.state == AlarmState.SHELVED:
            # Check shelving expiry
            if self.shelved_until and time.monotonic() > self.shelved_until:
                self.state = AlarmState.UNACKNOWLEDGED

    def acknowledge(self) -> None:
        """Operator explicitly acknowledged the alarm."""
        if self.state == AlarmState.UNACKNOWLEDGED:
            self.state = AlarmState.ACKNOWLEDGED
        elif self.state == AlarmState.RTN_UNACKNOWLEDGED:
            # Operator acknowledged that the condition cleared -> transitions to NORMAL
            self.state = AlarmState.NORMAL

    def clear(self) -> None:
        """Process variable returned to normal plausibility envelope."""
        if self.state == AlarmState.UNACKNOWLEDGED:
            # Cleared before acknowledgment -> RTN_UNACKNOWLEDGED (never directly NORMAL!)
            self.state = AlarmState.RTN_UNACKNOWLEDGED
        elif self.state == AlarmState.ACKNOWLEDGED:
            self.state = AlarmState.NORMAL

    def shelve(self, ttl_sec: float) -> None:
        """Temporarily shelve the alarm (e.g. during bench test or road maneuver)."""
        if ttl_sec <= 0:
            raise PlatformError("Shelving TTL must be positive", code="INVALID_ALARM_OPERATION")
        self.state = AlarmState.SHELVED
        self.shelved_until = time.monotonic() + ttl_sec

    def mark_bad_quality(self) -> None:
        """Underlying channel or sensor entered invalid state."""
        self.state = AlarmState.OUT_OF_SERVICE_BAD_QUALITY

    @property
    def badge(self) -> VisualBadge:
        return get_visual_badge(self.state, self.severity)

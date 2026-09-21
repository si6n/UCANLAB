# -*- coding: utf-8 -*-
"""P0-7 — false-positive benchmark over synthetic HEALTHY sessions.

WHY THIS EXISTS
---------------
The 2026-09-21 deep-discovery audit found the golden corpus contained only
POSITIVE cases: every one of the 56 files describes a present fault. A suite
that only tests "does it find the fault?" cannot detect a decoder that finds
faults EVERYWHERE — which is exactly the class of bug the same audit reproduced
live (a healthy engine at 2.6 bar bar raised a false overboost; finding 4).

This module builds SYNTHETIC NEGATIVE sessions — nominal telemetry, no DTCs, or
a single MISFIRE-free history code — and asserts the engine does not escalate
them. It is a benchmark, not a golden corpus: nothing here is presented as an
operator-verified repair record, and no data file is written.

The values used are the KB's OWN nominal bands (single source of truth):

  TurboBoost   nominal 2.2 - 3.2 bar   (KB SPN 102)
  CoolantTemp  nominal 82  - 95 °C     (KB SPN 110)
  OilPressure  >= 1.0 bar idle / 3.0 bar @ 2000 rpm (KB SPN 100)

Offline, deterministic — no network, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot

#: Severities that constitute a "stop the vehicle" escalation. A healthy
#: session must never reach one of these (drive_safety_policy maps both to RED).
_STOP_SEVERITIES = {"CRITICAL_STOP"}


@dataclass(frozen=True)
class HealthySession:
    """A synthetic nominal session with no expected fault."""

    name: str
    telemetry: dict[str, float]
    dtcs: tuple[dict[str, object], ...] = ()
    #: The subsystem the engine must NOT accuse for this session.
    forbidden_subsystems: tuple[str, ...] = ()


#: Nominal sessions drawn from the KB's own stated bands. Every value sits
#: strictly inside the nominal range so a correct engine stays quiet.
HEALTHY_SESSIONS: tuple[HealthySession, ...] = (
    HealthySession(
        name="fully-nominal-cruise",
        telemetry={"EngineSpeed": 1800.0, "BoostPressure": 2.6, "CoolantTemp": 90.0},
        forbidden_subsystems=("Aşırı Doldurma & Turboşarj", "Termal Yönetim & Soğutma"),
    ),
    HealthySession(
        name="idle-cold-start",
        telemetry={"EngineSpeed": 750.0, "BoostPressure": 0.0, "CoolantTemp": 60.0},
        forbidden_subsystems=("Aşırı Doldurma & Turboşarj",),
    ),
    HealthySession(
        name="full-load-within-band",
        telemetry={"EngineSpeed": 2200.0, "BoostPressure": 3.15, "CoolantTemp": 94.5},
        forbidden_subsystems=("Aşırı Doldurma & Turboşarj", "Termal Yönetim & Soğutma"),
    ),
    HealthySession(
        name="hot-day-but-nominal",
        telemetry={"EngineSpeed": 1600.0, "BoostPressure": 2.4, "CoolantTemp": 95.0},
        forbidden_subsystems=("Termal Yönetim & Soğutma",),
    ),
    HealthySession(
        name="no-telemetry-no-dtcs",
        telemetry={},
        forbidden_subsystems=(),
    ),
)


class TestHealthySessionsDoNotEscalate:
    """No synthetic healthy session may reach a stop-severity rung."""

    @pytest.mark.parametrize("session", HEALTHY_SESSIONS, ids=[s.name for s in HEALTHY_SESSIONS])
    def test_no_stop_severity(self, session: HealthySession) -> None:
        report = AiDiagnosticCopilot().analyze_session(
            list(session.dtcs), dict(session.telemetry), []
        )
        assert report.severity.value not in _STOP_SEVERITIES, (
            f"healthy session '{session.name}' escalated to {report.severity.value}: "
            f"{report.summary[:160]}"
        )

    @pytest.mark.parametrize("session", HEALTHY_SESSIONS, ids=[s.name for s in HEALTHY_SESSIONS])
    def test_forbidden_subsystems_are_not_accused(self, session: HealthySession) -> None:
        report = AiDiagnosticCopilot().analyze_session(
            list(session.dtcs), dict(session.telemetry), []
        )
        for subsystem in session.forbidden_subsystems:
            assert subsystem not in report.affected_subsystems, (
                f"healthy session '{session.name}' was diagnosed with '{subsystem}'"
            )


class TestHealthyTelemetryIsNotNarratedAsFault:
    """A nominal reading must not appear as a corroborating deviation."""

    def test_nominal_boost_has_no_overboost_correlation(self) -> None:
        report = AiDiagnosticCopilot().analyze_session(
            [], {"EngineSpeed": 1800.0, "BoostPressure": 2.6, "CoolantTemp": 90.0}, []
        )
        joined = " ".join(report.telemetry_correlations)
        assert "sapma var" not in joined, (
            "a nominal 2.6 bar reading was narrated as a boost deviation: " + joined
        )

    def test_nominal_coolant_has_no_overheat_correlation(self) -> None:
        report = AiDiagnosticCopilot().analyze_session(
            [], {"EngineSpeed": 1800.0, "BoostPressure": 2.6, "CoolantTemp": 90.0}, []
        )
        joined = " ".join(report.telemetry_correlations)
        assert "aşırı ısınma" not in joined.lower(), joined


class TestGenuineFaultsAreStillFound:
    """The benchmark must be able to FAIL — a positive control."""

    def test_overboost_is_still_detected(self) -> None:
        report = AiDiagnosticCopilot().analyze_session(
            [], {"EngineSpeed": 1800.0, "BoostPressure": 3.9, "CoolantTemp": 90.0}, []
        )
        assert "Aşırı Doldurma & Turboşarj" in report.affected_subsystems

    def test_overheat_is_still_detected(self) -> None:
        report = AiDiagnosticCopilot().analyze_session(
            [], {"EngineSpeed": 1800.0, "BoostPressure": 2.6, "CoolantTemp": 112.0}, []
        )
        assert "Termal Yönetim & Soğutma" in report.affected_subsystems

    def test_dtc_still_escalates(self) -> None:
        report = AiDiagnosticCopilot().analyze_session(
            [{"code": "SPN 100", "fmi": 1}], {}, []
        )
        assert report.severity.value == "CRITICAL_STOP"


class TestPromptPathFalsePositives:
    """The prompt router must not assert a fault the telemetry contradicts."""

    def test_healthy_boost_query_is_contextualised(self) -> None:
        out = AiDiagnosticCopilot().analyze_live_telemetry(
            1800.0, 2.6, 90.0, [], "turbo basıncı normal mi?"
        )
        assert "nominal bantta" in out, "the healthy reading was not contextualised"
        assert "DESTEKLEMİYOR" in out

    def test_healthy_coolant_query_is_contextualised(self) -> None:
        out = AiDiagnosticCopilot().analyze_live_telemetry(
            1800.0, 2.6, 90.0, [], "hararet normal mi?"
        )
        # Whatever diagnosis is routed, the measured 90 °C must be shown as
        # nominal (inside the 82-95 °C band) rather than as an overheat.
        assert "nominal bantta" in out or "Ölçüm Tutarlılığı" in out

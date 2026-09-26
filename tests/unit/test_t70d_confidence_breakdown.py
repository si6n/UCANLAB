"""T70-D: confidence-score breakdown surfaced to the operator.

MEASURED DEFECT (T70-D): ``compute_root_cause_confidence`` folds three weighted
evidence kinds and a golden-set calibration factor into ONE string. The operator
saw "Orta (%52 ağırlıklı kanıt skoru)" with no way to tell WHICH kind carried
the score — an identification-only match and a telemetry-corroborated one
rendered identically whenever their weighted means coincided.

T70-D adds ``root_cause_confidence_breakdown`` (a component view),
``format_confidence_breakdown`` (the rendering) and wires both into
``DiagnosticAnalysisReport.confidence_breakdown`` and the user card.

The critical invariant: the label is now DERIVED from the breakdown, so the two
can never disagree. ``test_label_matches_legacy_implementation`` pins the label
byte-for-byte against the pre-refactor formula.
"""

from __future__ import annotations

import pytest

from src.core.models.diagnostics import DiagnosticDomain, VehicleSession
from src.engine.ai.diagnostic_copilot import (
    CALIBRATION_CONFIDENCE_SUFFIX,
    ROOT_CAUSE_EVIDENCE_WEIGHTS,
    UNCALIBRATED_CONFIDENCE_SUFFIX,
    AiDiagnosticCopilot,
    compute_root_cause_confidence,
    format_confidence_breakdown,
    root_cause_confidence_breakdown,
)


def _legacy_label(
    dtc_count: int,
    scenario_matched: int,
    kb_matched: int,
    telemetry_correlation_count: int,
    calibration_factor: float | None,
) -> str:
    """The pre-T70-D implementation, reproduced verbatim as an oracle."""
    if dtc_count <= 0:
        return "Normal"
    identified = scenario_matched + kb_matched
    entries = {
        "identification": min(1.0, identified / dtc_count),
        "telemetry_correlation": min(1.0, telemetry_correlation_count / 2.0),
        "dtc_context": min(1.0, dtc_count / 2.0),
    }
    total_weight = sum(ROOT_CAUSE_EVIDENCE_WEIGHTS.values())
    weighted_sum = sum(
        ROOT_CAUSE_EVIDENCE_WEIGHTS[kind] * value for kind, value in entries.items()
    )
    base_score = weighted_sum / total_weight
    from src.engine.ai.diagnostic_copilot import _resolve_calibration_factor

    cal = _resolve_calibration_factor(calibration_factor)
    if cal is None:
        score = max(0.0, min(1.0, base_score))
        label = "Yüksek" if score >= 0.65 else ("Orta" if score >= 0.35 else "Düşük")
        return f"{label} (%{score * 100:.0f} ağırlıklı kanıt skoru){UNCALIBRATED_CONFIDENCE_SUFFIX}"
    score = max(0.0, min(1.0, base_score * cal))
    label = "Yüksek" if score >= 0.65 else ("Orta" if score >= 0.35 else "Düşük")
    return f"{label} (%{score * 100:.0f} ağırlıklı kanıt skoru){CALIBRATION_CONFIDENCE_SUFFIX}"


CASES = [
    (0, 0, 0, 0, None),
    (1, 1, 0, 0, 0.444),
    (2, 1, 1, 3, 0.5),
    (3, 0, 0, 0, 0.9),
    (5, 5, 5, 10, 1.0),
    (2, 0, 2, 1, 0.65),
]


class TestLabelParity:
    """The refactor must not move a single character of the label."""

    @pytest.mark.parametrize(
        "dtc_count,scenario,kb,corr,cal", CASES
    )
    def test_label_matches_legacy_implementation(
        self, dtc_count: int, scenario: int, kb: int, corr: int, cal: float | None
    ) -> None:
        expected = _legacy_label(dtc_count, scenario, kb, corr, cal)
        assert compute_root_cause_confidence(dtc_count, scenario, kb, corr, cal) == expected

    @pytest.mark.parametrize(
        "dtc_count,scenario,kb,corr,cal", CASES
    )
    def test_breakdown_label_equals_public_label(
        self, dtc_count: int, scenario: int, kb: int, corr: int, cal: float | None
    ) -> None:
        """The label the operator sees IS the breakdown's label."""
        breakdown = root_cause_confidence_breakdown(dtc_count, scenario, kb, corr, cal)
        assert breakdown["label"] == compute_root_cause_confidence(
            dtc_count, scenario, kb, corr, cal
        )


class TestBreakdownShape:
    """The components must be the real numbers, weighted as declared."""

    def test_components_cover_every_weighted_kind(self) -> None:
        breakdown = root_cause_confidence_breakdown(2, 1, 1, 3, 0.5)
        assert set(breakdown["components"]) == set(ROOT_CAUSE_EVIDENCE_WEIGHTS)

    def test_contributions_sum_to_base_score(self) -> None:
        breakdown = root_cause_confidence_breakdown(2, 1, 1, 3, 0.5)
        total = sum(c["contribution"] for c in breakdown["components"].values())
        assert total == pytest.approx(breakdown["base_score"], abs=1e-9)

    def test_score_is_base_times_calibration(self) -> None:
        breakdown = root_cause_confidence_breakdown(2, 1, 1, 3, 0.5)
        assert breakdown["score"] == pytest.approx(
            breakdown["base_score"] * breakdown["calibration"], abs=1e-9
        )

    def test_calibrated_flag_is_true_when_factor_known(self) -> None:
        breakdown = root_cause_confidence_breakdown(2, 1, 1, 3, 0.5)
        assert breakdown["calibrated"] is True
        assert breakdown["calibration"] == pytest.approx(0.5)

    def test_no_dtc_yields_no_components(self) -> None:
        breakdown = root_cause_confidence_breakdown(0, 0, 0, 0, None)
        assert breakdown["components"] == {}
        assert breakdown["label"] == "Normal"

    def test_identification_only_match_scores_lower(self) -> None:
        """The defect T70-D fixes: these two used to be indistinguishable."""
        no_telemetry = root_cause_confidence_breakdown(2, 2, 0, 0, 1.0)
        with_telemetry = root_cause_confidence_breakdown(2, 2, 0, 4, 1.0)
        assert no_telemetry["score"] < with_telemetry["score"]
        assert (
            no_telemetry["components"]["telemetry_correlation"]["value"]
            < with_telemetry["components"]["telemetry_correlation"]["value"]
        )


class TestRendering:
    """The rendered block must name every component and the calibration state."""

    def test_renders_all_three_kinds(self) -> None:
        block = format_confidence_breakdown(root_cause_confidence_breakdown(2, 1, 1, 3, 0.5))
        assert "Kod tanıma" in block
        assert "Canlı telemetri korelasyonu" in block
        assert "Aktif kod bağlamı" in block

    def test_renders_calibration_applied(self) -> None:
        block = format_confidence_breakdown(root_cause_confidence_breakdown(2, 1, 1, 3, 0.5))
        assert "0.500" in block
        assert "uygulandı" in block

    def test_uncalibrated_is_flagged_loudly(self, monkeypatch) -> None:
        """An uncalibrated score must never read as verified."""
        import src.engine.ai.diagnostic_copilot as dc

        monkeypatch.setattr(dc, "compute_calibration_factor", lambda: None)
        monkeypatch.setattr(dc, "_resolve_calibration_factor", lambda _f: None)
        breakdown = root_cause_confidence_breakdown(2, 1, 1, 3, None)
        assert breakdown["calibrated"] is False
        block = format_confidence_breakdown(breakdown)
        assert "UYGULANAMADI" in block
        assert UNCALIBRATED_CONFIDENCE_SUFFIX in breakdown["label"]

    def test_no_components_renders_nothing(self) -> None:
        assert format_confidence_breakdown(root_cause_confidence_breakdown(0, 0, 0, 0, None)) == ""
        assert format_confidence_breakdown({}) == ""

    def test_rendering_is_deterministic(self) -> None:
        breakdown = root_cause_confidence_breakdown(2, 1, 1, 3, 0.5)
        assert format_confidence_breakdown(breakdown) == format_confidence_breakdown(breakdown)


class TestReportWiring:
    """The breakdown must reach the report object and the user card."""

    def test_report_carries_breakdown(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"code": "P0300", "spn": None, "status": "ACTIVE"}],
            {"EngineSpeed": 1800.0},
            ["ECM"],
        )
        assert report.confidence_breakdown, "breakdown must be populated"
        assert report.confidence_breakdown["label"] == report.root_cause_probability

    def test_report_without_dtc_has_empty_components(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session([], {}, [])
        assert report.confidence_breakdown.get("components") == {}

    def test_user_card_carries_rendered_lines(self) -> None:
        from src.core.models.diagnostics import DiagnosticEvent
        from src.engine.ai.user_report_composer import compose_user_card

        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"code": "P0300", "spn": None, "status": "ACTIVE"}],
            {"EngineSpeed": 1800.0},
            ["ECM"],
        )
        session = VehicleSession(
            session_id="t70d-card", started_at_ns=1, domain=DiagnosticDomain.PASSENGER
        )
        session.events.append(
            DiagnosticEvent(
                code="P0300",
                status="ACTIVE",
                timestamp_ns=1,
                domain=DiagnosticDomain.PASSENGER,
                severity="MEDIUM",
            )
        )
        card = compose_user_card(report, session)
        lines = card.technical.get("confidence_breakdown_tr")
        assert lines, "the card must carry the rendered breakdown"
        assert any("Kod tanıma" in ln for ln in lines)

    def test_card_without_breakdown_gains_no_noise(self) -> None:
        """A session with no DTC renders no confidence section at all."""
        from src.engine.ai.user_report_composer import compose_user_card

        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session([], {}, [])
        session = VehicleSession(
            session_id="t70d-empty", started_at_ns=1, domain=DiagnosticDomain.PASSENGER
        )
        card = compose_user_card(report, session)
        assert not card.technical.get("confidence_breakdown_tr")

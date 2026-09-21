"""Regression locks for the verified AI-copilot defects (F-02/F-05/F-24/F-25,
Kanıt A, Kanıt C) fixed after the v2.1.0 inference-engine report audit.

Each test pins ONE verified defect. They are written against the empirical
reproductions recorded in the audit, so a future refactor that reintroduces the
old behaviour fails here rather than silently shipping fabrication.

Safety context (AGENTS.md §2.3 "No Fabricated Telemetry"):
- F-02  empty telemetry was narrated as a measured reading ("0 RPM", "85.0 °C")
        and an empty session was reported as "nominal".
- Kanıt A  generic free text ("problem", "hava durumu nedir") produced concrete
        DTC/SPN diagnoses with CRITICAL_STOP severity.
- Kanıt C  the engine counted its OWN hypothesis as telemetry corroboration,
        inflating confidence from ~%60 to ~%90 with zero measured signals.
- F-24  the quarantine gate was absent from the production DB load path, so
        scraped JavaScript/JSON-LD artifacts were merged into the live KB.
- F-25  an empty bus-metric snapshot was reported as "%0 (Nominal)".
- F-04/F-05  single-token symptom matches and scraped values in the database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.ai import diagnostic_copilot as dc
from src.engine.ai.diagnostic_copilot import (
    AiDiagnosticCopilot,
    CausalBayesianInferenceEngine,
    explain_traffic_metrics,
    search_dtc_by_symptom,
)
from src.engine.ai.harvest_validator import (
    CODE_ARTIFACT_PATTERN,
    QuarantineGatekeeper,
    validate_dtc_record,
)


def _query(text: str) -> str:
    return CausalBayesianInferenceEngine.evaluate_diagnostic_query(text, [], {})


class TestF02NoFabricatedTelemetry:
    """F-02: a missing signal is never narrated as a measured value."""

    def test_empty_session_does_not_claim_nominal(self) -> None:
        report = AiDiagnosticCopilot().analyze_session([], {}, {})
        blob = report.summary + " ".join(report.likely_causes)
        assert "nominal aralıkta çalışıyor" not in blob
        assert "veri yok" in blob.lower()

    def test_empty_session_does_not_invent_coolant_85(self) -> None:
        report = AiDiagnosticCopilot().analyze_session([], {}, {})
        blob = report.summary + " ".join(report.likely_causes)
        assert "85.0" not in blob

    def test_misfire_without_rpm_does_not_narrate_rpm(self) -> None:
        report = AiDiagnosticCopilot().analyze_session([{"code": "P0301"}], {}, {})
        assert not any("0 RPM" in c for c in report.telemetry_correlations)

    def test_misfire_with_rpm_still_narrates(self) -> None:
        report = AiDiagnosticCopilot().analyze_session(
            [{"code": "P0301"}], {"EngineSpeed": 2400.0}, {}
        )
        assert any("2400 RPM" in c for c in report.telemetry_correlations)

    def test_overheat_without_coolant_does_not_narrate_temp(self) -> None:
        report = AiDiagnosticCopilot().analyze_session([{"code": "P0115"}], {}, {})
        assert not any("°C" in c for c in report.telemetry_correlations)


class TestKanitCConfidenceNotSelfCorroborated:
    """Kanıt C: the hypothesis line must not count as telemetry evidence."""

    def test_no_telemetry_confidence_is_not_inflated(self) -> None:
        report = AiDiagnosticCopilot().analyze_session([{"code": "P0301"}], {}, {})
        # Old behaviour: %90 ("Yüksek"). Without measured signals it must not
        # reach the "Yüksek" (>=65%) label.
        assert not report.root_cause_probability.startswith("Yüksek")

    def test_measured_telemetry_raises_confidence_above_bare_dtc(self) -> None:
        bare = AiDiagnosticCopilot().analyze_session([{"code": "P0301"}], {}, {})
        measured = AiDiagnosticCopilot().analyze_session(
            [{"code": "P0301"}],
            {"EngineSpeed": 2400.0, "BoostPressure": 1.2, "CoolantTemp": 92.0},
            {},
        )
        assert bare.root_cause_probability != measured.root_cause_probability


class TestKanitANoFabricationFromGenericText:
    """Kanıt A: non-specific free text must abstain, not invent a code."""

    @pytest.mark.parametrize(
        "query",
        ["problem", "hava durumu nedir", "merhaba nasil", "aracimda bir sikinti var"],
    )
    def test_generic_query_abstains(self, query: str) -> None:
        out = _query(query)
        assert "Bilgi Bulunamadı" in out
        assert "[P0" not in out
        assert "[SPN" not in out

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("motorda tekleme var", "P0300"),
            ("P0300 nedir", "P0300"),
            ("turbo basinci dusuk", "P0234"),
            ("hararet yapiyor", "SPN110"),
        ],
    )
    def test_specific_query_still_diagnoses(self, query: str, expected: str) -> None:
        out = _query(query)
        assert f"[{expected}]" in out

    def test_brake_air_still_reachable_with_qualifier(self) -> None:
        out = _query("fren hava basinci dusuk")
        assert "[SPN1087]" in out


class TestF25NoNominalWithoutData:
    """F-25: an empty bus snapshot must not be reported as healthy."""

    def test_empty_bus_metrics_report_no_data(self) -> None:
        out = explain_traffic_metrics({}, "hat yuku durumu nedir")
        assert "Veri Yok" in out
        assert "Nominal" not in out

    def test_empty_bus_metrics_no_action_trigger(self) -> None:
        out = _query("hat yuku durumu nedir")
        assert "act_uds_0x14_clear_dtc" not in out

    def test_healthy_measured_bus_still_reports_nominal(self) -> None:
        out = explain_traffic_metrics(
            {"bus_load_percent": 22, "error_count": 0, "total_packets": 5000}, "hat yuku"
        )
        assert "Nominal" in out

    def test_anomalous_bus_still_alarms(self) -> None:
        out = explain_traffic_metrics(
            {"bus_load_percent": 88, "error_count": 12, "total_packets": 5000}, "hat yuku"
        )
        assert "ANOMALİ" in out


class TestF24QuarantineGate:
    """F-24: scraped code artifacts are quarantined; prose is not."""

    @pytest.mark.parametrize(
        "artifact",
        [
            "p2032 }}",
            "function loadData(){return null;}",
            "JSON.parse(data)",
            "var x=1;",
            "window.dataLayer = window.dataLayer || [];",
            '{"@type":"FAQPage"}',
        ],
    )
    def test_code_artifact_is_flagged(self, artifact: str) -> None:
        assert CODE_ARTIFACT_PATTERN.search(artifact)

    @pytest.mark.parametrize(
        "prose",
        [
            "Sensor arızası, kablo kopukluğu",
            "Often no drivability symptoms are present since the code has no defined function",
            "Termostat mekanik olarak açık konumda takılı kalmış.",
            "e.g. sometimes a function of engine load",
            "Check for loose or corroded connectors",
        ],
    )
    def test_legitimate_prose_is_not_flagged(self, prose: str) -> None:
        assert not CODE_ARTIFACT_PATTERN.search(prose)

    def test_js_record_is_quarantined(self) -> None:
        report = validate_dtc_record(
            {
                "code": "P2032",
                "symptoms": ["p2032 }}"],
                "causes": ["function loadData(){return null;}"],
            }
        )
        assert not report.is_valid

    def test_clean_record_is_approved(self) -> None:
        report = validate_dtc_record(
            {
                "code": "P2032",
                "symptoms": ["Motor arıza lambası yanıyor"],
                "causes": ["EGT sensörü devresi düşük voltaj"],
            }
        )
        assert report.is_valid

    def test_legitimate_spn_records_pass_the_batch_gate(self) -> None:
        result = QuarantineGatekeeper.audit_batch(
            [],
            [
                {
                    "spn": 110,
                    "fmi": 0,
                    "name": "Engine Coolant Temperature",
                    "causes": "Sensor arızası",
                    "actions": "Soğutma sistemini kontrol edin",
                }
            ],
        )
        assert len(result["approved_spns"]) == 1

    def test_database_source_is_free_of_code_artifacts(self) -> None:
        """F-05: the shipped DTC database contains no scraped code artifacts."""
        db_path = Path(__file__).resolve().parents[2] / "data" / "diagnostics" / "dtc_database.json"
        if not db_path.exists():  # pragma: no cover - DB ships with the repo
            pytest.skip("dtc_database.json not present")
        data = json.loads(db_path.read_text(encoding="utf-8"))
        offenders = []
        for code, info in data.items():
            if not isinstance(info, dict):
                continue
            for field in ("symptoms", "causes"):
                value = info.get(field)
                values = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
                for item in values:
                    if CODE_ARTIFACT_PATTERN.search(str(item)):
                        offenders.append(f"{code}.{field}")
        assert offenders == [], f"scraped artifacts in DB: {offenders[:10]}"

    def test_live_knowledge_base_is_free_of_code_artifacts(self) -> None:
        """F-24 wiring: the gate runs on the production load path."""
        dc.ensure_external_dtc_database_loaded()
        offenders = []
        for code, info in dc.EXPERT_KNOWLEDGE_BASE.items():
            if not isinstance(info, dict):
                continue
            for field in ("symptoms", "causes"):
                value = info.get(field)
                values = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
                for item in values:
                    if CODE_ARTIFACT_PATTERN.search(str(item)):
                        offenders.append(f"{code}.{field}")
        assert offenders == [], f"scraped artifacts in live KB: {offenders[:10]}"


class TestF04SymptomSearchMinScore:
    """F-04: a single coincidental token overlap must not name a DTC."""

    def test_single_generic_token_yields_no_hit(self) -> None:
        assert search_dtc_by_symptom("hava durumu nedir", limit=1) == []

    def test_generic_terms_are_excluded_from_search_terms(self) -> None:
        from src.engine.ai.diagnostic_copilot import _symptom_search_terms

        assert _symptom_search_terms("problem") == []
        # "hava" stays a legitimate search token (it is part of "hava basinci"),
        # so the min-score gate — not term filtering — is what blocks the
        # weather query. Verify the gate directly.
        assert _symptom_search_terms("hava basinci dusuk") != []

    def test_min_score_gate_is_two_terms(self) -> None:
        from src.engine.ai.diagnostic_copilot import _SYMPTOM_SEARCH_MIN_SCORE

        assert _SYMPTOM_SEARCH_MIN_SCORE == 2


class TestF01WholeWordMatching:
    """F-01: intent keywords match whole words, not substrings."""

    def test_has_standalone_word_rejects_substring(self) -> None:
        from src.engine.ai.diagnostic_copilot import _has_standalone_word

        assert not _has_standalone_word("hata karesi", "hat")
        assert _has_standalone_word("hata karesi", "hata")

    def test_error_frame_query_is_not_hijacked_by_traffic_path(self) -> None:
        out = _query("hata karesi nedir")
        assert "Hata Karesi" in out

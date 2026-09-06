"""Unit tests for AI Diagnostic Copilot reasoning engine and JSON parser."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from src.engine.ai.diagnostic_copilot import (
    AiDiagnosticCopilot,
    FaultSeverity,
)


def test_ai_copilot_critical_oil_pressure_analysis() -> None:
    copilot = AiDiagnosticCopilot()
    active_dtcs = [
        {"spn": 100, "fmi": 1, "description": "Engine Oil Pressure Low"},
    ]
    telemetry = {
        "EngineSpeed": 1800.0,
        "BoostPressure": 140.0,
        "CoolantTemp": 88.0,
    }
    ecus = ["Engine_ECU_0x00"]

    report = copilot.analyze_session(active_dtcs, telemetry, ecus)
    assert report.severity == FaultSeverity.CRITICAL_STOP
    assert "Motor Yağlama" in report.affected_subsystems[0]
    assert len(report.troubleshooting_steps) >= 2
    assert "Kritik düşük yağ basıncı" in report.likely_causes[0]


def test_ai_copilot_nominal_healthy_state() -> None:
    copilot = AiDiagnosticCopilot()
    report = copilot.analyze_session([], {"EngineSpeed": 1200.0, "BoostPressure": 120.0}, ["Engine_ECU_0x00"])
    assert report.severity == FaultSeverity.LOW
    assert "nominal" in report.summary.lower()
    assert len(report.troubleshooting_steps) == 1


def test_ai_copilot_overheating_scenario() -> None:
    copilot = AiDiagnosticCopilot()
    active_dtcs = [{"spn": 110, "fmi": 0, "description": "Engine Coolant Temperature High"}]
    report = copilot.analyze_session(active_dtcs, {"EngineSpeed": 2000.0, "CoolantTemp": 112.0}, ["ECU_0"])
    assert report.severity == FaultSeverity.CRITICAL_STOP
    assert any("Soğutma" in s for s in report.affected_subsystems)


def test_ai_copilot_injector_fault_scenario() -> None:
    copilot = AiDiagnosticCopilot()
    active_dtcs = [{"spn": 653, "fmi": 5, "description": "Cylinder 3 Injector Open Circuit"}]
    report = copilot.analyze_session(active_dtcs, {"EngineSpeed": 1500.0}, ["ECU_0"])
    assert report.severity == FaultSeverity.MEDIUM
    assert any("Silindir #3" in s for s in report.affected_subsystems)


def test_ai_copilot_turbo_boost_fault_scenario() -> None:
    copilot = AiDiagnosticCopilot()
    active_dtcs = [{"spn": 102, "fmi": 18, "description": "Boost Pressure Low"}]
    report = copilot.analyze_session(active_dtcs, {"EngineSpeed": 2200.0, "BoostPressure": 90.0}, ["ECU_0"])
    assert report.severity == FaultSeverity.MEDIUM
    assert any("Turbo" in s for s in report.affected_subsystems)


def test_ai_copilot_dpf_fault_scenario() -> None:
    copilot = AiDiagnosticCopilot()
    active_dtcs = [{"spn": 3251, "fmi": 0, "description": "DPF Differential Pressure High"}]
    report = copilot.analyze_session(active_dtcs, {"EngineSpeed": 1800.0}, ["ECU_0"])
    assert report.severity == FaultSeverity.MEDIUM
    assert any("DPF" in s for s in report.affected_subsystems)


def test_clean_and_parse_json_raw_object() -> None:
    raw = '{"summary": "Test Diagnosis", "severity": "HIGH", "likely_causes": ["Cause 1"]}'
    parsed = AiDiagnosticCopilot._clean_and_parse_json(raw)
    assert parsed["summary"] == "Test Diagnosis"
    assert parsed["severity"] == "HIGH"
    assert parsed["likely_causes"] == ["Cause 1"]


def test_clean_and_parse_json_markdown_fenced() -> None:
    raw = """```json
    {
        "summary": "Turbo boost pressure leak detected",
        "severity": "MEDIUM",
        "root_cause_probability": "%85",
        "likely_causes": ["Intercooler hose split"],
        "troubleshooting_steps": [
            {"step_number": 1, "action": "Check intercooler clamp", "target_component": "Hose", "difficulty": "Kolay"}
        ],
        "affected_subsystems": ["Turbocharger"],
        "telemetry_correlations": ["Low boost at high RPM"]
    }
    ```"""
    parsed = AiDiagnosticCopilot._clean_and_parse_json(raw)
    assert parsed["summary"] == "Turbo boost pressure leak detected"
    assert parsed["severity"] == "MEDIUM"
    assert len(parsed["troubleshooting_steps"]) == 1


def test_clean_and_parse_json_markdown_fenced_without_language_tag() -> None:
    raw = """```
    {
        "summary": "Fuel rail pressure sensor intermittent",
        "severity": "LOW"
    }
    ```"""
    parsed = AiDiagnosticCopilot._clean_and_parse_json(raw)
    assert parsed["summary"] == "Fuel rail pressure sensor intermittent"
    assert parsed["severity"] == "LOW"


def test_clean_and_parse_json_with_conversational_text() -> None:
    raw = """Sure! Here is the detailed vehicle analysis you requested:

    ```json
    {
        "summary": "All systems nominal",
        "severity": "INFO"
    }
    ```

    Please let me know if you need further help!"""
    parsed = AiDiagnosticCopilot._clean_and_parse_json(raw)
    assert parsed["summary"] == "All systems nominal"
    assert parsed["severity"] == "INFO"


def test_clean_and_parse_json_outermost_brackets_fallback() -> None:
    raw = 'Here is the JSON: {"summary": "Direct brackets without markdown", "severity": "LOW"} End of report.'
    parsed = AiDiagnosticCopilot._clean_and_parse_json(raw)
    assert parsed["summary"] == "Direct brackets without markdown"
    assert parsed["severity"] == "LOW"


def test_clean_and_parse_json_invalid_raises_decode_error() -> None:
    with pytest.raises(json.JSONDecodeError):
        AiDiagnosticCopilot._clean_and_parse_json("This is definitely not JSON and has no braces at all.")


def test_gemini_fallback_on_corrupted_response() -> None:
    copilot = AiDiagnosticCopilot(gemini_api_key="valid-mock-api-key-longer-than-10-chars")

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        {"candidates": [{"content": {"parts": [{"text": "Corrupted response {not valid json"}]}}]}
    ).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        report = copilot.analyze_session(
            [{"spn": 100, "fmi": 1}],
            {"EngineSpeed": 1500.0},
            ["ECU_0"],
        )
        # Should gracefully fall back to local expert engine
        assert report.ai_model_used == "Yerel Otomotiv Uzman Motoru (Çevrimdışı)"
        assert report.severity == FaultSeverity.CRITICAL_STOP


def test_gemini_successful_markdown_analysis() -> None:
    copilot = AiDiagnosticCopilot(gemini_api_key="valid-mock-api-key-longer-than-10-chars")

    llm_payload = """```json
    {
        "summary": "Gemini Cloud Diagnosis: Low Fuel Pressure",
        "severity": "MEDIUM",
        "root_cause_probability": "%94",
        "likely_causes": ["Clogged fuel filter"],
        "troubleshooting_steps": [
            {"step_number": 1, "action": "Replace secondary fuel filter", "target_component": "Fuel Filter", "difficulty": "Orta"}
        ],
        "affected_subsystems": ["Fuel Rail"],
        "telemetry_correlations": ["Fuel pressure dip"]
    }
    ```"""

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"candidates": [{"content": {"parts": [{"text": llm_payload}]}}]}).encode(
        "utf-8"
    )
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        report = copilot.analyze_session([], {}, ["ECU_0"])
        assert "Google Gemini" in report.ai_model_used and "Flash" in report.ai_model_used
        assert report.summary == "Gemini Cloud Diagnosis: Low Fuel Pressure"
        assert report.severity == FaultSeverity.MEDIUM
        assert len(report.troubleshooting_steps) == 1
        assert report.troubleshooting_steps[0].action == "Replace secondary fuel filter"


def test_gemini_fallback_on_http_errors() -> None:
    """Verify that HTTP network errors (503, 500, timeout) fall back gracefully to local offline engine."""
    copilot = AiDiagnosticCopilot(gemini_api_key="valid-mock-api-key-longer-than-10-chars")

    http_error = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        code=503,
        msg="Service Unavailable",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        report = copilot.analyze_session(
            [{"spn": 100, "fmi": 1}],
            {"EngineSpeed": 1500.0},
            ["ECU_0"],
        )
        assert report.ai_model_used == "Yerel Otomotiv Uzman Motoru (Çevrimdışı)"
        assert report.severity == FaultSeverity.CRITICAL_STOP


# ============================================================================
# F-03: API key transport DoD tests — key in x-goog-api-key header,
# never in the URL (CWE-598)
# ============================================================================


def test_gemini_request_carries_key_in_header_not_url() -> None:
    """F-03 DoD: the Gemini call must pass the key via x-goog-api-key header."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):  # noqa: ANN001
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
        ).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    copilot = AiDiagnosticCopilot(gemini_api_key="AIza-mock-key-0123456789abcdef")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        copilot.analyze_live_telemetry(
            rpm=1800.0, boost_bar=1.6, coolant_temp=85.0, dtc_codes=["P0300"], user_prompt="Sorun ne?"
        )

    assert "?key=" not in captured["url"], "API key leaked into URL"
    assert "apikey=" not in captured["url"].lower()
    assert captured["headers"].get("x-goog-api-key") == "AIza-mock-key-0123456789abcdef"


def test_gemini_endpoint_constant_matches_readme_model() -> None:
    """F-42/E-9 DoD: single endpoint constant pinned to gemini-2.0-flash."""
    from src.engine.ai.diagnostic_copilot import GEMINI_ENDPOINT

    assert GEMINI_ENDPOINT.endswith("gemini-2.0-flash:generateContent")
    assert "?key=" not in GEMINI_ENDPOINT


# ============================================================================
# DYNAMIC AUTOMOTIVE DIAGNOSTIC DATABASES INTEGRATION TESTS
# ============================================================================


def test_external_dtc_database_loaded_and_expanded() -> None:
    """Verify external DTC database loaded properly, expanding KB beyond 1800 codes."""
    from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE

    assert len(EXPERT_KNOWLEDGE_BASE) >= 1800
    # Verify presence of representative codes from each domain
    assert "U0001" in EXPERT_KNOWLEDGE_BASE
    assert "P2002" in EXPERT_KNOWLEDGE_BASE
    assert "C0035" in EXPERT_KNOWLEDGE_BASE
    assert "B0001" in EXPERT_KNOWLEDGE_BASE
    assert "P3000" in EXPERT_KNOWLEDGE_BASE


def test_dynamic_dtc_4stage_technician_reports() -> None:
    """Verify CausalBayesianInferenceEngine generates 4-stage technician report for dynamic codes."""
    from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

    # Test U0001 (High Speed CAN Bus)
    report_u0001 = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "U0001 ariza kodu nedir?", [], {"EngineSpeed": 0.0, "BoostPressure": 0.0, "CoolantTemp": 85.0}
    )
    assert "[U0001]" in report_u0001
    assert "4-AŞAMALI USTA TEKNİSYEN SAHA ONARIM KILAVUZU" in report_u0001
    assert "Aşama 1: Görsel & Mekanik Kontrol" in report_u0001
    assert "Aşama 2: Kesin Multimetre & Osiloskop Toleransları" in report_u0001

    # Test C0035 (Chassis / ABS)
    report_c0035 = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "C0035 hatasi verdi arac", [], {}
    )
    assert "[C0035]" in report_c0035
    assert "Alt Sistem:" in report_c0035

    # Test P3000 (Hybrid Battery)
    report_p3000 = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "P3000 nedir?", [], {}
    )
    assert "[P3000]" in report_p3000


def test_j1939_spn_query_evaluation() -> None:
    """Verify J1939 SPN & FMI queries retrieve heavy-duty diagnostic guides."""
    from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine, get_j1939_spn_database

    j1939_db = get_j1939_spn_database()
    assert len(j1939_db.get("spns", {})) >= 50

    # Query SPN 641 FMI 7 (VGT Turbo Actuator Mechanical Jam)
    report = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "SPN 641 FMI 7 arızası nedir?", [], {"EngineSpeed": 1200.0}
    )
    assert "SPN 641" in report
    assert "Turbo" in report or "VGT" in report
    assert "FMI 7" in report
    assert "SAE J1939-73" in report


def test_dynamic_loaders_and_fallbacks() -> None:
    """Verify getters and graceful fallback behavior when files are missing or invalid."""
    from src.engine.ai.diagnostic_copilot import (
        get_j1939_spn_database,
        get_mode06_database,
        get_uds_did_database,
        load_external_dtc_database,
    )

    # Valid loaders
    uds_db = get_uds_did_database()
    assert len(uds_db.get("dids", {})) >= 30

    mode06_db = get_mode06_database()
    assert len(mode06_db.get("monitors", {})) >= 15

    # Missing path fallback (must return 0 and empty dict gracefully without raising)
    assert load_external_dtc_database("non_existent_file.json") == 0
    assert get_j1939_spn_database("non_existent_file.json") == {}
    assert get_uds_did_database("non_existent_file.json") == {}
    assert get_mode06_database("non_existent_file.json") == {}


def test_dynamic_dtc_in_copilot_session_analysis() -> None:
    """Verify AiDiagnosticCopilot incorporates dynamically loaded DTCs into session report."""
    from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot, FaultSeverity

    copilot = AiDiagnosticCopilot()
    # P2002 is DPF efficiency from external DTC catalog
    report = copilot.analyze_session(
        [{"code": "P2002"}],
        {"EngineSpeed": 850.0, "BoostPressure": 0.1, "CoolantTemp": 88.0},
        ["ECU_ECM"],
    )

    assert report.severity in (FaultSeverity.MEDIUM, FaultSeverity.LOW)
    assert len(report.likely_causes) > 0
    assert len(report.troubleshooting_steps) > 0
    assert any("Emisyon" in s or "SCR" in s or "DPF" in s for s in report.affected_subsystems)


def test_manufacturer_specific_p1_codes_integration() -> None:
    """Verify manufacturer-specific P1 series codes (Ford, GM, VAG, Toyota, BMW)."""
    from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE, AiDiagnosticCopilot, FaultSeverity

    # Ford PATS immobilizer
    assert "P1260" in EXPERT_KNOWLEDGE_BASE
    p1260 = EXPERT_KNOWLEDGE_BASE["P1260"]
    assert "PATS" in p1260["title"]
    assert p1260["severity"] == "CRITICAL_STOP"
    assert any("PATS" in c for c in p1260["causes"])

    # GM/VAG/BMW/Toyota CKP-CMP correlation
    assert "P1345" in EXPERT_KNOWLEDGE_BASE
    p1345 = EXPERT_KNOWLEDGE_BASE["P1345"]
    assert "Krank" in p1345["title"] or "Kam" in p1345["title"] or "Correlation" in p1345["title"]

    # Toyota VVT malfunction
    assert "P1349" in EXPERT_KNOWLEDGE_BASE
    p1349 = EXPERT_KNOWLEDGE_BASE["P1349"]
    assert "VVT" in p1349["title"]
    assert any("OCV" in c for c in p1349["causes"])

    # Verify session analysis with P1 code
    copilot = AiDiagnosticCopilot()
    rep = copilot.analyze_session([{"code": "P1260"}], {}, ["PCM_0x7E0"])
    assert rep.severity == FaultSeverity.CRITICAL_STOP
    assert any("PATS" in c for c in rep.likely_causes)


def test_nhtsa_recalls_database_and_search() -> None:
    """Verify NHTSA recalls database loading, multi-criteria search, and report formatting."""
    from src.engine.ai.diagnostic_copilot import (
        format_nhtsa_recall_report,
        get_nhtsa_recalls_database,
        search_nhtsa_recalls,
    )

    db = get_nhtsa_recalls_database()
    assert len(db) >= 200

    # Search by vehicle
    ford_recalls = search_nhtsa_recalls(make="ford", model="f-150", year=2022)
    assert len(ford_recalls) >= 5

    # Search by category
    ev_recalls = search_nhtsa_recalls(category="Batarya")
    assert len(ev_recalls) >= 1

    # Search by keyword
    gateway_recalls = search_nhtsa_recalls(query="trailer brake")
    assert len(gateway_recalls) >= 1

    # Format report
    rep = format_nhtsa_recall_report(ford_recalls[0])
    assert "NHTSA Geri Çağırma" in rep
    assert "Sorun Özeti" in rep
    assert "Resmi Onarım" in rep

    # Missing path fallback
    assert get_nhtsa_recalls_database("missing_file.json") == {}
    assert search_nhtsa_recalls(make="ford", data_path="missing_file.json") == []


def test_copilot_recall_query_intent_resolution() -> None:
    """Verify natural language recall queries are dispatched to NHTSA knowledge base."""
    from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

    # Ford F-150 recall query
    ans_ford = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "Ford F-150 2022 geri çağırma bültenleri var mı?", [], {}
    )
    assert "NHTSA Resmi Güvenlik Geri Çağırma" in ans_ford
    assert "Ford Motor Company" in ans_ford
    assert "Eşleşen Kampanya" in ans_ford

    # Tesla recall query
    ans_tesla = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "Tesla Model 3 recall bültenleri", [], {}
    )
    assert "NHTSA Resmi Güvenlik Geri Çağırma" in ans_tesla
    assert "Tesla, Inc." in ans_tesla

    # Unmatched query gives informative fallback
    ans_unmatched = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "Ferrari Testarossa 1984 recall", [], {}
    )
    assert "NHTSA Geri Çağırma Arama Sonucu" in ans_unmatched


def test_desktop_bridge_diagnostics_integration() -> None:
    """Verify DesktopApiBridge methods for DTC lookup, NHTSA search, and metrics."""
    from src.ui.desktop_app import DesktopApiBridge

    class DummyApp:
        pass

    bridge = DesktopApiBridge(DummyApp())

    # 1. DB Metrics
    metrics = bridge.get_diagnostic_db_metrics()
    assert metrics["dtc_count"] >= 1800
    assert metrics["j1939_spn_count"] >= 50
    assert metrics["uds_did_count"] >= 30
    assert metrics["mode06_monitor_count"] >= 15
    assert metrics["nhtsa_recall_count"] >= 200

    # 2. Direct DTC Lookup
    dtc_p1260 = bridge.get_dtc_info("P1260")
    assert "PATS" in dtc_p1260.get("title", "")
    assert dtc_p1260.get("severity") == "CRITICAL_STOP"

    # 3. NHTSA Search from Bridge
    recalls = bridge.search_nhtsa_recalls(make="Ford", model="F-150", year=2022)
    assert len(recalls) >= 5
    assert any("22V193000" in r.get("campaign_number", "") or "TRAILER BRAKE" in r.get("component", "") for r in recalls)



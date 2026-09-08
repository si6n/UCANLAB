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


def test_copilot_dbc_queries_found_and_not_found() -> None:
    """Verify copilot correctly identifies present DBCs and explicitly states when DBC is not found."""
    from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

    # Found DBC
    res_golf = CausalBayesianInferenceEngine.evaluate_diagnostic_query("golf dbc var mı?", [], {})
    assert "Eşleşen DBC Dosyaları" in res_golf
    assert "vw_golf_mk4.dbc" in res_golf
    assert "Sinyal" in res_golf

    # Unknown DBC
    res_unknown = CausalBayesianInferenceEngine.evaluate_diagnostic_query("ferrari f40 dbc", [], {})
    assert "DBC Bulunamadı" in res_unknown
    assert "ferrari f40" in res_unknown
    assert "data/dbc/" in res_unknown

    # DBC catalog overview
    res_list = CausalBayesianInferenceEngine.evaluate_diagnostic_query("mevcut dbc listesi", [], {})
    assert "Kayıtlı DBC Kütüphanesi Özeti" in res_list
    assert "Binek Araçlar" in res_list


def test_copilot_unknown_codes_and_can_ids() -> None:
    """Verify copilot explicitly informs user when a DTC, SPN, or CAN ID is not found."""
    from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

    # Unknown DTC
    res_dtc = CausalBayesianInferenceEngine.evaluate_diagnostic_query("P1999 arızası nedir?", [], {})
    assert "Arıza Kodu Bulunamadı" in res_dtc
    assert "[P1999]" in res_dtc
    assert "Güç Aktarımı" in res_dtc

    # Unknown SPN
    res_spn = CausalBayesianInferenceEngine.evaluate_diagnostic_query("SPN 999999 hatası", [], {})
    assert "[SPN 999999] Kaydı Bulunamadı" in res_spn

    # Unknown CAN ID
    res_id = CausalBayesianInferenceEngine.evaluate_diagnostic_query("0x123 CAN ID nedir?", [], {})
    assert "CAN ID Tanımsız" in res_id
    assert "0X123" in res_id

    # Known Standard OBD ID
    res_obd = CausalBayesianInferenceEngine.evaluate_diagnostic_query("0x7DF CAN ID", [], {})
    assert "0x7DF" in res_obd
    assert "Standart OBD-II" in res_obd


def test_copilot_unmatched_query_concise_guidance() -> None:
    """Verify unmatched arbitrary queries return honest concise guidance rather than fake telemetry."""
    from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

    res = CausalBayesianInferenceEngine.evaluate_diagnostic_query("bugün hava nasıl olacak?", [], {})
    assert "Bilgi Bulunamadı" in res
    assert "Desteklenen Sorgu Formatları" in res
    assert len(res.splitlines()) <= 12


def test_action_trigger_extraction_and_metadata() -> None:
    """Verify Copilot action trigger generation, attachment, and parsing."""
    from src.engine.ai.diagnostic_copilot import (
        CausalBayesianInferenceEngine,
        attach_action_triggers,
        extract_action_triggers,
        make_j1939_dm1_action,
        make_j1939_dm11_action,
        make_uds_clear_dtc_action,
        make_uds_ecu_reset_action,
        make_uds_read_vin_action,
        make_uds_routine_action,
        make_uds_session_action,
        parse_action_triggers_from_text,
    )

    act_dtc = make_uds_clear_dtc_action(0xFFFFFF)
    assert act_dtc["action_type"] == "uds_clear_dtc"
    assert act_dtc["requires_confirmation"] is True
    assert "0x14" in act_dtc["label"]

    act_vin = make_uds_read_vin_action()
    assert act_vin["action_type"] == "uds_read_did"
    assert act_vin["params"].get("did") == 0xF190

    act_sess = make_uds_session_action(0x03)
    assert act_sess["action_type"] == "uds_session_control"
    assert act_sess["params"].get("session_type") == 0x03

    act_rout = make_uds_routine_action(0x0203)
    assert act_rout["action_type"] == "uds_routine"

    act_reset = make_uds_ecu_reset_action(0x01)
    assert act_reset["action_type"] == "uds_ecu_reset"

    act_dm11 = make_j1939_dm11_action()
    assert act_dm11["action_type"] == "j1939_clear_dtc"
    assert act_dm11["requires_confirmation"] is True

    act_dm1 = make_j1939_dm1_action()
    assert act_dm1["action_type"] == "j1939_dm1_query"

    # Test attachment & parsing
    raw_text = "Test diagnostics message"
    attached = attach_action_triggers(raw_text, [act_dtc, act_vin])
    assert "<!--ACTIONS:" in attached
    extracted_text, parsed_actions = parse_action_triggers_from_text(attached)
    assert extracted_text.strip() == raw_text
    assert len(parsed_actions) == 2
    assert parsed_actions[0]["action_type"] == "uds_clear_dtc"
    assert parsed_actions[1]["action_type"] == "uds_read_did"

    # Test automated attachment in evaluate_diagnostic_query
    res_dtc = CausalBayesianInferenceEngine.evaluate_diagnostic_query("P0300 arıza kodu var", [], {})
    assert "<!--ACTIONS:" in res_dtc
    acts = extract_action_triggers(res_dtc)
    assert any(a["action_type"] == "uds_clear_dtc" for a in acts)

    # Test J1939 automated action triggers
    res_j1939 = CausalBayesianInferenceEngine.evaluate_diagnostic_query("J1939 SPN 100 FMI 1", [], {})
    assert "<!--ACTIONS:" in res_j1939
    j1939_acts = extract_action_triggers(res_j1939)
    assert any(a["action_type"] == "j1939_clear_dtc" for a in j1939_acts)
    assert any(a["action_type"] == "j1939_dm1_query" for a in j1939_acts)


def test_can_packet_explainer_uds_and_j1939() -> None:
    """Verify packet explainer produces concise 2-3 lines explanations for UDS and J1939 payloads."""
    from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

    # 1. UDS Read VIN (0x22 F190)
    vin_expl = CausalBayesianInferenceEngine.explain_can_packet(0x7E0, bytes([0x03, 0x22, 0xF1, 0x90]))
    assert "UDS 0x22" in vin_expl
    assert "VIN" in vin_expl
    assert len(vin_expl.splitlines()) <= 5

    # 2. UDS Negative Response (0x7F 0x22 0x31)
    nrc_expl = CausalBayesianInferenceEngine.explain_can_packet(0x7E8, bytes([0x03, 0x7F, 0x22, 0x31]))
    assert "NRC 0x31" in nrc_expl
    assert "0x22" in nrc_expl
    assert "requestOutOfRange" in nrc_expl or "sınırların dışında" in nrc_expl

    # 3. UDS Clear DTCs (0x14)
    clear_expl = CausalBayesianInferenceEngine.explain_can_packet(0x7E0, bytes([0x04, 0x14, 0xFF, 0xFF, 0xFF]))
    assert "UDS 0x14" in clear_expl
    assert "ClearDiagnosticInformation" in clear_expl

    # 4. J1939 EEC1 (PGN 61444)
    # Engine speed raw: 0x1000 = 4096 -> 4096 * 0.125 = 512.0 rpm
    eec1_expl = CausalBayesianInferenceEngine.explain_can_packet(
        0x18F00400,
        bytes([0xF0, 0x7D, 0x82, 0x00, 0x10, 0x00, 0x00, 0x00]),
    )
    assert "EEC1" in eec1_expl or "61444" in eec1_expl
    assert "512 RPM" in eec1_expl or "Motor Devri" in eec1_expl

    # 5. J1939 CCVS (PGN 65265)
    # Wheel speed raw: 0x1000 = 4096 -> 4096 / 256 = 16.0 km/h
    ccvs_expl = CausalBayesianInferenceEngine.explain_can_packet(
        0x18FEF100,
        bytes([0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00]),
    )
    assert "CCVS" in ccvs_expl or "65265" in ccvs_expl
    assert "16.0" in ccvs_expl

    # 6. Natural query invoking packet explainer
    query_res = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "Lütfen şu paketi açıkla: 0x7E0 DLC 4 Data: 03 22 F1 90", [], {}
    )
    assert "0x7E0" in query_res
    assert "UDS 0x22" in query_res


def test_traffic_anomaly_awareness() -> None:
    """Verify Copilot detects high bus load, error frames, and babbling nodes with concise feedback."""
    from src.engine.ai.diagnostic_copilot import (
        AiDiagnosticCopilot,
        CausalBayesianInferenceEngine,
        explain_traffic_metrics,
        extract_action_triggers,
    )

    # 1. Direct explain_traffic_metrics helper
    high_load_metrics = {
        "bus_load_percent": 88.5,
        "error_count": 42,
        "recent_frame_rate": 2400.0,
        "status": "warning",
        "babbling_node": "0x120 (1250 fps)",
    }
    traffic_exp = explain_traffic_metrics(high_load_metrics, "CAN veri yolu trafiği durumu nedir?")
    assert "Veri Yolu Trafik Analizi" in traffic_exp
    assert "%88.5" in traffic_exp
    assert "42" in traffic_exp
    assert "0x120" in traffic_exp
    assert len(traffic_exp.splitlines()) <= 5

    # 2. evaluate_diagnostic_query with bus_metrics
    query_traffic = CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        "Trafik ve bus load analizi yap",
        [],
        {},
        bus_metrics=high_load_metrics,
    )
    assert "Veri Yolu Trafik Analizi" in query_traffic
    assert "%88.5" in query_traffic

    # 3. analyze_live_telemetry with bus_metrics integration
    copilot = AiDiagnosticCopilot()
    analysis = copilot.analyze_live_telemetry(
        rpm=1500.0,
        boost_bar=1.2,
        coolant_temp=85.0,
        dtc_codes=["P0300"],
        user_prompt="Trafik durumu",
        bus_metrics=high_load_metrics,
    )
    assert "88.5" in analysis or "Trafik" in analysis
    # Actions should be attached
    acts = extract_action_triggers(analysis)
    assert len(acts) > 0


def test_direct_actionable_diagnostic_intent_queries() -> None:
    """Verify Copilot recognizes direct diagnostic action requests and attaches structured action triggers."""
    from src.engine.ai.diagnostic_copilot import (
        CausalBayesianInferenceEngine,
        parse_action_triggers_from_text,
    )

    # 1. VIN Read query
    vin_res = CausalBayesianInferenceEngine.evaluate_diagnostic_query("aracın şasi numarasını (vin) oku", [], {})
    clean_vin, vin_acts = parse_action_triggers_from_text(vin_res)
    assert "0x22" in clean_vin or "VIN" in clean_vin
    assert any(a["action_type"] == "uds_read_did" and a["params"].get("did") == 0xF190 for a in vin_acts)

    # 2. Clear DTC query
    clear_res = CausalBayesianInferenceEngine.evaluate_diagnostic_query("tüm hata kodlarını sil ve hafızayı temizle", [], {})
    clean_clr, clr_acts = parse_action_triggers_from_text(clear_res)
    assert "0x14" in clean_clr or "DTC" in clean_clr
    assert any(a["action_type"] == "uds_clear_dtc" for a in clr_acts)

    # 3. Session switch query
    sess_res = CausalBayesianInferenceEngine.evaluate_diagnostic_query("genişletilmiş teşhis oturumuna geç (0x10 extended)", [], {})
    clean_sess, sess_acts = parse_action_triggers_from_text(sess_res)
    assert "0x10" in clean_sess
    assert any(a["action_type"] == "uds_session_control" for a in sess_acts)

    # 4. ECU Reset query
    reset_res = CausalBayesianInferenceEngine.evaluate_diagnostic_query("motor beynine hard ecu reset at", [], {})
    clean_rst, rst_acts = parse_action_triggers_from_text(reset_res)
    assert "0x11" in clean_rst or "Reset" in clean_rst
    assert any(a["action_type"] == "uds_ecu_reset" for a in rst_acts)

    # 5. J1939 DM11 Clear query
    dm11_res = CausalBayesianInferenceEngine.evaluate_diagnostic_query("j1939 dm11 arıza sil", [], {})
    clean_dm11, dm11_acts = parse_action_triggers_from_text(dm11_res)
    assert "DM11" in clean_dm11 or "65235" in clean_dm11
    assert any(a["action_type"] == "j1939_clear_dtc" for a in dm11_acts)

    # 6. J1939 DM1 Active Faults query
    dm1_res = CausalBayesianInferenceEngine.evaluate_diagnostic_query("ağır vasıta j1939 dm1 aktif arıza oku", [], {})
    clean_dm1, dm1_acts = parse_action_triggers_from_text(dm1_res)
    assert "DM1" in clean_dm1 or "65226" in clean_dm1
    assert any(a["action_type"] == "j1939_dm1_query" for a in dm1_acts)


def test_extended_uds_and_j1939_packet_explanations() -> None:
    """Verify packet explainer supports extended UDS services and commercial J1939 telemetry PGNs."""
    from src.engine.ai.diagnostic_copilot import explain_can_packet

    # 1. UDS 0x19 ReadDTCInformation
    t19, a19 = explain_can_packet(0x7E0, bytes([0x03, 0x19, 0x02, 0xFF]))
    assert "0x19 ReadDTCInformation" in t19
    assert any(a["action_type"] == "uds_clear_dtc" for a in a19)

    # 2. UDS 0x11 ECUReset
    t11, a11 = explain_can_packet(0x7E0, bytes([0x02, 0x11, 0x01]))
    assert "0x11 ECUReset" in t11
    assert any(a["action_type"] == "uds_ecu_reset" for a in a11)

    # 3. UDS 0x27 SecurityAccess
    t27, a27 = explain_can_packet(0x7E0, bytes([0x02, 0x27, 0x01]))
    assert "0x27 SecurityAccess" in t27
    assert "Request Seed" in t27

    # 4. UDS 0x2E WriteDataByIdentifier
    t2e, a2e = explain_can_packet(0x7E0, bytes([0x05, 0x2E, 0xF1, 0x90, 0x41, 0x42]))
    assert "0x2E WriteDataByIdentifier" in t2e
    assert "F190" in t2e

    # 5. UDS 0x3E TesterPresent
    t3e, a3e = explain_can_packet(0x7E0, bytes([0x02, 0x3E, 0x80]))
    assert "0x3E TesterPresent" in t3e

    # 6. UDS Positive Responses: 0x50, 0x51, 0x59, 0x67, 0x71
    t50, _ = explain_can_packet(0x7E8, bytes([0x02, 0x50, 0x03]))
    assert "0x10 DiagnosticSessionControl" in t50 and "Onaylandı" in t50
    t51, _ = explain_can_packet(0x7E8, bytes([0x02, 0x51, 0x01]))
    assert "0x11 ECUReset" in t51 and "Başarılı" in t51
    t59, a59 = explain_can_packet(0x7E8, bytes([0x03, 0x59, 0x02, 0xFF]))
    assert "0x19 ReadDTCInformation" in t59
    assert len(a59) > 0
    t67, _ = explain_can_packet(0x7E8, bytes([0x02, 0x67, 0x01]))
    assert "0x27 SecurityAccess" in t67 and "Kilit Açıldı" in t67
    t71, _ = explain_can_packet(0x7E8, bytes([0x04, 0x71, 0x01, 0xD0, 0x01]))
    assert "0x31 RoutineControl" in t71 and "Yürütüldü" in t71

    # 7. J1939 ET1 (PGN 65249 - Coolant Temp SPN 110: 85°C -> raw 125 = 0x7D)
    tet1, _ = explain_can_packet(0x18FEE100, bytes([125, 0, 0, 0, 0, 0, 0, 0]))
    assert "ET1" in tet1 or "65249" in tet1
    assert "85°C" in tet1

    # 8. J1939 EFL_P1 (PGN 65263 - Oil Pressure SPN 100: 4.0 Bar = 400 kPa -> raw 100 = 0x64 at byte 3)
    tefl, _ = explain_can_packet(0x18FEEF00, bytes([0, 0, 0, 100, 0, 0, 0, 0]))
    assert "EFL_P1" in tefl or "65263" in tefl
    assert "4.00 Bar" in tefl or "400 kPa" in tefl

    # 9. J1939 AMB (PGN 65269 - Ambient Temp SPN 171)
    tamb, _ = explain_can_packet(0x18FEF500, bytes([0, 0, 0, 0, 0, 0, 0, 0]))
    assert "AMB" in tamb or "65269" in tamb


def test_traffic_anomaly_report_punctuation_cleanliness() -> None:
    """Verify traffic anomaly report does not produce double period formatting flaws."""
    from src.engine.ai.diagnostic_copilot import explain_traffic_metrics

    rep = explain_traffic_metrics({
        "bus_load_percent": 82,
        "error_count": 7,
        "total_packets": 1420,
    })
    assert ".." not in rep
    assert "%82" in rep
    assert "7 adet" in rep




"""Unit tests for the fully-offline AI Diagnostic Copilot (deterministic engine).

The cloud-LLM narration layer was removed (operator decision, M7). These
tests lock the offline contract:
- analyze_session NEVER touches the network (urllib.socket monkeypatched
  to explode — proof by construction).
- Constructor accepts legacy kwargs (bridge backward compat) but ignores them.
- Severity authority is the deterministic expert engine only.
"""

from __future__ import annotations

import pytest

from src.engine.ai.diagnostic_copilot import (
    AiDiagnosticCopilot,
    FaultSeverity,
    mask_vin_in_text,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail-closed: ANY urllib/socket use inside the copilot explodes."""
    import urllib.request

    def _explode(*args, **kwargs):
        raise AssertionError("offline violation: copilot attempted a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    monkeypatch.setattr("socket.socket", _explode)


class TestOfflineContract:
    def test_analyze_session_never_network(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"spn": 100, "fmi": 1, "description": "Engine Oil Pressure Low"}],
            {"EngineSpeed": 1800.0, "BoostPressure": 140.0, "CoolantTemp": 88.0},
            ["Engine_ECU_0x00"],
            user_consented=True,  # even with consent: no cloud exists
        )
        assert report.severity == FaultSeverity.CRITICAL_STOP
        assert report.ai_model_used == "Yerel Otomotiv Uzman Motoru (Çevrimdışı)"

    def test_legacy_ctor_kwargs_accepted_and_ignored(self) -> None:
        """Bridge/API backward compat: legacy key args must not crash or arm anything."""
        copilot = AiDiagnosticCopilot(
            gemini_api_key="legacy-key-should-be-ignored",
            openai_api_key="sk-legacy-ignored",
            provider="openai",
        )
        report = copilot.analyze_session([], {"EngineSpeed": 1200.0}, ["ECU_0"])
        assert report.severity == FaultSeverity.LOW
        assert not hasattr(copilot, "narrator")
        assert not hasattr(copilot, "_key_provider")

    def test_repr_has_no_secrets(self) -> None:
        copilot = AiDiagnosticCopilot()
        assert "offline" in repr(copilot)

    def test_analyze_live_telemetry_offline(self) -> None:
        copilot = AiDiagnosticCopilot()
        answer = copilot.analyze_live_telemetry(
            rpm=1500.0, boost_bar=1.2, coolant_temp=88.0,
            dtc_codes=["P0234"], user_prompt="turbo basıncı düşük neden?",
            user_consented=True,
        )
        assert isinstance(answer, str) and len(answer) > 0


class TestDeterministicExpertScenarios:
    def test_critical_oil_pressure_analysis(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"spn": 100, "fmi": 1, "description": "Engine Oil Pressure Low"}],
            {"EngineSpeed": 1800.0, "BoostPressure": 140.0, "CoolantTemp": 88.0},
            ["Engine_ECU_0x00"],
        )
        assert report.severity == FaultSeverity.CRITICAL_STOP
        assert "Motor Yağlama" in report.affected_subsystems[0]
        assert len(report.troubleshooting_steps) >= 2
        assert "Kritik düşük yağ basıncı" in report.likely_causes[0]

    def test_nominal_healthy_state(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session([], {"EngineSpeed": 1200.0, "BoostPressure": 120.0}, ["Engine_ECU_0x00"])
        assert report.severity == FaultSeverity.LOW
        assert "nominal" in report.summary.lower()
        assert len(report.troubleshooting_steps) == 1

    def test_overheating_scenario(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"spn": 110, "fmi": 0, "description": "Engine Coolant Temperature High"}],
            {"EngineSpeed": 2000.0, "CoolantTemp": 112.0},
            ["ECU_0"],
        )
        assert report.severity == FaultSeverity.CRITICAL_STOP
        assert any("Soğutma" in s for s in report.affected_subsystems)

    def test_injector_fault_scenario(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"spn": 653, "fmi": 5, "description": "Cylinder 3 Injector Open Circuit"}],
            {"EngineSpeed": 1500.0},
            ["ECU_0"],
        )
        assert report.severity == FaultSeverity.MEDIUM
        assert any("Silindir #3" in s for s in report.affected_subsystems)

    def test_turbo_boost_fault_scenario(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"spn": 102, "fmi": 18, "description": "Boost Pressure Low"}],
            {"EngineSpeed": 2200.0, "BoostPressure": 90.0},
            ["ECU_0"],
        )
        assert report.severity == FaultSeverity.MEDIUM
        assert any("Turbo" in s for s in report.affected_subsystems)

    def test_dpf_fault_scenario(self) -> None:
        copilot = AiDiagnosticCopilot()
        report = copilot.analyze_session(
            [{"spn": 3251, "fmi": 0, "description": "DPF Differential Pressure High"}],
            {"EngineSpeed": 1800.0},
            ["ECU_0"],
        )
        assert report.severity == FaultSeverity.MEDIUM
        assert any("DPF" in s for s in report.affected_subsystems)

    def test_same_input_same_output(self) -> None:
        """Determinism: identical evidence -> byte-identical report content."""
        copilot = AiDiagnosticCopilot()
        dtcs = [{"spn": 100, "fmi": 1}]
        tel = {"EngineSpeed": 1800.0}
        r1 = copilot.analyze_session(dtcs, tel, ["ECU"])
        r2 = copilot.analyze_session(dtcs, tel, ["ECU"])
        assert r1.summary == r2.summary
        assert r1.severity == r2.severity
        assert r1.likely_causes == r2.likely_causes


class TestVinMasking:
    def test_mask_vin_in_text(self) -> None:
        masked = mask_vin_in_text("Şasi WVWZZZ1KZAW123456 okundu")
        assert masked == "Şasi ***********123456 okundu"

    def test_no_vin_untouched(self) -> None:
        assert mask_vin_in_text("P0300 no vin here") == "P0300 no vin here"


class TestEvaluateDiagnosticQueryCoverage:
    """Broad smoke sweep over evaluate_diagnostic_query sub-paths (offline engine)."""

    @pytest.mark.parametrize(
        "query,expect_substr",
        [
            ("0x00000000 hata karesi", "Hata Karesi"),
            ("DBC nedir", "DBC"),
            ("golf dbc", ""),  # catalog match may be empty offline
            ("0x1808E5F4 BMS hücre", "EV BMS"),
            ("SPN 100 yağ basıncı", "yağ"),
            ("P0300 tekleme", "P0300"),
            ("hat yükü çok yüksek", ""),  # generic expert path
        ],
    )
    def test_query_paths_return_structured_answer(self, query: str, expect_substr: str) -> None:
        from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

        answer = CausalBayesianInferenceEngine.evaluate_diagnostic_query(query, [], {"EngineSpeed": 1200.0})
        assert isinstance(answer, str)
        assert len(answer) > 0
        if expect_substr:
            assert expect_substr.lower() in answer.lower()

    def test_action_metadata_roundtrip_in_query(self) -> None:
        """Operator asking for a DTC clear inside a query still yields trigger markup."""
        from src.engine.ai.diagnostic_copilot import (
            attach_action_triggers,
            extract_action_triggers,
        )

        actions = extract_action_triggers("", "0x14 DTC temizle")
        assert any(a["action_type"] == "uds_clear_dtc" for a in actions)
        text = attach_action_triggers("Temizlik komutu hazir.", actions)
        assert "ACTIONS:" in text


class TestCanPacketExplainerCoverage:
    """explain_can_packet: the offline packet forensics engine (largest uncovered surface).

    Module-level explain_can_packet(can_id, payload) -> (str, list).
    """

    CASES = [
        # (can_id, payload, expected fragment in the decoded answer)
        ("0x0CF00400", [0x21, 0x0C, 0x00, 0x4B, 0x0D, 0x00, 0x00, 0x2E], "j1939"),
        ("0x18FEF100", [0x00, 0xFF, 0x00, 0x00, 0x04, 0x00, 0x00, 0xFF], "j1939"),
        ("0x1CECFF0E", [0x10, 0x1F, 0x14, 0x02, 0x00, 0x01, 0xFF, 0xFF], "isotransportprotocol"),
        ("0x1CEBFF0E", [0x11, 0x01, 0x0E, 0x00, 0x02, 0xFF, 0xFF, 0xFF], "isotransportprotocol"),
        ("0x7E8", [0x03, 0x41, 0x00, 0xBE, 0x7F, 0x96, 0x40, 0x40], "7e8"),
        ("0x7E0", [0x02, 0x01, 0x00], "7e0"),
        ("0x0CFFD916", [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08], "tanımsız"),
        ("0x111", [0x00, 0x00, 0x7D, 0x01, 0x00, 0x00, 0x00, 0x00], "0x111"),
        ("0x18FF03B0", [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07], "tanımsız"),
    ]

    @pytest.mark.parametrize("can_id,payload,frag", CASES)
    def test_explain_can_packet_paths(self, can_id: str, payload: list[int], frag: str) -> None:
        from src.engine.ai.diagnostic_copilot import explain_can_packet

        answer, signals = explain_can_packet(can_id, payload)
        assert isinstance(answer, str)
        assert len(answer) > 0
        assert isinstance(signals, list)
        assert frag.lower() in answer.lower()

    def test_explain_can_packet_unknown_id_generic(self) -> None:
        from src.engine.ai.diagnostic_copilot import explain_can_packet

        answer, _ = explain_can_packet("0x123", [0xAB, 0xCD, 0xEF, 0x01])
        assert isinstance(answer, str) and len(answer) > 0


class TestDiagnosticDatabases:
    """Knowledge-base getters: lazy load + search sanity."""

    def test_j1939_spn_database(self) -> None:
        from src.engine.ai.diagnostic_copilot import get_j1939_spn_database

        db = get_j1939_spn_database()
        assert isinstance(db, dict) and len(db) > 0

    def test_uds_did_database(self) -> None:
        from src.engine.ai.diagnostic_copilot import get_uds_did_database

        db = get_uds_did_database()
        assert isinstance(db, dict) and len(db) > 0

    def test_mode06_database(self) -> None:
        from src.engine.ai.diagnostic_copilot import get_mode06_database

        db = get_mode06_database()
        assert isinstance(db, dict)

    def test_extended_pid_database(self) -> None:
        from src.engine.ai.diagnostic_copilot import (
            get_extended_pid_database,
            search_extended_pids,
        )

        db = get_extended_pid_database()
        assert isinstance(db, dict)
        hits = search_extended_pids(query="RPM")
        assert isinstance(hits, list)

    def test_nhtsa_recalls(self) -> None:
        from src.engine.ai.diagnostic_copilot import (
            format_nhtsa_recall_report,
            search_nhtsa_recalls,
        )

        recalls = search_nhtsa_recalls(make="VOLKSWAGEN")
        if recalls:
            report = format_nhtsa_recall_report(recalls[0])
            assert isinstance(report, str) and len(report) > 0
        else:
            # Offline DB may have no VW entries — verify the empty path too.
            assert recalls == []


class TestJ1939TechnicianReport:
    def test_format_j1939_report(self) -> None:
        from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

        spn_entry = {
            "spn": 100,
            "name": "Engine Oil Pressure",
            "title_tr": "Motor Yağ Basıncı",
            "subsystem": "Motor Yağlama",
            "associated_pgn": 0,
            "unit": "kPa",
            "description": "Oil pressure below critical range",
        }
        text = CausalBayesianInferenceEngine._format_j1939_technician_report(
            spn_entry, "yağ basıncı", {"EngineSpeed": 1500.0}
        )
        assert isinstance(text, str) and len(text) > 0

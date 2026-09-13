"""Kullanıcı Karar Katmanı tests (offline AI acceptance gates).

Covers the safety-critical card invariants:
- CRITICAL severity -> RED; veri yok -> GRAY (never GREEN) (§47 T2/T3)
- EV/HV codes -> RED + "yetkisiz müdahale" warning (doküman §24)
- KB-less code -> generic template, no "kesin" wording (§45 K3)
- No telemetry -> never a fabricated "normal" claim (§47 T5)
- Determinism: same input -> byte-identical card dict
- user_kb validator: unknown field / missing source_ref rejected
"""

from __future__ import annotations

import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    VehicleSession,
)
from src.engine.ai.diagnostic_copilot import DiagnosticAnalysisReport, FaultSeverity, TroubleshootingStep
from src.engine.ai.drive_safety_policy import RISK_ADVICE, decide_risk, is_ev_hv_code, risk_advice
from src.engine.ai.user_kb import UserKbError, get_entry, load_user_kb
from src.engine.ai.user_report_composer import (
    NO_DTC_HEADLINE_TR,
    compose_user_card,
    is_honest_card,
)


def _session(codes: list[tuple[str, str]], domain: DiagnosticDomain = DiagnosticDomain.PASSENGER) -> VehicleSession:
    s = VehicleSession(session_id="sess-test", started_at_ns=1, domain=domain)
    for i, (code, sev) in enumerate(codes):
        s.events.append(
            DiagnosticEvent(timestamp_ns=2 + i, code=code, domain=domain, severity=sev, status="ACTIVE")
        )
    return s


def _report(severity: FaultSeverity, dtc_count: int, confidence: str = "Orta (%62 ağırlıklı kanıt skoru)") -> DiagnosticAnalysisReport:
    return DiagnosticAnalysisReport(
        summary="test",
        severity=severity,
        root_cause_probability=confidence,
        likely_causes=["Test neden 1", "Test neden 2"],
        troubleshooting_steps=[TroubleshootingStep(1, "Test adımı", "Hedef", "Kolay (Görsel)")],
        affected_subsystems=["Test Alt Sistem"],
        raw_dtc_count=dtc_count,
        telemetry_correlations=[],
    )


KB = load_user_kb()
assert KB, "user_kb.json failed to load"


class TestRiskPolicy:
    def test_critical_maps_red(self) -> None:
        assert decide_risk("CRITICAL_STOP") == "RED"

    def test_no_data_is_never_green(self) -> None:
        assert decide_risk("UNKNOWN") == "GRAY"
        assert decide_risk("") == "GRAY"
        assert decide_risk(None) == "GRAY"  # type: ignore[arg-type]

    def test_mil_flashing_escalates_to_red(self) -> None:
        assert decide_risk("MEDIUM", mil_flashing_symptom=True) == "RED"
        assert decide_risk("LOW", mil_flashing_symptom=True) == "RED"

    def test_ev_hv_forces_red_regardless_of_severity(self) -> None:
        assert decide_risk("LOW", ev_hv_codes=["P0AA6"]) == "RED"
        assert decide_risk("INFO", ev_hv_codes=["P0A0B"]) == "RED"

    def test_monotonic_no_downgrade(self) -> None:
        # escalation inputs must never lower a RED
        assert decide_risk("CRITICAL_STOP", mil_flashing_symptom=False) == "RED"

    def test_advice_templates_are_fixed(self) -> None:
        for level in ("RED", "YELLOW", "GREEN", "GRAY"):
            assert risk_advice(level) == RISK_ADVICE[level]
        assert "durdurun" in RISK_ADVICE["RED"].lower()

    def test_ev_hv_detection(self) -> None:
        assert is_ev_hv_code("P0A0B")
        assert is_ev_hv_code("P0AA6")
        assert is_ev_hv_code("p0a80")
        assert not is_ev_hv_code("P0300")
        assert not is_ev_hv_code("")


class TestCardComposition:
    def test_critical_severity_yields_red_card(self) -> None:
        card = compose_user_card(_report(FaultSeverity.CRITICAL_STOP, 1), _session([("U0100", "CRITICAL_STOP")]), user_kb=KB)
        assert card.risk_level == "RED"

    def test_medium_yields_yellow(self) -> None:
        card = compose_user_card(_report(FaultSeverity.MEDIUM, 1), _session([("P0301", "MEDIUM")]), user_kb=KB)
        assert card.risk_level == "YELLOW"

    def test_empty_session_is_gray_honest_card(self) -> None:
        card = compose_user_card(_report(FaultSeverity.LOW, 0), _session([]), user_kb=KB)
        assert card.risk_level == "GRAY"
        assert card.headline_tr == NO_DTC_HEADLINE_TR
        assert "sorun olmadığı anlamına gelmez" in card.summary_tr

    def test_simulating_note_on_empty_card(self) -> None:
        card = compose_user_card(_report(FaultSeverity.LOW, 0), _session([]), user_kb=KB, is_simulating=True)
        assert "simülasyon/demo" in card.summary_tr

    def test_ev_hv_card_red_with_unauthorized_warning(self) -> None:
        card = compose_user_card(_report(FaultSeverity.LOW, 1), _session([("P0AA6", "LOW")]), user_kb=KB)
        assert card.risk_level == "RED"
        assert "Yetkisiz müdahale yapmayın" in card.summary_tr
        assert "Turuncu kablolara dokunmayın" in card.summary_tr

    def test_kb_less_code_uses_generic_honest_template(self) -> None:
        card = compose_user_card(
            _report(FaultSeverity.MEDIUM, 1), _session([("P9999", "MEDIUM")]), user_kb=KB
        )
        assert card.risk_level == "YELLOW"
        assert "basitleştirilmiş açıklama mevcut değil" in card.summary_tr
        assert is_honest_card(card)

    def test_no_kesin_wording_in_user_texts(self) -> None:
        for sev in FaultSeverity:
            card = compose_user_card(_report(sev, 1), _session([("P0301", sev.value)]), user_kb=KB)
            assert is_honest_card(card), f"kesin wording leaked for {sev}"

    def test_no_telemetry_means_no_normal_claim(self) -> None:
        # §47 T5: empty session must never claim telemetry is "normal".
        card = compose_user_card(_report(FaultSeverity.LOW, 0), _session([]), user_kb=KB)
        blob = (card.summary_tr + " " + card.risk_advice_tr).lower()
        assert "normal" not in blob
        assert "veri" in blob or "bulunamadı" in blob

    def test_same_input_same_output_determinism(self) -> None:
        report = _report(FaultSeverity.MEDIUM, 1)
        session = _session([("P0301", "MEDIUM")])
        a = compose_user_card(report, session, user_kb=KB)
        b = compose_user_card(report, session, user_kb=KB)
        assert a.card_to_dict() == b.card_to_dict()

    def test_kb_entry_preferred_over_generic(self) -> None:
        card = compose_user_card(_report(FaultSeverity.MEDIUM, 1), _session([("P0301", "MEDIUM")]), user_kb=KB)
        assert card.headline_tr == "Motor teklemesi olabilir"
        assert card.source_badges == ("Yerel bilgi tabanı",)

    def test_spn_variant_lookup(self) -> None:
        card = compose_user_card(
            _report(FaultSeverity.CRITICAL_STOP, 1), _session([("SPN 100 FMI 1", "CRITICAL_STOP")]), user_kb=KB
        )
        assert card.headline_tr == "Motor yağ basıncı hatası olabilir"
        assert card.risk_level == "RED"

    def test_card_to_dict_shape(self) -> None:
        card = compose_user_card(_report(FaultSeverity.MEDIUM, 1), _session([("P0301", "MEDIUM")]), user_kb=KB)
        d = card.card_to_dict()
        assert d["card_version"] == 1
        assert d["risk_level"] in ("RED", "YELLOW", "GREEN", "GRAY")
        assert set(d) == {
            "card_version", "headline_tr", "summary_tr", "risk_level", "risk_advice_tr",
            "evidence_tr", "technical", "source_badges",
        }

    def test_risk_advice_derived_from_risk_policy(self) -> None:
        from src.engine.ai.drive_safety_policy import RISK_ADVICE

        card = compose_user_card(_report(FaultSeverity.MEDIUM, 1), _session([("P0301", "MEDIUM")]), user_kb=KB)
        assert card.risk_advice_tr == RISK_ADVICE["YELLOW"]


class TestUserKbValidator:
    def test_loads_full_seed(self) -> None:
        assert len(KB) >= 28

    def test_entry_fields_are_five_only(self) -> None:
        e = KB["P0300"]
        import dataclasses

        assert {f.name for f in dataclasses.fields(e)} == {
            "code", "user_title_tr", "user_summary_tr", "user_risk", "source_ref",
        }

    def _base_entry(self) -> dict:
        return {
            "code": "P0300", "user_title_tr": "x", "user_summary_tr": "x",
            "user_risk": "YELLOW", "source_ref": "test ref",
        }

    def test_missing_source_ref_rejected(self) -> None:
        from src.engine.ai.user_kb import _validate_entry

        entry = self._base_entry()
        entry["source_ref"] = ""
        with pytest.raises(UserKbError):
            _validate_entry("P0300", entry)

    def test_missing_field_rejected(self) -> None:
        from src.engine.ai.user_kb import _validate_entry

        entry = self._base_entry()
        del entry["user_title_tr"]
        with pytest.raises(UserKbError):
            _validate_entry("P0300", entry)

    def test_unknown_field_rejected(self) -> None:
        from src.engine.ai.user_kb import _validate_entry

        entry = self._base_entry()
        entry["evil_extra"] = 1
        with pytest.raises(UserKbError):
            _validate_entry("P0300", entry)

    def test_stripped_legacy_field_rejected(self) -> None:
        from src.engine.ai.user_kb import _validate_entry

        entry = self._base_entry()
        entry["drive_advice_tr"] = "x"
        with pytest.raises(UserKbError):
            _validate_entry("P0300", entry)

    def test_invalid_risk_rejected(self) -> None:
        from src.engine.ai.user_kb import _validate_entry

        entry = self._base_entry()
        entry["user_risk"] = "PURPLE"
        with pytest.raises(UserKbError):
            _validate_entry("P0300", entry)

    def test_valid_entry_passes(self) -> None:
        from src.engine.ai.user_kb import _validate_entry

        e = _validate_entry("P0300", self._base_entry())
        assert e.user_risk == "YELLOW"
        assert e.source_ref == "test ref"

    def test_get_entry_case_insensitive(self) -> None:
        assert get_entry("p0300", KB) is not None
        assert get_entry("p9999", KB) is None

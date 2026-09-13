"""FAZ 6 unit tests: technician report generation (determinism + honesty)."""

from __future__ import annotations

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.anomaly_detector import AnomalyFinding
from src.engine.ai.evidence_gate import SufficiencyReport
from src.engine.ai.golden_similarity import CaseMatch
from src.engine.ai.hypothesis_engine import Hypothesis
from src.engine.ai.session_report import build_technician_report


def _session(samples=(), events=()) -> VehicleSession:
    s = VehicleSession(session_id="sess-report", started_at_ns=1_000_000_000, domain=DiagnosticDomain.HEAVY_DUTY)
    s.samples.extend(samples)
    s.events.extend(events)
    return s


def _gate(anomaly=True, dtc=False) -> SufficiencyReport:
    return SufficiencyReport(
        anomaly_sufficient=anomaly,
        dtc_sufficient=dtc,
        gaps=[] if dtc else (["aktif DTC kaydı yok"] if anomaly else ["örnek yok"]),
        signal_inventory={"EngineSpeed": 12},
        active_dtc_count=1 if dtc else 0,
    )


def _full_session():
    samples = [
        SignalSample(
            timestamp_ns=1_000_000_000 + i * 6_000_000_000,
            name="EngineSpeed",
            raw_value=800,
            physical_value=100.0 * 8,
            unit="rpm",
            source=SignalSource.J1939,
        )
        for i in range(12)
    ]
    events = [
        DiagnosticEvent(
            timestamp_ns=1_070_000_000_000,
            code="SPN 100 FMI 1",
            domain=DiagnosticDomain.HEAVY_DUTY,
            severity="CRITICAL_STOP",
            status="ACTIVE",
        )
    ]
    return _session(samples, events)


class TestReport:
    def test_empty_session_short_sim_report(self) -> None:
        report = build_technician_report(
            _session(), _gate(anomaly=False), [], [], []
        )
        # Bulgu 7: no evidence -> SHORT report, no fabricated hypothesis table.
        assert "canlı veri kaydı yok — simülasyon/demo modu" in report
        assert "Hipotez tablosu üretilmedi" in report
        assert "| # |" not in report

    def test_full_report_sections(self) -> None:
        anomalies = [AnomalyFinding("EngineCoolantTemp", "5/5 örnek aralık dışı", 1.0, 5)]
        hyps = [Hypothesis("oil-pump-wear", "Yağ pompası aşınması", 0.8, ["SPN 100"], [])]
        cases = [CaseMatch("case-x", 0.78, ["DTC kesişimi"])]
        report = build_technician_report(_full_session(), _gate(dtc=True), anomalies, hyps, cases)
        for section in ("Veri Kalitesi", "Aktif DTC", "Anomali Bulguları", "Hipotez Sıralaması", "Benzer Doğrulanmış Vakalar", "Ayrıştırıcı"):
            assert section in report, f"missing section {section}"
        assert "| # | Hipotez |" in report
        assert "%78" in report

    def test_vin_masked_shown_not_raw(self) -> None:
        s = _full_session()
        object.__setattr__(s, "vin_masked", "***********123456")
        report = build_technician_report(s, _gate(dtc=True), [], [], [])
        assert "***********123456" in report
        # A raw 17-char VIN must never appear.
        assert "WVWZZZ1KZAW123456" not in report

    def test_actions_roundtrip(self) -> None:
        from src.engine.ai.diagnostic_copilot import parse_action_triggers_from_text

        report = build_technician_report(_full_session(), _gate(dtc=True), [], [], [])
        clean, actions = parse_action_triggers_from_text(report)
        assert actions, "report must embed deterministic action triggers"
        assert "ACTIONS:" not in clean
        kinds = {a["action_type"] for a in actions}
        assert "uds_clear_dtc" in kinds
        assert "j1939_dm1_query" in kinds

    def test_no_actions_when_no_dtcs(self) -> None:
        from src.engine.ai.diagnostic_copilot import parse_action_triggers_from_text

        report = build_technician_report(_session(), _gate(anomaly=False), [], [], [])
        _, actions = parse_action_triggers_from_text(report)
        assert actions == []

    def test_determinism_of_body(self) -> None:
        anomalies = [AnomalyFinding("EngineCoolantTemp", "5/5 örnek aralık dışı", 1.0, 5)]
        hyps = [Hypothesis("oil-pump-wear", "Yağ pompası aşınması", 0.8, ["SPN 100"], [])]
        r1 = build_technician_report(_full_session(), _gate(dtc=True), anomalies, hyps, [])
        r2 = build_technician_report(_full_session(), _gate(dtc=True), anomalies, hyps, [])
        # The cover line carries wall-clock (read-only record field); body
        # content below the cover must be identical.
        body1 = r1.splitlines()[3:]
        body2 = r2.splitlines()[3:]
        assert body1 == body2

    def test_insufficient_gaps_listed(self) -> None:
        # Insufficient-but-recorded session (some samples exist) -> the gaps
        # are listed under Veri Kalitesi.
        gate = SufficiencyReport(
            anomaly_sufficient=False,
            dtc_sufficient=False,
            gaps=["sinyal 'EngineSpeed': yalnız 3 örnek (min 10)"],
            signal_inventory={"EngineSpeed": 3},
            active_dtc_count=0,
        )
        samples = [
            SignalSample(timestamp_ns=1_000_000_000 + i, name="EngineSpeed", raw_value=800,
                         physical_value=100.0, unit="rpm", source=SignalSource.J1939)
            for i in range(3)
        ]
        report = build_technician_report(_session(samples), gate, [], [], [])
        assert "yalnız 3 örnek" in report
        assert "Yetersiz veri" in report

    def test_operator_measurement_labelled(self) -> None:
        anomalies = [AnomalyFinding("OP:EngineOilPressure", "3/3 örnek aralık dışında", 1.0, 3, synthetic=True)]
        report = build_technician_report(_full_session(), _gate(dtc=True), anomalies, [], [])
        assert "operatör beyanı" in report

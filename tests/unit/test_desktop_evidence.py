"""FAZ 1 desktop evidence tests: decode hooks + DM1 events + sim isolation.

Uses the same fake-frame builders as test_desktop_app.py; nothing touches
physical hardware (virtual bus).
"""

from __future__ import annotations

from src.ui.desktop_app import UniversalCanDesktopApp


def _app() -> UniversalCanDesktopApp:
    return UniversalCanDesktopApp(channel="vcan0", bitrate=250000)


def _ccvs_frame(sa: int, raw_speed: int):
    from src.core.models.can_frame import CanFrame

    arb = 0x18FEF100 | sa
    data = bytes([0x00, raw_speed & 0xFF, (raw_speed >> 8) & 0xFF, 0, 0, 0, 0, 0])
    return CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)


def _eec1_frame(sa: int, raw_rpm: int):
    from src.core.models.can_frame import CanFrame

    arb = 0x18F00400 | sa
    data = bytes([0, 0, 0, raw_rpm & 0xFF, (raw_rpm >> 8) & 0xFF, 0, 0, 0])
    return CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)


def _et1_frame(sa: int, raw_temp: int):
    from src.core.models.can_frame import CanFrame

    arb = 0x18FEEE00 | sa
    data = bytes([raw_temp & 0xFF, 0, 0, 0, 0, 0, 0, 0])
    return CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)


def _dm1_frame(spn: int, fmi: int, sa: int = 0x00):
    """DM1 (PGN 65226) with one 4-byte DTC record (SPN/FMI/OC/CM)."""
    from src.core.models.can_frame import CanFrame

    arb = 0x18FECA00 | sa
    b0 = spn & 0xFF
    b1 = (spn >> 8) & 0xFF
    b2 = ((spn >> 16) & 0xE0) | (fmi & 0x1F)
    b3 = 0x01  # occurrence 1, conversion 0
    data = bytes([0x00, 0x00, b0, b1, b2, b3, 0xFF, 0xFF])
    return CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)


class TestLiveSignalEvidence:
    def test_eec1_decode_records_signal_sample(self) -> None:
        app = _app()
        app._decode_j1939_signal(_eec1_frame(0x00, 16000))  # 2000 rpm
        session = app._diag_session
        assert session is not None
        samples = [s for s in session.samples if s.name == "EngineSpeed"]
        assert len(samples) == 1
        assert samples[0].physical_value == 2000.0
        assert samples[0].source.value == "J1939"
        assert samples[0].timestamp_ns > 0

    def test_ccvs_speed_records_sample(self) -> None:
        app = _app()
        app._decode_j1939_signal(_eec1_frame(0x00, 0))
        app._decode_j1939_signal(_ccvs_frame(0x00, 5120))  # 20 km/h
        samples = [s for s in app._diag_session.samples if s.name == "VehicleSpeed"]
        assert len(samples) == 1
        assert samples[0].physical_value == 20.0

    def test_et1_temp_records_sample(self) -> None:
        app = _app()
        app._decode_j1939_signal(_et1_frame(0x00, 100))  # 60 C
        samples = [s for s in app._diag_session.samples if s.name == "EngineCoolantTemp"]
        assert len(samples) == 1
        assert samples[0].physical_value == 60.0

    def test_sentinel_values_never_record(self) -> None:
        app = _app()
        app._decode_j1939_signal(_eec1_frame(0x00, 0xFE00))  # Error sentinel
        app._decode_j1939_signal(_et1_frame(0x00, 0xFE))  # Error sentinel
        assert app._diag_session.samples == []


class TestDm1Evidence:
    def test_dm1_dtc_becomes_diagnostic_event(self) -> None:
        app = _app()
        app._decode_j1939_signal(_dm1_frame(spn=100, fmi=1))
        events = [e for e in app._diag_session.events if e.code == "SPN 100 FMI 1"]
        assert len(events) == 1
        assert events[0].status == "ACTIVE"
        assert events[0].domain.value == "HEAVY_DUTY"
        # Severity from the SPN DB record for SPN 100 FMI 1 (CRITICAL_STOP)
        assert events[0].severity == "CRITICAL_STOP"

    def test_unknown_spn_gets_unknown_severity_not_fabricated(self) -> None:
        app = _app()
        app._decode_j1939_signal(_dm1_frame(spn=521999, fmi=9))
        events = list(app._diag_session.events)
        assert len(events) == 1
        assert events[0].severity == "UNKNOWN"

    def test_no_active_dtc_dm1_produces_no_event(self) -> None:
        # all-0xFF padding record -> parser skips -> no event
        app = _app()
        from src.core.models.can_frame import CanFrame

        arb = 0x18FECA00
        data = bytes([0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
        frame = CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)
        app._decode_j1939_signal(frame)
        assert app._diag_session.events == []


class TestSimIsolation:
    def test_sim_mode_appends_nothing(self) -> None:
        """Bulgu 7: simulator data must never enter the evidence base."""
        app = _app()
        app._set_ui_state(_is_simulating=True)
        n_before = len(app._diag_session.samples)
        app._decode_j1939_signal(_eec1_frame(0x00, 16000))
        app._decode_j1939_signal(_dm1_frame(spn=100, fmi=1))
        assert len(app._diag_session.samples) == n_before
        assert len(app._diag_session.events) == 0

    def test_reset_diagnostic_session_clears_evidence(self) -> None:
        app = _app()
        app._decode_j1939_signal(_eec1_frame(0x00, 16000))
        assert app._diag_session.samples
        old_id = app._diag_session.session_id
        app.reset_diagnostic_session()
        assert app._diag_session.session_id != old_id
        assert app._diag_session.samples == []
        assert app._signal_rings == {}


class TestOperatorMeasurement:
    def test_record_operator_measurement_prefixed(self) -> None:
        app = _app()
        res = app.record_operator_measurement("yağ basıncı", 0.4)
        assert res.get("success") is True
        assert res["recorded"].startswith("OP:")
        samples = [s for s in app._diag_session.samples if s.name.startswith("OP:")]
        assert len(samples) == 1
        assert samples[0].physical_value == 0.4

    def test_rejects_bad_values(self) -> None:
        app = _app()
        assert app.record_operator_measurement("x", "abc").get("success") is False
        assert app.record_operator_measurement("", 1.0).get("success") is False

        assert app.record_operator_measurement("x", float("nan")).get("success") is False


class TestBridgeSurface:
    def test_bridge_exposes_session_apis(self) -> None:
        from src.ui.desktop_app import DesktopApiBridge

        app = _app()
        bridge = DesktopApiBridge(app)
        summary = bridge.get_session_evidence_summary()
        assert summary.get("success") is True
        assert "signal_inventory" in summary
        analysis = bridge.get_diagnostic_analysis()
        assert analysis.get("success") is True
        # Empty session: gate insufficient, no anomalies, no hypotheses.
        assert analysis["gate"]["anomaly_sufficient"] is False
        assert analysis["hypotheses"] == []

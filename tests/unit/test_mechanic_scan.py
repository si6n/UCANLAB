"""Aşama 6: read-only OBD channel, scan runner, mechanic result card, customer report."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.core.models.diagnostics import DiagnosticDomain, DiagnosticEvent, Severity, VehicleSession
from src.engine.diagnosis.events import dm1_to_events, obd_codes_to_events
from src.engine.diagnosis.mechanic_result import (
    REPORT_DISCLAIMER_TR,
    CodeEvidence,
    ScanContext,
    _is_turkish,
    compose_mechanic_result,
    customer_report_text,
    explain_code,
)
from src.engine.diagnosis.obd_reader import ObdCode, ObdReadOutcome, SimulatedObdEcu, read_obd_fault_codes
from src.engine.diagnosis.scan import ScanRequest, ScanRunner, SimulatorScanBackend, _unique_session
from src.hal.base import AbstractBus
from src.safety.criticality import CRITICAL_UDS_SIDS
from src.safety.gateway import TxSafetyGateway
from src.safety.read_only_policy import ReadOnlyPolicy
from src.safety.state_machine import SafetyState


def _frame(arbitration_id: int, data: bytes, *, extended: bool = False, fd: bool = False) -> CanFrame:
    return CanFrame(channel_id="t", arbitration_id=arbitration_id, dlc=min(len(data), 8), data=data,
                    is_extended=extended, is_fd=fd)


def _policy(ttl_s: float = 60.0) -> ReadOnlyPolicy:
    return ReadOnlyPolicy(expires_ns=time.monotonic_ns() + int(ttl_s * 1e9))


# ---------------------------------------------------------------------------
# Read-only policy: exactly the read requests, nothing else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [0x01, 0x03, 0x07, 0x09, 0x0A])
def test_policy_allows_read_modes(mode: int) -> None:
    data = bytes([0x02, mode, 0x00]) if mode in (0x01, 0x09) else bytes([0x01, mode])
    assert _policy().violation(_frame(0x7DF, data.ljust(8, b"\x55")), time.monotonic_ns()) is None
    assert _policy().violation(_frame(0x7E0, data.ljust(8, b"\x55")), time.monotonic_ns()) is None


@pytest.mark.parametrize(
    ("arbitration_id", "data", "reason"),
    [
        (0x7DF, bytes([0x01, 0x04]), "READ_ONLY_SERVICE"),  # OBD Mode 04: clear codes
        (0x7E0, bytes([0x04, 0x14, 0xFF, 0xFF, 0xFF]), "READ_ONLY_SERVICE"),  # UDS clear DTC
        (0x7E0, bytes([0x02, 0x10, 0x03]), "READ_ONLY_SERVICE"),  # UDS session control
        (0x7E0, bytes([0x03, 0x22, 0xF1, 0x90]), "READ_ONLY_SERVICE"),  # UDS read DID (not in the list)
        (0x7E0, bytes([0x02, 0x2F, 0x01]), "READ_ONLY_SERVICE"),  # actuator test
        (0x7E8, bytes([0x01, 0x03]), "READ_ONLY_ID"),  # ECU response id
        (0x123, bytes([0x01, 0x03]), "READ_ONLY_ID"),
        (0x7DF, bytes([0x30, 0x00, 0x00]), "READ_ONLY_PAYLOAD"),  # flow control to functional id
        (0x7E0, bytes([0x31, 0x00, 0x00]), "READ_ONLY_PAYLOAD"),  # flow control "wait"
        (0x7E0, bytes([0x10, 0x0A, 0x03, 0, 0, 0, 0, 0]), "READ_ONLY_PAYLOAD"),  # first frame
        (0x7E0, bytes([0x21, 0, 0, 0, 0, 0, 0, 0]), "READ_ONLY_PAYLOAD"),  # consecutive frame
        (0x7E0, bytes([0x07, 0x03]), "READ_ONLY_PAYLOAD"),  # length beyond payload
        (0x7E0, bytes([0x00, 0x03]), "READ_ONLY_PAYLOAD"),  # zero length
        (0x7E0, b"", "READ_ONLY_PAYLOAD"),
    ],
)
def test_policy_refuses_everything_else(arbitration_id: int, data: bytes, reason: str) -> None:
    # Unpadded on purpose: a declared length beyond the bytes on the wire must be refused.
    assert _policy().violation(_frame(arbitration_id, data), time.monotonic_ns()) == reason


def test_policy_refuses_extended_fd_and_expired() -> None:
    now = time.monotonic_ns()
    assert _policy().violation(_frame(0x18DAF100, bytes([0x01, 0x03]) * 4, extended=True), now) == "READ_ONLY_ID"
    assert _policy().violation(_frame(0x7E0, bytes([0x01, 0x03]) * 4, fd=True), now) == "READ_ONLY_ID"
    expired = ReadOnlyPolicy(expires_ns=now - 1)
    assert expired.violation(_frame(0x7E0, bytes([0x01, 0x03]) * 4), now) == "READ_ONLY_EXPIRED"


def test_obd_mode_04_is_critical() -> None:
    assert 0x04 in CRITICAL_UDS_SIDS


# ---------------------------------------------------------------------------
# Gateway integration
# ---------------------------------------------------------------------------


class _SpyBus(AbstractBus):
    def __init__(self) -> None:
        super().__init__(channel_id="spy", bitrate=500_000)
        self.sent: list[CanFrame] = []
        self.is_connected = True

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:
        self.sent.append(frame)

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        return None


def _gateway() -> tuple[TxSafetyGateway, _SpyBus]:
    bus = _SpyBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7DF, *range(0x7E0, 0x7E8)})
    return gateway, bus


def test_gateway_read_only_stage_blocks_clear_and_passes_reads() -> None:
    gateway, bus = _gateway()
    gateway.install_read_only_policy(_policy())
    gateway.validate_and_transmit(_frame(0x7E0, bytes([0x01, 0x03]).ljust(8, b"\x55")))
    assert len(bus.sent) == 1
    for data in (bytes([0x01, 0x04]), bytes([0x04, 0x14, 0xFF, 0xFF, 0xFF]), bytes([0x02, 0x10, 0x02])):
        with pytest.raises(SafetyError) as err:
            gateway.validate_and_transmit(_frame(0x7DF, data.ljust(8, b"\x55")), user_confirmed=True)
        assert err.value.code == "READ_ONLY_VIOLATION"
    assert len(bus.sent) == 1


def test_gateway_drops_policy_when_tx_authority_ends() -> None:
    gateway, _ = _gateway()
    gateway.install_read_only_policy(_policy())
    gateway._on_safety_state_changed(SafetyState.ARMED_TX, SafetyState.ARMED_TX, "still armed")
    assert gateway.read_only_policy is not None
    gateway._on_safety_state_changed(SafetyState.ARMED_TX, SafetyState.PASSIVE, "disarm")
    assert gateway.read_only_policy is None


# ---------------------------------------------------------------------------
# App read-only session
# ---------------------------------------------------------------------------


def _app() -> Any:
    from src.ui.desktop_app import UniversalCanDesktopApp

    return UniversalCanDesktopApp(channel="vcan0", bitrate=500000)


def test_app_read_only_session_is_narrow_and_always_closed() -> None:
    app = _app()
    app.bus.connect()  # virtual python-can bus; run() normally connects it
    opened = app.open_read_only_session(reason="test")
    assert opened["success"] is True, opened
    assert app.supervisor.is_tx_permitted and app.gateway.read_only_policy is not None
    with pytest.raises(SafetyError):
        app.gateway.validate_and_transmit(_frame(0x7DF, bytes([0x01, 0x04]).ljust(8, b"\x55")))
    closed = app.close_read_only_session()
    assert closed["success"] is True
    assert not app.supervisor.is_tx_permitted and app.gateway.read_only_policy is None


def test_app_read_only_session_refusals() -> None:
    app = _app()
    app._set_ui_state(_is_simulating=True)
    assert app.open_read_only_session()["error_code"] == "SIMULATOR_ACTIVE"
    app._set_ui_state(_is_simulating=False)
    app.gateway.update_physical_speed(40.0)
    assert app.open_read_only_session()["error_code"] == "VEHICLE_MOVING"
    assert app.gateway.read_only_policy is None and not app.supervisor.is_tx_permitted


def test_full_arm_is_still_refused_with_unknown_speed() -> None:
    app = _app()
    assert app.arm_tx(reason="t")["success"] is False  # speed stale → the full arm stays closed


# ---------------------------------------------------------------------------
# OBD reader over the simulated ECU (real poller + ISO-TP + policy)
# ---------------------------------------------------------------------------


def test_reader_reads_stored_pending_and_multiframe() -> None:
    ecu = SimulatedObdEcu(stored=("P0301", "P0420", "P0171", "P0300"), pending=("P0128",))
    outcome = asyncio.run(read_obd_fault_codes(ecu, ecu.subscribe, channel_id="sim_obd", timeout_s=0.2))
    assert outcome.status == "ok" and outcome.answered_ecus == ["Motor kontrol ünitesi"]
    assert [(c.code, c.kind) for c in outcome.codes] == [
        ("P0301", "stored"), ("P0420", "stored"), ("P0171", "stored"), ("P0300", "stored"), ("P0128", "pending")]
    assert any(bytes(f.data)[0] == 0x30 and f.arbitration_id == 0x7E0 for f in ecu.sent)  # FC went physical


def test_reader_reports_refusal_instead_of_raising() -> None:
    class Refusing:
        async def send(self, frame: CanFrame, **_: Any) -> None:
            raise SafetyError("blocked", code="READ_ONLY_VIOLATION")

    outcome = asyncio.run(read_obd_fault_codes(Refusing(), lambda cb: (lambda: None), channel_id="x",
                                               timeout_s=0.05))
    assert outcome.status == "refused"


def test_reader_silent_car_is_no_answer() -> None:
    outcome = asyncio.run(read_obd_fault_codes(SimulatedObdEcu(), lambda cb: (lambda: None), channel_id="x",
                                               timeout_s=0.05))
    assert outcome.status == "no_answer" and outcome.codes == []


def test_simulated_ecu_enforces_policy() -> None:
    with pytest.raises(SafetyError):
        SimulatedObdEcu().send_sync(_frame(0x7DF, bytes([0x01, 0x04]).ljust(8, b"\x55")))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_events_from_obd_and_dm1() -> None:
    events = obd_codes_to_events([ObdCode("P0420", "stored", "", ""), ObdCode("P0420", "stored", "", ""),
                                  ObdCode("P0171", "pending", "", "")], 1)
    assert [(e.code, e.status) for e in events] == [("P0420", "ACTIVE"), ("P0171", "PENDING")]
    dm1 = dm1_to_events([SimpleNamespace(spn=3251, fmi=0), SimpleNamespace(spn=0, fmi=0),
                         SimpleNamespace(spn=999999, fmi=31)], 1)
    assert [e.code for e in dm1] == ["SPN 3251 FMI 0", "SPN 999999 FMI 31"]
    assert dm1[1].severity == Severity.UNKNOWN  # unknown stays visible, never invented


def test_unique_session_collapses_repeated_broadcasts() -> None:
    session = VehicleSession(session_id="s", started_at_ns=1, domain=DiagnosticDomain.HEAVY_DUTY)
    for _ in range(5):
        session.events.append(DiagnosticEvent(timestamp_ns=1, code="SPN 3251 FMI 0",
                                              domain=DiagnosticDomain.HEAVY_DUTY, severity=Severity.MEDIUM,
                                              status="ACTIVE"))
    assert len(_unique_session(session).events) == 1


# ---------------------------------------------------------------------------
# Mechanic result card
# ---------------------------------------------------------------------------


def _ctx(**kw: Any) -> ScanContext:
    return ScanContext(vehicle_label="Test", vehicle_type=kw.pop("vehicle_type", "truck"), **kw)


def test_turkish_detection_is_not_fooled_by_dotted_i() -> None:
    assert not _is_turkish("Failed regeneration attempts")
    assert not _is_turkish("Cylinder #1 Misfire")
    assert _is_turkish("Kablo bağlantılarını kontrol edin.")


def test_known_code_card_order_and_honesty() -> None:
    evidence = [explain_code("SPN 3251 FMI 0")]
    analysis = {
        "user_card": {"headline_tr": "Partikül filtresi tıkanıyor olabilir", "summary_tr": "Kesin olmayan özet.",
                      "risk_level": "YELLOW", "source_badges": ["Yerel bilgi tabanı"]},
        "report": {"likely_causes": ["DPF aşırı kurum yükü veya fark basınç sensörü arızası."],
                   "troubleshooting_steps": [{"action": "DPF hortumlarını kontrol edin.", "component": "DPF",
                                              "difficulty": "Kolay (Görsel)"}]},
        "gate": {"gaps": ["kayıt süresi 0.0 sn (min 60 sn)"]},
    }
    result = compose_mechanic_result(analysis, evidence, _ctx())
    assert result["urgency_tr"] == "Bu hafta"
    assert all(_is_turkish(c["text_tr"]) for c in result["causes"]) and len(result["causes"]) <= 3
    assert all(c["why_tr"].startswith("SPN 3251 FMI 0") or c["why_tr"].startswith("Analiz")
               for c in result["causes"])
    assert result["steps"][0]["difficulty_tr"].lower().startswith("kolay")
    assert "Emin olmak için motor çalışırken en az 60 sn daha kayıt alın." in result["missing_tr"]
    assert "J1939 SPN veritabanı" in result["sources_tr"]
    text = " ".join([result["summary_tr"], *[c["text_tr"] for c in result["causes"]]]).lower()
    assert "kesin" not in text


def test_red_risk_and_high_voltage_warning_come_first() -> None:
    analysis = {"user_card": {"headline_tr": "Yağ basıncı düşük olabilir", "risk_level": "RED"}, "report": {}}
    result = compose_mechanic_result(analysis, [explain_code("SPN 100 FMI 1")], _ctx(high_voltage=True,
                                     battery_message_tr="Akü zayıf (22.9 V)."))
    assert result["safety_tr"][0].startswith("Yüksek voltajlı araç")
    assert any(line.startswith("Aracı sürmeyin") for line in result["safety_tr"])
    assert result["urgency_tr"] == "Hemen"


def test_unknown_code_is_not_guessed() -> None:
    unknown = CodeEvidence(code="P1ABC", kind="stored", known=False)
    result = compose_mechanic_result({"user_card": {"risk_level": "GRAY"}, "report": {
        "likely_causes": ["Uydurma neden"]}}, [unknown], _ctx(vehicle_type="car", read_status="ok"))
    assert result["headline_tr"] == "Bu kodu tanımıyoruz"
    assert "Uydurmamak için yorum yapmıyoruz. Kod: P1ABC." in result["summary_tr"]
    assert result["causes"] == [] and result["steps"] == []


def test_no_codes_is_honest_and_listen_only_car_says_why() -> None:
    result = compose_mechanic_result({"user_card": {"risk_level": "GREEN"}, "report": {}}, [],
                                     _ctx(vehicle_type="car", read_status="declined", simulator=True))
    assert result["headline_tr"] == "Kayıtlı arıza kodu bulunamadı"
    assert "anlamına gelmez" in result["summary_tr"]
    assert any("okuma izni verilmedi" in line for line in result["missing_tr"])
    assert any("simülatör" in line for line in result["missing_tr"])


def test_customer_report() -> None:
    result = compose_mechanic_result({"user_card": {"headline_tr": "Motor teklemesi olabilir",
                                                    "summary_tr": "1. silindirde düzensiz ateşleme olabilir.",
                                                    "risk_level": "YELLOW"}, "report": {}},
                                     [explain_code("P0301", "stored")], _ctx(vehicle_type="car"))
    text = customer_report_text(result, workshop="Usta Oto")
    assert text.startswith("ARAÇ KONTROL RAPORU\nUsta Oto")
    assert "Aciliyet: Bu hafta" in text and "(P0301)" in text and text.endswith(REPORT_DISCLAIMER_TR)
    assert "kesin" not in text.lower()


# ---------------------------------------------------------------------------
# Scan runner (simulator end to end, analysis stubbed)
# ---------------------------------------------------------------------------


def _analysis_stub(session: VehicleSession) -> dict[str, Any]:
    codes = [e.code for e in session.events]
    return {"user_card": {"headline_tr": "Test", "summary_tr": "Test özeti", "risk_level": "YELLOW" if codes else
                          "GRAY"}, "report": {"likely_causes": [], "troubleshooting_steps": []}, "gate": {"gaps": []},
            "_codes": codes}


@pytest.mark.parametrize(
    ("vehicle_type", "allow_read", "expected_codes", "read_status"),
    [
        ("truck", False, ["SPN 3251 FMI 0"], None),
        ("car", True, ["P0301", "P0420", "P0171"], "ok"),
        ("car", False, [], "declined"),
        ("boat", False, [], None),
    ],
)
def test_simulator_scan_end_to_end(vehicle_type: str, allow_read: bool, expected_codes: list[str],
                                   read_status: str | None) -> None:
    runner = ScanRunner(lambda req: SimulatorScanBackend(req.vehicle_type, "ok", _analysis_stub, frame_sleep=False))
    runner.start(ScanRequest(vehicle_label="Sim", vehicle_type=vehicle_type, simulator=True,
                             allow_read=allow_read, listen_seconds=0.2))
    runner.wait()
    status = runner.status()
    assert status["state"] == "done" and status["step"] == "done"
    codes = [c["code"] for c in status["result"]["technical"]["codes"]]
    assert codes == expected_codes
    assert status["report_text"].endswith(REPORT_DISCLAIMER_TR)
    if vehicle_type == "car" and not allow_read:
        assert any("okuma izni verilmedi" in m for m in status["result"]["missing_tr"])


def test_scan_runner_rejects_parallel_scans_and_reports_failures() -> None:
    class Boom:
        def new_session(self, domain: DiagnosticDomain) -> VehicleSession:
            raise RuntimeError("x")

    runner = ScanRunner(lambda req: Boom())  # type: ignore[arg-type,return-value]
    runner.start(ScanRequest(vehicle_label="x", vehicle_type="truck"))
    runner.wait()
    assert runner.status()["step"] == "failed"


def test_ignition_off_car_read_is_no_answer() -> None:
    backend = SimulatorScanBackend("car", "ignition_off", _analysis_stub, frame_sleep=False)
    assert backend.read_obd() == ObdReadOutcome(status="no_answer")


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


def test_bridge_scan_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.ui.desktop_app as da
    from src.engine.connection import adapters as ad
    from src.engine.connection.wizard import CommittedConnection
    from src.ui.mechanic_prefs import MechanicPrefsStore

    monkeypatch.setattr(da, "_app_data_root", lambda: tmp_path)
    runner = ScanRunner(lambda req: SimulatorScanBackend(req.vehicle_type, req.scenario, _analysis_stub,
                                                         frame_sleep=False))
    app = SimpleNamespace(mechanic_prefs=MechanicPrefsStore(tmp_path / "p.json"), scan_runner=runner,
                          connection_wizard=SimpleNamespace(connection=None))
    bridge = da.DesktopApiBridge(app)  # type: ignore[arg-type]
    assert bridge.scan_start()["error_code"] == "NOT_CONNECTED"
    app.connection_wizard.connection = CommittedConnection(ad.SIMULATOR, "car", 500_000, True, "ok", {})
    assert bridge.scan_start()["error_code"] == "NO_VEHICLE_SELECTED"
    app.mechanic_prefs.update(vehicle_profile_id="car_generic")
    started = bridge.scan_start(allow_read=True)
    assert started["success"] and started["reading"] is True
    runner.wait()
    assert bridge.scan_status()["result"]["technical"]["codes"][0]["code"] == "P0301"
    assert bridge.scan_save_report("x" * 81)["error_code"] == "INVALID_WORKSHOP"
    saved = bridge.scan_save_report("Usta Oto")
    assert saved["success"] and Path(saved["path"]).read_text(encoding="utf-8").startswith("<!doctype html>")
    assert bridge.scan_cancel() == {"success": True}

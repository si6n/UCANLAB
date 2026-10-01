"""Regression tests for the 2026-10-01 full audit (docs/audit/AUDIT_REPORT.md).

Each test names the finding it pins. They exercise the real objects (no mock
standing in for the code under test) so a regression shows up as a failure,
not as a green test over a stub.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import pytest

from src.core.errors import SafetyError
from src.engine.pipeline.reassembly_pipeline import ReassembledMessage
from src.hal.virtual import UdsServerEcu, VirtualBus
from src.protocols.j1939.diagnostics import J1939DiagnosticService
from src.protocols.obd.models import ObdPidResult
from src.protocols.uds.client import UdsClient
from src.safety.estop import EmergencyStopSystem
from src.safety.exceptions import DualConfirmationRequiredError
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor


def _app() -> Any:
    from src.ui.desktop_app import UniversalCanDesktopApp

    return UniversalCanDesktopApp(channel="vcan0", bitrate=500000)


# ---------------------------------------------------------------------------
# AUD-01: a reassembled J1939 VIN (PGN 65260) crashed the handler with
# AttributeError ('ReassembledMessage' has no attribute 'bytes'), so the VIN
# was never detected and vehicle-identity checks ran without it.
# ---------------------------------------------------------------------------


def test_aud01_reassembled_vin_is_detected_and_logged_masked(caplog: pytest.LogCaptureFixture) -> None:
    app = _app()
    vin = "1M8GDM9AXKP042788"
    msg = ReassembledMessage(
        protocol="J1939",
        data=(vin + "*").encode("ascii"),
        timestamp_ns=1,
        channel_id="vcan0",
        pgn=65260,
    )

    app._handle_reassembled_message(msg)

    assert app._detected_vin == vin
    # The full VIN must never reach the log (project rule: masked VIN only).
    assert vin not in caplog.text


# ---------------------------------------------------------------------------
# AUD-02: start_obd_polling referenced a non-existent `self.safe_bus`, raised
# AttributeError on every call and leaked the router subscription it had just
# registered.
# ---------------------------------------------------------------------------


def test_aud02_obd_polling_starts_through_the_gateway_and_stops_cleanly() -> None:
    app = _app()
    try:
        result = app.start_obd_polling(pids=[0x0C], rate_hz=1.0)
        assert result["success"] is True, result
        # TX authority is the safety gateway (single choke-point), never the bus.
        assert app.obd_poller is not None
        assert app.obd_poller.tx_port is app.gateway
    finally:
        stopped = app.stop_obd_polling()
    assert stopped["success"] is True
    assert app.obd_poller is None
    assert app._obd_router_sub_id is None
    assert app._obd_poller_rx_sub is None


def test_aud02_failed_obd_start_releases_the_router_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    before = len(app.router._subscriptions) if hasattr(app.router, "_subscriptions") else None

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("poller construction failed")

    monkeypatch.setattr("src.ui.desktop_app.ActiveDiagnosticPoller", _boom)
    result = app.start_obd_polling(pids=[0x0C], rate_hz=1.0)

    assert result["success"] is False
    assert app.obd_poller is None
    assert app._obd_router_sub_id is None
    if before is not None:
        assert len(app.router._subscriptions) == before


# ---------------------------------------------------------------------------
# AUD-03: _handle_obd_pid_result read `result.success`, a field ObdPidResult
# does not have, so every decoded PID raised and live RPM / speed / coolant
# telemetry was never recorded.
# ---------------------------------------------------------------------------


def test_aud03_valid_pid_result_updates_live_telemetry() -> None:
    app = _app()
    app._handle_obd_pid_result(
        ObdPidResult(pid=0x0C, name="ENGINE_RPM", raw_bytes=b"\x0f\xa0", value=1000.0, unit="rpm")
    )
    assert app._current_rpm == 1000.0


def test_aud03_invalid_pid_result_is_ignored() -> None:
    app = _app()
    before = app._current_rpm
    app._handle_obd_pid_result(
        ObdPidResult(pid=0x0C, name="ENGINE_RPM", raw_bytes=b"", value=9999.0, unit="rpm", is_valid=False)
    )
    assert app._current_rpm == before


# ---------------------------------------------------------------------------
# AUD-04: a standard single-frame DM1 is 8 bytes (lamp, flash lamp, one 4-byte
# DTC, two 0xFF fill bytes, SAE J1939-73 §5.7.1). The 0xFF fill was counted as a
# ragged DTC tail, so every normal DM1 was flagged MALFORMED and logged a warning.
# ---------------------------------------------------------------------------


def test_aud04_single_frame_dm1_with_ff_fill_is_not_malformed() -> None:
    # SPN 100 (0x64), FMI 1, OC 3 -> 64 00 01 03, then 0xFF 0xFF fill.
    data = bytes([0x04, 0xFF, 0x64, 0x00, 0x01, 0x03, 0xFF, 0xFF])
    msg = J1939DiagnosticService.parse_dm1_or_dm2(data, pgn=65226, source_address=0x00)
    assert msg is not None
    assert msg.malformed is False
    assert [(d.spn, d.fmi, d.occurrence_count) for d in msg.dtcs] == [(100, 1, 3)]


def test_aud04_no_fault_dm1_is_not_malformed() -> None:
    data = bytes([0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF])
    msg = J1939DiagnosticService.parse_dm1_or_dm2(data, pgn=65226)
    assert msg is not None
    assert msg.malformed is False
    assert msg.dtcs == []


def test_aud04_a_real_ragged_tail_is_still_malformed() -> None:
    # Two meaningful bytes after a full DTC record are not 0xFF fill.
    data = bytes([0x04, 0xFF, 0x64, 0x00, 0x01, 0x03, 0x12, 0x34])
    msg = J1939DiagnosticService.parse_dm1_or_dm2(data, pgn=65226)
    assert msg is not None
    assert msg.malformed is True


# ---------------------------------------------------------------------------
# AUD-05..07: confirmation tokens on a production-wired gateway.
#
# The desktop app always configures a gateway confirmation secret, so Stage 5
# accepts only HMAC tokens. Three defects made every confirmed UDS action and
# every real ECU flash fail there, while unit tests passed because they used
# stub issuers / stub gateways:
#   AUD-05  a context-bound token was minted without the frame payload hash
#           -> TypeError (bytes(None)); flash steps naming a VIN and the
#           clear-DTC / session / reset actions all crashed.
#   AUD-06  the UDS client re-presented the single-use token on every
#           Consecutive Frame -> "already consumed" on the first CF, so no
#           multi-frame critical request (0x36 TransferData) could complete.
#   AUD-07  the flasher polled RoutineControl results (0x31 0x03) with no
#           token; the gateway classifies SID 0x31 as critical and refused it.
# These tests run the real gateway, real UDS client and the simulated ECU.
# ---------------------------------------------------------------------------

_TEST_VIN = "VF1TESTVIN1234567"


class _EcuBus(VirtualBus):
    """Virtual bus with a simulated UDS ECU listening on the wire."""

    server: UdsServerEcu | None = None

    def send(self, frame: Any) -> None:
        super().send(frame)
        if self.server is not None:
            self.server.process_frame(frame)


def _production_rig() -> tuple[_EcuBus, TxSafetyGateway, UdsClient]:
    estop = EmergencyStopSystem(reset_secret=os.urandom(32))
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE, estop=estop)
    supervisor.arm_tx("audit test arm")
    bus = _EcuBus(channel_id="vcan0")
    bus.connect()
    bus.server = UdsServerEcu(bus=bus, rx_id=0x7E0, tx_id=0x7E8, channel_id="vcan0", vin=_TEST_VIN)
    gateway = TxSafetyGateway(
        bus=bus,
        estop=estop,
        supervisor=supervisor,
        whitelist_ids={0x7E0},
        confirmation_secret=os.urandom(32),
    )
    gateway.update_vehicle_speed(0.0, source="physical")
    client = UdsClient(bus=bus, tx_port=gateway, tx_id=0x7E0, rx_id=0x7E8, channel_id="vcan0")
    return bus, gateway, client


def _bound_minter(gateway: TxSafetyGateway, context: str) -> Any:
    def _mint(frame_data: bytes) -> bytes:
        return gateway.issue_confirmation_token(
            0x7E0,
            payload_hash=TxSafetyGateway.confirmation_payload_hash(frame_data),
            context=context,
        )

    return _mint


def test_aud05_context_without_payload_hash_is_refused_explicitly() -> None:
    _bus, gateway, _client = _production_rig()
    with pytest.raises(SafetyError) as exc:
        gateway.issue_confirmation_token(0x7E0, context="uds_clear_dtc:a1")
    assert exc.value.code == "CONFIRMATION_PAYLOAD_UNBOUND"


def test_aud05_payload_bound_minter_authorizes_exactly_its_action() -> None:
    bus, gateway, client = _production_rig()
    assert bus.server is not None
    assert bus.server.dtcs

    ctx = "uds_clear_dtc:a1"
    resp = client.clear_dtc(
        0xFFFFFF,
        user_confirmed=True,
        confirmation_token=_bound_minter(gateway, ctx),
        confirmation_context=ctx,
    )
    assert resp.is_positive
    assert bus.server.dtcs == []

    # A token minted for one action/frame cannot authorize a different one.
    stolen = _bound_minter(gateway, ctx)(b"\x04\x14\xff\xff\xff\xaa\xaa\xaa")
    with pytest.raises(DualConfirmationRequiredError):
        client.ecu_reset(
            reset_type=0x01,
            user_confirmed=True,
            confirmation_token=stolen,
            confirmation_context=ctx,
        )


def test_aud05_desktop_confirm_token_for_context_returns_a_payload_minter() -> None:
    app = _app()
    if not app.gateway.has_confirmation_secret():
        pytest.skip("this build wires no gateway confirmation secret")
    plain = app._confirm_token_for(0x7E0)
    assert isinstance(plain, bytes)
    minter = app._confirm_token_for(0x7E0, "uds_clear_dtc:a1")
    assert callable(minter)
    token = minter(b"\x04\x14\xff\xff\xff\xaa\xaa\xaa")
    assert isinstance(token, bytes)
    assert len(token) == TxSafetyGateway._CONFIRM_BIND_LEN + 32


def test_aud06_multi_frame_critical_request_completes_with_one_token() -> None:
    bus, gateway, client = _production_rig()
    assert bus.server is not None
    seed_key = bytes(b ^ 0xFF for b in bus.server.seed_challenge)
    ctx = "flash:test:0x8000000"

    assert client.change_session(
        0x02, user_confirmed=True, confirmation_token=_bound_minter(gateway, ctx), confirmation_context=ctx
    ).is_positive
    assert client.security_access_request_seed(
        level=0x01, user_confirmed=True, confirmation_token=_bound_minter(gateway, ctx), confirmation_context=ctx
    ).is_positive
    assert client.security_access_send_key(
        level=0x01,  # the seed level; the client emits the key sub-function 0x02
        key=seed_key,
        user_confirmed=True,
        confirmation_token=_bound_minter(gateway, ctx),
        confirmation_context=ctx,
    ).is_positive
    assert client.request_download(
        memory_address=0x08000000,
        memory_size=64,
        user_confirmed=True,
        confirmation_token=_bound_minter(gateway, ctx),
        confirmation_context=ctx,
    ).is_positive

    block = bytes(range(64))  # 0x36 + BSC + 64 bytes -> FF + 9 CFs
    resp = client.transfer_data(
        block_sequence=1,
        data=block,
        is_critical_command=True,
        user_confirmed=True,
        confirmation_token=_bound_minter(gateway, ctx),
        confirmation_context=ctx,
    )
    assert resp.is_positive
    assert bytes(bus.server.downloaded_data) == block


def test_aud07_full_flash_completes_on_a_production_gateway() -> None:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from src.protocols.uds.flasher import EcuFlashingEngine, FlashingConfig

    bus, gateway, client = _production_rig()
    assert bus.server is not None
    logs: list[str] = []
    engine = EcuFlashingEngine(
        uds_client=client,
        gateway=gateway,
        on_log=lambda msg, _lvl="info": logs.append(msg),
        confirmation_token_factory=gateway.create_confirmation_issuer(),
    )
    signer = ed25519.Ed25519PrivateKey.generate()
    image = bytes(range(256)) * 2
    config = FlashingConfig(
        memory_address=0x08000000,
        data=image,
        block_size=256,
        security_key=bytes(b ^ 0xFF for b in bus.server.seed_challenge),
        expected_vin=_TEST_VIN,
        firmware_signature=signer.sign(image),
        trusted_pubkey=signer.public_key(),
        user_confirmed=True,
    )

    # Keep the speed interlock fed like the live telemetry loop does.
    stop = threading.Event()

    def _feed() -> None:
        while not stop.wait(0.1):
            gateway.update_vehicle_speed(0.0, source="physical")

    feeder = threading.Thread(target=_feed, daemon=True)
    feeder.start()
    try:
        ok = engine.execute_flash(config)
    finally:
        stop.set()
        feeder.join(timeout=1.0)

    assert ok is True, logs[-5:]
    assert bytes(bus.server.downloaded_data) == image


# ---------------------------------------------------------------------------
# AUD-08: decoders hand _record_signal_sample enum text ("neutral") or None
# ("not available"); math.isfinite() then raised TypeError, which escaped the
# RX path. The telemetry loop caught it per TICK, so one NMEA 2000 PGN 127493
# frame dropped every frame of that tick, including the black-box batch.
# ---------------------------------------------------------------------------


def _n2k_frame(pgn: int, data: bytes, sa: int = 0x10) -> Any:
    from src.core.models.can_frame import CanFrame

    arbitration_id = (2 << 26) | (pgn << 8) | sa
    return CanFrame.create(channel_id="vcan0", arbitration_id=arbitration_id, data=data, is_extended=True)


def test_aud08_n2k_transmission_gear_frame_does_not_raise() -> None:
    app = _app()
    # PGN 127493: instance 0, gear "forward" (code 1), oil pressure/temp N/A.
    frame = _n2k_frame(127493, bytes([0x00, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
    app._ingest_live_frame(frame)  # must not raise


def test_aud08_enum_and_missing_values_are_recorded_safely() -> None:
    app = _app()
    app._record_signal_sample("TransmissionGear_0", 1, "forward", "enum")
    app._record_signal_sample("FluidLevel_0_0", 0xFFFF, None, "percent")


# ---------------------------------------------------------------------------
# AUD-11: the version string was hard-coded in six places (pyproject, frontend
# package.json, Inno Setup script, launcher, update manager, license flow).
# src/version.py is the single source; the packaging files must agree with it.
# ---------------------------------------------------------------------------


def test_aud11_version_is_consistent_across_packaging_files() -> None:
    import json
    import re
    import tomllib
    from pathlib import Path

    from src.version import __version__

    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((root / "src/ui/frontend/package.json").read_text(encoding="utf-8"))
    iss = (root / "scripts/installer.iss").read_text(encoding="utf-8")
    iss_version = re.search(r'#define MyAppVersion "([^"]+)"', iss)

    assert pyproject["project"]["version"] == __version__
    assert package["version"] == __version__
    assert iss_version is not None and iss_version.group(1) == __version__


def test_aud11_runtime_defaults_use_the_single_version() -> None:
    import inspect

    from src.launcher.app import UniversalCanLauncher
    from src.launcher.updater import UpdateManager
    from src.security.cloud.license_flow import LicenseFlow
    from src.version import __version__

    for fn, param in (
        (UniversalCanLauncher.__init__, "current_version"),
        (UpdateManager.__init__, "current_version"),
        (LicenseFlow.__init__, "app_version"),
    ):
        assert inspect.signature(fn).parameters[param].default == __version__


def test_aud05_routine_start_with_payload_bound_token_passes_the_gateway() -> None:
    bus, gateway, client = _production_rig()
    ctx = "uds_routine:a1"
    resp = client.start_routine(
        0x0202,
        user_confirmed=True,
        confirmation_token=_bound_minter(gateway, ctx),
        confirmation_context=ctx,
    )
    assert resp.is_positive


def test_aud05_desktop_routine_action_presents_a_confirmation_token() -> None:
    import inspect

    from src.ui.desktop_app import UniversalCanDesktopApp

    source = inspect.getsource(UniversalCanDesktopApp)
    start = source.index('elif action_type in ("uds_routine"')
    branch = source[start : source.index("# UDS 0x11 ECU Reset", start)]
    assert "confirmation_token=self._confirm_token_for(client.tx_id, _ctx)" in branch
    assert "confirmation_context=_ctx" in branch


# ---------------------------------------------------------------------------
# AUD-12: user-facing UDS result messages carried double-encoded UTF-8
# ("âŒ" instead of "❌"), so the operator saw garbage in front of every
# ECU answer. Source text must be clean UTF-8.
# ---------------------------------------------------------------------------


def test_aud12_no_double_encoded_utf8_in_python_sources() -> None:
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src"
    # A UTF-8 lead byte read as cp1252 ("â", "Ã", "Ä", ...) followed by a
    # continuation-byte character. junk_content_signatures.py is the mojibake
    # DETECTOR and quotes the artefact in its comments on purpose.
    mojibake = re.compile("[Â-ô][\u0080-¿ŒœŠšŸŽžƒ–—‘-„†-•…‰‹›€™]")
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "junk_content_signatures.py":
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if mojibake.search(line):
                offenders.append(f"{path.relative_to(root)}:{no}")
    assert offenders == []

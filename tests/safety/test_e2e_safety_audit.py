"""Comprehensive ASIL-B/D & ISO 26262 End-to-End Safety Audit for TxSafetyGateway."""
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.engine.ai.diagnostic_copilot import (
    get_j1939_spn_database,
    get_mode06_database,
    get_uds_did_database,
    load_external_dtc_database,
)
from src.hal.replay.safety_filter import ReplaySafetyFilter
from src.hal.virtual import VirtualBus
from src.protocols.uds.flasher import EcuFlashingEngine, FlashingConfig, FlashingStep
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.exceptions import (
    DualConfirmationRequiredError,
    FrameSanityError,
    SpeedDataStaleError,
    SpeedInterlockError,
    WhitelistViolationError,
)
from src.safety.gateway import TxSafetyGateway

# T57-D / F-1: the flasher verifies an Ed25519 firmware signature by default.
_FW_KEY = ed25519.Ed25519PrivateKey.generate()
_FW_PUB = _FW_KEY.public_key()


def test_audit_database_integrity_and_scale():
    load_external_dtc_database()
    from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE
    assert len(EXPERT_KNOWLEDGE_BASE) >= 14188
    spn_db = get_j1939_spn_database()
    assert len(spn_db.get("spns", spn_db)) == 3937
    did_db = get_uds_did_database()
    assert len(did_db.get("dids", did_db)) == 68
    m06_db = get_mode06_database()
    assert len(m06_db.get("monitors", m06_db)) == 38

def test_audit_gateway_6_stage_chokepoint_fail_closed():
    bus = VirtualBus(channel_id="audit_vbus_1")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0, 0x18DA00F1})

    # Stage 1: Frame Sanity check
    with pytest.raises(FrameSanityError):
        gateway.validate_and_transmit("invalid_frame_object")  # type: ignore

    # Stage 2: E-Stop check
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Audit test")
    valid_frame = CanFrame.create(channel_id="audit_vbus_1", arbitration_id=0x7E0, data=b"\x02\x10\x01\x00\x00\x00\x00\x00")
    with pytest.raises(SafetyError, match="Emergency Stop"):
        gateway.validate_and_transmit(valid_frame)
    assert len(bus.sent_frames) == 0

    # Reset estop with proper challenge token flow
    token = estop.create_reset_token()
    estop.reset(token)
    assert estop.is_engaged is False

    # Stage 3: Whitelist check (fails closed on every miss; trips E-Stop after threshold of 5)
    unauthorized_frame = CanFrame.create(channel_id="audit_vbus_1", arbitration_id=0x123, data=b"\x01\x02")
    for _ in range(gateway.WHITELIST_ESTOP_AFTER):
        with pytest.raises(WhitelistViolationError):
            gateway.validate_and_transmit(unauthorized_frame)
    # 5 consecutive misses must trip E-Stop fail-closed
    assert estop.is_engaged is True

    # Generate fresh token AFTER trigger and reset
    token2 = estop.create_reset_token()
    estop.reset(token2)
    assert estop.is_engaged is False

    # Stage 4: Speed Interlock (stale speed fails closed and trips E-Stop)
    critical_routine_frame = CanFrame.create(channel_id="audit_vbus_1", arbitration_id=0x7E0, data=b"\x31\x01\x02\x01")
    with pytest.raises(SpeedDataStaleError):
        gateway.validate_and_transmit(critical_routine_frame, is_critical_command=True, user_confirmed=True)
    assert estop.is_engaged is True
    estop.reset(estop.create_reset_token())

    # Moving speed (>0.0) fails closed and trips E-Stop
    gateway.update_physical_speed(12.5)
    with pytest.raises(SpeedInterlockError):
        gateway.validate_and_transmit(critical_routine_frame, is_critical_command=True, user_confirmed=True)
    assert estop.is_engaged is True
    estop.reset(estop.create_reset_token())

    # NaN speed fails closed
    gateway.update_physical_speed(float("nan"))
    with pytest.raises(SpeedDataStaleError):
        gateway.validate_and_transmit(critical_routine_frame, is_critical_command=True, user_confirmed=True)
    assert estop.is_engaged is True
    estop.reset(estop.create_reset_token())

    # Stage 5: Dual Confirmation check (unconfirmed critical command fails closed without tripping E-Stop)
    gateway.update_physical_speed(0.0)
    with pytest.raises(DualConfirmationRequiredError):
        gateway.validate_and_transmit(critical_routine_frame, is_critical_command=True, user_confirmed=False)
    assert estop.is_engaged is False

    # When all 6 stages pass (Stationary 0.0 km/h, Whitelisted, Valid Frame, Not Estop, Confirmed)
    assert gateway.validate_and_transmit(critical_routine_frame, is_critical_command=True, user_confirmed=True) is True
    assert len(bus.sent_frames) == 1

    bus.disconnect()

def test_audit_replay_safety_filter_isotp_and_ecu_spoofing():
    filt = ReplaySafetyFilter(block_diagnostic_write=True, block_transport_tunneling=True)

    # 1. Block prohibited UDS SID in Single Frame (0x31 RoutineControl)
    sf_prohibited = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x04\x31\x01\x02\x01\x00\x00\x00")
    safe, reason = filt.is_frame_safe(sf_prohibited)
    assert safe is False
    assert "0x31" in reason

    # 2. Block prohibited First Frame (0x36 TransferData) and subsequent Consecutive Frames
    ff_prohibited = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x10\x14\x36\x01\xAA\xBB\xCC\xDD")
    safe_ff, reason_ff = filt.is_frame_safe(ff_prohibited)
    assert safe_ff is False
    assert "0x36" in reason_ff

    cf_subsequent = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x21\xEE\xFF\x11\x22\x33\x44\x55")
    safe_cf, reason_cf = filt.is_frame_safe(cf_subsequent)
    assert safe_cf is False
    assert "BLOCKED_TP_TUNNEL" in reason_cf or "PROHIBITED_ISO_TP_SESSION_SID" in reason_cf

    # 3. Block ECU Response ID Spoofing (0x7E8..0x7EF)
    ecu_resp = CanFrame.create(channel_id="c0", arbitration_id=0x7E8, data=b"\x02\x67\x01\x00\x00\x00\x00\x00")
    safe_resp, reason_resp = filt.is_frame_safe(ecu_resp)
    assert safe_resp is False
    assert "BLOCKED_ECU_RESPONSE_SPOOF" in reason_resp

def test_audit_flasher_fail_closed_contract():
    class DummyClient:
        def __init__(self):
            self.calls = []
        def change_session(self, session_type: Any, user_confirmed: bool = False, confirmation_token: Any = None, **kw: Any):
            self.calls.append(("change_session", session_type, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": b""})()
        def security_access_request_seed(self, level: int, user_confirmed: bool = False, **kw: Any):
            self.calls.append(("security_access_request_seed", level, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": bytes([level, 0x11, 0x22, 0x33, 0x44])})()
        def security_access_send_key(self, level: int, key: bytes, user_confirmed: bool = False, **kw: Any):
            self.calls.append(("security_access_send_key", level, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": bytes([level])})()
        def request_download(self, memory_address: int, memory_size: int, user_confirmed: bool = False, confirmation_token: Any = None, **kw: Any):
            self.calls.append(("request_download", memory_address, memory_size, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": b"\x20\x01\x00"})()
        def transfer_data(self, block_sequence: int, data: bytes, is_critical_command: bool = True, user_confirmed: bool = False, confirmation_token: Any = None, **kw: Any):
            if not is_critical_command or not user_confirmed:
                raise SafetyError("Critical command contract violated")
            self.calls.append(("transfer_data", block_sequence, is_critical_command, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": bytes([block_sequence & 0xFF])})()
        def request_transfer_exit(self, is_critical_command: bool = True, user_confirmed: bool = False, confirmation_token: Any = None, **kw: Any):
            self.calls.append(("request_transfer_exit", is_critical_command, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": b""})()
        def start_routine(self, routine_id: int, options: bytes = b"", user_confirmed: bool = False, confirmation_token: Any = None, **kw: Any):
            self.calls.append(("start_routine", routine_id, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": b""})()
        def request_routine_results(self, routine_id: int, user_confirmed: bool = False, **kw: Any):
            self.calls.append(("request_routine_results", routine_id, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": b"\x03\x02\x02\x00"})()
        def ecu_reset(self, reset_type: int, user_confirmed: bool = False, confirmation_token: Any = None, **kw: Any):
            self.calls.append(("ecu_reset", reset_type, user_confirmed))
            return type("Resp", (), {"is_positive": True, "nrc": None, "nrc_description_tr": "", "data": b""})()

    bus = VirtualBus(channel_id="v_flash")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    client = DummyClient()
    flasher = EcuFlashingEngine(uds_client=client, gateway=gateway)

    cfg = FlashingConfig(
        memory_address=0x08000000,
        data=b"\x90" * 256,
        block_size=64,
        security_key=b"\x00" * 4,
        user_confirmed=True,
        # T57-D / F-1 + F-4: satisfy the mandatory signature/identity gates.
        require_target_identity=False,
        trusted_pubkey=_FW_PUB,
        firmware_signature=_FW_KEY.sign(b"\x90" * 256),
    )
    # Preflight with stationary physical speed
    gateway.update_physical_speed(0.0)
    flasher.execute_flash(cfg)
    assert flasher.current_step == FlashingStep.COMPLETED
    assert any(call[0] == "transfer_data" for call in client.calls)

    bus.disconnect()

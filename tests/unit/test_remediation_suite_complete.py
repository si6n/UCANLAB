"""Comprehensive verification test suite for remediation of all review findings.

Validates:
- E-Stop challenge request API & authority capability pattern
- ISO 14229-1 UDS ALFI nibble encoding (size=high, addr=low)
- UDS Client request-response service ID binding
- UDS Flasher BSC validation and empty data rejection
- J1939 Transport Protocol BAM broadcast check, CTS next_seq skip abort, and EndOfMsgACK validation
- ReplaySafetyFilter EDP-bit masking protection
- RollingDiskBuffer quarantine resilience
- Cloud license offline_until enforcement
- SafeMultiplexedBus critical flag propagation
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError, ProtocolError
from src.core.models.can_frame import CanFrame
from src.engine.buffer.rolling_disk import RollingDiskBuffer
from src.engine.router import FrameRouter
from src.hal.replay.safety_filter import ReplaySafetyFilter
from src.protocols.j1939.transport import (
    ABORT_REASON_UNEXPECTED_CONTROL,
    TP_CTRL_ACK,
    TP_CTRL_BAM,
    TP_CTRL_CTS,
    J1939TransportProtocol,
)
from src.protocols.uds.client import UdsClient
from src.protocols.uds.flasher import EcuFlashingEngine, FlashingConfig
from src.protocols.uds.services import DiagnosticSessionType, UdsServiceBuilder
from src.safety.estop import (
    DEFAULT_ESTOP_KEY_NAME,
    EmergencyStopSystem,
    EStopResetAuthority,
    EStopTriggerSource,
)
from src.safety.multiplexer import SafeMultiplexedBus
from src.safety.secret_provider import EphemeralSecretBackend
from src.security.cloud.license_flow import LicenseFlow

# ==============================================================================
# 1. E-Stop API & Capability Pattern Tests
# ==============================================================================

def test_estop_request_reset_challenge_flow() -> None:
    provider = EphemeralSecretBackend()
    provider.store_secret(DEFAULT_ESTOP_KEY_NAME, os.urandom(32))
    estop = EmergencyStopSystem(secret_provider=provider)

    # Not engaged => None
    assert estop.request_reset_challenge() is None

    # Engaged => returns valid challenge
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "Manual E-Stop")
    assert estop.is_engaged is True
    challenge1 = estop.request_reset_challenge()
    assert challenge1 is not None
    assert len(challenge1.nonce) == 16

    # Repeated trigger preserves active challenge nonce
    estop.trigger(EStopTriggerSource.KEEPALIVE_TIMEOUT, "Watchdog timeout")
    challenge2 = estop.request_reset_challenge()
    assert challenge2 is not None
    assert challenge2.nonce == challenge1.nonce

    # Authority mints token and clears
    auth = EStopResetAuthority(estop, secret_provider=provider)
    token = auth.mint_reset_token()
    assert token is not None
    estop.reset(token)
    assert estop.is_engaged is False


# ==============================================================================
# 2. UDS ALFI Nibble Format Tests (ISO 14229-1)
# ==============================================================================

def test_uds_alfi_iso14229_format() -> None:
    # 0x14: size width 1, address width 4
    req14 = UdsServiceBuilder.build_request_download(
        memory_address=0x12345678, memory_size=0x50, address_and_length_format_identifier=0x14
    )
    assert req14 == b"\x34\x00\x14\x12\x34\x56\x78\x50"

    # 0x41: size width 4, address width 1
    req41 = UdsServiceBuilder.build_request_download(
        memory_address=0x80, memory_size=0x01020304, address_and_length_format_identifier=0x41
    )
    assert req41 == b"\x34\x00\x41\x80\x01\x02\x03\x04"


# ==============================================================================
# 3. UDS Client Request-Response Service ID Binding
# ==============================================================================

def test_uds_client_service_id_mismatch_dropped() -> None:
    bus = MagicMock()
    tx_port = MagicMock()
    client = UdsClient(bus=bus, tx_port=tx_port, tx_id=0x7E0, rx_id=0x7E8)

    # Frame carries response for different service (e.g. 0x62 ReadDID instead of requested 0x50 SessionControl)
    unsolicited = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x7E8,
        data=b"\x03\x62\xF1\x90\x00\x00\x00\x00",
    )
    calls = [unsolicited]

    def _recv_mock(timeout_s: float = 0.1) -> CanFrame | None:
        if calls:
            return calls.pop(0)
        return None

    bus.recv.side_effect = _recv_mock

    with pytest.raises(ProtocolError, match="timed out"):
        client.change_session(DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION)


# ==============================================================================
# 4. Flasher BSC Validation and Empty Data Rejection
# ==============================================================================

def test_flasher_empty_transfer_data_rejected() -> None:
    mock_client = MagicMock()
    # Positive response for download
    mock_client.request_download.return_value = MagicMock(is_positive=True, nrc=0, data=b"\x20\x10\x00")
    mock_client.change_session.return_value = MagicMock(is_positive=True, nrc=0)
    # TransferData positive response but EMPTY data (violates ISO 14229-1)
    mock_client.transfer_data.return_value = MagicMock(is_positive=True, nrc=0, data=b"")

    gateway = MagicMock()
    gateway.estop.is_engaged = False
    gateway.supervisor.is_tx_permitted = True
    gateway.watchdog.is_lease_valid = True
    gateway.SPEED_NOISE_THRESHOLD_KMH = 0.5
    gateway._current_vehicle_speed_kmh = 0.0
    gateway._last_speed_update_ns = 1

    engine = EcuFlashingEngine(uds_client=mock_client, gateway=gateway)
    config = FlashingConfig(
        memory_address=0x08000000,
        data=b"HELLO",
        block_size=5,
        user_confirmed=True,
    )
    with pytest.raises(ProtocolError, match="boş veya BSC içermiyor"):
        engine._execute_flash_inner(config, time.monotonic(), 0, "00000000")


# ==============================================================================
# 5. J1939 Transport Protocol Safety Checks
# ==============================================================================

def test_j1939_bam_non_broadcast_rejected() -> None:
    tp = J1939TransportProtocol(my_address=0xF9)
    # BAM addressed to 0x10 instead of 0xFF
    bam_non_bc = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x18EC1000,  # DA=0x10, SA=0x00
        data=bytes([TP_CTRL_BAM, 14, 0, 2, 0xFF, 0xEE, 0xFE, 0]),
        is_extended=True,
    )
    msg, resp = tp.handle_rx_frame(bam_non_bc)
    assert msg is None
    assert resp is None
    # No session opened
    assert len(tp._rx_sessions) == 0


def test_j1939_cts_skipping_next_seq_aborts() -> None:
    tp = J1939TransportProtocol(my_address=0x10)
    # Sender starts 20-packet transfer
    tp.start_cmdt_transfer(0xF9, 0xFEEE, bytes(140))
    key = (0x10, 0xF9, 0xFEEE, "j1939_ch0")
    session = tp._tx_sessions[key]
    assert session.next_sequence == 1

    # Receiver asks for packet 5 when next_sequence is 1 (illegal skip)
    bad_cts = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EC10F9,  # DA=0x10, SA=0xF9
        data=bytes([TP_CTRL_CTS, 5, 5, 0xFF, 0xFF, 0xEE, 0xFE, 0]),
        is_extended=True,
    )
    msg, abort = tp.handle_rx_frame(bad_cts)
    assert msg is None
    assert abort is not None
    assert abort.data[0] == 0xFF  # Conn_Abort
    assert abort.data[1] == ABORT_REASON_UNEXPECTED_CONTROL
    assert key not in tp._tx_sessions


def test_j1939_ack_mismatched_pgn_aborts() -> None:
    tp = J1939TransportProtocol(my_address=0x10)
    tp.start_cmdt_transfer(0xF9, 0xFEEE, bytes(14))
    key = (0x10, 0xF9, 0xFEEE, "j1939_ch0")
    session = tp._tx_sessions[key]
    session.state = "WAIT_ACK"
    session.next_sequence = 3

    # Peer sends ACK with wrong total_bytes (99 instead of 14)
    bad_ack = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EC10F9,
        data=bytes([TP_CTRL_ACK, 99, 0, 2, 0xFF, 0xEE, 0xFE, 0]),
        is_extended=True,
    )
    msg, abort = tp.handle_rx_frame(bad_ack)
    assert abort is not None
    assert abort.data[0] == 0xFF
    assert abort.data[1] == ABORT_REASON_UNEXPECTED_CONTROL
    assert key not in tp._tx_sessions


# ==============================================================================
# 6. Replay Safety Filter EDP Bit Check
# ==============================================================================

def test_replay_safety_filter_edp_bit_masked() -> None:
    filt = ReplaySafetyFilter()
    # DM11 (PGN 65235 / 0xFED3) with EDP bit (bit 25) set -> 0x1A...
    # Normal DM11: 0x18FED300
    # EDP-set DM11: 0x1AFED300 (bit 25 = 1)
    frame_edp = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1AFED300,
        data=b"\x00" * 8,
        is_extended=True,
    )
    is_safe, reason = filt.is_frame_safe(frame_edp)
    assert is_safe is False
    assert "BLOCKED_J1939_PGN" in reason


# ==============================================================================
# 7. RollingDiskBuffer Quarantine Resilience
# ==============================================================================

def test_rolling_disk_quarantine_resilience(tmp_path: Path) -> None:
    provider = EphemeralSecretBackend()
    key = os.urandom(32)
    provider.store_secret("ROLLING_DISK_HMAC_KEY", key)

    buf = RollingDiskBuffer(storage_dir=tmp_path / "bb", secret_provider=provider)
    frame = CanFrame.create(channel_id="ch0", arbitration_id=0x123, data=b"VALID")
    buf.append(frame)
    buf.flush(drain=True)

    # Corrupt one chunk
    chunk_files = list((tmp_path / "bb").glob("chunk_*.bin.zst"))
    assert len(chunk_files) == 1
    chunk_file = chunk_files[0]

    # Modify HMAC footer of the chunk
    import zstandard as zstd
    raw = zstd.ZstdDecompressor().decompress(chunk_file.read_bytes())
    tampered_raw = bytearray(raw)
    tampered_raw[-1] ^= 0xFF
    chunk_file.write_bytes(zstd.ZstdCompressor(level=3).compress(bytes(tampered_raw)))

    # With quarantine_corrupt=True, it moves aside and returns empty without crash
    frames = buf.read_all_stored_frames(quarantine_corrupt=True)
    assert len(frames) == 0
    assert (tmp_path / "bb" / f"{chunk_file.name}.corrupt").exists()
    buf.close()


# ==============================================================================
# 8. Cloud License offline_until Enforcement
# ==============================================================================

def test_cloud_license_offline_until_enforced() -> None:
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    client = MagicMock()
    client.get_device_id.return_value = "dev_123"

    flow = LicenseFlow(client=client, public_key=pub_key, trusted_keys={"ucan-cloud-ed25519-v1": pub_key})
    now = time.time()

    import base64
    import json
    ticket_payload = {
        "iss": "universal-can-cloud",
        "aud": "diagnostic-desktop-app",
        "kid": "ucan-cloud-ed25519-v1",
        "license_id": "lic_99",
        "organization_id": "org_1",
        "device_id": "dev_123",
        "tier": "enterprise",
        "features": ["flashing", "uds"],
        "iat": now - 7200,
        "exp": now + 86400,
        "offline_until": now - 10,  # Grace period expired!
        "schema_version": 1,
        "nonce": "abc12345",
    }
    raw_payload = json.dumps(ticket_payload).encode("utf-8")
    sig = priv_key.sign(raw_payload)
    token = f"{base64.urlsafe_b64encode(raw_payload).decode('utf-8').rstrip('=')}.{base64.urlsafe_b64encode(sig).decode('utf-8').rstrip('=')}"

    # Online check succeeds (now < exp)
    claims = flow.verify_cloud_ticket(token, is_offline=False)
    assert claims.license_id == "lic_99"

    # Offline check fails (now > offline_until)
    with pytest.raises(LicenseError) as exc_info:
        flow.verify_cloud_ticket(token, is_offline=True)
    assert exc_info.value.code == "OFFLINE_GRACE_EXPIRED"


# ==============================================================================
# 9. SafeMultiplexedBus Critical Flag Propagation
# ==============================================================================

def test_safe_multiplexed_bus_flags_propagation() -> None:
    phys_bus = MagicMock()
    gateway = MagicMock()
    router = FrameRouter()

    bus = SafeMultiplexedBus(physical_bus=phys_bus, gateway=gateway, router=router)
    frame = CanFrame.create(channel_id="ch0", arbitration_id=0x7E0, data=b"\x36\x01\xAA")

    bus.send_sync(frame, is_critical_command=True, user_confirmed=True)
    gateway.validate_and_transmit.assert_called_once_with(
        frame, is_critical_command=True, user_confirmed=True, budget_category="default"
    )

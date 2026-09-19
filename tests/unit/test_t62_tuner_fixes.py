"""Unit test suite verifying Review Phase 1-10 Tuner remediations (T62-T1 to T62-T8).

Covers:
- [T62-T1] J1939 TP.DT ambiguous parallel session fail-closed drop & session purge.
- [T62-T2] J1939 BAM pacing interval 50-200ms bounds enforcement.
- [T62-T3] Rolling disk HMAC key vault storage failure fail-closed & availability.
- [T62-T4] OBD/UDS polling channel_id verification before reassembly.
- [T62-T5] OEM J1939 decoded signals value iteration and sentinel filtering.
- [T62-T6] Signal discovery channel, format, and arbitration ID stream segregation.
- [T62-T7] UDS SecurityAccess & recovery request_transfer_exit gateway token forwarding.
- [T62-T8] VirtualChannel nominal torque finite positive validation.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.contracts.ports import InMemoryTxPort, QueueRxSubscription
from src.core.errors import SecurityError
from src.core.models.can_frame import CanFrame
from src.engine.buffer.rolling_disk import RollingDiskBuffer
from src.engine.decoder.dbc_decoder import DecodedSignal
from src.engine.discovery.engine import SignalDiscoveryEngine
from src.engine.virtual_channels.channel_engine import VirtualChannelEngine
from src.protocols.j1939.oem.registry import OemDecodedPayload
from src.protocols.j1939.transport import J1939TransportProtocol, ReassemblySession
from src.protocols.obd.poller import ActiveDiagnosticPoller
from src.protocols.uds.client import UdsClient
from src.protocols.uds.flasher import EcuFlashingEngine, FlashingConfig
from src.safety.secret_provider import SecretProvider

# ============================================================================
# [T62-T1] J1939 TP.DT ambiguous parallel session fail-closed drop
# ============================================================================


def test_t62_t1_j1939_ambiguous_tp_dt_dropped_fail_closed() -> None:
    """Ambiguous TP.DT with matching sequence on parallel sessions must drop fail-closed and clean sessions."""
    tp = J1939TransportProtocol(my_address=0xF9)
    now = tp._get_now()

    sess_a = ReassemblySession(
        source_address=0x10,
        destination_address=0xF9,
        target_pgn=0xEF01,
        total_bytes=14,
        total_packets=2,
        is_bam=False,
        expected_sequence=1,
        last_activity_time=now - 0.1,
        channel_id="ch0",
    )
    sess_b = ReassemblySession(
        source_address=0x10,
        destination_address=0xF9,
        target_pgn=0xEF02,
        total_bytes=14,
        total_packets=2,
        is_bam=False,
        expected_sequence=1,
        last_activity_time=now,
        channel_id="ch0",
    )

    key_a = (0x10, 0xF9, "ch0", 0xEF01)
    key_b = (0x10, 0xF9, "ch0", 0xEF02)
    tp._rx_sessions[key_a] = sess_a
    tp._rx_sessions[key_b] = sess_b
    tp._per_sa_sessions["16"] = 2

    assert tp.anomaly_metrics.ambiguous_dt_drop == 0

    # Looking up DT seq 1 when two sessions expect seq 1 must return (None, None)
    resolved_key, resolved_sess = tp._lookup_dt_session(0x10, 0xF9, "ch0", seq_num=1)
    assert resolved_key is None
    assert resolved_sess is None

    # Anomaly metric must be incremented
    assert tp.anomaly_metrics.ambiguous_dt_drop == 1
    assert tp.anomaly_metrics.snapshot()["ambiguous_dt_drop"] == 1

    # Both ambiguous sessions must be purged
    assert key_a not in tp._rx_sessions
    assert key_b not in tp._rx_sessions


# ============================================================================
# [T62-T2] J1939 BAM pacing interval 50-200ms bounds enforcement
# ============================================================================


def test_t62_t2_start_tp_bam_paced_interval_validation() -> None:
    """start_tp_bam_paced must strictly enforce 0.050 <= interval <= 0.200 seconds."""
    tp = J1939TransportProtocol(my_address=0x01)
    test_data = b"0123456789ABCDEF"  # 16 bytes -> 1 BAM announce + 3 DT packets

    # 0.0s must be rejected (violates SAE 50-200ms)
    with pytest.raises(ValueError, match="50..200 ms"):
        tp.start_tp_bam_paced(pgn=0xFEEE, data=test_data, interval_s=0.0)

    # Values < 0.050s must be rejected
    with pytest.raises(ValueError, match="50..200 ms"):
        tp.start_tp_bam_paced(pgn=0xFEEE, data=test_data, interval_s=0.049)

    # Values > 0.200s must be rejected
    with pytest.raises(ValueError, match="50..200 ms"):
        tp.start_tp_bam_paced(pgn=0xFEEE, data=test_data, interval_s=0.201)

    # Valid boundary values must succeed
    paced_min = tp.start_tp_bam_paced(pgn=0xFEEE, data=test_data, interval_s=0.050)
    assert len(paced_min) == 4
    # Announce is frame 0, DT1 is frame 1 (due at t0), DT2 is frame 2 (due at t0 + 0.050s)
    assert abs((paced_min[2][1] - paced_min[1][1]) - 0.050) < 1e-6

    paced_max = tp.start_tp_bam_paced(pgn=0xFEEE, data=test_data, interval_s=0.200)
    assert len(paced_max) == 4
    assert abs((paced_max[2][1] - paced_max[1][1]) - 0.200) < 1e-6

    # Default interval (0.050s) when interval_s is None
    paced_default = tp.start_tp_bam_paced(pgn=0xFEEE, data=test_data)
    assert len(paced_default) == 4
    assert abs((paced_default[2][1] - paced_default[1][1]) - 0.050) < 1e-6


# ============================================================================
# [T62-T3] Rolling disk HMAC key vault storage failure fail-closed
# ============================================================================


class FailingSecretProvider(SecretProvider):
    """Mock secret provider simulating vault write failure."""

    def get_secret(self, name: str) -> bytes:
        raise KeyError(f"Secret {name} not found")

    def store_secret(self, name: str, secret: bytes) -> None:
        raise OSError("Permission denied: Vault hardware inaccessible")


def test_t62_t3_rolling_disk_hmac_vault_failure_fails_closed(tmp_path: Path) -> None:
    """When HMAC key cannot be saved to the vault, fail closed with SecurityError."""
    failing_provider = FailingSecretProvider()

    with pytest.raises(SecurityError, match="Failed to persist rolling disk HMAC key into vault"):
        RollingDiskBuffer(
            storage_dir=tmp_path / "blackbox",
            secret_provider=failing_provider,
        )


def test_t62_t3_rolling_disk_is_available_flag(tmp_path: Path) -> None:
    """RollingDiskBuffer tracks is_available and drops frames when unavailable."""
    from src.safety.secret_provider import EphemeralSecretBackend

    provider = EphemeralSecretBackend()
    buf = RollingDiskBuffer(storage_dir=tmp_path / "blackbox_avail", secret_provider=provider)
    assert buf.is_available is True

    buf.close()
    assert buf.is_available is False


# ============================================================================
# [T62-T4] OBD/UDS poller channel_id verification before reassembly
# ============================================================================


@pytest.mark.asyncio
async def test_t62_t4_obd_poller_channel_id_filter_functional() -> None:
    """Functional poller must drop frames arriving on foreign channels before reassembly."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    poller = ActiveDiagnosticPoller(
        tx_port=tx_port,
        rx_subscription=rx_sub,
        channel_id="can0",
    )

    # Frame on wrong channel ('can1') with valid arbitration_id (0x7E8) and payload
    foreign_frame = CanFrame.create(
        channel_id="can1",
        arbitration_id=0x7E8,
        data=bytes([0x03, 0x41, 0x0C, 0x1A]),
        is_extended=False,
    )
    # Frame on correct channel ('can0')
    correct_frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E8,
        data=bytes([0x04, 0x41, 0x0C, 0x1A, 0xF8]),
        is_extended=False,
    )

    await rx_sub.put(foreign_frame)
    await rx_sub.put(correct_frame)

    results = await poller.poll_pid_functional(pid=0x0C, collect_window_s=0.05)
    # Only the frame from can0 should have been reassembled and decoded
    assert len(results) == 1
    assert results[0].source_rx_id == 0x7E8


@pytest.mark.asyncio
async def test_t62_t4_obd_poller_channel_id_filter_one_shot() -> None:
    """One-shot diagnostic query must ignore frames arriving on foreign channels."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    poller = ActiveDiagnosticPoller(
        tx_port=tx_port,
        rx_subscription=rx_sub,
        channel_id="can0",
    )

    # Foreign frame from can1
    foreign_frame = CanFrame.create(
        channel_id="can1",
        arbitration_id=0x7E8,
        data=bytes([0x03, 0x62, 0xF1, 0x90]),
        is_extended=False,
    )
    # Correct frame from can0
    correct_frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E8,
        data=bytes([0x04, 0x62, 0xF1, 0x90, 0x55]),
        is_extended=False,
    )

    await rx_sub.put(foreign_frame)
    await rx_sub.put(correct_frame)

    result = await poller.poll_did_once(did=0xF190, timeout_s=0.1)
    assert result is not None
    assert result.did == 0xF190


# ============================================================================
# [T62-T5] OEM J1939 decoded signals value iteration and sentinel filtering
# ============================================================================


def test_t62_t5_oem_decoded_payload_iteration_and_validity() -> None:
    """OemDecodedPayload iterates over signal values and honours decoder validity."""
    sig_valid = DecodedSignal(
        name="EngineSpeed",
        value=1500.0,
        unit="rpm",
        raw_value=12000,
        is_valid=True,
    )
    # The DECODER is responsible for sentinel detection: a not-available byte is
    # surfaced as is_valid=False. `get_value` must not re-guess from raw_value.
    sig_sentinel_16 = DecodedSignal(
        name="CoolantTemp",
        value=0.0,
        unit="degC",
        raw_value=0xFFFF,  # Not available sentinel
        is_valid=False,
    )
    sig_sentinel_8 = DecodedSignal(
        name="OilPressure",
        value=0.0,
        unit="kPa",
        raw_value=0xFF,  # Not available sentinel
        is_valid=False,
    )
    sig_invalid = DecodedSignal(
        name="BoostPressure",
        value=100.0,
        unit="kPa",
        raw_value=50,
        is_valid=False,
    )

    payload = OemDecodedPayload(
        manufacturer="Caterpillar",
        pgn=0xFF17,
        signals={
            "EngineSpeed": sig_valid,
            "CoolantTemp": sig_sentinel_16,
            "OilPressure": sig_sentinel_8,
            "BoostPressure": sig_invalid,
        },
        timestamp_ns=1_000_000,
    )

    # Direct iteration over payload yields DecodedSignal instances (no AttributeError on sig.name)
    yielded_names = [sig.name for sig in payload]
    assert "EngineSpeed" in yielded_names
    assert "CoolantTemp" in yielded_names

    assert payload.is_valid("EngineSpeed") is True
    assert payload.get_value("EngineSpeed") == 1500.0

    # Invalid (incl. sentinel-marked) signals yield nothing.
    assert payload.is_valid("CoolantTemp") is False
    assert payload.get_value("CoolantTemp") is None
    assert payload.get_value("CoolantTemp", default=-1.0) == -1.0

    assert payload.is_valid("OilPressure") is False
    assert payload.get_value("OilPressure") is None

    assert payload.is_valid("BoostPressure") is False
    assert payload.get_value("BoostPressure") is None


def test_t62_t5_enumerated_0xff_is_not_swallowed_as_sentinel() -> None:
    """A VALID signal whose raw value is 0xFF must still resolve.

    0xFF / 0xFFFF are legitimate enumerated values in many OEM layouts
    (e.g. Cummins PGN 61184 `target_cylinder == 0xFF` meaning "all
    cylinders"). Re-testing raw_value inside `get_value` silently swallowed
    real data, so sentinel policy stays at the decoder/`is_valid` level only.
    """
    sig_all_cylinders = DecodedSignal(
        name="target_cylinder",
        value=0xFF,
        unit="",
        raw_value=0xFF,
        is_valid=True,
    )
    payload = OemDecodedPayload(
        manufacturer="Cummins",
        pgn=61184,
        signals={"target_cylinder": sig_all_cylinders},
        timestamp_ns=1_000_000,
    )

    assert payload.is_valid("target_cylinder") is True
    assert payload.get_value("target_cylinder") == 0xFF


# ============================================================================
# [T62-T6] Signal discovery channel, format, and arbitration ID segregation
# ============================================================================


def test_t62_t6_signal_discovery_stream_segregation() -> None:
    """SignalDiscoveryEngine must segregate streams by (channel_id, is_extended, arbitration_id)."""
    engine = SignalDiscoveryEngine(min_frames=5)

    # Feed 10 frames of ID 0x100 on can0 (standard)
    frames_can0_std = [
        CanFrame.create(
            channel_id="can0",
            arbitration_id=0x100,
            data=bytes([i % 16, 0x11]),
            is_extended=False,
            timestamp_ns=i * 10_000_000,
        )
        for i in range(10)
    ]
    # Feed 10 frames of ID 0x100 on can1 (standard)
    frames_can1_std = [
        CanFrame.create(
            channel_id="can1",
            arbitration_id=0x100,
            data=bytes([0xAA, i % 8]),
            is_extended=False,
            timestamp_ns=i * 10_000_000,
        )
        for i in range(10)
    ]
    # Feed 10 frames of ID 0x100 on can0 (extended)
    frames_can0_ext = [
        CanFrame.create(
            channel_id="can0",
            arbitration_id=0x100,
            data=bytes([0x55, 0x55, i % 4]),
            is_extended=True,
            timestamp_ns=i * 10_000_000,
        )
        for i in range(10)
    ]

    engine.ingest_frames(frames_can0_std)
    engine.ingest_frames(frames_can1_std)
    engine.ingest_frames(frames_can0_ext)

    # 3 distinct stream keys
    key_can0_std = ("can0", False, 0x100)
    key_can1_std = ("can1", False, 0x100)
    key_can0_ext = ("can0", True, 0x100)

    assert key_can0_std in engine.discovered_keys
    assert key_can1_std in engine.discovered_keys
    assert key_can0_ext in engine.discovered_keys

    # Each bucket holds exactly 10 frames
    assert engine.get_frame_count(key_can0_std) == 10
    assert engine.get_frame_count(key_can1_std) == 10
    assert engine.get_frame_count(key_can0_ext) == 10

    # Analysis reports are segregated by key
    all_reports = engine.analyze_all()
    assert len(all_reports) == 3
    assert key_can0_std in all_reports
    assert key_can1_std in all_reports
    assert key_can0_ext in all_reports

    rep_can0_std = all_reports[key_can0_std]
    rep_can1_std = all_reports[key_can1_std]
    rep_can0_ext = all_reports[key_can0_ext]

    assert rep_can0_std.channel_id == "can0" and rep_can0_std.is_extended is False
    assert rep_can1_std.channel_id == "can1" and rep_can1_std.is_extended is False
    assert rep_can0_ext.channel_id == "can0" and rep_can0_ext.is_extended is True

    # Payload DLCs match the segregated stream lengths
    assert rep_can0_std.dlc == 2
    assert rep_can1_std.dlc == 2
    assert rep_can0_ext.dlc == 3


# ============================================================================
# [T62-T7] UDS SecurityAccess & recovery request_transfer_exit token forwarding
# ============================================================================


def test_t62_t7_uds_client_security_access_confirmation_token_forwarding() -> None:
    """Iso14229Client security_access methods forward confirmation_token to _send_and_receive."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    client = UdsClient(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8)
    client._send_and_receive = MagicMock()

    token = b"test_token_123456"
    client.security_access_request_seed(
        level=1,
        user_confirmed=True,
        confirmation_token=token,
    )
    client._send_and_receive.assert_called_once()
    _, kwargs = client._send_and_receive.call_args
    assert kwargs["confirmation_token"] == token
    assert kwargs["user_confirmed"] is True
    assert kwargs["is_critical_command"] is True

    client._send_and_receive.reset_mock()
    client.security_access_send_key(
        level=1,
        key=b"\x01\x02\x03\x04",
        user_confirmed=True,
        confirmation_token=token,
    )
    client._send_and_receive.assert_called_once()
    _, kwargs = client._send_and_receive.call_args
    assert kwargs["confirmation_token"] == token
    assert kwargs["user_confirmed"] is True
    assert kwargs["is_critical_command"] is True


def test_t62_t7_flasher_recovery_forwards_confirmation_token() -> None:
    """EcuFlashingEngine recovery ladder must forward confirmation_token to request_transfer_exit."""
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    client = UdsClient(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8)
    client.request_transfer_exit = MagicMock()
    client.change_session = MagicMock()

    mock_gateway = MagicMock()
    mock_issuer = MagicMock(return_value=b"flasher_recovery_token")
    flasher = EcuFlashingEngine(
        uds_client=client,
        gateway=mock_gateway,
        confirmation_token_factory=mock_issuer,
    )

    config = FlashingConfig(
        data=b"\x00" * 16,
        user_confirmed=True,
    )

    flasher._best_effort_recovery(config)

    client.request_transfer_exit.assert_called_once_with(
        user_confirmed=True,
        confirmation_token=b"flasher_recovery_token",
    )


def test_t62_t7_flasher_flash_sequence_security_access_forwards_confirmation_token() -> None:
    """EcuFlashingEngine step 4 forwards confirmation_token to security_access methods."""
    from src.protocols.uds.client import UdsResponse

    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    client = UdsClient(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8)
    client.change_session = MagicMock(return_value=UdsResponse(service_id=0x10, is_positive=True, data=b"\x02"))
    client.security_access_request_seed = MagicMock(
        return_value=UdsResponse(service_id=0x27, is_positive=True, data=b"\x01\x11\x22\x33\x44")
    )
    client.security_access_send_key = MagicMock(
        return_value=UdsResponse(service_id=0x27, is_positive=True, data=b"\x02")
    )
    client.request_download = MagicMock(
        return_value=UdsResponse(service_id=0x34, is_positive=True, data=b"\x20\x01\x00")
    )
    client.transfer_data = MagicMock(
        return_value=UdsResponse(service_id=0x36, is_positive=True, data=b"\x01")
    )
    client.request_transfer_exit = MagicMock(
        return_value=UdsResponse(service_id=0x37, is_positive=True, data=b"")
    )
    client.routine_control = MagicMock(
        return_value=UdsResponse(service_id=0x31, is_positive=True, data=b"")
    )
    client.start_routine = MagicMock(
        return_value=UdsResponse(service_id=0x31, is_positive=True, data=b"")
    )
    client.request_routine_results = MagicMock(
        return_value=UdsResponse(service_id=0x31, is_positive=True, data=b"\x03\x02\x02\x00")
    )
    client.ecu_reset = MagicMock(
        return_value=UdsResponse(service_id=0x11, is_positive=True, data=b"\x01")
    )

    mock_gateway = MagicMock()
    mock_gateway.estop.is_engaged = False
    mock_gateway.supervisor.is_tx_permitted = True
    mock_gateway.watchdog.is_lease_valid = True
    mock_gateway.is_speed_fresh_and_safe = MagicMock(return_value=True)

    mock_issuer = MagicMock(return_value=b"flasher_sec_token")
    flasher = EcuFlashingEngine(
        uds_client=client,
        gateway=mock_gateway,
        confirmation_token_factory=mock_issuer,
    )

    config = FlashingConfig(
        data=b"\x00" * 16,
        user_confirmed=True,
        security_level=1,
        security_key=b"\x99\x88\x77\x66",
        require_signature=False,
        require_target_identity=False,
    )

    flasher.execute_flash(config)

    # Verify seed and key calls received confirmation_token
    client.security_access_request_seed.assert_called_once_with(
        level=1,
        user_confirmed=True,
        confirmation_token=b"flasher_sec_token",
    )
    client.security_access_send_key.assert_called_once_with(
        level=1,
        key=b"\x99\x88\x77\x66",
        user_confirmed=True,
        confirmation_token=b"flasher_sec_token",
    )


# ============================================================================
# [T62-T8] VirtualChannel nominal torque finite positive validation
# ============================================================================


def test_t62_t8_calculate_torque_and_power_nominal_torque_validation() -> None:
    """calculate_torque_and_power must raise ValueError on non-finite or non-positive nominal_torque_nm."""
    # Zero nominal torque
    with pytest.raises(ValueError, match="finite positive"):
        VirtualChannelEngine.calculate_torque_and_power(1500.0, 50.0, nominal_torque_nm=0.0)

    # Negative nominal torque
    with pytest.raises(ValueError, match="finite positive"):
        VirtualChannelEngine.calculate_torque_and_power(1500.0, 50.0, nominal_torque_nm=-500.0)

    # NaN nominal torque
    with pytest.raises(ValueError, match="finite positive"):
        VirtualChannelEngine.calculate_torque_and_power(1500.0, 50.0, nominal_torque_nm=math.nan)

    # Infinite nominal torque
    with pytest.raises(ValueError, match="finite positive"):
        VirtualChannelEngine.calculate_torque_and_power(1500.0, 50.0, nominal_torque_nm=math.inf)

    # None nominal torque
    with pytest.raises(ValueError, match="finite positive"):
        VirtualChannelEngine.calculate_torque_and_power(1500.0, 50.0, nominal_torque_nm=None)  # type: ignore[arg-type]

    # Valid nominal torque succeeds
    torque, power_kw, power_hp = VirtualChannelEngine.calculate_torque_and_power(1500.0, 50.0, nominal_torque_nm=1000.0)
    assert torque == 500.0
    assert power_kw is not None and power_kw > 0
    assert power_hp is not None and power_hp > 0

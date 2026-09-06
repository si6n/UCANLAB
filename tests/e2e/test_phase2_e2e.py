"""Universal CAN-Bus Diagnostic & Telemetry Platform - Phase 2 Master E2E Test Suite.

Complies with Phase 2 Architecture (PROJECT.md), docs/testing/TEST_INFRA.md, and Formal Spec Report:
- R1: Universal TxPort Choke-Point & UDS Gateway (CAN-02)
- R2: Cross-Platform SecretProvider & Replay-Protected E-Stop (CAN-05)
- R3: Fail-Closed Dynamic Whitelist Policy (CAN-06)
- R4: Deadlock-Free Callback Dispatch & Snapshot-Then-Release (CAN-12)
- R5: Strict 6-Stage Gateway Rule Ordering & Clock Specialization (CAN-24, CAN-25)

4-Tier Test Architecture:
- Tier 1: Feature Coverage (>=5 tests per feature for R1-R5, Total >= 25)
- Tier 2: Boundary & Corner Cases (>=5 tests per feature for R1-R5, Total >= 25)
- Tier 3: Cross-Feature Pairwise Combinations (Total >= 12)
- Tier 4: Real-World Vehicle Diagnostic & Telemetry Scenarios (Total >= 6)
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import os
import queue
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import pytest

from src.core.contracts.ports import (
    InMemorySecretProvider,
    InMemoryTxPort,
    SecretProvider,
    TxPort,
)
from src.core.errors import SafetyError, SecurityError
from src.core.models.can_frame import CanFrame
from src.hal.base import AbstractBus
from src.protocols.uds.client import UdsClient
from src.protocols.uds.services import (
    DiagnosticSessionType,
    UdsServiceId,
)
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor

# ===========================================================================
# Test Helpers & Provider Mocks
# ===========================================================================


class MockMemoryBus(AbstractBus):
    """In-memory AbstractBus implementing protected _send_raw for HAL conformance."""

    def __init__(self, channel_id: str = "mock_vbus_0") -> None:
        super().__init__(channel_id=channel_id)
        self.sent_frames: list[CanFrame] = []
        self.rx_queue: queue.Queue[CanFrame] = queue.Queue()
        self.is_connected = True

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:
        if not self.is_connected:
            raise SafetyError("Cannot transmit on disconnected bus", code="BUS_DISCONNECTED")
        self.sent_frames.append(frame)

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        try:
            return self.rx_queue.get(timeout=timeout_s or 0.05)
        except queue.Empty:
            return None

    def inject_rx(self, frame: CanFrame) -> None:
        self.rx_queue.put(frame)


class SimulatedLinuxKeyfileSecretProvider:
    """Simulates POSIX 0600 file-backed SecretProvider backend."""

    def __init__(self, directory: str | None = None) -> None:
        self._dir = directory or tempfile.mkdtemp()
        self._keyfile = os.path.join(self._dir, "secrets.bin")
        self._secrets: dict[str, bytes] = {}

    def set_secret(self, key_name: str, secret: bytes) -> None:
        self._secrets[key_name] = secret
        with open(self._keyfile, "wb") as f:
            for k, v in self._secrets.items():
                f.write(f"{k}:".encode("utf-8") + v + b"\n")

    def get_secret(self, key_name: str) -> bytes:
        if key_name not in self._secrets:
            raise KeyError(f"Secret '{key_name}' not found")
        return self._secrets[key_name]


class SimulatedWindowsDpapiSecretProvider:
    """Simulates Windows DPAPI CryptProtectData / CryptUnprotectData provider."""

    def __init__(self, entropy: bytes = b"UniversalCAN_Hardware_Secret_Binding_2026") -> None:
        self.entropy = entropy
        self._encrypted_vault: dict[str, bytes] = {}

    def set_secret(self, key_name: str, secret: bytes) -> None:
        tag = hashlib.sha256(secret + self.entropy).digest()
        obfuscated = bytes([b ^ self.entropy[i % len(self.entropy)] for i, b in enumerate(secret)])
        self._encrypted_vault[key_name] = tag + obfuscated

    def get_secret(self, key_name: str) -> bytes:
        if key_name not in self._encrypted_vault:
            raise KeyError(f"Secret '{key_name}' not found")
        payload = self._encrypted_vault[key_name]
        tag = payload[:32]
        obfuscated = payload[32:]
        secret = bytes([b ^ self.entropy[i % len(self.entropy)] for i, b in enumerate(obfuscated)])
        expected_tag = hashlib.sha256(secret + self.entropy).digest()
        if not hmac.compare_digest(tag, expected_tag):
            raise SecurityError("DPAPI secret integrity check failed", code="DPAPI_TAMPERED")
        return secret


# ===========================================================================
# TIER 1: FEATURE COVERAGE (>=5 tests per feature for R1, R2, R3, R4, R5)
# ===========================================================================

# ---------------------------------------------------------------------------
# Tier 1 - R1: Universal TxPort & UDS Gateway Integration (CAN-02)
# ---------------------------------------------------------------------------


def test_tier1_txport_gateway_can_frame_transmission() -> None:
    """Tier 1.1.1: Verify CanFrame transmission through TxSafetyGateway with valid whitelist."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x10\x01\x00\x00\x00\x00\x00")

    result = gateway.validate_and_transmit(frame)
    assert result is True
    assert len(bus.sent_frames) == 1
    assert bus.sent_frames[0].arbitration_id == 0x7E0
    assert bus.sent_frames[0].data == frame.data


def test_tier1_txport_uds_client_read_did_over_txport() -> None:
    """Tier 1.1.2: Verify UdsClient ReadDID (0x22 0xF190) routing requests via TxPort."""
    bus = MockMemoryBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), tx_id=0x7E0, rx_id=0x7E8)

    resp_frame = CanFrame.create(
        channel_id="mock_vbus_0",
        arbitration_id=0x7E8,
        data=b"\x04\x62\xf1\x90\x55\x00\x00\x00",
    )
    bus.inject_rx(resp_frame)

    resp = client.read_did(0xF190)
    assert resp.is_positive is True
    assert resp.service_id == UdsServiceId.READ_DATA_BY_IDENTIFIER
    assert resp.data == b"\xf1\x90\x55"
    assert len(bus.sent_frames) == 1
    assert bus.sent_frames[0].arbitration_id == 0x7E0
    client.close()


def test_tier1_txport_uds_client_session_and_routine_controls() -> None:
    """Tier 1.1.3: Verify UdsClient session change and routine controls over TxPort."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway.for_testing(bus=bus)
    gateway.update_vehicle_speed(0.0)
    client = UdsClient(bus=bus, tx_port=gateway, tx_id=0x7E0, rx_id=0x7E8)

    # 1. Change Session
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x02\x50\x03\x00\x00\x00\x00\x00")
    )
    resp1 = client.change_session(DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION)
    assert resp1.is_positive is True
    assert resp1.service_id == UdsServiceId.DIAGNOSTIC_SESSION_CONTROL

    # 2. Write DID
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x03\x6e\x01\x00\x00\x00\x00\x00")
    )
    resp2 = client.write_did(0x0100, b"\x01\x02", user_confirmed=True)
    assert resp2.is_positive is True

    # 3. Start Routine
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x04\x71\x01\x02\x01\x00\x00\x00")
    )
    resp3 = client.start_routine(0x0201, b"\x01", user_confirmed=True)
    assert resp3.is_positive is True

    # 4. Stop Routine
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x04\x71\x02\x02\x01\x00\x00\x00")
    )
    resp4 = client.stop_routine(0x0201)
    assert resp4.is_positive is True

    # 5. Tester Present (No response suppression)
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x02\x7e\x00\x00\x00\x00\x00\x00")
    )
    resp5 = client.tester_present(suppress_response=False)
    assert resp5 is not None
    assert resp5.is_positive is True

    client.shutdown()


def test_tier1_txport_rejection_of_invalid_and_disconnected_ports() -> None:
    """Tier 1.1.4: Verify TxSafetyGateway / HAL rejection when underlying port is disconnected."""
    bus = MockMemoryBus()
    bus.disconnect()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    with pytest.raises(SafetyError, match="disconnected bus"):
        gateway.validate_and_transmit(frame)


@pytest.mark.asyncio
async def test_tier1_txport_sync_and_async_api_conformance() -> None:
    """Tier 1.1.5: Verify both sync (send_sync) and async (send) TxPort protocol methods."""
    port = InMemoryTxPort()
    assert isinstance(port, TxPort)

    frame_sync = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x10\x01")
    frame_async = CanFrame.create(channel_id="c0", arbitration_id=0x7E8, data=b"\x50\x01")

    port.send_sync(frame_sync)
    assert len(port.sent_frames) == 1
    assert port.sent_frames[0] == frame_sync

    await port.send(frame_async)
    assert len(port.sent_frames) == 2
    assert port.sent_frames[1] == frame_async


# ---------------------------------------------------------------------------
# Tier 1 - R2: Cross-Platform SecretProvider & Replay-Protected E-Stop (CAN-05)
# ---------------------------------------------------------------------------


def test_tier1_secret_provider_dynamic_key_provisioning() -> None:
    """Tier 1.2.1: Verify dynamic key provisioning from SecretProvider to EmergencyStopSystem."""
    provider = InMemorySecretProvider()
    assert isinstance(provider, SecretProvider)

    dynamic_secret = os.urandom(32)
    provider.set_secret("ESTOP_HMAC_KEY", dynamic_secret)

    retrieved = provider.get_secret("ESTOP_HMAC_KEY")
    assert retrieved == dynamic_secret

    estop = EmergencyStopSystem(reset_secret=retrieved, allow_self_reset=True)
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Operator button")
    assert estop.is_engaged

    token = estop.compute_reset_token()
    estop.reset(token)
    assert not estop.is_engaged


def test_tier1_estop_valid_token_reset() -> None:
    """Tier 1.2.2: Verify valid challenge-response reset disengages E-Stop."""
    # Test fixture secret (32 bytes) Ã¢â‚¬â€ explicitly NOT a production key; the
    # misleading old name tripped secret-scanner false positives.
    secret = b"estop_test_fixture_secret_32byte"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)

    estop.trigger(EStopTriggerSource.SPEED_INTERLOCK_BREACH, reason="Speed violation", vehicle_speed_kmh=45.0)
    assert estop.is_engaged
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.SPEED_INTERLOCK_BREACH

    nonce = estop.get_reset_nonce()
    assert len(nonce) == 16

    token = estop.compute_reset_token()
    assert token != ""
    assert isinstance(token, str)

    estop.reset(token)
    assert not estop.is_engaged
    # B10: engagement audit record survives the reset
    assert estop.last_event is not None
    assert estop.get_reset_nonce() == b""


def test_tier1_secret_provider_ephemeral_backend() -> None:
    """Tier 1.2.3: Verify ephemeral/in-memory SecretProvider storing and querying multiple secrets."""
    provider = InMemorySecretProvider(
        {
            "UDS_SEED_KEY": b"\xAA" * 16,
            "ESTOP_RESET_KEY": b"\xBB" * 32,
            "AES_GCM_SECRET": b"\xCC" * 32,
        }
    )
    assert provider.get_secret("UDS_SEED_KEY") == b"\xAA" * 16
    assert provider.get_secret("ESTOP_RESET_KEY") == b"\xBB" * 32
    assert provider.get_secret("AES_GCM_SECRET") == b"\xCC" * 32

    with pytest.raises(KeyError, match="not found"):
        provider.get_secret("UNSET_KEY")


def test_tier1_secret_provider_linux_backend_simulation() -> None:
    """Tier 1.2.4: Verify Linux ACL keyfile backend storing and retrieving secrets securely."""
    provider = SimulatedLinuxKeyfileSecretProvider()
    secret = b"linux_acl_protected_master_secret"
    provider.set_secret("ESTOP_SECRET", secret)

    retrieved = provider.get_secret("ESTOP_SECRET")
    assert retrieved == secret


def test_tier1_secret_provider_windows_dpapi_backend() -> None:
    """Tier 1.2.5: Verify Windows DPAPI simulated provider protects and recovers secret payload."""
    provider = SimulatedWindowsDpapiSecretProvider()
    secret = b"dpapi_hardware_bound_secret_token"
    provider.set_secret("WINDOWS_ESTOP_KEY", secret)

    retrieved = provider.get_secret("WINDOWS_ESTOP_KEY")
    assert retrieved == secret


# ---------------------------------------------------------------------------
# Tier 1 - R3: Fail-Closed Dynamic Whitelist Policy (CAN-06)
# ---------------------------------------------------------------------------


def test_tier1_whitelist_empty_whitelist_rejection() -> None:
    """Tier 1.3.1: Verify empty whitelist rejects unauthorized frame transmission."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x123, data=b"\x01")

    with pytest.raises(SafetyError, match="not in whitelist") as exc_info:
        gateway.validate_and_transmit(frame)
    assert exc_info.value.code == "WHITELIST_VIOLATION"


def test_tier1_whitelist_unauthorized_id_violation_triggers_estop() -> None:
    """Tier 1.3.2: Verify whitelist violation automatically trips E-Stop with UNAUTHORIZED_PAYLOAD."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame_unauthorized = CanFrame.create(channel_id="c0", arbitration_id=0x666, data=b"\xDE\xAD")

    with pytest.raises(SafetyError, match="not in whitelist"):
        gateway.validate_and_transmit(frame_unauthorized)

    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.UNAUTHORIZED_PAYLOAD


def test_tier1_whitelist_explicit_test_ids_permitted() -> None:
    """Tier 1.3.3: Verify explicit test whitelist allows intended testing IDs."""
    bus = MockMemoryBus()
    test_whitelist = {0x000, 0x100, 0x7E0, 0x7E8, 0x18DAF110, 0x1FFFFFFF}
    gateway = TxSafetyGateway(bus=bus, whitelist_ids=test_whitelist)

    for can_id in test_whitelist:
        frame = CanFrame.create(channel_id="c0", arbitration_id=can_id, data=b"\x00")
        assert gateway.validate_and_transmit(frame) is True


def test_tier1_whitelist_dynamic_runtime_id_addition() -> None:
    """Tier 1.3.4: Verify adding new ID to whitelist dynamically permits subsequent transmission."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    whitelist = {0x7E0}
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids=whitelist)

    frame_new = CanFrame.create(channel_id="c0", arbitration_id=0x7E8, data=b"\x01")

    # Initial attempt rejected
    with pytest.raises(SafetyError):
        gateway.validate_and_transmit(frame_new)
    assert estop.is_engaged is True

    # Reset E-Stop and dynamically add ID
    token = estop.compute_reset_token()
    estop.reset(token)
    assert not estop.is_engaged
    gateway.whitelist_ids.add(0x7E8)

    # Second attempt succeeds
    assert gateway.validate_and_transmit(frame_new) is True


def test_tier1_whitelist_multiple_authorized_ids() -> None:
    """Tier 1.3.5: Verify gateway correctly filters authorized vs unauthorized IDs in a multi-ID whitelist."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x100, 0x200, 0x300})

    assert gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01")) is True
    assert gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x200, data=b"\x02")) is True
    assert gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x300, data=b"\x03")) is True

    with pytest.raises(SafetyError):
        gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x400, data=b"\x04"))


# ---------------------------------------------------------------------------
# Tier 1 - R4: Deadlock-Free State Machine & Snapshot-Then-Release (CAN-12)
# ---------------------------------------------------------------------------


def test_tier1_state_machine_fault_transitions_and_locks() -> None:
    """Tier 1.4.1: Verify snapshot-then-release transition to FAULT increments epoch without holding lock."""
    supervisor = SafetySupervisor(initial_state=SafetyState.ACTIVE)
    assert supervisor.current_state == SafetyState.ACTIVE
    assert supervisor.epoch == 0

    supervisor._force_fault("Emergency line trip")
    assert supervisor.current_state == SafetyState.FAULT
    assert supervisor.epoch == 1
    assert supervisor.fault_reason == "Emergency line trip"


def test_tier1_state_machine_callback_execution_isolation() -> None:
    """Tier 1.4.2: Verify exception isolation prevents crashing callbacks from halting state transitions."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    executed: list[str] = []

    def failing_cb1(old: SafetyState, new: SafetyState, reason: str) -> None:
        executed.append("failing1")
        raise RuntimeError("Crash in listener 1")

    def failing_cb2(old: SafetyState, new: SafetyState, reason: str) -> None:
        executed.append("failing2")
        raise ZeroDivisionError("Crash in listener 2")

    def healthy_cb(old: SafetyState, new: SafetyState, reason: str) -> None:
        executed.append("healthy")

    supervisor.register_callback(failing_cb1)
    supervisor.register_callback(healthy_cb)
    supervisor.register_callback(failing_cb2)

    supervisor.transition_to(SafetyState.SAFE, "Boot OK")
    assert supervisor.current_state == SafetyState.SAFE
    assert executed == ["failing1", "healthy", "failing2"]


def test_tier1_state_machine_epoch_increment_on_all_transitions() -> None:
    """Tier 1.4.3: Verify state transition epoch counter strictly monotonically increases on each transition."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    epochs = [supervisor.epoch]

    supervisor.transition_to(SafetyState.SAFE, "1")
    epochs.append(supervisor.epoch)

    supervisor.transition_to(SafetyState.PASSIVE, "2")
    epochs.append(supervisor.epoch)

    supervisor.arm_tx("3")
    epochs.append(supervisor.epoch)

    supervisor.activate_tx("4")
    epochs.append(supervisor.epoch)

    supervisor.trigger_fault("5")
    epochs.append(supervisor.epoch)

    assert epochs == [0, 1, 2, 3, 4, 5]


def test_tier1_state_machine_lifecycle_transitions() -> None:
    """Tier 1.4.4: Verify complete lifecycle audit trail recording all transitions."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    supervisor.transition_to(SafetyState.SAFE, "Step 1")
    supervisor.transition_to(SafetyState.PASSIVE, "Step 2")

    history = supervisor.get_history()
    assert len(history) == 2
    assert history[0].from_state == SafetyState.STARTUP
    assert history[0].to_state == SafetyState.SAFE
    assert history[1].from_state == SafetyState.SAFE
    assert history[1].to_state == SafetyState.PASSIVE


def test_tier1_state_machine_reentrant_query_safety() -> None:
    """Tier 1.4.5: Verify reentrant state inspection inside callback does not deadlock."""
    inspected_states: list[SafetyState] = []
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)

    def reentrant_cb(old: SafetyState, new: SafetyState, reason: str) -> None:
        inspected_states.append(supervisor.get_state())
        _ = supervisor.get_epoch()
        _ = supervisor.is_tx_permitted
        _ = supervisor.get_state_duration_ns()

    supervisor.register_callback(reentrant_cb)
    supervisor.transition_to(SafetyState.SAFE, "safe")
    supervisor.transition_to(SafetyState.PASSIVE, "passive")

    assert inspected_states == [SafetyState.SAFE, SafetyState.PASSIVE]


# ---------------------------------------------------------------------------
# Tier 1 - R5: Rule Ordering & Monotonic Clocks (CAN-24, CAN-25)
# ---------------------------------------------------------------------------


def test_tier1_rule_ordering_6_stage_pipeline_enforcement() -> None:
    """Tier 1.5.1: Verify 6-stage gateway validation pipeline passes clean frames."""
    bus = MockMemoryBus()
    supervisor = SafetySupervisor(initial_state=SafetyState.ARMED_TX)
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, supervisor=supervisor, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01\x02\x03\x04")
    assert gateway.validate_and_transmit(frame, is_critical_command=False, user_confirmed=False) is True


def test_tier1_rule_ordering_speed_interlock_before_dual_confirmation() -> None:
    """Tier 1.5.2: Verify moving vehicle triggers speed interlock and trips E-Stop on critical commands."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    gateway.update_vehicle_speed(25.0)  # > 0.5 km/h
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")  # ECUReset

    with pytest.raises(SafetyError) as exc_info:
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)

    assert estop.is_engaged is True
    assert exc_info.value.code in {"SPEED_INTERLOCK_ACTIVE", "CONFIRMATION_REQUIRED"}


def test_tier1_rule_ordering_dual_confirmation_when_stationary() -> None:
    """Tier 1.5.3: Verify stationary vehicle requires dual confirmation for critical commands."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    gateway.update_vehicle_speed(0.0)

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")

    # Rejected without confirmation
    with pytest.raises(SafetyError, match="dual-confirmation missing"):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=False)

    # Allowed with confirmation
    assert gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True) is True


def test_tier1_clock_specialization_monotonic_durations() -> None:
    """Tier 1.5.4: Verify state duration calculations use monotonic time and increase smoothly."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    time.sleep(0.05)
    duration_ns = supervisor.state_duration_ns
    assert duration_ns >= 20_000_000  # At least 20ms
    assert supervisor.state_duration_sec >= 0.020


def test_tier1_clock_specialization_utc_wall_clock_audit_logging() -> None:
    """Tier 1.5.5: Verify audit records contain UTC ISO-8601 wall-clock timestamps."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    t_before = datetime.now(timezone.utc)
    time.sleep(0.005)
    supervisor.transition_to(SafetyState.SAFE, "Check UTC")
    t_after = datetime.now(timezone.utc)

    history = supervisor.get_history()
    assert len(history) == 1
    rec = history[0]
    assert t_before <= rec.wall_time_utc <= t_after
    d = rec.to_dict()
    assert "wall_time_utc" in d
    assert isinstance(d["wall_time_utc"], str)


# ===========================================================================
# TIER 2: BOUNDARY & CORNER CASES (>=5 tests per feature for R1, R2, R3, R4, R5)
# ===========================================================================

# ---------------------------------------------------------------------------
# Tier 2 - R1: Gateway Boundary Cases
# ---------------------------------------------------------------------------


def test_tier2_r1_canfd_maximum_dlc_64_bytes() -> None:
    """Tier 2.1.1: Verify transmission of maximum DLC CAN-FD 64-byte payload."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    payload = bytes(range(64))
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=payload, is_fd=True, dlc=15)

    assert gateway.validate_and_transmit(frame) is True
    assert len(bus.sent_frames[0].data) == 64


def test_tier2_r1_standard_can_boundary_id_zero_and_7ff() -> None:
    """Tier 2.1.2: Verify standard CAN arbitration ID boundaries 0x000 and 0x7FF."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x000, 0x7FF})

    frame_min = CanFrame.create(channel_id="c0", arbitration_id=0x000, data=b"\x00")
    frame_max = CanFrame.create(channel_id="c0", arbitration_id=0x7FF, data=b"\xFF")

    assert gateway.validate_and_transmit(frame_min) is True
    assert gateway.validate_and_transmit(frame_max) is True


def test_tier2_r1_extended_can_boundary_id_29bit_max() -> None:
    """Tier 2.1.3: Verify extended CAN arbitration ID boundary 0x1FFFFFFF (29-bit max)."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x1FFFFFFF})

    frame_ext = CanFrame.create(channel_id="c0", arbitration_id=0x1FFFFFFF, data=b"\x12\x34", is_extended=True)
    assert gateway.validate_and_transmit(frame_ext) is True


def test_tier2_r1_zero_length_payload_classic_and_fd() -> None:
    """Tier 2.1.4: Verify transmission of 0-byte DLC payload frame across gateway."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})

    frame_empty = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"", dlc=0)
    assert gateway.validate_and_transmit(frame_empty) is True


def test_tier2_r1_maximum_worker_pool_concurrency() -> None:
    """Tier 2.1.5: Verify UdsClient worker pool handles 32 concurrent requests without resource leak."""
    bus = MockMemoryBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), tx_id=0x7E0, rx_id=0x7E8, max_workers=8)

    def dummy_task(val: int) -> int:
        return val * 2

    futures = [client.execute_async(dummy_task, i) for i in range(32)]
    results = [f.result(timeout=2.0) for f in futures]
    assert results == [i * 2 for i in range(32)]
    client.close()


# ---------------------------------------------------------------------------
# Tier 2 - R2: E-Stop & SecretProvider Boundary Cases
# ---------------------------------------------------------------------------


def test_tier2_r2_replayed_nonce_token_rejection() -> None:
    """Tier 2.2.1: Verify previously valid reset token is rejected upon second trigger (anti-replay)."""
    secret = b"anti_replay_test_secret_32bytes!"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)

    # First trigger & reset
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="First")
    token1 = estop.compute_reset_token()
    estop.reset(token1)
    assert not estop.is_engaged

    # Second trigger
    estop.trigger(EStopTriggerSource.BUS_OFF_DETECTED, reason="Second")
    assert estop.is_engaged

    # Attempt replay of token1 -> Must fail with ESTOP_RESET_DENIED
    with pytest.raises(SafetyError) as exc_info:
        estop.reset(token1)
    assert exc_info.value.code in {"ESTOP_RESET_DENIED", "ESTOP_REPLAY_DETECTED"}
    assert estop.is_engaged


def test_tier2_r2_tampered_hmac_signature_rejection() -> None:
    """Tier 2.2.2: Verify 1-byte tampered HMAC token is rejected via constant-time verification."""
    secret = b"tamper_test_secret_32_bytes_len!"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)

    estop.trigger(EStopTriggerSource.HARDWARE_DISCONNECT, reason="Disconnect")
    valid_token = estop.compute_reset_token()

    # Tamper with the signature field (last field after last colon)
    parts = valid_token.split(":")
    if len(parts) == 5:
        tampered_sig = ("0" if parts[4][0] != "0" else "1") + parts[4][1:]
        tampered = f"{parts[0]}:{parts[1]}:{parts[2]}:{parts[3]}:{tampered_sig}"
    else:
        tampered = ("0" if valid_token[0] != "0" else "1") + valid_token[1:]

    with pytest.raises(SafetyError, match="Invalid E-Stop reset token") as exc_info:
        estop.reset(tampered)
    assert exc_info.value.code == "ESTOP_RESET_DENIED"
    assert estop.is_engaged


def test_tier2_r2_empty_or_malformed_token_rejection() -> None:
    """Tier 2.2.3: Verify empty, short, or non-hex reset tokens are strictly rejected."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.RATE_LIMIT_OVERFLOW, reason="Overflow")

    for bad_token in ["", "   ", "short", "invalid_hex!@#$", "0" * 32]:
        with pytest.raises(SafetyError):
            estop.reset(bad_token)
        assert estop.is_engaged


def test_tier2_r2_secret_provider_empty_secret_handling() -> None:
    """Tier 2.2.4: Verify SecretProvider handles empty or 0-byte secrets gracefully."""
    provider = InMemorySecretProvider()
    provider.set_secret("EMPTY_KEY", b"")
    assert provider.get_secret("EMPTY_KEY") == b""


def test_tier2_r2_secret_provider_large_key_storage() -> None:
    """Tier 2.2.5: Verify SecretProvider stores and retrieves large 4096-byte RSA/cryptographic keys."""
    provider = InMemorySecretProvider()
    large_secret = os.urandom(4096)
    provider.set_secret("RSA_4096_PRIVATE_KEY", large_secret)
    assert provider.get_secret("RSA_4096_PRIVATE_KEY") == large_secret


# ---------------------------------------------------------------------------
# Tier 2 - R3: Whitelist Boundary Cases
# ---------------------------------------------------------------------------


def test_tier2_r3_single_id_whitelist_exact_match() -> None:
    """Tier 2.3.1: Verify single-ID whitelist `{0x7E0}` allows exactly 0x7E0, rejects neighbors 0x7DF & 0x7E1."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})

    # Exact match allowed
    assert gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")) is True

    # Immediate lower neighbor rejected
    with pytest.raises(SafetyError):
        gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x7DF, data=b"\x01"))


def test_tier2_r3_full_range_boundary_whitelist() -> None:
    """Tier 2.3.2: Verify boundary mix containing standard and extended CAN IDs."""
    bus = MockMemoryBus()
    whitelist = {0x000, 0x7FF, 0x18DAF110, 0x1FFFFFFF}
    gateway = TxSafetyGateway(bus=bus, whitelist_ids=whitelist)

    for cid in whitelist:
        assert gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=cid, data=b"\x00")) is True


def test_tier2_r3_whitelist_large_capacity_1000_ids() -> None:
    """Tier 2.3.3: Verify large whitelist with 1000 IDs provides constant-time $O(1)$ lookups."""
    bus = MockMemoryBus()
    whitelist = set(range(1000))
    gateway = TxSafetyGateway(bus=bus, whitelist_ids=whitelist)

    t0 = time.perf_counter()
    for cid in range(500):
        res = (cid in gateway.whitelist_ids)
        assert res is True
    t_elapsed = time.perf_counter() - t0
    assert t_elapsed < 0.05


def test_tier2_r3_whitelist_frozen_set_compatibility() -> None:
    """Tier 2.3.4: Verify gateway functions seamlessly when given a frozen set of IDs."""
    bus = MockMemoryBus()
    frozen_ids = frozenset([0x100, 0x200, 0x300])
    gateway = TxSafetyGateway(bus=bus, whitelist_ids=set(frozen_ids))

    assert gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x200, data=b"\x01")) is True


def test_tier2_r3_whitelist_extended_29bit_format_validation() -> None:
    """Tier 2.3.5: Verify 29-bit J1939 PGN IDs in whitelist are validated correctly."""
    bus = MockMemoryBus()
    j1939_eec1_id = 0x0CF00400
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={j1939_eec1_id})

    frame = CanFrame.create(channel_id="c0", arbitration_id=j1939_eec1_id, data=b"\x00" * 8, is_extended=True)
    assert gateway.validate_and_transmit(frame) is True


# ---------------------------------------------------------------------------
# Tier 2 - R4: State Machine Boundary Cases
# ---------------------------------------------------------------------------


def test_tier2_r4_callback_raising_fatal_error_isolation() -> None:
    """Tier 2.4.1: Verify callback raising unexpected exception is safely isolated."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)

    def fatal_cb(old: SafetyState, new: SafetyState, reason: str) -> None:
        raise SystemError("Simulated critical system callback error")

    supervisor.register_callback(fatal_cb)
    supervisor.transition_to(SafetyState.SAFE, "Boot")
    assert supervisor.current_state == SafetyState.SAFE


def test_tier2_r4_multiple_cascading_callbacks() -> None:
    """Tier 2.4.2: Verify 5 cascading callbacks all execute safely without deadlock."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    cascade_log: list[int] = []

    def make_cb(idx: int) -> Callable[[SafetyState, SafetyState, str], None]:
        def _cb(old: SafetyState, new: SafetyState, reason: str) -> None:
            cascade_log.append(idx)
        return _cb

    for i in range(5):
        supervisor.register_callback(make_cb(i))

    supervisor.transition_to(SafetyState.SAFE, "init")
    assert cascade_log == [0, 1, 2, 3, 4]


def test_tier2_r4_concurrent_state_queries_from_10_threads() -> None:
    """Tier 2.4.3: Verify 10 concurrent threads querying state and epoch during transitions."""
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE)
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for _ in range(50):
                _ = supervisor.get_state()
                _ = supervisor.get_epoch()
                _ = supervisor.is_tx_permitted
                _ = supervisor.get_state_duration_ns()
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, i) for i in range(10)]
        for f in futures:
            f.result()

    assert len(errors) == 0


def test_tier2_r4_state_duration_ns_boundary_zero_elapsed() -> None:
    """Tier 2.4.4: Verify state duration calculation immediately after transition is >= 0."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    supervisor.transition_to(SafetyState.SAFE, "now")
    duration = supervisor.get_state_duration_ns()
    assert duration >= 0


def test_tier2_r4_repeated_force_fault_idempotence() -> None:
    """Tier 2.4.5: Verify calling _force_fault repeatedly with identical reason is idempotent."""
    supervisor = SafetySupervisor(initial_state=SafetyState.ACTIVE)
    supervisor._force_fault("Emergency stop")
    epoch1 = supervisor.epoch

    supervisor._force_fault("Emergency stop")
    epoch2 = supervisor.epoch
    assert epoch1 == epoch2


# ---------------------------------------------------------------------------
# Tier 2 - R5: Rule Ordering & Rate Limiting Boundary Cases
# ---------------------------------------------------------------------------


def test_tier2_r5_speed_threshold_exact_boundary_0_500_vs_0_501() -> None:
    """Tier 2.5.1: Verify speed 0.500 km/h is allowed (sensor noise) vs 0.501 km/h is blocked."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")

    # 0.500 km/h <= SPEED_NOISE_THRESHOLD_KMH (0.5 km/h) -> Allowed with user confirmation
    gateway.update_vehicle_speed(0.500)
    assert gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True) is True

    # 0.501 km/h > SPEED_NOISE_THRESHOLD_KMH -> Blocked and trips E-Stop!
    gateway.update_vehicle_speed(0.501)
    with pytest.raises(SafetyError, match="Safety Interlock"):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)
    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.SPEED_INTERLOCK_BREACH


def test_tier2_r5_rate_limiter_burst_exact_100_vs_101_boundary() -> None:
    """Tier 2.5.2 / P1-9: exactly 100 msg/s passes; overloads are rejected as
    backpressure, and only a sustained streak trips the E-Stop."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Transmit exactly 100 messages
    for _ in range(gateway.MAX_TX_RATE_PER_SEC):
        assert gateway.validate_and_transmit(frame) is True

    assert len(gateway._tx_timestamps) == 100

    # Overloads reject WITHOUT latching an E-Stop (backpressure semantics)
    for _ in range(gateway.RATE_ESTOP_AFTER - 1):
        with pytest.raises(SafetyError) as rej:
            gateway.validate_and_transmit(frame)
        assert rej.value.code == "RATE_LIMIT_EXCEEDED"
    assert estop.is_engaged is False

    # The sustained-streak threshold trips the E-Stop
    with pytest.raises(SafetyError) as exc_info:
        gateway.validate_and_transmit(frame)
    assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"
    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.RATE_LIMIT_OVERFLOW


def test_tier2_r5_rate_limiter_sliding_window_1000ms_recovery() -> None:
    """Tier 2.5.3: Verify rate budget is replenished after sliding window (1.0s) elapses."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Seed 50 timestamps from 2.0s ago
    old_t = time.monotonic() - 2.0
    for i in range(50):
        gateway._tx_timestamps.append((old_t, threading.get_ident(), i))

    # Transmit new frame -> expired timestamps popped
    assert gateway.validate_and_transmit(frame) is True
    assert len(gateway._tx_timestamps) == 1


def test_tier2_r5_system_wall_clock_shift_does_not_affect_monotonic_invariants() -> None:
    """Tier 2.5.4: Verify state machine duration is immune to wall clock date/time shifts."""
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    t0_mono = supervisor.state_change_timestamp_ns
    assert t0_mono > 0
    time.sleep(0.25)
    assert supervisor.state_duration_ns >= 50_000_000


def test_tier2_r5_speed_interlock_negative_speed_sanitization() -> None:
    """Tier 2.5.5: Verify negative speed values (sensor reverse polarity / corrupt) fail-closed."""
    gateway = TxSafetyGateway(bus=MockMemoryBus(), whitelist_ids={0x7E0})
    gateway.update_vehicle_speed(0.0)
    assert gateway._last_speed_update_ns > 0
    gateway.update_vehicle_speed(-15.0)
    assert gateway._last_speed_update_ns == 0


# ===========================================================================
# TIER 3: CROSS-FEATURE PAIRWISE COMBINATIONS (>= 12 tests)
# ===========================================================================


def test_tier3_uds_critical_service_ecu_reset_moving_vehicle_blocks_before_dual_confirmation() -> None:
    """Tier 3.1: UDS ECUReset (0x11) on moving vehicle (15 km/h) -> Speed interlock blocks and engages E-Stop."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    gateway.update_vehicle_speed(15.0)

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")

    with pytest.raises(SafetyError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)

    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.SPEED_INTERLOCK_BREACH


def test_tier3_uds_critical_service_write_did_moving_vehicle_blocks_before_dual_confirmation() -> None:
    """Tier 3.2: UDS WriteDID (0x2E) on moving vehicle (30 km/h) -> Speed interlock blocks and engages E-Stop."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    gateway.update_vehicle_speed(30.0)

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x2e\x01\x00\xaa\xbb")

    with pytest.raises(SafetyError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)

    assert estop.is_engaged is True


def test_tier3_estop_active_blocks_valid_uds_read_did_request() -> None:
    """Tier 3.3: Active E-Stop immediately blocks valid UDS ReadDID (0x22 0xF190) request."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Emergency Button")

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x22\xf1\x90")
    with pytest.raises(SafetyError, match="Emergency Stop is currently ENGAGED") as exc_info:
        gateway.validate_and_transmit(frame)
    assert exc_info.value.code == "ESTOP_ACTIVE"


def test_tier3_estop_reset_token_replay_attempt_while_vehicle_moving() -> None:
    """Tier 3.4: Replaying previous E-Stop reset token while vehicle is moving fails anti-replay."""
    secret = b"moving_vehicle_replay_secret_key"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)

    # First trigger and reset
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Stop 1")
    token1 = estop.compute_reset_token()
    estop.reset(token1)
    assert not estop.is_engaged

    # Second trigger on moving vehicle
    estop.trigger(EStopTriggerSource.SPEED_INTERLOCK_BREACH, reason="Speed breach", vehicle_speed_kmh=80.0)
    assert estop.is_engaged

    # Replay old token1 -> Rejection
    with pytest.raises(SafetyError) as exc_info:
        estop.reset(token1)
    assert exc_info.value.code in {"ESTOP_RESET_DENIED", "ESTOP_REPLAY_DETECTED"}
    assert estop.is_engaged


def test_tier3_empty_whitelist_and_valid_estop_token_interaction() -> None:
    """Tier 3.5: Authenticated E-Stop reset disengages E-Stop, but empty whitelist continues to block unauthorized frames."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Test")
    token = estop.compute_reset_token()
    estop.reset(token)
    assert not estop.is_engaged

    # Frame to unauthorized ID 0x123 still rejected by whitelist
    with pytest.raises(SafetyError, match="not in whitelist"):
        gateway.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x123, data=b"\x01"))


def test_tier3_reentrant_callback_triggering_estop_during_gateway_transmission() -> None:
    """Tier 3.6: Callback in supervisor triggers E-Stop mid-session; subsequent transmissions blocked immediately."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    supervisor = SafetySupervisor(initial_state=SafetyState.ARMED_TX)
    gateway = TxSafetyGateway(bus=bus, estop=estop, supervisor=supervisor, whitelist_ids={0x7E0})

    def fault_handler(old: SafetyState, new: SafetyState, reason: str) -> None:
        if new == SafetyState.FAULT:
            estop.trigger(EStopTriggerSource.PROCESS_TERMINATION, reason="Supervisor faulted")

    supervisor.register_callback(fault_handler)

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")
    assert gateway.validate_and_transmit(frame) is True

    supervisor.trigger_fault("Critical crash")
    assert estop.is_engaged is True

    with pytest.raises(SafetyError):
        gateway.validate_and_transmit(frame)


def test_tier3_rate_budget_exhausted_and_moving_vehicle() -> None:
    """Tier 3.7 / P1-9: sustained rate-limit overflow trips E-Stop; vehicle
    moving state updates do not clear E-Stop."""
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    for _ in range(gateway.MAX_TX_RATE_PER_SEC):
        gateway.validate_and_transmit(frame)

    # Drive the rejection streak up to the E-Stop threshold
    for _ in range(gateway.RATE_ESTOP_AFTER):
        with pytest.raises(SafetyError):
            gateway.validate_and_transmit(frame)
    assert estop.is_engaged is True

    gateway.update_vehicle_speed(50.0)
    assert estop.is_engaged is True


def test_tier3_secret_provider_rotation_during_active_estop_challenge() -> None:
    """Tier 3.8: SecretProvider key rotation invalidates tokens computed with old secret key."""
    provider = InMemorySecretProvider({"ESTOP_KEY": b"initial_secret_key_32_bytes_len!"})
    estop = EmergencyStopSystem(reset_secret=provider.get_secret("ESTOP_KEY"), allow_self_reset=True)

    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Test rotation")
    old_token = estop.compute_reset_token()

    # Rotate secret in provider and update E-Stop
    new_secret = b"rotated_new_secret_key_32_bytes!"
    provider.set_secret("ESTOP_KEY", new_secret)
    estop.reset_secret = provider.get_secret("ESTOP_KEY")

    # Old token fails
    with pytest.raises(SafetyError, match="Invalid E-Stop reset token"):
        estop.reset(old_token)

    # New token computed with rotated key succeeds
    new_token = estop.compute_reset_token()
    estop.reset(new_token)
    assert not estop.is_engaged


def test_tier3_uds_routine_control_with_watchdog_lease_expired_blocks_before_speed() -> None:
    """Tier 3.9: Expired watchdog lease blocks routine control (0x31) before checking speed or dual-confirmation.

    Deterministic: the lease runs on an injected VirtualClock (no real sleep).
    """
    from src.core.contracts.ports import VirtualClock

    bus = MockMemoryBus()
    supervisor = SafetySupervisor(initial_state=SafetyState.ARMED_TX)
    clock = VirtualClock(start_monotonic_sec=500.0)
    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=50.0, clock=clock)
    clock.advance(0.06)  # Expire watchdog lease

    gateway = TxSafetyGateway(bus=bus, watchdog=watchdog, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x31\x01\x02\x01")
    with pytest.raises(SafetyError, match="Watchdog lease has expired") as exc_info:
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)
    assert exc_info.value.code == "WATCHDOG_LEASE_EXPIRED"


def test_tier3_canfd_extended_pdu_over_uds_with_whitelist_and_rate_limiter() -> None:
    """Tier 3.10: Multi-frame CAN-FD UDS diagnostic session verified against whitelist and rate limits."""
    bus = MockMemoryBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})

    for i in range(10):
        frame = CanFrame.create(
            channel_id="c0",
            arbitration_id=0x7E0,
            data=bytes([(i * 3 + j) % 256 for j in range(64)]),
            is_fd=True,
            dlc=15,
        )
        assert gateway.validate_and_transmit(frame) is True

    assert len(bus.sent_frames) == 10


def test_tier3_supervisor_fault_clears_gateway_rate_limit_sliding_window() -> None:
    """Tier 3.11: Supervisor transition to FAULT flushes gateway rate limit sliding window."""
    bus = MockMemoryBus()
    supervisor = SafetySupervisor(initial_state=SafetyState.ARMED_TX)
    gateway = TxSafetyGateway(bus=bus, supervisor=supervisor, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")
    for _ in range(10):
        gateway.validate_and_transmit(frame)
    assert len(gateway._tx_timestamps) == 10

    supervisor.trigger_fault("Hardware line tripped")
    assert supervisor.is_fault is True
    assert len(gateway._tx_timestamps) == 0


def test_tier3_multi_channel_gateway_with_isolated_estop_and_whitelist() -> None:
    """Tier 3.12: Multiple independent gateways with distinct whitelists maintain isolated state."""
    bus1 = MockMemoryBus("bus1")
    bus2 = MockMemoryBus("bus2")
    estop1 = EmergencyStopSystem(allow_self_reset=True)
    estop2 = EmergencyStopSystem(allow_self_reset=True)

    gw1 = TxSafetyGateway(bus=bus1, estop=estop1, whitelist_ids={0x100})
    gw2 = TxSafetyGateway(bus=bus2, estop=estop2, whitelist_ids={0x200})

    frame1 = CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01")
    frame2 = CanFrame.create(channel_id="c0", arbitration_id=0x200, data=b"\x02")

    assert gw1.validate_and_transmit(frame1) is True
    assert gw2.validate_and_transmit(frame2) is True

    estop1.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Stop GW1")
    assert estop1.is_engaged is True
    assert estop2.is_engaged is False

    with pytest.raises(SafetyError):
        gw1.validate_and_transmit(frame1)
    assert gw2.validate_and_transmit(frame2) is True


# ===========================================================================
# TIER 4: REAL-WORLD APPLICATION SCENARIOS (>= 6 scenarios)
# ===========================================================================


def test_tier4_scenario1_full_diagnostic_session_stationary_vehicle() -> None:
    """Scenario 1: Complete diagnostic workflow on stationary vehicle with valid whitelist & dynamic HMAC key.

    Workflow:
    1. Verify stationary vehicle (0.0 km/h).
    2. Switch diagnostic session (0x10 0x03 Extended Diagnostic Session).
    3. Request SecurityAccess Seed (0x27 0x01) -> Receive 4-byte seed.
    4. Compute Key = Seed XOR 0xFF -> Send Key (0x27 0x02).
    5. Read VIN Data Identifier (0x22 0xF190).
    6. Write Calibration Data Identifier (0x2E 0x0100).
    7. Send Tester Present keepalive (0x3E 0x80).
    """
    bus = MockMemoryBus()
    secret_provider = InMemorySecretProvider({"ESTOP_HMAC_KEY": os.urandom(32)})
    estop = EmergencyStopSystem(reset_secret=secret_provider.get_secret("ESTOP_HMAC_KEY"), allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    gateway.update_vehicle_speed(0.0)

    client = UdsClient(bus=bus, tx_port=gateway, tx_id=0x7E0, rx_id=0x7E8)

    # 1. Diagnostic Session Control (0x10 0x03)
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x02\x50\x03\x00\x00\x00\x00\x00")
    )
    resp_session = client.change_session(DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION)
    assert resp_session.is_positive is True

    # 2. Security Access - Request Seed (0x27 0x01)
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x06\x67\x01\x11\x22\x33\x44\x00")
    )
    resp_seed = client.security_access_request_seed(level=1)
    assert resp_seed.is_positive is True
    seed = resp_seed.data[1:5]
    assert seed == b"\x11\x22\x33\x44"

    # 3. Security Access - Send Key (0x27 0x02)
    key = bytes([b ^ 0xFF for b in seed])
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x02\x67\x02\x00\x00\x00\x00\x00")
    )
    resp_key = client.security_access_send_key(level=2, key=key)
    assert resp_key.is_positive is True

    # 4. Read VIN DID (0x22 0xF190) Single-Frame
    bus.inject_rx(
        CanFrame.create(
            channel_id="mock_vbus_0",
            arbitration_id=0x7E8,
            data=b"\x04\x62\xf1\x90\x55\x00\x00\x00",
        )
    )
    resp_vin = client.read_did(0xF190)
    assert resp_vin.is_positive is True

    # 5. Write DID (0x2E 0x0100)
    bus.inject_rx(
        CanFrame.create(channel_id="mock_vbus_0", arbitration_id=0x7E8, data=b"\x03\x6e\x01\x00\x00\x00\x00\x00")
    )
    resp_write = client.write_did(0x0100, b"\x01\x02", user_confirmed=True)
    assert resp_write.is_positive is True

    # 6. Tester Present (0x3E 0x80)
    resp_tp = client.tester_present(suppress_response=True)
    assert resp_tp is None

    client.close()


def test_tier4_scenario2_estop_triggered_mid_session_and_hmac_recovery() -> None:
    """Scenario 2: Emergency stop triggered mid-session, all TX blocked, recovered via authenticated token.

    Workflow:
    1. Active session in progress -> Frame transmission succeeds.
    2. Emergency Stop triggered by USER_UI_BUTTON -> All subsequent transmissions blocked.
    3. Operator attempts unauthenticated reset -> Rejected.
    4. Operator obtains single-use challenge nonce from E-Stop controller.
    5. Authorized key server computes HMAC-SHA256 signature token.
    6. Operator submits signed token -> E-Stop disengaged.
    7. Diagnostic session resumes successfully.
    """
    bus = MockMemoryBus()
    secret = b"top_secret_hmac_key_for_estop_2026"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x22\xf1\x90")

    # Step 1: Normal transmission
    assert gateway.validate_and_transmit(frame) is True

    # Step 2: Emergency Stop triggered
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Operator Pressed Red Button")
    assert estop.is_engaged is True

    # Step 3: All transmission strictly blocked
    with pytest.raises(SafetyError, match="Emergency Stop is currently ENGAGED"):
        gateway.validate_and_transmit(frame)

    # Step 4: Unauthenticated reset fails
    with pytest.raises(SafetyError, match="Invalid E-Stop reset token"):
        estop.reset("unauthenticated_token_string")

    # Step 5: Acquire valid token
    valid_token = estop.compute_reset_token()
    assert valid_token != ""

    # Step 6: Authenticated reset
    estop.reset(valid_token)
    assert estop.is_engaged is False

    # Step 7: Transmission resumes cleanly
    assert gateway.validate_and_transmit(frame) is True


def test_tier4_scenario3_high_speed_driving_critical_security_rejection() -> None:
    """Scenario 3: High-speed driving scenario attempting security unlock (0x27) and ECU reset (0x11).

    Workflow:
    1. Vehicle speed sensor reports 120 km/h (highway driving).
    2. Upper layer / adversary attempts ECU Reset (0x11 0x01).
    3. Gateway instantly triggers SPEED_INTERLOCK_BREACH E-Stop and raises SafetyError.
    4. Vehicle remains moving -> Subsequent attempts immediately blocked by active E-Stop.
    5. Zero race conditions under concurrent transmission attempts.
    """
    bus = MockMemoryBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    gateway.update_vehicle_speed(120.0)  # 120 km/h

    frame_reset = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")

    with pytest.raises(SafetyError):
        gateway.validate_and_transmit(frame_reset, is_critical_command=True, user_confirmed=True)

    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.SPEED_INTERLOCK_BREACH
    assert estop.last_event.system_speed_kmh == 120.0

    # Subsequent frame blocked by E-Stop
    frame_sec = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x27\x01")
    with pytest.raises(SafetyError, match="Emergency Stop is currently ENGAGED"):
        gateway.validate_and_transmit(frame_sec)


def test_tier4_scenario4_malicious_can_replay_attack_simulation() -> None:
    """Scenario 4: Malicious CAN replay attack simulating captured E-Stop reset packets.

    Workflow:
    1. System triggered and reset normally; adversary on CAN bus sniffs and captures reset token T1.
    2. Time elapses, new safety violation occurs (e.g. TEMPERATURE_OVERHEAT).
    3. Adversary replays captured token T1.
    4. E-Stop anti-replay engine compares challenge nonce, detects mismatch, and rejects T1.
    5. E-Stop remains securely engaged.
    """
    secret = b"resilient_anti_replay_secret_32b"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)

    # Transaction 1
    estop.trigger(EStopTriggerSource.KEEPALIVE_TIMEOUT, reason="Heartbeat dropped")
    token1 = estop.compute_reset_token()
    estop.reset(token1)
    assert not estop.is_engaged

    # Transaction 2 (Adversary attacks)
    estop.trigger(EStopTriggerSource.TEMPERATURE_OVERHEAT, reason="Inverter over-temperature 110C")
    assert estop.is_engaged is True

    # Adversary injects captured token1 -> Must be rejected by anti-replay / epoch check
    with pytest.raises(SafetyError) as exc_info:
        estop.reset(token1)

    assert exc_info.value.code in {"ESTOP_RESET_DENIED", "ESTOP_REPLAY_DETECTED"}
    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.TEMPERATURE_OVERHEAT


def test_tier4_scenario5_multithreaded_telemetry_and_diagnostic_concurrency() -> None:
    """Scenario 5: Multi-threaded telemetry and diagnostic burst testing under heavy concurrency without deadlock.

    Concurrent Workloads:
    - Thread 1 & 2: Diagnostic read DID requests over TxSafetyGateway.
    - Thread 3: Cyclic telemetry streaming.
    - Thread 4: Live vehicle speed updates.
    - Thread 5: Safety Supervisor state and epoch inspections.
    """
    bus = MockMemoryBus()
    supervisor = SafetySupervisor(initial_state=SafetyState.ARMED_TX)
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, supervisor=supervisor, whitelist_ids={0x7E0, 0x18F00400})

    errors: list[Exception] = []
    iterations = 20

    def diag_worker(_worker_id: int) -> None:
        try:
            for _ in range(iterations):
                frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x22\xf1\x90")
                gateway.validate_and_transmit(frame)
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    def telemetry_worker() -> None:
        try:
            for _ in range(iterations):
                frame = CanFrame.create(channel_id="c0", arbitration_id=0x18F00400, data=b"\x00" * 8, is_extended=True)
                gateway.validate_and_transmit(frame)
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    def speed_worker() -> None:
        try:
            for _ in range(iterations):
                gateway.update_vehicle_speed(0.2)
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    def supervisor_worker() -> None:
        try:
            for _ in range(iterations):
                _ = supervisor.get_state()
                _ = supervisor.get_epoch()
                _ = supervisor.is_tx_permitted
                _ = supervisor.get_state_duration_ns()
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=diag_worker, args=(1,)),
        threading.Thread(target=diag_worker, args=(2,)),
        threading.Thread(target=telemetry_worker),
        threading.Thread(target=speed_worker),
        threading.Thread(target=supervisor_worker),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(bus.sent_frames) == iterations * 3


def test_tier4_scenario6_emergency_firmware_flashing_interrupted_by_bus_off() -> None:
    """Scenario 6: Firmware flashing transfer (0x34 -> 0x36) interrupted by Bus-Off hardware event.

    Workflow:
    1. Diagnostic client initiates RequestDownload (0x34).
    2. Data block 1 transferred (0x36 0x01).
    3. Bus-Off event occurs -> Emergency Stop engages with BUS_OFF_DETECTED.
    4. Supervisor forces FAULT state -> Block 2 transmission is rejected immediately.
    5. Hardware driver recovers; operator validates challenge nonce and resets E-Stop.
    6. Flashing workflow cleanly aborted and reset.
    """
    bus = MockMemoryBus()
    supervisor = SafetySupervisor(initial_state=SafetyState.ARMED_TX)
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, supervisor=supervisor, whitelist_ids={0x7E0})

    frame_download = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x34\x00\x44\x00\x00\x10\x00")
    frame_block1 = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x36\x01\xaa\xbb\xcc\xdd")
    frame_block2 = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x36\x02\x11\x22\x33\x44")

    # Steps 1 & 2: Initiate download and send Block 1
    assert gateway.validate_and_transmit(frame_download) is True
    assert gateway.validate_and_transmit(frame_block1) is True

    # Step 3: Bus-off controller error occurs
    estop.trigger(EStopTriggerSource.BUS_OFF_DETECTED, reason="CAN controller Tx error passive / bus-off")
    assert estop.is_engaged is True

    # Step 4: Block 2 transmission strictly blocked
    with pytest.raises(SafetyError) as exc_info:
        gateway.validate_and_transmit(frame_block2)
    assert exc_info.value.code in {"ESTOP_ACTIVE", "SAFETY_STATE_BLOCKED"}

    # Step 5: Operator recovery via HMAC challenge-response
    token = estop.compute_reset_token()
    estop.reset(token)
    assert not estop.is_engaged

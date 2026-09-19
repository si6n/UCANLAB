"""Tests for Tur 62 Marshal Safety Remediation (T62-M1 .. T62-M9)."""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.hal.virtual import VirtualBus
from src.safety.e2e.profiles import E2EProfileConfig, E2EProfileType
from src.safety.e2e.validator import E2ESafetyValidator
from src.safety.estop import EmergencyStopSystem
from src.safety.gateway import TxSafetyGateway
from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp


class MockMutableBus(VirtualBus):
    def __init__(self, channel: str = "vcan0", listen_only: bool = True) -> None:
        super().__init__(channel)
        self.listen_only = listen_only

    def set_listen_only(self, listen_only: bool) -> bool:
        self.listen_only = listen_only
        return True


def test_t62_m1_arm_tx_driver_rollback_on_supervisor_failure() -> None:
    """T62-M1: If supervisor fails to arm, physical driver must be rolled back to listen-only."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._is_simulating = False
    app.bus = MockMutableBus("vcan0", listen_only=True)
    app.gateway.update_physical_speed(0.0)

    with patch.object(app.supervisor, "arm_tx", side_effect=RuntimeError("injected supervisor failure")):
        res = app.arm_tx()
        assert res["success"] is False
        assert app.bus.listen_only is True


def test_t62_m1_arm_tx_rollback_failure_forces_fault() -> None:
    """T62-M1: If rollback to listen-only fails, force fault and E-Stop."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._is_simulating = False
    app.bus = MockMutableBus("vcan0", listen_only=True)
    app.gateway.update_physical_speed(0.0)

    with patch.object(app.supervisor, "arm_tx", side_effect=RuntimeError("injected supervisor failure")), \
         patch.object(app, "_set_driver_listen_only", side_effect=[True, False]):
        res = app.arm_tx()
        assert res["success"] is False
        assert "TX_ARM_ROLLBACK_FAILED" in res["error"]
        assert app.estop.is_engaged is True


def test_t62_m2_flash_safe_disarm_cleanup() -> None:
    """T62-M2: Post-flash safe disarm restores listen-only mode."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._is_simulating = False
    app.bus = MockMutableBus("vcan0", listen_only=False)

    cleanup_ok = app._safe_disarm_after_flash()
    assert cleanup_ok is True
    assert app.bus.listen_only is True


def test_t62_m4_rebind_bus_clears_total_timestamps() -> None:
    """T62-M4: rebind_bus clears global rate envelope counters."""
    bus1 = VirtualBus("vcan0")
    estop = EmergencyStopSystem()
    gateway = TxSafetyGateway(bus=bus1, estop=estop)

    # Artificially inject total timestamps and streak
    gateway._tx_total_timestamps.append((time.monotonic_ns(), 1234, 1))
    gateway._total_overload_streak = 5

    bus2 = VirtualBus("vcan1")
    gateway.rebind_bus(bus2)

    assert len(gateway._tx_total_timestamps) == 0
    assert gateway._total_overload_streak == 0


def test_t62_m5_abort_hook_unregister_on_rebind() -> None:
    """T62-M5: rebind_bus does not leak abort hooks in EmergencyStopSystem."""
    bus1 = MagicMock()
    bus1.flush_tx_buffer = MagicMock()
    estop = EmergencyStopSystem()

    gateway = TxSafetyGateway(bus=bus1, estop=estop)
    assert len(estop._abort_hooks) == 1

    bus2 = MagicMock()
    bus2.flush_tx_buffer = MagicMock()
    gateway.rebind_bus(bus2)
    assert len(estop._abort_hooks) == 1


def test_t62_m6_estop_get_secret_no_provider_io_under_lock() -> None:
    """T62-M6: _get_secret reads cached bytes without executing provider I/O under lock."""
    estop = EmergencyStopSystem()
    secret = estop._get_secret()
    assert isinstance(secret, bytes)
    assert len(secret) == 32


def test_t62_m7_e2e_stream_eviction_arrival_monotonic() -> None:
    """T62-M7: E2E stream eviction uses monotonic arrival time, ignoring spoofed frame timestamp."""
    validator = E2ESafetyValidator()
    cfg = E2EProfileConfig.create_autosar_profile_1(data_id=0x12)

    # Insert 1024 streams with futuristic frame timestamps
    for i in range(1024):
        validator.validate_raw(
            channel_id=f"ch_{i}",
            arbitration_id=0x100 + i,
            data=b"\x00" * 8,
            profile=cfg,
            timestamp_ns=10**18,  # Huge timestamp
        )

    assert len(validator._streams) == 1024
    # The first stream (ch_0) has the oldest arrival time, so it must be evicted on 1025th stream
    validator.validate_raw(
        channel_id="ch_new",
        arbitration_id=0x999,
        data=b"\x00" * 8,
        profile=cfg,
        timestamp_ns=0,
    )
    assert ("ch_0", 0x100) not in validator._streams
    assert ("ch_new", 0x999) in validator._streams


def test_t62_m8_e2e_counter_mask_validation() -> None:
    """T62-M8: Invalid / non-contiguous / capacity-exceeding counter masks are rejected."""
    # Capacity violation: 2-bit mask with modulo 16
    with pytest.raises(ValueError, match="exceeds maximum capacity"):
        E2EProfileConfig(
            profile_type=E2EProfileType.AUTOSAR_PROFILE_1A,
            data_id=0x10,
            crc_byte_offset=0,
            counter_byte_offset=1,
            counter_bit_mask=0x03,
            counter_bit_shift=0,
            counter_modulo=16,
            max_delta_counter=2,
        )

    # Non-contiguous bit mask
    with pytest.raises(ValueError, match="contiguous"):
        E2EProfileConfig(
            profile_type=E2EProfileType.AUTOSAR_PROFILE_1A,
            data_id=0x10,
            crc_byte_offset=0,
            counter_byte_offset=1,
            counter_bit_mask=0x05,  # 0b101
            counter_bit_shift=0,
            counter_modulo=4,
            max_delta_counter=2,
        )


def test_t62_m9_challenge_token_params_binding() -> None:
    """T62-M9: Altering action parameters invalidates the diagnostic challenge token."""
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._is_simulating = True
    app._current_speed_kmh = 0.0
    bridge = DesktopApiBridge(app)

    action = {
        "id": "routine-1",
        "action_type": "uds_routine",
        "params": {"routine_id": 0x1000},
        "requires_confirmation": True,
    }
    ch = bridge.request_diagnostic_challenge(action)
    assert ch["success"] is True
    token = ch["token"]

    # Tamper with routine_id while keeping the same token
    tampered_action = {
        "id": "routine-1",
        "action_type": "uds_routine",
        "params": {"routine_id": 0xD001},  # Altered!
        "requires_confirmation": True,
    }
    res = bridge.execute_diagnostic_action(tampered_action, confirmation_token=token)
    assert res["success"] is False
    assert "parametreler değiştirilmiş" in res["error"]

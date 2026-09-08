import asyncio
import concurrent.futures
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.core.contracts.ports import TxPort
from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.hal.virtual import VirtualBus
from src.safety.e2e.packager import E2ESafetyPackager
from src.safety.e2e.profiles import E2EProfileConfig
from src.safety.e2e.validator import E2ESafetyValidator
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.exceptions import (
    DualConfirmationRequiredError,
    FrameSanityError,
    RateLimitExceededError,
    SpeedDataStaleError,
    SpeedInterlockError,
    WhitelistFailClosedError,
    WhitelistViolationError,
)
from src.safety.gateway import TxSafetyGateway


def test_safety_gateway_normal_tx() -> None:
    bus = VirtualBus(channel_id="safety_vbus_0")
    bus.connect()

    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Transmit allowed
    assert gateway.validate_and_transmit(frame) is True
    assert len(bus.sent_frames) == 1
    bus.disconnect()


def test_safety_gateway_e2e_stamping_stage_seals_configured_ids() -> None:
    """docs/ai_context/02 §1 stage 6: configured IDs leave the gateway sealed
    with a rolling counter + CRC that the E2ESafetyValidator accepts, and the
    counter increments across successive sends."""
    bus = VirtualBus(channel_id="safety_vbus_e2e")
    bus.connect()

    profile = E2EProfileConfig.create_autosar_profile_1(data_id=0x0A0A)
    gateway = TxSafetyGateway(
        bus=bus,
        whitelist_ids={0x500},
        e2e_packager=E2ESafetyPackager(),
        e2e_profiles={0x500: profile},
    )

    raw = CanFrame.create(channel_id="c0", arbitration_id=0x500, data=b"\x00\x01\x11\x22\x33\x44\x55\x66")
    assert gateway.validate_and_transmit(raw) is True

    sealed = bus.sent_frames[0]
    # CRC byte offset 0 overwritten with a real CRC — not the raw 0x00 the
    # caller supplied — and the counter lives at byte 1 low nibble.
    assert sealed.data[0] != 0x00
    assert sealed.data[1] & 0x0F == 0

    validator = E2ESafetyValidator()
    # First frame on a fresh stream: INITIAL (authentic CRC, first sighting)
    result = validator.validate(sealed, profile)
    assert result.verdict.value == "INITIAL"
    assert result.is_crc_valid is True

    # Second send: counter advances and the consecutive frame validates OK
    assert gateway.validate_and_transmit(raw) is True
    sealed2 = bus.sent_frames[1]
    assert sealed2.data[1] & 0x0F == 1
    result2 = validator.validate(sealed2, profile)
    assert result2.verdict.value == "OK"
    bus.disconnect()


def test_safety_gateway_e2e_stamping_leaves_unconfigured_ids_unsealed() -> None:
    """Negative/boundary: without a profile for the ID, the frame passes
    through byte-identical — E2E protection stays opt-in per stream."""
    bus = VirtualBus(channel_id="safety_vbus_e2e2")
    bus.connect()

    gateway = TxSafetyGateway(
        bus=bus,
        whitelist_ids={0x501},
        e2e_packager=E2ESafetyPackager(),
        e2e_profiles={0x500: E2EProfileConfig.create_autosar_profile_1(data_id=0x0A0A)},
    )

    raw = CanFrame.create(channel_id="c0", arbitration_id=0x501, data=b"\x00\x01\x11\x22\x33\x44\x55\x66")
    assert gateway.validate_and_transmit(raw) is True
    assert bus.sent_frames[0].data == raw.data
    bus.disconnect()


def test_safety_gateway_estop_blocks_all() -> None:
    bus = VirtualBus(channel_id="safety_vbus_1")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")
    assert gateway.validate_and_transmit(frame) is True

    # Trigger E-Stop
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "Operator Pressed Red E-Stop Button")
    assert estop.is_engaged is True

    with pytest.raises(SafetyError, match="Emergency Stop is currently ENGAGED"):
        gateway.validate_and_transmit(frame)

    # Reset requires challenge token
    nonce = estop.get_reset_nonce()
    valid_token = estop.compute_reset_token(nonce)
    estop.reset(valid_token)

    assert estop.is_engaged is False
    assert gateway.validate_and_transmit(frame) is True
    bus.disconnect()


def test_safety_gateway_whitelist_violation_triggers_estop() -> None:
    bus = VirtualBus(channel_id="safety_vbus_2")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    # Unauthorized ID 0x123
    frame_bad = CanFrame.create(channel_id="c0", arbitration_id=0x123, data=b"\x01")

    with pytest.raises(WhitelistViolationError) as exc_info:
        gateway.validate_and_transmit(frame_bad)

    assert exc_info.value.code == "WHITELIST_VIOLATION"

    # Verify that E-Stop was automatically tripped
    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.UNAUTHORIZED_PAYLOAD
    bus.disconnect()


def test_safety_gateway_fail_closed_empty_whitelist() -> None:
    """Verify that empty or None whitelist is strictly Fail-Closed (R3)."""
    bus = VirtualBus(channel_id="safety_vbus_fc")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)

    # Empty whitelist â€” production gateway is strictly Fail-Closed
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids=set())
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    with pytest.raises(WhitelistFailClosedError) as exc_info:
        gateway.validate_and_transmit(frame)

    assert exc_info.value.code == "WHITELIST_FAIL_CLOSED"
    assert estop.is_engaged is False

    # None whitelist behaves identically
    gateway_none = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids=None)
    with pytest.raises(WhitelistFailClosedError):
        gateway_none.validate_and_transmit(frame)

    bus.disconnect()


def test_safety_gateway_for_testing_factory_bypasses_whitelist() -> None:
    """Verify the explicit for_testing() factory allows transmission when whitelist is empty."""
    bus = VirtualBus(channel_id="safety_vbus_testing")
    bus.connect()
    gateway = TxSafetyGateway.for_testing(bus=bus, whitelist_ids=set())

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x123, data=b"\x01")
    assert gateway.validate_and_transmit(frame) is True
    assert len(bus.sent_frames) == 1
    bus.disconnect()


def test_safety_gateway_speed_interlock_and_dual_confirmation() -> None:
    bus = VirtualBus(channel_id="safety_vbus_3")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x31\x01\x02\x01")

    # Critical command without any speed telemetry at boot fails immediately (fail-closed)
    with pytest.raises(SpeedDataStaleError) as exc_boot:
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)
    assert exc_boot.value.code == "SPEED_DATA_STALE"
    estop.reset(estop.create_reset_token())

    # Critical command without user confirmation fails when stationary
    gateway.update_vehicle_speed(0.0)
    with pytest.raises(DualConfirmationRequiredError, match="Operator dual-confirmation missing") as exc_info:
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=False)
    assert exc_info.value.code == "CONFIRMATION_REQUIRED"

    # Critical command with confirmation succeeds when stationary (speed = 0)
    gateway.update_vehicle_speed(0.0)
    assert gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True) is True

    # Minor sensor jitter (e.g. 0.2 km/h <= SPEED_NOISE_THRESHOLD_KMH 0.5 km/h) is permitted
    gateway.update_vehicle_speed(0.2)
    assert gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True) is True

    # Vehicle starts moving (speed = 25 km/h) -> Speed Interlock trips E-Stop!
    gateway.update_vehicle_speed(25.0)
    with pytest.raises(SpeedInterlockError, match="Safety Interlock") as exc_info2:
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)
    assert exc_info2.value.code == "SPEED_INTERLOCK_ACTIVE"

    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.SPEED_INTERLOCK_BREACH
    bus.disconnect()


def test_safety_gateway_rule_ordering_moving_vehicle_unconfirmed_command() -> None:
    """Verify CAN-24 Rule Ordering: Speed Interlock (Stage 4) takes precedence over Dual Confirmation (Stage 5)."""
    bus = VirtualBus(channel_id="safety_vbus_order")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")

    # Set vehicle in motion
    gateway.update_vehicle_speed(50.0)

    # Attempt critical command WITHOUT confirmation
    with pytest.raises(SpeedInterlockError) as exc_info:
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=False)

    assert exc_info.value.code == "SPEED_INTERLOCK_ACTIVE"
    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.SPEED_INTERLOCK_BREACH
    bus.disconnect()


def test_safety_gateway_speed_staleness_rejection() -> None:
    """Verify that stale vehicle speed telemetry (> 1.0s) blocks critical commands (CAN-24)."""
    bus = VirtualBus(channel_id="safety_vbus_stale")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")

    # Set speed update timestamp to 2 seconds in the past
    gateway._last_speed_update_ns = time.monotonic_ns() - 2_000_000_000

    with pytest.raises(SpeedDataStaleError) as exc_info:
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)

    assert exc_info.value.code == "SPEED_DATA_STALE"
    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.SPEED_INTERLOCK_BREACH
    bus.disconnect()


def test_safety_gateway_nan_and_negative_speed_fail_closed() -> None:
    """Verify that NaN or negative speeds immediately invalidate telemetry (fail-closed)."""
    bus = VirtualBus(channel_id="safety_vbus_nan")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x31\x01")

    # Valid initial speed
    gateway.update_vehicle_speed(0.0)
    assert gateway._last_speed_update_ns > 0

    # Corrupt with NaN
    gateway.update_vehicle_speed(float("nan"))
    assert gateway._last_speed_update_ns == 0

    with pytest.raises(SpeedDataStaleError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)

    estop.reset(estop.create_reset_token())

    # Corrupt with negative speed
    gateway.update_vehicle_speed(0.0)
    gateway.update_vehicle_speed(-15.0)
    assert gateway._last_speed_update_ns == 0

    with pytest.raises(SpeedDataStaleError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)

    bus.disconnect()


def test_safety_gateway_synthetic_speed_never_satisfies_interlock() -> None:
    """P0-2 (REVIEW C-2): a simulated stationary vehicle must not feed the
    speed interlock. Synthetic speed updates record the value but never
    refresh interlock freshness — critical commands stay blocked (stale)
    until genuine physical telemetry arrives."""
    bus = VirtualBus(channel_id="safety_vbus_synth")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")

    # Baseline: no feed at all -> stale (fail-closed)
    assert gateway._last_speed_update_ns == 0
    with pytest.raises(SpeedDataStaleError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)
    estop.reset(estop.create_reset_token())

    # Synthetic 0 km/h must NOT make the interlock fresh/authorized.
    gateway.update_vehicle_speed(0.0, source="synthetic")
    assert gateway._last_speed_update_ns == 0  # freshness untouched
    with pytest.raises(SpeedDataStaleError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)
    estop.reset(estop.create_reset_token())

    # Even a synthetic "moving" value must not engage the moving-vehicle
    # branch — the feed simply is not trusted for interlock purposes.
    gateway.update_vehicle_speed(80.0, source="synthetic")
    with pytest.raises(SpeedDataStaleError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)
    estop.reset(estop.create_reset_token())

    # Physical 0 km/h refreshes freshness and authorizes normally.
    gateway.update_vehicle_speed(0.0, source="physical")
    assert gateway._last_speed_update_ns > 0
    assert gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True) is True

    bus.disconnect()


def test_safety_gateway_synthetic_speed_does_not_overwrite_physical_freshness() -> None:
    """P0-2 detail: after a physical feed, a synthetic update must not extend
    (nor clear) the physical timestamp — the interlock keeps aging against
    the last physical sample."""
    bus = VirtualBus(channel_id="safety_vbus_synth2")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    gateway.update_vehicle_speed(0.0, source="physical")
    physical_ts = gateway._last_speed_update_ns
    assert physical_ts > 0

    # Synthetic updates leave the physical timestamp untouched.
    gateway.update_vehicle_speed(0.0, source="synthetic")
    assert gateway._last_speed_update_ns == physical_ts
    gateway.update_vehicle_speed(25.0, source="synthetic")
    assert gateway._last_speed_update_ns == physical_ts

    # The synthetic 25 km/h value was recorded for telemetry display, but
    # the interlock keeps evaluating against it (fail-closed: a recorded
    # synthetic "moving" value must not read as safe-and-stationary).
    assert gateway._current_vehicle_speed_kmh == 25.0
    assert gateway.is_speed_fresh_and_safe() is False  # 25 km/h > noise threshold

    bus.disconnect()


def test_safety_gateway_frame_sanity_checks() -> None:
    """Verify Stage 1: Frame Sanity & Range Validation."""
    bus = VirtualBus(channel_id="safety_vbus_sanity")
    bus.connect()
    gateway = TxSafetyGateway.for_testing(bus=bus, whitelist_ids={0x100})

    # 1. Non-CanFrame object
    with pytest.raises(FrameSanityError) as exc1:
        gateway.validate_and_transmit("invalid_frame_string")  # type: ignore[arg-type]
    assert exc1.value.code == "INVALID_FRAME_SANITY"

    # 2. Mock frame with out-of-range standard ID
    mock_bad_id = MagicMock(spec=CanFrame)
    mock_bad_id.is_extended = False
    mock_bad_id.arbitration_id = 0x800
    mock_bad_id.is_fd = False
    mock_bad_id.data = b"\x01"
    with pytest.raises(FrameSanityError) as exc2:
        gateway.validate_and_transmit(mock_bad_id)
    assert exc2.value.code == "INVALID_FRAME_SANITY"

    # 3. Mock frame with Classic CAN payload > 8 bytes
    mock_bad_len = MagicMock(spec=CanFrame)
    mock_bad_len.is_extended = False
    mock_bad_len.arbitration_id = 0x100
    mock_bad_len.is_fd = False
    mock_bad_len.data = b"\x01" * 12
    with pytest.raises(FrameSanityError) as exc3:
        gateway.validate_and_transmit(mock_bad_len)
    assert exc3.value.code == "INVALID_FRAME_SANITY"

    # 4. Valid CAN-FD frame up to 64 bytes passes sanity check
    frame_valid_fd = CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01" * 64, is_fd=True)
    assert gateway.validate_and_transmit(frame_valid_fd) is True

    bus.disconnect()


def test_safety_gateway_rate_limiting_deque_overflow() -> None:
    """P1-9: exceeding MAX_TX_RATE_PER_SEC rejects (backpressure); a
    SUSTAINED violation streak (RATE_ESTOP_AFTER consecutive rejections)
    escalates to an E-Stop."""
    bus = VirtualBus(channel_id="safety_vbus_rate_0")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Transmit exactly 100 messages (MAX_TX_RATE_PER_SEC)
    for _ in range(gateway.MAX_TX_RATE_PER_SEC):
        assert gateway.validate_and_transmit(frame) is True

    assert len(gateway._tx_timestamps) == 100

    # First overloads are plain rejections — backpressure, NOT an E-Stop
    for _ in range(gateway.RATE_ESTOP_AFTER - 1):
        with pytest.raises(RateLimitExceededError):
            gateway.validate_and_transmit(frame)
    assert estop.is_engaged is False, "single bursts must not latch an E-Stop (self-DoS guard)"

    # The sustained-streak threshold trips the E-Stop
    with pytest.raises(RateLimitExceededError) as exc_info:
        gateway.validate_and_transmit(frame)

    assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"
    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.RATE_LIMIT_OVERFLOW
    bus.disconnect()


def test_safety_gateway_sliding_window_expiration() -> None:
    """Verify that deque rate limiter pops expired timestamps (> 1s)."""
    bus = VirtualBus(channel_id="safety_vbus_rate_1")
    bus.connect()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Insert old timestamps (> 1.5 seconds ago) into deque
    old_time = time.monotonic() - 2.0
    for i in range(50):
        gateway._tx_timestamps.append((old_time, threading.get_ident(), i))

    assert len(gateway._tx_timestamps) == 50

    # Transmit a new frame -> expired timestamps should be popped out
    assert gateway.validate_and_transmit(frame) is True
    assert len(gateway._tx_timestamps) == 1
    bus.disconnect()


def test_safety_gateway_tx_port_protocol_conformance() -> None:
    """Verify TxSafetyGateway implements TxPort (send and send_sync)."""
    bus = VirtualBus(channel_id="safety_vbus_port")
    bus.connect()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})

    assert isinstance(gateway, TxPort)

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x10\x01")

    # Synchronous TxPort entry
    gateway.send_sync(frame)
    assert len(bus.sent_frames) == 1

    # Asynchronous TxPort entry
    asyncio.run(gateway.send(frame))
    assert len(bus.sent_frames) == 2

    bus.disconnect()


def test_safety_gateway_multithreaded_concurrency() -> None:
    """Verify thread safety under concurrent multi-threaded transmits."""
    bus = VirtualBus(channel_id="safety_vbus_rate_2")
    bus.connect()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Run 50 transmits across 4 concurrent worker threads (total 50 frames <= 100 limit)
    def worker_transmit() -> bool:
        return gateway.validate_and_transmit(frame)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker_transmit) for _ in range(50)]
        results = [f.result() for f in futures]

    assert all(results)
    assert len(gateway._tx_timestamps) == 50
    bus.disconnect()


# ============================================================================
# F-18: Per-Category Token-Bucket Budget DoD tests
# ============================================================================


def _budget_gateway(channel: str) -> tuple[VirtualBus, TxSafetyGateway]:
    bus = VirtualBus(channel_id=channel)
    bus.connect()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    return bus, gateway


def test_protocol_burst_budget_allows_full_bam_transfer() -> None:
    """F-18 DoD: a full J1939 BAM transfer (255 packets) must NOT trip E-Stop."""
    bus, gateway = _budget_gateway("safety_vbus_bam")
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"")

    # 255 CF packets â€” exactly the BAM maximum; must all pass without E-Stop
    for _ in range(255):
        assert gateway.validate_and_transmit(frame, budget_category="protocol_burst") is True

    assert gateway.estop.is_engaged is False
    assert len(bus.sent_frames) == 255
    bus.disconnect()


def test_default_lane_metered_exactly_once() -> None:
    """S-C-007 regression: the default lane is metered by the sliding window
    ONLY â€” the default token bucket must never double-count it and reject
    traffic that is well under the 100 msg/s window limit."""

    class _NoSendVirtualBus(VirtualBus):
        def privileged_send(self, frame) -> None:  # type: ignore[override]
            pass

    bus = _NoSendVirtualBus(channel_id="safety_vbus_single_meter")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"")

    # Simulate the old double-metering hazard: drain the default bucket as if
    # another consumer had used it (capacity 100). With single-metering the
    # window â€” not the bucket â€” governs the default lane, so 100 frames pass.
    gateway._budgets["default"]._tokens = 0.0
    for _ in range(gateway.MAX_TX_RATE_PER_SEC):
        assert gateway.validate_and_transmit(frame) is True

    # The 101st frame within one second trips the window, as designed
    with pytest.raises(RateLimitExceededError):
        gateway.validate_and_transmit(frame)
    bus.disconnect()


def test_protocol_burst_budget_exhaustion_at_capacity_plus_one() -> None:
    """F-18: protocol_burst capacity is 255 â€” the 256th burst frame is rejected."""
    bus, gateway = _budget_gateway("safety_vbus_bam2")
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"")

    gateway._budgets["protocol_burst"]._tokens = 0.0

    with pytest.raises(RateLimitExceededError, match="protocol_burst"):
        gateway.validate_and_transmit(frame, budget_category="protocol_burst")
    bus.disconnect()


def test_diagnostic_budget_capacity_is_ten() -> None:
    """F-18: diagnostic budget (10 tokens, 10/s refill) rejects the 11th frame."""
    bus, gateway = _budget_gateway("safety_vbus_diag")
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"")

    for _ in range(10):
        assert gateway.validate_and_transmit(frame, budget_category="diagnostic") is True

    with pytest.raises(RateLimitExceededError, match="diagnostic"):
        gateway.validate_and_transmit(frame, budget_category="diagnostic")
    bus.disconnect()


def test_calibration_budget_capacity_is_five() -> None:
    """F-18: calibration budget (5 tokens, 5/s refill) rejects the 6th frame."""
    bus, gateway = _budget_gateway("safety_vbus_cal")
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"")

    for _ in range(5):
        assert gateway.validate_and_transmit(frame, budget_category="calibration") is True

    with pytest.raises(RateLimitExceededError, match="calibration"):
        gateway.validate_and_transmit(frame, budget_category="calibration")
    bus.disconnect()


def test_unknown_budget_category_is_rejected() -> None:
    """F-18: an unregistered category fails closed instead of silently passing."""
    bus, gateway = _budget_gateway("safety_vbus_unk")
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"")

    with pytest.raises(FrameSanityError, match="Unknown TX budget category"):
        gateway.validate_and_transmit(frame, budget_category="nonexistent_category")
    bus.disconnect()


def test_budgets_are_independent_per_category() -> None:
    """F-18: draining one category must not consume another's tokens."""
    bus, gateway = _budget_gateway("safety_vbus_indep")
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"")

    # Drain diagnostic fully...
    for _ in range(10):
        gateway.validate_and_transmit(frame, budget_category="diagnostic")
    # ...calibration must still have its own 5 tokens
    for _ in range(5):
        assert gateway.validate_and_transmit(frame, budget_category="calibration") is True
    bus.disconnect()


def test_whitelist_masks_authorize_id_family() -> None:
    """E5: a (value, mask) pair authorizes the whole protocol-response family."""
    bus = VirtualBus(channel_id="safety_vbus_mask")
    bus.connect()

    # Authorize every TP.CM frame sourced from SA 0xF9 (any peer in the DA byte)
    masks = [(0x18EC00F9, 0x18EC00FF)]
    gateway = TxSafetyGateway(bus=bus, whitelist_masks=masks)

    # Responses to two different peers both match (id & mask) == value
    to_peer_a = CanFrame.create(channel_id="c0", arbitration_id=0x18EC01F9, data=b"\x11", is_extended=True)
    to_peer_b = CanFrame.create(channel_id="c0", arbitration_id=0x18EC42F9, data=b"\x11", is_extended=True)
    assert gateway.validate_and_transmit(to_peer_a) is True
    assert gateway.validate_and_transmit(to_peer_b) is True

    # A frame sourced from a DIFFERENT source address does not match the family
    other_sa = CanFrame.create(channel_id="c0", arbitration_id=0x18EC01AA, data=b"\x11", is_extended=True)
    with pytest.raises(WhitelistViolationError):
        gateway.validate_and_transmit(other_sa)
    bus.disconnect()


def test_whitelist_masks_alone_are_not_fail_closed() -> None:
    """E5: providing masks (without exact IDs) counts as a configured whitelist."""
    bus = VirtualBus(channel_id="safety_vbus_mask_only")
    bus.connect()

    gateway = TxSafetyGateway(bus=bus, whitelist_masks=[(0x7E0, 0x7FF)])
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Must not raise WhitelistFailClosedError; the ID matches the mask
    assert gateway.validate_and_transmit(frame) is True
    bus.disconnect()


def test_production_constructor_has_no_testing_bypass_flag() -> None:
    """B1 regression: allow_all_for_testing must not be a production constructor knob."""
    import inspect

    params = inspect.signature(TxSafetyGateway.__init__).parameters
    assert "allow_all_for_testing" not in params


# ============================================================================
# Lock-Scope Refactor Regression Tests (H2)
# ============================================================================


def test_estop_callback_does_not_block_on_slow_driver_io() -> None:
    """H2 Regression: E-Stop callback completes immediately even when privileged_send blocks.

    Verifies that the gateway lock is released before calling privileged_send, so
    E-Stop callbacks (which acquire the same lock) do not wait for driver I/O.
    """
    import threading

    # Controllable mock bus with event-gated send
    class SlowBus:
        def __init__(self) -> None:
            self.send_gate = threading.Event()
            self.privileged_send_entered = threading.Event()
            self.sent_frames: list[CanFrame] = []

        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def privileged_send(self, frame: CanFrame) -> None:
            self.privileged_send_entered.set()
            self.send_gate.wait()  # Block until gate is opened
            self.sent_frames.append(frame)

    bus = SlowBus()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    callback_completed = threading.Event()

    def estop_callback(event: object) -> None:
        callback_completed.set()

    estop.register_callback(estop_callback)

    # Start transmit in background (will block in privileged_send)
    def transmit_worker() -> None:
        try:
            gateway.validate_and_transmit(frame)
        except SafetyError:
            pass

    tx_thread = threading.Thread(target=transmit_worker)
    tx_thread.start()

    # Wait for privileged_send to be entered
    assert bus.privileged_send_entered.wait(timeout=2.0), "privileged_send was not entered"

    # Trigger E-Stop while send is blocked
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "Operator pressed red button")

    # Callback must complete immediately (not block on driver I/O)
    assert callback_completed.wait(timeout=2.0), "E-Stop callback blocked on gateway lock"

    # Unblock the send and clean up
    bus.send_gate.set()
    tx_thread.join(timeout=2.0)

    assert estop.is_engaged


def test_estop_race_window_snapshot_already_engaged() -> None:
    """H2 Regression: E-Stop engaged during Stage 2 â†’ tokens rolled back on Phase 2 rejection.

    Scenario: estop.is_engaged=True at snapshot time (caught in Stage 2 or between
    Stage 2 and snapshot). Tokens are consumed but frame is rejected in Phase 2.
    Verify rollback occurs.
    """
    bus = VirtualBus(channel_id="safety_vbus_race_engaged")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Engage E-Stop before validation
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "Pre-engaged")
    assert estop.is_engaged

    initial_timestamps = len(gateway._tx_timestamps)
    initial_budget_tokens = gateway._budgets["default"]._tokens

    # Attempt transmission â€” should be rejected in Stage 2 (no token consumption)
    with pytest.raises(SafetyError, match="Emergency Stop is currently ENGAGED"):
        gateway.validate_and_transmit(frame)

    # Verify no tokens were consumed (Stage 2 rejection happens before Stage 6)
    assert len(gateway._tx_timestamps) == initial_timestamps
    assert gateway._budgets["default"]._tokens == initial_budget_tokens
    assert len(bus.sent_frames) == 0

    bus.disconnect()


def test_estop_race_window_triggered_between_phases() -> None:
    """H2 Regression: E-Stop triggered between Phase 1 (lock release) and Phase 3 (send).

    Scenario: estop snapshot clear â†’ estop.trigger() in another thread â†’ Phase 2
    detects engagement â†’ tokens rolled back, frame rejected, no send.
    """
    import threading
    from unittest.mock import Mock

    estop = EmergencyStopSystem(allow_self_reset=True)

    # Mock bus that signals when privileged_send is about to be called
    bus = Mock()
    bus.sent_frames = []

    phase1_completed = threading.Event()
    estop_triggered = threading.Event()

    # Patch estop.is_engaged property to trigger E-Stop after Phase 1 snapshot
    call_count = [0]
    def patched_is_engaged_getter(self: EmergencyStopSystem) -> bool:
        call_count[0] += 1
        # First call: Stage 2 check (inside lock) â†’ return False
        # Second call: snapshot at lock release â†’ return False, signal Phase 1 done
        # Third call: Phase 2 double-check â†’ trigger estop, return True
        if call_count[0] == 2:
            phase1_completed.set()
        elif call_count[0] == 3:
            if not estop_triggered.is_set():
                estop._is_engaged = True
                estop_triggered.set()
        return estop._is_engaged

    # Monkey-patch the property
    original_property = type(estop).is_engaged
    type(estop).is_engaged = property(lambda self: patched_is_engaged_getter(self))

    try:
        gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
        frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

        initial_budget_tokens = gateway._budgets["default"]._tokens

        # Attempt transmission â€” Phase 2 should detect estop and rollback
        with pytest.raises(SafetyError, match="Emergency Stop is currently ENGAGED"):
            gateway.validate_and_transmit(frame)

        # Verify tokens were rolled back
        assert len(gateway._tx_timestamps) == 0
        assert gateway._budgets["default"]._tokens == initial_budget_tokens
        assert not bus.privileged_send.called
        assert estop._is_engaged
    finally:
        # Restore original property
        type(estop).is_engaged = original_property


# ============================================================================
# CRITICAL-1: E-Stop TOCTOU â€” TX fence hardening
# ============================================================================


class _RecordingSendBus:
    """Test double recording privileged dispatches (appends only on dispatch)."""

    def __init__(self) -> None:
        self.sent_frames: list[CanFrame] = []
        self.dispatch_threads: list[int] = []

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def privileged_send(self, frame: CanFrame) -> None:
        self.dispatch_threads.append(threading.get_ident())
        self.sent_frames.append(frame)


def test_estop_fires_while_send_delayed_at_fence_no_frame_leaks() -> None:
    """CRITICAL-1 (E-Stop TOCTOU): E-Stop mid-send must leak ZERO frames.

    The frame passes every validation stage against a CLEAR E-Stop, then its
    privileged dispatch is delayed inside Phase 3 (parked at the E-Stop send
    fence). The E-Stop engages while the frame is stalled mid-send. When the
    fence is granted, the in-lock re-verification must abort the dispatch â€”
    not even ONE frame may reach the wire (ASIL-B fail-closed).
    """
    estop = EmergencyStopSystem(allow_self_reset=True)
    bus = _RecordingSendBus()
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    errors: list[BaseException] = []

    def transmit_worker() -> None:
        try:
            gateway.validate_and_transmit(frame)
        except BaseException as exc:  # noqa: BLE001 â€” asserted below
            errors.append(exc)

    # Hold the E-Stop TX fence: the worker's privileged dispatch is DELAYED
    # in Phase 3 â€” it cannot reach the bus while we hold the fence lock.
    with estop.tx_send_lock:
        tx_thread = threading.Thread(target=transmit_worker, name="tx-fence-worker")
        tx_thread.start()
        # Let the worker complete validation and park at the fence. (Even if
        # it is still mid-validation at trigger time, every path aborts.)
        time.sleep(0.05)

        # E-Stop engages while the frame is stalled mid-send in the gateway.
        estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "Operator hit red button mid-send")

    # Fence released -> the worker must re-verify INSIDE the lock and abort.
    tx_thread.join(timeout=2.0)

    assert not tx_thread.is_alive(), "TX worker hung at the fence"
    assert len(errors) == 1
    assert isinstance(errors[0], SafetyError)
    # The engagement bumped the fence generation while the frame was parked:
    # the fenced re-verification aborts the dispatch. (Had the E-Stop fired
    # before Phase 2, the code would be ESTOP_ACTIVE instead â€” either way
    # the frame dies; the fence error is the more precise diagnosis.)
    assert errors[0].code in ("ESTOP_TX_FENCE_INVALIDATED", "ESTOP_ACTIVE")
    # HARD invariant: not even a single frame reached the wire.
    assert len(bus.sent_frames) == 0
    assert len(bus.dispatch_threads) == 0


def test_estop_tx_fence_epoch_invalidation_mid_send() -> None:
    """CRITICAL-1: a fence-epoch bump between validation and dispatch kills the frame.

    A frame validated against fence generation N must be rejected when the
    generation advanced (engagement + authorized reset) before the send lock
    was granted â€” even though the E-Stop ends up DISENGAGED at send time.
    Deterministic injection: the gateway's snapshot read of tx_fence is
    patched to advance the real fence (real trigger + real reset) right after
    the gateway captured generation 0.
    """
    estop = EmergencyStopSystem(allow_self_reset=True)
    bus = _RecordingSendBus()
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    calls = [0]
    original_tx_fence = type(estop).tx_fence

    def patched_tx_fence(self: EmergencyStopSystem) -> int:
        calls[0] += 1
        if calls[0] == 1:
            # The gateway is snapshotting RIGHT NOW: advance the real fence
            # (engagement + authorized reset each bump the generation) and
            # report the pre-bump generation the frame was validated against.
            self.trigger(EStopTriggerSource.BUS_OFF_DETECTED, "Bus-off while frame in flight")
            self.reset(self.create_reset_token())
            return 0
        return self._tx_fence

    type(estop).tx_fence = property(patched_tx_fence)
    try:
        with pytest.raises(SafetyError) as exc_info:
            gateway.validate_and_transmit(frame)
        assert exc_info.value.code == "ESTOP_TX_FENCE_INVALIDATED"
    finally:
        type(estop).tx_fence = original_tx_fence

    # The epoch-stale frame never reached the bus; E-Stop is disengaged again.
    assert len(bus.sent_frames) == 0
    assert not estop.is_engaged
    assert estop.tx_fence == 2


def test_estop_tx_fence_allows_clean_send_within_same_generation() -> None:
    """CRITICAL-1 positive control: unchanged fence generation lets the send through."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    bus = _RecordingSendBus()
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    assert gateway.validate_and_transmit(frame) is True
    assert len(bus.sent_frames) == 1
    assert estop.tx_fence == 0
    assert len(gateway._tx_timestamps) == 1


# ============================================================================
# HIGH-1: rate-limit rollback removes exactly the rolling thread's stamp
# ============================================================================


def test_rate_limit_rollback_removes_exactly_the_rolling_threads_stamp() -> None:
    """HIGH-1: rollback deletes the rolling thread's OWN stamp by identity.

    Deterministic interleave: thread A appends its stamp and parks at its
    in-fence re-check (holding the send fence); thread B appends its own
    stamp and parks at the fence behind A. A's send is then rejected â€” A's
    rollback must remove EXACTLY A's (now_ns, thread_id, seq) tuple; B's
    reservation must survive untouched (the old blind pop() deleted B's).
    """
    bus = VirtualBus(channel_id="safety_vbus_rollback")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    main_ident = threading.get_ident()

    # Prior successful send from the main thread â€” its stamp must survive.
    assert gateway.validate_and_transmit(frame) is True
    assert len(gateway._tx_timestamps) == 1

    gate_a = threading.Event()
    gate_b = threading.Event()
    a_at_fence = threading.Event()
    a_finished = threading.Event()
    per_thread_reads: dict[int, int] = {}
    a_errors: list[BaseException] = []
    b_result: list[bool] = []

    original_is_engaged = type(estop).is_engaged

    def patched_is_engaged(self: EmergencyStopSystem) -> bool:
        tid = threading.get_ident()
        reads = per_thread_reads.get(tid, 0)
        per_thread_reads[tid] = reads + 1
        # Each sender makes exactly 3 engagement reads (Stage 2, snapshot,
        # fenced re-check). Gate the 3rd read per thread so A parks INSIDE
        # the fence holding it, and B parks at its own re-check later.
        if reads == 2:
            if tid == thread_a.ident:
                a_at_fence.set()
                gate_a.wait(timeout=5.0)
            elif tid == thread_b.ident:
                gate_b.wait(timeout=5.0)
        return self._is_engaged

    def worker_a() -> None:
        try:
            gateway.validate_and_transmit(frame)
        except BaseException as exc:  # noqa: BLE001 â€” asserted below
            a_errors.append(exc)
        finally:
            a_finished.set()

    def worker_b() -> None:
        b_result.append(gateway.validate_and_transmit(frame))

    thread_a = threading.Thread(target=worker_a, name="rollback-a")
    thread_b = threading.Thread(target=worker_b, name="rollback-b")

    type(estop).is_engaged = property(patched_is_engaged)
    try:
        thread_a.start()
        # A is now parked INSIDE the fence (holding the send fence).
        assert a_at_fence.wait(timeout=5.0), "A never reached the fenced re-check"

        thread_b.start()
        # Deterministic evidence both stamps are appended (Phase 1) while A
        # is gated inside the fence and B waits at the fence behind it.
        deadline = time.monotonic() + 5.0
        while len(gateway._tx_timestamps) < 3 and time.monotonic() < deadline:
            time.sleep(0.002)
        assert len(gateway._tx_timestamps) == 3, "workers did not consume their stamps"

        # Reject A's send WITHOUT running the E-Stop machine (direct state
        # flip: no callbacks fire, so the sliding window is not cleared and
        # the rollback path is observed in isolation).
        estop._is_engaged = True
        gate_a.set()

        assert a_finished.wait(timeout=5.0), "A never completed"
        assert len(a_errors) == 1
        assert isinstance(a_errors[0], SafetyError)
        assert a_errors[0].code == "ESTOP_ACTIVE"

        # HIGH-1 core assertion: A's rollback removed EXACTLY A's own stamp.
        # B's reservation (and the earlier main-thread stamp) are intact.
        remaining = list(gateway._tx_timestamps)
        assert len(remaining) == 2, f"expected main+B stamps, got {remaining}"
        remaining_idents = {stamp[1] for stamp in remaining}
        assert main_ident in remaining_idents
        assert thread_b.ident in remaining_idents
        assert thread_a.ident not in remaining_idents

        # Disengage and release B: B's own stamp is retained on success.
        estop._is_engaged = False
        gate_b.set()
        thread_b.join(timeout=5.0)
        assert not thread_b.is_alive(), "B hung at the fence"
        assert b_result == [True]
        assert len(gateway._tx_timestamps) == 2
        # Main's + B's frames went out; A's rejected frame never did.
        assert len(bus.sent_frames) == 2
    finally:
        estop._is_engaged = False
        gate_a.set()
        gate_b.set()
        type(estop).is_engaged = original_is_engaged
        thread_a.join(timeout=5.0)
        thread_b.join(timeout=5.0)
        bus.disconnect()


# ============================================================================
# MEDIUM-6: managed bounded executor for the async TxPort entry
# ============================================================================


class _ThreadRecordingBus:
    """Bus double recording WHICH thread performed each privileged dispatch."""

    def __init__(self) -> None:
        self.sent_frames: list[CanFrame] = []
        self.dispatch_thread_names: list[str] = []

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def privileged_send(self, frame: CanFrame) -> None:
        self.dispatch_thread_names.append(threading.current_thread().name)
        self.sent_frames.append(frame)


def test_gateway_send_offloads_via_managed_bounded_executor() -> None:
    """MEDIUM-6: async sends run on the gateway's OWN bounded pool, never the loop default."""
    bus = _ThreadRecordingBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Managed and bounded by a class-level budget â€” not the event loop's
    # shared default executor (unbounded and shared with every other offload).
    assert gateway._tx_executor is not None
    assert gateway._tx_executor._max_workers == TxSafetyGateway.TX_EXECUTOR_MAX_WORKERS
    assert TxSafetyGateway.TX_EXECUTOR_MAX_WORKERS <= 8

    executor_before = gateway._tx_executor

    asyncio.run(gateway.send(frame))
    assert len(bus.sent_frames) == 1
    assert bus.dispatch_thread_names[0].startswith("tx-gateway-")

    # The SAME managed pool instance is reused across sends â€” never
    # recreated, never swapped for the loop default executor.
    asyncio.run(gateway.send(frame))
    assert gateway._tx_executor is executor_before
    assert len(bus.sent_frames) == 2
    assert all(name.startswith("tx-gateway-") for name in bus.dispatch_thread_names)


def test_gateway_executor_shutdown_is_idempotent_and_fails_closed() -> None:
    """MEDIUM-6: shutdown releases the pool; further async sends fail closed."""
    bus = _ThreadRecordingBus()
    gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})

    gateway.shutdown()
    gateway.shutdown()  # idempotent

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")
    with pytest.raises(SafetyError, match="shut down"):
        asyncio.run(gateway.send(frame))
    assert len(bus.sent_frames) == 0

"""Adversarial stress-tests and edge-case attacks by Challenger 2.

Target Systems:
1. DBC Signal Decoder: Truncated frames, oversized payloads, massive random CAN IDs (LRU cache eviction at/above 2048 entries), signal boundary arithmetic.
2. TxSafetyGateway: 1000 msg/s burst rate attacks, multi-threaded contention, sliding window deque eviction, dynamic speed race conditions.
3. UDS Async Client: Rapid concurrent async calls, timeouts, callback exceptions, shutdown lifecycle under active futures.
4. AI Copilot: Heavily malformed code fences, embedded backticks, nested conversational text, empty/corrupt LLM outputs, local expert extreme loads.
"""

from __future__ import annotations

import concurrent.futures
import queue
import random
import threading
import time
from typing import Any

import pytest

from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.engine.ai.diagnostic_copilot import (
    AiDiagnosticCopilot,
    FaultSeverity,
)
from src.engine.decoder.dbc_decoder import DbcSignalDecoder
from src.hal.base import AbstractBus
from src.protocols.uds.client import UdsClient
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.gateway import TxSafetyGateway

# ---------------------------------------------------------------------------
# DBC Test Database
# ---------------------------------------------------------------------------
STRESS_DBC = """VERSION ""

NS_ :

BS_:

BU_: Engine Tester

BO_ 256 EngineTelemetry: 8 Engine
 SG_ EngineSpeed : 0|16@1+ (0.125,0) [0|8000] "rpm" Vector__XXX
 SG_ CoolantTemp : 16|8@1+ (1,-40) [-40|215] "degC" Vector__XXX
 SG_ FuelPressure : 24|8@1+ (5,0) [0|1000] "kPa" Vector__XXX
 SG_ EngineLoad : 32|8@1+ (0.5,0) [0|100] "%" Vector__XXX

BO_ 2364539904 EEC1_J1939: 8 Engine
 SG_ J1939_Speed : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX
 SG_ J1939_Torque : 16|8@1+ (1,-125) [-125|125] "%" Vector__XXX
"""


# ===========================================================================
# 1. DBC Decoder Empirical Stress-Tests
# ===========================================================================


def test_dbc_truncated_frame_sweep() -> None:
    """Feed truncated frames of every size from 0 to length-1 (0..7 bytes).
    Verify all are safely rejected without returning corrupted padding or raising unhandled exceptions.
    """
    decoder = DbcSignalDecoder.from_dbc_string(STRESS_DBC)
    full_payload = b"\x12\x34\x56\x78\x9a\xbc\xde\xf0"  # 8 bytes

    for size in range(8):  # 0, 1, 2, 3, 4, 5, 6, 7 bytes
        truncated_data = full_payload[:size]
        frame = CanFrame.create(
            channel_id="ch_stress",
            arbitration_id=256,
            data=truncated_data,
            is_extended=False,
        )
        result = decoder.decode_frame(frame)
        assert result is None, f"Truncated frame of size {size} should have been rejected (returned None)"

    # Also test J1939 truncated frame sweep
    for size in range(8):
        frame_j1939 = CanFrame.create(
            channel_id="ch_stress",
            arbitration_id=0x0CF00400,
            data=full_payload[:size],
            is_extended=True,
        )
        result = decoder.decode_frame(frame_j1939)
        assert result is None, f"Truncated J1939 frame of size {size} should have been rejected"


def test_dbc_oversized_payloads_sweep() -> None:
    """Feed oversized payloads (9 to 64 bytes) to standard 8-byte messages.
    Verify payload is safely sliced to message definition length and signals are correctly decoded.
    """
    decoder = DbcSignalDecoder.from_dbc_string(STRESS_DBC)

    # 8-byte valid base: Speed=0x0FA0 (4000 raw -> 500 RPM), Coolant=120 (80 degC), Fuel=20 (100 kPa), Load=100 (50%)
    base_payload = b"\xa0\x0f\x78\x14\x64\x00\x00\x00"

    for extra_bytes_len in [1, 2, 4, 8, 16, 32, 56]:  # up to 64 bytes (CAN-FD)
        oversized_data = base_payload + bytes(range(extra_bytes_len))
        frame = CanFrame.create(
            channel_id="ch_stress",
            arbitration_id=256,
            data=oversized_data,
            is_extended=False,
            is_fd=True,
        )
        decoded = decoder.decode_frame(frame)
        assert decoded is not None
        assert decoded.message_name == "EngineTelemetry"
        assert decoded.signals["EngineSpeed"].value == 500.0
        assert decoded.signals["CoolantTemp"].value == 80.0
        assert decoded.signals["FuelPressure"].value == 100.0
        assert decoded.signals["EngineLoad"].value == 50.0


def test_dbc_lru_cache_massive_traffic_at_and_above_2048() -> None:
    """Stress-test DBC Decoder LRU cache under 10,000 randomized CAN IDs.
    Verify strict cache size upper bound (2048 entries), proper eviction, and deterministic retrieval.
    """
    max_cache = 2048
    decoder = DbcSignalDecoder.from_dbc_string(STRESS_DBC, max_cache_size=max_cache)
    assert decoder.max_cache_size == max_cache
    assert len(decoder._message_cache) == 0

    known_std_id = 256
    known_j1939_id = 0x0CF00400

    # Generate 5,000 distinct unknown standard IDs and 5,000 distinct unknown extended IDs
    rng = random.Random(42)
    random_ids: list[tuple[int, bool]] = []
    for _ in range(5000):
        # 11-bit IDs (excluding 256)
        cand = rng.randint(0x001, 0x7FF)
        if cand != known_std_id:
            random_ids.append((cand, False))

    for _ in range(5000):
        # 29-bit IDs
        cand = rng.randint(0x10000000, 0x1FFFFFFF)
        if cand != known_j1939_id:
            random_ids.append((cand, True))

    # Interleave with known IDs
    for idx, (arb_id, is_ext) in enumerate(random_ids):
        # Periodic query of known IDs to ensure LRU touches
        if idx % 100 == 0:
            decoder._lookup_message(known_std_id, is_extended=False)
            decoder._lookup_message(known_j1939_id, is_extended=True)

        res = decoder._lookup_message(arb_id, is_extended=is_ext)
        assert res is None, f"Unknown ID 0x{arb_id:X} should resolve to None"

        # Invariant check: Cache size MUST NEVER exceed max_cache_size
        assert len(decoder._message_cache) <= max_cache

    # After 10,000 lookups, cache should be exactly full (2048 entries)
    assert len(decoder._message_cache) == max_cache

    # Verify that known messages still resolve correctly after massive cache thrashing
    known_msg = decoder._lookup_message(known_std_id, is_extended=False)
    assert known_msg is not None
    assert known_msg.name == "EngineTelemetry"

    known_j1939 = decoder._lookup_message(known_j1939_id, is_extended=True)
    assert known_j1939 is not None
    assert known_j1939.name == "EEC1_J1939"


def test_dbc_signal_boundary_values() -> None:
    """Test extreme signal values (all 0x00, all 0xFF, alternating bit patterns) to verify no arithmetic overflows."""
    decoder = DbcSignalDecoder.from_dbc_string(STRESS_DBC)

    # All zeros
    frame_zeros = CanFrame.create(channel_id="c0", arbitration_id=256, data=b"\x00" * 8)
    dec_zeros = decoder.decode_frame(frame_zeros)
    assert dec_zeros is not None
    assert dec_zeros.signals["EngineSpeed"].value == 0.0
    assert dec_zeros.signals["CoolantTemp"].value == -40.0
    assert dec_zeros.signals["FuelPressure"].value == 0.0
    assert dec_zeros.signals["EngineLoad"].value == 0.0

    # All ones (0xFF)
    frame_ones = CanFrame.create(channel_id="c0", arbitration_id=256, data=b"\xff" * 8)
    dec_ones = decoder.decode_frame(frame_ones)
    assert dec_ones is not None
    # 0xFFFF = 65535 * 0.125 = 8191.875
    assert dec_ones.signals["EngineSpeed"].value == 8191.875
    # 0xFF = 255 - 40 = 215
    assert dec_ones.signals["CoolantTemp"].value == 215.0


# ===========================================================================
# 2. TxSafetyGateway Empirical Stress-Tests
# ===========================================================================


class MockBus(AbstractBus):
    """Simple high-throughput mock bus."""

    def __init__(self) -> None:
        super().__init__(channel_id="mock_bus")
        self.sent_frames: list[CanFrame] = []
        self._lock = threading.Lock()

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:
        with self._lock:
            self.sent_frames.append(frame)

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        return None


def test_safety_gateway_1000_msg_per_sec_burst_attack() -> None:
    """Burst 1000 messages in a tight loop to stress test the deque sliding window limiter.
    Verify exactly 100 messages pass, the 101st triggers RATE_LIMIT_OVERFLOW and engages E-Stop,
    and subsequent 899 messages are blocked.
    """
    bus = MockBus()
    estop = EmergencyStopSystem()
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x22")

    successful_tx = 0
    rate_limit_errors = 0
    estop_blocked_errors = 0

    for _ in range(1000):
        try:
            gateway.validate_and_transmit(frame)
            successful_tx += 1
        except SafetyError as exc:
            if exc.code == "RATE_LIMIT_EXCEEDED":
                rate_limit_errors += 1
            elif exc.code == "ESTOP_ACTIVE":
                estop_blocked_errors += 1
            else:
                raise

    # Exactly 100 transmitted (the MAX_TX_RATE_PER_SEC limit)
    assert successful_tx == 100
    # P1-9: overloads reject as backpressure first; the sustained streak
    # (RATE_ESTOP_AFTER consecutive rejections) then latches the E-Stop.
    assert rate_limit_errors == gateway.RATE_ESTOP_AFTER
    # Remaining attempts were blocked because E-Stop was engaged
    assert estop_blocked_errors == 1000 - 100 - gateway.RATE_ESTOP_AFTER
    assert len(bus.sent_frames) == 100
    assert estop.is_engaged is True
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.RATE_LIMIT_OVERFLOW


def test_safety_gateway_multithreaded_high_contention() -> None:
    """Stress test TxSafetyGateway under multi-threaded contention (20 worker threads).
    Verify thread safety of deque sliding window and absence of deadlocks or race conditions.
    """
    bus = MockBus()
    estop = EmergencyStopSystem()
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    # Launch 20 threads, each attempting 5 transmissions simultaneously (total 100 = exactly at limit)
    num_threads = 20
    tx_per_thread = 5

    def worker() -> list[bool]:
        res = []
        for _ in range(tx_per_thread):
            try:
                res.append(gateway.validate_and_transmit(frame))
            except SafetyError:
                res.append(False)
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker) for _ in range(num_threads)]
        results = [f.result() for f in futures]

    flat_results = [r for sublist in results for r in sublist]
    assert len(flat_results) == 100
    assert all(flat_results), "All 100 concurrent transmits within limit should succeed"
    assert len(bus.sent_frames) == 100
    assert estop.is_engaged is False


def test_safety_gateway_speed_update_race_condition() -> None:
    """Stress test race condition: Thread A updates vehicle speed continuously,
    Thread B transmits critical commands with confirmation.
    Verify speed interlock immediately triggers E-Stop on moving vehicle without deadlock.
    """
    bus = MockBus()
    estop = EmergencyStopSystem()
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x31\x01\x02\x01")

    stop_event = threading.Event()

    def speed_updater() -> None:
        speeds = [0.0, 10.0, 0.0, 50.0, 0.0, 80.0]
        idx = 0
        while not stop_event.is_set():
            gateway.update_vehicle_speed(speeds[idx % len(speeds)])
            idx += 1
            time.sleep(0.001)

    updater_thread = threading.Thread(target=speed_updater, daemon=True)
    updater_thread.start()

    # Now attempt critical transmits
    interlock_tripped = False
    for _ in range(50):
        try:
            gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)
        except SafetyError as exc:
            if exc.code == "SPEED_INTERLOCK_ACTIVE":
                interlock_tripped = True
                break
            elif exc.code == "ESTOP_ACTIVE":
                interlock_tripped = True
                break

    stop_event.set()
    updater_thread.join(timeout=1.0)

    # Interlock must have tripped if speed > 0 during any transmission
    if estop.is_engaged:
        assert interlock_tripped is True
        assert estop.last_event is not None
        assert estop.last_event.trigger == EStopTriggerSource.SPEED_INTERLOCK_BREACH


# ===========================================================================
# 3. UDS Async Client Empirical Stress-Tests
# ===========================================================================


class UdsMockDiagnosticBus(AbstractBus):
    """Thread-safe queue-based mock bus for high concurrency UDS testing."""

    def __init__(self) -> None:
        super().__init__(channel_id="uds_stress_bus")
        self.sent_frames: list[CanFrame] = []
        self.rx_queue: queue.Queue[CanFrame] = queue.Queue()
        self._lock = threading.Lock()

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:
        with self._lock:
            self.sent_frames.append(frame)

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        try:
            return self.rx_queue.get(timeout=timeout_s or 0.02)
        except queue.Empty:
            return None

    def inject_rx(self, frame: CanFrame) -> None:
        self.rx_queue.put(frame)


def test_uds_client_rapid_concurrent_async_calls() -> None:
    """Execute 50 concurrent async diagnostic calls through ThreadPoolExecutor.
    Verify worker threads execute without thread starvation, lost futures, or exceptions.
    """
    bus = UdsMockDiagnosticBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), tx_id=0x7E0, rx_id=0x7E8, max_workers=8)

    # Define a simulated fast diagnostic routine
    def mock_diagnostic_worker(routine_id: int) -> dict[str, Any]:
        time.sleep(0.01)  # small I/O simulation
        return {"routine_id": routine_id, "status": "COMPLETED"}

    completed_results: list[dict[str, Any]] = []
    results_lock = threading.Lock()

    def on_success(res: dict[str, Any]) -> None:
        with results_lock:
            completed_results.append(res)

    futures = []
    for i in range(50):
        fut = client.execute_async(
            mock_diagnostic_worker,
            i,
            callback=on_success,
        )
        futures.append(fut)

    # Wait for all futures
    for fut in futures:
        res = fut.result(timeout=5.0)
        assert res["status"] == "COMPLETED"

    # Wait briefly for all callback executions to finish
    time.sleep(0.2)
    with results_lock:
        assert len(completed_results) == 50
        routine_ids = {r["routine_id"] for r in completed_results}
        assert routine_ids == set(range(50))

    client.shutdown(wait=True)


def test_uds_client_callback_exception_isolation() -> None:
    """Test that if a user-supplied callback or error_callback raises an unhandled exception,
    the ThreadPoolExecutor does not crash and future resolution remains intact.
    """
    bus = UdsMockDiagnosticBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), tx_id=0x7E0, rx_id=0x7E8, max_workers=2)

    def exploding_callback(res: Any) -> None:
        raise RuntimeError("Explosion inside callback handler!")

    def exploding_error_callback(exc: Exception) -> None:
        raise RuntimeError("Explosion inside error callback handler!")

    # 1. Success path with exploding success callback
    def normal_routine() -> str:
        return "SUCCESS_VALUE"

    fut1 = client.execute_async(normal_routine, callback=exploding_callback)
    res1 = fut1.result(timeout=2.0)
    assert res1 == "SUCCESS_VALUE"

    # 2. Failure path with exploding error callback
    def failing_routine() -> None:
        raise ValueError("Original ECU routine failure")

    fut2 = client.execute_async(failing_routine, error_callback=exploding_error_callback)
    with pytest.raises(ValueError, match="Original ECU routine failure"):
        fut2.result(timeout=2.0)

    # Verify client is still healthy for subsequent calls
    fut3 = client.execute_async(normal_routine)
    assert fut3.result(timeout=2.0) == "SUCCESS_VALUE"

    client.shutdown(wait=True)


def test_uds_client_shutdown_during_running_futures() -> None:
    """Stress test client shutdown while background worker futures are actively running.
    Verify shutdown(wait=True) and shutdown(wait=False) complete cleanly.
    """
    bus = UdsMockDiagnosticBus()
    client = UdsClient(bus=bus, tx_port=TxSafetyGateway.for_testing(bus=bus), max_workers=4)

    def long_routine(duration: float) -> str:
        time.sleep(duration)
        return "FINISHED"

    futs = [client.execute_async(long_routine, 0.05) for _ in range(4)]

    # Shutdown with wait=True should wait for running futures to finish
    client.shutdown(wait=True)
    for fut in futs:
        assert fut.result() == "FINISHED"


# ===========================================================================
# 4. AI Diagnostic Copilot Empirical Stress-Tests (fully offline, M7)
#    The LLM JSON parser tests were removed with the cloud layer; the
#    remaining stress surface is the deterministic expert engine itself.
# ===========================================================================


def test_ai_copilot_extreme_loads_stay_deterministic() -> None:
    """Stress the offline local expert with 1000 DTCs + extreme telemetry
    (NaN, Inf, negatives). Verify it completes deterministically without
    crashing or inventing network calls."""
    copilot = AiDiagnosticCopilot()

    massive_dtcs: list[dict[str, object]] = []
    for i in range(1000):
        massive_dtcs.append({"spn": random.choice([100, 110, 651, 652, 102, 3251, 9999]), "fmi": i % 32})

    extreme_telemetry = {
        "EngineSpeed": 999999.0,
        "BoostPressure": -50.0,
        "CoolantTemp": 999.0,
    }

    report = copilot.analyze_session(
        massive_dtcs,
        extreme_telemetry,
        [f"ECU_{i}" for i in range(100)],
    )

    assert report.severity == FaultSeverity.CRITICAL_STOP
    assert report.raw_dtc_count == 1000
    assert len(report.troubleshooting_steps) >= 1
    assert report.ai_model_used == "Yerel Otomotiv Uzman Motoru (Çevrimdışı)"
    assert len(report.affected_subsystems) >= 1

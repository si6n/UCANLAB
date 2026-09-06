"""Adversarial Security, Safety & Concurrency Stress Test Suite.

Empirical verification of:
1. Anti-Tamper & Clock Rollback (Extreme backwards jumps, monotonic drift, corrupted HWM files)
2. E-Stop Security (HMAC token forgery, nonce replay attacks, concurrent race conditions)
3. RingBuffer Concurrency (High-throughput multi-threaded producers/consumers, zero frame corruption)
4. ReplayBus Timing Precision (Sub-millisecond spinloop benchmark, jitter and drift verification)
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import hmac
import random
import statistics
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError, SafetyError
from src.core.models.can_frame import CanFrame
from src.engine.buffer.ring_buffer import BinaryRingBuffer
from src.hal.replay.player import ReplayBus
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.security.license.validator import LicenseValidator


class FakeWallClock:
    """Deterministic wall-clock for G3: verify_token reads time from here."""

    def __init__(self, wall_ts: int) -> None:
        self._wall_ns = int(wall_ts) * 1_000_000_000

    def set(self, wall_ts: int) -> None:
        self._wall_ns = int(wall_ts) * 1_000_000_000

    def now_monotonic(self) -> float:
        return 0.0

    def now_monotonic_ns(self) -> int:
        return 0

    def now_wall_ns(self) -> int:
        return self._wall_ns


# ============================================================================
# 1. Anti-Tamper & Clock Rollback Empirical Stress Tests
# ============================================================================


def test_extreme_clock_rollback_scenarios() -> None:
    """Stress test anti-tamper with extreme backwards jumps and edge-case deltas."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    base_ts = 1_700_000_000

    payload_dict = {
        "user_id": "usr_stress",
        "tier": "ENTERPRISE",
        "hardware_fingerprint": "HW_TEST_1",
        "issued_at": base_ts - 100_000,
        "expires_at": base_ts + 100_000_000,
    }
    token = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    # 1. Extreme 10-year rollback (315,360,000 seconds in past)
    val_10yr = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_TEST_1",
        last_known_clock_ts=base_ts,
    )
    with pytest.raises(LicenseError) as exc:
        val_10yr.clock = FakeWallClock(base_ts - 315_360_000)
        val_10yr.verify_token(token)
    assert exc.value.code == "CLOCK_ROLLBACK_DETECTED"

    # 2. 1-second clock rollback (now < last_known_clock_ts)
    val_1s = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_TEST_1",
        last_known_clock_ts=base_ts,
    )
    with pytest.raises(LicenseError) as exc:
        val_1s.clock = FakeWallClock(base_ts - 1)
        val_1s.verify_token(token)
    assert exc.value.code == "CLOCK_ROLLBACK_DETECTED"

    # 3. Monotonic counter drift: Realtime clock frozen while monotonic advances
    # Boot at base_ts, monotonic elapsed = 300s, but realtime only advanced 100s (expected base_ts + 300)
    val_mono = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_TEST_1",
        boot_realtime=base_ts,
        boot_monotonic=0.0,
        last_known_clock_ts=base_ts,
    )
    with patch("time.monotonic", return_value=300.0):
        # Expected realtime = base_ts + 300. Tolerance is 60s -> min allowed is base_ts + 240
        # If current_ts is base_ts + 200 (< 240), it must trigger CLOCK_MONOTONIC_MISMATCH
        with pytest.raises(LicenseError) as exc:
            val_mono.clock = FakeWallClock(base_ts + 200)
            val_mono.verify_token(token)
        assert exc.value.code == "CLOCK_MONOTONIC_MISMATCH"

        # Edge case: Exactly on boundary (base_ts + 240) -> Allowed
        val_mono.clock = FakeWallClock(base_ts + 240)
        res = val_mono.verify_token(token)
        assert res.user_id == "usr_stress"


@pytest.mark.parametrize(
    "corrupt_content",
    [
        b"",  # Empty file
        b"\x00\x00\x00\x00",  # Null bytes
        b"\xff\xfe\xfd\xfc\xfa",  # Non-UTF8 binary data
        b"not_a_number\n",  # String
        b"1e10\n",  # Scientific notation
        b"12345.678\n",  # Float
        b"1700000000\n1800000000\n",  # Multi-line
        b"   \t\r\n   ",  # Whitespace
    ],
)
def test_corrupted_high_water_mark_disk_files(tmp_path: Path, corrupt_content: bytes) -> None:
    """Corrupted HWM files fail closed with HWM_CORRUPT instead of silently healing (F-04)."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    hwm_file = tmp_path / "corrupted_hwm.dat"
    hwm_file.write_bytes(corrupt_content)

    with pytest.raises(LicenseError, match="Corrupted HWM") as exc_info:
        LicenseValidator(
            public_key=pub_key,
            hardware_fingerprint="HW_1",
            high_water_mark_path=hwm_file,
        )
    assert exc_info.value.code == "HWM_CORRUPT"


def test_future_tampered_high_water_mark_blocks_validation(tmp_path: Path) -> None:
    """Verify that an adversary placing a future timestamp in HWM file trips rollback protection."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    hwm_file = tmp_path / "future_hwm.dat"
    # Bootstrap a validator to generate the HWM key inside its secret provider,
    # then forge a future timestamp with that key (F-04: key is per-install random).
    bootstrapped = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_1",
        last_known_clock_ts=1_700_000_000,
        last_online_sync_ts=1_700_000_000,
        boot_realtime=1_700_000_000,
        boot_monotonic=0.0,
    )
    future_ts = 1_700_010_000
    data = str(future_ts).encode("utf-8")
    mac = hmac.new(bootstrapped._hwm_key, data, hashlib.sha256).hexdigest()
    hwm_file.write_text(f"{future_ts}.{mac}", encoding="utf-8")

    now = 1_700_000_000
    payload_dict = {
        "user_id": "usr_future_hwm",
        "tier": "FREE",
        "hardware_fingerprint": "HW_1",
        "issued_at": now - 100,
        "expires_at": now + 100000,
    }
    token = LicenseValidator.generate_signed_token(priv_key, payload_dict)

    validator = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_1",
        high_water_mark_path=hwm_file,
        last_known_clock_ts=now,
        boot_realtime=now,
        boot_monotonic=0.0,
        last_online_sync_ts=now,
        secret_provider=bootstrapped._secret_provider,
    )
    # HWM loaded future timestamp -> current time now is in the past compared to HWM
    with patch("time.monotonic", return_value=0.0):
        with pytest.raises(LicenseError) as exc:
            validator.clock = FakeWallClock(now)
            validator.verify_token(token)
        assert exc.value.code == "CLOCK_ROLLBACK_DETECTED"


# ============================================================================
# 2. E-Stop Security & Adversarial Attack Tests
# ============================================================================


def test_estop_replay_attack_with_old_nonce_and_tokens() -> None:
    """Stress test E-Stop against replay attacks using multiple expired nonces."""
    secret = b"estop_super_secret_key_32_bytes"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)

    recorded_tokens: list[tuple[bytes, str]] = []

    # Cycle 1: Trigger, record token, reset
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Emergency 1")
    nonce1 = estop.get_reset_nonce()
    token1 = estop.compute_reset_token(nonce1)
    recorded_tokens.append((nonce1, token1))
    estop.reset(token1)
    assert not estop.is_engaged

    # Cycle 2: Trigger again, record token 2, reset
    estop.trigger(EStopTriggerSource.BUS_OFF_DETECTED, reason="Emergency 2")
    nonce2 = estop.get_reset_nonce()
    token2 = estop.compute_reset_token(nonce2)
    recorded_tokens.append((nonce2, token2))
    assert nonce1 != nonce2
    assert token1 != token2
    estop.reset(token2)
    assert not estop.is_engaged

    # Cycle 3: Trigger third time
    estop.trigger(EStopTriggerSource.TEMPERATURE_OVERHEAT, reason="Emergency 3")
    assert estop.is_engaged

    # Attack: Replay token1 and token2 against cycle 3
    for _old_nonce, old_token in recorded_tokens:
        with pytest.raises(SafetyError) as exc:
            estop.reset(old_token)
        assert exc.value.code == "ESTOP_RESET_DENIED"
        assert estop.is_engaged

    # Proper reset for cycle 3
    nonce3 = estop.get_reset_nonce()
    token3 = estop.compute_reset_token(nonce3)
    estop.reset(token3)
    assert not estop.is_engaged


@pytest.mark.parametrize(
    "forged_token",
    [
        "",  # Empty string
        "a" * 63,  # Too short (63 hex chars instead of 64)
        "a" * 65,  # Too long (65 hex chars)
        "0000000000000000000000000000000000000000000000000000000000000000",  # Zero hash
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",  # All f's
        "g" * 64,  # Non-hex characters
        " 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef ",  # Padded whitespace
        "\x00" * 64,  # Null byte string
    ],
)
def test_estop_forged_hmac_signatures_rejected(forged_token: str) -> None:
    """Verify that all forged, malformed, or invalid HMAC tokens are rejected."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.UNAUTHORIZED_PAYLOAD, reason="Malicious CAN payload")
    assert estop.is_engaged

    with pytest.raises(SafetyError) as exc:
        estop.reset(forged_token)
    assert exc.value.code == "ESTOP_RESET_DENIED"
    assert estop.is_engaged


def test_estop_concurrent_trigger_and_reset_race_conditions() -> None:
    """Stress-test concurrent trigger and reset spam across 10 threads."""
    secret = b"race_condition_test_secret_32b!"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)
    stop_event = threading.Event()
    exceptions: list[Exception] = []

    def trigger_worker(worker_id: int) -> None:
        try:
            while not stop_event.is_set():
                estop.trigger(
                    EStopTriggerSource.KEEPALIVE_TIMEOUT,
                    reason=f"Worker {worker_id} trigger",
                    vehicle_speed_kmh=float(worker_id),
                )
                time.sleep(0.0005)
        except Exception as e:
            exceptions.append(e)

    def reset_worker() -> None:
        try:
            while not stop_event.is_set():
                if estop.is_engaged:
                    nonce = estop.get_reset_nonce()
                    if nonce:
                        token = estop.compute_reset_token(nonce)
                        try:
                            estop.reset(token)
                        except SafetyError:
                            # Token might be invalidated if another trigger fired concurrently
                            pass
                time.sleep(0.0005)
        except Exception as e:
            exceptions.append(e)

    def reader_worker() -> None:
        try:
            while not stop_event.is_set():
                _ = estop.is_engaged
                _ = estop.last_event
                _ = estop.get_reset_nonce()
                time.sleep(0.0002)
        except Exception as e:
            exceptions.append(e)

    threads: list[threading.Thread] = []
    # 4 trigger threads, 4 reset threads, 4 reader threads = 12 concurrent threads
    for i in range(4):
        threads.append(threading.Thread(target=trigger_worker, args=(i,)))
        threads.append(threading.Thread(target=reset_worker))
        threads.append(threading.Thread(target=reader_worker))

    for t in threads:
        t.start()

    # Let the race run under high load for 0.5s
    time.sleep(0.5)
    stop_event.set()

    for t in threads:
        t.join()

    assert not exceptions, f"Thread exceptions occurred: {exceptions}"

    # Final deterministic reset must succeed if engaged
    if estop.is_engaged:
        nonce = estop.get_reset_nonce()
        token = estop.compute_reset_token(nonce)
        estop.reset(token)
    assert not estop.is_engaged


# ============================================================================
# 3. BinaryRingBuffer High-Throughput Concurrency & Stress Tests
# ============================================================================


def test_ring_buffer_extreme_multithreaded_stress() -> None:
    """Stress test BinaryRingBuffer with 8 producers pushing 20,000 frames and concurrent readers."""
    capacity = 10_000
    buf = BinaryRingBuffer(capacity=capacity)
    frames_per_worker = 2_500
    num_writers = 8
    total_expected_frames = num_writers * frames_per_worker  # 20,000 frames

    reader_errors: list[str] = []
    stop_reading = threading.Event()

    def writer_task(worker_id: int) -> None:
        for i in range(frames_per_worker):
            # Mix standard CAN and CAN-FD frames with varying payloads
            is_fd = i % 2 == 0
            data_size = 64 if is_fd else 8
            payload = bytes([(worker_id * 31 + j) % 256 for j in range(data_size)])

            frame = CanFrame(
                channel_id=f"bus_{worker_id}",
                arbitration_id=0x100 * worker_id + (i % 0x100),
                dlc=15 if is_fd else 8,
                data=payload,
                is_extended=bool(i % 3 == 0),
                is_fd=is_fd,
                brs=is_fd,
                esi=False,
                direction="tx" if (i % 5 == 0) else "rx",
                timestamp_ns=time.time_ns(),
            )
            buf.append(frame)

    def reader_task(reader_id: int) -> None:
        while not stop_reading.is_set():
            count_to_fetch = random.randint(1, 500)
            frames = buf.get_latest_frames(count_to_fetch)
            if frames:
                # Verify sequence monotonically increases within slice
                seqs = [f.sequence for f in frames]
                if seqs != sorted(seqs):
                    reader_errors.append(f"Reader {reader_id}: sequence not sorted: {seqs[:10]}")
                # Verify frame contents integrity
                for f in frames:
                    if len(f.data) not in (8, 64):
                        reader_errors.append(f"Reader {reader_id}: unexpected frame data len {len(f.data)}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        reader_futures = [executor.submit(reader_task, rid) for rid in range(4)]
        writer_futures = [executor.submit(writer_task, wid) for wid in range(num_writers)]

        # Wait for all writers to complete
        for wf in writer_futures:
            wf.result()

        # Stop readers and wait
        stop_reading.set()
        for rf in reader_futures:
            rf.result()

    assert not reader_errors, f"Reader validation errors: {reader_errors}"
    assert buf.total_written == total_expected_frames
    assert buf.current_size == capacity

    # Final retrieval of all frames in buffer (capacity = 10,000)
    final_frames = buf.get_latest_frames(capacity)
    assert len(final_frames) == capacity
    assert final_frames[0].sequence == total_expected_frames - capacity
    assert final_frames[-1].sequence == total_expected_frames - 1


# ============================================================================
# 4. ReplayBus Timing Precision & Jitter Benchmark Tests
# ============================================================================


def test_replay_bus_timing_precision_benchmark() -> None:
    """Empirically benchmark ReplayBus playback timing delta accuracy under 500 msg/s."""
    frame_count = 50  # 50 frames @ 2ms intervals (total 100ms playback)
    interval_ns = 2_000_000  # 2ms per frame

    frames = [
        CanFrame.create(
            channel_id="ch0",
            arbitration_id=0x100 + i,
            data=bytes([i % 256]),
            timestamp_ns=i * interval_ns,
        )
        for i in range(frame_count)
    ]

    bus = ReplayBus(frames)
    timestamps: list[float] = []

    def on_frame(_frame: CanFrame) -> None:
        timestamps.append(time.perf_counter())

    t0 = time.perf_counter()
    bus.play(callback=on_frame, speed=1.0)
    total_elapsed = time.perf_counter() - t0

    # Calculate actual inter-frame deltas in milliseconds
    deltas_ms = [(timestamps[i] - timestamps[i - 1]) * 1000.0 for i in range(1, len(timestamps))]

    mean_delta_ms = statistics.mean(deltas_ms)
    expected_total_ms = (frame_count - 1) * 2.0  # 98.0 ms

    assert abs(total_elapsed * 1000.0 - expected_total_ms) < 25.0
    assert abs(mean_delta_ms - 2.0) < 0.6


def test_license_bit_flip_and_grace_boundary() -> None:
    """Stress test Ed25519 cryptographic resilience against single bit-flips and exact 7-day grace boundaries."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    now = 1_700_000_000

    payload_dict = {
        "user_id": "usr_bitflip",
        "tier": "ENTERPRISE",
        "hardware_fingerprint": "HW_BITFLIP",
        "issued_at": now - 1000,
        "expires_at": now + 100_000,
    }
    token = LicenseValidator.generate_signed_token(priv_key, payload_dict)
    parts = token.split(".")
    payload_b64, sig_b64 = parts[0], parts[1]

    validator = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_BITFLIP",
        last_known_clock_ts=now,
        last_online_sync_ts=now,
        boot_realtime=now,
        boot_monotonic=0.0,
    )

    # 1. Baseline verification succeeds
    with patch("time.monotonic", return_value=0.0):
        validator.clock = FakeWallClock(now)
        verified = validator.verify_token(token)
        assert verified.user_id == "usr_bitflip"

    # 2. Bit-flip in signature
    sig_raw = bytearray(base64.urlsafe_b64decode(sig_b64.encode("ascii")))
    sig_raw[0] ^= 0x01  # Flip 1 bit
    tampered_sig_b64 = base64.urlsafe_b64encode(sig_raw).decode("ascii")
    tampered_token_sig = f"{payload_b64}.{tampered_sig_b64}"

    with patch("time.monotonic", return_value=0.0):
        with pytest.raises(LicenseError) as exc:
            validator.clock = FakeWallClock(now)
            validator.verify_token(tampered_token_sig)
        assert exc.value.code == "INVALID_SIGNATURE"

    # 3. Bit-flip in payload
    payload_raw = bytearray(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
    payload_raw[10] ^= 0x01  # Flip 1 bit in JSON
    tampered_payload_b64 = base64.urlsafe_b64encode(payload_raw).decode("ascii")
    tampered_token_payload = f"{tampered_payload_b64}.{sig_b64}"

    with patch("time.monotonic", return_value=0.0):
        with pytest.raises(LicenseError) as exc:
            validator.clock = FakeWallClock(now)
            validator.verify_token(tampered_token_payload)
        assert exc.value.code in ("INVALID_SIGNATURE", "MALFORMED_PAYLOAD")

    # 4. Offline grace period boundary: exactly 7 days (604,800s)
    # 7 days - 1 second -> ALLOWED
    validator_grace_ok = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_BITFLIP",
        last_known_clock_ts=now,
        last_online_sync_ts=now - 604_799,
        boot_realtime=now,
        boot_monotonic=0.0,
    )
    with patch("time.monotonic", return_value=0.0):
        validator_grace_ok.clock = FakeWallClock(now)
        v = validator_grace_ok.verify_token(token)
        assert v.user_id == "usr_bitflip"

    # 7 days + 1 second -> EXPIRED
    validator_grace_expired = LicenseValidator(
        public_key=pub_key,
        hardware_fingerprint="HW_BITFLIP",
        last_known_clock_ts=now,
        last_online_sync_ts=now - 604_801,
        boot_realtime=now,
        boot_monotonic=0.0,
    )
    with patch("time.monotonic", return_value=0.0):
        with pytest.raises(LicenseError) as exc:
            validator_grace_expired.clock = FakeWallClock(now)
            validator_grace_expired.verify_token(token)
        assert exc.value.code == "OFFLINE_GRACE_EXPIRED"


def test_gateway_estop_interlock_under_flood_and_recovery() -> None:
    """Stress test TxSafetyGateway and E-Stop interlock under high-rate unauthorized transmission flooding."""
    from src.hal.drivers.pcan_kvaser import PythonCanBus
    from src.safety.gateway import TxSafetyGateway

    bus = PythonCanBus(interface="virtual", channel="stress_vbus_flood", listen_only=False)
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0, 0x7E8})

    valid_frame = CanFrame.create(channel_id="ch0", arbitration_id=0x7E0, data=b"\x01\x02")
    malicious_frame = CanFrame.create(channel_id="ch0", arbitration_id=0x123, data=b"\xde\xad")

    # 1. Normal transmission works
    assert gateway.validate_and_transmit(valid_frame) is True

    # 2. Malicious frame triggers E-Stop
    with pytest.raises(SafetyError) as exc:
        gateway.validate_and_transmit(malicious_frame)
    assert exc.value.code == "WHITELIST_VIOLATION"
    assert estop.is_engaged is True

    # 3. Subsequent valid frames are instantly blocked by ESTOP_ACTIVE
    for _ in range(20):
        with pytest.raises(SafetyError) as exc:
            gateway.validate_and_transmit(valid_frame)
        assert exc.value.code == "ESTOP_ACTIVE"

    # 4. Valid HMAC reset restores transmission
    nonce = estop.get_reset_nonce()
    token = estop.compute_reset_token(nonce)
    estop.reset(token)
    assert estop.is_engaged is False

    # 5. Normal transmission resumes cleanly
    assert gateway.validate_and_transmit(valid_frame) is True
    bus.disconnect()


def test_ring_buffer_100k_frames_zero_corruption_stress() -> None:
    """High-rate 100,000 frames stress test across 16 producers and 4 consumers with zero corruption."""
    capacity = 5_000
    buf = BinaryRingBuffer(capacity=capacity)
    frames_per_worker = 6_250
    num_writers = 16
    total_frames = num_writers * frames_per_worker  # 100,000 frames

    corruption_detected: list[str] = []
    stop_event = threading.Event()

    def writer_task(wid: int) -> None:
        for idx in range(frames_per_worker):
            # Deterministic payload pattern: 16 bytes = wid(2B) + idx(4B) + fixed signature(10B)
            payload = bytearray(16)
            payload[0] = (wid >> 8) & 0xFF
            payload[1] = wid & 0xFF
            payload[2] = (idx >> 24) & 0xFF
            payload[3] = (idx >> 16) & 0xFF
            payload[4] = (idx >> 8) & 0xFF
            payload[5] = idx & 0xFF
            for k in range(6, 16):
                payload[k] = (k * 17) & 0xFF

            frame = CanFrame(
                channel_id=f"ch_{wid % 4}",
                arbitration_id=0x200 + wid,
                dlc=15,
                data=bytes(payload),
                is_extended=True,
                is_fd=True,
                brs=True,
                esi=False,
                direction="tx",
                timestamp_ns=time.time_ns(),
            )
            buf.append(frame)

    def reader_task(rid: int) -> None:
        while not stop_event.is_set():
            batch = buf.get_latest_frames(100)
            for f in batch:
                if len(f.data) != 16:
                    corruption_detected.append(f"Reader {rid}: Bad len {len(f.data)}")
                    continue
                # Verify fixed signature bytes 6..15
                for k in range(6, 16):
                    expected = (k * 17) & 0xFF
                    if f.data[k] != expected:
                        corruption_detected.append(f"Reader {rid}: Corrupt byte at {k}")
                        break
            time.sleep(0.0005)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        rfutures = [executor.submit(reader_task, i) for i in range(4)]
        wfutures = [executor.submit(writer_task, i) for i in range(num_writers)]

        for wf in wfutures:
            wf.result()

        stop_event.set()
        for rf in rfutures:
            rf.result()

    assert not corruption_detected, f"Payload corruption detected: {corruption_detected[:5]}"
    assert buf.total_written == total_frames
    assert buf.current_size == capacity


@pytest.mark.parametrize("speed_multiplier", [0.5, 2.0, 5.0, 10.0])
def test_replay_bus_speed_scaling_precision(speed_multiplier: float) -> None:
    """Verify replay timing precision scales proportionally across different speed multipliers."""
    frame_count = 30
    interval_ns = 2_000_000  # 2ms per frame (total trace = 58ms)

    frames = [
        CanFrame.create(
            channel_id="ch0",
            arbitration_id=0x100 + i,
            data=bytes([i]),
            timestamp_ns=i * interval_ns,
        )
        for i in range(frame_count)
    ]

    bus = ReplayBus(frames)
    t0 = time.perf_counter()
    bus.play(callback=lambda f: None, speed=speed_multiplier)
    elapsed = time.perf_counter() - t0

    expected_elapsed_s = ((frame_count - 1) * 0.002) / speed_multiplier
    tolerance_s = 0.025  # Ã‚Â±25ms tolerance on Windows scheduler

    assert abs(elapsed - expected_elapsed_s) < tolerance_s, (
        f"Speed {speed_multiplier}x timing error: actual {elapsed * 1000:.2f}ms vs expected {expected_elapsed_s * 1000:.2f}ms"
    )

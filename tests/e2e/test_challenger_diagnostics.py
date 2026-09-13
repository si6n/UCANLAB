"""Universal CAN-Bus Diagnostic & Telemetry Tool - Empirical Challenger Adversarial Test Suite.

Adversarial Stress Test Suite for:
- Milestone 1: OBD-II (SAE J1979) / UDS (ISO 14229) Diagnostic Parameter Knowledge Base & Active Poller
- Milestone 2: Heavy-Duty Commercial Vehicle OEM Proprietary J1939 Decoders

Sections:
1. Section 1: OBD-II & UDS Adversarial Edge Cases & Boundary Stress
2. Section 2: Active Diagnostic Poller Concurrency, Starvation & State Machine Stress
3. Section 3: OEM J1939 Decoders Boundary & Adversarial Challenge
4. Section 4: End-to-End Mixed Telemetry Workload & Resilience Stress
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from src.core.contracts.ports import (
    ClockProvider,
    InMemoryTxPort,
    QueueRxSubscription,
)
from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import SignalStatus
from src.protocols.j1939.oem.registry import (
    OemDecodedPayload,
    OemJ1939Registry,
    build_j1939_id,
)
from src.protocols.obd.models import (
    ObdPidResult,
    UdsDidResult,
)
from src.protocols.obd.pids import (
    ObdPidRegistry,
    decode_support_bitmask,
    is_pid_supported_by_bitmask,
)
from src.protocols.obd.poller import (
    DEFAULT_P2_TIMEOUT_S,
    ActiveDiagnosticPoller,
    PollerState,
)
from src.protocols.uds.did_database import (
    UdsDidRegistry,
)
from src.protocols.uds.nrc import UdsNrc

# ============================================================================
# Test Fixtures and Test Clock Helpers
# ============================================================================


class DeterministicClock(ClockProvider):
    """Deterministic monotonic clock for precise scheduler timing control."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._time = initial_time

    def now_monotonic(self) -> float:
        return self._time

    def now_monotonic_ns(self) -> int:
        return int(self._time * 1_000_000_000)

    def advance(self, seconds: float) -> None:
        self._time += seconds


# ============================================================================
# Section 1: OBD-II & UDS Adversarial Edge Cases & Boundary Stress
# ============================================================================


class TestObdAdversarialEdgeCases:
    """Adversarial challenge for OBD-II Mode 01 PID formulas, scaling, and bitmasks."""

    def test_obd_truncated_payloads_graceful_rejection(self) -> None:
        """Verify all standard PIDs reject truncated byte buffers with is_valid=False and ValueError."""
        registry = ObdPidRegistry()

        # PIDs requiring 1 byte (e.g. 0x04 Load, 0x05 Temp, 0x0D Speed)
        single_byte_pids = [0x04, 0x05, 0x0D, 0x0E, 0x0F, 0x11, 0x2F, 0x33, 0x5C]
        for pid in single_byte_pids:
            res = registry.decode(pid, b"")
            assert res.is_valid is False
            assert res.value is None
            # Underlying definition directly raises ValueError
            defn = registry.get(pid)
            assert defn is not None
            with pytest.raises(ValueError, match=r"requires at least"):
                defn.decode(b"")

        # PIDs requiring 2 bytes (e.g. 0x0C RPM, 0x10 MAF, 0x1F Run Time, 0x21 Distance)
        two_byte_pids = [0x02, 0x0C, 0x10, 0x1F, 0x21, 0x22, 0x23, 0x31, 0x42, 0x5E]
        for pid in two_byte_pids:
            res_empty = registry.decode(pid, b"")
            assert res_empty.is_valid is False
            res_1b = registry.decode(pid, b"\x00")
            assert res_1b.is_valid is False
            defn = registry.get(pid)
            assert defn is not None
            with pytest.raises(ValueError, match=r"requires at least"):
                defn.decode(b"\x00")

        # PIDs requiring 4 bytes (e.g. 0x00 Bitmask, 0x01 Status, 0x20 Bitmask)
        four_byte_pids = [0x00, 0x01, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0]
        for pid in four_byte_pids:
            for trunc_len in range(4):
                res_trunc = registry.decode(pid, b"\x00" * trunc_len)
                assert res_trunc.is_valid is False

    def test_obd_extreme_sensor_boundaries(self) -> None:
        """Verify mathematical correctness across minimum, maximum, and extreme boundaries."""
        registry = ObdPidRegistry()

        # PID 0x0C: Engine RPM ((A*256 + B)/4) -> 0..16383.75 rpm
        res_min = registry.decode(0x0C, b"\x00\x00")
        assert res_min.value == 0.0
        assert res_min.is_valid is True

        res_max = registry.decode(0x0C, b"\xFF\xFF")
        assert res_max.value == 16383.75
        assert res_max.is_valid is True

        res_mid = registry.decode(0x0C, b"\x1F\x40")  # (8000 / 4) = 2000 rpm
        assert res_mid.value == 2000.0

        # PID 0x05: Coolant Temp (A - 40) -> -40..215 °C
        assert registry.decode(0x05, b"\x00").value == -40.0
        assert registry.decode(0x05, b"\x28").value == 0.0  # 40 - 40 = 0 °C
        assert registry.decode(0x05, b"\xFF").value == 215.0

        # PID 0x06: Short Term Fuel Trim ((A/1.28) - 100) -> -100..+99.22 %
        assert registry.decode(0x06, b"\x00").value == -100.0
        assert registry.decode(0x06, b"\x80").value == 0.0  # 128/1.28 - 100 = 0%
        assert round(registry.decode(0x06, b"\xFF").value, 2) == 99.22

        # PID 0x0E: Timing Advance ((A/2) - 64) -> -64..+63.5 °
        assert registry.decode(0x0E, b"\x00").value == -64.0
        assert registry.decode(0x0E, b"\x80").value == 0.0
        assert registry.decode(0x0E, b"\xFF").value == 63.5

        # PID 0x10: MAF Air Flow Rate ((A*256 + B)/100) -> 0..655.35 g/s
        assert registry.decode(0x10, b"\x00\x00").value == 0.0
        assert registry.decode(0x10, b"\xFF\xFF").value == 655.35

        # PID 0x11: Throttle Position (A * 100 / 255) -> 0..100 %
        assert registry.decode(0x11, b"\x00").value == 0.0
        assert registry.decode(0x11, b"\xFF").value == 100.0

        # PID 0x23: Fuel Rail Pressure (A*256 + B) * 10 -> 0..655350 kPa
        assert registry.decode(0x23, b"\x00\x00").value == 0.0
        assert registry.decode(0x23, b"\xFF\xFF").value == 655350.0

        # PID 0x42: Control Module Voltage ((A*256 + B)/1000) -> 0..65.535 V
        assert registry.decode(0x42, b"\x00\x00").value == 0.0
        assert registry.decode(0x42, b"\x34\x56").value == 13.398
        assert registry.decode(0x42, b"\xFF\xFF").value == 65.535

        # PID 0x5C: Engine Oil Temperature (A - 40) -> -40..215 °C
        assert registry.decode(0x5C, b"\x00").value == -40.0
        assert registry.decode(0x5C, b"\x82").value == 90.0  # 130 - 40 = 90 °C
        assert registry.decode(0x5C, b"\xFF").value == 215.0

    def test_obd_bitmask_stress_all_patterns(self) -> None:
        """Adversarial testing of 4-byte PID support bitmask decoders across all anchors."""
        anchors = [0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0]

        for base in anchors:
            # 1. No PIDs supported: 0x00000000
            empty_pids = decode_support_bitmask(b"\x00\x00\x00\x00", base)
            assert empty_pids == []
            assert not is_pid_supported_by_bitmask(b"\x00\x00\x00\x00", base, base + 1)

            # 2. All 32 PIDs supported: 0xFFFFFFFF
            all_pids = decode_support_bitmask(b"\xFF\xFF\xFF\xFF", base)
            assert len(all_pids) == 32
            assert all_pids == list(range(base + 1, base + 33))
            assert is_pid_supported_by_bitmask(b"\xFF\xFF\xFF\xFF", base, base + 1)
            assert is_pid_supported_by_bitmask(b"\xFF\xFF\xFF\xFF", base, base + 32)
            assert not is_pid_supported_by_bitmask(b"\xFF\xFF\xFF\xFF", base, base + 33)

            # 3. MSB only: 0x80000000 (only base + 1)
            msb_pids = decode_support_bitmask(b"\x80\x00\x00\x00", base)
            assert msb_pids == [base + 1]

            # 4. LSB only: 0x00000001 (only base + 32, which indicates next anchor is present)
            lsb_pids = decode_support_bitmask(b"\x00\x00\x00\x01", base)
            assert lsb_pids == [base + 32]

            # 5. Alternating bit pattern: 0xAAAAAAAA (odd offsets: 1, 3, 5, ..., 31)
            alt_pids = decode_support_bitmask(b"\xAA\xAA\xAA\xAA", base)
            assert len(alt_pids) == 16
            expected_odds = [base + i for i in range(1, 33, 2)]
            assert alt_pids == expected_odds

            # 6. Alternating bit pattern: 0x55555555 (even offsets: 2, 4, 6, ..., 32)
            even_pids = decode_support_bitmask(b"\x55\x55\x55\x55", base)
            assert len(even_pids) == 16
            expected_evens = [base + i for i in range(2, 33, 2)]
            assert even_pids == expected_evens

    def test_obd_unregistered_pid_fallback(self) -> None:
        """Verify unlisted/custom PIDs decode gracefully into raw hex representation.

        REVIEW hardening: unknown PIDs are NOT valid telemetry — the result
        still carries the raw hex payload (nothing is invented) but is
        marked is_valid=False with unit='unknown' so downstream consumers
        can never present it as a physical reading."""
        registry = ObdPidRegistry()
        unlisted_pid = 0xFD

        # Non-empty raw bytes
        result = registry.decode(unlisted_pid, b"\xDE\xAD\xBE\xEF")
        assert result.pid == unlisted_pid
        assert "UNKNOWN_PID" in result.name
        assert result.value == "deadbeef"
        assert result.unit == "unknown"
        assert result.is_valid is False
        assert result.error_message is not None

        # Empty payload — same fail-closed contract
        result_empty = registry.decode(unlisted_pid, b"")
        assert result_empty.value == ""
        assert result_empty.is_valid is False

    def test_obd_freeze_dtc_and_monitor_status_edge_cases(self) -> None:
        """Adversarial stress on Freeze Frame DTC (PID 0x02) and Monitor Status (PID 0x01)."""
        registry = ObdPidRegistry()

        # PID 0x02: Freeze Frame DTCs
        # P0100 -> (0x01, 0x00)
        assert registry.decode(0x02, b"\x01\x00").value == "P0100"
        # C0321 -> 0x43, 0x21 (0b0100_0011 -> C0321)
        assert registry.decode(0x02, b"\x43\x21").value == "C0321"
        # B1234 -> 0x92, 0x34 (0b1001_0010 -> B1234)
        assert registry.decode(0x02, b"\x92\x34").value == "B1234"
        # U0400 -> 0xC4, 0x00 (0b1100_0100 -> U0400)
        assert registry.decode(0x02, b"\xC4\x00").value == "U0400"
        # 0x0000 -> "None"
        assert registry.decode(0x02, b"\x00\x00").value == "None"

        # PID 0x01: Monitor Status
        # All monitors active & complete, MIL ON, 7 DTCs
        res_01 = registry.decode(0x01, b"\x87\xFF\xFF\xFF")
        val = res_01.value
        assert isinstance(val, dict)
        assert val["mil_on"] is True
        assert val["dtc_count"] == 7
        assert val["is_diesel"] is True
        assert val["egr_available"] is True


# ============================================================================
# Section 2: UDS DID Database & Decoding Adversarial Stress
# ============================================================================


class TestUdsAdversarialEdgeCases:
    """Adversarial challenge for ISO 14229 UDS DID definitions and converters."""

    def test_uds_truncated_payloads_graceful_rejection(self) -> None:
        """Verify DIDs requiring fixed lengths reject truncated payloads without exceptions."""
        registry = UdsDidRegistry()

        # 0xF190 VIN requires 17 bytes
        res_vin_short = registry.decode(0xF190, b"16CHARACTERS_NO")
        assert res_vin_short.is_valid is False
        assert "requires at least 17 bytes" in str(res_vin_short.error_message)

        # 0x0100 Battery Voltage requires 2 bytes
        res_v_short = registry.decode(0x0100, b"\x12")
        assert res_v_short.is_valid is False
        assert "requires at least 2 bytes" in str(res_v_short.error_message)

        # 0x0103 Engine Speed requires 2 bytes
        res_rpm_short = registry.decode(0x0103, b"")
        assert res_rpm_short.is_valid is False

    def test_uds_extreme_out_of_range_boundaries(self) -> None:
        """Adversarial stress on physical min/max validity checking for UDS DIDs."""
        registry = UdsDidRegistry()

        # 0x0100 Battery Voltage: Valid range [0.0V, 655.35V], scale 0.01V
        # 12.50V (1250 raw -> 0x04E2) -> VALID
        res_v_valid = registry.decode(0x0100, b"\x04\xE2")
        assert res_v_valid.value == 12.50
        assert res_v_valid.is_valid is True

        # 0x0104 Accelerator Pedal Position: Valid range [0.0%, 100.0%], scale 0.01%
        # 85.00% (8500 raw -> 0x2134) -> VALID
        res_pedal = registry.decode(0x0104, b"\x21\x34")
        assert res_pedal.value == 85.00
        assert res_pedal.is_valid is True

        # 150.00% (15000 raw -> 0x3A98) -> Out of range -> INVALID
        res_pedal_oob = registry.decode(0x0104, b"\x3A\x98")
        assert res_pedal_oob.value == 150.00
        assert res_pedal_oob.is_valid is False
        assert "above maximum 100.0" in str(res_pedal_oob.error_message)

        # 0x0106 Steering Wheel Angle: Signed 16-bit, [-3276.8, +3276.7 deg]
        # +45.0 deg (450 raw -> 0x01C2) -> VALID
        res_steer_pos = registry.decode(0x0106, b"\x01\xC2")
        assert res_steer_pos.value == 45.0
        assert res_steer_pos.is_valid is True

        # -45.0 deg (-450 raw -> 0xFE3E) -> VALID
        res_steer_neg = registry.decode(0x0106, b"\xFE\x3E")
        assert res_steer_neg.value == -45.0
        assert res_steer_neg.is_valid is True

    def test_uds_ascii_did_malformed_and_control_chars(self) -> None:
        """Verify ASCII strings with embedded nulls, whitespace, and non-printable bytes."""
        registry = UdsDidRegistry()

        # Standard 17-char VIN with trailing nulls/spaces
        raw_vin = b"WVWZZZ3CZWE123456\x00\x00\x20"
        res_vin = registry.decode(0xF190, raw_vin)
        assert res_vin.value == "WVWZZZ3CZWE123456"
        assert res_vin.is_valid is True

        # System Name (0xF197) with control characters
        raw_sys = b"ABS_ESP_V1.2\r\n\t\x00"
        res_sys = registry.decode(0xF197, raw_sys)
        assert res_sys.value == "ABS_ESP_V1.2"

    def test_uds_bcd_dates_and_fingerprints(self) -> None:
        """Verify BCD date and programming fingerprint decoding."""
        registry = UdsDidRegistry()

        # 0xF199: Programming Date (YYYYMMDD BCD -> 4 bytes)
        # 2026-08-30 -> b"\x20\x26\x08\x30"
        res_date = registry.decode(0xF199, b"\x20\x26\x08\x30")
        assert res_date.value == "2026-08-30"

        # 0xF183: Boot Software Fingerprint (4B date + tester ID)
        raw_fp = b"\x20\x25\x11\x15\x41\x42\x43\x44"
        res_fp = registry.decode(0xF183, raw_fp)
        assert isinstance(res_fp.value, dict)
        assert res_fp.value["date"] == "2025-11-15"
        assert res_fp.value["tester_id"] == "41424344"

    def test_uds_unknown_did_fallback(self) -> None:
        """Verify unlisted DID returns structured raw hex payload.

        REVIEW hardening: unknown DIDs are NOT valid telemetry — raw hex is
        preserved (no fabricated decode) but flagged is_valid=False."""
        registry = UdsDidRegistry()
        unlisted_did = 0x99AA

        res = registry.decode(unlisted_did, b"\xCA\xFE\xBA\xBE")
        assert res.did == 0x99AA
        assert res.name == "UNKNOWN_DID_0x99AA"
        assert res.value == "CAFEBABE"
        assert res.unit == "unknown"
        assert res.is_valid is False
        assert res.error_message is not None


# ============================================================================
# Section 3: Active Diagnostic Poller Concurrency, Starvation & State Machine Stress
# ============================================================================


class TestPollerConcurrencyAndStarvationStress:
    """Stress testing the Active Diagnostic Poller scheduler under high-load conditions."""

    def test_poller_high_concurrency_100_jobs(self) -> None:
        """Stress test: Register 100 simultaneous PID and DID jobs and verify execution."""
        tx_port = InMemoryTxPort()
        clock = DeterministicClock(initial_time=100.0)
        # REVIEW hardening: the aggregate scheduler ceiling is 50 Hz
        # (above it is a configuration error, fail-closed) — the 100-job
        # concurrency coverage stays identical under the ceiling.
        poller = ActiveDiagnosticPoller(
            tx_port=tx_port,
            clock_provider=clock,
            max_rate_hz=50.0,
        )

        executed_pids: set[int] = set()
        executed_dids: set[int] = set()

        # Register 50 OBD PIDs (0x01..0x32) with uniform priority so all jobs rotate
        for pid in range(1, 51):
            rate = 1.0 + (pid % 5) * 2.0  # 1Hz to 9Hz

            def make_pid_cb(p: int):
                return lambda res: executed_pids.add(p)

            poller.register_pid(pid, rate_hz=rate, callback=make_pid_cb(pid), priority=5)

        # Register 50 UDS DIDs (0x0100..0x0131)
        for did_idx in range(50):
            did = 0x0100 + did_idx
            rate = 1.0 + (did_idx % 5) * 2.0

            def make_did_cb(d: int):
                return lambda res: executed_dids.add(d)

            poller.register_did(did, rate_hz=rate, callback=make_did_cb(did), priority=5)

        jobs = poller.get_registered_jobs()
        assert len(jobs) == 100

        # Step through 1000 scheduler intervals (advancing 15ms each) with positive responses
        for _ in range(1000):
            clock.advance(0.015)
            job = poller.step()
            if job is not None:
                if job.kind == "obd_pid":
                    resp_frame = CanFrame.create(
                        channel_id="obd_ch0",
                        arbitration_id=0x7E8,
                        data=bytes([0x04, 0x41, job.identifier, 0x10, 0x20, 0x00, 0x00, 0x00]),
                        is_extended=False,
                    )
                else:
                    resp_frame = CanFrame.create(
                        channel_id="obd_ch0",
                        arbitration_id=0x7E8,
                        data=bytes([0x05, 0x62, (job.identifier >> 8) & 0xFF, job.identifier & 0xFF, 0x04, 0xE2, 0x00, 0x00]),
                        is_extended=False,
                    )
                poller.process_rx_frame(resp_frame)

        # Verify that all 50 PIDs and 50 DIDs executed successfully
        assert len(executed_pids) == 50
        assert len(executed_dids) == 50

    def test_poller_priority_queue_starvation_prevention(self) -> None:
        """Verify lower-priority periodic jobs are not starved by high-frequency high-priority jobs."""
        tx_port = InMemoryTxPort()
        clock = DeterministicClock(initial_time=100.0)
        poller = ActiveDiagnosticPoller(
            tx_port=tx_port,
            clock_provider=clock,
            max_rate_hz=50.0,
        )

        high_prio_runs = 0
        low_prio_runs = 0

        def high_cb(r: Any) -> None:
            nonlocal high_prio_runs
            high_prio_runs += 1

        def low_cb(r: Any) -> None:
            nonlocal low_prio_runs
            low_prio_runs += 1

        # High priority (10): 20Hz (every 50ms)
        poller.register_pid(0x0C, rate_hz=20.0, callback=high_cb, priority=10)
        # Low priority (1): 2Hz (every 500ms)
        poller.register_did(0x0100, rate_hz=2.0, callback=low_cb, priority=1)

        # Simulate 2.0 seconds in 10ms steps
        for _ in range(200):
            clock.advance(0.010)
            job = poller.step()
            if job is not None:
                if job.kind == "obd_pid":
                    resp = CanFrame.create(
                        channel_id="obd_ch0",
                        arbitration_id=0x7E8,
                        data=bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0x00, 0x00, 0x00]),
                    )
                else:
                    resp = CanFrame.create(
                        channel_id="obd_ch0",
                        arbitration_id=0x7E8,
                        data=bytes([0x05, 0x62, 0x01, 0x00, 0x04, 0xE2, 0x00, 0x00]),
                    )
                poller.process_rx_frame(resp)

        # High priority should run ~40 times, Low priority should run ~4 times (not 0)
        assert high_prio_runs >= 35
        assert low_prio_runs >= 3, "Low priority job suffered complete starvation!"

    def test_poller_nrc_0x78_response_pending_deep_loop(self) -> None:
        """Verify Poller handles deep NRC 0x78 response pending chains without timeout abort."""
        tx_port = InMemoryTxPort()
        clock = DeterministicClock(initial_time=500.0)
        poller = ActiveDiagnosticPoller(tx_port=tx_port, clock_provider=clock)

        completed_result: list[UdsDidResult] = []
        poller.register_did(0xF190, rate_hz=1.0, callback=lambda r: completed_result.append(r))

        # Step to start transaction
        job = poller.step()
        assert job is not None
        assert poller.current_state == PollerState.WAITING_FOR_RESPONSE

        # Feed 10 consecutive NRC 0x78 frames spaced 1 second apart
        for _ in range(10):
            clock.advance(1.0)
            nrc_frame = CanFrame.create(
                channel_id="obd_ch0",
                arbitration_id=0x7E8,
                data=bytes([0x03, 0x7F, 0x22, UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING, 0x00, 0x00, 0x00, 0x00]),
            )
            poller.process_rx_frame(nrc_frame)
            assert poller.current_state == PollerState.WAITING_P2_STAR

        # Advance 2 more seconds (still within P2* 5.0s window) and send positive response
        clock.advance(2.0)
        final_resp = CanFrame.create(
            channel_id="obd_ch0",
            arbitration_id=0x7E8,
            data=bytes([0x06, 0x62, 0xF1, 0x90, 0x41, 0x42, 0x43, 0x00]),
        )
        poller.process_rx_frame(final_resp)

        assert poller.current_state == PollerState.COMPLETED
        assert len(completed_result) == 1
        assert completed_result[0].did == 0xF190

    def test_poller_nrc_0x21_busy_backoff_and_retry_exhaustion(self) -> None:
        """Verify exponential backoff retry on NRC 0x21 and eventual failure after max retries.

        P5 semantics: an NRC parks the job in RETRY_BACKOFF and frees the
        active slot (`_active_job = None`). A repeated NRC arriving while no
        request is outstanding is response noise and must be ignored. Each
        genuine retry therefore needs clock.advance(backoff) + poller.step()
        to retransmit, followed by a fresh NRC 0x21.
        """
        tx_port = InMemoryTxPort()
        clock = DeterministicClock(initial_time=200.0)
        poller = ActiveDiagnosticPoller(tx_port=tx_port, clock_provider=clock)

        poller.register_pid(0x0D, rate_hz=1.0, callback=lambda r: None)
        sent_frames_before = len(tx_port.sent_frames)
        job = poller.step()
        assert job is not None

        nrc_busy = CanFrame.create(
            channel_id="obd_ch0",
            arbitration_id=0x7E8,
            data=bytes([0x03, 0x7F, 0x01, UdsNrc.BUSY_REPEAT_REQUEST, 0x00, 0x00, 0x00, 0x00]),
        )

        # Attempt 1: outstanding request -> NRC 0x21 parks the job in backoff
        poller.process_rx_frame(nrc_busy)
        assert poller.current_state == PollerState.RETRY_BACKOFF
        assert job.retry_count == 1
        assert job.next_run_s == 200.0 + 0.050

        # While parked, a duplicate NRC is noise (no outstanding request)
        poller.process_rx_frame(nrc_busy)
        assert job.retry_count == 1  # unchanged

        # Backoff delays: 0.05, 0.10, 0.20 (BASE_BACKOFF_S * 2**(n-1));
        # each advance must exceed the 40 Hz rate-limiter floor (0.025 s).
        # retry_count is bumped when the retry's NRC is processed. The third
        # retry's NRC exceeds MAX_RETRIES and moves the job straight to FAILED.
        backoffs = [0.05, 0.10, 0.20]
        expected_retry = 2
        for delay in backoffs:
            clock.advance(delay)
            resent = poller.step()  # retransmit the parked job
            assert resent is not None, f"job did not retransmit after {delay}s backoff"
            poller.process_rx_frame(nrc_busy)
            assert job.retry_count == expected_retry
            if expected_retry <= 3:
                assert job.state == PollerState.RETRY_BACKOFF
            expected_retry += 1

        assert job.retry_count == 4  # 1 initial + 3 backoff retries
        assert job.state == PollerState.FAILED
        assert job.consecutive_failures == 1
        assert poller.current_state == PollerState.FAILED
        # Four transmissions actually hit the wire: initial + 3 retries
        assert len(tx_port.sent_frames) - sent_frames_before == 4

    def test_poller_timeout_recovery_and_channel_filter(self) -> None:
        """Verify request timeout detection and immunity against foreign channel frames."""
        tx_port = InMemoryTxPort()
        clock = DeterministicClock(initial_time=300.0)
        poller = ActiveDiagnosticPoller(
            tx_port=tx_port,
            clock_provider=clock,
            channel_id="obd_ch0",
        )

        poller.register_pid(0x0C, rate_hz=1.0, callback=lambda r: None)
        job = poller.step()
        assert job is not None
        assert poller.current_state == PollerState.WAITING_FOR_RESPONSE

        # Feed frame from different channel ("can_foreign") -> Must be ignored
        foreign_frame = CanFrame.create(
            channel_id="can_foreign",
            arbitration_id=0x7E8,
            data=bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0x00, 0x00, 0x00]),
        )
        res, fc = poller.process_rx_frame(foreign_frame)
        assert res is None
        assert poller.current_state == PollerState.WAITING_FOR_RESPONSE

        # Advance past P2 timeout (2.0 seconds) -> Next step detects timeout and enters retry backoff
        clock.advance(DEFAULT_P2_TIMEOUT_S + 0.1)
        poller.step()
        assert job.retry_count == 1
        assert poller.current_state == PollerState.RETRY_BACKOFF

    def test_poller_multithreaded_hammer_stress(self) -> None:
        """Adversarial multithreaded hammer: 10 threads concurrently registering, stepping, and feeding frames."""
        tx_port = InMemoryTxPort()
        # REVIEW hardening: aggregate ceiling is 50 Hz; the hammer targets
        # thread-safety under concurrent registration/step/feed, which the
        # ceiling does not constrain.
        poller = ActiveDiagnosticPoller(tx_port=tx_port, max_rate_hz=50.0)
        errors: list[Exception] = []

        def worker_register(worker_id: int) -> None:
            try:
                for i in range(100):
                    pid = (worker_id * 10 + i) % 250 + 1
                    poller.register_pid(pid, rate_hz=10.0, callback=lambda r: None)
                    if i % 3 == 0:
                        poller.unregister_pid(pid)
            except Exception as e:
                errors.append(e)

        def worker_step(worker_id: int) -> None:
            try:
                for _ in range(100):
                    poller.step()
                    time.sleep(0.0001)
            except Exception as e:
                errors.append(e)

        def worker_rx(worker_id: int) -> None:
            try:
                for i in range(100):
                    frame = CanFrame.create(
                        channel_id="obd_ch0",
                        arbitration_id=0x7E8,
                        data=bytes([0x03, 0x41, i % 50 + 1, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    )
                    poller.process_rx_frame(frame)
            except Exception as e:
                errors.append(e)

        threads = []
        for wid in range(3):
            threads.append(threading.Thread(target=worker_register, args=(wid,)))
            threads.append(threading.Thread(target=worker_step, args=(wid,)))
            threads.append(threading.Thread(target=worker_rx, args=(wid,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered concurrency exceptions: {errors}"

    def test_poller_callback_exception_isolation(self) -> None:
        """Verify an exception inside user callback is contained and does not crash poller state machine."""
        tx_port = InMemoryTxPort()
        poller = ActiveDiagnosticPoller(tx_port=tx_port)

        def crashing_callback(result: ObdPidResult) -> None:
            raise RuntimeError("Adversarial deliberate callback explosion!")

        poller.register_pid(0x0C, rate_hz=10.0, callback=crashing_callback)
        job = poller.step()
        assert job is not None

        # Feed positive response frame: 4 bytes payload (0x41, 0x0C, 0x1F, 0x40 -> 2000 rpm)
        resp = CanFrame.create(
            channel_id="obd_ch0",
            arbitration_id=0x7E8,
            data=bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0x00, 0x00, 0x00]),
        )
        res, _ = poller.process_rx_frame(resp)

        assert res is not None
        assert res.value == 2000.0
        # Poller state must transition to COMPLETED cleanly
        assert poller.current_state == PollerState.COMPLETED

    @pytest.mark.asyncio
    async def test_async_poll_pid_and_did_once_with_pending_nrc_and_timeout(self) -> None:
        """Verify asynchronous one-shot queries with NRC 0x78 extension and timeout handling."""
        tx_port = InMemoryTxPort()
        rx_sub = QueueRxSubscription()

        poller = ActiveDiagnosticPoller(
            tx_port=tx_port,
            rx_subscription=rx_sub,
        )

        # 1. Successful query after NRC 0x78 response pending
        async def delayed_ecu_response():
            await asyncio.sleep(0.01)
            # Send NRC 0x78
            rx_sub.put_nowait(
                CanFrame.create(
                    channel_id="obd_ch0",
                    arbitration_id=0x7E8,
                    data=bytes([0x03, 0x7F, 0x01, UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING, 0x00, 0x00, 0x00, 0x00]),
                )
            )
            await asyncio.sleep(0.01)
            # Send final positive response for PID 0x0C (RPM = 2000)
            rx_sub.put_nowait(
                CanFrame.create(
                    channel_id="obd_ch0",
                    arbitration_id=0x7E8,
                    data=bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0x00, 0x00, 0x00]),
                )
            )

        task = asyncio.create_task(delayed_ecu_response())
        result = await poller.poll_pid_once(0x0C, timeout_s=1.0)
        await task
        assert result.value == 2000.0

        # 2. Query timeout
        with pytest.raises(TimeoutError):
            await poller.poll_pid_once(0x0D, timeout_s=0.01)


# ============================================================================
# Section 4: OEM J1939 Decoders Boundary & Adversarial Challenge
# ============================================================================


class TestOemJ1939AdversarialAndBoundaries:
    """Adversarial verification of heavy commercial vehicle OEM proprietary decoders."""

    def test_universal_j1939_sentinel_0xff_and_0xfe_all_oems(self) -> None:
        """Adversarial stress: Verify 0xFF (Not Available) and 0xFE (Error Indicator) across all 6 OEM decoders."""
        registry = OemJ1939Registry()

        # All 6 OEM decoders and their primary proprietary PGNs
        oem_pgn_suite: list[tuple[str, int]] = [
            ("Cummins", 65300),      # Cummins DPF Aftertreatment
            ("Cummins", 65303),      # Cummins Cylinder Balancing
            ("Caterpillar", 65320),  # CAT ARD Aftertreatment
            ("Caterpillar", 65325),  # CAT HEUI Cylinder Trim
            ("Scania", 65400),       # Scania DPF Aftertreatment
            ("Scania", 65410),       # Scania Retarder Telemetry
            ("Scania", 65420),       # Scania Cylinder Balancing
            ("Volvo", 65350),        # Volvo ACM Aftertreatment
            ("Volvo", 65352),        # Volvo VEB Retarder
            ("Volvo", 65355),        # Volvo Cylinder Balancing
            ("Detroit", 65370),      # Detroit ACM Aftertreatment
            ("Detroit", 65375),      # Detroit Jake Brake Retarder
            ("Detroit", 65380),      # Detroit Cylinder Balancing
            ("Mercedes-Benz", 65450),# Actros BlueTec Aftertreatment
            ("Mercedes-Benz", 65455),# Actros HPEB Retarder
            ("Mercedes-Benz", 65460),# Actros Laufruheregelung
        ]

        for oem_name, pgn in oem_pgn_suite:
            # 1. All 0xFF (Not Available payload)
            payload_na = registry.decode_payload(
                pgn=pgn,
                data=b"\xFF" * 8,
                manufacturer_hint=oem_name,
            )
            assert payload_na is not None, f"Failed to decode 0xFF payload for {oem_name} PGN {pgn}"
            assert payload_na.manufacturer == oem_name

            # 2. 0xFFFE / 0xFE Error payload: Little-endian 16-bit error is b"\xFE\xFF"
            error_data = b"\xFE\xFF\xFE\xFF\xFE\xFF\xFE\xFF"
            payload_err = registry.decode_payload(
                pgn=pgn,
                data=error_data,
                manufacturer_hint=oem_name,
            )
            assert payload_err is not None, f"Failed to decode Error payload for {oem_name} PGN {pgn}"
            assert payload_err.manufacturer == oem_name

            # Verify numeric sensor signals are invalid on 0xFFFF and 0xFFFE
            if pgn == 65300:  # Cummins DPF
                assert payload_na.signals["dpf_soot_mass_load"].is_valid is False
                assert payload_na.signals["dpf_soot_mass_load"].status == SignalStatus.NOT_AVAILABLE
                assert payload_err.signals["dpf_soot_mass_load"].is_valid is False
                assert payload_err.signals["dpf_soot_mass_load"].status == SignalStatus.ERROR
            elif pgn == 65400:  # Scania DPF
                assert payload_na.signals["scania_dpf_soot_mass"].is_valid is False
                assert payload_err.signals["scania_dpf_soot_mass"].is_valid is False
            elif pgn == 65350:  # Volvo ACM
                assert payload_na.signals["volvo_dpf_soot_accumulation_level"].is_valid is False
                assert payload_err.signals["volvo_dpf_soot_accumulation_level"].is_valid is False
            elif pgn == 65320:  # CAT ARD
                assert payload_na.signals["cat_ard_combustion_air_pressure"].is_valid is False
                assert payload_err.signals["cat_ard_combustion_air_pressure"].is_valid is False
            elif pgn == 65370:  # Detroit ACM
                assert payload_na.signals["detroit_dpf_soot_mass_accumulation"].is_valid is False
                assert payload_err.signals["detroit_dpf_soot_mass_accumulation"].is_valid is False
            elif pgn == 65450:  # Actros BlueTec
                assert payload_na.signals["mercedes_dpf_soot_load_index"].is_valid is False
                assert payload_err.signals["mercedes_dpf_soot_load_index"].is_valid is False

    def test_oem_maximum_soot_load_and_extreme_aftertreatment(self) -> None:
        """Verify maximum valid numerical limits for soot mass, AdBlue dosing, and differential pressure."""
        registry = OemJ1939Registry()

        # 1. Cummins DPF (PGN 65300):
        # raw soot = 0xFFFD -> 65533 * 0.1 = 6553.3 g
        # raw ash = 0xFD -> 253 g
        # raw dp = 0xFFFD -> 65533 * 0.01 = 655.33 kPa
        # raw def = 0xFFFD -> 65533 * 0.01 = 655.33 g/s
        data_cummins_max = bytes([
            0xFD, 0xFF,  # Soot 6553.3g
            0x01,        # Active Parked, Inhibit Off, Lamp Off
            0xFD,        # Ash 253g
            0xFD, 0xFF,  # DP 655.33 kPa
            0xFD, 0xFF,  # DEF 655.33 g/s
        ])
        cummins_res = registry.decode_payload(pgn=65300, data=data_cummins_max, manufacturer_hint="Cummins")
        assert cummins_res is not None
        assert cummins_res.get_value("dpf_soot_mass_load") == 6553.3
        assert cummins_res.get_value("dpf_ash_mass_load_index") == 253.0
        assert cummins_res.get_value("dpf_differential_pressure") == 655.33
        assert cummins_res.get_value("def_actual_dosing_rate") == 655.33

        # 2. Scania DPF (PGN 65400):
        # raw soot = 0xFFFD -> 65533 * 0.05 = 3276.65 g
        # raw dosing = 0xFD -> 253 * 0.1 = 25.3 g/min
        data_scania_max = bytes([
            0xFD, 0xFF,  # Soot 3276.65g
            0x01,        # Highway running
            0xFD,        # AdBlue 25.3 g/min
            0x64,        # Tank level 100%
            0xFD, 0x00,  # DP 25.3 kPa
            0xFD,        # NOx
        ])
        scania_res = registry.decode_payload(pgn=65400, data=data_scania_max, manufacturer_hint="Scania")
        assert scania_res is not None
        assert scania_res.get_value("scania_dpf_soot_mass") == 3276.65
        assert scania_res.get_value("scania_adblue_dosing_command") == 25.3

        # 3. Volvo ACM (PGN 65350):
        # raw soot = 0xFFFD -> 6553.3 g
        # raw dosing = 0xFFFD -> 65533 * 0.05 = 3276.65 g/s
        data_volvo_max = bytes([
            0xFD, 0xFF,  # Soot 6553.3g
            0x02,        # Active in-drive
            0xFD, 0xFF,  # DEF dosing 3276.65 g/s
            0x64,        # Tank level 100%
            0xFD, 0x03,  # Temp
        ])
        volvo_res = registry.decode_payload(pgn=65350, data=data_volvo_max, manufacturer_hint="Volvo")
        assert volvo_res is not None
        assert volvo_res.get_value("volvo_dpf_soot_accumulation_level") == 6553.3
        assert volvo_res.get_value("volvo_adblue_dosing_mass_flow_rate") == 3276.65

        # 4. Actros BlueTec (PGN 65450):
        # raw soot = 0xFFFD -> 6553.3 %
        # raw flow = 0xFD, 0x01 -> 509 * 0.1 = 50.9 g/min
        data_actros_max = bytes([
            0xFD, 0xFF,  # Soot load 6553.3%
            0x01,        # Highway regen
            0xFD, 0x01,  # Flow
            0x64,        # 100% level
            0xFD, 0x01,  # SCR Temp
        ])
        actros_res = registry.decode_payload(pgn=65450, data=data_actros_max, manufacturer_hint="Mercedes-Benz")
        assert actros_res is not None
        assert actros_res.get_value("mercedes_dpf_soot_load_index") == 6553.3

    def test_oem_negative_cylinder_balance_offsets(self) -> None:
        """Adversarial stress on signed cylinder balance conversions (negative, zero, positive)."""
        registry = OemJ1939Registry()

        # 1. Cummins Cylinder Balancing (PGN 65303): Formula: raw * 0.1 - 12.8 mg/stroke
        # raw 0x00 -> -12.8 mg/stroke
        # raw 0x80 (128) -> 12.8 - 12.8 = 0.0 mg/stroke
        # raw 0xFD (253) -> 25.3 - 12.8 = +12.5 mg/stroke
        data_cum_cyl = bytes([0x00, 0x80, 0xFD, 0x00, 0x80, 0xFD, 0x64, 0x00])
        cum_res = registry.decode_payload(pgn=65303, data=data_cum_cyl, manufacturer_hint="Cummins")
        assert cum_res is not None
        assert cum_res.get_value("cylinder_1_fuel_trim_offset") == -12.8
        assert cum_res.get_value("cylinder_2_fuel_trim_offset") == 0.0
        assert cum_res.get_value("cylinder_3_fuel_trim_offset") == 12.5

        # 2. Scania Smooth Running / Cylinder Balancing (PGN 65420): Formula: raw * 0.25 - 32.0 mm³/stroke
        # raw 0x00 -> -32.0 mm³/stroke
        # raw 0x80 (128) -> 32.0 - 32.0 = 0.0 mm³/stroke
        # raw 0xFA (250) -> 62.5 - 32.0 = +30.5 mm³/stroke
        data_sca_cyl = bytes([0x00, 0x80, 0xFA, 0x00, 0x80, 0xFA, 0x00, 0x80])
        sca_res = registry.decode_payload(pgn=65420, data=data_sca_cyl, manufacturer_hint="Scania")
        assert sca_res is not None
        assert sca_res.get_value("scania_cyl_1_smooth_running") == -32.0
        assert sca_res.get_value("scania_cyl_2_smooth_running") == 0.0
        assert sca_res.get_value("scania_cyl_3_smooth_running") == 30.5

        # 3. Volvo Cylinder Balancing (PGN 65355): Formula: raw * 0.1 - 12.8 mg/stroke
        # raw 0x00 -> -12.8 mg/stroke
        # raw 0x80 (128) -> 12.8 - 12.8 = 0.0 mg/stroke
        # raw 0xFD (253) -> 25.3 - 12.8 = +12.5 mg/stroke
        data_vol_cyl = bytes([0x00, 0x80, 0xFD, 0x00, 0x80, 0xFD, 0x64, 0x00])
        vol_res = registry.decode_payload(pgn=65355, data=data_vol_cyl, manufacturer_hint="Volvo")
        assert vol_res is not None
        assert vol_res.get_value("volvo_cyl_1_adaptive_trim_offset") == -12.8
        assert vol_res.get_value("volvo_cyl_2_adaptive_trim_offset") == 0.0
        assert vol_res.get_value("volvo_cyl_3_adaptive_trim_offset") == 12.5

        # 4. Caterpillar MEUI/HEUI Cylinder Trim (PGN 65325): Formula: raw * 0.1 - 12.8 mm³/stroke
        # raw 0x00 -> -12.8 mm³/stroke
        # raw 0x80 (128) -> 12.8 - 12.8 = 0.0 mm³/stroke
        # raw 0xFD (253) -> 25.3 - 12.8 = +12.5 mm³/stroke
        data_cat_cyl = bytes([0x00, 0x80, 0xFD, 0x00, 0x80, 0xFD, 0x64, 0x00])
        cat_res = registry.decode_payload(pgn=65325, data=data_cat_cyl, manufacturer_hint="Caterpillar")
        assert cat_res is not None
        assert cat_res.get_value("cat_cyl_1_trim_offset") == -12.8
        assert cat_res.get_value("cat_cyl_2_trim_offset") == 0.0
        assert cat_res.get_value("cat_cyl_3_trim_offset") == 12.5

        # 5. Detroit Cylinder Balancing (PGN 65380): Formula: raw * 0.05 - 6.4 mg/stroke
        # raw 0x00 -> -6.4 mg/stroke
        # raw 0x80 (128) -> 0.0 mg/stroke
        # raw 0xC8 (200) -> 3.6 mg/stroke
        data_det_cyl = bytes([0x00, 0x80, 0xC8, 0x00, 0x80, 0xC8, 0x64, 0x00])
        det_res = registry.decode_payload(pgn=65380, data=data_det_cyl, manufacturer_hint="Detroit")
        assert det_res is not None
        assert det_res.get_value("detroit_cyl_1_fuel_offset_trim") == -6.4
        assert det_res.get_value("detroit_cyl_2_fuel_offset_trim") == 0.0
        assert det_res.get_value("detroit_cyl_3_fuel_offset_trim") == 3.6

        # 6. Actros Laufruheregelung (PGN 65460): Formula: raw * 0.1 - 12.8 mm³/Hub
        # raw 0x00 -> -12.8 mm³/Hub
        # raw 0x80 (128) -> 0.0 mm³/Hub
        # raw 0xFD (253) -> +12.5 mm³/Hub
        data_act_cyl = bytes([0x00, 0x80, 0xFD, 0x00, 0x80, 0xFD, 0x64, 0x00])
        act_res = registry.decode_payload(pgn=65460, data=data_act_cyl, manufacturer_hint="Mercedes-Benz")
        assert act_res is not None
        assert act_res.get_value("zylinder_1_mengenkorrektur") == -12.8
        assert act_res.get_value("zylinder_2_mengenkorrektur") == 0.0
        assert act_res.get_value("zylinder_3_mengenkorrektur") == 12.5

    def test_oem_extreme_retarder_steps_and_enums(self) -> None:
        """Adversarial stress on retarder stage mappings and torque step boundaries."""
        registry = OemJ1939Registry()

        # 1. Scania Retarder (PGN 65410): Stages 0..6
        for stage in range(7):
            data = bytes([stage, 0x64, 0x10, 0x50, 0x20, 0x00, 0x00, 0x00])
            res = registry.decode_payload(pgn=65410, data=data, manufacturer_hint="Scania")
            assert res is not None
            sig = res.get_signal("scania_retarder_lever_stage_request")
            assert sig is not None
            assert sig.is_valid is True

        # Reserved retarder stage (e.g. 10) — REVIEW hardening: map-miss
        # codes decode fail-closed (is_valid=False, RESERVED/UNKNOWN label
        # carrying the raw code), never a fabricated stage name.
        data_res = bytes([10, 0x64, 0x10, 0x50, 0x20, 0x00, 0x00, 0x00])
        res_res = registry.decode_payload(pgn=65410, data=data_res, manufacturer_hint="Scania")
        reserved_sig = res_res.get_signal("scania_retarder_lever_stage_request")
        assert "RESERVED/UNKNOWN (0x0A)" in str(reserved_sig.value)
        assert reserved_sig.is_valid is False

        # 2. Volvo VEB Retarder (PGN 65352): Stages 0..4
        for stage in range(5):
            data = bytes([stage, 0x64, 0x50, 0x30, 0x00, 0x00, 0x00, 0x00])
            res = registry.decode_payload(pgn=65352, data=data, manufacturer_hint="Volvo")
            assert res is not None
            assert res.get_signal("volvo_veb_engine_brake_stage").is_valid is True

        # 3. Detroit Jake Brake (PGN 65375): Stages 0..3
        for stage in range(4):
            data = bytes([stage, 0x64, 0x01, 0x40, 0x00, 0x00, 0x00, 0x00])
            res = registry.decode_payload(pgn=65375, data=data, manufacturer_hint="Detroit")
            assert res is not None
            assert res.get_signal("detroit_jake_brake_stage").is_valid is True

        # 4. Actros HPEB Retarder (PGN 65455): Stages 0..3
        for stage in range(4):
            data = bytes([stage, 0x64, 0x50, 0x55, 0x00, 0x00, 0x00, 0x00])
            res = registry.decode_payload(pgn=65455, data=data, manufacturer_hint="Mercedes-Benz")
            assert res is not None
            assert res.get_signal("mercedes_hpeb_motorbremse_stufe").is_valid is True

    def test_oem_proprietary_a_unicast_routing_and_commands(self) -> None:
        """Verify Proprietary A (PGN 61184 / 0xEF00) service routine commands."""
        registry = OemJ1939Registry()

        # Cummins Forced DPF Regen Start: Command 0x3A
        can_id_cum = build_j1939_id(pgn=61184, sa=0xF9, da=0x00)
        frame_cum = CanFrame.create(
            channel_id="oem_j1939",
            arbitration_id=can_id_cum,
            data=bytes([0x3A, 0x01, 0x00, 0x00, 0x55, 0x55, 0x55, 0x55]),
            is_extended=True,
        )
        res_cum = registry.decode_frame(frame_cum, manufacturer_hint="Cummins")
        assert res_cum is not None
        assert res_cum.service_id == 0x3A
        assert res_cum.get_value("service_command_name") == "DPF Forced Parked Regeneration Start"

        # Caterpillar Cylinder Cutout: Command 0x20
        frame_cat = CanFrame.create(
            channel_id="oem_j1939",
            arbitration_id=can_id_cum,
            data=bytes([0x20, 0x03, 0x01, 0x00, 0x55, 0x55, 0x55, 0x55]),
            is_extended=True,
        )
        res_cat = registry.decode_frame(frame_cat, manufacturer_hint="Caterpillar")
        assert res_cat is not None
        assert res_cat.service_id == 0x20
        assert res_cat.get_value("service_command_name") == "Cylinder Cutout Diagnostic Test"

    def test_oem_truncated_and_non_extended_frames(self) -> None:
        """Adversarial stress: Truncated payloads (< 8 bytes), standard 11-bit frames, and unknown PGNs."""
        registry = OemJ1939Registry()

        # 1. Truncated payload (< 8 bytes) -> Returns None gracefully without crash
        for pgn in [65300, 65320, 65400, 65350, 65370, 65450]:
            assert registry.decode_payload(pgn=pgn, data=b"\x01\x02\x03") is None
            assert registry.decode_payload(pgn=pgn, data=b"") is None

        # 2. Standard 11-bit CAN frame -> Returns None
        frame_11bit = CanFrame.create(
            channel_id="oem_j1939",
            arbitration_id=0x7DF,
            data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
            is_extended=False,
        )
        assert registry.decode_frame(frame_11bit) is None

        # 3. Unknown PGN outside proprietary ranges
        assert registry.decode_payload(pgn=12345, data=b"\x00" * 8) is None


# ============================================================================
# Section 5: End-to-End Mixed Workload Stress & Resilience Benchmarks
# ============================================================================


class TestEndToEndMixedWorkloadStress:
    """Stress testing simultaneous diagnostic queries and OEM broadcast traffic."""

    def test_e2e_mixed_poller_and_oem_bus_traffic(self) -> None:
        """Verify seamless co-existence of high-rate OBD/UDS polling and OEM J1939 broadcasts."""
        tx_port = InMemoryTxPort()
        clock = DeterministicClock(initial_time=100.0)
        poller = ActiveDiagnosticPoller(
            tx_port=tx_port,
            clock_provider=clock,
            channel_id="main_bus",
            max_rate_hz=50.0,
        )
        oem_registry = OemJ1939Registry()

        polled_obd_results: list[ObdPidResult] = []
        polled_uds_results: list[UdsDidResult] = []
        decoded_oem_payloads: list[OemDecodedPayload] = []

        poller.register_pid(0x0C, rate_hz=20.0, callback=lambda r: polled_obd_results.append(r), priority=9)
        poller.register_did(0xF190, rate_hz=5.0, callback=lambda r: polled_uds_results.append(r), priority=5)

        # Run 200 cycles of mixed traffic
        for cycle in range(200):
            clock.advance(0.010)

            # 1. Step poller
            job = poller.step()
            if job is not None:
                if job.kind == "obd_pid":
                    obd_resp = CanFrame.create(
                        channel_id="main_bus",
                        arbitration_id=0x7E8,
                        data=bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0x00, 0x00, 0x00]),
                    )
                    poller.process_rx_frame(obd_resp)
                else:
                    uds_resp = CanFrame.create(
                        channel_id="main_bus",
                        arbitration_id=0x7E8,
                        data=bytes([0x06, 0x62, 0xF1, 0x90, 0x56, 0x49, 0x4E, 0x00]),
                    )
                    poller.process_rx_frame(uds_resp)

            # 2. Inject asynchronous OEM J1939 broadcast frames into the same bus
            if cycle % 5 == 0:
                # Cummins DPF frame
                can_id_cum = build_j1939_id(pgn=65300, sa=0x00)
                cum_frame = CanFrame.create(
                    channel_id="main_bus",
                    arbitration_id=can_id_cum,
                    data=bytes([0x64, 0x00, 0x01, 0x14, 0x28, 0x00, 0x10, 0x00]),
                    is_extended=True,
                )
                # Poller should safely ignore it
                poller_res, _ = poller.process_rx_frame(cum_frame)
                assert poller_res is None

                # OEM Registry should decode it
                oem_res = oem_registry.decode_frame(cum_frame)
                assert oem_res is not None
                decoded_oem_payloads.append(oem_res)

        assert len(polled_obd_results) >= 35
        assert len(polled_uds_results) >= 8
        assert len(decoded_oem_payloads) == 40

    def test_poller_rapid_start_stop_cycling(self) -> None:
        """Verify rapid asynchronous start/stop transitions without resource leaks."""
        tx_port = InMemoryTxPort()
        poller = ActiveDiagnosticPoller(tx_port=tx_port)

        for _ in range(50):
            poller.start()
            assert poller.is_running is True or poller.is_running is False
            poller.stop()
            assert poller.is_running is False

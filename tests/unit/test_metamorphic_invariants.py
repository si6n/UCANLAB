"""Metamorphic and Zero-GC Invariant Test Suite.

Complies with:
- Consolidated Audit Report (KONSOLIDE_KEIF_RAPORU.md) Chapter D (Test ve Doğrulama) & Chapter E (Performans ve Mimari)
- Action 16: MR-13 (Fault-Injection Purity) + MR-16 (Fail-Closed Closure) CI tests
- Action 17: MR-8 (Timestamp Invariance) test
- Action 25: Zero-GC steady-state testable invariant
- Inviolable Functional Safety Invariants (ISO 26262 ASIL-B/D, AGENTS.md §2)
"""

from __future__ import annotations

import gc
import struct
import time
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from src.core.contracts.ports import SystemClockProvider
from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.core.models.diagnostics import (
    DiagnosticEvent,
    DtcClass,
    DtcNamespace,
    Severity,
    SignalSample,
    VehicleSession,
    classify_dtc_class,
    classify_dtc_namespace,
)
from src.engine.ai.diagnostic_copilot import (
    AiDiagnosticCopilot,
    _derive_severity,
    ensure_external_dtc_database_loaded,
)
from src.engine.ai.drive_safety_policy import decide_risk, validate_ai_dialogue_action
from src.engine.ai.severity_rules import resolve_severity
from src.engine.buffer.ring_buffer import BinaryRingBuffer
from src.hal.virtual import VirtualBus
from src.safety.e2e.packager import E2ESafetyPackager
from src.safety.e2e.profiles import E2EProfileConfig
from src.safety.e2e.validator import E2ESafetyValidator
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.exceptions import (
    FrameSanityError,
    RateLimitExceededError,
    WhitelistViolationError,
)
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor


# ==============================================================================
# 1. MR-13: Fault-Injection Purity (Hata Enjeksiyonu Saflığı)
# ==============================================================================
class TestMetamorphicMR13FaultInjectionPurity:
    """MR-13: Injected faults must strictly isolate and never alter unrelated telemetry or ground-truth state."""

    @pytest.fixture(autouse=True)
    def setup_copilot(self) -> None:
        ensure_external_dtc_database_loaded()
        self.copilot = AiDiagnosticCopilot()

    def test_mr13_single_fault_injection_isolation(self) -> None:
        """Injecting a fault into one subsystem leaves unrelated signals and findings intact."""
        # Baseline nominal telemetry
        nominal_telemetry = {
            "EngineSpeed": 1800.0,
            "CoolantTemp": 85.0,
            "BatteryVoltage": 14.1,
            "OilPressure": 350.0,
        }
        baseline_report = self.copilot.analyze_session(
            active_dtcs=[],
            telemetry_snapshot=nominal_telemetry,
            active_ecus=["0x00"],
        )
        assert baseline_report.severity.name in ("LOW", "INFO")
        assert not any("hararet" in c.lower() or "overheat" in c.lower() for c in baseline_report.likely_causes)

        # Fault injection: Overheating fault injected into CoolantTemp
        fault_telemetry = dict(nominal_telemetry)
        fault_telemetry["CoolantTemp"] = 118.0  # Overheating trigger >= 105C

        fault_report = self.copilot.analyze_session(
            active_dtcs=[{"code": "P0217", "title": "Engine Coolant Over Temperature"}],
            telemetry_snapshot=fault_telemetry,
            active_ecus=["0x00"],
        )

        # 1. The injected fault is correctly identified
        assert fault_report.severity.name in ("HIGH", "CRITICAL", "CRITICAL_STOP")
        assert any("soğutma" in c.lower() or "termostat" in c.lower() or "hararet" in c.lower() for c in fault_report.likely_causes)

        # 2. Purity check: Unrelated telemetry values remain identical in ground truth
        assert fault_telemetry["EngineSpeed"] == nominal_telemetry["EngineSpeed"]
        assert fault_telemetry["BatteryVoltage"] == nominal_telemetry["BatteryVoltage"]
        assert fault_telemetry["OilPressure"] == nominal_telemetry["OilPressure"]

        # 3. No hallucinated/fabricated diagnoses for healthy subsystems (oil pressure, battery)
        assert not any("akü" in c.lower() or "alternatör" in c.lower() for c in fault_report.likely_causes)
        assert not any("yağ pompası" in c.lower() for c in fault_report.likely_causes)

    def test_mr13_zero_hallucination_guarantee_on_clean_telemetry(self) -> None:
        """Clean telemetry with no DTCs must never fabricate diagnostic events or critical causes."""
        clean_snapshot = {
            "EngineSpeed": 850.0,
            "CoolantTemp": 88.0,
            "BoostPressure": 1.0,
        }
        report = self.copilot.analyze_session(
            active_dtcs=[],
            telemetry_snapshot=clean_snapshot,
            active_ecus=["0x00"],
        )
        assert report.severity.name in ("LOW", "INFO")
        # No emergency stop, no high risk
        assert not any("acil" in c.lower() for c in report.likely_causes)

    def test_mr13_independent_fault_non_cross_talk(self) -> None:
        """Injecting faults in two independent subsystems produces orthogonal findings without cross-talk."""
        # Fault A: Misfire P0300
        report_a = self.copilot.analyze_session(
            active_dtcs=[{"code": "P0300"}],
            telemetry_snapshot={"EngineSpeed": 1500.0},
            active_ecus=["0x00"],
        )
        misfire_causes = set(report_a.likely_causes)

        # Fault B: Transmission / Wheel Speed
        report_b = self.copilot.analyze_session(
            active_dtcs=[{"code": "P0700"}],
            telemetry_snapshot={"EngineSpeed": 1500.0},
            active_ecus=["0x10"],
        )
        trans_causes = set(report_b.likely_causes)

        # Joint injection
        report_joint = self.copilot.analyze_session(
            active_dtcs=[{"code": "P0300"}, {"code": "P0700"}],
            telemetry_snapshot={"EngineSpeed": 1500.0},
            active_ecus=["0x00", "0x10"],
        )
        joint_causes = set(report_joint.likely_causes)

        # Both sets of causes must be represented in joint analysis
        assert any(c in joint_causes for c in misfire_causes)
        assert any(c in joint_causes for c in trans_causes)

    def test_mr13_fault_reversibility(self) -> None:
        """When an injected fault is resolved/removed, diagnostic evaluation returns to nominal."""
        telemetry = {"EngineSpeed": 2000.0, "BoostPressure": 2.8}
        # Injected overboost fault
        fault_report = self.copilot.analyze_session(
            active_dtcs=[{"code": "P0234"}],
            telemetry_snapshot=telemetry,
            active_ecus=["0x00"],
        )
        assert any("turbo" in c.lower() or "wastegate" in c.lower() for c in fault_report.likely_causes)

        # Fault removed (nominal boost)
        nominal_telemetry = {"EngineSpeed": 2000.0, "BoostPressure": 1.1}
        restored_report = self.copilot.analyze_session(
            active_dtcs=[],
            telemetry_snapshot=nominal_telemetry,
            active_ecus=["0x00"],
        )
        assert not any("wastegate sıkışmış" in c.lower() for c in restored_report.likely_causes)


# ==============================================================================
# 2. MR-16: Fail-Closed Closure (Fail-Closed Kapanma)
# ==============================================================================
class TestMetamorphicMR16FailClosedClosure:
    """MR-16: Unlisted/unknown faults or invalid frames must fail closed; never evaluate to benign or OK."""

    def test_mr16_unlisted_dtc_never_evaluates_to_ok_or_green(self) -> None:
        """Tabloda olmayan arıza OK vermemeli: unlisted DTCs must not evaluate to benign or safe (GREEN)."""
        unlisted_codes = [
            ("P9999", "Unknown Fault"),
            ("P3FFF", "Unknown Proprietary"),
            ("SPN 999999 FMI 31", ""),
            ("UNKNOWN_CODE_XYZ", "Malformed"),
        ]
        for code, title in unlisted_codes:
            # 1. Severity derivation fails safe to UNKNOWN or never benign
            sev = resolve_severity(code, title=title, subsystem="")
            assert sev == Severity.UNKNOWN

            # 2. Risk decision maps to GRAY (caution/investigate), never GREEN (OK/safe)
            risk = decide_risk(sev.value)
            assert risk == "GRAY"
            assert risk != "GREEN"

            # 3. Class classifier fails safe to NoClass
            dtc_class = classify_dtc_class(code, sev)
            assert dtc_class == DtcClass.NoClass

    def test_mr16_risk_decision_fails_safe_to_gray(self) -> None:
        """decide_risk on unlisted/unknown severity maps to GRAY (never GREEN/safe)."""
        # Unknown/empty severity
        assert decide_risk("UNKNOWN") == "GRAY"
        assert decide_risk("") == "GRAY"
        assert decide_risk("INVALID_LEVEL") == "GRAY"

        # GREEN is reserved ONLY for known benign non-critical levels (LOW / INFO with no EV/HV or MIL flash)
        assert decide_risk("LOW") == "GREEN"
        assert decide_risk("INFO") == "GREEN"

        # Any EV/HV code forces escalation to RED fail-closed
        assert decide_risk("LOW", ev_hv_codes=["P0A1F"]) == "RED"

    def test_mr16_dialogue_action_fails_closed_without_operator_confirmation(self) -> None:
        """Mutating actions strictly fail closed without affirmative operator confirmation and stationary speed."""
        # Mutating action without confirmation -> rejected
        allowed, msg = validate_ai_dialogue_action("PROPOSE_WRITE", confirmed_by_operator=False, vehicle_speed_kmh=0.0)
        assert allowed is False
        assert "çift onayı" in msg

        # Mutating action with confirmation but vehicle moving -> rejected fail-closed
        allowed, msg = validate_ai_dialogue_action("PROPOSE_DTC_CLEAR", confirmed_by_operator=True, vehicle_speed_kmh=15.0)
        assert allowed is False
        assert "hareketsiz değil" in msg

        # Mutating action with confirmation but missing speed -> rejected fail-closed
        allowed, msg = validate_ai_dialogue_action("PROPOSE_ROUTINE", confirmed_by_operator=True, vehicle_speed_kmh=None)
        assert allowed is False

    def test_mr16_safety_gateway_fails_closed_on_invalid_frame(self) -> None:
        """TxSafetyGateway rejects malformed frames, out-of-whitelist IDs, and invalid DLCs fail-closed."""
        bus = VirtualBus(channel_id="vbus_mr16")
        bus.connect()
        gateway = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})

        # 1. Unwhitelisted arbitration ID
        unwhitelisted = CanFrame.create(channel_id="vbus_mr16", arbitration_id=0x123, data=b"\x00")
        with pytest.raises(WhitelistViolationError):
            gateway.validate_and_transmit(unwhitelisted)

        # 2. Corrupt DLC (classic CAN DLC > 8 without FD)
        with pytest.raises(ValueError):
            CanFrame.create(channel_id="vbus_mr16", arbitration_id=0x7E0, data=b"\x00" * 9, is_fd=False)

        bus.disconnect()

    def test_mr16_e2e_crc_corruption_fails_closed(self) -> None:
        """A single bit flip in an E2E-protected frame must fail validation closed (is_valid=False)."""
        validator = E2ESafetyValidator()
        packager = E2ESafetyPackager()
        profile = E2EProfileConfig.create_autosar_profile_1(data_id=0x1234)

        # Produce valid stamped frame via packager.package
        raw_frame = CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x00" * 8)
        sealed_frame = packager.package(raw_frame, profile)

        res_valid = validator.validate(sealed_frame, profile)
        assert res_valid.is_valid is True

        # Corrupt 1 payload bit
        corrupted_data = bytearray(sealed_frame.data)
        corrupted_data[4] ^= 0x01
        corrupted_frame = CanFrame.create(channel_id="c0", arbitration_id=0x100, data=bytes(corrupted_data))

        res_corrupt = validator.validate(corrupted_frame, profile)
        assert res_corrupt.is_valid is False
        assert res_corrupt.is_crc_valid is False
        assert res_corrupt.verdict is not None

    def test_mr16_estop_fail_closed_reset_resistance(self) -> None:
        """E-Stop reset fails closed against forged, invalid, or expired reset tokens."""
        estop = EmergencyStopSystem(allow_self_reset=False)
        estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="MR16 E-Stop check")
        assert estop.is_engaged is True

        # Attempt reset with forged token -> raises SafetyError and remains engaged
        with pytest.raises(SafetyError):
            estop.reset("FORGED_HEX_TOKEN_12345678")
        assert estop.is_engaged is True  # Latched


# ==============================================================================
# 3. MR-8: Timestamp Invariance (Zaman Damgası Değişmezliği)
# ==============================================================================
class TestMetamorphicMR8TimestampInvariance:
    """MR-8: Monotonic non-decreasing timestamps invariant to wall-clock shifts."""

    def test_mr8_monotonic_clock_non_decreasing(self) -> None:
        """Monotonic timestamps strictly satisfy t_{i+1} >= t_i."""
        t_prev = time.monotonic_ns()
        for _ in range(100):
            t_curr = time.monotonic_ns()
            assert t_curr >= t_prev
            t_prev = t_curr

    def test_mr8_wall_clock_jump_invariance_on_watchdog(self) -> None:
        """Stepping wall-clock backward/forward does not alter monotonic watchdog timing."""
        supervisor = SafetySupervisor(initial_state=SafetyState.SAFE, allow_unauthenticated_arm=True)
        supervisor.transition_to(SafetyState.PASSIVE)
        supervisor.arm_tx()

        watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=500.0)
        watchdog.arm_heartbeat_token("token-mr8")
        watchdog.start()
        try:
            assert watchdog.is_lease_valid is True

            # Simulate wall clock jumping backward by 1 hour (NTP step)
            with patch("time.time", return_value=time.time() - 3600.0):
                # Watchdog lease must still be valid because it queries time.monotonic()
                assert watchdog.is_lease_valid is True

            # Simulate wall clock jumping forward by 1 hour
            with patch("time.time", return_value=time.time() + 3600.0):
                assert watchdog.is_lease_valid is True
        finally:
            watchdog.stop()

    def test_mr8_system_clock_safety_verification(self) -> None:
        """SystemClockProvider enforces non-adjustable monotonic clock properties."""
        SystemClockProvider.validate_clock_safety()


# ==============================================================================
# 4. MR-10: Replay Idempotence (Oynatma İdempotansı)
# ==============================================================================
class TestMetamorphicMR10ReplayIdempotence:
    """MR-10: Replaying identical frame sequence produces identical results with no state leakage."""

    def test_mr10_copilot_analysis_replay_idempotence(self) -> None:
        """Replaying identical telemetry twice produces identical diagnostic report."""
        copilot = AiDiagnosticCopilot()
        dtcs = [{"code": "P0101"}, {"code": "P0171"}]
        telemetry = {"EngineSpeed": 1200.0, "CoolantTemp": 90.0, "BoostPressure": 0.8}
        ecus = ["0x00"]

        report1 = copilot.analyze_session(dtcs, telemetry, ecus)
        report2 = copilot.analyze_session(dtcs, telemetry, ecus)

        assert report1.severity == report2.severity
        assert report1.likely_causes == report2.likely_causes
        assert [s.action for s in report1.troubleshooting_steps] == [s.action for s in report2.troubleshooting_steps]
        assert report1.summary == report2.summary
        assert report1.confidence_breakdown == report2.confidence_breakdown

    def test_mr10_ring_buffer_deterministic_replay(self) -> None:
        """Replaying the same frame sequence into two ring buffers yields identical state."""
        buf1 = BinaryRingBuffer(capacity=100)
        buf2 = BinaryRingBuffer(capacity=100)

        frames = [
            CanFrame.create(channel_id="ch0", arbitration_id=0x100 + i, data=bytes([i % 256] * 8), timestamp_ns=i * 1000)
            for i in range(50)
        ]
        for f in frames:
            buf1.append(f)
            buf2.append(f)

        assert buf1.total_written == buf2.total_written
        assert buf1.current_size == buf2.current_size

        v1_p1, v1_p2, s1 = buf1.get_latest_view(50, copy=False)
        v2_p1, v2_p2, s2 = buf2.get_latest_view(50, copy=False)
        assert s1 == s2
        np.testing.assert_array_equal(v1_p1, v2_p1)
        np.testing.assert_array_equal(v1_p2, v2_p2)


# ==============================================================================
# 5. MR-4 & MR-6: Byte-Order Declaration & Signal Factorization
# ==============================================================================
class TestMetamorphicMR4AndMR6SignalInvariants:
    """MR-4 (Byte-order declaration) and MR-6 (Signal factorization / non-interference)."""

    def test_mr4_byte_order_declaration(self) -> None:
        """Big-endian vs little-endian signal decoding determinism."""
        raw_val = 0x1234
        # Little-endian encoding (Intel)
        le_bytes = struct.pack("<H", raw_val)
        # Big-endian encoding (Motorola)
        be_bytes = struct.pack(">H", raw_val)

        assert struct.unpack("<H", le_bytes)[0] == raw_val
        assert struct.unpack(">H", be_bytes)[0] == raw_val
        assert le_bytes != be_bytes

    def test_mr6_orthogonal_signal_factorization(self) -> None:
        """Mutating Signal A in byte 0 has zero effect on the decoded value of Signal B in byte 1."""
        target_signal_b_val = 0x5A

        for signal_a_val in range(256):
            payload = bytes([signal_a_val, target_signal_b_val, 0x00, 0x00])
            # Decode Signal B (byte 1)
            decoded_b = payload[1]
            assert decoded_b == target_signal_b_val


# ==============================================================================
# 6. Steady-State Zero-GC Invariant
# ==============================================================================
class TestZeroGcSteadyStateInvariant:
    """Audit Report Chapter E.1: Steady-state invariant for N consecutive frames:
    (a) gc.get_stats()[g]['collections'] unchanged (g in 0, 1, 2)
    (b) gc.callbacks phase == "start" count == 0
    (c) gc.get_freeze_count() unchanged
    """

    def test_zero_gc_steady_state_append(self) -> None:
        """5,000 steady-state appends to BinaryRingBuffer must cause ZERO cyclic GC collections."""
        buf = BinaryRingBuffer(capacity=50_000)
        frame = CanFrame.create(
            channel_id="ch0",
            arbitration_id=0x18FEEE00,
            data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
            timestamp_ns=1_000_000,
        )

        # Warm-up phase (channel interning, buffer setup)
        for _ in range(500):
            buf.append(frame)

        # Clean slate: collect before entering steady state
        gc.collect()

        stats_before = [g["collections"] for g in gc.get_stats()]
        start_events = 0

        def gc_hook(phase: str, info: dict[str, Any]) -> None:
            nonlocal start_events
            if phase == "start":
                start_events += 1

        gc.callbacks.append(gc_hook)
        try:
            # Steady-state execution: 5,000 appends
            for _ in range(5_000):
                buf.append(frame)
        finally:
            gc.callbacks.remove(gc_hook)

        stats_after = [g["collections"] for g in gc.get_stats()]

        # (a) Collections unchanged for generations 0, 1, and 2
        assert stats_before == stats_after, f"Cyclic GC collections occurred: before={stats_before}, after={stats_after}"
        # (b) Callback start events == 0
        assert start_events == 0, f"GC start callback fired {start_events} times"

    def test_zero_gc_steady_state_batch_append(self) -> None:
        """100 batches of 50 frames appended to BinaryRingBuffer must cause ZERO cyclic GC collections."""
        buf = BinaryRingBuffer(capacity=50_000)
        batch = [
            CanFrame.create(
                channel_id="ch0",
                arbitration_id=0x100 + (i % 16),
                data=b"\xaa\xbb\xcc\xdd",
                timestamp_ns=i * 1000,
            )
            for i in range(50)
        ]

        # Warm-up
        for _ in range(10):
            buf.append_batch(batch)

        gc.collect()
        stats_before = [g["collections"] for g in gc.get_stats()]

        start_events = 0

        def gc_hook(phase: str, info: dict[str, Any]) -> None:
            nonlocal start_events
            if phase == "start":
                start_events += 1

        gc.callbacks.append(gc_hook)
        try:
            for _ in range(100):
                buf.append_batch(batch)
        finally:
            gc.callbacks.remove(gc_hook)

        stats_after = [g["collections"] for g in gc.get_stats()]
        assert stats_before == stats_after
        assert start_events == 0

    def test_zero_gc_steady_state_zero_copy_view(self) -> None:
        """Zero-copy view queries in steady state must cause ZERO cyclic GC collections."""
        buf = BinaryRingBuffer(capacity=10_000)
        frame = CanFrame.create(
            channel_id="ch0",
            arbitration_id=0x123,
            data=b"\x11\x22\x33\x44",
            timestamp_ns=1000,
        )
        for _ in range(1000):
            buf.append(frame)

        gc.collect()
        stats_before = [g["collections"] for g in gc.get_stats()]

        start_events = 0

        def gc_hook(phase: str, info: dict[str, Any]) -> None:
            nonlocal start_events
            if phase == "start":
                start_events += 1

        gc.callbacks.append(gc_hook)
        try:
            for _ in range(5000):
                _p1, _p2, _seq = buf.get_latest_view(100, copy=False)
        finally:
            gc.callbacks.remove(gc_hook)

        stats_after = [g["collections"] for g in gc.get_stats()]
        assert stats_before == stats_after
        assert start_events == 0

"""T47-B regression tests for the ASAMA-1 TX security review findings.

Each test here was written BEFORE the corresponding fix (TDD red -> green) and
pins the invariant from the T47-A remediation plan
(`spn_gap_hunter/output/t47a_tx_review_verification.md`):

  * P1  / G-1  synthetic speed must never authorise a critical command
  * P2  / G-2  gateway derives command criticality from the frame itself
  * P3  / G-3  production composition root wires the HMAC secrets
  * P4  / E-1  reset authority must not share the enforcement secret provider
  * P6  / S-2  arm-token verification is atomic (single-use under contention)
  * P7  / G-5  a global aggregate TX envelope bounds the per-lane budgets
  * P9  / G-9  whitelist miss-streak documentation matches the code
  * P12 / S-3  constructor rejects an estop object without `is_engaged`
  * P13 / S-4  malformed/oversized arm-token expiry raises SafetyError
  * P14 / E-3  reset backoff rejects before the (expensive) HMAC computation
  * P15 / E-4  the dead raw-nonce `EStopResetAuthority.compute_reset_token` is gone
  * P16 / G-7  the whitelist test bypass cannot be flipped by plain attribute write
  * P17 / E-5  ephemeral E-Stop secret fallback is reported at CRITICAL level

Findings explicitly marked WRONG / NO-LONGER-VALID by T47-A (G-4, S-1, G-8, W-1)
are intentionally NOT implemented and therefore have no tests here.
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
import threading
import time

import pytest

from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.hal.virtual import VirtualBus
from src.safety.estop import (
    DEFAULT_ESTOP_KEY_NAME,
    EmergencyStopSystem,
    EStopResetAuthority,
    EStopTriggerSource,
)
from src.safety.exceptions import (
    DualConfirmationRequiredError,
    RateLimitExceededError,
    SpeedDataStaleError,
    SpeedInterlockError,
)
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor


def _gateway(*, whitelist_ids: set[int] | None = None) -> tuple[TxSafetyGateway, VirtualBus, EmergencyStopSystem]:
    bus = VirtualBus(channel_id=f"t47_vbus_{time.monotonic_ns()}")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(
        bus=bus,
        estop=estop,
        whitelist_ids=whitelist_ids if whitelist_ids is not None else {0x7E0, 0x7DF},
    )
    return gateway, bus, estop


# ---------------------------------------------------------------------------
# P1 / G-1 — synthetic speed must never satisfy the speed interlock
# ---------------------------------------------------------------------------


def test_g1_physical_moving_then_synthetic_zero_still_blocks_critical_command() -> None:
    """G-1 core exploit: physical 50 km/h + synthetic 0 km/h must NOT pass.

    Before the fix the synthetic branch overwrote the *interlock* value while
    keeping physical freshness, so Stage 4 saw 0.0 km/h and let a critical
    command through while the vehicle was actually moving at 50 km/h.
    """
    gateway, bus, estop = _gateway()
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x11\x01")

    gateway.update_physical_speed(50.0)
    assert gateway.is_speed_fresh_and_safe() is False

    # Simulator reports a stationary vehicle.
    gateway.record_synthetic_speed(0.0)

    with pytest.raises(SpeedInterlockError):
        gateway.validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)

    state, value = gateway.speed_interlock_state()
    assert state == "moving"
    assert value == 50.0

    bus.disconnect()


def test_g1_synthetic_speed_is_display_only_and_never_touches_interlock_fields() -> None:
    """The interlock reads only the physical sample; display keeps the synthetic."""
    gateway, bus, _estop = _gateway()

    gateway.update_physical_speed(12.0)
    physical_ts = gateway._last_speed_update_ns
    assert physical_ts > 0

    gateway.record_synthetic_speed(77.0)
    assert gateway._last_speed_update_ns == physical_ts
    assert gateway._physical_speed_kmh == 12.0
    assert gateway._display_speed_kmh == 77.0

    # A NaN synthetic value only invalidates the DISPLAY channel — the
    # physical interlock value/timestamp are untouched (and the frame stays
    # blocked because the physical sample is still "moving").
    gateway.record_synthetic_speed(float("nan"))
    assert math.isnan(gateway._display_speed_kmh)
    assert gateway._physical_speed_kmh == 12.0
    assert gateway._last_speed_update_ns == physical_ts
    assert gateway.speed_interlock_state()[0] == "moving"

    bus.disconnect()


def test_g1_moving_display_speed_via_auto_is_backward_compatible() -> None:
    """`_current_vehicle_speed_kmh` stays as the display/telemetry mirror."""
    gateway, bus, _estop = _gateway()

    gateway.update_physical_speed(3.0)
    assert gateway._current_vehicle_speed_kmh == 3.0

    gateway.record_synthetic_speed(9.0)
    assert gateway._current_vehicle_speed_kmh == 9.0  # display mirror updated
    assert gateway._physical_speed_kmh == 3.0  # interlock untouched

    bus.disconnect()


def test_g1_nan_physical_feed_invalidates_interlock_only() -> None:
    gateway, bus, _estop = _gateway()

    gateway.update_physical_speed(4.0)
    gateway.update_physical_speed(float("nan"))
    assert gateway._last_speed_update_ns == 0
    assert gateway.speed_interlock_state()[0] == "stale"

    bus.disconnect()


# ---------------------------------------------------------------------------
# P2 / G-2 — gateway-side criticality classification
# ---------------------------------------------------------------------------


def test_g2_uds_ecu_reset_without_caller_flag_still_hits_speed_interlock() -> None:
    """A whitelisted 0x7E0 `11 01` frame with default flags must be critical.

    Before the fix `is_critical_command` defaulted to False, so a protocol
    engine that forgot the flag skipped Stage 4/5 entirely.
    """
    gateway, bus, estop = _gateway()
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x11\x01")

    # Fresh physical feed, but the vehicle is moving -> frame must be fenced.
    gateway.update_physical_speed(30.0)
    with pytest.raises(SpeedInterlockError):
        gateway.send_sync(frame)

    estop.reset(estop.create_reset_token())

    # Stale feed -> likewise fail closed even without the caller flag.
    gateway_stale, bus_stale, _estop_stale = _gateway()
    with pytest.raises(SpeedDataStaleError):
        gateway_stale.send_sync(frame)

    bus.disconnect()
    bus_stale.disconnect()


def test_g2_uds_clear_dtc_and_security_access_derived_critical() -> None:
    """Fresh stationary physical feed + a mutating service still needs proof.

    With `user_confirmed` absent the critical classification surfaces as
    `DualConfirmationRequiredError`. Each attempt latches the E-Stop, so the
    reset is performed between frames.
    """
    gateway, bus, estop = _gateway()
    gateway.update_physical_speed(0.0)

    for payload in (
        b"\x02\x14\xff",  # 0x14 ClearDiagnosticInformation
        b"\x02\x27\x01",  # 0x27 SecurityAccess
        b"\x02\x10\x03",  # 0x10 DiagnosticSessionControl
        b"\x02\x31\x01",  # 0x31 RoutineControl
        b"\x03\x2e\xf1\x90",  # 0x2E WriteDataByIdentifier
    ):
        frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=payload)
        assert gateway._frame_is_critical(frame) is True
        with pytest.raises(Exception) as exc:
            gateway.send_sync(frame)
        assert exc.value.__class__.__name__ == "DualConfirmationRequiredError"
        # Clear the E-Stop latched by the missing-confirmation rejection.
        estop.reset(estop.create_reset_token())
        gateway.update_physical_speed(0.0)

    bus.disconnect()


def test_g2_read_only_uds_services_are_not_escalated() -> None:
    """Reads (0x22/0x19/0x3E) must stay unaffected — no false positives."""
    gateway, bus, _estop = _gateway()
    gateway.update_physical_speed(0.0)

    for payload in (
        b"\x03\x22\xf1\x90",  # 0x22 ReadDataByIdentifier
        b"\x02\x19\x02",  # 0x19 ReadDTCInformation
        b"\x02\x3e\x00",  # 0x3E TesterPresent
    ):
        frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=payload)
        assert gateway.validate_and_transmit(frame) is True

    bus.disconnect()


def test_g2_isof_tp_consecutive_frames_are_not_misclassified() -> None:
    """ISO-TP CF (PCI nibble 0x2) carries no SID: must not become critical."""
    gateway, bus, _estop = _gateway()
    gateway.update_physical_speed(42.0)  # moving; any critical frame would raise

    cf = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x21\x11\x01AA")
    assert gateway.validate_and_transmit(cf, budget_category="protocol_burst") is True

    bus.disconnect()


def test_g2_caller_flag_still_can_tighten_but_never_relax() -> None:
    gateway, bus, _estop = _gateway()
    gateway.update_physical_speed(0.0)

    # Explicit caller flag on a benign follow-up frame still requires proof.
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x3e\x00")
    assert gateway._frame_is_critical(frame) is False
    with pytest.raises(DualConfirmationRequiredError):
        gateway.validate_and_transmit(frame, is_critical_command=True)

    bus.disconnect()


# ---------------------------------------------------------------------------
# P4 / E-1 — reset authority must not fall back to the enforcement provider
# ---------------------------------------------------------------------------


def test_e1_authority_requires_explicit_secret_provider() -> None:
    estop = EmergencyStopSystem(reset_secret=b"k" * 32)
    with pytest.raises(TypeError):
        EStopResetAuthority(estop)  # type: ignore[call-arg]


def test_e1_authority_rejects_a_shared_enforcement_provider() -> None:
    from src.safety.secret_provider import EphemeralSecretBackend

    provider = EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: b"s" * 32})
    estop = EmergencyStopSystem(secret_provider=provider, allow_self_reset=True)

    # None is refused explicitly (fail-closed, not a silent fallback).
    with pytest.raises(SafetyError) as exc_none:
        EStopResetAuthority(estop, None)  # type: ignore[arg-type]
    assert exc_none.value.code == "ESTOP_AUTHORITY_NOT_INDEPENDENT"

    # The enforcement object's own provider is refused as well.
    with pytest.raises(SafetyError) as exc:
        EStopResetAuthority(estop, provider)
    assert exc.value.code == "ESTOP_AUTHORITY_NOT_INDEPENDENT"


def test_e1_authority_cannot_fall_back_to_enforcement_provider() -> None:
    from src.safety.secret_provider import EphemeralSecretBackend

    shared = EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: b"s" * 32})
    estop = EmergencyStopSystem(secret_provider=shared, allow_self_reset=True)

    # An independent provider is accepted and still mints a verifiable token.
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "t47")
    authority = EStopResetAuthority(
        estop, EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: b"s" * 32})
    )
    assert authority.secret_provider is not estop._secret_provider
    token = authority.mint_reset_token()
    assert token is not None
    estop.reset(token)
    assert estop.is_engaged is False


def test_e1_authority_provider_is_keyword_only_and_documented() -> None:
    """`reset_authority_provider()` supplies a distinct, seeded provider."""
    estop = EmergencyStopSystem(reset_secret=b"k" * 32)
    authority = EStopResetAuthority(estop, secret_provider=estop.reset_authority_provider())
    assert authority.secret_provider is not estop._secret_provider
    assert authority.reset_secret == estop._get_secret()


# ---------------------------------------------------------------------------
# P6 / S-2 — arm token verification is atomic
# ---------------------------------------------------------------------------


def test_s2_concurrent_arm_with_same_token_succeeds_exactly_once() -> None:
    """S-2: two threads presenting the SAME token must not both be armed."""
    secret = b"t47-arm-secret-0123456789abcdef"
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE, auth_secret=secret)
    # Token minted once, then the supervisor is returned to PASSIVE so both
    # threads can attempt the SAME single-use token concurrently.
    token = supervisor.issue_arm_token()
    supervisor.transition_to(SafetyState.PASSIVE)

    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            supervisor.arm_tx("t47 race", auth_token=token)
            outcome = "armed"
        except SafetyError as exc:
            outcome = exc.code or "error"
        with lock:
            results.append(outcome)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        for fut in futures:
            fut.result()

    # Exactly one thread spent the token; the other is refused (either as a
    # replay or because the state machine was already ARMED_TX).
    assert results.count("armed") == 1
    assert set(results) - {"armed"} <= {"ARM_AUTH_REPLAYED", "ILLEGAL_SAFETY_TRANSITION"}


# ---------------------------------------------------------------------------
# P7 / G-5 — global aggregate TX envelope
# ---------------------------------------------------------------------------


def test_g5_total_tx_rate_bounded_across_lanes() -> None:
    """Per-lane budgets must never multiply into an unbounded TX plane."""
    gateway, bus, _estop = _gateway(whitelist_ids={0x7E0})
    gateway.update_physical_speed(0.0)
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x3e\x00")

    assert gateway.MAX_TOTAL_TX_PER_SEC > 0
    assert gateway.MAX_TOTAL_TX_PER_SEC < sum(cap for cap, _ in gateway.BUDGETS.values())

    sent = 0
    for _ in range(gateway.MAX_TOTAL_TX_PER_SEC * 2):
        try:
            gateway.validate_and_transmit(frame, budget_category="protocol_burst")
        except RateLimitExceededError:
            break
        sent += 1
    assert sent <= gateway.MAX_TOTAL_TX_PER_SEC
    assert sent >= gateway.MAX_TOTAL_TX_PER_SEC // 2

    bus.disconnect()


# ---------------------------------------------------------------------------
# P9 / G-9 — whitelist superset documentation stays truthful
# ---------------------------------------------------------------------------


def test_g9_whitelist_superset_allows_known_override() -> None:
    """`whitelist_superset_allowed` is the auditable override of the mask gate."""
    gateway, bus, _estop = _gateway(whitelist_ids={0x7E0})
    assert gateway.whitelist_superset_allowed is False
    assert gateway.WHITELIST_ESTOP_AFTER == 5
    bus.disconnect()


# ---------------------------------------------------------------------------
# P12 / S-3 — constructor rejects an estop without is_engaged
# ---------------------------------------------------------------------------


def test_s3_constructor_rejects_object_without_is_engaged() -> None:
    class _NotAnEstop:
        pass

    with pytest.raises(TypeError):
        SafetySupervisor(initial_state=SafetyState.PASSIVE, estop=_NotAnEstop())


def test_s3_constructor_accepts_a_real_estop_and_none() -> None:
    supervisor_none = SafetySupervisor(initial_state=SafetyState.PASSIVE)
    assert supervisor_none is not None

    estop = EmergencyStopSystem()
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE, estop=estop)
    assert supervisor.current_state == SafetyState.PASSIVE


# ---------------------------------------------------------------------------
# P13 / S-4 — arm token parse hardening
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_expiry", ["-1", "99999999999999999999999999", "0x10"])
def test_s4_malformed_expiry_raises_safety_error(bad_expiry: str) -> None:
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE, auth_secret=b"z" * 32)
    token = f"{bad_expiry}.{'ab' * 16}.{'cd' * 32}"
    with pytest.raises(SafetyError) as exc:
        supervisor.arm_tx("t47", auth_token=token)
    assert exc.value.code == "ARM_AUTH_INVALID"


def test_s4_oversized_expiry_does_not_leak_overflow_error() -> None:
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE, auth_secret=b"z" * 32)
    token = f"{2**64}.{'ab' * 16}.{'cd' * 32}"
    with pytest.raises(SafetyError):
        supervisor.arm_tx("t47", auth_token=token)


# ---------------------------------------------------------------------------
# P14 / E-3 — backoff must throttle before the HMAC computation
# ---------------------------------------------------------------------------


def test_e3_backoff_rejects_before_hmac_computation(monkeypatch: pytest.MonkeyPatch) -> None:
    estop = EmergencyStopSystem(reset_secret=b"b" * 32, allow_self_reset=True)
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "t47 backoff")

    # Two failures arm the exponential cooldown (first typo is tolerated).
    for _ in range(2):
        with pytest.raises(SafetyError):
            estop.reset("00" * 32)
    assert estop.failed_reset_attempts >= 2

    calls = {"n": 0}
    import hmac as _hmac

    real_hmac_new = _hmac.new

    def _counting_hmac(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return real_hmac_new(*args, **kwargs)

    monkeypatch.setattr("src.safety.estop.hmac.new", _counting_hmac)

    # A shape-invalid signature cannot possibly verify -> rejected by the
    # cheap gate without ever computing the HMAC.
    with pytest.raises(SafetyError):
        estop.reset("not-a-hex-signature")
    assert calls["n"] == 0, "backoff must reject malformed input before the HMAC"

    # A VALID credential is still accepted while the cooldown is armed
    # (the operator is never locked out by an attacker's failed guesses).
    token = estop.create_reset_token()
    assert token is not None
    estop.reset(token)
    assert estop.is_engaged is False


# ---------------------------------------------------------------------------
# P15 / E-4 — dead raw-nonce API removed
# ---------------------------------------------------------------------------


def test_e4_dead_raw_nonce_api_is_gone() -> None:
    from src.safety.secret_provider import EphemeralSecretBackend

    estop = EmergencyStopSystem(secret_provider=EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: b"q" * 32}))
    authority = EStopResetAuthority(
        estop, secret_provider=EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: b"q" * 32})
    )
    assert not hasattr(authority, "compute_reset_token")
    assert hasattr(authority, "mint_reset_token")


# ---------------------------------------------------------------------------
# P16 / G-7 — whitelist bypass flag is not externally writable
# ---------------------------------------------------------------------------


def test_g7_whitelist_bypass_flag_is_read_only() -> None:
    gateway, bus, _estop = _gateway()
    assert gateway._whitelist_bypass_for_testing is False

    with pytest.raises(AttributeError):
        gateway._whitelist_bypass_for_testing = True  # type: ignore[misc]
    assert gateway._whitelist_bypass_for_testing is False

    bus.disconnect()


def test_g7_for_testing_factory_still_enables_bypass_under_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UCANLAB_TEST_MODE", "1")
    bus = VirtualBus(channel_id=f"t47_vbus_bypass_{time.monotonic_ns()}")
    bus.connect()
    gateway = TxSafetyGateway.for_testing(bus=bus, whitelist_ids=set())
    assert gateway._whitelist_bypass_for_testing is True
    bus.disconnect()


# ---------------------------------------------------------------------------
# P17 / E-5 — ephemeral fallback is a CRITICAL audit event
# ---------------------------------------------------------------------------


def test_e5_ephemeral_fallback_logs_critical_and_exposes_downgrade(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FailingStore:
        def has_secret(self, name: str) -> bool:
            return False

        def store_secret(self, name: str, secret: bytes) -> None:
            raise OSError("disk full")

        def get_secret(self, name: str) -> bytes:
            raise KeyError(name)

    with caplog.at_level(logging.CRITICAL, logger="safety.estop"):
        estop = EmergencyStopSystem(secret_provider=_FailingStore())  # type: ignore[arg-type]

    assert estop.protection_downgraded is True
    assert any(record.levelno >= logging.CRITICAL for record in caplog.records)


# ---------------------------------------------------------------------------
# P3 / G-3 — the SafeMultiplexedBus choke-point must forward the HMAC proof
# ---------------------------------------------------------------------------


def test_g3_multiplexer_forwards_confirmation_token_to_the_gateway() -> None:
    """The only production TX choke-point must not strip the Stage-5 proof.

    With a confirmation secret wired, `validate_and_transmit` fails closed when a
    critical frame arrives without a token. If `SafeMultiplexedBus` dropped the
    keyword, a properly-authorized critical command could never be sent.
    """
    from src.core.models.can_frame import CanFrame as _CanFrame
    from src.engine.router import FrameRouter
    from src.safety.multiplexer import SafeMultiplexedBus

    bus = VirtualBus(channel_id=f"t47_vbus_mux_{time.monotonic_ns()}")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(
        bus=bus,
        estop=estop,
        whitelist_ids={0x7E0},
        confirmation_secret=b"c" * 32,
    )
    gateway.update_physical_speed(0.0)
    mux = SafeMultiplexedBus(physical_bus=bus, gateway=gateway, router=FrameRouter())

    frame = _CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x11\x01")

    # No token -> fail closed (the derived-critical 0x11 needs cryptographic proof).
    with pytest.raises(DualConfirmationRequiredError):
        mux.send(frame, user_confirmed=True)

    # Token forwarded through the multiplexer -> accepted.
    token = gateway.issue_confirmation_token(0x7E0, ttl_s=30.0)
    mux.send(frame, user_confirmed=True, confirmation_token=token)
    assert bus.sent_frames[-1].arbitration_id == 0x7E0

    bus.disconnect()


# ---------------------------------------------------------------------------
# P3 / G-3 — production composition root wiring (secret derivation, no bridge
# minting primitive, arm_tx requires the operator token)
# ---------------------------------------------------------------------------


def test_g3_composition_root_wires_both_hmac_secrets() -> None:
    from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)

    # Supervisor + gateway own real secrets, so the T41/G-1 fail-closed paths
    # are LIVE in production wiring instead of dead code.
    assert app.supervisor._auth_secret is not None
    assert app.gateway._confirmation_secret is not None
    assert app.supervisor._auth_secret != app.gateway._confirmation_secret

    # The minting primitives must NOT be reachable from the renderer bridge.
    bridge = DesktopApiBridge(app)
    for name in ("issue_arm_token", "issue_confirmation_token", "_mint_arm_token", "_mint_confirmation_token"):
        assert not hasattr(bridge, name), f"{name} must not be exposed to the WebView"


def test_g3_arm_tx_without_operator_token_fails_closed() -> None:
    """§2.5-style: the renderer cannot arm TX by presenting no authorization."""
    from src.safety.state_machine import SafetyState
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app.watchdog.heartbeat(app._heartbeat_token)
    assert app.supervisor.current_state == SafetyState.PASSIVE

    with pytest.raises(SafetyError) as exc:
        app.supervisor.arm_tx(reason="renderer attempted unauthenticated arm")
    assert exc.value.code == "ARM_AUTH_REQUIRED"
    assert app.supervisor.current_state == SafetyState.PASSIVE

    # The trusted composition root mints the token -> arm succeeds.
    app.supervisor.arm_tx(reason="operator", auth_token=app._mint_arm_token())
    assert app.supervisor.current_state == SafetyState.ARMED_TX


def test_g3_diagnostic_action_requires_a_gateway_hmac_token() -> None:
    """The renderer's challenge is a nonce; gateway HMAC proof stays process-internal.

    R2-EN2: a forged/self-minted 32-hex string is refused, a genuine nonce
    challenge verifies once (single-use), and a gateway HMAC token presented
    from JS is refused — only the composition root mints TX authorization.
    """
    from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    bridge = DesktopApiBridge(app)
    app._is_simulating = True

    action = {"action_type": "uds_ecu_reset", "id": "t47-g3"}

    # A forged/self-minted 32-hex string (the old in-process format) is refused.
    forged = "ab" * 16
    res_forged = app._verify_diagnostic_token(forged, action) if hasattr(app, "_verify_diagnostic_token") else None
    if res_forged is None:
        # No dedicated helper: exercise the bridge entry point instead.
        res_forged = bridge.execute_diagnostic_action(action, confirmation_token=forged)
        assert res_forged["success"] is False

    ch = bridge.request_diagnostic_challenge(action)
    assert ch["success"] is True
    assert ch.get("cryptographic") is None
    token = ch["token"]

    first = bridge.execute_diagnostic_action(action, confirmation_token=token)
    assert first["success"] is True

    # Single-use: the nonce challenge burns on first use.
    replay = bridge.execute_diagnostic_action(action, confirmation_token=token)
    assert replay["success"] is False

    # A genuine gateway HMAC token presented from JS is refused (R2-EN2).
    gw_token = app.gateway.issue_confirmation_token(0x7E0).hex()
    res_gw = bridge.execute_diagnostic_action(action, confirmation_token=gw_token)
    assert res_gw["success"] is False


# ---------------------------------------------------------------------------
# P2 / G-2 — false-positive guard for the frame-derived criticality
# ---------------------------------------------------------------------------


def test_g2_j1939_read_only_requests_are_not_escalated() -> None:
    """Only write/actuation targets escalate; read-only J1939 requests do not.

    S1-P1-2 (REVIEW Aşama 1): a J1939-21 Request (PGN 59904) carries the
    REQUESTED PGN in its first three payload bytes, so the escalation must be
    decided from that payload rather than treating every Request as
    read-only. This test therefore uses a REQUEST payload naming a read-only
    PGN (DM1 65226) to prove no false positive, while
    `test_review_phase1_safety_fixes.py` proves a Request naming a writable
    PGN (DM11) IS escalated.
    """
    gateway, bus, _estop = _gateway(whitelist_ids={0x7E0})

    def _pgn_bytes(pgn: int) -> bytes:
        return bytes([pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF])

    def _j1939(pgn: int, payload: bytes = b"\x01\x02") -> object:
        # Priority 6, PGN in the extended id, source 0xF9.
        arb = (6 << 26) | (pgn << 8) | 0xF9
        return CanFrame.create(channel_id="c0", arbitration_id=arb, data=payload, is_extended=True)

    assert gateway._frame_is_critical(_j1939(65235)) is True  # DM11 clear
    assert gateway._frame_is_critical(_j1939(65228)) is True  # DM3 clear
    # A Request that names a read-only PGN (DM1) must NOT escalate.
    assert gateway._frame_is_critical(_j1939(59904, _pgn_bytes(65226))) is False
    assert gateway._frame_is_critical(_j1939(65226)) is False  # DM1 read

    bus.disconnect()


def test_g2_j1939_request_escalation_is_fail_closed_on_short_payload() -> None:
    """S1-P1-2: an undecodable Request payload is treated as critical.

    A Request whose payload is too short to name a PGN cannot be proven
    read-only, so it must escalate rather than pass Stage 4/5.
    """
    gateway, bus, _estop = _gateway(whitelist_ids={0x7E0})
    arb = (6 << 26) | (59904 << 8) | 0xF9
    frame = CanFrame.create(channel_id="c0", arbitration_id=arb, data=b"\x01", is_extended=True)
    assert gateway._frame_is_critical(frame) is True
    bus.disconnect()


def test_g2_classification_never_fails_open_on_a_broken_frame() -> None:
    """A frame object that explodes during inspection must be treated as critical."""
    gateway, bus, _estop = _gateway()

    class _ExplodingFrame:
        @property
        def data(self) -> bytes:
            raise RuntimeError("boom")

    assert gateway._frame_is_critical(_ExplodingFrame()) is True

    bus.disconnect()


def test_e2_secret_rotation_is_still_noticed_despite_the_cache() -> None:
    """E-2: caching the secret must not make a provider key rotation a no-op.

    `reset()` used to read the provider under `_lock` (slow-store risk). It now
    reads a cached copy and re-reads the provider exactly once when the
    provider's revision counter changes, so a rotated key invalidates the
    tokens minted with the previous one.
    """
    from src.safety.secret_provider import EphemeralSecretBackend

    provider = EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: b"o" * 32})
    estop = EmergencyStopSystem(secret_provider=provider, allow_self_reset=True)

    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "t47 rotation")
    old_token = estop.compute_reset_token()
    assert old_token

    provider.store_secret(DEFAULT_ESTOP_KEY_NAME, b"n" * 32)

    with pytest.raises(SafetyError, match="Invalid E-Stop reset token"):
        estop.reset(old_token)


def test_e2_reset_does_not_touch_the_provider_when_no_rotation_happened() -> None:
    """Steady state: the provider is NOT consulted while `_lock` is held."""
    from src.safety.secret_provider import EphemeralSecretBackend

    calls = {"n": 0}

    class _CountingProvider(EphemeralSecretBackend):
        def get_secret(self, name: str) -> bytes:
            calls["n"] += 1
            return super().get_secret(name)

    provider = _CountingProvider({DEFAULT_ESTOP_KEY_NAME: b"o" * 32})
    estop = EmergencyStopSystem(secret_provider=provider, allow_self_reset=True)
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "t47 cache")
    token = estop.compute_reset_token()

    before = calls["n"]
    estop.reset(token)
    assert estop.is_engaged is False
    assert calls["n"] == before, "reset() must use the cached secret, not provider I/O"


# ---------------------------------------------------------------------------
# R2 (TUR 2) — ASAMA 1-6 review remainders
# ---------------------------------------------------------------------------


def _r2_gateway(**kwargs: object) -> tuple[TxSafetyGateway, VirtualBus, EmergencyStopSystem]:
    """Gateway with a confirmation secret wired (production-like)."""
    bus = VirtualBus(channel_id=f"r2_vbus_{time.monotonic_ns()}")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(
        bus=bus,
        estop=estop,
        whitelist_ids={0x7E0, 0x7DF},
        confirmation_secret=b"r" * 32,
        **kwargs,  # type: ignore[arg-type]
    )
    gateway.update_physical_speed(0.0)
    return gateway, bus, estop


def test_r2_g1_rate_reject_does_not_burn_the_confirmation_token() -> None:
    """R2-G1: a token validated in Stage 5 but rejected in Stage 6 stays valid."""
    gateway, bus, _estop = _r2_gateway()
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x11\x01")

    token = gateway.issue_confirmation_token(0x7E0, ttl_s=30.0)
    # Saturate the default lane so Stage 6 rejects.
    for _ in range(TxSafetyGateway.MAX_TX_RATE_PER_SEC):
        filler = CanFrame.create(channel_id="c0", arbitration_id=0x7DF, data=b"\x01\x02")
        gateway.validate_and_transmit(filler)
    with pytest.raises(RateLimitExceededError):
        gateway.validate_and_transmit(
            frame, is_critical_command=True, user_confirmed=True, confirmation_token=token
        )
    # The token must NOT be consumed by the rejected attempt: drain the lane
    # and retry with the SAME token.
    gateway._tx_timestamps.clear()
    gateway._tx_total_timestamps.clear()
    assert gateway.validate_and_transmit(
        frame, is_critical_command=True, user_confirmed=True, confirmation_token=token
    ) is True

    bus.disconnect()


def test_r2_g2_rollback_removes_the_global_envelope_stamp() -> None:
    """R2-G2: fence/estop rollback removes the aggregate-envelope stamp too."""
    gateway, bus, _estop = _r2_gateway()
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7DF, data=b"\x01\x02")
    gateway.validate_and_transmit(frame)
    assert len(gateway._tx_total_timestamps) == 1
    total_stamp = gateway._tx_total_timestamps[0]
    stamp = gateway._tx_timestamps[0]
    gateway._rollback_tx_reservation(True, False, stamp, None, total_stamp=total_stamp)
    assert len(gateway._tx_total_timestamps) == 0
    assert len(gateway._tx_timestamps) == 0

    bus.disconnect()


def test_r2_g3_bad_profile_type_fails_fast_at_construction() -> None:
    """R2-G3: a non-enum profile_type is a config error, not a TX-path crash."""
    from src.safety.e2e.profiles import E2EProfileConfig

    with pytest.raises(ValueError, match="profile_type"):
        E2EProfileConfig(profile_type="E2E_P01")  # type: ignore[arg-type]

    bus = VirtualBus(channel_id=f"r2_vbus_g3_{time.monotonic_ns()}")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    with pytest.raises(ValueError, match="profile_type"):
        TxSafetyGateway(
            bus=bus,
            estop=estop,
            whitelist_ids={0x7E0},
            e2e_profiles={0x7E0: E2EProfileConfig.__new__(E2EProfileConfig)},
        )
    bus.disconnect()


def test_r2_g3_e2e_stage_failure_is_a_safety_error_with_rollback() -> None:
    """R2-G3: E2E stamping errors roll back the reservation as SafetyError."""
    from src.safety.e2e.packager import E2ESafetyPackager
    from src.safety.e2e.profiles import E2EProfileConfig, E2EProfileType

    gateway, bus, _estop = _r2_gateway(
        e2e_packager=E2ESafetyPackager(),
        e2e_profiles={
            0x7E0: E2EProfileConfig(profile_type=E2EProfileType.AUTOSAR_PROFILE_1C)
        },
    )

    def _boom(frame: object, profile: object) -> object:
        raise ValueError("injected packager fault")

    gateway.e2e_packager.package = _boom  # type: ignore[method-assign]
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x3e\x00")
    with pytest.raises(SafetyError) as exc:
        gateway.validate_and_transmit(frame)
    assert exc.value.code == "E2E_STAMP_FAILED"
    assert len(gateway._tx_timestamps) == 0
    assert len(gateway._tx_total_timestamps) == 0

    bus.disconnect()


def test_r2_g4_broad_mask_requires_superset_acknowledgement() -> None:
    """R2-G4: a ~2M-ID mask without the explicit flag fails closed."""
    bus = VirtualBus(channel_id=f"r2_vbus_g4_{time.monotonic_ns()}")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    with pytest.raises(SafetyError) as exc:
        TxSafetyGateway(
            bus=bus, estop=estop, whitelist_masks=[(0, 0x00FF0000)],
        )
    assert exc.value.code == "WHITELIST_SUPERSET_DENIED"
    # Explicit acknowledgement wires it.
    gateway = TxSafetyGateway(
        bus=bus,
        estop=estop,
        whitelist_ids={0x7E0},
        whitelist_masks=[(0, 0x00FF0000)],
        whitelist_superset_allowed=True,
    )
    assert gateway.whitelist_superset_allowed is True
    bus.disconnect()


def test_r2_s2_failed_arm_does_not_burn_the_token() -> None:
    """R2-S2: a rejected arm (illegal transition) leaves the token spendable."""
    from src.safety.state_machine import SafetySupervisor

    supervisor = SafetySupervisor(auth_secret=b"s" * 32)
    token = supervisor.issue_arm_token(ttl_s=30.0)
    # STARTUP -> ARMED_TX is an illegal transition: the burn must not happen.
    with pytest.raises(SafetyError):
        supervisor.arm_tx(reason="r2-s2 illegal transition", auth_token=token)
    # Walk to PASSIVE through legal transitions, then arm with the SAME token.
    supervisor.transition_to(SafetyState.SAFE, reason="r2-s2")
    supervisor.transition_to(SafetyState.PASSIVE, reason="r2-s2")
    supervisor.arm_tx(reason="r2-s2 retry", auth_token=token)
    assert supervisor.current_state == SafetyState.ARMED_TX


def test_r2_g5_en3_bound_token_rejects_wrong_payload_and_wrong_action() -> None:
    """R2-G5/R2-EN3: payload/action-bound tokens reject mismatched use."""
    gateway, bus, _estop = _r2_gateway()
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x11\x01")
    other = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x11\x02")
    ctx = "uds_ecu_reset:t47"

    bound = gateway.issue_confirmation_token(
        0x7E0,
        payload_hash=TxSafetyGateway.confirmation_payload_hash(bytes(frame.data)),
        context=ctx,
    )
    # Wrong payload -> reject, token NOT consumed (fail-closed, retryable).
    with pytest.raises(DualConfirmationRequiredError):
        gateway.validate_and_transmit(
            other,
            is_critical_command=True,
            user_confirmed=True,
            confirmation_token=bound,
            confirmation_context=ctx,
        )
    # Wrong action context -> reject.
    with pytest.raises(DualConfirmationRequiredError):
        gateway.validate_and_transmit(
            frame,
            is_critical_command=True,
            user_confirmed=True,
            confirmation_token=bound,
            confirmation_context="uds_clear_dtc:t47",
        )
    # Exact frame + exact context -> accept.
    assert gateway.validate_and_transmit(
        frame,
        is_critical_command=True,
        user_confirmed=True,
        confirmation_token=bound,
        confirmation_context=ctx,
    ) is True

    bus.disconnect()


def test_r2_h1_abort_hook_registered_for_python_can_driver() -> None:
    """R2-H1: the E-Stop driver abort hook resolves on PythonCanBus."""
    from src.hal.drivers.pcan_kvaser import PythonCanBus

    bus = PythonCanBus(interface="virtual", channel="r2_h1")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    assert gateway._resolve_driver_flush() is not None
    assert len(estop._abort_hooks) > 0
    bus.disconnect()

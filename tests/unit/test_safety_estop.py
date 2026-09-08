"""Comprehensive unit tests for Replay-Protected Emergency Stop (E-Stop) system and SecretProvider integration."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.core.errors import SafetyError
from src.safety.estop import (
    DEFAULT_ESTOP_KEY_NAME,
    EmergencyStopSystem,
    EmergencyStopToken,
    EStopEvent,
    EStopTriggerSource,
)
from src.safety.secret_provider import (
    EphemeralSecretBackend,
    LinuxSecretBackend,
)


def test_estop_initial_state() -> None:
    """Verify default clean unengaged state upon instantiation."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    assert not estop.is_engaged
    assert estop.last_event is None
    assert estop.get_reset_nonce() == b""
    assert estop.active_challenge is None
    assert estop.epoch == 0
    assert estop.secret_provider is not None


@pytest.mark.parametrize("trigger_src", list(EStopTriggerSource))
def test_estop_10_trigger_sources(trigger_src: EStopTriggerSource) -> None:
    """Verify all 10 E-Stop trigger sources engage the system and generate event records."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    reason = f"Test trigger {trigger_src.name}"
    speed = 42.5

    estop.trigger(trigger_src, reason=reason, vehicle_speed_kmh=speed)

    assert estop.is_engaged
    event = estop.last_event
    assert event is not None
    assert isinstance(event, EStopEvent)
    assert event.trigger == trigger_src
    assert event.reason == reason
    assert event.system_speed_kmh == speed
    assert isinstance(event.timestamp_ns, int)
    assert event.timestamp_ns > 0

    nonce = estop.get_reset_nonce()
    assert isinstance(nonce, bytes)
    assert len(nonce) == 16

    challenge = estop.active_challenge
    assert challenge is not None
    assert challenge.epoch == 0
    assert challenge.nonce == nonce
    assert challenge.action == "ESTOP_RESET"


def test_estop_structured_token_dataclass_and_serialization() -> None:
    """Verify EmergencyStopToken formatting and parsing."""
    token = EmergencyStopToken(
        epoch=3,
        nonce="0123456789abcdef0123456789abcdef",
        timestamp_monotonic_ns=1234567890123,
        action="ESTOP_RESET",
        signature="deadbeefcafe1234567890abcdefdeadbeefcafe1234567890abcdefdeadbeef",
    )
    token_str = token.to_token_string()
    assert token_str == "3:0123456789abcdef0123456789abcdef:1234567890123:ESTOP_RESET:deadbeefcafe1234567890abcdefdeadbeefcafe1234567890abcdefdeadbeef"

    parsed = EmergencyStopToken.from_token_string(token_str)
    assert parsed.epoch == 3
    assert parsed.nonce == "0123456789abcdef0123456789abcdef"
    assert parsed.timestamp_monotonic_ns == 1234567890123
    assert parsed.action == "ESTOP_RESET"
    assert parsed.signature == token.signature

    with pytest.raises(ValueError, match="Invalid token format"):
        EmergencyStopToken.from_token_string("bad:format")


def test_estop_structured_token_reset_success() -> None:
    """Verify challenge-response reset flow using structured EmergencyStopToken."""
    secret = b"test_secret_key_32_bytes_long!!!"
    provider = EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: secret})
    estop = EmergencyStopSystem(secret_provider=provider, allow_self_reset=True)

    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Operator pressed red button")
    assert estop.is_engaged

    # Create structured token
    token_obj = estop.create_reset_token()
    assert token_obj is not None
    assert isinstance(token_obj, EmergencyStopToken)
    assert token_obj.epoch == 0
    assert token_obj.action == "ESTOP_RESET"

    # Reset using token object
    estop.reset(token_obj)
    assert not estop.is_engaged
    # B10: audit record of the engagement survives the reset
    assert estop.last_event is not None
    assert estop.last_event.trigger == EStopTriggerSource.USER_UI_BUTTON
    assert estop.get_reset_nonce() == b""
    assert estop.epoch == 1


def test_estop_string_token_reset_success() -> None:
    """Verify reset using serialized token string."""
    secret = b"another_secret_key_for_testing!"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)

    estop.trigger(EStopTriggerSource.SPEED_INTERLOCK_BREACH, reason="Speed breach", vehicle_speed_kmh=88.0)
    assert estop.is_engaged

    token_str = estop.compute_reset_token()
    assert ":" in token_str

    estop.reset(token_str)
    assert not estop.is_engaged
    assert estop.epoch == 1


def test_estop_anti_replay_consumed_nonce_rejected() -> None:
    """Verify that replaying a token after disengagement and re-engagement fails."""
    estop = EmergencyStopSystem(allow_self_reset=True)

    # Engagement 1
    estop.trigger(EStopTriggerSource.SPEED_INTERLOCK_BREACH, reason="Cycle 1")
    token1 = estop.create_reset_token()
    assert token1 is not None
    estop.reset(token1)
    assert not estop.is_engaged
    assert estop.epoch == 1

    # Engagement 2
    estop.trigger(EStopTriggerSource.TEMPERATURE_OVERHEAT, reason="Cycle 2")
    assert estop.is_engaged

    # Replay token1 -> Must fail (epoch mismatch & consumed nonce)
    with pytest.raises(SafetyError, match="E-Stop token"):
        estop.reset(token1)
    assert estop.is_engaged

    # Valid token2 resets
    token2 = estop.create_reset_token()
    assert token2 is not None
    assert token2.epoch == 1
    estop.reset(token2)
    assert not estop.is_engaged
    assert estop.epoch == 2


def test_estop_epoch_mismatch_rejected() -> None:
    """Verify that a token with wrong epoch is rejected."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.COMMUNICATION_TIMEOUT, reason="Lost heartbeat")

    valid_token = estop.create_reset_token()
    assert valid_token is not None

    # Forged epoch
    mismatched_token = EmergencyStopToken(
        epoch=valid_token.epoch + 10,
        nonce=valid_token.nonce,
        timestamp_monotonic_ns=valid_token.timestamp_monotonic_ns,
        action=valid_token.action,
        signature=valid_token.signature,
    )

    with pytest.raises(SafetyError, match="epoch mismatch"):
        estop.reset(mismatched_token)
    assert estop.is_engaged


def test_estop_action_mismatch_rejected() -> None:
    """Verify that a token with invalid action is rejected."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.HARDWARE_DISCONNECT, reason="Adapter unplugged")

    valid_token = estop.create_reset_token()
    assert valid_token is not None

    bad_action_token = EmergencyStopToken(
        epoch=valid_token.epoch,
        nonce=valid_token.nonce,
        timestamp_monotonic_ns=valid_token.timestamp_monotonic_ns,
        action="UNAUTHORIZED_ACTION",
        signature=valid_token.signature,
    )

    with pytest.raises(SafetyError, match="Invalid E-Stop token action"):
        estop.reset(bad_action_token)
    assert estop.is_engaged


def test_estop_nonce_mismatch_rejected() -> None:
    """Verify that a token with wrong nonce is rejected."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.RATE_LIMIT_OVERFLOW, reason="Tx flood")

    valid_token = estop.create_reset_token()
    assert valid_token is not None

    bad_nonce_token = EmergencyStopToken(
        epoch=valid_token.epoch,
        nonce="00000000000000000000000000000000",
        timestamp_monotonic_ns=valid_token.timestamp_monotonic_ns,
        action=valid_token.action,
        signature=valid_token.signature,
    )

    with pytest.raises(SafetyError, match="nonce mismatch"):
        estop.reset(bad_nonce_token)
    assert estop.is_engaged


def test_estop_stale_token_ttl_expired() -> None:
    """Verify that an expired token beyond TTL is rejected."""
    estop = EmergencyStopSystem(max_token_age_s=0.01, allow_self_reset=True)  # 10ms TTL
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="Fast timeout test")

    valid_token = estop.create_reset_token()
    assert valid_token is not None

    # Wait for TTL expiration
    time.sleep(0.05)

    with pytest.raises(SafetyError, match="expired"):
        estop.reset(valid_token)
    assert estop.is_engaged


def test_estop_forged_hmac_signature_rejected() -> None:
    """Verify that forged or corrupted signatures are rejected in constant time."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.UNAUTHORIZED_PAYLOAD, reason="Injected frame")

    token = estop.create_reset_token()
    assert token is not None

    # Corrupt signature
    corrupted_sig = "0" * len(token.signature)
    bad_token = EmergencyStopToken(
        epoch=token.epoch,
        nonce=token.nonce,
        timestamp_monotonic_ns=token.timestamp_monotonic_ns,
        action=token.action,
        signature=corrupted_sig,
    )

    with pytest.raises(SafetyError, match="Invalid E-Stop reset token"):
        estop.reset(bad_token)
    assert estop.is_engaged


def test_estop_zero_hardcoded_secrets_in_source() -> None:
    """Integrity check: verify no hardcoded secret string literals exist in estop.py."""
    estop_file = Path(__file__).resolve().parent.parent.parent / "src" / "safety" / "estop.py"
    source = estop_file.read_text(encoding="utf-8")
    assert "EMERGENCY_STOP_DEFAULT_HMAC_SECRET_2026" not in source
    assert "b\"EMERGENCY_STOP" not in source


def test_estop_linux_secret_provider_integration(tmp_path: Path) -> None:
    """Verify EmergencyStopSystem integrated with LinuxSecretBackend encrypted storage."""
    storage_file = tmp_path / "estop_secrets.bin"
    provider = LinuxSecretBackend(storage_path=storage_file)

    estop = EmergencyStopSystem(secret_provider=provider, allow_self_reset=True)
    assert provider.has_secret(DEFAULT_ESTOP_KEY_NAME)

    estop.trigger(EStopTriggerSource.PROCESS_TERMINATION, reason="Shutdown test")
    token = estop.create_reset_token()
    assert token is not None
    estop.reset(token)
    assert not estop.is_engaged


def test_estop_callbacks_and_error_isolation() -> None:
    """Verify all callbacks are executed and exceptions in one callback do not abort others."""
    estop = EmergencyStopSystem(allow_self_reset=True)

    events_received: list[EStopEvent] = []

    def cb1(event: EStopEvent) -> None:
        events_received.append(event)

    def cb_failing(event: EStopEvent) -> None:
        raise RuntimeError("Callback exploded!")

    def cb2(event: EStopEvent) -> None:
        events_received.append(event)

    estop.register_callback(cb1)
    estop.register_callback(cb_failing)
    estop.register_callback(cb2)

    estop.trigger(EStopTriggerSource.BUS_OFF_DETECTED, reason="Bus-off controller fault")

    assert len(events_received) == 2
    assert events_received[0].trigger == EStopTriggerSource.BUS_OFF_DETECTED
    assert events_received[1].trigger == EStopTriggerSource.BUS_OFF_DETECTED


def test_estop_reset_when_not_engaged_is_noop() -> None:
    """Verify calling reset when not engaged returns cleanly without error."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    assert not estop.is_engaged
    estop.reset("dummy_token")
    assert not estop.is_engaged


def test_estop_concurrent_triggers_thread_safety() -> None:
    """Verify thread safety under concurrent triggers and queries."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            for _ in range(50):
                estop.trigger(EStopTriggerSource.COMMUNICATION_TIMEOUT, reason=f"Thread {idx}")
                _ = estop.is_engaged
                _ = estop.last_event
                _ = estop.get_reset_nonce()
                _ = estop.epoch
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert estop.is_engaged


def test_estop_concurrent_trigger_and_reset_toctou_safety() -> None:
    """Stress test E-Stop TOCTOU safety under rapid interleaved concurrent triggers and resets."""
    secret = b"toctou_verification_secret_32b!"
    estop = EmergencyStopSystem(reset_secret=secret, allow_self_reset=True)
    errors: list[Exception] = []
    iterations = 100

    def trigger_loop() -> None:
        try:
            for i in range(iterations):
                estop.trigger(
                    EStopTriggerSource.SPEED_INTERLOCK_BREACH,
                    reason=f"Interlock cycle {i}",
                )
        except Exception as exc:
            errors.append(exc)

    def reset_loop() -> None:
        try:
            for _ in range(iterations):
                tok = estop.create_reset_token()
                if tok is not None:
                    try:
                        estop.reset(tok)
                    except SafetyError:
                        pass  # Expected race
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=trigger_loop)
    t2 = threading.Thread(target=reset_loop)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors


def test_estop_consumed_nonce_window_is_bounded() -> None:
    """B9: the replay store must not grow without bound.

    Consumed nonces evict oldest-first beyond MAX_CONSUMED_NONCES. Eviction
    is safe: challenges older than max_token_age_s are TTL-rejected anyway,
    so a replayed nonce that fell off the window can no longer verify.
    """
    estop = EmergencyStopSystem(allow_self_reset=True)
    # Fill the window beyond capacity through the REAL production insertion
    # path (_record_consumed_nonce is what reset() calls on success)
    for i in range(estop.MAX_CONSUMED_NONCES + 50):
        estop._record_consumed_nonce(i.to_bytes(16, "big"))

    assert len(estop._consumed_nonces) <= estop.MAX_CONSUMED_NONCES
    # Insertion-ordered eviction: the OLDEST entries are the ones gone
    assert (0).to_bytes(16, "big") not in estop._consumed_nonces


def test_estop_reset_secret_property_resolution() -> None:
    """Verify reset_secret property is readable on both EmergencyStopSystem and EStopResetAuthority."""
    from src.safety.estop import EStopResetAuthority

    secret = b"my_super_secret_signing_key_123"
    estop = EmergencyStopSystem(reset_secret=secret)

    # 1. EmergencyStopSystem.reset_secret must return the secret without SafetyError
    assert estop.reset_secret == secret

    # 2. EStopResetAuthority.reset_secret must also resolve the secret
    authority = EStopResetAuthority(estop)
    assert authority.reset_secret == secret
    assert authority.secret_provider is estop.secret_provider

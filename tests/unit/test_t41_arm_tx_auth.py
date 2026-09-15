"""T41 / S-1 regression tests — arm_tx/activate_tx operator authorization.

Independent review finding (t41_claude_review.md, KRITIK): the ARMED_TX gate
is the single functional authorization point for TX, yet ``arm_tx`` /
``activate_tx`` accepted an ``auth_token`` that was NEVER verified — only a
WARNING was logged when it was None. ``supervisor.arm_tx(auth_token="garbage")``
and ``supervisor.arm_tx()`` both armed TX.

These tests pin the FIXED contract (each FAILS on the pre-fix code):

  1. arm_tx() without a token when a secret is configured  -> SafetyError
  2. arm_tx("garbage") when a secret is configured         -> SafetyError
  3. arm_tx(valid_token)                                   -> ARMED_TX
  4. allow_unauthenticated_arm=True + UCANLAB_TEST_MODE=1  -> WARNING + arms
  5. allow_unauthenticated_arm=True + no test-mode         -> RuntimeError
"""

from __future__ import annotations

import logging

import pytest

from src.core.errors import SafetyError
from src.safety.state_machine import SafetyState, SafetySupervisor

SECRET = b"t41-arm-secret-32-bytes-long!!!"


def _passive(**kwargs: object) -> SafetySupervisor:
    return SafetySupervisor(initial_state=SafetyState.PASSIVE, **kwargs)


# ---------------------------------------------------------------------------
# 1. No token, secret configured -> fail closed
# ---------------------------------------------------------------------------


def test_arm_tx_without_token_fails_closed() -> None:
    supervisor = _passive(auth_secret=SECRET)
    with pytest.raises(SafetyError):
        supervisor.arm_tx("operator arm")
    assert supervisor.current_state == SafetyState.PASSIVE
    assert supervisor.is_tx_permitted is False


def test_arm_tx_none_token_fails_closed() -> None:
    supervisor = _passive(auth_secret=SECRET)
    with pytest.raises(SafetyError):
        supervisor.arm_tx("operator arm", auth_token=None)
    assert supervisor.current_state == SafetyState.PASSIVE


def test_activate_tx_without_token_fails_closed() -> None:
    supervisor = _passive(auth_secret=SECRET)
    # Reach ARMED_TX legitimately, then attack the second half of the gate.
    supervisor.arm_tx("arm", auth_token=supervisor.issue_arm_token())
    assert supervisor.current_state == SafetyState.ARMED_TX
    with pytest.raises(SafetyError):
        supervisor.activate_tx("stream")
    assert supervisor.current_state == SafetyState.ARMED_TX


# ---------------------------------------------------------------------------
# 2. Garbage / malformed / forged token -> fail closed (the exploit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_token",
    [
        "garbage",  # the exact exploit from the review
        "deadbeef" * 8,  # well-formed hex, wrong MAC
        "",
        "1.2.3",  # structurally wrong
        "not-a-token",
    ],
)
def test_arm_tx_garbage_token_fails_closed(bad_token: str) -> None:
    supervisor = _passive(auth_secret=SECRET)
    with pytest.raises(SafetyError):
        supervisor.arm_tx("operator arm", auth_token=bad_token)
    assert supervisor.current_state == SafetyState.PASSIVE
    assert supervisor.is_tx_permitted is False


def test_forged_token_with_attacker_secret_fails_closed() -> None:
    """A token minted under a DIFFERENT secret must not verify."""
    attacker = _passive(auth_secret=b"attacker-secret-attacker-secret!!")
    forged = attacker.issue_arm_token()
    victim = _passive(auth_secret=SECRET)
    with pytest.raises(SafetyError):
        victim.arm_tx("operator arm", auth_token=forged)
    assert victim.current_state == SafetyState.PASSIVE


# ---------------------------------------------------------------------------
# 3. Valid token -> authorized transition (happy path preserved)
# ---------------------------------------------------------------------------


def test_arm_tx_valid_token_transitions_to_armed_tx() -> None:
    supervisor = _passive(auth_secret=SECRET)
    supervisor.arm_tx("operator arm", auth_token=supervisor.issue_arm_token())
    assert supervisor.current_state == SafetyState.ARMED_TX
    assert supervisor.is_tx_permitted is True

    supervisor.activate_tx("stream", auth_token=supervisor.issue_arm_token())
    assert supervisor.current_state == SafetyState.ACTIVE


def test_arm_token_is_single_use() -> None:
    """A replayed arm token must fail closed (no double-spend of proof)."""
    supervisor = _passive(auth_secret=SECRET)
    token = supervisor.issue_arm_token()
    supervisor.arm_tx("arm", auth_token=token)
    supervisor.enter_passive_mode("back to passive")
    with pytest.raises(SafetyError):
        supervisor.arm_tx("replay", auth_token=token)
    assert supervisor.current_state == SafetyState.PASSIVE


def test_expired_arm_token_fails_closed() -> None:
    supervisor = _passive(auth_secret=SECRET)
    # TTL is coerced to a minimum of 1s; a 0-TTL mint expires immediately.
    token = supervisor.issue_arm_token(ttl_s=0.0)
    import time as _time

    _time.sleep(1.2)
    with pytest.raises(SafetyError):
        supervisor.arm_tx("late", auth_token=token)
    assert supervisor.current_state == SafetyState.PASSIVE


# ---------------------------------------------------------------------------
# 4. Test-only escape hatch: requires UCANLAB_TEST_MODE=1
# ---------------------------------------------------------------------------


def test_allow_unauthenticated_arm_requires_test_mode_to_construct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
    with pytest.raises(RuntimeError):
        _passive(auth_secret=SECRET, allow_unauthenticated_arm=True)


def test_allow_unauthenticated_arm_with_test_mode_warns_and_arms(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("UCANLAB_TEST_MODE", "1")
    with caplog.at_level(logging.WARNING):
        supervisor = _passive(auth_secret=SECRET, allow_unauthenticated_arm=True)
        supervisor.arm_tx("operator arm")  # no token
        assert supervisor.current_state == SafetyState.ARMED_TX
    assert any("allow_unauthenticated_arm=True" in r.getMessage() for r in caplog.records)


def test_allow_unauthenticated_arm_default_off_still_fails_closed() -> None:
    """The default (flag absent) must remain fail-closed even in test mode."""
    supervisor = _passive(auth_secret=SECRET)
    with pytest.raises(SafetyError):
        supervisor.arm_tx("operator arm")
    assert supervisor.current_state == SafetyState.PASSIVE

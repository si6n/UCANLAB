"""T57-F regression tests: H-1, H-2, H-3, O-1, O-2, D-3, D-5.

Every test below was written BEFORE its fix (TDD red -> green). Each one
pins the documented behaviour of a HIGH/other finding from
``docs/review/IYILESTIRME-PLANI.md`` §4 (task T57-F):

  * H-1  ``execute_diagnostic_action`` must accept the confirmation token
         ONLY from the explicit ``confirmation_token`` parameter — the
         legacy ``action["confirmation_token"]`` / ``action["token"]`` /
         string-valued ``user_confirmed`` fallbacks let the *caller's own
         payload* satisfy the "second, independent channel" invariant.
  * H-1b ``flash_start`` has the same fallback via ``config``.
  * H-2  ``DesktopApiBridge.heartbeat`` must feed the watchdog with a
         shared caller token (HMAC-compared inside the watchdog), so the
         800 ms liveness interlock is not driven by an anonymous stream.
  * H-2b ``arm_tx``'s opportunistic lease refresh must pass the same token.
  * H-3  ``TxWatchdogSupervisor.is_lease_valid`` is the per-frame gate the
         safety gateway consumes; a token-authorised lease must stay
         valid on the frame path (identity of the heartbeat is enforced,
         not just its timestamp).
  * O-1  ``arm_tx`` must refresh the lease BEFORE/independently of the
         driver-mode transition — the old ordering masked the start race
         by refreshing inside the arm critical section.
  * O-2  ``toggle_simulator`` must honour ``estop.is_engaged`` (the
         authoritative latch), not only the ``_is_estop`` UI mirror.
  * D-3  the unsigned ``_check_for_updates_unverified`` manifest path must
         be unreachable outside a test build.
  * D-5  ``sys.path.insert(0, <root>)`` must not run in a frozen build
         (the repo root would otherwise outrank every bundled module).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from src.safety.watchdog import TxWatchdogSupervisor

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# H-2 / H-2b: token-authenticated watchdog heartbeats
# ---------------------------------------------------------------------------


def test_watchdog_rejects_heartbeat_without_caller_token() -> None:
    """H-2: an anonymous heartbeat must NOT refresh the lease.

    Before the fix ``heartbeat(None)`` logged a warning and still reset
    ``_last_heartbeat_time`` — the 800 ms liveness interlock was driven by
    an identity-less stream.
    """
    from src.safety.state_machine import SafetyState, SafetySupervisor

    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)
    watchdog.arm_heartbeat_token("bridge-token")

    before = watchdog.remaining_lease_sec
    with pytest.raises(PermissionError):
        watchdog.heartbeat()  # no caller_token -> must fail closed
    # The lease must NOT have been refreshed by the anonymous call.
    assert watchdog.remaining_lease_sec <= before


def test_watchdog_rejects_heartbeat_with_wrong_caller_token() -> None:
    """H-2: HMAC ``compare_digest`` — a wrong/forged token is refused."""
    from src.safety.state_machine import SafetyState, SafetySupervisor

    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)
    watchdog.arm_heartbeat_token("right-token")

    with pytest.raises(PermissionError):
        watchdog.heartbeat("wrong-token")


def test_watchdog_accepts_heartbeat_with_correct_caller_token() -> None:
    """H-2: the shared bridge<->watchdog token is accepted."""
    from src.safety.state_machine import SafetyState, SafetySupervisor

    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)
    watchdog.arm_heartbeat_token("shared-token")
    watchdog.heartbeat("shared-token")

    assert watchdog.is_lease_valid is True


def test_bridge_heartbeat_passes_shared_token_to_watchdog() -> None:
    """H-2: ``DesktopApiBridge.heartbeat`` must authenticate its pulse.

    The bridge is the only UI-reachable heartbeat entry point; it must
    forward a token that the watchdog recognises instead of calling
    ``heartbeat()`` anonymously (the legacy path that logged a warning).
    """
    from src.safety.state_machine import SafetyState, SafetySupervisor
    from src.ui.desktop_app import DesktopApiBridge

    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    class _FakeApp:
        def __init__(self) -> None:
            self.watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)

    app = _FakeApp()
    bridge = DesktopApiBridge(app)  # type: ignore[arg-type]

    calls: list[Any] = []
    original = app.watchdog.heartbeat

    def _spy(caller_token: str | None = None) -> None:
        calls.append(caller_token)
        return original(caller_token)

    app.watchdog.heartbeat = _spy  # type: ignore[method-assign]

    assert bridge.heartbeat() is True
    assert calls and calls[0] is not None, "bridge heartbeat still anonymous (H-2)"
    # The forwarded token must actually satisfy the watchdog.
    assert app.watchdog.is_lease_valid is True


def test_bridge_heartbeat_token_is_actually_accepted_by_watchdog() -> None:
    """H-2: end-to-end via the real bridge object.

    The bridge must arm the watchdog with the shared token it then sends;
    the watchdog must not raise and the lease must be refreshed.
    """
    from src.safety.state_machine import SafetyState, SafetySupervisor
    from src.ui.desktop_app import DesktopApiBridge

    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)

    class _FakeApp:
        def __init__(self) -> None:
            self.watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0)

    app = _FakeApp()
    bridge = DesktopApiBridge(app)  # type: ignore[arg-type]

    # The bridge mints + arms a token; the first pulse succeeds.
    assert bridge.heartbeat() is True
    assert app.watchdog.is_lease_valid is True
    # Second pulse reuses the same token.
    assert bridge.heartbeat() is True
    assert app.watchdog.is_lease_valid is True


def test_arm_tx_refreshes_lease_before_driver_transition() -> None:
    """O-1: heartbeat must be refreshed BEFORE/independently of the arm step.

    The old code refreshed the lease *inside* the driver-mode critical
    section, right before the ARMED_TX flip — the start race was masked.
    The fix refreshes it (token-authenticated) before the transition.
    """
    from src.ui import desktop_app as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    # The refresh must appear before `_set_driver_listen_only(False)` inside arm_tx.
    arm_start = source.index("def arm_tx(")
    arm_end = source.index("def _mint_arm_token(", arm_start)
    arm_body = source[arm_start:arm_end]

    refresh_idx = arm_body.find("heartbeat(")
    driver_idx = arm_body.find("_set_driver_listen_only(False)")
    assert refresh_idx != -1, "arm_tx never refreshes the watchdog lease (O-1)"
    assert driver_idx != -1, "arm_tx no longer flips driver mode"
    assert refresh_idx < driver_idx, (
        "O-1: watchdog heartbeat must run BEFORE the driver-mode transition"
    )


# ---------------------------------------------------------------------------
# O-2: toggle_simulator must honour estop.is_engaged
# ---------------------------------------------------------------------------


def test_toggle_simulator_refuses_when_estop_object_engaged_but_flag_clear() -> None:
    """O-2: ``estop.is_engaged`` is authoritative; ``_is_estop`` may lag."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = object.__new__(UniversalCanDesktopApp)
    # Simulate the drifted state: UI mirror says "not latched" but the
    # real E-Stop object is engaged.
    app._is_estop = False
    app._is_simulating = False

    class _EngagedEstop:
        is_engaged = True

    app.estop = _EngagedEstop()  # type: ignore[assignment]

    before = app._is_simulating
    result = UniversalCanDesktopApp.toggle_simulator(app)
    assert result is before, "toggle_simulator ran while estop.is_engaged was True (O-2)"
    assert app._is_simulating is before


# ---------------------------------------------------------------------------
# D-3: unsigned manifest path unreachable outside a test build
# ---------------------------------------------------------------------------


def test_run_preflight_unsigned_manifest_refused_outside_test_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-3: ``custom_update_manifest`` must be refused in production."""
    from src.launcher.app import UniversalCanLauncher

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    launcher = UniversalCanLauncher(current_version="13.0.0")

    with pytest.raises(PermissionError):
        launcher.run_preflight(custom_update_manifest={"version": "99.0.0"})


def test_run_preflight_unsigned_manifest_allowed_in_test_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-3: the test-only parser stays usable under pytest (existing suite)."""
    from src.launcher.app import UniversalCanLauncher

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_x (call)")
    launcher = UniversalCanLauncher(current_version="13.0.0")
    report = launcher.run_preflight(custom_update_manifest={"version": "13.2.0"})
    assert report.update_info.latest_version == "13.2.0"


# ---------------------------------------------------------------------------
# D-5: sys.path.insert never runs in a frozen build
# ---------------------------------------------------------------------------


def test_main_path_insert_skipped_when_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-5: a frozen build must not prepend the repo root to ``sys.path``."""
    import importlib

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "path", ["<frozen>"])
    original_path = list(sys.path)

    mod = importlib.reload(importlib.import_module("src.main"))
    try:
        assert _REPO_ROOT.as_posix() not in [p.replace("\\", "/") for p in sys.path]
    finally:
        sys.path[:] = original_path
        monkeypatch.delattr(sys, "frozen", raising=False)
        importlib.reload(mod)


def test_launcher_path_insert_skipped_when_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-5: same guard for the launcher bootstrap."""
    import importlib

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "path", ["<frozen>"])
    original_path = list(sys.path)

    mod = importlib.reload(importlib.import_module("src.launcher.app"))
    try:
        assert _REPO_ROOT.as_posix() not in [p.replace("\\", "/") for p in sys.path]
    finally:
        sys.path[:] = original_path
        monkeypatch.delattr(sys, "frozen", raising=False)
        importlib.reload(mod)


# ---------------------------------------------------------------------------
# H-1 / H-1b: confirmation token only from the explicit parameter
# ---------------------------------------------------------------------------


def test_diagnostic_action_rejects_token_from_action_payload() -> None:
    """H-1: ``action["confirmation_token"]`` must not satisfy confirmation.

    Build a real app with a *issued* token, then smuggle it through the
    caller-controlled ``action`` dict — the call must be refused because
    only the explicit ``confirmation_token`` parameter is trusted.
    """
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = object.__new__(UniversalCanDesktopApp)
    app._is_estop = False
    app._is_simulating = True
    app._current_speed_kmh = 0.0

    class _Estop:
        is_engaged = False

    app.estop = _Estop()  # type: ignore[assignment]

    captured: dict[str, Any] = {}

    def _fake_verify(token: Any, action_type: str, action_id: str) -> tuple[bool, str]:
        captured["token"] = token
        return False, "no token"

    app._verify_and_consume_diagnostic_token = _fake_verify  # type: ignore[attr-defined]

    action = {
        "type": "clear_dtc",
        "id": "x",
        "confirmation_token": "smuggled",
        "params": {},
    }
    # The verifier must receive `None` (the explicit param is None) — NOT the
    # smuggled action["confirmation_token"].
    UniversalCanDesktopApp.execute_diagnostic_action(
        app, action, confirmation_token=None, user_confirmed=False
    )
    assert captured.get("token") is None, (
        "H-1: action['confirmation_token'] was trusted as the independent token"
    )


def test_diagnostic_action_rejects_string_user_confirmed() -> None:
    """H-1: a string ``user_confirmed`` must not be treated as a token."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = object.__new__(UniversalCanDesktopApp)
    app._is_estop = False
    app._is_simulating = True
    app._current_speed_kmh = 0.0

    class _Estop:
        is_engaged = False

    app.estop = _Estop()  # type: ignore[assignment]
    captured: dict[str, Any] = {}

    def _fake_verify(token: Any, action_type: str, action_id: str) -> tuple[bool, str]:
        captured["token"] = token
        return False, "no token"

    app._verify_and_consume_diagnostic_token = _fake_verify  # type: ignore[attr-defined]

    action = {"type": "clear_dtc", "id": "x", "params": {}}
    UniversalCanDesktopApp.execute_diagnostic_action(
        app, action, confirmation_token=None, user_confirmed="fake-token"
    )
    assert captured.get("token") is None, (
        "H-1: string user_confirmed was trusted as the independent token"
    )


def test_flash_start_rejects_token_from_config() -> None:
    """H-1b: ``config["confirmation_token"]``/``config["token"]`` ignored."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = object.__new__(UniversalCanDesktopApp)
    app._is_estop = False
    app._is_simulating = True
    app._current_speed_kmh = 0.0

    class _Estop:
        is_engaged = False

    app.estop = _Estop()  # type: ignore[assignment]

    captured: dict[str, Any] = {}

    def _fake_verify(token: Any, action_type: str, action_id: str) -> tuple[bool, str]:
        captured["token"] = token
        return False, "no token"

    app._verify_and_consume_diagnostic_token = _fake_verify  # type: ignore[attr-defined]

    config = {
        "confirmation_token": "smuggled",
        "token": "smuggled2",
    }
    UniversalCanDesktopApp.flash_start(app, config, confirmation_token=None)
    assert captured.get("token") is None, (
        "H-1b: config['confirmation_token']/['token'] was trusted as the token"
    )

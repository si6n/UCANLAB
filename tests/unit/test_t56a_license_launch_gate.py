"""T56-A regression tests for finding L-1: the license validity must be a
hard launch gate (fail-closed).

Finding L-1 (`docs/review/ASAMA-2-lisans.md`): `UniversalCanLauncher.run_preflight`
computed

    can_launch = not has_critical_failures and target_exe.exists() and not has_blocking_mandatory_update

with no reference to `auth_status.has_valid_license`, so a machine with no
license / an expired ticket / a device mismatch still launched the core
binary. The preflight only printed the license state; it never enforced it.

Each test below was written BEFORE the fix (TDD red -> green):

  * an unlicensed AuthStatus must make `can_launch` False (P0 gate)
  * an expired / device-mismatch AuthStatus must make `can_launch` False
  * `--launch` must not bypass the gate (it goes through `report.can_launch`)
  * `_dev_override_enabled()` must never widen the license gate
  * a CRITICAL log carrying tier + reason is emitted when the gate closes
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.launcher import app as app_mod
from src.launcher.app import UniversalCanLauncher
from src.launcher.auth import AuthStatus, LauncherAuthManager
from src.launcher.prereqs import PrereqStatus
from src.launcher.updater import UpdateInfo
from src.safety.secret_provider import EphemeralSecretBackend
from src.security.cloud.client import CloudClient, CloudConfig


def _no_critical_prereqs() -> list[PrereqStatus]:
    """Prereq list with no critical failure — every other gate green."""
    return [PrereqStatus(name="dummy", is_available=True, details="ok", is_critical=True)]


def _no_update() -> UpdateInfo:
    return UpdateInfo(
        has_update=False,
        current_version="13.0.0",
        latest_version="13.0.0",
        check_succeeded=True,
    )


def _make_launcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_status: AuthStatus,
    target_executable: Path | None = None,
) -> UniversalCanLauncher:
    """Launcher whose ONLY unsatisfied gate is the license (by default)."""
    launcher = UniversalCanLauncher(current_version="13.0.0")

    monkeypatch.setattr(
        app_mod.PrereqChecker,
        "run_all_checks",
        classmethod(lambda cls: _no_critical_prereqs()),
    )
    monkeypatch.setattr(launcher.update_manager, "check_for_updates", lambda: _no_update())
    monkeypatch.setattr(launcher.auth_manager, "get_current_status", lambda: auth_status)
    if target_executable is not None:
        monkeypatch.setattr(
            UniversalCanLauncher,
            "resolve_target_executable",
            classmethod(lambda cls: target_executable),
        )
    return launcher


def _wire_main(
    monkeypatch: pytest.MonkeyPatch,
    launcher: UniversalCanLauncher,
) -> list[Any]:
    """Wire `app.main()` to `launcher` and capture launch attempts -> argv list."""
    launched: list[Any] = []

    def _record_launch(*args: Any, **kwargs: Any) -> int:
        launched.append((args, kwargs))
        return 0

    monkeypatch.setattr(launcher, "launch_main_app", _record_launch)
    monkeypatch.setattr(app_mod, "UniversalCanLauncher", lambda *a, **kw: launcher)
    return launched


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Invoke `app.main()` with a controlled argv (monkeypatched sys module)."""
    monkeypatch.setattr(app_mod.sys, "argv", argv)
    return app_mod.main()


def _status(**overrides: Any) -> AuthStatus:
    base: dict[str, Any] = {
        "is_authenticated": False,
        "has_valid_license": False,
        "hwid": "A" * 32,
        "tier": "FREE",
    }
    base.update(overrides)
    return AuthStatus(**base)


def test_unlicensed_status_blocks_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-1: a machine with no valid license must NOT be launchable."""
    target = Path(__file__).resolve().parent.parent.parent / "src" / "main.py"
    assert target.exists(), "test fixture expects src/main.py to exist"

    launcher = _make_launcher(monkeypatch, auth_status=_status(has_valid_license=False, tier="FREE"))
    report = launcher.run_preflight()

    assert report.target_executable.exists()
    assert report.auth_status.has_valid_license is False
    assert report.can_launch is False, "license gate missing: unlicensed machine can launch"


def test_expired_status_blocks_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-1: an expired ticket (tier=EXPIRED, per auth.py) must also block."""
    launcher = _make_launcher(
        monkeypatch,
        auth_status=_status(has_valid_license=False, tier="EXPIRED", expires_at=1),
    )
    report = launcher.run_preflight()

    assert report.auth_status.tier == "EXPIRED"
    assert report.auth_status.has_valid_license is False
    assert report.can_launch is False


def test_device_mismatch_status_blocks_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-1: a device-mismatch ticket must also block."""
    launcher = _make_launcher(
        monkeypatch,
        auth_status=_status(is_authenticated=True, has_valid_license=False, tier="PRO"),
    )
    report = launcher.run_preflight()

    assert report.can_launch is False


def test_valid_license_can_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: with a valid license and every other gate green, launch is allowed."""
    launcher = _make_launcher(
        monkeypatch,
        auth_status=_status(has_valid_license=True, is_authenticated=True, tier="PRO"),
    )
    report = launcher.run_preflight()

    assert report.can_launch is True


def test_launch_flag_does_not_bypass_license_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-1: the real `--launch` CLI path goes through `report.can_launch` ->
    it must be blocked, and `launch_main_app` must never be reached.

    (Exercises the true `app.main()` path, not a re-derived expression.)
    """
    launcher = _make_launcher(monkeypatch, auth_status=_status(has_valid_license=False))
    launched = _wire_main(monkeypatch, launcher)

    rc = _run_main(monkeypatch, ["launcher", "--launch"])

    assert rc == 1, "--launch opened the app without a license"
    assert launched == [], "launch_main_app must not be reached without a license"


def test_check_only_reports_failure_without_license(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-1: `--check-only` must report failure (rc=1) without a license, too."""
    launcher = _make_launcher(monkeypatch, auth_status=_status(has_valid_license=False))
    launched = _wire_main(monkeypatch, launcher)

    rc = _run_main(monkeypatch, ["launcher", "--check-only"])

    assert rc == 1
    assert launched == []


def test_dev_override_never_relaxes_license_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-1: the dev override must never widen the license gate."""
    monkeypatch.setenv("UCAN_LAUNCHER_DEV_OVERRIDE", "1")
    assert app_mod._dev_override_enabled() is True

    launcher = _make_launcher(monkeypatch, auth_status=_status(has_valid_license=False))
    launched = _wire_main(monkeypatch, launcher)

    rc = _run_main(monkeypatch, ["launcher", "--launch"])

    assert rc == 1, "dev override relaxed the license gate"
    assert launched == []


def test_license_gate_blocks_even_when_dev_override_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-1 (unit-level): the preflight report itself is closed regardless of
    the dev override, so no caller can widen it."""
    monkeypatch.setenv("UCAN_LAUNCHER_DEV_OVERRIDE", "1")

    launcher = _make_launcher(monkeypatch, auth_status=_status(has_valid_license=False))
    report = launcher.run_preflight()

    assert report.can_launch is False


def test_unlicensed_main_prints_activation_guidance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """L-1: a blocked launch must tell the operator how to activate."""
    launcher = _make_launcher(monkeypatch, auth_status=_status(has_valid_license=False))
    _wire_main(monkeypatch, launcher)

    rc = _run_main(monkeypatch, ["launcher", "--launch"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "--activate" in out, "no activation guidance printed on a blocked launch"
    assert "license" in out.lower()


def test_unlicensed_launch_emits_critical_log_with_tier(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """L-1: the closed gate must be logged at CRITICAL with tier + reason."""
    launcher = _make_launcher(
        monkeypatch,
        auth_status=_status(
            is_authenticated=True,
            has_valid_license=False,
            tier="EXPIRED",
            expires_at=1,
        ),
    )
    with caplog.at_level(logging.CRITICAL, logger="launcher.app"):
        report = launcher.run_preflight()

    assert report.can_launch is False
    critical = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical, "no CRITICAL record emitted for the blocked license gate"
    messages = " ".join(r.getMessage() for r in critical)
    assert "license" in messages.lower()
    # tier + reason must be visible in the structured payload or message.
    extras = " ".join(str(getattr(r, "tier", "")) + str(getattr(r, "reason", "")) for r in critical)
    combined = messages + extras
    assert "EXPIRED" in combined


def test_auth_manager_reports_unlicensed_on_empty_vault() -> None:
    """Sanity: a fresh vault has has_valid_license=False (the value the gate must read)."""
    secrets = EphemeralSecretBackend()
    priv = ed25519.Ed25519PrivateKey.generate()
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    auth = LauncherAuthManager(cloud_client=client, secret_provider=secrets, public_key=priv.public_key())

    status = auth.get_current_status()
    assert status.has_valid_license is False

"""Unit tests for WindowsPowerManager and KeepSystemAwake."""

import sys

import pytest

from src.hal.power.win32_power import KeepSystemAwake, WindowsPowerManager

# SetThreadExecutionState is a Windows-only kernel32 call. On Linux/macOS the
# manager deliberately no-ops, so the lease assertions below cannot hold there
# (GitLab CI's Linux runner exposed this). Keep the tests executing on Windows
# — where the real behaviour lives — instead of weakening the assertions.
windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows SetThreadExecutionState semantics"
)


def test_win32_power_manager_calls() -> None:
    # Test execution across platforms (graceful fallback on non-Windows / no crash on Win32)
    assert WindowsPowerManager.prevent_sleep(keep_display_on=True) is True
    assert WindowsPowerManager.restore_sleep() is True

    # Test context manager
    with KeepSystemAwake(keep_display_on=False):
        pass


@windows_only
def test_nested_keep_awake_inner_release_keeps_outer_active() -> None:
    """D10: the inner context's exit must NOT restore sleep while the outer
    context still holds its lease (SetThreadExecutionState is per-thread)."""
    from src.hal.power.win32_power import KeepSystemAwake, WindowsPowerManager

    WindowsPowerManager.reset_for_testing()

    with KeepSystemAwake():
        assert WindowsPowerManager.is_active() is True
        with KeepSystemAwake():
            assert WindowsPowerManager.is_active() is True
        # Inner exited — outer lease must still hold
        assert WindowsPowerManager.is_active() is True
    # Outer exited — now and only now released
    assert WindowsPowerManager.is_active() is False

    WindowsPowerManager.reset_for_testing()


@windows_only
def test_unbalanced_restore_sleep_is_rejected() -> None:
    """D10: restore without a matching prevent logs-and-refuses instead of
    silently restoring kernel sleep under someone else's lease."""
    from src.hal.power.win32_power import WindowsPowerManager

    WindowsPowerManager.reset_for_testing()
    assert WindowsPowerManager.restore_sleep() is False
    WindowsPowerManager.reset_for_testing()

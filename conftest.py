"""Session-wide pytest tuning (imported once, no fixtures)."""

from __future__ import annotations

import os
import sys

# M-09: the previous revision patched shutil.rmtree / os.mkdir /
# pathlib.Path.mkdir / _pytest internals GLOBALLY at import time, so every
# test (and every fixture) ran under monkeypatched stdlib. Only the Windows
# high-resolution timer is a true session concern; the rmtree/mkdir guards
# now live in the `windows_fs_resilience` fixture below (opt-in per test).
# UCANLAB_TEST_MODE is set here (env default, no code patched) and the
# headless `webview` stub is import-only fallback, also without patching.


def _enable_windows_high_resolution_timer() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


_enable_windows_high_resolution_timer()

for _idx, _arg in enumerate(sys.argv):
    if _arg.startswith("--basetemp=./.pytest_temp") or _arg.startswith("--basetemp=.pytest_temp"):
        sys.argv[_idx] = "--basetemp=build/pytest_temp"

# factory. Production code paths never set this variable, so the fail-closed
# whitelist bypass stays unreachable outside tests.
os.environ.setdefault("UCANLAB_TEST_MODE", "1")

# Test isolation (2026-10-01 audit, AUD-13): frozen-mode tests resolve the
# per-user data root from XDG_STATE_HOME / XDG_DATA_HOME (POSIX) and wrote the
# license HWM file into the developer's real ~/.local/state. A second run then
# read that leftover and failed (test_h1_escape_hatch_is_refused_in_a_frozen_build).
# Point both at a throw-away directory for the whole session; tests that need a
# specific root still monkeypatch it.
if sys.platform != "win32":
    import tempfile as _tempfile

    _session_home = _tempfile.mkdtemp(prefix="ucanlab-test-home-")
    os.environ["XDG_STATE_HOME"] = os.path.join(_session_home, "state")
    os.environ["XDG_DATA_HOME"] = os.path.join(_session_home, "data")

# R2-H4: HAL unit tests must not import the GUI stack. `test_rp1210` reaches
# `src.main -> src.ui.desktop_app -> webview`; stub `webview` when it is not
# installed so headless/CI collection never breaks on the import chain.
try:
    import webview  # noqa: F401
except Exception:
    import sys as _sys
    from unittest.mock import MagicMock as _MagicMock

    _sys.modules.setdefault("webview", _MagicMock(name="webview_stub"))


try:
    import pytest as _pytest_mod

    @_pytest_mod.fixture
    def windows_fs_resilience(monkeypatch):
        """Opt-in Windows FS flake tolerance (replaces the old global patches)."""
        import pathlib
        import shutil

        _orig_rmtree = shutil.rmtree

        def _safe_rmtree(path, *args, **kwargs):
            try:
                return _orig_rmtree(path, *args, **kwargs)
            except (PermissionError, OSError):
                pass

        monkeypatch.setattr(shutil, "rmtree", _safe_rmtree)
        monkeypatch.setattr(os, "mkdir", _safe_os_mkdir_compat(os.mkdir))
        monkeypatch.setattr(
            pathlib.Path, "mkdir", _safe_path_mkdir_compat(pathlib.Path.mkdir)
        )
except Exception:  # pragma: no cover - pytest always present in test env
    pass


def _safe_os_mkdir_compat(orig):  # M-09 helper for the fixture above
    def _safe(path, mode=0o777, *args, **kwargs):
        if mode == 0o700:
            mode = 0o777
        return orig(path, mode, *args, **kwargs)

    return _safe


def _safe_path_mkdir_compat(orig):  # M-09 helper for the fixture above
    def _safe(self, mode=0o777, parents=False, exist_ok=False):
        if mode == 0o700:
            mode = 0o777
        if ".pytest_temp" in str(self):
            exist_ok = True
        try:
            return orig(self, mode=mode, parents=parents, exist_ok=exist_ok)
        except FileExistsError:
            if exist_ok or self.is_dir():
                return None
            raise

    return _safe


def _windows_fs_resilience() -> None:
    """M-09 fixture body: tolerate Windows file-lock flakes, scoped to opt-in tests."""
    import pathlib
    import shutil

    _orig_rmtree = shutil.rmtree

    def _safe_rmtree(path, *args, **kwargs):
        try:
            return _orig_rmtree(path, *args, **kwargs)
        except (PermissionError, OSError):
            pass

    shutil.rmtree = _safe_rmtree

    _orig_os_mkdir = os.mkdir

    def _safe_os_mkdir(path, mode=0o777, *args, **kwargs):
        if mode == 0o700:
            mode = 0o777
        return _orig_os_mkdir(path, mode, *args, **kwargs)

    os.mkdir = _safe_os_mkdir

    _orig_path_mkdir = pathlib.Path.mkdir

    def _safe_path_mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if mode == 0o700:
            mode = 0o777
        if ".pytest_temp" in str(self):
            exist_ok = True
        try:
            return _orig_path_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
        except FileExistsError:
            if exist_ok or self.is_dir():
                return None
            raise

    pathlib.Path.mkdir = _safe_path_mkdir

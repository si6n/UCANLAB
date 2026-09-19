"""Session-wide pytest tuning (imported once, no fixtures)."""

from __future__ import annotations

import ctypes
import os
import pathlib
import shutil
import sys


def _enable_windows_high_resolution_timer() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


_enable_windows_high_resolution_timer()

for _idx, _arg in enumerate(sys.argv):
    if _arg.startswith("--basetemp=./.pytest_temp") or _arg.startswith("--basetemp=.pytest_temp"):
        sys.argv[_idx] = "--basetemp=build/pytest_temp"

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

try:
    import _pytest.pathlib
    _orig_cleanup = _pytest.pathlib.cleanup_dead_symlinks
    def _safe_cleanup(root):
        try:
            return _orig_cleanup(root)
        except (PermissionError, OSError):
            pass
    _pytest.pathlib.cleanup_dead_symlinks = _safe_cleanup
    _pytest.pathlib.on_rm_rf_error = lambda *args, **kwargs: None

    _orig_rm_rf = _pytest.pathlib.rm_rf
    def _safe_rm_rf(p):
        try:
            return _orig_rm_rf(p)
        except (PermissionError, OSError):
            pass
    _pytest.pathlib.rm_rf = _safe_rm_rf
except Exception:
    pass

try:
    import _pytest.tmpdir
    _pytest.tmpdir.cleanup_dead_symlinks = lambda *args, **kwargs: None
    _orig_getbasetemp = _pytest.tmpdir.TempPathFactory.getbasetemp
    def _patched_getbasetemp(self):
        if self._basetemp is not None:
            return self._basetemp
        if self._given_basetemp is not None:
            raw_str = str(self._given_basetemp)
            if ".pytest_temp" in raw_str:
                target = pathlib.Path("pytest_temp_workspace").resolve()
                target.mkdir(parents=True, exist_ok=True)
                self._basetemp = target
                return target
        return _orig_getbasetemp(self)
    _pytest.tmpdir.TempPathFactory.getbasetemp = _patched_getbasetemp
except Exception:
    pass
# factory. Production code paths never set this variable, so the fail-closed
# whitelist bypass stays unreachable outside tests.
os.environ.setdefault("UCANLAB_TEST_MODE", "1")

# R2-H4: HAL unit tests must not import the GUI stack. `test_rp1210` reaches
# `src.main -> src.ui.desktop_app -> webview`; stub `webview` when it is not
# installed so headless/CI collection never breaks on the import chain.
try:
    import webview  # noqa: F401
except Exception:
    import sys as _sys
    from unittest.mock import MagicMock as _MagicMock

    _sys.modules.setdefault("webview", _MagicMock(name="webview_stub"))

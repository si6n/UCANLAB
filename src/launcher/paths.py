"""Launcher path anchors (H-5 / H-3).

FROZEN-BUILD CONTRACT (H-5)
==========================
The launcher is a PyInstaller/Nuitka ``--onefile`` bootstrap. Before this
module existed, every launcher path was derived from

    ``Path(__file__).resolve().parent.parent.parent``

which is the *build machine's* source tree — it does not exist on the target
machine. A frozen launcher therefore never found its sibling payload or its
hash manifest and silently fell back to a raw ``src/main.py`` that is not
bundled at all.

Three distinct roots are required, mirroring ``src/ui/desktop_app.py``:

``resource_root()``
    Read-only bundled assets (``target.hash``, ``data/``). For a onefile build
    PyInstaller extracts these into ``sys._MEIPASS`` (a temporary directory
    that is DELETED on exit) — always resolve this at call time, never cache.

``app_root()``
    The executable's own directory: writable, persistent, and the sibling of
    the payload artifact. On Windows ``sys.executable`` is the canonical
    anchor; ``Path.cwd()`` (the Nuitka fallback) is used only when
    ``sys.executable`` is unavailable, because the CWD is caller-influenced.

``repo_root()`` / ``app_data_root()``
    Source-tree checkout and the persistent per-user data root used for the
    mandatory-update obligation record.

Every anchored resolution can be overridden with ``UCANLAB_LAUNCHER_ROOT`` for
packaging pipelines that stage the payload in a directory that is not the
executable's own directory (same escape hatch in both frozen and source mode,
so tests never depend on ``sys.frozen``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Highest-precedence root override (packaging / tests). Empty by default.
LAUNCHER_ROOT_ENV = "UCANLAB_LAUNCHER_ROOT"

#: Persistent per-user data directory name (H-3 obligation record).
APP_DATA_DIR_NAME = "UCANLab Launcher"


def _env_root() -> Path | None:
    raw = str(os.environ.get(LAUNCHER_ROOT_ENV, "")).strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:  # pragma: no cover - defensive
        return None


def repo_root() -> Path:
    """Source-tree checkout root (``<root>/src/launcher/paths.py`` -> ``<root>``)."""
    return Path(__file__).resolve().parent.parent.parent


def is_frozen() -> bool:
    """True inside a PyInstaller/Nuitka frozen build."""
    return bool(getattr(sys, "frozen", False)) or bool(getattr(sys, "_MEIPASS", None))


def resource_root() -> Path:
    """Read-only bundled-resource root (``_MEIPASS`` when frozen)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return repo_root()


def app_root() -> Path:
    """Writable root that holds the launcher payload and the packaged manifest.

    Precedence (H-5):

    1. ``UCANLAB_LAUNCHER_ROOT`` override, when set.
    2. frozen build -> ``Path(sys.executable).resolve().parent`` — the launcher
       is the parent process, so its sibling ``dist/`` tree is the payload root.
    3. source checkout -> the repo root (the historical behaviour).
    """
    override = _env_root()
    if override is not None:
        return override
    if getattr(sys, "frozen", False):
        exe = str(getattr(sys, "executable", "") or "")
        if exe:
            return Path(exe).resolve().parent
        # Nuitka always sets sys.executable; keep a documented fallback.
        cwd = str(Path.cwd())
        if cwd:
            return Path(cwd).resolve()
    return repo_root()


def app_data_root() -> Path:
    """Persistent writable per-user data root for launcher state (H-3).

    ``%LOCALAPPDATA%``/``$XDG_DATA_HOME`` when available; the source tree is
    NOT used, so a source checkout, a frozen launcher and the desktop app all
    agree on one location. Deliberately not the vault: the obligation record
    must be readable even when nothing has been written to the vault yet.
    """
    override = _env_root()
    if override is not None:
        return override / "launcher-state"
    base: Path | None = None
    if sys.platform == "win32":
        local = str(os.environ.get("LOCALAPPDATA", "")).strip()
        if local:
            base = Path(local)
    else:
        xdg = str(os.environ.get("XDG_DATA_HOME", "")).strip()
        if xdg:
            base = Path(xdg)
        else:
            home = str(os.environ.get("HOME", "")).strip()
            if home:
                base = Path(home) / ".local" / "share"
    if base is None:
        base = Path.home()
    try:
        return (base / APP_DATA_DIR_NAME).resolve()
    except OSError:  # pragma: no cover - defensive
        return base / APP_DATA_DIR_NAME

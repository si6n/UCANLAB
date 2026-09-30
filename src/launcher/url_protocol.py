"""Per-user ``ucanlab://`` URL protocol registration (Windows).

The web sign-in page offers "Open in the app" as a second way to return the
single-use sign-in code (see ``src/security/cloud/desktop_auth.py``). Windows
maps the scheme to a command line through ``HKCU\\Software\\Classes\\ucanlab``;
writing under HKCU needs no admin rights and affects only the current user.

The registered command passes the URL as ONE quoted argument, so a crafted URL
cannot inject extra command-line switches. The receiving process validates the
URL strictly (``parse_deep_link``) and only forwards code + state to the
running app's loopback listener.

Not verified on real Windows in CI: see docs/product/HARDWARE_TEST_CHECKLIST.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

logger = get_logger("launcher.url_protocol")

SCHEME = "ucanlab"
_CLASSES_KEY = rf"Software\Classes\{SCHEME}"


def protocol_command(executable: Path, script: Path | None = None) -> str:
    """The ``shell\\open\\command`` value. ``%1`` is always a single quoted argument."""
    if script is None:
        return f'"{executable}" "%1"'
    return f'"{executable}" "{script}" "%1"'


def _default_target() -> tuple[Path, Path | None]:
    if getattr(sys, "frozen", False):
        return Path(sys.executable), None
    # Source checkout: python.exe <repo>/main.py "%1"
    return Path(sys.executable), Path(__file__).resolve().parents[2] / "main.py"


def register_url_protocol(winreg_module: Any | None = None, executable: Path | None = None,
                          script: Path | None = None) -> bool:
    """Register ``ucanlab://`` for the current user. Returns False off Windows.

    ``winreg_module`` is a test seam; production passes nothing.
    """
    if winreg_module is None:
        if sys.platform != "win32":
            logger.info("ucanlab:// registration skipped: not Windows")
            return False
        import winreg as winreg_module  # type: ignore[no-redef]

    if executable is None:
        executable, script = _default_target()

    reg = winreg_module
    try:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, _CLASSES_KEY) as key:
            reg.SetValueEx(key, "", 0, reg.REG_SZ, "URL:UCanLab")
            reg.SetValueEx(key, "URL Protocol", 0, reg.REG_SZ, "")
        with reg.CreateKey(reg.HKEY_CURRENT_USER, _CLASSES_KEY + r"\shell\open\command") as key:
            reg.SetValueEx(key, "", 0, reg.REG_SZ, protocol_command(executable, script))
    except OSError as exc:
        logger.warning("ucanlab:// registration failed", extra={"error": type(exc).__name__})
        return False
    logger.info("ucanlab:// protocol registered for current user")
    return True

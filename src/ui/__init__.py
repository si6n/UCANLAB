"""UI Subsystem for Universal CAN-Bus Diagnostic Platform."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path when imported without package context
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp
except ImportError:
    from .desktop_app import DesktopApiBridge, UniversalCanDesktopApp

__all__ = ["DesktopApiBridge", "UniversalCanDesktopApp"]

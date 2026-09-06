"""Windows 10/11 x64 Power Management & USB Sleep Prevention.

Prevents laptop USB selective suspend and system sleep during high-speed telemetry recording.
Complies with MASTER_PLAN.md Section 9.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import ClassVar, Self

from src.core.logging import get_logger

logger = get_logger("hal.power")

# Windows Execution State Flags
ES_SYSTEM_REQUIRED: int = 0x00000001
ES_DISPLAY_REQUIRED: int = 0x00000002
ES_AWAYMODE_REQUIRED: int = 0x00000040
ES_CONTINUOUS: int = 0x80000000


class WindowsPowerManager:
    """Controls Windows kernel thread execution state to prevent USB and system sleep.

    SetThreadExecutionState is per-thread: a nested context releasing its hold
    would silently drop the outer context's protection. A per-thread reference count
    (H-2) keeps the kernel awake for each thread until its LAST holder releases it.
    """

    _leases: ClassVar[dict[int, int]] = {}
    _display_required: ClassVar[dict[int, bool]] = {}
    _is_active: ClassVar[bool] = False
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def prevent_sleep(cls, keep_display_on: bool = False) -> bool:
        """Tell Windows kernel to keep system awake and USB controllers powered."""
        if sys.platform != "win32":
            return True

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return False

        tid = threading.get_ident()
        with cls._lock:
            depth = cls._leases.get(tid, 0)
            cls._leases[tid] = depth + 1
            prev_disp = cls._display_required.get(tid, False)
            if keep_display_on and not prev_disp:
                cls._display_required[tid] = True
            elif depth > 0:
                return True

        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        if keep_display_on or cls._display_required.get(tid, False):
            flags |= ES_DISPLAY_REQUIRED

        try:
            prev_state = windll.kernel32.SetThreadExecutionState(flags)
            if prev_state != 0:
                with cls._lock:
                    cls._is_active = True
                logger.info("SetThreadExecutionState: System Sleep Prevention ACTIVATED")
                return True
            with cls._lock:
                cls._leases[tid] = depth
        except (AttributeError, OSError, RuntimeError) as exc:
            logger.warning("Failed to invoke SetThreadExecutionState", extra={"error": str(exc)})
            with cls._lock:
                cls._leases[tid] = depth

        return False

    @classmethod
    def restore_sleep(cls) -> bool:
        """Restore normal Windows power management behavior for the calling thread."""
        if sys.platform != "win32":
            return True

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return False

        tid = threading.get_ident()
        with cls._lock:
            depth = cls._leases.get(tid, 0)
            if depth <= 0:
                logger.warning("restore_sleep called without a matching prevent_sleep")
                return False
            cls._leases[tid] = depth - 1
            if depth - 1 > 0:
                return True
            cls._leases.pop(tid, None)
            cls._display_required.pop(tid, None)
            if not cls._leases:
                cls._is_active = False

        try:
            prev_state = windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            if prev_state != 0:
                logger.info("SetThreadExecutionState: Normal Power Management RESTORED")
                return True
        except (AttributeError, OSError, RuntimeError) as exc:
            logger.warning("Failed to restore SetThreadExecutionState", extra={"error": str(exc)})

        return False

    @classmethod
    def is_active(cls) -> bool:
        with cls._lock:
            return cls._is_active

    @classmethod
    def reset_for_testing(cls) -> None:
        """Clear reference state between tests."""
        with cls._lock:
            cls._leases.clear()
            cls._is_active = False


class KeepSystemAwake:
    """Context manager for scoped sleep prevention during diagnostics and flashing.

    Nestable (D10): the inner exit does NOT restore kernel sleep while an
    outer context still holds its lease.
    """

    def __init__(self, keep_display_on: bool = False) -> None:
        self.keep_display_on = keep_display_on

    def __enter__(self) -> Self:
        WindowsPowerManager.prevent_sleep(keep_display_on=self.keep_display_on)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        WindowsPowerManager.restore_sleep()

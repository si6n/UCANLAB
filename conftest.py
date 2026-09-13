"""Session-wide pytest tuning (imported once, no fixtures)."""

from __future__ import annotations

import ctypes
import os
import sys


def _enable_windows_high_resolution_timer() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


_enable_windows_high_resolution_timer()

# Test harness: allow the explicit test-only TxSafetyGateway.for_testing()
# factory. Production code paths never set this variable, so the fail-closed
# whitelist bypass stays unreachable outside tests.
os.environ.setdefault("UCANLAB_TEST_MODE", "1")

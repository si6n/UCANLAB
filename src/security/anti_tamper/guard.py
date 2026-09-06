"""Win32 Anti-Debug, Integrity Verification & Anti-Tamper Protection Guard."""

from __future__ import annotations

import ctypes
import hashlib
import sys
import time
from collections.abc import Callable
from typing import ClassVar

from src.core.errors import SecurityError
from src.core.logging import get_logger

logger = get_logger("security.anti_tamper")


class AntiTamperGuard:
    """Detects active debuggers, process hooking, and execution tampering.

    API failures fail closed: an unenforceable check is treated as a violation
    rather than silently ignored (F-10).
    """

    @classmethod
    def is_debugger_present(cls) -> bool:
        """Query Win32 IsDebuggerPresent API. API failure fails closed."""
        if sys.platform != "win32":
            return False

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            raise SecurityError("Anti-tamper unable to probe IsDebuggerPresent", code="ANTI_TAMPER_VIOLATION")

        try:
            return bool(windll.kernel32.IsDebuggerPresent())
        except (AttributeError, OSError, RuntimeError) as exc:
            raise SecurityError(
                "Anti-tamper IsDebuggerPresent probe failed",
                code="ANTI_TAMPER_VIOLATION",
                cause=exc,
            ) from exc

    @classmethod
    def check_remote_debugger(cls) -> bool:
        """Query Win32 CheckRemoteDebuggerPresent API. API failure fails closed."""
        if sys.platform != "win32":
            return False

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            raise SecurityError("Anti-tamper unable to probe remote debugger", code="ANTI_TAMPER_VIOLATION")

        try:
            fn = windll.kernel32.CheckRemoteDebuggerPresent
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
            fn.restype = ctypes.c_int
            is_present = ctypes.c_bool(False)
            current_proc = windll.kernel32.GetCurrentProcess()
            res = fn(current_proc, ctypes.byref(is_present))
            if res == 0:
                raise SecurityError(
                    "CheckRemoteDebuggerPresent Win32 API call failed (fail closed)",
                    code="ANTI_TAMPER_VIOLATION",
                )
            if is_present.value:
                return True
        except (AttributeError, OSError, RuntimeError) as exc:
            raise SecurityError(
                "Anti-tamper remote debugger probe failed",
                code="ANTI_TAMPER_VIOLATION",
                cause=exc,
            ) from exc

        return False

    # B8 (REVIEW): 200 SHA-256 digests of an 11-byte input complete in
    # microseconds even on power-throttled workshop hardware — a 250 ms
    # threshold let hardware breakpoints and hypervisor single-step hooks
    # (5-20 ms of overhead) pass undetected, providing only a false sense
    # of security. 20 ms keeps a safety margin for worst-case CPU steal /
    # scheduler contention on rugged tablets while catching real
    # instrumentation. TWO consecutive breaches are still required below,
    # so a one-off GC pause or antivirus burst cannot flag a legitimate
    # operator.
    TIMING_THRESHOLD_MS: ClassVar[float] = 20.0

    @classmethod
    def detect_timing_anomaly(cls, threshold_ms: float | None = None) -> bool:
        """Measure SHA-256 probe timing to detect single-stepping instrumentation.

        REVIEW.md 5.1 / SEC-2: requires TWO consecutive threshold breaches —
        a one-off GC pause, antivirus scan burst, or scheduler hiccup must
        not trip the anti-tamper path.
        """
        effective_threshold = cls.TIMING_THRESHOLD_MS if threshold_ms is None else threshold_ms
        measurements: list[float] = []
        for _ in range(2):
            t0 = time.perf_counter_ns()
            for _ in range(200):
                hashlib.sha256(b"tamper_probe").digest()
            elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
            measurements.append(elapsed_ms)
            if elapsed_ms <= effective_threshold:
                return False
        logger.warning(
            "Sustained timing anomaly (2 consecutive probes above threshold)! Possible debugger single-stepping.",
            extra={
                "elapsed_ms": measurements[-1],
                "probe_timings_ms": measurements,
                "threshold_ms": effective_threshold,
            },
        )
        return True

    @classmethod
    def enforce(
        cls,
        on_violation: Callable[[str], None] | None = None,
        timing_threshold_ms: float | None = None,
    ) -> None:
        """Run all tamper probes; on violation invoke the injected action or fail closed."""
        violations: list[str] = []
        if cls.is_debugger_present():
            violations.append("debugger")
        if cls.check_remote_debugger():
            violations.append("remote_debugger")
        if cls.detect_timing_anomaly(timing_threshold_ms):
            violations.append("timing")

        if violations:
            reason = f"Anti-tamper: {', '.join(violations)}"
            logger.critical(reason)
            if on_violation is not None:
                on_violation(reason)
            else:
                raise SecurityError(reason, code="ANTI_TAMPER_VIOLATION")

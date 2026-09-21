"""Win32 Anti-Debug, Integrity Verification & Anti-Tamper Protection Guard."""

from __future__ import annotations

import ctypes
import hashlib
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
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
            fn = windll.kernel32.IsDebuggerPresent
            # SEC-05 (Batch B): the prototype was never declared, so ctypes
            # assumed the default (c_int) return. Win32 BOOL is a 4-byte int
            # and the value is zero/non-zero, so the bool() below is correct —
            # but declaring the real prototype keeps the ABI honest and makes
            # the call fail loudly if the symbol is ever a different shape.
            fn.argtypes = []
            fn.restype = wintypes.BOOL
            return bool(fn())
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
            # SEC-05 (Batch B): the prototypes were wrong in three ways and
            # made the probe unreliable on x64:
            #   * the out-parameter was declared as `POINTER(c_bool)` — c_bool
            #     is ONE byte while Win32 `BOOL` is a 4-byte int, so the API
            #     wrote 4 bytes into a 1-byte buffer (handing 3 bytes of
            #     adjacent stack memory a value derived from the result);
            #   * `restype` was c_int instead of BOOL (harmless in practice
            #     but not the declared ABI);
            #   * `wintypes` was never imported and `GetCurrentProcess` had no
            #     restype, so its 64-bit pseudo-handle was truncated to a
            #     32-bit int before being handed back to the API.
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
            fn.restype = wintypes.BOOL
            get_current_process = windll.kernel32.GetCurrentProcess
            get_current_process.argtypes = []
            get_current_process.restype = wintypes.HANDLE
            is_present = wintypes.BOOL(False)
            current_proc = get_current_process()
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

        REVIEW 5.1 / SEC-2: requires TWO consecutive threshold breaches —
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
        *,
        observe_only: bool = False,
    ) -> None:
        """Run all tamper probes and fail closed on any violation.

        SEC-06 (Batch B): the `raise` used to live in the `else` branch of
        ``if on_violation is not None``, so passing a *log-only* callback
        silenced the violation entirely — the F-10 anti-tamper contract was
        defeated by a parameter. A violation now ALWAYS raises; the callback,
        when supplied, is invoked first as a notification/telemetry hook.
        Observe-only behaviour is preserved as an EXPLICIT opt-in
        (``observe_only=True``, e.g. a diagnostics sweep that must not abort),
        never as an accidental side effect of passing a callback.
        """
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
            if observe_only:
                logger.warning(
                    "Anti-tamper violation observed in observe-only mode; not raising",
                    extra={"reason": reason},
                )
                return
            raise SecurityError(reason, code="ANTI_TAMPER_VIOLATION")

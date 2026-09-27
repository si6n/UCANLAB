"""Independent TX Watchdog and Heartbeat Lease Supervisor.

Complies with Saha Risk Kataloğu v1.2 Sections 20, 36.5, 38.
"""

from __future__ import annotations

import atexit
import os
import threading
from typing import TYPE_CHECKING, ClassVar

from src.core.contracts.ports import SystemClockProvider, VirtualClock
from src.core.logging import get_logger
from src.safety.estop import EStopTriggerSource

if TYPE_CHECKING:
    from src.core.contracts.ports import ClockProvider
    from src.safety.estop import EmergencyStopSystem
    from src.safety.state_machine import SafetySupervisor

logger = get_logger("safety.watchdog")

#: Environment flag acknowledging a virtualized (test) clock on the watchdog.
#: The real safety lease must run on `time.monotonic()`; a VirtualClock on the
#: production path silently freezes the 800 ms interlock, so it is refused
#: unless the harness explicitly opts in (S-11).
_TEST_MODE_ENV: str = "UCANLAB_TEST_MODE"

#: Fail-closed teardown message (S-17).
_PROCESS_TEARDOWN_REASON: str = (
    "PROCESS_TERMINATION: watchdog supervisor torn down at process exit "
    "— TX authorization revoked fail-closed"
)


class TxWatchdogSupervisor:
    """Independent supervisor thread enforcing monotonic heartbeat leases for transmission."""

    DEFAULT_TIMEOUT_MS: ClassVar[float] = 800.0  # 800ms lease duration (B8: README-documented value)
    CHECK_INTERVAL_SEC: ClassVar[float] = 0.050  # 50ms check loop resolution

    def __init__(
        self,
        supervisor: SafetySupervisor,
        estop: EmergencyStopSystem | None = None,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        clock: ClockProvider | None = None,
    ) -> None:
        # docs/ai_context/05 §4: timeout tests must inject a ClockProvider
        # (VirtualClock) instead of sleeping against the real clock. The
        # default remains the platform monotonic system clock.
        resolved = clock if clock is not None else SystemClockProvider()
        # S-11: the check is isinstance-based (the old `type(...).__name__`
        # string test missed subclasses) and FAILS CLOSED — a virtualized clock
        # on the watchdog would freeze lease expiry, so it is only tolerated
        # under the explicit test-mode acknowledgement.
        if isinstance(resolved, VirtualClock) and os.environ.get(_TEST_MODE_ENV) != "1":
            raise RuntimeError(
                "TxWatchdogSupervisor refuses a VirtualClock clock: the 800 ms "
                "lease must be anchored to monotonic time. Set "
                f"{_TEST_MODE_ENV}=1 to acknowledge a test harness."
            )
        if type(resolved).__name__ == "VirtualClock" and not isinstance(resolved, VirtualClock):
            # Duck-typed virtual clock (a test double that does not subclass
            # VirtualClock): warn loudly, the type system cannot prove this one.
            logger.warning(
                "TxWatchdogSupervisor wired with a duck-typed virtual clock "
                "(test clock in prod path?)"
            )
        # F2 / Aksiyon 19: Verify monotonic clock safety for production clock
        if not isinstance(resolved, VirtualClock):
            SystemClockProvider.validate_clock_safety()
        self._clock = resolved
        self.supervisor = supervisor
        self.estop = estop
        self.timeout_sec = max(0.050, timeout_ms / 1000.0)

        self._last_heartbeat_time = self._clock.now_monotonic()
        self._last_heartbeat_wall_ns: int = self._clock.now_wall_ns()
        self._is_running = False
        self._started_once = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # H-2: shared bridge<->watchdog heartbeat token. Empty until
        # `arm_heartbeat_token()` registers it; heartbeats are refused until
        # then (fail-closed) so no anonymous stream can hold the lease open.
        self._heartbeat_token: str = ""
        # P0-4 (REVIEW H-4): wakeable stop signal. The old monitor loop
        # parked in time.sleep(0.050) with a literal `while True:` condition
        # that never consulted _is_running — stop() could only wait 1 s for
        # a thread that NEVER exits, and a stop()/start() cycle leaked a
        # second immortal monitor. This Event both breaks the loop promptly
        # and makes the sleep interruptible.
        self._stop_event = threading.Event()
        # S-17: atexit teardown hook registration latch (idempotent).
        self._teardown_registered = False

        if self.supervisor:
            self.supervisor.register_callback(self._on_safety_state_changed)
            # REGRESSION 1 (S-02/S-08) wiring: let the supervisor's ARM path
            # anchor this lease through the H-2 token gate. The watchdog owns
            # the token, so it publishes itself + the SHARED TOKEN reference
            # (not a copy: rotation must be visible) via the supervisor's
            # `bind_watchdog`. Without this, ARM would have no token-gated way
            # to anchor the lease and S-08's `_is_running` requirement would
            # make every legitimate arm-then-transmit flow fail closed.
            binder = getattr(self.supervisor, "bind_watchdog", None)
            if binder is not None:
                try:
                    binder(self)
                except Exception as exc:  # noqa: BLE001 - never break construction
                    logger.error(
                        "Failed to bind watchdog to supervisor (arm-time lease anchor unavailable)",
                        extra={"error": str(exc)},
                    )

    @property
    def clock(self) -> ClockProvider:
        """Read-only clock handle (mutable security dependency guard)."""
        return self._clock

    def _on_safety_state_changed(self, old_state: object, new_state: object, reason: str) -> None:
        """Fail-closed lease invalidation on FAULT entry (S-02).

        The legacy body RE-ANCHORED ``_last_heartbeat_time`` whenever the
        supervisor entered ARMED_TX/ACTIVE — a second, token-free lease-refresh
        path that contradicted the H-2 contract enforced by :meth:`heartbeat`
        (which requires the shared caller token). Any caller able to drive a
        state transition could therefore hold the 800 ms liveness interlock
        open without ever presenting a token. The re-anchor is deleted; the
        only remaining effect of a state change is to INVALIDATE the lease on
        entry into a non-TX-bearing state, so expiry is re-derived from the
        real heartbeat (fail-closed), never from the transition.
        """
        state_val = getattr(new_state, "value", str(new_state))
        if state_val not in {"FAULT", "SAFE", "SAFE_STATE", "STARTUP", "PASSIVE"}:
            return
        with self._lock:
            # Push the lease into the past: `is_lease_valid` is False until a
            # token-authenticated heartbeat re-anchors it.
            self._last_heartbeat_time -= self.timeout_sec + 1.0
            self._last_heartbeat_wall_ns -= int((self.timeout_sec + 1.0) * 1_000_000_000)
        logger.warning(
            "Watchdog lease invalidated on safety state change (fail-closed)",
            extra={"from": getattr(old_state, "value", str(old_state)), "to": state_val,
                   "reason": reason},
        )

    def arm_heartbeat_token(self, token: str) -> None:
        """Register the shared bridge<->watchdog heartbeat token (H-2).

        The 800 ms liveness interlock must not be driven by an anonymous
        stream: every :meth:`heartbeat` must present the token registered
        here, compared with :func:`hmac.compare_digest` (constant time).
        Re-arming with a different value rotates the token; re-arming with
        the same value is a no-op.
        """
        cleaned = str(token)
        if not cleaned:
            raise ValueError("heartbeat caller token must be non-empty")
        with self._lock:
            self._heartbeat_token = cleaned

    def _token_is_valid_locked(self, caller_token: str | None) -> bool:
        """H-2 token check; caller MUST hold ``self._lock``.

        Constant-time comparison against the token registered by
        :meth:`arm_heartbeat_token`. An empty registration refuses EVERY
        caller (fail-closed): with no token armed there is no identity that
        may hold the lease open.
        """
        import hmac as _hmac

        expected = self._heartbeat_token
        presented = caller_token if isinstance(caller_token, str) else ""
        return bool(expected) and _hmac.compare_digest(presented, expected)

    def heartbeat(self, caller_token: str | None = None) -> None:
        """Refresh transmission authorization lease (H-2).

        ``caller_token`` is REQUIRED: an anonymous or mismatched pulse is a
        hard :class:`PermissionError` and does NOT extend the lease. The
        watchdog no longer fails open on an identity-less stream.
        """
        with self._lock:
            if not self._token_is_valid_locked(caller_token):
                logger.error("watchdog heartbeat refused: missing/forged caller token")
                raise PermissionError("watchdog heartbeat requires the shared caller token")
            self._last_heartbeat_time = self._clock.now_monotonic()
            self._last_heartbeat_wall_ns = self._clock.now_wall_ns()

    def anchor_lease_for_arm(self, caller_token: str | None = None) -> bool:
        """S-02/S-08 correctness: anchor the lease at the ARM instant — token-gated.

        REGRESSION 1 root cause: the S-02 remediation deleted the token-free
        re-anchor that ``_on_safety_state_changed`` performed on ARMED_TX/
        ACTIVE. That deletion was correct (the token-free refresh WAS the
        fail-open hole), but nothing replaced it: ``start()`` anchors the
        lease only on the FIRST start, and a non-UI arm path (any flow that
        never entered ``run()``) could therefore hold ``ARMED_TX`` with the
        monitor never started — S-08's ``_is_running`` requirement then made
        ``is_lease_valid`` False and the very first legitimate frame was
        refused at ``gateway.py`` Stage 2 with ``WATCHDOG_LEASE_EXPIRED``.

        This restores the *capability* without restoring the hole, and does so
        with the SMALLEST possible intervention:

        * The re-anchor runs THROUGH the H-2 token gate — the exact same
          constant-time check :meth:`heartbeat` uses, and therefore the same
          identity requirement. There is no code path into this method that
          skips the token, so an anonymous/identity-less stream still cannot
          hold the 800 ms interlock (the S-02 invariant).
        * It is a NO-OP when the lease is already valid. Re-anchoring an
          already-live lease would (a) silently extend authorization without a
          heartbeat (S-02) and (b) perturb the timestamp an
          ARMED_TX-transition observer is entitled to see unchanged.
        * It NEVER starts supervision. Starting the monitor is an application
          lifecycle decision owned by the composition root (``run()`` /
          ``_ensure_armed``); doing it implicitly here leaked a monitor thread
          on every arm and made lease validity depend on an unrelated side
          effect. When the monitor is down the lease is invalid (S-08) and this
          method reports that honestly instead of masking it.

        Returns ``True`` when the lease is valid after the call (including the
        already-valid no-op case), ``False`` when the caller presented no valid
        token OR supervision is not running (never raises; the lease is NOT
        extended).
        """
        with self._lock:
            if not self._token_is_valid_locked(caller_token):
                logger.error(
                    "watchdog lease re-anchor refused: missing/forged caller token (fail-closed)"
                )
                return False
            if not self._is_running:
                # S-08: a valid lease requires a live monitor. Do not fake it.
                logger.error(
                    "watchdog lease re-anchor refused: monitor not running (fail-closed; "
                    "the composition root must start supervision before arming TX)"
                )
                return False
            elapsed = max(0.0, self._clock.now_monotonic() - self._last_heartbeat_time)
            already_valid = elapsed <= self.timeout_sec
            if not already_valid:
                self._last_heartbeat_time = self._clock.now_monotonic()
                self._last_heartbeat_wall_ns = self._clock.now_wall_ns()
        return True

    @property
    def remaining_lease_sec(self) -> float:
        """Returns time in seconds until current lease expires."""
        with self._lock:
            elapsed = max(0.0, self._clock.now_monotonic() - self._last_heartbeat_time)
            return max(0.0, self.timeout_sec - elapsed)

    @property
    def is_lease_valid(self) -> bool:
        """True only while the monitor is RUNNING and the lease is fresh (S-08).

        The lease timestamp is anchored in `__init__` so a freshly built
        supervisor is "valid" for 800 ms even with the monitor thread never
        started — TX authority appeared alive while nothing enforced it. The
        monitor's running flag is therefore part of the predicate.
        """
        with self._lock:
            elapsed = max(0.0, self._clock.now_monotonic() - self._last_heartbeat_time)
            return self._is_running and elapsed <= self.timeout_sec

    def start(self) -> None:
        """Start the watchdog monitor background thread.

        Lease is anchored only on the FIRST start; restarts never refresh it
        (a stop()/start() cycle must not silently extend authorization).
        """
        with self._lock:
            if self._is_running:
                return
            # P0-4: a previous monitor thread that has not fully exited yet
            # (stop() raced its final iteration) must not be superseded by a
            # second live monitor — two threads evaluating lease expiry can
            # both reach trigger_fault/estop.trigger.
            if self._thread is not None and self._thread.is_alive():
                logger.error(
                    "TX Watchdog monitor thread still alive; refusing to start a second monitor"
                )
                return
            self._stop_event.clear()
            self._is_running = True
            if not self._started_once:
                # MIXED-CLOCK HAZARD (measured 2026-09-27, T84).
                #
                # These two anchors come from DIFFERENT clocks whenever the
                # injected provider virtualises monotonic time but not wall
                # time — which is exactly what `VirtualClock` does (it
                # virtualises monotonic by design; wall time stays real so
                # licence/file comparisons keep working). The monitor thread
                # then compares them in the suspend-detection branch:
                #
                #   elapsed      = now_monotonic() - _last_heartbeat_time    # frozen
                #   elapsed_wall = now_wall_ns()   - _last_heartbeat_wall_ns # grows
                #   if elapsed_wall > timeout and (elapsed_wall - elapsed) > timeout/2:
                #       suspend_detected = True
                #
                # With the virtual clock standing still, `elapsed_wall` alone
                # crosses the threshold after ~150 ms of REAL time and the
                # thread takes the suspend path unprompted. Measured on a
                # harness with timeout_ms=60: fault=True estop=True after
                # 0.15 s of real time with the virtual clock still at 0.000 s.
                #
                # The behaviour is CORRECT for the case it was written for (a
                # real OS suspend makes wall time leap while monotonic lags)
                # and it fails CLOSED, so it is deliberately NOT changed here.
                # It is recorded because it makes any test that injects a
                # VirtualClock and leaves the monitor thread running
                # machine-speed dependent: on a fast host the thread may not
                # get a slice before the test drives `poll_once()`; on a loaded
                # CI runner it does. A test in that position must stop the
                # thread before advancing the clock — see the note in
                # tests/e2e/test_safety_wiring.py.
                self._last_heartbeat_time = self._clock.now_monotonic()
                self._last_heartbeat_wall_ns = self._clock.now_wall_ns()
                self._started_once = True
            self._thread = threading.Thread(
                target=self._monitor_loop,
                name="tx_watchdog_supervisor",
                daemon=True,
            )
            self._thread.start()
            # S-17: a daemon monitor is killed outright at interpreter exit —
            # no further lease enforcement, but TX authority could still be
            # held by a driver left in active mode. Register a one-shot,
            # fail-closed teardown that engages the E-Stop at process exit.
            self._register_teardown_hook()
            logger.info(
                "TX Watchdog Supervisor started",
                extra={"timeout_ms": self.timeout_sec * 1000.0},
            )

    def _register_teardown_hook(self) -> None:
        """S-17: fail-closed E-Stop on process teardown (idempotent)."""
        if self._teardown_registered:
            return
        atexit.register(self._on_process_teardown)
        self._teardown_registered = True

    def _on_process_teardown(self) -> None:
        """S-17: revoke TX authority when the process tears down.

        The monitor thread is a daemon: `atexit` runs before daemon threads
        are killed, so this hook is the last guaranteed supervision step. It
        engages the E-Stop with ``PROCESS_TERMINATION`` whenever TX may still
        be authorized (running monitor OR a still-permitted supervisor) and
        never raises — a teardown hook that throws would mask the exit path.
        """
        try:
            with self._lock:
                was_running = self._is_running
            tx_permitted = bool(getattr(self.supervisor, "is_tx_permitted", False))
            if not (was_running or tx_permitted):
                return
            if self.estop is not None:
                self.estop.trigger(EStopTriggerSource.PROCESS_TERMINATION, _PROCESS_TEARDOWN_REASON)
            if was_running:
                try:
                    self.supervisor.trigger_fault(_PROCESS_TEARDOWN_REASON)
                except Exception as exc:  # noqa: BLE001
                    logger.critical(
                        "PROCESS_TERMINATION: supervisor fault trigger failed during teardown",
                        extra={"error": str(exc)},
                    )
        except Exception as exc:  # noqa: BLE001 - teardown must never raise
            logger.critical(
                "PROCESS_TERMINATION: watchdog teardown hook failed",
                extra={"error": str(exc)},
            )

    def stop(self) -> None:
        """Stop the watchdog monitor.

        REVIEW2 #1: if the monitor survives the join window (wedged inside
        a trigger callback), the old code only logged an ERROR and left the
        supervisor without its 800 ms enforcement layer — permanently, since
        a later start() refuses while the zombie thread lives. The wedge is
        now escalated independently: trigger a supervisor fault + E-Stop so
        TX authority is revoked through a second path even though the
        watchdog's own monitor is stuck (fail-safe: never leave TX armed
        with dead supervision).
        """
        with self._lock:
            self._is_running = False
        # Signal OUTSIDE the lock: the monitor may be waiting on the event
        # (wake immediately) or holding the lock inside _monitor_loop_once.
        self._stop_event.set()
        wedged = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                # The loop can no longer spin forever (it breaks on
                # _is_running/_stop_event), so surviving past the join means
                # the thread is wedged inside a trigger callback — escalate.
                wedged = True
                logger.error("TX Watchdog monitor thread failed to exit within 1.0s timeout")
        if wedged:
            # Fail-safe escalation through paths independent of the wedged
            # monitor thread: revoke TX authority now, not on the next
            # (never-arriving) monitor iteration.
            try:
                self.supervisor.trigger_fault(
                    "WATCHDOG_MONITOR_WEDGED: stop() join timed out — monitor thread stuck; revoking TX authorization",
                )
            except Exception as exc:  # noqa: BLE001
                logger.critical(
                    "WATCHDOG_MONITOR_WEDGED: supervisor fault trigger also failed",
                    extra={"error": str(exc)},
                )
            if self.estop:
                try:
                    self.estop.trigger(
                        EStopTriggerSource.KEEPALIVE_TIMEOUT,
                        "Watchdog monitor thread wedged during stop — TX authorization revoked fail-safe",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.critical(
                        "WATCHDOG_MONITOR_WEDGED: estop trigger also failed",
                        extra={"error": str(exc)},
                    )
        logger.info("TX Watchdog Supervisor stopped")

    def poll_once(self) -> None:
        """Run exactly one supervision check, independent of the monitor thread.

        Deterministic test drive (docs/ai_context/05 §4): tests inject a
        VirtualClock, advance it past the lease, and call poll_once() to
        observe the FAULT/E-Stop cascade without sleeping or racing the
        background thread.
        """
        self._monitor_loop_once(force=True)

    def _monitor_loop_once(self, force: bool = False) -> None:
        """Single supervision check, isolated so tests can drive it deterministically.

        M-15 (P2-2): the trigger callbacks (supervisor.trigger_fault /
        estop.trigger) run OUTSIDE self._lock. The watchdog lock is a plain
        (non-reentrant) threading.Lock; any callback that re-enters the
        watchdog (heartbeat from within the fault path) would have
        deadlocked the whole monitor thread while it held the lock.
        """
        with self._lock:
            if not self._is_running and not force:
                return
            now = self._clock.now_monotonic()
            now_wall_ns = self._clock.now_wall_ns()
            elapsed = max(0.0, now - self._last_heartbeat_time)

            # F2 (Aksiyon 19): Suspend protection check
            # If wall time leaped ahead past lease window while monotonic time lagged (suspend signature)
            suspend_detected = False
            if self._last_heartbeat_wall_ns > 0:
                elapsed_wall = max(0.0, (now_wall_ns - self._last_heartbeat_wall_ns) / 1_000_000_000.0)
                if (
                    self.supervisor.is_tx_permitted
                    and elapsed_wall > self.timeout_sec
                    and (elapsed_wall - elapsed) > (self.timeout_sec / 2.0)
                ):
                    suspend_detected = True

            # Only enforce watchdog if transmission is armed or active
            if not (self.supervisor.is_tx_permitted and (elapsed > self.timeout_sec or suspend_detected)):
                return

            elapsed_ms = (elapsed if not suspend_detected else elapsed_wall) * 1000.0
            timeout_ms = self.timeout_sec * 1000.0

            # Re-check lease freshness under the lock immediately before fault trigger
            # to prevent false-positive cutoff if heartbeat arrived mid-flight.
            fresh_elapsed = max(0.0, self._clock.now_monotonic() - self._last_heartbeat_time)
            if not suspend_detected and fresh_elapsed <= self.timeout_sec:
                return

            expired = True  # decision made under the lock; triggers fire below

        if expired:
            reason = (
                f"SUSPEND_DETECTED: OS sleep/suspend detected ({elapsed_ms:.1f}ms wall gap) during TX lease"
                if suspend_detected
                else f"WATCHDOG_TIMEOUT: Lease expired after {elapsed_ms:.1f} ms without heartbeat"
            )
            logger.critical(
                "TX Watchdog Lease Expired! Revoking all TX authorization.",
                extra={"elapsed_ms": elapsed_ms, "timeout_ms": timeout_ms, "suspend_detected": suspend_detected},
            )
            # Revoke TX in state machine with primary root cause — outside
            # the watchdog lock (callbacks may re-enter the watchdog).
            try:
                self.supervisor.trigger_fault(reason)
            except Exception as sup_exc:  # noqa: BLE001
                logger.critical("Failed to trigger supervisor fault during watchdog timeout", extra={"error": str(sup_exc)})

            # Engage hardware/software E-Stop (fail-safe cutoff) — outside
            # the watchdog lock for the same reason.
            if self.estop:
                try:
                    self.estop.trigger(
                        EStopTriggerSource.KEEPALIVE_TIMEOUT,
                        f"Watchdog lease expired ({elapsed_ms:.1f}ms > {timeout_ms:.1f}ms)",
                    )
                except Exception as estop_exc:  # noqa: BLE001
                    logger.critical("Failed to engage E-Stop during watchdog timeout", extra={"error": str(estop_exc)})

    def _monitor_loop(self) -> None:
        """Continuous background check enforcing lease bounds.

        S-H-001: the loop body is exception-isolated — a crashing callback or
        trigger must never silently kill the monitor thread and leave TX
        authorization open forever. If the loop itself dies despite the
        guards, the finally-block revokes TX authority as a last resort.

        P0-4 (REVIEW H-4): the loop condition is `while self._is_running` and
        the park is an interruptible Event.wait — stop() now actually
        terminates the thread instead of timing out against an immortal
        `while True:` loop, and a stop()/start() cycle can never produce two
        concurrent monitors.
        """
        try:
            while self._is_running:
                try:
                    self._monitor_loop_once()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "TX Watchdog monitor iteration failed; continuing supervision",
                        extra={"error": str(exc)},
                    )

                if self._stop_event.wait(self.CHECK_INTERVAL_SEC):
                    break  # stop() signaled — exit promptly
        except BaseException as exc:  # loop machinery itself failed
            logger.critical(
                "TX Watchdog monitor loop terminated abnormally — revoking TX authorization",
                extra={"error": str(exc)},
            )
        finally:
            # Last-resort safety: never leave TX authority open when the
            # supervisor thread is gone — EXCEPT during an orderly stop()
            # where the caller intentionally ended supervision (the legacy
            # code fired WATCHDOG_MONITOR_DIED on every shutdown, which made
            # the fault path the normal teardown path).
            if self._is_running:
                self._is_running = False
                try:
                    self.supervisor.trigger_fault("WATCHDOG_MONITOR_DIED: monitor thread exited unexpectedly")
                except Exception:  # noqa: BLE001
                    logger.critical("WATCHDOG_MONITOR_DIED: supervisor fault trigger also failed")
                if self.estop:
                    try:
                        self.estop.trigger(
                            EStopTriggerSource.KEEPALIVE_TIMEOUT,
                            "WATCHDOG_MONITOR_DIED: monitor thread exited unexpectedly",
                        )
                    except Exception:  # noqa: BLE001
                        logger.critical("WATCHDOG_MONITOR_DIED: estop trigger also failed")

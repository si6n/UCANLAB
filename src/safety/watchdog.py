"""Independent TX Watchdog and Heartbeat Lease Supervisor.

Complies with Saha Risk Kataloğu v1.2 Sections 20, 36.5, 38.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, ClassVar

from src.core.contracts.ports import SystemClockProvider
from src.core.logging import get_logger
from src.safety.estop import EStopTriggerSource

if TYPE_CHECKING:
    from src.core.contracts.ports import ClockProvider
    from src.safety.estop import EmergencyStopSystem
    from src.safety.state_machine import SafetySupervisor

logger = get_logger("safety.watchdog")


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
        self.clock = clock if clock is not None else SystemClockProvider()
        self.supervisor = supervisor
        self.estop = estop
        self.timeout_sec = max(0.050, timeout_ms / 1000.0)

        self._last_heartbeat_time = self.clock.now_monotonic()
        self._is_running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # P0-4 (REVIEW H-4): wakeable stop signal. The old monitor loop
        # parked in time.sleep(0.050) with a literal `while True:` condition
        # that never consulted _is_running — stop() could only wait 1 s for
        # a thread that NEVER exits, and a stop()/start() cycle leaked a
        # second immortal monitor. This Event both breaks the loop promptly
        # and makes the sleep interruptible.
        self._stop_event = threading.Event()

        if self.supervisor:
            self.supervisor.register_callback(self._on_safety_state_changed)

    def _on_safety_state_changed(self, old_state: object, new_state: object, reason: str) -> None:
        """Re-anchor lease timestamp upon transitioning into an active or armed transmission state."""
        state_val = getattr(new_state, "value", str(new_state))
        if state_val in {"ARMED_TX", "ACTIVE"}:
            with self._lock:
                self._last_heartbeat_time = self.clock.now_monotonic()

    def heartbeat(self) -> None:
        """Refresh transmission authorization lease."""
        with self._lock:
            self._last_heartbeat_time = self.clock.now_monotonic()

    @property
    def remaining_lease_sec(self) -> float:
        """Returns time in seconds until current lease expires."""
        with self._lock:
            elapsed = self.clock.now_monotonic() - self._last_heartbeat_time
            return max(0.0, self.timeout_sec - elapsed)

    @property
    def is_lease_valid(self) -> bool:
        with self._lock:
            return (self.clock.now_monotonic() - self._last_heartbeat_time) <= self.timeout_sec

    def start(self) -> None:
        """Start the watchdog monitor background thread."""
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
            self._last_heartbeat_time = self.clock.now_monotonic()
            self._thread = threading.Thread(
                target=self._monitor_loop,
                name="tx_watchdog_supervisor",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "TX Watchdog Supervisor started",
                extra={"timeout_ms": self.timeout_sec * 1000.0},
            )

    def stop(self) -> None:
        """Stop the watchdog monitor."""
        with self._lock:
            self._is_running = False
        # Signal OUTSIDE the lock: the monitor may be waiting on the event
        # (wake immediately) or holding the lock inside _monitor_loop_once.
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                # The loop can no longer spin forever (it breaks on
                # _is_running/_stop_event), so surviving past the join means
                # the thread is wedged inside a trigger callback — escalate.
                logger.error("TX Watchdog monitor thread failed to exit within 1.0s timeout")
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
            now = self.clock.now_monotonic()
            elapsed = now - self._last_heartbeat_time

            # Only enforce watchdog if transmission is armed or active
            if not (self.supervisor.is_tx_permitted and elapsed > self.timeout_sec):
                return

            elapsed_ms = elapsed * 1000.0
            timeout_ms = self.timeout_sec * 1000.0

            # Re-check lease freshness under the lock immediately before fault trigger
            # to prevent false-positive cutoff if heartbeat arrived mid-flight.
            if (self.clock.now_monotonic() - self._last_heartbeat_time) <= self.timeout_sec:
                return

            expired = True  # decision made under the lock; triggers fire below

        if expired:
            logger.critical(
                "TX Watchdog Lease Expired! Revoking all TX authorization.",
                extra={"elapsed_ms": elapsed_ms, "timeout_ms": timeout_ms},
            )
            # Revoke TX in state machine with primary root cause — outside
            # the watchdog lock (callbacks may re-enter the watchdog).
            try:
                self.supervisor.trigger_fault(
                    f"WATCHDOG_TIMEOUT: Lease expired after {elapsed_ms:.1f} ms without heartbeat",
                )
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

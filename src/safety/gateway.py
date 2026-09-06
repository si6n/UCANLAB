"""Dual-Confirmation TX Safety Gateway enforcing CORE_SAFETY_FLOOR and Speed Interlocks.

Matches MASTER_PLAN.md Section 7, ISO 26262 ASIL-B/D, and Saha Risk Kataloğu v1.2 Sections 4, 19, 20.
Enforces strict 6-stage policy evaluation order:
1. Frame Sanity & Range Validation
2. Safety State & E-Stop Status (supervisor, watchdog lease, E-Stop)
3. Whitelist Authorization (Fail-Closed)
4. Speed Interlock (Stationary & Freshness)
5. Dual Confirmation Check
6. Rate Budget (Sliding Window in monotonic nanoseconds)
followed by the optional E2E stamping stage (docs/ai_context/02 §1 stage 6:
rolling counter + CRC sealing via E2ESafetyPackager when a profile is
configured for the frame's arbitration id) before fenced dispatch.
"""

from __future__ import annotations

import collections
import concurrent.futures
import math
import threading
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, ClassVar

from src.core.errors import SafetyError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.safety.e2e.packager import E2ESafetyPackager
from src.safety.e2e.profiles import E2EProfileConfig
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.exceptions import (
    DualConfirmationRequiredError,
    FrameSanityError,
    RateLimitExceededError,
    SpeedDataStaleError,
    SpeedInterlockError,
    WhitelistFailClosedError,
    WhitelistViolationError,
)

if TYPE_CHECKING:
    from src.hal.base import AbstractBus
    from src.safety.state_machine import SafetySupervisor
    from src.safety.watchdog import TxWatchdogSupervisor

logger = get_logger("safety.gateway")


class TxBudget:
    """Monotonic token bucket: `capacity` burst tokens refilled at `refill_per_sec`."""

    __slots__ = ("capacity", "refill_per_sec", "_tokens", "_last_refill_ns", "_lock")

    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._tokens = float(capacity)
        self._last_refill_ns = time.monotonic_ns()
        self._lock = threading.Lock()

    def try_consume(self, n: int = 1) -> bool:
        """Consume `n` tokens if available; returns False when the bucket is dry."""
        with self._lock:
            now_ns = time.monotonic_ns()
            elapsed_s = (now_ns - self._last_refill_ns) / 1e9
            if elapsed_s > 0:
                self._tokens = min(float(self.capacity), self._tokens + elapsed_s * self.refill_per_sec)
                self._last_refill_ns = now_ns
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def refund(self, n: int = 1) -> None:
        """Refund `n` previously consumed tokens back to the bucket (capped at capacity)."""
        with self._lock:
            self._tokens = min(float(self.capacity), self._tokens + n)


class TxSafetyGateway:
    """Security and Functional Safety Gateway filtering all outgoing CAN transmissions."""

    MAX_TX_RATE_PER_SEC: ClassVar[int] = 100  # Max 100 msg/s to prevent bus starvation
    SPEED_NOISE_THRESHOLD_KMH: ClassVar[float] = 0.5  # Permitted sensor jitter / noise threshold
    SPEED_VALIDITY_TIMEOUT_NS: ClassVar[int] = 1_000_000_000  # 1.0 second speed freshness timeout
    RATE_LIMIT_WINDOW_NS: ClassVar[int] = 1_000_000_000  # 1.0 second sliding window (nanoseconds)
    # P1-9: consecutive default-lane rejections tolerated before escalating
    # to an E-Stop. A single burst = backpressure; a sustained pattern =
    # runaway sender and must fail hard.
    RATE_ESTOP_AFTER: ClassVar[int] = 5

    # Per-category token buckets (F-18): protocol bursts such as a J1939 BAM
    # transfer (<=255 packets) must fit inside a single burst budget.
    BUDGETS: ClassVar[dict[str, tuple[int, float]]] = {
        "diagnostic": (10, 10.0),
        "calibration": (5, 5.0),
        "protocol_burst": (255, 100.0),
        "default": (100, 100.0),
    }

    # MEDIUM-6: bounded, gateway-owned executor for the async TxPort entry.
    # Never offload onto the event loop's shared default executor (unbounded
    # and shared with every other offload in the process).
    TX_EXECUTOR_MAX_WORKERS: ClassVar[int] = 4
    TX_EXECUTOR_THREAD_PREFIX: ClassVar[str] = "tx-gateway-"

    def __init__(
        self,
        bus: AbstractBus,
        estop: EmergencyStopSystem | None = None,
        supervisor: SafetySupervisor | None = None,
        watchdog: TxWatchdogSupervisor | None = None,
        whitelist_ids: set[int] | None = None,
        whitelist_masks: Sequence[tuple[int, int]] | None = None,
        e2e_packager: E2ESafetyPackager | None = None,
        e2e_profiles: Mapping[int, E2EProfileConfig] | None = None,
    ) -> None:
        self.bus = bus
        if estop is not None:
            self.estop = estop
        elif watchdog is not None and watchdog.estop is not None:
            self.estop = watchdog.estop
        else:
            self.estop = EmergencyStopSystem()
        self.supervisor = supervisor
        self.watchdog = watchdog
        self.whitelist_ids: set[int] = set(whitelist_ids) if whitelist_ids is not None else set()
        # (value, mask) pairs: an ID passes when (id & mask) == value. Used to
        # authorize whole protocol-response families (e.g. TP.CM frames sourced
        # from our J1939 address) without enumerating every peer address.
        self.whitelist_masks: tuple[tuple[int, int], ...] = (
            tuple(whitelist_masks) if whitelist_masks is not None else ()
        )
        # E2E stamping stage (docs/ai_context/02 §1 stage 6 / transport_e2e
        # spec): when configured, frames whose arbitration_id maps to an
        # E2E profile are sealed (rolling counter + CRC) right before rate
        # accounting, so the on-wire payload is protected end-to-end.
        # Unconfigured IDs pass through unstamped — E2E protection is opt-in
        # per protected stream, matching the profiles' per-stream counters.
        self.e2e_packager = e2e_packager
        self.e2e_profiles: Mapping[int, E2EProfileConfig] = dict(e2e_profiles) if e2e_profiles else {}
        # Fail-closed whitelist stage can only be bypassed through the
        # explicit for_testing() factory — never via a constructor flag
        # that production wiring could set by accident.
        self._whitelist_bypass_for_testing: bool = False

        self._tx_timestamps: "collections.deque[tuple[int, int, int]]" = collections.deque()
        # P1-9: consecutive-rejection counter for sustained-overload detection
        self._rate_overload_streak: int = 0
        # HIGH-1: monotonically increasing per-call sequence number. Combined
        # with the thread id it makes every stamp uniquely identifiable, so a
        # rollback removes EXACTLY the caller's own reservation — never the
        # newest stamp of an unrelated concurrent sender (old blind pop()).
        self._stamp_seq: int = 0
        self._budgets: dict[str, TxBudget] = {
            name: TxBudget(capacity, refill) for name, (capacity, refill) in self.BUDGETS.items()
        }
        self._current_vehicle_speed_kmh: float = 0.0
        self._last_speed_update_ns: int = 0
        self._lock = threading.RLock()

        # MEDIUM-6: gateway-owned bounded executor for async sends (F-26/E-12).
        # Eagerly constructed, single instance, reused across sends — never
        # the event loop's shared default executor (unbounded and shared with
        # every other offload in the process). Threads spawn lazily inside the
        # pool, so an idle gateway costs nothing.
        self._tx_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.TX_EXECUTOR_MAX_WORKERS,
            thread_name_prefix=self.TX_EXECUTOR_THREAD_PREFIX,
        )
        self._tx_executor_shutdown = False

        # Wire E-stop callback to halt bus TX and trigger fault state
        self.estop.register_callback(self._on_estop_triggered)

        if self.supervisor:
            self.supervisor.register_callback(self._on_safety_state_changed)

    @classmethod
    def for_testing(
        cls,
        bus: AbstractBus,
        estop: EmergencyStopSystem | None = None,
        whitelist_ids: set[int] | None = None,
    ) -> TxSafetyGateway:
        """Test/demo-only factory that bypasses the fail-closed whitelist stage.

        Kept out of the production constructor signature on purpose: the
        bypass is only reachable through this explicitly named factory.
        All other policy stages (E-Stop, speed interlock, dual confirmation,
        rate budget) remain fully enforced.
        """
        instance = cls(bus=bus, estop=estop, whitelist_ids=whitelist_ids)
        object.__setattr__(instance, "_whitelist_bypass_for_testing", True)
        return instance

    def _on_estop_triggered(self, event: object) -> None:
        logger.warning("TX Gateway notified of E-Stop engagement. All TX halted.")
        if self.supervisor and not self.supervisor.is_fault:
            self.supervisor.trigger_fault("E-Stop engagement triggered from hardware/software event")
        with self._lock:
            self._tx_timestamps.clear()

    def _on_safety_state_changed(self, old_state: object, new_state: object, reason: str) -> None:
        if getattr(new_state, "value", str(new_state)) == "FAULT":
            with self._lock:
                self._tx_timestamps.clear()

    def update_vehicle_speed(self, speed_kmh: float) -> None:
        """Update live vehicle speed for dynamic interlock enforcement.

        Always timestamps with time.monotonic_ns() on reception to prevent
        clock domain skew. NaN, negative or non-finite values are treated as
        corrupted telemetry and invalidate freshness to 0 (fail-closed).
        """
        with self._lock:
            if not math.isfinite(speed_kmh) or speed_kmh < 0.0:
                self._current_vehicle_speed_kmh = float("nan")
                self._last_speed_update_ns = 0
                return
            self._current_vehicle_speed_kmh = float(speed_kmh)
            self._last_speed_update_ns = time.monotonic_ns()

    def is_speed_fresh_and_safe(self, max_age_ns: int | None = None) -> bool:
        """Public inquiry API for preflight checks: True when speed is fresh and below threshold."""
        timeout = max_age_ns if max_age_ns is not None else self.SPEED_VALIDITY_TIMEOUT_NS
        with self._lock:
            if self._last_speed_update_ns == 0:
                return False
            now_ns = time.monotonic_ns()
            if (now_ns - self._last_speed_update_ns) > timeout:
                return False
            if math.isnan(self._current_vehicle_speed_kmh):
                return False
            return self._current_vehicle_speed_kmh <= self.SPEED_NOISE_THRESHOLD_KMH

    def validate_and_transmit(
        self,
        frame: CanFrame,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
    ) -> bool:
        """Enforce strict 6-stage policy evaluation order before transmitting onto HAL.

        Lock structure: validation + token consumption under lock → snapshot estop
        state → release lock → final estop guard with rollback → transmit outside lock.
        This ensures watchdog/estop callbacks never block on driver I/O.
        """
        # -----------------------------------------------------------------
        # PHASE 1: VALIDATION + STATE MUTATION (under gateway lock)
        # -----------------------------------------------------------------
        timestamp_consumed = False
        budget_consumed = False
        stamp: tuple[int, int, int] | None = None
        budget: TxBudget | None = None

        with self._lock:
            now_ns = time.monotonic_ns()

            # -----------------------------------------------------------------
            # Stage 1: Frame Sanity & Range Validation
            # -----------------------------------------------------------------
            if not isinstance(frame, CanFrame):
                raise FrameSanityError("Transmission rejected: Invalid frame object")

            max_id = 0x1FFFFFFF if frame.is_extended else 0x7FF
            if not (0 <= frame.arbitration_id <= max_id):
                raise FrameSanityError(
                    f"Frame sanity violation: ID 0x{frame.arbitration_id:X} out of range (max 0x{max_id:X})",
                    details={"arbitration_id": frame.arbitration_id, "max_id": max_id},
                )

            if not frame.is_fd and len(frame.data) > 8:
                raise FrameSanityError(
                    f"Frame sanity violation: Classic CAN payload > 8 bytes (len={len(frame.data)})",
                    details={"length": len(frame.data)},
                )

            if frame.is_fd and len(frame.data) > 64:
                raise FrameSanityError(
                    f"Frame sanity violation: CAN-FD payload > 64 bytes (len={len(frame.data)})",
                    details={"length": len(frame.data)},
                )

            # -----------------------------------------------------------------
            # Stage 2: Safety State & E-Stop Status
            # -----------------------------------------------------------------
            if self.supervisor is not None and not self.supervisor.is_tx_permitted:
                raise SafetyError(
                    f"Transmission blocked: Safety State is '{self.supervisor.current_state.value}' (TX not permitted)",
                    code="SAFETY_STATE_BLOCKED",
                )

            if self.watchdog is not None and not self.watchdog.is_lease_valid:
                raise SafetyError(
                    "Transmission blocked: Watchdog lease has expired",
                    code="WATCHDOG_LEASE_EXPIRED",
                )

            if self.estop.is_engaged:
                raise SafetyError(
                    "Transmission blocked: Emergency Stop is currently ENGAGED",
                    code="ESTOP_ACTIVE",
                )

            # -----------------------------------------------------------------
            # Stage 3: Whitelist Authorization (Fail-Closed)
            # -----------------------------------------------------------------
            if not self._whitelist_bypass_for_testing:
                if not self.whitelist_ids and not self.whitelist_masks:
                    raise WhitelistFailClosedError(
                        "Transmission blocked: Dynamic whitelist is empty or unconfigured (Fail-Closed)",
                    )
                id_allowed = frame.arbitration_id in self.whitelist_ids or any(
                    (frame.arbitration_id & mask) == value for value, mask in self.whitelist_masks
                )
                if not id_allowed:
                    logger.warning(
                        "TX Frame rejected by Whitelist filter",
                        extra={"arbitration_id": hex(frame.arbitration_id)},
                    )
                    self.estop.trigger(
                        EStopTriggerSource.UNAUTHORIZED_PAYLOAD,
                        f"Attempted TX to non-whitelisted ID: 0x{frame.arbitration_id:08X}",
                    )
                    raise WhitelistViolationError(
                        f"Transmission blocked: ID 0x{frame.arbitration_id:08X} not in whitelist",
                        details={"arbitration_id": frame.arbitration_id},
                    )

            # -----------------------------------------------------------------
            # Stage 4: Speed Interlock (Evaluated STRICTLY BEFORE Dual Confirmation)
            # -----------------------------------------------------------------
            if is_critical_command:
                # Speed telemetry freshness check
                if self._last_speed_update_ns == 0 or (now_ns - self._last_speed_update_ns) > self.SPEED_VALIDITY_TIMEOUT_NS:
                    self.estop.trigger(
                        EStopTriggerSource.SPEED_INTERLOCK_BREACH,
                        "Critical command attempted with stale or missing vehicle speed telemetry",
                        vehicle_speed_kmh=self._current_vehicle_speed_kmh,
                    )
                    raise SpeedDataStaleError(
                        "Safety Interlock: Critical command blocked due to stale vehicle speed telemetry",
                    )

                # Speed threshold check
                if self._current_vehicle_speed_kmh > self.SPEED_NOISE_THRESHOLD_KMH:
                    logger.critical(
                        "Speed interlock triggered on critical command",
                        extra={"speed": self._current_vehicle_speed_kmh},
                    )
                    self.estop.trigger(
                        EStopTriggerSource.SPEED_INTERLOCK_BREACH,
                        f"Critical command attempted while moving ({self._current_vehicle_speed_kmh} km/h)",
                        vehicle_speed_kmh=self._current_vehicle_speed_kmh,
                    )
                    raise SpeedInterlockError(
                        f"Safety Interlock: Critical command blocked while vehicle is moving ({self._current_vehicle_speed_kmh} km/h)",
                    )

            # -----------------------------------------------------------------
            # Stage 5: Dual Confirmation Check
            # -----------------------------------------------------------------
            if is_critical_command and not user_confirmed:
                raise DualConfirmationRequiredError(
                    "Critical command rejected: Operator dual-confirmation missing",
                )

            # -----------------------------------------------------------------
            # Stage 6: Rate Budget Enforcement
            # The sliding window (Stage 6a) throttles the general traffic lane.
            # Categorised bursts are governed by their own token bucket (Stage
            # 6b) — e.g. a J1939 BAM transfer legitimately sends up to 255
            # packets well above MAX_TX_RATE_PER_SEC, so it is exempt from the
            # default-lane window and bounded by its bucket instead.
            # S-C-007 fix: the default lane is metered EXACTLY ONCE — by the
            # sliding window — and never also through the default token
            # bucket. The 'default' bucket exists only as the fallback for
            # lanes that do not use the window.
            # -----------------------------------------------------------------
            budget = self._budgets.get(budget_category)
            if budget is None:
                raise FrameSanityError(
                    f"Unknown TX budget category '{budget_category}'",
                    details={"category": budget_category},
                )

            if budget_category == "default":
                # Default lane: sliding window only (single meter)
                while self._tx_timestamps:
                    first_ts_ns = self._tx_timestamps[0][0]
                    if (now_ns - first_ts_ns) >= self.RATE_LIMIT_WINDOW_NS:
                        self._tx_timestamps.popleft()
                    else:
                        break

                if len(self._tx_timestamps) >= self.MAX_TX_RATE_PER_SEC:
                    # P1-9: a legitimate protocol burst (e.g. ISO-TP CF train
                    # on the uncategorised TxPort lane) used to slam the
                    # system into a PERMANENT E-Stop — self-DoS. Now the
                    # first overloads are plain rejects (backpressure), and
                    # only a SUSTAINED violation pattern (RATE_ESTOP_AFTER
                    # consecutive rejections within one window) escalates
                    # to an E-Stop as evidence of a runaway/blocked loop.
                    self._rate_overload_streak += 1
                    if self._rate_overload_streak >= self.RATE_ESTOP_AFTER:
                        logger.critical(
                            "TX rate limit sustained violation pattern — triggering E-Stop",
                            extra={"streak": self._rate_overload_streak},
                        )
                        self.estop.trigger(
                            EStopTriggerSource.RATE_LIMIT_OVERFLOW,
                            f"Sustained TX rate overload ({self._rate_overload_streak} consecutive rejections)",
                        )
                    else:
                        logger.warning(
                            "TX rate limit exceeded — frame rejected (backpressure)",
                            extra={"streak": self._rate_overload_streak},
                        )
                    raise RateLimitExceededError("Transmission rate limit exceeded (100 msg/s)")
                self._rate_overload_streak = 0

                # HIGH-1: identity-carrying stamp — (now_ns, thread_id, seq).
                # The seq counter guarantees uniqueness even when one thread
                # parks between append and rollback while another sender
                # with the same thread id (impossible) or a colliding now_ns
                # (possible under coarse clocks) interleaves. Rollback now
                # removes EXACTLY this tuple, never a blind pop().
                self._stamp_seq += 1
                stamp = (now_ns, threading.get_ident(), self._stamp_seq)
                self._tx_timestamps.append(stamp)
                timestamp_consumed = True
            else:
                # Categorised lane: token bucket only (single meter)
                if not budget.try_consume():
                    logger.error(
                        "TX budget exhausted",
                        extra={"category": budget_category},
                    )
                    raise RateLimitExceededError(
                        f"TX budget '{budget_category}' exhausted (capacity {budget.capacity})",
                    )
                budget_consumed = True

            # Snapshot estop state at the moment of lock release
            estop_snapshot = self.estop.is_engaged
            # CRITICAL-1: capture the TX fence generation the frame is being
            # validated against. PHASE 3 re-verifies it under the E-Stop send
            # lock — any trigger/reset transition since this snapshot kills
            # the frame before it can reach the wire.
            fence_snapshot = self.estop.tx_fence

            # -----------------------------------------------------------------
            # E2E STAMPING STAGE (docs/ai_context/02 §1 stage 6)
            # Applied only when a profile is configured for this arbitration
            # id: the frame is sealed (rolling counter + CRC-8) and the sealed
            # frame replaces the raw one for dispatch. Runs under the gateway
            # lock because the packager's per-stream counters are stateful —
            # sealing outside the lock could interleave senders on one stream.
            # -----------------------------------------------------------------
            if self.e2e_packager is not None and frame.arbitration_id in self.e2e_profiles:
                frame = self.e2e_packager.package(frame, self.e2e_profiles[frame.arbitration_id])

        # -----------------------------------------------------------------
        # PHASE 2: FINAL E-STOP GUARD (lock-free)
        # estop.is_engaged acquires estop's own RLock (leaf lock), which does
        # not enter the gateway lock ordering — no deadlock risk.
        # -----------------------------------------------------------------
        if estop_snapshot or self.estop.is_engaged:
            # E-Stop was engaged either during Stage 2 validation or in the
            # window between lock release and this check. Roll back consumed tokens.
            self._rollback_tx_reservation(timestamp_consumed, budget_consumed, stamp, budget)
            raise SafetyError(
                "Transmission blocked: Emergency Stop is currently ENGAGED",
                code="ESTOP_ACTIVE",
            )

        # -----------------------------------------------------------------
        # PHASE 3: TRANSMIT (outside lock, no rollback after this point)
        # D8: privileged dispatch through the explicit gateway port — no
        # more duck-typed reach into the driver's private _send_raw
        #
        # CRITICAL-1 (E-Stop fence): the dispatch is FENCED. The send lock
        # serializes SENDERS against each other and the fence check closes
        # the validate-then-send window against a trigger+reset pair (any
        # completed state transition bumps the generation). Honest limits
        # (P1-10): trigger() itself does NOT take this lock, so a single
        # engagement CAN land between the fence check and the bus write —
        # at most the frame already in the driver queue may still exit the
        # wire. The fence guarantees "no frame validated before a
        # COMPLETED transition survives"; driver-level flush/abort is the
        # HAL's responsibility, documented in estop callbacks.
        # -----------------------------------------------------------------
        with self.estop.tx_send_lock:
            if fence_snapshot != self.estop.tx_fence or self.estop.is_engaged:
                # State transitioned between validation and dispatch: the
                # reservation is already rolled back below; nothing reaches the wire.
                self._rollback_tx_reservation(timestamp_consumed, budget_consumed, stamp, budget)
                raise SafetyError(
                    "Transmission blocked: E-Stop TX fence invalidated "
                    "(state transition during dispatch)",
                    code="ESTOP_TX_FENCE_INVALIDATED",
                )
            self.bus.privileged_send(frame)
        return True

    def _rollback_tx_reservation(
        self,
        timestamp_consumed: bool,
        budget_consumed: bool,
        stamp: tuple[int, int, int] | None,
        budget: TxBudget | None,
    ) -> None:
        """Roll back exactly the caller's own consumed rate-limit reservation.

        HIGH-1: the sliding-window stamp is identity-carrying
        (now_ns, thread_id, seq). A rollback removes EXACTLY that tuple via
        remove(stamp) — never a blind pop() that could delete the newest
        stamp of an unrelated concurrent sender.
        """
        with self._lock:
            if timestamp_consumed and stamp is not None:
                try:
                    self._tx_timestamps.remove(stamp)
                except ValueError:
                    # The stamp was already removed (e.g. the window was
                    # cleared by an E-Stop callback). Nothing to roll back.
                    pass
            if budget_consumed and budget is not None:
                budget.refund()

    def send_sync(
        self,
        frame: CanFrame,
        budget_category: str = "default",
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
    ) -> None:
        """Synchronously transmit frame conforming to TxPort protocol.

        P1-9: the optional budget_category lets protocol engines use their
        dedicated lanes (e.g. 'protocol_burst' for ISO-TP CF trains) instead
        of colliding with the 100 msg/s default-lane wall. Defaults to the
        uncategorised 'default' lane for plain TxPort callers.
        """
        self.validate_and_transmit(
            frame,
            is_critical_command=is_critical_command,
            user_confirmed=user_confirmed,
            budget_category=budget_category,
        )

    async def send(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
    ) -> None:
        """Asynchronously transmit without blocking the running event loop (F-26/E-12).

        The synchronous validation pipeline may perform blocking work (driver TX,
        E-Stop state checks) — offloading it keeps ISO-TP CF bursts responsive.

        MEDIUM-6: the offload runs on the gateway's OWN bounded, managed
        ThreadPoolExecutor (thread_name_prefix 'tx-gateway-'), never on the
        event loop's shared default executor (which is unbounded and shared
        with every other offload in the process).
        """
        import asyncio
        import functools

        if self._tx_executor is None or self._tx_executor_shutdown:
            # Fail-closed: no managed pool -> no offload -> no transmission.
            raise SafetyError(
                "Transmission blocked: gateway TX executor is shut down",
                code="TX_EXECUTOR_SHUT_DOWN",
            )

        loop = asyncio.get_running_loop()
        fn = functools.partial(
            self.send_sync,
            frame,
            budget_category,
            is_critical_command=is_critical_command,
            user_confirmed=user_confirmed,
        )
        await loop.run_in_executor(self._tx_executor, fn)

    def shutdown(self) -> None:
        """Release the managed TX executor (MEDIUM-6). Idempotent.

        Sends attempted after shutdown fail closed with SafetyError. The
        synchronous path (send_sync / validate_and_transmit) remains fully
        functional — only the async offload lane is retired.
        """
        executor = self._tx_executor
        if executor is not None and not self._tx_executor_shutdown:
            self._tx_executor_shutdown = True
            executor.shutdown(wait=False)
            logger.info("TX gateway executor shut down")

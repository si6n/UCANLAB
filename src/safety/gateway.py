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
import hashlib
import hmac
import math
import os
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
    # Whitelist miss policy: first miss = reject+alarm, persistent pattern
    # at/above this streak latches the E-Stop (fail-closed on abuse).
    # Threshold mirrors RATE_ESTOP_AFTER-style escalation: isolated misses
    # (fuzz/misconfig) stay reject+alarm, sustained abuse latches.
    # P9 (G-9): the streak is reset by ANY whitelisted frame
    # (`if id_allowed: self._whitelist_miss_streak = 0`), so an
    # N-miss-then-hit pattern NEVER latches — the value is an
    # ESCALATION THRESHOLD, not a per-window miss ratio.
    WHITELIST_ESTOP_AFTER: ClassVar[int] = 5
    # P7 (G-5): hard ceiling on the aggregate TX rate across EVERY lane. Must
    # stay below `sum(BUDGETS.values())` so it is a real envelope rather than
    # a rubber stamp; a runaway producer cannot exceed it by rotating
    # `budget_category` strings.
    MAX_TOTAL_TX_PER_SEC: ClassVar[int] = 400
    # P2 (G-2): UDS service identifiers whose transmission mutates ECU state
    # (session/reset/clear/security/routine/write/communication-control).
    # A whitelisted diagnostic frame carrying one of these as its first
    # service byte is critical EVEN IF the caller forgot the flag.
    CRITICAL_UDS_SIDS: ClassVar[frozenset[int]] = frozenset(
        {0x10, 0x11, 0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36, 0x37, 0x85}
    )
    # P2 (G-2): J1939 PGNs that CLEAR / MUTATE diagnostic state. DM11 (65235)
    # clears active DTCs, DM3 (65228) clears previously-active DTCs. Requests
    # (59904) and reads are deliberately excluded so a read-only DM1/DM4 poll
    # is not forced onto the physical-speed interlock.
    CRITICAL_J1939_PGNS: ClassVar[frozenset[int]] = frozenset({65235, 65228})
    # P0 (perf): backpressure WARN output is rate-limited to one summary per
    # second — a throttled sender hammering the window used to emit one
    # log record per rejected frame, flooding stdout I/O and slowing the
    # very loop that should back off (positive feedback).
    _RATE_LOG_INTERVAL_NS: ClassVar[int] = 1_000_000_000

    # Per-category token buckets (F-18): protocol bursts such as a J1939 BAM
    # transfer (<=255 packets) must fit inside a single burst budget.
    # P0 (perf): the 'simulation' lane carries synthetic multi-ECU traffic
    # (4x50 Hz + 2x10 Hz + 2x1 Hz ~= 220 msg/s aggregate) which the 100 msg/s
    # default sliding window rejected at ~80% rate — with per-frame WARN
    # output and eventual RATE_ESTOP_AFTER escalation, the simulator was
    # throttling itself into an E-Stop. Capacity 500/refill 250 sustains the
    # generator's real cadence while still bounding a runaway loop.
    BUDGETS: ClassVar[dict[str, tuple[int, float]]] = {
        "diagnostic": (10, 10.0),
        "calibration": (5, 5.0),
        "protocol_burst": (255, 100.0),
        "simulation": (500, 250.0),
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
        confirmation_secret: bytes | None = None,
        allow_legacy_boolean_confirm: bool = False,
        whitelist_superset_allowed: bool = False,
    ) -> None:
        self._bus = bus
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
        # P9 (G-11): explicit, auditable acknowledgement that the configured
        # mask set is a deliberately BROAD family override (e.g. a J1939
        # response mask of 0xF9 that matches many source addresses). The
        # narrow `(id & mask) == value` test alone cannot tell a broad
        # legitimately-configured mask from a mistake (`mask=0` matches
        # everything), so the override must be granted knowingly.
        self.whitelist_superset_allowed: bool = bool(whitelist_superset_allowed)
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
        #
        # P16 (G-7): the flag is stored under a name-mangled private slot and
        # exposed read-only. A plain attribute write used to be able to flip
        # the fail-closed Stage 3 off from any code holding the gateway.
        self.__whitelist_bypass_for_testing: bool = False
        # Optional HMAC key for ConfirmationToken verification (dual-confirm
        # hardening). When None, legacy boolean path stays intact.
        self._confirmation_secret: bytes | None = (
            bytes(confirmation_secret) if confirmation_secret is not None else None
        )
        # T41 / G-1 (Y-1): when a confirmation secret IS configured, the HMAC
        # ConfirmationToken is the ONLY accepted proof — a bare `user_confirmed`
        # boolean must NOT bypass it. The explicit, default-OFF escape hatch
        # below exists solely for staged migrations; enabling it restores the
        # legacy behavior with a WARNING instead of the fail-closed rejection.
        self._allow_legacy_boolean_confirm: bool = bool(allow_legacy_boolean_confirm)
        self._consumed_confirmations: set[bytes] = set()
        # Whitelist single-miss streak: first miss = reject+alarm, persistent
        # pattern (>= WHITELIST_ESTOP_AFTER) = latch E-Stop.
        self._whitelist_miss_streak: int = 0

        self._tx_timestamps: "collections.deque[tuple[int, int, int]]" = collections.deque()
        # P1-9: consecutive-rejection counter for sustained-overload detection
        self._rate_overload_streak: int = 0
        # P0 (perf): rate-limited backpressure logging state
        self._last_rate_log_ns: int = 0
        self._rate_log_suppressed: int = 0
        # HIGH-1: monotonically increasing per-call sequence number. Combined
        # with the thread id it makes every stamp uniquely identifiable, so a
        # rollback removes EXACTLY the caller's own reservation — never the
        # newest stamp of an unrelated concurrent sender (old blind pop()).
        self._stamp_seq: int = 0
        self._budgets: dict[str, TxBudget] = {
            name: TxBudget(capacity, refill) for name, (capacity, refill) in self.BUDGETS.items()
        }
        # P1 (G-1): provenance split. The INTERLOCK only ever reads the
        # physical field; the display field is telemetry/UI only and can be
        # written by the simulator without ever authorising a critical
        # command. `_current_vehicle_speed_kmh` remains the display mirror
        # for backward compatibility with existing readers.
        self._physical_speed_kmh: float = 0.0
        self._display_speed_kmh: float = 0.0
        self._last_speed_update_ns: int = 0
        self._lock = threading.RLock()

        # P7 (G-5): global aggregate TX envelope. Per-lane token buckets bound
        # each LANE, but the sum of every lane's capacity used to be
        # reachable by a mis-wired producer that simply picked a different
        # `budget_category` string — the total TX plane had no ceiling. This
        # non-bypassing window caps the aggregate of ALL lanes.
        self._tx_total_timestamps: "collections.deque[int]" = collections.deque()
        self._total_overload_streak: int = 0

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

    @property
    def bus(self) -> AbstractBus:
        """Read-only HAL bus handle (mutable security dependency guard)."""
        return self._bus

    @bus.setter
    def bus(self, _value: AbstractBus) -> None:
        raise AttributeError("TxSafetyGateway.bus is read-only (use rebind_bus for controlled swaps)")

    @property
    def _whitelist_bypass_for_testing(self) -> bool:
        """Read-only view of the test-only whitelist bypass flag (P16 / G-7)."""
        return self.__whitelist_bypass_for_testing

    @_whitelist_bypass_for_testing.setter
    def _whitelist_bypass_for_testing(self, _value: bool) -> None:
        raise AttributeError(
            "TxSafetyGateway._whitelist_bypass_for_testing is read-only "
            "(use TxSafetyGateway.for_testing under UCANLAB_TEST_MODE=1)"
        )

    @property
    def _current_vehicle_speed_kmh(self) -> float:
        """Backward-compatible DISPLAY mirror of the speed telemetry (P1 / G-1).

        Read-only on purpose: the interlock consumes `_physical_speed_kmh` and no
        caller may inject an interlock value by writing an attribute. Assignment
        is refused so a mock/test that used to poke this private field is forced
        through `update_physical_speed()` / `record_synthetic_speed()`.
        """
        return self._display_speed_kmh

    @_current_vehicle_speed_kmh.setter
    def _current_vehicle_speed_kmh(self, _value: float) -> None:
        raise AttributeError(
            "TxSafetyGateway._current_vehicle_speed_kmh is read-only "
            "(use update_physical_speed() / record_synthetic_speed())"
        )

    def rebind_bus(self, new_bus: AbstractBus) -> None:
        """Controlled HAL bus swap (reconnect path).

        The only sanctioned way to change the bus after construction. The swap
        itself is explicit and auditable; arbitrary attribute assignment stays
        blocked. Clears rate-limit state so the new channel starts clean.

        REVIEW (validate-then-dispatch TOCTOU, B-25): the swap runs under
        the E-Stop TX send lock and bumps the fence generation. A frame
        validated against the OLD bus (channel/whitelist/rate state) can no
        longer dispatch onto the NEW bus mid-reconnect — the PHASE 3 fence
        re-check rejects it fail-closed instead of writing to the wrong
        physical network.
        """
        # Serialize against in-flight dispatches (fence re-check + send run
        # under this lock) so the swap is atomic with respect to senders.
        with self.estop.tx_send_lock:
            with self._lock:
                old = self._bus
                self._bus = new_bus
                self._tx_timestamps.clear()
                self._rate_overload_streak = 0
                self._whitelist_miss_streak = 0
            # Invalidate every in-flight validated frame: dispatch compares
            # its fence snapshot under tx_send_lock and rejects on mismatch.
            # estop.tx_fence getter takes the estop lock; bump via trigger-
            # free generation advance (read-modify-write under estop lock).
            self.estop.advance_tx_fence("gateway bus rebind")
        logger.warning(
            "TX Gateway bus rebound",
            extra={
                "old_channel": getattr(old, "channel_id", None),
                "new_channel": getattr(new_bus, "channel_id", None),
            },
        )

    def issue_confirmation_token(self, arbitration_id: int, ttl_s: float = 30.0) -> bytes:
        """Mint a single-use HMAC confirmation token for a critical arbitration ID.

        Operator/UI authorization path: the token binds (arbitration_id, expiry,
        nonce) under the gateway confirmation secret. Requires a configured secret.
        """
        if self._confirmation_secret is None:
            raise SafetyError(
                "Confirmation tokens require a gateway confirmation_secret",
                code="CONFIRMATION_NOT_CONFIGURED",
            )
        import secrets as _secrets

        expiry_ns = time.monotonic_ns() + int(max(1.0, ttl_s) * 1_000_000_000)
        nonce = _secrets.token_bytes(16)
        payload = (
            int(arbitration_id).to_bytes(4, "big") + expiry_ns.to_bytes(8, "big", signed=False) + nonce
        )
        mac = hmac.new(self._confirmation_secret, payload, hashlib.sha256).digest()
        return payload + mac

    def _verify_confirmation_token(self, token: bytes | str, frame: CanFrame) -> None:
        """Verify a presented ConfirmationToken (fail-closed, single-use, TTL-bound)."""
        assert self._confirmation_secret is not None
        raw: bytes
        if isinstance(token, str):
            try:
                raw = bytes.fromhex(token.strip())
            except ValueError as exc:
                raise DualConfirmationRequiredError(
                    "Critical command rejected: malformed confirmation token",
                ) from exc
        elif isinstance(token, (bytes, bytearray)):
            raw = bytes(token)
        else:
            raise DualConfirmationRequiredError(
                "Critical command rejected: malformed confirmation token",
            )
        if len(raw) != 4 + 8 + 16 + 32:
            raise DualConfirmationRequiredError(
                "Critical command rejected: malformed confirmation token",
            )
        payload, mac = raw[:-32], raw[-32:]
        expected = hmac.new(self._confirmation_secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise DualConfirmationRequiredError(
                "Critical command rejected: invalid confirmation token",
            )
        arb = int.from_bytes(payload[0:4], "big")
        expiry_ns = int.from_bytes(payload[4:12], "big", signed=False)
        if arb != frame.arbitration_id:
            raise DualConfirmationRequiredError(
                "Critical command rejected: confirmation token arbitration mismatch",
            )
        if time.monotonic_ns() > expiry_ns:
            raise DualConfirmationRequiredError(
                "Critical command rejected: confirmation token expired",
            )
        if payload in self._consumed_confirmations:
            raise DualConfirmationRequiredError(
                "Critical command rejected: confirmation token already consumed",
            )
        self._consumed_confirmations.add(payload)
        if len(self._consumed_confirmations) > 1024:
            # Bounded replay set: TTL expiry bounds live tokens; evict oldest.
            self._consumed_confirmations.pop()

    @classmethod
    def for_testing(
        cls,
        bus: AbstractBus,
        estop: EmergencyStopSystem | None = None,
        whitelist_ids: set[int] | None = None,
    ) -> TxSafetyGateway:
        """Test/demo-only factory that bypasses the fail-closed whitelist stage.

        Guarded: requires UCANLAB_TEST_MODE==1 in the environment, otherwise
        raises RuntimeError so production wiring cannot reach the bypass.
        Kept out of the production constructor signature on purpose: the
        bypass is only reachable through this explicitly named factory.
        All other policy stages (E-Stop, speed interlock, dual confirmation,
        rate budget) remain fully enforced.
        """
        if os.environ.get("UCANLAB_TEST_MODE") != "1":
            raise RuntimeError(
                "TxSafetyGateway.for_testing requires UCANLAB_TEST_MODE==1 "
                "(refusing fail-closed whitelist bypass in production)"
            )
        instance = cls(bus=bus, estop=estop, whitelist_ids=whitelist_ids)
        # P16 (G-7): written through the name-mangled slot — the public
        # `_whitelist_bypass_for_testing` name is a read-only property.
        instance._TxSafetyGateway__whitelist_bypass_for_testing = True
        return instance

    def _on_estop_triggered(self, event: object) -> None:
        logger.warning("TX Gateway notified of E-Stop engagement. All TX halted.")
        if self.supervisor and not self.supervisor.is_fault:
            self.supervisor.trigger_fault("E-Stop engagement triggered from hardware/software event")
        with self._lock:
            self._tx_timestamps.clear()
            # M-11 (P2-1): reset the overload streak that caused this E-Stop.
            # The streak survived the latch — after the operator completed the
            # authenticated reset, the very first burst re-latched the E-Stop
            # immediately, making the rate-limit condition effectively
            # un-clearable without a process restart.
            self._rate_overload_streak = 0
            self._whitelist_miss_streak = 0

    def _on_safety_state_changed(self, old_state: object, new_state: object, reason: str) -> None:
        if getattr(new_state, "value", str(new_state)) == "FAULT":
            with self._lock:
                self._tx_timestamps.clear()

    def update_vehicle_speed(self, speed_kmh: float, *, source: str = "physical") -> None:
        """Update live vehicle speed for dynamic interlock enforcement.

        Always timestamps with time.monotonic_ns() on reception to prevent
        clock domain skew. NaN, negative or non-finite values are treated as
        corrupted telemetry and invalidate freshness to 0 (fail-closed).

        P0-2 (REVIEW C-2) / P1 (G-1): `source` binds provenance to the
        interlock. Only `"physical"` (hardware-derived telemetry) may satisfy
        the speed interlock; any other value is written to the DISPLAY channel
        ONLY — it can never overwrite the value the interlock reads, so a
        simulated stationary vehicle cannot authorise a critical command while
        a real vehicle is connected and moving.

        Backward compat: signature kept. Prefer update_physical_speed() /
        record_synthetic_speed() going forward.
        """
        with self._lock:
            if source != "physical":
                # P1 (G-1): display-only write. The interlock fields
                # (`_physical_speed_kmh`, `_last_speed_update_ns`) are NOT
                # touched, so the last physical sample keeps governing the
                # freshness/value decision (fail-closed when none arrived).
                # A NaN/negative synthetic value invalidates the DISPLAY
                # channel only — it must never be able to *authorise* by
                # clearing the physical interlock (G-8).
                self._display_speed_kmh = (
                    float(speed_kmh) if math.isfinite(speed_kmh) and speed_kmh >= 0.0 else float("nan")
                )
                return
            if not math.isfinite(speed_kmh) or speed_kmh < 0.0:
                # G-8: corrupted PHYSICAL telemetry fails the interlock closed.
                self._physical_speed_kmh = float("nan")
                self._display_speed_kmh = float("nan")
                self._last_speed_update_ns = 0
                return
            self._physical_speed_kmh = float(speed_kmh)
            self._display_speed_kmh = float(speed_kmh)
            self._last_speed_update_ns = time.monotonic_ns()

    def update_physical_speed(self, speed_kmh: float) -> None:
        """Authoritative HAL speed feed — the ONLY writer of interlock freshness.

        Must be called exclusively by the trusted hardware telemetry path.
        """
        self.update_vehicle_speed(speed_kmh, source="physical")

    def record_synthetic_speed(self, speed_kmh: float) -> None:
        """Non-authoritative simulator/scenario speed (display only).

        Recorded for telemetry but never satisfies the TX speed interlock.
        """
        self.update_vehicle_speed(speed_kmh, source="synthetic")

    def is_speed_fresh_and_safe(self, max_age_ns: int | None = None) -> bool:
        """Public inquiry API for preflight checks: True when speed is fresh and below threshold."""
        timeout = max_age_ns if max_age_ns is not None else self.SPEED_VALIDITY_TIMEOUT_NS
        with self._lock:
            if self._last_speed_update_ns == 0:
                return False
            now_ns = time.monotonic_ns()
            if (now_ns - self._last_speed_update_ns) > timeout:
                return False
            if math.isnan(self._physical_speed_kmh):
                return False
            return self._physical_speed_kmh <= self.SPEED_NOISE_THRESHOLD_KMH

    def speed_interlock_state(self, max_age_ns: int | None = None) -> tuple[str, float]:
        """REVIEW 3: tri-state interlock verdict with the live PHYSICAL speed.

        Single authoritative speed source for UI/supervisor preflight gates
        (kills the desktop's parallel `_current_speed_kmh` truth):
          ("stale", speed)  — no physical feed yet, timed out, or NaN
          ("moving", speed) — fresh physical speed above the noise threshold
          ("ok", speed)     — fresh, finite, at/below threshold

        P1 (G-1): the returned value is `_physical_speed_kmh` — the synthetic
        display channel never reaches a preflight gate.
        """
        timeout = max_age_ns if max_age_ns is not None else self.SPEED_VALIDITY_TIMEOUT_NS
        with self._lock:
            speed = self._physical_speed_kmh
            if self._last_speed_update_ns == 0:
                return "stale", speed
            if (time.monotonic_ns() - self._last_speed_update_ns) > timeout:
                return "stale", speed
            if math.isnan(speed):
                return "stale", speed
            if speed > self.SPEED_NOISE_THRESHOLD_KMH:
                return "moving", speed
            return "ok", speed

    def _frame_is_critical(self, frame: CanFrame) -> bool:
        """P2 (G-2): derive command criticality from the FRAME ITSELF.

        The `is_critical_command` caller flag is an assertion, not a control:
        a new/mis-wired protocol engine could send UDS 0x11 ECUReset with the
        flag left at its `False` default and skip Stage 4/5 entirely. This
        policy classifies a frame as critical on its own evidence, so the
        caller can only ever TIGHTEN the gate, never relax it.

        Classification (deliberately narrow to avoid false positives):
          * A whitelisted classical UDS request carrying an ISO-TP
            SingleFrame / FirstFrame PCI nibble (0x0 / 0x1) whose service
            byte is one of `CRITICAL_UDS_SIDS`. ConsecutiveFrames (0x2) and
            flow-control (0x3) carry no SID and are never escalated.
          * J1939 extended frames for PGN 65235 (DM11 Clear Active DTCs) and
            PGN 65228 (DM3 Clear Previously Active DTCs). Read-only requests
            such as PGN 59904 (Request) are intentionally NOT included.
        """
        try:
            data = frame.data
            if not data:
                return False
            is_extended = bool(getattr(frame, "is_extended", False))
            if not is_extended:
                if frame.arbitration_id not in self.whitelist_ids:
                    return False
                if len(data) < 2:
                    return False
                pci = data[0] >> 4
                if pci not in (0x0, 0x1):
                    return False
                return data[1] in self.CRITICAL_UDS_SIDS
            if not (self.whitelist_ids or self.whitelist_masks):
                # Fail-closed: no whitelist configured means Stage 3 refuses
                # everything anyway; do not invent criticality here.
                return False
            pgn = (frame.arbitration_id >> 8) & 0x3FFFF
            return pgn in self.CRITICAL_J1939_PGNS
        except Exception:  # pragma: no cover - defensive: never fail open
            logger.error("Criticality classification failed; treating frame as critical", exc_info=True)
            return True

    def validate_and_transmit(
        self,
        frame: CanFrame,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
        confirmation_token: bytes | str | None = None,
        inbound_triggered: bool = False,
    ) -> bool:
        """Enforce strict 6-stage policy evaluation order before transmitting onto HAL.

        Lock structure: validation + token consumption under lock → snapshot estop
        state → release lock → final estop guard with rollback → transmit outside lock.
        This ensures watchdog/estop callbacks never block on driver I/O.

        confirmation_token: optional HMAC-bound dual-confirmation token. When a
        confirmation secret is configured on the gateway, a critical command
        must present a valid single-use token; the legacy `user_confirmed`
        boolean remains for backward compatibility and is documented as
        operator-assertion only (not cryptographic proof).

        inbound_triggered: marks the frame as a PROTOCOL RESPONSE to inbound
        bus traffic (J1939 TP CTS/ACK, ISO-TP FC) rather than an
        application-originated command. The RATE_ESTOP escalation is
        disabled for these: a hostile/busy remote node RTS-flooding the tool
        must not be able to lock the entire TX plane by baiting CTS
        responses above the default-lane budget (remote self-DoS). Such
        responses are still rate-limited (reject + drop + log) but never
        escalate to an E-Stop.

        P2 (G-2): criticality is DERIVED from the frame (see
        `_frame_is_critical`) in addition to the caller flag, so a protocol
        engine that forgets `is_critical_command=True` still hits Stage 4/5.
        """
        # P2 (G-2): a caller may only ever TIGHTEN the criticality gate.
        if not is_critical_command and self._frame_is_critical(frame):
            is_critical_command = True
            logger.warning(
                "Frame criticality derived from frame contents (caller flag was False)",
                extra={"arbitration_id": hex(getattr(frame, "arbitration_id", 0))},
            )
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
                if id_allowed:
                    # Allowed frame resets the single-miss streak.
                    self._whitelist_miss_streak = 0
                if not id_allowed:
                    # Reject + alarm on every miss; latch E-Stop when the
                    # persistent-violation streak reaches WHITELIST_ESTOP_AFTER.
                    # P9 (G-9): the earlier comment claimed "threshold currently
                    # 1" while the constant is 5, and a single miss does NOT
                    # latch — the streak resets on any allowed frame. The
                    # escalation is real but only for an unbroken run of misses.
                    # RLock is re-entrant: trigger() -> gateway callback
                    # re-acquires the same lock on this thread without deadlock.
                    self._whitelist_miss_streak += 1
                    logger.warning(
                        "TX Frame rejected by Whitelist filter",
                        extra={
                            "arbitration_id": hex(frame.arbitration_id),
                            "streak": self._whitelist_miss_streak,
                        },
                    )
                    if self._whitelist_miss_streak >= self.WHITELIST_ESTOP_AFTER:
                        logger.critical(
                            "Whitelist violation pattern — triggering E-Stop",
                            extra={"streak": self._whitelist_miss_streak},
                        )
                        self.estop.trigger(
                            EStopTriggerSource.UNAUTHORIZED_PAYLOAD,
                            f"TX to non-whitelisted ID: 0x{frame.arbitration_id:08X} "
                            f"(streak {self._whitelist_miss_streak})",
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
                        vehicle_speed_kmh=self._physical_speed_kmh,
                    )
                    raise SpeedDataStaleError(
                        "Safety Interlock: Critical command blocked due to stale vehicle speed telemetry",
                    )

                # Speed threshold check (P1 / G-1: PHYSICAL channel only)
                if self._physical_speed_kmh > self.SPEED_NOISE_THRESHOLD_KMH:
                    logger.critical(
                        "Speed interlock triggered on critical command",
                        extra={"speed": self._physical_speed_kmh},
                    )
                    self.estop.trigger(
                        EStopTriggerSource.SPEED_INTERLOCK_BREACH,
                        f"Critical command attempted while moving ({self._physical_speed_kmh} km/h)",
                        vehicle_speed_kmh=self._physical_speed_kmh,
                    )
                    raise SpeedInterlockError(
                        f"Safety Interlock: Critical command blocked while vehicle is moving ({self._physical_speed_kmh} km/h)",
                    )

            # -----------------------------------------------------------------
            # Stage 5: Dual Confirmation Check
            #
            # Backward compat: the `user_confirmed` boolean is KEPT (API break
            # is out of scope). It is documented as operator-assertion only.
            # When a confirmation secret is configured, the HMAC token is the
            # ONLY accepted proof: a missing token fails CLOSED even when the
            # boolean is True (T41 / G-1 fix). The legacy boolean path is
            # reachable only via an explicit, default-OFF constructor opt-in
            # (`allow_legacy_boolean_confirm=True`), which logs a WARNING.
            # -----------------------------------------------------------------
            if is_critical_command:
                if self._confirmation_secret is not None and confirmation_token is not None:
                    self._verify_confirmation_token(confirmation_token, frame)
                if not user_confirmed:
                    raise DualConfirmationRequiredError(
                        "Critical command rejected: Operator dual-confirmation missing",
                    )
                if self._confirmation_secret is not None and confirmation_token is None:
                    if not self._allow_legacy_boolean_confirm:
                        # Fail-closed: a configured secret means cryptographic
                        # proof is mandatory — the boolean is NOT sufficient.
                        raise DualConfirmationRequiredError(
                            "Critical command rejected: confirmation token required "
                            "(a confirmation secret is configured on this gateway)",
                        )
                    # Explicit legacy opt-in: allow (compat) but audit loudly.
                    logger.warning(
                        "Critical command used legacy boolean confirmation "
                        "(no ConfirmationToken presented)",
                        extra={"arbitration_id": hex(frame.arbitration_id)},
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

            # P7 (G-5): GLOBAL aggregate envelope. The per-lane buckets below
            # bound each lane in isolation; this window bounds their SUM, so a
            # mis-wired producer cannot raise the effective ceiling by simply
            # rotating `budget_category`. Applied to EVERY lane, including
            # protocol responses (inbound_triggered), because bus starvation
            # is a physical limit, not a trust decision.
            while self._tx_total_timestamps and (
                now_ns - self._tx_total_timestamps[0]
            ) >= self.RATE_LIMIT_WINDOW_NS:
                self._tx_total_timestamps.popleft()
            if len(self._tx_total_timestamps) >= self.MAX_TOTAL_TX_PER_SEC:
                self._total_overload_streak += 1
                logger.error(
                    "Global TX envelope exceeded — aggregate rate limit across all lanes",
                    extra={
                        "aggregate_in_window": len(self._tx_total_timestamps),
                        "limit": self.MAX_TOTAL_TX_PER_SEC,
                        "category": budget_category,
                    },
                )
                if self._total_overload_streak >= self.RATE_ESTOP_AFTER and not inbound_triggered:
                    logger.critical(
                        "Sustained global TX overload — triggering E-Stop",
                        extra={"streak": self._total_overload_streak},
                    )
                    self.estop.trigger(
                        EStopTriggerSource.RATE_LIMIT_OVERFLOW,
                        f"Sustained global TX envelope overload "
                        f"({self._total_overload_streak} consecutive rejections)",
                    )
                raise RateLimitExceededError(
                    f"Global TX envelope exceeded ({self.MAX_TOTAL_TX_PER_SEC} msg/s across all lanes)"
                )
            self._total_overload_streak = 0
            self._tx_total_timestamps.append(now_ns)

            # M-23 (P2-4): the simulation lane's generous budget (500/250)
            # exists for the DEMO generator's synthetic multi-ECU traffic. A
            # frame only qualifies when BOTH the bus is virtual AND the frame
            # itself is synthetic — the old check trusted the caller's
            # category string alone, so a mis-wired producer could pump 250
            # msg/s of live-bus traffic through the simulator lane.
            if budget_category == "simulation":
                # Lazy import: gateway → HAL import at module scope would
                # create a cycle for VirtualBus (a HAL leaf).
                from src.hal.virtual import VirtualBus as _VirtualBus

                bus_is_virtual = getattr(self._bus, "interface", None) == "virtual" or isinstance(
                    self._bus, _VirtualBus
                )
                frame_is_synthetic = getattr(frame, "source", None) in ("virtual", "synthetic")
                if not (bus_is_virtual and frame_is_synthetic):
                    raise FrameSanityError(
                        "TX budget 'simulation' is reserved for synthetic frames on a virtual bus",
                        details={
                            "frame_source": getattr(frame, "source", None),
                            "bus_interface": getattr(self._bus, "interface", None),
                        },
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
                    # REVIEW HIGH (remote self-DoS): inbound-triggered protocol
                    # responses (CTS/ACK/FC answers to bus traffic) never
                    # escalate — a hostile or malfunctioning remote node
                    # RTS-flooding the tool must not be able to bait the
                    # responder into latching a crypto-reset E-Stop that
                    # kills the entire TX plane. Reject + count + log only.
                    if (
                        self._rate_overload_streak >= self.RATE_ESTOP_AFTER
                        and not inbound_triggered
                    ):
                        logger.critical(
                            "TX rate limit sustained violation pattern — triggering E-Stop",
                            extra={"streak": self._rate_overload_streak},
                        )
                        self.estop.trigger(
                            EStopTriggerSource.RATE_LIMIT_OVERFLOW,
                            f"Sustained TX rate overload ({self._rate_overload_streak} consecutive rejections)",
                        )
                    elif inbound_triggered and self._rate_overload_streak >= self.RATE_ESTOP_AFTER:
                        # Rate-limited visibility for the inbound-response flood.
                        if (now_ns - self._last_rate_log_ns) >= self._RATE_LOG_INTERVAL_NS:
                            logger.warning(
                                "Inbound-triggered protocol responses rate-limited (no E-Stop escalation)",
                                extra={
                                    "streak": self._rate_overload_streak,
                                    "suppressed_since_last": self._rate_log_suppressed,
                                },
                            )
                            self._last_rate_log_ns = now_ns
                            self._rate_log_suppressed = 0
                        else:
                            self._rate_log_suppressed += 1
                    else:
                        # P0 (perf): one backpressure summary per second —
                        # per-frame WARNINGs at burst rate flooded the log
                        # I/O and slowed the sender further (positive
                        # feedback, mirroring the FrameRouter E-2 fix).
                        if (now_ns - self._last_rate_log_ns) >= self._RATE_LOG_INTERVAL_NS:
                            logger.warning(
                                "TX rate limit exceeded — frames rejected (backpressure)",
                                extra={
                                    "streak": self._rate_overload_streak,
                                    "suppressed_since_last": self._rate_log_suppressed,
                                },
                            )
                            self._last_rate_log_ns = now_ns
                            self._rate_log_suppressed = 0
                        else:
                            self._rate_log_suppressed += 1
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
        # the validate-then-send window against any COMPLETED trigger/reset
        # transition (each bumps the generation under the estop lock).
        # trigger() deliberately does NOT take this lock: a dispatch blocked
        # in driver I/O holds it, and the engagement must still complete
        # promptly (test_estop_callback_does_not_block_on_slow_driver_io).
        #
        # P8 (G-6) — RESIDUAL WINDOW, ACCEPTED RISK (no mitigation wired):
        # the window between the fence re-check below and `privileged_send`
        # cannot be closed by an abort/flush hook today. `EmergencyStopSystem`
        # supports `register_abort_hook` and runs the hooks outside its locks
        # (estop.py trigger()), but this gateway deliberately does NOT register
        # one because no HAL driver exposes a TX flush/abort primitive
        # (`src/hal` has no `flush`/`abort` on AbstractBus or its drivers) — a
        # hook would have nothing to call. The window is therefore an accepted,
        # documented residual: bounded by the duration of a single
        # `privileged_send` call on a driver that is not already wedged. When a
        # HAL flush/abort API lands, register it here via
        # `self.estop.register_abort_hook(...)` — that is the intended home for
        # the mitigation described in the review.
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
            self._bus.privileged_send(frame)
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
        confirmation_token: bytes | str | None = None,
        inbound_triggered: bool = False,
    ) -> None:
        """Synchronously transmit frame conforming to TxPort protocol.

        P1-9: the optional budget_category lets protocol engines use their
        dedicated lanes (e.g. 'protocol_burst' for ISO-TP CF trains) instead
        of colliding with the 100 msg/s default-lane wall. Defaults to the
        uncategorised 'default' lane for plain TxPort callers.

        inbound_triggered: protocol responses to inbound traffic — never
        E-Stop-escalated on rate overload (see validate_and_transmit).
        """
        self.validate_and_transmit(
            frame,
            is_critical_command=is_critical_command,
            user_confirmed=user_confirmed,
            budget_category=budget_category,
            confirmation_token=confirmation_token,
            inbound_triggered=inbound_triggered,
        )

    async def send(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
        confirmation_token: bytes | str | None = None,
        inbound_triggered: bool = False,
    ) -> None:
        """Asynchronously transmit without blocking the running event loop (F-26/E-12).

        The synchronous validation pipeline may perform blocking work (driver TX,
        E-Stop state checks) — offloading it keeps ISO-TP CF bursts responsive.

        MEDIUM-6: the offload runs on the gateway's OWN bounded, managed
        ThreadPoolExecutor (thread_name_prefix 'tx-gateway-'), never on
        the event loop's shared default executor (which is unbounded and shared
        with every other offload in the process).

        P11 (G-11): `inbound_triggered` used to be dropped here, so an async
        protocol responder could self-E-Stop on a remote RTS flood while the
        sync path was protected. It is forwarded to `send_sync` explicitly.
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
            confirmation_token=confirmation_token,
            inbound_triggered=inbound_triggered,
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

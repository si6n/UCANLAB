"""Formal Safety State Machine enforcing Fail-Silent and Safe-by-Default Operation.

Complies with Saha Risk Kataloğu v1.2 Sections 4, 5, 6, 37, 38 and CAN-12, CAN-25.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets as _secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

from src.core.errors import SafetyError
from src.core.logging import get_logger

logger = get_logger("safety.state_machine")

MAX_REASON_LEN: int = 512
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_reason(reason: str) -> str:
    """Bound + sanitize audit reason (fail-closed, log-injection hardening)."""
    if not isinstance(reason, str):
        reason = str(reason)
    if len(reason) > MAX_REASON_LEN:
        reason = reason[:MAX_REASON_LEN] + "…[truncated]"
    return _CONTROL_CHAR_RE.sub("?", reason)


class SafetyState(str, Enum):
    """Formal Safety Operational States."""

    STARTUP = "STARTUP"  # System booting up, hardware initializing, TX strictly blocked
    SAFE = "SAFE"  # Initialization complete, bus offline or TX disconnected
    PASSIVE = "PASSIVE"  # Default mode: Listen-Only RX, zero transmissions allowed
    ARMED_TX = "ARMED_TX"  # Explicit operator authorization granted, policy checks verified
    ACTIVE = "ACTIVE"  # Active verified transmission in progress with live watchdog lease
    FAULT = "FAULT"  # Safety violation / Watchdog timeout / E-Stop; TX revoked, queue flushed


@dataclass(slots=True, frozen=True)
class StateTransitionRecord:
    """Immutable audit log record for state transitions."""

    from_state: SafetyState
    to_state: SafetyState
    reason: str
    epoch: int
    monotonic_timestamp_ns: int
    wall_time_utc: datetime
    duration_ns: int

    def to_dict(self) -> dict[str, Any]:
        """Convert transition record to dictionary format for audit logging."""
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "reason": self.reason,
            "epoch": self.epoch,
            "monotonic_timestamp_ns": self.monotonic_timestamp_ns,
            "wall_time_utc": self.wall_time_utc.isoformat(),
            "duration_ns": self.duration_ns,
        }


class SafetySupervisor:
    """Central Safety State Supervisor governing all transmission permissions."""

    ALLOWED_TRANSITIONS: ClassVar[dict[SafetyState, set[SafetyState]]] = {
        SafetyState.STARTUP: {SafetyState.SAFE, SafetyState.FAULT},
        SafetyState.SAFE: {SafetyState.PASSIVE, SafetyState.FAULT},
        SafetyState.PASSIVE: {SafetyState.ARMED_TX, SafetyState.SAFE, SafetyState.FAULT},
        SafetyState.ARMED_TX: {SafetyState.ACTIVE, SafetyState.PASSIVE, SafetyState.FAULT},
        SafetyState.ACTIVE: {SafetyState.ARMED_TX, SafetyState.PASSIVE, SafetyState.FAULT},
        SafetyState.FAULT: {
            SafetyState.PASSIVE,
            SafetyState.SAFE,
        },  # MUST NOT transition directly to ARMED_TX or ACTIVE
    }

    def __init__(
        self,
        initial_state: SafetyState = SafetyState.STARTUP,
        estop: Any | None = None,
        auth_secret: bytes | None = None,
        allow_unauthenticated_arm: bool = False,
    ) -> None:
        # Fail-open start guard: TX-permitting states can never be the boot state.
        if initial_state in {SafetyState.ARMED_TX, SafetyState.ACTIVE}:
            raise ValueError(
                f"Refusing fail-open boot state {initial_state.value}: "
                "initial_state must be STARTUP/SAFE/PASSIVE/FAULT"
            )
        if auth_secret is not None and not isinstance(auth_secret, (bytes, bytearray)):
            raise TypeError("auth_secret must be bytes or None")
        self._state = initial_state
        self._estop = estop
        self._estop_bound = estop is not None
        # T41 / S-1 (Y-2): the ARMED_TX/ACTIVE gate is the single functional
        # authorization point for TX. When an HMAC `auth_secret` is configured,
        # arm_tx/activate_tx REQUIRE a valid single-use token — a missing token
        # fails CLOSED with SafetyError. `allow_unauthenticated_arm=True` is an
        # explicit, default-OFF escape hatch for staged migrations.
        # When auth_secret is None there is no authenticator wired: the legacy
        # (unauthenticated) path is preserved for backward compatibility and
        # logs a WARNING so the missing identity check is visible.
        self._auth_secret: bytes | None = bytes(auth_secret) if auth_secret is not None else None
        self._allow_unauthenticated_arm: bool = bool(allow_unauthenticated_arm)
        self._consumed_arm_tokens: deque[bytes] = deque(maxlen=1024)
        self._epoch: int = 0
        self._state_change_timestamp_ns: int = time.monotonic_ns()
        self._last_duration_ns: int = 0
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[SafetyState, SafetyState, str], None]] = []
        self._fault_reason: str = ""
        self._history: deque[StateTransitionRecord] = deque(maxlen=10_000)
        self._fault_timestamps_ns: deque[int] = deque(maxlen=32)

    def bind_estop(self, estop: Any) -> None:
        """Associate an EmergencyStopSystem instance to govern fault exits.

        One-time only + type-checked: the object must expose `is_engaged`,
        otherwise TypeError. Re-binding raises SafetyError (fail-closed).
        """
        with self._lock:
            if self._estop_bound:
                raise SafetyError(
                    "E-Stop already bound (one-time binding)",
                    code="ESTOP_ALREADY_BOUND",
                )
            if not hasattr(estop, "is_engaged"):
                raise TypeError(
                    f"bind_estop requires an object with 'is_engaged', got {type(estop).__name__}"
                )
            self._estop = estop
            self._estop_bound = True

    @property
    def current_state(self) -> SafetyState:
        """Thread-safe property returning the current safety state."""
        with self._lock:
            return self._state

    def get_state(self) -> SafetyState:
        """Thread-safe accessor returning the current safety state."""
        return self.current_state

    @property
    def epoch(self) -> int:
        """Monotonically increasing state transition epoch counter."""
        with self._lock:
            return self._epoch

    def get_epoch(self) -> int:
        """Thread-safe accessor returning the current state transition epoch."""
        return self.epoch

    @property
    def is_tx_permitted(self) -> bool:
        """TX is strictly prohibited unless in ARMED_TX or ACTIVE state."""
        with self._lock:
            return self._state in {SafetyState.ARMED_TX, SafetyState.ACTIVE}

    @property
    def is_passive(self) -> bool:
        """Check if supervisor is in PASSIVE listen-only mode."""
        with self._lock:
            return self._state == SafetyState.PASSIVE

    @property
    def is_fault(self) -> bool:
        """Check if supervisor is currently in FAULT state."""
        with self._lock:
            return self._state == SafetyState.FAULT

    @property
    def fault_reason(self) -> str:
        """Thread-safe property returning the reason for entering FAULT state."""
        with self._lock:
            return self._fault_reason

    def get_fault_reason(self) -> str:
        """Thread-safe accessor returning the reason for entering FAULT state."""
        return self.fault_reason

    @property
    def state_change_timestamp_ns(self) -> int:
        """Monotonic timestamp in nanoseconds of the most recent state transition."""
        with self._lock:
            return self._state_change_timestamp_ns

    @property
    def state_duration_ns(self) -> int:
        """Nanoseconds elapsed in the current safety state calculated via monotonic clock."""
        with self._lock:
            current_ns = time.monotonic_ns()
            return max(0, current_ns - self._state_change_timestamp_ns)

    def get_state_duration_ns(self) -> int:
        """Thread-safe accessor returning nanoseconds spent in the current safety state."""
        return self.state_duration_ns

    @property
    def state_duration_sec(self) -> float:
        """Seconds elapsed in the current safety state calculated via monotonic clock."""
        return self.state_duration_ns / 1_000_000_000.0

    def get_history(self) -> list[StateTransitionRecord]:
        """Thread-safe accessor returning a copy of state transition history records."""
        with self._lock:
            return list(self._history)

    def register_callback(self, callback: Callable[[SafetyState, SafetyState, str], None]) -> None:
        """Register listener for state change events (old_state, new_state, reason)."""
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[SafetyState, SafetyState, str], None]) -> None:
        """Unregister a previously registered listener callback."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    FAULT_RATE_LIMIT_PER_SEC: ClassVar[int] = 5

    def transition_to(self, new_state: SafetyState, reason: str = "") -> None:
        """Execute a formal validated state transition adhering to Snapshot-Then-Release."""
        reason = _sanitize_reason(reason)
        illegal: str | None = None
        with self._lock:
            if self._state == new_state:
                return

            now_monotonic_ns = time.monotonic_ns()
            now_utc = datetime.now(timezone.utc)
            duration_ns = max(0, now_monotonic_ns - self._state_change_timestamp_ns)

            if (
                self._estop is not None
                and getattr(self._estop, "is_engaged", False)
                and new_state in {SafetyState.PASSIVE, SafetyState.ARMED_TX, SafetyState.ACTIVE}
            ):
                err_msg = (
                    f"Cannot transition to '{new_state.value}' while Emergency Stop is engaged. "
                    "Reset E-Stop with valid cryptographic token first."
                )
                logger.critical(err_msg)
                raise SafetyError(err_msg, code="ESTOP_ENGAGED")

            allowed = self.ALLOWED_TRANSITIONS.get(self._state, set())
            if new_state not in allowed:
                # Illegal transition: mutate state and snapshot listeners under
                # the lock, but defer dispatch AND the raise to outside (B6) so
                # re-entrant callbacks cannot deadlock and no SafetyError ever
                # unwinds through a held lock.
                err_msg = (
                    f"Illegal Safety State transition from '{self._state.value}' to '{new_state.value}'. "
                    f"Reason: {reason or 'N/A'}"
                )
                logger.critical(err_msg)
                old_state = self._state
                self._state = SafetyState.FAULT
                fault_reason = "ILLEGAL_STATE_TRANSITION: " + err_msg
                self._fault_reason = fault_reason
                self._epoch += 1
                self._state_change_timestamp_ns = now_monotonic_ns
                self._last_duration_ns = 0

                record = StateTransitionRecord(
                    from_state=old_state,
                    to_state=SafetyState.FAULT,
                    reason=fault_reason,
                    epoch=self._epoch,
                    monotonic_timestamp_ns=now_monotonic_ns,
                    wall_time_utc=now_utc,
                    duration_ns=duration_ns,
                )
                self._history.append(record)
                callbacks_snapshot = list(self._callbacks)
                illegal = err_msg
            else:
                old_state = self._state
                self._state = new_state
                self._epoch += 1
                self._state_change_timestamp_ns = now_monotonic_ns
                self._last_duration_ns = 0

                if new_state == SafetyState.FAULT:
                    self._fault_reason = reason
                elif old_state == SafetyState.FAULT and new_state in {SafetyState.PASSIVE, SafetyState.SAFE}:
                    self._fault_reason = ""

                record = StateTransitionRecord(
                    from_state=old_state,
                    to_state=new_state,
                    reason=reason,
                    epoch=self._epoch,
                    monotonic_timestamp_ns=now_monotonic_ns,
                    wall_time_utc=now_utc,
                    duration_ns=duration_ns,
                )
                self._history.append(record)
                callbacks_snapshot = list(self._callbacks)

        if illegal is not None:
            # Snapshot-Then-Release: dispatch + raise outside the lock (B6)
            self._dispatch_callbacks(callbacks_snapshot, old_state, SafetyState.FAULT, self._fault_reason)
            raise SafetyError(illegal, code="ILLEGAL_SAFETY_TRANSITION")

        logger.info(
            "Safety State Transition",
            extra={
                "from": old_state.value,
                "to": new_state.value,
                "reason": reason,
                "epoch": self._epoch,
                "duration_ms": duration_ns / 1_000_000.0,
            },
        )

        # Snapshot-Then-Release: Execute callbacks outside lock with exception isolation
        self._dispatch_callbacks(callbacks_snapshot, old_state, new_state, reason)

    def enter_passive_mode(self, reason: str = "Operator entered PASSIVE listen-only mode") -> None:
        """Safely transition to PASSIVE (Listen-Only) mode."""
        self.transition_to(SafetyState.PASSIVE, reason=reason)

    def issue_arm_token(self, ttl_s: float = 30.0) -> str:
        """Mint a single-use HMAC authorization token for arm_tx/activate_tx.

        Requires a configured `auth_secret` (see __init__). The token binds a
        random nonce + monotonic expiry under the secret; it is verified with
        hmac.compare_digest and burned on first use, so a replayed token fails
        closed. Format: ``<expiry_ns>.<nonce_hex>.<mac_hex>``.
        """
        if self._auth_secret is None:
            raise SafetyError(
                "Arm tokens require a configured supervisor auth_secret",
                code="ARM_AUTH_NOT_CONFIGURED",
            )
        expiry_ns = time.monotonic_ns() + int(max(1.0, ttl_s) * 1_000_000_000)
        nonce = _secrets.token_bytes(16)
        payload = expiry_ns.to_bytes(8, "big", signed=False) + nonce
        mac = hmac.new(self._auth_secret, payload, hashlib.sha256).digest()
        return f"{expiry_ns}.{nonce.hex()}.{mac.hex()}"

    def _verify_arm_token(self, auth_token: str) -> None:
        """Verify a presented arm token (fail-closed: expiry + single-use + HMAC)."""
        assert self._auth_secret is not None
        token = auth_token.strip() if isinstance(auth_token, str) else ""
        parts = token.split(".")
        if len(parts) != 3:
            raise SafetyError(
                "TX authorization rejected: malformed auth token",
                code="ARM_AUTH_INVALID",
            )
        expiry_s, nonce_hex, mac_hex = parts
        try:
            expiry_ns = int(expiry_s)
            nonce = bytes.fromhex(nonce_hex)
            mac = bytes.fromhex(mac_hex)
        except ValueError as exc:
            raise SafetyError(
                "TX authorization rejected: malformed auth token",
                code="ARM_AUTH_INVALID",
            ) from exc
        payload = expiry_ns.to_bytes(8, "big", signed=False) + nonce
        expected = hmac.new(self._auth_secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise SafetyError(
                "TX authorization rejected: invalid auth token",
                code="ARM_AUTH_INVALID",
            )
        if time.monotonic_ns() > expiry_ns:
            raise SafetyError(
                "TX authorization rejected: auth token expired",
                code="ARM_AUTH_EXPIRED",
            )
        if payload in self._consumed_arm_tokens:
            raise SafetyError(
                "TX authorization rejected: auth token already consumed",
                code="ARM_AUTH_REPLAYED",
            )
        self._consumed_arm_tokens.append(payload)

    def _require_arm_authorization(self, operation: str, auth_token: str | None) -> None:
        """Enforce the TX-authorization gate for arm_tx/activate_tx (T41 / S-1).

        Fail-closed policy:
          * ``auth_secret`` configured -> a valid, unexpired, single-use token
            is REQUIRED (missing/invalid => SafetyError). The legacy
            ``allow_unauthenticated_arm`` opt-in downgrades this to a WARNING.
          * ``auth_secret`` None -> no authenticator is wired; the legacy
            unauthenticated path is preserved with a WARNING (backward compat).
        """
        if self._auth_secret is not None:
            if auth_token is None:
                if self._allow_unauthenticated_arm:
                    logger.warning(
                        "%s without auth_token (allow_unauthenticated_arm=True legacy override)",
                        operation,
                    )
                    return
                logger.critical(
                    "%s without auth_token while auth_secret is configured — FAIL CLOSED",
                    operation,
                )
                raise SafetyError(
                    f"{operation} rejected: auth_token is required "
                    "(a supervisor auth_secret is configured)",
                    code="ARM_AUTH_REQUIRED",
                )
            self._verify_arm_token(auth_token)
            return
        logger.warning(
            "%s without auth_secret configured (legacy unauthenticated path)",
            operation,
        )

    def arm_tx(
        self,
        reason: str = "Operator explicitly ARMED TX pipeline",
        auth_token: str | None = None,
    ) -> None:
        """Explicitly arm the TX pipeline.

        The authorization gate is enforced by ``_require_arm_authorization``:
        when an ``auth_secret`` is configured on this supervisor, ``auth_token``
        is mandatory and HMAC-verified (fail-closed). See the constructor for
        the ``allow_unauthenticated_arm`` compatibility flag.
        """
        self._require_arm_authorization("arm_tx", auth_token)
        self.transition_to(SafetyState.ARMED_TX, reason=reason)

    def activate_tx(
        self,
        reason: str = "TX active transmission ongoing",
        auth_token: str | None = None,
    ) -> None:
        """Transition from ARMED_TX to ACTIVE (same authorization gate as arm_tx)."""
        self._require_arm_authorization("activate_tx", auth_token)
        self.transition_to(SafetyState.ACTIVE, reason=reason)

    def trigger_fault(self, reason: str = "Safety fault detected") -> None:
        """Trigger FAULT state, immediately revoking all TX authorization.

        Rate-limited to FAULT_RATE_LIMIT_PER_SEC per second (fail-closed audit;
        excess calls are logged and dropped to prevent DoS via fault storms).
        """
        now_ns = time.monotonic_ns()
        with self._lock:
            # Prune timestamps older than 1s.
            while self._fault_timestamps_ns and (now_ns - self._fault_timestamps_ns[0]) >= 1_000_000_000:
                self._fault_timestamps_ns.popleft()
            if len(self._fault_timestamps_ns) >= self.FAULT_RATE_LIMIT_PER_SEC:
                logger.warning(
                    "trigger_fault rate-limited (fault storm suppressed)",
                    extra={"reason": _sanitize_reason(reason)},
                )
                return
            self._fault_timestamps_ns.append(now_ns)
        logger.warning("Safety FAULT triggered", extra={"reason": _sanitize_reason(reason)})
        self.transition_to(SafetyState.FAULT, reason=reason)

    def _force_fault(self, reason: str) -> None:
        """Internal emergency transition to FAULT bypassing transition table.

        Adheres strictly to Snapshot-Then-Release locking and complete callback exception isolation.
        """
        reason = _sanitize_reason(reason)
        with self._lock:
            if self._state == SafetyState.FAULT and self._fault_reason == reason:
                return
            now_monotonic_ns = time.monotonic_ns()
            now_utc = datetime.now(timezone.utc)
            duration_ns = max(0, now_monotonic_ns - self._state_change_timestamp_ns)

            old_state = self._state
            self._state = SafetyState.FAULT
            self._fault_reason = reason
            self._epoch += 1
            self._state_change_timestamp_ns = now_monotonic_ns
            self._last_duration_ns = 0

            record = StateTransitionRecord(
                from_state=old_state,
                to_state=SafetyState.FAULT,
                reason=reason,
                epoch=self._epoch,
                monotonic_timestamp_ns=now_monotonic_ns,
                wall_time_utc=now_utc,
                duration_ns=duration_ns,
            )
            self._history.append(record)
            callbacks_snapshot = list(self._callbacks)

        logger.critical(
            "Safety State Forced FAULT",
            extra={
                "from": old_state.value,
                "to": SafetyState.FAULT.value,
                "reason": reason,
                "epoch": self._epoch,
            },
        )

        # Snapshot-Then-Release: Execute callbacks outside lock with exception isolation
        self._dispatch_callbacks(callbacks_snapshot, old_state, SafetyState.FAULT, reason)

    @staticmethod
    def _dispatch_callbacks(
        callbacks: list[Callable[[SafetyState, SafetyState, str], None]],
        old_state: SafetyState,
        new_state: SafetyState,
        reason: str,
    ) -> None:
        """Dispatch callbacks outside the lock with complete exception isolation."""
        for cb in callbacks:
            try:
                cb(old_state, new_state, reason)
            except Exception as exc:
                logger.error(
                    "Error in Safety State Machine callback",
                    extra={
                        "error": str(exc),
                        "callback": getattr(cb, "__name__", repr(cb)),
                        "from_state": old_state.value,
                        "to_state": new_state.value,
                    },
                    exc_info=True,
                )


__all__ = [
    "SafetyState",
    "SafetySupervisor",
    "StateTransitionRecord",
]

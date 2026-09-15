"""10-Trigger Hardware/Software Emergency Stop (E-Stop) Interlock System.

Enforces immediate hardware and software transmission cutoffs upon fault detection,
with anti-replay, epoch-tracked, and timing-safe challenge-response cryptographic reset.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from src.core.errors import SafetyError
from src.core.logging import get_logger
from src.safety.secret_provider import (
    EphemeralSecretBackend,
    SecretProvider,
    get_default_secret_provider,
)

if TYPE_CHECKING:
    pass

logger = get_logger("safety.estop")

DEFAULT_ESTOP_KEY_NAME: str = "ESTOP_HMAC_SECRET"
DEFAULT_TOKEN_MAX_AGE_NS: int = 30_000_000_000  # 30 seconds

# Token input bounds (fail-closed, memory/CPU DoS hardening).
MAX_TOKEN_STRING_LEN: int = 512
NONCE_HEX_LEN: int = 32  # 16 bytes -> 32 hex chars
SIG_HEX_LEN: int = 64  # SHA-256 -> 64 hex chars
ALLOWED_TOKEN_ACTIONS: frozenset[str] = frozenset({"ESTOP_RESET"})
MAX_TOKEN_EPOCH: int = 2**63 - 1
MAX_TOKEN_TIMESTAMP_NS: int = 2**63 - 1
# Failed-reset backoff: exponential, capped.
RESET_BACKOFF_BASE_SEC: float = 0.2
RESET_BACKOFF_CAP_SEC: float = 5.0


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


class EStopTriggerSource(Enum):
    """10 Distinct E-Stop Triggers."""

    USER_UI_BUTTON = "USER_UI_BUTTON"
    BUS_OFF_DETECTED = "BUS_OFF_DETECTED"
    KEEPALIVE_TIMEOUT = "KEEPALIVE_TIMEOUT"
    SPEED_INTERLOCK_BREACH = "SPEED_INTERLOCK_BREACH"
    HARDWARE_DISCONNECT = "HARDWARE_DISCONNECT"
    RATE_LIMIT_OVERFLOW = "RATE_LIMIT_OVERFLOW"
    UNAUTHORIZED_PAYLOAD = "UNAUTHORIZED_PAYLOAD"
    TEMPERATURE_OVERHEAT = "TEMPERATURE_OVERHEAT"
    PROCESS_TERMINATION = "PROCESS_TERMINATION"
    COMMUNICATION_TIMEOUT = "COMMUNICATION_TIMEOUT"


@dataclass(slots=True)
class EStopEvent:
    """Recorded E-Stop engagement event."""

    trigger: EStopTriggerSource
    reason: str
    timestamp_ns: int
    system_speed_kmh: float = 0.0


@dataclass(slots=True, frozen=True)
class EStopChallenge:
    """Cryptographic challenge issued upon E-Stop engagement."""

    epoch: int
    nonce: bytes
    timestamp_monotonic_ns: int
    action: str = "ESTOP_RESET"
    max_age_ns: int = DEFAULT_TOKEN_MAX_AGE_NS
    # Wall-clock capture for audit correlation only — never used in TTL or
    # signature math (monotonic clock is authoritative there).
    timestamp_wall_ns: int = 0

    def serialize_for_signature(self) -> bytes:
        """Deterministic serialization for HMAC computation."""
        return f"{self.epoch}:{self.nonce.hex()}:{self.timestamp_monotonic_ns}:{self.action}".encode("utf-8")


@dataclass(slots=True, frozen=True)
class EmergencyStopToken:
    """Structured cryptographic authorization token for E-Stop reset."""

    epoch: int
    nonce: str
    timestamp_monotonic_ns: int
    action: str
    signature: str

    def to_token_string(self) -> str:
        """Serialize token into canonical colon-separated representation."""
        return f"{self.epoch}:{self.nonce}:{self.timestamp_monotonic_ns}:{self.action}:{self.signature}"

    @classmethod
    def from_token_string(cls, token_str: str) -> EmergencyStopToken:
        """Parse token from canonical string format (fail-closed bounds)."""
        raw = token_str.strip()
        if len(raw) > MAX_TOKEN_STRING_LEN:
            raise ValueError(f"Token string exceeds {MAX_TOKEN_STRING_LEN} char limit ({len(raw)})")
        parts = raw.split(":")
        if len(parts) != 5:
            raise ValueError(f"Invalid token format, expected 5 colon-separated fields (got {len(parts)})")
        try:
            epoch = int(parts[0])
            nonce_hex = parts[1]
            ts = int(parts[2])
            action = parts[3]
            sig = parts[4]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Malformed token fields: {exc}") from exc
        if not (0 <= epoch <= MAX_TOKEN_EPOCH):
            raise ValueError(f"Token epoch out of range: {epoch}")
        if len(nonce_hex) != NONCE_HEX_LEN or not _is_hex(nonce_hex):
            raise ValueError(f"Token nonce must be {NONCE_HEX_LEN} hex chars")
        if not (0 <= ts <= MAX_TOKEN_TIMESTAMP_NS):
            raise ValueError(f"Token timestamp out of range: {ts}")
        if action not in ALLOWED_TOKEN_ACTIONS:
            raise ValueError(f"Token action not allowlisted: {action!r}")
        if len(sig) != SIG_HEX_LEN or not _is_hex(sig):
            raise ValueError(f"Token signature must be {SIG_HEX_LEN} hex chars")
        return cls(
            epoch=epoch,
            nonce=nonce_hex,
            timestamp_monotonic_ns=ts,
            action=action,
            signature=sig,
        )


class EmergencyStopSystem:
    """Master Emergency Stop controller ensuring immediate hardware/software TX cutoff.

    Integrates SecretProvider for dynamic key retrieval (zero hardcoded secrets),
    maintains an anti-replay store of consumed nonces, enforces monotonic TTL windows,
    and performs constant-time HMAC-SHA256 authorization verification.
    """

    # B9: replay window is bounded — nonces are 16 random bytes (128-bit) and a challenge
    # older than max_token_age_s can never verify again, so retaining far more
    # than a full window of recent nonces adds no protection, only memory.
    MAX_CONSUMED_NONCES: ClassVar[int] = 1024

    def __init__(
        self,
        reset_secret: bytes | None = None,
        secret_provider: SecretProvider | None = None,
        key_name: str = DEFAULT_ESTOP_KEY_NAME,
        max_token_age_s: float = 30.0,
        allow_self_reset: bool = False,
    ) -> None:
        self._key_name = key_name
        self._max_token_age_ns = int(max_token_age_s * 1_000_000_000)
        # P1-1: minting/verification separation — the enforcement object
        # refuses to mint reset tokens unless explicitly elevated. Only
        # EStopResetAuthority constructs (or flags) an instance with this
        # enabled; everything else (gateway wiring, UI hold, test fixtures)
        # operates verification-only.
        self._allow_self_reset = allow_self_reset

        if reset_secret is not None:
            self._secret_provider: SecretProvider = EphemeralSecretBackend({self._key_name: bytes(reset_secret)})
        elif secret_provider is not None:
            self._secret_provider = secret_provider
        else:
            self._secret_provider = get_default_secret_provider()

        # Ensure a valid key exists in the provider; if not, generate a dynamic 256-bit key
        self._protection_downgraded: bool = False
        if not self._secret_provider.has_secret(self._key_name):
            try:
                self._secret_provider.store_secret(self._key_name, os.urandom(32))
            except Exception as exc:
                # P17 (E-5): an ephemeral fallback means the reset secret is
                # process-local and does NOT survive a restart — the audit
                # trail/protection level silently weakens. Report at CRITICAL
                # (not WARNING) and expose the downgrade so the UI can banner it.
                self._protection_downgraded = True
                logger.critical(
                    "Failed to persist initial E-Stop secret — falling back to an "
                    "EPHEMERAL, process-local key (reset tokens will not survive a "
                    "restart; protection level downgraded)",
                    extra={"error": str(exc), "key_name": self._key_name},
                )
                self._secret_provider = EphemeralSecretBackend({self._key_name: os.urandom(32)})
        # P5 (E-2): resolve the HMAC secret ONCE at construction. `reset()` used
        # to call `_get_secret()` while holding `self._lock`, which runs file
        # I/O + AES-GCM/DPAPI decryption under the SAME lock that `trigger()`
        # and `is_engaged` need — a slow/hung secret store delayed E-Stop
        # engagement. The secret is cached here; `refresh_secret()` is the
        # explicit, controlled path for a key rotation.
        self._cached_secret: bytes | None = self._load_secret()
        # P17 (E-5) / B11: the provider version observed at the last successful
        # load, so a rotation performed through the provider (not through
        # `refresh_secret()`) is still detected by `_get_secret()`.
        self._cached_secret_version: int = self._provider_version()

        self._is_engaged = False
        self._last_event: EStopEvent | None = None
        self._callbacks: list[Callable[[EStopEvent], None]] = []
        self._active_challenge: EStopChallenge | None = None
        # B9: ordered dict preserves insertion (consumption) order so the
        # oldest nonce can be evicted when the window is full.
        self._consumed_nonces: "collections.OrderedDict[bytes, bool]" = collections.OrderedDict()
        self._epoch: int = 0
        # CRITICAL-1 (E-Stop TOCTOU): TX fence generation + dedicated send
        # lock. A frame may only be dispatched when the fence generation it
        # was validated against is STILL current at dispatch time, checked
        # while holding this lock — closing the check-then-send race window.
        self._tx_fence: int = 0
        self._tx_send_lock = threading.Lock()
        self._lock = threading.RLock()
        # Failed-reset hardening: consecutive failure counter + backoff deadline.
        self._failed_reset_attempts: int = 0
        self._reset_backoff_until_ns: int = 0
        # Abort/flush hooks: gateway registers a driver abort/flush callback
        # so trigger() can request HAL-level cancellation after fencing.
        self._abort_hooks: list[Callable[[], None]] = []

    @property
    def is_engaged(self) -> bool:
        """Return True if E-Stop is currently engaged and transmissions are blocked."""
        with self._lock:
            return self._is_engaged

    @property
    def last_event(self) -> EStopEvent | None:
        """Return the most recent EStopEvent if engaged."""
        with self._lock:
            return self._last_event

    @property
    def epoch(self) -> int:
        """Return current monotonically increasing state epoch counter."""
        with self._lock:
            return self._epoch

    @property
    def tx_fence(self) -> int:
        """Return current monotonic TX fence generation (CRITICAL-1).

        The generation advances on EVERY engagement and on EVERY authorized
        reset — any state transition a validated frame must not survive.
        Readers comparing a captured generation against the live one must do
        so while holding `tx_send_lock` (see `acquire_tx_fence`).
        """
        with self._lock:
            return self._tx_fence

    @property
    def tx_send_lock(self) -> threading.Lock:
        """Return the dedicated E-Stop TX send lock (CRITICAL-1).

        Gateway dispatch serializes on this leaf lock so the fenced
        re-verification and the bus write are atomic with respect to any
        engagement / reset state transition. It never nests inside the
        estop RLock or the gateway lock (deadlock-free leaf lock).
        """
        return self._tx_send_lock

    def advance_tx_fence(self, reason: str) -> None:
        """Advance the TX fence generation without engaging the E-Stop.

        REVIEW (bus rebind TOCTOU): a controlled HAL bus swap is a state
        transition that validated frames must not survive — a frame
        validated against the old channel must never dispatch onto the new
        one. Gateway rebind_bus calls this while holding tx_send_lock so the
        PHASE-3 fence re-check rejects in-flight frames fail-closed. The
        bump never takes tx_send_lock itself (leaf-lock discipline), so it
        is safe to call with the send lock already held.
        """
        with self._lock:
            self._tx_fence += 1
        logger.warning(
            "E-Stop TX fence advanced (non-engagement state transition)",
            extra={"reason": reason, "tx_fence": self._tx_fence},
        )

    @property
    def active_challenge(self) -> EStopChallenge | None:
        """Return the currently active cryptographic challenge."""
        with self._lock:
            return self._active_challenge

    @property
    def secret_provider(self) -> SecretProvider:
        """Return the bound SecretProvider instance."""
        return self._secret_provider

    def _load_secret(self) -> bytes | None:
        """Resolve the HMAC secret from the provider (I/O; call OUTSIDE `_lock`).

        P5 (E-2): split out of `_get_secret` so the expensive file I/O +
        decryption never runs while the E-Stop lock is held.
        """
        try:
            return self._secret_provider.get_secret(self._key_name)
        except Exception as exc:
            raise SafetyError(
                f"Failed to retrieve E-Stop HMAC secret: {exc}",
                code="ESTOP_RESET_DENIED",
                cause=exc,
            ) from exc

    def _provider_version(self) -> int:
        """Revision counter of the bound SecretProvider (P5 / E-2 rotation probe).

        Providers that expose their mutation revision (`revision`) are tracked so
        a key rotation performed out-of-band is still noticed by `_get_secret()`.
        A provider without the attribute is treated as version 0 — the cache is
        then only refreshed by the explicit `refresh_secret()` path.
        """
        return int(getattr(self._secret_provider, "revision", 0))

    def _get_secret(self) -> bytes:
        """Return the cached HMAC secret (leaf operation, safe under `_lock`).

        P5 (E-2): the provider I/O happened once in __init__ (`_load_secret`);
        `reset()` now only reads a cached bytes object while holding the lock,
        so a slow/hung secret store can no longer delay `trigger()` /
        `is_engaged`.

        Rotation safety: if the provider advertises a changed `revision`, the
        secret is re-read EXACTLY ONCE under the lock, so a rotated key still
        invalidates tokens minted with the old one. In the steady state (no
        rotation) no provider I/O happens while the lock is held.
        """
        secret = self._cached_secret
        if secret is None or self._provider_version() != self._cached_secret_version:
            secret = self._load_secret()
            self._cached_secret = secret
            self._cached_secret_version = self._provider_version()
        if secret is None:
            raise SafetyError(
                "E-Stop HMAC secret is not loaded — call refresh_secret()",
                code="ESTOP_RESET_DENIED",
            )
        return secret

    def refresh_secret(self) -> bytes:
        """Re-read the HMAC secret from the provider (explicit key rotation).

        Performs the provider I/O OUTSIDE the E-Stop lock and atomically swaps
        the cached value, so rotation never widens the lock-hold window.
        """
        fresh = self._load_secret()
        with self._lock:
            self._cached_secret = fresh
            self._cached_secret_version = self._provider_version()
        return fresh

    @property
    def protection_downgraded(self) -> bool:
        """True when the E-Stop secret fell back to a process-local key (P17)."""
        return self._protection_downgraded

    def reset_authority_provider(self, key_name: str = DEFAULT_ESTOP_KEY_NAME) -> SecretProvider:
        """P4 (E-1): an INDEPENDENT provider for `EStopResetAuthority`.

        The authority must not be able to fall back to the enforcement
        object's provider (that fallback made the ISO 26262 mint/verify
        separation nominal). This returns a fresh in-process provider seeded
        with a copy of the current secret so the authority can still mint a
        verifiable challenge response, without sharing the enforcement store.
        """
        return EphemeralSecretBackend({key_name: bytes(self._get_secret())})

    def get_reset_nonce(self) -> bytes:
        """Return the single-use cryptographic challenge nonce for the current E-Stop engagement."""
        with self._lock:
            if self._active_challenge is not None:
                return self._active_challenge.nonce
            return b""

    def get_active_challenge(self) -> EStopChallenge | None:
        """Return the active challenge record."""
        with self._lock:
            return self._active_challenge

    def reissue_challenge(self) -> EStopChallenge:
        """Reissue a fresh cryptographic challenge if the previous one expired or was cleared."""
        with self._lock:
            if not self._is_engaged:
                raise SafetyError("Cannot issue E-Stop challenge when not engaged", code="ESTOP_NOT_ENGAGED")
            self._active_challenge = EStopChallenge(
                epoch=self._epoch,
                nonce=os.urandom(16),
                timestamp_monotonic_ns=time.monotonic_ns(),
                timestamp_wall_ns=time.time_ns(),
                action="ESTOP_RESET",
                max_age_ns=self._max_token_age_ns,
            )
            return self._active_challenge

    def request_reset_challenge(self) -> EStopChallenge | None:
        """Return currently active challenge if valid, or reissue a fresh one if engaged."""
        with self._lock:
            if not self._is_engaged:
                return None
            now_ns = time.monotonic_ns()
            if (
                self._active_challenge is None
                or (now_ns - self._active_challenge.timestamp_monotonic_ns) > self._active_challenge.max_age_ns
            ):
                return self.reissue_challenge()
            return self._active_challenge

    def create_reset_token(self) -> EmergencyStopToken | None:
        """Generate a valid, signed EmergencyStopToken for the currently active challenge.

        P1-1 (self-signing separation): token MINTING is an authorization
        operation and no longer available on the enforcement object by
        default. It must go through `EStopResetAuthority`, which is the only
        component configured with `allow_self_reset=True`. Any code holding
        a plain `EmergencyStopSystem` reference (gateway, flasher, protocol
        engines, a buggy retry loop) cannot forge a reset token anymore.
        """
        with self._lock:
            if not self._allow_self_reset:
                raise SafetyError(
                    "Token minting is denied on this EmergencyStopSystem instance — "
                    "route reset authorization through EStopResetAuthority",
                    code="ESTOP_MINT_DENIED",
                )
            if not self._is_engaged:
                return None

            now_ns = time.monotonic_ns()
            if self._active_challenge is None or (now_ns - self._active_challenge.timestamp_monotonic_ns) > self._active_challenge.max_age_ns:
                self.reissue_challenge()

            challenge = self._active_challenge
            if challenge is None:
                return None

            secret = self._get_secret()
            sig = hmac.new(secret, challenge.serialize_for_signature(), hashlib.sha256).hexdigest()

            return EmergencyStopToken(
                epoch=challenge.epoch,
                nonce=challenge.nonce.hex(),
                timestamp_monotonic_ns=challenge.timestamp_monotonic_ns,
                action=challenge.action,
                signature=sig,
            )

    def compute_reset_token(
        self,
        nonce: bytes | str | None = None,
        epoch: int | None = None,
        timestamp_ns: int | None = None,
        action: str = "ESTOP_RESET",
    ) -> str:
        """Compute expected token string for authorization.

        If called with an active challenge, returns a canonical structured token string
        or computed signature compatible with reset().

        P1-1: same authority gate as create_reset_token.
        """
        with self._lock:
            if not self._allow_self_reset:
                raise SafetyError(
                    "Token computation is denied on this EmergencyStopSystem instance — "
                    "route reset authorization through EStopResetAuthority",
                    code="ESTOP_MINT_DENIED",
                )
            secret = self._get_secret()

            # B11: when a live challenge exists, create_reset_token() already
            # answers the "engaged + challenge present" precondition — a
            # None re-check here was dead code (the outer challenge guard
            # guarantees the precondition).
            if self._active_challenge is not None:
                token_obj = self.create_reset_token()
                return token_obj.to_token_string() if token_obj is not None else ""

            if nonce is None:
                return ""

            nonce_bytes = nonce if isinstance(nonce, bytes) else bytes.fromhex(nonce)
            if not nonce_bytes:
                return ""

            if epoch is not None and timestamp_ns is not None:
                payload = f"{epoch}:{nonce_bytes.hex()}:{timestamp_ns}:{action}".encode("utf-8")
                sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
                return f"{epoch}:{nonce_bytes.hex()}:{timestamp_ns}:{action}:{sig}"

            return hmac.new(secret, nonce_bytes, hashlib.sha256).hexdigest()

    def register_callback(self, callback: Callable[[EStopEvent], None]) -> None:
        """Register listener to be invoked immediately upon E-Stop trigger."""
        with self._lock:
            self._callbacks.append(callback)

    def register_abort_hook(self, hook: Callable[[], None]) -> None:
        """Register a driver abort/flush hook invoked after fence publication on trigger().

        Gateway wires its HAL abort/flush here so an engagement can request
        driver-queue cancellation. Hooks run outside locks with isolation.
        """
        with self._lock:
            self._abort_hooks.append(hook)

    def trigger(
        self,
        trigger: EStopTriggerSource,
        reason: str,
        vehicle_speed_kmh: float = 0.0,
    ) -> None:
        """Engage Emergency Stop immediately and cut all transmissions."""
        with self._lock:
            now_wall_ns = time.time_ns()
            now_monotonic_ns = time.monotonic_ns()

            already_engaged = self._is_engaged
            self._is_engaged = True

            # If not already engaged or challenge is missing/expired, issue fresh challenge
            if (
                not already_engaged
                or self._active_challenge is None
                or (now_monotonic_ns - self._active_challenge.timestamp_monotonic_ns) > self._active_challenge.max_age_ns
            ):
                challenge_nonce = os.urandom(16)
                self._active_challenge = EStopChallenge(
                    epoch=self._epoch,
                    nonce=challenge_nonce,
                    timestamp_monotonic_ns=now_monotonic_ns,
                    timestamp_wall_ns=now_wall_ns,
                    action="ESTOP_RESET",
                    max_age_ns=self._max_token_age_ns,
                )
            self._last_event = EStopEvent(
                trigger=trigger,
                reason=reason,
                timestamp_ns=now_wall_ns,
                system_speed_kmh=vehicle_speed_kmh,
            )
            # CRITICAL-1: every engagement invalidates all in-flight validated
            # frames. The fence bump stays under the estop _lock and MUST NOT
            # take tx_send_lock: a dispatch may block holding tx_send_lock
            # inside driver I/O (see gateway test
            # test_estop_callback_does_not_block_on_slow_driver_io) — if
            # trigger() waited on tx_send_lock, the engagement itself would
            # deadlock behind the in-flight send and the E-Stop callback
            # would never fire. Coordination with the send path is via (a)
            # the PHASE-3 fenced re-check in the gateway (any COMPLETED
            # transition bumps the generation and kills validated frames
            # still waiting for the fence) and (b) abort/flush hooks below
            # that request HAL queue cancellation for the already-dispatched
            # frame. Residual window (engagement landing between fence check
            # and bus write) is documented at the gateway PHASE-3 site.
            self._tx_fence += 1
            event_snapshot = self._last_event
            callbacks_snapshot = list(self._callbacks)
            abort_snapshot = list(self._abort_hooks)

        logger.critical(
            "EMERGENCY STOP ENGAGED",
            extra={
                "trigger": trigger.value,
                "reason": reason,
                "speed_kmh": vehicle_speed_kmh,
                "epoch": self._epoch,
            },
        )

        # Notify all listeners outside lock with exception isolation
        for cb in callbacks_snapshot:
            try:
                cb(event_snapshot)
            except Exception as exc:
                logger.error("Error in E-Stop callback", extra={"error": str(exc)})
        # Driver abort/flush hooks: request HAL queue cancellation outside locks.
        for hook in abort_snapshot:
            try:
                hook()
            except Exception as exc:
                logger.error("Error in E-Stop abort hook", extra={"error": str(exc)})

    @staticmethod
    def _looks_well_formed_signature(sig: str) -> bool:
        """Cheap shape check for a submitted reset signature (P14 / E-3).

        Only the canonical 64-hex-char SHA-256 digest can possibly verify, so a
        shape-mismatched input can be rejected without the HMAC. This is NOT a
        credential check.
        """
        candidate = sig.strip() if isinstance(sig, str) else ""
        return len(candidate) == SIG_HEX_LEN and _is_hex(candidate)

    def _reset_failure(self, reason: str, sig_prefix: str = "") -> SafetyError:
        """Record a failed reset: counter + exponential backoff + audit log.

        The first consecutive failure does NOT arm the cooldown (tolerates a
        single operator typo / stale-token retry); persistence (>=2) is
        throttled with exponential backoff capped at RESET_BACKOFF_CAP_SEC.
        Sustained online guessing is still rate-limited; a lone mistake is not
        punished with a lockout.
        """
        self._failed_reset_attempts += 1
        backoff_sec = 0.0
        if self._failed_reset_attempts >= 2:
            # Cap the exponent BEFORE shifting: 0.2 * 2**5 = 6.4 > 5.0 cap, so
            # shifts beyond 5 never change the result but would overflow float
            # (and int->float conversion) under hammering.
            shift = min(self._failed_reset_attempts - 2, 5)
            backoff_sec = min(RESET_BACKOFF_BASE_SEC * (2**shift), RESET_BACKOFF_CAP_SEC)
            self._reset_backoff_until_ns = time.monotonic_ns() + int(backoff_sec * 1_000_000_000)
        level = "CRITICAL" if self._failed_reset_attempts >= 5 else "WARNING"
        if level == "CRITICAL":
            logger.critical(
                "E-Stop reset denied (repeated failures)",
                extra={
                    "reason": reason,
                    "attempts": self._failed_reset_attempts,
                    "backoff_sec": round(backoff_sec, 3),
                    "sig_prefix": sig_prefix[:8],
                    "epoch": self._epoch,
                },
            )
        else:
            logger.warning(
                "E-Stop reset denied",
                extra={
                    "reason": reason,
                    "attempts": self._failed_reset_attempts,
                    "backoff_sec": round(backoff_sec, 3),
                    "sig_prefix": sig_prefix[:8],
                    "epoch": self._epoch,
                },
            )
        return SafetyError(reason, code="ESTOP_RESET_DENIED")

    @property
    def failed_reset_attempts(self) -> int:
        """Consecutive failed reset counter (reset on success)."""
        with self._lock:
            return self._failed_reset_attempts

    def reset(self, authorization_token: str | EmergencyStopToken) -> None:
        """Manual reset of E-Stop requiring a valid replay-protected challenge-response token."""
        # Phase 1: verify + mutate engagement state under _lock (no tx_send_lock nesting).
        needs_fence_bump = False
        with self._lock:
            if not self._is_engaged:
                return  # No-op when not engaged

            if self._active_challenge is None:
                raise self._reset_failure("No active E-Stop challenge available")

            challenge = self._active_challenge
            now_monotonic_ns = time.monotonic_ns()

            # 1. Parse token input (fail-closed bounds; raw strings capped too).
            token_epoch: int
            token_nonce_hex: str
            token_ts: int
            token_action: str
            sig: str

            if isinstance(authorization_token, EmergencyStopToken):
                token_epoch = authorization_token.epoch
                token_nonce_hex = authorization_token.nonce
                token_ts = authorization_token.timestamp_monotonic_ns
                token_action = authorization_token.action
                sig = authorization_token.signature
                # Field bounds for object path (same limits as string parsing).
                if not (0 <= token_epoch <= MAX_TOKEN_EPOCH):
                    raise self._reset_failure(f"Token epoch out of range: {token_epoch}", sig)
                if len(token_nonce_hex) != NONCE_HEX_LEN or not _is_hex(token_nonce_hex):
                    raise self._reset_failure("E-Stop token nonce mismatch", token_nonce_hex)
                if not (0 <= token_ts <= MAX_TOKEN_TIMESTAMP_NS):
                    raise self._reset_failure("E-Stop reset token timestamp expired", sig)
                if token_action not in ALLOWED_TOKEN_ACTIONS:
                    raise self._reset_failure("Invalid E-Stop token action", sig)
                if len(sig) != SIG_HEX_LEN or not _is_hex(sig):
                    raise self._reset_failure("Invalid E-Stop reset token", sig)
            elif isinstance(authorization_token, str):
                token_str = authorization_token.strip()
                if not token_str:
                    raise self._reset_failure("Invalid E-Stop reset token")
                if len(token_str) > MAX_TOKEN_STRING_LEN:
                    raise self._reset_failure(
                        f"E-Stop reset token exceeds {MAX_TOKEN_STRING_LEN} char limit",
                        token_str,
                    )

                if ":" in token_str:
                    try:
                        parsed = EmergencyStopToken.from_token_string(token_str)
                        token_epoch = parsed.epoch
                        token_nonce_hex = parsed.nonce
                        token_ts = parsed.timestamp_monotonic_ns
                        token_action = parsed.action
                        sig = parsed.signature
                    except ValueError as exc:
                        raise self._reset_failure(
                            f"Malformed E-Stop reset token structure: {exc}",
                            token_str,
                        ) from exc
                else:
                    # Raw signature submitted against current active challenge
                    if len(token_str) > MAX_TOKEN_STRING_LEN:
                        raise self._reset_failure("E-Stop reset token exceeds length limit", token_str)
                    token_epoch = challenge.epoch
                    token_nonce_hex = challenge.nonce.hex()
                    token_ts = challenge.timestamp_monotonic_ns
                    token_action = challenge.action
                    sig = token_str
            else:
                raise self._reset_failure(
                    f"Unsupported token type: {type(authorization_token).__name__}",
                )

            # 2. Anti-Replay: Check if nonce was already consumed
            if challenge.nonce in self._consumed_nonces:
                raise self._reset_failure(
                    "E-Stop token nonce has already been consumed (Replay Attack)",
                    sig,
                )

            # 3. Epoch verification
            if token_epoch != challenge.epoch or token_epoch != self._epoch:
                raise self._reset_failure(
                    "E-Stop token epoch mismatch (Replay or Stale Trigger)",
                    sig,
                )

            # 4. Action verification
            if token_action != "ESTOP_RESET":
                raise self._reset_failure("Invalid E-Stop token action", sig)

            # 5. Nonce match verification
            if token_nonce_hex.lower() != challenge.nonce.hex().lower():
                raise self._reset_failure("E-Stop token nonce mismatch", sig)

            # 6. TTL / Expiration Check (using monotonic clock)
            age_ns = now_monotonic_ns - challenge.timestamp_monotonic_ns
            if age_ns > challenge.max_age_ns or age_ns < 0:
                self._active_challenge = None
                raise self._reset_failure("E-Stop reset token expired", sig)

            token_age_ns = now_monotonic_ns - token_ts
            if token_age_ns > challenge.max_age_ns or token_age_ns < 0:
                self._active_challenge = None
                raise self._reset_failure("E-Stop reset token timestamp expired", sig)

            # 7. Constant-Time HMAC Signature Verification
            #
            # P14 (E-3): the backoff used to be consulted only AFTER the full
            # HMAC was computed, so it relabelled the error without saving any
            # CPU. A cheap shape/credential gate now runs first: while the
            # cooldown is armed, a submitted signature that is NOT a
            # well-shaped hex credential matching the current challenge
            # structure cannot possibly be valid, so it is rejected here.
            #
            # The gate is deliberately NOT a credential check: a VALID operator
            # token presented during the cooldown still reaches the HMAC below
            # and is accepted (a real credential is never punished by a lockout
            # — otherwise an attacker could DoS the operator). Only the
            # expensive HMAC for garbage input is skipped.
            if (
                now_monotonic_ns < self._reset_backoff_until_ns
                and not self._looks_well_formed_signature(sig)
            ):
                raise self._reset_failure("E-Stop reset rate-limited (backoff active)", sig)

            secret = self._get_secret()
            structured_payload = challenge.serialize_for_signature()
            expected_sig_bytes = hmac.new(secret, structured_payload, hashlib.sha256).digest()

            try:
                sig_bytes = bytes.fromhex(sig.strip())
            except ValueError as exc:
                raise self._reset_failure(
                    f"Invalid E-Stop reset token signature format: {exc}",
                    sig,
                ) from exc

            is_valid = hmac.compare_digest(sig_bytes, expected_sig_bytes)

            if is_valid:
                pass  # valid operator credential bypasses the guess-throttle below
            elif now_monotonic_ns < self._reset_backoff_until_ns:
                raise self._reset_failure("E-Stop reset rate-limited (backoff active)", sig)
            else:
                raise self._reset_failure("Invalid E-Stop reset token", sig)

            # 8. Success: Consume nonce, advance epoch, disengage E-Stop
            self._record_consumed_nonce(challenge.nonce)
            self._is_engaged = False
            self._active_challenge = None
            self._epoch += 1
            self._failed_reset_attempts = 0
            self._reset_backoff_until_ns = 0
            success_epoch = self._epoch
            needs_fence_bump = True

        # Phase 2: fence publication under tx_send_lock only (consistent ordering).
        if needs_fence_bump:
            with self._tx_send_lock:
                with self._lock:
                    self._tx_fence += 1
            logger.warning(
                "Emergency Stop successfully reset by authorized operator",
                extra={"epoch": success_epoch},
            )

    def _record_consumed_nonce(self, nonce: bytes) -> None:
        """Add a nonce to the replay store, evicting oldest beyond the cap.

        B9: challenges older than max_token_age_ns are already TTL-rejected,
        so a nonce that fell off the window can never verify again — the
        eviction never re-opens a live replay window.
        """
        self._consumed_nonces[nonce] = True
        while len(self._consumed_nonces) > self.MAX_CONSUMED_NONCES:
            self._consumed_nonces.popitem(last=False)  # evict oldest


class EStopResetAuthority:
    """Separate authorization component that mints E-Stop reset tokens (P1-1).

    ISO 26262 independence: the component that ENFORCES the E-Stop
    (`EmergencyStopSystem`) does not mint the credential that clears it.
    This authority holds its own access to the SecretProvider key and
    signs challenges directly. The shared enforcement object is NEVER elevated.

    P4 (E-1): the previous implementation defaulted `secret_provider` to
    `estop._secret_provider`, so the "independent" authority transparently
    reused the enforcement object's store and the separation was nominal.
    The provider is now a REQUIRED, distinct argument; sharing the enforcement
    provider fails closed with `ESTOP_AUTHORITY_NOT_INDEPENDENT`.
    """

    def __init__(
        self,
        estop: EmergencyStopSystem,
        secret_provider: SecretProvider,
        key_name: str = DEFAULT_ESTOP_KEY_NAME,
    ) -> None:
        if secret_provider is None:
            raise SafetyError(
                "EStopResetAuthority requires an independent secret_provider "
                "(the enforcement object's provider must not be reused)",
                code="ESTOP_AUTHORITY_NOT_INDEPENDENT",
            )
        if secret_provider is estop._secret_provider:
            raise SafetyError(
                "EStopResetAuthority must not share the enforcement object's "
                "SecretProvider — mint/verify separation would be nominal",
                code="ESTOP_AUTHORITY_NOT_INDEPENDENT",
            )
        self._estop = estop
        self._key_name = key_name
        self._secret_provider = secret_provider

    @property
    def estop(self) -> EmergencyStopSystem:
        """The enforcement object this authority may mint tokens for."""
        return self._estop

    @property
    def reset_secret(self) -> bytes:
        """Retrieve the HMAC signing secret from the bound SecretProvider."""
        return self._secret_provider.get_secret(self._key_name)

    @property
    def secret_provider(self) -> SecretProvider:
        """The bound SecretProvider instance."""
        return self._secret_provider

    def mint_reset_token(self) -> EmergencyStopToken | None:
        """Mint a fresh, signed reset token for the active challenge.

        Mirrors the operator-driven flow: challenge is read from the enforcement
        object, and this authority signs it using its distinct capability.
        """
        challenge = self._estop.request_reset_challenge()
        if challenge is None:
            return None

        secret = self._secret_provider.get_secret(self._key_name)
        sig = hmac.new(secret, challenge.serialize_for_signature(), hashlib.sha256).hexdigest()

        return EmergencyStopToken(
            epoch=challenge.epoch,
            nonce=challenge.nonce.hex(),
            timestamp_monotonic_ns=challenge.timestamp_monotonic_ns,
            action=challenge.action,
            signature=sig,
        )

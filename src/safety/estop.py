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
        """Parse token from canonical string format."""
        parts = token_str.strip().split(":")
        if len(parts) != 5:
            raise ValueError(f"Invalid token format, expected 5 colon-separated fields (got {len(parts)})")
        try:
            return cls(
                epoch=int(parts[0]),
                nonce=parts[1],
                timestamp_monotonic_ns=int(parts[2]),
                action=parts[3],
                signature=parts[4],
            )
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Malformed token fields: {exc}") from exc


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
        if not self._secret_provider.has_secret(self._key_name):
            try:
                self._secret_provider.store_secret(self._key_name, os.urandom(32))
            except Exception as exc:
                logger.warning(
                    "Failed to persist initial E-Stop secret, using ephemeral fallback",
                    extra={"error": str(exc)},
                )
                self._secret_provider = EphemeralSecretBackend({self._key_name: os.urandom(32)})

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

    @property
    def active_challenge(self) -> EStopChallenge | None:
        """Return the currently active cryptographic challenge."""
        with self._lock:
            return self._active_challenge

    @property
    def secret_provider(self) -> SecretProvider:
        """Return the bound SecretProvider instance."""
        return self._secret_provider

    @property
    def reset_secret(self) -> bytes:
        """Retrieve current binary HMAC secret from SecretProvider.

        P1-1: the enforcement object no longer hands out the signing
        secret — possession of it is equivalent to reset authority.
        Only EStopResetAuthority (which shares the provider) may resolve it.
        """
        raise SafetyError(
            "The E-Stop signing secret is not exposed on the enforcement "
            "object (P1-1). Use EStopResetAuthority.",
            code="ESTOP_SECRET_DENIED",
        )

    @reset_secret.setter
    def reset_secret(self, secret: bytes) -> None:
        """Update binary HMAC secret in SecretProvider."""
        with self._lock:
            self._secret_provider.store_secret(self._key_name, bytes(secret))

    def _get_secret(self) -> bytes:
        """Internal secret resolver."""
        try:
            return self._secret_provider.get_secret(self._key_name)
        except Exception as exc:
            raise SafetyError(
                f"Failed to retrieve E-Stop HMAC secret: {exc}",
                code="ESTOP_RESET_DENIED",
                cause=exc,
            ) from exc

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
            # frames — the fence generation advances atomically with the
            # engagement under the estop lock.
            self._tx_fence += 1
            event_snapshot = self._last_event
            callbacks_snapshot = list(self._callbacks)

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

    def reset(self, authorization_token: str | EmergencyStopToken) -> None:
        """Manual reset of E-Stop requiring a valid replay-protected challenge-response token."""
        with self._lock:
            if not self._is_engaged:
                return  # No-op when not engaged

            if self._active_challenge is None:
                raise SafetyError("No active E-Stop challenge available", code="ESTOP_RESET_DENIED")

            challenge = self._active_challenge
            now_monotonic_ns = time.monotonic_ns()

            # 1. Parse token input
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
            elif isinstance(authorization_token, str):
                token_str = authorization_token.strip()
                if not token_str:
                    raise SafetyError("Invalid E-Stop reset token", code="ESTOP_RESET_DENIED")

                if ":" in token_str:
                    try:
                        parsed = EmergencyStopToken.from_token_string(token_str)
                        token_epoch = parsed.epoch
                        token_nonce_hex = parsed.nonce
                        token_ts = parsed.timestamp_monotonic_ns
                        token_action = parsed.action
                        sig = parsed.signature
                    except ValueError as exc:
                        raise SafetyError(
                            f"Malformed E-Stop reset token structure: {exc}",
                            code="ESTOP_RESET_DENIED",
                            cause=exc,
                        ) from exc
                else:
                    # Raw signature submitted against current active challenge
                    token_epoch = challenge.epoch
                    token_nonce_hex = challenge.nonce.hex()
                    token_ts = challenge.timestamp_monotonic_ns
                    token_action = challenge.action
                    sig = token_str
            else:
                raise SafetyError(
                    f"Unsupported token type: {type(authorization_token).__name__}",
                    code="ESTOP_RESET_DENIED",
                )

            # 2. Anti-Replay: Check if nonce was already consumed
            if challenge.nonce in self._consumed_nonces:
                raise SafetyError(
                    "E-Stop token nonce has already been consumed (Replay Attack)",
                    code="ESTOP_RESET_DENIED",
                )

            # 3. Epoch verification
            if token_epoch != challenge.epoch or token_epoch != self._epoch:
                raise SafetyError(
                    "E-Stop token epoch mismatch (Replay or Stale Trigger)",
                    code="ESTOP_RESET_DENIED",
                )

            # 4. Action verification
            if token_action != "ESTOP_RESET":
                raise SafetyError("Invalid E-Stop token action", code="ESTOP_RESET_DENIED")

            # 5. Nonce match verification
            if token_nonce_hex.lower() != challenge.nonce.hex().lower():
                raise SafetyError("E-Stop token nonce mismatch", code="ESTOP_RESET_DENIED")

            # 6. TTL / Expiration Check (using monotonic clock)
            age_ns = now_monotonic_ns - challenge.timestamp_monotonic_ns
            if age_ns > challenge.max_age_ns or age_ns < 0:
                self._active_challenge = None
                raise SafetyError("E-Stop reset token expired", code="ESTOP_RESET_DENIED")

            token_age_ns = now_monotonic_ns - token_ts
            if token_age_ns > challenge.max_age_ns or token_age_ns < 0:
                self._active_challenge = None
                raise SafetyError("E-Stop reset token timestamp expired", code="ESTOP_RESET_DENIED")

            # 7. Constant-Time HMAC Signature Verification
            # P1-11: parse to bytes FIRST — hmac.compare_digest rejects
            # non-ASCII str inputs with a bare TypeError (crashing the UI
            # reset flow); a malformed hex signature must surface as a
            # SafetyError instead. Comparing bytes also removes the .lower()
            # dance entirely.
            secret = self._get_secret()
            structured_payload = challenge.serialize_for_signature()
            expected_sig_bytes = hmac.new(secret, structured_payload, hashlib.sha256).digest()

            try:
                sig_bytes = bytes.fromhex(sig.strip())
            except ValueError as exc:
                raise SafetyError(
                    f"Invalid E-Stop reset token signature format: {exc}",
                    code="ESTOP_RESET_DENIED",
                    cause=exc,
                ) from exc

            is_valid = hmac.compare_digest(sig_bytes, expected_sig_bytes)

            if not is_valid:
                raise SafetyError("Invalid E-Stop reset token", code="ESTOP_RESET_DENIED")

            # 8. Success: Consume nonce, advance epoch, disengage E-Stop
            # B9: bounded replay window — see _record_consumed_nonce for why
            # eviction is safe against replay.
            self._record_consumed_nonce(challenge.nonce)
            self._is_engaged = False
            # B10: keep the engagement audit record — only the challenge state
            # is cleared; last_event remains the "why did we stop" evidence.
            self._active_challenge = None
            self._epoch += 1
            # CRITICAL-1: an authorized reset is also a TX state transition —
            # any frame validated before the reset (against the engaged or
            # the pre-engagement generation) must not be dispatched after it.
            self._tx_fence += 1
            logger.warning(
                "Emergency Stop successfully reset by authorized operator",
                extra={"epoch": self._epoch},
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
    """

    def __init__(
        self,
        estop: EmergencyStopSystem,
        secret_provider: SecretProvider | None = None,
        key_name: str = DEFAULT_ESTOP_KEY_NAME,
    ) -> None:
        self._estop = estop
        self._key_name = key_name
        self._secret_provider = secret_provider or estop._secret_provider

    @property
    def estop(self) -> EmergencyStopSystem:
        """The enforcement object this authority may mint tokens for."""
        return self._estop

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

    def compute_reset_token(
        self,
        nonce: bytes | str | None = None,
        epoch: int | None = None,
        timestamp_ns: int | None = None,
        action: str = "ESTOP_RESET",
    ) -> str:
        """Authorization helper producing a valid reset token string or signature."""
        secret = self._secret_provider.get_secret(self._key_name)
        if nonce is None:
            token = self.mint_reset_token()
            return token.to_token_string() if token is not None else ""

        nonce_bytes = nonce.encode("utf-8") if isinstance(nonce, str) else (nonce or b"")
        return hmac.new(secret, nonce_bytes, hashlib.sha256).hexdigest()

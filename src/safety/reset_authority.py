"""E-Stop reset authority helper.

Provides a minimal EStopResetAuthority that can mint reset tokens using the
shared SecretProvider without exposing the raw secret from the enforcement
(EmergencyStopSystem) object.

This file implements the recommended pattern: authority objects hold a
reference to the SecretProvider (or an elevated EmergencyStopSystem that
shares the provider) and perform signing operations. The enforcement path
does not expose the raw secret and instead verifies tokens.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from src.safety.estop import DEFAULT_ESTOP_KEY_NAME, EmergencyStopToken, EStopChallenge
from src.safety.secret_provider import SecretProvider, get_default_secret_provider


class EStopResetAuthority:
    """Authority that mints E-Stop reset tokens.

    Usage:
        authority = EStopResetAuthority(secret_provider)
        token = authority.mint_reset_token(challenge)

    The implementation uses HMAC-SHA256 and returns an EmergencyStopToken
    compatible with the enforcement verifier in EmergencyStopSystem.
    """

    def __init__(self, secret_provider: Optional[SecretProvider] = None, key_name: str = DEFAULT_ESTOP_KEY_NAME) -> None:
        self._secret_provider = secret_provider or get_default_secret_provider()
        self._key_name = key_name

    def _get_secret_bytes(self) -> bytes:
        """Retrieve secret bytes from the provider.

        Try a few common method names on SecretProvider to be robust to
        small API differences (get_secret / retrieve / resolve). If none are
        present, raise AttributeError.
        """
        provider = self._secret_provider
        for meth in ("get_secret", "retrieve_secret", "resolve_secret", "secret_bytes"):
            fn = getattr(provider, meth, None)
            if callable(fn):
                return fn(self._key_name)

        # fallback: some providers expose a mapping-like interface
        if hasattr(provider, "__getitem__"):
            try:
                return provider[self._key_name]
            except Exception:
                pass

        raise AttributeError("SecretProvider has no known getter method for secrets")

    def mint_reset_token(self, challenge: EStopChallenge) -> EmergencyStopToken:
        """Sign a challenge and return an EmergencyStopToken.

        The signature is HMAC-SHA256(secret, challenge.serialize_for_signature()).
        The nonce in the token is serialized as hex to match canonical form.
        """
        secret = self._get_secret_bytes()
        sig = hmac.new(secret, challenge.serialize_for_signature(), hashlib.sha256).hexdigest()
        return EmergencyStopToken(
            epoch=challenge.epoch,
            nonce=challenge.nonce.hex(),
            timestamp_monotonic_ns=challenge.timestamp_monotonic_ns,
            action=challenge.action,
            signature=sig,
        )

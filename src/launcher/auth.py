"""Launcher Authentication & License Activation Manager.

Interacts with Universal-CAN-Cloud API, registers hardware fingerprint (HWID),
and securely stores/validates cryptographic Ed25519 license tokens in Windows DPAPI.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.logging import get_logger
from src.safety.secret_provider import SecretProvider, get_default_secret_provider
from src.security.cloud.client import CloudClient, CloudConfig
from src.security.cloud.license_flow import (
    DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64,
    CloudLicenseClaims,
    LicenseFlow,
)
from src.security.hwid.collector import generate_hardware_fingerprint

logger = get_logger("launcher.auth")


@dataclass(slots=True, frozen=True)
class AuthStatus:
    """Launcher authentication and license status."""

    is_authenticated: bool
    has_valid_license: bool
    hwid: str
    user_email: str | None = None
    tier: str = "COMMUNITY"
    features: tuple[str, ...] = ()
    expires_at: int = 0
    offline_until: int = 0
    error: str | None = None


class LauncherAuthManager:
    """Manages cloud authentication, HWID registration, and Ed25519 license tickets."""

    def __init__(
        self,
        cloud_client: CloudClient | None = None,
        secret_provider: SecretProvider | None = None,
        public_key: ed25519.Ed25519PublicKey | None = None,
    ) -> None:
        self.secrets = secret_provider or get_default_secret_provider()
        # P1-6/E7: the launcher previously constructed the client with a bare
        # CloudConfig() whose default base_url is the loopback dev server —
        # production launches sent device tokens and license refs to
        # http://127.0.0.1:8000. Resolve the production endpoint through the
        # same env-aware helper the desktop app uses.
        from src.ui.desktop_app import _resolve_cloud_base_url

        self.client = cloud_client or CloudClient(
            config=CloudConfig(base_url=_resolve_cloud_base_url()), secret_provider=self.secrets
        )

        if public_key is not None:
            self.public_key = public_key
        else:
            try:
                pub_bytes = base64.b64decode(DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64)
                self.public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
            except Exception:
                self.public_key = None

        self.flow = LicenseFlow(self.client, self.public_key) if self.public_key else None

    @property
    def hwid(self) -> str:
        """Current machine hardware fingerprint."""
        return generate_hardware_fingerprint()

    def get_current_status(self) -> AuthStatus:
        """Inspect stored credentials and evaluate current license validity."""
        hwid = self.hwid
        has_session = self.client.has_session_token()

        if not self.secrets.has_secret("CLOUD_LICENSE_TICKET") or not self.flow:
            return AuthStatus(
                is_authenticated=has_session,
                has_valid_license=False,
                hwid=hwid,
                tier="FREE",
            )

        ticket_str = self.secrets.get_secret("CLOUD_LICENSE_TICKET").decode("utf-8")
        try:
            # P1-6 (REVIEW H-5): this is OFFLINE re-verification of a stored
            # ticket — pass is_offline=True so the backend-granted grace
            # window (offline_until) is actually enforced. The old default
            # (False) made the grace check dead code: an expired-grace
            # ticket was accepted forever as long as `exp` had not passed.
            claims = self.flow.verify_cloud_ticket(ticket_str, is_offline=True)
            return AuthStatus(
                is_authenticated=has_session,
                has_valid_license=True,
                hwid=hwid,
                tier=claims.tier,
                features=claims.features,
                expires_at=claims.expires_at,
                offline_until=claims.offline_until,
            )
        except Exception as exc:
            return AuthStatus(
                is_authenticated=has_session,
                has_valid_license=False,
                hwid=hwid,
                tier="EXPIRED",
                error=str(exc),
            )

    def login_web_session(self, session_token: str) -> bool:
        """Store session token from web login."""
        try:
            self.client.store_session_token(session_token.strip())
            return True
        except Exception as exc:
            logger.error("Failed to store session token", extra={"error": str(exc)})
            return False

    def activate_with_key(self, license_key: str, device_name: str = "Desktop Client") -> CloudLicenseClaims:
        """Register device with cloud and activate license key."""
        if not self.flow:
            raise RuntimeError("License flow public key not configured.")

        # 1. Register device if not yet registered
        if not self.client.get_device_token():
            self.flow.register_device(device_name=device_name, hwid=self.hwid)

        # 2. Activate license key
        claims = self.flow.activate_license(license_key.strip())
        return claims

    def logout(self) -> None:
        """Clear local session and license token from DPAPI."""
        self.client.clear_session_token()
        if self.secrets.has_secret("CLOUD_LICENSE_TICKET"):
            self.secrets.delete_secret("CLOUD_LICENSE_TICKET")

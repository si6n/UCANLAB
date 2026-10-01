"""Launcher Authentication & License Activation Manager.

Interacts with Universal-CAN-Cloud API, registers hardware fingerprint (HWID),
and securely stores/validates cryptographic Ed25519 license tokens in Windows DPAPI.
"""

from __future__ import annotations

import base64
import time
import webbrowser
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError, PlatformError
from src.core.logging import get_logger
from src.safety.secret_provider import SecretProvider, get_default_secret_provider
from src.security.cloud.client import CloudClient, CloudConfig
from src.security.cloud.desktop_auth import (
    DesktopAuthorizer,
    DeviceCodeLogin,
    DevicePollStatus,
    LoginOutcome,
    PkceSession,
    build_browser_login_url,
    license_state_from_error,
    portal_url_for,
)
from src.security.cloud.license_flow import (
    DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64,
    CloudLicenseClaims,
    LicenseFlow,
    default_license_hwm_path,
)
from src.security.hwid.collector import generate_hardware_fingerprint

logger = get_logger("launcher.auth")


@dataclass(slots=True, frozen=True)
class AuthStatus:
    """Launcher authentication and license status."""

    is_authenticated: bool
    has_valid_license: bool
    hwid: str
    tier: str = "COMMUNITY"
    features: tuple[str, ...] = ()
    expires_at: int = 0
    offline_until: int = 0
    error: str | None = None
    #: LicenseError code behind a failed verification (e.g. OFFLINE_GRACE_EXPIRED).
    error_code: str | None = None


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
        #
        # M-6 CONSTRAINT (documented, deliberately NOT fixed by moving code):
        # the canonical implementation of `_resolve_cloud_base_url` lives in
        # `src/ui/desktop_app.py`, so this function-local import pulls the
        # whole UI graph into the launcher and would form an import cycle if
        # the UI ever imported the launcher. The correct home is a small
        # `src/security/cloud/endpoints.py`, but creating it requires editing
        # `src/security/**` and `src/ui/**` — both OUT OF SCOPE for this batch.
        # The import therefore stays function-local (never at module import
        # time) with this note as the documented constraint.
        from src.ui.desktop_app import _resolve_cloud_base_url

        self.client = cloud_client or CloudClient(
            config=CloudConfig(base_url=_resolve_cloud_base_url(), enforce_allowlist=True),
            secret_provider=self.secrets,
        )

        if public_key is not None:
            self.public_key = public_key
        else:
            # T41 / LA-1 (Y-3): a corrupt/mis-packaged embedded public key used
            # to be swallowed by a bare `except Exception` and silently dropped
            # licensing (flow=None → FREE). That is a fail-OPEN: the failure
            # must be loud and terminal instead. Decode errors now raise
            # LicenseError (fail-closed) after a CRITICAL log.
            try:
                pub_bytes = base64.b64decode(DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64, validate=True)
                self.public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
            except Exception as exc:
                logger.critical(
                    "Embedded cloud public key is invalid — refusing to start "
                    "with licensing disabled (fail-closed)",
                    extra={"error": str(exc)},
                )
                raise LicenseError(
                    "Embedded license public key is invalid or corrupt; "
                    "refusing to run with license verification disabled.",
                    code="EMBEDDED_KEY_INVALID",
                    cause=exc,
                ) from exc

        if self.public_key is None:  # defensive: unreachable, never fail open
            logger.critical("License flow public key could not be established")
            raise LicenseError(
                "License public key unavailable after initialization.",
                code="EMBEDDED_KEY_INVALID",
            )
        # T57-B / L-2: the launcher previously built its LicenseFlow WITHOUT a
        # hwm_path, so `_persist_hwm` was a no-op and its offline re-verification
        # ran with a process-local anti-rollback anchor — every launcher restart
        # reset `last_known_clock_ts` to boot time and a clock rollback while the
        # launcher was closed went undetected. Share the desktop app's persistent
        # HWM file so both call paths seal/read the SAME anchor.
        self.flow = LicenseFlow(self.client, self.public_key, hwm_path=default_license_hwm_path())

    @property
    def hwid(self) -> str:
        """Current machine hardware fingerprint."""
        return generate_hardware_fingerprint()

    def get_current_status(self) -> AuthStatus:
        """Inspect stored credentials and evaluate current license validity."""
        hwid = self.hwid
        has_session = self.client.has_session_token()

        # L-3: the legacy `or not self.flow` clause was dead. The comment only
        # mentions why; the real guard is the has_secret() check below.
        if not self.secrets.has_secret("CLOUD_LICENSE_TICKET"):
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
            # T57-B / L-6: always pass the registered device id explicitly —
            # never rely on the client fallback — so device binding cannot be
            # silently skipped when the vault is in an unexpected state.
            claims = self.flow.verify_cloud_ticket(
                ticket_str,
                expected_device_id=self.client.get_device_id(),
                is_offline=True,
            )
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
            # A-1 (ASAMA-2): this branch swallowed every verification failure
            # and returned has_valid_license=False with no log record, so an
            # operator could not tell WHY the machine lost its license
            # (expired ticket, device mismatch, corrupt vault, clock skew...).
            # Emit a structured record carrying the exception type/message and
            # the device id this build resolved; the fail-closed result is
            # unchanged.
            logger.warning(
                "Cloud license ticket verification failed; treating as unlicensed",
                extra={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "device_id": self.client.get_device_id(),
                    "has_session_token": has_session,
                },
            )
            return AuthStatus(
                is_authenticated=has_session,
                has_valid_license=False,
                hwid=hwid,
                tier="EXPIRED",
                error=str(exc),
                error_code=getattr(exc, "code", None),
            )

    def login_web_session(self, session_token: str) -> bool:
        """Store session token from web login.

        A5-1 (REVIEW Aşama 5): an empty or whitespace-only value used to be
        accepted and stored as ``b""``. Because `has_session_token()` only
        checks for the secret's presence, `AuthStatus.is_authenticated` then
        reported True for a session with no credential. Reject blank input at
        the boundary so a "logged in" state always implies a real token.
        """
        if not isinstance(session_token, str) or not session_token.strip():
            logger.warning("Rejected web session login with an empty token")
            return False
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

    def refresh_silently(self) -> LoginOutcome | None:
        """Renew the stored license with the device credential, no browser (S10).

        None when there is nothing to renew or the cloud is unreachable; an
        outcome otherwise ("ready", or a definitive refusal with a message).
        """
        authorizer = DesktopAuthorizer(self.client, self.flow, hwid_provider=lambda: self.hwid)
        try:
            return authorizer.refresh_online()
        except PlatformError as exc:
            logger.info("Silent license refresh skipped", extra={"error_code": exc.code})
            return None

    def sign_in(
        self,
        *,
        open_browser: Callable[[str], object] = webbrowser.open,
        browser_timeout_s: float = 300.0,
        on_device_code: Callable[[dict[str, object]], None] | None = None,
        device_timeout_s: float = 600.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> LoginOutcome:
        """Mechanic flow sign-in, run by the launcher BEFORE the license gate.

        The license gate (L-1) stays closed: nothing here launches the core
        binary. The browser opens ucanlab.org; the single-use code comes back
        to a loopback listener in THIS process (PKCE verifier never leaves it);
        the device is registered and the organisation's license activated. When
        the browser never returns (firewall, other device), the device-code
        fallback runs if ``on_device_code`` is given to show the code.
        """
        from src.ui.desktop_app import _bind_loopback_server, _DesktopAuthCallbackHandler

        # No internet: say so now instead of waiting minutes for a browser
        # page that cannot load. Any HTTP answer (even an error) means online.
        try:
            self.client.request("GET", "/health", health_endpoint=True)
        except PlatformError:
            return LoginOutcome(status="error", state=license_state_from_error("CLOUD_UNREACHABLE"),
                                error_code="CLOUD_UNREACHABLE")

        authorizer = DesktopAuthorizer(self.client, self.flow, hwid_provider=lambda: self.hwid)
        session = PkceSession.new()
        handler = _DesktopAuthCallbackHandler
        result: dict[str, object] = {"status": "pending", "error": None}
        try:
            server, port = _bind_loopback_server()
        except OSError:
            server = None
            logger.warning("No loopback port free for browser sign-in; using the code fallback")
        if server is not None:
            handler.app_instance = SimpleNamespace(cloud_client=self.client, desktop_authorizer=authorizer)
            handler.expected_state = session.state
            handler.code_verifier = session.verifier
            handler.redirect_uri = f"http://127.0.0.1:{port}/callback"
            handler.auth_result = result
            try:
                url = build_browser_login_url(portal_url_for(self.client.config.base_url), port, session, self.hwid)
                try:
                    open_browser(url)
                except Exception as exc:  # noqa: BLE001 — no browser: fall through to the code
                    logger.warning("Could not open the system browser", extra={"error": type(exc).__name__})
                deadline = time.monotonic() + browser_timeout_s
                server.timeout = 0.5
                while result.get("status") == "pending" and time.monotonic() < deadline:
                    server.handle_request()
            finally:
                handler.code_verifier = ""
                server.server_close()
            outcome = result.get("login_outcome")
            if result.get("status") == "completed" and isinstance(outcome, LoginOutcome):
                return outcome

        if on_device_code is None:
            return LoginOutcome(status="error", state=license_state_from_error("SIGN_IN_FAILED"),
                                error_code="SIGN_IN_FAILED")
        return self._sign_in_with_device_code(authorizer, on_device_code, device_timeout_s, sleep)

    def _sign_in_with_device_code(
        self,
        authorizer: DesktopAuthorizer,
        on_device_code: Callable[[dict[str, object]], None],
        timeout_s: float,
        sleep: Callable[[float], None],
    ) -> LoginOutcome:
        login = DeviceCodeLogin(self.client)
        try:
            ticket = login.start()
        except PlatformError as exc:
            code = exc.code if exc.code in ("RATE_LIMITED", "CLOUD_UNREACHABLE") else "SIGN_IN_FAILED"
            return LoginOutcome(status="error", state=license_state_from_error(code), error_code=code)
        on_device_code(ticket.public_view(time.monotonic()))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            sleep(max(0.0, ticket.next_poll_at - time.monotonic()))
            try:
                polled = login.poll(ticket)
            except PlatformError:
                continue  # transient network error: keep polling until the deadline
            if polled.status is DevicePollStatus.COMPLETED and polled.session_token:
                return authorizer.complete_login(polled.session_token)
            if polled.status is DevicePollStatus.DENIED:
                return LoginOutcome(status="error", state=license_state_from_error("DEVICE_CODE_DENIED"),
                                    error_code="DEVICE_CODE_DENIED")
            if polled.status in (DevicePollStatus.EXPIRED, DevicePollStatus.FAILED):
                break
        return LoginOutcome(status="error", state=license_state_from_error("DEVICE_CODE_EXPIRED"),
                            error_code="DEVICE_CODE_EXPIRED")

    def logout(self) -> None:
        """Clear local session and license token from DPAPI."""
        self.client.clear_session_token()
        if self.secrets.has_secret("CLOUD_LICENSE_TICKET"):
            self.secrets.delete_secret("CLOUD_LICENSE_TICKET")

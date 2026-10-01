"""Desktop sign-in and license bootstrap for the mechanic flow (Aşama 3).

Contract with the cloud: ``docs/product/CLOUD_AUTH_CONTRACT.md`` and the
machine-readable ``docs/product/cloud_auth_contract.v1.json``.

Three ways for the browser sign-in to reach the app, all ending in the same
PKCE-protected code exchange:

1. **Loopback** (primary, RFC 8252 §7.3): the browser redirects to
   ``http://127.0.0.1:{47820|47821|47822}/callback``. Implemented by the
   exclusive listener in ``src/ui/desktop_app.py``.
2. **``ucanlab://auth/callback``** (secondary): when the browser does not follow
   the loopback redirect, the web page offers an "open in app" link. Windows
   starts a short-lived app process with that URL; ``forward_deep_link`` hands
   the code to the already-running app's loopback listener, so there is still
   exactly one code path and the PKCE verifier never leaves the first process.
   A custom scheme can be registered by any program (RFC 8252 §8.8), so the
   deep link carries nothing the attacker could use without the verifier.
3. **Device code** (fallback, RFC 8628): the app shows a short code, the user
   approves it on ``ucanlab.org/cihaz`` from any signed-in browser, and the app
   polls. The grant is PKCE-bound, so a leaked device code is useless.

After any of them, ``DesktopAuthorizer.complete_login`` stores the session in
the OS vault (DPAPI on Windows), registers the device (HWID), picks the
organisation's license for this device and activates it; the Ed25519 ticket is
verified locally and kept for offline use until ``offline_until``.

Nothing in this module writes a token, verifier, device code or password to a
URL (other than the single-use loopback/deep-link code) or to a log record.
"""

from __future__ import annotations

import base64
import datetime as _dt
import enum
import hashlib
import json
import re
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from src.core.errors import LicenseError, PlatformError, SecurityError
from src.core.logging import get_logger
from src.security.cloud.client import CloudClient, validate_session_token
from src.security.cloud.license_flow import CloudLicenseClaims, LicenseFlow
from src.security.hwid.collector import generate_hardware_fingerprint

logger = get_logger("security.cloud.desktop_auth")

# --- Contract constants (cloud_auth_contract.v1.json "constants") -----------
LOOPBACK_PORTS: tuple[int, ...] = (47820, 47821, 47822)
LOOPBACK_CALLBACK_PATH = "/callback"
DEEP_LINK_SCHEME = "ucanlab"
DEEP_LINK_HOST = "auth"
DEEP_LINK_PATH = "/callback"
DEVICE_VERIFICATION_PATH = "/cihaz"
DEFAULT_DEVICE_NAME = "UCanLab Desktop"

# Endpoint paths relative to /api/v1 (CloudConfig.endpoint adds the prefix).
PATH_DESKTOP_TOKEN = "/auth/desktop/token"
PATH_DEVICE_START = "/auth/desktop/device/start"
PATH_DEVICE_TOKEN = "/auth/desktop/device/token"
PATH_LICENSES = "/licenses"

# Single-use codes are URL-safe base64 (server: secrets.token_urlsafe(32)).
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,512}$")
_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
# Parameters that would mean a long-lived credential is travelling in a URL.
_FORBIDDEN_CALLBACK_PARAMS = frozenset({"token", "session_token", "access_token", "id_token", "password"})


# ---------------------------------------------------------------------------
# PKCE (RFC 7636)
# ---------------------------------------------------------------------------


def pkce_verifier() -> str:
    """RFC 7636 §4.1 verifier: 43 chars of unreserved characters."""
    return secrets.token_urlsafe(32)


def pkce_challenge(verifier: str) -> str:
    """S256 challenge: base64url(SHA256(ASCII(verifier))) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class PkceSession:
    """One sign-in attempt: CSRF ``state`` + PKCE pair. Never logged."""

    state: str = field(repr=False)
    verifier: str = field(repr=False)
    challenge: str

    @classmethod
    def new(cls) -> PkceSession:
        verifier = pkce_verifier()
        return cls(state=secrets.token_urlsafe(24), verifier=verifier, challenge=pkce_challenge(verifier))


def portal_url_for(api_base_url: str) -> str:
    """Web portal origin for an API base URL (production API lives on ucanlab.org)."""
    base = api_base_url.rstrip("/")
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    if host == "ucanlab.org" or host.endswith(".ucanlab.org"):
        return "https://ucanlab.org"
    return base


def build_browser_login_url(portal_url: str, port: int, session: PkceSession, hwid: str) -> str:
    """URL opened in the system browser. Carries only public values.

    ``state`` is a CSRF nonce and ``code_challenge`` a one-way digest; the
    verifier stays in this process. Only a 16-char HWID prefix is sent so the
    page can label the device without learning the full fingerprint.
    """
    if port not in LOOPBACK_PORTS:
        raise SecurityError(f"Loopback port {port} is not in the contract allowlist", code="LOOPBACK_PORT_NOT_ALLOWED")
    query = urllib.parse.urlencode(
        {"port": port, "state": session.state, "hwid": hwid[:16], "code_challenge": session.challenge}
    )
    return f"{portal_url.rstrip('/')}/auth/desktop?{query}"


# ---------------------------------------------------------------------------
# ucanlab:// deep link
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeepLinkCallback:
    code: str = field(repr=False)
    state: str = field(repr=False)
    port: int


def parse_deep_link(url: str) -> DeepLinkCallback:
    """Validate ``ucanlab://auth/callback?code=…&state=…&port=…`` strictly.

    Anything else (other host/path, a fragment, duplicated or unknown
    parameters, a port outside the allowlist, or a parameter that looks like a
    long-lived credential) is refused: the URL comes from outside the app.
    """
    if not isinstance(url, str) or len(url) > 2048:
        raise SecurityError("Deep link is missing or too long", code="DEEP_LINK_INVALID")
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme.lower() != DEEP_LINK_SCHEME or parts.netloc.lower() != DEEP_LINK_HOST:
        raise SecurityError("Deep link target is not ucanlab://auth", code="DEEP_LINK_INVALID")
    if parts.path != DEEP_LINK_PATH or parts.fragment:
        raise SecurityError("Deep link path must be /callback with no fragment", code="DEEP_LINK_INVALID")

    params = urllib.parse.parse_qs(parts.query, keep_blank_values=True, strict_parsing=False)
    if _FORBIDDEN_CALLBACK_PARAMS & set(params):
        raise SecurityError("Deep link carries a credential parameter; refusing", code="DEEP_LINK_CREDENTIAL")
    if set(params) != {"code", "state", "port"} or any(len(v) != 1 for v in params.values()):
        raise SecurityError("Deep link must carry exactly code, state and port", code="DEEP_LINK_INVALID")

    code, state, port_raw = params["code"][0], params["state"][0], params["port"][0]
    if not _CODE_RE.match(code) or not _STATE_RE.match(state):
        raise SecurityError("Deep link code/state has an invalid format", code="DEEP_LINK_INVALID")
    if not port_raw.isdigit() or int(port_raw) not in LOOPBACK_PORTS:
        raise SecurityError("Deep link port is not in the allowlist", code="DEEP_LINK_INVALID")
    return DeepLinkCallback(code=code, state=state, port=int(port_raw))


def forward_deep_link(
    url: str,
    *,
    opener: urllib.request.OpenerDirector | None = None,
    timeout_s: float = 5.0,
) -> bool:
    """Hand a deep-link code to the running app's loopback listener.

    Runs in the short-lived process Windows starts for ``ucanlab://``. The
    request goes straight to 127.0.0.1 with proxies disabled, so the code can
    never be routed through a system proxy. Returns True when the listener
    accepted it; the listener still checks ``state`` and redeems the code with
    the verifier only it holds.
    """
    callback = parse_deep_link(url)
    target = (
        f"http://127.0.0.1:{callback.port}{LOOPBACK_CALLBACK_PATH}?"
        + urllib.parse.urlencode({"code": callback.code, "state": callback.state})
    )
    director = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        # Fixed http://127.0.0.1 target built above from validated parts.
        with director.open(target, timeout=timeout_s) as resp:  # nosec B310
            ok = int(getattr(resp, "status", 0)) == 200
    except OSError as exc:
        logger.warning("Deep link could not reach the running app", extra={"port": callback.port, "error": type(exc).__name__})
        return False
    logger.info("Deep link forwarded to loopback listener", extra={"port": callback.port, "accepted": ok})
    return ok


# ---------------------------------------------------------------------------
# Loopback code exchange (shared by loopback + deep link)
# ---------------------------------------------------------------------------


def exchange_authorization_code(client: CloudClient, code: str, verifier: str, redirect_uri: str) -> str:
    """POST /auth/desktop/token → validated session token (not stored here)."""
    resp = client.request(
        "POST",
        PATH_DESKTOP_TOKEN,
        json_body={"code": code, "code_verifier": verifier, "redirect_uri": redirect_uri},
    )
    if resp.status != 200:
        raise SecurityError("Sign-in code was rejected by the server", code="WEB_LOGIN_EXCHANGE_FAILED",
                            details={"status": resp.status})
    token = resp.json_object().get("session_token")
    if not token:
        raise SecurityError("Token exchange returned no session_token", code="WEB_LOGIN_EXCHANGE_FAILED")
    return validate_session_token(token)


# ---------------------------------------------------------------------------
# Device code (RFC 8628)
# ---------------------------------------------------------------------------


class DevicePollStatus(str, enum.Enum):
    PENDING = "pending"
    SLOW_DOWN = "slow_down"
    COMPLETED = "completed"
    DENIED = "denied"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class DeviceLoginTicket:
    """What the UI shows (user code + where to type it) plus private poll state."""

    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_at: float
    interval: int
    device_code: str = field(repr=False)
    verifier: str = field(repr=False)
    next_poll_at: float = 0.0

    def public_view(self, now: float) -> dict[str, Any]:
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "verification_uri_complete": self.verification_uri_complete,
            "expires_in": max(0, int(self.expires_at - now)),
            "interval": self.interval,
        }


@dataclass(frozen=True)
class DevicePollResult:
    status: DevicePollStatus
    session_token: str | None = field(default=None, repr=False)


class DeviceCodeLogin:
    """Client side of the device-code fallback."""

    SLOW_DOWN_STEP_S = 5

    def __init__(self, client: CloudClient, clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self._clock = clock

    def start(self, client_name: str = DEFAULT_DEVICE_NAME) -> DeviceLoginTicket:
        verifier = pkce_verifier()
        resp = self.client.request(
            "POST",
            PATH_DEVICE_START,
            json_body={
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
                "client_name": client_name[:64],
            },
        )
        if resp.status == 429:
            raise SecurityError("Too many sign-in attempts", code="RATE_LIMITED")
        if resp.status != 200:
            raise SecurityError("Could not start code sign-in", code="DEVICE_LOGIN_START_FAILED",
                                details={"status": resp.status})
        data = resp.json_object()
        try:
            now = self._clock()
            interval = max(1, int(data["interval"]))
            return DeviceLoginTicket(
                user_code=str(data["user_code"]),
                verification_uri=str(data["verification_uri"]),
                verification_uri_complete=str(data["verification_uri_complete"]),
                expires_at=now + int(data["expires_in"]),
                interval=interval,
                device_code=str(data["device_code"]),
                verifier=verifier,
                next_poll_at=now + interval,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SecurityError("Malformed device sign-in response", code="DEVICE_LOGIN_START_FAILED") from exc

    def poll(self, ticket: DeviceLoginTicket) -> DevicePollResult:
        """One poll. Never polls faster than the server's interval."""
        now = self._clock()
        if now >= ticket.expires_at:
            return DevicePollResult(DevicePollStatus.EXPIRED)
        if now < ticket.next_poll_at:
            return DevicePollResult(DevicePollStatus.PENDING)

        resp = self.client.request(
            "POST",
            PATH_DEVICE_TOKEN,
            json_body={"device_code": ticket.device_code, "code_verifier": ticket.verifier},
        )
        ticket.next_poll_at = self._clock() + ticket.interval
        if resp.status == 200:
            token = resp.json_object().get("session_token")
            if not token:
                return DevicePollResult(DevicePollStatus.FAILED)
            return DevicePollResult(DevicePollStatus.COMPLETED, session_token=validate_session_token(token))
        if resp.status == 429:
            ticket.interval += self.SLOW_DOWN_STEP_S
            ticket.next_poll_at = self._clock() + ticket.interval
            return DevicePollResult(DevicePollStatus.SLOW_DOWN)

        reason = _error_message(resp)
        if reason == "authorization_pending":
            return DevicePollResult(DevicePollStatus.PENDING)
        if reason == "slow_down":
            ticket.interval += self.SLOW_DOWN_STEP_S  # RFC 8628 §3.5
            ticket.next_poll_at = self._clock() + ticket.interval
            return DevicePollResult(DevicePollStatus.SLOW_DOWN)
        if reason == "access_denied":
            return DevicePollResult(DevicePollStatus.DENIED)
        if reason == "expired_token":
            return DevicePollResult(DevicePollStatus.EXPIRED)
        return DevicePollResult(DevicePollStatus.FAILED)


def _error_message(resp: Any) -> str:
    """API error envelope {"error": {"message": …}} → message, or ''."""
    try:
        body = resp.json_object()
    except Exception:  # noqa: BLE001 — any malformed body is just "no reason"
        return ""
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message", ""))
    return str(body.get("detail", ""))


# ---------------------------------------------------------------------------
# Tier → mode entitlements (MECHANIC_FLOW.md §6.3)
# ---------------------------------------------------------------------------

#: Tiers that include Expert (engineer) mode. Unknown tiers get the mechanic
#: flow only: a new server tier must be added here deliberately.
_ENGINEER_TIERS = frozenset({"pro", "heavy_duty_pro", "marine_pro", "fleet", "enterprise"})
_TIER_RANK = {"enterprise": 5, "fleet": 4, "pro": 3, "heavy_duty_pro": 3, "marine_pro": 3, "starter": 1}


@dataclass(frozen=True)
class Entitlements:
    tier: str
    mechanic: bool
    engineer: bool
    active_tests: bool
    dtc_clear: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "mechanic": self.mechanic,
            "engineer": self.engineer,
            "active_tests": self.active_tests,
            "dtc_clear": self.dtc_clear,
        }


NO_ENTITLEMENTS = Entitlements(tier="none", mechanic=False, engineer=False, active_tests=False, dtc_clear=False)


def entitlements_for(tier: str, features: tuple[str, ...] | list[str]) -> Entitlements:
    """Mode rights granted by a VERIFIED license ticket.

    Writing to the vehicle (clearing codes, active tests) needs the
    ``active_tests`` feature; everything else in the mechanic flow only needs a
    valid license. Callers must pass claims from ``verify_cloud_ticket``.
    """
    normalized = (tier or "").strip().lower()
    feature_set = {str(f).strip().lower() for f in features}
    writes = "active_tests" in feature_set
    return Entitlements(
        tier=normalized or "unknown",
        mechanic=True,
        engineer=normalized in _ENGINEER_TIERS,
        active_tests=writes,
        dtc_clear=writes,
    )


# ---------------------------------------------------------------------------
# License selection
# ---------------------------------------------------------------------------


def _parse_ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.UTC)
    return parsed.timestamp()


def select_license(licenses: list[dict[str, Any]], device_id: str | None, now: float) -> str | None:
    """Pick the license_ref to activate on this device, or None.

    1. A live license already bound to THIS device (re-activation keeps the seat).
    2. Otherwise the best free (unbound) seat: highest tier, then latest expiry.
    A license bound to another device is never chosen: moving it is the
    HWID-reset flow's job, not a silent side effect of signing in.
    """
    live: list[dict[str, Any]] = []
    for lic in licenses:
        if not isinstance(lic, dict) or not lic.get("is_active") or not lic.get("license_ref"):
            continue
        expires = _parse_ts(lic.get("expires_at"))
        if expires is None or expires <= now:
            continue
        live.append(lic)

    if device_id:
        for lic in live:
            if str(lic.get("device_id") or "") == device_id:
                return str(lic["license_ref"])

    free = [lic for lic in live if not lic.get("device_id")]
    if not free:
        return None
    free.sort(
        key=lambda lic: (_TIER_RANK.get(str(lic.get("tier", "")).lower(), 0), _parse_ts(lic.get("expires_at")) or 0),
        reverse=True,
    )
    return str(free[0]["license_ref"])


# ---------------------------------------------------------------------------
# User-facing messages (plain language, TR + EN)
# ---------------------------------------------------------------------------

_MESSAGES: dict[str, tuple[str, str]] = {
    "OFFLINE_GRACE_EXPIRED": (
        "Lisansınızın çevrimdışı süresi doldu. İnternete bağlanıp yeniden giriş yapın; kayıtlarınız silinmez.",
        "Your offline period has ended. Connect to the internet and sign in again; your data is kept.",
    ),
    "LICENSE_EXPIRED": (
        "Lisansınızın süresi doldu. ucanlab.org üzerinden yenileyebilirsiniz.",
        "Your license has expired. You can renew it on ucanlab.org.",
    ),
    "CLOCK_ROLLBACK_DETECTED": (
        "Bilgisayarın saati geri alınmış görünüyor. Tarih ve saati düzeltip tekrar deneyin.",
        "The computer clock looks wrong. Correct the date and time and try again.",
    ),
    "CLOCK_MONOTONIC_MISMATCH": (
        "Bilgisayarın saati çalışırken değiştirildi. Saati düzeltip uygulamayı yeniden başlatın.",
        "The computer clock changed while running. Correct it and restart the app.",
    ),
    "DEVICE_MISMATCH": (
        "Bu lisans başka bir bilgisayara ait. Yeniden giriş yapın.",
        "This license belongs to another computer. Please sign in again.",
    ),
    "NO_LICENSE": (
        "Hesabınızda bu bilgisayar için boş lisans yok. ucanlab.org'dan lisans alın ya da başka bir cihazdan taşıyın.",
        "Your account has no free license for this computer. Get one on ucanlab.org or move one from another device.",
    ),
    "LICENSE_IN_USE": (
        "Lisansınız başka bir bilgisayarda kullanılıyor. ucanlab.org'da 'Cihaz taşıma' ile bu bilgisayara alın.",
        "Your license is in use on another computer. Move it here with 'Move device' on ucanlab.org.",
    ),
    "ROLE_NOT_ALLOWED": (
        "Hesap rolünüz cihaz eklemeye izin vermiyor. Atölye yöneticinizden 'Teknisyen' yetkisi isteyin.",
        "Your account role cannot add devices. Ask your workshop admin for the Technician role.",
    ),
    "CLOUD_UNREACHABLE": (
        "İnternete ulaşılamıyor. İlk giriş için internet gerekli; sonrasında 7 güne kadar internetsiz çalışır.",
        "Can't reach the internet. The first sign-in needs internet; after that it works offline for up to 7 days.",
    ),
    "RATE_LIMITED": (
        "Çok fazla deneme yapıldı. Bir dakika bekleyip tekrar deneyin.",
        "Too many attempts. Wait a minute and try again.",
    ),
    "DEVICE_CODE_EXPIRED": (
        "Kodun süresi doldu. Yeni kod alın.",
        "The code has expired. Get a new one.",
    ),
    "DEVICE_CODE_DENIED": (
        "Giriş web sitesinde reddedildi.",
        "Sign-in was declined on the website.",
    ),
    "SIGN_IN_FAILED": (
        "Giriş tamamlanamadı. Tekrar deneyin.",
        "Sign-in could not be completed. Please try again.",
    ),
    "LICENSE_INVALID": (
        "Lisans doğrulanamadı. Yeniden giriş yapın.",
        "The license could not be verified. Please sign in again.",
    ),
    "LICENSE_REVOKED": (
        "Lisansınız iptal edilmiş ya da başka bir bilgisayara taşınmış. ucanlab.org'dan kontrol edip yeniden giriş yapın.",
        "Your license was revoked or moved to another computer. Check on ucanlab.org and sign in again.",
    ),
    "ORGANIZATION_INACTIVE": (
        "Atölye hesabınız etkin değil. ucanlab.org'dan hesabınızı kontrol edin.",
        "Your workshop account is not active. Check your account on ucanlab.org.",
    ),
}

# Device refresh refusals that mean "this seat is gone": the stored ticket is
# dropped at once instead of running out its offline window (S10).
_DEFINITIVE_REFRESH_REFUSALS: dict[str, str] = {
    "LICENSE_REVOKED": "LICENSE_REVOKED",
    "SEAT_NOT_HELD": "LICENSE_REVOKED",
    "LICENSE_NOT_FOUND": "LICENSE_REVOKED",
    "LICENSE_EXPIRED": "LICENSE_EXPIRED",
    "ORGANIZATION_INACTIVE": "ORGANIZATION_INACTIVE",
}


def user_message(code: str | None) -> tuple[str, str]:
    """(TR, EN) plain-language text for an error code; generic fallback."""
    if code in _MESSAGES:
        return _MESSAGES[code]
    if code and code.startswith("CLOCK_"):
        return _MESSAGES["CLOCK_ROLLBACK_DETECTED"]
    return _MESSAGES["LICENSE_INVALID"] if code else _MESSAGES["SIGN_IN_FAILED"]


# ---------------------------------------------------------------------------
# License state for the UI (offline countdown)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenseState:
    status: str  # "active" | "missing" | "expired" | "clock_problem" | "invalid"
    entitlements: Entitlements
    offline_seconds_left: int = 0
    error_code: str | None = None

    @property
    def offline_days_left(self) -> int:
        return self.offline_seconds_left // 86400

    def as_dict(self) -> dict[str, Any]:
        tr, en = user_message(self.error_code) if self.error_code else ("", "")
        return {
            "status": self.status,
            "entitlements": self.entitlements.as_dict(),
            "offline_seconds_left": self.offline_seconds_left,
            "offline_days_left": self.offline_days_left,
            "error_code": self.error_code,
            "message_tr": tr,
            "message_en": en,
        }


_EXPIRED_CODES = frozenset({"OFFLINE_GRACE_EXPIRED", "LICENSE_EXPIRED"})


def license_state_from_claims(claims: CloudLicenseClaims, now: float) -> LicenseState:
    deadline = min(claims.expires_at, claims.offline_until)
    return LicenseState(
        status="active",
        entitlements=entitlements_for(claims.tier, claims.features),
        offline_seconds_left=max(0, int(deadline - now)),
    )


def license_state_from_error(code: str | None) -> LicenseState:
    if code in _EXPIRED_CODES:
        status = "expired"
    elif code and code.startswith("CLOCK_"):
        status = "clock_problem"
    else:
        status = "invalid"
    return LicenseState(status=status, entitlements=NO_ENTITLEMENTS, error_code=code)


MISSING_LICENSE_STATE = LicenseState(status="missing", entitlements=NO_ENTITLEMENTS)


# ---------------------------------------------------------------------------
# Post-login bootstrap
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoginOutcome:
    status: str  # "ready" | "no_license" | "error"
    state: LicenseState
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        tr, en = user_message(self.error_code) if self.error_code else ("", "")
        return {"status": self.status, "license": self.state.as_dict(), "error_code": self.error_code,
                "message_tr": tr, "message_en": en}


class DesktopAuthorizer:
    """Turns a fresh session token into an activated, verified license."""

    def __init__(
        self,
        client: CloudClient,
        flow: LicenseFlow,
        *,
        hwid_provider: Callable[[], str] = generate_hardware_fingerprint,
        device_name: str = DEFAULT_DEVICE_NAME,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.flow = flow
        self._hwid_provider = hwid_provider
        self.device_name = device_name
        self._clock = clock

    def complete_login(self, session_token: str) -> LoginOutcome:
        """Store the session, register the device, activate the best license."""
        try:
            self.client.store_session_token(validate_session_token(session_token))
            return self._activate(allow_reregister=True)
        except PlatformError as exc:
            code = exc.code if exc.code in _MESSAGES else "SIGN_IN_FAILED"
            logger.warning("Desktop login bootstrap failed", extra={"error_code": exc.code})
            return LoginOutcome(status="error", state=license_state_from_error(code), error_code=code)

    def refresh_online(self) -> LoginOutcome | None:
        """Roll the offline window forward while online.

        First with the device credential (``/licenses/refresh``, no user
        session needed — S10), then, if that is not possible and a session
        exists, by re-activating. Returns None when there is nothing to
        refresh or the cloud is not reachable: the stored ticket then keeps
        working until offline_until. A definitive refusal (revoked, moved,
        expired, organisation disabled) drops the stored ticket at once.
        """
        if not self.client.get_device_token():
            return None
        license_ref = self._stored_license_ref()
        if license_ref is not None:
            try:
                claims = self.flow.refresh_license(license_ref, self._hwid_provider())
                return LoginOutcome(status="ready", state=license_state_from_claims(claims, self._clock()))
            except LicenseError as exc:
                reason = str((exc.details or {}).get("reason", ""))
                mapped = _DEFINITIVE_REFRESH_REFUSALS.get(reason) if exc.code == "REFRESH_REJECTED" else None
                if mapped is not None:
                    logger.warning("Cloud refused the license refresh; ticket dropped", extra={"reason": reason})
                    self.client.clear_license_ticket()
                    return LoginOutcome(status="error", state=license_state_from_error(mapped), error_code=mapped)
                logger.info("Device license refresh not possible", extra={"error_code": exc.code, "reason": reason})
            except PlatformError as exc:
                logger.info("Online license refresh skipped", extra={"error_code": exc.code})
                return None
        if not self.client.has_session_token():
            return None
        try:
            return self._activate(allow_reregister=False)
        except PlatformError as exc:
            logger.info("Online license refresh skipped", extra={"error_code": exc.code})
            return None

    def _stored_license_ref(self) -> str | None:
        """license_id of the stored ticket, read even after its offline window ended.

        Only used as the lookup key for /licenses/refresh; the cloud checks
        everything and the new ticket is verified before it is stored.
        """
        ticket = self.client.get_license_ticket()
        if not ticket:
            return None
        try:
            body = ticket.strip().split(".")[0]
            data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except Exception:  # noqa: BLE001 — unreadable ticket: no device refresh
            return None
        ref = data.get("license_id") if isinstance(data, dict) else None
        return ref if isinstance(ref, str) and 4 <= len(ref) <= 64 else None

    def _activate(self, *, allow_reregister: bool) -> LoginOutcome:
        if not self.client.get_device_token():
            self._register()

        license_ref = self._pick_license()
        if license_ref is None:
            return LoginOutcome(status="no_license", state=MISSING_LICENSE_STATE, error_code="NO_LICENSE")

        try:
            claims = self.flow.activate_license(license_ref)
        except LicenseError as exc:
            if exc.code == "ACTIVATION_REJECTED" and allow_reregister:
                # A device token revoked by an HWID reset is refused with 403.
                # Re-register once (the server rebinds the same device row).
                self.client.clear_device_token()
                self._register()
                claims = self.flow.activate_license(license_ref)
            elif exc.code == "ACTIVATION_FAILED" and "HTTP 409" in str(exc):
                # Lost a race for a free seat, or the seat moved meanwhile.
                return LoginOutcome(status="error", state=license_state_from_error("LICENSE_IN_USE"),
                                    error_code="LICENSE_IN_USE")
            else:
                return LoginOutcome(status="error", state=license_state_from_error(exc.code), error_code=exc.code)

        return LoginOutcome(status="ready", state=license_state_from_claims(claims, self._clock()))

    def _register(self) -> None:
        try:
            self.flow.register_device(device_name=self.device_name, hwid=self._hwid_provider())
        except LicenseError as exc:
            if exc.code == "REGISTRATION_UNAUTHORIZED":
                raise SecurityError("Role cannot register devices", code="ROLE_NOT_ALLOWED") from exc
            if exc.code == "RATE_LIMITED":
                raise SecurityError("Registration rate limited", code="RATE_LIMITED") from exc
            raise SecurityError("Device registration failed", code="SIGN_IN_FAILED") from exc

    def _pick_license(self) -> str | None:
        resp = self.client.request("GET", PATH_LICENSES)
        if resp.status == 401:
            raise SecurityError("Session was not accepted", code="SIGN_IN_FAILED")
        if resp.status != 200:
            raise SecurityError("Could not list licenses", code="SIGN_IN_FAILED", details={"status": resp.status})
        try:
            items = resp.json()
        except ValueError as exc:
            raise SecurityError("Malformed license list", code="SIGN_IN_FAILED") from exc
        if not isinstance(items, list):
            raise SecurityError("Malformed license list", code="SIGN_IN_FAILED")
        return select_license(items, self.client.get_device_id(), self._clock())

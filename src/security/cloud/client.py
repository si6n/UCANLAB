"""Cloud client configuration & authenticated HTTP transport.

 The desktop -> cloud session is a browser-independent HttpOnly cookie the
 operator acquires by logging into the web portal; the desktop stores the
 session token via the platform SecretProvider (DPAPI on Windows) and replays
 it as a cookie on every request. Device flow uses the device_token instead.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from src.core.errors import LicenseError, SecurityError
from src.core.logging import get_logger
from src.safety.secret_provider import SecretProvider, get_default_secret_provider

logger = get_logger("security.cloud.client")


# B4 (REVIEW): caller-supplied headers must never be able to override or
# strip the DPAPI-managed session credentials. A malicious/buggy WebView
# bridge call passing extra_headers={"Cookie": ...} would otherwise
# silently replace the authenticated session cookie (session spoofing) or
# remove authentication entirely.
_PROTECTED_REQUEST_HEADERS: frozenset[str] = frozenset(
    {"cookie", "authorization", "host", "content-length", "transfer-encoding", "connection"}
)


def _sanitize_extra_headers(extra_headers: dict[str, str] | None) -> dict[str, str]:
    """Drop credential/transport-controlled headers from caller input."""
    if not extra_headers:
        return {}
    sanitized: dict[str, str] = {}
    for name, value in extra_headers.items():
        if name.lower() in _PROTECTED_REQUEST_HEADERS:
            logger.warning(
                "Rejected caller-supplied protected header",
                extra={"header": name},
            )
            continue
        sanitized[name] = value
    return sanitized


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip authentication credentials (Cookie, Authorization) on cross-origin redirects."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        orig_host = urllib.parse.urlsplit(req.full_url).netloc.lower()
        new_host = urllib.parse.urlsplit(newurl).netloc.lower()
        if orig_host != new_host:
            # Cross-origin redirect: strip sensitive credentials to prevent leakage
            new_req.headers.pop("Cookie", None)
            new_req.headers.pop("Authorization", None)
            if hasattr(new_req, "unredirected_hdrs"):
                new_req.unredirected_hdrs.pop("Cookie", None)
                new_req.unredirected_hdrs.pop("Authorization", None)
        return new_req

_SESSION_SECRET_NAME = "CLOUD_SESSION_TOKEN"
_DEVICE_TOKEN_SECRET_NAME = "CLOUD_DEVICE_TOKEN"
_DEVICE_ID_SECRET_NAME = "CLOUD_DEVICE_ID"
LICENSE_TICKET_SECRET_NAME = "CLOUD_LICENSE_TICKET"

# Retry policy: transient network/5xx failures are retried with linear backoff.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(slots=True)
class CloudConfig:
    """Connection settings for the Universal CAN Cloud API."""

    base_url: str = "http://127.0.0.1:8000"
    api_prefix: str = "/api/v1"
    timeout_seconds: float = 30.0
    upload_timeout_seconds: float = 120.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.5
    max_retry_backoff_seconds: float = 60.0
    user_agent: str = "UniversalCAN-Desktop/13.0"
    # SEC-C-001: production builds must speak HTTPS. Loopback dev servers are
    # the only permitted plain-HTTP exception (no MITM surface on localhost).
    require_https: bool = True

    def __post_init__(self) -> None:
        self._validate_scheme()

    def _validate_scheme(self) -> None:
        """Fail closed on insecure transport (SEC-C-001).

        Only https:// URLs — or loopback (localhost / 127.0.0.1 / [::1]) for
        local development — are accepted. Any other http:// endpoint would
        expose the session cookie to network interception.
        """
        if not self.require_https:
            return
        if not self.base_url:
            return
        url = self.base_url.strip().lower()
        if url.startswith("https://"):
            return
        if url.startswith("http://") and self._is_loopback(url):
            return
        raise SecurityError(
            "Cloud API base_url must use HTTPS (only loopback http://localhost "
            "is allowed for development); refusing insecure session transport",
            code="CLOUD_INSECURE_TRANSPORT",
            details={"base_url": self.base_url},
        )

    @staticmethod
    def _is_loopback(url: str) -> bool:
        """True when the http:// authority targets a loopback host (127.0.0.0/8, ::1, localhost).

        L-5 (3FABLE, MED-2): parse with urlsplit + ipaddress instead of manual
        splitting. Note that 0.0.0.0 is non-loopback (INADDR_ANY) and is strictly rejected.
        """
        try:
            import ipaddress
            from urllib.parse import urlsplit

            hostname = urlsplit(url).hostname
            if not hostname:
                return False
            try:
                return ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                return hostname == "localhost"
        except Exception:  # noqa: BLE001 — malformed URL is not loopback
            return False

    def endpoint(self, path: str, *, health_endpoint: bool = False) -> str:
        """Build a full URL; health endpoints live outside the api prefix."""
        if not path.startswith("/"):
            path = "/" + path
        prefix = "" if health_endpoint else self.api_prefix
        return f"{self.base_url.rstrip('/')}{prefix}{path}"


@dataclass(slots=True)
class CloudResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None


class CloudClient:
    """Minimal authenticated HTTP client for the cloud REST API.

    Session strategy (MASTER_PLAN §3.2):
      1. The operator logs into the web portal once and pastes the session
         token into the desktop settings; it is stored under DPAPI.
      2. Device-scoped calls (telemetry upload, activation) prefer the
         device_token issued at registration.
    """

    def __init__(
        self,
        config: CloudConfig | None = None,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        self.config = config or CloudConfig()
        self._secrets = secret_provider or get_default_secret_provider()

    def set_base_url(self, url: str) -> None:
        """Change the API base URL — re-validated (3FABLE-H2).

        Assigning `client.config.base_url = url` bypassed CloudConfig's
        __post_init__ scheme check (dataclass attribute assignment never
        re-runs it), letting a WebView script point the DPAPI-stored
        session cookie at an arbitrary plain-HTTP host. This setter
        reconstructs the config so the HTTPS/loopback validation always
        runs; an invalid URL raises before any state changes.
        """
        new_config = CloudConfig(
            base_url=url.rstrip("/"),
            api_prefix=self.config.api_prefix,
            timeout_seconds=self.config.timeout_seconds,
            upload_timeout_seconds=self.config.upload_timeout_seconds,
            max_retries=self.config.max_retries,
            retry_backoff_seconds=self.config.retry_backoff_seconds,
            max_retry_backoff_seconds=self.config.max_retry_backoff_seconds,
            user_agent=self.config.user_agent,
            require_https=self.config.require_https,
        )
        # Only swap after the constructor validated the new URL.
        self.config = new_config

    # ------------------------------------------------------------------
    # Credential storage (DPAPI-backed)
    # ------------------------------------------------------------------
    def store_session_token(self, token: str) -> None:
        self._secrets.store_secret(_SESSION_SECRET_NAME, token.encode("utf-8"))
        logger.info("Cloud session token stored (DPAPI)")

    def has_session_token(self) -> bool:
        return self._secrets.has_secret(_SESSION_SECRET_NAME)

    def clear_session_token(self) -> None:
        if self._secrets.has_secret(_SESSION_SECRET_NAME):
            self._secrets.delete_secret(_SESSION_SECRET_NAME)

    def store_device_token(self, token: str) -> None:
        self._secrets.store_secret(_DEVICE_TOKEN_SECRET_NAME, token.encode("utf-8"))
        logger.info("Cloud device token stored (DPAPI)")

    def get_device_token(self) -> str | None:
        if not self._secrets.has_secret(_DEVICE_TOKEN_SECRET_NAME):
            return None
        return self._secrets.get_secret(_DEVICE_TOKEN_SECRET_NAME).decode("utf-8")

    def clear_device_token(self) -> None:
        if self._secrets.has_secret(_DEVICE_TOKEN_SECRET_NAME):
            self._secrets.delete_secret(_DEVICE_TOKEN_SECRET_NAME)

    def store_device_id(self, device_id: str) -> None:
        self._secrets.store_secret(_DEVICE_ID_SECRET_NAME, device_id.encode("utf-8"))
        logger.info("Cloud device ID stored (DPAPI)")

    def get_device_id(self) -> str | None:
        if not self._secrets.has_secret(_DEVICE_ID_SECRET_NAME):
            return None
        return self._secrets.get_secret(_DEVICE_ID_SECRET_NAME).decode("utf-8")

    def clear_device_id(self) -> None:
        if self._secrets.has_secret(_DEVICE_ID_SECRET_NAME):
            self._secrets.delete_secret(_DEVICE_ID_SECRET_NAME)

    def store_license_ticket(self, ticket_token: str) -> None:
        """Persist the signed license ticket for offline re-verification (grace)."""
        self._secrets.store_secret(LICENSE_TICKET_SECRET_NAME, ticket_token.encode("utf-8"))
        logger.info("Cloud license ticket stored (DPAPI)")

    # ------------------------------------------------------------------
    # HTTP core (urllib to stay dependency-free; PyInstaller friendly)
    # ------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        extra_headers: dict[str, str] | None = None,
        health_endpoint: bool = False,
    ) -> CloudResponse:
        url = self.config.endpoint(path, health_endpoint=health_endpoint)
        data = raw_body
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type

        session = None
        if self._secrets.has_secret(_SESSION_SECRET_NAME):
            session = self._secrets.get_secret(_SESSION_SECRET_NAME).decode("utf-8")
        if session:
            headers["Cookie"] = f"ucan_session={session}"
        # B4: sanitized AFTER the session cookie is applied — extra headers
        # can neither replace nor strip it.
        headers.update(_sanitize_extra_headers(extra_headers))

        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                opener = urllib.request.build_opener(_SafeRedirectHandler())
                with opener.open(req, timeout=self.config.timeout_seconds) as resp:  # nosec: B310
                    return CloudResponse(
                        status=resp.status,
                        body=resp.read(),
                        headers={k: v for k, v in resp.headers.items()},
                    )
            except urllib.error.HTTPError as exc:
                body = exc.read() if exc.fp else b""
                if exc.code in _RETRY_STATUSES and attempt < self.config.max_retries:
                    self._sleep_backoff(attempt, exc.headers)
                    continue
                return CloudResponse(status=exc.code, body=body, headers=dict(exc.headers or {}))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < self.config.max_retries:
                    self._sleep_backoff(attempt, None)
                    continue
                break

        raise SecurityError(
            f"Cloud API unreachable after {self.config.max_retries + 1} attempts: {last_exc}",
            code="CLOUD_UNREACHABLE",
            cause=last_exc,
        )

    def _sleep_backoff(self, attempt: int, response_headers: Any) -> None:
        """Honour Retry-After when the server sent one; otherwise linear backoff."""
        import time as _time

        delay = self.config.retry_backoff_seconds * (attempt + 1)
        if response_headers is not None:
            retry_after = response_headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay = max(delay, float(retry_after))
                except (TypeError, ValueError):
                    pass  # non-numeric Retry-After (HTTP-date) — fall back to backoff
        # SEC-C-007: Cap maximum sleep delay to prevent DoS lockup from malicious/misconfigured server
        delay = min(delay, self.config.max_retry_backoff_seconds)
        _time.sleep(delay)
        logger.debug("Retrying cloud request", extra={"attempt": attempt + 1, "delay_s": delay})


def ensure_cloud_available(client: CloudClient) -> None:
    """Fail fast with a friendly error when the cloud is unreachable."""
    resp = client.request("GET", "/health", health_endpoint=True)
    if resp.status != 200:
        raise LicenseError("Cloud health check failed", code="CLOUD_HEALTH_CHECK_FAILED")

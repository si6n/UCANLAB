"""Cloud client configuration & authenticated HTTP transport.

 The desktop -> cloud session is a browser-independent HttpOnly cookie the
 operator acquires by logging into the web portal; the desktop stores the
 session token via the platform SecretProvider (DPAPI on Windows) and replays
 it as a cookie on every request. Device flow uses the device_token instead.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, ClassVar

from src.core.errors import LicenseError, ProtocolError, SecurityError, TransportError
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


def _spki_pin_bytes(pin: str) -> bytes | None:
    """Decode an SPKI SHA-256 pin to its 32 raw bytes, or None if malformed.

    Accepts the two conventional spellings:
      * base64 (the HPKP/``Public-Key-Pins`` form, e.g. ``AAAA...=``)
      * colon- or dash-separated hex (``ab:cd:...``/``ab-cd-...``)
    Returns ``None`` for anything that is not a well-formed 32-byte digest.
    """
    import base64
    import binascii

    text = pin.strip()
    if not text:
        return None
    # Hex form: allow ':'/'-' separators and optional 0x prefix.
    candidate = text[2:] if text.lower().startswith("0x") else text
    normalised = candidate.replace(":", "").replace("-", "")
    if normalised and all(ch in "0123456789abcdefABCDEF" for ch in normalised):
        if len(normalised) == 64:
            try:
                return binascii.unhexlify(normalised)
            except (binascii.Error, ValueError):
                return None
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    return decoded if len(decoded) == 32 else None


def _spki_digest_from_der(der: bytes) -> bytes:
    """SHA-256 digest of a DER certificate's SubjectPublicKeyInfo.

    Separated from the connection shim so it is directly unit-testable without
    a live TLS peer.
    """
    from cryptography.hazmat.primitives import serialization as _ser
    from cryptography.x509 import load_der_x509_certificate as _load_der

    try:
        cert = _load_der(der)
        spki = cert.public_key().public_bytes(
            encoding=_ser.Encoding.DER,
            format=_ser.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on any parse failure
        raise SecurityError(
            f"Unable to extract SPKI from peer certificate: {exc}",
            code="CLOUD_PIN_PARSE_FAILED",
        ) from exc
    return hashlib.sha256(spki).digest()


def _verify_pinned_peer(sock: Any, pins: frozenset[bytes]) -> None:
    """Fail closed unless the connected socket's leaf SPKI is in ``pins``.

    Kept as a free function (not inlined in the handler) so tests can drive it
    with a fake socket instead of standing up a real TLS server.
    """
    if sock is None:
        raise SecurityError("TLS socket missing after connect", code="CLOUD_PIN_NO_CERT")
    der = sock.getpeercert(binary_form=True)
    if not der:
        raise SecurityError(
            "Peer presented no certificate; refusing unpinned connection",
            code="CLOUD_PIN_NO_CERT",
        )
    if _spki_digest_from_der(der) not in pins:
        raise SecurityError(
            "Cloud TLS certificate SPKI does not match any configured pin "
            "(possible MITM); connection refused",
            code="CLOUD_PIN_MISMATCH",
        )


def _make_pinned_https_handler(pins: frozenset[bytes]) -> urllib.request.HTTPSHandler:
    """An `HTTPSHandler` whose connections enforce the SPKI pin allowlist.

    Pinning is ADDITIVE to normal CA verification: the stock SSL context still
    validates the chain and hostname first, so a pinned-but-untrusted
    certificate is rejected before the pin check even runs.
    """
    import http.client

    class _PinnedConnection(http.client.HTTPSConnection):
        def connect(self) -> None:
            super().connect()
            _verify_pinned_peer(self.sock, pins)

    class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: Any) -> Any:
            return self.do_open(_PinnedConnection, req)

    return _PinnedHTTPSHandler()


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Fail-closed redirect policy for authenticated cloud requests (I-07).

    The previous policy FOLLOWED cross-origin redirects and merely stripped
    Cookie/Authorization — a redirect to an attacker host still received an
    unauthenticated request, and per-destination scheme/host/IP was never
    re-validated. New policy: follow ONLY same-origin (scheme+host+port)
    HTTPS redirects (loopback http allowed for dev); anything else —
    cross-origin, scheme downgrade, non-allowlisted host, literal-IP
    private/loopback mismatch — refuses the redirect (returns None, so
    urllib surfaces the 3xx as an error instead of re-sending credentials).
    """

    def __init__(
        self,
        allowed_hosts: tuple[str, ...] | None = None,
        enforce_allowlist: bool = False,
        require_https: bool = True,
    ) -> None:
        self._allowed_hosts = allowed_hosts
        self._enforce_allowlist = enforce_allowlist
        self._require_https = require_https

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int] | None:
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return None
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            return None
        host = (parts.hostname or "").lower()
        try:
            port = parts.port if parts.port is not None else (443 if scheme == "https" else 80)
        except ValueError:
            return None
        return (scheme, host, port)

    @staticmethod
    def _resolve_ips(host: str) -> list[str]:
        """Resolve a hostname to every address it currently maps to.

        Redirect targets are a cold path, so the DNS lookup cost is
        acceptable; a name that cannot be resolved yields an empty list and
        the caller fails closed.
        """
        import socket as _socket

        try:
            infos = _socket.getaddrinfo(host, None, proto=_socket.IPPROTO_TCP)
        except OSError:
            return []
        ips: list[str] = []
        for _family, _type, _proto, _canon, sockaddr in infos:
            addr = str(sockaddr[0])
            if addr not in ips:
                ips.append(addr)
        return ips

    def _reject_private_resolution(self, host: str, newurl: str) -> None:
        """DNS-rebinding cover: every address a redirect host resolves to
        must be public. A name that resolves to loopback/private/link-local
        (or fails to resolve) is refused fail-closed, so an attacker cannot
        pass the allowlist with a public A record and then serve a private
        one on the redirect hop.
        """
        if host in DEV_ONLY_CLOUD_HOSTS:
            return  # explicit dev exception (loopback dev server)
        import ipaddress

        resolved = self._resolve_ips(host)
        if not resolved:
            raise SecurityError(
                f"Refusing cloud redirect to unresolvable host {host!r}",
                code="CLOUD_REDIRECT_REFUSED",
                details={"to": newurl},
            )
        for addr in resolved:
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise SecurityError(
                    f"Refusing cloud redirect: {host!r} resolves to non-public {addr!r}",
                    code="CLOUD_REDIRECT_REFUSED",
                    details={"to": newurl, "resolved": addr},
                )

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        # Fail CLOSED with a typed error (never follow-and-strip, never
        # return None into urllib which would surface as an untyped crash
        # downstream). Each refusal names the destination.
        orig = self._origin(req.full_url)
        new = self._origin(newurl)
        if orig is None or new is None or orig != new:
            raise SecurityError(
                f"Refusing cross-origin/scheme-downgrade cloud redirect to {newurl!r}",
                code="CLOUD_REDIRECT_REFUSED",
                details={"from": req.full_url, "to": newurl},
            )
        new_parts = urllib.parse.urlsplit(newurl)
        new_host = (new_parts.hostname or "").lower()
        new_scheme = (new_parts.scheme or "").lower()
        if new_scheme == "http" and not CloudConfig._is_loopback(newurl):
            raise SecurityError(
                f"Refusing cloud redirect to non-loopback plain HTTP {newurl!r}",
                code="CLOUD_REDIRECT_REFUSED",
                details={"to": newurl},
            )
        if self._enforce_allowlist or is_production():
            allowed = _allowed_hosts(self._allowed_hosts)
            if new_host not in allowed:
                raise SecurityError(
                    f"Refusing cloud redirect to non-allowlisted host {new_host!r}",
                    code="CLOUD_REDIRECT_REFUSED",
                    details={"to": newurl},
                )
        try:
            import ipaddress

            try:
                ip = ipaddress.ip_address(new_host)
            except ValueError:
                ip = None
            # Literal-IP destinations: reject private/loopback outright.
            if ip is not None and (ip.is_private or ip.is_loopback) and new_host not in DEV_ONLY_CLOUD_HOSTS:
                raise SecurityError(
                    f"Refusing cloud redirect to private/reserved IP {new_host!r}",
                    code="CLOUD_REDIRECT_REFUSED",
                    details={"to": newurl},
                )
            if ip is None:
                # Hostname destination: resolve and verify every address is
                # public (DNS-rebinding cover — previously deferred; the
                # redirect hop is cold so the lookup is affordable).
                self._reject_private_resolution(new_host, newurl)
        except SecurityError:
            raise
        except Exception:
            pass
        return super().redirect_request(req, fp, code, msg, headers, newurl)

_SESSION_SECRET_NAME = "CLOUD_SESSION_TOKEN"
_DEVICE_TOKEN_SECRET_NAME = "CLOUD_DEVICE_TOKEN"
_DEVICE_ID_SECRET_NAME = "CLOUD_DEVICE_ID"
LICENSE_TICKET_SECRET_NAME = "CLOUD_LICENSE_TICKET"

# Retry policy: transient network/5xx failures are retried with linear backoff.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

CANONICAL_CLOUD_HOSTS: tuple[str, ...] = (
    "ucan-cloud.si6n.io",
    "cloud.universalcan.io",
    "ucanlab.org",
    "api.ucanlab.org",
)

# SEC-07 (Batch B): loopback hosts USED to sit inside `CANONICAL_CLOUD_HOSTS`,
# which contradicted the "production speaks HTTPS to a cloud host" claim: a
# frozen (production) build would happily send the DPAPI-stored session cookie
# to 127.0.0.1 / localhost / ::1 — i.e. to any local process that manages to
# bind the port first. The production allowlist above is now cloud-only;
# loopback lives in this explicitly dev-only list, consulted only when the
# build is NOT production (see `_allowed_hosts`).
DEV_ONLY_CLOUD_HOSTS: tuple[str, ...] = (
    "127.0.0.1",
    "localhost",
    "::1",
)

#: Hardened session-token grammar (SEC-08). The token arrives from the web
#: portal (or a WebView bridge call) and is replayed verbatim as the value of
#: an HTTP `Cookie` header, so it must never be able to carry `;`, whitespace,
#: or CRLF — a `\r\n` in a header value is request splitting, and `;` would
#: inject extra cookie attributes. Deliberately conservative:
#: base64url/hex-ish tokens only.
_SESSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{8,512}$")


def is_production() -> bool:
    """True in a frozen build or when UCANLAB_ENV indicates production."""
    import os
    import sys

    if getattr(sys, "frozen", False):
        return True
    return os.environ.get("UCANLAB_ENV", "").strip().lower() in ("production", "prod")


def _allowed_hosts(configured: tuple[str, ...] | None) -> tuple[str, ...]:
    """The effective host allowlist (SEC-07).

    An explicit `allowed_hosts` always wins. Otherwise a production build gets
    the cloud-only canonical list, while a non-production run additionally
    accepts the dev-only loopback list so `python src/main.py` against a local
    backend keeps working.
    """
    if configured:
        return tuple(h.lower() for h in configured)
    if is_production():
        return CANONICAL_CLOUD_HOSTS
    return CANONICAL_CLOUD_HOSTS + DEV_ONLY_CLOUD_HOSTS


def validate_session_token(token: object) -> str:
    """Return a validated session token, or raise SecurityError (SEC-08).

    A blank or mistyped token used to be stored and later replayed as
    ``Cookie: ucan_session=<token>`` with no format check at all, and a token
    containing CRLF would have been injected straight into the header block.
    Fail closed with a typed error instead.
    """
    if not isinstance(token, str):
        raise SecurityError(
            f"Session token must be a string, got {type(token).__name__}",
            code="BAD_SESSION_TOKEN",
        )
    if "\r" in token or "\n" in token or "\x00" in token:
        raise SecurityError(
            "Session token contains control characters (CRLF/NUL); refusing it",
            code="BAD_SESSION_TOKEN",
        )
    stripped = token.strip()
    if not _SESSION_TOKEN_RE.fullmatch(stripped):
        raise SecurityError(
            "Session token does not match the expected format "
            "(8-512 chars of [A-Za-z0-9._~+/=-])",
            code="BAD_SESSION_TOKEN",
        )
    return stripped


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
    allowed_hosts: tuple[str, ...] | None = None
    enforce_allowlist: bool = False
    # FAZ 4 (hardening): optional certificate pinning. When
    # `pinned_spki_sha256` is non-empty, every TLS connection must present a
    # leaf certificate whose Subject Public Key Info hashes (base64 SHA-256,
    # the standard HPKP/SPKI form, colon-separated hex also accepted) to one of
    # the listed values. Default is EMPTY (off) so existing deployments keep
    # the standard CA-verified behaviour; pinning is opt-in because a stale pin
    # bricks connectivity, so it must be a conscious operator decision.
    pinned_spki_sha256: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self._validate_scheme()
        self._validate_host()
        self._validate_pins()

    def _validate_pins(self) -> None:
        """Fail fast on a malformed pin list (SEC hardening, FAZ 4).

        A pin that silently fails to parse would leave the connection
        *unpinned* while the operator believes otherwise — the worst outcome.
        Validate the *form* here so a typo is a configuration error, not a
        silent downgrade.
        """
        for pin in self.pinned_spki_sha256:
            if not isinstance(pin, str) or not pin.strip():
                raise SecurityError(
                    "pinned_spki_sha256 entries must be non-empty strings",
                    code="CLOUD_PIN_MALFORMED",
                )
            if not _spki_pin_bytes(pin):
                raise SecurityError(
                    f"pinned_spki_sha256 entry {pin!r} is not a valid "
                    "base64 SHA-256 SPKI pin (expected 32 bytes)",
                    code="CLOUD_PIN_MALFORMED",
                )

    def _validate_host(self) -> None:
        """Enforce canonical host allowlist and reject userinfo (T62-U1 / SEC-C-002)."""
        if not self.base_url:
            return
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(self.base_url)
        except Exception as exc:
            raise SecurityError(
                f"Malformed base_url: {exc}",
                code="CLOUD_MALFORMED_URL",
                details={"base_url": self.base_url},
            ) from exc

        if parsed.username or parsed.password:
            raise SecurityError(
                "Userinfo in cloud base URL is forbidden",
                code="CLOUD_USERINFO_FORBIDDEN",
                details={"base_url": self.base_url},
            )

        hostname = (parsed.hostname or "").lower()
        if not hostname:
            raise SecurityError(
                "Cloud API base_url has no valid hostname",
                code="CLOUD_INVALID_HOSTNAME",
                details={"base_url": self.base_url},
            )

        if self.enforce_allowlist or is_production():
            allowed = _allowed_hosts(self.allowed_hosts)
            if hostname not in allowed:
                raise SecurityError(
                    f"Cloud API host {hostname!r} is not in canonical allowlist; refusing connection (fail-closed)",
                    code="CLOUD_HOST_NOT_ALLOWED",
                    details={"hostname": hostname, "base_url": self.base_url},
                )

    def _validate_scheme(self) -> None:
        """Fail closed on insecure transport (SEC-C-001).

        Only https:// URLs â€” or loopback (localhost / 127.0.0.1 / [::1]) for
        local development â€” are accepted. Any other http:// endpoint would
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
        except Exception:  # noqa: BLE001 â€” malformed URL is not loopback
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

    def json_object(self) -> dict[str, Any]:
        """F5 (REVIEW Aşama 6): parse the body as a JSON OBJECT, fail-closed.

        `json()` raised a raw `json.JSONDecodeError` for a malformed body and
        returned a bare `AttributeError` when the body was valid JSON but not
        an object (e.g. a list or a string), both of which escaped as
        unhandled exceptions instead of a typed error. Callers that expect an
        object should use this accessor, which raises
        `ProtocolError(code="CLOUD_MALFORMED_RESPONSE")`.
        """
        if not self.body:
            raise ProtocolError(
                "Cloud response body is empty but a JSON object was expected",
                code="CLOUD_MALFORMED_RESPONSE",
                details={"status": self.status},
            )
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(
                f"Cloud response body is not valid JSON: {exc}",
                code="CLOUD_MALFORMED_RESPONSE",
                details={"status": self.status},
                cause=exc,
            ) from exc
        if not isinstance(parsed, dict):
            raise ProtocolError(
                f"Cloud response body is JSON {type(parsed).__name__}, expected an object",
                code="CLOUD_MALFORMED_RESPONSE",
                details={"status": self.status, "type": type(parsed).__name__},
            )
        return parsed


class CloudClient:
    """Minimal authenticated HTTP client for the cloud REST API.

    Session strategy (MASTER_PLAN Â§3.2):
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

    # M-20 (P2-11): hard cap on response bodies. API JSON responses are a
    # few KB; telemetry upload acknowledgements even less. A compromised or
    # buggy endpoint streaming gigabytes must not OOM the diagnostic tool.
    MAX_RESPONSE_BODY_BYTES: ClassVar[int] = 8 * 1024 * 1024  # 8 MiB
    MAX_ERROR_BODY_BYTES: ClassVar[int] = 64 * 1024  # 64 KiB for HTTPError bodies

    def _build_opener(self, url: str) -> urllib.request.OpenerDirector:
        """Build the urllib opener, adding SPKI pinning when configured.

        FAZ 4: with an empty `pinned_spki_sha256` the opener is identical to
        the previous behaviour (redirect credential-stripping only). With pins
        configured, an HTTPS handler whose connection class verifies the leaf
        SPKI is installed, so a swapped certificate is rejected even if it
        chains to a trusted CA (and the standard CA check still runs first).
        """
        handlers: list[Any] = [
            _SafeRedirectHandler(
                allowed_hosts=self.config.allowed_hosts,
                enforce_allowlist=self.config.enforce_allowlist,
                require_https=self.config.require_https,
            )
        ]
        pins = self._pinned_spki_digests
        if pins and url.lower().startswith("https://"):
            handlers.append(_make_pinned_https_handler(pins))
        return urllib.request.build_opener(*handlers)

    @property
    def _pinned_spki_digests(self) -> frozenset[bytes]:
        """Decoded pin set (empty frozenset when pinning is disabled)."""
        pins = getattr(self.config, "pinned_spki_sha256", ()) or ()
        decoded = {_spki_pin_bytes(p) for p in pins}
        return frozenset(d for d in decoded if d is not None)

    @classmethod
    def _read_body_bounded(cls, resp: Any) -> bytes:
        """Read an HTTP response body with a hard size cap (fail-closed)."""
        # 1. Refuse up front when the server declares an oversized body.
        content_length = resp.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = -1
            if declared > cls.MAX_RESPONSE_BODY_BYTES:
                raise TransportError(
                    f"Response body too large: declared {declared} bytes "
                    f"(limit {cls.MAX_RESPONSE_BODY_BYTES})"
                )
        # 2. Stream in chunks and stop hard at the cap even without a
        # Content-Length (chunked transfer, lying header).
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > cls.MAX_RESPONSE_BODY_BYTES:
                raise TransportError(
                    f"Response body exceeded {cls.MAX_RESPONSE_BODY_BYTES} bytes mid-stream"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @classmethod
    def _read_error_body_bounded(cls, exc: Any) -> bytes:
        """Read an HTTPError body capped at 64KB (error path must not OOM)."""
        fp = getattr(exc, "fp", None)
        if fp is None:
            return b""
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = fp.read(8192)
            if not chunk:
                break
            total += len(chunk)
            if total > cls.MAX_ERROR_BODY_BYTES:
                break
            chunks.append(chunk)
        return b"".join(chunks)[: cls.MAX_ERROR_BODY_BYTES]

    def set_base_url(self, url: str) -> None:
        """Change the API base URL â€” re-validated (3FABLE-H2).

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
            allowed_hosts=self.config.allowed_hosts,
            enforce_allowlist=self.config.enforce_allowlist,
            pinned_spki_sha256=self.config.pinned_spki_sha256,
        )
        # Only swap after the constructor validated the new URL.
        self.config = new_config

    # ------------------------------------------------------------------
    # Credential storage (DPAPI-backed)
    # ------------------------------------------------------------------
    def store_session_token(self, token: str) -> None:
        """Persist the operator's web session token, format-checked (SEC-08)."""
        validated = validate_session_token(token)
        self._secrets.store_secret(_SESSION_SECRET_NAME, validated.encode("utf-8"))
        logger.info("Cloud session token stored (DPAPI)")

    def has_session_token(self) -> bool:
        return self._secrets.has_secret(_SESSION_SECRET_NAME)

    def get_session_token(self) -> str | None:
        if not self._secrets.has_secret(_SESSION_SECRET_NAME):
            return None
        return self._secrets.get_secret(_SESSION_SECRET_NAME).decode("utf-8")

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
    # User session & Subscription management
    # ------------------------------------------------------------------
    def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate with cloud portal using email and password.

        Sends credentials to /auth/login. Extracts session token from
        response JSON or Set-Cookie header, validates it, and persists in DPAPI.
        """
        if not email or not isinstance(email, str) or "@" not in email:
            raise SecurityError("Geçerli bir e-posta adresi giriniz", code="INVALID_EMAIL")
        if not password or not isinstance(password, str):
            raise SecurityError("Şifre gereklidir", code="INVALID_PASSWORD")

        resp = self.request(
            "POST",
            "/auth/login",
            json_body={"email": email.strip(), "password": password},
        )
        data = resp.json_object() if resp.body else {}
        if resp.status not in (200, 201):
            detail = data.get("detail") or data.get("error") or f"Giriş başarısız (HTTP {resp.status})"
            raise SecurityError(detail, code="LOGIN_FAILED")

        token = data.get("token") or data.get("session_token") or data.get("access_token")
        if not token:
            cookie_hdr = resp.headers.get("Set-Cookie", "") or resp.headers.get("set-cookie", "")
            if "ucan_session=" in cookie_hdr:
                import re

                m = re.search(r"ucan_session=([^;,\s]+)", cookie_hdr)
                if m:
                    token = m.group(1)
        if not token:
            raise SecurityError(
                "Giriş yanıtında geçerli oturum belirteci bulunamadı",
                code="NO_SESSION_TOKEN",
            )

        self.store_session_token(token)
        return data

    def login_with_token(self, token: str) -> dict[str, Any]:
        """Authenticate using an existing web session token.

        I-06: verify with a request-scoped credential BEFORE persisting.
        The token is validated against /auth/me using the override path
        (never touches the shared vault); only an HTTP 200 persists to
        DPAPI. Bridge success derives from the verified response, so a
        bad/expired token returns success False instead of True+None.
        """
        validated = validate_session_token(token)
        user_info: dict[str, Any] | None = None
        try:
            resp = self.request("GET", "/auth/me", session_token=validated)
            if resp.status == 200 and resp.body:
                user_info = resp.json_object()
        except Exception as exc:
            logger.warning("Token login verification failed: %s", exc)
            user_info = None
        if user_info is None:
            return {"success": False, "token": None, "user": None}
        self.store_session_token(validated)
        return {"success": True, "token": validated, "user": user_info}

    def logout(self) -> None:
        """Clear local session token and cached license ticket."""
        self.clear_session_token()
        if self._secrets.has_secret(LICENSE_TICKET_SECRET_NAME):
            self._secrets.delete_secret(LICENSE_TICKET_SECRET_NAME)
        logger.info("Cloud session logged out; tokens cleared (DPAPI)")

    def get_current_user_and_subscription(self) -> dict[str, Any] | None:
        """Query /auth/me for current user identity and subscription status."""
        if not self.has_session_token():
            return None
        try:
            resp = self.request("GET", "/auth/me")
            if resp.status == 200 and resp.body:
                return resp.json_object()
            return None
        except Exception as exc:
            logger.warning("Failed to fetch user and subscription from cloud: %s", exc)
            return None

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
        session_token: str | None = None,
    ) -> CloudResponse:
        """Perform a cloud request.

        D2 (REVIEW Aşama 2): ``session_token`` is a REQUEST-SCOPED credential
        override. Previously the only way to make a call with a different
        session was to save the caller's token into the shared secret vault,
        fire the request, then restore the previous value — a save/fire/restore
        window during which a concurrent request would use the WRONG session,
        and a crash inside the window left the vault holding the wrong token.
        Passing the token here keeps the override local to this call and never
        mutates shared state; the vault is read only when no override is given.
        """
        url = self.config.endpoint(path, health_endpoint=health_endpoint)
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if self.config.enforce_allowlist or is_production():
            allowed = _allowed_hosts(self.config.allowed_hosts)
            if hostname not in allowed:
                raise SecurityError(
                    f"Cloud API host {hostname!r} is not in canonical allowlist; refusing request (fail-closed)",
                    code="CLOUD_HOST_NOT_ALLOWED",
                    details={"hostname": hostname, "url": url},
                )
        logger.debug("CloudClient request %s %s://%s%s", method, parsed.scheme, hostname, parsed.path)
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

        # D2: an explicit override wins and never touches the shared vault.
        session = session_token
        if session is None and self._secrets.has_secret(_SESSION_SECRET_NAME):
            session = self._secrets.get_secret(_SESSION_SECRET_NAME).decode("utf-8")
        if session:
            # SEC-08 (Batch B): re-validate immediately before the header is
            # built. `store_session_token` now validates on write, but the
            # value here can also come from a pre-existing vault entry (written
            # by an older build) or from a request-scoped override supplied by
            # a bridge call — neither has passed this gate yet.
            session = validate_session_token(session)
            headers["Cookie"] = f"ucan_session={session}"
            headers["Authorization"] = f"Bearer {session}"
        # B4: sanitized AFTER the session cookie is applied â€” extra headers
        # can neither replace nor strip it.
        headers.update(_sanitize_extra_headers(extra_headers))

        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                opener = self._build_opener(url)
                with opener.open(req, timeout=self.config.timeout_seconds) as resp:  # nosec: B310
                    return CloudResponse(
                        status=resp.status,
                        # M-20 (P2-11): bounded read â€” a hostile or
                        # misbehaving endpoint must not be able to stream an
                        # unbounded body into memory. Enforce Content-Length
                        # up front and cap the streamed bytes either way.
                        body=self._read_body_bounded(resp),
                        headers={k: v for k, v in resp.headers.items()},
                    )
            except urllib.error.HTTPError as exc:
                try:
                    body = self._read_error_body_bounded(exc)
                except TransportError:
                    body = b""
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
                    pass  # non-numeric Retry-After (HTTP-date) â€” fall back to backoff
        # SEC-C-007: Cap maximum sleep delay to prevent DoS lockup from malicious/misconfigured server
        delay = min(delay, self.config.max_retry_backoff_seconds)
        _time.sleep(delay)
        logger.debug("Retrying cloud request", extra={"attempt": attempt + 1, "delay_s": delay})


def ensure_cloud_available(client: CloudClient) -> None:
    """Fail fast with a friendly error when the cloud is unreachable."""
    resp = client.request("GET", "/health", health_endpoint=True)
    if resp.status != 200:
        raise LicenseError("Cloud health check failed", code="CLOUD_HEALTH_CHECK_FAILED")

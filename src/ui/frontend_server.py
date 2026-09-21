"""Hardened local static server for the WebView frontend (F6-1).

Motivation
----------
REVIEW Aşama 6 (F6-1): the desktop shell loaded the frontend straight off
``file://`` with **no Content-Security-Policy and no navigation guard**. That
left two doors open:

1. **No CSP** — any injected markup/script (a compromised dependency, a
   pasted-then-rendered string, a future template bug) could execute and reach
   the ``window.pywebview.api`` bridge, which exposes TX / E-Stop / flash
   authority. The renderer must never be able to reach those endpoints except
   through the audited bridge calls the UI itself makes.
2. **No navigation policy** — a link or redirect could navigate the embedded
   browser to a remote origin, at which point that origin owns the window and
   still holds the bridge.

Design
------
We serve the built ``dist/`` over a loopback-only HTTP server that:

* binds **127.0.0.1** on an ephemeral port (never 0.0.0.0);
* only serves files that resolve INSIDE the dist root (path-traversal proof,
  including symlink escape);
* answers with a strict ``Content-Security-Policy`` that forbids remote script
  /connect/frame origins, ``X-Content-Type-Options: nosniff``,
  ``Referrer-Policy: no-referrer`` and ``Cross-Origin-*`` isolation;
* returns 404 for everything else and never reflects the request path.

The CSP is the primary control; the server is deliberately tiny so it is easy
to audit. It is a *local* asset host, not a network service.
"""

from __future__ import annotations

import http.server
import mimetypes
import socket
import threading
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit

from src.core.logging import get_logger

logger = get_logger(__name__)

#: Origins the renderer may ever talk to. Everything is same-origin because the
#: app is fully offline (AGENTS.md §2.8) — no CDN, no font host, no telemetry
#: beacon. 'unsafe-inline' is needed for styles because the built bundle
#: inlines critical CSS; scripts get NO such allowance.
CONTENT_SECURITY_POLICY: Final[str] = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'none'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "worker-src 'self' blob:; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

_SECURITY_HEADERS: Final[dict[str, str]] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), usb=()",
    "Cache-Control": "no-store",
}


class FrontendRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve only the dist root, only inside it, always with the CSP headers."""

    #: Set by the factory below.
    dist_root: Path

    server_version = "UCANLABLocal/1.0"
    sys_version = ""  # do not advertise the Python version

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, directory=str(self.dist_root), **kwargs)

    # -- security headers -------------------------------------------------
    def end_headers(self) -> None:
        for header, value in _SECURITY_HEADERS.items():
            self.send_header(header, value)
        super().end_headers()

    # -- path confinement -------------------------------------------------
    def translate_path(self, path: str) -> str:
        """Resolve a request path, refusing anything outside the dist root.

        Overriding this (rather than trusting the base class) makes the
        containment check explicit and covers percent-encoded traversal
        (``%2e%2e%2f``) plus absolute-path injection.
        """
        raw = unquote(urlsplit(path).path)
        # Strip the leading slash and any drive/UNC noise, then join.
        relative = raw.lstrip("/\\")
        candidate = (self.dist_root / relative).resolve()
        try:
            candidate.relative_to(self.dist_root)
        except ValueError:
            logger.warning("Frontend asset request escaped the dist root", extra={"path": raw[:200]})
            return str(self.dist_root / "__forbidden__")
        if candidate.is_dir():
            candidate = candidate / "index.html"
        return str(candidate)

    def list_directory(self, path):  # type: ignore[no-untyped-def]
        """Never render a directory listing."""
        self.send_error(404, "Not Found")
        return None

    def log_message(self, format, *args) -> None:  # type: ignore[no-untyped-def]
        # Route through the structured logger and drop the client address
        # (always loopback) so no request data lands in plain stderr.
        logger.debug("frontend asset: %s", format % args)


def _make_handler(dist_root: Path) -> type[FrontendRequestHandler]:
    class _Handler(FrontendRequestHandler):
        pass

    _Handler.dist_root = dist_root
    return _Handler


class FrontendServer:
    """Loopback-only static server for the built frontend."""

    def __init__(self, dist_root: Path) -> None:
        self.dist_root = Path(dist_root).resolve()
        if not self.dist_root.is_dir():
            raise FileNotFoundError(f"frontend dist root not found: {self.dist_root}")
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/index.html"

    def start(self) -> str:
        """Bind 127.0.0.1:0 and serve in a daemon thread. Returns the URL."""
        handler = _make_handler(self.dist_root)
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Frontend asset server listening on loopback", extra={"port": self.port})
        return self.url

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError as exc:  # pragma: no cover - teardown best effort
                logger.warning("Frontend asset server shutdown error", extra={"error": str(exc)})
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> FrontendServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def is_loopback_url(url: str) -> bool:
    """True when `url` points at loopback (used by the navigation guard)."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def guess_content_type(path: Path) -> str:
    """MIME type for an asset, defaulting to a non-sniffable binary type."""
    ctype, _ = mimetypes.guess_type(str(path))
    return ctype or "application/octet-stream"


def _unused_socket_probe() -> int:  # pragma: no cover - helper kept for clarity
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])

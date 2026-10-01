"""In-process fake of the cloud auth/license API (real HTTP on 127.0.0.1).

Built from ``docs/product/cloud_auth_contract.v1.json``: ``ROUTES`` must cover
exactly the contract's endpoints (enforced by test_cloud_auth_contract.py), and
behaviour mirrors UCANLAB-CLOUD ``backend/app`` — error envelope, PKCE-bound
single-use codes, RFC 8628 poll errors, seat-bound license activation and
Ed25519-signed tickets. Server-side semantics are tested for real in the cloud
repo; this fake exists so the desktop client can be tested without it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE = "/api/v1"
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ23456789"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()


@dataclass
class FakeLicense:
    license_ref: str
    tier: str = "pro"
    features: list[str] = field(default_factory=lambda: ["j1939", "uds", "active_tests"])
    expires_at: float = field(default_factory=lambda: time.time() + 365 * 86400)
    is_active: bool = True
    device_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class FakeCloudState:
    ticket_signer: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    organization_id: str = "22222222-2222-2222-2222-222222222222"
    user_role_can_register: bool = True
    sessions: dict[str, str] = field(default_factory=dict)  # token -> email
    auth_codes: dict[str, tuple[str, str]] = field(default_factory=dict)  # code -> (challenge, redirect_uri)
    device_grants: dict[str, dict[str, Any]] = field(default_factory=dict)  # device_code -> grant
    devices: dict[str, str] = field(default_factory=dict)  # device_token -> device_id
    device_ids_by_hwid: dict[str, str] = field(default_factory=dict)
    licenses: list[FakeLicense] = field(default_factory=list)
    offline_days: int = 7
    refresh_reject: str = ""  # force /licenses/refresh to refuse with this reason
    device_interval: int = 5
    requests: list[tuple[str, str]] = field(default_factory=list)

    # --- helpers the tests call directly ("the browser side") -------------
    def new_browser_session(self, email: str = "usta@atolye.example.com") -> str:
        token = "sess_" + secrets.token_urlsafe(24)
        self.sessions[token] = email
        return token

    def issue_auth_code(self, challenge: str, port: int = 47820) -> str:
        code = secrets.token_urlsafe(32)
        self.auth_codes[code] = (challenge, f"http://127.0.0.1:{port}/callback")
        return code

    def approve_user_code(self, user_code: str, email: str = "usta@atolye.example.com", deny: bool = False) -> None:
        for grant in self.device_grants.values():
            if grant["user_code"] == user_code:
                grant["decision"] = "denied" if deny else "approved"
                grant["email"] = email
                return
        raise KeyError(user_code)

    def revoke_device_tokens(self) -> None:
        self.devices.clear()

    def sign_ticket(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        sig = self.ticket_signer.sign(body)
        enc = base64.urlsafe_b64encode
        return enc(body).decode().rstrip("=") + "." + enc(sig).decode().rstrip("=")


Handler = Callable[["FakeCloudHandler", dict[str, Any]], None]
ROUTES: dict[tuple[str, str], str] = {}


def route(method: str, path: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        ROUTES[(method, path)] = fn.__name__
        return fn

    return deco


class FakeCloudHandler(BaseHTTPRequestHandler):
    state: FakeCloudState

    def log_message(self, *args: Any) -> None:  # silence
        pass

    # --- plumbing ---------------------------------------------------------
    def _send(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str) -> None:
        code = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "error"}.get(
            status, "error"
        )
        self._send(status, {"error": {"code": code, "message": message, "request_id": "fake"}})

    def _session_email(self) -> str | None:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "ucan_session" and value in self.state.sessions:
                return self.state.sessions[value]
        return None

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        self.state.requests.append((method, path))
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if not path.startswith(BASE):
            self._error(404, "not found")
            return
        name = ROUTES.get((method, path[len(BASE):]))
        if name is None:
            self._error(404, "not found")
            return
        getattr(self, name)(body)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    # --- contract endpoints -------------------------------------------------
    @route("POST", "/auth/desktop/authorize")
    def desktop_authorize(self, body: dict[str, Any]) -> None:
        if self._session_email() is None:
            self._error(401, "not signed in")
            return
        if body.get("port") not in (47820, 47821, 47822):
            self._error(400, "loopback port is not allowed")
            return
        code = self.state.issue_auth_code(body["code_challenge"], body["port"])
        self._send(200, {"code": code, "redirect_uri": self.state.auth_codes[code][1], "expires_in": 300})

    @route("POST", "/auth/desktop/token")
    def desktop_token(self, body: dict[str, Any]) -> None:
        record = self.state.auth_codes.get(body.get("code", ""))
        if record is None or _challenge(body.get("code_verifier", "")) != record[0] or body.get(
            "redirect_uri"
        ) != record[1]:
            self._error(400, "invalid_grant")
            return
        del self.state.auth_codes[body["code"]]  # single use
        token = self.state.new_browser_session()
        self._send(200, {"session_token": token, "token_type": "session", "user": {"email": self.state.sessions[token]}})

    @route("POST", "/auth/desktop/device/start")
    def device_start(self, body: dict[str, Any]) -> None:
        device_code = secrets.token_urlsafe(32)
        raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
        user_code = f"{raw[:4]}-{raw[4:]}"
        self.state.device_grants[device_code] = {
            "user_code": user_code,
            "challenge": body["code_challenge"],
            "decision": None,
            "consumed": False,
            "expires_at": time.time() + 600,
            "last_poll": None,
            "interval": self.state.device_interval,
        }
        uri = "https://ucanlab.org/cihaz"
        self._send(200, {
            "device_code": device_code, "user_code": user_code, "verification_uri": uri,
            "verification_uri_complete": f"{uri}?code={user_code}", "expires_in": 600,
            "interval": self.state.device_interval,
        })

    @route("POST", "/auth/desktop/device/approve")
    def device_approve(self, body: dict[str, Any]) -> None:
        email = self._session_email()
        if email is None:
            self._error(401, "not signed in")
            return
        try:
            self.state.approve_user_code(body.get("user_code", ""), email, deny=body.get("decision") == "deny")
        except KeyError:
            self._error(400, "invalid_user_code")
            return
        self._send(200, {"status": "denied" if body.get("decision") == "deny" else "approved",
                         "client_name": "UCanLab Desktop"})

    @route("POST", "/auth/desktop/device/token")
    def device_token(self, body: dict[str, Any]) -> None:
        grant = self.state.device_grants.get(body.get("device_code", ""))
        if grant is None or _challenge(body.get("code_verifier", "")) != grant["challenge"] or grant["consumed"]:
            self._error(400, "invalid_grant")
            return
        now = time.time()
        if now >= grant["expires_at"]:
            self._error(400, "expired_token")
            return
        if grant["decision"] == "denied":
            self._error(400, "access_denied")
            return
        if grant["decision"] is None:
            too_fast = grant["last_poll"] is not None and now - grant["last_poll"] < grant["interval"]
            grant["last_poll"] = now
            self._error(400, "slow_down" if too_fast else "authorization_pending")
            return
        grant["consumed"] = True
        token = self.state.new_browser_session(grant["email"])
        self._send(200, {"session_token": token, "token_type": "session", "user": {"email": grant["email"]}})

    @route("GET", "/auth/me")
    def me(self, body: dict[str, Any]) -> None:
        email = self._session_email()
        if email is None:
            self._error(401, "not signed in")
            return
        self._send(200, {"id": str(uuid.uuid4()), "email": email, "organization_id": self.state.organization_id})

    @route("POST", "/devices/register")
    def device_register(self, body: dict[str, Any]) -> None:
        if self._session_email() is None:
            self._error(401, "not signed in")
            return
        if not self.state.user_role_can_register:
            self._error(403, "Technician role required")
            return
        hwid = body.get("hwid", "")
        device_id = self.state.device_ids_by_hwid.setdefault(hwid, str(uuid.uuid4()))
        token = "devtok_" + secrets.token_urlsafe(24)
        self.state.devices[token] = device_id
        self._send(201, {"device_id": device_id, "device_token": token, "hwid_resets_remaining": 1})

    @route("GET", "/licenses")
    def license_list(self, body: dict[str, Any]) -> None:
        if self._session_email() is None:
            self._error(401, "not signed in")
            return
        self._send(200, [
            {
                "id": lic.id, "license_ref": lic.license_ref, "tier": lic.tier, "features": lic.features,
                "expires_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(lic.expires_at)),
                "offline_until": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 7 * 86400)),
                "is_active": lic.is_active, "organization_id": self.state.organization_id, "device_id": lic.device_id,
            }
            for lic in self.state.licenses
        ])

    @route("POST", "/licenses/activate")
    def license_activate(self, body: dict[str, Any]) -> None:
        if self._session_email() is None:
            self._error(401, "not signed in")
            return
        device_id = self.state.devices.get(body.get("device_token", ""))
        lic = next((x for x in self.state.licenses if x.license_ref == body.get("license_ref")), None)
        if device_id is None or lic is None or not lic.is_active or lic.expires_at <= time.time():
            self._error(403, "device token, license or tier mismatch")
            return
        if lic.device_id is not None and lic.device_id != device_id:
            self._error(409, "license bound to another device")
            return
        lic.device_id = device_id
        now = int(time.time())
        offline_until = now + self.state.offline_days * 86400
        ticket = self.state.sign_ticket({
            "iss": "universal-can-cloud", "aud": "diagnostic-desktop-app", "kid": "v1",
            "license_id": lic.license_ref, "organization_id": self.state.organization_id, "device_id": device_id,
            "tier": lic.tier, "features": lic.features, "iat": now, "exp": int(lic.expires_at),
            "offline_until": offline_until, "schema_version": 1, "nonce": body["nonce"],
        })
        self._send(200, {"license_token": ticket, "expires_at": str(int(lic.expires_at)),
                         "offline_until": str(offline_until)})


    @route("POST", "/licenses/refresh")
    def license_refresh(self, body: dict[str, Any]) -> None:
        # No session: device credential only; never claims a free seat.
        device_id = self.state.devices.get(body.get("device_token", ""))
        hwid_device = self.state.device_ids_by_hwid.get(body.get("hwid", ""))
        if device_id is None or hwid_device != device_id or self.state.refresh_reject:
            self._error(403, self.state.refresh_reject or "DEVICE_REJECTED")
            return
        lic = next((x for x in self.state.licenses if x.license_ref == body.get("license_ref")), None)
        if lic is None:
            self._error(403, "LICENSE_NOT_FOUND")
            return
        if not lic.is_active:
            self._error(403, "LICENSE_REVOKED")
            return
        if lic.device_id != device_id:
            self._error(403, "SEAT_NOT_HELD")
            return
        now = int(time.time())
        offline_until = now + self.state.offline_days * 86400
        ticket = self.state.sign_ticket({
            "iss": "universal-can-cloud", "aud": "diagnostic-desktop-app", "kid": "v1",
            "license_id": lic.license_ref, "organization_id": self.state.organization_id, "device_id": device_id,
            "tier": lic.tier, "features": lic.features, "iat": now, "exp": int(lic.expires_at),
            "offline_until": offline_until, "schema_version": 1, "nonce": body["nonce"],
        })
        self._send(200, {"license_token": ticket, "expires_at": str(int(lic.expires_at)),
                         "offline_until": str(offline_until)})

class FakeCloud:
    """Context manager: start the fake on an ephemeral port."""

    def __init__(self) -> None:
        self.state = FakeCloudState()
        handler = type("BoundFakeCloudHandler", (FakeCloudHandler,), {"state": self.state})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> FakeCloud:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

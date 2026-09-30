"""Mechanic-flow sign-in (Aşama 3): PKCE, deep link, device code, license bootstrap.

Runs the real CloudClient + LicenseFlow against ``FakeCloud`` (real HTTP on
127.0.0.1, Ed25519-signed tickets). CI/simulator evidence only: nothing here
touches Windows DPAPI, the registry or a real browser.
"""

from __future__ import annotations

import time
import urllib.parse
from http.server import HTTPServer
from types import SimpleNamespace
from typing import Any

import pytest

from src.core.errors import SecurityError
from src.safety.secret_provider import EphemeralSecretBackend
from src.security.cloud import desktop_auth as da
from src.security.cloud.client import CloudClient, CloudConfig
from src.security.cloud.license_flow import LicenseFlow
from tests.unit.fake_cloud_auth import FakeCloud, FakeLicense

HWID = "a" * 64


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


@pytest.fixture
def client(cloud) -> CloudClient:
    return CloudClient(config=CloudConfig(base_url=cloud.base_url, max_retries=0), secret_provider=EphemeralSecretBackend())


@pytest.fixture
def flow(cloud, client) -> LicenseFlow:
    return LicenseFlow(client, public_key=cloud.state.ticket_signer.public_key())


@pytest.fixture
def authorizer(client, flow) -> da.DesktopAuthorizer:
    return da.DesktopAuthorizer(client, flow, hwid_provider=lambda: HWID)


# ---------------------------------------------------------------------------
# PKCE + browser URL
# ---------------------------------------------------------------------------


def test_pkce_matches_rfc7636_appendix_b() -> None:
    assert da.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_session_repr_hides_secrets() -> None:
    session = da.PkceSession.new()
    assert session.verifier not in repr(session) and session.state not in repr(session)
    assert len(session.verifier) >= 43


def test_browser_url_carries_only_public_values() -> None:
    session = da.PkceSession.new()
    url = da.build_browser_login_url("https://ucanlab.org", 47820, session, "f" * 64)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert set(query) == {"port", "state", "hwid", "code_challenge"}
    assert session.verifier not in url
    assert query["hwid"] == ["f" * 16]
    with pytest.raises(SecurityError):
        da.build_browser_login_url("https://ucanlab.org", 5000, session, "f" * 64)


# ---------------------------------------------------------------------------
# ucanlab:// deep link
# ---------------------------------------------------------------------------

_CODE = "c" * 43
_STATE = "s" * 32


def _link(**overrides: str) -> str:
    params = {"code": _CODE, "state": _STATE, "port": "47821", **overrides}
    return "ucanlab://auth/callback?" + urllib.parse.urlencode(params)


def test_parse_deep_link_accepts_contract_shape() -> None:
    cb = da.parse_deep_link(_link())
    assert (cb.code, cb.state, cb.port) == (_CODE, _STATE, 47821)
    assert _CODE not in repr(cb)


@pytest.mark.parametrize(
    "url",
    [
        "https://auth/callback?code=x",
        "ucanlab://evil/callback?" + urllib.parse.urlencode({"code": _CODE, "state": _STATE, "port": "47820"}),
        "ucanlab://auth/other?" + urllib.parse.urlencode({"code": _CODE, "state": _STATE, "port": "47820"}),
        _link() + "#frag",
        _link(port="5000"),
        _link(port="4782O"),
        _link(code="bad code!"),
        _link(state="short"),
        _link() + "&extra=1",
        _link() + "&code=" + _CODE,
        "ucanlab://auth/callback?port=47820&state=" + _STATE,
        "",
        "ucanlab://auth/callback?" + "a" * 3000,
    ],
)
def test_parse_deep_link_rejects_anything_else(url: str) -> None:
    with pytest.raises(SecurityError):
        da.parse_deep_link(url)


def test_parse_deep_link_refuses_credentials_in_url() -> None:
    with pytest.raises(SecurityError) as exc:
        da.parse_deep_link(_link(session_token="sess_abcdefgh"))
    assert exc.value.code == "DEEP_LINK_CREDENTIAL"


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


class _Opener:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.status, self.error, self.urls = status, error, []

    def open(self, url: str, timeout: float) -> _Resp:
        self.urls.append(url)
        if self.error:
            raise self.error
        return _Resp(self.status)


def test_forward_deep_link_targets_loopback_only() -> None:
    opener = _Opener()
    assert da.forward_deep_link(_link(), opener=opener) is True  # type: ignore[arg-type]
    target = urllib.parse.urlsplit(opener.urls[0])
    assert (target.scheme, target.hostname, target.port, target.path) == ("http", "127.0.0.1", 47821, "/callback")
    assert urllib.parse.parse_qs(target.query) == {"code": [_CODE], "state": [_STATE]}


def test_forward_deep_link_reports_unreachable_app() -> None:
    assert da.forward_deep_link(_link(), opener=_Opener(error=ConnectionRefusedError())) is False  # type: ignore[arg-type]
    assert da.forward_deep_link(_link(), opener=_Opener(status=400)) is False  # type: ignore[arg-type]


def test_main_entry_forwards_deep_link(monkeypatch) -> None:
    import src.main as app_main

    seen: list[str] = []
    monkeypatch.setattr(da, "forward_deep_link", lambda url: seen.append(url) or True)
    assert app_main._handle_deep_link([_link()]) == 0
    assert seen == [_link()]
    assert app_main._handle_deep_link(["--cli"]) is None
    monkeypatch.setattr(da, "forward_deep_link", lambda url: (_ for _ in ()).throw(SecurityError("x", code="DEEP_LINK_INVALID")))
    assert app_main._handle_deep_link(["ucanlab://evil"]) == 2


# ---------------------------------------------------------------------------
# Loopback callback handler (state/CSRF + exchange + auto license)
# ---------------------------------------------------------------------------


def _run_callback(app: Any, query: dict[str, str], expected_state: str, verifier: str, redirect_uri: str) -> dict:
    from src.ui import desktop_app

    handler = desktop_app._DesktopAuthCallbackHandler
    handler.app_instance = app
    handler.expected_state = expected_state
    handler.code_verifier = verifier
    handler.redirect_uri = redirect_uri
    handler.auth_result = {"status": "pending"}
    server = HTTPServer(("127.0.0.1", 0), handler)
    try:
        import threading
        import urllib.request

        th = threading.Thread(target=server.handle_request, daemon=True)
        th.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/callback?" + urllib.parse.urlencode(query)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            opener.open(url, timeout=10).read()
        except urllib.error.HTTPError:
            pass
        th.join(timeout=10)
    finally:
        server.server_close()
        handler.code_verifier = ""
    return handler.auth_result


def test_loopback_callback_rejects_state_mismatch(cloud, client, authorizer) -> None:
    session = da.PkceSession.new()
    code = cloud.state.issue_auth_code(session.challenge)
    app = SimpleNamespace(cloud_client=client, desktop_authorizer=authorizer)
    result = _run_callback(app, {"code": code, "state": "x" * 32}, session.state, session.verifier,
                           "http://127.0.0.1:47820/callback")
    assert result["status"] == "error"
    assert code in cloud.state.auth_codes  # never exchanged
    assert not client.has_session_token()


def test_loopback_callback_signs_in_and_activates_license(cloud, client, authorizer) -> None:
    cloud.state.licenses.append(FakeLicense("lic_pro_1", tier="pro"))
    session = da.PkceSession.new()
    code = cloud.state.issue_auth_code(session.challenge)
    app = SimpleNamespace(cloud_client=client, desktop_authorizer=authorizer)
    result = _run_callback(app, {"code": code, "state": session.state}, session.state, session.verifier,
                           "http://127.0.0.1:47820/callback")
    assert result["status"] == "completed"
    assert result["login"]["status"] == "ready"
    assert result["login"]["license"]["entitlements"]["engineer"] is True
    assert client.has_session_token() and client.get_device_token()


def test_loopback_code_is_useless_without_verifier(cloud, client) -> None:
    session = da.PkceSession.new()
    code = cloud.state.issue_auth_code(session.challenge)
    with pytest.raises(SecurityError):
        da.exchange_authorization_code(client, code, da.pkce_verifier(), "http://127.0.0.1:47820/callback")
    token = da.exchange_authorization_code(client, code, session.verifier, "http://127.0.0.1:47820/callback")
    assert token.startswith("sess_")
    with pytest.raises(SecurityError):  # replay of a used code
        da.exchange_authorization_code(client, code, session.verifier, "http://127.0.0.1:47820/callback")


# ---------------------------------------------------------------------------
# Device code
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_device_code_full_flow(cloud, client, authorizer) -> None:
    cloud.state.licenses.append(FakeLicense("lic_fleet", tier="fleet", features=["j1939"]))
    clock = _Clock()
    login = da.DeviceCodeLogin(client, clock=clock)
    ticket = login.start()
    assert ticket.device_code not in repr(ticket) and ticket.verifier not in repr(ticket)
    view = ticket.public_view(clock())
    assert set(view) == {"user_code", "verification_uri", "verification_uri_complete", "expires_in", "interval"}

    # Too early: no network call at all.
    before = len(cloud.state.requests)
    assert login.poll(ticket).status is da.DevicePollStatus.PENDING
    assert len(cloud.state.requests) == before

    clock.t += ticket.interval
    assert login.poll(ticket).status is da.DevicePollStatus.PENDING  # server: authorization_pending

    cloud.state.approve_user_code(ticket.user_code)
    clock.t += ticket.interval
    cloud.state.device_grants[ticket.device_code]["last_poll"] = None
    result = login.poll(ticket)
    assert result.status is da.DevicePollStatus.COMPLETED and result.session_token
    assert result.session_token not in repr(result)

    outcome = authorizer.complete_login(result.session_token)
    assert outcome.status == "ready"
    ent = outcome.state.entitlements
    assert (ent.mechanic, ent.engineer, ent.active_tests) == (True, True, False)


def test_device_code_slow_down_backs_off(cloud, client) -> None:
    clock = _Clock()
    login = da.DeviceCodeLogin(client, clock=clock)
    ticket = login.start()
    clock.t += ticket.interval
    login.poll(ticket)
    # The fake saw a poll 0 s ago; force another network poll right away.
    ticket.next_poll_at = clock.t
    assert login.poll(ticket).status is da.DevicePollStatus.SLOW_DOWN
    assert ticket.interval == 10


def test_device_code_denied_and_expired(cloud, client) -> None:
    clock = _Clock()
    login = da.DeviceCodeLogin(client, clock=clock)
    ticket = login.start()
    cloud.state.approve_user_code(ticket.user_code, deny=True)
    clock.t += ticket.interval
    assert login.poll(ticket).status is da.DevicePollStatus.DENIED

    ticket2 = login.start()
    clock.t = ticket2.expires_at + 1
    assert login.poll(ticket2).status is da.DevicePollStatus.EXPIRED


def test_device_code_verifier_is_required(cloud, client) -> None:
    clock = _Clock()
    login = da.DeviceCodeLogin(client, clock=clock)
    ticket = login.start()
    cloud.state.approve_user_code(ticket.user_code)
    stolen = da.DeviceLoginTicket(
        user_code=ticket.user_code, verification_uri="", verification_uri_complete="",
        expires_at=ticket.expires_at, interval=5, device_code=ticket.device_code, verifier=da.pkce_verifier(),
    )
    assert login.poll(stolen).status is da.DevicePollStatus.FAILED
    clock.t += ticket.interval
    assert login.poll(ticket).status is da.DevicePollStatus.COMPLETED


# ---------------------------------------------------------------------------
# License selection + entitlements
# ---------------------------------------------------------------------------

NOW = 1_800_000_000.0


def _lic(ref: str, **kw: Any) -> dict[str, Any]:
    base = {"license_ref": ref, "tier": "pro", "is_active": True, "expires_at": "2027-06-01T00:00:00+00:00",
            "device_id": None}
    base.update(kw)
    return base


def test_select_license_prefers_seat_already_on_this_device() -> None:
    items = [_lic("free_ent", tier="enterprise"), _lic("mine", tier="starter", device_id="dev-1")]
    assert da.select_license(items, "dev-1", NOW) == "mine"


def test_select_license_best_free_seat_and_never_steals() -> None:
    items = [
        _lic("other_dev", tier="enterprise", device_id="dev-9"),
        _lic("starter", tier="starter"),
        _lic("fleet_old", tier="fleet", expires_at="2027-01-01T00:00:00Z"),
        _lic("fleet_new", tier="fleet", expires_at="2027-05-01T00:00:00Z"),
        _lic("inactive", tier="enterprise", is_active=False),
        _lic("expired", tier="enterprise", expires_at="2020-01-01T00:00:00Z"),
        _lic("garbage", expires_at="not a date"),
        "not a dict",  # type: ignore[list-item]
    ]
    assert da.select_license(items, "dev-1", NOW) == "fleet_new"
    assert da.select_license([_lic("x", device_id="dev-9")], "dev-1", NOW) is None
    assert da.select_license([], None, NOW) is None


@pytest.mark.parametrize(
    ("tier", "features", "engineer", "writes"),
    [
        ("starter", ["j1939"], False, False),
        ("pro", ["uds"], True, False),
        ("PRO", ["ACTIVE_TESTS"], True, True),
        ("heavy_duty_pro", ["active_tests"], True, True),
        ("mystery_tier", ["active_tests"], False, True),
        ("", [], False, False),
    ],
)
def test_entitlements_for(tier: str, features: list[str], engineer: bool, writes: bool) -> None:
    ent = da.entitlements_for(tier, features)
    assert ent.mechanic is True
    assert ent.engineer is engineer
    assert ent.active_tests is writes and ent.dtc_clear is writes


# ---------------------------------------------------------------------------
# Bootstrap edge cases
# ---------------------------------------------------------------------------


def test_complete_login_without_license(cloud, authorizer) -> None:
    token = cloud.state.new_browser_session()
    outcome = authorizer.complete_login(token)
    assert outcome.status == "no_license" and outcome.error_code == "NO_LICENSE"
    data = outcome.as_dict()
    assert "lisans" in data["message_tr"].lower() and data["message_en"]
    assert data["license"]["entitlements"]["mechanic"] is False


def test_complete_login_role_not_allowed(cloud, authorizer) -> None:
    cloud.state.user_role_can_register = False
    cloud.state.licenses.append(FakeLicense("lic_1"))
    outcome = authorizer.complete_login(cloud.state.new_browser_session())
    assert outcome.status == "error" and outcome.error_code == "ROLE_NOT_ALLOWED"


def test_complete_login_rejects_bad_token(authorizer) -> None:
    outcome = authorizer.complete_login("bad token\r\nX-Injected: 1")
    assert outcome.status == "error" and outcome.error_code == "SIGN_IN_FAILED"


def test_complete_login_reregisters_revoked_device(cloud, client, authorizer) -> None:
    cloud.state.licenses.append(FakeLicense("lic_1"))
    assert authorizer.complete_login(cloud.state.new_browser_session()).status == "ready"
    cloud.state.revoke_device_tokens()  # e.g. HWID reset on the web
    assert authorizer.complete_login(cloud.state.new_browser_session()).status == "ready"


def test_complete_login_seat_race_is_explained(cloud, client, flow, authorizer, monkeypatch) -> None:
    lic = FakeLicense("lic_1")
    cloud.state.licenses.append(lic)
    # The seat is free when listed, then taken by another machine before activation.
    original = da.select_license

    def _pick_then_steal(items, device_id, now):
        ref = original(items, device_id, now)
        lic.device_id = "someone-else"
        return ref

    monkeypatch.setattr(da, "select_license", _pick_then_steal)
    outcome = authorizer.complete_login(cloud.state.new_browser_session())
    assert outcome.status == "error" and outcome.error_code == "LICENSE_IN_USE"


def test_cloud_unreachable_on_first_login(client, flow) -> None:
    offline_client = CloudClient(config=CloudConfig(base_url="http://127.0.0.1:9", max_retries=0),
                                 secret_provider=EphemeralSecretBackend())
    auth = da.DesktopAuthorizer(offline_client, LicenseFlow(offline_client, public_key=flow.public_key),
                                hwid_provider=lambda: HWID)
    outcome = auth.complete_login("sess_offline_token_123")
    assert outcome.status == "error" and outcome.error_code == "CLOUD_UNREACHABLE"
    assert "internet" in outcome.as_dict()["message_tr"].lower()


# ---------------------------------------------------------------------------
# Offline window, expiry, clock manipulation (review items)
# ---------------------------------------------------------------------------


def _ticket(client: CloudClient) -> str:
    return client._secrets.get_secret("CLOUD_LICENSE_TICKET").decode("utf-8")


def test_offline_use_until_offline_until_then_clear_message(cloud, client, flow, authorizer) -> None:
    cloud.state.licenses.append(FakeLicense("lic_1"))
    assert authorizer.complete_login(cloud.state.new_browser_session()).status == "ready"
    ticket = _ticket(client)
    device_id = client.get_device_id()

    # Six days later, no network: still valid.
    six_days = time.time() + 6 * 86400
    later = LicenseFlow(client, public_key=flow.public_key, boot_realtime=six_days,
                        boot_monotonic=time.monotonic(), clock_provider=lambda: six_days)
    claims = later.verify_cloud_ticket(ticket, expected_device_id=device_id, is_offline=True)
    state = da.license_state_from_claims(claims, six_days)
    assert state.status == "active"
    assert 0 < state.offline_seconds_left <= 86400 + 5

    # Eight days later: the offline window has closed.
    eight_days = time.time() + 8 * 86400
    expired = LicenseFlow(client, public_key=flow.public_key, boot_realtime=eight_days,
                          boot_monotonic=time.monotonic(), clock_provider=lambda: eight_days)
    with pytest.raises(Exception) as exc:
        expired.verify_cloud_ticket(ticket, expected_device_id=device_id, is_offline=True)
    state = da.license_state_from_error(exc.value.code)  # type: ignore[attr-defined]
    assert state.status == "expired"
    assert "yeniden giriş" in state.as_dict()["message_tr"]


def test_clock_rollback_is_reported_not_trusted(cloud, client, flow, authorizer) -> None:
    cloud.state.licenses.append(FakeLicense("lic_1"))
    assert authorizer.complete_login(cloud.state.new_browser_session()).status == "ready"
    ticket = _ticket(client)
    clock = {"now": time.time()}
    guarded = LicenseFlow(client, public_key=flow.public_key, clock_provider=lambda: clock["now"])
    guarded.verify_cloud_ticket(ticket, is_offline=True)  # anchors the high-water mark
    clock["now"] -= 3 * 86400  # user winds the clock back to stretch the offline window
    with pytest.raises(Exception) as exc:
        guarded.verify_cloud_ticket(ticket, is_offline=True)
    assert exc.value.code == "CLOCK_ROLLBACK_DETECTED"  # type: ignore[attr-defined]
    assert da.license_state_from_error(exc.value.code).status == "clock_problem"  # type: ignore[attr-defined]


def test_refresh_online_rolls_window_and_survives_offline(cloud, client, authorizer) -> None:
    cloud.state.licenses.append(FakeLicense("lic_1"))
    assert authorizer.complete_login(cloud.state.new_browser_session()).status == "ready"
    first = _ticket(client)
    time.sleep(1.1)  # iat has 1 s resolution
    refreshed = authorizer.refresh_online()
    assert refreshed is not None and refreshed.status == "ready"
    assert _ticket(client) != first

    client.set_base_url("http://127.0.0.1:9")  # cloud gone
    assert authorizer.refresh_online() is None
    assert _ticket(client)  # the stored ticket is kept for offline use


def test_refresh_online_noop_without_session(client, authorizer) -> None:
    assert authorizer.refresh_online() is None


def test_user_message_fallbacks() -> None:
    assert da.user_message("CLOCK_SOMETHING_NEW") == da.user_message("CLOCK_ROLLBACK_DETECTED")
    assert da.user_message("TOTALLY_UNKNOWN") == da.user_message("LICENSE_INVALID")
    assert da.user_message(None) == da.user_message("SIGN_IN_FAILED")


# ---------------------------------------------------------------------------
# Bridge (what the React start screen calls)
# ---------------------------------------------------------------------------


def _bridge(client: CloudClient, flow: LicenseFlow, authorizer: da.DesktopAuthorizer, clock: _Clock | None = None):
    from src.ui.desktop_app import DesktopApiBridge

    app = SimpleNamespace(
        cloud_client=client,
        license_flow=flow,
        desktop_authorizer=authorizer,
        device_login=da.DeviceCodeLogin(client, clock=clock or _Clock()),
        _device_login_ticket=None,
        _secret_provider=client._secrets,
    )
    return DesktopApiBridge(app), app  # type: ignore[arg-type]


def test_bridge_state_and_device_login(cloud, client, flow, authorizer) -> None:
    cloud.state.licenses.append(FakeLicense("lic_1", tier="starter", features=["j1939"]))
    clock = _Clock()
    bridge, app = _bridge(client, flow, authorizer, clock)

    state = bridge.auth_get_state()
    assert state["loginRequired"] is True and state["license"]["status"] == "missing"

    started = bridge.auth_start_device_login()
    assert started["success"] and "device_code" not in started and "verifier" not in started
    clock.t += 5
    assert bridge.auth_poll_device_login()["status"] == "pending"

    cloud.state.approve_user_code(started["user_code"])
    clock.t += 10
    cloud.state.device_grants[app._device_login_ticket.device_code]["last_poll"] = None
    done = bridge.auth_poll_device_login()
    assert done["status"] == "completed" and done["login"]["status"] == "ready"
    assert done["login"]["license"]["entitlements"]["engineer"] is False

    state = bridge.auth_get_state()
    assert state["loginRequired"] is False and state["license"]["status"] == "active"
    assert state["license"]["offline_days_left"] in (6, 7)
    assert bridge.auth_poll_device_login() == {"success": False, "status": "idle"}
    assert bridge.auth_refresh_license()["license"]["status"] == "active"


def test_bridge_device_login_denied_and_cancel(cloud, client, flow, authorizer) -> None:
    clock = _Clock()
    bridge, app = _bridge(client, flow, authorizer, clock)
    started = bridge.auth_start_device_login()
    cloud.state.approve_user_code(started["user_code"], deny=True)
    clock.t += 5
    denied = bridge.auth_poll_device_login()
    assert denied["success"] is False and denied["error_code"] == "DEVICE_CODE_DENIED"
    assert denied["message_tr"]
    bridge.auth_start_device_login()
    assert bridge.auth_cancel_device_login() == {"success": True} and app._device_login_ticket is None


def test_bridge_start_reports_offline(flow, authorizer) -> None:
    offline = CloudClient(config=CloudConfig(base_url="http://127.0.0.1:9", max_retries=0),
                          secret_provider=EphemeralSecretBackend())
    bridge, _ = _bridge(offline, flow, authorizer)
    res = bridge.auth_start_device_login()
    assert res["success"] is False and res["error_code"] == "CLOUD_UNREACHABLE"
    assert "internet" in res["message_tr"].lower()


def test_bridge_state_with_corrupt_ticket(client, flow, authorizer) -> None:
    client.store_license_ticket("not.a.ticket")
    bridge, _ = _bridge(client, flow, authorizer)
    state = bridge.auth_get_state()
    assert state["loginRequired"] is True and state["license"]["status"] == "invalid"


# ---------------------------------------------------------------------------
# Launcher sign-in (runs before the license gate; the gate itself is unchanged)
# ---------------------------------------------------------------------------


@pytest.fixture
def launcher_auth(cloud, client, monkeypatch, tmp_path):
    from src.launcher import auth as launcher_auth_mod
    from src.ui import desktop_app

    monkeypatch.setattr(launcher_auth_mod, "default_license_hwm_path", lambda: tmp_path / "hwm.txt")
    monkeypatch.setattr(launcher_auth_mod.LauncherAuthManager, "hwid", property(lambda self: HWID))

    # Bind an ephemeral port instead of 47820-47822: other suites leave those in
    # TIME_WAIT on Linux, which would make this test order-dependent.
    bound: dict[str, int] = {}

    def _bind():
        server = desktop_app._ExclusiveHTTPServer(("127.0.0.1", 0), desktop_app._DesktopAuthCallbackHandler)
        bound["port"] = server.server_address[1]
        monkeypatch.setattr(da, "LOOPBACK_PORTS", (bound["port"],))
        return server, bound["port"]

    monkeypatch.setattr(desktop_app, "_bind_loopback_server", _bind)
    manager = launcher_auth_mod.LauncherAuthManager(
        cloud_client=client, secret_provider=client._secrets, public_key=cloud.state.ticket_signer.public_key()
    )
    return manager


def _fake_browser(cloud):
    """Plays the web page: authorize with the page's challenge, redirect to loopback."""
    import threading
    import urllib.request

    def _open(url: str) -> bool:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert set(query) == {"port", "state", "hwid", "code_challenge"}
        port = int(query["port"][0])
        code = cloud.state.issue_auth_code(query["code_challenge"][0], port)
        callback = f"http://127.0.0.1:{port}/callback?" + urllib.parse.urlencode(
            {"code": code, "state": query["state"][0]}
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        threading.Thread(target=lambda: opener.open(callback, timeout=10).read(), daemon=True).start()
        return True

    return _open


def test_launcher_sign_in_via_browser(cloud, launcher_auth) -> None:
    cloud.state.licenses.append(FakeLicense("lic_1", tier="enterprise"))
    outcome = launcher_auth.sign_in(open_browser=_fake_browser(cloud), browser_timeout_s=10)
    assert outcome.status == "ready"
    assert outcome.state.entitlements.engineer is True
    status = launcher_auth.get_current_status()
    assert status.has_valid_license is True and status.tier == "enterprise"


def test_launcher_sign_in_falls_back_to_device_code(cloud, launcher_auth) -> None:
    cloud.state.licenses.append(FakeLicense("lic_1", tier="starter", features=["j1939"]))
    cloud.state.device_interval = 1
    shown: list[dict] = []

    def _show(view: dict) -> None:
        shown.append(view)
        cloud.state.approve_user_code(str(view["user_code"]))  # user approves on the phone

    outcome = launcher_auth.sign_in(open_browser=lambda url: False, browser_timeout_s=0.3, on_device_code=_show)
    assert outcome.status == "ready" and outcome.state.entitlements.engineer is False
    assert shown and "device_code" not in shown[0]


def test_launcher_sign_in_without_fallback_reports_failure(cloud, launcher_auth) -> None:
    outcome = launcher_auth.sign_in(open_browser=lambda url: False, browser_timeout_s=0.2)
    assert outcome.status == "error" and outcome.error_code == "SIGN_IN_FAILED"


def test_launcher_sign_in_offline_fails_fast(cloud, launcher_auth) -> None:
    launcher_auth.client.set_base_url("http://127.0.0.1:9")
    opened: list[str] = []
    started = time.monotonic()
    outcome = launcher_auth.sign_in(open_browser=opened.append, browser_timeout_s=30)
    assert outcome.error_code == "CLOUD_UNREACHABLE"
    assert opened == [] and time.monotonic() - started < 10


def test_launcher_status_carries_error_code(cloud, launcher_auth) -> None:
    launcher_auth.client.store_device_id("dev-x")
    launcher_auth.client.store_license_ticket("not.a.ticket")
    status = launcher_auth.get_current_status()
    assert status.has_valid_license is False and status.error_code


def test_launcher_device_code_denied(cloud, launcher_auth) -> None:
    cloud.state.device_interval = 1
    outcome = launcher_auth.sign_in(
        open_browser=lambda url: False, browser_timeout_s=0.2,
        on_device_code=lambda view: cloud.state.approve_user_code(str(view["user_code"]), deny=True),
    )
    assert outcome.error_code == "DEVICE_CODE_DENIED"


def _report(can_launch: bool, licensed: bool):
    from pathlib import Path

    from src.launcher.app import LauncherPreflightReport
    from src.launcher.auth import AuthStatus
    from src.launcher.updater import UpdateInfo

    return LauncherPreflightReport(
        can_launch=can_launch,
        prereqs=[],
        auth_status=AuthStatus(is_authenticated=licensed, has_valid_license=licensed, hwid="A" * 32),
        update_info=UpdateInfo(has_update=False, current_version="13.0.0", latest_version="13.0.0",
                               check_succeeded=True, manifest_verified=True),
        target_executable=Path("main.py"),
    )


@pytest.mark.parametrize(("sign_in_ok", "expected_rc"), [(True, 0), (False, 1)])
def test_launcher_main_signs_in_then_reruns_gate(monkeypatch, sign_in_ok: bool, expected_rc: int) -> None:
    from src.launcher import app as app_mod

    reports = [_report(False, False), _report(sign_in_ok, sign_in_ok)]
    launched: list[object] = []
    fake = SimpleNamespace(
        version="13.0.0",
        run_preflight=lambda: reports.pop(0),
        launch_main_app=lambda extra_args=None: launched.append(extra_args) or 0,
        auth_manager=SimpleNamespace(),
    )
    outcome = SimpleNamespace(status="ready" if sign_in_ok else "error",
                              as_dict=lambda: {"license": {"offline_days_left": 7}, "message_tr": "Giriş tamamlanamadı."})
    fake.auth_manager.sign_in = lambda **kw: outcome
    monkeypatch.setattr(app_mod, "UniversalCanLauncher", lambda *a, **kw: fake)
    monkeypatch.setattr(app_mod.sys, "argv", ["launcher"])
    monkeypatch.setattr(app_mod.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    assert app_mod.main() == expected_rc
    assert bool(launched) is sign_in_ok  # the core is reached only through a passing gate


# ---------------------------------------------------------------------------
# Windows URL protocol registration (fake winreg; real Windows is manual)
# ---------------------------------------------------------------------------


class _FakeKey:
    def __init__(self, store: dict, path: str) -> None:
        self.store, self.path = store, path

    def __enter__(self) -> _FakeKey:
        return self

    def __exit__(self, *a: object) -> None:
        return None


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def CreateKey(self, root: str, path: str) -> _FakeKey:  # noqa: N802 — winreg API name
        return _FakeKey(self.values, f"{root}\\{path}")

    def SetValueEx(self, key: _FakeKey, name: str, reserved: int, kind: int, value: str) -> None:  # noqa: N802
        self.values[(key.path, name)] = value


def test_url_protocol_registration_quotes_argument(tmp_path) -> None:
    from pathlib import Path

    from src.launcher.url_protocol import register_url_protocol

    reg = _FakeWinreg()
    exe = Path("C:/Program Files/UCanLab/UCanLab.exe")
    assert register_url_protocol(winreg_module=reg, executable=exe) is True
    command = reg.values[("HKCU\\Software\\Classes\\ucanlab\\shell\\open\\command", "")]
    assert command == f'"{exe}" "%1"'
    assert reg.values[("HKCU\\Software\\Classes\\ucanlab", "URL Protocol")] == ""


def test_url_protocol_registration_off_windows(monkeypatch) -> None:
    import sys

    from src.launcher.url_protocol import protocol_command, register_url_protocol

    monkeypatch.setattr(sys, "platform", "linux")
    assert register_url_protocol() is False
    assert protocol_command(__import__("pathlib").Path("py"), __import__("pathlib").Path("main.py")).endswith('"%1"')

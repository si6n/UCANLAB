"""Prove the auth-callback fix: a client disconnect must not corrupt the outcome.

Reproduces the reported failure. Before the fix, the success path and the
response write shared one try/except, so a browser that closed the tab before
the body drained (WinError 10053) produced:

  * auth_result["status"] = "error"  — a COMPLETED login reported as failed
  * a second write to the dead socket in the error path — the debugger stop

The disconnect is simulated deterministically by replacing the handler's
`wfile` with one that raises ConnectionAbortedError, which is exactly what the
socket layer does when the peer goes away mid-write. A raw socket-close test
would race the server loop and often never reach the handler at all.
"""

import io
import sys

sys.path.insert(0, "C:/Users/canak/Desktop/Universal-CAN-BUS-Tool")

from src.ui.desktop_app import _DesktopAuthCallbackHandler  # noqa: E402

# A deliberately FAKE, non-secret fixture value. Kept low-entropy and
# obviously synthetic so secret scanners do not flag it as a live
# credential: a real token never looks like this.
TOKEN = "tok_test_0000000000000000"


class BrokenWriter:
    """A wfile whose peer has vanished: every write fails like WinError 10053."""

    def write(self, _data):
        raise ConnectionAbortedError(10053, "An established connection was aborted by the software in your host machine")

    def flush(self):
        pass


class RecordingWriter:
    """Captures what the handler would send."""

    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    def flush(self):
        pass


class SilentHandler(_DesktopAuthCallbackHandler):
    """Handler whose response bookkeeping does not touch a real socket.

    BaseHTTPRequestHandler.send_response() calls log_request(), which reaches
    for self.server/self.requestline — absent when the handler is driven
    directly. Overriding those two keeps the test focused on the logic under
    test (outcome tracking + write safety) rather than on socket plumbing.
    """

    def log_request(self, code="-", size="-"):
        pass

    def log_error(self, *args, **kwargs):
        pass

    def log_message(self, *args, **kwargs):
        pass


class FakeCloudClient:
    def __init__(self, *, token=TOKEN, fail=False):
        self.token = token
        self.fail = fail
        self.stored = None

    def request(self, method, path, json_body=None):
        if self.fail:
            raise RuntimeError("token exchange exploded")

        class _Resp:
            def json_object(self_inner):
                return {"session_token": self.token}

        return _Resp()

    def store_session_token(self, tok):
        self.stored = tok

    def get_device_token(self):
        return "device-token-present"  # skip auto device registration


class FakeApp:
    def __init__(self, client):
        self.cloud_client = client
        self.license_flow = None


def make_handler(path, *, app, expected_state="s", verifier="v", redirect_uri="http://127.0.0.1:47820/callback", writer):
    """Build a handler without a real socket, wired to `writer`."""
    h = SilentHandler.__new__(SilentHandler)
    h.path = path
    h.request = None
    h.requestline = f"GET {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 54321)
    h.server = None
    h.rfile = io.BytesIO(b"")
    h.wfile = writer
    h.close_connection = True
    h.request_version = "HTTP/1.1"
    h.command = "GET"
    # BaseHTTPRequestHandler needs these to build the status line.
    h.protocol_version = "HTTP/1.1"
    h.sys_version = ""
    h.server_version = "TestServer/1.0"
    h.error_message_format = "%(code)d %(message)s"
    h.error_content_type = "text/html; charset=utf-8"
    # Per-request state lives on the class (set by cloud_start_web_login).
    _DesktopAuthCallbackHandler.app_instance = app
    _DesktopAuthCallbackHandler.expected_state = expected_state
    _DesktopAuthCallbackHandler.code_verifier = verifier
    _DesktopAuthCallbackHandler.redirect_uri = redirect_uri
    _DesktopAuthCallbackHandler.auth_result = {"status": "pending", "error": None}
    return h


def results():
    return _DesktopAuthCallbackHandler.auth_result


def case_disconnect_during_success_response() -> bool:
    """THE REPORTED BUG: the client vanishes while the success page is written.

    The token is already stored at that point, so the login MUST remain
    completed and no second response may be attempted.
    """
    client = FakeCloudClient()
    app = FakeApp(client)
    handler = make_handler("/callback?code=auth-code-123&state=s", app=app, writer=BrokenWriter())

    handler.do_GET()  # must not raise

    res = results()
    ok = res["status"] == "completed" and res["error"] is None and client.stored == TOKEN
    print(f"  auth_result={res} stored={client.stored!r}")
    print(f"  [{'PASS' if ok else 'FAIL'}] completed login survives a disconnect mid-response")
    return ok


def case_disconnect_on_early_error_path() -> bool:
    """A disconnect on the state-mismatch path must not raise either."""
    client = FakeCloudClient()
    handler = make_handler("/callback?code=c&state=WRONG", app=FakeApp(client), writer=BrokenWriter())

    handler.do_GET()  # must not raise

    res = results()
    ok = res["status"] == "error" and client.stored is None
    print(f"  auth_result={res}")
    print(f"  [{'PASS' if ok else 'FAIL'}] disconnect on the error path is swallowed")
    return ok


def case_real_auth_failure() -> bool:
    """A genuine exchange failure must still be reported as an error, with 400."""
    client = FakeCloudClient(fail=True)
    writer = RecordingWriter()
    handler = make_handler("/callback?code=c&state=s", app=FakeApp(client), writer=writer)

    handler.do_GET()

    res = results()
    head = writer.data.decode("utf-8", "replace")
    ok = res["status"] == "error" and client.stored is None and " 400 " in head.split("\r\n")[0]
    print(f"  status line={head.splitlines()[0]!r} auth_result={res}")
    print(f"  [{'PASS' if ok else 'FAIL'}] real auth failure still returns 400 + error")
    return ok


def case_state_mismatch() -> bool:
    """The CSRF guard must still reject a wrong state with 400."""
    client = FakeCloudClient()
    writer = RecordingWriter()
    handler = make_handler("/callback?code=c&state=WRONG", app=FakeApp(client), writer=writer)

    handler.do_GET()

    res = results()
    head = writer.data.decode("utf-8", "replace")
    ok = res["status"] == "error" and client.stored is None and " 400 " in head.split("\r\n")[0]
    print(f"  status line={head.splitlines()[0]!r} auth_result={res}")
    print(f"  [{'PASS' if ok else 'FAIL'}] state mismatch still rejected with 400")
    return ok


def case_error_param_escaped() -> bool:
    """The provider-supplied error string must be HTML-escaped, not echoed raw."""
    client = FakeCloudClient()
    writer = RecordingWriter()
    handler = make_handler("/callback?state=s&error=%3Cscript%3Ealert(1)%3C/script%3E", app=FakeApp(client), writer=writer)

    handler.do_GET()

    body = writer.data.decode("utf-8", "replace")
    ok = "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body
    print(f"  raw <script> present: {'<script>alert(1)</script>' in body}")
    print(f"  [{'PASS' if ok else 'FAIL'}] provider error string is escaped")
    return ok


def case_duplicate_callback_after_success() -> bool:
    """A late second callback must not downgrade a completed login."""
    client = FakeCloudClient()
    handler = make_handler("/callback?code=c&state=s", app=FakeApp(client), writer=RecordingWriter())
    handler.do_GET()
    assert results()["status"] == "completed"

    _DesktopAuthCallbackHandler._mark_failed("late duplicate callback")
    res = results()
    ok = res["status"] == "completed" and res["error"] is None
    print(f"  auth_result={res}")
    print(f"  [{'PASS' if ok else 'FAIL'}] late duplicate cannot downgrade success")
    return ok


def case_legacy_token_param_rejected() -> bool:
    """A legacy ?token= callback must NOT be accepted as a session."""
    client = FakeCloudClient()
    writer = RecordingWriter()
    handler = make_handler("/callback?state=s&token=stolen-token-value", app=FakeApp(client), writer=writer)

    handler.do_GET()

    res = results()
    ok = res["status"] == "error" and client.stored is None
    print(f"  auth_result={res} stored={client.stored!r}")
    print(f"  [{'PASS' if ok else 'FAIL'}] legacy token param is refused")
    return ok


def case_success_response_is_sent() -> bool:
    """The happy path must still deliver the success page with 200."""
    client = FakeCloudClient()
    writer = RecordingWriter()
    handler = make_handler("/callback?code=c&state=s", app=FakeApp(client), writer=writer)

    handler.do_GET()

    body = writer.data.decode("utf-8", "replace")
    ok = (
        results()["status"] == "completed"
        and " 200 " in body.split("\r\n")[0]
        and "Giriş Başarılı" in body
        and client.stored == TOKEN
    )
    print(f"  status line={body.splitlines()[0]!r} stored={client.stored!r}")
    print(f"  [{'PASS' if ok else 'FAIL'}] happy path delivers 200 + success page")
    return ok


def main() -> int:
    print("Auth callback regression tests\n")
    cases = [
        ("Client disconnects mid-success-response (the reported WinError 10053)", case_disconnect_during_success_response),
        ("Client disconnects on the early error path", case_disconnect_on_early_error_path),
        ("Genuine token-exchange failure", case_real_auth_failure),
        ("CSRF state mismatch", case_state_mismatch),
        ("Provider error string escaping", case_error_param_escaped),
        ("Duplicate callback after success", case_duplicate_callback_after_success),
        ("Legacy ?token= param refused", case_legacy_token_param_rejected),
        ("Happy path still returns 200", case_success_response_is_sent),
    ]
    outcomes = []
    for i, (label, fn) in enumerate(cases, 1):
        print(f"{i}. {label}")
        try:
            outcomes.append(bool(fn()))
        except Exception as exc:  # a raised exception IS the reported bug
            print(f"  [FAIL] raised {type(exc).__name__}: {exc}")
            outcomes.append(False)
        print()

    print("=" * 62)
    if all(outcomes):
        print(f"RESULT: ALL {len(outcomes)} PASSED")
        return 0
    print(f"RESULT: {outcomes.count(False)} FAILED of {len(outcomes)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

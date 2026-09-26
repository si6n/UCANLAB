"""Verify the loopback callback port is now exclusively owned.

Security review finding: HTTPServer.allow_reuse_address == 1 maps to
SO_REUSEADDR, which on Windows lets a SECOND process bind an address that is
already bound and listening. Measured consequences before the fix:

  * app first, squatter second -> squatter bind SUCCEEDS (port shared);
  * squatter first, app second -> the squatter (first binder) receives the
    browser's redirect, i.e. the authorization code.

PKCE does not cover this: the attacker supplies their own challenge/verifier,
so a code delivered to their listener is redeemable for the victim's session.

This drives the REAL `_bind_loopback_server` from desktop_app.py — not a
replica — so the test fails if the production code regresses.
"""

import socket
import sys
import threading

sys.path.insert(0, "C:/Users/canak/Desktop/Universal-CAN-BUS-Tool")

from src.ui.desktop_app import (  # noqa: E402
    _DESKTOP_LOOPBACK_PORTS,
    _SO_EXCLUSIVEADDRUSE,
    _bind_loopback_server,
)

HOST = "127.0.0.1"


def squat(port: int):
    """A hostile local process holding an allowlisted port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, port))
    s.listen(1)
    return s


def case_platform_support() -> bool:
    print("1. Platform support")
    print(f"   SO_EXCLUSIVEADDRUSE available: {_SO_EXCLUSIVEADDRUSE is not None}")
    if _SO_EXCLUSIVEADDRUSE is None:
        print("   [SKIP] non-Windows: POSIX SO_REUSEADDR already forbids two live listeners")
        return True
    print("   [PASS] exclusivity is enforceable on this platform")
    return True


def case_app_binds_cleanly() -> bool:
    """The normal path must still work."""
    print("\n2. App binds a free allowlisted port")
    srv, port = _bind_loopback_server()
    try:
        ok = port in _DESKTOP_LOOPBACK_PORTS
        print(f"   bound {HOST}:{port} (allowlist {_DESKTOP_LOOPBACK_PORTS})")
        print(f"   [{'PASS' if ok else 'FAIL'}] app takes an allowlisted port")
        return ok
    finally:
        srv.server_close()


def case_squatter_cannot_join() -> bool:
    """THE FIX: while the app holds the port, a squatter must be refused."""
    print("\n3. App holds the port; a squatter tries to join")
    srv, port = _bind_loopback_server()
    try:
        try:
            atk = squat(port)
        except OSError as exc:
            print(f"   squatter refused on {port}: errno={exc.errno}")
            print("   [PASS] the port is exclusively owned")
            return True
        atk.close()
        print(f"   squatter BOUND {port} alongside the app  <-- hole still open")
        print("   [FAIL] port is shared")
        return False
    finally:
        srv.server_close()


def case_app_refuses_squatted_port() -> bool:
    """A squatter holding the port must not be handed the code."""
    print("\n4. Squatter holds an allowlisted port; the app must not use it")
    held = squat(_DESKTOP_LOOPBACK_PORTS[0])
    try:
        try:
            srv, port = _bind_loopback_server()
        except OSError as exc:
            print(f"   app could not bind any port: {exc}")
            print("   [PASS] no code would be issued (caller reports an error)")
            return True
        srv.server_close()
        moved = port != _DESKTOP_LOOPBACK_PORTS[0]
        print(f"   app bound {port} instead of the squatted {_DESKTOP_LOOPBACK_PORTS[0]}")
        print(f"   [{'PASS' if moved else 'FAIL'}] app never shares the squatter's port")
        return moved
    finally:
        held.close()


def case_squatter_on_all_ports() -> bool:
    """With every allowlisted port held, the app must fail closed."""
    print("\n5. Every allowlisted port squatted (hostile host)")
    held = []
    try:
        for p in _DESKTOP_LOOPBACK_PORTS:
            try:
                held.append(squat(p))
            except OSError:
                print(f"   could not squat {p} (already in use) — continuing")
        if not held:
            print("   [SKIP] no port could be squatted in this environment")
            return True
        try:
            srv, port = _bind_loopback_server()
            srv.server_close()
            print(f"   app bound {port} despite squatters  <-- unexpected")
            print("   [FAIL] app found a port it should not have")
            return False
        except OSError:
            print("   app refused to bind  <-- fail-closed")
            print("   [PASS] no authorization code is issued to a foreign listener")
            return True
    finally:
        for s in held:
            s.close()


def case_serves_its_own_callback() -> bool:
    """End-to-end: the app's own listener must still receive the redirect."""
    print("\n6. The app's listener serves its own callback")
    import http.client

    from src.ui.desktop_app import _DesktopAuthCallbackHandler as H

    srv, port = _bind_loopback_server()
    H.expected_state = "test-state"
    H.auth_result = {"status": "pending", "error": None}
    H.app_instance = None
    H.code_verifier = ""
    H.redirect_uri = ""
    threading.Thread(target=srv.handle_request, daemon=True).start()

    try:
        conn = http.client.HTTPConnection(HOST, port, timeout=5)
        conn.request("GET", "/callback?state=test-state&code=x")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        status = resp.status
        print(f"   HTTP {status} from the app's own listener")
        # No PKCE context -> 400 is the correct, fail-closed answer; what
        # matters is that the APP answered, not a foreign process.
        ok = status in (400, 200)
        print(f"   [{'PASS' if ok else 'FAIL'}] app received its own callback")
        return ok
    except Exception as exc:
        print(f"   request failed: {exc}")
        return False
    finally:
        srv.server_close()


def main() -> int:
    print("Loopback callback port ownership tests\n")
    cases = [
        ("Platform support", case_platform_support),
        ("App binds cleanly", case_app_binds_cleanly),
        ("Squatter cannot join the app", case_squatter_cannot_join),
        ("App refuses a squatted port", case_app_refuses_squatted_port),
        ("All ports squatted -> fail closed", case_squatter_on_all_ports),
        ("App serves its own callback", case_serves_its_own_callback),
    ]
    outcomes = []
    for _label, fn in cases:
        try:
            outcomes.append(bool(fn()))
        except Exception as exc:
            print(f"   [FAIL] raised {type(exc).__name__}: {exc}")
            outcomes.append(False)

    print("\n" + "=" * 64)
    passed = outcomes.count(True)
    print(f"RESULT: {passed}/{len(outcomes)} passed")
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())

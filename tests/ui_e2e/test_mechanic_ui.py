"""Aşama 7: the real React screens driven against the real bridge (simulator).

The built frontend (``src/ui/frontend/dist``) is served over HTTP. An init
script gives the page a ``window.pywebview.api`` whose methods forward every
call over ``fetch`` to a real ``DesktopApiBridge`` around a real
``UniversalCanDesktopApp`` — the same object graph pywebview exposes on
Windows. Only the license read is pinned (no cloud in tests), so this is the
whole mechanic flow on the simulator: mode → vehicle → adapter → listen-only
test → scan → result → customer report.

Skipped when Playwright, a Chromium build or the frontend bundle is missing
(e.g. in CI). Screenshots go to ``UCANLAB_UI_SCREENSHOTS_DIR`` when set.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

DIST = Path(__file__).resolve().parents[2] / "src" / "ui" / "frontend" / "dist"
if not (DIST / "index.html").is_file():
    pytest.skip("frontend bundle not built (npm run build)", allow_module_level=True)

PRO_LICENSE = {
    "status": "active",
    "entitlements": {"tier": "pro", "mechanic": True, "engineer": True, "active_tests": False, "dtc_clear": False},
    "offline_seconds_left": 604800, "offline_days_left": 7, "error_code": None, "message_tr": "", "message_en": "",
}


def _serve(bridge: Any) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(DIST), **kwargs)

        def log_message(self, *args: Any) -> None:  # keep test output clean
            pass

        def do_POST(self) -> None:  # noqa: N802 — http.server naming
            name = self.path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(length) or b"[]")
            method = getattr(bridge, name, None)
            if name.startswith("_") or not callable(method):
                self.send_error(404)
                return
            body = json.dumps(method(*args), default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _init_script(names: list[str]) -> str:
    return (
        "window.pywebview = { api: {} };"
        f"for (const name of {json.dumps(names)}) {{"
        "  window.pywebview.api[name] = (...args) => fetch('/api/' + name, {method: 'POST', body: JSON.stringify(args)})"
        "    .then((r) => r.json());"
        "}"
    )


@pytest.fixture
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import src.ui.desktop_app as da
    from src.ui.mechanic_prefs import MechanicPrefsStore

    monkeypatch.setattr(da, "_app_data_root", lambda: tmp_path)
    monkeypatch.setattr(da.DesktopApiBridge, "SCAN_LISTEN_S_SIMULATOR", 0.5)
    app = da.UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app.mechanic_prefs = MechanicPrefsStore(tmp_path / "mechanic_prefs.json")
    app.connection_wizard._window_s = 0.3
    bridge = da.DesktopApiBridge(app)
    state = {"success": True, "signedIn": True, "loginRequired": False, "license": PRO_LICENSE}
    bridge.auth_get_state = lambda: dict(state)  # type: ignore[method-assign]
    bridge.auth_refresh_license = lambda: dict(state)  # type: ignore[method-assign]
    server = _serve(bridge)
    names = sorted(da.DesktopApiBridge.BRIDGE_RISK_MANIFEST)
    shots_dir = Path(os.environ["UCANLAB_UI_SCREENSHOTS_DIR"]) if os.environ.get("UCANLAB_UI_SCREENSHOTS_DIR") else None
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=os.environ.get("UCANLAB_CHROMIUM") or None)
            except Exception as exc:  # noqa: BLE001 — no browser build available
                pytest.skip(f"Chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 900, "height": 900}, locale="tr-TR")
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(_init_script(names))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html")

            def shot(name: str) -> None:
                if shots_dir is not None:
                    shots_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(shots_dir / f"{name}.png"), full_page=True)

            yield page, shot, app
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
    assert not app.supervisor.is_tx_permitted


def test_car_flow_with_consented_read(ui: Any) -> None:
    page, shot, app = ui
    page.wait_for_selector("[data-testid=mode-mechanic]")
    shot("01-mode")
    page.click("[data-testid=mode-mechanic]")
    page.wait_for_selector("[data-testid=vehicle-type-car]")
    shot("02-vehicle-type")
    page.click("[data-testid=vehicle-type-car]")
    page.wait_for_selector("[data-testid=vehicle-car_pre_obd]")
    assert page.is_disabled("[data-testid=vehicle-car_pre_obd]")
    shot("03-vehicle-list")
    page.click("[data-testid=vehicle-car_toyota]")
    page.wait_for_selector("[data-testid=adapter-simulator]")
    shot("04-adapter")
    page.click("[data-testid=adapter-simulator]")
    page.wait_for_selector("[data-testid=connect-ready]", timeout=20000)
    shot("05-ready")
    page.click("[data-testid=start-scan]")
    page.wait_for_selector("[data-testid=allow-read]")
    shot("06-consent")
    page.click("[data-testid=allow-read]")
    page.wait_for_selector("[data-testid=result-headline]", timeout=60000)
    shot("07-result")
    assert "P0301" in (page.text_content("details") or "")
    page.click("[data-testid=customer-report]")
    page.wait_for_selector("[data-testid=report-text]")
    assert "son karar ustanındır" in page.inner_text("[data-testid=report-text]")
    shot("08-report")
    assert app.mechanic_prefs.load().vehicle_profile_id == "car_toyota"


def test_truck_flow_listen_only_and_error_path(ui: Any) -> None:
    page, shot, _app = ui
    page.click("[data-testid=mode-mechanic]")
    page.click("[data-testid=vehicle-type-truck]")
    page.click("[data-testid=vehicle-truck_scania]")
    page.click("[data-testid=adapter-simulator]")
    page.wait_for_selector("[data-testid=connect-ready]", timeout=20000)
    page.click("[data-testid=start-scan]")
    page.wait_for_selector("[data-testid=result-headline]", timeout=60000)
    shot("09-truck-result")
    assert page.inner_text("[data-testid=urgency]").startswith("Aciliyet")
    assert "SPN 3251 FMI 0" in (page.text_content("details") or "")


def test_result_stays_reachable_and_the_estop_is_on_every_screen(ui: Any) -> None:
    page, _shot, app = ui
    page.click("[data-testid=mode-mechanic]")
    assert page.is_visible("[data-testid=estop]")
    page.click("[data-testid=vehicle-type-truck]")
    page.click("[data-testid=vehicle-truck_scania]")
    page.click("[data-testid=adapter-simulator]")
    page.wait_for_selector("[data-testid=connect-ready]", timeout=20000)
    page.click("[data-testid=start-scan]")
    page.wait_for_selector("[data-testid=result-headline]", timeout=60000)
    # The action bar keeps the next step in view; the result itself scrolls.
    box = page.locator("[data-testid=customer-report]").bounding_box()
    assert box is not None and box["y"] + box["height"] <= 900
    page.mouse.move(450, 450)
    page.mouse.wheel(0, 2000)
    page.wait_for_function("document.querySelector('[data-testid=mech-scroll]').scrollTop > 0")
    page.click("[data-testid=estop]")
    page.wait_for_selector("[data-testid=estop-banner]", timeout=5000)
    assert app.estop.is_engaged

"""B1: the engineer workbench against the real bridge and the simulated vehicle.

Same harness as ``test_mechanic_ui`` (built frontend over HTTP, every
``pywebview.api`` call forwarded to a real ``DesktopApiBridge``), plus the
Python→JS push channel: ``evaluate_js`` calls are queued by a fake window and
replayed in the page, and the real telemetry loop runs, so the live traffic
table shows exactly the frames the ingest path delivered.

Skipped without Playwright, Chromium or the frontend bundle (e.g. in CI).
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


class _QueueWindow:
    """Stands in for the pywebview window: collects pushed JS for the page."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: list[str] = []

    def evaluate_js(self, js: str) -> None:
        with self.lock:
            self.items.append(js)

    def drain(self) -> list[str]:
        with self.lock:
            items, self.items = self.items[-50:], []
        return items


def _serve(bridge: Any, window: _QueueWindow) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(DIST), **kwargs)

        def log_message(self, *args: Any) -> None:
            pass

        def _json(self, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/__pushed_js":
                self._json(window.drain())
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            name = self.path.rsplit("/", 1)[-1]
            args = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"[]")
            method = getattr(bridge, name, None)
            if name.startswith("_") or not callable(method):
                self.send_error(404)
                return
            self._json(method(*args))

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
        "setInterval(() => fetch('/__pushed_js').then((r) => r.json())"
        "  .then((list) => list.forEach((js) => (0, eval)(js))), 100);"
    )


@pytest.fixture
def wb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import src.ui.desktop_app as da
    from src.ui.mechanic_prefs import MechanicPrefsStore

    monkeypatch.setattr(da, "_app_data_root", lambda: tmp_path)
    app = da.UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app.mechanic_prefs = MechanicPrefsStore(tmp_path / "mechanic_prefs.json")
    app._reconnect_bus("virtual", "wb_ui", 250000)
    window = _QueueWindow()
    app._window = window
    app._running = True
    loop = threading.Thread(target=app._telemetry_loop, daemon=True)
    loop.start()
    bridge = da.DesktopApiBridge(app)
    state = {"success": True, "signedIn": True, "loginRequired": False, "license": PRO_LICENSE}
    bridge.auth_get_state = lambda: dict(state)  # type: ignore[method-assign]
    bridge.auth_refresh_license = lambda: dict(state)  # type: ignore[method-assign]
    server = _serve(bridge, window)
    names = sorted(da.DesktopApiBridge.BRIDGE_RISK_MANIFEST)
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=os.environ.get("UCANLAB_CHROMIUM") or None)
            except Exception as exc:  # noqa: BLE001 — no browser build available
                pytest.skip(f"Chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="tr-TR")
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(_init_script(names))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html")
            page.click("[data-testid=mode-engineer]")
            yield page, app
            assert errors == []
            browser.close()
    finally:
        app._running = False
        loop.join(timeout=2)
        server.shutdown()
    assert not app.supervisor.is_tx_permitted


def test_empty_bus_shows_no_invented_frames(wb: Any) -> None:
    page, _app = wb
    page.wait_for_selector("[data-testid=traffic-empty]")
    page.wait_for_timeout(800)
    assert page.locator("[data-testid=id-table]").count() == 0
    assert "veri yok" in page.text_content("[data-testid=bus-chip]")
    assert page.text_content("[data-testid=safety-chip]").strip() == "Yalnızca dinleme"


def test_simulated_truck_frames_flow_through_the_real_ingest(wb: Any) -> None:
    page, app = wb
    page.select_option("[data-testid=sim-type]", "truck")
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid=id-table]", timeout=20000)
    # VEP1 (PGN 65271, SA 0) is part of the simulated truck: it must appear.
    page.wait_for_selector("[data-testid='id-row-0x18FEF700']", timeout=10000)
    assert "Simülatör" in page.text_content("[data-testid=bus-chip]")
    assert app.bus_info()["simulated"] is True
    assert app.ring_buffer.current_size > 0  # the same frames were recorded

    page.click("[data-testid='id-row-0x18FEF700']")
    page.wait_for_selector("[data-testid=id-inspector]")

    page.fill("[data-testid=traffic-filter]", "FECA")
    page.wait_for_timeout(400)
    rows = page.locator("[data-testid^=id-row-]")
    assert rows.count() == 1 and "0x18FECA00" in rows.first.text_content()

    page.click("[data-testid=stop-simulator]")
    page.wait_for_selector("[data-testid=traffic-empty]")
    assert app.bus_info()["simulated"] is False


def test_records_export_goes_through_python(wb: Any) -> None:
    page, app = wb
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid=id-table]", timeout=20000)
    page.click("[data-testid=nav-records]")
    page.click("[data-testid=format-csv]")
    page.click("[data-testid=export-raw]")
    page.wait_for_selector("[data-testid=export-raw-result]")
    assert "Kaydedildi" in page.text_content("[data-testid=export-raw-result]")


def test_estop_locks_transmit_but_recording_continues(wb: Any) -> None:
    page, app = wb
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid=id-table]", timeout=20000)
    page.click("[data-testid=estop]")
    page.wait_for_selector("[data-testid=estop-banner]", timeout=5000)
    assert app.estop.is_engaged
    before = app.ring_buffer.current_size
    page.wait_for_timeout(600)
    assert app.ring_buffer.current_size >= before


def test_plot_draws_decoded_simulator_values(wb: Any) -> None:
    page, app = wb
    page.select_option("[data-testid=sim-type]", "truck")
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid=id-table]", timeout=20000)
    page.click("[data-testid=nav-plot]")
    page.wait_for_selector("[data-testid=chart-last-EngineSpeed]", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('[data-testid=chart-EngineSpeed] polyline')?.getAttribute('points')?.split(' ').length > 5",
        timeout=15000,
    )
    shown = float(page.text_content("[data-testid=chart-last-EngineSpeed]").replace(".", "").replace(",", "."))
    assert 590 <= shown <= 710
    assert "Simülatör" in page.text_content("[data-testid=signal-EngineSpeed]")
    assert app._diag_session.samples == []  # plotted, never evidence


def test_plot_empty_state_points_to_discovery(wb: Any) -> None:
    page, _app = wb
    page.click("[data-testid=nav-plot]")
    page.wait_for_selector("[data-testid=plot-empty]")
    page.click("[data-testid=plot-empty] button")
    assert page.text_content("[data-testid=page-title]") == "Sinyal keşfi"

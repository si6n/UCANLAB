"""B1: the engineer workbench against the real bridge and the simulated vehicle.

Same harness as ``test_mechanic_ui`` (built frontend over HTTP, every
``pywebview.api`` call forwarded to a real ``DesktopApiBridge``), plus the
Python→JS push channel: ``evaluate_js`` calls are queued by a fake window and
replayed in the page, and the real telemetry loop runs, so the live traffic
table shows exactly the frames the ingest path delivered.

Skipped without Playwright, Chromium or the frontend bundle (e.g. in CI).
"""

from __future__ import annotations

import hashlib
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


def test_estop_reset_dialog_needs_the_out_of_band_token(wb: Any) -> None:
    from src.safety.estop import EStopResetAuthority

    page, app = wb
    page.click("[data-testid=estop]")
    page.wait_for_selector("[data-testid=estop-banner]", timeout=5000)
    page.click("[data-testid=estop-reset-open]")
    page.click("[data-testid=estop-reset-request]")
    page.wait_for_selector("[data-testid=estop-reset-command]")
    assert "scripts/estop_reset_tool.py" in page.text_content("[data-testid=estop-reset-command]")
    # A made-up token is refused; the latch stays.
    page.fill("[data-testid=estop-reset-token]", "TOKEN:not-a-real-token")
    page.click("[data-testid=estop-reset-submit]")
    page.wait_for_selector("[data-testid=estop-reset-dialog] [role=alert]")
    assert app.estop.is_engaged
    # The authorised holder mints the token out of band (what the tool does).
    token = EStopResetAuthority(app.estop, secret_provider=app.estop.reset_authority_provider()).mint_reset_token()
    assert token is not None
    page.fill("[data-testid=estop-reset-token]", "TOKEN:" + token.to_token_string())
    page.click("[data-testid=estop-reset-submit]")
    page.wait_for_selector("[data-testid=estop-reset-done]", timeout=5000)
    assert not app.estop.is_engaged
    page.wait_for_selector("[data-testid=estop-banner]", state="detached", timeout=5000)
    assert not app.supervisor.is_tx_permitted  # released, but transmit stays off


def test_language_and_theme_are_remembered_and_applied(wb: Any) -> None:
    page, _app = wb
    page.click("[data-testid=nav-settings]")
    page.click("[data-testid=settings-tab-appearance]")
    page.click("[data-testid=settings-lang-en]")
    page.wait_for_function("document.documentElement.lang === 'en'")
    assert page.text_content("[data-testid=page-title]") == "Settings"
    assert page.text_content("[data-testid=safety-chip]").strip() == "Listen only"
    page.click("[data-testid=settings-theme-light]")
    assert page.evaluate("document.documentElement.classList.contains('dark')") is False
    assert page.evaluate("localStorage.getItem('ucanlab.theme')") == "light"
    page.click("[data-testid=settings-lang-tr]")
    page.wait_for_function("document.documentElement.lang === 'tr'")
    assert page.text_content("[data-testid=page-title]") == "Ayarlar"


def test_live_traffic_rows_work_from_the_keyboard(wb: Any) -> None:
    page, _app = wb
    page.select_option("[data-testid=sim-type]", "truck")
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid='id-row-0x18FEF700']", timeout=20000)
    page.focus("[data-testid='id-row-0x18FEF700']")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid=id-inspector]")
    assert page.get_attribute("[data-testid='id-row-0x18FEF700']", "aria-selected") == "true"
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-testid=id-inspector]", state="detached")


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


def test_discovery_from_live_traffic_to_dbc(wb: Any) -> None:
    page, app = wb
    page.select_option("[data-testid=sim-type]", "truck")
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid='id-row-0x0CF00400']", timeout=20000)
    page.wait_for_timeout(1500)
    page.click("[data-testid='id-row-0x0CF00400']")
    page.click("[data-testid=send-to-discovery]")
    assert page.text_content("[data-testid=page-title]") == "Sinyal keşfi"
    page.wait_for_selector("[data-testid=hypotheses]", timeout=20000)
    assert page.get_attribute("[data-testid='stream-0x0CF00400']", "aria-pressed") == "true"
    page.click("[data-testid=save-dbc]")  # nothing approved yet
    page.wait_for_selector("[data-testid=save-dbc-result]")
    assert "onaylayın" in page.text_content("[data-testid=save-dbc-result]")
    page.locator("[data-testid^=approve-]").first.click()
    page.wait_for_selector("text=Onaylandı")
    page.click("[data-testid=save-dbc]")
    page.wait_for_function(
        "() => document.querySelector('[data-testid=save-dbc-result]')?.textContent.includes('_SIMULATOR.dbc')", timeout=10000
    )
    assert app.discovery_save_dbc(True)["signals"] >= 1


def test_press_and_release_experiment_finds_the_simulated_pedal(wb: Any) -> None:
    page, app = wb
    page.select_option("[data-testid=sim-type]", "truck")
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid=id-table]", timeout=20000)
    page.click("[data-testid=nav-discovery]")
    page.click("[data-testid=discovery-tab-experiment]")
    page.click("[data-testid=stimulus-start]")
    page.wait_for_selector("[data-testid=stimulus-toggle]")
    for _ in range(2):
        page.wait_for_timeout(1500)
        page.click("[data-testid=stimulus-toggle]")
        page.wait_for_selector("[data-testid=stimulus-phase]:has-text('Uygulanıyor')")
        page.wait_for_timeout(1500)
        page.click("[data-testid=stimulus-toggle]")
        page.wait_for_selector("[data-testid=stimulus-phase]:has-text('Dokunmayın')")
    page.wait_for_selector("[data-testid=stimulus-results] li", timeout=10000)
    page.wait_for_function(
        "() => [...document.querySelectorAll('[data-testid=stimulus-results] li')].slice(0, 3)"
        ".some((li) => li.textContent.includes('0x0CF00300') && li.textContent.includes('bayt 1'))",
        timeout=10000,
    )
    assert app.bus.pedal_pressed is False  # released at the end of the last round
    page.click("[data-testid=stimulus-stop]")
    page.wait_for_selector("[data-testid=stimulus-start]")
    assert app.stimulus_status() == {"running": False}


def test_assistant_labels_simulation_and_never_runs_vehicle_actions(wb: Any) -> None:
    page, app = wb
    page.select_option("[data-testid=sim-type]", "truck")
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid=id-table]", timeout=20000)
    page.wait_for_timeout(2500)
    page.click("[data-testid=nav-assistant]")
    page.wait_for_selector("[data-testid=assistant-headline]", timeout=20000)
    assert page.is_visible("[data-testid=assistant-simulated]")
    assert "DPF" in page.text_content("[data-testid=assistant-headline]")
    # The screen is split into at most three panels (cards in its two columns).
    assert page.locator("[data-testid=assistant-view] > div > section").count() <= 3
    if page.locator("[data-testid=answer-unknown]").count():
        page.click("[data-testid=answer-unknown]")
        page.wait_for_timeout(800)
    assert app._diag_session.samples == [] and app._diag_session.events == []
    assert not app.supervisor.is_tx_permitted


def test_assistant_copilot_card_answers_free_text_read_only(wb: Any) -> None:
    """Copilot upgrade: the six-section answer renders in the existing view,
    the safety banner comes first, and asking never touches the bus or session."""
    page, app = wb
    page.select_option("[data-testid=sim-type]", "truck")
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid=id-table]", timeout=20000)
    page.click("[data-testid=nav-assistant]")
    page.wait_for_selector("[data-testid=copilot-card]", timeout=20000)
    page.fill("[data-testid=copilot-query]", "abs ışığı yandı, motor ısınıyor")
    page.click("[data-testid=copilot-ask]")
    page.wait_for_selector("[data-testid=copilot-banner-brakes]", timeout=20000)
    assert "ABS" in page.text_content("[data-testid=copilot-summary]")
    assert page.is_visible("[data-testid=copilot-urgency]")
    assert page.is_visible("[data-testid=copilot-steps]")
    assert page.locator("[data-testid=copilot-technical]").count() == 1
    assert app._diag_session.samples == [] and app._diag_session.events == []
    assert not app.supervisor.is_tx_permitted


def test_ecu_dry_run_on_the_simulator_sends_nothing(wb: Any) -> None:
    page, app = wb
    page.select_option("[data-testid=sim-type]", "truck")
    page.click("[data-testid=start-simulator]")
    page.wait_for_selector("[data-testid=id-table]", timeout=20000)
    sent: list[Any] = []
    app.bus.send = lambda frame, *a, **k: sent.append(frame)  # would record any TX
    page.click("[data-testid=nav-ecu]")
    page.wait_for_selector("[data-testid=ecu-simulated]", timeout=10000)
    page.click("[data-testid=ecu-TCU]")
    page.fill("[data-testid=ecu-vin]", "WDB9634031L123456")
    # 40 KiB: the hex alone is far past the 16 KiB challenge cap (bound by SHA-256).
    image = bytes((i * 13) & 0xFF for i in range(40 * 1024))
    page.set_input_files("[data-testid=ecu-file]", files=[{"name": "tcu.bin", "mimeType": "application/octet-stream", "buffer": image}])
    page.wait_for_selector("[data-testid=ecu-file-info]")
    assert hashlib.sha256(image).hexdigest()[:16] in page.text_content("[data-testid=ecu-file-info]")
    page.fill("[data-testid=ecu-signature]", "ab" * 32)
    page.fill("[data-testid=ecu-pubkey]", "cd" * 32)
    assert page.is_disabled("[data-testid=ecu-start]")  # the acknowledgement is mandatory
    page.check("[data-testid=ecu-ack]")
    page.click("[data-testid=ecu-start]")
    page.wait_for_selector("[data-testid=ecu-done]", timeout=20000)
    prog = app.flash_progress()
    assert prog["status"] == "completed" and prog["total_bytes"] == len(image)
    assert sent == []
    assert not app.supervisor.is_tx_permitted


def _fake_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.ui.desktop_app as da
    from src.engine.connection.adapters import READY, SIMULATOR, AdapterInfo

    fake = AdapterInfo(kind="pcan", interface="virtual", channel="wb_ui_hw", label="PCAN-USB", status=READY)
    monkeypatch.setattr(da, "discover_adapters", lambda: [fake, SIMULATOR])


def test_settings_connects_an_adapter_listen_only(wb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    page, app = wb
    _fake_adapters(monkeypatch)
    page.click("[data-testid=nav-settings]")
    page.wait_for_selector("[data-testid='adapter-pcan:wb_ui_hw']")
    assert page.locator("[data-testid='adapter-simulator:sim0']").count() == 0  # the toolbar owns the simulator
    page.click("[data-testid=manual-bitrate-500000]")
    page.click("[data-testid=manual-connect]")
    page.wait_for_selector("[data-testid=settings-message][role=status]")
    page.wait_for_function("document.querySelector('[data-testid=current-bus]').textContent.includes('wb_ui_hw')")
    info = app.bus_info()
    assert info["channel"] == "wb_ui_hw" and info["bitrate"] == 500_000 and info["listen_only"] is True
    assert not app.supervisor.is_tx_permitted


def test_settings_listen_test_reports_a_silent_bus_honestly(wb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    page, app = wb
    _fake_adapters(monkeypatch)
    app.connection_wizard._window_s = 0.2
    page.click("[data-testid=nav-settings]")
    page.wait_for_selector("[data-testid='adapter-pcan:wb_ui_hw']")
    page.click("[data-testid=vtype-boat]")
    page.click("[data-testid=listen-test-start]")
    page.wait_for_selector("[data-testid=listen-test-result]", timeout=15000)
    assert page.is_visible("[data-testid=settings-message][role=alert]")
    assert "0" in page.text_content("[data-testid=listen-test-result]")
    assert app.bus_info()["channel"] != "wb_ui_hw"  # nothing committed without traffic


def test_settings_licence_safety_and_sources_are_read_only_views(wb: Any) -> None:
    page, _app = wb
    page.click("[data-testid=nav-settings]")
    page.click("[data-testid=settings-tab-licence]")
    page.wait_for_selector("[data-testid=licence-status]")
    assert "Etkin" in page.text_content("[data-testid=licence-status]")
    assert "PRO" in page.text_content("[data-testid=settings-licence]")
    page.click("[data-testid=settings-tab-safety]")
    page.wait_for_selector("[data-testid=safety-supervisor]")
    assert "yalnız dinleme" in page.text_content("[data-testid=safety-supervisor]")
    page.click("[data-testid=settings-tab-sources]")
    page.wait_for_selector("[data-testid=attribution-source]")
    # The CC BY 4.0 credit is painted inline (no click needed).
    assert "megadata.pro" in page.text_content("[data-testid=settings-view]")


def test_pinout_states_only_standard_assignments(wb: Any) -> None:
    page, _app = wb
    page.click("[data-testid=nav-pinout]")
    page.wait_for_selector("[data-testid=pinout-view]")
    assert "CAN High" in page.text_content("[data-testid=pinout-detail]")
    assert "Kablo rengi" not in page.text_content("[data-testid=pinout-detail]")  # OBD-II has no standard colours
    page.click("[data-testid=pin-13]")
    assert "Üreticiye bırakılmış" in page.text_content("[data-testid=pinout-detail]")
    page.click("[data-testid=connector-deutsch9]")
    assert "Pin C" in page.text_content("[data-testid=pinout-detail]")
    assert "Sarı" in page.text_content("[data-testid=pinout-detail]")


def _write_trace(app: Any, name: str = "bench_truck.asc", n: int = 400) -> None:
    import src.ui.desktop_app as da

    lines = ["date Mon Jan 1 00:00:00 2024", "base hex timestamps absolute"]
    for i in range(n):
        frame = ["18FEF100x       Rx d 8 F0 00 00 C0 00 00 00 FF", "18FEEE00x       Rx d 8 7D FF FF FF FF FF FF FF"][i % 2]
        lines.append(f"   {0.01 * (i + 1):.6f} 1  {frame}")
    folder = da._app_data_root() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("\n".join(lines) + "\n")


def test_records_replay_is_labelled_and_stays_out_of_evidence(wb: Any) -> None:
    page, app = wb
    _write_trace(app)
    app.approve_ccvs_source(0x00, reason="ui test")
    page.click("[data-testid=nav-records]")
    page.click("[data-testid='record-exports/bench_truck.asc']")
    page.click("[data-testid=replay-speed-1]")
    page.click("[data-testid=replay-start]")
    page.wait_for_selector("[data-testid=replay-status]", timeout=10000)
    page.click("[data-testid=nav-traffic]")
    page.wait_for_function("document.querySelector('[data-testid=bus-chip]').textContent.includes('Kayıttan')", timeout=10000)
    page.click("[data-testid=nav-records]")
    page.click("[data-testid=replay-stop]")
    page.wait_for_selector("[data-testid=replay-status]", state="detached", timeout=10000)
    assert app._diag_session.samples == [] and app._diag_session.events == []
    assert app.gateway.speed_interlock_state()[0] == "stale"
    assert not app.supervisor.is_tx_permitted


def test_records_upload_needs_sign_in_and_open_folder_is_fixed(wb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.ui.desktop_app as da

    page, app = wb
    _write_trace(app, "to_upload.asc", 4)
    opened: list[list[str]] = []
    monkeypatch.setattr(da.sys, "platform", "linux")
    monkeypatch.setattr(da.subprocess, "Popen", lambda args, **kw: opened.append(args))
    monkeypatch.setattr(app.cloud_client, "has_session_token", lambda: False)
    page.click("[data-testid=nav-records]")
    page.click("[data-testid='record-exports/to_upload.asc']")
    page.click("[data-testid=record-upload]")
    page.wait_for_selector("[data-testid=record-result]")
    assert "oturum açın" in page.text_content("[data-testid=record-result]")
    page.click("[data-testid=records-open-folder]")
    page.wait_for_timeout(300)
    assert opened == [["xdg-open", str(da._app_data_root() / "exports")]]

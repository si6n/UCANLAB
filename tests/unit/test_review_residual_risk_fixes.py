"""Regression tests for the second remediation batch (residual-risk items).

Covers:
  * D2    — request-scoped cloud session token (no shared-vault save/restore).
  * F5    — typed cloud response parsing (`CLOUD_MALFORMED_RESPONSE`).
  * F6-1  — CSP + path confinement on the local frontend asset server.
  * F6-4  — every bridge wrapper enforces a concrete capability.
  * F6-6  — the EXE builder rebuilds a stale dist and refuses remote origins.
  * F7    — export paths are confined; no temp-dir exemption.
  * A5-2  — the secret-store protection level is observed at startup.
  * A5-6  — the TX choke-point architectural gate (see
            tests/safety/test_tx_chokepoint_architecture.py).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.core.errors import ProtocolError

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# D2 — request-scoped session token
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for the object returned by ``opener.open``.

    Two protocol details matter:

    * `CloudClient.request` uses it as a CONTEXT MANAGER, and
    * `_read_body_bounded` streams via `read(n)` until it returns EMPTY, so the
      double must yield its body once and then b"".
    """

    def __init__(self, body: bytes = b"{}") -> None:
        self.status = 200
        self._body = body
        self._drained = False
        self.headers: dict[str, str] = {"Content-Length": str(len(body))}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, amt: int | None = None) -> bytes:
        if self._drained:
            return b""
        self._drained = True
        return self._body

    def json_object(self):
        return json.loads(self._body)


def _install_capture(monkeypatch: pytest.MonkeyPatch, captured: dict[str, str]) -> None:
    def _fake_open(req, timeout=None):  # noqa: ANN001
        captured.update(dict(req.headers))
        return _FakeResponse()

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *a, **k: type("O", (), {"open": staticmethod(_fake_open)})(),
    )


def _make_client():
    """Build a CloudClient with an isolated in-memory secret store."""
    from src.safety.secret_provider import EphemeralSecretBackend
    from src.security.cloud.client import CloudClient, CloudConfig

    config = CloudConfig(base_url="http://127.0.0.1:9")
    return CloudClient(config=config, secret_provider=EphemeralSecretBackend())


def test_d2_request_signature_accepts_session_token() -> None:
    """D2: the request API must expose a request-scoped token parameter."""
    import inspect

    from src.security.cloud.client import CloudClient

    params = inspect.signature(CloudClient.request).parameters
    assert "session_token" in params, "request() must accept a request-scoped session_token"


def test_d2_override_never_mutates_the_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2: passing an override must not write to (or clear) the shared store."""
    client = _make_client()
    client.store_session_token("stored-token")

    captured: dict[str, str] = {}
    _install_capture(monkeypatch, captured)

    client.request("GET", "/auth/me", session_token="override-token")

    assert captured.get("Cookie") == "ucan_session=override-token"
    # The vault still holds the ORIGINAL token — the override was scoped.
    assert client._secrets.get_secret("CLOUD_SESSION_TOKEN").decode() == "stored-token"


def test_d2_falls_back_to_the_vault_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2: without an override the stored session is used, as before."""
    client = _make_client()
    client.store_session_token("vault-token")

    captured: dict[str, str] = {}
    _install_capture(monkeypatch, captured)

    client.request("GET", "/auth/me")
    assert captured.get("Cookie") == "ucan_session=vault-token"


def test_d2_desktop_app_no_longer_swaps_the_vault() -> None:
    """D2: the save/fire/restore window must be gone from the bridge."""
    source = (REPO_ROOT / "src" / "ui" / "desktop_app.py").read_text(encoding="utf-8")
    body = source.split("def cloud_test_connection", 1)[1].split("\n    def ", 1)[0]
    assert "session_token=candidate" in body, "the override must be passed per-request"
    assert "store_session_token(candidate)" not in body, "the vault must not be swapped"


# ---------------------------------------------------------------------------
# F5 — typed cloud response
# ---------------------------------------------------------------------------


def _resp(body: bytes, status: int = 200):
    from src.security.cloud.client import CloudResponse

    return CloudResponse(status=status, body=body)


def test_f5_json_object_returns_dict() -> None:
    assert _resp(b'{"a": 1}').json_object() == {"a": 1}


def test_f5_json_object_rejects_invalid_json() -> None:
    with pytest.raises(ProtocolError) as exc_info:
        _resp(b"{not json").json_object()
    assert exc_info.value.code == "CLOUD_MALFORMED_RESPONSE"


def test_f5_json_object_rejects_empty_body() -> None:
    with pytest.raises(ProtocolError) as exc_info:
        _resp(b"").json_object()
    assert exc_info.value.code == "CLOUD_MALFORMED_RESPONSE"


@pytest.mark.parametrize("body", [b"[1,2,3]", b'"a string"', b"42", b"null"])
def test_f5_json_object_rejects_non_object_json(body: bytes) -> None:
    with pytest.raises(ProtocolError) as exc_info:
        _resp(body).json_object()
    assert exc_info.value.code == "CLOUD_MALFORMED_RESPONSE"


def test_f5_call_sites_use_the_typed_accessor() -> None:
    """F5: the object-expecting call sites must not use the loose accessor."""
    for rel in (
        "src/ui/desktop_app.py",
        "src/security/cloud/license_flow.py",
        "src/security/cloud/telemetry_uploader.py",
        "src/launcher/updater.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert ".json_object()" in text, f"{rel} should use the typed accessor"
        assert not re.search(r"\w+\.json\(\)", text), f"{rel} still calls the loose .json()"


# ---------------------------------------------------------------------------
# F6-1 — CSP + confined local asset server
# ---------------------------------------------------------------------------


@pytest.fixture()
def served(tmp_path: Path):
    from src.ui.frontend_server import FrontendServer

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")

    server = FrontendServer(dist)
    server.start()
    try:
        yield server, dist, tmp_path
    finally:
        server.stop()


def test_f6_1_server_binds_loopback_only(served) -> None:
    server, _dist, _tmp = served
    from src.ui.frontend_server import is_loopback_url

    assert server.url.startswith("http://127.0.0.1:")
    assert is_loopback_url(server.url)


def test_f6_1_csp_and_security_headers_present(served) -> None:
    server, _dist, _tmp = served
    from src.ui.frontend_server import CONTENT_SECURITY_POLICY

    with urllib.request.urlopen(server.url) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Security-Policy") == CONTENT_SECURITY_POLICY
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"
        assert resp.headers.get("Cross-Origin-Resource-Policy") == "same-origin"


def test_f6_1_csp_forbids_remote_origins(served) -> None:
    from src.ui.frontend_server import CONTENT_SECURITY_POLICY

    # default-src 'none' plus explicit 'self' allowlists — no wildcard host.
    assert "default-src 'none'" in CONTENT_SECURITY_POLICY
    assert "connect-src 'self'" in CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY
    assert "http://" not in CONTENT_SECURITY_POLICY.replace("http://127.0.0.1", "")
    assert "*" not in CONTENT_SECURITY_POLICY


@pytest.mark.parametrize(
    "path",
    ["/../secret.txt", "/%2e%2e%2fsecret.txt", "/..%2fsecret.txt", "/subdir/../../secret.txt"],
)
def test_f6_1_path_traversal_is_blocked(served, path: str) -> None:
    server, _dist, _tmp = served
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}")
    assert exc_info.value.code == 404


def test_f6_1_legitimate_asset_is_served(served) -> None:
    server, _dist, _tmp = served
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/app.js") as resp:
        assert resp.read() == b"console.log(1)"


def test_f6_1_root_serves_index_without_a_listing(served) -> None:
    """`/` yields the SPA entry point — and never a directory listing."""
    server, _dist, _tmp = served
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/") as resp:
        body = resp.read().decode("utf-8")
    assert "<html>" in body
    # A listing would name sibling entries.
    assert "app.js" not in body
    assert "Index of" not in body


def test_f6_1_unknown_asset_is_404(served) -> None:
    server, _dist, _tmp = served
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/nope.js")
    assert exc_info.value.code == 404


def test_f6_1_loopback_guard_rejects_remote_hosts() -> None:
    from src.ui.frontend_server import is_loopback_url

    for url in ("http://evil.com/", "https://example.org/x", "file:///etc/passwd", "ftp://x"):
        assert is_loopback_url(url) is False
    for url in ("http://127.0.0.1:1/", "http://localhost/x", "http://[::1]/y"):
        assert is_loopback_url(url) is True


def test_f6_1_desktop_app_serves_via_the_server() -> None:
    """F6-1: the app must not load the frontend straight off file://."""
    source = (REPO_ROOT / "src" / "ui" / "desktop_app.py").read_text(encoding="utf-8")
    assert "FrontendServer(" in source
    assert "frontend_url = self._frontend_server.start()" in source
    assert "url=str(dist_html.resolve())" not in source


# ---------------------------------------------------------------------------
# F6-4 — bridge wrappers must each guard their own capability
# ---------------------------------------------------------------------------


def test_f6_4_every_bridge_wrapper_checks_its_capability() -> None:
    """F6-4: no wrapper may fall back with only a shell-level check."""
    ts = (REPO_ROOT / "src" / "ui" / "frontend" / "src" / "services" / "bridge.ts").read_text(
        encoding="utf-8"
    )
    blocks = re.split(r"\n  public static (?:async )?", ts)
    loose: list[str] = []
    checked = 0
    for block in blocks[1:]:
        name = block.split("(")[0].strip()
        if "requireNativeOrDev" not in block:
            continue
        if "requireCapability" in block:
            checked += 1
        else:
            loose.append(name)
    assert not loose, f"bridge wrappers without a capability guard: {loose}"
    assert checked >= 20, f"expected the whole bridge surface to be guarded, saw {checked}"


def test_f6_4_capability_guard_is_strict_in_production() -> None:
    ts = (REPO_ROOT / "src" / "ui" / "frontend" / "src" / "services" / "bridge.ts").read_text(
        encoding="utf-8"
    )
    # The guard must throw in production when the method is absent.
    assert "refusing to fake success" in ts
    body = ts.split("private static requireCapability", 1)[1].split("\n  }", 1)[0]
    assert "throw new Error" in body


# ---------------------------------------------------------------------------
# F6-6 — the EXE builder must not ship a stale or online bundle
# ---------------------------------------------------------------------------


def _load_build_exe():
    import importlib.util

    path = REPO_ROOT / "scripts" / "build_exe.py"
    spec = importlib.util.spec_from_file_location("build_exe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_f6_6_stale_dist_is_detected(tmp_path: Path) -> None:
    import os
    import time

    module = _load_build_exe()
    frontend = tmp_path / "src"
    frontend.mkdir()
    dist = frontend / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")

    stale, why = module._dist_is_stale(frontend, dist)
    assert stale is False, why

    # Touch a source file to be newer than the built bundle.
    time.sleep(0.01)
    src_file = frontend / "App.tsx"
    src_file.write_text("export const App = 1", encoding="utf-8")
    os.utime(src_file, (time.time() + 10, time.time() + 10))

    stale, why = module._dist_is_stale(frontend, dist)
    assert stale is True
    assert "newer" in why


def test_f6_6_missing_dist_is_stale(tmp_path: Path) -> None:
    module = _load_build_exe()
    frontend = tmp_path / "src"
    frontend.mkdir()
    stale, why = module._dist_is_stale(frontend, frontend / "dist")
    assert stale is True
    assert "missing" in why


def test_f6_6_remote_origin_in_bundle_is_refused(tmp_path: Path) -> None:
    module = _load_build_exe()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<link href="https://fonts.googleapis.com/css?family=Inter">', encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="remote origin"):
        module._assert_dist_offline(dist)


def test_f6_6_clean_bundle_passes(tmp_path: Path) -> None:
    module = _load_build_exe()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    module._assert_dist_offline(dist)  # must not raise


def test_f6_6_build_uses_both_checks() -> None:
    source = (REPO_ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    assert "_dist_is_stale(" in source
    assert "_assert_dist_offline(" in source


def test_f6_6_checked_in_dist_has_no_remote_origin() -> None:
    """The trap itself: the shipped dist must not reference a CDN."""
    dist = REPO_ROOT / "src" / "ui" / "frontend" / "dist"
    if not dist.is_dir():
        pytest.skip("no checked-in dist")
    module = _load_build_exe()
    module._assert_dist_offline(dist)


# ---------------------------------------------------------------------------
# F7 — export path confinement
# ---------------------------------------------------------------------------


def test_f7_explicit_root_required_by_default(tmp_path: Path) -> None:
    from src.engine.exporters.path_guard import resolve_export_path

    with pytest.raises(ValueError, match="exports_root is required"):
        resolve_export_path(tmp_path / "out.mat", None)


def test_f7_temp_dir_is_not_exempt(tmp_path: Path) -> None:
    """F7: the old temp-dir waiver must be gone."""
    import tempfile

    from src.engine.exporters.path_guard import resolve_export_path

    outside = Path(tempfile.gettempdir()) / "escape.mat"
    with pytest.raises(ValueError):
        resolve_export_path(outside, tmp_path)


def test_f7_absolute_path_outside_root_is_rejected(tmp_path: Path) -> None:
    from src.engine.exporters.path_guard import resolve_export_path

    root = tmp_path / "exports"
    root.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        resolve_export_path(tmp_path / "elsewhere" / "x.mat", root)


def test_f7_relative_basename_is_placed_inside_root(tmp_path: Path) -> None:
    from src.engine.exporters.path_guard import resolve_export_path

    root = tmp_path / "exports"
    root.mkdir()
    resolved = resolve_export_path("session.mat", root)
    assert resolved.parent == root.resolve()
    assert resolved.name == "session.mat"


@pytest.mark.parametrize("bad", ["../evil.mat", "a/b.mat", "..\\evil.mat"])
def test_f7_relative_traversal_names_are_rejected(tmp_path: Path, bad: str) -> None:
    from src.engine.exporters.path_guard import resolve_export_path

    root = tmp_path / "exports"
    root.mkdir()
    with pytest.raises(ValueError):
        resolve_export_path(bad, root)


def test_f7_inside_path_is_accepted(tmp_path: Path) -> None:
    from src.engine.exporters.path_guard import resolve_export_path

    root = tmp_path / "exports"
    root.mkdir()
    target = root / "ok.mat"
    assert resolve_export_path(target, root) == target.resolve()


def test_f7_all_exporters_use_the_shared_guard() -> None:
    """F7: the five duplicated helpers must delegate to one implementation."""
    for rel in (
        "src/engine/exporters/mat_exporter.py",
        "src/engine/exporters/mdf4_exporter.py",
        "src/engine/exporters/kml_exporter.py",
        "src/engine/exporters/pdf_report.py",
        "src/engine/discovery/dbc_builder.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        # Tolerate both the single-line import and the parenthesised multi-line
        # form ruff produces once a module imports several guard helpers.
        assert "path_guard import" in text and "resolve_export_path" in text, rel
        assert "gettempdir" not in text, f"{rel} still carries the temp-dir waiver"


# ---------------------------------------------------------------------------
# A5-2 — protection level is observed
# ---------------------------------------------------------------------------


def test_a5_2_protection_level_is_consumed_by_the_app() -> None:
    source = (REPO_ROOT / "src" / "ui" / "desktop_app.py").read_text(encoding="utf-8")
    assert "_report_secret_protection_level" in source
    assert "protection_level()" in source


def test_a5_2_degraded_levels_are_warned() -> None:
    """A degraded provider must produce a warning, not a silent default."""
    source = (REPO_ROOT / "src" / "ui" / "desktop_app.py").read_text(encoding="utf-8")
    body = source.split("def _report_secret_protection_level", 1)[1].split("\n    def ", 1)[0]
    assert "EPHEMERAL" in body and "FALLBACK_FILE" in body
    assert "DEGRADED" in body


def test_a5_2_security_md_documents_the_seed_model() -> None:
    doc = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for needle in ("machine_seed", "same-user", "AES", "protection_level", "A5-3"):
        assert needle in doc, f"SECURITY.md must document {needle}"


def test_a5_2_protection_level_enum_values_are_known() -> None:
    from src.safety.secret_provider import ProtectionLevel

    assert {level.value for level in ProtectionLevel} == {
        "DPAPI",
        "FILE_AES_GCM_0600",
        "EPHEMERAL",
        "FALLBACK_FILE",
    }


def test_a5_2_report_helper_handles_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reporting must never block boot, even if the provider misbehaves."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    class _Broken:
        def protection_level(self):  # noqa: ANN201
            raise RuntimeError("no backend")

    app = UniversalCanDesktopApp.__new__(UniversalCanDesktopApp)
    app._secret_provider = _Broken()
    assert app._report_secret_protection_level() == "UNKNOWN"


# ---------------------------------------------------------------------------
# NEW FINDING (offline invariant) — no cloud-LLM client may ship in the frontend
# ---------------------------------------------------------------------------


def test_frontend_has_no_cloud_llm_client() -> None:
    """AGENTS.md §2.8: the AI layer is fully offline.

    Found while rebuilding the frontend: `geminiClient.ts` and
    `openAiClient.ts` still existed in `src/services/` (orphaned — nothing
    imported them, so they were tree-shaken out of the bundle, but they were
    one import away from being live and they contradicted the recorded design
    decision). They were removed.
    """
    services = REPO_ROOT / "src" / "ui" / "frontend" / "src" / "services"
    assert not (services / "geminiClient.ts").exists(), "cloud-LLM client must not ship"
    assert not (services / "openAiClient.ts").exists(), "cloud-LLM client must not ship"


def test_frontend_source_never_calls_a_generative_ai_endpoint() -> None:
    """No frontend source may reference a generative-AI API host."""
    src = REPO_ROOT / "src" / "ui" / "frontend" / "src"
    offenders: list[str] = []
    for path in src.rglob("*"):
        if path.suffix not in (".ts", ".tsx") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in ("generativelanguage.googleapis.com", "api.openai.com"):
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()} -> {needle}")
    assert not offenders, "generative-AI endpoint referenced: " + ", ".join(offenders)


def test_bundled_frontend_has_no_remote_origin() -> None:
    """The shipped bundle must not reference a CDN or a remote font host."""
    dist = REPO_ROOT / "src" / "ui" / "frontend" / "dist"
    if not dist.is_dir():
        pytest.skip("no checked-in dist")
    offenders: list[str] = []
    for asset in dist.rglob("*"):
        if asset.suffix.lower() not in (".html", ".js", ".css"):
            continue
        text = asset.read_text(encoding="utf-8", errors="ignore")
        for needle in ("fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr.net", "unpkg.com"):
            if needle in text:
                offenders.append(f"{asset.name} -> {needle}")
    assert not offenders, "bundled asset references a remote origin: " + ", ".join(sorted(set(offenders)))


# ---------------------------------------------------------------------------
# Residual item #2 — atomic export writes
# ---------------------------------------------------------------------------


def test_atomic_write_text_leaves_no_partial_file_on_failure(tmp_path: Path) -> None:
    """A failing write must not clobber the previous good file."""
    import src.engine.exporters.path_guard as pg

    target = tmp_path / "report.html"
    pg.atomic_write_text(target, "GOOD")
    assert target.read_text(encoding="utf-8") == "GOOD"

    def _boom(_tmp: Path, _final: Path) -> None:
        raise OSError("disk full")

    original = pg._atomic_replace
    pg._atomic_replace = _boom  # type: ignore[assignment]
    try:
        with pytest.raises(OSError):
            pg.atomic_write_text(target, "CORRUPT-PARTIAL")
    finally:
        pg._atomic_replace = original  # type: ignore[assignment]

    assert target.read_text(encoding="utf-8") == "GOOD", "partial write clobbered a good file"
    assert not list(tmp_path.glob("*.part")), "scratch file left behind"


def test_atomic_write_text_publishes_complete_content(tmp_path: Path) -> None:
    from src.engine.exporters.path_guard import atomic_write_text

    target = tmp_path / "kml" / "track.kml"
    atomic_write_text(target, "<kml>ok</kml>")
    assert target.read_text(encoding="utf-8") == "<kml>ok</kml>"


def test_atomic_producer_path_roundtrip(tmp_path: Path) -> None:
    """The library-writer path (scipy/asammdf) publishes atomically too."""
    from src.engine.exporters.path_guard import atomic_producer_path, commit_producer_path

    final = tmp_path / "out.mat"
    scratch, dest = atomic_producer_path(final)
    assert scratch != dest
    scratch.write_bytes(b"BINARY")
    commit_producer_path(scratch, dest)
    assert dest.read_bytes() == b"BINARY"
    assert not scratch.exists()


def test_commit_producer_path_rejects_missing_output(tmp_path: Path) -> None:
    from src.engine.exporters.path_guard import atomic_producer_path, commit_producer_path

    scratch, dest = atomic_producer_path(tmp_path / "never.mat")
    with pytest.raises(FileNotFoundError):
        commit_producer_path(scratch, dest)


def test_all_text_exporters_use_the_atomic_helper() -> None:
    """Every text exporter must route through the shared atomic writer."""
    root = REPO_ROOT / "src" / "engine" / "exporters"
    for name in ("kml_exporter.py", "pdf_report.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "atomic_write_text" in text, f"{name} does not write atomically"


def test_mat_exporter_uses_atomic_producer_path() -> None:
    text = (REPO_ROOT / "src" / "engine" / "exporters" / "mat_exporter.py").read_text(encoding="utf-8")
    assert "atomic_producer_path" in text
    assert "commit_producer_path" in text
    # The old non-atomic call must be gone.
    assert "savemat(str(path), mat_dict)" not in text


# ---------------------------------------------------------------------------
# Residual item #1 (D4) — no optimistic "Saved" for a rejected config
# ---------------------------------------------------------------------------


def test_d4_settings_view_awaits_backend_before_persisting() -> None:
    """D4: localStorage must not be written before the backend confirms."""
    src = (
        REPO_ROOT / "src" / "ui" / "frontend" / "src" / "components" / "settings" / "SettingsView.tsx"
    ).read_text(encoding="utf-8")
    handle = src.index("const handleSave")
    body = src[handle : handle + 1600]
    assert "await DesktopBridge.cloudSaveConfig" in body, "save is not awaited"
    assert body.index("await DesktopBridge.cloudSaveConfig") < body.index(
        "localStorage.setItem"
    ), "localStorage written before the backend confirmed"


def test_d4_settings_modal_awaits_backend_before_persisting() -> None:
    src = (
        REPO_ROOT / "src" / "ui" / "frontend" / "src" / "components" / "modals" / "SettingsModal.tsx"
    ).read_text(encoding="utf-8")
    handle = src.index("const handleSave")
    body = src[handle : handle + 2000]
    assert "await DesktopBridge.cloudSaveConfig" in body, "save is not awaited"
    assert body.index("await DesktopBridge.cloudSaveConfig") < body.index(
        "localStorage.setItem"
    ), "localStorage written before the backend confirmed"
    # A rejection must surface an error rather than closing as saved.
    assert "setActionFeedback" in body, "rejected save does not surface an error"


def test_d4_backend_rejects_non_allowlisted_host() -> None:
    """The backend really can reject, so the optimistic toast was load-bearing."""
    src = (REPO_ROOT / "src" / "ui" / "desktop_app.py").read_text(encoding="utf-8")
    start = src.index("def cloud_save_config")
    body = src[start : start + 1400]
    assert "allowed_domains" in body
    assert '"success": False' in body

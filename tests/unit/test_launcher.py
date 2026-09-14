"""Unit tests for the Universal CAN Desktop Launcher & Auto-Updater subsystem."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519

from src.launcher.app import UniversalCanLauncher
from src.launcher.auth import LauncherAuthManager
from src.launcher.prereqs import PrereqChecker
from src.launcher.updater import UpdateManager
from src.safety.secret_provider import EphemeralSecretBackend
from src.security.cloud.client import CloudClient, CloudConfig


def test_prereqs_checker_runs() -> None:
    results = PrereqChecker.run_all_checks()
    assert len(results) >= 2
    names = [r.name for r in results]
    assert "Microsoft Edge WebView2" in names
    assert "Visual C++ Redistributable" in names


def test_updater_semver_comparison() -> None:
    updater = UpdateManager(current_version="13.0.0")

    assert updater.is_newer_version("13.0.1") is True
    assert updater.is_newer_version("13.1.0") is True
    assert updater.is_newer_version("v14.0.0") is True
    assert updater.is_newer_version("13.0.0") is False
    assert updater.is_newer_version("12.9.9") is False
    assert updater.is_newer_version("invalid") is False


def test_updater_sha256_verification() -> None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        payload = b"Universal CAN Platform v13.1 Update Binary Payload"
        tmp.write(payload)
        tmp_path = Path(tmp.name)

    try:
        expected_hash = hashlib.sha256(payload).hexdigest()
        assert UpdateManager.verify_file_sha256(tmp_path, expected_hash) is True
        assert UpdateManager.verify_file_sha256(tmp_path, "0" * 64) is False
    finally:
        tmp_path.unlink(missing_ok=True)


def test_updater_manifest_detection() -> None:
    updater = UpdateManager(current_version="13.0.0")
    manifest = {
        "version": "13.1.0",
        "download_url": "https://example.com/update-13.1.0.exe",
        "sha256": "abc123def456",
        "size_bytes": 15000000,
        "release_notes": "Added CAN-FD ADAS filter and 14 benchmark traces",
        "mandatory": True,
    }
    info = updater._check_for_updates_unverified(custom_manifest=manifest)

    assert info.has_update is True
    assert info.latest_version == "13.1.0"
    assert info.current_version == "13.0.0"
    assert info.mandatory is True
    assert "14 benchmark traces" in info.release_notes


def test_launcher_auth_manager_lifecycle() -> None:
    secrets = EphemeralSecretBackend()
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    auth = LauncherAuthManager(cloud_client=client, secret_provider=secrets, public_key=pub_key)

    # Initial state: unauthenticated, free
    status = auth.get_current_status()
    assert status.is_authenticated is False
    assert status.has_valid_license is False
    assert len(status.hwid) >= 16

    # Test login session token storage
    assert auth.login_web_session("sess_test_token_123") is True
    assert auth.get_current_status().is_authenticated is True

    # Test logout
    auth.logout()
    assert auth.get_current_status().is_authenticated is False


def test_launcher_preflight_report() -> None:
    launcher = UniversalCanLauncher(current_version="13.0.0")
    manifest = {
        "version": "13.2.0",
        "download_url": "https://example.com/v13.2.0.exe",
        "release_notes": "Next-gen release",
    }
    report = launcher.run_preflight(custom_update_manifest=manifest)

    assert isinstance(report.can_launch, bool)
    assert len(report.prereqs) >= 2
    assert report.update_info.has_update is True
    assert report.target_executable.exists()


def test_updater_rejects_hashless_package() -> None:
    """L-C-002 regression: a manifest without sha256 must fail closed."""
    updater = UpdateManager(current_version="13.0.0")
    manifest = {
        "version": "13.1.0",
        "download_url": "https://example.com/update-no-hash.exe",
        "sha256": "",  # missing integrity anchor
        "size_bytes": 100,
    }
    # REVIEW hardening: unsigned custom_manifest path removed from production;
    # tests exercise the explicit test-only parser.
    info = updater._check_for_updates_unverified(custom_manifest=manifest)
    assert info.has_update is True

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "update.exe"
        assert updater.download_update(info, dest) is False
        assert not dest.exists()


def test_updater_rejects_non_https_download_url() -> None:
    """L-H-001 regression: plain-HTTP download URLs are refused."""
    updater = UpdateManager(current_version="13.0.0")
    manifest = {
        "version": "13.1.0",
        "download_url": "http://example.com/update-insecure.exe",
        "sha256": "a" * 64,
    }
    info = updater._check_for_updates_unverified(custom_manifest=manifest)

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "update.exe"
        assert updater.download_update(info, dest) is False
        assert not dest.exists()


def test_cloud_config_rejects_non_loopback_http() -> None:
    """SEC-C-001 regression: only HTTPS or loopback HTTP is permitted."""
    import pytest

    from src.core.errors import SecurityError

    # Plain HTTP to a network host must fail closed
    with pytest.raises(SecurityError, match="HTTPS"):
        CloudConfig(base_url="http://updates.universal-can.example.com:8000")

    # Loopback HTTP stays allowed for local development
    cfg = CloudConfig(base_url="http://localhost:8000")
    assert cfg.endpoint("/health", health_endpoint=True).startswith("http://localhost:8000")

    # HTTPS always allowed
    cfg_https = CloudConfig(base_url="https://cloud.universal-can.example.com")
    assert cfg_https.endpoint("/health", health_endpoint=True).startswith("https://")


def test_updater_rejects_when_signature_required_without_public_key() -> None:
    """Fail-closed: require_signature=True with public_key=None must reject download immediately."""
    updater = UpdateManager(current_version="13.0.0", public_key=None, require_signature=True)
    manifest = {
        "version": "13.1.0",
        "download_url": "https://cloud.universalcan.io/update.exe",
        "sha256": "a" * 64,
        "signature": "sig_dummy_base64",
    }
    info = updater._check_for_updates_unverified(custom_manifest=manifest)

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "update.exe"
        assert updater.download_update(info, dest) is False
        assert not dest.exists()


def test_updater_signature_verification_flow(monkeypatch) -> None:
    """Validate Ed25519 signature verified download flow."""
    import base64
    import io
    import urllib.request

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    payload = b"Universal-CAN-Update-Binary-Payload-2026"
    sig = priv_key.sign(payload)
    sig_b64 = base64.b64encode(sig).decode("ascii")
    sha256 = hashlib.sha256(payload).hexdigest()

    updater = UpdateManager(current_version="13.0.0", public_key=pub_key, require_signature=True)
    manifest = {
        "version": "13.1.0",
        "download_url": "https://cloud.universalcan.io/update.exe",
        "sha256": sha256,
        "signature": sig_b64,
        "size_bytes": len(payload),
    }
    info = updater._check_for_updates_unverified(custom_manifest=manifest)

    # Mock urllib opener to return the payload
    class FakeResponse(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, req, timeout=60: FakeResponse(payload),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: Path(tmpdir)))
        dest = Path(tmpdir) / "update.exe"
        assert updater.download_update(info, dest) is True
        expected_dest = Path(tmpdir) / "13.1.0" / "update.exe"
        assert expected_dest.exists()
        assert expected_dest.read_bytes() == payload


def test_updater_concurrent_downloads_temp_isolation(monkeypatch) -> None:
    """Validate that concurrent download calls do not collide on temp files."""
    import base64
    import io
    import threading
    import urllib.request

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    payload = b"Concurrent-Update-Binary-Payload"
    sig = priv_key.sign(payload)
    sig_b64 = base64.b64encode(sig).decode("ascii")
    sha256 = hashlib.sha256(payload).hexdigest()

    updater = UpdateManager(current_version="13.0.0", public_key=pub_key, require_signature=True)
    manifest = {
        "version": "13.1.0",
        "download_url": "https://cloud.universalcan.io/update.exe",
        "sha256": sha256,
        "signature": sig_b64,
        "size_bytes": len(payload),
    }
    info = updater._check_for_updates_unverified(custom_manifest=manifest)

    class FakeResponse(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, req, timeout=60: FakeResponse(payload),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: Path(tmpdir)))
        results: list[bool] = []

        def worker() -> None:
            res = updater.download_update(info, "update.exe")
            results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        assert all(results)
        expected_dest = Path(tmpdir) / "13.1.0" / "update.exe"
        assert expected_dest.exists()
        assert expected_dest.read_bytes() == payload



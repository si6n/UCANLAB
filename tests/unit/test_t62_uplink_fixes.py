"""T62 Uplink fixes verification suite (Review Stages 1-10 / T62-U1 to T62-U6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError, SecurityError
from src.launcher.app import (
    TARGET_MANIFEST_ENV,
    UniversalCanLauncher,
    _manifest_path,
)
from src.launcher.updater import UpdateInfo
from src.safety.secret_provider import InMemorySecretProvider
from src.security.cloud.client import (
    CANONICAL_CLOUD_HOSTS,
    CloudClient,
    CloudConfig,
    is_production,
)
from src.security.cloud.license_flow import LicenseFlow
from src.ui.desktop_app import _resolve_cloud_base_url


# ==============================================================================
# T62-U1: Cloud base URL host allowlist and frozen/production fail-closed checks
# ==============================================================================
class TestT62U1CloudAllowlist:
    def test_canonical_cloud_hosts_defined(self) -> None:
        """Canonical allowlist includes official hosts and loopback addresses."""
        for host in ("ucan-cloud.si6n.io", "cloud.universalcan.io", "ucanlab.org", "api.ucanlab.org", "127.0.0.1", "localhost", "::1"):
            assert host in CANONICAL_CLOUD_HOSTS

    def test_cloud_config_rejects_userinfo(self) -> None:
        """Userinfo in cloud base URL must fail closed."""
        with pytest.raises(SecurityError) as exc_info:
            CloudConfig(base_url="https://admin:pass@api.ucanlab.org")
        assert exc_info.value.code == "CLOUD_USERINFO_FORBIDDEN"

    def test_cloud_config_enforces_allowlist_when_requested(self) -> None:
        """Unknown host rejected when enforce_allowlist=True."""
        with pytest.raises(SecurityError) as exc_info:
            CloudConfig(base_url="https://attacker.example", enforce_allowlist=True)
        assert exc_info.value.code == "CLOUD_HOST_NOT_ALLOWED"

    def test_cloud_config_enforces_allowlist_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unknown host rejected when is_production() is True."""
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert is_production() is True
        with pytest.raises(SecurityError) as exc_info:
            CloudConfig(base_url="https://attacker.example")
        assert exc_info.value.code == "CLOUD_HOST_NOT_ALLOWED"

    def test_cloud_client_request_enforces_allowlist(self) -> None:
        """CloudClient.request() fails closed if host is not allowlisted."""
        cfg = CloudConfig(base_url="https://ucanlab.org", enforce_allowlist=True)
        client = CloudClient(config=cfg, secret_provider=InMemorySecretProvider())
        # Force config base_url to untrusted host bypassing dataclass check
        object.__setattr__(client.config, "base_url", "https://evil.example")
        with pytest.raises(SecurityError) as exc_info:
            client.request("GET", "/health", health_endpoint=True)
        assert exc_info.value.code == "CLOUD_HOST_NOT_ALLOWED"

    def test_resolve_cloud_base_url_rejects_unknown_host_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_resolve_cloud_base_url() rejects untrusted host override in production/frozen."""
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setenv("UCANLAB_CLOUD_BASE_URL", "https://attacker.example")
        with pytest.raises(SecurityError) as exc_info:
            _resolve_cloud_base_url()
        assert exc_info.value.code == "CLOUD_UNTRUSTED_HOST_OVERRIDE"

    def test_resolve_cloud_base_url_rejects_dev_override_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_resolve_cloud_base_url() rejects dev loopback override in production/frozen."""
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setenv("UCANLAB_CLOUD_DEV", "1")
        with pytest.raises(SecurityError) as exc_info:
            _resolve_cloud_base_url()
        assert exc_info.value.code == "CLOUD_DEV_OVERRIDE_FORBIDDEN"

    def test_resolve_cloud_base_url_accepts_canonical_host_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_resolve_cloud_base_url() accepts official allowlisted host in production."""
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setenv("UCANLAB_CLOUD_BASE_URL", "https://api.ucanlab.org")
        assert _resolve_cloud_base_url() == "https://api.ucanlab.org"


# ==============================================================================
# T62-U2: Frozen launcher target manifest override defense
# ==============================================================================
class TestT62U2LauncherManifestOverride:
    def test_manifest_override_ignored_when_frozen(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """sys.frozen=True must strictly ignore UCANLAB_TARGET_MANIFEST override."""
        monkeypatch.setattr("sys.frozen", True, raising=False)
        fake_attacker_manifest = tmp_path / "attacker_manifest.hash"
        fake_attacker_manifest.write_text("evil_digest", encoding="utf-8")
        monkeypatch.setenv(TARGET_MANIFEST_ENV, str(fake_attacker_manifest))

        resolved = _manifest_path()
        assert resolved != fake_attacker_manifest
        assert resolved.name == "target.hash"
        assert "attacker" not in str(resolved)

    def test_manifest_override_accepted_when_not_frozen(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """not sys.frozen allows UCANLAB_TARGET_MANIFEST for test harness / packaging."""
        monkeypatch.delattr("sys.frozen", raising=False)
        custom_manifest = tmp_path / "custom.hash"
        custom_manifest.write_text("test_digest", encoding="utf-8")
        monkeypatch.setenv(TARGET_MANIFEST_ENV, str(custom_manifest))

        assert _manifest_path() == custom_manifest


# ==============================================================================
# T62-U3: LicenseFlow HWM anti-rollback fail-closed protection
# ==============================================================================
class TestT62U3LicenseHwmFailClosed:
    def test_hwm_first_run_returns_fallback_and_persists_marker(self, tmp_path: Path) -> None:
        """On first run with no prior HWM, boot time fallback is used and marker is set on persist."""
        vault = InMemorySecretProvider()
        client = CloudClient(config=CloudConfig(), secret_provider=vault)
        hwm_file = tmp_path / "license_hwm.txt"
        key = ed25519.Ed25519PrivateKey.generate().public_key()

        assert not vault.has_secret("LICENSE_HWM_INITIALIZED")
        flow = LicenseFlow(client=client, public_key=key, hwm_path=hwm_file, boot_realtime=1000.0)
        assert flow.last_known_clock_ts == 1000.0

        # Persisting marks initialization in secret provider
        flow._persist_hwm(2000.0)
        assert vault.has_secret("LICENSE_HWM_INITIALIZED")
        assert hwm_file.exists()

    def test_hwm_deleted_after_initialization_fails_closed(self, tmp_path: Path) -> None:
        """Deleting HWM file after initialization fails closed with HWM_UNAVAILABLE."""
        vault = InMemorySecretProvider()
        client = CloudClient(config=CloudConfig(), secret_provider=vault)
        hwm_file = tmp_path / "license_hwm.txt"
        key = ed25519.Ed25519PrivateKey.generate().public_key()

        flow1 = LicenseFlow(client=client, public_key=key, hwm_path=hwm_file, boot_realtime=1000.0)
        flow1._persist_hwm(2500.0)

        # Attacker deletes HWM file to attempt clock rollback
        hwm_file.unlink()
        assert not hwm_file.exists()

        # Restarting flow must raise HWM_UNAVAILABLE (fail-closed)
        with pytest.raises(LicenseError) as exc_info:
            LicenseFlow(client=client, public_key=key, hwm_path=hwm_file, boot_realtime=500.0)
        assert exc_info.value.code == "HWM_UNAVAILABLE"

    def test_hwm_tampered_file_fails_closed(self, tmp_path: Path) -> None:
        """Tampered HWM content fails closed with HWM_UNAVAILABLE."""
        vault = InMemorySecretProvider()
        client = CloudClient(config=CloudConfig(), secret_provider=vault)
        hwm_file = tmp_path / "license_hwm.txt"
        key = ed25519.Ed25519PrivateKey.generate().public_key()

        flow = LicenseFlow(client=client, public_key=key, hwm_path=hwm_file, boot_realtime=1000.0)
        flow._persist_hwm(2000.0)

        # Attacker modifies the timestamp portion
        hwm_file.write_text("999999.badmac", encoding="utf-8")
        with pytest.raises(LicenseError) as exc_info:
            LicenseFlow(client=client, public_key=key, hwm_path=hwm_file, boot_realtime=1000.0)
        assert exc_info.value.code == "HWM_UNAVAILABLE"


# ==============================================================================
# T62-U4: Mandatory update obligation IO error fail-closed handling
# ==============================================================================
class TestT62U4ObligationIOFailClosed:
    def test_obligation_read_oserror_blocks_launch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError when reading obligation file returns IO_ERROR and blocks launch."""
        launcher = UniversalCanLauncher.for_testing(current_version="13.0.0")

        # Mock check_for_updates failure
        failed_update = UpdateInfo(
            current_version="13.0.0",
            latest_version="13.0.0",
            has_update=False,
            mandatory=False,
            check_succeeded=False,
        )
        monkeypatch.setattr(launcher.update_manager, "check_for_updates", lambda: failed_update)

        # Mock _obligation_path to raise OSError on read_text
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError("Disk read error")
        monkeypatch.setattr(launcher, "_obligation_path", lambda: mock_path)

        report = launcher.run_preflight()
        assert report.can_launch is False

    def test_obligation_write_oserror_blocks_launch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError when writing mandatory obligation file fails closed (can_launch=False)."""
        launcher = UniversalCanLauncher.for_testing(current_version="13.0.0")

        mandatory_update = UpdateInfo(
            current_version="13.0.0",
            latest_version="13.5.0",
            has_update=True,
            mandatory=True,
            check_succeeded=True,
        )
        monkeypatch.setattr(launcher.update_manager, "check_for_updates", lambda: mandatory_update)

        # Mock _record_mandatory_obligation failure
        monkeypatch.setattr(launcher, "_record_mandatory_obligation", lambda ver: False)

        report = launcher.run_preflight()
        assert report.can_launch is False


# ==============================================================================
# T62-U5: Launcher unsigned manifest gate refactor (no PYTEST_CURRENT_TEST spoof)
# ==============================================================================
class TestT62U5UnsignedManifestGate:
    def test_unsigned_manifest_rejected_without_explicit_constructor_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spoofed PYTEST_CURRENT_TEST environment variable does NOT allow unsigned manifests."""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "malicious_spoofed_test_call")
        launcher = UniversalCanLauncher(current_version="13.0.0")

        with pytest.raises(PermissionError) as exc_info:
            launcher.run_preflight(custom_update_manifest={"version": "99.0.0"})
        assert "test-only path" in str(exc_info.value)

    def test_unsigned_manifest_allowed_with_explicit_injection(self) -> None:
        """Explicit allow_unsigned_manifest=True or for_testing() accepts custom manifest."""
        launcher = UniversalCanLauncher.for_testing(current_version="13.0.0")
        report = launcher.run_preflight(custom_update_manifest={"version": "13.2.0"})
        assert report.update_info.latest_version == "13.2.0"


# ==============================================================================
# T62-U6: gitlab-ci.yml pip-audit and requirements lock security
# ==============================================================================
class TestT62U6CiAndRequirementsSecurity:
    def test_gitlab_ci_pip_audit_is_mandatory(self) -> None:
        """pip-audit in .gitlab-ci.yml must have allow_failure: false."""
        ci_path = Path(".gitlab-ci.yml")
        content = ci_path.read_text(encoding="utf-8")
        assert "pip-audit:" in content
        # Find pip-audit block
        pip_audit_section = content.split("pip-audit:")[1].split("\n\n")[0]
        assert "allow_failure: false" in pip_audit_section

    def test_requirements_lock_exists_and_contains_hashes(self) -> None:
        """requirements.lock must exist and contain sha256 hashes for core packages."""
        lock_path = Path("requirements.lock")
        assert lock_path.is_file(), "requirements.lock must be present"
        content = lock_path.read_text(encoding="utf-8")
        assert "--hash=sha256:" in content
        for pkg in ("python-can", "cantools", "cryptography", "pydantic"):
            assert pkg in content

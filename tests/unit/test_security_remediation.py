"""Tests verifying field security remediation (F-1 through F-6)."""

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.launcher.updater import UpdateManager
from src.safety.secret_provider import (
    DEFAULT_DPAPI_ENTROPY,
    derive_machine_dpapi_entropy,
)
from src.security.cloud.client import CloudClient, CloudConfig
from src.ui.desktop_app import DesktopApiBridge


class TestSecurityRemediation:
    """Validate all security hardening fixes."""

    def test_f3_telemetry_upload_path_validation(self, tmp_path: Path) -> None:
        """F-3: Ensure arbitrary file reads and sensitive directory access are blocked."""
        # Valid diagnostic file
        valid_file = tmp_path / "session_log.mf4"
        valid_file.write_bytes(b"MDF4_VALID_DATA")
        assert DesktopApiBridge._validate_telemetry_upload_path(str(valid_file)) == valid_file.resolve()

        # Non-existent file rejected
        with pytest.raises(ValueError, match="Dosya bulunamadi"):
            DesktopApiBridge._validate_telemetry_upload_path(str(tmp_path / "non_existent.bin"))

        # Disallowed extension (.exe, .py, .dll) rejected
        bad_ext_file = tmp_path / "exploit.exe"
        bad_ext_file.write_bytes(b"MZ...")
        with pytest.raises(ValueError, match="Izin verilmeyen dosya formati"):
            DesktopApiBridge._validate_telemetry_upload_path(str(bad_ext_file))

        # Sensitive path rejected
        sensitive_file = tmp_path / "secrets.dpapi"
        sensitive_file.write_bytes(b"SECRET_DATA")
        with pytest.raises(ValueError, match="Guvenlik politikasi"):
            DesktopApiBridge._validate_telemetry_upload_path(str(sensitive_file))

    def test_f4_raw_content_filename_sanitization(self) -> None:
        """F-4: Verify filename sanitization in cloud_upload_raw_content."""
        mock_app = MagicMock()
        mock_app.telemetry_uploader.upload_file.return_value = MagicMock(session_id="s123", status="ok")
        bridge = DesktopApiBridge(mock_app)

        # Path traversal and injection attempt
        malicious_filename = "../../../sensitive_data:stream.bin"
        res = bridge.cloud_upload_raw_content(malicious_filename, "test content")

        assert res["success"] is True
        assert res["sessionId"] == "s123"
        call_args = mock_app.telemetry_uploader.upload_file.call_args
        temp_file_used = call_args[1]["file_path"]
        # Ensure temporary file does not contain traversal separators or colons in base name
        base_name = Path(temp_file_used).name
        assert ".." not in base_name
        assert ":" not in base_name

    def test_f5_retry_after_capping(self) -> None:
        """F-5: Verify Retry-After header delay is capped to prevent DoS."""
        config = CloudConfig(
            base_url="http://localhost:8000",
            max_retry_backoff_seconds=10.0,
        )
        client = CloudClient(config=config)

        # Huge Retry-After value
        headers = {"Retry-After": "999999"}
        with patch("time.sleep") as mock_sleep:
            client._sleep_backoff(attempt=0, response_headers=headers)
            mock_sleep.assert_called_once_with(10.0)

    def test_f1_updater_ed25519_signature_verification(self, tmp_path: Path) -> None:
        """F-1: Verify Ed25519 signature checking on downloaded update packages."""
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        package_file = tmp_path / "update_v13.1.0.exe"
        content = b"GENUINE_BINARY_PAYLOAD_V13_1"
        package_file.write_bytes(content)

        # Valid signature
        sig_bytes = private_key.sign(content)
        sig_b64 = base64.b64encode(sig_bytes).decode("ascii")

        assert UpdateManager.verify_file_signature(package_file, sig_b64, public_key) is True

        # Tampered content / invalid signature
        tampered_file = tmp_path / "tampered.exe"
        tampered_file.write_bytes(b"MALICIOUS_PAYLOAD")
        assert UpdateManager.verify_file_signature(tampered_file, sig_b64, public_key) is False

    def test_f6_machine_dpapi_entropy_derivation(self) -> None:
        """F-6: Verify machine-tied DPAPI entropy derivation and format."""
        entropy = derive_machine_dpapi_entropy()
        assert isinstance(entropy, bytes)
        assert len(entropy) == 32  # SHA-256 output
        assert entropy != DEFAULT_DPAPI_ENTROPY

    def test_cloud_test_connection_domain_whitelist(self) -> None:
        """Verify cloud_test_connection rejects non-whitelisted domains to prevent credential exfiltration."""
        mock_app = MagicMock()
        bridge = DesktopApiBridge(mock_app)

        # Malicious external domain
        res = bridge.cloud_test_connection(url="https://attacker.evil.com/api")
        assert res["success"] is False
        assert "izin listesinde değil" in res["error"]

        # Valid domain accepted
        mock_app.cloud_client.request.return_value = MagicMock(status=200)
        res_valid = bridge.cloud_test_connection(url="https://cloud.universalcan.io")
        assert res_valid["success"] is True

    def test_uds_client_reentrant_lock(self) -> None:
        """Verify UdsClient operation lock is re-entrant (RLock)."""
        from src.hal.virtual import VirtualBus
        from src.protocols.uds.client import UdsClient
        from tests.unit.test_reassembly_pipeline import MockTxPort
        bus = VirtualBus(channel_id="vcan0")
        tx_port = MockTxPort()
        client = UdsClient(tx_id=0x7E0, rx_id=0x7E8, channel_id="vcan0", bus=bus, tx_port=tx_port)
        # Should acquire without deadlock
        with client._operation_lock:
            with client._operation_lock:
                assert True

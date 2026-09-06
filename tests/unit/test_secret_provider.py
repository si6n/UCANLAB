"""Unit tests for Cryptographic Secret Providers (Windows DPAPI, Linux, Ephemeral)."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from src.core.errors import SecurityError
from src.safety.secret_provider import (
    EphemeralSecretBackend,
    LinuxSecretBackend,
    SecretProvider,
    WindowsDPAPISecretBackend,
    get_default_secret_provider,
    get_platform_secret_provider,
)


def test_ephemeral_secret_backend_crud() -> None:
    """Test full CRUD operations on in-memory EphemeralSecretBackend."""
    backend = EphemeralSecretBackend()
    assert isinstance(backend, SecretProvider)

    # Initial state
    assert backend.list_secrets() == []
    assert not backend.has_secret("TEST_KEY")

    # Store and retrieve
    secret_val = b"my_super_secret_binary_data_\x00\xff\xfe"
    backend.store_secret("TEST_KEY", secret_val)
    assert backend.has_secret("TEST_KEY")
    assert backend.get_secret("TEST_KEY") == secret_val
    assert backend.list_secrets() == ["TEST_KEY"]

    # Update
    new_val = b"updated_secret_value_2026"
    backend.set_secret("TEST_KEY", new_val)
    assert backend.get_secret("TEST_KEY") == new_val

    # Delete
    backend.delete_secret("TEST_KEY")
    assert not backend.has_secret("TEST_KEY")
    assert "TEST_KEY" not in backend.list_secrets()

    # KeyError on non-existent
    with pytest.raises(KeyError, match="Secret 'TEST_KEY' not found"):
        backend.get_secret("TEST_KEY")

    # Type validation
    with pytest.raises(TypeError):
        backend.store_secret(123, b"data")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        backend.store_secret("KEY", "not_bytes")  # type: ignore[arg-type]

    # Clear
    backend.store_secret("K1", b"v1")
    backend.store_secret("K2", b"v2")
    assert len(backend.list_secrets()) == 2
    backend.clear()
    assert len(backend.list_secrets()) == 0


def test_linux_secret_backend_lifecycle(tmp_path: Path) -> None:
    """Test LinuxSecretBackend AES-256-GCM encryption, persistence, and 0600 permissions."""
    storage_file = tmp_path / "secrets.bin"
    backend = LinuxSecretBackend(storage_path=storage_file)

    key1 = "ESTOP_HMAC_KEY"
    val1 = os.urandom(32)
    key2 = "UDS_SESSION_KEY"
    val2 = b"session_secret_data_12345"

    backend.store_secret(key1, val1)
    backend.store_secret(key2, val2)

    assert storage_file.exists()
    assert backend.has_secret(key1)
    assert backend.has_secret(key2)
    assert backend.get_secret(key1) == val1
    assert backend.get_secret(key2) == val2

    # Verify file is encrypted (raw secret bytes must not appear in plaintext)
    raw_content = storage_file.read_bytes()
    assert val1 not in raw_content
    assert val2 not in raw_content
    assert raw_content.startswith(LinuxSecretBackend.MAGIC_HEADER)

    # Re-open in a fresh instance
    backend2 = LinuxSecretBackend(storage_path=storage_file)
    assert backend2.get_secret(key1) == val1
    assert backend2.get_secret(key2) == val2
    assert sorted(backend2.list_secrets()) == [key1, key2]

    # Delete
    backend2.delete_secret(key1)
    assert not backend2.has_secret(key1)
    assert backend2.has_secret(key2)


def test_linux_secret_backend_tamper_detection(tmp_path: Path) -> None:
    """Test LinuxSecretBackend tamper and corruption rejection."""
    storage_file = tmp_path / "corrupted_secrets.bin"
    backend = LinuxSecretBackend(storage_path=storage_file)
    backend.store_secret("SECRET", b"sensitive_bytes")

    # Corrupt ciphertext
    data = bytearray(storage_file.read_bytes())
    data[-1] ^= 0xFF  # Invalidate AES-GCM authentication tag
    storage_file.write_bytes(bytes(data))

    backend_corrupted = LinuxSecretBackend(storage_path=storage_file)
    with pytest.raises(SecurityError, match="Decryption failed|corrupted"):
        backend_corrupted.get_secret("SECRET")

    # Corrupt magic header
    data[:4] = b"BAD!"
    storage_file.write_bytes(bytes(data))
    with pytest.raises(SecurityError, match="Invalid secret store magic header"):
        backend_corrupted.get_secret("SECRET")


def test_linux_secret_backend_custom_master_key(tmp_path: Path) -> None:
    """Test LinuxSecretBackend with explicit master key."""
    storage_file = tmp_path / "custom_master.bin"
    master_key = b"explicit_32_byte_master_secret_!"
    backend = LinuxSecretBackend(storage_path=storage_file, master_key=master_key)

    secret_data = b"confidential_payload"
    backend.store_secret("CUSTOM", secret_data)

    # Reading with same master key succeeds
    backend_same = LinuxSecretBackend(storage_path=storage_file, master_key=master_key)
    assert backend_same.get_secret("CUSTOM") == secret_data

    # Reading with wrong master key fails with SecurityError
    backend_wrong = LinuxSecretBackend(storage_path=storage_file, master_key=b"wrong_32_byte_master_secret____!")
    with pytest.raises(SecurityError):
        backend_wrong.get_secret("CUSTOM")


def test_windows_dpapi_secret_backend_lifecycle(tmp_path: Path) -> None:
    """Test WindowsDPAPISecretBackend storage, retrieval, and persistence."""
    storage_file = tmp_path / "secrets.dpapi"
    backend = WindowsDPAPISecretBackend(storage_path=storage_file)

    key = "ESTOP_RESET_SECRET"
    val = b"windows_dpapi_protected_secret_bytes_12345"

    backend.store_secret(key, val)
    assert backend.has_secret(key)
    assert backend.get_secret(key) == val

    # Verify secret is stored in encrypted format on disk
    if storage_file.exists():
        raw_text = storage_file.read_text(encoding="utf-8")
        assert "windows_dpapi_protected_secret_bytes" not in raw_text

    # Re-open in fresh instance
    backend2 = WindowsDPAPISecretBackend(storage_path=storage_file)
    assert backend2.get_secret(key) == val

    # Delete
    backend2.delete_secret(key)
    assert not backend2.has_secret(key)


def test_windows_dpapi_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test WindowsDPAPISecretBackend fallback when DPAPI is not available."""
    storage_file = tmp_path / "secrets_fallback.dpapi"
    fallback_file = tmp_path / "secrets_fallback.fallback.bin"
    fallback = LinuxSecretBackend(storage_path=fallback_file)

    backend = WindowsDPAPISecretBackend(storage_path=storage_file, fallback_backend=fallback)

    # Force DPAPI unavailable
    monkeypatch.setattr(backend, "_is_windows_dpapi_available", lambda: False)

    backend.store_secret("FB_KEY", b"fallback_secret_value")
    assert backend.has_secret("FB_KEY")
    assert backend.get_secret("FB_KEY") == b"fallback_secret_value"
    assert backend.list_secrets() == ["FB_KEY"]

    backend.delete_secret("FB_KEY")
    assert not backend.has_secret("FB_KEY")


def test_factory_get_default_secret_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test get_default_secret_provider auto-detection."""
    # Force ephemeral flag
    p1 = get_default_secret_provider(force_ephemeral=True)
    assert isinstance(p1, EphemeralSecretBackend)

    # Force ephemeral via env var
    monkeypatch.setenv("UNIVERSAL_CAN_EPHEMERAL_SECRETS", "1")
    p2 = get_default_secret_provider()
    assert isinstance(p2, EphemeralSecretBackend)

    monkeypatch.delenv("UNIVERSAL_CAN_EPHEMERAL_SECRETS", raising=False)

    # Platform default
    p3 = get_platform_secret_provider()
    if sys.platform == "win32":
        assert isinstance(p3, WindowsDPAPISecretBackend)
    else:
        assert isinstance(p3, LinuxSecretBackend)


def test_concurrent_secret_access(tmp_path: Path) -> None:
    """Verify thread safety under concurrent writes and reads."""
    storage_file = tmp_path / "concurrent.bin"
    backend = LinuxSecretBackend(storage_path=storage_file)

    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            for i in range(20):
                key = f"key_{worker_id}_{i}"
                val = f"value_{worker_id}_{i}".encode("utf-8")
                backend.store_secret(key, val)
                assert backend.get_secret(key) == val
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_machine_seed_read_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed seed handling (AGENTS §2b): a transient OSError while reading
    the persistent machine seed must raise SecurityError — NEVER silently mint
    a replacement seed that would invalidate every previously sealed secret."""

    storage_file = tmp_path / "secrets.bin"
    seed_file = tmp_path / "machine_seed.bin"
    seed_file.write_bytes(b"preexisting_seed_material_32_bytes_pad!!")

    backend = LinuxSecretBackend(storage_path=storage_file)

    def _boom(self: object) -> bytes:
        raise OSError("transient disk error")

    # Patch only the seed file instance's read_bytes — everything else
    # (store, key derivation inputs) stays on the real filesystem.
    monkeypatch.setattr(type(seed_file), "read_bytes", _boom)

    with pytest.raises(SecurityError, match="could not be read"):
        backend._get_machine_seed()


def test_machine_seed_missing_file_mints_new_seed(tmp_path: Path) -> None:
    """Positive/boundary: with no seed file present a fresh 32-byte seed is
    created and persisted so the second run reuses it (F-09)."""
    storage_file = tmp_path / "secrets.bin"
    backend = LinuxSecretBackend(storage_path=storage_file)

    seed1 = backend._get_machine_seed()
    assert len(seed1) == 32

    seed_file = tmp_path / "machine_seed.bin"
    assert seed_file.exists()
    seed2 = backend._get_machine_seed()
    assert seed1 == seed2

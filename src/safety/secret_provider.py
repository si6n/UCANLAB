"""Universal CAN-Bus Diagnostic & Telemetry Platform - Cryptographic Secret Providers.

Provides hardened, cross-platform cryptographic key and secret management backends:
- Windows DPAPI backend (CryptProtectData / CryptUnprotectData with local encrypted fallback)
- Linux / POSIX backend (AES-256-GCM encrypted local keyfile with 0600 file permissions and HKDF)
- Ephemeral in-memory backend (for CI, test runners, and dynamic session keys)
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib
import json
import os
import sys
import threading
import time
from abc import ABC, abstractmethod
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.core.errors import SecurityError
from src.core.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("safety.secret_provider")

DEFAULT_DPAPI_ENTROPY: bytes = b"UniversalCAN_Hardware_Secret_Binding_2026"
DEFAULT_LINUX_SALT: bytes = b"UniversalCAN_Linux_Secret_Salt_2026"
DEFAULT_KDF_INFO: bytes = b"UniversalCAN_Secret_Key_Derivation_v1"


def derive_machine_dpapi_entropy(base_entropy: bytes = DEFAULT_DPAPI_ENTROPY) -> bytes:
    """Derive hardware-tied DPAPI secondary entropy binding (F-06 / SEC-SP-001).

    Mixes machine node, platform identity, and base entropy through SHA-256
    to prevent cross-device credential transfer attacks while maintaining
    deterministic local recovery.
    """
    import hashlib
    import platform

    ident = f"{platform.node()}-{platform.machine()}-{os.environ.get('COMPUTERNAME', '')}".encode("utf-8")
    return hashlib.sha256(base_entropy + b":" + ident).digest()


class SecretProvider(ABC):
    """Abstract interface for secure secret and key storage providers."""

    @abstractmethod
    def get_secret(self, name: str) -> bytes:
        """Retrieve a binary secret by its logical identifier.

        Args:
            name: Logical secret key name (e.g. 'ESTOP_HMAC_SECRET').

        Returns:
            Decrypted secret bytes.

        Raises:
            KeyError: If secret does not exist.
            SecurityError: If decryption, permissions, or tamper checks fail.
        """
        ...

    @abstractmethod
    def store_secret(self, name: str, secret: bytes) -> None:
        """Securely persist or update a binary secret.

        Args:
            name: Logical secret key name.
            secret: Binary secret data to store.

        Raises:
            SecurityError: If encryption or file access fails.
            TypeError: If secret is not bytes or name is not str.
        """
        ...

    def set_secret(self, name: str, secret: bytes) -> None:
        """Alias for store_secret to ensure full compatibility with ports.SecretProvider contract."""
        self.store_secret(name, secret)

    def delete_secret(self, name: str) -> None:
        """Remove a secret from the provider.

        Args:
            name: Logical secret key name to remove.

        Raises:
            KeyError: If secret does not exist.
            SecurityError: If removal or storage write fails.
        """
        raise NotImplementedError(f"delete_secret is not supported by {self.__class__.__name__}")

    def has_secret(self, name: str) -> bool:
        """Check whether a secret exists and can be retrieved.

        Args:
            name: Logical secret key name.

        Returns:
            True if secret exists, False otherwise.
        """
        try:
            self.get_secret(name)
            return True
        except (KeyError, SecurityError):
            return False

    def list_secrets(self) -> list[str]:
        """List all available secret names in this provider.

        Returns:
            List of secret key names.
        """
        return []


# =====================================================================
# Ephemeral In-Memory Backend (CI / Testing / Dynamic Session Keys)
# =====================================================================


class EphemeralSecretBackend(SecretProvider):
    """Thread-safe in-memory secret provider for testing, CI, and ephemeral sessions."""

    def __init__(self, secrets: dict[str, bytes] | None = None) -> None:
        self._secrets: dict[str, bytes] = dict(secrets) if secrets is not None else {}
        self._lock = threading.RLock()

    def get_secret(self, name: str) -> bytes:
        with self._lock:
            if name not in self._secrets:
                raise KeyError(f"Secret '{name}' not found in {self.__class__.__name__}")
            return self._secrets[name]

    def store_secret(self, name: str, secret: bytes) -> None:
        if not isinstance(name, str):
            raise TypeError(f"Secret name must be str, got {type(name).__name__}")
        if not isinstance(secret, (bytes, bytearray)):
            raise TypeError(f"Secret value must be bytes, got {type(secret).__name__}")
        with self._lock:
            self._secrets[name] = bytes(secret)

    def delete_secret(self, name: str) -> None:
        with self._lock:
            if name not in self._secrets:
                raise KeyError(f"Secret '{name}' not found in {self.__class__.__name__}")
            del self._secrets[name]

    def clear(self) -> None:
        """Clear all stored in-memory secrets."""
        with self._lock:
            self._secrets.clear()

    def list_secrets(self) -> list[str]:
        with self._lock:
            return list(self._secrets.keys())


# Aliases for Ephemeral backend
EphemeralSecretProvider = EphemeralSecretBackend
InMemorySecretBackend = EphemeralSecretBackend
InMemorySecretProvider = EphemeralSecretBackend


# =====================================================================
# Linux / POSIX Encrypted Keyfile Backend (0600 Permissions + AES-GCM)
# =====================================================================


class LinuxSecretBackend(SecretProvider):
    """Encrypted local file secret provider for Linux and POSIX platforms.

    Uses AES-256-GCM encryption with HKDF key derivation from machine credentials,
    and strictly enforces POSIX 0600 file permissions (owner read/write only).
    """

    MAGIC_HEADER: bytes = b"UCANSEC1"

    def __init__(
        self,
        storage_path: Path | str | None = None,
        master_key: bytes | None = None,
        salt: bytes = DEFAULT_LINUX_SALT,
    ) -> None:
        if storage_path is None:
            config_home = os.environ.get("XDG_CONFIG_HOME")
            base_dir = Path(config_home) if config_home else Path.home() / ".config"
            self.storage_path = base_dir / "universal_can" / "secrets.bin"
        else:
            self.storage_path = Path(storage_path)

        self._master_key = master_key
        self._salt = salt
        self._lock = threading.RLock()

    def _derive_encryption_key(self, salt: bytes) -> bytes:
        """Derive 256-bit AES-GCM key via HKDF from machine seed or explicit master key."""
        if self._master_key is not None:
            seed = self._master_key
        else:
            seed = self._get_machine_seed()

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=DEFAULT_KDF_INFO,
        )
        return hkdf.derive(seed)

    def _get_machine_seed(self) -> bytes:
        """Gather platform machine identifier for user/machine tied key derivation."""
        # 1. Prioritize user-isolated persistent random seed (0600)
        seed_file = self.storage_path.parent / "machine_seed.bin"
        if seed_file.exists():
            try:
                existing = seed_file.read_bytes()
                if existing:
                    return existing
            except OSError as exc:
                raise SecurityError(
                    f"Machine seed file exists but could not be read: {exc}",
                    code="MACHINE_SEED_UNREADABLE",
                    cause=exc,
                ) from exc

        # 2. Gather machine-specific identity to bind together with random entropy
        candidates = [
            Path("/etc/machine-id"),
            Path("/var/lib/dbus/machine-id"),
            Path("/etc/hostid"),
        ]
        machine_frag = b""
        for candidate in candidates:
            try:
                if candidate.is_file():
                    content = candidate.read_bytes().strip()
                    if content:
                        machine_frag = content
                        break
            except OSError:
                continue

        seed = os.urandom(32) if not machine_frag else hashlib.sha256(machine_frag + os.urandom(32)).digest()
        try:
            seed_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(seed_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(seed)
            os.chmod(seed_file, 0o600)
            logger.info("Generated persistent random machine seed for secret backend")
        except OSError as exc:
            # H-6 (P1-5): an unpersisted seed is NOT a soft failure. The old
            # code logged a warning and returned an in-memory seed while
            # _save_all_secrets reported success — the next launch (or, per
            # the same defect, even a later read in the SAME session) derived
            # keys from a different ephemeral seed and every stored secret
            # became permanently undecryptable, with the blackbox HMAC key
            # additionally quarantining all prior chunks. Fail closed so the
            # caller knows persistence is broken BEFORE writing secrets
            # against a seed that will not survive.
            raise SecurityError(
                "Machine seed could not be persisted — refusing to encrypt secrets "
                "against an ephemeral seed that would make them undecryptable",
                code="MACHINE_SEED_UNWRITABLE",
                cause=exc,
            ) from exc
        return seed

    def _load_all_secrets(self) -> dict[str, bytes]:
        """Load and decrypt all secrets from file."""
        if not self.storage_path.exists():
            return {}

        try:
            raw_data = self.storage_path.read_bytes()
            if not raw_data:
                return {}

            magic_len = len(self.MAGIC_HEADER)
            if len(raw_data) < magic_len + 16 + 12 + 16:  # magic + salt(16) + nonce(12) + tag(16)
                raise SecurityError(
                    "Secret store file is corrupted or too short",
                    code="SECURITY_ERROR",
                )

            magic = raw_data[:magic_len]
            if magic != self.MAGIC_HEADER:
                raise SecurityError(
                    "Invalid secret store magic header",
                    code="SECURITY_ERROR",
                )

            salt = raw_data[magic_len : magic_len + 16]
            nonce = raw_data[magic_len + 16 : magic_len + 28]
            ciphertext = raw_data[magic_len + 28 :]

            key = self._derive_encryption_key(salt)
            aesgcm = AESGCM(key)

            try:
                decrypted_json = aesgcm.decrypt(nonce, ciphertext, self.MAGIC_HEADER + salt)
            except Exception as exc:
                raise SecurityError(
                    "Decryption failed: corrupted keyfile or invalid authentication tag",
                    code="SECURITY_ERROR",
                    cause=exc,
                ) from exc

            payload = json.loads(decrypted_json.decode("utf-8"))
            secrets_raw: dict[str, str] = payload.get("secrets", {})
            return {k: base64.b64decode(v.encode("ascii")) for k, v in secrets_raw.items()}

        except SecurityError:
            raise
        except Exception as exc:
            raise SecurityError(
                f"Failed to read secret store: {exc}",
                code="SECURITY_ERROR",
                cause=exc,
            ) from exc

    def _save_all_secrets(self, secrets: dict[str, bytes]) -> None:
        """Encrypt and write all secrets to storage with 0600 file permissions."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

            salt = os.urandom(16)
            key = self._derive_encryption_key(salt)
            aesgcm = AESGCM(key)
            nonce = os.urandom(12)

            serialized_secrets = {
                k: base64.b64encode(v).decode("ascii") for k, v in secrets.items()
            }
            payload = json.dumps({"version": 1, "secrets": serialized_secrets}).encode("utf-8")

            ciphertext = aesgcm.encrypt(nonce, payload, self.MAGIC_HEADER + salt)
            blob = self.MAGIC_HEADER + salt + nonce + ciphertext

            # Write file atomically with 0600 permissions.
            # L-14 (P3-5): the temp name is unique per write — the fixed
            # ".tmp" name let two concurrent writers interleave partial
            # blobs into the same file before either rename.
            temp_file = self.storage_path.with_suffix(
                f".tmp-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
            )

            # Create file with 0600 flags if supported
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            mode = 0o600
            try:
                fd = os.open(temp_file, flags, mode)
                with os.fdopen(fd, "wb") as f:
                    f.write(blob)
            except OSError as exc:
                logger.debug("os.open with 0600 failed, using plain write", extra={"error": str(exc)})
                temp_file.write_bytes(blob)

            try:
                os.chmod(temp_file, 0o600)
            except OSError as exc:
                logger.debug("Could not enforce 0600 on temp secret store", extra={"error": str(exc)})

            temp_file.replace(self.storage_path)

            try:
                os.chmod(self.storage_path, 0o600)
            except OSError as exc:
                logger.debug("Could not enforce 0600 on secret store", extra={"error": str(exc)})

        except Exception as exc:
            raise SecurityError(
                f"Failed to save secret store: {exc}",
                code="SECURITY_ERROR",
                cause=exc,
            ) from exc

    def get_secret(self, name: str) -> bytes:
        with self._lock:
            secrets = self._load_all_secrets()
            if name not in secrets:
                raise KeyError(f"Secret '{name}' not found in {self.__class__.__name__}")
            return secrets[name]

    def store_secret(self, name: str, secret: bytes) -> None:
        if not isinstance(name, str):
            raise TypeError(f"Secret name must be str, got {type(name).__name__}")
        if not isinstance(secret, (bytes, bytearray)):
            raise TypeError(f"Secret value must be bytes, got {type(secret).__name__}")

        with self._lock:
            secrets = self._load_all_secrets()
            secrets[name] = bytes(secret)
            self._save_all_secrets(secrets)

    def delete_secret(self, name: str) -> None:
        with self._lock:
            secrets = self._load_all_secrets()
            if name not in secrets:
                raise KeyError(f"Secret '{name}' not found in {self.__class__.__name__}")
            del secrets[name]
            self._save_all_secrets(secrets)

    def list_secrets(self) -> list[str]:
        with self._lock:
            secrets = self._load_all_secrets()
            return list(secrets.keys())


# Aliases for Linux backend
LinuxSecretProvider = LinuxSecretBackend
LinuxKeyfileSecretProvider = LinuxSecretBackend


# =====================================================================
# Windows DPAPI Secret Backend (CryptProtectData / CryptUnprotectData)
# =====================================================================


class _WindowsDATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


class WindowsDPAPISecretBackend(SecretProvider):
    """Hardware and user-tied secret provider using Windows Data Protection API (DPAPI).

    Uses CryptProtectData / CryptUnprotectData via win32crypt or ctypes.
    If DPAPI is unavailable or running on non-Windows, cleanly falls back to
    an AES-256-GCM encrypted local keyfile.
    """

    def __init__(
        self,
        storage_path: Path | str | None = None,
        entropy: bytes = DEFAULT_DPAPI_ENTROPY,
        fallback_backend: SecretProvider | None = None,
    ) -> None:
        if storage_path is None:
            local_appdata = os.environ.get("LOCALAPPDATA")
            base_dir = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
            self.storage_path = base_dir / "UniversalCAN" / "secrets.dpapi"
        else:
            self.storage_path = Path(storage_path)

        self.entropy = entropy
        self._lock = threading.RLock()
        self._fallback_backend = fallback_backend

    def _is_windows_dpapi_available(self) -> bool:
        """Check if Windows DPAPI is callable on the current platform."""
        if sys.platform != "win32":
            return False
        try:
            # Quick probe
            _ = ctypes.windll.crypt32.CryptProtectData
            return True
        except Exception:
            return False

    def _dpapi_protect(self, data: bytes) -> bytes:
        """Protect data using win32crypt if available or ctypes."""
        # 1. Try win32crypt if present
        try:
            win32crypt = importlib.import_module("win32crypt")
            protected = win32crypt.CryptProtectData(
                data,
                "UniversalCAN Secret",
                self.entropy,
                None,
                None,
                0,
            )
            return bytes(protected)
        except (ImportError, OSError, AttributeError) as exc:
            logger.debug("win32crypt unavailable, falling back to ctypes", extra={"error": str(exc)})

        # 2. Use ctypes crypt32
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        # B7: buffers are bound to named locals so the ctypes GC cannot free
        # them while the BLOB fields still point into their memory.
        data_buffer = ctypes.create_string_buffer(data)
        blob_in = _WindowsDATA_BLOB(len(data), ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_byte)))
        blob_out = _WindowsDATA_BLOB()

        blob_entropy_ptr = None
        entropy_buffer = None
        if self.entropy:
            entropy_buffer = ctypes.create_string_buffer(self.entropy)
            blob_entropy = _WindowsDATA_BLOB(len(self.entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)))
            blob_entropy_ptr = ctypes.byref(blob_entropy)

        res = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            ctypes.c_wchar_p("UniversalCAN Secret"),
            blob_entropy_ptr,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not res:
            err = ctypes.GetLastError()
            raise OSError(f"CryptProtectData failed with error code {err}")

        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def _dpapi_unprotect_single(self, encrypted_data: bytes, entropy_bytes: bytes) -> bytes:
        """Single-attempt unprotect of DPAPI data using win32crypt or ctypes."""
        # 1. Try win32crypt if present
        try:
            win32crypt = importlib.import_module("win32crypt")
            _desc, decrypted = win32crypt.CryptUnprotectData(
                encrypted_data,
                entropy_bytes,
                None,
                None,
                0,
            )
            return bytes(decrypted)
        except (ImportError, OSError, AttributeError) as exc:
            logger.debug("win32crypt unavailable, falling back to ctypes", extra={"error": str(exc)})

        # 2. Use ctypes crypt32
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        data_buffer = ctypes.create_string_buffer(encrypted_data)
        blob_in = _WindowsDATA_BLOB(len(encrypted_data), ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_byte)))
        blob_out = _WindowsDATA_BLOB()

        blob_entropy_ptr = None
        entropy_buffer = None
        if entropy_bytes:
            entropy_buffer = ctypes.create_string_buffer(entropy_bytes)
            blob_entropy = _WindowsDATA_BLOB(len(entropy_bytes), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)))
            blob_entropy_ptr = ctypes.byref(blob_entropy)

        res = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            blob_entropy_ptr,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not res:
            err = ctypes.GetLastError()
            raise OSError(f"CryptUnprotectData failed with error code {err}")

        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def _dpapi_unprotect(self, encrypted_data: bytes) -> bytes:
        """Unprotect DPAPI data with primary entropy and fallback to legacy base entropy (F-06)."""
        try:
            return self._dpapi_unprotect_single(encrypted_data, self.entropy)
        except Exception as primary_exc:
            if self.entropy != DEFAULT_DPAPI_ENTROPY:
                try:
                    return self._dpapi_unprotect_single(encrypted_data, DEFAULT_DPAPI_ENTROPY)
                except Exception:
                    pass
            raise primary_exc

    def _get_fallback(self) -> SecretProvider:
        """Get or initialize fallback encrypted file provider."""
        if self._fallback_backend is None:
            fallback_file = self.storage_path.with_suffix(".fallback.bin")
            self._fallback_backend = LinuxSecretBackend(storage_path=fallback_file)
        return self._fallback_backend

    def _load_store(self) -> dict[str, Any]:
        """Load JSON storage file."""
        if not self.storage_path.exists():
            return {"version": 1, "backend": "windows_dpapi", "secrets": {}}
        try:
            content = self.storage_path.read_text(encoding="utf-8")
            return json.loads(content)  # type: ignore[no-any-return]
        except Exception as exc:
            raise SecurityError(
                f"Failed to read DPAPI secret file: {exc}",
                code="SECURITY_ERROR",
                cause=exc,
            ) from exc

    def _save_store(self, data: dict[str, Any]) -> None:
        """Save JSON storage file atomically."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            # L-14 (P3-5): unique temp name — concurrent writers must not
            # interleave partial files under one fixed ".tmp" path.
            temp_path = self.storage_path.with_suffix(
                f".tmp-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
            )
            temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp_path.replace(self.storage_path)
        except Exception as exc:
            raise SecurityError(
                f"Failed to save DPAPI secret file: {exc}",
                code="SECURITY_ERROR",
                cause=exc,
            ) from exc

    def get_secret(self, name: str) -> bytes:
        with self._lock:
            if not self._is_windows_dpapi_available():
                return self._get_fallback().get_secret(name)

            try:
                store = self._load_store()
                secrets = store.get("secrets", {})
                if name not in secrets:
                    # B3: a miss in the DPAPI store must also check the
                    # fallback depot before raising — the split-brain case
                    # (secret written via fallback during a DPAPI outage).
                    fallback = self._get_fallback()
                    if fallback.has_secret(name):
                        logger.warning("Secret served from fallback depot (missing in DPAPI store)", extra={"name": name})
                        return fallback.get_secret(name)
                    raise KeyError(f"Secret '{name}' not found in {self.__class__.__name__}")

                encrypted_b64 = secrets[name]
                encrypted_blob = base64.b64decode(encrypted_b64.encode("ascii"))
                return self._dpapi_unprotect(encrypted_blob)
            except KeyError:
                raise
            except Exception as exc:
                logger.warning("Windows DPAPI read failed, trying fallback", extra={"error": str(exc)})
                if self._fallback_backend and self._fallback_backend.has_secret(name):
                    return self._fallback_backend.get_secret(name)
                raise SecurityError(
                    f"DPAPI decryption failed for secret '{name}': {exc}",
                    code="SECURITY_ERROR",
                    cause=exc,
                ) from exc

    def store_secret(self, name: str, secret: bytes) -> None:
        if not isinstance(name, str):
            raise TypeError(f"Secret name must be str, got {type(name).__name__}")
        if not isinstance(secret, (bytes, bytearray)):
            raise TypeError(f"Secret value must be bytes, got {type(secret).__name__}")

        with self._lock:
            if not self._is_windows_dpapi_available():
                self._get_fallback().store_secret(name, secret)
                return

            try:
                encrypted_blob = self._dpapi_protect(bytes(secret))
                encrypted_b64 = base64.b64encode(encrypted_blob).decode("ascii")

                store = self._load_store()
                if "secrets" not in store:
                    store["secrets"] = {}
                store["secrets"][name] = encrypted_b64
                self._save_store(store)
            except Exception as exc:
                logger.warning("Windows DPAPI store failed, falling back to encrypted file", extra={"error": str(exc)})
                self._get_fallback().store_secret(name, secret)

    def delete_secret(self, name: str) -> None:
        with self._lock:
            if not self._is_windows_dpapi_available():
                self._get_fallback().delete_secret(name)
                return

            store = self._load_store()
            secrets = store.get("secrets", {})
            if name not in secrets:
                if self._fallback_backend and self._fallback_backend.has_secret(name):
                    self._fallback_backend.delete_secret(name)
                    return
                raise KeyError(f"Secret '{name}' not found in {self.__class__.__name__}")

            del secrets[name]
            self._save_store(store)

    def list_secrets(self) -> list[str]:
        with self._lock:
            if not self._is_windows_dpapi_available():
                return self._get_fallback().list_secrets()

            store = self._load_store()
            return list(store.get("secrets", {}).keys())


# Aliases for Windows DPAPI backend
WindowsDpapiSecretProvider = WindowsDPAPISecretBackend


# =====================================================================
# Factory & Auto-Detection Helper
# =====================================================================


def get_default_secret_provider(
    storage_dir: Path | str | None = None,
    force_ephemeral: bool = False,
) -> SecretProvider:
    """Auto-detect and instantiate the optimal SecretProvider for the current runtime platform.

    - If force_ephemeral=True or UNIVERSAL_CAN_EPHEMERAL_SECRETS=1: EphemeralSecretBackend
    - On Windows: WindowsDPAPISecretBackend (with automatic fallback)
    - On Linux / macOS / POSIX: LinuxSecretBackend (0600 file permissions + AES-256-GCM)

    Args:
        storage_dir: Optional custom storage directory.
        force_ephemeral: If True, forces in-memory ephemeral storage.

    Returns:
        Configured concrete SecretProvider instance.
    """
    if force_ephemeral or os.environ.get("UNIVERSAL_CAN_EPHEMERAL_SECRETS") == "1":
        return EphemeralSecretBackend()

    try:
        if sys.platform == "win32":
            path = Path(storage_dir) / "secrets.dpapi" if storage_dir else None
            return WindowsDPAPISecretBackend(
                storage_path=path,
                entropy=derive_machine_dpapi_entropy(),
            )
        else:
            path = Path(storage_dir) / "secrets.bin" if storage_dir else None
            return LinuxSecretBackend(storage_path=path)
    except Exception as exc:
        logger.warning(
            "Failed to initialize platform SecretProvider, falling back to Ephemeral",
            extra={"error": str(exc)},
        )
        return EphemeralSecretBackend()


# Alias for factory
get_platform_secret_provider = get_default_secret_provider

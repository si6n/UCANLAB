"""Automated delta and full update manager for Universal CAN Platform.

Provides cryptographic SHA-256 verified package download, semver comparison,
and atomic file replacement to keep the desktop client updated seamlessly.
"""

from __future__ import annotations

import base64
import hashlib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.logging import get_logger
from src.security.cloud.client import CloudClient

logger = get_logger("launcher.updater")


@dataclass(slots=True, frozen=True)
class UpdateInfo:
    """Discovered update package metadata."""

    has_update: bool
    current_version: str
    latest_version: str
    download_url: str = ""
    sha256_hash: str = ""
    signature: str = ""  # Base64 encoded Ed25519 signature of the package
    package_size_bytes: int = 0
    release_notes: str = ""
    mandatory: bool = False


class UpdateManager:
    """Manages version checking, SHA-256 verified downloads, and atomic binary updates."""

    def __init__(
        self,
        current_version: str = "13.0.0",
        cloud_client: CloudClient | None = None,
        public_key: ed25519.Ed25519PublicKey | None = None,
        require_signature: bool = True,
    ) -> None:
        self.current_version = current_version
        self.cloud_client = cloud_client
        self.public_key = public_key
        self.require_signature = require_signature

    @staticmethod
    def _parse_semver(v: str) -> tuple[int, int, int]:
        """Convert 'v13.2.1' or '13.2.1' string into comparable tuple."""
        clean = v.lstrip("vV").strip().split("-")[0]
        parts = clean.split(".")
        try:
            return (
                int(parts[0]) if len(parts) > 0 else 0,
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except ValueError:
            return (0, 0, 0)

    def is_newer_version(self, latest_version: str) -> bool:
        """Return True if latest_version > current_version."""
        return self._parse_semver(latest_version) > self._parse_semver(self.current_version)

    def check_for_updates(self, custom_manifest: dict[str, Any] | None = None) -> UpdateInfo:
        """Query Cloud API or manifest for new version releases."""
        if custom_manifest:
            latest_v = custom_manifest.get("version", self.current_version)
            has_up = self.is_newer_version(latest_v)
            return UpdateInfo(
                has_update=has_up,
                current_version=self.current_version,
                latest_version=latest_v,
                download_url=custom_manifest.get("download_url", ""),
                sha256_hash=custom_manifest.get("sha256", ""),
                signature=custom_manifest.get("signature", ""),
                package_size_bytes=custom_manifest.get("size_bytes", 0),
                release_notes=custom_manifest.get("release_notes", ""),
                mandatory=custom_manifest.get("mandatory", False),
            )

        if not self.cloud_client:
            return UpdateInfo(has_update=False, current_version=self.current_version, latest_version=self.current_version)

        try:
            resp = self.cloud_client.request("GET", "/updates/latest")
            if resp.status != 200:
                return UpdateInfo(has_update=False, current_version=self.current_version, latest_version=self.current_version)

            data = resp.json()
            latest_v = data.get("version", self.current_version)
            has_up = self.is_newer_version(latest_v)

            return UpdateInfo(
                has_update=has_up,
                current_version=self.current_version,
                latest_version=latest_v,
                download_url=data.get("download_url", ""),
                sha256_hash=data.get("sha256", ""),
                signature=data.get("signature", ""),
                package_size_bytes=data.get("size_bytes", 0),
                release_notes=data.get("release_notes", ""),
                mandatory=data.get("mandatory", False),
            )
        except Exception as exc:
            logger.warning("Update check failed", extra={"error": str(exc)})
            return UpdateInfo(has_update=False, current_version=self.current_version, latest_version=self.current_version)

    @classmethod
    def verify_file_sha256(cls, file_path: Path | str, expected_hash: str) -> bool:
        """Verify SHA-256 hash of a downloaded file against expected signature."""
        path = Path(file_path)
        if not path.is_file():
            return False

        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)

        calculated = hasher.hexdigest().lower()
        return calculated == expected_hash.strip().lower()

    @classmethod
    def verify_file_signature(
        cls,
        file_path: Path | str,
        signature_b64: str,
        public_key: ed25519.Ed25519PublicKey,
    ) -> bool:
        """Verify Ed25519 signature of the downloaded binary file (SEC-U-001)."""
        path = Path(file_path)
        if not path.is_file() or not signature_b64.strip():
            return False

        try:
            # Pad base64 if needed
            raw_sig = signature_b64.strip()
            pad_len = -len(raw_sig) % 4
            sig_bytes = base64.b64decode(raw_sig + ("=" * pad_len))
            file_bytes = path.read_bytes()
            public_key.verify(sig_bytes, file_bytes)
            return True
        except (InvalidSignature, ValueError, OSError) as exc:
            logger.error("Update package Ed25519 signature verification failed", extra={"error": str(exc)})
            return False

    def download_update(
        self,
        update_info: UpdateInfo,
        destination_path: Path | str,
        progress_callback: Callable[[int, int, float], None] | None = None,
    ) -> bool:
        """Download update file and verify SHA-256 hash and Ed25519 signature.

        L-C-002: an update without a SHA-256 hash is rejected — an unsigned
        package must never execute on the operator's machine.
        L-H-001: download URLs must be HTTPS.
        SEC-U-001: Ed25519 signature verification enforced when configured.
        """
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not update_info.download_url:
            return False

        # L-H-001: only HTTPS transport for update binaries
        if not update_info.download_url.lower().startswith("https://"):
            logger.error(
                "Update download rejected: URL is not HTTPS",
                extra={"download_url": update_info.download_url},
            )
            return False

        # L-C-002: hash-less packages fail closed (supply-chain guard)
        if not update_info.sha256_hash:
            logger.error(
                "Update rejected: manifest provides no SHA-256 hash",
                extra={"version": update_info.latest_version},
            )
            return False

        # Fail closed if signature is strictly required but missing
        if self.require_signature and not update_info.signature:
            logger.error(
                "Update rejected: manifest provides no Ed25519 signature while signature is required",
                extra={"version": update_info.latest_version},
            )
            return False

        try:
            temp_dest = dest.with_suffix(".tmp_download")
            req = urllib.request.Request(update_info.download_url, headers={"User-Agent": "UniversalCAN-Launcher/13.0"})
            with urllib.request.urlopen(req, timeout=60) as response, open(temp_dest, "wb") as out_file:  # nosec: B310
                total_size = int(response.headers.get("Content-Length", update_info.package_size_bytes or 0))
                bytes_downloaded = 0
                while chunk := response.read(65536):
                    out_file.write(chunk)
                    bytes_downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        pct = round((bytes_downloaded / total_size) * 100, 1)
                        progress_callback(bytes_downloaded, total_size, pct)

            if not self.verify_file_sha256(temp_dest, update_info.sha256_hash):
                temp_dest.unlink(missing_ok=True)
                logger.error("Downloaded update SHA-256 hash mismatch. File discarded.")
                return False

            # Verify Ed25519 signature if key or signature present
            if self.public_key is not None and update_info.signature:
                if not self.verify_file_signature(temp_dest, update_info.signature, self.public_key):
                    temp_dest.unlink(missing_ok=True)
                    logger.error("Downloaded update Ed25519 signature mismatch. File discarded.")
                    return False
            elif self.require_signature:
                temp_dest.unlink(missing_ok=True)
                logger.error("Downloaded update rejected: signature requirement not satisfied.")
                return False

            temp_dest.replace(dest)
            logger.info("Update downloaded and verified successfully", extra={"path": str(dest)})
            return True
        except Exception as exc:
            logger.error("Download update failed", extra={"error": str(exc)})
            return False

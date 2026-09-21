"""Automated delta and full update manager for Universal CAN Platform.

Provides cryptographic SHA-256 verified package download, semver comparison,
and atomic file replacement to keep the desktop client updated seamlessly.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
import time
import urllib.error
import urllib.parse
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

# E1 (P1-7): hosts allowed to serve update binaries. The cloud manifest
# names a download_url; a compromised manifest response must not be able to
# pivot the launcher's fetch onto an attacker-controlled origin.
ALLOWED_UPDATE_HOSTS: tuple[str, ...] = (
    "ucan-cloud.si6n.io",
    "cloud.universalcan.io",
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """E1 (P1-7): update downloads must not follow HTTP redirects.

    A 30x on the update URL allowed a man-in-the-middle (or a hostile
    manifest) to pivot the download from https://cdn... to http:// or an
    arbitrary host. The hash/signature checks still guard content
    integrity, but the download itself must stay on the pinned origin —
    scheme downgrade and arbitrary-origin fetch fail closed instead.
    """

    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, hdrs: Any, newurl: Any) -> None:  # type: ignore[override]
        return None


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
    # M-25 (P2-14): tri-state result of the update CHECK itself. The old
    # flow reported every failed check (offline, DNS poisoning, proxy) as
    # "no update", which satisfied the mandatory-update gate downstream —
    # fail-open. Callers must treat check_succeeded=False as "unknown",
    # not "clear".
    check_succeeded: bool = True


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
        self._lock = threading.RLock()

    @staticmethod
    def _parse_semver(v: str) -> tuple[int, int, int] | None:
        """Convert 'v13.2.1' or '13.2.1' string into comparable tuple.

        REVIEW2 #8: returns None on a malformed version string — the old
        silent (0, 0, 0) fallback made a corrupted manifest compare as
        "current" (mandatory security updates silently skipped).
        """
        clean = v.lstrip("vV").strip().split("-")[0]
        parts = clean.split(".")
        try:
            return (
                int(parts[0]) if len(parts) > 0 else 0,
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except ValueError:
            return None

    def is_newer_version(self, latest_version: str) -> bool:
        """Return True if latest_version > current_version.

        REVIEW2 #8: an unparseable version (either side) is UNKNOWN, not
        "not newer" — callers treat None as a failed check so a broken
        manifest can never be silently interpreted as up-to-date.
        """
        latest = self._parse_semver(latest_version)
        current = self._parse_semver(self.current_version)
        if latest is None or current is None:
            logger.warning(
                "Version comparison fell back to unknown (unparseable semver)",
                extra={"latest": latest_version, "current": self.current_version},
            )
            return False
        return latest > current

    def check_for_updates(self, custom_manifest: dict[str, Any] | None = None) -> UpdateInfo:
        """Query Cloud API for new version releases.

        The unsigned ``custom_manifest`` injection path was removed: an
        unsigned dict must never decide update/download routing. Pass
        ``None`` (default). Tests use :meth:`_check_for_updates_unverified`.
        """
        if custom_manifest is not None:
            raise ValueError("custom_manifest is not accepted: unsigned manifests cannot drive updates")
        return self._query_cloud_for_updates()

    def _check_for_updates_unverified(self, custom_manifest: dict[str, Any] | None = None) -> UpdateInfo:
        """Test-only manifest parser without signature verification."""
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
        return self._query_cloud_for_updates()

    def _query_cloud_for_updates(self) -> UpdateInfo:

        if not self.cloud_client:
            # M-25 (P2-14): unknown, not "no update" — see UpdateInfo.
            return UpdateInfo(
                has_update=False,
                current_version=self.current_version,
                latest_version=self.current_version,
                check_succeeded=False,
            )

        try:
            resp = self.cloud_client.request("GET", "/updates/latest")
            if resp.status != 200:
                return UpdateInfo(
                    has_update=False,
                    current_version=self.current_version,
                    latest_version=self.current_version,
                    check_succeeded=False,
                )

            # F5: parse as a JSON object; a malformed body raises a typed
            # ProtocolError(CLOUD_MALFORMED_RESPONSE) instead of a raw
            # JSONDecodeError/AttributeError escaping the update check.
            data = resp.json_object()
            latest_v = data.get("version", self.current_version)
            # REVIEW2 #8: an unparseable manifest version is a FAILED check
            # (check_succeeded=False), never a silent "no update" verdict —
            # the mandatory-update gate treats it as unknown and holds.
            if self._parse_semver(latest_v) is None:
                logger.warning(
                    "Update manifest version unparseable — treating check as failed",
                    extra={"latest_version_raw": repr(latest_v)},
                )
                return UpdateInfo(
                    has_update=False,
                    current_version=self.current_version,
                    latest_version=str(latest_v),
                    check_succeeded=False,
                )
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
            return UpdateInfo(
                has_update=False,
                current_version=self.current_version,
                latest_version=self.current_version,
                check_succeeded=False,
            )

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

    # Verify path: chunked read with hard cap (no full-file read_bytes).
    _VERIFY_CHUNK_BYTES = 8192
    _VERIFY_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB

    @classmethod
    def _updates_root(cls) -> Path:
        return (Path(__file__).resolve().parent.parent.parent / "dist" / "updates").resolve()

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
            # Chunked read (8KB) with 512MB cap — no read_bytes() OOM.
            hasher_chunks: list[bytes] = []
            total = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(cls._VERIFY_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > cls._VERIFY_MAX_BYTES:
                        logger.error("Update package too large for signature verification")
                        return False
                    hasher_chunks.append(chunk)
            file_bytes = b"".join(hasher_chunks)
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
        The final file is pinned under dist/updates/<version>/ — a
        caller-supplied path can only contribute its basename.
        """
        import re as _re

        updates_root = Path(self._updates_root()).resolve()
        version_raw = str(update_info.latest_version or "").strip()
        if not version_raw or not _re.fullmatch(r"[A-Za-z0-9._-]{1,32}", version_raw):
            logger.error("Update rejected: invalid version for destination pinning")
            return False
        # Only the basename of the caller path is honored; directories are
        # discarded so ../../ escapes cannot leave the pinned tree.
        safe_name = Path(str(destination_path)).name
        if not safe_name or safe_name in (".", ".."):
            logger.error("Update rejected: invalid destination filename")
            return False
        dest_dir = (updates_root / version_raw).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = (dest_dir / safe_name).resolve()
        try:
            if not dest.is_relative_to(updates_root):
                logger.error("Update rejected: destination escapes updates root")
                return False
        except Exception:
            return False

        if not update_info.download_url:
            return False

        # L-H-001: only HTTPS transport for update binaries
        if not update_info.download_url.lower().startswith("https://"):
            logger.error(
                "Update download rejected: URL is not HTTPS",
                extra={"download_url": update_info.download_url},
            )
            return False

        # E1 (P1-7): pin the download origin — scheme AND host. A compromised
        # /updates/latest response must not pivot the fetch onto an
        # arbitrary origin (SSRF) or a downgrade host.
        parsed = urllib.parse.urlsplit(update_info.download_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_UPDATE_HOSTS:
            logger.error(
                "Update download rejected: download host is not in the pinned allowlist",
                extra={"host": parsed.hostname, "allowed": list(ALLOWED_UPDATE_HOSTS)},
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

        # Fail closed if signature is strictly required but no public key is configured (Ed25519 trust anchor)
        if self.require_signature and self.public_key is None:
            logger.error(
                "Update rejected: Ed25519 signature is required but no public key is configured",
                extra={"version": update_info.latest_version},
            )
            return False

        with self._lock:
            temp_dest = dest.with_name(
                f"{dest.name}.tmp-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
            )
            try:
                req = urllib.request.Request(update_info.download_url, headers={"User-Agent": "UniversalCAN-Launcher/13.0"})
                # E1 (P1-7): opener with redirect following DISABLED — see
                # _NoRedirectHandler. A 30x raises HTTPError here (fail closed)
                # instead of silently fetching from wherever it points.
                opener = urllib.request.build_opener(_NoRedirectHandler)
                # M-20 (P2-11): hard cap on the downloaded package — the manifest
                # declares its size; a redirect-then-inflate endpoint must not
                # stream an unbounded body to disk.
                MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
                with opener.open(req, timeout=60) as response, open(temp_dest, "wb") as out_file:  # nosec: B310
                    headers = getattr(response, "headers", None)
                    content_length = headers.get("Content-Length", update_info.package_size_bytes or 0) if headers else (update_info.package_size_bytes or 0)
                    total_size = int(content_length)
                    if total_size > MAX_PACKAGE_BYTES:
                        logger.error("Update rejected: declared package size exceeds cap", extra={"declared": total_size})
                        temp_dest.unlink(missing_ok=True)
                        return False
                    bytes_downloaded = 0
                    aborted = False
                    while chunk := response.read(65536):
                        out_file.write(chunk)
                        bytes_downloaded += len(chunk)
                        if bytes_downloaded > MAX_PACKAGE_BYTES:
                            logger.error("Update aborted: package exceeded size cap mid-stream")
                            aborted = True
                            break
                        if progress_callback and total_size > 0:
                            pct = round((bytes_downloaded / total_size) * 100, 1)
                            progress_callback(bytes_downloaded, total_size, pct)
                if aborted:
                    temp_dest.unlink(missing_ok=True)
                    return False

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
                temp_dest.unlink(missing_ok=True)
                logger.error("Download update failed", extra={"error": str(exc)})
                return False

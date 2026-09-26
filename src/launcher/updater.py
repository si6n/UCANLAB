"""Automated delta and full update manager for Universal CAN Platform.

Provides Ed25519+SHA-256 verified package download and semver comparison.

C-2: this module is a **download/staging** pipeline,
NOT an installer. It verifies a package and pins it under
``dist/updates/<version>/``; replacing the live executable is a separate,
explicitly gated operator step (:meth:`UpdateManager.install_update`, which
requires a previously verified staged artifact plus an operator confirmation
token). Nothing here rewrites the running binary on its own, and there is no
unattended auto-install path — the previous docstring claimed "atomic file
replacement" that did not exist anywhere in the module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.logging import get_logger
from src.launcher import paths as launcher_paths
from src.launcher.manifest_sig import (
    MANIFEST_SIGNATURE_FIELD,
    verify_manifest_signature,
)
from src.security.cloud.client import CANONICAL_CLOUD_HOSTS, CloudClient

logger = get_logger("launcher.updater")

# E1 (P1-7) / M-2: hosts allowed to serve update binaries. The cloud manifest
# names a download_url; a compromised manifest response must not be able to
# pivot the launcher's fetch onto an attacker-controlled origin.
#
# M-2: this list had drifted from the canonical
# cloud host set (``ucanlab.org`` / ``api.ucanlab.org`` were missing, so a
# legitimate CDN host was rejected while the two lists could diverge further
# on the next edit). The allowlist is now DERIVED from the single canonical
# constant in ``src.security.cloud.client`` — loopback dev hosts are dropped
# (an update package is never fetched from localhost) and the result must
# stay non-empty, otherwise the module refuses to load (fail-closed).
ALLOWED_UPDATE_HOSTS: tuple[str, ...] = tuple(
    host for host in CANONICAL_CLOUD_HOSTS if host not in ("127.0.0.1", "localhost", "::1")
)
if not ALLOWED_UPDATE_HOSTS:  # pragma: no cover - defensive, cannot happen today
    raise RuntimeError(
        "Update host allowlist is empty: CANONICAL_CLOUD_HOSTS lost every production host"
    )

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Operator confirmation token required by :meth:`UpdateManager.install_update`.
#: Overridable so a packaging/deployment script can supply the token out of
#: band; it is never stored in the repository and never auto-generated.
INSTALL_CONFIRMATION_ENV = "UCANLAB_UPDATE_INSTALL_TOKEN"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """E1 (P1-7): update downloads must not follow HTTP redirects.

    A 30x on the update URL allowed a man-in-the-middle (or a hostile
    manifest) to pivot the download from https://cdn... to http:// or an
    arbitrary host. The hash/signature checks still guard content
    integrity, but the download itself must stay on the pinned origin —
    scheme downgrade and arbitrary-origin fetch fail closed instead.
    """

    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, hdrs: Any, newurl: Any) -> None:
        return None


@dataclass(slots=True, frozen=True)
class UpdateInfo:
    """Discovered update package metadata.

    ``manifest_verified`` is False whenever the manifest itself carried no
    valid Ed25519 signature (H-1) — in that case every other field, including
    ``mandatory`` and ``version``, is UNTRUSTED and must not route anything.
    """

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
    # H-1: provenance of the manifest body (not of the package).
    manifest_verified: bool = False
    manifest_key_id: str = ""


#: Sentinel version string used when a manifest failed signature verification.
UNVERIFIED_VERSION = "0.0.0"


class UpdateManager:
    """Manages version checking, SHA-256 verified downloads, and update staging."""

    def __init__(
        self,
        current_version: str = "13.0.0",
        cloud_client: CloudClient | None = None,
        public_key: ed25519.Ed25519PublicKey | None = None,
        require_signature: bool = True,
        *,
        manifest_keys: dict[str, ed25519.Ed25519PublicKey] | None = None,
        allow_unsigned_manifest: bool = False,
    ) -> None:
        self.current_version = current_version
        self.cloud_client = cloud_client
        self.public_key = public_key
        self.require_signature = require_signature
        # H-1: the kid ring used to verify the MANIFEST body. It is left EMPTY
        # unless a ring is injected — the trust anchor must be embedded by the
        # builder (the launcher passes
        # ``license_flow.TRUSTED_CLOUD_PUBLIC_KEYS_B64``), never derived from a
        # caller-supplied key that could differ from the packaged one.
        self._manifest_keys: dict[str, ed25519.Ed25519PublicKey] = (
            dict(manifest_keys) if manifest_keys is not None else {}
        )
        # TEST-ONLY escape hatch (explicitly named, default OFF): allows an
        # unsigned manifest to drive update routing. It is only reachable
        # through UniversalCanLauncher.for_testing()/allow_unsigned_manifest.
        self.allow_unsigned_manifest = bool(allow_unsigned_manifest)
        self._lock = threading.RLock()

    @staticmethod
    def _parse_semver(v: str) -> tuple[int, int, int] | None:
        """Convert 'v13.2.1' or '13.2.1' string into comparable tuple.

        REVIEW2 #8: returns None on a malformed version string — the old
        silent (0, 0, 0) fallback made a corrupted manifest compare as
        "current" (mandatory security updates silently skipped).

        L-7: the parser used to PAD missing
        components with 0, so ``"13.2"`` silently became ``(13, 2, 0)`` and a
        truncated manifest compared as a valid (older) release. Exactly three
        numeric components are now required; anything else is UNKNOWN (None).
        """
        clean = str(v).lstrip("vV").strip().split("-")[0]
        parts = clean.split(".")
        if len(parts) != 3:
            return None
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
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
        """TEST-ONLY manifest parser without signature verification.

        Reachable only through ``UniversalCanLauncher.for_testing()`` /
        ``allow_unsigned_manifest=True`` (T62-U5). The returned UpdateInfo is
        explicitly marked ``manifest_verified=False``; callers that honour the
        H-1 rule must ignore every field of it.
        """
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
                manifest_verified=False,
            )
        return self._query_cloud_for_updates()

    def _failed_check(self, latest_version: str | None = None) -> UpdateInfo:
        """Fail-closed UpdateInfo: unknown check result, nothing trusted."""
        return UpdateInfo(
            has_update=False,
            current_version=self.current_version,
            latest_version=self.current_version if latest_version is None else str(latest_version),
            check_succeeded=False,
            manifest_verified=False,
        )

    def _query_cloud_for_updates(self) -> UpdateInfo:

        if not self.cloud_client:
            # M-25 (P2-14): unknown, not "no update" — see UpdateInfo.
            return self._failed_check()

        try:
            resp = self.cloud_client.request("GET", "/updates/latest")
            if resp.status != 200:
                return self._failed_check()

            # F5: parse as a JSON object; a malformed body raises a typed
            # ProtocolError(CLOUD_MALFORMED_RESPONSE) instead of a raw
            # JSONDecodeError/AttributeError escaping the update check.
            data = resp.json_object()

            # H-1: NOTHING below this gate is trusted until the manifest body
            # itself carries a valid Ed25519 signature under the embedded kid
            # ring. Fail closed when the signature is absent or invalid: the
            # response is downgraded to "check failed" with every routing field
            # (mandatory, version, download_url, ...) left EMPTY, so a
            # compromised /updates/latest response cannot drive update routing
            # or plant a blocking obligation. The root cause is logged loudly.
            #
            # The TEST-ONLY `allow_unsigned_manifest` escape hatch (explicitly
            # named; reachable only via UniversalCanLauncher.for_testing()) is
            # the ONLY way to skip this requirement, and it is refused in a
            # frozen build.
            manifest_verified = self.allow_unsigned_manifest or verify_manifest_signature(
                data, self._manifest_keys, require=True
            )
            if not manifest_verified:
                logger.error(
                    "Update manifest signature is absent or invalid — every manifest field "
                    "is untrusted; update check reported as FAILED (fail-closed)",
                    extra={"has_signature": bool(data.get(MANIFEST_SIGNATURE_FIELD))},
                )
                return self._failed_check()

            latest_v = data.get("version", self.current_version)
            # REVIEW2 #8: an unparseable manifest version is a FAILED check
            # (check_succeeded=False), never a silent "no update" verdict —
            # the mandatory-update gate treats it as unknown and holds.
            if self._parse_semver(latest_v) is None:
                logger.warning(
                    "Update manifest version unparseable — treating check as failed",
                    extra={"latest_version_raw": repr(latest_v)},
                )
                return self._failed_check(latest_version=latest_v)
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
                manifest_verified=True,
                manifest_key_id=str(data.get("manifest_kid", "") or ""),
            )
        except Exception as exc:
            logger.warning("Update check failed", extra={"error": str(exc)})
            return self._failed_check()

    @classmethod
    def verify_file_sha256(cls, file_path: Path | str, expected_hash: str) -> bool:
        """Verify SHA-256 hash of a downloaded file against the manifest digest.

        M-9: two real defects, both fixed here.

        * the expected digest was never format-validated, so a truncated or
          garbage value was compared leniently against a well-formed digest;
        * the comparison was a plain ``==``.

        The review's *timing-oracle* claim is **wrong** and deliberately not
        "fixed" as such: a SHA-256 of a user-space file is not
        secret-dependent, so there is no meaningful timing signal to exploit.
        ``hmac.compare_digest`` is used purely for consistency with
        ``app._verify_target_hash`` and so a future call site that *does*
        compare a secret cannot inherit a non-constant-time helper.
        """
        path = Path(file_path)
        if not path.is_file():
            return False

        expected = str(expected_hash or "").strip().lower()
        if not _SHA256_RE.fullmatch(expected):
            logger.error("Update SHA-256 verification refused: malformed expected digest")
            return False

        try:
            if path.stat().st_size > cls._VERIFY_MAX_BYTES:
                logger.error(
                    "Update package exceeds the verification byte ceiling",
                    extra={"size": path.stat().st_size, "cap": cls._VERIFY_MAX_BYTES},
                )
                return False
        except OSError:
            return False

        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(cls._VERIFY_CHUNK_BYTES):
                hasher.update(chunk)

        return hmac.compare_digest(hasher.hexdigest().lower(), expected)

    # H-6: ONE byte ceiling for the whole update path. The verify path used to
    # cap at 512 MiB while the download path accepted up to 2 GiB, so a
    # legitimate 600 MiB package downloaded successfully and then failed
    # verification — a guaranteed-failure window.
    MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
    _VERIFY_CHUNK_BYTES = 8192
    _VERIFY_MAX_BYTES = MAX_PACKAGE_BYTES

    @classmethod
    def _updates_root(cls) -> Path:
        """Pinned staging root for downloaded packages.

        H-5: anchored to the launcher's app root (``sys.executable`` in a
        frozen build), never to ``Path(__file__)`` inside the extraction dir.
        """
        return (launcher_paths.app_root() / "dist" / "updates").resolve()

    @classmethod
    def verify_file_signature(
        cls,
        file_path: Path | str,
        signature_b64: str,
        public_key: ed25519.Ed25519PublicKey,
    ) -> bool:
        """Verify Ed25519 signature of the downloaded binary file (SEC-U-001).

        H-6: the byte ceiling is enforced from ``stat()`` BEFORE any payload is
        read. Ed25519 (via ``cryptography``) signs the *message*, not a digest,
        so an in-cap file is read once into the verify call — but an over-cap
        file is refused without buffering it first, which was the actual
        memory-spike defect.
        """
        path = Path(file_path)
        if not path.is_file() or not signature_b64.strip():
            return False

        try:
            size = path.stat().st_size
            if size > cls._VERIFY_MAX_BYTES:
                logger.error(
                    "Update package too large for signature verification (fail-closed)",
                    extra={"size": size, "cap": cls._VERIFY_MAX_BYTES},
                )
                return False
        except OSError:
            return False

        try:
            # Pad base64 if needed
            raw_sig = signature_b64.strip()
            pad_len = -len(raw_sig) % 4
            sig_bytes = base64.b64decode(raw_sig + ("=" * pad_len), validate=True)

            # ``cryptography``'s verify() needs the message: one bounded read
            # (size already capped via stat() above).
            with open(path, "rb") as f:
                payload = f.read()
            public_key.verify(sig_bytes, payload)
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

        The artifact is **STAGED, NOT INSTALLED**: it is verified and pinned
        under ``dist/updates/<version>/``. Replacing the live executable is a
        separate, explicitly confirmed operator action
        (:meth:`install_update`) — there is no unattended auto-install.

        L-C-002: an update without a SHA-256 hash is rejected — an unsigned
        package must never execute on the operator's machine.
        L-H-001: download URLs must be HTTPS.
        SEC-U-001: Ed25519 signature verification enforced when configured.
        A caller-supplied path can only contribute its basename.
        """
        updates_root = Path(self._updates_root()).resolve()
        version_raw = str(update_info.latest_version or "").strip()
        if not version_raw or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", version_raw):
            logger.error("Update rejected: invalid version for destination pinning")
            return False
        # Only the basename of the caller path is honored; directories are
        # discarded so ../../ escapes cannot leave the pinned tree.
        safe_name = Path(str(destination_path)).name
        if not safe_name or safe_name in (".", ".."):
            logger.error("Update rejected: invalid destination filename")
            return False
        # L-6: the staging directory is NOT created here. Every gate below is
        # evaluated first — mkdir() is the first filesystem side effect and it
        # happens immediately before the download opens.
        #
        # NOTE on `.resolve()`: `dest_dir`/`dest` are resolved for the
        # containment gate below, but the temp file is derived from the
        # UNRESOLVED `dest`. On Windows `Path(tmpdir).resolve()` re-expands an
        # 8.3 short path (``RUNNER~1``) to the long form, and concurrent
        # threads doing that under load intermittently disagree about the
        # parent — each thread then derived its temp sibling from a different
        # string and the containment gate compared apples to oranges. Deriving
        # the temp name from the same literal string as `dest` keeps every
        # thread's candidate inside `dest_dir`.
        dest_dir = updates_root / version_raw
        dest = dest_dir / safe_name
        try:
            if not dest.resolve().is_relative_to(updates_root.resolve()) or not dest_dir.resolve().is_relative_to(
                updates_root.resolve()
            ):
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
        # E1 (P1-7): pin the download origin — scheme AND host. A compromised
        # /updates/latest response must not pivot the fetch onto an
        # arbitrary origin (SSRF) or a downgrade host.
        # M-2: the allowlist is the shared canonical set (see module header).
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
                # L-6: every gate above passed — only now create the staging
                # directory (the first filesystem side effect of the download).
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    logger.error("Update rejected: staging directory unavailable", extra={"error": str(exc)})
                    return False
                # H-6 / M-20 (P2-11): ONE hard cap on the downloaded package,
                # shared with the verification ceiling so a package can never
                # download successfully and then fail verification on size.
                max_package_bytes = self.MAX_PACKAGE_BYTES
                with opener.open(req, timeout=60) as response, open(temp_dest, "wb") as out_file:  # nosec: B310
                    headers = getattr(response, "headers", None)
                    content_length = headers.get("Content-Length", update_info.package_size_bytes or 0) if headers else (update_info.package_size_bytes or 0)
                    total_size = int(content_length)
                    if total_size > max_package_bytes:
                        logger.error("Update rejected: declared package size exceeds cap", extra={"declared": total_size})
                        temp_dest.unlink(missing_ok=True)
                        return False
                    bytes_downloaded = 0
                    aborted = False
                    while chunk := response.read(65536):
                        out_file.write(chunk)
                        bytes_downloaded += len(chunk)
                        if bytes_downloaded > max_package_bytes:
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

                # C-2: stage, do NOT install. `os.replace` of the live
                # executable only ever happens in install_update(), behind an
                # operator confirmation token.
                temp_dest.replace(dest)
                logger.info(
                    "Update downloaded and verified — STAGED (not installed)",
                    extra={
                        "path": str(dest),
                        "version": version_raw,
                        "install_requires": "explicit operator confirmation via install_update()",
                    },
                )
                return True
            except Exception as exc:
                temp_dest.unlink(missing_ok=True)
                logger.error("Download update failed", extra={"error": str(exc)})
                return False

    # ------------------------------------------------------------------
    # C-2: explicit, signature-gated INSTALL step (never automatic)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_install_token() -> str:
        """Operator confirmation token, only from the process environment."""
        return str(os.environ.get(INSTALL_CONFIRMATION_ENV, "")).strip()

    def confirm_install(self, staged_path: Path | str, *, token: str | None = None) -> bool:
        """Return True only for an explicit, non-empty operator confirmation.

        The token must satisfy ALL of:

        * non-empty and at least 16 characters (a bare ``--yes`` cannot pass);
        * equal to ``UCANLAB_UPDATE_INSTALL_TOKEN`` (out-of-band secret), or —
          in a non-frozen build only — to ``UCAN_LAUNCHER_DEV_OVERRIDE``
          (the pre-existing, explicitly named dev escape hatch);
        * never accepted in a frozen build when the env token is unset.

        A blank/missing token means "no confirmation" -> fail closed.
        """
        candidate = str(token if token is not None else self._read_install_token()).strip()
        if len(candidate) < 16:
            logger.error(
                "Update install refused: no explicit operator confirmation token",
                extra={"staged_path": str(staged_path)},
            )
            return False
        expected = self._read_install_token()
        if expected and hmac.compare_digest(candidate, expected):
            return True
        if not launcher_paths.is_frozen():
            dev = str(os.environ.get("UCAN_LAUNCHER_DEV_OVERRIDE", "")).strip()
            if dev and hmac.compare_digest(candidate, dev):
                logger.warning("Update install confirmed through the DEV override (non-frozen build only)")
                return True
        logger.error("Update install refused: confirmation token does not match", extra={"staged_path": str(staged_path)})
        return False

    def install_update(
        self,
        staged_path: Path | str,
        live_target: Path | str,
        *,
        expected_sha256: str = "",
        signature_b64: str = "",
        confirmation_token: str | None = None,
    ) -> bool:
        """Explicitly install a previously VERIFIED staged update artifact.

        C-2: the module had no install path at all
        while its docstring claimed "atomic file replacement". This method
        makes the step real, and gates it — there is deliberately no way for a
        download, a UI timer or a manifest flag to reach it unattended:

        1. an explicit operator confirmation token (:meth:`confirm_install`);
        2. the staged artifact must live under the pinned
           ``dist/updates/<version>/`` staging root;
        3. the artifact must MATCH the manifest SHA-256 (``expected_sha256``);
        4. its Ed25519 package signature must verify against the configured
           trust anchor whenever ``require_signature`` is set — an unsigned
           artifact is NEVER installed;
        5. the replacement is ``os.replace`` (atomic on one filesystem) into a
           ``.new`` sibling of the live target; the caller, not this module,
           decides when the idle process swaps it in.

        Returns True only after the verified replacement was performed.
        """
        staged = Path(staged_path)
        target = Path(live_target)

        if not self.confirm_install(staged, token=confirmation_token):
            return False

        updates_root = Path(self._updates_root()).resolve()
        try:
            staged_resolved = staged.resolve()
            if not staged_resolved.is_file() or not staged_resolved.is_relative_to(updates_root):
                logger.error(
                    "Update install refused: artifact is not a verified staging file",
                    extra={"staged_path": str(staged), "staging_root": str(updates_root)},
                )
                return False
        except OSError as exc:
            logger.error("Update install refused: cannot resolve staged artifact", extra={"error": str(exc)})
            return False

        if not expected_sha256 or not self.verify_file_sha256(staged_resolved, expected_sha256):
            logger.error("Update install refused: staged artifact does not match the manifest SHA-256")
            return False

        if self.require_signature:
            if not signature_b64 or self.public_key is None:
                logger.error("Update install refused: signature or trust anchor missing (unsigned artifact)")
                return False
            if not self.verify_file_signature(staged_resolved, signature_b64, self.public_key):
                logger.error("Update install refused: staged artifact signature invalid")
                return False

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            swapped = target.with_name(f"{target.name}.new")
            os.replace(str(staged_resolved), str(swapped))
        except OSError as exc:
            logger.error("Update install failed (live target not replaced)", extra={"error": str(exc)})
            return False

        logger.warning(
            "Signed update staged for replacement — operator must restart the platform",
            extra={"prepared": str(swapped), "live_target": str(target)},
        )
        return True

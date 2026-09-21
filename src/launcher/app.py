"""Universal CAN-Bus Platform - Desktop Launcher & Bootstrap Controller.

Coordinates pre-flight environment checks, cloud authentication / HWID binding,
update validation, and secure execution of the core diagnostic application.
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# D-5: prepend the project root to sys.path ONLY when running from source.
# A frozen build (Nuitka/PyInstaller) must never place the on-disk repo root
# at the front of the import search path — that directory would then outrank
# every bundled module and let loose files hijack `import src...`. Frozen
# builds resolve imports from their own bundle (sys.frozen / sys._MEIPASS).
if not getattr(sys, "frozen", False) and not getattr(sys, "_MEIPASS", None):
    _PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError
from src.core.logging import get_logger
from src.launcher import paths as launcher_paths
from src.launcher.auth import AuthStatus, LauncherAuthManager
from src.launcher.prereqs import PrereqChecker, PrereqStatus
from src.launcher.updater import UpdateInfo, UpdateManager
from src.security.cloud.license_flow import DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64

logger = get_logger("launcher.app")

# M-5: the embedded cloud public key is NOT re-declared here. It is imported
# from ``src.security.cloud.license_flow`` (the single source of truth used by
# ``launcher.auth`` and the UI), so the three copies that could silently drift
# apart are now one. Re-exported under the historical module-level name because
# existing call sites/tests refer to ``src.launcher.app`` for it.
__all__ = [
    "DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64",
    "TARGET_MANIFEST_DIR",
    "TARGET_MANIFEST_ENV",
    "TARGET_MANIFEST_NAME",
    "LauncherPreflightReport",
    "UniversalCanLauncher",
    "main",
]

# D-4: the launcher spawns whatever executable `resolve_target_executable`
# happens to find on disk. If ``<root>/dist/`` is writable (e.g. via the
# C-1 file-path surface), an attacker could drop an unsigned
# ``ucanlab.exe``. Every frozen candidate must therefore match a hash
# manifest that lives OUTSIDE the writable dist/ tree.
TARGET_MANIFEST_NAME = "target.hash"
TARGET_MANIFEST_DIR = "data"
TARGET_MANIFEST_ENV = "UCANLAB_TARGET_MANIFEST"
#: Directory (relative to the app root) that holds the packaged payload.
TARGET_DIST_DIR = "dist"


def _manifest_path() -> Path:
    """Resolve the target-hash manifest (env override for tests/packaging).

    In a frozen build (sys.frozen), environment overrides are strictly ignored
    and the manifest must be read from the packaged, non-writable root (T62-U2).
    """
    import os
    import sys

    is_frozen = getattr(sys, "frozen", False)
    if not is_frozen:
        override = os.environ.get(TARGET_MANIFEST_ENV, "").strip()
        if override:
            return Path(override)

    if is_frozen:
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / TARGET_MANIFEST_DIR / TARGET_MANIFEST_NAME

    # H-5: the source-tree manifest lives at the bundled-resource root.
    return launcher_paths.resource_root() / TARGET_MANIFEST_DIR / TARGET_MANIFEST_NAME


def _sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file (never loads large binaries into RAM)."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_target_manifest(manifest_path: Path | None = None) -> str:
    """Read the expected SHA-256 hex digest from the manifest.

    Accepts a bare digest line or ``<digest>  <name>`` (sha256sum format).
    Returns the lowercased hex digest, or raises ``RuntimeError`` when the
    manifest is missing/empty (callers treat that as fail-closed).
    """
    path = manifest_path if manifest_path is not None else _manifest_path()
    if not path.is_file():
        raise RuntimeError(f"Hedef hash manifesti bulunamadi: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        if token and all(c in "0123456789abcdefABCDEF" for c in token) and len(token) == 64:
            return token.lower()
    raise RuntimeError(f"Hedef hash manifesti gecersiz veya bos: {path}")


def _verify_target_hash(target: Path, expected_hex: str) -> bool:
    """Constant-time-ish comparison of a file's SHA-256 against the manifest."""
    import hmac

    try:
        actual = _sha256_file(target)
    except OSError:
        return False
    return hmac.compare_digest(actual, expected_hex.lower())


def _dev_override_enabled() -> bool:
    """True only in an explicit non-frozen dev environment (L-C-001).

    Production (frozen) builds never honor the override. The legacy
    PYTEST_CURRENT_TEST branch was removed: a local env var must never
    decide a security gate in a shipped binary.
    """
    import os
    import sys

    if getattr(sys, "frozen", False):
        return False
    return os.environ.get("UCAN_LAUNCHER_DEV_OVERRIDE") == "1"


@dataclass(slots=True, frozen=True)
class LauncherPreflightReport:
    """Consolidated preflight readiness report."""

    can_launch: bool
    prereqs: list[PrereqStatus]
    auth_status: AuthStatus
    update_info: UpdateInfo
    target_executable: Path


class UniversalCanLauncher:
    """Master Launcher orchestration engine."""

    def __init__(
        self,
        current_version: str = "13.0.0",
        *,
        allow_unsigned_manifest: bool = False,
        auth_manager: LauncherAuthManager | None = None,
        update_manager: UpdateManager | None = None,
    ) -> None:
        self.version = current_version
        # M-25 (P2-14) / H-5: the mandatory-update obligation record lives in
        # the PERSISTENT per-user data root, not in the source tree (which does
        # not exist in a frozen build) and not in the writable dist/ tree.
        self.root_dir = launcher_paths.repo_root()
        self.app_root = launcher_paths.app_root()
        self._allow_unsigned_manifest = bool(allow_unsigned_manifest) and not getattr(sys, "frozen", False)
        self.auth_manager = auth_manager or LauncherAuthManager()
        # M-5: fail CLOSED exactly like auth.py:75-88. A corrupt embedded key
        # used to be swallowed into `pub_key = None`, which silently disabled
        # manifest/package verification (fail-open). Decode strictly and raise.
        try:
            pub_bytes = base64.b64decode(DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64, validate=True)
            pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        except Exception as exc:
            logger.critical(
                "Embedded cloud public key is invalid — refusing to start with update "
                "verification disabled (fail-closed)",
                extra={"error": str(exc)},
            )
            raise LicenseError(
                "Embedded cloud public key is invalid or corrupt; refusing to run with "
                "update/manifest verification disabled.",
                code="EMBEDDED_KEY_INVALID",
                cause=exc,
            ) from exc

        resolved_update_manager = update_manager or UpdateManager(
            current_version=self.version,
            cloud_client=self.auth_manager.client,
            public_key=pub_key,
            require_signature=True,
            # H-1: the manifest body is verified against the embedded key ring
            # (same trust anchor as the binary), not just the single key.
            manifest_keys=self._embedded_key_ring(),
            allow_unsigned_manifest=self._allow_unsigned_manifest,
        )
        if update_manager is not None and self._allow_unsigned_manifest:
            # T62-U5: an injected test manager participates in the explicit
            # test-only escape hatch; production never reaches this branch.
            update_manager.allow_unsigned_manifest = True
        self.update_manager = resolved_update_manager

    @staticmethod
    def _embedded_key_ring() -> dict[str, ed25519.Ed25519PublicKey]:
        """Decode the embedded Ed25519 kid ring (fail-closed on any entry).

        The ring is the same SSOT `auth.py` uses for license tickets, so the
        manifest and the package are verified against one trust anchor.
        """
        from src.security.cloud.license_flow import TRUSTED_CLOUD_PUBLIC_KEYS_B64

        ring: dict[str, ed25519.Ed25519PublicKey] = {}
        for kid, b64 in TRUSTED_CLOUD_PUBLIC_KEYS_B64.items():
            try:
                ring[str(kid)] = ed25519.Ed25519PublicKey.from_public_bytes(
                    base64.b64decode(b64, validate=True)
                )
            except Exception as exc:
                logger.critical(
                    "Embedded cloud key ring entry is invalid — fail-closed",
                    extra={"kid": kid, "error": str(exc)},
                )
                raise LicenseError(
                    f"Embedded cloud key ring entry '{kid}' is invalid or corrupt; refusing to run.",
                    code="EMBEDDED_KEY_INVALID",
                    cause=exc,
                ) from exc
        return ring

    @classmethod
    def for_testing(
        cls,
        current_version: str = "13.0.0",
        *,
        auth_manager: LauncherAuthManager | None = None,
        update_manager: UpdateManager | None = None,
    ) -> UniversalCanLauncher:
        """Explicit test-only constructor allowing unsigned update manifests (T62-U5)."""
        return cls(
            current_version=current_version,
            allow_unsigned_manifest=True,
            auth_manager=auth_manager,
            update_manager=update_manager,
        )

    @classmethod
    def resolve_target_executable(cls) -> Path:
        """Find the core application executable or main.py entry point.

        H-5: the root is anchored to the LAUNCHER's own directory
        (``sys.executable`` in a frozen build, the checkout when running from
        source) instead of ``Path(__file__).parent.parent.parent``. The old
        anchor pointed at the *build machine's* source tree, so a frozen
        launcher never found its sibling payload.
        """
        root = launcher_paths.app_root()

        # 1. Check for Nuitka / PyInstaller compiled standalone executable.
        #    Both the launcher and its payload are staged under <app_root>/dist/
        #    by the build scripts; the bare-root entries cover an installer
        #    that places the payload next to the launcher.
        candidate_exes = [
            root / TARGET_DIST_DIR / "ucanlab.exe",
            root / TARGET_DIST_DIR / "main.dist" / "ucanlab.exe",
            root / TARGET_DIST_DIR / "Universal-CAN-Tool.exe",
            root / TARGET_DIST_DIR / "Universal_CAN_Diagnostic.exe",
            root / TARGET_DIST_DIR / "main.dist" / "Universal-CAN-Tool.exe",
            root / TARGET_DIST_DIR / "main.dist" / "Universal_CAN_Diagnostic.exe",
            root / "ucanlab.exe",
            root / "Universal-CAN-Tool.exe",
            root / "Universal_CAN_Diagnostic.exe",
        ]
        for candidate in candidate_exes:
            if candidate.is_file():
                return candidate

        # 2. Fallback to raw Python main.py (source checkout only). A frozen
        #    build bundles no .py sources, so the caller fails closed because
        #    the resolved path does not exist.
        return root / "src" / "main.py"

    def run_preflight(self, custom_update_manifest: dict[str, Any] | None = None) -> LauncherPreflightReport:
        """Execute all environment, auth, and update checks."""
        prereqs = PrereqChecker.run_all_checks()
        has_critical_failures = any(not p.is_available and p.is_critical for p in prereqs)

        auth_status = self.auth_manager.get_current_status()
        if custom_update_manifest is not None:
            # D-3 / T62-U5: the unsigned manifest parser is TEST-ONLY. In production
            # (or any launcher instance without explicit allow_unsigned_manifest injection),
            # this parameter must be unreachable — an unsigned dict can never drive update
            # routing. Environment variable spoofing (PYTEST_CURRENT_TEST) is eliminated.
            if not self._allow_unsigned_manifest:
                logger.critical(
                    "Unsigned update manifest refused outside an explicit test constructor — launch blocked",
                    extra={"reason": "custom_update_manifest is test-only"},
                )
                raise PermissionError(
                    "custom_update_manifest is a test-only path and is not reachable in production"
                )
            update_info = self.update_manager._check_for_updates_unverified(custom_manifest=custom_update_manifest)
        else:
            update_info = self.update_manager.check_for_updates()
        target_exe = self.resolve_target_executable()

        has_blocking_mandatory_update = update_info.has_update and update_info.mandatory
        # H-1: `mandatory` is only trustworthy when the manifest body carried a
        # valid Ed25519 signature. An unverified manifest can never create (or
        # clear) a blocking obligation — it is already reported as a FAILED
        # check by the updater, and the recorded-obligation rule below applies.
        manifest_trusted = bool(update_info.manifest_verified) or self._allow_unsigned_manifest
        if update_info.has_update and update_info.mandatory and not manifest_trusted:
            logger.error(
                "Unsigned update manifest claimed mandatory=True — flag IGNORED (fail-closed)",
                extra={"version": update_info.latest_version},
            )
            has_blocking_mandatory_update = False

        # H-3: a MISSING obligation file is not "no obligation" any more. The
        # obligation is normally symmetric (recorded while a mandatory release
        # is outstanding, cleared once the running version satisfies it). A
        # file that has been deleted must therefore be treated as TAMPERED.
        recorded_obligation = self._load_recorded_mandatory_obligation()

        # M-25 (P2-14) / H-3: a FAILED update check is not a green light. If
        # the launcher has a recorded mandatory-update obligation (persisted
        # from a previous successful check), an unreachable update server must
        # not clear it — that is exactly the offline-attack window the gate
        # exists for. Without a recorded obligation the failure stays
        # non-blocking (a fresh install behind a flaky network must still
        # start), but the report carries check_succeeded=False so the UI can
        # surface the degraded state.
        if not update_info.check_succeeded and recorded_obligation is not None:
            has_blocking_mandatory_update = True
            logger.error(
                "Update check failed while a mandatory update obligation is on record — launch blocked",
                extra={"recorded_min_version": recorded_obligation},
            )

        can_launch = (
            not has_critical_failures
            and target_exe.exists()
            and not has_blocking_mandatory_update
            and bool(auth_status.has_valid_license)
        )
        # L-1: a machine with no license / an expired ticket / a device
        # mismatch must never reach the core binary. Fail-closed and logged
        # at CRITICAL with the tier so the operator can act on it.
        if not auth_status.has_valid_license:
            logger.critical(
                "License gate CLOSED - launch blocked: valid license required",
                extra={
                    "tier": getattr(auth_status, "tier", "UNKNOWN"),
                    "reason": getattr(auth_status, "error", "") or "no valid license",
                    "widening_allowed": False,
                },
            )

        # H-3: a recorded obligation whose file has DISAPPEARED (or whose seal
        # cannot be validated) is TAMPERED, never "cleared". Block launch and
        # say why: deleting local state must not restore the fail-open window
        # the obligation record exists to close.
        if recorded_obligation in self._TAMPERED_OBLIGATION_SENTINELS:
            has_blocking_mandatory_update = True
            can_launch = False
            logger.critical(
                "Mandatory-update obligation record is missing/tampered — launch blocked (fail-closed)",
                extra={"obligation_state": recorded_obligation, "path": str(self._obligation_path())},
            )

        if update_info.check_succeeded and manifest_trusted and update_info.has_update and update_info.mandatory:
            record_ok = self._record_mandatory_obligation(update_info.latest_version)
            if not record_ok:
                logger.error("Failed to persist mandatory update obligation — launch blocked (fail-closed)")
                can_launch = False
        elif (
            update_info.check_succeeded
            and manifest_trusted
            and update_info.has_update
            and not update_info.mandatory
            and recorded_obligation not in self._TAMPERED_OBLIGATION_SENTINELS
        ):
            # H-2: a non-mandatory newer release must NOT clear the obligation
            # unconditionally — that let a single spoofed/legitimate
            # non-mandatory release release a device that never installed the
            # obligated security update. The obligation is cleared only when
            # the RUNNING version already satisfies it.
            self._clear_mandatory_obligation_if_satisfied(recorded_obligation)

        return LauncherPreflightReport(
            can_launch=can_launch,
            prereqs=prereqs,
            auth_status=auth_status,
            update_info=update_info,
            target_executable=target_exe,
        )

    # ------------------------------------------------------------------
    # M-25 (P2-14): persisted mandatory-update obligation
    # ------------------------------------------------------------------

    # Sealed mandatory-update obligation (<ver>.<hmac>) + symmetric "armed"
    # marker (H-3). BOTH files must exist once an obligation has been
    # recorded; the marker is what makes deletion detectable.
    _OBLIGATION_HMAC_KEY_NAME = "LAUNCHER_OBLIGATION_HMAC"
    _OBLIGATION_ARMED_KEY_NAME = "LAUNCHER_OBLIGATION_ARMED"
    #: Non-version sentinels returned by the loader; both mean "tampered".
    _TAMPERED_OBLIGATION_SENTINELS = frozenset({"TAMPERED", "IO_ERROR", "MISSING_WHILE_ARMED"})
    # M-7 (verify/launcher.md:294 + README.md:72-73): the documented flags are
    # forwarded to the child. `-i/-c/-b` are the short forms of
    # --interface/--channel/--bitrate and `--log-level` is documented too — a
    # non-allowlisted flag was silently DROPPED, so a documented invocation
    # started the app with default hardware settings.
    _ALLOWED_EXTRA_ARGS = frozenset(
        {
            "--channel",
            "-c",
            "--interface",
            "-i",
            "--bitrate",
            "-b",
            "--log-level",
            "--cli",
            "--help",
        }
    )

    def _obligation_path(self) -> Path:
        """H-5/H-3: obligation record lives in the persistent per-user data root.

        It used to live under ``<source tree>/dist/``: a directory that does
        not exist in a frozen build (so the record silently never persisted)
        and one that the update DOWNLOAD path itself writes to (so the record
        could be clobbered by a download). The app-data root is outside both.
        """
        return launcher_paths.app_data_root() / "launcher_mandatory_update.txt"

    def _obligation_armed_path(self) -> Path:
        return launcher_paths.app_data_root() / "launcher_mandatory_update.armed"

    def _obligation_armed(self) -> bool:
        """True when an obligation has EVER been recorded / cleared by this install.

        Persisted in the secret vault so it survives a file deletion, a
        reinstall of the same user profile and a dist/ wipe — which is exactly
        the state an attacker would produce to re-open the gate.
        """
        try:
            secrets = getattr(self.auth_manager, "secrets", None)
            if secrets is None:
                from src.safety.secret_provider import get_default_secret_provider

                secrets = get_default_secret_provider()
            return bool(secrets.has_secret(self._OBLIGATION_ARMED_KEY_NAME))
        except Exception:
            return False

    def _arm_obligation_record(self) -> None:
        """One-way arming marker: once set it is never cleared (fail-closed)."""
        try:
            secrets = getattr(self.auth_manager, "secrets", None)
            if secrets is None:
                from src.safety.secret_provider import get_default_secret_provider

                secrets = get_default_secret_provider()
            if not secrets.has_secret(self._OBLIGATION_ARMED_KEY_NAME):
                secrets.store_secret(self._OBLIGATION_ARMED_KEY_NAME, b"1")
            self._obligation_armed_path().parent.mkdir(parents=True, exist_ok=True)
            self._obligation_armed_path().write_text("armed\n", encoding="utf-8")
        except Exception as exc:  # pragma: no cover - best effort, loud on failure
            logger.error("Could not arm the obligation record", extra={"error": str(exc)})

    def _obligation_hmac_key(self) -> bytes | None:
        """Return the HMAC key for the obligation seal, or None (fail-closed)."""
        try:
            secrets = getattr(self.auth_manager, "secrets", None)
            if secrets is None:
                from src.safety.secret_provider import get_default_secret_provider

                secrets = get_default_secret_provider()
            if not secrets.has_secret(self._OBLIGATION_HMAC_KEY_NAME):
                return None
            key = bytes(secrets.get_secret(self._OBLIGATION_HMAC_KEY_NAME))
            if len(key) < 16:
                return None
            return key
        except Exception:
            return None

    def _load_recorded_mandatory_obligation(self) -> str | None:
        """Return the obligated minimum version, or ``None`` when none is recorded.

        H-3: ABSENCE IS NO LONGER "NO OBLIGATION" once the record has been
        armed. An armed install (a mandatory release was recorded earlier —
        see ``_arm_obligation_record``) must still have its sealed file; a
        missing file is reported as ``MISSING_WHILE_ARMED`` and the caller
        blocks. The sealed record carries an HMAC over the version and the
        armed marker lives in the secret vault, so neither the file contents
        nor its removal can be forged without the vault key.
        """
        try:
            import hashlib as _hashlib
            import hmac as _hmac

            path = self._obligation_path()
            if not path.exists():
                if self._obligation_armed():
                    logger.critical(
                        "Mandatory-update obligation file is MISSING although an obligation "
                        "was previously recorded — treated as TAMPERED (fail-closed)",
                        extra={"path": str(path)},
                    )
                    return "MISSING_WHILE_ARMED"
                return None
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                return "TAMPERED" if self._obligation_armed() else None
            key = self._obligation_hmac_key()
            if key is None:
                logger.error("Obligation seal key unavailable — fail-closed (blocking launch on failed check)")
                return "TAMPERED"
            ver, sep, mac = raw.rpartition(".")
            if not sep or not ver or not mac:
                logger.error("Obligation file malformed — fail-closed")
                return "TAMPERED"
            expected = _hmac.new(key, ver.encode("utf-8"), _hashlib.sha256).hexdigest()
            if not _hmac.compare_digest(expected, mac):
                logger.error("Obligation seal mismatch — fail-closed")
                return "TAMPERED"
            return ver
        except OSError as exc:
            logger.error("Obligation file read I/O error — fail-closed", extra={"error": str(exc)})
            return "IO_ERROR"

    def _record_mandatory_obligation(self, min_version: str) -> bool:
        try:
            import hashlib as _hashlib
            import hmac as _hmac
            import os as _os

            key: bytes | None = self._obligation_hmac_key()
            if key is None:
                # First-time seal: create a stable key, then seal. If the
                # vault is unavailable, fail closed without writing an
                # unsigned file.
                try:
                    secrets = getattr(self.auth_manager, "secrets", None)
                    if secrets is None:
                        from src.safety.secret_provider import get_default_secret_provider

                        secrets = get_default_secret_provider()
                    key = _os.urandom(32)
                    secrets.store_secret(self._OBLIGATION_HMAC_KEY_NAME, key)
                except Exception as exc:
                    logger.error("Failed to provision obligation seal key — fail-closed", extra={"error": str(exc)})
                    return False
            ver = str(min_version).strip()
            mac = _hmac.new(key, ver.encode("utf-8"), _hashlib.sha256).hexdigest()
            path = self._obligation_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{ver}.{mac}", encoding="utf-8")
            # H-3: arm the record so a later DELETION is detectable.
            self._arm_obligation_record()
            return True
        except OSError as exc:
            logger.error("Failed to record mandatory update obligation — fail-closed", extra={"error": str(exc)})
            return False

    def _clear_mandatory_obligation(self) -> None:
        """Remove the sealed record (the armed marker is one-way and stays)."""
        try:
            self._obligation_path().unlink(missing_ok=True)
            self._arm_obligation_record()
        except OSError:
            pass

    def _clear_mandatory_obligation_if_satisfied(self, recorded: str | None) -> bool:
        """H-2: clear the obligation only when the RUNNING version satisfies it.

        The old code cleared the record whenever a newer non-mandatory version
        was seen. A device that never installed the obligated security update
        (e.g. release 14.0 mandatory, then 13.5 non-mandatory published out of
        order — or a spoofed manifest) had its block removed. The record is
        now cleared only when ``current_version >= recorded minimum``.

        With nothing recorded there is nothing to preserve, so the (already
        non-existent) record stays absent — the caller reaches this branch
        only after the tampered sentinels were excluded.
        """
        from src.launcher.updater import UpdateManager as _UpdateManager

        if recorded is None:
            self._clear_mandatory_obligation()
            return True
        current = _UpdateManager._parse_semver(self.version)
        obligated = _UpdateManager._parse_semver(recorded)
        if current is None or obligated is None:
            logger.error(
                "Cannot prove the running version satisfies the recorded obligation — record kept (fail-closed)",
                extra={"current": self.version, "recorded": recorded},
            )
            return False
        if current >= obligated:
            logger.info(
                "Running version satisfies the recorded mandatory obligation — record cleared",
                extra={"current": self.version, "recorded": recorded},
            )
            self._clear_mandatory_obligation()
            return True
        logger.warning(
            "Running version does NOT satisfy the recorded mandatory obligation — record kept",
            extra={"current": self.version, "recorded": recorded},
        )
        return False

    @classmethod
    def _filter_extra_args(cls, args: list[str] | None) -> list[str]:
        """Allowlist unknown CLI args forwarded to the child process."""
        if not args:
            return []
        filtered: list[str] = []
        i = 0
        while i < len(args):
            tok = str(args[i])
            name = tok.split("=", 1)[0]
            if name in cls._ALLOWED_EXTRA_ARGS:
                filtered.append(tok)
                # Allow a separate value token (e.g. "--channel vcan0") when
                # the flag was not given as --flag=value.
                if "=" not in tok and i + 1 < len(args) and not str(args[i + 1]).startswith("-"):
                    filtered.append(str(args[i + 1]))
                    i += 1
            else:
                logger.warning("Dropping non-allowlisted launcher arg", extra={"arg": tok[:64]})
            i += 1
        return filtered

    @classmethod
    def verify_resolved_target(cls, target: Path) -> bool:
        """D-4: verify a frozen target against the hash manifest.

        Returns ``True`` when the file matches the manifest digest. Raises
        ``RuntimeError`` (fail-closed, blocked launch) when:

        * the manifest is missing/invalid AND the target is a frozen binary
          (an unsigned exe in ``dist/`` is exactly the takeover D-4 covers);
        * the digest does not match.

        The raw-Python entry point (``.py``) is the trusted-development
        fallback and is allowed without a manifest.
        """
        if target.suffix.lower() == ".py":
            return True

        expected = _load_target_manifest()  # raises RuntimeError if absent
        if not _verify_target_hash(target, expected):
            logger.error(
                "Hedef ikili hash manifestiyle eslesmiyor - baslatma reddedildi",
                extra={"target": str(target), "expected": expected},
            )
            raise RuntimeError(
                f"Hedef ikili hash dogrulamasi basarisiz (imzasiz/degistirilmis): {target}"
            )
        logger.info("Hedef ikili hash manifesti dogrulandi", extra={"target": str(target)})
        return True

    def launch_main_app(self, extra_args: list[str] | None = None) -> int:
        """Spawn the core application executable with integrity checks.

        M-3 (verify/launcher.md:433-448): this method is a public API and a
        second entry point. It used to check only "file exists + hash matches",
        so an in-process caller (a bridge method, a plugin, a future UI wiring)
        reached the payload with no license / no prerequisite check at all.
        The preflight gate is now evaluated HERE too, with the same
        fail-closed rule as ``main()``.
        """
        args = self._filter_extra_args(extra_args or [])

        # M-3: the license/prerequisite gate is mandatory, not a main() detail.
        # The test-only unsigned-manifest injection is deliberately NOT
        # propagated, so a caller cannot smuggle a manifest into this gate.
        report = self.run_preflight()
        if not report.can_launch:
            logger.critical(
                "launch_main_app refused: preflight gate closed (fail-closed)",
                extra={
                    "has_valid_license": bool(report.auth_status.has_valid_license),
                    "check_succeeded": bool(report.update_info.check_succeeded),
                    "mandatory_update": bool(report.update_info.has_update and report.update_info.mandatory),
                },
            )
            return 1

        target = self.resolve_target_executable().resolve()

        if not target.is_file():
            logger.error("Target executable not found or not a valid file", extra={"target": str(target)})
            return 1

        # D-4: refused before spawn when the binary does not match the
        # manifest — a writable dist/ no longer means code execution.
        try:
            self.verify_resolved_target(target)
        except RuntimeError as exc:
            logger.error("Target integrity gate blocked launch", extra={"error": str(exc)})
            return 1

        # M-4: case-insensitive, matching verify_resolved_target and the .exe
        # branch below — `Main.PY` used to take the wrong branch.
        if target.suffix.lower() == ".py":
            import hashlib as _hashlib

            try:
                h = _hashlib.sha256()
                with open(target, "rb") as _f:
                    while _chunk := _f.read(65536):
                        h.update(_chunk)
                logger.warning(
                    "Launching unsigned Python fallback entry point",
                    extra={"target": str(target), "sha256": h.hexdigest()},
                )
            except OSError as exc:
                logger.error("Refusing to execute unreadable fallback script", extra={"error": str(exc)})
                return 1
            cmd = [sys.executable, str(target)] + args
        elif target.suffix.lower() == ".exe":
            cmd = [str(target)] + args
        else:
            logger.error("Refusing to execute unverified binary extension", extra={"target": str(target)})
            return 1

        logger.info("Launching Universal CAN Platform", extra={"target": str(target), "cli_args": args})
        # C-1 (verify/launcher.md:473): NO timeout. The launcher is the PARENT
        # of the platform process; `timeout=300` made subprocess.call() kill a
        # perfectly healthy diagnostic session after five minutes and returned
        # a negative exit code to an operator who only saw the app vanish. The
        # child's lifetime is the operator's to control.
        return subprocess.call(cmd, shell=False)


def _prompt_license_key() -> str:
    """Read the activation key without exposing it on argv (M-8).

    ``--activate <KEY>`` put the secret into the process list (any local user
    can read the command line), the shell history, and any process monitor.
    The key is now read interactively with echo disabled.
    """
    import getpass

    try:
        return str(getpass.getpass("License key (input hidden): ")).strip()
    except (EOFError, KeyboardInterrupt):
        print("")
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal CAN Platform - Launcher & Auto-Updater")
    parser.add_argument("--check-only", action="store_true", help="Run preflight checks and exit")
    # M-8: a FLAG, not a value. The key never travels on argv.
    parser.add_argument("--activate", action="store_true", help="Activate a cloud license key (prompts, hidden input)")
    parser.add_argument("--device-name", type=str, default="Desktop Diagnostic Tool", help="Device name for registration")
    parser.add_argument("--launch", action="store_true", help="Launch the main application immediately")

    args, unknown = parser.parse_known_args()
    launcher = UniversalCanLauncher()

    if args.activate:
        # L-H-004: never echo the license key itself to the console/logs
        license_key = _prompt_license_key()
        if not license_key:
            print("Activation Failed: no license key provided.")
            return 1
        print("Activating license key...")
        try:
            claims = launcher.auth_manager.activate_with_key(license_key, device_name=args.device_name)
            print(f"License Activated Successfully! Tier: {claims.tier}, Features: {list(claims.features)}")
        except Exception as exc:
            print(f"Activation Failed: {exc}")
            return 1

    report = launcher.run_preflight()
    print("=" * 65)
    print("UNIVERSAL CAN-BUS PLATFORM - LAUNCHER PRE-FLIGHT DIAGNOSTICS")
    print("=" * 65)
    print(f"Version: v{launcher.version} | HWID: {report.auth_status.hwid[:8]}…")
    print(f"License Tier: {report.auth_status.tier} | Active: {report.auth_status.has_valid_license}")
    print(f"Target Binary: {report.target_executable}")
    print("-" * 65)
    print("Prerequisites & Drivers:")
    for p in report.prereqs:
        icon = "OK" if p.is_available else ("FAIL" if p.is_critical else "WARN")
        print(f"  [{icon}] {p.name}: {p.details}")
    print("-" * 65)

    # L-8: a FAILED check is not a green light. Without this the operator saw
    # the same silence for "you are up to date" and for "the update server was
    # unreachable / the manifest signature was invalid".
    if not report.update_info.check_succeeded:
        print("UPDATE CHECK FAILED (result UNKNOWN — NOT a green light): no trusted update manifest was obtained.")
        print("  Treat 'no update' as UNVERIFIED until a signed manifest is reachable.")
    elif report.update_info.has_update:
        print(f"UPDATE AVAILABLE: v{report.update_info.latest_version} (Current: v{report.update_info.current_version})")
        if report.update_info.release_notes:
            print(f"  Release Notes: {report.update_info.release_notes}")

    if args.check_only:
        return 0 if report.can_launch else 1

    # L-C-001: --launch never bypasses the preflight gate. A failed
    # preflight always blocks, even with the dev override (the override
    # only gates non-launch diagnostics, never execution).
    if report.can_launch:
        return launcher.launch_main_app(extra_args=unknown)

    if args.launch and _dev_override_enabled():
        print("WARNING: --launch dev override ignored — preflight FAILED, launch blocked.")
    if not report.auth_status.has_valid_license:
        # L-1: point the operator at activation when the license gate closed.
        print(f"License gate CLOSED (tier={report.auth_status.tier}): no valid license for this device.")
        print("Activate this device with a license key to enable launch:")
        print("    ucanlab_launcher --activate <LICENSE-KEY> [--device-name <NAME>]")
    print("Preflight FAILED: launch aborted. Use --check-only for diagnostics.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

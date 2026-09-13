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

# Ensure project root is in sys.path when invoked directly as python src/launcher/app.py
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.logging import get_logger
from src.launcher.auth import AuthStatus, LauncherAuthManager
from src.launcher.prereqs import PrereqChecker, PrereqStatus
from src.launcher.updater import UpdateInfo, UpdateManager

logger = get_logger("launcher.app")

DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64 = "eX3vJQWpo/pKrkpi5Y+f7m5ooUCRbCyY201DTnAjz/Q="


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

    def __init__(self, current_version: str = "13.0.0") -> None:
        self.version = current_version
        # M-25 (P2-14): stable project root for the mandatory-update
        # obligation record.
        self.root_dir = Path(__file__).resolve().parent.parent.parent
        self.auth_manager = LauncherAuthManager()
        try:
            pub_bytes = base64.b64decode(DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64)
            pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        except Exception:
            pub_key = None
        self.update_manager = UpdateManager(
            current_version=self.version,
            cloud_client=self.auth_manager.client,
            public_key=pub_key,
            require_signature=True,
        )

    @classmethod
    def resolve_target_executable(cls) -> Path:
        """Find the core application executable or main.py entry point."""
        root = Path(__file__).resolve().parent.parent.parent

        # 1. Check for Nuitka / PyInstaller compiled standalone executable
        candidate_exes = [
            root / "dist" / "ucanlab.exe",
            root / "dist" / "main.dist" / "ucanlab.exe",
            root / "dist" / "Universal-CAN-Tool.exe",
            root / "dist" / "Universal_CAN_Diagnostic.exe",
            root / "dist" / "main.dist" / "Universal-CAN-Tool.exe",
            root / "dist" / "main.dist" / "Universal_CAN_Diagnostic.exe",
        ]
        for candidate in candidate_exes:
            if candidate.is_file():
                return candidate

        # 2. Fallback to raw Python main.py
        return root / "src" / "main.py"

    def run_preflight(self, custom_update_manifest: dict[str, Any] | None = None) -> LauncherPreflightReport:
        """Execute all environment, auth, and update checks."""
        prereqs = PrereqChecker.run_all_checks()
        has_critical_failures = any(not p.is_available and p.is_critical for p in prereqs)

        auth_status = self.auth_manager.get_current_status()
        if custom_update_manifest is not None:
            # Test-only path: unsigned manifest, never clears a sealed
            # obligation in production (main() always passes None).
            update_info = self.update_manager._check_for_updates_unverified(custom_manifest=custom_update_manifest)
        else:
            update_info = self.update_manager.check_for_updates()
        target_exe = self.resolve_target_executable()

        has_blocking_mandatory_update = update_info.has_update and update_info.mandatory
        # M-25 (P2-14): a FAILED update check is not a green light. If the
        # launcher has a recorded mandatory-update obligation (persisted from
        # a previous successful check), an unreachable update server must
        # not clear it — that is exactly the offline-attack window the gate
        # exists for. Without a recorded obligation the failure stays
        # non-blocking (a fresh install behind a flaky network must still
        # start), but the report carries check_succeeded=False so the UI can
        # surface the degraded state.
        if not update_info.check_succeeded:
            recorded = self._load_recorded_mandatory_obligation()
            if recorded is not None:
                has_blocking_mandatory_update = True
                logger.error(
                    "Update check failed while a mandatory update obligation is on record — launch blocked",
                    extra={"recorded_min_version": recorded},
                )
        can_launch = not has_critical_failures and target_exe.exists() and not has_blocking_mandatory_update

        if update_info.check_succeeded and update_info.has_update and update_info.mandatory:
            self._record_mandatory_obligation(update_info.latest_version)
        elif update_info.check_succeeded and update_info.has_update and not update_info.mandatory:
            # Obligation cleared only by a SUCCESSFUL check that says so.
            self._clear_mandatory_obligation()

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

    # Sealed mandatory-update obligation (<ver>.<hmac>).
    _OBLIGATION_HMAC_KEY_NAME = "LAUNCHER_OBLIGATION_HMAC"
    _ALLOWED_EXTRA_ARGS = frozenset({"--channel", "--interface", "--bitrate", "--cli", "--help"})

    def _obligation_path(self) -> Path:
        return self.root_dir / "dist" / "launcher_mandatory_update.txt"

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
        try:
            import hashlib as _hashlib
            import hmac as _hmac

            path = self._obligation_path()
            if not path.exists():
                return None
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                return None
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
        except OSError:
            return None

    def _record_mandatory_obligation(self, min_version: str) -> None:
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
                    return
            ver = str(min_version).strip()
            mac = _hmac.new(key, ver.encode("utf-8"), _hashlib.sha256).hexdigest()
            path = self._obligation_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{ver}.{mac}", encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to record mandatory update obligation", extra={"error": str(exc)})

    def _clear_mandatory_obligation(self) -> None:
        try:
            self._obligation_path().unlink(missing_ok=True)
        except OSError:
            pass

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

    def launch_main_app(self, extra_args: list[str] | None = None) -> int:
        """Spawn the core application executable with integrity checks."""
        target = self.resolve_target_executable().resolve()
        args = self._filter_extra_args(extra_args or [])

        if not target.is_file():
            logger.error("Target executable not found or not a valid file", extra={"target": str(target)})
            return 1

        if target.suffix == ".py":
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
        return subprocess.call(cmd, shell=False, timeout=300)


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal CAN Platform - Launcher & Auto-Updater")
    parser.add_argument("--check-only", action="store_true", help="Run preflight checks and exit")
    parser.add_argument("--activate", type=str, help="Activate cloud license key")
    parser.add_argument("--device-name", type=str, default="Desktop Diagnostic Tool", help="Device name for registration")
    parser.add_argument("--launch", action="store_true", help="Launch the main application immediately")

    args, unknown = parser.parse_known_args()
    launcher = UniversalCanLauncher()

    if args.activate:
        # L-H-004: never echo the license key itself to the console/logs
        print("Activating license key...")
        try:
            claims = launcher.auth_manager.activate_with_key(args.activate, device_name=args.device_name)
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

    if report.update_info.has_update:
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
    print("Preflight FAILED: launch aborted. Use --check-only for diagnostics.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

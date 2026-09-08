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
    """True only in a development/test environment (L-C-001).

    Production builds must never honor --launch past a failed preflight;
    the override requires an explicit opt-in environment variable.
    """
    import os

    return bool(os.environ.get("UCAN_LAUNCHER_DEV_OVERRIDE") or os.environ.get("PYTEST_CURRENT_TEST"))


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
        update_info = self.update_manager.check_for_updates(custom_manifest=custom_update_manifest)
        target_exe = self.resolve_target_executable()

        has_blocking_mandatory_update = update_info.has_update and update_info.mandatory
        can_launch = not has_critical_failures and target_exe.exists() and not has_blocking_mandatory_update

        return LauncherPreflightReport(
            can_launch=can_launch,
            prereqs=prereqs,
            auth_status=auth_status,
            update_info=update_info,
            target_executable=target_exe,
        )

    def launch_main_app(self, extra_args: list[str] | None = None) -> int:
        """Spawn the core application executable with integrity checks."""
        target = self.resolve_target_executable().resolve()
        args = extra_args or []

        if not target.is_file():
            logger.error("Target executable not found or not a valid file", extra={"target": str(target)})
            return 1

        if target.suffix == ".py":
            cmd = [sys.executable, str(target)] + args
        elif target.suffix.lower() == ".exe":
            cmd = [str(target)] + args
        else:
            logger.error("Refusing to execute unverified binary extension", extra={"target": str(target)})
            return 1

        logger.info("Launching Universal CAN Platform", extra={"target": str(target), "args": args})
        return subprocess.call(cmd)


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

    # L-C-001: --launch must never bypass preflight DRM/auth gating in
    # production builds. It stays a developer convenience only.
    if report.can_launch:
        return launcher.launch_main_app(extra_args=unknown)

    if args.launch and _dev_override_enabled():
        print("WARNING: --launch dev override active — bypassing preflight gate.")
        return launcher.launch_main_app(extra_args=unknown)

    print("Preflight FAILED: launch aborted. Use --check-only for diagnostics.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

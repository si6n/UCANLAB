"""Pre-flight prerequisites and hardware driver checker for Universal CAN Launcher.

Inspects Windows environment for Edge WebView2, Visual C++ Redistributable,
and CAN interface hardware drivers (PCAN, Kvaser, RP1210, Vector).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class PrereqStatus:
    """Status result of an environment prerequisite check."""

    name: str
    is_available: bool
    details: str
    download_url: str | None = None
    is_critical: bool = True


class PrereqChecker:
    """Automated environment and driver diagnostic engine."""

    WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
    VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    PEAK_CAN_URL = "https://www.peak-system.com/quick/DrvSetup"

    @classmethod
    def check_webview2(cls) -> PrereqStatus:
        """Verify Microsoft Edge WebView2 runtime availability."""
        if sys.platform != "win32":
            return PrereqStatus("Microsoft Edge WebView2", True, "Non-Windows OS (mock/native fallback)", is_critical=False)

        try:
            import winreg

            subkeys = [
                r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
            ]
            for hkey in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in subkeys:
                    try:
                        with winreg.OpenKey(hkey, sub) as key:
                            val, _ = winreg.QueryValueEx(key, "pv")
                            if val:
                                return PrereqStatus("Microsoft Edge WebView2", True, f"Installed (Version {val})", is_critical=True)
                    except OSError:
                        continue
        except Exception:
            pass

        return PrereqStatus(
            "Microsoft Edge WebView2",
            False,
            "WebView2 Runtime not detected. Required for modern GUI rendering.",
            download_url=cls.WEBVIEW2_URL,
            is_critical=True,
        )

    @classmethod
    def check_vcredist(cls) -> PrereqStatus:
        """Verify Microsoft Visual C++ 2015-2022 Redistributable availability."""
        if sys.platform != "win32":
            return PrereqStatus("Visual C++ Redistributable", True, "Non-Windows OS", is_critical=False)

        try:
            import winreg

            key_path = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                installed, _ = winreg.QueryValueEx(key, "Installed")
                val, _ = winreg.QueryValueEx(key, "Version")
                if installed == 1:
                    return PrereqStatus("Visual C++ Redistributable", True, f"Installed (Version {val})", is_critical=True)
        except Exception:
            pass

        return PrereqStatus(
            "Visual C++ Redistributable",
            False,
            "Visual C++ x64 runtime missing. Required for C++ native modules.",
            download_url=cls.VCREDIST_URL,
            is_critical=True,
        )

    @classmethod
    def check_can_drivers(cls) -> list[PrereqStatus]:
        """Detect installed CAN interface hardware driver DLLs."""
        drivers: list[PrereqStatus] = []
        if sys.platform != "win32":
            return [PrereqStatus("CAN Hardware Drivers", True, "SocketCAN / Virtual CAN ready", is_critical=False)]

        sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        syswow = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SysWOW64"

        # 1. PEAK PCAN-Basic
        has_pcan = (sys32 / "PCANBasic.dll").exists() or (syswow / "PCANBasic.dll").exists()
        drivers.append(
            PrereqStatus(
                "PEAK-System PCAN-Basic",
                has_pcan,
                "PCANBasic.dll available" if has_pcan else "PCAN-USB drivers not installed (Optional)",
                download_url=cls.PEAK_CAN_URL if not has_pcan else None,
                is_critical=False,
            )
        )

        # 2. Kvaser CANlib
        has_kvaser = (sys32 / "canlib32.dll").exists() or (syswow / "canlib32.dll").exists()
        drivers.append(
            PrereqStatus(
                "Kvaser CANlib",
                has_kvaser,
                "canlib32.dll available" if has_kvaser else "Kvaser drivers not installed (Optional)",
                is_critical=False,
            )
        )

        # 3. RP1210 Adapters (Nexiq USB-Link / Noregon DLA) - 32-bit and 64-bit DLLs
        has_rp1210 = (
            (sys32 / "rp121032.dll").exists()
            or (syswow / "rp121032.dll").exists()
            or (sys32 / "RP121064.dll").exists()
            or (sys32 / "rp121064.dll").exists()
        )
        drivers.append(
            PrereqStatus(
                "TMC RP1210 Diagnostic Adapter",
                has_rp1210,
                "RP1210 driver DLL available" if has_rp1210 else "RP1210 driver not detected (Optional for heavy-duty)",
                is_critical=False,
            )
        )

        return drivers

    @classmethod
    def run_all_checks(cls) -> list[PrereqStatus]:
        """Run all pre-flight prerequisite checks."""
        results: list[PrereqStatus] = [
            cls.check_webview2(),
            cls.check_vcredist(),
        ]
        results.extend(cls.check_can_drivers())
        return results

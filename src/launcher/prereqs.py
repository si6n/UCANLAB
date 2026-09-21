"""Pre-flight prerequisites and hardware driver checker for Universal CAN Launcher.

Inspects Windows environment for Edge WebView2 (GUI mode only),
Visual C++ Redistributable, and CAN interface hardware drivers (PCAN, Kvaser,
RP1210). Vector support is provided by ``python-can``'s ``vector`` backend at
runtime and is deliberately NOT probed here (L-1: the previous docstring named
Vector although no check existed) — that backend stays optional and
hardware-untested, and ``python-can`` reports its absence at connect time.
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
    #: Runtime DLLs that prove the MSVC x64 C runtime is present. Checked as
    #: files (System32/SysWOW64) rather than by a single registry key — the
    #: redistributable can be side-loaded by another product, installed by an
    #: installer that omits the VS "Runtimes" key, or live in WinSxS.
    VCRUNTIME_DLLS = ("msvcp140.dll", "vcruntime140.dll")

    # ------------------------------------------------------------------
    # M-1 (verify/launcher.md:62,74,88): WebView2 is only needed for the
    # pywebview GUI. In `--cli` (headless sniffer/analyzer) mode, a missing
    # WebView2 runtime used to fold into `has_critical_failures` and refuse to
    # run at all — for the one mode that loads no browser engine.
    # ------------------------------------------------------------------
    @classmethod
    def is_cli_mode(cls, argv: list[str] | None = None) -> bool:
        """True when the operator asked for the headless CLI front-end."""
        tokens = sys.argv[1:] if argv is None else list(argv)
        if os.environ.get("UCANLAB_CLI_MODE", "").strip() == "1":
            return True
        return any(str(tok).split("=", 1)[0] in ("--cli", "-c") for tok in tokens)

    @classmethod
    def check_webview2(cls, *, cli_mode: bool = False) -> PrereqStatus:
        """Verify Microsoft Edge WebView2 runtime availability.

        Non-critical in CLI mode (M-1): the headless front-end never loads a
        browser engine, so its absence must not block the launch.
        """
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
                                return PrereqStatus(
                                    "Microsoft Edge WebView2",
                                    True,
                                    f"Installed (Version {val})",
                                    is_critical=not cli_mode,
                                )
                    except OSError:
                        continue
        except Exception:
            pass

        if cli_mode:
            return PrereqStatus(
                "Microsoft Edge WebView2",
                False,
                "WebView2 Runtime not detected (not required in --cli mode).",
                download_url=cls.WEBVIEW2_URL,
                is_critical=False,
            )
        return PrereqStatus(
            "Microsoft Edge WebView2",
            False,
            "WebView2 Runtime not detected. Required for modern GUI rendering.",
            download_url=cls.WEBVIEW2_URL,
            is_critical=True,
        )

    @classmethod
    def _vcredist_evidence(cls) -> str | None:
        """Return evidence that the MSVC x64 runtime is installed, else None.

        Three independent probes, any one sufficient (M-1):

        1. the canonical x64 Runtimes registry key;
        2. ``System32``/``SysWOW64`` runtime DLLs;
        3. the WinSxS side-by-side component store.
        """
        try:
            import winreg

            key_path = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                installed, _ = winreg.QueryValueEx(key, "Installed")
                val, _ = winreg.QueryValueEx(key, "Version")
                if installed == 1:
                    return f"Installed (Version {val})"
        except Exception:
            pass

        root = cls._system_root()
        for directory in (root / "System32", root / "SysWOW64"):
            for dll in cls.VCRUNTIME_DLLS:
                if (directory / dll).is_file():
                    return f"Runtime DLL present ({directory.name}\\{dll})"

        try:
            import winreg

            winners = r"SOFTWARE\Microsoft\Windows\CurrentVersion\SideBySide\Winners"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, winners):
                return "WinSxS side-by-side component store present"
        except Exception:
            return None

    @classmethod
    def check_vcredist(cls) -> PrereqStatus:
        """Verify Microsoft Visual C++ 2015-2022 Redistributable availability."""
        if sys.platform != "win32":
            return PrereqStatus("Visual C++ Redistributable", True, "Non-Windows OS", is_critical=False)

        evidence = cls._vcredist_evidence()
        if evidence is not None:
            return PrereqStatus("Visual C++ Redistributable", True, evidence, is_critical=True)

        return PrereqStatus(
            "Visual C++ Redistributable",
            False,
            "Visual C++ x64 runtime missing. Required for C++ native modules.",
            download_url=cls.VCREDIST_URL,
            is_critical=True,
        )

    @staticmethod
    def _system_root() -> Path:
        """Resolve the Windows system root via GetSystemDirectoryW (collector.py model)."""
        if sys.platform != "win32":
            return Path(r"C:\Windows")
        try:
            import ctypes as _ctypes

            _buf = _ctypes.create_unicode_buffer(260)
            _get = _ctypes.windll.kernel32.GetSystemDirectoryW
            if _get(_buf, len(_buf)):
                _sysdir = Path(_buf.value).resolve()
                # GetSystemDirectoryW returns <root>\System32 -> parent is the root.
                _root = _sysdir.parent
                if _root.is_dir():
                    return _root
        except Exception:
            pass
        return Path(r"C:\Windows")

    @classmethod
    def check_can_drivers(cls) -> list[PrereqStatus]:
        """Detect installed CAN interface hardware driver DLLs."""
        drivers: list[PrereqStatus] = []
        if sys.platform != "win32":
            return [PrereqStatus("CAN Hardware Drivers", True, "SocketCAN / Virtual CAN ready", is_critical=False)]

        _root = cls._system_root()
        sys32 = _root / "System32"
        syswow = _root / "SysWOW64"

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
    def run_all_checks(cls, *, cli_mode: bool | None = None) -> list[PrereqStatus]:
        """Run all pre-flight prerequisite checks.

        ``cli_mode`` defaults to auto-detection from ``sys.argv`` (M-1) so the
        existing zero-argument call sites stay correct.
        """
        if cli_mode is None:
            cli_mode = cls.is_cli_mode()
        results: list[PrereqStatus] = [
            cls.check_webview2(cli_mode=cli_mode),
            cls.check_vcredist(),
        ]
        results.extend(cls.check_can_drivers())
        return results

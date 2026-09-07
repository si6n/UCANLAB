"""Automated One-Click Standalone Windows .EXE Builder for Universal CAN-Bus Diagnostic v13.0."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _run_npm_build(frontend_dir: Path) -> int:
    npm = shutil.which("npm")
    if npm is None:
        print("[HATA] npm bulunamadi. Node.js kurulu mu?")
        return 1
    return subprocess.call([npm, "run", "build"], cwd=str(frontend_dir))


def _installed_can_backends() -> list[str]:
    """Enumerate every python-can interface backend installed in this env.

    N-05 R3: `can.Bus(interface=...)` imports backends dynamically via
    importlib, which PyInstaller cannot see — bundle ALL of them.
    """
    try:
        import pkgutil

        import can.interfaces

        return [m.name for m in pkgutil.iter_modules(can.interfaces.__path__)]
    except Exception as exc:  # noqa: BLE001 — build must not die on enumeration
        print(f"[UYARI] can.interfaces listelenemedi ({exc}); bilinen arka uclar eklenecek.")
        return [
            "virtual",
            "pcan",
            "kvaser",
            "vector",
            "socketcan",
            "socketcand",
            "serial",
            "slcan",
            "gs_usb",
            "udp_multicast",
        ]


def build_exe() -> int:
    root_dir = Path(__file__).parent.parent.resolve()
    frontend_dir = root_dir / "src" / "ui" / "frontend"
    frontend_dist = frontend_dir / "dist"

    print("==================================================")
    print(">>> Universal CAN-Bus Platform v13.0 - EXE Builder")
    print("==================================================")

    # 1. Check & Build Frontend if needed
    if not frontend_dist.exists() or not (frontend_dist / "index.html").exists():
        print("[1/2] Frontend React+Tailwind paketi derleniyor...")
        ret = _run_npm_build(frontend_dir)
        if ret != 0:
            print("[HATA] Frontend derleme hatasi!")
            return ret
    else:
        print("[1/2] Frontend React+Tailwind paketi hazir.")

    # 2. Package into Ultra-Fast & Compact Standalone .EXE using PyInstaller
    print("[2/2] Tek parca Windows .EXE derleniyor...")
    entry_point = root_dir / "src" / "main.py"
    data_arg = f"{frontend_dist};src/ui/frontend/dist"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=Universal_CAN_Diagnostic",
        "--onefile",
        "--noconsole",
        "--clean",
        f"--add-data={data_arg}",
        # N-05/F-36: guarantee safety & security modules are bundled — the
        # runtime import graph is analysed from src/, root added to search path
        f"--paths={root_dir}",
        "--hidden-import=src.safety.gateway",
        "--hidden-import=src.safety.estop",
        "--hidden-import=src.safety.watchdog",
        "--hidden-import=src.safety.state_machine",
        "--hidden-import=src.safety.secret_provider",
        "--hidden-import=src.safety.multiplexer",
        "--hidden-import=src.safety.reset_authority",
        "--hidden-import=src.safety.e2e.packager",
        "--hidden-import=src.safety.e2e.validator",
        "--hidden-import=src.security.license.validator",
        "--hidden-import=src.security.anti_tamper.guard",
        "--hidden-import=src.security.cloud.client",
        "--hidden-import=src.security.cloud.license_flow",
        "--hidden-import=src.security.cloud.telemetry_uploader",
        "--hidden-import=src.security.hwid.collector",
        # N-05 R2: protocol engines not reachable from the runtime import
        # graph (flasher is UI/CLI-driven, OEM decoders are registry-lazy) —
        # explicitly bundled so the release exe ships the full feature set
        # (UDS flashing, J1939 OEM decoders, OBD-II poller, replay, exporters,
        # signal discovery, virtual channels, win32 power).
        "--hidden-import=src.protocols.uds.flasher",
        "--hidden-import=src.protocols.uds.did_database",
        "--hidden-import=src.protocols.uds.client",
        "--hidden-import=src.protocols.uds.isotp",
        "--hidden-import=src.protocols.uds.services",
        "--hidden-import=src.protocols.uds.nrc",
        "--hidden-import=src.protocols.obd.poller",
        "--hidden-import=src.protocols.obd.pids",
        "--hidden-import=src.protocols.obd.models",
        "--hidden-import=src.protocols.j1939.transport",
        "--hidden-import=src.protocols.j1939.diagnostics",
        "--hidden-import=src.protocols.j1939.address_claim",
        "--hidden-import=src.protocols.j1939.oem.registry",
        "--hidden-import=src.protocols.j1939.oem.cummins",
        "--hidden-import=src.protocols.j1939.oem.caterpillar",
        "--hidden-import=src.protocols.j1939.oem.scania",
        "--hidden-import=src.protocols.j1939.oem.volvo",
        "--hidden-import=src.protocols.j1939.oem.detroit",
        "--hidden-import=src.protocols.j1939.oem.actros",
        "--hidden-import=src.protocols.nmea2000.fast_packet",
        "--hidden-import=src.protocols.volvo.volvo_decoder",
        "--hidden-import=src.hal.replay.parsers",
        "--hidden-import=src.hal.replay.player",
        "--hidden-import=src.hal.replay.safety_filter",
        "--hidden-import=src.hal.power.win32_power",
        "--hidden-import=src.hal.virtual",
        "--hidden-import=src.hal.tx_port",
        "--hidden-import=src.hal.can_interface",
        "--hidden-import=src.engine.discovery.engine",
        "--hidden-import=src.engine.discovery.bitstats",
        "--hidden-import=src.engine.discovery.segmenter",
        "--hidden-import=src.engine.discovery.hypotheses",
        "--hidden-import=src.engine.discovery.dbc_builder",
        "--hidden-import=src.engine.discovery.evidence",
        "--hidden-import=src.engine.discovery.detectors.checksum",
        "--hidden-import=src.engine.discovery.detectors.counter",
        "--hidden-import=src.engine.exporters.kml_exporter",
        "--hidden-import=src.engine.exporters.mat_exporter",
        "--hidden-import=src.engine.exporters.mdf4_exporter",
        "--hidden-import=src.engine.exporters.pdf_report",
        "--hidden-import=src.engine.virtual_channels.channel_engine",
        # Exclude legacy unused heavy GUI packages
        "--exclude-module=PySide6",
        "--exclude-module=pyqtgraph",
        "--exclude-module=shiboken6",
        "--exclude-module=tkinter",
        "--exclude-module=matplotlib",
        # Explicit hidden imports for webview and automotive CAN engine
        "--hidden-import=webview",
        "--hidden-import=clr_loader",
        "--hidden-import=pythonnet",
        "--hidden-import=bottle",
        "--hidden-import=proxy_tools",
        "--hidden-import=can",
        "--hidden-import=cantools",
        "--hidden-import=asammdf",
        f"--distpath={root_dir / 'dist'}",
        f"--workpath={root_dir / 'build'}",
        str(entry_point),
    ]

    # python-can backends are resolved at RUNTIME via importlib
    # (can.interface._get_class_for_interface) — PyInstaller's static
    # analysis never sees them, so every installed interface backend must
    # be explicitly bundled or `can.Bus(interface=...)` fails inside the
    # frozen exe with CanInterfaceNotImplementedError (N-05 R3).
    backend_flags = [f"--hidden-import=can.interfaces.{b}" for b in sorted(_installed_can_backends())]
    cmd = cmd[:-3] + backend_flags + cmd[-3:]  # keep distpath/workpath/entry_point last

    print("Komut calistiriliyor...")
    ret = subprocess.call(cmd, cwd=str(root_dir))
    if ret == 0:
        exe_path = root_dir / "dist" / "Universal_CAN_Diagnostic.exe"
        print("==================================================")
        print("TEBRIKLER! .EXE dosyaniz basariyla olusturuldu:")
        print(f"Dosya Konumu: {exe_path}")
        print("==================================================")
    return ret


if __name__ == "__main__":
    sys.exit(build_exe())

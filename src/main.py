"""Universal CAN-Bus Diagnostic & Telemetry Platform - Main Application Launcher.

Provides modern Native Desktop GUI (React + Tailwind + Edge WebView2), CLI execution,
AI Copilot reasoning, and Official Report Center.
Matches Universal CAN-Bus Diagnostic v13.0 Design Specification.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path when invoked directly as python src/main.py
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core.errors import SecurityError
from src.core.logging import get_logger, setup_logging
from src.hal.base import AbstractBus
from src.hal.drivers.pcan_kvaser import PythonCanBus
from src.ui.desktop_app import UniversalCanDesktopApp

logger = get_logger("app.main")


def build_bus(interface: str, channel: str, bitrate: int, listen_only: bool = True) -> AbstractBus:
    """Single bus factory for every launch path (K4-a).

    rp1210 uses the RP1210Bus adapter over the vendor client (device id from
    --channel, e.g. "1"); all other interfaces go through python-can.

    Safe-by-default (CONTRIBUTING.md): every production wiring path opens
    the bus listen-only unless the caller explicitly opts into active TX
    (CLI --tx flag). Protocol engines that need to transmit reconnect
    through this factory with listen_only=False after the operator arms TX.
    """
    if interface == "rp1210":
        from src.hal.rp1210.bus import RP1210Bus

        try:
            device_id = int(channel)
        except ValueError as exc:
            raise ValueError(
                f"rp1210 interface requires a numeric device id, got {channel!r}"
            ) from exc
        # P0-3 (REVIEW C-4): forward listen_only — the old call dropped the
        # flag entirely, so every RP1210 adapter opened as an ACTIVE
        # transceiver (ACK-producing) even when every layer above believed it
        # was passive. RP1210Bus now honors the flag by hard-blocking send()
        # in listen-only mode (fail-closed).
        return RP1210Bus(device_id=device_id, bitrate=bitrate, listen_only=listen_only)
    return PythonCanBus(interface=interface, channel=channel, bitrate=bitrate, listen_only=listen_only)

# Global placeholder for Qt testing hooks
QApplication: Any = None


class UniversalCanMainWindow:
    """MainWindow wrapper supporting both Qt and Desktop webview lifecycle."""

    def __init__(self, bus: Any | None = None, channel: str = "vcan0", bitrate: int = 250000) -> None:
        # F-30: single composition root — reuse the injected bus or let the
        # desktop app own exactly one bus; never create a second instance.
        self.bus = bus
        self._desktop_app = UniversalCanDesktopApp(
            channel=channel, bitrate=bitrate, bus=bus
        )

    def show(self) -> None:
        pass

    def close(self) -> None:
        if self.bus and hasattr(self.bus, "disconnect"):
            self.bus.disconnect()

    def run(self) -> None:
        self._desktop_app.run()


def _anti_tamper_gate() -> bool:
    """G-1t: run the AntiTamperGuard probes as a hard launch gate.

    ``AntiTamperGuard.enforce()`` had no production caller — the debugger /
    timing probes were dead code in shipped builds. The entry point now
    consults it before wiring any hardware or starting the UI: a violation
    (or a fail-closed probe error, e.g. an unreadable Win32 API) aborts the
    launch with a non-zero exit. ``on_violation`` captures the reason so the
    function returns a tri-state instead of raising.
    """
    from src.security.anti_tamper.guard import AntiTamperGuard

    violation: list[str] = []
    try:
        AntiTamperGuard.enforce(on_violation=violation.append)
    except SecurityError as exc:
        logger.critical("Anti-tamper gate failed closed; aborting launch", extra={"error": str(exc)})
        return False
    if violation:
        logger.critical("Anti-tamper violation detected; aborting launch", extra={"reason": violation[0]})
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Universal CAN-Bus Diagnostic & Telemetry Tool")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode instead of GUI")
    parser.add_argument("--channel", type=str, default="vcan0", help="CAN Channel (e.g. PCAN_USBBUS1, 0, vcan0)")
    parser.add_argument(
        "--interface",
        type=str,
        default="virtual",
        help="Hardware driver (virtual, pcan, kvaser, vector, rp1210)",
    )
    parser.add_argument("--bitrate", type=int, default=250000, help="CAN Bitrate (e.g. 250000, 500000)")
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    log_level_val = getattr(logging, args.log_level.upper(), logging.INFO)
    setup_logging(level=log_level_val)

    logger.info(
        "Starting Universal CAN Platform v13.0",
        extra={"interface": args.interface, "channel": args.channel, "bitrate": args.bitrate},
    )

    if not _anti_tamper_gate():
        print("Anti-tamper check failed: launch aborted.")
        return 1

    if args.cli:
        # H-3 (P1-2): the --tx flag was removed. It opened the physical bus
        # as an ACTIVE transceiver (ACK-producing) with no TxSafetyGateway,
        # no whitelist, and no supervisor anywhere in the CLI path — an
        # operator could affect a live vehicle bus by merely sniffing with
        # the wrong flag. The CLI is a passive monitor: listen-only, always.
        # Interactive TX belongs to the desktop app, whose every outbound
        # frame passes the single audited gateway choke-point.
        print("=== Universal CAN-Bus CLI Mode (Listen-Only) ===")
        bus = build_bus(interface=args.interface, channel=args.channel, bitrate=args.bitrate, listen_only=True)
        bus.connect()
        print(f"Connected to {args.interface}:{args.channel} @ {args.bitrate} bps. Listening for frames...")
        # REVIEW LOW-2 / REVIEW2 #3 / REVIEW3 #3: a transient HardwareError
        # (USB unplug, bus-off, vendor driver hiccup) used to escape the loop
        # and kill the CLI with a raw traceback. Field sniffing needs a
        # bounded retry/backoff profile: reconnect with capped attempts, log
        # each failure, exit cleanly only when the bus is truly gone.
        retry_delay_s = 0.25
        max_retry_delay_s = 1.0
        consecutive_failures = 0
        max_consecutive_failures = 20
        try:
            while True:
                try:
                    frame = bus.recv(timeout_s=1.0)
                except KeyboardInterrupt:
                    raise
                except Exception as recv_exc:  # noqa: BLE001 — transient bus errors must not kill the monitor
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        logger.error(
                            "CLI bus receive failed repeatedly; giving up",
                            extra={"failures": consecutive_failures, "error": str(recv_exc)},
                        )
                        print(f"Bağlantı kalıcı olarak kesildi: {recv_exc}")
                        return 1
                    logger.warning(
                        "CLI bus receive failed; retrying with backoff",
                        extra={
                            "failure": consecutive_failures,
                            "backoff_s": retry_delay_s,
                            "error": str(recv_exc),
                        },
                    )
                    time.sleep(retry_delay_s)
                    retry_delay_s = min(retry_delay_s * 2.0, max_retry_delay_s)
                    continue
                consecutive_failures = 0
                retry_delay_s = 0.25
                if frame:
                    print(
                        f"[{frame.timestamp_ns / 1e9:.6f}] ID: 0x{frame.arbitration_id:08X} DLC: {frame.dlc} Data: {' '.join(f'{b:02X}' for b in frame.data)}",
                        flush=True,
                    )
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            try:
                bus.disconnect()
            except Exception as exc:  # noqa: BLE001 — teardown must not mask the loop exit
                logger.warning("CLI bus disconnect failed", extra={"error": str(exc)})
        return 0

    # Launch GUI
    # If QApplication is mocked/present
    qapp_cls = getattr(sys.modules.get("src.main", sys.modules[__name__]), "QApplication", None)
    if qapp_cls is not None:
        qapp = qapp_cls(sys.argv)
        bus = build_bus(interface=args.interface, channel=args.channel, bitrate=args.bitrate)
        window = UniversalCanMainWindow(bus=bus, channel=args.channel, bitrate=args.bitrate)
        window.show()
        try:
            ret = qapp.exec()
            return int(ret) if ret is not None else 0
        finally:
            bus.disconnect()

    # Launch Modern Native Desktop GUI (WebView2 + React + Tailwind)
    app = UniversalCanDesktopApp(channel=args.channel, bitrate=args.bitrate, interface=args.interface)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

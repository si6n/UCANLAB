"""Adapter discovery for the mechanic connection wizard (Aşama 5).

Finds what a mechanic can plug in without opening any channel for traffic and
without sending anything:

* **PCAN** — loads ``PCANBasic`` and asks for attached channels. The DLL
  missing means "driver not installed", which is a different fix from
  "adapter not plugged in".
* **Kvaser** — python-can loads ``canlib32`` at import; ``None`` means the
  driver is missing. Kvaser's always-present *virtual* channels are hidden.
* **RP1210** (heavy-duty VCIs) — vendors listed in the RP1210 INI
  ``[VendorDIL]`` sections. RP1210 cannot tell whether the device is plugged
  in until a client connects, so these report ``driver_ready``.
* **SocketCAN** (Linux, development) — ``can0`` / ``vcan0`` style interfaces.
* **Simulator** — always available, needs no hardware.

Every probe is isolated: a crashing vendor DLL yields a "could not check"
entry for that brand only, never an exception to the UI.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from src.core.logging import get_logger

logger = get_logger("engine.connection.adapters")

# Status values, ordered from "use it" to "cannot use it".
READY = "ready"  # device seen and usable
DRIVER_READY = "driver_ready"  # driver installed; presence known only after connecting (RP1210)
DRIVER_MISSING = "driver_missing"  # a vendor library is absent
NOT_FOUND = "not_found"  # driver present, no device attached
PROBE_FAILED = "probe_failed"  # the vendor library misbehaved

_DRIVER_HELP = {
    "pcan": (
        "PCAN adaptörü için sürücü kurulu değil. peak-system.com adresinden 'PEAK-System Driver' paketini "
        "kurun, sonra adaptörü çıkarıp tekrar takın.",
        "The PCAN driver is not installed. Install the 'PEAK-System Driver' package from peak-system.com, "
        "then unplug and replug the adapter.",
    ),
    "kvaser": (
        "Kvaser adaptörü için sürücü kurulu değil. kvaser.com adresinden 'Kvaser Drivers for Windows' "
        "paketini kurun, sonra adaptörü çıkarıp tekrar takın.",
        "The Kvaser driver is not installed. Install 'Kvaser Drivers for Windows' from kvaser.com, then "
        "unplug and replug the adapter.",
    ),
}


@dataclass(frozen=True)
class AdapterInfo:
    """One thing the mechanic could connect through."""

    kind: str  # "pcan" | "kvaser" | "rp1210" | "socketcan" | "simulator"
    interface: str  # python-can / build_bus interface name
    channel: str
    label: str
    status: str
    message_tr: str = ""
    message_en: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.status in (READY, DRIVER_READY)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": f"{self.kind}:{self.channel}", "kind": self.kind, "interface": self.interface,
            "channel": self.channel, "label": self.label, "status": self.status, "usable": self.usable,
            "message_tr": self.message_tr, "message_en": self.message_en,
        }


SIMULATOR = AdapterInfo(
    kind="simulator", interface="simulator", channel="sim0", label="Simülatör", status=READY,
    message_tr="Gerçek araç ve adaptör olmadan deneme için.",
    message_en="For practice without a real vehicle or adapter.",
)


def _driver_missing(kind: str, label: str) -> AdapterInfo:
    tr, en = _DRIVER_HELP[kind]
    return AdapterInfo(kind=kind, interface=kind, channel="", label=label, status=DRIVER_MISSING,
                       message_tr=tr, message_en=en)


def probe_pcan() -> list[AdapterInfo]:
    try:
        from can.interfaces.pcan.basic import PCANBasic
    except Exception:  # noqa: BLE001 — python-can build without PCAN support
        return []
    try:
        PCANBasic()  # type: ignore[no-untyped-call]
    except OSError:
        return [_driver_missing("pcan", "PCAN-USB")] if sys.platform == "win32" else []
    from can.interfaces.pcan.pcan import PcanBus

    found = []
    for cfg in PcanBus._detect_available_configs():  # type: ignore[no-untyped-call]
        name = str(cfg.get("device_name") or "PCAN-USB")
        found.append(AdapterInfo(kind="pcan", interface="pcan", channel=str(cfg["channel"]), label=name,
                                 status=READY, details={"supports_fd": bool(cfg.get("supports_fd"))}))
    return found


def probe_kvaser() -> list[AdapterInfo]:
    try:
        from can.interfaces.kvaser import canlib
    except Exception:  # noqa: BLE001
        return []
    if getattr(canlib, "__canlib", None) is None:
        return [_driver_missing("kvaser", "Kvaser")] if sys.platform == "win32" else []
    found = []
    for cfg in canlib.KvaserBus._detect_available_configs():  # type: ignore[no-untyped-call]
        name = str(cfg.get("device_name") or "Kvaser")
        if "virtual" in name.lower():
            continue  # Kvaser installs virtual channels that never reach a vehicle
        found.append(AdapterInfo(kind="kvaser", interface="kvaser", channel=str(cfg["channel"]), label=name,
                                 status=READY))
    return found


def probe_rp1210() -> list[AdapterInfo]:
    if sys.platform != "win32":
        return []
    from src.hal.rp1210.client import _declared_vendor_dlls

    vendors = sorted(_declared_vendor_dlls())
    if not vendors:
        return []
    # The app's RP1210 path (src/main.py::build_bus) opens device id 1 through
    # the bitness-matched RP1210 entry DLL; vendor choice is the INI's.
    return [
        AdapterInfo(kind="rp1210", interface="rp1210", channel="1", label="RP1210 (" + ", ".join(vendors) + ")",
                    status=DRIVER_READY,
                    message_tr="Ağır vasıta teşhis cihazı sürücüsü bulundu. Cihaz bağlanırken kontrol edilecek.",
                    message_en="Heavy-duty VCI driver found. The device is checked when connecting.",
                    details={"vendors": vendors})
    ]


def probe_socketcan() -> list[AdapterInfo]:
    if not sys.platform.startswith("linux"):
        return []
    import can

    return [
        AdapterInfo(kind="socketcan", interface="socketcan", channel=str(cfg["channel"]),
                    label=f"SocketCAN {cfg['channel']}", status=READY)
        for cfg in can.detect_available_configs(interfaces=["socketcan"])
    ]


DEFAULT_PROBES: tuple[tuple[str, Callable[[], list[AdapterInfo]]], ...] = (
    ("pcan", probe_pcan),
    ("kvaser", probe_kvaser),
    ("rp1210", probe_rp1210),
    ("socketcan", probe_socketcan),
)


def discover_adapters(
    probes: Iterable[tuple[str, Callable[[], list[AdapterInfo]]]] = DEFAULT_PROBES,
    *,
    include_simulator: bool = True,
) -> list[AdapterInfo]:
    """All adapters, usable ones first; the simulator is always last."""
    results: list[AdapterInfo] = []
    for kind, probe in probes:
        try:
            results.extend(probe())
        except Exception as exc:  # noqa: BLE001 — one broken vendor DLL must not hide the others
            logger.warning("Adapter probe failed", extra={"kind": kind, "error": type(exc).__name__})
            results.append(AdapterInfo(
                kind=kind, interface=kind, channel="", label=kind.upper(), status=PROBE_FAILED,
                message_tr="Bu adaptör türü kontrol edilemedi. Adaptörü çıkarıp takın ve tekrar deneyin.",
                message_en="This adapter type could not be checked. Unplug and replug it, then try again.",
            ))
    rank = {READY: 0, DRIVER_READY: 1, DRIVER_MISSING: 2, PROBE_FAILED: 3, NOT_FOUND: 4}
    results.sort(key=lambda a: rank.get(a.status, 9))
    if include_simulator:
        results.append(SIMULATOR)
    return results

"""Listen-only connection test (Aşama 5, MECHANIC_FLOW.md §3.9-3.10, §4).

The test opens the adapter **listen-only** at each bitrate candidate of the
selected vehicle type, listens for a short window and decides:

* which bitrate carries clean traffic,
* how many control units (J1939 / NMEA 2000 source addresses) talk,
* whether the traffic the vehicle type should always broadcast is present
  (e.g. J1939 engine controller PGN 61444 on a truck),
* the battery voltage when the vehicle broadcasts it (J1939 PGN 65271).

Safety (MECHANIC_FLOW.md §7, G1): nothing is ever transmitted. A channel that
cannot prove it is listen-only is refused before it is opened, and the test
never calls ``send``. Every outcome maps to one plain-language message.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.core.errors import HardwareError, SafetyError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.hal.base import AbstractBus, BusState

logger = get_logger("engine.connection.listen_test")

PGN_VEHICLE_ELECTRICAL_POWER = 65271  # J1939 VEP1, SPN 168 battery potential

# Outcome codes → (TR, EN). Wording follows MECHANIC_FLOW.md §4.
MESSAGES: dict[str, tuple[str, str]] = {
    "READY": ("Araçla bağlantı kuruldu.", "Connected to the vehicle."),
    "QUIET_VEHICLE": (
        "Adaptör takılı ama araç şu an kendiliğinden veri göndermiyor. Birçok otomobilde bu normaldir; "
        "arıza kodları taramada sizin izninizle sorulacak. Kontağın açık olduğundan emin olun.",
        "The adapter is connected but the vehicle isn't sending data by itself. This is normal for many "
        "cars; fault codes will be asked for during the scan with your permission. Make sure the ignition is on.",
    ),
    "EXPECTED_MISSING": (
        "Veri geliyor ama beklediğimiz motor beyni yok. Başka bir teşhis soketi olabilir.",
        "Data is flowing but the engine unit isn't there. There may be another diagnostic socket.",
    ),
    "NO_TRAFFIC": (
        "Araçtan veri gelmiyor. Kontak açık mı? Soket tam oturdu mu?",
        "No data from the vehicle. Is the ignition on? Is the plug fully seated?",
    ),
    "WRONG_BITRATE": (
        "Araçla dil uyuşmadı. Doğru soketi ve doğru araç türünü seçtiğinizden emin olun.",
        "Couldn't match the vehicle's data speed. Check the socket and the vehicle type.",
    ),
    "BUS_ERROR": (
        "Hatta elektriksel bir sorun var (bağlantı kopuk veya kısa devre olabilir). Kabloyu ve soketi kontrol edin.",
        "There's an electrical problem on the line (open or short circuit). Check the cable and the socket.",
    ),
    "ADAPTER_ERROR": (
        "Adaptör açılamadı. USB kablosunu çıkarıp tekrar takın. Başka bir program adaptörü kullanıyorsa kapatın.",
        "The adapter could not be opened. Unplug and replug the USB cable. Close any other program using it.",
    ),
    "LISTEN_ONLY_UNAVAILABLE": (
        "Bu adaptör güvenli dinleme modunu desteklemiyor, bağlantı testi yapılmadı.",
        "This adapter can't do safe listen-only mode; the test was not run.",
    ),
    "CANCELLED": ("Bağlantı testi durduruldu.", "Connection test stopped."),
}

BATTERY_WEAK: tuple[str, str] = (
    "Akü zayıf ({volts:.1f} V). Sonuçlar yanıltıcı olabilir. Önce aküyü şarj edin.",
    "Battery is weak ({volts:.1f} V). Results may be misleading. Charge it first.",
)

USABLE = frozenset({"READY", "QUIET_VEHICLE", "EXPECTED_MISSING"})


class ListenChannel(Protocol):
    """What the test needs from an adapter. Implementations never transmit."""

    def open(self, bitrate: int) -> None: ...
    def read(self, timeout_s: float) -> CanFrame | None: ...
    def error_frames(self) -> int: ...
    def bus_off(self) -> bool: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class ExpectedPgn:
    pgn: int
    name_tr: str
    name_en: str


@dataclass
class BitrateObservation:
    bitrate: int
    frames: int = 0
    error_frames: int = 0
    bus_off: bool = False
    source_addresses: set[int] = field(default_factory=set)
    pgns: set[int] = field(default_factory=set)
    standard_ids: set[int] = field(default_factory=set)
    battery_volts: float | None = None

    @property
    def clean(self) -> bool:
        return self.frames >= 3 and not self.bus_off and self.error_frames <= max(1, self.frames // 10)

    def as_dict(self) -> dict[str, Any]:
        return {"bitrate": self.bitrate, "frames": self.frames, "error_frames": self.error_frames,
                "bus_off": self.bus_off}


@dataclass
class ListenTestResult:
    code: str
    bitrate: int | None = None
    ecu_count: int = 0
    frames: int = 0
    expected: list[dict[str, Any]] = field(default_factory=list)
    battery_volts: float | None = None
    battery_warning: bool = False
    attempts: list[BitrateObservation] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.code in USABLE

    def as_dict(self) -> dict[str, Any]:
        tr, en = MESSAGES[self.code]
        out: dict[str, Any] = {
            "code": self.code, "usable": self.usable, "bitrate": self.bitrate, "ecu_count": self.ecu_count,
            "frames": self.frames, "expected": self.expected, "message_tr": tr, "message_en": en,
            "battery_volts": self.battery_volts, "battery_warning": self.battery_warning,
            "battery_message_tr": "", "battery_message_en": "",
            "attempts": [a.as_dict() for a in self.attempts],
        }
        if self.battery_warning and self.battery_volts is not None:
            out["battery_message_tr"] = BATTERY_WEAK[0].format(volts=self.battery_volts)
            out["battery_message_en"] = BATTERY_WEAK[1].format(volts=self.battery_volts)
        return out


def _j1939_pgn_sa(arbitration_id: int) -> tuple[int, int]:
    """PGN and source address of a 29-bit J1939 / NMEA 2000 identifier."""
    sa = arbitration_id & 0xFF
    pf = (arbitration_id >> 16) & 0xFF
    ps = (arbitration_id >> 8) & 0xFF
    dp_edp = (arbitration_id >> 24) & 0x03
    pgn = (dp_edp << 16) | (pf << 8) | (ps if pf >= 240 else 0)
    return pgn, sa


def battery_volts_from_vep1(data: bytes) -> float | None:
    """SPN 168 (bytes 5-6, 0.05 V/bit). None when not available or implausible."""
    if len(data) < 6:
        return None
    raw = data[4] | (data[5] << 8)
    if raw >= 0xFB00:  # J1939 "error / not available"
        return None
    volts = raw * 0.05
    return round(volts, 2) if 6.0 <= volts <= 36.0 else None


def battery_is_weak(volts: float) -> bool:
    """< 11.8 V on a 12 V system, < 23.6 V on a 24 V system (MECHANIC_FLOW.md §4)."""
    return volts < (23.6 if volts > 18.0 else 11.8)


def observe(channel: ListenChannel, bitrate: int, window_s: float, *,
            clock: Callable[[], float] = time.monotonic,
            cancelled: Callable[[], bool] = lambda: False) -> BitrateObservation:
    obs = BitrateObservation(bitrate=bitrate)
    channel.open(bitrate)
    try:
        start_errors = channel.error_frames()
        deadline = clock() + window_s
        while clock() < deadline and not cancelled():
            frame = channel.read(timeout_s=min(0.05, max(0.0, deadline - clock())))
            if frame is None:
                if channel.bus_off():
                    obs.bus_off = True
                    break
                continue
            obs.frames += 1
            if frame.is_extended:
                pgn, sa = _j1939_pgn_sa(frame.arbitration_id)
                obs.pgns.add(pgn)
                obs.source_addresses.add(sa)
                if pgn == PGN_VEHICLE_ELECTRICAL_POWER:
                    volts = battery_volts_from_vep1(bytes(frame.data))
                    if volts is not None:
                        obs.battery_volts = volts
            else:
                obs.standard_ids.add(frame.arbitration_id)
        obs.error_frames = max(0, channel.error_frames() - start_errors)
        obs.bus_off = obs.bus_off or channel.bus_off()
    finally:
        channel.close()
    return obs


def run_listen_test(
    channel: ListenChannel,
    bitrates: Sequence[int],
    expected: Sequence[ExpectedPgn] = (),
    *,
    window_s: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[str, int | None], None] = lambda step, bitrate: None,
) -> ListenTestResult:
    """Try each bitrate listen-only; stop at the first with clean traffic."""
    attempts: list[BitrateObservation] = []
    chosen: BitrateObservation | None = None
    for bitrate in bitrates:
        if cancelled():
            return ListenTestResult(code="CANCELLED", attempts=attempts)
        on_progress("bitrate", bitrate)
        try:
            obs = observe(channel, bitrate, window_s, clock=clock, cancelled=cancelled)
        except SafetyError:
            return ListenTestResult(code="LISTEN_ONLY_UNAVAILABLE", attempts=attempts)
        except HardwareError as exc:
            if getattr(exc, "code", None) == "HARDWARE_LISTEN_ONLY_UNSUPPORTED":
                return ListenTestResult(code="LISTEN_ONLY_UNAVAILABLE", attempts=attempts)
            logger.warning("Listen test could not open the adapter",
                           extra={"bitrate": bitrate, "error": getattr(exc, "code", type(exc).__name__)})
            return ListenTestResult(code="ADAPTER_ERROR", attempts=attempts)
        except (OSError, ValueError) as exc:
            logger.warning("Listen test could not open the adapter",
                           extra={"bitrate": bitrate, "error": type(exc).__name__})
            return ListenTestResult(code="ADAPTER_ERROR", attempts=attempts)
        attempts.append(obs)
        if obs.clean:
            chosen = obs
            break
    if cancelled():
        return ListenTestResult(code="CANCELLED", attempts=attempts)

    if chosen is None:
        if any(a.bus_off for a in attempts):
            code = "BUS_ERROR"
        elif any(a.error_frames or a.frames for a in attempts):
            code = "WRONG_BITRATE"
        else:
            code = "QUIET_VEHICLE" if not expected else "NO_TRAFFIC"
        return ListenTestResult(code=code, attempts=attempts,
                                bitrate=bitrates[0] if code == "QUIET_VEHICLE" and bitrates else None)

    on_progress("ecus", chosen.bitrate)
    seen = [
        {"pgn": e.pgn, "name_tr": e.name_tr, "name_en": e.name_en, "seen": e.pgn in chosen.pgns}
        for e in expected
    ]
    code = "READY" if all(s["seen"] for s in seen) else "EXPECTED_MISSING"
    volts = chosen.battery_volts
    return ListenTestResult(
        code=code, bitrate=chosen.bitrate, frames=chosen.frames,
        ecu_count=len(chosen.source_addresses), expected=seen, battery_volts=volts,
        battery_warning=volts is not None and battery_is_weak(volts), attempts=attempts,
    )


class BusListenChannel:
    """ListenChannel over a HAL bus built per bitrate. Refuses non-listen-only buses."""

    def __init__(self, factory: Callable[[int], AbstractBus]) -> None:
        self._factory = factory
        self._bus: AbstractBus | None = None

    def open(self, bitrate: int) -> None:
        bus = self._factory(bitrate)
        if getattr(bus, "listen_only", False) is not True:
            raise SafetyError("connection test requires a listen-only bus")
        bus.connect()
        if bus.get_metrics().state != BusState.PASSIVE:
            # A driver that connected ACTIVE did not honour listen-only.
            bus.disconnect()
            raise SafetyError("adapter did not enter listen-only mode")
        self._bus = bus

    def read(self, timeout_s: float) -> CanFrame | None:
        return self._bus.recv(timeout_s=timeout_s) if self._bus is not None else None

    def error_frames(self) -> int:
        return self._bus.get_metrics().error_frames if self._bus is not None else 0

    def bus_off(self) -> bool:
        return self._bus is not None and self._bus.get_metrics().state == BusState.BUS_OFF

    def close(self) -> None:
        if self._bus is not None:
            try:
                self._bus.disconnect()
            finally:
                self._bus = None

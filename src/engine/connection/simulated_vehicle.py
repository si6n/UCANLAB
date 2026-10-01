"""Hardware-free vehicle for the connection wizard's simulator mode (Aşama 5).

``SimulatedVehicleBus`` is a listen-only bus that plays the broadcast traffic a
vehicle type produces at its native bitrate: J1939 engine/transmission/brake
units on trucks and machines, NMEA 2000 engine parameters on boats, 11-bit
powertrain frames on cars. Opened at the wrong bitrate it produces only error
frames, like a real transceiver. Scenarios reproduce the error paths of
MECHANIC_FLOW.md §4 so the whole wizard can be exercised without hardware.

It can never transmit: ``send`` always raises ``SafetyError``. Frames are
marked ``source="synthetic"`` so nothing downstream mistakes them for a real
vehicle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.core.errors import HardwareError, SafetyError
from src.core.models.can_frame import CanFrame
from src.hal.base import AbstractBus, BusState

SCENARIOS = ("ok", "ignition_off", "wrong_socket", "weak_battery", "bus_short", "no_listen_only")


def _j1939_id(pgn: int, sa: int, priority: int = 6) -> int:
    return (priority << 26) | (pgn << 8) | sa


@dataclass(frozen=True)
class _Tx:
    arbitration_id: int
    data: bytes
    extended: bool = True


def _vep1(volts: float) -> bytes:
    raw = int(round(volts / 0.05))
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, raw & 0xFF, raw >> 8, 0xFF, 0xFF])


def _dm1_single(spn: int, fmi: int, *, amber: bool = True) -> bytes:
    """DM1 with one DTC (lamp byte: amber warning lamp on), occurrence count 1."""
    lamps = 0x04 if amber else 0x00
    return bytes([lamps, 0xFF, spn & 0xFF, (spn >> 8) & 0xFF, ((spn >> 16) & 0x07) << 5 | (fmi & 0x1F), 0x01,
                  0xFF, 0xFF])


# Simulated truck/machine fault: DPF differential pressure high (SPN 3251 FMI 0).
SIMULATED_DM1 = _dm1_single(3251, 0)

_TRUCK = (
    _Tx(_j1939_id(61444, 0x00, 3), bytes([0xF0, 0x7D, 0x82, 0xC0, 0x12, 0x00, 0xF0, 0x82])),  # EEC1 ~600 rpm
    _Tx(_j1939_id(61442, 0x03, 3), bytes([0xC0, 0x00, 0x00, 0xFF, 0xF0, 0xFF, 0xFF, 0xFF])),  # ETC1
    _Tx(_j1939_id(65265, 0x00), bytes([0xF0, 0x00, 0x00, 0xC0, 0x00, 0x00, 0x00, 0xFF])),  # CCVS stationary
    _Tx(_j1939_id(65262, 0x00), bytes([0x7D, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])),  # ET1 85 °C
    _Tx(_j1939_id(61441, 0x0B, 3), bytes([0xFF] * 8)),  # EBC1 brakes
    _Tx(_j1939_id(65226, 0x00), SIMULATED_DM1),  # DM1 active codes
)
_BOAT = (
    _Tx(_j1939_id(127488, 0x10, 2), bytes([0x00, 0x70, 0x17, 0xFF, 0xFF, 0x7F, 0xFF, 0xFF])),  # engine rapid
    _Tx(_j1939_id(127489, 0x10, 2), bytes([0x00, 0x1A, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])),  # engine dynamic
    _Tx(_j1939_id(127508, 0x20, 6), bytes([0x00, 0xE8, 0x04, 0xFF, 0x7F, 0xFF, 0xFF, 0xFF])),  # battery status
)
_CAR = (
    _Tx(0x0C9, bytes([0x00, 0x0B, 0xB8, 0x00, 0x00, 0x00, 0x00, 0x00]), False),
    _Tx(0x1A0, bytes([0x00] * 8), False),
    _Tx(0x3E9, bytes([0x5A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]), False),
)

NATIVE_BITRATE = {"car": 500_000, "truck": 250_000, "boat": 250_000, "construction": 250_000}


class SimulatedVehicleBus(AbstractBus):
    """Listen-only simulated vehicle. See module docstring."""

    FRAME_INTERVAL_S = 0.002

    def __init__(self, vehicle_type: str, bitrate: int, scenario: str = "ok",
                 *, sleep: bool = True) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}")
        super().__init__(channel_id="sim_vehicle", bitrate=bitrate)
        self.vehicle_type = vehicle_type
        self.scenario = scenario
        self.listen_only = scenario != "no_listen_only"
        self._sleep = sleep
        self._i = 0
        if vehicle_type == "boat":
            self._schedule: tuple[_Tx, ...] = _BOAT
        elif vehicle_type == "car":
            self._schedule = _CAR
        else:
            self._schedule = _TRUCK
        if scenario == "wrong_socket":
            # Body/comfort network: traffic flows, but no engine controller (SA 0).
            self._schedule = (_Tx(_j1939_id(65217, 0x21), bytes([0xFF] * 8)),
                              _Tx(_j1939_id(64997, 0x33), bytes([0xFF] * 8)))
        elif vehicle_type in ("truck", "construction"):
            volts = 22.9 if scenario == "weak_battery" else 27.6
            self._schedule = (*self._schedule, _Tx(_j1939_id(65271, 0x00), _vep1(volts)))

    @property
    def native_bitrate(self) -> int:
        return NATIVE_BITRATE.get(self.vehicle_type, 250_000)

    def connect(self) -> None:
        self.is_connected = True
        self.metrics.state = BusState.PASSIVE if self.listen_only else BusState.ACTIVE

    def disconnect(self) -> None:
        self.is_connected = False
        self.metrics.state = BusState.DISCONNECTED

    def send(self, frame: CanFrame) -> None:
        raise SafetyError("the simulated vehicle is listen-only; nothing is ever transmitted",
                          code="SIMULATOR_LISTEN_ONLY")

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        if not self.is_connected:
            raise HardwareError("simulated vehicle bus is not connected")
        if self._sleep:
            time.sleep(self.FRAME_INTERVAL_S)
        if self.scenario == "ignition_off":
            return None
        if self.scenario == "bus_short":
            self.metrics.error_frames += 1
            if self.metrics.error_frames > 32:  # a shorted line goes bus-off almost at once
                self.metrics.state = BusState.BUS_OFF
            return None
        if self.bitrate != self.native_bitrate:
            self.metrics.error_frames += 1
            return None
        tx = self._schedule[self._i % len(self._schedule)]
        self._i += 1
        self.metrics.rx_frames += 1
        return CanFrame(channel_id=self.channel_id, arbitration_id=tx.arbitration_id, dlc=len(tx.data),
                        data=tx.data, is_extended=tx.extended, source="synthetic")

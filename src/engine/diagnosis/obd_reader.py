"""Read-only OBD-II fault-code readout (Aşama 6, MECHANIC_FLOW.md §3.11, §7.2).

Asks the engine and transmission controllers for stored (Mode 03), pending
(Mode 07) and permanent (Mode 0A) codes, and the engine controller for its
freeze frame (Mode 02: the conditions when the code was stored) and its
on-board monitor results (Mode 06: each test with the ECU's own min/max
limits) and its identification (Mode 09: VIN, calibration IDs, CVNs, ECU
name), through the existing
``ActiveDiagnosticPoller`` and the ``TxSafetyGateway``. It only runs inside a
consented read-only session (``ReadOnlyPolicy`` installed on the gateway); the
reader itself never builds anything but these read requests.

Requests are sent to the *physical* request addresses (0x7E0 / 0x7E1) so that
the ISO-TP flow control for a multi-frame answer also goes to the physical
address (ISO 15765-4), which is what the read-only policy allows.

``SimulatedObdEcu`` is a hardware-free car for the simulator: it validates
every frame against the same ``ReadOnlyPolicy`` and answers like an ECU,
including multi-frame answers that need flow control.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.core.contracts.ports import QueueRxSubscription
from src.core.errors import ProtocolError, SafetyError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.protocols.obd.poller import ActiveDiagnosticPoller
from src.safety.read_only_policy import ReadOnlyPolicy

logger = get_logger("engine.diagnosis.obd_reader")

# (request id, response id, TR name, EN name)
OBD_ECUS: tuple[tuple[int, int, str, str], ...] = (
    (0x7E0, 0x7E8, "Motor kontrol ünitesi", "Engine control unit"),
    (0x7E1, 0x7E9, "Şanzıman kontrol ünitesi", "Transmission control unit"),
)
MODE_KIND = {0x03: "stored", 0x07: "pending", 0x0A: "permanent"}
RESPONSE_IDS = frozenset(range(0x7E8, 0x7F0))

Subscribe = Callable[[Callable[[CanFrame], None]], Callable[[], None]]


@dataclass(frozen=True)
class ObdCode:
    code: str
    kind: str  # stored | pending | permanent
    ecu_tr: str
    ecu_en: str


# Freeze-frame PIDs read in Mode 02 -> canonical copilot signal names
# (signal_measurement_map.json obd.pid). Fuel trims have no copilot signal yet;
# they are kept for display under their own names.
FREEZE_FRAME_PIDS: dict[int, str] = {
    0x0C: "EngineSpeed", 0x05: "CoolantTemp", 0x04: "EngineLoad", 0x0D: "VehicleSpeed",
    0x0F: "IntakeAirTemp", 0x11: "ThrottlePosition", 0x42: "BatteryVoltage", 0x10: "EngineAirFlow",
    0x23: "FuelPressure", 0x5C: "EngineOilTemp", 0x06: "ShortTermFuelTrimB1", 0x07: "LongTermFuelTrimB1",
}
MAX_MODE06_MIDS = 40


@dataclass
class ObdReadOutcome:
    status: str  # ok | no_answer | refused | error
    codes: list[ObdCode] = field(default_factory=list)
    answered_ecus: list[str] = field(default_factory=list)
    unsupported_modes: list[str] = field(default_factory=list)
    # Mode 02: {"dtc": "P0301", "readings": {"EngineSpeed": 780.0, ...}, "ecu": "..."} or None
    freeze_frame: dict[str, Any] | None = None
    # Mode 06: [{"mid", "tid", "uasid", "value", "min", "max", "unit", "scaled", "passed"}]
    monitors: list[dict[str, Any]] = field(default_factory=list)
    # Mode 09 / J1939 VI+DM19+SOFT+CI: {"vin", "calibrations": [{"cal_id", "cvn"}], "ecu_name",
    # "software", "component", "ecu", "protocol"} with only the fields the ECU answered, or None
    identity: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "codes": [c.__dict__ for c in self.codes],
            "answered_ecus": list(self.answered_ecus),
            "unsupported_modes": list(self.unsupported_modes),
            "freeze_frame": self.freeze_frame,
            "monitors": list(self.monitors),
            "identity": self.identity,
        }


async def read_freeze_frame(poller: ActiveDiagnosticPoller, tx_id: int, rx_id: int,
                            timeout_s: float) -> dict[str, Any] | None:
    """Mode 02 frame 0: the DTC that stored it, then the listed PIDs. None when there is no frame."""
    try:
        head = await poller.poll_freeze_frame_once(0x02, 0, tx_id=tx_id, rx_id=rx_id, timeout_s=timeout_s)
    except (TimeoutError, ProtocolError):
        return None
    dtc = str(head.value or "") if head.is_valid else ""
    if not dtc or dtc.endswith("0000"):
        return None  # "P0000": no freeze frame stored
    readings: dict[str, float] = {}
    for pid, name in FREEZE_FRAME_PIDS.items():
        try:
            result = await poller.poll_freeze_frame_once(pid, 0, tx_id=tx_id, rx_id=rx_id, timeout_s=timeout_s)
        except (TimeoutError, ProtocolError):
            continue  # PID not in this ECU's freeze frame
        if result.is_valid and isinstance(result.value, (int, float)) and not isinstance(result.value, bool):
            readings[name] = round(float(result.value), 3)
    return {"dtc": dtc, "readings": readings}


async def read_monitor_results(poller: ActiveDiagnosticPoller, tx_id: int, rx_id: int,
                               timeout_s: float) -> list[dict[str, Any]]:
    """Mode 06: supported OBDMIDs (0x00, 0x20, … chain), then every supported test MID."""
    from src.protocols.obd.mode06 import decode_mode06_response

    supported: list[int] = []
    base = 0x00
    while base <= 0xE0:
        try:
            mids, _tests = decode_mode06_response(await poller.poll_mode06_once(base, tx_id=tx_id, rx_id=rx_id,
                                                                                timeout_s=timeout_s))
        except (TimeoutError, ProtocolError, ValueError):
            break
        supported += [m for m in mids if m % 0x20 != 0]
        if base + 0x20 not in mids:
            break
        base += 0x20
    out: list[dict[str, Any]] = []
    for mid in supported[:MAX_MODE06_MIDS]:
        try:
            _mids, tests = decode_mode06_response(await poller.poll_mode06_once(mid, tx_id=tx_id, rx_id=rx_id,
                                                                                timeout_s=timeout_s))
        except (TimeoutError, ProtocolError, ValueError):
            continue
        out += [t.as_dict() for t in tests]
    return out


async def read_vehicle_identity(poller: ActiveDiagnosticPoller, tx_id: int, rx_id: int,
                                timeout_s: float) -> dict[str, Any] | None:
    """Mode 09 VIN, calibration IDs, CVNs and ECU name; None when the ECU answers none of them."""
    from src.protocols.obd import mode09

    async def ask(infotype: int) -> bytes | None:
        try:
            return await poller.poll_vehicle_info_once(infotype, tx_id=tx_id, rx_id=rx_id, timeout_s=timeout_s)
        except (TimeoutError, ProtocolError):
            return None

    out: dict[str, Any] = {}
    try:
        if (raw := await ask(mode09.INFOTYPE_VIN)) is not None and (vin := mode09.decode_vin(raw)):
            out["vin"] = vin
        cal_ids = mode09.decode_cal_ids(raw) if (raw := await ask(mode09.INFOTYPE_CAL_ID)) is not None else []
        cvns = mode09.decode_cvns(raw) if cal_ids and (raw := await ask(mode09.INFOTYPE_CVN)) is not None else []
        if cal_ids:
            # CVN n belongs to calibration n (SAE J1979); a missing CVN stays empty, never invented.
            out["calibrations"] = [{"cal_id": c, "cvn": cvns[i] if i < len(cvns) else ""}
                                   for i, c in enumerate(cal_ids)]
        if (raw := await ask(mode09.INFOTYPE_ECU_NAME)) is not None and (name := mode09.decode_ecu_name(raw)):
            out["ecu_name"] = name
    except ValueError:
        pass  # a malformed answer ends the identity read; what was decoded stays
    return out or None


async def read_obd_fault_codes(
    tx_port: Any,
    subscribe: Subscribe,
    *,
    channel_id: str,
    ecus: Sequence[tuple[int, int, str, str]] = OBD_ECUS,
    timeout_s: float = 1.0,
    snapshot: bool = True,
) -> ObdReadOutcome:
    """Mode 03/07/0A from each ECU, then (``snapshot``) the first answering ECU's
    freeze frame (Mode 02), monitor results (Mode 06) and identification
    (Mode 09). Never raises; the outcome says what happened."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[CanFrame] = asyncio.Queue()
    subscription = QueueRxSubscription(queue)

    def deliver(frame: CanFrame) -> None:
        if frame.arbitration_id in RESPONSE_IDS:
            loop.call_soon_threadsafe(queue.put_nowait, frame)

    unsubscribe = subscribe(deliver)
    outcome = ObdReadOutcome(status="no_answer")
    try:
        for tx_id, rx_id, name_tr, name_en in ecus:
            poller = ActiveDiagnosticPoller(tx_port=tx_port, rx_subscription=subscription,
                                            channel_id=channel_id, tx_id=tx_id, rx_id=rx_id)
            answered = False
            for mode, kind in MODE_KIND.items():
                try:
                    result = await poller.poll_dtc_once(mode, tx_id=tx_id, rx_id=rx_id, timeout_s=timeout_s)
                except TimeoutError:
                    if mode == 0x03:
                        break  # this ECU does not answer; don't keep asking it
                    continue
                except ProtocolError:
                    answered = True
                    outcome.unsupported_modes.append(f"{name_en}:{kind}")
                    continue
                answered = True
                outcome.codes.extend(ObdCode(code, kind, name_tr, name_en) for code in result.dtcs)
            if answered:
                outcome.answered_ecus.append(name_tr)
                if snapshot and outcome.freeze_frame is None and not outcome.monitors:
                    ff = await read_freeze_frame(poller, tx_id, rx_id, timeout_s)
                    if ff is not None:
                        outcome.freeze_frame = dict(ff, ecu=name_tr)
                    outcome.monitors = await read_monitor_results(poller, tx_id, rx_id, timeout_s)
                    identity = await read_vehicle_identity(poller, tx_id, rx_id, timeout_s)
                    if identity is not None:
                        outcome.identity = dict(identity, ecu=name_tr, protocol="OBD Mode 09")
    except SafetyError as exc:
        logger.error("Read request refused by the TX gateway", extra={"code": getattr(exc, "code", "")})
        outcome.status = "refused"
        return outcome
    except Exception as exc:  # noqa: BLE001 — a reader failure is a result, not a crash
        logger.error("OBD read failed", extra={"error": type(exc).__name__})
        outcome.status = "error"
        return outcome
    finally:
        unsubscribe()
    outcome.status = "ok" if outcome.answered_ecus else "no_answer"
    return outcome


class SimulatedObdEcu:
    """Hardware-free OBD-II car: a TxPort that answers read requests.

    Every frame is checked against a ``ReadOnlyPolicy`` exactly like the real
    gateway would; a violation raises ``SafetyError``.
    """

    # Freeze frame of the first stored code (raw PID data bytes, SAE J1979 encodings):
    # 780 rpm, 91 °C coolant, 23 % load, standing, 14.1 V, STFT +9 %, LTFT +14 %.
    FREEZE_FRAME: dict[int, bytes] = {
        0x0C: bytes([0x0C, 0x30]), 0x05: bytes([131]), 0x04: bytes([59]), 0x0D: bytes([0]),
        0x42: bytes([0x37, 0x14]), 0x06: bytes([140]), 0x07: bytes([146]),
    }
    # Mode 06: catalyst B1 (OBDMID 0x21) passes near its limit; misfire cylinder 1
    # (OBDMID 0xA2) fails: 41 counts against a maximum of 20.
    MODE06: dict[int, bytes] = {           # supported-OBDMID bitmasks (bit 31 = base+1 … bit 0 = base+32)
        0x00: bytes([0x80, 0x00, 0x00, 0x01]),  # 0x01; 0x20 -> next range
        0x20: bytes([0x80, 0x00, 0x00, 0x01]),  # 0x21; 0x40 -> next range
        0x40: bytes([0x00, 0x00, 0x00, 0x01]),  # 0x60 -> next range
        0x60: bytes([0x00, 0x00, 0x00, 0x01]),  # 0x80 -> next range
        0x80: bytes([0x00, 0x00, 0x00, 0x01]),  # 0xA0 -> next range
        0xA0: bytes([0x40, 0x00, 0x00, 0x00]),  # 0xA2 (misfire cylinder 1); end
    }
    MODE06_TESTS: dict[int, list[bytes]] = {
        0x01: [bytes([0x01, 0x01, 0x0A, 0x0E, 0x10, 0x0B, 0xB8, 0x0F, 0xA0])],   # O2 B1S1 R>L switch, passes
        0x21: [bytes([0x21, 0x82, 0x01, 0x00, 0x2E, 0x00, 0x00, 0x00, 0x30])],   # catalyst B1: 46 of max 48
        0xA2: [bytes([0xA2, 0x0B, 0x24, 0x00, 0x29, 0x00, 0x00, 0x00, 0x14])],   # cylinder 1 misfires: 41 > 20
    }

    # Mode 09: a synthetic VIN with a Volkswagen Group WMI (no real vehicle), one
    # calibration with its CVN and the ECU name.
    VIN = "WVWZZZ1KZS1M00001"
    CAL_ID = "SIMCAL-0001"
    CVN = bytes([0x1A, 0x2B, 0x3C, 0x4D])
    ECU_NAME = b"ECM\x00-EngineControl".ljust(20, b"\x00")

    def __init__(self, stored: Sequence[str] = ("P0301", "P0420"), pending: Sequence[str] = ("P0171",),
                 permanent: Sequence[str] = (), *, policy_ttl_s: float = 60.0) -> None:
        self._codes = {0x03: list(stored), 0x07: list(pending), 0x0A: list(permanent)}
        self._freeze_dtc = list(stored)[0] if stored else None
        self._policy = ReadOnlyPolicy(expires_ns=time.monotonic_ns() + int(policy_ttl_s * 1e9),
                                      reason="simulator")
        self._listeners: list[Callable[[CanFrame], None]] = []
        self._pending_cf: dict[int, list[bytes]] = {}
        self.sent: list[CanFrame] = []

    def subscribe(self, callback: Callable[[CanFrame], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _emit(self, arbitration_id: int, data: bytes) -> None:
        frame = CanFrame(channel_id="sim_obd", arbitration_id=arbitration_id, dlc=8,
                         data=data.ljust(8, b"\xaa"), source="synthetic")
        for listener in list(self._listeners):
            listener(frame)

    @staticmethod
    def _encode(code: str) -> bytes:
        letter = "PCBU".index(code[0])
        a = (letter << 6) | (int(code[1]) << 4) | int(code[2], 16)
        return bytes([a, int(code[3:5], 16)])

    async def send(self, frame: CanFrame, **_: Any) -> None:
        self.send_sync(frame)

    def send_sync(self, frame: CanFrame, **_: Any) -> None:
        violation = self._policy.violation(frame, time.monotonic_ns())
        if violation is not None:
            raise SafetyError("simulated gateway refused a non read-only frame", code="READ_ONLY_VIOLATION",
                              details={"violation": violation})
        self.sent.append(frame)
        data = bytes(frame.data)
        response_id = frame.arbitration_id + 8
        if data[0] == 0x30:  # flow control: release the consecutive frames
            for chunk in self._pending_cf.pop(response_id, []):
                self._emit(response_id, chunk)
            return
        if frame.arbitration_id != 0x7E0:
            return  # only the engine ECU exists in the simulated car
        mode = data[1]
        payload = self._answer(mode, data)
        if payload is None:
            self._emit(response_id, bytes([0x03, 0x7F, mode, 0x31 if mode in (0x02, 0x06, 0x09) else 0x11]))
            return
        if len(payload) <= 7:
            self._emit(response_id, bytes([len(payload)]) + payload)
            return
        self._emit(response_id, bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6])
        rest, chunks, seq = payload[6:], [], 1
        while rest:
            chunks.append(bytes([0x20 | (seq & 0x0F)]) + rest[:7])
            rest, seq = rest[7:], seq + 1
        self._pending_cf[response_id] = chunks

    def _answer(self, mode: int, data: bytes) -> bytes | None:
        if mode in self._codes:
            codes = self._codes[mode]
            return bytes([mode + 0x40, len(codes)]) + b"".join(self._encode(c) for c in codes)
        if mode == 0x02 and self._freeze_dtc is not None:
            pid, frame = data[2], data[3]
            if frame != 0:
                return None
            if pid == 0x02:
                return bytes([0x42, 0x02, 0x00]) + self._encode(self._freeze_dtc)
            raw = self.FREEZE_FRAME.get(pid)
            return None if raw is None else bytes([0x42, pid, 0x00]) + raw
        if mode == 0x06:
            mid = data[2]
            if mid in self.MODE06:
                return bytes([0x46, mid]) + self.MODE06[mid]
            tests = self.MODE06_TESTS.get(mid)
            return None if tests is None else bytes([0x46]) + b"".join(tests)
        if mode == 0x09:
            infotype = data[2]
            items = {0x02: self.VIN.encode("ascii"), 0x04: self.CAL_ID.encode("ascii").ljust(16, b"\x00"),
                     0x06: self.CVN, 0x0A: self.ECU_NAME}.get(infotype)
            return None if items is None else bytes([0x49, infotype, 0x01]) + items
        return None

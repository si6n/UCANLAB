"""Read-only OBD-II fault-code readout (Aşama 6, MECHANIC_FLOW.md §3.11, §7.2).

Asks the engine and transmission controllers for stored (Mode 03), pending
(Mode 07) and permanent (Mode 0A) codes through the existing
``ActiveDiagnosticPoller`` and the ``TxSafetyGateway``. It only runs inside a
consented read-only session (``ReadOnlyPolicy`` installed on the gateway); the
reader itself never builds anything but these three requests.

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


@dataclass
class ObdReadOutcome:
    status: str  # ok | no_answer | refused | error
    codes: list[ObdCode] = field(default_factory=list)
    answered_ecus: list[str] = field(default_factory=list)
    unsupported_modes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "codes": [c.__dict__ for c in self.codes],
            "answered_ecus": list(self.answered_ecus),
            "unsupported_modes": list(self.unsupported_modes),
        }


async def read_obd_fault_codes(
    tx_port: Any,
    subscribe: Subscribe,
    *,
    channel_id: str,
    ecus: Sequence[tuple[int, int, str, str]] = OBD_ECUS,
    timeout_s: float = 1.0,
) -> ObdReadOutcome:
    """Mode 03/07/0A from each ECU. Never raises; the outcome says what happened."""
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

    def __init__(self, stored: Sequence[str] = ("P0301", "P0420"), pending: Sequence[str] = ("P0171",),
                 permanent: Sequence[str] = (), *, policy_ttl_s: float = 60.0) -> None:
        self._codes = {0x03: list(stored), 0x07: list(pending), 0x0A: list(permanent)}
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
        if mode not in self._codes:
            self._emit(response_id, bytes([0x03, 0x7F, mode, 0x11]))
            return
        codes = self._codes[mode]
        payload = bytes([mode + 0x40, len(codes)]) + b"".join(self._encode(c) for c in codes)
        if len(payload) <= 7:
            self._emit(response_id, bytes([len(payload)]) + payload)
            return
        self._emit(response_id, bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6])
        rest, chunks, seq = payload[6:], [], 1
        while rest:
            chunks.append(bytes([0x20 | (seq & 0x0F)]) + rest[:7])
            rest, seq = rest[7:], seq + 1
        self._pending_cf[response_id] = chunks

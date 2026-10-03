"""Read-only J1939 snapshot readout for heavy-duty vehicles (DM4 freeze frame, DM7 -> DM30 test results).

The heavy-duty counterpart of the OBD-II freeze frame (Mode 02) and monitor
results (Mode 06) in ``obd_reader``. Active codes need no request on J1939
(DM1 is broadcast and lands in the scan session while listening); the snapshot
is what the engine ECU only gives when asked:

* **DM4**: the conditions (engine speed, load, coolant, boost, vehicle speed)
  stored with a fault code, requested with PGN 59904;
* **DM30**: the ECU's own test results with its own limits, asked for with
  DM7 test identifier 247 ("report the results already stored") for the SPNs
  of the active codes and of the freeze frame. No test is ever started.

Multi-packet answers arrive over the J1939-21 transport protocol; the reader
answers the ECU's RTS with CTS / end-of-message ACK through the existing
``J1939TransportProtocol``. Everything goes through the ``TxSafetyGateway``
inside a consented read session whose ``ReadOnlyPolicy`` has ``j1939=True``.

The result is an ``ObdReadOutcome`` (``freeze_frame`` + ``monitors``), so the
copilot reads a truck's snapshot exactly like a car's. Monitor records carry
``spn`` / ``fmi`` / ``tid`` instead of the OBD ``mid``.

``SimulatedJ1939Ecu`` is a hardware-free truck engine ECU for the simulator:
it validates every frame against the same policy and answers over the
transport protocol like a real ECU.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from typing import Any

from src.core.errors import SafetyError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.engine.diagnosis.obd_reader import ObdReadOutcome
from src.protocols.j1939.dm_results import (
    PGN_DM4,
    PGN_DM7,
    PGN_DM30,
    decode_dm4,
    decode_dm30,
    dm7_arbitration_id,
    dm7_report_payload,
)
from src.protocols.j1939.transport import PGN_TP_CM, PGN_TP_DT, J1939TransportProtocol
from src.safety.read_only_policy import ReadOnlyPolicy

logger = get_logger("engine.diagnosis.j1939_reader")

PGN_ACK: int = 59392  # 0xE800 Acknowledgment (byte 0: 0 ACK, 1 NACK, 2 access denied, 3 busy)
ENGINE_SA: int = 0x00
TOOL_SA: int = 0xF9
ENGINE_NAME_TR = "Motor kontrol ünitesi (J1939)"
MAX_DM30_SPNS = 20

Subscribe = Callable[[Callable[[CanFrame], None]], Callable[[], None]]


def _ids(arbitration_id: int) -> tuple[int, int, int]:
    """(PGN, destination or 0xFF, source) of a 29-bit J1939 identifier (data page 0)."""
    pf = (arbitration_id >> 16) & 0xFF
    ps = (arbitration_id >> 8) & 0xFF
    sa = arbitration_id & 0xFF
    if pf < 0xF0:  # PDU1: PS is the destination address
        return pf << 8, ps, sa
    return (pf << 8) | ps, 0xFF, sa


def _request_frame(pgn: int, target: int, source: int, channel_id: str) -> CanFrame:
    data = bytes((pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF)) + b"\xff" * 5
    return CanFrame.create(channel_id=channel_id, arbitration_id=0x18EA0000 | (target << 8) | source,
                           data=data, is_extended=True, direction="tx")


async def read_j1939_snapshot(
    tx_port: Any,
    subscribe: Subscribe,
    *,
    channel_id: str,
    spns: Iterable[int] = (),
    target: int = ENGINE_SA,
    source: int = TOOL_SA,
    timeout_s: float = 1.25,
) -> ObdReadOutcome:
    """DM4 from ``target``, then DM30 for ``spns`` plus the freeze frame's SPN. Never raises."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[CanFrame] = asyncio.Queue()
    tp = J1939TransportProtocol(my_address=source, channel_id=channel_id)

    def deliver(frame: CanFrame) -> None:
        if frame.is_extended:
            loop.call_soon_threadsafe(queue.put_nowait, frame)

    async def exchange(frame: CanFrame, want_pgn: int, asked_pgn: int) -> bytes | None:
        while not queue.empty():
            queue.get_nowait()  # stale traffic from before this request
        await tx_port.send(frame)
        deadline = loop.time() + timeout_s
        while (remaining := deadline - loop.time()) > 0:
            try:
                rx = await asyncio.wait_for(queue.get(), remaining)
            except TimeoutError:
                break
            pgn, da, sa = _ids(rx.arbitration_id)
            if sa != target or da not in (source, 0xFF):
                continue
            data = bytes(rx.data)
            if pgn == want_pgn:
                return data
            if pgn == PGN_ACK and len(data) >= 8 and data[0] in (1, 2, 3) \
                    and int.from_bytes(data[5:8], "little") in (want_pgn, asked_pgn):
                return None  # the ECU says it does not support / will not answer this
            if pgn in (PGN_TP_CM, PGN_TP_DT):
                completed, resp = tp.handle_rx_frame(rx)
                for out in ([resp] if resp is not None else []) + tp.take_pending_tx_frames():
                    await tx_port.send(out, inbound_triggered=True)
                if completed is not None and completed.pgn == want_pgn and completed.source_address == target:
                    return bytes(completed.data)
        return None

    unsubscribe = subscribe(deliver)
    outcome = ObdReadOutcome(status="no_answer")
    answered = False
    try:
        dm4 = await exchange(_request_frame(PGN_DM4, target, source, channel_id), PGN_DM4, PGN_DM4)
        frames = decode_dm4(dm4) if dm4 is not None else []
        answered = dm4 is not None
        if frames:
            ff = frames[0]
            outcome.freeze_frame = {"dtc": ff.dtc, "readings": dict(ff.readings), "ecu": ENGINE_NAME_TR,
                                    "protocol": "J1939 DM4"}
        wanted: list[int] = []
        for spn in [*spns, *(f.spn for f in frames)]:
            if isinstance(spn, int) and 0 < spn <= 0x7FFFF and spn not in wanted:
                wanted.append(spn)
        for spn in wanted[:MAX_DM30_SPNS]:
            request = CanFrame.create(channel_id=channel_id, arbitration_id=dm7_arbitration_id(target, source),
                                      data=dm7_report_payload(spn), is_extended=True, direction="tx")
            dm30 = await exchange(request, PGN_DM30, PGN_DM7)
            if dm30 is None:
                continue
            answered = True
            outcome.monitors += [t.as_dict() for t in decode_dm30(dm30)]
    except SafetyError as exc:
        logger.error("J1939 read request refused by the TX gateway", extra={"code": getattr(exc, "code", "")})
        outcome.status = "refused"
        return outcome
    except Exception as exc:  # noqa: BLE001 — a reader failure is a result, not a crash
        logger.error("J1939 read failed", extra={"error": type(exc).__name__})
        outcome.status = "error"
        return outcome
    finally:
        unsubscribe()
    if answered:
        outcome.answered_ecus.append(ENGINE_NAME_TR)
    outcome.status = "ok" if answered else "no_answer"
    return outcome


def spns_from_codes(codes: Iterable[str]) -> list[int]:
    """SPN numbers from session event codes such as ``"SPN 3251 FMI 0"``."""
    out: list[int] = []
    for code in codes:
        parts = str(code).split()
        if len(parts) >= 2 and parts[0] == "SPN" and parts[1].isdigit() and int(parts[1]) not in out:
            out.append(int(parts[1]))
    return out


class SimulatedJ1939Ecu:
    """Hardware-free truck engine ECU (source address 0x00) answering DM4 and DM7/DM30.

    Every frame is checked against a ``ReadOnlyPolicy(j1939=True)`` exactly like
    the real gateway would; a violation raises ``SafetyError``.
    """

    # DM4: one freeze frame for SPN 3251 FMI 0 (DPF differential pressure high, the
    # simulated truck's DM1 code): 160 kPa boost, 1450 rpm, 82 % load, 88 °C coolant, 64 km/h.
    FREEZE_FRAME: bytes = bytes([
        12, 0xB3, 0x0C, 0x00, 0x02,       # length, SPN 3251 FMI 0, OC 2
        0x00, 80, 0x50, 0x2D, 82, 128,    # torque mode, boost /2 kPa, rpm /0.125, load %, coolant +40
        0x00, 0x40,                       # vehicle speed /(1/256) km/h
    ])
    # DM30 for SPN 3251: test 10 fails (raw 520 against a maximum of 400, no minimum);
    # test 11 passes (raw 35 between 20 and 60).
    DM30_TESTS: dict[int, bytes] = {
        3251: bytes([10, 0xB3, 0x0C, 0x00, 0x01, 0x00, 0x08, 0x02, 0x90, 0x01, 0xFF, 0xFF])
        + bytes([11, 0xB3, 0x0C, 0x01, 0x01, 0x00, 0x23, 0x00, 0x3C, 0x00, 0x14, 0x00]),
    }

    def __init__(self, *, policy_ttl_s: float = 60.0) -> None:
        self._policy = ReadOnlyPolicy(expires_ns=time.monotonic_ns() + int(policy_ttl_s * 1e9),
                                      reason="simulator", j1939=True)
        self._listeners: list[Callable[[CanFrame], None]] = []
        self._outgoing: dict[int, list[bytes]] = {}  # destination -> DT packets waiting for CTS
        self.sent: list[CanFrame] = []

    def subscribe(self, callback: Callable[[CanFrame], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _emit(self, arbitration_id: int, data: bytes) -> None:
        frame = CanFrame(channel_id="sim_j1939", arbitration_id=arbitration_id, dlc=8,
                         data=data.ljust(8, b"\xff"), is_extended=True, source="synthetic")
        for listener in list(self._listeners):
            listener(frame)

    def _nack(self, pgn: int, requester: int) -> None:
        self._emit(0x18E8FF00 | ENGINE_SA,
                   bytes([0x01, 0xFF, 0xFF, 0xFF, requester, pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF]))

    def _respond(self, pgn: int, payload: bytes, dest: int) -> None:
        if len(payload) <= 8:
            pdu = ((pgn | dest) if (pgn >> 8) & 0xFF < 0xF0 else pgn)
            self._emit(0x18000000 | (pdu << 8) | ENGINE_SA, payload)
            return
        chunks = [bytes([seq + 1]) + payload[seq * 7:seq * 7 + 7] for seq in range((len(payload) + 6) // 7)]
        self._outgoing[dest] = chunks
        self._emit(0x1CEC0000 | (dest << 8) | ENGINE_SA,
                   bytes([0x10, len(payload) & 0xFF, len(payload) >> 8, len(chunks), 0xFF,
                          pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF]))

    async def send(self, frame: CanFrame, **_: Any) -> None:
        self.send_sync(frame)

    def send_sync(self, frame: CanFrame, **_: Any) -> None:
        violation = self._policy.violation(frame, time.monotonic_ns())
        if violation is not None:
            raise SafetyError("simulated gateway refused a non read-only frame", code="READ_ONLY_VIOLATION",
                              details={"violation": violation})
        self.sent.append(frame)
        pgn, da, sa = _ids(frame.arbitration_id)
        data = bytes(frame.data)
        if da not in (ENGINE_SA, 0xFF):
            return  # only the engine ECU exists in the simulated truck
        if pgn == 0xEA00:
            requested = int.from_bytes(data[:3], "little")
            if requested == PGN_DM4:
                self._respond(PGN_DM4, self.FREEZE_FRAME, sa)
            elif da == ENGINE_SA:
                self._nack(requested, sa)
        elif pgn == 0xE300:
            spn = data[1] | (data[2] << 8) | ((data[3] & 0xE0) << 11)
            tests = self.DM30_TESTS.get(spn)
            if tests is None:
                self._nack(PGN_DM7, sa)
            else:
                self._respond(PGN_DM30, tests, sa)
        elif pgn == PGN_TP_CM:
            control = data[0]
            if control == 0x11:  # CTS: send the granted packets
                count, first = data[1], data[2]
                for chunk in self._outgoing.get(sa, [])[first - 1:first - 1 + count]:
                    self._emit(0x1CEB0000 | (sa << 8) | ENGINE_SA, chunk)
            elif control in (0x13, 0xFF):  # end-of-message ACK / abort
                self._outgoing.pop(sa, None)

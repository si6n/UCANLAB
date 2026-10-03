"""Scan runner (Aşama 6, MECHANIC_FLOW.md §3.11): listen → (consented read) → analyse → result.

The runner is the same for a real vehicle and the simulator; only the backend
differs. A backend provides four things:

* ``new_session()`` — a fresh evidence session for this scan;
* ``listen(session, seconds, cancelled, progress)`` — passive collection
  (J1939 DM1 broadcasts land in the session as events);
* ``read_obd()`` — the read-only OBD-II readout; called only for a car and
  only when the mechanic allowed reading;
* ``read_j1939(spns)`` (optional) — the read-only J1939 snapshot of a truck or
  machine (DM4 freeze frame, DM30 test results for the active codes' SPNs);
  called only when the mechanic allowed reading;
* ``analyze(session)`` — the existing analysis pipeline.

Repeated broadcasts of the same code are collapsed before analysis, so a
code seen 60 times in a minute counts once.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from src.core.logging import get_logger
from src.core.models.diagnostics import DiagnosticDomain, VehicleSession
from src.engine.diagnosis.events import dm1_to_events, obd_codes_to_events
from src.engine.diagnosis.mechanic_result import (
    CodeEvidence,
    ScanContext,
    compose_mechanic_result,
    customer_report_text,
    explain_code,
)
from src.engine.diagnosis.j1939_reader import SimulatedJ1939Ecu, read_j1939_snapshot, spns_from_codes
from src.engine.diagnosis.obd_reader import ObdReadOutcome, SimulatedObdEcu, read_obd_fault_codes

logger = get_logger("engine.diagnosis.scan")

DOMAIN_FOR_TYPE = {
    "car": DiagnosticDomain.PASSENGER,
    "truck": DiagnosticDomain.HEAVY_DUTY,
    "boat": DiagnosticDomain.MARINE,
    "construction": DiagnosticDomain.HEAVY_DUTY,
}


@dataclass(frozen=True)
class ScanRequest:
    vehicle_label: str
    vehicle_type: str
    high_voltage: bool = False
    simulator: bool = False
    allow_read: bool = False
    listen_seconds: float = 15.0
    battery_message_tr: str = ""
    scenario: str = "ok"


class ScanBackend(Protocol):
    def new_session(self, domain: DiagnosticDomain) -> VehicleSession: ...
    def listen(self, session: VehicleSession, seconds: float, cancelled: Callable[[], bool],
               progress: Callable[[float], None]) -> None: ...
    def read_obd(self) -> ObdReadOutcome: ...
    def analyze(self, session: VehicleSession) -> dict[str, Any]: ...


def _unique_session(session: VehicleSession) -> VehicleSession:
    """Copy with repeated (code, status) events collapsed; samples kept."""
    copy = VehicleSession(session_id=session.session_id, started_at_ns=session.started_at_ns, domain=session.domain,
                          make=session.make, model=session.model)
    seen: set[tuple[str, str]] = set()
    for event in list(session.events):
        key = (event.code, event.status)
        if key not in seen:
            seen.add(key)
            copy.events.append(event)
    copy.samples.extend(list(session.samples))
    return copy


class ScanRunner:
    def __init__(self, backend_factory: Callable[[ScanRequest], ScanBackend]) -> None:
        self._backend_factory = backend_factory
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"state": "idle"}
        self.last_result: dict[str, Any] | None = None
        # The last OBD read (codes, freeze frame, Mode 06 monitors) for the copilot.
        self.last_obd: ObdReadOutcome | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout_s: float = 60.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout_s)

    def _set(self, **changes: Any) -> None:
        with self._lock:
            self._state.update(changes)

    def start(self, request: ScanRequest) -> str:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("scan already running")
            scan_id = uuid.uuid4().hex[:12]
            self._cancel.clear()
            self.last_obd = None  # a new scan never reuses another vehicle's freeze frame
            self._state = {"state": "running", "scan_id": scan_id, "step": "listening", "progress": 0.0,
                           "result": None, "report_text": None}
            self._thread = threading.Thread(target=self._run, args=(request,), name="mechanic-scan", daemon=True)
            self._thread.start()
        return scan_id

    def _run(self, request: ScanRequest) -> None:
        try:
            backend = self._backend_factory(request)
            session = backend.new_session(DOMAIN_FOR_TYPE.get(request.vehicle_type, DiagnosticDomain.HEAVY_DUTY))
            backend.listen(session, request.listen_seconds, self._cancel.is_set,
                           lambda fraction: self._set(progress=round(min(1.0, fraction), 2)))
            if self._cancel.is_set():
                self._set(state="done", step="cancelled")
                return

            kinds: dict[str, tuple[str, str]] = {}
            read_status: str | None = None
            if request.vehicle_type == "car":
                if request.allow_read:
                    self._set(step="reading")
                    outcome = backend.read_obd()
                    self.last_obd = outcome
                    read_status = outcome.status
                    session.events.extend(obd_codes_to_events(outcome.codes, time.monotonic_ns()))
                    for code in outcome.codes:
                        kinds.setdefault(code.code, (code.kind, code.ecu_tr))
                else:
                    read_status = "declined"
            elif request.allow_read and request.vehicle_type in ("truck", "construction"):
                # DM1 already arrived while listening; the snapshot (DM4 freeze frame,
                # DM30 test results) is for the copilot and does not change the code list.
                read_j1939 = getattr(backend, "read_j1939", None)
                if read_j1939 is not None:
                    self._set(step="reading")
                    active = [e.code for e in list(session.events) if e.status == "ACTIVE"]
                    self.last_obd = read_j1939(spns_from_codes(active))

            self._set(step="analyzing")
            unique = _unique_session(session)
            analysis = backend.analyze(unique)
            evidence: list[CodeEvidence] = []
            for event in unique.events:
                if event.status not in ("ACTIVE", "PENDING"):
                    continue
                kind, ecu = kinds.get(event.code, ("active" if event.status == "ACTIVE" else "pending", ""))
                evidence.append(explain_code(event.code, kind, ecu))
            ctx = ScanContext(
                vehicle_label=request.vehicle_label, vehicle_type=request.vehicle_type,
                high_voltage=request.high_voltage, simulator=request.simulator,
                listen_only=not request.allow_read, read_requested=request.allow_read, read_status=read_status,
                passive_seconds=request.listen_seconds, battery_message_tr=request.battery_message_tr,
            )
            result = compose_mechanic_result(analysis, evidence, ctx)
            self.last_result = result
            self._set(state="done", step="done", progress=1.0, result=result,
                      report_text=customer_report_text(result))
            logger.info("Scan finished", extra={"codes": len(evidence), "risk": result["risk_level"],
                                                "read_status": read_status, "simulator": request.simulator})
        except Exception as exc:  # noqa: BLE001 — report, never crash the app
            logger.error("Scan failed", extra={"error": type(exc).__name__})
            self._set(state="done", step="failed", result=None)


class SimulatorScanBackend:
    """Hardware-free scan: simulated broadcasts + simulated OBD-II ECU."""

    def __init__(self, vehicle_type: str, scenario: str, analyze: Callable[[VehicleSession], dict[str, Any]],
                 *, frame_sleep: bool = True) -> None:
        self.vehicle_type = vehicle_type
        self.scenario = scenario
        self._analyze = analyze
        self._sleep = frame_sleep

    def new_session(self, domain: DiagnosticDomain) -> VehicleSession:
        return VehicleSession(session_id=f"sim-{uuid.uuid4().hex[:8]}", started_at_ns=time.monotonic_ns(),
                              domain=domain)

    def listen(self, session: VehicleSession, seconds: float, cancelled: Callable[[], bool],
               progress: Callable[[float], None]) -> None:
        from src.engine.connection.simulated_vehicle import NATIVE_BITRATE, SimulatedVehicleBus
        from src.protocols.j1939.diagnostics import J1939DiagnosticService

        bitrate = NATIVE_BITRATE.get(self.vehicle_type, 250_000)
        bus = SimulatedVehicleBus(self.vehicle_type, bitrate, self.scenario, sleep=self._sleep)
        bus.connect()
        start = time.monotonic()
        try:
            while not cancelled() and (elapsed := time.monotonic() - start) < seconds:
                progress(elapsed / seconds if seconds else 1.0)
                frame = bus.recv(timeout_s=0.05)
                if frame is None or not frame.is_extended:
                    continue
                pgn = (frame.arbitration_id >> 8) & 0x3FFFF
                if pgn == 65226:
                    dm = J1939DiagnosticService.parse_dm1_or_dm2(bytes(frame.data), pgn=65226,
                                                                 source_address=frame.arbitration_id & 0xFF)
                    if dm is not None:
                        session.events.extend(dm1_to_events(dm.dtcs, time.monotonic_ns()))
        finally:
            bus.disconnect()
        progress(1.0)

    def read_obd(self) -> ObdReadOutcome:
        if self.scenario == "ignition_off":
            return ObdReadOutcome(status="no_answer")  # an ECU that is off never answers
        ecu = SimulatedObdEcu()
        return asyncio.run(read_obd_fault_codes(ecu, ecu.subscribe, channel_id="sim_obd", timeout_s=0.3))

    def read_j1939(self, spns: list[int]) -> ObdReadOutcome:
        if self.scenario == "ignition_off":
            return ObdReadOutcome(status="no_answer")
        ecu = SimulatedJ1939Ecu()
        return asyncio.run(read_j1939_snapshot(ecu, ecu.subscribe, channel_id="sim_j1939", spns=spns,
                                               timeout_s=0.3))

    def analyze(self, session: VehicleSession) -> dict[str, Any]:
        return self._analyze(session)


class LiveScanBackend:
    """Real vehicle: the app's telemetry loop fills the session while we wait."""

    def __init__(self, new_session: Callable[[DiagnosticDomain], VehicleSession],
                 read_obd: Callable[[], ObdReadOutcome],
                 analyze: Callable[[VehicleSession], dict[str, Any]], *, tick_s: float = 0.2,
                 read_j1939: Callable[[list[int]], ObdReadOutcome] | None = None) -> None:
        self._new_session = new_session
        self._read_obd = read_obd
        self._read_j1939 = read_j1939
        self._analyze = analyze
        self._tick = tick_s

    def new_session(self, domain: DiagnosticDomain) -> VehicleSession:
        return self._new_session(domain)

    def listen(self, session: VehicleSession, seconds: float, cancelled: Callable[[], bool],
               progress: Callable[[float], None]) -> None:
        start = time.monotonic()
        while not cancelled() and (elapsed := time.monotonic() - start) < seconds:
            progress(elapsed / seconds if seconds else 1.0)
            time.sleep(self._tick)
        progress(1.0)

    def read_obd(self) -> ObdReadOutcome:
        return self._read_obd()

    def read_j1939(self, spns: list[int]) -> ObdReadOutcome:
        if self._read_j1939 is None:
            return ObdReadOutcome(status="no_answer")
        return self._read_j1939(spns)

    def analyze(self, session: VehicleSession) -> dict[str, Any]:
        return self._analyze(session)

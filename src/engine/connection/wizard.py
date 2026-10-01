"""Connection wizard runner (Aşama 5): runs one listen-only test in the background.

The UI polls ``status()``; ``start()`` returns immediately. For a real adapter
the app's own bus is first *parked* on a virtual channel (so the test can open
the physical channel exclusively), and after a usable result the app bus is
*committed* to the adapter at the bitrate the test found — still listen-only,
with TX disarmed by the reconnect path. For the simulator nothing of the app
bus is touched.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.core.logging import get_logger
from src.engine.connection.adapters import AdapterInfo
from src.engine.connection.listen_test import (
    BusListenChannel,
    ExpectedPgn,
    ListenChannel,
    ListenTestResult,
    run_listen_test,
)
from src.engine.connection.simulated_vehicle import SimulatedVehicleBus
from src.engine.vehicle.profiles import VehicleType
from src.hal.base import AbstractBus

logger = get_logger("engine.connection.wizard")

BusFactory = Callable[[str, str, int], AbstractBus]  # (interface, channel, bitrate) -> listen-only bus


@dataclass(frozen=True)
class CommittedConnection:
    adapter: AdapterInfo
    vehicle_type: str
    bitrate: int | None
    simulator: bool
    scenario: str | None
    result: dict[str, Any]


class ConnectionWizard:
    def __init__(
        self,
        bus_factory: BusFactory,
        *,
        park_app_bus: Callable[[], None] = lambda: None,
        commit_app_bus: Callable[[AdapterInfo, int], None] = lambda adapter, bitrate: None,
        window_s: float = 2.0,
        simulator_sleep: bool = True,
    ) -> None:
        self._bus_factory = bus_factory
        self._park = park_app_bus
        self._commit = commit_app_bus
        self._window_s = window_s
        self._sim_sleep = simulator_sleep
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"state": "idle"}
        self.connection: CommittedConnection | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def cancel(self) -> None:
        self._cancel.set()

    def _set(self, **changes: Any) -> None:
        with self._lock:
            self._state.update(changes)

    def start(self, adapter: AdapterInfo, vehicle_type: VehicleType, *, scenario: str | None = None) -> str:
        """Start a test; raises RuntimeError if one is already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("connection test already running")
            test_id = uuid.uuid4().hex[:12]
            self._cancel.clear()
            self._state = {"state": "running", "test_id": test_id, "step": "opening", "bitrate": None,
                           "adapter": adapter.as_dict(), "vehicle_type": vehicle_type.id, "result": None}
            self._thread = threading.Thread(
                target=self._run, args=(test_id, adapter, vehicle_type, scenario),
                name="connection-wizard", daemon=True,
            )
            self._thread.start()
        return test_id

    def wait(self, timeout_s: float = 30.0) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout_s)

    def _channel(self, adapter: AdapterInfo, vehicle_type: str, scenario: str | None) -> ListenChannel:
        if adapter.kind == "simulator":
            return BusListenChannel(
                lambda bitrate: SimulatedVehicleBus(vehicle_type, bitrate, scenario or "ok", sleep=self._sim_sleep)
            )
        return BusListenChannel(lambda bitrate: self._bus_factory(adapter.interface, adapter.channel, bitrate))

    def _run(self, test_id: str, adapter: AdapterInfo, vtype: VehicleType, scenario: str | None) -> None:
        simulator = adapter.kind == "simulator"
        started = time.monotonic()
        result: ListenTestResult | None = None
        try:
            if not simulator:
                self._park()
            expected = [ExpectedPgn(e.pgn, e.name_tr, e.name_en) for e in vtype.expected_traffic]
            result = run_listen_test(
                self._channel(adapter, vtype.id, scenario), vtype.bitrate_candidates, expected,
                window_s=self._window_s, cancelled=self._cancel.is_set,
                on_progress=lambda step, bitrate: self._set(step=step, bitrate=bitrate),
            )
            payload = result.as_dict()
            if result.usable and result.bitrate is not None:
                if not simulator:
                    self._commit(adapter, result.bitrate)
                self.connection = CommittedConnection(adapter=adapter, vehicle_type=vtype.id, bitrate=result.bitrate,
                                                      simulator=simulator, scenario=scenario, result=payload)
            logger.info("Connection test finished", extra={
                "code": result.code, "adapter": adapter.kind, "bitrate": result.bitrate,
                "ecus": result.ecu_count, "duration_s": round(time.monotonic() - started, 2),
            })
            self._set(state="done", step="done", result=payload)
        except Exception as exc:  # noqa: BLE001 — the wizard must report, never crash the app
            logger.error("Connection test failed unexpectedly", extra={"error": type(exc).__name__})
            fallback = ListenTestResult(code="ADAPTER_ERROR").as_dict()
            self._set(state="done", step="done", result=fallback)

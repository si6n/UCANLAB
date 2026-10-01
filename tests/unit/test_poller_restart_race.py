"""ActiveDiagnosticPoller.stop() from a foreign thread must survive the loop closing.

``stop()`` scheduled two callbacks on the worker loop: first
``stop_event.set``, then ``task.cancel``. The first one ends
``run_until_complete`` and the worker closes the loop — so on a loaded
machine the loop could already be closed when the second call ran:
``RuntimeError: Event loop is closed`` (seen under ``pytest -n 4``).
"""

from __future__ import annotations

import threading
import time

from src.core.contracts.ports import InMemoryTxPort
from src.protocols.obd.poller import ActiveDiagnosticPoller


def _poller_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "active_diag_poller"]


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_stop_survives_the_loop_closing_between_scheduled_callbacks() -> None:
    poller = ActiveDiagnosticPoller(tx_port=InMemoryTxPort())
    poller.start()
    assert _wait(lambda: poller._loop_task is not None and poller._loop_task.get_loop().is_running())
    loop = poller._loop_task.get_loop()
    original = loop.call_soon_threadsafe
    calls = {"n": 0}

    def call_then_let_the_worker_finish(callback, *args, **kwargs):
        handle = original(callback, *args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            # Deterministic version of the race: the worker processes the
            # first callback and closes its loop before stop() continues.
            _wait(loop.is_closed, timeout=2.0)
        return handle

    loop.call_soon_threadsafe = call_then_let_the_worker_finish  # type: ignore[method-assign]
    poller.stop()  # used to raise RuntimeError: Event loop is closed
    assert poller.is_running is False
    assert _wait(lambda: _poller_threads() == [])


def test_rapid_cycles_leave_no_thread_behind() -> None:
    poller = ActiveDiagnosticPoller(tx_port=InMemoryTxPort())
    for _ in range(100):
        poller.start()
        poller.stop()
        assert poller.is_running is False
    assert _wait(lambda: _poller_threads() == [])

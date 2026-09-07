"""High-Performance Multiplexed CAN Frame Router and Dispatcher.

Implements the Observer / Pub-Sub pattern to distribute incoming CAN frames
to multiple protocol engines (UDS, J1939, NMEA2000, DBC decoders, and UI)
without single-consumer starvation or frame stealing.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame

logger = get_logger("engine.router")


@dataclass(slots=True)
class Subscription:
    """Active subscriber registration handle."""

    sub_id: int
    callback: Callable[[CanFrame], None] | None = None
    frame_queue: queue.Queue[CanFrame] | None = None
    filter_ids: set[int] | None = None  # None = accept all arbitration IDs
    channel_id: str | None = None  # None = accept all channels
    is_demoted: bool = False


class FrameRouter:
    """Centralized thread-safe message router and dispatcher for CAN bus streams."""

    MAX_QUEUE_SIZE: ClassVar[int] = 10_000

    # M-7 (3FABLE): a synchronous callback running longer than this budget
    # is demoted — a WARN is emitted and the subscriber is moved to
    # queue-only dispatch so one slow consumer (UI, AI copilot) can never
    # HOL-block the RX thread and trip protocol timers (N_Cr, T1).
    CALLBACK_BUDGET_MS: ClassVar[float] = 20.0
    # E-2: rate-limited drop logging — one summary per subscriber per second.
    _DROP_LOG_INTERVAL_S: ClassVar[float] = 1.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscriptions: dict[int, Subscription] = {}
        # Perf (C-8): immutable routing snapshot — route_frame() fans frames
        # out over this tuple WITHOUT taking the lock or copying the dict.
        # Mutators (subscribe/unsubscribe/restore/clear) rebuild the snapshot
        # under the lock (copy-on-write). Subscriptions change at setup/teardown
        # rates, frames at bus rates — the trade favors the hot path.
        self._route_snapshot: tuple[Subscription, ...] = ()
        self._next_sub_id: int = 1
        self._total_routed: int = 0
        self._total_dropped: int = 0
        # E-2: (sub_id, monotonic-second) of the last drop log per subscriber
        self._last_drop_log: dict[int, float] = {}
        self._drop_counts_since_log: dict[int, int] = {}

    def _rebuild_snapshot_locked(self) -> None:
        """Refresh the lock-free routing snapshot. Caller must hold the lock."""
        self._route_snapshot = tuple(self._subscriptions.values())

    def subscribe(
        self,
        callback: Callable[[CanFrame], None] | None = None,
        filter_ids: set[int] | None = None,
        channel_id: str | None = None,
        use_queue: bool = False,
        queue_maxsize: int = MAX_QUEUE_SIZE,
    ) -> tuple[int, queue.Queue[CanFrame] | None]:
        """Register a new consumer subscription.

        Returns (subscription_id, queue_instance_or_None).
        """
        fq: queue.Queue[CanFrame] | None = None
        if use_queue:
            fq = queue.Queue(maxsize=queue_maxsize)

        with self._lock:
            sub_id = self._next_sub_id
            self._next_sub_id += 1
            sub = Subscription(
                sub_id=sub_id,
                callback=callback,
                frame_queue=fq,
                filter_ids=set(filter_ids) if filter_ids is not None else None,
                channel_id=channel_id,
            )
            self._subscriptions[sub_id] = sub
            self._rebuild_snapshot_locked()

        return sub_id, fq

    def unsubscribe(self, sub_id: int) -> bool:
        """Remove an existing subscription by ID."""
        with self._lock:
            removed = self._subscriptions.pop(sub_id, None) is not None
            if removed:
                self._rebuild_snapshot_locked()
            return removed

    def route_frame(self, frame: CanFrame) -> int:
        """Dispatch an ingested frame to all matching subscribers.

        Returns the number of subscribers that accepted the frame.

        Perf (C-8): iterates the immutable copy-on-write snapshot — no lock
        acquisition, no per-frame list(values()) copy. In-flight frames may
        see a subscription removed microseconds earlier; that is the same
        linearization the old lock+copy provided, at a fraction of the cost.
        """
        subscribers = self._route_snapshot
        self._total_routed += 1

        matched_count = 0
        for sub in subscribers:
            # Check channel filter
            if sub.channel_id is not None and frame.channel_id != sub.channel_id:
                continue

            # Check arbitration ID filter
            if sub.filter_ids is not None and frame.arbitration_id not in sub.filter_ids:
                continue

            matched_count += 1

            # M-7: dispatch to callback with a time budget; overspending
            # subscribers are demoted to queue-only so they stop stalling
            # the RX thread.
            if sub.callback is not None:
                t0 = time.monotonic()
                try:
                    sub.callback(frame)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Error in FrameRouter subscriber callback",
                        extra={"sub_id": sub.sub_id, "error": str(exc)},
                    )
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                if elapsed_ms > self.CALLBACK_BUDGET_MS:
                    logger.warning(
                        "Slow FrameRouter callback exceeded time budget",
                        extra={
                            "sub_id": sub.sub_id,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "budget_ms": self.CALLBACK_BUDGET_MS,
                        },
                    )
                    # Only demote to queue-only if the subscriber actually has a queue to read from.
                    # Setting callback=None on a callback-only subscriber leaves it completely deaf.
                    if sub.frame_queue is not None:
                        with self._lock:
                            if sub.sub_id in self._subscriptions:
                                self._subscriptions[sub.sub_id].callback = None
                                self._subscriptions[sub.sub_id].is_demoted = True

            # Dispatch to queue (non-blocking with drop on full)
            if sub.frame_queue is not None:
                try:
                    sub.frame_queue.put_nowait(frame)
                except queue.Full:
                    with self._lock:
                        self._total_dropped += 1
                        self._drop_counts_since_log[sub.sub_id] = (
                            self._drop_counts_since_log.get(sub.sub_id, 0) + 1
                        )
                        now = time.monotonic()
                        should_log = (now - self._last_drop_log.get(sub.sub_id, 0.0)) >= self._DROP_LOG_INTERVAL_S
                        if should_log:
                            burst = self._drop_counts_since_log.pop(sub.sub_id, 0)
                            self._last_drop_log[sub.sub_id] = now
                    # E-2: log at most one summary per subscriber per second —
                    # per-frame WARNINGs at bus rate flooded the log disk I/O
                    # and slowed the RX thread further (positive feedback).
                    if should_log:
                        logger.warning(
                            "FrameRouter subscriber queue full; frames dropped",
                            extra={
                                "sub_id": sub.sub_id,
                                "dropped_in_window": burst,
                                "arbitration_id_last": hex(frame.arbitration_id),
                            },
                        )

        return matched_count

    def restore_callback(
        self, sub_id: int, callback: Callable[[CanFrame], None]
    ) -> bool:
        """Restore or reassign callback on an existing (or demoted) subscription (MED-4)."""
        with self._lock:
            sub = self._subscriptions.get(sub_id)
            if sub is None:
                return False
            sub.callback = callback
            sub.is_demoted = False
            return True

    def clear(self) -> None:
        """Remove all active subscriptions."""
        with self._lock:
            self._subscriptions.clear()
            self._rebuild_snapshot_locked()

    @property
    def subscription_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "active_subscriptions": len(self._subscriptions),
                "total_routed": self._total_routed,
                "total_dropped": self._total_dropped,
            }

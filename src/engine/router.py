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
from dataclasses import dataclass, replace
from typing import ClassVar

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame

logger = get_logger("engine.router")


@dataclass(slots=True, frozen=True)
class Subscription:
    """Active subscriber registration handle.

    RT-2: FROZEN. The router publishes subscriptions through an immutable
    copy-on-write snapshot (``_route_snapshot``); demote/restore/clear replace
    the whole object via ``dataclasses.replace`` instead of mutating it in
    place, so an in-flight ``route_frame`` iteration always sees a consistent
    (never half-updated) subscription.
    """

    sub_id: int
    callback: Callable[[CanFrame], None] | None = None
    frame_queue: queue.Queue[CanFrame] | None = None
    filter_ids: frozenset[int] | None = None  # None = accept all arbitration IDs
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
    # REVIEW2 #2 / REVIEW3 #13: a callback-ONLY subscriber (no queue to be
    # demoted onto) that keeps blowing the budget gets its callback REMOVED
    # after this many consecutive violations — "deaf" beats "holds the whole
    # RX thread hostage on every frame" (protocol timers miss otherwise).
    CALLBACK_TRIP_AFTER: ClassVar[int] = 10

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscriptions: dict[int, Subscription] = {}
        # Perf (C-8): immutable routing snapshot — route_frame() fans frames
        # out over this tuple WITHOUT taking the lock or copying the dict.
        # Mutators (subscribe/unsubscribe/restore/demote/clear) rebuild the
        # snapshot under the lock (copy-on-write). RT-2: the objects in it are
        # frozen, so an in-flight iteration can never observe a half-mutated
        # subscription. Subscriptions change at setup/teardown rates, frames at
        # bus rates — the trade favors the hot path.
        self._route_snapshot: tuple[Subscription, ...] = ()
        self._next_sub_id: int = 1
        # L-16 (P3-11): dedicated counter lock. The counters were mutated
        # without any lock; multiple ingest threads racing `+=` lost
        # increments (metrics only — never frame delivery, which iterates
        # the immutable copy-on-write snapshot and needs no lock).
        self._stats_lock = threading.Lock()
        self._total_routed: int = 0
        self._total_dropped: int = 0
        # E-2: (sub_id, monotonic-second) of the last drop log per subscriber
        self._last_drop_log: dict[int, float] = {}
        self._drop_counts_since_log: dict[int, int] = {}
        # REVIEW2 #2: consecutive CALLBACK_BUDGET_MS violations per subscriber
        # (callback-only subscribers get tripped; queue subscribers get demoted).
        self._callback_trip_counts: dict[int, int] = {}

    def _rebuild_snapshot_locked(self) -> None:
        """Refresh the lock-free routing snapshot. Caller must hold the lock."""
        self._route_snapshot = tuple(self._subscriptions.values())

    def _replace_subscription_locked(self, sub: Subscription, **changes: object) -> None:
        """RT-2: copy-on-write subscription update. Caller must hold the lock.

        ``Subscription`` objects are frozen: the routing snapshot is claimed
        immutable, so demote/restore/callback-removal must NOT mutate the
        instances the snapshot already published. A ``dataclasses.replace``
        copy is installed in ``_subscriptions`` and the snapshot is rebuilt —
        in-flight ``route_frame`` iterations keep the consistent old object.
        """
        self._subscriptions[sub.sub_id] = replace(sub, **changes)  # type: ignore[arg-type]
        self._rebuild_snapshot_locked()

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
        if (
            not isinstance(queue_maxsize, int)
            or isinstance(queue_maxsize, bool)
            or not (1 <= queue_maxsize <= self.MAX_QUEUE_SIZE)
        ):
            raise ValueError(
                f"queue_maxsize must be in range 1..{self.MAX_QUEUE_SIZE}, got {queue_maxsize!r}"
            )
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
                filter_ids=frozenset(filter_ids) if filter_ids is not None else None,
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
                # REVIEW2 #2: drop per-subscriber bookkeeping with the sub.
                self._callback_trip_counts.pop(sub_id, None)
                self._drop_counts_since_log.pop(sub_id, None)
                self._last_drop_log.pop(sub_id, None)
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
        with self._stats_lock:  # RT-1: routed uses the same lock as dropped
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
                            current = self._subscriptions.get(sub.sub_id)
                            if current is not None:
                                # RT-2: copy-on-write replacement (frozen sub).
                                self._replace_subscription_locked(
                                    current, callback=None, is_demoted=True
                                )
                    else:
                        # REVIEW2 #2 / REVIEW3 #13: a queue-less subscriber
                        # cannot be demoted, but it also must not keep
                        # stalling every routed frame forever. After
                        # CALLBACK_TRIP_AFTER consecutive over-budget calls
                        # the callback is removed (subscriber goes deaf,
                        # loudly logged) — restore_callback() can re-arm it.
                        with self._lock:
                            trips = self._callback_trip_counts.get(sub.sub_id, 0) + 1
                            self._callback_trip_counts[sub.sub_id] = trips
                            current = self._subscriptions.get(sub.sub_id)
                            if trips >= self.CALLBACK_TRIP_AFTER and current is not None:
                                # RT-2: copy-on-write replacement (frozen sub).
                                self._replace_subscription_locked(
                                    current, callback=None, is_demoted=True
                                )
                                logger.error(
                                    "FrameRouter callback-only subscriber tripped after repeated budget violations — "
                                    "callback removed (restore via restore_callback)",
                                    extra={"sub_id": sub.sub_id, "consecutive_violations": trips},
                                )
                else:
                    # In-budget call resets the consecutive-violation counter.
                    if self._callback_trip_counts:
                        with self._lock:
                            self._callback_trip_counts.pop(sub.sub_id, None)

            # Dispatch to queue (non-blocking with drop on full)
            if sub.frame_queue is not None:
                try:
                    sub.frame_queue.put_nowait(frame)
                except queue.Full:
                    # RT-1: dropped/total_routed share _stats_lock so stats()
                    # can never observe a torn pair (dropped > routed).
                    with self._stats_lock:
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
            # RT-2: copy-on-write replacement (frozen sub) — rebuild the
            # snapshot so route_frame picks up the re-armed callback.
            self._replace_subscription_locked(sub, callback=callback, is_demoted=False)
            # REVIEW2 #2: a fresh operator-armed callback starts with a
            # clean trip count (it may genuinely be faster now).
            self._callback_trip_counts.pop(sub_id, None)
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
        # RT-1: both counters are mutated/read under the SAME _stats_lock, so
        # the snapshot is internally consistent (no torn routed/dropped pair).
        # Lock order stays _lock -> _stats_lock, matching route_frame.
        with self._stats_lock:
            return {
                "active_subscriptions": len(self._route_snapshot),
                "total_routed": self._total_routed,
                "total_dropped": self._total_dropped,
            }

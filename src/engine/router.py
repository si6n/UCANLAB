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


class _PendingCounter:
    """RT-3: one thread's unflushed routed/dropped deltas.

    Accumulated lock-free by the owning thread so ``route_frame`` never
    contends on a shared mutex. Registered in ``FrameRouter._pending_counters``
    so ``stats``/``flush_pending_counters`` can drain the sub-threshold
    remainder from other threads and keep the reported totals exact.
    """

    __slots__ = ("routed", "dropped")

    def __init__(self) -> None:
        self.routed = 0
        self.dropped = 0


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

    # RT-3 (FAZ 1 / review #6): frames a single thread accumulates in its
    # thread-local delta before paying one `_stats_lock` acquisition to publish
    # them. Bounds metric staleness to this many frames while removing the
    # per-frame global lock from the hot path.
    FLUSH_THRESHOLD: ClassVar[int] = 256

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
        # RT-3 (FAZ 1 / review #6): per-thread pending deltas. `route_frame`
        # used to take the global `_stats_lock` on EVERY frame just to bump one
        # integer, serializing every RX ingest thread on a single mutex. Each
        # thread now accumulates into its own thread-local delta and only
        # touches the global lock on a flush (every FLUSH_THRESHOLD frames) —
        # turning the hot path into a per-thread, lock-free increment.
        # `_pending_counters` holds a reference to every thread's delta so
        # `stats` (and `flush_pending_counters`) can drain the not-yet-flushed
        # remainder and stay EXACTLY consistent instead of "eventually
        # consistent". A strong reference (not a WeakSet) is required: a
        # finished thread's `threading.local()` storage is reclaimed with the
        # thread, and a weak registry would silently drop its final,
        # sub-threshold delta (observed as an exact-frames loss on join).
        # Entries are removed by `_drain_pending_locked` once a delta reaches
        # zero, so the registry stays bounded by the number of *live* producers.
        self._tls = threading.local()
        self._pending_counters: set[_PendingCounter] = set()
        # E-2: (sub_id, monotonic-second) of the last drop log per subscriber
        self._last_drop_log: dict[int, float] = {}
        self._drop_counts_since_log: dict[int, int] = {}
        # REVIEW2 #2: consecutive CALLBACK_BUDGET_MS violations per subscriber
        # (callback-only subscribers get tripped; queue subscribers get demoted).
        self._callback_trip_counts: dict[int, int] = {}

    def _rebuild_snapshot_locked(self) -> None:
        """Refresh the lock-free routing snapshot. Caller must hold the lock."""
        self._route_snapshot = tuple(self._subscriptions.values())

    def _pending(self) -> _PendingCounter:
        """RT-3: this thread's pending-delta object (created on first use).

        The object is (re-)registered in ``_pending_counters`` on every call.
        Registration must be idempotent rather than one-shot: ``stats`` prunes
        drained deltas from the registry, but the owning thread keeps its
        cached object in thread-local storage and would otherwise accumulate
        into an *unregistered* delta that no future drain could observe.
        """
        counter = getattr(self._tls, "counter", None)
        if counter is None:
            counter = _PendingCounter()
            self._tls.counter = counter
        # A `set.add` of an already-present item is a no-op, so this is cheap
        # and safe to run on the hot path (no lock needed: the set is only
        # mutated here by the owning thread and read under `_stats_lock` by
        # `stats`, and `set.add` of a hashable object is GIL-atomic).
        self._pending_counters.add(counter)
        return counter

    def _flush_counter_locked(self, counter: _PendingCounter) -> None:
        """Publish one thread's pending deltas into the global counters.

        Caller MUST hold ``_stats_lock``.

        `stats` may drain a *different* thread's delta while that thread is
        still routing frames, so the read-and-reset of each field must be
        atomic with respect to that thread's `+= 1`. CPython guarantees the
        individual read and write are atomic, but a naive
        ``self._total += counter.routed; counter.routed = 0`` loses an
        increment that lands between the read and the reset. Using
        ``counter.routed -= taken`` instead closes the window: the flush
        subtracts exactly what it published, so a concurrent ``+= 1`` between
        the read and the subtract survives (and is picked up by the next
        flush) rather than being zeroed.
        """
        routed = counter.routed
        if routed:
            self._total_routed += routed
            counter.routed -= routed
        dropped = counter.dropped
        if dropped:
            self._total_dropped += dropped
            counter.dropped -= dropped

    def _drain_pending_locked(self) -> None:
        """Flush every registered delta and prune the ones that are now empty.

        Caller MUST hold ``_stats_lock``. Pruning keeps the registry bounded by
        the number of threads that still have unflushed frames, so long-lived
        processes with churning producer threads do not accumulate entries.
        """
        empty: list[_PendingCounter] = []
        for counter in self._pending_counters:
            self._flush_counter_locked(counter)
            if not counter.routed and not counter.dropped:
                empty.append(counter)
        for counter in empty:
            self._pending_counters.discard(counter)

    def flush_pending_counters(self) -> None:
        """RT-3: publish every thread's not-yet-flushed deltas.

        Called on shutdown/teardown so a caller that inspects `stats` after the
        RX threads stopped still observes the exact totals.
        """
        with self._stats_lock:
            self._drain_pending_locked()

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
        # RT-3 (FAZ 1 / review #6): accumulate in the thread-local delta and
        # only take the global `_stats_lock` once per FLUSH_THRESHOLD frames.
        # The old per-frame `with self._stats_lock: self._total_routed += 1`
        # serialized every RX ingest thread on one mutex for a single-integer
        # bump. `stats` drains the pending remainder, so the reported totals
        # stay exact (not merely eventually consistent).
        pending = self._pending()
        pending.routed += 1
        if pending.routed >= self.FLUSH_THRESHOLD:
            with self._stats_lock:
                self._flush_counter_locked(pending)

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
                    # RT-1/RT-3: dropped/total_routed share the global counter
                    # pair so stats() can never observe a torn pair
                    # (dropped > routed). The per-subscriber bookkeeping below
                    # must still be mutated under `_stats_lock` (it is shared
                    # mutable state), but the global `_total_dropped` delta is
                    # accumulated thread-locally and flushed alongside the
                    # routed delta.
                    pending = self._pending()
                    pending.dropped += 1
                    with self._stats_lock:
                        if pending.dropped >= self.FLUSH_THRESHOLD:
                            self._flush_counter_locked(pending)
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
        # RT-1/RT-3: both counters are mutated/read under the SAME _stats_lock,
        # so the snapshot is internally consistent (no torn routed/dropped
        # pair). RT-3: every thread's pending delta is drained first so the
        # totals are EXACT even though the hot path accumulates lock-free.
        # Lock order stays _lock -> _stats_lock, matching route_frame.
        with self._stats_lock:
            self._drain_pending_locked()
            return {
                "active_subscriptions": len(self._route_snapshot),
                "total_routed": self._total_routed,
                "total_dropped": self._total_dropped,
            }

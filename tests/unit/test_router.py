"""Comprehensive unit and concurrency tests for FrameRouter.

Tests pub/sub callback registration, queue-based subscription with multi-consumer
distribution, arbitration ID and channel filtering, unsubscribe/clear mechanics,
queue overflow / drop tracking, and exception isolation.
"""

from __future__ import annotations

import queue
import threading
import time

from src.core.models.can_frame import CanFrame
from src.engine.router import FrameRouter


def _create_test_frame(
    channel_id: str = "can0",
    arbitration_id: int = 0x100,
    data: bytes = b"\x01\x02\x03\x04",
) -> CanFrame:
    """Helper to create valid CanFrame objects for testing."""
    return CanFrame.create(
        channel_id=channel_id,
        arbitration_id=arbitration_id,
        data=data,
    )


class TestFrameRouterBasics:
    """Basic initialization and stats tests."""

    def test_initial_state(self) -> None:
        router = FrameRouter()
        assert router.subscription_count == 0
        assert router.stats == {
            "active_subscriptions": 0,
            "total_routed": 0,
            "total_dropped": 0,
        }

    def test_route_with_no_subscribers(self) -> None:
        router = FrameRouter()
        frame = _create_test_frame()
        matched = router.route_frame(frame)
        assert matched == 0
        assert router.stats["total_routed"] == 1
        assert router.stats["total_dropped"] == 0


class TestCallbackPubSub:
    """Test callback registration, invocation, and multi-subscriber broadcast."""

    def test_single_callback_subscription_and_routing(self) -> None:
        router = FrameRouter()
        received_frames: list[CanFrame] = []

        sub_id, fq = router.subscribe(callback=received_frames.append)

        assert sub_id == 1
        assert fq is None
        assert router.subscription_count == 1

        frame = _create_test_frame(arbitration_id=0x123)
        matched = router.route_frame(frame)

        assert matched == 1
        assert len(received_frames) == 1
        assert received_frames[0] == frame
        assert router.stats["total_routed"] == 1

    def test_multi_subscriber_broadcasting(self) -> None:
        """Verify pub/sub broadcast without frame stealing or starvation."""
        router = FrameRouter()
        sub1_frames: list[CanFrame] = []
        sub2_frames: list[CanFrame] = []
        sub3_frames: list[CanFrame] = []

        id1, _ = router.subscribe(callback=sub1_frames.append)
        id2, _ = router.subscribe(callback=sub2_frames.append)
        id3, _ = router.subscribe(callback=sub3_frames.append)

        assert id1 != id2 and id2 != id3
        assert router.subscription_count == 3

        frame = _create_test_frame(arbitration_id=0x456)
        matched = router.route_frame(frame)

        assert matched == 3
        assert len(sub1_frames) == 1
        assert len(sub2_frames) == 1
        assert len(sub3_frames) == 1
        assert sub1_frames[0] == sub2_frames[0] == sub3_frames[0] == frame

    def test_callback_exception_isolation(self) -> None:
        """Verify an error in one callback does not prevent other callbacks from receiving frames."""
        router = FrameRouter()
        received_frames: list[CanFrame] = []

        def failing_callback(frame: CanFrame) -> None:
            raise RuntimeError("Intentional callback failure")

        router.subscribe(callback=failing_callback)
        router.subscribe(callback=received_frames.append)

        frame = _create_test_frame()
        matched = router.route_frame(frame)

        assert matched == 2
        assert len(received_frames) == 1
        assert received_frames[0] == frame


class TestQueueSubscriptions:
    """Test queue-based subscriptions and multi-consumer distribution."""

    def test_queue_subscription_creation(self) -> None:
        router = FrameRouter()
        sub_id, fq = router.subscribe(use_queue=True, queue_maxsize=50)

        assert sub_id == 1
        assert isinstance(fq, queue.Queue)
        assert fq.maxsize == 50

    def test_queue_multi_consumer_distribution(self) -> None:
        """Verify frames are delivered independently to multiple queue consumers."""
        router = FrameRouter()
        _, q1 = router.subscribe(use_queue=True)
        _, q2 = router.subscribe(use_queue=True)

        assert q1 is not None and q2 is not None

        frame1 = _create_test_frame(arbitration_id=0x101)
        frame2 = _create_test_frame(arbitration_id=0x102)

        router.route_frame(frame1)
        router.route_frame(frame2)

        assert q1.get_nowait() == frame1
        assert q1.get_nowait() == frame2
        assert q1.empty()

        assert q2.get_nowait() == frame1
        assert q2.get_nowait() == frame2
        assert q2.empty()

    def test_combined_callback_and_queue_subscription(self) -> None:
        """A subscriber can receive frames via both a callback and a queue."""
        router = FrameRouter()
        callback_frames: list[CanFrame] = []

        _, q = router.subscribe(
            callback=callback_frames.append,
            use_queue=True,
        )
        assert q is not None

        frame = _create_test_frame()
        matched = router.route_frame(frame)

        assert matched == 1
        assert len(callback_frames) == 1
        assert callback_frames[0] == frame
        assert q.get_nowait() == frame


class TestFiltering:
    """Test arbitration ID and channel filtering logic."""

    def test_arbitration_id_filtering(self) -> None:
        router = FrameRouter()
        filtered_frames: list[CanFrame] = []
        router.subscribe(
            callback=filtered_frames.append,
            filter_ids={0x100, 0x200},
        )

        frame100 = _create_test_frame(arbitration_id=0x100)
        frame200 = _create_test_frame(arbitration_id=0x200)
        frame300 = _create_test_frame(arbitration_id=0x300)

        assert router.route_frame(frame100) == 1
        assert router.route_frame(frame200) == 1
        assert router.route_frame(frame300) == 0

        assert len(filtered_frames) == 2
        assert filtered_frames == [frame100, frame200]

    def test_channel_id_filtering(self) -> None:
        router = FrameRouter()
        can0_frames: list[CanFrame] = []
        can1_frames: list[CanFrame] = []
        all_frames: list[CanFrame] = []

        router.subscribe(callback=can0_frames.append, channel_id="can0")
        router.subscribe(callback=can1_frames.append, channel_id="can1")
        router.subscribe(callback=all_frames.append, channel_id=None)

        f_can0 = _create_test_frame(channel_id="can0")
        f_can1 = _create_test_frame(channel_id="can1")
        f_vcan0 = _create_test_frame(channel_id="vcan0")

        assert router.route_frame(f_can0) == 2  # can0 + all
        assert router.route_frame(f_can1) == 2  # can1 + all
        assert router.route_frame(f_vcan0) == 1  # only all

        assert can0_frames == [f_can0]
        assert can1_frames == [f_can1]
        assert all_frames == [f_can0, f_can1, f_vcan0]

    def test_combined_id_and_channel_filtering(self) -> None:
        router = FrameRouter()
        received: list[CanFrame] = []
        router.subscribe(
            callback=received.append,
            channel_id="can0",
            filter_ids={0x7DF, 0x7E0},
        )

        f_match = _create_test_frame(channel_id="can0", arbitration_id=0x7DF)
        f_wrong_channel = _create_test_frame(channel_id="can1", arbitration_id=0x7DF)
        f_wrong_id = _create_test_frame(channel_id="can0", arbitration_id=0x123)
        f_wrong_both = _create_test_frame(channel_id="can1", arbitration_id=0x123)

        assert router.route_frame(f_match) == 1
        assert router.route_frame(f_wrong_channel) == 0
        assert router.route_frame(f_wrong_id) == 0
        assert router.route_frame(f_wrong_both) == 0

        assert received == [f_match]


class TestUnsubscribeAndClear:
    """Test subscription removal and lifecycle management."""

    def test_unsubscribe_existing_subscription(self) -> None:
        router = FrameRouter()
        sub1_frames: list[CanFrame] = []
        sub2_frames: list[CanFrame] = []

        sub1_id, _ = router.subscribe(callback=sub1_frames.append)
        sub2_id, _ = router.subscribe(callback=sub2_frames.append)
        assert router.subscription_count == 2

        unsub_res = router.unsubscribe(sub1_id)
        assert unsub_res is True
        assert router.subscription_count == 1

        frame = _create_test_frame()
        matched = router.route_frame(frame)

        assert matched == 1
        assert len(sub1_frames) == 0
        assert len(sub2_frames) == 1

    def test_unsubscribe_nonexistent_or_duplicate(self) -> None:
        router = FrameRouter()
        sub_id, _ = router.subscribe(callback=lambda _: None)

        assert router.unsubscribe(sub_id) is True
        assert router.unsubscribe(sub_id) is False
        assert router.unsubscribe(9999) is False

    def test_clear_all_subscriptions(self) -> None:
        router = FrameRouter()
        frames: list[CanFrame] = []

        router.subscribe(callback=frames.append)
        router.subscribe(use_queue=True)
        assert router.subscription_count == 2

        router.clear()
        assert router.subscription_count == 0
        assert router.stats["active_subscriptions"] == 0

        matched = router.route_frame(_create_test_frame())
        assert matched == 0
        assert len(frames) == 0


class TestQueueOverflowAndDropTracking:
    """Test queue full / drop tracking mechanisms."""

    def test_queue_overflow_tracking(self) -> None:
        router = FrameRouter()
        _, fq = router.subscribe(use_queue=True, queue_maxsize=2)
        assert fq is not None

        frame1 = _create_test_frame(arbitration_id=0x01)
        frame2 = _create_test_frame(arbitration_id=0x02)
        frame3 = _create_test_frame(arbitration_id=0x03)
        frame4 = _create_test_frame(arbitration_id=0x04)

        router.route_frame(frame1)
        router.route_frame(frame2)
        assert router.stats["total_dropped"] == 0

        # Now queue is full (maxsize=2), subsequent frames should drop
        matched3 = router.route_frame(frame3)
        assert matched3 == 1  # Matched the subscriber filter
        assert router.stats["total_dropped"] == 1

        matched4 = router.route_frame(frame4)
        assert matched4 == 1
        assert router.stats["total_dropped"] == 2

        assert router.stats["total_routed"] == 4

        # Verify only the first two frames are in the queue
        assert fq.get_nowait() == frame1
        assert fq.get_nowait() == frame2
        assert fq.empty()


class TestConcurrencyAndThreadSafety:
    """Test thread-safety under concurrent frame routing, subscriptions, and queue operations."""

    def test_concurrent_routing_and_queuing(self) -> None:
        router = FrameRouter()
        num_frames = 200
        num_subscribers = 4

        queues: list[queue.Queue[CanFrame]] = []
        for _ in range(num_subscribers):
            _, q = router.subscribe(use_queue=True, queue_maxsize=num_frames * 2)
            assert q is not None
            queues.append(q)

        def producer() -> None:
            for i in range(num_frames):
                frame = _create_test_frame(arbitration_id=i)
                router.route_frame(frame)

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=producer)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert router.stats["total_routed"] == num_frames * 2
        assert router.stats["total_dropped"] == 0

        for q in queues:
            assert q.qsize() == num_frames * 2

    def test_concurrent_subscribe_unsubscribe_and_routing(self) -> None:
        """Ensure no race conditions or deadlocks during dynamic subscription changes under load."""
        router = FrameRouter()
        stop_event = threading.Event()
        routed_count = 0
        lock = threading.Lock()

        def router_worker() -> None:
            nonlocal routed_count
            while not stop_event.is_set():
                router.route_frame(_create_test_frame())
                with lock:
                    routed_count += 1

        def subscriber_worker() -> None:
            while not stop_event.is_set():
                sub_id, _ = router.subscribe(callback=lambda _: None)
                router.unsubscribe(sub_id)

        t_route = threading.Thread(target=router_worker)
        t_sub = threading.Thread(target=subscriber_worker)

        t_route.start()
        t_sub.start()

        # Let both run for a brief window
        time.sleep(0.1)

        stop_event.set()
        t_route.join()
        t_sub.join()

        assert routed_count > 0
        assert router.stats["total_routed"] == routed_count


class TestThreadLocalCounters:
    """RT-3 (FAZ 1 / review #6): per-thread stats counters.

    `route_frame` used to take the global `_stats_lock` on EVERY frame just to
    bump `_total_routed`, serializing all RX ingest threads on one mutex. The
    counters are now accumulated thread-locally and flushed periodically, while
    `stats` stays EXACT (it drains every thread's pending delta).
    """

    def test_stats_exact_below_flush_threshold(self) -> None:
        """Totals must be exact even when fewer than FLUSH_THRESHOLD frames were routed."""
        router = FrameRouter()
        router.subscribe(callback=lambda _: None)

        frames = FrameRouter.FLUSH_THRESHOLD // 4
        for i in range(frames):
            router.route_frame(_create_test_frame(arbitration_id=i))

        # Nothing should have been flushed to the global counters yet...
        assert router._total_routed == 0
        # ...but `stats` must still report the exact total by draining pending.
        assert router.stats["total_routed"] == frames
        # Draining must have published the delta (not double-counted).
        assert router._total_routed == frames
        assert router.stats["total_routed"] == frames

    def test_stats_exact_across_flush_boundary(self) -> None:
        """A count crossing the flush threshold must not double-count or drop frames."""
        router = FrameRouter()
        router.subscribe(callback=lambda _: None)

        frames = FrameRouter.FLUSH_THRESHOLD * 3 + 7
        for i in range(frames):
            router.route_frame(_create_test_frame(arbitration_id=i % 0x7FF))

        assert router.stats["total_routed"] == frames

    def test_hot_path_does_not_take_stats_lock_per_frame(self) -> None:
        """The per-frame path must not acquire `_stats_lock` (the review #6 bottleneck).

        Asserts the lock is acquired strictly fewer times than frames routed by
        instrumenting `threading.Lock.acquire` via a counting proxy.
        """
        router = FrameRouter()
        router.subscribe(callback=lambda _: None)

        acquire_count = 0
        real_lock = router._stats_lock

        class _CountingLock:
            def __enter__(self_inner):  # noqa: N805
                nonlocal acquire_count
                acquire_count += 1
                return real_lock.__enter__()

            def __exit__(self_inner, *exc):  # noqa: N805
                return real_lock.__exit__(*exc)

        router._stats_lock = _CountingLock()  # type: ignore[assignment]

        frames = FrameRouter.FLUSH_THRESHOLD * 2
        for i in range(frames):
            router.route_frame(_create_test_frame(arbitration_id=i % 0x7FF))

        # Old code: one acquisition per frame. New code: ~one per FLUSH_THRESHOLD.
        assert acquire_count <= frames // FrameRouter.FLUSH_THRESHOLD + 2

    def test_multi_thread_totals_are_exact(self) -> None:
        """Concurrent producers must not lose increments (the L-16 regression)."""
        router = FrameRouter()
        router.subscribe(callback=lambda _: None)

        num_threads = 8
        per_thread = FrameRouter.FLUSH_THRESHOLD * 2 + 13
        barrier = threading.Barrier(num_threads)

        def producer() -> None:
            barrier.wait()
            for i in range(per_thread):
                router.route_frame(_create_test_frame(arbitration_id=i % 0x7FF))

        threads = [threading.Thread(target=producer) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert router.stats["total_routed"] == num_threads * per_thread

    def test_dropped_and_routed_stay_consistent(self) -> None:
        """stats() must never observe a torn pair (dropped > routed)."""
        router = FrameRouter()
        _, q = router.subscribe(use_queue=True, queue_maxsize=1)
        assert q is not None

        for i in range(50):
            router.route_frame(_create_test_frame(arbitration_id=i))

        stats = router.stats
        assert stats["total_dropped"] >= 1  # queue capped at 1
        assert stats["total_routed"] == 50
        assert stats["total_dropped"] <= stats["total_routed"]

    def test_flush_pending_counters_publishes_remainder(self) -> None:
        """An explicit flush must publish sub-threshold remainders exactly once."""
        router = FrameRouter()
        router.subscribe(callback=lambda _: None)

        for i in range(5):
            router.route_frame(_create_test_frame(arbitration_id=i))

        assert router._total_routed == 0
        router.flush_pending_counters()
        assert router._total_routed == 5
        router.flush_pending_counters()  # idempotent: nothing left pending
        assert router._total_routed == 5


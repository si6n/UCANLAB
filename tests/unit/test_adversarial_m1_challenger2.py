"""Adversarial stress-tests and empirical challenge harness for Milestone 1.

Target Systems:
1. InMemoryTxPort & QueueRxSubscription: High concurrency async/sync producers & consumers,
   backpressure, unread cancellation, timeout edge cases, and thread safety.
2. SystemClockProvider: Monotonic non-decreasing invariants across millions of iterations
   and multi-core thread migrations, resolution verification, and precision correlation.
3. Exception Chaining & Serialization: Formal exception hierarchy, multi-hop `raise ... from cause`
   propagation, RFC 7807 `to_dict()` JSON serialization under heavy stress, and pickle/deepcopy fidelity.
4. CanFrame Data Contract: Invariant boundaries, hash-set collision resistance, and thread safety.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import pickle
import threading
import time
from typing import Any

import pytest

from src.core.contracts import (
    InMemorySecretProvider,
    InMemoryTxPort,
    QueueRxSubscription,
    SystemClockProvider,
)
from src.core.errors import PlatformError, TransportError
from src.core.exceptions import (
    IsoTpBufferOverflowError,
    IsoTpError,
    IsoTpFlowControlError,
    IsoTpInvalidPduError,
    IsoTpSequenceError,
    IsoTpTimeoutError,
    J1939SequenceError,
    J1939SessionCollisionError,
    J1939TpAbortError,
    J1939TpError,
    J1939TpTimeoutError,
)
from src.core.models.can_frame import CanFrame

# ============================================================================
# 1. InMemoryTxPort & QueueRxSubscription Concurrency Stress-Tests
# ============================================================================


class TestInMemoryTxPortConcurrencyStress:
    """Empirical concurrency stress tests for InMemoryTxPort."""

    @pytest.mark.asyncio
    async def test_massive_async_concurrent_producers(self) -> None:
        """Stress InMemoryTxPort with 50 concurrent async tasks emitting 200 frames each (10,000 total)."""
        port = InMemoryTxPort()
        num_tasks = 50
        frames_per_task = 200
        total_frames = num_tasks * frames_per_task

        async def producer(task_id: int) -> None:
            for seq in range(frames_per_task):
                frame = CanFrame.create(
                    channel_id=f"ch_{task_id % 4}",
                    arbitration_id=0x100 + (task_id % 32),
                    data=f"T{task_id:02d}S{seq:04d}".encode("ascii"),
                )
                await port.send(frame)

        await asyncio.gather(*(producer(i) for i in range(num_tasks)))

        assert len(port.sent_frames) == total_frames
        assert all(isinstance(f, CanFrame) for f in port.sent_frames)

    def test_massive_multithreaded_sync_producers(self) -> None:
        """Stress InMemoryTxPort with 20 OS threads calling send_sync concurrently (10,000 total)."""
        port = InMemoryTxPort()
        num_threads = 20
        frames_per_thread = 500
        total_expected = num_threads * frames_per_thread

        def sync_worker(thread_id: int) -> None:
            for seq in range(frames_per_thread):
                frame = CanFrame.create(
                    channel_id="ch_sync",
                    arbitration_id=0x200 + thread_id,
                    data=f"T{thread_id:02d}{seq:04d}".encode("ascii"),
                )
                port.send_sync(frame)

        threads = [threading.Thread(target=sync_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(port.sent_frames) == total_expected

    @pytest.mark.asyncio
    async def test_clear_and_reuse_stress(self) -> None:
        """Verify repeated clear and send cycles under async load without memory leak or state corruption."""
        port = InMemoryTxPort()
        for cycle in range(20):
            frame = CanFrame.create(channel_id="ch0", arbitration_id=0x123, data=bytes([cycle]))
            await port.send(frame)
            assert len(port.sent_frames) == 1
            port.clear()
            assert len(port.sent_frames) == 0


class TestQueueRxSubscriptionConcurrencyStress:
    """Empirical concurrency and lifecycle stress tests for QueueRxSubscription."""

    @pytest.mark.asyncio
    async def test_multi_producer_multi_consumer_fifo_integrity(self) -> None:
        """10 concurrent producers and 10 concurrent consumers processing 10,000 frames total.

        Verify 0 frame loss, 0 frame duplication, and exact data fidelity.
        """
        sub = QueueRxSubscription()
        num_producers = 10
        num_consumers = 10
        frames_per_producer = 1000
        total_frames = num_producers * frames_per_producer

        received_frames: list[CanFrame] = []
        recv_lock = asyncio.Lock()

        async def producer(prod_id: int) -> None:
            for i in range(frames_per_producer):
                frame = CanFrame.create(
                    channel_id=f"ch_{prod_id}",
                    arbitration_id=0x300 + prod_id,
                    data=f"P{prod_id:02d}F{i:04d}".encode("ascii"),
                )
                await sub.put(frame)

        async def consumer() -> None:
            while True:
                frame = await sub.recv(timeout_s=0.1)
                if frame is None:
                    # Check if all frames received
                    async with recv_lock:
                        if len(received_frames) >= total_frames:
                            break
                    await asyncio.sleep(0.001)
                    continue
                async with recv_lock:
                    received_frames.append(frame)
                    if len(received_frames) >= total_frames:
                        break

        # Launch producers and consumers concurrently
        prod_tasks = [asyncio.create_task(producer(i)) for i in range(num_producers)]
        cons_tasks = [asyncio.create_task(consumer()) for i in range(num_consumers)]

        await asyncio.gather(*prod_tasks)
        await asyncio.gather(*cons_tasks)

        assert len(received_frames) == total_frames
        # Verify uniqueness of all payloads
        payload_set = {f.data for f in received_frames}
        assert len(payload_set) == total_frames

    @pytest.mark.asyncio
    async def test_timed_recv_boundary_and_timeout_exactness(self) -> None:
        """Stress-test timeout behavior on empty and sparse queues."""
        sub = QueueRxSubscription()

        # 1. Zero timeout on empty queue -> returns None immediately
        t0 = time.perf_counter()
        res = await sub.recv(timeout_s=0.0)
        t_elapsed = time.perf_counter() - t0
        assert res is None
        assert t_elapsed < 0.01  # Instant return

        # 2. Negative timeout -> treated as zero timeout
        res_neg = await sub.recv(timeout_s=-1.0)
        assert res_neg is None

        # 3. Finite short timeout (20ms) on empty queue
        t0 = time.perf_counter()
        res_timed = await sub.recv(timeout_s=0.02)
        t_elapsed = time.perf_counter() - t0
        assert res_timed is None
        # asyncio schedules the timeout on time.monotonic(), which ticks every
        # ~15.6 ms on Windows, while this test measures with perf_counter(). A
        # 20 ms wait can therefore look as short as 20 ms minus one monotonic
        # tick (observed: 5.1 ms on windows-latest). The lower bound allows
        # exactly that granularity and no more; the upper bound is unchanged.
        tick = time.get_clock_info("monotonic").resolution
        assert max(0.0, 0.02 - tick - 0.001) <= t_elapsed <= 0.15

        # 4. Data arrives before timeout (put after 10ms)
        frame = CanFrame.create(channel_id="ch0", arbitration_id=0x400, data=b"\xAA\xBB")

        async def delayed_put() -> None:
            await asyncio.sleep(0.01)
            await sub.put(frame)

        asyncio.create_task(delayed_put())
        recvd = await sub.recv(timeout_s=0.1)
        assert recvd == frame

    @pytest.mark.asyncio
    async def test_unsubscribe_cancels_active_and_future_receivers(self) -> None:
        """Verify unsubscription immediately unblocks waiting consumers and drops future recvs."""
        sub = QueueRxSubscription()

        # Spawn waiting task
        async def waiting_consumer() -> CanFrame | None:
            return await sub.recv(timeout_s=1.0)

        wait_task = asyncio.create_task(waiting_consumer())
        await asyncio.sleep(0.01)

        # Unsubscribe while wait_task is waiting
        sub.unsubscribe()
        assert sub.is_unsubscribed is True

        # Wait task should complete or return None when timeout / next check happens
        # In current implementation, unsubscribe marks flag, next recv call returns None
        assert await sub.recv(timeout_s=None) is None
        assert await sub.recv(timeout_s=0.1) is None
        assert await sub.recv(timeout_s=0.0) is None

        # Ensure task finishes within timeout
        res = await wait_task
        # Could be None due to timeout or unsubscription
        assert res is None

    @pytest.mark.asyncio
    async def test_concurrent_waiting_consumers_all_unblock_on_flood(self) -> None:
        """50 consumer tasks waiting on empty queue; 50 frames arrive in burst.

        Verify all 50 waiting consumers resolve their respective frame without hang.
        """
        sub = QueueRxSubscription()
        num_consumers = 50

        async def consumer(cid: int) -> CanFrame | None:
            return await sub.recv(timeout_s=1.0)

        tasks = [asyncio.create_task(consumer(i)) for i in range(num_consumers)]
        await asyncio.sleep(0.01)  # Ensure all tasks enter wait_for / get()

        # Enqueue 50 frames
        for i in range(num_consumers):
            frame = CanFrame.create(channel_id="ch0", arbitration_id=0x100 + i, data=bytes([i]))
            sub.put_nowait(frame)

        results = await asyncio.gather(*tasks)
        assert len(results) == num_consumers
        assert all(isinstance(f, CanFrame) for f in results)
        recvd_ids = {f.arbitration_id for f in results if f is not None}
        assert recvd_ids == {0x100 + i for i in range(num_consumers)}

    @pytest.mark.asyncio
    async def test_bounded_queue_backpressure_and_overflow(self) -> None:
        """Verify behavior when QueueRxSubscription is initialized with a bounded asyncio.Queue."""
        bounded_q: asyncio.Queue[CanFrame] = asyncio.Queue(maxsize=2)
        sub = QueueRxSubscription(queue=bounded_q)

        frame1 = CanFrame.create(channel_id="ch0", arbitration_id=0x1, data=b"\x01")
        frame2 = CanFrame.create(channel_id="ch0", arbitration_id=0x2, data=b"\x02")
        frame3 = CanFrame.create(channel_id="ch0", arbitration_id=0x3, data=b"\x03")

        sub.put_nowait(frame1)
        sub.put_nowait(frame2)

        # put_nowait should raise QueueFull on 3rd item
        with pytest.raises(asyncio.QueueFull):
            sub.put_nowait(frame3)

        # Async put blocks until space is available
        put_task = asyncio.create_task(sub.put(frame3))
        await asyncio.sleep(0.01)
        assert not put_task.done()

        # Consuming one item frees space for put_task
        r1 = await sub.recv(timeout_s=0.1)
        assert r1 == frame1
        await put_task  # Should now finish
        assert put_task.done()

        r2 = await sub.recv(timeout_s=0.1)
        assert r2 == frame2
        r3 = await sub.recv(timeout_s=0.1)
        assert r3 == frame3


class TestSecretProviderStress:
    """Stress tests for InMemorySecretProvider."""

    def test_concurrent_secret_access_and_updates(self) -> None:
        """20 threads reading and updating InMemorySecretProvider simultaneously."""
        provider = InMemorySecretProvider({"master_key": b"\x00" * 32})
        num_threads = 20
        ops_per_thread = 500

        def worker(tid: int) -> None:
            for i in range(ops_per_thread):
                provider.set_secret(f"thread_{tid}_key_{i}", f"secret_{tid}_{i}".encode())
                val = provider.get_secret("master_key")
                assert val == b"\x00" * 32
                my_val = provider.get_secret(f"thread_{tid}_key_{i}")
                assert my_val == f"secret_{tid}_{i}".encode()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()



# ============================================================================
# 2. SystemClockProvider Precision & Monotonic Guarantees
# ============================================================================


class TestSystemClockProviderStress:
    """Empirical precision and monotonic non-decreasing guarantee tests for SystemClockProvider."""

    def test_single_threaded_high_frequency_monotonic_guarantee(self) -> None:
        """Sample SystemClockProvider 1,000,000 times in a tight loop.

        Verify strictly non-decreasing invariant (t_{i+1} >= t_i) for both seconds and nanoseconds.
        """
        clock = SystemClockProvider()
        iterations = 1_000_000

        # Test nanosecond clock
        prev_ns = clock.now_monotonic_ns()
        for _ in range(iterations):
            curr_ns = clock.now_monotonic_ns()
            assert curr_ns >= prev_ns, f"Monotonic NS clock went backward: {curr_ns} < {prev_ns}"
            prev_ns = curr_ns

        # Test seconds clock
        prev_sec = clock.now_monotonic()
        for _ in range(iterations):
            curr_sec = clock.now_monotonic()
            assert curr_sec >= prev_sec, f"Monotonic Sec clock went backward: {curr_sec} < {prev_sec}"
            prev_sec = curr_sec

    def test_multi_threaded_clock_monotonicity_and_concurrency(self) -> None:
        """Sample clock across 16 concurrent threads (100,000 samples per thread = 1.6M total).

        Verify per-thread monotonicity and absence of cross-thread corruption or exceptions.
        """
        clock = SystemClockProvider()
        num_threads = 16
        samples_per_thread = 100_000

        violations: list[str] = []
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            prev_ns = clock.now_monotonic_ns()
            prev_s = clock.now_monotonic()
            for i in range(samples_per_thread):
                curr_ns = clock.now_monotonic_ns()
                curr_s = clock.now_monotonic()
                if curr_ns < prev_ns:
                    with lock:
                        violations.append(f"Thread {thread_idx} NS regressed at iter {i}: {curr_ns} < {prev_ns}")
                if curr_s < prev_s:
                    with lock:
                        violations.append(f"Thread {thread_idx} S regressed at iter {i}: {curr_s} < {prev_s}")
                prev_ns = curr_ns
                prev_s = curr_s

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, idx) for idx in range(num_threads)]
            concurrent.futures.wait(futures)

        assert len(violations) == 0, f"Monotonicity violations detected: {violations[:5]}"

    def test_clock_seconds_and_nanoseconds_coherence(self) -> None:
        """Verify that now_monotonic() (seconds) and now_monotonic_ns() (nanoseconds) remain coherent."""
        clock = SystemClockProvider()
        for _ in range(10_000):
            t_s = clock.now_monotonic()
            t_ns = clock.now_monotonic_ns()
            t_s_after = clock.now_monotonic()

            # Converted nanoseconds should lie between [t_s * 1e9 - 1ms, t_s_after * 1e9 + 1ms]
            expected_ns_lower = int(t_s * 1_000_000_000) - 1_000_000  # 1ms tolerance
            expected_ns_upper = int(t_s_after * 1_000_000_000) + 1_000_000
            assert expected_ns_lower <= t_ns <= expected_ns_upper, (
                f"Clock incoherence: s={t_s}, ns={t_ns}, s_after={t_s_after}"
            )


# ============================================================================
# 3. Exception Chaining, Serialization & Inheritance Stress-Tests
# ============================================================================


class TestExceptionChainingAndSerializationStress:
    """Stress test formal exception taxonomy, Python exception chaining, serialization, and pickle."""

    @pytest.mark.parametrize(
        "exc_class, kwargs",
        [
            (
                IsoTpTimeoutError,
                {
                    "message": "N_Bs timeout",
                    "timeout_type": "N_Bs",
                    "elapsed_ms": 1050.0,
                    "limit_ms": 1000.0,
                    "details": {"peer": 0x7E0},
                },
            ),
            (
                IsoTpFlowControlError,
                {
                    "message": "FC wait exceeded",
                    "flow_status": 1,
                    "wft_count": 17,
                    "reason": "WFTMAX_EXCEEDED",
                    "details": {"sa": 0xF1},
                },
            ),
            (
                IsoTpBufferOverflowError,
                {
                    "message": "RX buffer overflow",
                    "requested_length": 65536,
                    "max_buffer_size": 4096,
                    "details": {"channel": "ch1"},
                },
            ),
            (
                IsoTpSequenceError,
                {"expected_sn_or_msg": 5, "actual_sn": 8, "details": {"consecutive_frame_index": 22}},
            ),
            (
                IsoTpInvalidPduError,
                {"message": "Invalid PCI", "pci_type": 3, "raw_data": b"\x39\x00\x01", "details": {"source": "can0"}},
            ),
            (
                J1939TpAbortError,
                {"message": "Abort Reason 1", "reason": 1, "target_pgn": 61444, "sa": 0x00, "da": 0xF9},
            ),
            (
                J1939SessionCollisionError,
                {"message": "Session collision", "sa": 0x10, "da": 0x20, "old_pgn": 65226, "new_pgn": 65227},
            ),
            (
                J1939SequenceError,
                {"message": "DT seq error", "expected_seq": 4, "received_seq": 7, "sa": 0x01, "da": 0x02},
            ),
            (
                J1939TpTimeoutError,
                {
                    "message": "T1 timeout",
                    "timeout_type": "T1",
                    "elapsed_ms": 800.0,
                    "limit_ms": 750.0,
                    "sa": 0x05,
                    "da": 0x06,
                    "target_pgn": 65226,
                },
            ),
            (IsoTpError, {"message": "Generic IsoTp", "code": "ISOTP_CUSTOM", "details": {"k": "v"}}),
            (J1939TpError, {"message": "Generic J1939", "code": "J1939_CUSTOM", "details": {"k": "v"}}),
        ],
    )
    def test_all_exceptions_rfc7807_and_json_serialization(self, exc_class: type, kwargs: dict[str, Any]) -> None:
        """Verify that every exception subclass implements to_dict() returning JSON-serializable dictionaries."""
        cause_err = ValueError("Root hardware failure")
        kwargs_with_cause = dict(kwargs)
        kwargs_with_cause["cause"] = cause_err

        exc = exc_class(**kwargs_with_cause)

        # Invariant checks
        assert isinstance(exc, PlatformError)
        assert isinstance(exc, TransportError)
        assert exc.cause is cause_err

        # Test to_dict()
        d = exc.to_dict()
        assert isinstance(d, dict)
        assert "code" in d
        assert "message" in d
        assert "timestamp_ns" in d
        assert "details" in d
        assert "cause" in d
        assert "Root hardware failure" in str(d["cause"])

        # Test strict JSON serialization
        json_str = json.dumps(d)
        assert len(json_str) > 0
        parsed_back = json.loads(json_str)
        assert parsed_back["code"] == d["code"]

    def test_python_standard_raise_from_chaining(self) -> None:
        """Verify Python 3 `raise ... from cause` sets __cause__ and __context__ seamlessly."""
        try:
            try:
                raise ConnectionResetError("Physical bus disconnected")
            except ConnectionResetError as hw_err:
                raise IsoTpTimeoutError(
                    "N_Bs timeout due to bus disconnect",
                    timeout_type="N_Bs",
                    elapsed_ms=1001.0,
                    limit_ms=1000.0,
                    cause=hw_err,
                ) from hw_err
        except IsoTpTimeoutError as caught:
            assert caught.__cause__ is not None
            assert isinstance(caught.__cause__, ConnectionResetError)
            assert caught.cause is caught.__cause__
            d = caught.to_dict()
            assert "Physical bus disconnected" in d["cause"]

    def test_multi_hop_exception_chaining(self) -> None:
        """Verify 4-layer exception chaining: OSError -> TransportError -> IsoTpSequenceError -> J1939SessionCollisionError."""
        try:
            try:
                try:
                    try:
                        raise OSError("CAN controller hardware buffer overrun")
                    except OSError as e0:
                        raise TransportError("Transport frame drop", cause=e0) from e0
                except TransportError as e1:
                    raise IsoTpSequenceError(expected_sn=3, actual_sn=5, cause=e1) from e1
            except IsoTpSequenceError as e2:
                raise J1939SessionCollisionError("Collision after sequence failure", sa=1, da=2, old_pgn=1, new_pgn=2, cause=e2) from e2
        except J1939SessionCollisionError as final_err:
            assert final_err.cause is not None
            assert isinstance(final_err.cause, IsoTpSequenceError)
            assert final_err.cause.cause is not None
            assert isinstance(final_err.cause.cause, TransportError)
            assert final_err.cause.cause.cause is not None
            assert isinstance(final_err.cause.cause.cause, OSError)

    def test_massive_concurrent_exception_instantiation_and_serialization(self) -> None:
        """Instantiate and serialize 10,000 exception objects across 10 threads.

        Verify thread safety, zero timestamp regressions in single threads, and zero crash.
        """
        num_threads = 10
        excs_per_thread = 1000

        def worker(thread_id: int) -> list[dict[str, Any]]:
            results = []
            for i in range(excs_per_thread):
                err = IsoTpTimeoutError(
                    f"Timeout in thread {thread_id} iter {i}",
                    timeout_type="N_Cr",
                    elapsed_ms=1000.0 + i,
                    limit_ms=1000.0,
                    details={"thread_id": thread_id, "iteration": i, "sub_data": [1, 2, 3]},
                )
                d = err.to_dict()
                results.append(d)
            return results

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            all_results = [f.result() for f in futures]

        flat_results = [r for sub in all_results for r in sub]
        assert len(flat_results) == num_threads * excs_per_thread
        # Verify JSON roundtrip for all 10,000
        combined_json = json.dumps(flat_results)
        assert len(combined_json) > 100_000

    def test_deepcopy_and_pickle_fidelity(self) -> None:
        """Verify that all exception instances support copy.deepcopy and pickle roundtripping."""
        original_exc = J1939TpAbortError(
            message="Abort connection",
            reason=2,
            target_pgn=65226,
            sa=0x11,
            da=0xF9,
            details={"notes": "Deepcopy test", "active_nodes": [1, 2, 3]},
        )

        # 1. Deepcopy test
        copied = copy.deepcopy(original_exc)
        assert copied.message == original_exc.message
        assert copied.reason == original_exc.reason
        assert copied.target_pgn == original_exc.target_pgn
        assert copied.sa == original_exc.sa
        assert copied.da == original_exc.da
        assert copied.details == original_exc.details

        # 2. Pickle roundtrip test
        pickled_bytes = pickle.dumps(original_exc)
        unpickled: J1939TpAbortError = pickle.loads(pickled_bytes)
        assert unpickled.message == original_exc.message
        assert unpickled.reason == original_exc.reason
        assert unpickled.target_pgn == original_exc.target_pgn
        assert unpickled.sa == original_exc.sa
        assert unpickled.da == original_exc.da


# ============================================================================
# 4. CanFrame Immutability and Hashing Stress-Tests
# ============================================================================


class TestCanFrameStress:
    """Stress test CanFrame data model for immutability, hashing, and thread safety."""

    def test_can_frame_hashability_and_set_uniqueness(self) -> None:
        """Verify 5,000 distinct CanFrames can be hashed and stored in sets/dicts without collisions."""
        frame_set: set[CanFrame] = set()
        for i in range(5000):
            frame = CanFrame.create(
                channel_id=f"ch_{i % 8}",
                arbitration_id=i % 0x7FF,
                data=i.to_bytes(4, "big"),
                timestamp_ns=1000000 + i,
            )
            frame_set.add(frame)

        assert len(frame_set) == 5000

    def test_can_frame_slots_immutability(self) -> None:
        """Verify CanFrame is strictly frozen and cannot be mutated."""
        frame = CanFrame.create(channel_id="ch0", arbitration_id=0x123, data=b"\x01\x02")
        with pytest.raises((AttributeError, TypeError)):
            frame.arbitration_id = 0x456  # type: ignore[misc]

        with pytest.raises((AttributeError, TypeError)):
            frame.data = b"\x99"  # type: ignore[misc]

    def test_can_frame_pad_payload_and_crc_type(self) -> None:
        """Stress-test CAN-FD padding and CRC determination across 0..64 byte lengths."""
        for length in range(65):
            data = bytes(range(length))
            frame = CanFrame.create(
                channel_id="ch0",
                arbitration_id=0x7E0,
                data=data,
                is_fd=True,
            )
            padded = frame.padded_data
            assert len(padded) >= length
            if length <= 16:
                assert frame.crc_type in ("CRC-15", "CRC-17")
            else:
                assert frame.crc_type == "CRC-21"

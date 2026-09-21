"""Empirical Adversarial Stress Test Suite for Milestone 1.

Challenger test harness validating:
1. Protocol runtime checkability (@runtime_checkable) with duck-typed objects, missing methods,
   PEP 544 structural attribute presence vs callable invocation, and async/sync invocations.
2. Complete exception inheritance taxonomy (PlatformError -> TransportError -> IsoTpError/J1939TpError),
   verifying isolation and polymorphic catch blocks.
3. Exception argument handling (positional, kwargs, defaults), boundary values (negative, zero, huge, None),
   dictionary serialization (JSON compatibility), cause chaining, and pickle roundtripping.
4. Concurrency, queue exhaustion, and teardown resilience on concrete port implementations.
"""

from __future__ import annotations

import asyncio
import json
import pickle
from typing import Any

import pytest

from src.core.contracts import (
    ClockProvider,
    InMemorySecretProvider,
    InMemoryTxPort,
    QueueRxSubscription,
    RxSubscription,
    SecretProvider,
    SystemClockProvider,
    TxPort,
)
from src.core.errors import (
    HardwareError,
    LicenseError,
    PlatformError,
    ProtocolError,
    SafetyError,
    SecurityError,
    TransportError,
)
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
# Section 1: Protocol Runtime Checkability & Duck Typing Stress Tests
# ============================================================================


class MinimalDuckTxPort:
    """Duck-typed TxPort without inheriting from Protocol."""

    def __init__(self) -> None:
        self.sent: list[CanFrame] = []

    async def send(self, frame: CanFrame) -> None:
        self.sent.append(frame)

    def send_sync(self, frame: CanFrame) -> None:
        self.sent.append(frame)


class TxPortMissingSendSync:
    """Missing send_sync method."""

    async def send(self, frame: CanFrame) -> None:
        pass


class TxPortMissingSend:
    """Missing async send method."""

    def send_sync(self, frame: CanFrame) -> None:
        pass


class TxPortNonCallableSend:
    """send is a property / attribute rather than callable."""

    send = "not_callable"

    def send_sync(self, frame: CanFrame) -> None:
        pass


class MinimalDuckRxSubscription:
    """Duck-typed RxSubscription without inheriting from Protocol."""

    def __init__(self, frame: CanFrame | None = None) -> None:
        self._frame = frame
        self.unsubscribed = False

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        if self.unsubscribed:
            return None
        return self._frame

    def unsubscribe(self) -> None:
        self.unsubscribed = True


class RxSubMissingUnsubscribe:
    """Missing unsubscribe method."""

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        return None


class RxSubMissingRecv:
    """Missing recv method."""

    def unsubscribe(self) -> None:
        pass


class RxSubNonCallableRecv:
    """recv is an integer attribute."""

    recv = 12345

    def unsubscribe(self) -> None:
        pass


class MinimalDuckClockProvider:
    """Duck-typed ClockProvider without inheriting from Protocol."""

    def now_monotonic(self) -> float:
        return 123.456

    def now_monotonic_ns(self) -> int:
        return 123456000000

    def now_wall_ns(self) -> int:
        return 1780000000000000000


class ClockMissingNs:
    """Missing now_monotonic_ns."""

    def now_monotonic(self) -> float:
        return 0.0


class ClockMissingFloat:
    """Missing now_monotonic."""

    def now_monotonic_ns(self) -> int:
        return 0


class MinimalDuckSecretProvider:
    """Duck-typed SecretProvider without inheriting from Protocol."""

    def get_secret(self, key_name: str) -> bytes:
        return b"super_secret_payload"


class SecretMissingGetSecret:
    """Missing get_secret method."""

    pass


class TestProtocolRuntimeChecking:
    """Adversarial validation of runtime checkability on all 4 core port protocols."""

    def test_tx_port_runtime_checking_matrix(self) -> None:
        # Full implementation passes
        assert isinstance(MinimalDuckTxPort(), TxPort)
        assert isinstance(InMemoryTxPort(), TxPort)

        # Incomplete implementations fail isinstance (missing required attributes)
        assert not isinstance(TxPortMissingSendSync(), TxPort)
        assert not isinstance(TxPortMissingSend(), TxPort)
        assert not isinstance(object(), TxPort)
        assert not isinstance(None, TxPort)
        assert not isinstance("string_port", TxPort)

        # PEP 544 structural attribute check behavior:
        # @runtime_checkable inspects attribute existence (hasattr). Thus non-callable attribute satisfies hasattr.
        assert isinstance(TxPortNonCallableSend(), TxPort)

    def test_rx_subscription_runtime_checking_matrix(self) -> None:
        # Full implementation passes
        assert isinstance(MinimalDuckRxSubscription(), RxSubscription)
        assert isinstance(QueueRxSubscription(), RxSubscription)

        # Incomplete implementations fail (missing required attributes)
        assert not isinstance(RxSubMissingUnsubscribe(), RxSubscription)
        assert not isinstance(RxSubMissingRecv(), RxSubscription)
        assert not isinstance(object(), RxSubscription)
        assert not isinstance(None, RxSubscription)

        # PEP 544 structural attribute check behavior
        assert isinstance(RxSubNonCallableRecv(), RxSubscription)

    def test_clock_provider_runtime_checking_matrix(self) -> None:
        # Full implementation passes
        assert isinstance(MinimalDuckClockProvider(), ClockProvider)
        assert isinstance(SystemClockProvider(), ClockProvider)

        # Incomplete implementations fail
        assert not isinstance(ClockMissingNs(), ClockProvider)
        assert not isinstance(ClockMissingFloat(), ClockProvider)
        assert not isinstance(object(), ClockProvider)

    def test_secret_provider_runtime_checking_matrix(self) -> None:
        # Full implementation passes
        assert isinstance(MinimalDuckSecretProvider(), SecretProvider)
        assert isinstance(InMemorySecretProvider(), SecretProvider)

        # Incomplete implementations fail
        assert not isinstance(SecretMissingGetSecret(), SecretProvider)
        assert not isinstance(object(), SecretProvider)

    @pytest.mark.asyncio
    async def test_duck_typed_port_invocations(self) -> None:
        """Verify calling async and sync methods on duck-typed objects matches contract behavior."""
        port = MinimalDuckTxPort()
        frame = CanFrame.create("ch0", 0x7E0, b"\x02\x10\x01\x00\x00\x00\x00\x00")
        await port.send(frame)
        port.send_sync(frame)
        assert len(port.sent) == 2

        sub = MinimalDuckRxSubscription(frame)
        assert await sub.recv(timeout_s=1.0) == frame
        sub.unsubscribe()
        assert await sub.recv(timeout_s=1.0) is None

        clock = MinimalDuckClockProvider()
        assert clock.now_monotonic() == 123.456
        assert clock.now_monotonic_ns() == 123456000000

        secret = MinimalDuckSecretProvider()
        assert secret.get_secret("any_key") == b"super_secret_payload"

    @pytest.mark.asyncio
    async def test_non_callable_attribute_invocation_faults(self) -> None:
        """Adversarial stress test: non-callable attributes pass PEP 544 isinstance but raise TypeError on call."""
        bad_port = TxPortNonCallableSend()
        frame = CanFrame.create("ch0", 0x7E0, b"\x00")
        with pytest.raises(TypeError):
            await bad_port.send(frame)  # type: ignore[operator]

        bad_rx = RxSubNonCallableRecv()
        with pytest.raises(TypeError):
            await bad_rx.recv()  # type: ignore[operator]


# ============================================================================
# Section 2: Concrete Port Implementation Stress & Concurrency Tests
# ============================================================================


class TestQueueRxSubscriptionStress:
    """Stress testing QueueRxSubscription under concurrency, timeout edge cases, and cancellations."""

    @pytest.mark.asyncio
    async def test_recv_timeout_boundary_values(self) -> None:
        sub = QueueRxSubscription()
        frame = CanFrame.create("ch0", 0x100, b"\x01\x02")

        # Negative timeout with empty queue -> returns None
        assert await sub.recv(timeout_s=-1.0) is None
        assert await sub.recv(timeout_s=-100.0) is None

        # Zero timeout with empty queue -> returns None immediately
        assert await sub.recv(timeout_s=0.0) is None
        assert await sub.recv(timeout_s=0) is None

        # Negative timeout with item in queue -> returns item immediately
        sub.put_nowait(frame)
        assert await sub.recv(timeout_s=-1.0) == frame

        # Zero timeout with item in queue -> returns item immediately
        sub.put_nowait(frame)
        assert await sub.recv(timeout_s=0.0) == frame

    @pytest.mark.asyncio
    async def test_concurrent_producers_and_consumers(self) -> None:
        """High throughput concurrent async producer and consumer on QueueRxSubscription."""
        sub = QueueRxSubscription()
        num_frames = 200
        sent_frames = [
            CanFrame.create("ch0", 0x100 + i, bytes([i % 256, (i * 2) % 256]))
            for i in range(num_frames)
        ]

        async def producer() -> None:
            for f in sent_frames:
                await sub.put(f)
                await asyncio.sleep(0.0001)

        async def consumer() -> list[CanFrame]:
            received: list[CanFrame] = []
            while len(received) < num_frames:
                f = await sub.recv(timeout_s=1.0)
                if f is not None:
                    received.append(f)
                else:
                    break
            return received

        prod_task = asyncio.create_task(producer())
        cons_task = asyncio.create_task(consumer())

        await prod_task
        received_frames = await cons_task

        assert len(received_frames) == num_frames
        assert received_frames == sent_frames

    @pytest.mark.asyncio
    async def test_unsubscribe_during_pending_recv(self) -> None:
        """Verify cancelling or unsubscribing while a recv is actively waiting."""
        sub = QueueRxSubscription()

        async def waiting_recv() -> CanFrame | None:
            return await sub.recv(timeout_s=5.0)

        recv_task = asyncio.create_task(waiting_recv())
        await asyncio.sleep(0.01)

        # Unsubscribe and cancel
        sub.unsubscribe()
        recv_task.cancel()

        try:
            res = await recv_task
            assert res is None
        except asyncio.CancelledError:
            pass  # Expected standard async cancellation

        # Subsequent recv calls must return None immediately
        assert await sub.recv(timeout_s=1.0) is None


class TestSecretProviderStress:
    """Stress testing InMemorySecretProvider keys, encodings, and overwriting."""

    def test_in_memory_secret_provider_edge_keys(self) -> None:
        provider = InMemorySecretProvider()

        # Empty string key
        provider.set_secret("", b"empty_key_val")
        assert provider.get_secret("") == b"empty_key_val"

        # Unicode key
        provider.set_secret("🔑_secret_key_123", b"unicode_secret")
        assert provider.get_secret("🔑_secret_key_123") == b"unicode_secret"

        # Large binary payload
        large_secret = b"\xAA\xBB\xCC\xDD" * 1024
        provider.set_secret("large_key", large_secret)
        assert provider.get_secret("large_key") == large_secret

        # Overwriting key
        provider.set_secret("key1", b"val1")
        assert provider.get_secret("key1") == b"val1"
        provider.set_secret("key1", b"val2")
        assert provider.get_secret("key1") == b"val2"


class TestClockProviderStress:
    """Stress testing SystemClockProvider monotonicity and nanosecond consistency."""

    def test_monotonic_clock_properties(self) -> None:
        clock = SystemClockProvider()

        t_s1 = clock.now_monotonic()
        t_ns1 = clock.now_monotonic_ns()

        assert isinstance(t_s1, float)
        assert isinstance(t_ns1, int)

        for _ in range(50):
            t_s2 = clock.now_monotonic()
            t_ns2 = clock.now_monotonic_ns()
            assert t_s2 >= t_s1
            assert t_ns2 >= t_ns1
            t_s1, t_ns1 = t_s2, t_ns2


# ============================================================================
# Section 3: Formal Exception Taxonomy & Inheritance Matrix
# ============================================================================

ALL_ISOTP_EXCEPTION_CLASSES = [
    IsoTpError,
    IsoTpTimeoutError,
    IsoTpFlowControlError,
    IsoTpBufferOverflowError,
    IsoTpSequenceError,
    IsoTpInvalidPduError,
]

ALL_J1939_EXCEPTION_CLASSES = [
    J1939TpError,
    J1939TpAbortError,
    J1939SessionCollisionError,
    J1939SequenceError,
    J1939TpTimeoutError,
]

ALL_PLATFORM_ERROR_SUBCLASSES = [
    HardwareError,
    TransportError,
    ProtocolError,
    SafetyError,
    LicenseError,
    SecurityError,
]


class TestExceptionHierarchyMatrix:
    """Exhaustive check of inheritance relations and polymorphic catch blocks."""

    @pytest.mark.parametrize("exc_cls", ALL_ISOTP_EXCEPTION_CLASSES)
    def test_all_isotp_exceptions_inherit_from_transport_and_platform_error(
        self, exc_cls: type[Exception]
    ) -> None:
        assert issubclass(exc_cls, IsoTpError)
        assert issubclass(exc_cls, TransportError)
        assert issubclass(exc_cls, PlatformError)
        assert issubclass(exc_cls, Exception)
        # Verify isolation: must not inherit from J1939TpError
        assert not issubclass(exc_cls, J1939TpError)

    @pytest.mark.parametrize("exc_cls", ALL_J1939_EXCEPTION_CLASSES)
    def test_all_j1939_exceptions_inherit_from_transport_and_platform_error(
        self, exc_cls: type[Exception]
    ) -> None:
        assert issubclass(exc_cls, J1939TpError)
        assert issubclass(exc_cls, TransportError)
        assert issubclass(exc_cls, PlatformError)
        assert issubclass(exc_cls, Exception)
        # Verify isolation: must not inherit from IsoTpError
        assert not issubclass(exc_cls, IsoTpError)

    @pytest.mark.parametrize("exc_cls", ALL_PLATFORM_ERROR_SUBCLASSES)
    def test_platform_error_subclasses(self, exc_cls: type[Exception]) -> None:
        assert issubclass(exc_cls, PlatformError)
        assert issubclass(exc_cls, Exception)

    def test_polymorphic_catch_blocks(self) -> None:
        """Ensure exceptions can be caught at any level of the hierarchy."""
        instances: list[Exception] = [
            IsoTpTimeoutError(),
            IsoTpFlowControlError(),
            IsoTpBufferOverflowError(),
            IsoTpSequenceError(1, 2),
            IsoTpInvalidPduError(),
            J1939TpAbortError(),
            J1939SessionCollisionError(),
            J1939SequenceError(),
            J1939TpTimeoutError(),
        ]

        for exc in instances:
            # Catch via TransportError
            caught_transport = False
            try:
                raise exc
            except TransportError:
                caught_transport = True
            assert caught_transport

            # Catch via PlatformError
            caught_platform = False
            try:
                raise exc
            except PlatformError:
                caught_platform = True
            assert caught_platform


# ============================================================================
# Section 4: Exception Argument Handling, Serialization & Extreme Boundaries
# ============================================================================


class TestExceptionArgumentHandlingAndSerialization:
    """Stress test positional vs keyword arguments, boundary parameters, and serialization."""

    def test_isotp_timeout_error_variations(self) -> None:
        # 1. Defaults. P2-5 (Batch G): `elapsed_ms`/`limit_ms` no longer default
        #    to the fabricated-looking 1000.0 — an unmeasured duration is
        #    reported as None ("not observed"), never as a plausible number.
        e_def = IsoTpTimeoutError()
        assert e_def.timeout_type == "N_Bs"
        assert e_def.elapsed_ms is None
        assert e_def.limit_ms is None
        assert e_def.code == "ISOTP_TIMEOUT_N_Bs"

        # 2. Positional
        e_pos = IsoTpTimeoutError("Timeout N_Cr", "N_Cr", 1500.5, 1000.0, {"extra": "val"})
        assert e_pos.timeout_type == "N_Cr"
        assert e_pos.elapsed_ms == 1500.5
        assert e_pos.limit_ms == 1000.0
        assert e_pos.details["extra"] == "val"
        assert e_pos.code == "ISOTP_TIMEOUT_N_Cr"

        # 3. Kwargs only
        e_kw = IsoTpTimeoutError(
            message="N_As exceeded",
            timeout_type="N_As",
            elapsed_ms=50.0,
            limit_ms=25.0,
            details={"peer": "0x7E0"},
        )
        assert e_kw.timeout_type == "N_As"
        assert e_kw.elapsed_ms == 50.0

        # 4. Boundary measurements are still reported verbatim. P2-5 (Batch G):
        #    `timeout_type` is now validated against the ISO 15765-2 timer set
        #    (N_As/N_Ar/N_Bs/N_Br/N_Cs/N_Cr) because it is interpolated into the
        #    machine-readable `code`; the old `timeout_type=""` case — which
        #    produced the truncated code "ISOTP_TIMEOUT_" — is rejected.
        e_bound = IsoTpTimeoutError("Boundary", timeout_type="N_Cr", elapsed_ms=-10.0, limit_ms=0.0)
        assert e_bound.timeout_type == "N_Cr"
        assert e_bound.elapsed_ms == -10.0
        assert e_bound.limit_ms == 0.0
        assert e_bound.code == "ISOTP_TIMEOUT_N_Cr"
        with pytest.raises(ValueError, match="Unknown ISO-TP timer"):
            IsoTpTimeoutError("Boundary", timeout_type="")

        # 5. Serialization & JSON compatibility
        d = e_pos.to_dict()
        assert isinstance(d, dict)
        assert d["code"] == "ISOTP_TIMEOUT_N_Cr"
        assert d["details"]["timeout_type"] == "N_Cr"
        assert "timestamp_ns" in d
        # Must be JSON serializable
        json_str = json.dumps(d)
        assert "ISOTP_TIMEOUT_N_Cr" in json_str

    def test_isotp_flow_control_error_variations(self) -> None:
        # Defaults
        e_def = IsoTpFlowControlError()
        assert e_def.flow_status is None
        assert e_def.wft_count is None
        assert e_def.reason == "FLOW_CONTROL_ERROR"
        assert e_def.code == "ISOTP_FLOW_CONTROL_ERROR"

        # Positional
        e_pos = IsoTpFlowControlError("WFT limit", 1, 16, "WFTMAX_EXCEEDED", {"peer": 0x7E8})
        assert e_pos.flow_status == 1
        assert e_pos.wft_count == 16
        assert e_pos.reason == "WFTMAX_EXCEEDED"
        assert e_pos.details["peer"] == 0x7E8

        # JSON serializability
        d = e_pos.to_dict()
        assert json.dumps(d)

    def test_isotp_buffer_overflow_error_variations(self) -> None:
        # Defaults
        e_def = IsoTpBufferOverflowError()
        assert e_def.requested_length == 0
        assert e_def.max_buffer_size is None
        assert e_def.code == "ISOTP_BUFFER_OVERFLOW"

        # Positional & huge buffer length
        huge_len = 2**32 - 1
        e_huge = IsoTpBufferOverflowError("Buffer overflow", huge_len, 4096, {"channel": "can0"})
        assert e_huge.requested_length == huge_len
        assert e_huge.max_buffer_size == 4096
        assert json.dumps(e_huge.to_dict())

    def test_isotp_sequence_error_variations(self) -> None:
        # Positional integers (expected_sn, actual_sn)
        e1 = IsoTpSequenceError(15, 0)
        assert e1.expected_sn == 15
        assert e1.actual_sn == 0
        assert "expected 15, got 0" in e1.message

        # Kwargs
        e2 = IsoTpSequenceError(expected_sn=3, actual_sn=5)
        assert e2.expected_sn == 3
        assert e2.actual_sn == 5

        # Custom message + positional. P2-6 (Batch G): the string form no longer
        # silently defaults `expected_sn` to 0 (0 is a legal CF index, so the
        # fabricated value was indistinguishable from a real report) — an
        # explicit `expected_sn=` is now mandatory.
        with pytest.raises(TypeError, match="expected_sn"):
            IsoTpSequenceError("Custom SN error", 2, details={"wrap": True})

        e3 = IsoTpSequenceError(
            "Custom SN error", 2, details={"wrap": True}, expected_sn=1
        )
        assert e3.message == "Custom SN error"
        assert e3.actual_sn == 2
        assert e3.expected_sn == 1
        assert e3.details["wrap"] is True

        # Custom message + explicit expected_sn kwarg
        e4 = IsoTpSequenceError("Custom SN message", expected_sn=7, actual_sn=9)
        assert e4.expected_sn == 7
        assert e4.actual_sn == 9
        assert json.dumps(e4.to_dict())

    def test_isotp_invalid_pdu_error_variations(self) -> None:
        # Defaults
        e_def = IsoTpInvalidPduError()
        assert e_def.pci_type is None
        assert e_def.raw_data is None
        assert e_def.to_dict()["details"]["raw_data_hex"] is None

        # With raw bytes
        raw = b"\x00\x11\x22\x33\x44\x55\x66\x77"
        e_raw = IsoTpInvalidPduError("Bad Classic SF", pci_type=0, raw_data=raw)
        assert e_raw.pci_type == 0
        assert e_raw.raw_data == raw
        d = e_raw.to_dict()
        assert d["details"]["raw_data_hex"] == "0011223344556677"
        # Bytes converted to hex string -> must serialize cleanly to JSON
        assert json.dumps(d)

    def test_j1939_tp_abort_error_variations(self) -> None:
        # Defaults
        e_def = J1939TpAbortError()
        assert e_def.reason == 255
        assert e_def.target_pgn == 0
        assert e_def.sa == 0
        assert e_def.da == 0
        assert e_def.code == "J1939_TP_ABORT"

        # Positional
        e_pos = J1939TpAbortError("Reason 2 collision", 2, 65226, 0x00, 0xF9, {"retries": 0})
        assert e_pos.reason == 2
        assert e_pos.target_pgn == 65226
        assert e_pos.sa == 0x00
        assert e_pos.da == 0xF9
        assert json.dumps(e_pos.to_dict())

    def test_j1939_session_collision_error_variations(self) -> None:
        # Defaults
        e_def = J1939SessionCollisionError()
        assert e_def.sa == 0
        assert e_def.da == 0
        assert e_def.old_pgn == 0
        assert e_def.new_pgn == 0

        # Kwargs
        e_kw = J1939SessionCollisionError(
            message="Collision on (0x01, 0xF9)",
            sa=0x01,
            da=0xF9,
            old_pgn=61444,
            new_pgn=65226,
        )
        assert e_kw.sa == 0x01
        assert e_kw.da == 0xF9
        assert e_kw.old_pgn == 61444
        assert e_kw.new_pgn == 65226
        assert json.dumps(e_kw.to_dict())

    def test_j1939_sequence_error_variations(self) -> None:
        # Defaults
        e_def = J1939SequenceError()
        assert e_def.expected_seq == 1
        assert e_def.received_seq == 1

        # Positional
        e_pos = J1939SequenceError("Sequence mismatch", 3, 5, 0x10, 0xF9)
        assert e_pos.expected_seq == 3
        assert e_pos.received_seq == 5
        assert e_pos.sa == 0x10
        assert e_pos.da == 0xF9
        assert json.dumps(e_pos.to_dict())

    def test_j1939_tp_timeout_error_variations(self) -> None:
        # Defaults. P2-5 (Batch G): `elapsed_ms`/`limit_ms` no longer default to
        # the fabricated-looking 750.0 — an unmeasured duration is None.
        e_def = J1939TpTimeoutError()
        assert e_def.timeout_type == "T1"
        assert e_def.elapsed_ms is None
        assert e_def.limit_ms is None
        assert e_def.code == "J1939_TIMEOUT_T1"

        # Positional with SA, DA, target_pgn
        e_pos = J1939TpTimeoutError("T2 Timeout", "T2", 1255.0, 1250.0, 0x05, 0xF9, 65226)
        assert e_pos.timeout_type == "T2"
        assert e_pos.elapsed_ms == 1255.0
        assert e_pos.limit_ms == 1250.0
        assert e_pos.sa == 0x05
        assert e_pos.da == 0xF9
        assert e_pos.target_pgn == 65226
        assert e_pos.code == "J1939_TIMEOUT_T2"
        assert json.dumps(e_pos.to_dict())

    def test_exception_chaining_and_cause(self) -> None:
        """Verify exception cause propagation and serialization with cause."""
        root_cause = ConnectionResetError("Socket closed by remote hardware")
        err = IsoTpTimeoutError("N_Bs timeout", timeout_type="N_Bs", cause=root_cause)

        assert err.cause is root_cause
        d = err.to_dict()
        assert "cause" in d
        assert "Socket closed by remote hardware" in d["cause"]
        assert json.dumps(d)

    def test_pickle_roundtrip_support(self) -> None:
        """Verify all exception instances can be pickled and unpickled (multiprocessing IPC safety)."""
        test_exceptions: list[Any] = [
            IsoTpError("base error"),
            IsoTpTimeoutError("timeout", timeout_type="N_Ar", elapsed_ms=100.0, limit_ms=50.0),
            IsoTpFlowControlError("fc", flow_status=2, wft_count=5, reason="WAIT_EXCEEDED"),
            IsoTpBufferOverflowError("overflow", requested_length=5000, max_buffer_size=4096),
            IsoTpSequenceError(1, 2),
            IsoTpInvalidPduError("invalid pdu", pci_type=0, raw_data=b"\x01\x02"),
            J1939TpError("j1939 error"),
            J1939TpAbortError("abort", reason=1, target_pgn=65226, sa=1, da=2),
            J1939SessionCollisionError("collision", sa=1, da=2, old_pgn=100, new_pgn=200),
            J1939SequenceError("seq", expected_seq=2, received_seq=4, sa=1, da=2),
            J1939TpTimeoutError("timeout", timeout_type="T3", elapsed_ms=800, limit_ms=750),
        ]

        for original in test_exceptions:
            serialized = pickle.dumps(original)
            deserialized = pickle.loads(serialized)
            assert isinstance(deserialized, type(original))
            assert str(deserialized) == str(original)
            if hasattr(original, "code"):
                assert deserialized.code == original.code

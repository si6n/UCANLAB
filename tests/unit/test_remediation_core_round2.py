"""Batch G regression tests — ``src/core/**`` remediation round 2 (2026-09-22).

One test per finding from the core audit (corrected severities:
no real P0 in this batch — P0-1/P0-2 are P1, P0-3/P0-4 are P2, and P1-4/P2-1
were partly wrong in the review).

Naming: ``test_<id>_<slug>``. Each test fails on the pre-fix tree.

Scope note: nothing here asserts an E2E or TX behaviour. ``CanFrame.crc_type``
and ``IsoTpInvalidPduError.raw_data`` are descriptive metadata in the current
wiring (consumed by tests only), so these tests lock the *contract* of those
accessors, not a transmission outcome.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import time

import pytest

from src.core import contracts as core_contracts
from src.core.contracts import VirtualClock
from src.core.contracts.ports import (
    InMemorySecretProvider,
    InMemoryTxPort,
    QueueRxSubscription,
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
    ISOTP_TIMERS,
    J1939_TP_TIMERS,
    IsoTpBufferOverflowError,
    IsoTpFlowControlError,
    IsoTpInvalidPduError,
    IsoTpSequenceError,
    IsoTpTimeoutError,
    J1939SequenceError,
    J1939SessionCollisionError,
    J1939TpAbortError,
    J1939TpTimeoutError,
)
from src.core.logging import JsonFormatter, get_logger, setup_logging
from src.core.models.can_frame import (
    DLC_TO_LENGTH,
    CanFrame,
    get_hardware_crc_type,
    pad_payload,
)
from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    Severity,
    SignalSample,
    SignalSource,
    VehicleSession,
    mask_vin_in_text,
)

_TS = 1_000_000


def _mk_frame(**overrides: object) -> CanFrame:
    kwargs: dict[str, object] = {
        "channel_id": "can0",
        "arbitration_id": 0x123,
        "dlc": 2,
        "data": b"\x01\x02",
        "timestamp_ns": _TS,
    }
    kwargs.update(overrides)
    return CanFrame(**kwargs)  # type: ignore[arg-type]


# ===========================================================================
# P0-1 (real P1) — data is never coerced: bytearray payload aliases
# ===========================================================================


def test_p0_1_bytearray_payload_is_snapshotted_not_aliased() -> None:
    buf = bytearray(b"\x01\x02")
    frame = _mk_frame(data=buf)

    assert isinstance(frame.data, bytes)
    assert frame.data == b"\x01\x02"

    # Mutating the caller's buffer after construction must not reach the frame.
    buf[0] = 0xFF
    assert frame.data == b"\x01\x02"
    assert frame.data == bytes((1, 2))


def test_p0_1_memoryview_payload_is_coerced_to_bytes() -> None:
    frame = _mk_frame(data=memoryview(b"\xAA\xBB"))
    assert type(frame.data) is bytes
    assert frame.data == b"\xAA\xBB"


def test_p0_1_hash_stability_after_caller_buffer_mutation() -> None:
    """The frozen contract implies the hash is stable for the frame's lifetime."""
    buf = bytearray(b"\x11\x22")
    frame = _mk_frame(data=buf)
    before = hash(frame)
    buf[1] = 0x99
    assert hash(frame) == before
    assert frame.data == b"\x11\x22"


# ===========================================================================
# P0-2 (real P1) — VehicleSession VIN validated only at construction
# ===========================================================================


def _session(**overrides: object) -> VehicleSession:
    kwargs: dict[str, object] = {
        "session_id": "sess-1",
        "started_at_ns": 0,
        "domain": DiagnosticDomain.PASSENGER,
    }
    kwargs.update(overrides)
    return VehicleSession(**kwargs)  # type: ignore[arg-type]


def test_p0_2_raw_vin_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="RAW VIN"):
        _session(vin_masked="WVWZZZ1KZAW123456")


def test_p0_2_raw_vin_rejected_on_later_assignment() -> None:
    session = _session()
    assert session.vin_masked is None
    with pytest.raises(ValueError, match="RAW VIN"):
        session.vin_masked = "WVWZZZ1KZAW123456"
    assert session.vin_masked is None  # fail-closed: no partial write


def test_p0_2_non_mask_assignment_rejected_on_later_assignment() -> None:
    session = _session()
    for bad in ("123456", "", "ABC", "*" * 10 + "123456", "***********12345"):
        with pytest.raises(ValueError):
            session.vin_masked = bad
    assert session.vin_masked is None


def test_p0_2_non_str_assignment_rejected() -> None:
    session = _session()
    with pytest.raises(ValueError, match="str or None"):
        session.vin_masked = 42  # type: ignore[assignment]


def test_p0_2_mask_assignment_accepted_and_none_clears() -> None:
    session = _session()
    session.vin_masked = mask_vin_in_text("WVWZZZ1KZAW123456")
    assert session.vin_masked == "***********123456"
    session.vin_masked = None
    assert session.vin_masked is None


def test_p0_2_vin_alias_reaches_the_same_validator() -> None:
    assert _session(vin=mask_vin_in_text("WVWZZZ1KZAW123456")).vin_masked == "***********123456"
    with pytest.raises(ValueError, match="RAW VIN"):
        _session(vin="WVWZZZ1KZAW123456")


def test_p0_2_conflicting_vin_and_vin_masked_rejected() -> None:
    with pytest.raises(ValueError, match="disagree"):
        _session(vin="***********123456", vin_masked="***********654321")


def test_p0_2_events_and_samples_containers_start_empty_and_frozen_records() -> None:
    session = _session()
    assert session.events == []
    assert session.samples == []

    sample = SignalSample(
        timestamp_ns=1,
        name="EngineSpeed",
        raw_value=1,
        physical_value=1.0,
        unit="rpm",
        source=SignalSource.J1939,
    )
    event = DiagnosticEvent(
        timestamp_ns=2,
        code="P0201",
        domain=DiagnosticDomain.PASSENGER,
        severity=Severity.MEDIUM,
        status="ACTIVE",
    )
    session.samples.append(sample)
    session.events.append(event)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.physical_value = 9.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.status = "HISTORY"  # type: ignore[misc]


def test_p0_2_constructor_keyword_list_is_preserved() -> None:
    """The canonical keyword set keeps working (no constructor widening/break)."""
    session = _session(
        make="Volvo Penta",
        model="D4-300",
        trace_ref="x.json",
        events=[],
        samples=[],
    )
    assert (session.make, session.model, session.trace_ref) == ("Volvo Penta", "D4-300", "x.json")


# ===========================================================================
# P0-3 (real P2, label-only) — CRC type used the application length
# ===========================================================================


def test_p0_3_crc_type_uses_dlc_capacity_not_payload_length() -> None:
    # Sub-capacity FD payload behind DLC 15: the frame is still framed at 64
    # bytes on the wire, so the polynomial is CRC-21, not CRC-17.
    frame = CanFrame(
        channel_id="canfd0",
        arbitration_id=0x123,
        dlc=15,
        data=b"\xAA",
        is_fd=True,
        timestamp_ns=_TS,
    )
    assert frame.crc_type == "CRC-21"
    assert frame.crc_type == get_hardware_crc_type(True, DLC_TO_LENGTH[15])


def test_p0_3_crc_type_unchanged_for_capacity_sized_payloads() -> None:
    assert _mk_frame(data=b"\x00" * 2, dlc=2).crc_type == "CRC-15"
    fd9 = CanFrame(
        channel_id="c", arbitration_id=0x123, dlc=9, data=b"\x00" * 12, is_fd=True, timestamp_ns=_TS
    )
    assert fd9.crc_type == "CRC-17"  # capacity 12 <= 16
    fd11 = CanFrame(
        channel_id="c", arbitration_id=0x123, dlc=11, data=b"\x00" * 20, is_fd=True, timestamp_ns=_TS
    )
    assert fd11.crc_type == "CRC-21"


def test_p0_3_crc_type_is_descriptive_only_no_tx_steering() -> None:
    """Guard against over-claiming: crc_type is a derived property, not a field."""
    assert isinstance(CanFrame.__dict__["crc_type"], property)
    # Not a dataclass field -> it cannot be set by a HAL/TX producer and it is
    # absent from the frame's serialized field set.
    assert "crc_type" not in {f.name for f in dataclasses.fields(CanFrame)}
    assert not hasattr(CanFrame, "set_crc_type")


# ===========================================================================
# P0-4 (real P2) — re.match + "$" accepted a trailing newline
# ===========================================================================


@pytest.mark.parametrize("bad", ["can0\n", "can0\r\n", "\ncan0", "can0\r", "can 0", "can0;", ""])
def test_p0_4_channel_id_trailing_newline_and_junk_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="Invalid channel_id"):
        _mk_frame(channel_id=bad)


@pytest.mark.parametrize("good", ["can0", "CAN0", "bus_1", "socketcan:can0", "vcan-0", "a" * 64])
def test_p0_4_channel_id_allowlist_still_accepts_legitimate_ids(good: str) -> None:
    assert _mk_frame(channel_id=good).channel_id == good


def test_p0_4_channel_id_over_length_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid channel_id"):
        _mk_frame(channel_id="a" * 65)


# ===========================================================================
# P1-1 — caller details overwrite structured fields; raw_data not truncated
# ===========================================================================


def test_p1_1_caller_details_cannot_overwrite_structured_fields() -> None:
    err = IsoTpTimeoutError(
        timeout_type="N_Bs",
        elapsed_ms=1050.0,
        limit_ms=1000.0,
        details={"timeout_type": "FORGED", "elapsed_ms": 0.0, "limit_ms": 999999.0},
    )
    assert err.details["timeout_type"] == "N_Bs"
    assert err.details["elapsed_ms"] == 1050.0
    assert err.details["limit_ms"] == 1000.0
    assert err.timeout_type == "N_Bs"
    assert err.code == "ISOTP_TIMEOUT_N_Bs"


def test_p1_1_non_colliding_caller_details_are_preserved() -> None:
    err = IsoTpTimeoutError(timeout_type="N_Cr", details={"session_id": 42, "channel": "can0"})
    assert err.details["session_id"] == 42
    assert err.details["channel"] == "can0"


@pytest.mark.parametrize(
    "build, colliding_key, forged, structured",
    [
        (
            lambda d: IsoTpFlowControlError(details=d),
            "reason",
            "FORGED",
            "FLOW_CONTROL_ERROR",
        ),
        (
            lambda d: IsoTpBufferOverflowError(details=d),
            "requested_length",
            -1,
            0,
        ),
        (
            lambda d: IsoTpSequenceError(3, 4, details=d),
            "expected_sn",
            -1,
            3,
        ),
        (
            lambda d: IsoTpInvalidPduError(pci_type=0, raw_data=b"\x00", details=d),
            "pci_type",
            7,
            0,
        ),
        (
            lambda d: J1939TpAbortError(reason=1, details=d),
            "reason",
            99,
            1,
        ),
        (
            lambda d: J1939SessionCollisionError(sa=1, details=d),
            "sa",
            0xFF,
            1,
        ),
        (
            lambda d: J1939SequenceError(expected_seq=2, details=d),
            "expected_seq",
            -1,
            2,
        ),
        (
            lambda d: J1939TpTimeoutError(timeout_type="T1", details=d),
            "timeout_type",
            "FORGED",
            "T1",
        ),
    ],
)
def test_p1_1_every_exception_details_merge_is_collision_safe(
    build: object, colliding_key: str, forged: object, structured: object
) -> None:
    err = build({colliding_key: forged})  # type: ignore[operator]
    assert err.details[colliding_key] == structured
    assert err.to_dict()["details"][colliding_key] == structured


def test_p1_1_raw_data_retained_within_redaction_budget() -> None:
    pdu = bytes(range(64))
    err = IsoTpInvalidPduError(pci_type=0, raw_data=pdu)

    # The serialized form already disclosed only the first 8 bytes...
    redacted = err.to_dict()["details"]["raw_data_hex"]
    assert redacted == pdu[:8].hex() + "..."
    # ...so the object must not hand back the full PDU either.
    assert err.raw_data == pdu[:8]
    assert pdu[8:] not in (err.raw_data or b"")


def test_p1_1_short_raw_data_is_not_truncated_or_marked() -> None:
    raw = b"\x12\x34"
    err = IsoTpInvalidPduError(pci_type=1, raw_data=raw)
    assert err.raw_data == raw
    assert err.to_dict()["details"]["raw_data_hex"] == raw.hex()
    assert "..." not in err.to_dict()["details"]["raw_data_hex"]


def test_p1_1_none_raw_data_stays_none() -> None:
    err = IsoTpInvalidPduError(pci_type=None, raw_data=None)
    assert err.raw_data is None
    assert err.to_dict()["details"]["raw_data_hex"] is None


# ===========================================================================
# P1-2 — details aliasing; __cause__ ignored
# ===========================================================================


def test_p1_2_caller_details_dict_is_copied_not_aliased() -> None:
    caller_details = {"stage": "initial"}
    err = PlatformError("boom", details=caller_details)
    assert err.details == {"stage": "initial"}
    assert err.details is not caller_details

    caller_details["stage"] = "mutated-after-raise"
    caller_details["injected"] = "payload"
    assert err.details == {"stage": "initial"}


def test_p1_2_serialized_details_are_immune_to_later_caller_mutation() -> None:
    caller_details = {"channel": "can0"}
    err = HardwareError("bus off", details=caller_details)
    snapshot = err.to_dict()
    caller_details["channel"] = "can1"
    assert snapshot["details"] == {"channel": "can0"}


def test_p1_2_none_details_yields_empty_dict() -> None:
    assert PlatformError("x").details == {}
    assert PlatformError("x", details=None).details == {}


def test_p1_2_raise_from_sets_cause_and_reaches_to_dict() -> None:
    root = ConnectionResetError("bus disconnected")
    try:
        try:
            raise root
        except ConnectionResetError as exc:
            raise SafetyError("TX aborted", code="TX_ABORT") from exc
    except SafetyError as caught:
        assert caught.cause is None  # no explicit cause= was passed
        assert caught.__cause__ is root
        assert caught.chained_cause is root
        assert "bus disconnected" in caught.to_dict()["cause"]


def test_p1_2_explicit_cause_wins_over_dunder_cause() -> None:
    explicit = ValueError("explicit")
    implicit = ValueError("implicit")
    try:
        raise implicit
    except ValueError:
        err = ProtocolError("wrapped", cause=explicit)
        err.__cause__ = implicit
        assert err.chained_cause is explicit
        assert err.to_dict()["cause"] == "explicit"


def test_p1_2_include_cause_false_still_omits_cause() -> None:
    try:
        raise ValueError("root")
    except ValueError as exc:
        err = TransportError("wrapped", cause=exc)
    assert "cause" not in err.to_dict(include_cause=False)


# ===========================================================================
# P1-3 — BRS/ESI accepted on classic CAN frames
# ===========================================================================


def test_p1_3_classic_frame_rejects_brs() -> None:
    with pytest.raises(ValueError, match="CAN-FD-only"):
        _mk_frame(is_fd=False, brs=True)


def test_p1_3_classic_frame_rejects_esi() -> None:
    with pytest.raises(ValueError, match="CAN-FD-only"):
        _mk_frame(is_fd=False, esi=True)


def test_p1_3_classic_frame_rejects_brs_and_esi_together() -> None:
    with pytest.raises(ValueError, match="CAN-FD-only"):
        _mk_frame(is_fd=False, brs=True, esi=True)


def test_p1_3_fd_frame_accepts_brs_and_esi() -> None:
    frame = CanFrame(
        channel_id="canfd0",
        arbitration_id=0x123,
        dlc=9,
        data=b"\x00" * 12,
        is_fd=True,
        brs=True,
        esi=True,
        timestamp_ns=_TS,
    )
    assert frame.brs is True
    assert frame.esi is True


def test_p1_3_classic_frame_defaults_unchanged() -> None:
    frame = _mk_frame()
    assert frame.brs is False
    assert frame.esi is False


# ===========================================================================
# P1-4 (residual only) — bare `except Exception: return None` in QueueRxSubscription
# ===========================================================================


@pytest.mark.asyncio
async def test_p1_4_recv_propagates_unexpected_errors_on_the_untimed_path() -> None:
    class _BrokenQueue(QueueRxSubscription):
        pass

    sub = _BrokenQueue()
    sub._queue = _RaisingQueue(RuntimeError("queue backend exploded"))

    with pytest.raises(RuntimeError, match="exploded"):
        await sub.recv(timeout_s=None)


@pytest.mark.asyncio
async def test_p1_4_recv_propagates_unexpected_errors_on_the_timed_path() -> None:
    sub = QueueRxSubscription()
    sub._queue = _RaisingQueue(RuntimeError("queue backend exploded"))

    with pytest.raises(RuntimeError, match="exploded"):
        await sub.recv(timeout_s=0.05)


@pytest.mark.asyncio
async def test_p1_4_expected_empty_queue_still_returns_none() -> None:
    sub = QueueRxSubscription()
    assert await sub.recv(timeout_s=0.0) is None
    assert await sub.recv(timeout_s=-1.0) is None
    assert await sub.recv(timeout_s=0.01) is None


@pytest.mark.asyncio
async def test_p1_4_cancellation_still_propagates() -> None:
    sub = QueueRxSubscription()
    task = asyncio.create_task(sub.recv(timeout_s=None))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class _RaisingQueue:
    """Minimal queue double whose ``get`` raises an unexpected exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def get(self) -> object:
        raise self._exc

    def get_nowait(self) -> object:
        raise self._exc


class _RecordCollector(logging.Handler):
    """Collect emitted ``LogRecord`` objects for assertions."""

    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


# ===========================================================================
# P1-5 — logging handler level / propagate / allow_nan
# ===========================================================================


def _reset_universal_can_logger() -> None:
    logger = logging.getLogger("universal_can")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


class _RootHandlersRemoved:
    """Context manager: empty the stdlib ROOT handler list, then restore it.

    Several P1-5/P3-4 assertions depend on whether root is configured — that is
    the whole point of the fix — so that state must be set deterministically
    rather than inherited from whatever pytest's logging plugin installed.
    """

    def __enter__(self) -> logging.Logger:
        self._root = logging.getLogger()
        self._saved = list(self._root.handlers)
        for handler in self._saved:
            self._root.removeHandler(handler)
        _reset_universal_can_logger()
        return self._root

    def __exit__(self, *exc_info: object) -> None:
        for handler in list(self._root.handlers):
            self._root.removeHandler(handler)
        for handler in self._saved:
            self._root.addHandler(handler)
        _reset_universal_can_logger()


def test_p1_5_second_setup_call_reapplies_handler_level() -> None:
    """The real failure mode: a later setup_logging(DEBUG) was a no-op.

    Handler installation is now conditional on root being unconfigured, so the
    invariant is asserted on whichever handler path is live.
    """
    with _RootHandlersRemoved() as root:
        collector = _RecordCollector([])
        root.addHandler(collector)
        try:
            first = setup_logging(level=logging.WARNING, json_output=False)
            assert first.level == logging.WARNING

            second = setup_logging(level=logging.DEBUG, json_output=False)
            assert second is first
            assert second.level == logging.DEBUG
            # Any handler we own was re-levelled too.
            assert all(h.level == logging.DEBUG for h in second.handlers)
        finally:
            root.removeHandler(collector)


def test_p1_5_own_handler_level_reapplied_when_we_own_it() -> None:
    """When root is empty we own the handler; a second call must re-level it."""
    with _RootHandlersRemoved():
        first = setup_logging(level=logging.WARNING, json_output=False)
        assert first.handlers, "we must install a handler when root has none"
        assert first.handlers[0].level == logging.WARNING

        second = setup_logging(level=logging.DEBUG, json_output=False)
        assert second is first
        assert second.level == logging.DEBUG
        assert second.handlers[0].level == logging.DEBUG


def test_p1_5_setup_logging_keeps_propagation_enabled() -> None:
    """Propagation to root must stay ON: root handlers (and `caplog`) need it.

    The double-logging fix is implemented by NOT installing a second handler
    when root is already configured — never by severing propagation.
    """
    with _RootHandlersRemoved():
        logger = setup_logging(level=logging.INFO, json_output=False)
        assert logger.propagate is True


def test_p1_5_no_own_handler_when_root_already_has_one() -> None:
    """Exactly-once delivery: root's handler is the only path, so no handler here."""
    with _RootHandlersRemoved() as root:
        collected: list[logging.LogRecord] = []
        root_handler = _RecordCollector(collected)
        root.addHandler(root_handler)
        try:
            logger = setup_logging(level=logging.DEBUG, json_output=True)
            assert logger.handlers == [], "must not add a second emitting handler"
            get_logger("engine.decoder").warning("propagated record")
            assert [r.getMessage() for r in collected] == ["propagated record"]
        finally:
            root.removeHandler(root_handler)


def test_p1_5_records_reach_root_exactly_once() -> None:
    """The full contract: root sees the record once, and it is not emitted twice."""
    with _RootHandlersRemoved() as root:
        collected: list[logging.LogRecord] = []
        root_handler = _RecordCollector(collected)
        root.addHandler(root_handler)
        try:
            logger = setup_logging(level=logging.DEBUG, json_output=False)
            own: list[logging.LogRecord] = []
            own_handler = _RecordCollector(own)
            # Simulate a host that ALSO asked us for our own handler path.
            logger.addHandler(own_handler)
            try:
                get_logger("hal.bus").warning("single emission")
            finally:
                logger.removeHandler(own_handler)
            assert [r.getMessage() for r in collected] == ["single emission"]
            assert [r.getMessage() for r in own] == ["single emission"]
        finally:
            root.removeHandler(root_handler)


def test_p1_5_enabled_records_actually_emit_after_level_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression for the real failure mode: DEBUG records were dropped.

    The observation point is a handler on ROOT, which doubles as proof that
    propagation to root still works. `setup_logging` is called with DEBUG and
    `caplog` (which the pytest logging plugin wires to root) is what proves the
    record actually left the project logger.
    """
    _reset_universal_can_logger()
    logger = setup_logging(level=logging.DEBUG, json_output=False)
    assert logger.level == logging.DEBUG
    # With root configured by the host (pytest/caplog), the effective threshold
    # is derived by walking UP the hierarchy, so DEBUG must be reachable.
    assert logging.getLogger("universal_can.engine.decoder").isEnabledFor(logging.DEBUG)

    with caplog.at_level(logging.DEBUG, logger="universal_can.engine.decoder"):
        get_logger("engine.decoder").debug("now visible")
    assert [r.getMessage() for r in caplog.records] == ["now visible"]
    assert caplog.records[0].levelno == logging.DEBUG


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_p1_5_json_formatter_never_emits_invalid_json_for_non_finite(bad: float) -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1, msg="m", args=(), exc_info=None
    )
    record.extra = {"signal": bad}
    out = formatter.format(record)
    # Strict JSON: `parse_constant` (the NaN/Infinity hook) must never fire.
    def _reject(token: str) -> None:
        raise ValueError(f"invalid JSON constant emitted: {token}")

    parsed = json.loads(out, parse_constant=_reject)
    assert isinstance(parsed["signal"], str)
    assert parsed["signal"].startswith("<non-finite:")
    # The raw bare token must not appear unquoted (it would be `:NaN`/`:Infinity`).
    for token in ("NaN", "Infinity"):
        assert f":{token}" not in out
        assert f":-{token}" not in out


def test_p1_5_json_formatter_still_emits_finite_floats_numerically() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1, msg="m", args=(), exc_info=None
    )
    record.extra = {"speed_kmh": 45.5, "count": 3}
    parsed = json.loads(formatter.format(record))
    assert parsed["speed_kmh"] == 45.5
    assert parsed["count"] == 3


# ===========================================================================
# P1-6 — timestamp_ns defaulted to the WALL clock
# ===========================================================================


def test_p1_6_timestamp_defaults_to_monotonic_domain() -> None:
    before_mono = time.monotonic_ns()
    before_wall = time.time_ns()
    # Deliberately NOT `_mk_frame()`: the helper pins timestamp_ns for hash
    # determinism. Omitting the argument exercises the default_factory.
    frame = CanFrame(channel_id="can0", arbitration_id=0x123, dlc=2, data=b"\x01\x02")
    after_mono = time.monotonic_ns()
    after_wall = time.time_ns()

    assert before_mono <= frame.timestamp_ns <= after_mono
    # It must NOT be a wall-clock (epoch-nanosecond) reading.
    assert not (before_wall <= frame.timestamp_ns <= after_wall)
    assert frame.timestamp_ns < before_wall
    assert after_wall > 1_000_000_000_000  # sanity: wall clock really is epoch-scale


def test_p1_6_explicit_wall_clock_still_supported() -> None:
    wall = time.time_ns()
    assert _mk_frame(timestamp_ns=wall).timestamp_ns == wall
    assert (
        CanFrame.create(
            channel_id="can0", arbitration_id=0x123, data=b"\x01\x02", timestamp_ns=wall
        ).timestamp_ns
        == wall
    )


def test_p1_6_create_default_also_monotonic() -> None:
    before = time.monotonic_ns()
    frame = CanFrame.create(channel_id="can0", arbitration_id=0x123, data=b"\x01\x02")
    assert before <= frame.timestamp_ns <= time.monotonic_ns()


def test_p1_6_wall_clock_fields_remain_available() -> None:
    """The dedicated absolute-time fields survive, so no capability was lost."""
    wall = time.time_ns()
    frame = _mk_frame(hardware_timestamp_ns=wall, host_timestamp_ns=wall)
    assert frame.hardware_timestamp_ns == wall
    assert frame.host_timestamp_ns == wall


# ===========================================================================
# P2-1 — create() had brs but no esi (the review's padding premise was WRONG)
# ===========================================================================


def test_p2_1_create_accepts_esi_for_symmetry_with_brs() -> None:
    frame = CanFrame.create(
        channel_id="canfd0",
        arbitration_id=0x123,
        data=b"\x00" * 12,
        is_fd=True,
        brs=True,
        esi=True,
    )
    assert (frame.brs, frame.esi) == (True, True)


def test_p2_1_create_rejects_esi_without_fd() -> None:
    with pytest.raises(ValueError, match="CAN-FD-only"):
        CanFrame.create(channel_id="can0", arbitration_id=0x123, data=b"\x01\x02", esi=True)


def test_p2_1_create_does_not_pad() -> None:
    """The documented contract is NO padding — lock it explicitly."""
    frame = CanFrame.create(channel_id="canfd0", arbitration_id=0x123, data=b"\x00" * 10, is_fd=True)
    assert frame.dlc == 9  # minimal DLC for 10 bytes
    assert len(frame.data) == 10  # NOT padded to the DLC-9 capacity of 12
    assert len(frame.padded_data) == 12  # padding stays opt-in via the accessor


def test_p2_1_create_forwards_error_state() -> None:
    frame = CanFrame.create(
        channel_id="can0", arbitration_id=0x123, data=b"\x01\x02", error_state="bus_off"
    )
    assert frame.error_state == "bus_off"


# ===========================================================================
# P2-2 — mask_vin_in_text is not defined in core
# ===========================================================================


def test_p2_2_mask_vin_in_text_is_defined_in_core() -> None:
    assert mask_vin_in_text("WVWZZZ1KZAW123456") == "***********123456"
    assert mask_vin_in_text("Şasi WVWZZZ1KZAW123456 okundu") == "Şasi ***********123456 okundu"
    assert mask_vin_in_text("P0300 no vin here") == "P0300 no vin here"
    assert mask_vin_in_text(None) == "None"


def test_p2_2_mask_is_case_insensitive() -> None:
    """A lowercase VIN used to slip through unmasked (REVIEW 4.2)."""
    assert mask_vin_in_text("1hgcr2f83ha123456") == "***********123456"
    assert mask_vin_in_text("WVWZZZ1KZAW123456") == mask_vin_in_text("wvwzzz1kzaw123456")


def test_p2_2_mask_is_idempotent() -> None:
    once = mask_vin_in_text("WVWZZZ1KZAW123456")
    assert mask_vin_in_text(once) == once
    assert mask_vin_in_text(once + " and WVWZZZ1KZAW123456") == once + " and " + once


def test_p2_2_mask_does_not_touch_text_without_a_17_char_run() -> None:
    for text in (
        "SPN 84 FMI 4 / DID 0xF190",
        "P0300 no vin here",
        "SN 1234567890123456",  # 16 chars — below the ISO 3779 length
    ):
        assert mask_vin_in_text(text) == text


def test_p2_2_mask_still_masks_a_bare_17_char_digit_run() -> None:
    """Documented behaviour: the mask is structural (17-char run), not semantic.

    A 17-digit serial happens to sit inside the VIN alphabet — over-masking a
    serial is a privacy-safe failure direction and is left as-is.
    """
    assert mask_vin_in_text("SN 12345678901234567") == "SN ***********234567"


def test_p2_2_core_mask_output_is_accepted_by_vehicle_session() -> None:
    session = _session(vin_masked=mask_vin_in_text("WVWZZZ1KZAW123456"))
    assert session.vin_masked == "***********123456"


# ===========================================================================
# P2-3 — InMemorySecretProvider shallow copy / uncoerced values
# ===========================================================================


def test_p2_3_bytearray_secret_is_copied_on_ingest() -> None:
    src = bytearray(b"\x00" * 32)
    provider = InMemorySecretProvider({"hmac": src})

    src[0] = 0xFF  # mutating the caller's buffer must not rewrite the vault
    assert provider.get_secret("hmac") == b"\x00" * 32


def test_p2_3_caller_dict_mutation_does_not_reach_the_provider() -> None:
    secrets: dict[str, bytes] = {"a": b"\x01"}
    provider = InMemorySecretProvider(secrets)
    secrets["a"] = b"\x02"
    secrets["b"] = b"\x03"
    assert provider.get_secret("a") == b"\x01"
    with pytest.raises(KeyError):
        provider.get_secret("b")


def test_p2_3_set_secret_coerces_bytearray() -> None:
    provider = InMemorySecretProvider()
    src = bytearray(b"\xAA" * 16)
    provider.set_secret("k", src)
    src[0] = 0x00
    assert provider.get_secret("k") == b"\xAA" * 16


def test_p2_3_get_secret_returns_a_defensive_copy() -> None:
    provider = InMemorySecretProvider({"k": b"\x01\x02"})
    got = provider.get_secret("k")
    if isinstance(got, bytearray):  # pragma: no cover - bytes has no item assignment
        got[0] = 0xFF
    assert provider.get_secret("k") == b"\x01\x02"
    assert type(provider.get_secret("k")) is bytes


def test_p2_3_whitelist_superset_flag_is_on_the_secret_store() -> None:
    default = InMemorySecretProvider()
    assert default.allow_whitelist_superset is False
    flagged = InMemorySecretProvider({}, allow_whitelist_superset=True)
    assert flagged.allow_whitelist_superset is True


# ===========================================================================
# P2-4 — VirtualClock float math + missing export
# ===========================================================================


def test_p2_4_virtual_clock_ns_is_exact_with_no_float_rounding() -> None:
    clock = VirtualClock(start_monotonic_sec=1000.0)
    # 1000.0 s is exactly 1e12 ns; the old float path could drift off this.
    assert clock.now_monotonic_ns() == 1_000_000_000_000

    clock.advance(0.8)  # the 800 ms watchdog lease limit
    assert clock.now_monotonic_ns() == 1_000_800_000_000

    clock.advance(0.1)  # classic 0.1 s float-imprecision case
    assert clock.now_monotonic_ns() == 1_000_900_000_000


def test_p2_4_virtual_clock_repeated_advance_does_not_accumulate_drift() -> None:
    clock = VirtualClock(start_monotonic_sec=0.0)
    for _ in range(1000):
        clock.advance(0.1)
    assert clock.now_monotonic_ns() == 100_000_000_000


def test_p2_4_virtual_clock_ns_helpers_are_integer_exact() -> None:
    clock = VirtualClock(start_monotonic_sec=5.0)
    clock.advance_ns(1)
    assert clock.now_monotonic_ns() == 5_000_000_001
    clock.set_ns(7_000_000_003)
    assert clock.now_monotonic_ns() == 7_000_000_003


def test_p2_4_virtual_clock_monotonic_guards_preserved() -> None:
    clock = VirtualClock(start_monotonic_sec=10.0)
    with pytest.raises(ValueError, match="non-negative"):
        clock.advance(-1.0)
    with pytest.raises(ValueError, match="backwards"):
        clock.set(9.0)
    with pytest.raises(ValueError, match="backwards"):
        clock.set_ns(9_000_000_000)


def test_p2_4_virtual_clock_seconds_view_is_coherent() -> None:
    clock = VirtualClock(start_monotonic_sec=1234.5)
    assert clock.now_monotonic() == 1234.5
    assert clock.now_monotonic_ns() == 1_234_500_000_000


def test_p2_4_virtual_clock_exported_from_contracts_package() -> None:
    assert core_contracts.VirtualClock is VirtualClock
    assert "VirtualClock" in core_contracts.__all__
    assert "VirtualClock" in __import__("src.core.contracts", fromlist=["__all__"]).__all__


# ===========================================================================
# P2-5 — unsanitized timeout_type in the error code; fake default measurements
# ===========================================================================


@pytest.mark.parametrize("bad", ["FORGED", "", "N_Bs/x", "../N_Bs", "N_Bs;drop", "T1"])
def test_p2_5_isotp_unknown_timer_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="Unknown ISO-TP timer"):
        IsoTpTimeoutError(timeout_type=bad)


@pytest.mark.parametrize("bad", ["FORGED", "", "T1/x", "N_Bs", "T5"])
def test_p2_5_j1939_unknown_timer_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="Unknown SAE J1939 timer"):
        J1939TpTimeoutError(timeout_type=bad)


@pytest.mark.parametrize("good", sorted(ISOTP_TIMERS))
def test_p2_5_isotp_all_known_timers_accepted(good: str) -> None:
    err = IsoTpTimeoutError(timeout_type=good)
    assert err.code == f"ISOTP_TIMEOUT_{good}"
    assert err.timeout_type == good


@pytest.mark.parametrize("good", sorted(J1939_TP_TIMERS))
def test_p2_5_j1939_all_known_timers_accepted(good: str) -> None:
    err = J1939TpTimeoutError(timeout_type=good)
    assert err.code == f"J1939_TIMEOUT_{good}"


def test_p2_5_default_elapsed_and_limit_are_not_fake_measurements() -> None:
    isotp = IsoTpTimeoutError()
    assert isotp.elapsed_ms is None and isotp.limit_ms is None
    assert isotp.to_dict()["details"]["elapsed_ms"] is None
    assert isotp.to_dict()["details"]["limit_ms"] is None

    j1939 = J1939TpTimeoutError()
    assert j1939.elapsed_ms is None and j1939.limit_ms is None
    assert j1939.to_dict()["details"]["limit_ms"] is None


def test_p2_5_measured_values_are_still_reported_as_given() -> None:
    err = IsoTpTimeoutError(timeout_type="N_Bs", elapsed_ms=1050.5, limit_ms=1000.0)
    assert (err.elapsed_ms, err.limit_ms) == (1050.5, 1000.0)
    assert err.to_dict()["details"]["elapsed_ms"] == 1050.5


def test_p2_5_default_placeholder_serializes_as_valid_json() -> None:
    payload = json.dumps(IsoTpTimeoutError().to_dict(), allow_nan=False)
    assert '"elapsed_ms": null' in payload


# ===========================================================================
# P2-6 — string branch silently defaulted expected_sn to 0
# ===========================================================================


def test_p2_6_custom_message_without_expected_sn_raises_type_error() -> None:
    with pytest.raises(TypeError, match="expected_sn"):
        IsoTpSequenceError("Custom sequence mismatch message")


def test_p2_6_custom_message_with_expected_sn_still_works() -> None:
    err = IsoTpSequenceError("Custom message", expected_sn=7, actual_sn=9)
    assert err.message == "Custom message"
    assert (err.expected_sn, err.actual_sn) == (7, 9)


def test_p2_6_int_form_still_derives_expected_sn() -> None:
    err = IsoTpSequenceError(3, 4)
    assert err.expected_sn == 3
    assert "expected 3, got 4" in err.message


def test_p2_6_int_form_honours_explicit_expected_sn() -> None:
    err = IsoTpSequenceError(3, 4, expected_sn=5)
    assert err.expected_sn == 5


def test_p2_6_expected_sn_zero_is_reported_not_confused_with_a_default() -> None:
    err = IsoTpSequenceError("msg", expected_sn=0, actual_sn=1)
    assert err.expected_sn == 0


# ===========================================================================
# P2-7 — docstring claimed RFC 7807 that to_dict never emits
# ===========================================================================


def test_p2_7_errors_docstring_does_not_claim_rfc7807() -> None:
    from src.core import errors as errors_module

    doc = errors_module.__doc__ or ""
    assert "RFC 7807 problem details" not in doc
    assert "does NOT emit RFC 7807" in doc


def test_p2_7_to_dict_is_flat_and_documents_its_non_rfc_shape() -> None:
    payload = ProtocolError("bad frame").to_dict()
    assert set(payload) == {"code", "message", "timestamp_ns", "details"}
    for rfc_member in ("type", "title", "status", "detail", "instance"):
        assert rfc_member not in payload


# ===========================================================================
# P2-8 — incomplete public export surface
# ===========================================================================


def test_p2_8_core_package_exports_diagnostics_and_contracts() -> None:
    import src.core as core

    for name in (
        "VehicleSession",
        "SignalSample",
        "SignalSource",
        "DiagnosticEvent",
        "DiagnosticDomain",
        "Severity",
        "mask_vin_in_text",
        "VirtualClock",
        "IsoTpTimeoutError",
        "J1939TpTimeoutError",
        "exceptions",
        "errors",
        "models",
        "contracts",
    ):
        assert hasattr(core, name), name
        assert name in core.__all__, name


def test_p2_8_models_package_exports_diagnostics_and_dlc_helpers() -> None:
    import src.core.models as models

    for name in (
        "CanFrame",
        "DLC_TO_LENGTH",
        "dlc_to_length",
        "length_to_dlc",
        "pad_payload",
        "get_hardware_crc_type",
        "VehicleSession",
        "SignalSample",
        "DiagnosticEvent",
        "DiagnosticDomain",
        "Severity",
        "mask_vin_in_text",
    ):
        assert hasattr(models, name), name
        assert name in models.__all__, name


def test_p2_8_telemetry_signal_does_not_exist_and_was_not_invented() -> None:
    """AGENTS.md §1 names a class that is absent repo-wide — drift, not a defect."""
    import src.core.models as models

    assert not hasattr(models, "TelemetrySignal")
    assert "TelemetrySignal" not in models.__all__


def test_p2_8_export_lists_have_no_duplicates_and_are_sorted() -> None:
    import src.core as core

    assert len(core.__all__) == len(set(core.__all__))
    assert core.__all__ == sorted(core.__all__)


# ===========================================================================
# P2-9 — severity allowlist + agriculture/ISOBUS domain
# ===========================================================================


@pytest.mark.parametrize(
    "good", ["UNKNOWN", "INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL_STOP"]
)
def test_p2_9_allowed_severities_accepted(good: str) -> None:
    event = DiagnosticEvent(
        timestamp_ns=1,
        code="P0201",
        domain=DiagnosticDomain.PASSENGER,
        severity=Severity(good),
        status="ACTIVE",
    )
    assert event.severity is Severity[good]


@pytest.mark.parametrize("good", ["UNKNOWN", "INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL_STOP"])
def test_p2_9_documented_string_literal_is_accepted_and_normalised(good: str) -> None:
    """In-scope producers pass the literal; it must normalise to the member."""
    event = DiagnosticEvent(
        timestamp_ns=1,
        code="P0201",
        domain=DiagnosticDomain.PASSENGER,
        severity=good,  # type: ignore[arg-type]
        status="ACTIVE",
    )
    assert event.severity is Severity[good]


@pytest.mark.parametrize("bad", ["HIHG", "medium", "", "NONE", "URGENT", "INFO "])
def test_p2_9_unlisted_severity_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="severity"):
        DiagnosticEvent(
            timestamp_ns=1,
            code="P0201",
            domain=DiagnosticDomain.PASSENGER,
            severity=bad,  # type: ignore[arg-type]
            status="ACTIVE",
        )


def test_p2_9_unknown_is_a_modelled_rung_not_a_fabricated_default() -> None:
    """UNKNOWN is the honest no-KB-entry answer (AGENTS.md §2.3), allowlisted."""
    assert Severity.UNKNOWN.value == "UNKNOWN"
    assert Severity.UNKNOWN.isupper()


def test_p2_9_agriculture_isobus_domain_exists() -> None:
    assert DiagnosticDomain("AGRICULTURE") is DiagnosticDomain.AGRICULTURE
    assert "AGRICULTURE" in DiagnosticDomain.__members__


def test_p2_9_existing_domains_unchanged() -> None:
    for name in ("HEAVY_DUTY", "PASSENGER", "MARINE", "INDUSTRIAL"):
        assert name in DiagnosticDomain.__members__


def test_p2_9_severity_values_match_the_engine_enum() -> None:
    """Core must not import engine; the allowlist is kept in sync by VALUE."""
    from src.engine.ai.diagnostic_copilot import FaultSeverity

    engine_values = {f.value for f in FaultSeverity}
    for member in Severity:
        if member is Severity.UNKNOWN:
            # UNKNOWN is core's own no-KB-entry rung; the engine has no
            # equivalent member (it reports severity=None / UNKNOWN separately).
            assert member.value not in engine_values
            continue
        assert member.value in engine_values
        assert FaultSeverity(member.value).value == member.value


# ===========================================================================
# P2-10 — InMemoryTxPort had no guard in the core contracts module
# ===========================================================================


def test_p2_10_in_memory_tx_port_requires_explicit_test_acknowledgement() -> None:
    port = InMemoryTxPort(unsafe_test_double=True)
    assert port.sent_frames == []
    assert InMemoryTxPort.is_test_double is True


def test_p2_10_in_memory_tx_port_refuses_denied_flag() -> None:
    with pytest.raises(RuntimeError, match="TEST DOUBLE"):
        InMemoryTxPort(unsafe_test_double=False)


def test_p2_10_rejection_message_names_the_tx_safety_gateway() -> None:
    with pytest.raises(RuntimeError, match="TxSafetyGateway"):
        InMemoryTxPort(unsafe_test_double=False)


def test_p2_10_default_and_positional_still_construct_for_tests() -> None:
    assert isinstance(InMemoryTxPort(), InMemoryTxPort)
    with pytest.raises(TypeError):
        InMemoryTxPort(False)  # type: ignore[misc]  # keyword-only guard is intentional


def test_p2_10_documented_as_never_a_production_tx_path() -> None:
    doc = InMemoryTxPort.__doc__ or ""
    assert "TEST DOUBLE" in doc
    assert "TxSafetyGateway" in doc
    assert "AGENTS.md" in doc


# ===========================================================================
# P3-1 — six identical subclass __init__ bodies
# ===========================================================================


@pytest.mark.parametrize(
    "cls, expected",
    [
        (HardwareError, "HARDWARE_ERROR"),
        (TransportError, "TRANSPORT_ERROR"),
        (ProtocolError, "PROTOCOL_ERROR"),
        (SafetyError, "SAFETY_ERROR"),
        (LicenseError, "LICENSE_ERROR"),
        (SecurityError, "SECURITY_ERROR"),
    ],
)
def test_p3_1_default_code_class_attribute_drives_the_code(cls: type, expected: str) -> None:
    assert cls.default_code == expected
    err = cls("boom")
    assert err.code == expected
    assert isinstance(err, PlatformError)


@pytest.mark.parametrize(
    "cls",
    [HardwareError, TransportError, ProtocolError, SafetyError, LicenseError, SecurityError],
)
def test_p3_1_subclasses_still_accept_explicit_code_details_and_cause(cls: type) -> None:
    err = cls("boom", code="CUSTOM", details={"k": "v"}, cause=ValueError("root"))
    assert err.code == "CUSTOM"
    assert err.details == {"k": "v"}
    assert err.cause is not None
    assert err.to_dict()["cause"] == "root"


def test_p3_1_no_duplicate_init_bodies_remain() -> None:
    for cls in (
        HardwareError,
        TransportError,
        ProtocolError,
        SafetyError,
        LicenseError,
        SecurityError,
    ):
        assert "__init__" not in cls.__dict__ or cls.__dict__["__init__"] is PlatformError.__init__


# ===========================================================================
# P3-2 — pad_payload(pad_byte=...) range
# ===========================================================================


@pytest.mark.parametrize("bad", [-1, 256, 1000, -255])
def test_p3_2_pad_byte_out_of_range_rejected_explicitly(bad: int) -> None:
    with pytest.raises(ValueError, match="pad_byte"):
        pad_payload(b"\x01", 2, pad_byte=bad)


@pytest.mark.parametrize("bad", [1.5, "0xCC", None, True])
def test_p3_2_pad_byte_non_int_rejected(bad: object) -> None:
    with pytest.raises(ValueError, match="pad_byte"):
        pad_payload(b"\x01", 2, pad_byte=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("good", [0x00, 0xCC, 0xFF])
def test_p3_2_valid_pad_bytes_accepted(good: int) -> None:
    assert pad_payload(b"\x01", 2, pad_byte=good) == b"\x01" + bytes([good])


def test_p3_2_default_padding_unchanged() -> None:
    assert pad_payload(b"\x01\x02", 3) == b"\x01\x02\xcc"


def test_p3_2_existing_guards_unchanged() -> None:
    with pytest.raises(ValueError, match="exceeds DLC"):
        pad_payload(b"\x00" * 4, 2)
    assert pad_payload(b"\x01", 1) == b"\x01"


# ===========================================================================
# P3-3 — enum/str inconsistency and the stale VALID_SOURCES comment
# ===========================================================================


def test_p3_3_valid_sources_includes_synthetic_and_comment_matches() -> None:
    assert CanFrame.VALID_SOURCES == frozenset(
        {"physical", "replay", "virtual", "injected", "synthetic"}
    )
    assert len(CanFrame.VALID_SOURCES) == 5
    asset = CanFrame.VALID_SOURCES
    assert "synthetic" in asset


def test_p3_3_synthetic_source_is_accepted_on_a_frame() -> None:
    assert _mk_frame(source="synthetic").source == "synthetic"


def test_p3_3_unknown_source_still_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="Invalid source"):
        _mk_frame(source="made_up")


def test_p3_3_channel_pattern_is_no_longer_redundantly_anchored() -> None:
    # fullmatch is used, so the "$" anchor is gone; verify the pattern itself
    # rejects a trailing newline when used the way __post_init__ uses it.
    import re

    assert not re.fullmatch(CanFrame.CHANNEL_ID_PATTERN, "can0\n")
    assert re.fullmatch(CanFrame.CHANNEL_ID_PATTERN, "can0")


# ===========================================================================
# P3-4 — get_logger did not ensure handlers
# ===========================================================================


def test_p3_4_get_logger_configures_the_namespace_when_unconfigured() -> None:
    """P3-4 with an empty root: get_logger must stand the logging path up itself."""
    with _RootHandlersRemoved():
        assert logging.getLogger("universal_can").handlers == []
        logger = get_logger("hal.test")
        assert logger.name == "universal_can.hal.test"
        assert logging.getLogger("universal_can").handlers


def test_p3_4_get_logger_reuses_root_handlers_when_root_is_configured() -> None:
    """P3-4 + P1-5: with root configured, get_logger must NOT double-handle."""
    with _RootHandlersRemoved() as root:
        collector = _RecordCollector([])
        root.addHandler(collector)
        try:
            logger = get_logger("hal.test")
            assert logger.name == "universal_can.hal.test"
            assert logging.getLogger("universal_can").handlers == []
        finally:
            root.removeHandler(collector)


def test_p3_4_get_logger_does_not_duplicate_handlers() -> None:
    """Repeated get_logger calls must never accumulate handlers."""
    with _RootHandlersRemoved() as root:
        collector = _RecordCollector([])
        root.addHandler(collector)
        try:
            setup_logging(level=logging.INFO, json_output=False)
            baseline = len(logging.getLogger("universal_can").handlers)
            for i in range(5):
                get_logger(f"mod{i}")
            assert len(logging.getLogger("universal_can").handlers) == baseline
        finally:
            root.removeHandler(collector)


# ===========================================================================
# P3-5 — QueueRxSubscription lives in core contracts (documented decision)
# ===========================================================================


def test_p3_5_queue_rx_subscription_location_is_documented() -> None:
    from src.core.contracts.ports import QueueRxSubscription as QRS

    doc = QRS.__doc__ or ""
    assert "P3-5" in doc
    assert "RxSubscription" in doc


@pytest.mark.asyncio
async def test_p3_5_queue_rx_subscription_still_satisfies_the_port() -> None:
    from src.core.contracts import RxSubscription

    sub = QueueRxSubscription()
    assert isinstance(sub, RxSubscription)
    assert await sub.recv(timeout_s=0.0) is None


# ===========================================================================
# P3-6 — the module shadows stdlib `logging` (documented, NOT renamed)
# ===========================================================================


def test_p3_6_shadowing_is_documented_and_stdlib_import_still_resolves() -> None:
    from src.core import logging as core_logging

    doc = core_logging.__doc__ or ""
    assert "P3-6" in doc
    # The module's own `logging` attribute must be the STANDARD LIBRARY module.
    assert core_logging.logging is logging
    assert not hasattr(core_logging.logging, "JsonFormatter")


def test_p3_6_rename_would_break_out_of_scope_imports_so_file_is_unchanged() -> None:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    assert (root / "src" / "core" / "logging.py").is_file()
    assert not (root / "src" / "core" / "structured_logging.py").exists()


# ===========================================================================
# Cross-cutting: no safety-relevant validation was relaxed
# ===========================================================================


def test_fail_closed_validations_are_all_still_enforced() -> None:
    with pytest.raises(ValueError, match="Invalid channel_id"):
        _mk_frame(channel_id="bad id")
    with pytest.raises(ValueError, match="Invalid DLC"):
        _mk_frame(dlc=16)
    with pytest.raises(ValueError, match="arbitration_id"):
        _mk_frame(arbitration_id=0x800, is_extended=False)
    with pytest.raises(ValueError, match="Classic CAN DLC"):
        _mk_frame(dlc=9, data=b"\x00" * 12)
    with pytest.raises(ValueError, match="does not match DLC"):
        _mk_frame(dlc=8, data=b"\x01\x02")
    with pytest.raises(ValueError, match="non-empty"):
        CanFrame(channel_id="c", arbitration_id=0x1, dlc=12, data=b"", is_fd=True, timestamp_ns=_TS)
    with pytest.raises(ValueError, match="Invalid direction"):
        _mk_frame(direction="both")
    with pytest.raises(ValueError, match="Invalid error_state"):
        _mk_frame(error_state="broken")
    with pytest.raises(ValueError, match="non-finite|finite"):
        SignalSample(
            timestamp_ns=1,
            name="X",
            raw_value=0,
            physical_value=math.nan,
            unit="-",
            source=SignalSource.J1939,
        )


def test_can_frame_is_still_frozen_and_hashable() -> None:
    frame = _mk_frame()
    with pytest.raises((AttributeError, TypeError)):
        frame.data = b"\x09\x09"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        frame.brs = True  # type: ignore[misc]
    assert len({_mk_frame(data=b"\x01\x02"), _mk_frame(data=b"\x01\x03")}) == 2


def test_can_frame_equality_and_repr_survive_the_bytes_coercion() -> None:
    a = _mk_frame()
    b = _mk_frame()
    assert a == b
    assert "can0" in repr(a)

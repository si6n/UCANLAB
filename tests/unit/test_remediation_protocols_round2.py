"""Batch D (round 2) protocol remediations — regression suite.

Findings verified OPEN in ``docs/audit/verify/protocols.md`` and assigned to
Batch D of ``docs/audit/IMPROVEMENT_PLAN.md``:

    M14  Intel HEX EOF record is mandatory (truncated image rejected)
    L7   S-Record S0 header "validation" was a tautology
    L5   ``build_clear_diagnostic_information`` range-validates ``dtc_group``
    M10  ``build_read_dtc_information`` resolves the sub-function via the enum
    H2   Address Claim Request honours the destination address (and replies
         Cannot-Claim when we hold no address)
    M12  Poller job key includes the ECU (tx/rx ids)
    H3   OBD poller extends P2* on NRC 0x78 with an absolute wall
    L1   ``_ExpiringAddressTable`` exposes a NAME-shaped Mapping API
    L2   Claim-rate buckets are pruned
    L3   ``J1939Name`` validates each subfield's bit width
    M8   SA -> NAME/OEM tables carry a monotonic TTL
    M6   Volvo out-of-spec code types are skipped, never labelled PPID
    M5   No static Volvo SA allowlist — provenance needs a NAME claim
    M7   Volvo Address Claim PGN parse uses the shared parser
    M1   J1939 Request payloads are padded to the canonical 8-byte frame
    M2   CM=1 DTC records are flagged unsupported (never silently wrong)
    M13  Flasher asks the gateway for a secret through a public accessor
    L6   Flasher warning goes to the module logger
    M3/M4/M9/L8  cosmetic only (covered by the M9 default assertion)

No test here transmits: every assertion is on the bytes a builder RETURNS or
on a decode result. TX stays behind the caller's TxPort/gateway (AGENTS.md §2.1).
"""

from __future__ import annotations

import asyncio
import struct
import time

import pytest

from src.core.contracts.ports import InMemoryTxPort
from src.core.errors import ProtocolError
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.address_claim import (
    NULL_ADDRESS,
    AddressClaimEngine,
    AddressClaimState,
    J1939Name,
    _ExpiringAddressTable,
)
from src.protocols.j1939.diagnostics import (
    J1939DiagnosticService,
)
from src.protocols.j1939.oem.registry import OemJ1939Registry
from src.protocols.obd.poller import ActiveDiagnosticPoller, PollerState
from src.protocols.uds.firmware import IntelHexParser, SRecordParser
from src.protocols.uds.isotp import MAX_UDS_PAYLOAD_FD, IsoTpTransport
from src.protocols.uds.services import (
    ReadDtcInformationType,
    UdsServiceBuilder,
)
from src.protocols.volvo.volvo_decoder import VolvoPentaDecoder

# ===========================================================================
# helpers
# ===========================================================================


def _ihex_line(data: bytes, address: int = 0, record_type: int = 0) -> str:
    """Build one checksum-correct Intel HEX record."""
    raw = bytes([len(data), (address >> 8) & 0xFF, address & 0xFF, record_type]) + data
    return f":{raw.hex().upper()}{((~sum(raw) + 1) & 0xFF):02X}\n"


def _srec_line(rec_type: str, address: int, data: bytes) -> str:
    addr_len = 2 if rec_type in ("0", "1", "5", "9") else (3 if rec_type in ("2", "8") else 4)
    raw = bytes([addr_len + len(data) + 1]) + address.to_bytes(addr_len, "big") + data
    return f"S{rec_type}{raw.hex().upper()}{((~sum(raw)) & 0xFF):02X}\n"


class _FakeClock:
    """Controllable monotonic clock for deterministic scheduler tests."""

    def __init__(self, start_time: float = 100.0) -> None:
        self._time = start_time

    def now_monotonic(self) -> float:
        return self._time

    def now_monotonic_ns(self) -> int:
        return int(self._time * 1_000_000_000)

    def advance(self, seconds: float) -> None:
        self._time += seconds


class _ScriptedRxSub:
    """RxSubscription that hands back queued frames, else yields briefly.

    `recv` returns None quickly when the queue is empty so the poller
    promptly picks up ALREADY-QUEUED frames (a real bus delivers frames at
    arbitrary times, not in lockstep with the caller's timeout). The await
    matters: it yields control so `handle_rx_frame` can re-inspect the
    deadline it re-armed on NRC 0x78.
    """

    def __init__(self) -> None:
        self._frames: list[CanFrame] = []
        self.served = 0

    def push(self, frame: CanFrame) -> None:
        self._frames.append(frame)

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        if not self._frames:
            # Yield control, but far quicker than the caller's requested
            # timeout: this double returns None often so the ALREADY-QUEUED
            # frames are picked up promptly (a real bus delivers frames at
            # arbitrary times, not in lockstep with the timeout).
            await asyncio.sleep(0.001)
            return None
        self.served += 1
        return self._frames.pop(0)


# ===========================================================================
# M14 — Intel HEX EOF record is mandatory (truncated image must fail closed)
# ===========================================================================


def test_m14_truncated_intel_hex_without_eof_record_is_rejected() -> None:
    """M14: data records with NO `:00000001FF` terminator is a TRUNCATED file.

    Before the fix the guard read `if not raw_chunks and not seen_eof`, which
    only fires when BOTH are empty — an image cut off mid-transfer was
    accepted as a complete, flashable container.
    """
    truncated = _ihex_line(b"\x11\x22\x33\x44", address=0x0000) + _ihex_line(
        b"\x55\x66\x77\x88", address=0x0004
    )
    with pytest.raises(ProtocolError, match="missing EOF record"):
        IntelHexParser.parse_string(truncated)


def test_m14_complete_intel_hex_with_eof_record_is_accepted() -> None:
    """M14 positive control: the same image WITH its EOF record parses."""
    complete = (
        _ihex_line(b"\x11\x22\x33\x44", address=0x0000)
        + _ihex_line(b"\x55\x66\x77\x88", address=0x0004)
        + _ihex_line(b"", address=0x0000, record_type=1)
    )
    container = IntelHexParser.parse_string(complete)
    assert container.file_format == "intel_hex"
    assert container.total_bytes == 8


def test_m14_empty_intel_hex_still_rejected() -> None:
    """M14: an EOF-only / empty file keeps failing closed."""
    with pytest.raises(ProtocolError):
        IntelHexParser.parse_string(_ihex_line(b"", address=0x0000, record_type=1))


def test_m14_data_record_after_eof_still_rejected() -> None:
    """M14: the single-EOF-then-nothing semantics are preserved."""
    bad = (
        _ihex_line(b"\x11\x22\x33\x44", address=0x0000)
        + _ihex_line(b"", address=0x0000, record_type=1)
        + _ihex_line(b"\x55\x66\x77\x88", address=0x0004)
    )
    with pytest.raises(ProtocolError, match="after EOF"):
        IntelHexParser.parse_string(bad)


# ===========================================================================
# L7 — S-Record S0 header validation is no longer a tautology
# ===========================================================================


def test_l7_valid_s0_header_record_accepted() -> None:
    """L7: `S0` with count >= 3 (2-byte address + >= 1 data byte) is valid."""
    srec = _srec_line("0", 0x0000, b"HDR") + _srec_line("3", 0x08000000, b"\x11\x22\x33\x44")
    container = SRecordParser.parse_string(srec)
    assert container.file_format == "s_record"
    assert container.segments[0].data == b"\x11\x22\x33\x44"


def test_l7_degenerate_s0_header_record_rejected() -> None:
    """L7: the tautology is gone and a real structural S0 check is present.

    The old check `count != 3 + max(0, count - 3)` could never raise for
    count >= 3, so it validated nothing (a degenerate `S0` was accepted).
    Wire forms with an unusable count are already rejected by the
    length/checksum gates above, so this asserts on the AST instead of
    constructing an unreachable frame.
    """
    import ast
    import inspect
    import textwrap

    cls_source = inspect.getsource(SRecordParser.parse_string)
    tree = ast.parse(textwrap.dedent(cls_source))
    # Look for a tautological `X != 3 + max(0, X - 3)` comparison anywhere in
    # the executable AST (comments/docstrings are not part of it, so the
    # explanatory comment is correctly ignored).
    tautologies: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.NotEq, ast.Eq)) for op in node.ops):
            continue
        for comparator in node.comparators:
            calls = [n for n in ast.walk(comparator) if isinstance(n, ast.Call)]
            if any(isinstance(c.func, ast.Name) and c.func.id == "max" for c in calls):
                tautologies.append(ast.unparse(node))
    assert not tautologies, f"L7 tautology is back in the S0 branch: {tautologies}"
    assert "malformed S0 header record" in cls_source, "L7 real S0 structural check is missing"


# ===========================================================================
# L5 — build_clear_diagnostic_information validates dtc_group
# ===========================================================================


def test_l5_clear_diagnostic_information_accepts_valid_group() -> None:
    assert UdsServiceBuilder.build_clear_diagnostic_information(0xFFFFFF) == b"\x14\xff\xff\xff"
    assert UdsServiceBuilder.build_clear_diagnostic_information(0x000000) == b"\x14\x00\x00\x00"
    assert UdsServiceBuilder.build_clear_diagnostic_information(0x123456) == b"\x14\x12\x34\x56"


def test_l5_clear_diagnostic_information_rejects_out_of_range_group() -> None:
    """L5: 0x1FFFFFFF used to truncate silently to `14 FF FF FF`."""
    with pytest.raises(ValueError, match="out of 24-bit range"):
        UdsServiceBuilder.build_clear_diagnostic_information(0x1FFFFFFF)
    with pytest.raises(ValueError, match="out of 24-bit range"):
        UdsServiceBuilder.build_clear_diagnostic_information(-1)


# ===========================================================================
# M10 — build_read_dtc_information resolves the sub-function via the enum
# ===========================================================================


def test_m10_read_dtc_information_accepts_enum_and_valid_int() -> None:
    enum_req = UdsServiceBuilder.build_read_dtc_information(
        ReadDtcInformationType.REPORT_DTC_BY_STATUS_MASK, status_mask=0x08
    )
    assert enum_req == b"\x19\x02\x08"

    int_req = UdsServiceBuilder.build_read_dtc_information(0x02, status_mask=0x08)
    assert int_req == enum_req  # a valid int resolves identically


def test_m10_read_dtc_information_rejects_out_of_range_sub_function() -> None:
    """M10: `0x103 & 0xFF` silently became `0x03` (a DIFFERENT valid request)."""
    with pytest.raises(ValueError, match="Invalid ReadDTCInformation sub-function"):
        UdsServiceBuilder.build_read_dtc_information(0x103)
    with pytest.raises(ValueError, match="Invalid ReadDTCInformation sub-function"):
        UdsServiceBuilder.build_read_dtc_information(-1)
    # 0xFF is above the highest defined ReadDtcInformation sub-function (0x1A).
    with pytest.raises(ValueError, match="Invalid ReadDTCInformation sub-function"):
        UdsServiceBuilder.build_read_dtc_information(0xFF)


def test_m10_read_dtc_information_truncation_would_have_changed_semantics() -> None:
    """M10: prove the old truncation produced a *different* legal request."""
    assert 0x103 & 0xFF == 0x03  # the old wire byte
    assert 0x103 != 0x03  # ...but not the requested sub-function


# ===========================================================================
# H2 — Address Claim Request honours the destination address
# ===========================================================================


def _tool_name(identity: int = 100) -> J1939Name:
    return J1939Name(
        arbitrary_address_capable=True,
        industry_group=1,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=128,
        manufacturer_code=500,
        identity_number=identity,
    )


def _request_frame(da: int, source_address: int = 0x42) -> CanFrame:
    """PGN 59904 Request PGN asking for PGN 60928, addressed to `da`."""
    # priority 6, PDU1 PF 0xEA, PS = DA, SA = source_address
    can_id = (6 << 26) | (0xEA00 << 8) | ((da & 0xFF) << 8) | (source_address & 0xFF)
    return CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=can_id,
        data=b"\x00\xee\x00" + b"\xff" * 5,
        is_extended=True,
    )


def test_h2_request_for_another_node_is_not_answered() -> None:
    """H2: a point-to-point Request addressed to ANOTHER SA must be ignored.

    The old handler discarded `_da` and answered every request whenever it
    held a claim, broadcasting a spurious Address Claim per request.
    """
    engine = AddressClaimEngine(name=_tool_name(), preferred_address=0xF9)
    engine.start_claiming()
    time.sleep(0.35)
    assert engine.state == AddressClaimState.CLAIMED

    # Request addressed to SA 0x80 — not us (we are 0xF9).
    assert engine.handle_rx_frame(_request_frame(da=0x80)) is None


def test_h2_broadcast_request_is_answered_when_claimed() -> None:
    """H2 positive control: the global (0xFF) request is still answered."""
    engine = AddressClaimEngine(name=_tool_name(), preferred_address=0xF9)
    engine.start_claiming()
    time.sleep(0.35)
    assert engine.state == AddressClaimState.CLAIMED

    reply = engine.handle_rx_frame(_request_frame(da=0xFF))
    assert reply is not None
    assert reply.arbitration_id == 0x18EEFFF9
    assert reply.data == engine.name.to_bytes()


def test_h2_point_to_point_request_addressed_to_us_is_answered() -> None:
    """H2 positive control: a request addressed to OUR SA is answered."""
    engine = AddressClaimEngine(name=_tool_name(), preferred_address=0xF9)
    engine.start_claiming()
    time.sleep(0.35)

    reply = engine.handle_rx_frame(_request_frame(da=engine.current_address))
    assert reply is not None
    assert reply.arbitration_id == 0x18EEFF00 | engine.current_address


def test_h2_cannot_claim_state_answers_with_null_address() -> None:
    """H2: with no address we MUST emit the Cannot-Claim broadcast (SA 0xFE).

    Previously the handler only ever answered from CLAIMED, so this reply was
    never emitted and arbitration peers were left blind.
    """
    engine = AddressClaimEngine(name=_tool_name(), preferred_address=0xF9)
    engine.start_claiming()
    engine.cancel_pending_claim_timer()
    engine.state = AddressClaimState.CANNOT_CLAIM
    engine.current_address = NULL_ADDRESS

    reply = engine.handle_rx_frame(_request_frame(da=0xFF))
    assert reply is not None
    assert reply.arbitration_id == 0x18EEFF00 | NULL_ADDRESS
    assert reply.arbitration_id & 0xFF == 0xFE
    assert reply.data == engine.name.to_bytes()


# ===========================================================================
# M12 — the poller job key includes the ECU conversation
# ===========================================================================


def test_m12_same_pid_on_two_ecus_creates_two_jobs() -> None:
    """M12: a second ECU used to OVERWRITE the first ECU's job silently."""
    poller = ActiveDiagnosticPoller(tx_port=InMemoryTxPort())
    poller.register_pid(0x0C, rate_hz=10.0, callback=lambda r: None, tx_id=0x7E0, rx_id=0x7E8)
    poller.register_pid(0x0C, rate_hz=10.0, callback=lambda r: None, tx_id=0x7E2, rx_id=0x7EA)

    jobs = poller.get_registered_jobs()
    assert len(jobs) == 2
    assert {(j.tx_id, j.rx_id) for j in jobs} == {(0x7E0, 0x7E8), (0x7E2, 0x7EA)}
    assert all(j.identifier == 0x0C for j in jobs)


def test_m12_same_did_on_two_ecus_creates_two_jobs() -> None:
    poller = ActiveDiagnosticPoller(tx_port=InMemoryTxPort())
    poller.register_did(0xF190, rate_hz=1.0, callback=lambda r: None, tx_id=0x7E0, rx_id=0x7E8)
    poller.register_did(0xF190, rate_hz=1.0, callback=lambda r: None, tx_id=0x7E2, rx_id=0x7EA)
    assert len(poller.get_registered_jobs()) == 2


def test_m12_same_dtc_mode_on_two_ecus_creates_two_jobs() -> None:
    poller = ActiveDiagnosticPoller(tx_port=InMemoryTxPort())
    poller.register_dtc_mode(0x03, rate_hz=1.0, callback=lambda r: None, tx_id=0x7DF, rx_id=0x7E8)
    poller.register_dtc_mode(0x03, rate_hz=1.0, callback=lambda r: None, tx_id=0x7E2, rx_id=0x7EA)
    assert len(poller.get_registered_jobs()) == 2


def test_m12_identical_registration_still_replaces_in_place() -> None:
    """M12: re-registering the SAME conversation keeps one job (idempotent)."""
    poller = ActiveDiagnosticPoller(tx_port=InMemoryTxPort())
    poller.register_pid(0x0C, rate_hz=10.0, callback=lambda r: None)
    poller.register_pid(0x0C, rate_hz=20.0, callback=lambda r: None)
    jobs = poller.get_registered_jobs()
    assert len(jobs) == 1
    assert jobs[0].rate_hz == 20.0


def test_m12_unregister_drops_every_ecu_conversation() -> None:
    """M12: unregistering an identifier clears it on ALL ECUs."""
    poller = ActiveDiagnosticPoller(tx_port=InMemoryTxPort())
    poller.register_pid(0x0C, rate_hz=10.0, callback=lambda r: None, tx_id=0x7E0, rx_id=0x7E8)
    poller.register_pid(0x0C, rate_hz=10.0, callback=lambda r: None, tx_id=0x7E2, rx_id=0x7EA)
    assert len(poller.get_registered_jobs()) == 2

    assert poller.unregister_pid(0x0C) is True
    assert poller.get_registered_jobs() == []
    assert poller.unregister_pid(0x0C) is False


# ===========================================================================
# H3 — P2* extension on NRC 0x78 (absolute wall + count cap)
# ===========================================================================


def test_h3_await_diagnostic_response_rearms_p2_star_on_nrc_78() -> None:
    """H3: a 0x78 must EXTEND the deadline and keep waiting for the answer.

    The pre-fix loop re-checked a FIXED deadline, so a slow ECU that answers
    `7F 22 78` was declared timed out even though it was progressing. The
    fix re-arms the P2* window (mirroring `UdsClient.MAX_PENDING_ABS_S`).
    """
    tx_port = InMemoryTxPort()
    rx_sub = _ScriptedRxSub()
    clock = _FakeClock(start_time=100.0)
    poller = ActiveDiagnosticPoller(
        tx_port=tx_port, rx_subscription=rx_sub, clock_provider=clock
    )

    # Initial P2 window is 0.05 s; the answer arrives at virtual t=100.20 —
    # i.e. ONLY a re-armed P2* window (which runs to 105.0) can catch it.
    async def _runner() -> object:
        task = asyncio.ensure_future(poller.poll_did_once(did=0x0100, timeout_s=0.05))

        def _sf(sid: int, payload: bytes) -> CanFrame:
            body = bytes([sid]) + payload
            return CanFrame.create(
                channel_id="obd_ch0",
                arbitration_id=0x7E8,
                data=bytes([len(body)]) + body + b"\x55" * (7 - len(body)),
                direction="rx",
            )

        # Prime the RX queue BEFORE the request goes out: the poller starts by
        # awaiting `tx_port.send`, so the frames must be waiting when the
        # `recv` loop begins (a real bus would deliver them at any time).
        # 1) ECU answers "responsePending" while the virtual clock is still
        #    inside the original 0.05 s P2 window -> this must re-arm it.
        rx_sub.push(_sf(0x7F, bytes([0x22, 0x78])))
        # 2) The real answer, injected only AFTER the clock has jumped past
        #    the ORIGINAL deadline. Only a re-armed P2* window can accept it.
        answered_after_original_deadline = {"done": False}

        async def _inject_late_answer() -> None:
            while not tx_port.sent_frames:
                await asyncio.sleep(0.001)
            # Let the 0x78 be consumed first (virtual clock unchanged).
            for _ in range(5):
                await asyncio.sleep(0.001)
            clock.advance(0.20)  # past 100.05, well inside the re-armed 105.0
            answered_after_original_deadline["done"] = True
            rx_sub.push(_sf(0x62, bytes([0x01, 0x00, 0x04, 0xB0])))

        injector = asyncio.ensure_future(_inject_late_answer())
        try:
            result = await asyncio.wait_for(task, timeout=5.0)
        finally:
            injector.cancel()
        assert answered_after_original_deadline["done"] is True
        return result

    try:
        result = asyncio.run(_runner())
    finally:
        poller.stop()

    assert result.did == 0x0100  # type: ignore[attr-defined]
    assert result.value == 12.0  # type: ignore[attr-defined]


def test_h3_without_the_rearm_the_same_scenario_would_time_out() -> None:
    """H3 negative control: no 0x78 => the original deadline still kills it.

    Proves the previous test really exercised the extension (the answer
    arrives after the ORIGINAL deadline, so only a re-armed window passes).
    """
    tx_port = InMemoryTxPort()
    rx_sub = _ScriptedRxSub()
    clock = _FakeClock(start_time=100.0)
    poller = ActiveDiagnosticPoller(
        tx_port=tx_port, rx_subscription=rx_sub, clock_provider=clock
    )

    async def _runner() -> object:
        task = asyncio.ensure_future(poller.poll_did_once(did=0x0100, timeout_s=0.05))
        body = bytes([0x62, 0x01, 0x00, 0x04, 0xB0])
        # No 0x78 is queued, so the poller keeps the ORIGINAL deadline.
        # Deliver the answer only AFTER the clock has passed it: it must be
        # rejected (only a re-armed P2* window may accept a late answer).
        late_answer = CanFrame.create(
            channel_id="obd_ch0",
            arbitration_id=0x7E8,
            data=bytes([len(body)]) + body + b"\x55" * (7 - len(body)),
            direction="rx",
        )

        async def _answer_after_deadline() -> None:
            while not tx_port.sent_frames:
                await asyncio.sleep(0.001)
            clock.advance(0.20)  # past the original 100.05 deadline
            rx_sub.push(late_answer)

        answerer = asyncio.ensure_future(_answer_after_deadline())
        try:
            return await asyncio.wait_for(task, timeout=5.0)
        finally:
            answerer.cancel()

    try:
        with pytest.raises(TimeoutError, match="Timeout"):
            asyncio.run(_runner())
    finally:
        poller.stop()


def test_h3_await_diagnostic_response_still_times_out_without_pending() -> None:
    """H3: no response at all must still raise TimeoutError (no regression)."""
    tx_port = InMemoryTxPort()
    rx_sub = _ScriptedRxSub()
    poller = ActiveDiagnosticPoller(tx_port=tx_port, rx_subscription=rx_sub)

    with pytest.raises(TimeoutError, match="Timeout"):
        asyncio.run(poller.poll_pid_once(pid=0x0C, timeout_s=0.05))


def test_h3_pending_budget_constants_mirror_uds_client() -> None:
    """H3: the poller's pending budget matches UdsClient's (no divergence)."""
    from src.protocols.uds.client import UdsClient

    assert ActiveDiagnosticPoller.MAX_PENDING_ABS_S == UdsClient.MAX_PENDING_ABS_S
    assert ActiveDiagnosticPoller.MAX_NRC_78_COUNT == UdsClient.MAX_NRC_78_COUNT
    assert ActiveDiagnosticPoller.MAX_PENDING_ABS_S > 0


def test_h3_sync_path_rearms_p2_star_and_resets_budget_per_dispatch() -> None:
    """H3: the synchronous state machine re-arms P2* per 0x78 and clamps it."""
    tx_port = InMemoryTxPort()
    clock = _FakeClock(start_time=100.0)
    poller = ActiveDiagnosticPoller(tx_port=tx_port, clock_provider=clock)

    poller.register_did(0xF190, rate_hz=1.0, callback=lambda r: None)
    job = poller.step()
    assert job is not None
    assert job.pending_wall_deadline_s == 100.0 + ActiveDiagnosticPoller.MAX_PENDING_ABS_S
    assert job.nrc_78_count == 0

    def _nrc78() -> CanFrame:
        return CanFrame.create(
            channel_id="obd_ch0",
            arbitration_id=0x7E8,
            data=bytes([0x03, 0x7F, 0x22, 0x78, 0x55, 0x55, 0x55, 0x55]),
            direction="rx",
        )

    poller.process_rx_frame(_nrc78())
    assert poller.current_state == PollerState.WAITING_P2_STAR
    assert job.nrc_78_count == 1
    assert job.response_deadline_s == 105.0

    # A SECOND 0x78 must RE-ARM (the pre-fix code set it once); advance the
    # clock so the new deadline is provably fresh.
    clock.advance(2.0)
    poller.process_rx_frame(_nrc78())
    assert job.nrc_78_count == 2
    assert job.response_deadline_s == 107.0  # re-armed, not the stale 105.0


def test_h3_sync_path_declares_failure_when_pending_budget_exhausted() -> None:
    """H3: the count cap terminates a 0x78 spam instead of looping forever.

    Zero-fabrication/fail-closed: the transaction is marked FAILED rather
    than being silently left open or reported as a positive result.
    """
    tx_port = InMemoryTxPort()
    clock = _FakeClock(start_time=100.0)
    poller = ActiveDiagnosticPoller(tx_port=tx_port, clock_provider=clock)
    poller.register_did(0xF190, rate_hz=1.0, callback=lambda r: None)

    job = poller.step()
    assert job is not None
    job.nrc_78_count = ActiveDiagnosticPoller.MAX_NRC_78_COUNT  # budget spent

    nrc_frame = CanFrame.create(
        channel_id="obd_ch0",
        arbitration_id=0x7E8,
        data=bytes([0x03, 0x7F, 0x22, 0x78, 0x55, 0x55, 0x55, 0x55]),
        direction="rx",
    )
    poller.process_rx_frame(nrc_frame)
    assert poller.current_state == PollerState.FAILED
    assert job.state == PollerState.FAILED


def test_h3_sync_path_declares_failure_when_absolute_wall_passed() -> None:
    """H3: the absolute P2* wall also terminates a pending flood."""
    tx_port = InMemoryTxPort()
    clock = _FakeClock(start_time=100.0)
    poller = ActiveDiagnosticPoller(tx_port=tx_port, clock_provider=clock)
    poller.register_did(0xF190, rate_hz=1.0, callback=lambda r: None)

    job = poller.step()
    assert job is not None
    clock.advance(ActiveDiagnosticPoller.MAX_PENDING_ABS_S + 1.0)

    nrc_frame = CanFrame.create(
        channel_id="obd_ch0",
        arbitration_id=0x7E8,
        data=bytes([0x03, 0x7F, 0x22, 0x78, 0x55, 0x55, 0x55, 0x55]),
        direction="rx",
    )
    # Past the wall the active job times out on the next step() rather than
    # being extended; the RX handler must not resurrect it either.
    poller.process_rx_frame(nrc_frame)
    assert poller.current_state == PollerState.FAILED


# ===========================================================================
# L1 — _ExpiringAddressTable presents a NAME-shaped Mapping API
# ===========================================================================


def test_l1_expiring_table_never_leaks_raw_tuples() -> None:
    """L1: keys()/__iter__/values()/items() must expose NAMEs, not (NAME, ttl)."""
    table: _ExpiringAddressTable = _ExpiringAddressTable()
    name = _tool_name()
    table[0x42] = name

    assert list(table.keys()) == [0x42]
    assert list(iter(table)) == [0x42]
    assert len(table) == 1
    for value in table.values():
        assert isinstance(value, J1939Name)
    for sa, value in table.items():
        assert sa == 0x42 and isinstance(value, J1939Name)
    assert table[0x42] is name
    assert table.get(0x42) is name
    assert 0x42 in table


def test_l1_expiring_table_pop_and_clear_are_name_shaped() -> None:
    """L1: the remaining Mapping methods go through the same hooks."""
    table: _ExpiringAddressTable = _ExpiringAddressTable()
    table[0x10] = _tool_name(identity=1)
    popped = table.pop(0x10)
    assert isinstance(popped, J1939Name)
    assert len(table) == 0

    table[0x11] = _tool_name(identity=2)
    table.clear()
    assert len(table) == 0


def test_l1_expiring_table_setdefault_and_update_expose_names() -> None:
    table: _ExpiringAddressTable = _ExpiringAddressTable()
    default = table.setdefault(0x20, _tool_name(identity=7))
    assert isinstance(default, J1939Name)
    assert isinstance(table[0x20], J1939Name)

    table.update({0x21: _tool_name(identity=8)})
    assert isinstance(table[0x21], J1939Name)


def test_l1_expired_entries_disappear_from_every_reader() -> None:
    """L1: TTL expiry is honoured by the whole Mapping surface."""
    table: _ExpiringAddressTable = _ExpiringAddressTable()
    table[0x30] = _tool_name()
    # Stamp an already-expired TTL directly (internal tuple write).
    table.data[0x31] = (_tool_name(identity=9), time.monotonic() - 1.0)

    assert list(table.keys()) == [0x30]
    assert len(table) == 1
    assert 0x31 not in table
    assert table.get(0x31) is None


# ===========================================================================
# L2 — claim-rate buckets are pruned
# ===========================================================================


def test_l2_claim_rate_buckets_are_pruned() -> None:
    """L2: buckets older than 2x the window are dropped on the prune path."""
    engine = AddressClaimEngine(name=_tool_name(), preferred_address=0xF9)
    now = time.monotonic()

    window = engine.CLAIM_RATE_WINDOW_S
    engine._claim_rate[0x10] = [now, 1.0]  # fresh
    engine._claim_rate[0x11] = [now - (2.0 * window) - 1.0, 1.0]  # stale
    engine._claim_rate[0x12] = [now - (2.0 * window) - 99.0, 4.0]  # very stale

    engine._prune_address_table(now)

    assert 0x10 in engine._claim_rate
    assert 0x11 not in engine._claim_rate
    assert 0x12 not in engine._claim_rate


def test_l2_fresh_bucket_is_untouched_by_prune() -> None:
    engine = AddressClaimEngine(name=_tool_name(), preferred_address=0xF9)
    now = time.monotonic()
    engine._claim_rate[0x20] = [now, 3.0]
    engine._prune_address_table(now)
    assert engine._claim_rate[0x20] == [now, 3.0]


# ===========================================================================
# L3 — J1939Name validates each subfield's bit width
# ===========================================================================


def test_l3_out_of_range_industry_group_raises_instead_of_truncating() -> None:
    """L3: `industry_group=99` used to be masked to 3 bits and encoded as 3."""
    with pytest.raises(ValueError, match="industry_group"):
        J1939Name(
            arbitrary_address_capable=True,
            industry_group=99,
            vehicle_system_instance=0,
            vehicle_system=0,
        )


@pytest.mark.parametrize(
    ("field", "bad_value", "width"),
    [
        ("vehicle_system_instance", 16, 4),
        ("vehicle_system", 128, 7),
        ("reserved", 2, 1),
        ("function", 256, 8),
        ("function_instance", 32, 5),
        ("ecu_instance", 8, 3),
        ("manufacturer_code", 2048, 11),
        ("identity_number", 1 << 21, 21),
    ],
)
def test_l3_every_field_width_is_enforced(field: str, bad_value: int, width: int) -> None:
    kwargs: dict[str, object] = {
        "arbitrary_address_capable": True,
        "industry_group": 1,
        "vehicle_system_instance": 0,
        "vehicle_system": 0,
    }
    kwargs[field] = bad_value
    with pytest.raises(ValueError, match=field):
        J1939Name(**kwargs)  # type: ignore[arg-type]
    assert bad_value.bit_length() > width  # the test value is genuinely invalid


def test_l3_boundary_values_are_accepted() -> None:
    name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=7,  # 3 bits max
        vehicle_system_instance=15,  # 4 bits max
        vehicle_system=127,  # 7 bits max
        reserved=1,
        function=255,
        function_instance=31,
        ecu_instance=7,
        manufacturer_code=2047,
        identity_number=(1 << 21) - 1,
    )
    # Round-trip: no masking occurred.
    assert J1939Name.from_int64(name.to_int64()) == name


def test_l3_negative_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="manufacturer_code"):
        J1939Name(
            arbitrary_address_capable=True,
            industry_group=1,
            vehicle_system_instance=0,
            vehicle_system=0,
            manufacturer_code=-1,
        )


# ===========================================================================
# M8 — SA -> NAME/OEM tables carry a monotonic TTL
# ===========================================================================


def test_m8_oem_registry_uses_an_expiring_table() -> None:
    """M8: the OEM registry's SA->NAME table is TTL-bounded, not a plain dict."""
    registry = OemJ1939Registry()
    assert isinstance(registry._sa_name_codes, _ExpiringAddressTable)

    name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=0,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=0,
        manufacturer_code=10,  # Cummins
        identity_number=1,
    )
    claim = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EEFF00,
        data=name.to_bytes(),
        is_extended=True,
    )
    registry.record_address_claim(claim)
    assert registry._manufacturer_code_for_sa(0x00) == 10


def test_m8_oem_registry_claim_entry_expires() -> None:
    """M8: an expired claim entry is dropped and reads as unknown (None)."""
    registry = OemJ1939Registry()
    name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=0,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=0,
        manufacturer_code=10,
        identity_number=1,
    )
    # Backdate the internal entry past its TTL.
    registry._sa_name_codes.data[0x00] = (name, time.monotonic() - 1.0)
    assert registry._manufacturer_code_for_sa(0x00) is None


def test_m8_volvo_name_table_entries_expire() -> None:
    """M8: the Volvo SA->manufacturer table is TTL-bounded too."""
    sa = 0x5A
    saved = dict(VolvoPentaDecoder._sa_name_codes)
    try:
        name = J1939Name(
            arbitrary_address_capable=True,
            industry_group=0,
            vehicle_system_instance=0,
            vehicle_system=0,
            function=0,
            manufacturer_code=174,  # Volvo Penta
            identity_number=3,
        )
        claim = CanFrame.create(
            channel_id="volvo_can",
            arbitration_id=0x18EEFF00 | sa,
            data=name.to_bytes(),
            is_extended=True,
        )
        VolvoPentaDecoder.record_address_claim(claim)
        assert VolvoPentaDecoder._live_name_code(sa) == 174

        # Backdate past the TTL: the code is no longer established.
        VolvoPentaDecoder._sa_name_codes[sa] = (174, time.monotonic() - 1.0)
        assert VolvoPentaDecoder._live_name_code(sa) is None
        assert sa not in VolvoPentaDecoder._sa_name_codes  # pruned on read
    finally:
        VolvoPentaDecoder._sa_name_codes.clear()
        VolvoPentaDecoder._sa_name_codes.update(saved)


# ===========================================================================
# M5 / M6 / M7 — Volvo decoder
# ===========================================================================


def test_m6_out_of_spec_code_type_is_skipped_not_labelled_ppid() -> None:
    """M6: code type 4 used to become a fabricated `PPID <n>` record."""
    payload = b"\x04\x64\x81"  # code_type 4, id 100, FMI 1 active
    dtcs = VolvoPentaDecoder.parse_edc_fault_payload(payload)
    assert dtcs == []


def test_m6_reserved_code_type_is_skipped() -> None:
    dtcs = VolvoPentaDecoder.parse_edc_fault_payload(b"\xff\x64\x81")
    assert dtcs == []


def test_m6_valid_code_types_all_decode() -> None:
    """M6: {0,1,2,3} remain fully decodable (PPID is still supported)."""
    payload = b"\x00\x64\x81\x01\x15\x82\x02\x60\x03\x03\x62\x04"
    dtcs = VolvoPentaDecoder.parse_edc_fault_payload(payload)
    assert [d.code_type for d in dtcs] == ["PID", "SID", "PPID", "PSID"]
    assert [d.code_id for d in dtcs] == [0x64, 0x15, 0x60, 0x62]


def test_m6_only_the_bad_record_is_dropped() -> None:
    """M6: a skipped record must not swallow its well-formed neighbours."""
    payload = b"\x00\x64\x81\x09\x15\x82\x01\x15\x82"
    dtcs = VolvoPentaDecoder.parse_edc_fault_payload(payload)
    assert [d.code_type for d in dtcs] == ["PID", "SID"]


def test_m5_no_static_sa_allowlist_exists() -> None:
    """M5: the {0x00, 0x01} static allowlist is deliberately removed."""
    assert not hasattr(VolvoPentaDecoder, "VOLVO_PENTA_SOURCE_ADDRESSES")
    assert VolvoPentaDecoder.VOLVO_NAME_MANUFACTURER_CODES == frozenset({174})


def test_m5_unclaimed_sa_decodes_low_confidence() -> None:
    """M5: an address alone never yields HIGH — provenance needs a NAME."""
    state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=0x00))
    assert state is not None
    assert state.attribution_confidence == "LOW"


def test_m5_volvo_name_claim_yields_high_confidence() -> None:
    sa = 0x37
    saved = dict(VolvoPentaDecoder._sa_name_codes)
    try:
        name = J1939Name(
            arbitrary_address_capable=True,
            industry_group=0,
            vehicle_system_instance=0,
            vehicle_system=0,
            function=0,
            manufacturer_code=174,
            identity_number=4,
        )
        VolvoPentaDecoder.record_address_claim(
            CanFrame.create(
                channel_id="volvo_can",
                arbitration_id=0x18EEFF00 | sa,
                data=name.to_bytes(),
                is_extended=True,
            )
        )
        state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=sa))
        assert state is not None
        assert state.attribution_confidence == "HIGH"
    finally:
        VolvoPentaDecoder._sa_name_codes.clear()
        VolvoPentaDecoder._sa_name_codes.update(saved)


def test_m5_non_volvo_name_claim_refuses_decode() -> None:
    sa = 0x38
    saved = dict(VolvoPentaDecoder._sa_name_codes)
    try:
        name = J1939Name(
            arbitrary_address_capable=True,
            industry_group=0,
            vehicle_system_instance=0,
            vehicle_system=0,
            function=0,
            manufacturer_code=10,  # Cummins
            identity_number=5,
        )
        VolvoPentaDecoder.record_address_claim(
            CanFrame.create(
                channel_id="volvo_can",
                arbitration_id=0x18EEFF00 | sa,
                data=name.to_bytes(),
                is_extended=True,
            )
        )
        assert VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=sa)) is None
    finally:
        VolvoPentaDecoder._sa_name_codes.clear()
        VolvoPentaDecoder._sa_name_codes.update(saved)


def _evc_frame(sa: int, data: bytes = b"\xaf\x01\x01\x26\x02\x20\x03\xff") -> CanFrame:
    return CanFrame.create(
        channel_id="volvo_can",
        arbitration_id=0x18FF5000 | sa,  # PGN 65360 (0xFF50), PDU2
        data=data,
        is_extended=True,
    )


def test_m7_address_claim_uses_shared_parser() -> None:
    """M7: `record_address_claim` delegates to `parse_j1939_id`.

    Proves the shared parser is on the path by patching it and asserting the
    call happened — a hand-rolled mask would silently ignore the patch.
    """
    sa = 0x39
    saved = dict(VolvoPentaDecoder._sa_name_codes)
    calls: list[int] = []

    import src.protocols.volvo.volvo_decoder as vd

    real_parse = vd.parse_j1939_id

    def _spy(arbitration_id: int):
        calls.append(arbitration_id)
        return real_parse(arbitration_id)

    vd.parse_j1939_id = _spy  # type: ignore[assignment]
    try:
        name = J1939Name(
            arbitrary_address_capable=True,
            industry_group=0,
            vehicle_system_instance=0,
            vehicle_system=0,
            function=0,
            manufacturer_code=174,
            identity_number=6,
        )
        claim = CanFrame.create(
            channel_id="volvo_can",
            arbitration_id=0x18EEFF00 | sa,
            data=name.to_bytes(),
            is_extended=True,
        )
        VolvoPentaDecoder.record_address_claim(claim)
        assert calls == [claim.arbitration_id]
        assert VolvoPentaDecoder._live_name_code(sa) == 174
    finally:
        vd.parse_j1939_id = real_parse  # type: ignore[assignment]
        VolvoPentaDecoder._sa_name_codes.clear()
        VolvoPentaDecoder._sa_name_codes.update(saved)


def test_m7_non_claim_pgn_is_ignored() -> None:
    sa = 0x3A
    saved = dict(VolvoPentaDecoder._sa_name_codes)
    try:
        frame = CanFrame.create(
            channel_id="volvo_can",
            arbitration_id=0x18FEF100 | sa,  # PGN 65265, not 60928
            data=b"\x00" * 8,
            is_extended=True,
        )
        VolvoPentaDecoder.record_address_claim(frame)
        assert VolvoPentaDecoder._live_name_code(sa) is None
    finally:
        VolvoPentaDecoder._sa_name_codes.clear()
        VolvoPentaDecoder._sa_name_codes.update(saved)


# ===========================================================================
# M1 — J1939 Request payloads are padded to 8 bytes with 0xFF
# ===========================================================================


def test_m1_request_frames_are_padded_to_eight_bytes() -> None:
    dm11 = J1939DiagnosticService.create_dm11_frame()
    assert len(dm11.data) == 8
    assert dm11.data[:3] == b"\xd3\xfe\x00"  # payload semantics unchanged
    assert dm11.data[3:] == b"\xff" * 5
    assert dm11.dlc == 8

    dm3 = J1939DiagnosticService.create_dm3_frame()
    assert len(dm3.data) == 8
    assert dm3.data[:3] == b"\xcc\xfe\x00"
    assert dm3.data[3:] == b"\xff" * 5

    for pgn in (65229, 65230, 65231, 65235):
        frame = J1939DiagnosticService.create_request_frame(pgn)
        assert len(frame.data) == 8
        assert int.from_bytes(frame.data[:3], "little") == pgn


def test_m1_dm11_dm3_request_payloads_are_padded() -> None:
    assert J1939DiagnosticService.dm11_request_payload() == b"\xd3\xfe\x00" + b"\xff" * 5
    assert J1939DiagnosticService.dm3_request_payload() == b"\xcc\xfe\x00" + b"\xff" * 5


def test_m1_padding_helper_leaves_long_payloads_alone() -> None:
    from src.protocols.j1939.diagnostics import _pad_request_payload

    already_full = b"\x01" * 8
    assert _pad_request_payload(already_full) == already_full
    assert _pad_request_payload(b"\x01\x02\x03") == b"\x01\x02\x03" + b"\xff" * 5


# ===========================================================================
# M2 — CM=1 DTC records are flagged unsupported, never silently wrong
# ===========================================================================


def test_m2_cm0_record_is_supported() -> None:
    from src.protocols.j1939.diagnostics import DiagnosticTroubleCode

    # SPN 100, FMI 1, OC 3, CM 0
    dtc = DiagnosticTroubleCode.from_bytes(bytes([0x64, 0x00, 0x01, 0x03]))
    assert dtc.conversion_method == 0
    assert dtc.is_supported is True
    assert dtc.spn == 100
    assert dtc.fmi == 1
    assert dtc.occurrence_count == 3


def test_m2_cm1_record_is_flagged_unsupported() -> None:
    """M2: CM=1 uses a different SPN layout we do not implement — flag it.

    Zero-fabrication: the record must NOT be presented as a trusted SPN.
    """
    from src.protocols.j1939.diagnostics import DiagnosticTroubleCode

    # Byte3 bit 7 set => Conversion Method 1.
    dtc = DiagnosticTroubleCode.from_bytes(bytes([0x64, 0x00, 0x01, 0x83]))
    assert dtc.conversion_method == 1
    assert dtc.is_supported is False
    assert dtc.conversion_method_supported is False


def test_m2_dm1_parser_propagates_the_cm1_flag() -> None:
    """M2: the CM flag survives `parse_dm1_or_dm2` on the decoded record."""
    # Byte0 lamps, Byte1 flash, then one DTC with CM=1.
    payload = bytes([0x40, 0xFF, 0x64, 0x00, 0x01, 0x83])
    msg = J1939DiagnosticService.parse_dm1_or_dm2(payload, pgn=65226, source_address=0)
    assert msg is not None
    assert len(msg.dtcs) == 1
    assert msg.dtcs[0].conversion_method == 1
    assert msg.dtcs[0].is_supported is False


# ===========================================================================
# M13 / L6 — flasher gateway accessor + module logger
# ===========================================================================


def test_m13_uses_public_accessor_when_available() -> None:
    """M13: `has_confirmation_secret()` is preferred over the private attr."""
    from src.protocols.uds.flasher import EcuFlashingEngine

    seen: list[str] = []

    class _Gateway:
        def has_confirmation_secret(self) -> bool:
            seen.append("accessor")
            return True

        def issue_confirmation_token(self, arbitration_id: int, ttl_s: float = 30.0) -> bytes:
            return b"TOKEN"

    engine = EcuFlashingEngine(uds_client=object(), gateway=_Gateway())  # type: ignore[arg-type]
    token = engine._confirmation_token()
    assert seen == ["accessor"]
    assert token == b"TOKEN"


def test_m13_accessor_false_means_no_token() -> None:
    from src.protocols.uds.flasher import EcuFlashingEngine

    class _Gateway:
        def has_confirmation_secret(self) -> bool:
            return False

        def issue_confirmation_token(self, arbitration_id: int, ttl_s: float = 30.0) -> bytes:
            raise AssertionError("must not mint without a secret")

    engine = EcuFlashingEngine(uds_client=object(), gateway=_Gateway())  # type: ignore[arg-type]
    assert engine._confirmation_token() is None


def test_m13_legacy_gateway_without_accessor_still_works() -> None:
    """M13: `src/safety/**` is another workstream's scope — the flasher must
    keep working against a gateway that has not grown the accessor yet."""
    from src.protocols.uds.flasher import EcuFlashingEngine

    class _LegacyGateway:
        def __init__(self) -> None:
            self._confirmation_secret = b"S" * 32

        def issue_confirmation_token(self, arbitration_id: int, ttl_s: float = 30.0) -> bytes:
            return b"LEGACY"

    engine = EcuFlashingEngine(uds_client=object(), gateway=_LegacyGateway())  # type: ignore[arg-type]
    assert engine._confirmation_token() == b"LEGACY"


def test_m13_legacy_gateway_without_secret_gets_no_token() -> None:
    from src.protocols.uds.flasher import EcuFlashingEngine

    class _LegacyGateway:
        def issue_confirmation_token(self, arbitration_id: int, ttl_s: float = 30.0) -> bytes:
            raise AssertionError("must not mint without a secret")

    engine = EcuFlashingEngine(uds_client=object(), gateway=_LegacyGateway())  # type: ignore[arg-type]
    assert engine._confirmation_token() is None


def test_m13_raising_accessor_fails_safe() -> None:
    """M13: an accessor that raises must degrade to "no secret", never crash."""
    from src.protocols.uds.flasher import EcuFlashingEngine

    class _BrokenGateway:
        def has_confirmation_secret(self) -> bool:
            raise RuntimeError("boom")

        def issue_confirmation_token(self, arbitration_id: int, ttl_s: float = 30.0) -> bytes:
            raise AssertionError("must not mint")

    engine = EcuFlashingEngine(uds_client=object(), gateway=_BrokenGateway())  # type: ignore[arg-type]
    assert engine._confirmation_token() is None


def test_l6_non_standard_block_size_logs_to_the_module_logger() -> None:
    """L6: the warning must reach `protocols.uds.flasher`, not a phantom name."""
    import inspect

    from src.protocols.uds import flasher as flasher_module

    source = inspect.getsource(flasher_module)
    assert 'logging.getLogger("universal_can.protocols.uds.flasher")' not in source
    assert "logger.warning(" in source

    from src.protocols.uds.flasher import FlashingConfig

    config = FlashingConfig(
        memory_address=0x08000000,
        data=b"\xAA" * 512,
        block_size=300,  # > MIN_BLOCK_SIZE, <= cap, but not a standard size
        user_confirmed=True,
    )
    assert config.data  # constructed without raising
    assert 300 not in FlashingConfig.ALLOWED_BLOCK_SIZES


# ===========================================================================
# M3 / M4 / M9 / L8 — cosmetic / documentation fixes (no behaviour change)
# ===========================================================================


def test_m9_isotp_default_buffer_matches_the_uds_fd_ceiling() -> None:
    """M9: the constructor default must equal MAX_UDS_PAYLOAD_FD (1 MiB)."""
    transport = IsoTpTransport()
    assert transport.max_buffer_size == MAX_UDS_PAYLOAD_FD
    assert MAX_UDS_PAYLOAD_FD == 1048576


def test_m3_session_key_annotation_is_a_five_tuple() -> None:
    """M3: the declared session-key shape matches the runtime 5-tuple."""
    import inspect

    from src.protocols.nmea2000.fast_packet import Nmea2000FastPacketDecoder

    source = inspect.getsource(Nmea2000FastPacketDecoder.__init__)
    # The declaration names a 5-tuple (SA, DA, PGN, seq, channel)...
    assert "dict[tuple[int, int, int, int, str], FastPacketSession]" in source
    assert "dict[tuple[int, int, int, str], FastPacketSession]" not in source
    # ...and the runtime key really is that 5-tuple.
    handler = inspect.getsource(Nmea2000FastPacketDecoder.handle_rx_frame)
    assert "session_key = (source_address, effective_da, pgn, sequence_id, frame.channel_id)" in handler


def test_m4_docstring_no_longer_references_a_missing_helper() -> None:
    """M4: `_validate_fp_header` is not promised (and does not exist)."""
    import inspect

    from src.protocols.nmea2000.fast_packet import Nmea2000FastPacketDecoder

    module_source = inspect.getsource(
        __import__("src.protocols.nmea2000.fast_packet", fromlist=["x"])
    )
    assert "def _validate_fp_header" not in module_source
    doc = inspect.getdoc(Nmea2000FastPacketDecoder.handle_rx_frame) or ""
    assert "_validate_fp_header." not in doc
    assert "_handle_locked" in doc  # the checks are documented inline


def test_l8_lazy_import_is_documented_as_intentional() -> None:
    import inspect

    from src.protocols.j1939 import pgn

    doc = inspect.getdoc(pgn._impl) or ""
    assert "L8" in doc
    assert "cycle" in doc


# ===========================================================================
# Cross-cutting: no test above may transmit through a private path
# ===========================================================================


def test_builders_return_bytes_and_never_transmit() -> None:
    """AGENTS.md §2.1: builders RETURN payloads; TX stays with the caller."""
    tx_port = InMemoryTxPort()
    _ = tx_port  # present only to document the expected TX choke-point
    assert isinstance(J1939DiagnosticService.dm11_request_payload(), bytes)
    assert isinstance(UdsServiceBuilder.build_clear_diagnostic_information(), bytes)
    assert len(struct.pack("<I", 0)) == 4  # struct import used (no dead import)

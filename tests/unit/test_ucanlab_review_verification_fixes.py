"""Regression tests for the UCANLAB comprehensive-review verification pass.

An external code review (2026-09) made 16 findings; the verification pass
confirmed 9 real, judged 2 exaggerated, and REFUTED 5. Only the
confirmed/exaggerated-but-real items are fixed here; the refuted findings have
NO tests by design — implementing them would contradict verified behaviour.
The review artifacts themselves were working documents and were not kept in
the repository, so this docstring records the outcome rather than a file path.

Findings covered (TDD: written to fail on the pre-fix code):

  * REVIEW 1.3  MEDIUM  `rebind_bus()` must reset per-category token buckets
  * REVIEW 2.1  CRITICAL (defense-in-depth) a replay-sourced frame must never
                        elicit a J1939 TP physical TX response
  * REVIEW 2.2  HIGH    RP1210 timestamps must be monotonic (no wall-clock jump)
  * REVIEW 4.1  HIGH    `DbcBuilder` must honour `report.is_extended`
  * REVIEW 4.2  HIGH    VIN masking must be case-insensitive

Findings explicitly REFUTED (1.2, 2.3, 3.3, 6.1, 6.3) are intentionally not
tested — implementing them would contradict the verified code behaviour.
"""

from __future__ import annotations

import time

import pytest

from src.core.models.can_frame import CanFrame
from src.engine.ai.diagnostic_copilot import mask_vin_in_text
from src.hal.virtual import VirtualBus
from src.protocols.j1939.pgn import build_j1939_id
from src.protocols.j1939.transport import J1939TransportProtocol
from src.safety.estop import EmergencyStopSystem
from src.safety.gateway import TxSafetyGateway


# ---------------------------------------------------------------------------
# REVIEW 1.3 — rebind_bus must reset the per-category token buckets
# ---------------------------------------------------------------------------
def _gateway() -> tuple[TxSafetyGateway, VirtualBus]:
    bus = VirtualBus(channel_id=f"rev_vbus_{time.monotonic_ns()}")
    bus.connect()
    estop = EmergencyStopSystem(allow_self_reset=True)
    gateway = TxSafetyGateway(
        bus=bus,
        estop=estop,
        whitelist_ids={0x7E0},
        whitelist_masks=[],
        allow_legacy_boolean_confirm=True,
    )
    return gateway, bus


def test_review_1_3_rebind_bus_resets_category_budgets() -> None:
    """A drained bucket on the old link must start full on the rebound link.

    Pre-fix: `rebind_bus` cleared the sliding-window state but left
    `_budgets` untouched, so a `protocol_burst` bucket exhausted on a faulty
    connection stayed empty after reconnect and the first critical burst was
    rejected with RateLimitExceededError.
    """
    gateway, bus = _gateway()
    bucket = gateway._budgets["protocol_burst"]
    # Drain the burst bucket completely.
    for _ in range(bucket.capacity):
        assert bucket.try_consume() is True
    assert bucket.try_consume() is False, "bucket should be exhausted pre-rebind"

    new_bus = VirtualBus(channel_id=f"rev_vbus2_{time.monotonic_ns()}")
    new_bus.connect()
    gateway.rebind_bus(new_bus)

    refreshed = gateway._budgets["protocol_burst"]
    assert refreshed.capacity == bucket.capacity
    # Fresh bucket starts at capacity => the first burst token is available.
    assert refreshed.try_consume() is True, "rebind must refill the category buckets"

    # Every configured category is reset, not just the drained one.
    for name, (cap, _refill) in TxSafetyGateway.BUDGETS.items():
        assert gateway._budgets[name].capacity == cap

    new_bus.disconnect()
    bus.disconnect()


# ---------------------------------------------------------------------------
# REVIEW 2.1 — replay-sourced frames must never trigger a physical TX response
# ---------------------------------------------------------------------------
def _rts_frame(*, source: str) -> CanFrame:
    """Build a valid J1939 TP.CM RTS addressed to our SA (0xF9).

    An RTS addressed to us normally makes the TP engine emit a CTS response.
    """
    my_sa = 0xF9
    target_pgn = 65262  # ET1 — a benign PGN so only the transport path matters
    size, packets = 20, 3
    arb = build_j1939_id(pgn=60416, sa=0x00, da=my_sa, priority=6)
    data = bytes(
        [
            0x10,
            size & 0xFF,
            (size >> 8) & 0xFF,
            packets,
            target_pgn & 0xFF,
            (target_pgn >> 8) & 0xFF,
            (target_pgn >> 16) & 0xFF,
            my_sa,
        ]
    )
    return CanFrame.create(
        channel_id="replay0",
        arbitration_id=arb,
        data=data,
        is_extended=True,
        source=source,
    )


def test_review_2_1_live_rts_still_generates_cts() -> None:
    """Positive control: a LIVE RTS must still be answered (no over-blocking)."""
    tp = J1939TransportProtocol(my_address=0xF9)
    _completed, resp = tp.handle_rx_frame(_rts_frame(source="physical"))
    assert resp is not None, "a live RTS addressed to us must still be answered"
    # The CTS is a FlowControl frame. On this J1939 CMDT path the response
    # bytes are "11 03 01 ff ff fe 00 f9": data[0]=0x11 => PCI nibble 0x1
    # (FlowControl), then block size (data[1]) and STmin (data[2]). We assert
    # only the PCI class so a legitimate FlowStatus/parameter change does not
    # break this control test.
    assert resp.data[0] >> 4 == 0x1, "response must be a FlowControl frame"


def test_review_2_1_replay_rts_suppresses_cts() -> None:
    """A replay-sourced RTS must produce NO outbound frame (fail-closed)."""
    tp = J1939TransportProtocol(my_address=0xF9)
    completed, resp = tp.handle_rx_frame(
        _rts_frame(source="replay"), suppress_tx_responses=True
    )
    assert resp is None, "replay RTS must not yield a physical TX response"
    assert completed is None
    assert tp.take_pending_tx_frames() == [], "no queued overflow TX for replay"


def test_review_2_1_suppression_clears_queued_overflow() -> None:
    """Suppression must also drain any queued multi-frame overflow responses."""
    tp = J1939TransportProtocol(my_address=0xF9)
    tp.handle_rx_frame(_rts_frame(source="replay"), suppress_tx_responses=True)
    assert tp.take_pending_tx_frames() == []


# ---------------------------------------------------------------------------
# REVIEW 2.2 — RP1210 timestamps must be monotonic
# ---------------------------------------------------------------------------
def test_review_2_2_rp1210_timestamp_is_monotonic() -> None:
    """Two consecutive decodes must never regress, even under a clock step.

    Pre-fix the decoder called `time.time_ns()` per packet, which walks
    backwards on an NTP step / leap second and corrupted jitter + blackbox
    ordering.
    """
    from src.hal.rp1210.bus import RP1210Bus

    bus = RP1210Bus(client=_StubRP1210Client())  # no real DLL load
    first = bus._monotonic_timestamp_ns()
    second = bus._monotonic_timestamp_ns()
    assert second > first, "successive timestamps must strictly increase"

    # Simulate a large backwards wall-clock step: the emitted value must NOT
    # regress because it is anchored to the monotonic clock.
    bus._ts_anchor_wall_ns -= 3_600_000_000_000  # -1 hour of wall clock
    third = bus._monotonic_timestamp_ns()
    assert third > second, "a backwards wall-clock step must not regress output"

    # And the raw call sites no longer use the naive wall clock.
    import inspect

    src = inspect.getsource(RP1210Bus._decode_rp1210_packet)
    assert "time.time_ns()" not in src, "decode must use the monotonic helper"


class _StubRP1210Client:
    """Minimal stand-in so RP1210Bus construction never touches the vendor DLL."""

    def __init__(self) -> None:
        self.device_id = 1
        self.protocol = "J1939"

    def __getattr__(self, name: str):  # pragma: no cover - only used if called
        def _noop(*_args, **_kwargs):
            return None

        return _noop


# ---------------------------------------------------------------------------
# REVIEW 4.1 — DbcBuilder must honour report.is_extended
# ---------------------------------------------------------------------------
def _id_report(arbitration_id: int, is_extended: bool) -> object:
    """Build a minimal IdReport carrying one placeable 8-bit signal."""
    from src.engine.discovery.hypotheses import Hypothesis, IdReport

    return IdReport(
        arbitration_id=arbitration_id,
        frame_count=50,
        rate_hz=10.0,
        dlc=8,
        hypotheses=[
            Hypothesis(
                htype="SIGNAL",
                start_bit=0,
                length=8,
                is_little_endian=True,
                is_signed=False,
                confidence=0.9,
                status="approved",
                name="Sig",
                min_value=0.0,
                max_value=255.0,
            )
        ],
        is_extended=is_extended,
    )


def test_review_4_1_dbc_builder_honours_is_extended_flag() -> None:
    """A 29-bit ID numerically <= 0x7FF must export as an extended frame.

    Pre-fix `is_extended = report.arbitration_id > 0x7FF` mis-typed such
    frames as 11-bit standard, so the DBC never matched them in CANoe.
    """
    from src.engine.discovery.dbc_builder import DbcBuilder

    # 0x00000001 is numerically <= 0x7FF but is a 29-bit ID on the wire.
    report = _id_report(arbitration_id=0x00000001, is_extended=True)

    db = DbcBuilder.build_database([report])
    assert db.messages, "a hypothesis must yield one message"
    assert db.messages[0].is_extended_frame is True, (
        "report.is_extended=True must win over the >0x7FF heuristic"
    )


def test_review_4_1_standard_frame_still_not_extended() -> None:
    """Negative control: an 11-bit ID must stay standard."""
    from src.engine.discovery.dbc_builder import DbcBuilder

    report = _id_report(arbitration_id=0x123, is_extended=False)
    db = DbcBuilder.build_database([report])
    assert db.messages[0].is_extended_frame is False


# ---------------------------------------------------------------------------
# REVIEW 4.2 — VIN masking must be case-insensitive
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "vin",
    [
        "1HGCR2F83HA123456",  # upper (always worked)
        "1hgcr2f83ha123456",  # lower — the leak
        "1HgCr2F83hA123456",  # mixed
    ],
)
def test_review_4_2_vin_masked_regardless_of_case(vin: str) -> None:
    """Every case variant must be masked down to the last 6 characters."""
    text = f"Vehicle {vin} reported a fault."
    masked = mask_vin_in_text(text)
    assert vin not in masked, f"VIN case variant leaked: {vin!r}"
    # VIN collapse: all but the final 6 chars are replaced with '*'.
    assert "*" * 11 + vin[-6:] in masked


def test_review_4_2_non_vin_text_untouched() -> None:
    """Negative control: ordinary text must not be mangled."""
    msg = "Boost pressure nominal at 180 kPa"
    assert mask_vin_in_text(msg) == msg

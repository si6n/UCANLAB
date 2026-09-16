"""T56-C regressions — UDS NRC 0x78 absolute bound + ISO-TP side findings.

Findings (docs/review/ASAMA-4-protokoller.md):
- U-1 (KRİTİK): ``UdsClient._send_and_receive`` re-armed ``deadline`` on every
  NRC 0x78 and ``max_pending_timeout_s`` defaulted to ``None`` → an ECU that
  spams response-pending faster than P2* (5 s) looped the exchange forever,
  holding ``_operation_lock`` and blocking TesterPresent / every other UDS op.
- I-1 (ORTA): ``decode_st_min`` mapped the reserved 0xFA-0xFF range to 127 ms
  while the async ``IsoTpSender._apply_st_min`` *waited* that long per CF —
  a spoofed FC slowed a transfer to a crawl.
- I-2 (ORTA): ``IsoTpSender.send`` had no payload size cap (``segment_message``
  did) → an oversized payload produced a near-unbounded CF flood.
"""

from __future__ import annotations

import queue

import pytest

from src.core.contracts.ports import InMemoryTxPort
from src.core.errors import ProtocolError
from src.core.models.can_frame import CanFrame
from src.hal.base import AbstractBus
from src.protocols.uds.client import UdsClient
from src.protocols.uds.isotp import (
    FS_CTS,
    MAX_UDS_PAYLOAD_CLASSIC,
    PCI_FLOW_CONTROL,
    IsoTpPayloadTooLargeError,
    IsoTpSender,
    decode_st_min,
)


class _AlwaysPendingBus(AbstractBus):
    """Hostile ECU: answers ANY request with NRC 0x78 (responsePending).

    ISO-TP single frame `03 7F <sid> 78` is re-injected every ``recv()`` so
    the pending arrives far faster than the P2* (5 s) extension window.
    """

    def __init__(self, channel_id: str = "mock_pending_0") -> None:
        super().__init__(channel_id=channel_id)
        self.sent_frames: list[CanFrame] = []
        self.rx_queue: queue.Queue[CanFrame] = queue.Queue()
        self.pending_served = 0

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:
        self.sent_frames.append(frame)

    def send_sync(self, frame: CanFrame) -> None:
        self.send(frame)

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        # Every read hands back a fresh `7F 22 78` (arbitration 0x7E8).
        sid = self.sent_frames[0].data[1] if self.sent_frames else 0x22
        self.pending_served += 1
        return CanFrame.create(
            channel_id=self.channel_id,
            arbitration_id=0x7E8,
            data=bytes([0x03, 0x7F, sid, 0x78, 0x00, 0x00, 0x00, 0x00]),
        )

    def inject_rx(self, frame: CanFrame) -> None:
        self.rx_queue.put(frame)


def test_t56c_u1_pending_spam_hits_absolute_bound() -> None:
    """U-1: continuous NRC 0x78 must NOT loop forever — the absolute pending
    wall fires with ``UDS_TIMEOUT`` and the ``_operation_lock`` is released.

    BEFORE the fix ``max_absolute_deadline`` was ``None`` (default), so each
    0x78 re-armed ``deadline = now + 5 s`` and this call never returned.
    AFTER the fix ``MAX_PENDING_ABS_S`` (default 30 s) bounds the total dwell;
    the test pins the wall to 0.5 s so it stays fast.
    """
    bus = _AlwaysPendingBus()
    client = UdsClient(
        bus=bus,
        tx_port=bus,
        tx_id=0x7E0,
        rx_id=0x7E8,
        max_pending_timeout_s=0.5,
    )

    try:
        with pytest.raises(ProtocolError) as exc_info:
            client._send_and_receive(b"\x22\xf1\x90", timeout_s=5.0)
        assert exc_info.value.code == "UDS_TIMEOUT"
        # Pending genuinely re-armed at least once, then the absolute wall cut it.
        assert bus.pending_served >= 1
        # The serialized conversation lock must be free again (no wedge).
        assert client._operation_lock.acquire(blocking=False) is True
        client._operation_lock.release()
    finally:
        client.close()


def test_t56c_u1_nrc_78_count_cap_is_enforced() -> None:
    """U-1: the NRC 0x78 counter ceiling also terminates the loop.

    With a permissive time wall (no absolute timeout supplied → class default)
    the count cap alone must stop a pending flood: ``_send_and_receive`` has
    to surface ``UDS_TIMEOUT`` instead of spinning on pendings forever.
    """
    bus = _AlwaysPendingBus()
    client = UdsClient(bus=bus, tx_port=bus, tx_id=0x7E0, rx_id=0x7E8)

    # Shrink both bounds so the assertion is fast and isolated to the counter.
    client.MAX_NRC_78_COUNT = 3  # type: ignore[misc]
    client.max_pending_timeout_s = 60.0

    try:
        with pytest.raises(ProtocolError) as exc_info:
            client._send_and_receive(b"\x22\xf1\x90", timeout_s=5.0)
        assert exc_info.value.code == "UDS_TIMEOUT"
        assert exc_info.value.details["nrc_78_count"] >= 3
    finally:
        client.close()


def test_t56c_u1_default_absolute_bound_is_finite() -> None:
    """U-1: the class default is a *finite* wall, not ``None``.

    Guards the regression at the source: a future edit that drops the default
    re-opens the unbounded loop.
    """
    assert isinstance(UdsClient.MAX_PENDING_ABS_S, float)
    assert UdsClient.MAX_PENDING_ABS_S > 0
    assert isinstance(UdsClient.MAX_NRC_78_COUNT, int)
    assert UdsClient.MAX_NRC_78_COUNT > 0


# ---------------------------------------------------------------------------
# I-1 — decode_st_min reserved 0xFA-0xFF must not stall at 127 ms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw_byte", [0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF])
def test_t56c_i1_reserved_stmin_fa_ff_clamped(raw_byte: int) -> None:
    """I-1: reserved 0xFA-0xFF decodes to the 10 ms spoof cap, not 127 ms.

    BEFORE: the fallthrough ``return 127.0`` gave a spoofed FC a 127 ms stall
    per consecutive frame. AFTER: same 10 ms cap as the other reserved range.
    """
    assert decode_st_min(raw_byte) == 10.0


@pytest.mark.asyncio
async def test_t56c_i1_sender_does_not_stall_127ms_on_reserved_stmin() -> None:
    """I-1: an async sender facing FC(STmin=0xFA) must not pace 127 ms/CF.

    A 20-byte payload is FF + 2 CFs. With the pre-fix 127 ms reserve both CFs
    alone cost >250 ms; the clamped cap keeps the whole transfer well under
    the 127 ms floor of a single legacy stall.
    """
    import time

    tx_port = InMemoryTxPort()
    rx_sub = _ScriptedRxSub()
    sender = IsoTpSender(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8, n_bs_timeout_s=1.0)

    rx_sub.push(_fc_frame(st_min=0xFA, bs=0))

    started = time.monotonic()
    await sender.send(bytes(range(20)))
    elapsed = time.monotonic() - started

    assert len(tx_port.sent_frames) == 3  # FF + 2 CFs, transfer completed
    # Pre-fix each of the 2 CFs waited 127 ms (>=0.254 s total). The 10 ms cap
    # keeps both under 0.12 s even with Windows' 15 ms scheduler granularity.
    assert elapsed < 0.2, f"reserved STmin still stalls the sender ({elapsed:.3f}s)"


def _fc_frame(st_min: int, bs: int) -> CanFrame:
    data = bytes([(PCI_FLOW_CONTROL << 4) | FS_CTS, bs & 0xFF, st_min & 0xFF]) + b"\xcc" * 5
    return CanFrame.create(channel_id="uds", arbitration_id=0x7E8, data=data, is_fd=False)


class _ScriptedRxSub:
    """Minimal RxSubscription: yields queued frames in order (None otherwise)."""

    def __init__(self) -> None:
        self._frames: list[CanFrame] = []

    def push(self, frame: CanFrame) -> None:
        self._frames.append(frame)

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:  # noqa: ARG002
        if self._frames:
            return self._frames.pop(0)
        return None


# ---------------------------------------------------------------------------
# I-2 — IsoTpSender.send payload cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t56c_i2_sender_rejects_oversized_classic_payload() -> None:
    """I-2: ``send`` must fail closed on a payload over the classic cap.

    BEFORE: ``send`` had no size check (only ``segment_message`` did), so a
    multi-megabyte payload was accepted and would flood the bus with CFs.
    AFTER: the same ``MAX_UDS_PAYLOAD_CLASSIC`` ceiling rejects it up front.
    """
    tx_port = InMemoryTxPort()
    rx_sub = _ScriptedRxSub()
    sender = IsoTpSender(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8, is_fd=False)

    oversized = b"\x36" * (MAX_UDS_PAYLOAD_CLASSIC + 1)

    with pytest.raises(IsoTpPayloadTooLargeError):
        await sender.send(oversized)

    # Fail-closed: nothing at all reached the wire.
    assert len(tx_port.sent_frames) == 0


@pytest.mark.asyncio
async def test_t56c_i2_sender_accepts_payload_at_cap() -> None:
    """I-2 boundary: a payload exactly at the cap is still segmentable.

    Only the frame stream is asserted (the unbounded CF loop never runs to
    completion without an FC stream) — the point is that the cap check does
    not reject a legitimate payload.
    """
    tx_port = InMemoryTxPort()
    rx_sub = _ScriptedRxSub()
    sender = IsoTpSender(tx_port=tx_port, rx_sub=rx_sub, tx_id=0x7E0, rx_id=0x7E8, is_fd=False)

    # Feed one FC that commands the whole rest in a single block (bs=0) with
    # STmin=0 so the transfer drains without an unbounded wait.
    rx_sub.push(_fc_frame(st_min=0x00, bs=0))

    import asyncio

    await asyncio.wait_for(sender.send(b"\x36" * MAX_UDS_PAYLOAD_CLASSIC), timeout=20.0)

    assert len(tx_port.sent_frames) >= 2  # FF + CFs emitted, no size rejection

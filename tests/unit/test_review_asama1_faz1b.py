"""Regression tests for REVIEW Aşama 1 — Faz 1B (J1939 PGN/SID fail-closed hardening).

Faz 1A found two invariant violations beyond the audited S1-P1-1/S1-P1-2/DB:

  * A1-F1 — the live-TX gateway treated the full J1939 ``0x18DBxxxx`` family
    (global destination ``0xFF``) as plain J1939 traffic.  DM11/DM3 clears and
    writable-Requests addressed there fell into the "J1939 doprav, not UDS"
    fallthrough and skipped the speed interlock + dual confirmation.
  * A1-F2 — the same ``0xDB`` family, and truncated ISO-TP PCI frames, were
    classified non-critical on the 29-bit gateway path while the replay
    filter contains explicit ``0xDA/0xDB`` handling and UNKNOWN-SID blocking
    for the same inputs — i.e. live TX was again less guarded than replay
    (the exact S1-P1-3 inversion, in a new place).

Failing-before, passing-after: these tests exercise the unpatched code
first (the A1-F2 cases document the exact ``False``/``True`` divergence).
"""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.hal.replay.safety_filter import ReplaySafetyFilter
from src.hal.virtual import VirtualBus
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor


def _armed_gateway() -> TxSafetyGateway:
    sup = SafetySupervisor()
    for state in (SafetyState.SAFE, SafetyState.PASSIVE, SafetyState.ARMED_TX):
        sup.transition_to(state, "test")
    return TxSafetyGateway(
        bus=VirtualBus(),
        supervisor=sup,
        whitelist_ids={0x18DAF100, 0x18DBFFF9},
        confirmation_secret=b"\x01" * 32,
    )


def _ext(arb_id: int, payload: bytes, *, fd: bool = False) -> CanFrame:
    return CanFrame(
        channel_id="can0",
        arbitration_id=arb_id,
        dlc=len(payload),
        data=payload,
        is_extended=True,
        is_fd=fd,
    )


# ---------------------------------------------------------------------------
# A1-F1 — 0xDB global-destination family must share the 0xDA policy
# ---------------------------------------------------------------------------


def test_db_dm11_clear_frame_is_critical() -> None:
    """A prohibited UDS SID on 0x18DBFFF9 is critical (global functional)."""
    gw = _armed_gateway()
    # ClearDiagnosticInformation (0x14, FF-framed) on the ISO-TP global
    # functional address — the 0xDB-family analogue of the 0xDA ECUReset
    # case. (Raw J1939 DM11 clears travel on their own diagnostic PGNs,
    # e.g. 0x18FE + PGN 65235, covered by the J1939 branch + its tests.)
    frame = _ext(0x18DBFFF9, bytes([0x10, 0x08, 0x14, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
    assert gw._frame_is_critical(frame) is True


def test_db_request_shaped_bytes_are_not_a_bypass() -> None:
    """0xDB Request-shaped bytes: gateway non-critical, filter passes too.

    0x18DBFFF9 is claimed by the ISO-TP 0xDA/0xDB gate, so [0xFF, ...] is
    judged as ISO-TP SingleFrame PCI 0xF (SF_DL nibble 0xF, data[1] != 0x00
    so NOT the escape form) — a well-formed 15-byte-claim SF whose SID byte
    0xD3 is not a critical UDS SID, hence non-critical. This is CORRECT:
    S1-P1-2 Request-for-writable-PGN semantics belong to genuine J1939
    PGN-59904 traffic, and 0xDB is not a J1939 PGN carrier. NO bypass — a
    REAL erase primitive on 0xDB (0x14 FF above, 0x11 SF below) IS critical.
    """
    payload = bytes([0xFF, 0xD3, 0xFE, 0x00, 0x00, 0x00, 0x00, 0x00])
    assert _gateway_critical(0x18DBFFF9, payload) is False
    assert _filter_blocks(0x18DBFFF9, payload) is False


def test_live_cf_stays_non_critical_but_replay_blocks_it() -> None:
    """CF split-brain, documented: live non-critical, replay blocked.

    A ConsecutiveFrame carries no SID by design, so the live gateway must
    NOT self-escalate it (pre-A1 contract — otherwise every benign
    multi-frame 0x22 read would pin behind the interlock). The replay
    filter blocks the same CF via BLOCKED_TP_TUNNEL because a replayed CF
    can only mean herding a live ECU through somebody else's session.
    Both verdicts are safety-correct IN THEIR OWN CONTEXT — the S1-P1-3
    "live must never be less guarded" invariant applies to ATTRIBUTABLE
    frames (SF/FF with a decodable SID), not to session fragments.
    """
    payload = bytes([0x21, 0x11, 0x01, 0x02])
    assert _gateway_critical(0x18DAF100, payload) is False
    assert _filter_blocks(0x18DAF100, payload) is True


def test_db_uds_ecu_reset_is_critical() -> None:
    """A 0xDB frame carrying ISO-TP SID 0x11 is UDS-critical."""
    gw = _armed_gateway()
    frame = _ext(0x18DB33F1, bytes([0x02, 0x11, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00]))
    assert gw._frame_is_critical(frame) is True


# ---------------------------------------------------------------------------
# A1-F2 — gateway must agree with the replay filter on truncated PCI
# ---------------------------------------------------------------------------


def _gateway_critical(arb_id: int, payload: bytes) -> bool:
    return _armed_gateway()._frame_is_critical(_ext(arb_id, payload))


def _filter_blocks(arb_id: int, payload: bytes) -> bool:
    filt = ReplaySafetyFilter()
    frame = _ext(arb_id, payload)
    safe, _ = filt.is_frame_safe(frame)
    return not safe


def test_truncated_single_frame_agrees_with_filter() -> None:
    """[0x02] on 0xDA: filter blocks UNKNOWN; gateway must be critical."""
    arb_id = 0x18DAF100
    assert _filter_blocks(arb_id, bytes([0x02])) is True
    assert _gateway_critical(arb_id, bytes([0x02])) is True


def test_truncated_first_frame_agrees_with_filter() -> None:
    """[0x10] lone FF byte: filter blocks UNKNOWN; gateway must be critical."""
    arb_id = 0x18DAF100
    assert _filter_blocks(arb_id, bytes([0x10])) is True
    assert _gateway_critical(arb_id, bytes([0x10])) is True


def test_db_clear_agrees_with_filter() -> None:
    """0xDB DM11-SID frame: filter blocks it AND gateway is critical.

    0x18DBFFF9 is an ISO-TP global-functional address, not a raw-J1939 PGN
    carrier — so the SAFETY-CORRECT test vector is a prohibited UDS SID on
    that address (0x14 ClearDiagnosticInformation, FF-framed), which both
    the replay filter (PROHIBITED_29BIT_UDS_SID) and the gateway (critical)
    must refuse. The old vector carried a raw DM11 byte pattern (FF nibble
    = ConsecutiveFrame, no SID) that neither side could attribute — the
    failure that made this test red was the TEST's category error, not a
    product bypass.
    """
    payload = bytes([0x10, 0x08, 0x14, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    assert _filter_blocks(0x18DBFFF9, payload) is True
    assert _gateway_critical(0x18DBFFF9, payload) is True

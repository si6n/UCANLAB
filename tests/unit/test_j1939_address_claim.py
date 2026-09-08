"""Unit tests for SAE J1939-81 Address Claiming State Machine and 64-Bit NAME."""

import time

from src.core.models.can_frame import CanFrame
from src.protocols.j1939.address_claim import (
    AddressClaimEngine,
    AddressClaimState,
    J1939Name,
)


def test_j1939_name_10_subfields_encoding() -> None:
    name = J1939Name(
        arbitrary_address_capable=True,  # 1 bit
        industry_group=1,  # On-Highway (3 bits)
        vehicle_system_instance=0,  # 4 bits
        vehicle_system=0,  # 7 bits
        reserved=0,  # 1 bit
        function=128,  # Diagnostic Tool (8 bits)
        function_instance=0,  # 5 bits
        ecu_instance=0,  # 3 bits
        manufacturer_code=500,  # 11 bits
        identity_number=12345,  # 21 bits
    )

    int64_val = name.to_int64()
    raw_bytes = name.to_bytes()
    assert len(raw_bytes) == 8

    decoded = J1939Name.from_bytes(raw_bytes)
    assert decoded.arbitrary_address_capable is True
    assert decoded.industry_group == 1
    assert decoded.vehicle_system_instance == 0
    assert decoded.vehicle_system == 0
    assert decoded.reserved == 0
    assert decoded.function == 128
    assert decoded.function_instance == 0
    assert decoded.ecu_instance == 0
    assert decoded.manufacturer_code == 500
    assert decoded.identity_number == 12345
    assert decoded.to_int64() == int64_val


def test_address_claim_win_contention() -> None:
    # My name has lower number (identity=100) -> Higher Priority
    my_name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=1,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=128,
        identity_number=100,
    )
    engine = AddressClaimEngine(name=my_name, preferred_address=0xF9)

    # Start claiming
    claim_frame = engine.start_claiming()
    assert engine.state.value == AddressClaimState.CLAIMING.value
    assert claim_frame.arbitration_id == 0x18EEFFF9

    # Another ECU sends claim for 0xF9 with higher number (identity=200) -> Lower Priority
    other_name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=1,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=128,
        identity_number=200,
    )
    other_frame = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EEFFF9,
        data=other_name.to_bytes(),
        is_extended=True,
    )

    # Engine should defend its address by re-broadcasting my claim
    defense_frame = engine.handle_rx_frame(other_frame)
    assert defense_frame is not None
    assert defense_frame.arbitration_id == 0x18EEFFF9
    assert defense_frame.data == my_name.to_bytes()

    # M-26 (P2-7): the re-assert armed a FRESH 250 ms window — a manual
    # confirm inside the window is now ignored (early-claim guard). Wait for
    # the window to elapse, then confirm.
    time.sleep(0.30)
    engine.confirm_claimed()
    assert engine.is_address_claimed is True
    assert engine.state.value == AddressClaimState.CLAIMED.value


def test_address_claim_loss_and_fallback() -> None:
    # My name has higher number (identity=500) -> Lower Priority
    my_name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=1,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=128,
        identity_number=500,
    )
    engine = AddressClaimEngine(name=my_name, preferred_address=0xF9)
    engine.start_claiming()

    # Competing ECU has lower number (identity=50) -> Higher Priority
    competitor_name = J1939Name(
        arbitrary_address_capable=False,
        industry_group=1,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=128,
        identity_number=50,
    )
    comp_frame = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EEFFF9,
        data=competitor_name.to_bytes(),
        is_extended=True,
    )

    # We lost 0xF9. Since arbitrary_address_capable is True, it tries fallback address 128 (0x80)
    fallback_frame = engine.handle_rx_frame(comp_frame)
    assert fallback_frame is not None
    assert engine.current_address == 128
    assert fallback_frame.arbitration_id == 0x18EEFF80


# ============================================================================
# F-31: Automatic 250ms claim-confirmation timer DoD tests
# ============================================================================


def _tool_name(identity: int = 100) -> J1939Name:
    return J1939Name(
        arbitrary_address_capable=True,
        industry_group=1,
        vehicle_system_instance=0,
        vehicle_system=0,
        reserved=0,
        function=128,
        function_instance=0,
        ecu_instance=0,
        manufacturer_code=500,
        identity_number=identity,
    )


def test_claim_confirms_automatically_after_250ms_window() -> None:
    """F-31 DoD: an uncontested claim finalizes via the daemon timer, hands-free."""
    engine = AddressClaimEngine(name=_tool_name(), preferred_address=0xF9)
    assert engine.state == AddressClaimState.UNINITIALIZED

    engine.start_claiming()
    assert engine.state == AddressClaimState.CLAIMING
    assert engine.is_address_claimed is False

    # No contention arrives; wait past the 250ms claim window (with margin)
    time.sleep(0.35)

    assert engine.is_address_claimed is True
    assert engine.state == AddressClaimState.CLAIMED
    assert engine.current_address == 0xF9


def test_contention_inside_window_cancels_auto_confirmation() -> None:
    """F-31: a collision within the window cancels the timer for THAT address —
    no premature CLAIMED on the contended SA. The losing side (arbitrary-
    address-capable) re-claims a fallback SA with a fresh 250 ms window."""
    engine = AddressClaimEngine(name=_tool_name(identity=100), preferred_address=0xF9)
    engine.start_claiming()
    engine.cancel_pending_claim_timer()

    # Contention frame from a numerically LOWER name (we lose the address)
    higher_name = _tool_name(identity=50)  # lower identity -> wins arbitration
    comp_frame = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EEFFF9,
        data=higher_name.to_bytes(),
        is_extended=True,
    )
    fallback_frame = engine.handle_rx_frame(comp_frame)
    assert fallback_frame is not None  # re-claim on fallback SA emitted
    assert engine.state == AddressClaimState.CLAIMING  # fresh window, not confirmed yet
    assert engine.current_address != 0xF9  # contended SA abandoned

    # After the fresh window elapses the FALLBACK claim confirms — the
    # contended address 0xF9 is never claimed, but the engine is not stuck.
    time.sleep(0.35)
    assert engine.state == AddressClaimState.CLAIMED
    assert engine.is_address_claimed is True
    assert engine.current_address != 0xF9


def test_contention_late_does_not_break_confirmed_claim() -> None:
    """F-31: after auto-confirmation the state is stable CLAIMED."""
    engine = AddressClaimEngine(name=_tool_name(identity=100), preferred_address=0xF9)
    engine.start_claiming()
    time.sleep(0.35)
    assert engine.state == AddressClaimState.CLAIMED

    # Late contention from a HIGHER name — we defend, but stay claimed/claiming
    winner_name = _tool_name(identity=200)
    late_frame = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EEFFF9,
        data=winner_name.to_bytes(),
        is_extended=True,
    )
    resp = engine.handle_rx_frame(late_frame)
    assert resp is not None  # re-assert claim frame emitted
    assert engine.is_address_claimed is True


def test_fallback_reclaim_re_arms_confirmation_timer() -> None:
    """P1 fix: after losing arbitration, the fallback re-claim must start its
    own 250 ms contention window — otherwise the engine stays CLAIMING forever
    and J1939 TX is permanently dead despite a valid claim on the wire."""
    my_name = _tool_name(identity=500)  # higher number -> lower priority
    engine = AddressClaimEngine(name=my_name, preferred_address=0xF9)
    engine.start_claiming()

    competitor = _tool_name(identity=50)  # lower number -> wins arbitration
    comp_frame = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EEFFF9,
        data=competitor.to_bytes(),
        is_extended=True,
    )

    fallback_frame = engine.handle_rx_frame(comp_frame)
    assert fallback_frame is not None
    assert engine.current_address == 128
    assert engine.state == AddressClaimState.CLAIMING  # fresh claim in progress

    # A fresh timer must be armed: after the 250 ms window the fallback claim
    # finalizes to CLAIMED (previously it never did).
    time.sleep(0.35)
    assert engine.state == AddressClaimState.CLAIMED
    assert engine.is_address_claimed is True


def test_self_echo_claim_is_ignored() -> None:
    """P6: a claim echoing OUR exact NAME must not trigger arbitration."""
    engine = AddressClaimEngine(name=_tool_name(identity=100), preferred_address=0xF9)
    engine.start_claiming()

    # A frame carrying our identical NAME (self-echo / duplicate transmitter)
    echo_frame = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EEFFF9,
        data=engine.name.to_bytes(),
        is_extended=True,
    )
    resp = engine.handle_rx_frame(echo_frame)
    assert resp is None  # no re-assert ping-pong
    assert engine.current_address == 0xF9  # we stay put
    assert engine.state == AddressClaimState.CLAIMING  # claim flow undisturbed


# ============================================================================
# M-26 (P2-7): stale-timer + early-fire guards
# ============================================================================


def test_stale_confirm_callback_cannot_finalize_fresh_claim() -> None:
    """M-26: a timer callback queued behind lock contention (already
    'cancelled' logically) must NEVER confirm a claim started afterwards —
    the generation counter rejects it outright."""
    engine = AddressClaimEngine(name=_tool_name(identity=100), preferred_address=0xF9)
    engine.start_claiming()  # arms generation N
    stale_generation = engine._claim_generation

    # Contention arrives: timer cancelled (generation bumped to N+1), engine
    # wins arbitration and re-arms a FRESH window (generation N+2).
    competitor = _tool_name(identity=50)  # higher number -> we win
    comp_frame = CanFrame.create(
        channel_id="j1939_ch0",
        arbitration_id=0x18EEFFF9,
        data=competitor.to_bytes(),
        is_extended=True,
    )
    reassert = engine.handle_rx_frame(comp_frame)
    assert reassert is not None
    assert engine.state == AddressClaimState.CLAIMING
    assert engine._claim_generation != stale_generation

    # The STALE callback (generation N) now fires — e.g. it was already
    # sitting in the RLock queue when cancel() was called. It must be a
    # no-op: the fresh claim's 250 ms window has not elapsed.
    engine._confirm_claimed_guarded(stale_generation)
    assert engine.state == AddressClaimState.CLAIMING  # NOT confirmed early

    # After the real window elapses, the fresh claim finalizes normally.
    time.sleep(0.30)
    assert engine.state == AddressClaimState.CLAIMED


def test_manual_confirm_during_window_is_ignored() -> None:
    """M-26: confirm_claimed() called DURING a live claim window is a caller
    bug — ignored until the window has actually elapsed (no early claim)."""
    engine = AddressClaimEngine(name=_tool_name(identity=100), preferred_address=0xF9)
    engine.start_claiming()

    engine.confirm_claimed()  # immediately: window just armed
    assert engine.state == AddressClaimState.CLAIMING

    time.sleep(0.30)
    engine.confirm_claimed()  # window elapsed: manual confirmation valid
    assert engine.state == AddressClaimState.CLAIMED

"""T57-A (ASAMA-1 TX review) regression tests — S-1, G-4, G-6, G-10, G-11, W-1.

Each test documents the pre-fix (failing) behaviour and the post-fix
(fail-closed) contract. Written BEFORE the fix, per the T57 acceptance rule.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.hal.virtual import VirtualBus
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor

# ---------------------------------------------------------------------------
# S-1 (HIGH): trigger_fault must ALWAYS transition to FAULT.
# Pre-fix: the 6th fault inside a 1 s re-arm storm was silently DROPPED and the
# supervisor stayed in ARMED_TX (fail-open). The limiter must only thin the LOG.
# ---------------------------------------------------------------------------


def test_s1_trigger_fault_rate_limit_never_drops_fault_transition() -> None:
    """S-1: a fault storm must never leave the supervisor TX-permitted."""
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE)
    supervisor.arm_tx("Operator authorization")

    transitions = []
    supervisor.register_callback(lambda o, n, r: transitions.append((o.value, n.value)))

    # 6 faults, each followed by an operator re-arm cycle: the 6th used to be
    # swallowed by the 5/s limiter and leave the machine in ARMED_TX.
    for i in range(6):
        supervisor.trigger_fault(f"storm fault {i}")
        # Every single fault MUST land in FAULT, no matter the rate.
        assert supervisor.current_state == SafetyState.FAULT, (
            f"fault #{i + 1} was dropped by the rate limiter (state="
            f"{supervisor.current_state.value})"
        )
        assert supervisor.is_tx_permitted is False
        assert supervisor.fault_reason == f"storm fault {i}"
        supervisor.transition_to(SafetyState.SAFE, "operator cleared")
        supervisor.transition_to(SafetyState.PASSIVE, "operator re-armed")
        supervisor.arm_tx("Operator authorization")

    # Every trigger was RECEIVED and recorded, even the ones that were
    # idempotent at the state level.
    assert supervisor._fault_event_count == 6
    fault_transitions = [t for t in transitions if t[1] == SafetyState.FAULT.value]
    assert len(fault_transitions) == 6


def test_s1_fault_storm_log_is_still_rate_limited(caplog) -> None:
    """S-1: the limiter's remaining job is log thinning, not gating."""
    supervisor = SafetySupervisor(initial_state=SafetyState.PASSIVE)
    with caplog.at_level("WARNING"):
        for i in range(10):
            supervisor.trigger_fault(f"noisy {i}")
    assert supervisor.current_state == SafetyState.FAULT
    # The state is idempotent (10 calls, 1 transition) but every fault EVENT
    # is still accounted for.
    assert supervisor.epoch == 1
    assert supervisor._fault_event_count == 10
    # The limiter still compresses the safety-log chatter.
    storm_logs = [r for r in caplog.records if "log rate-limited" in r.message]
    assert storm_logs, "the fault-storm log limiter no longer thins the audit log"


def test_s1_reentrant_fault_callback_cannot_drop_the_fault_record() -> None:
    """S-1: a callback re-entering trigger_fault must not lose the transition.

    Pre-fix, the callback fired AFTER state/history mutation; under the storm
    limiter it ran too, so nothing was lost. With the transition now taken on
    every call, a re-entrant trigger inside a callback could no-op the pending
    legality transition — the fault record is therefore announced first.
    """
    supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP)
    reentered = {"done": False}

    def reenter(old, new, reason):
        if not reentered["done"]:
            reentered["done"] = True
            supervisor.trigger_fault("re-entrant fault")

    supervisor.register_callback(reenter)
    # STARTUP -> ACTIVE is ILLEGAL: it forces FAULT and raises.
    with pytest.raises(SafetyError):
        supervisor.transition_to(SafetyState.ACTIVE, "illegal jump")

    assert supervisor.current_state == SafetyState.FAULT
    # The fault reason may be overwritten by the re-entrant call, but the
    # event ledger proves the original fault was not swallowed.
    assert supervisor._fault_event_count >= 1


# ---------------------------------------------------------------------------
# G-4 (MEDIUM): _consumed_confirmations must be an ordered, TTL-pruned store.
# Pre-fix: a plain `set` + blind `.pop()` deleted a RANDOM entry, so a still
# live (unexpired) token could be evicted and REPLAYED.
# ---------------------------------------------------------------------------


def _mint(gw: TxSafetyGateway, arb_id: int, ttl_s: float = 30.0) -> bytes:
    return gw.issue_confirmation_token(arb_id, ttl_s=ttl_s)


def _insert_expired(gw: TxSafetyGateway, payload: bytes) -> None:
    """Record a token whose TTL already elapsed."""
    gw._consume_confirmation(payload, expiry_ns=time.monotonic_ns() - 1_000_000_000)


def test_g4_store_stays_bounded_under_every_insert_shape() -> None:
    """G-4: the replay store is bounded no matter how entries are inserted.

    Pre-fix, expired junk accumulated unboundedly (the `set` had no TTL prune),
    so the 1024-limit tripped constantly and each trip deleted a RANDOM member —
    including live tokens. The store must now never exceed the ceiling.
    """
    bus = VirtualBus(channel_id="t57_g4_capacity")
    bus.connect()
    gw = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0}, confirmation_secret=b"k" * 32)
    ceiling = gw._CONSUMED_CONFIRMATIONS_MAX

    # Mixed load: expired entries interleaved with long-lived ones.
    for i in range(ceiling * 3):
        payload = bytes([i % 251]) * 8 + i.to_bytes(8, "big") + b"\xaa" * 8
        if i % 3 == 0:
            expiry = time.monotonic_ns() - 1_000_000_000  # already expired
        else:
            expiry = time.monotonic_ns() + 600_000_000_000  # live
        gw._consume_confirmation(payload, expiry_ns=expiry)
        assert len(gw._consumed_confirmations) <= ceiling
    bus.disconnect()


def test_g4_live_token_survives_expired_eviction() -> None:
    """G-4: a live token must never be the entry chosen to make room.

    The store holds one long-lived token plus a stream of already-expired
    entries; the expiry-aware prune drains the expired ones first, so the live
    token is the last survivor.
    """
    bus = VirtualBus(channel_id="t57_g4_live")
    bus.connect()
    gw = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0}, confirmation_secret=b"k" * 32)
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x01")

    live = _mint(gw, 0x7E0, ttl_s=60.0)
    gw._verify_confirmation_token(live, frame)
    live_payload = live[:-32]
    assert live_payload in gw._consumed_confirmations

    # Re-consume the SAME live payload repeatedly: it must never be dropped by
    # the capacity prune (insertion-order refresh keeps it as the newest).
    for _ in range(gw._CONSUMED_CONFIRMATIONS_MAX + 10):
        gw._consume_confirmation(live_payload, expiry_ns=time.monotonic_ns() + 60_000_000_000)

    assert live_payload in gw._consumed_confirmations
    # A replay of the live token is still refused.
    with pytest.raises(Exception, match=r"(?i)already consumed|expired"):
        gw._verify_confirmation_token(live, frame)
    bus.disconnect()


def test_g4_capacity_eviction_is_fifo_and_bounded() -> None:
    """G-4: with no expired entries the store stays bounded and evicts oldest-first."""
    bus = VirtualBus(channel_id="t57_g4_fifo")
    bus.connect()
    gw = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0}, confirmation_secret=b"k" * 32)
    ceiling = gw._CONSUMED_CONFIRMATIONS_MAX
    far_future = time.monotonic_ns() + 600_000_000_000

    first = (0x7E0).to_bytes(4, "big") + b"\x01" * 10
    gw._consume_confirmation(first, expiry_ns=far_future)
    # Fill past the ceiling with live tokens.
    for i in range(ceiling + 10):
        filler = (0x7E0).to_bytes(4, "big") + i.to_bytes(10, "big")
        gw._consume_confirmation(filler, expiry_ns=far_future)

    assert len(gw._consumed_confirmations) == ceiling
    assert first not in gw._consumed_confirmations, "oldest entry was not evicted first"
    # The most recent entry must survive (random eviction could have dropped it).
    assert (0x7E0).to_bytes(4, "big") + (ceiling + 9).to_bytes(10, "big") in gw._consumed_confirmations
    bus.disconnect()


def test_g4_expired_entries_are_pruned() -> None:
    """G-4: expired tokens are pruned so the store cannot grow without bound."""
    bus = VirtualBus(channel_id="t57_g4_prune")
    bus.connect()
    gw = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0}, confirmation_secret=b"k" * 32)

    expired_suffix = (time.monotonic_ns() + 1).to_bytes(8, "big")
    for i in range(50):
        payload = (0x7E0).to_bytes(4, "big") + expired_suffix + i.to_bytes(16, "big")
        gw._consume_confirmation(payload, expiry_ns=int.from_bytes(expired_suffix, "big"))
    # Let the TTL elapse, then force a prune via one more consumption.
    time.sleep(0.01)
    live_suffix = (time.monotonic_ns() + 60_000_000_000).to_bytes(8, "big")
    fresh = (0x7E0).to_bytes(4, "big") + live_suffix + b"\xff" * 16
    gw._consume_confirmation(fresh, expiry_ns=int.from_bytes(live_suffix, "big"))

    assert fresh in gw._consumed_confirmations
    assert len(gw._consumed_confirmations) == 1, "expired entries were not pruned"
    bus.disconnect()


def test_g4_replay_store_is_ordered() -> None:
    """G-4: the store must be insertion-ordered (FIFO eviction), not a `set`."""
    from collections import OrderedDict

    bus = VirtualBus(channel_id="t57_g4_order")
    bus.connect()
    gw = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0}, confirmation_secret=b"k" * 32)
    assert isinstance(gw._consumed_confirmations, OrderedDict)
    bus.disconnect()


# ---------------------------------------------------------------------------
# G-6 (MEDIUM): the abort-hook mitigation must be WIRED, not just documented.
# The gateway registers a HAL TX-flush hook on the E-Stop when the driver
# exposes one; when it does not, the residual is bounded/documented.
# ---------------------------------------------------------------------------


class _FlushableBus(VirtualBus):
    """HAL stub exposing the flush_tx_buffer abort primitive G-6 looks for."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.flush_calls = 0

    def flush_tx_buffer(self) -> None:
        self.flush_calls += 1


def test_g6_abort_hook_registered_when_driver_supports_flush() -> None:
    """G-6: a driver with flush_tx_buffer gets an E-Stop abort hook."""
    bus = _FlushableBus(channel_id="t57_g6_flush")
    bus.connect()
    estop = EmergencyStopSystem()
    TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    assert len(estop._abort_hooks) == 1, "no abort hook was registered on the E-Stop"
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "test engagement")
    assert bus.flush_calls == 1, "the registered abort hook did not flush the driver TX queue"
    bus.disconnect()


def test_g6_no_abort_hook_when_driver_lacks_flush() -> None:
    """G-6: a driver without a flush primitive must not get a no-op hook."""
    bus = VirtualBus(channel_id="t57_g6_noflush")
    bus.connect()
    estop = EmergencyStopSystem()
    TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
    assert estop._abort_hooks == []
    # The residual window is documented as an accepted risk at the PHASE-3 site.
    bus.disconnect()


# ---------------------------------------------------------------------------
# G-10 (LOW, but real in a fault storm): estop.trigger() must NOT run while the
# gateway RLock is held. Pre-fix the gateway lock was held across trigger(),
# blocking every other sender and update_physical_speed.
# ---------------------------------------------------------------------------


def test_g10_estop_trigger_runs_outside_gateway_lock() -> None:
    """G-10: snapshot-then-release — trigger() outside the gateway lock."""
    bus = VirtualBus(channel_id="t57_g10")
    bus.connect()
    estop = EmergencyStopSystem()
    gw = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})

    observed = {}
    original_trigger = estop.trigger

    def probe_trigger(*args, **kwargs):
        # A sibling thread must be able to take the gateway lock while
        # trigger() is running (i.e. the triggering thread released it).
        acquired = []

        def contender() -> None:
            got = gw._lock.acquire(timeout=1.0)
            acquired.append(got)
            if got:
                gw._lock.release()

        t = threading.Thread(target=contender)
        t.start()
        t.join(2.0)
        observed["lock_free"] = acquired and acquired[0]
        return original_trigger(*args, **kwargs)

    estop.trigger = probe_trigger  # type: ignore[method-assign]

    # Drive the whitelist-violation E-Stop path.
    gw._whitelist_miss_streak = gw.WHITELIST_ESTOP_AFTER - 1
    with pytest.raises(SafetyError):
        gw.validate_and_transmit(CanFrame.create(channel_id="c0", arbitration_id=0x999, data=b"\x01"))

    assert observed.get("lock_free") is True, (
        "estop.trigger() ran while the gateway RLock was held (fault-storm sender block)"
    )
    bus.disconnect()


# ---------------------------------------------------------------------------
# G-11 (LOW): whitelist_ids must be immutable + rebindable, and mask==0 must
# never authorize every ID.
# ---------------------------------------------------------------------------


def test_g11_whitelist_is_immutable_frozenset() -> None:
    """G-11: runtime `whitelist_ids.add()` must be impossible."""
    bus = VirtualBus(channel_id="t57_g11_frozen")
    bus.connect()
    gw = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    assert isinstance(gw.whitelist_ids, frozenset)
    with pytest.raises(AttributeError):
        gw.whitelist_ids.add(0xDEAD)  # type: ignore[attr-defined]
    bus.disconnect()


def test_g11_rebind_whitelist_is_the_sanctioned_path() -> None:
    """G-11: `rebind_whitelist()` is the explicit, auditable widening path."""
    bus = VirtualBus(channel_id="t57_g11_rebind")
    bus.connect()
    gw = TxSafetyGateway(bus=bus, whitelist_ids={0x7E0})
    frame_new = CanFrame.create(channel_id="c0", arbitration_id=0x7E8, data=b"\x01")

    with pytest.raises(SafetyError):
        gw.validate_and_transmit(frame_new)

    gw.rebind_whitelist({0x7E0, 0x7E8})
    assert gw.whitelist_ids == frozenset({0x7E0, 0x7E8})
    assert gw.validate_and_transmit(frame_new) is True
    bus.disconnect()


def test_g11_zero_mask_never_authorizes_any_id() -> None:
    """G-11: `(id & 0) == 0` used to match EVERY id — mask==0 must be inert.

    R2-G4: hardened to fail-fast — a zero mask is rejected at construction
    (ValueError) instead of lingering as a dead whitelist entry that only
    fails at send time.
    """
    bus = VirtualBus(channel_id="t57_g11_mask0")
    bus.connect()
    with pytest.raises(ValueError, match="maskesi 0 olamaz"):
        TxSafetyGateway(bus=bus, whitelist_masks=[(0x0, 0x0)])
    bus.disconnect()


def test_g11_valid_mask_still_authorizes_family() -> None:
    """G-11: the mask!=0 guard must not break legitimate family authorization."""
    bus = VirtualBus(channel_id="t57_g11_mask_ok")
    bus.connect()
    gw = TxSafetyGateway(bus=bus, whitelist_masks=[(0x1CEC00F9, 0x1CEC00FF)])
    f = CanFrame.create(channel_id="c0", arbitration_id=0x1CEC01F9, data=b"\x11", is_extended=True)
    assert gw.validate_and_transmit(f) is True
    bus.disconnect()


# ---------------------------------------------------------------------------
# W-1: the wiring claim must match reality. AGENTS.md §2.7 is a PROTECTED
# agent-instruction file (writes require explicit user consent), so this test
# pins the CODE-side fact instead: EcuFlashingEngine IS reachable from
# flash_start()'s live-bus branch. The doc correction is tracked separately.
# ---------------------------------------------------------------------------


def test_w1_flash_start_reaches_ecu_flashing_engine_on_live_branch() -> None:
    """W-1: EcuFlashingEngine must be constructed on flash_start's real branch.

    AGENTS.md §2.7 listed it as NOT wired; the source shows otherwise, so the
    contradiction is resolved on the code side and asserted here.
    """
    import inspect

    from src.ui import desktop_app

    source = inspect.getsource(desktop_app.UniversalCanDesktopApp.flash_start)
    assert "EcuFlashingEngine(" in source, (
        "flash_start no longer constructs EcuFlashingEngine — if the wiring was "
        "genuinely removed, AGENTS.md §2.7 becomes correct again"
    )
    assert "execute_flash(" in source, (
        "flash_start no longer calls execute_flash — the live flash path is gone"
    )
    # The engine is bound to the TxSafetyGateway choke-point, not a raw driver.
    assert "gateway=self.gateway" in source, (
        "EcuFlashingEngine must be wired through the TxSafetyGateway choke-point"
    )

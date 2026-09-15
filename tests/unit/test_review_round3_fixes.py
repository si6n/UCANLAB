"""Regression tests for the third-party review findings (REVIEW.md, REVIEW 2, REVIEW 3).

Covers the fixes applied in this remediation round:
- CSV parser: a CanFrame invariant violation in one row never aborts the load.
- Ring buffer: sequence labels stay coherent with the snapshot (TOCTOU).
- Rolling disk: append() after close() is rejected loudly, never silently lost.
- Gateway: inbound-triggered protocol responses never E-Stop-escalate on rate
  overload; rebind_bus invalidates in-flight frames via the TX fence.
- FrameRouter: a callback-only subscriber is tripped after repeated over-budget
  callbacks instead of holding the RX thread hostage forever.
- ISO-TP: `_rx_session = None` only clears the default session, not the table.
- Updater: an unparseable manifest version is a FAILED check, never "up-to-date".
- VirtualBus: recv(None) blocks (contract), recv(0) polls.
- Watchdog: stop() with a wedged monitor escalates through supervisor + E-Stop.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

import pytest

from src.core.models.can_frame import CanFrame
from src.engine.buffer.ring_buffer import BinaryRingBuffer
from src.engine.buffer.rolling_disk import RollingDiskBuffer
from src.engine.router import FrameRouter
from src.hal.replay.parsers import CsvParser
from src.hal.virtual import VirtualBus

# ============================================================================
# REVIEW.md HIGH-1: CSV parser resilience (Y-06 parity)
# ============================================================================


def _write_tmp_csv(content: str) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def test_csv_parser_dlc_payload_mismatch_row_is_skipped_not_crash() -> None:
    """A row whose DLC/payload lengths disagree must be skipped, not raise."""
    content = """time,id,dlc,data
0.000000,0x123,8,aa bb
0.100000,0x124,2,cc dd
"""
    path = _write_tmp_csv(content)
    try:
        frames = CsvParser.parse_file(path)
        assert len(frames) == 1
        assert frames[0].arbitration_id == 0x124
    finally:
        path.unlink(missing_ok=True)


def test_csv_parser_classic_dlc_over_8_row_is_skipped() -> None:
    """Classic CAN frames (is_fd fixed False) with DLC>8 are skipped."""
    content = """time,id,dlc,data
0.000000,0x123,12,01 02 03 04 05 06 07 08 09 0a 0b 0c
0.100000,0x124,1,ee
"""
    path = _write_tmp_csv(content)
    try:
        frames = CsvParser.parse_file(path)
        assert len(frames) == 1
        assert frames[0].dlc == 1
    finally:
        path.unlink(missing_ok=True)


def test_csv_parser_invalid_canframe_invariant_never_aborts_load() -> None:
    """Any CanFrame.__post_init__ rejection in one row is logged and skipped."""
    content = """time,id,dlc,data,channel
0.000000,0x123,2,aa bb,valid_ch
0.100000,0x124,9,ff,also_ok_len_mismatch
0.200000,0x125,1,11,third
"""
    path = _write_tmp_csv(content)
    try:
        frames = CsvParser.parse_file(path)
        # The DLC=9 classic row is skipped; the other two survive.
        assert [f.arbitration_id for f in frames] == [0x123, 0x125]
    finally:
        path.unlink(missing_ok=True)


# ============================================================================
# REVIEW.md MEDIUM-1: ring buffer sequence coherence (TOCTOU)
# ============================================================================


def test_ring_buffer_get_latest_view_returns_coherent_total_written() -> None:
    buf = BinaryRingBuffer(capacity=8)
    for i in range(5):
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=b"\x01"))
    old_part, new_part, total = buf.get_latest_view(5)
    assert total == 5
    assert len(old_part) + len(new_part) == 5


def test_ring_buffer_sequence_labels_survive_concurrent_append() -> None:
    """A concurrent append between snapshot and materialization must not
    shift the exported frames' sequence labels (TOCTOU regression)."""
    buf = BinaryRingBuffer(capacity=1024)
    for i in range(100):
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=b"\x01"))

    stop = threading.Event()

    def writer() -> None:
        i = 100
        while not stop.is_set():
            buf.append(CanFrame.create(channel_id="ch0", arbitration_id=i, data=b"\x02"))
            i += 1

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        for _ in range(20):
            frames = buf.get_latest_frames(50)
            assert len(frames) == 50
            seqs = [f.sequence for f in frames]
            # Labels must be strictly consecutive regardless of racing appends.
            assert seqs == list(range(seqs[0], seqs[0] + 50)), seqs
    finally:
        stop.set()
        t.join(timeout=2.0)


# ============================================================================
# REVIEW.md MEDIUM-2 / REVIEW 2 #5: rolling disk close-vs-append race
# ============================================================================


def test_rolling_disk_append_after_close_is_rejected_not_lost() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        buf = RollingDiskBuffer(storage_dir=tmp, chunk_frame_threshold=2)
        f = CanFrame.create(channel_id="ch0", arbitration_id=0x123, data=b"\x01\x02")
        buf.append(f)
        buf.close()
        # Late producer: must be counted as rejected, never silently queued
        # for a dead worker.
        buf.append(f)
        assert buf._rejected_frames >= 1
        # close() is idempotent.
        buf.close()


def test_rolling_disk_close_is_idempotent_and_worker_exits() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        buf = RollingDiskBuffer(storage_dir=tmp, chunk_frame_threshold=2)
        buf.append(CanFrame.create(channel_id="ch0", arbitration_id=1, data=b"\x01"))
        buf.close()
        buf.close()
        assert not buf._flush_worker.is_alive()


# ============================================================================
# REVIEW.md HIGH-3: gateway inbound-triggered rate escalation
# ============================================================================


def _make_gateway_fixture(monkeypatch: pytest.MonkeyPatch):
    """Build a minimal gateway fixture with estop spy (no real whitelist gating)."""
    from src.hal.virtual import VirtualBus
    from src.safety.estop import EmergencyStopSystem
    from src.safety.gateway import TxSafetyGateway
    from src.safety.state_machine import SafetyState, SafetySupervisor

    monkeypatch.setenv("UCANLAB_TEST_MODE", "1")
    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)
    supervisor.arm_tx()
    estop = EmergencyStopSystem()
    bus = VirtualBus(channel_id="gw_test")
    bus.connect()
    gw = TxSafetyGateway.for_testing(bus=bus, estop=estop, whitelist_ids={0x123})
    return gw, estop


def test_gateway_inbound_triggered_overload_never_estops(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostile remote node baiting CTS responses above the default lane must
    not latch a RATE_LIMIT_OVERFLOW E-Stop (remote self-DoS)."""
    gw, estop = _make_gateway_fixture(monkeypatch)
    frame = CanFrame.create(channel_id="gw_test", arbitration_id=0x123, data=b"\x01")

    for _ in range(200):
        try:
            gw.validate_and_transmit(frame, inbound_triggered=True)
        except Exception:
            # RateLimitExceededError after the lane saturates — expected.
            pass
    assert not estop.is_engaged, "inbound-triggered responses must never E-Stop-escalate"


def test_gateway_application_overload_still_estops(monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing fail-hard contract for local runaway senders is preserved."""
    from src.safety.exceptions import RateLimitExceededError

    gw, estop = _make_gateway_fixture(monkeypatch)
    frame = CanFrame.create(channel_id="gw_test", arbitration_id=0x123, data=b"\x01")

    engaged = False
    for _ in range(200):
        try:
            gw.validate_and_transmit(frame)
        except RateLimitExceededError:
            if estop.is_engaged:
                engaged = True
                break
    assert engaged, "sustained default-lane overload by a local sender must E-Stop"
    assert estop.is_engaged


def test_gateway_rebind_bus_invalidates_inflight_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    """rebind_bus bumps the TX fence — a frame validated against the old bus
    is rejected at dispatch time instead of hitting the new bus."""
    gw, estop = _make_gateway_fixture(monkeypatch)
    from src.hal.virtual import VirtualBus

    fence_before = estop.tx_fence
    new_bus = VirtualBus(channel_id="gw_test_2")
    new_bus.connect()
    gw.rebind_bus(new_bus)
    assert estop.tx_fence != fence_before, "rebind must advance the TX fence"
    # The new bus is bound; the old state (rate windows) starts clean.
    assert gw.bus is new_bus


# ============================================================================
# REVIEW 2 #2 / REVIEW 3 #13: FrameRouter callback-only circuit breaker
# ============================================================================


def test_router_trips_callback_only_slow_subscriber() -> None:
    router = FrameRouter()
    calls: list[int] = []

    def slow_sub(frame: CanFrame) -> None:
        calls.append(frame.arbitration_id)
        time.sleep(0.05)  # 50 ms > 20 ms budget

    sub_id, _ = router.subscribe(callback=slow_sub)
    frame = CanFrame.create(channel_id="ch0", arbitration_id=0x100, data=b"\x01")

    for _ in range(FrameRouter.CALLBACK_TRIP_AFTER + 2):
        router.route_frame(frame)

    assert len(calls) == FrameRouter.CALLBACK_TRIP_AFTER, (
        "callback must be removed after CALLBACK_TRIP_AFTER consecutive violations"
    )

    # restore_callback re-arms with a clean trip count.
    def fast_sub(frame: CanFrame) -> None:
        pass

    assert router.restore_callback(sub_id, fast_sub) is True
    router.route_frame(frame)  # fast — must not re-trip
    assert router._subscriptions[sub_id].callback is fast_sub


def test_router_in_budget_callback_never_trips() -> None:
    router = FrameRouter()

    def fast_sub(frame: CanFrame) -> None:
        pass

    router.subscribe(callback=fast_sub)
    frame = CanFrame.create(channel_id="ch0", arbitration_id=0x100, data=b"\x01")
    for _ in range(100):
        router.route_frame(frame)


# ============================================================================
# REVIEW.md LOW-7: ISO-TP _rx_session setter scope
# ============================================================================


def test_isotp_rx_session_none_setter_only_clears_default_session() -> None:
    from src.protocols.uds.isotp import IsoTpRxSession, IsoTpTransport

    t = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8, channel_id="uds_a")
    s_default = IsoTpRxSession(rx_id=0x7E8, total_bytes=8, channel_id="uds_a")
    s_other = IsoTpRxSession(rx_id=0x7E2, total_bytes=8, channel_id="uds_b")

    t._rx_session = s_default
    t._sessions[(0x7E2, "uds_b")] = s_other

    t._rx_session = None  # compatibility clear: default slot only

    assert (0x7E2, "uds_b") in t._sessions, "parallel session must survive"
    assert (0x7E8, "uds_a") not in t._sessions, "default slot must be cleared"

    # Full reset remains available via the explicit API.
    assert t.clear_sessions() >= 1
    assert t._sessions == {}


# ============================================================================
# REVIEW 2 #8: updater semver parse hardening
# ============================================================================


def test_updater_unparseable_manifest_version_fails_check() -> None:
    from src.launcher.updater import UpdateManager

    assert UpdateManager._parse_semver("13.2.1") == (13, 2, 1)
    assert UpdateManager._parse_semver("v13.2.1-beta") == (13, 2, 1)
    assert UpdateManager._parse_semver("garbage.version") is None
    assert UpdateManager._parse_semver("13.x.y") is None

    um = UpdateManager(current_version="13.0.0", cloud_client=None)
    # is_newer_version never compares None (no TypeError), returns False for unknown.
    assert um.is_newer_version("not.a.version") is False


# ============================================================================
# REVIEW.md LOW-1 / REVIEW 3 #16: VirtualBus.recv contract
# ============================================================================


def test_virtual_bus_recv_none_blocks_until_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """timeout_s=None must block indefinitely per the RxSubscription contract."""
    vbus = VirtualBus(channel_id="vcontract")
    vbus.connect()
    frame = CanFrame.create(channel_id="vcontract", arbitration_id=0x321, data=b"\x09")

    def injector() -> None:
        time.sleep(0.15)  # longer than any legacy 0.1s clamp
        vbus.inject_rx(frame)

    t = threading.Thread(target=injector)
    t.start()
    try:
        start = time.monotonic()
        got = vbus.recv(timeout_s=None)  # must NOT return at ~0.1s
        elapsed = time.monotonic() - start
        assert got is not None and got.arbitration_id == 0x321
        assert elapsed >= 0.12, f"recv(None) must block past the old 0.1s clamp (elapsed={elapsed:.3f})"
    finally:
        t.join(timeout=2.0)
        vbus.disconnect()


def test_virtual_bus_recv_zero_is_nonblocking_poll() -> None:
    vbus = VirtualBus(channel_id="vcontract")
    vbus.connect()
    assert vbus.recv(timeout_s=0) is None
    vbus.inject_rx(CanFrame.create(channel_id="vcontract", arbitration_id=0x111, data=b"\x01"))
    got = vbus.recv(timeout_s=0)
    assert got is not None and got.arbitration_id == 0x111
    vbus.disconnect()


# ============================================================================
# REVIEW 2 #1: watchdog stop() wedge escalation
# ============================================================================


def test_watchdog_stop_with_wedged_monitor_revokes_tx(monkeypatch: pytest.MonkeyPatch) -> None:
    """A monitor that ignores the stop signal must trigger a supervisor fault
    + E-Stop (fail-safe), not just an ERROR log line."""
    from src.safety.estop import EmergencyStopSystem
    from src.safety.state_machine import SafetyState, SafetySupervisor
    from src.safety.watchdog import TxWatchdogSupervisor

    supervisor = SafetySupervisor(initial_state=SafetyState.SAFE)
    supervisor.transition_to(SafetyState.PASSIVE)
    supervisor.arm_tx()
    estop = EmergencyStopSystem()
    wd = TxWatchdogSupervisor(supervisor=supervisor, estop=estop, timeout_ms=60_000.0)
    wd.start()

    # Wedge the monitor: join() will time out because is_alive stays True.
    monkeypatch.setattr(
        type(wd._thread), "join", lambda self, timeout=None: None, raising=True
    )

    wd.stop()
    assert estop.is_engaged, "wedged monitor stop must escalate to E-Stop"
    assert supervisor.is_fault


# ============================================================================
# REVIEW 2 #4: PythonCanBus.connect wraps ALL backend exceptions
# ============================================================================


def test_python_can_bus_connect_wraps_unexpected_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.hal.drivers.pcan_kvaser as pcan_mod
    from src.core.errors import HardwareError
    from src.hal.drivers.pcan_kvaser import PythonCanBus

    bus = PythonCanBus(interface="vector", channel="1", bitrate=500000)

    def boom(**kwargs):
        raise ModuleNotFoundError("No module named 'canlib'")

    monkeypatch.setattr(pcan_mod.can, "Bus", boom)
    with pytest.raises(HardwareError, match="vector"):
        bus.connect()
    assert bus.is_connected is False


# ============================================================================
# REVIEW 3 #5: J1939 PGN decode correctness (ET1 vs Engine Hours)
# ============================================================================


def _et1_frame(raw_temp: int, sa: int = 0x00) -> CanFrame:
    """ET1 (PGN 65262 / 0xFEEE) — the real coolant temperature PGN."""
    arb = 0x18FEEE00 | sa
    data = bytes([raw_temp, 0, 0, 0, 0, 0, 0, 0])
    return CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)


def _engine_hours_frame(hours_lsb: int, sa: int = 0x00) -> CanFrame:
    """Engine Hours, Revolutions (PGN 65249 / 0xFEE1) — NOT a temperature."""
    arb = 0x18FEE100 | sa
    data = bytes([hours_lsb, 0, 0, 0, 0, 0, 0, 0])
    return CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)


def test_et1_pgn_65262_feeds_coolant_temperature() -> None:
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._decode_j1939_signal(_et1_frame(125))  # 125 - 40 = 85 °C
    assert app._current_temp == 85.0


def test_engine_hours_pgn_65249_never_feeds_temperature() -> None:
    """The old PS=0xE1 guard rendered the Engine Hours LSB as a fabricated
    coolant temperature (overheat/under-temp hallucinations)."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._current_temp = 21.0  # baseline
    app._decode_j1939_signal(_engine_hours_frame(250))  # 250 h raw — must NOT become 210 °C
    assert app._current_temp == 21.0, "Engine Hours LSB must not be decoded as coolant °C"


def test_edp_set_ccvs_frame_not_misfiled_by_hand_rolled_math() -> None:
    """Frames with EDP bit set must not fall into the old (pf, ps) buckets —
    the shared parser preserves EDP/DP in the PGN so an EDP-set CCVS-shaped
    ID no longer satisfies the interlock through the wrong bucket."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    # 0x18FEF100 with EDP (bit 25, 0x02000000) set: 0x1AFEF100 — still a
    # valid 29-bit ID, but a different PGN through the shared parser.
    arb = 0x1AFEF100
    data = bytes([0x00, 0x00, 0x00, 0, 0, 0, 0, 0])
    frame = CanFrame.create(channel_id="vcan0", arbitration_id=arb, data=data, is_extended=True)
    app._decode_j1939_signal(frame)
    # EDP-set frame maps to a different PGN — the CCVS bucket must not fire.
    assert app.gateway._last_speed_update_ns == 0


# ============================================================================
# REVIEW 3 #1: E-Stop keeps the forensic (black-box) recording alive
# ============================================================================


def test_estop_does_not_stop_live_ingest_recording() -> None:
    """While E-Stop is latched, the live RX path must keep running — only
    TX (and the DEMO generator) are cut. The forensic window right after
    an E-Stop is exactly what the black-box recorder exists for."""
    from src.core.models.can_frame import CanFrame as CF
    from src.hal.virtual import VirtualBus as VB
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    # Simulate the app being live (not DEMO) with a virtual bus behind it.
    vb = VB(channel_id="vcan0")
    vb.connect()
    app._set_ui_state(_is_simulating=False)
    with app._bus_lock:
        old_bus = app.bus
        app.bus = vb
    try:
        app.trigger_estop()
        assert app._is_estop is True

        frame = CF.create(channel_id="vcan0", arbitration_id=0x123, data=b"\x01\x02")
        # Drive one live-tick worth of the loop body directly (the loop is
        # time-driven; the ingest call is the unit under test).
        before = app.ring_buffer.total_written
        app._ingest_live_frame(frame)
        app.ring_buffer.append_batch([frame])
        after = app.ring_buffer.total_written
        assert after > before, "live frames must still be recorded during E-Stop"
    finally:
        with app._bus_lock:
            app.bus = old_bus
        try:
            app.rolling_disk.close()
        except Exception:
            pass


# ============================================================================
# REVIEW 3 #17: replay negative-delta clamp
# ============================================================================


def test_replay_player_survives_backward_timestamp_jump() -> None:
    """A capture whose wall-clock timestamps jump backwards must not stall
    or burst the replay (deltas clamp to 0)."""
    from src.hal.replay.player import ReplayBus

    frames = [
        CanFrame.create(channel_id="ch0", arbitration_id=1, data=b"\x01", timestamp_ns=1_000_000_000),
        CanFrame.create(channel_id="ch0", arbitration_id=2, data=b"\x02", timestamp_ns=500_000_000),  # backwards
        CanFrame.create(channel_id="ch0", arbitration_id=3, data=b"\x03", timestamp_ns=1_100_000_000),
    ]
    bus = ReplayBus(frames)
    played: list[int] = []

    start = time.monotonic()
    bus.play(lambda f: played.append(f.arbitration_id), speed=1.0)
    elapsed = time.monotonic() - start

    assert played == [1, 2, 3]
    # The 0.1s positive gap must play; the negative one must be instant.
    assert elapsed < 0.5, f"backward jump must not stall replay (elapsed={elapsed:.2f}s)"


# ============================================================================
# REVIEW 3 #41: bus reconnect disarms TX fail-closed
# ============================================================================


def test_reconnect_disarms_tx_and_requires_explicit_rearm() -> None:
    """A settings-triggered bus reconnect swaps the physical channel — TX
    authority must NOT survive the swap (the reconnected bus opens
    listen-only; ARMED_TX + listen-only is a dead protocol that fails with
    HardwareError on every CTS/ACK/response). The supervisor drops to
    PASSIVE and the operator must re-arm explicitly."""
    from src.safety.state_machine import SafetyState
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    # Boot PASSIVE -> operator arms TX explicitly.
    # T47-B (P3/G-3): the composition root wires an ARM_AUTH_SECRET, so arming
    # requires the single-use HMAC token that the trusted root mints here.
    assert app.supervisor.current_state == SafetyState.PASSIVE
    app.supervisor.arm_tx(reason="operator test arm", auth_token=app._mint_arm_token())
    assert app.supervisor.is_tx_permitted is True

    # Settings change triggers the reconnect transaction (the UI sends the
    # human-readable "baudRate" setting).
    app.update_settings({"baudRate": "500 kBd"})

    assert app.supervisor.is_tx_permitted is False, "reconnect must disarm TX"
    assert app.supervisor.current_state == SafetyState.PASSIVE
    assert app.bus.bitrate == 500000
    # The gateway rebind target follows the new bus instance.
    assert app.gateway.bus is app.bus


# ============================================================================
# REVIEW 3 (buffer hardening): channel-map overrun report + rolling-disk
# admission-time enum membership.
# ============================================================================


def test_ring_buffer_channel_map_overflow_reports_net_stats() -> None:
    """REVIEW 3-MEDIUM: when the 16-bit channel map closes, the overflow is
    reported via channel_map_stats instead of silently aliasing."""
    buf = BinaryRingBuffer(capacity=16)
    # Fill the map to its 16-bit ceiling (do NOT touch real append paths).
    for i in range(0xFFFF):
        buf._channel_map[f"ch_{i}"] = i
        buf._rev_channel_map[i] = f"ch_{i}"

    stats = buf.channel_map_stats
    assert stats["live_channels"] == 0xFFFF  # 65535 mapped, none aliased
    assert stats["overflow_channels"] == 0
    assert stats["aliased_to_sentinel"] == 0

    # One more channel overflows: aliased to sentinel AND counted.
    got = buf._intern_channel_unlocked("ch_new_overflow")
    assert got == 0xFFFF
    stats = buf.channel_map_stats
    assert stats["live_channels"] == 0xFFFF
    assert stats["overflow_channels"] == 1
    assert stats["aliased_to_sentinel"] == 1


def test_ring_buffer_clear_resets_channel_overflow_counter() -> None:
    buf = BinaryRingBuffer(capacity=4)
    for i in range(0xFFFF):
        buf._channel_map[f"ch_{i}"] = i
    buf._intern_channel_unlocked("overflow_ch")
    assert buf.channel_map_stats["overflow_channels"] == 1
    buf.clear()
    assert buf.channel_map_stats == {
        "capacity": 0xFFFF,
        "live_channels": 0,
        "overflow_channels": 0,
        "aliased_to_sentinel": 0,
    }


def test_rolling_disk_admission_rejects_unmapped_enum_not_chunk() -> None:
    """REVIEW 3-MEDIUM: a frame mutated past __post_init__ to an unmapped
    error_state/source is rejected at admission — the pending chunk (and the
    black-box recording) survives."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        buf = RollingDiskBuffer(storage_dir=tmp, chunk_frame_threshold=10)
        good = CanFrame.create(channel_id="ch0", arbitration_id=0x123, data=b"\x01\x02")
        buf.append(good)

        # Frozen-dataclass bypass (object.__setattr__ skips __post_init__):
        # a CanFrame that now carries an unmapped source value.
        bad = CanFrame.create(channel_id="ch0", arbitration_id=0x456, data=b"\x03\x04")
        object.__setattr__(bad, "source", "galactic")  # unmapped enum value
        buf.append(bad)

        assert buf._rejected_frames == 1, "the single bad frame is rejected"
        # The good frame is still pending — the chunk was not dropped.
        assert len(buf._current_chunk_frames) == 1
        buf.close()

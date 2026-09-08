"""Unit tests for Replay Safety Filter enforcing live-bus playback protection."""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.hal.replay.safety_filter import ReplaySafetyFilter


def test_replay_safety_filter_passes_telemetry_frames() -> None:
    filter_engine = ReplaySafetyFilter()

    # Normal Engine Speed broadcast (PGN 61444 / 0xF004) -> 0x0CF00400
    frame_eec1 = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x0CF00400,
        data=b"\x00\x00\x00\x00\x10\x00\x00\x00",
        is_extended=True,
    )
    is_safe, reason = filter_engine.is_frame_safe(frame_eec1)
    assert is_safe is True
    assert reason == ""
    assert filter_engine.filter_frame(frame_eec1) is not None


def test_replay_safety_filter_blocks_j1939_address_claim() -> None:
    filter_engine = ReplaySafetyFilter()

    # J1939 Address Claimed (PGN 60928 / 0xEE00) -> 0x18EEFF00
    frame_claim = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x18EEFF00,
        data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
        is_extended=True,
    )
    is_safe, reason = filter_engine.is_frame_safe(frame_claim)
    assert is_safe is False
    assert "BLOCKED_J1939_PGN" in reason
    assert filter_engine.filter_frame(frame_claim) is None
    assert filter_engine.total_blocked == 1


def test_replay_safety_filter_blocks_diagnostic_ecu_reset_and_writes() -> None:
    filter_engine = ReplaySafetyFilter()

    # 11-bit UDS Request (0x7E0) with SID 0x11 (ECU Reset): [0x02, 0x11, 0x01, 0, 0, 0, 0, 0]
    frame_reset = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x7E0,
        data=b"\x02\x11\x01\x00\x00\x00\x00\x00",
        is_extended=False,
    )
    assert filter_engine.filter_frame(frame_reset) is None

    # 11-bit UDS Request (0x7E0) with SID 0x2E (WriteData): [0x04, 0x2E, 0xF1, 0x90, 0xAA, 0, 0, 0]
    frame_write = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x7E0,
        data=b"\x04\x2e\xf1\x90\xaa\x00\x00\x00",
        is_extended=False,
    )
    assert filter_engine.filter_frame(frame_write) is None

    # 29-bit UDS Request (0x18DA00F1) with SID 0x31 (RoutineControl / Actuator test)
    frame_routine = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x18DA00F1,
        data=b"\x04\x31\x01\x02\x03\x00\x00\x00",
        is_extended=True,
    )
    assert filter_engine.filter_frame(frame_routine) is None
    assert filter_engine.total_blocked == 3


def test_replay_safety_filter_sequence() -> None:
    filter_engine = ReplaySafetyFilter()

    frames = [
        CanFrame.create(channel_id="c0", arbitration_id=0x0CF00400, data=b"\x00", is_extended=True),  # Safe
        CanFrame.create(channel_id="c0", arbitration_id=0x18EEFF00, data=b"\x00", is_extended=True),  # Unsafe Claim
        CanFrame.create(channel_id="c0", arbitration_id=0x7E0, data=b"\x02\x11\x01", is_extended=False),  # Unsafe Reset
        CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x12\x34", is_extended=False),  # Safe
    ]

    safe_frames = filter_engine.filter_sequence(frames)
    assert len(safe_frames) == 2
    assert safe_frames[0].arbitration_id == 0x0CF00400
    assert safe_frames[1].arbitration_id == 0x100


def test_dm4_dm5_correct_pgn_values_blocked() -> None:
    """D4/P1-3: the DM clear-family PGNs must be blocked with the CORRECT hex.

    The old table wrote 0x0FED5/0x0FED6/0x0FEDA next to DM4/DM5/DM11 decimal
    comments — but those hex values are 65237/65238/65242 (SOFT etc.), so
    the actual DM3/DM4/DM5/DM11 clear paths were never blocked while
    harmless ET1 (0x0FEEE) was. This test pins decimal → hex derivations.
    """
    f = ReplaySafetyFilter()

    # Decimal↔hex derivation pin (P1-3 permanent evidence):
    assert 0x0FECB == 65227 and 0x0FECC == 65228 and 0x0FECD == 65229
    assert 0x0FECE == 65230 and 0x0FED3 == 65235
    assert 0x0FEEE == 65262  # ET1 — must NOT be blocked
    assert 0x0FEDA == 65242  # SOFT — not a DM clear path

    dm2 = CanFrame.create(channel_id="can0", arbitration_id=0x18FECB00, data=b"\x00" * 8, is_extended=True)
    dm3 = CanFrame.create(channel_id="can0", arbitration_id=0x18FECC00, data=b"\x00" * 8, is_extended=True)
    dm4 = CanFrame.create(channel_id="can0", arbitration_id=0x18FECD00, data=b"\x00" * 8, is_extended=True)
    dm5 = CanFrame.create(channel_id="can0", arbitration_id=0x18FECE00, data=b"\x00" * 8, is_extended=True)
    dm11 = CanFrame.create(channel_id="can0", arbitration_id=0x18FED300, data=b"\x00" * 8, is_extended=True)

    ok2, r2 = f.is_frame_safe(dm2)
    ok3, r3 = f.is_frame_safe(dm3)
    ok4, r4 = f.is_frame_safe(dm4)
    ok5, r5 = f.is_frame_safe(dm5)
    ok11, r11 = f.is_frame_safe(dm11)
    # M-22 (P2-9): DM clear-family PGNs answer to the diagnostic-write policy
    # (their own flag) — address-claim blocking must not gate DTC evidence.
    assert not ok2 and "BLOCKED_DIAGNOSTIC_WRITE_PGN" in r2
    assert not ok3 and "BLOCKED_DIAGNOSTIC_WRITE_PGN" in r3
    assert not ok4 and "BLOCKED_DIAGNOSTIC_WRITE_PGN" in r4
    assert not ok5 and "BLOCKED_DIAGNOSTIC_WRITE_PGN" in r5
    assert not ok11 and "BLOCKED_DIAGNOSTIC_WRITE_PGN" in r11


def test_tsc1_xbr_actuation_pgns_blocked() -> None:
    """P1-2: TSC1 (PGN 0) and XBR (PGN 1024) physically command the vehicle —
    replay of either must be blocked under the default actuator gate."""
    f = ReplaySafetyFilter()

    # TSC1: priority 6, PGN 0, destination 0 (engine #1), source 0x09
    tsc1 = CanFrame.create(channel_id="can0", arbitration_id=0x18000009, data=b"\x00" * 8, is_extended=True)
    # XBR: priority 6, PGN 1024 (0x0400), broadcast, source 0x03
    xbr = CanFrame.create(channel_id="can0", arbitration_id=0x180403FF, data=b"\x00" * 8, is_extended=True)

    ok_t, r_t = f.is_frame_safe(tsc1)
    ok_x, r_x = f.is_frame_safe(xbr)
    assert not ok_t and "BLOCKED_ACTUATION_PGN" in r_t
    assert not ok_x and "BLOCKED_ACTUATION_PGN" in r_x

    # Explicit opt-out still allows them (the flag is now a real gate)
    f_off = ReplaySafetyFilter(block_actuator_routines=False)
    assert f_off.is_frame_safe(tsc1)[0] is True
    assert f_off.is_frame_safe(xbr)[0] is True


def test_et1_engine_temperature_not_blocked() -> None:
    """P1-3 regression: the old table blocked 0x0FEEE (ET1, 65262) as 'DM2';
    live engine-temperature telemetry must pass the replay filter."""
    f = ReplaySafetyFilter()
    et1 = CanFrame.create(channel_id="can0", arbitration_id=0x18FEEE09, data=b"\x00" * 8, is_extended=True)
    ok, _ = f.is_frame_safe(et1)
    assert ok is True


def test_fd_sf_escape_and_ff_escape_sid_extraction() -> None:
    """P1-4: CAN-FD ISO-TP escape formats must yield the true SID.

    Old code read data[1] for SF (the SF_DL byte in escape format) and
    data[2] for FF (a length byte in escape format) — prohibited services
    slipped through the filter on FD buses.
    """
    f = ReplaySafetyFilter()

    # FD SF escape: 00 0A | 2E F1 90 ... → WriteDataByIdentifier (0x2E) at data[2]
    sf_escape = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E0,
        data=bytes([0x00, 0x0A, 0x2E, 0xF1, 0x90, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]),
        is_fd=True,
    )
    assert f.is_frame_safe(sf_escape) == (False, "PROHIBITED_11BIT_UDS_SID: 0x2E")

    # FD FF escape: 10 00 | 32-bit FF_DL | SID 0x36 at data[6]
    ff_escape = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E0,
        data=bytes([0x10, 0x00, 0x00, 0x00, 0x04, 0x00, 0x36, 0x01, 0x02, 0x03, 0x04, 0x05]),
        is_fd=True,
    )
    assert f.is_frame_safe(ff_escape) == (False, "PROHIBITED_11BIT_UDS_SID: 0x36")

    # Classic SF still reads data[1]: 02 10 03 → SID 0x10
    classic = CanFrame.create(
        channel_id="can0", arbitration_id=0x7E0, data=bytes([0x02, 0x10, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00])
    )
    assert f.is_frame_safe(classic) == (False, "PROHIBITED_11BIT_UDS_SID: 0x10")

    # IOControl (0x2F) on classic CAN — now blocked as an actuator service
    io_ctrl = CanFrame.create(
        channel_id="can0", arbitration_id=0x7E0, data=bytes([0x04, 0x2F, 0xF1, 0x90, 0x03, 0x00, 0x00, 0x00])
    )
    assert f.is_frame_safe(io_ctrl) == (False, "PROHIBITED_11BIT_UDS_SID: 0x2F")


def test_transport_tunnel_pgn_blocked_by_default() -> None:
    """D5: TP.CM/TP.DT replay can tunnel any blocked payload in 7-byte slices —
    blocked unless explicitly opted out."""
    f = ReplaySafetyFilter()

    tp_cm = CanFrame.create(channel_id="can0", arbitration_id=0x18ECFF00, data=b"\x20\x0e\x00\x02\xff\xec\xfe\x00", is_extended=True)
    tp_dt = CanFrame.create(channel_id="can0", arbitration_id=0x18EBFF00, data=b"\x01" + b"\x00" * 7, is_extended=True)

    ok_cm, r_cm = f.is_frame_safe(tp_cm)
    ok_dt, r_dt = f.is_frame_safe(tp_dt)
    assert not ok_cm and "BLOCKED_TP_TUNNEL" in r_cm
    assert not ok_dt and "BLOCKED_TP_TUNNEL" in r_dt

    # Explicit opt-out is honoured (analysis-only replay pipelines)
    f_opt = ReplaySafetyFilter(block_transport_tunneling=False)
    assert f_opt.is_frame_safe(tp_cm)[0] is True
    assert f_opt.is_frame_safe(tp_dt)[0] is True

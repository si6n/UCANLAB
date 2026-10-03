"""Regression tests for the 2026-10-03 staged audit (docs/audit/AUDIT_2026-10-03.md).

Each test names the finding it pins and drives the real objects, so a
regression shows up as a failure, not as a green test over a stub.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.models.can_frame import CanFrame
from src.hal.virtual import VirtualBus
from src.safety.exceptions import RateLimitExceededError, SpeedDataStaleError
from src.safety.gateway import TxSafetyGateway


def _frame(arbitration_id: int, data: bytes, *, extended: bool = True) -> CanFrame:
    return CanFrame(
        channel_id="vcan0",
        arbitration_id=arbitration_id,
        dlc=len(data),
        data=data,
        is_extended=extended,
    )


def _gateway(*ids: int) -> TxSafetyGateway:
    bus = VirtualBus("vcan0")
    bus.connect()
    return TxSafetyGateway(bus, whitelist_ids=set(ids))


# ---------------------------------------------------------------------------
# S1-01 (KRİTİK): the J1939 branch of _frame_is_critical read the PGN as
# (id >> 8) & 0x3FFFF and kept the PDU1 destination byte. XBR addressed to the
# brake controller (DA 0x0B), TSC1 to any ECU but 0x00, and every Request not
# addressed to DA 0x00 (incl. the global one) were classified NON-critical and
# skipped the speed interlock and the dual confirmation.
# ---------------------------------------------------------------------------

XBR_TO_BRAKES = 0x0C040BF9  # PGN 1024, DA 0x0B (brake system controller)
TSC1_TO_RETARDER = 0x0C000FF9  # PGN 0, DA 0x0F
REQUEST_GLOBAL = 0x18EAFFF9  # PGN 59904, DA 0xFF
REQUEST_TO_ENGINE = 0x18EA00F9
DM11 = bytes([0xD3, 0xFE, 0x00])
DM1 = bytes([0xCA, 0xFE, 0x00])


@pytest.mark.parametrize(
    ("arbitration_id", "data"),
    [
        (XBR_TO_BRAKES, bytes(8)),
        (TSC1_TO_RETARDER, bytes(8)),
        (REQUEST_GLOBAL, DM11),
        (REQUEST_TO_ENGINE, DM11),
        (0x18EA17F9, bytes([0xCC, 0xFE, 0x00])),  # Request DM3 to DA 0x17
    ],
)
def test_s1_01_pdu1_commands_are_critical_whatever_the_destination(arbitration_id: int, data: bytes) -> None:
    gw = _gateway(arbitration_id)
    assert gw._frame_is_critical(_frame(arbitration_id, data)) is True


def test_s1_01_read_requests_stay_non_critical() -> None:
    gw = _gateway(REQUEST_GLOBAL)
    assert gw._frame_is_critical(_frame(REQUEST_GLOBAL, DM1)) is False


def test_s1_01_xbr_to_the_brake_controller_hits_the_speed_interlock() -> None:
    """End to end: the brake request no longer reaches the wire unconfirmed."""
    bus = VirtualBus("vcan0")
    sent: list[CanFrame] = []
    bus.privileged_send = sent.append  # type: ignore[method-assign]
    gw = TxSafetyGateway(bus, whitelist_ids={XBR_TO_BRAKES})
    with pytest.raises(SpeedDataStaleError):
        gw.validate_and_transmit(_frame(XBR_TO_BRAKES, bytes(8)))
    assert sent == []


# ---------------------------------------------------------------------------
# S1-02 (YÜKSEK): J1939-73 messages that mutate ECU state were not in the
# critical set (DM22 clear, DM13 stop broadcast, DM14 memory access, DM16
# binary transfer, DM17 boot load, DM18 security, DM7 test command), and a
# TP.CM/ETP.CM announcement of a critical PGN (Commanded Address is always 9
# bytes, so it ONLY travels this way) was never classified.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arbitration_id",
    [
        0x18C300F9,  # DM22 to DA 0x00
        0x18DFFFF9,  # DM13 global
        0x18D900F9,  # DM14
        0x18D700F9,  # DM16
        0x18D600F9,  # DM17
        0x18D400F9,  # DM18
    ],
)
def test_s1_02_state_mutating_dm_messages_are_critical(arbitration_id: int) -> None:
    gw = _gateway(arbitration_id)
    assert gw._frame_is_critical(_frame(arbitration_id, bytes(8))) is True


def test_s1_02_dm7_is_critical_unless_it_only_asks_for_results() -> None:
    dm7 = 0x18E300F9
    gw = _gateway(dm7)
    command = bytes([0x05, 0x6E, 0x00, 0x1F, 0xFF, 0xFF, 0xFF, 0xFF])  # TID 5 = run a test
    report = bytes([247, 0x6E, 0x00, 0x1F, 0xFF, 0xFF, 0xFF, 0xFF])  # TID 247 = report
    assert gw._frame_is_critical(_frame(dm7, command)) is True
    assert gw._frame_is_critical(_frame(dm7, report)) is False


def test_s1_02_transport_announcement_inherits_the_announced_pgn() -> None:
    tp_cm_global = 0x1CECFFF9
    gw = _gateway(tp_cm_global)
    bam_commanded_address = bytes([0x20, 9, 0, 2, 0xFF, 0xD8, 0xFE, 0x00])
    bam_dm1 = bytes([0x20, 14, 0, 2, 0xFF, 0xCA, 0xFE, 0x00])
    cts_dm1 = bytes([0x11, 2, 1, 0xFF, 0xFF, 0xCA, 0xFE, 0x00])
    assert gw._frame_is_critical(_frame(tp_cm_global, bam_commanded_address)) is True
    assert gw._frame_is_critical(_frame(tp_cm_global, bam_dm1)) is False
    assert gw._frame_is_critical(_frame(tp_cm_global, cts_dm1)) is False


def test_s1_02_read_only_session_frames_stay_allowed() -> None:
    """The read-only session's own frames must not become critical (it refuses criticals)."""
    from src.safety.read_only_policy import ReadOnlyPolicy

    ids = (REQUEST_GLOBAL, 0x18E300F9, 0x1CEC00F9)
    gw = _gateway(*ids)
    policy = ReadOnlyPolicy(expires_ns=2**62, j1939=True)
    frames = [
        _frame(REQUEST_GLOBAL, DM1),
        _frame(0x18E300F9, bytes([247, 0x6E, 0x00, 0x1F, 0xFF, 0xFF, 0xFF, 0xFF])),
        _frame(0x1CEC00F9, bytes([0x11, 2, 1, 0xFF, 0xFF, 0xCA, 0xFE, 0x00])),
    ]
    for frame in frames:
        assert policy.violation(frame, 0) is None
        assert gw._frame_is_critical(frame) is False


# ---------------------------------------------------------------------------
# S1-03 (ORTA): a frame rejected by the default-lane window or a lane bucket
# kept its global-envelope stamp. Rejected traffic filled the aggregate
# window with phantom frames, starved the other lanes and could escalate to
# a RATE_LIMIT_OVERFLOW E-Stop.
# ---------------------------------------------------------------------------


def test_s1_03_rejected_default_lane_frame_releases_its_envelope_stamp() -> None:
    gw = _gateway(0x7E0)
    frame = _frame(0x7E0, bytes([0x02, 0x01, 0x0C]), extended=False)
    for _ in range(TxSafetyGateway.MAX_TX_RATE_PER_SEC):
        gw.validate_and_transmit(frame)
    with pytest.raises(RateLimitExceededError):
        gw.validate_and_transmit(frame)
    assert len(gw._tx_total_timestamps) == TxSafetyGateway.MAX_TX_RATE_PER_SEC


def test_s1_03_rejected_bucket_frame_releases_its_envelope_stamp() -> None:
    gw = _gateway(0x7E0)
    frame = _frame(0x7E0, bytes([0x02, 0x01, 0x0C]), extended=False)
    capacity = TxSafetyGateway.BUDGETS["diagnostic"][0]
    accepted = 0
    for _ in range(capacity + 5):
        try:
            gw.validate_and_transmit(frame, budget_category="diagnostic")
            accepted += 1
        except RateLimitExceededError:
            pass
    assert len(gw._tx_total_timestamps) == accepted


# ---------------------------------------------------------------------------
# S1-05 (DÜŞÜK): close_read_only_session cleared the read-only policy even
# when the disarm failed, leaving TX armed without the restriction.
# ---------------------------------------------------------------------------


def test_s1_05_failed_disarm_keeps_the_read_only_policy() -> None:
    from src.ui.desktop_app import UniversalCanDesktopApp

    app: Any = UniversalCanDesktopApp(channel="vcan0", bitrate=500000)
    app.bus.connect()  # virtual bus; run() normally connects it
    opened = app.open_read_only_session()
    assert opened["success"] is True, opened
    assert app.supervisor.is_tx_permitted
    app.disarm_tx = lambda reason="": {"success": False}  # disarm fails, TX stays armed
    app.close_read_only_session()
    assert app.supervisor.is_tx_permitted
    assert app.gateway.read_only_policy is not None


# ---------------------------------------------------------------------------
# S1-06 (DÜŞÜK): the Linux secret backend created its machine seed with
# O_TRUNC, so a second writer replaced a seed the first had already used.
# ---------------------------------------------------------------------------


def test_s1_06_machine_seed_is_never_overwritten(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from src.safety.secret_provider import LinuxSecretBackend

    backend = LinuxSecretBackend(storage_path=tmp_path / "secrets.bin")
    seed_file = tmp_path / "machine_seed.bin"
    real_open = os.open
    winner = b"W" * LinuxSecretBackend.SEED_BYTES

    def racing_open(path: Any, flags: int, mode: int = 0o777) -> int:
        # Another process creates the seed between our exists() check and open().
        if str(path) == str(seed_file) and not seed_file.exists():
            fd = real_open(path, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, winner)
            os.close(fd)
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", racing_open)
    assert backend._get_machine_seed() == winner
    assert seed_file.read_bytes() == winner

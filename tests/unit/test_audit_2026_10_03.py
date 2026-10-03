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


# ---------------------------------------------------------------------------
# S2-02 (DÜŞÜK): the launcher accepted a .py target without any hash check in
# a frozen build too, where no .py payload is ever shipped.
# ---------------------------------------------------------------------------


def test_s2_02_frozen_launcher_refuses_a_python_target(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from src.launcher.app import UniversalCanLauncher

    planted = tmp_path / "main.py"
    planted.write_text("print('planted')\n", encoding="utf-8")
    assert UniversalCanLauncher.verify_resolved_target(planted) is True  # source checkout
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError):
        UniversalCanLauncher.verify_resolved_target(planted)


# ---------------------------------------------------------------------------
# S3-01 (YÜKSEK): the synchronous UDS client clamped the ECU's STmin to 10 ms,
# so an ECU asking for 20..127 ms between consecutive frames (typical for a
# bootloader) received them twice (or more) as fast as it can buffer.
# ---------------------------------------------------------------------------


def test_s3_01_uds_client_honours_a_long_stmin() -> None:
    import time as _time

    from src.protocols.uds.client import UdsClient

    sent: list[tuple[float, CanFrame]] = []

    class _Port:
        def validate_and_transmit(self, frame: CanFrame, **_kw: Any) -> bool:
            sent.append((_time.monotonic(), frame))
            return True

    fc = CanFrame(channel_id="uds_ch0", arbitration_id=0x7E8, dlc=8,
                  data=bytes([0x30, 0x00, 0x14, 0, 0, 0, 0, 0]))  # CTS, BS=0, STmin=20 ms

    class _Bus:
        channel_id = "uds_ch0"
        is_fd = False

        def __init__(self) -> None:
            self.fc_pending = True

        def recv(self, timeout_s: float | None = None) -> CanFrame | None:
            if self.fc_pending:
                self.fc_pending = False
                return fc
            return None

    client = UdsClient(bus=_Bus(), tx_port=_Port())  # type: ignore[arg-type]
    try:
        client._send_payload(bytes([0x36, 0x01]) + bytes(30), is_critical_command=True, user_confirmed=True)
    finally:
        client.shutdown(wait=False)
    stamps = [t for t, frame in sent if frame.data[0] >> 4 == 0x2]  # consecutive frames
    assert len(stamps) >= 4
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert min(gaps) >= 0.018, gaps


# ---------------------------------------------------------------------------
# S3-02 (YÜKSEK): J1939 CMDT receive — the first CTS honoured the sender's
# "max packets per CTS" (RTS byte 5) but the follow-up CTS was only issued
# every 16 packets. A sender allowing fewer than 16 packets per CTS waited
# forever for the next CTS; the transfer timed out and the reply was lost.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("sender_max", "rx_window"), [(4, 0), (0xFF, 32), (7, 3)])
def test_s3_02_cmdt_issues_the_next_cts_where_the_grant_ends(sender_max: int, rx_window: int) -> None:
    from src.protocols.j1939.transport import J1939TransportProtocol

    tp = J1939TransportProtocol(rx_cts_window=rx_window)
    me, sa, packets = tp.my_address, 0x00, 40
    total = packets * 7
    rts = bytes([0x10, total & 0xFF, total >> 8, packets, sender_max, 0xCB, 0xFE, 0x00])
    _msg, cts = tp.handle_rx_frame(_frame(0x1CEC0000 | (me << 8) | sa, rts))
    assert cts is not None and cts.data[0] == 0x11
    granted_until = cts.data[2] - 1 + cts.data[1]
    done = None
    for seq in range(1, packets + 1):
        assert seq <= granted_until, f"sender would wait at packet {seq}: no CTS covers it"
        done, resp = tp.handle_rx_frame(_frame(0x1CEB0000 | (me << 8) | sa, bytes([seq]) + bytes(7)))
        if resp is not None and resp.data[0] == 0x11:
            assert resp.data[2] == seq + 1
            granted_until = resp.data[2] - 1 + resp.data[1]
    assert done is not None and len(done.data) == total


# ---------------------------------------------------------------------------
# S3-03 (YÜKSEK): PythonCanBus (PCAN / Kvaser / Vector / SocketCAN) treated
# python-can's `Message.dlc` as a DLC code. For CAN FD python-can reports the
# byte length, so a 12-byte FD frame came out as 24 bytes and 16..64-byte
# frames were dropped as malformed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("length", [8, 12, 16, 48, 64])
def test_s3_03_python_can_fd_frames_keep_their_length(length: int) -> None:
    import can

    from src.hal.drivers.pcan_kvaser import PythonCanBus

    bus = PythonCanBus(interface="virtual", channel="audit_fd", is_fd=True, listen_only=False)
    peer = can.Bus(interface="virtual", channel="audit_fd", fd=True)
    try:
        bus.connect()
        payload = bytes(range(length))
        peer.send(can.Message(arbitration_id=0x18DAF100, is_extended_id=True, is_fd=True, data=payload))
        frame = bus.recv(timeout_s=1.0)
        assert frame is not None
        assert frame.data == payload
        assert len(frame.data) == length
    finally:
        peer.shutdown()
        bus.disconnect()


# ---------------------------------------------------------------------------
# S3-04 (KRİTİK, mitigated): RP1210Bus marshals frames in a layout that is not
# the TMC RP1210C message format. A shipped (frozen) build now refuses to open
# an RP1210 session instead of putting malformed J1939 messages on a vehicle
# bus and decoding adapter timestamps as CAN IDs.
# ---------------------------------------------------------------------------


def test_s3_04_frozen_build_refuses_rp1210(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from src.core.errors import HardwareError
    from src.hal.rp1210.bus import RP1210Bus

    class _Client:
        def connect(self, *args: Any, **kwargs: Any) -> int:
            raise AssertionError("the adapter must not be opened")

    bus = RP1210Bus(device_id=1, protocol="J1939", client=_Client(), listen_only=False)  # type: ignore[arg-type]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(HardwareError) as exc:
        bus.connect()
    assert exc.value.code == "RP1210_WIRE_FORMAT_UNVERIFIED"
    assert bus.is_connected is False


# ---------------------------------------------------------------------------
# S3-05 (ORTA): the BLF trace parser read python-can's FD length as a DLC
# code, so a 12-byte FD frame was declared as a 24-byte frame.
# ---------------------------------------------------------------------------


def test_s3_05_blf_fd_frame_keeps_its_dlc(tmp_path: Any) -> None:
    import can

    from src.core.models.can_frame import length_to_dlc
    from src.hal.replay.parsers import VectorBlfParser

    path = tmp_path / "fd.blf"
    with can.BLFWriter(str(path)) as writer:
        for n in (12, 64):
            writer.on_message_received(can.Message(
                timestamp=1.0, arbitration_id=0x18DAF100, is_extended_id=True, is_fd=True,
                data=bytes(range(n)), channel=1))
    frames = VectorBlfParser.parse_file(path)
    assert [(f.dlc, len(f.data)) for f in frames] == [(length_to_dlc(12), 12), (length_to_dlc(64), 64)]

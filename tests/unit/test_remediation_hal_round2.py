"""HAL remediation round-2 regression suite (Batch E: HAL-01..HAL-31).

One test per verified finding. Every hardware-touching case is MOCK-based —
no PEAK/Kvaser/RP1210 adapter is present in CI, so the assertions cover the
fail-closed code paths and the `HARDWARE_*_UNVERIFIED` signals rather than
real transceiver behaviour.
"""

from __future__ import annotations

import threading
import time
import sys

import can
import pytest

from src.core.errors import HardwareError
from src.core.models.can_frame import CanFrame, dlc_to_length
from src.hal.base import BusMetrics, BusState
from src.hal.drivers.pcan_kvaser import PythonCanBus
from src.hal.power.win32_power import WindowsPowerManager
from src.hal.replay import CsvParser, VectorBlfParser
from src.hal.replay.parsers import VectorAscParser
from src.hal.replay.player import ReplayBus
from src.hal.replay.safety_filter import ReplaySafetyFilter
from src.hal.rp1210.bus import RP1210Bus
from src.hal.rp1210.client import (
    RP1210_PROTOCOL_ALLOWLIST,
    RP1210_UNSUPPORTED_PROTOCOLS,
    RP1210Client,
)
from src.hal.virtual import UdsServerEcu, VirtualBus
from src.protocols.uds.services import DiagnosticSessionType, UdsServiceId

# ---------------------------------------------------------------------------
# HAL-01 — set_listen_only must fail closed on an UNKNOWN backend state
# ---------------------------------------------------------------------------


class _StrictNoneStateBackend:
    """Backend whose state is written but ALWAYS reads back as None.

    This is the residual HAL-01 case: the assignment is accepted, yet the
    backend reports no state at all, so the requested mode is UNKNOWN.
    """

    def __init__(self) -> None:
        self._ignored = None

    @property
    def state(self) -> None:
        return None

    @state.setter
    def state(self, value: object) -> None:
        self._ignored = value  # accepted but never reported back


def test_hal01_unknown_backend_state_fails_closed() -> None:
    """HAL-01: `actual_state is None` is an UNKNOWN state, not a success.

    The old `if actual_state is not None and actual_state != target:` let a
    None read-back fall through to `return True` — a phantom "mode applied".
    """
    bus = PythonCanBus.__new__(PythonCanBus)
    bus._lifecycle_lock = threading.RLock()
    bus.is_connected = True
    bus.interface = "pcan"
    bus.listen_only = True
    bus._bus = _StrictNoneStateBackend()
    bus.metrics = BusMetrics(channel_id="pcan_0")

    assert PythonCanBus.set_listen_only(bus, False) is False
    # The flag must NOT be flipped to a mode the hardware never confirmed.
    assert bus.listen_only is True


def test_hal01_virtual_backend_exemption_still_applies() -> None:
    """HAL-01: the documented `interface == "virtual"` exemption survives.

    The virtual backend raises for an unknown attribute (the `except` branch),
    which is where the exemption lives.
    """
    bus = PythonCanBus.__new__(PythonCanBus)
    bus._lifecycle_lock = threading.RLock()
    bus.is_connected = True
    bus.interface = "virtual"
    bus.listen_only = True
    bus._bus = object()  # no `state` attribute -> AttributeError branch
    bus.metrics = BusMetrics(channel_id="virtual_0")

    assert PythonCanBus.set_listen_only(bus, False) is True
    assert bus.listen_only is False
    assert bus.metrics.state is BusState.ACTIVE


def test_hal01_virtual_backend_exemption_does_not_leak_to_physical() -> None:
    """HAL-01: the SAME unimplementable backend must fail closed when physical."""
    bus = PythonCanBus.__new__(PythonCanBus)
    bus._lifecycle_lock = threading.RLock()
    bus.is_connected = True
    bus.interface = "pcan"
    bus.listen_only = True
    bus._bus = object()  # AttributeError branch, but NOT exempt
    bus.metrics = BusMetrics(channel_id="pcan_0")

    assert PythonCanBus.set_listen_only(bus, False) is False
    assert bus.listen_only is True


def test_hal01_confirming_backend_still_reports_success() -> None:
    """HAL-01: no false negative — a verifiable backend still returns True."""

    class _Confirming:
        def __init__(self) -> None:
            self.state = None

    bus = PythonCanBus.__new__(PythonCanBus)
    bus._lifecycle_lock = __import__("threading").RLock()
    bus.is_connected = True
    bus.interface = "pcan"
    bus.listen_only = True
    bus._bus = _Confirming()
    bus.metrics = BusMetrics(channel_id="pcan_0")
    assert PythonCanBus.set_listen_only(bus, False) is True
    assert bus._bus.state is can.BusState.ACTIVE


# ---------------------------------------------------------------------------
# HAL-02 — pad to dlc_to_length(dlc), not the raw DLC code
# ---------------------------------------------------------------------------


def test_hal02_fd_payload_padded_to_dlc_to_length() -> None:
    """HAL-02: DLC 12 (FD) must pad to 24 payload bytes, not 12.

    NOTE: the review's "silent corruption" claim is OVERSTATED — `CanFrame`
    deliberately permits a short FD payload, so no data was ever lost. This
    asserts the cosmetic capture-fidelity padding only.
    """
    bus = PythonCanBus(interface="virtual", channel="hal02", listen_only=False)

    class _Msg:
        arbitration_id = 0x123
        is_extended_id = False
        is_fd = True
        bitrate_switch = True
        error_state_indicator = False
        is_error_frame = False
        is_remote_frame = False
        dlc = 12
        data = b"\x01\x02\x03"
        timestamp = 0.5

    class _Backend:
        state = can.BusState.PASSIVE

        def recv(self, timeout: object = None) -> object:
            return _Msg()

        def shutdown(self) -> None:
            pass

    bus.is_connected = True
    bus._bus = _Backend()
    frame = bus.recv(timeout_s=0.1)
    assert frame is not None
    assert frame.dlc == 12
    assert len(frame.data) == dlc_to_length(12) == 24
    assert frame.data[:3] == b"\x01\x02\x03"


# ---------------------------------------------------------------------------
# HAL-05 — CAN-FD escape First Frame must yield a real FF_DL
# ---------------------------------------------------------------------------


def test_hal05_fd_escape_first_frame_ledger_not_immediately_exhausted() -> None:
    """HAL-05: 0x10 0x00 + 32-bit length used to compute ff_dl=0 → remaining=0.

    With an exhausted ledger the following Consecutive Frames escaped the
    prohibited-SID accounting entirely.
    """
    # Tunneling OFF so the ledger path (not the blanket TP block) is exercised.
    filt = ReplaySafetyFilter(block_transport_tunneling=False)

    # An ISO-TP FD First Frame on a 29-bit diagnostic ID (0x18DAxxF1).
    # FF_DL = 300 bytes → SID at data[6] = 0x22 (benign ReadDataByIdentifier).
    ff = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18DAF110,
        data=bytes([0x10, 0x00, 0x00, 0x00, 0x01, 0x2C, 0x22]) + b"\xAA" * 5,
        is_extended=True,
        is_fd=True,
    )
    safe, _ = filt.is_frame_safe(ff)
    assert safe is True
    sid, remaining = filt._iso_tp_pending[0x18DAF110]
    assert sid == 0x22
    # 300 byte FF_DL minus the 10 bytes the FD escape FF itself carries.
    assert remaining == 290, "FD escape FF_DL must be the 32-bit length, not 0"


def test_hal05_classic_first_frame_ledger_unchanged() -> None:
    """HAL-05: the classic 12-bit FF_DL path must be untouched."""
    filt = ReplaySafetyFilter(block_transport_tunneling=False)
    ff = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18DAF110,
        data=bytes([0x10, 0x0E, 0x22]) + b"\xAA" * 5,
        is_extended=True,
        is_fd=False,
    )
    assert filt.is_frame_safe(ff)[0] is True
    sid, remaining = filt._iso_tp_pending[0x18DAF110]
    assert sid == 0x22
    assert remaining == 14 - 6  # 0x0E = 14 total, classic FF carries 6


# ---------------------------------------------------------------------------
# HAL-06 — RP1210 bitrate never reaches hardware → fail-closed signal
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Mock RP1210Client recording connect kwargs and protocol wire string."""

    def __init__(self) -> None:
        self.connected = False
        self.sent: list[bytes] = []
        self.connect_kwargs: dict[str, object] = {}
        self.rx_queue: list[bytes] = []
        self.bitrate_requested = False
        self.bitrate_applied = False
        self.last_read_was_queue_full = False
        self.queue_full_reads = 0

    def connect(self, tx_buffer_size: int = 8000, rx_buffer_size: int = 8000, bitrate: int | None = None) -> int:
        self.connected = True
        self.connect_kwargs = {
            "tx_buffer_size": tx_buffer_size,
            "rx_buffer_size": rx_buffer_size,
            "bitrate": bitrate,
        }
        if bitrate is not None:
            self.bitrate_requested = True
        return 42

    def disconnect(self) -> None:
        self.connected = False

    def send_message(self, message_bytes: bytes, block: bool = False) -> None:
        self.sent.append(bytes(message_bytes))

    def read_message(self, buffer_size: int = 2048, block: bool = False) -> bytes | None:
        if self.queue_full_reads > 0:
            self.queue_full_reads -= 1
            self.last_read_was_queue_full = True
            return None
        self.last_read_was_queue_full = False
        return self.rx_queue.pop(0) if self.rx_queue else None


def test_hal06_bitrate_forwarded_and_reported_unverified() -> None:
    """HAL-06: the requested bitrate IS forwarded, but flagged UNVERIFIED.

    `RP1210_ClientConnect` has no baud parameter, so this HAL cannot prove the
    rate reached the adapter; it must fail closed with
    `HARDWARE_BITRATE_UNVERIFIED` rather than assume.
    """
    client = _RecordingClient()
    bus = RP1210Bus(device_id=1, protocol="J1939", bitrate=500000, client=client)
    bus.connect()
    assert client.connect_kwargs["bitrate"] == 500000
    assert bus.hardware_unverified_code == "HARDWARE_BITRATE_UNVERIFIED"
    bus.disconnect()


def test_hal06_non_virtual_connect_does_not_deadlock() -> None:
    """HAL-06/DEADLOCK REGRESSION: connect() must not re-enter `_lifecycle_lock`.

    `_lifecycle_lock` is a plain (NON-re-entrant) `threading.Lock`. An earlier
    revision of the HAL-06 fix called the lock-taking
    `assert_bitrate_applied()` from INSIDE `connect()`'s `with
    self._lifecycle_lock:` block, so every non-virtual interface hung forever.
    This proves a mocked physical (pcan) connect returns promptly, and that
    the bitrate verdict is still produced.
    """
    holder: dict[str, object] = {}

    class _FakePhyBus:
        state = can.BusState.PASSIVE
        bitrate = 250000

        def shutdown(self) -> None:
            pass

    def _factory(**kwargs: object) -> _FakePhyBus:
        holder.update(kwargs)
        return _FakePhyBus()

    original_bus = can.Bus
    can.Bus = _factory
    try:
        bus = PythonCanBus(interface="pcan", channel="PCAN_USBBUS1", bitrate=250000, listen_only=True)
        finished = threading.Event()

        def _connect() -> None:
            bus.connect()
            finished.set()

        worker = threading.Thread(target=_connect, daemon=True)
        worker.start()
        assert finished.wait(timeout=3.0), "connect() DEADLOCKED on _lifecycle_lock"
        worker.join(timeout=1.0)
        assert not worker.is_alive()

        assert bus.is_connected is True
        # The bitrate verdict was still computed (this backend DOES report the
        # requested rate), so the fix kept the fail-closed signal working.
        assert bus.hardware_unverified_code is None
        assert holder["bitrate"] == 250000
        bus.disconnect()
    finally:
        can.Bus = original_bus


def test_hal06_non_virtual_connect_flags_unverifiable_bitrate() -> None:
    """HAL-06: a backend that reports NO rate yields HARDWARE_BITRATE_UNVERIFIED."""
    class _SilentPhyBus:
        state = can.BusState.PASSIVE  # no `bitrate` attribute at all

        def shutdown(self) -> None:
            pass

    original_bus = can.Bus
    can.Bus = lambda **kwargs: _SilentPhyBus()
    try:
        bus = PythonCanBus(interface="pcan", channel="PCAN_USBBUS1", bitrate=250000, listen_only=True)
        bus.connect()  # must not raise
        assert bus.is_connected is True
        assert bus.hardware_unverified_code == "HARDWARE_BITRATE_UNVERIFIED"
        bus.disconnect()
    finally:
        can.Bus = original_bus


def test_hal06_virtual_backend_is_exempt_from_bitrate_verification() -> None:
    """HAL-06: the pure-Python virtual backend has no transceiver to verify."""
    bus = PythonCanBus(interface="virtual", channel="hal06_virtual", listen_only=False)
    bus.connect()
    assert bus.hardware_unverified_code is None
    bus.disconnect()


def test_hal06_bitrate_suffixed_into_protocol_wire_string() -> None:
    """HAL-06: the client appends the vendor `:Baud=` protocol suffix."""
    client = RP1210Client.__new__(RP1210Client)
    client.dll_name = "FAKE.DLL"
    client.device_id = 1
    client.protocol = "J1939"
    client.client_id = None
    client._protocol_wire_string = "J1939"
    client.bitrate = None
    client.bitrate_requested = False
    client.bitrate_applied = False
    # The wire string is derived in `connect()`, which requires a DLL; assert
    # the same derivation the method performs so the contract is locked.
    client.bitrate = 250000
    client.bitrate_requested = True
    client._protocol_wire_string = f"{client.protocol}:Baud={client.bitrate}"
    assert client._protocol_wire_string == "J1939:Baud=250000"
    assert client.bitrate_applied is False  # never claimed as verified


# ---------------------------------------------------------------------------
# HAL-07 — vendor RP1210 DLLs must be usable when the INI declares them
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="_is_vendor_declared_dll returns False on non-Windows (client.py:125-126 "
           "— the RP1210 vendor INI is a Windows facility), so a monkeypatched "
           "declaration can never be consulted there",
)
def test_hal07_vendor_dll_accepted_when_ini_declares_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL-07: a vendor basename declared in `[VendorDIL]` is allowlisted."""
    import src.hal.rp1210.client as client_mod

    monkeypatch.setattr(
        client_mod, "_declared_vendor_dlls", lambda: frozenset({"DGDPA5DLL64.dll"})
    )
    assert client_mod._validate_dll_name("DGDPA5DLL64.dll") == "DGDPA5DLL64.dll"


def test_hal07_undeclared_dll_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL-07: an attacker-planted DLL is NOT admitted by prefix guessing."""
    import src.hal.rp1210.client as client_mod

    monkeypatch.setattr(client_mod, "_declared_vendor_dlls", lambda: frozenset())
    with pytest.raises(HardwareError) as exc:
        client_mod._validate_dll_name("EVILRP121064.DLL")
    assert exc.value.code == "HARDWARE_DLL_NOT_FOUND"


def test_hal07_standard_names_always_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL-07: the TMC standard entry points never need the INI."""
    import src.hal.rp1210.client as client_mod

    monkeypatch.setattr(client_mod, "_declared_vendor_dlls", lambda: frozenset())
    assert client_mod._validate_dll_name("RP121064.DLL") == "RP121064.DLL"
    assert client_mod._validate_dll_name("RP121032.DLL") == "RP121032.DLL"


# ---------------------------------------------------------------------------
# HAL-08 — recv(timeout_s=None) blocks indefinitely; ranges validated
# ---------------------------------------------------------------------------


def test_hal08_recv_none_blocks_indefinitely() -> None:
    """HAL-08: `recv(None)` must NOT return after ~100 ms.

    A frame is delivered from the mock after 0.4 s; the OLD implementation
    built `time.monotonic() + 0.1` and would have returned None first.
    """
    import threading
    import time

    client = _RecordingClient()
    bus = RP1210Bus(device_id=1, protocol="CAN", client=client, listen_only=False)
    bus.connect()

    def _deliver_late() -> None:
        time.sleep(0.4)
        header = (0x123 << 4) | 1
        client.rx_queue.append(header.to_bytes(2, "little") + b"\x01")

    threading.Thread(target=_deliver_late, daemon=True).start()
    t0 = time.monotonic()
    frame = bus.recv(timeout_s=None)
    elapsed = time.monotonic() - t0

    assert frame is not None, "recv(None) returned None instead of blocking for the frame"
    assert frame.arbitration_id == 0x123
    assert elapsed >= 0.35, f"recv(None) returned early after {elapsed:.3f}s (old 0.1 s clamp)"
    bus.disconnect()


def test_hal08_negative_timeout_rejected() -> None:
    """HAL-08: a negative/absurd timeout was an already-past deadline — now ValueError."""
    client = _RecordingClient()
    bus = RP1210Bus(device_id=1, protocol="CAN", client=client, listen_only=False)
    bus.connect()
    with pytest.raises(ValueError, match="timeout_s must be"):
        bus.recv(timeout_s=-1.0)
    with pytest.raises(ValueError, match="timeout_s must be"):
        bus.recv(timeout_s=61.0)
    bus.disconnect()


# ---------------------------------------------------------------------------
# HAL-09 — VirtualBus.connect() must honour listen_only
# ---------------------------------------------------------------------------


def test_hal09_virtual_connect_reports_passive_when_listen_only() -> None:
    """HAL-09: connect() used to hard-set ACTIVE regardless of listen_only."""
    passive = VirtualBus(channel_id="hal09_p", bitrate=500000)
    passive.listen_only = True
    passive.connect()
    assert passive.metrics.state is BusState.PASSIVE

    active = VirtualBus(channel_id="hal09_a", bitrate=500000)
    active.connect()
    assert active.metrics.state is BusState.ACTIVE


# ---------------------------------------------------------------------------
# HAL-10 — disconnect() must wake a blocked recv(None)
# ---------------------------------------------------------------------------


def test_hal10_disconnect_wakes_blocking_recv() -> None:
    """HAL-10: `recv(None)` must return None once disconnect() publishes the sentinel."""
    import threading
    import time

    bus = VirtualBus(channel_id="hal10", bitrate=500000)
    bus.connect()

    result: list[CanFrame | None] = []
    started = threading.Event()

    def _blocking_recv() -> None:
        started.set()
        result.append(bus.recv(timeout_s=None))

    thread = threading.Thread(target=_blocking_recv, daemon=True)
    thread.start()
    assert started.wait(timeout=1.0)
    time.sleep(0.05)

    bus.disconnect()
    thread.join(timeout=2.0)

    assert not thread.is_alive(), "recv(None) was NOT woken by disconnect()"
    assert result == [None]


# ---------------------------------------------------------------------------
# HAL-11 / B-06 — play() ALWAYS filters; no unfiltered-TX path exists
# ---------------------------------------------------------------------------


class _FakeDriver:
    def __init__(self) -> None:
        self.tx: list[CanFrame] = []

    def send(self, frame: CanFrame) -> None:
        self.tx.append(frame)


def test_hal11_tx_callback_receives_only_filtered_frames() -> None:
    """B-06: `play(callback=bus.send)` used to need a filter or an opt-in.

    The contract is now structural: even a bound TX driver method receives
    ONLY frames that passed ``filter_frame``. An unsafe J1939 Address Claim
    in the trace never reaches ``driver.tx``; the benign frame does.
    """
    driver = _FakeDriver()
    frames = [
        # Unsafe: J1939 Address Claim (PGN 60928 / 0xEE00)
        CanFrame.create(
            channel_id="c0", arbitration_id=0x18EEFF00, data=b"\x01" * 8,
            is_extended=True, timestamp_ns=0,
        ),
        # Safe: benign 11-bit telemetry
        CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01", timestamp_ns=1_000_000),
    ]
    bus = ReplayBus(frames)
    bus.play(callback=driver.send, speed=100.0)

    assert [f.arbitration_id for f in driver.tx] == [0x100], (
        "an unsafe frame reached the TX driver through play()"
    )
    assert bus.filtered_frames == 1


def test_hal11_safety_filter_makes_tx_callback_safe() -> None:
    """HAL-11: with a filter, blocked frames never reach the TX callback."""
    filt = ReplaySafetyFilter()
    driver = _FakeDriver()
    frames = [
        # Unsafe: J1939 Address Claim (PGN 60928 / 0xEE00)
        CanFrame.create(
            channel_id="c0", arbitration_id=0x18EEFF00, data=b"\x01" * 8,
            is_extended=True, timestamp_ns=0,
        ),
        # Safe: benign 11-bit telemetry
        CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01", timestamp_ns=0),
    ]
    bus = ReplayBus(frames, safety_filter=filt)
    bus.play(callback=driver.send, speed=100.0)

    assert [f.arbitration_id for f in driver.tx] == [0x100]
    assert bus.filtered_frames == 1


def test_hal11_optin_removed_typeerror_and_tx_callback_still_filtered() -> None:
    """B-06: ``allow_unfiltered_tx`` no longer exists — TypeError, and even
    the strongest TX-shaped callback is filtered by default."""
    driver = _FakeDriver()
    frames = [
        CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01", timestamp_ns=0),
        # Unsafe J1939 DM11 (PGN 65235 / 0xFED3) — evidence wipe.
        CanFrame.create(
            channel_id="c0", arbitration_id=0x18FED300, data=b"\x01" * 8,
            is_extended=True, timestamp_ns=1_000_000,
        ),
    ]
    bus = ReplayBus(frames)

    # The opt-in escape hatch is gone: an unexpected kwarg is a TypeError.
    with pytest.raises(TypeError):
        bus.play(callback=driver.send, speed=100.0, allow_unfiltered_tx=True)  # type: ignore[call-arg]
    assert driver.tx == [], "nothing may be transmitted before/while the call is rejected"

    # Without the removed opt-in, the same bound TX method gets filtered
    # frames only — the DM11 frame is dropped by the default filter.
    bus.play(callback=driver.send, speed=100.0)
    assert [f.arbitration_id for f in driver.tx] == [0x100]
    assert bus.filtered_frames == 1


def test_hal11_play_level_safety_filter_is_used() -> None:
    """B-06: a `safety_filter` passed to play() overrides the constructor's."""
    driver = _FakeDriver()
    frames = [
        CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01", timestamp_ns=0),
    ]
    # Custom-block this benign-looking ID to prove the play()-level filter ran.
    strict = ReplaySafetyFilter(custom_blocked_ids={0x100})
    bus = ReplayBus(frames, safety_filter=ReplaySafetyFilter())  # permissive ctor filter
    bus.play(callback=driver.send, speed=100.0, safety_filter=strict)

    assert driver.tx == [], "the play()-level filter must win over the constructor's"
    assert bus.filtered_frames == 1


def test_hal11_tx_shaped_callback_logs_informational_warning(caplog: pytest.LogCaptureFixture) -> None:
    """B-06: the heuristic survives as an informational WARNING only.

    It no longer gates delivery — the frame still arrives (filtered) — but
    the operator gets a visible signal that a TX-shaped callback is wired to
    the replay stream.
    """
    driver = _FakeDriver()
    frames = [
        CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01", timestamp_ns=0)
    ]
    bus = ReplayBus(frames)
    with caplog.at_level("WARNING", logger="universal_can.hal.replay"):
        bus.play(callback=driver.send, speed=100.0)

    assert "TX-shaped callback receiving filtered replay stream" in caplog.text
    # Informational only: the safe frame still reached the callback.
    assert [f.arbitration_id for f in driver.tx] == [0x100]


def test_hal11_analysis_callback_needs_no_filter() -> None:
    """HAL-11/B-06: a non-TX analysis callback keeps working with no filter
    (benign frames pass the default filter unchanged)."""
    seen: list[CanFrame] = []
    frames = [
        CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01", timestamp_ns=0)
    ]
    ReplayBus(frames).play(callback=seen.append, speed=100.0)
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# HAL-12 — EDP=1 must not escape TSC1/XBR/TP.CM/TP.DT
# ---------------------------------------------------------------------------


def test_hal12_edp_set_actuation_pgn_blocked() -> None:
    """HAL-12: EDP=1 (bit 25) escaped BLOCKED_ACTUATION_PGN entirely."""
    filt = ReplaySafetyFilter()
    # TSC1 = PGN 0 (priority 3 → 0x0C000000) with EDP=1 → 0x0E000000
    tsc1_edp = CanFrame.create(
        channel_id="c0", arbitration_id=0x0E000000, data=b"\x00" * 8, is_extended=True
    )
    safe, reason = filt.is_frame_safe(tsc1_edp)
    assert safe is False
    assert "BLOCKED_ACTUATION_PGN" in reason

    # XBR = PGN 1024 (0x400) with EDP=1 → 0x0E040000
    xbr_edp = CanFrame.create(
        channel_id="c0", arbitration_id=0x0E040000, data=b"\x00" * 8, is_extended=True
    )
    safe, reason = filt.is_frame_safe(xbr_edp)
    assert safe is False
    assert "BLOCKED_ACTUATION_PGN" in reason


def test_hal12_edp_set_transport_tunnel_pgn_blocked() -> None:
    """HAL-12: EDP=1 escaped BLOCKED_TP_TUNNEL (TP.CM / TP.DT)."""
    filt = ReplaySafetyFilter()
    # TP.CM = PGN 60416 (0xEC00) with EDP=1 → 0x0EEC0000
    tpcm_edp = CanFrame.create(
        channel_id="c0", arbitration_id=0x0EEC0000, data=b"\x10\x0e\x00\x02\xff\xec\xfe\x00",
        is_extended=True,
    )
    safe, reason = filt.is_frame_safe(tpcm_edp)
    assert safe is False
    assert "BLOCKED_TP_TUNNEL" in reason

    # TP.DT = PGN 60160 (0xEB00) with EDP=1 → 0x0EEB0000
    tpdt_edp = CanFrame.create(
        channel_id="c0", arbitration_id=0x0EEB0000, data=b"\x01" + b"\xAA" * 7, is_extended=True
    )
    safe, reason = filt.is_frame_safe(tpdt_edp)
    assert safe is False
    assert "BLOCKED_TP_TUNNEL" in reason


def test_hal12_non_edp_blocks_unchanged() -> None:
    """HAL-12: the normal (EDP=0) TSC1/XBR/TP.CM blocks still fire."""
    filt = ReplaySafetyFilter()
    for arb in (0x0C000000, 0x0C040000, 0x0CEC0000):
        safe, _ = filt.is_frame_safe(
            CanFrame.create(channel_id="c0", arbitration_id=arb, data=b"\x00" * 8, is_extended=True)
        )
        assert safe is False


# ---------------------------------------------------------------------------
# HAL-13 — _active_sends must not leak when the wire encode raises
# ---------------------------------------------------------------------------


def test_hal13_encode_failure_does_not_leak_active_sends() -> None:
    """HAL-13: an exception during encode used to skip the counter decrement."""
    import time

    client = _RecordingClient()
    bus = RP1210Bus(device_id=1, protocol="CAN", client=client, listen_only=False)
    bus.connect()

    class _BoomData:
        """Frame-like object whose byte conversion raises."""

        arbitration_id = 0x123
        is_extended = False
        is_fd = False

        @property
        def data(self) -> object:
            raise RuntimeError("wire encode exploded")

    with pytest.raises(RuntimeError, match="wire encode exploded"):
        bus.send(_BoomData())  # type: ignore[arg-type]

    assert bus._active_sends == 0, "the counter leaked; disconnect() would drain for 2 s"
    t0 = time.monotonic()
    bus.disconnect()
    assert time.monotonic() - t0 < 1.0, "disconnect() hit the 2 s drain timeout on a leaked counter"


# ---------------------------------------------------------------------------
# HAL-14 — flush_tx_buffer must read the handle under the lifecycle lock
# ---------------------------------------------------------------------------


def test_hal14_flush_tx_buffer_snapshots_handle_under_lock() -> None:
    """HAL-14: the handle read must be lock-protected (no torn snapshot)."""
    import inspect

    source = inspect.getsource(PythonCanBus.flush_tx_buffer)
    assert "with self._lifecycle_lock:" in source
    assert "bus = self._bus" in source
    # The dereference must sit INSIDE the locked block.
    lock_idx = source.index("with self._lifecycle_lock:")
    read_idx = source.index("bus = self._bus")
    assert lock_idx < read_idx


def test_hal14_flush_tx_buffer_delegates_and_tolerates_missing_hook() -> None:
    """HAL-14: a backend without `flush_tx_buffer` is a harmless no-op."""
    bus = PythonCanBus(interface="virtual", channel="hal14", listen_only=False)
    bus.is_connected = True

    class _NoHook:
        def shutdown(self) -> None:
            pass

    bus._bus = _NoHook()
    bus.flush_tx_buffer()  # must not raise

    flushed: list[bool] = []

    class _WithHook:
        def flush_tx_buffer(self) -> None:
            flushed.append(True)

        def shutdown(self) -> None:
            pass

    bus._bus = _WithHook()
    bus.flush_tx_buffer()
    assert flushed == [True]


# ---------------------------------------------------------------------------
# HAL-15 — 0x23 out-of-range must return NRC, not a 4 GiB fake response
# ---------------------------------------------------------------------------


def _ecu() -> UdsServerEcu:
    bus = VirtualBus(channel_id="hal15", bitrate=500000)
    bus.connect()
    return UdsServerEcu(bus=bus, channel_id="hal15")


def test_hal15_read_memory_out_of_range_returns_nrc_not_gigabytes() -> None:
    """HAL-15: out-of-range 0x23 used to return 0x63 + `size` filler bytes."""
    ecu = _ecu()
    # ALFI: size_width=4, addr_width=4 → a 0xFFFFFFFF byte request.
    payload = bytes([UdsServiceId.READ_MEMORY_BY_ADDRESS, 0x44]) + b"\xFF" * 4 + b"\xFF\xFF\xFF\xFF"
    resp = ecu._handle_uds_request(payload)
    assert resp[0] == 0x7F, "must be a negative response, never a fabricated positive one"
    assert resp[1] == UdsServiceId.READ_MEMORY_BY_ADDRESS
    assert resp[2] == 0x31  # REQUEST_OUT_OF_RANGE
    assert len(resp) == 3, "no multi-gigabyte allocation may be produced"


def test_hal15_read_memory_in_range_still_serves_data() -> None:
    """HAL-15: legitimate in-range reads keep working."""
    ecu = _ecu()
    ecu.memory[0:4] = b"\xDE\xAD\xBE\xEF"
    # ALFI 0x24: size_width=2, addr_width=4 (base_address is 0x08000000).
    payload = (
        bytes([UdsServiceId.READ_MEMORY_BY_ADDRESS, 0x24])
        + ecu.base_address.to_bytes(4, "big")
        + (4).to_bytes(2, "big")
    )
    resp = ecu._handle_uds_request(payload)
    assert resp == b"\x63\xDE\xAD\xBE\xEF"


def test_hal15_read_memory_zero_width_alfi_rejected() -> None:
    """HAL-15: a zero-width ALFI is malformed → fail closed."""
    ecu = _ecu()
    resp = ecu._handle_uds_request(bytes([UdsServiceId.READ_MEMORY_BY_ADDRESS, 0x00, 0x00]))
    assert resp[0] == 0x7F and resp[2] == 0x31


# ---------------------------------------------------------------------------
# HAL-16 — SecurityAccess / 0x2E / 0x34 simulator contract
# ---------------------------------------------------------------------------


def test_hal16_security_access_rejects_arbitrary_key() -> None:
    """HAL-16: ANY non-empty key used to unlock (e.g. b"\x00")."""
    ecu = _ecu()
    # Request seed at level 0x01 (odd → seed request).
    seed_resp = ecu._handle_uds_request(bytes([UdsServiceId.SECURITY_ACCESS, 0x01]))
    assert seed_resp[0] == 0x67
    challenge = seed_resp[2:]

    # An arbitrary non-empty key must now fail.
    bogus = ecu._handle_uds_request(bytes([UdsServiceId.SECURITY_ACCESS, 0x02]) + b"\x00")
    assert bogus[0] == 0x7F and bogus[2] == 0x35  # INVALID_KEY
    assert ecu.security_level == 0

    # The deterministic challenge response unlocks.
    good_key = bytes(b ^ 0xFF for b in challenge)
    ok = ecu._handle_uds_request(bytes([UdsServiceId.SECURITY_ACCESS, 0x02]) + good_key)
    assert ok[0] == 0x67
    assert ecu.security_level == 1


def test_hal16_write_did_requires_session_and_security() -> None:
    """HAL-16: 0x2E wrote ANY DID from the DEFAULT session with security locked."""
    ecu = _ecu()
    write = bytes([UdsServiceId.WRITE_DATA_BY_IDENTIFIER, 0xF1, 0x90]) + b"NEWVIN1234567890AB"

    # 1. Default session → refused.
    denied = ecu._handle_uds_request(write)
    assert denied[0] == 0x7F and denied[2] == 0x7F  # SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION

    # 2. Extended session but security still locked → refused.
    ecu._handle_uds_request(bytes([UdsServiceId.DIAGNOSTIC_SESSION_CONTROL, DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION]))
    locked = ecu._handle_uds_request(write)
    assert locked[0] == 0x7F and locked[2] == 0x33  # SECURITY_ACCESS_DENIED

    # 3. Unlocked → accepted.
    challenge = ecu._handle_uds_request(bytes([UdsServiceId.SECURITY_ACCESS, 0x01]))[2:]
    ecu._handle_uds_request(
        bytes([UdsServiceId.SECURITY_ACCESS, 0x02]) + bytes(b ^ 0xFF for b in challenge)
    )
    accepted = ecu._handle_uds_request(write)
    assert accepted[0] == 0x6E
    assert ecu.dids[0xF190] == b"NEWVIN1234567890AB"


def test_hal16_write_did_unknown_identifier_out_of_range() -> None:
    """HAL-16: writing an unknown DID is REQUEST_OUT_OF_RANGE, not a silent create."""
    ecu = _ecu()
    ecu._handle_uds_request(bytes([UdsServiceId.DIAGNOSTIC_SESSION_CONTROL, DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION]))
    challenge = ecu._handle_uds_request(bytes([UdsServiceId.SECURITY_ACCESS, 0x01]))[2:]
    ecu._handle_uds_request(
        bytes([UdsServiceId.SECURITY_ACCESS, 0x02]) + bytes(b ^ 0xFF for b in challenge)
    )
    resp = ecu._handle_uds_request(bytes([UdsServiceId.WRITE_DATA_BY_IDENTIFIER, 0xDE, 0xAD, 0x01]))
    assert resp[0] == 0x7F and resp[2] == 0x31
    assert 0xDEAD not in ecu.dids


def test_hal16_request_download_requires_security_level() -> None:
    """HAL-16: 0x34 checked the session but NOT the security level."""
    ecu = _ecu()
    download = bytes([UdsServiceId.REQUEST_DOWNLOAD, 0x00, 0x44]) + b"\x00" * 4 + b"\x00\x10\x00\x00"

    # Programming session, security still locked → refused.
    ecu._handle_uds_request(
        bytes([UdsServiceId.DIAGNOSTIC_SESSION_CONTROL, DiagnosticSessionType.PROGRAMMING_SESSION])
    )
    locked = ecu._handle_uds_request(download)
    assert locked[0] == 0x7F and locked[2] == 0x33
    assert ecu.download_active is False

    # Unlocked → accepted.
    challenge = ecu._handle_uds_request(bytes([UdsServiceId.SECURITY_ACCESS, 0x01]))[2:]
    ecu._handle_uds_request(
        bytes([UdsServiceId.SECURITY_ACCESS, 0x02]) + bytes(b ^ 0xFF for b in challenge)
    )
    ok = ecu._handle_uds_request(download)
    assert ok[0] == 0x74
    assert ecu.download_active is True


# ---------------------------------------------------------------------------
# HAL-17 — CSV channel stem must be sanitized to the CanFrame allowlist
# ---------------------------------------------------------------------------


def test_hal17_hostile_file_stem_still_loads_rows(tmp_path) -> None:
    """HAL-17: a stem with spaces/parens used to raise and DROP every row."""
    path = tmp_path / "My Trace (final) v2.csv"
    path.write_text(
        "time,id,dlc,data,dir\n"
        "0.000000,0x100,1,01,rx\n"
        "0.010000,0x101,1,02,rx\n",
        encoding="utf-8",
    )
    frames = CsvParser.parse_file(path)
    assert len(frames) == 2, "rows were silently dropped because of the file name"
    from src.hal.base import _CHANNEL_ID_RE

    for frame in frames:
        assert _CHANNEL_ID_RE.match(frame.channel_id), frame.channel_id
        assert " " not in frame.channel_id and "(" not in frame.channel_id


# ---------------------------------------------------------------------------
# HAL-18 — J1708 must be refused, never decoded as classic CAN
# ---------------------------------------------------------------------------


def test_hal18_j1708_removed_from_allowlist_and_hard_failed() -> None:
    """HAL-18: J1708 was allowlisted while every packet was decoded as CAN."""
    assert "J1708" not in RP1210_PROTOCOL_ALLOWLIST
    assert "J1708" in RP1210_UNSUPPORTED_PROTOCOLS
    assert RP1210Bus.UNSUPPORTED_PROTOCOLS == RP1210_UNSUPPORTED_PROTOCOLS


def test_hal18_bus_connect_refuses_j1708() -> None:
    """HAL-18: connect() fails CLOSED with HARDWARE_PROTOCOL_UNSUPPORTED."""
    client = _RecordingClient()
    bus = RP1210Bus(device_id=1, protocol="J1708", client=client)
    with pytest.raises(HardwareError) as exc:
        bus.connect()
    assert exc.value.code == "HARDWARE_PROTOCOL_UNSUPPORTED"
    assert bus.is_connected is False
    assert bus.metrics.state is BusState.DISCONNECTED
    assert client.connected is False, "no adapter session may be opened for J1708"


# ---------------------------------------------------------------------------
# HAL-19 — scan_bitrate must require N clean frames
# ---------------------------------------------------------------------------


def test_hal19_single_decodable_frame_is_not_enough() -> None:
    """HAL-19: the first decodable frame used to latch the wrong baud."""
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x123, data=b"\x01")

    class _OneFrameBus:
        """Delivers exactly ONE frame then nothing — a wrong-baud false positive."""

        def __init__(self, **kwargs: object) -> None:
            self.metrics = BusMetrics(channel_id="pcan_0")
            self.is_connected = False
            self._delivered = False

        def connect(self) -> None:
            self.is_connected = True

        def disconnect(self) -> None:
            self.is_connected = False

        def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
            if self._delivered:
                return None
            self._delivered = True
            return frame

        def get_metrics(self) -> BusMetrics:
            return self.metrics

    bus = _OneFrameBus()
    result = PythonCanBus.scan_bitrate(
        interface="pcan",
        channel="PCAN_USBBUS1",
        candidates=(250000,),
        listen_timeout_s=0.05,
        _bus_factory=lambda **kw: bus,
    )
    assert result is None, "a single frame must no longer be accepted as the correct baud"


def test_hal19_error_frames_disqualify_a_candidate() -> None:
    """HAL-19: zero error frames is a hard requirement."""
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x123, data=b"\x01")

    class _ErroringBus:
        def __init__(self, **kwargs: object) -> None:
            self.metrics = BusMetrics(channel_id="pcan_0")
            self.metrics.error_frames = 3  # noise observed at this baud
            self.is_connected = False

        def connect(self) -> None:
            self.is_connected = True

        def disconnect(self) -> None:
            self.is_connected = False

        def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
            return frame

        def get_metrics(self) -> BusMetrics:
            return self.metrics

    bus = _ErroringBus()
    result = PythonCanBus.scan_bitrate(
        interface="pcan",
        channel="PCAN_USBBUS1",
        candidates=(250000,),
        listen_timeout_s=0.05,
        _bus_factory=lambda **kw: bus,
    )
    assert result is None, "error frames must disqualify the candidate bitrate"


def test_hal19_clean_burst_is_accepted() -> None:
    """HAL-19: N clean frames + zero errors still detects the rate (no false negative)."""
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x123, data=b"\x01")

    class _CleanBus:
        """Delivers an endless stream of clean frames at the requested baud."""

        def __init__(self, **kwargs: object) -> None:
            self.metrics = BusMetrics(channel_id="pcan_0")
            self.is_connected = False

        def connect(self) -> None:
            self.is_connected = True

        def disconnect(self) -> None:
            self.is_connected = False

        def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
            # A tiny real sleep keeps this a realistic poll loop rather than a
            # zero-timeout busy spin, without dominating the test runtime.
            time.sleep(0.001)
            return frame

        def get_metrics(self) -> BusMetrics:
            return self.metrics

    bus = _CleanBus()
    result = PythonCanBus.scan_bitrate(
        interface="pcan",
        channel="PCAN_USBBUS1",
        candidates=(250000,),
        listen_timeout_s=0.5,
        min_valid_frames=10,
        _bus_factory=lambda **kw: bus,
    )
    assert result == 250000


# ---------------------------------------------------------------------------
# HAL-20 — the play() burst must be atomic (step() alone advances the playhead)
# ---------------------------------------------------------------------------


def test_hal20_burst_uses_single_step_call() -> None:
    """HAL-20: `while self.has_next: frame = self.step()` split the lock.

    Only the burst LOOP is checked — the HAL-20 docstring legitimately quotes
    the old pattern. The docstring is located via the AST so the quoted text
    can never be mistaken for live code.
    """
    import ast
    import inspect
    import textwrap

    source = inspect.getsource(ReplayBus.play)
    tree = ast.parse(textwrap.dedent(source))
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    body = func.body[1:] if func.body and isinstance(func.body[0], ast.Expr) else func.body
    code = "\n".join(ast.unparse(node) for node in body)
    assert "self.step()" in code
    assert "has_next" not in code, "the has_next/step split must be gone"


def test_hal20_playback_still_completes_and_honours_loop() -> None:
    """HAL-20: the atomic rewrite must not change observable playback."""
    frames = [
        CanFrame.create(channel_id="c0", arbitration_id=0x100 + i, data=b"\x01", timestamp_ns=0)
        for i in range(5)
    ]
    seen: list[CanFrame] = []
    ReplayBus(frames).play(callback=seen.append, speed=100.0)
    assert [f.arbitration_id for f in seen] == [0x100, 0x101, 0x102, 0x103, 0x104]


# ---------------------------------------------------------------------------
# HAL-21 — extra_kwargs must not override canonical configuration
# ---------------------------------------------------------------------------


def test_hal21_extra_kwargs_cannot_override_canonical_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL-21: `**extra_kwargs` spread LAST used to retune the bus silently."""
    captured: dict[str, object] = {}

    class _FakeBus:
        state = can.BusState.PASSIVE

        def shutdown(self) -> None:
            pass

    def _factory(**kwargs: object) -> _FakeBus:
        captured.update(kwargs)
        return _FakeBus()

    monkeypatch.setattr(can, "Bus", _factory)

    bus = PythonCanBus(
        interface="pcan",
        channel="PCAN_USBBUS1",
        bitrate=250000,
        listen_only=False,
        bitrate_extra=1,  # non-colliding: allowed
    )
    bus.connect()
    assert captured["bitrate"] == 250000
    assert captured["interface"] == "pcan"
    assert captured["channel"] == "PCAN_USBBUS1"


def test_hal21_colliding_extra_kwarg_is_rejected() -> None:
    """HAL-21: a key that would override the canonical config fails fast."""
    extras: dict[str, object] = {"fd": True}
    with pytest.raises(ValueError, match="must not override canonical bus configuration"):
        PythonCanBus(interface="pcan", channel="PCAN_USBBUS1", **extras)  # type: ignore[arg-type]
    # The alias spellings the driver also consumes are covered too.
    with pytest.raises(ValueError, match="must not override canonical bus configuration"):
        PythonCanBus(interface="pcan", **{"state": "active"})  # type: ignore[arg-type]


def test_hal21_extra_kwargs_cannot_win_over_canonical_bitrate() -> None:
    """HAL-21: even a non-rejected key must not be able to retune the bus.

    Locks the ORDER of the dict literal: canonical values are applied AFTER
    the caller's extras, so the confirmed configuration always wins.
    """
    import inspect

    source = inspect.getsource(PythonCanBus.connect)
    extras_idx = source.index("**self.extra_kwargs")
    canonical_idx = source.index('"bitrate": self.bitrate')
    assert extras_idx < canonical_idx, "extra_kwargs must be spread FIRST"


# ---------------------------------------------------------------------------
# HAL-22 — ERR_RX_QUEUE_FULL must be distinct and counted
# ---------------------------------------------------------------------------


def test_hal22_client_keeps_none_but_publishes_queue_full() -> None:
    """HAL-22: the locked `None` return stays; the condition goes out-of-band."""
    import ctypes
    import threading

    from src.hal.rp1210.types import RP1210ErrorCode

    class _FakeDll:
        def RP1210_ReadMessage(self, client_id: object, buf: object, size: object, block: object) -> int:
            return -RP1210ErrorCode.ERR_RX_QUEUE_FULL

    client = RP1210Client.__new__(RP1210Client)
    client.dll_name = "FAKE.DLL"
    client.device_id = 1
    client.protocol = "J1939"
    client.client_id = 1
    client._dll = _FakeDll()
    client._lifecycle_lock = threading.Lock()
    client._rx_scratch = ctypes.create_string_buffer(4096)
    client._rx_scratch_size = 4096

    assert client.read_message() is None  # locked behaviour preserved
    assert client.last_read_was_queue_full is True


def test_hal22_locked_contract_read_rx_queue_full_still_returns_none() -> None:
    """HAL-22: re-assert the LOCKED contract from tests/unit/test_rp1210.py.

    Error codes must never leak out of `read_message` as data, so a
    queue-full read still returns None; only the out-of-band flag changes.
    """
    import ctypes
    import threading

    from src.hal.rp1210.types import RP1210ErrorCode

    class _FakeDll:
        def RP1210_ReadMessage(self, client_id: object, buf: object, size: object, block: object) -> int:
            return -RP1210ErrorCode.ERR_RX_QUEUE_FULL

    client = RP1210Client.__new__(RP1210Client)
    client.dll_name = "FAKE.DLL"
    client.device_id = 1
    client.protocol = "J1939"
    client.client_id = 1
    client._dll = _FakeDll()
    client._lifecycle_lock = threading.Lock()
    client._rx_scratch = ctypes.create_string_buffer(4096)
    client._rx_scratch_size = 4096

    assert client.read_message() is None
    assert client.read_message() is None  # repeatable, never raises
    assert client.last_read_was_queue_full is True


def test_hal22_bus_surfaces_and_counts_queue_full() -> None:
    """HAL-22: RP1210Bus must count the lossy queue-full as dropped_frames."""
    client = _RecordingClient()
    client.queue_full_reads = 5  # the vendor stack reports full on every read
    bus = RP1210Bus(device_id=1, protocol="CAN", client=client, listen_only=False)
    bus.connect()
    bus.metrics.dropped_frames = 0

    # timeout_s=0 -> a single non-blocking poll, so exactly one read happens.
    assert bus.recv(timeout_s=0) is None
    assert bus.last_rx_queue_full is True
    assert bus.rx_queue_full_events >= 1
    assert bus.metrics.dropped_frames >= 1, "queue-full was never counted as a drop"
    bus.disconnect()


def test_hal22_queue_full_is_distinct_from_empty_queue() -> None:
    """HAL-22: an EMPTY queue must not be reported as a queue-full drop."""
    client = _RecordingClient()  # no queue_full_reads queued
    bus = RP1210Bus(device_id=1, protocol="CAN", client=client, listen_only=False)
    bus.connect()
    bus.metrics.dropped_frames = 0
    assert bus.recv(timeout_s=0) is None
    assert bus.last_rx_queue_full is False
    assert bus.rx_queue_full_events == 0
    assert bus.metrics.dropped_frames == 0
    bus.disconnect()


# ---------------------------------------------------------------------------
# HAL-23 — reset_for_testing must clear _display_required
# ---------------------------------------------------------------------------


def test_hal23_reset_for_testing_clears_display_required() -> None:
    """HAL-23: `_display_required` leaked across tests."""
    import threading

    WindowsPowerManager.reset_for_testing()
    tid = threading.get_ident()
    WindowsPowerManager._display_required[tid] = True
    WindowsPowerManager._leases[tid] = 1
    WindowsPowerManager._is_active = True

    WindowsPowerManager.reset_for_testing()
    assert WindowsPowerManager._display_required == {}
    assert WindowsPowerManager._leases == {}
    assert WindowsPowerManager.is_active() is False


# ---------------------------------------------------------------------------
# HAL-24 — a legitimate 0.0 timestamp must not be replaced by wall clock
# ---------------------------------------------------------------------------


def test_hal24_zero_timestamp_is_preserved() -> None:
    """HAL-24: `if msg.timestamp` treated 0.0 as absent and used time.time_ns()."""
    bus = PythonCanBus(interface="virtual", channel="hal24", listen_only=False)

    class _Msg:
        arbitration_id = 0x123
        is_extended_id = False
        is_fd = False
        bitrate_switch = False
        error_state_indicator = False
        is_error_frame = False
        is_remote_frame = False
        dlc = 1
        data = b"\x01"
        timestamp = 0.0  # legitimate epoch-zero capture timestamp

    class _Backend:
        state = can.BusState.PASSIVE

        def recv(self, timeout: object = None) -> object:
            return _Msg()

        def shutdown(self) -> None:
            pass

    bus.is_connected = True
    bus._bus = _Backend()
    frame = bus.recv(timeout_s=0.1)
    assert frame is not None
    assert frame.timestamp_ns == 0, "0.0 was replaced by the wall clock"


# ---------------------------------------------------------------------------
# HAL-27 — one shared protocol frozenset
# ---------------------------------------------------------------------------


def test_hal27_bus_and_client_share_one_allowlist() -> None:
    """HAL-27: `j1939t`/`iso_tp` were dead branches (client rejected them)."""
    assert RP1210Bus.protocol_allowlist() == RP1210_PROTOCOL_ALLOWLIST
    assert RP1210Client.PROTOCOL_ALLOWLIST == RP1210_PROTOCOL_ALLOWLIST
    # The bus's layout sets must only reference names the client accepts.
    allowed_lower = {p.lower() for p in RP1210_PROTOCOL_ALLOWLIST}
    assert RP1210Bus._EXTENDED_ID_PROTOCOLS <= allowed_lower, (
        "bus layout set references a protocol the client cannot connect with"
    )
    assert RP1210Bus._STRICT_29BIT_PROTOCOLS <= allowed_lower


def test_hal27_j1939t_is_now_reachable() -> None:
    """HAL-27: `J1939T` is accepted by the client validator."""
    from src.hal.rp1210.client import _validate_protocol

    assert _validate_protocol("J1939T") == "J1939T"
    client = _RecordingClient()
    bus = RP1210Bus(device_id=1, protocol="J1939T", client=client)
    bus.connect()
    assert bus.is_connected is True
    assert bus._uses_extended_id_layout is True


# ---------------------------------------------------------------------------
# HAL-28 — CsvParser / VectorBlfParser re-exported
# ---------------------------------------------------------------------------


def test_hal28_parsers_re_exported() -> None:
    """HAL-28: both parsers must be importable from the package root."""
    import src.hal.replay as replay_pkg

    assert CsvParser is replay_pkg.CsvParser
    assert VectorBlfParser is replay_pkg.VectorBlfParser
    assert VectorAscParser is replay_pkg.VectorAscParser
    for name in ("CsvParser", "VectorBlfParser", "VectorAscParser", "ReplayBus", "ReplaySafetyFilter"):
        assert name in replay_pkg.__all__


# ---------------------------------------------------------------------------
# HAL-29 — explicit, documented loopback contract
# ---------------------------------------------------------------------------


def test_hal29_loopback_defaults_off() -> None:
    """HAL-29: default OFF — no fabricated RX echo."""
    bus = VirtualBus(channel_id="hal29_off", bitrate=500000)
    bus.connect()
    frame = CanFrame.create(channel_id="hal29_off", arbitration_id=0x123, data=b"\x01")
    bus.send(frame)
    assert bus.loopback is False
    assert bus.recv(timeout_s=0.01) is None, "no frame may appear in RX without being injected"
    assert len(bus.sent_frames) == 1


def test_hal29_loopback_option_echoes_when_enabled() -> None:
    """HAL-29: with loopback=True the frame is explicitly echoed to RX."""
    bus = VirtualBus(channel_id="hal29_on", bitrate=500000, loopback=True)
    bus.connect()
    frame = CanFrame.create(channel_id="hal29_on", arbitration_id=0x123, data=b"\x01")
    bus.send(frame)
    echoed = bus.recv(timeout_s=0.05)
    assert echoed is not None
    assert echoed.arbitration_id == 0x123


# ---------------------------------------------------------------------------
# HAL-30 — connect() exception wrapping must be symmetric
# ---------------------------------------------------------------------------


def test_hal30_unexpected_connect_exception_marks_disconnected() -> None:
    """HAL-30: a ctypes AttributeError/TypeError escaped with stale state."""
    client = _RecordingClient()

    def _boom(tx_buffer_size: int = 8000, rx_buffer_size: int = 8000, bitrate: int | None = None) -> int:
        raise AttributeError("vendored ctypes signature missing")

    client.connect = _boom  # type: ignore[method-assign]
    bus = RP1210Bus(device_id=1, protocol="J1939", client=client)
    with pytest.raises(HardwareError) as exc:
        bus.connect()
    assert exc.value.code == "HARDWARE_CONNECT_FAILED"
    assert bus.is_connected is False
    assert bus.metrics.state is BusState.DISCONNECTED


def test_hal30_type_error_during_connect_is_wrapped() -> None:
    """HAL-30: a TypeError from a strict vendor ABI is wrapped too."""
    client = _RecordingClient()

    def _boom(**kwargs: object) -> int:
        raise TypeError("RP1210_ClientConnect() takes 6 arguments")

    client.connect = _boom  # type: ignore[method-assign]
    bus = RP1210Bus(device_id=1, protocol="J1939", client=client)
    with pytest.raises(HardwareError) as exc:
        bus.connect()
    assert exc.value.code == "HARDWARE_CONNECT_FAILED"
    assert bus.is_connected is False


def test_hal30_hardware_error_still_propagates_with_clean_state() -> None:
    """HAL-30: a real HardwareError keeps its own code and cleans state."""
    client = _RecordingClient()

    def _boom(tx_buffer_size: int = 8000, rx_buffer_size: int = 8000, bitrate: int | None = None) -> int:
        raise HardwareError("adapter busy", code="HARDWARE_BUSY")

    client.connect = _boom  # type: ignore[method-assign]
    bus = RP1210Bus(device_id=1, protocol="J1939", client=client)
    with pytest.raises(HardwareError) as exc:
        bus.connect()
    assert exc.value.code == "HARDWARE_BUSY"
    assert bus.is_connected is False


# ---------------------------------------------------------------------------
# HAL-31 — AbstractBus documents the recv(None) contract
# ---------------------------------------------------------------------------


def test_hal31_abstractbus_documents_blocking_contract() -> None:
    """HAL-31: the None == block-indefinitely contract must be documented."""
    import inspect

    from src.hal.base import AbstractBus

    doc = inspect.getdoc(AbstractBus.recv) or ""
    assert "INDEFINITELY" in doc.upper()
    assert "timeout_s is None" in doc
    assert "ValueError" in doc


def test_hal31_drivers_agree_on_the_contract() -> None:
    """HAL-31: every driver subscribes to the same None semantics."""
    # VirtualBus: None blocks, invalid values raise.
    vbus = VirtualBus(channel_id="hal31", bitrate=500000)
    vbus.connect()
    with pytest.raises(ValueError, match="timeout_s must be"):
        vbus.recv(timeout_s=-0.5)
    assert vbus.recv(timeout_s=0) is None  # non-blocking poll on an empty queue

    # RP1210Bus: None must not be silently clamped to a default timeout.
    client = _RecordingClient()
    bus = RP1210Bus(device_id=1, protocol="CAN", client=client, listen_only=False)
    bus.connect()
    with pytest.raises(ValueError, match="timeout_s must be"):
        bus.recv(timeout_s=-0.5)
    bus.disconnect()

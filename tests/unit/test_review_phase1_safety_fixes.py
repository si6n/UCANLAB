"""Regression tests for REVIEW Aşama 1 (safety / TX choke-point) findings.

Each test was written AFTER reproducing the reported defect in this repository
and FAILS on the unpatched code. They exist because the suite was green
(2231 passed) while the following bypasses were live:

  * S1-P1-1 — 29-bit ISO-TP UDS frames (``0x18DAxxxx``) had NO criticality
    derivation, so ECUReset / RequestDownload / LinkControl skipped the
    speed interlock (Stage 4) and the HMAC dual-confirmation (Stage 5).
  * S1-P1-2 — a J1939 Request (PGN 59904) for a writable PGN (DM11 clear)
    was classified non-critical, so "erase active DTCs" was reachable
    without the physical-speed interlock.
  * S1-P1-3 — the gateway's critical-SID set was NARROWER than the replay
    filter's prohibited set (0x38/0x3D/0x87), i.e. live TX was less guarded
    than replay — the unsafe direction.
  * S1-P1-4 — ``set_listen_only`` returned True (phantom success) when the
    backend could not change state, so ``disarm_tx`` reported success while
    the hardware stayed ACTIVE and kept ACK-ing on the vehicle bus.
  * S1-P0-1 — ``RP1210Bus`` had no ``set_listen_only``, so ``arm_tx`` could
    never enable TX on RP1210 adapters (the advertised heavy-duty TX path).
  * S1-P2-5 — ``get_default_secret_provider()`` was not a singleton, so the
    E-Stop and the out-of-band reset tool could hold DIVERGENT keys.
  * S1-P1-6 — ``skipTargetIdentity`` was honored straight from the JS bridge
    payload, dropping the VIN/serial binding on a signed image.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from src.core.models.can_frame import CanFrame
from src.hal.base import BusMetrics, BusState
from src.hal.drivers.pcan_kvaser import PythonCanBus
from src.hal.replay.safety_filter import ReplaySafetyFilter
from src.hal.rp1210.bus import RP1210Bus
from src.hal.virtual import VirtualBus
from src.safety.criticality import CRITICAL_UDS_SIDS, PROHIBITED_UDS_SIDS
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor


def _armed_gateway(confirmation_secret: bytes | None = b"\x01" * 32) -> TxSafetyGateway:
    """Build a gateway in ARMED_TX with the production-shaped whitelist."""
    from src.engine.pipeline.reassembly_pipeline import j1939_protocol_response_masks

    sup = SafetySupervisor()
    for state in (SafetyState.SAFE, SafetyState.PASSIVE, SafetyState.ARMED_TX):
        sup.transition_to(state, "test")
    return TxSafetyGateway(
        bus=VirtualBus(),
        supervisor=sup,
        whitelist_ids={0x7DF, *range(0x7E0, 0x7E8)},
        whitelist_masks=list(j1939_protocol_response_masks(0xF9)),
        confirmation_secret=confirmation_secret,
    )


def _is_whitelisted(gateway: TxSafetyGateway, arb_id: int) -> bool:
    return arb_id in gateway.whitelist_ids or any(
        mask != 0 and (arb_id & mask) == value for value, mask in gateway.whitelist_masks
    )


def _frame(arb_id: int, data: bytes, *, extended: bool) -> CanFrame:
    return CanFrame(
        channel_id="can0",
        arbitration_id=arb_id,
        dlc=len(data),
        data=data,
        is_extended=extended,
    )


# ---------------------------------------------------------------------------
# S1-P1-1 — 29-bit ISO-TP UDS criticality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sid", "label"),
    [
        (0x11, "ECUReset"),
        (0x31, "RoutineControl"),
        (0x34, "RequestDownload"),
        (0x2E, "WriteDataByIdentifier"),
        (0x87, "LinkControl"),
        (0x3D, "WriteMemoryByAddress"),
    ],
)
def test_29bit_isotp_uds_is_critical_without_caller_flag(sid: int, label: str) -> None:
    """S1-P1-1: `0x18DAxxxx` (ISO 15765-4) must derive UDS criticality.

    Before the fix the extended branch only compared the J1939 PGN, so every
    one of these returned False and skipped Stage 4/5. `0x18DA00F9` is the
    physical request address (SA=F9, DA=00) and IS whitelisted by the
    production `j1939_protocol_response_masks(0xF9)` masks.
    """
    from src.engine.pipeline.reassembly_pipeline import j1939_protocol_response_masks

    gateway = _armed_gateway()
    mask_ids = list(j1939_protocol_response_masks(0xF9))
    assert len(mask_ids) > 0  # sanity: the production masks loaded
    arbitration_id = 0x18DA0000 | 0xF9  # PF=0xDA, SA=0xF9, DA=0x00
    assert _is_whitelisted(gateway, arbitration_id), f"{label}: test address must be reachable"

    frame = _frame(arbitration_id, bytes([0x02, sid, 0x01]), extended=True)
    assert gateway._frame_is_critical(frame) is True, f"{label} (0x{sid:02X}) must be critical on 29-bit ISO-TP"


def test_29bit_isotp_canfd_escape_sf_sid_is_critical() -> None:
    """S1-P1-1: CAN-FD escape SingleFrame puts the SID after the 32-bit length.

    Layout (ISO 15765-2:2016 §9.2): ``[0x00][0x00][DL32][SID][...]`` — the SID
    is therefore at offset 6, not 1. The pre-fix code read only offset 1/2, so
    an escape-format ECUReset was invisible to the criticality gate.
    """
    gateway = _armed_gateway()
    arbitration_id = 0x18DA0000 | 0xF9
    data = bytes([0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x11, 0x01, 0x00, 0x00])
    frame = CanFrame(
        channel_id="can0",
        arbitration_id=arbitration_id,
        dlc=len(data),
        data=data,
        is_extended=True,
        is_fd=True,
    )
    assert gateway._frame_is_critical(frame) is True


def test_29bit_isotp_canfd_escape_ff_sid_is_critical() -> None:
    """S1-P1-1: CAN-FD escape FirstFrame — ``[0x10][0x00][DL32][SID]``."""
    gateway = _armed_gateway()
    arbitration_id = 0x18DA0000 | 0xF9
    data = bytes([0x10, 0x00, 0x40, 0x00, 0x00, 0x00, 0x34, 0x00, 0x00, 0x00])
    frame = CanFrame(
        channel_id="can0",
        arbitration_id=arbitration_id,
        dlc=len(data),
        data=data,
        is_extended=True,
        is_fd=True,
    )
    assert gateway._frame_is_critical(frame) is True


def test_29bit_readonly_sid_is_not_critical() -> None:
    """No false positive: a read-only 29-bit UDS service stays non-critical."""
    gateway = _armed_gateway()
    arbitration_id = 0x18DA0000 | 0xF9
    # 0x22 ReadDataByIdentifier is a read -> must NOT force the interlock.
    frame = _frame(arbitration_id, bytes([0x03, 0x22, 0xF1, 0x90]), extended=True)
    assert gateway._frame_is_critical(frame) is False


def test_29bit_consecutive_frame_is_not_escalated_by_itself() -> None:
    """ConsecutiveFrame (0x2) carries no SID, so it must not self-escalate."""
    gateway = _armed_gateway()
    arbitration_id = 0x18DA0000 | 0xF9
    frame = _frame(arbitration_id, bytes([0x21, 0x11, 0x01, 0x02]), extended=True)
    assert gateway._frame_is_critical(frame) is False


def test_29bit_isotp_is_rejected_without_dual_confirmation_token() -> None:
    """S1-P1-1 end-to-end: the escalation must actually gate transmission.

    With `confirmation_secret` configured, a critical frame lacking a token
    cannot be authorized by the bare `user_confirmed=True` boolean.
    """
    from src.safety.exceptions import DualConfirmationRequiredError

    gateway = _armed_gateway()
    gateway.update_physical_speed(0.0)  # vehicle stationary, feed fresh
    arbitration_id = 0x18DA0000 | 0xF9
    frame = _frame(arbitration_id, bytes([0x02, 0x11, 0x01]), extended=True)

    with pytest.raises(DualConfirmationRequiredError):
        gateway.validate_and_transmit(frame, is_critical_command=False, user_confirmed=True)


# ---------------------------------------------------------------------------
# S1-P1-2 — J1939 Request payload criticality
# ---------------------------------------------------------------------------


def _pgn_le(pgn: int) -> bytes:
    """J1939-21 little-endian PGN encoding for a Request payload."""
    return bytes([pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF])


@pytest.mark.parametrize(
    ("pgn", "label"),
    [
        (65235, "DM11 Clear Active DTCs"),
        (65228, "DM3 Clear Previously Active DTCs"),
        (65229, "DM4 Freeze Frame Clear"),
        (65230, "DM5 Readiness Clear"),
        (65240, "Commanded Address"),
        (0, "TSC1"),
        (1024, "XBR"),
    ],
)
def test_j1939_request_for_writable_pgn_is_critical(pgn: int, label: str) -> None:
    """S1-P1-2: requesting a writable/actuation PGN is a remote command."""
    gateway = _armed_gateway()
    arbitration_id = 0x18EA0000 | 0xF9  # PGN 59904 (Request), SA=0xF9
    assert _is_whitelisted(gateway, arbitration_id), "Request address must be reachable"

    frame = _frame(arbitration_id, _pgn_le(pgn), extended=True)
    assert gateway._frame_is_critical(frame) is True, f"Request for {label} must be critical"


@pytest.mark.parametrize("pgn", [65226, 65227, 65265])
def test_j1939_request_for_readonly_pgn_is_not_critical(pgn: int) -> None:
    """No false positive: requesting a read-only PGN stays non-critical."""
    gateway = _armed_gateway()
    frame = _frame(0x18EA0000 | 0xF9, _pgn_le(pgn), extended=True)
    assert gateway._frame_is_critical(frame) is False


def test_j1939_request_short_payload_fails_closed() -> None:
    """An undecodable Request payload must be treated as critical."""
    gateway = _armed_gateway()
    frame = _frame(0x18EA0000 | 0xF9, bytes([0x01, 0x02]), extended=True)
    assert gateway._frame_is_critical(frame) is True


def test_j1939_direct_write_pgns_are_critical() -> None:
    """Direct DM11/DM3/TSC1/XBR transmissions stay critical."""
    gateway = _armed_gateway()
    for pgn in (65235, 65228, 65229, 65230, 65240, 0, 1024):
        arbitration_id = 0x18000000 | (pgn << 8) | 0xF9
        assert gateway._frame_is_critical(
            _frame(arbitration_id, bytes([0x01, 0x02, 0x03]), extended=True)
        ), f"PGN {pgn} must be critical"


# ---------------------------------------------------------------------------
# S1-P1-3 — single source of truth for the critical SID catalogue
# ---------------------------------------------------------------------------


def test_gateway_and_replay_sid_policies_do_not_diverge() -> None:
    """S1-P1-3: live-TX criticality must cover the replay-prohibited SIDs.

    The only legitimate difference is 0x3E (TesterPresent), which is
    dangerous to replay but is a benign session keep-alive that must not be
    pinned to the physical-speed interlock.
    """
    gateway_sids = set(TxSafetyGateway.CRITICAL_UDS_SIDS)
    replay_sids = set(ReplaySafetyFilter.PROHIBITED_UDS_SIDS)

    assert replay_sids - gateway_sids == {0x3E}, (
        "replay must not be stricter than live TX apart from TesterPresent; "
        f"extra={sorted(hex(s) for s in replay_sids - gateway_sids)}"
    )
    assert gateway_sids - replay_sids == set(), (
        f"gateway-critical SIDs missing from replay policy: {sorted(hex(s) for s in gateway_sids - replay_sids)}"
    )


@pytest.mark.parametrize("sid", [0x38, 0x3D, 0x87])
def test_previously_missing_sids_are_critical_11bit(sid: int) -> None:
    """S1-P1-3: 0x38/0x3D/0x87 were absent from the gateway's critical set."""
    gateway = _armed_gateway()
    assert sid in gateway.CRITICAL_UDS_SIDS
    frame = _frame(0x7E0, bytes([0x02, sid, 0x01]), extended=False)
    assert gateway._frame_is_critical(frame) is True


def test_shared_catalogue_is_the_source_of_both_policies() -> None:
    """Both policies import the shared catalogue, so drift is structural."""
    assert set(TxSafetyGateway.CRITICAL_UDS_SIDS) == set(CRITICAL_UDS_SIDS)
    assert set(ReplaySafetyFilter.PROHIBITED_UDS_SIDS) == set(PROHIBITED_UDS_SIDS)
    assert 0x3E in PROHIBITED_UDS_SIDS and 0x3E not in CRITICAL_UDS_SIDS


# ---------------------------------------------------------------------------
# S1-P1-4 — set_listen_only must fail closed
# ---------------------------------------------------------------------------


class _UnmutableBackend:
    """Backend whose transceiver state cannot be read or written (slcan-like)."""

    @property
    def state(self) -> object:
        raise NotImplementedError("backend has no mutable state")

    @state.setter
    def state(self, value: object) -> None:
        raise NotImplementedError("backend has no mutable state")


def test_set_listen_only_unsupported_backend_returns_false() -> None:
    """S1-P1-4: an unverifiable mode change must NOT report success.

    The old code swallowed NotImplementedError, then still assigned
    `self.listen_only` and returned True — so `disarm_tx` looked successful
    while the hardware stayed ACTIVE and kept producing ACKs on the bus.
    """
    bus = PythonCanBus.__new__(PythonCanBus)
    bus._lifecycle_lock = threading.RLock()
    bus.is_connected = True
    bus.listen_only = True
    bus._bus = _UnmutableBackend()
    bus.metrics = MagicMock()

    assert PythonCanBus.set_listen_only(bus, False) is False
    # The software flag must NOT be flipped to a mode the hardware never entered.
    assert bus.listen_only is True


class _ConfirmingBackend:
    """Backend whose state assignment is observable (normal PCAN/Kvaser path)."""

    def __init__(self) -> None:
        self._state = None

    @property
    def state(self) -> object:
        return self._state

    @state.setter
    def state(self, value: object) -> None:
        self._state = value


def test_set_listen_only_confirming_backend_returns_true() -> None:
    """No false negative: a verifiable backend still reports success."""
    import can

    bus = PythonCanBus.__new__(PythonCanBus)
    bus._lifecycle_lock = threading.RLock()
    bus.is_connected = True
    bus.listen_only = True
    bus._bus = _ConfirmingBackend()
    bus.metrics = MagicMock()

    assert PythonCanBus.set_listen_only(bus, False) is True
    assert bus.listen_only is False
    assert bus._bus.state is can.BusState.ACTIVE


def test_set_listen_only_disconnected_returns_false() -> None:
    """A disconnected backend cannot confirm a mode change."""
    bus = PythonCanBus.__new__(PythonCanBus)
    bus._lifecycle_lock = threading.RLock()
    bus.is_connected = False
    bus.listen_only = True
    bus._bus = None
    bus.metrics = MagicMock()

    assert PythonCanBus.set_listen_only(bus, False) is False


# ---------------------------------------------------------------------------
# S1-P0-1 — RP1210 must be armable
# ---------------------------------------------------------------------------


def _fresh_rp1210() -> RP1210Bus:
    bus = RP1210Bus.__new__(RP1210Bus)
    bus.device_id = 1
    bus.protocol = "J1939"
    bus.bitrate = 250000
    bus.listen_only = True
    bus.is_connected = False
    bus._lifecycle_lock = threading.Lock()
    bus._active_sends = 0
    bus._active_recvs = 0
    bus._drain_cond = threading.Condition(bus._lifecycle_lock)
    bus.metrics = BusMetrics(channel_id="rp1210_dev1", bitrate=250000)
    return bus


def test_rp1210_exposes_set_listen_only() -> None:
    """S1-P0-1: without this method `arm_tx` could never leave listen-only."""
    assert hasattr(RP1210Bus, "set_listen_only")
    assert callable(RP1210Bus.set_listen_only)


def test_rp1210_arm_tx_enables_send() -> None:
    """S1-P0-1: arming must flip the software TX gate to ACTIVE."""
    bus = _fresh_rp1210()
    bus.is_connected = True
    assert RP1210Bus.set_listen_only(bus, False) is True
    assert bus.listen_only is False
    assert bus.metrics.state is BusState.ACTIVE
    # Disarm must restore the TX block.
    assert RP1210Bus.set_listen_only(bus, True) is True
    assert bus.listen_only is True
    assert bus.metrics.state is BusState.PASSIVE


def test_rp1210_set_listen_only_when_disconnected_fails_closed() -> None:
    """Nothing to switch while disconnected -> report failure, not success."""
    bus = _fresh_rp1210()
    assert RP1210Bus.set_listen_only(bus, False) is False
    assert bus.listen_only is True


def test_rp1210_declares_it_is_not_a_passive_sniffer() -> None:
    """S1-P1-5: the adapter ACKs, so it must not claim hardware listen-only."""
    bus = _fresh_rp1210()
    assert RP1210Bus.hardware_listen_only.__get__(bus) is False


def test_rp1210_send_is_blocked_while_listen_only() -> None:
    """The passive guarantee (send() refusal) must survive the new method."""
    from src.core.errors import HardwareError

    bus = _fresh_rp1210()
    bus.is_connected = True
    bus._client = MagicMock()
    frame = _frame(0x18EA00F9, bytes([0x00, 0xEF, 0xFE]), extended=True)
    with pytest.raises(HardwareError):
        RP1210Bus.send(bus, frame)


# ---------------------------------------------------------------------------
# S1-P1-6 — skipTargetIdentity must not be bridge-controlled
# ---------------------------------------------------------------------------


def test_flash_start_rejects_skip_target_identity_from_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """S1-P1-6: a renderer-supplied waiver must be ignored in production."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    monkeypatch.delenv("UCANLAB_FLASH_SKIP_IDENTITY", raising=False)
    monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)

    config = {"firmwareSignature": "aa" * 64, "trustedPubkey": "bb" * 32}
    error = UniversalCanDesktopApp._validate_flash_prerequisites({**config, "skipTargetIdentity": True})
    assert error is not None, "skipTargetIdentity from the bridge must not waive target identity"


def test_flash_start_requires_identity_without_waiver(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ui.desktop_app import UniversalCanDesktopApp

    monkeypatch.delenv("UCANLAB_FLASH_SKIP_IDENTITY", raising=False)
    monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)

    config = {"firmwareSignature": "aa" * 64, "trustedPubkey": "bb" * 32}
    assert UniversalCanDesktopApp._validate_flash_prerequisites(config) is not None
    assert (
        UniversalCanDesktopApp._validate_flash_prerequisites(
            {**config, "expectedVin": "WVWZZZ1JZ3W386752"}
        )
        is None
    )


def test_flash_identity_waiver_requires_both_lab_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lab escape hatch needs BOTH env vars (not renderer input)."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    config = {"firmwareSignature": "aa" * 64, "trustedPubkey": "bb" * 32, "skipTargetIdentity": True}

    monkeypatch.setenv("UCANLAB_FLASH_SKIP_IDENTITY", "1")
    monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
    assert UniversalCanDesktopApp._validate_flash_prerequisites(config) is not None

    monkeypatch.setenv("UCANLAB_TEST_MODE", "1")
    assert UniversalCanDesktopApp._validate_flash_prerequisites(config) is None


def test_flash_config_require_target_identity_ignores_bridge_waiver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The motor gate itself must not be relaxed by the bridge payload."""
    from src.ui.desktop_app import UniversalCanDesktopApp

    monkeypatch.delenv("UCANLAB_FLASH_SKIP_IDENTITY", raising=False)
    monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
    assert UniversalCanDesktopApp._flash_identity_waiver_allowed() is False


# ---------------------------------------------------------------------------
# S1-P2-5 — one SecretProvider per process
# ---------------------------------------------------------------------------


def test_estop_and_gateway_share_secret_provider() -> None:
    """S1-P2-5: EmergencyStopSystem must reuse the caller's provider.

    When it self-instantiates, the E-Stop's HMAC key and the key the external
    reset tool reads can diverge (deterministic with EphemeralSecretBackend),
    producing an unexplained "invalid reset token".
    """
    from src.safety.estop import EmergencyStopSystem
    from src.safety.secret_provider import get_default_secret_provider

    provider = get_default_secret_provider()
    estop = EmergencyStopSystem(secret_provider=provider)
    assert estop._secret_provider is provider


def test_default_secret_provider_is_memoized() -> None:
    """S1-P2-5: identical factory calls must return the SAME instance."""
    from src.safety.secret_provider import get_default_secret_provider

    assert get_default_secret_provider() is get_default_secret_provider()


def test_default_secret_provider_shares_estop_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The divergence that broke reset-token validation must be gone."""
    from src.safety.estop import DEFAULT_ESTOP_KEY_NAME, EmergencyStopSystem
    from src.safety.secret_provider import get_default_secret_provider

    monkeypatch.setenv("UNIVERSAL_CAN_EPHEMERAL_SECRETS", "1")
    # force a fresh cache slot for the ephemeral variant
    from src.safety import secret_provider as sp

    sp._DEFAULT_PROVIDER_CACHE.pop((None, True), None)

    provider = get_default_secret_provider()
    estop = EmergencyStopSystem(secret_provider=provider)
    assert provider.has_secret(DEFAULT_ESTOP_KEY_NAME) is True
    assert estop._secret_provider is provider


def test_distinct_storage_dirs_still_yield_distinct_providers(tmp_path) -> None:
    """No over-sharing: per-call parameters must still isolate providers."""
    from src.safety.secret_provider import get_default_secret_provider

    a = get_default_secret_provider(storage_dir=tmp_path / "a")
    b = get_default_secret_provider(storage_dir=tmp_path / "b")
    assert a is not b
    assert get_default_secret_provider(storage_dir=tmp_path / "a") is a


# ---------------------------------------------------------------------------
# Desktop composition-root wiring (identity, not just behaviour)
# ---------------------------------------------------------------------------


def test_desktop_composition_root_shares_provider_with_estop() -> None:
    """The production composition root must not mint a second provider."""
    import inspect

    from src.ui.desktop_app import UniversalCanDesktopApp

    source = inspect.getsource(UniversalCanDesktopApp.__init__)
    assert "EmergencyStopSystem(secret_provider=self._secret_provider)" in source, (
        "composition root must inject the shared provider into EmergencyStopSystem"
    )


def test_estop_reset_tool_passes_provider() -> None:
    """The out-of-band tool must bind the same provider it mints with."""
    from pathlib import Path

    tool = Path(__file__).resolve().parents[2] / "scripts" / "estop_reset_tool.py"
    text = tool.read_text(encoding="utf-8")
    assert "EmergencyStopSystem(secret_provider=provider)" in text, (
        "reset tool must inject the provider it signed the challenge with"
    )

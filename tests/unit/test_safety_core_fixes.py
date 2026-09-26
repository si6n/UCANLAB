"""Safety-core regression tests: B-03, B-05, I-01, I-02, I-03 (+B-04 verify)."""

from __future__ import annotations

import os

import pytest

from src.core.contracts.ports import InMemoryTxPort
from src.core.errors import SafetyError
from src.core.models.can_frame import CanFrame
from src.protocols.uds.client import UdsClient
from src.protocols.uds.flasher import EcuFlashingEngine, FlashingConfig
from src.safety.estop import (
    DEFAULT_ESTOP_KEY_NAME,
    EmergencyStopSystem,
)
from src.safety.exceptions import FrameSanityError
from src.safety.gateway import TxSafetyGateway
from src.safety.secret_provider import EphemeralSecretBackend, ProtectionLevel
from src.safety.state_machine import SafetyState, SafetySupervisor


def _frame(arb: int = 0x7E0, data: bytes = b"\x10\x03") -> CanFrame:
    return CanFrame.create(channel_id="t", arbitration_id=arb, data=data, is_extended=False)


def _bus():
    from tests.unit.test_uds_client import MockDiagnosticBus

    return MockDiagnosticBus()


def _gw(**kw) -> TxSafetyGateway:
    os.environ["UCANLAB_TEST_MODE"] = "1"
    estop = EmergencyStopSystem(reset_secret=os.urandom(32))
    sup = SafetySupervisor(initial_state=SafetyState.PASSIVE, estop=estop)
    sup.arm_tx("test arm")  # legacy unauthenticated path (no auth_secret)
    gw = TxSafetyGateway.for_testing(bus=_bus(), estop=estop, **kw)
    gw.supervisor = sup
    return gw


# B-03: provider init failure + EPHEMERAL env -> arm rejected.
def test_b03_failing_store_marks_downgraded_and_arm_refused() -> None:
    class _FailingStore(EphemeralSecretBackend):
        def store_secret(self, name: str, secret: bytes) -> None:
            raise OSError("disk full")

        def get_secret(self, name: str) -> bytes:
            raise KeyError(name)

    estop = EmergencyStopSystem(secret_provider=_FailingStore())  # type: ignore[arg-type]
    assert estop.protection_downgraded is True
    with pytest.raises(SafetyError) as exc:
        estop.assert_arm_permitted("arm_tx")
    assert exc.value.code == "ESTOP_PROTECTION_DOWNGRADED"


def test_b03_ephemeral_env_counts_as_downgraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIVERSAL_CAN_EPHEMERAL_SECRETS", "1")
    from src.safety import secret_provider as sp

    sp._DEFAULT_PROVIDER_CACHE.pop((None, True), None)
    try:
        provider = sp.get_default_secret_provider()
        assert provider.protection_level() == ProtectionLevel.EPHEMERAL  # typed port
        estop = EmergencyStopSystem(secret_provider=provider)
        assert estop.protection_downgraded is True
        with pytest.raises(SafetyError) as exc:
            estop.assert_arm_permitted("arm_tx")
        assert exc.value.code == "ESTOP_PROTECTION_DOWNGRADED"
        # Re-evaluate on provider replacement: durable recovery re-arms.
        good = EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: os.urandom(32)})
        good.protection_downgraded = False
        object.__setattr__(good, "protection_level", lambda: ProtectionLevel.FILE_AES_GCM_0600)
        estop.bind_secret_provider(good)
        assert estop.protection_downgraded is False
        estop.assert_arm_permitted("arm_tx")  # no raise
    finally:
        sp._DEFAULT_PROVIDER_CACHE.pop((None, True), None)


def test_b03_explicit_reset_secret_stays_armed() -> None:
    estop = EmergencyStopSystem(reset_secret=os.urandom(32))
    assert estop.protection_downgraded is False
    estop.assert_arm_permitted("arm_tx")


def test_b03_refresh_re_evaluates_downgrade() -> None:
    provider = EphemeralSecretBackend({DEFAULT_ESTOP_KEY_NAME: os.urandom(32)})
    estop = EmergencyStopSystem(secret_provider=provider)
    assert estop.protection_downgraded is True  # bare ephemeral counts
    object.__setattr__(provider, "protection_level", lambda: ProtectionLevel.FILE_AES_GCM_0600)
    provider.protection_downgraded = False
    estop.refresh_secret()
    assert estop.protection_downgraded is False


# B-05 CCVS default-closed: old tests assert learning; new behavior: no trust.
def _ccvs(sa: int, raw: int) -> CanFrame:
    return CanFrame.create(
        channel_id="v", arbitration_id=0x18FEF100 | sa,
        data=bytes([0, raw & 0xFF, (raw >> 8) & 0xFF, 0, 0, 0, 0, 0]), is_extended=True,
    )


def test_b05_spoofed_first_frame_never_binds_trust() -> None:
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._decode_j1939_signal(_ccvs(0x2A, 0))  # attacker first
    assert app._ccvs_trusted_sa is None
    assert app.gateway._last_speed_update_ns == 0  # interlock stays stale
    state, _ = app.gateway.speed_interlock_state()
    assert state == "stale"
    assert app.arm_tx(reason="t")["success"] is False


def test_b05_approved_sa_feeds_interlock_and_reconnect_drops_it() -> None:
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app.approve_ccvs_source(0x00, reason="test")
    app._decode_j1939_signal(_ccvs(0x00, 0))
    assert app.gateway._last_speed_update_ns > 0
    app._reconnect_bus()
    assert app._ccvs_trusted_sa is None
    app.gateway._last_speed_update_ns = 0
    app._decode_j1939_signal(_ccvs(0x00, 0))
    assert app.gateway._last_speed_update_ns == 0


# I-01: cross-lane total_stamp leak (benign non-critical frames; no speed feed needed).
def test_i01_sim_lane_reject_leaves_no_total_stamp() -> None:
    gw = _gw()
    before = len(gw._tx_total_timestamps)
    live = _frame(0x123, b"\x00\x00")
    with pytest.raises(FrameSanityError):
        gw.validate_and_transmit(live, budget_category="simulation")
    assert len(gw._tx_total_timestamps) == before


def test_i01_cross_lane_budget_not_starved_by_rejects() -> None:
    gw = _gw()
    live = _frame(0x123, b"\x00\x00")
    for _ in range(10):
        with pytest.raises(FrameSanityError):
            gw.validate_and_transmit(live, budget_category="simulation")
    assert gw.validate_and_transmit(_frame(0x123, b"\x00\x00")) is True


# I-02: send_sync fallback forwards proof or raises typed SafetyError.
def test_i02_critical_proof_never_silently_dropped() -> None:
    from src.core.contracts.ports import QueueRxSubscription

    class _Narrow:
        def __init__(self) -> None:
            self.calls: list[CanFrame] = []

        def send_sync(self, frame: CanFrame) -> None:
            self.calls.append(frame)

    port = _Narrow()
    c = UdsClient(tx_port=InMemoryTxPort(unsafe_test_double=True),
                  rx_sub=QueueRxSubscription(), tx_id=0x7E0, rx_id=0x7E8)
    c.tx_port = port  # type: ignore[assignment]
    with pytest.raises(SafetyError) as exc:
        c._tx_frame(_frame(), True, True, confirmation_token=b"tok")
    assert exc.value.code == "UDS_TX_PROOF_CONTRACT_VIOLATION"
    assert port.calls == []
    # Non-critical still falls back.
    c._tx_frame(_frame(), False, False)
    assert len(port.calls) == 1


# I-03: payload-swap rejection on bound flash token.
def test_i03_bound_token_rejects_payload_swap() -> None:
    os.environ["UCANLAB_TEST_MODE"] = "1"
    secret = os.urandom(32)
    estop = EmergencyStopSystem(reset_secret=os.urandom(32))
    sup = SafetySupervisor(initial_state=SafetyState.PASSIVE, estop=estop)
    sup.arm_tx("test arm")
    gw = TxSafetyGateway(bus=_bus(), estop=estop, supervisor=sup,
                         whitelist_ids={0x7E0}, confirmation_secret=secret)
    gw.update_vehicle_speed(0.0, source="physical")
    frame = _frame(0x7E0, b"\x10\x03")
    token = gw.issue_confirmation_token(
        0x7E0, payload_hash=gw.confirmation_payload_hash(bytes(frame.data)), context="flash:VIN1:0x0",
    )
    assert gw.validate_and_transmit(
        frame, is_critical_command=True, user_confirmed=True,
        confirmation_token=token, confirmation_context="flash:VIN1:0x0",
    ) is True
    swapped = _frame(0x7E0, b"\x10\x02")
    token2 = gw.issue_confirmation_token(
        0x7E0, payload_hash=gw.confirmation_payload_hash(bytes(frame.data)), context="flash:VIN1:0x0",
    )
    with pytest.raises(SafetyError):
        gw.validate_and_transmit(
            swapped, is_critical_command=True, user_confirmed=True,
            confirmation_token=token2, confirmation_context="flash:VIN1:0x0",
        )
    # Legacy unbound token never authorizes a context-bound (flash) step.
    legacy = gw.issue_confirmation_token(0x7E0)
    with pytest.raises(SafetyError):
        gw.validate_and_transmit(
            frame, is_critical_command=True, user_confirmed=True,
            confirmation_token=legacy, confirmation_context="flash:VIN1:0x0",
        )


def test_i03_flasher_mint_binds_target_identity() -> None:
    port = InMemoryTxPort(unsafe_test_double=True)
    from src.core.contracts.ports import QueueRxSubscription

    client = UdsClient(tx_port=port, rx_sub=QueueRxSubscription(), tx_id=0x7E0, rx_id=0x7E8)
    seen: dict = {}

    def factory(arb: int, **kw):
        seen.update(kw)
        return b"tok"

    eng = EcuFlashingEngine(uds_client=client, gateway=_gw(), confirmation_token_factory=factory)
    eng._active_config = FlashingConfig(data=b"\x00" * 16, expected_vin="VIN123",
                                        require_signature=False, require_target_identity=False)
    eng._confirmation_token()
    assert "context" in seen and "VIN123" in str(seen["context"])


# B-04 REFUTED: boot is listen-only; arm/disarm enforce driver mode.
def test_b04_boot_listen_only_and_arm_disarm_enforced() -> None:
    import inspect

    from src.ui import desktop_app as da

    src = inspect.getsource(da.UniversalCanDesktopApp.__init__)
    assert "listen_only=True" in src or "listen-only" in src.lower()
    assert "_set_driver_listen_only(False)" in inspect.getsource(da.UniversalCanDesktopApp.arm_tx)
    assert "_set_driver_listen_only(True)" in inspect.getsource(da.UniversalCanDesktopApp.disarm_tx)

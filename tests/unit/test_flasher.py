"""Unit tests for the ECU Flashing Engine (ISO 14229 sequence + safety gates).

Y-12 (REVIEW-QWEN): the flashing orchestrator is safety-critical — it gets
its own dedicated suite: dual-confirmation enforcement, E-Stop abort,
failed-transfer recovery, checksum rejection, and K-08 recovery confirmation
propagation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import ProtocolError, SafetyError
from src.protocols.uds.flasher import EcuFlashingEngine, FlashingConfig, FlashingStep

# T57-D / F-1: the flasher now verifies an Ed25519 firmware signature over the
# image and enforces target-identity verification by default. These tests
# exercise OTHER flasher behaviours (sequencing, recovery, contracts), so they
# supply a valid signature and opt out of the identity DID check; the mandatory
# paths are covered in tests/unit/test_t57d_flasher_firmware_trust.py.
_FW_KEY = ed25519.Ed25519PrivateKey.generate()
_FW_PUB = _FW_KEY.public_key()


@dataclass
class _UdsResponse:
    is_positive: bool = True
    nrc_description_tr: str = ""
    data: bytes = b""
    nrc: int = 0x10


class _RecordingUdsClient:
    """Scriptable UDS client double: per-method reply queues + call log."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Map method name -> list of responses (popped left); missing entry
        # means "always positive default".
        self.replies: dict[str, list[_UdsResponse]] = {}
        self.users_confirmed: list[bool | None] = []

    def _record(self, name: str, **kwargs: Any) -> _UdsResponse:
        confirmed = kwargs.get("user_confirmed")
        if "user_confirmed" in kwargs:
            self.users_confirmed.append(confirmed)
        self.calls.append((name, kwargs))
        queue = self.replies.get(name)
        if queue:
            resp = queue.pop(0)
        else:
            # P1-6: default 0x34 response carries a parseable
            # maxNumberOfBlockLength (lengthFormat 0x20: 2-byte length).
            # Default 0x36 TransferData echoes the block sequence counter (ISO 14229-1).
            if name == "request_download":
                resp = _UdsResponse(data=bytes([0x20, 0x10, 0x00]))
            elif name == "transfer_data":
                block_seq = kwargs.get("block_sequence", 1)
                resp = _UdsResponse(data=bytes([block_seq & 0xFF]))
            elif name == "security_access_request_seed":
                # REVIEW (SecurityAccess echo): a real ECU answers
                # `67 <requested level> <seed bytes>`; the flasher now
                # validates the subfunction echo before consuming the seed.
                # P1-5: the programming-session seed request uses level=1.
                req_level = kwargs.get("level", 1)
                resp = _UdsResponse(data=bytes([req_level & 0xFF, 0x11, 0x22, 0x33, 0x44]))
            else:
                resp = _UdsResponse(data=b"")
        return resp

    def read_did(self, did: int, **kw: Any) -> _UdsResponse:
        return self._record("read_did", did=did, **kw)

    def change_session(self, session_type: Any, **kw: Any) -> _UdsResponse:
        return self._record("change_session", session_type=session_type, **kw)

    def security_access_request_seed(self, level: int = 1, **kw: Any) -> _UdsResponse:
        return self._record("security_access_request_seed", level=level, **kw)

    def security_access_send_key(self, level: int, key: bytes, **kw: Any) -> _UdsResponse:
        return self._record("security_access_send_key", level=level, key=key, **kw)

    def request_download(self, memory_address: int, memory_size: int, **kw: Any) -> _UdsResponse:
        return self._record("request_download", memory_address=memory_address, memory_size=memory_size, **kw)

    def transfer_data(self, block_sequence: int = 0, data: bytes = b"", **kw: Any) -> _UdsResponse:
        return self._record("transfer_data", block_sequence=block_sequence, data=data, **kw)

    def request_transfer_exit(self, **kw: Any) -> _UdsResponse:
        return self._record("request_transfer_exit", **kw)

    def start_routine(self, routine_id: int, options: bytes = b"", **kw: Any) -> _UdsResponse:
        return self._record("start_routine", routine_id=routine_id, options=options, **kw)

    def request_routine_results(self, routine_id: int, **kw: Any) -> _UdsResponse:
        return self._record("request_routine_results", routine_id=routine_id, **kw)

    def ecu_reset(self, reset_type: int = 0x01, user_confirmed: bool = False, **kw: Any) -> _UdsResponse:
        return self._record("ecu_reset", reset_type=reset_type, user_confirmed=user_confirmed, **kw)


class _StubGateway:
    """TxSafetyGateway double: controllable E-Stop + P1-8 precondition fields."""

    def __init__(
        self,
        engaged: bool = False,
        tx_permitted: bool = True,
        lease_valid: bool = True,
        speed_kmh: float = 0.0,
        speed_fresh: bool = True,
    ) -> None:
        estop = MagicMock()
        estop.is_engaged = engaged
        self.estop = estop
        self.supervisor = MagicMock()
        self.supervisor.is_tx_permitted = tx_permitted
        self.watchdog = MagicMock()
        self.watchdog.is_lease_valid = lease_valid
        self.SPEED_NOISE_THRESHOLD_KMH = 0.5
        # T47-B (P1/G-1): the interlock value lives on the PHYSICAL channel;
        # the display mirror is read-only on the real gateway. This double
        # models a gateway whose physical sample is `speed_kmh`.
        self._physical_speed_kmh = speed_kmh
        self._display_speed_kmh = speed_kmh
        self._last_speed_update_ns = 1 if speed_fresh else 0


def _config(**overrides: Any) -> FlashingConfig:
    defaults: dict[str, Any] = {
        "memory_address": 0x08000000,
        "data": b"\x55" * 512,
        "block_size": 256,
        "user_confirmed": True,
        # REVIEW hardening: verify_checksum=False is fail-closed rejected.
        "verify_checksum": True,
        "reset_after_flash": True,
        # T57-D / F-1 + F-4: satisfy the (now mandatory) signature + identity
        # gates so these tests isolate their own behaviour.
        "require_target_identity": False,
        "trusted_pubkey": _FW_PUB,
        "firmware_signature": _FW_KEY.sign(b"\x55" * 512),
    }
    defaults.update(overrides)
    # Re-sign whenever the payload was overridden without an explicit sig.
    if "firmware_signature" not in overrides:
        defaults["firmware_signature"] = _FW_KEY.sign(bytes(defaults["data"]))
    return FlashingConfig(**defaults)


def _run(client: _RecordingUdsClient, config: FlashingConfig, engaged: bool = False) -> EcuFlashingEngine:
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway(engaged=engaged))
    engine.execute_flash(config)
    return engine


def test_flash_requires_dual_confirmation() -> None:
    """An unconfirmed config is rejected before ANY UDS traffic is sent."""
    client = _RecordingUdsClient()
    with pytest.raises(SafetyError, match="Dual-Confirmation"):
        _run(client, _config(user_confirmed=False))
    assert client.calls == []  # no session/security/download attempted


def test_flash_aborts_when_estop_engaged() -> None:
    """E-Stop engagement blocks flashing before any UDS traffic is sent."""
    client = _RecordingUdsClient()
    with pytest.raises(SafetyError, match="E-Stop"):
        _run(client, _config(), engaged=True)
    assert client.calls == []


# ---------------------------------------------------------------------------
# FAZ 2 (review #5): the flashing engine's run state (`current_step`,
# `_is_cancelled`, `_active_config`) is now guarded by a reentrant state lock,
# and a single-run claim makes a concurrent `execute_flash` fail closed.
# ---------------------------------------------------------------------------


def test_faz2_cancel_is_visible_from_another_thread() -> None:
    """A `cancel()` from a different thread must abort the flash deterministically."""
    import threading

    from src.core.errors import ProtocolError

    client = _RecordingUdsClient()
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())

    reached = threading.Event()
    release = threading.Event()

    # Make the first UDS step park so the cancel() lands mid-flash.
    original = client.change_session

    def _blocking_change_session(*args: Any, **kwargs: Any) -> _UdsResponse:
        reached.set()
        assert release.wait(timeout=5.0)
        return original(*args, **kwargs)

    client.change_session = _blocking_change_session  # type: ignore[method-assign]

    result: list[BaseException] = []

    def _run_flash() -> None:
        try:
            engine.execute_flash(_config())
        except BaseException as exc:  # noqa: BLE001 — captured for the assertion
            result.append(exc)

    flash_thread = threading.Thread(target=_run_flash)
    flash_thread.start()
    assert reached.wait(timeout=5.0)

    engine.cancel()  # UI thread
    release.set()
    flash_thread.join(timeout=5.0)

    assert not flash_thread.is_alive()
    assert len(result) == 1
    assert isinstance(result[0], ProtocolError)
    assert "iptal" in str(result[0]).lower()


def test_faz2_concurrent_execute_flash_fails_closed() -> None:
    """A second concurrent `execute_flash` on the same engine must be refused."""
    import threading

    client = _RecordingUdsClient()
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())

    reached = threading.Event()
    release = threading.Event()
    original = client.change_session

    def _blocking_change_session(*args: Any, **kwargs: Any) -> _UdsResponse:
        reached.set()
        assert release.wait(timeout=5.0)
        return original(*args, **kwargs)

    client.change_session = _blocking_change_session  # type: ignore[method-assign]

    first_result: list[BaseException] = []

    def _first() -> None:
        try:
            engine.execute_flash(_config())
        except BaseException as exc:  # noqa: BLE001
            first_result.append(exc)

    t = threading.Thread(target=_first)
    t.start()
    assert reached.wait(timeout=5.0)

    # Second call while the first is mid-flight -> fail closed.
    with pytest.raises(ProtocolError, match="zaten bir flashing"):
        engine.execute_flash(_config())

    release.set()
    t.join(timeout=5.0)

    # The first run must NOT have been cancelled by the second call's state
    # reset (the bug this fix closes): if it failed, it failed for its own
    # reason (an unscripted routine response), never with the cancel message.
    assert not any("iptal" in str(exc).lower() for exc in first_result)


def test_faz2_state_lock_does_not_serialize_the_whole_flash() -> None:
    """The state lock must not be held across bus I/O.

    `cancel()` from another thread must be able to acquire `_state_lock`
    promptly while a flash is blocked inside a UDS call.
    """
    import threading
    import time as _time

    client = _RecordingUdsClient()
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())

    reached = threading.Event()
    release = threading.Event()
    original = client.change_session

    def _blocking_change_session(*args: Any, **kwargs: Any) -> _UdsResponse:
        reached.set()
        assert release.wait(timeout=5.0)
        return original(*args, **kwargs)

    client.change_session = _blocking_change_session  # type: ignore[method-assign]

    # The flash thread is expected to end with a cancellation ProtocolError;
    # capture it so the failure is asserted rather than surfacing as an
    # unhandled-thread-exception warning.
    errors: list[BaseException] = []

    def _flash() -> None:
        try:
            engine.execute_flash(_config())
        except BaseException as exc:  # noqa: BLE001 — asserted below
            errors.append(exc)

    t = threading.Thread(target=_flash)
    t.start()
    assert reached.wait(timeout=5.0)

    start = _time.monotonic()
    engine.cancel()  # must not block on the state lock
    elapsed = _time.monotonic() - start
    assert elapsed < 1.0, f"cancel() blocked for {elapsed:.3f}s — lock held across I/O?"

    release.set()
    t.join(timeout=5.0)
    assert len(errors) == 1
    assert "iptal" in str(errors[0]).lower()


def test_faz2_current_step_property_reads_consistently() -> None:
    """`current_step` is exposed through a locked property, not a raw attribute."""
    engine = EcuFlashingEngine(uds_client=_RecordingUdsClient(), gateway=_StubGateway())
    assert engine.current_step == FlashingStep.IDLE
    assert isinstance(type(engine).current_step, property)

    client = _RecordingUdsClient()
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]
    flashed = _run(client, _config())
    assert flashed.current_step != FlashingStep.IDLE


def test_flash_full_happy_path_sequence() -> None:
    """The happy-path sequence issues the canonical service order
    (P1-5: programming session BEFORE security access; checksum verified)."""
    client = _RecordingUdsClient()
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    # REVIEW (routineControl echo): ISO 14229 positive replies echo
    # controlType + routineId before the routineStatusRecord — a real ECU
    # answers `71 03 02 02 00` for REQUEST_ROUTINE_RESULTS on routine
    # 0x0202 with status 0x00 (correctlyCompleted). The old mock omitted
    # the echo prefix, which the parser (correctly) rejects.
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]
    engine = _run(client, _config())
    names = [name for name, _ in client.calls]
    assert names == [
        "change_session",              # extended session (0x10 0x03)
        "change_session",              # programming session (0x10 0x02)
        "request_download",
        "transfer_data",               # 512 B / 256 B blocks = 2 blocks
        "transfer_data",
        "request_transfer_exit",
        "start_routine",               # checksum verification (0x31 0x01)
        "request_routine_results",     # checksum result poll (0x31 0x03)
        "ecu_reset",
    ]
    assert engine.current_step is FlashingStep.COMPLETED


def test_flash_negative_transfer_data_triggers_recovery_and_raises() -> None:
    """A rejected 0x36 block aborts; recovery runs the non-bricking ladder."""
    client = _RecordingUdsClient()
    client.replies["transfer_data"] = [
        _UdsResponse(is_positive=False, nrc_description_tr="NRC 0x71")
    ]
    with pytest.raises(ProtocolError, match="Blok"):
        _run(client, _config())
    # REVIEW.md 3.2: 0x37-first ladder, never an automatic hard reset
    assert any(c[0] == "request_transfer_exit" for c in client.calls)
    assert not any(c[0] == "ecu_reset" for c in client.calls)


def test_flash_recovery_propagates_operator_confirmation_k08() -> None:
    """K-08 + REVIEW.md 3.2: recovery follows the 0x37 -> default-session
    ladder and NEVER issues an operator-confirmed hard reset."""
    client = _RecordingUdsClient()
    client.replies["transfer_data"] = [
        _UdsResponse(is_positive=False, nrc_description_tr="NRC 0x71")
    ]
    # 0x37 also rejected -> ladder proceeds to the default-session retreat
    client.replies["request_transfer_exit"] = [
        _UdsResponse(is_positive=False, nrc_description_tr="NRC 0x24")
    ]
    with pytest.raises(ProtocolError):
        _run(client, _config(user_confirmed=True))
    # Ladder step 1: RequestTransferExit attempted
    assert any(c[0] == "request_transfer_exit" for c in client.calls)
    # Ladder step 2: default session retreat attempted
    assert any(c[0] == "change_session" and c[1].get("session_type") == 1 for c in client.calls)
    # NEVER a hard reset on a partially flashed ECU
    assert not any(c[0] == "ecu_reset" for c in client.calls)


def test_flash_recovery_never_fabricates_confirmation() -> None:
    """K-08 strict variant: with the 0x37/0x10-0x01 ladder, recovery never
    transmits ANY dual-confirmation-gated command (no ecu_reset at all)."""
    client = _RecordingUdsClient()
    client.replies["transfer_data"] = [
        _UdsResponse(is_positive=False, nrc_description_tr="NRC 0x72")
    ]
    with pytest.raises(ProtocolError):
        engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway(engaged=False))
        cfg = _config(user_confirmed=True)
        engine.execute_flash(cfg)
    # No confirmation-gated call is ever issued during recovery
    assert not any(c[0] == "ecu_reset" for c in client.calls)
    assert any(c[0] == "request_transfer_exit" for c in client.calls)


def test_flash_checksum_rejection_triggers_recovery() -> None:
    """A failed 0x31 checksum routine aborts with the non-bricking ladder."""
    client = _RecordingUdsClient()
    client.replies["start_routine"] = [
        _UdsResponse(is_positive=False, nrc_description_tr="NRC 0x31")
    ]
    with pytest.raises(ProtocolError, match="0x31"):
        _run(client, _config(verify_checksum=True))
    assert any(c[0] == "request_transfer_exit" for c in client.calls)
    assert not any(c[0] == "ecu_reset" for c in client.calls)


def test_flash_security_access_after_programming_session_p1_5() -> None:
    """P1-5 regression: 0x27 runs INSIDE the programming session (0x10 0x02),
    never before it � the ECU re-locks security on session transition."""
    client = _RecordingUdsClient()
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    # REVIEW (routineControl echo): see happy-path test — real ECU replies
    # `71 03 <rid> <rid> <status>`; status 0x00 = correctly completed.
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())
    engine.execute_flash(_config(security_key=b"\x01\x02\x03\x04"))
    names = [name for name, _ in client.calls]
    assert names == [
        "change_session",                # extended
        "change_session",                # programming (0x10 0x02) FIRST
        "security_access_request_seed",  # THEN 0x27
        "security_access_send_key",
        "request_download",
        "transfer_data",
        "transfer_data",
        "request_transfer_exit",
        "start_routine",
        "request_routine_results",
        "ecu_reset",
    ]
    # Security level inside the programming session:
    seed_call = next(c for c in client.calls if c[0] == "security_access_request_seed")
    assert seed_call[1]["level"] == 1  # defaults to security_level


def test_flash_security_level_split_for_programming_session() -> None:
    """P1-5: programming_security_level overrides the seed/key level used
    inside the bootloader session."""
    client = _RecordingUdsClient()
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    # REVIEW (routineControl echo): `71 03 02 02 00` (controlType + rid 0x0202 + status 0x00).
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())
    engine.execute_flash(
        _config(security_key=b"\xAA", security_level=1, programming_security_level=0x11)
    )
    seed_call = next(c for c in client.calls if c[0] == "security_access_request_seed")
    key_call = next(c for c in client.calls if c[0] == "security_access_send_key")
    assert seed_call[1]["level"] == 0x11
    assert key_call[1]["level"] == 0x11


def test_flash_block_size_clamped_to_ecu_max_p1_6() -> None:
    """P1-6 regression: maxNumberOfBlockLength bounds every 0x36 message.

    ECU reports 0x00FF (255): effective payload block = 255 - 2 = 253 bytes
    even though the config asks for 256."""
    client = _RecordingUdsClient()
    # lengthFormat 0x20 → 2-byte maxNumberOfBlockLength = 0x00FF (255)
    client.replies["request_download"] = [_UdsResponse(is_positive=True, data=bytes([0x20, 0x00, 0xFF]))]
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    # REVIEW (routineControl echo): `71 03 02 02 00` (controlType + rid 0x0202 + status 0x00).
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]

    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())
    engine.execute_flash(_config())  # block_size=256 > 253

    transfers = [c for c in client.calls if c[0] == "transfer_data"]
    # 512 bytes with 253-byte blocks → 3 blocks (253 + 253 + 6)
    assert len(transfers) == 3
    assert all(len(c[1]["data"]) <= 253 for c in transfers)


def test_flash_missing_max_block_length_fails_closed_p1_6() -> None:
    """P1-6: a 0x34 response without maxNumberOfBlockLength aborts BEFORE
    any data is transferred (no half-erased ECU)."""
    client = _RecordingUdsClient()
    client.replies["request_download"] = [_UdsResponse(is_positive=True, data=b"")]

    with pytest.raises(ProtocolError, match="maxNumberOfBlockLength"):
        _run(client, _config())
    assert not any(c[0] == "transfer_data" for c in client.calls)
    # Recovery ladder attempted (session was opened); no hard reset (3.2)
    assert any(c[0] == "request_transfer_exit" for c in client.calls)
    assert not any(c[0] == "ecu_reset" for c in client.calls)


def test_flash_empty_checksum_result_fails_closed_p1_7() -> None:
    """P1-7 regression: an empty routineStatusRecord is NOT proof — the
    flash must abort instead of resetting an unverified image."""
    client = _RecordingUdsClient()
    client.replies["request_routine_results"] = [_UdsResponse(is_positive=True, data=b"")]

    with pytest.raises(ProtocolError, match="fail-closed|boş"):
        _run(client, _config(verify_checksum=True))
    # The exception aborts before the step-9 success reset; recovery runs
    # the non-bricking ladder — no ecu_reset is EVER issued (3.2)
    assert any(c[0] == "request_transfer_exit" for c in client.calls)
    assert not any(c[0] == "ecu_reset" for c in client.calls)


def test_flash_preflight_rejects_moving_vehicle_p1_8() -> None:
    """P1-8: a vehicle above the speed threshold fails at step 1 — before
    any session control reaches the ECU."""
    client = _RecordingUdsClient()
    gateway = _StubGateway(speed_kmh=5.0)
    engine = EcuFlashingEngine(uds_client=client, gateway=gateway)
    with pytest.raises(SafetyError, match="hareket"):
        engine.execute_flash(_config())
    assert client.calls == []


def test_flash_preflight_rejects_stale_speed_telemetry_p1_8() -> None:
    """P1-8: no fresh speed telemetry = cannot prove the vehicle is
    stationary = no flash."""
    client = _RecordingUdsClient()
    gateway = _StubGateway(speed_fresh=False)
    engine = EcuFlashingEngine(uds_client=client, gateway=gateway)
    with pytest.raises(SafetyError, match="taze değil|yok"):
        engine.execute_flash(_config())
    assert client.calls == []


def test_flash_preflight_rejects_locked_supervisor_p1_8() -> None:
    """P1-8: supervisor without TX permission fails before the ECU is
    disturbed (the old code hit the gateway wall only at 0x34, after the
    ECU was already in programming session)."""
    client = _RecordingUdsClient()
    gateway = _StubGateway(tx_permitted=False)
    engine = EcuFlashingEngine(uds_client=client, gateway=gateway)
    with pytest.raises(SafetyError, match="süpervizör"):
        engine.execute_flash(_config())
    assert client.calls == []


def test_flash_preflight_rejects_expired_watchdog_lease_p1_8() -> None:
    """P1-8: an expired watchdog lease fails up front."""
    client = _RecordingUdsClient()
    gateway = _StubGateway(lease_valid=False)
    engine = EcuFlashingEngine(uds_client=client, gateway=gateway)
    with pytest.raises(SafetyError, match="watchdog"):
        engine.execute_flash(_config())
    assert client.calls == []


def test_flash_with_firmware_container() -> None:
    from src.protocols.uds.firmware import FirmwareContainer, MemorySegment

    client = _RecordingUdsClient()
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]

    container = FirmwareContainer(
        segments=[MemorySegment(address=0x08010000, data=b"\xAA" * 512)],
        entry_point=0x08010000,
        file_format="intel_hex",
    )
    config = FlashingConfig(
        container=container,
        block_size=256,
        user_confirmed=True,
        # T57-D / F-1 + F-4: satisfy the mandatory signature/identity gates.
        require_target_identity=False,
        trusted_pubkey=_FW_PUB,
        firmware_signature=_FW_KEY.sign(b"\xAA" * 512),
    )
    assert config.memory_address == 0x08010000
    assert config.data == b"\xAA" * 512

    engine = _run(client, config)
    assert engine.current_step is FlashingStep.COMPLETED
    assert any(c[0] == "request_download" and c[1]["memory_address"] == 0x08010000 for c in client.calls)


def test_flash_expected_vin_match_and_mismatch() -> None:
    # 1. Match
    client = _RecordingUdsClient()
    client.replies["read_did"] = [_UdsResponse(is_positive=True, data=b"\xf1\x90VF1CORRECTVIN12345")]
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]

    config = _config(expected_vin="VF1CORRECTVIN12345")
    engine = _run(client, config)
    assert engine.current_step is FlashingStep.COMPLETED

    # 2. Mismatch
    client2 = _RecordingUdsClient()
    client2.replies["read_did"] = [_UdsResponse(is_positive=True, data=b"\xf1\x90VF1WRONGVIN0000000")]
    config2 = _config(expected_vin="VF1CORRECTVIN12345")
    with pytest.raises(SafetyError, match="VIN uyuşmazlığı"):
        _run(client2, config2)


def test_flash_with_erase_routine() -> None:
    client = _RecordingUdsClient()
    client.replies["start_routine"] = [
        _UdsResponse(is_positive=True, data=b""),  # erase routine
        _UdsResponse(is_positive=True, data=b""),  # checksum routine
    ]
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]

    config = _config(erase_routine_id=0xFF00, erase_routine_options=b"\x01")
    engine = _run(client, config)
    assert engine.current_step is FlashingStep.COMPLETED

    routine_calls = [c for c in client.calls if c[0] == "start_routine"]
    assert len(routine_calls) == 2
    assert routine_calls[0][1]["routine_id"] == 0xFF00
    assert routine_calls[1][1]["routine_id"] == 0x0202


# ============================================================================
# REVIEW 3 — flasher hardening: preflight bounds, reset negative, session
# contract (no silent user_confirmed downgrade).
# ============================================================================


def test_flash_preflight_rejects_oversized_image_review3() -> None:
    """REVIEW 3: an image larger than the ECU profile bound is rejected at
    config construction — BEFORE the erase routine wipes the target region."""
    with pytest.raises(ValueError, match="exceeds ECU profile max"):
        _config(max_image_bytes=256)  # default data is 512 bytes


def test_flash_preflight_rejects_address_out_of_window_review3() -> None:
    """REVIEW 3: address outside the ECU memory window is rejected up front."""
    with pytest.raises(ValueError, match="below ECU profile minimum"):
        _config(memory_min_address=0x08020000)  # default address 0x08000000
    with pytest.raises(ValueError, match="exceeds ECU profile maximum"):
        _config(memory_max_address=0x08000100)  # 512-byte image overruns


def test_flash_preflight_rejects_unaligned_address_review3() -> None:
    """REVIEW 3: alignment violation is rejected up front."""
    with pytest.raises(ValueError, match="not 4-byte aligned"):
        _config(memory_address=0x08000002, memory_alignment=4)


def test_flash_ecu_reset_negative_response_fails_review3() -> None:
    """REVIEW 3-HIGH: a rejected 0x11 must NOT be reported as COMPLETED —
    the image is written but the ECU never booted into it."""
    client = _RecordingUdsClient()
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]
    client.replies["ecu_reset"] = [
        _UdsResponse(is_positive=False, nrc_description_tr="NRC 0x22 koşullar yanlış")
    ]
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())
    with pytest.raises(ProtocolError, match="ECU Reset .* reddedildi"):
        engine.execute_flash(_config())
    assert engine.current_step is FlashingStep.FAILED
    # Recovery ladder ran (no silent completion).
    assert any(c[0] == "request_transfer_exit" for c in client.calls)


def test_flash_session_contract_no_typeerror_fallback_review3() -> None:
    """REVIEW 3-CRITICAL: a client whose change_session lacks user_confirmed
    must fail HARD — the old TypeError fallback silently re-issued the
    request without the dual-confirmation flag (gateway Stage-5 bypass)."""
    class _LegacyClient(_RecordingUdsClient):
        def change_session(self, session_type: Any, **kw: Any) -> _UdsResponse:
            if "user_confirmed" in kw:
                raise TypeError("legacy signature without user_confirmed")
            return super().change_session(session_type)

    client = _LegacyClient()
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())
    with pytest.raises(TypeError):
        engine.execute_flash(_config())


def test_flash_transfer_data_contract_no_unflagged_fallback_review2() -> None:
    """REVIEW 2 CRITICAL-1: a client whose transfer_data cannot accept
    is_critical_command must fail HARD with SafetyError — the old
    inspect.signature fallback silently sent the 0x36 flash write WITHOUT
    the critical-command flag (gateway Stage-4/5 bypass)."""
    class _LegacyTransferClient(_RecordingUdsClient):
        def transfer_data(self, block_sequence: int = 0, data: bytes = b"", **kw: Any) -> _UdsResponse:
            if "is_critical_command" in kw:
                raise TypeError("legacy transfer_data signature without is_critical_command")
            return super().transfer_data(block_sequence, data=data)

    client = _LegacyTransferClient()
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())
    with pytest.raises(SafetyError, match="transfer_data safety contract"):
        engine.execute_flash(_config())
    # The blocked call never reached an unflagged send.
    unflagged = [c for c in client.calls if c[0] == "transfer_data" and not c[1].get("is_critical_command")]
    assert not unflagged


def test_flash_transfer_data_always_carries_critical_flag_review2() -> None:
    """REVIEW 2: every 0x36 block call carries is_critical_command=True and
    the session-bound operator confirmation (P1-1 chain preserved)."""
    client = _RecordingUdsClient()
    client.replies["start_routine"] = [_UdsResponse(is_positive=True, data=b"")]
    client.replies["request_routine_results"] = [
        _UdsResponse(is_positive=True, data=b"\x03\x02\x02\x00")
    ]
    engine = EcuFlashingEngine(uds_client=client, gateway=_StubGateway())
    assert engine.execute_flash(_config(user_confirmed=True)) is True
    td_calls = [c for c in client.calls if c[0] == "transfer_data"]
    assert len(td_calls) == 2  # 512 B / 256 B blocks
    for _name, kwargs in td_calls:
        assert kwargs.get("is_critical_command") is True
        assert kwargs.get("user_confirmed") is True


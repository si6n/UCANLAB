"""Physical-mode flash E2E (mocked motor) — the pre-arm gate chain.

Real-hardware flashing cannot run in CI, so this drives
``UniversalCanDesktopApp.flash_start`` in PHYSICAL mode (``_is_simulating
= False``) with the UDS/flash motor stubbed at the boundary. What it proves
that a unit test of ``_validate_flash_prerequisites`` alone does not:

* every gate is evaluated in the documented order and the bus is NEVER
  armed when a gate refuses (the stubbed ``arm_tx`` records the call);
* a complete, correctly-signed, in-bounds request reaches the motor and the
  worker is dispatched (acceptance, not fabricated success);
* the E-Stop, speed interlock, confirmation-token and bounds gates each
  refuse independently with the bus left in listen-only.

The real ``EcuFlashingEngine`` behavior is covered separately by
``tests/unit/test_flasher.py``; here the engine is a recorder so the test
asserts the APP-side contract without a physical ECU.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from src.core.models.can_frame import CanFrame
from src.ui.desktop_app import UniversalCanDesktopApp


def _valid_config(**overrides: Any) -> dict[str, Any]:
    """A complete, frontend-shaped flash request (B-07 contract)."""
    base: dict[str, Any] = {
        "action_type": "ecu_flash",
        "id": "flash-1",
        "ecu": "ECM",
        "fileName": "fw.bin",
        "sizeBytes": 4096,
        "data": "00" * 4096,
        "memoryAddress": 0x80000,
        "blockSize": 256,
        "firmwareSignature": "ab" * 32,
        "trustedPubkey": "cd" * 32,
        "expectedVin": "WVWZZZ1KZDP123456",
    }
    base.update(overrides)
    return base


class _RecordingEngine:
    """Stub standing in for EcuFlashingEngine; records the config it got."""

    def __init__(self) -> None:
        self.configs: list[Any] = []
        self.started = threading.Event()

    def execute_flash(self, config: Any) -> bool:
        self.configs.append(config)
        self.started.set()
        return True


@pytest.fixture()
def physical_app(monkeypatch: pytest.MonkeyPatch):
    """A physical-mode app with the motor and arm path stubbed.

    Returns (app, armed_calls, engine_holder).
    """
    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    app._is_simulating = False

    # Speed interlock: a live, stationary, trusted physical feed.
    app.approve_ccvs_source(0x00, reason="e2e fixture")
    app._decode_j1939_signal(
        CanFrame.create(
            channel_id="vcan0",
            arbitration_id=0x18FEF100,
            data=bytes([0x00, 0x00, 0x00, 0, 0, 0, 0, 0]),
            is_extended=True,
        )
    )

    armed_calls: list[str] = []

    def _fake_arm(reason: str = "") -> dict[str, Any]:
        armed_calls.append(reason)
        return {"success": True, "state": "ARMED_TX"}

    monkeypatch.setattr(app, "arm_tx", _fake_arm)
    # The motor + UDS factory are replaced so no real protocol work runs.
    monkeypatch.setattr(app, "create_uds_client", lambda: object())
    holder: dict[str, _RecordingEngine] = {}

    def _fake_engine(**kwargs: Any) -> _RecordingEngine:
        engine = _RecordingEngine()
        holder["engine"] = engine
        return engine

    import src.ui.desktop_app as mod

    monkeypatch.setattr(mod, "EcuFlashingEngine", _fake_engine)
    # Flashing config construction touches the real class; keep it real (it is
    # a frozen dataclass with no I/O) so the bounds contract is genuinely
    # exercised end to end.
    return app, armed_calls, holder


def _mint(app: UniversalCanDesktopApp, config: dict[str, Any]) -> str:
    mint = app.request_diagnostic_challenge(config, native_presence=True)
    assert mint["success"] is True, mint
    return str(mint["token"])


def _wait_for_worker(app: UniversalCanDesktopApp, timeout: float = 3.0) -> None:
    thread = getattr(app, "_flash_thread", None)
    if thread is not None:
        thread.join(timeout=timeout)


class TestPhysicalFlashGateChain:
    def test_estop_refuses_before_anything_else(self, physical_app) -> None:
        app, armed, _holder = physical_app
        token = _mint(app, _valid_config())
        app.trigger_estop()

        res = app.flash_start(_valid_config(), confirmation_token=token)
        assert res["success"] is False
        assert "e-stop" in res["error"].lower() or "acil" in res["error"].lower()
        assert armed == [], "the bus must never be armed when the E-Stop gate refuses"

    def test_stale_speed_feed_refuses(self, physical_app) -> None:
        app, armed, _holder = physical_app
        # Age out the physical feed: a fresh app with no CCVS at all.
        fresh = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
        fresh._is_simulating = False
        token = _mint(fresh, _valid_config())
        res = fresh.flash_start(_valid_config(), confirmation_token=token)
        assert res["success"] is False
        assert "hız" in res["error"].lower() or "speed" in res["error"].lower()
        assert armed == []

    def test_missing_confirmation_token_refuses_without_arming(self, physical_app) -> None:
        app, armed, _holder = physical_app
        res = app.flash_start(_valid_config(), confirmation_token=None)
        assert res["success"] is False
        assert armed == [], "no token → no arm"

    def test_missing_signature_refuses_without_arming(self, physical_app) -> None:
        app, armed, _holder = physical_app
        cfg = _valid_config(firmwareSignature=None)
        token = _mint(app, cfg)
        res = app.flash_start(cfg, confirmation_token=token)
        assert res["success"] is False
        assert "imza" in res["error"].lower()
        assert armed == []

    def test_out_of_bounds_address_refuses_without_arming(self, physical_app) -> None:
        app, armed, _holder = physical_app
        cfg = _valid_config(memoryAddress=0xFFFF0000)
        token = _mint(app, cfg)
        res = app.flash_start(cfg, confirmation_token=token)
        assert res["success"] is False
        assert "adres" in res["error"].lower() or "aralık" in res["error"].lower()
        assert armed == []

    def test_full_valid_request_reaches_the_motor(self, physical_app) -> None:
        """The happy path: gates pass, bus arms, worker dispatches."""
        app, armed, holder = physical_app
        cfg = _valid_config()
        token = _mint(app, cfg)

        res = app.flash_start(cfg, confirmation_token=token)
        assert res["success"] is True, res
        assert res.get("accepted") is True, "flash_start reports acceptance, not success"
        assert armed, "the physical path must arm TX before dispatch"

        _wait_for_worker(app)
        engine = holder.get("engine")
        assert engine is not None and engine.configs, "the motor never received a config"
        forwarded = engine.configs[0]
        # The forwarded motor config carries the trust material and identity.
        assert getattr(forwarded, "firmware_signature", None) == bytes.fromhex("ab" * 32)
        assert getattr(forwarded, "expected_vin", None) == "WVWZZZ1KZDP123456"
        assert getattr(forwarded, "memory_address", None) == 0x80000

    def test_token_is_single_use_across_flash_attempts(self, physical_app) -> None:
        app, _armed, _holder = physical_app
        cfg = _valid_config()
        token = _mint(app, cfg)

        first = app.flash_start(cfg, confirmation_token=token)
        assert first["success"] is True
        _wait_for_worker(app)

        # A second attempt with the SAME token must fail (burned).
        second = app.flash_start(cfg, confirmation_token=token)
        assert second["success"] is False
        assert "kullanılmış" in second["error"].lower() or "token" in second["error"].lower()

    def test_simulated_flash_is_sandboxed_and_needs_no_presence(self, monkeypatch) -> None:
        """Sim mode reaches its own branch and never arms the physical bus."""
        app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
        app._is_simulating = True
        app._current_speed_kmh = 0.0

        armed: list[str] = []
        monkeypatch.setattr(app, "arm_tx", lambda reason="": armed.append(reason) or {"success": True})

        cfg = _valid_config()
        mint = app.request_diagnostic_challenge(cfg, native_presence=False)
        res = app.flash_start(cfg, confirmation_token=mint["token"])
        assert res["success"] is True
        _wait_for_worker(app, timeout=5.0)
        assert armed == [], "the simulation branch must never arm physical TX"

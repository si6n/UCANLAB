"""Workbench B5 (ECU programlama): image integrity, digest binding, dry run on the simulator."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import pytest

from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp


@pytest.fixture
def app() -> Any:
    application = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    application._reconnect_bus("virtual", "ecu_test", 250000)
    return application


def _image(n: int = 64 * 1024) -> bytes:
    return bytes((i * 7 + 3) & 0xFF for i in range(n))


def _request(image: bytes, **extra: Any) -> dict[str, Any]:
    return {
        "action_type": "ecu_flash",
        "id": "flash-ECM-1",
        "ecu": "ECM",
        "fileName": "fw.bin",
        "sizeBytes": len(image),
        "memoryAddress": 0x8000,
        "blockSize": 1024,
        "dataSha256": hashlib.sha256(image).hexdigest(),
        **extra,
    }


def _wait_flash(app: Any, timeout_s: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        prog = app.flash_progress()
        if prog.get("status") in ("completed", "failed", "cancelled"):
            return prog
        time.sleep(0.05)
    raise AssertionError("flash did not finish")


@pytest.mark.parametrize(
    ("config", "fragment"),
    [
        ({}, "yok"),
        ({"data": "zz"}, "hex değil"),
        ({"data": ""}, "boş"),
        ({"data": "0011", "sizeBytes": 3}, "boyutu"),
        ({"data": "0011", "dataSha256": "00" * 32}, "SHA-256"),
    ],
)
def test_flash_image_is_mandatory_and_consistent(config: dict[str, Any], fragment: str) -> None:
    err = UniversalCanDesktopApp._check_flash_image(config)
    assert err is not None and fragment in err


def test_flash_image_check_accepts_a_consistent_image() -> None:
    img = _image(32)
    cfg = {"data": img.hex(), "sizeBytes": 32, "dataSha256": hashlib.sha256(img).hexdigest().upper()}
    assert UniversalCanDesktopApp._check_flash_image(cfg) is None


def test_missing_image_is_refused_before_any_token_is_spent(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    cfg = {"action_type": "ecu_flash", "id": "x", "sizeBytes": 2048}
    ch = app.request_diagnostic_challenge(cfg)
    res = app.flash_start(cfg, ch["token"])
    assert res["success"] is False and "yok" in res["error"]
    assert ch["token"] in app._diagnostic_challenges  # still unspent


def test_large_image_is_authorized_by_its_digest_on_the_simulator(app: Any) -> None:
    """A 64 KiB image (128 KiB of hex) is far past the 16 KiB challenge cap."""
    app.start_simulated_vehicle("truck")
    sent: list[Any] = []
    app.bus.send = lambda frame, *a, **k: sent.append(frame)  # would record any TX
    img = _image()
    req = _request(img)
    ch = app.request_diagnostic_challenge(req)  # the digest, not the bytes
    assert ch["success"] is True
    res = app.flash_start({**req, "data": img.hex()}, ch["token"])
    assert res["success"] is True, res
    assert app.flash_preconditions()["flash_status"] in ("in_progress", "completed")
    prog = _wait_flash(app)
    assert prog["status"] == "completed" and prog["total_bytes"] == len(img)
    assert sent == []
    assert not app.supervisor.is_tx_permitted


def test_tampered_image_is_refused_even_with_a_valid_token(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    img = _image(4096)
    req = _request(img)
    ch = app.request_diagnostic_challenge(req)
    evil = bytearray(img)
    evil[100] ^= 0xFF
    res = app.flash_start({**req, "data": bytes(evil).hex()}, ch["token"])
    assert res["success"] is False and "SHA-256" in res["error"]


def test_digest_swapped_after_the_challenge_is_refused(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    approved, other = _image(4096), _image(4000)
    ch = app.request_diagnostic_challenge(_request(approved))
    res = app.flash_start({**_request(other), "data": other.hex()}, ch["token"])
    assert res["success"] is False and "parametre" in res["error"]


def test_target_ecu_is_part_of_the_binding(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    img = _image(2048)
    ch = app.request_diagnostic_challenge(_request(img))
    res = app.flash_start({**_request(img, ecu="TCU"), "data": img.hex()}, ch["token"])
    assert res["success"] is False and "parametre" in res["error"]


def test_non_simulated_bus_still_requires_native_presence(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
    monkeypatch.setattr(app.gateway, "speed_interlock_state", lambda *a, **k: ("ok", 0.0))
    img = _image(2048)
    req = _request(img)
    ch = app.request_diagnostic_challenge(req, native_presence=False)
    res = app.flash_start({**req, "data": img.hex()}, ch["token"])
    assert res["success"] is False and "native" in res["error"]


def test_non_simulated_bus_refuses_without_a_live_speed_feed(app: Any) -> None:
    img = _image(2048)
    req = _request(img)
    ch = app.request_diagnostic_challenge(req, native_presence=True)
    res = app.flash_start({**req, "data": img.hex()}, ch["token"])
    assert res["success"] is False and "hız" in res["error"].lower()


def test_estop_blocks_the_simulator_dry_run_too(app: Any) -> None:
    app.start_simulated_vehicle("truck")
    app._is_estop = True  # latched E-Stop (the UI flag the gate reads first)
    img = _image(2048)
    req = _request(img)
    ch = app.request_diagnostic_challenge(req)
    res = app.flash_start({**req, "data": img.hex()}, ch["token"])
    assert res["success"] is False and "E-Stop" in res["error"]
    assert app.flash_preconditions()["estop"] is True


def test_preconditions_report_mode_and_gates(app: Any) -> None:
    pre = app.flash_preconditions()
    assert pre["simulated"] is False and pre["native_confirmation_required"] is True
    assert pre["speed_state"] == "stale" and pre["flash_status"] == "idle"
    app.start_simulated_vehicle("truck")
    for _ in range(200):
        app._ingest_live_frame(app.bus.recv(timeout_s=0.0))
    pre = app.flash_preconditions()
    assert pre["simulated"] is True and pre["native_confirmation_required"] is False
    assert pre["speed_state"] == "ok" and pre["speed_kmh"] == 0.0


def test_preconditions_is_a_read_bridge_method(app: Any) -> None:
    assert DesktopApiBridge.BRIDGE_RISK_MANIFEST["flash_preconditions"] == "read"
    assert DesktopApiBridge(app).flash_preconditions()["success"] is True

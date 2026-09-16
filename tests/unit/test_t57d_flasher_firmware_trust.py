"""T57-D / F-1 + F-4 + F-2 regression tests for the flasher trust chain.

Findings (docs/review/ASAMA-4-protokoller.md):
- F-1: `firmware_signature` was only length-checked; no cryptographic
  verification against the image, and `min_version` anti-rollback was never
  enforced (fail-open). Fix: `trusted_pubkey` + `require_signature=True`
  (default) Ed25519 verification over `bytes(config.data)`, fail-closed.
- F-4: VIN/serial target-identity verification was optional (default OFF).
  Fix: `require_target_identity=True` (default) — a flash without an
  expected VIN/serial is refused before any frame leaves the tool.
- F-2 plumbing: the flasher must present the gateway HMAC confirmation
  token on every critical step, or production flash dies at step-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import SafetyError
from src.protocols.uds.flasher import EcuFlashingEngine, FlashingConfig, FlashingStep

_PRIVKEY = ed25519.Ed25519PrivateKey.generate()
_PUBKEY = _PRIVKEY.public_key()


@dataclass
class _UdsResponse:
    is_positive: bool = True
    nrc_description_tr: str = ""
    data: bytes = b""
    nrc: int = 0x10


class _StubClient:
    """Scriptable UDS client double accepting the flasher's keyword contract."""

    def __init__(self, did_replies: dict[int, bytes] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.did_replies = did_replies or {}

    def _record(self, name: str, **kwargs: Any) -> _UdsResponse:
        self.calls.append((name, kwargs))
        if name == "request_download":
            return _UdsResponse(data=bytes([0x20, 0x10, 0x00]))
        if name == "transfer_data":
            seq = kwargs.get("block_sequence", 1)
            return _UdsResponse(data=bytes([seq & 0xFF]))
        if name == "read_did":
            did = kwargs.get("did")
            if did in self.did_replies:
                return _UdsResponse(data=self.did_replies[did])
        if name == "request_routine_results":
            return _UdsResponse(data=b"\x03\x02\x02\x00")
        return _UdsResponse(data=b"")

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
    """Gateway double WITHOUT a confirmation secret (legacy wiring)."""

    def __init__(self, engaged: bool = False) -> None:
        estop = MagicMock()
        estop.is_engaged = engaged
        self.estop = estop
        self.supervisor = MagicMock()
        self.supervisor.is_tx_permitted = True
        self.watchdog = MagicMock()
        self.watchdog.is_lease_valid = True
        self.SPEED_NOISE_THRESHOLD_KMH = 0.5
        self._physical_speed_kmh = 0.0
        self._display_speed_kmh = 0.0
        self._last_speed_update_ns = 1


class _SecretGateway(_StubGateway):
    """Gateway double WITH a confirmation secret (production wiring)."""

    def __init__(self) -> None:
        super().__init__()
        self._confirmation_secret = b"S" * 32
        self.issued: list[bytes] = []

    def issue_confirmation_token(self, arbitration_id: int, ttl_s: float = 30.0) -> bytes:
        token = b"TOKEN" + bytes([len(self.issued) & 0xFF])
        self.issued.append(token)
        return token


def _signed_config(data: bytes = b"\x55" * 512, **overrides: Any) -> FlashingConfig:
    """Build a config with a valid Ed25519 signature and identity opt-out."""
    defaults: dict[str, Any] = {
        "memory_address": 0x08000000,
        "data": data,
        "block_size": 256,
        "user_confirmed": True,
        "verify_checksum": True,
        "reset_after_flash": True,
        # F-4 identity is mandatory by default; this suite isolates the
        # signature path, so identity is explicitly opted out where untested.
        "require_target_identity": False,
        "trusted_pubkey": _PUBKEY,
        "firmware_signature": _PRIVKEY.sign(bytes(data)),
    }
    defaults.update(overrides)
    # Re-sign whenever the payload was overridden without an explicit sig.
    if "firmware_signature" not in overrides:
        defaults["firmware_signature"] = _PRIVKEY.sign(bytes(defaults["data"]))
    return FlashingConfig(**defaults)


def _run(client: _StubClient, config: FlashingConfig, gateway: Any | None = None) -> EcuFlashingEngine:
    engine = EcuFlashingEngine(uds_client=client, gateway=gateway or _StubGateway())
    engine.execute_flash(config)
    return engine


# ---------------------------------------------------------------------------
# F-1 — firmware signature is cryptographically verified (fail-closed)
# ---------------------------------------------------------------------------


def test_f1_missing_signature_fails_closed_before_any_tx() -> None:
    client = _StubClient()
    cfg = _signed_config(firmware_signature=None)
    with pytest.raises(SafetyError) as exc:
        _run(client, cfg)
    assert exc.value.code == "FLASH_SIGNATURE_MISSING"
    assert client.calls == []  # nothing reached the ECU


def test_f1_missing_trust_anchor_fails_closed() -> None:
    client = _StubClient()
    cfg = _signed_config(trusted_pubkey=None)
    with pytest.raises(SafetyError) as exc:
        _run(client, cfg)
    assert exc.value.code == "FLASH_TRUST_ANCHOR_MISSING"
    assert client.calls == []


def test_f1_invalid_signature_fails_closed() -> None:
    client = _StubClient()
    data = b"\x55" * 512
    cfg = _signed_config(data=data, firmware_signature=_PRIVKEY.sign(b"\x00" * 512))
    with pytest.raises(SafetyError) as exc:
        _run(client, cfg)
    assert exc.value.code == "FLASH_SIGNATURE_INVALID"
    assert client.calls == []


def test_f1_tampered_image_fails_closed() -> None:
    """A signature valid for image A must not authorize tampered image B."""
    client = _StubClient()
    data = b"\x55" * 512
    signature = _PRIVKEY.sign(data)
    cfg = FlashingConfig(
        memory_address=0x08000000,
        data=b"\x66" * 512,  # tampered
        block_size=256,
        user_confirmed=True,
        require_target_identity=False,
        trusted_pubkey=_PUBKEY,
        firmware_signature=signature,
    )
    with pytest.raises(SafetyError) as exc:
        _run(client, cfg)
    assert exc.value.code == "FLASH_SIGNATURE_INVALID"
    assert client.calls == []


def test_f1_valid_signature_accepted() -> None:
    client = _StubClient()
    engine = _run(client, _signed_config())
    assert engine.current_step is FlashingStep.COMPLETED
    assert any(name == "transfer_data" for name, _ in client.calls)


def test_f1_explicit_require_signature_false_is_the_only_optout() -> None:
    """require_signature=False is a documented opt-out (no signature needed)."""
    client = _StubClient()
    cfg = _signed_config(require_signature=False, firmware_signature=None, trusted_pubkey=None)
    engine = _run(client, cfg)
    assert engine.current_step is FlashingStep.COMPLETED


# ---------------------------------------------------------------------------
# F-1 — min_version anti-rollback is enforced
# ---------------------------------------------------------------------------


def test_f1_min_version_rollback_denied() -> None:
    client = _StubClient(did_replies={0xF189: b"\xf1\x89" + b"3"})
    cfg = _signed_config(min_version=5)
    with pytest.raises(SafetyError) as exc:
        _run(client, cfg)
    assert exc.value.code == "FLASH_ROLLBACK_DENIED"
    # Rejected before any erase/write routine
    assert not any(name == "request_download" for name, _ in client.calls)
    assert not any(name == "start_routine" for name, _ in client.calls)


def test_f1_min_version_satisfied_allows_flash() -> None:
    client = _StubClient(did_replies={0xF189: b"\xf1\x89" + b"7"})
    cfg = _signed_config(min_version=5)
    engine = _run(client, cfg)
    assert engine.current_step is FlashingStep.COMPLETED


def test_f1_min_version_unverifiable_fails_closed() -> None:
    """An ECU whose version cannot be parsed must not silently pass the floor."""
    client = _StubClient(did_replies={0xF189: b"\xf1\x89" + b"unknown"})
    cfg = _signed_config(min_version=5)
    with pytest.raises(SafetyError) as exc:
        _run(client, cfg)
    assert exc.value.code == "FLASH_ROLLBACK_UNVERIFIABLE"


# ---------------------------------------------------------------------------
# F-4 — target identity (VIN/serial) is mandatory by default
# ---------------------------------------------------------------------------


def test_f4_target_identity_mandatory_by_default() -> None:
    client = _StubClient()
    cfg = FlashingConfig(
        memory_address=0x08000000,
        data=b"\x55" * 512,
        block_size=256,
        user_confirmed=True,
        trusted_pubkey=_PUBKEY,
        firmware_signature=_PRIVKEY.sign(b"\x55" * 512),
    )
    # require_target_identity defaults to True and no VIN/serial is provided.
    with pytest.raises(SafetyError) as exc:
        _run(client, cfg)
    assert exc.value.code == "FLASH_TARGET_IDENTITY_REQUIRED"
    assert client.calls == []


def test_f4_vin_verified_when_supplied() -> None:
    client = _StubClient(did_replies={0xF190: b"\xf1\x90VF1CORRECTVIN12345"})
    cfg = _signed_config(require_target_identity=True, expected_vin="VF1CORRECTVIN12345")
    engine = _run(client, cfg)
    assert engine.current_step is FlashingStep.COMPLETED


def test_f4_vin_mismatch_fails_closed() -> None:
    client = _StubClient(did_replies={0xF190: b"\xf1\x90VF1WRONGVIN0000000"})
    cfg = _signed_config(require_target_identity=True, expected_vin="VF1CORRECTVIN12345")
    with pytest.raises(SafetyError) as exc:
        _run(client, cfg)
    assert "VIN uyuşmazlığı" in str(exc.value)


# ---------------------------------------------------------------------------
# F-2 plumbing — the gateway HMAC token is presented on every critical step
# ---------------------------------------------------------------------------


def test_f2_gateway_token_presented_on_every_critical_step() -> None:
    client = _StubClient()
    gateway = _SecretGateway()
    engine = _run(client, _signed_config(), gateway=gateway)
    assert engine.current_step is FlashingStep.COMPLETED

    critical = {
        "change_session",
        "request_download",
        "transfer_data",
        "request_transfer_exit",
        "start_routine",
        "ecu_reset",
    }
    seen: set[str] = set()
    for name, kwargs in client.calls:
        if name in critical:
            seen.add(name)
            assert kwargs.get("confirmation_token") is not None, f"{name} carried no token"
    # Every critical service actually ran and carried a token.
    assert critical <= seen
    # A fresh single-use token was minted for each critical step.
    assert len(gateway.issued) >= len(seen)


def test_f2_no_secret_gateway_omits_token() -> None:
    """Legacy wiring (no confirmation secret) must not fabricate a token."""
    client = _StubClient()
    _run(client, _signed_config(), gateway=_StubGateway())
    for name, kwargs in client.calls:
        if name in {"change_session", "request_download", "transfer_data", "ecu_reset"}:
            assert kwargs.get("confirmation_token") is None

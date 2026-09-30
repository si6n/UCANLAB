"""Cross-repo contract pin: desktop client ↔ UCANLAB-CLOUD auth/license API.

``docs/product/cloud_auth_contract.v1.json`` is copied byte-for-byte to
UCANLAB-CLOUD ``backend/tests/contracts/``; that repo checks its OpenAPI schema
against it. Here we check that the client constants and the test fake agree
with the same file. Both repos pin the same digest, so changing the contract
on one side fails that side's CI until the other side is updated too.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.security.cloud import desktop_auth as da
from tests.unit.fake_cloud_auth import ROUTES

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "product" / "cloud_auth_contract.v1.json"
# Must equal CONTRACT_SHA256 in UCANLAB-CLOUD backend/tests/test_desktop_auth_contract.py.
CONTRACT_SHA256 = "e3992f813e06bbfabf8f49f4cfedc51c49bcee215dd16ff8e1dce03dfbbdcdf2"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _normalized_bytes() -> bytes:
    # Windows checkouts may convert LF to CRLF (core.autocrlf); the digest is
    # over the LF form so both repos and every OS agree on one value.
    return CONTRACT_PATH.read_bytes().replace(b"\r\n", b"\n")


def test_contract_digest_is_pinned() -> None:
    digest = hashlib.sha256(_normalized_bytes()).hexdigest()
    assert digest == CONTRACT_SHA256, (
        "cloud_auth_contract.v1.json changed: copy it to UCANLAB-CLOUD backend/tests/contracts/ and update "
        "CONTRACT_SHA256 in both repos"
    )


def test_client_constants_match_contract() -> None:
    c = _contract()["constants"]
    assert list(da.LOOPBACK_PORTS) == c["loopback_ports"]
    assert da.LOOPBACK_CALLBACK_PATH == c["loopback_callback_path"]
    assert da.DEEP_LINK_SCHEME == c["deep_link_scheme"]
    assert f"{da.DEEP_LINK_SCHEME}://{da.DEEP_LINK_HOST}{da.DEEP_LINK_PATH}" == c["deep_link_callback"]
    assert da.DEVICE_VERIFICATION_PATH == c["device_verification_path"]


def test_desktop_loopback_ports_match_contract() -> None:
    from src.ui import desktop_app

    assert list(desktop_app._DESKTOP_LOOPBACK_PORTS) == _contract()["constants"]["loopback_ports"]


def test_client_paths_are_contract_endpoints() -> None:
    paths = {(e["method"], e["path"]) for e in _contract()["endpoints"]}
    for method, path in [
        ("POST", da.PATH_DESKTOP_TOKEN),
        ("POST", da.PATH_DEVICE_START),
        ("POST", da.PATH_DEVICE_TOKEN),
        ("GET", da.PATH_LICENSES),
        ("POST", "/devices/register"),
        ("POST", "/licenses/activate"),
        ("GET", "/auth/me"),
    ]:
        assert (method, path) in paths, f"client calls {method} {path} which the contract does not define"


def test_fake_cloud_implements_exactly_the_contract() -> None:
    contract = {(e["method"], e["path"]) for e in _contract()["endpoints"]}
    assert set(ROUTES) == contract

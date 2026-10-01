"""Update staging containment: lexical gate + link/junction gate, no 8.3 races."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import io
import os
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.launcher.updater import UpdateManager

PAYLOAD = b"Update-Binary-Payload"


def _updater(monkeypatch: pytest.MonkeyPatch, root: Path, version: str) -> tuple[UpdateManager, Any]:
    key = ed25519.Ed25519PrivateKey.generate()
    updater = UpdateManager(current_version="13.0.0", public_key=key.public_key(), require_signature=True)
    info = updater._check_for_updates_unverified(custom_manifest={
        "version": version,
        "download_url": "https://cloud.universalcan.io/update.exe",
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "signature": base64.b64encode(key.sign(PAYLOAD)).decode("ascii"),
        "size_bytes": len(PAYLOAD),
    })

    class FakeResponse(io.BytesIO):
        headers = {"Content-Length": str(len(PAYLOAD))}

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", lambda self, req, timeout=60: FakeResponse(PAYLOAD))
    monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: root))
    return updater, info


def test_existing_version_directory_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The racing case: another thread already created the version directory.
    (tmp_path / "13.1.0").mkdir()
    updater, info = _updater(monkeypatch, tmp_path, "13.1.0")
    assert updater.download_update(info, "update.exe") is True
    assert (tmp_path / "13.1.0" / "update.exe").read_bytes() == PAYLOAD


@pytest.mark.parametrize("version", ["..", "."])
def test_dot_versions_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    root = tmp_path / "updates"
    root.mkdir()
    updater, info = _updater(monkeypatch, root, "13.1.0")
    info = dataclasses.replace(info, latest_version=version)
    assert updater.download_update(info, "update.exe") is False
    assert list(tmp_path.iterdir()) == [root]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink support")
def test_version_directory_linked_outside_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "updates"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, root / "13.1.0", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted for this user")
    updater, info = _updater(monkeypatch, root, "13.1.0")
    assert updater.download_update(info, "update.exe") is False
    assert list(outside.iterdir()) == []

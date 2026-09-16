"""T58-A regression test for finding A-1: `get_current_status` swallowed every
exception and returned `has_valid_license=False` without logging the reason, so
an operator could not tell WHY the machine was unlicensed (expired ticket,
device mismatch, corrupt vault, ...).

Fix (`src/launcher/auth.py`): the `except Exception` branch now emits a
structured log carrying the exception type/message and the resolved device id
before returning the unlicensed AuthStatus. The returned status is unchanged
(fail-closed) — only the observability gap is closed.

Written BEFORE the fix (TDD red -> green).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.launcher.auth import LauncherAuthManager
from src.safety.secret_provider import EphemeralSecretBackend
from src.security.cloud.client import CloudClient, CloudConfig


class _ExplodingFlow:
    """Minimal stand-in for LicenseFlow that fails ticket verification."""

    def verify_cloud_ticket(self, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("device mismatch: ticket bound to another HWID")


def _manager_with_ticket(secrets: EphemeralSecretBackend) -> LauncherAuthManager:
    priv = ed25519.Ed25519PrivateKey.generate()
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    auth = LauncherAuthManager(
        cloud_client=client,
        secret_provider=secrets,
        public_key=priv.public_key(),
    )
    # A stored ticket is required to reach the verify_cloud_ticket() try-block.
    secrets.store_secret("CLOUD_LICENSE_TICKET", b"not-a-real-ticket")
    assert auth.flow is not None, "test requires a configured license flow"
    auth.flow = _ExplodingFlow()  # type: ignore[assignment]
    return auth


def test_a1_verify_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A-1: the swallowed reason must reach the operator as a structured log."""
    secrets = EphemeralSecretBackend()
    auth = _manager_with_ticket(secrets)

    with caplog.at_level(logging.WARNING, logger="launcher.auth"):
        status = auth.get_current_status()

    # Behaviour is unchanged: still fail-closed.
    assert status.has_valid_license is False

    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert records, "get_current_status swallowed the exception with no log record"
    messages = " ".join(r.getMessage() for r in records)
    assert "unlicensed" in messages.lower()

    # Structured payload: operator needs the exception type, reason and device id.
    extras = " ".join(
        str(getattr(r, "error_type", ""))
        + " "
        + str(getattr(r, "error", ""))
        + " "
        + str(getattr(r, "device_id", ""))
        for r in records
    )
    assert "device mismatch" in extras, "the exception reason must be logged"
    assert "ValueError" in extras, "exception type must be structured on the record"
    # The record always carries the device_id key; assert it is present
    # (structured), not that it is non-empty (a fresh client has no device yet).
    assert any(hasattr(r, "device_id") for r in records), "device_id key must be structured on the record"

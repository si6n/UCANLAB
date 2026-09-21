"""Cloud updater — DELIBERATELY DISABLED (fail-closed stub). (SEC-11, Batch B)

This path used to be a 0-byte file inside the security package: a magnet for a
future ``from src.security.cloud.updater import ...`` that would silently import
an empty namespace and "succeed" while doing nothing. Rather than delete the
path (which turns a stale import into an ``ImportError`` at an arbitrary point),
the module is now an explicit fail-closed stub — every callable surface raises
``SecurityError(code="UPDATER_DISABLED")``.

Security rationale (do not "fix" this by implementing it here):

* The cloud updater is NOT enabled by design. Enabling a network-fetching
  update path requires signed manifests, an install path, and an explicit
  operator decision — see §4 of the improvement plan and the
  launcher's own signature-checked ``src/launcher/updater.py``.
* Nothing under ``src/`` imports this module, and it must stay that way.
"""

from __future__ import annotations

from typing import Any, NoReturn

from src.core.errors import SecurityError

#: Stable error code so callers/tests can assert the refusal.
UPDATER_DISABLED_CODE = "UPDATER_DISABLED"


def _disabled(operation: str) -> NoReturn:
    raise SecurityError(
        f"Cloud updater is disabled by design ({operation}); "
        "use the launcher's signed updater instead.",
        code=UPDATER_DISABLED_CODE,
    )


def check_for_updates(*args: Any, **kwargs: Any) -> NoReturn:
    """Refuse: the cloud updater is disabled."""
    _disabled("check_for_updates")


def download_update(*args: Any, **kwargs: Any) -> NoReturn:
    """Refuse: the cloud updater is disabled (no network fetch is performed)."""
    _disabled("download_update")


def install_update(*args: Any, **kwargs: Any) -> NoReturn:
    """Refuse: the cloud updater is disabled."""
    _disabled("install_update")


class CloudUpdater:
    """Fail-closed updater stand-in: construction and attribute access refuse."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _disabled("CloudUpdater()")

    def __getattr__(self, name: str) -> NoReturn:
        _disabled(f"CloudUpdater.{name}")


__all__ = [
    "UPDATER_DISABLED_CODE",
    "CloudUpdater",
    "check_for_updates",
    "download_update",
    "install_update",
]

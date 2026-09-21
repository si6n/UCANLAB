"""Single source of truth for the license anti-rollback High-Water-Mark (HWM).

SEC-02 (Batch B): the HWM secret name *and* the on-disk format used to be
duplicated across two classes that never imported each other:

* ``src/security/license/validator.py`` used the vault secret
  ``LICENSE_HWM_HMAC_KEY`` and wrote ``"<hwm_ts>:<sync_ts>.<mac>"``
  (HMAC over ``"<hwm_ts>:<sync_ts>"``).
* ``src/security/cloud/license_flow.py`` used the secret ``LICENSE_HWM_KEY``
  and wrote ``"<ts>.<mac>"`` (HMAC over ``"<ts>"``).

Because the secret names differed, a file sealed by one class could never be
verified by the other: the first read quarantined it as a "lost vault key"
(HMAC mismatch) and fell back to the conservative grace floor. Because the
*formats* differed, the reader also had to guess which layout it was looking
at. Both defects are fixed by routing every read and write through this
module:

* :data:`HWM_SECRET_NAME` — the one vault key name.
* :data:`HWM_INITIALIZED_MARKER` — the "an HWM has been written before" flag.
* :func:`seal_hwm` — canonical writer (two-field format, restrictive
  permissions, fsync-before-replace).
* :func:`parse_hwm_text` — canonical reader with explicit legacy support.

Legacy read compatibility (never broken): a single-field ``"<ts>.<mac>"`` file
is still accepted, and its MAC is verified with BOTH the legacy secret name
(``LICENSE_HWM_KEY``, for vaults written by the old ``LicenseFlow``) and the
canonical name, so no pre-existing installation is locked out or silently
quarantined.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from src.core.errors import LicenseError
from src.core.logging import get_logger

logger: logging.Logger = get_logger("security.hwm_format")

#: The one and only vault secret name for the HWM integrity key.
HWM_SECRET_NAME: str = "LICENSE_HWM_HMAC_KEY"

#: Legacy vault secret name (pre-SEC-02 ``LicenseFlow``). Read-only: kept so an
#: HWM file sealed by an older build still verifies instead of being
#: quarantined. Nothing writes under this name any more.
LEGACY_HWM_SECRET_NAMES: tuple[str, ...] = ("LICENSE_HWM_KEY",)

#: Vault flag recording that an HWM file has been written at least once, so a
#: *missing* file afterwards is a tamper signal rather than a first run.
HWM_INITIALIZED_MARKER: str = "LICENSE_HWM_INITIALIZED"

#: Length of the hex-encoded HMAC-SHA256 tag.
_MAC_HEX_LEN: int = 64


@dataclass(slots=True, frozen=True)
class HwmRecord:
    """A parsed, integrity-verified HWM file."""

    hwm_ts: int
    """Wall-clock high-water mark (anti-rollback floor), in seconds."""

    sync_ts: int | None
    """Grace-period anchor. ``None`` for a legacy single-field file that
    carried no sync component (the caller keeps its own anchor)."""

    legacy: bool
    """True when the file used the pre-SEC-02 single-field layout."""


def hwm_mac(key: bytes, ts_part: str) -> str:
    """HMAC-SHA256 (hex) over the canonical ``"<hwm_ts>[:<sync_ts>]"`` text."""
    return hmac.new(key, ts_part.encode("utf-8"), hashlib.sha256).hexdigest()


def canonical_ts_part(hwm_ts: int, sync_ts: int) -> str:
    """The canonical two-field signed text (shared by writer and reader)."""
    return f"{int(hwm_ts)}:{int(sync_ts)}"


def format_hwm_text(hwm_ts: int, sync_ts: int, key: bytes) -> str:
    """Render the canonical HWM file body ``"<hwm_ts>:<sync_ts>.<mac>"``."""
    ts_part = canonical_ts_part(hwm_ts, sync_ts)
    return f"{ts_part}.{hwm_mac(key, ts_part)}"


def parse_hwm_text(text: str, keys: list[bytes]) -> HwmRecord:
    """Parse (and integrity-check) an HWM file body.

    Raises:
        LicenseError(code="HWM_CORRUPT"): the body is unreadable/shapeless.
        LicenseError(code="HWM_MAC_MISMATCH"): the shape is right but no
            supplied key reproduces the tag — the caller decides between
            quarantine (lost vault key) and tamper handling.
    """
    content = (text or "").strip()
    if not content or "." not in content:
        raise LicenseError("Corrupted HWM file (missing HMAC)", code="HWM_CORRUPT")
    ts_part, mac_str = content.rsplit(".", 1)
    if len(mac_str) != _MAC_HEX_LEN or not ts_part:
        raise LicenseError("Corrupted HWM file (invalid format)", code="HWM_CORRUPT")

    if ":" in ts_part:
        hwm_str, sync_str = ts_part.split(":", 1)
        if not (hwm_str.isdigit() and sync_str.isdigit()):
            raise LicenseError("Corrupted HWM file (invalid format)", code="HWM_CORRUPT")
        sync_ts: int | None = int(sync_str)
        legacy = False
    else:
        if not ts_part.isdigit():
            raise LicenseError("Corrupted HWM file (invalid format)", code="HWM_CORRUPT")
        sync_ts = None
        legacy = True

    if not any(hmac.compare_digest(mac_str, hwm_mac(key, ts_part)) for key in keys):
        raise LicenseError("HWM integrity check failed", code="HWM_MAC_MISMATCH")

    return HwmRecord(hwm_ts=int(ts_part.split(":", 1)[0]), sync_ts=sync_ts, legacy=legacy)


def read_hwm_file(path: Path, keys: list[bytes]) -> HwmRecord:
    """Read + parse an HWM file, converting I/O failures into HWM_CORRUPT."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LicenseError("Corrupted HWM file (unreadable)", code="HWM_CORRUPT", cause=exc) from exc
    return parse_hwm_text(text, keys)


def seal_hwm(path: Path, hwm_ts: int, sync_ts: int, key: bytes) -> None:
    """Atomically persist the canonical HWM file.

    SEC-16 (Batch B): the temp file is created with owner-only permissions
    (``O_WRONLY|O_CREAT|O_EXCL`` + ``0o600``) and ``os.fsync``-ed BEFORE the
    atomic ``os.replace``. ``write_text`` gave the file the process umask
    (typically 0o644) and never forced the bytes to stable storage, so a crash
    could leave a rename that survived while its contents did not.

    Every failure raises ``LicenseError(code="HWM_PERSIST_FAILED")`` — the HWM
    is the anti-rollback anchor, so an unpersisted anchor must never be
    treated as persisted (SEC-04).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # L-14 (P3-5): unique temp name — a fixed ".tmp" suffix let concurrent
    # writers interleave partial files before either rename.
    tmp_path = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}-{time.monotonic_ns()}")
    body = format_hwm_text(hwm_ts, sync_ts, key)
    fd: int | None = None
    try:
        # O_EXCL: never silently adopt/overwrite an existing file at this name.
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None  # now owned (and closed) by the file object
            handle.write(body)
            handle.flush()
            # Durability: without fsync a crash can leave the rename visible
            # but the data empty/partial on some filesystems.
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        if fd is not None:  # pragma: no cover - os.fdopen failed
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best-effort cleanup
            pass
        logger.error("Failed to persist license HWM — fail-closed", extra={"error": str(exc)})
        raise LicenseError(
            f"Failed to persist license high-water mark (fail-closed): {exc}",
            code="HWM_PERSIST_FAILED",
            cause=exc,
        ) from exc


__all__ = [
    "HWM_INITIALIZED_MARKER",
    "HWM_SECRET_NAME",
    "LEGACY_HWM_SECRET_NAMES",
    "HwmRecord",
    "canonical_ts_part",
    "format_hwm_text",
    "hwm_mac",
    "parse_hwm_text",
    "read_hwm_file",
    "seal_hwm",
]

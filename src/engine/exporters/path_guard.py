"""Shared export-path confinement (F7, REVIEW Aşama 6).

All exporters previously re-implemented the same `_resolve_export_path` helper,
and each copy carried the same weakness: when the caller omitted
``exports_root`` the fallback root became ``Path.cwd() / "exports"`` **and the
confinement check was additionally waived for anything under the system temp
directory**. That waiver is a real escape primitive — an attacker who controls
the export filename (the renderer / a bridge argument) can point the export at
``%TEMP%`` and write outside the intended tree, and the "fallback" behaviour
silently differed from the strict, explicit-root behaviour.

This module makes confinement uniform and fail-closed:

* the root must be given EXPLICITLY by production callers; and
* the resolved target must live inside it — no temp exemption, ever.

The one legitimate need the old temp waiver served (unit tests writing to
``tmp_path``) is met properly: tests pass their temp directory as the root.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Filename length ceiling; NTFS allows 255 chars, leave room for an extension.
MAX_EXPORT_FILENAME_CHARS: int = 200


def resolve_export_path(
    output_file: str | Path,
    exports_root: str | Path | None,
    *,
    allow_cwd_fallback: bool = False,
) -> Path:
    """Resolve and confine an export target. Raises ValueError on escape.

    F7: `exports_root=None` is REFUSED by default. A caller that wants the
    historical convenience fallback must opt in explicitly with
    ``allow_cwd_fallback=True`` — and even then the result is confined to the
    (uncertain, but at least named) ``cwd/exports`` root. There is no temp-dir
    exemption: confinement is all-or-nothing.
    """
    if exports_root is None:
        if not allow_cwd_fallback:
            raise ValueError(
                "exports_root is required; refusing to write to an unspecified location (fail-closed)"
            )
        root = (Path.cwd() / "exports").resolve()
    else:
        root = Path(exports_root).resolve()

    candidate = Path(output_file)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        # A relative name is interpreted as a basename INSIDE the root, so a
        # caller cannot escape with "../../etc/passwd" either.
        #
        # `Path.parts` splits on the PLATFORM separator only, so on POSIX a
        # Windows-style name such as `..\\evil.mat` arrives as a single part
        # and slips past a parts-based check — while a Windows consumer of the
        # same export directory would read it as a traversal. Reject both
        # separators explicitly, and additionally refuse any name that still
        # contains a separator after normalisation.
        name = str(candidate)
        if (
            any(part in ("..", "") for part in candidate.parts)
            or candidate.name != name
            or "\\" in name
            or "/" in name
            or name in (".", "..")
        ):
            raise ValueError(f"Export filename must be a bare name, got {output_file!r}")
        resolved = (root / candidate.name).resolve()

    if not resolved.name or len(resolved.name) > MAX_EXPORT_FILENAME_CHARS:
        raise ValueError(f"Export filename is empty or too long: {resolved.name!r}")

    try:
        inside = resolved.is_relative_to(root)
    except (OSError, ValueError) as exc:  # pragma: no cover - platform dependent
        raise ValueError(f"Export path validation failed: {exc}") from exc
    if not inside:
        raise ValueError(f"Export path escapes exports root: {resolved}")

    # A symlinked parent would let the file land outside the root even though
    # the lexical check passed, so verify the real parent too.
    try:
        real_parent = resolved.parent.resolve()
        real_root = root.resolve()
        if not real_parent.is_relative_to(real_root):
            raise ValueError(f"Export path escapes exports root via symlink: {resolved}")
    except OSError:
        pass

    return resolved


# ---------------------------------------------------------------------------
# Atomic export writes (REVIEW Aşama 6, residual item #2)
# ---------------------------------------------------------------------------
#
# Exporters used to write straight to the destination path. A crash, a full
# disk, or a library error part-way through therefore left a TRUNCATED file
# that looks legitimate — a diagnostic artefact an engineer may then trust.
#
# The fix is the standard write-to-temp-then-rename dance. The rename is atomic
# on both NTFS and POSIX filesystems, so a reader only ever observes the OLD
# file or the COMPLETE new one, never a half-written one.

#: Suffix for the in-progress temp file. Kept next to the target so the final
#: rename stays on the same filesystem (a cross-device rename is not atomic).
_TMP_SUFFIX: str = ".part"


def _atomic_replace(tmp_path: Path, final_path: Path) -> None:
    """Rename ``tmp_path`` onto ``final_path``.

    Cleanup on failure is the CALLER's responsibility (see ``_publish``): doing
    it here would be defeated by a test double or any subclass that raises
    before reaching the unlink.
    """
    tmp_path.replace(final_path)


def _publish(tmp_path: Path, final_path: Path) -> None:
    """Atomically publish ``tmp_path`` as ``final_path``, never leaking scratch.

    On ANY failure the in-progress file is removed, so a failed export cannot
    leave a stray ``*.part`` beside the operator's real artefact.
    """
    try:
        _atomic_replace(tmp_path, final_path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort cleanup
            pass
        raise


def atomic_write_text(final_path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``final_path`` atomically.

    Returns the destination path. The destination is only ever observed as the
    previous contents or the complete new contents.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_name(final_path.name + _TMP_SUFFIX)
    try:
        with open(tmp_path, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            # Durability: get the bytes onto the platter before the rename, so a
            # power loss cannot leave a renamed-but-empty file.
            os.fsync(handle.fileno())
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort cleanup
            pass
        raise
    _publish(tmp_path, final_path)
    return final_path


def atomic_write_bytes(final_path: Path, data: bytes) -> Path:
    """Byte-oriented counterpart of :func:`atomic_write_text`."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_name(final_path.name + _TMP_SUFFIX)
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort cleanup
            pass
        raise
    _publish(tmp_path, final_path)
    return final_path


def atomic_producer_path(final_path: Path) -> tuple[Path, Path]:
    """Return ``(scratch_path, final_path)`` for a library that writes the file itself.

    Some writers (``scipy.io.savemat``, ``asammdf.MDF.save``) take a path and do
    their own ``open()``, so we cannot feed them a handle. Hand them the scratch
    path instead, then call :func:`commit_producer_path` to publish it.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    return final_path.with_name(final_path.name + _TMP_SUFFIX), final_path


def commit_producer_path(scratch_path: Path, final_path: Path) -> Path:
    """Publish the file a third-party writer produced at ``scratch_path``."""
    if not scratch_path.exists():
        raise FileNotFoundError(f"Exporter did not produce {scratch_path}")
    _publish(scratch_path, final_path)
    return final_path

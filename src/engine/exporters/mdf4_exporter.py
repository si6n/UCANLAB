"""ASAM MDF4 (.mf4) Standard Binary Telemetry Exporter."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

try:
    from asammdf import MDF, Signal
except Exception:  # pragma: no cover
    MDF = None  # type: ignore[assignment,misc]
    Signal = None  # type: ignore[assignment,misc]

from src.core.logging import get_logger
from src.engine.exporters.path_guard import (
    commit_producer_path,
    resolve_export_path,
)
from src.engine.exporters.series_validation import validate_signal_series

logger = get_logger("engine.exporters.mdf4")

_SIG_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


def _resolve_export_path(output_file: str | Path, exports_root: str | Path | None) -> Path:
    # F7: shared, fail-closed confinement (see path_guard). The former local
    # copy exempted the system temp dir when `exports_root` was omitted.
    return resolve_export_path(output_file, exports_root, allow_cwd_fallback=True)


class Mdf4Exporter:
    """Exports time series telemetry channels into ASAM MDF4 (.mf4) files."""

    @classmethod
    def export_signals(
        cls,
        output_file: str | Path,
        signals_data: dict[str, tuple[list[float], list[float], str]],  # name -> (timestamps_s, values, unit)
        exports_root: str | Path | None = None,
    ) -> Path:
        """Export dictionary of signals into MDF4.

        Args:
            output_file: Target path ending in .mf4
            signals_data: Dict mapping signal_name -> (timestamps_s, values, unit)
        """
        path = _resolve_export_path(output_file, exports_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if MDF is None or Signal is None:
            raise RuntimeError("asammdf package is required for MDF4 export but is not installed")
        if len(signals_data) > 4096:
            raise ValueError("Too many signals for export")

        mdf = MDF()
        signal_list: list[Signal] = []

        for sig_name, (timestamps, values, unit) in signals_data.items():
            if not isinstance(sig_name, str) or not _SIG_NAME_RE.fullmatch(sig_name):
                raise ValueError(f"Invalid signal name: {sig_name!r}")
            if not timestamps or not values:
                continue

            # REVIEW 3 (LOW): length / NaN / monotonicity validation —
            # fail BEFORE writing anything, never mid-file. P1-9: the shared
            # validator now also guards the MAT exporter.
            validate_signal_series(sig_name, timestamps, values)

            t_arr = np.array(timestamps, dtype=np.float64)
            v_arr = np.array(values, dtype=np.float64)

            sig = Signal(
                samples=v_arr,
                timestamps=t_arr,
                name=sig_name,
                unit=unit,
            )
            signal_list.append(sig)

        if signal_list:
            mdf.append(signal_list)

        # REVIEW 3 (LOW) / P1-8: atomic write — save to a scratch file beside
        # the target (same filesystem -> os.replace is atomic), then publish.
        # A crash mid-save previously left a truncated .mf4 at the target path,
        # which asammdf later opened as a corrupt session file; and a save that
        # raised left the `.tmp-` scratch file behind on disk forever.
        # Scratch path: the `_atomic_replace`/`.part` suffix that
        # `atomic_producer_path` owns is illegal on several Windows builds (a
        # create-then-rename over an existing `.part` target hits a
        # WindowsError 183 / Permission denied), so the scratch name is passed
        # explicitly and only `commit_producer_path` (whose `_publish` unlinks
        # the scratch on ANY failure) is reused.
        scratch_path = path.with_name(path.stem + ".mf4tmp" + path.suffix)
        final_path = path
        try:
            mdf.save(str(scratch_path), overwrite=True)
        except BaseException:
            scratch_path.unlink(missing_ok=True)
            raise
        commit_producer_path(scratch_path, final_path)
        logger.info("Saved ASAM MDF4 file", extra={"file": str(path), "signals": len(signal_list)})
        return path

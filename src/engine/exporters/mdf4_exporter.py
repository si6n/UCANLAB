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
from src.engine.exporters.path_guard import resolve_export_path

logger = get_logger("engine.exporters.mdf4")

_SIG_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


def _validate_signal_series(sig_name: str, timestamps: list[float], values: list[float]) -> None:
    """REVIEW 3 (LOW): per-signal series validation before export.

    - lengths must match (MDF4 channels are sample-aligned; mismatched
      arrays corrupt the file or raise deep inside asammdf);
    - NaN/Inf samples are rejected — a NaN sample is a pipeline bug, not
      a measurement, and silently writing it poisons the whole file;
    - timestamps must be strictly monotonically increasing (MDF4
      requirement for time master channels; duplicates or backwards
      time corrupt every channel alignment).
    """
    if len(timestamps) != len(values):
        raise ValueError(
            f"Signal {sig_name!r}: timestamps ({len(timestamps)}) and values "
            f"({len(values)}) length mismatch"
        )
    for i, t in enumerate(timestamps):
        if not (t == t) or t in (float("inf"), float("-inf")):  # NaN or Inf
            raise ValueError(f"Signal {sig_name!r}: non-finite timestamp at index {i}")
    for i, v in enumerate(values):
        if not (v == v) or v in (float("inf"), float("-inf")):  # NaN or Inf
            raise ValueError(f"Signal {sig_name!r}: non-finite value at index {i}")
    for i in range(1, len(timestamps)):
        if timestamps[i] <= timestamps[i - 1]:
            raise ValueError(
                f"Signal {sig_name!r}: timestamps not strictly increasing at index {i} "
                f"({timestamps[i - 1]} -> {timestamps[i]})"
            )


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
            # fail BEFORE writing anything, never mid-file.
            _validate_signal_series(sig_name, timestamps, values)

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

        # REVIEW 3 (LOW): atomic write — save to a temp file in the SAME
        # directory (same filesystem -> os.replace is atomic), then swap.
        # A crash mid-save previously left a truncated .mf4 at the target
        # path, which asammdf later opened as a corrupt session file.
        # NOTE: asammdf MDF.save() rewrites non-.mf4 extensions to .mf4,
        # so the temp file must itself end in .mf4.
        tmp_path = path.with_name(path.stem + ".tmp-" + path.suffix)
        mdf.save(str(tmp_path), overwrite=True)
        tmp_path.replace(path)
        logger.info("Saved ASAM MDF4 file", extra={"file": str(path), "signals": len(signal_list)})
        return path

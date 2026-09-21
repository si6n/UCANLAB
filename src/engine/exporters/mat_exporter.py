"""MATLAB (.mat) Telemetry Exporter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

try:
    from scipy.io import savemat  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    savemat = None  # type: ignore[assignment]

from src.core.logging import get_logger
from src.engine.exporters.path_guard import (
    atomic_producer_path,
    commit_producer_path,
    resolve_export_path,
)

logger = get_logger("engine.exporters.mat")

_SIG_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


def _resolve_export_path(output_file: str | Path, exports_root: str | Path | None) -> Path:
    # F7: shared, fail-closed confinement (see path_guard). The former local
    # copy exempted the system temp dir when `exports_root` was omitted.
    return resolve_export_path(output_file, exports_root, allow_cwd_fallback=True)


class MatExporter:
    """Exports time series telemetry channels into MATLAB .mat files."""

    @classmethod
    def export_signals(
        cls,
        output_file: str | Path,
        signals_data: dict[str, tuple[list[float], list[float], str]],  # name -> (timestamps_s, values, unit)
        exports_root: str | Path | None = None,
    ) -> Path:
        """Export dictionary of signals into MATLAB .mat format."""
        path = _resolve_export_path(output_file, exports_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if savemat is None:
            raise RuntimeError("scipy package is required for MATLAB export but is not installed")

        mat_dict: dict[str, Any] = {}

        if len(signals_data) > 4096:
            raise ValueError("Too many signals for export")
        for sig_name, (timestamps, values, unit) in signals_data.items():
            if not isinstance(sig_name, str) or not _SIG_NAME_RE.fullmatch(sig_name):
                raise ValueError(f"Invalid signal name: {sig_name!r}")
            clean_name = sig_name
            mat_dict[f"{clean_name}_time"] = np.array(timestamps, dtype=np.float64)
            mat_dict[f"{clean_name}_val"] = np.array(values, dtype=np.float64)
            mat_dict[f"{clean_name}_unit"] = unit

        # Residual item #2: scipy.io.savemat() opens the path itself, so give it
        # a scratch path and publish it atomically on success. A failed or
        # interrupted export must not leave a truncated .mat that looks valid.
        scratch_path, final_path = atomic_producer_path(path)
        try:
            savemat(str(scratch_path), mat_dict)
        except Exception:
            scratch_path.unlink(missing_ok=True)
            raise
        commit_producer_path(scratch_path, final_path)
        logger.info("Saved MATLAB .mat file", extra={"file": str(path), "signals": len(signals_data)})
        return path

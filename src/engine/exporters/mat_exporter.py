"""MATLAB (.mat) Telemetry Exporter."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import savemat  # type: ignore[import-untyped]

from src.core.logging import get_logger

logger = get_logger("engine.exporters.mat")

_SIG_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


def _resolve_export_path(output_file: str | Path, exports_root: str | Path | None) -> Path:
    explicit = exports_root is not None
    root = Path(exports_root) if explicit else (Path.cwd() / "exports")
    resolved = Path(output_file).resolve()
    try:
        is_inside = resolved.is_relative_to(root.resolve())
    except Exception as exc:
        raise ValueError(f"Export path validation failed: {exc}") from exc
    if not is_inside:
        if not explicit:
            try:
                if resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
                    return resolved
            except Exception:
                pass
        raise ValueError(f"Export path escapes exports root: {resolved}")
    return resolved


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

        savemat(str(path), mat_dict)
        logger.info("Saved MATLAB .mat file", extra={"file": str(path), "signals": len(signals_data)})
        return path

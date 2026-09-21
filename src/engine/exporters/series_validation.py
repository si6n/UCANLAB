"""Shared per-signal series validation for telemetry exporters (P1-9).

Moved out of ``mdf4_exporter`` because it was the ONLY exporter validating its
inputs: ``MatExporter`` wrote ``np.array(timestamps)`` / ``np.array(values)``
straight into the MATLAB dict, so a NaN/Inf sample or a non-monotonic time
master was persisted silently. A NaN in a time master is a pipeline bug, not a
measurement (AGENTS.md §2.3 — never fabricate/normalise silently), and a
non-monotonic channel corrupts every sample alignment in the artefact an
engineer will later trust.

Both exporters now call :func:`validate_signal_series` BEFORE writing anything,
so a bad series fails fast instead of producing a half-valid file.
"""

from __future__ import annotations

from collections.abc import Sequence


def validate_signal_series(
    sig_name: str, timestamps: Sequence[float], values: Sequence[float]
) -> None:
    """Validate one (timestamps, values) series before it is exported.

    Raises ``ValueError`` when:

    - the two sequences have different lengths (channels are sample-aligned;
      a mismatch corrupts the file or raises deep inside the writer);
    - any timestamp or value is NaN/Inf;
    - the timestamps are not STRICTLY increasing (duplicates or backwards
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


__all__ = ["validate_signal_series"]

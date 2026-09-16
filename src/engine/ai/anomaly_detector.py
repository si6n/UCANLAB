"""Threshold-DB driven generic anomaly detection (FAZ 3, U2).

Deterministic range-violation scan over session evidence. Thresholds load
from ``data/diagnostics/telemetry_thresholds.json`` — every entry carries a
``source_ref`` back to the KB/scenario record it was lifted from; the agent
invents no values (AGENTS.md §2.3). Signals without a threshold entry are
passed over silently (data may exist, honest "no finding" — never a
fabricated anomaly).

``synthetic`` marks operator-declared chat measurements (FAZ 5): they are
displayed evidence, but their evidence weight is halved in hypothesis
scoring (plan §FAZ 3.2 / §Riskler).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.models.diagnostics import VehicleSession

# Ratio-based anomaly threshold: a signal is anomalous when at least this
# share of its samples falls outside the nominal range (plan §FAZ 3.2).
DEFAULT_OUT_OF_RANGE_RATIO: float = 0.20


class ThresholdDatabaseError(ValueError):
    """Raised when the threshold JSON violates the schema (fail-closed)."""


@dataclass(slots=True, frozen=True)
class AnomalyFinding:
    """One deterministic out-of-range finding for a signal."""

    signal: str
    finding: str
    ratio: float
    evidence_sample_count: int
    synthetic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "finding": self.finding,
            "ratio": round(self.ratio, 3),
            "evidence_sample_count": self.evidence_sample_count,
            "synthetic": self.synthetic,
        }


def _resolve_thresholds_path() -> Path:
    if getattr(sys, "frozen", False):
        frozen = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve() / "data" / "diagnostics" / "telemetry_thresholds.json"
        if frozen.is_file():
            return frozen
    return Path(__file__).resolve().parents[3] / "data" / "diagnostics" / "telemetry_thresholds.json"


def _validate_threshold_payload(payload: dict[str, Any]) -> None:
    """Fail-closed shape check mirroring golden_cases.py (unknown field rejection)."""
    allowed_top = {"schema_version", "_source_rule", "signals"}
    extra = set(payload) - allowed_top
    if extra:
        raise ThresholdDatabaseError(f"unknown top-level field(s): {sorted(extra)}")
    if payload.get("schema_version") != 1:
        raise ThresholdDatabaseError(f"unsupported schema_version: {payload.get('schema_version')!r}")
    signals = payload.get("signals")
    if not isinstance(signals, dict):
        raise ThresholdDatabaseError("signals must be an object")

    for name, entry in signals.items():
        if not isinstance(entry, dict):
            raise ThresholdDatabaseError(f"signals.{name} must be an object")
        extra_s = set(entry) - {"unit", "ranges", "source_ref"}
        if extra_s:
            raise ThresholdDatabaseError(f"signals.{name} unknown field(s): {sorted(extra_s)}")
        if not str(entry.get("source_ref", "")).strip():
            raise ThresholdDatabaseError(f"signals.{name}.source_ref is mandatory (no fabricated thresholds)")
        ranges = entry.get("ranges")
        if not isinstance(ranges, list) or not ranges:
            raise ThresholdDatabaseError(f"signals.{name}.ranges must be a non-empty array")
        for i, r in enumerate(ranges):
            extra_r = set(r) - {"min_rpm", "max_rpm", "min", "max"}
            if extra_r:
                raise ThresholdDatabaseError(f"signals.{name}.ranges[{i}] unknown field(s): {sorted(extra_r)}")
            if not isinstance(r.get("min"), (int, float)) and not isinstance(r.get("max"), (int, float)):
                raise ThresholdDatabaseError(f"signals.{name}.ranges[{i}] needs at least one of min/max")
            for key in ("min", "max", "min_rpm", "max_rpm"):
                val = r.get(key)
                if val is not None and not isinstance(val, (int, float)):
                    raise ThresholdDatabaseError(f"signals.{name}.ranges[{i}].{key} must be numeric or null")


def load_thresholds(data_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load + validate the telemetry threshold DB (fail-closed)."""
    target = data_path or _resolve_thresholds_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThresholdDatabaseError(f"cannot read/parse {target.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ThresholdDatabaseError("threshold payload must be a JSON object")
    _validate_threshold_payload(payload)
    signals: dict[str, dict[str, Any]] = payload["signals"]
    return signals


def _band_for(ranges: list[dict[str, Any]], rpm: float | None) -> dict[str, Any] | None:
    """Select the range band active at the given RPM (first match, deterministic)."""
    if rpm is None:
        # No RPM reference recorded: fall back to the first band (documented
        # simplification — bands are only meaningful with engine-speed
        # context; ponytail: rpm-join per sample is the upgrade path).
        return ranges[0]
    for band in ranges:
        lo = band.get("min_rpm")
        hi = band.get("max_rpm")
        if (lo is None or rpm >= lo) and (hi is None or rpm <= hi):
            return band
    return None


def detect_anomalies(
    session: VehicleSession,
    thresholds: dict[str, dict[str, Any]],
) -> list[AnomalyFinding]:
    """Deterministic range-violation scan over recorded samples.

    - Signals without a threshold entry produce NO finding (silent pass —
      "veri yok" honesty; never a fabricated nominal/anomaly).
    - Out-of-range share >= 20% of samples -> anomaly finding.
    - Operator-declared samples (name starts with "OP:") carry
      ``synthetic=True`` and the finding says "operatör beyanı".
    """
    per_signal: dict[str, list[Any]] = {}
    for sample in session.samples:
        per_signal.setdefault(sample.name, []).append(sample)

    # RPM reference: engine speed samples provide the band context.
    rpm_samples = [s.physical_value for s in session.samples if _normalize(s.name) == "enginespeed"]
    rpm_ref = rpm_samples[-1] if rpm_samples else None

    findings: list[AnomalyFinding] = []
    for name in sorted(per_signal):
        samples = per_signal[name]
        # T56-B / A3-2: resolve the operator-declared ("OP:") prefix to the
        # base signal name before the threshold lookup. The bridge records
        # operator measurements under the prefixed name
        # (desktop_app.record_operator_measurement), so matching only on the
        # raw name made the whole declaration path dead: no finding, and the
        # FAZ 3.2 half-weight in hypothesis_engine never triggered.
        is_synthetic = name.startswith("OP:")
        base = name[3:] if is_synthetic else name
        entry = thresholds.get(base) or thresholds.get(_camelize(base))
        if entry is None:
            continue
        band = _band_for(entry.get("ranges", []), rpm_ref)
        if band is None:
            continue
        lo, hi = band.get("min"), band.get("max")
        lo = lo if lo is not None else None  # bind for closure below
        out_count = 0
        for s in samples:
            v = s.physical_value
            if lo is not None and v < lo:
                out_count += 1
            elif hi is not None and v > hi:
                out_count += 1
        if not out_count:
            continue
        ratio = out_count / len(samples)
        if ratio < DEFAULT_OUT_OF_RANGE_RATIO:
            continue
        synthetic = is_synthetic
        unit = entry.get("unit", "")
        bounds_txt = []
        if lo is not None:
            bounds_txt.append(f"min {lo:g}")
        if hi is not None:
            bounds_txt.append(f"max {hi:g}")
        finding_txt = (
            f"{out_count}/{len(samples)} örnek nominal aralık dışında ({' / '.join(bounds_txt)} {unit})"
            + (" — operatör beyanı" if synthetic else "")
        )
        findings.append(
            AnomalyFinding(
                signal=name,
                finding=finding_txt,
                ratio=ratio,
                evidence_sample_count=len(samples),
                synthetic=synthetic,
            )
        )
    # Deterministic order: by signal name.
    findings.sort(key=lambda f: f.signal)
    return findings


def _camelize(name: str) -> str:
    """Map normalized signal names back to CamelCase DB keys.

    J1939 evidence records PGN-derived names (e.g. "engine_coolant_temp");
    the threshold DB uses signal names like "EngineCoolantTemp". The map is
    explicit, not a generic algorithm — only the pairs actually recorded.
    """
    mapping = {
        "engine_coolant_temp": "EngineCoolantTemp",
        "coolant_temp": "EngineCoolantTemp",
        "turbo_boost": "TurboBoost",
        "boost_pressure": "TurboBoost",
        "engine_oil_pressure": "EngineOilPressure",
        "oil_pressure": "EngineOilPressure",
    }
    return mapping.get(name, name)


def _normalize(name: str) -> str:
    lowered = name.strip().lower()
    for sep in (" ", "_"):
        lowered = lowered.replace(sep, "-")
    return lowered.strip("-")


__all__ = [
    "AnomalyFinding",
    "ThresholdDatabaseError",
    "load_thresholds",
    "detect_anomalies",
    "DEFAULT_OUT_OF_RANGE_RATIO",
]

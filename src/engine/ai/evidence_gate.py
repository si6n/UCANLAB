"""Data Quality Gate for diagnostic evidence (FAZ 1).

Deterministic, stdlib-only sufficiency evaluation over a ``VehicleSession``
evidence base. Two tiers (plan §FAZ 1):

1. ``anomaly_sufficient`` — sample-count / recency / confidence checks pass:
   the generic anomaly scan may run.
2. ``dtc_sufficient`` — tier 1 PLUS at least one ACTIVE ``DiagnosticEvent``:
   the hypothesis engine may run. A healthy vehicle without DTCs can still
   get an anomaly scan; the report states "aktif DTC kaydı yok" honestly.

The gate never fabricates: insufficient sessions stop the corresponding
analysis tier and surface ``gaps`` verbatim (AGENTS.md §2.3 — no fabricated
telemetry; a missing signal is "veri yok", never zero).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.models.diagnostics import (
    DiagnosticEvent,
    SignalSource,
    VehicleSession,
)

# Thresholds shared with the anomaly threshold DB (plan: "eşik DB ile aynı
# dosyadan, varsayılan 10"). Deliberately conservative defaults; tuning is
# an operator decision, never an agent invention.
MIN_SAMPLES_PER_SIGNAL: int = 10
MIN_RECORDING_SPAN_S: float = 60.0
MAX_SAMPLE_AGE_S: float = 10.0
# DISCOVERED signals with a low confidence share reduce evidence trust.
MIN_MEAN_SAMPLE_CONFIDENCE: float = 0.5


@dataclass(slots=True, frozen=True)
class SufficiencyReport:
    """Frozen outcome of the evidence quality gate."""

    anomaly_sufficient: bool
    dtc_sufficient: bool
    gaps: list[str] = field(default_factory=list)
    signal_inventory: dict[str, int] = field(default_factory=dict)
    active_dtc_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_sufficient": self.anomaly_sufficient,
            "dtc_sufficient": self.dtc_sufficient,
            "gaps": list(self.gaps),
            "signal_inventory": dict(sorted(self.signal_inventory.items())),
            "active_dtc_count": self.active_dtc_count,
        }


def _latest_timestamp_ns(session: VehicleSession) -> int:
    """Latest sample/event monotonic timestamp; 0 for an empty session."""
    stamps = [s.timestamp_ns for s in session.samples] + [e.timestamp_ns for e in session.events]
    return max(stamps) if stamps else 0


def _mean_confidence(samples: list[Any]) -> float:
    if not samples:
        return 0.0
    return float(sum(s.confidence for s in samples) / len(samples))


def evaluate_sufficiency(session: VehicleSession) -> SufficiencyReport:
    """Evaluate the two-tier evidence sufficiency of a session (deterministic).

    Order-independent: gap strings are emitted in a fixed pass order and the
    inventory is sorted at report build, so identical sessions yield
    byte-identical reports (determinism invariant, plan §Mimari Kural).
    """
    gaps: list[str] = []
    inventory: dict[str, int] = {}

    per_signal: dict[str, list[Any]] = {}
    for sample in session.samples:
        per_signal.setdefault(sample.name, []).append(sample)
    for name in sorted(per_signal):
        samples = per_signal[name]
        inventory[name] = len(samples)
        if len(samples) < MIN_SAMPLES_PER_SIGNAL:
            gaps.append(f"sinyal '{name}': yalnız {len(samples)} örnek (min {MIN_SAMPLES_PER_SIGNAL})")

    # Recording span: started_at_ns -> newest record. A session that never
    # recorded anything has zero span and fails tier 1 by construction.
    latest_ns = _latest_timestamp_ns(session)
    span_s = max(0.0, (latest_ns - session.started_at_ns) / 1e9)
    if span_s < MIN_RECORDING_SPAN_S:
        gaps.append(f"kayıt süresi {span_s:.1f} sn (min {MIN_RECORDING_SPAN_S:.0f} sn)")

    # Recency: every inventoried signal must have a fresh last sample. With
    # no clock access (pure function over recorded monotonic stamps) the
    # honest freshness proxy is the session's own latest record: signals
    # lagging behind it by more than MAX_SAMPLE_AGE_S are stale.
    for name in sorted(per_signal):
        last_ns = max(s.timestamp_ns for s in per_signal[name])
        if latest_ns and (latest_ns - last_ns) / 1e9 > MAX_SAMPLE_AGE_S:
            gaps.append(f"sinyal '{name}': son örnek güncel değil (> {MAX_SAMPLE_AGE_S:.0f} sn)")

    # DISCOVERED share & mean sample confidence (auto-discovery trust).
    # Fail-closed boundaries (A3-9): DISCOVERED must stay strictly UNDER 0.5
    # (`>=` flags the exact-50% case) and the mean confidence must be strictly
    # ABOVE the minimum (`<=` flags the exact-boundary case). Both directions
    # reject the boundary rather than admitting low-confidence data.
    discovered = [s for s in session.samples if s.source is SignalSource.DISCOVERED]
    total = len(session.samples)
    if total and len(discovered) / total >= 0.5:
        gaps.append(f"örneklerin çoğunluğu DISCOVERED ({len(discovered)}/{total}) — güven düşük")
    if session.samples:
        mean_conf = _mean_confidence(session.samples)
        if mean_conf <= MIN_MEAN_SAMPLE_CONFIDENCE:
            gaps.append(f"ortalama örnek güveni {mean_conf:.2f} (min {MIN_MEAN_SAMPLE_CONFIDENCE:.2f})")

    anomaly_sufficient = not gaps

    active_dtcs = [e for e in session.events if e.status == "ACTIVE"]
    dtc_sufficient = anomaly_sufficient and bool(active_dtcs)
    if anomaly_sufficient and not active_dtcs:
        # Tier 2 blocked but tier 1 ran: the report must say so honestly.
        gaps.append("aktif DTC kaydı yok — hipotez motoru çalıştırılmadı")

    return SufficiencyReport(
        anomaly_sufficient=anomaly_sufficient,
        dtc_sufficient=dtc_sufficient,
        gaps=gaps,
        signal_inventory=inventory,
        active_dtc_count=len(active_dtcs),
    )


def active_dtc_events(session: VehicleSession) -> list[DiagnosticEvent]:
    """Sorted ACTIVE diagnostic events of a session (deterministic order)."""
    return sorted(
        (e for e in session.events if e.status == "ACTIVE"),
        key=lambda e: (e.timestamp_ns, e.code),
    )

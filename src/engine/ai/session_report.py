"""Automatic technician report over a diagnostic session (FAZ 6).

Markdown report assembled from gate / anomaly / hypothesis / similarity
outputs — all content derives from recorded evidence or KB text. Sim mode
(Bulgu 7): no evidence -> short honest report stating there is no live
data; the hypothesis table is never fabricated. Deterministic except the
cover line (wall-clock, read-only record field per plan §Riskler).
"""

from __future__ import annotations

import time
from typing import Any

from src.core.models.diagnostics import VehicleSession
from src.engine.ai.anomaly_detector import AnomalyFinding
from src.engine.ai.diagnostic_copilot import attach_action_triggers, make_j1939_dm1_action, make_uds_clear_dtc_action
from src.engine.ai.evidence_gate import SufficiencyReport, active_dtc_events
from src.engine.ai.golden_similarity import CaseMatch, similarity_confidence_label
from src.engine.ai.hypothesis_engine import Hypothesis

_SIM_MARKER = "canlı veri kaydı yok — simülasyon/demo modu"


def build_technician_report(
    session: VehicleSession,
    sufficiency: SufficiencyReport,
    anomalies: list[AnomalyFinding],
    hypotheses: list[Hypothesis],
    similar_cases: list[CaseMatch],
    discriminating_tests: list[str] | None = None,
) -> str:
    """Build the Markdown session report (deterministic body, wall-clock cover)."""
    duration_s = max(0.0, _session_span_s(session))
    lines: list[str] = []
    lines.append("# Teşhis Oturum Raporu")
    # Cover: the ONLY wall-clock field (plan §Riskler — read-only record).
    lines.append("")
    lines.append(f"- Oturum: `{session.session_id}` | Domain: {session.domain.value} | VIN: {session.vin_masked or '—'}")
    lines.append(f"- Kayıt süresi: {duration_s:.0f} sn | Rapor üretimi: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Data quality ──
    lines.append("")
    lines.append("## Veri Kalitesi")
    if not session.samples and not session.events:
        lines.append(f"- {_SIM_MARKER}")
        lines.append("- Hipotez tablosu üretilmedi (kanıt yok).")
        return "\n".join(lines) + "\n"
    if sufficiency.anomaly_sufficient:
        lines.append("- Kanıt kalitesi yeterli (anomali analizi çalıştırılabilir).")
    else:
        lines.append("- Yetersiz veri — ilgili analiz kademesi durduruldu:")
        for gap in sufficiency.gaps:
            lines.append(f"  - {gap}")
    if not sufficiency.dtc_sufficient and sufficiency.anomaly_sufficient:
        for gap in sufficiency.gaps:
            lines.append(f"- {gap}")

    # ── Active DTCs ──
    lines.append("")
    lines.append("## Aktif DTC Kayıtları")
    actives = active_dtc_events(session)
    if actives:
        for ev in actives:
            lines.append(f"- `{ev.code}` — şiddet: {ev.severity}")
    else:
        lines.append("- Aktif DTC kaydı yok.")

    # ── Anomaly findings ──
    lines.append("")
    lines.append("## Anomali Bulguları")
    if anomalies:
        for a in anomalies:
            tag = " (operatör beyanı)" if a.synthetic else ""
            lines.append(f"- **{a.signal}**: {a.finding}{tag} [{a.evidence_sample_count} örnek]")
    else:
        lines.append("- Anomali bulgusu yok (eşik DB kapsamındaki sinyaller nominal).")

    # ── Hypotheses ──
    lines.append("")
    lines.append("## Hipotez Sıralaması")
    if hypotheses:
        lines.append("")
        lines.append("| # | Hipotez | Ağırlıklı kanıt skoru | Destekleyen | Çelişen |")
        lines.append("|---|---------|----------------------|-------------|---------|")
        for i, h in enumerate(hypotheses, start=1):
            sup = "; ".join(h.supporting_evidence) or "—"
            con = "; ".join(h.contradicting_evidence) or "—"
            lines.append(f"| {i} | {h.fault} | %{h.score * 100:.0f} | {sup} | {con} |")
        lines.append("")
        lines.append("*Skorlar ağırlıklı kanıt skorudur; kesinlik değildir.*")
    else:
        lines.append("- Hipotez üretilmedi (yetersiz DTC/kanıt).")

    # ── Similar verified cases ──
    lines.append("")
    lines.append("## Benzer Doğrulanmış Vakalar")
    if similar_cases:
        label = similarity_confidence_label(similar_cases)
        for m in similar_cases:
            lines.append(f"- `{m.case_id}` — %{m.similarity * 100:.0f} benzerlik ({label}) — {', '.join(m.reasons) or 'metadata'}")
    else:
        lines.append("- Raporlanmaya değer benzer vaka yok (eşik altı).")

    # ── Discriminating tests ──
    lines.append("")
    lines.append("## Önerilen Ayrıştırıcı Testler")
    if discriminating_tests:
        for t in discriminating_tests:
            lines.append(f"- {t}")
    else:
        lines.append("- Ayrıştırıcı test önerisi yok (hipotez çifti belirsiz değil veya KB ölçüm adımı yok).")

    # ── Action triggers (deterministic make_* generators only) ──
    actions = [make_uds_clear_dtc_action(), make_j1939_dm1_action()] if actives else []
    body = "\n".join(lines) + "\n"
    if actions:
        body = attach_action_triggers(body, actions)
    return body


def _session_span_s(session: VehicleSession) -> float:
    stamps = [s.timestamp_ns for s in session.samples] + [e.timestamp_ns for e in session.events]
    if not stamps:
        return 0.0
    return (max(stamps) - session.started_at_ns) / 1e9


def report_summary_dict(
    sufficiency: SufficiencyReport,
    anomalies: list[AnomalyFinding],
    hypotheses: list[Hypothesis],
    similar_cases: list[CaseMatch],
) -> dict[str, Any]:
    """JSON-friendly analysis summary for the bridge (no TS-side logic)."""
    return {
        "gate": sufficiency.to_dict(),
        "anomalies": [a.to_dict() for a in anomalies],
        "hypotheses": [h.to_dict() for h in hypotheses],
        "similar_cases": [m.to_dict() for m in similar_cases],
        "similarity_label": similarity_confidence_label(similar_cases),
    }


__all__ = ["build_technician_report", "report_summary_dict", "_SIM_MARKER"]

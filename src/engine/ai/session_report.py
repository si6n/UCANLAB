"""Automatic technician report over a diagnostic session (FAZ 6).

Markdown report assembled from gate / anomaly / hypothesis / similarity
outputs — all content derives from recorded evidence or KB text. Sim mode
(Bulgu 7): no evidence -> short honest report stating there is no live
data; the hypothesis table is never fabricated. Deterministic except the
cover line (wall-clock, read-only record field per plan §Riskler).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import time
from dataclasses import replace
from typing import Any

from src.core.models.diagnostics import VehicleSession
from src.engine.ai.anomaly_detector import AnomalyFinding
from src.engine.ai.diagnostic_copilot import attach_action_triggers, make_j1939_dm1_action
from src.engine.ai.evidence_gate import SufficiencyReport, active_dtc_events
from src.engine.ai.golden_similarity import CaseMatch, similarity_confidence_label
from src.engine.ai.hypothesis_engine import Hypothesis

_SIM_MARKER = "canlı veri kaydı yok — simülasyon/demo modu"


def _seal_heading(signing_key: bytes | None) -> str:
    """P2-9: name the seal for what it actually is.

    A keyless SHA-256 digest proves only that the bytes were not corrupted in
    transit — anyone can recompute it, so it is an INTEGRITY CHECKSUM, never a
    cryptographic seal. Only the keyed HMAC branch may be marketed as one.
    """
    if signing_key:
        return "**Rapor Kriptografik Mührü (R2-EN1):**"
    return "**Rapor Bütünlük Sağlaması (anahtarsız, R2-EN1):**"


def _sign_report(raw: str, signing_key: bytes | None) -> tuple[str, str]:
    """R2-EN1: keyed HMAC when a signing key is supplied, else plain SHA-256 checksum."""
    if signing_key:
        digest = _hmac.new(signing_key, raw.encode("utf-8"), hashlib.sha256).hexdigest().upper()
        return digest, "HMAC-SHA256 (keyed, tamper-evident)"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest().upper(), "SHA-256 (unkeyed integrity checksum — not a signature)"


def build_technician_report(
    session: VehicleSession,
    sufficiency: SufficiencyReport,
    anomalies: list[AnomalyFinding],
    hypotheses: list[Hypothesis],
    similar_cases: list[CaseMatch],
    discriminating_tests: list[str] | None = None,
    dialogue_transcript: list[dict[str, Any]] | None = None,
    eliminated_hypotheses: list[Any] | None = None,
    technician_correction: str | None = None,
    signing_key: bytes | None = None,
) -> str:
    """Build the Markdown session report (deterministic body, wall-clock cover, R2-EN1 seal)."""
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
        raw_body = "\n".join(lines) + "\n"
        seal, algo = _sign_report(raw_body, signing_key)
        return raw_body + f"\n---\n{_seal_heading(signing_key)} `{seal}`  \n*Algoritma:* {algo}\n"
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
        # P0-4 (AGENTS.md §2.3): an empty anomaly list is NOT evidence of a
        # healthy vehicle when the anomaly scan never became eligible. The old
        # line asserted "eşik DB kapsamındaki sinyaller nominal" unconditionally,
        # turning "we could not look" into "everything is fine".
        if sufficiency.anomaly_sufficient:
            lines.append(
                "- Anomali bulgusu yok (tarama çalıştırıldı; eşik DB kapsamındaki sinyaller nominal)."
            )
        else:
            lines.append(
                "- Anomali taraması YAPILMADI — yetersiz kanıt; bu oturum için "
                "'nominal/sağlıklı' HÜKMÜ VERİLEMEZ (bkz. Veri Kalitesi eksikleri)."
            )

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
        # T1-1: the table scores are DAMPED by the golden-set factor. Disclose
        # it, otherwise a reader cannot tell a calibrated %79 from a raw,
        # self-normalised %100 and will treat the number as a hard probability.
        lines.append(
            "*Skorlar ağırlıklı kanıt skorudur; kesinlik değildir. "
            "Ham skorlar doğrulanmış golden-set **kalibrasyon faktörü** ile söndürülmüştür "
            "(aşırı-güven denetimi); kanıt defterleri değiştirilmemiştir.*"
        )
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

    # ── Interactive Dialogue & Evidence Chain (FAZ 5) ──
    if dialogue_transcript:
        lines.append("")
        lines.append("## İnteraktif Diyalog Transkripti & Kanıt Zinciri")
        lines.append("| # | Soru | Cevap | Tür | Kanıt / Etki |")
        lines.append("|---|------|-------|-----|--------------|")
        for i, turn in enumerate(dialogue_transcript, start=1):
            q_txt = turn.get("question", "")
            ans_txt = turn.get("answer", "")
            k_txt = turn.get("kind", "")
            link = turn.get("evidence_link", "")
            lines.append(f"| {i} | {q_txt} | {ans_txt} | {k_txt} | {link} |")

    if eliminated_hypotheses:
        lines.append("")
        lines.append("## Elenen Olasılıklar ve Nedenleri")
        for eh in eliminated_hypotheses:
            fault = getattr(eh, "fault", None) or (eh.get("fault") if isinstance(eh, dict) else str(eh))
            reason = getattr(eh, "reason", None) or (eh.get("reason") if isinstance(eh, dict) else "")
            qid = getattr(eh, "eliminated_by_question_id", None) or (eh.get("eliminated_by_question_id") if isinstance(eh, dict) else "")
            qid_str = f" (Soru: {qid})" if qid else ""
            lines.append(f"- **{fault}**: {reason}{qid_str}")

    if technician_correction:
        lines.append("")
        lines.append("## Teknisyen Düzeltme Notu (\"Yanıldım\" Geri Bildirimi)")
        lines.append(f"> {technician_correction.strip()}")
        lines.append("*(Bu geri bildirim cihaz-içi öğrenme havuzuna golden case adayı olarak kaydedilmiştir.)*")

    # ── Action triggers (read-only deterministic generators only) ──
    # P0-3: this report used to attach an automatic ``make_uds_clear_dtc_action()``
    # whenever ACTIVE DTCs existed — a destructive UDS 0x14 write offered on a
    # document alone, with no operator request and no repair confirmation.
    # A technician report is a record, not a command surface: only the
    # READ-ONLY J1939 DM1 query survives.
    actions = [make_j1939_dm1_action()] if actives else []
    body = "\n".join(lines) + "\n"
    if actions:
        body = attach_action_triggers(body, actions)

    # ── Integrity seal / cryptographic seal (R2-EN1) ──
    seal, algo = _sign_report(body, signing_key)
    seal_block = f"\n---\n{_seal_heading(signing_key)} `{seal}`  \n*Algoritma:* {algo}\n"
    return body + seal_block


def calibrated_hypotheses(
    hypotheses: list[Hypothesis],
    calibration_factor: float | None = None,
) -> list[Hypothesis]:
    """Damp hypothesis scores with the golden-set calibration factor (T1-1).

    ``rank_hypotheses`` SELF-NORMALISES its scores to the top candidate, so the
    leader always reads %100 regardless of how weak its absolute evidence is —
    a self-referential number that is not a probability. The golden corpus
    measured that overconfidence, and this function applies the damping the
    live panel/export paths need.

    Only the SCORE is rewritten. ``fault``, ``supporting_evidence``,
    ``contradicting_evidence``, ``discriminating_tests`` and ``expected_dtcs``
    are carried over VERBATIM: the evidence ledger is recorded fact and must
    never be rewritten by a presentation-layer adjustment (AGENTS.md §2.3).

    ``calibration_factor=None`` resolves the cached golden-set factor; a
    resolved ``None`` (corpus unavailable) leaves the scores untouched. Offline,
    deterministic, idempotent for a given factor.
    """
    from src.engine.ai.calibration import calibrate_score

    factor = calibration_factor
    if factor is None:
        try:
            from src.engine.ai.calibration import compute_calibration_factor

            factor = compute_calibration_factor()
        except Exception:  # noqa: BLE001 — calibration must never break the report
            factor = None

    out: list[Hypothesis] = []
    for h in hypotheses:
        damped_score = calibrate_score(h.score, calibration_factor=factor)
        # Only ``score`` is rewritten. The evidence ledgers are passed through
        # untouched — `dataclasses.replace` carries the remaining fields by
        # reference, so the frozen ``tuple`` ledgers keep their exact type and
        # identity. Coercing them to ``list`` here (as an earlier revision did)
        # silently changed the declared type of a frozen field and made
        # ``damped.supporting_evidence == raw.supporting_evidence`` fail by
        # container type alone — a presentation adjustment must not reshape
        # recorded evidence (AGENTS.md §2.3).
        out.append(replace(h, score=damped_score))
    return out


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


__all__ = [
    "build_technician_report",
    "calibrated_hypotheses",
    "report_summary_dict",
    "_seal_heading",
    "_sign_report",
    "_SIM_MARKER",
]

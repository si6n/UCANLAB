"""Deterministic user decision-card composer (doküman §29-30/§46).

``DiagnosticAnalysisReport`` + ``VehicleSession`` (mühendis çıktısı) ->
``UserDiagnosticCard`` (kullanıcı çıktısı). LLM YOK — şablon NLG.
Kanıt zinciri kapalı: kart yalnız mevcut rapor/oturum kanıtlarından
ÜRETİLİR, kanıt toplayamaz. Determinizm: aynı girdi -> aynı kart.

Kart yalnız risk bandı + başlık + özet + kaynak taşır (karar kartı
budama planı 2026-09-12): sürüş önerisi / şimdi yapın / bunu yapmayın /
ustaya not / güven katmanı kaldırıldı.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.core.models.diagnostics import VehicleSession
from src.engine.ai.diagnostic_copilot import (
    DiagnosticAnalysisReport,
    get_j1939_spn_database,
)
from src.engine.ai.drive_safety_policy import (
    EV_HV_WARNING_TR,
    RISK_ADVICE,
    RiskLevel,
    decide_risk,
    is_ev_hv_code,
)
from src.engine.ai.user_kb import UserKbEntry, get_entry, load_user_kb

CARD_VERSION = 1

# Doküman §40 — "kod bulunamadı" dürüst ekran metni birebir.
NO_DTC_HEADLINE_TR = "Kayıtlı arıza kodu bulunamadı"
NO_DTC_SUMMARY_TR = (
    "Bu, araçta hiçbir sorun olmadığı anlamına gelmez. "
    "Bazı sorunlar her taramada kod üretmez."
)

# Doküman §45 Kural 3 — "kesin" kelimesi kartlarda yasak.
_FORBIDDEN_WORD = "kesin"


@dataclass(slots=True, frozen=True)
class UserDiagnosticCard:
    """Teknik olmayan kullanıcının karar kartı (doküman §30/§46)."""

    headline_tr: str
    summary_tr: str
    risk_level: RiskLevel
    risk_advice_tr: str
    evidence_tr: tuple[str, ...]
    technical: dict[str, Any] = field(default_factory=dict)
    source_badges: tuple[str, ...] = ()
    card_version: int = CARD_VERSION

    def card_to_dict(self) -> dict[str, Any]:
        """Bridge payload (doküman §46 JSON şekli)."""
        return {
            "card_version": self.card_version,
            "headline_tr": self.headline_tr,
            "summary_tr": self.summary_tr,
            "risk_level": self.risk_level,
            "risk_advice_tr": self.risk_advice_tr,
            "evidence_tr": list(self.evidence_tr),
            "technical": dict(self.technical),
            "source_badges": list(self.source_badges),
        }


def _session_dtc_codes(session: VehicleSession) -> list[str]:
    """Aktif DTC kodları, evrensel büyük/küçük normalizasyonlu sıra ile."""
    return [e.code for e in sorted((e for e in session.events if e.status == "ACTIVE"), key=lambda e: (e.timestamp_ns, e.code))]


def _kb_code_variants(code: str) -> list[str]:
    """"SPN 100 FMI 1" -> ["SPN 100 FMI 1", "SPN100"]; birebir KB anahtarı denemeleri."""
    cleaned = " ".join((code or "").strip().upper().split())
    variants = [cleaned]
    if cleaned.startswith("SPN"):
        parts = cleaned.split()
        if len(parts) >= 2:
            variants.append(f"SPN{parts[1]}")
    return variants


def _extract_spn_number(code: str) -> int | None:
    """Extract numeric SPN ID from code variants like 'SPN 629 FMI 12', 'SPN629', 'SPN_629'."""
    cleaned = (code or "").strip().upper()
    m = re.search(r"\bSPN[_\s-]*([0-9]+)\b", cleaned)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def compose_user_card(
    report: DiagnosticAnalysisReport,
    session: VehicleSession,
    user_kb: dict[str, UserKbEntry] | None = None,
    is_simulating: bool = False,
) -> UserDiagnosticCard:
    """Deterministic user decision card from the engineering outputs.

    - Boş oturum/kanıt -> GRAY + doküman §40 dürüst kart (asla GREEN değil).
    - EV/HV kod -> RED zorlaması + §24 uyarısı.
    - KB'de olmayan kod -> J1939 SPN veritabanı veya genel dürüst şablon (uydurma yok).
    """
    kb = user_kb if user_kb is not None else load_user_kb()
    dtc_codes = _session_dtc_codes(session)

    # ── Empty session: honest "no code found" card (doküman §40) ──
    if not dtc_codes and report.raw_dtc_count == 0:
        sim_note = " (canlı veri kaydı yok — simülasyon/demo)" if is_simulating else ""
        return UserDiagnosticCard(
            headline_tr=NO_DTC_HEADLINE_TR,
            summary_tr=NO_DTC_SUMMARY_TR + sim_note,
            risk_level="GRAY",
            risk_advice_tr=RISK_ADVICE["GRAY"],
            evidence_tr=("Kayıtlı arıza kodu bulunamadı.",),
            technical={"dtcs": [], "severity": report.severity.value, "subsystem": "", "confidence_score": None},
            source_badges=("Kural tabanlı çevrimdışı motor",),
        )

    # ── Risk: severity + EV/HV zorlaması (fail-safe, monotonic) ──
    risk = decide_risk(report.severity.value, dtc_codes)

    # ── KB lookup: ilk eşleşen aktif kod ──
    entry: UserKbEntry | None = None
    for code in dtc_codes:
        for variant in _kb_code_variants(code):
            entry = get_entry(variant, kb)
            if entry is not None:
                break
        if entry is not None:
            break

    # ── J1939 SPN DB lookup: KB'de olmayan SPN kodları için köprü (Boşluk 10) ──
    spn_info: dict[str, Any] | None = None
    if entry is None:
        try:
            j1939_db = get_j1939_spn_database()
            spns = j1939_db.get("spns", {}) if isinstance(j1939_db, dict) else {}
            for code in dtc_codes:
                spn_num = _extract_spn_number(code)
                if spn_num is not None and f"SPN_{spn_num}" in spns:
                    spn_info = spns[f"SPN_{spn_num}"]
                    break
        except Exception:
            spn_info = None

    ev_codes = [c for c in dtc_codes if is_ev_hv_code(c)]
    if ev_codes:
        risk = "RED"

    # ── Texts: KB kaydı varsa KB, yoksa J1939 DB, yoksa dürüst genel şablon ──
    if entry is not None:
        headline = entry.user_title_tr
        summary = entry.user_summary_tr
        badges: tuple[str, ...] = ("Yerel bilgi tabanı",)
    elif spn_info is not None:
        title_tr = (spn_info.get("title_tr") or spn_info.get("name") or "J1939 SPN Arızası").strip()
        subsystem = (spn_info.get("subsystem") or "Ağır Vasıta J1939").strip()

        if title_tr.lower().endswith("olabilir"):
            headline = title_tr
        elif title_tr.lower().endswith(("arızası", "hatası", "sorunu", "uyarısı")):
            headline = f"{title_tr} olabilir"
        else:
            headline = f"{title_tr} sorunu olabilir"

        summary = f"Ağır vasıta {subsystem} sisteminde ({title_tr}) aktif durum kaydedildi."
        if len(dtc_codes) > 1:
            summary += f" Araçta toplam {len(dtc_codes)} adet aktif hata kaydı bulunmaktadır."
        badges = ("J1939 SPN veritabanı",)
    else:
        headline = "Arıza kaydedildi"
        summary = (
            f"Araçta {len(dtc_codes)} adet aktif hata kaydı tespit edildi. "
            "Bu kod için basitleştirilmiş açıklama mevcut değil; teknik detaylar aşağıdadır."
        )
        badges = ("Kural tabanlı çevrimdışı motor",)

    # Öneri satırı mühendis raporu severity'sinden türetilir (§6 sabit şablon).
    advice = RISK_ADVICE[risk]

    # EV/HV: §24 uyarı metni summary'ye eklenir (doküman §24 birebir).
    if ev_codes:
        summary = f"{summary}\n{EV_HV_WARNING_TR}"

    # ── Evidence: yalnız mevcut rapor kanıtları ──
    evidence: list[str] = [f"Aktif hata kodu sayısı: {len(dtc_codes)}"]
    if report.telemetry_correlations:
        evidence.extend(report.telemetry_correlations[:2])
    else:
        evidence.append("Canlı telemetri korelasyonu sınırlı")

    technical: dict[str, Any] = {
        "dtcs": dtc_codes[:10],
        "severity": report.severity.value,
        "subsystem": "; ".join(report.affected_subsystems[:3]),
        "confidence_score": None,
    }

    return UserDiagnosticCard(
        headline_tr=headline,
        summary_tr=summary,
        risk_level=risk,
        risk_advice_tr=advice,
        evidence_tr=tuple(evidence),
        technical=technical,
        source_badges=badges,
    )


def is_honest_card(card: UserDiagnosticCard) -> bool:
    """§45 Kural 3: kullanıcıya görünen metinlerde 'kesin' kelimesi yasak."""
    texts = " ".join([card.headline_tr, card.summary_tr, card.risk_advice_tr]).lower()
    return _FORBIDDEN_WORD not in texts


__all__ = [
    "UserDiagnosticCard",
    "compose_user_card",
    "is_honest_card",
    "CARD_VERSION",
    "NO_DTC_HEADLINE_TR",
    "NO_DTC_SUMMARY_TR",
]

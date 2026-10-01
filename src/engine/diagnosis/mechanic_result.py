"""Mechanic result card and customer report (Aşama 6, MECHANIC_FLOW.md §3.12-3.13).

Turns the existing engineering analysis (``_analyze_session`` output: user
decision card + copilot report + evidence gate) and the per-code knowledge
records into the fixed card order the mechanic sees:

1. safety warning (first line)   5. what to do, simple → hard
2. one-sentence summary          6. missing data and how to get it
3. urgency                       7. technical details (collapsed)
4. likely causes with reasons    8. source records

Honesty rules (G5): every sentence comes from a knowledge record, the
analysis, or a fixed template. No measurement or value is invented; an
unknown code gets the "we won't guess" line. The word "kesin" never appears
in text shown to the mechanic or the customer (§45 Kural 3).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.core.logging import get_logger

logger = get_logger("engine.diagnosis.mechanic_result")

URGENCY = {
    "RED": ("Hemen", "Now"),
    "YELLOW": ("Bu hafta", "This week"),
    "GREEN": ("Bir sonraki bakımda", "Next service"),
    "GRAY": ("Bilinmiyor", "Unknown"),
}

# Plain explanations for terms that show up in knowledge-base texts.
GLOSSARY: dict[str, str] = {
    "ECU": "Aracın bir bölümünü yöneten bilgisayar (kontrol ünitesi).",
    "DTC": "Arıza kodu: kontrol ünitesinin kaydettiği hata numarası.",
    "SPN": "Ağır vasıtalarda arızalı parçayı/ölçümü gösteren numara.",
    "FMI": "Arızanın türü (çok yüksek, çok düşük, kablo kopuk gibi).",
    "DPF": "Dizel partikül filtresi: egzozdaki isi tutan filtre.",
    "EGR": "Egzoz gazının bir kısmını motora geri gönderen sistem.",
    "SCR": "AdBlue ile egzozdaki zararlı gazı azaltan sistem.",
    "AdBlue": "Egzoz temizliği için kullanılan üre sıvısı (DEF).",
    "Katalizör": "Egzoz gazını temizleyen, egzoz hattındaki parça.",
    "Oksijen sensörü": "Egzozdaki oksijeni ölçerek yakıt ayarına yardım eden sensör (lambda).",
    "Lambda": "Egzozdaki oksijeni ölçen sensör.",
    "Enjektör": "Motora yakıt püskürten parça.",
    "Buji": "Benzinli motorda yakıtı ateşleyen kıvılcımı veren parça.",
    "Bobin": "Bujiye yüksek voltaj veren ateşleme parçası.",
    "Turbo": "Motora daha fazla hava basan, egzozla dönen parça.",
    "OBD": "Araçların standart teşhis sistemi ve soketi.",
    "J1939": "Kamyon ve iş makinelerinin ortak veri dili.",
    "CAN": "Araçtaki bilgisayarların birbirleriyle konuştuğu veri hattı.",
    "Silindir": "Motorda yakıtın yandığı bölmelerden biri.",
    "Krank": "Motorun dönen ana mili; sensörü motor devrini ölçer.",
}

_DIFFICULTY_RANK = (("kolay", 0), ("orta", 1), ("zor", 2), ("uzman", 2), ("ileri", 2))
# No re.I: under IGNORECASE Python folds "İ"/"ı" onto plain "i"/"I", which
# would make every English sentence containing an "i" look Turkish.
_TURKISH_CHARS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_TURKISH_WORDS = re.compile(r"\b(ve|veya|ile|kontrol|edin|yapın|olabilir)\b", re.I)
_SPN_RE = re.compile(r"SPN\s+(\d+)(?:\s+FMI\s+(\d+))?", re.I)
_EMOJI_OR_MARKUP = re.compile(r"[\U0001F300-\U0001FAFF☀-➿*`]")


@dataclass(frozen=True)
class CodeEvidence:
    """What the knowledge bases say about one observed code."""

    code: str
    kind: str  # stored | pending | permanent | active (J1939 DM1)
    known: bool
    title_tr: str = ""
    source_tr: str = ""
    causes: tuple[str, ...] = ()
    steps: tuple[tuple[str, str, str], ...] = ()  # (action, component, difficulty)
    severity: str = ""
    ecu_tr: str = ""


@dataclass
class ScanContext:
    vehicle_label: str
    vehicle_type: str
    high_voltage: bool = False
    simulator: bool = False
    listen_only: bool = True
    read_requested: bool = False
    read_status: str | None = None  # ok | no_answer | refused | error | declined | None
    passive_seconds: float = 0.0
    battery_message_tr: str = ""
    extra_missing: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_OR_MARKUP.sub("", str(text))).strip()


def _no_kesin(text: str) -> str:
    """§45 Kural 3: never show "kesin" (certain) to the mechanic or customer."""
    text = re.sub(r"\bkesinlikle\b", "mutlaka", text, flags=re.I)
    return re.sub(r"\bkesin\b", "net", text, flags=re.I)


def _is_turkish(text: str) -> bool:
    """Mechanic-facing text is Turkish; English source notes stay in the technical section."""
    return bool(_TURKISH_CHARS.search(text) or _TURKISH_WORDS.search(text))


def _difficulty_rank(difficulty: str) -> int:
    lowered = difficulty.lower()
    return next((rank for word, rank in _DIFFICULTY_RANK if word in lowered), 1)


def explain_code(code: str, kind: str = "active", ecu_tr: str = "") -> CodeEvidence:
    """Look the code up in the local knowledge bases. Unknown codes stay unknown."""
    from src.engine.ai.diagnostic_copilot import (
        EXPERT_KNOWLEDGE_BASE,
        ensure_external_dtc_database_loaded,
        get_j1939_spn_database,
    )

    match = _SPN_RE.search(code)
    if match:
        spn, fmi = match.group(1), match.group(2)
        record = get_j1939_spn_database().get("spns", {}).get(f"SPN_{spn}")
        if not isinstance(record, dict):
            return CodeEvidence(code=code, kind=kind, known=False, ecu_tr=ecu_tr)
        fmi_rec = (record.get("fault_matrix") or {}).get(str(fmi)) if fmi is not None else None
        fmi_rec = fmi_rec if isinstance(fmi_rec, dict) else {}
        title = _clean(fmi_rec.get("fault_title") or record.get("title_tr") or record.get("name") or "")
        # Only the FMI-level record is specific to this fault. Record-level
        # causes/steps are SPN-wide and, for some SPNs, describe a different
        # subsystem (e.g. NOx text under a DPF SPN) — never shown as advice.
        causes = [c.strip() for c in str(fmi_rec.get("causes") or "").split(";") if c.strip()]
        steps: list[tuple[str, str, str]] = []
        if fmi_rec.get("diagnostic_action"):
            steps.append((_clean(fmi_rec["diagnostic_action"]), "", "Orta (Alet Gerekir)"))
        return CodeEvidence(code=code, kind=kind, known=bool(title), title_tr=title,
                            source_tr="J1939 SPN veritabanı", causes=tuple(_clean(c) for c in causes[:4]),
                            steps=tuple(steps), severity=str(fmi_rec.get("severity") or ""), ecu_tr=ecu_tr)

    from src.engine.ai.user_kb import get_entry, load_user_kb

    ensure_external_dtc_database_loaded()
    entry = EXPERT_KNOWLEDGE_BASE.get(code.upper())
    user = get_entry(code.upper(), load_user_kb())
    if not isinstance(entry, dict):
        if user is None:
            return CodeEvidence(code=code, kind=kind, known=False, ecu_tr=ecu_tr)
        return CodeEvidence(code=code, kind=kind, known=True, title_tr=_clean(user.user_title_tr),
                            source_tr="Yerel bilgi tabanı (sade anlatım)", ecu_tr=ecu_tr)
    dtc_steps = tuple(
        (_clean(s[0]), _clean(s[1]) if len(s) > 1 else "", _clean(s[2]) if len(s) > 2 else "")
        for s in entry.get("steps") or [] if isinstance(s, (list, tuple)) and s
    )
    title = _clean(entry.get("title") or code)
    if user is not None and not _is_turkish(title):
        title = _clean(user.user_title_tr)
    return CodeEvidence(
        code=code, kind=kind, known=True, title_tr=title,
        source_tr="SAE J2012 / yerel arıza kodu veritabanı",
        causes=tuple(_clean(c) for c in entry.get("causes") or [] if isinstance(c, str)),
        steps=dtc_steps, severity=str(entry.get("severity") or ""), ecu_tr=ecu_tr,
    )


_KIND_TR = {"stored": "kayıtlı", "pending": "bekleyen", "permanent": "kalıcı", "active": "aktif"}


def _causes(evidence: Sequence[CodeEvidence], fallback: Iterable[str]) -> list[dict[str, str]]:
    """Up to three causes, at least one per code when possible.

    Round-robin over codes. A code whose knowledge record has no Turkish cause
    takes its turn from the analysis engine's own list instead, labelled as
    such (the engine's list is not attributed to a specific code).
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    per_code = [[c for c in ev.causes if _is_turkish(c)] for ev in evidence]
    spare = [t for t in (_clean(c) for c in fallback) if t and _is_turkish(t)]

    def take_spare() -> None:
        while spare:
            text = spare.pop(0)
            if text not in seen:
                seen.add(text)
                out.append({"text_tr": _no_kesin(text), "why_tr": "Analiz motorunun aktif kodlardan çıkarımı",
                            "source_tr": "Kural tabanlı çevrimdışı motor"})
                return

    depth = max((len(c) for c in per_code), default=0)
    for i in range(max(depth, 1)):
        for ev, causes in zip(evidence, per_code, strict=True):
            if len(out) >= 3:
                return out
            if i < len(causes):
                if causes[i] not in seen:
                    seen.add(causes[i])
                    out.append({
                        "text_tr": _no_kesin(causes[i]),
                        "why_tr": f"{ev.code} kodu {_KIND_TR.get(ev.kind, ev.kind)}: {ev.title_tr}",
                        "source_tr": ev.source_tr,
                    })
            elif i == 0:
                take_spare()
    while len(out) < 3 and spare:
        take_spare()
    return out[:3]


def _steps(evidence: Sequence[CodeEvidence], report_steps: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    candidates: list[tuple[str, str, str]] = [s for ev in evidence for s in ev.steps]
    candidates += [(_clean(s.get("action", "")), _clean(s.get("component", "")), _clean(s.get("difficulty", "")))
                   for s in report_steps]
    seen: set[str] = set()
    unique = []
    for action, component, difficulty in candidates:
        if action and _is_turkish(action) and action not in seen:
            seen.add(action)
            unique.append((action, component, difficulty))
    unique.sort(key=lambda s: _difficulty_rank(s[2]))  # stable: KB order within a level
    return [
        {"n": str(i + 1), "action_tr": _no_kesin(a), "component_tr": c, "difficulty_tr": d}
        for i, (a, c, d) in enumerate(unique[:6])
    ]


def _missing(analysis: dict[str, Any], ctx: ScanContext, unknown: Sequence[CodeEvidence]) -> list[str]:
    lines: list[str] = []
    if ctx.vehicle_type == "car" and ctx.read_status in (None, "declined"):
        lines.append("Arıza kodları okunmadı: okuma izni verilmedi. Kodları görmek için yeni taramada "
                     "'Okumaya izin ver'i seçin.")
    elif ctx.read_status == "no_answer":
        lines.append("Araç okuma isteğine yanıt vermedi. Kontağın açık olduğundan ve kablonun OBD soketine "
                     "tam oturduğundan emin olun.")
    elif ctx.read_status in ("refused", "error"):
        lines.append("Okuma isteği güvenlik nedeniyle gönderilemedi; yalnız dinleme verisi kullanıldı.")
    gate = analysis.get("gate") or {}
    for gap in gate.get("gaps") or []:
        if "kayıt süresi" in str(gap):
            lines.append("Emin olmak için motor çalışırken en az 60 sn daha kayıt alın.")
        else:
            lines.append(_clean(gap))
    if not (analysis.get("report") or {}).get("telemetry_correlations"):
        lines.append("Canlı ölçümle doğrulanmadı: motor çalışırken tarama tekrarlanırsa sonuç güçlenir.")
    lines.extend(f"{ev.code} kodu için veritabanında açıklama yok." for ev in unknown)
    lines.extend(ctx.extra_missing)
    if ctx.simulator:
        lines.append("Bu sonuç simülatörden gelir; gerçek araç verisi değildir.")
    # de-duplicate, keep order
    return list(dict.fromkeys(lines))


def _glossary(texts: Iterable[str]) -> list[dict[str, str]]:
    blob = " ".join(texts)
    return [{"term": term, "meaning_tr": meaning} for term, meaning in GLOSSARY.items()
            if re.search(rf"\b{re.escape(term)}", blob, re.I)]


def compose_mechanic_result(
    analysis: dict[str, Any], evidence: Sequence[CodeEvidence], ctx: ScanContext
) -> dict[str, Any]:
    """Fixed-order mechanic card (MECHANIC_FLOW.md §3.12)."""
    card = analysis.get("user_card") or {}
    report = analysis.get("report") or {}
    risk = str(card.get("risk_level") or "GRAY")
    if risk not in URGENCY:
        risk = "GRAY"
    known = [e for e in evidence if e.known]
    unknown = [e for e in evidence if not e.known]

    safety: list[str] = []
    if ctx.high_voltage or "Yüksek voltaj" in str(card.get("summary_tr", "")):
        safety.append("Yüksek voltajlı araç: turuncu kablolara dokunmayın; yalnız okuma yapıldı.")
    if risk == "RED" and evidence:
        safety.append(f"Aracı sürmeyin: {_no_kesin(_clean(card.get('headline_tr', '')))}.")
    if ctx.battery_message_tr:
        safety.append(ctx.battery_message_tr)

    if evidence:
        summary = _no_kesin(_clean(card.get("summary_tr") or card.get("headline_tr") or ""))
        headline = _no_kesin(_clean(card.get("headline_tr") or ""))
    else:
        headline = "Kayıtlı arıza kodu bulunamadı"
        summary = "Bu, araçta hiçbir sorun olmadığı anlamına gelmez. Bazı sorunlar her taramada kod üretmez."
    if evidence and not known:
        headline = "Bu kodu tanımıyoruz"
        summary = ("Uydurmamak için yorum yapmıyoruz. Kod: " + ", ".join(e.code for e in unknown) + ".")

    if evidence:
        causes = _causes(known, report.get("likely_causes") or [])
        steps = _steps(known, report.get("troubleshooting_steps") or [])
    else:
        causes = []
        steps = [
            {"n": "1", "action_tr": "Müşterinin şikâyetini (ses, titreme, lamba, güç kaybı) not edin.",
             "component_tr": "", "difficulty_tr": "Kolay"},
            {"n": "2", "action_tr": "Şikâyet sürüyorsa motor çalışırken ve şikâyet anında yeniden tarayın.",
             "component_tr": "", "difficulty_tr": "Kolay"},
        ]
    missing = _missing(analysis, ctx, unknown)
    sources = list(dict.fromkeys([e.source_tr for e in known if e.source_tr] +
                                 [str(b) for b in card.get("source_badges") or []]))
    technical = {
        "codes": [{"code": e.code, "kind": e.kind, "ecu_tr": e.ecu_tr, "title_tr": e.title_tr,
                   "severity": e.severity, "known": e.known} for e in evidence],
        "severity": report.get("severity"),
        "subsystems": list(report.get("affected_subsystems") or []),
        "confidence_tr": _clean(((card.get("technical") or {}).get("confidence_label")) or ""),
        "source_notes": [f"{e.code}: {c}" for e in known for c in e.causes if not _is_turkish(c)],
    }
    glossary = _glossary([headline, summary] + [c["text_tr"] for c in causes] + [s["action_tr"] for s in steps]
                         + ["ECU"] * bool(evidence))
    urgency_tr, urgency_en = URGENCY[risk]
    return {
        "safety_tr": safety,
        "headline_tr": headline,
        "summary_tr": summary,
        "risk_level": risk,
        "urgency_tr": urgency_tr,
        "urgency_en": urgency_en,
        "advice_tr": _clean(card.get("risk_advice_tr") or ""),
        "causes": causes,
        "steps": steps,
        "missing_tr": missing,
        "technical": technical,
        "sources_tr": sources or ["Kural tabanlı çevrimdışı motor"],
        "glossary": glossary,
        "simulator": ctx.simulator,
        "vehicle_label": ctx.vehicle_label,
    }


REPORT_DISCLAIMER_TR = "Bu rapor otomatik teşhis önerisidir; son karar ustanındır."


def customer_report_text(result: dict[str, Any], *, workshop: str = "", when: datetime | None = None) -> str:
    """One-page plain-language customer report (MECHANIC_FLOW.md §3.13)."""
    stamp = (when or datetime.now()).strftime("%d.%m.%Y %H:%M")
    lines = ["ARAÇ KONTROL RAPORU"]
    if workshop:
        lines.append(workshop)
    lines += [f"Araç: {result.get('vehicle_label', '')}", f"Tarih: {stamp}", ""]
    for line in result.get("safety_tr") or []:
        lines.append(f"UYARI: {line}")
    lines += [f"Bulgu: {result.get('headline_tr', '')}", str(result.get("summary_tr", "")),
              f"Aciliyet: {result.get('urgency_tr', '')}", ""]
    codes = [c for c in (result.get("technical") or {}).get("codes", []) if c.get("known")]
    if codes:
        lines.append("Bulunan arızalar:")
        lines += [f"- {c['title_tr']} ({c['code']})" for c in codes]
        lines.append("")
    steps = result.get("steps") or []
    if steps:
        lines.append("Önerilen işlem:")
        lines += [f"{s['n']}. {s['action_tr']}" for s in steps[:3]]
        lines.append("")
    if result.get("simulator"):
        lines.append("Not: Bu rapor simülatör verisiyle oluşturulmuştur.")
    lines.append(REPORT_DISCLAIMER_TR)
    return _no_kesin("\n".join(lines))

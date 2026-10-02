"""Structured, plain-language answers for the offline copilot (TR/EN).

Public entry point: :func:`answer_query`. It runs
``parse_query`` -> ``reason`` -> this composer and returns a
:class:`StructuredAnswer` with exactly six user-facing sections:

1. ``summary``      — two or three plain sentences;
2. ``urgency``      — RED / YELLOW / GREEN / GRAY with fixed drive advice;
3. ``causes``       — ranked, each with why / why-not and source refs;
4. ``steps``        — what to do, simplest checks first;
5. ``missing_data`` — which measurement is missing and how to take it;
6. ``technical``    — DTC/SPN/FMI/PGN details, glossary, sources (collapsible).

Safety banners (high voltage, fire, brakes, steering) are a separate list and
are always rendered FIRST. Every claim carries the ``source#key`` refs it was
built from; fixed templates are marked ``template:*``. Nothing here produces a
measurement: numbers only come from the user's input or from a cited record.
Output is deterministic (no clock, no randomness).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.engine.ai.copilot_reasoner import CodeFact, Hypothesis, Reasoning, reason
from src.engine.ai.knowledge_base import KnowledgeBase, fold_text, get_knowledge_base
from src.engine.ai.query_understanding import parse_query

__all__ = ["CopilotOptions", "StructuredAnswer", "answer_query", "ENGINE_ID"]

ENGINE_ID = "ucanlab-offline-copilot/2 (deterministic, no LLM, no network)"


@dataclass(frozen=True, slots=True)
class CopilotOptions:
    local_search: bool | None = None   # None -> follow UCANLAB_COPILOT_LOCAL_SEARCH (default on)
    include_recalls: bool = True
    max_causes: int = 5
    max_steps: int = 8


# ---------------------------------------------------------------- strings
_S: dict[str, dict[str, str]] = {
    "risk.RED": {"tr": "Acil — aracı güvenli bir yerde durdurun", "en": "Urgent — stop the vehicle safely"},
    "risk.YELLOW": {"tr": "Dikkat — yakın zamanda kontrol ettirin", "en": "Caution — have it checked soon"},
    "risk.GREEN": {"tr": "Acil bir durum görünmüyor", "en": "No urgent issue seen"},
    "risk.GRAY": {"tr": "Belirsiz — aciliyeti söylemek için veri yetersiz", "en": "Unclear — not enough data to judge urgency"},
    "color.RED": {"tr": "kırmızı", "en": "red"},
    "color.YELLOW": {"tr": "sarı", "en": "yellow"},
    "color.GREEN": {"tr": "yeşil", "en": "green"},
    "color.GRAY": {"tr": "gri", "en": "gray"},
    "conf.high": {"tr": "yüksek", "en": "high"},
    "conf.medium": {"tr": "orta", "en": "medium"},
    "conf.low": {"tr": "düşük", "en": "low"},
    "kind.graph": {"tr": "kök neden grafiği", "en": "root-cause graph"},
    "kind.record": {"tr": "kod kaydındaki olası neden", "en": "possible cause listed in the code record"},
    "kind.suspected": {"tr": "yalnız şikâyetten çıkarım (kod okunmadı)", "en": "inferred from the complaint only (no code read)"},
    "h.summary": {"tr": "1. Kısa özet", "en": "1. Summary"},
    "h.urgency": {"tr": "2. Acil mi?", "en": "2. Is it urgent?"},
    "h.causes": {"tr": "3. En olası nedenler", "en": "3. Most likely causes"},
    "h.steps": {"tr": "4. Ne yapmalı", "en": "4. What to do"},
    "h.missing": {"tr": "5. Eksik veri ve nasıl alınır", "en": "5. Missing data and how to get it"},
    "h.technical": {"tr": "6. Teknik detay", "en": "6. Technical details"},
    "h.recalls": {"tr": "NHTSA geri çağırma / şikâyet kayıtları (ABD, ayrı kaynak)", "en": "NHTSA recall / complaint records (US, separate source)"},
    "likelihood": {"tr": "adaylar arasında göreli", "en": "relative among candidates"},
    "confidence": {"tr": "güven", "en": "confidence"},
    "support": {"tr": "Destekleyen", "en": "Supports"},
    "against": {"tr": "Çelişen", "en": "Against"},
    "needs": {"tr": "Doğrulamak için ölçün", "en": "Measure to confirm"},
    "no_causes": {"tr": "Kayıtlı kök neden bulunamadı; tahmin yürütülmedi.", "en": "No recorded root cause found; nothing was guessed."},
    "no_missing": {"tr": "Bu aşamada kritik eksik veri yok.", "en": "No critical data missing at this point."},
    "likelihood_note": {
        "tr": "Yüzdeler yalnız listelenen adaylar arasındaki göreli sıralamadır, kesin olasılık değildir.",
        "en": "Percentages are only the relative ranking among the listed candidates, not absolute probabilities.",
    },
    "recall_note": {
        "tr": "Bu kayıtlar marka/model ve kelime eşleşmesiyle bulundu; aracınızın kapsamda olduğu VIN ile doğrulanmadı.",
        "en": "Found by make/model and keyword match; your vehicle's coverage is NOT verified by VIN.",
    },
    "nothing": {
        "tr": "Girdide tanınan bir arıza kodu, belirti veya ölçüm bulunamadı; veritabanında eşleşen kayıt yok. Tahmin yürütülmedi.",
        "en": "No recognised fault code, symptom or measurement in the input; no matching record in the database. Nothing was guessed.",
    },
    "unknown_code": {
        "tr": "{code} veritabanında yok; anlamı uydurulmadı.",
        "en": "{code} is not in the database; its meaning was not guessed.",
    },
}

_BANNERS: dict[str, dict[str, str]] = {
    "fire": {
        "tr": "⚠ YANGIN RİSKİ: Aracı hemen durdurun, kontağı kapatın ve herkesi araçtan uzaklaştırın. Duman veya alev varsa 112'yi arayın.",
        "en": "⚠ FIRE RISK: Stop the vehicle now, switch off and move everyone away. If there is smoke or flame, call emergency services.",
    },
    "high_voltage": {
        "tr": "⚠ YÜKSEK VOLTAJ: Turuncu kablolara dokunmayın. Yetkisiz müdahale yapmayın; şarjı durdurun ve yetkili HV servisini çağırın.",
        "en": "⚠ HIGH VOLTAGE: Do not touch orange cables. No untrained work; stop charging and call an authorised HV service.",
    },
    "brakes": {
        "tr": "⚠ FREN SİSTEMİ: Fren etkinliği azalmış olabilir. Frenler kontrol edilmeden yola çıkmayın.",
        "en": "⚠ BRAKES: Braking may be reduced. Do not drive on until the brakes are checked.",
    },
    "steering": {
        "tr": "⚠ DİREKSİYON: Direksiyon hakimiyeti etkilenebilir. Düşük hızla güvenli bir yere çekin ve kontrol ettirin.",
        "en": "⚠ STEERING: Steering control may be affected. Pull over slowly to a safe place and have it checked.",
    },
}

_ADVICE_EN: dict[str, str] = {
    "RED": "Stop the vehicle safely.\nSwitch the engine off and do not restart it.\nCall a tow truck or roadside assistance.",
    "YELLOW": "You may drive, but do not load the engine.\nAvoid high revs, towing, hills and long trips.\nGo to a workshop as soon as possible.",
    "GREEN": "No urgent condition is seen right now.\nKeep an eye on the fault and add it to the service plan.\nScan again if symptoms appear.",
    "GRAY": "There is not enough data on this.\nA professional check is recommended for your safety.",
}

_STATUS: dict[str, dict[str, str]] = {
    "critical_high": {"tr": "kritik yüksek", "en": "critically high"},
    "critical_low": {"tr": "kritik düşük", "en": "critically low"},
    "high": {"tr": "uyarı seviyesinin üstünde", "en": "above warning level"},
    "above_nominal": {"tr": "normal aralığın biraz üstünde", "en": "slightly above normal range"},
    "low": {"tr": "olması gerekenden düşük", "en": "lower than required"},
    "normal": {"tr": "normal aralıkta", "en": "within normal range"},
    "implausible": {"tr": "fiziksel olarak mümkün değil (sensör/veri hatası)", "en": "physically impossible (sensor/data error)"},
    "no_threshold": {"tr": "veritabanında bu sinyal için eşik yok; yorumlanmadı", "en": "no threshold for this signal in the database; not judged"},
    "needs_context": {"tr": "devir bilinmeden yorumlanamaz", "en": "cannot be judged without engine speed"},
    "plausible": {"tr": "makul bir okuma (bu nedeni zayıflatır)", "en": "a plausible reading (weakens this cause)"},
    "at_limit": {"tr": "sensör ölçüm aralığının en alt sınırında (tipik kopuk/kısa devre varsayılan değeri)",
                 "en": "pinned at the bottom of the sensor range (typical open/short default value)"},
    "below_nominal": {"tr": "kayıttaki normal çalışma aralığının altında (yük/sıcaklık koşuluna bağlı olabilir)",
                      "en": "below the recorded normal operating range (may depend on load/temperature)"},
}


def _t(key: str, lang: str, **kw: Any) -> str:
    entry = _S.get(key, {})
    text = entry.get(lang) or entry.get("tr") or key
    return text.format(**kw) if kw else text


# ------------------------------------------------------------- data model
@dataclass(slots=True)
class StructuredAnswer:
    language: str
    safety_banners: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    urgency: dict[str, Any] = field(default_factory=dict)
    causes: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    missing_data: list[dict[str, Any]] = field(default_factory=list)
    technical: dict[str, Any] = field(default_factory=dict)
    recalls: dict[str, Any] = field(default_factory=dict)
    understood: dict[str, Any] = field(default_factory=dict)
    engine: str = ENGINE_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "safety_banners": list(self.safety_banners),
            "summary": self.summary,
            "urgency": dict(self.urgency),
            "causes": list(self.causes),
            "steps": list(self.steps),
            "missing_data": list(self.missing_data),
            "technical": dict(self.technical),
            "recalls": dict(self.recalls),
            "understood": dict(self.understood),
            "engine": self.engine,
        }

    def all_refs(self) -> list[str]:
        """Every data citation in the answer (templates/input excluded)."""
        refs: list[str] = []

        def add(items: Iterable[str]) -> None:
            for r in items:
                if r and not r.startswith(("template:", "input")) and r not in refs:
                    refs.append(r)

        add(self.urgency.get("refs", []))
        for c in self.causes:
            add(c.get("refs", []))
            add(e.get("ref", "") for e in c.get("support", []) + c.get("against", []))
        for s in self.steps:
            add(s.get("refs", []))
        for m in self.missing_data:
            add(m.get("refs", []))
        for code in self.technical.get("codes", []):
            add(code.get("refs", []))
        add(t.get("ref", "") for t in self.technical.get("telemetry", []))
        add(p.get("ref", "") for p in self.technical.get("pgns", []) + self.technical.get("pids", []))
        add(h.get("ref", "") for h in self.technical.get("similar_records", []))
        for rec in self.recalls.get("items", []):
            add([rec.get("ref", "")])
        return refs

    # ----------------------------------------------------------- markdown
    def to_markdown(self) -> str:
        lang = self.language
        lines: list[str] = []
        for b in self.safety_banners:
            lines.append(f"**{b['text']}**")
        if lines:
            lines.append("")
        lines += [f"## {_t('h.summary', lang)}", self.summary, ""]
        u = self.urgency
        icon = {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}.get(u.get("level", ""), "⚪")
        lines.append(f"## {_t('h.urgency', lang)} {icon} {u.get('label', '')}")
        lines += [f"- {a}" for a in u.get("advice", [])]
        for reason_line in u.get("reasons", []):
            lines.append(f"- _{reason_line['text']}_ `{reason_line['ref']}`" if reason_line.get("ref") else f"- _{reason_line['text']}_")
        lines.append("")
        lines.append(f"## {_t('h.causes', lang)}")
        if not self.causes:
            lines.append(_t("no_causes", lang))
        for c in self.causes:
            lines.append(
                f"{c['rank']}. **{c['title']}** — %{round(c['likelihood'] * 100)} ({_t('likelihood', lang)}), "
                f"{_t('confidence', lang)}: {c['confidence_label']} · _{c['kind_label']}_"
            )
            for e in c["support"]:
                lines.append(f"   - {_t('support', lang)}: {e['text']} `{e['ref']}`")
            for e in c["against"]:
                lines.append(f"   - {_t('against', lang)}: {e['text']} `{e['ref']}`")
            if c.get("measure_to_confirm"):
                lines.append(f"   - {_t('needs', lang)}: {', '.join(c['measure_to_confirm'])}")
        if self.causes:
            lines.append(f"_{_t('likelihood_note', lang)}_")
        lines += ["", f"## {_t('h.steps', lang)}"]
        for s in self.steps:
            ref = f" `{', '.join(s['refs'])}`" if s.get("refs") else ""
            diff = f" _({s['difficulty']})_" if s.get("difficulty") else ""
            lines.append(f"{s['n']}. {s['text']}{diff}{ref}")
        lines += ["", f"## {_t('h.missing', lang)}"]
        if not self.missing_data:
            lines.append(_t("no_missing", lang))
        for m in self.missing_data:
            lines.append(f"- **{m['what']}** {m['how']}")
        lines += ["", f"<details><summary>{_t('h.technical', lang)}</summary>", ""]
        for code in self.technical.get("codes", []):
            lines.append(f"- **{code['key']}** — {code.get('title') or '—'}")
            for label, key in (("FMI", "fmi_text"), ("PGN", "pgn_text"), ("Severity", "severity"),
                               ("Ref.", "reference_values"), ("OEM", "oem_text")):
                if code.get(key):
                    lines.append(f"  - {label}: {code[key]}")
            if code.get("refs"):
                lines.append(f"  - `{', '.join(code['refs'])}`")
        for f in self.technical.get("telemetry", []):
            lines.append(f"- {f['signal']} = {f['value']} {f['unit']} → {f['status_text']}"
                         + (f" ({f['reference']})" if f.get("reference") else "") + (f" `{f['ref']}`" if f.get("ref") else ""))
        for item in self.technical.get("pgns", []) + self.technical.get("pids", []):
            lines.append(f"- {item['text']} `{item['ref']}`")
        if self.technical.get("similar_records"):
            lines.append("- " + ("Metin benzerliği olan kayıtlar (teşhis değil, yalnız ipucu):" if lang == "tr"
                                 else "Records with similar wording (not a diagnosis, hint only):"))
        for hit in self.technical.get("similar_records", []):
            lines.append(f"  - {hit['title']} `{hit['ref']}`")
        if self.technical.get("glossary"):
            lines.append("")
            for g in self.technical["glossary"]:
                lines.append(f"- _{g['term']}_: {g['text']}")
        lines += ["", "</details>"]
        if self.recalls.get("items") or self.recalls.get("complaints"):
            lines += ["", f"### {_t('h.recalls', lang)}", f"_{self.recalls.get('note', '')}_"]
            for rec in self.recalls.get("items", []):
                lines.append(f"- {rec['campaign']}: {rec['component']} `{rec['ref']}`")
            if self.recalls.get("complaints"):
                cc = self.recalls["complaints"]
                lines.append(f"- {cc['text']} `{cc['ref']}`")
        return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------- render
def _evidence_text(marker: str, lang: str, kb: KnowledgeBase) -> str:
    kind, _, payload = marker.partition(":")
    if kind == "code":
        return f"{payload} {'aktif' if lang == 'tr' else 'active'}"
    if kind == "complaint":
        rec = kb.symptom(payload).record or {}
        name = rec.get("name_tr") if lang == "tr" else rec.get("name_en")
        return f"{'şikâyet' if lang == 'tr' else 'complaint'}: {name or payload}"
    if kind == "complaint_code":
        sid, _, code = payload.partition("|")
        rec = kb.symptom(sid).record or {}
        name = rec.get("name_tr") if lang == "tr" else rec.get("name_en")
        return (f"şikâyet ({name or sid}) bu kodu işaret ediyor: {code}" if lang == "tr"
                else f"complaint ({name or sid}) points to code {code}")
    if kind == "severity":
        key, _, sev = payload.partition("|")
        return f"{key}: {'kayıtlı ciddiyet' if lang == 'tr' else 'recorded severity'} {sev}"
    if kind == "ev":
        return (f"{payload}: elektrikli/hibrit yüksek voltaj kodu" if lang == "tr"
                else f"{payload}: electric/hybrid high-voltage code")
    if kind == "lamp":
        if payload == "red_stop":
            return "DM1: kırmızı STOP lambası yanıyor" if lang == "tr" else "DM1: red STOP lamp is on"
        return "DM1: uyarı lambası yanıyor" if lang == "tr" else "DM1: warning lamp is on"
    if kind == "safety":
        names = {"fire": ("yangın", "fire"), "high_voltage": ("yüksek voltaj", "high voltage"),
                 "brakes": ("fren", "brakes"), "steering": ("direksiyon", "steering")}
        cats = ", ".join(names.get(c, (c, c))[0 if lang == "tr" else 1] for c in payload.split(","))
        return f"{'güvenlik açısından kritik sistem' if lang == 'tr' else 'safety-critical system'}: {cats}"
    if kind == "stop_safety":
        if payload == "steering":
            return ("direksiyon şikâyeti: direksiyon hâkimiyeti kaybolabilir" if lang == "tr"
                    else "steering complaint: steering control may be lost")
        return ("fren şikâyeti: aracı durdurma gücü azalmış olabilir" if lang == "tr"
                else "brake complaint: stopping power may be reduced")
    if kind == "stop_complaint":
        rec = kb.symptom(payload).record or {}
        name = rec.get("name_tr") if lang == "tr" else rec.get("name_en")
        return (f"şikâyet: {name or payload} — motor çalıştırılmaya devam ederse kalıcı hasar görebilir"
                if lang == "tr" else f"complaint: {name or payload} — running the engine on can cause permanent damage")
    if kind == "fmi":
        num, _, fam = payload.partition("|")
        if fam == "electrical":
            return (f"FMI {num} elektriksel arıza tipidir (sensör/kablo); gerçek bir aşırı değer anlamına gelmez"
                    if lang == "tr" else f"FMI {num} is an electrical failure mode (sensor/wiring), not a real extreme value")
        return (f"FMI {num} 'veri geçerli ama aralık dışı' demektir; sensör çalışıyor, değer gerçek"
                if lang == "tr" else f"FMI {num} means 'data valid but out of range': the sensor works, the value is real")
    if kind == "signal":
        sig, value, unit, status = (payload.split("|") + ["", "", "", ""])[:4]
        return f"{sig} = {value} {unit} — {_STATUS.get(status, {}).get(lang, status)}"
    return marker


def _code_title(c: CodeFact, lang: str) -> str:
    if lang == "en":
        return c.title_en or c.title_tr
    return c.title_tr or c.title_en


def _cause_dict(h: Hypothesis, rank: int, lang: str, kb: KnowledgeBase) -> dict[str, Any]:
    return {
        "rank": rank,
        "id": h.id,
        "title": h.title,
        "likelihood": h.likelihood,
        "confidence": h.confidence,
        "confidence_label": _t(f"conf.{h.confidence}", lang),
        "kind": h.kind,
        "kind_label": _t(f"kind.{h.kind}", lang),
        "codes": list(h.codes),
        "support": [{"text": _evidence_text(s, lang, kb), "ref": r} for s, r in h.support],
        "against": [{"text": _evidence_text(s, lang, kb), "ref": r} for s, r in h.against],
        "measure_to_confirm": list(h.missing_signals[:3]),
        "falsifiable": h.falsifiable,
        "refs": list(h.refs),
    }


def _short(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def _summary(r: Reasoning, lang: str, kb: KnowledgeBase) -> str:
    pq = r.parsed
    found = [c for c in r.codes if c.found]
    unknown = [c for c in r.codes if not c.found]
    if not found and not r.symptom_records and not r.findings:
        parts = [_t("unknown_code", lang, code=c.key) for c in unknown]
        return " ".join(parts + [_t("nothing", lang)]) if not parts else " ".join(parts)
    bits: list[str] = []
    if found:
        listing = ", ".join(f"{c.key} ({_short(_code_title(c, lang))})" if _code_title(c, lang) else c.key for c in found[:4])
        bits.append(f"Değerlendirilen kod: {listing}." if lang == "tr" else f"Codes evaluated: {listing}.")
    for c in unknown:
        bits.append(_t("unknown_code", lang, code=c.key))
    if r.symptom_records:
        names = ", ".join(
            str(rec.get("name_tr") if lang == "tr" else rec.get("name_en")) for _sid, rec in r.symptom_records[:3])
        bits.append(f"Anlaşılan şikâyet: {names}." if lang == "tr" else f"Complaint understood as: {names}.")
    abnormal = [f for f in r.findings if f.abnormal]
    others = [f for f in r.findings if not f.abnormal]
    if others and not found:
        txt = ", ".join(f"{f.signal} {f.value:g} {f.unit} ({_STATUS.get(f.status, {}).get(lang, f.status)})" for f in others[:3])
        bits.append(f"Ölçülen: {txt}." if lang == "tr" else f"Measured: {txt}.")
    if abnormal:
        txt = ", ".join(f"{f.signal} {f.value:g} {f.unit} ({_STATUS[f.status][lang]})" for f in abnormal[:3])
        bits.append(f"Ölçüm dışı değer: {txt}." if lang == "tr" else f"Out-of-range reading: {txt}.")
    if r.hypotheses:
        top = r.hypotheses[0]
        conf = _t(f"conf.{top.confidence}", lang)
        bits.append(f"En olası neden: {top.title} (güven: {conf})." if lang == "tr"
                    else f"Most likely cause: {top.title} (confidence: {conf}).")
    else:
        bits.append(_t("no_causes", lang))
    if pq.corrections:
        fixes = ", ".join(f"{a}→{b}" for a, b in pq.corrections[:3])
        bits.append(f"(Yazım düzeltildi: {fixes})" if lang == "tr" else f"(Spelling corrected: {fixes})")
    return " ".join(bits)


def _urgency(r: Reasoning, lang: str, kb: KnowledgeBase) -> dict[str, Any]:
    from src.engine.ai.drive_safety_policy import RISK_ADVICE

    advice = RISK_ADVICE[r.risk] if lang == "tr" else _ADVICE_EN[r.risk]  # type: ignore[index]
    reasons = [{"text": _evidence_text(t, lang, kb), "ref": ref} for t, ref in r.risk_reasons]
    return {
        "level": r.risk,
        "color": _t(f"color.{r.risk}", lang),
        "label": _t(f"risk.{r.risk}", lang),
        "advice": advice.split("\n"),
        "reasons": reasons,
        "refs": [ref for _t2, ref in r.risk_reasons if ref] + ["template:drive_safety_policy.RISK_ADVICE"],
    }


def _steps(r: Reasoning, lang: str, max_steps: int) -> list[dict[str, Any]]:
    steps: list[tuple[str, str, list[str]]] = []
    seen: set[str] = set()

    token_sets: list[set[str]] = []

    def add(text: str, difficulty: str, refs: list[str]) -> None:
        key = fold_text(text)[:120]
        toks = {t for t in fold_text(text).split() if len(t) > 3}
        # near-duplicate steps (same advice reworded in two records) shown once
        if not key or key in seen or any(toks and len(toks & o) / len(toks | o) > 0.5 for o in token_sets):
            return
        seen.add(key)
        token_sets.append(toks)
        steps.append((text, difficulty, refs))

    for cat in r.safety:
        add(_BANNERS[cat][lang].lstrip("⚠ "), "", [f"template:safety.{cat}"])
    if r.risk == "RED" and not r.safety:
        add("Motoru çalıştırmaya devam etmeyin; önce aşağıdaki kontrolleri yapın." if lang == "tr"
            else "Do not keep the engine running; do the checks below first.", "", ["template:risk.RED"])
    if not r.codes:
        add("Tarama ekranından arıza kodlarını okuyun (OBD-II Mode 03 / J1939 DM1)." if lang == "tr"
            else "Read the fault codes from the scan screen (OBD-II Mode 03 / J1939 DM1).", "", ["template:scan"])
    for c in r.codes:
        if not c.found:
            add((f"{c.key}: kodu tarama cihazından tekrar okuyup doğrulayın; bu kod için doğrulanmış kayıt yok, "
                 "üreticinin servis bilgisine başvurun.") if lang == "tr" else
                (f"{c.key}: re-read and confirm the code with the scan tool; there is no verified record for it, "
                 "consult the manufacturer's service information."), "", ["template:unknown_code"])
    for sid, rec in r.symptom_records[:2]:
        for q in list(rec.get("initial_questions") or [])[:2]:
            add(f"{'Kontrol edin' if lang == 'tr' else 'Check'}: {q}", "Kolay" if lang == "tr" else "Easy",
                [f"canonical_symptoms#{sid}"])
    code_steps: list[tuple[int, int, str, str, str]] = []
    for ci, c in enumerate(r.codes):
        for si, (action, difficulty, ref) in enumerate(c.steps[:4]):
            from src.engine.ai.copilot_reasoner import _difficulty_rank

            code_steps.append((_difficulty_rank(difficulty) if difficulty else 1, ci * 10 + si, action, difficulty, ref))
    for _rank, _order, action, difficulty, ref in sorted(code_steps):
        add(action, difficulty, [ref])
    out = []
    for i, (text, difficulty, refs) in enumerate(steps[:max_steps], 1):
        out.append({"n": i, "text": text, "difficulty": difficulty, "refs": refs})
    return out


def _technical(r: Reasoning, lang: str, kb: KnowledgeBase, similar: list[Any]) -> dict[str, Any]:
    codes = []
    for c in r.codes:
        fmi_text = ""
        if c.fmi is not None:
            body = (c.fmi_text_tr or c.fmi_text_en) if lang == "tr" else (c.fmi_text_en or c.fmi_text_tr)
            generic = (" (genel FMI tanımı)" if lang == "tr" else " (generic FMI definition)") if c.fmi_generic else ""
            fmi_text = f"{c.fmi}: {body}{generic}" if body else str(c.fmi)
        pgn_text = ""
        if c.pgn is not None:
            pgn_text = f"{c.pgn}" + (f" ({c.pgn_acronym})" if c.pgn_acronym else "") + (f" — {c.pgn_label}" if c.pgn_label else "")
        sev = f"{c.severity}" + (f" `{c.severity_ref}`" if c.severity_ref else "")
        codes.append({
            "key": c.key, "kind": c.kind, "found": c.found, "origin": c.origin,
            "title": _code_title(c, lang), "title_tr": c.title_tr, "title_en": c.title_en,
            "description": c.description[:400], "subsystem": c.subsystem, "system": c.system_id,
            "fmi_text": fmi_text, "pgn_text": pgn_text, "severity": sev if c.found else "",
            "reference_values": (("Kayıttaki referans (ölçüm değil): " if lang == "tr" else "Reference from record (not a measurement): ")
                                 + c.reference_values) if c.reference_values else "",
            "oem_text": (", ".join(c.oem_makes[:8]) + (f" — {c.oem_text}" if c.oem_text else "")) if (c.oem_makes or c.oem_text) else "",
            "occurrence_count": c.occurrence_count,
            "notice": c.notice,
            "refs": list(c.refs),
        })
    telemetry = [{
        "signal": f.signal, "value": f.value, "unit": f.unit, "status": f.status,
        "status_text": _STATUS.get(f.status, {}).get(lang, f.status), "reference": f.reference,
        "ref": f.ref, "origin": f.origin, "unit_assumed": f.unit_assumed,
    } for f in r.findings]
    pgns = []
    for pgn in r.parsed.pgns:
        look = kb.pgn(pgn)
        if look.found:
            rec = look.record
            desc = rec["canboat"][0].get("description") if rec.get("canboat") else rec.get("label")
            spns = ", ".join(str(s) for s in rec.get("spns", [])[:8])
            pgns.append({"pgn": pgn, "text": f"PGN {pgn}: {desc or rec.get('acronym') or ''}" + (f" — SPN {spns}" if spns else ""),
                         "ref": look.ref})
        else:
            pgns.append({"pgn": pgn, "text": f"PGN {pgn}: " + ("veritabanında yok" if lang == "tr" else "not in database"),
                         "ref": ""})
    pids = []
    for pid in r.parsed.pids:
        look = kb.pid(pid, "01")
        if look.found:
            row = look.record[0]
            pids.append({"pid": pid, "text": f"Mode 01 PID {pid}: {row.get('name')} ({row.get('unit')})", "ref": look.ref})
    lamps = [{"mil": lp.mil, "red_stop": lp.red_stop, "amber_warning": lp.amber_warning, "protect": lp.protect}
             for lp in r.parsed.dm1_lamps]
    return {
        "codes": codes,
        "telemetry": telemetry,
        "pgns": pgns,
        "pids": pids,
        "dm1_lamps": lamps,
        "similar_records": [{"title": h.title, "ref": h.ref, "score": h.score} for h in similar],
        "glossary": [],
        "notes": list(r.parsed.notes),
    }


def _glossary(answer: StructuredAnswer, kb: KnowledgeBase, lang: str) -> list[dict[str, str]]:
    blob = " " + fold_text(answer.to_markdown()) + " "
    out: list[dict[str, str]] = []
    for term, rec in sorted(kb.glossary().items()):
        if any(f" {fold_text(m)} " in blob for m in rec.get("match") or [] if fold_text(m)):
            out.append({"term": term, "text": str(rec.get(lang) or rec.get("tr") or ""), "ref": f"copilot_glossary#{term}"})
    return out[:10]


# ------------------------------------------------------------------ entry
def answer_query(
    text: str = "",
    *,
    dtcs: Iterable[Any] = (),
    telemetry: Mapping[str, Any] | None = None,
    dm1: Iterable[Any] = (),
    vehicle_make: str | None = None,
    vehicle_model: str | None = None,
    language: str | None = None,
    options: CopilotOptions | None = None,
    kb: KnowledgeBase | None = None,
) -> StructuredAnswer:
    """Answer one copilot request with the six-section structured format."""
    opts = options or CopilotOptions()
    kb = kb or get_knowledge_base()
    parsed = parse_query(text, dtcs=dtcs, telemetry=telemetry, dm1=dm1, vehicle_make=vehicle_make,
                         vehicle_model=vehicle_model, language=language, kb=kb)
    r = reason(parsed, kb, include_recalls=opts.include_recalls)
    lang = parsed.language

    similar: list[Any] = []
    if not r.codes and not r.symptom_records and parsed.text.strip():
        from src.engine.ai.local_search import LocalSearchIndex, local_search_enabled

        if local_search_enabled(opts.local_search):
            idx = getattr(kb, "_copilot_search_index", None)
            if idx is None:
                idx = LocalSearchIndex(kb)
                kb._copilot_search_index = idx  # type: ignore[attr-defined]
            similar = idx.search(parsed.text)

    ans = StructuredAnswer(language=lang)
    ans.safety_banners = [{"category": c, "text": _BANNERS[c][lang]} for c in r.safety]
    ans.summary = _summary(r, lang, kb)
    ans.urgency = _urgency(r, lang, kb)
    ans.causes = [_cause_dict(h, i, lang, kb) for i, h in enumerate(r.hypotheses[: opts.max_causes], 1)]
    ans.steps = _steps(r, lang, opts.max_steps)
    ans.missing_data = [{
        "key": m.key, "what": m.what_tr if lang == "tr" else m.what_en,
        "how": m.how_tr if lang == "tr" else m.how_en, "refs": list(m.refs),
    } for m in r.missing]
    ans.technical = _technical(r, lang, kb, similar)
    if r.recalls or r.complaints:
        complaints = None
        if r.complaints:
            n = r.complaints.get("can_related_complaints", 0)
            complaints = {
                "text": (f"{parsed.vehicle_make}: NHTSA'da CAN/elektronik ile ilgili {n} şikâyet kaydı var (genel; bu arızaya özgü değil)."
                         if lang == "tr" else
                         f"{parsed.vehicle_make}: {n} CAN/electronics-related NHTSA complaints on file (general; not specific to this fault)."),
                "ref": r.complaints.get("ref", ""),
            }
        ans.recalls = {"note": _t("recall_note", lang), "items": list(r.recalls), "complaints": complaints}
    ans.understood = parsed.to_dict()
    ans.technical["glossary"] = _glossary(ans, kb, lang)
    ans.technical["sources"] = ans.all_refs()
    return ans

"""Drive-safety risk policy: severity + context -> RED/YELLOW/GREEN/GRAY (K1).

Deterministic, monotonic mapping (plan §1 Mimari Kurallar):
- CRITICAL_STOP -> RED; MEDIUM -> YELLOW; LOW -> GREEN; bilinmiyor -> GRAY.
- Veri yoksunluğu ASLA yeşil göstermez ("bilmiyorum" = GRAY).
- Yükseltme kuralları (fail-safe, yalnız yukarı): yanıp sönen motor ışığı
  semptomu -> RED; EV/HV kod (P0A*, izolasyon, HVIL) -> her durumda RED.
- Tüm sürüş önerileri sabit şablondur (doküman §6 birebir); serbest metin
  üretimi YOKTUR.

Fully offline; core-model + ai-package imports only (isolation invariant).
"""

from __future__ import annotations

from typing import Literal

RiskLevel = Literal["RED", "YELLOW", "GREEN", "GRAY"]

# AI dialogue action types & authority charter (FAZ 0)
AIDialogueActionType = Literal[
    "QUESTION",
    "REQUEST_MEASUREMENT",
    "PROPOSE_READ",
    "PROPOSE_WRITE",
    "PROPOSE_ROUTINE",
    "PROPOSE_FLASH",
    "PROPOSE_DTC_CLEAR",
]

READ_ONLY_AI_ACTIONS: frozenset[str] = frozenset({
    "QUESTION",
    "REQUEST_MEASUREMENT",
    "PROPOSE_READ",
})

MUTATING_AI_ACTIONS: frozenset[str] = frozenset({
    "PROPOSE_WRITE",
    "PROPOSE_ROUTINE",
    "PROPOSE_FLASH",
    "PROPOSE_DTC_CLEAR",
})

# Doküman §6 şablonları birebir — değiştirilemez sabit tablo.
RISK_ADVICE: dict[RiskLevel, str] = {
    "RED": (
        "Aracı güvenli bir şekilde durdurun.\n"
        "Motoru kapatın ve tekrar çalıştırmayın.\n"
        "Çekici veya yol yardım çağırın."
    ),
    "YELLOW": (
        "Aracı kullanabilirsiniz ama motoru zorlamayın.\n"
        "Yüksek devir, römork, yokuş ve uzun süreli sürüşten kaçının.\n"
        "En kısa sürede servise gidin."
    ),
    "GREEN": (
        "Şu an acil bir durum görülmüyor.\n"
        "Hatayı takip edin ve bakım planına ekleyin.\n"
        "Belirti oluşursa tekrar tarama yapın."
    ),
    "GRAY": (
        "Bu konuda yeterli veri yok.\n"
        "Güvenliğiniz için profesyonel kontrol önerilir."
    ),
}

# Doküman §24 EV/HV uyarı metinleri birebir.
EV_HV_WARNING_TR = (
    "Yüksek voltaj sistemi tehlikeli olabilir.\n"
    "Turuncu kablolara dokunmayın.\n"
    "Yetkisiz müdahale yapmayın.\n"
    "Aracı şarj etmeyi durdurun ve yetkili servis çağırın."
)

# Severity -> risk tek yönlü, monotonic tablo (plan §1).
_SEVERITY_RISK: dict[str, RiskLevel] = {
    "CRITICAL_STOP": "RED",
    "CRITICAL": "RED",
    "HIGH": "RED",
    "MEDIUM": "YELLOW",
    "LOW": "GREEN",
    "INFO": "GREEN",
}

# EV/HV kod aileleri: her durumda RED (doküman §5/§24).
_EV_HV_PREFIXES: tuple[str, ...] = ("P0A",)
_EV_HV_KEYWORDS: tuple[str, ...] = ("HVIL", "IZOLASYON", "İZOLASYON")


def is_ev_hv_code(code: str) -> bool:
    """True when a DTC code belongs to the EV/high-voltage family (P0A*, HVIL, izolasyon)."""
    cleaned = (code or "").strip().upper()
    if not cleaned:
        return False
    if any(cleaned.startswith(p) for p in _EV_HV_PREFIXES):
        return True
    return any(k in cleaned for k in _EV_HV_KEYWORDS)


def decide_risk(
    severity: str,
    ev_hv_codes: list[str] | None = None,
    mil_flashing_symptom: bool = False,
) -> RiskLevel:
    """Monotonic severity->risk mapping with fail-safe escalation only.

    - Unknown/empty severity -> GRAY (never GREEN: "veri yok asla yeşil değil").
    - EV/HV code present -> RED regardless of severity.
    - MIL flashing symptom -> RED escalation (doküman §5).
    - Escalation is one-way: a risk is never lowered by another rule.
    """
    risk = _SEVERITY_RISK.get((severity or "").strip().upper(), "GRAY")
    if any(is_ev_hv_code(c) for c in (ev_hv_codes or [])):
        risk = "RED"
    if mil_flashing_symptom and risk != "RED":
        risk = "RED"
    return risk


def risk_advice(risk: RiskLevel) -> str:
    """Fixed drive-advice template for a risk level (doküman §6 birebir)."""
    return RISK_ADVICE[risk]


def validate_ai_dialogue_action(
    action_type: str,
    confirmed_by_operator: bool = False,
    vehicle_speed_kmh: float | None = None,
) -> tuple[bool, str]:
    """Validate whether an AI dialogue initiative is permissible under safety policy.

    - READ_ONLY_AI_ACTIONS (QUESTION, REQUEST_MEASUREMENT, PROPOSE_READ) are non-mutating
      and allowed freely by the dialogue engine.
    - MUTATING_AI_ACTIONS (PROPOSE_WRITE, PROPOSE_ROUTINE, PROPOSE_FLASH, PROPOSE_DTC_CLEAR)
      STRICTLY require affirmative operator confirmation AND confirmed stationary speed (0.0 km/h).
    - Missing speed (None), negative speed, or speed > 0.0 fail closed immediately.
    """
    clean_action = (action_type or "").strip().upper()
    if clean_action in READ_ONLY_AI_ACTIONS:
        return True, "Eylem bilgi toplama/okuma amaçlıdır; izin verildi."

    if clean_action in MUTATING_AI_ACTIONS:
        if not confirmed_by_operator:
            return False, "Operatör çift onayı olmadan mutating eylem yürütülemez."
        if vehicle_speed_kmh is None:
            return False, "Araç hız telemetrisi eksik: interlock doğrulanamadı (fail-closed)."
        if vehicle_speed_kmh != 0.0:
            return False, f"Araç hareketsiz değil ({vehicle_speed_kmh:.1f} km/h): mutating eylem engellendi."
        return True, "Operatör onaylı ve araç sabit: mutating eyleme izin verildi."

    return False, f"Bilinmeyen veya yetkisiz eylem tipi: '{action_type}'"


__all__ = [
    "RiskLevel",
    "RISK_ADVICE",
    "EV_HV_WARNING_TR",
    "AIDialogueActionType",
    "READ_ONLY_AI_ACTIONS",
    "MUTATING_AI_ACTIONS",
    "decide_risk",
    "risk_advice",
    "is_ev_hv_code",
    "validate_ai_dialogue_action",
]

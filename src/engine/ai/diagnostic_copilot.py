"""AI Diagnostic Copilot & Automated Telemetry Intelligence Engine.

Provides multi-domain root-cause analysis, dynamic fault correlation, offline Causal Bayesian
inference, Turkish/English automotive NLP tokenization, and optional live Google Gemini / OpenAI Cloud LLMs.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from src.core.logging import get_logger
from src.safety.secret_provider import SecretProvider

logger = get_logger("engine.ai_copilot")

# Single endpoint constant — the API key travels in the x-goog-api-key
# header, never in the URL (CWE-598). Keep the model name in sync with
# README (F-42 / E-9).
#
# Model rotation without a code change: UCAN_GEMINI_MODEL overrides the
# default (e.g. "gemini-2.5-flash"). The endpoint is derived lazily so the
# override applies process-wide; invalid values simply keep the default.
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"

_GEMINI_MODEL_CANDIDATES = (
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-1.5-flash",
)


def _resolve_gemini_model() -> str:
    override = os.environ.get("UCAN_GEMINI_MODEL", "").strip()
    if override and override in _GEMINI_MODEL_CANDIDATES:
        return override
    return DEFAULT_GEMINI_MODEL


def gemini_endpoint() -> str:
    """Resolved Gemini generateContent endpoint (model from env override)."""
    return f"https://generativelanguage.googleapis.com/v1beta/models/{_resolve_gemini_model()}:generateContent"


class FaultSeverity(Enum):
    """AI Risk and Urgency Assessment."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    CRITICAL_STOP = "CRITICAL_STOP"


def map_severity_or_default(raw: Any) -> FaultSeverity:
    """Parse a severity string from an LLM response without raising (L-9).

    An LLM may emit an unmodeled severity word — failing the entire
    analysis on enum conversion would discard a otherwise valid report, so
    unknown values fall back to MEDIUM (visible urgency, not silent).
    """
    try:
        return FaultSeverity(str(raw))
    except ValueError:
        return FaultSeverity.MEDIUM


@dataclass(slots=True)
class TroubleshootingStep:
    """Actionable step recommended by the AI."""

    step_number: int
    action: str
    target_component: str
    difficulty: str  # "Kolay (Görsel)" | "Orta (Alet Gerekir)" | "İleri (Servis)"


@dataclass(slots=True)
class DiagnosticAnalysisReport:
    """Comprehensive AI-generated diagnostic analysis."""

    summary: str
    severity: FaultSeverity
    root_cause_probability: str
    likely_causes: list[str]
    troubleshooting_steps: list[TroubleshootingStep]
    affected_subsystems: list[str]
    raw_dtc_count: int
    telemetry_correlations: list[str]
    ai_model_used: str = "Yerel Otomotiv Uzman Motoru (Çevrimdışı)"
    timestamp_ns: int = field(default_factory=time.time_ns)


@dataclass(slots=True)
class CopilotActionTrigger:
    """Structured actionable diagnostic routine trigger metadata for UI buttons."""

    id: str
    label: str
    action_type: str  # "uds_clear_dtc", "uds_read_did", "uds_session_control", "uds_routine", "uds_ecu_reset", "j1939_clear_dtc", "j1939_dm1_query"
    params: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = True
    confirm_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "action_type": self.action_type,
            "params": self.params,
            "requires_confirmation": self.requires_confirmation,
            "confirm_text": self.confirm_text,
        }


def make_uds_clear_dtc_action(group: int = 0xFFFFFF) -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_uds_0x14_clear_dtc",
        label="▶️ UDS 0x14 DTC Temizle",
        action_type="uds_clear_dtc",
        params={"group": group},
        requires_confirmation=True,
        confirm_text="Aktif ve geçmiş tüm DTC arıza kodları ECU hafızasından silinecektir. Devam edilsin mi?",
    ).to_dict()


def make_uds_read_vin_action() -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_uds_0x22_f190_vin",
        label="▶️ UDS 0x22 F190 VIN Oku",
        action_type="uds_read_did",
        params={"did": 0xF190, "name": "VIN"},
        requires_confirmation=False,
    ).to_dict()


def make_uds_session_action(session_type: int = 3) -> dict[str, Any]:
    session_names = {1: "Default", 2: "Programming", 3: "Extended", 4: "Safety"}
    s_name = session_names.get(session_type, hex(session_type))
    return CopilotActionTrigger(
        id=f"act_uds_0x10_session_{session_type}",
        label=f"▶️ UDS 0x10 {s_name} Session",
        action_type="uds_session_control",
        params={"session_type": session_type},
        requires_confirmation=True,
        confirm_text=f"Teşhis oturumu '{s_name} (0x{session_type:02X})' moduna geçirilecektir. Onaylıyor musunuz?",
    ).to_dict()


def make_uds_routine_action(routine_id: int, name: str = "") -> dict[str, Any]:
    lbl = f"▶️ UDS 0x31 Rutin (0x{routine_id:04X})" if not name else f"▶️ UDS 0x31 {name}"
    return CopilotActionTrigger(
        id=f"act_uds_0x31_routine_{routine_id:04x}",
        label=lbl,
        action_type="uds_routine",
        params={"routine_id": routine_id, "name": name},
        requires_confirmation=True,
        confirm_text=f"0x{routine_id:04X} nolu diagnostik rutin çalıştırılacaktır. Onaylıyor musunuz?",
    ).to_dict()


def make_uds_ecu_reset_action(reset_type: int = 1) -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_uds_0x11_ecu_reset",
        label="▶️ UDS 0x11 ECU Reset",
        action_type="uds_ecu_reset",
        params={"reset_type": reset_type},
        requires_confirmation=True,
        confirm_text="ECU donanımsal olarak yeniden başlatılacaktır (Hard Reset). Onaylıyor musunuz?",
    ).to_dict()


def make_j1939_dm11_action() -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_j1939_dm11_clear",
        label="▶️ J1939 DM11 Arıza Temizle",
        action_type="j1939_clear_dtc",
        params={"pgn": 65235},
        requires_confirmation=True,
        confirm_text="Ağır vasıta J1939 aktif arıza kayıtları (DM11 PGN 65235) silinecektir. Onaylıyor musunuz?",
    ).to_dict()


def make_j1939_dm1_action() -> dict[str, Any]:
    return CopilotActionTrigger(
        id="act_j1939_dm1_query",
        label="▶️ J1939 DM1 Arıza Oku",
        action_type="j1939_dm1_query",
        params={"pgn": 65226},
        requires_confirmation=False,
    ).to_dict()


def attach_action_triggers(text: str, actions: list[dict[str, Any]]) -> str:
    """Append structured JSON metadata comment to copilot response text."""
    if not actions:
        return text
    unique_actions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for a in actions:
        aid = a.get("id")
        if aid and aid not in seen_ids:
            seen_ids.add(aid)
            unique_actions.append(a)
    if not unique_actions:
        return text
    meta_json = json.dumps(unique_actions, ensure_ascii=False)
    return f"{text}\n<!--ACTIONS:{meta_json}-->"


def parse_action_triggers_from_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse out structured action triggers from text comment, or extract if not found."""
    match = re.search(r"<!--ACTIONS:(.*?)-->", text, re.DOTALL)
    if match:
        clean_text = text[: match.start()].rstrip() + text[match.end() :]
        try:
            actions = json.loads(match.group(1).strip())
            if isinstance(actions, list):
                return clean_text, actions
        except Exception:
            pass
    return text, extract_action_triggers(text)


# P1 fix: context-aware routine identifier patterns. The legacy scanner
# grabbed the FIRST 4-digit hex anywhere in the text, so a query like
# "CAN ID 0x18F0 rutin" produced a bogus routine button. Order: tight
# "routine/rutin id 0xNNNN" forms, then a bounded window after the keyword,
# then raw request bytes "0x31 0xNNNN", then bare hex after the keyword.
_ROUTINE_ID_PATTERNS: tuple[str, ...] = (
    r"(?:routine|rutin|rid)\s*(?:id)?\s*[:=]?\s*0x([0-9a-f]{4})\b",
    r"(?:routine|rutin)\s+(?:id)?\s*(?:0x)?([0-9a-f]{4})\b",
    r"(?:routine|rutin)[^0-9a-f]{0,30}?0x([0-9a-f]{4})\b",
    r"0x31\s+0x([0-9a-f]{4})\b",
)


def _extract_routine_id(combined_text: str) -> int:
    """Extract the routine identifier from lowered copilot text, honestly.

    Returns the OEM default 0xD001 (HVIL interlock loopback) when no
    explicit routine identifier is stated — same legacy default the
    desktop bridge expects.
    """
    for pattern in _ROUTINE_ID_PATTERNS:
        m = re.search(pattern, combined_text)
        if m:
            return int(m.group(1), 16)
    return 0xD001


def extract_action_triggers(text: str, user_query: str = "") -> list[dict[str, Any]]:
    """Scan response text and user query for actionable diagnostic recommendations."""
    combined = f"{user_query} {text}".lower()
    actions: list[dict[str, Any]] = []
    seen_types: set[str] = set()

    if any(k in combined for k in ["0x14", "dtc temizle", "clear dtc", "hata kodlarını sil", "hafızasını sil", "hafızasını temizle", "kodlarını temizle"]):
        actions.append(make_uds_clear_dtc_action())
        seen_types.add("uds_clear_dtc")

    if any(k in combined for k in ["f190", "vin oku", "şasi no", "read vin", "chassis number"]):
        actions.append(make_uds_read_vin_action())
        seen_types.add("uds_read_did")

    if any(k in combined for k in ["extended session", "genişletilmiş oturum", "0x10 0x03", "0x10"]):
        if "uds_session_control" not in seen_types:
            actions.append(make_uds_session_action(3))
            seen_types.add("uds_session_control")

    if "0x31" in combined or "routine" in combined or "rutin" in combined:
        rid = _extract_routine_id(combined)
        actions.append(make_uds_routine_action(rid))
        seen_types.add("uds_routine")

    if "dm11" in combined or "pgn 65235" in combined:
        actions.append(make_j1939_dm11_action())
        seen_types.add("j1939_clear_dtc")
    if "dm1" in combined or "pgn 65226" in combined:
        if "j1939_dm1_query" not in seen_types:
            actions.append(make_j1939_dm1_action())
            seen_types.add("j1939_dm1_query")

    return actions


def explain_traffic_metrics(bus_metrics: dict[str, Any], user_query: str = "") -> str:
    """Generate concise 2-3 line CAN traffic and anomaly diagnostic report."""
    bus_load = bus_metrics.get("bus_load_percent", 0)
    error_count = bus_metrics.get("error_count", 0)
    anomalies = list(bus_metrics.get("anomalies", []))
    total_pkts = bus_metrics.get("total_packets", 0)
    babbling = bus_metrics.get("babbling_node")
    if babbling and not any(str(babbling) in a for a in anomalies):
        anomalies.append(f"Babbling Node: {babbling}")

    if bus_load > 75 or error_count > 5 or anomalies:
        status_tag = "⚠️ **KRİTİK ANOMALİ ALARMI**"
        line1 = f"📊 **Veri Yolu Trafik Analizi & Anomali Raporu ({status_tag}):**"
        line2 = f"• **Veri Yolu Yükü:** %{bus_load} (Eşik >%75) | Hata Karesi: {error_count} adet | Toplam: {total_pkts} paket"
        anom_desc = " • ".join(anomalies).rstrip(".") if anomalies else "Hat üzerinde yüksek yük veya hata karesi patlaması mevcut"
        line3 = f"• **Teşhis:** {anom_desc}. 120Ω sonlandırma direncini ve fiziksel katman voltajlarını (CAN-H/CAN-L) inceleyin."
    else:
        line1 = "📊 **Veri Yolu Trafik Analizi & Hat Durumu:**"
        line2 = f"• **Veri Yolu Yükü:** %{bus_load} (Nominal) | Hata Karesi: {error_count} adet | Toplam: {total_pkts} paket"
        line3 = "• **Teşhis:** CAN veri yolu nominal hız ve frekansta çalışıyor. Anomali veya babbling node tespit edilmedi."
    return f"{line1}\n{line2}\n{line3}"


# Weighted evidence kinds for local-expert root-cause confidence, mirroring
# engine/discovery/evidence.py methodology (P0 honesty fix: replaces the old
# hardcoded "%94 Belirlenimsiz Güvenilirlik" placeholder). Rule scenario and
# knowledge-base matches are complementary identification evidence — a DTC
# explained by a rule does not also require a KB hit.
ROOT_CAUSE_EVIDENCE_WEIGHTS: dict[str, float] = {
    "identification": 0.50,
    "telemetry_correlation": 0.30,
    "dtc_context": 0.20,
}


def compute_root_cause_confidence(
    dtc_count: int,
    scenario_matched: int,
    kb_matched: int,
    telemetry_correlation_count: int,
) -> str:
    """Compute an honest weighted root-cause confidence label for the offline expert.

    Each evidence kind is normalised to 0..1, weighted, and the weighted mean
    is taken over the total weight — so a full rule+telemetry match scores
    high while an unknown DTC with no corroboration scores near zero.
    """
    if dtc_count <= 0:
        return "Normal"
    identified = scenario_matched + kb_matched
    entries = {
        "identification": min(1.0, identified / dtc_count),
        "telemetry_correlation": min(1.0, telemetry_correlation_count / 2.0),
        "dtc_context": min(1.0, dtc_count / 2.0),
    }
    total_weight = sum(ROOT_CAUSE_EVIDENCE_WEIGHTS.values())
    weighted_sum = sum(ROOT_CAUSE_EVIDENCE_WEIGHTS[kind] * value for kind, value in entries.items())
    score = weighted_sum / total_weight
    label = "Yüksek" if score >= 0.65 else ("Orta" if score >= 0.35 else "Düşük")
    return f"{label} (%{score * 100:.0f} ağırlıklı kanıt skoru)"


def extract_hex_payload_from_query(query: str) -> list[int]:
    """Extract byte values from query string."""
    match = re.search(r"(?:Hex Payload|Payload|Data|Veri)\s*[:=]?\s*([0-9A-Fa-f\s]{2,})", query, re.IGNORECASE)
    candidate = match.group(1) if match else query
    cleaned = re.sub(r"\b0x[0-9A-Fa-f]{3,8}\b", "", candidate)
    tokens = re.findall(r"\b[0-9A-Fa-f]{2}\b", cleaned)
    return [int(t, 16) for t in tokens]


_FALLBACK_DBC_DECODER: Any = None
_FALLBACK_DBC_LOCK = threading.Lock()


def _get_fallback_dbc_decoder() -> Any:
    """Lazy-load DBC decoder for fallback signal decoding."""
    global _FALLBACK_DBC_DECODER
    if _FALLBACK_DBC_DECODER is not None:
        return _FALLBACK_DBC_DECODER
    with _FALLBACK_DBC_LOCK:
        if _FALLBACK_DBC_DECODER is not None:
            return _FALLBACK_DBC_DECODER
        try:
            from src.engine.decoder.dbc_decoder import DbcSignalDecoder
            cand = _DBC_DATA_DIR / "heavy_duty" / "j1939_canboat.dbc"
            if cand.exists():
                _FALLBACK_DBC_DECODER = DbcSignalDecoder.from_dbc_file(cand)
                return _FALLBACK_DBC_DECODER
        except Exception as exc:
            logger.debug("Fallback DBC decoder initialization skipped: %s", exc)
        return None


# ISO 15765-2 (ISO-TP) UDS service identifiers recognised by the packet
# explainer — requests and positive/negative responses.
_UDS_KNOWN_SIDS: frozenset[int] = frozenset(
    {
        0x10, 0x11, 0x14, 0x19, 0x22, 0x27, 0x28, 0x2E, 0x31, 0x3E,
        0x50, 0x51, 0x54, 0x59, 0x62, 0x67, 0x71, 0x7F, 0x01,
    }
)


def _resolve_uds_sid_index(payload_bytes: list[int]) -> int:
    """Resolve the byte offset of the UDS SID inside an ISO-TP framed payload.

    ISO 15765-2 PCI types (upper nibble of byte 0):
      0x0 = Single Frame (SF)  -> SID at byte 1
      0x1 = First Frame (FF)  -> SID at byte 2 (bytes 0-1 are PCI+length)
      0x2 = Consecutive Frame (CF) -> no reliable SID position
      0x3 = Flow Control (FC) -> not a service payload

    P1 fix: the legacy heuristic could misread a First Frame such as
    ``10 14 ...`` as SF with SID 0x14 (ClearDiagnosticInformation). With the
    explicit PCI nibble check, multi-frame requests are offset correctly and
    CF/FC fragments are never mistaken for fresh service requests.
    """
    if not payload_bytes:
        return 0
    pci_type = (payload_bytes[0] >> 4) & 0xF
    if pci_type == 0x0 and len(payload_bytes) > 1:
        return 1  # Single Frame: [0|len, SID, ...]
    if pci_type == 0x1 and len(payload_bytes) > 2:
        return 2  # First Frame: [0x1x, len_hi, len_lo, SID, ...]
    if pci_type in (0x2, 0x3):
        return 0  # CF fragment / FC frame — no reliable SID position
    # Unframed payload (e.g. raw physical request): SID at byte 0 unless
    # byte 0 is not a known SID while byte 1 is.
    if payload_bytes[0] not in _UDS_KNOWN_SIDS and len(payload_bytes) > 1 and payload_bytes[1] in _UDS_KNOWN_SIDS:
        return 1
    return 0


def explain_can_packet(
    can_id_hex_or_int: str | int,
    payload: bytes | list[int] | str = b"",
) -> tuple[str, list[dict[str, Any]]]:
    """Break down a CAN frame payload into a concise 2-3 line explanation with action triggers."""
    if isinstance(can_id_hex_or_int, str):
        cleaned_id = can_id_hex_or_int.strip().lower()
        if cleaned_id.startswith("0x"):
            can_id = int(cleaned_id, 16)
        else:
            can_id = int(cleaned_id, 16) if all(c in "0123456789abcdef" for c in cleaned_id) else 0
    else:
        can_id = int(can_id_hex_or_int)

    if isinstance(payload, str):
        payload_bytes = [int(t, 16) for t in re.findall(r"\b[0-9A-Fa-f]{2}\b", payload)]
    elif isinstance(payload, bytes):
        payload_bytes = list(payload)
    else:
        payload_bytes = list(payload)

    # 1. UDS Diagnostics (0x7DF or 0x7E0..0x7EF)
    if (0x7E0 <= can_id <= 0x7EF) or can_id == 0x7DF:
        if payload_bytes:
            sid_idx = _resolve_uds_sid_index(payload_bytes)

            sid = payload_bytes[sid_idx]

            # 0x10 Diagnostic Session Control
            if sid == 0x10:
                subfn = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                sub_names = {1: "Default", 2: "Programming", 3: "Extended", 4: "Safety System"}
                s_name = sub_names.get(subfn & 0x7F, f"0x{subfn:02X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x10):**"
                line2 = f"• **Servis:** `0x10 DiagnosticSessionControl` — {s_name} Session (0x{subfn:02X})"
                line3 = f"• **Anlam:** ECU'dan {s_name} oturumuna geçiş talep ediliyor."
                actions = [make_uds_session_action(subfn & 0x7F)]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x11 ECU Reset
            if sid == 0x11:
                rt = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                rt_names = {1: "Hard Reset", 2: "Key Off/On Reset", 3: "Soft Reset"}
                rt_name = rt_names.get(rt & 0x7F, f"Reset Tipi 0x{rt:02X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x11):**"
                line2 = f"• **Servis:** `0x11 ECUReset` — {rt_name} (0x{rt:02X})"
                line3 = "• **Anlam:** ECU işlemcisinin donanımsal/yazılımsal yeniden başlatılması talep ediliyor."
                actions = [make_uds_ecu_reset_action(rt & 0x7F)]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x14 Clear Diagnostic Information
            if sid == 0x14:
                dtc_grp = 0xFFFFFF
                if len(payload_bytes) >= sid_idx + 4:
                    dtc_grp = (payload_bytes[sid_idx + 1] << 16) | (payload_bytes[sid_idx + 2] << 8) | payload_bytes[sid_idx + 3]
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x14):**"
                line2 = f"• **Servis:** `0x14 ClearDiagnosticInformation` (DTC Grup: 0x{dtc_grp:06X})"
                line3 = "• **Anlam:** ECU hafızasındaki aktif ve geçmiş tüm DTC arıza kodlarının silinmesi talep ediliyor."
                actions = [make_uds_clear_dtc_action(dtc_grp)]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x19 Read DTC Information
            if sid == 0x19:
                subfn = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 2
                mask = payload_bytes[sid_idx + 2] if len(payload_bytes) > sid_idx + 2 else 0xFF
                sub_names = {
                    1: "reportNumberOfDTCByStatusMask",
                    2: "reportDTCByStatusMask",
                    4: "reportDTCSnapshotRecordByDTCNumber",
                    6: "reportDTCExtendedDataRecordByDTCNumber",
                }
                sub_name = sub_names.get(subfn, f"SubFunction 0x{subfn:02X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x19):**"
                line2 = f"• **Servis:** `0x19 ReadDTCInformation` — {sub_name} (Maske: 0x{mask:02X})"
                line3 = "• **Anlam:** ECU hata belleğindeki kayıtlı DTC arıza kodları ve durum maskesi sorgulanıyor."
                actions = [make_uds_clear_dtc_action()]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x22 Read Data By Identifier
            if sid == 0x22:
                did = (payload_bytes[sid_idx + 1] << 8 | payload_bytes[sid_idx + 2]) if len(payload_bytes) >= sid_idx + 3 else 0
                known_dids = {
                    0xF190: "VIN (Araç Şasi Numarası)",
                    0xF187: "Yedek Parça Numarası",
                    0xF189: "ECU Yazılım Versiyonu",
                    0xF197: "Sistem Adı",
                    0x1102: "Common Rail Yakıt Basıncı",
                    0x4100: "EV Batarya Hücre Voltaj Haritası",
                    0x4101: "EV Min/Max Hücre Voltajı",
                    0x4102: "HVIL Sensör Voltajı",
                    0x4105: "Batarya Sıcaklık Dağılımı",
                }
                did_name = known_dids.get(did, f"DID 0x{did:04X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x22):**"
                line2 = f"• **Servis:** `0x22 ReadDataByIdentifier` — 0x{did:04X} ({did_name})"
                line3 = f"• **Anlam:** ECU'dan {did_name} parametresinin anlık telemetri değeri sorgulanıyor."
                actions = [make_uds_read_vin_action()] if did == 0xF190 else []
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x27 Security Access
            if sid == 0x27:
                sec_sub = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                sec_mode = "Request Seed" if (sec_sub % 2 == 1) else "Send Key"
                line1 = f"📦 **UDS Güvenlik Paketi (ID: 0x{can_id:03X} / SID 0x27):**"
                line2 = f"• **Servis:** `0x27 SecurityAccess` — {sec_mode} (Seviye 0x{sec_sub:02X})"
                line3 = "• **Anlam:** ECU'nun korumalı teşhis ve programlama alanlarına erişim anahtarı doğrulanıyor."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x28 Communication Control
            if sid == 0x28:
                ctrl_type = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                c_names = {0: "enableRxAndTx", 1: "enableRxAndDisableTx", 3: "disableRxAndTx"}
                c_name = c_names.get(ctrl_type, f"0x{ctrl_type:02X}")
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x28):**"
                line2 = f"• **Servis:** `0x28 CommunicationControl` — {c_name}"
                line3 = "• **Anlam:** CAN veri yolu üzerindeki normal mesaj iletimi geçici olarak durduruluyor/açılıyor."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x2E Write Data By Identifier
            if sid == 0x2E:
                did = (payload_bytes[sid_idx + 1] << 8 | payload_bytes[sid_idx + 2]) if len(payload_bytes) >= sid_idx + 3 else 0
                data_hex = " ".join(f"{b:02X}" for b in payload_bytes[sid_idx + 3:])
                line1 = f"📦 **UDS Yazma Paketi (ID: 0x{can_id:03X} / SID 0x2E):**"
                line2 = f"• **Servis:** `0x2E WriteDataByIdentifier` — DID 0x{did:04X}"
                line3 = f"• **Anlam:** ECU parametresi üzerine yeni değer yazılıyor (Veri: `{data_hex or 'Boş'}`)."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x31 Routine Control
            if sid == 0x31:
                ctrl_type = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                rid = (payload_bytes[sid_idx + 2] << 8 | payload_bytes[sid_idx + 3]) if len(payload_bytes) >= sid_idx + 4 else 0
                ctrl_names = {1: "Start Routine", 2: "Stop Routine", 3: "Request Routine Results"}
                line1 = f"📦 **UDS Teşhis Paketi (ID: 0x{can_id:03X} / SID 0x31):**"
                line2 = f"• **Servis:** `0x31 RoutineControl` — {ctrl_names.get(ctrl_type, 'Routine')} (ID: 0x{rid:04X})"
                line3 = f"• **Anlam:** ECU üzerinde 0x{rid:04X} nolu teşhis veya kalibrasyon rutini yürütülüyor."
                actions = [make_uds_routine_action(rid)]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x3E Tester Present
            if sid == 0x3E:
                subfn = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                suppress = bool(subfn & 0x80)
                line1 = f"📦 **UDS Keep-Alive Paketi (ID: 0x{can_id:03X} / SID 0x3E):**"
                line2 = f"• **Servis:** `0x3E TesterPresent` (Yanıt Bastırma={suppress})"
                line3 = "• **Anlam:** Tanı oturumunun zaman aşımına uğramasını önlemek için periyodik sinyal iletiliyor."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x7F Negative Response
            if sid == 0x7F:
                rej_sid = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                nrc = payload_bytes[sid_idx + 2] if len(payload_bytes) > sid_idx + 2 else 0
                nrc_hex = f"0x{nrc:02X}"
                nrc_info = UDS_NRC_CATALOG.get(nrc_hex, {"name": "Genel Red", "cause": "ECU işlemi reddetti", "action": "Ön koşulları kontrol edin"})
                line1 = f"🛑 **UDS Negatif Yanıt (ID: 0x{can_id:03X} / NRC {nrc_hex}):**"
                line2 = f"• **Reddedilen Servis:** `0x{rej_sid:02X}` | Hata: {nrc_info['name']}"
                line3 = f"• **Neden:** {nrc_info['cause']}. Çözüm: {nrc_info['action']}"
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x50 Positive Response (Diagnostic Session Control)
            if sid == 0x50:
                st = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x50):**"
                line2 = f"• **Servis:** `0x10 DiagnosticSessionControl` Onaylandı (Oturum: 0x{st:02X})"
                line3 = "• **Sonuç:** ECU talep edilen teşhis oturumuna başarıyla geçti."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x51 Positive Response (ECU Reset)
            if sid == 0x51:
                rt = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 1
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x51):**"
                line2 = f"• **Servis:** `0x11 ECUReset` Başarılı (Reset Tipi: 0x{rt:02X})"
                line3 = "• **Sonuç:** ECU yeniden başlatma komutunu kabul etti ve sıfırlanıyor."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x54 Positive Response (Clear DTC)
            if sid == 0x54:
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x54):**"
                line2 = "• **Servis:** `0x14 ClearDiagnosticInformation` Başarıyla Tamamlandı"
                line3 = "• **Sonuç:** ECU hata hafızası sıfırlandı. Arıza kodları başarıyla temizlendi."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x59 Positive Response (Read DTC Information)
            if sid == 0x59:
                subfn = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 2
                mask = payload_bytes[sid_idx + 2] if len(payload_bytes) > sid_idx + 2 else 0
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x59):**"
                line2 = f"• **Servis:** `0x19 ReadDTCInformation` Başarılı Yanıt (Durum Maskesi: 0x{mask:02X})"
                line3 = "• **İçerik:** ECU arıza kodları raporlandı. Arıza temizleme için UDS 0x14 kullanılabilir."
                actions = [make_uds_clear_dtc_action()]
                return (f"{line1}\n{line2}\n{line3}", actions)

            # 0x62 Positive Response (Read DID)
            if sid == 0x62:
                did = (payload_bytes[sid_idx + 1] << 8 | payload_bytes[sid_idx + 2]) if len(payload_bytes) >= sid_idx + 3 else 0
                data_tail = payload_bytes[sid_idx + 3:]
                val_str = ""
                if did == 0xF190 and data_tail:
                    ascii_str = "".join(chr(b) for b in data_tail if 32 <= b <= 126)
                    val_str = f" Araç VIN: `{ascii_str}` |" if ascii_str else ""
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x62):**"
                line2 = f"• **Servis:** `0x22 Read DID 0x{did:04X}` Başarılı Yanıt"
                line3 = f"• **İçerik:**{val_str} Veri baytları: `{' '.join(f'{b:02X}' for b in data_tail[:8])}`"
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x67 Positive Response (Security Access)
            if sid == 0x67:
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x67):**"
                line2 = "• **Servis:** `0x27 SecurityAccess` Kilit Açıldı (Security Unlocked)"
                line3 = "• **Sonuç:** Güvenlik katmanı doğrulandı. Korumalı rutin ve yazma işlemleri aktif."
                return (f"{line1}\n{line2}\n{line3}", [])

            # 0x71 Positive Response (Routine Control)
            if sid == 0x71:
                rid = (payload_bytes[sid_idx + 2] << 8 | payload_bytes[sid_idx + 3]) if len(payload_bytes) >= sid_idx + 4 else 0
                line1 = f"✅ **UDS Pozitif Yanıt (ID: 0x{can_id:03X} / SID 0x71):**"
                line2 = f"• **Servis:** `0x31 RoutineControl` Başarıyla Yürütüldü (RID: 0x{rid:04X})"
                line3 = "• **Sonuç:** Teşhis rutini başarıyla tamamlandı."
                return (f"{line1}\n{line2}\n{line3}", [])

            # OBD-II Mode 01
            if sid == 0x01:
                pid = payload_bytes[sid_idx + 1] if len(payload_bytes) > sid_idx + 1 else 0
                pids = {
                    0x04: ("Hesaplanan Motor Yükü", lambda b: f"%{b[0]*100/255:.1f}" if len(b) > 0 else ""),
                    0x05: ("Motor Soğutma Sıvısı Sıcaklığı (ECT)", lambda b: f"{b[0] - 40}°C" if len(b) > 0 else ""),
                    0x0B: ("Emme Manifoldu Basıncı (MAP)", lambda b: f"{b[0]} kPa" if len(b) > 0 else ""),
                    0x0C: ("Motor Devri (RPM)", lambda b: f"{(b[0]*256 + b[1])/4:.0f} RPM" if len(b) > 1 else ""),
                    0x0D: ("Araç Hızı (Speed)", lambda b: f"{b[0]} km/s" if len(b) > 0 else ""),
                    0x0F: ("Emme Havası Sıcaklığı (IAT)", lambda b: f"{b[0] - 40}°C" if len(b) > 0 else ""),
                    0x11: ("Gaz Kelebeği Pozisyonu", lambda b: f"%{b[0]*100/255:.1f}" if len(b) > 0 else ""),
                }
                p_name, calc = pids.get(pid, (f"PID 0x{pid:02X}", lambda b: ""))
                val_txt = calc(payload_bytes[sid_idx + 2:]) if len(payload_bytes) > sid_idx + 2 else ""
                val_s = f" — Değer: {val_txt}" if val_txt else ""
                line1 = f"📡 **OBD-II Canlı Telemetri Sorgusu (ID: 0x{can_id:03X}):**"
                line2 = f"• **Servis:** `Mode 01 PID 0x{pid:02X}` — {p_name}"
                line3 = f"• **Anlam:** Standart OBD-II canlı parametre talebi{val_s}."
                return (f"{line1}\n{line2}\n{line3}", [])

    # 2. J1939 Extended 29-bit Frames
    if can_id > 0x7FF:
        pgn = (can_id >> 8) & 0x3FFFF
        pf = (can_id >> 16) & 0xFF
        if pf < 240:
            pgn = pgn & 0x3FF00

        # PGN 61444 EEC1
        if pgn == 61444:
            rpm_val = ((payload_bytes[4] << 8 | payload_bytes[3]) * 0.125) if len(payload_bytes) >= 5 else 0.0
            torque_val = (payload_bytes[2] - 125) if len(payload_bytes) >= 3 else 0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 61444 / EEC1):**"
            line2 = "• **Sistem:** Elektronik Motor Denetleyicisi 1 (Electronic Engine Controller 1)"
            line3 = f"• **Çözülen Sinyaller:** Motor Devri: `{rpm_val:.0f} RPM`, Aktüel Motor Torku: `%{torque_val}`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65265 CCVS
        if pgn == 65265:
            speed_kmh = ((payload_bytes[2] << 8 | payload_bytes[1]) / 256.0) if len(payload_bytes) >= 3 else 0.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65265 / CCVS):**"
            line2 = "• **Sistem:** Seyir Kontrolü & Araç Hızı (Cruise Control & Vehicle Speed)"
            line3 = f"• **Çözülen Sinyaller:** Tekerlek Tabanlı Araç Hızı: `{speed_kmh:.1f} km/s`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65226 DM1
        if pgn == 65226:
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65226 / DM1):**"
            line2 = "• **Sistem:** Aktif Diyagnostik Hata Kodları (Active Diagnostic Trouble Codes)"
            line3 = "• **Anlam:** Araçtaki aktif arıza lambası (MIL/AWL) ve mevcut SPN/FMI hata durumunu bildirir."
            actions = [make_j1939_dm1_action()]
            return (f"{line1}\n{line2}\n{line3}", actions)

        # PGN 65235 DM11
        if pgn == 65235:
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65235 / DM11):**"
            line2 = "• **Sistem:** Aktif Arıza Kodlarını Temizleme (Diagnostic Data Clear)"
            line3 = "• **Anlam:** ECU hafızasındaki aktif arıza kodlarının sıfırlanmasını talep eder."
            actions = [make_j1939_dm11_action()]
            return (f"{line1}\n{line2}\n{line3}", actions)

        # PGN 65249 ET1 (Engine Temperature 1)
        if pgn == 65249:
            coolant_t = (payload_bytes[0] - 40) if len(payload_bytes) >= 1 else 0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65249 / ET1):**"
            line2 = "• **Sistem:** Motor Sıcaklığı 1 (Engine Temperature 1)"
            line3 = f"• **Çözülen Sinyaller:** Motor Soğutma Sıvısı Sıcaklığı (SPN 110): `{coolant_t}°C`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65263 EFL_P1 (Engine Fluid Level/Pressure 1)
        if pgn == 65263:
            oil_press_kpa = (payload_bytes[3] * 4) if len(payload_bytes) >= 4 else 0
            oil_bar = oil_press_kpa / 100.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65263 / EFL_P1):**"
            line2 = "• **Sistem:** Motor Sıvı Seviye ve Basınçları 1 (Engine Fluid Level/Pressure 1)"
            line3 = f"• **Çözülen Sinyaller:** Motor Yağ Basıncı (SPN 100): `{oil_bar:.2f} Bar` ({oil_press_kpa} kPa)."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65262 ET2 (Engine Temperature 2)
        if pgn == 65262:
            oil_temp = (((payload_bytes[3] << 8 | payload_bytes[2]) * 0.03125) - 273) if len(payload_bytes) >= 4 else 0.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65262 / ET2):**"
            line2 = "• **Sistem:** Motor Sıcaklığı 2 (Engine Temperature 2)"
            line3 = f"• **Çözülen Sinyaller:** Motor Yağ Sıcaklığı (SPN 175): `{oil_temp:.1f}°C`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65269 AMB (Ambient Conditions)
        if pgn == 65269:
            amb_temp = (((payload_bytes[4] << 8 | payload_bytes[3]) * 0.03125) - 273) if len(payload_bytes) >= 5 else 0.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65269 / AMB):**"
            line2 = "• **Sistem:** Ortam Çevre Koşulları (Ambient Conditions)"
            line3 = f"• **Çözülen Sinyaller:** Dış Ortam Hava Sıcaklığı (SPN 171): `{amb_temp:.1f}°C`."
            return (f"{line1}\n{line2}\n{line3}", [])

        # PGN 65257 LFE (Fuel Economy)
        if pgn == 65257:
            fuel_rate = ((payload_bytes[1] << 8 | payload_bytes[0]) * 0.05) if len(payload_bytes) >= 2 else 0.0
            line1 = f"🚛 **SAE J1939 Paket Analizi (ID: 0x{can_id:08X} - PGN 65257 / LFE):**"
            line2 = "• **Sistem:** Yakıt Ekonomisi (Fuel Economy / Liquid Fuel Economy)"
            line3 = f"• **Çözülen Sinyaller:** Anlık Yakıt Tüketim Debisi (SPN 183): `{fuel_rate:.1f} L/h`."
            return (f"{line1}\n{line2}\n{line3}", [])

    # 3. Known Standard Diagnostic IDs without payload
    if can_id == 0x7DF:
        return ("📡 **CAN ID 0x7DF:** Standart OBD-II Fonksiyonel Yayın İsteği (Tüm bağlı ECU'lara eşzamanlı genel sorgu).", [])
    if 0x7E0 <= can_id <= 0x7E7:
        ecu_name = "Motor (ECM/PCM)" if can_id == 0x7E0 else ("Şanzıman (TCM)" if can_id == 0x7E1 else f"ECU_{can_id - 0x7E0}")
        return (f"📡 **CAN ID 0x{can_id:03X}:** ISO 15765-4 Standart OBD-II / UDS Fiziksel İstek Hattı ({ecu_name}).", [])
    if 0x7E8 <= can_id <= 0x7EF:
        ecu_name = "Motor (ECM/PCM)" if can_id == 0x7E8 else ("Şanzıman (TCM)" if can_id == 0x7E9 else f"ECU_{can_id - 0x7E8}")
        return (f"📡 **CAN ID 0x{can_id:03X}:** ISO 15765-4 Standart OBD-II / UDS Fiziksel Yanıt Hattı ({ecu_name}).", [])

    # 4. DBC Fallback Signal Decoding for any frame with payload
    if payload_bytes:
        decoder = _get_fallback_dbc_decoder()
        if decoder is not None:
            try:
                from src.core.models.can_frame import CanFrame
                is_ext = can_id > 0x7FF
                cf = CanFrame(
                    arbitration_id=can_id,
                    is_extended=is_ext,
                    dlc=len(payload_bytes),
                    data=bytes(payload_bytes[:8]),
                    channel_id="ch0",
                )
                decoded_msg = decoder.decode_frame(cf)
                if decoded_msg and decoded_msg.signals:
                    sig_strs = [
                        f"{s.name}: `{s.value}` {s.unit}".strip()
                        for s in list(decoded_msg.signals.values())[:3]
                    ]
                    line1 = f"📦 **DBC Çözümlenmiş Mesaj (ID: 0x{can_id:X} - {decoded_msg.message_name}):**"
                    line2 = f"• **Sinyaller:** {', '.join(sig_strs)}"
                    line3 = f"• **Detay:** Vector DBC veritabanı ile {len(decoded_msg.signals)} adet sinyal başarıyla çözümlendi."
                    return (f"{line1}\n{line2}\n{line3}", [])
            except Exception:
                pass

    # 5. Fallback for unrecognized frame
    hex_str = " ".join(f"{b:02X}" for b in payload_bytes) if payload_bytes else "Boş"
    return (
        f"⚠️ **CAN ID Tanımsız (0X{can_id:X}):**\n"
        f"Bu mesaj kimliği için yerel veritabanında veya protokol motorunda kayıtlı bir sinyal tanımı bulunamadı.\n"
        f"• Sniffer tablosundan canlı veri uzunluğunu (DLC={len(payload_bytes)}) ve bayt değişimlerini (`{hex_str}`) inceleyebilirsiniz.",
        [],
    )


# ============================================================================
# COMPREHENSIVE MULTI-DOMAIN AUTOMOTIVE KNOWLEDGE BASE (120+ CODES & PROTOCOLS)
# ============================================================================

EXPERT_KNOWLEDGE_BASE: dict[str, dict[str, Any]] = {
    # ------------------ EV, HIGH VOLTAGE & BMS ------------------
    "P0A0B": {
        "title": "Yüksek Voltaj Güvenlik Kilidi (HVIL) Devresi Açık (HVIL Circuit Open)",
        "subsystem": "EV Yüksek Voltaj Güvenlik & BMS",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Manuel Servis Şalteri (MSD) tam oturmamış veya pilot kontağı ayrılmış.",
            "İnverter, DC-DC veya klima kompresörü HV turuncu kapağındaki interlock köprüsü açık.",
            "HVIL 100 Hz PWM sinyal hattında kopukluk veya şasiye kısa devre (R_loop > 5 Ohm).",
        ],
        "steps": [
            ("MSD emniyet mandalını söküp kilit tırnağının yerine tam oturduğunu kontrol edin.", "Manuel Servis Şalteri (MSD)", "Kolay (Görsel)"),
            ("BMS HVIL çıkış pini ile dönüş pini arasındaki loop direncini ölçün (Kontak KAPALI: R < 5 Ω).", "HVIL Tesisat Döngüsü", "Orta (Alet Gerekir)"),
            ("Osiloskopta HVIL sinyalini gözlemleyin: 100 Hz ±5% kare dalga, %50 doluluk ve 12V/5V genlik olmalıdır.", "BMS Kontrol Ünitesi (BECM)", "İleri (Servis)"),
        ],
        "measurement": "Nominal HVIL Döngü Direnci: <5.0 Ω | PWM: 100 Hz, %50 Duty Cycle, V_high > 9.0V (12V sistem) / > 3.8V (5V sistem).",
        "uds_routine": "UDS Routine 0x31 (ID 0xD001: HVIL Interlock Loopback & Latch Reset)",
    },
    "P0A0D": {
        "title": "HVIL Devresi Yüksek Voltaj Kısa Devre (HVIL Circuit High)",
        "subsystem": "EV Yüksek Voltaj Güvenlik & BMS",
        "severity": "CRITICAL_STOP",
        "causes": [
            "HVIL sinyal kablosu araç 12V/24V akü besleme hattına (KL30/KL15) ezilerek kısa devre yapmış.",
            "BMS dahili pull-up direnç katı arızalanmış.",
        ],
        "steps": [
            ("HVIL soketini BMS'ten ayırıp araç tesisatındaki voltajı şasiye göre ölçün (0V olmalıdır).", "HVIL Kablo Demeti", "Orta (Alet Gerekir)"),
            ("12V besleme kablo demetlerinde sürtünme ve ezilme kontrolü yapın.", "Kablo Tesisatı", "Kolay (Görsel)"),
        ],
        "measurement": "HVIL Sinyal Voltajı > 5.5V (5V loop) veya > 15.0V (12V loop) arıza eşiğidir.",
        "uds_routine": "UDS Service 0x22 (DID 0x4102: HVIL Sense ADC Raw Voltage)",
    },
    "P0AA6": {
        "title": "Yüksek Voltaj İzolasyon Direnci Düşüklüğü (HV Isolation Fault)",
        "subsystem": "EV Batarya Paketi & Yüksek Voltaj İzolasyonu",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Batarya muhafazası içine soğutma sıvısı (antifriz) veya nem sızması.",
            "Klima kompresörü stator sargı izolasyonunun kompresör yağı ile bozulması.",
            "İnverter IGBT güç modülü substratında dielektrik delinme.",
        ],
        "steps": [
            ("LOTO güvenlik prosedürünü uygulayın (MSD sök, 10 dk bekle, DC Bus < 5V sıfır enerji onayı).", "HV Batarya Paketi", "İleri (Servis)"),
            ("Fluke 1587 / Megger ile 500V/1000V DC test voltajında HV+ ve HV- hatlarının şasiye izolasyonunu ölçün.", "HV+ / HV- Hatları", "İleri (Servis)"),
            ("HV alt dallarını (Klima, PTC Isıtıcı, OBC, DC-DC) tek tek ayırarak arızalı komponenti izole edin.", "Yüksek Voltaj Dağıtım Kutusu (PDU)", "İleri (Servis)"),
        ],
        "measurement": "ISO 6469-1 / UNECE R100 Standardı: Min İzolasyon Direnci ≥ 500 Ω/V DC (400V için ≥ 200 kΩ, 800V için ≥ 400 kΩ). Sağlıklı sistem: > 50 MΩ.",
        "uds_routine": "UDS Routine 0x31 (ID 0xD010: Automated Isolation Self-Test Sequence)",
    },
    "P0A80": {
        "title": "Hibrit / Elektrikli Araç Batarya Paketi Değişimi (Replace EV Battery Pack)",
        "subsystem": "EV Batarya Paketi & Hücre Sağlığı (SOH)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Hücreler arası kapasite kaybı >%30 (SOH_C < %70) veya iç direnç sapması >%50.",
            "Hücre delta voltajının yük altında >150 mV ve beklemede >50 mV seviyesine açılması.",
            "Hücre içi lityum kaplanması (lithium plating) ve aktif katot kütle kaybı.",
        ],
        "steps": [
            ("Bataryayı %100 SOC'ye şarj edip hücre dengeleme (balancing) rutinini tamamlayın.", "BMS Hücre Dengeleme", "Orta (Alet Gerekir)"),
            ("0.5C - 1C yük darbesi uygulayarak her bir hücrenin iç direncini (Ri = ΔV/ΔI) loglayın.", "Hücre Denetim Devresi (CSC)", "İleri (Servis)"),
            ("Diverjans gösteren zayıf hücre modülünü veya tüm batarya paketini değiştirin.", "Batarya Modülü", "İleri (Servis)"),
        ],
        "measurement": "Nominal Hücre Delta Voltajı: <30 mV | Arıza / Değişim Eşiği: >150 mV (Yükte) veya >50 mV (Dengede).",
        "uds_routine": "UDS Service 0x22 (DID 0x4100: Individual Cell Voltages & SOH Map)",
    },
    "P0A93": {
        "title": "İnverter Soğutma Sistemi Performansı (Inverter Cooling Performance)",
        "subsystem": "Elektrik Motoru & İnverter Termal Yönetimi",
        "severity": "MEDIUM",
        "causes": [
            "Elektrikli inverter su pompasının (E-Pump) debi kaybetmesi veya sıkışması.",
            "İnverter soğutma ceketinde hava cebi kalması veya radyatör petek tıkanıklığı.",
            "İnverter IGBT güç modülü altındaki termal macun kuruması/bozulması.",
        ],
        "steps": [
            ("UDS Routine 0x31 ile inverter soğutma pompasını %100 PWM ile çalıştırıp debiyi kontrol edin.", "Elektrikli Su Pompası", "Orta (Alet Gerekir)"),
            ("Soğutma devresinde vakumlu hava alma prosedürünü uygulayın.", "Soğutma Sıvısı Devresi", "Orta (Alet Gerekir)"),
            ("İnverter giriş ve çıkış sıcaklık sensörleri arasındaki farkı kontrol edin (Normal ΔT < 10°C).", "İnverter Sıcaklık Sensörleri", "Kolay (Görsel)"),
        ],
        "measurement": "İnverter IGBT Kritik Sıcaklık Limiti: >110°C (Derate başlar), >125°C (Acil Kesinti).",
        "uds_routine": "UDS Routine 0x31 (ID 0xD012: Coolant Circuit Vacuum Bleeding Routine)",
    },
    "P0B24": {
        "title": "Batarya Hücre Kritik Düşük Voltaj (Cell Undervoltage)",
        "subsystem": "EV Batarya Hücre Koruma",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Hücrede aşırı kendi kendine deşarj (mikro kısa devre) veya hücre voltajı < 2.50V (NMC) / < 2.00V (LFP).",
            "CSC kartı gerilim örnekleme hattında lehim çatlağı veya kopukluk.",
        ],
        "steps": [
            ("Hücre voltajını CSC soket pinlerinden 6.5 dijit DMM ile doğrudan ölçün.", "Hücre Klemensleri", "İleri (Servis)"),
            ("Gerçekten 2.0V altına inmiş hücreyi ASLA şarj etmeyin (Bakır dendrit yangın riski) — modülü değiştirin.", "Batarya Hücre Modülü", "İleri (Servis)"),
        ],
        "measurement": "NMC/NCA Alt Kesme: 2.50V | LFP Alt Kesme: 2.00V | Sağlıklı Nominal: 3.20V - 4.20V.",
        "uds_routine": "UDS Service 0x22 (DID 0x4101: Cell Min/Max Voltage Tracking)",
    },
    "P0AC0": {
        "title": "Batarya Sıcaklık Sensörü Aralık / Performans (Battery Temp Sensor Range)",
        "subsystem": "EV Batarya Termal İzleme",
        "severity": "MEDIUM",
        "causes": [
            "Modül NTC termistöründe direnç kayması (>%10) veya soket gevşekliği.",
            "Komşu sensörler ile okuma farkının >5°C olması.",
        ],
        "steps": [
            ("Sensör direncini 25°C ortamda multimetre ile ölçün (10 kΩ ±%1 olmalıdır).", "10k NTC Termistör", "Orta (Alet Gerekir)"),
            ("Termistör kablo demetinde şasiye sürtünme ve ezilme kontrolü yapın.", "Termal Sensör Kablo Demeti", "Kolay (Görsel)"),
        ],
        "measurement": "10k NTC Değerleri: 25°C = 10.0 kΩ (2.50V), 0°C = 32.6 kΩ (3.82V), 60°C = 2.48 kΩ (0.99V).",
        "uds_routine": "UDS Service 0x22 (DID 0x4105: Battery Module Temperature Distribution)",
    },
    "P0AA1": {
        "title": "Pozitif Ana Kontaktör Kapalı Yapışık Kaldı (Positive Contactor Stuck Closed)",
        "subsystem": "EV Yüksek Voltaj Kontaktör Grubu",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Aşırı inrush akımı veya precharge direnci arızası nedeniyle kontaktör kontaklarının kaynak olması.",
            "BMS kontaktör bobin sürme transistörünün (Low-Side FET) kısa devre olması.",
        ],
        "steps": [
            ("LOTO uygulayın, MSD sökün. Kontaktör güç terminalleri arasındaki direnci ölçün (>100 MΩ olmalıdır).", "Pozitif Ana Kontaktör", "İleri (Servis)"),
            ("0.0 Ω okunuyorsa kontaktör kontakları mekanik olarak kaynamıştır — kontaktör grubunu yenileyin.", "HV Kontaktör Bloğu", "İleri (Servis)"),
        ],
        "measurement": "Kontaktör Açık Durum Direnci: >100 MΩ | Bobin Direnci: 24.0 Ω ±%10.",
        "uds_routine": "UDS Routine 0x31 (ID 0xD020: Contactor Weld Detection Self-Test)",
    },
    "P0AA2": {
        "title": "Pozitif Ana Kontaktör Açık Kaldı / Çekmiyor (Positive Contactor Stuck Open)",
        "subsystem": "EV Yüksek Voltaj Kontaktör Grubu",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Precharge voltajının 300 ms içinde %95 seviyesine ulaşamaması (Precharge zaman aşımı).",
            "Kontaktör bobin sargısının yanması/kopması veya soket gevşekliği.",
        ],
        "steps": [
            ("Kontaktör bobin direncini ölçün (20 - 30 Ω arası olmalıdır).", "Kontaktör Bobini", "Orta (Alet Gerekir)"),
            ("Precharge direncini ölçün (Nominal 33 Ω veya 47 Ω, açık devre olmamalıdır).", "Precharge Direnci", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Precharge Zaman Aşımı Eşiği: 300 ms (V_bus < %90 V_pack ise kontak açılır).",
        "uds_routine": "UDS Routine 0x31 (ID 0xD021: Precharge Relay & Resistor Health Check)",
    },

    # ------------------ HEAVY DUTY & SAE J1939 ------------------
    "SPN100": {
        "title": "Motor Yağ Basıncı Hatası (Engine Oil Pressure Fault)",
        "subsystem": "Ağır Vasıta Yağlama Sistemi (J1939)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 1 (Kritik Düşük): Yağ pompası aşınması, karterde yağ seviyesinin tükenmesi veya ana yatak aşınması.",
            "FMI 3 (Voltaj Yüksek): Sinyal kablosu 5V referansa veya 24V hatta kısa devre.",
            "FMI 4 (Voltaj Düşük): Sinyal kablosu şasiye kısa devre veya sensör kopuk.",
        ],
        "steps": [
            ("Motoru derhal durdurun ve yağ seviye çubuğunu kontrol edin.", "Motor Karteri", "Kolay (Görsel)"),
            ("Sensör soketinde 5.0V besleme (Pin 1), Şasi (Pin 2) ve Sinyal voltajını (Pin 3) ölçün (Rölantide 1.2 - 2.5V).", "Yağ Basınç Sensörü", "Orta (Alet Gerekir)"),
            ("Mekanik manometre bağlayarak gerçek yağ basıncını doğrulayın (Rölanti >1.0 bar, 1800 RPM >3.0 bar).", "Yağ Galerisi Test Portu", "İleri (Servis)"),
        ],
        "measurement": "Sensör Skalası: 0.5V = 0 kPa, 4.5V = 1000 kPa | Kritik Kırmızı Lamba Limiti: <70 kPa (Rölanti), <180 kPa (Devirde).",
        "uds_routine": "J1939 DM11 (PGN 65235 Clear Active) & DM4 (PGN 65229 Freeze Frame Oku)",
    },
    "SPN102": {
        "title": "Turbo Takviye Basıncı Hatası (Turbo Boost Pressure Fault)",
        "subsystem": "Ağır Vasıta Hava Emiş & Turboşarj (J1939)",
        "severity": "MEDIUM",
        "causes": [
            "FMI 0/16 (Aşırı Basınç): VGT aktüatör kanatçıklarının kurumdan sıkışması veya wastegate valf arızası.",
            "FMI 18 (Düşük Basınç): Intercooler hortum yırtığı, intercooler radyatör çatlağı veya kompresör çark hasarı.",
            "FMI 2 (Tutarsızlık): Kontak açıkken atmosfer basıncı (SPN 108) ile turbo basıncı farkı >15 kPa.",
        ],
        "steps": [
            ("Intercooler hortum kelepçelerini ve şarj hava borularını duman makinesi ile sızdırmazlık testine tabi tutun.", "Şarj Havası Boruları & CAC", "Orta (Alet Gerekir)"),
            ("VGT aktüatör kolunun hareketini teşhis cihazından %0 - %100 sürerek test edin.", "Elektronik VGT Aktüatörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Tam Yükte Nominal Boost: 2.2 - 3.2 Bar (Abs) | Maksimum Güvenlik Limiti: 3.6 Bar.",
        "uds_routine": "J1939 Routine: VGT Vane Position Calibration & End-Stop Learning",
    },
    "SPN110": {
        "title": "Motor Soğutma Sıvısı Sıcaklığı (Engine Coolant Temperature)",
        "subsystem": "Ağır Vasıta Termal & Soğutma Sistemi (J1939)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 0 (Kritik Yüksek): Sıcaklık >108°C; termostat kapalı kalmış, viskoz fan kilitlenmiyor veya radyatör tıkalı.",
            "FMI 3 (Açık Devre): Sensör kablosu kopuk (ECU -40°C algılar ve fanı %100 açar).",
            "FMI 4 (Şasiye Kısa Devre): Sensör sinyali şasiye kısa devre (ECU +140°C algılar ve torku %50 kısar).",
        ],
        "steps": [
            ("Radyatör alt ve üst hortum sıcaklıklarını infrared termometre ile karşılaştırın (ΔT > 15°C ise termostat açmıyor).", "Termostat & Radyatör", "Kolay (Görsel)"),
            ("ECT sensör direncini ölçün: 20°C'de ~2.5 kΩ, 80°C'de ~320 Ω, 100°C'de ~180 Ω olmalıdır.", "Soğutma Sıvısı Sıcaklık Sensörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Normal Çalışma Aralığı: 82°C - 95°C | Uyarı (AWL): >103°C | Kırmızı Lamba (RSL Derate): >108°C.",
        "uds_routine": "J1939 Actuator Test: Viscous Fan Clutch 100% Engagement Override",
    },
    "SPN190": {
        "title": "Motor Devri / Krank Sinyal Hatası (Engine Speed / Crank Phase Sync)",
        "subsystem": "Ağır Vasıta Motor Zamanlama & Krank",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 0 (Aşırı Devir): Motor devri >2450 RPM (Yokuş aşağı vites hatası).",
            "FMI 2 (Faz Senkronizasyon Kaybı): Krank ve kam mili sinyal desenleri arasında açısal kayma (Triger/dişli boşluğu).",
            "FMI 8 (Sinyal Paraziti): Marş dinamosu veya enjektör kablosundan krank sensör zırhına elektromanyetik girişim (EMI).",
        ],
        "steps": [
            ("Osiloskop ile Krank (VR sinüs) ve Kam (Hall 0-5V) sinyallerini eşzamanlı kaydedip diş eksiklerini karşılaştırın.", "Krank & Kam Sensörleri", "İleri (Servis)"),
            ("Krank sensörü hava boşluğunu (Air-gap) sentil ile ölçün (0.8 - 1.2 mm olmalıdır).", "Volan Dişli Çelengi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "VR Sensör Direnci: 800 - 1400 Ω | Marş Sırasında VR AC Genlik: >1.0 Vpp.",
        "uds_routine": "J1939 Engine Speed Calibration & Cylinder Cutout Test",
    },
    "SPN1761": {
        "title": "AdBlue (DEF) Tank Seviyesi Hatası (DEF Tank Level)",
        "subsystem": "Ağır Vasıta SCR & Emisyon Sistemi (J1939)",
        "severity": "MEDIUM",
        "causes": [
            "FMI 17 (Seviye <%10): Sarı ikaz lambası.",
            "FMI 18 (Seviye <%5): Seviye 1 Tork Kısıtlaması (%25 tork kaybı).",
            "FMI 1 (Seviye <%2.5 / Depo Boş): Kırmızı stop lambası, Seviye 2 Kısıtlama: 5 mph (20 km/s) hız sınırlaması.",
        ],
        "steps": [
            ("AdBlue deposuna ISO 22241 standardında temiz DEF sıvısı ekleyin.", "AdBlue Deposu", "Kolay (Görsel)"),
            ("Ultrasonik şamandıra sensörünün soket voltajını ve CAN hattı iletişimini kontrol edin.", "DEF Seviye & Kalite Sensörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "AdBlue Şamandıra Skalası: %0 - %100 (0.4%/bit) | Refraktometre Üre Yoğunluğu: %32.5 ±%0.7.",
        "uds_routine": "J1939 Routine: DEF Dosing System Priming & Inducement Reset",
    },
    "SPN3251": {
        "title": "DPF Fark Basıncı Hatası (DPF Differential Pressure Delta-P)",
        "subsystem": "Ağır Vasıta DPF & Egzoz Sonrası İşlem",
        "severity": "MEDIUM",
        "causes": [
            "FMI 0 (Aşırı Kurum Tıkanıklığı): DPF basınç farkı >35 kPa; partikül filtresi dolu, rejenerasyon kilitlenmiş.",
            "FMI 1 (Filtre Delik/Yok): Basınç farkı <0.2 kPa; DPF peteği çatlak, içi boşaltılmış veya sökülmüş.",
            "FMI 2 (Hortum Ters/Tıkalı): Basınç boruları ters takılmış veya donmuş kondensat ile tıkanmış.",
        ],
        "steps": [
            ("DPF fark basınç sensörü silikon hortumlarında delinme veya erime olup olmadığını kontrol edin.", "DPF Basınç Hortumları", "Kolay (Görsel)"),
            ("Kurum yükü <40g ise cihaz üzerinden Park Halinde Manuel Servis Rejenerasyonu (Stationary DPF Regen) başlatın.", "Dizel Partikül Filtresi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Temiz DPF Rölanti Basıncı: 0.5 - 2.0 kPa | Tam Yük: 5.0 - 12.0 kPa | Tıkalı Limit: >25.0 kPa.",
        "uds_routine": "J1939 Service Routine: Stationary DPF Service Regeneration (PGN 64892)",
    },
    "SPN3364": {
        "title": "AdBlue (DEF) Sıvı Kalitesi Uygunsuz (DEF Quality / Concentration)",
        "subsystem": "Ağır Vasıta SCR & AdBlue Kalite Kontrol",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 18 (Kalite Düşük): AdBlue tankına su, mazot veya cam suyu karıştırılmış (Konsantrasyon <%28 veya >%38).",
            "FMI 2: Ultrasonik kalite sensöründe hava kabarcığı veya kristalleşme.",
        ],
        "steps": [
            ("Optik refraktometre ile depodaki sıvının üre konsantrasyonunu ölçün (Tam %32.5 olmalıdır).", "AdBlue Sıvısı", "Kolay (Görsel)"),
            ("Hatalı sıvı tespit edilirse depoyu komple boşaltın, deiyonize su ile çalkalayıp orijinal AdBlue doldurun.", "AdBlue Depo & Filtresi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Standart Üre Oranı: %32.5 ±%0.7 (ISO 22241). İndükleme Sayacı: 10 saat sonra 20 km/s hız kilidi.",
        "uds_routine": "J1939 Routine: DEF Quality Tampering Counter Reset Routine",
    },
    "SPN4364": {
        "title": "SCR DeNOx Dönüşüm Verimliliği Düşük (SCR Conversion Efficiency Low)",
        "subsystem": "Ağır Vasıta SCR Katalizör Verimliliği",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 1 (Verim <%45): SCR katalizörü kükürt veya motor yağı ile zehirlenmiş.",
            "FMI 18 (Verim %45-%75): AdBlue dozaj enjektörü kristalleşerek tıkanmış, DEF pompa basıncı düşük (<8.5 bar) veya çıkış NOx sensörü kaymış.",
        ],
        "steps": [
            ("AdBlue dozajlama enjektörünü söküp temizleyin; UDS üzerinden 3 dakikalık dozaj testini çalıştırın (110 - 135 mL gelmelidir).", "AdBlue Dozaj Enjektörü", "Orta (Alet Gerekir)"),
            ("Giriş (SPN 3216) ve Çıkış (SPN 3226) NOx sensör değerlerini motor freninde (0 mg enjeksiyon) karşılaştırın (İkisi de 0 ppm olmalıdır).", "NOx Sensörleri", "İleri (Servis)"),
        ],
        "measurement": "Nominal SCR DeNOx Verimliliği: >%90 | DEF Çalışma Basıncı: 9.0 ±0.5 Bar.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0302: DEF Dosing Quantity Measurement Test)",
    },
    "SPN651": {
        "title": "Silindir 1 Enjektör Devresi / Mekanik Arıza (Cylinder 1 Injector)",
        "subsystem": "Ağır Vasıta Common Rail Enjeksiyon",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 5 (Açık Devre): Enjektör bobin teli kopuk veya külbütör altı soketi çıkmış.",
            "FMI 6 (Aşırı Akım): Bobin sargısı kısa devre yapmış (ECU koruma için 1-2-3 silindir bankasını kapatır).",
            "FMI 7 (Mekanik Tepkisizlik): Enjektör iğnesi kapalı sıkışmış veya geri dönüşe aşırı yakıt kaçırıyor.",
        ],
        "steps": [
            ("Külbütör kapağı altındaki 1. silindir enjektör bobin direncini hassas miliohmmetre ile ölçün (0.35 - 0.55 Ω).", "1. Silindir Enjektörü", "Orta (Alet Gerekir)"),
            ("500V Megger ile bobin terminallerinin motor gövdesine izolasyonunu ölçün (>100 MΩ olmalıdır).", "Enjektör İzolasyonu", "İleri (Servis)"),
            ("10 saniyelik marş sırasında 1. enjektörün geri dönüş kaçak miktarını ölçün (Maks ≤ 5.0 mL).", "Geri Dönüş Hattı", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Solenoid Bobin Direnci: 0.35 - 0.55 Ω | İzolasyon: >100 MΩ | Marş Geri Dönüş: ≤ 5.0 mL / 10s.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0205: Automated Cylinder Cutout Test)",
    },
    "SPN1087": {
        "title": "EBS Servis Fren Devresi 1 Hava Basıncı (EBS Brake Circuit 1 Air Pressure)",
        "subsystem": "Ağır Vasıta EBS & Pnömatik Fren Sistemi",
        "severity": "CRITICAL_STOP",
        "causes": [
            "FMI 1 (Düşük Hava): Devre 1 hava basıncı <5.5 bar; hava kompresörü arızası, dört yollu emniyet valfi veya pnömatik kaçak.",
            "FMI 2 (Tutarsızlık): Devre 1 ile Devre 2 arasında frenleme anında >2.0 bar fark olması.",
        ],
        "steps": [
            ("Hava kurutucu tahliyesini ve dört yollu emniyet dağıtım valfini kaçak spreyi ile test edin.", "Dört Yollu Emniyet Valfi", "Kolay (Görsel)"),
            ("Kompresörün 0'dan 12 bara dolum süresini kronometre ile ölçün (Maksimum < 4 dakika).", "Pnömatik Hava Kompresörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Nominal Devre Basıncı: 10.0 - 12.5 Bar | Kırmızı İkaz & İmdat Eşiği: <5.5 Bar.",
        "uds_routine": "EBS Modulator Routine: Brake Cylinder Pressure Imbalance Calibration",
    },

    # ------------------ POWERTRAIN, GASOLINE & DIESEL EURO 6 ------------------
    "P0300": {
        "title": "Rastgele / Çoklu Silindir Ateşleme Hatası (Random/Multiple Cylinder Misfire)",
        "subsystem": "Ateşleme & Yakıt Enjeksiyon Sistemi",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Buji elektrot aşınması veya tırnak aralığının fabrika toleransından sapması.",
            "Ateşleme bobini sekonder sargı izolasyon kaçağı veya bobin soket korozyonu.",
            "Enjektör püskürtme deseni tıkanıklığı veya yakıt rayı basınç düşüklüğü.",
            "Krank mili (CKP) veya Kam mili (CMP) sensör sinyalinde CAN gürültüsü ve tork dalgalanması.",
        ],
        "steps": [
            ("Osilatör ekranında silindir ateşleme dalga boyunu ve krank sinyalini kontrol ediniz.", "Krank & Ateşleme Bobinleri", "Orta (Alet Gerekir)"),
            ("Enjektör dengeleme oranlarını ve yakıt rayı basıncını (UDS 0x22 DID 0x1102) ölçün.", "Yakıt Dağıtım Rayı", "Orta (Alet Gerekir)"),
            ("Bujilerin primer/sekonder direnç değerlerini ve kompresyon basıncını test edin.", "Silindir Yanma Odası", "İleri (Servis)"),
        ],
        "measurement": "Primer Bobin Direnci: 0.5 - 1.5 Ω | Sekonder: 5.0 - 15.0 kΩ | Kompresyon: >11.0 Bar (Benzin), >24.0 Bar (Dizel).",
        "uds_routine": "UDS Routine 0x31 (ID 0x0201: Silindir Kompresyon & Balans Testi)",
    },
    "P0087": {
        "title": "Yakıt Dağıtım Borusu Basıncı Çok Düşük (Fuel Rail Pressure Too Low)",
        "subsystem": "Yüksek Basınçlı Yakıt Enjeksiyon Sistemi (Common Rail)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Yüksek basınç yakıt pompası (HPFP / CP4) iç eleman aşınması veya debi kontrol valfi (VCV) tutukluğu.",
            "Yakıt filtresi parafinleşmesi veya tıkanıklığı nedeniyle emiş hattında vakum oluşması.",
            "Enjektör geri dönüş valflerinin aşırı sızdırması (Back-leakage).",
            "Basınç regülatörü (DRV) veya basınç tahliye valfinin (PRV) açık kalması.",
        ],
        "steps": [
            ("Yakıt filtresini kontrol ediniz ve alçak basınç besleme pompasının basıncını (min 4.5 bar) ölçünüz.", "Yakıt Filtresi & Depo Pompası", "Kolay (Görsel)"),
            ("Enjektörlerin geri dönüş miktarlarını dereceli kaplar ile ölçün (10 sn marşta maks 5 mL/enjektör).", "Common Rail Enjektörleri", "Orta (Alet Gerekir)"),
            ("Yüksek basınç pompası çıkış debisini ve ray basınç sensörü (SPN 157) sinyal voltajını osiloskopta doğrulayın.", "HPFP & Ray Basınç Sensörü", "İleri (Servis)"),
        ],
        "measurement": "Marş İçin Minimum Gerekli Ray Basıncı: ≥ 250 Bar (3600 psi) | Tam Yük: 1600 - 2200 Bar.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0203: Yüksek Basınç Yakıt Pompası Sızdırmazlık Testi)",
    },
    "P0234": {
        "title": "Turboşarj / Süperşarj Aşırı Takviye Basıncı (Engine Overboost Condition)",
        "subsystem": "Aşırı Doldurma & Hava Emiş Sistemi",
        "severity": "MEDIUM",
        "causes": [
            "Wastegate aktüatör kolunun mekanik olarak kapalı konumda sıkışması.",
            "N75 Boost kontrol selenoid valfinin elektriksel olarak açık kalması veya tıkanması.",
            "MAP / Takviye basınç sensörü (SPN 102) kalibrasyon sapması.",
            "Vakum hatlarında delinme veya çekvalf arızası.",
        ],
        "steps": [
            ("Wastegate aktüatör kolunu vakum pompası (Mityvac) ile test edin (0.6 barda tam açılmalıdır).", "Wastegate Aktüatörü", "Orta (Alet Gerekir)"),
            ("N75 selenoid valf bobin direncini ölçün (25 - 35 Ω) ve PWM sürücü sinyalini osiloskopta izleyin.", "N75 Boost Selenoidi", "Orta (Alet Gerekir)"),
            ("MAP sensörü canlı verisini motor kapalıyken barometrik sensör ile karşılaştırın (fark <15 hPa).", "MAP Sensörü", "Kolay (Görsel)"),
        ],
        "measurement": "N75 Bobin Direnci: 25 - 35 Ω | Vakum Tutma: -0.8 Bar'da 1 dakika boyunca düşmemeli.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0204: VGT / Wastegate Aktüatör Histerezis Testi)",
    },
    "P0016": {
        "title": "Krank - Kam Mili Pozisyon Korelasyon Hatası (Crank/Cam Correlation Bank 1)",
        "subsystem": "Motor Mekanik & Zamanlama",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Triger kayışı/zincirinde uzama, senteden atlama veya gergide gevşeme.",
            "VVT değişken subap zamanlama selenoidinin yağ çamuru ile tıkanması.",
            "Krank kasnağı harmonik damper kauçuğunun sıyırması.",
        ],
        "steps": [
            ("Osiloskop ile Krank (CKP) ve Kam (CMP) sinyallerini eşzamanlı kaydedip faz açısını inceleyin.", "Zamanlama Sensörleri", "İleri (Servis)"),
            ("VVT selenoid valfini söküp mikro filtresindeki çapak ve yağ çamurunu temizleyin.", "VVT Selenoid Valfi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Faz Senkronizasyon Sapma Limiti: < ±4.0° Krank Açısı.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0150: VVT Camshaft Phase Angle Adaptation)",
    },
    "P0420": {
        "title": "Katalitik Konvertör Sistemi Verimliliği Eşik Altında (Catalyst System Efficiency)",
        "subsystem": "Egzoz Emisyon & Katalitik Konvertör",
        "severity": "LOW",
        "causes": [
            "Katalizör monolitinin kurşun/yağ ile zehirlenmesi veya seramik peteğin erimesi.",
            "Arka (Downstream) oksijen sensörünün (O2S Bank 1 Sensor 2) sinyal dalgalanması.",
            "Egzoz manifoldu veya esnek spiral boruda hava kaçağı.",
        ],
        "steps": [
            ("Canlı telemetride ön ve arka oksijen sensör voltajlarını karşılaştırın (Arka sensör 0.6 - 0.7V sabit kalmalıdır).", "O2 Sensörü 2", "Orta (Alet Gerekir)"),
            ("Egzoz hattında spiral ve flanş kaçaklarını duman testi ile kontrol edin.", "Egzoz Spiral Borusu", "Kolay (Görsel)"),
        ],
        "measurement": "Sağlıklı Arka Lambda Voltajı: 0.60V - 0.75V (Sabit) | Arızalı: Ön sensör gibi 0.1V - 0.9V salınım.",
        "uds_routine": "UDS Service 0x19 (Subfunction 0x04: Freeze Frame & Catalyst Bed Temp)",
    },

    # ------------------ ADAS, CAN-FD & CHASSIS ------------------
    "C1A00": {
        "title": "Ön Radar Sensörü Hizalama Hatası (Forward Radar Alignment Error)",
        "subsystem": "ADAS & Sürüş Destek Sistemleri (CAN-FD)",
        "severity": "MEDIUM",
        "causes": [
            "Ön tampon darbesi sonrası radar braketinin açısal olarak kayması (>1.5°).",
            "Radar önündeki amblem veya plastik radom üzerinde yoğun kar/çamur kaplaması.",
        ],
        "steps": [
            ("Radar kapağındaki yabancı cisim ve buz tabakasını temizleyin.", "Ön Radar Kapağı", "Kolay (Görsel)"),
            ("Lazer ve hedef reflektör panosu (Doppler Reflector) kullanarak statik radar kalibrasyonunu başlatın.", "Radar Braketi", "İleri (Servis)"),
        ],
        "measurement": "Maksimum İzin Verilen Açısal Sapma: Yatayda < ±0.8°, Düşeyde < ±0.5°.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0501: ADAS Front Radar Dynamic Alignment Routine)",
    },
    "U0100": {
        "title": "Motor Kontrol Ünitesi (ECM) ile İletişim Kaybı (Lost Communication With ECM)",
        "subsystem": "CAN-Bus Omurga İletişim Hatası",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Motor beyni ana besleme sigortası (KL30/KL15) veya ana güç rölesi yanmış.",
            "CAN_H veya CAN_L hatlarında kopukluk veya şasiye kısa devre.",
            "Motor beyni ana şasi kablosunun (KL31) gevşemesi/paslanması.",
        ],
        "steps": [
            ("ECM ana besleme sigortalarını ve motor kontrol rölesini (Main Relay) multimetre ile test edin.", "Motor Sigorta Kutusu", "Kolay (Görsel)"),
            ("OBD soketinde Pin 6 (CAN_H) ve Pin 14 (CAN_L) arasındaki direnci ölçün (60 Ω olmalıdır).", "OBD-II Portu (Pin 6/14)", "Orta (Alet Gerekir)"),
            ("Motor beyni gövde şasi pini ile akü eksi kutbu arasındaki voltaj düşümünü ölçün (<50 mV olmalıdır).", "ECM Şasi Bağlantısı", "Orta (Alet Gerekir)"),
        ],
        "measurement": "CAN Omurga Direnci: 60.0 Ω ±%5 | Şasi Voltaj Düşümü: <50 mV DC.",
        "uds_routine": "UDS Service 0x28 (CommunicationControl: EnableRxAndTx 0x00)",
    },
    "U0126": {
        "title": "Direksiyon Açı Sensörü (SAS) ile İletişim Kaybı (Lost Comm with SAS)",
        "subsystem": "Şasi, ESP & Direksiyon Açı Sensörü",
        "severity": "MEDIUM",
        "causes": [
            "Direksiyon zembereği içindeki SAS optik okuyucusunun sıfır noktasını kaybetmesi.",
            "Direksiyon kolonu CAN alt ağı kablo temassızlığı.",
        ],
        "steps": [
            ("Direksiyonu tam sol ve tam sağ yaparak sıfır noktası adaptasyonunu gerçekleştirin.", "Direksiyon Simidi", "Kolay (Görsel)"),
            ("SAS CAN besleme soketindeki 12V ve GND pinlerini ölçün.", "SAS Modül Soketi", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Düz Konum Açı Toleransı: 0.0° ±1.5° | Besleme: 12.0 - 14.5V DC.",
        "uds_routine": "UDS Routine 0x31 (ID 0x0402: Steering Angle Sensor Zero Calibration)",
    },
    "U0415": {
        "title": "ABS / Fren Kontrol Modülünden Geçersiz Veri Alındı (Invalid Data From ABS)",
        "subsystem": "Fren Kontrol (ABS/ESP) & Çekiş Kontrolü",
        "severity": "MEDIUM",
        "causes": [
            "Tekerlek hız sensörlerinden birinde (WSS) sinyal atlaması veya porya manyetik halkasında paslanma.",
            "Lastik ebatları veya yuvarlanma çapları arasında >%3 fark olması.",
        ],
        "steps": [
            ("Dört tekerleğin hız sensörü canlı sinyallerini osiloskop veya canlı grafikten izleyin.", "Tekerlek Hız Sensörleri", "Orta (Alet Gerekir)"),
            ("Porya bilyası üzerindeki manyetik enkoder halkasını temizleyin.", "Porya Enkoder Halkası", "Kolay (Görsel)"),
        ],
        "measurement": "Hall Hız Sensörü Akım Seviyeleri: Düşük = 7 mA, Yüksek = 14 mA.",
        "uds_routine": "UDS Service 0x22 (DID 0x0310: 4-Wheel Speed Synchronous Vector)",
    },

    # ------------------ MARINE & NMEA 2000 ------------------
    "N2K_IMPELLER": {
        "title": "Deniz Suyu Çark (İmpeller) Arızası & Anlık Hararet (Raw Water Impeller Failure)",
        "subsystem": "Marin Motor Çift Devreli Soğutma (NMEA 2000)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Lastik impeller kanatlarının kuru çalışma veya aşınma nedeniyle parçalanması (Su debisi sıfıra indi).",
            "Deniz suyu emiş filtresinin (Sea Strainer) poşet/deniz anası ile tamamen tıkanması.",
            "Kinseft vanasının (Seacock) kapalı unutulması.",
        ],
        "steps": [
            ("Motoru derhal stop edin! Kinseft vanasının açık olduğunu ve deniz suyu filtresini kontrol edin.", "Deniz Suyu Filtresi (Strainer)", "Kolay (Görsel)"),
            ("Deniz suyu pompası kapağını söküp kauçuk impeller kanatlarını kontrol edin; kopan parçaları eşanjör girişinde arayın.", "Deniz Suyu Pompası", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Termal Gradyan Eşiği: dT/dt > 1.5°C/saniye (Rölantide dahi saniyeler içinde 100°C üzerine fırlar).",
        "uds_routine": "NMEA 2000 PGN 127489 (Engine Dynamic) & PGN 130310 (Water Temp)",
    },
    "N2K_EXHAUST_ELBOW": {
        "title": "Islak Egzoz Karışım Dirseği Aşırı Sıcaklık (Wet Exhaust Mixing Elbow Overheat)",
        "subsystem": "Marin Egzoz & Yangın Güvenliği",
        "severity": "CRITICAL_STOP",
        "causes": [
            "Egzoz dirseği su püskürtme deliklerinin (spray ring) kireç ve pas ile tıkanması.",
            "Ham su enjeksiyonunun kesilmesi nedeniyle 550°C'lik kuru egzoz gazının doğrudan susturucuya geçmesi.",
        ],
        "steps": [
            ("Egzoz dirseğine gelen su besleme hortumunu söküp su çıkışını test edin.", "Egzoz Karışım Dirseği", "Kolay (Görsel)"),
            ("Fiberglas susturucu ve kauçuk egzoz hortumunun sıcaklığını kontrol edin (85°C üzeri erime riski taşır).", "Fiberglas Susturucu (Waterlock)", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Güvenli Çalışma: 40°C - 65°C | Alarm: ≥75°C | Kritik Erime & Su Alma Tehlikesi: >105°C.",
        "uds_routine": "NMEA 2000 PGN 127489 (Exhaust Gas Temperature & Discrete Alarm)",
    },
    "N2K_HEAT_EXCHANGER": {
        "title": "Marin Eşanjör Kireçlenmesi & Yüksek Yükte Hararet (Heat Exchanger Scaling)",
        "subsystem": "Marin Isı Değiştirici & Termal Kapasite",
        "severity": "MEDIUM",
        "causes": [
            "Bakır-nikel eşanjör boru demetinin içinde kalsiyum karbonat (CaCO3) ve midye tabakası oluşması.",
            "Rölantide ve düşük devirde hararet yapmazken, %75 üzeri gazda (WOT) soğutma kapasitesinin yetersiz kalması.",
        ],
        "steps": [
            ("Eşanjör kapaklarını söküp boru demetini (tube bundle) özel asit/kireç çözücü solüsyon ile temizleyin (Rydlyme).", "Marin Eşanjör Boru Demeti", "İleri (Servis)"),
            ("Çinko tutyaları (Anodes) kontrol edip %50'den fazla erimişse yenileriyle değiştirin.", "Çinko Kurban Anotlar", "Kolay (Görsel)"),
        ],
        "measurement": "Eşanjör Sıcaklık Düşüşü: Sağlıklı ΔT = 8°C - 12°C | Kireçli Arızalı ΔT < 4°C.",
        "uds_routine": "NMEA 2000 PGN 127489 (Engine Load % vs Coolant Temp Delta)",
    },
    "N2K_PROP_SLIP": {
        "title": "Pervane Kavitasyonu / Yüksek Kayma Oranı (Propeller Slip & Cavitation)",
        "subsystem": "Marin Hidrodinamik & Sevk Sistemi",
        "severity": "MEDIUM",
        "causes": [
            "Pervane kanatlarında eğilme, çentik veya kauçuk göbek (hub) sıyırması.",
            "Yüksek torkta pervanenin su tutuşunu kaybetmesi (Slip >%35).",
            "Gövde altında yoğun kekamoz (marine growth) ve sürtünme direnci artışı.",
        ],
        "steps": [
            ("Pervaneyi dalgıç veya karada kontrol edin; kanat hatvesinde eğrilik ve kavitasyon korozyonunu inceleyin.", "Gemi Pervanesi & Şaftı", "Kolay (Görsel)"),
            ("SOG (GPS Hızı) ile Şaft Devri × Hatve teorik hızını karşılaştırıp dinamik kayma oranını hesaplayın.", "GPS & Şaft Hız Sensörü", "Orta (Alet Gerekir)"),
        ],
        "measurement": "Pervane Slip Formülü: Slip% = (1 - (SOG × 1215.22 / (RPM/Ratio × Pitch))) × 100 | Normal Kayan Gövde: %10 - %18.",
        "uds_routine": "NMEA 2000 PGN 128259 (Speed Water Ref) & PGN 129026 (SOG Rapid)",
    },

    # ------------------ CAN PHYSICAL LAYER & OSCILLOSCOPE FORENSICS ------------------
    "CAN_TERM_60": {
        "title": "CAN-Bus 120Ω Sonlandırma Direnci Hatası (CAN Termination Fault)",
        "subsystem": "CAN Fiziksel Katman (ISO 11898-2)",
        "severity": "CRITICAL_STOP",
        "causes": [
            "120 Ω okunuyorsa: Hat ucundaki iki adet 120Ω sonlandırma direncinden biri kopuk veya soketi çıkmış.",
            "0 - 10 Ω okunuyorsa: CAN_H ve CAN_L kabloları birbirine kısa devre.",
            "Sonsuz (Açık Devre): Hat üzerindeki iki sonlandırma direnci de kopuk veya ana omurga hattı kesik.",
            "30 - 40 Ω okunuyorsa: Hatta yanlışlıkla 3. veya 4. bir paralel 120Ω direnç takılmış.",
        ],
        "steps": [
            ("Akü kutup başını veya kontağı KAPATIN. OBD soketi Pin 6 (CAN_H) ile Pin 14 (CAN_L) arasını ohmmetre ile ölçün.", "OBD-II Portu (Pin 6/14)", "Kolay (Görsel)"),
            ("60.0 Ω okunmalıdır. 120 Ω ise hat sonundaki ECU'ların (Motor Beyni ve Gösterge/ABS) soketlerini kontrol edin.", "Omurga Sonlandırma Dirençleri", "Orta (Alet Gerekir)"),
            ("Osiloskopta kare dalga köşelerindeki çınlama (ringing/reflection) genliğini kontrol edin.", "CAN Diferansiyel Sinyali", "İleri (Servis)"),
        ],
        "measurement": "Standart Eşdeğer Direnç: 60.0 Ω ±%5 (120Ω // 120Ω) | Hata Toleransı: 55 Ω - 65 Ω.",
        "uds_routine": "ISO 11898-2 Physical Layer Multimeter Verification",
    },
    "CAN_VOLT_FAULT": {
        "title": "CAN Fiziksel Katman Voltaj Anomalisi (CAN Bias / Ground Offset Fault)",
        "subsystem": "CAN Fiziksel Katman Elektriksel Teşhis",
        "severity": "CRITICAL_STOP",
        "causes": [
            "CAN_H voltajı 3.5V yerine 12V/24V akü voltajına oturmuş (Artıya kısa devre).",
            "CAN_L voltajı 1.5V yerine 0V şasiye yapışmış (Şasiye kısa devre).",
            "Düğümler arası şasi potansiyel farkı (Ground Offset) >2.0V üzerine çıkmış.",
        ],
        "steps": [
            ("Kontak AÇIK durumdayken Pin 6 (CAN_H) ve Pin 14 (CAN_L) voltajlarını şasiye göre ayrı ayrı ölçün.", "CAN Hat Voltajları", "Orta (Alet Gerekir)"),
            ("Normal Resesif (Boşta): CAN_H = 2.5V, CAN_L = 2.5V (V_diff = 0.0V).", "Diferansiyel Denge", "Orta (Alet Gerekir)"),
            ("Normal Dominant (Veri Anı): CAN_H = 3.5V, CAN_L = 1.5V (V_diff = 2.0V).", "Veri İletim Seviyesi", "İleri (Servis)"),
        ],
        "measurement": "Resesif: 2.50V ±0.15V | Dominant CAN_H: 3.50V ±0.25V | Dominant CAN_L: 1.50V ±0.25V | V_diff: 2.00V ±0.30V.",
        "uds_routine": "Oscilloscope 10x Differential Probing Mode",
    },
}

# ============================================================================
# DYNAMIC DIAGNOSTIC DATABASE LOADER & TELEMETRY ACCESSORS
# ============================================================================

def _resolve_external_data_dir() -> Path:
    """Resolve the external diagnostics data directory (H-9 / P1-11).

    Frozen PyInstaller builds resolve `__file__` inside the _MEIPASS
    extraction directory, so a bare `Path(__file__).parents[3]` misses the
    bundled data — silently degrading the DTC knowledge base from ~1870 to
    34 hardcoded rules. Prefer the frozen bundle root first, then fall back
    to the repository layout.
    """
    import sys

    if getattr(sys, "frozen", False):
        frozen_root = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
        frozen_dir = frozen_root / "data" / "diagnostics"
        if frozen_dir.is_dir():
            return frozen_dir
    return Path(__file__).resolve().parents[3] / "data" / "diagnostics"


_EXTERNAL_DATA_DIR: Path = _resolve_external_data_dir()
_CACHED_J1939_DB: dict[str, Any] | None = None
_CACHED_UDS_DID_DB: dict[str, Any] | None = None
_CACHED_MODE06_DB: dict[str, Any] | None = None
_CACHED_EXTENDED_PID_DB: dict[str, Any] | None = None


def _validate_dtc_entry_shape(code: str, info: Any) -> bool:
    """Shape-validate one external DTC entry before merging (M-17 / P2-16).

    A hostile or corrupted external DB must not inject garbage into the
    expert base: `steps` must be a list of 2-3 element sequences of strings
    (a bare string used to be character-indexed by the consumer), `severity`
    must be a known level, and the required top-level fields must be present.
    """
    if not isinstance(info, dict):
        return False
    for required_field in ("title", "subsystem", "severity"):
        val = info.get(required_field)
        if not isinstance(val, str) or not val.strip():
            return False
    severity = info.get("severity")
    if severity not in ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL", "CRITICAL_STOP"):
        return False
    steps = info.get("steps")
    if steps is not None:
        if not isinstance(steps, (list, tuple)):
            return False
        for s in steps:
            if not isinstance(s, (list, tuple)) or not 2 <= len(s) <= 3:
                return False
            if not all(isinstance(part, str) for part in s):
                return False
    causes = info.get("causes")
    if causes is not None and not isinstance(causes, (list, tuple)):
        return False
    _ = code  # validated by caller (dict key, always str from JSON)
    return True


def load_external_dtc_database(data_path: Path | str | None = None) -> int:
    """Dynamically load and merge external DTC catalog into EXPERT_KNOWLEDGE_BASE.

    Preserves hardcoded expert rules (existing entries are never overwritten).
    Returns the number of new DTC codes merged.
    """
    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "dtc_database.json"
    if not target.exists():
        # H-9 (P1-11): missing DB is a DEGRADED mode, not business as usual —
        # debug level hid the loss of ~98% of the knowledge base in frozen
        # builds. Surface it at WARNING so operators can notice.
        logger.warning("External DTC database not found at %s — knowledge base degraded to built-in rules", target)
        return 0

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if not isinstance(data, dict):
            logger.warning("External DTC database format invalid (expected dict, got %s)", type(data).__name__)
            return 0

        added = 0
        rejected = 0
        for code, info in data.items():
            # M-17 (P2-16): shape validation BEFORE merge — garbage entries
            # are counted and skipped, never merged.
            if not _validate_dtc_entry_shape(code, info):
                rejected += 1
                continue
            # Preserve existing rich hand-crafted rules
            if code not in EXPERT_KNOWLEDGE_BASE:
                EXPERT_KNOWLEDGE_BASE[code] = info
                added += 1

        if rejected:
            logger.warning(
                "External DTC database: %d entries rejected by shape validation", rejected
            )
        logger.info("Merged %d external DTC codes into EXPERT_KNOWLEDGE_BASE (total: %d)", added, len(EXPERT_KNOWLEDGE_BASE))
        return added
    except Exception as exc:
        logger.warning("Failed to load external DTC database from %s: %s", target, exc)
        return 0


def get_j1939_spn_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return SAE J1939 SPN & FMI fault knowledge base."""
    global _CACHED_J1939_DB
    if _CACHED_J1939_DB is not None and data_path is None:
        return _CACHED_J1939_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "j1939_spn_fmi_database.json"
    if not target.exists():
        logger.warning("J1939 SPN/FMI database not found at %s — J1939 fault decoding degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_J1939_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load J1939 database: %s", exc)
    return {}


def get_uds_did_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return OEM-specific UDS Data Identifier (DID) telemetry catalog."""
    global _CACHED_UDS_DID_DB
    if _CACHED_UDS_DID_DB is not None and data_path is None:
        return _CACHED_UDS_DID_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "uds_did_database.json"
    if not target.exists():
        logger.warning("UDS DID database not found at %s — DID telemetry catalog degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_UDS_DID_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load UDS DID database: %s", exc)
    return {}


def get_mode06_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return SAE J1979 Mode $06 On-Board Monitoring Tests database."""
    global _CACHED_MODE06_DB
    if _CACHED_MODE06_DB is not None and data_path is None:
        return _CACHED_MODE06_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "obd_mode06_database.json"
    if not target.exists():
        logger.warning("Mode $06 database not found at %s — monitor test data degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_MODE06_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load Mode 06 database: %s", exc)
    return {}


_CACHED_NHTSA_RECALLS_DB: dict[str, Any] | None = None


def get_extended_pid_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return Extended / Enhanced OBD-II PID Database (Mode $22 + Custom).

    Contains manufacturer-specific PIDs from Ford, GM, Toyota, VAG, BMW,
    Hyundai/Kia, Nissan with scaling formulas, ECU headers, and value ranges.
    """
    global _CACHED_EXTENDED_PID_DB
    if _CACHED_EXTENDED_PID_DB is not None and data_path is None:
        return _CACHED_EXTENDED_PID_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "extended_pid_database.json"
    if not target.exists():
        logger.warning("Extended PID database not found at %s — enhanced PID queries degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            # Filter out entries with empty PIDs (comment rows from CSV imports)
            if "pids" in data:
                data["pids"] = [p for p in data["pids"] if p.get("pid")]
                data.setdefault("metadata", {})["total_pids"] = len(data["pids"])
            if data_path is None:
                _CACHED_EXTENDED_PID_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load Extended PID database: %s", exc)
    return {}


def search_extended_pids(
    manufacturer: str | None = None,
    category: str | None = None,
    query: str | None = None,
    service: str | None = None,
    limit: int = 20,
    data_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Search extended PIDs by manufacturer, category, keyword, or OBD service mode.

    Examples:
        search_extended_pids(manufacturer="Ford", category="emission")
        search_extended_pids(query="DPF soot")
        search_extended_pids(manufacturer="Toyota", category="hv_battery")
    """
    db = get_extended_pid_database(data_path)
    if not db:
        return []

    pids = db.get("pids", [])
    mfr_clean = manufacturer.lower().strip() if manufacturer else ""
    cat_clean = category.lower().strip() if category else ""
    qry_clean = query.lower().strip() if query else ""
    svc_clean = service.strip() if service else ""

    results: list[dict[str, Any]] = []
    for p in pids:
        if mfr_clean and mfr_clean not in p.get("manufacturer", "").lower():
            continue
        if cat_clean and cat_clean not in p.get("category", "").lower():
            continue
        if svc_clean and svc_clean != p.get("service", ""):
            continue
        if qry_clean:
            searchable = f"{p.get('name', '')} {p.get('description', '')} {p.get('unit', '')} {p.get('vehicle_model', '')}".lower()
            if qry_clean not in searchable:
                continue
        results.append(p)
        if len(results) >= limit:
            break

    return results


def get_extended_pid_info(pid_hex: str, manufacturer: str | None = None) -> dict[str, Any] | None:
    """Lookup a specific extended PID by its hex code, optionally filtered by manufacturer."""
    db = get_extended_pid_database()
    if not db:
        return None

    pid_clean = pid_hex.upper().strip()
    mfr_clean = manufacturer.lower().strip() if manufacturer else ""

    for p in db.get("pids", []):
        p_hex = p.get("pid_hex", "").upper().strip()
        p_pid = p.get("pid", "").upper().strip()
        if pid_clean in (p_hex, p_pid):
            if not mfr_clean or mfr_clean in p.get("manufacturer", "").lower():
                return p
    return None


_CACHED_DBC_CATALOG: dict[str, Any] | None = None
_DBC_DATA_DIR: Path = Path(__file__).resolve().parents[3] / "data" / "dbc"


def get_dbc_catalog(catalog_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return DBC catalog metadata."""
    global _CACHED_DBC_CATALOG
    if _CACHED_DBC_CATALOG is not None and catalog_path is None:
        return _CACHED_DBC_CATALOG

    target = Path(catalog_path) if catalog_path else _DBC_DATA_DIR / "catalog.json"
    if not target.exists():
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if catalog_path is None:
                _CACHED_DBC_CATALOG = data
            return data
    except Exception as exc:
        logger.warning("Failed to load DBC catalog: %s", exc)
    return {}


def search_dbc_catalog(query: str, catalog_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Search available DBC files by keyword or model name."""
    cat = get_dbc_catalog(catalog_path)
    if not cat:
        return []

    norm = AutomotiveTokenizer.normalize_text(query).replace("dbc", "").strip()
    words = [
        w for w in norm.split()
        if len(w) > 1 and w not in ("can", "file", "dosya", "dosyasi", "var", "mi", "mu", "nedir", "hangi", "katalog", "hakkinda", "bilgi", "ver")
    ]
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for cat_name, cat_data in cat.get("categories", {}).items():
        title = cat_data.get("title", cat_name)
        protocol = cat_data.get("protocol", "CAN")
        for f in cat_data.get("files", []):
            fname = f.get("filename", "")
            fname_lower = fname.lower()
            if fname in seen:
                continue

            matched = False
            if norm and (norm in fname_lower or fname_lower in norm):
                matched = True
            elif words and all(w in fname_lower for w in words):
                matched = True

            if matched:
                seen.add(fname)
                results.append({
                    "filename": fname,
                    "category": title,
                    "protocol": protocol,
                    "messages_count": f.get("messages_count", 0),
                    "signals_count": f.get("signals_count", 0),
                })

    return results


def get_nhtsa_recalls_database(data_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return NHTSA CAN-Bus, Electrical & Software Recalls database."""
    global _CACHED_NHTSA_RECALLS_DB
    if _CACHED_NHTSA_RECALLS_DB is not None and data_path is None:
        return _CACHED_NHTSA_RECALLS_DB

    target = Path(data_path) if data_path else _EXTERNAL_DATA_DIR / "nhtsa_can_recalls_database.json"
    if not target.exists():
        logger.warning("NHTSA recalls database not found at %s — recall lookups degraded", target)
        return {}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if isinstance(data, dict):
            if data_path is None:
                _CACHED_NHTSA_RECALLS_DB = data
            return data
    except Exception as exc:
        logger.warning("Failed to load NHTSA recalls database: %s", exc)
    return {}


def search_nhtsa_recalls(
    make: str | None = None,
    model: str | None = None,
    year: int | None = None,
    query: str | None = None,
    category: str | None = None,
    limit: int = 10,
    data_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Search NHTSA recalls by vehicle specification, symptom keywords, or category."""
    db = get_nhtsa_recalls_database(data_path)
    if not db:
        return []

    make_clean = make.lower().strip() if make else ""
    model_clean = model.lower().strip() if model else ""
    query_clean = query.lower().strip() if query else ""
    cat_clean = category.lower().strip() if category else ""

    results: list[dict[str, Any]] = []

    for _campaign_id, rec in db.items():
        # Match vehicle make/model/year if specified
        if make_clean or model_clean or year:
            vehicle_match = False
            for v in rec.get("affected_vehicles", []):
                v_make = v.get("make", "").lower()
                v_model = v.get("model", "").lower()
                v_year = v.get("year")

                make_ok = not make_clean or make_clean in v_make
                model_ok = not model_clean or model_clean in v_model
                year_ok = not year or year == v_year

                if make_ok and model_ok and year_ok:
                    vehicle_match = True
                    break
            if not vehicle_match:
                continue

        # Match category if specified
        if cat_clean and cat_clean not in rec.get("category", "").lower():
            continue

        # Match text query in summary, component, consequence, or remedy
        if query_clean:
            searchable = f"{rec.get('component', '')} {rec.get('summary', '')} {rec.get('consequence', '')} {' '.join(rec.get('affected_systems', []))}".lower()
            if query_clean not in searchable:
                continue

        results.append(rec)
        if len(results) >= limit:
            break

    return results


def format_nhtsa_recall_report(recall: dict[str, Any]) -> str:
    """Format an NHTSA safety recall into a concise, actionable summary."""
    camp = recall.get("campaign_number", "Bilinmiyor")
    mfr = recall.get("manufacturer", "-")
    comp = recall.get("component", "-")
    ota = "OTA Güncelleme" if recall.get("over_the_air_update") else "Servis Onarımı"
    summary = (recall.get("summary") or "-").strip()
    if len(summary) > 160:
        summary = summary[:157] + "..."
    remedy = (recall.get("remedy") or "-").strip()
    if len(remedy) > 140:
        remedy = remedy[:137] + "..."

    vehicles = recall.get("affected_vehicles", [])
    v_info = ""
    if vehicles:
        first = vehicles[0]
        v_info = f" | {first.get('make', '')} {first.get('model', '')} ({first.get('year', '')})"
        if len(vehicles) > 1:
            v_info += f" (+{len(vehicles) - 1} model)"

    return (
        f"🚨 **NHTSA Geri Çağırma (Recall): {camp}** ({mfr}{v_info})\n"
        f"• **Modül:** {comp} [{ota}]\n"
        f"• **📋 Sorun Özeti:** {summary}\n"
        f"• **🔧 Resmi Onarım:** {remedy}"
    )


# M-18 (P2-17): the external DTC database loads lazily on first use instead
# of at module import — the bare module-scope call parsed 2.3 MB of JSON
# (~0.4 s I/O) on EVERY process start, including CLI invocations that never
# touch diagnostics. ensure_external_dtc_database_loaded() is idempotent and
# thread-safe; direct load_external_dtc_database(data_path=...) callers
# (tests) bypass the cache deliberately.
_DTC_DB_LOAD_LOCK = threading.Lock()
_DTC_DB_LOADED = False


def ensure_external_dtc_database_loaded() -> None:
    """Idempotent, thread-safe lazy load of the external DTC database."""
    global _DTC_DB_LOADED
    if _DTC_DB_LOADED:
        return
    with _DTC_DB_LOAD_LOCK:
        if _DTC_DB_LOADED:
            return
        load_external_dtc_database()
        _DTC_DB_LOADED = True

# ============================================================================
# COMPLETE ISO 14229 UDS NEGATIVE RESPONSE CODE (NRC) CATALOG
# ============================================================================


UDS_NRC_CATALOG: dict[str, dict[str, str]] = {
    "0x10": {"name": "generalReject", "cause": "ECU donanımsal meşguliyet veya dahili hata nedeniyle isteği reddetti.", "action": "İsteği 50 ms sonra tekrarlayın veya ECU'ya soft reset atın."},
    "0x11": {"name": "serviceNotSupported", "cause": "İstenen Servis ID (SID) bu ECU yazılımında tanımlı değil.", "action": "ECU yazılım versiyonunu ve desteklenen servis listesini (0x19 0x0A) kontrol edin."},
    "0x12": {"name": "subFunctionNotSupported", "cause": "İstenen alt fonksiyon (Subfunction) bu serviste desteklenmiyor.", "action": "Subfunction baytını kontrol edin (Örn: 0x10 0x02 yerine 0x10 0x03 deneyin)."},
    "0x13": {"name": "incorrectMessageLengthOrInvalidFormat", "cause": "İstek bayt uzunluğu veya çerçeve formatı hatalı.", "action": "ISO-TP çerçeve uzunluğunu ve parametre bayt sayısını doğrulayın."},
    "0x14": {"name": "responseTooLong", "cause": "Yanıt bayt uzunluğu taşıma tamponunu aşıyor.", "action": "Sorguyu daraltın (Tüm liste yerine tek tek DID veya DTC okuyun)."},
    "0x22": {"name": "conditionsNotCorrect", "cause": "Ön koşullar sağlanmadı (Örn: Motor çalışırken rutin başlatılamaz veya voltaj <11.0V).", "action": "Kontağı açın, motoru durdurun, akü besleme cihazı bağlayın (>12.5V) ve el frenini çekin."},
    "0x24": {"name": "requestSequenceError", "cause": "Sıralama hatası (Örn: Seed almadan Key gönderme veya 0x34 olmadan 0x36 çağırma).", "action": "Prosedürü en baştan sırasıyla işletin (0x10 0x02 -> 0x27 0x01 -> 0x27 0x02 -> 0x34)."},
    "0x31": {"name": "requestOutOfRange", "cause": "DID, Routine ID veya yazılmak istenen parametre değeri sınırların dışında.", "action": "Parametre sınırlarını ve DID hex adresini ODX/CDD veritabanından doğrulayın."},
    "0x33": {"name": "securityAccessDenied", "cause": "Güvenlik kilidi kapalı; bu işlem için Seed/Key açılması şart.", "action": "0x27 0x01 servisi ile Seed isteyip doğru Key algoritmasını hesaplayarak gönderin."},
    "0x35": {"name": "invalidKey", "cause": "Gönderilen güvenlik anahtarı (Key) yanlış.", "action": "DLL algoritmasını, gizli anahtarı ve byte endianness sırasını kontrol edin."},
    "0x36": {"name": "exceededNumberOfAttempts", "cause": "Üst üste 3 hatalı Key denemesi yapıldığı için güvenlik kilidi kilitlendi.", "action": "ECU gücünü kesmeyin; 10 dakikalık anti-brute-force ceza süresinin dolmasını bekleyin."},
    "0x37": {"name": "requiredTimeDelayNotExpired", "cause": "Ceza süresi dolmadan yeni bir güvenlik erişim isteği yapıldı.", "action": "Geri sayım süresinin (10 dk) tamamen sıfırlanmasını bekleyin."},
    "0x78": {"name": "requestCorrectlyReceived-ResponsePending", "cause": "ECU işlemi kabul etti, arka planda işliyor (Flash silme/kripto hesabı).", "action": "İsteği tekrarlamayın! P2* client zamanlayıcısını (5000 ms) bekleyin."},
    "0x7E": {"name": "subFunctionNotSupportedInActiveSession", "cause": "Bu alt fonksiyon mevcut oturumda yasak.", "action": "0x10 0x03 ile Extended Session'a geçiş yapın."},
    "0x7F": {"name": "serviceNotSupportedInActiveSession", "cause": "Bu servis mevcut oturumda çalıştırılamaz.", "action": "0x10 0x02 Programming Session veya 0x10 0x03 Extended Session açın."},
    "0x83": {"name": "engineIsRunning", "cause": "Test için motorun durdurulması şart.", "action": "Motoru stop edip sadece kontağı açık bırakın."},
    "0x88": {"name": "vehicleSpeedTooHigh", "cause": "Araç hızı >0 km/s olduğu için güvenlik gereği işlem engellendi.", "action": "Aracı tamamen durdurun ve el frenini çekin."},
    "0x92": {"name": "voltageTooHigh", "cause": "Akü/şebeke voltajı çok yüksek (>16.0V / >32.0V).", "action": "Harici şarj cihazını sökün veya regülatörü kontrol edin."},
    "0x93": {"name": "voltageTooLow", "cause": "Akü voltajı güvenli flash/rutin sınırının altında (<11.0V).", "action": "Harici akü destek ünitesi bağlayın (13.8V - 14.4V)."},
}

# ============================================================================
# BILINGUAL TURKISH/ENGLISH AUTOMOTIVE NLP TOKENIZER & SEMANTIC ONTOLOGY
# ============================================================================

AUTOMOTIVE_SEMANTIC_DICTIONARY: dict[str, list[str]] = {
    "MISFIRE": [
        "tekleme", "tekliyor", "misfire", "silkeleme", "sarsinti", "sarsintili", "3 silindir", "atesleme hatasi",
        "atesleme", "buji", "bobin", "enjektor", "avans", "vuruntu", "patlatma", "piston", "kompresyon"
    ],
    "TURBO_BOOST": [
        "turbo", "overboost", "underboost", "basinc", "boost", "wastegate", "intercooler", "n75", "vgt",
        "islik sesi", "hava kacagi", "hortum patlak", "hava akis", "maf", "map", "cekis dusuklugu", "bayilma",
        "kara duman", "siyah duman", "duman atiyor"
    ],
    "OVERHEAT_COOLING": [
        "hararet", "sicaklik", "sogutma", "termostat", "radyator", "fan", "antifriz", "su kaynatiyor", "su eksiltme",
        "hortum sisme", "devirdaim", "su pompasi", "expansion tank", "genlesme kabi", "conta yakma", "ust kapak contasi",
        "beyaz buhar", "tatli koku", "mayonez"
    ],
    "EV_HV_BATTERY": [
        "ev", "bms", "hvil", "izolasyon", "batarya", "pil", "hucre", "delta voltaj", "precharge", "kontaktor", "megger",
        "yuksek voltaj", "high voltage", "msd", "servis salteri", "turtle mode", "kapasite kaybi", "soh", "soc", "inverter",
        "termal kacak", "thermal runaway", "dc-dc", "igbt"
    ],
    "HEAVY_DUTY_J1939": [
        "j1939", "spn", "fmi", "dm1", "dm2", "dm4", "dm11", "adblue", "def", "dpf", "scr", "nox", "rejenerasyon",
        "kirmizi lamba", "sari lamba", "rsl", "awl", "tork kisitlama", "5 mph", "hiz limiti", "cummins", "detroit",
        "scania", "volvo truck", "paccar", "hava basinci", "ebs"
    ],
    "MARINE_NMEA2000": [
        "marine", "marin", "tekne", "yat", "gemi", "nmea", "nmea 2000", "n2k", "pgn", "impeller", "cark", "deniz suyu",
        "strainer", "esnjor", "esanchor", "egzoz dirsegi", "mixing elbow", "susturucu", "waterlock", "pervane", "slip",
        "kavitasyon", "dumen", "potansiyometre", "volvo penta", "evc", "yanmar"
    ],
    "CAN_PHYSICAL_LAYER": [
        "can bus", "haberlesme", "120 ohm", "sonlandirma", "60 ohm", "direnc", "kisa devre", "acik devre", "bus off",
        "error passive", "osiloskop", "voltaj", "pinout", "obd", "deutsch", "can_h", "can_l", "gurultu", "parazit",
        "ground offset", "topraklama"
    ],
    "UDS_PROTOCOL": [
        "uds", "servis", "service", "0x10", "0x11", "0x14", "0x19", "0x22", "0x27", "0x28", "0x2e", "0x2f", "0x31",
        "0x34", "0x36", "0x37", "0x85", "nrc", "seed", "key", "guvenlik", "oturum", "did", "routine", "flash"
    ],
    "ELECTRICAL_STARTING": [
        "mars", "mars basmiyor", "mars almiyor", "gec calisma", "aku", "alternator", "sarj dinamosu", "konjektor",
        "sigorta", "role", "tik sesi", "kutup basi", "voltaj dusuk", "akinti", "kacak"
    ],
}


class AutomotiveTokenizer:
    """Sub-millisecond, typo-tolerant bilingual morphological tokenizer."""

    TURKISH_CHAR_MAP = str.maketrans({
        "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i", "İ": "i",
        "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u"
    })

    COMMON_SUFFIXES = [
        "lerden", "lardan", "lerinden", "larindan", "lerinde", "larinda", "lerinin", "larinin",
        "lerdeki", "lardaki", "dan", "den", "tan", "ten", "nin", "nin", "nun", "nün", "in", "in", "un", "ün",
        "ler", "lar", "daki", "deki", "teki", "taki", "e", "a", "ye", "ya", "de", "da", "te", "ta",
        "ing", "ed", "s", "es", "tion", "tions", "ment"
    ]

    @classmethod
    def normalize_text(cls, text: str) -> str:
        """Strip accents, lowercase, and clean punctuation."""
        lowered = text.translate(cls.TURKISH_CHAR_MAP).lower()
        cleaned = re.sub(r"[^\w\s\-\.]", " ", lowered)
        return re.sub(r"\s+", " ", cleaned).strip()

    @classmethod
    def lemmatize_word(cls, word: str) -> str:
        """Deterministic stemmer stripping common automotive nominal suffixes."""
        if len(word) <= 4:
            return word
        for suffix in sorted(cls.COMMON_SUFFIXES, key=len, reverse=True):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                return word[:-len(suffix)]
        return word

    @classmethod
    def extract_semantic_intents(cls, text: str) -> dict[str, float]:
        """Extract matching domain intents with confidence score (0.0 to 1.0)."""
        norm_text = cls.normalize_text(text)
        tokens = [cls.lemmatize_word(w) for w in norm_text.split()]
        scores: dict[str, float] = {}

        for domain, keywords in AUTOMOTIVE_SEMANTIC_DICTIONARY.items():
            match_count = 0.0
            for kw in keywords:
                norm_kw = cls.normalize_text(kw)
                if " " in norm_kw:
                    if norm_kw in norm_text:
                        match_count += 2.5
                else:
                    lem_kw = cls.lemmatize_word(norm_kw)
                    if lem_kw in tokens or norm_kw in tokens:
                        match_count += 1.0
                    else:
                        for t in tokens:
                            if len(t) >= 5 and cls._levenshtein_distance(t, lem_kw) <= 1:
                                match_count += 0.8
                                break
            if match_count > 0:
                scores[domain] = min(1.0, match_count / 3.0)

        return scores

    @staticmethod
    def _levenshtein_distance(s1: str, s2: str) -> int:
        if abs(len(s1) - len(s2)) > 1:
            return 2
        if s1 == s2:
            return 0
        d: dict[tuple[int, int], int] = {}
        len1, len2 = len(s1), len(s2)
        for i in range(-1, len1 + 1):
            d[(i, -1)] = i + 1
        for j in range(-1, len2 + 1):
            d[(-1, j)] = j + 1
        for i in range(len1):
            for j in range(len2):
                cost = 0 if s1[i] == s2[j] else 1
                d[(i, j)] = min(
                    d[(i - 1, j)] + 1,
                    d[(i, j - 1)] + 1,
                    d[(i - 1, j - 1)] + cost
                )
                if i > 0 and j > 0 and s1[i] == s2[j - 1] and s1[i - 1] == s2[j]:
                    d[(i, j)] = min(d[(i, j)], d[(i - 2, j - 2)] + 1)
        return d[(len1 - 1, len2 - 1)]


# ============================================================================
# CAUSAL BAYESIAN & DETERMINISTIC INFERENCE ENGINE
# ============================================================================

class CausalBayesianInferenceEngine:
    """Exact probabilistic inference calculating P(Fault_i | Evidence) and synthesizing 4-stage technician reports."""

    @staticmethod
    def explain_can_packet(
        can_id_hex_or_int: str | int,
        payload: bytes | list[int] | str = b"",
    ) -> str:
        text, actions = explain_can_packet(can_id_hex_or_int, payload)
        return attach_action_triggers(text, actions)

    @staticmethod
    def explain_traffic_metrics(
        bus_metrics: dict[str, Any],
        user_query: str = "",
    ) -> str:
        return explain_traffic_metrics(bus_metrics, user_query)

    @classmethod
    def evaluate_diagnostic_query(
        cls,
        user_query: str,
        active_dtcs: list[dict[str, Any]],
        telemetry: dict[str, Any],
        bus_metrics: dict[str, Any] | None = None,
    ) -> str:
        """Generate comprehensive 4-stage master technician report."""
        # M-18 (P2-17): knowledge bases load lazily at first diagnostic use
        # (idempotent) instead of at module import.
        ensure_external_dtc_database_loaded()
        if bus_metrics:
            telemetry = {**telemetry, **bus_metrics}
        intents = AutomotiveTokenizer.extract_semantic_intents(user_query)
        norm_query = AutomotiveTokenizer.normalize_text(user_query)

        # 0. CAN Traffic & Bus Load Anomaly Awareness
        is_traffic_query = any(w in norm_query for w in ["trafik", "hat yuku", "bus load", "anomali", "error frame", "hata karesi", "patlama", "babbling"])
        if is_traffic_query and any(w in norm_query for w in ["durum", "nasil", "yuku", "yuzde", "load", "anomali", "rapor", "analiz", "hat", "hata"]):
            traffic_rep = explain_traffic_metrics(telemetry, user_query)
            actions = [make_uds_clear_dtc_action()]
            return attach_action_triggers(traffic_rep, actions)

        # 0.1 Direct Diagnostic Action Requests (UDS & J1939 Actionable Triggers)
        has_dtc_in_query = bool(re.search(r"\b([PBUC][0-9A-F]{4})\b", user_query, re.IGNORECASE))
        has_spn_in_query = bool(re.search(r"\bspn\s*([0-9]+)\b", norm_query))

        # J1939 DM11 Clear DTC request
        is_dm11_action = any(w in norm_query for w in ["dm11", "j1939 ariza sil", "agir vasita ariza sil", "j1939 temizle", "pgn 65235"])
        if is_dm11_action and not has_dtc_in_query:
            text = (
                "🚛 **SAE J1939 DM11 (PGN 65235 - Diagnostic Data Clear):**\n"
                "• **Protokol:** Ağır vasıta ticari araçlarda aktif ve geçmiş DM1 arıza kayıtlarını siler.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak DM11 silme komutunu gönderebilirsiniz."
            )
            return attach_action_triggers(text, [make_j1939_dm11_action()])

        # J1939 DM1 Query request
        is_dm1_action = any(w in norm_query for w in ["dm1 oku", "j1939 dm1", "agir vasita ariza oku", "aktif ariza oku", "dm1 sorgula"])
        if is_dm1_action and not has_spn_in_query:
            text = (
                "🚛 **SAE J1939 DM1 (PGN 65226 - Active Diagnostic Trouble Codes):**\n"
                "• **Protokol:** Ağır vasıta hattında aktif arıza lambaları (MIL, Red Stop, Amber) ve SPN/FMI kayıtlarını dinler.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak DM1 durumunu sorgulayabilirsiniz."
            )
            return attach_action_triggers(text, [make_j1939_dm1_action()])

        # VIN Read request
        is_vin_action = any(w in norm_query for w in ["vin oku", "sasi no oku", "sasi numarasi oku", "read vin", "chassis number", "f190 oku"]) or (
            ("vin" in norm_query or "sasi" in norm_query) and any(w in norm_query for w in ["nasil", "oku", "nereden", "ogren", "sorgula", "nedir"])
        )
        if is_vin_action and not has_dtc_in_query:
            text = (
                "📄 **UDS 0x22 ReadDataByIdentifier (DID 0xF190 - VIN):**\n"
                "• **Servis:** Araç Şasi Numarası (VIN) doğrudan motor veya gövde kontrol ünitesinden okunur.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak UDS 0x22 F190 sorgusunu yürütebilirsiniz."
            )
            return attach_action_triggers(text, [make_uds_read_vin_action()])

        # Clear DTC request (generic UDS)
        is_clear_action = any(w in norm_query for w in ["dtc temizle", "ariza sil", "arizalari sil", "hata kodlarini sil", "hafizayi sil", "hafizayi temizle", "clear dtc", "hata sil", "0x14"])
        if is_clear_action and not has_dtc_in_query and not has_spn_in_query and not is_dm11_action:
            text = (
                "🧹 **UDS 0x14 ClearDiagnosticInformation (DTC Temizle):**\n"
                "• **Servis:** ECU hata hafızasındaki aktif ve geçmiş tüm DTC arıza kayıtları sıfırlanır.\n"
                "• **Güvenlik:** Çift operatör onayı ve aracın duruyor olması (0.0 km/s) zorunludur.\n"
                "• **İşlem:** Aşağıdaki onaylı butona tıklayarak temizleme komutunu iletebilirsiniz."
            )
            return attach_action_triggers(text, [make_uds_clear_dtc_action()])

        # Diagnostic Session Control request
        is_session_action = any(w in norm_query for w in ["oturum degistir", "extended session", "genisletilmis oturum", "session degistir", "0x10"])
        if is_session_action and not has_dtc_in_query and not has_spn_in_query:
            text = (
                "🔄 **UDS 0x10 DiagnosticSessionControl (Extended Session):**\n"
                "• **Servis:** ECU teşhis oturumu Genişletilmiş Oturum (0x03 Extended) moduna geçirilir.\n"
                "• **Amaç:** Gelişmiş test rutinleri (0x31) ve yazma işlemleri için gereklidir.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak oturumu değiştirebilirsiniz."
            )
            return attach_action_triggers(text, [make_uds_session_action(3)])

        # ECU Reset request
        is_reset_action = any(w in norm_query for w in ["ecu reset", "beyin reset", "hard reset", "beyni sifirla", "0x11"])
        if is_reset_action and not has_dtc_in_query:
            text = (
                "⚡ **UDS 0x11 ECUReset (Hard Reset):**\n"
                "• **Servis:** ECU mikrodenetleyicisi donanımsal olarak baştan başlatılır.\n"
                "• **Güvenlik:** Araç duruyor olmalı ve kullanıcı onayı gereklidir.\n"
                "• **İşlem:** Aşağıdaki eylem butonuna tıklayarak ECU Reset komutunu iletebilirsiniz."
            )
            return attach_action_triggers(text, [make_uds_ecu_reset_action(1)])

        # 0. CAN Frame Forensics (e.g. from right-click context menu or frame questions)
        is_error_frame = "(ERR)" in user_query or "Error Frame" in user_query or "isErrorFrame" in user_query or "hata karesi" in norm_query or "0x00000000" in user_query or "0x0000000" in user_query
        if is_error_frame and not re.search(r"\b([PBUC][0-9A-F]{4})\b", user_query, re.IGNORECASE):
            return (
                "🔴 **CAN Hata Karesi (Error Frame / Bus Error):**\n"
                "• **Durum:** Fiziksel katman hatası (Bit Stuffing veya CRC hatası / Active Error Flag) nedeniyle çerçeve iletimi durduruldu.\n"
                "• **Olası Nedenler:** Hat paraziti, sonlandırma direnci eksikliği veya yanlış baudrate.\n"
                "• **Hızlı Test:** OBD Pin 6 (CAN-H) ve Pin 14 (CAN-L) arası direnci ölçün (Nominal: 60.0 Ω ±3Ω / 120Ω sonlandırma)."
            )

        # 0.2 Specific CAN Packet / Hex Payload Explainer
        payload_bytes = extract_hex_payload_from_query(user_query)

        # 0.5. DBC File & Signal Map Queries
        is_dbc_query = any(w in norm_query for w in ["dbc", "sinyal haritasi", "can veritabani", "sinyal listesi"])
        if is_dbc_query:
            if any(w in norm_query for w in ["nedir", "ne demek", "nasil"]) and len(norm_query.split()) <= 4:
                return (
                    "📦 **DBC (CAN Database) Nedir?**\n"
                    "• CAN veri yolundaki ham bit/bayt mesajlarını fiziksel değerlere (RPM, Hız, Sıcaklık) çeviren sinyal haritasıdır.\n"
                    "• Projemizde 180+ hazır DBC (Binek, Ağır Vasıta J1939, Marin N2K, EV BMS) bulunmaktadır."
                )
            if any(w in norm_query for w in ["liste", "mevcut", "hangi", "neler var", "katalog"]) and len(norm_query.split()) <= 5:
                return (
                    "📦 **Kayıtlı DBC Kütüphanesi Özeti:**\n"
                    "• **Binek Araçlar:** 148 dosya (VW, BMW, Toyota, Ford, Honda, Hyundai vb.)\n"
                    "• **EV & Batarya (BMS):** 17 dosya (Tesla, Nissan Leaf, Kona EV, BYD vb.)\n"
                    "• **Ağır Vasıta (J1939):** 8 dosya (Actros, Scania, Volvo, Cummins, Cat)\n"
                    "• **Marin & Tarım:** 5 dosya (NMEA 2000, ISOBUS)\n"
                    "💡 Belirli bir model aramak için: *'golf dbc'*, *'bmw dbc'*, *'tesla dbc'*"
                )

            matched_dbcs = search_dbc_catalog(user_query)
            if matched_dbcs:
                res_lines = [f"📦 **Eşleşen DBC Dosyaları ({len(matched_dbcs)} adet):**"]
                for d in matched_dbcs[:4]:
                    res_lines.append(f"• **{d['filename']}** ({d['category']})\n  ↳ {d['messages_count']} Mesaj, {d['signals_count']} Sinyal [{d['protocol']}]")
                if len(matched_dbcs) > 4:
                    res_lines.append(f"*(+{len(matched_dbcs) - 4} diğer dosya)*")
                return "\n".join(res_lines)
            else:
                clean_term = re.sub(r"\b(dbc|can|dosyasi|var|mi|araniyor|icin|hakkinda|bilgi|ver)\b", "", norm_query).strip()
                return (
                    f"❌ **DBC Bulunamadı:** '{clean_term or user_query}' ile eşleşen bir DBC dosyası veritabanında mevcut değil.\n"
                    f"💡 Kütüphanemizde 180+ hazır DBC bulunmaktadır. Kendi .dbc dosyanızı `data/dbc/` klasörüne ekleyebilirsiniz."
                )

        can_id_match = re.search(r"(?:CAN ID|can_id|id)\s*[:=]?\s*(0x[0-9A-Fa-f]+)", user_query, re.IGNORECASE)
        if not can_id_match and not any(w in norm_query for w in ["nrc", "negatif", "dtc", "sid", "did", "servis"]):
            can_id_match = re.search(r"\b(0x[0-9A-Fa-f]{3,8})\b", user_query, re.IGNORECASE)
        can_id_hex = can_id_match.group(1).upper() if can_id_match else ""

        # Specific Packet Explainer for CAN frame with payload or diagnostic intent
        if can_id_hex and payload_bytes:
            if not any(k in can_id_hex for k in ["1808E5", "1807E5", "1809E5", "18F020"]):
                expl_text, actions = explain_can_packet(can_id_hex, payload_bytes)
                return attach_action_triggers(expl_text, actions)
        elif not can_id_hex and payload_bytes and len(payload_bytes) >= 2 and any(w in norm_query for w in ["payload", "paket", "veri", "byte", "bayt", "hex"]):
            expl_text, actions = explain_can_packet(0x7E0, payload_bytes)
            return attach_action_triggers(expl_text, actions)

        # EV BMS Specific Frames
        if "1808E5" in can_id_hex or "0x1808E5F4" in user_query:
            cell_min = telemetry.get("bms_cell_voltage_min_v")
            cell_max = telemetry.get("bms_cell_voltage_max_v")
            meas = f"Min={cell_min:.3f}V, Max={cell_max:.3f}V (Delta V={(cell_max-cell_min)*1000:.0f}mV) (ölçüm)" if (cell_min is not None and cell_max is not None) else "Canlı ölçüm yok"
            return (
                f"⚡ **EV BMS Hücre Voltajları (0x1808E5F4 - PGN 61447):**\n"
                f"• **Protokol:** ISO 11898-2 (EV Yüksek Voltaj BMS)\n"
                f"• **Ölçüm Durumu:** {meas}\n"
                f"• **Hedef:** Hücre voltaj farkı <30 mV olmalıdır."
            )

        if "1807E5" in can_id_hex or "0x1807E5F4" in user_query:
            soc = telemetry.get("bms_soc_percent")
            soh = telemetry.get("bms_soh_percent")
            meas = f"SOC=%{soc:.1f}, SOH=%{soh:.1f}" if soc is not None and soh is not None else "Canlı ölçüm bekleniyor"
            return (
                f"⚡ **EV BMS Şarj & Sağlık (0x1807E5F4 - PGN 61446):**\n"
                f"• **Protokol:** ISO 11898-2 (BMS ECU 0xF4)\n"
                f"• **Durum:** {meas}"
            )

        if "1809E5" in can_id_hex or "0x1809E5F4" in user_query:
            bat_temp = telemetry.get("bms_pack_temp_c")
            temp_str = f"{bat_temp:.1f}°C" if bat_temp is not None else "Canlı ölçüm bekleniyor"
            return (
                f"⚡ **EV BMS Termal Yönetimi (0x1809E5F4 - PGN 61448):**\n"
                f"• **Paket Sıcaklığı:** {temp_str}\n"
                f"• **Hedef:** Nominal çalışma aralığı 20°C - 35°C."
            )

        if "18F020" in can_id_hex or "0x18F020F4" in user_query:
            isolation = telemetry.get("bms_hv_isolation_mohm")
            iso_str = f"{isolation:.1f} MΩ (Nominal >500 Ω/V)" if isolation is not None else "Canlı ölçüm bekleniyor"
            return (
                f"⚡ **EV BMS Yüksek Voltaj İzolasyonu (0x18F020F4):**\n"
                f"• **İzolasyon Direnci:** {iso_str}\n"
                f"• **Kontrol:** Kontaktör durumları ve şasi kaçak izleme."
            )

        # 1. Direct DTC code match in prompt (P0xxx, C1xxx, U0xxx, B0xxx)
        dtc_match = re.search(r"\b([PBUC][0-9A-F]{4})\b", user_query, re.IGNORECASE)
        direct_dtc = dtc_match.group(1).upper() if dtc_match else None

        # 2. Check for SPN numbers (e.g. SPN 100, SPN 102, SPN 3251, SPN 641)
        spn_match = re.search(r"\bspn\s*([0-9]+)\b", norm_query)
        if spn_match:
            spn_num = spn_match.group(1)
            spn_key = f"SPN{spn_num}"
            if spn_key in EXPERT_KNOWLEDGE_BASE:
                direct_dtc = spn_key
            else:
                j1939_db = get_j1939_spn_database()
                spn_entry = j1939_db.get("spns", {}).get(f"SPN_{spn_num}")
                if spn_entry:
                    return cls._format_j1939_technician_report(spn_entry, norm_query, telemetry)
                else:
                    return (
                        f"⚠️ **[SPN {spn_num}] Kaydı Bulunamadı:**\n"
                        f"Bu SPN parametresi yerel J1939 veritabanında kayıtlı değil.\n"
                        f"• Üreticiye özel (Proprietary) bir PGN/SPN olabilir. SAE J1939-71 kataloğundan teyit edin."
                    )

        # 3. Check for UDS NRC codes (e.g. NRC 0x22, NRC 0x33, NRC 0x78)
        nrc_match = re.search(r"\b(?:nrc|negatif yanit)\s*(?:0x)?([0-9a-f]{2})\b", norm_query)
        if nrc_match:
            nrc_hex = f"0x{nrc_match.group(1).upper()}"
            if nrc_hex in UDS_NRC_CATALOG:
                nrc_info = UDS_NRC_CATALOG[nrc_hex]
                return (
                    f"🛑 **UDS Negatif Yanıt ({nrc_hex} - {nrc_info['name']}):**\n"
                    f"• **Neden:** {nrc_info['cause']}\n"
                    f"• **Çözüm:** {nrc_info['action']}\n"
                    f"• **Ön Koşul:** `0x10 0x03` Extended Session, Kontak AÇIK/Motor KAPALI (Ignition ON, Engine OFF), Akü >12.5V."
                )
            else:
                return f"⚠️ **[NRC {nrc_hex}] Tanımsız:** Standart ISO 14229 kataloğunda bu negatif yanıt kodu tanımlı değil."

        # 3.5 Check for NHTSA Safety Recalls & TSB Queries
        is_recall_query = any(w in norm_query for w in ["recall", "geri cagirma", "tsb", "teknik bulten", "kampanya", "nhtsa"])
        if is_recall_query:
            found_year = None
            year_match = re.search(r"\b(201[8-9]|202[0-5])\b", user_query)
            if year_match:
                found_year = int(year_match.group(1))

            found_make = None
            known_makes = [
                ("ford", "ford"), ("lincoln", "lincoln"), ("tesla", "tesla"),
                ("chevrolet", "chevrolet"), ("chevy", "chevrolet"), ("gm", "chevrolet"),
                ("gmc", "gmc"), ("cadillac", "cadillac"), ("toyota", "toyota"),
                ("lexus", "lexus"), ("volkswagen", "volkswagen"), ("vw", "volkswagen"),
                ("audi", "audi"), ("bmw", "bmw"), ("hyundai", "hyundai"),
                ("kia", "kia"), ("ram", "ram"), ("jeep", "jeep"),
                ("mercedes", "mercedes-benz"), ("volvo", "volvo"),
            ]
            for kw, mname in known_makes:
                if kw in norm_query:
                    found_make = mname
                    break

            query_kw = None
            for kw in [
                "f-150", "f150", "mach-e", "mache", "explorer", "escape", "bronco",
                "model 3", "model y", "model s", "model x",
                "bolt", "silverado", "corvette", "lyriq", "sierra",
                "rav4", "prius", "camry", "corolla", "highlander", "tundra",
                "id.4", "id4", "tiguan", "atlas", "e-tron", "etron", "q5", "a4",
                "330i", "i4", "ix", "x5",
                "ioniq 5", "ioniq", "ev6", "telluride", "wrangler",
                "trailer brake", "gateway", "bms", "battery", "batarya",
                "contactor", "direksiyon", "steering", "fren", "brake",
                "software", "yazilim", "ota", "park"
            ]:
                if kw in norm_query:
                    query_kw = kw.replace("f150", "f-150").replace("mache", "mach-e").replace("id4", "id.4").replace("etron", "e-tron")
                    break

            recalls = search_nhtsa_recalls(
                make=found_make,
                year=found_year,
                query=query_kw or (None if found_make else norm_query.replace("recall", "").replace("geri cagirma", "").strip()),
                limit=3,
            )

            if recalls:
                reports = [format_nhtsa_recall_report(r) for r in recalls]
                header = (
                    f"📢 **NHTSA Resmi Güvenlik Geri Çağırma (Recall) & TSB Raporu:**\n"
                    f"🔎 **Kriter:** {found_make.upper() if found_make else 'Tümü'} | Yıl: {found_year or 'Tümü'} | Eşleşen Kampanya: {len(recalls)} adet\n\n"
                )
                return header + ("\n\n" + "─" * 40 + "\n\n").join(reports)
            else:
                return (
                    f"ℹ️ **NHTSA Geri Çağırma Arama Sonucu:**\n"
                    f"Belirtilen kriterlere uygun (`{user_query}`) geri çağırma kaydı bulunamadı.\n"
                    "Lütfen araç modeli (Örn: *'Ford F-150'*, *'Tesla Model 3'*) belirterek deneyin."
                )

        # 3.8 CAN ID Specific lookup if not matched above
        if can_id_hex and not direct_dtc:
            val = int(can_id_hex, 16)
            if val == 0x7DF:
                return "📡 **CAN ID 0x7DF:** Standart OBD-II Fonksiyonel Yayın İsteği (Tüm bağlı ECU'lara eşzamanlı genel sorgu)."
            elif 0x7E0 <= val <= 0x7E7:
                ecu_name = "Motor (ECM/PCM)" if val == 0x7E0 else ("Şanzıman (TCM)" if val == 0x7E1 else f"ECU_{val - 0x7E0}")
                return f"📡 **CAN ID {can_id_hex}:** ISO 15765-4 Standart OBD-II / UDS Fiziksel İstek Hattı ({ecu_name})."
            elif 0x7E8 <= val <= 0x7EF:
                ecu_name = "Motor (ECM/PCM)" if val == 0x7E8 else ("Şanzıman (TCM)" if val == 0x7E9 else f"ECU_{val - 0x7E8}")
                return f"📡 **CAN ID {can_id_hex}:** ISO 15765-4 Standart OBD-II / UDS Fiziksel Yanıt Hattı ({ecu_name})."
            else:
                return (
                    f"⚠️ **CAN ID Tanımsız ({can_id_hex}):**\n"
                    f"Bu mesaj kimliği için yerel veritabanında veya protokol motorunda kayıtlı bir sinyal tanımı bulunamadı.\n"
                    f"• Sniffer tablosundan canlı veri uzunluğunu (DLC) ve bayt değişimlerini inceleyebilirsiniz."
                )

        # 4. If direct DTC is identified, render structured 4-stage technician report
        target_code = direct_dtc
        is_general_fault_query = any(w in norm_query for w in ["ariza", "dtc", "hata kodu", "fault", "nedir", "analiz et", "neden"])
        if not target_code and not can_id_hex and is_general_fault_query and active_dtcs:
            first_dtc = active_dtcs[0]
            if isinstance(first_dtc, dict):
                spn = first_dtc.get("spn")
                code_str = str(first_dtc.get("code", ""))
                if spn and f"SPN{spn}" in EXPERT_KNOWLEDGE_BASE:
                    target_code = f"SPN{spn}"
                elif code_str in EXPERT_KNOWLEDGE_BASE:
                    target_code = code_str

        if target_code:
            if target_code in EXPERT_KNOWLEDGE_BASE:
                return cls._format_4stage_technician_report(target_code, telemetry)
            else:
                cat_char = target_code[0].upper()
                is_oem = len(target_code) > 1 and target_code[1] in ("1", "2")
                cat_desc = {
                    "P": "Güç Aktarımı (Powertrain)",
                    "C": "Şasi / ABS / ESP (Chassis)",
                    "B": "Gövde / Konfor (Body)",
                    "U": "Ağ / CAN İletişimi (Network)",
                }.get(cat_char, "Bilinmeyen")
                oem_note = "Üreticiye Özel (OEM-Specific)" if is_oem else "Standart SAE"
                return (
                    f"⚠️ **[{target_code}] Arıza Kodu Bulunamadı:**\n"
                    f"Bu kod yerel teşhis kütüphanesinde kayıtlı değil.\n"
                    f"• **Kategori:** {cat_desc} ({oem_note})\n"
                    f"• **Tavsiye:** Aracın yetkili servis kılavuzunu inceleyin veya UDS `0x19 0x02` servisi ile çevre koşullarını (Freeze Frame) okuyun."
                )

        # 5. Semantic Intent Matching using Causal Graph
        if intents.get("EV_HV_BATTERY", 0.0) >= 0.5 or any(w in norm_query for w in ["izolasyon", "hvil", "batarya", "megger", "precharge", "turtle"]):
            if "izolasyon" in norm_query or "megger" in norm_query or "kacak" in norm_query:
                return cls._format_4stage_technician_report("P0AA6", telemetry)
            if "hvil" in norm_query or "interlock" in norm_query or "salter" in norm_query:
                return cls._format_4stage_technician_report("P0A0B", telemetry)
            if "precharge" in norm_query or "kontaktor" in norm_query:
                return cls._format_4stage_technician_report("P0AA1", telemetry)
            return cls._format_4stage_technician_report("P0A80", telemetry)

        if intents.get("HEAVY_DUTY_J1939", 0.0) >= 0.5 or any(w in norm_query for w in ["adblue", "def", "dpf", "scr", "yag basinci", "fmi", "derate"]):
            if "yag" in norm_query:
                return cls._format_4stage_technician_report("SPN100", telemetry)
            if "dpf" in norm_query or "rejenerasyon" in norm_query:
                return cls._format_4stage_technician_report("SPN3251", telemetry)
            if "adblue" in norm_query or "def" in norm_query or "kalite" in norm_query:
                return cls._format_4stage_technician_report("SPN3364", telemetry)
            if "enjektor" in norm_query:
                return cls._format_4stage_technician_report("SPN651", telemetry)
            if "fren" in norm_query or "hava" in norm_query:
                return cls._format_4stage_technician_report("SPN1087", telemetry)
            return cls._format_4stage_technician_report("SPN4364", telemetry)

        if intents.get("MARINE_NMEA2000", 0.0) >= 0.5 or any(w in norm_query for w in ["impeller", "cark", "marin", "deniz suyu", "esnjor", "mixing elbow", "pervane", "slip"]):
            if "impeller" in norm_query or "cark" in norm_query or "deniz suyu" in norm_query:
                return cls._format_4stage_technician_report("N2K_IMPELLER", telemetry)
            if "egzoz" in norm_query or "dirsek" in norm_query or "elbow" in norm_query or "waterlock" in norm_query:
                return cls._format_4stage_technician_report("N2K_EXHAUST_ELBOW", telemetry)
            if "esnjor" in norm_query or "kirec" in norm_query or "yuksek yuk" in norm_query:
                return cls._format_4stage_technician_report("N2K_HEAT_EXCHANGER", telemetry)
            return cls._format_4stage_technician_report("N2K_PROP_SLIP", telemetry)

        if intents.get("CAN_PHYSICAL_LAYER", 0.0) >= 0.5 or any(w in norm_query for w in ["120 ohm", "60 ohm", "sonlandirma", "direnc", "can h", "can l", "kisa devre", "pinout"]):
            if "voltaj" in norm_query or "bias" in norm_query or "offset" in norm_query:
                return cls._format_4stage_technician_report("CAN_VOLT_FAULT", telemetry)
            return cls._format_4stage_technician_report("CAN_TERM_60", telemetry)

        if intents.get("MISFIRE", 0.0) >= 0.5 or any(w in norm_query for w in ["tekliyor", "tekleme", "sarsinti", "atesleme", "buji"]):
            return cls._format_4stage_technician_report("P0300", telemetry)

        if intents.get("TURBO_BOOST", 0.0) >= 0.5 or any(w in norm_query for w in ["turbo", "overboost", "underboost", "kara duman", "bayiliyor", "cekis"]):
            return cls._format_4stage_technician_report("P0234", telemetry)

        if intents.get("OVERHEAT_COOLING", 0.0) >= 0.5 or any(w in norm_query for w in ["hararet", "termostat", "radyator", "fan", "su kaynatiyor", "ust kapak contasi"]):
            return cls._format_4stage_technician_report("SPN110", telemetry)

        if "u0100" in norm_query or "iletisim koptu" in norm_query or "beyin cevap vermiyor" in norm_query:
            return cls._format_4stage_technician_report("U0100", telemetry)

        # 6. Fallback General Diagnosis (Honest about lack of data, concise and simplified)
        fallback_text = (
            f"ℹ️ **Bilgi Bulunamadı:** '{user_query[:60]}' hakkında yerel teşhis veritabanında doğrudan bir eşleşme bulunamadı.\n\n"
            f"💡 **Desteklenen Sorgu Formatları:**\n"
            f"• **Arıza Kodları:** *P0300*, *U0100*, *C0035*, *P1260*\n"
            f"• **Ağır Vasıta SPN:** *SPN 100 FMI 1*, *SPN 641*\n"
            f"• **DBC Dosyaları:** *golf dbc*, *tesla dbc*, *j1939 dbc*\n"
            f"• **Geri Çağırma / TSB:** *Ford F-150 recall*, *Tesla Model 3 kampanya*\n"
            f"• **Fiziksel Katman:** *120 ohm testi*, *CAN hata karesi*"
        )
        extracted = extract_action_triggers(user_query)
        if extracted:
            return attach_action_triggers(fallback_text, extracted)
        return fallback_text

    @classmethod
    def _format_4stage_technician_report(cls, code: str, telemetry: dict[str, float]) -> str:
        """Format an industry-standard 4-stage master technician field guide in concise format."""
        info = EXPERT_KNOWLEDGE_BASE[code]
        rpm = telemetry.get("EngineSpeed", 0.0)
        boost = telemetry.get("BoostPressure", 0.0)
        temp = telemetry.get("CoolantTemp", 85.0)

        causes = info.get("causes", [])
        top_causes = causes[:2] if causes else ["İlgili alt sistem elektriksel veya mekanik parametre sapması."]
        causes_formatted = "\n".join(f"  • {c}" for c in top_causes)

        steps = info.get("steps", [])
        steps_lines: list[str] = []
        for idx, s in enumerate(steps[:2]):
            if len(s) >= 2:
                steps_lines.append(f"  {idx + 1}. {s[0]} *(Hedef: {s[1]})*")
            elif len(s) == 1:
                steps_lines.append(f"  {idx + 1}. {s[0]}")
        steps_formatted = "\n".join(steps_lines) if steps_lines else "  • Tesisat ve sensör bağlantılarını kontrol edin."

        measurement_block = info.get("measurement", "Nominal voltaj ve şasi dirençlerini test edin.")
        routine_block = info.get("uds_routine", "UDS Service 0x14 (DTC Temizleme)")

        # Check if there are related NHTSA recalls for this code (max 1)
        nhtsa_block = ""
        related_recalls = search_nhtsa_recalls(query=code, limit=1)
        if not related_recalls:
            title_lower = info.get("title", "").lower()
            for kw in ["trailer brake", "contactor", "interlock", "theft", "pats", "purge"]:
                if kw in title_lower:
                    related_recalls = search_nhtsa_recalls(query=kw, limit=1)
                    break
        if related_recalls:
            r = related_recalls[0]
            nhtsa_block = f"\n📢 **NHTSA Geri Çağırma:** {r.get('campaign_number')} ({r.get('manufacturer')}) — {r.get('component')}"

        telemetry_str = f" | {rpm:.0f} RPM, {boost:.2f} Bar, {temp:.1f}°C" if rpm > 0 or boost > 0 else ""

        report_text = (
            f"🚨 **[{code}] — {info.get('title', code)}** *(Öncelik: {info.get('severity', 'MEDIUM')})*\n"
            f"🏷️ **Alt Sistem:** {info.get('subsystem', 'Genel Teşhis')}{telemetry_str}\n\n"
            f"🔍 **Olası Nedenler:**\n{causes_formatted}\n\n"
            f"📋 **4-AŞAMALI USTA TEKNİSYEN SAHA ONARIM KILAVUZU:**\n"
            f"**Aşama 1: Görsel & Mekanik Kontrol:**\n{steps_formatted}\n"
            f"⚡ **Aşama 2: Kesin Multimetre & Osiloskop Toleransları:**\n  • {measurement_block}\n"
            f"💻 **Aşama 3: UDS / J1939 Özel Teşhis Rutinleri:**\n  • `{routine_block}`\n"
            f"🔧 **Aşama 4: Parça Değişim & Adaptasyon Prosedürü:**\n  • Parça değişimi sonrası kontak açıkken `UDS 0x14` ile arıza hafızasını temizleyin."
            f"{nhtsa_block}"
        )
        actions = [make_uds_clear_dtc_action()]
        if "0x31" in routine_block:
            m = re.search(r"0x([0-9a-fA-F]{4})", routine_block)
            if m:
                actions.append(make_uds_routine_action(int(m.group(1), 16)))
        actions.extend(extract_action_triggers(report_text))
        return attach_action_triggers(report_text, actions)

    @classmethod
    def _format_j1939_technician_report(cls, spn_entry: dict[str, Any], query: str, telemetry: dict[str, float]) -> str:
        """Format a heavy-duty commercial vehicle J1939 SPN & FMI diagnostic guide in concise format."""
        spn = spn_entry.get("spn", 0)
        name = spn_entry.get("name", "Bilinmeyen SPN")
        title_tr = spn_entry.get("title_tr", name)
        subsystem = spn_entry.get("subsystem", "Ağır Vasıta J1939")
        pgn = spn_entry.get("associated_pgn", 0)
        unit = spn_entry.get("unit", "-")
        desc = spn_entry.get("description", "")
        range_info = spn_entry.get("range", [spn_entry.get("range_min", 0), spn_entry.get("range_max", 0)])
        range_str = f"{range_info[0]}..{range_info[1]} {unit}" if isinstance(range_info, list) and len(range_info) >= 2 else f"{range_info} {unit}"

        fmi_match = re.search(r"\bfmi\s*([0-9]+)\b", query)
        fmi_info_str = ""
        if fmi_match:
            fmi_num = fmi_match.group(1)
            fmi_tree = spn_entry.get("fault_matrix", {}).get(fmi_num)
            if not fmi_tree:
                j1939_db = get_j1939_spn_database()
                fmi_def = j1939_db.get("fmi_definitions", {}).get(fmi_num, {})
                if fmi_def:
                    fmi_tree = {
                        "fmi_name": fmi_def.get("name", ""),
                        "fault_title": fmi_def.get("description_tr", ""),
                        "diagnostic_action": fmi_def.get("diagnostic_action", ""),
                        "severity": "MEDIUM",
                    }
            if fmi_tree:
                fmi_info_str = (
                    f"⚡ **FMI {fmi_num} ({fmi_tree.get('fmi_name', '')}):** "
                    f"{fmi_tree.get('fault_title', fmi_tree.get('description_tr', ''))}\n"
                    f"• **Eylem:** {fmi_tree.get('diagnostic_action', fmi_tree.get('action', 'Sensör devresini kontrol edin.'))}\n\n"
                )

        report_text = (
            f"🚛 **[SPN {spn}] — {title_tr} ({name})**\n"
            f"🏷️ **Alt Sistem:** {subsystem} | **PGN:** {pgn} | **Aralık:** {range_str}\n"
            f"📝 **Açıklama:** {desc[:140] + ('...' if len(desc) > 140 else '')}\n\n"
            f"{fmi_info_str}"
            f"📋 **SAE J1939-73 Saha Teşhis Adımları:**\n"
            f"1. CAN hattında PGN {pgn} periyodunu ve DM1 aktif arıza lambasını kontrol edin.\n"
            f"2. Sensör besleme voltajını (5V/12V) ve şasi hattını multimetre ile test edin."
        )
        actions = [make_j1939_dm1_action(), make_j1939_dm11_action()]
        return attach_action_triggers(report_text, actions)




# ============================================================================
# MAIN AI DIAGNOSTIC COPILOT (HYBRID LOCAL / CLOUD ENGINE)
# ============================================================================

class AiDiagnosticCopilot:
    """Intelligent reasoning engine analyzing DTCs, telemetry signals, and ECU health."""

    def __init__(
        self,
        gemini_api_key: str | None = None,
        openai_api_key: str | None = None,
        provider: str = "auto",
        secret_provider: SecretProvider | None = None,
    ) -> None:
        self._gemini_api_key = gemini_api_key
        # L-8 (P3-3): the OpenAI key is no longer a public plaintext
        # attribute (it leaked through repr()/pickling and survived
        # set_key_provider). Same vault-property pattern as the Gemini key.
        self._openai_api_key = openai_api_key
        self.provider = provider
        self._key_provider: SecretProvider | None = secret_provider

    def set_key_provider(self, secret_provider: SecretProvider) -> None:
        """Route all API key lookups through the secret vault (F-08).

        The key is never stored as a plain attribute and never logged.
        """
        self._key_provider = secret_provider
        # Drop any previously held plain-text key (L-8: BOTH providers now)
        self._gemini_api_key = None
        self._openai_api_key = None

    @property
    def gemini_api_key(self) -> str | None:
        """Resolve the Gemini key from the vault; plain ctor key only as legacy fallback."""
        if self._key_provider is not None:
            try:
                return self._key_provider.get_secret("GEMINI_API_KEY").decode("utf-8")
            except KeyError:
                return None
        return self._gemini_api_key

    @property
    def openai_api_key(self) -> str | None:
        """Resolve the OpenAI key from the vault; plain ctor key only as legacy fallback (L-8)."""
        if self._key_provider is not None:
            try:
                return self._key_provider.get_secret("OPENAI_API_KEY").decode("utf-8")
            except KeyError:
                return None
        return self._openai_api_key

    @staticmethod
    def _clean_and_parse_json(raw_text: str) -> dict[str, Any]:
        """Extract and parse JSON object from markdown, backticks, or conversational text."""
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1 and start < end:
            result: dict[str, Any] = json.loads(raw_text[start : end + 1])
            return result
        fallback: dict[str, Any] = json.loads(raw_text)
        return fallback

    def analyze_session(
        self,
        active_dtcs: list[dict[str, object]],
        telemetry_snapshot: dict[str, float],
        active_ecus: list[str],
    ) -> DiagnosticAnalysisReport:
        """Perform deterministic expert analysis or trigger Google Gemini / OpenAI LLM."""
        if self.provider == "openai" or (
            self.provider == "auto" and self.openai_api_key and len(self.openai_api_key.strip()) > 10
        ):
            try:
                return self._analyze_with_openai(active_dtcs, telemetry_snapshot, active_ecus)
            except Exception as exc:
                logger.warning("OpenAI API call failed, trying fallback", extra={"error": str(exc)})

        if self.gemini_api_key and len(self.gemini_api_key.strip()) > 10:
            try:
                return self._analyze_with_gemini(active_dtcs, telemetry_snapshot, active_ecus)
            except (urllib.error.URLError, json.JSONDecodeError, KeyError, TimeoutError, OSError) as exc:
                # M-02: never log raw exception strings from the API path —
                # urllib errors can echo the full request URL (which may carry
                # the API key). Log the type + safe code only.
                logger.warning(
                    "Gemini API call failed, falling back to local expert engine",
                    extra={"error_type": type(exc).__name__, "detail": getattr(exc, "reason", None)},
                )

        return self._analyze_local_expert(active_dtcs, telemetry_snapshot, active_ecus)

    def _analyze_local_expert(
        self,
        active_dtcs: list[dict[str, object]],
        telemetry_snapshot: dict[str, float],
        active_ecus: list[str],
    ) -> DiagnosticAnalysisReport:
        # M-18 (P2-17): lazy knowledge-base load at first analysis use.
        ensure_external_dtc_database_loaded()
        dtc_count = len(active_dtcs)
        rpm = telemetry_snapshot.get("EngineSpeed", 0.0)
        raw_boost = telemetry_snapshot.get("BoostPressure", 0.0)
        # Normalize boost: if > 10, it's in kPa (e.g. 120 kPa = 1.20 Bar)
        boost_bar = raw_boost / 100.0 if raw_boost > 10.0 else raw_boost
        coolant_temp = telemetry_snapshot.get("CoolantTemp", 85.0)

        likely_causes: list[str] = []
        steps: list[TroubleshootingStep] = []
        correlations: list[str] = []
        affected: list[str] = []
        severity = FaultSeverity.LOW
        # Evidence counters feeding the honest weighted confidence score (P0).
        scenario_matched_count = 0
        kb_matched_count = 0

        # Scenario 1: Oil Pressure Fault (SPN 100)
        if any(d.get("spn") == 100 for d in active_dtcs):
            scenario_matched_count += sum(1 for d in active_dtcs if d.get("spn") == 100)
            severity = FaultSeverity.CRITICAL_STOP
            affected.append("Motor Yağlama & Yatak Sistemi")
            likely_causes.append(
                "Kritik düşük yağ basıncı (Yağ pompası aşınması, karterde yağ eksilmesi veya filtre tıkanıklığı)"
            )
            steps.append(
                TroubleshootingStep(
                    1,
                    "Motoru derhal durdurun ve yağ çubuğundan yağ seviyesini kontrol edin.",
                    "Yağ Karteri / Çubuğu",
                    "Kolay (Görsel)",
                )
            )
            steps.append(
                TroubleshootingStep(
                    2,
                    "Mekanik yağ basınç göstergesi ile karter basıncını ölçün (Rölantide min 1.0 bar, 2000 RPM'de 3.0 bar).",
                    "Yağ Basınç Sensörü Portu",
                    "Orta (Alet Gerekir)",
                )
            )
            correlations.append(
                f"Kritik yağ basınç arızası mevcutken motor devri {rpm:.0f} RPM seviyesinde; yatak sarma riski çok yüksek!"
            )

        # Scenario 2: EV Battery Isolation Fault (P0AA6 / P0A0B)
        ev_codes = {"P0AA6", "P0A0B", "P0A80", "P0A93"}
        if any(str(d.get("code", "")).upper() in ev_codes for d in active_dtcs):
            scenario_matched_count += sum(1 for d in active_dtcs if str(d.get("code", "")).upper() in ev_codes)
            severity = FaultSeverity.CRITICAL_STOP
            affected.append("EV Yüksek Voltaj Güvenlik & Batarya")
            likely_causes.append("Yüksek voltaj izolasyon direnci düşüklüğü veya HVIL interlock güvenlik hattı kesintisi.")
            steps.append(
                TroubleshootingStep(
                    len(steps) + 1,
                    "LOTO güvenlik protokolünü uygulayın: MSD şalterini çekin, 10 dk bekleyin, 1000V DMM ile sıfır enerji teyidi yapın.",
                    "Manuel Servis Şalteri (MSD)",
                    "İleri (Servis)",
                )
            )
            steps.append(
                TroubleshootingStep(
                    len(steps) + 1,
                    "Fluke 1587 / Megger ile 500V/1000V DC testinde HV+ ve HV- hatlarının şasiye izolasyon direncini ölçün (>50 MΩ olmalıdır).",
                    "HV Güç Hatları & Kompresör",
                    "İleri (Servis)",
                )
            )
            correlations.append("Yüksek voltaj güvenlik kilidi devrede; kontaktörler ark yapmadan otomatik açıldı.")

        # Scenario 3: Cylinder Injector Faults (SPN 651 - SPN 656)
        injector_count = sum(1 for d in active_dtcs if isinstance(d.get("spn"), int) and 651 <= d.get("spn", 0) <= 656)
        if injector_count > 0:
            scenario_matched_count += injector_count
        for d in active_dtcs:
            spn = d.get("spn")
            if isinstance(spn, int) and 651 <= spn <= 656:
                cyl_idx = spn - 650
                severity = FaultSeverity.MEDIUM
                affected.append(f"Silindir #{cyl_idx} Yakıt Enjeksiyonu")
                likely_causes.append(f"Silindir #{cyl_idx} enjektör devresi arızası (Açık devre, kısa devre veya geri dönüş kaçağı).")
                steps.append(
                    TroubleshootingStep(
                        len(steps) + 1,
                        f"Silindir #{cyl_idx} enjektör bobin direncini (0.35 - 0.55 Ω) ölçün.",
                        f"{cyl_idx}. Silindir Enjektörü",
                        "Orta (Alet Gerekir)",
                    )
                )

        # Scenario 4: DPF Differential Pressure (SPN 3251 / SPN 3719)
        if any(d.get("spn") in {3251, 3719} or "DPF" in str(d.get("description", "")).upper() for d in active_dtcs):
            scenario_matched_count += sum(
                1
                for d in active_dtcs
                if d.get("spn") in {3251, 3719} or "DPF" in str(d.get("description", "")).upper()
            )
            severity = FaultSeverity.MEDIUM
            affected.append("Egzoz & DPF Sistemi")
            likely_causes.append("DPF partikül filtresi aşırı kurum yükü veya fark basınç sensörü arızası.")
            steps.append(
                TroubleshootingStep(
                    len(steps) + 1,
                    "DPF fark basınç sensörü hortumlarını ve kurum yükünü kontrol edin.",
                    "DPF Filtresi & Sensörü",
                    "Kolay (Görsel)",
                )
            )

        # Scenario 5: Misfire / Tekleme (P0300, P0301-P0304)
        misfire_count = sum(1 for d in active_dtcs if str(d.get("code", "")).upper().startswith("P030"))
        if misfire_count > 0:
            scenario_matched_count += misfire_count
            if severity != FaultSeverity.CRITICAL_STOP:
                severity = FaultSeverity.MEDIUM
            affected.append("Silindir Ateşleme & Enjeksiyon")
            likely_causes.append("Ateşleme bobini izolasyon kaçağı, buji elektrot aşınması veya enjektör tıkanıklığı.")
            steps.append(
                TroubleshootingStep(
                    len(steps) + 1,
                    "Osilatör ekranında ateşleme bobini sekonder dalga formunu ve krank devir çentiklerini izleyin.",
                    "Ateşleme Bobinleri & Bujiler",
                    "Orta (Alet Gerekir)",
                )
            )
            correlations.append(f"Motor {rpm:.0f} RPM devirde silindir teklemesi nedeniyle tork dalgalanması yaşıyor.")

        # Scenario 6: Overboost / Underboost (P0234, P0299, SPN 102)
        turbo_count = sum(
            1
            for d in active_dtcs
            if str(d.get("code", "")).upper() in {"P0234", "P0299"} or d.get("spn") == 102
        )
        if turbo_count > 0 or boost_bar > 2.5:
            scenario_matched_count += turbo_count
            if severity != FaultSeverity.CRITICAL_STOP:
                severity = FaultSeverity.MEDIUM
            affected.append("Aşırı Doldurma & Turboşarj")
            likely_causes.append("Wastegate mekanik sıkışması, N75 selenoid arızası veya intercooler hortum kaçağı.")
            steps.append(
                TroubleshootingStep(
                    len(steps) + 1,
                    "Vakum pompası ile wastegate aktüatör kolunun hareketini test edin (0.6 barda tam açılmalıdır).",
                    "Wastegate / VGT Aktüatörü",
                    "Orta (Alet Gerekir)",
                )
            )
            correlations.append(f"Turbo basıncı {boost_bar:.2f} Bar seviyesinde; hedef basınç aralığından sapma var.")

        # Scenario 7: Overheat / Termal Sorunlar (P0115, SPN 110)
        heat_count = sum(
            1
            for d in active_dtcs
            if str(d.get("code", "")).upper() == "P0115" or d.get("spn") == 110
        )
        if coolant_temp > 103.0 or heat_count > 0:
            scenario_matched_count += heat_count
            if coolant_temp > 108.0 or any(d.get("spn") == 110 and d.get("fmi") == 0 for d in active_dtcs):
                severity = FaultSeverity.CRITICAL_STOP
            elif severity != FaultSeverity.CRITICAL_STOP:
                severity = FaultSeverity.MEDIUM
            affected.append("Termal Yönetim & Soğutma")
            likely_causes.append("Termostat kapalı kalması, radyatör fan arızası veya soğutma sıvısı seviye düşüklüğü.")
            steps.append(
                TroubleshootingStep(
                    len(steps) + 1,
                    "Radyatör alt hortumunu kontrol edin; soğuksa termostat açmıyordur.",
                    "Termostat & Radyatör Hortumu",
                    "Kolay (Görsel)",
                )
            )
            correlations.append(f"Motor soğutma sıvısı {coolant_temp:.1f}°C sıcaklıkta; kritik hararet eşiğinde!")

        # Identify DTCs already covered by specific hardcoded scenarios above
        handled_indices: set[int] = set()
        for idx, d in enumerate(active_dtcs):
            c_str = str(d.get("code", "")).upper()
            s_val = d.get("spn")
            desc_u = str(d.get("description", "")).upper()
            if s_val == 100:
                handled_indices.add(idx)
            elif c_str in {"P0AA6", "P0A0B", "P0A80", "P0A93"}:
                handled_indices.add(idx)
            elif isinstance(s_val, int) and 651 <= s_val <= 656:
                handled_indices.add(idx)
            elif s_val in {3251, 3719} or "DPF" in desc_u:
                handled_indices.add(idx)
            elif c_str.startswith("P030"):
                handled_indices.add(idx)
            elif c_str in {"P0234", "P0299"} or s_val == 102:
                handled_indices.add(idx)
            elif c_str == "P0115" or s_val == 110:
                handled_indices.add(idx)

        # Dynamic EXPERT_KNOWLEDGE_BASE lookup for active DTCs not matched by scenarios 1..7
        for idx, d in enumerate(active_dtcs):
            if idx in handled_indices:
                continue
            code_candidate = str(d.get("code", "")).upper()
            spn_candidate = f"SPN{d.get('spn')}" if d.get("spn") else ""
            match_key = code_candidate if code_candidate in EXPERT_KNOWLEDGE_BASE else (spn_candidate if spn_candidate in EXPERT_KNOWLEDGE_BASE else None)
            if match_key:
                kb_matched_count += 1
                info = EXPERT_KNOWLEDGE_BASE[match_key]
                subsys = info.get("subsystem", "Genel Teşhis")
                if subsys not in affected:
                    affected.append(subsys)
                for cause in info.get("causes", []):
                    if cause not in likely_causes:
                        likely_causes.append(cause)
                for s in info.get("steps", []):
                    if len(steps) < 5:
                        act = s[0] if len(s) > 0 else "İnceleme yapın"
                        target_comp = s[1] if len(s) > 1 else "İlgili Komponent"
                        diff = s[2] if len(s) > 2 else "Orta (Alet Gerekir)"
                        steps.append(TroubleshootingStep(len(steps) + 1, act, target_comp, diff))
                sev_str = info.get("severity", "MEDIUM")
                if sev_str == "CRITICAL_STOP":
                    severity = FaultSeverity.CRITICAL_STOP
                elif sev_str == "MEDIUM" and severity != FaultSeverity.CRITICAL_STOP:
                    severity = FaultSeverity.MEDIUM


        # Default fallback if no specific rule matched
        if not likely_causes:

            if dtc_count > 0:
                likely_causes.append("CAN veri yolunda aktif diagnostik hata kodları kaydedildi.")
                steps.append(
                    TroubleshootingStep(
                        1,
                        "Hata kodlarının detaylarını ve freeze frame verilerini UDS 0x19 servisi ile sorgulayın.",
                        "Elektronik Kontrol Üniteleri (ECU)",
                        "Kolay (Görsel)",
                    )
                )
            else:
                likely_causes.append("Aktif hata tespit edilmedi. Telemetri sinyalleri nominal aralıkta çalışıyor.")
                steps.append(
                    TroubleshootingStep(
                        1,
                        "Rutin periyodik bakım ve CAN sinyal osiloskop kontrollerini sürdürün.",
                        "Genel Araç Sistemi",
                        "Kolay (Görsel)",
                    )
                )

        if dtc_count == 0 and not affected:
            summary = (
                f"Çevrimdışı AI Analizi: Nominal durum: Aktif arıza kodu tespit edilmedi. "
                f"Telemetri sinyalleri nominal aralıkta çalışıyor. Sistem Durumu: {severity.value}."
            )
        else:
            summary = (
                f"Çevrimdışı AI Analizi: Toplam {dtc_count} aktif arıza kodu tespit edildi. "
                f"Sistem Durumu: {severity.value}. Ana etki alanı: {', '.join(affected) if affected else 'Genel Sistem'}."
            )

        return DiagnosticAnalysisReport(
            summary=summary,
            severity=severity,
            root_cause_probability=compute_root_cause_confidence(
                dtc_count=dtc_count,
                scenario_matched=scenario_matched_count,
                kb_matched=kb_matched_count,
                telemetry_correlation_count=len(correlations),
            ),
            likely_causes=likely_causes,
            troubleshooting_steps=steps,
            affected_subsystems=affected if affected else ["CAN Veri Yolu & Genel Telemetri"],
            raw_dtc_count=dtc_count,
            telemetry_correlations=correlations,
            ai_model_used="Yerel Otomotiv Uzman Motoru (Çevrimdışı)",
        )

    def analyze_live_telemetry(
        self,
        rpm: float,
        boost_bar: float,
        coolant_temp: float,
        dtc_codes: list[str],
        user_prompt: str,
        bus_metrics: dict[str, Any] | None = None,
    ) -> str:
        """Helper for live interactive prompt query with deep reasoning."""
        # M-18 (P2-17): lazy knowledge-base load at first analysis use.
        ensure_external_dtc_database_loaded()
        # 1. Live Gemini API call if configured
        if self.gemini_api_key and len(self.gemini_api_key.strip()) > 10:
            try:
                bus_info = ""
                if bus_metrics:
                    bus_info = f", Hat Yükü=%{bus_metrics.get('bus_load_percent', 0)}, Hata Karesi={bus_metrics.get('error_count', 0)}"
                prompt_text = (
                    "Sen 'Universal CAN-Bus Diagnostic & Telemetry Tool' profesyonel teşhis yazılımının yerleşik AI asistanısın.\n"
                    "Kullanıcı doğrudan CAN hattına bağlı ve canlı paketleri inceliyor.\n\n"
                    "KESİN KURALLAR:\n"
                    "1. KISA VE BASİTLEŞTİRİLMİŞ YANIT VER: Giriş/çıkış laf kalabalığı ve uzun teorik paragraflar KESİNLİKLE YASAKTIR. En fazla 3-4 kısa maddede doğrudan çözümü ve kontrol noktasını söyle.\n"
                    "2. BİLMEDİĞİN KONUDA DÜRÜST OL: Sorulan DBC dosyası, araç modeli, ECU, CAN ID veya parametre hakkında kesin bilgin/kaydın yoksa 'Bu konu/DBC hakkında veritabanında yeterli bilgi bulunamadı' de. Asla uydurma veri üretme.\n"
                    "3. ASLA 'aracı servise götürün' veya 'başka teşhis cihazı kullanın' deme, kullanıcı zaten profesyonel teşhis donanımına bağlı.\n\n"
                    f"Kullanıcı Sorusu: {user_prompt}\n"
                    f"Canlı Telemetri: Motor={rpm:.0f} RPM, Turbo={boost_bar:.2f} Bar, Sıcaklık={coolant_temp:.1f}°C, Aktif DTC={', '.join(dtc_codes) if dtc_codes else 'Yok'}{bus_info}"
                )
                payload = {
                    "contents": [{"parts": [{"text": prompt_text}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512},
                }
                data = json.dumps(payload).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.gemini_api_key.strip(),
                }
                req = urllib.request.Request(gemini_endpoint(), data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=8.0) as resp:  # nosec: B310
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    parts = resp_json.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    answer = "".join(p.get("text", "") for p in parts if not p.get("thought", False)).strip()
                    if answer:
                        actions = extract_action_triggers(answer, user_prompt)
                        ans_with_act = attach_action_triggers(answer, actions)
                        return f"✨ **Google Gemini 2.0 Flash (Bulut Zekası):**\n\n{ans_with_act}"
            except Exception as e:
                # M-02: sanitized — the raw exception text may include the
                # request URL carrying the API key (CWE-532).
                logger.warning(
                    "Live Gemini prompt failed, falling back to deterministic local expert engine",
                    extra={"error_type": type(e).__name__},
                )

        # 2. Fully Offline Deterministic Causal Bayesian Inference
        telemetry: dict[str, Any] = {
            "EngineSpeed": rpm,
            "BoostPressure": boost_bar,
            "CoolantTemp": coolant_temp,
            **(bus_metrics or {}),
        }
        active_dtc_objs = [{"code": c} for c in dtc_codes]
        return CausalBayesianInferenceEngine.evaluate_diagnostic_query(user_prompt, active_dtc_objs, telemetry)

    def _analyze_with_gemini(
        self,
        active_dtcs: list[dict[str, object]],
        telemetry_snapshot: dict[str, float],
        active_ecus: list[str],
    ) -> DiagnosticAnalysisReport:
        """Call Google Gemini 2.0 Flash REST API with structured JSON output."""
        if not self.gemini_api_key:
            raise ValueError("Gemini API key is required")
        prompt = (
            "Sen 'Universal CAN-Bus Diagnostic & Telemetry Tool' profesyonel araç teşhis yazılımının yerleşik AI Başmühendisisin.\n"
            f"Aktif DTC Listesi: {json.dumps(active_dtcs, ensure_ascii=False)}\n"
            f"Canlı Telemetri: {json.dumps(telemetry_snapshot, ensure_ascii=False)}\n"
            f"Aktif ECU'lar: {', '.join(active_ecus)}\n\n"
            "Aşağıdaki JSON şemasına BİREBİR UYGUN geçerli bir JSON yanıtı döndür:\n"
            "{\n"
            '  "summary": "Analiz özeti",\n'
            '  "severity": "INFO" | "LOW" | "MEDIUM" | "CRITICAL_STOP",\n'
            '  "root_cause_probability": "Kök neden olasılık derecesi",\n'
            '  "likely_causes": ["Neden 1", "Neden 2"],\n'
            '  "troubleshooting_steps": [\n'
            '    {"step_number": 1, "action": "Eylem", "target_component": "Komponent", "difficulty": "Kolay (Görsel)" | "Orta (Alet Gerekir)" | "İleri (Servis)"}\n'
            "  ],\n"
            '  "affected_subsystems": ["Alt sistem 1"],\n'
            '  "telemetry_correlations": ["Telemetri korelasyonu 1"]\n'
            "}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.gemini_api_key.strip(),
        }
        req = urllib.request.Request(gemini_endpoint(), data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=10.0) as resp:  # nosec: B310
            resp_data = json.loads(resp.read().decode("utf-8"))
            candidate = resp_data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = self._clean_and_parse_json(candidate)
            steps = [
                TroubleshootingStep(
                    s.get("step_number", idx + 1),
                    s.get("action", ""),
                    s.get("target_component", ""),
                    s.get("difficulty", "Orta (Alet Gerekir)"),
                )
                for idx, s in enumerate(parsed.get("troubleshooting_steps", []))
            ]
            return DiagnosticAnalysisReport(
                summary=parsed.get("summary", "Gemini Analizi Tamamlandı."),
                # L-9 (P3-4): map unknown severity strings to MEDIUM instead
                # of raising ValueError out of the cloud analysis.
                severity=map_severity_or_default(parsed.get("severity", "MEDIUM")),
                root_cause_probability=parsed.get("root_cause_probability", "Yüksek"),
                likely_causes=parsed.get("likely_causes", []),
                troubleshooting_steps=steps,
                affected_subsystems=parsed.get("affected_subsystems", []),
                raw_dtc_count=len(active_dtcs),
                telemetry_correlations=parsed.get("telemetry_correlations", []),
                ai_model_used="Google Gemini 2.0 Flash (Bulut Zekası)",
            )

    def _analyze_with_openai(
        self,
        active_dtcs: list[dict[str, object]],
        telemetry_snapshot: dict[str, float],
        active_ecus: list[str],
    ) -> DiagnosticAnalysisReport:
        """Call OpenAI Chat Completions API with structured JSON output."""
        if not self.openai_api_key:
            raise ValueError("OpenAI API key is required")
        url = "https://api.openai.com/v1/chat/completions"
        system_prompt = (
            "Sen 'Universal CAN-Bus Diagnostic & Telemetry Tool' profesyonel araç teşhis yazılımının yerleşik AI Başmühendisisin.\n"
            "Görevin araçtaki CAN-Bus telemetrisini ve DTC hata kodlarını analiz edip doğrudan sahada uygulanabilir 4 aşamalı onarım kılavuzu üretmektir."
        )
        user_prompt = (
            f"Aktif DTC Listesi: {json.dumps(active_dtcs, ensure_ascii=False)}\n"
            f"Canlı Telemetri: {json.dumps(telemetry_snapshot, ensure_ascii=False)}\n"
            f"Aktif ECU'lar: {', '.join(active_ecus)}\n\n"
            "JSON Formatında Yanıt Ver:\n"
            "{\n"
            '  "summary": "Özet",\n'
            '  "severity": "INFO" | "LOW" | "MEDIUM" | "CRITICAL_STOP",\n'
            '  "root_cause_probability": "Olasılık",\n'
            '  "likely_causes": ["Neden 1"],\n'
            '  "troubleshooting_steps": [{"step_number": 1, "action": "Adım", "target_component": "Komponent", "difficulty": "Orta (Alet Gerekir)"}],\n'
            '  "affected_subsystems": ["Sistem 1"],\n'
            '  "telemetry_correlations": ["Korelasyon 1"]\n'
            "}"
        )
        models_to_try = ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
        last_error: Exception | None = None
        for model in models_to_try:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                }
                data = json.dumps(payload).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.openai_api_key.strip()}",
                }
                req = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=12.0) as resp:  # nosec: B310
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    content = resp_data["choices"][0]["message"]["content"]
                    parsed = self._clean_and_parse_json(content)
                    steps = [
                        TroubleshootingStep(
                            s.get("step_number", idx + 1),
                            s.get("action", ""),
                            s.get("target_component", ""),
                            s.get("difficulty", "Orta (Alet Gerekir)"),
                        )
                        for idx, s in enumerate(parsed.get("troubleshooting_steps", []))
                    ]
                    return DiagnosticAnalysisReport(
                        summary=parsed.get("summary", "OpenAI Analizi Tamamlandı."),
                        # L-9 (P3-4): map unknown severity strings to MEDIUM
                        # instead of raising ValueError out of the whole
                        # cloud analysis.
                        severity=map_severity_or_default(parsed.get("severity", "MEDIUM")),
                        root_cause_probability=parsed.get("root_cause_probability", "Yüksek"),
                        likely_causes=parsed.get("likely_causes", []),
                        troubleshooting_steps=steps,
                        affected_subsystems=parsed.get("affected_subsystems", []),
                        raw_dtc_count=len(active_dtcs),
                        telemetry_correlations=parsed.get("telemetry_correlations", []),
                        ai_model_used=f"OpenAI {model} (ChatGPT Bulut Zekası)",
                    )
            except urllib.error.HTTPError as exc:
                # L-9 (P3-4): classify HTTP failures — auth/quota errors will
                # NOT get better by trying the next model; retrying burned
                # 36 s and 3 requests. Fail over to the local expert now.
                last_error = exc
                if exc.code in (401, 403):
                    logger.warning("OpenAI request rejected (auth) — switching to local expert", extra={"status": exc.code})
                    break
                if exc.code == 429:
                    logger.warning("OpenAI rate limited — switching to local expert", extra={"status": exc.code})
                    break
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue

        # L-9 (P3-4): log the exception TYPE + safe message, never the raw
        # repr — HTTPError/URLError reprs can embed request URLs and
        # Authorization fragments, violating the file's own sanitize policy.
        logger.warning(
            "All OpenAI models failed — falling back to local expert",
            extra={"error_type": type(last_error).__name__ if last_error else None},
        )
        return self._analyze_local_expert(active_dtcs, telemetry_snapshot, active_ecus)

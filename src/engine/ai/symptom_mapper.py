"""Symptom-to-Subsystem & DTC Mapping Engine (FAZ 2).

Enables triage when no DTC is present (e.g., user reports "marş basmıyor, siyah duman").
Maps plain Turkish symptoms to candidate automotive/marine/heavy-duty subsystems,
seed DTCs, and discriminating initial questions.

Fully offline, deterministic, zero external framework dependencies.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class SymptomMatchResult:
    """Outcome of mapping user-reported symptoms to vehicle subsystems."""

    query: str
    matched_symptoms: tuple[str, ...]
    suspected_subsystems: tuple[str, ...]
    candidate_dtcs: tuple[str, ...]
    initial_questions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "matched_symptoms": list(self.matched_symptoms),
            "suspected_subsystems": list(self.suspected_subsystems),
            "candidate_dtcs": list(self.candidate_dtcs),
            "initial_questions": list(self.initial_questions),
        }


def _normalize_text(text: str) -> str:
    """Normalize Turkish characters and lower-case text for robust keyword matching."""
    t = text.lower().strip()
    t = t.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t)


# Curated symptom mapping dictionary (grounded in standard automotive & marine failure modes)
SYMPTOM_KNOWLEDGE_BASE: list[dict[str, Any]] = [
    {
        "id": "crank-no-start",
        "keywords": ["mars basmiyor", "calismiyor", "mars almiyor", "mars basiyor calismiyor", "atesleme yok", "crank"],
        "subsystems": ["Ateşleme & Marş Sistemi", "Yakıt Besleme Sistemi", "Akü & Elektrik Besleme"],
        "candidate_dtcs": ["P0335", "P0627", "P0562", "SPN 100", "SPN 636"],
        "initial_questions": [
            "Marş motoru anahtarı çevirdiğinizde canlı dönüyor mu?",
            "Gösterge panelinde akü veya anahtar (immobilizer) ışığı yanıp sönüyor mu?",
            "Depoda yeterli yakıt var mı ve yakıt pompası vızıltısı duyuluyor mu?",
        ],
    },
    {
        "id": "black-smoke",
        "keywords": ["siyah duman", "kara duman", "egzozdan duman", "fazla yakit"],
        "subsystems": ["Hava Emiş & Turbo Sistemi", "Yakıt Enjeksiyon Sistemi", "EGR & Emisyon"],
        "candidate_dtcs": ["P0101", "P0299", "P0401", "SPN 102", "SPN 27"],
        "initial_questions": [
            "Hızlanma esnasında motor çekişinde bariz bir düşüş veya ıslık sesi var mı?",
            "Hava filtresi en son ne zaman değiştirildi?",
            "Canlı MAF sensörü ve turbo şarj basıncı nominal değerde mi?",
        ],
    },
    {
        "id": "engine-overheating",
        "keywords": ["hararet", "motor hararet", "su kaynatiyor", "antifriz", "sicaklik yuksek"],
        "subsystems": ["Soğutma Sistemi", "Termostat & Radyatör", "Silindir Kapak Contası"],
        "candidate_dtcs": ["P0217", "P0117", "P0128", "SPN 110"],
        "initial_questions": [
            "Soğutma suyu genleşme kabında su seviyesi Min altında mı?",
            "Radyatör soğutma fanı motor ısındığında devreye giriyor mu?",
            "Kalorifer sıcak hava üflüyor mu yoksa soğuk mu kalıyor?",
        ],
    },
    {
        "id": "rough-idle-vibration",
        "keywords": ["titreme", "rolanti", "rolantide dalgalanma", "tekleme", "silkeleme"],
        "subsystems": ["Ateşleme Sistemi (Buji/Bobin)", "Yakıt Enjektörleri", "Vaküm Kaçağı"],
        "candidate_dtcs": ["P0300", "P0301", "P0171", "SPN 651"],
        "initial_questions": [
            "Titreme sadece rölantide mi yoksa gaza basıldığında da devam ediyor mu?",
            "Motor arıza lambası (Check Engine) yanıp sönüyor mu?",
            "Vaküm hortumlarında emiş kaçağı veya tıslama sesi duyuluyor mu?",
        ],
    },
    {
        "id": "low-oil-pressure",
        "keywords": ["yag basinci", "yag lambasi", "kirmizi yag", "yag eksik"],
        "subsystems": ["Motor Yağlama Sistemi", "Yağ Basınç Sensörü", "Yağ Pompası"],
        "candidate_dtcs": ["P0524", "P0521", "SPN 100"],
        "initial_questions": [
            "Yağ seviye çubuğunu kontrol ettiniz mi, yağ seviyesi nerede?",
            "Motordan sibop/şakırtı sesi geliyor mu?",
            "Kırmızı yağ basınç lambası sürekli mi yanıyor yoksa rölantide mi yanıp sönüyor?",
        ],
    },
    {
        "id": "marine-lanyard-kill",
        "keywords": ["marin", "kordon", "kill switch", "tekne mars basmiyor", "volvo penta calismiyor"],
        "subsystems": ["Marin Emniyet & Kilitleme Devresi", "Helm Kontrol Paneli"],
        "candidate_dtcs": ["SPN 636", "SPN 2000"],
        "initial_questions": [
            "Acil durdurma kordonu (safety lanyard) yerine tam oturmuş durumda mı?",
            "Vites kolu tam 'Boş' (Neutral) konumunda mı?",
        ],
    },
    {
        "id": "dpf-regeneration-failed",
        "keywords": ["dpf", "partikul", "rejenerasyon", "egzoz tikanik", "filtre tikanik", "dpf lambasi"],
        "subsystems": ["Egzoz & DPF Sistemi", "Egzoz Gazı Sıcaklık Sensörleri"],
        "candidate_dtcs": ["P2002", "P2453", "SPN 3251", "SPN 3719"],
        "initial_questions": [
            "DPF diferansiyel basınç sensörü rölantide kaç mbar okuyor?",
            "Son başarılı rejenerasyondan bu yana kaç km yol yapıldı?",
            "Aracın egzoz gazı sıcaklık sensörleri (EGT) 600°C üzerine çıkabiliyor mu?",
        ],
    },
    {
        "id": "def-scr-adblue-warning",
        "keywords": ["adblue", "def", "scr", "ure", "nox", "emisyon ikazi", "motor calismayacak"],
        "subsystems": ["SCR & AdBlue Dozaj Sistemi", "NOx Sensörleri"],
        "candidate_dtcs": ["P20EE", "SPN 1761", "SPN 3364", "SPN 4364"],
        "initial_questions": [
            "AdBlue deposundaki sıvı seviyesi ve kırılma indisi (refraktometre %32.5) teyit edildi mi?",
            "AdBlue enjektör ucu kristalleşmiş üre kalıntısıyla tıkanmış mı?",
            "Katalizör giriş ve çıkış NOx sensörleri ısıtıcı devreleri sağlam mı?",
        ],
    },
    {
        "id": "transmission-slip-limp",
        "keywords": ["sanziman", "vites gecmiyor", "vites kaydiriyor", "limp mode", "koruma modu", "vuruntu", "sanziman vuruyor"],
        "subsystems": ["Otomatik Şanzıman (TCM)", "Şanzıman Hidrolik Basınç Solenoidleri"],
        "candidate_dtcs": ["P0700", "P0730", "P0740", "P0750"],
        "initial_questions": [
            "Şanzıman yağ seviyesi ve yağın rengi/kokusu (yanık kokusu var mı) kontrol edildi mi?",
            "Vites geçişindeki vuruntu veya kaydırma sadece soğukken mi yoksa ısınınca da var mı?",
            "Tork konvertör kilitleme (TCC) selenoid direnci nominal aralıkta mı?",
        ],
    },
    {
        "id": "abs-esp-traction-fault",
        "keywords": ["abs", "esp", "patinaj", "fren sistemi", "direksiyon aci", "kayma ikazi", "fren lambasi"],
        "subsystems": ["Fren & ABS/ESP Kontrol Ünitesi", "Tekerlek Hız Sensörleri"],
        "candidate_dtcs": ["U0121", "U0415", "C1A00"],
        "initial_questions": [
            "Gösterge panelinde ABS ve ESP ikaz lambaları birlikte mi yanıyor?",
            "Canlı veride 4 tekerleğin hız sensör sinyalleri düz yolda birbirine eşit mi?",
            "Direksiyon açı sensörü orta konumda sıfır derece kalibrasyonunu koruyor mu?",
        ],
    },
    {
        "id": "turbo-underboost-power-loss",
        "keywords": ["cekis dusuk", "turbo basmiyor", "araba gitmiyor", "guc kaybi", "bayilma", "gaz yemiyor", "cekis"],
        "subsystems": ["Turboşarj & Wastegate Sistemi", "Hava Emiş Manifoldu"],
        "candidate_dtcs": ["P0299", "P0101", "SPN 102"],
        "initial_questions": [
            "Tam gaz hızlanmada intercooler basınç hortumlarında şişme/sertleşme var mı?",
            "Emiş veya intercooler borularında tiz bir hava kaçırma ıslığı duyuluyor mu?",
            "Turbo şarj basınç sensörü (MAP) rölantide atmosferik basınca eşit mi?",
        ],
    },
    {
        "id": "turbo-overboost",
        "keywords": ["asiri basinc", "overboost", "turbo fazla basiyor", "ani kesilme", "basinc fazla"],
        "subsystems": ["VGT Değişken Geometri Mekanizması", "Turbo Şarj Basınç Kontrol Valfi (N75)"],
        "candidate_dtcs": ["P0234", "SPN 102"],
        "initial_questions": [
            "VGT kanatçık vakum aktüatör kolu elle hareket ettirildiğinde takılma yapıyor mu?",
            "N75 elektrovalfi vakum tahliyesi yapabiliyor mu?",
        ],
    },
    {
        "id": "battery-drain-parasitic",
        "keywords": ["aku bitiyor", "aku bosaliyor", "sabah calismiyor", "kacak akim", "aku lambasi", "sarj etmiyor"],
        "subsystems": ["Akü & Şarj Sistemi", "Araç Gövde Kontrol Modülü (BCM)"],
        "candidate_dtcs": ["P0562", "P0620", "SPN 168"],
        "initial_questions": [
            "Kontak kapatılıp araç uyku moduna geçtikten sonra parazit kaçak akım kaç mA?",
            "Alternatör rölantide en az 13.8V, 2000 devirde 14.2V üretiyor mu?",
            "Akü kutup başı klemenslerinde gevşeklik veya beyaz sülfatlaşma var mı?",
        ],
    },
    {
        "id": "can-bus-communication-loss",
        "keywords": ["haberlesme", "iletisim yok", "can bus", "u kodlari", "gosterge calismiyor", "ag hatasi", "iletisim hatasi"],
        "subsystems": ["CAN Veri Yolu Ağı (CAN-H / CAN-L)", "Gateway & ECU Haberleşmesi"],
        "candidate_dtcs": ["U0100", "U0101", "U0126", "U0155"],
        "initial_questions": [
            "OBD portunun Pin 6 ve Pin 14 uçları arasından ölçülen hat direnci 60 Ohm mu?",
            "CAN-H ve CAN-L hatlarının şasiye göre durağan voltajları 2.5V civarında mı?",
            "Ağda iletişim kurulamayan modülün besleme sigortası ve şasi pini sağlam mı?",
        ],
    },
    {
        "id": "fuel-rail-pressure-drop",
        "keywords": ["yakit basinci", "rampa basinci", "enjektor", "common rail", "gaz verince stop", "mazot basinci"],
        "subsystems": ["Yüksek Basınç Yakıt Pompası (HPFP)", "Common Rail Basınç Regülatörü"],
        "candidate_dtcs": ["P0087", "P0088", "SPN 157"],
        "initial_questions": [
            "Tam gaz yük altında ölçülen ray basıncı hedef basıncı birebir takip edebiliyor mu?",
            "Mazot filtresi girişinde veya deposunda talaş/metal partikülü tespit edildi mi?",
            "Enjektör geri dönüş kaçak testinde silindirler arası dengesizlik var mı?",
        ],
    },
    {
        "id": "glow-plug-cold-start",
        "keywords": ["kizdirma bujisi", "sogukta calismiyor", "sabah gec calisiyor", "beyaz duman", "kizdirma lambasi"],
        "subsystems": ["Kızdırma Bujisi & Isıtma Rölesi", "Ön Isıtma Devresi"],
        "candidate_dtcs": ["P0380", "P0670"],
        "initial_questions": [
            "Her bir kızdırma bujisinin şasiye göre omajı 0.8 - 1.5 Ohm aralığında mı?",
            "Kontak ilk açıldığında kızdırma buji klemenslerine akü gerilimi (12V) geliyor mu?",
        ],
    },
    {
        "id": "ebs-air-brake-leak",
        "keywords": ["hava kacak", "fren havasi", "kompresor dolmuyor", "hava tupu", "imdat", "fren hava basinci"],
        "subsystems": ["Pnömatik Fren Devresi (EBS)", "Hava Kompresörü & Drier (Kurutucu)"],
        "candidate_dtcs": ["SPN 1087", "SPN 1088"],
        "initial_questions": [
            "Devre 1 ve Devre 2 hava tüpleri kaç bar basınca ulaşıyor (nominal 8.5-10 bar)?",
            "Hava kurutucu (APU/drier) tahliyesinden sürekli hava kaçak sesi geliyor mu?",
        ],
    },
    {
        "id": "marine-raw-water-cooling",
        "keywords": ["deniz suyu", "impeller", "kondense", "egzozdan su gelmiyor", "marin hararet", "deniz suyu sogutma"],
        "subsystems": ["Deniz Suyu Soğutma Devresi", "Raw Water Pump & Isı Eşanjörü"],
        "candidate_dtcs": ["SPN 110"],
        "initial_questions": [
            "Borda emiş vanası açık ve deniz suyu sepet filtresi (strainer) temiz mi?",
            "Deniz suyu pompası kauçuk impeller kanatçıkları sağlam mı (kopuk kanat var mı)?",
        ],
    },
    {
        "id": "ev-hv-isolation-warning",
        "keywords": ["izolasyon", "elektrikli arac", "yuksek voltaj", "hvil", "turuncu kablo", "sarj olmuyor", "hvil hatasi"],
        "subsystems": ["Yüksek Voltaj (HV) Batarya & İnverter", "HVIL Güvenlik Kilidi"],
        "candidate_dtcs": ["P0A0B", "P0AA6", "P0A80"],
        "initial_questions": [
            "HV servis emniyet kilidi (Manual Service Disconnect) yerine tam oturmuş mu?",
            "İzolasyon izleme modülü şasi ile pozitif/negatif HV baraları arasında kaç kOhm direnç okuyor?",
        ],
    },
]


def map_symptoms_to_systems(query: str) -> SymptomMatchResult:
    """Analyze natural-language symptom text and match against known failure profiles."""
    cleaned = (query or "").strip()
    if not cleaned:
        return SymptomMatchResult(
            query=cleaned,
            matched_symptoms=(),
            suspected_subsystems=(),
            candidate_dtcs=(),
            initial_questions=(),
        )

    norm_query = _normalize_text(cleaned)
    matched_names: list[str] = []
    subsystems: list[str] = []
    dtcs: list[str] = []
    questions: list[str] = []

    for entry in SYMPTOM_KNOWLEDGE_BASE:
        hit = False
        for kw in entry["keywords"]:
            norm_kw = _normalize_text(kw)
            if norm_kw in norm_query:
                hit = True
                break
        if hit:
            matched_names.append(entry["id"])
            for sub in entry["subsystems"]:
                if sub not in subsystems:
                    subsystems.append(sub)
            for dtc in entry["candidate_dtcs"]:
                if dtc not in dtcs:
                    dtcs.append(dtc)
            for q in entry["initial_questions"]:
                if q not in questions:
                    questions.append(q)

    return SymptomMatchResult(
        query=cleaned,
        matched_symptoms=tuple(matched_names),
        suspected_subsystems=tuple(subsystems),
        candidate_dtcs=tuple(dtcs),
        initial_questions=tuple(questions),
    )


__all__ = [
    "SymptomMatchResult",
    "map_symptoms_to_systems",
    "SYMPTOM_KNOWLEDGE_BASE",
]

# Çevrimdışı Teşhis Copilot'u — Mimari, Veri Kaynakları, Genişletme

> Kapsam: `src/engine/ai/` altındaki **yapılandırılmış cevap yolu** (copilot
> upgrade, 2026-10). Eski motorun (kural senaryoları, `explain_can_packet`,
> kullanıcı kartı, diyalog) ayrıntısı için `docs/OFFLINE_AI_ENGINE.md`.

## 1. Değişmez kurallar

| Kural | Nasıl korunuyor |
|---|---|
| Tamamen çevrimdışı: bulut LLM yok, API anahtarı yok, ağ çağrısı yok | `tests/safety/test_ai_tx_isolation.py` (AST import taraması `src/engine/ai/**` altındaki **tüm** dosyaları kapsar, yeni modüller dahil) |
| Copilot ölçüm/değer **uydurmaz**; veri yoksa "veri yok" der | `tests/unit/test_copilot_no_fabrication.py` (her sayı girdiye veya atıf yapılan kayda izlenir, her atıf gerçek kayda çözülür) |
| Copilot CAN hattına yazamaz, TX yolu açamaz | Yeni modüllerde yazma/TX çağrısı yok (AST testi); köprü uç noktası `read` sınıfında; UI kartı yalnız okur |
| Deterministik | Aynı girdi → aynı `to_dict()` (test); saat/rastgelelik kullanılmaz |

## 2. Akış

```
serbest metin ─┐
DTC / SPN-FMI ─┤                       ┌──────────────────────────┐
canlı telemetri┼─► query_understanding ─► copilot_reasoner ─► copilot_answer ─► StructuredAnswer
DM1 (PGN 65226)┘   (TR/EN, yazım hatası,   (kod gerçekleri, eşik       (6 bölüm + güvenlik bandı,
                    DM1 çözümü, birimler)   değerlendirmesi, kök neden    TR/EN, kaynak id'leri)
                              │             sıralaması, aciliyet,
                              ▼             güvenlik, eksik veri)
                       KnowledgeBase  ◄───────────┘
                (tembel, önbellekli, indeksli; tek veri erişim katmanı)
```

Giriş noktaları:

* Python: `answer_query(text, dtcs=…, telemetry=…, dm1=…, vehicle_make=…, language=…)`
  veya `AiDiagnosticCopilot().answer(...)` → `StructuredAnswer` (`to_dict()`, `to_markdown()`).
* Masaüstü köprüsü: `ask_copilot_structured(query, language)` (salt okuma) ve
  `get_diagnostic_analysis()` çıktısındaki ek `structured_answer` anahtarı (oturumdaki
  AKTİF kodlar + ölçülmüş telemetri).
* UI: Uzman masası → Teşhis asistanı → "Copilot'a sorun" kartı
  (`src/ui/frontend/src/components/workbench/CopilotAnswer.tsx`).

## 3. Modüller

| Dosya | Sorumluluk |
|---|---|
| `knowledge_base.py` | `KnowledgeBase`: 23 kaynağın tek erişim noktası. Kurulum hiçbir şey okumaz; her kaynak ilk kullanımda yüklenir, kilitle korunur, önbellekte kalır. Kırık kaynak copilot'u düşürmez (`source_unavailable`). Her sonuç `Lookup(found, source, key, record, reason)`; `ref = "kaynak#anahtar"`. `resolve_ref()` basılan atıfı yeniden doğrular. |
| `query_understanding.py` | `parse_query`: dil tespiti; DTC (`PO101`→`P0101`), SPN/FMI yazımları, PGN/PID; DM1 baytlarını SAE J1939-73 düzenine göre çözer (lambalar, SPN/FMI/OC, CM=1 uyarısı); semptom eşleştirme (Türkçe katlama, ek/çekim toleransı, Damerau ≤1/≤2 yazım hatası); metinden ölçüm okuma (°F→°C, psi/kPa→bar, MΩ→kΩ). Birimsiz değer `unit_assumed=True`; uyumsuz birim/NaN/Inf **reddedilir**. Sinyal adı geçmeyen gösterge değeri ("hararet yapıyor, göstergede 112 derece"): semptom tek bir ölçülebilir sinyale bağlıysa (`_SYMPTOM_SIGNAL`) ve metinde **açık birimli**, o sinyale çevrilebilen **tek** sayı varsa o sinyalin okuması olur (`origin="text_context"`, not: `context_reading:<sinyal><-<semptom>`); birimsiz, uyumsuz veya birden fazla aday varsa bağlanmaz. Yazım hatası toleransı Türkçe olumsuzluk ekini (`-mıyor/-maz/-madı`) asla aşmaz: "dönüyor" ≠ "dönmüyor". |
| `copilot_reasoner.py` | Kod gerçekleri (DTC + OEM katmanı + J1939 + FMI), telemetri bulguları (`telemetry_thresholds`, UN R100 izolasyon Ω/V), kök neden sıralaması, aciliyet, güvenlik kategorileri, eksik ölçümler, NHTSA. |
| `copilot_answer.py` | `StructuredAnswer` + TR/EN metinler + Markdown; sabit şablonlar `template:*` olarak işaretlenir. |
| `local_search.py` | **Opsiyonel** BM25 ipucu katmanı (yalnız hiçbir kod/semptom eşleşmediğinde, "teşhis değil" etiketiyle). Kapatma: `CopilotOptions(local_search=False)` veya `UCANLAB_COPILOT_LOCAL_SEARCH=0`. Çekirdek onsuz çalışır (test). |

### 3.1 Puanlama (deterministik, belgeli)

| Kanıt | Puan |
|---|---|
| Düğümün beklediği kod aktif | +3.0 (her kod) |
| Düğümün beyan ettiği FMI aktif FMI ile aynı | +0.5 |
| FMI ailesi (J1939-73): 0/1/15–18 "veri geçerli" → fiziksel neden +1, sensör nedeni −1; 3–6 elektriksel → tersi | ±1.0 |
| Kanıt sinyali eşik dışı ölçüldü / normal ölçüldü | +1.5 / −1.0 |
| Çelişen sinyal makul ölçüldü / sensör sınırında (ör. ECT −40 °C) | −1.5 / +1.0 |
| Yalnız şikâyetten ulaşılan aday kod / şikâyet aktif kodu doğruluyor | +1.0 / +0.5 |
| Grafta düğüm yok → kayıttaki temiz nedenler (dolgu metinler elenir) | +2.0 − 0.05·i |

Neden metni olamayan hasat artıkları hem kayıt nedenlerinden hem graf düğüm
başlıklarından elenir (`_is_harvest_residue`): numaralı parça listesi
("1. 20-Way TCM … 2. …"), FMI tablo dökümü ("… FMI 1, 4, 17, 18: …"), prosedür
metni ("Key off", "Note:", "refer to") ve cümle ortasında kesilmiş parça
("… power supply to"). Veri değişmez; yalnız cevapta neden olarak gösterilmez.

Puanlar listelenen adaylar arasında softmax ile **göreli** yüzdeye çevrilir;
cevap bunun kesin olasılık olmadığını açıkça yazar. Güven: kod + telemetri +
çelişki yok → yüksek; kod (graf) veya telemetri → orta; diğerleri → düşük.
Eşitlikte en ağır kodun nedeni önce gelir.

### 3.2 Aciliyet ve güvenlik

* Aciliyet: kod ciddiyeti `drive_safety_policy.decide_risk` ile (tek otorite),
  kritik telemetri → KIRMIZI, eşik üstü → en az SARI, DM1 kırmızı STOP lambası →
  KIRMIZI, EV kodu (P0A*) → KIRMIZI. Veri yoksa **GRİ** (asla yeşil değil).
* Kod/ölçüm olmadan da KIRMIZI olan şikâyetler (politika 2026-10):
  * `STOP_SYMPTOMS` — hararet (`engine-overheating`), düşük yağ basıncı
    (`low-oil-pressure`): motor çalışmaya devam ederse kalıcı hasar. Aynı sinyal
    **normal ölçülmüşse** yükseltilmez.
  * Fren/direksiyon şikâyeti ("fren pedalı boşa gidiyor", "direksiyon
    ağırlaştı"): güvenlik bandı "yola çıkmayın" derken aciliyetin "kullanabilirsiniz"
    demesi çelişkiydi. Yalnız uyarı lambası/sensör şikâyetleri (`abs`,
    `abs-esp-traction-fault`, `steering-angle-sensor-uncalibrated`,
    `brake-light-switch-rationality`) SARI kalır.
* Güvenlik bandı (cevabın **ilk satırı**): yangın, yüksek voltaj, fren,
  direksiyon. Tetikleyiciler: kod sistemi (`system_taxonomy`), semptom alanı
  (EV_HV), `symptom_lexicon.safety_terms`, HV ölçümleri.

## 4. Veri kaynakları (KnowledgeBase kaydı)

| Kaynak id | Dosya | Copilot'ta kullanımı |
|---|---|---|
| `dtc_database` | `diagnostics/dtc_database.json` (14.484) | Başlık, alt sistem, temiz nedenler, adımlar, referans değerler |
| `dtc_oem_layer` | `diagnostics/dtc_database_oem_layer.json` | OEM marka listesi, kayıt dışı kodların genel tanımı |
| `j1939_spn_fmi` | `diagnostics/j1939_spn_fmi_database.json` (4.291 SPN, 32 FMI) | SPN adı, FMI anlamı + ciddiyeti, PGN, nedenler, adımlar |
| `extended_pid` | `diagnostics/extended_pid_database.json` (244) | PID açıklaması, ölçüm rehberi |
| `obd_mode06`, `uds_did` | Mode 06 / UDS DID | KB üzerinden erişilebilir (eski paket açıklama yolu) |
| `canonical_symptoms` | 152 semptom | Şikâyet → aday kod, ilk kontroller |
| `symptom_lexicon` (yeni) | 25 kayıt, 186 TR/EN ifade + güvenlik terimleri | Gündelik ifadeler |
| `root_cause_graph` | 8.884 düğüm | Kök neden adayları, kanıt/çelişen sinyaller |
| `signal_aliases` + `signal_measurement_map` (yeni, 24 sinyal) | | Sinyal adı birleştirme; eksik ölçüm için SPN/PGN/PID rehberi |
| `system_taxonomy` | 26 sistem | Fren/direksiyon güvenlik tespiti |
| `telemetry_thresholds` | 7 sinyal | Nominal/uyarı/kritik değerlendirme |
| `hv_safety_thresholds` | UN R100 vb. | İzolasyon direnci Ω/V kontrolü (HV-ISO-001) |
| `dtc_severity_rules` | SAE J2012 kural tablosu | Kod ciddiyeti |
| `nhtsa_recalls`, `nhtsa_complaints` | 282 kampanya, 4.459 şikâyet | Ayrı ve açıkça etiketli "NHTSA" bölümü (VIN doğrulaması yok) |
| `canboat_pgn` | 628 N2K PGN + DM1 | PGN açıklaması, DM1 alan düzeni |
| `copilot_glossary` (yeni, 32 terim) | | Jargon için tek cümlelik TR/EN açıklama |
| `user_kb`, `dtc_procedures`, `dbc_catalog`, `golden_cases` | | KB üzerinden erişilebilir; kullanıcı kartı / kalibrasyon yolları |

Atıf biçimi: `dtc_database#P0101.causes[0]`, `j1939_spn_fmi#SPN_110.FMI_0`,
`root_cause_graph#thermostat-stuck`, `telemetry_thresholds#EngineCoolantTemp`,
`template:safety.brakes` (sabit şablon).

## 5. Genişletme

* **Yeni şikâyet ifadesi:** `scripts/copilot_data/build_lexicon.py` içindeki
  listeye mevcut bir `symptom_id` için ifade ekleyin → betiği çalıştırın.
  Yeni semptom gerekiyorsa önce `canonical_symptoms.json` (kaynaklı) güncellenir.
* **Yeni ölçülebilir sinyal:** `scripts/copilot_data/build_signal_map.py`'ye
  satır ekleyin; SPN/PID alanları veritabanından **kopyalanır** (elle yazılmaz).
  Eşik gerekiyorsa `telemetry_thresholds.json`'a yalnız kaynaklı değer eklenir.
* **Yeni terim:** `scripts/copilot_data/build_glossary.py` (yalnız tanım; değer/limit yok).
* **Yeni veri kaynağı:** `knowledge_base.SOURCE_FILES`'a ekleyin, tembel
  yükleyici + `Lookup` dönen metot + `resolve_ref` dalı yazın,
  `scripts/validate_copilot_data.py`'ye çapraz kontrol ekleyin.
* **Her yeni kayıtta** `provenance` bloğu (`provenance_schema.json`) zorunludur;
  kaynağı/lisansı belirsiz veri eklenmez (`data/PROVENANCE.md`).

Kontroller (CI'da koşar):

```bash
python scripts/validate_copilot_data.py          # FAIL varsa çıkış 1
python scripts/rebuild_csv_exports.py --verify   # 7 CSV ikizi JSON ile senkron mu
python scripts/rebuild_csv_exports.py            # ikizleri tek kaynaktan yeniden üret
python -m pytest tests/unit/test_copilot_*.py tests/safety/test_ai_tx_isolation.py
```

## 6. Testler

| Dosya | İçerik |
|---|---|
| `test_copilot_golden_scenarios.py` | 49 altın senaryo (DTC, SPN/FMI, DM1, semptom, gösterge değeri, olumsuzluk, telemetri+kod, çelişkili kanıt, veri yok, EV/HV, fren/direksiyon, çoklu kod, yazım hatası, TR/EN, NHTSA, PGN) + 6 bölüm/ilk satır güvenlik kontrolü |
| `test_copilot_no_fabrication.py` | Atıf çözümü, sayı izlenebilirliği, yalnız verilen sinyallerde bulgu, bilinmeyen koda anlam verilmemesi, NaN/birim reddi, determinizm, yazma/TX yokluğu |
| `test_copilot_knowledge_and_parsing.py` | KB tembelliği, indeksler, kaçırma nedenleri, ayrıştırıcı birim testleri |
| `test_copilot_performance.py` | Kurulum < 10 ms, sıcak sorgu ort. < 150 ms (ölçülen 2–13 ms), bellek < 8 MB, arama katmanı aç/kapa |
| `test_copilot_bridge_and_data.py` | Köprü uç noktası, ek analiz anahtarı, veri kapısı, üreticilerin bayt-eşdeğerliği |
| `tests/ui_e2e/test_workbench_ui.py::test_assistant_copilot_card_answers_free_text_read_only` | Gerçek tarayıcıda kart, güvenlik bandı, salt okuma |

## 7. Terim sözlüğü

`data/diagnostics/copilot_glossary.json` — cevapta geçen jargon (DTC, SPN, FMI,
PGN, DM1, OBD, ECU, DPF, EGR, SCR/AdBlue, HV, BMS, HVIL, MAF, CKP, …) için tek
cümlelik Türkçe/İngilizce açıklamalar. Cevabın teknik bölümünde, yalnız metinde
gerçekten geçen terimler listelenir. Kayıtlar tanım içerir; değer, limit,
neden veya prosedür içermez.

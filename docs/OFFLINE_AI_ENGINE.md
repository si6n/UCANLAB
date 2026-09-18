# Çevrimdışı AI Teşhis Motoru — Mimari ve Geliştirme Rehberi

> Bu doküman, Universal CAN-Bus Diagnostic & Telemetry Tool içindeki **tamamen
> çevrimdışı (offline) deterministik AI teşhis motorunu** anlatır: bileşenleri,
> veri akışları, bilgi tabanları, doğrulama kilitleri ve geliştirme için açık
> uçlar. Motoru geliştirmek isteyen bir mühendise (insan veya AI) doğrudan
> rehber olması için yazılmıştır.

---

## 1. Genel Bakış

```
                     ┌──────────────────────────────────────────────────┐
                     │            src/engine/ai/ (AI KATMANI)          │
                     │                                                  │
 Operator sorusu ───▶│  AutomotiveTokenizer (NLP)                       │
 + DTC listesi       │        │                                         │
 + telemetri         │        ▼                                         │
 + CAN frame         │  CausalBayesianInferenceEngine ──────────────▶   │──▶ Rapor (Markdown)
 (sağ tık "açıkla")  │   evaluate_diagnostic_query / explain_can_packet │    + Aksiyon butonları
                     │        │                                         │    (<!--ACTIONS:JSON-->)
                     │        ▼                                         │
                     │  AiDiagnosticCopilot.analyze_session             │
                     │   (_analyze_local_expert: 7 hard senaryo)        │
                     │        │                                         │
                     │        ▼                                         │
                     │  EXPERT_KNOWLEDGE_BASE + harici DB'ler           │
                     │  (data/diagnostics/*.json — 9.300+ DTC)          │
                     └──────────────────────────────────────────────────┘
```

Temel özellikler:

- **Tamamen çevrimdışı.** Ağ çağrısı yapan tek bir satır yoktur (aşağıda
  nasıl garanti edildiği var). API anahtarı, bulut LLM, internet gerekmez.
- **Deterministik.** Aynı girdi → aynı çıktı. Testi
  `test_same_input_same_output` (tests/unit/test_ai_copilot.py).
- **Otorite hiyerarşisi tek yönlüdür**: severity (aciliyet) yalnızca kural
  motorundan ve bilgi tabanından çıkar. Raporlarda LLM yoktur; "AI" adı
  altında çalışan şey bir kural + istatistik motorudur.
- **ISO 26262 uyumlu düşünce**: yanlış-positif teşhis yerine "veri yok"
  demek tercih edilir (No Fabricated Telemetry).

Mimari kararların tarihi: Bulut LLM (Gemini/OpenAI) katmanı hiçbir zaman
teşhis otoritesi değildi; izole bir "narrator" modülüne taşındıktan sonra
operatör kararıyla **tamamen kaldırıldı** (M7). Bu doküman yalnızca kalan
deterministik motoru anlatır.

---

## 2. Dosya Haritası

| Dosya | Rol |
|---|---|
| `src/engine/ai/diagnostic_copilot.py` (~4260 satır) | Motorun tamamı: NLP, bilgi tabanları, senaryolar, paket analizi, rapor sentezi, aksiyon tetikleri |
| `src/engine/ai/golden_cases.py` | Golden-Traces vaka yükleyici + doğrulayıcı (onarım-doğrulanmış kanıt tabanı) |
| `src/engine/ai/golden_similarity.py` | Vaka benzerlik motoru: Jaccard/alan/marka/sinyal skorlaması (yalnız kalibrasyona uygun vakalar) |
| `src/engine/ai/hypothesis_engine.py` | Arıza hipotezi üretimi ve sıralama |
| `src/engine/ai/discriminating_tests.py` | Ayırt edici test önerileri |
| `src/engine/ai/anomaly_detector.py` | Zaman serisi anomali + `telemetry_thresholds.json` eşik motoru |
| `src/engine/ai/evidence_gate.py` | Oturum kanıt kapısı (kanıtsız iddia geçemez) |
| `src/engine/ai/session_report.py` | Oturum raporu üretimi (benzer-vaka bloğu dahil) |
| `src/engine/ai/drive_safety_policy.py` | Sürüş güvenliği politikası |
| `src/engine/ai/user_kb.py` | Kullanıcı bilgi tabanı yükleyici (`user_kb.json`, fail-closed şema) |
| `src/engine/ai/user_report_composer.py` | Kullanıcı karar kartı (başlık/risk/öneri) |
| `src/engine/ai/harvest_validator.py` | Dış kaynak veri doğrulama + karantina kapısı (hasat hattı) |
| `src/core/models/diagnostics.py` | P1 veri modeli: `SignalSample`, `DiagnosticEvent`, `VehicleSession` (frozen) |
| `data/diagnostics/*.json` | Harici bilgi tabanları (aşağıda §5) |
| `data/golden_traces/cases/*.json` | Golden-Traces vaka dosyaları + `schema.json` |
| `tests/safety/test_ai_tx_isolation.py` | Mimari izolasyon kilidi (AST + namespace + ağ yasağı) |
| `tests/unit/test_ai_copilot.py` | Motorun davranış testleri |

---

## 3. Bileşenler

### 3.1 `AutomotiveTokenizer` — iki dilli, yazım hatasına toleranslı NLP

(`AUTOMOTIVE_SEMANTIC_DICTIONARY` L2461+, `AutomotiveTokenizer` L2507+)

- **`normalize_text`**: Türkçe karakter normalizasyonu (İ→i, ı→i vb.) +
  lowercase + noktalama temizliği.
- **`lemmatize_word`**: Türkçek ek çözümleme ("titriyor" → "titr",
  "basınçlı" → "basinc"). Basit kural tabanlı sonek kırpma.
- **`extract_semantic_intents(text) -> dict[domain, float]`**: metni 9
  semantik alana skorlar; skor = `min(1.0, match_count / 3.0)`:
  - tam kelime eşleşmesi +1.0, çok-kelimeli ifade +2.5,
  - **Levenshtein mesafesi ≤ 1 yazım hatası** (uzunluk ≥ 5) +0.8
    (`_levenshtein_distance` — Damerau varyantı, transpozisyon dahil).
- **`AUTOMOTIVE_SEMANTIC_DICTIONARY`** (L2461+): 9 alan —
  `MISFIRE`, `TURBO_BOOST`, `OVERHEAT_COOLING`, `EV_HV_BATTERY`,
  `HEAVY_DUTY_J1939`, `MARINE_NMEA2000`, `CAN_PHYSICAL_LAYER`,
  `UDS_PROTOCOL`, `ELECTRICAL_STARTING`. Her biri 15-20 TR/EN anahtar
  kelime içerir.

**Geliştirme notu:** Yeni bir araç alanı eklemek için sözlüğe yeni bir
anahtar + kelime listesi eklemek yeterlidir; `extract_semantic_intents`
otomatik olarak tarar. Sözlük bilinçli olarak typo-toleranslı çalışır.

### 3.2 `CausalBayesianInferenceEngine` — sorgu değerlendirme orkestrasyonu

(`CausalBayesianInferenceEngine` L2623+, `evaluate_diagnostic_query` L2696+)

Statik yöntemler; sınıf stateless. `evaluate_diagnostic_query(user_query,
active_dtcs, telemetry)` sırasıyla:

1. **Hata karesi yolu**: "(ERR)" / "hata karesi" → fiziksel katman
   teşhisi (sonlandırma direnci, bit stuffing, 60Ω/120Ω ölçümü).
2. **Hex payload çözümleme**: `extract_hex_payload_from_query` ile baytları
   çıkarır, `explain_can_packet`'e yönlendirir.
3. **DBC / sinyal haritası sorguları**: "dbc nedir", "golf dbc" →
   `search_dbc_catalog` katalog taraması.
4. **J1939 SPN yolu**: "SPN 84" gibi → `EXPERT_KNOWLEDGE_BASE` içinde
   `SPN{num}` anahtarı → `_format_j1939_technician_report`.
5. **OBD-II P kodları**: `\b([PBUC][0-9A-F]{4})\b` regex → KB eşleşmesi →
   4 aşamalı teknisyen raporu.
6. **Belirsiz sorgu**: niyet skorları + aktif DTC + telemetri ile genel
   uzman cevabı sentezlenir. Bilgi yoksa dürüstçe "Bu konu hakkında
   veritabanında yeterli bilgi bulunamadı" döner — **uydurma yok**.

`explain_can_packet` (modül seviyesinde L423+, en büyük yüzey):
CAN ID'yi protokol tablolarına göre sınıflandırır (J1939 PGN/CCVS/DM1/RTS/
CTS/ETP, OBD-II 7E0/7E8, marin N2K, EV BMS 0x18xx E5 ailesi...), DBC
decoder (fallback, tembel yüklenir) ile sinyalleri fiziksel değere
çözer, bilinmeyen ID'lerde "tanımsız — DLC ve bayt değişimlerini
inceleyin" der. İmza:
`explain_can_packet(can_id_hex_or_int: str|int, payload: bytes|list[int]|str) -> tuple[str, list[dict]]`

### 3.3 `AiDiagnosticCopilot._analyze_local_expert` — 7 hard senaryo

(`_analyze_local_expert` L4005+, `analyze_session` L3991+)

Oturum-bazlı analiz (`analyze_session(dtcs, telemetry, ecus)`). Sabit
kurallı senaryolar, her biri DTC deseni + telemetri eşiği eşleştirir:

| # | Senaryo | Tetik | Severity |
|---|---|---|---|
| 1 | Yağ basıncı | SPN 100 (+telemetri korelasyonu) | CRITICAL_STOP |
| 2 | EV HV izolasyon | P0AA6/P0A0B/P0A0D | CRITICAL_STOP |
| 3 | Enjektör hattı | SPN 651-656 | MEDIUM |
| 4 | DPF | SPN 3251/3719 | MEDIUM |
| 5 | Misfire | P030x | CRITICAL_STOP/MEDIUM |
| 6 | Turbo | SPN 102/P0234/P0299 | MEDIUM |
| 7 | Hararet | SPN 110 | CRITICAL_STOP |

Senaryo eşleşmeyen DTC'ler `EXPERT_KNOWLEDGE_BASE`'ten zenginleştirilir.
Çıktı: `DiagnosticAnalysisReport` (frozen dataclass; özet, severity,
kök-neden olasılığı, neden listesi, `TroubleshootingStep` listesi,
etkilenen alt sistemler, telemetri korelasyonları).

**Kök-neden güven skoru** (`compute_root_cause_confidence`, L324):
kanıtlar ağırlıklandırılır ve 0-1 normalize edilir:
`identification = (senaryo+KB eşleşmesi)/DTC sayısı`,
`telemetry_correlation = korelasyon/2`, `dtc_context = DTC/2`.
Ağırlıklar `ROOT_CAUSE_EVIDENCE_WEIGHTS` sabitinde. Skor ≥0.65 "Yüksek",
≥0.35 "Orta", altı "Düşük" — yüzde ile birlikte dürüstçe raporlanır
("ağırlıklı kanıt skoru"). Bilinmeyen DTC + korelasyon yok → düşük skor.

### 3.4 Aksiyon tetikleri — operatör butonları

(L90-280: `CopilotActionTrigger`, `make_uds_*_action`, `attach_action_triggers`,
`parse_action_triggers_from_text`, `extract_action_triggers`)

- Butonlar yalnızca **operatör metninden** (`extract_action_triggers("",
  user_query)` — "DTC temizle", "VIN oku" gibi kalıplar) veya
  deterministik DTC eşlemesinden üretilir.
- Metne `<!--ACTIONS:[{...}]-->` olarak gömülür; frontend
  (`diagnosticEngine.ts` → `parseActionMetadata`) parse edip buton çizer.
- Tıklama `execute_diagnostic_action` bridge'ine gider ve **TxSafetyGateway
  choke-point'inden** geçer (E-Stop → hız kilidi → çift onay).
- **P0 güvenlik sözleşmesi**: yabancı metin (LLM olsun olmasın) ASLA
  tetik taranmaz. `test_embedded_action_patterns_in_foreign_text_do_not_reach_executor`
  bunu kilitler.

### 3.5 VIN maskesi

`mask_vin_in_text` (L31): 17 haneli VIN'lerin ilk 11'i `*` yapar.
Golden-Traces ve `VehicleSession` bunu zorunlu kılar (ham VIN fail-closed).

---

## 4. Veri Akışı (Çağrı Zinciri)

**Canlı soru-cevap** (chat): `App.tsx` → `DesktopBridge.askCopilot` →
`DesktopApiBridge.ask_copilot` → `AiDiagnosticCopilot.analyze_live_telemetry`
→ `ensure_external_dtc_database_loaded()` (tembel) →
`CausalBayesianInferenceEngine.evaluate_diagnostic_query`.

**Oturum analizi** (DTC paneli): `analyze_session` → `_analyze_local_expert`.

**Frame açıklaması** (sağ tık): `explain_can_packet(can_id, payload)`.

**Aksiyon butonu**: rapor içinde `<!--ACTIONS:...-->` → frontend parse →
buton → `execute_diagnostic_action` → TxSafetyGateway.

---

## 5. Bilgi Tabanları

### 5.1 Inline: `EXPERT_KNOWLEDGE_BASE` (L821+)

Elle yazılmış, zengin DTC kayıtları: `title` (TR+EN), `subsystem`,
`severity`, `causes[]`, `steps[]` (eylem/hedef/loğifika-derecesi),
`measurement` (nominal değerler!), `uds_routine` (bağlı UDS servisi).
EV/HV bölümü özellikle derindir (HVIL loop direnci, izolasyon Ω/V
standartları, LOTO prosedürleri).

### 5.2 Harici DB'ler (`data/diagnostics/`, tembel yüklenir)

| Dosya | Boyut | İçerik |
|---|---|---|
| `dtc_database.json` | 26.5 MB | **14.352 DTC** (P/B/C/U) — KB şemasıyla aynı alanlar |
| `j1939_spn_fmi_database.json` | 23.9 MB | **4.253 SPN** kayıtları + FMI tanımları (SAE J1939-71/-73) |
| `uds_did_database.json` | 0.03 MB | UDS DID sözlüğü (68 DID) |
| `obd_mode06_database.json` | 0.08 MB | Mode 06 Monitored Test sonuçları (38 MID + 9 Class2) |
| `extended_pid_database.json` | 0.06 MB | Üretici-özel genişletilmiş PID'ler (112) |
| `nhtsa_can_recalls_database.json` | 0.41 MB | NHTSA geri çağırma kayıtları (282) |
| `nhtsa_can_complaints_database.json` | 5.9 MB | NHTSA şikâyet havuzu |
| `telemetry_thresholds.json` | — | Telemetri nominal eşikleri (`anomaly_detector` okur) |
| `user_kb.json` | — | Kullanıcı bilgi tabanı (`user_kb` okur, fail-closed) |
| `root_cause_graph.json` | — | Kök-neden grafiği (`hypothesis_engine` okur) |
| `PROVENANCE.md` | — | Veri kaynakları ve lisansları |

Yükleme: `ensure_external_dtc_database_loaded()` → `load_external_dtc_database`
→ yeni kodlar `EXPERT_KNOWLEDGE_BASE`'e merge edilir (mevcut inline kayıtlar
ezilmez). Kaynak dizin çözümü `_resolve_external_data_dir` (PyInstaller
frozen build desteği).

### 5.3 Golden-Traces (`data/golden_traces/cases/`)

Onarım-doğrulanmış vaka kanıt tabanı (P1): semptom + DTC + ilgi sinyalleri
+ **gerçek arıza** + onarım + doğrulama. Yükleyici `golden_cases.py`
stdlib-şema doğrulaması yapar: bilinmeyen alan, ham VIN, tarihsiz
`verified:true`, boş `actual_fault` ile verified — hepsi **fail-closed**
reddedilir. Draft vakalar kalibrasyona giremez. Vaka şeması:
`schema.json` (v1). Amaç: vaka benzerliği teşhis kalibrasyonu —
bkz. §7 geliştirme uçları.

---

## 6. Güvenlik ve İzolasyon Kilitleri (değiştirilemez kurallar)

1. **AST import taraması** (`tests/safety/test_ai_tx_isolation.py`):
   `src/engine/ai/**` altındaki her dosyanın import'u
   `FORBIDDEN_AI_IMPORT_ROOTS` sabitine karşı denetlenir — HAL,
   TxSafetyGateway, E-Stop, UDS/J1939 istemcileri, TxPort **ve tüm ağ
   istemcileri** (`urllib`, `http`, `socket`, `requests`). Yeni kökler
   sabite eklenerek sınırlar sıkılaştırılır. Bu testi kıran PR reddedilir
   (AGENTS.md §2.8).
2. **Runtime ağ yasağı**: `urlopen` patlayıcıya yamanmış halde
   `analyze_session` koşulur — hiçbir ağ çağrısı yapılmadığının davranış
   kanıtı.
3. **Trigger kaynağı izolasyonu**: yabancı metin → buton üretilemez.
4. **Dürüstlük**: bilinmeyen konu → "veri yok" cevabı; NaN/Inf telemetri →
   P1 modelinde reddedilir.

Geliştirme yaparken bu dört testi her zaman yeşil tutun.

---

## 7. Geliştirme İçin Açık Uçlar (öncelik sıralı)

### U1 — Golden-Traces vaka benzerliği motoru ✅ TAMAMLANDI
`src/engine/ai/golden_similarity.py` Jaccard (DTC kümesi) + alan/marka/sinyal
ağırlıklı skorlamayı gerçekler (`dtc_jaccard` 0.50 / `signals` 0.25 /
`domain` 0.15 / `make` 0.10); raporlama eşiği 0.50, yüksek-benzerlik eşiği
0.65; corpus <2 vakada "yüksek benzerlik" ifadesi yasaktır. Yalnız
`calibration_eligible_cases()` çıktısı katılır. Tüketildiği yerler:
`desktop_app` (`find_similar_cases`), `session_report.py`,
`hypothesis_engine.py`.
Kalan iş: kalibrasyon korpusunu büyütmek — `data/golden_traces/cases/`
altında şema + 1 taslak tohum vaka var; yeni vakalar operatör verisiyle,
hiçbir alan uydurulmadan eklenir (AGENTS.md AI katmanı kuralları).

### U2 — Telemetri eşik veritabanı ✅ TEMEL TAMAMLANDI
Nominal aralıklar `data/diagnostics/telemetry_thresholds.json` altında JSON
olarak durur ve `anomaly_detector` tarafından okunur. Kalan iş: yeni
SPN/semptom boşlukları kapatıldıkça eşik kapsamını genişletmek.

### U3 — Levenshtein tolerans ayarı
Şu an mesafe ≤1. Yanlış-negatif (tutmayan kelime) şikayetlerinde
tutarlılık gerektirir: toleransı artırmak yanlış-positif alan
skalamalarını da büyütür — `extract_semantic_intents` skor formülüyle
birlikte ayarlayın ve `test_extract_semantic_intents` tarzı birim
testleriyle sabitleyin.

### U4 — `_analyze_local_expert` refactoring
~280 satır prosedürel. Senaryoları birer kural objesine (pattern eşleştirici +
telemetri eşikleri + rapor şablonu) çevirmek U2 ile birleşir.

### U5 — Diğer kesişimler
- `explain_can_packet` protokol tabloları sabit — J1939 PGN sözlüğünü
  `j1939_spn_fmi_database.json`'dan besleyerek genişletilebilir.
- Türkçe/İngilizce lemmatizer İtalyanca/Almanca gibi dillere
  genişletilebilir (`normalize_text` karakter haritası ile).

### U6 — Ölçüm/ölçek
Tüm motor Python; sıcak yol (chat) <10 ms tipik. Büyük harici DB
aramalarında gerekirse LRU cache (DBC decoder'daki gibi) eklenir;
PyInstaller boyut planlaması unutulmasın (dtc_database.json 26.5 MB).

---

## 8. Test Yürütme

```bash
# Motor davranış testleri
python -m pytest tests/unit/test_ai_copilot.py -q

# İzolasyon kilitleri (HER değişiklikte çalıştır)
python -m pytest tests/safety/test_ai_tx_isolation.py -q

# Tüm proje + kapı
python -m pytest -q --cov=src --cov-fail-under=80
```

İzolasyon testine yeni yasak kök eklemek: `FORBIDDEN_AI_IMPORT_ROOTS`
sabitine ekleyin — tarama otomatik sıkılaşır.

---

## 9. Katkı Kontrol Listesi

- [ ] `tests/safety/test_ai_tx_isolation.py` yeşil mi? (zorunlu)
- [ ] Aynı girdi → aynı çıktı korunuyor mu? (determinizm)
- [ ] Bilinmeyen girdide "veri yok" davranışı korunuyor mu?
- [ ] Yeni veri alanı `schema.json`/doğrulayıcıya eklendi mi?
- [ ] Golden-Traces'e veri eklerken: değerler saha kaynağı mı (uydurma yok)?
- [ ] Severity yalnız kural/KB'den mi geliyor?
- [ ] Kapsama ≥%80 (CI kapısı)?

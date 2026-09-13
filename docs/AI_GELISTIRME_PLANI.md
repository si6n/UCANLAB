# Çevrimdışı AI Analizi ve Geliştirme Planı

Analyst: tuner [Dev] — 2026-09-13
Kapsam: `src/engine/ai/**` (11 modül) + `data/diagnostics/*` veri katmanları
Yöntem: Kaynak kod satır referanslı statik analiz (tahmin yok, her iddia dosya:satır kanıtlı)

---

## 1. Mevcut Durum (kanıtlı)

### 1.1 Modül envanteri

| Modül | Satır | Rol |
|---|---|---|
| `diagnostic_copilot.py` | 2961 | Ana motor: tokenizer, KB, DB yükleyiciler, rapor formatlayıcılar |
| `hypothesis_engine.py` | 255 | root_cause_graph.json + hipotez sıralama (FAZ 4) |
| `golden_similarity.py` | 173 | Golden-Case benzerlik araması (FAZ 2) |
| `golden_cases.py` | 236 | Kullanıcı doğrulamalı vaka yükleyici (FAZ 1) |
| `user_kb.py` | 114 | user_kb.json yükleyici, fail-closed şema (K1) |
| `user_report_composer.py` | 188 | Kullanıcı karar kartı (başlık/risk/öneri) |
| `evidence_gate.py` | 137 | Oturum kanıt kapısı (aktif DTC event'leri) |
| `anomaly_detector.py` | 225 | telemetry_thresholds.json eşik motoru |
| `discriminating_tests.py` | 66 | Hipotez ayrıştırıcı test önerileri |
| `session_report.py` | 145 | Oturum raporu + eylem tetikleyicileri |
| `drive_safety_policy.py` | 108 | Sürüş güvenlik politikası |

### 1.2 SPN farkındalığı: VAR (kısmen)

**Kanıtlar:**

- `SPN_####` anahtar arama: **VAR** — `src/engine/ai/diagnostic_copilot.py:2214`
  ```python
  spn_entry = j1939_db.get("spns", {}).get(f"SPN_{spn_num}")
  ```
- `get_j1939_spn_database()` çağrısı: **VAR** — `diagnostic_copilot.py:2213` (sorgu akışı) ve `diagnostic_copilot.py:2492` (FMI fallback), `diagnostic_copilot.py:1460-1480` (tanım; 6.25 MB JSON, module-level `_CACHED_J1939_DB` önbelleği `diagnostic_copilot.py:1376`)
- Sorgu regex'i `\bspn\s*([0-9]+)\b`: `diagnostic_copilot.py:2033` ve `2206`
- FMI yorumlama: **VAR** — `diagnostic_copilot.py:2486-2506`: `\bfmi\s*([0-9]+)\b` yakalanır, önce `spn_entry["fault_matrix"][fmi_num]` (`:2490`), boşsa `fmi_definitions[fmi_num]` fallback (`:2493-2500`), sonuç rapora `fmi_name/fault_title/diagnostic_action/severity` olarak işlenir (`:2502-2506`)
- DB içeriği (ölçüldü): 3710 SPN, 3529'unda `fault_matrix` dolu, 3710'unda `title_tr`, hepsinde `name`, `subsystem`; `associated_pgn` yalnız 208 SPN'de; `fmi_definitions` 20 kayıt (metadata 57 diyor — uyumsuzluk, bkz. Boşluk 5)
- DM1/DM2 çözme AI modülünde YOK — `src/protocols/j1939/diagnostics.py:150` `parse_dm1_or_dm2` var; AI'a giren yol: `src/ui/desktop_app.py:2488,2589` ve `src/engine/pipeline/reassembly_pipeline.py:444` DM1/DM2 parse eder, DTC dict'i (`{"code","spn","fmi"}`) AI'a öyle taşınır. AI ham DM1 byte'ı asla görmez, sadece parse edilmiş `d.get("spn")` (`diagnostic_copilot.py:2324`, `2861`).

### 1.3 Kullanılan veri katmanları

| Katman | Kayıt | AI'da okunuyor? | Kanıt |
|---|---|---|---|
| DTC (14.352) | `dtc_database.json` (16.9 MB) | EVET — `EXPERT_KNOWLEDGE_BASE`'e merge, lazy+thread-safe (`diagnostic_copilot.py:1415-1457`, `1812-1821`) | `load_external_dtc_database` |
| J1939 SPN (3.710) | `j1939_spn_fmi_database.json` (6.25 MB) | EVET — SPN sorgu yolu + FMI fallback (`:2213`, `:2492`) | `get_j1939_spn_database` |
| UDS DID (68) | `uds_did_database.json` | Yükleyici VAR, tüketim sınırlı | `get_uds_did_database` `:1483` |
| Mode06 (38+9) | `obd_mode06_database.json` | Yükleyici VAR | `get_mode06_database` `:1506` |
| NHTSA recall (282) | `nhtsa_can_recalls_database.json` | EVET — recall sorguları + 4-stage rapor gömmesi (`:2279`, `:2441`) | `search_nhtsa_recalls` `:1716` |
| Extended PID | `extended_pid_database.json` | Yükleyici VAR | `get_extended_pid_database` `:1532` |
| root_cause_graph | `root_cause_graph.json` | EVET (hypothesis_engine) | `hypothesis_engine.py:132` |
| telemetry_thresholds | `telemetry_thresholds.json` | EVET (anomaly_detector) | `anomaly_detector.py:103` |
| user_kb | `user_kb.json` | EVET (user_report_composer) | `user_report_composer.py:97` |

### 1.4 Kullanılmayan katmanlar — KRİTİK BOŞLUK

`grep oem_variants|gm_monitor|nhtsa_evidence src/**` → **0 sonuç** (tüm src/ tarandı).

| Katman | Kayıt sayısı | Durum |
|---|---|---|
| `oem_variants` (2.911 DTC) | dtc_database.json içinde | **OKUNMUYOR** — dtc_database.json'a merge edildi (örn. P0101 → AUDI varyant açıklaması) ama hiçbir AI/rapor kodu bu alanı okumuyor. `_format_4stage_technician_report` (`diagnostic_copilot.py:2416-2471`) yalnız `causes/steps/measurement/uds_routine/severity/title/subsystem` okur. |
| `gm_monitor` (2.256 DTC) | dtc_database.json içinde | **OKUNMUYOR** — GM monitor parametreleri (örn. P0101 MAF rationality testi) hiçbir çıktıda görünmüyor. |
| `nhtsa_evidence` (233 DTC) | dtc_database.json içinde | **OKUNMUYOR** — per-DTC NHTSA ODI şikayet kanıtları raporlara hiç girmiyor. (Recall DB ayrı ve o okunuyor.) |
| J1939 `dd_procedures`, `oem_field_evidence`, `sitrak_systems`, `oem_engine_families` | SPN girdilerinde (SPN_100 örneği) | **OKUNMUYOR** — `_format_j1939_technician_report` (`:2474-2518`) yalnız `name/title_tr/subsystem/pgn/unit/description/range/fault_matrix` okur. OEM vaka kanıtı (Detroit DD prosedürleri, Cummins oluşumları) DB'de duruyor, raporda yok. |
| NHTSA complaints (6.2 MB) | `nhtsa_can_complaints_database.json` | **Hiçbir yükleyici yok** — diagnostic_copilot'ta complaints accessor tanımlı değil (grep: 0 hit). |

Ölçüm kanıtı (Python json.load + sayım, 2026-09-13):
```
dtc_database.json: 14352 kayıt; oem_variants: 2911, gm_monitor: 2256, nhtsa_evidence: 233 dolu
severity dağılımı: MEDIUM 13154, LOW 536, CRITICAL_STOP 281, HIGH 379, INFO 2
P:9291, U:1883, C:1388, B:1790; vag_code'lu: 2102
j1939_spn_fmi_database.json: 3710 SPN; fault_matrix 3529; title_tr 3710; associated_pgn 208
```

### 1.5 Çıkarım zinciri (gerçek akış, kod izlendi)

**"P0301 nedir?"** (`analyze_live_telemetry` → `CausalBayesianInferenceEngine.evaluate_diagnostic_query`, `diagnostic_copilot.py:2008`):
1. Tokenizer: `extract_semantic_intents` (`:1933`) + `normalize_text` (`:1916`)
2. Trafik/DM11/DM1/VIN/clear/session eylem kısa yolları (`:2024-2098`)
3. DTC regex `\b([PBUC][0-9A-F]{4})\b` (`:2202`) → `direct_dtc = "P0301"`
4. `EXPERT_KNOWLEDGE_BASE["P0301"]` (14.352'li merge edilmiş KB) → `_format_4stage_technician_report("P0301")` (`:2332-2333`): causes[:2] + steps[:2] + measurement + uds_routine + NHTSA recall bloğu (`:2439-2450`)
5. Not: merge edilmiş kayıtlarda `causes/steps` **olmayan** 5052 kayıt için (`causes` 9300/14352) boş liste → `top_causes` fallback tek satır (`:2424`)

**"SPN 100 FMI 4"** (aynı giriş, `:2206-2222`):
1. `spn_match` yakalar (`:2206`) → `SPN100` EXPERT_KB'de var mı? → VAR (hardcoded, `:938`) → `direct_dtc="SPN100"` → 4-stage P-raporu
2. `SPN100` hardcoded'da olmasaydı: `get_j1939_spn_database()["spns"]["SPN_100"]` (`:2213-2214`) → `_format_j1939_technician_report` (`:2474`): başlık + subsystem/PGN + FMI 4 fault_matrix satırı (`:2490` → `:2502-2506`) + DM1/DM11 eylem butonları (`:2517`)
3. DB'de de yoksa: dürüst "Kayıt bulunamadı" mesajı (`:2218-2222`)

**Oturum analizi** (`analyze_session` → `_analyze_local_expert`, `:2805-2935`):
1. 7 senaryo kuralı (SCENARIO_RULES, U4) — SPN110 hararet (`:2741`), yağ basıncı vb.
2. Senaryoya düşmeyen DTC'ler için KB lookup: `d.get("spn")` → `"SPN{n}"` anahtarı (`:2860-2862`)
3. **EKSİK:** `spn` alanı EXPERT_KB'de hardcoded SPN'lerden (938-1142: SPN100/102/110/190/1761/3251/3364/4364/651/1087) biri değilse ve `code` alanı KB'de yoksa → **3710'luk J1939 DB hiç denenmez** — DM1'den gelen SPN 629 gibi bir kod "aktif arıza tespit edildi" genel fallback'ine (`:2888-2897`) düşer. Bu, en sık üretim yolu (canlı DM1 akışı) için SPN DB'sinin BYPASS edildiği anlamına gelir.

---

## 2. Tespit Edilen Boşluklar (öncelik sırası)

| # | Boşluk | Etki | Efor | Öncelik |
|---|---|---|---|---|
| 1 | Oturum analizinde SPN→J1939 DB fallback yok: `_analyze_local_expert` `SPN{n}` anahtarını yalnız EXPERT_KB'de arar, `get_j1939_spn_database()`'e hiç düşmez (`diagnostic_copilot.py:2860-2862`) | Canlı DM1 akışından gelen 3700+ SPN'nin teşhis içeriği (causes/steps/severity) rapora hiç girmez; sorgu yolu ile oturum yolu asimetrik | Düşük (~30 satır) | **P0** |
| 2 | `oem_variants` (2.911), `gm_monitor` (2.256), `nhtsa_evidence` (233) alanları merge edildi ama hiçbir kod okumuyor (src/ geneli grep: 0 hit) | 5 MB+ kuratılmış OEM kanıtı raporda ölü yük; "aynı P-kodu farklı markada farklı anlam" sorusuna cevap verilemiyor | Düşük-Orta (~40 satır) | **P0** |
| 3 | SPN raporu FMI severity'yi göstermiyor: `_format_j1939_technician_report` FMI bloğunu basar ama `fault_matrix[fmi]["severity"]` (örn. FMI1=CRITICAL_STOP) rapor başlığındaki severity'ye yansımıyor (`:2501-2516`); toplam severity hesaplamasına hiç girmiyor | SPN 100 FMI 1 gibi kritik arıza MEDIUM gibi raporlanabilir | Düşük (~10 satır) | **P1** |
| 4 | Çoklu DTC korelasyonu zayıf: senaryo kuralları tek DTC/telemetri eşleşmesi (`:2741` tekil `any()`), `_analyze_local_expert` DTC'leri bağımsız işler (`:2857` döngüsü), correlations yalnız 7 senaryodan üretilir | İlişkili kod kümeleri (SPN 3216+3226 NOx çifti, P0299+P0401) birlikte değerlendirilmez; korelasyon motoru olarak hypothesis_engine var (root_cause_graph `expected_dtcs` kesişimi, `hypothesis_engine.py:184`) ama graph yalnız birkaç düğüm (root_cause_graph.json 6.6 KB) | Orta | **P1** |
| 5 | FMI tanım kataloğu eksik: DB `fmi_definitions` 20 kayıt (0-19), metadata `total_fmis: 57` diyor; J1939 FMI 20-31 tanımsız → fallback `fmi_def` boş → FMI satırı hiç basılmaz | FMI 20-31 sorgularında (örn. FMI 31 "condition exists") sessiz bilgi kaybı | Düşük (veri) | **P1** |
| 6 | `associated_pgn` yalnız 208/3710 SPN'de; `pgn_acronym` var ama J1939 SA (source address → hangi ECU yayınlıyor) verisi yok | "hangi ECU'nun uyarısı" sorusu cevapsız (SA→ECM/TCM/ABS eşlemesi eksik) | Orta (veri toplama) | **P2** |
| 7 | NHTSA complaints DB (6.2 MB) için accessor yok; `nhtsa_evidence` alanı da okunmuyor (Boşluk 2) | Per-DTC şikayet istatistiği ("bu kod bu modelde yaygın mı") raporlanamıyor | Düşük (veri zaten var) | **P2** |
| 8 | KB merge edilen 5052 kayıtta `causes/steps` yok (9300/14352 dolu) → tek satır jenerik fallback (`:2424`) | Geniş DB kapsamasına rağmen yarısı "kontrol edin" seviyesinde kalıyor | Veri (harici) | **P2** |
| 9 | J1939 DB `causes` alanı hiçbir SPN'de yok (0/3710) — causes zinciri yalnız 10 hardcoded SPN'de var | "SPN → olası nedenler zinciri" 3700 SPN için mevcut değil | Veri (harvest) | **P2** |
| 10 | `user_kb.json` (11 KB) küçük; `user_report_composer._kb_code_variants` "SPN 100 FMI 1"→"SPN100" deniyor (`user_report_composer.py:74-82`) ama user_kb'de SPN kaydı yoksa dürüst genel şablon — J1939 DB'ye köprü yok | Kullanıcı kartı SPN'lerde hep jenerik | Düşük | **P3** |

### Kapasite (Soru 5 cevabı — ölçülmüş sınırlar)

- J1939 DB soğuk yükleme: CI'de assert < 2.0s, tepe bellek < 150 MB (`tests/unit/test_j1939_v190_load.py:76-77`); 1000 sıcak çağrı < 50ms (`:88`)
- Oturum analizi: < 50ms/oturum, 20 örnek sonrası < 5MB büyüme (`:147`, `:162`)
- DTC DB: lazy + tek sefer merge (`:1812-1821`), 16.9 MB JSON tek parse; `causes` okuma `info.get("causes", [])` — bellek zaten kabullenilmiş, yeni alan okumaları ek maliyet ≈ 0
- **Sonuç:** 14.352 DTC + 3.710 SPN mevcut mimariyle sınıra takılmıyor; performans engeli değil, veri-bağlantı engeli var.

---

## 3. Geliştirme Planı (faz faz)

### Faz 1 — Oturum yolunda SPN DB fallback (P0, ~30 satır, 0 harici bağımlılık)

- [ ] `diagnostic_copilot.py:2860-2862` — `_analyze_local_expert` KB lookup'ında `spn_candidate` EXPERT_KB'de yoksa `get_j1939_spn_database()["spns"][f"SPN_{n}"]` dene; bulunca `fault_matrix`'ten causes/steps/severity sentezle (FMI `d.get("fmi")` ile `fault_matrix[fmi]` satırını seç, severity'i `_raise_severity`'ye taşı)
- [ ] Regresyon testi: `tests/unit/test_j1939_v190_load.py::TestCopilotSession` genişlet — SPN 629 FMI 12 (DB'de causes'ı olmayan, hardcoded dışı) oturum analizinde boş fallback'e DEĞİL sentezlenmiş neden listesine düşmeli; mevcut `test_analyze_session_with_spn_codes` davranış kilidi korunan tek test
- [ ] Doğrulama: `pytest tests/unit/test_j1939_v190_load.py tests/unit/test_ai_copilot.py tests/safety/test_ai_tx_isolation.py -q` (AI-TX izolasyon invariant'ı: yeni kod HAL/TX import eklememeli — sadece mevcut `get_j1939_spn_database` çağrısı, izinli)

### Faz 2 — Ölü OEM katmanlarını rapora bağla (P0, ~40 satır)

- [ ] `_format_4stage_technician_report` (`:2416`): `info.get("oem_variants")` → marka bazlı varyant bloğu (ilk 3 OEM, tek satır özet; AUDI/VW/Ford farklı nedenleri); `info.get("gm_monitor")` → "GM Monitor Testi" satırı (`parameter` + `monitor` kısaltılmış); `info.get("nhtsa_evidence")` → "Alan Kanıtı" satırı (ilk 1 ODI şikayeti, make/model/yıl)
- [ ] `_format_j1939_technician_report` (`:2474`): `spn_entry.get("dd_procedures")` + `oem_field_evidence` → "OEM Saha Prosedürü" bloğu; `sitrak_systems`/`oem_engine_families` → occurrence satırı
- [ ] `extract_action_triggers` taraması yeni metin üzerinden zaten çalışır (`:2470`) — yeni bloklarda eylem kelimeleri (`0x14`, `dm11` vb.) tetiklenebilir, bilinçli kullan
- [ ] Test: her üç alan için birer fixture DTC (P0101 üçüne de sahip) — rapor metninde alan içerikleri görünmeli; alanı olmayan DTC raporu değişmemeli (backward-compat lock)

### Faz 3 — FMI severity + çoklu DTC korelasyonu (P1)

- [ ] `_format_j1939_technician_report` FMI bloğuna severity etiketi ekle (`fmi_tree.get("severity")`, `:2501`); CRITICAL_STOP ise başlık önceliğini yükselt
- [ ] FMI 20-31 tanımlarını DB'ye ekle (veri değişikliği, `fmi_definitions` 20→32; `total_fmis` metadata ile tutarlı hale getir — Boşluk 5)
- [ ] `_analyze_local_expert` DTC döngüsünden önce aktif kod kümesinden bilinen ilişkili-çift tablosu eşleştir (başlangıç: SPN 3216+3226, P0299+P0401, U0100+P0620 gibi 5-10 çift, `root_cause_graph.json` düğümlerine paralel); eşleşen çift `correlations` listesine "ilişkili kod kümesi" olarak girer — `compute_root_cause_confidence` (`:317`) `telemetry_correlation_count` üzerinden otomatik faydalanır
- [ ] `hypothesis_engine`'i oturum analizine bağla (şu an yalnız hipotez API'si; `analyze_session` çağırmıyor): `rank_hypotheses` sonucunun top-1'i `DiagnosticAnalysisReport.summary`'ya "kök neden adayı" satırı olarak ekle (FAZ 4 altyapısı hazır, sadece çağrı eksik)

### Faz 4 — Veri katkıları (P2, kod değil veri)

- [ ] J1939 SA→ECU eşlemesi DB'ye (SA 0=Motor, 3=Trafik, 49=ABS gibi standart tablo) → SPN raporunda "Yayınlayan ECU" satırı
- [ ] `associated_pgn` kapsamını 208→hedef 1000+ (canboat PGN YAML zaten kaynakta, harvest pipeline'ı mevcut)
- [ ] NHTSA complaints accessor (Boşluk 7) — kullanım: kullanıcı kartı "alan sıklığı" rozeti
- [ ] SPN-level `causes` harvest (Boşluk 9) — DieselLaptops/4RoadService kaynakları zaten PROVENANCE.md'de

### Faz 5 — Kullanıcı kartı J1939 köprüsü (P3)

- [ ] `user_report_composer.compose_user_card`: KB'de olmayan `SPN{n}` kodları için `get_j1939_spn_database()`'den `title_tr` ile dürüst kart üret (uydurma yok — yalnız DB alanları)
- [ ] `user_kb.json`'a yüksek-darbe SPN'ler için operatör onaylı kart ekle (Golden-Traces disiplini: operatör verisi olmadan draft kalır)

---

## 4. SPN Kontrolü — Detaylı Cevap

**Soru: Çevrimdışı yapay zekamız SPN'leri kontrol ediyor mu?**

**Cevap: EVET, iki yoldan — ama asimetrik ve üç önemli delikle.**

**VAR olan (kanıtlı):**
1. **Etkileşimli sorgu yolu tam çalışıyor.** "SPN 100 FMI 4" yazınca: regex `\bspn\s*([0-9]+)\b` (`diagnostic_copilot.py:2206`) → hardcoded `SPN100` KB (`:938-`) veya `get_j1939_spn_database()["spns"]["SPN_100"]` (`:2213-2214`) → FMI 4 `fault_matrix` satırı + `fmi_definitions` fallback (`:2486-2506`) → J1939 teknisyen raporu + DM1/DM11 eylem butonları (`:2517`). 3710 SPN'nin tamamı sorgu yoluyla erişilebilir, 3529'unda FMI bazlı fault_matrix var.
2. **FMI yorumlama gerçek.** Sorgudaki FMI numarası SPN'in `fault_matrix`'inden spesifik fault_title + diagnostic_action çeker; SPN'e özgü kayıt yoksa genel `fmi_definitions` kataloğundan (şu an 20 kayıt).
3. **DM1 entegrasyonu parse-katmanında mevcut.** `parse_dm1_or_dm2` (`src/protocols/j1939/diagnostics.py:150`) SPN/FMI'yi byte'dan çözer; UI (`desktop_app.py:2488`) ve reassembly pipeline'ı bunu DTC dict'ine çevirir; copilot bu dict'ten `d.get("spn")` okur (`:2324`, `:2861`). Uçtan uca test de var: `tests/unit/test_j1939_v190_load.py:165-183` (DM1 byte → SPN_629 → fault_matrix[12]).
4. **Otomatik senaryolar SPN farkındalı.** HEAVY_DUTY_J1939 intent'i (`:2361-2372`) yağ basıncı/DPF/AdBlue/injektör/fren kelimelerini SPN100/3251/3364/651/1087/4364 raporlarına yönlendirir.

**DELİK 1 (en kritik): Oturum analizi SPN DB'sini bypass ediyor.** `_analyze_local_expert` (`:2856-2862`) aktif DTC'lerde yalnız `SPN{n}` anahtarını EXPERT_KB'de (hardcoded ~10 SPN + 14352 DTC) arar — J1939 DB'ye (3710 SPN) hiç düşmez. Canlı DM1 akışından gelen bilinen-bir-SPN dahi causes/steps/severity'siz genel fallback'e (`:2888-2897`) düşer. Sorgu yolu zengin, oturum yolu fakir — Faz 1 bunu kapatır.

**DELİK 2: OEM kanıt katmanları ölü.** `oem_variants` (2.911), `gm_monitor` (2.256), `nhtsa_evidence` (233) alanları dtc_database.json'da mevcut ama `src/` genelinde tek satır bile bunları okumuyor (grep kanıtı: 0 hit). "Aynı P-kodu farklı markada farklı anlam" sorusunun verisi hazır, tüketimi yok — Faz 2.

**DELİK 3: FMI severity ve korelasyon eksik.** FMI bloğundaki severity (`fault_matrix["1"]["severity"] = "CRITICAL_STOP"` gibi) rapor başlığına/hesaplamaya taşınmıyor; FMI 20-31 tanımsız; ilişkili DTC çiftleri birlikte değerlendirilmiyor — Faz 3.

**Kapasite:** 14.352 DTC + 3.710 SPN mevcut mimaride ölçülmüş sınırlar içinde (soğuk yük < 2s, tepe RAM < 150MB, oturum < 50ms — `test_j1939_v190_load.py:76-77,88,147`). Darboğaz veri değil, veri-bağlantısı.

---

## Ek Notlar

- Bu analiz sırasında kod DEĞİŞTİRİLMEDİ (kural gereği); tüm referanslar 2026-09-13 HEAD'ine aittir.
- Önerilen tüm Faz 1-3 değişiklikleri `tests/safety/test_ai_tx_isolation.py` kısıtlarına uygundur (yeni import yok, yalnız mevcut modül-içi fonksiyon çağrıları).
- Faz 1 için hazır diff taslağı istenirse `diagnostic_copilot.py:2856-2862` bölgesine `_lookup_spn_kb_or_j1939(code, spn)` yardımcısı olarak yazılabilir.

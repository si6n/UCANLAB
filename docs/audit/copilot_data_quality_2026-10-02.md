# Copilot veri denetimi — 2026-10-02 (copilot upgrade)

Araç: `scripts/validate_copilot_data.py` (CI'da koşar) + bu turdaki elle
incelemeler. Tüm sayılar ölçüldü; tahmin yok.

## 1. Copilot hangi veriyi kullanıyordu? (önce → sonra)

Kanıt yöntemi: `src/engine/ai/**` içinde dosya adına referans (grep) + yükleyici
çağrısı. "Sonra" sütunu `KnowledgeBase.SOURCE_FILES` kaydıdır.

| Kaynak | Önce (AI katmanında okuyan) | Sonra |
|---|---|---|
| `dtc_database.json` | `diagnostic_copilot.load_external_dtc_database` | KB `dtc()` (aynı doğrulanmış birleşik görünüm) |
| `j1939_spn_fmi_database.json` | `diagnostic_copilot`, `j1939_severity` | KB `spn()`, `fmi_definition()`, PGN→SPN indeksi |
| `extended_pid_database.json` | `diagnostic_copilot` | KB `pid()` (servis+PID indeksi) |
| `obd_mode06_database.json`, `uds_did_database.json` | `diagnostic_copilot` | KB `mode06()`, `uds_did()` |
| `root_cause_graph.json` | `hypothesis_engine`, `dialogue_engine`, `calibration` | KB kod→düğüm indeksi |
| `signal_aliases.json` | `hypothesis_engine`, `diagnostic_copilot` | KB `canonical_signal()` |
| `telemetry_thresholds.json` | `anomaly_detector`, `diagnostic_copilot` | KB `telemetry_threshold()` |
| `dtc_severity_rules.json` | `severity_rules` | KB `dtc_severity()` |
| `nhtsa_can_recalls/complaints` | `diagnostic_copilot` (sohbet yolu) | KB `recalls()`, `complaint_count()` → ayrı NHTSA bölümü |
| `user_kb.json`, `knowledge/dtc_procedures`, `dbc/catalog.json`, `golden_traces/` | ilgili modüller | KB üzerinden de erişilebilir |
| **`canonical_symptoms.json`** (152) | **YOK** (yalnız CSV üreticisi; `symptom_mapper` kendi 19 kayıtlık iç listesini kullanıyordu) | Şikâyet → semptom → aday kod |
| **`system_taxonomy.json`** | **YOK** (yalnız yorum satırı) | Etiket→sistem indeksi, fren/direksiyon güvenlik tespiti |
| **`hv_safety_thresholds.json`** | **YOK** | UN R100 izolasyon Ω/V kontrolü |
| **`canboat_pgn_reference.json`** | **YOK** (yalnız UI atıf kataloğu) | PGN açıklaması, DM1 alan düzeni |
| **`dtc_database_oem_layer.json`** | **YOK** (yalnız UI atıf kataloğu) | OEM marka listesi, kayıt dışı kodların genel tanımı |
| `symptom_lexicon.json`, `signal_measurement_map.json`, `copilot_glossary.json` | — (yeni) | Gündelik ifade, ölçüm rehberi, jargon açıklaması |
| `knowledge/isobus_ddi`, `data/traces/` | protokol/kayıt katmanı (copilot değil) | değişmedi (copilot kapsamı dışında) |

Özet: önce 20 bilgi kaynağının **15**'i AI katmanında okunuyordu, 5'i hiç
kullanılmıyordu. Şimdi 20'nin tamamı + 3 yeni kaynak (toplam **23**) tek
`KnowledgeBase` katmanından erişilir.

## 2. Doğrulama sonuçları

| Ölçüm | Önce | Sonra |
|---|---|---|
| Ayrıştırılan JSON dosyası | 685 | 686 (+`quarantine/t21_seed_audit.json`, −`.t21_before`) |
| DTC kaydı `dtc_record_schema` + sentezlenmiş provenance ile geçerli | 14.181 / 14.484 | **14.484 / 14.484** |
| `hv_safety_thresholds` provenance şemaya uygun | 0 / 6 | **6 / 6** |
| Türkçe açıklamalı FMI tanımı | 20 / 32 | **32 / 32** |
| Sistem kimliği olan graf düğümü | 8.845 / 8.884 | **8.884 / 8.884** |
| Standart (üreticiden bağımsız) Mode 01 PID kaydı | 114 (05, 0C, 0D, 10… yoktu) | **132** |
| CSV ikizi tek komuttan üretilen | 5 (+1 ayrı araç, NHTSA üreticisiz) | **7 / 7**, bayt-eşdeğer, `--verify` |
| Provenance bloğu doğrulanan | — | 88 |
| Doğrulayıcı FAIL | (araç yoktu) | **0** |

## 3. Bulgular ve yapılanlar

| # | Bulgu | Kanıt | Yapılan |
|---|---|---|---|
| 1 | 303 DTC kaydında `procedures_full` liste yerine tek metin ("1. … 2. …"); şema dışı ve copilot okuyucusu (`isinstance(list)`) bunları **sessizce yok sayıyordu** | ör. `U0100` | Kendi "N." işaretlerinden bölündü (303'ünün de numaralandırması 1..n kesintisiz — yazmadan önce doğrulandı); metin değişmedi. `scripts/copilot_data/fix_data_quality.py` |
| 2 | 12 FMI (13, 20–30) Türkçe açıklamasız | `fmi_definitions` | Her kaydın kendi SAE J1939-73 adının Türkçesi + `description_tr_source` |
| 3 | 39 seed graf düğümü `system` alanı taşımıyor | `root_cause_graph.json` | Düğümün ilk kodunun kayıttaki alt sistem etiketi → `system_taxonomy` eşlemesiyle türetildi |
| 4 | HV eşik provenance'ı şemaya uymuyor (`agent` yok, 2 enum dışı `source.type`) | doğrulayıcı | `agent` dürüstçe `legacy-unrecorded` olarak; tipler şema karşılığına eşlendi; değer/alıntılara dokunulmadı |
| 5 | T2-4 OBDex birleştirmesi, hex numarası bir Toyota Torque satırıyla çakışan 18 **standart** Mode 01 PID'i atlamış (05 soğutma suyu, 0C devir, 0D hız, 10 MAF, 11 TPS…) | `data/PROVENANCE.md` §3.2 "18'i zaten mevcuttu" | Aynı sabit, sha256'sı doğrulanmış CC0 dosyasından eklendi (`add_obdex_standard_pids.py`) |
| 6 | NHTSA geri çağırma CSV'sinin üreticisi yoktu | — | Kural çıkarıldı, bayt-eşdeğer üretim `rebuild_csv_exports.py`'ye eklendi |
| 7 | `extended_pid` CSV aracı `.bak_t24` yedeği bırakıyordu | çalışma ağacı | Yedek silindi; doğrulayıcı `data/` altında artık dosyayı FAIL sayar |
| 8 | UDS DID anahtarları karışık yazım (`0x1153` / `0XF180`) | `uds_did_database.json` | Veri değiştirilmedi (mevcut okuyucu tolere ediyor); KB sayısal değere göre arar |
| 9 | 2 DTC anahtarı SAE J2012 dışı (`C6500`, `C6789`; başlıklar kırpık PDF parçası) | doğrulayıcı WARN | Kayıt sayısı testlerde sabit (14.484) olduğu için silinmedi; copilot bunları geçerli kod saymaz |
| 10 | 39 `fault_matrix` satırı FMI > 31 kullanıyor (FMI 5 bit; ulaşılamaz) — SITRAK katmanı | doğrulayıcı WARN | Raporlandı; KB 0–31 dışını reddeder |
| 11 | 7 kanonik semptom aday kodu hiçbir kayıtta yok (C1A00, C1564, B0413, B0423, C0755, C0760, C0765) | doğrulayıcı WARN | Silinmedi (bilgi kaybı olur); copilot bu kodlar için neden üretmez |

## 4. Çelişkiler (düzeltilmedi, karar gerektirir)

* **P0AA6 `measurement`** "UNECE R100: Min ≥ 500 Ω/V DC" diyor; `hv_safety_thresholds`
  HV-ISO-001'in R100 alıntısı DC için **100 Ω/V**, AC için 500 Ω/V. Copilot
  hesaplamada yalnız alıntılı HV-ISO-001'i kullanır; kayıt metni "kayıttaki
  referans (ölçüm değil)" etiketiyle gösterilir. Öneri: kayıt metni alıntıya göre düzeltilmeli.
* **HV-ISO-002** "7 MΩ @ 500 VDC" alıntısı R100 metninde doğrulanamadı; copilot kullanmaz.
* **NHTSA kategori etiketi**: ör. `21V922000` (fren balatası) "CAN-Bus & Ağ Haberleşme
  Arızaları" kategorisinde. Copilot kategoriyi kullanmaz; bileşen/özet metnine bakar.
* **FMI 22–30** adı veride "Data Erratic (Reserved)"; SAE J1939-73'te bu kodlar yalnız
  "ayrılmış". TR açıklama standarda göre yazıldı.
* `fault_matrix[*].oem_occurrences` içinde `T62 repair.diesellaptops.com harvest` gibi
  görev/kaynak etiketleri OEM adı yerine geçiyor (2.219 satır).
* `SPN_110.steps[1]` ve `[2]` neredeyse aynı metin; copilot yakın kopyaları tek gösterir.

## 5. Artıklar

| Dosya | Karar | Gerekçe |
|---|---|---|
| `root_cause_graph.json.t21_before` | **Silindi** | Hiçbir kod okumuyordu (türetici `root_cause_graph_seed.json` arar). 53 düğümün 38'i zaten grafta; 15'i kurtarılamadı (4'ü neden değil sinyal adı, 3'ü mevcut düğüm kopyası, 1'i yanlış kod eşlemesi — U0121'i tekerlek sensörüne bağlıyor —, diğerlerinin kaynak atfı çözülmüyor veya altın-kalibrasyonlu sıralamayı değiştirir). Düğüm başına karar: `quarantine/t21_seed_audit.json`. Git geçmişinden geri alınabilir. |
| `root_cause_graph_rejects.json` | Tutuldu | Türeticinin ret kaydı; testler okuyor (`test_t2_1_graph_depth_and_metric.py`) |
| `quarantine/*.json` (3 dosya) | Tutuldu | Karantina kanıt kayıtları; testler okuyor (`test_t2_3…`, `test_t2_5…`) |

## 6. Kapsam boşlukları

* **DTC**: 14.484 kod (P 9.292 / U 1.886 / B 1.846 / C 1.460). Türkçe `title_tr` dolu: yalnız
  **513**. 1.756 kodun ciddiyeti `UNKNOWN`. Kök neden grafı 5.589 DTC'yi kapsıyor (%39).
* **J1939**: 4.291 SPN, 11.146 SPN×FMI satırı (SPN başına ort. 2,6); grafta 1.590 SPN (%37).
  `associated_pgn` yalnız 1.006 SPN'de. Ağır vasıta OEM izleri büyük ölçüde Cummins/Detroit;
  Volvo (7), Caterpillar (6), Paccar (16), Mack (22) zayıf.
* **OEM (binek)**: 33 marka; Toyota (45), Mercedes (32), Hyundai/Kia (76) az; Avrupa ticari
  araç markaları (Scania, MAN, DAF, Iveco, Renault Trucks) OEM katmanında yok.
* **Telemetri eşikleri**: yalnız 7 sinyal (akü voltajı, yağ sıcaklığı, rail basıncı, DPF fark
  basıncı için kaynaklı eşik yok → copilot bu değerleri "eşik yok; yorumlanmadı" diye gösterir).
* **Graf kanıt sinyalleri**: 8.884 düğümün 2.717'sinde kanıt sinyali, yalnız 2'sinde çelişen sinyal.
* **Semptomlar**: 152 (ALL 76, binek 28, ağır vasıta 18, deniz 17, EV 13); binek hidrolik fren
  ve hidrolik direksiyon şikâyetleri yok.

## 7. Kalan işler (öneri)

1. DTC `title_tr` boşluğu (13.971 kod): kaynaklı/onaylı çeviri hattı gerekiyor.
2. Eksik telemetri eşikleri (akü, yağ sıcaklığı, rail, DPF) için kaynaklı değerler.
3. Grafa kalibrasyon koşusuyla birlikte P0335/P0420 vb. reddedilmiş gerçek nedenlerin yeniden alınması.
4. Avrupa ağır vasıta OEM kapsamı (lisansı açık kaynak bulunmalı).
5. Bölüm 4'teki çelişkilerin veri sahibince düzeltilmesi.

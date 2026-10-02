# PROVENANCE — data/diagnostics Kaynak ve Doğrulama Kaydı

> **Not (2026-09-30):** Bu dosyadaki `tools/data_ingest/*` komutları ve `staging/` yolları tarihsel yeniden üretim adımlarıdır; araçlar depodan kaldırılmıştır. Kanıt kaydı olarak korunur.

Bu klasördeki teşhis bilgi tabanlarının kaynak zinciri, doğrulama yöntemi ve
güncelleme geçmişi burada tutulur. `data/dbc/LICENSES.md` ile aynı disiplini
izler: içerik üretilmez, kamuya açık kaynaklar ve resmî belgeler referans alınır.

## Copilot upgrade — 2026-10-02 (yeni dosyalar + veri kalitesi düzeltmeleri)

Ayrıntılı rapor: `docs/audit/copilot_data_quality_2026-10-02.md`. Doğrulama:
`python scripts/validate_copilot_data.py` (CI'da, FAIL=0).

| Dosya | Değişiklik | Kaynak / yöntem | Lisans |
|---|---|---|---|
| `symptom_lexicon.json` (YENİ) | 25 kayıt, 186 TR/EN gündelik ifade + güvenlik yönlendirme terimleri | Yalnız mevcut `canonical_symptoms` kimliklerine işaret eder; her kayıtta `provenance` (curator, `internal_kb`). Üretici: `scripts/copilot_data/build_lexicon.py` | Proje içi |
| `signal_measurement_map.json` (YENİ) | 24 sinyal → SPN/PGN/OBD PID/birim/eşik anahtarı | SPN/PID alanları `j1939_spn_fmi_database.json` ve `extended_pid_database.json`'dan **kopyalanır**; doğrulayıcı adları yeniden eşler. Üretici: `build_signal_map.py` | Kaynak dosyaların lisansı |
| `copilot_glossary.json` (YENİ) | 32 terim, tek cümlelik TR/EN tanım | Curator; değer/limit/neden içermez. Üretici: `build_glossary.py` | Proje içi |
| `extended_pid_database.json` | 226 → **244**: T2-4'ün atladığı 18 standart Mode 01 PID | OBDex `data/pids/mode01.yaml` @ `bc58b0eb…`, sha256 `cc4c435f…` (yazmadan önce doğrulanır). `scripts/copilot_data/add_obdex_standard_pids.py` | CC0-1.0 |
| `dtc_database.json` | 303 kayıtta `procedures_full` metni adım listesine bölündü | Kendi "N." işaretleri (1..n kesintisiz); metin aynen. `fix_data_quality.py` | değişmedi |
| `j1939_spn_fmi_database.json` | FMI 13, 20–30 `description_tr` | Kaydın kendi SAE J1939-73 adının Türkçesi; `description_tr_source` alanında | değişmedi |
| `root_cause_graph.json` | 39 seed düğüme `system` | Kodun kayıttaki alt sistem etiketi → `system_taxonomy` | değişmedi |
| `hv_safety_thresholds.json` | provenance şema uyumu (`agent: legacy-unrecorded`, 2 `source.type` eşlemesi) | Değer ve alıntılar aynen | değişmedi |
| `root_cause_graph.json.t21_before` | **silindi** | Kullanılmayan seed artığı; karar kaydı `quarantine/t21_seed_audit.json` | — |

CSV ikizleri (7 dosya) artık tek kaynaktan üretilir: `python scripts/rebuild_csv_exports.py`
(`--verify` CI kontrolü). NHTSA geri çağırma CSV'si kuralı bu turda çıkarıldı ve bayt-eşdeğer doğrulandı.

## T2-4 — Dış veri kaynakları (licence-verified vendor merge)

> Kardeş dosyalar: `data/PROVENANCE.md` (merge tarafı, artefakt sha256'ları),
> `data/licenses/` (atıf + NOTICE metinleri), `tools/data_ingest/PROVENANCE.md`
> (ingest tarafı). Aşağıdaki tablo bu klasördeki kaynak zincirinin özetidir.

### Kaynak tablosu — 5 kayıt, her biri LICENSE dosyasından okunarak doğrulandı

| # | Kaynak | Lisans | Atıf zorunlu | Kaynak URL | Pinlenmiş commit SHA | sha256 (artefakt) |
|---|---|---|---|---|---|---|
| 1 | **OBDex** (generic DTC + Mode 01/09 PID) | **CC0-1.0** | Hayır | https://github.com/foerbsnavi/OBDex | `bc58b0eb7273226a1aabae98e956b70b8362bda1` | `a765cade770ffe756a5d4ea91c61fc128d5508f06f38552cd803dd726fecef63` (P0xxx) |
| 2 | **canboat** (`docs/canboat.json`, NMEA-2000 PGN) | **Apache-2.0** | **Evet** (NOTICE) | https://github.com/canboat/canboat | `f7f088b49d58f5b4a0feb9b29c288b0ae18a7880` | `b5a2c0c84b59af33caef583a372f9e763deb54ef4caf187825eff33d068735ba` |
| 3 | **dtc-database** (Wal33D, OEM katmanı) | **MIT** | **Evet** (telif) | https://github.com/Wal33D/dtc-database | `04c43d72e7db7197658b6f72fe582c5076d9eee8` | `099a4ffd60398112a0540b0bbc93a5929e05e7f4e6d4988ca2a50858af01b743` |
| 4 | **SITRAK error codes** (SPN/FMI ailesi) | **CC-BY-4.0** | **Evet** (atıf) | https://github.com/STAS63-bit/sitrak-error-codes | `fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742` | `69d726d69d5612eb890de0aa2579beef2220a2f2b371b8cbcd560e75f12840d3` |
| 5 | **canboat J1939 DM1** (`database/j1939/pgns/065226-activeTroubleCodes.yaml`) | **Apache-2.0** | **Evet** (NOTICE) | https://github.com/canboat/canboat | `f7f088b49d58f5b4a0feb9b29c288b0ae18a7880` | `2c821042983bd5c751794dba388f1a8121c57ecacd5d0ab5a9ec95b03ec197c9` |

Tüm staging artefaktlarının tam sha256 listesi: `data/PROVENANCE.md` §2.
Bağımsız decode doğrulaması (5 pass / 0 fail): `tools/data_ingest/verification.json`.

### SITRAK — CC BY 4.0 atıfı (YASAL ZORUNLULUK)

`j1939_spn_fmi_database.json` içinde SITRAK katmanı **3.399 SPN** kaydında
(lisans/shell fail-closed sonrası) şu atıf alanlarıyla işaretlidir:

| Alan | Değer |
|---|---|
| `sitrak_attribution` | `Источник: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)` |
| `_source_license_sitrak` | `CC-BY-4.0` |
| `_source_ref_sitrak` | `https://github.com/STAS63-bit/sitrak-error-codes/blob/fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742/error-codes.json` |
| `sitrak_fmi_family` | SPN başına FMI ailesi (7.981 FMI satırı) |
| `metadata.sitrak_layer.attribution` | `© МегаДата / megadata.pro (https://megadata.pro) — CC BY 4.0` |

**Atıf metni:** `МегаДата / megadata.pro` · **Kaynak:** https://megadata.pro
· **Lisans:** CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)

**Atıfın erişilebilir olduğu yerler (ürün about/licenses yüzeyi):**

| # | Konum | Biçim |
|---|---|---|
| 1 | `data/licenses/ATTRIBUTION.sitrak.md` | Kanonik, ürünle dağıtılan atıf dosyası (tam metin + kaynak + commit) |
| 2 | `data/licenses/NOTICE.canboat` | canboat Apache-2.0 NOTICE koruması |
| 3 | `data/licenses/ATTRIBUTION.obdex-and-dtcdb.md` | OBDex (CC0, yükümlülük yok) + Wal33D MIT telif metni |
| 4 | `data/PROVENANCE.md` | Merge tarafı kaynak tablosu (sha256 + url + lisans + commit) |
| 5 | `data/diagnostics/j1939_spn_fmi_database.json` | Her SITRAK kaydında makine-okur atıf alanları |
| 6 | Bu dosya, §T2-4 | Kaynak tablosu + atıf metni |

> **Dürüstlük notu (kapatılması gereken boşluk):** React/pywebview arayüzü
> (`src/ui/**`) başka bir ajan tarafından aktif düzenlenmektedir ve T2-4 görev
> kartı oraya yazmayı yasaklar. Ölçülerek doğrulandı: depoda hâlihazırda
> **veri kaynağı atıfı taşıyan bir about/licenses UI yüzeyi YOK** (`src/ui/**`
> içinde `megadata|SITRAK|canboat|OBDex|Apache` araması = 0 eşleşme; yalnız
> Ed25519 *bulut lisansı* kartı var). Bu nedenle atıf, **dağıtım-içeriği
> düzeyinde** zorunluluğu karşılar ve ürünle birlikte sevk edilir; ancak
> **render edilen bir UI panelinde** gösterilmesi `cockpit` sahipliğinde bir
> değişikliktir ve bu turda yapılmamıştır. Orchestrator'a yönlendirilmiştir.

## T2-4 merge kaydı — 2026-09-21 (katmanlı dış-veri merge + fail-closed düzeltme)

`tools/data_ingest/merge_staging_into_data.py` (idempotent, katmanlı, `.bak` alır)
+ `tools/data_ingest/rebuild_extended_pid_csv.py` (extended PID CSV ikizi).

**Ölçülen sonuçlar (iddia değil):**

| Hedef | Öncesi | Sonrası | Kaynak | Lisans |
|---|---|---|---|---|
| `dtc_database.json` | 14.352 | **14.469** | OBDex 9.533 generic (9.416 zaten vardı) | CC0-1.0 |
| `extended_pid_database.json` | 112 | **226** | OBDex Mode 01/09 (132; 18 zaten vardı) | CC0-1.0 |
| `j1939_spn_fmi_database.json` | 4.253 SPN | **4.253 SPN** / **3.399 sitrak etiketli** / 7.981 FMI | SITRAK 8.042 kayıt | CC-BY-4.0 |
| `dtc_database_oem_layer.json` | (yok) | **YENİ** 12.128 kod / 9.390 OEM / 33 üretici | Wal33D 18.805 satır | MIT |
| `canboat_pgn_reference.json` | (yok) | **YENİ** 628 N2K PGN + DM1 (65226) | canboat | Apache-2.0 |

**Katmanlılık kanıtı:** `--verify-idempotent` → "hicbir mevcut deger silinmedi/degismedi".
Byte-düzeyi idempotency: merge iki kez koşuldu, ikinci koşuda `added=0`,
`unchanged=true`, dosya hash'leri aynı. j1939 `db_keys_unchanged=true`.

**⚠️ Yakalanan uydurma ihlali (AGENTS.md §2.3) — araç fail-closed yapıldı.**
İlk taslak, SITRAK kaynağında DB'de karşılığı olmayan SPN'ler için
**13 uydurma (shell) kayıt** açıyordu (`{"spn": N}` + atıf alanları; `name`,
`title_tr`, `fault_matrix` YOK). Bu, "kaynak alan boşsa boş kalır, uydurma yok"
kuralının ihlaliydi. Araç düzeltildi:

- `assert_no_shell_rows()` — merge ÖNCESİ koşul: DB'de shell satır varsa **durur**.
- `canonical_spn_key()` — hex/uydurma SPN anahtarı reddeder (kaynakta
  `0x01`, `0x03` gibi 10 bozuk SPN + DB'de olmayan `SPN_139/140/141`).
- Gerçek kayıt yoksa **kayıt AÇILMAZ**; atlanır ve raporlanır
  (`skipped_no_real_record`, `skipped_detail`).
- Karantina kanıtı: `data/diagnostics/quarantine/t2_4_sitrak_shell_rows.json`
  (13 satır, `root_cause`, `recoverable_from`, `rows_removed=13`,
  `db_keys_before=4266 → after=4253`).

Sonuç: SPN anahtar sayısı merge boyunca **değişmedi** (4.253 → 4.253);
yalnız mevcut gerçek kayıtlara alter anahtar eklendi.

**Bilinen ve kabul edilen tek üst-veri farkı:** `extended_pid_database.json`
`metadata.total_pids` 112 → 226. Veri değil türev sayaçtır ve gerçek kayıt
sayısına eşitlenmek zorundadır, aksi halde `scripts/data_integrity_audit.py`
`metadata_totals` denetimi FAIL verir.

**Açık bulgu (bu turun ürünü DEĞİL, düzeltilmedi):**
`diagnostic_copilot.py:2079` falsy `pid` değerli PID kayıtlarını eler; bu
yan etki olarak **PID `0x00`** ("PIDs supported [01-20]", OBDex'te gerçek ve
standart) Mode 01 ve Mode 09 kayıtlarını da düşürür → yükleyici 226 yerine
223 PID görür. Filtrenin amacı CSV import'undan gelen yorum satırlarını
(`pid=""`) temizlemekti. `src/engine/ai/**` başka ajanın aktif alanı olduğu
ve görev kartı oraya yazmayı yasakladığı için dokunulmadı.

## CSV ikizleri (tüm tablolar) — bütünlük kaydı

### v-R1 — 2026-09-19 (CSV veri satırları geri yüklendi — sessiz veri kaybı onarımı)

**Bulgu (kanıt: git).** `f8bb892` ("feat(diagnostics): J1939/SAE J1939DA enrichment +
offline AI gap remediation") commit'i üç CSV ikizinin VERİ satırlarını boşalttı;
yalnız başlık kaldı:

| CSV | f80eb36 (önce) dolu satır | f8bb892 / HEAD (sonra) dolu satır |
|---|---|---|
| `dtc_database.csv` | 1.889 | **0** (14.166 boş satır) |
| `j1939_spn_fmi_database.csv` | 1.437 | **0** (3.710 boş satır) |
| `uds_did_database.csv` | 37 | **0** (68 boş satır) |

Boş satırlar doğru kolon sayısına sahip olduğu için hiçbir CSV okuyucu hata vermedi
(Excel'de "boş sayfa" olarak görünür — sessiz veri kaybı). `extended_pid_database.csv`
(113) ve `obd_mode06_database.csv` (248) bozulmadı. Bu boşalma dosyanın kendi başlığını
da değiştirmişti (`code,title,…` → `DTC Kodu,Başlık,…`); onarım mevcut başlığı korur.

**Onarım.** `scripts/rebuild_csv_exports.py` — CSV ikizleri JSON bilgi tabanlarından
yeniden üretilir; uydurma yok, her hücre JSON'daki bir alandan gelir:

- `dtc_database.csv` → 14.352 satır (kayıt ↔ satır 1:1), mevcut başlık korundu
  (`code,title,subsystem,severity,causes_count,steps_count`)
- `uds_did_database.csv` → 68 satır (1:1), mevcut başlık korundu
  (`did_hex,did_int,name,name_tr,oem,subsystem,unit,scaling,offset,byte_length`)
- `j1939_spn_fmi_database.csv` → 11.128 FMI satırı, üretici düzeni
  (`scripts/expand_j1939_spn_database.py`; kanıt başlık: git `f80eb36`:
  `SPN,FMI,Başlık (TR),Alt Sistem,PGN,FMI Tanımı,Öncelik,Saha Adımı`); PGN bilinmiyorsa
  `0 (TBD)` (eski dosya kuralı). `diagnostic_action` boş olan 6.231 satır boş kalır —
  kaynakta yok, doldurulmaz.

**Doğrulama (bağımsız ölçüm).** Rastgele 400/400 `dtc` satırı ve 68/68 `uds` satırı JSON
ile birebir; `j1939` satır sayısı `fault_matrix` toplamına eşit (11.128); hiçbir satırda
kolon sayısı sapması yok; tekrar koşum aynı md5 (idempotent); yazım atomik
(tmp + `os.replace`).

**Kalıcı kapı (yeni).**

- `scripts/data_integrity_audit.py` — tam denetim (JSON/metadata/CSV/DBC/schema/prosedür/kırpma),
  çıkış kodu FAIL bulgusu varsa `1`.
- `tests/unit/test_data_integrity.py` — 21 test: boş satır, satır sayısı = JSON kaydı,
  kolon genişliği, metadata toplamları, DBC varlık+sha256+boyut, Golden-Traces şema uyumu,
  yetim prosedür yok, kırpma & başlık boşluğu ratchet.
- Rapor: `docs/audit/data_integrity_2026-09-19.md`.

**Kural (yeni).** Bir bilgi tabanı güncellendiğinde CSV ikizi de aynı commit'te yeniden
üretilir; CI bu uyumu test eder. Ham çıktılar `spn_gap_hunter/output/` altında kalır,
`data/` yalnız küratörlü ve doğrulanmış içerik taşır.
## obd_mode06_database (JSON + CSV)

### v1.1.0 — 2026-09-12 (Tur-24: GM resmi Mode $06 tanımları, 20 → 38 monitor)

`CAN-DTC-Collector/spn_gap_hunter/mode06_harvester.py` hasadı. Kaynak: GM resmi
"Mode $06 data definitions" PDF'leri
(`https://gsi.ext.gm.com/gmspo/mode6/pdf/GM CAN mode $06 data final_dm.pdf`,
GMLAN; ücretsiz, robots engelsiz):

- **+18 yeni MID** (0x03/05/06/07 O2 bank serileri, 0x22/31 Catalyst bank 2,
  0x3C/3D EVAP purge/vent, 0x41-47 O2 heater ailesi, 0x71/72 SAI bank 2,
  0xAA-AD misfire extended) — monitor kimlikleri SAE J1979 bilinen MID
  aralıklarıyla etiketlendi; **+129 yeni TID testi** (aralık + çözünürlük
  alanlarıyla, ör. "Rich to Lean Sensor Threshold Voltage, 0.0000 to 7.990 V,
  0.122 mV/bit").
- Toplam 38 monitor / 193 test satırı. J1850/Class2 PDF'i (76 TID/CID kaydı)
  ham havuzda (`raw_mode06_tur24.json`); MID şemasına uymadığı için DB'ye
  girmedi, sonraki turda CID katmanı olarak değerlendirilecek.
- `name_tr` yeni kayıtlarda bilinçli olarak boş (çeviri ayrı tur).
- Ham kanıt: `C:/Users/canak/Desktop/UCANLAB-ARSIV/gm_mode06_raw/` (2 ana PDF + 69 model-yılı
  parametre dosyası + 20 Bmode6 PDF).

## uds_did_database (JSON + CSV)

### v1.1.0 — 2026-09-12 (Tur-24: ISO 14229 Annex F standart DID'leri, 36 → 68)

`CAN-DTC-Collector/spn_gap_hunter/uds_did_harvester.py` hasadı. Kaynak:
ISO 14229:2006 Annex F standart data-identifier kataloğu (python-udsoncan
`DataIdentifier` sınıfı üzerinden, MIT lisansı):

- **+32 standart DID** (0xF180-0xF19F): Boot/Application Software
  Identification, Active Diagnostic Session (0xF186), VIN (0xF190),
  ECU Serial Number, Programming Date, Calibration Date/Equipment ailesi.
  Tümü evrensel (OEM bağımsız) — `oem: "ISO 14229 (Universal)"`.
- VAG DSG MVB ölçüm grubu (G510, group 011.1) ham havuzda;
  DID şemasına uymadığı için DB'ye girmedi.
- `name_tr` yeni kayıtlarda bilinçli olarak boş (çeviri ayrı tur).

## dtc_database (JSON + CSV)

### v2.3.0 — 2026-09-15 (Tur-46: T45 DTC harvest'i paralel `_en` alanlarıyla birleştirme, 14.352 sabit)

`scripts/merge_t45_dtc.py` (T46-A, kanban t_edda7bef) birleştirmesi.

**KURAL — DİL AYRIMI.** DB'nin kendi `symptoms` / `causes` / `title` /
`description` alanları **Türkçe**'dir. T45 hasadı **İngilizce** olduğu için bu
alanlara yazılmadı; her İngilizce metin PARALEL bir `*_en` alanına konuldu ve
her alanın kaynağı `*_en_source` ile damgalandı. Çeviri yapılmadı (ayrı tur).
Üzerine yazma yok: yalnız boş/eksik alanlar dolduruldu; mevcut provenance
(`evidence_url` 4866, `source` 4866, `causes_source` 5049) dokunulmadan kaldı.

Kaynaklar (T45'te ücretsiz doğrulandı):

| Kaynak | Girdi | Katkı |
|---|---|---|
| troublecodes.net | `spn_gap_hunter/output/t45c_tcn_symptoms.json` (617 satır / 336 kod) | gerçek İngilizce semptom + neden + tanım |
| obdhut.com | `spn_gap_hunter/output/t45c_dtc_symptoms.json` (4.515 satır / 4.515 kod) | İngilizce neden + tanım (kaynakta `symptoms`=`causes` kopyasıydı, boşaltıldı) |

- **Zenginleşen alanlar:** `symptoms_en` **336**, `causes_en` **4.512**,
  `description_en` **4.512**, `title_en` **4.512** → 13.872 alan /
  **4.515 kayıt** (31,5% kapsam). `symptoms_en` yalnız troublecodes.net
  satırlarından geldi (obdhut semptomu güvenilmezdi).
- **Kayıt sayısı 14.352 → 14.352 (değişmedi).** Yeni kod açılmadı;
  bilinmeyen kod 0, kaynaksız kayıt 0 (atlanmadı). Kaynak URL'si olmayan satır
  birleştirilmez (kural).
- **Idempotent + deterministik:** script iki kez çalıştırıldı, DB SHA-256
  `7541c4659cd19e2cf4dc3e7539060e2dc91c4940a9923963f87dbe02d66ba9c0` her iki
  koşuda aynı; ikinci koşuda `fields_written=0`.
- Her zengineştirilen kayda `_t46_merge: "t46a"` işareti konuldu.
- **Doğrulama:** `tests/unit/test_t46_merge.py` → 14/14 PASS; tam suite
  1746 PASS (düşüş yok); `ruff check scripts/ tests/` temiz (T46 dosyaları).
  Merge öncesi 753 kayıtta `symptoms` zaten İngilizce (owner-complaint/Tur-25
  mirası, T45 hasadıyla örtüşmüyor) — T46 bunu ARTIRMADI (yeni ASCII-only
  semptom: **0**), kanıt `spn_gap_hunter/output/_t46_preexisting.py`.
- Yedek: `data/diagnostics/dtc_database.json.bak_t46_20260915_204830`
  (22.309.248 bayt, SHA-256 `5d6221ab734e178c3d3e307a68040fa80465209a380d8d08b60a44aa2727b51b`).

### v2.2.0 — 2026-09-12 (Tur-25: Wal33D MIT entegrasyonu + 721 hatalı başlık onarımı, 9.300 → 14.166)

`CAN-DTC-Collector/spn_gap_hunter/merge_tur25_wal33d.py` + `merge_tur25_dtc_titlefix.py`
birleştirmeleri. **Sıfır BrightData kredisi** — tümü ücretsiz kaynaklar.

**(A) Sistematik başlık hatası tespit edildi ve kısmen giderildi:**
Tur-3/4 toplu hasadında kod harfi kopyalanmamış; C/B/U kayıtlarının başlıkları
yanlışlıkla `"P#### OBD Code"` şablonuyla yazılmıştı (C=322, B=213, U=186 → **721 kayıt**).
Copilot bu kodları kullanıcıya yanlış harf + jenerik başlıkla sunuyordu.

- **281 başlık onarıldı** (%39): Wal33D generic (243), racetunefiles.com (38).
- **440 başlık kaynaksız kaldı** — SAE J2012'de rezerve/boşluk alanlar; komsulari
  Wal33D'de tanımlı olduğu halde bu kodlar hiçbir ücretsiz kaynakta yayınlanmamış
  (C0005, C0013, B0006, B0015 vb.). Doğrulandı: dtcsearch.com, racetunefiles.com,
  carobdcodes.com, obd-codes.com, troublecodes.net — hepsi 404/403.

**(B) Wal33D/dtc-database (MIT) entegrasyonu** — `@marshal` keşfi:
Kaynak: https://github.com/Wal33D/dtc-database (`data/dtc_codes.db`, MIT lisansı,
18.805 kayıt / 34 marka: 9.415 generic SAE J2012 + 9.390 üreticiye özel).

- **+4.866 yeni generic kod** (P:3.598, U:769, C:379, B:120) — DB 9.300 → 14.166 (+%52).
  Her kayıt: `title`, `subsystem` (harf bazlı), `severity`, `source`,
  `evidence_url`; `title_tr` bilinçli olarak boş (çeviri ayrı tur).
- **OEM katmanı ayrı dosyada** (`raw_wal33d_oem_layer_tur25.json`): 2.911 kod için
  9.390 üretici kaydı. Anahtar şemasını (P/B/C/U) bozmamak için ana DB'ye
  karıştırılmadı — HANDOFF_TUR24 §4.4 kuralı.
- Kanıt: `git clone` gerekmez; `raw.githubusercontent.com/.../dtc_codes.db`
  doğrudan indirilir (3.11 MB, 0 kredi).

**(C) Yan kaynaklar:**
- `dtcsearch.com`: C+U kodları için %100 doğru başlık (171 kayıt) — 440 kalanın
  çoğunu kapsamıyor (C1091+/U1000+ aralıkları sınırlı).
- `racetunefiles.com`: B kodlarında %20 kapsama (42 kayıt).
- `carobdcodes.com`: B1200+/C1091+/U1000+ aralıklarında çalışıyor, düşük kapsama.

**Doğrulama:** `load_external_dtc_database()` → 14.183 kayıt;
`tests/unit/test_ai_copilot.py` + `tests/unit/test_desktop_evidence.py` +
`tests/safety/` → 61/61 PASS. Yedekler: `dtc_database.json.bak_tur25_wal33d`,
`.bak_tur25`.

### v2.1.0 — 2026-09-12 (Tur-24: zayıf B/C/U kayıt onarımı, 901 → 846 jenerik başlık)

`CAN-DTC-Collector/spn_gap_hunter/merge_tur24_dtc.py` birleştirmesi.
Hedef: DB'de jenerik başlıklı ("OBD Code"/"DTC Code") 901 zayıf kayıttan
B0091-120 / C0106-180 / U0073-300 aralığındaki 156'sı.

- **55 başlık onarıldı** (racetunefiles.com DTC referansı; robots.txt AI-crawler
  açık, ücretsiz): SRS/restraints serisi (B0091-99 "Left Side Restraints
  Sensor 1-3", "Roll Over Sensor"), C0110/0131/0161 ABS ailesi, U0075-99
  ailesi, U0300 vb. 7 kayıt tam causes+symptoms zenginleşti.
- **8 başlık** obd-codes.com'dan (BrightData ile çekilen 156 sayfadan yalnız
  8'i yayında idi; 147'si 404 — kaynak bu kod kapsamını yazmamış, kredi israfı
  erken durdurma ile sınırlandı).
- **+2 VAG semptom katmanı**: ross-tech wiki 400 fault-code sayfası hasat
  edildi (397 kayıt, ücretsiz); 4'ü DB `vag_code_5digit` ile eşleşti, 2 P-kod
  (P0121→16505, P3054→19510) semptom/solutions ile zenginleşti. Kalan 393
  VAG kodu ham havuzda (`raw_rosstech_vag_tur24.json`) — P/B/C/U anahtar
  şemasını bozmamak için yeni kayıt olarak eklenmedi, eşleme turu ayrı.
- Başlıklarda kod öneki temizlendi (118 kayıt: "B0091 Active switch..." →
  "Active switch...").
- CSV şemaya uygun yeniden üretildi (9300 satır).

### v1.4.0 — 2026-09-11 (VAG fabrika kodu eşleme katmanı)

vag-hub.com/vw-error-codes (2.102 satır, ücretsiz) tablosundan 5 haneli VAG
fabrika kodu ↔ P-kod eşlemesi mevcut kayıtlara `vag_code_5digit` +
`vag_desc_en` alanları olarak eklendi (2.102 DTC zenginleştirmesi). Kaynak:
VAG-hub public list; P-kodları zaten DB kaynak kümesiyle örtüşüyor.

### v1.3.0 — 2026-09-06 (Üreticiye Özel P1 Serisi Kodlar Eklendi, 1816 → 1840 kod)

Ford, GM, Volkswagen/Audi (VAG), Toyota/Lexus ve BMW platformlarına ait 24 adet kritik üretici P1 kodu (`P1000`, `P1130`, `P1131`, `P1151`, `P1260 PATS İmmobilizer`, `P1345 CKP-CMP Korelasyonu`, `P1349 VVT`, `P1450 EVAP Purge`, `P1516 TAC`, `P1602 Terminal 30`, `P1682 Kontak Voltajı`, `P1744 TCC` vb.) fabrika servis kılavuzları ve resmi OBD standartları referans alınarak entegre edildi. Her kod için OEM multimetre toleransları, UDS rutinleri ve 4 aşamalı saha teşhis rehberi eklendi.

## j1939_spn_fmi_database (JSON + CSV)

### v1.7.0 — 2026-09-11 (Tur-23 gap merge: 641 → 865 SPN)

`CAN-DTC-Collector/spn_gap_hunter/merge_tur23_j1939.py` birleştirmesi (kullanıcı
onayıyla). İki yeni ücretsiz kaynak + BrightData aşılan bir kaynak:

- **+192 SPN** (`source: "tur23_j1939hub"`): j1939hub.com sitemap'ında sayfası
  olup ana DB'de bulunmayan SPN'ler; her SPN için temsili FMI sayfası (FMI-0
  tercihli) çekildi. İsim otoritesi: SS 1033423 (102'sinde resmî isim);
  `evidence_url` alanı sayfa kaynağını taşır. Description, sayfadaki
  "indicates..." tanım cümlesinden alındı.
- **+30 SPN** (`source: "tur23_spnfmi"`) + **2 SPN** (`tur23_hub+spnfmi`):
  spnfmi.com Cummins (1440 satır) + Detroit (162 satır) fault-code tabloları,
  BrightData Scraping Browser ile aşıldı (4 kredi). FMI haritaları
  `lamp`/`cummins_desc` zenginleştirmeli. 28'i SS 1033423 resmî isimli.
- PGN: 3-OEM seti (`raw_3oem_j1939_pgn.json`, Mack+Volvo+Peterbilt) eşleşen
  kayıtlarda `pgn_cross_oem_confirmed`/`pgn_oem_count` alanları eklendi.
- `title_tr` yeni kayıtlarda bilinçli olarak boş (HANDOFF 5.4: çeviri ayrı tur).
- CSV şemaya uygun yeniden üretildi.

### v1.3.0 — 2026-09-06 (OEM tablo hasadı birleştirmesi: 87 → 423 SPN)

`CAN-DTC-Collector/spn_gap_hunter` ofisinin `oem_table_harvester.py` çıktısı
birleştirildi (kullanıcı onayıyla). Kaynak: DieselLaptops'un 97 OEM arıza-kodu
tablo sayfası (Detroit DD13/15/16 tüm yıl aralıkları, Cummins ISX15/B6.7,
PACCAR MX-13, Volvo, Mack...) — **26.434 SPN/FMI satırı, 618 benzersiz SPN,
tamamı ücretsiz erişim (0 BrightData kredisi)**.

- **+336 yeni SPN** (Tier A: 8/8 motor ailesi tarafından doğrulanmış adaylar).
  Her kayıt: konsensüs isim (çoklu-OEM oy birliği), `oem_field_evidence`
  (aile sayısı + FMI bazlı saha anlamları), fmi_definitions'a bağlı arıza
  matrisi. Türkçe başlıklar kural-tabanlı sözlükle üretildi (`tr_quality`
  notu: tam çeviri için ayrı dil denetimi turu önerilir).
- **64 mevcut SPN** için `oem_field_evidence` zenginleştirmesi (OEM saha
  anlamları FMI bazında).
- PGN ataması: 15 SPN canboat YAML eşleşmesiyle; geri kalanlarda
  `pgn_acronym="TBD"` (özel PGN geçişi bekliyor).
- Etiket düzeltmeleri konsensüsle teyit: 3480 = Fuel Compensation Pressure,
  3482 = AFT Fuel Shutoff Valve 1, 3483 = AFT Regeneration Status,
  3471 = Hydrocarbon Doser Circuit (önceki turdaki J1939DA beklenti
  etiketleriyle çeliştiği için bu SPN'ler merge EDİLMEDİ, yalnızca mevcut
  DB'ye girenler alındı).

### v1.2.0 — 2026-09-06 (5 mevcut kayıtta PGN düzeltmesi)

İlk 53 kaydın SPN↔PGN etiketleri, canboat `database/j1939/pgns` YAML'ları ve
bu depodaki `data/dbc/heavy_duty/j1939_canboat.dbc` (aynı upstream'in DBC
dönüşümü — canlı çözücü davranışının aynası) ile satır satır çapraz kontrol
edildi. Düzeltilenler:

| SPN | Eski | Yeni | Kanıt |
|---|---|---|---|
| 92 Engine Percent Load | 61444 (EEC1) | **61443 (EEC2)** | canboat YAML + depo içi DBC: sinyal PGN 61443 bit 16'da |
| 105 Intake Manifold Temp | 65270 (ET1) | **65270 (IC1)** | PGN numarası doğruydu; kısaltma aynı PGN'deki 102/106/107/173 ile uyumsuzdu |
| 157 Metering Rail 1 Press. | 65271 (FED1) | **65243 (EFL_P2)** | canboat YAML + ISOBUS SPN dokümanı: 156/157/164 aynı mesajda; 65271 zaten VEP1 (SPN 168) |
| 3246 DPF Outlet Gas Temp | 64948 (A1D1) | **64947 (A1D2)** | canboat YAML + DBC: 64948=giriş çifti (3241/3242), 64947=çıkış çifti (3245/3246) |
| 3251 DPF Diff. Pressure | 64948 (A1D1) | **64946 (A1D3)** | canboat YAML + DBC: 3251, 64946 bit 32'de (3250 ile aynı mesaj) |

**Bilinçli olarak korununanlar:** SPN 3242/3246'nın adları "…Intake/Outlet Gas
**Temperature**" olarak bırakıldı — canboat bu SPN'lere "Pressure 2" dese de
[Scania DM1 dokümanı](https://www.scania.com), [Cummins 3316/3255 çapraz
referansı](https://otrperformance.com/blogs/quick-tips/cummins-fault-code-3255-spn-3246-fmi-0-or-16-a-critical-dpf-sensor-issue)
ve Detroit Diesel saha kodları sıcaklığı teyit ediyor (canboat adlandırması
bu iki SPN'de sapıtıyor). SPN 1087/1088 (AIR1), 641 (VGT1), 1072 (EBC1),
651-656 (FED2), 628-630 (SFT) gibi canboat j1939 klasöründe bulunmayan
kayıtların etiketlerine bağımsız kaynak bulunamadığı için dokunulmadı.

### v1.1.0 — 2026-09-06 (34 yeni SPN eklendi, 53 → 87)

Eklenecek SPN'ler `canboat/canboat` deposunun `database/j1939/pgns/*.yaml`
dosyalarından taranarak seçildi (mevcut tabloda olmayanlar); her aday ikinci bir
bağımsız resmî kaynakla çapraz doğrulandı:

| Kaynak | Lisans / Durum | Katkısı |
|---|---|---|
| [canboat/canboat — database/j1939/pgns](https://github.com/canboat/canboat/tree/master/database/j1939/pgns) | Apache-2.0 | SPN↔PGN eşleşmesi, alan adları, çözünürlük/offset (ölçekleme) |
| [NHTSA TSB MC-10141869 (SS 1033423, "J-1939 Fault Code Source Address")](https://static.nhtsa.gov/odi/tsbs/2018/MC-10141869-9999.pdf) | ABD resmî belgesi | Resmî SPN ad çapraz doğrulaması (ör. SPN 164 "Engine Fuel Injection Control Pressure") |
| [DEIF J1939 measurements dokümanı](https://documentation.deif.com/r/ie-150-agc-150-engine-communication-4189341302-uk/generic-j1939/general/j1939-measurements) | Üretici dokümanı | SPN 173 ölçekleme teyidi (0.03125 °C/bit, −273 °C offset, PGN 65270) |
| [Detroit Diesel EOBD raporları (vanderhaags.com)](https://pix.vanderhaags.com/original/engine-assembly-dd15-detroit-86492157c2.pdf) | Saha kaydı | SPN 164/3563 gerçek saha FMI örnekleri |
| ISO 11783-11 / SAE J1939-71 yaygın ölçekleme tabloları | Standart fact'leri | Sıcaklık/basınç bit kodlamaları (TEMPERATURE_UFIX16_J1939 vb.) |

**Doğrulanmayan / reddedilen adaylar:** SPN 3241/3245/5862 gibi "preliminary
FMI" türevleri iki kaynak arasında ad çelişkisi taşıdığı için eklenmedi;
SPN 102/105/157/108 gibi mevcut kayıtların PGN etiketlerine dokunulmadı.
Türkçe başlık, alt sistem, arıza matrisi ve saha adımları bu depoda yazılmıştır
(mevcut kayıtların editoryal stilini izler) ve kaynak lisanslarından
etkilenmez; canboat verisi Apache-2.0 atfıyla kullanılır.

### v1.0.0 — ilk yayın (53 SPN + 20 FMI tanımı)

İlk küratörlük kaydı bu depo geçmişinde bulunur.

## nhtsa_can_recalls_database (JSON + CSV)

### v1.0.0 — 2026-09-06 (İlk Yayın — 282 Tekil Güvenlik Geri Çağırma & TSB Kampanyası)

- **Kaynak:** ABD Ulusal Karayolu Trafik Güvenliği İdaresi (NHTSA) Açık API (`https://api.nhtsa.gov/recalls/recallsByVehicle`).
- **Lisans / Durum:** ABD Federal Hükümeti Resmî Kamu Verisi (Public Domain / US Gov Open Data).
- **Kapsam:** 2020–2024 model yılları arasındaki 82 modern araç platformu (Ford F-150/Mach-E/Explorer, Tesla Model 3/Y/S/X, GM Silverado/Bolt, Toyota RAV4/Prius, VW ID.4, BMW 3-Serisi/i4/iX, Hyundai Ioniq 5, Kia EV6, Jeep Wrangler vb.).
- **Filtreleme & Küratörlük:** 708 toplam kampanya taranarak CAN-Bus iletişim kaybı, Central Gateway (CGW), BCM, Yüksek Voltaj Batarya / BMS / Kontaktör / Pyrofuse, Elektronik Fren / Direksiyon ve OTA (Uzaktan Yazılım Güncelleme) ile ilişkili 282 tekil güvenlik bülteni seçildi ve kategorize edildi.

## Diğer tablolar

- `obd_mode06_database.json/csv` — SAE J1979 Mode $06 izleyici tablosu (MIDs, TIDs, CIDs).
- `uds_did_database.json/csv` — ISO 14229 DID kataloğu (standart 0xF1xx + OEM VAG, BMW, Ford, Tesla).
- `nhtsa_can_complaints_database.json` — NHTSA complaints API (api.nhtsa.gov,
  ABD resmî kamu verisi): 50 araç platformu, 4.388 CAN/elektrik/yazılım ilgili
  şikâyet (ODI numaralı, ham kanıt niteliğinde; Copilot henüz okumuyor).

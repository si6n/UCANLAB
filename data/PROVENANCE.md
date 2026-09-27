# PROVENANCE — `data/` dış-veri katmanı (T2-4)

Bu dosya, `data/` altındaki bilgi tabanlarının **dış kaynaklı katmanlarını**
belgeler: her artefakt için `sha256` + `source_url` + `license` + `commit SHA`.

T1-3 (`tools/data_ingest/PROVENANCE.md`) verinin **indirilmesini/doğrulanmasını**
kaydeder; bu dosya verinin **`data/`'ya merge edilmesini** kaydeder. İkisi
birbirini tamamlar, biri diğerinin yerine geçmez.

- Ingest tarafı: `tools/data_ingest/PROVENANCE.md`, `tools/data_ingest/provenance.json`
- Bağımsız decode kanıtı: `tools/data_ingest/verification.json` (**5 pass / 0 fail**)
- Merge aracı: `tools/data_ingest/merge_staging_into_data.py` (idempotent, katmanlı)
- Lisans/atıf dosyaları: `data/licenses/`

> **Runtime tamamen çevrimdışıdır.** Bu veri **build-time vendor** edilmiştir;
> ürün çalışırken yalnız diskten okur. Hiçbir LLM/bulut/ağ çağrısı yoktur.
> Merge script'leri `src/engine/ai/` altına **yazmaz** (AI-TX izolasyonu korunur).

---

## 1. Kaynaklar ve lisansları (LICENSE dosyasından okunarak doğrulandı)

| Kaynak | Repo | Pinlenmiş commit | Lisans (SPDX) | Atıf zorunlu | Kanıt dosyası |
|---|---|---|---|---|---|
| OBDex | `foerbsnavi/OBDex` | `bc58b0eb7273226a1aabae98e956b70b8362bda1` | **CC0-1.0** | Hayır | `licenses/LICENSE-DATA.obdex` |
| CANboat | `canboat/canboat` | `f7f088b49d58f5b4a0feb9b29c288b0ae18a7880` | **Apache-2.0** | **Evet** (NOTICE) | `licenses/LICENSE.canboat` |
| dtc-database | `Wal33D/dtc-database` | `04c43d72e7db7197658b6f72fe582c5076d9eee8` | **MIT** | **Evet** (telif) | `licenses/LICENSE.dtc-database` |
| SITRAK error codes | `STAS63-bit/sitrak-error-codes` | `fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742` | **CC-BY-4.0** | **Evet** (atıf) | `licenses/LICENSE.sitrak` |

Lisans metinleri `tools/data_ingest/licenses/` altında korunur (sha256'ları
`tools/data_ingest/PROVENANCE.md`'de). Atıf uygulaması: `data/licenses/`.

---

## 2. Staging artefaktları (kaynaktan indirilen ham dosyalar)

Tümü `tools/data_ingest/staging/` altında; `sha256` değerleri indirme anında
hesaplandı ve `tools/data_ingest/provenance.json` + `verification.json` ile
bağımsız doğrulandı.

| Artefakt | Kaynak URL | Boyut | sha256 | Lisans |
|---|---|---|---|---|
| `obdex/data/generic/B0xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/B0xxx_enriched.yaml) | 445,985 | `73d317fb49b01a369ee29126bb0bb4f31c4775a5fe0a7e7fae1456aa980d3bc4` | CC0-1.0 |
| `obdex/data/generic/C0xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/C0xxx_enriched.yaml) | 1,041,438 | `1d60a394ff9cfde96b6e7a738420ac05dd48b85ebfe798001c38310aba7073e6` | CC0-1.0 |
| `obdex/data/generic/P0xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/P0xxx_enriched.yaml) | 6,206,241 | `a765cade770ffe756a5d4ea91c61fc128d5508f06f38552cd803dd726fecef63` | CC0-1.0 |
| `obdex/data/generic/P2xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/P2xxx_enriched.yaml) | 5,750,928 | `eda26317419f7e897c13eb254f74a01d905f180c7006174298d08d665b42dc4a` | CC0-1.0 |
| `obdex/data/generic/P3xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/P3xxx_enriched.yaml) | 255,238 | `bba19fe7dddb757866632ae939e4ace7dbbbe639816d3a46ce3a6936053708a3` | CC0-1.0 |
| `obdex/data/generic/U0xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/U0xxx_enriched.yaml) | 2,228,894 | `60aebde267b4bae53902e0abfeb7b489e314cd9e2ecdf0eb62384ea6b5a56afc` | CC0-1.0 |
| `obdex/data/generic/U3xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/U3xxx_enriched.yaml) | 370,022 | `f8a4b3723140b059216bad1d5fd450ce067c9c6bb8361f4120bbd422ecc769b7` | CC0-1.0 |
| `obdex/data/pids/mode01.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/pids/mode01.yaml) | 32,408 | `cc4c435fe5ce8af2ff5b9b084dd9a96c32c8257d2ac8116e819281cb06fa367a` | CC0-1.0 |
| `obdex/data/pids/mode09.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/pids/mode09.yaml) | 2,893 | `aaf0aa7041f8f1f81cfc0a7dfd1c3dab62dda68ca43c64d46357a44a04726233` | CC0-1.0 |
| `canboat/canboat.json` | [link](https://raw.githubusercontent.com/canboat/canboat/f7f088b49d58f5b4a0feb9b29c288b0ae18a7880/docs/canboat.json) | 2,664,633 | `b5a2c0c84b59af33caef583a372f9e763deb54ef4caf187825eff33d068735ba` | Apache-2.0 |
| `canboat/j1939_065226-activeTroubleCodes.yaml` | [link](https://raw.githubusercontent.com/canboat/canboat/f7f088b49d58f5b4a0feb9b29c288b0ae18a7880/database/j1939/pgns/065226-activeTroubleCodes.yaml) | 1,421 | `2c821042983bd5c751794dba388f1a8121c57ecacd5d0ab5a9ec95b03ec197c9` | Apache-2.0 |
| `dtcdb/dtc_codes.db` | [link](https://raw.githubusercontent.com/Wal33D/dtc-database/04c43d72e7db7197658b6f72fe582c5076d9eee8/data/dtc_codes.db) | 3,256,320 | `099a4ffd60398112a0540b0bbc93a5929e05e7f4e6d4988ca2a50858af01b743` | MIT |
| `sitrak/sitrak_error-codes.json` | [link](https://raw.githubusercontent.com/STAS63-bit/sitrak-error-codes/fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742/error-codes.json) | 1,962,001 | `69d726d69d5612eb890de0aa2579beef2220a2f2b371b8cbcd560e75f12840d3` | CC-BY-4.0 |

**Toplam: 24,218,422 bayt / 13 veri artefaktı.** (Lisans dosyaları ayrıca 5 adet.)

---

## 3. Merge edilen hedefler (T2-4 çıktıları)

Her hedef: ne eklendi, hangi lisans, hangi kaynak referansı, kaç kayıt.

### 3.1 `data/diagnostics/dtc_database.json` — OBDex generic DTC katmanı

| Alan | Değer |
|---|---|
| Eklenti türü | **YENİ KAYIT** (mevcut 14.352 kayıt dokunulmadı) |
| Öncesi → Sonrası | 14.352 → **14.469** kayıt (**+117**) |
| Kaynak kayıt | 9.533 OBDex generic DTC; 9.416'sı zaten mevcuttu (atlandı, ezilmedi) |
| Lisans | CC0-1.0 |
| `_source_ref` | `https://github.com/foerbsnavi/OBDex/blob/bc58b0eb7273226a1aabae98e956b70b8362bda1` |
| Kayıt başına etiket | `_source_license` + `_source_ref` (117/117 kayıtta doğrulandı) |

Dosya düzeyinde yeni kayıtlar kendi seviyesinde etiketlenir; bunun yanında her
yeni kayda `obdex` alt-nesnesi (kategori, `affected_components`, `common_causes`,
`symptoms`, `repair`, `flags`, Almanca başlık/açıklama) eklenir. Kaynakta
**olmayan** alanlar uydurulmaz (`steps` boş kalır — OBDex'te saha adımı yoktur).

### 3.2 `data/diagnostics/extended_pid_database.json` — OBDex Mode 01/09 PID katmanı

| Alan | Değer |
|---|---|
| Eklenti türü | **YENİ KAYIT** (mevcut 112 PID dokunulmadı) |
| Öncesi → Sonrası | 112 → **226** PID (+114); kaynak 132 → 18'i zaten mevcuttu |
| Lisans | CC0-1.0 |
| `_source_ref` | OBDex pinli commit URL |
| Kayıt başına etiket | `_source_license` + `_source_ref` (114/114 doğrulandı) |
| `metadata` | `total_pids` 226'ya güncellendi; `sources`'a `obdex` eklendi; `obdex_layer` bloğu eklendi |

> **Not (dürüstlük):** `_source_license`/`_source_ref` alanları yalnız **T2-4'te
> eklenen** PID kayıtlarında bulunur (114 adet). Önceden var olan 112 kayıt
> olduğu gibi bırakılmıştır — geriye dönük etiketleme yapılmadı, çünkü o
> kayıtların kaynağı `curated_expert`/`torque_csv`'dir ve bu turun kapsamı
> dışındadır. Etiketsiz 2 kayıt PID filtresiyle elenen satırlardır (§4.2).

### 3.3 `data/diagnostics/dtc_database_oem_layer.json` — **YENİ DOSYA** (Wal33D MIT)

| Alan | Değer |
|---|---|
| Eklenti türü | **YENİ DOSYA** (komşu dosya; mevcut anahtar şeması bozulmadı) |
| İçerik | 18.805 kaynak satır → **12.128 kod**, **2.911 kod için 9.390 OEM kaydı**, 33 üretici |
| Lisans | MIT |
| `_source_ref` | `https://github.com/Wal33D/dtc-database/blob/04c43d72e7db7197658b6f72fe582c5076d9eee8/data/dtc_codes.db` |
| `_source_license` | Her kod kaydında (12.128/12.128) |

Ana DB'nin `P/B/C/U` anahtar şemasını bozmamak için OEM katmanı ayrı dosyada
tutuldu (T25 / HANDOFF_TUR24 §4.4 kuralının aynısı — `data/diagnostics/PROVENANCE.md`).

### 3.4 `data/diagnostics/canboat_pgn_reference.json` — **YENİ DOSYA** (canboat Apache-2.0)

| Alan | Değer |
|---|---|
| Eklenti türü | **YENİ DOSYA** (mevcut DBC/katalog dosyalarına yazılmadı) |
| NMEA-2000 PGN | **628 kayıt** (PGN aralığı 59.392–131.012; 348 farklı PGN numarası) |
| J1939 DM1 | PGN **65226** (Active Trouble Codes) alan düzeni |
| Lisans | Apache-2.0 |
| `_source_ref` | canboat pinli commit URL |
| Kayıt başına etiket | `_source_license` + `_source_ref` (629/629 kayıtta) |

> **Şema tuzağı (belgelenmiş):** canboat'ın JSON tarafı **PascalCase**
> (`PGN`/`Fields`/`Id`), J1939 YAML tarafı **küçük harf** (`pgn`/`fields`).
> Yanlış parser sessizce 0 kayıt okur. Burada iki format ayrı ayrı işlenir.
>
> **Düzeltilen tesis hatası:** canboat.json'da bir PGN numarası **tekil
> değildir** (aynı PGN için birden çok varyant, ör. PGN 59392 `Fallback: true`).
> PGN'i sözlük anahtarı yapmak 628 kaydı sessizce **348'e** düşürüyordu. Bu
> yüzden hem kayıpsız liste hem `pgns_by_number` indeks haritası yazılır.

### 3.5 `data/diagnostics/j1939_spn_fmi_database.json` — SITRAK CC-BY-4.0 katmanı

| Alan | Değer |
|---|---|
| Eklenti türü | **YENİ ALAN** (mevcut tüm alanlar korundu) |
| Öncesi → Sonrası | 4.253 → **4.266** SPN kaydı; **3.412 SPN'de `sitrak_fmi_family`**, **7.981 FMI satırı** |
| Kaynak | 8.042 SITRAK kaydı (SPN+FMI), 44 sistem |
| Lisans | **CC-BY-4.0** |
| `_source_ref` | SITRAK pinli commit URL |
| Etiket | `_source_license_sitrak` + `_source_ref_sitrak` + `sitrak_attribution` (3.412/3.412) |

Eklenen **yalnız yeni** anahtarlar: `sitrak_fmi_family`, `sitrak_systems`,
`_source_license_sitrak`, `_source_ref_sitrak`, `sitrak_attribution`.
`fault_matrix` (11.128 satır), `causes`, `steps`, `procedures_full` alanlarına
**dokunulmadı** — `_source_license` gibi düz anahtarlar bu dosyanın kendi
şemasıyla çakışmasın diye `_source_license_sitrak` adı kullanıldı.

**CC BY 4.0 atıfı:** `МегаДата / megadata.pro` — ayrıntı `data/licenses/ATTRIBUTION.sitrak.md`.

---

## 4. Katmanlılık ve idempotency kanıtı

### 4.1 Hiçbir mevcut değer ezilmedi (ölçüldü)

`python tools/data_ingest/merge_staging_into_data.py --verify-idempotent`
her hedefi `.bak_t24` (merge öncesi) ile **yapısal** karşılaştırır: silinen
anahtar yok, değişen skaler yok.

Bilinen ve kabul edilen tek fark: `extended_pid_database.json.metadata.total_pids`
112 → 226. Bu **bilinçli** bir düzeltmedir: üst veri sayacı gerçek kayıt sayısına
eşitlenmiştir; aksi halde `scripts/data_integrity_audit.py` `metadata_totals`
denetimi FAIL verirdi (bkz. `scripts/data_integrity_audit.py:92`, kriter
`len(d["pids"])`). Sayaç, veri değil türev üst veridir.

### 4.2 Ölçülen entegrasyon kanıtı (yükleyiciler)

Üretim yükleyicileri çağrılarak ölçüldü (iddia değil):

| Yükleyici | Ölçüm |
|---|---|
| `catalog_size(force_reload=True)` | **14.469** |
| `load_external_dtc_database()` | **+14.447** kayıt eklendi (34 yerleşik kural korundu → 14.481) |
| `get_j1939_spn_database()` | **4.266** SPN; `sitrak_fmi_family` taşıyan **3.412** |
| `get_extended_pid_database()` | **223** PID (226 − 3 filtrelenen); bunlardan **112** `data_source="obdex"` |

**Dürüstlük notları (gizlenmedi):**

1. `load_external_dtc_database()` **5 kaydı** şekil-dışı/karantina nedeniyle
   reddetti (mevcut fail-closed kapısı). Bu beklenen davranıştır — T2-3'ün
   kurduğu kapı korunmuştur, gevşetilmedi.
2. `get_extended_pid_database()` 3 kaydı eler çünkü
   `diagnostic_copilot.py:2079` falsy `pid` değerli kayıtları filtreler.
   Bunlardan **2'si benim eklediğim PID `0x00`** kaydıdır (Mode 01 ve Mode 09
   "PIDs supported [01-20]"). **PID `0x00` kaynakta gerçek ve standart bir
   PID'dir**; filtrenin amacı CSV import'undan gelen yorum satırlarını
   (`pid=""`) temizlemekti ve `0` değerini yan etki olarak eliyor.
   → **Bu bir ön-koşul hatasıdır, benim merge'ümün ürünü değil.**
   `src/engine/ai/**` başka bir ajan tarafından aktif olarak düzenlendiği ve
   görev kartım o dizine yazmayı yasakladığı için **düzeltilmedi**; bulgu
   olduğu gibi raporlanır.
3. Eski 112 PID kaydında `_source_license` yoktur (bu turun kapsamı dışı,
   bkz. §3.2 notu).

---

## 5. Reddedilen kaynaklar (bu turda da dokunulmadı)

T1-3'ün reddettiği kaynak listesi aynen yürürlüktedir; T2-4 bunların hiçbirine
dokunmadı (gerekçeler `tools/data_ingest/PROVENANCE.md` §"Rejected sources"):

SAE J2012/J1979/J1939-71/J1939-73 standart metinleri · ISO 14229-1 / ISO 15031-5 /
ISO 15765-2 · OEM TSB gövdeleri (Cummins/CAT/Scania/Volvo/Detroit/Mercedes/
PACCAR) · AllData / Mitchell 1 / Identifix / Snap-on / Autodata · iATN ve
herkese açık forumlar · Wikipedia OBD-II PID ve DTC tabloları (CC BY-SA viral) ·
lisanssız depolar (alperunlu/DTCparser, f-steff/…, digitalyacht/…, linux-can/
can-utils) · NMEA 2000/0183 standart belgeleri · UDS DID / Mode 06 veri
tabloları (açık yeniden-dağıtılabilir kaynak YOK — uydurulmaz).

---

## 6. Yeniden üretme

```bash
# 1) staging'den data/'ya katmanlı merge (idempotent, .bak alır)
python tools/data_ingest/merge_staging_into_data.py --apply

# 2) katmanlılık denetimi (mevcut değer ezilmedi mi?)
python tools/data_ingest/merge_staging_into_data.py --verify-idempotent

# 3) türev CSV ikizleri
python scripts/rebuild_csv_exports.py                       # dtc, uds, j1939
python tools/data_ingest/rebuild_extended_pid_csv.py        # extended_pid

# 4) kapılar
python scripts/data_integrity_audit.py                      # FAIL=0 beklenir
python -m pytest tests/unit/test_data_integrity.py -q        # 21 passed beklenir
```

Ağ erişimi **yalnız** `tools/data_ingest/fetch_sources.py` içindedir (build-time
ingest). Bu dokümandaki hiçbir merge/denetim adımı ağ kullanmaz.

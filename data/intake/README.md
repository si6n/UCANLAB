# INTAKE — ham teşhis verisi kabul durağı (Copilot intake)

`data/intake/` **bilgi tabanı değildir.** Buradaki hiçbir dosya
KnowledgeBase'e bağlanmaz: copilot yükleyicileri
(`src/engine/ai/diagnostic_copilot.py`, `src/engine/ai/golden_cases.py`) bu
dizini okumaz, `scripts/validate_intake.py` ise buraya **yazmaz** — yalnızca
`data/diagnostics` ve `data/golden_traces` ile karşılaştırıp **raporlar**.

Bu dizin, atölyeden gelen ham DTC / SPN-FMI / trace / vaka / OEM notunu
kaynak+lisans kanıtıyla *toplamak* içindir. Terfi (promotion) elle ve gözden
geçirmeyle olur; aşağıdaki "Entegrasyon adımları" bölümü tek yol haritasıdır.

> Kalıcı kayıtların kanıt zinciri `data/PROVENANCE.md` ve
> `data/diagnostics/PROVENANCE.md` içindedir. `data/intake/` o iki dosyanın
> **yerine geçmez**; sadece kaynak notlarının toplandığı ilk duraktır.

## Dizin yapısı

| Yol | İçerik | Dosya biçimi |
|---|---|---|
| `dtc/` | ham DTC başvurusu | `<intake-id>.json` (zarf + `payload`) |
| `spn_fmi/` | ham SPN/FMI çifti | `<intake-id>.json` |
| `pgn/` | PGN alan düzeni (J1939/NMEA-2000) — `pgn_layout` | `<intake-id>.json` |
| `oem/` | üreticiye özgü açıklama ayrışması — `oem_divergence` | `<intake-id>.json` |
| `defects/` | kendi verimizden **ölçülmüş** kusur — `kb_defect` | `<intake-id>.json` |
| `spn_ref/` | PGN düzenlerinden toplanan SPN referansı — `spn_reference` | `<intake-id>.json` |
| `gaps/` | hiçbir provenance belgesinde olmayan kaynak — `provenance_gap` | `<intake-id>.json` |
| `traces/` | ham yakalama (kare dosyası + yan kenar dosyası) | `<id>.json`/`.jsonl` + `<id>.meta.json` |
| `cases/` | vaka taslağı — **her zaman `draft: true`** | `<intake-id>.json` |
| `oem_notes/` | OEM/üretici notu | `<intake-id>.md` (üstte JSON meta bloğu) |
| `_templates/` | tür başına **dolu** örnek (kopyalanır, doğrulanır) | `*.template.json`, `*.template.md` |
| `MANIFEST.md` | kaynak + lisans + `bytes` + `sha256` kaydı (**üretilir**, elle yazılmaz) | Markdown tablo |
| `NOTICE.*.md` | lisans atıfı (Apache-2.0 NOTICE vb.) — kayıt değildir | Markdown |
| `README.md` | bu dosya | — |

## Kurallar (ihlal = doğrulama FAIL)

### 1. Kaynak + lisans zorunludur; belirsizse reddedilir

Her kayıtta `source` zarfı tam olmak zorundadır: `title`, `path`, `type`,
`publisher`, `licence`, `access_date`. `licence` kapalı bir listeden
(`ALLOWED_LICENCES`) olmalıdır: `CC0-1.0`, `CC-BY-4.0`, `MIT`, `Apache-2.0`, …
`public-domain`, `project-internal` ya da **`proprietary`** (OEM el kitabı/TSB:
kaynak bilinen ama özel — okunabilir ve saklanabilir, **dağıtılamaz**; terfi
lisansı temiz bir yeniden yazımla yapılır).

**"Lisansı bilmiyorum", "TBD", "?" → kayıt reddedilir.** Belirsizlik bir
uyarı değil, ret sebebidir: lisansı çözülemeyen bir satır ileride hukuki
sorumluluğun altına imza atmak demektir. Aynı mantık `source.type` ve
`access_date` içindir. Lisans listeye girmiyorsa kaynak **redd edilir**;
`data/PROVENANCE.md` §5'teki kaynaklar (SAE/ISO standart metinleri, AllData
vb.) bu yüzden hiçbir zaman kopyalanmaz.

### 2. VIN ve kişisel veri yoktur (VIN maskeli)

Ham 17 karakterlik VIN, e-posta, telefon ve IBAN **herhangi bir alanda**
reddedilir (`RAW_VIN_RE`/`EMAIL_RE`/`PHONE_RE`/`IBAN_RE`; aynı regex ailesi
`src/engine/ai/golden_cases.py:62` ile aynı davranışı gösterir).

Araç kimliği yalnızca maskeyle tutulur ve alan adı bunu söyler:
`"vin_masked": "WBA*****X2M"`. Maske karakteri (`* X • <>`) içermeyen bir
`vin_masked` değeri reddedilir.

Ölçülmüş iki ayrım (ikisi de kanıtla sabitlendi): `sha256` özetleri ve CAN
karelerinin hex verisi **kişisel veri değildir** — tarayıcı bunları çıkarır,
aksi halde bir hash içindeki 8 haneli rakam dizisi "telefon" sanılıyordu.
Telefon kuralı yalnız gerçek TR uzunluklarını (10 haneli mobil, 11 haneli
sabit hat) kabul eder. Müşteri adı, plaka, telefon, e-posta ve
kayıt tarihi/gps içeren trace satırları **kabul edilmez** — kare verisi
teknik veridir, kişi verisi değildir. Şüpheli bulgular (IP, IMEI benzeri
rakam) WARN'dir ve gözden geçirme kuyruğuna düşer.

### 3. Büyük trace Git'e girmez: hash + konum MANIFEST'te

Depo geneli boyut kapısı `scripts/check_corpus_size_sentry.py` ile ölçülür
(tek dosya ≤ **100 MB**, `data/traces` ≤ **350 MB**, `data/dbc` ≤ **50 MB**,
`data/diagnostics` ≤ **150 MB**, `data/knowledge` ≤ **20 MB**). Intake bu
eşiklerden **çok daha katıdır**:

- `traces/` içinde commit edilebilen tek kare dosyası **≤ 1 MiB**
  (`DEFAULT_MAX_INLINE_TRACE_BYTES`, `--max-inline-trace-bytes` ile değiştirilebilir);
- bunun üstündeki her yakalama `in_git: false` olmak zorundadır, `external_location`
  (arşiv nesne deposu yolu) almalı ve MANIFEST'in **"Git'e girmeyen trace'ler"**
  tablosunda `sha256` + `bytes` + konum olarak kayıtlı olmalıdır;
- `.blf/.mf4/.asc/.trc/.casc/.dat/.db/.zip/.gz/.zst` uzantıları `.gitignore` ile
  ve doğrulayıcıyla intake'e giremez;
- `data/intake` toplamı **≤ 5 MiB** (`DEFAULT_MAX_INTAKE_BYTES`), aşarsa FAIL.

Böylece intake hiçbir zaman blob çöplüğüne dönüşemez; büyük veri repo dında
kalır, depoda yalnızca doğrulanabilir bir kanıt satırı bulunur.

### 4. Vaka `draft: true`'dur; onaysız kalibrasyona girmez

`cases/` altındaki her kayıt `draft: true` taşımak zorundadır; `draft: false`
FAIL'dir. Intake vakasında `verified` alanı **yoktur** (şema tam alan kümesi
kontrolü bunu zaten reddeder) ve `actual_fault`/`repair`/`verification`
teknisyen onayı olmadan `null` kalır.

Sonuç: intake vakası hiçbir koşulda
`src/engine/ai/golden_cases.py:245` `calibration_eligible_cases()` kümesine
giremez — çünkü o fonksiyon yalnızca `data/golden_traces/cases/` dizinini okur
ve `verified && !is_draft` ister. Bu, kasıtlı bir ayrımdır: terfi elle
yapılır (§ Entegrasyon adımları).

### 5. Bilinmeyen alan boş kalır, uydurma yok

- Bilinmeyen metin alanı `null`, bilinmeyen liste `[]`; asla tahmin edilmez.
- Şema **tam alan kümesidir** (`additionalProperties: false` mantığı): fazladan
  bir alan, kimsenin denetlemediği bir alandır ve reddedilir.
- `confidence` yalnız `unverified | single_source | corroborated` olabilir;
  `operator_verified` bir intake durumu **değildir** — doğrulama terfi adımıdır.
- Şablonlardaki `example.invalid` adresleri yer tutucudur; gerçek kayıtta
  gerçek belge adresi yazılır.

## Kayıt türleri

| `record_type` | Dizin | Payload özeti |
|---|---|---|
| `dtc` | `dtc/` | `code`, `system`, `title_en/tr`, `description`, `severity`, `known_symptoms`, `possible_causes`, `status`, `freeze_frame_ref`, `oem_ref`, `vin_masked` |
| `spn_fmi` | `spn_fmi/` | `spn` (0–524287), `fmi` (0–31), `system`, `description`, `typical_causes`, `pgn`, `oem_ref`, `vin_masked` |
| `pgn_layout` | `pgn/` | `pgn`, `pgn_id`, `description`, `pgn_type`, `priority`, `interval_ms`, `fields[]`, `upstream_keys`, `source_file` |
| `case` | `cases/` | `case_id`, `domain`, `make/model/year`, `symptom`, `dtcs[]`, `signals_of_interest[]`, `actual_fault`, `repair`, `verification`, `trace_refs[]`, `vin_masked` |
| `oem_note` | `oem_notes/` | `make/model/year`, `oem_code`, `system`, `evidence_refs[]`, `related_dtcs[]`, `vin_masked` (gövde markdown dosyanın altında) |
| `oem_divergence` | `oem/` | `make`, `source_file`, `source_rows`, `divergence_count`, `divergences[]` |
| `spn_reference` | `spn_ref/` | `spn`, `names_en[]`, `units[]`, `resolutions[]`, `bit_lengths[]`, `evidence_pgns[]`, `evidence_text[]`, `sources[]`, `kb_state`, `kb_name`, `kb_unit` |
| `provenance_gap` | `gaps/` | `source_key`, `occurrences`, `files[]`, `fields[]`, `sample_values[]`, `sample_record_keys[]`, `documented_in[]`, `licence_status` |
| `kb_defect` | `defects/` | `defect_code`, `severity`, `summary`, `why_it_matters`, `target_file`, `target_sha256`, `detector_expression`, `affected_count`, `examples[]` |
| `trace` | `traces/` | `frame_file`, `frame_file_sha256`, `frame_file_bytes`, `format`, `in_git`, `external_location`, `started_at`, `duration_s`, `channel_count`, `frame_count`, `bus`, `vin_masked` |

`spn_reference` kayıtları **iki bağımsız temsilden** beslenir: canboat'ın pinli
J1939 PGN alan düzenleri (YAML) ve repoda zaten vendor olan
`data/dbc/heavy_duty/j1939_canboat.dbc` (`CM_ SG_` yorumları, `data/dbc/manifest.json`
sha256'ı ile doğrulanır — eşleşmezse hiç kullanılmaz). İki temsil aynı cümlenin
iki biçimi olduğu için, ikisi de alıntılanan kayıt `confidence: corroborated`
der; validator bunu **zorunlu** kılar (iki kaynak + `single_source` → FAIL).
DBC yorumları ayrıca bit konumu ve çözünürlük metni taşır
(`SPN 1184: bits 33-40 (byte 4), 1 km/h per bit`).

`spn_reference` kayıtları, J1939 PGN alan düzenlerinde **gerçekten yazan**
(`SPN n` ifadesi geçen) SPN referanslarını toplar; sayı asla alan adından veya
bit düzeninden **türetilmez**. `kb_state` o SPN'nin bilgi tabanında olup
olmadığını gösterir (`absent` = terfi adayı) ve doğrulayıcı her çalıştırmada
yeniden ölçer: durum değişirse `WARN kb_drift`. Ad farkları (canboat kısa alan
adı kullanır: "Engine Coolant Temp" ↔ KB "Engine Coolant Temperature") kusur
sayılmaz, `spn_name_variant` metriğiyle **sayılır**; yalnız *birim çelişkisi*
(`kb_unit_conflict`) veya KB adının bir tanım cümlesi olması (`kb_name_defect`)
kayda değer INFO üretir.

`provenance_gap` kayıtları **kaynak başına** izlenebilirlik kanıtıdır: veri bir
kaynağı işaret ediyor ama ne `data/PROVENANCE.md` ne de
`data/diagnostics/PROVENANCE.md` onu adlandırmıyor. Alan sayısı (8.918) okunamaz
bir yığın olduğu için her **ayrı kaynak anahtarı** için bir kayıt tutulur (26
kayıt): `occurrences`, hangi dosyada, hangi alanda, örnek değerler ve kayıt
anahtarları. `licence_status` yalnız `unresolved` olabilir — lisans çözülürse
kayıt arşivlenir. Doğrulayıcı her çalıştırmada anahtarı yeniden ölçer:
`provenance_open` / `provenance_closed` / `WARN provenance_drift`.

`kb_defect` kayıtları **kendi verimizdeki** kusurları ölçümle kayda geçirir:
`detector_expression` (kusuru tanımlayan ifade), `affected_count` (bugünkü sayı),
`examples[]` (olduğu gibi örnekler) ve `target_file` + `target_sha256` (hangi
veri sürümü ölçüldü). Doğrulayıcı her çalıştırmada dedektörü **yeniden
çalıştırır**:

| Durum | Anlamı | Rapor |
|---|---|---|
| sayı aynı | kusur hâlâ açık | `INFO defect_open` |
| sayı 0 | düzeltildi, kayıt arşivlenebilir | `INFO defect_closed` |
| sayı değişti | dedektör ya da veri değişti | `WARN defect_drift` |
| hedef dosya sha256'ı değişti | ölçüm bayat | `WARN defect_stale` |
| dedektör var, kaydı yok | kuyruktan kaybolmak üzere | `INFO defect_unstaged` |

`oem_divergence.divergences[]` bir satırdır: `code`, `source_description_en`
(upstream'in üreticiye özgü metni) ve `kb_description_en` (OEM katmanının o kod
için bugün sakladığı tek metin). Katman kod başına **tek** açıklama tuttuğu için
üreticiye özgü ifade kaybolur; bu kayıtlar o kanıtı gözden geçirme için geri
getirir. Doğrulayıcı her çalıştırmada satırları yeniden ölçer ve
`kb_divergence` INFO satırı üretir.

`pgn_layout.fields[]` bir alanı tanımlar: `field_id`, `name`, `spn`, `bits`,
`unit`, `resolution`, `description`. `spn` **yalnız** upstream metninde yazıyorsa
doldurulur (doğrulayıcı bunu `WARN pgn_spn_source` ile denetler). Upstream'da
okunamayan üst düzey anahtarlar `upstream_keys` içinde kayıt altına alınır —
sessizce atılmaz. 64 biti aşan `bits` değerleri upstream'in "henüz çözülmemiş
fast-packet" gösterimidir: düzeltilmez, `WARN pgn_bits_bulk` ile işaretlenir.

## Kaynak taraması (keşif aracı)

```bash
python scripts/intake_scan_sources.py --report docs/audit/intake_source_scan_2026-10-02.md
python scripts/intake_scan_sources.py --json /tmp/scan.json
python scripts/intake_scan_sources.py --stage              # doğrula (yazmaz)
python scripts/intake_scan_sources.py --stage --apply      # intake'e yaz
python scripts/intake_scan_sources.py --stage-oem --apply  # OEM ayrışmaları
python scripts/intake_scan_sources.py --offline --tarballs-dir /tmp/tb
```

Kendi verimizdeki kusurları ölçer ve intake'e kaydeder:

```bash
python scripts/intake_kb_defects.py                     # ölç ve raporla
python scripts/intake_kb_defects.py --stage             # kayıtları doğrula
python scripts/intake_kb_defects.py --stage --apply     # yeni ölçümü sahalama
python scripts/intake_kb_defects.py --stage --refresh   # değişen ölçümü kabul et (bilinçli)
python scripts/intake_kb_defects.py --stage-gaps --apply   # kaynak başına boşluk kayıtları
python scripts/intake_scan_sources.py --stage-spn --apply   # SPN referansları
```

**13 dedektor** (2026-10-02 ölçümü) — kanıt zinciri:

| Dedektor | Etkilenen | Öncelik |
|---|---|---|
| `kb_source_value_not_in_provenance_doc` | 8.918 | high |
| `j1939_source_without_licence` | 1.220 | high |
| `spn_unit_placeholder` | 4.202 | medium |
| `spn_name_embeds_spn_fmi_token` | 148 | medium |
| `spn_parameter_name_not_in_alias_map` | 155 | medium |
| `spn_name_is_fmi_sentence` | 23 | high |
| `spn_without_fault_matrix` | 64 | medium |
| `dtc_severity_unknown_and_unclassed` | 1.756 | medium |
| `dtc_missing_title_tr` | 13.971 | low (bilinen kapsam eksiği) |
| `dtc_missing_symptoms` | 13.209 | low (bilgi eksiği) |
| `root_cause_graph_dtc_coverage` | 8.895 | medium (neden zinciri yok) |
| `cause_node_without_evidence_signal` | 6.167 | low (doğrulanabilir sinyal yok) |
| `measurement_signal_without_threshold` | 17 | medium (eşik tanımı yok) |

Ölçüm iki aileden oluşur: **kanıt zinciri** (lisans/izlenebilirlik, kayıt alanı
kalitesi, çapraz referans bütünlüğü) ve **yetkinlik** (copilotun gerçekte
yapabildikleri):

| Yetkinlik ölçümü | Değer | Anlamı |
|---|---|---|
| `dtc_severity_unknown_and_unclassed` | 1.756 (%12,1) | Şiddet ve sınıf aynı kayıtlarda placeholder → **sistematik** atlanmış küme |
| `dtc_missing_symptoms` | 13.209 (%91,2) | Semptom→DTC eşleştiricinin metni yok |
| `root_cause_graph_dtc_coverage` | 8.895 (%61,4) | Neden zinciri kurulamayan kod |
| `cause_node_without_evidence_signal` | 6.167 düğüm (%69,4) | Doğrulanacak sinyal adı yok → yalnız metin |
| `measurement_signal_without_threshold` | 17 / 24 sinyal | Sinyal çözülüyor ama "iyi mi kötü mü" cevaplanamıyor |

Çapraz referans tamlığı ise **temiz**: grafikteki 5.589 DTC ve 1.590 SPN
referansının tamamı DB'de karşılık buluyor (0 sarkan referans) — test bunu
kapı olarak sabitler.

`--stage` **kaydı asla sessizce ezmaz**: ölçüm değiştiyse hata verir ve
`--refresh` ile bilinçli kabul gerekir. Ölçümler girdi dosyalarının
`(path, mtime, size)` imzasına göre önbelleklenir — test paketi kapıyı onlarca
kez çalıştırdığı için bu, doğruluk değişmeden hız kazandırır.

Taranan dört kaynak: OBDex (CC0-1.0), canboat (Apache-2.0), Wal33D/dtc-database
(MIT) ve (referans için) SITRAK (CC-BY-4.0). `--stage` canboat J1939 PGN
düzenlerini, `--stage-oem` Wal33D üretici listelerini sahaya alır.

Araç, `data/PROVENANCE.md` §2'de kanıtlanmış **pinli commit'leri** indirir,
artefakt envanterini `sha256` ile çıkarır ve hangisinin zaten vendor edildiğini
belgeler. Ağ erişimi yalnız bu araçtadır (build-time ingest). 2026-10-02
ölçümü: 95 artefakt, 11 birebir doğrulanmış (0 sapma), **84 yeni** — canboat
`database/j1939/pgns/` düzenleri. Ayrıntı:
`docs/audit/intake_source_scan_2026-10-02.md`.

YAML okuyucu canboat'ın üretilmiş alt kümesi için **kasıtlı olarak dar**tır ve
kapatmada davranır: okuyamadığı satırı sessizce geçmez, hata verir. PyYAML
bağımlılığı yoktur; her makinede aynı kayıt üretilir.

## Doğrulama

```bash
python scripts/validate_intake.py                          # rapor + çıkış kodu
python scripts/validate_intake.py --report docs/audit/intake.md
python scripts/validate_intake.py --print-trace-meta data/intake/traces/<file>.jsonl
python scripts/validate_intake.py --sync-manifest          # MANIFEST güncel mi?
python scripts/validate_intake.py --sync-manifest --apply  # MANIFEST'i üret
```

`--sync-manifest` intake'in **tek yazıcısıdır** ve yalnız `MANIFEST.md` üretir:
hash, bayt, `intake_id`, `licence` ve `source.path` alanları kayıtlardan okunur,
elle yazılmaz.

Kontroller (hepsi fail-closed, yalnız stdlib — `golden_cases.py` ile aynı
disiplin):

1. **Yerleşim** — kayıt yalnız bilinen alt dizinlerde; yedek/artefakt dosyası,
   bilinmeyen yol ve commit edilmiş ham container reddedilir; her kare
   dosyasının `<id>.meta.json` kanadı olmalıdır.
2. **Şema** — zarf ve `payload` tam alan kümesiyle doğrulanır; tip/format/
   aralık ihlalleri (ör. `fmi > 31`, `code` J2012 kalıbında değil) FAIL'dir.
3. **Provenance/lisans** — §1.
4. **VIN/PII** — §2.
5. **Trace politikası** — §3; kare dosyasının `sha256`/`bytes` değerleri
   `.meta.json` ile karşılaştırılır, `can_frames_json` kareleri şekil
   (`t/channel/id/dlc/data`, `data` = `dlc` bayt) ve `frame_count` eşleşmesi
   denetlenir.
6. **MANIFEST ↔ dosya ↔ hash** — şablonlar hariç her artefaktın tam **tek bir**
   MANIFEST satırı olmalı; `bytes`, `sha256`, `intake_id` ve `licence` diskteki
   dosyayla ve kaydın kendi `source.licence` değeriyle uyuşmalıdır. Satırın
   işaret ettiği dosya var olmalı, hiçbir kayıt MANIFEST'e yazılmamış olmamalıdır.
   `intake_id` **kayıt düzeyinde** tekildir (bir trace'in kare dosyası sahibiyle
   aynı `intake_id`'yi taşır); iki ayrı kayıt aynı `intake_id`'yi kullanamaz.
7. **Draft kuralı** — §4.
8. **Karantina değişmezleri** — `data/diagnostics/quarantine/` içindeki denetim
   iddiaları her koşuda yeniden doğrulanır: `already_in_graph` denilen 38 tohum
   düğümün hâlâ grafta olması, `not_recovered` denilen 15 düğümün grafta
   **olmaması**, 13 karantina kabuk satırı anahtarının DB'de bulunmaması ve
   karantina edilen LLM blok özetlerinin DB metninde geçmemesi. Bu bir *bulgu*
   değil bir **kapıdır**: sonraki bir merge eski temizliği sessizce geri
   getirirse FAIL verir.
9. **Çakışma raporu (yalnız rapor)** — mevcut `data/diagnostics/dtc_database.json`,
   `dtc_database_oem_layer.json`, `j1939_spn_fmi_database.json`,
   `canboat_pgn_reference.json` ve `data/golden_traces/cases/` ile
   karşılaştırılır. `pgn_layout` alanlarının SPN referansları da KB'de aranır:
   bulunamayan SPN `WARN pgn_spn_unknown` üretir (bilgi eksiği, terfi iş kalemi). Bulgu `kb_overlap`
   ("zaten var") veya `kb_new` ("bilgi tabanında yok, terfi adayı") olur.
   **Hiçbir kayıt üzerine yazılmaz, hiçbir dosya değiştirilmez.** Doğrulayıcı
   `data/diagnostics` ve `data/golden_traces` dosyalarını yalnızca okur.

FAIL varsa çıkış kodu `1`'dir; WARN/INFO yalnızca raporlanır.

> **CI bağlantısı notu:** bu kapı şu an repo kapılarından ayrı çalışır
> (`scripts/validate_copilot_data.py` intake'i tarar çünkü `data/diagnostics`,
> `data/knowledge` ve `data/golden_traces` ile sınırlıdır). `ci.yml`'e
> `python scripts/validate_intake.py` adımının eklenmesi ayrı bir değişikliktir;
> eklenene kadar katkıcı ve integrator bu komutu elle çalıştırmak zorundadır.

### MANIFEST satır biçimi

```markdown
| intake_id | path | kind | bytes | sha256 | licence | source |
|---|---|---|---|---|---|---|
| dtc-p0301-workshop | `data/intake/dtc/dtc-p0301-workshop.json` | dtc | 1187 | `1f3c…` (64 hex) | proprietary | `https://…` |
```

`path` depo kökünden (`data/intake/…`) yazılabilir; doğrulayıcı öneki ayıklar.
Git'e girmeyen yakalama için ayrı tablo:

```markdown
| intake_id | format | bytes | sha256 | location |
|---|---|---|---|---|
| trace-long-2026-10 | blf | 81234567 | `9ab0…` (64 hex) | `s3://ucanlab-intake/2026-10/…blf` |
```

## Entegrasyon adımları (intake → data/diagnostics ve golden_traces)

Bu adımlar **elle**, gözden geçirme kapılarıyla yürür. Otomatik merge yoktur.

**Adım 0 — Topla.** `_templates/`'den kopyala, yer tutucuları gerçek kaynakla
değiştir, MANIFEST satırını ekle.

> **Kapı 0:** `python scripts/validate_intake.py` → `FAIL=0`.
> Raporu PR/commit notuna yapıştır; `kb_new` satırları "bu kod bizde yok"
> kanıtıdır, `kb_overlap` satırları "bunu zaten biliyoruz" uyarısıdır.

**Adım 1 — Kaynak doğrulaması (curator).** Belge/URL gerçekten var mı, lisans
doğru mu, atıf gerekiyor mu, PII sızdı mı? Belirsizse kayıt **reddedilir**
(duruma göyle `data/diagnostics/quarantine/` — intake'ten silinmez, gerekçesiyle
kapatılır). `data/licenses/` altına atıf metni eklenmesi gerekiyorsa eklenir.

> **Kapı 1:** ikinci göz (farklı kişi) kaynak + lisans + maskeleme onayı imzalar.

**Adım 2 — DTC / SPN-FMI terfisi (bilgi tabanı).**

- **DTC:** `data/diagnostics/dtc_database.json` içinde kod **zaten varsa** hiçbir
  şey yazılmaz (katmanlılık kuralı, `data/PROVENANCE.md` §4.1); yalnızca
  eksik alanlar ve `_source_license`/`_source_ref` etiketleri eklenir. Yeni kod
  ise ana şemaya `P/B/C/U` anahtarıyla, üreticiye özgü kayıt gerekiyorsa
  `data/diagnostics/dtc_database_oem_layer.json` komşu dosyasına yazılır
  (T25 kuralı). Her durumda `dtc_record_schema.json` +
  `provenance_schema.json` şeması geçerlidir.
- **SPN/FMI:** `data/diagnostics/j1939_spn_fmi_database.json` içindeki `spns`
  sözlüğüne **yalnız yeni** alanlar eklenir (`fault_matrix`, `causes`, `steps`
  korunur; CC-BY kaynaklarda `_source_license_sitrak` benzeri çakışmayan alan
  adı kullanılır).

> **Kapı 2:** `python scripts/data_integrity_audit.py` → FAIL=0 ve
> `python scripts/validate_copilot_data.py` → FAIL=0 (şema, provenance,
> çapraz referans, CSV ikizleri senkron).
> `python scripts/rebuild_csv_exports.py` ile CSV ikizleri yeniden üretilir.

**Adım 3a — PGN düzeni terfisi (`pgn/`).** `pgn_layout` kayıtları terfi
edilirse `data/diagnostics/canboat_pgn_reference.json` içindeki `j1939` bloğuna
eklenir; **mevcut NMEA-2000 `pgns` listesi ve `pgns_by_number` indeksi
bozulmaz** (data/PROVENANCE.md §3.4'teki katmanlılık kuralı, T25/HANDOFF_TUR24
§4.4). Kayıt başına `_source_license` + `_source_ref` etiketi zorunludur ve
Apache-2.0 atıfı `data/licenses/NOTICE.canboat` ile korunur.

> **Kapı 3a:** `python scripts/validate_copilot_data.py` → FAIL=0 ve
> `python -m pytest tests/unit/test_data_attributions.py -q` → PASS.
> Bulgu: staged alanların **66 SPN referansı** KB'de yok; terfi önce bu SPN'ler
> için `spn_fmi` kaydı açılması gerekir, aksi halde alan düzeni "tanınan
> parametre" diye görünür ama parametre kaydı yoktur.

**Adım 3b — Üretici açıklaması ayrışmaları (`oem/`).** `oem_divergence`
kayıtları, OEM katmanının "kod başına tek açıklama" darboğazını kanıtlar. Terfi
kararı bir katmanlılık sorusudur: ya `dtc_database_oem_layer.json` kayıtları
`oem_records[]` ile üreticiye özgü açıklamayı taşır (ana şema bozulmaz), ya da
açıklama `description.en` içinde üreticiye göre birleştirilir. Her iki durumda
da **hiçbir mevcut değer ezilmez**; yalnız yeni alan eklenir ve kayıt başına
`_source_license` + `_source_ref` konur.

> **Kapı 3b:** `python scripts/validate_copilot_data.py` → FAIL=0,
> `python -m pytest tests/unit/test_data_integrity.py tests/unit/test_data_attributions.py -q`
> → PASS ve `python scripts/validate_intake.py` → FAIL=0 (ayrışma yeniden
> ölçülür ve kapanır).

**Adım 3 — Trace terfisi.** Sadece ≤ 1 MiB'lik, kişisel veri içermeyen ve
`frame_count`/`sha256` doğrulanan yakalamalar `data/traces/` altına alınabilir.
Büyük yakalamalar intake'te kalır: yalnız `sha256` + konum MANIFEST'te durur,
terfi ancak hash'i doğrulanıp küçültülmüş türevi üretildiğinde yapılır.

> **Kapı 3:** `python scripts/check_corpus_size_sentry.py` → PASS.
> `python -m pytest tests/unit/test_replay_reducers_and_sentry.py -q` → PASS.

**Adım 4 — Vaka terfisi (golden_traces).** `cases/` kaydı, teknisyen onayı
verdikten sonra `data/golden_traces/cases/schema.json` **v1** alan kümesine
elle çevrilir: `draft`/`submitter`/`source`/`confidence`/`notes` **taşınmaz**;
`case_id`, `domain`, `make`, `model`, `year`, `symptom`, `dtcs`,
`signals_of_interest`, `actual_fault`, `repair`, `verification`, `trace_ref`,
`verified`, `verified_date` yazılır. `domain: AGRICULTURE` v1 şemasında yok —
o vakalar şema genişletilene kadar intake'te bekler (doğrulayıcı WARN verir).
`verified: true` için `actual_fault` ve `verified_date` dolu olmak zorundadır.

> **Kapı 4:** `python scripts/validate_copilot_data.py` → FAIL=0 ve
> `python -m pytest tests/unit/test_golden_cases.py tests/unit/test_validate_intake.py -q` → PASS.

**Adım 5 — Kalibrasyon kapısı.** Terfi edilen vaka ancak
`calibration_eligible_cases()` (`src/engine/ai/golden_cases.py:245`) içinde
görünürse AI kalibrasyonuna girer. Intake kaydının bu listeye **hiçbir**
doğrudan erişimi yoktur.

> **Kapı 5:** `python -m pytest tests/unit/test_ai_roadmap_faz*.py -q` → PASS
> (regresyon), sonra intake kaydı MANIFEST'ten **kaldırılmadan** arşivlenir
> (`docs/audit/` raporu + intake satırı `promoted:` notuyla işaretlenir).

**Yasak olan:** intake dosyasını kopyalayıp doğrulamadan
`data/diagnostics`/`data/golden_traces` içine yapıştırmak, mevcut bir kaydı
ezmek, `draft: true` vakayı doğrulanmış gibi işaretlemek, kaynağı/lisansı
belirsiz bir satırı "sonra düzeliriz" diye bırakmak.

## Şablonlar (kopyala-yapıştır örnekleri)

| Şablon | Tür |
|---|---|
| `_templates/dtc.template.json` | DTC başvurusu |
| `_templates/spn_fmi.template.json` | SPN/FMI çifti |
| `_templates/case.template.json` | vaka taslağı (`draft: true`) |
| `_templates/pgn_layout.template.json` | PGN alan düzeni |
| `_templates/spn_reference.template.json` | SPN referansı |
| `_templates/provenance_gap.template.json` | kaynak izlenebilirliği boşluğu |
| `_templates/kb_defect.template.json` | ölçülmüş kusur |
| `_templates/oem_divergence.template.json` | OEM açıklama ayrışması |
| `_templates/trace.frames.template.json` + `_templates/trace.meta.template.json` | kare dosyası + kanadı |
| `_templates/oem_note.template.md` | OEM notu (meta bloğu + gövde) |

Şablonlar `_templates/` altında olduğu için MANIFEST'e yazılmaz ve kayıt
taramasına girmez; yine de `tests/unit/test_validate_intake.py` her şablonu
gerçek bir kayıt gibi doğrular.

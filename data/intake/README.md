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
| `traces/` | ham yakalama (kare dosyası + yan kenar dosyası) | `<id>.json`/`.jsonl` + `<id>.meta.json` |
| `cases/` | vaka taslağı — **her zaman `draft: true`** | `<intake-id>.json` |
| `oem_notes/` | OEM/üretici notu | `<intake-id>.md` (üstte JSON meta bloğu) |
| `_templates/` | tür başına **dolu** örnek (kopyalanır, doğrulanır) | `*.template.json`, `*.template.md` |
| `MANIFEST.md` | kaynak + lisans + `bytes` + `sha256` kaydı | Markdown tablo |
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
`vin_masked` değeri reddedilir. Müşteri adı, plaka, telefon, e-posta ve
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

## Doğrulama

```bash
python scripts/validate_intake.py                      # rapor + çıkış kodu
python scripts/validate_intake.py --report docs/audit/intake.md
python scripts/validate_intake.py --print-trace-meta data/intake/traces/<file>.jsonl
```

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
8. **Çakışma raporu (yalnız rapor)** — mevcut `data/diagnostics/dtc_database.json`,
   `dtc_database_oem_layer.json`, `j1939_spn_fmi_database.json` ve
   `data/golden_traces/cases/` ile karşılaştırılır. Bulgu `kb_overlap`
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
| `_templates/trace.frames.template.json` + `_templates/trace.meta.template.json` | kare dosyası + kanadı |
| `_templates/oem_note.template.md` | OEM notu (meta bloğu + gövde) |

Şablonlar `_templates/` altında olduğu için MANIFEST'e yazılmaz ve kayıt
taramasına girmez; yine de `tests/unit/test_validate_intake.py` her şablonu
gerçek bir kayıt gibi doğrular.

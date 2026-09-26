# KONSOLİDE KEŞİF RAPORU — TEK DOSYA

> **Konu:** "Universal CAN-Bus Diagnostic & Telemetry Platform" — veri korpusu, kanıt kaynakları, protokol standartları, donanım, lisans, mevzuat, güvenlik değişmezleri, performans, UI
> **Tarih:** 2026-09-25 23:32 → 2026-09-26 (kesintisiz tur)
> **Yöntem:** 6 dalga · 20+ paralel alt görev · statik envanter + internet araştırması (her iddia URL + tarih taşır)
> **Roller:** `scout [RE/DBC]` · `telemetry [Data]` · `chassis [HAL]` · `marshal [Safety]` · `uplink [Cloud]` · `cockpit [UI]`

---

## ⚠️ DOSYA KAYBI NOTU

Bu turda yazılan 14 ayrı rapor dosyası (`INTERNET_RESEARCH_01`…`13` + `DATA_CORPUS_SOURCE_DISCOVERY` + dizin) **çalışma alanı sıfırlandığı için diskten silinmiştir.** `docs/research/` dizini yeniden kontrol edildi: içinde yalnız 3 eski dosya (`dbc_expansion_scan_2026-09-02.md`, `OFFLINE_COPILOT_RESEARCH.md`, `signal_discovery/`) var, yeni raporların hiçbiri yok.

**Bu dosya, kaybolan tüm raporların tek dosyada konsolide edilmiş hâlidir.** Bölüm A–I özet + sentezdir; **Bölüm J–K ayrıntılı referans tablolarıdır** ve ilk yazımda atlanmış içerikleri içerir. Tam metinler yeniden üretilebilir (yöntem ve URL'ler korunmuştur).

### ⚠️ Kapsam boşlukları — tam listesi

**1) Sonuç hiç dönmeyen 4 dal:**

| Dal | Konu | Nasıl kapatılır |
|---|---|---|
| `f24f020b` | Güvenlik standartları + AI tavrı (ISO 21434, SOTIF derinlemesine) | `websearch` rate-limit'i düşünce yeniden başlat |
| `f24eb1e2` | Rakipler + open-source prior art (ayrıntılı) | Aynı |
| `f24e38717` | UDS/ISO 14229 tam servis + NRC tablosu | ⚠ **KISMEN kapatıldı** — Bölüm J.1'de servis/NRC/rutin/DID tabloları var, ama **J1979-2 (OBDonUDS) PID/servis/DID eşlemesi** ve **ISO 14229 madde numaraları** eksik |
| `f24b3bd9c` | Paketleme/dağıtım (PyInstaller/Nuitka, imzalama, updater) | Aynı **veya** `scripts/build_exe.py` + `scripts/build_nuitka.py` yerel okumasıyla kapatılabilir |

**2) İlk yazımda özetlenip atlanan ve **Bölüm J–K ile eklenen** içerik:**
UDS tam servis tablosu + 30 `0x19` alt fonksiyonu + tam NRC + rutin ID aralıkları + DoCAN zamanlama (J.1) ·
NMEA-0183 cümle tablosu + 30+ PGN + **40+ satırlık arıza modu tablosu** + 25 veri kümesi + tekne izleme açık kaynak (J.2) ·
14 DBC upstream'in sürüm/lisans/hareket tablosu + biçim tuzakları (K.1) ·
Paket sağlığı tablosu + ölü paket listesi + ISOBUS boşluğu (K.2) ·
**35 alanın tam listesi** + AUTOSAR sözlüğü + EP literatürü (K.3) ·
Yükleme protokolü + S3 checksum matrisi (K.4) ·
Broker + zaman serisi + OTel kardinalite tabloları (K.5) ·
UI stack + pywebview backend + i18n araç tabloları (K.6) ·

**3) Hâlâ boş olan tek bölüm:** **K.7 — paketleme/dağıtım** (yukarıda açıklandı).

**Hiçbir `src/`, `tests/` veya `data/` dosyası değiştirilmemiştir.**

---

# BÖLÜM A — YÖNETİCİ ÖZETİ

## A.1 En kritik 11 bulgu (P0 — sevk edilemez / sessiz veri kaybı)

| # | Bulgu | Nerede | Düzeltme |
|---|---|---|---|
| **1** | 🔴 **GPL-3.0 metni depoda HİÇBİR YERDE yok** (`COPYING` yok) ama **≥17 GPL türevi `.dbc` dağıtılıyor** (dalathegreat 6, qwec01 4, ARCFOX 3 vb.). GPL-3.0 §4/§5(a) ihlali — yorum gerektirmeyen, nesnel. | `data/dbc/passenger/nissan_leaf_*`, `arcfox_*`, `ev_bms/gb27830_2015.dbc` | GPL türevi dosyaları dağıtımdan çıkar; LEAF için `commaai/opendbc`'ye geç |
| **2** | 🔴 **`jaguar_xf_x250_*.dbc` CC-BY-SA-4.0** ve **`j1939_isobus_vdma_all.dbc` birleştirilmiş.** CC BY-SA 4.0'ın §4(b)'si *"birleştirilmiş veritabanı = Adapted Material"* diyor; §3(b)(1) Adapter's License'ın CC BY-SA olmasını şart koşuyor. **CC BY-SA 4.0, 2.0/3.0'deki "Collection" emniyet limanını KALDIRDI.** Proprietary üründe **imkânsız.** | `data/dbc/passenger/jaguar_*` | **1. çıkış adayı** |
| **3** | 🔴 **`isobus_dd11783.dbc` için hiçbir açık lisans yok** — isobus.net terms-of-use yayımlamıyor; ISO'nun kendi metni *"No reproduction on networking permitted without license from ISO"* diyor. Ayrıca `opendbc-ag` bu DD'yi **scrape edip MIT olarak yeniden lisanslıyor.** | `data/dbc/` kök | Sil ya da `iso11783_agisostack.dbc` (MIT) ile değiştir |
| **4** | 🔴 **`data/traces/` (289 MB, en büyük varlık) hiçbir koddan okunmuyor.** ReplayBus yalnız `.asc`/`.blf`/`.csv` destekliyor; 130+ gerçek yakalamanın **hiçbiri bu formatta değil** (`.raw`, `.all`, `.ebl`, `.dle`, `.pcap`, `.trc`, `.TXT`). | `src/hal/replay/parsers.py:1` | Dönüştürücü kur (Bölüm C.3) |
| **5** | 🔴 **`data/knowledge/` (601 dosya) hiçbir build script'inde paketlenmiyor** — `build_exe.py:147-156` ve `build_nuitka.py:92-101` yalnız `data/diagnostics` ekliyor. Yol `Path(__file__).parents[3]` ile çözüldüğü için frozen build'de **601 prosedür sessizce kayboluyor.** | `src/engine/ai/procedure_validator.py:195` | Her iki script'e ekle |
| **6** | 🔴 **`.blf` okuyucuları nesneleri sessizce düşürüyor** — python-can: *"Only CAN messages and error frames are supported. **Other object types are silently ignored.**"* Sessiz kare kaybı **zaman çizelgesi uydurmaktır** ⇒ AGENTS.md §2.3 ihlali. | `python-can` `BLFReader` | `skipped_object_types` sayacı + fail-closed |
| **7** | 🔴 **`gb27830_2015.dbc` yanlış adlandırılmış.** **GB/T 27830 bir kimyasal deri korozyonu test yöntemidir** (GB/T 27830-**2011**, OECD No. 431 benimsenmesi), EV standardı **değildir.** Gerçek standart **GB/T 27930**; 2015 sürümü **2024-04-01'de ilga edildi**, yerine 2023 sürümü geçti. DBC'nin ID'leri ayrıca **32-bit sentetik** — `python-can` bu dosyayı olduğu gibi yükleyemez. | `data/dbc/ev_bms/` | `gbt27930_2023.dbc` olarak yeniden adlandır + 29-bit düzelt |
| **8** | 🔴 **OpenSkipper GPL-3.0, CC BY-SA 3.0 DEĞİLDİR** — repo `LICENSE` dosyası GNU GPL v3 metnini birebir içeriyor. Projenin `LICENSE-note.txt`'i CC BY-SA 3.0 diyor ve `OpenSkipper/SampleLogs` kaynağını gösteriyor; **hiçbir CC BY-SA bildirimi bulunamadı.** 27 MB'lık korpus doğrulanmadan dağıtılmamalı. | `data/traces/marine/openskipper_samples/` | Legal inceleme; kaynağı doğrula veya çıkar |
| **9** | ⭐⭐ **CRA Madde 14 raporlama yükümlülüğü 2026-09-11'de YÜRÜRLÜĞE GİRDİ** — 24 saat erken uyarı / 72 saat tam bildirim / sonuç raporu ≤14 gün (aktif istismar edilen açıklar) veya ≤1 ay (ciddi olaylar), ENISA Tek Raporlama Platformu üzerinden. **Bugünden 15 gün önce.** Sıfır süreç ⇒ canlı açık. Tam uygulama **2027-12-11**. | Tüm ürün | 🔴 **Hemen CSIRT/PSIRT temsilcisi atayın ve VDP yayımlayın** |
| **10** | 🔴 **Lisans tuzakları:** `obd` (python-OBD) **GPL-2.0-only** — MIT/kapalı ürüne bağlanamaz. `python-can` **LGPL-3.0-only**, `asammdf` **LGPL-3.0-or-later** — Windows/PyInstaller'da frozen ikililer klasik LGPL ihlalidir. | `pyproject.toml` | Bağımlılık kararı; alternatif: HAL sürücülerini `TxPort`/`RxSubscription` üzerinden clean-room yeniden yaz |
| **11** | 🔴 **NMEA 2000 PGN veritabanı NMEA'nın telifli IP'sidir; Apache-2.0 yalnız canboat *kodunu* kapsar.** canboat README'si: *"the NMEA 2000 database… **is copyrighted by the NMEA**… we have **reverse engineered** the NMEA 2000 database."* `LICENSES.md`'de buna "Apache-2.0" demek **yanlış bir SPDX beyanıdır.** | `data/dbc/LICENSES.md` | Ayrı ve dürüst giriş ekleyin |

## A.2 Yanlış olduğu kanıtlanmış 9 varsayım (koddan/korumadan çıkarılmalı)

| # | Yanlış iddia | Gerçek (kaynak) |
|---|---|---|
| 1 | "GB/T 27830-2015 = EV şarj iletişim protokolü" | **GB/T 27830-2011 = kimyasal deri korozyonu test yöntemi** (std.samr.gov.cn). Gerçek: **GB/T 27930** |
| 2 | "SAE J2845 = RCDD (Şok Dedektörlü)" | **J2845 = A/C soğutucu teknisyen eğitimi.** "BMS teşhis iletişimi" için **hiçbir SAE standardı yok** |
| 3 | "PGN 1213 = lamba talebi PGN'i" | PGN 1213 = **"Dash Display 1"** (veri PGN'i). Lamba durumu **DM1/DM12/DM31 içinde**, SPN 623/624/987/1213 |
| 4 | "Mode $07 = farklı modülden DTC" | J1979/ISO 15031-5'te **$07 = pending DTC**. Modül bazlı okuma = **fiziksel adresleme** |
| 5 | "UDS'de Mode $08 var" | **Yok.** J1979 $08 = on-board test kontrolü; UDS halefi **RoutineControl 0x2F** |
| 6 | "TID = 0xFF, test tamamlanmadı" | **`$FF` rezerve.** Konvansiyon **`TV = Min = Max = 0x0000`** |
| 7 | "OEM PID'ler 0xA000–0xAFFF" | J1979-2 → **`$F400–$F5FF`** (PID), `$F600–$F7FF` (MID), `$F800–$F8FF` (InfoType). Subaru `$1000+` |
| 8 | "ISO 6469-3 = test yöntemleri" | **Elektriksel Güvenlik.** Pak test yöntemi ≈ **ISO 12405-4** |
| 9 | "NMEA 0183-1:2019" | **Yok.** NMEA 0183 **v4.30** (Ara 2023) v4.11'i (2018) değiştirdi. `$YDBT` neredeyse kesinle Yacht Devices **proprietary** |

**Ayrıca uydurulmuş/ölü referanslar:** `shemps/byd-atto3` (repo **404**) · `carob.europa.eu` (**530**) · `GSITLC GM host` (ölü) · `final_rev1.pdf` (**404** → doğrusu `final_dm.pdf`) · `zone.ni.com` ASC açıklaması (ölü bağlantı) · **"CAROB" diye bir AB/UNECE teşhis veritabanı YOK** (tek arama eşleşmesi tarım veri seti) · "GB/T 27930.5" yayımlanmamış · **"balkanization problem"** terimi arXiv/Crossref'de **sıfır** sonuç → topluluk sözlüğü, standart değil

## A.3 Ücretsiz kazanç (19 kalem)

| # | Bulgu | Kazanç |
|---|---|---|
| ⭐A | **OBDex (veri CC0-1.0 / kod MIT, 2026-05-01)** — 9.533 jenerik DTC, **%100 zenginleştirme, sıfır stub**, 132 PID formül/birim/aralıkla, JSON-Schema + CI, **anahtarsız CDN** | Kazınan katmanın doğrudan ikamesi. Mevcut DB'de **13.476/14.352 (%93,9) kayıt boş `symptoms`** taşıyor |
| ⭐B | **canboat v8.3.0 (2026-09-25)** — sizin kopyanız v8.0–v8.2 | **+10 PGN, +930 alan tanımı** |
| ⭐C | **canboat `database/pgns/*.yaml` içinde 638 gömülü `samples:` bloğu** (ham kare + beklenen çözülmüş alan) | `canboat_analyzer_tests/` (691 KB) korpusundan **daha değerli** regresyon vektörü |
| ⭐D | **ASAM MDF 4.3.0 (23 Eyl 2025)** — GNSS + ham sensör + **SOME/IP** loglamayı standardize etti, geriye uyumlu, ücretsiz "view online" | `KmlExporter`/`Mdf4Exporter` için **hedef standart**. "raw + conversion formulas" deposu **doğal olarak uydurma-karşıtı** |
| ⭐E | **`pretty_j1939` (NMFTA)** — lisanslı J1939DA `.xls` → JSON | SPN scraping'inin **yerine geçen meşru açık kanal** |
| ⭐F | **VW e-Golf `BMS_Monitoring` bayrak seti** (`ERROR_CONTACTOR_OPEN`/`_WELD`/`_ISOLATION`/`EMRG_SHUTDOWN_*`) — 15 bayrak | `TxSafetyGateway` fail-closed sözlüğüne **1:1** eşleniyor |
| ⭐G | **UN R100 atıf alınabilir eşikler:** 100 Ω/V izolasyon · 7 MΩ @ 500 VDC · **5 dakikalık ön uyarı sinyali** · GM saha-normali 6200 kΩ · Tesla HVIL ~20 mA | `hv_safety_thresholds.json` için hazır, provenance'lı tablo |
| ⭐H | **GB/T 27930 çoklu-PGN toplama kuralı** — *"fonksiyon başarıyla gönderilmiş sayılmak için **tüm** mesajlar alınmış olmalı"* | Doğrudan **fail-closed zorunluluk** |
| ⭐I | ⭐⭐ **UDS `0x29 Authentication` + `0x84 SecuredDataTransmission`** (ISO 14229-1:2020), `0x27 SecurityAccess`'in resmî alternatifi — 23 NRC hazır taksonomi (0x34–0x3A, 0x50–0x5D) | **Araç→ECU kimlik doğrulaması zaten standartlaştırılmış.** RP1210 varsayımına gerek yok |
| ⭐J | ⭐⭐ **RFC 4998 "Evidence Record Syntax"** (Proposed Standard, Ağustos 2007) — Merkle ağacı + **Timestamp Renewal** + **Hash-Tree Renewal**, **hash algoritması zayıflamasına ve sertifika bitimine dayanıklı**, ASN.1 | `RollingDiskBuffer` HMAC zincirinizi **2007'den beri bir standarda** oturtur |
| ⭐K | ⭐⭐ **RFC 8785 (JCS)** — lisans imzası için kanonik serileştirme; I-JSON girdi, `NaN`/vekil karakter terminating error, **Unicode normalizasyonu uygulanmaz** | "Hangi baytları imzaladık?" belirsizliği yapısal olarak biter |
| ⭐L | ⭐ **16 metamorphic ilişki** (MR-1…MR-16) — **MR-13 (fault-injection saflığı)** ve **MR-16 (fail-closed closure)** AGENTS.md §2.2'yi bir oracle'a çeviriyor | En yüksek değer/yük oranı |
| ⭐M | ⭐ **CiA 306 EDS = düz INI, CiA login'iyle ücretsiz**; `python-canopen` zaten EDS'ten OD kuruyor | ~200 satırlık parser, **lisans engeli yok** |
| ⭐N | **Mosquitto 2.1.2** `sparkplug-aware` + `persist-sqlite` eklentileri, **EPL-2.0 OR EDL-1.0 (izinli çift lisans)** | Edge broker varsayılanı. 🔴 `max_packet_size` artık **2 MB** — MDF4 tel üzerinde gitmemeli |
| ⭐O | **QuestDB 10.0.1 Apache-2.0**, 8M+ satır/sn, Airbus günde milyarlarca nokta, Parquet/Iceberg export | Sıcak katman, kilitsiz çıkış |
| ⭐P | **Git SHA-256 repo formatı (git 2.55.0)** — *"Object names can be signed and third parties can trust the hash to address the signed object"* | İmzalı manifest hedefiyle uyumlu |
| ⭐Q | **IMO A.1021(26) dört alarm önceliği** (emergency alarm / alarm / warning / caution) + §10.1.2 SOLAS eşleme tablosu + §5.9 *"machinery alarm must be so arranged that failure of the power supply or signal-generator device cannot cause loss of function"* | Deniz severity sözlüğünüzün üst kaynağı. **Fail-closed gereksiniminin IMO dilindeki karşılığı** |
| ⭐R | ⭐ **`10.5281/zenodo.19857425`** (CC BY 4.0) — gerçek deniz dizelinde **fiziksel olarak enjekte edilmiş 5 arıza** (kompresör filtresi, **hava soğutucu kirlenmesi**, **enjeksiyon memiği tıkanması**, **soğutma pompası kavitasyonu**, **turbo bozulması**), 4 yük noktası, ~115.000 örnek, 70 kanal | Soğutma/turbo/enjeksiyon arıza kurallarını **fiziksel olarak doğrulayan tek kaynak** |
| ⭐S | ⭐ **Aksanti olmayan kanıtlı konumlandırma:** GitHub'da "çevrimdışı, kural tabanlı, kanıt izleyen çok protokollu teşhis" projesi **0** (ASIL+Python+CAN: 0; araç-arıza-bilgi-tabanı: 1, o da **bulut-RAG bağımlı**; uzman-sistemi: 14, hepsi akademik ≤3★, hiçbiri bus'a bağlı değil) | En güçlü konumlandırma argümanı. **Yöntemiyle birlikte** sunulmalı |

## A.4 Teknik olarak doğrulanamayan ve tahmin edilmemesi gereken 5 madde

| # | Madde | Neden | Ne yapılmalı |
|---|---|---|---|
| 1 | 🔴 **ASIL kapsam sayıları (SPFM/LFM/HFM)** | `iso.org` **403** tüm seans | Bu rakamları **hiçbir güvenlik dosyasına koymayın** |
| 2 | 🔴 **CAN bus-off kurtarma kuralı (128 × 11 çekinik bit)** | Bosch `can2spec.pdf` **404** | **Bir kurtarma politikası yazılmamalı.** O zamana kadar `restart-ms 0`, otomatik kurtarma yok |
| 3 | **WWH-OBD (ISO 27145) ve Euro 7** | `websearch` 429, `iso.org` 403, EUR-Lex boş gövde | Bellekten sınıf tablosu **yazılmayacak** — bu tam olarak "makul görünen yanlış cevabın cevap olmaktan kötü" olduğu içerik |
| 4 | **ISO 26262-6 windowed/question-mark watchdog** maddesi | Standart metni okunamadı | Donanım özelliği doğrulandı, **madde atfı yapılmamalı** |
| 5 | **IEC 61508 tool qualification** maddesi | `webstore.iec.ch` 404 | **Madde numarası uydurulmayacak** |

---

# BÖLÜM B — YEREL ENVANTER (`data/` klasörü)

## B.1 Genel

```
data/                                    1.123 dosya · 384 MB
├── dbc/          229 dosya   32 MB   219 .dbc + catalog/manifest/LICENSES
├── diagnostics/    19 dosya   61 MB   PROVENANCE.md + 9 DB (JSON) + 6 CSV ikizi
├── traces/        217 dosya  289 MB   5 alt proje (marine/*)
├── knowledge/     601 dosya  2,4 MB  dtc_procedures/*.json
└── golden_traces/  56 dosya  232 KB  cases/*.json + schema.json
```

## B.2 `data/diagnostics/` — en olgun varlık, ama 4 sorun

| Sorun | Detay |
|---|---|
| 🔴 **Dört tutarsız DBC dosya sayısı** | **219** (disk) / **196** (audit md) / **186** (`catalog.json`) / **218** (`dbc_sync_report.json`). Hiçbir CI kapısı listelenmemiş dosyaları denetlemiyor |
| **J1939 severity: 22 farklı dize** | `MEDIUM` 8.515 · `MEDIUM`-dışı 14 farklı yazım (`"CHECK AT NEXT STOP"`, `"Critical"`, `"critical"`, `"Serious"`, `null`×172…). **Çapraz referans yok, ratchet yok** |
| **GM Mode $06 URL'si 404** | `PROVENANCE.md`'deki `final_rev1.pdf` diye dosya yok; gerçek ad **`final_dm.pdf`**, host `gsi.ext.gm.com` (çalışıyor), `gsitlc.ext.gm.com` ölü |
| **TCS gürültüsü** | 4 ayrı, birbiriyle tutarsız sayı: Mode $06 256/247/193 · şikâyet 4.388/4.459/**8.918** (ölçülen `odi_number` sayısı) · `.dbc_sync_report.json` `local_after: 218` |

**Korunan güçlü yanlar:** `dtc_database.json` 14.352 kayıt, **%100 severity doluluğu** · 33,5 MB'lık `root_cause_graph.json` 53 düğüm · `telemetry_thresholds.json` 7 sinyal **fail-closed** (`source_ref` zorunlu) · `PROVENANCE.md` 314 satır kaynak sicili · GM Mode $06 PDF'leri **HTTP 200, 651.885 bayt** canlı.

## B.3 `data/knowledge/` — yapısal olarak kusursuz, içerik olarak zayıf

- 601 dosya, **tek commit** (`6590632`) ile toplu yüklenmiş — elde yazım/üretim ayrımı yapılamıyor
- **4 üretici kalıbı** kanıtlandı: (A) 200 SPN · (B) 150 SPN · (C) 150 oto-şablon · (D) 101 elle yazılmış
- 🔴 **116 dosyada** `symptoms == ["Motor arıza lambası"]` (kodla ilgisiz genel metin)
- 🔴 **77 tam şablon stub** (B ve C ailelerinin %100'ü)
- 🔴 **150 dosyada** `safety_note` eksik ölçüm adımı
- 🔴 **32 dosyada 26 tekrar**: placeholder hedefi (`"<KOD> İlgili Komponent"`) aynı adımı iki kez
- 🔴 **Kazırma artıkları**: `P0A80.json:6` bir **kaynak başlığını** belirti alanında; `P0A80.json:16` kaynak metnini soru alanında; `P0005.json` belirti alanında **onarım prosedürü cümleleri**
- `schema_version: 1` · `pass_next`/`fail_next` yalnız **4 farklı değer** · `kind` alanı **%100 `yes_no`** (başka tip hiç kullanılmamış)
- **Hiçbir provenance alanı yok** — `ALLOWED_TOP_FIELDS` teknik olarak ek alanı reddediyor

## B.4 `data/traces/` — gerçek formatlar uzantı yanıltıyor

| Uzantı | Gerçek format |
|---|---|
| `.raw` | **Metin**, canboat raw CSV `ts,src,pgn,A,B,len,b1..bn` — ikili **değil**, NMEA-0183 **değil**. DLC 26'ya kadar çıkabiliyor |
| `.all` | canboat FAST (`# format=FAST`, ISO-TP FP devamları) |
| `.log` | **Karışık**: canboat metni + Digital Yacht iKonvert `!PDGY,…` base64 + candump |
| `.pcap` | **Gerçek pcap**, LINKTYPE 227, sabit 32 bayt adım, ~50.000 paket |
| `.ebl` | 🔴 **İkili ama SABİT 20 BAYT KAYIT DEĞİL** — NGT-1 kaçış-escape'li akış + araya serpiştirilmiş başlık kayıtları |
| `.dle` | Yacht Devices YDNU — ikili, **29 bayt** iddiası kamu kaynağından doğrulanamadı |
| `.trc` | PEAK PCAN-View metin izi (`;$STARTTIME=` OLE tarih) |
| `.in/.out/.err` | canboat analyzer **golden-regresyon üçlüleri** — `PLAIN`/candump/Actisense üç alt biçim; `.err` çoğunlukla boş |

**Haritası doğrulanmış senaryolar:** Project Haris = **SINILIND** (Estonian Maritime Academy eğitim gemisi, Stentor 54) + **ARTHUR** bench (NAVI-TRAINER Pro 5000, NMEA-0183), 2026-08-03→08-17, 144.157 satır · MITO 31 = RIB, 2 zamanlı hızlı planing tekne, 3 senaryo (free-drift, **düz koşu seakeeping**, 2 dönüş manevrası), ~630.000 NMEA-0183 cümlesi, **koordinat 40°43.8′N / 014°25.1′E** · mito31 `Log_NMEA_2000_turn.TXT` **367.391 satır, 2 motor** (sol/sağ)

**Lisans:** canboat Apache-2.0 (ama PGN DB NMEA telifli) · OpenSkipper **GPL-3.0** · MITO 31 CC BY 4.0 · Project Haris CC BY 4.0 (metadata `"license":"mit"`) · **Hiçbir veri kümesinde `LICENSE` dosyası yok** — yalnız `openskipper_samples/LICENSE-note.txt`

## B.5 `data/golden_traces/` — iki toplu üretim partisi

- 55 vaka, **54 kalibrasyona uygun** (1 taslak: `volvo_penta_d4_300_nonstart.json`)
- **`trace_ref` 55/55 `null`** — hiç CAN trace kanıtı yok
- **`actual_fault` = `symptom`'un kopyası** (4 dosyada doğrulandı) — yükleyici gerçek saha metni ile KB açıklaması kopyasını **ayırt edemiyor**
- 🔴 **6 vaka sahte kodlara dayanıyor:** `N2K_IMPELLER`, `N2K_EXHAUST_ELBOW`, `N2K_HEAT_EXCHANGER`, `N2K_PROP_SLIP`, `CAN_TERM_60`, `CAN_VOLT_FAULT` — yalnız `diagnostic_copilot.py:1534-1616` içindeki satır-içi KB anahtarları; `data/diagnostics/**` grep = **0**
- 🔴 **4 `case-n2k-*` vakası `domain: PASSENGER`, `make: Volkswagen`, `model: Passat`** — içerik ise deniz suyu/egzoz/makine soğutma. **Denizcilik arıza bilgisi PASSENGER altında.** MARNE domain'inin tek vakası **boş taslak**
- 🔴 **18/54 `observed_behavior` cümle ortasında kesik** (örn. `"...Rölanti Basıncı: 5.0 - 12.0 kPa | Tıkalı Limi"`) — mevcut kesik ratchet bu alanı **kapsamıyor**
- **`signals_of_interest[].name` = `EngineSpeed` 31/54 (%57)** — HV batarya ön-şarj, MSD oturma, CAN sonlandırma direnci gibi ilgisiz arızalarda bile
- `make` kanonikleştirme yok: `Mercedes` ↔ `Mercedes-Benz` — `golden_similarity` 0,10 ağırlıklı `make` terimini sessizce **0** puanlıyor
- **Sinyal adı uyuşmazlığı:** vakalar `CoolantTemp`/`BoostPressure` ↔ `telemetry_thresholds.json` `EngineCoolantTemp`/`TurboBoost` ↔ `root_cause_graph.json` `boost_pressure`/`turbo_boost` — `golden_similarity.py:98-102` alias kullanmıyor ⇒ çapraz alt sistem Jaccard **yapısal olarak 0**

---

# BÖLÜM C — PROTOKOL VE STANDART KEŞİFLERİ

## C.1 J1939 — 22 FMI tanımı, severity eşlemesi, ölçekleme

**FMI tablosu birebir SAE J1939-73 Rev SEP2006 Appendix A'dan** (UNECE'de ücretsiz: `https://unece.org/fileadmin/DAM/trans/doc/2006/wp29/WP29-140-06e.pdf`)

**En sık yanlış alıntılanan 5 tanım:**

| FMI | Doğru | Yaygın hata |
|---|---|---|
| 0 | *Above Normal Operational Range — **Most Severe*** | "İyi" sanılır; **en şiddetli** taşma |
| 4 | *Voltage Below Normal, or Shorted To **Low** Source* | spnfmi.com **"High"** yazıyor — olgusal hata |
| 12 | *Bad Intelligent Device — **ECU değiştirilir***, haberleşme arızası hariç | "kötü sensör" diye okunur |
| 16/18 | 16 = **ÜSTÜ (yüksek)** orta · 18 = **ALT (düşük)** orta | Sık değiştirilir ve "High" diye düzleştirilir |
| 19 | *Received Network Data In Error* — **alınan değer zaten hata göstergesiydi**; **ölçüm yapan modül gönderene bağlı** | FMI 2 ile karıştırılır; teşhis yönü ters çevrilir |

**Severity: tek standart mekanizması 4 lamba (her biri 2 bit)**

| Lamba | SPN | Anlamı |
|---|---|---|
| MIL | **1213** | *"relay **only emissions-related** trouble code information"* |
| Red Stop | **623** | *"severe enough condition that it **warrants stopping the vehicle**"* |
| Amber Warning | **624** | *"reporting a problem… but **the vehicle need not be immediately stopped**"* |
| Protect | **987** | *"**most probably not electronic subsystem related**"* |

**Önerilen kanonik 5-değerli eşleme:** `CRITICAL_STOP` (RSL On/hızlı flash + SPN 5246 ≥ L3) > `HIGH` (AWL On/flash) > `MEDIUM` (yalnız MIL On) > `LOW` (MIL flash / FMI 15-17) > `INFO` (lamba yok veya yalnız Protect). **Bilinmeyen `severity_raw` ⇒ fail-closed seviye 5 + istisna listesi — asla seviye 1'e.**

**Ölçekleme tuzakları:** çözünürlükler yalnız **2'nin kuvveti** · offset `0` veya tam ±yarım aralık (**"0.4 offset" J1939 değil, J1587/legacy kalıntısı**) · 🔴 **16-bit UINT + negatif offset'te `0xFFFF` = üst sınır, `-1` DEĞİL** (sign-extend etmeyin) · sıcaklık SLOT'unda offset tam **`-273`** (tam sayı, -273.15 değil) · 🔴 **NA göstergesi `FE`–`FF` bloğudur**, `0xFF` ile sınırlı değil · J1939-71 §5.4.9: **kullanılmayan bitler 1'dir, 0 değil** · **DBC start-bit ≠ J1939 start pozisyonu** — karıştırmak her sinyali bir bit kaydırır

**`canmatrix` riski:** canmatrix issue #242 — J1939 DBC'de `VFrameFormat`'ı `3` (`ExtendedCAN_FD`) yerine `4` (`J1939PG`) yazıyor. **canmatrix bugün J1939 DBC'yi doğru round-trip edemiyor.**

## C.2 OBD modları (SAE J1979 tam gövdesinden)

| Mod | Ad | Doğrulama notu |
|---|---|---|
| `$01` | Current Powertrain Diagnostic Data | 4 veri baytı A–D; destek bit maskesi `$00/$20/…/$E0` |
| `$02` | Freeze Frame | PID `$02` = FF'yi tetikleyen DTC; `$0000` ⇒ FF yok |
| `$03` | Emission DTC | Bayt2 = sayı, sonra DTC başına 2 bayt |
| `$04` | Clear/Reset | Motor **çalışmıyor** olmalı (NRC `$22`). 4 baytlık desen ISO 15031-5:2013+/J1979-2'de |
| `$05` | O2 Monitor | 🔴 **CAN'da TANIMSIZ** — `7F 05 11` |
| `$06` | Monitor Test Results | **İki farklı düzen:** J1850 **7 bayt** (TID/CID) · ISO 15765-4 **9 bayt** (OBDMID/TID/UASID) |
| `$07` | **Pending DTC** | 🔴 "Farklı modül" değil — **fiziksel adresleme** |
| `$08` | Control of On-Board Test | 🔴 UDS'de **yok**; halefi `0x2F` |
| `$09` | Vehicle Information | `$02`=VIN, `$06`=CVN |
| `$0A` | **Permanent DTC** | 🔴 **J1979/ISO 15031-5'te YOK** — WWH-OBD / J1979-2 |

**Mode $06 kritik noktaları:**
- 🔴 **`TV = MIN = MAX = 0x0000` = "monitör hiç çalışmadı", geçme DEĞİLDİR** — decoder Pass bildirmeden önce bunu özel ele almalı
- **OBDMID blokları** `$20`'nin katları destek bit maskesidir: `$01–$10` O2 · `$21–$3D` kat/EGR/EVAP · `$41–$50` O2 ısıtıcı · `$61–$74` ısıtılmış kat/ikincil hava · `$81–$84` yakıt sistemi · `$A1–$AD` misfire · `$E1–$FF` üretici
- **Normatif araç davranışı:** *"Prior to requesting OBD Monitor test results the external test equipment **shall evaluate if the monitor is complete**"* ⇒ Mode $06 istekleri `$01/$01` hazır bitlerine **bağlanmak zorunda**
- 🔴 **Brief'teki "11/13/15 bayt kayıt" ve "AVAIL/TC/CML" bulunamadı** — doğrulanmış 9 (CAN) ve 7 (non-CAN) bayt

**GM PDF'leri (doğrulanmış canlı URL'ler):**
- `https://gsi.ext.gm.com/gmspo/mode6/pdf/GM%20CAN%20mode%20%2406%20data%20final_dm.pdf` (HTTP 200, 651.885 B)
- `https://gsi.ext.gm.com/gmspo/mode6/pdf/GM%20Class2%20mode%20%2406%20data%20final_dm.pdf`
- `https://gsi.ext.gm.com/gmspo/mode6/index.html` (HTTP 200)

## C.3 NMEA-2000 ve deniz log formatları

**canboat v8.3.0 (2026-09-25) — 630 PGN / 5.034 alan.** Sizin kopyanız 620/4.105.

**Fast-packet algoritması (`common/common.h` birebir):**
```
bucket 0 = 6 yük baytı (data offset 2); bucket 1..31 = 7 bayt (offset 1)
data[0] = (index << 3) | (order & 0x07)
maks yük = 6 + 7*31 = 223 bayt; ISO-TP ile 1785
```

**`canboat convert --to` çıktı seçenekleri:** yalnız `plain | json | text | ydwg02 | actisense | actisense-ebl`. 🔴 **`.asc`/`.blf`/candump/pcap çıktısı YOK.** Girdi olarak ise `.pcap`/`.pcap.gz`/`.nif`/`.ebl`/candump×2/PCAN-View/Actisense/YDWG-02/ikonvert/Airmar/Chetco/Garmin CSV okuyor. ⇒ **Evrensel ön uç olarak kullanın, kuyruğu kendiniz yazın (~100 satır Python).**

**Dönüştürücünün yapması gerekenler — AGENTS.md §2.3 riski:**
1. **Fast-payload'ı 8 bayta geri böl** (§2'deki algoritma) — `.asc`/`.blf` "ham tek kare" konteynerlerdir
2. **PLAIN/YDWG-02/candump/PCAN-View/`.ebl`** zaten ham kare — yeniden bölme, sadece yeniden zaman damgala
3. 🔴 **Tarih uydurma yasağı:** Actisense ASCII ve YDWG-02 **yalnız gün saati** taşır (tarih yok) ⇒ tarihi uydur ve `time_base: assumed` işaretle. **`.dle`de mutlak zaman damgası hiç yok** — türet ve işaretle
4. **`PLAIN_MIX_FAST` modu** — tek yakalamada önceden birleştirilmiş FAST satırları + ham devam kareleri karışabilir; `plain` ilk >8 bayt satırında kilitlenir
5. **Dosya başına provenance sidecar:** `source_format`, `input_sha256`, `frame_count`, `timestamp_policy ∈ {exact, relative_to_header, assumed_date, none}`, `lossy`

**P2K çerçeve haritası (yerel korpustan doğrulandı):** `127488` motor hızlı (10 Hz) · `127489` motor dinamik (2 Hz, soğutma K 0.01) · `127493` şanzıman (10 Hz) · `127508` batarya (1,5 Hz) · `130311/130312/130314` çevre/sıcaklık/basınç · `126983` **Alert** (21 alan — `alertPriority`, `alertState`, `acknowledgeStatus`, `escalationStatus`, `thresholdStatus`) · `065280+` OEM proprietary (`mercuryEngineTelemetryLowSpeed` → `malfunctionIndicator`, `intakeAirTemperature`, `exhaustGasTemperature`)

> 🔴 **Her sinyali `(instance|sid, src)` ile anahtarlayın, PGN'e göre değil** — yoksa port/starboard motorları ve iki bataryayı sessizce birleştirirsiniz.

## C.4 CAN donanım, replay ve log formatları

**python-can 4.6.1 (2025-08-12) — 🔴 `LGPL-3.0-only`, `py>=3.10`, 22 arayüz backend.**

**Raporun en önemli performans/hukuki bulgusu:** `BLFReader` *"Only CAN messages and error frames are supported. **Other object types are silently ignored.**"* ve `MF4Reader` yalnız kendi `MF4Writer` çıktısını okuyabilir. ⇒ **python-can okuyucuları CAN dışı her şeyi sessizce düşürür — AGENTS.md §2.3 ihlali.** Reader `skipped_object_types` sayacı + fail-closed olmalı.

**Diğer bulgular:**
- 🔴 **`python -m can.bridge` choke-point'i baypas eder** — policy'siz iki bus arası iletim. `cansend`/`cangen` de aynı. **Kurulumda `udev`/servis kısıtı gerekir**
- 🔴 **Vector `flush_tx_buffer()` kasıtlı olarak "high voltage message (ID=0, DLC=0, no data)" gönderir** — TX politikasında yasaklı ilkeler listesine alın
- 🟢 **`BusCapabilities` alanları:** `fd_dialect` (BOSCH/ISO), kanal başına `fd_clocks: set[int]` (PCAN {20,24,30,40,60,80} MHz ≠ Vector 80 MHz), `rx_queue_size` 2'nin kuvveti (CAN 16–32768 / **FD 8192–524288**)
- 🟢 **`can.cli.add_bus_arguments` / `create_bus_from_namespace`** (4.6.0) — CLI argümanlarını yeniden yazmayın
- 🟢 **`Notifier.find_instances(bus)`** — RX aboneliklerinin yaşam döngüsü kaydı
- 🟢 **PCAN, Linux SocketCAN'da kernel 3.4'ten beri yerel** — PEAK kullanıcıuzay kitaplığı gerekmez
- 🟢 **`gs_usb`/candleLight** (VID:PID `0x1D50:0x606F`) tek çekirdek-sürücüsüz taşınabilir yol — **ama *"Message filtering is not supported"***

**Replay komut künyesi (doğrulanmış):**
```bash
sudo modprobe vcan && sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0
candump -L can0 > run.log                        # kompakt .log (canplayer girdisi)
asc2log run.asc > run.log                       # .asc -> compact (ZORUNLU ön adım)
canplayer -I run.log vcan0=can0                 # arayüz remap
canplayer -I run.log -s 0.1 -g 2 vcan0=can0      # boşluk kıstırma
python -m can.player -i socketcan -c vcan0 -g 0.002 -s 0.5 run.asc
```
🔴 `canplayer` **`.asc` değil `.log` alır** (man: *"replay a **compact CAN frame logfile**"*).

**ASAM MDF 4.3.0 (23 Eylül 2025):** *"de-facto standard for measurement & calibration systems (MC-systems)"* · *"allows storage of **raw measurement values and corresponding conversion formulas**; therefore raw data can still be interpreted correctly"* · 4.3.0 GNSS + ham sensör + **SOME/IP** ekledi, **alternatif sıkıştırma algoritmaları** tanıttı, tam geriye uyumlu · 20 yazarlı grup (Audi, BMW, dSPACE, ETAS, Mercedes, **Porsche**, **Vector**…) · `asammdf` 8.8.27

## C.5 UDS / ISO 14229

**⭐ `0x29 Authentication` (RSID `0x69`):** *"provides a mechanism for the client to **prove its identity**… It is an **alternative for SecurityAccess** service."*
**⭐ `0x84 SecuredDataTransmission` (RSID `0xC4`):** *"allows the client to **secure UDS communication** using either **encryption or signature**."*
**0x83 AccessTimingParameter** — ISO 14229-1:2020'de **KALDIRILDI**.

**Sertifika doğrulama NRC'leri (fail-closed durum makinesi için hazır taksonomi):**
`0x34` authenticationRequired · `0x35` invalidKey · `0x36` exceedNumberOfAttempts · `0x37` requiredTimeDelayNotExpired · `0x38` secureDataTransmissionRequired · `0x39` secureDataTransmissionNotAllowed · `0x3A` secureDataVerificationFailed · `0x50`–`0x57` sertifika (invalid time period / signature / chain of trust / type / format / content / scope / certificate) · `0x58` ownership verification failed · `0x59` challenge calculation failed · `0x5A` setting access rights failed · `0x5B` session key creation/derivation failed · `0x5C` configuration data usage failed · `0x5D` de-authentication failed

**DID kataloğu (ISO 14229 Annex F) — `data/diagnostics/uds_did_database.json` 68 DID'in kaynağı:** `0xF180` BootSoftwareID · `0xF181` ApplicationSoftwareID · `0xF183/4/5` fingerprint'ler · `0xF186` ActiveDiagnosticSession · `0xF187` ManufacturerSparePartNumber · `0xF189` ECUSoftwareNumber · `0xF18A` ECUSoftwareVersionNumber · `0xF190` **VIN** · `0xF191` ECUHardwareNumber · `0xF192` SystemSupplierIdentifier · `0xF193` ECUManufacturingDate · `0xF194` **ECUSerialNumber** · `0xF195` SupportedFunctionalUnits · `0xF197` VIN (ISO 3779) · `0xF198` ECUHardwareVersionNumber · `0xF19A` SystemSupplierECUHardwareVersionNumber · `0xF19B` SystemSupplierECUSoftwareNumber · `0xF19C` SystemSupplierECUSoftwareVersionNumber · `0xF19D` ExhaustRegulationOrTypeApprovalNumber · `0xF19E` SystemNameOrEngineType · `0xF19F` **RepairShopCodeOrTesterSerialNumber** · `0xF1A0` ManufacturerSoftingNumber · `0xF1A2` CalibrationRepairShopCode · `0xF1A5` **EngineSerialNumber** · `0xF1A6/7/8` SystemConfigurableDID/VID/VDS

**J2534 (PC ↔ araç pass-thru):** v04.04'te **13 fonksiyon** · protokol kimlikleri `0x05` CAN · `0x06` **ISO 15765 DoCAN** · 🔴 **ISO15765 kanalında `FLOW_CONTROL_FILTER` ZORUNLU** — yoksa `ERR_NO_FLOW_CONTROL (0x17)`; J2534 entegrasyonundaki en yaygın tek hata · `PASSTHRU_MESSAGE_DATA_SIZE = 4128` · `OpenJ2534` (425★), `j2534-api` (pip, yalnız Windows/32-bit), **`SardineCAN-Arduino` (tek açık kaynak J2534 cihazı)**, `OpenVehicleDiag` (Rust 1005★)

**DoIP (ISO 13400-2):** header `Version|InverseVersion|PayloadType|PayloadLength` (8 B) · sürüm `0x03` = 2019 · payload `0x8001` = **Diagnostic message (UDS taşıyan)** · routing activation `0x00` Default / **`0x01` WWH-OBD** / `0xE0` Central security · yanıt `0x10` başarı · **port TCP+UDP 13400, TLS 3496** · *"DoIP is a transport/session wrapper; **there is no separate DoIP diagnostic session**"* · 🔴 **Mantıksal adres aralıkları DOĞRULANAMADI** — Wireshark bunları UAT tablosu olarak sevk ediyor, sabit kodlamayın

**CANopen / CiA 301:** bit zamanlaması **10–1000 kbit/s** · ⭐ **CiA 301 v4.2.0 "PAS" (Publicly Available Standard), ücretsiz CiA login'iyle** · ⭐ **EDS = CiA 306 = düz INI** (~200 satırlık parser, **lisans engeli yok**) · XDD = CiA 311, ISO 15745 uyumlu XML · `python-canopen` 2.4.1 MIT, **ama uyumlu master DEĞİL, CANopen-FD desteği YOK**

## C.6 ISO-TP kenar durumları (doğrulanmış, `net/can/isotp.c`)

| # | Kenar durumu | Doğru davranış |
|---|---|---|
| 1 | BS = 0 | FC karesi yok, tüm PDU tek patlamada |
| 2 | ⭐ **STmin 0x80–0xF0 rezerve** | Çekirdek **`0x7F`'e kırpıyor — fail-safe = EN YAVAŞ.** "En hızlı" yorumu güvenlik hatası |
| 3 | STmin 0xF1–0xF9 kaçışı | **100–900 µs** = `(v − 0xF0) × 100 µs` |
| 4 | CF sequence | FF sonrası ilk CF **SN=1**; 4 bit, `(sn+1)%16` |
| 5 | SN uyuşmazlığı | `EILSEQ`, RX IDLE'a döner, kare düşer |
| 6 | FC.OVFLW (FS=0x2) | Alıcı tampon taşması; gönderici `EMSGSIZE` |
| 7 | ⭐ `CAN_ISOTP_WAIT_TX_DONE` | 🔴 **Güvenlik ağı geçidi için ZORUNLU** — varsayılanda `write()` kuyruğa alır ve döner, **TX hataları sessizce kaybolur** |
| 8 | ⭐ **CAN FD SF_DL kaçışı** | `tx_dl > 8` iken tek kare **zorunlu** `SF_DL = 0x00` kullanmalı |
| 9 | ⭐ **CAN FD "length-optimisation" tuzağı** | İçerik 7. baytta bitiyorsa `len` **7** olmalı, 8 değil. İhlal `EBADMSG` |
| 10 | CAN FD dolgu | **ZORUNLU**; 0-8→8, 9-12→12, 13-16→16, 17-20→20, 21-24→24, 25-32→32, 33-48→48, >48→**64** |
| 11 | 4095 bayt PDU | 🔴 **586 CAN karesi** — `txqueuelen` 4000 gerekli, aksi halde `-ENOBUFS` |
| 12 | 0x78 yeniden-birleştirme sırasında | ISO 15765-2'de mekanizma **YOK**; pratikte ECU iptal edip tek karede yanıt verir |

## C.7 CAN hata işleme

**TEC/REC eşikleri (`include/uapi/linux/can/error.h`'den doğrulandı):** 0–95 error-active · **96–127 error-warning** · **128–255 error-passive** · **≥256 bus-off**. 🔴 **`data[5]` rezerve** — kullanmayın.

**Hata çerçevesi 8 bayt:** sınıf `can_id` bit maskesinde (`0x01` TX timeout · `0x02` LOSTARB · `0x04` CRLT · `0x08` PROT · `0x10` TRX · `0x20` ACK · **`0x40` BUSOFF** · `0x80` BUSERROR *"(may flood!)"* · `0x100` RESTARTED · `0x200` CNT→`data[6]=TEC`, `data[7]=REC`)

**Bus-off politikası:** 🔴 **`restart-ms` 0 bırakın** — otomatik yeniden başlatma kararı operatör/politika kararı olmalı · `CAN_RAW_ERR_FILTER` ile **hata/bus-off aboneliğini açıkça isteyin** (varsayılan **kapalı**) · hata çerçevesi seline hız sınırlayıcı · yeniden-armaları `CAN_ERR_RESTARTED` (0x100) / `CAN_ERR_CRTL_ACTIVE` (`data[1]` 0x40) **kenarlarına** bağlayın, `sleep`e değil · 🔴 `presume-ack` **kullanmayın** (ACK uydurur) · ⭐ **`listen-only on` zorunlu** — ACK etmeyen araç **tüm aracı error-passive yapar**

## C.8 SecOC ve araç kimlik doğrulama

**SecOC (AUTOSAR):** ⭐ **CAN yükünün ayrılmış bir kısmında** MAC (ör. 8 baytın 28 biti) · **grup anahtarı** · simetrik kısaltılmış MAC, imza değil (*"digital signatures are not applicable in CAN due to excessive computational and bandwidth requirements"*) · 🔴 **BİLİNEN ZAYIFLIK: kaynak kimlik doğrulaması YOK** — *"these approaches thus do not protect against compromised ECU"* · freshness value mekanizması **DOĞRULANAMADI**

> **Kapsam notu:** SecOC **uygulama PDU'larını** korur, teşhis hattını değil. Araç kimlik doğrulaması **`0x29`/`0x84` ile** yapılır ve şimdi uygulanabilir.

## C.9 GB/T 27930, ISO 6469, UN R100

**Çin EV standartları:** 🔴 **GB/T 27830 bir EV standardı DEĞİLDİR** · **GB/T 27930-2015 İLGA EDİLDİ (2024-04-01)** · **GB/T 27930-2023** güncel (CAN 2.0B, 250 kbit/s, 29-bit extended, J1939-21 PDU, little-endian, **pozitif akım = deşarj**) · ⭐ **GB/T 27930.2-2024** (GB/T 20234.3 iletişimi) · **GB 44263-2024** zorunlu güvenlik (DC CP pull-up 12 V ±0,6 V; yük düşürme kuralı) · ⭐ **GB/T 39086-2020** — BMS'e özel, ISO 26262'yi item olarak uygular, **FTTI'yi tanımlar**, **güvenli durum = HV döngüsünü kes**

**UN R100 (04 serisi, OJ 2021/2190) — atıf alınabilir eşikler:** izolasyon **≥100 Ω/V** · **≥7 MΩ @ 500 VDC** (Ek 9B) · yakıt hücresi DC bus'ta IRM **zorunlu** + uyarı, **Ek 6**'ya göre doğrulanır · şarj kuplajı istisna · 🔴 **`presume-ack`/`restart-ms` benzeri tuzak yok** ama *"it may be necessary to **deactivate** the on-board isolation resistance monitoring system"* (ölçüm sırasında) · ⭐ **5 dakikalık ön uyarı sinyali** — R100'de uygulanabilir en iyi teşhis arayüzü

**UN R155 (OJ 2021/387) §11.3/§11.4:** tehdit listesi **"malicious diagnostic messages"** ve **"malicious proprietary messages"** içerir ⇒ **CAN/UDS karesi enjekte eden bir teşhis aracı tam olarak bu sınıftır**

**ISO 15118:** Gen-1 **TLS 1.2** + **secp256r1** · Gen-2 (-20) **TLS 1.3** + **secp521r1/Curve448** · 🔴 **kanal kurulmadan önce sürüm değiş tokuşu olmadığı için secp256r1-only sertifika araç tarafında fatal alert üretir** (CharIN 2026-04-22 rehberi) · **DIN 70121 ile ISO 15118-2 UYUMLU DEĞİLDİR** (*"not interoperable"*) · GB/T 27930 ile **kardeş standart, katmanlı değil** (farklı veri hattı katmanları)

**EIS araştırması (Sandia/NHTSA 2024):** Hızlı EIS **22,5 dk** (tek hücre) / **29,2 dk** (1S4P) · VOC 7,1/17,3 · H₂ **−0,4** (işe yaramaz) · 🔴 **Standart DEĞİLDİR** — AGENTS.md §2.3 gereği sentetik işaretlenmeli, güvenlik eylemi tetiklememeli

---

# BÖLÜM D — GÜVENLİK DEĞİŞMEZLERİ DENETİMİ

## D.1 8 değişmez — hüküm tablosu

| # | Değişmez | Hüküm | Dayanak |
|---|---|---|---|
| 1 | Tek denetimli TX choke-point | **BELİRTİLMEMİŞ** | ISO 13849 Cat 3/4 *"no single component failure is permitted to cause the loss of the safety function"* — ilke standart-zeminli, "choke-point" kelimesi değil |
| 2 | Fail-closed (hata/zaman aşımı/CRC/sayac/watchdog) | ✅ **EN GÜÇLÜ** | MD 2006/42/EC: *"The emergency stop function must be available and operational at all times, regardless of the operating mode"* |
| 3 | Uydurulmamış telemetri | ⚠️ **YANLIŞ ADLANDIRILMIŞ** | ISO 26262 sözlüğü **fault→error→failure**; hatasız bir decoder'ın uydurduğu değer **arıza değil** ⇒ SPFM/LFM/HFM **yardımcı olamaz**. Doğru ev **SOTIF (ISO 21448) + güvenlik** |
| 4a | E-Stop HMAC-SHA256 token | ✅ **Standart-üstü** | RFC 2104 §6: *"the birthday attack on HMAC is **totally impractical**"* · 🔴 **Keyless collision *"approaching feasibility"*** |
| 4b | 800 ms monoton watchdog lease | 🔴 **2 somut kusur** | aşağıda D.2/D.3 |
| 5 | Renderer güvenliği devre dışı bırakamaz | ✅ **EN İYİ KANITLANMIŞ** | MD 2006/42/EC §1.2.4.3 birebir: *"**disengaging the device must not restart the machinery but only permit restarting**"* |
| 6 | Hız kilidi provenance | 🔴 **EN ZAYIF — ÇÜRÜTÜLDÜ** | aşağıda D.4 |
| 7 | Bağlanmadan önce kusur incelemesi | ✅ **DOĞRU, ATIF YANLIŞ** | ISO 26262-8 hedefi doğru ama mekanizması **niteliklendirme dosyası + freedom-from-interference**, kod-inceleme kapısı değil |
| 8 | Tam çevrimdışı AI, güvenlik yolunda LLM yok | ✅ **DOĞRU SONUÇ, YANLIŞ GEREKÇE** | Hiçbir standart AI'yı yasaklamıyor. **Gerçek argüman ISO 26262-8 araç nitelendirmesidir:** bir LLM'nin *determinizmi, sınırı, belgelenebilirliği ve analiz edilmiş arıza modu yoktur* |

## D.2 🔴 Üç somut kod kusuru (bulgu değil, tespit)

| # | Kusur | Kanıt | Düzeltme |
|---|---|---|---|
| **F1** | 🔴 **Ephemeral sır yedeği fail-OPEN** — sır sağlayıcısı erişilemez olduğunda hata loglanıp **sistem armalı kalıyor**, kimlik doğrulama **sıfıra** düşüyor | `src/safety/estop.py:199-203` | Sır deposu hatası ⇒ `ESTOP_TRIGGERED` |
| **F2** | 🔴 **`CLOCK_MONOTONIC` suspend'u SAYMAZ** — bir TX lease'i suspend boyunca **sessizce donuyor** | man7 `clock_getres(2)`: *"This clock does not count time that the system is suspended."* | `CLOCK_BOOTTIME` kullanın ya da resume'da `SAFE_STATE` zorlayın |
| **F3** | 🔴 **Watchdog aynı Python sürecinde** — `/dev/watchdog`, `WDIOC_*`, `question-mark` **grep: sıfır eşleşme**; `windowed` yalnız ISO-TP eşleşmesi | repo geneli taraması | Ya dürüst mimari iddiası (*"Cat-2 muadili, aynı süreç, tek zaman tabanı"*) ya da `/dev/watchdog`'u ikinci katman olarak sürün |

> ⭐ **Ek tasarım kusuru:** `watched` özne ile `watchdog` **aynı süreçte**. GIL aç bırakmayan saf-Python bir spin loop watchdog thread'ini **aç bırakır** — tam olarak lease'in var olma sebebi olan koşul. *(Mühendislik akıl yürütmesi, ölçülmüş iddia değil — GIL aç bırakma testiyle doğrulayın.)*

## D.3 Saat tablosu — TX lease için hangisi

| Saat | Ayarlanabilir? | Geriye atlar? | Suspend sayar? | Frekans ayarlanır? | Lease için doğru mu? |
|---|---|---|---|---|---|
| `CLOCK_REALTIME` | **Evet** | **Evet** (NTP, admin) | Hayır | Evet | 🔴 **HİÇBİR ZAMAN** — sadece log/denetim |
| `CLOCK_MONOTONIC` | Hayır (`clock_settime` **başarısız olur**) | Hayır | 🔴 **HAYIR** | **Evet** | Yalnız süreç hiç suspend olmayacaksa |
| `CLOCK_MONOTONIC_RAW` | Hayır | Hayır | Hayır | Hayır | İzleme/tracing |
| ⭐ **`CLOCK_BOOTTIME`** | Hayır | Hayır | **EVET** | Evet | ✅ **Suspend'dan geçmesi gereken 800 ms lease için doğru seçim** |

**Eksik kontroller:** (a) başlangıçta `time.get_clock_info('monotonic')` ile `monotonic=True, adjustable=False, resolution ≤ 0.050` doğrulaması, yoksa `arm_tx` reddi; (b) monoton delta `0`'a clamp; (c) `CLOCK_MONOTONIC` yoksa **duvar saatine düşme — `SAFE_STATE`**; (d) `mlockall()` (preallocate edilmiş tamponlar için); (e) `SCHED_DEADLINE` + `sched_getattr(SCHED_GETATTR_FLAG_DL_DYNAMIC=1)` ile **çekirdek-doğruluklu** deadline ölçümü.

**Anti-rollback ≠ anti-replay:** bir *reset* token için doğru özellik **anti-replay + nonce tek kullanımlık**, anti-rollback değil. RFC 6479 kayan pencere `[WB, WT]` referans alınabilir — **mevcut tasarımda yok.**

## D.4 🔴 Hız kilidi — iddia çürütüldü, yeni formülasyon

**"Plausibility check" standardize edilmiş bir terim DEĞİLDİR** ve tek başına **kanıtlanmış biçimde yenilir:**

| Bulgu | Kaynak |
|---|---|
| *"**Synthetic Sensor Spoofing**: 3 of 4 published process-based (plausibility) detectors defeated… not able to reliably learn physical properties"* | arXiv:2012.03586 |
| *"a system can be **perfectly attackable even if the plant is stable**"* · *"attacks may result in unbounded estimation errors **while remaining undetected**"* | arXiv:2005.08122 |
| *"if messages from some of the sensors are **even intermittently authenticated**, stealthy attacks could not result in unbounded state estimation errors"* | arXiv:2005.08122 |
| Doğru yeterli koşul: **redundant observability** (güvenlik indeksi ↔ tespit edilebilirlik) | arXiv:1805.02640 |
| 7 saldırı yolu, **103 spoofing vektörü, 77'si hiç düşünülmemiş** | arXiv:2205.04662 |
| CAN'ın **kimlik doğrulama/yetkilendirme/gizlilik kontrolü yok**, gönderen kimliği yok | arXiv:2308.04972, arXiv:2008.10941 |
| Fiziksel katman parmak izi: kanal %95,2 / ECU %98,3 — **%100 değil** | arXiv:1801.09011 |

> **Önerilen yeni formülasyon (I-6′):** Güvenlik açısından kritik bir kilit, **yalnız plausibility kontrolüne dayanamaz.** Her aday örnek `(source_class, freshness_monotonic_ns, integrity_verdict, transformation_history)` taşır. Yetkilendirme şunları **birlikte** gerektirir: (a) `source_class == PHYSICAL`; (b) bütünlük hükmü sağlam (E2E CRC + rolling counter); (c) **monoton** saatle ölçülen tazelik sınırı içinde; (d) **boş** interpolasyon/dışa değerlendirme geçmişi; (e) aynı büyüklüğün **fiziksel olarak bağımsız** en az bir ölçümüyle çapraz uyumu. **Sentetik/oynatma/simülatör değerler yapısal olarak `PHYSICAL` provenance kaydı dolduramaz** — bir in-process bileşenin kontrol ettiği bir `is_synthetic` bayrağı **buna yetmez** (arXiv:2603.10388: doğru format + cadence + kabul edilmişlik üretmek mümkün).

## D.5 Güvenlik mimarisi desenleri

| Desen | Bu platform |
|---|---|
| **ISO 13849 Cat 2** (tek kanal + teşhis) | ✅ **En yakın eşleşme** |
| Cat 3/4 (çift kanal + karşılaştırma) | ❌ Watchdog aynı süreçte; choke-point ve korunan işlev aynı güven alanında; **CCF puanı hiç hesaplanmıyor** |
| **CCF (common-cause failure)** — ISO 13849'da Cat 2/3/4 için **≥65** gerekir | 🔴 **Hiç puanlanmamış.** Somut CCF kalemleri: tek Python yorumlayıcı/OS (gateway+watchdog+E-Stop+protokol motorları), süreç-yerel tek sır, tek UI süreci |
| ASIL decomposition `A(B)+A(B)=B` | *"should not introduce a common point of failure"* — yükümlülük **standardda adı geçiyor, kontrol listesi yok** |

**Dürüst mimari iddia:** ⭐ **"ISO 26262 ASIL-B/D tasarım ilkeleri"** ⇒ **"tek paylaşılan güven alanında Cat-2 muadili, kanal-üzerinde-teşhis"**. Mevcut metin mimari iddiayı **fazla güçlü** söylüyor.

## D.6 ⭐ Kanıt zinciri ve test oracle'ları

**Kanıt mimarisi (RFC 4998 tabanlı):**
```
Yakalama → Normalize → Kural Motoru → Karar + Neden ID
   └─ segment HMAC-SHA256(prev_hash ‖ payload ‖ monotonic_ts)   [saat yoksa yazma yok]
        └─ oturum başına Merkle kökü + imzalı manifest (Sigstore/Cosign)
             ├─ in-toto attestation: kim hangi build'i, hangi korpus/kural sürümünü çalıştırdı
             │    └─ SLSA L2 predicate (v1.1 Retired, **v1.2 güncel**)
             ├─ Rekor katılım kanıtı
             └─ çevrimdışı doğrulayıcı CLI
```
🔴 **Zincir kırılması ⇒ `SAFE_STATE` + operatör alarmu.** 🔴 **Denetim izinin `DELETE` edilebilir bir Loki akışı olmasına izin verilmeyin.**

**16 metamorphic ilişki — en kritik 6'sı:**

| # | İlişki | Yakaladığı |
|---|---|---|
| MR-8 | **Zaman damgası değişmezliği** | Gizli duvar-saati okumaları; `ClockProvider` enjeksiyonunu zorlar |
| MR-10 | **Oynatma idempotansı** | Copilot'ta idempotent olmayan durum birikimi |
| MR-13 | ⭐ **Fault-injection saflığı** | **"Halüsinasyon" (uydurma) teşhisler** |
| MR-16 | ⭐ **Fail-closed closure** | Tabloda olmayan arıza `OK` vermemeli |
| MR-4 | Bayt-sırası takası **ilan** edilmelidir | DBC decoder'da sessiz endian hataları |
| MR-6 | Sinyal faktörizasyonu | Çakışan sinyal, yanlış PGN maskesi |

**CEM/fault-tree:** ⭐ **Yayımlanmış satır sayılmış bir CEM tablosu BULUNAMADI** (arXiv, Crossref, Zenodo'da sıfır — mühendislik CEM'leri dizinlenmeyen eklerde yaşıyor). ⇒ **Kendi CEM'inizi sürümlü, hash-imzalı bir artefakt olarak yayımlayın — bir ödünç tablodan *daha güçlü* kanıttır, çünkü *doğrulanabilir*dir.** İzlenebilirlik hedefi: **her CEM satırı ≥1 golden trace'ten erişilebilir VE ≥1 mutant tarafından öldürülebilir olmalı.**

**Test araçları:** `mutmut 3.8.0` · `hypothesis 6.168.1` + `HypoFuzz` · `pyperf` (`compare_to`, `--track-memory`) · `asv 0.6.6` (adım tespiti, git geçmişi üzerinden kademeli bozulma) · `pytest-benchmark 5.3.0` · 🔴 **`aiT` (AbsInt) C/C++ için statik WCET — Python için YOKTUR**; kanıt dosyasında dürüst olun

**Mutasyon testi ↔ ISO 26262:** 🔴 **Normatif eşleme YOKTUR.** MC/DC zorunludur; mutasyon skoru *tamamlayıcıdır*. İkisini de raporlayın.

---

# BÖLÜM E — PERFORMANS VE MİMARİ

## E.1 ⭐ "Zero-GC" iddiası test edilemez şekilde tanımlanmış

`gc.freeze()`/`gc.disable()` **sadece siklikkolektörü** baskılar; CPython'ın **refcount tabanlı** yok etme yine her refcount→0'da çalışır.

**Test edilebilir yeniden ifade:**
```
Steady-state invariant: herhangi N ardışık çözülmüş kare için
  (a) gc.get_stats()[g]['collections'] değişmemiş (g ∈ 0,1,2)
  (b) gc.callbacks'te phase == "start" olay sayısı == 0
  (c) gc.get_freeze_count() değişmemiş
  (d) tracemalloc delta == 0 bayt
```
`gc.callbacks` (3.3+) ve `gc.get_stats()` (3.4+) tekinci-taraf kancalar.

## E.2 ⭐ `cantools` maliyet profili — tek yayımlanmış ölçüm

**cantools PR #428 (2022-05-22, 2022-05-24 birleşti), 27 sinyalli bir kare, `cProfile` ×10.000:**

| Ölçüm | Değer |
|---|---|
| Toplam (önce) | **76,2 µs/kare** → sonra **41,2 µs/kare** (%46 iyileşme) |
| `_encode_field` tottime | **0,493 s / 0,762 s = %64,7** |
| `bitstruct.c` `pack` (C) | **0,006 s = %0,8** |
| Fonksiyon çağrısı | 2.570.002 → 660.002 (**−%74**) |

> ⭐ **Sonuç:** `cantools` **bit açma yavaş olduğu için değil**, her sinyalin bir C fonksiyonuna ulaşmak için ~2,8 µs yorumlayıcı yükü ödemesinden dolayı yavaş. **Hedef: kare başına Python çerçevesi üretimini ortadan kaldırmak — "daha hızlı bit matematiği" değil.**

**1000 Hz × 30 sinyal için tasarım sonucu:**
1. Ring buffer `kfifo_out_linear` tarzı **sürekli** span'ler sunar
2. ≥K kare (K ≈ 8–32) olduğunda **tek toplu çözme**: `np.frombuffer(span, '>u1').reshape(N,8)` → `np.unpackbits(axis=1, bitorder='big')` (DBC `big_endian` ile birebir) → sinyal başına bir gather + shift/mask/offset/scale
3. <K karede **kare-başı NumPy'ye DÜŞMEYİN** (ufunc dispatch yükü saf Python'dan yavaştır — anti-pattern)
4. `cantools` **referans oracle** olarak testlerde kalsın

## E.3 Zstandard

| Bulgu | Etki |
|---|---|
| ⭐ **Bağlam yeniden kullanımı "possibly over 10x faster"** (100.000 × 100 bayt girdi) | Tek en büyük kazanç; **her seviye ayarından kat kat büyük** |
| `ZstdCompressor`/`ZstdDecompressor` **thread-safe DEĞİLDİR** | Thread başına bir örnek, başlangıçta oluştur, hot loop'ta **yok** |
| Ölçülen (Silesia, i7-9700K, zstd 1.5.7): `-1` → oran **2,896**, 510 MB/s sıkıştırma, 1550 MB/s açma | `COMPRESSION_LEVEL_DEFAULT` = 3 (stdlib) · sıkıştırma/açma dengesi için iyi başlangıç |
| **Long-distance matching'i kapatın** → `window_log` 128 MiB'e çıkıyor | ⭐ 32-bit/gömülü DUT'ta RSS bütçesi için zorunlu |
| `write_checksum` = **XXHash64, kriptografik DEĞİL** | ⭐ **HMAC-SHA256'nizle karıştırmayın.** MAC'i **şifreli metin** üzerinden hesaplayın (aksi halde doğrulamak için açmak gerekir) |

> ⭐ **1 kHz akış kare başına zstd çerçevelemeyin:** 1000 kare/sn × 8 B = 8 kB/s = 691 MB/gün; kare-başı çerçeveleme oranı ~1'e çöker ve **86,4 milyon kare başlığı/gün** üretir. **`FLUSH_BLOCK` ile 1 sn veya 64 KB'de bir akışlayın.**

## E.4 Cloud telemetri

| Bulgu | Karar |
|---|---|
| 🔴 **GCS: parça bütünlüğü YAPILAMAZ** — *"you can't perform integrity checks on intermediate portions or chunks"* | Kendi SHA-256 manifestiniz şart |
| 🔴 **S3 composite checksum'da parça numaraları 1'den başlayarak ARALIKSIZ olmak ZORUNDA** — değilse **HTTP 500** (400 değil) | |
| S3 `CreateMultipartUpload` **başlatmadan sonra bitmez — ücret ödersiniz** | `Complete`/`Abort` zorunlu |
| ⭐ **`tus 1.0`** — dört backend için resmî sunucu bağdaştırıcısı olan tek açık protokol | Tek istemci + yerel eşleme |
| `CRC64NVME` artık S3 varsayılanı | Rust/Go istemci için CRC32C tercih edin |
| ⭐ **DB şemasına `synthetic: bool` + `source_channel` sütunu** | AGENTS.md §2.3'ün depolama katmanındaki ifadesi |
| 🔴 **InfluxDB 3 Core AGPL DEĞİLDİR** (Apache-2.0/MIT, "free to download and run with no license required") — **AMA** depo **2026-02-27'de arşivlendi**, `influxdb` Docker `latest` etiketi **2026-09-15'te** Core'a döndü | **QuestDB'ye gidin** (Apache-2.0, 8M+ satır/sn) |
| 🔴 **Loki'de yerleşik kimlik doğrulama YOK** | Atölye ağına açık gateway için **kimlik doğrulama ters vekil** |
| **Jaeger v2.21.0 kırıcı** — v1 HTTP uçları kaldırıldı | ≤2.20 sabitleyin |
| ⭐ **Tempo 3.1** yayımlanan imajlarını "keyless cosign" ile imzalar + **SLSA build provenance** ekler | İmzalı teslim zinciri için somut örnek |

## E.5 Lisans/SBOM/mevzuat

**Uyumlu dağıtımın ihtiyaç duyduğu dosyalar:** `LICENSE` · 🔴 **`LICENSES/` (SPDX adlarıyla verbatim metinler — GPL-3.0 dahil!)** · `THIRD_PARTY_NOTICES.md` · `ATTRIBUTION.md` · `REUSE.toml` · `sbom.spdx.json` + `bom.json` · dosya başına `SPDX-License-Identifier`

**CI kabı (5 satır):**
```bash
pip-licenses --with-license-file --format=markdown-vertical > THIRD_PARTY_NOTICES.md
licensecheck --summary --fail-on-opensource-mismatch     # metadata sürüklenme kapısı
reuse lint                                              # REUSE 3.3 kapısı
cyclonedx-bom -o bom.json -o sbom.spdx.json --output-format JSON
scancode-toolkit --license,file --copyright,file data/  # beyan metadata'sı olmayan vendor dosyaları
```

> ⭐ **Kopyalanacak Rust öncüsü:** `rust-lang/rust` 208 satırlık `REUSE.toml` — `precedence = "override"`, tek dosyayla monorepo ölçeğine nasıl çıkıldığını gösteriyor.

**Kurulum tuzakları:** 🔴 **`scancode-licensedb` PyPI'da YOK** (`scancode-toolkit` içinde) · 🔴 **PyPI'daki `syft` paketi Anchore syft DEĞİLDİR, PySyft (ilgisiz)** — `pip install syft` yapmayın · 🔴 **Redpanda BSL + RCL** (izinli lisanslı proje için risk)

**AB mevzuat takvimi:**

| Tarih | Ne |
|---|---|
| **2026-09-11** | 🔴 **CRA Madde 14 raporlama — YÜRÜRLÜKTE** (24s/72s/≤14g/≤1ay, ENISA SRP) |
| 2027-12-11 | CRA **tam uygulama** (Ek I, CE işareti) |
| 2026-08-02 | AI Act kalanı + **Madde 50 şeffaflığı** |
| 2027-12-02 / 2028-08-02 | AI Act Ek III / Ek I — ⚠ **"Digital Omnibus on AI" değiştirdi, konsolide metin olmadan güvenmeyin** |

> ⭐⭐ **Stratejik karar: Kural tabanlı deterministik motor AI Act kapsamı DIŞINDA.** AI Act Madde 3(1) bir AI sistemini *"çıkarım ve bir özerklik derecesi"* üzerinden tanımlar; sürümlü kural tablosuyla öğrenilmiş parametresiz çalışan bir uzman motoru kapsam dışındadır. **Asıl risk ters yönde: bir LLM'i geri koymak** (bulut anlatımı, DTC metni üzerinde RAG) sizi Madde 50 ve GPAI kapsamına alır. 🔴 **Ürünü "AI" olarak pazarlamayın** — "deterministik uzman motoru" deyin. **LLM'i kaldırma kararı bir mimari tercih değil, bir mevzuat kararıdır.**

**LGPL kararı (ertelenemez):** `python-can` LGPL-3.0-only + `asammdf` LGPL-3.0-or-later neredeyse zorunlu. Uyumlu yol: harici bağımlılık + kaynak sunumu + yeniden bağlama izni — **Windows/PyInstaller'da statik gömen frozen ikililer klasik ihlalidir.** Önlem: HAL arayüzünüz (`TxPort`/`RxSubscription`) zaten dar; **`ctypes` ile clean-room yeniden yazım gerçek bir seçenek.**

## E.6 UI, i18n ve erişilebilirlik

**pywebview 6.2.1 (2026-04-15), BSD-3-Clause.** 🔴 **Güvenlik dokümanı ~2 paragraf ve 2018 tarihli** — origin kontrolü, dosya sistemi hapsetme, CSP, bridge yetkilendirmesi **yok**. `window.pywebview.api.<method>` **ne yayınlarsanız odur**; allow/deny listesi, imza doğrulama, origin kontrolü, hız sınırı **yok**. `expose()` **çalışma zamanında** yetenek kümesi büyütebilir.

🔴 **3 belge edilmiş dosya sistemi erişim yolu:** (1) yerleşik HTTP sunucusu `127.0.0.1`'de — oturum-uniq CSRF jetonu ile azaltılır; (2) sürükle-bırak `pywebviewFullPath` **mutlak yolunu** döndürür; (3) `REMOTE_DEBUGGING_PORT` ayarı — **üretim teşhis aracında tam süreç ihlali**.

🔴 **`mshtml` (IE11) yedeği bir güvenlik karar noktası** — WebView2 Runtime yoksa pywebview sessizce 2017 motoruna düşebilir. `gui='edgechromium'` sabitleyin ve **fail closed** verin.
🔴 **`cefpython3` 66.1 (2021-02-16) = Chrome 66 (2018)** — **yeni iş için kullanmayın**, ~8 yıllık yamasız Chromium. `pywebview[cef]` extra'sı hâlâ kurulabilir.
🔴 **PyInstaller'da `cef` herhangi bir yerde etkinse o ikili 8 yıllık Chromium açıklarıyla gelir.** Cevher durumunda: pywebview'ın `js_api`'si WebView2'nin `AreHostObjectsAllowed`/`IsScriptEnabled` modelinden **daha kaba** — origin-gated mesajlaşma gerekiyorsa **kendi ince WebView2 host'unu** yazın ya da **tamamen yerel içerik + kısıtlayıcı CSP** ile kalın.

**Türkçe i18n — sert veriler (UCD 18.0.0, 2026-05-19):**

| Kural | Sonuç |
|---|---|
| `0049;0131;0049;0049;tr Not_Before_Dot` | **`I` → `ı`** |
| `0069;0069;0130;0130;tr` | **`i` → `İ`** |
| `0307; ;0307;0307;tr After_I` | `İ` küçültmede nokta **düşer** |
| JS: `"istanbul".toLocaleUpperCase("tr") === "İSTANBUL"` (`en-US` → `ISTANBUL`) | **CSS `text-transform: uppercase` aynı hatanın kılığı** |
| 🔴 Python `str.upper()` **yerel-duyarsızdır** (3.10.12'de doğrulandı: `'istanbul'.upper() == 'ISTANBUL'`) | **Türkçe gösterim büyütmesi ICU/Babel/`str.translate` üzerinden, asla `.upper()` ile** |
| Sıralama kod noktası düzeninde değil (`ı`↔`i`, `ş`↔`c`) | **Kullanıcıya görünür sıralı listede `Intl.Collator('tr')`** |
| Font: `ç ğ ı ö ş ü Ç Ğ İ Ö Ş Ü` — 12 glif | ⭐ **Gömülü fontun `İ`(U+0130) ve `ı`(U+0131) içerdiğini doğrulayın; her glif için mürekkep testi** |
| `tr-TR` (webview) vs `tr_TR` (Babel) karıştırılırsa **çift biçimlendirme sürüklenmesi** | |

**EN/TR karma metin için üç sınıf:** (1) **çevrilemez tanımlayıcılar** (`SPN 190`, `PGN 61444`, `0x0F`) — katalogda hiç olmaz, DBC/DB'den render edilir; 🔴 **teşhis değerleri yerelden bağımsız, Latin rakam ve hex konvansiyonuyla**; (2) **sözlük terimleri** (~250 terim, `do_not_translate` bayrağı); (3) **düzyazı**. 🔴 **Asla çevrilmiş parçaları birleştirmeyin** — Türkçe sözcük sırası bozulur.

**Severity renk standartları:**

| Standart | Renkler | Not |
|---|---|---|
| ⭐ **ISO 3864-2:2016** | CAUTION sarı RAL 1003 `#F9A900` · **WARNING turuncu RAL 2010 `#D05D29`** · DANGER kırmızı RAL 3001 `#9B2423` | **3 kademeli signal-word merdiveni — en doğrudan uygulanabilir** |
| **ISO 3864-4:2011** | RAL referansları + `#237F52` yeşil, `#005387` mavi, `#ECECE7` beyaz, `#2B2B2C` siyah | 🔴 **Bunlar baskı/işaret renkleri — koyu UI yüzeyinde 3:1 veya 4.5:1 sağlamaz. Ölçün.** |
| **IEC 60073** | *Coding principles for indicators and actuators* | 🔴 **Güncel sürüm/yıl DOĞRULANAMADI.** `webstore.iec.ch` arama 404 |
| 🔴 **"ISO 3864-1:2024"** | **YOK** — en güncel hâlâ **2011**. Tasarım belgesine yazmayın | |
| **WCAG 2.2 SC 1.4.1 (A)** | *"Color is not used as the only visual means…"* — **ve kontrast oranı farkı ≥3:1 olsa bile** renk-ayrımı tek başına yetersiz; yeşil-kenar=geçerli / kırmızı-kenar=geçersiz örneğinde **ek görsel gösterge zorunlu** | ⭐ **Her severity rozeti renk + şekil/ikon + metin taşımalı** |
| **WCAG 2.2 SC 1.4.11 (AA)** | ≥**3:1**; *"**Status icons on an application's dashboard (without associated text) have a 3:1 minimum contrast ratio**"* | Doğrudan uygulanabilir |

**Alarm durum modeli (EEMUA 191 Ed.4, Kasım 2024, ISBN 978-0-85931-243-1; ISA 18.2 ve **IEC 62682:2023** ile hizalı):**

| # | Durum | Bu araçta karşılığı |
|---|---|---|
| 0 | Normal | Sinyaller plausibility zarfı içinde |
| 1 | **Unacknowledged** | Yeni DTC/DM1/DM arızası, onaylanmamış |
| 2 | Acknowledged (hâlâ aktif) | Onaylanmış, temizlenmemiş DM1 |
| 3 | ⭐ **RTN Unacknowledged** | **Koşullar geçti, operatör gitmediğini bilmiyor** — ⭐ **en çok atlanan durum; "yeşil/normal" karosu OLMAMALI** |
| 4 | Shelved | "Yol testi sırasında yoksay" — **TTL taşımalı** |
| 5 | **Suppressed by design** | Anonansiyon edilmeyen koşullar |
| 6 | Suppressed temporarily | Sinyal yok çünkü ECU uyku/transport modunda |
| 7 | ⭐ **Out of service / bad quality** | **Ölçüm yolu geçersiz — bir alarm DEĞİLDİR**; sentinel/plausibility sınırlı kanal, bayat CAN karesi. **Sıfır-uydurma değişmezinin doğrudan karşılığı** |

**IMO A.1021(26) dört önceliği:** *emergency alarm / alarm / warning / caution* + §10.1.2 SOLAS eşleme tablosu + §4.2 *"both audible and visual means"* + §5.9 *"failure of the power supply or signal-generator device cannot cause loss of function"*

---

# BÖLÜM F — BİLGİ TABANI VE PROVENANCE

## F.1 ⭐ 35 alanlı DTC kayıt şeması (AUTOSAR temelli)

**AUTOSAR SWS Diagnostic Event Manager (609 pp) ve Dcm (835 pp) tam metin olarak indirildi.** Güncel Classic sürümü **R25-11**.

**⭐ R21-11 changelog'un en önemli satırı:** *"`SAE J2012_4 DTCs and UDS DTCs separated`"* ⇒ **J2012 jenerik DTC'ler ile UDS/OEM DTC'leri ayrı ad alanlarıdır.** 14.352 kaydınız `dtc_namespace` taşımıyorsa **severity normalizasyonu sağlıksızdır.**

**Zorunlu alanlar:** `record_id` · `dtc_code` · ⭐ **`dtc_namespace`** (7 değer) · ⭐ **`dtc_class`** (ISO 27145-3: `A/B1/B2/C/NoClass`) · `severity` / `severity_raw` / `severity_norm_rule` · `mil_effect` · `title{en,tr}` · `description{en,tr}` · `system` · `component` · ⭐ `validating_symptoms` (**ayrı kanonik belirti tablosuna ID referansı**) · `detecting_conditions` · `detection_logic` · `debounce` · `confirmation` · `aging` · `monitoring` · `status_bits` + `status_byte` · `snapshot_records[]` · `extended_data[]` · `freeze_frame` · `j1939{spn,fmi,...}` · `spn_fault_matrix_refs[]` · `confirming_tests[]` · `repair{}` · `deactivation{}` · `related_codes[]` · ⭐ `provenance[]` · `stub_flag` (hesaplanır)

**WWH-OBD önceliği:** `A > B1 > B2 > C > OBD DTC'si yok`
**ASIL decomposition:** `A(B) + A(B) = B`; ⚠ *"should not introduce a common point of failure"*

## F.2 ⭐ Provenance kayıt şeması

```json
{
  "provenance_id": "prv-0001",
  "target": { "record_id": "...", "field": "description.en", "xpath": "/records/42/description/en" },
  "activity": "normalize_from_source", "activity_version": "extract-v3.2.1",
  "activity_started": "2026-03-14T09:12:04Z", "activity_input_hash": "sha256:9f2c…",
  "agent": { "type": "human", "id": "kb-editor-07", "role": "author" },
  "source": { "title": "...", "path": "https://doi.org/...", "type": "standard",
              "publisher": "SAE International", "revision": "2007-12",
              "licence": "proprietary", "access_date": "2026-03-12",
              "snapshot": { "archive_url": "...", "sha256": "...", "bytes": 1234567,
                            "pages_cited": [4, 5] } },
  "locator": { "type": "page|cell|row|section|jsonpath", "value": "..." },
  "verbatim": "kaynakta basıldığı gibi metin",
  "transform": "none" | { "rule_id": "SEV-R3", "rule_hash": "sha256:..." },
  "confidence": "operator_verified|single_source|corroborated|unverified",
  "corroboration": [ { "provenance_id": "prv-0002", "agreement": "verbatim|semantic|conflict" } ],
  "retrieved_from_web": true, "http_last_modified": "...", "http_etag": "..."
}
```

> ⭐ **`source_url_validity_rate` mekanizması:** `path` **yanında** `http_last_modified`/`http_etag` saklayın ve URL'leri düzenli HEAD ile yoklayın. **Çürüyen URL iddiayı silmez** — `source.status = "unreachable"|"moved"|"gone"` olur. 🔴 **Bu olmadan %33,9 eksik URL geri %0'a döner.**

## F.3 ⭐ 16 metrik seti

| # | Metrik | Mevcut | Hedef |
|---|---|---|---|
| M1 | Şema geçerlilik oranı | *ölçülmedi* | %100 |
| M2 | Açıklama tamamlanması (en+tr, ≥20 token, şablon değil) | *ölçülmedi* | → 1.0 |
| **M3** | ⭐ **Belirti kapsamı** | **%6,1** (%93,9 eksik) | Q4'e kadar %60 |
| M4 | Stub'suz oran (601 prosedür: 524 gerçek, 77 stub) | %87,2 | → 1.0 |
| M5 | ⭐ Kaynak-URL geçerlilik (**var + snapshot geçerli** olarak ikiye ayrı) | %66,1 var / %33,9 yok | %95 var |
| **M6** | ⭐ **Provenance tamamlanması** | **0** | 1.0 |
| M7 | Birebir (verbatim) oranı | — | 1.0 |
| M8 | Kaynaklar arası uyum (çatışma < %5) | — | — |
| M9 | Severity normalizasyon kapsamı | 22→5 gerekli | 1.0 |
| **M10** | ⭐ **Eşlenmemiş severity sayısı** | — | **0 (fail-closed)** — sıfırdan farklı bir değer **güvenlik bulgusudur, KPI değil** |
| M11 | Tekrar annotation oranı (CombinedDTC meşrudur — **silme, işaretle**) | — | 1.0 |
| M12 | Öksüz referans sayısı (601 prosedür + 53 düğüm) | — | 0 |
| M13 | DIAGNOSABILITY (bileşik) | — | monoton ↑ |
| M14a | Confirming-test tamamlanması (makine-değerlendirilebilir `pass_criteria` ile) | — | → 1.0 |
| **M15a** | ⭐ **EP kural kapsamı (doğrulanmış golden vakalarda)** | 55 vaka ↔ 53 düğüm | → 1.0 |
| M16a | Debounce/aging kapsamı (ISO 26262-6 kanıtı) | — | 1.0 |

> ⭐ **"Diagnosability" = M15a** (metin doluluğu **değil**). M2/M3/M4/M6 gerekli ama yetersizdir; yalnız M15a tüm zinciri çalıştırır. **M2'nin M15a'nın yerine geçmesine asla izin vermeyin.**

## F.4 Veri kalitesi otomasyonu

**Stub tespiti (4 bağımsız dedektör):** (1) RapidFuzz `WRatio` ile 77 bilinen stub'a karşı ≥0,90; (2) n-gram Shannon entropisi + type-token oranı; (3) placeholder sözlüğü + **TR'de EN ile bayt-aynı alan**; (4) çapraz alan anlamsal çökmesi.
**Alan kardinalitesi alarmı:** 14.352 satır üzerinde kardinalitesi 22 olan alan, kontrolden kaçmış kontrollü sözlüktür ⇒ **enum'a girene kadar build kırılır.**
**Yakın-tekrar:** SimHash (hammadde ≤3) + MinHash-LSH (datasketch, eşik ≈0,8) + karakter 3-gram TF-IDF kosinüs.
**CI:** `check-jsonschema 0.38.2` (hook kimlikleri: `check-jsonschema`, `check-metaschema`) · `pandera` · `Great Expectations 1.23.2` · `pre-commit`

## F.5 ⭐ Türkçe boşluğu — doğrulanmış negatif

| Deneme | Sonuç |
|---|---|
| tr.wikipedia `"arıza kodu"` | **Madde yok** (61 sonuç, hepsi alakasız) |
| tr.wikipedia `"OBD arıza kodları"` | **Madde yok** |
| tr.wikipedia `OBD-II PID'leri` | Var ama **yalnız 5 dilde** (en, es, fi, ru, zh — **Türkçe madde yok**), "needs translation" şablonu |
| `arizakodlari.com` | HTTP 200 ama **JS-render, sıfır metin** |
| `arabam.com` | **Cloudflare 403** |
| TDK sözlük | **HTTP 525** |

> **⇒ Ulaşabildiğim her yerde **provenance, sürümleme ve lisans netliği olmayan** yalnızca ticari/SEO Türkçe DTC içeriği var.** Bu bir boşluk **değil, stratejik avantajdır.** Türkçe tarafınız **yazılmalı** ve EN/TR asimetriniz farklılaştırıcıdır. **Bu siteleri kazımayın** — provenance yok, ToS riski var, yeniden üretilebilirlik kısıtınızı ihlal eder.

---

# BÖLÜM G — 2026 LİSANS & MEVZÜAT KONUMLANDIRMASI

**Paket sağlığı:** `cantools` **44.1.0 (2026-09-16)**, MIT, **241 sürüm** — ekosistemin **en aktif** CAN paketi, sert sabitle · `python-can` 4.6.1, **LGPL-3.0-only** · `udsoncan` 1.26.1, MIT, 41 sürüm, **tek bakımcı** (bus-factor-1) · `can-isotp` 2.0.7, MIT · `nmea2000` 2026.8.1, Apache-2.0, **çok aktif kod / çok küçük benimseme** · `canmatrix` 1.2 (2025-02-17) ⚠ **19 aydır PyPI sürümü yok**, `development` dalında düzeltmeler var · 🔴 `obd` **GPL-2.0-only**

**PyPI'da 404 verenler:** `isobus` · `isobus2/3` · `iso14229` · `python-isotp` · `isotp` · `mdflib` · `savvycan` · `ngsim` · `python-OBD` · `python-canopen` (→ `canopen`) · `python-can-matrix` · `can-bridge` (→ `can_bridge`) · `licensedb` · `scancode-licensedb` · `cyclonedx` (→ `cyclonedx-bom`) · `syft` (→ GitHub releases)

**🔴 ISOBUS'ta bakımlı Python kütüphanesi YOK:** `jboomer/python-isobus` **son commit 2017-03-15** — 9 YIL ÖLÜ. Tek referans `Open-Agriculture/AgIsoStack++` (C++, aktif, ISO 11783-11 **2026090801** DDI'yi doğrudan senkronize ediyor). ⇒ **Python'da ISOBUS bir maliyet kalemi; hendek = küratör hattı (sürüm sabitleme + diff + atıf), decoder değil.**

**Konumlandırma — savunulabilir niş:** "daha çok protokol" değil:

| Sütun | Kanıtlanmış boşluk |
|---|---|
| **P1** | ⭐ `ISO 26262 ASIL python CAN gateway tool` → **0 repo** · `J1939 diagnostic analyzer` → **1** (47★, C++) · `NMEA 2000 diagnostic` → **4** (en fazla 1★, 2022) · `J1939 python library` → **4** (9★). Yerleşikler *hizmet alanına* göre dikey parçalanmış, neredeyse tamamen *yolcu-EV'ye* odaklı. **Tek ikilide UDS/DoIP + J1939 + N2K + ISOBUS + CANopen + OBD-II kapsayan gerçek yapısal boşluk.** |
| **P2** | ⭐ **Çevrimdışı/egemenlik 2026'da bir *mevzuat* satış noktasıdır** (CRA, AI Act, AB veri yerleşimi). Liman otoriteleri, balıkçı filoları, savunma-adjant tersaneler için *"veri araca/tekneye/traktöre çıkmaz"* **tedarik dili.** |
| **P3** | ⭐ **Kanıt izleme ürünün kendisi, bir özellik değil.** Pazar aracı teşhisi *dışında* denetlenebilir kanıta talep gösteriyor (ISO 21434 çalışma ürünleri, R155 CSMS izleri, ISO 26262 §7 kanıt kayıtları, artık CRA Ek I teknik dokümantasyonu). Satıcılar **servis kaydı** üretiyor; **neredeyse kimse kanıt paketi** üretmiyor — imzalı, hash-zincirli, oynatılabilir, her severity iddiası bir kural kimliğine + karelere çözülebilir. **Denetim artefaktını satın, tarayıcıyı değil.** |
| **P4** | ⭐ **Güvenlik mimarisi pazarlama özelliği.** `commaai/opendbc` (3.4k★, MIT) yayımlanmış güvenlik modeli olan **tek** yaygın bilinen açık kaynak otomotiv projesi. **opendbc bir kontrol yığınıdır, teşhis yığını değil — rakip değil, karşılaştırmadır.** |
| **P5** | ⭐ **ISOBUS veri provenance'ı hendek** (bakımı yok, ama hendek küratör hattıdır). |

> **Komşu oyuncular:** Bosch ESI[tronic] / Texa / Launch / Snap-on / Autel — hepsi **marka kapsamı × bulut hizmetleri × donanım kilidi** üzerinden rekabet ediyor. Gelir modeli ağ bağımlılığı ve marka kapsamı aboneliğini gerektiriyor. **Hiçbiri çalışan, denetlenebilir, çevrimdışı bir aracı bir özellik olarak konumlandıramaz — çevrimdışı onların yapısal borçlarıdır.**

**Farklılaştırma iddiası (yöntemiyle birlikte sunulmalı):** *"Hiçbir açık kaynak proje, CAN-UDS, J1939, NMEA-2000 ve ISO 11783 boyunca çevrimdışı, deterministik, kural-tabanlı, kanıt-izleyen çok protokollu araç/makine teşhisi gerçekleştirmiyor."* **Kanıt (2026-09-26):** 0 + 1 (bulut-RAG bağımlı) + 14 (akademik, ≤3★) + 1 + 4. ⚠ **GitHub repo araması bir *vekil*dir, nüfus sayımı değil — kapsam korunmalı.**

---

# BÖLÜM H — ÖNCELİKLENDİRİLMİŞ EYLEM PLANI

## H.1 P0 — Hemen (yasal/veri kaybı)

| # | Aksiyon | Kanıt |
|---|---|---|
| 1 | 🔴 **CRA Madde 14:** CSIRT/PSIRT temsilcisi + VDP + ENISA SRP iş akışı | Rapor 10 §4.1 |
| 2 | 🔴 **`LICENSES/` dizinini SPDX adlarıyla kurun** + `REUSE.toml` + `reuse lint` CI kapısı — **GPL-3.0 metni dahil** | Rapor 01 §7, 10 §2.4 |
| 3 | 🔴 **GPL-3.0 ve CC-BY-SA-4.0 türevi `.dbc`'leri dağıtımdan çıkarın** | Rapor 01 §7 |
| 4 | 🔴 **OpenSkipper korpusunun kökenini yeniden doğrulayın** (legal inceleme) | Rapor 09 §3.1 |
| 5 | 🔴 **`NMEA 2000` PGN veritabanını `LICENSES.md`'de ayrı, dürüst girişle kaydedin** | Rapor 10 §2.3a |
| 6 | 🔴 **`data/knowledge/`'yi `build_exe.py` ve `build_nuitka.py`'ye ekleyin** | Rapor 00 §2 |
| 7 | 🔴 **`obd` (GPL-2.0) bağımlılığını kaldırın**; python-can/asammdf için LGPL kararını verin | Rapor 10 §1.1, §2.5 |
| 8 | 🔴 **`gb27830_2015.dbc` → `gbt27930_2023.dbc`**, 29-bit ID düzeltmesi, PROVENANCE güncellemesi | Rapor 07 §0 |
| 9 | 🔴 **`data/traces/` için dönüştürücü kurun** (`canboat convert --to json` + Python adaptör) | Rapor 03 §5 |

## H.2 P1 — Entegrite kapıları

| # | Aksiyon |
|---|---|
| 10 | 🔴 **ASIL kapsam sayılarını lisanslı ISO 26262-5/-6 kopyasıyla doğrulayın** — hiçbir 🔴 rakamı güvenlik dosyasına koymayın |
| 11 | 🔴 **Bus-off kurtarma politikası yazmayın**; `restart-ms 0`; 128×11 kuralı Bosch CAN 2.0'den okunana kadar |
| 12 | ⭐ **`dtc_namespace` + `dtc_class` alanlarını zorunlu kılın** (AUTOSAR) |
| 13 | ⭐ **Severity normalizasyonunu deterministik tabloyla uygulayın**; bilinmeyen ⇒ fail-closed seviye 5 |
| 14 | ⭐ **`provenance[]` şemasını devreye alın** — `dependentRequired` ile "anahtar-dışı alan ⇒ provenance" |
| 15 | 🔴 **J2012-DA'yı satın alıp okumadan severity modelini dondurmayın** — `DTCSeverity ↔ Failure Type` eşlemesi doğrulanmamış |
| 16 | ⭐ **`MR-13` (FI saflığı) + `MR-16` (fail-closed closure) CI testleri** |
| 17 | ⭐ **`MR-8` (zaman damgası değişmezliği) testi** |
| 18 | ⭐ **`F1`: ephemeral sır yedeğini fail-closed yapın** (`estop.py:199-203`) |
| 19 | ⭐ **`F2`: `CLOCK_BOOTTIME`'a geçin** ya da resume'da `SAFE_STATE` |
| 20 | ⭐ **`CAN_ISOTP_WAIT_TX_DONE` zorunlu kılın** |
| 21 | ⭐ **UDS `0x29`/`0x84` yolunu `TxSafetyGateway`'e ekleyin** + 23 NRC durum makinesi |
| 22 | 🔴 **DBC lint CI'ı:** `canmatrix --check` + `cantools load_file(encoding='utf-8', strict=True)` + tekrarlı `BO_` / çoklu `VERSION` / eksik `BS_:` / `factor==0` |
| 23 | **DBC katalog↔disk her iki yönde kapı** (219/196/186/218 drift'i) |
| 24 | **GM Mode $06 URL'sini `_dm.pdf` olarak düzeltin**; `obd_mode06`'yı `CSV_PAIRS` kapısına ekleyin |
| 25 | **`mutmut` + `Hypothesis` + `pyperf`/`asv` CI'ı**; **"zero-GC"yi test edilebilir invaryanta çevirin** |
| 26 | ⭐ **`SCHED_DEADLINE` + `sched_getattr(SCHED_GETATTR_FLAG_DL_DYNAMIC=1)` + `mlockall`** katmanı |

## H.3 P2 — Varlıkları bağlanabilir hâle getir

| # | Aksiyon |
|---|---|
| 27 | ⭐ **Kanonik belirti tablosu** (~150–400 satır) kurun — %93,9 boşluk **yalnız** böyle kapanır |
| 28 | ⭐ **`Zenodo 19857425`'i** (5 fiziksel olarak doğrulanmış deniz arızası) `data/traces/` altına alın |
| 29 | ⭐ **`can-train-and-test`** (arXiv:2308.04972) dış karşılaştırma korpusu olarak |
| 30 | ⭐ **`Wasm-R3` record-reduce-replay** ile 289 MB korpusunu azaltın |
| 31 | ⭐ **`hv_safety_thresholds.json`** ekleyin (100 Ω/V · 7 MΩ · 5 dk · 6200 kΩ · 20 mA, her biri `authority`+`clause`+`url`) |
| 32 | ⭐ **RFC 4998 kanıt zinciri** — Merkle kökü + `prev‖entry` + düzenli çapraz imzalama; kırılma ⇒ `SAFE_STATE` |
| 33 | ⭐ **RFC 8785 (JCS) lisans imzası** + 12 adımlık kontrol listesi (özellikle **"çevrimdışı kullanım salt-okunur teşhise düşer"** oturum-token modeli) |
| 34 | ⭐ **Canopen EDS/DCF** (CiA 306 = INI, ücretsiz) |
| 35 | ⭐ **Mosquitto 2.1.x + QuestDB + DuckDB** + `synthetic: bool` şema sütunu |
| 36 | ⭐ **isobus.net DDI sürüm sabitleme + diff + atıf hattı** (2026050501 / 2025092601 / 2026090801) |
| 37 | **289 MB korpusu LFS + nesne deposu olarak ikiye ayırın**; CI'a boyut nöbetçisi. Depoyu SHA-256 formatına çevirmeyin |
| 38 | ⭐ **ISO 3864-2 3 kademeli renk merdiveni + WCAG 1.4.1/1.4.11 uyumu**; **EEMUA 191 7 durumlu alarm modeli** (RTN-Unack "yeşil" **OLMAMALI**) |
| 39 | ⭐ **`gui='edgechromium'` sabitle + fail closed**; `cef` extra'sını yasakla; `REMOTE_DEBUGGING_PORT`'u dev-only yap |
| 40 | **TR pseudo-locale CI** (+%35 uzunluk, aksan köşeli parantez, `msgfmt --check` ile katalog kaçağı ⇒ build kırılır) |

---

# BÖLÜM İ — DOĞRULANAMAYANLAR (yatırım öncesi kapatılmalı)

| # | Madde | Neden |
|---|---|---|
| 1 | ISO 26262-1…-12 madde metni (tüm parçalar) | `iso.org` **403**; Wikipedia özetiyle sınırlı |
| 2 | **ASIL kapsam sayıları (SPFM/LFM/HFM)** | Aynı — 🔴 **güvenlik dosyasına konulamaz** |
| 3 | ISO 26262-6 windowed/question-mark watchdog maddesi | Aynı — madde atfı yapılmamalı |
| 4 | ISO 26262-8 TIER / Tool Confidence Level tablosu | Aynı — **TIER numarası güvenlik dosyasına konulamaz** |
| 5 | IEC 61508-3/8 tool qualification maddesi | `webstore.iec.ch` 404 — **madde numarası uydurulmayacak** |
| 6 | ISO 13850 madde numarası (bilinçli reset) | Metin doğrulandı (MD 2006/42/EC §1.2.4.3 birebir), **ISO 13850 madde numarası** doğrulanamadı |
| 7 | **MD 2006/42/EC'nin halefi Reg. (EU) 2023/1230** (2027'den) | Boş gövde — ⚠ **E-Stop argümanını 2027 öncesi yeniden çapaya alın** |
| 8 | **CAN bus-off kurtarma kuralı (128 × 11 çekinik bit)** | Bosch `can2spec.pdf` 404 — 🔴 **tahmin edilmemeli** |
| 9 | **WWH-OBD (ISO 27145) + Euro 7 (Reg. (EU) 2024/1257)** | `websearch` 429, ISO 403, EUR-Lex boş — 🔴 **bellekten yazılmayacak** |
| 10 | **TimescaleDB TSL, InfluxDB 3 Core güncel deposu, VictoriaMetrics OSS, ZeroMQ, OpenTSDB, git-annex, Sparkplug B, OPC-UA, RFC 3161, Python JCS kütüphaneleri** | Lisans/sürüm metinleri okunmadı |
| 11 | Fawaz/Boyle/Jewell "Resilient Control Systems" (2012) | IEEE paywall, arXiv'de yok — **Aynı iddia yapan 4 arXiv makalesi doğrulandı, onlar kullanıldı** |
| 12 | Chadha vd. sensor-spoofing | Hangi makale kastedildi netleşmedi — **soğrudan doğrulanabilir SoK makalesi kullanıldı** |
| 13 | Kulkarni vd. orijinal HLC (PODC 2014) | PDF ikili → okunamıyor; **takip makaleleri okundu** |
| 14 | "Balkanization problem" terimi | arXiv + Crossref **sıfır** → **topluluk sözlüğü**; kavram ISO 13849 CCF maddesiyle ele alındı |
| 15 | IEC 60073 güncel sürüm/yılı | `webstore.iec.ch` arama 404 — **"IEC 60073:20xx" yazmayın** |
| 16 | ISO 3864-1:2024 | **YOK** — en güncel 2011 |
| 17 | "ISO 26262-1:2019 olmadı" gibi spekülasyonlar | Spekülasyon **yapılmadı** |

**Araç sınırları (tüm seans):** `websearch` çoğu alt görevde 404/429 verdi; `iso.org` 403; `sae.org` Angular SPA; `eur-lex.europa.eu` kısmen boş gövde; `api.github.com` 403; `w3.org` 403; `cdn.standards.iteh.ai` önizlemeleri **RC4-şifreli**; `webfetch` PDF render edemiyor (ham FlateDecode); DuckDuckGo/Mojeek/Bing/searx CAPTCHA/zehirli sonuç.

**En güvenilir sürüm kaynağı keşfi:** vendor sayfaları değil, **`pyproject.toml` extras sabitlemeleri** (`rp1210>=1.0.1`, `asammdf>=6.0.0`, `gs-usb>=0.2.1`, `python-can-candle>=1.2.2`, `nixnet>=0.3.2`, `python-ics>=2.12`) — **bu teknik genel olarak uygulanmalı.**

---

---

# BÖLÜM J — EKSİK İÇERİK (EKLENDİ)

## J.1 UDS / ISO 14229 tam servis tablosu (Bölüm C.5'in tamamı)

| SID | RSID | Servis | İstek / Pozitif yanıt | Not |
|---|---|---|---|---|
| `0x10` | `0x50` | **DiagnosticSessionControl** | `10 <sub> [P2server_max P2*server_max]` → `50 <sub> [timing]` | Alt fonksiyonlar: `01` default · `02` programming · `03` extended · `04` safetySystem · `05`. 🔴 **Alt fonksiyon ≠ `00` ise pozitif yanıt `sub` + `P2server_max` + `P2*server_max` içerir — yanıt boyutu alt fonksiyona bağlıdır** |
| `0x11` | `0x51` | **ECUReset** | `11 <sub>` → `51 <sub>` | `01` hardReset · `02` keyOffOnReset · `03` softReset · `04` enableRapidPowerShutDown · `05` disableRapidPowerShutDown. 🔴 **Araca reset atmak** — `TxSafetyGateway` policy'sine tabi |
| `0x12` | `0x52` | ClearConfiguration | `12` | |
| `0x14` | `0x54` | **ClearDiagnosticInformation** | `14 <groupMask>` → `54` | Gruplar: `0x01–0x04` emisyon · `0x17–0x1F` · `0x2E` · `0x2F` · `0x33–0x35` · `0x37` · `0x38` · `0x3D` · `0x3E` · `0x7F–0xFF` OEM. ⚠ **DTC silme kalıcı zararlı — hata tespit geçmişi silinir** |
| `0x15` | `0x55` | ReadECUIdentification | `15` | |
| `0x16` | `0x56` | ReadActiveDiagnosticSession | `16` → `56 <sub>` | |
| `0x17` | `0x57` | ControlOfDTCSetting | `17 <01=on / 02=off>` | ⚠ **DTC toplama'yı kapatmak teşhis zincirini bozar — mutlak yasak olmalı** |
| `0x18` | `0x58` | ResponseOnEvent | `18 <sub> […]` | |
| `0x19` | `0x59` | **ReadDTCInformation** | `19 <sub> […]` → `59 …` | **En geniş servis — 30+ alt fonksiyon**, aşağıdaki J.1.1 tablosu |
| `0x1A` | `0x5A` | ReadDataByIdentifier | `1A <DID>` → `5A <DID> <data>` | |
| `0x1B` | `0x5B` | ReadDataByPeriodicIdentifier | `1B <transmissionMode> <DID…>` → `5B …` | |
| `0x1C` | `0x5C` | ReadScalingDataByIdentifier | `1C <DID>` | ISO 14229-1:2020 eki |
| `0x22` | `0x62` | **ReadDataByIdentifier** | `22 <DID…>` → `62 <DID> <data…>` | Temel okuma servisi |
| `0x23` | `0x63` | ReadMemoryByAddress | `23 <memAddr> <memSize>` | |
| `0x24` | `0x64` | ReadScalingDataByIdentifier | `24 <DID>` | |
| `0x25` | `0x65` | ReadDataByIdentifierPeriodicIdentifier | `25 …` | |
| `0x26` | `0x66` | ReadDataByIdentifierRecord | `26 <DID…>` | |
| **`0x27`** | `0x67` | ⭐ **SecurityAccess** | `27 <level> [key]` / `27 <seedLevel>` → `67 <seed>` | `01`/`02` L1 · `11`/`12` L2 · `21`/`22` L3 · `31`/`32` L4 · `41`/`42` L5. 🔴 **Yalnız seed/key — anahtar çevikliği, sertifika, seed ötesi nonce YOK** |
| `0x28` | `0x68` | **CommunicationControl** | `28 <sub> [nodeControlNumber]` | `00` enableRxAndTx · `01` enableRxAndDisableTx · `02` disableRxAndEnableTx · `03` disableRxAndTx · `04`/`05` EnhancedAddressInformation |
| `0x2A` | `0x6A` | ControlOfDTCSetting (2020) | `2A <01=on / 02=off>` | |
| `0x2B` | `0x6B` | ReadDataByPeriodicIdentifier (2020) | `2B …` | |
| `0x2C` | `0x6C` | **LinkControl** | `2C <sub> [baudrate]` → `6C …` | `01` verifyBaudRateTransitionWithFixedBaudRate · `02` …SpecificBaudRate · `03` **transitionBaudRate**. 🔴 **CAN↔CAN-FD ya da bit hızı değiştirir — güvenlik-kapılı operasyon** |
| `0x2D` | `0x6D` | ControlConditionIdentifiers | `2D …` | |
| `0x2E` | `0x6E` | **WriteDataByIdentifier** | `2E <DID> <data>` → `6E <DID>` | 🔴 **ECU'ya veri yazmak — mutlak TX policy'sine tabi** |
| `0x2F` | `0x6F` | ⭐ **RoutineControl** | `2F <01=start / 02=stop / 03=requestResults> <routineID>` → `6F …` | **J1979 Mode $08'in halefi** — rutin kontrolleri |
| `0x31` | `0x71` | **RequestDownload** | `31 <dataFormatIdentifier> <addressAndLengthFormatIdentifier> <memoryAddress> <memorySize>` | ⭐ **Flashing zincirinin ilk adımı** |
| `0x32` | `0x72` | RequestUpload | `32 …` | |
| `0x33` | `0x73` | RequestTransfer | `33 <transferRequest> …` · veri `01`–`05` | |
| `0x34` | `0x74` | RequestDownloadData | `34 <blockSequenceCounter> <dataFormat> <data…>` | |
| `0x35` | `0x75` | RequestUploadData | `35 …` | |
| `0x36` | `0x76` | **TransferData** | `36 <blockSequenceCounter> <data…>` → `76 <counter>` | 🔴 **`0x00–0xEF` veri, `0xF0–0xFF` yönetim kaçışı** |
| `0x37` | `0x77` | RequestTransferExit | `37` → `77` | |
| `0x38` | `0x78` | RequestFileTransfer | `38 <mode> <filePathAndNameLength> <dataFormat> <fileSize> <filePath>` | |
| `0x3D` | `0x7D` | TesterPresent (2020) | `3D 00` → `7D 00` | |
| **`0x3E`** | `0x7E` | ⭐ **TesterPresent** | `3E 00` → `7E 00` | **Session timeout'u sürdürmek için periyodik gönderilmeli** |
| `0x3F` | `0x7F` | RequestExtendedDataByIdentifier | `3F <DID>` | |
| **`0x84`** | `0xC4` | ⭐⭐ **SecuredDataTransmission** | `84 <code> …` | *"secure UDS communication using either **encryption or signature**"* — **araç→ECU güvenliği** |
| `0x85` | `0xC5` | ControlOfDTCSetting (2020) | `85 <01=on / 02=off>` | |
| `0x86` | `0xC6` | ResponseOnEvent | `86 <sub> …` | `00` stopResponseOnEvent · `01` onDTCStatusChange · `02` onTimerInterrupt · `03` onChangeOfDataIdentifier · `04` reportActivatedEvents · `05` startResponseOnEvent · `06` clearResponseOnEvent · `07` onComparisonOfValues · `08` reportMostRecentDtcOnStatusChange · `09` reportDTCRecordInformationOnDtcStatusChange |
| `0x87` | `0xC7` | LinkControl (2020) | `87 <sub> [baudrate]` | |

> 🔴 **2020'de kaldırılan:** `0x83 AccessTimingParameter` (RSID `0xC3`)
> 🔴 **OEM/araç değiştirir veya kısıtlar:** `0x14` DTC sil · `0x17`/`0x2A`/`0x85` DTC toplama kapatma · `0x2C`/`0x87` bit hızı/CAN-FD geçişi · `0x2E` DID yazma · `0x31–0x37` bellek indirme/yükleme · `0x11` ECU sıfırlama · `0x28` haberleşme kontrolü

### J.1.1 ReadDTCInformation (`0x19`) alt fonksiyonları

| Sub | Ad | Yanıt içeriği |
|---|---|---|
| `0x01` | reportNumberOfDTCByStatusMask | `DTCStatusAvailabilityMask` + `DTCFormatIdentifier` + `DTCCount` |
| `0x02` | reportDTCByStatusMask | `DTCAndStatusRecord` |
| `0x03` | reportDTCSnapshotIdentifier | |
| `0x04` | reportDTCSnapshotRecordByDTCNumber | |
| `0x05` | reportDTCStoredDataByRecordNumber | |
| `0x06` | reportDTCExtDataRecordByDTCNumber | `DTCMaskRecord` + `DTCOrigin` + `MemorySelection` parametreli |
| `0x07` | reportNumberOfDTCBySeverityMaskRecord | `0x01` ile aynı yanıt şekli |
| `0x08` | reportDTCBySeverityMaskRecord | |
| `0x09` | reportSeverityInformationOfDTC | `DTCAndSeverityRecord` |
| `0x0A` | reportSupportedDTC | |
| `0x0B` | reportFirstTestFailedDTC | |
| `0x0C` | reportFirstConfirmedDTC | |
| `0x0D` | reportMostRecentTestFailedDTC | |
| `0x0E` | reportMostRecentConfirmedDTC | |
| `0x0F` | reportMirrorMemoryDTCByStatusMask | |
| `0x10` | reportMirrorMemoryDTCExtDataRecordByDTCNumber | |
| `0x11` | reportMirrorMemoryDTCBySeverityMask | |
| `0x12` | reportNumberOfMirrorMemoryDTCByStatusMask | `0x01` ile aynı şekil |
| `0x13` | reportNumberOfEmissionsRelatedOBDDTCBySeverityMask | |
| `0x14` | reportEmissionsRelatedOBDDTCBySeverityMask | |
| `0x15` | reportDTCFaultDetectionCounter | |
| `0x16` | reportDTCWithPermanentStatus | |
| `0x17` | reportDTCExtDataRecordByRecordNumber | 🔴 **kimlik doğrulama gerektirir** |
| `0x18` | reportUserDefMemoryDTCByStatusMask | |
| `0x19` | reportUserDefMemoryDTCSnapshotRecordByDTCNumber | |
| `0x1A` | reportUserDefMemoryDTCExtDataRecordByDTCNumber | |
| `0x42` | reportDTCSupportedBySeverityMask | |
| `0x55` | reportDTCWithPermanentStatus | ⭐ **WWH-OBD — OBD Mode $0A'nın UDS karşılığı** |
| `0x56` | reportDTCCountersByStatusMask | |
| `0x57` | reportDTCCountersByDTCAndStatusMaskRecord | |
| `0x58` | reportDTCCountersByRecordNumber | |
| `0x59` | reportDTCPersistentDataByRecordNumber | |
| `0x5A` | reportDTCInformationByDTCReadinessGroupIdentifier | |

**AUTOSAR Dcm R24-11'den doğrulanan parametreler:** `0x01` → `DTCStatusMask`; `0x06` → `DTCMaskRecord`, `DTCOrigin`, `MemorySelection`; `0x17` → **kimlik doğrulama zorunlu** (`RS_Diag_04233`)

**DTC durum baytı (Dem R24-11):** **bit 0 = TestFailed** (occurrence counter `DEM_PROCESS_OCCCTR_TF` **0→1 geçişinde** artar) · **bit 3 = Confirmed DTC** (event confirmation) · **bit 6** = "bu işletme döngüsünde test edildi". 🔴 **Monitor durumu UDS durumundan AYRI:** `Dem_ResetMonitorStatus` monitor bit 0'ı temizler, debounce'u sıfırlar ve **açıkça UDS durum baytını değiştirmez, önceden saklanan freeze frame'i SİLMEZ.** 🔴 **Bit 1, 2, 4, 5, 7 atamaları DOĞRULANMADI.**

### J.1.2 Negatif yanıt kodları (tam)

| NRC | Anlam |
|---|---|
| `0x10` | generalReject |
| `0x11` | serviceNotSupported |
| `0x12` | subFunctionNotSupported |
| `0x13` | incorrectMessageLengthOrInvalidFormat |
| `0x14` | responseTooLong |
| `0x21` | busyRepeatRequest |
| `0x22` | conditionsNotCorrect |
| `0x23` | requestSequenceError |
| `0x24` | noResponseFromSubnetComponent |
| `0x25` | failurePreventsExecutionOfRequestedAction |
| `0x26` | requestOutOfRange |
| `0x27` | securityAccessDenied |
| `0x28` | authenticationRequired |
| `0x29` | invalidKey |
| `0x2A` | exportKeyNotAvailable |
| `0x2B` | exportKeyNotValid |
| `0x2C` | certificateNotVerified |
| `0x2D` | ownershipVerificationFailed |
| `0x2E` | challengeCalculationFailed |
| `0x2F` | settingAccessRightsFailed |
| `0x30` | sessionKeyCreationOrDerivationFailed |
| `0x31` | configurationDataUsageFailed |
| `0x32` | deAuthenticationFailed |
| `0x33` | uploadDownloadNotAccepted |
| `0x34` | authenticationRequired *(0x29/0x84 yolu)* |
| `0x35` | invalidKey *(0x29/0x84)* |
| `0x36` | exceedNumberOfAttempts |
| `0x37` | requiredTimeDelayNotExpired |
| `0x38` | secureDataTransmissionRequired |
| `0x39` | secureDataTransmissionNotAllowed |
| `0x3A` | secureDataVerificationFailed |
| `0x50`–`0x57` | certificateVerificationFailed: invalid **time period / signature / chain of trust / type / format / content / scope / certificate** |
| `0x58` | ownershipVerificationFailed |
| `0x59` | challengeCalculationFailed |
| `0x5A` | settingAccessRightsFailed |
| `0x5B` | sessionKeyCreationOrDerivationFailed |
| `0x5C` | configurationDataUsageFailed |
| `0x5D` | deAuthenticationFailed |
| `0x70` | uploadDownloadNotAccepted |
| `0x71` | transferDataSuspended |
| `0x72` | generalProgrammingFailure |
| `0x73` | **wrongBlockSequenceCounter** |
| ⭐ `0x78` | **requestCorrectlyReceived — ResponsePending** |
| `0x7E` | subFunctionNotSupportedInActiveSession |
| `0x7F` | serviceNotSupportedInActiveSession |
| `0x81`–`0x94` | Ön koşul: rpmTooHigh/Low · engineIsRunning/NotRunning · engineRunTimeTooLow · temperatureTooHigh/Low · vehicleSpeedTooHigh/Low · throttlePedalTooHigh/Low · transmissionRangeNotInNeutral/NotInGear · `0x8F` brakeSwitchNotClosed · `0x90` shiftLeverNotInPark · `0x91` torqueConverterClutchLocked · `0x92` voltageTooHigh · `0x93` voltageTooLow · `0x94` resourceTemporarilyNotAvailable |

> ⚠ **2020 belirsizliği:** `0x27–0x32` **ve** `0x34–0x3A`; `0x50–0x57` **ve** `0x58–0x5D` — ISO 14229-1:2020 hem klasik hem genişletilmiş/sertifika setini taşıyor. **Kodlama tamamlanırken hangi setin hangi servise ait olduğu tek tek doğrulanmalı.**

### J.1.3 RoutineControl rutin ID aralıkları

`0x0000` eraseMemory · `0x0001` eraseMirrorMemoryDTCs · `0x0200–0x02FF` checks · `0x0203` eraseMemory · `0xD000–0xD1FF` deployLoopRoutineID · `0xE200–0xE2FF` safetySystem · `0xE300–0xEFFF` specificManufacturer · `0xF000–0xFEFF` systemSupplierSpecific · `0xFF00–0xFFFF` programming

### J.1.4 DoCAN (ISO 15765-2) zamanlama

`P2_CAN_max = 50 ms` · `P2*_CAN = 5000 ms` · `P2_K-Line = 25/50 ms` · `P3_K-Line = 55/5000 ms`
🔴 **`$78` işleme algoritması (J1979 §4.1.4.3.4, birebir uygulayın):** (1) ECU P2CAN içinde `$78` döner; (2) tester + o ECU `P2CAN_max := P2*CAN (5000 ms)` yapar; (3) **başka bir ECU `$78` gönderirse P2CAN_max P2*CAN'a sıfırlanır**; (4) daha fazla zamana ihtiyaç duyan ECU P2\*CAN **süresi dolmadan `$78`'i tekrarlar**; (5) tüm pozitif yanıtlar alındıktan veya P2\*CAN_max dolduktan sonra **P2CAN_max Tablo 5 değerine döner**.
🔴 **J1979 §4.1.4.1:** *"For service $06 the use of a negative response message including response code $22 **is not permitted**."*

## J.2 Denizcilik referansları (Bölüm C.3'ün tamamı)

### J.2.1 NMEA 0183 — güncel sürüm ve tel biçimi

**🔴 v4.30 (Ara 2023)**, v4.11'i (27 Kas 2018) değiştirdi. Tarihçe: 1.xx 1983 → 2.00 1992 (RS-232→RS-422) → 3.00 2000 → 4.00 2008 → 4.10 2012 → 4.11 2018 → **4.30 2023**. Proprietary, ≥ USD 2.000 (Eylül 2020).
`NMEA 0183` = **IEC 61162-1** (4.800-7-N-1; 0183-HS = 38.400 baud, AIS için) · `NMEA 2000` = **IEC 61162-3**

**Tel biçimi:** yazdırılabilir ASCII `0x20`–`0x7E`, **maks 82 karakter** · `$` alan-ayrılmış / `!` kapsüllenmiş (`!AIVDM`) · ilk 5 karakter = **2 talker + 3 biçimlendirici** · `,` ayraç, **mevcut olmayan değerler BOŞ alan** (asla atlanmaz) · kontrol toplamı `*` + 2 hex = **`$` ile `*` arasının XOR'u** · `\` TAG bloğu, `^` ISO/IEC 8859-1 hex, `~` rezerve
**GNSS talker kimlikleri:** `GP` GPS · `GL` GLONASS · `GA` Galileo · `GI` NavIC/IRNSS · `BD`/`GB` BeiDou · `GQ` QZSS
**Proprietary konvansiyonu:** `$P<üretici><biçimlendirici>` — standardize **DEĞİLDİR**, 0183'te olmayan ham RPM/boost/EGT verisinin birincil kaynağı

### J.2.2 Motor ve gemi izleme cümleleri

| Cümle | Anlam | Motor/gemi ilgisi | Doğrulama |
|---|---|---|---|
| `$GPGGA` | GNSS fix | SOG/konum plausibility kanıtı | ✅ |
| `$GPRMC` | Recommended Minimum (**SOG**, **COG**, manyetik varyasyon) | ⭐ **Kayma/performans için SOG** | ✅ |
| `$GPGSV` / `$GPGSA` | Uydular / DOP | ⭐ **GNSS kalitesi & jamleme tespiti** | ✅ |
| `$GPGNS`/`$GPGST`/`$GPGBS` | Fix / pseudorange gürültü / uydu arızası | Bütünlük | ✅ |
| `$HEHDT` | Gerçek başlık | Dümen/başlık telemetrisi | **[K]** |
| `$HCHDG` | Başlık, **sapma**, varyasyon | ⭐ **Sapma = sensör hatası** | ✅ |
| `$HEROT` | Dönüş hızı | Yaw-rate | ✅ |
| `$HERSA` | Dümen sensörü açısı | ⭐ **Dümen aktüatör geri beslemesi** | ✅ |
| `$HESC` (HSC) | Başlık dümenleme komutu | ⭐ **Komut ↔ geri besleme = dümen döngüsü arıza imzası** | ✅ |
| `$WIMDA` / `$IIMDA` | **Meteorolojik bileşik** (baro, hava sıcaklığı, **deniz suyu sıcaklığı**, nem, çiy, rüzgâr) | ⭐ **Baro → yük izleme; su sıcaklığı → soğutma girişi** | ✅ (`pynmea2` örneği) |
| `$IIMVPW`/`$IIMRLW`/`$IIMWV` | Rüzgâr | Görünür/gerçek rüzgâr | [K] |
| 🔴 `$YDBT` | *(Yacht Devices)* yaw/pitch/roll | Ref düzlemi | **[K]** — **büyük olasılıkla proprietary, standart cümle DEĞİL.** ⭐ **Yaw/pitch/roll için N2K PGN 127257 kullanın** |
| `$SDDBT` | Sounder derinlik | `DBT`/`DBS`(**deprecated**→`DPT`)/`DBK`/`DPT` | `SDD` talker **[K]** |
| ⭐ `$VLW` | **Sudan geçilen mesafe** (log + trip) | **0183'teki TEK kilometre sayacı** | ✅ |
| ⭐ `$VHW` | Su hızı & başlık | ⭐ **STW ⇒ akıntı/drift ayrımı** | ✅ |
| `$VWT`/`$VPW`/`$VWR` | Gerçek/bağıl rüzgâr | | ✅ |
| `$ROL` | Roll | Kayma/hizalama | **[K]** — pratikte PGN 127257 |
| ⭐ `$THS` | **Gerçek Başlık ve Durum (geçerli/geçersiz bayrağı)** | ⭐ **0183'teki en değerli uydurma-karşıtı sinyal** | ✅ |
| `$MOB` | Denize düşen | Güvenlik | [K] |
| `$TLB` | Hedef etiketi | Çoklu hedef | [K] |
| `$APB`/`$APA`/`$XTE` | Kaptan, sapma | | ✅ |
| ⭐ `$RPM` (**$IIRPM**) | Devir (motor/mil) | ⭐ **0183 üzerinden motor/mil devri** | ✅ |
| `$DSC`/`$DSE`/`$VDM`/`$VDO`/`$VDR`/`$VDL` | DSC / AIVDM-AIVDO-AIVDR | 0183-HS | ✅ |

**`nmea0183-signalk@3.22.1` 172 ayrıştırma kancası** + **22 SeaTalk datagramı**

### J.2.3 Deniz arıza modu tablosu (⭐ işaretli satırlar Zenodo 19857425'te fiziksel olarak doğrulandı)

| Sistem | Arıza | Ölçülebilir belirti | Otorite |
|---|---|---|---|
| **Ham su soğutma — strainer/göğüs** | Debris, deniz büyümesi, çanta yırtığı | Sabit RPM/yükte debi/basınç ↓, **soğutma sıcaklığı ↑** (127489), **`coolantPressure` ↓**; artış hızı ∝ yük; **RPM'den bağımsız** | `II-1/51.1.1` · ⭐ **`FSS Code 9.1.14`** |
| **Ham su — çark/pompa aşınması** | Kanat aşınması, yüzük boşluğu | Sabit emiş basıncında debi ↓; **döngüsel** sıcaklık titremesi; kavitasyon | `II-1/51.1.1` |
| ⭐ **Soğutma pompası kavitasyonu** (60/85 % yük) | NPSH ihlali | **Ani, yükle-korelasyonlu** basınç çöküşü + sıcaklık sıçraması; artış debi kaybıyla **orantılı değil**; gövde titreşimiyle korelasyon (130311/130314) | **Zenodo 19857425** |
| ⭐ **Hava soğutucu kirlenmesi** (4/4 yük) | Tuz/kömür tortusu | ⭐ **Sabit yakıt/gaz kelebeğinde `boostPressure` ↓` + `temperature` ↑** — **"hava soğutma kaybı" imzası**; ⭐ **belirti yükle ölçeklenir** ⇒ emme tıkanmasından ayırır | **Zenodo 19857425** |
| ⭐ **Kompresör filtresi tıkanması** (4/4 yük) | Emme kısıtlanması | Sabit yükte `boost` ↓, soğutma ↑, **ama şarj-havası-soğutucu diferansiyeli YOK** | **Zenodo 19857425** |
| **Egzoz dirseği karışma dirseği** | Kurum/kireç, su taşıması | `boost`/EGT uyumsuzluğu; su taşıması ⇒ soğutma seviyesi artışı | [K] |
| **Isı eşleyici** | Kirlenme, tüp kaçağı, hava kilidi | Yaklaşma sıcaklığı kayması (130312 çifti); **yükten bağımsız düzensiz** salınım (hava kilidi) | [K] / alarm **FSS 9.1.14** |
| **Yağlama** | Basınç düşüşü, sıcaklık artışı, **basınç değişim hızı** | 127489 `oilPressure`/`oilTemperature`; ⭐ **SOLAS `II-1/52`: düşük yağ basıncı *veya* güvenli sınırları aşan değişim hızı* üzerinde otomatik kapama** | madde no ✅ |
| ⭐ **Enjeksiyon / memiği** (1 ve 2 delikli) | Tıkanma | Silindir-silindir EGT yayılımı ↑, **sabit kelebekte RPM kararsızlığı** | **Zenodo 19857425** |
| ⭐ **Turbo bozulması** (40/60/85 %) | Kirlenme / rulman / wastegate | `boost` ↓ ve **kademeli gazda boost yanıt süresi ↑**; turbineler arası EGT dengesizlik; ıslık frekansı kayması | **Zenodo 19857425** |
| **Şanzıman** | Diş teması aşınması, rulman, düşük yağ, hatalı vites | 127493 `transmissionGear`/`oilPressure`/`oilTemperature`; ⭐ **çıkış mili RPM / motor RPM = gerçek oran** | [K] / PGN ✅ |
| ⭐ **Pervane kayması (slip)** | Kirlenme, eğik kanat, yanlış adım, aşırı yük | ⭐ **`STW` (130778 longitudinal water-referenced) ile `SOG` (129026) farkı akıntı/drift'tir — slip DEĞİLDİR, çıkarılmalıdır.** Mil devri gövde titreşiminden (**Zenodo 21965154**, 102,53 MB telefon ivmeölçeri) | `II-1/31.2.8` "Propeller speed/direction/pitch" **zorunlu gösterge** ✅ |
| **Mil / hizalama / rulman** | Hizalama hatası, bükülmüş mil, rulman aşınması | 1× ve 2× mil sırası genlik+faz; **eksenel 2× = hizalama, radyal 1× = denge**; thrust rulman ⇒ pervane yürümesi | [K] |
| **Dümen** | Mil contası, pompa arızası, faz kaybı, düşük hidrolik, kilitlenme, sensör ofseti | `II-1/29` · `29.5.2` · `29.8.4` · `29.12.2` · `29.11` · `30.1` · `30.3`; ⭐ **PGN 127245 `rudder`: `angleOrder` vs `position` — emir/konum hatası arıza göstergesi**; `$HERSA`/`$HESC`/`$HEROT` | A.1021(26) ✅ |
| **Bilge** | Yüksek seviye, pompa arızası, tıkalı strainer | `II-1/48.1,48.2`; 127505 `fluidLevel`; ⭐ **erken gösterge mutlak seviye değil, YÜKSELME HIZIDIR** | A.1021(26) ✅ |
| **Sahil gücü / AC** | Şarjör/transfer anahtarı, düşük gerilim, yanlış faz | 127503/127504/127507 `operatingState`/`chargeMode`/`equalizationPending` | PGN ✅ |
| **DC güç** | Kükürt, alternatör/kayış, ev yükü düşüşü, toprak arızası | 127508; ⭐ **127506 `stateOfHealth`, `rippleVoltage`**; ⭐ **127514 `generatorOnReason`/`generatorOffReason` — bir *neden***; `II-1/42.5.3,43.5.3`, `II-1/45.4.2`/`53.4.2` | ✅ |
| **Jeneratör yükü** | Aşırı yük, uzun paralel, kötü paylaşım, ters güç, AGS avlanması | 127514 (açma/kapama **neden**), 127509, 127510/127511; 0183'te kW yükü **hiç yok** | PGN ✅ |
| **HVAC** | Kompresör/soğutma devresi, evaporatör buzlanma, kaçak | 130329 (28 alan) **`seaWaterTemperature`**, `loopTemperature`, `evaporatorTemperature`; A.1021(26) IGC/GC `15.4.b` | ✅ |
| ⭐ **Su arıtıcı** | Ön/son filtre, yüksek basınç pompası, tuzluluk, acil durdurma | 130567 (23 alan) **`preFilterPressure`, `postFilterPressure`, `feedPressure`, `systemHighPressure`, `productWaterFlow`, `brineWaterFlow`, `salinity`, `filterStatus`, `emergencyStop`** | ✅ |
| **Çapa / vinç** | Dümen kaçması, solenoid, ip sayacı, zayıf dib | 128777 **`rodeCounterValue`, `windlassLineSpeed`, `anchorDockingStatus`** | ✅ |
| **Tekne gövdesi** | Kirlilik → artan direnç | Yalnız **artan artık direnç**: eşleşen SOG'da `fuelRate` ↑ (127489 vs 130578 su hızı), `instantaneousFuelEconomy` ↑ (127497) | [K] |

> ⭐ **Uydurma-karşıtı not:** Her belirti ya **doğrudan** bir N2K/0183 kanalı ya da **beyan edilmiş provenance sınıfına sahip** bir kanaldan türetilmiştir. **Açık geçerlilik/öncelik bayrağı** taşıyan sinyaller — 0183 `$THS`, N2K **126983 `alert`** (`alertPriority`, `alertState`, `acknowledgeStatus`, `escalationStatus`, `thresholdStatus`), **129029 `gnssPositionData`**, **`130312 temperature.source`** — `EXPERT_KNOWLEDGE_BASE`'in **severity atamadan önce kanıt olarak zorunlu istediği** sinyallerdir; A.1021(26)'nın dört öncelik taksonomisiyle birebir hizalanır.

### J.2.4 Yeni deniz veri kümeleri (2024–2026)

| Veri kümesi | DOI | Lisans | Boyut | İçerik |
|---|---|---|---:|---|
| ⭐ **Marine Engine Fault** | `10.5281/zenodo.19857425` | CC BY 4.0 | 26,54 MB | ⭐ **5 fiziksel olarak enjekte edilmiş arıza, 4 yük noktası, ~115.000 örnek, 70 kanal** |
| ⭐ **Telefon ivmeölçeri → mil devri** | `10.5281/zenodo.21965154` | CC BY 4.0 | **102,53 MB** | Order tracking; eğitim gemisi gövde montajı |
| AIR-MBA Marine Diesel Fault | `10.5281/zenodo.18214751` | CC BY 4.0 | 13,10 MB | Kaggle `ziya07/marine-engine-performance-and-fault-diagnosis-dataset` sarmalayıcı |
| ⭐ **RAG karar-destek korpusları** | `10.5281/zenodo.20258637/.20258638` | CC BY 4.0 | 1 dosya | ⭐ **"Reorganizer-Retriever-Reranker" deniz bakım QA korpusu — copilot KB tohumu** |
| Deniz dizeli rulman teknik durumu | `10.5281/zenodo.22643435` | CC BY 4.0 | küçük | |
| Düşük devirli iki zamanlı NOx | `10.5281/zenodo.21501763` | CC BY 4.0 | 25 dosya | |
| Bant verimli kenar analitiği | `10.5281/zenodo.19257600` | CC BY 4.0 | 4,24 MB | Edge anomalisi + seçici uydu uplink |
| İki zamanlı gömlek yağ filmi | `10.5281/zenodo.21799345` | CC BY 4.0 | 0 dosya | Lazer floresans |
| Jack-up bacak doğal titreşim (WTIV) | `10.5281/zenodo.18772370/.18772371` | CC BY 4.0 | 1 dosya | |
| Kırılma pompalı vagon titreşimi | `10.4121/1a7c2672-…` (4TU) | CC BY 4.0 | — | |
| Makine dairesi havalandırma | `10.6084/m9.figshare.24877305` | CC BY 4.0 | 95 KB | |
| Tuna kefeli avcı gemisi yakıt verimi | `10.5281/zenodo.7533411/.7533412` | CC BY 4.0 | — | |
| Amonyak deniz motoru emisyonları | `10.17632/5h2jjrvnbv.1` | CC BY 4.0 | — | |
| Çift yakıtlı feribot simülasyonu | `10.17632/6rr4kydcd2.1` | CC BY 4.0 | — | |
| Yük etkisi / BSFC (dökme yük gemileri) | `10.17632/ckpjygrzjb.1` | CC-BY ailesi | — | |
| **VMV kademeli planing gövde serisi** | `10.17632/tr59fyyjwb.1` | CC BY 4.0 | — | MITO-31 eşlikçisi |
| Barcelona deniz okulu AIS | `10.34810/data3419` | CC BY 4.0 | ≈935 MB | |
| AegeaNET Syros AIS | `10.5281/zenodo.18926663` | CC BY 4.0 | 2 dosya | |
| Rotterdam Limanı AIS | `10.5281/zenodo.19091478` | CC BY 4.0 | 1 dosya | |
| SAR Ship Gulf (Sentinel-1 + AIS) | `10.5281/zenodo.21000650` | CC BY 4.0 | 3 dosya | |
| AIS nmea örnek kümesi | `10.5281/zenodo.7611497/.7611498` | **MIT** | küçük | |
| ⭐ **SimulaMet Jammertest 2025 NMEA** | `10.21227/a7v4-xw11` (IEEE DataPort) | CC BY 4.0 | — | ⭐ **Gerçek GNSS parazit/jamleme — *sentinel-bounded / plausibility-checked fiziksel telemetri* kuralınızın (AGENTS.md §2.6) doğrudan sınaması** |
| NMEA + İHA, çok-konstellasyon GNSS | `10.21227/84w1-hv02` | CC BY 4.0 | — | |
| RV METEOR GLONASS+GPS | `10.1594/pangaea.728445` | CC BY 3.0 | 80,8 MB | |
| ALBATROSS otonom yelken platformu | `10.5281/zenodo.21485808` | CC BY 4.0 | 1 dosya | |
| Tara hiperspektral biyo-optik | `10.5281/zenodo.19738690` | CC BY 4.0 | 3 dosya | |
| 🔴 **Ship Hull Vinyl Dataset** | `10.25375/uct.25828264` | 🔴 **`.v1`=CC BY 4.0 · `.v2/.v3`=CC BY-NC 4.0** | 6,3–11,5 GB | **Sürümler arası lisans değişmiş — ticari kullanım için `.v1`'i sabitleyin** |
| Gemi kirlilik önleyici kaplamalar | `10.6084/m9.figshare.12296060` | CC BY 4.0 | 517 KB | |
| Marine Fouling Image ×3 | `10.21227/k07g-3s57`, `6dzp-j356`, `0e7h-r986` | CC BY 4.0 | — | |

**Depo envanteri:** Zenodo en zengin · Mendeley Data her iki MITO-31 kaydı · IEEE DataPort · figshare telemetride ince · **PANGAEA araştırma gemisi alet verisinde mükemmel, motor telemetrisi neredeyse yok** · 🔴 **MarineTraffic'ten ücretsiz kamuya açık AIS veri kümesi YOK**
⚠ **Boşluk:** Project Haris ve canboat örnekleri dışında **rekreasyon/planing tekne NMEA-0183 veya CAN/J1939 motor PGN izi içeren hiçbir açık küme 2020–2026'da bulunamadı.**

### J.2.5 Tekne izleme açık kaynak

| Proje | Lisans | Not |
|---|---|---|
| **canboat** | Apache-2.0 (ama PGN DB NMEA telifli) | ⭐ **Cihaz "quirk"leri (gps-rollover, gps-relay, scx20, wmm, motion) varsayılan KAPALI ve asla veri uydurmuyor** · ⭐ **iKonvert/NGT-1'de iletim listesi FINAL — listelenmemiş PGN'ler reddedilir** ⇒ **AGENTS.md §2.1'in istediği fail-closed choke-point deseni** |
| canboatjs | Apache-2.0 (119★) | |
| SignalK (spek / sunucu / discussion) | Apache-2.0 (434★) | **PGN tanımlamaz** — `n2k-signalk` → `canboatjs` → canboat DB |
| `nmea0183-signalk` | Apache-2.0 | **172 kanca + 22 SeaTalk datagramı** |
| 🔴 **OpenSkipper** | **GPL-3.0** (71★) | **ÇİFT YÖNLÜ — N2K *gönderir*.** Dr Andrew Mason, Univ. of Auckland. **GPL-3.0 copyleft bağlarsanız bağlar** |
| OpenCPN | GPL ailesi (1.489★) | |
| `signalk-obd2-monitor` | **MIT** | ⭐ **OBD2 over SignalK — sizin kopyanıza doğrudan benzer** |
| `engineguard` | **MIT** | ESP32 tekne makine dairesi izleyici |
| `pynmea2` 1.19.0 | ⚠️ **LGPL-benzeri [K] — vendor etmeden LICENSE'ı okuyun** | 27 çekirdek + 11 proprietary modül |
| ⭐ `pynmeagps` 1.1.7 | MIT | **89 proprietary GNSS mesajı** — plausibility/provenance katmanı için |
| `nmea` (Rust) 0.8.0 | Apache-2.0, `no_std` | 32 cümle, %39,49 belgelenmiş |

**N2K geçitleri:** Actisense NGT-1 · Digital Yacht iKonvert + NavLink2 · Maretron IPG100 · Linux SocketCAN (Project Haris'in kullandığı) · Yacht Devices YDNG-USB (YDWG-02 açık) · ESP32 DIY (`esp32-nmea2000`, `hatlabs/SH-wg-firmware`, `NMEA2000WifiGateway-with-ESP32`, `M5Stack-NMEA-2000-Display-CAN-BUS`)
**Doğrulanamayan:** BBN Marine, iKrew, Helmüller, Garmin/Airmar/Furuno/Raymarine/Simrad açık parser'ları, Kip/Kipbox (530)

**Denizcilik standartları — doğrulanan:**

| Standart | Durum |
|---|---|
| ⭐ **IMO A.1021(26)** (2 Aralık 2009) *Code on Alerts and Indicators* | **4 öncelik: emergency alarm / alarm / warning / caution** · §4.2 *"both audible and visual means"* · §5.9 *"failure of the power supply or signal-generator device cannot cause loss of function"* · §10.1.2 SOLAS eşleme tablosu · §9.1–9.3 gruplama |
| **IMO A.741(18) ISM Code** (4 Kasım 1993) | §9 şirket, §11 gemi, §12 şirket doğrulama, §13 sertifikasyon |
| **SOLAS 1974** | 🔴 **Yalnız Bölüm V bir eğlence teknesini bağlar** ⇒ SOLAS uyumu *ihtiyaç*; bağlayıcı kanun bayrak devletinin rekreasyon yönetmeliği (USCG 33 CFR 183 subpart K; AB RCD 2014/90/EU + EN ISO 13297) |
| 🔴 **ISO 19847-1/-2/-3, ISO 19848** | **[K] — `iso.org` 403.** Başlıklar ve kapsam tutarlı ama **kataloğa karşı doğrulanamadı**. Deniz odağı için en yakın standart — **lisanslı kaynaktan edinin** |
| 🔴 **ISO 11783-6 = "Virtual terminal"** (Ed.4, 348 s.) — brief'in "kontrol ünitesi teşhisi" iddiası **yanlış**; md.4.3 **Akustik alarm** nesnesi taşır | ✅ |
| 🔴 **ISO 18497 = tarım makineleri HAAM**, 2024'te -1/-2/-3/-4 ile değişti; *yer makinesi* fonksiyonel güvenliği **ISO 19014** | ✅ |
| **ISO 19014-1:2018** | **MCSSA → MPLr**; 🔴 **NOT 4: *"Audible warnings are excluded from the requirements of diagnostic coverage."*** §5 = acil eylem uyarı göstergeleri; **ekipman kökenli tehlikeler (elektrik çarpması, yangın) kapsam dışı** |
| **ISO 25119-1:2018 + Amd 1:2020** | ⭐ **ISOBUS teşhis standardı** — HARA, teşhis kapsamı, güvenli durum |
| Sınıf kuralları (DNV/LR/ABS/BV/ClassNK) | **[K]** — `rules.dnv.com` 507 bayt stub döndürdü |

---

---

# BÖLÜM K — EKSİK İÇERİK (EKLENDİ, 2/2)

## K.1 DBC upstream lisans tablosu (Bölüm A'nın tamamı)

| # | Upstream | Lisans (doğrulandı) | Son hareket | Not / risk |
|---|---|---|---|---|
| 1 | **commaai/opendbc** (120 dosya, 58 düz + 62 `dbc/generator/`) | **MIT** | 2026-09-25 | ⭐ **En temiz tek MIT kaynak.** v0.3.1 (2026-04-22), 397→408 araç, 3.4k★. **Ağaçta GPL/CC **yok** (tam tarball tarandı).** Marka-kodu `_common.dbc` + model `.dbc` → `generator.py` ile `*_generated.dbc` — **sizin birleştirilmiş setinizle aynı mimari** |
| 2 | **canboat** (`docs/canboat.dbc`, 508 KB, 345 `BO_`/1602 `SG_`/554 `CM_`/281 `VAL_`) | **Apache-2.0** (© 2009-2026 Kees Verruijt) | v8.3.0, 2026-09-25 | 🔴 **PGN DB NMEA telifli.** DBC **sentetik 32-bit PGN-türevi ID'ler** (`BO_ 2565341438 PGN_59392_…`) — **gerçek 29-bit arbitration ID değil**; canlı bus'ta kullanmadan dönüştürmek gerekir. Birçok sinyal bozuk metadata taşıyor: `(1,0) [0|0] ""` — 🔴 **aralıkları sıfırlamazsanız plausibility kontrolleri meşru bus değerlerini reddeder (AGENTS.md §2.6)** |
| 3 | **BogGyver/opendbc — `master`** (143 dosya) | 🔴 **LİSANS DOSYASI YOK** (`raw…/master/LICENSE` → 404) | 2021-03-21 (bayat) | ⚠ **En büyük DBC setine sahip dalda lisans iksiri yok** |
| 3b | BogGyver — `tesla_unity_dev` (varsayılan, 121 dosya) | **MIT** | 2022-10-30 | `LICENSES.md`'nin doğru atıfta bulunduğu dal |
| 4 | **fsfarmscaper/jaguar-xf-x250-can** (2 dosya) | **BÖLÜNMÜŞ: MIT (script'ler) + CC BY-SA 4.0 (DBC + dokümanlar)** — `LICENSE` + ayrı `LICENSE-DOCS` ile doğrulandı | 2026-08-22 | Repo **2026-06-30'da kurulmuş, çok yeni.** ⭐ **Yazar bilinçli olarak DBC'leri MIT'ten çıkardı — aleyhine açık, belirsizliğe bırakılmayan bir ikshir** |
| 5 | **dalathegreat/leaf_can_bus_messages** (6 dosya) | **GPL-3.0** | 2026-07-06 | 233★, LEAF ZE0/AZE0/ZE1. **Dosya bazlı lisans istisnası yok** |
| 6 | **qwec01/ARCFOX_dbc** (4 dosya) | **GPL-3.0** | 🔴 **2024-03-24 (2,5 yıl terk)** | 7★. **CJK — cp1252 varsayılanı sessizce bozar.** Yeniden lisans isteyecek bakımcı yok |
| 7 | **berumiya/CAN_DBC_6thGenMazda** (2 dosya) | **CC-BY-4.0** (SA **yok** ⇒ **copyleft yok**) | 2026-07-24 | 2025-12-31'de kurulmuş, aktif |
| 8 | **chris-youngblut-solutions/opendbc-ag** (3 dosya, 2.690 PGN) | MIT *iddia ediyor* | 2026-06-18 | 🔴 **`extract_vdma.py` ile isobus.net VDMA DD'sini scrape edip MIT olarak yeniden lisanslıyor** — "2.627/2.690 PGN VDMA Data Dictionary'den" |
| 9 | **nberlette/canbus** (5 dosya) | MIT (`license.md`, küçük harf — kök `LICENSE` **yok**) | 2025-07-22 | ⚠ **`dbc/j1939.dbc` içinde gömülü NOTICE: *"yalnızca eğitim amaçlı örnek; üretim veya ticari uygulamada kullanımı amaçlanmamıştır"* — `LICENSES.md` yalnız MIT diyor** |
| 10 | joshwardell/model3dbc (1 dosya) | MIT | 2023-10-24 | 409★, 3 yıl push yok |
| 11 | autti/abraham (1) | MIT | 2018-07-12 | Fiilen terk (8 yıl) |
| 12 | alan707/openOBD2 (1) | MIT | 2019-05-30 | Terk (7 yıl) |
| 13 | ~~shemps/byd-atto3~~ | ❌ **REPO 404** | — | `LICENSES.md` **var olmayan repo'ya** atıf yapıyor |
| 14 | arthithadee/J1939-DBC-Database | MIT | 2026-04-14 | 1★, yalnız 4–5 PGN — kaynak olarak işe yaramaz |

**DBC biçim tuzakları (219 dosya üzerinde ölçüldü):**

| Tuzak | Sayı | Not |
|---|---:|---|
| `VERSION` bloğu **yok** | 55/219 (%25,1) | **Hata değil** — `VERSION` opsiyonel, boş sürüm geçerli. Normalleştirici gerektirmemeli |
| `BS_:` eksik | 58/219 (%26,5) | *"REQUIRED KEYWORD ama bölüm genelde boş … OBSOLETE"* |
| **Çoklu `VERSION` bloğu** | 4/219 | `j1939_isobus_vdma_all.dbc` ×3, `j1939_isobus_ag.dbc` ×2 — neredeyse kesin **kötü birleştirme**; parser sessizce ilk ya da sonuncuyu alır |
| **Dosya içi tekrarlı `BO_` ID** | 4/219 | 🔴 **`marine/n2k_canboat.dbc` 57 tekrarlı ID**; `heavy_duty/j1939_canboat.dbc` 2 — **CAN ID üzerinden anahtarlanan decoder belirsiz bağlanır** |
| Non-ASCII bayt | 32/219 | 0 geçersiz UTF-8 ama CJK `CM_`/`VAL_` var |

> 🔴 **Kodlama tuzağı kesin:** cantools `.dbc` için varsayılan olarak **`cp1252`** kullanır, UTF-8 değil. UTF-8 kodlanmış Çince bir `CM_` **istisna fırlatmadan sessizce mojibake** olur. **`encoding='utf-8'` açıkça geçirin veya koklayın.**

**DBC araçları 2026:** `cantools` **44.1.0 (2026-09-16)** MIT (241 sürüm, **en aktif**) · `canmatrix` 1.2 BSD-2-Clause ⚠ 19 aydır sürüm yok · `dbc-rs` (`sigma tactical`) **en kapsamlı açık DBC grameri + tuzak belgesi** · `can-dbc` (Rust) 10.0.0 · 🔴 **`vector_dbc` (nberlette) `LICENSE.GPL-3.0` ile geliyor** — proprietary ürüne bağlama · `can_tools` 2.1.2 MIT (cp1252 toleranslı) · Vector CANdb++ referans · `savvycan` DBC Manager **GPL-2.0** · CSS Electronics DBC Editor (ücretsiz)
🔴 **"Vector DBC 5.x" resmî bir spesifikasyon DEĞİLDİR** — belgelenen şey CANdb++'ın J1939 format bölünmesi.

## K.2 Paket sağlığı tablosu (Bölüm G'nin tamamı)

| Paket | Sürüm | Tarih | Lisans | İndirme/ay | Karar |
|---|---|---|---|---:|---|
| ⭐ `python-can` | **4.6.1** | 2025-08-12 | 🔴 **LGPL-3.0-only** | 3.188.346 | **Al** — ama **LGPL bir ticari lisanslama kararıdır** |
| ⭐ `cantools` | **44.1.0** | **2026-09-16** | MIT | 992.999 | **Al, sert sabitle** (hızlı sürüm temposu) |
| `canmatrix` | 1.2 | 2025-02-17 | BSD-2-Clause | 670.553 | ⚠ `==1.2` sabitle; `development` dalında düzeltmeler var |
| `udsoncan` | 1.26.1 | 2026-06-25 | MIT | 176.728 | **Al** — 724★, 41 sürüm, **tek bakımcı** |
| `can-isotp` | 2.0.7 | 2025-05-14 | MIT | 221.220 | **Al** |
| `canopen` (= python-canopen) | 2.4.1 | 2025-08-05 | ⚠ **LICENSE'ı doğrulayın** | 631.092 | Al (CANopen/EDS/PDO) |
| `nmea2000` (tomer-w) | 2026.8.1 | 2026-08-22 | Apache-2.0 | 3.138 (58/gün) | **Al** — kod çok aktif, **benimseme çok küçük** (HA'nın N2K entegrasyonunun arkasında) |
| `asammdf` | 8.8.27 | 2026-09-07 | 🔴 **LGPL-3.0-or-later** | 729.466 | **Al** — aynı LGPL uyarısı |
| `openDBC` | 0.3.1 | 2026-04-22 | MIT | (pypistats 404) | Repo tarafından |
| 🔴 `obd` | 0.7.3 | 2025-04-07 | **GPL-2.0-only** | 15.420 | ⛔ **Bağlama** |
| ⭐ `license-expression` | 30.4.4 | 2025-07-22 | Apache-2.0 | 31.622.842 | Manifest doğrulama kütüphanesi |
| `pip-licenses` | **5.5.5** | **2026-03-28** | MIT | 3.218.600 | CI'da `THIRD_PARTY_NOTICES` üretimi |
| `licensecheck` | 2026.0.8 | 2026-06-18 | MIT | 379.872 | **Metadata sürüklenme kapısı** |
| ⭐ `reuse` (FSFE) | **6.2.0** | 2025-10-27 | Apache-2.0 | 639.410 | ⭐ **Tek yanıt: "Notices dosyası için en iyi pratik"** |
| `scancode-toolkit` | 32.5.0 | 2026-01-15 | Apache-2.0 | 81.335 | Vendor edilmiş veri dosyaları için |
| `cyclonedx-bom` | **7.4.0** | **2026-09-15** | Apache-2.0 | 1.594.983 | **SBOM** |
| `spdx-tools` | 0.8.5 | 2026-03-13 | Apache-2.0 | 3.828.027 | SPDX 2.3 |
| 🔴 `tern` | 2.12.1 | 2023-07-14 | Apache-2.0 | — | ⛔ Terk edilmiş |
| `shiv` / `pex` / `briefcase` | — | — | — | — | ⛔ Doğrulanmadı |
| `pyinstaller` | doğrulanamadı | — | GPL + bundler istisnası | — | **Dal hiç dönmedi** |
| `nuitka` | doğrulanamadı | — | Apache-2.0 (ticari sürüm de var) | — | **Dal hiç dönmedi** |

**2026'da var olmayan / yeniden adlandırılan paketler (PyPI JSON API'si ile 404 doğrulandı):**
`isobus` · `isobus2` · `isobus3` · `isobuspython` · `isobuslib` · `iso14229` · `python-isotp` · `isotp` · `isotpn` · `isotp-ng` · `mdflib` · `savvycan` · `ngsim` · `python-OBD` · `python-canopen` · `python-can-matrix` · `can-bridge` · `adafruit-circuitpython-canbus` · `licensedb` · `reuse-software` · `cyclonedx` · `osv-scalibr` · `spdx3` · `importlib-license`

**Düzeltilmesi gereken adlar:** `python-canopen`→**`canopen`** · `can-bridge`→**`can_bridge`** (python-can 4.6.0'da `python -m can.bridge`) · `pycan`→`python-can` · `cyclonedx-py`→**`cyclonedx-bom`** takma adı · `scancode-licensedb`→**`scancode-toolkit`** içinde

**ISOBUS boşluğu:** `jboomer/python-isobus` **son commit 2017-03-15** (9 YIL ÖLÜ) · `GwnDaan/python-isobus-library` kendi ifadesiyle *"(incomplete)"* · ⭐ **`Open-Agriculture/AgIsoStack++`** (C++, 2026-09-23'te çoklu merge) **referans uygulama** — commit `c0e1ebb` (2026-09-19) *"Sync DDI enum, table and value formatters with the **ISO 11783-11 2026090801** export"* · `AgIsoStack-rs` · `AGRIForward/ROS2ISOBUS`

**Diğer ekosistem ölçümleri:** SPDX License List **3.29.0 (2026-09-16)** · SPDX Spesifikasyonu **3.0** (ISO/IEC 5962:2021) · ⭐ **REUSE Specification 3.3 (2024-11-14)** · CycloneDX **1.7 (2025-10-21, ECMA-424 2. baskı)** — Data Provenance & Citations, IP Transparency, **CBOM**, harici bileşenler · gettext **1.0 (2026-01-28)** · CLDR **48.2 (2026-03-17)**, 49 beta · UCD **18.0.0 (2026-05-19)** · ICU **79**

**CISA 2026 SBOM Minimum Elements (Temmuz 2026) — CRA alıntısı:** *"The European Union's Cyber Resilience Act (Regulation (EU) 2024/2847) **requires manufacturers of products with digital elements to provide an SBOM** as part of the technical documentation."* · 🔴 **CRA imzalı SBOM ZORUNLU TUTMAZ** (makine-okunur biçim + üst düzey bağımlılıklar ister) — güçlü çıkarım, doğrulanmış alıntı değil · **AI Act hiç SBOM-imza şartı içermez** · Almanya **BSI TR-03183-2** · Hindistan **CERT-In Technical Guidelines on Bill of Materials**

## K.3 35 alanlı DTC şeması — tam liste (Bölüm F.1'in tamamı)

| # | Alan | Zorunlu | Not / normatif dayanak |
|---|---|:-:|---|
| 1 | `record_id` | ✔ | dilden bağımsız kararlı slug (deterministik hash) |
| 2 | `dtc_code` | ✔ | `P0420` · `SPN 3216 FMI 4` · `U0100` |
| 3 | ⭐ `dtc_namespace` | ✔ | `SAE_J2012`\|`SAE_J2012_4`\|`ISO_14229_1`\|`OBD`\|`WWH_OBD`\|`J1939`\|`OEM` — ⭐ **AUTOSAR: kayıt başına tek format mümkün** (`"A combination of different DTC formats is not possible"`) |
| 4 | ⭐ `dtc_class` | ✔ | `NoClass`\|`A`\|`B1`\|`B2`\|`C` — **ISO 27145-3 sınıfları, MIL mantığını sürer** |
| 5 | `severity` | ✔ | 5 kanonik değer |
| 6 | `severity_raw` | ✔ | ⭐ **22 ham dizeyi ASLA atmayın — o provenance'dır** |
| 7 | `severity_norm_rule` | türetilmişse ✔ | `SEV-R3` gibi, sürüm sabitli kural kimliği |
| 8 | `mil_effect` | ✔ | `none`\|`on`\|`blink`\|`on_after_X` |
| 9 | `title{en,tr,…}` | ✔ her dil | Güvenlik yolunda **makine çevirisi yok** |
| 10 | `description{en,tr}` | ✔ | ⭐ **≥20 token kuralı = anti-stub kapısı** |
| 11 | `system` / `subsystem` | ✔ | J2012 sütunları |
| 12 | `component` | ✔ | |
| 13 | `component_location` | – | J2012 "Component Location" |
| 14 | ⭐ `validating_symptoms` (**belirti id dizisi**) | ✔ | ⭐ **%93,9'luk boşluğun tek çözümü — ayrı kanonik belirti tablosuna ID** |
| 15 | `symptoms[]` | ✔ | `{symptom_id, text{en,tr}, observable, operator_reported}` |
| 16 | `detecting_conditions[]` | ✔ | `{condition_id, text{en,tr}, signal_ref, op_min, op_max, unit}` — ⭐ **makine-değerlendirilebilir** |
| 17 | `detection_logic` | ✔ | `range`\|`rationality`\|`rate_of_change`\|`circuit_continuity`\|`sub_function`\|`debounced_count`\|`time_qualified` |
| 18 | `debounce` | ✔ | `{type: counter\|time, counter_threshold, time_ms, timebase_s}` |
| 19 | `confirmation` | ✔ | `{model: none\|n_cycles\|n_ops\|time, n, operation_cycle_ref}` |
| 20 | `aging` | ✔ | `{type, threshold, counter_ref}` |
| 21 | `false_positive_avoidance[]` | – | ISO 26262-6 tanı kavramı |
| 22 | `monitoring` | – | `{monitor_id, obd_pid, obd_monitor_id, continuous, readiness_group_ref}` |
| 23 | `status_bits` (8 bool) + ⭐ `status_byte` (hex int) | ✔ | **Tel sadakati için baytı da saklayın.** bit 0 TestFailed · bit 3 Confirmed · bit 6 testedThisOperationCycle |
| 24 | `status_availability_mask` | – | `DTCStatusAvailabilityMask` |
| 25 | `snapshot_records[]` | ✔ | `{record_number 1..254, store_trigger, did_ref, data_elements[]}` — **kronolojik sıra atanmış** |
| 26 | `extended_data[]` | ✔ | `0x19 0x06` ile raporlanır |
| 27 | `freeze_frame` | ✔ | `0x19 0x04` |
| 28 | `j1939` | – | `{spn 0..524287, fmi, occurrence_count, conversion_method_ref, lamp_strategy, filter_mask_ref, freeze_frame_class_ref}` |
| 29 | `spn_fault_matrix_refs[]` | – | 53 düğümlük grafiğe FK |
| 30 | `confirming_tests[]` | ✔ | ⭐ **makine-değerlendirilebilir `pass_criteria` ile** — prosedür değil, test olmalı |
| 31 | `repair` | ✔ | 601 prosedür JSON'unu id ile bağlar |
| 32 | `deactivation` | – | `0x19 0x1A`; ⭐ **"UI asla bastıramaz"** değişmezi |
| 33 | `related_codes[]` | – | `CombinedDTC` ilişkileri |
| 34 | ⭐ `provenance[]` | ✔ | **Zorunlu, boş olamaz** |
| 35 | `stub_flag` (hesaplanır) | ✔ | CI hesaplar; `true` ise kalibrasyon uygunluğunu engeller |

**AUTOSAR Dem R24-11 kavram sözlüğü:** `Event` · `Event confirmation` · `Event memory` · `Event memory overflow indication` · `Event qualification` · `Monitored component` · ⭐ **`CombinedDTC`** (*"Normal DTC, but referenced by multiple events reported by several monitors"*) · `Class B1 counter` · `Continuous-MI counter` · `Cumulative Continuous-MI counter` · ⭐ **`Debounce counter`** · `Aging` / `Aging counter`

**Yer değiştirme stratejileri:** `DEM_DISPLACEMENT_FULL` · `DEM_DISPLACEMENT_PRIO_OCC` — ⭐ *"Fuel system/Misfire over the rest (US/EU5) and **Class A before B1, B2, C (Euro VI)**"*

**Bibliyografya (2026) — EP kuralı üretimi ve doğrulama:**
- ⭐ **CAREP** (arXiv:2602.01155) — EP kuralları elle yazılıyor, pahalı, hataya açık. **29.100+ benzersiz DTC, 474 EP.** Bilinmeyen EP'leri otomatik keşfediyor, **LLM-only baseline'ları geçerken şeffaf nedensel izleri koruyor**
- **BiCarFormer** (arXiv:2602.01109) — 22.137 hata kodu, 360 EP
- **Doktora tezi** (arXiv:2603.16313, 135 s.) — Boolean EP kural sentezi
- ⭐ **Modality Dropout** (arXiv:2608.23161) — ağır sanayi tanılaması = *"unstructured multi-lingual service complaints"*; metin-tek %65,3 → metin+DTC+dropout **%68,8**; sınıf bazlı alım/egzoz sensör %93 / metin %80, yakıt %15→%38. ⭐ **Türkçe belirti metniniz ile J1939 SPN telemetriniz tamamlayıcıdır; eksik değer ağırlıklı telemetri tablosu normaldir**
- **KG + LLM** (Electronics 2025) — **AGENTS.md §2.8'in yasakladığı miminin tam kontrast vakası**
- **LLM Ensemble HiL** (arXiv:2608.10710) — MCC 0,902 — kaçındığınız karşı-teva
- Halderman *Advanced Automotive Fault Diagnosis* 4. baskı 2016 — belirti-tabanlı ↔ DTC-tabanlı ayrımının fiili referansı

## K.4 Yükleme protokolü tablosu (Bölüm E.4'ün tamamı)

| Protokol | Sürüm | Oturum | Sürdürme probu | Bütünlük | Süre |
|---|---|---|---|---|---|
| ⭐ **tus 1.0** | 1.0.0, spec 2016-03-25 | `POST` + `Upload-Length` → `201` + `Location` | `HEAD` → `Upload-Offset` (**her zaman** olmalı, 0 olsa bile), `Cache-Control: no-store` | `Upload-Checksum: <alg> <base64>`; sunucu `sha1` **zorunlu**; 🔴 `460 Checksum Mismatch` | `Upload-Expires` |
| tus paralel | — | `Upload-Concat: partial` → `final;<url1> <url2>` | final URL'de `PATCH` **403** almalı | final `Upload-Offset == Upload-Length` | sunucu parçaları silebilir |
| tus keşif | — | `OPTIONS` → `Tus-Version`/`Tus-Extension`/`Tus-Max-Size` | 🔴 sürüm uyuşmazlığı → **`412 Precondition Failed`** | — | — |
| **S3 Multipart** | güncel | `CreateMultipartUpload` (**checksum tipi ZORUNLU**) → `UploadId` | `ListParts` (**maks 1.000**/sayfa) — ⚠ *"Do not use the result of this listing when sending a complete request"*, **kendi listenizi tutun** | §K.4.1 | 🔴 **başlatmadan sonra bitmez — ücret ödersiniz** |
| **GCS resumable** | güncel | `POST …?uploadType=resumable` → session URI | `Content-Range: bytes */SIZE` → **`308`** + `Range`; `200/201` bitti, `499` iptal, `204` finalleşti | 🔴 **YALNIZ son nesnede `Content-MD5`** — parça kontrolü **YAPILAMAZ** | **1 hafta** |
| **Azure Block Blob** | 2019-12-12+ | `Put Block` + **`Put Block List`** | `Get Block List` | blok MD5; **bir blok eksikse tüm commit başarısız** | **1 hafta**, maks 50.000 blok |
| **HTTP** | **RFC 9110, Internet Standard, Haziran 2022** | — | §14 Range · `Accept-Ranges: bytes` · `Content-Range` | — | — |

### K.4.1 S3 checksum matrisi

| Algoritma | Tip | `CreateMultipartUpload` | `UploadPart` | `Complete` |
|---|---|---|---|---|
| ⭐ **CRC-64/NVME** | tam nesne | **ZORUNLU** | opsiyonel | opsiyonel |
| CRC-32 / CRC-32C | tam nesne | **ZORUNLU** + `x-amz-checksum-type` | opsiyonel | opsiyonel |
| CRC32C/SHA1/SHA256/MD5/XXHash64/3/128/SHA512 | **karma** | **ZORUNLU** | **ZORUNLU** | **ZORUNLU** tüm parça checksum'ları geri yankılanır |

🔴 *"Amazon S3 automatically uses the **CRC-64/NVME**… this is also the recommended option."* Rust/Go istemci için CRC32C tercih edin.
🔴 **Karma checksum'da parça numaraları 1'den başlayarak ARALIKSIZ olmak ZORUNDA — değilse HTTP 500** (400 değil).
🔴 **SSE-C/KMS izni eksikse tam nesne checksum'suz OLUŞURULUR.** Parça `ETag`'ı nesnenin MD5'i değil; tam nesne ETag'ı *checksum'ların checksum'ı*.
İzinler: Create/Upload/Complete ⇒ `s3:PutObject` · Abort ⇒ `s3:AbortMultipartUpload` · ListParts ⇒ `s3:ListMultipartUploadParts` · ListMPU ⇒ `s3:ListBucketMultipartUploads`

**Yeniden deneme:** GCS — 🔴 *"those bytes **cannot be overwritten**"* ⇒ **sürdürmeden önce daima ofseti yeniden yoklayın**; parça boyutu **256 KiB'nin katı**; oturumlar başlatma bölgesine sabit. S3 — yalnız **başarısız parçaları** yeniden deneyin; ⚠ *"make sure you free all storage consumed by all parts"*. tus — 🔴 **`460`/`400` alınca yüklenen parça ATILMALI ve ofset GÜNCELLENMEMELİDİR.**

## K.5 Broker ve zaman serisi tabloları (Bölüm E.4'ün tamamı)

| Broker | Sürüm | Lisans | Not |
|---|---|---|---|
| ⭐ **Eclipse Mosquitto** | **2.1.2** (2026-02-09) | **EPL-2.0 OR EDL-1.0** (2.0.6'da SPDX'i BSD-3-Clause oldu, *"licenses are identical"*) | 2.1.0: ⭐ **`sparkplug-aware` + `persist-sqlite`** (SQLite3 zorunlu derleme bağımlılığı). Yerleşik WebSocket, PROXY v1/v2, kqueue, systemd watchdog. 🔴 **`max_packet_size` artık 2.000.000 bayt** |
| Apache Kafka | 4.3.1 (2026-06-25) | Apache-2.0 | **KRaft tek mod**, **Java 17+**. Tek edge gateway için aşırı |
| NATS | 2.15 | Apache-2.0 *(doğrulanmadı)* | `max_payload` 1 MB varsayılan, 64 MB sert üst sınır. Yerel `mqtt` bloğu, JetStream, `no_fast_producer_stall` |
| RabbitMQ | doğrulanamadı | **MPL-2.0** | ⚠ **4.0 öncesi SPL'den değişti** |
| 🔴 Redpanda | doğrulanamadı | **BSL + RCL** | *"will at some point… become Apache 2"* + kurumsal özellikler ayrı **OSS olmayan** lisans |
| **MQTT 5.0** | **OASIS Standard, 7 Mart 2019** | **Non-Assertion Mode** | Shared Subscriptions · `$` öneki rezerve · ⭐ **WebSocket taşıması NORMATİF** · §5.4.5 **uygulama mesajı düzeyinde şifreleme** |

| Zaman serisi | Sürüm | Lisans | Yazma karakteristiği |
|---|---|---|---|
| ⭐ **QuestDB** | **10.0.1** | **Apache-2.0** | Yerel kolonlu, WAL→nesne deposu; **8M+ satır/sn**, 100K+ ticker, <10 ms; **Airbus günde milyarlarca nokta.** OSS = tek örnek, replika/HA yok |
| ClickHouse | doğrulanamadı | **Apache-2.0** | Merge-tree, toplu ekleme |
| **InfluxDB 3 Core** | 3.11 | ⭐ **AGPL DEĞİLDİR** (Apache-2.0/MIT) | Diskless + Parquet; ⭐ **AMA depo 2026-02-27'de arşivlendi**, `latest` etiketi 2026-09-15'te döndü, **HA yok** |
| VictoriaMetrics | v1.152.0 (2026-09-11) | Topluluk/Enterprise ayrımı ⚠ | Yüksek kardinaliteli metrikler |
| Prometheus | v3.15.0 (2026-09-24) | **Apache-2.0** | **Yalnız yerel, tek yazıcı, çekme.** **Agent mode** var |
| **TimescaleDB** | 2.30.1 (2026-09-17) | ⚠ **Timescale License** | ⭐ **`DeferredChunkAppend`** — "son okuma"yı O(chunks)→**O(1)**; ⭐ **PG15 desteği 2.29.0'da KALDIRILDI → yalnız PG 16/17/18** |
| **DuckDB** | v1.5.5 (2026-07-22) | MIT (⚠ doğrulanmadı) | Parquet/Iceberg/Lance/ADBC/DuckLake |

**OpenTelemetry kardinalite:** ⭐ **Varsayılan sert limit 2000** (kaynakta "2000 **SHOULD** be used"). Taşma öznitelik kümesi **`otel.metric.overflow = true`**. *"Measurements **MUST NOT** be double-counted or dropped during an overflow."* ⇒ 🔴 **`(arbitration_id, bus, ecu_address, dtc_code, scenario)` kombinasyonu saniyeler içinde 2000'i aşar ve sessizce çöker. Telemetri **metrik değil event/log olmalıdır.****
**OTLP:** gRPC **4317** / HTTP **4318** · mesaj boyutu sunucu önerisi **64 MiB** · tekrar denetilebilir HTTP yalnız **429/502/503/504** · 🔴 **"OTLP does not use explicit protocol version numbering"** — `"OTLP v1.2"` diye bir şey yok · 🔴 **bilinen sınır: *"the client will typically choose to re-send such data… **This is a deliberate choice**"* — **cloud yükleyiciniz kendi tarafında idempotent olmalı**
**Throughput tavanı (spec'in kendi örneği):** 100 span, 200 ms RTT, 300 ms sunucu süresi ⇒ **tek eşzamanlı istekte 200 span/s** ⇒ ⭐ **kare-granülar exporter matematiksel olarak imkânsız; batch zorunlu**
**Prometheus:** *"Do not use labels to store dimensions with high cardinality"* · ⭐ **gecikmeli histogram için OTel Base2 üstel histogram (MaxSize 160, MaxScale 20) kullanın** — 1 ms–100 s / <%5 hata tam olarak tasarım noktanız

**ASAM MDF 4.3.0 ek notları:** *"Distributed data blocks even make it possible to directly write sorted MDF files"* — ⭐ **format-native toplu/vektörel çözme cevabı** · *"allows storage of raw measurement values and corresponding conversion formulas; therefore raw data can still be interpreted correctly"* — ⭐ **deponun en güçlü uydurma-karşıtı hizalaması**

## K.6 UI ve i18n araç karşılaştırma tabloları (Bölüm E.6'nın tamamı)

| Seçenek | Sürüm (tarih) | Motor | Ayak izi | Python | Lisans | Güvenlik |
|---|---|---|---|---|---|---|
| ⭐ **pywebview** | **6.2.1 (2026-04-15)** | Win: **WebView2**→`mshtml`(⚠); macOS WKWebView; Linux WebKitGTK; opsiyonel Qt/CEF/Android | İnce shim, **sistem webview'ını kullanır** | Native | **BSD-3-Clause** | ⭐ CVE geçmişi yok (NVD + GH Advisory'de 0). 🔴 `SECURITY.md` *"only latest release supported"*. 🔴 **`mshtml` yedeği güvenlik karar noktası** |
| Electron | 44.4.5 (2026-09-23) | Chromium paketler | ⭐ **122.995.981 B (~117 MiB)** sıkıştırılmış, koddan önce | Yok (Node) | **MIT** + Chromium BSD | Sandbox'lı renderer; büyük yüzey |
| **Tauri 2** | stable **2.11.6** (3.0.0-alpha.2 var — kararlı **demeyin**) | Sistem webview | ⭐ **"<600 KB"** (satıcı iddiası) | Sidecar | **Apache-2.0 OR MIT** | ⭐ **Radically Open Security tarafından denetlendi**; izin/CSP modeli |
| Wails | 2.14.0 (2026-08-10) | Sistem webview | Küçük | Sidecar | MIT | En ince API; en az güvenlik pazarlaması |
| Neutralino | 6.9.0 (2026-07-24) | Sistem webview | **8.050.711 B (~7,7 MiB)** | Sidecar | MIT | ⭐ **`net.*` API webview CORS'unu atlar (kendi belgelediği risk)** |
| Düz WebView2 host (C#/.NET) | Edge Stable **154** | WebView2 (Evergreen) | ~1,5 MB | Sidecar Python | MS runtime şartları | ⭐ **En iyi belgelenmiş sertleştirme rehberi** |
| ⛔ `cefpython3` | 66.1 (2021-02-16) | **CEF/Chrome 66 (2018)** | Win tekerlek ~65–69 MB | Native | BSD-3 | ⛔ **Yeni iş için kullanmayın** |

**pywebview backend tablosu (Windows seçim sırası `edgechromium` → `mshtml`; `PYWEBVIEW_GUI` ile geçersiz kılınabilir):** macOS `cocoa`→`WebKit.WKWebView` · Win `edgechromium`→Chromium(WebView2)→MSHTML · Win `cef` extra → **CEF 66** · Linux `gtk`→WebKit2(>2.2) · Linux `qt`→QtWebEngine · Android `android`

**pywebview köprüsü — neyin açık olduğu:** `window.pywebview.api.<method>` **`create_window(js_api=…)` veya `window.expose(func)` ile ne yayınlarsanız odur.** *"Method name must not start with an underscore."* 🔴 **Allow/deny listesi, imza doğrulama, origin kontrolü, hız sınırı YOK.** `expose()` **çalışma zamanında** yetenek kümesi büyütebilir (6.2 bir yarış koşulu düzeltti). **Expose edilen fonksiyonlar ayrı thread'lerde çalışır ve thread-safe DEĞİLDİR.** `evaluate_js` kodu sarmalayıp `eval`'ler; `run_js` olduğu gibi çalıştırır. `window.state` her üst düzey özellik atamasında yayılır (6.0'da yeni) — **herhangi bir sayfa betiği renderer-görünür durumu yazabilir.**

🔴 **Yapılandırma tehlikeleri:** `ALLOW_FILE_URLS` **varsayılan `True`** (5.0.1) · `REMOTE_DEBUGGING_PORT` ve `WEBVIEW2_RUNTIME_PATH` mevcut (6.1/5.4) · **üretim teşhis aracında uzak hata ayıklama portu = tam süreç ihlali**
**WebView2 bayrak farkları:** 🔴 SmartScreen `--msSmartScreenProtection` *"**Makes SmartScreen protection available**"* ⇒ **varsayılan KAPALI** · 🔴 Local Network Access kontrolleri *"**Disabled by default; must be enabled by the app**"* · 🔴 **Yükseltilmiş (elevated) host'ta WebView2 `WEBVIEW2_*` env bayraklarını ve `HKCU` override'larını YOK SAYAR** · **SYSTEM olarak çalışamaz** (Credential Provider senaryolarını bloklar) · UDF `.exe` yanında varsayılan — *"**in most cases, you should specify a custom UDF**"*
**MS sertleştirme rehberi (7 madde):** *"**Treat all web content as insecure**"* (her `ExecuteScript`/`PostWebMessageAsJson`/`WebMessageReceived` öncesi `Source` kontrolü) · *"**Avoid generic proxies**"* · `PostWebMessageAsJson` kullanın · `AreHostObjectsAllowed=false`, `IsWebMessageEnabled=false`, `IsScriptEnabled=false`, `AreDefaultScriptDialogsEnabled=false` · navigasyonda per-origin ayar · **standart (yükseltilmemiş) bütünlükte çalıştırın** · SYSTEM olarak çalışamaz
🔴 **pywebview bu anahtarları sunmuyor** — `js_api`'si WebView2 modelinden **daha kaba**. Orijin-gated mesajlaşma gerekiyorsa **kendi ince host'unuzu yazın.**

| i18n aracı | Sürüm | Model | TR desteği | EN/TR teknik terim | Pseudo-loc |
|---|---|---|---|---|---|
| **GNU gettext** | **1.0 (2026-01-28)** | `.po`/`.mo`, `msgctxt` ile bağlam ayrımı | `tr_TR` katalog; 🔴 **araç yerel-duyarsız — `msgstr` büyütmesi sizin sorumluluğunuzda** | ⭐ **Mükemmel:** `msgctxt` + `msgid_plural` "CAN bus"/"busbar" çakışmasını çözer | Yok (elle) |
| **ICU MessageFormat** | **ICU 79** | `{n, plural}` / `{d, date}` — biçimleme + çeviri tek sözdiziminde | Yerel `tr` çoğul kuralları, `tr_TR` sayı/tarih desenleri, yerel `i` büyütmesi | ⭐ **Dizeye gömülü birim/sayı/para için en iyi**; terim ayrımı zayıf (`ctxt` yok) | Yok |
| ⭐ **Fluent (Mozilla)** | Spec doğrulanamadı | AST: `term` / `message` / `attribute` / `select` / `plural` / `selectordinal` | ⭐ **Dil-agnostik tasarım; `tr-TR` büyütmesi çevirmene görünür; Firefox'in bayrak TR yerelleştirmesi** | ⭐ **Üçünden en iyisi:** `terms` "PGN"/"SPN" için tek kaynak; mesaj başına `-term`/`-context` varyantı | ⭐ **Yerleşik** (Firefox transform) |
| **CLDR** | **48.2** / 49 beta | **Veri** (çoğul, sayı, para, tarih, birim, sıralama) | `tr` için kanonik; ICU/Babel/`Intl` üzerinden dolaylı | *"Türkçe ne yapar"*ı verir, *"nasıl söylenir"*i değil | `psuLocale` varyantları |
| **Python Babel** | doğrulanamadı | gettext uyumlu + CLDR biçimleme | ⭐ **Python tarafında doğru `tr_TR` sayı/tarih/para** | | Yok |

**Pseudo-localizasyon CI önerisi:** EN→TR panoda iki hata kipi = **+%30–40 uzunluk** ve sentinel karakterler (`$`, `#`, `{}`, `%`). ⭐ **TR bağlamında pseudo-localizer büyütme yapacaksa `toLocaleUpperCase('tr')` semantiğini kullanmalı — `toUpperCase()` değil.** **`msgfmt --check` ile katalog kaçağı build'i kırsın** (fail-closed lokalizasyon kapısı).

## K.7 Paketleme/dağıtım — ⚠️ **SONUÇ ALINAMADI**

`f24b3bd9c` dalı (PyInstaller/Nuitka, `--add-data`, veri yolu çözümleme, `platformdirs`, imzalama/notarization, updater'lar) **hiç sonuç döndürmedi.** Bu nedenle şu başlıklar **bu konsolide raporda boştur:**

- PyInstaller güncel sürümü, `--add-data` sözdizimi, `onefile`/`onedir` trade-off'ları, GPL + bundler istisnasının ticari ürün etkisi, hook/runtime-hook gereksinimleri, HMAC-doğrulanan dosyaların sıkıştırma/at-rest davranışı
- Nuitka güncel sürümü + ticari durum, `--include-data-dir`/`--include-package-data`, startup süresi/obfuscation karşılaştırması
- Alternatifler: cx_Freeze · Briefcase (BeeWare) · PyOxidizer · `python-build-standalone` · Docker/AppImage/Snap/Flatpak/MSIX
- **Veri dosyası yolu çözümleme:** `importlib.resources` vs `sys._MEIPASS` vs `__file__`-göreli vs `platformdirs` vs `sysconfig.get_path("purelib")`; OS konvansiyonları (XDG_DATA_DIRS, `%LOCALAPPDATA%`, `~/Library/Application Support`); "portable vs installed" ayrımı
- **Kod imzalama:** Windows Authenticode (EV/OV sert., SmartScreen itibarı) · macOS Developer ID + `notarytool` + Gatekeeper · Linux debsign/rpm/AppImage imza/Flatpak GPG
- **Güncelleme:** imzalı güncelleme manifesti deseni; CRA'nın açık yönetimi + destek süresi gereksinimlerinin auto-update etkisi

**Bu bölümü kapatmak için yapılacak:** `websearch` rate-limit'i düştüğünde dalı yeniden başlatın, **ya da** konuyu doğrudan `scripts/build_exe.py` ve `scripts/build_nuitka.py` okuyarak yerel bir analiz yapın (bu ikisi depoda mevcut ve okunabilir).

---

---

# BÖLÜM L — `src/` BAĞLAMA DENETİMİ (YEREL TARAMA, 2026-09-26)

> **Yöntem:** `wc -l`, AST tabanlı import grafiği, literal grep, doğrudan dosya okuması. Salt-okunur. Her iddia `file:line` kanıtlı.

## L.0 `src/` envanteri

**141 Python dosyası, 50.528 satır.**

| Paket | Satır | En büyük dosyalar |
|---|---:|---|
| `src/core/` | 1.152 | `exceptions.py` 321 · `contracts/ports.py` 318 · `models/can_frame.py` 277 |
| `src/hal/` | 2.163 | `rp1210/bus.py` 465 · `replay/safety_filter.py` 460 · `replay/parsers.py` 454 · `drivers/pcan_kvaser.py` 441 |
| `src/safety/` | 5.361 | `gateway.py` 1.828 · `secret_provider.py` 962 · `estop.py` 920 · `state_machine.py` 653 |
| `src/protocols/` | 8.449 | `j1939/transport.py` 1.671 · `obd/pids.py` 1.441 · `uds/isotp.py` 1.402 · `uds/flasher.py` 1.119 |
| `src/engine/` | **20.478** | ⭐ **`ai/diagnostic_copilot.py` 5.060** · `pipeline/reassembly_pipeline.py` 1.094 · `buffer/rolling_disk.py` 952 · `ai/dialogue_engine.py` 588 · `decoder/dbc_decoder.py` 584 |
| `src/security/` | 2.978 | `cloud/client.py` 694 · `license_flow.py` 639 · `license/validator.py` 483 · `hwid/collector.py` 333 · 🔴 **`cloud/updater.py` — 0 satır (BOŞ DOSYA)** |
| `src/ui/` | 4.611 | `desktop_app.py` 4.394 · `frontend_server.py` 212 |
| `src/launcher/` | 1.352 | `app.py` 536 · `updater.py` 421 · `auth.py` 207 |

## L.1 Yol çözümleme — `src/` içinde yalnız **4 desen** var

| Desen | Konum | Not |
|---|---|---|
| **(A)** frozen-first + repo fallback, `Path(__file__).resolve().parents[3]` | `diagnostic_copilot.py:1650-1655`, `anomaly_detector.py:54-59`, `hypothesis_engine.py:89-94`, `user_kb.py:49-54`, `golden_cases.py:34-39` | Depo kökü = `src/engine/ai/*.py`'den 4 seviye yukarı |
| **(B)** 🔴 **repo-only `parents[3]`, frozen dalı YOK** | `diagnostic_copilot.py:2052` (`_DBC_DATA_DIR`), `procedure_validator.py:195`, `:221` | ⭐ **L.4'ün kök nedeni** |
| **(C)** app-data-root göreli | `desktop_app.py:846`, `:908` | |
| **(D)** yalnız operatör argümanı | tüm `*_database(data_path=...)` | |

> 🔴 **`importlib.resources` hiçbir yerde kullanılmıyor.** `src/` içinde `subprocess` ile data okuyan yok.

## L.2 ⭐ `data/dbc` — 33 dosya katalog DIŞINDA (sha256 ile doğrulandı)

**Kod yolları yalnız 2 gerçek okuma:**
- `diagnostic_copilot.py:2052` `_DBC_DATA_DIR` · `:2061` `catalog.json` okuma
- `diagnostic_copilot.py:528` **`heavy_duty/j1939_canboat.dbc`** → `DbcSignalDecoder.from_dbc_file()` (tembel, `:520-533`)

> ⭐ **Ölçülen ve doğrulanan tutarsızlık:** `catalog.json` `total_files = 186` bildiriyor; **186'nın hepsi** diskte mevcut ve `size_bytes`+`sha256` eşleşiyor (186'nın tamamı yeniden hash'lendi). Ama **diskte 219 `.dbc` var** ⇒ **33 dosya katalog dışında, test edilmemiş ve envanter dışı.** Aralarında kodun **yorumlarda alıntıladığı** dosyalar var:
> `isobus_dd11783.dbc` · `isobus_vdma.dbc` · `j1939_canboat.dbc` (kök) · `j1939_ccvs1_extra.dbc` · `j1939_isobus_ag.dbc` · `j1939_isobus_vdma_all.dbc` · `n2k_canboat.dbc` · `marine/canboat_refs/canboat.dbc` + 12 `passenger/_*.dbc` parçası

> 🔴 **Kök seviyesi tekrarlar mevcut ve yalnız `heavy_duty/` kopyası kataloglanmış** — ama `src/protocols/j1939/oem/registry.py:21` ve `src/protocols/volvo/volvo_decoder.py:116` **KÖK** yolu alıntılıyor. Yorumlar kataloğun dışındaki bir yola işaret ediyor.

**Paketleme (COPY):** `build_nuitka.py:60,95-96` `--include-data-dir` · `installer.iss:55` `Source: "..\data\dbc\*"` · `build_installer.py:64,86-90` ön-uçuş sayımı
**Referans vermeyenler:** `build_exe.py` · `ucanlab.spec` · `ucanlab_launcher.spec` · `.gitlab-ci.yml` · `.github/workflows/ci.yml` · `pyproject.toml` · `conftest.py` · `main.py`

## L.3 🔴 `data/traces` — daha kesin bulgu

> 🔴 **`data/traces` içinde TEK BİR `.asc` ve TEK BİR `.blf` dosyası YOK.**
> `player.py:62-70` yalnız `.asc` / `.csv` / `.blf` kabul ediyor, gerisinde `Unsupported trace format` atıyor. **Yüklenebilir olan yalnız 4 `.csv` dosyası:**
> `sample3_GPSMAP820_…n2klog.csv` · `sample3_GPSMAP4008_…csv` · `project_haris/vessel.csv` · `project_haris/combined.csv`

**Uzantı sayımı vs parser yeteneği:**

| ext | adet | `player.py` yükleyebilir mi? |
|---|---:|---|
| `.raw` | 90 | ✗ |
| `.out` | 27 | ✗ |
| `.err` | 26 | ✗ |
| `.in` | 25 | ✗ |
| `.txt`/`.TXT` | 16 | ✗ |
| `.log` | 8 | ✗ 🔴 **ama allowlist'te** |
| `.ebl` | 7 | ✗ |
| `.md` | 5 | ✗ |
| **`.csv`** | **4** | ✅ |
| `.all` | 3 | ✗ |
| `.trc`/`.pcap`/`.json`/`.dle`/`.gitkeep`/`.data` | 6 | ✗ |

> ⭐⭐ **İKİNCİ, BAĞIMSIZ KUSUR:** `desktop_app.py:914-916` `REPLAY_EXTENSION_HINTS` listesi `.log`, `.json`, `.mf4`, `.mdf`, `.bin`, `.zst` içeriyor — ama `player.py:63-70` **yalnız** `.asc/.csv/.blf` kabul ediyor. **`data/traces`'taki 8 `.log` dosyası güvenlik allowlist'ini geçiyor, sonra parser'da başarısız oluyor.** 🔴 **Uzantı listesi parser'ın yetenek kümesinden türetilmemiş.** `parsers.py:1` docstring'i doğruluyor.

> 🔴 **Test kirliliği:** `test_t56d_replay_path_allowlist.py:36-41` deponun **kendi `data/traces`** dizinine `mkdir` yapıp `.asc` **yazıyor**; `:90-98` aynı dizine `.exe` (MZ baytları) **yazıyor**. `_app_data_root()` non-frozen modda depo kökünü döndürüyor (`desktop_app.py:185-186`).

> 🔴 **`.gitignore:93-99`** — `data/traces/*` **ignore edilmiş**, yalnız `marine/{canboat_samples,canboat_analyzer_tests}/**` geri dahil edilmiş. ⇒ diskteki 289 MB **izlenen ve yerel-izlenmeyen içeriğin karışımı.**

## L.4 ⭐ `data/knowledge` — sessiz kaybın KESİN mekanizması

**Desen (B) — `sys.frozen` dalı yok.** `procedure_validator.py:194-196` ve `:220-222`:
```python
target_dir = dir_path or (Path(__file__).resolve().parents[3] / "data" / "knowledge" / "dtc_procedures")
```
`:198-200` → `if not target_dir.is_dir(): return None` — 🔴 **fail-silent, log yok**
`:223` → `if not target_dir.is_dir(): return {}` — 🔴 **fail-silent, log yok**

> ⭐ **Kesin sonuç:** Frozen build'de `__file__` = `<_MEIPASS>/src/engine/ai/procedure_validator.py` ⇒ `parents[3]` = `_MEIPASS` ⇒ yol `<_MEIPASS>/data/knowledge/dtc_procedures`. 🔴 **Hiçbir build script bu dizini asla oluşturmuyor** (`build_exe.py`, `build_nuitka.py`, `installer.iss` — üçü de sıfır referans). **601 prosedür `return None`/`return {}` ile kayboluyor, tek bir log satırı olmadan.**

## L.5 ⭐ `data/diagnostics` — tam bağlı ama iki yeni kusur

**Bağlı olan (10 loader, hepsi desen A):** `dtc_database.json` (`:1705`) · `j1939_spn_fmi_database.json` (`:1783`) · `nhtsa_can_complaints_database.json` (`:1811`) · `uds_did_database.json` (`:1833`) · `obd_mode06_database.json` (`:1856`) · `extended_pid_database.json` (`:1968`) · `nhtsa_can_recalls_database.json` (`:2125`) · `telemetry_thresholds.json` · `root_cause_graph.json` · `user_kb.json`

> ⭐ **YENİ KUSUR 1 — `data/` dizinine TEK yazma:** `user_kb.py:115` `return _resolve_kb_path().parent / "user_feedback.json"` ⇒ **`data/diagnostics/user_feedback.json`** · `:152-153` `mkdir(parents=True)` + `write_text(...)` = **WRITE**. Dosya diskte yok (`.gitignore:85` ile ignore).
> **Sonuc:** salt-okunur kurulumda (`Program Files`) ya da frozen onefile'da UI köprüsünden kaydedilen operatör geri bildirimi **geçici `_MEIPASS` çıkarma dizinine** yazılır ve **çıkışta kaybolur.**

> ⭐ **YENİ KUSUR 2 — 6 CSV ikizinin `src/` okuyucusu YOK:** `obd_mode06_database.csv` · `uds_did_database.csv` · `extended_pid_database.csv` · `nhtsa_can_recalls_database.csv` · `j1939_spn_fmi_database.csv` · `dtc_database.csv`. **Tüm `src/engine/decoder`, `src/hal`, `src/protocols` ağacı hiçbir `.csv` açmıyor.** Yalnız `test_data_integrity.py:45-51` ve `scripts/rebuild_csv_exports.py:49` tarafından tüketiliyorlar.

**Referans vermeyen dosyalar:** `PROVENANCE.md` (yalnız docs) · `dbc_sync_report.json` (yalnız docs) · 🔴 `nhtsa_hd_trucks_STAGING.json` — **`scripts/fetch_nhtsa_hd_trucks.py:6,25` yazıyor, hiçbir şey okumuyor**

**Script tarafı — 12 merge/expand script'i JSON'a YAZIYOR:** `expand_j1939_spn_database.py:21-23` · `merge_t45_dtc.py:52` · `merge_t44_recovery.py:52` · `t63_merge.py:23-24` · `t63_adapter.py:26` · `t53_j1939_text_cleanup.py:22` · `fix_f05_scraped_artifacts.py:24` · `t_df0cf373_merge.py:22-23` · `_fix_merge.py:11` · `_restore_en.py:10` (🔴 `subprocess` ile `git show HEAD:data/…`) · `rebuild_csv_exports.py:49` · `merge_harvested_diagnostics.py:12-13`

## L.6 `data/golden_traces` — bağlı, `sys._MEIPASS` dalı **VAR**

`golden_cases.py:34-39` → `if sys.frozen: _MEIPASS/"data"/"golden_traces"/"cases"`; `is_dir()` değilse `parents[3]/…` fallback'i. ⭐ **Bu, `data/diagnostics` ile `data/knowledge`'nin aksine doğru deseni kullanıyor** — yani düzeltme için hazır bir örnek depoda mevcut.

## L.7 Test kapsamı

| data dizini | Test edilen invariant |
|---|---|
| `data/dbc` | `test_curated_dbc_pack.py:24-38` manifest+catalog varlığı, `total_files >= 70`, `total_messages >= 10000`, `total_signals >= 40000`, 5 kategori; `:40-60` EEC1 → `Engine_RPM == 1600.0` · `test_data_integrity.py:127-137` **katalogdaki her girdi yeniden hash'leniyor** |
| `data/diagnostics` | `test_data_integrity.py:37-51` JSON↔CSV satır eşitliği (5 çift) · `:90-118` metadata toplamları · `test_harvest_validator.py:65` · `test_ai_fabrication_fixes.py:217` · `test_ai_roadmap_faz2.py:114-118` canlı UI yolu |
| `data/knowledge` | `test_data_integrity.py:157-169` varlık, `schema_version==1`, `symptoms`+`measurement_steps`, dosya↔`dtc` tutarlılığı, **öksüzlük denetimi** · `test_ai_roadmap_faz2.py:50-55` `>= 3` |
| `data/golden_traces` | `test_golden_cases.py` + `test_data_integrity.py:143-154` şema + taslak dışlama |
| `data/traces` | 🔴 **yalnız izin listesi testi** — **korpusun kendisi test edilmiyor** |

🔴 **`data/traces` için 289 MB'lık gerçek korpusun hiçbir testi yok.**

## L.8 Boş dosya

🔴 **`src/security/cloud/updater.py` — 0 satır.** Kardeşi `src/launcher/updater.py` 421 satır.

---

## SONUÇ

Turun kapsamı: **yerel envanter (1.123 dosya) + 20+ alt araştırma dalı.** Çıktı:

- **11 P0 bulgu** (lisans/sevk edilemez, sessiz veri kaybı, canlı mevzuat yükümlülüğü)
- **9 yanlış varsayım** kaynaklarla çürütüldü
- **5 tahmin edilememesi gereken madde** işaretlendi
- **19 ücretsiz kazanç** kalemi
- **40 maddelik eylem planı** (P0/P1/P2)
- **17 doğrulanamayan kalem** ile yatırım öncesi kapatılacak liste

En değerli üç çıktı:
1. **`TxSafetyGateway` için standartlaştırılmış bir yol bulundu** — UDS `0x29 Authentication` + `0x84 SecuredDataTransmission` (ISO 14229-1:2020), 23 NRC hazır taksonomi
2. **Denetim izi zinciri 2007'den beri bir standarda oturuyor** — RFC 4998 Evidence Record Syntax
3. **Aksanti olmayan konumlandırma kanıtlandı** — GitHub'da bu ürünün yaptığı şeyi yapan tek bir açık kaynak proje yok, ve bu sayı **yöntemiyle birlikte** belgelenebilir

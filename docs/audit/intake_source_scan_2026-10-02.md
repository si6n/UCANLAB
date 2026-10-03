# Upstream kaynak taraması — intake keşfi (2026-10-02)

Araç: `python scripts/intake_scan_sources.py --report …` (ağ yalnız burada).
Dal: `ccr-intake`. Bu rapor **keşif kanıtıdır**; `data/diagnostics` ve
`data/golden_traces` dosyalarına yazılmamıştır.

## 1. Özet (ölçülen)

| Ölçüm | Değer |
|---|---|
| Taranan upstream artefakt | **134** (OBDex `data/` + canboat `database/j1939/pgns/` + `docs/canboat.json` + Wal33D `data/source-data/`) |
| Vendor edilmiş ve **hash birebir doğrulanmış** | **13** |
| Hash uyuşmazlığı | **0** |
| Vendor edilmemiş (yeni bulunan) | **84** (64.461 bayt) |
| Intake'e sahaya alınan `pgn_layout` kaydı | **84** |
| Intake'e sahaya alınan `oem_divergence` kaydı | **37** (5.922 ayrışma satırı) |
| Intake'e sahaya alınan `spn_reference` kaydı | **168** (66 terfi adayı + 102 uzlaştırma) |
| Intake'e sahaya alınan `kb_defect` kaydı | **8** (kendi verimizden ölçülmüş kusur) |
| Kopyalanan PGN alanı | **378** |
| Kopyalanan SPN referansı | **171** (168 ayrı SPN) |
| KB'de **olmayan** SPN referansı | **66** |

## 2. Kaynak doğrulaması

| Repo | Commit | Lisans | Sonuç |
|---|---|---|---|
| `foerbsnavi/OBDex` | `bc58b0eb7273226a1aabae98e956b70b8362bda1` | CC0-1.0 | 9/9 dosya zaten vendor; **9/9 sha256 birebir** |
| `canboat/canboat` | `f7f088b49d58f5b4a0feb9b29c288b0ae18a7880` | Apache-2.0 (NOTICE zorunlu) | `docs/canboat.json` (2,4 MB) ve DM1 YAML'ı birebir doğrulandı; **84 PGN düzeni yeni** |
| `Wal33D/dtc-database` | `04c43d72e7db7197658b6f72fe582c5076d9eee8` | MIT (atıf zorunlu) | `data/dtc_codes.db` birebir doğrulandı; **37 per-manufacturer kaynak listesi yeni** |
| `STAS63-bit/sitrak-error-codes` | `fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742` | CC-BY-4.0 (atıf zorunlu) | `error-codes.json` birebir doğrulandı; **artık artefakt yok** (taranan 4 kaynak tamamen) |

İndirilen tarball, `data/PROVENANCE.md` §2'deki kanıt satırlarıyla **birebir**
örtüşüyor: 11/11 artefaktta hash eşleşti, sapma yok. Yani "upstream değişti"
spekülasyonu bu ölçümle dışlandı.

## 3. Keşif: canboat J1939 PGN düzenleri repoda yok

`data/diagnostics/canboat_pgn_reference.json` kendi notunda bunu zaten söylüyor:
*"canboat.json J1939 ICERMEZ (T1-3 dogrulamasi: PGN 65226 yok, SPN/FMI alani
olan PGN 0, J1939 tipli PGN 0). J1939 DM1 ayri artefakttir."*

Sonuç: repoda J1939 tarafı **yalnız DM1 (PGN 65226)** ile temsil ediliyor; 84
PGN'nin alan düzeni, `bits`/`unit`/`resolution` değerleri ve SPN referansları
hiçbir yerde yok. Bunlar `data/intake/pgn/` altında `pgn_layout` kaydı olarak
sahaya alındı — **terfi edilmedi**.

## 4. İkincil keşif: 66 SPN bilgi tabanında eksik

84 kayıttaki 168 ayrı SPN referansının **66** tanesi
`data/diagnostics/j1939_spn_fmi_database.json` içinde **yok**:

| SPN | Staged kaynak (PGN) |
|---|---|
| SPN 126 | PGN 65272 |
| SPN 163 | PGN 61445 |
| SPN 526 | PGN 61445 |
| SPN 1032 | PGN 65201 |
| SPN 1128 | PGN 65190 |
| SPN 1129 | PGN 65190 |
| SPN 1130 | PGN 65190 |
| SPN 1132 | PGN 65189 |
| SPN 1133 | PGN 65189 |
| SPN 1802 | PGN 65189 |
| SPN 1803 | PGN 65189 |
| SPN 2433 | PGN 65031 |
| SPN 2434 | PGN 65031 |
| SPN 2807 | PGN 64914 |
| SPN 2809 | PGN 64976 |
| SPN 2810 | PGN 64976 |
| SPN 2811 | PGN 64976 |
| SPN 2896 | PGN 61443 |
| SPN 2970 | PGN 61443 |
| SPN 2979 | PGN 61443 |
| SPN 3027 | PGN 65272 |
| SPN 3028 | PGN 65272 |
| SPN 3243 | PGN 64948 |
| SPN 3244 | PGN 64948 |
| SPN 3247 | PGN 64947 |
| SPN 3254 | PGN 64946 |
| SPN 3543 | PGN 64914 |
| SPN 3544 | PGN 64914 |
| SPN 3548 | PGN 65130 |
| SPN 3549 | PGN 65130 |
| SPN 3550 | PGN 65130 |
| SPN 3551 | PGN 65130 |
| SPN 3552 | PGN 65130 |
| SPN 3553 | PGN 65130 |
| SPN 3554 | PGN 65130 |
| SPN 3562 | PGN 64976 |
| SPN 3589 | PGN 64914 |
| SPN 3601 | PGN 64914 |
| SPN 3602 | PGN 64914 |
| SPN 3604 | PGN 64914 |
| … | +26 SPN daha |

Örnekler: SPN 1032/1033 (ECU Distance / Run Time, PGN 65201), SPN 2896/2970/2979
(ECU2, PGN 61443). Copilot bu parametreleri "tanıyorma" iddiasında bulunmuyor —
doğrulayıcı her biri için `WARN pgn_spn_unknown` üretir. Bu, terfi kuyruğunun
ilk gerçek iş kalemidir; terfi kararı intake README'sindeki Kapı 1-2'ye bağlı.

## 4b. Keşif: OEM katmanı üreticiye özgü açıklamayı kaybetmiş

Wal33D/dtc-database deposunda vendor edilmemiş **37 per-manufacturer kaynak
listesi** var (`data/source-data/*.txt`, 18.825 satır). Repo yalnız derlenmiş
`data/dtc_codes.db` dosyasını (3.256.320 bayt, sha256 `099a4ffd…`) aldı; bu liste
`build_database.py` ile derlenirken **açıklama kod başına tek satıra düşüyor**
("last write wins").

Ölçüm (gerçek satır karşılaştırması):

| Ölçüm | Değer |
|---|---|
| Kaynak satır (37 dosya) | **18825** |
| Kod sayısı | 12.128 — **OEM katmanında eksik kod yok** (0/18.825) |
| **Açıklaması katmandaki metinden farklı satır** | **5922** |
| Etkilenen ayrı kod | **877** |
| Etkilenen üretici listesi | **37** |

Yani katmanın *kapsamı* eksik değil; **anlam kaybı** var. Örnek (FORD listesi):

| Kod | Upstream (FORD) | Katmanda saklanan |
|---|---|---|
| `P1100` | Mass Air Flow Sensor Intermittent | BARO Sensor Circuit |
| `P1101` | Mass Air Flow Sensor Out of Self-Test Range | Oxygen Sensor Circuit Bank 1 Sensor 1 Voltage Too Low/Air Leak |

Copilot bugün `P1100` için "BARO Sensor Circuit" cevabı verebilir; Acura/Honda
tarafında aynı kod "BARO Circuit Range Performance Malfunction" anlamına gelir.
Bu bir **veri kalitesi kusuru**, tespiti intake kuyruğuna `oem_divergence`
kayıtları olarak girdi (37 kayıt, ~557 KiB satır kanıtı). Doğrulayıcı her
çalıştırmada satırları yeniden ölçer: `oem_divergence_rows=5922`,
`oem_divergence_open=5922` → **hiçbiri kapanmamış**, yani kusur kendiliğinden
çözülmüyor ve terfi kararı gerekiyor (README Adım 3b).

## 4c. Keşif çıktısının terfiye hazır hâle getirilmesi: SPN referansları

Bölüm 4'te bulunan 66 eksik SPN için intake kuyruğu artık **terfiye hazır kanıt**
taşıyor: `data/intake/spn_ref/` altında her SPN için bir kayıt var (toplam **168**
kayıt; 66'sı `kb_state: absent`, 102'si `present`).

| Alan | İçerik |
|---|---|
| `spn`, `names_en[]` | upstream metninden birebir parametre adı |
| `units[]`, `resolutions[]`, `bit_lengths[]` | yalnız upstream verdiyse |
| `evidence_pgns[]`, `evidence_text[]` | SPN'nin **hangi PGN'de, hangi metinde** geçtiği |
| `sources[]` | her kanıt dosyasının `sha256` + bayt boyutu |
| `kb_state`, `kb_name`, `kb_unit` | bugünkü KB durumu (ölçülmüş) |

Sayı **türetilmez**: yalnız upstream metninde yazan `SPN n` ifadesi sayılır
(`collect_spn_evidence()`), test bunu ayrı bir fixture ile doğrular.

Ölçülen uzlaştırma (kapı her çalıştırmada tekrarlar):

| Ölçüm | Değer |
|---|---|
| KB'de **olmayan** SPN (terfi adayı) | **66** |
| KB'de **olan** SPN | 102 |
| Ad farkı (kısa alan adı ↔ uzun J1939 adı — kusur **değil**, sayılır) | 45 |
| Birim çelişkisi (upstream birim ≠ KB birimi) | 1 (SPN 1127: canboat `kPa`, KB `Standart J1939`) |
| KB adı bir tanım cümlesi olan SPN | 23 (`kb_name_defect` INFO) |

İlk 25 terfi adayı:

| SPN | Upstream parametre adı | Kanıt PGN |
|---|---|---|
| 126 | Transmission Filter Differential Pressure | PGN 65272 |
| 163 | Transmission Current Range | PGN 61445 |
| 526 | Transmission Actual Gear Ratio | PGN 61445 |
| 1032 | Total ECU Distance | PGN 65201 |
| 1128 | Engine Turbocharger 2 Boost Pressure | PGN 65190 |
| 1129 | Engine Turbocharger 3 Boost Pressure | PGN 65190 |
| 1130 | Engine Turbocharger 4 Boost Pressure | PGN 65190 |
| 1132 | Engine Intake Manifold 3 Temperature | PGN 65189 |
| 1133 | Engine Intake Manifold 4 Temperature | PGN 65189 |
| 1802 | Engine Intake Manifold 5 Temperature | PGN 65189 |
| 1803 | Engine Intake Manifold 6 Temperature | PGN 65189 |
| 2433 | Engine Exhaust Manifold Bank 2 Temperature 1 | PGN 65031 |
| 2434 | Engine Exhaust Manifold Bank 1 Temperature 1 | PGN 65031 |
| 2807 | Engine Fuel Shutoff 2 Control | PGN 64914 |
| 2809 | Engine Air Filter 2 Differential Pressure | PGN 64976 |
| 2810 | Engine Air Filter 3 Differential Pressure | PGN 64976 |
| 2811 | Engine Air Filter 4 Differential Pressure | PGN 64976 |
| 2896 | Momentary Engine Maximum Power Enable | PGN 61443 |
| 2970 | Accelerator Pedal 2 Low Idle Switch | PGN 61443 |
| 2979 | Vehicle Acceleration Rate Limit Status | PGN 61443 |
| 3027 | Transmission Oil Level 1 High / Low | PGN 65272 |
| 3028 | Transmission Oil Level 1 Countdown Timer | PGN 65272 |
| 3243 | Aftertreatment 1 Intake Gas Temperature 2 Preliminary FMI | PGN 64948 |
| 3244 | Aftertreatment 1 Intake Gas Pressure 2 Preliminary FMI | PGN 64948 |
| 3247 | Aftertreatment 1 Outlet Gas Temperature 2 Preliminary FMI | PGN 64947 |

Doğrulayıcı her çalıştırmada `kb_state`'i yeniden ölçer; KB'de bir SPN eklendiyse
`WARN kb_drift` verir ve kayıt gözden geçirilir — yani bu liste **kendini
günceller**.

## 4d. Fonksiyonel boşluk: J1939 sinyal adlarının 155'u çözümlenemiyor

Yukarıdaki bulguların pratik karşılığı şu: kullanıcı gerçek bir J1939 sinyalinin
adını sorduğunda copilot bunu kanonik sinyale **çözülemiyor**. Ölçüm:

| Ölçüm | Değer |
|---|---|
| canboat kanıtındaki J1939 parametre adı | 168 |
| `signal_aliases.json` + `signal_measurement_map.json` sözlüğü | 136 form |
| **Çözümlenemeyen ad** | **155** |

Örnekler: `Engine Intercooler Temp` (SPN 52), `Intake Manifold Temp` (SPN 105),
`Transmission Oil Level 1` (SPN 124), `Engine Intake Air Mass Flow Rate`
(SPN 132), `Accelerator Pedal Position 1` (SPN 91) — hepsi gerçek ECU
mesajlarında geçiyor, hiçbiri sözlükte yok.

Bu bir **kusur değil, kapsam boşluğudur**, ama işlevsel sonucu gerçek: sorgu
kanala düşüyor. Çözüm intake'ten değil, `signal_aliases.json`
(`source_forms`) + `signal_measurement_map.json` (`aliases`) genişletmesinden
gelir; terfi kanıtı `data/intake/spn_ref/` altında hazır.

Bu ölçüm `scripts/intake_kb_defects.py` içinde bir dedektör
(`spn_parameter_name_not_in_alias_map`) olarak yaşar: yani kavram ileride
sözlük genişletildikçe **kendiliğinden kapanır** ve kapıdan geçen bir sayıya
dönüşür.

## 4e. En ciddi bulgu: 1.220 SPN kaydının kaynağı lisansa bağlanamıyor

Bu turda upstream yerine **kendi teslim ettiğimiz verinin** kanıt zinciri
denetlendi ve politika ihlali ölçüldü. `data/PROVENANCE.md` §1 her kaynak için
çözülebilir bir lisans ister ("belirsizse reddet") ve §5 ticari/forum kazımasını
reddeder. `data/diagnostics/j1939_spn_fmi_database.json` bu standardın dışında:

| Ölçüm | Değer |
|---|---|
| `metadata.sources` içindeki kaynak | 19 |
| **Kendi lisansını yazan kaynak** | **2** (canboat Apache-2.0, SITRAK CC BY 4.0) |
| Kamu/standart kaynağı (NHTSA ×2, ISO 11783 ölçek tabloları) | 3 |
| **Lisansı belirtilmemiş ticari-manual sitesinden toplama** | **11** |
| `attribution` bloğunda adı geçen kaynak | **1 / 19** |

Kayda göre en çok geçen kaynak alanları: `sitrak_ccby4` (2.845 — lisanslı ve
atıflı), `tur23_j1939hub` (224), `tier_b` (117), `tier_c_ss_verified` (98),
`detroitdieselengines.info` (72), `dtcdocs.com` (39), `t66_procarmanuals` (29),
`wholefleet.ca` (9).

Kayıt düzeyinde ölçüm (**1220 / 4.291 kayıt**):

| Sınıf | Adet | Örnek |
|---|---|---|
| `no_source` — hiç `source` alanı yok, metnin nereden geldiği belirsiz | 855 | `SPN_190` (Engine Speed), `SPN_1033` (Total ECU Run Time), `SPN_10294` |
| `unlicensed_src` — kaynak adı var, lisans alanı yok, beyan edilmiş lisanslı kaynak da değil | 365 | `tier_b`, `tier_c_ss_verified`, `t66_procarmanuals` |

Bu **hukuki hüküm değildir**; kanıtlanmış bir *atıf/lisans çözülebilirliği*
eksikliğidir ve karar veri sahibinindir (atıf eklemek, kaynakları temizlemek veya
ilgili SPN'leri çıkarmak). Ama bugün itiraz gelirse cevap üretilebilecek bir kanıt
zinciri **yok**.

Ölçüm `j1939_source_without_licence` dedektörü olarak yaşıyor ve
`data/intake/defects/` altında `severity: high` ile kayıtlı: kaynak temizlenirse
sayaç düşer, kayıt kapanır; yeni lisanssız kaynak eklirse sayaç artar.
Aynı dosyanın diğer kusurları (`spn_name_is_fmi_sentence`,
`spn_unit_placeholder`) da aynı dosyada ölçülüyor — yani tek bir veri dosyası
üç ayrı, birbirinden bağımsız kanıt zinciri sorunu taşıyor.

## 4f. İzlenebilirlik: 8.918 kaynak alanı hiçbir provenance belgesinde yok

4e'de *lisans* eksikliği ölçüldü. Bu turda daha zayıf ama daha ucuz düzeltilebilir
sınıf ölçüldü: **verinin işaret ettiği ama repoda hiç yazılmamış kaynaklar**.

Yöntem: `data/diagnostics/**.json` içindeki kaynak taşıyan alanlar
(`source`, `_source_ref`, `evidence_url`, `url`, `*_source`) tarandı; her değerin
anlamlı token'ı `data/PROVENANCE.md` **ve** `data/diagnostics/PROVENANCE.md`
korpusunda arandı. Eşleşme yoksa "belgelenmemiş" sayıldı.
Doğrulayıcı kalibrasyonu: OBDex/canboat/SITRAK/Wal33D/troublecodes.net/
j1939hub/GM/ISO değerleri **belgelenmiş** olarak sınıflanıyor (yanlış-pozitif
kontrolü testte sabit).

| Dosya | Belgelenmemiş kaynak alanı | En sık değerler |
|---|---|---|
| `dtc_database.json` | **8.656** | `obd2.com` (3.960), `openlaborproject.com` (1.202), `autofaultcodes.com` (621), `geekobd.com`, `obd2hub.com`, `theerrorcodes.com` |
| `j1939_spn_fmi_database.json` | **247** | `detroitdieselengines.info` (72), `T66 procarmanuals.com` (61), `dtcdocs.com` (39) |
| `extended_pid_database.json` | **15** | `ForScan community`, `ISTA/BimmerLink community`, `VCDS/OBD11 community` |

Toplam **8918**.

Neden önemli: copilotun **ana** bilgi tabanı (`dtc_database.json`, 14.484 kod)
kaynak alanlarının büyük kısmını, repo'da hiç geçmeyen ticari DTC sitelerine
işaret ediyor. Bu gizli bir veri değil — `data/diagnostics/PROVENANCE.md` T45/T54
hasat günlüğünü açıkça tutuyor — ama **bu alanlar için** zincir yürütülemez.
En ucuz düzeltme: ya belgele (satır düzeyinde kaynak + lisans) ya da alanı kaldır.

Ölçüm `kb_source_value_not_in_provenance_doc` dedektörü olarak yaşıyor
(`severity: high`): kaynaklar belgelendikçe sayaç düşer ve kayıt kapanır.

### Hangi üreticinin metni hayatta kaldı? (ölçüm)

Katmandaki metnin **her kod için** tam olarak bir kaynak satırına eşit olduğu
doğrulandı (12.128/12.128; hiçbir metin uydurulmamış, hiçbiri kaybolmamış).
Ama upstream'da **877 kodun birden çok farklı ifadesi** var ve hayatta kalan
metin şu dağılımla tek bir tanesine bağlıyor:

| Hayatta kalan liste | Kod sayısı |
|---|---|
| `p_codes.txt` (genel) | 7.355 |
| `other_codes.txt` (genel) | 1.990 |
| `u_codes.txt` (genel) | 1.228 |
| `volkswagen_codes.txt` | 528 |
| `c_codes.txt` (genel) | 497 |
| `b_codes.txt` (genel) | 300 |
| diğer (FORD 42, DODGE 31, SUBARU 28 …) | kalanı |

Somut örnek — `P1101` upstream'ta **üç** farklı tanım taşıyor:

| Liste | `P1101` metni |
|---|---|
| `other_codes.txt` | MAF Sensor Out Of Self Test Range./KOER Not Able To Complete KOER Aborted |
| `ford_codes.txt` | Mass Air Flow Sensor Out of Self-Test Range |
| `volkswagen_codes.txt` | Oxygen Sensor Circuit Bank 1 Sensor 1 Voltage Too Low/Air Leak |
| **katmanda saklanan** | **Oxygen Sensor Circuit Bank 1 Sensor 1 Voltage Too Low/Air Leak** (VW) |

Yani bir Ford/Acura aracında copilot **Volkswagen tanımını** servis edebilir.
Bu bir stil tercihi değil, ölçülmüş bir veri kaybıdır.

> Düzeltme intake'ten yapılmaz: `data/diagnostics/` dosyalarına bu turda
> **hiçbir yazma yapılmadı** (test bunu byte seviyesinde doğrular).

## 5. Beklenen/dürüst sınırlar

- **Hiçbir şey uydurulmadı.** Alan adları, `description`, `bits`, `unit`,
  `resolution` metinleri upstream'ten birebir kopyalandı. Upstream'da olmayan
  alan `null`/`[]` bırakıldı; okunmayan üst düzey anahtarlar
  `payload.upstream_keys` içinde **kayıt altında** tutuldu (sessizce atılmadı).
- **SPN türetilmedi.** SPN yalnız upstream `name`/`description` metninde
  yazıyorsa alınır; doğrulayıcı bunu ayrıca denetler (`WARN pgn_spn_source`).
- **Bulk `bits` yerinde tutuldu.** 4 fast-packet kaydında upstream
  "reverse engineered değil" anlamına gelen 1768-1784 bitlik `Data` alanı
  vardır; bu değerler *düzeltilmedi*, `WARN pgn_bits_bulk` ile işaretlendi.
- **Yalnız lisansı belli olan kaynak tarandı.** `data/PROVENANCE.md` §5'teki
  reddedilmiş kaynaklar (SAE/ISO metinleri, AllData, forumlar) taramaya
  **hiç girmedi**.
- **DM1 kaydı sahaya alınmadı** (zaten vendor): aynı şeyi kopyalamak keşif
  değildir.

## 6. Yeniden üretme

```bash
python scripts/intake_scan_sources.py --report docs/audit/intake_source_scan_2026-10-02.md
python scripts/intake_scan_sources.py --stage            # doğrula (yazmaz)
python scripts/intake_scan_sources.py --stage --apply    # intake'e yaz
python scripts/intake_scan_sources.py --stage-oem --apply
python scripts/intake_scan_sources.py --stage-spn --apply
python scripts/intake_kb_defects.py --stage --apply
python scripts/validate_intake.py --sync-manifest --apply
python scripts/validate_intake.py --sync-manifest --apply
python scripts/validate_intake.py                       # FAIL=0 beklenir
python -m pytest tests/unit/test_validate_intake.py tests/unit/test_intake_scan_sources.py \
                   tests/unit/test_intake_kb_defects.py -q
```

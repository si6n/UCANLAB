# Upstream kaynak taraması — intake keşfi (2026-10-02)

Araç: `python scripts/intake_scan_sources.py --report …` (ağ yalnız burada).
Dal: `ccr-intake`. Bu rapor **keşif kanıtıdır**; `data/diagnostics` ve
`data/golden_traces` dosyalarına yazılmamıştır.

## 1. Özet (ölçülen)

| Ölçüm | Değer |
|---|---|
| Taranan upstream artefakt | **133** (OBDex `data/` + canboat `database/j1939/pgns/` + `docs/canboat.json` + Wal33D `data/source-data/`) |
| Vendor edilmiş ve **hash birebir doğrulanmış** | **12** |
| Hash uyuşmazlığı | **0** |
| Vendor edilmemiş (yeni bulunan) | **84** (64.461 bayt) |
| Intake'e sahaya alınan `pgn_layout` kaydı | **84** |
| Intake'e sahaya alınan `oem_divergence` kaydı | **37** (5.922 ayrışma satırı) |
| Kopyalanan PGN alanı | **378** |
| Kopyalanan SPN referansı | **171** (168 ayrı SPN) |
| KB'de **olmayan** SPN referansı | **66** |

## 2. Kaynak doğrulaması

| Repo | Commit | Lisans | Sonuç |
|---|---|---|---|
| `foerbsnavi/OBDex` | `bc58b0eb7273226a1aabae98e956b70b8362bda1` | CC0-1.0 | 9/9 dosya zaten vendor; **9/9 sha256 birebir** |
| `canboat/canboat` | `f7f088b49d58f5b4a0feb9b29c288b0ae18a7880` | Apache-2.0 (NOTICE zorunlu) | `docs/canboat.json` (2,4 MB) ve DM1 YAML'ı birebir doğrulandı; **84 PGN düzeni yeni** |
| `Wal33D/dtc-database` | `04c43d72e7db7197658b6f72fe582c5076d9eee8` | MIT (atıf zorunlu) | `data/dtc_codes.db` birebir doğrulandı; **37 per-manufacturer kaynak listesi yeni** |

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
python scripts/validate_intake.py --sync-manifest --apply
python scripts/validate_intake.py                       # FAIL=0 beklenir
python -m pytest tests/unit/test_validate_intake.py tests/unit/test_intake_scan_sources.py -q
```

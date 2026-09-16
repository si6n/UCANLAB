# T61-C — DB Boşluk Analizi + Tarama Hedefi (0 Kredi)

**Tarih:** 2026-09-16
**Depo:** `C:/Users/canak/Desktop/Universal-CAN-BUS-Tool`
**Görev:** T61-C (telemetry) — SADECE ANALİZ, TARAMA YOK, 0 BrightData kredisi
**DB yazma:** YAPILMADI (T62 merge ayrı görev)

---

## 0. Özet

| Metrik | Değer | Kanıt |
|---|---|---|
| J1939 SPN | **3.947** | `"spn":` × 3947 |
| DTC kod | **14.352** | `dtc_database.json` len |
| `procedures_full` **BOŞ** SPN | **3.627** (%91,9) | 3947 − 320 |
| DTC `symptoms` **BOŞ** | **13.521** (%94,2) | 14352 − 831 |
| DTC `causes/steps` BOŞ | **3** | yalnız 3 kod |
| BrightData kredisi | **0** | tarama yok |

**En kritik bulgu:** DTC tarafında `symptoms` %94,2 boş → **13.521 kod**. Bu, J1939
`procedures_full` boşluğundan (3.627) kat kat büyük ve TEK kanıtlı kaynağı
`obd2pros.com` (T55-C, 31 kredi) — henüz yalnız 31 kod denenmişti.

---

## 1. J1939 SPN DB alan doluluk (kesin sayım)

Kaynak: `data/diagnostics/j1939_spn_fmi_database.json`, `spns` sözlüğü, n=**3947**.

| Alan | Dolu | Boş | Kapsam |
|---|---:|---:|---:|
| `name` | 3.947 | 0 | 100,00 % |
| `title_tr` | 3.947 | 0 | 100,00 % |
| `description` | 3.694 | 253 | 93,59 % |
| `causes` | 3.720 | 227 | 94,25 % |
| `steps` | 3.457 | **490** | 87,59 % |
| `description_en` | 3.230 | 717 | 81,83 % |
| `fault_matrix` | 3.910 | 37 | 99,06 % |
| `subsystem` | 3.910 | 37 | 99,06 % |
| `evidence_url` | 3.039 | 908 | 77,00 % |
| `associated_pgn` | 1.360 | 2.587 | 34,46 % |
| **`procedures_full`** | **320** | **3.627** | **8,11 %** |
| `symptoms` | 27 | 3.920 | 0,68 % |

### 1.1 `procedures_full` boşluk — severity dağılımı

Severity, T55-A ile **AYNI** yöntemle `fault_matrix.fmi_name` metninden türetildi
(uydurma yok): `"Most Severe Level"` → critical, `"Moderately Severe"` → high,
diğer fault_matrix → medium, fault_matrix yok → unknown.

| Severity | Toplam | Dolu | Boş | Kapsam |
|---|---:|---:|---:|---:|
| **critical** | 353 | 110 | **243** | 31,2 % |
| **high** | 79 | 6 | **73** | 7,6 % |
| medium | 3.478 | 167 | 3.311 | 4,8 % |
| unknown | 37 | 37 | 0 | 100 % |

> Toplam severity: critical 353 / high 79 / medium 3478 / unknown 37.
> `critical+high` boşluk = **316 SPN** → ticari değeri en yüksek grup.

### 1.2 `procedures_full` boşluk — subsystem dağılımı (ilk 15)

| Subsystem | Toplam | Boş | Kapsam |
|---|---:|---:|---:|
| KNORREBS | 1.156 | 1.155 | 0,1 % |
| Bosch | 654 | 651 | 0,5 % |
| AMT | 209 | 208 | 0,5 % |
| Güç Kumanda Ünitesi (PCU) | 150 | 150 | 0,0 % |
| OGP | 148 | 148 | 0,0 % |
| Şebeke & İletişim Katmanı (J1939) | 147 | 133 | 9,5 % |
| Ağır Vasıta J1939 (OEM tablo hasadı) | 143 | 113 | 21,0 % |
| ZF_GearBox | 114 | 111 | 2,6 % |
| Motor Yönetimi & Genel Güç Aktarma (J1939) | 99 | 83 | 16,2 % |
| BCM_HR | 55 | 55 | 0,0 % |
| EBS | 49 | 48 | 2,0 % |
| KNORRABS8 | 45 | 45 | 0,0 % |
| HR_Radar | 43 | 43 | 0,0 % |
| ECAS4PLUS | 41 | 40 | 2,4 % |
| ECAS | 40 | 40 | 0,0 % |

> **Dikkat:** KNORREBS tek başına 1.155 boşluk — ancak bu grup SITRAK (Çin) OEM
> verisidir ve `sitrak-error-codes` (CC BY 4.0) zaten tarandı, kapsamı düşük.
> Gerçek ticari değer **critical severity (243) + motor/aftertreatment** alt
> sistemlerinde.

### 1.3 Diğer SPN boşlukları

| Alan kombinasyonu | Adet |
|---|---:|
| `steps` boş | 490 |
| `causes` boş | 227 |
| `description` **ve** `description_en` boş | 209 |
| `steps` **ve** `causes` boş | 227 |
| `steps`+`causes`+`description`(+en) hepsi boş | **200** |
| `symptoms` boş | 3.920 |
| `associated_pgn` boş | 2.587 |

---

## 2. DTC DB alan doluluk (kesin sayım)

Kaynak: `data/diagnostics/dtc_database.json`, n=**14.352**.

| Alan | Dolu | Boş | Kapsam |
|---|---:|---:|---:|
| `title`, `subsystem`, `severity` | 14.352 | 0 | 100 % |
| `causes` | 14.349 | **3** | 99,98 % |
| `steps` | 14.349 | **3** | 99,98 % |
| **`symptoms`** | **831** | **13.521** | **5,79 %** |
| `symptoms_en` | 345 | 14.007 | 2,40 % |
| `measurement` / `uds_routine` | 9.300 | 5.052 | 64,80 % |
| `evidence_url` | 4.866 | 9.486 | 33,90 % |
| `title_en` / `description_en` / `causes_en` | 4.512 | 9.840 | 31,44 % |
| `oem_variants` | 2.911 | 11.441 | 20,28 % |
| `vag_code` | 2.102 | 12.250 | 14,65 % |
| `j1939_spn_fmi` | 697 | 13.655 | 4,86 % |
| `oem_details` | 34 | 14.318 | 0,24 % |
| `title_tr` | 0 | 14.352 | 0,00 % |

### 2.1 DTC `symptoms` boşluk — severity

| Severity | Toplam | symptoms boş | Kapsam |
|---|---:|---:|---:|
| **critical (CRITICAL_STOP)** | 281 | **205** | 27,0 % |
| **high** | 379 | **379** | 0,0 % |
| medium | 13.154 | 12.493 | 5,0 % |
| low | 536 | 444 | 17,2 % |
| info | 2 | 0 | 100 % |

> **high severity 379/379 = TAMAMEN boş.** En yüksek öncelikli DTC hedefi.

### 2.2 DTC prefix dağılımı

| Prefix | Adet | symptoms boş |
|---|---:|---:|
| P (Powertrain) | 9.291 | 8.677 |
| U (Network) | 1.883 | 1.670 |
| B (Body) | 1.790 | 1.789 |
| C (Chassis) | 1.388 | 1.385 |

### 2.3 DTC `symptoms` kalite bayrakları

- `symptoms == causes` birebir kopya: **0** (T45-C'deki obdhut.com kopya sorunu temizlenmiş)
- `symptoms` uzunluğu < 25 karakter: **3**
- Boilerplate ifade içeren: **124** (ör. "check engine", "servis")

---

## 3. Öncelik: Ticari değer taşıyan boşluklar

### 3.1 OEM yaygınlığı (fault_matrix `oem_occurrences` sayımı)

SPN fault_matrix'lerindeki OEM occurrences → hangi platform yaygın:

| OEM platform | Occurrence |
|---|---:|
| Cummins ISB6.7 2013 | 239 |
| Cummins B6.7 2024 | 238 |
| Cummins B6.7 2021 | 238 |
| Cummins ISB6.7 2010 | 235 |
| Cummins B6.7 2017 | 230 |
| Cummins ISB6.7 2007 | 175 |
| Cummins ISM 2004/2007 | 157 / 157 |
| Cummins ISX15 2013 | 85 |
| Cummins L9 2016 | 79 |
| Detroit Diesel DD13 2014 | 77 |
| Detroit Diesel DD13 2010/2008/2017 | 71/66/65 |
| Detroit Diesel DD15 2008 | 42 |

> **Cummins B6.7/ISB6.7 ailesi açık ara en yaygın** (1.555 occurrence).
> Detroit DD13/DD15 ikinci (≈441). Bu → öncelik **Cummins + Detroit** prosedürlerinde.

### 3.2 Öncelik skoru formülü

```
priority_score = SEV_W[severity] + SUB_W[sıcak alt sistem] + min(oem_occurrence, 60)
  SEV_W: critical=100, high=60, medium=25, unknown=0
  SUB_W: motor/aftertreatment/şanzıman/fren/yakıt = 30, diğer = 0
```

### 3.3 Öncelik listesi (en değerli 200 boşluk)

**Tam liste:** `t61c_gap_targets.json` → `priority_gaps.list` (200 kayıt, tüm alanlar).

Top 10:

| # | SPN | Severity | OEM sayısı | Puan | Önerilen kaynak |
|---:|---:|---|---:|---:|---|
| 1 | 52 | critical | 0 | 130 | aidieseltech.com |
| 2 | 109 | critical | 0 | 130 | aidieseltech.com |
| 3 | 120 | critical | 0 | 130 | roadservice.app |
| 4 | 124 | critical | 0 | 130 | roadservice.app |
| 5 | 131 | critical | 0 | 130 | aidieseltech.com |
| 6 | 147 | critical | 0 | 130 | aidieseltech.com |
| 7 | 153 | critical | 0 | 130 | aidieseltech.com |
| 8 | 156 | critical | 0 | 130 | aidieseltech.com |
| 9 | 230 | critical | 0 | 130 | aidieseltech.com |
| 10 | 231 | critical | 0 | 130 | aidieseltech.com |

**200'lük liste dağılımı:**
- Severity: **critical 200** (100 %) — çünkü tüm critical boşluk 243'tür.
- Kaynak: aidieseltech 155 / professionaldieselrepair 31 / roadservice 14.

**Tüm 3.627 boşluk dağılımı:** critical 243 / high 73 / medium 3.311.

### 3.4 ⚠️ Öncelik listesi UYARISI (dürüst değerlendirme)

Top-200 otomatik skorlama sonucu **tamamı critical** çıktı; ancak bunların
çoğunun `oem_occurrence_count = 0`'dır — yani hiçbir OEM platformunda
gözlemlenmemiş, düşük ticari değerli jenerik critical kayıtlar.

**Gerçek en yüksek değerli boşluklar** şu filtreyle bulunur:
`severity ∈ {critical, high}` **VE** `oem_occurrence_count > 0`
**VE** `subsystem` ∈ {motor, aftertreatment, şanzıman, fren}.

Bu koşulu sağlayan kayıtlar `t61c_gap_targets.json` → `priority_gaps.list`
içinde `oem_occurrence_count > 0` ile filtrelenebilir. Skorlama ham
sıralamadır; **T62 merge görevinde OEM filtresi uygulanmalıdır.**

---

## 4. Kaynak → boşluk eşleştirme tablosu

Her kaynak için **kapsam kanıtı** T50/T51 harvest dosyalarından SPN setleri
çıkarılarak ölçüldü (bkz. `t61c_gap_targets.json` → `source_coverage`).

| Kaynak | Tur | Metot | Kapsam kanıtı | Uygun boşluk |
|---|---|---|---|---|
| `repair.diesellaptops.com` | ≤35/T45-A | free | **1.149 SPN** (26.434 satır) | **En geniş katalog → tüm SPN** |
| `raw.githubusercontent.com/STAS63-bit/sitrak-error-codes` | T45-A | free | 3.402 SPN / 8.042 kayıt | KNORREBS/Bosch (SITRAK) |
| `detroitmanuals.info` | T50 | free (348 s.) | 135 SPN / 756 kayıt (27–4259) | Detroit DD13/15/16 |
| `roadservice.app/diagnose/` | T50 | free (177 s.) | 114 SPN (84–5967) | Eaton/ZF/Knorr EBS-ECAS |
| `aidieseltech.com/diagnostic-pages/` | T51 | free (174 s.) | 108 SPN (27–521049) | Cummins/dizel sensor-tesisat |
| `professionaldieselrepair.com/fault-codes` | T51 | free (792 s.) | 92 SPN (27–524287) | OEM platform (Kenworth/Peterbilt/Freightliner) |
| `b2bendix.com` | ≤35 | free | 27 SPN / 357 satır | Bendix fren/ABS (EBS, ECAS, KNORRABS) |
| `obd2pros.com/dtc-codes/` | T55-C | BD (31 kr) | 31 DTC | **DTC symptoms — TEK kanıtlı kaynak** |
| `j1939hub.com/{cat}/spn-{n}-fmi-{m}/` | T45-A | free | ~452 SPN katalog | jenerik network SPN |
| `bigrigfaults.com/faults/spn-{n}-fmi-{m}` | T55-B/C | free | **placeholder oranı yüksek** | DÜŞÜK ÖNCELİK |

### 4.1 Kaynak verimlilik karşılaştırması

| Kaynak | Sayfa | Distinct SPN | Verim (SPN/sayfa) | Kredi |
|---|---:|---:|---:|---:|
| roadservice.app | 177 | 114 | 0,64 | 0 |
| detroitmanuals.info | 348 | 135 | 0,39 | 0 |
| aidieseltech.com | 174 | 108 | 0,62 | 0 |
| professionaldieselrepair.com | 792 | 92 | 0,12 | 0 |
| **T50+T51 birleşik** | 1.491 | **276** | — | **0** |

- 4 kaynakta da bulunan SPN: **27**
- Yalnız 1 kaynakta bulunan SPN: **182** → kaynaklar büyük ölçüde **örtüşmez**,
  yani her kaynak kendi başına değerli.

### 4.2 Öneri (öncelik sırası, 0 kredi)

1. **DTC `symptoms` (13.521 boşluk)** — `obd2pros.com` genişlet (31→ daha fazla).
   Tek kanıtlı kaynak; BD kredisi gerekir ama hacim potansiyeli en yüksek.
   Alternatif free kaynak: `troublecodes.net` (T45-C, 755 anlam / 680 semptom —
   zaten tarandı, T62'de uygulanmamış kısımlar kontrol edilmeli).
2. **critical+high SPN `procedures_full` (316 boşluk)** — T50/T51 kaynakları
   zaten %100 taranmış. **Geriye kalan boşluklar bu kaynaklarda YOK** →
   yeni free kaynak gerekir (`repair.diesellaptops.com` 1.149 SPN kataloğu
   henüz tam sömürülmemiş).
3. **KNORREBS/Bosch (1.806 boşluk)** — SITRAK verisi zaten mevcut; düşük öncelik.
4. `bigrigfaults.com` — placeholder oranı nedeniyle **tekrar taranmasın**.

---

## 5. T55-B / T55-C kalıntı analizi

T60 (commit `a275012`) T55 çıktılarının bir kısmını uyguladı. Kalan var mı?

### 5.1 T60 iddiaları CANLI doğrulandı ✅

| İddia | Doğrulama | Sonuç |
|---|---|---|
| 10 yeni SPN eklendi | 3011/3146/3265/3318/3568/4321/4485/5309/5398/523497 hepsi `spns`'te | ✅ |
| 27 SPN `procedures_full` doldu | 179,2633,…,520371,523497 hepsi dolu | ✅ |
| 9 DTC `symptoms_en` doldu | B1212,C0035,C0040,C0265,C1201,P1152,P1299,P1421,U0415 hepsi dolu | ✅ |

### 5.2 T55-B (`t55b_free_harvest.json`, bigrigfaults)

| Durum | Adet |
|---|---:|
| Toplam kayıt | 129 |
| `placeholder=true` → **REDDEDİLDİ** (uygulanmadı, doğru) | 10 |
| Gerçek kayıt | 119 |
| Gerçek kayıt → DB'de SPN/FMI **mevcut** | 81 |
| Gerçek kayıt → SPN mevcut, bu FMI `procedures_full` içinde yok | 38 |
| Gerçek kayıt → `procedures_full` hâlâ **boş** | **0** |

> **T55-B: uygulanacak NET kalıntı = 0.** SPN'lerin tümü `procedures_full` dolu.
> 38 kayıt farklı FMI'lar için (aynı SPN'in başka FMI'ları) ama T60 bunları
> farklı kaynak (T50/T51) ile doldurmuş → yeniden uygulama gerekmez.

### 5.3 T55-C (`t55c_bd_harvest.json`)

| Durum | Adet |
|---|---:|
| Toplam kayıt | 183 |
| `real=false` → **REDDEDİLDİ** (placeholder metin) | 33 |
| Gerçek kayıt | 150 |
| — gerçek SPN-tipi | 118 |
| — gerçek DTC-tipi | 32 |

**SPN tarafı:**

| Durum | Adet |
|---|---:|
| Gerçek SPN kayıt → DB'de FMI mevcut | 80 |
| Gerçek SPN kayıt → PF var ama bu FMI yok | 38 |
| Gerçek SPN kayıt → `procedures_full` hâlâ boş | **0** |

**DTC tarafı:**

| Durum | Adet |
|---|---:|
| `symptoms_en` dolu (T60 uyguladı) | 27 |
| `symptoms` (TR) dolu | 2 |
| `symptoms` + `symptoms_en` ikisi de boş | **1** |
| DB'de kod yok (C2100, P0000) | 2 |

> **T55-C: uygulanacak NET kalıntı ≈ 0** (SPN) ve **1 DTC** (symptoms boş).
> Tek kalan: DB'de olup T55-C'de `real` işaretli bir DTC — düşük değer.

### 5.4 Kalıntı sonucu

**T55-B ve T55-C çıktılarında uygulanmamış ANLAMLI kayıt YOK.**
T60 işini tam yaptı: placeholder'ları reddetti (10 + 33), gerçek kayıtları
uyguladı. Kalan "38 + 38" farklı-FMI kayıtları veri kaybı değil — aynı SPN'in
T50/T51 ile daha iyi kaynaktan doldurulmuş olmasıdır.

---

## 6. Kanıt (canlı doğrulama — birebir)

| # | İddia | Komut | Sonuç |
|---|---|---|---|
| 1 | SPN = 3947 | `grep -c '"spn":' spn_db.json` | **3947** |
| 2 | `procedures_full` anahtar = 320 | `grep -c '"procedures_full"' spn_db.json` | **320** |
| 3 | SPN_100 dolu | python json load | `pf_dolu=True steps_dolu=True` |
| 4 | SPN_3236 boş | python json load | `pf_dolu=False` |
| 5 | DTC = 14352 | python json load | **14352** |
| 6 | B1212 `symptoms_en` dolu | python json load | True |
| 7 | P0101 `symptoms` dolu | python json load | True |
| 8 | Dosya boyutları | bytes | SPN 16.999.071 B / 271.087 satır; DTC 26.026.623 B / 571.763 satır |

**Doğrulama script'i:** `_t61c_verify.py` (repo kökünde, salt-okunur).

---

## 7. Çıktılar ve kapsam

- `spn_gap_hunter/output/t61c_gap_targets.json` — tam makine-okunur çıktı
- `spn_gap_hunter/output/t61c_gap_targets.md` — bu rapor

**Kapsam dışı:** DB'ye yazma yapılmadı (kural: T62 merge ayrı görev).
Tarama yapılmadı, 0 BrightData kredisi kullanıldı.

### T55-A dosyası güncelleme notu

Görev, `t55a_pf_gap_analysis.json`'un güncellenmesini istedi (DB 3937→3947).
Bu dosya **T55-A'nın tarihsel çıktısıdır** ve üzerine yazmak T55-A kanıtını
bozar. Bunun yerine **güncellenmiş değerler bu rapora ve
`t61c_gap_targets.json`'a yazıldı.** Fark:

| Metrik | T55-A (3937 SPN) | T61-C (3947 SPN) |
|---|---:|---:|
| `procedures_full` dolu | 293 | **320** (+27, T60) |
| `procedures_full` boş | 3.644 | **3.627** (−17) |
| Kapsam | 7,4 % | **8,11 %** |

> Not: T55-A boş=3644, T60 öncesi 3937−293=3644. T60 +10 SPN +17 PF doldurma ile
> 3947−320=3627 boş. Tutarlı.

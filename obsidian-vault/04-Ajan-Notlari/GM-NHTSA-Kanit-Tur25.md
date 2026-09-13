---
tags: [ajan-raporu, gm, mode06, nhtsa, tur25]
ajan: pitboss
tur: Tur-25 Ek (otonom)
kredi: 0
created: 2026-09-13
---

# 🔬 GM Mode06 ve NHTSA Kanıt Katmanları

## 1. GM Mode06 Kanıt Katmanı (gsi.ext.gm.com)

**Kaynak:** GM Global Service Information — resmi, kamu, ücretsiz
**Kapsam:** 899 PDF hedefi → **438 indirildi** (2000-2006, GM'nin yayınladığı tüm aralık)

| Metrik | Değer |
|---|---|
| İndirilen PDF | 438 (28 MB) |
| DTC içeren PDF | **358** |
| Benzersiz DTC | **1.053** |
| DTC'ye eklenen `gm_monitor` | **1.031** |
| Yeni DTC kaydı | **22** |

### Örnek içerik (resmi GM teşhis prosedürleri)

```
B0159 — Outside Air Temperature Sensor:
  "This DTC diagnoses if the OATS ambient temperature reading correlates
   with the ambient temperature predicted from the IATS. Min_OAT − Max_IAT..."

C0078 — Tire Diameter Mismatch:
  "This diagnostic detects if the ABS is indicating a tire diameter mis-match
   condition for PCM skid signal. ABS controller sends a message to PCM..."

P0030 — O2 Sensor Heater Control Circuit:
  "This DTC checks the Heater Output Driver circuit for electrical integrity..."
```

**Değer:** Her DTC için GM'nin **gerçek monitor stratejisi + eşik değerleri + enable koşulları**.

**Kısıt:** PDF 2000-2006 aralığı (GM sonrasını yayınlamıyor). 461 URL 404 döndü — bu beklenen.

## 2. NHTSA Şikayet Kanıt Katmanı

**Kaynak:** `api.nhtsa.gov/complaints/complaintsByVehicle` — kamu, ücretsiz

| Metrik | Değer |
|---|---|
| Probe (marka-model-yıl) | 384 |
| Başarılı | 255 |
| Toplanan şikayet | **54.027** |
| DTC içeren şikayet | **789** |
| Dosya boyutu | 39,3 MB |

### Örnek (gerçek vaka + DTC)

```
FORD F-150 2016 | P0705, P0706, P0707, P0720, P0722, P1702
  "Truck downshifts into low gear, yellow wrench and engine light come on
   and the truck goes into limp mode, no warnings or anything..."

FORD F-150 2016 | P0715, P0717
  "Vehicle transmission downshift to a lower gear while traveling at city
   and highway speeds without any prior warning or driver input.
   Diagnostic showed codes P0715 and P0717."
```

**Değer:** DTC'leri **gerçek arıza semptomlarıyla** bağlar. Ana projenin
`VehicleSession.diagnostic_evidence` / `DiagnosticEvent` kanıt modeline katkı
(DTC veritabanına değil).

## Üretilen Dosyalar

| Dosya | İçerik |
|---|---|
| `pitboss_gm_harvest.py` | PDF indirici (cache-first) |
| `parse_gm_pdfs_v2.py` | DTC + monitor stratejisi çıkarıcı |
| `merge_tur25_gm_evidence.py` | DTC DB merge |
| `raw_gm_mode06_dtc_evidence.json` | 1.053 DTC kanıtı (1,2 MB) |
| `pitboss_nhtsa_complaints.py` | Şikayet toplayıcı |
| `raw_nhtsa_complaints_tur25.json` | 54.027 şikayet (39,3 MB) |

## Yedekler

`dtc_database.json.bak_tur25_gm`, `.bak_tur25_wal33d_oem`, `.bak_tur25_spnfmi`

## İlgili

- [[SITRAK-Ceviri-Tur25]]
- [[Teşhis-Veritabani-Ozeti]]
- [[Ajan-Tur-Kayitlari]]

---
tags: [ajan-raporu, gm, mode06, v2, tur26]
ajan: telemetry
tur: Tur-26
kredi: 0
created: 2026-09-13
---

# 🔬 GM Mode06 v2 — 2007-2025 Arşivi Açıldı

## Kritik Keşif: URL Encode Hatası

**Sorun:** GM'nin 2007+ PDF URL'lerinde **boşluk karakteri** var:
```
07_GRP01_All Engines.pdf
```
`urllib` boşluğu encode etmediği için istek iptal oluyordu (0 byte).

**Çözüm:** `urllib.parse.quote(url, safe=":/?&=#%+,()[]!$&'*;@~")` → **%20** encode.

**Sonuç:** 2007-2025 arşivi tamamen erişilebilir hale geldi.

## Sonuçlar

| Metrik | Değer |
|---|---|
| İndirilen (v2, 2007-2025) | **333/333 (%100)** |
| Yeni dosya | 293 |
| Toplam arşiv | **765 PDF / 2.63 GB** |
| Ayrıştırılan PDF | 347 |
| Benzersiz DTC (v2) | **1.965** |
| **Birleşik benzersiz DTC** | **2.256** |
| Yeni DTC kaydı | **+164** |
| Ek `gm_monitor` | +1.039 |

## Kanıt Örnekleri (gerçek GM monitor stratejileri)

```
P0016 — Crankshaft/Camshaft Correlation:
  "Calculation of crank position by CKP sensor and CMP sensor disagree
   by < 6deg crank angle. Detects implausible camshaft/crankshaft sensor..."

P0642 — Sensor Reference Voltage A Circuit Low:
  "Prevents P0643, P0335, P0336 DTCs not set
   Ignition ON, Engine Speed ≥ 50 rpm
   PATH 1) 255 crankshaft increments (60 increments/revolution)..."
```

## DTC DB Etkisi

| | Önce | Sonra |
|---|---|---|
| Toplam DTC | 14.188 | **14.352** |
| `gm_monitor` | 1.053 | **2.092** |

## Dosyalar

| Dosya | İçerik |
|---|---|
| `gm_harvest_v2.py` | URL-encode düzeltmeli indirici |
| `raw_gm_mode06_v2_progress.json` | 1.965 DTC kanıtı (14 MB) |
| `raw_gm_mode06_evidence_merged.json` | v1+v2 birleşik (29,4 MB) |
| `merge_tur25_gm_evidence.py` | Merge scripti |

## Kısıt

- GM arşivi 2000-2025 aralığını kapsıyor (GM sonrasını yayınlamıyor)
- 1995-1999 URL'leri katalogda var ama dosyalar mevcut değil

## İlgili

- [[SITRAK-Ceviri-Tur25]]
- [[GM-NHTSA-Kanit-Tur25]]
- [[Teşhis-Veritabani-Ozeti]]

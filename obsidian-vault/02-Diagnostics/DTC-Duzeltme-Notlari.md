---
tags: [dtc, veri-kalitesi, duzeltme]
created: 2026-09-13
---

# 🔧 DTC Veri Kalitesi Düzeltmeleri

## Sistematik başlık hatası (Tur-25'te bulundu)

**Sorun:** 721 kaydın başlığı yanlıştı — C/B/U kodlarına yanlışlıkla `"P#### OBD Code"` yazılmış.

**Kök neden:** Tur-3/4 toplu hasadında kod harfi (`C`/`B`/`U`) kopyalanmamış, şablon `P` sabit kalmış.

| Kod | Önce (hatalı) | Sonra (doğru) |
|---|---|---|
| `U0002` | `P0002 OBD Code` | `High Speed CAN Communication Bus Performance` |
| `C0001` | `P0001 OBD Code` | `TCS Control Channel A Valve 1` |
| `B0014` | `P0014 OBD Code` | `Right Front/Passenger Frontal Deployment Loop Circuit` |

**Sonuç:** 331/721 onarıldı. Kalan 390 SAE J2012'de **rezerve alan** (komşuları tanımlı, kendileri değil — dtcsearch/racetunefiles/carobdcodes/obd-codes hepsi 404/403).

## Merge scriptleri

- `merge_tur25_wal33d.py` — 4.866 yeni kod + 243 başlık
- `merge_tur25_dtc_titlefix.py` — 331 başlık onarımı (3 kaynak birleşik)

> [!warning] Veri bütünlüğü kuralı
> Merge sırasında **dolu alanlara dokunma** — yalnız boş/jenerik alanları doldur.

---
tags: [ajan-raporu, sitrak, ceviri, tur25]
ajan: pitboss + telemetry/scout/chassis/tuner/marshal/uplink/cockpit
tur: Tur-25 Ek
kredi: 0
created: 2026-09-13
---

# 🌍 SITRAK Çevirisi ve J1939 Entegrasyonu

## Özet

**5.518 Rusça metin İngilizce'ye çevrildi (%100)** ve **3.402 SPN'lik SITRAK veritabanı** J1939 veritabanına entegre edildi.

| Metrik | Önce | Sonra |
|---|---|---|
| J1939 SPN | 865 | **3.710** (4.3x) |
| DTC korelasyonu | — | **697** kayıt |
| Çeviri | 0 | **5.518** (%100) |
| BrightData kredisi | — | **0** |

## Kaynak ve Lisans

- **Kaynak:** `STAS63-bit/sitrak-error-codes` (GitHub, branch `master`)
- **Lisans:** **CC BY 4.0** — © Megadata / megadata.pro
- **Atıf:** Her kayıtta `attribution` alanı + `metadata.attribution.sitrak`
- **Kapsam:** 8.042 kayıt / 3.402 benzersiz SPN / 76 FMI / 44 sistem
- **Sistemler:** Bosch ECU (MC11/MC13), ZF TraXon, KNORR EBS/ABS, WABCO EBS, ECAS, ABS, NanoBCU

## Çeviri Süreci (öğrenilen dersler)

### ⛔ Yanlış yöntem (veriyi bozdu)
Kelime-bazlı sözlük + Rusça sözcük sırasını koruyarak değiştirme:
```
"Передний мост" → "Переднandй axle"   ← Kiril kalıntı
"отдельным"     → "оwithтальным"       ← kelime içi bozulma
```
1.368 kayıt silindi ve yeniden yapıldı.

### ✅ Doğru yöntem
Her metni **oku/anla → tam İngilizce cümle yaz**. İndeks anahtarlı tablo:
```python
T = {index: "English full sentence", ...}
strings[i]["en"] = T[i]
```

### Kalite kapısı
`sitrak_qc.py` her partı denetler: `kiril_kalinti`, `karisik_yazim`, `kopyalanmis`,
`kisaltma_kayip`, `sayi_kayip`, `kirpilmis`. **Sonuç: TEMİZ (0 sorun).**

## Üretilen Dosyalar

| Dosya | İçerik |
|---|---|
| `sitrak_translate_part1-4.json` | 5.518 çevrilmiş metin |
| `sitrak_qc.py` | Kalite denetleyici |
| `merge_tur25_sitrak.py` | Merge scripti (J1939 + DTC korelasyon) |
| `CEVIRI_YONTEMI_OKU.md` | Yöntem uyarısı (ajanlar için) |
| `sitrak_sozluk.json` | Otomotiv terminoloji sözlüğü |
| `sitrak_rules_v3.py`, `sitrak_abs_rules*.py` | Kalıp kuralları (yardımcı) |

## Merge Sonucu

- **J1939:** 865 → **3.710 SPN** (v1.9.0)
- **Zenginleştirme:** 348 mevcut SPN'e `fmi_map` + `sitrak_systems`
- **DTC korelasyonu:** 697 kayda `j1939_spn_fmi` alanı
- **Yedekler:** `j1939_spn_fmi_database.json.bak_tur25_sitrak`, `dtc_database.json.bak_tur25_spnfmi`

## İlgili

- [[Teşhis-Veritabani-Ozeti]]
- [[Ajan-Tur-Kayitlari]]
- [[DTC-Duzeltme-Notlari]]

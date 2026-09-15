# DENETİM: SENTEZ — Bütünleşik Güvenlik ve Mimari Raporu

Ürün: Ticari CAN/CAN-FD Teşhis Platformu.
Girdi: `docs/review/ASAMA-[1-6]-*.md` raporları.

## 1. Görev
6 aşamanın tamamlanan raporlarını oku ve birleşik sentez raporunu **`docs/review/OZET.md`** dosyasına üret. (Kaynak kodları tekrar okumaya gerek yoktur).

## 2. Rapor Formatı

```markdown
# BİRLEŞİK GÜVENLİK VE MİMARİ DENETİM ÖZETİ
Tarih: <YYYY-MM-DD> | Aşama Sayısı: 6
Bulgu Dağılımı: Toplam N (KRİTİK: X | YÜKSEK: Y | ORTA: Z | DÜŞÜK: W | BİLGİ: V)

## 1. Birleşik Bulgu Matrisi
| # | Aşama | dosya:satır | Severity | Sorun Özeti | Önerilen Düzeltme |
|---|---|---|---|---|---|
*(Tüm aşamaların bulguları severity sırasına göre dizilmiş)*

## 2. Çapraz Sistemik Riskler (Ortak Desenler)
### [Desen A] <Başlık: Örn. Yutulan Hatalar / Fail-Open Eğilimi / Eşzamanlılık Korumasızlığı>
- **Etkilenen Katmanlar / Dosyalar:**
- **Kök Neden:**
- **Merkezi Çözüm Stratejisi:**

## 3. Düzeltme Yol Haritası (Kritik Öncelik Sıralaması)
| Sıra | Bulgu / Bileşen | Risk & Gerekçe | Tahmini Efor | Öncelik Seviyesi |
|---|---|---|---|---|
*(Can güvenliği / ASIL > Ticari koruma > Veri bütünlüğü > Diğer)*

## 4. Ticari Dağıtım ve Yayın Kararı (Go / No-Go)
- **Güvenlik & Stabilite Notu:** <1-5 arası puan ve gerekçe>
- **Ticari Sürüm Engelleri (Blockers):** <Yayınlanmadan önce MUTLAKA kapatılması gereken maddeler>
- **Karar:** GO / NO-GO
```

## 3. Tamamlama Bildirimi
Bitince sadece özeti yaz:
`SENTEZ TAMAM | Toplam Bulgu: N (KRİTİK: X, YÜKSEK: Y, ORTA: Z) | Karar: GO/NO-GO | Rapor: docs/review/OZET.md`

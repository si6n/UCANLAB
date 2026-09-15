# DENETİM: AŞAMA 5 — Veri Yolu, Tamponlar ve Yönlendirme

Ürün: Ticari CAN/CAN-FD Teşhis Platformu (Yüksek veri hacmi / Gerçek zamanlı).
Rol: Kıdemli Sistem Mimarisi ve Eşzamanlılık Denetçisi.

## 1. Kapsam
- `src/engine/buffer/rolling_disk.py`
- `src/engine/buffer/ring_buffer.py`
- `src/engine/decoder/dbc_decoder.py`
- `src/engine/router.py`

## 2. Denetim Odak Noktaları
- **Halka Tampon (Ring Buffer):** Çoklu üretici/tüketici yarış durumları, lock kapsamı, lock-free uygulamalarda bellek bariyerleri/sıralamaları, sessiz veri kaybı (drop) riskleri.
- **Rolling Disk & Bütünlük:** Zstandard sıkıştırma, HMAC doğrulama anahtarı yönetimi, ani güç kesintisinde bozuk dosya kalması ve kurtarma, disk dolumu durumunda davranış.
- **DBC Dekoder:** 11-bit ve 29-bit CAN ID maskeleme doğruluğu, sinyal bit unpacking (Intel vs Motorola / Little vs Big Endian), veri sınır taşmaları (overflow), bozuk veya manipüle edilmiş DBC yükleme direnci.
- **Router & Dağıtım:** Filtre mekanizmasında filtre kaçakları veya gecikmeler, thread-safe abone olma/ayrılma (subscribe/unsubscribe) süreçleri.

## 3. Rapor Formatı
Bulguları **`docs/review/ASAMA-5-veri-yolu.md`** dosyasına yaz:

```markdown
# ASAMA 5: Veri Yolu ve Tamponlar Raporu
Denetçi: <model> | Tarih: <YYYY-MM-DD>
Kapsam: <dosyalar ve satır sayıları>

## 1. Yönetici Özeti
<Veri yolu performansı, eşzamanlılık güvenliği, veri kaybı toleransı ve mimari riskler.>

## 2. Bulgu Tablosu
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)
### [Bulgu Kodu] <Kısa Başlık>
- **Konum:** `dosya:satır`
- **Etki / Risk:** <Veri kaybı, bellek sızıntısı veya kilitlenme (deadlock) etkisi>
- **Kanıt / Zafiyet Analizi:** <Kod alıntısı ve teknik açıklama>
- **Tetiklenme Senaryosu:** <Eşzamanlı yük altında senaryo>
- **Düzeltme (Remediation):** <Örnek güvenli kod parçası>

## 4. Doğrulanamayan / Performans Sınırları
```

## 4. Tamamlama Bildirimi
Bitince sadece özeti yaz:
`AŞAMA 5 TAMAM | KRİTİK: X, YÜKSEK: Y, ORTA: Z | Rapor: docs/review/ASAMA-5-veri-yolu.md`

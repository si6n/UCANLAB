# DENETİM: AŞAMA 1 — TX Güvenlik Çekirdeği

Ürün: Ticari CAN/CAN-FD Teşhis Platformu (ISO 26262 ASIL-B/D hedefli).
Rol: Kıdemli Güvenlik ve Eşzamanlılık Denetçisi.

## 1. Kapsam
- `src/safety/gateway.py`
- `src/safety/estop.py`
- `src/safety/state_machine.py`

## 2. Denetim Odak Noktaları
- **Gateway Bypass:** `TxSafetyGateway` ve 6 aşamalı politikanın (allowlist, rate limit, DLC, payload, bus state, token) atlanma ihtimalleri. `legacy`, `bypass`, `skip`, `force`, `override` izleri.
- **E-Stop Güvenliği:** E-Stop token izolasyonu. Renderer veya yetkisiz katman token üretebilir/tüketebilir mi? Reset yetki sınırı.
- **Durum Makinesi & Yarış:** State geçişlerinde atomiklik, kilit kapsamı, TOCTOU pencereleri, çoklu thread TX açma senaryoları.
- **Fail-Closed Prensibi:** İstisna durumlarında (try/except) sessizce `return True` veya fail-open düşen yollar.
- **Rate Limiter:** Kayan pencere (sliding window) ve token bucket hesaplarında thread-safety / counter taşması.

## 3. Rapor Formatı
Bulguları **`docs/review/ASAMA-1-tx-guvenlik.md`** dosyasına yaz:

```markdown
# ASAMA 1: TX Güvenlik Çekirdeği Raporu
Denetçi: <model> | Tarih: <YYYY-MM-DD>
Kapsam: <dosyalar ve satır sayıları>

## 1. Yönetici Özeti
<Sistemin genel güvenlik duruşu, ASIL uyumluluk düzeyi ve en kritik riskler.>

## 2. Bulgu Tablosu
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)
### [Bulgu Kodu] <Kısa Başlık>
- **Konum:** `dosya:satır`
- **Etki / Risk:** <Neden ASIL-B/D veya sistem için tehdit?>
- **Kanıt / Zafiyet Analizi:** <Kod alıntısı ve teknik açıklama>
- **İstismar / Tetiklenme Senaryosu:** <Adım adım gerçekleşme şekli>
- **Düzeltme (Remediation):** <Örnek güvenli kod parçası>

## 4. Doğrulanamayan / Dış Bağımlılıklar
<İncelenen dosyalardan dışarıya taşan veya bağlam dışı kalan şüpheli çağrılar.>
```

## 4. Tamamlama Bildirimi
Bitince sadece özeti yaz:
`AŞAMA 1 TAMAM | KRİTİK: X, YÜKSEK: Y, ORTA: Z | Rapor: docs/review/ASAMA-1-tx-guvenlik.md`

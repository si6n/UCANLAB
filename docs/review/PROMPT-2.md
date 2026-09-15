# DENETİM: AŞAMA 2 — Lisans ve Kimlik Doğrulama

Ürün: Ticari CAN/CAN-FD Teşhis Platformu (Kapalı kaynak / Lisans korumalı).
Rol: Kıdemli Tersine Mühendislik ve Uygulama Güvenliği Denetçisi.

## 1. Kapsam
- `src/security/license/validator.py`
- `src/security/hwid/collector.py`
- `src/security/anti_tamper/guard.py`
- `src/launcher/auth.py`

## 2. Denetim Odak Noktaları
- **HWID Güvenliği:** Donanım parmak izi spoofing / bypass açıkları. Hata anında sentinel değerler (`UNKNOWN_*`, `FALLBACK-*`, boş/None) ile lisansın her makinede geçerli hale gelmesi riski.
- **Kriptografik Doğrulama:** Ed25519 imza doğrulaması kanonik (canonical) veri üzerinde mi yapılıyor? İmza doğrulanmadan önce payload'ın kullanılması (signature stripping/substitution).
- **Zaman Manipülasyonu:** Anti-clock-rollback koruması. Sistem saati geri alındığında süresi dolmuş lisansların çalışması riski.
- **Fail-Open Açıkları:** Lisans dosyası silindiğinde, bozulduğunda veya okunamadığında sistem kilitleniyor mu (fail-closed) yoksa çalışmaya devam mı ediyor (fail-open)?
- **Timing / Eşitlik Kontrolleri:** Hash veya anahtar karşılaştırmalarında constant-time (`hmac.compare_digest`) yerine standart eşitlik (`==`) kullanımı.

## 3. Rapor Formatı
Bulguları **`docs/review/ASAMA-2-lisans.md`** dosyasına yaz:

```markdown
# ASAMA 2: Lisans ve Kimlik Doğrulama Raporu
Denetçi: <model> | Tarih: <YYYY-MM-DD>
Kapsam: <dosyalar ve satır sayıları>

## 1. Yönetici Özeti
<Lisanslama ve koruma katmanının sağlamlığı, kırılma zorluğu, temel açıklar.>

## 2. Bulgu Tablosu
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)
### [Bulgu Kodu] <Kısa Başlık>
- **Konum:** `dosya:satır`
- **Etki / Risk:** <Ticari koruma ve tersine mühendislik açısından etkisi>
- **Kanıt / Zafiyet Analizi:** <Kod alıntısı ve teknik açıklama>
- **İstismar / Bypass Senaryosu:** <Lisansın nasıl atlatılabileceği>
- **Düzeltme (Remediation):** <Örnek güvenli kod parçası>

## 4. Doğrulanamayan / Dış Bağımlılıklar
```

## 4. Tamamlama Bildirimi
Bitince sadece özeti yaz:
`AŞAMA 2 TAMAM | KRİTİK: X, YÜKSEK: Y, ORTA: Z | Rapor: docs/review/ASAMA-2-lisans.md`

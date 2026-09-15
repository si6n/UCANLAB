# DENETİM: AŞAMA 4 — Protokol Katmanları (J1939 / ISO-TP / UDS / Flasher)

Ürün: Ticari CAN/CAN-FD Teşhis Platformu (ISO 26262 ASIL-B/D hedefli).
Rol: Kıdemli Otomotiv Protokol ve Gömülü Sistem Denetçisi.

## 1. Kapsam
- `src/protocols/j1939/transport.py`
- `src/protocols/uds/isotp.py`
- `src/protocols/uds/flasher.py`
- `src/protocols/uds/client.py`

## 2. Denetim Odak Noktaları
- **J1939 Transport Protocol (TP):** BAM ve RTS/CTS oturumlarında tampon büyüme sınırları, yetim/kesilen oturumların bellek sızıntısı yapması, sequence number taşması, eşzamanlı multi-peer çakışmaları.
- **ISO-TP (ISO 15765-2):** Reassembly tampon güvenliği (4095 byte ve CAN-FD sınırları), FF/CF/FC akış kontrol durum makineleri, geçersiz BS / STmin değerlerinin işlenmesi, DoS/OOM riskleri.
- **UDS Client & Security:** Seed/Key (SecurityAccess 0x27) atlatma riskleri, NRC (özellikle 0x78 responsePending, 0x33, 0x35) zaman aşımı yönetimi, P2/P2* sayaç doğruluğu.
- **ECU Flasher:** Firmware yükleme öncesi imza/CRC doğrulaması var mı? İletişim kopması / güç kesintisinde ECU tuğla olma (bricking) riskine karşı rollback ve kurtarma garantileri.

## 3. Rapor Formatı
Bulguları **`docs/review/ASAMA-4-protokoller.md`** dosyasına yaz:

```markdown
# ASAMA 4: Protokol Katmanları Raporu
Denetçi: <model> | Tarih: <YYYY-MM-DD>
Kapsam: <dosyalar ve satır sayıları>

## 1. Yönetici Özeti
<Protokol yığınının standarda uygunluğu, robustluk, DoS/OOM direnci ve kritik riskler.>

## 2. Bulgu Tablosu
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)
### [Bulgu Kodu] <Kısa Başlık>
- **Konum:** `dosya:satır`
- **Etki / Risk:** <Protokol çökmesi, ECU hasarı veya veri bozulması etkisi>
- **Kanıt / Zafiyet Analizi:** <Kod alıntısı ve teknik açıklama>
- **İstismar / Tetiklenme Senaryosu:** <Protokol seviyesinde senaryo>
- **Düzeltme (Remediation):** <Örnek güvenli kod parçası>

## 4. Doğrulanamayan / Standart Uyuşmazlıkları
```

## 4. Tamamlama Bildirimi
Bitince sadece özeti yaz:
`AŞAMA 4 TAMAM | KRİTİK: X, YÜKSEK: Y, ORTA: Z | Rapor: docs/review/ASAMA-4-protokoller.md`

# DENETİM: AŞAMA 6 — Masaüstü Arayüzü ve Başlatıcı

Ürün: Ticari CAN/CAN-FD Teşhis Platformu (pywebview + JS Arayüzü).
Rol: Kıdemli İstemci Güvenliği ve Sistem Entegrasyon Denetçisi.

## 1. Kapsam
- `src/ui/desktop_app.py`
- `src/launcher/app.py`
- `src/main.py`

## 2. Denetim Odak Noktaları
- **pywebview IPC Köprüsü:** JS -> Python geçişinde katı girdi doğrulaması var mı? DOM/Renderer katmanından gelen parametreler tipe ve aralığa göre filtreleniyor mu?
- **Dosya Yolu ve Komut Enjeksiyonu:** Path traversal (`..`, mutlak yollar), dosya indirme/yükleme manipülasyonu, `subprocess` ve `shell=True` riskleri.
- **Yetki Aşımı (Privilege Escalation):** Renderer üzerinden doğrudan tehlikeli fonksiyonlar (TX frame gönderimi, firmware flash tetikleme, E-Stop bypass) yetkisiz veya teyitsiz çağrılabiliyor mu?
- **Hassas Bilgi Sızıntısı:** Lisans bilgileri, API anahtarları veya işletim sistemi yollarının IPC veya loglar üzerinden açık edilmesi.
- **Başlatma ve Kapanma Döngüsü:** Main başlatma sırasında güvenlik katmanlarının UI'dan önce ayağa kalkması; çökmelerde fail-safe durumu.

## 3. Rapor Formatı
Bulguları **`docs/review/ASAMA-6-ui.md`** dosyasına yaz:

```markdown
# ASAMA 6: Arayüz ve Başlatıcı Raporu
Denetçi: <model> | Tarih: <YYYY-MM-DD>
Kapsam: <dosyalar ve satır sayıları>

## 1. Yönetici Özeti
<IPC güvenliği, istemci saldırı yüzeyi ve yetki izolasyonu analizi.>

## 2. Bulgu Tablosu
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)
### [Bulgu Kodu] <Kısa Başlık>
- **Konum:** `dosya:satır`
- **Etki / Risk:** <İstemci istismarı, RCE, yerel yetki yükseltme etkisi>
- **Kanıt / Zafiyet Analizi:** <Kod alıntısı ve teknik açıklama>
- **İstismar Senaryosu:** <JS arayüzünden backend tetikleme adımları>
- **Düzeltme (Remediation):** <Örnek güvenli kod parçası>

## 4. Kapsam Boşlukları / Test Edilemeyen Alanlar
```

## 4. Tamamlama Bildirimi
Bitince sadece özeti yaz:
`AŞAMA 6 TAMAM | KRİTİK: X, YÜKSEK: Y, ORTA: Z | Rapor: docs/review/ASAMA-6-ui.md`

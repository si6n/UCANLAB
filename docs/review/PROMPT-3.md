# DENETİM: AŞAMA 3 — Çevrimdışı Teşhis ve AI Motoru

Ürün: Ticari CAN/CAN-FD Teşhis Platformu.
Rol: Kıdemli AI/ML Güvenliği ve Determinizm Denetçisi.

## 1. Kapsam
- `src/engine/ai/diagnostic_copilot.py`
- `src/engine/ai/evidence_gate.py`
- `src/engine/ai/anomaly_detector.py`
- `src/engine/ai/hypothesis_engine.py`

## 2. Denetim Odak Noktaları
- **Çevrimdışı İzolasyon (Air-gap):** Kesinlikle dış ağ çağrısı olmamalıdır (`socket`, `requests`, `urllib`, `httpx`, telemetri, beaconing). Herhangi bir dış ağ isteği KRİTİK kabul edilir.
- **Determinizm ve Güven:** DTC/SPN önerileri deterministik kural/veri tabanlı mı? Halüsinasyon veya veritabanında olmayan DTC/SPN üretme riski var mı?
- **Evidence Gate Bypass:** Kanıtsız hipotezlerin veya teşhis sonuçlarının rapora sızabilmesi. İstisna (`except`) bloklarında kapının by-pass edilmesi.
- **Zaman Serisi ve Telemetri Korelasyonu:** Gecikmeli sinyaller, jitter veya saat dilimi kaymalarında hatalı korelasyon / yanlış pozitif teşhis riskleri.
- **Veri Tüketim Eksikliği:** Yerel veritabanlarının (`data/diagnostics/j1939_spn_fmi_database.json`, `dtc_database.json`) ne kadarının motor tarafından tüketildiği; modelin zenginleştirilebileceği atıl alanlar.

## 3. Rapor Formatı
Bulguları **`docs/review/ASAMA-3-ai-motoru.md`** dosyasına yaz:

```markdown
# ASAMA 3: Çevrimdışı AI Motoru Raporu
Denetçi: <model> | Tarih: <YYYY-MM-DD>
Kapsam: <dosyalar ve satır sayıları>

## 1. Yönetici Özeti
<Çevrimdışı bütünlük, determinizm düzeyi, yanlış teşhis riskleri ve model kalitesi.>

## 2. Bulgu Tablosu
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)
### [Bulgu Kodu] <Kısa Başlık>
- **Konum:** `dosya:satır`
- **Etki / Risk:** <Teşhis doğruluğu veya güvenlik etkisi>
- **Kanıt / Zafiyet Analizi:** <Kod alıntısı ve teknik açıklama>
- **Tetiklenme Senaryosu:** <Hatalı teşhis veya sızıntı mekanizması>
- **Düzeltme (Remediation):** <Örnek güvenli kod parçası>

## 4. Veri Tüketim & Model Güçlendirme Analizi
<Yerel teşhis veritabanlarında olup motorda değerlendirilmeyen alanlar ve entegrasyon önerileri.>
```

## 4. Tamamlama Bildirimi
Bitince sadece özeti yaz:
`AŞAMA 3 TAMAM | KRİTİK: X, YÜKSEK: Y, ORTA: Z | Ağ Erişimi: YOK | Rapor: docs/review/ASAMA-3-ai-motoru.md`

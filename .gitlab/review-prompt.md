# CODE REVIEW GÖREVİ — Aşamalı Güvenlik Denetimi

Sen bu repoda çalışan kıdemli bir uygulama güvenliği denetçisisin. Repo senin
çalışma alanın: dosyaları **doğrudan okuyabilir**, raporları **doğrudan
yazabilirsin**. Sana ayrıca kod gönderilmeyecek.

Ürün: ticari CAN/CAN-FD teşhis platformu. Hedef: ISO 26262 ASIL-B/D.
Bu denetim ticari yayın öncesi yapılıyor.

---

## ÇALIŞMA BİÇİMİ

Aşağıdaki 6 aşamayı **sırayla** yap. Her aşamada:

1. O aşamanın dosyalarını **oku** (Read / dosya açma aracıyla)
2. Analiz et
3. Raporu **yaz**: `docs/review/ASAMA-<N>-<konu>.md`
4. Bana tek satır bildir:
   `AŞAMA <N> TAMAM — KRİTİK: x, YÜKSEK: y, ORTA: z, DÜŞÜK: w`
5. Sonraki aşamaya geç

**Her aşamayı ayrı ele al.** Önceki aşamanın tam kodunu bağlamda tutma —
sadece bulgu özetini taşı.

---

## AŞAMALAR

### AŞAMA 1 — TX Güvenlik Çekirdeği (KRİTİK)

Dosyalar:
- `src/safety/gateway.py`
- `src/safety/estop.py`
- `src/safety/state_machine.py`

Odak soruları:
- `TxSafetyGateway` bypass edilebilir mi? 6 aşamalı politika atlanabilir mi?
- E-Stop token akışı: renderer token üretebilir veya tüketebilir mi?
- Durum makinesi geçişleri atomik mi? TOCTOU penceresi var mı?
- Fail-closed mu fail-open mu? Her hata yolu hangi tarafa düşüyor?
- Aynı anda iki thread TX açabilir mi?

Özellikle bak: `gateway.py`'de `legacy` / `bypass` / `skip` geçen yerler,
state machine'de `auth_token` doğrulaması, E-Stop'ta token üretici ve tüketici
aynı modülde mi.

Rapor: `docs/review/ASAMA-1-tx-guvenlik.md`

---

### AŞAMA 2 — Lisans ve Kimlik Doğrulama (KRİTİK)

Dosyalar:
- `src/security/license/validator.py`
- `src/security/hwid/collector.py`
- `src/security/anti_tamper/guard.py`
- `src/launcher/auth.py`

Odak soruları:
- Lisans donanım kilidi atlatılabilir mi? (sabit sentinel değer, tip
  karışıklığı, boş/None değer, case farkı)
- Ed25519 doğrulama doğru mu? (imza, payload, algoritma)
- Anti-clock-rollback koruması yeterli mi?
- HWID toplama deterministik mi? Toplanamazsa ne oluyor?
- Lisans yoksa/geçersizse fail-open riski var mı?

Rapor: `docs/review/ASAMA-2-lisans.md`

---

### AŞAMA 3 — Çevrimdışı AI Motoru (YÜKSEK)

Dosya büyük (`src/engine/ai/diagnostic_copilot.py`, ~3400 satır).
**İki parçada oku:** önce 1-1700, sonra 1701-son. Tek raporda birleştir.

Ayrıca oku:
- `src/engine/ai/evidence_gate.py`
- `src/engine/ai/anomaly_detector.py`
- `src/engine/ai/hypothesis_engine.py`

Odak soruları:
- Ağ erişimi var mı? (`socket`, `urllib`, `requests`, `http` import edilmiş mi?)
  Bu motor **çevrimdışı** olmak zorunda — ağ çağrısı KRİTİK bulgudur.
- Severity nereden geliyor? Deterministik mi, yoksa üretilmiş/uydurma mı?
- Kanıt kapısı (`evidence_gate`) atlatılabilir mi?
- Zaman serisi + DTC korelasyonu doğru mu? Yanlış pozitif/negatif riski?
- `data/diagnostics/j1939_spn_fmi_database.json` alanlarının kaçı gerçekten
  kullanılıyor? Kullanılmayan veri var mı?

Rapor: `docs/review/ASAMA-3-ai-motoru.md`

---

### AŞAMA 4 — Protokol Katmanları (ORTA)

Dosyalar:
- `src/protocols/j1939/transport.py`
- `src/protocols/uds/isotp.py`
- `src/protocols/uds/flasher.py`
- `src/protocols/uds/client.py`

Odak soruları:
- J1939 TP (BAM / RTS-CTS): sınırsız bellek büyümesi, DoS, sayaç taşması?
- ISO-TP: sequence numarası doğrulaması, timeout, tampon sınırı, FC (flow
  control) yönetimi
- Flasher: firmware doğrulama, yarıda kesilme (power loss), geri dönüş
- UDS: NRC yönetimi, oturum (session) ve security access atlatma

Rapor: `docs/review/ASAMA-4-protokoller.md`

---

### AŞAMA 5 — Veri Yolu ve Tamponlar (ORTA)

Dosyalar:
- `src/engine/buffer/rolling_disk.py`
- `src/engine/buffer/ring_buffer.py`
- `src/engine/decoder/dbc_decoder.py`
- `src/engine/router.py`

Odak soruları:
- Halka tampon: yarış durumu (race), GC baskısı, sınır kontrolü, taşma
- Disk tamponu: Zstandard + HMAC bütünlük doğrulaması doğru mu? Anahtar yönetimi
- DBC decoder: maske eşleşmesi, sinyal sınırı taşması, endianness
- Router: çerçeve yönlendirme hataları, sessiz çerçeve kaybı

Rapor: `docs/review/ASAMA-5-veri-yolu.md`

---

### AŞAMA 6 — Arayüz ve Başlatıcı (DÜŞÜK)

Dosyalar:
- `src/ui/desktop_app.py` (kapsamı düşük, ~%58 — boşlukları da belirt)
- `src/launcher/app.py`
- `src/main.py`

Odak soruları:
- pywebview köprüsü: JS → Python geçişinde girdi doğrulama var mı?
- Dosya yolu / komut enjeksiyonu riski
- Yetki yükseltme (privilege escalation) yolu
- Hata yönetimi: sessiz yutulan istisnalar

Rapor: `docs/review/ASAMA-6-ui.md`

---

### SON ADIM — ÖZET

6 rapor bitince `docs/review/OZET.md` yaz:
1. Tüm bulguların severity'ye göre birleşik tablosu
2. Çapraz kesişen hata desenleri (aynı hata birden fazla dosyada)
3. Öncelik sırası: ilk düzeltilecek 10 sorun + gerekçe
4. Genel olgunluk değerlendirmesi + tahmini düzeltme eforu

---

## HER RAPORUN ŞABLONU

```markdown
# ASAMA <N>: <Başlık>
Denetçi: <model adı>  |  Tarih: <tarih>
Kapsam: <okunan dosyalar ve satır sayıları>

## 1. Genel Değerlendirme
<3-5 cümle: bu dosyalar ne yapıyor, olgunluk seviyesi, en büyük risk>

## 2. Bulgular
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|

severity ∈ {KRİTİK, YÜKSEK, ORTA, DÜŞÜK, BİLGİ}

## 3. Severity Özeti
KRİTİK: N | YÜKSEK: N | ORTA: N | DÜŞÜK: N | BİLGİ: N

## 4. En Önemli 3 Bulgu (detay)
### B1: <başlık>
- **Kanıt** (satır numaralı kod alıntısı)
- **Neden sorun**
- **Somut senaryo** (adım adım: şu girdi şunu yapar)
- **Düzeltme** (net, uygulanabilir)
### B2, B3 aynı formatta

## 5. Doğrulanamayanlar
<bu aşamada bağlamda olmayan ama şüphelendiğin şeyler. Yoksa "yok" yaz.>

## 6. Sonraki Aşama İçin Not
<bu dosyalarda gördüğün ama başka dosyada olduğu için inceleyemediğin
 bağımlılıklar>
```

---

## KURALLAR

1. **Bulgu uydurma.** Yalnızca okuduğun kodla kanıtlayabildiğini yaz.
   Kanıtlayamıyorsan "incelenemedi" de. Spekülasyon yasak.
2. **Her bulgu `dosya:satır` referansı içermeli.** Uydurma satır numarası yasak.
3. **Severity gerekçelendir.** Somut senaryo olmadan KRİTİK deme.
4. **Kod değiştirme.** Bu görev sadece analiz ve rapor. Hiçbir `.py` dosyasını
   düzenleme, commit yapma, test çalıştırma.
5. Türkçe yaz. Teknik terimler İngilizce kalabilir (HMAC, TOCTOU, fail-closed).
6. Aşama bitmeden sonrakine geçme.
7. Çıktı şablona uysun; ekstra bölüm ekleme.

**Şimdi AŞAMA 1 ile başla.**

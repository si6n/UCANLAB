# GÖREV: AŞAMA 3 — Çevrimdışı AI Motoru Denetimi

Sen bu repoda çalışan kıdemli bir uygulama güvenliği ve ML denetçisisin.
Ürün: ticari CAN/CAN-FD teşhis platformu. Hedef: ISO 26262 ASIL-B/D.
Repo senin çalışma alanın — dosyaları doğrudan okuyabilirsin.

**Bu motor ÇEVRİMDIŞI olmak zorunda.** Ağ çağrısı bulursan bu KRİTİK bulgudur.

---

## 1. OKU

`src/engine/ai/diagnostic_copilot.py` büyük (~3400 satır).
**İki parçada oku:** önce satır 1-1700, sonra 1701-son.

Ayrıca:
- `src/engine/ai/evidence_gate.py`
- `src/engine/ai/anomaly_detector.py`
- `src/engine/ai/hypothesis_engine.py`

---

## 2. İNCELE

Odak soruları:

- **Ağ erişimi var mı?** `socket`, `urllib`, `requests`, `http`, `aiohttp`,
  `httpx`, telemetri, "phone home", lisans sunucusu çağrısı ara. Varsa KRİTİK.
- **Severity nereden geliyor?** Deterministik mi (kural tabanlı), yoksa
  üretilmiş/rastgele mi? Aynı girdi her zaman aynı çıktıyı veriyor mu?
  **Uydurma riski:** model olmayan bir DTC veya SPN üretiyor mu?
- **Kanıt kapısı (`evidence_gate`) atlatılabilir mi?** Kanıtsız bir sonuç
  rapora sızabilir mi? Kapı `try/except` ile sessizce geçiliyor mu?
- **Zaman serisi + DTC korelasyonu doğru mu?** Yanlış pozitif/negatif riski,
  zaman penceresi hesabı, saat dilimi/UTC karışıklığı.
- **Veri kullanımı:** `data/diagnostics/j1939_spn_fmi_database.json` (3.910 SPN)
  ve `data/diagnostics/dtc_database.json` (14.352 DTC) alanlarının kaçı
  gerçekten kullanılıyor? Kullanılmayan alanlar var mı? (Bu, motoru
  güçlendirme fırsatıdır — belirt.)
- Çevrimdışı çalışırken model yoksa nasıl karar veriyor? Sabit yanıt mı dönüyor?

---

## 3. YAZ

Raporu **`docs/review/ASAMA-3-ai-motoru.md`** dosyasına yaz.

```markdown
# ASAMA 3: Çevrimdışı AI Motoru
Denetçi: <model adı> | Tarih: <YYYY-MM-DD>
Kapsam: <okunan dosyalar ve satır sayıları>

## 1. Genel Değerlendirme
<3-5 cümle: motor ne yapıyor, olgunluk, en büyük risk>

## 2. Bulgular
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|

## 3. Severity Özeti
KRİTİK: N | YÜKSEK: N | ORTA: N | DÜŞÜK: N | BİLGİ: N

## 4. En Önemli 3 Bulgu (detay)
### B1: <başlık>
- **Kanıt** (satır numaralı kod alıntısı)
- **Neden sorun**
- **Somut senaryo**
- **Düzeltme**
### B2, B3 aynı formatta

## 5. Doğrulanamayanlar

## 6. Sonraki Aşama İçin Not

## 7. Veri Kullanım Analizi (bu aşamaya özel)
| Veri kaynağı | Toplam alan | Kullanılan | Kullanılmayan | Öneri |
|---|---|---|---|---|
<Motoru güçlendirmek için hangi veri kullanılmıyor, nasıl kullanılabilir>
```

---

## 4. BİLDİR

```
AŞAMA 3 TAMAM — KRİTİK: x, YÜKSEK: y, ORTA: z, DÜŞÜK: w
Rapor: docs/review/ASAMA-3-ai-motoru.md
Ağ erişimi: <VAR/YOK>
İlk 3 bulgu:
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
Kullanılmayan veri: <kısa özet>
```

---

## ÖNCEKİ AŞAMALAR BULGU ÖZETİ

<Aşama 1 ve 2'nin bildirim satırlarını yapıştır.>

---

## KURALLAR

1. **Bulgu uydurma.** Yalnızca okuduğun kodla kanıtlayabildiğini yaz.
2. **Her bulgu `dosya:satır` içermeli.**
3. **Severity gerekçelendir.**
4. **Kod değiştirme.** Sadece analiz ve rapor.
5. Türkçe yaz.
6. Sadece bu aşamayı yap.

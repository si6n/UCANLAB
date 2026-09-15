# GÖREV: AŞAMA 1 — TX Güvenlik Çekirdeği Denetimi

Sen bu repoda çalışan kıdemli bir uygulama güvenliği denetçisisin.
Ürün: ticari CAN/CAN-FD teşhis platformu. Hedef: ISO 26262 ASIL-B/D.
Bu denetim ticari yayın öncesi yapılıyor. Repo senin çalışma alanın —
dosyaları doğrudan okuyabilir, raporu doğrudan yazabilirsin.

---

## 1. OKU

Şu üç dosyayı baştan sona oku:

- `src/safety/gateway.py`
- `src/safety/estop.py`
- `src/safety/state_machine.py`

---

## 2. İNCELE

Odak soruları:

- `TxSafetyGateway` bypass edilebilir mi? 6 aşamalı politika atlanabilir mi?
- E-Stop token akışı: renderer token **üretebilir** veya **tüketebilir** mi?
  Üretici ve tüketici aynı modülde mi?
- Durum makinesi geçişleri atomik mi? TOCTOU penceresi var mı?
- Fail-closed mu fail-open mu? Her hata yolu hangi tarafa düşüyor?
  (`try/except` içinde sessizce `return True` var mı?)
- Aynı anda iki thread TX açabilir mi? Kilit (lock) kapsamı doğru mu?

Özellikle şu izleri ara:
- `legacy`, `bypass`, `skip`, `force`, `unsafe`, `override` geçen yerler
- `state_machine` içinde `auth_token` doğrulaması yapılıyor mu, atlanıyor mu
- E-Stop reset yetkisi kime verilmiş
- Rate budget / sliding window hesabında yarış durumu

---

## 3. YAZ

Raporu **`docs/review/ASAMA-1-tx-guvenlik.md`** dosyasına yaz.

```markdown
# ASAMA 1: TX Güvenlik Çekirdeği
Denetçi: <model adı> | Tarih: <YYYY-MM-DD>
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
 bağımlılıklar. Örn: "gateway.py:412, src/safety/e2e/validator.py'ye
 delege ediyor — orası incelenmeli.">
```

---

## 4. BİLDİR

Bitince bana **sadece şu tek satırı** yaz:

```
AŞAMA 1 TAMAM — KRİTİK: x, YÜKSEK: y, ORTA: z, DÜŞÜK: w
Rapor: docs/review/ASAMA-1-tx-guvenlik.md
İlk 3 bulgu:
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
```

---

## KURALLAR

1. **Bulgu uydurma.** Yalnızca okuduğun kodla kanıtlayabildiğini yaz.
   Kanıtlayamıyorsan "incelenemedi" de. Spekülasyon yasak.
2. **Her bulgu `dosya:satır` içermeli.** Uydurma satır numarası yasak.
3. **Severity gerekçelendir.** Somut senaryo olmadan KRİTİK deme.
4. **Kod değiştirme.** Sadece analiz ve rapor. Hiçbir `.py` dosyasını
   düzenleme, commit yapma, test çalıştırma.
5. Türkçe yaz. Teknik terimler İngilizce kalabilir (HMAC, TOCTOU, fail-closed).
6. Sadece bu aşamayı yap. Sonraki aşamalara geçme.

# GÖREV: AŞAMA 5 — Veri Yolu ve Tamponlar Denetimi

Sen bu repoda çalışan kıdemli bir sistem/eşzamanlılık denetçisisin.
Ürün: ticari CAN/CAN-FD teşhis platformu. Hedef: ISO 26262 ASIL-B/D.
Repo senin çalışma alanın — dosyaları doğrudan okuyabilirsin.

---

## 1. OKU

- `src/engine/buffer/rolling_disk.py`
- `src/engine/buffer/ring_buffer.py`
- `src/engine/decoder/dbc_decoder.py`
- `src/engine/router.py`

---

## 2. İNCELE

Odak soruları:

**Halka tampon (ring_buffer.py):**
- Üretici/tüketici yarış durumu: kilit (lock) kapsamı doğru mu?
  Kilit kilitsiz (lock-free) yazma varsa bellek sıralaması (memory ordering) doğru mu?
- Tam dolu / tam boş ayrımı (`full` vs `empty`) doğru mu?
- Sınır kontrolü: indeks taşması, modulo hatası
- GC baskısı: döngü içinde nesne üretimi, kopyalama
- Çerçeve kaybı sessiz mi (sayaç/drop metrik var mı)?

**Disk tamponu (rolling_disk.py):**
- Zstandard sıkıştırma + HMAC bütünlük doğrulaması doğru mu?
- HMAC anahtarı nereden geliyor? Sabit/dosyada mı, güvenli mi?
- Yazma sırasında güç kesilirse bozuk dosya okunur mu? Doğrulama var mı?
- Disk dolduğunda ne oluyor? Sessiz kayıp?
- Döndürme (rotation) sırasında yarış durumu

**DBC decoder (dbc_decoder.py):**
- Maske eşleşmesi: `mask & can_id` hesabı doğru mu? Genişletilmiş (29-bit) ID?
- Sinyal çıkarma: bit offset, uzunluk, endianness (Intel/Motorola)
- Sınır kontrolü: bit uzunluğu çerçeveyi aşıyor mu? Taşma?
- Faktör/offset hesabı, fiziksel değer aralığı
- Bozuk DBC dosyası yüklenirse ne oluyor?

**Router (router.py):**
- Çerçeve yönlendirme: filtre doğru mu, çerçeve kaybı var mı?
- Kayıtlı olmayan ID gelirse sessizce düşüyor mu?
- Eşzamanlı kayıt/silme (subscribe/unsubscribe) sırasında yarış

---

## 3. YAZ

Raporu **`docs/review/ASAMA-5-veri-yolu.md`** dosyasına yaz.

```markdown
# ASAMA 5: Veri Yolu ve Tamponlar
Denetçi: <model adı> | Tarih: <YYYY-MM-DD>
Kapsam: <okunan dosyalar ve satır sayıları>

## 1. Genel Değerlendirme

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
```

---

## 4. BİLDİR

```
AŞAMA 5 TAMAM — KRİTİK: x, YÜKSEK: y, ORTA: z, DÜŞÜK: w
Rapor: docs/review/ASAMA-5-veri-yolu.md
İlk 3 bulgu:
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
```

---

## ÖNCEKİ AŞAMALAR BULGU ÖZETİ

<Aşama 1-4 bildirimlerini yapıştır.>

---

## KURALLAR

1. **Bulgu uydurma.** Yalnızca okuduğun kodla kanıtlayabildiğini yaz.
2. **Her bulgu `dosya:satır` içermeli.**
3. **Severity gerekçelendir.**
4. **Kod değiştirme.** Sadece analiz ve rapor.
5. Türkçe yaz.
6. Sadece bu aşamayı yap.

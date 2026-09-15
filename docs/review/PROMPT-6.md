# GÖREV: AŞAMA 6 — Arayüz ve Başlatıcı Denetimi

Sen bu repoda çalışan kıdemli bir masaüstü uygulama güvenliği denetçisisin.
Ürün: ticari CAN/CAN-FD teşhis platformu (pywebview + JS arayüz).
Repo senin çalışma alanın — dosyaları doğrudan okuyabilirsin.

---

## 1. OKU

- `src/ui/desktop_app.py` (büyük, ~3100 satır — kapsamı düşük, boşlukları belirt)
- `src/launcher/app.py`
- `src/main.py`

---

## 2. İNCELE

Odak soruları:

**pywebview köprüsü (desktop_app.py):**
- JS → Python geçişinde **girdi doğrulaması** var mı? Renderer'dan gelen
  her argüman tip/aralık kontrolünden geçiyor mu?
- **Dosya yolu enjeksiyonu:** renderer dosya yolu belirleyebiliyor mu?
  `..` ile dizin dışına çıkma (path traversal), mutlak yol kabul ediliyor mu?
- **Komut enjeksiyonu:** `subprocess`, `os.system`, `shell=True` kullanımı
  var mı? Argümanlar kaçışlanıyor mu?
- **Yetki yükseltme:** UI'dan motor kontrolü (TX, flash, E-Stop reset)
  doğrudan çağrılabiliyor mu? Araya onay katmanı girmeli mi?
- Hassas veri sızıntısı: lisans anahtarı, kullanıcı yolu, anahtar loglanıyor mu?

**Launcher (launcher/app.py):**
- Güncelleme mekanizması varsa: imza doğrulaması, indirme kaynağı
- Süreç başlatma: argümanlar nereden geliyor?
- Yetki yükseltme (UAC / sudo) akışı

**main.py:**
- Başlatma sırası: güvenlik kapıları UI'dan önce mi kuruluyor?
- Hata yönetimi: sessizce yutulan istisnalar
- Kayıt/log: hassas veri var mı?

---

## 3. YAZ

Raporu **`docs/review/ASAMA-6-ui.md`** dosyasına yaz.

```markdown
# ASAMA 6: Arayüz ve Başlatıcı
Denetçi: <model adı> | Tarih: <YYYY-MM-DD>
Kapsam: <okunan dosyalar ve satır sayıları, artı okunamayan bölümler>

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
<desktop_app.py büyük — hangi bölümleri okuyamadın, neden>

## 6. Kapsam Boşlukları
<test kapsamı %58 — hangi fonksiyonlar test edilmemiş görünüyor>
```

---

## 4. BİLDİR

```
AŞAMA 6 TAMAM — KRİTİK: x, YÜKSEK: y, ORTA: z, DÜŞÜK: w
Rapor: docs/review/ASAMA-6-ui.md
İlk 3 bulgu:
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
```

---

## ÖNCEKİ AŞAMALAR BULGU ÖZETİ

<Aşama 1-5 bildirimlerini yapıştır.>

---

## KURALLAR

1. **Bulgu uydurma.** Yalnızca okuduğun kodla kanıtlayabildiğini yaz.
2. **Her bulgu `dosya:satır` içermeli.**
3. **Severity gerekçelendir.**
4. **Kod değiştirme.** Sadece analiz ve rapor.
5. Türkçe yaz.
6. Sadece bu aşamayı yap.

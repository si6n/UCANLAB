# GÖREV: AŞAMA 2 — Lisans ve Kimlik Doğrulama Denetimi

Sen bu repoda çalışan kıdemli bir uygulama güvenliği denetçisisin.
Ürün: ticari CAN/CAN-FD teşhis platformu. Hedef: ISO 26262 ASIL-B/D.
Repo senin çalışma alanın — dosyaları doğrudan okuyabilirsin.

---

## 1. OKU

- `src/security/license/validator.py`
- `src/security/hwid/collector.py`
- `src/security/anti_tamper/guard.py`
- `src/launcher/auth.py`

---

## 2. İNCELE

Odak soruları:

- **Donanım kilidi atlatılabilir mi?** HWID toplanamazsa ne oluyor?
  Sabit sentinel değer var mı (`UNKNOWN_CPU`, `UNKNOWN_DISK`, `FALLBACK-*`,
  boş string, `None`)? İki farklı makine aynı fingerprint'e düşebilir mi?
- **Karşılaştırma doğru mu?** Case-sensitivity, tip karışıklığı (`str` vs
  `bytes`), kısmi eşleşme (`in` vs `==`), boş/None değer geçiyor mu?
- **Ed25519 imza doğrulaması doğru mu?** Payload canonical mı? İmza
  doğrulanmadan önce payload kullanılıyor mu? Algoritma karışıklığı riski var mı?
- **Anti-clock-rollback** koruması yeterli mi? Sistem saati geri alınırsa
  süresi geçmiş lisans çalışır mı?
- **Fail-open riski:** lisans yok / bozuk / süresi geçmiş ise uygulama
  başlıyor mu, yoksa duruyor mu?
- `launcher/auth.py`: kimlik doğrulama akışında TOCTOU, sabit karşılaştırma
  (`constant-time` şart mı), zayıf hash

---

## 3. YAZ

Raporu **`docs/review/ASAMA-2-lisans.md`** dosyasına yaz.

```markdown
# ASAMA 2: Lisans ve Kimlik Doğrulama
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

## 6. Sonraki Aşama İçin Not
```

---

## 4. BİLDİR

```
AŞAMA 2 TAMAM — KRİTİK: x, YÜKSEK: y, ORTA: z, DÜŞÜK: w
Rapor: docs/review/ASAMA-2-lisans.md
İlk 3 bulgu:
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
```

---

## ÖNCEKİ AŞAMA (Aşama 1) BULGU ÖZETİ

<Aşama 1'in bildirim satırını buraya yapıştır. Çok kısaysa, o aşamanın
raporundaki "## 2. Bulgular" tablosunu da ekleyebilirsin. Tam raporu ve
kodu YAPMA — sadece bulgu özeti.>

---

## KURALLAR

1. **Bulgu uydurma.** Yalnızca okuduğun kodla kanıtlayabildiğini yaz.
2. **Her bulgu `dosya:satır` içermeli.** Uydurma satır numarası yasak.
3. **Severity gerekçelendir.** Somut senaryo olmadan KRİTİK deme.
4. **Kod değiştirme.** Sadece analiz ve rapor.
5. Türkçe yaz. Teknik terimler İngilizce kalabilir.
6. Sadece bu aşamayı yap.

# GÖREV: AŞAMA 4 — Protokol Katmanları Denetimi

Sen bu repoda çalışan kıdemli bir gömülü sistem ve protokol denetçisisin.
Ürün: ticari CAN/CAN-FD teşhis platformu. Hedef: ISO 26262 ASIL-B/D.
Repo senin çalışma alanın — dosyaları doğrudan okuyabilirsin.

---

## 1. OKU

- `src/protocols/j1939/transport.py`
- `src/protocols/uds/isotp.py`
- `src/protocols/uds/flasher.py`
- `src/protocols/uds/client.py`

---

## 2. İNCELE

Odak soruları:

**J1939 TP (transport.py):**
- BAM ve RTS/CTS akışları: çok paketli mesaj tamponu sınırsız büyüyebilir mi?
- Yarım kalan oturum (peer mesajı keserse) ne oluyor? Tampon sızar mı?
- Sayaç (sequence number) doğrulaması var mı? Taşma?
- Aynı anda iki peer'den mesaj gelirse çakışma?
- Zaman aşımı (timeout) yönetimi — asılı oturum temizliği

**ISO-TP (isotp.py):**
- Sequence number doğrulaması, FF/CF/FC durum makinesi
- Blok boyutu (BS) ve STmin doğrulaması, geçersiz değer
- Tampon sınırı: bildirilen uzunluk (`FF` içindeki) gerçek ayırmadan büyükse?
- Timeout ve N_As / N_Bs / N_Cr yönetimi
- Reassembly'de bellek ayırma saldırısı (4095 byte sınırı)

**Flasher (flasher.py):**
- Firmware imza/CRC doğrulaması var mı? Doğrulama **yüklemeden önce** mi?
- Güç kesilirse / yarıda kesilirse ne oluyor? Geri dönüş (rollback) var mı?
- Blok sırası, adres sınırı, taşma
- `0x34/0x36/0x37` (RequestDownload/TransferData/RequestTransferExit) akışı

**UDS (client.py):**
- NRC (negative response code) yönetimi — hangi NRC'ler ele alınmış?
- Oturum (session) ve SecurityAccess atlatma: seed/key akışı doğru mu?
- Sabit bekleme (`P2*`) ve tekrar deneme sınırı
- Servis kimliği doğrulaması: hatalı SID sessizce düşüyor mu?

---

## 3. YAZ

Raporu **`docs/review/ASAMA-4-protokoller.md`** dosyasına yaz.

```markdown
# ASAMA 4: Protokol Katmanları
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
AŞAMA 4 TAMAM — KRİTİK: x, YÜKSEK: y, ORTA: z, DÜŞÜK: w
Rapor: docs/review/ASAMA-4-protokoller.md
İlk 3 bulgu:
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
- <dosya:satır> <kısa başlık>
```

---

## ÖNCEKİ AŞAMALAR BULGU ÖZETİ

<Aşama 1-3 bildirimlerini yapıştır.>

---

## KURALLAR

1. **Bulgu uydurma.** Yalnızca okuduğun kodla kanıtlayabildiğini yaz.
2. **Her bulgu `dosya:satır` içermeli.**
3. **Severity gerekçelendir.**
4. **Kod değiştirme.** Sadece analiz ve rapor.
5. Türkçe yaz.
6. Sadece bu aşamayı yap.

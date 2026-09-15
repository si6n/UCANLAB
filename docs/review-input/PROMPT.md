# UCANLAB — Aşamalı Kod Review Promptu

Bu klasör, ana projenin güçlü bir model ile **aşamalı, token-verimli** review edilmesi için hazırlanmıştır.

## Dosyalar

```
review-input/
  ASAMA-1-PAKET.txt   ~31K token   TX güvenlik çekirdeği (gateway, estop, state_machine)
  ASAMA-2-PAKET.txt   ~12K token   Lisans, HWID, anti-tamper, auth
  ASAMA-3a-PAKET.txt  ~27K token   AI motoru, satır 1-1700
  ASAMA-3b-PAKET.txt  ~26K token   AI motoru, satır 1701-3394
  ASAMA-4-PAKET.txt   ~62K token   J1939 TP, ISO-TP, flasher, UDS
  ASAMA-5-PAKET.txt   ~23K token   rolling_disk, ring_buffer, DBC, router
  ASAMA-6-PAKET.txt   ~52K token   UI, launcher, main
```

Her paket **satır numaralı** kaynak içerir. Model ayrıca dosya açmak zorunda değildir;
`dosya:satır` referansı verebilir.

## Token ekonomisi

| Yöntem | Maliyet | Not |
|---|---|---|
| Tüm kaynağı tek oturumda | ~1.550.000 token | Her turda bağlam yeniden ödenir |
| **Aşama başına ayrı oturum** | **~249.000 token** | **6x tasarruf** |
| Paketleri repoda tutup model okutmak | ~0 girdi | En ekonomik |

226.725 token'lık tam kaynak çoğu modelin bağlam limitini aşar — bölünmesi zorunludur.

## PROMPT (kopyala-yapıştır)

```
Sen kıdemli bir uygulama güvenliği ve kod kalitesi denetçisisin.
ISO 26262 ASIL-B/D hedefli, ticari bir CAN/CAN-FD teşhis ürününü denetleyeceksin.

=== BU AŞAMA: AŞAMA 1 — TX Güvenlik Çekirdeği ===

# KURALLAR
1. SADECE sana verilen dosyaları incele. Başka dosya okuma, komut çalıştırma,
   varsayım yapma.
2. BULGU UYDURMA. Yalnızca verilen kodla kanıtlayabildiğini yaz.
   Kanıtlayamıyorsan "incelenemedi" de. Spekülasyon yasak.
3. Her bulgu şu formatta:
   | # | dosya:satır | severity | sorun | sömürü/başarısızlık senaryosu | düzeltme |
   severity ∈ {KRİTİK, YÜKSEK, ORTA, DÜŞÜK, BİLGİ}
4. Severity gerekçelendir. Somut senaryo olmadan KRİTİK deme.
5. Kod yazma, dosya oluşturma, test çalıştırma. Sadece analiz.
6. Türkçe yaz (teknik terimler İngilizce kalabilir: HMAC, TOCTOU, fail-closed).
7. Çıktıyı AŞAĞIDAKİ ŞABLONA göre ver, başka şey ekleme.

# ODAK SORULARI
- TxSafetyGateway bypass edilebilir mi? 6 aşama atlanabilir mi?
- E-Stop token akışı: renderer token üretebilir/tüketebilir mi?
- Durum makinesi geçişleri atomik mi? TOCTOU penceresi var mı?
- Fail-closed mu fail-open mu? Her hata yolu hangi tarafa düşüyor?
- Aynı anda iki thread TX açabilir mi?

# ÇIKTI ŞABLONU

# ASAMA 1: TX Güvenlik Çekirdeği
Denetçi: <model adı>  |  Tarih: <tarih>
Kapsam: <dosyalar ve satır sayıları>

## 1. Genel Değerlendirme
<3-5 cümle: bu dosyalar ne yapıyor, olgunluk seviyesi, en büyük risk>

## 2. Bulgular
| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|

## 3. Severity Özeti
KRİTİK: N  |  YÜKSEK: N  |  ORTA: N  |  DÜŞÜK: N  |  BİLGİ: N

## 4. En Önemli 3 Bulgu (detay)
### B1: <başlık>
- **Kanıt** (kod alıntısı):
```python
<satır numaralı kod>
```
- **Neden sorun:** <açıklama>
- **Somut senaryo:** <adım adım>
- **Düzeltme:** <net, uygulanabilir>
### B2, B3 aynı formatta

## 5. Doğrulanamayanlar
<bağlamda olmayan ama şüphelendiğin şeyler. Yoksa "yok" yaz.>

## 6. Sonraki Aşama İçin Not
<bu dosyalarda gördüğün ama başka dosyada olduğu için inceleyemediğin
 bağımlılıklar.>

=== AŞAĞIDA İNCELENECEK DOSYALAR ===
<ASAMA-1-PAKET.txt içeriğini buraya yapıştır>
```

## Diğer aşamalar

Sadece başlık + odak soruları + paket değişir:

| Aşama | Başlık | Paket |
|---|---|---|
| 2 | Lisans ve Kimlik Doğrulama | ASAMA-2 |
| 3a | AI Motoru (çekirdek) | ASAMA-3a |
| 3b | AI Motoru (kural tabanı) | ASAMA-3b |
| 4 | Protokoller (J1939 TP, ISO-TP, UDS) | ASAMA-4 |
| 5 | Veri Yolu ve Tamponlar | ASAMA-5 |
| 6 | UI ve Başlatıcı | ASAMA-6 |

**Aşama 2+ için başa önceki özeti ekle:**
```
ÖNCEKİ AŞAMA BULGU ÖZETİ:
- KRİTİK: <dosya:satır> <kısa açıklama>
- YÜKSEK: <dosya:satır> <kısa açıklama>
```

## Odak soruları (diğer aşamalar)

**Aşama 2:**
- Lisans donanım kilidi atlatılabilir mi? (sentinel, tip karışıklığı, boş değer)
- Ed25519 doğrulama doğru mu? Anti-clock-rollback yeterli mi?
- HWID deterministik mi? Lisans yoksa fail-open riski var mı?

**Aşama 3a/3b:**
- Ağ erişimi var mı? (`socket`, `urllib`, `requests` import edilmiş mi?)
- Severity deterministik mi, uydurma riski var mı?
- Kanıt kapısı atlatılabilir mi? DB alanlarının kaçı kullanılıyor?

**Aşama 4:**
- TP durum makinesi: sınırsız bellek büyümesi, DoS?
- ISO-TP: sequence/sayaç doğrulaması, timeout, buffer sınırı
- Flasher: firmware doğrulama, yarıda kesilme, geri dönüş
- UDS: NRC yönetimi, oturum/security access bypass

**Aşama 5:**
- Halka tampon: yarış durumu, GC baskısı, sınır kontrolü
- Disk tamponu: Zstandard + HMAC doğru mu?
- DBC decoder: maske eşleşmesi, sinyal sınırı taşması

**Aşama 6:**
- pywebview köprüsü, güvenlik sızıntısı, girdi doğrulama

## Son adım: OZET.md

```
Sana 6 aşamalık review raporunun BULGU TABLOLARI veriliyor. Şunları üret:
1. Tüm bulguları severity'ye göre birleşik tablo
2. Çapraz kesişen sorunlar (aynı hata deseni birden fazla dosyada)
3. Öncelik sırası: ilk düzeltilecek 10 sorun ve neden
4. Genel olgunluk değerlendirmesi + tahmini düzeltme eforu

<6 raporun "## 2. Bulgular" tablolarını buraya yapıştır>
```

## Çıktı konumu

Raporlar: `obsidian-vault/02-Diagnostics/review/ASAMA-N-<konu>.md`
(bu klasör .gitignore'dadır — kişisel not alanı)

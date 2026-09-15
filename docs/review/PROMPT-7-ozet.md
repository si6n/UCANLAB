# GÖREV: SENTEZ — 6 Aşamalık Denetimin Birleşik Özeti

6 aşamalık güvenlik denetimi tamamlandı. Raporlar `docs/review/` altında:

- `ASAMA-1-tx-guvenlik.md`
- `ASAMA-2-lisans.md`
- `ASAMA-3-ai-motoru.md`
- `ASAMA-4-protokoller.md`
- `ASAMA-5-veri-yolu.md`
- `ASAMA-6-ui.md`

---

## 1. OKU

Altı raporun **tamamını** oku (kaynak kod okumana gerek yok).

---

## 2. ÜRET

**`docs/review/OZET.md`** dosyasına yaz:

### Bölüm 1 — Birleşik Bulgu Tablosu
Tüm aşamaların bulgularını tek tabloda, severity'ye göre sıralı (KRİTİK önce).

```markdown
# BİRLEŞİK DENETİM ÖZETİ
Tarih: <YYYY-MM-DD> | Aşama sayısı: 6
Toplam bulgu: N (KRİTİK: x, YÜKSEK: y, ORTA: z, DÜŞÜK: w, BİLGİ: v)

## 1. Tüm Bulgular
| # | aşama | dosya:satır | severity | sorun | düzeltme |
|---|---|---|---|---|---|
```

### Bölüm 2 — Çapraz Kesişen Desenler
Aynı hata deseni birden fazla dosyada tekrar ediyor mu?
Örnek sorular:
- Yalnızca test edilmiş ama kanıtlanmamış varsayımlar her yerde mi?
- `try/except` ile sessiz yutma kaç yerde?
- Fail-open varsayılanları kaç modülde?
- Girdi doğrulaması eksikliği hangi katmanlarda ortak?

```markdown
## 2. Çapraz Kesişen Desenler
### Desen A: <ad>
Görüldüğü yerler: <dosya:satır listesi>
Kök neden: <tek bir altyapı/düzen eksikliği mi?>
Tek düzeltme noktası: <nerede çözülürse hepsi kapanır>
```

### Bölüm 3 — Öncelik Sırası (İlk 10)

```markdown
## 3. İlk Düzeltilecek 10 Sorun
| Sıra | Bulgu | Neden şimdi | Efor | Etki |
|---|---|---|---|---|
```
Gerekçe: hangi sırada düzeltilirse risk en hızlı düşer. Güvenlik açığı >
veri bütünlüğü > yanlış teşhis > kozmetik.

### Bölüm 4 — Olgunluk ve Efor

```markdown
## 4. Genel Değerlendirme
**Olgunluk:** <1-5 arası puan + gerekçe>
**Güçlü yönler:** <neler iyi yapılmış>
**En büyük risk:** <tek cümle>
**Tahmini efor:** <kişi-gün, kritik+ yüksek bulgular için>
**Ticari yayın için engel mi:** <evet/hayır + gerekçe>
```

### Bölüm 5 — Doğrulanamayanlar (birleşik)
Altı raporda "incelenemedi" denilen her şeyi topla. Ne ek bilgi gerekiyor?

---

## 3. BİLDİR

```
SENTEZ TAMAM
Toplam bulgu: N (KRİTİK: x, YÜKSEK: y, ORTA: z)
Olgunluk: N/5
Ticari yayın engeli: <VAR/YOK>
İlk 3 öncelik:
1. <dosya:satır> <başlık>
2. <dosya:satır> <başlık>
3. <dosya:satır> <başlık>
Rapor: docs/review/OZET.md
```

---

## KURALLAR

1. **Yeni bulgu ekleme.** Sadece altı raporda olanları sentezle. Kaynak kod okuma.
2. **Her satırda rapor referansı olsun** — hangi aşamadan geldiği belli olsun.
3. Çelişki varsa (iki aşama aynı dosyaya farklı şey diyor) **belirt**, çözmeye çalışma.
4. Türkçe yaz.
5. Kod değiştirme.

# Çevrimdışı Teşhis Copilot'u — Mimari, Veri Kaynakları, Genişletme

> Kapsam: `src/engine/ai/` altındaki **yapılandırılmış cevap yolu** (copilot
> upgrade, 2026-10). Eski motorun (kural senaryoları, `explain_can_packet`,
> kullanıcı kartı, diyalog) ayrıntısı için `docs/OFFLINE_AI_ENGINE.md`.

## 1. Değişmez kurallar

| Kural | Nasıl korunuyor |
|---|---|
| Tamamen çevrimdışı: bulut LLM yok, API anahtarı yok, ağ çağrısı yok | `tests/safety/test_ai_tx_isolation.py` (AST import taraması `src/engine/ai/**` altındaki **tüm** dosyaları kapsar, yeni modüller dahil) |
| Copilot ölçüm/değer **uydurmaz**; veri yoksa "veri yok" der | `tests/unit/test_copilot_no_fabrication.py` (her sayı girdiye veya atıf yapılan kayda izlenir, her atıf gerçek kayda çözülür) |
| Copilot CAN hattına yazamaz, TX yolu açamaz | Yeni modüllerde yazma/TX çağrısı yok (AST testi); köprü uç noktası `read` sınıfında; UI kartı yalnız okur |
| Deterministik | Aynı girdi → aynı `to_dict()` (test); saat/rastgelelik kullanılmaz |

## 2. Akış

```
serbest metin ─┐
DTC / SPN-FMI ─┤                       ┌──────────────────────────┐
canlı telemetri┼─► query_understanding ─► copilot_reasoner ─► copilot_answer ─► StructuredAnswer
DM1 (PGN 65226)┘   (TR/EN, yazım hatası,   (kod gerçekleri, eşik       (6 bölüm + güvenlik bandı,
                    DM1 çözümü, birimler)   değerlendirmesi, kök neden    TR/EN, kaynak id'leri)
                              │             sıralaması, aciliyet,
                              ▼             güvenlik, eksik veri)
                       KnowledgeBase  ◄───────────┘
                (tembel, önbellekli, indeksli; tek veri erişim katmanı)
```

Giriş noktaları:

* Python: `answer_query(text, dtcs=…, telemetry=…, dm1=…, vehicle_make=…, language=…)`
  veya `AiDiagnosticCopilot().answer(...)` → `StructuredAnswer` (`to_dict()`, `to_markdown()`).
* Masaüstü köprüsü: `ask_copilot_structured(query, language)` (salt okuma) ve
  `get_diagnostic_analysis()` çıktısındaki ek `structured_answer` anahtarı (oturumdaki
  AKTİF kodlar + ölçülmüş telemetri).
* UI: Uzman masası → Teşhis asistanı → "Copilot'a sorun" kartı
  (`src/ui/frontend/src/components/workbench/CopilotAnswer.tsx`).

## 3. Modüller

| Dosya | Sorumluluk |
|---|---|
| `knowledge_base.py` | `KnowledgeBase`: 23 kaynağın tek erişim noktası. Kurulum hiçbir şey okumaz; her kaynak ilk kullanımda yüklenir, kilitle korunur, önbellekte kalır. Kırık kaynak copilot'u düşürmez (`source_unavailable`). Her sonuç `Lookup(found, source, key, record, reason)`; `ref = "kaynak#anahtar"`. `resolve_ref()` basılan atıfı yeniden doğrular. |
| `query_understanding.py` | `parse_query`: dil tespiti; DTC (`PO101`→`P0101`), SPN/FMI yazımları, PGN/PID; DM1 baytlarını SAE J1939-73 düzenine göre çözer (lambalar, SPN/FMI/OC, CM=1 uyarısı); semptom eşleştirme (Türkçe katlama, ek/çekim toleransı, Damerau ≤1/≤2 yazım hatası); metinden ölçüm okuma (°F→°C, psi/kPa→bar, MΩ→kΩ). Birimsiz değer `unit_assumed=True`; uyumsuz birim/NaN/Inf **reddedilir**. Sinyal adı geçmeyen gösterge değeri ("hararet yapıyor, göstergede 112 derece"): semptom tek bir ölçülebilir sinyale bağlıysa (`_SYMPTOM_SIGNAL`) ve metinde **açık birimli**, o sinyale çevrilebilen **tek** sayı varsa o sinyalin okuması olur (`origin="text_context"`, not: `context_reading:<sinyal><-<semptom>`); birimsiz, uyumsuz veya birden fazla aday varsa bağlanmaz. Yazım hatası toleransı Türkçe olumsuzluk ekini (`-mıyor/-maz/-madı`) asla aşmaz: "dönüyor" ≠ "dönmüyor". Bir semptomun eşleşen kelimeleri başka bir semptomun daha uzun ve daha yüksek puanlı eşleşmesinin alt kümesiyse o semptom düşer: "akü şarj olmuyor" 12 V aküdür, "şarj olmuyor" tek başına adlandıracağı EV şarj portu değil. |
| `copilot_reasoner.py` | Kod gerçekleri (DTC + OEM katmanı + J1939 + FMI), telemetri bulguları (`telemetry_thresholds`, UN R100 izolasyon Ω/V), kök neden sıralaması, aciliyet, güvenlik kategorileri, eksik ölçümler, NHTSA. |
| `copilot_answer.py` | `StructuredAnswer` + TR/EN metinler + Markdown; sabit şablonlar `template:*` olarak işaretlenir. |
| `local_search.py` | **Opsiyonel** BM25 ipucu katmanı (yalnız hiçbir kod/semptom eşleşmediğinde, "teşhis değil" etiketiyle). Kapatma: `CopilotOptions(local_search=False)` veya `UCANLAB_COPILOT_LOCAL_SEARCH=0`. Çekirdek onsuz çalışır (test). |

### 3.1 Puanlama (deterministik, belgeli)

| Kanıt | Puan |
|---|---|
| Düğümün beklediği kod aktif | +3.0 (her kod) |
| Düğümün beyan ettiği FMI aktif FMI ile aynı | +0.5 |
| FMI ailesi (J1939-73): 0/1/15–18 "veri geçerli" → fiziksel neden +1, sensör nedeni −1; 3–6 elektriksel → tersi | ±1.0 |
| Kanıt sinyali eşik dışı ölçüldü / normal ölçüldü | +1.5 / −1.0 |
| Çelişen sinyal makul ölçüldü / sensör sınırında (ör. ECT −40 °C) | −1.5 / +1.0 |
| Yalnız şikâyetten ulaşılan aday kod / şikâyet aktif kodu doğruluyor | +1.0 / +0.5 |
| Şikâyetin tarif ettiği okuma (hararet → CoolantTemp, yağ lambası → EngineOilPressure) düğümün kanıt sinyali: gerçek (tehlikeli) arıza sensör arızasından önce | +0.5 |
| Soru cevabı alt sistemi/kodu destekliyor / çürütüyor (§3.1.1) | +1.5 / −1.5 |
| Grafta düğüm yok → kayıttaki temiz nedenler (dolgu metinler elenir) | +2.0 − 0.05·i |

Neden metni olamayan hasat artıkları hem kayıt nedenlerinden hem graf düğüm
başlıklarından elenir (`_is_harvest_residue`): numaralı parça listesi
("1. 20-Way TCM … 2. …"), FMI tablo dökümü ("… FMI 1, 4, 17, 18: …"), prosedür
metni ("Key off", "Note:", "refer to") ve cümle ortasında kesilmiş parça
("… power supply to"). Veri değişmez; yalnız cevapta neden olarak gösterilmez, 220 karakteri aşan yapıştırılmış paragraflar, bileşen tanımları ("O2 Sensor : Measures …") ve ok işaretli FMI test adımları da elenir.

Şikâyet eşleşti ama ne graf ne kod kaydı neden veriyorsa (şikâyet-only
sorguların %43'ü) neden uydurulmaz: semptom kaydının `subsystems` alanındaki ilk
3 alt sistem `kind=area` olarak, "önce burayı kontrol edin" etiketiyle listelenir.
Bu satırlarda yüzde gösterilmez (`likelihood=0`), güven her zaman düşüktür ve
kaynak `canonical_symptoms#<id>` kaydıdır. Gerçek bir neden bulunduğunda `area`
satırı hiç eklenmez. Alt sistem adları kayıtta Türkçedir; İngilizce cevap
`subsystem_labels_en.json` çevirisini gösterir (`build_subsystem_labels.py`).

Sayılar semptom eşleşmesinde korunur: "3. silindir tekleme" →
`misfire-cylinder-3`, "bank 2 fakir" → `lean-condition-bank2`. Sayılar 4
karakterden kısa olduğu için yalnız tam eşleşir (bulanık eşleşme yok). Kod
biçimli belirteçler (P0301 vb.) semptom eşleşmesinden önce yine çıkarılır.

Kod okunmamış bir şikâyette üçten az aday varsa semptomun kendi alt sistemleri de
eklenir; tek bir alakasız düğüm tek başına "en olası neden" olarak kalmaz.

Puanlar listelenen adaylar arasında softmax ile **göreli** yüzdeye çevrilir;
bütün adaylar aynı puandaysa yüzde gösterilmez (`likelihood=0`) ve özet "Eşit
ağırlıklı adaylar (veri sıralamaya yetmiyor)" der — eşit bölünmüş yüzde ölçülmüş
bir olasılık gibi okunur.
cevap bunun kesin olasılık olmadığını açıkça yazar. Güven: kod + telemetri +
çelişki yok → yüksek; kod (graf) veya telemetri → orta; diğerleri → düşük.
Eşitlikte en ağır kodun nedeni önce gelir.

### 3.1.1 Sorular ve cevaplar (`symptom_checks`)

Cevap, eşleşen şikâyetin `initial_questions` sorularını **cevaplanabilir** olarak
listeler (`checks` alanı; markdown'da 4. bölümün altında "Sorular"). Cevaplar
aynı sorguyla geri gönderilir (`answer_query(..., answers={"<symptom_id>.q<n>": …})`,
köprü: `ask_copilot_structured(query, language, answers)`); arayüz Evet / Hayır /
Bilmiyorum düğmeleri ve ölçüm alanı gösterir. Canlı oturum cevabında (kodlar okundu, cümle yazılmadı) da sorular cevaplanabilir; köprü boş sorguyu yalnız `answers` doluysa kabul eder.

* Etkiler küratörlüdür: `scripts/copilot_data/build_symptom_checks.py` →
  `data/diagnostics/symptom_checks.json` (147 semptom, 270 soru; numaralı aileler — silindir tekleme, enjektör/bobin devresi, vites oranı — tek şablondan üretilir). Bir cevap yalnız
  **o semptomun kendi** alt sistemlerini (`subsystems` indeksi) veya kendi aday
  kodlarını (`candidate_dtcs`) öne alır (+1.5) ya da geriye iter (−1.5, "çelişen"
  olarak gösterilir) ve tek cümlelik sabit bir açıklama taşır. Doğrulayıcı ve
  `test_copilot_checks.py` hedeflerin semptoma ait olduğunu denetler.
* Öne alınan alt sistem yoksa `area` satırı olarak eklenir; kodlu bir hedef
  (ör. `P0217`) graf nedenini doğrudan yükseltir. Cevapla desteklenen ve
  çelişkisi olmayan aday "orta" güven alır.
* Ölçüm soruları bantlarla değerlendirilir (`min ≤ v < max`; bantlar boşluksuz).
  Metinde sorunun kendi anahtar kelimesi ve tek bir birimli sayı varsa
  ("uyku akımı 320 mA") cevap metinden alınır; iki aday sayı varsa alınmaz.
* Aktif bir kod, `candidate_dtcs` alanında o kodu sayan ve sorusu olan
  semptomların sorularını da getirir (P0301 → `misfire-cylinder-1`: bobin/buji
  değiştirme testi). Bu semptom operatörün şikâyeti olarak raporlanmaz
  (`understood.symptoms` boş kalır); cevabı yalnız soru etkisi olarak işlenir ve
  aktif kodun graf nedeninin önüne geçmez. OBD kodlarında (P/B/C/U) önce genel
  alan semptomları alınır; denizcilik / EV / ağır vasıta semptomu yalnız genel
  semptom yoksa gelir (bir otomobilin U0100'ü tekne emniyet kordonunu sormaz).
* Özet, cevaplanmamış sorular arasından listelenen adayları en çok etkileyeni
  "Önce şu soruyu cevaplayın: …" diye gösterir. Önde birden fazla aday aynı
  puandaysa özet "En olası neden" demez, "Önde, eşit ağırlıkta: …" der.
* "Bilmiyorum", tanınmayan değer veya bu sorguda eşleşmeyen semptomun cevabı
  hiçbir şeyi değiştirmez (`answer_ignored:<key>` notu). Cevaplar aciliyeti
  düşürmez.

### 3.1.2 Ortak kök neden (`reasoning_rules`)

Birden çok kod aynı anda aktifse, hepsini tek bir ortak arıza açıklayabilir
(tutumluluk ilkesi): kısa devre yapan ortak 5 V referans her sensörün "devre
düşük" kodunu, CAN omurga arızası her "iletişim kaybı" kodunu üretir.
`scripts/copilot_data/build_reasoning_rules.py` → `data/diagnostics/reasoning_rules.json`
(6 kural: ortak sensör referansı, ortak sensör şasesi, CAN omurgası, düşük besleme
gerilimi, çoklu silindir teklemesi, krank+eksantrik birlikte). Graf zaten bir ortak
neden düğümü taşıyorsa (P0171+P0174 "iki bankta fakir") kural eklenmez.

* Eşleşme: aktif ve bilinen kodlar üzerinde kod kalıbı (`codes_regex`), başlık
  kalıbı (`title_regex`, TR+EN katlanmış), en az kod sayısı, zorunlu kodlar.
* Aday `kind=pattern`, puan `3.0 + (açıklanan kod − 1)`; aynı kodları tek tek
  toplayan bir graf düğümü varsa (üç silindir için "buji/bobin") onun 0.5 üstü.
  Destek satırı "N bulgu aynı ortak nedeni işaret ediyor: …" ve her kod.
* Kuralın ilk kontrol adımı "Ne yapmalı"nın başına (güvenlik satırlarından sonra)
  gelir: ör. "sensör soketlerinde 5 V referansı ölçün; sensörleri tek tek ayırın".
* Tek kod veya ilgisiz kodlar kural tetiklemez.
* `signal_low` alanı (düşük akü gerilimi okuması) eşiği olan sinyallerde çalışır;
  `BatteryVoltage` için henüz eşik kaydı yok, bu yüzden şimdilik yalnız P0562 ile tetiklenir.

### 3.1.3 Çalışma durumu ve senaryolar (`operating_state.py`, `operating_scenarios`)

Aynı ölçüm, alındığı durumda anlam kazanır: 12.3 V dinlenmiş aküde normale
yakın, çalışan motorda "alternatör şarj etmiyor"dur; 8 kPa DPF fark basıncı
rölantide tıkanma, tam yükte normaldir. `operating_state.infer_state` durumu
istekten çıkarır, ölçüm sözden önce gelir, kanıt yoksa "unknown" kalır:

* motor: `off` (<50 rpm) · `cranking` (<400) · `idle` (≤1100) · `running` · `load`
  (yük ≥%70 veya "yükte / tam gaz / yokuşta"); metin ipuçları: "rölantide",
  "kontak kapalı", "marşta", "seyir halinde"…
* ısıl: `cold` / `warm` ("soğukken", "sıcakken" veya soğutma suyu ≥70 °C);
* sistem gerilimi: 12 / 24 V (metinde "24V sistem" veya akü okuması >18 V).

Durum cevapta görünür (`technical.state`, arayüzde "Durum" çipi) ve her kararın
kaynağı tutulur (`EngineSpeed=750`, `text:rolantide`).

* **Akü gerilimi** (operatör onaylı bantlar, 2026-10-03; 24 V = 2×12 V):
  motor çalışırken normal 13.8–14.4 V (<13.2 düşük, <12.0 kritik, >15.0 yüksek,
  ≥15.5 kritik); dururken normal 12.4–12.9 V (<12.4 zayıf, <12.0 düşük, <10.5
  kritik, >12.9 yüzey şarjı); marşta ≥9.6 V. Motor durumu bilinmezse ≥13.2 V
  "çalışıyor", <12.0 V her durumda düşük, arası `needs_context` ve eksik veri
  "motor çalışırken mi, dururken mi?". Akü arızası tek başına KIRMIZI yapmaz (SARI).
* **Turbo** tam yük bandı yalnız yükte uygulanır; rölantide düşük basınç normal,
  durum bilinmezse `needs_context`.
* **Yağ basıncı** metinde "rölantide" varsa devir verilmeden rölanti bandıyla değerlendirilir.
* **Senaryolar** (11): alternatör şarj etmiyor, regülatör aşırı şarj, marşta
  zayıf akü, dinlenmede boş akü, yüzey şarjı (yalnız not), rölantide dolu DPF,
  yükte düşük turbo, ısınmış motorda açık termostat, rölantide hararet (fan/hava
  akışı), yükte hararet (radyatör/pompa/termostat), motor dururken yağ basıncı
  (sensör güvenilmez). Tetiklenen senaryo `kind=scenario` aday ekler; kanıt
  satırı durumu ve değeri yazar, ilk kontrolü "Ne yapmalı"ya gelir.
* Ölçüm şikâyetle çelişirse ölçüm kazanır: "hararet" denip su 62 °C ölçülürse
  aciliyet KIRMIZI'ya çekilmez.

### 3.1.4 Kod durumu, aralıklı arıza ve onarım doğrulaması

* Kodlar durumuyla gelebilir: `dtcs=[{"code": "P0301", "status": "HISTORY"}]`
  (`ACTIVE` / `HISTORY` / `PENDING`, J1939'da `oc` tekrar sayısı). Canlı oturumda
  masaüstü uygulaması her kodun en güçlü durumunu gönderir (ACTIVE > PENDING > HISTORY).
* **Geçmiş kod** şu an mevcut değildir: nedenleri yine sıralar ama daha az
  puanla (2.0, aktifte 3.0), güven "düşük", aciliyete katılmaz; kanıt satırı
  "geçmiş kod (aralıklı arıza olabilir)" der. **Bekleyen kod** aciliyeti en çok
  SARI yapar.
* **Aralıklı arıza** (geçmiş kod veya ≥5 tekrarlı elektriksel kod) için
  "kablo demetini sallayarak (wiggle test) izleyin" adımı eklenir; "veri geçerli"
  FMI'lı tekrar (gerçek bir koşul) için eklenmez. Bekleyen kod için "onay için
  bir sürüş döngüsü" adımı eklenir.
* Kodlu her cevabın son adımı **onarım doğrulamasıdır**: "kodları silin,
  arızanın görüldüğü koşulda (ör. rölantide) test edin; kod geri gelirse
  sonraki adaya geçin".

### 3.1.5 Devam sorusu (konuşma bağlamı)

`answer_query(text, context_text=<önceki soru>)` (köprü: `ask_copilot_structured(query,
language, answers, context)`). Yeni mesaj kendi kodunu veya şikâyetini içermiyorsa
("rölantide 106 derece") önceki soruyla birlikte okunur ve özet "(Önceki soruyla
birlikte değerlendirildi.)" diye başlar; kendi şikâyeti varsa ("klima çalışmıyor")
yeni konu sayılır. Arayüz konuşmayı biriktirir; "Yeni konu" düğmesi sıfırlar.
"rölanti" tek başına artık bir şikâyet değil, durum kelimesidir.

### 3.1.6 Freeze frame (Mode 02) ve ECU'nun kendi testleri (Mode 06)

ECU ikaz eşiklerini kalibrasyonunda tutar ve standart servislerle vermez; tek
standart istisna **Mode 06**'dır: ECU her izleme testinin (katalizör, O2,
silindir başına tekleme, EVAP, EGR …) ölçtüğü değeri **kendi min/max
limitiyle** yayınlar. **Mode 02 (freeze frame)**, kod kaydedildiği andaki
koşulları (devir, yük, su sıcaklığı, yakıt düzeltmeleri, akü gerilimi …) verir.

* Okuma: `engine/diagnosis/obd_reader.py` Mode 03/07/0A'dan sonra ilk cevap veren
  ECU'dan freeze frame (çerçeve 0: önce kodu kaydeden DTC, sonra PID'ler) ve
  Mode 06 (desteklenen OBDMID zinciri 0x00/0x20/…, sonra her MID; en çok 40)
  okur. `ReadOnlyPolicy` yalnız okuma servislerine izin verir; 0x02 ve 0x06
  listeye eklendi, Mode 04 (silme) ve 08 (kontrol) reddedilmeye devam eder.
* Çözümleme: `protocols/obd/mode06.py`. Geçti/kaldı kararı ham değerlerle
  verilir (değer ve limitler aynı UASID'yi paylaşır; 0x80+ işaretli); ölçekleme
  yalnız gösterim içindir, tabloda olmayan UASID ham gösterilir.
* Copilot: `answer_query(..., freeze_frame=, monitors=)`; masaüstünde son
  taramanın okuması (`ScanRunner.last_obd`, her yeni taramada temizlenir)
  otomatik verilir.
  * Freeze frame **arıza anı durumunu** verir (`fault_state`): özet "Arıza anı
    (P0301, freeze frame): rölantide, sıcak motor; …", senaryolar arıza anı
    değerleriyle de çalışır ("arıza anında" kanıtı), onarım doğrulaması bu
    durumda yapılır. STFT+LTFT toplamı >+%20 / <-%20 fakir/zengin karışımı
    doğrular ve aktif kodun nedenlerine kanıt olur.
  * Başarısız Mode 06 testi `kind=monitor` aday olur ("ECU testi başarısız: …;
    41 sayım, ECU limiti 0–20"), testin ilgili kodunun (OBDMID→DTC eşlemesi)
    graf nedenlerini kod olmasa da güçlendirir, aciliyeti SARI yapar ve
    "test geçmeden onarım bitmiş sayılmaz" adımı ekler. Geçen ama limitin son
    %10'unda olan test "sınırda" diye özetlenir.
* Simülatör (`SimulatedObdEcu`) freeze frame ve Mode 06 cevaplarını da verir.

### 3.1.7 Ağır vasıta: J1939 DM4 (freeze frame) ve DM7 → DM30 (test sonuçları)

Kamyon/iş makinesinde aktif kodlar DM1 yayınıyla zaten gelir; Mode 02/06'nın
J1939 karşılığı ise yalnız istenince verilir:

* **DM4** (PGN 65229): kodun kaydedildiği andaki zorunlu parametreler —
  boost (SPN 102), devir (SPN 190), yük (SPN 92), su sıcaklığı (SPN 110), araç
  hızı (SPN 84). Request PGN 59904 ile istenir. "Mevcut değil" aralığındaki
  (0xFB.. / 0xFB00..) parametre okumaya eklenmez, uydurulmaz.
* **DM7 → DM30** (PGN 58112 → 41984): yalnız **TID 247 + FMI 31** biçimi
  kurulur ("bu SPN için ECU'nun zaten çalıştırdığı testlerin sonuçlarını
  bildir"). TID 1–245 test *başlatır*; ne okuyucu kurar ne politika geçirir.
  DM30 kaydı: TID, SPN/FMI, SLOT, değer, üst limit, alt limit. Karar ham
  değerle verilir (aynı SLOT); 0xFB00+ değer = test tamamlanmamış (kayıt
  atlanır), 0xFB00+ limit = o tarafta limit yok (tek taraflı limit "≤ 400"
  diye gösterilir). Doğrulanmış SLOT tablosu olmadığından değerler **ham**
  gösterilir. DM8 (PGN 65232) DM30 ile değiştirildiği için istenmez.
* Güvenlik: `ReadOnlyPolicy(j1939=True)` 29-bit çerçevede yalnız (1) okunur
  DM'ler için Request (DM1/2/4/5/6/12; DM3/DM11 silme reddedilir), (2) TID 247
  DM7, (3) belirli ECU'ya TP.CM CTS / ACK / abort (çok paketli cevabı almak
  için) geçirir. Binek araç oturumu (`j1939=False`) her 29-bit çerçeveyi
  reddetmeye devam eder.
* Kod: `protocols/j1939/dm_results.py` (çözümleme), `engine/diagnosis/j1939_reader.py`
  (`read_j1939_snapshot`: DM4, sonra aktif DM1 kodlarının ve freeze frame'in
  SPN'leri için DM30, en çok 20 SPN; çok paketli cevaplar mevcut
  `J1939TransportProtocol` ile RTS/CTS üzerinden). Sonuç `ObdReadOutcome`
  olarak döner; tarama izni verilen kamyon/iş makinesi taramasında
  `ScanRunner.last_obd` dolar ve copilot'a otomatik gider.
* Copilot: freeze frame kodu "SPN 3251 FMI 0" biçiminde kabul edilir; arıza
  anı durumu aynı şekilde çıkarılır. DM30 kayıtları (`spn`/`fmi`/`tid`)
  `monitors` içinde gelir; başarısız test `kind=monitor` aday olur
  (`j1939_spn_fmi#SPN_<n>` atfıyla), "SPN <n>" kodunun graf nedenlerini
  güçlendirir, aciliyeti SARI yapar ve "DM30 yeniden okunmadan onarım bitmiş
  sayılmaz" adımı ekler. SPN veritabanında olmayan test satır olarak
  gösterilir ama atıf olmadan aday olmaz.
* Simülatör (`SimulatedJ1939Ecu`): SPN 3251 FMI 0 (DPF fark basıncı) için
  freeze frame ve iki DM30 testi (biri limit dışı) verir.
* Sınır: yalnız simülatörle doğrulandı; gerçek ECU'larda DM4/DM30 desteği
  ve DM7'ye hedefli/global cevap biçimi üreticiye göre değişir.

### 3.1.8 Kanonik parametre: SPN/PID adlı okumalar

Aynı fiziksel büyüklük protokole göre farklı kimlik taşır (motor devri =
SPN 190 = Mode 01 PID 0x0C). Bus araçları ve dışa aktarılmış kayıtlar
okumayı çoğu zaman ad yerine bu kimlikle verir: `{"SPN 110": 104}`,
`{"PID 0C": 1450}`.

* `KnowledgeBase.protocol_signal(name)` bu anahtarı (`SPN 110`, `SPN_190`,
  `J1939 SPN 102`, `PID 0C`, `PID 0x05`, `01 PID 0C`) kanonik sinyale ve
  protokolün kendi birimine çevirir. Kaynak `signal_measurement_map`'teki
  `j1939.spn` / `obd.pid` alanlarıdır; bunlar sevk edilen J1939 ve OBD
  veritabanlarından kopyalanmıştır. Yeni eşleme uydurulmaz.
* `canonical_signal()` önce bu katmana bakar. Böylece SPN/PID adlı okuma,
  adıyla gelen okumayla aynı eşiğe, makul aralığa ve senaryoya bağlanır.
* Birimsiz değer protokolün kendi birimindedir ve dönüştürülür:
  `"SPN 100": 350` = 350 kPa = 3.5 bar (kanonik yağ basıncı bar'dır).
  Dönüşümü olmayan birim (ör. SPN 27 "-") okumayı bilinmeyen anahtara
  düşürür; değer zorla yorumlanmaz.
* İki kanonik sinyalin paylaştığı kimlik belirsizdir ve hiçbir şeye
  çevrilmez.
* Metin içindeki "SPN 110" arıza kodu olarak kalır; bu katman yalnız
  telemetri / freeze frame anahtarlarına uygulanır.

### 3.1.9 Araç ve ECU kimliği (Mode 09, J1939 VI / DM19 / SOFT / CI)

Okuma izni verilmiş taramada ECU'dan kimlik de okunur. Bunların hepsi
yalnız bilgi bildirir; ECU'da hiçbir şeyi değiştirmez.

* Binek: Mode 09 InfoType 0x02 (VIN), 0x04 (kalibrasyon kimliği, CAL ID),
  0x06 (CVN), 0x0A (ECU adı). Mode 09 salt-okuma politikasında zaten
  vardı.
* Ağır vasıta: Request (PGN 59904) ile VI (65260, VIN), DM19 (54016,
  CAL ID + CVN), SOFT (65242, yazılım kimliği), CI (65259, bileşen
  kimliği). Bu dört PGN `READ_ONLY_J1939_REQUEST_PGNS`'e eklendi.
* Çözümleme katıdır: temiz yazdırılabilir ASCII olmayan alan atılır,
  onarılıp kimlik gibi gösterilmez. CVN'si gelmeyen kalibrasyonun CVN'i
  boş kalır.
* Sonuç `ObdReadOutcome.identity` olarak copilot'a `identity=` ile gelir.
  Okuma yoksa uygulama bus'tan yayınla gelen VIN'i (PGN 65260) kullanır.

Copilot'ta:

* VIN biçimi ISO 3779'a göre denetlenir. Marka yalnız araç kataloğundaki
  WMI öneklerinden çıkarılır (`profiles_for_vin`); bilinmeyen önek tahmin
  edilmez.
* Marka önceliği: seçilen araç > VIN > metindeki marka kelimesi. Araç
  seçilmemişse VIN'in markası geri çağırma / şikâyet aramasında kullanılır
  ve not "marka VIN'den belirlendi" der. Aynı WMI'yi paylaşan marka
  grubunda (Hyundai-Kia) arama markası belirlenmez.
* Seçilen marka ile VIN'in markası çelişirse özet uyarır: markaya özel
  kayıtlar yanlış araca ait olabilir.
* Özet ve teknik bölüm VIN'i maskeli gösterir (WMI + son 4 hane); tam VIN
  cevaba hiç girmez.
* Kalibrasyon kimliği varsa ve kod bulunduysa "bu kalibrasyon için yazılım
  güncellemesi / teknik bülten var mı bakın" adımı eklenir
  (`template:calibration_check`). Elimizde bülten verisi yoktur; copilot
  bülten uydurmaz, yalnız bakılacak yeri söyler.
* Simülatör VIN'leri sentetiktir (VW ve Volvo Trucks WMI'li, gerçek araç
  değil).

### 3.1.10 Üreticiye özel kodun markaya göre anlamı

Wal33D OEM katmanı kod başına tek açıklama tutuyordu ("son yazan kazanır").
Oysa birçok üretici kodu markaya göre başka arıza demektir: P1106 GM'de MAP
sensörü, Honda'da BARO devresi; P1101 Ford'da hava debimetresi, VW metninde
oksijen sensörü. Intake taraması (`docs/audit/intake_source_scan_2026-10-02.md`
§4b, §4o) üretici başına metni sahaya almıştı.

* `scripts/build_dtc_oem_meanings.py`, `data/intake/oem/` kayıtları ve OEM
  katmanından `dtc_oem_meanings.json`'u üretir: anlamı markalar arasında
  farklı olan 694 kod, metin upstream'den birebir. Genel listeler
  (other/p/c/u) marka sayılmaz.
* `KnowledgeBase.dtc_oem_meaning(code, make)` herhangi bir marka etiketini
  (`Honda`, `volkswagen`, `Volkswagen Group`) `make_aliases` ile Wal33D
  listesine çevirir. GM markaları ortak GM listesine düşer. İki marka içeren
  etiket ("Hyundai-Kia") marka grubudur ve sonuç vermez.
* Marka biliniyorsa (seçilen araç, VIN veya metin) o markanın anlamı
  kullanılır:
  * Anlam genel kayıtla aynıysa (içerik kelimelerinin en az yarısı ortak)
    yalnız not edilir.
  * Farklıysa genel kayıt başka markanın arızasını anlatıyordur: o kaydın
    neden, adım, semptom, referans ve şiddeti atılır, kod kök neden
    grafiğine sokulmaz. Başlık markanın anlamı olur ve "bu üreticinin servis
    akışını izleyin" adımı eklenir (`dtc_oem_meanings#<kod>.<MARKA>`).
* Marka bilinmiyorsa farklı anlamlar eksik veri olarak listelenir ve marka
  istenir; özet "yorum kesin değil" der.

### 3.2 Aciliyet ve güvenlik

* Aciliyet: kod ciddiyeti `drive_safety_policy.decide_risk` ile (tek otorite),
  kritik telemetri → KIRMIZI, eşik üstü → en az SARI, DM1 kırmızı STOP lambası →
  KIRMIZI, EV kodu (P0A*) → KIRMIZI. Veri yoksa **GRİ** (asla yeşil değil).
* Kod/ölçüm olmadan da KIRMIZI olan şikâyetler (politika 2026-10):
  * `STOP_SYMPTOMS` — hararet (`engine-overheating`), düşük yağ basıncı
    (`low-oil-pressure`): motor çalışmaya devam ederse kalıcı hasar. Aynı sinyal
    **normal ölçülmüşse** yükseltilmez.
  * Fren/direksiyon şikâyeti ("fren pedalı boşa gidiyor", "direksiyon
    ağırlaştı"): güvenlik bandı "yola çıkmayın" derken aciliyetin "kullanabilirsiniz"
    demesi çelişkiydi. Yalnız uyarı lambası/sensör şikâyetleri (`abs`,
    `abs-esp-traction-fault`, `steering-angle-sensor-uncalibrated`,
    `brake-light-switch-rationality`) SARI kalır.
* Güvenlik bandı (cevabın **ilk satırı**): yangın, yüksek voltaj, fren,
  direksiyon. Tetikleyiciler: kod sistemi (`system_taxonomy`), semptom alanı
  (EV_HV), `symptom_lexicon.safety_terms`, HV ölçümleri. Metinde egzoz geçiyorsa
  duman kelimeleri yangın bandını tetiklemez ("egzozdan mavi duman" bir motor
  belirtisidir). Yalnız güvenlik terimi tanındıysa özet bunu açıkça söyler.

## 4. Veri kaynakları (KnowledgeBase kaydı)

| Kaynak id | Dosya | Copilot'ta kullanımı |
|---|---|---|
| `dtc_database` | `diagnostics/dtc_database.json` (14.484) | Başlık, alt sistem, temiz nedenler, adımlar, referans değerler |
| `dtc_oem_layer` | `diagnostics/dtc_database_oem_layer.json` | OEM marka listesi, kayıt dışı kodların genel tanımı |
| `dtc_oem_meanings` | `diagnostics/dtc_oem_meanings.json` | Anlamı markaya göre değişen 694 üretici kodunun marka başına metni (§3.1.10) |
| `j1939_spn_fmi` | `diagnostics/j1939_spn_fmi_database.json` (4.291 SPN, 32 FMI) | SPN adı, FMI anlamı + ciddiyeti, PGN, nedenler, adımlar |
| `extended_pid` | `diagnostics/extended_pid_database.json` (244) | PID açıklaması, ölçüm rehberi |
| `obd_mode06`, `uds_did` | Mode 06 / UDS DID | KB üzerinden erişilebilir (eski paket açıklama yolu) |
| `canonical_symptoms` | 152 semptom | Şikâyet → aday kod, ilk kontroller |
| `symptom_lexicon` (yeni) | 34 kayıt, 301 TR/EN ifade + güvenlik terimleri | Gündelik ifadeler |
| `operating_scenarios` (yeni) | Akü bantları (12/24 V × durum) + 11 senaryo | Durum-bilinçli yorum (§3.1.3) |
| `reasoning_rules` (yeni) | 6 ortak kök neden kuralı | Birden çok kodu tek nedenle açıklama (§3.1.2) |
| `graph_title_i18n` (yeni) | 184 graf düğümü | Graf başlıklarının TR/EN gösterimi: sık ulaşılan düğümler küratörlü, OEM etiketli kalıplar ("[Kia] Faulty X") bileşen sözlüğüyle |
| `subsystem_labels_en` (yeni) | 315 alt sistem etiketi | İngilizce cevapta `area` satırlarının adı (yalnız etiket çevirisi) |
| `symptom_checks` (yeni) | 147 semptom, 270 soru | Soru cevaplarının küratörlü etkileri (§3.1.1) |
| `root_cause_graph` | 8.884 düğüm | Kök neden adayları, kanıt/çelişen sinyaller |
| `signal_aliases` + `signal_measurement_map` (yeni, 24 sinyal) | | Sinyal adı birleştirme; eksik ölçüm için SPN/PGN/PID rehberi |
| `system_taxonomy` | 26 sistem | Fren/direksiyon güvenlik tespiti |
| `telemetry_thresholds` | 10 sinyal (soğutma suyu, turbo, yağ basıncı devre bantlı, devir, hız, yük, tork; DPF fark basıncı, fren hava devre 1/2 — kaynak: KB ölçüm metinleri) | Nominal/uyarı/kritik değerlendirme; fren havası kırmızı eşik altındaysa KIRMIZI + fren bandı |
| `hv_safety_thresholds` | UN R100 vb. | İzolasyon direnci Ω/V kontrolü (HV-ISO-001) |
| `dtc_severity_rules` | SAE J2012 kural tablosu | Kod ciddiyeti |
| `nhtsa_recalls`, `nhtsa_complaints` | 282 kampanya, 4.459 şikâyet | Ayrı ve açıkça etiketli "NHTSA" bölümü (VIN doğrulaması yok) |
| `canboat_pgn` | 628 N2K PGN + DM1 | PGN açıklaması, DM1 alan düzeni |
| `copilot_glossary` (yeni, 32 terim) | | Jargon için tek cümlelik TR/EN açıklama |
| `user_kb`, `dtc_procedures`, `dbc_catalog`, `golden_cases` | | KB üzerinden erişilebilir; kullanıcı kartı / kalibrasyon yolları |

Atıf biçimi: `dtc_database#P0101.causes[0]`, `j1939_spn_fmi#SPN_110.FMI_0`,
`root_cause_graph#thermostat-stuck`, `telemetry_thresholds#EngineCoolantTemp`,
`template:safety.brakes` (sabit şablon).

## 5. Genişletme

* **Yeni şikâyet ifadesi:** `scripts/copilot_data/build_lexicon.py` içindeki
  listeye mevcut bir `symptom_id` için ifade ekleyin → betiği çalıştırın.
  Yeni semptom gerekiyorsa önce `canonical_symptoms.json` (kaynaklı) güncellenir.
* **Yeni ölçülebilir sinyal:** `scripts/copilot_data/build_signal_map.py`'ye
  satır ekleyin; SPN/PID alanları veritabanından **kopyalanır** (elle yazılmaz).
  Eşik gerekiyorsa `telemetry_thresholds.json`'a yalnız kaynaklı değer eklenir.
* **Yeni terim:** `scripts/copilot_data/build_glossary.py` (yalnız tanım; değer/limit yok).
* **Yeni veri kaynağı:** `knowledge_base.SOURCE_FILES`'a ekleyin, tembel
  yükleyici + `Lookup` dönen metot + `resolve_ref` dalı yazın,
  `scripts/validate_copilot_data.py`'ye çapraz kontrol ekleyin.
* **Her yeni kayıtta** `provenance` bloğu (`provenance_schema.json`) zorunludur;
  kaynağı/lisansı belirsiz veri eklenmez (`data/PROVENANCE.md`).

Kontroller (CI'da koşar):

```bash
python scripts/validate_copilot_data.py          # FAIL varsa çıkış 1
python scripts/rebuild_csv_exports.py --verify   # 7 CSV ikizi JSON ile senkron mu
python scripts/rebuild_csv_exports.py            # ikizleri tek kaynaktan yeniden üret
python -m pytest tests/unit/test_copilot_*.py tests/safety/test_ai_tx_isolation.py
```

## 6. Testler

| Dosya | İçerik |
|---|---|
| `test_copilot_golden_scenarios.py` | 69 altın senaryo (çalışma durumu, akü, ortak kök neden, soru cevapları, gündelik ifadeler, DTC, SPN/FMI, DM1, semptom, gösterge değeri, olumsuzluk, telemetri+kod, çelişkili kanıt, veri yok, EV/HV, fren/direksiyon, çoklu kod, yazım hatası, TR/EN, NHTSA, PGN) + 6 bölüm/ilk satır güvenlik kontrolü |
| `test_copilot_no_fabrication.py` | Her küratörlü sorunun her cevabı (424 durum) için atıf çözümü ve sayı izlenebilirliği; atıf çözümü, sayı izlenebilirliği, yalnız verilen sinyallerde bulgu, bilinmeyen koda anlam verilmemesi, NaN/birim reddi, determinizm, yazma/TX yokluğu |
| `test_copilot_knowledge_and_parsing.py` | KB tembelliği, indeksler, kaçırma nedenleri, ayrıştırıcı birim testleri |
| `test_copilot_performance.py` | Kurulum < 10 ms, sıcak sorgu ort. < 150 ms (ölçülen 2–13 ms), bellek < 8 MB, arama katmanı aç/kapa |
| `test_obd_freeze_frame_mode06.py` | Mode 06 çözümleme (işaretli/ham UASID, bitmask), okuyucu + salt-okuma politikası, tarama sonrası saklama, copilot arıza anı/ECU testi/yakıt düzeltmesi |
| `test_copilot_operating_state.py` | Durum çıkarımı, akü gerilimi duruma göre, 24 V, DPF/turbo/hararet senaryoları, yüzey şarjı |
| `test_copilot_checks.py` | Soru hedeflerinin semptoma aitliği, bant sürekliliği, cevapla öne alma/geri itme, metinden ölçüm, etkisiz cevaplar, TR/EN |
| `test_copilot_bridge_and_data.py` | Köprü uç noktası, `answers` doğrulaması, ek analiz anahtarı, veri kapısı, üreticilerin bayt-eşdeğerliği |
| `tests/ui_e2e/test_workbench_ui.py::test_assistant_copilot_card_answers_free_text_read_only` | Gerçek tarayıcıda kart, güvenlik bandı, salt okuma; `test_copilot_check_answers_narrow_the_causes`, `test_copilot_session_answer_questions_are_answerable`: soru cevaplama akışı |

## 7. Terim sözlüğü

`data/diagnostics/copilot_glossary.json` — cevapta geçen jargon (DTC, SPN, FMI,
PGN, DM1, OBD, ECU, DPF, EGR, SCR/AdBlue, HV, BMS, HVIL, MAF, CKP, …) için tek
cümlelik Türkçe/İngilizce açıklamalar. Cevabın teknik bölümünde, yalnız metinde
gerçekten geçen terimler listelenir. Kayıtlar tanım içerir; değer, limit,
neden veya prosedür içermez.

# Uzman Masası (Engineer Workbench) — Yeniden Yapım Planı

> Durum: **B1–B3 birleşti. B3b bas-bırak deneyi, B4 Teşhis asistanı.** B5–B8 her biri ayrı PR olarak yapılır.

Eski uzman ekranı (sniffer, osiloskop, tersine mühendislik, ECU, pin rehberi, raporlar, ayarlar,
copilot) tamirci akışında kullanılan tasarım diliyle baştan kuruluyor: yuvarlak kartlar, her
kontrolün yanında tek cümlelik açıklama, yalnızca ölçülen değerleri gösteren dürüst durum çipleri.

## 1. Ürün ilkeleri (tamirci akışıyla aynı süzgeç)

1. **Uydurma veri yok.** Ekranda görünen her çerçeve Python'un ingest yolundan gelir. Hat boşsa
   ekran "veri yok" der; örnek satır, varsayılan sayı veya rastgele grafik gösterilmez.
2. **Kaynak her yerde yazılı.** Çerçevenin kaynağı (`physical` → "Araç", `synthetic` → "Simülatör",
   `replay` → "Kayıttan" …) durum çubuğunda ve akış tablosunda görünür.
3. **Güvenlik her ekranda.** ACİL DURDUR düğmesi ve gönderim durumu ("Yalnızca dinleme" /
   "Araca yazma açık" / "Güvenlik kilidi") her sayfanın üst çubuğundadır. UI yaşam sinyali (TX
   watchdog kirası) kabuk açıkken sürer; pencere donarsa kira düşer, TX reddedilir.
4. **Araca yazan hiçbir şey varsayılan açık değil.** Kabuk TX başlatmaz; yazma gerektiren modüller
   (ECU programlama, aktif testler, arıza silme) mevcut TX Gateway / native onay / challenge
   akışından geçer ve ayrı PR'larda yeniden kurulur.
5. **Jargon azaltılır, silinmez.** "Sniffer" → "Canlı trafik", "Bus load" → "Yük ≈ %21" ve
   üzerine gelince nasıl hesaplandığı.

## 2. Envanter: eski ekran ve bulgular

| Eski modül | Dosya(lar) | Veri kaynağı (bulgu) | Yeni yer |
|---|---|---|---|
| Dashboard / Sniffer | `App.tsx`, `dashboard/DataTable.tsx`, `CanSnifferTable.tsx`, `SummaryStrip.tsx` | **Açılışta 30 sabit örnek satır** (`INITIAL_PACKET_ROWS`), sayaç 15.553'ten başlıyor, "Başlat" tarayıcıda **rastgele paket üretiyor** (`Math.random`). Gerçek çerçeveler `onNewCanFrames` ile ancak araca bağlıyken geliyordu. | **B1: Canlı trafik** |
| Grafik ve sinyal analizi | `ScopePanel.tsx`, `SignalOscilloscope.tsx`, `anomalyDetector.ts` | Sabit dalga biçimleri; HV/SOC/akım değerleri Python DEMO döngüsünün sabitleri (398,4 V, %78,4). | **B2: Grafik** (Python'un çözdüğü sinyaller) |
| Sinyal keşfi + tersine mühendislik | `discovery/SignalDiscoveryView.tsx`, `modals/ReverseEngineeringModal.tsx`, `reverseEngineeringEngine.ts` | Tarayıcıdaki TS motoru; Python'daki `discovery_*` uçları **hiç kullanılmıyordu**. | **B3** (Python motoru); uyaran deneyi B3b |
| Copilot | `dashboard/AiCopilotPanel.tsx`, `diagnosticEngine.ts`, `copilotContextBuilder.ts` | Durum, **uydurma telemetriyle** (850 rpm, 88 °C…) TS'te hesaplanıyordu; aksiyonlar (kod silme dahil) `userConfirmed=true` ile **otomatik onaylı** çağrılıyordu. | **B4: Teşhis asistanı** (Python analiz + soru-cevap; araca yazan aksiyon yok) |
| ECU programlama | `ecu/EcuFlashingView.tsx`, `flashRequest.ts` | Python `flash_*` + challenge; doğru bağlı. | B5 (B1'de önceki görünüm) |
| Pin rehberi | `pinout/PinoutGuideView.tsx` | Statik içerik. | B6 (B1'de önceki görünüm) |
| Raporlar | `reports/ReportsExportView.tsx`, `exportService.ts` | Ekrandaki 300 satırı tarayıcıda yeniden kodluyordu. | **B1: Kayıt ve rapor** (Python `export_logs`, `export_session_report`) + B7 |
| Ayarlar | `settings/*` | Python'a bağlı (bulut, lisans, kaynak lisansları, kullanım modu). | B6 (B1'de önceki görünüm) |
| Simülatör stüdyosu | `modals/SimulatorStudioModal.tsx`, `canSimulator.ts` | Tarayıcı simülatörü. | B1'de yerine **gerçek simüle araç hattı** (§4) |

Python tarafında bulunan iki ek sorun (B4'te düzeltildi):

* `_bus_load` canlı yolda `min(100, kare_sayısı / 2)` — tick başına 200 kare = "%100". Ekrandaki yük artık
  gelen çerçevelerin bit uzunluğundan ve hattın bit hızından hesaplanıyor (bit doldurma hariç, "≈").
* `_error_count` hem hata çerçevelerini hem **DM1 arıza kodu sayısını** tutuyor. Ekran yalnızca
  sürücünün kendi sayacını (`bus.metrics.error_frames`) ve `bus_off` durumunu gösterir.

## 3. Bilgi mimarisi

```
Uzman masası
├── Veri
│   ├── Canlı trafik        (B1)  kimliğe göre / akış, filtre, dondur, bit değişim haritası
│   ├── Sinyal keşfi        (B3)  Python discovery_* + DBC dışa aktarma
│   └── Grafik              (B2)  çözülmüş sinyaller (J1939 / NMEA2000 / OBD), birimli
├── Araç
│   ├── Teşhis asistanı     (B4)  Python analiz + soru-cevap; aksiyonlar onaylı
│   ├── ECU programlama     (B5)  challenge + native onay + E-Stop + hız kilidi
│   └── Pin rehberi         (B6)
├── Çıktı
│   └── Kayıt ve rapor      (B1, B7: kayıttan oynatma, klasörü aç, buluta yükleme onayı)
├── Ayarlar                 (B6)  adaptör bağlantısı (adapter_scan + dinleme testi), lisans, mod
└── Tamirci moduna geç
```

Üst çubuk (her sayfada): sayfa adı + tek cümle · hat çipi (adaptör/simülatör + kaynak) ·
Yük ≈ % · sürücü hata çerçevesi (varsa) · BUS-OFF (varsa) · güvenlik durumu · ACİL DURDUR.

## 4. Simülatör: tarayıcı üreteci yerine gerçek simüle hat

Eski "Başlat" düğmesi Python'da yalnızca sabit telemetri değerleri üreten DEMO döngüsünü açıyor,
paketleri ise tarayıcı uyduruyordu. B1'de uzman masası, bağlantı sihirbazının kullandığı
**salt-dinleme simüle araç hattını** (`SimulatedVehicleBus`, çerçeveler `source="synthetic"`)
uygulamanın hattı olarak bağlar:

* `sim_vehicle_start(vehicle_type)` — risk sınıfı `config`. Ayarlardaki yeniden bağlanma ile **aynı**
  güvenli takas (`_install_bus_locked`): eski hat canlıysa ve yenisi açılamazsa eskisi kalır,
  gateway yeniden bağlanır, TX PASSIVE'e düşer, kanal durumu sıfırlanır.
* Reddedilir: E-Stop kilitliyken (gerçek hattın adli kaydı sürmeli), TX açıkken (önce yazma kapatılır),
  bilinmeyen araç türünde.
* `sim_vehicle_stop()` — önceki adaptöre döner. `bus_get_info()` — `read`; ne dinlendiğini söyler.
* Simüle hat `send()` çağrısında `SafetyError` atar: hiçbir koşulda çerçeve göndermez.

## 4.1 Grafik (B2)

* Python çözücülerinin (`_record_signal_sample`: J1939, NMEA 2000, OBD, OEM) ürettiği her sayısal değer
  ayrı bir **çizim halkasına** yazılır (`_plot_rings`; sinyal başına 6000 nokta, en çok 128 sinyal).
  Bu halka teşhis kanıtından ayrıdır: simüle araç çizilebilir ama **asla kanıta girmez** (#32).
* `plot_signal_list` ve `plot_signal_series(names, window_s)` — ikisi de `read`. Pencere 1–120 sn'ye,
  istek 8 sinyale, yanıt sinyal başına 1500 noktaya sınırlı (en yeni nokta her zaman korunur).
* Hat değiştiğinde (simülatör ↔ adaptör) çizim sıfırlanır; iki kaynağın eğrisi aynı grafikte birleşmez.
* Ekran: her sinyal kendi grafiğinde (farklı birimler tek eksende karışmaz), tek renk, yalnız gelen
  değerler birleştirilir (yumuşatma/tahmin yok), fareyle üzerine gelince değer ve "kaç sn önce".
* Uzman masası simülatörü artık **hareketli**: rölanti ~650 ± 50 rpm salınır, soğutma suyu 70 → 88 °C
  ısınır (deterministik, rastgelelik yok). Bağlantı sihirbazının simülatörü sabit kalır.

## 4.2 Sinyal keşfi (B3)

* Ekran yalnız Python `SignalDiscoveryEngine`'i kullanır (kanal + çerçeve biçimi + kimlik anahtarlı).
  `discovery_list` / `discovery_report` (`read`), `discovery_set_approval` (`config`), `discovery_save_dbc` (`data`).
* Bit ızgarası her bitin davranışını gösterir (sabit / seyrek / düzenli / sürekli değişen; tek renk, koyuluk = etkinlik).
  Adayın üzerine gelince kapsadığı bitler çerçevelenir.
* Adaylar (sayaç, sağlama toplamı, sinyal, sabit) **istatistiksel tahmindir**; güven yüzdesiyle gösterilir, anlamını
  operatör verir. Varsayılan DBC dışa aktarımı **yalnız onaylıları** içerir.
* Düzeltilen motor hatası: onay yalnız önbellekteki rapor nesnesindeydi; canlı trafikte her yeni çerçeve raporu
  yeniden kurduğu için onay kayboluyor, "yalnız onaylılar" DBC'si boş çıkıyordu. Onaylar artık motorda kalıcı.
* DBC `exports/dbc/` altına yol korumasıyla yazılır; simülatör verisi içeriyorsa dosya adı `_SIMULATOR` ile biter.
* Hat değişiminde keşif verisi sıfırlanır (simülatör ve araç kimlikleri karışmaz).
* Eski ekrandaki "pedala bas / bırak" uyaran deneyi tarayıcıdaki TS motorundaydı; Python'a taşındı (**B3b**, §4.3).

## 4.3 Bas-bırak deneyi (B3b)

* `StimulusExperiment` (Python): operatör "Dokunmayın" ↔ "Şimdi uygula" arasında geçerken her çerçeve o anki aşamaya
  kaydedilir. Sonuçta her kimliğin baytları **etki büyüklüğüyle** (aşama ortalamaları farkı / birleşik standart sapma)
  ve bitleri **1 olma oranı farkıyla** sıralanır. Her iki aşamada da gürültülü olan bayt puan almaz; her aşamada sabit
  olup aşamalar arasında değişen (temiz basamak) en üste çıkar.
* Bu bir **ilişkidir, kanıt değil**: motor tepkisi gibi dolaylı etkiler de listelenir; ekran bunu açıkça söyler.
* Uçlar: `stimulus_start / set_phase / stop` (`config`), `stimulus_status / result` (`read`). Hiçbir çerçeve gönderilmez.
* Uzman masası simülatöründe deneme için **simüle pedal** var (`sim_vehicle_pedal`, `config`, yalnız hareketli simülatör):
  EEC2 (PGN 61443) bayt 1 = %60 olur ve motor ~900 rpm hızlanır. Bağlantı sihirbazının simülatöründe pedal çerçevesi yoktur.
* Hat değişiminde deney sonlanır.

## 4.4 Teşhis asistanı (B4)

* Ekran yalnız Python analiz hattını gösterir (kanıt kapısı → anomali → sıralı hipotezler → karar kartı) ve
  soru-cevap triyajını (`record_operator_answer`). TS tarafında hiçbir teşhis hesaplanmaz.
* **Araca yazan hiçbir işlem bu ekrandan çalışmaz.** Önerilen işlemler metin olarak listelenir, araca yazanlar
  "Araca yazar" etiketiyle; eski paneldeki otomatik onaylı `executeDiagnosticAction(action, true)` çağrısı yok.
* Simülatör hattayken asistan **ayrı bir simülasyon oturumunu** analiz eder (`_sim_diag_session`): sonuç "Simülasyon
  sonucu" bandıyla gösterilir, gerçek oturuma ve teknisyen raporuna hiçbir şey girmez, onarım geri bildirimi (öğrenme
  kaydı) simülasyonda gizlenir. Simüle DM1 tekrarları tek olaya indirilir; oturum en çok 20 000 örnek tutar.
* Analize giden canlı telemetri yalnız ölçülmüş değerlerdir: turbo basıncı bir çözücü bildirmeden girmez (önceden
  başlangıçtaki 0,0 "ölçülmüş 0 bar" olarak gidiyordu); simülatör hattayken telemetri boştur.
* `_active_dtc_count` artık `_error_count`'tan (hata çerçeveleri) ayrı; hat yükü çerçeve bit uzunluğundan hesaplanır.
* "En olası" sonuç güven değeriyle gösterilir; kanıt kapısı yetersizse "Ön değerlendirme: kesin değil" uyarısı çıkar.

## 4.5 ECU programlama (B5)

* Akış dört adım: **hedef** (ECM/TCU/ABS/BCM + VIN) → **yazılım** (dosya, SHA-256, adres, blok, imza, ortak anahtar) →
  **ön koşullar** (E-Stop, araç duruyor, başka işlem yok, girdiler geçerli; akü/besleme uygulamaca ölçülmez, metinle
  hatırlatılır) → **onay** (risk kutusu işaretlenmeden düğme açılmaz). Son karar her zaman Python'dadır
  (`flash_start`); ekrandaki koşullar yalnız `flash_preconditions` (okuma, G/Ç yok) ile gösterilir.
* **Onay yazılımın özetine bağlanır.** Onay isteği (`request_diagnostic_challenge`) görüntünün baytlarını değil
  `dataSha256` değerini taşır; `flash_start` gelen baytları yeniden özetler ve uyuşmazsa reddeder. Önceden ham hex
  onaya bağlanıyordu ve onay yükü 16 KB ile sınırlı olduğu için ~8 KB'den büyük gerçek bir yazılım hiç
  yetkilendirilemiyordu (eski ekran ayrıca onayı `data` olmadan isteyip `data` ile başlattığı için her seferinde
  "parametreler değiştirilmiş" hatası alıyordu). Hedef ECU da artık bağlamın parçası.
* **Görüntü zorunlu:** veri yoksa, hex değilse, boşsa, boyut bildirilenden farklıysa veya özet uyuşmazsa işlem onay
  harcanmadan reddedilir. Önceden fiziksel dalda eksik veri sessizce `sizeBytes` kadar sıfıra dönüşüyordu.
* **Simüle hatta prova:** simüle araç hiç gönderemez (`send()` hata verir); ECU ekranı orada "Prova modu" bandıyla
  adımları gösterir, TX kolu kurulmaz, hiçbir çerçeve gönderilmez. E-Stop ve "araç duruyor" kilidi provada da geçerli.
  Gerçek/sanal hatta yerel (native) operatör onayı ve ağ geçidinin hız kilidi aynen gerekir.

## 4.6 Ayarlar ve Pin rehberi (B6)

* **Bağlantı:** adaptör listesi `adapter_scan` ile gelir (hiçbir kanal açılmaz; simülatör burada değil, trafik
  ekranındaki düğmede). İki yol var, ikisi de adaptörü **yalnız dinleme** modunda açar ve araca hiçbir çerçeve göndermez:
  * *Dinleyerek bağlan* (`workbench_connection_test_start`): tamircinin bağlantı testinin aynısı, ama araç tipini
    mühendis seçer (tercih dosyasına yazılmaz). Hız aday listesi araç tipinden gelir; yalnız kullanılabilir bir sonuç
    uygulama hattını o hıza bağlar. Trafik yoksa hiçbir şey bağlanmaz ve bu açıkça söylenir.
  * *Sabit hızla bağlan* (`workbench_bus_connect`): 125/250/500/1000 kbit/s. Kanal açılamazsa önceki hat korunur ve
    ekran "bağlandı" demez (`CONNECT_FAILED`).
  * Acil durdurma kilitliyken ya da araca yazma açıkken hat değiştirilmez (simülatör geçişiyle aynı kural).
* **Lisans:** `auth_get_state` / `auth_refresh_license` salt okunur gösterimi (durum, paket, çevrimdışı süre, açık
  özellikler) ve kullanım modu geçişi. Eski ekrandaki elle sunucu adresi/oturum anahtarı girişi ve eski web girişi bu
  ekrana alınmadı (giriş, tamirci akışındaki cihaz kodu ekranındadır).
* **Güvenlik:** denetçi durumu, E-Stop ve hız kilidi canlı okunur; kurallar listesi yalnız uygulamanın gerçekten
  zorladıklarını yazar. Eski ekrandaki "ISO 26262 ASIL-D", "Zero-GC" gibi doğrulanmamış ifadeler kaldırıldı.
* **Veri lisansları:** `get_data_attributions` (ağa çıkmaz); okunamayan lisans dosyası kırmızı uyarıyla gösterilir.
* **Pin rehberi:** OBD-II (SAE J1962), Deutsch 9 pin (SAE J1939-13) ve NMEA 2000 Micro-C; yalnız standardın atadığı
  görevler. Eski rehber her OBD pinine bir kablo rengi veriyordu (standart renk yok), "Sarı" yazan CAN-H'yi mavi
  gösteriyordu ve 13. pine "Flash yetkisi" gibi uydurma görevler yazıyordu; üreticiye bırakılmış pinler artık öyle
  anılıyor. Kablo rengi yalnız standardın sabitlediği yerlerde (J1939 CAN çifti, NMEA 2000) var. Multimetre kontrol
  değerleri ISO 11898-2'ye göredir ve uygulamanın ölçmediği açıkça yazılıdır.

## 5. Doğrulama durumu

| Ne | Nasıl doğrulandı |
|---|---|
| Simüle hat bağlama/çözme, ret koşulları, sürücü sayaçları | `tests/unit/test_workbench_simulator.py` (CI, Linux + Windows) |
| Gerçek köprü + gerçek telemetri döngüsü + simüle kamyon → Canlı trafik, filtre, dışa aktarma, E-Stop | `tests/ui_e2e/test_workbench_ui.py` (Playwright; yerelde koşar, CI'da Playwright yok → atlanır) |
| Grafik: çizim halkası, sınırlar, simülatör işareti, hat değişiminde sıfırlama | `tests/unit/test_workbench_plot.py` (CI) + `test_workbench_ui.py` (yerel Playwright) |
| Teşhis asistanı: simülasyon oturumu ayrımı, ölçülmemiş/simüle telemetri, DTC/hata sayacı, hat yükü | `tests/unit/test_workbench_assistant.py` (CI) + `test_workbench_ui.py` (yerel Playwright) |
| Bas-bırak deneyi: sıralama, aşama/girdi doğrulama, simüle pedal | `tests/unit/test_stimulus_experiment.py` (CI) + `test_workbench_ui.py` (yerel Playwright) |
| ECU programlama: görüntü denetimi, özet bağlama (64 KB), kurcalanmış veri, simüle hatta prova (gönderim yok), native onay | `tests/unit/test_workbench_ecu.py` (CI) + `test_workbench_ui.py` (yerel Playwright) |
| Ayarlar → Bağlantı: yalnız dinleme, sabit hız, trafikli/trafiksiz dinleme testi, E-Stop/TX kuralı, başarısız bağlantı | `tests/unit/test_workbench_settings.py` (CI) + `test_workbench_ui.py` (yerel Playwright, sanal kanal) |
| Sinyal keşfi: rapor, onayın kalıcılığı, DBC kaydı, girdi doğrulama | `tests/unit/test_workbench_discovery.py` (CI) + `test_workbench_ui.py` (yerel Playwright) |
| Fiziksel adaptör (PCAN/Kvaser/RP1210) ile Canlı trafik, Grafik, Sinyal keşfi | **Doğrulanmadı.** `HARDWARE_TEST_CHECKLIST.md` W8 |
| Gerçek ECU'ya yazılım yükleme (UDS 0x34/0x36/0x37) | **Doğrulanmadı.** `HARDWARE_TEST_CHECKLIST.md` W8-17…W8-20 |
| Ayarlar'dan gerçek PCAN/Kvaser/RP1210 adaptörüne bağlanma | **Doğrulanmadı.** `HARDWARE_TEST_CHECKLIST.md` W8-21…W8-24 |
| WebView2 (Windows) içinde görünüm ve 2000 kare/sn akışta akıcılık | **Doğrulanmadı.** `HARDWARE_TEST_CHECKLIST.md` W8 |

Ekran görüntüleri (`screenshots/workbench/`) simülatörle, Linux Chromium'da alınmıştır.

## 6. Modül sırası (her biri ayrı dal + PR, öncekiler birleşmeden sonraki başlamaz)

| Aşama | Dal | İçerik |
|---|---|---|
| B1 | `flow/b1-workbench` | Kabuk, Canlı trafik, Kayıt ve rapor (temel), simüle hat, bu belge |
| B2 | `flow/b2-plot` | Grafik: Python'un çözdüğü sinyaller için okuma ucu + çizim |
| B3 | `flow/b3-discovery` | Sinyal keşfi Python `discovery_*` üzerine (TS motoru B8'de silinir) |
| B3b | `flow/b3b-stimulus` | Uyaran deneyi (bas/bırak) Python'da: iki aşama arasında değişen bayt/bitleri sıralar |
| B4 | `flow/b4-assistant` | Teşhis asistanı Python analiz üzerine; `_bus_load` / `_error_count` düzeltmesi |
| B5 | `flow/b5-ecu` | ECU programlama ekranı (aynı güvenlik akışı) |
| B6 | `flow/b6-settings` | Ayarlar (adaptör bağlantısı, lisans, mod, kaynak lisansları) + Pin rehberi |
| B7 | `flow/b7-records` | Kayıttan oynatma, klasörü aç, buluta yükleme (açık onayla) |
| B8 | `flow/b8-cleanup` | Eski `App.tsx`, dashboard, modallar, TS motorları silinir; ön yüz lint tabanı sıfırlanır ve CI'daki `continue-on-error` kaldırılır |

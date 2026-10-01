# Uzman Masası (Engineer Workbench) — Yeniden Yapım Planı

> Durum: **B1 — kabuk + Canlı trafik + Kayıt ve rapor + simülatör hattı.** Onay noktası:
> B2–B8 modülleri bu belgedeki tasarım dili onaylandıktan sonra, her biri ayrı PR olarak yapılır.

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
| Grafik ve sinyal analizi | `ScopePanel.tsx`, `SignalOscilloscope.tsx`, `anomalyDetector.ts` | Sabit dalga biçimleri; HV/SOC/akım değerleri Python DEMO döngüsünün sabitleri (398,4 V, %78,4). | B2: Grafik (Python'un çözdüğü sinyaller) |
| Sinyal keşfi + tersine mühendislik | `discovery/SignalDiscoveryView.tsx`, `modals/ReverseEngineeringModal.tsx`, `reverseEngineeringEngine.ts` | Tarayıcıdaki TS motoru; Python'daki `discovery_*` uçları **hiç kullanılmıyordu**. | B3 (B1'de önceki görünüm, canlı akışla besleniyor) |
| Copilot | `dashboard/AiCopilotPanel.tsx`, `diagnosticEngine.ts`, `copilotContextBuilder.ts` | Durum, **uydurma telemetriyle** (850 rpm, 88 °C…) TS'te hesaplanıyordu. | B4 (Python `get_diagnostic_analysis` / diyalog) — B1'de kapalı |
| ECU programlama | `ecu/EcuFlashingView.tsx`, `flashRequest.ts` | Python `flash_*` + challenge; doğru bağlı. | B5 (B1'de önceki görünüm) |
| Pin rehberi | `pinout/PinoutGuideView.tsx` | Statik içerik. | B6 (B1'de önceki görünüm) |
| Raporlar | `reports/ReportsExportView.tsx`, `exportService.ts` | Ekrandaki 300 satırı tarayıcıda yeniden kodluyordu. | **B1: Kayıt ve rapor** (Python `export_logs`, `export_session_report`) + B7 |
| Ayarlar | `settings/*` | Python'a bağlı (bulut, lisans, kaynak lisansları, kullanım modu). | B6 (B1'de önceki görünüm) |
| Simülatör stüdyosu | `modals/SimulatorStudioModal.tsx`, `canSimulator.ts` | Tarayıcı simülatörü. | B1'de yerine **gerçek simüle araç hattı** (§4) |

Python tarafında bulunan iki ek sorun (B1 ekranı bunları kullanmaz; düzeltme B4'te):

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

## 5. Doğrulama durumu

| Ne | Nasıl doğrulandı |
|---|---|
| Simüle hat bağlama/çözme, ret koşulları, sürücü sayaçları | `tests/unit/test_workbench_simulator.py` (CI, Linux + Windows) |
| Gerçek köprü + gerçek telemetri döngüsü + simüle kamyon → Canlı trafik, filtre, dışa aktarma, E-Stop | `tests/ui_e2e/test_workbench_ui.py` (Playwright; yerelde koşar, CI'da Playwright yok → atlanır) |
| Fiziksel adaptör (PCAN/Kvaser/RP1210) ile Canlı trafik | **Doğrulanmadı.** `HARDWARE_TEST_CHECKLIST.md` W8 |
| WebView2 (Windows) içinde görünüm ve 2000 kare/sn akışta akıcılık | **Doğrulanmadı.** `HARDWARE_TEST_CHECKLIST.md` W8 |

Ekran görüntüleri (`screenshots/workbench/`) simülatörle, Linux Chromium'da alınmıştır.

## 6. Modül sırası (her biri ayrı dal + PR, öncekiler birleşmeden sonraki başlamaz)

| Aşama | Dal | İçerik |
|---|---|---|
| B1 | `flow/b1-workbench` | Kabuk, Canlı trafik, Kayıt ve rapor (temel), simüle hat, bu belge |
| B2 | `flow/b2-plot` | Grafik: Python'un çözdüğü sinyaller için okuma ucu + çizim |
| B3 | `flow/b3-discovery` | Sinyal keşfi Python `discovery_*` üzerine; TS motoru silinir |
| B4 | `flow/b4-assistant` | Teşhis asistanı Python analiz üzerine; `_bus_load` / `_error_count` düzeltmesi |
| B5 | `flow/b5-ecu` | ECU programlama ekranı (aynı güvenlik akışı) |
| B6 | `flow/b6-settings` | Ayarlar (adaptör bağlantısı, lisans, mod, kaynak lisansları) + Pin rehberi |
| B7 | `flow/b7-records` | Kayıttan oynatma, klasörü aç, buluta yükleme (açık onayla) |
| B8 | `flow/b8-cleanup` | Eski `App.tsx`, dashboard, modallar, TS motorları silinir; ön yüz lint tabanı sıfırlanır ve CI'daki `continue-on-error` kaldırılır |

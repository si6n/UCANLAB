# UCANLAB Kapsamlı Kod Review — Aşama 6

**İncelenen revizyon:** `011b83599836c7f5b9b51c0f946565684d505ca8`  
**Kapsam:** OBD/UDS tek-seferlik teşhis polling akışı; Desktop bridge challenge/aksiyon yetkilendirmesi; OEM J1939 canlı decoder entegrasyonu; Signal Discovery kanal/format izolasyonu.  
**İnceleme yöntemi:** Bulguyu içeren dosyalar tam okundu; hedefli davranış probları çalıştırıldı; kaynak koduna veya testlere hiçbir düzeltme uygulanmadı.

## Doğrulama özeti

- Hedefli testler: **81 passed**
  - `tests/e2e/test_challenger_diagnostics.py`
  - `tests/unit/test_desktop_app.py`
  - `tests/unit/test_dbc_decoder.py`
  - `tests/unit/test_signal_discovery.py`
- Ruff: **All checks passed** (`src`, `tests`)
- Bulgular aşağıdaki kontrollü problarla yeniden üretildi.
- Çalışma ağacında kaynak/test değişikliği yok; yalnızca bu rapor dosyası oluşturuldu.

---

## H1 — Diagnostic challenge token aksiyon parametrelerine bağlanmıyor

**Önem:** Yüksek  
**Güven:** Doğrulandı  
**Dosya/satırlar:**

- `src/ui/desktop_app.py:1822-1864` — challenge yalnızca `action_type` ve `action_id` saklıyor.
- `src/ui/desktop_app.py:1866-1895` — token doğrulaması yalnızca bu iki alanı karşılaştırıyor.
- `src/ui/desktop_app.py:1996` sonrası — yürütülecek `params` doğrudan ikinci çağrının `action` nesnesinden okunuyor; ör. `routine_id`, `group`, `session_type`, `reset_type`, `destination_address`.

### Somut kanıt

Bir challenge `uds_routine` / `routine-card-1` kimliğiyle `routine_id=0x1000` için alındı. Aynı token, aynı `action_type` ve `id` korunup `routine_id=0xD001` yapılarak gönderildi. Backend değişmiş parametreyi kabul etti:

```text
challenge_action: uds_routine routine-card-1
mutated_routine_accepted: True
executed_routine_id: 0xd001
```

Ayrıca `action_id` boş challenge ile oluşturulursa şu koşul nedeniyle (`if action_id and challenge.action_id and ...`) aynı türde farklı kimlikli bir aksiyonun da kabul edilmesi mümkün.

### Etki

Renderer/WebView içindeki kötü niyetli veya ele geçirilmiş JavaScript, operatörün onayladığı aksiyon türü/kart kimliği korunurken gerçek parametreleri değiştirebilir. Fiziksel modda bu durum farklı bir UDS rutininin, farklı reset/session tipinin, farklı DTC grubunun veya farklı J1939 hedef adresinin operatörün gördüğü aksiyondan farklı yürütülmesine yol açabilir. Bu, “onaylanan komut = gönderilen komut” invariantını kırıyor.

### Önerilen çözüm — uygulanmadı

1. Challenge oluşturulurken güvenlik açısından anlamlı alanların canonical JSON gösterimini üretin: `action_type`, `id`, normalize edilmiş ve allowlist/range doğrulamasından geçmiş `params`.
2. Challenge kaydında bu canonical payload’ın kriptografik özeti veya tamamı tutulmalı; tüketim sırasında gelen aksiyon aynı canonical payload ile birebir eşleşmeden token kabul edilmemeli.
3. `action_id` boşsa challenge üretimini reddedin; boş kimlik wildcard olmamalı.
4. Challenge eşleşmesi başarısız olduğunda token tüketilmiş kalmalı; parametre aralıkları ve izinli komut/rutin listesi ayrıca backend’de doğrulanmalı.
5. Bu alanlar için negatif test eklenmeli: aynı token ile `routine_id`, `target_address`, `group`, `reset_type`, `session_type` değişiklikleri reddedilmeli.

---

## H2 — OBD/UDS one-shot polling yabancı kanaldaki aynı arbitration ID yanıtını kabul ediyor

**Önem:** Orta-Yüksek  
**Güven:** Doğrulandı  
**Dosya/satırlar:**

- `src/protocols/obd/poller.py:697-760` — `_await_diagnostic_response()` yanıtı yalnızca `arbitration_id` ile filtreliyor.
- `src/protocols/obd/poller.py:748-755` — `frame.channel_id` hiç kontrol edilmeden ISO-TP reassembly ve eşleşme yapılıyor.
- `src/protocols/obd/poller.py:817-867` — functional OBD polling döngüsünde de yalnızca response ID kontrol ediliyor.

### Somut kanıt

Poller `channel_id='obd_ch0'` ile oluşturuldu. `0x7E8` yanıtı `other_ch` kanalından enjekte edildi. Yanıt gerçek kabul edildi:

```text
foreign_channel_result: 1024.0 True None
tx_count: 1
```

### Etki

Aynı process içinde birden fazla CAN kanalı veya tüm kanalları besleyen ortak `RxSubscription` kullanıldığında başka fiziksel ağdaki ECU cevabı, beklenen ECU yanıtı gibi raporlanabilir. Bu, teşhis sonucunun yanlış ECU’ya bağlanmasına ve yanlış ölçüm/DTC/VIN raporlanmasına neden olabilir. Multi-frame durumda yabancı kanalın First Frame/Consecutive Frame parçaları da aynı transport akışına girebilir.

### Önerilen çözüm — uygulanmadı

1. `_await_diagnostic_response()` ve `poll_pid_functional()` içinde `frame.channel_id == self.channel_id` kontrolünü reassembly’den önce zorunlu kılın.
2. Bir poller birden fazla kanalı destekleyecekse kanal, `(channel_id, rx_id, tx_id)` conversation anahtarının parçası olmalı; yalnızca arbitration ID ile transport seçilmemeli.
3. Flow Control frame’lerinin de doğru `channel_id` ile üretildiği ve gönderildiği doğrulanmalı.
4. Aynı arbitration ID’yi iki kanalda taşıyan negatif test ve multi-frame çapraz kanal testi eklenmeli.

---

## H3 — OEM J1939 canlı telemetry entegrasyonu dictionary key’ini signal nesnesi sanıyor

**Önem:** Yüksek  
**Güven:** Doğrulandı  
**Dosya/satırlar:**

- `src/protocols/j1939/oem/registry.py:139-145` — `OemDecodedPayload.signals` tipi `dict[str, DecodedSignal]`.
- `src/ui/desktop_app.py:3133-3148` — `for sig in oem_payload.signals:` ile dictionary key’i (`str`) dönülüyor; hemen ardından `sig.name` erişimi yapılıyor.
- `src/ui/desktop_app.py:3149-3150` — oluşan `AttributeError` geniş canlı decode try/except bloğunda yakalanıp yalnızca debug seviyesinde bastırılıyor.

### Somut kanıt

Volvo proprietary PGN `0xFF46` için registry doğrudan geçerli bir `OemDecodedPayload` ve signal sözlüğü döndürüyor. Aynı frame `_decode_j1939_signal()` içine verildiğinde canlı kayıt callback’i hiç çağrılmadı:

```text
registry returned OemDecodedPayload(... signals={...})
captured []
```

Bu davranış, sözlükteki ilk anahtar string olduğu için `sig.name` erişiminde hata oluşması ve hatanın sessizce yutulmasıyla uyumlu.

### Etki

Cummins, Caterpillar, Scania, Volvo, Detroit ve Actros OEM decoder’ları “destekleniyor” görünmesine rağmen proprietary J1939 sinyalleri canlı evidence/session akışına girmiyor. Aynı nedenle OEM motor devri, soğutma sıcaklığı ve boost sinyalleri güncellenmiyor; teşhis analizi bu veri yokluğunu nominal/boş telemetri gibi görebiliyor. Hata log seviyesi debug olduğu için operatör bunu çalışma zamanında fark etmeyebilir.

### Önerilen çözüm — uygulanmadı

1. Dictionary değerleri üzerinde iterasyon yapılmalı; her `DecodedSignal` nesnesi ayrı işlenmeli.
2. `sig.is_valid` / `sig.status` kontrolü yapılmadan değer evidence/session veya interlock state’ine aktarılmamalı.
3. Bu yol için her OEM’den en az bir geçerli frame ile canlı callback, session sample, RPM/temperature/boost güncelleme testleri eklenmeli.
4. Decoder hataları yalnızca debug’a bırakılmamalı; sayılabilir ve operatörün görebileceği telemetry-decode health metriğine bağlanmalı.

---

## M1 — OEM sentinel/geçersizlik durumu canlı evidence katmanına aktarılmaya hazır değil

**Önem:** Orta  
**Güven:** Kod + decoder çıktısı ile doğrulandı; H3 nedeniyle mevcut akışta önceki hata tarafından maskeleniyor.  
**Dosya/satırlar:**

- `src/protocols/j1939/oem/registry.py:145-160` — signal geçerliliği ayrı `is_valid` alanında tutuluyor.
- `src/protocols/j1939/oem/volvo.py:104-123` — `0xFFFF`/`0xFFFE` sentinel’leri `is_valid=False` olsa da fiziksel `value=0.0` ile temsil ediliyor.
- `src/ui/desktop_app.py:3137-3148` — değer aktarımı tasarım olarak `is_valid` kontrolü yapmıyor.

### Somut kanıt

Volvo DPF soot alanına `0xFFFF` verildiğinde decoder çıktısı:

```text
invalid_signal: volvo_dpf_soot_accumulation_level value= 0.0 is_valid= False status= SignalStatus.NOT_AVAILABLE
```

Bu yaklaşım, H3’teki dictionary iterasyon hatası düzeltilip aynı aktarım kodu çalışır hâle geldiğinde `0.0 g` değerinin gerçek ölçüm gibi session’a yazılmasına ve geçersiz motor/ısı/boost değerlerinin state’e alınmasına izin verecek. Bu nedenle H3’ün çözüm tasarımında birlikte ele alınması gerekiyor; mevcut revizyonda H3 nedeniyle bu spesifik aktarım etkisi çalıştırma yolunda maskeli.

### Önerilen çözüm — uygulanmadı

- Geçersiz/sentinel signal’lar evidence örneği olarak hiç yazılmamalı veya açıkça `NOT_AVAILABLE/ERROR` kalitesiyle ayrı bir kayıt türünde tutulmalı.
- `is_valid` olmayan signal’lar `_current_rpm`, `_current_temp`, `_current_boost` gibi güvenlik/analiz state’lerini güncellememeli.
- `0.0` gibi sentinel yerine `None` veya kalite taşıyan bir tip tercih edilmeli; “geçersiz ama sıfır” ile “gerçek sıfır” ayrımı kaybolmamalı.

---

## H4 — Signal Discovery ingest sırasında ayırdığı kanalları analyze aşamasında tekrar birleştiriyor

**Önem:** Yüksek  
**Güven:** Doğrulandı  
**Dosya/satırlar:**

- `src/engine/discovery/engine.py:38-43` — frame bucket anahtarı `(channel_id, is_extended, arbitration_id)`.
- `src/engine/discovery/engine.py:111-120` — `analyze_id()` tüm bucket’lardan aynı numeric ID’yi tek `frames` listesinde birleştiriyor.
- `src/engine/discovery/engine.py:126-180` — birleşik liste üzerinden DLC, rate, entropy, counter/checksum ve signal hipotezleri çıkarılıyor.
- `src/ui/desktop_app.py` bridge API’si `discovery_analyze_id(arb_id)` olarak yalnızca numeric ID alıyor; kanal/format seçemiyor.

### Somut kanıt

Aynı `0x100` ID’si `can0` ve `can1` üzerinde ayrı ayrı 5 frame olarak ingest edildi. İç bucket sayısı iki kalırken rapor bunları tek seri saydı:

```text
stored_bucket_count: 2
report_frame_count: 10
report_dlc: 8
report_rate_hz: 2500.0
first_hypotheses: [('SIG_B0_16B', 0.0, 255.0), ...]
```

İki kanalın zaman serileri birbirinden bağımsız olmasına rağmen birleşik seri üzerinde `0..4` ile `255..251` değerleri aynı sinyalin aralığıymış gibi değerlendirildi.

### Etki

Discovery, farklı CAN ağlarından veya classic/extended formatlarından gelen aynı numeric ID’yi tek fiziksel mesaj sanabilir. Sonuç olarak yanlış entropy/rate/sinyal sınırı ve yanlış otomatik DBC üretilebilir. Bu DBC daha sonra canlı decoder’a yüklenirse yanlış scaling/sinyal yorumları oluşturur; özellikle aynı ID’nin iki araç/ağ profilinde farklı anlamı olduğu durumlarda analiz güvenilmez hâle gelir.

### Önerilen çözüm — uygulanmadı

1. Rapor kimliği `(channel_id, is_extended, arbitration_id)` olmalı; `analyze_id()` bu anahtarı istemeli veya ambiguity varsa açıkça hata dönmeli.
2. UI/API’de kanal ve frame formatı seçimi eklenmeli; yalnızca numeric ID ile birden fazla bucket birleştirilmemeli.
3. `_reports_cache` anahtarı ile gerçek bucket anahtarı aynı sözleşmeye getirilmeli; şu an cache annotation tuple iken `arb_id` ile indeksleniyor.
4. Aynı numeric ID’yi iki kanalda taşıyan regresyon testi, rapor frame count/rate/hypothesis ayrışmasını zorunlu kılmalı.

---

## Önceliklendirilmiş çözüm sırası

1. **H1:** Challenge payload’ını tam aksiyon parametrelerine bağlayın; fiziksel diagnostic komutlarda parametre değişikliği reddedilmeden güvenlik kapısı tamamlanmış sayılmamalı.
2. **H3:** OEM canlı decoder entegrasyonundaki dictionary iteration hatasını ve hata görünürlüğünü düzeltin; aksi hâlde OEM desteği canlı akışta fiilen kapalı.
3. **H4:** Discovery raporlarını kanal/format bazında ayırın; yanlış DBC üretimi veri güvenilirliğini doğrudan bozuyor.
4. **H2:** One-shot diagnostic response kanal izolasyonunu zorunlu kılın.
5. **M1:** OEM invalid/sentinel kalitesini evidence ve state aktarımında koruyun.

## Aşama sonucu

Aşama 6 tamamlandı ve burada duruyorum. **Hiçbir bulgu uygulanmadı; yalnızca review ve raporlama yapıldı.**

# UCANLAB Kod İncelemesi — Aşama 2

**İnceleme odağı:** Safety çekirdeği, HAL yaşam döngüsü, UDS client/flasher ve reconnect davranışı.

**İncelenen revision:** `011b83599836c7f5b9b51c0f946565684d505ca8`

**Durum:** Kaynak koduna patch uygulanmadı. Bu aşama tamamlandı ve inceleme burada durduruldu.

## 1. Okunan dosyalar

Önceki aşamadaki bulguları uydurmadan doğrulamak için bulguların bulunduğu dosyalar tam olarak okundu:

- `src/ui/desktop_app.py` — satır 1–3689
- `src/safety/gateway.py` — satır 1–1701
- `src/safety/state_machine.py` — satır 1–653
- `src/safety/watchdog.py` — satır 1–325
- `src/safety/estop.py` — satır 1–913
- `src/hal/base.py` — satır 1–130
- `src/hal/tx_port.py`
- `src/hal/can_interface.py`
- `src/hal/rp1210/bus.py` — satır 1–339
- `src/hal/drivers/pcan_kvaser.py` — satır 1–412
- `src/protocols/uds/client.py` — satır 1–970
- `src/protocols/uds/flasher.py` — satır 1–1032

Ayrıca ilgili safety, HAL, UDS, flashing, e2e ve regression testleri çalıştırıldı.

## 2. Doğrulama sonuçları

### Statik analiz

```text
ruff check kritik dosyalar: All checks passed!
```

### Hedefli testler

```text
Safety/Desktop/Flasher/Review Round 3: 106 passed
Gateway/StateMachine/Watchdog/EStop/RP1210/PythonCanBus: 109 passed
```

### Tam test süiti

```text
2040 passed, 4 skipped, 1 warning
```

Testlerin geçmesi olumlu; ancak aşağıdaki bulguların bazıları mevcut regression testlerinin kapsamadığı yaşam döngüsü veya transaction invariants'larıdır.

## 3. Önceki aşama bulgularının çalışan kodla doğrulanması

### [HIGH] H1 — `arm_tx()` supervisor hatasında driver'ı aktif bırakıyor

**Kod:** `src/ui/desktop_app.py:1577-1592`

Gerçek akış:

1. `_set_driver_listen_only(False)` başarılı olursa fiziksel driver TX-capable hale geliyor.
2. `self.supervisor.arm_tx(...)` bundan sonra çağrılıyor.
3. Supervisor hata verirse `except` yalnızca `{"success": False, ...}` döndürüyor.
4. Driver'ı tekrar listen-only moda alan rollback bulunmuyor.

Kontrollü test double ile doğrulanan çıktı:

```text
H1 result: {'success': False, 'error': 'injected supervisor arm failure'}
H1 listen_only after failed arm: False
```

Bu, rapordaki bulgunun doğrudan kaynak davranışıyla doğrulandığını gösteriyor. `success=False` döndüğü halde driver aktif kalıyor.

**Çözüm:**

- Driver geçişini transaction olarak ele alın.
- `supervisor.arm_tx()` başarısızsa aynı `_bus_lock` altında `_set_driver_listen_only(True)` çalıştırın.
- Rollback başarısızsa `SafetySupervisor` FAULT ve E-Stop zincirini tetikleyin.
- Başarılı dönüşten önce şu invariantları birlikte doğrulayın:
  - `supervisor.is_tx_permitted is True`
  - driver `listen_only is False`
  - bus bağlantısı aktif
- `test_arm_driver_rolls_back_when_supervisor_rejects` ve `test_arm_rollback_failure_forces_fault` testlerini ekleyin.

---

### [HIGH] H2 — Flashing iptali/hatası sonrası TX disarm garanti edilmiyor

**Kod:**

- `src/ui/desktop_app.py:2538-2604`
- `src/ui/desktop_app.py:2630-2638`

`flash_start()` gerçek flashing worker'ını başlatmadan önce `arm_tx()` çağırıyor. Buna karşın:

- `_real_flash_worker()` `finally` bloğunda sadece UI progress yayımlıyor.
- `flash_cancel()` yalnızca `self.flashing_engine.cancel()` çağırıyor.
- `flash_cancel()` veya worker cleanup içinde `disarm_tx()` çağrılmıyor.
- Simülasyon worker'ında da tamamlanma/iptal sonrası ortak TX cleanup yok.

Kontrollü test double ile doğrulanan çıktı:

```text
H2 cancel result: {'success': True, 'message': 'Flashing iptal edildi.'}
H2 supervisor state: ARMED_TX
H2 listen_only after cancel: False
```

Yani iptal başarılı olarak raporlanırken hem supervisor hâlâ `ARMED_TX`, hem de driver aktif durumda kalabiliyor.

**Çözüm:**

- Gerçek ve simülasyon flashing yolları için ortak `_finish_flash_transaction()` cleanup fonksiyonu oluşturun.
- Worker'ın `finally` bloğunda her durumda:
  1. tester-present thread'ini durdurun,
  2. UDS recovery/release sonucunu kaydedin,
  3. `disarm_tx()` çağırın,
  4. driver'ın listen-only olduğunu doğrulayın,
  5. cleanup başarısızsa FAULT/E-Stop tetikleyin.
- `flash_cancel()` yalnızca cancellation flag set etmeli; protokol güvenli sınırda durduktan sonra worker cleanup yapmalı.
- `cleanup_failed` ayrı ve alarm üreten bir status olmalı.
- `test_flash_cancel_disarms_tx` ve `test_flash_worker_exception_disarms_tx` eklenmeli.

---

### [HIGH / tehdit modeline bağlı] H3 — ISO-TP multi-frame kritik işlemlerde HMAC token yalnızca First Frame'e uygulanıyor

**Kod:**

- `src/protocols/uds/client.py:476-528`
- `src/protocols/uds/client.py:650-705`
- `src/safety/gateway.py:960-997`

`_send_payload()` multi-frame mesajlarda `confirmation_token` değerini yalnızca First Frame gönderimine geçiriyor:

```python
self._tx_frame(frames[0], ..., confirmation_token=confirmation_token)
self._send_consecutive_frames_flow_controlled(
    frames[1:], payload, is_critical_command, user_confirmed
)
```

Consecutive Frame gönderimleri token/context olmadan yapılıyor. Gateway de ISO-TP CF frame'lerini PCI nibble `0x2` nedeniyle kritik olarak sınıflandırmıyor; mevcut test açıkça CF'lerin kritik sayılmaması gerektiğini doğruluyor (`tests/unit/test_t47_tx_review_fixes.py:222-228`).

Bu tasarım ISO-TP açısından anlaşılır; ancak flashing gibi bir UDS yazma transaction'ı için güvenlik sonucu şudur: HMAC doğrulaması tüm transferi değil, yalnızca First Frame'i kapsıyor. İn-process bir çağıran CF gönderme yetkisine ulaşabilirse, CF'ler aynı operator token'ını sunmadan gateway'den geçebilir.

Bu bulgu renderer'ın tek başına uzaktan istismar edebildiği kanıtlanmış bir açık değildir; fakat “kritik flashing adımlarının gateway tokenıyla korunması” iddiasıyla mevcut uygulama arasında kapsam boşluğu vardır.

**Çözüm seçenekleri:**

1. Gateway içinde ISO-TP transaction authorization lease oluşturun:
   - kritik FF doğrulanınca `(channel, tx_id, rx_id, transaction_id, payload_digest, expiry)` kaydedilsin,
   - yalnızca doğru sıra numaralı CF'ler bu lease ile geçsin,
   - FC/timeout/abort/complete durumlarında lease kapatılsın.
2. Alternatif olarak kritik UDS payload için tek bir “session authorization” context'i üretip FF ve bütün CF'lerde içsel olarak taşıyın; aynı tokenı tekrar tüketilebilir HMAC token gibi kullanmayın.
3. Unsolicited CF, yanlış transaction, yanlış uzunluk veya sıra numarası durumunda gateway TX'i reddetmeli ve lease'i iptal etmeli.

**Ek testler:**

- Tokenlı FF sonrası doğru transaction'a ait CF train geçer.
- Token olmadan başlayan CF reddedilir.
- Aynı arbitration ID'de başka payload'a ait CF reddedilir.
- Yanlış sequence number veya FC timeout sonrası kalan CF'ler reddedilir.
- Transfer tamamlandıktan sonra eski lease ile CF gönderilemez.

---

### [MEDIUM] M1 — `rebind_bus()` global aggregate rate window'ını temizlemiyor

**Kod:** `src/safety/gateway.py:468-498`

`rebind_bus()` `_tx_timestamps` ve bazı streak sayaçlarını temizliyor; fakat global envelope için kullanılan:

- `_tx_total_timestamps`
- `_total_overload_streak`

temizlenmiyor. Buna rağmen docstring “new channel starts clean” diyor.

Bu gerçek davranışla doğrulandı:

```text
before_rebind_total_stamps: 400
after_rebind_lane_stamps: 0
after_rebind_total_stamps: 400
next_send_after_rebind: RateLimitExceededError Global TX envelope exceeded (400 msg/s across all lanes)
```

Sonuç: Eski fiziksel kanaldaki trafik yeni kanala taşınıyor ve reconnect sonrası ilk TX'ler global rate limit tarafından reddedilebiliyor. Bu doğrudan güvenlik bypass'ı değil; fakat reconnect/availability davranışını bozan ve “yeni kanal temiz başlar” sözleşmesini ihlal eden bir bug.

**Çözüm:** Bus rebind transaction'ında aynı lock altında şunları da temizleyin:

```python
self._tx_total_timestamps.clear()
self._total_overload_streak = 0
```

Buna karşılık reconnect sırasında eski channel'a ait token bucket state'lerinin de sıfırlanıp sıfırlanmayacağı açık bir politika olarak test edilmeli. Minimum test:

- Eski bus global envelope'ı doldurur.
- `rebind_bus(new_bus)` çağrılır.
- Yeni bus'ın ilk normal TX'i global limit nedeniyle reddedilmez.

---

### [MEDIUM] M2 — Bus reconnect'lerinde E-Stop abort hook'ları birikiyor

**Kod:**

- `src/safety/gateway.py:389-403`
- `src/safety/gateway.py:496-498`
- `src/safety/estop.py:515-522`

Her `rebind_bus()` sonrasında yeni bus'ın flush hook'u `EmergencyStopSystem._abort_hooks` listesine ekleniyor. Eski hook kaldırılmıyor; çünkü unregister API yok.

Kontrollü flushable bus ile doğrulanan çıktı:

```text
hooks_after_construct: 1
hooks_after_rebind: 2
hooks_after_rebind: 3
hooks_after_rebind: 4
```

Sonuçlar:

- Her E-Stop'ta artık kullanılmayan eski bus hook'ları da çağrılıyor.
- Eski bus nesneleri closure bound-method referansları nedeniyle tutulabilir.
- Çok sayıda reconnect sonrası callback maliyeti ve bellek kullanımı artar.
- Eski sürücüye ait cleanup çağrıları anlamsız veya yan etkili olabilir.

**Çözüm:**

- `EmergencyStopSystem` içine hook handle/token döndüren `register_abort_hook()` ve `unregister_abort_hook()` sözleşmesi ekleyin.
- Gateway mevcut hook handle'ını saklasın.
- `rebind_bus()` önce eski hook'u kaldırsın, sonra yeni bus hook'unu eklesin.
- EStop callback/abort hook listesi için reconnect sonrası sabit boyut invariant testi ekleyin.

---

### [MEDIUM] M3 — `PythonCanBus.recv()` ile `disconnect()` arasında receive-lifetime koruması yok

**Kod:** `src/hal/drivers/pcan_kvaser.py:194-224` ve `:283-310`

`recv()` lifecycle lock altında yalnızca handle snapshot alıyor; sonra lock'u bırakıp `bus_snapshot.recv(...)` çağırıyor. `disconnect()` ise aynı anda:

1. `is_connected=False` yapabiliyor,
2. `_bus.shutdown()` çağırabiliyor,
3. `_bus=None` yapabiliyor.

`disconnect()` aktif send'leri `_active_sends` ile bekliyor; fakat aktif receive sayısını takip etmiyor. Docstring “concurrent disconnect cannot free the bus mid-call” diyor, ancak mevcut kod receive çağrısını drain etmiyor.

Muhtemel sonuçlar:

- Vendor handle kapanırken `recv()` devam eder.
- C tabanlı sürücüde yarış, `OSError/RuntimeError` veya nondeterministic frame kaybı oluşabilir.
- Telemetry loop bunu hardware disconnect gibi yorumlayıp safety fault zincirini tetikleyebilir.

**Çözüm:**

- `_active_recvs` sayacı ekleyin ve send/receive için ortak drain deadline kullanın.
- `disconnect()` hem `_active_sends` hem `_active_recvs` sıfırlanana kadar beklesin.
- Deadline aşılırsa sürücüye özel zorunlu shutdown davranışı açıkça “forced close” olarak işaretlenmeli ve üst katmana deterministic fault raporlanmalı.
- Gerçek vendor mock'unda blocked `recv()` + concurrent `disconnect()` regression testi ekleyin.

Aynı yaşam döngüsü sözleşmesi RP1210 client için de gözden geçirilmeli; `RP1210Bus.recv()` ve `disconnect()` arasında benzer client-handle yarış ihtimali var.

---

### [MEDIUM] M4 — E-Stop secret rotation path'i provider I/O'yu lock altında çalıştırabiliyor

**Kod:** `src/safety/estop.py:333-356` ve `:789-791`

`_get_secret()` açıklamasında provider I/O'nun E-Stop lock'u altında yapılmaması gerektiği yazıyor. Ancak provider revision değişmişse `_get_secret()` içinde `_load_secret()` tekrar çağrılıyor. `reset()` bunu `with self._lock` bloğu içinde çağırıyor.

Dolayısıyla secret rotation anında yavaş/bozuk secret provider:

- E-Stop reset akışını bloklayabilir,
- aynı lock'u kullanan `trigger()` ve `is_engaged` erişimlerini geciktirebilir,
- E-Stop responsiveness hedefini bozabilir.

**Çözüm:**

- Secret rotation'ı `refresh_secret()` gibi lock dışı I/O yapan tek kontrollü akışa indirin.
- `_get_secret()` yalnızca önceden yüklenmiş immutable cache'i okusun.
- Revision değişikliği tespit edilirse reset sırasında I/O yapmak yerine fail-closed `ESTOP_SECRET_ROTATION_PENDING` döndürün veya ayrı refresh worker'ı ile cache'i önceden güncelleyin.
- Provider'ın bloklandığı concurrency testi ekleyin; `trigger()` çağrısının secret store yüzünden beklemediğini doğrulayın.

## 4. Öncelikli düzeltme sırası

1. **H1:** arm failure rollback ve rollback-failure FAULT/E-Stop.
2. **H2:** flashing tamamlanma/iptal/hata için ortak disarm cleanup.
3. **H3:** ISO-TP multi-frame transaction authorization/lease modeli.
4. **M1:** Reconnect'te global rate window ve overload state temizliği.
5. **M2:** Abort hook unregister ve reconnect leak düzeltmesi.
6. **M3:** HAL receive/send lifetime drain sözleşmesi.
7. **M4:** E-Stop secret cache rotation'ını lock dışına taşıma.

## 5. Aşama 2 sonucu

Bu aşamada önceki iki yüksek öncelikli bulgu çalışan kodla tekrar doğrulandı. Ayrıca dört yeni gerçek kod bulgusu tespit edildi:

- Multi-frame flashing authorization kapsam boşluğu
- Reconnect sonrası global rate window sızıntısı
- Reconnect sonrası abort hook birikimi
- HAL receive/disconnect yaşam döngüsü yarışı
- E-Stop secret rotation lock/I/O problemi

Kaynak koduna değişiklik yapılmadı. İnceleme bu noktada durduruldu.

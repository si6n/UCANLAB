# UCANLAB Kod Review — Aşama 5

- **İncelenen revizyon:** `011b83599836c7f5b9b51c0f946565684d505ca8`
- **Kapsam:** E2E safety profilleri ve validator, Tx gateway E2E wiring, ring/rolling blackbox buffer, FrameRouter, SafeMultiplexedBus, RP1210 HAL, temel `CanFrame` modeli ve exporter sınırları.
- **Yaklaşım:** Bulgularda kullanılan dosyaların tamamı okundu; hedef testler çalıştırıldı; şüpheli davranışlar izole probe'larla doğrulandı.
- **Kısıt:** Kaynak kodu, testler ve konfigürasyon değiştirilmedi. Bu aşama yalnızca review'dur.

## Yönetici özeti

Bu aşamada 1 yüksek/orta etkili ve 2 orta önem seviyesinde bulgu doğrulandı. En önemli risk, E2E stream state eviction kararının frame içindeki güvenilmeyen timestamp'e göre verilmesi ve state silindikten sonra yeni frame'in `INITIAL` olarak kabul edilmesidir. Ayrıca geçersiz counter maskeleri configuration-time'da reddedilmiyor ve rolling blackbox HMAC anahtarı vault'a yazılamadığında ephemeral anahtarla devam ediliyor.

## Bulgular

### H1 — E2E stream eviction, güvenilmeyen frame timestamp'iyle yapıldığı için continuity state'i silinip `INITIAL` kabulü oluşabiliyor

**Önem:** HIGH/MEDIUM (özellikle replay veya yazılım içi injection yolu frame timestamp'ini kontrol edebiliyorsa)  
**Dosya:** `src/safety/e2e/validator.py:106-132`

**Kanıt:**

`validate()` frame'in `timestamp_ns` değerini doğrudan `validate_raw()`'a geçiriyor. Stream tablosu dolduğunda eviction şu değerle yapılıyor:

```python
oldest_key = min(
    self._streams,
    key=lambda k: self._streams[k].last_timestamp_ns,
)
```

`last_timestamp_ns` frame'den gelen timestamp; validator'ın kendi arrival-time/monotonic zamanı değil.

İzole probe:

```text
legit_state_after_fill: None
post_eviction_verdict: INITIAL is_valid: True previous: None
```

Meşru stream `timestamp_ns=0` ile başlatıldı. 1024 farklı stream `timestamp_ns=10**18` ile dolduruldu. Meşru stream eviction ile silindi; aynı stream'in sonraki counter=1 frame'i `INITIAL` ve `is_valid=True` olarak kabul edildi. Böylece continuity geçmişi kayboldu.

Bu durum fiziksel CAN sürücüsünün host timestamp'i güvenilir biçimde ürettiği normal akışta daha düşük olasılıktadır; ancak replay, virtual bus, test/injection veya timestamp taşıyan başka bir yazılım yolu için doğrudan geçerlidir. E2E validator'ın görevi replay/sequence koruması olduğundan stream state eviction'ın untrusted metadata'ya bağlanması güvenli değildir.

**Çözüm:**

1. `StreamRxState` içine validator'ın kendi `last_seen_monotonic_ns` alanını ekleyin.
2. Eviction yalnızca `time.monotonic_ns()` ile ölçülen arrival zamanına göre yapılsın.
3. Frame timestamp'i raporlama/telemetri amacıyla tutulabilir, fakat güvenlik state eviction kararı için kullanılmamalı.
4. State eviction sonrası yeni stream'in `INITIAL` kabul edilmesi politika gereğiyse, en azından “continuity lost” işareti üretin ve güvenlik tüketicilerinin bunu `is_valid=True` gibi kullanmasını engelleyin.
5. Test ekleyin: çok sayıda stream ile tablo doldurulurken frame timestamp'leri geçmiş/gelecek değerlere sahip olsa bile eviction gerçek arrival sırasına göre olmalı.

---

### M1 — Geçersiz E2E counter maskesi kabul ediliyor ve gateway yalnızca `profile_type` kontrol ediyor

**Önem:** MEDIUM  
**Dosyalar:**

- `src/safety/e2e/profiles.py:91-129`
- `src/safety/gateway.py:195-208`

**Kanıt:**

`E2EProfileConfig.__post_init__()` offset, modulo ve profile type kontrolleri yapıyor; ancak `counter_bit_mask` ile `counter_bit_shift` ilişkisinin ve maskenin temsil ettiği değer aralığının geçerliliğini kontrol etmiyor. Gateway de yalnızca `profile_type` enum kontrolü yapıyor.

Aşağıdaki yapı kabul ediliyor:

```python
counter_bit_mask=0x03
counter_bit_shift=0
counter_modulo=16
```

Probe sonucu:

```text
invalid_mask_profile_constructed: True
counter_field 0 validator_counter 0 verdict INITIAL
counter_field 1 validator_counter 1 verdict OK
counter_field 2 validator_counter 2 verdict OK
counter_field 3 validator_counter 3 verdict OK
counter_field 0 validator_counter 0 verdict WRONG_SEQUENCE
```

Packager counter=4 üretmeye çalışırken yalnızca 2 bitlik alan counter'ı 0'a kırpıyor; validator bunu tekrar/yanlış sequence olarak değerlendiriyor. Bu profil gateway'e bağlanabildiği için üretim TX akışında E2E koruması öngörülebilir biçimde çalışmayabilir.

**Çözüm:**

- `counter_bit_mask` için `0..0xFF`, sıfır olmayan ve bit alanı olarak geçerli bir değer zorunlu kılın.
- Maskenin contiguous bit alanı olmasını ve `counter_bit_shift` ile uyumlu olmasını doğrulayın.
- `counter_modulo <= (mask >> shift) + 1` ve ilgili profilin standardındaki counter genişliği kuralını enforce edin.
- `counter_bit_shift` için negatif/>7 ve maskenin dışına taşan değerleri reddedin.
- Gateway profile validation'ı yalnızca enum yerine `E2EProfileConfig` invariant'larının tamamını doğrulasın.
- Test ekleyin: `mask=0x03, modulo=16`, `mask=0`, `shift=8`, dağınık maskeler ve modulo/bit-width uyuşmazlıkları construction/wiring aşamasında reddedilmeli.

---

### M2 — Rolling blackbox HMAC anahtarı vault'a yazılamazsa ephemeral anahtarla devam ediliyor

**Önem:** MEDIUM (blackbox kayıtlarının sonraki restart'ta okunamaz hale gelmesi ve forensic veri kaybı)  
**Dosya:** `src/engine/buffer/rolling_disk.py:60-91`

**Kanıt:**

`_get_hmac_key()` key bulunamadığında yeni 32-byte key üretiyor. `store_secret()` başarısız olursa yalnızca warning log'layıp yine de `(key, True)` döndürüyor:

```python
except Exception as store_err:
    logger.warning("Failed to store rolling disk HMAC key", ...)
return key, True
```

İzole probe sonucu:

```text
returned_key_len: 32 was_minted: True
```

Vault read-only veya bozukken mevcut process yeni key ile chunk yazmaya devam edebilir. Restart sonrasında aynı key geri alınamayacağı için chunks HMAC doğrulamasını geçemez ve quarantine edilir; blackbox kayıtları kalıcı biçimde kullanılamaz hale gelir.

Bu davranış, fonksiyonun “key-loss event” ve “pre-existing chunks quarantine” sözleşmesiyle de uyumsuzdur: key'in hiç persist edilemediği ilk kurulum/çalışma durumunda bile veri yazımı güvenilir değil.

**Çözüm:**

1. HMAC key persist edilemiyorsa yeni key'i kullanarak yazmaya devam etmeyin; `SecurityError` ile rolling buffer'ı açıkça unavailable durumuna alın.
2. Uygulama bu durumda blackbox kayıtlarının korumasız/gelecekte okunamaz olacağını kullanıcıya ve safety telemetry'ye bildirsin.
3. Key'in store sonrası round-trip okunabildiğini doğrulayın; doğrulama başarısızsa chunk yazmayın.
4. Test ekleyin: vault `store_secret()` hatasında buffer init/append/flush fail-closed olmalı; restart sonrasında kayıp key nedeniyle sessiz veri kaybı oluşmamalı.

---

## İncelenen fakat yeni doğrulanmış bulguya dönüştürülmeyen alanlar

- `src/safety/multiplexer.py`: TX gateway choke-point'i, queue tabanlı RX ownership ve disconnect davranışı incelendi; bu aşamada yeni bir bypass doğrulanmadı.
- `src/engine/router.py`: copy-on-write subscription snapshot, callback demotion, queue drop metriği ve lock kullanımı incelendi; yeni kritik bypass doğrulanmadı.
- `src/hal/rp1210/client.py` ve `src/hal/rp1210/bus.py`: DLL allowlist, Win32 sistem yolu, ctypes ABI imzaları, classic DLC sınırları ve listen-only TX engeli incelendi; yeni doğrulanmış kritik bulgu çıkarılmadı.
- Exporter modülleri: output-root sınırları, XML escaping, signal validation ve MDF atomic swap incelendi; yeni kritik bulgu doğrulanmadı.
- `src/core/models/can_frame.py`: arbitration/DLC/data/source/error-state invariant'ları incelendi; yeni kritik bypass doğrulanmadı.

## Doğrulama

Çalıştırılan hedef testler:

```text
226 passed in 8.90s
```

Çalıştırılan lint:

```text
ruff check ...
All checks passed!
```

Ek probe'lar:

- E2E validator state eviction sonrası frame'i `INITIAL/is_valid=True` kabul etti.
- Geçersiz 2-bit counter maskesiyle 16-state counter profili construction aşamasını geçti ve 5. frame'de sequence hatası oluşturdu.
- Rolling disk HMAC key vault'a yazılamasa da ephemeral key döndürdü.

Testlerin başarılı olması bu üç senaryonun mevcut suite tarafından kapsandığı anlamına gelmiyor; bu aşamada yalnızca inceleme ve doğrulama yapıldı.

## Sonraki olası review alanları

- OBD/UDS polling ve diagnostic response tüketimi
- Discovery/DBC decoder ve signal validity sınırları
- AI/copilot ve raporlama veri akışları
- Frontend IPC/bridge yetkilendirme ve renderer güven sınırı

Bu aşamada review durduruldu; hiçbir uygulama yapılmadı.

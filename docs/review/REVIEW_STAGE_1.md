# UCANLAB Kod İncelemesi — Aşama 1

**İnceleme odağı:** En kritik çalışma zamanı sınırı: `UI → SafetySupervisor → TxSafetyGateway → HAL/CAN sürücüsü`

**İncelenen revision:** `011b83599836c7f5b9b51c0f946565684d505ca8`

**İnceleme tarihi:** 2026-09-19

## 1. Kapsam ve yaklaşım

İlk aşamada aşağıdaki alanlar incelendi:

- `src/ui/desktop_app.py` — composition root, TX arm/disarm ve ECU flashing akışı
- `src/safety/` — E-Stop, supervisor, watchdog ve TX gateway sınırı
- `src/hal/` — gerçek/sanal CAN sürücüleri ve listen-only geçişleri
- `src/protocols/uds/flasher.py` — flashing worker yaşam döngüsü ve recovery
- `src/main.py` — CLI/GUI giriş yolları
- `tests/safety`, `tests/e2e`, ilgili `tests/unit` dosyaları
- `.github/workflows/ci.yml`, `pyproject.toml`

Bu aşamada **kaynak kodu değiştirilmedi**; yalnızca rapor oluşturuldu.

## 2. Mimari özeti

Üretim GUI akışında genel güvenlik zinciri doğru yönde kurulmuş görünüyor:

```text
WebView/React UI
    ↓
DesktopApiBridge
    ↓
UniversalCanDesktopApp
    ↓
SafetySupervisor + TxWatchdogSupervisor + EmergencyStopSystem
    ↓
TxSafetyGateway
    ↓
PythonCanBus / RP1210Bus / diğer HAL'ler
    ↓
CAN/CAN-FD fiziksel bus
```

Olumlu noktalar:

- Başlangıçta bus `listen_only=True` ile açılıyor.
- CLI yolu TX yapmıyor ve pasif dinleme modunda kalıyor.
- TX mesajları için merkezi `TxSafetyGateway` kullanılıyor.
- Watchdog, E-Stop, whitelist, hız interlock'u ve dual-confirmation mekanizmaları mevcut.
- ECU flashing tarafında imza, hedef kimliği, checksum ve anti-rollback kontrolleri bulunuyor.
- CI'da Ruff, Bandit, pip-audit, CodeQL ve coverage eşiği tanımlı.

## 3. Bulgular

### [HIGH] H1 — `arm_tx()` kısmi başarısızlıkta fiziksel driver'ı aktif bırakabilir

**Konum:** `src/ui/desktop_app.py:1577-1592`

Akış şu şekilde:

1. `_set_driver_listen_only(False)` çağrılıyor; donanım TX-capable moda geçiriliyor.
2. Ardından `self.supervisor.arm_tx(...)` çağrılıyor.
3. Supervisor geçişi/token doğrulaması/başka bir kontrol hata verirse genel `except` yalnızca hata döndürüyor.
4. Driver'ı tekrar listen-only moda alan bir rollback yok.

Sonuç olarak uygulama `success=False` döndürürken fiziksel transceiver aktif kalabilir. Supervisor TX iznini vermemiş olsa bile araç bus'ında ACK/aktif transceiver etkisi oluşabilir. Bu, özellikle authorization veya state transition hatasında güvenli durumun garanti edilmediği anlamına gelir.

**Önerilen çözüm:**

- Driver modunu ve supervisor geçişini tek bir transaction gibi ele alın.
- `supervisor.arm_tx()` herhangi bir nedenle başarısız olursa `finally` içinde `_set_driver_listen_only(True)` çalıştırın.
- Rollback başarısızlığında yalnızca log yazmayın; E-Stop/fault durumunu tetikleyin ve sonucu `TX_ARM_ROLLBACK_FAILED` koduyla dönün.
- Başarıdan sonra da `bus.listen_only is False` ve `supervisor.is_tx_permitted is True` koşullarını birlikte doğrulayın.
- Bu işlem `_bus_lock` altında atomik kalmalı.

Örnek davranış:

```python
with self._bus_lock:
    driver_enabled = False
    try:
        if not self._set_driver_listen_only(False):
            return failure("DRIVER_ACTIVE_MODE_REJECTED")
        driver_enabled = True
        self.supervisor.arm_tx(reason=reason, auth_token=arm_token)
        if not self.supervisor.is_tx_permitted:
            raise SafetyError("Supervisor TX izni vermedi")
    except Exception:
        if driver_enabled and not self._set_driver_listen_only(True):
            self._force_safety_fault("TX_ARM_ROLLBACK_FAILED")
            return failure("TX_ARM_ROLLBACK_FAILED")
        raise
```

> Yukarıdaki kod doğrudan uygulanacak nihai patch değildir; mevcut HAL API'sinin hata sözleşmesiyle uyarlanmalıdır.

**Eklenmesi gereken testler:**

- Driver aktif moda geçtikten sonra `SafetySupervisor.arm_tx()` hata verirse bus tekrar listen-only olmalı.
- Rollback başarısız olursa supervisor FAULT/E-Stop devreye girmeli.
- `arm_tx()` başarısız dönüşünde `bus.listen_only=True` invariant'ı test edilmeli.
- Aynı senaryonun gerçek `RP1210Bus` ve `PythonCanBus` adaptörleri için mock/integration varyantları eklenmeli.

---

### [HIGH] H2 — Flashing iptali/hatası sonrası TX modu açık kalabilir

**Konumlar:**

- `src/ui/desktop_app.py:2538-2604`
- `src/ui/desktop_app.py:2630-2638`

`flash_start()` gerçek flashing worker'ını başlatmadan önce `arm_tx()` çağırıyor. Ancak:

- `_real_flash_worker()` içindeki `finally` yalnızca UI progress gönderiyor.
- `flash_cancel()` flashing engine'e `cancel()` iletiyor ama `disarm_tx()` çağırmıyor.
- Başarısız flashing, exception veya cancellation sonrası fiziksel bus'ın listen-only moda döndürülmesi garanti edilmiyor.

Watchdog daha sonra sistemi fault durumuna taşıyabilir; ancak bu gecikmeli bir emniyet mekanizmasıdır ve flashing sonlanır sonlanmaz aktif transceiver'ın kapatılmasını garanti etmez. Ayrıca simülasyon ve gerçek flashing yollarında cleanup davranışı birbirinden farklı kalabilir.

**Önerilen çözüm:**

- Flash yaşam döngüsünü ortak bir `finally` cleanup fonksiyonuna bağlayın.
- Başarı, hata ve iptal durumlarının tamamında:
  1. UDS recovery/release tamamlanmalı,
  2. `disarm_tx()` çağrılmalı,
  3. fiziksel driver listen-only doğrulanmalı,
  4. başarısızsa E-Stop/fault tetiklenmeli.
- `flash_cancel()` yalnızca isteği işaretlemeli; gerçek disarm işlemi worker'ın `finally` bloğunda, protokol sınırı güvenli şekilde geçildikten sonra yapılmalı.
- Uygulama kapanışı için de aynı cleanup fonksiyonu kullanılmalı.
- Flash status değerleri `completed`, `failed`, `cancelled`, `cleanup_failed` gibi ayrıştırılmalı; yalnızca `cancelled` yazıp TX durumunu belirsiz bırakılmamalı.

Örnek yaşam döngüsü:

```python
try:
    success = self.flashing_engine.execute_flash(flash_cfg)
    status = "completed" if success else "failed"
except Exception as exc:
    status = "failed"
    error = str(exc)
finally:
    cleanup_error = self._safe_disarm_after_flash()
    if cleanup_error:
        status = "cleanup_failed"
        self._force_safety_fault("FLASH_TX_CLEANUP_FAILED")
```

**Eklenmesi gereken testler:**

- Transfer ortasında `flash_cancel()` sonrası `supervisor.is_tx_permitted is False`.
- UDS exception sonrası `bus.listen_only is True`.
- Checksum/recovery hatası sonrası disarm garantisi.
- Simülasyon flashing'inde de aynı status/cleanup sözleşmesi.
- Worker tamamlanmadan ikinci `flash_start()` çağrısının reddedilmesi ve ilk worker'ın cleanup'ının bozulmaması.

---

### [MEDIUM] M1 — Kritik UI/HAL transaction invariant'ları için doğrudan entegrasyon testi eksik

Mevcut test suite safety ve protocol seviyesinde geniş görünüyor; ancak yukarıdaki iki kritik uygulama-seviyesi invariant için kaynakta açık bir test bulunamadı:

- `driver active → supervisor arm failure → driver rollback`
- `flash worker exit/cancel/failure → disarm + listen-only`

`tests/unit/test_t57f_ui_token_watchdog.py` watchdog pulse sırasını test ediyor; `tests/unit/test_review_round3_fixes.py` reconnect sonrası disarm'ı test ediyor. Ancak `UniversalCanDesktopApp.arm_tx()` içindeki başarısızlık rollback'i ve `flash_cancel()` cleanup'ı doğrudan doğrulanmıyor.

**Önerilen çözüm:**

- Bu iki invariantı `tests/unit/test_desktop_app.py` veya ayrı `tests/safety/test_desktop_tx_lifecycle.py` dosyasına ekleyin.
- Test bus'ı `set_state(listen_only)` çağrılarını ve sıralarını kaydetmeli.
- Her test, yalnızca dönen hata mesajını değil, fiziksel mod ile supervisor state'ini birlikte assert etmeli.

---

### [LOW] L1 — Test/lint araçları mevcut inceleme ortamında kurulu değil

Bu çalışma ortamında şu komutlar çalıştırılamadı:

- `python -m pytest ...` → `No module named pytest`
- `ruff check ...` → `No module named ruff`

Bu, kaynak kodunda doğrulanmış bir kusur değildir; ilk aşamadaki dinamik doğrulama kapsamını sınırlar.

**Önerilen çözüm:**

- İzole bir sanal ortam oluşturup `requirements.txt` ve `requirements-dev.txt` kurun.
- Önce kritik testleri, sonra tam suite'i çalıştırın:

```bash
python -m pytest -q tests/safety tests/e2e/test_safety_wiring.py tests/unit/test_desktop_app.py
python -m ruff check src tests
python -m pytest -q --cov=src --cov-fail-under=80
```

- CI'da kullanılan Python sürümleriyle (3.12/3.13) yerel sonucu karşılaştırın.

## 4. Öncelikli düzeltme sırası

1. **H1'i düzeltin:** `arm_tx()` için driver rollback ve rollback-failure fault.
2. **H2'yi düzeltin:** Flash worker/cancel/failure için ortak disarm cleanup.
3. H1/H2 için uygulama seviyesinde regression testlerini ekleyin.
4. Test/lint araçlarını kurup kritik testleri çalıştırın.
5. Sonraki aşamada `src/safety` ve `src/hal` iç implementasyonlarına inin; özellikle gateway'in tüm gönderim yollarını, RP1210 ctypes sınırını ve driver hata/teardown sözleşmelerini inceleyin.

## 5. Aşama 1 sonucu

İlk aşamada iki **yüksek öncelikli yaşam döngüsü/emniyet bulgusu** tespit edildi:

- `arm_tx()` başarısız olduğunda aktif driver rollback'i garanti değil.
- ECU flashing iptal/hata ile bittiğinde TX disarm'ı garanti değil.

Bu rapor oluşturulduktan sonra inceleme burada durduruldu. Kaynak koduna patch uygulanmadı.

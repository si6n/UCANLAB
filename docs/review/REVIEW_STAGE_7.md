# UCANLAB Kapsamlı Review — Aşama 7

**İncelenen revizyon:** `011b83599836c7f5b9b51c0f946565684d505ca8`

**Kapsam:** UDS firmware flashing, firmware imza doğrulama/trust-anchor akışı, SecurityAccess (0x27), flash challenge token kapsamı ve başarısız flash sonrası recovery.

**Kısıt:** Bu aşamada yalnızca review yapıldı. Kaynak koda veya testlere düzeltme uygulanmadı.

## Doğrulama yöntemi

- Bulguyla ilişkili kaynak dosyalarının tamamı okundu; özellikle `src/ui/desktop_app.py`, `src/protocols/uds/flasher.py` ve `src/protocols/uds/client.py`.
- Hedefli testler çalıştırıldı:
  - `tests/unit/test_flasher.py`
  - `tests/unit/test_firmware.py`
  - `tests/unit/test_t56c_uds_pending_bound.py`
  - `tests/unit/test_t57f_ui_token_watchdog.py`
  - Sonuç: **59 passed**
- Ruff çalıştırıldı: **All checks passed**.
- Çalışma ağacında yalnızca önceki review raporları bulunuyor; uygulama kaynaklarında değişiklik yapılmadı.

## Bulgular

### H1 — Firmware trust anchor renderer girdisinden geliyor

**Önem:** Kritik / yüksek — firmware authenticity kontrolü etkisizleşebilir.

#### Kanıt

- `src/ui/desktop_app.py:2359-2376` yalnızca `firmwareSignature` ve `trustedPubkey` alanlarının mevcut olup olmadığını kontrol ediyor.
- `src/ui/desktop_app.py:2391-2400` `trustedPubkey` değerini doğrudan renderer konfigürasyonundan `Ed25519PublicKey` nesnesine dönüştürüyor.
- `src/ui/desktop_app.py:2568-2583` bu anahtarı `FlashingConfig.trusted_pubkey` olarak flash motoruna iletiyor.
- `src/protocols/uds/flasher.py:491-510` imzayı `config.trusted_pubkey` ile doğruluyor. Kod yorumunda “embedded trust anchor” denmesine rağmen ilgili anahtar burada uygulama içinde sabitlenmiş değil; üst katmandan gelen değer kullanılıyor.

Kontrollü probe’da yeni bir Ed25519 anahtar çifti oluşturulup aynı renderer girdisiyle imzalanan keyfi imajın doğrulandığı görüldü:

```text
renderer_supplied_key_verifies_renderer_supplied_signature: True
require_signature: True
require_target_identity: True
```

#### Etki

`require_signature=True` olması tek başına yeterli değil; doğrulama anahtarı da aynı güvenilmeyen akıştan geliyorsa kontrol self-attestation’a dönüşüyor. Renderer girdisi güvenilmez hale gelirse yetkisiz bir imaj, saldırganın kendi anahtarıyla imzalanıp flash ön koşulunu geçebilir.

Ayrıca `src/ui/desktop_app.py:2371-2375` ve `:2582` üzerinden `skipTargetIdentity=True` renderer tarafından sağlanabiliyor. Bu değer hedef VIN/seri doğrulamasını devre dışı bırakıyor; bunun için bağımsız bir yetkilendirme katmanı görünmüyor.

#### Çözüm önerisi

- Trust anchor’ı Python tarafındaki güvenilir composition root’a sabitleyin veya işletim sistemi/secure element destekli güvenli anahtar deposundan yükleyin; public key’i renderer’dan kabul etmeyin.
- İmzalı manifest içinde imaj SHA-256, boyut, hedef ECU kimliği, bellek adresi ve izin verilen politika alanlarını bağlayın; flash başlamadan önce bunların imzalı değerlerle eşleşmesini zorunlu tutun.
- `skipTargetIdentity` seçeneğini production akışında kaldırın veya ayrı, yüksek riskli ve güvenilir operatör yetkisi isteyecek şekilde koruyun.
- Negatif test ekleyin: renderer’ın verdiği anahtarla imzalanmış keyfi imaj reddedilmeli; kimlik doğrulama istisnası bağımsız yetki olmadan kabul edilmemeli.

---

### H2 — Flash challenge token’ı imajı ve kritik flash parametrelerini bağlamıyor

**Önem:** Yüksek — kullanıcı onayı başka bir flash niyetine aitken farklı bir imaj/parametre yürütülebilir.

#### Kanıt

- `src/ui/desktop_app.py:2429-2439` challenge doğrulaması yalnızca `action_type` ve `action_id` üzerinden yapılıyor.
- Token doğrulamasından sonra flash için kritik alanlar renderer konfigürasyonundan okunuyor: `src/ui/desktop_app.py:2568-2583`. Bu alanlar arasında `data`, `memoryAddress`, `blockSize`, firmware imzası, public key, hedef VIN/seri ve `skipTargetIdentity` bulunuyor.

Dolayısıyla token’ın doğruladığı niyet ile gerçekten yürütülen payload arasında kriptografik bağ yok. Aynı `action_type`/`id` kapsamındaki bir challenge sonrasında imaj veya flash parametreleri değişse bile token kontrolü bu değişikliği tespit etmiyor.

#### Etki

Kullanıcı bir firmware, hedef ve adres için onay vermişken renderer katmanı farklı firmware’i, farklı adresi veya farklı hedef kimliği politikasını aynı action kimliğiyle gönderebilir. H1’deki trust-anchor problemiyle birlikte değerlendirildiğinde bu, hem imaj kimliğinin hem de imza doğrulama bağlamının onay dışına taşınmasına yol açıyor.

#### Çözüm önerisi

Challenge oluşturulurken canonical bir flash niyeti üretin ve en az şu alanları token kapsamına alın:

- Firmware SHA-256 ve boyut
- Hedef ECU VIN/seri bilgisi
- Bellek adresi ve block size
- İmza/manifest kimliği
- `require_target_identity` ve benzeri politika bayrakları

Yürütme sırasında aynı canonical payload yeniden hesaplanmalı ve token’daki değerle birebir karşılaştırılmalı. Action ID tek başına yeterli kabul edilmemeli.

---

### H3 — SecurityAccess (0x27) çağrıları kritik gateway token’ını iletmiyor

**Önem:** Orta-yüksek — güvenlik erişimi yapılandırılmış gerçek gateway üzerinde flash akışı çalışmayabilir.

#### Kanıt

- `src/protocols/uds/flasher.py:702` seed isteği `security_access_request_seed(level=..., user_confirmed=...)` ile çağrılıyor; confirmation token yok.
- `src/protocols/uds/flasher.py:743-745` key gönderimi de yalnızca `level`, `key` ve `user_confirmed` ile çağrılıyor.
- `src/protocols/uds/client.py:197-205` ve `:207-214` bu iki metodu kritik komut olarak işaretliyor, fakat `_send_and_receive(..., confirmation_token=...)` parametresini iletmiyor.
- Aynı flash akışındaki `change_session`, `request_download`, `transfer_data`, `request_transfer_exit`, `start_routine` ve `ecu_reset` çağrılarında token aktarımı bulunurken 0x27’nin iki ayağında bulunmuyor.

#### Etki

Confirmation secret yapılandırılmış production gateway’de kritik komut için HMAC/token kanıtı olmadan 0x27 isteği gönderildiğinde gateway’in fail-closed olarak reddetmesi beklenir. Bu bir yetki atlatma değil; tersine, SecurityAccess kullanan gerçek flash konfigürasyonunun çalışmamasına yol açan bir tutarsızlık ve kullanılabilirlik problemidir. Programlama oturumu açıldıktan sonra bu hata alınabileceği için ECU recovery akışı da devreye girebilir.

#### Çözüm önerisi

- `security_access_request_seed` ve `security_access_send_key` API’lerine confirmation token/context parametreleri ekleyin.
- Flasher’ın her iki 0x27 adımına geçerli, ilgili flash oturumuna bağlanmış token iletmesini sağlayın.
- Confirmation secret aktif bir gateway entegrasyon testi ekleyin: geçerli token olmadan seed/key reddedilmeli, geçerli token ile iki adım da başarılı olmalı.

---

### H4 — Flash recovery içindeki RequestTransferExit (0x37) token olmadan çağrılıyor

**Önem:** Yüksek — başarısız flash sonrası güvenli toparlanma yolu kendi kritik komut kapısıyla çakışıyor.

#### Kanıt

- `src/protocols/uds/flasher.py:978-1015` recovery önce transferi temiz kapatmak için `self.uds_client.request_transfer_exit()` çağırıyor.
- `src/protocols/uds/client.py:382-410` `request_transfer_exit` varsayılan olarak kritik, `user_confirmed=False` ve token’sız çağrıldığında kritik gateway doğrulamasını karşılamıyor.
- Recovery dokümantasyonu 0x37’yi ilk ve en temiz toparlanma adımı olarak tanımlıyor; ancak bu çağrıda ne `user_confirmed` ne de `confirmation_token` sağlanıyor. Hata yakalanıp yutuluyor ve akış `change_session(DEFAULT_SESSION)` fallback’ine geçiyor (`:1017-1025`).

#### Etki

Production gateway kritik komutlar için token istiyorsa, başarısız flash sonrasında recovery’nin ilk adımı reddedilir. Sistem bunu loglayıp daha zayıf/ikincil fallback’e geçer. Bu, ECU’nun transfer durumundan güvenli biçimde çıkmasını garanti etmez ve yarım transfer sonrası operatöre bırakılan recovery yükünü artırır.

#### Çözüm önerisi

- Recovery için flash oturumuna bağlı, önceden yetkilendirilmiş özel bir recovery token/context akışı kullanın veya 0x37’yi normal kritik komut akışıyla geçerli kanıt sağlayarak çağırın.
- 0x37’nin gerçekten gönderildiğini ve gateway tarafından kabul edildiğini doğrulayan failure-injection testi ekleyin.
- 0x37 çalışmazsa fallback’in yalnızca loglanması yerine operatöre açık ve yüksek görünürlüklü recovery durumu üretin; ECU’nun güvenli durumda olduğu varsayılmamalı.

## Sonuç

Aşama 7’de dört bulgu tespit edildi:

- **1 kritik/yüksek:** firmware trust anchor renderer girdisine bağlı.
- **1 yüksek:** flash challenge token’ı payload ve kritik parametrelerle bağlı değil.
- **1 orta-yüksek:** SecurityAccess token aktarımı eksik.
- **1 yüksek:** recovery RequestTransferExit token’sız.

Hedefli testler **59 passed**, Ruff **temiz**. Hiçbir bulgu uygulanmadı; yalnızca raporlandı.

**Aşama 7 tamamlandı ve burada duruyorum.**

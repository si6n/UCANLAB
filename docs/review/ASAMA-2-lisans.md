# ASAMA 2: Lisans ve Kimlik Doğrulama Raporu
Denetçi: Claude (Bu oturum) | Tarih: 2026-09-15
Kapsam: `src/security/license/validator.py` (421 satır), `src/security/hwid/collector.py` (222 satır), `src/security/anti_tamper/guard.py` (137 satır), `src/launcher/auth.py` (166 satır). Bağlam için okunan ek dosyalar: `src/launcher/app.py`, `src/security/cloud/license_flow.py`, `src/security/cloud/client.py`, `src/ui/desktop_app.py` (cloud/lisans wiring dilimleri), `src/safety/secret_provider.py`, `tests/unit/test_license_validator.py`, `tests/unit/test_adversarial_final_gate.py`, `tests/unit/test_anti_tamper.py`.

> **Doğrulama yöntemi:** Yalnızca statik kaynak kod okuması. Bu oturumda **hiçbir test çalıştırılmadı**, hiçbir bulgu çalışma zamanında yeniden üretilmedi. Her "Kanıt" satır referansı ile kod alıntısıdır; "Senaryo" kod okumasından türetilmiş tetiklenme yoludur, saha/laboratuvar doğrulaması değildir. Satır numaraları 1 tabanlıdır ve yazım sırasında dosyalardan yeniden doğrulanmıştır. Repo'da T47-B'nin paralel düzenlemeleri nedeniyle satır numaraları bu rapor sonrası kayabilir; kanıt alıntıları kimlik için esas alınmalıdır.
>
> **Severity ölçütü (bu rapor için):** KRİTİK = mevcut üretim wiring'inde doğrudan tetiklenebilir koruma bypass'ı. YÜKSEK = invariant ihlali / fail-open tasarım (tetiklenmesi ek koşul veya çağıran gerektirir). ORTA = savunma katmanı zayıflığı / latent (bağlanmamış) koruma. DÜŞÜK = sınırlı etki, fail-closed yönde veya kod kalitesi. BİLGİ = not.

> **ÖNEMLİ YAPISAL BULGU (severity yorumunu belirler):** Bu aşamanın iki ana modülü **üretimde bağlı değildir**. `LicenseValidator` ve `AntiTamperGuard` yalnızca `src/security/__init__.py` / kendi `__init__.py`'leri tarafından re-export edilir ve **yalnızca testler** tarafından çağrılır; hiçbir üretim entry point'i (desktop_app, launcher, main) bunları kurmaz. `grep -rn "LicenseValidator|AntiTamperGuard" --include=*.py` sonucu üretimde yalnızca `__init__` re-export'larını gösterir. Üretimde lisans doğrulama iki ayrı yoldan yapılır:
> 1. **`LicenseFlow`** (`src/security/cloud/license_flow.py`) — Ed25519 cloud ticket doğrulaması; `desktop_app` içinde kurulur (`desktop_app.py:771`).
> 2. **`LauncherAuthManager`** (`src/launcher/auth.py`) — launcher preflight'ında durum sorgular; `LicenseFlow`'a delege eder.
>
> Bu nedenle `validator.py` ve `guard.py` içindeki birçok açık **latent**'tir (ORTA); rapor bunu her bulguda açıkça belirtir. KRİTİK sınıflandırılan bulgular, **mevcut üretim wiring'inde** doğrudan tetiklenebilen bulgulardır.

## 1. Yönetici Özeti

Lisans ve kimlik katmanının **kriptografik çekirdeği sağlamdır**: cloud ticket Ed25519 imzası `payload_bytes` (imzalanan kanonik bayt dizisi) üzerinde doğrulanır ve **payload JSON'a parse edilmeden önce** (sıra doğru — imza doğrulanmadan hiçbir alan kullanılmaz, `license_flow.py:306-341`); `kid` bilinmeyen anahtarda fail-closed; `nonce` boş bırakılamaz; cihaz bağlama (device_id) atlanamaz; HMAC ve imza karşılaştırmalarında `hmac.compare_digest` kullanılır; HWM (anti-rollback) dosyası HMAC ile mühürlenir ve bozuk/mühürsüz dosyada **hiçbir değer benimsenmeden** muhafazakâr tabana düşülür (G2/H4/H1 düzeltmeleri doğru uygulanmış). HWID koleksiyonu `GetSystemDirectoryW` ile mutlak yol çözer, PowerShell komutunu allow-list regex ile doğrular, sentinel (`UNKNOWN_*`, `FALLBACK-*`) değerleri `LicenseValidator` tarafında fail-closed kapıdan geçirir.

Buna karşın — ve asıl sorun burada — **lisans doğrulama bir güvenlik kapısı olarak değil, bir bilgi/telemetri katmanı olarak kablolanmıştır.** `LauncherAuthManager.get_current_status()` bir `AuthStatus` döndürür; `has_valid_license` bayrağı `UniversalCanLauncher.run_preflight()` içinde **yalnızca ekrana yazdırılır** ve `can_launch = not has_critical_failures and target_exe.exists() and not has_blocking_mandatory_update` hesabına **hiç girmez** (`app.py:133`, `:322`). Yani "aktif lisans yok / lisans süresi doldu / cihaz eşleşmiyor" durumları uygulamayı **durdurmaz**. Bu, mevcut üretim wiring'inde doğrudan tetiklenebilen bir koruma bypass'ıdır → **KRİTİK**.

İkinci yapısal boşluk: ürünün gerçek lisans çekirdeği olması amaçlanan `LicenseValidator` ve anti-tamper `AntiTamperGuard` **hiç bağlanmamıştır**; dolayısıyla içlerindeki zafiyetler (fallback wildcard, fingerprint koleksiyon hatasında HWM ilerlemesi) ve anti-tamper'ın `enforce()`'unun hiçbir çağıranı olmaması koruma sağlamaz.

Üçüncü bulgu: cloud ticket doğrulamasında **anti-rollback high-water mark iki çağrı yolunda farklı sağlamlıkta**. `desktop_app` `LicenseFlow`'u kalıcı bir HWM yolu ile kurar (`desktop_app.py:775`, doğrulandı), ancak **`LauncherAuthManager` kendi `LicenseFlow` örneğini `hwm_path` olmadan kurar** (`auth.py:94`) → `LicenseFlow._hwm_path is None` → `_persist_hwm` no-op → launcher'ın yaptığı offline doğrulamada HWM yalnız bellekte ve her çalıştırmada `boot_realtime`'a sıfırlanır. Bu, H-4/M-19'un "restart'ta sıfırlanma" düzeltmesini launcher yolu için fiilen etkisiz kılar: launcher kapatılıp saat geri alınırsa rollback tespit edilmez → **YÜKSEK**. Dördüncü olarak, `_hmac_key()` vault anahtarı yokken `store_secret` **hatasını yutup** rastgele bir anahtar üretir (`license_flow.py:138-144`); her checkpoint'te yeni anahtar → HWM kalıcı olsa bile sürekli mühür uyuşmazlığı ve **her restart'ta rollback tespitinin sessizce sıfırlanması** → **YÜKSEK** (bir koşula bağlı).

Kısacası: kriptografi iyi, **politika/bağlama zayıf**. Ticari koruma açısından en kritik risk lisanslı lisanssız ayrımının hiçbir yaptırıma bağlı olmamasıdır; tersine mühendislik açısından ise `LicenseValidator`'ın bayat/bağlanmamış olması ikinci derecedir çünkü gerçek doğrulama zaten cloud ticket tarafında.

## 2. Bulgu Tablosu

| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
| L-1 | `src/launcher/app.py:133`, `:322`; `src/launcher/auth.py:101-138` | KRİTİK | `has_valid_license` launch kapısına girmiyor; `can_launch` yalnız prereq+exe+mandatory-update bakıyor. Lisans yokluğu/expiry/cihaz uyuşmazlığı yalnız ekrana yazılıyor. | Lisanssız/süresi dolmuş makinede launcher `can_launch=True` → `launch_main_app` çekirdek binary'yi çalıştırır. `--check-only` hariç gate yok. | `can_launch`'a `report.auth_status.has_valid_license` ekle (fail-closed); geçersizse aktivasyon UI'ı göster ve çık. Lisans kapısını `prereqs` ile aynı blocker seviyesine al. |
| L-2 | `src/launcher/auth.py:94`; `src/security/cloud/license_flow.py:84`, `:100`, `:170-173` | YÜKSEK | `LauncherAuthManager` `LicenseFlow`'u `hwm_path` **vermeden** kurar (`desktop_app` verir) → `_hwm_path=None` → `_persist_hwm` no-op. Launcher'ın offline doğrulaması bu örnekte yapıldığı için rollback penceresi süreç-yereldir. | Launcher kapatılıp saat geri alınır → yeniden çalıştırmada `last_known_clock_ts = boot_realtime = now` → `now < last_known_clock_ts` asla doğru olmaz → rollback tespit edilmez; `offline_until`/`exp` geri alınmış saatle değerlendirilir. | `auth.py`'de de `hwm_path=_app_data_root()/"logs"/"license_hwm.txt"` geçir; ortak `_default_hwm_path()` yardımcısı çıkar. İki `LicenseFlow` örneği aynı HWM dosyasını paylaşmalı. |
| L-3 | `src/security/cloud/license_flow.py:122-144`, `:159-161` | YÜKSEK | `_hmac_key()` vault'ta `LICENSE_HWM_KEY` yoksa `store_secret` hatasını **yutar** (`except Exception: pass`) ve rastgele bir anahtar döndürür; her yeni süreç/checkpoint yeni anahtar → eski mühür doğrulanamaz. | Vault yazılamaz (izinsiz dizin/kilitli DPAPI) → her restart HWM mühürü uyuşmaz → `_load_persistent_hwm` sessizce boot-time'a düşer (rollback tespiti sıfırlanır). Saldırgan bu yolu kasıtlı tetikleyebilir. | Anahtar provisioning başarısızsa HWM'yi **kalıcı olarak güvenilmez** kabul et ve CRITICAL log + UI uyarısı üret; anahtarı üret-tut yerine, vault yoksa fail-closed davran (yazma başarısızsa anahtarı sabitle ve rollback penceresini conservat-derived floor'dan başlat). |
| L-4 | `src/security/license/validator.py:369-372`, `:83`, `:226-228` | YÜKSEK (latent) | `_allow_wildcard` constructor opt-in'i, `payload.hardware_fingerprint == "*"` token'ı **her makinede** geçerli kılar; fingerprint dalı atlanır. `validator.py` üretimde bağlı değil ama invariant kırılgan. | Bir çağıran `allow_wildcard_license=True` geçirirse (veya yeni bir wiring bunu unutursa) wildcard token tüm host'larda doğrulanır → HWID kilidi ölür. | Wildcard'ı yalnız frozen/test ortamında etkinleştir (`getattr(sys,"frozen",False)` kontrolü), üretimde kaldır; wildcard kabulünü CRITICAL logla ve `AuthStatus.has_valid_license`'ı wildcard'da düşür. |
| L-5 | `src/security/license/validator.py:204-228`, `:362-367` | YÜKSEK (latent) | `_is_indeterminate_fingerprint` yalnız `self.hardware_fingerprint` (yerel, koleksiyon tarafı) için uygulanır; **token içindeki** `payload.hardware_fingerprint` sentinel kapıdan geçirilmez. Token `FALLBACK-<host>-<mac>` taşıyorsa eşitlik karşılaştırmasına girer. | WMI okuması iki farklı makinede de başarısız → ikisi de aynı/benzer `FALLBACK-*` üretirse, bir makinede üretilen token diğerinde `payload.hardware_fingerprint == self.hardware_fingerprint` ile geçebilir (koleksiyon tarafı da sentinel ise L-5+nokta). | Doğrulamadan önce `self._is_indeterminate_fingerprint(payload.hardware_fingerprint)` de kontrol et; eşitlik dalından **önce** iki tarafı da indeterminate için reddet. |
| L-6 | `src/security/cloud/license_flow.py:285-292`, `:406`; `src/launcher/auth.py:121`, `:131-138` | YÜKSEK | `is_offline` çağıran beyanına bağlı; `exp` kontrolü (403) `offline_until`'dan **bağımsız** çalışır ve `verify_cloud_ticket` çağrılırken `expected_device_id` çağıran tarafından atlanabilir. `get_current_status` istisnayı yutup `has_valid_license=False` döner ama bu bayrak L-1 nedeniyle yaptırımsız. | Uzun `exp` + kısa `offline_until` ticket: `is_offline=False` ile çağrılırsa offline grace hiç uygulanmaz; ayrıca online activation dışında hiçbir yerde `expected_device_id` verilmez → cihaz bağlama yalnız `client.get_device_id()`'ye dayanır. | `is_offline`'ı bir "çağıran beyanı" olmaktan çıkar (kalıcı HWM + network yokluğuyla otomatik belirle); `verify_cloud_ticket`'a her zaman `expected_device_id=hwid` geçir; `exp` ile `offline_until`'ı `min()` semantiğiyle birleştir. |
| G-1 | `src/security/anti_tamper/guard.py:116-137` | ORTA (latent) | `enforce()` hiçbir üretim çağrısına sahip değil (grep: yalnız tanım). Anti-tamper ve timing anomalisi çalışma zamanında hiç uygulanmaz; `detect_timing_anomaly` yalnız testten çağrılır. | Debugger/hypervisor altında çalıştırılan ikili hiçbir engel görmez; ticari koruma ve lisans çalınması kolaylaşır. | `main.py`/`desktop_app` başlangıcında `AntiTamperGuard.enforce(on_violation=...)` çağır; `enforce`'un dönüşünü launch gate'ine bağla. |
| V-1 | `src/security/license/validator.py:296-298` | ORTA (latent) | `high_water_mark_path` yokken anchor kesin sessizce `now`'a ilerler; hiçbir uyarı/`protection_level` düşüşü loglanmaz. Üretimde bu yolu izleyen modül (validator) bağlı olsaydı rollback koruması yalnız oturum-içi olurdu. | Rollback korumasının kalıcılığı olmadığı fark edilmeden ship edilir (L-2'nin validator'daki eşdeğeri). | `high_water_mark_path` verilmezse CRITICAL log + belge; üretimde zorunlu kıl. |
| B-1 | `src/security/hwid/collector.py:158-166` | ORTA (latent) | `collect_primary_mac()` `uuid.getnode()`'e düşer; `uuid.getnode()` MAC yoksa **rastgele** düğüm döndürür (random bit set) → fingerprint süreçler arası değişebilir, kararlılık sözleşmesi kırılır. Sentinel/biçim doğrulaması (`_INVALID_UUIDS`) MAC için yok. | Adaptörü olmayan/sanal makinede fingerprint her çağrıda farklı olabilir → meşru kullanıcı kilitlenir veya (cache ile) yanlış kimlik sabitlenir. | `uuid.getnode()`'ün random bitini kontrol et (`(node>>40)&0x2`), rastgelelik tespitinde `UNKNOWN_MAC` döndür; `_compute_hardware_fingerprint` bunu indeterminate olarak ele alsın. |
| B-2 | `src/security/hwid/collector.py:119-126`, `:175-195` | ORTA (latent) | `collect_motherboard_uuid()` fallback'i `platform.node()` + MAC'e bağlı; tamamen kopuk bir makinede (DHCP MAC yok) iki farklı host aynı `FALLBACK-<node>-<mac>`'e yakınsayabilir. Fingerprint `mb_uuid \| cpu \| disk \| bios` birleşimine dayanır; tek bileşen eksikse diğerleri benzersizliği taşır (kısmi ağırlık). | Klonlanmış VM/hostname şablonunda HWID çakışması → tek lisans çok makinede. | Fallback'te en az 2 bağımsız fiziksel bileşen zorunlu kıl; hepsi sentinel ise indeterminate kabul et (validator kapısı zaten var). |
| B-3 | `src/security/hwid/collector.py:57` | ORTA (latent) | PowerShell allow-list regex `[A-Za-z0-9_().|,'= \-]+` WMI sınıf/alan adlarını serbest metin olarak kabul eder; `_wmi_query` sınıf/alan adlarını sabitlerden alsa da `_run_powershell`'in sözleşmesi "hiç dış girdi yok" **assert ile değil** yorumla güvence altında. | Gelecekteki bir çağıran kullanıcı girdisini `wmi_class`'a interpole ederse (ör. `-Filter 'Index=0'`'a benzer bir yol) komut enjeksiyonu mümkün olur; regex `.` ve `|` serbest bırakır. | Sınıf/alan adlarını modül sabiti allow-list'e karşı doğrula; `_run_powershell`'i yalnız iç çağrılara açık `_`-önekli tut ve `wmi_class in ALLOWED` assert'i ekle. |
| A-1 | `src/launcher/auth.py:106`, `:114`, `:131-138`, `:140-147` | DÜŞÜK | `get_current_status` tüm `Exception`'ı yutup `has_valid_license=False` döner; `login_web_session` sessiz `False`. Operatöre neden başarısız olduğu gösterilmez (yalnız `error` alanı, UI bağlı değil). | Teşhis zorluğu; güvenlik ihlali değil. | Log'u yapılandırılmış alanla zenginleştir; UI'da `error` alanını göster. |
| C-1 | `src/security/license/validator.py:47` vs `src/security/cloud/license_flow.py:131` | DÜŞÜK | İki ayrı HWM anahtar adı: `LICENSE_HWM_HMAC_KEY` (validator) ve `LICENSE_HWM_KEY` (cloud flow). İki alt sistem ayrı anahtar kullanır (izolasyon doğru) fakat isimlendirme kafa karıştırıcı; dokümantasyon yok. | Bakım hatası riski (yanlış anahtar adı göçü). | İsimleri `..._HWM_...` şemasında birleştir ve docstring'de hangi modülün hangi anahtarı sahiplendiğini yaz. |
| I-1 | `src/security/license/validator.py:408-421` | BİLGİ | `generate_signed_token` üretim kodunda public sınıf metodu; private key alır. Test/backend yardımcısı olsa da ship edilen modülde durur. | Sadece Erişilebilirse risk; süreç-içi private key gerektirir. | Üretim build'inden çıkar veya `if TYPE_CHECKING`/test-only modüle taşı. |

*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)

### [L-1] Lisans geçerliliği launch kapısına giriyor gibi görünüp hiç girmiyor (fail-open yaptırım)
- **Konum:** `src/launcher/app.py:133` (`can_launch`), `:322` (yalnız yazdırma); `src/launcher/auth.py:101-138` (`get_current_status`).
- **Etki / Risk:** Ürünün ticari koruma modelinin temel taahhüdü "geçerli lisans olmadan çalışmaz"dır. Kod bu taahhüdü **yerine getirmiyor**: `AuthStatus.has_valid_license` bir sonuç değişkenine dâhil edilmez. Launcher preflight başarılı olduğu sürece çekirdek binary lisanssız da başlatılır. Bu, mevcut üretim wiring'inde doğrudan tetiklenebildiği için KRİTİK ölçütüne girer.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # src/launcher/app.py:107
  auth_status = self.auth_manager.get_current_status()
  ...
  # src/launcher/app.py:133
  can_launch = not has_critical_failures and target_exe.exists() and not has_blocking_mandatory_update
  # ^ auth_status / has_valid_license BURADA YOK
  ...
  # src/launcher/app.py:322 (yalnız konsol çıktısı)
  print(f"License Tier: {report.auth_status.tier} | Active: {report.auth_status.has_valid_license}")
  ...
  # src/launcher/app.py:342-343
  if report.can_launch:
      return launcher.launch_main_app(extra_args=unknown)
  ```
  `has_valid_license` yalnız `app.py:322`'de ekrana basılır; başka hiçbir yerde dallanma koşulu değildir (`grep` ile doğrulandı: `src/launcher/app.py` içinde yalnız `:322`). `get_current_status()` lisans yok/expired/cihaz-uyuşmazlığında `has_valid_license=False` döner (`auth.py:109`, `:134`) ama bu değer `can_launch`'a ulaşmaz.
- **İstismar / Bypass Senaryosu:** Temiz bir makinede: (1) kur ve hiç aktive etme; (2) `launcher --launch`; (3) prereq'ler tam ve `target_exe` mevcutsa `can_launch=True` → uygulama açılır. Aynı şekilde süresi dolmuş ticket (`has_valid_license=False`, tier=EXPIRED) veya başka cihazın ticket'ı (device mismatch) uygulamayı durdurmaz. `AuthStatus` doğru hesaplanır; **yaptırım yoktur**.
- **Düzeltme (Remediation):**
  ```python
  # app.py run_preflight()
  license_ok = auth_status.has_valid_license
  can_launch = (
      not has_critical_failures
      and target_exe.exists()
      and not has_blocking_mandatory_update
      and license_ok                      # fail-closed
  )
  if not license_ok:
      logger.critical("Launch blocked: no valid license", extra={"tier": auth_status.tier})
  ```
  Ayrıca `main()` içinde `--launch` yolu dahi lisans kapısını atlamamalı (`report.can_launch` yeterli); lisanssız durumda aktivasyon akışına yönlendir. `_dev_override_enabled()`'ın lisans kapısını **asla** gevşetmediğini test et.

### [L-2] Launcher'ın cloud-ticket doğrulamasında anti-rollback HWM kalıcı değil (launcher tarafı)
- **Konum:** `src/launcher/auth.py:94` (`LicenseFlow(self.client, self.public_key)` — `hwm_path` yok); `src/security/cloud/license_flow.py:84` (`hwm_path=None` varsayılanı), `:100-101` (`self._hwm_path`, `last_known_clock_ts`), `:170-173` (`_persist_hwm` erken dönüş); karşılaştırma için `src/ui/desktop_app.py:771-779` (burada `hwm_path` **verilir**).
- **Etki / Risk:** `_persist_hwm` yalnız `self._hwm_path is not None` iken yazar; `_load_persistent_hwm` de aynı koşulda okur. `desktop_app` `LicenseFlow`'u `hwm_path=_app_data_root()/"logs"/"license_hwm.txt"` ile kurar (doğrulandı, `desktop_app.py:775`), ancak **`LauncherAuthManager` kendi ayrı `LicenseFlow` örneğini `hwm_path` olmadan kurar** (`auth.py:94`). Launcher'ın offline lisans sorgusu (`get_current_status` → `auth.py:121`) bu ikinci örnek üzerinden yapıldığı için launcher'da rollback penceresi **süreç-yereldir**: her çalıştırmada `last_known_clock_ts = boot_realtime = time.time()` olur. Bu, H-4/M-19'un "restart'ta sıfırlanma" düzeltmesini launcher yolu için etkisiz kılar.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # launcher/auth.py:94  — hwm_path YOK
  self.flow = LicenseFlow(self.client, self.public_key)
  ...
  # launcher/auth.py:121
  claims = self.flow.verify_cloud_ticket(ticket_str, is_offline=True)

  # license_flow.py:84, :100-101
  hwm_path: Path | None = None,
  ...
  self._hwm_path: Path | None = hwm_path
  self.last_known_clock_ts: float = self._load_persistent_hwm(self.boot_realtime)

  # license_flow.py:172-173
  if self._hwm_path is None:
      return                       # persist no-op

  # karşılaştırma — desktop_app.py:775 (bu yolda KALICI)
  hwm_path=_app_data_root() / "logs" / "license_hwm.txt",
  ```
  Aynı koruma iki farklı çağrı yolunda farklı sağlamlıkta: desktop_app kalıcı, launcher süreç-yerel. `_load_persistent_hwm` hatası (`license_flow.py:160-162`) sessizce `fallback`'e döner ve hiçbir log/uyarı üretmez; bu yüzden kalıcılığın kaybolduğu sessizce gözden kaçar.
- **İstismar / Bypass Senaryosu:** (1) Launcher bir kez çalışır; HWM diske yazılmaz (`_hwm_path is None`). (2) Kullanıcı/saldırgan launcher'ı kapatır, saat geri alır (ör. ticket'ın `offline_until`'ından öncesine). (3) Launcher yeniden çalıştırılır → yeni `LicenseFlow` örneği `last_known_clock_ts = boot_realtime = now` ile başlar → `now < self.last_known_clock_ts - 1.0` (`license_flow.py:369`) asla doğru olmaz → rollback tespit edilmez → `is_offline and now > offline_until` (`:406`) geri alınmış saatle değerlendirilir → süresi geçmiş grace yeniden geçerli görünür.
- **Düzeltme (Remediation):**
  ```python
  # auth.py — desktop_app ile AYNI kalıcı HWM dosyasını paylaş
  from src.ui.desktop_app import _app_data_root   # veya ortak helpers modülü
  self.flow = LicenseFlow(
      self.client,
      self.public_key,
      hwm_path=_app_data_root() / "logs" / "license_hwm.txt",
  )
  ```
  Ortak bir `_default_hwm_path()` yardımcısı çıkarıp hem `desktop_app` hem `launcher` aynı yolu kullansın. `_load_persistent_hwm` mühür uyuşmazlığında (`:160-162`) CRITICAL log + UI uyarısı üretsin; `boot_realtime` de `max(fallback, hwm_ts)` ile korunsun.

### [L-3] `_hmac_key()` vault yazma hatasını yutup her seferinde yeni anahtar üretiyor (HWM mührü güvenilmez)
- **Konum:** `src/security/cloud/license_flow.py:122-144` (`_hmac_key`), `:159-161` (mühür karşılaştırması), `:165-168` (hata halinde boot-time'a düşme).
- **Etki / Risk:** HWM dosyasının bütünlüğü, `LICENSE_HWM_KEY` adlı vault anahtarına dayanır. Anahtar vault'ta yoksa kod `hashlib.sha256(b"ucanlab-license-hwm" + os.urandom(32))` ile **rastgele** bir anahtar türetir ve `store_secret` çağrısının başarısızlığını `except Exception: pass` ile **yutar**. Vault yazılamadığında her yeni çağrı yeni anahtar üretir → dosyadaki eski mühür asla doğrulanamaz → `_load_persistent_hwm` sessizce boot-time'a düşer → rollback tespiti her restart'ta sıfırlanır. Bu, L-2'nin kalıcılığını da bozar ve saldırgan tarafından **kasıtlı tetiklenebilir** (ör. `logs/` dizinini salt-okunur yapmak veya DPAPI profilini bozmak).
- **Kanıt / Zafiyet Analizi:**
  ```python
  # license_flow.py:138-144
  derived = hashlib.sha256(b"ucanlab-license-hwm" + os.urandom(32)).digest()
  if secrets is not None:
      try:
          secrets.store_secret("LICENSE_HWM_KEY", derived)
      except Exception:  # noqa: BLE001 — vault unavailable: in-memory HWM only
          pass
  return derived
  ```
  `except Exception: pass` yorumu "in-memory HWM only" dese de, `self._hwm_path` **set edilmişse** (desktop_app yolu) dosya yine yazılır; ancak yazılan mühür bir sonraki süreçte **farklı** anahtarla karşılaştırılır → `_load_persistent_hwm` `compare_digest` başarısız → `return fallback` (`:160-162`) → HWM boot-time'a çöker. Yorum ile davranış çelişir.
- **İstismar / Bypass Senaryosu:** (1) Saldırgan `LICENSE_HWM_KEY`'in yazılamadığı bir ortam yaratır (izinsiz `logs/`, bozuk DPAPI profili). (2) Uygulama bir kez çalışır, HWM diske yazılır ama anahtar kalıcılaşmaz. (3) Saldırgan saati geri alır ve yeniden başlatır. (4) `_hmac_key` yeni rastgele anahtar üretir → eski mühür uyuşmaz → HWM boot-time'a düşer → rollback tespit edilmez. Ayrıca `exp` kontrolü geri alınmış `now` ile değerlendirilir.
- **Düzeltme (Remediation):**
  ```python
  def _hmac_key(self) -> bytes:
      secrets = getattr(self.client, "_secrets", None)
      if secrets is not None and secrets.has_secret("LICENSE_HWM_KEY"):
          return secrets.get_secret("LICENSE_HWM_KEY")
      # vault anahtarı üretilemiyorsa HWM güvenilmez — fail-closed
      raise LicenseError(
          "License HWM integrity key unavailable; refusing to treat HWM as trustworthy.",
          code="HWM_KEY_UNAVAILABLE",
      )
  ```
  Eğer meşru "ilk kurulum" akışı gerekiyorsa: anahtarı **süreç-ömrü boyunca** türet-tut, `store_secret` başarısızsa CRITICAL log + UI uyarısı ver ve HWM'yi `now - MAX_GRACE` conservat-derived floor'dan başlat (yorumdaki davranışı kodla hizala).

### [L-4] `LicenseValidator` fallback wildcard lisansı her makinede geçerli kılıyor (latent)
- **Konum:** `src/security/license/validator.py:83` (`self._allow_wildcard = allow_wildcard_license is True`), `:226-228` (`*` indeterminate sayılmaz), `:369-372` (eşitlik dalı).
- **Etki / Risk:** `allow_wildcard_license=True` geçirildiğinde `payload.hardware_fingerprint == "*"` olan token, fingerprint eşitliğine girmeden `logger.warning(...)` ile kabul edilir → HWID bağı etkisizleşir. Bu modül şu an üretimde bağlı değildir, ancak constructor parametresi üretimde yanlışlıkla set edilirse ya da gelecekte kablolanırsa koruma ölür. Comment "TEST MODE ONLY" dese de bunu **zorlayan bir kontrol yoktur**.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # validator.py:83
  self._allow_wildcard = allow_wildcard_license is True
  # validator.py:226-228
  fp = fingerprint.strip().lower()
  if not fp or fp == "*":
      return False                      # "*" indeterminate DEĞİL
  ...
  # validator.py:369-372
  if payload.hardware_fingerprint == self.hardware_fingerprint:
      pass
  elif payload.hardware_fingerprint == "*" and self._allow_wildcard:
      logger.warning("Wildcard license accepted (TEST MODE ONLY)")
  ```
  `_allow_wildcard` constructor'dan gelir; env fallback'i (G4) kaldırılmış olması iyi bir düzeltmedir, ancak constructor yolu hâlâ açık ve sınıflandırma yalnız comment'tedir.
- **İstismar / Bypass Senaryosu:** Bir wiring hatası `LicenseValidator(pub, allow_wildcard_license=True)` yapar (ör. bir "dev" bayrağının yanlışlıkla üretime sızması). Cloud/backend wildcard ("*") token üretirse (veya saldırgan kendi wildcard token'ını imzalatırsa) tek token tüm makinelerde çalışır.
- **Düzeltme (Remediation):**
  ```python
  def __init__(self, ..., allow_wildcard_license: bool | None = None, ...):
      if allow_wildcard_license is True and getattr(sys, "frozen", False):
          raise LicenseError("Wildcard licenses are disabled in frozen builds", code="WILDCARD_FORBIDDEN")
      self._allow_wildcard = allow_wildcard_license is True
  ```
  Wildcard kabul edildiğinde `AuthStatus.has_valid_license`'ı düşür ve CRITICAL log + UI uyarısı ver.

### [L-5] Token içindeki sentinel fingerprint doğrulama kapısından geçirilmiyor (latent)
- **Konum:** `src/security/license/validator.py:204-228` (`_is_indeterminate_fingerprint`), `:362-367` (yalnız `self.hardware_fingerprint` için çağrı), `:369-387` (eşitlik karşılaştırması).
- **Etki / Risk:** Fail-closed kapı yalnız **yerel** fingerprint için uygulanır. Token'ın taşıdığı `payload.hardware_fingerprint` doğrudan `==` karşılaştırmasına girer. İki makine WMI okuması başarısız olduğunda koleksiyon `FALLBACK-<hostname>-<mac>` üretir; aynı hostname şablonu + aynı MAC (klonlanmış/atlanmış VM) durumunda çakışma olasılığı vardır. Ayrıca yerel taraf sentinel değilken token `UNKNOWN_*` taşıyorsa (ör. elle üretilmiş/kurcalanmış token) bu, eşitlik sağlanmadıkça reddedilir — asıl risk **her iki tarafın da aynı sentinel'e yakınsamasıdır**.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # validator.py:362-367 — SADECE yerel taraf kontrol edilir
  if self._is_indeterminate_fingerprint(self.hardware_fingerprint):
      raise LicenseError("Hardware identity could not be determined ...", code="HARDWARE_INDETERMINATE")
  # validator.py:369 — token tarafı kapıdan GEÇMEZ
  if payload.hardware_fingerprint == self.hardware_fingerprint:
      pass
  ```
  `_is_indeterminate_fingerprint` `payload` için hiç çağrılmaz.
- **İstismar / Bypass Senaryosu:** Klonlanmış VM fleet'inde (aynı hostname deseni + aynı fiziksel olmayan MAC) iki makine aynı `FALLBACK-*` üretir → birinde üretilen token diğerinde `==` ile geçer. Veya `sys.platform != "win32"` dalında (`collector.py:180-192`) tüm bileşenler sentinel olup `NON_WIN32-<node>-<machine>-<processor>-<mac>` üretilir; `mb_uuid.startswith("FALLBACK-")` koşulu bu dala **girmez** (`mb_uuid` `NON_WIN32-` ile başlar) → `_is_indeterminate_fingerprint` yerel tarafta `FALLBACK-` marker'ını yakalayamaz, ancak `NON_WIN32-` hiç marker değildir. Sonuç: sentinel-benzeri ama tanınmayan bir fingerprint doğrulanır.
- **Düzeltme (Remediation):**
  ```python
  if self._is_indeterminate_fingerprint(self.hardware_fingerprint):
      raise LicenseError(..., code="HARDWARE_INDETERMINATE")
  if self._is_indeterminate_fingerprint(payload.hardware_fingerprint):   # token tarafı da
      raise LicenseError("License token carries an indeterminate hardware id", code="HARDWARE_INDETERMINATE")
  ```
  Ayrıca `_indeterminate_markers`'a `"NON_WIN32-"` ekle (collector'ın non-win32 fallback öneki) ve `_INVALID_UUIDS`/MAC sentinel kontrolünü koleksiyon tarafında da uygula.

### [L-6] Offline grace yalnız çağıran beyanına bağlı; device binding ve `exp`↔`offline_until` semantiği zayıf
- **Konum:** `src/security/cloud/license_flow.py:279-292` (`is_offline` parametresi ve docstring), `:403-407` (`exp` ve `offline_until` ayrı kontroller), `:343-363` (device binding); `src/launcher/auth.py:121` (`verify_cloud_ticket(ticket_str, is_offline=True)`).
- **Etki / Risk:** `is_offline` bir "çağıran sözleşmesi"dir; yanlış çağrı (varsayılan `False`) offline grace'i **hiç uygulamaz** ve lisans "online" sayılır. Ayrıca `exp` (`:403`) ve `offline_until` (`:406`) bağımsız değerlendirilir; uzun `exp` + kısa `offline_until` bir ticket, `is_offline=False` ile çağrıldığında sonsuza dek geçerli olur. Device binding ise çağıran `expected_device_id` vermezse yalnız `client.get_device_id()`'ye dayanır; o da vault'tan silinirse (fresh install/cleared vault) fail-closed kapı (`:349-354`) yine çalışır — bu kısım doğru. Asıl açık, `is_offline`'ın beyana bağlı olması ve `exp`'in `offline_until`'ı ezmesidir.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # license_flow.py:287-291 (docstring)
  """... `is_offline` is enforced by the CALLER stating
  its verification mode. ... MUST pass is_offline=True ..."""
  # license_flow.py:403-407
  if now > data["exp"]:
      raise LicenseError("Cloud license ticket has expired.", code="LICENSE_EXPIRED")
  if is_offline and now > data["offline_until"]:      # is_offline False ise ATLANIR
      raise LicenseError("Cloud offline grace period has expired.", code="OFFLINE_GRACE_EXPIRED")
  ```
  `verify_cloud_ticket(data["license_token"])` çağrısı `activate_license` içinde `is_offline` **vermeden** yapılır (`:261`) — bu online yolda doğrudur. Ancak herhangi bir gelecek çağıran varsayılanı unutursa grace atlanır.
- **İstismar / Bypass Senaryosu:** Bir çağıran `verify_cloud_ticket(ticket)`'ı (`is_offline` default `False`) kullanır. Ticket'ın `exp`'i 1 yıl, `offline_until`'ı 7 gün olsa bile `offline_until` kontrolü atlanır → makine ağdan tamamen kopuk şekilde 1 yıl boyunca lisanslı sayılır. Backend grace politikası fiilen devre dışı kalır.
- **Düzeltme (Remediation):**
  ```python
  # is_offline'ı beyana bırakma; kaynağı türet:
  effective_deadline = min(data["exp"], data["offline_until"]) if offline_mode_detected else data["exp"]
  if now > effective_deadline:
      raise LicenseError("Cloud license/grace deadline passed.", code="LICENSE_EXPIRED")
  # ve her zaman device_id doğrula:
  claims = self.flow.verify_cloud_ticket(ticket_str, expected_device_id=hwid, is_offline=True)
  ```
  `offline_mode_detected`'i "ağ erişilemedi" sonucundan değil, **kalıcı HWM + son başarılı online sync zamanı**ndan türet.

## 4. Doğrulanamayan / Dış Bağımlılıklar

- **Test çalıştırılmadı.** `tests/unit/test_license_validator.py`, `test_adversarial_final_gate.py`, `test_adversarial_stress.py`, `test_anti_tamper.py`, `test_review_round2_fixes.py` yalnız okundu/varlıkları görüldü; geçtikleri veya bulguları yakaladıkları iddia edilmiyor. Testlerin `LicenseValidator(hardware_fingerprint="HW_ANY")` gibi **sabit** fingerprint'lerle kurulduğu görüldü — yani L-5 (token-tarafı sentinel) ve L-4 (wildcard) muhtemelen test edilmiyor.
- **Backend (`Universal-CAN-Cloud`).** `offline_until` ve `exp` değerlerinin sunucu tarafında nasıl üretildiği bu repoda değil; L-6'nın fiili etkisi backend grace politikasına bağlı. `nonce`, `kid`, `device_id` üretimi doğrulanmadı.
- **`main.py` lisans kapısı.** `src/main.py` incelenmedi (kapsam dışı); çekirdek uygulamanın kendi içinde ikinci bir lisans kapısı olup olmadığı doğrulanamadı. L-1'in "launch_main_app sonrası ne olur" kısmı buna bağlı. **Not:** `grep` `main.py`'de lisans/HWID referansı bulmadı; bu, çekirdekte ek kapı olmadığına işaret eder ama `desktop_app` (main.py'nin import ettiği UI) içindeki geniş dosya tümüyle satır-satır taranmadı.
- **`WindowsDPAPISecretBackend` fallback zinciri.** `secret_provider.py:673-678` ve `:712-764` fallback davranışı yalnız üst düzey okundu; L-3'ün "vault yazılamaz" senaryosunun gerçekte hangi koşullarda oluştuğu (DPAPI entropy kilidi, roaming profil) ölçülmedi.
- **`uuid.getnode()` rastgelelik bayrağı (B-1).** `collect_primary_mac` fallback'inin gerçek makinelerde kaç kez random node döndürdüğü ölçülmedi; `(node>>40)&0x2` bit kontrolünün eklenmemiş olduğu koddan doğrulandı, etkisi saha verisi gerektirir.
- **Anti-tamper `enforce()` bağlama niyeti (G-1).** `guard.py`'nin hiç çağrılmaması kodla doğrulandı; bunun "bilinçli olarak kapatıldı" mı yoksa "eksik wiring" mi olduğu repo dokümanlarından (AGENTS.md/MASTER_PLAN) bu oturumda teyit edilmedi.
- **T47-B paralel düzenlemeleri.** Aynı repoda başka dosyalar düzenlendiği için bu rapordaki satır numaraları yazım anındaki dosya içeriğine aittir; kanıt alıntıları (kod metni) satır numaralarından bağımsız kimlik taşır.
- **`_hmac_key` "in-memory HWM only" yorumu ↔ kod.** Yorum, `self._hwm_path` set edilmişken dosyanın **yine yazıldığını** (ve bir sonraki okumada mühür uyuşmazlığı doğacağını) yansıtmıyor; bu çelişki koddan doğrulandı ancak üretimde bu yolun gerçekten tetiklenip tetiklenmediği çalıştırılmadan bilinemez.

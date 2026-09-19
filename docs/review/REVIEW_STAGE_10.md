# UCANLAB Kapsamlı Review — Aşama 10 / Kapanış Denetimi

**İncelenen revizyon:** `011b83599836c7f5b9b51c0f946565684d505ca8`

**Kapsam:** Launcher/preflight, update manager, target integrity, dependency/CI yapılandırması, logging/exception yolları, anti-tamper ve önceki bulguların çapraz etkileri.

**Kısıt:** Bu aşamada yalnızca review yapıldı. Kaynak koda veya testlere düzeltme uygulanmadı.

## Doğrulama

- Launcher, updater, auth, prereq, anti-tamper, knowledge-pack, license validator, logging, `pyproject.toml`, dependency listeleri ve GitHub/GitLab CI dosyaları okundu.
- Hedef testler: **64 passed, 1 skipped**
  - `test_launcher.py`
  - `test_main.py`
  - `test_logging.py`
  - `test_secret_provider.py`
  - `test_contracts_exceptions.py`
  - `test_remediation_suite_complete.py`
- Ruff: **All checks passed**.
- Bandit: **temiz** (`bandit -r src/ -ll -q`).
- `requirements.txt` üzerinde pip-audit: **No known vulnerabilities found**; CI’deki `PYSEC-2026-2447` ignore kuralı nedeniyle 2 advisory ignore edildi.
- Launcher trust-boundary probe sonucu:

```text
manifest_override_in_normal_process: /tmp/attacker-manifest
manifest_override_in_frozen_process: /tmp/attacker-manifest
unsigned_manifest_gate_with_spoofed_env: True
```

## Yeni bulgular

### M1 — Unsigned update manifest test kapısı yalnızca environment variable varlığına güveniyor

**Önem:** Orta — frozen binary’de kapalı olsa da source/unfrozen launcher güvenlik iddiası ile gerçek kontrol arasında fark var.

#### Kanıt

- `src/launcher/app.py:113-128` `custom_update_manifest` yolunu açan `_unsigned_manifest_allowed()` yalnızca process’in frozen olup olmadığını ve `PYTEST_CURRENT_TEST` environment variable’ının dolu olup olmadığını kontrol ediyor.
- Aynı fonksiyon gerçek bir pytest runtime’ı, çağıran process’i veya test framework provenance’ını doğrulamıyor.
- Kontrollü probe’da yalnızca şu environment değeriyle kapı açıldı:

```text
PYTEST_CURRENT_TEST=spoofed-by-caller
unsigned_manifest_gate_with_spoofed_env: True
```

#### Etki

Source/unfrozen launcher sürecine environment yazabilen bir çağıran, unsigned `custom_update_manifest` yolunu test yolu gibi açabilir. Bu manifest update routing ve mandatory-update obligation kararlarını imzasız veriye bağlar. Frozen build’de `sys.frozen` kontrolü bu yolu kapatıyor; ancak source tabanlı dağıtım, yanlış paketlenmiş/unfrozen çalıştırma veya geliştirici araçlarının production’da kullanılması için güvenlik sınırı zayıf kalıyor.

#### Çözüm önerisi

- Unsigned manifest kabulünü environment variable’a bağlamayın.
- Testler için ayrı dependency injection veya açık test-only constructor kullanın; production entrypoint bu API’yi hiç expose etmesin.
- Unfrozen çalışma ortamını production kabul eden dağıtımlarda signed manifest zorunlu olsun.
- Negatif test ekleyin: `PYTEST_CURRENT_TEST` elle ayarlansa bile unsigned manifest reddedilmeli.

---

### M2 — Python dependency çözümlemesi aralıklarla yapılıyor; hash kilitli reproducible lock yok

**Önem:** Orta — build zamanında tedarik zinciri ve reproducibility riski.

#### Kanıt

- `requirements.txt:8-40` bağımlılıkların çoğu yalnızca alt/üst sürüm aralıklarıyla tanımlı (`>=...,<...`); exact version ve hash yok.
- `requirements.txt:3-5` repository’nin kendisi de hash lock’ın commit edilmediğini açıkça belirtiyor.
- `.github/workflows/ci.yml` ve `.gitlab-ci.yml` doğrudan `pip install -r requirements.txt` çalıştırıyor; `--require-hashes` kullanan bir lock kurulumu yok.
- GitLab pipeline’ında `.gitlab-ci.yml:66-73` pip-audit job’ı `allow_failure: true`; bu mirror pipeline’da dependency audit başarısızlığı merge/pipeline sonucunu zorunlu olarak durdurmuyor.
- Bu çalışmadaki güncel `pip-audit` taraması requirements için bilinen zafiyet bulmadı; bu, aralık çözümlemesinin gelecekte aynı paketleri getireceğini garanti etmez.

#### Etki

Aynı commit farklı zamanda farklı dependency sürümleri kurabilir. Üst sınırlar bazı riski azaltıyor fakat belirli artifact/hash doğrulaması sağlamıyor. GitLab mirror’da audit başarısızlıkları izin verilen hata olduğu için güvenlik sinyali CI sonucunda gözden kaçabilir.

#### Çözüm önerisi

- CI ve release için hash’li lock dosyası üretin ve `pip install --require-hashes` kullanın.
- Dependency güncellemelerini kontrollü bot/PR sürecine bağlayın; source ve wheel hash’lerini saklayın.
- GitLab pip-audit job’ını zorunlu yapın veya yalnızca advisory database erişilemezliği ile gerçek vulnerability sonucunu ayrı durumlara ayırın.
- Release build’in kurduğu dependency setini artifact metadata ile kaydedin.

## Önceki bulguların final çapraz kontrolü

### H1 — Frozen target manifest environment override hâlâ açık

Bu, önceki aşamada raporlanan hedef hash manifesti bulgusunun kapanmadığını doğrulayan bir yeniden kontroldür; yeni bir bulgu olarak sayılmadı.

- `src/launcher/app.py:48-56` `_manifest_path()` `UCANLAB_TARGET_MANIFEST` değerini `sys.frozen` kontrolü olmadan kabul ediyor.
- Probe’da frozen flag zorla açıkken bile sonuç değişmedi:

```text
manifest_override_in_frozen_process: /tmp/attacker-manifest
```

Bu nedenle frozen target integrity kontrolü, environment’ı değiştirebilen bir başlatıcı/hostile wrapper tarafından uygulamanın beklenen manifesti dışındaki bir dosyaya yönlendirilebilir. Önceki Stage 4 raporundaki bulgu açık kalmıştır. Çözüm, production/frozen build’de manifest yolunu bundle veya imzalı, yazılamaz/allowlist edilmiş kurulum köküne sabitlemek ve environment override’ı yalnızca gerçek test harness’ında etkinleştirmektir.

## Bulgusuz kapanış kontrolleri

- Launcher child-process çağrısı `shell=False` kullanıyor ve ekstra argümanlar allowlist’ten geçiriliyor.
- Update URL’lerinde HTTPS, host allowlist, redirect kapatma, SHA-256 ve gerekli durumda Ed25519 imzası kontrol ediliyor.
- Logging formatter protected alanları override ettirmiyor; bu aşamada yeni token/secret log sızıntısı doğrulanmadı.
- Anti-tamper, knowledge-pack manifest/file-set binding ve lisans HWM akışlarında yeni bağımsız bypass tespit edilmedi.
- Önceki aşamalarda raporlanan UDS, safety gateway, cloud endpoint, HWM, replay ve UI flash bulguları bu aşamada yeniden uygulanmadı; yalnızca çapraz etkileri not edildi.

## Sonuç

Aşama 10’da:

- **2 yeni bulgu** tespit edildi:
  - **Orta:** `PYTEST_CURRENT_TEST` varlığı unsigned manifest test kapısını spoof edebiliyor.
  - **Orta:** Python dependency seti hash kilitli ve reproducible değil; GitLab pip-audit kapısı da zorunlu değil.
- **1 önceki yüksek önem bulgusu** yeniden doğrulandı:
  - Frozen target manifest yolu environment override’a açık.

Hedef testler **64 passed, 1 skipped**, Ruff ve Bandit **temiz**, requirements pip-audit sonucu **bilinen vulnerability yok** (CI’deki ignore kuralı notuyla).

Bu kapanış aşamasında da hiçbir bulgu uygulanmadı.

**Aşama 10 tamamlandı ve burada duruyorum.**

# UCANLAB Kapsamlı Review — Aşama 8

**İncelenen revizyon:** `011b83599836c7f5b9b51c0f946565684d505ca8`

**Kapsam:** React frontend, pywebview desktop bridge, ECU flash UI–backend veri sözleşmesi, cloud telemetry upload UI akışı ve WebView dış kaynakları.

**Kısıt:** Bu aşamada yalnızca review yapıldı. Kaynak koda veya testlere düzeltme uygulanmadı.

## Doğrulama yöntemi

- Bulguyla ilişkili dosyaların tamamı okundu: `src/ui/frontend/src/components/ecu/EcuFlashingView.tsx`, `src/ui/frontend/src/components/reports/ReportsExportView.tsx`, `src/ui/frontend/index.html` ve ilgili `src/ui/desktop_app.py`/cloud bridge akışları.
- Flash UI payload’ı ile backend ön koşulları karşılaştırıldı.
- Frontend derlemesi çalıştırıldı: `npm run build` — **başarılı** (`tsc` + Vite).
- Statik probe sonucu:

```text
flash_ui_payload_keys: ['memoryAddress', 'blockSize', 'sizeBytes']
missing_required_auth_material: ['firmwareSignature', 'trustedPubkey']
backend_zero_fill_fallback: True
backend_prereq_requires_signature: True
backend_prereq_requires_target_identity_or_skip: True
```

- Fresh çalışma ortamında Python `pytest`, `webview`, `can` ve `cantools` kurulu olmadığı için Python bridge testleri bu aşamada çalıştırılmadı. Bu nedenle sonuçlar frontend build’i ve kaynak kodu karşılaştırmasıyla sınırlıdır.

## Bulgular

### H1 — ECU Flash UI seçilen firmware’i backend’e göndermiyor

**Önem:** Yüksek — gerçek flash akışı kullanılamaz; simülasyon ile gerçek işlem arasındaki sözleşme yanıltıcıdır.

#### Kanıt

Frontend’de `src/ui/frontend/src/components/ecu/EcuFlashingView.tsx:331-357`:

- Challenge yalnızca `action_type`, ECU, `id` ve `fileName` ile isteniyor.
- `flashStart` çağrısına `sizeBytes`, `memoryAddress` ve `blockSize` gönderiliyor.
- Seçilen dosyanın byte içeriği, tam SHA-256 değeri, `firmwareSignature` veya `trustedPubkey` gönderilmiyor.

Dosyanın tamamı frontend’de okunup SHA-256 hesaplanıyor (`:164-183`), ancak sonuç yalnızca UI’da kısaltılmış bir metin olarak gösteriliyor; backend payload’ına konulmuyor.

Backend tarafında:

- `src/ui/desktop_app.py:2527-2537` gerçek modda başlamadan önce firmware imzası ve trust anchor ön koşulunu zorunlu tutuyor.
- `src/ui/desktop_app.py:2557-2566` `config.data` yoksa `sizeBytes` kadar `0x00` üretiyor.
- `src/ui/desktop_app.py:2568-2579` gerçek `FlashingConfig` bu sıfır dolgulu payload ve eksik imza/public key ile oluşturuluyor.

#### Etki

Bu UI akışıyla gerçek modda seçilen dosyanın içeriği flash motoruna ulaşmıyor. Üstelik imza ve trust-anchor alanları da gönderilmediği için backend’in fail-closed ön koşulu gerçek flash başlamadan işlemi reddediyor.

Simülasyon modunda ise frontend yalnızca seçilen dosyanın adı/boyutu üzerinden ilerliyor; loglarda protokol adımları gösteriliyor fakat dosya ECU’ya yazılmıyor. Bu davranış UI’da belirtilmiş olsa da, gerçek modla aynı dosya sözleşmesinin kullanılmaması uygulama bütünlüğü ve operatör beklentisi açısından kritik bir uyumsuzluk oluşturuyor.

Bu bulgu Aşama 7’deki trust-anchor ve flash-token bulgularını ortadan kaldırmaz; tersine, UI’nin bu güvenlik malzemelerini hiç taşımaması nedeniyle backend güvenlik kapısının pratikte sürekli fail-closed olmasına yol açar.

#### Çözüm önerisi

- Browser `File` nesnesini doğrudan güvenilir kabul etmeyin; dosyayı güvenli bir native import/staging API’si üzerinden backend’e aktarın.
- Backend’in aldığı gerçek byte dizisi için hash’i kendisi hesaplasın; renderer’dan gelen hash yalnızca karşılaştırma/UX verisi olsun.
- İmzalı manifest ve uygulama tarafından sabitlenmiş trust anchor akışını kullanın; public key’i renderer’dan kabul etmeyin.
- Challenge payload’ına gerçek firmware SHA-256, boyut, hedef ECU ve flash adres aralığını bağlayın.
- UI entegrasyon testi ekleyin: seçilen iki farklı dosya backend’e farklı byte dizisi olarak ulaşmalı; imza/trust-anchor yoksa gerçek flash başlamamalı ve seçilen dosyanın adıyla sıfır dolgulu fallback arasında sessiz dönüşüm olmamalı.

---

### M1 — Cloud upload UI “5 MB parçalı/resumable” diyor, bridge raw upload’ı 1 MB’de kesiyor

**Önem:** Orta — büyük seansların kullanıcıya açık bir hata mesajı olmadan değil, backend hata mesajıyla başarısız olması; ürün sözleşmesi ve kullanılabilirlik problemi.

#### Kanıt

- `src/ui/frontend/src/components/reports/ReportsExportView.tsx:146-148` telemetri seansının **5 MB parçalar halinde** parçalı/resumable aktarılacağını söylüyor.
- Aynı dosyada `:75-89` tüm frame’ler tek bir string içinde oluşturulup tek `cloudUploadRawContent(...)` çağrısına veriliyor; parçalara bölme veya resumable session başlatma yok.
- `src/ui/desktop_app.py:818-850` `cloud_upload_raw_content` için sabit **1 MiB** sınırı var ve aşılırsa içerik doğrudan reddediliyor.
- Frontend’de upload öncesi içerik boyutu kontrolü veya chunk retry/continuation mantığı bulunmuyor.

#### Etki

Seans içeriği 1 MiB’yi aştığında UI’nin vaat ettiği 5 MB parçalı aktarım gerçekleşmiyor; tek çağrı backend tarafından reddediliyor. Kullanıcıya gösterilen “Parçalı Resumable” durumu ile gerçek aktarım yolu birbirinden farklı.

#### Çözüm önerisi

- Gerçek bir upload session/chunk API’si kullanın; chunk boyutu, sıra numarası, checksum, retry ve resume token’ı backend ile birlikte tanımlayın.
- Tek çağrılık raw upload kullanılacaksa UI metnini ve limitleri gerçek davranışla eşleştirin.
- Upload başlamadan önce içerik boyutunu ölçüp sınırı aşan seanslar için açık hata/alternatif export sunun.
- 1 MiB altı, tam 1 MiB ve 1 MiB üstü seanslarla entegrasyon testleri ekleyin.

---

### M2 — Yerel desktop WebView açılışta üçüncü taraf font kaynaklarına bağlanıyor

**Önem:** Düşük-orta — hassas/izole çalışma ortamlarında veri gizliliği ve deterministik açılış riski.

#### Kanıt

`src/ui/frontend/index.html:9-11` şu dış kaynakları yükletiyor:

- `https://fonts.googleapis.com`
- `https://fonts.gstatic.com`
- Google Fonts stylesheet’i üzerinden Inter ve JetBrains Mono

Bu uygulama pywebview içinde yerel bir `file://`/dist sayfası olarak açılıyor (`src/ui/desktop_app.py` içindeki `webview.create_window` akışı); fontlar bundle içine gömülmemiş.

#### Etki

Uygulama başlatıldığında üçüncü taraf sunuculara ağ isteği oluşuyor. Bu, teşhis aracının çalıştırıldığı ortamın dış servise görünmesine ve ağ erişimi olmayan/izole servis ortamlarında font yükleme gecikmesi veya farklı render davranışına neden olabilir.

#### Çözüm önerisi

- Font dosyalarını uygulama paketine dahil edip CSS üzerinden yerel olarak yükleyin.
- Dış kaynak kullanımını production build’den kaldırın veya kullanıcı tarafından açıkça etkinleştirilen, belgelenmiş bir seçenek yapın.
- Offline/air-gapped build için dış ağ isteği olmadığını test eden bir smoke test ekleyin.

## Sonuç

Aşama 8’de üç bulgu tespit edildi:

- **1 yüksek:** Flash UI seçilen firmware içeriğini ve gerekli doğrulama malzemesini backend’e göndermiyor.
- **1 orta:** Cloud upload UI ile gerçek 1 MiB raw-upload sınırı uyuşmuyor.
- **1 düşük-orta:** Yerel WebView üçüncü taraf Google Fonts kaynaklarına bağlanıyor.

Frontend derlemesi başarılı oldu. Python bridge testleri, bu fresh ortamda gerekli Python bağımlılıkları bulunmadığı için çalıştırılamadı; sonuçlar bu nedenle kaynak karşılaştırması ve frontend build’iyle sınırlı tutuldu.

**Aşama 8 tamamlandı ve burada duruyorum. Hiçbir bulgu uygulanmadı.**

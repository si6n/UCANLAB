# Donanım ve Windows Doğrulama Listesi (Tamirci Akışı)

> CI ve simülatör sonuçları bu listeyi **karşılamaz**. Buradaki her madde gerçek Windows makinesinde ve/veya gerçek araç/adaptörle elle yapılır. Sonuç, tarih ve makine/adaptör bilgisiyle işaretlenir.
>
> Durum sütunu: ☐ yapılmadı · ✅ geçti · ❌ kaldı (not düş)

## Aşama 3 — Giriş ve yetkilendirme (Windows)

| # | Adım | Beklenen | Durum |
|---|---|---|---|
| W3-1 | Temiz bir Windows kullanıcı hesabında launcher'ı çift tıklayarak aç (lisans yok). | Konsolda "tarayıcıda ucanlab.org açılıyor" yazar, varsayılan tarayıcı `ucanlab.org/auth/desktop?...` açar. | ☐ |
| W3-2 | Tarayıcıda giriş yap. | Sekme "Giriş başarılı" der, launcher "Giriş tamam. Lisans etkin; internetsiz 7 gün…" yazar ve ana uygulamayı açar. | ☐ |
| W3-3 | Windows Güvenlik Duvarı ilk dinlemede uyarı gösteriyor mu? | 127.0.0.1'e bağlanan dinleyici için uyarı çıkmamalı; çıkarsa not düş. | ☐ |
| W3-4 | Tarayıcı sekmesini yönlendirmeden kapat, 5 dk bekle. | Launcher cihaz koduna geçer, kodu ve `ucanlab.org/cihaz` adresini yazar. | ☐ |
| W3-5 | Kodu telefondan `ucanlab.org/cihaz` sayfasında gir. | Telefon "Bilgisayar bağlandı", launcher birkaç saniye içinde uygulamayı açar. | ☐ |
| W3-6 | `/cihaz` sayfasında "Bu ben değilim". | Launcher "Giriş web sitesinde reddedildi" yazar, uygulama **açılmaz**. | ☐ |
| W3-7 | `python main.py --register-url-protocol` (veya paketli exe ile) çalıştır; `HKCU\Software\Classes\ucanlab\shell\open\command` değerine bak. | `"…\UCanLab.exe" "%1"` biçiminde, tek tırnaklı `%1`. | ☐ |
| W3-8 | Uygulama açıkken web sayfasındaki "Uygulamada aç" butonuna bas. | Windows onay sorar (ilk sefer), kısa süreli süreç kodu dinleyiciye iletir, giriş tamamlanır; ek pencere açılmaz. | ☐ |
| W3-9 | `ucanlab://auth/callback?code=x&state=y&port=5000` adresini Çalıştır'a yaz. | Reddedilir (çıkış kodu 2), hiçbir ağ çağrısı olmaz. | ☐ |
| W3-10 | Giriş sonrası DPAPI: `%APPDATA%`/vault'ta oturum, cihaz jetonu ve bilet düz metin olarak **görünmemeli**. | Dosyalar şifreli; jeton/bilet düz metin aranınca bulunmaz. | ☐ |
| W3-11 | Log dosyalarında `sess_`, `devtok_`, `code_verifier`, `device_code` ara. | Hiçbiri bulunmaz (yalnız HWID'in ilk 8 karakteri olabilir). | ☐ |
| W3-12 | İnterneti kes, uygulamayı yeniden başlat. | Giriş istemez; üstte kalan çevrimdışı gün sayısı görünür. | ☐ |
| W3-13 | Saati 8 gün ileri al, uygulamayı başlat (internet kapalı). | "Lisansınızın çevrimdışı süresi doldu…" mesajı; ana uygulama açılmaz / giriş ekranı gelir. | ☐ |
| W3-14 | Saati 3 gün geri al (önce bir kez çevrim içi açılmış olmalı). | "Bilgisayarın saati geri alınmış görünüyor…" mesajı. | ☐ |
| W3-15 | Aynı hesapta tek lisans varken ikinci bilgisayarda giriş. | "Lisansınız başka bir bilgisayarda kullanılıyor…" veya "boş lisans yok" mesajı; ilk bilgisayarın lisansı etkilenmez. | ☐ |
| W3-16 | Hesap rolü "Görüntüleyici" iken giriş. | "Hesap rolünüz cihaz eklemeye izin vermiyor…" mesajı. | ☐ |
| W3-17 | 47820-47822 portlarından birini başka bir programla meşgul et, girişi dene. | Bir sonraki boş porta geçer; üçü de doluysa doğrudan cihaz koduna düşer. | ☐ |

## Aşama 4 — Mod ve araç seçimi (Windows + gerçek araç)

| # | Adım | Beklenen | Durum |
|---|---|---|---|
| W4-1 | Kurulu uygulamada ilk açılış (starter lisans). | "Uygulamayı nasıl kullanacaksınız?" çıkar; Mühendis kartı kilitli ve "Bu mod paketinizde yok" yazar. | ☐ |
| W4-2 | Tamirci'yi seç, uygulamayı kapat-aç. | Mod sorusu tekrar gelmez; "Son seçilen araç" kartı görünür. `%APPDATA%` altında `mechanic_prefs.json` oluşmuştur. | ☐ |
| W4-3 | Pro lisansla Ayarlar › Donanım › Kullanım modu › "Mühendis moduna geç". | Uzman ekranları açılır; tekrar açılışta Mühendis hatırlanır. | ☐ |
| W4-4 | Kurulu (PyInstaller) sürümde araç listesini aç. | Liste dolu gelir (katalog paketlenmiş); `CATALOG_INVALID` görülmez. | ☐ |
| W4-5 | Gerçek bir J1939 kamyonda (ör. Scania) dinleme bağlantısı + adres talepleri görüldükten sonra yanlış marka (Volvo) seçili iken kimlik kontrolü. | "Seçtiğiniz araç ile aracın kendisi uyuşmuyor… Scania" uyarısı ve "Scania olarak değiştir" düğmesi. | ☐ |
| W4-6 | VIN yayını (PGN 65260) olan kamyonda doğru marka seçili iken kimlik kontrolü. | Uyarı çıkmaz; log'da VIN yalnız maskeli (`YS2**********1234`) görünür. | ☐ |
| W4-7 | Kimlik bilgisi yayınlamayan araçta (otomobil, OBD isteği yapılmadan). | Uyarı çıkmaz ("bilinmiyor" sessiz kalır); tahmin üretilmez. | ☐ |

## Aşama 5 — Adaptör ve bağlantı testi (Windows + gerçek adaptör/araç)

| # | Adım | Beklenen | Durum |
|---|---|---|---|
| W5-1 | PEAK sürücüsü **kurulu değilken** PCAN-USB tak, adaptör ekranını aç. | "PCAN adaptörü için sürücü kurulu değil… peak-system.com" uyarısı; uygulama çökmez. | ☐ |
| W5-2 | Sürücüyü kur, "Kurdum, tekrar dene". | "PCAN-USB bulundu ✓". | ☐ |
| W5-3 | Kvaser Leaf takılı ve takılı değilken tara. | Takılıyken "Kvaser … bulundu"; takılı değilken Kvaser **sanal** kanalları listede görünmez. | ☐ |
| W5-4 | RP1210 VCI (ör. Nexiq USB-Link, DG DPA5) sürücüsü kurulu makinede tara. | "RP1210 (…)" satırı; bağlantı testinde cihaz takılı değilse "Adaptör açılamadı…" | ☐ |
| W5-5 | Kamyonda (J1939 250k) kontak açık, bağlantı testi. | "Hazır ✓ … N kontrol ünitesi görüldü"; test sırasında adaptörün TX LED'i yanmaz / bus analizöründe adaptörden **hiç çerçeve yok** (ACK dahil). | ☐ |
| W5-6 | Aynı kamyonda kontak kapalı. | "Araçtan veri gelmiyor. Kontak açık mı?…" | ☐ |
| W5-7 | Kamyon seçiliyken 500k ağa (ör. bazı yeni kamyonlar) bağlan. | 250k'da hata çerçevesi → 500k'da "Hazır"; log'da iki deneme. | ☐ |
| W5-8 | Otomobilde (gateway'li, OBD soketi sessiz) test. | "Adaptör takılı ama araç şu an kendiliğinden veri göndermiyor…" (kullanılabilir). | ☐ |
| W5-9 | PGN 65271 yayınlayan kamyonda akü zayıfken (≤23 V). | "Akü zayıf (… V)" uyarısı ve "Yine de devam". Değer bir multimetre ölçümüyle karşılaştırılır. | ☐ |
| W5-10 | Test sürerken USB kablosunu çek. | "Adaptör açılamadı…" ya da "Hatta trafik yok"; uygulama donmaz, E-Stop tetiklenmez. | ☐ |
| W5-11 | Başarılı testten sonra Uzman ekranında veri yolu durumu. | Seçilen adaptör + bulunan hız, durum **PASSIVE**, TX kilitli. | ☐ |
| W5-12 | PCAN donanım dinleme onayı (`PCAN_LISTEN_ONLY` okuması) desteklenmeyen eski bir PCAN'da test. | Log'da "hardware listen-only could not be independently verified" uyarısı; test yine yalnız dinler. | ☐ |

## Sonraki aşamalar

Aşama 6 ve Aşama 7 (uçtan uca) maddeleri ilgili aşamalarda bu dosyaya eklenecek.

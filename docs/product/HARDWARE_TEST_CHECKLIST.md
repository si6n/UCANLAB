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
| W3-18 | Giriş yaptıktan 2 gün sonra (tarayıcı oturumu bitmiş) internete bağlıyken launcher'ı aç. | Tarayıcı açılmaz; konsolda "Lisans internet üzerinden yenilendi." ve çevrimdışı süre yeniden 7 gün. |
| W3-19 | ucanlab.org'da lisansı iptal et (ya da koltuğu başka cihaza taşı), sonra bu bilgisayarda launcher'ı internetteyken aç. | "Lisansınız iptal edilmiş ya da başka bir bilgisayara taşınmış…" mesajı; uygulama açılmaz (bilet silinmiş). |
| W3-20 | 10 gün internetsiz kaldıktan sonra (çevrimdışı süre dolmuş) internete bağlanıp aç. | Tarayıcı açılmadan lisans yenilenir ve uygulama açılır. |

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

## Aşama 6 — Tarama, okuma izni, sonuç (Windows + gerçek araç)

| # | Adım | Beklenen | Durum |
|---|---|---|---|
| W6-1 | OBD-II otomobilde (MIL yanık) "Okumaya izin ver" → işletim sistemi onayı → tarama. | Kayıtlı/bekleyen kodlar sonuçta görünür; kodlar başka bir teşhis cihazıyla karşılaştırılır. | ☐ |
| W6-2 | Aynı araçta bus analizörü ile trafiği kaydet. | Adaptörden yalnız `0x7E0/0x7E1` Mode 03/07/0A tek çerçeveleri ve fiziksel adrese `0x30` akış kontrolü çıkar; **Mode 04 veya başka servis yok**. | ☐ |
| W6-3 | Onay penceresinde "Hayır". | "Okuma onaylanmadı" mesajı; araca hiçbir çerçeve gitmez (analizörle doğrula). | ☐ |
| W6-4 | Tarama bitince Uzman ekranında güvenlik durumu. | PASSIVE; sürücü dinleme modunda; TX kilitli. | ☐ |
| W6-5 | Okuma sırasında uygulama penceresini simge durumuna küçült. | Watchdog süresi dolar, okuma "güvenlik nedeniyle gönderilemedi" ile biter; TX kapanır. | ☐ |
| W6-6 | Araç hareket ederken (J1939 CCVS hız > 0) kamyonda okuma yok; otomobilde hız bilinmiyorsa okuma izni. | Hareket halinde oturum açılmaz (`VEHICLE_MOVING`). | ☐ |
| W6-7 | Kamyonda aktif arıza (DM1) varken dinleme taraması. | Kod(lar) sonuçta, aciliyet ve adımlar görünür; aynı kod bir kez sayılır. | ☐ |
| W6-8 | Birden fazla ECU'su olan otomobil (motor + şanzıman). | İki ünitenin kodları ayrı ayrı "Teknik detay"da görünür. Yanıt vermeyen ünite için eksik veri satırı. | ☐ |
| W6-9 | Müşteri raporu kaydet → tarayıcıda aç → yazdır. | Tek sayfa, Türkçe karakterler doğru, son satır "son karar ustanındır". | ☐ |
| W6-10 | `dtc_clear` yetkili lisansla kod sil (motor kapalı). | Mevcut onay jetonu akışı; sonuç mesajı; yeniden taramada kodlar yok (arıza giderildiyse). | ☐ |

## Aşama 7 — Uçtan uca (Windows + gerçek adaptör + gerçek araç)

| # | Adım | Beklenen | Durum |
|---|---|---|---|
| W7-1 | Temiz Windows'ta kurulum → launcher → tarayıcıda giriş → ilk açılış. | Mod sorusu; Tamirci → araç türü → araç → adaptör → takma → dinleme → Hazır → tarama → sonuç, kesintisiz. | ☐ |
| W7-2 | Aynı akış, pywebview içinde (EdgeChromium). | Testteki HTTP köprüsüyle aynı ekranlar; konsolda JS hatası yok. | ☐ |
| W7-3 | W7-1 boyunca bus analizörü kaydı (kamyon, dinleme taraması). | Adaptörden hiç çerçeve yok (ACK dahil). | ☐ |
| W7-4 | Otomobilde "Okumaya izin ver" ile tam akış + analizör. | Yalnız W6-2'deki okuma çerçeveleri; akış sonunda PASSIVE. | ☐ |
| W7-5 | İnternet kesikken (bilet geçerli) tam akış. | Her adım çalışır; üst çubukta çevrimdışı gün sayısı. | ☐ |
| W7-6 | Ekran okuyucu / 125 % ölçek / 1366×768 ekran. | Metinler taşmaz, düğmeler erişilebilir. | ☐ |

## Uzman masası B1 — Canlı trafik ve simüle hat (Windows + gerçek adaptör)

| # | Adım | Beklenen | Durum |
|---|---|---|---|
| W8-1 | Mühendis modu, adaptör bağlı, motor çalışıyor. | Canlı trafikte yalnız araç çerçeveleri; hat çipinde adaptör + "Araç"; "Simülatör" hiçbir yerde yok. | ☐ |
| W8-2 | Aynı anda bus analizörü kaydı. | Kimlik sayısı ve sıklıklar analizörle uyuşur; adaptörden hiç çerçeve yok. | ☐ |
| W8-3 | "Yük ≈ %" çipi, 500 kbit/s otomobil ve 250 kbit/s kamyonda. | Analizörün ölçtüğü yükün biraz altında (bit doldurma hariç), hiçbir zaman üstünde değil. | ☐ |
| W8-4 | 2000 kare/sn üzeri hat (kamyon + römork) 10 dk. | Pencere akıcı; donma yok; TX watchdog kirası düşmez (dinleme modunda zaten TX yok). | ☐ |
| W8-5 | Simülatörü başlat → kapat (adaptör bağlıyken). | Önceki adaptöre geri döner; araç çerçeveleri tekrar akar. | ☐ |
| W8-6 | E-Stop'a bas (araç bağlı). | "Güvenlik kilidi" bandı; kayıt sürer; simülatöre geçiş reddedilir. | ☐ |
| W8-7 | Hat kısa devre / CAN-H kopuk. | Sürücü hata çerçevesi çipi ve BUS-OFF görünür. | ☐ |
| W8-8 | Kayıt ve rapor → MDF4 dışa aktar, asammdf/CANape ile aç. | Dosya açılır, çerçeve sayısı tutar. | ☐ |
| W8-9 | Grafik: kamyonda motor devri ve soğutma suyu, gaz verip bırakarak. | Değerler gösterge paneliyle uyuşur; "Simülatör" etiketi yok; gecikme < 1 sn. | ☐ |
| W8-10 | Grafik açıkken adaptör ↔ simülatör geçişi. | Grafik boşalır; iki kaynağın eğrisi birleşmez. | ☐ |

## Doğrulama özeti (neyin nerede doğrulandığı)

| Alan | CI / simülatör | Gerçek donanım / Windows |
|---|---|---|
| Giriş, PKCE, cihaz kodu, lisans seçimi | ✅ birim testleri + sahte bulut sunucusu | ☐ W3-* |
| Mod, araç kataloğu, kimlik karşılaştırma | ✅ birim + uçtan uca | ☐ W4-* |
| Adaptör bulma (PCAN/Kvaser/RP1210) | ✅ taklit sürücülerle | ☐ W5-1…W5-4 |
| Dinleme testi, hız tarama, hata yolları | ✅ simülatör | ☐ W5-5…W5-12 |
| Salt-okuma kanalı, OBD okuma | ✅ politika + geçit + uygulama oturumu + sahte ECU | ☐ W6-1…W6-6 |
| Sonuç kartı, müşteri raporu | ✅ gerçek analiz motoru, simülatör | ☐ W6-7…W6-9 |
| Arıza silme (mevcut akış) | ✅ mevcut testler | ☐ W6-10 |
| React ekranları | ✅ Playwright + gerçek köprü (HTTP) | ☐ W7-2 (pywebview) |
| Uzman masası: canlı trafik, simüle hat, dışa aktarma | ✅ birim + Playwright (gerçek köprü + telemetri döngüsü, simülatör) | ☐ W8-* |

**İlk gerçek donanım denemesinde dikkat:** W5-5 / W6-2 / W7-3 (analizörle "hiç çerçeve yok" ve "yalnız okuma çerçevesi" doğrulaması) ilk yapılacak adımlardır. Bunlar geçmeden araçta okuma izni kullanılmamalı.

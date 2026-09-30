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

## Sonraki aşamalar

Aşama 5 (adaptör, soket, dinleme testi) ve Aşama 7 (uçtan uca) maddeleri ilgili aşamalarda bu dosyaya eklenecek.

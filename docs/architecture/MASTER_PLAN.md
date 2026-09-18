# Universal CAN-Bus Diagnostic & Telemetry Platform
# GRAND UNIFIED MASTER ARCHITECTURAL SPECIFICATION
## Nihai Bütünleşik Master Mimari Şartname ve Görev Yol Haritası (`MASTER_PLAN.md`)
*Tarih: 2026-09-17 | Sürüm: 13.1 Grand Unified Industrial Master (Pure Architecture Specification)*
*Bu doküman; SAE International (J1939-21, J1939-71, J1939-73, J1939-81), ISO 11898-1/2:2015-2024, ISO 14229 UDS, TMC RP1210 (A/B/C), NMEA 2000, Microsoft Learn (Win32/DPAPI/CIM), OWASP Top 10, RFC 8032 Ed25519, İyzico V3, PayTR ve TimescaleDB standartlarıyla doğrulanmış, hakem denetiminden geçmiş ve tüm alt şartnameleri tek çatı altında toplayan BÜTÜNLEŞİK NİHAİ MASTER ŞARTNAMEDİR.*

> **Durum Notu (2026-09-17, v13.1):** Normatif gövde değişmedi; yalnızca durum
> bilgileri eklendi. Masaüstü ürün tarafında M1–M7 kilometre taşları tamamlandı
> (OBD/UDS bilgi tabanları, OEM J1939, çok paketli taşıma, E2E güvenlik, bulut
> istemcisi, tam-çevrimdışı AI + P1 veri modeli + Golden-Traces formatı).
> AI katmanı operatör kararıyla **tam çevrimdışıdır** (bulut LLM anlatıcı
> kaldırıldı). T47–T61 güvenlik/teşhis review turu kapatıldı; kayıtlar vault
> (`Review-Dogrulama-T56-T59`) ve git geçmişindedir. FAZ 5 sunucu tarafı ayrı
> depodadır (`Universal-CAN-Cloud`). Uygulama sapmaları §19.1 sonundaki
> "Uygulama Durum Notu"ndadır.

---

# İÇİNDEKİLER
1. **BÖLÜM 1: Sistem Vizyonu, Hedef Persona ve 6 Katmanlı Normatif Mimari**
2. **BÖLÜM 2: Lisanslama Modeli, Hukuki Standartlar (LGPLv3) ve Tehdit Modeli**
3. **BÖLÜM 3: Kanonik Lisans Token Şeması (RFC 8032 Ed25519 SSOT) & Cihaz Güvenliği (DPAPI)**
4. **BÖLÜM 4: Ağır Vasıta & Marin Protokol Standartları (J1939 BAM/CMDT, J1939-81 64-bit NAME, N2K, Volvo MID)**
5. **BÖLÜM 5: CAN-FD (Flexible Data-Rate, ISO 11898-1:2015/2024), 64-Bayt DLC & Donanımsal CRC-17/21**
6. **BÖLÜM 6: Çift Yönlü Aktif Testler, Teşhis Profilleri ve Merkezi TX Gateway (Core Safety Floor)**
7. **BÖLÜM 7: Sinyal Keşif Asistanı ve Kanıt Motoru (Signal Discovery & Evidence Engine)**
8. **BÖLÜM 8: Doğrulanmış Matematiksel Kanallar & Sanal Sensörler (Virtual Channels)**
9. **BÖLÜM 9: Donanım Soyutlama Katmanı (HAL), TMC RP1210 (A/B/C), ReplayBus & CanFrame (dlc dahil)**
10. **BÖLÜM 10: Binary Ring Buffer Kara Kutu (38 MB RAM + Rolling Chunks) & Çoklu Dışa Aktarım (MDF4, MAT, KML)**
11. **BÖLÜM 11: Masaüstü Arayüzü (Technician vs Engineer Mode) & Performans (5.000 msg/s @ 60 FPS)**
12. **BÖLÜM 12: Araç Bilgi Paketleri Mimarisi (Knowledge Pack .pack, manifest.json.sig, JSON Şemaları)**
13. **BÖLÜM 13: Kurumsal Web SaaS, Multi-Tenancy (B2B Atölye Modeli) ve RBAC**
14. **BÖLÜM 14: Ticaret, Ödeme & Webhook Güvenliği (İyzico V3 / PayTR İki Aşamalı Hash)**
15. **BÖLÜM 15: 3 Katmanlı Bulut Telemetri (S3 + PostgreSQL + TimescaleDB Hypertables)**
16. **BÖLÜM 16: OpenAPI v1 REST Uç Noktaları Sözleşmesi ve Resumable Chunk Upload**
17. **BÖLÜM 17: Kapsamlı 10 Katmanlı FMEA Risk Kütüğü ve Önleyici Savunma Kılavuzu**
18. **BÖLÜM 18: Çok Katmanlı Test Piramidi (15 Golden Trace, Fuzzing, Hypothesis Property Tests)**
19. **BÖLÜM 19: Mimari Karar Kayıtları (ADR-001 ~ ADR-012) & Bütünleşik Görev Yol Haritası (Roadmap & DoD)**

---

# BÖLÜM 1: Sistem Vizyonu, Hedef Persona ve 6 Katmanlı Normatif Mimari

**Universal CAN-Bus Diagnostic & Telemetry Platform**, bağımsız marin ve ağır vasıta atölyeleri, saha teknisyenleri ve filo yöneticileri için tasarlanmış **donanım-bağımsız, çoklu-protokol destekli ticari bir teşhis (DTC), aktif servis testi ve canlı telemetri ekosistemidir.**

### 1.1. Hedef Persona ve Pazar Konumlandırması
* **Bağımsız Marin & Ağır Vasıta Servis Şirketleri (B2B Atölye)**: Şirket hesabı altında birden fazla usta/teknisyen çalıştıran, birden çok teşhis bilgisayarına sahip kurumsal servisler.
* **Mobil Saha Teknisyenleri & Bağımsız Ustalar (B2C Pro)**: Tek bilgisayarla sahada arıza tespiti yapan bireysel profesyoneller.
* **Filo Yöneticileri (Fleet Operators)**: Kendi tekne veya kamyon filosunun anlık DTC arızalarını ve telemetri geçmişini buluttan izleyen yöneticiler.
* **Piyasa Boşluğu**: Jaltest'in yıllık yüksek donanım ve lisans maliyetinin pahalı kaldığı, OEM yazılımlarının (Volvo Penta VODIA, Cummins INSITE, Caterpillar ET) tek markaya kilitli olduğu pazarda; teknisyenin elindeki mevcut endüstriyel adaptörlerle (RP1210 / PEAK / Kvaser / Vector) çalışan, modüler, hızlı ve uygun maliyetli çok markalı teşhis platformu ihtiyacını karşılar.

### 1.2. 6 Katmanlı Normatif Mimari Model

```text
┌───────────────────────────────────────────────────────────────────────────┐
│ 1. SUNUM & ARAYÜZ (Technician Mode / Engineer Mode / Reports / Next.js)   │
├───────────────────────────────────────────────────────────────────────────┤
│ 2. ALAN & ANLAMSAL MODEL (Vehicle / ECU / Signal / DTC / Test Result)    │
├───────────────────────────────────────────────────────────────────────────┤
│ 3. TEŞHİS SERVİSLERİ (J1939 DM / UDS / N2K / J1587 / OEM Plugin)         │
├───────────────────────────────────────────────────────────────────────────┤
│ 4. TAŞIMA KATMANI (J1939 TP BAM & CMDT / ISO-TP / N2K Fast Packet)       │
├───────────────────────────────────────────────────────────────────────────┤
│ 5. CAN ÇEKİRDEĞİ & TX GATEWAY (Classic / FD / Future XL / Bus Metrics)    │
├───────────────────────────────────────────────────────────────────────────┤
│ 6. HAL & SÜRÜCÜLER (RP1210 / PEAK / Kvaser / Vector / GS_USB / ReplayBus)│
└───────────────────────────────────────────────────────────────────────────┘
```

---

# BÖLÜM 2: Lisanslama Modeli, Hukuki Standartlar (LGPLv3) ve Tehdit Modeli

### 2.1. PySide6 / LGPLv3 Hukuki Uyumluluk Şartı
* **Dinamik Bağlantı Standartı**: PySide6 (Qt6), LGPLv3 lisansı altında dağıtılmaktadır. Ticari ve kapalı kaynaklı dağıtım koşullarına %100 uymak adına:
  - Qt6 DLL'leri ve PySide6 C-uzantıları (`.dll`, `.pyd`) Nuitka'nın `--standalone` modunda ayrı dinamik kütüphaneler olarak paketlenir; statik olarak tek ikiliye gömülmez.
  - Kullanıcının Qt DLL'lerini yenisiyle değiştirebilme hakkı korunur.
  - Şirkete ait tescilli iş mantığı, teşhis algoritmaları ve lisans kontrol mekanizmaları Nuitka ile C++ makine koduna derlenir.

### 2.2. Gerçekçi DRM Modeli ve Bulut Değer Çapası (Cloud Value Anchor)
* İstemci tarafındaki koruma (HWID + Nuitka + Anti-Debug) **"Gündelik korsanlığı ve yetkisiz lisans dağıtımını caydırıcı profesyonel bir kilit"** olarak konumlandırılır.
* Platformun asıl çalınamaz ve kırılamaz değeri **Sunucu Tarafındaki Bulut Servislerinde** toplanır:
  1. Kriptografik imzalı **Knowledge Pack (Araç Kütüphanesi)** güncellemeleri.
  2. Filo telematik kayıtları ve servis geçmişi bulut hesap tabanlıdır.
  3. Lisanslama Ed25519 asimetrik biletlerle yönetilir.

### 2.3. HWID Operasyonel Esnekliği & EV Kod İmzalama
* **Bileşen Kaynakları (Doğrulandı - Microsoft CIM API)**:
  - `Motherboard UUID`: `Win32_ComputerSystemProduct.UUID` (UUID `0000...` / `FFFF...` ise fallback uygulanır).
  - `CPU Processor ID`: `Win32_Processor.ProcessorId`
  - `System Physical Disk Serial`: Windows OS'un kurulu olduğu `C:` diskinin bağlı bulunduğu `PhysicalDrive0` donanımsal seri numarası (`ASSOCIATORS OF {Win32_LogicalDisk.DeviceID='C:'} WHERE AssocClass=Win32_LogicalDiskToPartition`). Harici USB disklerden etkilenmez.
  - `BIOS Serial Number`: `Win32_BIOS.SerialNumber`
* **Self-Service HWID Sıfırlama Politikası**: Teknisyenlerin sahada bilgisayar bozulması, format veya disk değişimi durumlarında mağdur olmaması için web portalı üzerinden **30 Takvim Gününde 1 Kez (720 Saat)** otomatik cihaz taşıma/sıfırlama hakkı tanınır.
* **EV Code Signing**: Windows Defender, SmartScreen ve kurumsal EDR yazılımlarının düşük seviyeli API çağrılarını sahte virüs (false-positive) olarak engellemesini önlemek için uygulama **EV (Extended Validation) Kod İmzalama Sertifikası** ile imzalanır.

### 2.4. Sistem Saati Hilesi Koruması (Anti-Clock Rollback)
1. **High-Water Mark**: Her başarılı çalışmada geçerli zaman damgası şifrelenerek `%ProgramData%\<app>\` altındaki ACL korumalı yerel yapılandırmaya yazılır. `Mevcut Saat < Son_Kaydedilen_Saat` ise program kilitlenir.
2. **Monotonic Counter**: Windows `GetTickCount64()` donanımsal sistem çalışma sayacı (milisaniye) ile saat artışı çapraz kontrol edilir. Sistem saati geriye alınsa dahi `GetTickCount64` geriye gidemez.
3. **Fırsatçı NTP Doğrulaması**: İnternet bağlantısı yakalandığı anda Google/Cloudflare NTP ile yerel saat doğrulanır (İnternetsiz sahada kilitlenme yapılmaz).

---

# BÖLÜM 3: Kanonik Lisans Token Şeması (RFC 8032 Ed25519 SSOT) & Cihaz Güvenliği (DPAPI)

### 3.1. Kanonik Lisans Token Şeması (SSOT)
Sunucu tarafında **Ed25519 Özel Anahtarı** ile imzalanan ve istemcide gömülü **Ed25519 Genel Anahtarı** ile doğrulanan JWS biletinin kesin veri modeli:

```json
{
  "iss": "universal-can-cloud",
  "aud": "diagnostic-desktop-app",
  "kid": "key-2026-v1",
  "license_id": "lic_987654321",
  "organization_id": "org_123456",
  "device_id": "dev_abcdef",
  "tier": "marine_pro",
  "features": ["j1939", "nmea2000", "active_tests", "mdf4_export"],
  "iat": 1756000000,
  "exp": 1787536000,
  "offline_until": 1756604800,
  "schema_version": 1,
  "nonce": "a8f5c1d2e3f4"
}
```

### 3.2. Masaüstü Cihaz Kimlik Doğrulama Akışı & DPAPI Koruması
1. Masaüstü uygulama ilk açılışta `POST /api/v1/devices/register` ile cihaz parmak izini gönderir.
2. Sunucu cihaza tekil bir `device_token` tahsis eder.
3. Bu token yerel diskte düz metin olarak değil, **Windows DPAPI (`CryptProtectData`)** kullanılarak geçerli Windows kullanıcı hesabıyla şifrelenip saklanır.

---

# BÖLÜM 4: Ağır Vasıta & Marin Protokol Standartları

### 4.1. SAE J1939-21 Taşıma Katmanı: BAM ve CMDT (RTS/CTS)
*Doğrulandı: SAE J1939-21 Transport Protocol Specification*

1. **BAM (Broadcast Announce Message)**:
   - Hedef: Global Yayın (`DA = 255 / 0xFF`).
   - Paketler Arası Süre ($T_r$): **Nominal $50\text{ ms} - 200\text{ ms}$**.
   - $T_1$ Timeout (Receiver Timeout): Maksimum **$750\text{ ms}$**.
   - Kullanım: DM1 Canlı Arıza Yayınları, Genel Motor Telemetrisi.
2. **CMDT (Connection Mode Data Transfer - RTS/CTS)**:
   - Hedef: Noktadan Noktaya Belirli ECU (`DA != 255`).
   - Oturum Anahtarı: `(Source Address, Destination Address, Target PGN)`.
   - $T_2$ Timeout (CTS Bekleme): **$1250\text{ ms}$**.
   - $T_3$ Timeout (İlk Veri Paketi Bekleme): **$1250\text{ ms}$**.
   - $T_4$ Timeout (Hold Time): **$1050\text{ ms}$**.
   - **Hata İptali (`TP.Conn_Abort`)**: `PGN 60416 (0xEC00 / TP.CM)` çerçevesi içinde **Control Byte `0xFF`** ile oturum sonlandırılır. *(Not: PGN 60160 yalnızca `TP.DT` veri paketleri içindir).*
   - Kullanım: DM2 Geçmiş Arıza İstekleri, DM3, DM11 Arıza Silme, PGN 59904 İstek Yanıtları.

### 4.2. SAE J1939-81 Address Claim Protokolü & Tam 64-Bit NAME Yapısı (10 Alt Alan)
CAN hattına veri gönderilmeden önce (Aktif test, DTC silme) yazılım geçerli bir Kaynak Adres (SA) kazanmak zorundadır:

| Bit Konumu | Bit Genişliği | Alan Adı (Field Name) | Açıklama |
| :---: | :---: | :--- | :--- |
| **Bit 63** | 1 bit | `Arbitrary Address Capable (AAC)` | 1 = Alternatif adres alabilir, 0 = Sabit adres |
| **Bit 62..60** | 3 bit | `Industry Group (IG)` | 0=Global, 1=Karayolu, 2=Tarım, 3=İnşaat, 4=Marin |
| **Bit 59..56** | 4 bit | `Vehicle System Instance` | Araç sistemi örneği (0..15) |
| **Bit 55..49** | 7 bit | `Vehicle System` | Araç sistemi türü (Traktör, Römork vb.) |
| **Bit 48** | 1 bit | `Reserved` | Rezerve (SAE standardına göre 0) |
| **Bit 47..40** | 8 bit | `Function` | Cihaz işlevi (Motor, Şanzıman, Teşhis Cihazı) |
| **Bit 39..35** | 5 bit | `Function Instance` | İşlev örneği (0..31) |
| **Bit 34..32** | 3 bit | `ECU Instance` | ECU örneği (0..7) |
| **Bit 31..21** | 11 bit | `Manufacturer Code` | SAE üretici kodu |
| **Bit 20..0** | 21 bit | `Identity Number` | Benzersiz cihaz seri numarası |
| **Toplam** | **64 bit** | **J1939 NAME** | **Küçük 64-bit tamsayı değeri önceliklidir (Kazanır)** |

### 4.3. SAE J1939-71 MSB Tabanlı Kesin Sentinel Değer Tablosu
J1939'da hata ve veri yok göstergeleri **en yüksek anlamlı bayt (MSB) aralığında** kodlanır:

| Sinyal Genişliği | Geçerli Veri Aralığı | Parametreye Özel | Reserved (Rezerve) | ERROR (Sensör Arızası) | NOT AVAILABLE (Veri Yok) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **1 Byte (8-bit)** | `0x00 .. 0xFA` | `0xFB` | `0xFC .. 0xFD` | **`0xFE`** | **`0xFF`** |
| **2 Byte (16-bit)** | `0x0000 .. 0xFAFF` | `0xFB00 .. 0xFBFF` | `0xFC00 .. 0xFDFF` | **`0xFE00 .. 0xFEFF`** | **`0xFF00 .. 0xFFFF`** |
| **4 Byte (32-bit)** | `0x00000000 .. 0xFAFFFFFF` | `0xFB000000 .. 0xFBFFFFFF` | `0xFC000000 .. 0xFDFFFFFF` | **`0xFE000000 .. 0xFEFFFFFF`** | **`0xFF000000 .. 0xFFFFFFFF`** |
| **2-Bit Discrete** | `0b00` (Off) / `0b01` (On) | — | — | **`0b10`** | **`0b11`** |
| **4-Bit Nibble** | `0x0 .. 0xD` | — | — | **`0xE`** | **`0xF`** |

### 4.4. Tam SAE J1939-73 FMI Hata Tablosu (0-31)
* 4-Baytlık DTC Formülü:
  $$\text{SPN} = \text{Data}[0] \mid (\text{Data}[1] \ll 8) \mid ((\text{Data}[2] \ \&\ 0\text{xE}0) \ll 11)$$
  $$\text{FMI} = \text{Data}[2] \ \&\ 0\text{x}1\text{F} \quad | \quad \text{OC} = \text{Data}[3] \ \&\ 0\text{x}7\text{F} \quad | \quad \text{CM} = (\text{Data}[3] \gg 7) \ \&\ 0\text{x}01$$

FMI 0 (Above Normal - Most Severe), FMI 1 (Below Normal - Most Severe), FMI 2 (Erratic/Parazit), FMI 3 (Voltage High), FMI 4 (Voltage Low), FMI 5 (Current Low/Open), FMI 6 (Current High/Grounded), FMI 7 (Mechanical Not Responding), FMI 8 (Abnormal Frequency), FMI 9 (Abnormal Update Rate / Timeout), FMI 10 (Abnormal Rate of Change), FMI 11 (Root Cause Unknown), FMI 12 (Bad Device), FMI 13 (Out of Calibration), FMI 14 (Special Instructions), FMI 15-18 (Moderately/Least Severe High/Low), FMI 19 (Network Data Error), FMI 31 (Condition Exists).

### 4.5. NMEA 2000 Fast Packet ve Volvo Penta EVC Dekoderi
* **Fast Packet**: 1. Çerçeve (`Byte[0] = (Seq << 5) | 0, Byte[1] = Total_Bytes, Byte[2..7] = Data`), Sonraki Çerçeveler (`Byte[0] = (Seq << 5) | Frame_Idx, Byte[1..7] = Data`). Maksimum 223 Bayt / 32 Frame.
* `PGN 127488 (Engine Rapid - RPM/Boost/Tilt)`, `PGN 127489 (Engine Dynamic - Oil/Temp/Volt/Load)`, `PGN 127493 (Transmission)`, `PGN 127497 (Fluid Level)`.
* **Volvo Penta**: EDC1/4/7 (MID 128 PID/SID J1587) ve EVC-A..E (PGN 65280/65535 CAN J1939).

---

# BÖLÜM 5: CAN-FD (Flexible Data-Rate, ISO 11898-1:2015/2024), 64-Bayt DLC & Donanımsal CRC

### 5.1. Çift Bitrate ve Kontrol Bitleri
* Nominal: 250/500 kbps (%75-80 sample point). Veri: 2.0/4.0/5.0 Mbps (%75-80 sample point, TDC aktif).
* Kontrol Bitleri: `FDF=1` (FD Format), `BRS=1` (Bit Rate Switch), `ESI` (Error State Indicator).

### 5.2. 64-Bayt DLC ve Donanımsal CRC Formülü
$$\text{Donanımsal CRC Sınırı} = \begin{cases} \text{CRC-17} & \text{if } \text{payload\_bytes} \le 16\text{ Bayt (DLC } 0..10\text{)} \\ \text{CRC-21} & \text{if } \text{payload\_bytes} \ge 20\text{ Bayt (DLC } 11..15\text{)} \end{cases}$$

| DLC | Klasik CAN Bayt | CAN-FD Bayt | Klasik CRC | CAN-FD CRC |
| :---: | :---: | :---: | :---: | :---: |
| **0 .. 8** | 0 .. 8 Bayt | 0 .. 8 Bayt | **CRC-15** | **CRC-17** |
| **9** | 8 Bayt | **12 Bayt** | — | **CRC-17** |
| **10** | 8 Bayt | **16 Bayt** | — | **CRC-17** |
| **11** | 8 Bayt | **20 Bayt** | — | **CRC-21** |
| **12** | 8 Bayt | **24 Bayt** | — | **CRC-21** |
| **13** | 8 Bayt | **32 Bayt** | — | **CRC-21** |
| **14** | 8 Bayt | **48 Bayt** | — | **CRC-21** |
| **15** | 8 Bayt | **64 Bayt** | — | **CRC-21** |

---

# BÖLÜM 6: Çift Yönlü Aktif Testler, Teşhis Profilleri ve Merkezi TX Gateway

### 6.1. Çekirdek Güvenlik Bariyeri (Core Safety Floor) ve TX Gateway
$$\mathtt{EFFECTIVE\_SAFETY = CORE\_SAFETY\_FLOOR\ \mathbf{AND}\ PACK\_SAFETY\_RULES}$$
*(Dinamik Knowledge Pack kuralları, çekirdeğin koyduğu "Hız == 0, Park ON, Vites Boşta" güvenlik şartlarını asla gevşetemez).*

```text
UI / Diagnostic Services / Plugins / Scripts
                    ↓
               TX Gateway
                    ↓
    ┌───────────────┴───────────────────────────┐
    │ 1. Kullanıcı & Lisans Yetkisi            │
    │ 2. CORE SAFETY FLOOR (Hız == 0, Park ON) │
    │ 3. Pack Safety Rules (RPM, Sıcaklık)      │
    │ 4. Hat Hız Limiti & Pacing                │
    │ 5. Çift Gönderim Koruması                 │
    │ 6. Şifreli Audit Log Kaydı                │
    └───────────────┬───────────────────────────┘
                    ↓
            HAL CAN Sürücüsü (Physical TX)
```

### 6.2. Onay Modalı, TesterPresent & 20-50 ms Best-Effort Abort
* **Zorunlu Onay Modalı**: Teknisyen testi başlatmadan önce yasal sorumluluk ve ön koşul onayını işaretler.
* **TesterPresent Akışı**: Arka planda **1500 ms periyotla (veya Knowledge Pack yapılandırmasına göre)** `0x3E 0x80` gönderilir.
* **Acil İptal (Safety Abort)**: "Space" tuşuna basıldığında veya hat koptuğunda **20-50 ms içinde en iyi gayretle (Best-Effort) acil iptal komutu (`0x31 0x02` / DM7 Abort)** gönderilir ve TX derhal kapatılır.

---

# BÖLÜM 7: Sinyal Keşif Asistanı ve Kanıt Motoru (Signal Discovery)

Bilinmeyen bir CAN hattında kesinlik iddiası yerine çok katmanlı kanıt toplayan akıllı asistan:
* Bit Entropi Analizi (Değişim Sıklığı) + Monotonik Sayaç Kalıpları (+1 mod N).
* Checksum / CRC-8 Hipotez Doğrulama + Zaman Serisi Korelasyonu (Gaz Pedalı vb.).
* Güven Skoru (% Confidence Score) $\to$ Teknisyen Onayı $\to$ DBC / KCD / SYM Dışa Aktarım.

---

# BÖLÜM 8: Doğrulanmış Matematiksel Kanallar & Sanal Sensörler

* **Doğrulanmış Güç Formülü (SPN 513/544)**:
  $$T_{\text{Nm}} = \left( \frac{\text{SPN 513}}{100} \right) \times \text{SPN 544} \quad \mid \quad P_{\text{kW}} = \frac{\text{RPM} \times T_{\text{Nm}}}{9549.3} \quad \mid \quad P_{\text{HP}} = P_{\text{kW}} \times 1.34102$$
* **Tüketim Formülleri**:
  $$\text{Seyir (L/NM)} = \frac{\text{Fuel Rate (L/h)}}{\text{GPS SOG (Knots)}} \quad \mid \quad \text{Karayolu (L/100km)} = \frac{\text{Fuel Rate (L/h)} \times 100}{\text{Vehicle Speed (km/h)}}$$
* **Pervane Kayması**:
  $$\text{Slip (\%)} = \left( 1 - \frac{V_{\text{actual\_knots}} \times 0.514444}{\frac{\text{Engine RPM}}{\text{Gear Ratio}} \times \text{Pitch}_{\text{meters}} \times \frac{1}{60}} \right) \times 100$$

---

# BÖLÜM 9: Donanım HAL, TMC RP1210 (A/B/C), ReplayBus & CanFrame

### 9.1. Endüstriyel Donanım Desteği
* **TMC RP1210 (A/B/C) C-API**: NEXIQ USB-Link 2/3, DG DPA5, Noregon DLA 2.0 doğrudan sürücü desteği.
* **Doğrudan CAN**: PEAK PCAN (USB/FD), Kvaser (Leaf/FD), Vector (VN16xx), candleLight/GS_USB, SLCAN, J2534 PassThru.
* **ReplayBus Engine**: Deterministik adımlama, hızlandırma, hata enjeksiyonu ve Golden Trace oynatıcı.

### 9.2. Çoklu Hat `CanFrame` Veri Sözleşmesi (Kanonik Model)
```python
class CanFrame:
    channel_id: str  # "engine0" | "n2k0" | "obd"
    arbitration_id: int  # 11-bit veya 29-bit CAN ID
    dlc: int  # 0..15 orijinal CAN-FD DLC kodu
    data: bytes  # 0..64 bayt veri yükü
    is_extended: bool  # True = 29-bit CAN ID
    is_fd: bool  # True = CAN-FD Çerçevesi
    brs: bool  # True = Bit Rate Switch Aktif
    esi: bool  # True = Error State Indicator
    direction: str  # "rx" | "tx"
    timestamp_ns: int  # Normalleştirilmiş nanosaniye damgası
    hardware_timestamp_ns: int | None
    host_timestamp_ns: int | None
    sequence: int  # Oturumsal artan sıra numarası
    error_state: str  # "active" | "passive" | "bus_off"
    source: str  # "physical" | "replay" | "virtual" | "injected"
```

---

# BÖLÜM 10: Binary Ring Buffer Kara Kutu & Çoklu Format Dışa Aktarım

* **Kapasite ve Bellek Bütçesi**:
  - $5.000\text{ msg/s} \times 3600\text{ s} = \mathbf{18.000.000\text{ frame/saat}}$, 10 dakika = $\mathbf{3.000.000\text{ frame}}$.
  - **RAM Ring**: 60 saniye ($300.000\text{ frame} \approx \mathbf{38\text{ MB RAM}}$) sabit genişlikli bitişik bellek.
  - **Rolling Disk Chunks**: 10 dakikalık tampon, dönen 5 MB'lık sıkıştırılmış disk bloklarına yazılır.
* **Dışa Aktarım Formatları**: ASAM MDF4 (`.mf4`), MATLAB (`.mat` v7.3), DIAdem (`.tdms`), Google Earth GPS (`.kml`), Vector `.asc` / `.blf`, CSV.

---

# BÖLÜM 11: Masaüstü Arayüzü (Technician vs Engineer Mode) & Performans

* **Technician Mode**: Sade göstergeler, Türkçe/İngilizce DTC listesi, yönlendirmeli aktif testler, e-imzalı PDF servis raporu.
* **Engineer Mode**: 5000 msg/s sanal tablolu (`QTableView`) raw CAN sniffer, 60 FPS osiloskop (`PyQtGraph`), bitfield ısı haritası, Sinyal Keşif Asistanı.
* **Performans Mimarisi**: RX worker $\to$ C/NumPy Ring Buffer $\to$ 30-60 Hz QTimer toplu güncelleme (0 Frame Drop).

---

# BÖLÜM 12: Araç Bilgi Paketleri Mimarisi (Knowledge Pack .pack & JSON Şemaları)

```text
Vehicle Knowledge Pack (.pack)
 ├── manifest.json                  # Paket adı, sürüm, uyumluluk
 ├── manifest.json.sig              # manifest.json dosyasının Ed25519 imzası
 ├── signals.dbc                    # CAN sinyal ve PGN/SPN tanımları
 ├── dtc_definitions.json           # Standart ve OEM arıza kodları, metinler
 ├── diagnostic_procedures.json     # Desteklenen testler (j1939_73, uds, volvo, nmea2000)
 ├── safety_rules.json              # Gerekli ön koşullar (RPM, sıcaklık, vites)
 └── checksums.sha256               # Paket içi tüm dosyaların SHA256 özetleri
```

---

# BÖLÜM 13: Kurumsal Web SaaS, Multi-Tenancy (B2B Atölye Modeli) ve RBAC

* **Multi-Tenancy**: Ana müşteri sınırı `Organization`'dır. Roller: `OWNER`, `ADMIN`, `TECHNICIAN`, `VIEWER`, `BILLING`.
* **Kiracı İzolasyonu**: $\mathtt{WHERE\ id = :resource\_id\ AND\ organization\_id = :authenticated\_org\_id}$.
* **Oturum Güvenliği**: OWASP `HttpOnly + Secure + SameSite=Strict` çerezler + `Argon2id` parola hashlemesi.

---

# BÖLÜM 14: Ticaret, Ödeme & Webhook Güvenliği (İyzico V3 / PayTR İki Aşamalı Hash)

* **Model Ayrımı**: `orders` (Sepet/Sipariş) $\to$ `payment_transactions` (Ödeme Denemesi) $\to$ `payment_events` (Webhook Bildirimi - `provider_event_id UNIQUE`).
* **İyzico 3D Secure**: `X-IYZ-SIGNATURE-V3` başlığı üzerinden `HMAC_SHA256(secret_key, request_body)` doğrulaması.
* **PayTR İki Aşamalı Hash Formülleri**:
  - **Başlatma Token Hash'i (`PAYTR_INIT_TOKEN_HASH`)**:
    $$\text{Token Hash} = \text{Base64}(\text{HMAC\_SHA256}(\text{merchant\_key}, \text{merchant\_id} + \text{user\_ip} + \text{merchant\_oid} + \text{email} + \text{amount} + \text{type} + \text{installment} + \text{currency} + \text{merchant\_salt}))$$
  - **Bildirim Doğrulama Hash'i (`PAYTR_CALLBACK_HASH`)**:
    $$\text{Callback Hash} = \text{Base64}(\text{HMAC\_SHA256}(\text{merchant\_key}, \text{merchant\_oid} + \text{merchant\_salt} + \text{status} + \text{total\_amount}))$$

---

# BÖLÜM 15: 3 Katmanlı Bulut Telemetri (S3 + PostgreSQL + TimescaleDB Hypertables)

1. **PostgreSQL**: Organizasyon, araç (VIN/HIN), teknisyen, seans özeti ve DTC arıza indeksleri.
2. **S3 / MinIO Object Storage**: Zstandard sıkıştırılmış ham MDF4 (`.mf4.zst`) ve e-imzalı PDF servis raporları.
3. **TimescaleDB Zaman Serisi**: PostgreSQL hypertable continuous aggregates ile webde anlık çizdirilen sensör grafikleri.

---

# BÖLÜM 16: OpenAPI v1 REST Uç Noktaları Sözleşmesi ve Resumable Upload

```text
AUTH & ORGANIZATIONS:
  POST   /api/v1/auth/login                      (HttpOnly Cookie ile giriş)
  POST   /api/v1/devices/register                (Cihaz kaydı & device_token alma)
  POST   /api/v1/licenses/activate               (Ed25519 lisans bileti alma)

COMMERCE & PAYMENTS:
  POST   /api/v1/orders/checkout                 (Sipariş & Ödeme başlat)
  POST   /api/v1/webhooks/iyzico                 (İyzico V3 Idempotent Webhook)
  POST   /api/v1/webhooks/paytr                  (PayTR Callback Idempotent Webhook)

TELEMATICS & SESSIONS:
  POST   /api/v1/telematics/sessions             (Seans başlatma & boyut bildirme)
  PUT    /api/v1/telematics/sessions/{id}/chunks/{idx} (Resumable chunk yükleme)
  POST   /api/v1/telematics/sessions/{id}/complete (SHA256 doğrulama & S3 kaydetme)
  GET    /api/v1/oem-packages                    (İmzalı Knowledge Pack manifestleri)
```

---

# BÖLÜM 17: Kapsamlı 10 Katmanlı FMEA Risk Kütüğü ve Önleyici Savunma Kılavuzu

| # | Risk Alanı | En Kritik Hata Modu | Mimari Önleyici Savunma |
| :---: | :--- | :--- | :--- |
| **1** | **Fiziksel Katman** | 60Ω Terminasyon bozulması & Ground Loop | Galvanik İzolasyonlu adaptör şartı + Listen-Only Bitrate tarama |
| **2** | **Donanım HAL** | 16 ms USB gecikmesi & RP1210 çökmesi | FTDI 1ms Latency Timer + 64-bit izole Ctypes wrapper |
| **3** | **OS / Windows** | EDR virüs uyarısı & USB Uyku Modu | EV Kod İmzalama + `SetThreadExecutionState` uyku kilidi |
| **4** | **J1939 Protokol** | Adres almadan hatta yazma | TX Gateway Kilidi (`is_address_claimed == True`) + 10 Alan NAME |
| **5** | **Aktif Testler** | Hareket halinde motor durdurma | Core Safety Floor (Hız=0, Vites=Boşta) + Disclaim Onayı |
| **6** | **Güvenlik / Abort** | İletişim kopmasında ECU kilitlenmesi | 1500 ms TesterPresent + 20-50 ms Best-Effort Abort |
| **7** | **Masaüstü GUI** | 5000 msg/s'de GUI donması | Binary Ring Buffer (38MB RAM) + 30-60 Hz Toplu QTimer |
| **8** | **Lisanslama** | Sistem saatini geriye alma hilesi | `GetTickCount64()` + High-Water Mark şifreleme |
| **9** | **Bulut / SaaS** | Webhook tekrarı & Kiracı veri sızıntısı | `provider_event_id UNIQUE` + Tenant Context Middleware |
| **10**| **Telemetri** | PostgreSQL'in devasa loglarla çökmesi | S3 Object Storage + TimescaleDB Hypertables |

---

# BÖLÜM 18: Çok Katmanlı Test Piramidi (15 Golden Trace, Fuzzing, Hypothesis)

> **Durum Notu (2026-09-17):** Aşağıdaki 15 vektör hedef listedir. Mevcut
> durum: `data/golden_traces/cases/` altında v1 şema + 1 taslak tohum vaka
> (`volvo_penta_d4_300_nonstart.json`, operatör verisi bekliyor);
> `tests/golden_traces/` dizini henüz kurulmadı. Kalibrasyona uygun vaka
> birikince vektörler buradan beslenecek.

`tests/golden_traces/` dizini altındaki 15 adet gerçek araç benchmark vektörü:
1. `j1939_dm1_single.asc`, 2. `j1939_dm1_bam_multiframe.asc`, 3. `j1939_cmdt_rts_cts.asc`, 4. `j1939_address_claim_win.asc`, 5. `j1939_address_claim_loss.asc`, 6. `j1939_dm11_clear_ack.asc`, 7. `n2k_engine_rapid.asc`, 8. `n2k_fast_packet_dynamic.asc`, 9. `n2k_transmission_dynamic.asc`, 10. `n2k_fluid_level.asc`, 11. `volvo_mid128_pid100.asc`, 12. `volvo_evc_prop_a.asc`, 13. `uds_iso15765_flow_control.asc`, 14. `uds_routine_compression.asc`, 15. `canfd_64byte_high_load.asc`.
* **Doğrulama**: L1 (Byte Exact), L2 (Frame Semantic), L3 (Diagnostic Semantic).
* **Property-Based Testing**: `hypothesis` ile SPN/FMI, DLC 0..15 ve Sentinel sınır değerleri.

---

# BÖLÜM 19: Mimari Karar Kayıtları (ADR-001 ~ ADR-012) & Bütünleşik Görev Yol Haritası

### 19.1. Mimari Karar Kayıtları Özeti (ADR)
* `ADR-001`: Python 3.12+ / PySide6 & Nuitka C++ Native Compiler.
* `ADR-002`: RFC 8032 Ed25519 Asimetrik Lisanslama.
* `ADR-003`: 3 Katmanlı Telemetri (S3 + PostgreSQL + TimescaleDB).
* `ADR-004`: Merkezi TX Gateway & Core Safety Floor.
* `ADR-005`: HAL Katmanında TMC RP1210 ve ReplayBus Önceliği.
* `ADR-006`: Araç Bilgi Paketleri (.pack & manifest.json.sig).
* `ADR-007`: OWASP HttpOnly Cookie & Argon2id.
* `ADR-008`: B2B Multi-Tenancy (Organization-Centric Model).
* `ADR-009`: İyzico V3 & PayTR İki Aşamalı Idempotent Webhook.
* `ADR-010`: Nuitka Standalone & LGPLv3 Dinamik Bağlantı.
* `ADR-011`: Zaman Serisi Depolama Katmanı Seçimi (TimescaleDB).
* `ADR-012`: Masaüstü Cihaz Kimlik Doğrulama Akışı & Windows DPAPI.

> **Uygulama Durum Notu (2026-09-17):** ADR'ler karar tarihindeki haliyle
> korunur; aşağıdaki sapmalar bilinçli güncel durumdur:
> - ADR-001 "Python 3.12+ / PySide6": uygulamada `pyproject.toml`
>   `requires-python = ">=3.11"` ve masaüstü arayüz **pywebview + React 18**
>   (`src/ui/desktop_app.py`, `src/ui/frontend/`) kullanılır.
> - Test ve güvenlik izolasyon kilitleri `tests/safety/` altındadır
>   (`test_ai_tx_isolation.py` AI katmanı çevrimdışılığını AST ile kilitler).

---

### 19.2. Bütünleşik ve Güncellenmiş Görev Yol Haritası (Grand Unified Roadmap & DoD)

Status Legend:
- `[ ]` Pending
- `[-]` In Progress
- `[x]` Completed

```
                      BÜTÜNLEŞİK 6 AŞAMALI GELİŞTİRME PLANI
 
 ┌─ [FAZ 0: MİMARİ TEMEL & HAL]──> PlatformError, CanFrame (dlc), ReplayBus, RP1210 Wrapper
 ├─ [FAZ 1: CAN ENGINE & GUI]  ──> Binary Ring Buffer (38MB), 60 FPS Osiloskop, QTableView Sniffer
 ├─ [FAZ 2: J1939 TEŞHİS & TP] ──> J1939-81 (10 Alan), BAM/CMDT (PGN 60416 Abort), DM1/DM2/DM11
 ├─ [FAZ 3: MARİN & TELEMETRİ] ──> N2K FastPacket, Volvo MID, Sanal Kanallar, MDF4, PDF Rapor
 ├─ [FAZ 4: AKTİF TEŞHİS & PACK]─> TX Gateway (Core Safety Floor), UDS 0x31, Knowledge Pack (.pack)
 └─ [FAZ 5: BULUT, SAAS & WEB] ──> Next.js, FastAPI, İyzico/PayTR, TimescaleDB, S3 Telemetri
```

---

#### 🧱 FAZ 0: Mimari Temel, HAL & Donanım Katmanı
- [x] **Task 0.1: Proje Dizin İskeleti, Ortak Hata Hiyerarşisi (`PlatformError`) & Yapılandırılmış Loglama** (`src/core/errors/`, `src/core/logging/`).
  > *Kabul Kriteri (DoD)*: `PlatformError` tabanlı `HardwareError`, `TransportError`, `ProtocolError`, `SafetyError`, `LicenseError`, `SecurityError` sınıfları tanımlandı; `structlog` JSON formatter ile nanosaniye hassasiyetli loglama birim testlerle doğrulandı.
- [x] **Task 0.2: Çoklu Hat `CanFrame` Veri Modeli (`dlc: int` dahil) & Yardımcı Dönüştürücüler** (`src/core/models/can_frame.py`).
  > *Kabul Kriteri (DoD)*: `CanFrame` dataclass'ı 15 alanı eksiksiz içerir (`dlc: int` 0..15 kodu dahil); CAN Classic (0-8B) ve CAN-FD (12, 16, 20, 24, 32, 48, 64B) padding dönüşümleri `hypothesis` testleriyle %100 kapsandı.
- [x] **Task 0.3: TMC RP1210 (A/B/C) C-API Sürücüsü & İzole Ctypes Sarmalayıcısı** (`src/hal/rp1210/`).
  > *Kabul Kriteri (DoD)*: `RP1210_ClientConnect`, `RP1210_ReadMessage`, `RP1210_SendMessage` çağrıları struct padding hizalaması ile sarıldı; NEXIQ ve DPA5 DLL'leri bulunamadığında kontrollü `HardwareError` fırlatır.
- [x] **Task 0.4: ReplayBus Deterministik Oynatıcı & ASC/BLF Okuyucu** (`src/hal/replay/`).
  > *Kabul Kriteri (DoD)*: Vector `.asc` ve `.blf` log dosyalarını nanosaniye zaman damgalarıyla deterministik oynatır; adım adım (step-by-step) ve hızlandırma modları `pytest` ile test edildi.
- [x] **Task 0.5: PEAK PCAN, Kvaser, Vector, GS_USB & J2534 HAL Sürücüleri** (`src/hal/drivers/`).
  > *Kabul Kriteri (DoD)*: `python-can` entegrasyonu tamamlandı; Listen-Only bitrate tarama ve FTDI 1ms latency timer ayarı doğrulandı.

---

#### ⚡ FAZ 1: CAN Mühendislik Motoru & Yüksek Hızlı Masaüstü GUI
- [x] **Task 1.1: 5.000 msg/s Sabit Genişlikli C/NumPy Binary Ring Buffer & Rolling Disk Chunks** (`src/engine/buffer/`).
  > *Kabul Kriteri (DoD)*: 60 saniyelik RAM Ring ($300.000\text{ frame} \approx 38\text{ MB RAM}$) kesintisiz döner; 10 dakikalık geçmiş 5 MB'lık sıkıştırılmış disk bloklarına taşma olmadan yazılır.
- [x] **Task 1.2: PySide6 `QTableView` + `QAbstractTableModel` Sanal Kaydırmalı CAN Sniffer** (`src/ui/engineer/sniffer/`).
  > *Kabul Kriteri (DoD)*: 5.000 msg/s yük altında 60 FPS render sağlar; Python GIL kilitlenmesi yaşanmaz; frame drop oranı %0'dır.
- [x] **Task 1.3: 60 FPS Osiloskop Grafik Motoru (`PyQtGraph`) & Bitfield Isı Haritası** (`src/ui/engineer/scope/`).
  > *Kabul Kriteri (DoD)*: Seçilen sinyaller donma olmadan canlı çizdirilir; baytların değişim sıklığı renk skalasında gösterilir.
- [x] **Task 1.4: DBC Ayrıştırma & Canlı Sinyal Kod Çözücü Motoru** (`src/engine/decoder/`).
  > *Kabul Kriteri (DoD)*: `cantools` entegrasyonu ile gelen CAN çerçeveleri anlık olarak fiziksel mühendislik birimlerine dönüştürülür.

---

#### 🚛 FAZ 2: SAE J1939 Ağır Vasıta Teşhis & Taşıma Katmanı
- [x] **Task 2.1: SAE J1939-81 Address Claim State Machine & 10 Alt Alanlı 64-Bit NAME** (`src/protocols/j1939/address_claim.py`).
  > *Kabul Kriteri (DoD)*: 64-bit NAME önceliğine göre adres kazanma, çakışma durumunda alternatif adres talep etme ve Null Address (`0xFE`) geçişi birim testlerle doğrulandı.
- [x] **Task 2.2: SAE J1939-21 BAM & CMDT (RTS/CTS) Taşıma Katmanı Motoru** (`src/protocols/j1939/transport.py`).
  > *Kabul Kriteri (DoD)*: $T_1=750\text{ms}, T_2=1250\text{ms}, T_3=1250\text{ms}, T_4=1050\text{ms}$ zamanlama kurallarına uyulur; hata durumunda `TP.Conn_Abort (PGN 60416, Control Byte 0xFF)` yayınlanır; oturum bellek sızıntısı olmadan temizlenir.
- [x] **Task 2.3: SAE J1939-73 Teşhis Servisleri: DM1, DM2, DM3, DM11 & Tam FMI 0-31 Tablosu** (`src/protocols/j1939/diagnostics.py`).
  > *Kabul Kriteri (DoD)*: Aktif ve geçmiş arıza kodları SPN, FMI, OC olarak ayrıştırılır; Türkçe/İngilizce açıklamalar ve olası kök nedenler ekrana basılır.
- [x] **Task 2.4: SAE J1939-71 MSB Sentinel Filtresi & Sınır Değer Doğrulayıcı** (`src/protocols/j1939/sentinel.py`).
  > *Kabul Kriteri (DoD)*: `0xFE` (ERROR) ve `0xFF` (NOT AVAILABLE) durumları doğru sınıflandırılır; geçersiz veriler sensör grafiğine sokulmaz.

---

#### ⚓ FAZ 3: Marin Protokolleri, Sanal Sensörler & Telemetri Raporlama
- [x] **Task 3.1: NMEA 2000 Fast Packet Dekoderi & Standart PGN Kütüphanesi** (`src/protocols/nmea2000/`).
  > *Kabul Kriteri (DoD)*: `PGN 127488`, `127489`, `127493`, `127497` Fast Packet birleştirme mantığı ile %100 doğrulandı.
- [x] **Task 3.2: Volvo Penta EDC (MID 128 PID/SID) & EVC Dekoderi** (`src/protocols/volvo/`).
  > *Kabul Kriteri (DoD)*: J1587 ve CAN üzerindeki özel Volvo Penta parametreleri ve arıza kodları ayrıştırılır.
- [x] **Task 3.3: Doğrulanmış Matematiksel Sanal Kanallar Motoru** (`src/engine/virtual_channels/`).
  > *Kabul Kriteri (DoD)*: SPN 513/544 Güç ($\text{kW/HP}$), Seyir Verimliliği ($\text{L/NM}$), Karayolu Tüketimi ($\text{L/100km}$) ve Marin Pervane Kayması ($\text{Slip \%}$) doğru formüllerle hesaplanır.
- [x] **Task 3.4: ASAM MDF4 (`.mf4`), MATLAB (`.mat`), KML & Kurumsal PDF Servis Raporu** (`src/engine/exporters/`).
  > *Kabul Kriteri (DoD)*: Seans kayıtları standart ASAM MDF4 ve e-imzalı kurumsal HTML/PDF formatında hatasız dışa aktarılır.

---

#### 🛡️ FAZ 4: Çift Yönlü Aktif Testler, TX Gateway & Knowledge Pack
- [x] **Task 4.1: Merkezi TX Gateway & Çekirdek Güvenlik Bariyeri (`CORE_SAFETY_FLOOR`)** (`src/safety/gateway.py`, `src/safety/estop.py`).
  > *Kabul Kriteri (DoD)*: `VehicleSpeed == 0`, `Dual Confirmation Token`, `Whitelist Filter` ve `Rate Limiting (100 msg/s)` şartları sağlanmadan hiçbir CAN TX paketine izin verilmez; 10-tetikleyicili E-Stop ile tam koruma mühürlendi.
- [x] **Task 4.2: UDS (ISO 14229 / ISO 15765-2) & J1939 DM7/DM8 Aktif Servis Testleri** (`src/protocols/uds/`).
  > *Kabul Kriteri (DoD)*: Silindir Cut-out, Kompresyon Testi, DPF Rejenerasyonu komut dizileri (0x10, 0x22, 0x2E, 0x31, 0x3E, 0x7F NRC) ve DoCAN ISO-TP multi-frame (SF, FF, CF, FC) motoru %100 doğrulandı.
- [x] **Task 4.3: Araç Bilgi Paketleri (Knowledge Pack `.pack`) Yükleyici & `manifest.json.sig` Doğrulayıcı** (`src/security/knowledge_pack/`).
  > *Kabul Kriteri (DoD)*: Ed25519 ile imzalanmış `.pack` arşivleri diske yazılmadan doğrudan RAM'de AES-256-GCM ile çözülür; `secure_zero_memory` ile bellek sıfırlanır.
- [x] **Task 4.4: Ed25519 Lisans Biletleme, 7-Gün Çevrimdışı Grace Period & Anti-Debug** (`src/security/license/`, `src/security/anti_tamper/`, `src/hal/power/`).
  > *Kabul Kriteri (DoD)*: Ed25519 lisans doğrulaması, HWID kilidi, saat manipülasyonu engelleme, SetThreadExecutionState USB uyku koruması ve Win32 Anti-Debug modülü tamamlandı.

---

#### ☁️ FAZ 5: Kurumsal Web Platformu, Multi-Tenancy, Ödeme & Bulut Telemetri
> **Durum Notu (2026-09-17):** Masaüstü istemci tarafı tamamlandı (M6:
> cihaz kaydı, Ed25519 lisans aktivasyonu, 5 MB parçalı MDF4 yükleme;
> `src/cloud/`). Aşağıdaki Task 5.x maddeleri sunucu tarafı
> (`Universal-CAN-Cloud` deposu) kapsamıdır.
- [ ] **Task 5.1: FastAPI REST API İskeleti, TimescaleDB & PostgreSQL Şeması** (`backend/app/`).
  > *Kabul Kriteri (DoD)*: Multi-Tenant `Organization` modeli, TimescaleDB hypertables ve S3/MinIO istemcisi çalışır.
- [ ] **Task 5.2: İyzico 3D Secure (V3) & PayTR İki Aşamalı Idempotent Webhook Entegrasyonu** (`backend/app/routers/payments.py`).
  > *Kabul Kriteri (DoD)*: `PAYTR_INIT_TOKEN_HASH` ve `PAYTR_CALLBACK_HASH` ile `provider_event_id UNIQUE` idempotency kuralları doğrulanır; başarılı ödemede otomatik Ed25519 lisansı üretilir.
- [ ] **Task 5.3: Masaüstü Cihaz Kaydı (`device_token`), Windows DPAPI & Ed25519 Lisans Dağıtımı** (`backend/app/routers/licenses.py`).
  > *Kabul Kriteri (DoD)*: 30 takvim gününde 1 kez HWID sıfırlama hakkı tanınır; kanonik lisans token şeması döner.
- [ ] **Task 5.4: Parçalı & Resumable Telemetri Yükleme (`Content-Range` + S3 + TimescaleDB)** (`backend/app/routers/telematics.py`).
  > *Kabul Kriteri (DoD)*: MDF4 dosyaları parça parça yüklenir; S3'e arşivlenir; sensör verileri TimescaleDB continuous aggregates ile webde Recharts üzerinden çizdirilir.
- [ ] **Task 5.5: Next.js Müşteri & Teknisyen Dashboard'u, Fiyatlandırma Vitrini ve SuperAdmin Paneli** (`web/`).
  > *Kabul Kriteri (DoD)*: Dark marine tasarım, lisans/cihaz yönetimi, filo telematik haritası ve audit log izleme ekranları tamamlanır.
- [ ] **Task 5.6: Nuitka C++ Standalone Derleme Pipeline'ı & EV Kod İmzalama** (`build/`).
  > *Kabul Kriteri (DoD)*: LGPLv3 dinamik bağlantı kurallarına uygun, tek tıklamayla çalışan `Universal_CAN_Setup.exe` üretilir.


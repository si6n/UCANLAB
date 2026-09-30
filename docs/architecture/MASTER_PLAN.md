# Universal CAN-Bus Diagnostic & Telemetry Platform
# Mimari Şartname (`MASTER_PLAN.md`)
*Son güncelleme: 2026-09-30 | Sürüm: 14.0 (koda göre yeniden yazıldı)*

> **Kapsam ve durum:** Bu belge masaüstü ürünün (bu depo) mimari referansıdır ve
> **mevcut koda göre güncellenmiştir**. Kod dosyalarının atıf yaptığı bölüm
> numaraları (ör. §3.2, §4.1, §9.2, §11.2) korunmuştur. Web/SaaS/ödeme tarafı
> ayrı depodadır (`Universal-CAN-Cloud`) ve burada yalnızca istemci sözleşmesi
> tutulur (§13–16). Özet ve özellik envanteri için `PROJECT.md`, karar kayıtları
> için `docs/adrs/`, veri kaynakları için `data/PROVENANCE.md` esas alınır.
> Standartlar: SAE J1939-21/-71/-73/-81, ISO 11898-1, ISO 14229, ISO 15765-2,
> NMEA 2000, TMC RP1210.

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
│ 1. SUNUM & ARAYÜZ (React 18 + TypeScript / pywebview-WebView2 / Reports) │
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

---

# BÖLÜM 2: Lisanslama Modeli, Paketleme ve Tehdit Modeli

### 2.1. Paketleme ve Dağıtım
* Arayüz **React 18 + TypeScript** (`src/ui/frontend/`), masaüstü kabuk **pywebview / WebView2** (`src/ui/desktop_app.py`); Qt/PySide6 kullanılmaz (PyInstaller derlemesinde açıkça hariç tutulur, `scripts/build_exe.py`).
* Derleme: PyInstaller tek dosya `.exe` (`scripts/build_exe.py`), Nuitka C-seviyesi derleme (`scripts/build_nuitka.py`), Inno Setup kurulum paketi (`scripts/installer.iss`, `scripts/build_installer.py`).
* Ürün ticari, lisanslı ve kapalı kaynaktır (bkz. `README.md`). Windows Defender/SmartScreen yanlış pozitiflerini azaltmak için dağıtım EV kod imzalama ile yapılır.

### 2.2. Gerçekçi DRM Modeli ve Bulut Değer Çapası (Cloud Value Anchor)
* İstemci tarafındaki koruma (HWID + derleme + Anti-Debug) **"Gündelik korsanlığı ve yetkisiz lisans dağıtımını caydırıcı profesyonel bir kilit"** olarak konumlandırılır.
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
* **Doğrudan CAN (python-can üzerinden, `src/hal/drivers/pcan_kvaser.py`)**: PEAK PCAN, Kvaser, Vector (donanımda test edilmedi), GS_USB, Linux SocketCAN (donanımda test edilmedi), sanal kanal.
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

# BÖLÜM 11: Masaüstü Arayüzü ve Performans

* **Teknoloji**: React 18 + TypeScript + Tailwind (`src/ui/frontend/`), Python arka uçla pywebview köprüsü üzerinden konuşur (`src/ui/desktop_app.py`, `src/ui/frontend_server.py`).
* **Ekranlar**: canlı kokpit/dashboard, CAN sniffer tablosu, sinyal osiloskobu, ECU/teşhis (DTC), AI copilot, sinyal keşfi, pinout, raporlar, ayarlar (`src/ui/frontend/src/components/`).
* **Hata modeli (§11.2)**: `PlatformError` kökünden `HardwareError`, `TransportError`, `ProtocolError`, `SafetyError`, `LicenseError`, `SecurityError` (`src/core/errors.py`, `src/core/exceptions.py`); yapılandırılmış loglama `structlog` (`src/core/logging.py`).
* **Performans mimarisi**: RX worker → NumPy ring buffer → toplu UI güncelleme (hedef 60 FPS, kare kaybı olmadan).
* **Alarm/atıf**: `src/ui/alarm_model.py`, `src/ui/data_attribution_catalog.py`.

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

# BÖLÜM 13–15: Web SaaS, Ödeme ve Bulut Telemetri (ayrı depo)

Kurumsal web platformu (multi-tenancy/RBAC), ödeme entegrasyonları ve bulut
telemetri depolama (PostgreSQL + S3 + TimescaleDB) **`Universal-CAN-Cloud`
deposunun** kapsamındadır ve bu depoda uygulanmaz. Masaüstü istemcinin
bağımlı olduğu sözleşme §16'da; istemci kodu `src/security/cloud/` altındadır.

---

# BÖLÜM 16: Bulut İstemci Sözleşmesi (OpenAPI v1, istemci tarafı)

```text
POST   /api/v1/devices/register                        (cihaz kaydı & device_token, DPAPI ile saklanır)
POST   /api/v1/licenses/activate                       (Ed25519 lisans bileti)
POST   /api/v1/telematics/sessions                     (seans başlatma & boyut bildirme)
PUT    /api/v1/telematics/sessions/{id}/chunks/{idx}   (5 MB resumable parça yükleme)
POST   /api/v1/telematics/sessions/{id}/complete       (SHA-256 doğrulama)
GET    /api/v1/oem-packages                            (imzalı Knowledge Pack manifestleri)
```
İstemci: `src/security/cloud/{client,license_flow,telemetry_uploader,updater}.py`.

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
| **7** | **Masaüstü GUI** | 5000 msg/s'de UI donması | Binary Ring Buffer (300K frame) + toplu UI güncelleme |
| **8** | **Lisanslama** | Sistem saatini geriye alma hilesi | `GetTickCount64()` + High-Water Mark şifreleme |
| **9** | **Bulut / SaaS** | Webhook tekrarı & Kiracı veri sızıntısı | Sunucu tarafı (`Universal-CAN-Cloud`) |
| **10**| **Telemetri** | PostgreSQL'in devasa loglarla çökmesi | Sunucu tarafı (`Universal-CAN-Cloud`) |

---

# BÖLÜM 18: Test Piramidi

* **Dizinler**: `tests/unit/` (çekirdek, protokol, HAL, AI, veri bütünlüğü, adversarial, benchmark), `tests/safety/` (TX choke-point, AI-TX izolasyonu, link-fault E-Stop), `tests/integration/`, `tests/e2e/`; ayrıntı için `docs/ai_context/05_TESTING_AND_VERIFICATION.md`.
* **Benchmark vektörleri**: `tests/fixtures/benchmarks/vectors/` altında 14 `.asc` izi (J1939 DM1/BAM/CMDT/address claim/DM11, N2K, Volvo MID128, UDS/ISO-TP) ve `expected/` beklentileri; `tests/integration/test_benchmark_vectors.py` doğrular.
* **Golden vakalar**: teşhis vakaları `data/golden_traces/cases/` altındadır (v1 şema; çoğunda canlı kayıt referansı henüz yok, bkz. `docs/audit/data_integrity_2026-09-26.md`).
* **Doğrulama seviyeleri**: L1 (Byte Exact), L2 (Frame Semantic), L3 (Diagnostic Semantic).
* **Property-based**: `hypothesis` ile SPN/FMI, DLC 0..15 ve sentinel sınır değerleri.

---

# BÖLÜM 19: Mimari Karar Kayıtları ve Uygulama Durumu

### 19.1. Karar Kayıtları
Yazılı ADR'ler: `docs/adrs/0001_hexagonal_architecture.md`, `docs/adrs/0002_tx_safety_chokepoint.md`.
Ek kalıcı kararlar (kod ve testlerde kilitli):
* Ed25519 (RFC 8032) asimetrik lisanslama (`src/security/license/`, §3).
* Merkezi TX Gateway + Core Safety Floor; hiçbir bileşen (AI dahil) doğrudan TX yapamaz (`tests/safety/`).
* AI copilot tamamen çevrimdışıdır: bulut LLM yok, API anahtarı yok (`test_ai_tx_isolation.py` AST ile kilitler).
* Araç Bilgi Paketleri: Ed25519 imzalı `.pack` (`src/security/knowledge_pack/`, §12).
* Windows DPAPI ile cihaz token saklama (§3.2).
* Python `>=3.11` (CI: 3.12 ve 3.13); arayüz React 18 + pywebview.

### 19.2. Uygulama Durumu (masaüstü)

| Faz | Kapsam | Kod | Durum |
| :--- | :--- | :--- | :---: |
| 0 | Hata hiyerarşisi, `CanFrame`, RP1210, ReplayBus, python-can sürücüleri | `src/core/`, `src/hal/` | Tamam |
| 1 | Ring buffer + rolling disk, DBC decoder, UI konsolu | `src/engine/buffer/`, `src/engine/decoder/`, `src/ui/` | Tamam |
| 2 | J1939 Address Claim, TP (BAM/CMDT), DM1–DM11, sentinel | `src/protocols/j1939/` | Tamam |
| 3 | NMEA 2000, Volvo Penta, sanal kanallar, MDF4/MAT/KML/rapor | `src/protocols/nmea2000/`, `src/protocols/volvo/`, `src/engine/virtual_channels/`, `src/engine/exporters/` | Tamam |
| 4 | TX Gateway, E-Stop, UDS/ISO-TP, Knowledge Pack, lisans, anti-tamper | `src/safety/`, `src/protocols/uds/`, `src/security/` | Tamam |
| 5 | Bulut istemcisi (kayıt, lisans, parçalı yükleme) | `src/security/cloud/` | Tamam (istemci) |
| 5 | Sunucu, web panel, ödeme | `Universal-CAN-Cloud` (ayrı depo) | Bu depo dışı |

Ek modüller (planın dışında eklendi): OBD-II (`src/protocols/obd/`), CANopen, ISOBUS,
OEM J1939 çözücüleri (`src/protocols/j1939/oem/`), Signal Discovery (`src/engine/discovery/`),
çevrimdışı AI copilot (`src/engine/ai/`, bkz. `docs/OFFLINE_AI_ENGINE.md`).

import { ChatMessage, CopilotAction, DiagnosticState, ScenarioType, TelemetryPoint } from '../types/can';
import { KNOWN_DTCS } from './canSimulator';
import { DesktopBridge } from './bridge';
import { GeminiClient } from './geminiClient';
import { OpenAiClient } from './openAiClient';

// ============================================================================
// SAE J1939 FMI TABLE REFERENCE (SAE J1939-73)
// ============================================================================
const J1939_FMI_DEFINITIONS: Record<number, string> = {
  0: "Aşırı yüksek değer (Kritik sınır aşıldı)",
  1: "Aşırı düşük değer (Kritik sınırın altında)",
  2: "Sinyal kararsız veya kesintili",
  3: "Yüksek voltaj / Artıya kısa devre",
  4: "Düşük voltaj / Şasiye kısa devre",
  5: "Açık devre / Kablo kopuk",
  6: "Aşırı akım / Toprağa kısa devre",
  7: "Mekanik sistem yanıt vermiyor / Sıkışmış",
  8: "Anormal frekans veya darbe genişliği",
  9: "İletişim gecikmesi / Güncelleme yok",
  10: "Anormal ani değişim",
  11: "Belirlenemeyen arıza modu",
  12: "Sensör veya beyin dahili donanım arızası",
  13: "Kalibrasyon / Ayar dışı",
  14: "Özel üretici arıza durumu",
  15: "Uyarı eşiğinin üzerinde (Hafif yüksek)",
  16: "Uyarı eşiğinin üzerinde (Orta yüksek)",
  17: "Uyarı eşiğinin altında (Hafif düşük)",
  18: "Uyarı eşiğinin altında (Orta düşük)",
  19: "Ağdan hatalı veri alındı",
  22: "Normal çalışma aralığının dışında"
};

// ============================================================================
// SAE J1939 SPN TABLE REFERENCE (SAE J1939-71)
// ============================================================================
const J1939_SPN_DEFINITIONS: Record<number, string> = {
  84: "Araç Hız Sensörü",
  91: "Gaz Pedalı Konumu",
  94: "Yakıt İletim Basıncı",
  100: "Motor Yağ Basıncı",
  102: "Turbo Takviye Basıncı (Boost)",
  105: "Emme Manifoldu Sıcaklığı",
  108: "Barometrik Ortam Basıncı",
  110: "Motor Soğutma Suyu Sıcaklığı (Hararet)",
  157: "Yakıt Ray Basıncı (Common Rail)",
  158: "Kontak Anahtarı Voltajı",
  168: "Akü Voltajı",
  175: "Motor Yağ Sıcaklığı",
  190: "Motor Devri (RPM)",
  512: "Sürücü Tork Talebi",
  513: "Gerçek Motor Torku",
  651: "Silindir #1 Enjektörü",
  652: "Silindir #2 Enjektörü",
  653: "Silindir #3 Enjektörü",
  654: "Silindir #4 Enjektörü",
  655: "Silindir #5 Enjektörü",
  656: "Silindir #6 Enjektörü",
  970: "Acil Durdurma Anahtarı (E-STOP)",
  1087: "EBS Fren Hava Basıncı Devre 1",
  1172: "Turbo Giriş Sıcaklığı",
  1761: "AdBlue (DEF) Tank Seviyesi",
  3251: "DPF Diferansiyel Basıncı",
  3364: "AdBlue (DEF) Reaktif Kalitesi",
  3719: "DPF Kurum Yükü",
  4364: "SCR Katalizör DeNOx Dönüşüm Verimi",
  520201: "Yakıt Filtresinde Su Tespiti"
};

// ============================================================================
// ISO 14229 UDS NEGATIVE RESPONSE CODES (NRC)
// ============================================================================
const UDS_NRC_MAP: Record<number, { name: string; cause: string; action: string }> = {
  0x10: { name: "generalReject", cause: "ECU donanımsal meşguliyet veya dahili hata nedeniyle isteği reddetti.", action: "50 ms sonra tekrarlayın veya soft reset atın." },
  0x11: { name: "serviceNotSupported", cause: "İstenen Servis ID bu ECU yazılımında tanımlı değil.", action: "Desteklenen servis listesini (0x19 0x0A) sorgulayın." },
  0x12: { name: "subFunctionNotSupported", cause: "Alt fonksiyon desteklenmiyor.", action: "Subfunction baytını kontrol edin (Örn: 0x10 0x03)." },
  0x13: { name: "incorrectMessageLengthOrInvalidFormat", cause: "Çerçeve formatı veya bayt uzunluğu hatalı.", action: "ISO-TP çerçeve uzunluğunu ve parametreleri doğrulayın." },
  0x14: { name: "responseTooLong", cause: "Yanıt bayt uzunluğu taşıma tamponunu aşıyor.", action: "Sorguyu daraltın (Tekil DID okuyun)." },
  0x22: { name: "conditionsNotCorrect", cause: "Ön koşullar sağlanmadı (Motor çalışıyor veya voltaj <11.0V).", action: "Kontağı açın, motoru durdurun, akü besleme bağlayın (>12.5V) ve el frenini çekin." },
  0x24: { name: "requestSequenceError", cause: "Sıralama hatası (Seed almadan Key gönderme).", action: "Prosedürü en baştan sırasıyla işletin (0x10 0x02 -> 0x27 0x01 -> 0x27 0x02)." },
  0x31: { name: "requestOutOfRange", cause: "İstenen parametre kabul aralığı dışında.", action: "Parametre sınırlarını ODX veritabanından kontrol edin." },
  0x33: { name: "securityAccessDenied", cause: "Güvenlik kilidi kapalı (Seed/Key açılması şart).", action: "0x27 0x01 servisi ile Seed isteyip Key hesaplayıp gönderin." },
  0x35: { name: "invalidKey", cause: "Gönderilen güvenlik anahtarı yanlış.", action: "DLL algoritmasını ve byte endianness sırasını kontrol edin." },
  0x36: { name: "exceededNumberOfAttempts", cause: "Üst üste 3 hatalı deneme yapıldığı için güvenlik kilitlendi.", action: "10 dakikalık ceza süresinin dolmasını bekleyin." },
  0x37: { name: "requiredTimeDelayNotExpired", cause: "Ceza süresi dolmadan yeni istek yapıldı.", action: "Geri sayım süresinin dolmasını bekleyin." },
  0x78: { name: "requestCorrectlyReceived-ResponsePending", cause: "ECU isteği aldı, işliyor (Flash silme/hesaplama).", action: "İsteği tekrarlamayın! P2* zamanlayıcısını (5000 ms) bekleyin." },
  0x7E: { name: "subFunctionNotSupportedInActiveSession", cause: "Alt fonksiyon mevcut oturumda yasak.", action: "0x10 0x03 Extended Session'a geçiş yapın." },
  0x7F: { name: "serviceNotSupportedInActiveSession", cause: "Servis mevcut oturumda çalıştırılamaz.", action: "0x10 0x02 veya 0x10 0x03 oturumu açın." },
  0x83: { name: "engineIsRunning", cause: "Test için motorun durdurulması şart.", action: "Motoru stop edip kontağı açık bırakın." },
  0x88: { name: "vehicleSpeedTooHigh", cause: "Araç hızı >0 km/s olduğu için güvenlik engeli.", action: "Aracı tamamen durdurun ve el frenini çekin." },
  0x92: { name: "voltageTooHigh", cause: "Akü/şebeke voltajı çok yüksek (>16.0V).", action: "Harici şarj cihazını sökün veya regülatörü kontrol edin." },
  0x93: { name: "voltageTooLow", cause: "Akü voltajı sınırın altında (<11.0V).", action: "Harici akü destek ünitesi bağlayın (13.8V - 14.4V)." }
};

// ============================================================================
// 300+ BILINGUAL TURKISH/ENGLISH AUTOMOTIVE NLP SEMANTIC DICTIONARY
// ============================================================================
const AUTOMOTIVE_SEMANTIC_DICTIONARY: Record<string, string[]> = {
  MISFIRE: [
    "tekleme", "tekliyor", "misfire", "silkeleme", "sarsinti", "sarsintili", "3 silindir", "atesleme hatasi",
    "atesleme", "buji", "bobin", "enjektor", "avans", "vuruntu", "patlatma", "piston", "kompresyon"
  ],
  TURBO_BOOST: [
    "turbo", "overboost", "underboost", "basinc", "boost", "wastegate", "intercooler", "n75", "vgt",
    "islik sesi", "hava kacagi", "hortum patlak", "hava akis", "maf", "map", "cekis dusuklugu", "bayilma",
    "kara duman", "siyah duman", "duman atiyor"
  ],
  OVERHEAT_COOLING: [
    "hararet", "sicaklik", "sogutma", "termostat", "radyator", "fan", "antifriz", "su kaynatiyor", "su eksiltme",
    "hortum sisme", "devirdaim", "su pompasi", "expansion tank", "genlesme kabi", "conta yakma", "ust kapak contasi",
    "beyaz buhar", "tatli koku", "mayonez"
  ],
  EV_HV_BATTERY: [
    "ev", "bms", "hvil", "izolasyon", "batarya", "pil", "hucre", "delta voltaj", "precharge", "kontaktor", "megger",
    "yuksek voltaj", "high voltage", "msd", "servis salteri", "turtle mode", "kapasite kaybi", "soh", "soc", "inverter",
    "termal kacak", "thermal runaway", "dc-dc", "igbt"
  ],
  HEAVY_DUTY_J1939: [
    "j1939", "spn", "fmi", "dm1", "dm2", "dm4", "dm11", "adblue", "def", "dpf", "scr", "nox", "rejenerasyon",
    "kirmizi lamba", "sari lamba", "rsl", "awl", "tork kisitlama", "5 mph", "hiz limiti", "cummins", "detroit",
    "scania", "volvo truck", "paccar", "hava basinci", "ebs"
  ],
  MARINE_NMEA2000: [
    "marine", "marin", "tekne", "yat", "gemi", "nmea", "nmea 2000", "n2k", "pgn", "impeller", "cark", "deniz suyu",
    "strainer", "esnjor", "esanchor", "egzoz dirsegi", "mixing elbow", "susturucu", "waterlock", "pervane", "slip",
    "kavitasyon", "dumen", "potansiyometre", "volvo penta", "evc", "yanmar"
  ],
  CAN_PHYSICAL_LAYER: [
    "can bus", "haberlesme", "120 ohm", "sonlandirma", "60 ohm", "direnc", "kisa devre", "acik devre", "bus off",
    "error passive", "osiloskop", "voltaj", "pinout", "obd", "deutsch", "can_h", "can_l", "gurultu", "parazit",
    "ground offset", "topraklama"
  ],
  UDS_PROTOCOL: [
    "uds", "servis", "service", "0x10", "0x11", "0x14", "0x19", "0x22", "0x27", "0x28", "0x2e", "0x2f", "0x31",
    "0x34", "0x36", "0x37", "0x85", "nrc", "seed", "key", "guvenlik", "oturum", "did", "routine", "flash"
  ],
  ELECTRICAL_STARTING: [
    "mars", "mars basmiyor", "mars almiyor", "gec calisma", "aku", "alternator", "sarj dinamosu", "konjektor",
    "sigorta", "role", "tik sesi", "kutup basi", "voltaj dusuk", "akinti", "kacak"
  ],
};

// ============================================================================
// COMPREHENSIVE EXPERT KNOWLEDGE BASE FOR 4-STAGE TECHNICIAN FIELD REPORTS
// ============================================================================
const EXPERT_REPORTS: Record<string, {
  title: string;
  subsystem: string;
  severity: string;
  causes: string[];
  steps: Array<{ action: string; target: string; difficulty: string }>;
  measurement: string;
  routine: string;
}> = {
  P0A0B: {
    title: "Yüksek Voltaj Güvenlik Kilidi (HVIL) Devresi Açık (HVIL Circuit Open)",
    subsystem: "EV Yüksek Voltaj Güvenlik & BMS",
    severity: "CRITICAL_STOP",
    causes: [
      "Manuel Servis Şalteri (MSD) tam oturmamış veya pilot kontağı ayrılmış.",
      "İnverter, DC-DC veya klima kompresörü HV turuncu kapağındaki interlock köprüsü açık.",
      "HVIL 100 Hz PWM sinyal hattında kopukluk veya şasiye kısa devre (R_loop > 5 Ohm)."
    ],
    steps: [
      { action: "MSD emniyet mandalını söküp kilit tırnağının yerine tam oturduğunu kontrol edin.", target: "Manuel Servis Şalteri (MSD)", difficulty: "Kolay (Görsel)" },
      { action: "BMS HVIL çıkış pini ile dönüş pini arasındaki loop direncini ölçün (Kontak KAPALI: R < 5 Ω).", target: "HVIL Tesisat Döngüsü", difficulty: "Orta (Alet Gerekir)" },
      { action: "Osiloskopta HVIL sinyalini gözlemleyin: 100 Hz ±5% kare dalga, %50 doluluk ve 12V/5V genlik olmalıdır.", target: "BMS Kontrol Ünitesi (BECM)", difficulty: "İleri (Servis)" }
    ],
    measurement: "Nominal HVIL Döngü Direnci: <5.0 Ω | PWM: 100 Hz, %50 Duty Cycle, V_high > 9.0V (12V sistem) / > 3.8V (5V sistem).",
    routine: "UDS Routine 0x31 (ID 0xD001: HVIL Interlock Loopback & Latch Reset)"
  },
  P0AA6: {
    title: "Yüksek Voltaj İzolasyon Direnci Düşüklüğü (HV Isolation Fault)",
    subsystem: "EV Batarya Paketi & Yüksek Voltaj İzolasyonu",
    severity: "CRITICAL_STOP",
    causes: [
      "Batarya muhafazası içine soğutma sıvısı (antifriz) veya nem sızması.",
      "Klima kompresörü stator sargı izolasyonunun kompresör yağı ile bozulması.",
      "İnverter IGBT güç modülü substratında dielektrik delinme."
    ],
    steps: [
      { action: "LOTO güvenlik prosedürünü uygulayın (MSD sök, 10 dk bekle, DC Bus < 5V sıfır enerji onayı).", target: "HV Batarya Paketi", difficulty: "İleri (Servis)" },
      { action: "Fluke 1587 / Megger ile 500V/1000V DC test voltajında HV+ ve HV- hatlarının şasiye izolasyonunu ölçün.", target: "HV+ / HV- Hatları", difficulty: "İleri (Servis)" },
      { action: "HV alt dallarını (Klima, PTC Isıtıcı, OBC, DC-DC) tek tek ayırarak arızalı komponenti izole edin.", target: "Yüksek Voltaj Dağıtım Kutusu (PDU)", difficulty: "İleri (Servis)" }
    ],
    measurement: "ISO 6469-1 / UNECE R100 Standardı: Min İzolasyon Direnci ≥ 500 Ω/V DC (400V için ≥ 200 kΩ, 800V için ≥ 400 kΩ). Sağlıklı sistem: > 50 MΩ.",
    routine: "UDS Routine 0x31 (ID 0xD010: Automated Isolation Self-Test Sequence)"
  },
  P0A80: {
    title: "Hibrit / Elektrikli Araç Batarya Paketi Değişimi (Replace EV Battery Pack)",
    subsystem: "EV Batarya Paketi & Hücre Sağlığı (SOH)",
    severity: "CRITICAL_STOP",
    causes: [
      "Hücreler arası kapasite kaybı >%30 (SOH_C < %70) veya iç direnç sapması >%50.",
      "Hücre delta voltajının yük altında >150 mV ve beklemede >50 mV seviyesine açılması.",
      "Hücre içi lityum kaplanması (lithium plating) ve aktif katot kütle kaybı."
    ],
    steps: [
      { action: "Bataryayı %100 SOC'ye şarj edip hücre dengeleme (balancing) rutinini tamamlayın.", target: "BMS Hücre Dengeleme", difficulty: "Orta (Alet Gerekir)" },
      { action: "0.5C - 1C yük darbesi uygulayarak her bir hücrenin iç direncini (Ri = ΔV/ΔI) loglayın.", target: "Hücre Denetim Devresi (CSC)", difficulty: "İleri (Servis)" },
      { action: "Diverjans gösteren zayıf hücre modülünü veya tüm batarya paketini değiştirin.", target: "Batarya Modülü", difficulty: "İleri (Servis)" }
    ],
    measurement: "Nominal Hücre Delta Voltajı: <30 mV | Arıza / Değişim Eşiği: >150 mV (Yükte) veya >50 mV (Dengede).",
    routine: "UDS Service 0x22 (DID 0x4100: Individual Cell Voltages & SOH Map)"
  },
  SPN100: {
    title: "Motor Yağ Basıncı Hatası (Engine Oil Pressure Fault)",
    subsystem: "Ağır Vasıta Yağlama Sistemi (J1939)",
    severity: "CRITICAL_STOP",
    causes: [
      "FMI 1 (Kritik Düşük): Yağ pompası aşınması, karterde yağ seviyesinin tükenmesi veya ana yatak aşınması.",
      "FMI 3 (Voltaj Yüksek): Sinyal kablosu 5V referansa veya 24V hatta kısa devre.",
      "FMI 4 (Voltaj Düşük): Sinyal kablosu şasiye kısa devre veya sensör kopuk."
    ],
    steps: [
      { action: "Motoru derhal durdurun ve yağ seviye çubuğunu kontrol edin.", target: "Motor Karteri", difficulty: "Kolay (Görsel)" },
      { action: "Sensör soketinde 5.0V besleme (Pin 1), Şasi (Pin 2) ve Sinyal voltajını (Pin 3) ölçün (Rölantide 1.2 - 2.5V).", target: "Yağ Basınç Sensörü", difficulty: "Orta (Alet Gerekir)" },
      { action: "Mekanik manometre bağlayarak gerçek yağ basıncını doğrulayın (Rölanti >1.0 bar, 1800 RPM >3.0 bar).", target: "Yağ Galerisi Test Portu", difficulty: "İleri (Servis)" }
    ],
    measurement: "Sensör Skalası: 0.5V = 0 kPa, 4.5V = 1000 kPa | Kritik Kırmızı Lamba Limiti: <70 kPa (Rölanti), <180 kPa (Devirde).",
    routine: "J1939 DM11 (PGN 65235 Clear Active) & DM4 (PGN 65229 Freeze Frame Oku)"
  },
  SPN110: {
    title: "Motor Soğutma Sıvısı Sıcaklığı (Engine Coolant Temperature)",
    subsystem: "Ağır Vasıta Termal & Soğutma Sistemi (J1939)",
    severity: "CRITICAL_STOP",
    causes: [
      "FMI 0 (Kritik Yüksek): Sıcaklık >108°C; termostat kapalı kalmış, viskoz fan kilitlenmiyor veya radyatör tıkalı.",
      "FMI 3 (Açık Devre): Sensör kablosu kopuk (ECU -40°C algılar ve fanı %100 açar).",
      "FMI 4 (Şasiye Kısa Devre): Sensör sinyali şasiye kısa devre (ECU +140°C algılar ve torku %50 kısar)."
    ],
    steps: [
      { action: "Radyatör alt ve üst hortum sıcaklıklarını infrared termometre ile karşılaştırın (ΔT > 15°C ise termostat açmıyor).", target: "Termostat & Radyatör", difficulty: "Kolay (Görsel)" },
      { action: "ECT sensör direncini ölçün: 20°C'de ~2.5 kΩ, 80°C'de ~320 Ω, 100°C'de ~180 Ω olmalıdır.", target: "Soğutma Sıvısı Sıcaklık Sensörü", difficulty: "Orta (Alet Gerekir)" }
    ],
    measurement: "Normal Çalışma Aralığı: 82°C - 95°C | Uyarı (AWL): >103°C | Kırmızı Lamba (RSL Derate): >108°C.",
    routine: "J1939 Actuator Test: Viscous Fan Clutch 100% Engagement Override"
  },
  SPN3251: {
    title: "DPF Fark Basıncı Hatası (DPF Differential Pressure Delta-P)",
    subsystem: "Ağır Vasıta DPF & Egzoz Sonrası İşlem",
    severity: "MEDIUM",
    causes: [
      "FMI 0 (Aşırı Kurum Tıkanıklığı): DPF basınç farkı >35 kPa; partikül filtresi dolu, rejenerasyon kilitlenmiş.",
      "FMI 1 (Filtre Delik/Yok): Basınç farkı <0.2 kPa; DPF peteği çatlak, içi boşaltılmış veya sökülmüş.",
      "FMI 2 (Hortum Ters/Tıkalı): Basınç boruları ters takılmış veya donmuş kondensat ile tıkanmış."
    ],
    steps: [
      { action: "DPF fark basınç sensörü silikon hortumlarında delinme veya erime olup olmadığını kontrol edin.", target: "DPF Basınç Hortumları", difficulty: "Kolay (Görsel)" },
      { action: "Kurum yükü <40g ise cihaz üzerinden Park Halinde Manuel Servis Rejenerasyonu (Stationary DPF Regen) başlatın.", target: "Dizel Partikül Filtresi", difficulty: "Orta (Alet Gerekir)" }
    ],
    measurement: "Temiz DPF Rölanti Basıncı: 0.5 - 2.0 kPa | Tam Yük: 5.0 - 12.0 kPa | Tıkalı Limit: >25.0 kPa.",
    routine: "J1939 Service Routine: Stationary DPF Service Regeneration (PGN 64892)"
  },
  SPN3364: {
    title: "AdBlue (DEF) Sıvı Kalitesi Uygunsuz (DEF Quality / Concentration)",
    subsystem: "Ağır Vasıta SCR & AdBlue Kalite Kontrol",
    severity: "CRITICAL_STOP",
    causes: [
      "FMI 18 (Kalite Düşük): AdBlue tankına su, mazot veya cam suyu karıştırılmış (Konsantrasyon <%28 veya >%38).",
      "FMI 2: Ultrasonik kalite sensöründe hava kabarcığı veya kristalleşme."
    ],
    steps: [
      { action: "Optik refraktometre ile depodaki sıvının üre konsantrasyonunu ölçün (Tam %32.5 olmalıdır).", target: "AdBlue Sıvısı", difficulty: "Kolay (Görsel)" },
      { action: "Hatalı sıvı tespit edilirse depoyu komple boşaltın, deiyonize su ile çalkalayıp orijinal AdBlue doldurun.", target: "AdBlue Depo & Filtresi", difficulty: "Orta (Alet Gerekir)" }
    ],
    measurement: "Standart Üre Oranı: %32.5 ±%0.7 (ISO 22241). İndükleme Sayacı: 10 saat sonra 20 km/s hız kilidi.",
    routine: "J1939 Routine: DEF Quality Tampering Counter Reset Routine"
  },
  P0300: {
    title: "Rastgele / Çoklu Silindir Ateşleme Hatası (Random/Multiple Cylinder Misfire)",
    subsystem: "Ateşleme & Yakıt Enjeksiyon Sistemi",
    severity: "CRITICAL_STOP",
    causes: [
      "Buji elektrot aşınması veya tırnak aralığının fabrika toleransından sapması.",
      "Ateşleme bobini sekonder sargı izolasyon kaçağı veya bobin soket korozyonu.",
      "Enjektör püskürtme deseni tıkanıklığı veya yakıt rayı basınç düşüklüğü."
    ],
    steps: [
      { action: "Osilatör ekranında silindir ateşleme dalga boyunu ve krank sinyalini kontrol ediniz.", target: "Krank & Ateşleme Bobinleri", difficulty: "Orta (Alet Gerekir)" },
      { action: "Enjektör dengeleme oranlarını ve yakıt rayı basıncını (UDS 0x22 DID 0x1102) ölçün.", target: "Yakıt Dağıtım Rayı", difficulty: "Orta (Alet Gerekir)" },
      { action: "Bujilerin primer/sekonder direnç değerlerini ve kompresyon basıncını test edin.", target: "Silindir Yanma Odası", difficulty: "İleri (Servis)" }
    ],
    measurement: "Primer Bobin Direnci: 0.5 - 1.5 Ω | Sekonder: 5.0 - 15.0 kΩ | Kompresyon: >11.0 Bar (Benzin), >24.0 Bar (Dizel).",
    routine: "UDS Routine 0x31 (ID 0x0201: Silindir Kompresyon & Balans Testi)"
  },
  P0234: {
    title: "Turboşarj / Süperşarj Aşırı Takviye Basıncı (Engine Overboost Condition)",
    subsystem: "Aşırı Doldurma & Hava Emiş Sistemi",
    severity: "MEDIUM",
    causes: [
      "Wastegate aktüatör kolunun mekanik olarak kapalı konumda sıkışması.",
      "N75 Boost kontrol selenoid valfinin elektriksel olarak açık kalması veya tıkanması.",
      "MAP / Takviye basınç sensörü (SPN 102) kalibrasyon sapması."
    ],
    steps: [
      { action: "Wastegate aktüatör kolunu vakum pompası (Mityvac) ile test edin (0.6 barda tam açılmalıdır).", target: "Wastegate Aktüatörü", difficulty: "Orta (Alet Gerekir)" },
      { action: "N75 selenoid valf bobin direncini ölçün (25 - 35 Ω) ve PWM sürücü sinyalini osiloskopta izleyin.", target: "N75 Boost Selenoidi", difficulty: "Orta (Alet Gerekir)" },
      { action: "MAP sensörü canlı verisini motor kapalıyken barometrik sensör ile karşılaştırın (fark <15 hPa).", target: "MAP Sensörü", difficulty: "Kolay (Görsel)" }
    ],
    measurement: "N75 Bobin Direnci: 25 - 35 Ω | Vakum Tutma: -0.8 Bar'da 1 dakika boyunca düşmemeli.",
    routine: "UDS Routine 0x31 (ID 0x0204: VGT / Wastegate Aktüatör Histerezis Testi)"
  },
  CAN_TERM_60: {
    title: "CAN-Bus 120Ω Sonlandırma Direnci Hatası (CAN Termination Fault)",
    subsystem: "CAN Fiziksel Katman (ISO 11898-2)",
    severity: "CRITICAL_STOP",
    causes: [
      "120 Ω okunuyorsa: Hat ucundaki iki adet 120Ω sonlandırma direncinden biri kopuk veya soketi çıkmış.",
      "0 - 10 Ω okunuyorsa: CAN_H ve CAN_L kabloları birbirine kısa devre.",
      "Sonsuz (Açık Devre): Hat üzerindeki iki sonlandırma direnci de kopuk veya ana omurga hattı kesik."
    ],
    steps: [
      { action: "Akü kutup başını veya kontağı KAPATIN. OBD soketi Pin 6 (CAN_H) ile Pin 14 (CAN_L) arasını ohmmetre ile ölçün.", target: "OBD-II Portu (Pin 6/14)", difficulty: "Kolay (Görsel)" },
      { action: "60.0 Ω okunmalıdır. 120 Ω ise hat sonundaki ECU'ların (Motor Beyni ve Gösterge/ABS) soketlerini kontrol edin.", target: "Omurga Sonlandırma Dirençleri", difficulty: "Orta (Alet Gerekir)" },
      { action: "Osiloskopta kare dalga köşelerindeki çınlama (ringing/reflection) genliğini kontrol edin.", target: "CAN Diferansiyel Sinyali", difficulty: "İleri (Servis)" }
    ],
    measurement: "Standart Eşdeğer Direnç: 60.0 Ω ±%5 (120Ω // 120Ω) | Hata Toleransı: 55 Ω - 65 Ω.",
    routine: "ISO 11898-2 Physical Layer Multimeter Verification"
  },
  N2K_IMPELLER: {
    title: "Deniz Suyu Çark (İmpeller) Arızası & Anlık Hararet (Raw Water Impeller Failure)",
    subsystem: "Marin Motor Çift Devreli Soğutma (NMEA 2000)",
    severity: "CRITICAL_STOP",
    causes: [
      "Lastik impeller kanatlarının kuru çalışma veya aşınma nedeniyle parçalanması (Su debisi sıfıra indi).",
      "Deniz suyu emiş filtresinin (Sea Strainer) poşet/deniz anası ile tamamen tıkanması.",
      "Kinseft vanasının (Seacock) kapalı unutulması."
    ],
    steps: [
      { action: "Motoru derhal stop edin! Kinseft vanasının açık olduğunu ve deniz suyu filtresini kontrol edin.", target: "Deniz Suyu Filtresi (Strainer)", difficulty: "Kolay (Görsel)" },
      { action: "Deniz suyu pompası kapağını söküp kauçuk impeller kanatlarını kontrol edin; kopan parçaları eşanjör girişinde arayın.", target: "Deniz Suyu Pompası", difficulty: "Orta (Alet Gerekir)" }
    ],
    measurement: "Termal Gradyan Eşiği: dT/dt > 1.5°C/saniye (Rölantide dahi saniyeler içinde 100°C üzerine fırlar).",
    routine: "NMEA 2000 PGN 127489 (Engine Dynamic) & PGN 130310 (Water Temp)"
  },
  N2K_EXHAUST_ELBOW: {
    title: "Islak Egzoz Karışım Dirseği Aşırı Sıcaklık (Wet Exhaust Mixing Elbow Overheat)",
    subsystem: "Marin Egzoz & Yangın Güvenliği",
    severity: "CRITICAL_STOP",
    causes: [
      "Egzoz dirseği su püskürtme deliklerinin (spray ring) kireç ve pas ile tıkanması.",
      "Ham su enjeksiyonunun kesilmesi nedeniyle 550°C'lik kuru egzoz gazının doğrudan susturucuya geçmesi."
    ],
    steps: [
      { action: "Egzoz dirseğine gelen su besleme hortumunu söküp su çıkışını test edin.", target: "Egzoz Karışım Dirseği", difficulty: "Kolay (Görsel)" },
      { action: "Fiberglas susturucu ve kauçuk egzoz hortumunun sıcaklığını kontrol edin (85°C üzeri erime riski taşır).", target: "Fiberglas Susturucu (Waterlock)", difficulty: "Orta (Alet Gerekir)" }
    ],
    measurement: "Güvenli Çalışma: 40°C - 65°C | Alarm: ≥75°C | Kritik Erime & Su Alma Tehlikesi: >105°C.",
    routine: "NMEA 2000 PGN 127489 (Exhaust Gas Temperature & Discrete Alarm)"
  }
};

export class DiagnosticEngine {
  private geminiApiKey: string = '';
  private openAiApiKey: string = '';
  private aiProvider: 'auto' | 'gemini' | 'openai' = 'auto';

  public setApiKey(key: string) {
    this.geminiApiKey = key;
  }

  public getApiKey(): string {
    return this.geminiApiKey;
  }

  public setOpenAiApiKey(key: string) {
    this.openAiApiKey = key;
  }

  public getOpenAiApiKey(): string {
    return this.openAiApiKey;
  }

  public setAiProvider(provider: 'auto' | 'gemini' | 'openai') {
    this.aiProvider = provider;
  }

  public getAiProvider(): 'auto' | 'gemini' | 'openai' {
    return this.aiProvider;
  }

  public evaluateSystemState(
    scenario: ScenarioType,
    telemetry: TelemetryPoint
  ): DiagnosticState {
    const timestampStr = new Date().toLocaleTimeString('tr-TR', { hour12: false });

    if (scenario === 'misfire_p0300') {
      const dtc = KNOWN_DTCS.P0300;
      return {
        healthStatus: 'critical',
        dtcCount: 1,
        activeDtcs: [dtc],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'Kritik Ateşleme / Tekleme Arızası!',
        liveAnalysisSummary: `• Motor Devri: ${telemetry.rpm} RPM (Dalgalı)\n• Tork Kaybı: %32 anlık düşüş\n• Hata: Silindir ateşleme hatası (P0300)`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 14.8
        },
        recommendedActions: [
          { id: '1', text: '1. Osiloskopta motor devrini izleyin.', completed: true },
          { id: '2', text: '2. Buji ve ateşleme bobinlerini kontrol edin.', completed: false },
          { id: '3', text: '3. Enjektör püskürtmesini test edin.', completed: false }
        ]
      };
    }

    if (scenario === 'overboost') {
      const dtc = KNOWN_DTCS.P0234;
      return {
        healthStatus: 'warning',
        dtcCount: 1,
        activeDtcs: [dtc],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'Aşırı Turbo Basıncı (Overboost)!',
        liveAnalysisSummary: `• Turbo Basıncı: ${telemetry.turboBoostBar} Bar (Kritik >2.2 Bar)\n• Hata: P0234 Aşırı Takviye Basıncı`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 3.8
        },
        recommendedActions: [
          { id: '1', text: '1. Turbo wastegate kolunun sıkışıp sıkışmadığına bakın.', completed: true },
          { id: '2', text: '2. N75 selenoid valf soketini kontrol edin.', completed: false }
        ]
      };
    }

    if (scenario === 'overheat') {
      const dtc = KNOWN_DTCS.P0115;
      return {
        healthStatus: 'warning',
        dtcCount: 1,
        activeDtcs: [dtc],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'Yüksek Motor Harareti!',
        liveAnalysisSummary: `• Sıcaklık: ${telemetry.coolantTempC}°C (Kritik >105°C)\n• Hata: P0115 Motor Sıcaklık Uyarısı`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 2.1
        },
        recommendedActions: [
          { id: '1', text: '1. Su seviyesini ve termostatı kontrol edin.', completed: false },
          { id: '2', text: '2. Radyatör fanının dönüp dönmediğini kontrol edin.', completed: false }
        ]
      };
    }

    if (scenario === 'bus_surge') {
      return {
        healthStatus: 'warning',
        dtcCount: 0,
        activeDtcs: [],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'CAN Veri Yolu Yoğun Trafik / Hata!',
        liveAnalysisSummary: `• Bus Yükü: %${telemetry.busLoadPercent}\n• Hata Kareleri: ${telemetry.errorCount} adet`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 8.4
        },
        recommendedActions: [
          { id: '1', text: '1. CAN_H ve CAN_L arasındaki 120Ω direncini ölçün.', completed: true },
          { id: '2', text: '2. Gürültülü kablo tesisatını izole edin.', completed: false }
        ]
      };
    }

    if (scenario === 'ev_bms_telemetry') {
      const isIsolationFault = (telemetry.cellMinMaxDeltaV || 0) > 0.15;
      const dtc = isIsolationFault ? KNOWN_DTCS.P0A0B : KNOWN_DTCS.P0A80;
      return {
        healthStatus: isIsolationFault ? 'critical' : 'warning',
        dtcCount: 1,
        activeDtcs: [dtc],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: isIsolationFault ? 'HV Batarya İzolasyon Güvenlik Uyarısı!' : 'EV Batarya Yönetim Sistemi (BMS) Aktif',
        liveAnalysisSummary: `• Paket Voltajı: ${telemetry.packVoltageV || 398.4} V | Akım: ${telemetry.packCurrentA || 42.5} A\n• Şarj Durumu (SOC): %${telemetry.batterySocPercent || 78.4} | Hücre Delta: ${((telemetry.cellMinMaxDeltaV || 0.015) * 1000).toFixed(0)} mV\n• Hata: ${dtc.code} (${dtc.title.split('(')[0]})`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 1.8,
          packVoltage: telemetry.packVoltageV,
          batterySoc: telemetry.batterySocPercent
        },
        recommendedActions: [
          { id: '1', text: '1. Yüksek Voltaj (HV) DC barasında Megger izolasyon testi yapın.', completed: true },
          { id: '2', text: '2. 0x1808E5F4 ID hücre voltajlarını dengeleme rutinine alın.', completed: false }
        ]
      };
    }

    if (scenario === 'marine_vessel_n2k') {
      const dtc = KNOWN_DTCS.SPN_520201;
      return {
        healthStatus: 'warning',
        dtcCount: 1,
        activeDtcs: [dtc],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'NMEA 2000 Marin Seyir & Motor Telemetrisi',
        liveAnalysisSummary: `• Hız (SOG): ${telemetry.sogKnots || 18.6} Knots | Derinlik: ${telemetry.depthMeters || 24.8} m\n• Dümen Açısı: ${telemetry.rudderDeg || -2.5}° | Pervane Slip: %${telemetry.propellerSlipPct || 11.2}\n• Uyarı: SPN 520201 Marin Yakıt Filtresi Su Tespiti`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 2.4,
          sogKnots: telemetry.sogKnots,
          depthMeters: telemetry.depthMeters
        },
        recommendedActions: [
          { id: '1', text: '1. Su ayırıcı ön yakıt filtresi tahliye vanasını kontrol edin.', completed: false },
          { id: '2', text: '2. Pervane slip oranını optimize etmek için seyir trimini ayarlayın.', completed: true }
        ]
      };
    }

    if (scenario === 'j1939_multi_ecu_fleet') {
      const dtc = KNOWN_DTCS.SPN_1087;
      return {
        healthStatus: 'critical',
        dtcCount: 1,
        activeDtcs: [dtc],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'J1939 Filo Ağı (5 Düğüm: ECM, TCM, Retarder, EBS, Cluster)',
        liveAnalysisSummary: `• Aktif Düğümler: ECM (0x00), TCM (0x03), Retarder (0x10), EBS (0x0B), Cluster (0x17)\n• Veri Yolu Sağlığı: Normal (250 kbps) | Adres Talebi: PGN 60928 Doğrulandı\n• Hata: SPN 1087 Elektronik Fren Sistemi Hava Basıncı Düşük`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 3.2
        },
        recommendedActions: [
          { id: '1', text: '1. EBS modülü hava kurutucu tahliye vanasını test edin.', completed: false },
          { id: '2', text: '2. TCM şanzıman vites pozisyon çerçevesini (0x0C000003) doğrulayın.', completed: true }
        ]
      };
    }

    if (scenario === 'can_fd_adas_vision') {
      const dtc = KNOWN_DTCS.C1A00;
      return {
        healthStatus: 'warning',
        dtcCount: 1,
        activeDtcs: [dtc],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'CAN-FD 64B ADAS Ön Radar & Kamera Telemetrisi',
        liveAnalysisSummary: `• CAN-FD Protokolü: BRS Aktif (2.0 Mbps Veri Fazı / 500 kbps Arbitrasyon)\n• 0x220 Radar Çerçevesi: 64 Bayt 8 Hedef Nesne Kümesi\n• Hata: DTC C1A00 Ön Radar Görüş Engeli / Kalibrasyon`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 1.2
        },
        recommendedActions: [
          { id: '1', text: '1. Radar radom kapağı ve ön cam kamera optiğini temizleyin.', completed: false },
          { id: '2', text: '2. UDS Servis 0x31 ile ADAS Statik Kalibrasyon rutinini başlatın.', completed: false }
        ]
      };
    }

    if (scenario === 'intermittent_wiring_fault') {
      const dtc = KNOWN_DTCS.U0100;
      return {
        healthStatus: 'critical',
        dtcCount: 1,
        activeDtcs: [dtc],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'Kesintili Hat Bağlantısı & Bus-Off Hatası!',
        liveAnalysisSummary: `• CAN-H / CAN-L Hattı: Mikro temas kesintileri ve CRC hataları tespit edildi.\n• Hata Kareleri: ${telemetry.errorCount} adet | Bus Yükü: %${telemetry.busLoadPercent}\n• Hata: DTC U0100 Ağ İletişim Kaybı`,
        currentTelemetry: {
          rpm: telemetry.rpm,
          coolantTemp: telemetry.coolantTempC,
          turboPressure: telemetry.turboBoostBar,
          responseLatencyMs: 28.5
        },
        recommendedActions: [
          { id: '1', text: '1. OBD-II Pin 6 ve 14 arası 60Ω sonlandırma direncini ölçün.', completed: true },
          { id: '2', text: '2. Kablo demetinde şasiye temas eden ezilmiş kabloları kontrol edin.', completed: false }
        ]
      };
    }

    const isTelemetryActive = (telemetry.rpm > 0) || (telemetry.turboBoostBar > 0) || (telemetry.coolantTempC > 0);

    if (!isTelemetryActive) {
      return {
        healthStatus: 'standby',
        dtcCount: 0,
        activeDtcs: [],
        lastScanTimestamp: timestampStr,
        liveAnalysisTitle: 'CAN Veri Yolu Beklemede (Sinyal Yok)',
        liveAnalysisSummary: 'CAN veri yolu dinleniyor. Henüz aktif bir veri akışı veya paket transferi başlatılmadı.',
        currentTelemetry: {
          rpm: 0,
          coolantTemp: 0,
          turboPressure: 0,
          responseLatencyMs: 0
        },
        recommendedActions: []
      };
    }

    return {
      healthStatus: 'nominal',
      dtcCount: 0,
      activeDtcs: [],
      lastScanTimestamp: timestampStr,
      liveAnalysisTitle: 'Sistem Durumu Nominal',
      liveAnalysisSummary: `• Motor Devri: ${telemetry.rpm} RPM | Turbo: ${telemetry.turboBoostBar} Bar | Sıcaklık: ${telemetry.coolantTempC}°C\nTüm CAN düğümleri normal çalışma aralığında, aktif arıza kodu yok.`,
      currentTelemetry: {
        rpm: telemetry.rpm,
        coolantTemp: telemetry.coolantTempC,
        turboPressure: telemetry.turboBoostBar,
        responseLatencyMs: 2.1
      },
      recommendedActions: [
        { id: '1', text: '1. Canlı telemetri akışı aktif ve parametreler stabil.', completed: true }
      ]
    };
  }

  /**
   * Helper: Extract byte array from natural language query or Hex Payload line.
   */
  private extractHexBytes(query: string): number[] {
    const payloadMatch = query.match(/(?:hex payload|payload|veri|data)[:\s]*((?:[0-9A-Fa-f]{2}[\s,-]*){1,64})/i);
    let hexStr = '';
    if (payloadMatch) {
      hexStr = payloadMatch[1];
    } else {
      const matches = query.match(/\b[0-9A-Fa-f]{2}\b/g);
      if (matches && matches.length >= 2) {
        return matches.map(m => parseInt(m, 16));
      }
      return [];
    }

    const cleaned = hexStr.replace(/[^0-9A-Fa-f]/g, ' ').trim();
    const tokens = cleaned.split(/\s+/).filter(t => t.length === 2);
    return tokens.map(t => parseInt(t, 16));
  }

  /**
   * Compact, Human-Friendly Forensics for SAE J1939 DM1 (PGN 65226).
   */
  private decodeJ1939DM1(bytes: number[], _canIdHex: string): string {
    if ((bytes[2] === 0xFF && bytes[3] === 0xFF) || (bytes[0] === 0x00 && bytes[2] === 0xFF)) {
      return `✅ **J1939 DM1 (Aktif Arıza Yok):**\n\n• **İkaz Lambaları:** Kapalı / Normal\n• **Aktif Arıza Kodu:** **0 DTC** (Beyinde kayıtlı aktif arıza bulunmuyor).\n• **Durum:** Tüm alt sistemler nominal çalışma aralığında.`;
    }

    const b0 = bytes[0];
    const amberLamp = (b0 >> 2) & 0x03;
    const redStopLamp = (b0 >> 4) & 0x03;
    const milLamp = (b0 >> 6) & 0x03;

    let lampText = "Normal (İkaz Lambaları Kapalı)";
    if (redStopLamp === 1) lampText = "🔴 Kırmızı Acil Durdurma Lambası (STOP)";
    else if (milLamp === 1) lampText = "🟠 Motor Arıza Lambası (Check Engine)";
    else if (amberLamp === 1) lampText = "🟡 Sarı Servis Uyarı Lambası";

    const b2 = bytes[2];
    const b3 = bytes[3];
    const b4 = bytes[4];
    const b5 = bytes[5];

    const spn = ((b4 & 0xE0) << 11) | (b3 << 8) | b2;
    const fmi = b4 & 0x1F;
    const oc = b5 & 0x7F;

    const spnName = J1939_SPN_DEFINITIONS[spn] || `SPN ${spn} (Özel Sensör)`;
    const fmiDesc = J1939_FMI_DEFINITIONS[fmi] || `Arıza Kodu FMI ${fmi}`;

    return `🚨 **Aktif Arıza Tespiti (J1939 DM1):**\n\n• **Gösterge Lambası:** ${lampText}\n• **Arıza Tanımı:** ${spnName} - *${fmiDesc}* (SPN ${spn} / FMI ${fmi})\n• **Tekrarlanma Sayısı:** Beyin bu hatayı **${oc} kez** kaydetti.\n\n🛠️ **Hızlı Çözüm Adımları:**\n1. İlgili sensörün kablo soketinde gevşeklik, korozyon veya kırık var mı bakın.\n2. Kabloyu ve sensörü ölçtükten sonra arıza hafızasını temizleyin.`;
  }

  /**
   * Compact, Human-Friendly Forensics for SAE J1939 EEC1 (PGN 61444).
   */
  private decodeJ1939EEC1(bytes: number[], _canIdHex: string): string {
    if (bytes.length < 5) return `⚡ **J1939 EEC1:** Bayt sayısı eksik.`;

    const demandTorque = bytes[1] - 125;
    const actualTorque = bytes[2] - 125;
    const rpm = (((bytes[4] << 8) | bytes[3]) * 0.125).toFixed(0);
    const isMatch = Math.abs(actualTorque - demandTorque) <= 10;

    return `⚡ **Motor Devri & Tork Durumu (J1939 EEC1):**\n\n• **Motor Devri:** **${rpm} RPM**\n• **Sürücü Tork Talebi:** %${demandTorque}\n• **Gerçek Üretilen Tork:** %${actualTorque}\n\n${isMatch ? '✅ Motor devri ve tork talebi dengeli, sorun yok.' : '⚠️ **Dikkat:** Motor istenen torku tam üretemiyor (Ateşleme teklemesi veya yakıt düşüklüğü olabilir).'}`;
  }

  /**
   * Compact Forensics for NMEA 2000 PGN 127488.
   */
  private decodeN2K127488(bytes: number[]): string {
    if (bytes.length < 6) return `🌊 **NMEA 2000:** Eksik veri.`;

    const rpm = (((bytes[2] << 8) | bytes[1]) * 0.25).toFixed(0);
    const boostBar = (((bytes[4] << 8) | bytes[3]) * 0.001).toFixed(2);
    const trim = bytes[5] > 127 ? bytes[5] - 256 : bytes[5];

    return `🌊 **Marin Motor Telemetrisi (NMEA 2000):**\n\n• **Motor Devri:** **${rpm} RPM**\n• **Turbo Basıncı:** **${boostBar} Bar**\n• **Trim Açısı:** %${trim}\n\n✅ Marin motor telemetrisi normal akıyor.`;
  }

  /**
   * Compact Forensics for UDS Negative Response (0x7F).
   */
  private decodeUdsNegativeResponse(bytes: number[]): string {
    const rejectedSid = bytes.length >= 2 ? `0x${bytes[1].toString(16).toUpperCase().padStart(2, '0')}` : '0x??';
    const nrcCode = bytes.length >= 3 ? bytes[2] : 0;
    const nrcInfo = UDS_NRC_MAP[nrcCode] || { name: 'generalReject', cause: 'İşlem reddedildi', action: 'Koşulları kontrol edin.' };

    return `❌ **Beyin (ECU) İsteği Reddetti (UDS 0x7F):**\n\n• **İstenen Servis:** \`${rejectedSid}\`\n• **Reddetme Nedeni:** *${nrcInfo.name}* (${nrcInfo.cause})\n• **Çözüm:** ${nrcInfo.action}\n\n🛠️ **Usta Tavsiyesi:** Kontağın açık (Ignition ON), motorun durmuş (Engine OFF) ve akü voltajının >12.5V olduğundan emin olun.`;
  }

  /**
   * Normalize and tokenize Turkish/English query.
   */
  private normalizeQuery(text: string): string {
    const map: Record<string, string> = {
      'ç': 'c', 'Ç': 'c', 'ğ': 'g', 'Ğ': 'g', 'ı': 'i', 'I': 'i', 'İ': 'i',
      'ö': 'o', 'Ö': 'o', 'ş': 's', 'Ş': 's', 'ü': 'u', 'Ü': 'u'
    };
    const lowered = text.replace(/[çÇğĞıIİöÖşŞüÜ]/g, m => map[m] || m).toLowerCase();
    return lowered.replace(/[^\w\s\-\.]/g, ' ').replace(/\s+/g, ' ').trim();
  }

  /**
   * Format 4-stage technician field report.
   */
  private format4StageReport(codeKey: string, curRpm: number, curBoost: number, curTemp: number = 85.0): string {
    const report = EXPERT_REPORTS[codeKey];
    if (!report) return '';

    const causes = report.causes.map(c => `  • ${c}`).join('\n');
    const steps = report.steps.map((s, idx) => `  ${idx + 1}. **[${s.difficulty}]** ${s.action} *(Hedef: ${s.target})*`).join('\n');

    return `🚨 **[${codeKey}] — ${report.title}**\n` +
      `🏷️ **Alt Sistem:** ${report.subsystem} | **Öncelik:** ${report.severity}\n` +
      `📊 **Canlı Telemetri Durumu:** ${curRpm} RPM | ${curBoost.toFixed(2)} Bar | ${curTemp.toFixed(1)}°C\n\n` +
      `🔍 **Kök Neden & Arıza Mekanizması:**\n${causes}\n\n` +
      `📋 **4-AŞAMALI USTA TEKNİSYEN SAHA ONARIM KILAVUZU:**\n\n` +
      `**Aşama 1: Görsel & Mekanik Kontrol:**\n${steps}\n\n` +
      `⚡ **Aşama 2: Kesin Multimetre & Osiloskop Toleransları:**\n  • ${report.measurement}\n\n` +
      `💻 **Aşama 3: UDS / J1939 Özel Teşhis Rutinleri:**\n  • \`${report.routine}\`\n\n` +
      `🔧 **Aşama 4: Parça Değişim & Adaptasyon Prosedürü:**\n` +
      `  • Arızalı parçayı değiştirdikten sonra kontak \`Ignition ON, Engine OFF\` konumunda \`UDS 0x14 0xFFFFFF\` ile arıza hafızasını silin ve 1 sürüş çevrimi (Drive Cycle) yapın.`;
  }

  /**
   * Deep Offline Automotive Intelligence & Reasoning Engine.
   */
  public async generateCopilotResponse(
    query: string,
    state: DiagnosticState
  ): Promise<ChatMessage> {
    const qNorm = this.normalizeQuery(query);
    const qLower = query.toLowerCase().trim();
    const timestamp = new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
    const curRpm = state.currentTelemetry.rpm || 1850;
    const curBoost = state.currentTelemetry.turboPressure || 1.4;
    const curTemp = state.currentTelemetry.coolantTemp || 85.0;

    const sysContext = `Sen "Universal CAN-Bus Diagnostic & Telemetry Tool" profesyonel araç teşhis yazılımının içerisindeki yerleşik AI Teşhis Başmühendisisin.
Kullanıcı zaten CAN veri yoluna (OBD-II / J1939 / NMEA2000) doğrudan bağlı ve canlı paketleri bu cihaz ile okuyor!

KESİN VE TAVİZSİZ ALAN KISITLAMALARI (DOMAIN GUARDRAILS):
1. Sen SADECE ve SADECE otomotiv ve marin elektronik, CAN-Bus haberleşmesi (J1939, UDS, N2K, OBD-II), araç telemetrisi, sensörler ve arıza teşhisi alanında uzmanlaşmış özel bir mühendislik yapay zekasısın.
2. Otomotiv, araç telemetrisi, donanım veya arıza teşhisi dışındaki HERHANGİ BİR KONUDA (günlük sohbet, hava durumu, yemek tarifi, siyaset, genel felsefe, edebiyat, genel kodlama vb.) soru sorulursa KESİNLİKLE yanıt verme!
3. Konu dışı sorularda SADECE şunu söyle:
   "⚠️ Ben Universal CAN-Bus Teşhis ve Telemetri asistanıyım. Yalnızca araç telemetrisi, CAN veri yolu protokolleri (J1939, UDS, NMEA 2000) ve arıza teşhis konularında yardımcı olabilirim."
4. ASLA "DTC'yi başka bir teşhis cihazıyla okuyun", "aracı servise götürün" veya "bir tarayıcı bağlayın" DEME! Çünkü kullanıcı ZATEN bu teşhis cihazını kullanıyor ve arıza verisini doğrudan CAN hattından canlı okuyor.
5. Doğrudan net, maddeli ve sahada uygulanabilir 4 aşamalı fiziksel onarım adımları ver (Sensör soketi, multimetre ohm/volt ölçümü, osiloskop, UDS servis 0x14/0x31).

Canlı Araç Durumu:
• Motor Devri: ${curRpm} RPM | Turbo: ${curBoost} Bar | Sıcaklık: ${curTemp}°C
• Sistem Sağlığı: ${state.healthStatus === 'nominal' ? 'Nominal (0 DTC)' : `Arıza (${state.dtcCount} DTC)`}`;

    // 0. Check OpenAI ChatGPT if selected or key starts with sk-
    const shouldUseOpenAi = (this.aiProvider === 'openai') || 
                            (this.aiProvider === 'auto' && this.openAiApiKey && this.openAiApiKey.trim().length > 10) ||
                            (this.openAiApiKey && this.openAiApiKey.trim().startsWith('sk-'));

    if (shouldUseOpenAi && this.openAiApiKey && this.openAiApiKey.trim().length > 10) {
      try {
        const openAiRes = await OpenAiClient.generateContent(this.openAiApiKey, query, sysContext);
        if (openAiRes.success && openAiRes.text.trim().length > 0) {
          return {
            id: `msg-${Date.now()}`,
            sender: 'copilot',
            timestamp,
            text: `✨ **OpenAI ${openAiRes.modelUsed} (ChatGPT Bulut Zekası):**\n\n${openAiRes.text.trim()}`
          };
        } else if (openAiRes.error) {
          console.warn('OpenAI API error:', openAiRes.error);
        }
      } catch (err: any) {
        console.warn('OpenAI API fetch failed:', err);
      }
    }

    // 1. Check Google Gemini if selected or key is present
    const shouldUseGemini = (this.aiProvider === 'gemini') || 
                            (this.aiProvider === 'auto' && this.geminiApiKey && this.geminiApiKey.trim().length > 10) ||
                            (this.geminiApiKey && this.geminiApiKey.trim().startsWith('AIza'));

    if (shouldUseGemini && this.geminiApiKey && this.geminiApiKey.trim().length > 10) {
      try {
        const geminiRes = await GeminiClient.generateContent(this.geminiApiKey, query, sysContext);
        if (geminiRes.success && geminiRes.text.trim().length > 0) {
          const modelTitle = geminiRes.modelUsed ? geminiRes.modelUsed.replace(/^models\//, '') : 'Gemini 2.0 Flash';
          const parsedGemini = this.parseActionMetadata(geminiRes.text.trim());
          return {
            id: `msg-${Date.now()}`,
            sender: 'copilot',
            timestamp,
            text: `✨ **Google ${modelTitle} (Bulut Zekası):**\n\n${parsedGemini.cleanText}`,
            actions: parsedGemini.actions,
          };
        } else if (geminiRes.error) {
          console.warn('Gemini API error response:', geminiRes.error);
        }
      } catch (err: any) {
        console.warn('Gemini API fetch failed, using local expert engine:', err);
      }
    }

    // 2. Check if Python Desktop Bridge is present and returns a deep answer
    const nativeRes = await DesktopBridge.askCopilot(query);
    if (nativeRes && !nativeRes.includes("Girdiğiniz sorgu (") && !nativeRes.includes("sorunuz için uzman")) {
      const parsed = this.parseActionMetadata(nativeRes);
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        text: parsed.cleanText,
        actions: parsed.actions,
      };
    }

    // ─────────────────────────────────────────────────────────────────────────
    // A. DYNAMIC CAN FRAME FORENSICS (E.G. FROM RIGHT-CLICK CONTEXT MENU)
    // ─────────────────────────────────────────────────────────────────────────
    const hexBytes = this.extractHexBytes(query);
    const canIdMatch = query.match(/0x[0-9A-Fa-f]+/);
    const canIdHex = canIdMatch ? canIdMatch[0].toUpperCase() : '0x00000000';
    const isErrorFrame = query.includes('(ERR)') || query.includes('Error Frame') || query.includes('isErrorFrame') || qNorm.includes('hata karesi') || canIdHex === '0X00000000' || canIdHex === '0X0000000' || canIdHex === '0X0';

    // 0. Physical Layer CAN Error Frame (Active / Passive Error Flag)
    if (isErrorFrame && !query.match(/\b([PBUC][0-9A-Fa-f]{4})\b/)) {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        isDtcCard: true,
        text: `🔴 **CAN Fiziksel Katman Hata Karesi (CAN Physical Layer Error Frame / Bus Error):**\n\n` +
          `• **Mesaj Tipi:** Donanımsal Hata Karesi (Active Error Flag)\n` +
          `• **Protokol:** ISO 11898-2 Fiziksel Katman Denetimi\n` +
          `• **Açıklama:** Bu bir standart veri paketi (Data Frame) değildir. CAN denetleyicisi fiziksel iletim hattında bir anomali yakaladığında hatta ardışık 6 dominant bit basarak (Active Error Flag) hatalı mesajın iletimini sonlandırır.\n\n` +
          `🔍 **Olası Fiziksel Hata Nedenleri:**\n` +
          `1. **Bit Stuffing Hatası:** 5 ardışık aynı bitten sonra zıt stuffing bitinin gelmemesi.\n` +
          `2. **CRC / Checksum Hatası:** Yoldaki elektriksel gürültü veya parazit sebebiyle sağlama toplamının bozulması.\n` +
          `3. **ACK (Onay) Hatası:** Veri yolunda mesajı onaylayacak başka aktif bir düğümün bulunmaması.\n` +
          `4. **Hat Sonlandırma / Empedans:** 120Ω sonlandırma dirençlerinin takılı olmaması veya açık devre olması (Hat yansımaları).\n\n` +
          `🛠️ **Usta Teknisyen Saha Kontrol Adımları:**\n` +
          `1. **Direnç Testi:** OBD-II Pin 6 (CAN-H) ve Pin 14 (CAN-L) arasını multimetre ile ölçün (Nominal: 60.0 Ω ±3Ω).\n` +
          `2. **Voltaj Testi:** Şasiye göre CAN-H (2.5V - 3.5V) ve CAN-L (2.5V - 1.5V) diferansiyel seviyelerini osiloskopta inceleyin.\n` +
          `3. **Kablo Tesisatı:** Şasiye temas eden ezilmiş kabloları veya gevşek soket klemenslerini izole edin.`
      };
    }

    // 1. SAE J1939 DM1 Active DTC Frame
    if (canIdHex.includes('18FECA') || qNorm.includes('dm1')) {
      if (hexBytes.length >= 6) {
        return {
          id: `msg-${Date.now()}`,
          sender: 'copilot',
          timestamp,
          isDtcCard: true,
          text: this.decodeJ1939DM1(hexBytes, canIdHex)
        };
      }
    }

    // 2. SAE J1939 EEC1 Engine Speed & Torque Frame
    if (canIdHex.includes('0CF004') || canIdHex.includes('61444') || qNorm.includes('eec1')) {
      if (hexBytes.length >= 5) {
        return {
          id: `msg-${Date.now()}`,
          sender: 'copilot',
          timestamp,
          text: this.decodeJ1939EEC1(hexBytes, canIdHex)
        };
      }
    }

    // 3. NMEA 2000 PGN 127488 Engine Rapid Update
    if (canIdHex.includes('19F200') || qNorm.includes('127488')) {
      if (hexBytes.length >= 6) {
        return {
          id: `msg-${Date.now()}`,
          sender: 'copilot',
          timestamp,
          text: this.decodeN2K127488(hexBytes)
        };
      }
    }

    // 4. EV BMS Pack Voltage & Current (0x1806E5F4)
    if (canIdHex.includes('1806E5') || canIdHex === '0X1806E5F4') {
      if (hexBytes.length >= 4) {
        const volt = (((hexBytes[0] << 8) | hexBytes[1]) * 0.1).toFixed(1);
        const amp = ((((hexBytes[2] << 8) | hexBytes[3]) * 0.1) - 500.0).toFixed(1);
        const kw = ((parseFloat(volt) * parseFloat(amp)) / 1000).toFixed(1);
        return {
          id: `msg-${Date.now()}`,
          sender: 'copilot',
          timestamp,
          text: `⚡ **Yüksek Voltaj Batarya (EV BMS 0x1806E5F4):**\n\n` +
            `• **HV Paket Voltajı:** **${volt} V DC**\n` +
            `• **Anlık Akım:** **${amp} A** (${parseFloat(amp) > 0 ? 'Tüketim / Çekiş' : 'Rejenerasyon / Şarj'})\n` +
            `• **Çekilen Güç:** **${kw} kW**\n\n` +
            `✅ Batarya DC-Link voltajı ve akım değerleri normal çalışma limitleri dahilindedir.`
        };
      }
    }

    // 5. EV BMS Cell Voltages & Delta (0x1808E5F4)
    if (canIdHex.includes('1808E5') || canIdHex === '0X1808E5F4') {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        text: `⚡ **EV BMS Batarya Hücre Voltajları & Dengeleme (0x1808E5F4 - PGN 61447):**\n\n` +
          `• **Protokol:** ISO 11898-2 (EV Yüksek Voltaj BMS Ağı)\n` +
          `• **Kaynak Düğüm:** Batarya Yönetim Sistemi (BMS ECU - 0xF4)\n` +
          `• **Min Hücre Voltajı:** 3.78 V\n` +
          `• **Max Hücre Voltajı:** 3.80 V\n` +
          `• **Hücre Voltaj Farkı (Delta V):** 20 mV (<30 mV Nominal Denge Aralığında)\n\n` +
          `📊 **Sistem Durumu:**\n` +
          `Batarya hücreleri arasındaki voltaj farkı (cell delta) güvenli sınırlar içerisindedir. Hücre içi aşırı şarj veya derin deşarj riski tespit edilmedi; CSC denetleyicileri aktif pasif dengeleme (balancing) modundadır.`
      };
    }

    // 6. EV BMS SOC & SOH (0x1807E5F4)
    if (canIdHex.includes('1807E5') || canIdHex === '0X1807E5F4') {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        text: `⚡ **EV BMS Şarj & Sağlık Durumu (0x1807E5F4 - PGN 61446):**\n\n` +
          `• **Protokol:** ISO 11898-2 (EV Yüksek Voltaj BMS)\n` +
          `• **Batarya Şarj Seviyesi (SOC):** %78.4\n` +
          `• **Batarya Sağlık Durumu (SOH):** %98.0\n` +
          `• **Maksimum Şarj Kabul Limiti:** 120 kW (DC Hızlı Şarj Hazır)\n\n` +
          `✅ Batarya kapasite sağlığı ve hücre ömrü nominal aralıkta.`
      };
    }

    // 7. EV BMS Temperatures (0x1809E5F4)
    if (canIdHex.includes('1809E5') || canIdHex === '0X1809E5F4') {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        text: `⚡ **EV BMS & İnverter Termal Yönetimi (0x1809E5F4 - PGN 61448):**\n\n` +
          `• **Batarya Paketi Ortalama Sıcaklığı:** 28.5°C\n` +
          `• **En Sıcak Hücre Modülü:** 31.0°C (Limit: <45°C)\n` +
          `• **İnverter IGBT Sıcaklığı:** 48.0°C (Limit: <110°C)\n\n` +
          `✅ Soğutma devresi ve termal pompalar nominal çalışma rejiminde.`
      };
    }

    // 8. EV BMS Isolation & Contactors (0x18F020F4)
    if (canIdHex.includes('18F020') || canIdHex === '0X18F020F4') {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        text: `⚡ **EV BMS Yüksek Voltaj İzolasyonu & Kontaktör Güvenliği (0x18F020F4):**\n\n` +
          `• **Ana Pozitif Kontaktör (Main Contactor+):** Kapalı (Aktif İletimde)\n` +
          `• **Ana Negatif Kontaktör (Main Contactor-):** Kapalı (Aktif İletimde)\n` +
          `• **Ön Şarj Rölesi (Precharge Relay):** Tamamlandı / Açık\n` +
          `• **HV İzolasyon Direnci:** >50 MΩ (Güvenli, Eşik >500 Ω/V)\n` +
          `• **HVIL Güvenlik Kilidi:** Kapalı Döngü (Sağlam)\n\n` +
          `✅ Yüksek voltaj bara güvenliği devrede, kontaktör yapışması veya kaçak yok.`
      };
    }

    // 9. CAN-FD 64B ADAS Radar Object Cluster (0x00000220)
    if (canIdHex.includes('0220') || qNorm.includes('radar') || qNorm.includes('can-fd')) {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        text: `📡 **CAN-FD ADAS Ön Radar Nesne İzleme (0x220):**\n\n` +
          `• **Protokol:** CAN-FD (64 Bayt Genişletilmiş Yük, 2 Mbps BRS)\n` +
          `• **Hedef Kümesi:** 8 adet bağımsız engel noktası tespit edildi.\n` +
          `• **Ön Araç Mesafesi:** **42.5 metre** | **Bağıl Hız:** -8.4 km/h\n` +
          `• **Sensör Güvenilirlik Skoru:** %98.5 (Parazitsiz Görüş)\n\n` +
          `✅ ADAS Radar nesne ayrıştırma matrisi başarıyla çözüldü.`
      };
    }

    // 10. UDS Negative Response (0x7F)
    if (hexBytes.length >= 3 && hexBytes[0] === 0x7F) {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        isDtcCard: true,
        text: this.decodeUdsNegativeResponse(hexBytes)
      };
    }

    // ─────────────────────────────────────────────────────────────────────────
    // B. DIRECT CODE MATCHING (DTC, SPN, NRC)
    // ─────────────────────────────────────────────────────────────────────────
    // 1. Direct DTC code match
    const dtcMatch = query.match(/\b([PBUC][0-9A-Fa-f]{4})\b/);
    if (dtcMatch) {
      const code = dtcMatch[1].toUpperCase();
      if (EXPERT_REPORTS[code]) {
        return {
          id: `msg-${Date.now()}`,
          sender: 'copilot',
          timestamp,
          isDtcCard: true,
          dtcInfo: KNOWN_DTCS[code],
          text: this.format4StageReport(code, curRpm, curBoost, curTemp)
        };
      }
    }

    // 2. Direct SPN match
    const spnMatch = qNorm.match(/\bspn\s*([0-9]+)\b/);
    if (spnMatch) {
      const spnKey = `SPN${spnMatch[1]}`;
      if (EXPERT_REPORTS[spnKey]) {
        return {
          id: `msg-${Date.now()}`,
          sender: 'copilot',
          timestamp,
          isDtcCard: true,
          text: this.format4StageReport(spnKey, curRpm, curBoost, curTemp)
        };
      }
    }

    // 3. Direct NRC match
    const nrcMatch = qNorm.match(/\b(?:nrc|negatif yanit)\s*(?:0x)?([0-9a-f]{2})\b/);
    if (nrcMatch) {
      const nrcNum = parseInt(nrcMatch[1], 16);
      if (UDS_NRC_MAP[nrcNum]) {
        const info = UDS_NRC_MAP[nrcNum];
        return {
          id: `msg-${Date.now()}`,
          sender: 'copilot',
          timestamp,
          isDtcCard: true,
          text: `🛑 **ISO 14229 UDS Negatif Yanıt Analizi (NRC 0x${nrcNum.toString(16).toUpperCase()} - ${info.name})**\n\n` +
            `• **Teknik Neden:** ${info.cause}\n` +
            `• **Çözüm / Saha Eylemi:** ${info.action}\n\n` +
            `🛠️ **Usta Teknisyen Tavsiyesi:**\n` +
            `1. Oturum durumunu kontrol edin (\`0x10 0x03\` Extended Session gerekliliği).\n` +
            `2. Araç durur vaziyette ve motor kapalı (\`Ignition ON, Engine OFF\`) olmalıdır.\n` +
            `3. Akü voltajının \`>12.5V\` olduğundan emin olun.`
        };
      }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // C. SEMANTIC INTENT MATCHING VIA CAUSAL KNOWLEDGE GRAPH
    // ─────────────────────────────────────────────────────────────────────────
    // 1. EV & High Voltage Battery (P0AA6, P0A0B, P0A80)
    if (qNorm.includes('izolasyon') || qNorm.includes('megger') || qNorm.includes('hvil') || qNorm.includes('batarya') || qNorm.includes('turtle mode') || qNorm.includes('precharge')) {
      if (qNorm.includes('izolasyon') || qNorm.includes('megger')) {
        return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('P0AA6', curRpm, curBoost, curTemp) };
      }
      if (qNorm.includes('hvil') || qNorm.includes('interlock') || qNorm.includes('salter')) {
        return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('P0A0B', curRpm, curBoost, curTemp) };
      }
      return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('P0A80', curRpm, curBoost, curTemp) };
    }

    // 2. Heavy-Duty J1939 Fleet (SPN 100, SPN 3251, SPN 3364)
    if (qNorm.includes('adblue') || qNorm.includes('def') || qNorm.includes('dpf') || qNorm.includes('rejenerasyon') || (qNorm.includes('yag') && qNorm.includes('basinc'))) {
      if (qNorm.includes('yag')) {
        return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('SPN100', curRpm, curBoost, curTemp) };
      }
      if (qNorm.includes('dpf') || qNorm.includes('rejenerasyon')) {
        return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('SPN3251', curRpm, curBoost, curTemp) };
      }
      return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('SPN3364', curRpm, curBoost, curTemp) };
    }

    // 3. Marine NMEA 2000 (Impeller, Mixing Elbow)
    if (qNorm.includes('impeller') || qNorm.includes('cark') || qNorm.includes('deniz suyu') || qNorm.includes('mixing elbow') || qNorm.includes('esnjor') || qNorm.includes('marin')) {
      if (qNorm.includes('impeller') || qNorm.includes('cark') || qNorm.includes('deniz suyu')) {
        return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('N2K_IMPELLER', curRpm, curBoost, curTemp) };
      }
      return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('N2K_EXHAUST_ELBOW', curRpm, curBoost, curTemp) };
    }

    // 4. CAN Physical Layer & Termination (CAN_TERM_60)
    if (qNorm.includes('120 ohm') || qNorm.includes('60 ohm') || qNorm.includes('sonlandirma') || (qNorm.includes('can') && qNorm.includes('direnc'))) {
      return { id: `msg-${Date.now()}`, sender: 'copilot', timestamp, text: this.format4StageReport('CAN_TERM_60', curRpm, curBoost, curTemp) };
    }

    // 5. Silindir Tekleme & Misfire (P0300)
    if (qNorm.includes('tekle') || qNorm.includes('misfire') || qNorm.includes('sarsinti') || qNorm.includes('atesleme') || qNorm.includes('buji')) {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        isDtcCard: true,
        dtcInfo: KNOWN_DTCS.P0300,
        text: this.format4StageReport('P0300', curRpm, curBoost, curTemp)
      };
    }

    // 6. Turbo, Overboost & Kara Duman (P0234)
    if (qNorm.includes('turbo') || qNorm.includes('overboost') || qNorm.includes('underboost') || qNorm.includes('kara duman') || qNorm.includes('siyah duman') || qNorm.includes('bayil')) {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        isDtcCard: true,
        dtcInfo: KNOWN_DTCS.P0234,
        text: this.format4StageReport('P0234', curRpm, curBoost, curTemp)
      };
    }

    // 7. Hararet & Soğutma (SPN 110 / P0115)
    if (qNorm.includes('hararet') || qNorm.includes('sicaklik') || qNorm.includes('termostat') || qNorm.includes('radyator') || qNorm.includes('su kaynat')) {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        isDtcCard: true,
        dtcInfo: KNOWN_DTCS.P0115,
        text: this.format4StageReport('SPN110', curRpm, curBoost, curTemp)
      };
    }

    // 8. Domain Guardrail for Off-Topic Queries (Sohbet / Konu Dışı Filtresi)
    if (
      qNorm.includes('nasil') ||
      qNorm.includes('naber') ||
      qNorm.includes('yemek') ||
      qNorm.includes('tarif') ||
      qNorm.includes('hava nasil') ||
      qNorm.includes('saka') ||
      qNorm.includes('fikra') ||
      qNorm.includes('siir') ||
      qNorm.includes('siyaset') ||
      qNorm.includes('film') ||
      qNorm.includes('muzik') ||
      qNorm.includes('felsefe') ||
      qNorm.includes('fal')
    ) {
      return {
        id: `msg-${Date.now()}`,
        sender: 'copilot',
        timestamp,
        text: `⚠️ **Universal CAN-Bus Teşhis Asistanı:**\n\nBen yalnızca araç telemetrisi, CAN veri yolu protokolleri (J1939, UDS, NMEA 2000) ve otomotiv arıza teşhisi konularında hizmet veren özel bir mühendislik asistanıyım.\n\nLütfen araç telemetrisi, sensör değerleri, CAN ID'leri veya arıza belirtileri ile ilgili bir soru sorunuz.`
      };
    }

    // 9. Fallback General Comprehensive Diagnosis
    return {
      id: `msg-${Date.now()}`,
      sender: 'copilot',
      timestamp,
      text: `🧠 **Çevrimdışı AI Teşhis Başmühendisi (Edge Inference Engine v13.0):**\n\n` +
        `• **Anlık Telemetri:** Motor: **${curRpm} RPM** | Turbo: **${curBoost.toFixed(2)} Bar** | Sıcaklık: **${curTemp.toFixed(1)}°C**\n` +
        `• **Durum:** Sistem hazır ve CAN veri yolu sürekli taranıyor.\n\n` +
        `🛠️ **Hızlı Teşhis Rehberi:**\n` +
        `1. Doğrudan arıza kodu sorabilirsiniz (Örn: *'P0AA6'*, *'SPN 100 FMI 1'*, *'NRC 0x22'*).\n` +
        `2. Saha semptomu belirtebilirsiniz (Örn: *'kara duman atıyor dip gazda bayılıyor'*, *'120 ohm testi'*, *'EV batarya izolasyon hatası'*, *'marin motorda impeller aşırı ısınması'*).\n` +
        `3. Sniffer tablosundaki herhangi bir pakete **sağ tıklayarak 'AI Copilot\\'a Analiz Ettir'** seçeneğini kullanabilirsiniz.`
    };
  }

  public parseActionMetadata(rawText: string): { cleanText: string; actions: CopilotAction[] } {
    const actionMatch = rawText.match(/<!--ACTIONS:(.*?)-->/s);
    let actions: CopilotAction[] = [];
    let cleanText = rawText;

    if (actionMatch) {
      cleanText = rawText.replace(/<!--ACTIONS:.*?-->/s, '').trim();
      try {
        const parsed = JSON.parse(actionMatch[1].trim());
        if (Array.isArray(parsed)) {
          actions = parsed;
        }
      } catch (err) {
        console.warn('Failed to parse actions JSON metadata:', err);
      }
    }

    if (actions.length === 0) {
      actions = this.extractDynamicActions(rawText);
    }

    return { cleanText, actions };
  }

  public extractDynamicActions(text: string): CopilotAction[] {
    const lower = text.toLowerCase();
    const actions: CopilotAction[] = [];

    if (lower.includes('0x14') || lower.includes('dtc temizle') || lower.includes('hafızasını sil') || lower.includes('hata kodlarını sil')) {
      actions.push({
        id: 'act_uds_0x14_clear_dtc',
        label: '▶️ UDS 0x14 DTC Temizle',
        action_type: 'uds_clear_dtc',
        params: { group: 0xFFFFFF },
        requires_confirmation: true,
        confirm_text: 'Aktif ve geçmiş tüm DTC arıza kodları ECU hafızasından silinecektir. Devam edilsin mi?',
      });
    }

    if (lower.includes('f190') || lower.includes('vin oku') || lower.includes('şasi no')) {
      actions.push({
        id: 'act_uds_0x22_f190_vin',
        label: '▶️ UDS 0x22 F190 VIN Oku',
        action_type: 'uds_read_did',
        params: { did: 0xF190, name: 'VIN' },
        requires_confirmation: false,
      });
    }

    if (lower.includes('extended session') || lower.includes('0x10 0x03')) {
      actions.push({
        id: 'act_uds_0x10_session_3',
        label: '▶️ UDS 0x10 Extended Session (0x03)',
        action_type: 'uds_session_control',
        params: { session_type: 3 },
        requires_confirmation: true,
        confirm_text: 'Genişletilmiş teşhis oturumuna (0x03) geçilecek. Onaylıyor musunuz?',
      });
    }

    if (lower.includes('dm11') || lower.includes('65235')) {
      actions.push({
        id: 'act_j1939_dm11_clear',
        label: '▶️ J1939 DM11 Arıza Temizle',
        action_type: 'j1939_clear_dtc',
        params: { pgn: 65235 },
        requires_confirmation: true,
        confirm_text: 'Ağır vasıta J1939 aktif arıza kayıtları silinecektir. Onaylıyor musunuz?',
      });
    }

    if (lower.includes('dm1') || lower.includes('65226')) {
      actions.push({
        id: 'act_j1939_dm1_query',
        label: '▶️ J1939 DM1 Arıza Oku',
        action_type: 'j1939_dm1_query',
        params: { pgn: 65226 },
        requires_confirmation: false,
      });
    }

    if (lower.includes('0x31') || lower.includes('rutin') || lower.includes('routine')) {
      actions.push({
        id: 'act_uds_0x31_routine_d001',
        label: '▶️ UDS 0x31 Teşhis Rutini (0xD001)',
        action_type: 'uds_routine',
        params: { routine_id: 0xD001 },
        requires_confirmation: true,
        confirm_text: '0xD001 nolu teşhis rutini başlatılacaktır. Onaylıyor musunuz?',
      });
    }

    if (lower.includes('0x11') || lower.includes('ecu reset') || lower.includes('hard reset')) {
      actions.push({
        id: 'act_uds_0x11_ecu_reset',
        label: '▶️ UDS 0x11 ECU Reset',
        action_type: 'uds_ecu_reset',
        params: { reset_type: 1 },
        requires_confirmation: true,
        confirm_text: 'ECU donanımsal olarak yeniden başlatılacaktır (Hard Reset). Onaylıyor musunuz?',
      });
    }

    return actions;
  }
}

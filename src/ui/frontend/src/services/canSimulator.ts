import { CANFrame, TelemetryPoint, ScenarioType, DtcInfo, FaultInjectionType } from '../types/can';

export const KNOWN_DTCS: Record<string, DtcInfo> = {
  P0300: {
    code: 'DTC P0300',
    title: 'Rastgele/Çoklu Silindir Ateşleme Hızı Hatası (Random Misfire)',
    ecu: 'ECM (Motor Kontrol Modülü - 0x00)',
    severity: 'critical',
    rootCauses: [
      'Buji aşınması, elektrot aralığı bozulması veya ateşleme bobini izolasyon kaçağı',
      'Enjektör püskürtme deseni bozukluğu veya yakıt rayı basınç düşüklüğü',
      '0x0CF00400 ID tork dalgalanması ve krank mili sensör sinyal gürültüsü'
    ],
    recommendedActions: [
      'Osiloskop ekranında silindir ateşleme dalga boyunu kontrol ediniz.',
      'Enjektör dengeleme oranlarını ve yakıt rayı basınç sensörünü doğrulayınız.',
      'Buji ve ateşleme bobinlerinin primer/sekonder dirençlerini ölçünüz.'
    ],
    oscilloscopeNote: 'Osiloskop ekranında motor devri eğrisinde ani 150-300 RPM mikro-dalgalanmalar ve tork kaybı tespit edildi.'
  },
  P0234: {
    code: 'DTC P0234',
    title: 'Turboşarj / Süperşarj Aşırı Basınç Durumu (Engine Overboost)',
    ecu: 'ECM (Motor Kontrol Modülü - 0x00)',
    severity: 'warning',
    rootCauses: [
      'Wastegate aktüatör mekanik sıkışması veya vakum kaçağı',
      'Boost kontrol selenoid valf arızası (N75 valfi tepkisiz)',
      'MAP / Takviye basınç sensörü kalibrasyon sapması'
    ],
    recommendedActions: [
      'Turbo basıncı 2.4 Bar üzerine çıktığında acil tahliye valfini test ediniz.',
      'Vakum hortumlarında kırılma veya tıkanıklık kontrolü yapınız.',
      'Wastegate kol hareket mesafesini manuel aktüatör ile doğrulayınız.'
    ],
    oscilloscopeNote: 'Turbo basınç eğrisi hedef 1.4 Bar değerini aşarak 2.45 Bar tepe noktasına ulaştı.'
  },
  P0299: {
    code: 'DTC P0299',
    title: 'Turboşarj Düşük Takviye Basıncı (Engine Underboost)',
    ecu: 'ECM (Motor Kontrol Modülü - 0x00)',
    severity: 'warning',
    rootCauses: [
      'Intercooler hava hortumlarında yırtık, kelepçe gevşekliği veya çatlak',
      'Wastegate kapağının tam kapanmaması veya egzoz türbin kanat aşınması',
      'EGR valfinin açık konumda takılı kalması'
    ],
    recommendedActions: [
      'Intercooler ve emiş borularına duman testi (smoke test) uygulayınız.',
      'EGR valfi pozisyonunu ve karbon kurum birikintisini inceleyiniz.'
    ],
    oscilloscopeNote: 'Takviye basıncı hedef değerin altında kalıyor.'
  },
  P0115: {
    code: 'DTC P0115',
    title: 'Motor Soğutma Suyu Sıcaklık Devresi Arızası (ECT Sensor)',
    ecu: 'ECM (Motor Kontrol Modülü - 0x00)',
    severity: 'warning',
    rootCauses: [
      'ECT sensör kablo demetinde şasiye kısa devre veya açık devre',
      'Termostat açık kalması veya soğutma sıvısı seviye düşüklüğü',
      'Sensör NTC direnç karakteristiğinin bozulması'
    ],
    recommendedActions: [
      '0x18FEE100 J1939 ET1 frame sıcaklık baytını kontrol ediniz.',
      'Sensör soketindeki 5V referans ve sinyal voltajını ölçünüz.',
      'Radyatör fan kontrol modülünün tetikleme durumunu izleyiniz.'
    ],
    oscilloscopeNote: 'Soğutma suyu telemetrisi 105°C eşiğini aşarak uyarı bölgesine girdi.'
  },
  P0101: {
    code: 'DTC P0101',
    title: 'Kütle Hava Akış (MAF) Sensörü Devre / Performans Sorunu',
    ecu: 'ECM (Motor Kontrol Modülü - 0x00)',
    severity: 'warning',
    rootCauses: [
      'MAF sensörü ölçüm elemanı üzerinde yağ veya toz kirliliği',
      'Hava filtresi sonrası emiş borularında kaçak (kaçak hava girişi)'
    ],
    recommendedActions: [
      'MAF sensörünü özel sensör temizleme spreyi ile temizleyiniz.',
      'Hava filtresi kutusu ve kelepçelerin sızdırmazlığını kontrol ediniz.'
    ],
    oscilloscopeNote: 'Hava akış debisi motor devrine orantısız kalıyor.'
  },
  P0171: {
    code: 'DTC P0171',
    title: 'Sistem Çok Fakir (Bank 1 - System Too Lean)',
    ecu: 'ECM (Motor Kontrol Modülü - 0x00)',
    severity: 'warning',
    rootCauses: [
      'Vakum hortumu sızıntısı veya emme manifoldu conta kaçağı',
      'Tıkalı yakıt enjektörleri veya düşük yakıt pompa basıncı',
      'Ön Lambda sensörü kirlenmesi'
    ],
    recommendedActions: [
      'Kısa ve uzun vadeli yakıt trim (STFT/LTFT) canlı verilerini izleyiniz.',
      'Yakıt rayı basıncını manometre ile doğrulayınız.'
    ],
    oscilloscopeNote: 'Lambda sensörü sürekli fakir karışım voltajı üretiyor.'
  },
  U0100: {
    code: 'DTC U0100',
    title: 'Motor Kontrol Modülü (ECM/PCM) İletişim Kaybı (CAN Bus-Off)',
    ecu: 'Ağ İletişim Ağ Geçidi (Gateway - 0x00)',
    severity: 'critical',
    rootCauses: [
      'CAN-H veya CAN-L hattında kopukluk, kısa devre veya 120Ω direnç kaybı',
      'ECM ana rölesi veya şasi hattında voltaj düşüşü',
      'Aşırı elektriksel parazit ve bus yükü taşması'
    ],
    recommendedActions: [
      'OBD-II Pin 6 ve Pin 14 arası 60Ω sonlandırma direncini ölçünüz.',
      'ECM ana besleme voltajını ve şasi sürekliliğini kontrol ediniz.'
    ],
    oscilloscopeNote: 'CAN veri yolunda ACK alınamıyor ve hata bayrakları yükseliyor.'
  },
  P0A0B: {
    code: 'DTC P0A0B',
    title: 'Yüksek Voltaj Sistemi İzolasyon Direnci Düşüklüğü (High Voltage Isolation Fault)',
    ecu: 'BMS (Batarya Yönetim Modülü - 0xF4)',
    severity: 'critical',
    rootCauses: [
      'HV Batarya paketi gövde sızdırmazlık contası nem/su sızıntısı',
      'İnvertör DC-Link barası ile şasi arası izolasyon direncinin < 500 kΩ olması',
      'HVDC kontaktör anahtarlama yüzeyinde karbonlaşma veya ark'
    ],
    recommendedActions: [
      'Yüksek voltaj güvenlik prosedürlerine uygun olarak izolasyon test cihazı (Megger) ile 500V DC testi yapın.',
      'HV servis fişini (MSD) çekerek batarya ve invertör taraflarını izole ölçün.'
    ],
    oscilloscopeNote: 'İzolasyon direnci 42 kΩ değerine gerileyerek güvenlik eşiğini ihlal etti.'
  },
  P0A80: {
    code: 'DTC P0A80',
    title: 'Hibrit / Elektrikli Araç Batarya Paketi Hücre Dengesizliği (Cell Imbalance)',
    ecu: 'BMS (Batarya Yönetim Modülü - 0xF4)',
    severity: 'warning',
    rootCauses: [
      'Hücre modülleri arasında >150mV voltaj farkı (Cell Voltage Delta)',
      'Hücre pasif dengeleme (balancing) dirençlerinden birinin açık devre kalması',
      'Termal gradyan farkından kaynaklı hücre yaşlanma hız farkı'
    ],
    recommendedActions: [
      'BMS canlı veri akışında 0x1808E5F4 CAN ID üzerindeki min/maks hücre voltajlarını izleyin.',
      'Hücre dengeleme rutinini (UDS Servis 0x31) başlatın.'
    ],
    oscilloscopeNote: 'Min ve Maks hücre voltajları arasındaki fark 240 mV seviyesine ulaştı.'
  },
  SPN_520201: {
    code: 'SPN 520201',
    title: 'Marin Yakıt Filtresi Su Tespit Uyarısı (Water In Fuel Alarm)',
    ecu: 'Marin Engine Helm Controller (0x00)',
    severity: 'warning',
    rootCauses: [
      'Racor / Ön yakıt filtresi su ayırıcı haznesinin dolması',
      'Kondansasyon kaynaklı ana yakıt tankı dibinde biriken su',
      'Su seviye sensörü korozyonu'
    ],
    recommendedActions: [
      'Ön yakıt filtresi tahliye tapasını açarak su haznesini boşaltınız.',
      'Yakıt tankı dip numunesini kontrol ediniz.'
    ],
    oscilloscopeNote: 'NMEA 2000 PGN 127489 motor dinamik durumunda Water-In-Fuel bayrağı 1 oldu.'
  },
  SPN_1087: {
    code: 'SPN 1087',
    title: 'EBS Elektronik Fren Hava Besleme Basıncı Düşük (Pneumatic Circuit 1 Low)',
    ecu: 'EBS (Elektronik Fren Sistemi - 0x0B)',
    severity: 'critical',
    rootCauses: [
      'Pnömatik fren devresi 1 hava tüpü basıncının < 6.5 Bar olması',
      'Hava kurutucu (APU) tahliye valfi sızıntısı veya kompresör debi kaybı',
      'Fren basınç sensörü kablo temassızlığı'
    ],
    recommendedActions: [
      'Fren hava tüpü manometresini ve EBS canlı basınç telemetrisini doğrulayınız.',
      'Dört yollu koruma valfini ve kaçakları test ediniz.'
    ],
    oscilloscopeNote: 'Fren devresi besleme basıncı 5.2 Bar seviyesine geriledi.'
  },
  C1A00: {
    code: 'DTC C1A00',
    title: 'ADAS Ön Radar / Kamera Görüş Engeli veya Kalibrasyon Hatası',
    ecu: 'ADAS / Radar Modülü (0x20)',
    severity: 'warning',
    rootCauses: [
      'Radar radom kapağında yoğun çamur, kar veya yabancı cisim kaplaması',
      'Ön kamera optik camında buğulanma veya çizik',
      'Sensör montaj braketi eksen kaçıklığı (>1.5 derece)'
    ],
    recommendedActions: [
      'Radar kapağını ve ön cam optik bölgesini temizleyiniz.',
      'ADAS Statik Hedef Panosu ile radar kalibrasyon rutinini başlatınız.'
    ],
    oscilloscopeNote: 'CAN-FD 0x220 ID radar nesne takip verisi hedef mesafesini 0 olarak raporluyor.'
  }
};

interface CanDef {
  idHex: string;
  pgn: number;
  name: string;
  dlc: number;
  channel: 'vcan0' | 'can0' | 'can1';
  dir: 'RX' | 'TX';
  ecuName: string;
  periodMs: number;
  isCanFd?: boolean;
  domain: 'automotive' | 'marine' | 'ev' | 'canfd' | 'network';
}

const EXTENDED_CAN_LIBRARY: CanDef[] = [
  // ── 1. Heavy Duty / J1939 Commercial Fleet ─────────────────────────────────
  { idHex: '0x0CF00400', pgn: 61444, name: 'J1939 EEC1 (Engine Speed & Torque)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'ECM (0x00)', periodMs: 20, domain: 'automotive' },
  { idHex: '0x0CF00300', pgn: 61443, name: 'J1939 EEC2 (Accelerator & Load)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'ECM (0x00)', periodMs: 50, domain: 'automotive' },
  { idHex: '0x18FEE100', pgn: 65249, name: 'J1939 ET1 (Engine Temperature)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'ECM (0x00)', periodMs: 1000, domain: 'automotive' },
  { idHex: '0x18FEF600', pgn: 65270, name: 'J1939 IC1 (Turbo Boost & Intake)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'ECM (0x00)', periodMs: 500, domain: 'automotive' },
  { idHex: '0x18FEF200', pgn: 65266, name: 'J1939 LFE (Fuel Economy & LPH)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'ECM (0x00)', periodMs: 100, domain: 'automotive' },
  { idHex: '0x18FEE000', pgn: 65248, name: 'J1939 TCO1 (Vehicle Speed & Distance)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Tachograph (0xEE)', periodMs: 50, domain: 'automotive' },
  { idHex: '0x18FECA00', pgn: 65226, name: 'J1939 DM1 (Active DTC Trouble Codes)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'ECM (0x00)', periodMs: 1000, domain: 'automotive' },
  { idHex: '0x18FF0501', pgn: 65281, name: 'Proprietary Telemetry (Counter+CRC)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Body Gateway (0x01)', periodMs: 20, domain: 'automotive' },
  { idHex: '0x0C000003', pgn: 0,     name: 'J1939 ETC1 (Transmission Gear)', dlc: 8, channel: 'can0', dir: 'TX', ecuName: 'TCM (0x03)', periodMs: 10, domain: 'automotive' },
  { idHex: '0x18F0010B', pgn: 61441, name: 'J1939 EBC1 (Brake System ABS/EBS)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'EBS (0x0B)', periodMs: 20, domain: 'automotive' },
  { idHex: '0x18F00010', pgn: 61440, name: 'J1939 ERC1 (Electronic Retarder)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Retarder (0x10)', periodMs: 50, domain: 'automotive' },
  { idHex: '0x18EEFF00', pgn: 60928, name: 'J1939 Address Claimed (ECM Name)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'ECM (0x00)', periodMs: 2000, domain: 'automotive' },

  // ── 2. Electric Vehicle / High Voltage BMS ────────────────────────────────
  { idHex: '0x1806E5F4', pgn: 61445, name: 'BMS Pack Voltage & Current (HV DC)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'BMS (0xF4)', periodMs: 20, domain: 'ev' },
  { idHex: '0x1807E5F4', pgn: 61446, name: 'BMS SOC & State of Health (SOH %)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'BMS (0xF4)', periodMs: 100, domain: 'ev' },
  { idHex: '0x1808E5F4', pgn: 61447, name: 'BMS Cell Voltage Min/Max/Delta', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'BMS (0xF4)', periodMs: 100, domain: 'ev' },
  { idHex: '0x1809E5F4', pgn: 61448, name: 'BMS Battery & Inverter Temperatures', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'BMS (0xF4)', periodMs: 500, domain: 'ev' },
  { idHex: '0x18F020F4', pgn: 61472, name: 'BMS Isolation & Contactor Safety', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'BMS (0xF4)', periodMs: 200, domain: 'ev' },
  { idHex: '0x0C08A0EF', pgn: 61449, name: 'EV Inverter Motor RPM & Torque', dlc: 8, channel: 'can0', dir: 'TX', ecuName: 'Inverter (0xEF)', periodMs: 10, domain: 'ev' },

  // ── 3. NMEA 2000 Marine Helm & Vessel ─────────────────────────────────────
  { idHex: '0x19F20000', pgn: 127488, name: 'N2K Engine Rapid (Port RPM/Boost)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Port Helm (0x00)', periodMs: 100, domain: 'marine' },
  { idHex: '0x19F20001', pgn: 127488, name: 'N2K Engine Rapid (Starboard RPM)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Stbd Helm (0x01)', periodMs: 100, domain: 'marine' },
  { idHex: '0x19F20100', pgn: 127489, name: 'N2K Engine Dynamic (Temp/Oil/LPH)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Port Helm (0x00)', periodMs: 500, domain: 'marine' },
  { idHex: '0x19F50300', pgn: 128267, name: 'N2K Water Depth (Echo Sounder)', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Sonar (0x22)', periodMs: 1000, domain: 'marine' },
  { idHex: '0x19F50200', pgn: 128259, name: 'N2K Speed Over Ground & Water Spd', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'GPS/GNSS (0x1C)', periodMs: 250, domain: 'marine' },
  { idHex: '0x19F11200', pgn: 127245, name: 'N2K Rudder Angle & Autopilot', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Autopilot (0x28)', periodMs: 100, domain: 'marine' },
  { idHex: '0x19F30100', pgn: 130306, name: 'N2K Wind Speed & Angle Sensor', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'Anemometer (0x35)', periodMs: 500, domain: 'marine' },

  // ── 4. CAN-FD & ADAS Radar / Vision ───────────────────────────────────────
  { idHex: '0x00000220', pgn: 544,    name: 'CAN-FD 64B Radar Object Tracking', dlc: 64, channel: 'vcan0', dir: 'RX', ecuName: 'Radar (0x20)', periodMs: 20, isCanFd: true, domain: 'canfd' },
  { idHex: '0x00000240', pgn: 576,    name: 'CAN-FD 32B Camera Lane Departure', dlc: 32, channel: 'vcan0', dir: 'RX', ecuName: 'Camera (0x21)', periodMs: 33, isCanFd: true, domain: 'canfd' },
  { idHex: '0x000001A0', pgn: 416,    name: 'CAN-FD 16B Steering Angle + CRC8', dlc: 16, channel: 'vcan0', dir: 'RX', ecuName: 'SAS (0x25)', periodMs: 10, isCanFd: true, domain: 'canfd' },

  // ── 5. UDS Diagnostic Response (Mock ECU) ─────────────────────────────────
  { idHex: '0x000007E8', pgn: 2024,   name: 'ISO 14229 UDS ECM Diagnostic Rx', dlc: 8, channel: 'vcan0', dir: 'RX', ecuName: 'ECM UDS (0x7E8)', periodMs: 0, domain: 'network' }
];

const COLOR_PALETTES: ('blue' | 'indigo' | 'emerald' | 'slate' | 'amber' | 'rose')[][] = [
  ['blue', 'indigo', 'blue', 'indigo', 'emerald', 'slate', 'slate', 'slate'],
  ['blue', 'blue', 'emerald', 'emerald', 'indigo', 'slate', 'slate', 'slate'],
  ['indigo', 'slate', 'emerald', 'blue', 'slate', 'emerald', 'indigo', 'blue'],
  ['slate', 'emerald', 'indigo', 'emerald', 'blue', 'blue', 'slate', 'slate'],
  ['amber', 'amber', 'emerald', 'indigo', 'blue', 'slate', 'slate', 'slate'],
];

export class CANSimulatorEngine {
  private isRunning: boolean = false;
  private scenario: ScenarioType = 'nominal';
  private frameRateTarget: number = 25;
  private speedMultiplier: number = 1.0;
  private totalPackets: number = 15523;
  private totalDisplayed: number = 444;
  private errorFramesCount: number = 0;
  private baseStartTime: number = Date.now() - 74000;
  
  // Physical Engine & Telemetry State
  private currentRpm: number = 2381;
  private currentTurboBoost: number = 1.66;
  private currentCoolantTemp: number = 85;
  private currentOilPressure: number = 4.2;
  private currentVehicleSpeed: number = 82.5;
  private currentGear: number = 5;
  private currentTorqueNm: number = 820;
  private currentPowerKw: number = 205;
  private currentPowerHp: number = 275;
  private currentFuelRateLph: number = 24.5;
  private currentFuelL100km: number = 29.7;
  
  // EV / BMS State
  private currentPackVoltage: number = 398.4;
  private currentPackCurrent: number = 42.5;
  private currentBatterySoc: number = 78.4;
  private currentCellMinMaxDelta: number = 0.015;
  private currentInverterTemp: number = 48.0;

  // Marine N2K State
  private currentSogKnots: number = 18.6;
  private currentDepthMeters: number = 24.8;
  private currentRudderDeg: number = -2.5;
  private currentPropellerSlipPct: number = 11.2;

  // Simulation Controls & Stimulus
  private busLoad: number = 0;
  private stimulusLevelPercent: number = 0;
  private rollingCounter: number = 0;
  private isSensorFrozen: boolean = false;
  private isIntermittentWiringFault: boolean = false;
  private wiringFaultCountdown: number = 0;
  private activeCustomDtc: string | null = null;

  private listeners: {
    onNewFrame?: (frame: CANFrame) => void;
    onNewFrameBatch?: (frames: CANFrame[]) => void;  // F-35: batched emission
    onTelemetryUpdate?: (telemetry: TelemetryPoint) => void;
    onStatsUpdate?: (stats: { totalPackets: number; busLoad: number; errorCount: number; frameRate: number }) => void;
  } = {};

  private timerId: number | null = null;
  private telemetryTimerId: number | null = null;
  private statsTimerId: number | null = null;
  private lastFrameSec: number = 74.0380;
  private framesThisSecond: number = 0;
  private bitsAccumulatedThisSecond: number = 0;
  private currentActualFrameRate: number = 0;

  constructor() {
    this.startSimulation();
  }

  public setStimulusLevel(pct: number) {
    this.stimulusLevelPercent = Math.max(0, Math.min(100, pct));
  }

  public setSpeedMultiplier(mult: number) {
    this.speedMultiplier = Math.max(0.25, Math.min(10.0, mult));
  }

  public setFrameRateTarget(fps: number) {
    this.frameRateTarget = Math.max(5, Math.min(250, fps));
    this.restartFrameTimer();
  }

  public setScenario(scenario: ScenarioType) {
    this.scenario = scenario;
    this.activeCustomDtc = null;
    this.isSensorFrozen = false;
    this.isIntermittentWiringFault = false;

    if (scenario === 'bus_surge') {
      this.busLoad = 82;
    } else if (scenario === 'overboost') {
      this.currentTurboBoost = 2.45;
      this.currentRpm = 2650;
    } else if (scenario === 'overheat') {
      this.currentCoolantTemp = 109;
    } else if (scenario === 'ev_bms_telemetry') {
      this.currentPackVoltage = 396.2;
      this.currentPackCurrent = 68.0;
      this.currentBatterySoc = 74.2;
      this.currentCellMinMaxDelta = 0.022;
      this.busLoad = 34;
    } else if (scenario === 'marine_vessel_n2k') {
      this.currentSogKnots = 22.4;
      this.currentDepthMeters = 18.5;
      this.currentRpm = 2250;
      this.busLoad = 38;
    } else if (scenario === 'can_fd_adas_vision') {
      this.busLoad = 55;
    } else if (scenario === 'intermittent_wiring_fault') {
      this.isIntermittentWiringFault = true;
      this.wiringFaultCountdown = 5;
    } else if (scenario === 'nominal') {
      this.busLoad = 35;
      this.currentTurboBoost = 1.66;
      this.currentCoolantTemp = 85;
      this.currentRpm = 2381;
      this.currentPackVoltage = 398.4;
      this.currentBatterySoc = 78.4;
    }
  }

  public getScenario(): ScenarioType {
    return this.scenario;
  }

  public pause() {
    this.isRunning = false;
  }

  public resume() {
    this.isRunning = true;
  }

  public toggleRunning(): boolean {
    this.isRunning = !this.isRunning;
    return this.isRunning;
  }

  public getIsRunning(): boolean {
    return this.isRunning;
  }

  public emergencyStop() {
    this.isRunning = false;
    this.busLoad = 0;
    this.currentRpm = 0;
    this.currentTurboBoost = 0.0;
    this.currentPackCurrent = 0.0;
    this.currentVehicleSpeed = 0.0;
  }

  // ── Fault Injection APIs ──────────────────────────────────────────────────
  public injectFault(type: FaultInjectionType) {
    // REVIEW (LIVE guard): injectFault used to bypass the isLiveMode gate
    // that startSimulation enforces — a synthetic error frame landed in
    // the LIVE sniffer buffer via the unconditionally-subscribed frame
    // listener. All synthetic fault effects stop in LIVE mode.
    if (this.isLiveMode()) {
      return;
    }
    if (type === 'error_frame') {
      this.errorFramesCount += 5;
      this.generateExplicitErrorFrame();
    } else if (type === 'dtc_fault') {
      if (this.scenario === 'ev_bms_telemetry') {
        this.activeCustomDtc = 'P0A0B';
        this.currentCellMinMaxDelta = 0.240;
      } else if (this.scenario === 'marine_vessel_n2k') {
        this.activeCustomDtc = 'SPN_520201';
      } else {
        this.activeCustomDtc = 'P0300';
      }
    } else if (type === 'sensor_freeze') {
      this.isSensorFrozen = !this.isSensorFrozen;
    } else if (type === 'babbling_surge') {
      this.busLoad = 94;
      for (let i = 0; i < 8; i++) {
        setTimeout(() => this.generateNextFrame(), i * 4);
      }
    } else if (type === 'wiring_dropout') {
      this.isIntermittentWiringFault = true;
      this.wiringFaultCountdown = 8;
      this.errorFramesCount += 12;
    }
  }

  public subscribe(callbacks: typeof this.listeners) {
    this.listeners = { ...this.listeners, ...callbacks };
  }

  /**
   * F-29: single simulator module with LIVE/DEMO split. When the native
   * pywebview bridge is present the backend already streams real CAN frames
   * and telemetry (F-28) — synthetic generation is suppressed so the two
   * generators never collide. Browser-only runs fall back to DEMO mode.
   */
  public isLiveMode(): boolean {
    return typeof window !== 'undefined' && !!window.pywebview;
  }

  private restartFrameTimer() {
    if (this.timerId) clearInterval(this.timerId);
    if (this.isLiveMode()) return;
    // F-35: batch IPC — generate 5 frames per 200ms tick instead of one
    // frame per interval. At the same average rate the number of timer (and
    // potential bridge) invocations drops 5x.
    // REVIEW (frame budget math): framesPerTick used
    // BATCH_SIZE*BATCH_INTERVAL_MS*fps/1000 = fps*framesPerTick — every
    // tick emitted the WHOLE per-second budget, i.e. 5x the target fps
    // (25 fps target -> 125 frames/s). Per-tick budget is simply
    // fps * interval / 1000 (BATCH_SIZE only bounds the split across
    // ticks for very high fps targets).
    const BATCH_INTERVAL_MS = 200;
    const maxBatch = 50;
    const framesPerTick = Math.min(
      maxBatch,
      Math.max(1, Math.round((this.frameRateTarget * this.speedMultiplier * BATCH_INTERVAL_MS) / 1000))
    );
    this.timerId = window.setInterval(() => {
      if (!this.isRunning) return;
      const batch: CANFrame[] = [];
      for (let i = 0; i < framesPerTick; i++) {
        batch.push(this.generateNextFrame());
      }
      if (batch.length > 0 && this.listeners.onNewFrameBatch) {
        this.listeners.onNewFrameBatch(batch);
      }
    }, BATCH_INTERVAL_MS);
  }

  private startSimulation() {
    if (this.isLiveMode()) {
      // LIVE: real data arrives via window.onNewCanFrame / onTelemetryTick /
      // onStatsTick — never start synthetic timers next to the real stream.
      return;
    }
    this.restartFrameTimer();

    this.telemetryTimerId = window.setInterval(() => {
      if (!this.isRunning) return;
      this.updateTelemetry();
    }, 100);

    this.statsTimerId = window.setInterval(() => {
      this.currentActualFrameRate = this.isRunning ? this.framesThisSecond : 0;
      this.framesThisSecond = 0;

      // Realistic physical bus load estimation from accumulated bit lengths
      const nominalBaud = 250000;
      const physicalLoad = Math.round((this.bitsAccumulatedThisSecond / (nominalBaud * 0.45)) * 100);
      this.bitsAccumulatedThisSecond = 0;

      if (this.scenario === 'bus_surge') {
        this.busLoad = Math.min(98, Math.max(75, physicalLoad + 62 + Math.random() * 12));
      } else if (this.scenario === 'can_fd_adas_vision') {
        this.busLoad = Math.min(78, Math.max(45, physicalLoad + 35));
      } else if (this.isIntermittentWiringFault && this.wiringFaultCountdown > 0) {
        this.busLoad = Math.min(95, Math.max(68, physicalLoad + 50));
      } else {
        this.busLoad = this.isRunning ? Math.min(65, Math.max(22, physicalLoad + 25)) : 0;
      }

      if (this.listeners.onStatsUpdate) {
        this.listeners.onStatsUpdate({
          totalPackets: this.totalPackets,
          busLoad: Math.round(this.busLoad),
          errorCount: this.errorFramesCount,
          frameRate: this.currentActualFrameRate
        });
      }
    }, 1000);
  }

  private updateTelemetry() {
    if (this.isSensorFrozen) return;

    const t = (Date.now() / 1000) * this.speedMultiplier;
    const stimFactor = this.stimulusLevelPercent / 100.0;

    let targetRpm = 1800 + stimFactor * 1400 + Math.sin(t * 0.8) * 80 + Math.cos(t * 1.5) * 30;
    let targetTurbo = 1.2 + stimFactor * 0.8 + Math.sin(t * 0.5) * 0.12;
    let targetSpeed = 40 + stimFactor * 75 + Math.sin(t * 0.3) * 5;

    // Domain / Scenario Specific Physics Modulation
    if (this.scenario === 'misfire_p0300' || this.activeCustomDtc === 'P0300') {
      if (Math.random() > 0.6) {
        targetRpm -= 220 + Math.random() * 160;
      }
      this.currentTorqueNm = Math.max(300, 750 - 240);
    } else if (this.scenario === 'overboost') {
      targetTurbo = 2.42 + Math.sin(t * 1.2) * 0.22;
      targetRpm += 300;
    } else if (this.scenario === 'overheat') {
      this.currentCoolantTemp = Math.min(118, this.currentCoolantTemp + 0.04 * this.speedMultiplier);
    } else if (this.scenario === 'ev_bms_telemetry') {
      this.currentPackCurrent = stimFactor > 0.05 
        ? Math.round(20 + stimFactor * 140 + Math.sin(t * 2.0) * 12)
        : Math.round(-35 + Math.sin(t) * 5);
      this.currentPackVoltage = Math.max(340, parseFloat((398.0 - (this.currentPackCurrent * 0.08)).toFixed(1)));
      this.currentBatterySoc = Math.max(10, parseFloat((this.currentBatterySoc - 0.005 * (this.currentPackCurrent > 0 ? 1 : -0.5)).toFixed(2)));
      if (this.activeCustomDtc === 'P0A0B') {
        this.currentCellMinMaxDelta = 0.240;
      } else {
        this.currentCellMinMaxDelta = 0.015 + Math.sin(t * 0.2) * 0.006;
      }
    } else if (this.scenario === 'marine_vessel_n2k') {
      this.currentSogKnots = parseFloat((12.0 + stimFactor * 14.0 + Math.sin(t * 0.4) * 1.5).toFixed(1));
      this.currentDepthMeters = parseFloat((22.0 + Math.sin(t * 0.1) * 8.0).toFixed(1));
      this.currentRudderDeg = parseFloat((Math.sin(t * 0.25) * 15.0).toFixed(1));
      const theoreticalKnots = (targetRpm / 2.0 * 21.0) / 1215.22;
      this.currentPropellerSlipPct = theoreticalKnots > 0 
        ? Math.max(4, Math.min(35, parseFloat(((1.0 - (this.currentSogKnots / theoreticalKnots)) * 100).toFixed(1))))
        : 12.0;
    }

    if (this.isIntermittentWiringFault && this.wiringFaultCountdown > 0) {
      this.wiringFaultCountdown--;
      if (Math.random() > 0.4) {
        this.errorFramesCount++;
      }
    }

    this.currentRpm = Math.max(0, Math.round(targetRpm));
    this.currentTurboBoost = Math.max(0, parseFloat(targetTurbo.toFixed(2)));
    this.currentVehicleSpeed = Math.max(0, parseFloat(targetSpeed.toFixed(1)));

    // Virtual Calculated Channels
    const actualTorquePct = Math.min(100, Math.max(10, 30 + Math.round(stimFactor * 65)));
    this.currentTorqueNm = parseFloat(((actualTorquePct / 100.0) * 1000.0).toFixed(1));
    this.currentPowerKw = parseFloat(((this.currentRpm * this.currentTorqueNm) / 9549.3).toFixed(1));
    this.currentPowerHp = parseFloat((this.currentPowerKw * 1.34102).toFixed(1));
    this.currentFuelRateLph = parseFloat((4.5 + (this.currentPowerKw * 0.18)).toFixed(1));
    this.currentFuelL100km = this.currentVehicleSpeed > 5 
      ? parseFloat(((this.currentFuelRateLph * 100.0) / this.currentVehicleSpeed).toFixed(1))
      : 0;

    const nowSec = (Date.now() - this.baseStartTime) / 1000;
    const point: TelemetryPoint = {
      timeSec: nowSec,
      timeFormatted: `${nowSec.toFixed(2)}s`,
      rpm: this.currentRpm,
      turboBoostBar: this.currentTurboBoost,
      coolantTempC: this.currentCoolantTemp,
      oilPressureBar: 4.2 + Math.sin(t) * 0.2,
      busLoadPercent: this.busLoad,
      errorCount: this.errorFramesCount,

      // EV
      batterySocPercent: this.currentBatterySoc,
      packVoltageV: this.currentPackVoltage,
      packCurrentA: this.currentPackCurrent,
      cellMinMaxDeltaV: this.currentCellMinMaxDelta,
      inverterTempC: this.currentInverterTemp,

      // Marine
      sogKnots: this.currentSogKnots,
      depthMeters: this.currentDepthMeters,
      rudderDeg: this.currentRudderDeg,
      propellerSlipPct: this.currentPropellerSlipPct,

      // Derived Virtual
      torqueNm: this.currentTorqueNm,
      powerKw: this.currentPowerKw,
      powerHp: this.currentPowerHp,
      instantFuelRateLph: this.currentFuelRateLph,
      instantFuelEconomyL100km: this.currentFuelL100km,
      gearPosition: this.currentVehicleSpeed > 60 ? 6 : this.currentVehicleSpeed > 40 ? 4 : 2
    };

    if (this.listeners.onTelemetryUpdate) {
      this.listeners.onTelemetryUpdate(point);
    }
  }

  private generateExplicitErrorFrame(): CANFrame {
    this.totalPackets++;
    this.totalDisplayed++;
    this.framesThisSecond++;
    this.lastFrameSec += 0.015;

    const frame: CANFrame = {
      id: `err-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
      timeSec: this.lastFrameSec,
      timeFormatted: `${this.lastFrameSec.toFixed(4)}s`,
      channel: 'vcan0',
      canIdHex: '0x00000000',
      canIdDec: 0,
      frameType: 'ERR',
      dir: 'RX',
      dlc: 0,
      dataHex: ['CRC_ERR', 'STUFF_ERR', 'BIT0_ERR', 'ACK_DELIM'],
      ascii: '[ERR]',
      isErrorFrame: true,
      signalName: 'CAN Bus Physical Error Frame (CRC/Stuffing Violation)',
      ecuName: 'CAN Controller (Bus-Off)'
    };

    // REVIEW (single delivery path): explicit error frames also flow through
    // the batch hook only — a bare onNewFrame emit here duplicated them
    // into any batch that followed.
    if (this.listeners.onNewFrameBatch) {
      this.listeners.onNewFrameBatch([frame]);
    }
    return frame;
  }

  private generateNextFrame(): CANFrame {
    this.totalPackets++;
    this.totalDisplayed++;
    this.framesThisSecond++;

    this.lastFrameSec += (0.0120 + Math.random() * 0.0280) / this.speedMultiplier;
    const timeFormatted = `${this.lastFrameSec.toFixed(4)}s`;

    // Filter library by active domain
    let activeLibrary = EXTENDED_CAN_LIBRARY;
    if (this.scenario === 'ev_bms_telemetry') {
      activeLibrary = EXTENDED_CAN_LIBRARY.filter(d => d.domain === 'ev' || d.idHex === '0x18FF0501');
    } else if (this.scenario === 'marine_vessel_n2k') {
      activeLibrary = EXTENDED_CAN_LIBRARY.filter(d => d.domain === 'marine' || d.idHex === '0x18FF0501');
    } else if (this.scenario === 'can_fd_adas_vision') {
      activeLibrary = EXTENDED_CAN_LIBRARY.filter(d => d.domain === 'canfd' || d.idHex === '0x0CF00400');
    } else if (this.scenario === 'nominal' || this.scenario === 'bus_surge') {
      activeLibrary = EXTENDED_CAN_LIBRARY.filter(d => d.idHex !== '0x18FECA00' && d.domain !== 'canfd');
    }

    const defIndex = Math.floor(Math.random() * activeLibrary.length);
    const def = activeLibrary[defIndex];

    const isExtended = def.idHex.length > 6 || def.idHex.startsWith('0x18') || def.idHex.startsWith('0x0C') || def.idHex.startsWith('0x19');
    const baseBits = isExtended ? 67 : 47;
    const frameBits = Math.round((baseBits + (def.dlc * 8)) * 1.15);
    this.bitsAccumulatedThisSecond += frameBits;

    const dataHex: string[] = [];
    const rawData: number[] = [];
    let ascii = '';

    for (let i = 0; i < def.dlc; i++) {
      let b = Math.floor(Math.random() * 256);

      // J1939 EEC1 Engine Speed & Torque (0x0CF00400)
      if (def.idHex === '0x0CF00400') {
        if (i === 1) b = Math.min(250, 125 + Math.round(this.stimulusLevelPercent * 1.25));
        else if (i === 2) b = Math.min(250, 125 + Math.round(this.stimulusLevelPercent * 1.2));
        else if (i === 3) b = Math.floor(this.currentRpm / 256);
        else if (i === 4) b = this.currentRpm % 256;
        else b = 0xFF;
      }
      // J1939 IC1 Turbo Boost (0x18FEF600)
      else if (def.idHex === '0x18FEF600') {
        if (i === 1) b = Math.min(255, Math.floor(this.currentTurboBoost * 50));
        else b = 0xFF;
      }
      // J1939 ET1 Temperature (0x18FEE100)
      else if (def.idHex === '0x18FEE100') {
        if (i === 0) b = Math.min(250, Math.floor(this.currentCoolantTemp + 40));
        else if (i === 1) b = Math.min(250, Math.floor(this.currentOilPressure * 25));
        else b = 0xFF;
      }
      // EV BMS Pack Voltage & Current (0x1806E5F4)
      else if (def.idHex === '0x1806E5F4') {
        const rawVolt = Math.round(this.currentPackVoltage * 10);
        const rawAmp = Math.round((this.currentPackCurrent + 500) * 10);
        if (i === 0) b = (rawVolt >> 8) & 0xFF;
        else if (i === 1) b = rawVolt & 0xFF;
        else if (i === 2) b = (rawAmp >> 8) & 0xFF;
        else if (i === 3) b = rawAmp & 0xFF;
        else b = 0x00;
      }
      // EV BMS SOC (0x1807E5F4)
      else if (def.idHex === '0x1807E5F4') {
        if (i === 0) b = Math.round(this.currentBatterySoc * 2);
        else if (i === 1) b = 196;
        else b = 0xFF;
      }
      // Marine N2K Rapid (0x19F20000)
      else if (def.idHex === '0x19F20000') {
        const rawRpm = Math.round(this.currentRpm * 4);
        const rawBoost = Math.round(this.currentTurboBoost * 1000);
        if (i === 0) b = 0x00;
        else if (i === 1) b = rawRpm & 0xFF;
        else if (i === 2) b = (rawRpm >> 8) & 0xFF;
        else if (i === 3) b = rawBoost & 0xFF;
        else if (i === 4) b = (rawBoost >> 8) & 0xFF;
      }
      // J1939 DM1 Diagnostic Faults (0x18FECA00)
      else if (def.idHex === '0x18FECA00') {
        if (this.scenario === 'misfire_p0300' || this.activeCustomDtc === 'P0300') {
          const dtcBytes = [0x04, 0xFF, 0x8B, 0x02, 0x32, 0x0E, 0xFF, 0xFF];
          b = dtcBytes[i] ?? 0xFF;
        } else if (this.scenario === 'overboost') {
          const dtcBytes = [0x04, 0xFF, 0x66, 0x00, 0x20, 0x07, 0xFF, 0xFF];
          b = dtcBytes[i] ?? 0xFF;
        } else if (this.scenario === 'overheat') {
          const dtcBytes = [0x14, 0xFF, 0x6E, 0x00, 0x20, 0x05, 0xFF, 0xFF];
          b = dtcBytes[i] ?? 0xFF;
        } else {
          b = (i === 0) ? 0x00 : 0xFF;
        }
      }
      // Proprietary / Signal Discovery Frame (0x18FF0501)
      else if (def.idHex === '0x18FF0501') {
        if (i === 0) b = Math.floor(Math.random() * 256);
        else if (i === 1) b = Math.round(this.stimulusLevelPercent * 2.5) & 0xFF;
        else if (i === 2) b = (this.rollingCounter++) & 0x0F;
        else if (i === 7) b = (rawData.reduce((acc, val) => acc ^ val, 0) ^ 0xAA) & 0xFF;
        else b = 0xFF;
      }

      rawData.push(b);
      dataHex.push(b.toString(16).toUpperCase().padStart(2, '0'));
      ascii += (b >= 32 && b <= 126) ? String.fromCharCode(b) : '.';
    }

    const isError = (this.scenario === 'bus_surge' && Math.random() > 0.8) || 
                    (this.isIntermittentWiringFault && Math.random() > 0.7);
    if (isError) {
      this.errorFramesCount++;
    }

    const palette = COLOR_PALETTES[Math.floor(Math.random() * COLOR_PALETTES.length)];

    const frame: CANFrame = {
      id: `frame-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`,
      timeSec: this.lastFrameSec,
      timeFormatted,
      channel: def.channel,
      canIdHex: def.idHex,
      canIdDec: parseInt(def.idHex, 16),
      pgn: def.pgn,
      frameType: isError ? 'ERR' : def.isCanFd ? 'FD' : isExtended ? 'Ext' : 'Std',
      dir: def.dir,
      dlc: def.dlc,
      dataHex,
      ascii,
      isErrorFrame: isError,
      colorPalette: palette,
      signalName: def.name,
      ecuName: def.ecuName,
      isCanFd: def.isCanFd
    };

    // REVIEW (single delivery path): generateNextFrame used to fire
    // onNewFrame per frame while restartFrameTimer ALSO emitted the whole
    // batch via onNewFrameBatch — every frame was delivered twice. The
    // batch hook is the one and only delivery path (F-35).

    return frame;
  }

  public destroy() {
    if (this.timerId) clearInterval(this.timerId);
    if (this.telemetryTimerId) clearInterval(this.telemetryTimerId);
    if (this.statsTimerId) clearInterval(this.statsTimerId);
  }

  public static getInitialFrames(): CANFrame[] {
    const rawSeeds = [
      { time: '74.0380s', ch: 'vcan0', id: '0x19F20000', type: 'Ext', dir: 'RX', dlc: 8, hex: ['1E', '11', '0D', '4B', '54', 'A4', '0F', 'D6'], ascii: '...KT...', ecu: 'Port Helm (0x00)', sig: 'N2K Engine Rapid' },
      { time: '74.1500s', ch: 'can0',  id: '0x0C000003', type: 'Ext', dir: 'TX', dlc: 8, hex: ['0F', 'A2', '5F', '1C', '31', 'FD', 'E3', 'CD'], ascii: '.._.1...', ecu: 'TCM (0x03)', sig: 'J1939 Transmission' },
      { time: '74.2350s', ch: 'vcan0', id: '0x1806E5F4', type: 'Ext', dir: 'RX', dlc: 8, hex: ['0F', '90', '02', '14', '00', '00', '00', '00'], ascii: '........', ecu: 'BMS (0xF4)', sig: 'BMS Pack Voltage (398.4V)' },
      { time: '74.3160s', ch: 'vcan0', id: '0x18FEF200', type: 'Ext', dir: 'RX', dlc: 8, hex: ['E5', '2A', 'D5', '4E', 'A6', '9E', 'CB', 'DB'], ascii: '.*.N....', ecu: 'ECM (0x00)', sig: 'J1939 Fuel Economy' },
      { time: '74.4060s', ch: 'vcan0', id: '0x0CF00400', type: 'Ext', dir: 'RX', dlc: 8, hex: ['F0', '7D', '7D', '25', '4B', 'FF', 'FF', 'FF'], ascii: '.}...K..', ecu: 'ECM (0x00)', sig: 'J1939 EEC1 Engine RPM' },
      { time: '74.5230s', ch: 'vcan0', id: '0x18FEE100', type: 'Ext', dir: 'RX', dlc: 8, hex: ['7D', '69', '00', '00', '00', '00', '00', '00'], ascii: '}i......', ecu: 'ECM (0x00)', sig: 'J1939 ET1 Engine Temp' },
    ];

    return rawSeeds.map((s, idx) => ({
      id: `seed-${idx}`,
      timeSec: parseFloat(s.time.replace('s', '')),
      timeFormatted: s.time,
      channel: s.ch,
      canIdHex: s.id,
      canIdDec: parseInt(s.id, 16),
      frameType: 'Ext',
      dir: s.dir as any,
      dlc: 8,
      dataHex: s.hex,
      ascii: s.ascii,
      ecuName: s.ecu,
      signalName: s.sig,
      colorPalette: COLOR_PALETTES[idx % COLOR_PALETTES.length]
    }));
  }
}


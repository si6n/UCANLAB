import React, { useState } from 'react';
import { 
 X, 
 Play, 
 Pause, 
 Activity, 
 Zap, 
 Cpu, 
 Radio, 
 Anchor, 
 Car, 
 AlertTriangle, 
 CheckCircle2, 
 Layers, 
 Gauge, 
 Sparkles, 
 Check, 
 ChevronRight,
 ShieldAlert,
 Flame,
 BatteryCharging,
 Truck
} from 'lucide-react';
import { ScenarioType } from '../../types/can';

interface SimulatorStudioModalProps {
 isOpen: boolean;
 onClose: () => void;
 isSimulating: boolean;
 activeScenario: ScenarioType;
 simulationSpeed?: number;
 channel?: string;
 baudRate?: string;
 totalPackets?: number;
 busLoad?: number;
 onToggleSimulator: () => void;
 onSelectScenario: (scenario: ScenarioType) => void;
 onChangeSpeed?: (speed: number) => void;
 onEstop?: () => void;
}

type CategoryFilter = 'all' | 'automotive' | 'ev' | 'marine' | 'canfd' | 'stress';

export const SimulatorStudioModal: React.FC<SimulatorStudioModalProps> = ({
 isOpen,
 onClose,
 isSimulating,
 activeScenario,
 simulationSpeed = 1.0,
 channel = 'vcan0',
 baudRate = '250 kbps',
 totalPackets = 0,
 busLoad = 0,
 onToggleSimulator,
 onSelectScenario,
 onChangeSpeed,
 onEstop
}) => {
 const [selectedCategory, setSelectedCategory] = useState<CategoryFilter>('all');

 if (!isOpen) return null;

 const scenarioList = [
 {
 key: 'nominal' as ScenarioType,
 category: 'automotive' as CategoryFilter,
 categoryLabel: 'Otomotiv & Standart Teleometri',
 title: 'Nominal Çalışma & Standart Telemetri',
 subTitle: 'Motor RPM, Turbo Basıncı, Soğutma Suyu & Yağ Basıncı',
 protocol: 'ISO 11898-2',
 frameFormat: '11-Bit Std (0x100 - 0x300)',
 baud: '250 kbps',
 period: '10ms - 100ms',
 dtc: '0 DTC (Nominal)',
 severity: 'nominal',
 desc: 'Dinamik gaz pedalı dalgalanması, değişken motor devri (850 - 4500 RPM), 0.2 - 1.8 Bar turbo basıncı ve 88°C - 93°C soğutma suyu sıcaklığı üreten temiz CAN veri yolu.',
 signals: ['Engine Speed (RPM)', 'Turbo Boost (Bar)', 'Coolant Temp (°C)', 'Oil Pressure (Bar)']
 },
 {
 key: 'misfire_p0300' as ScenarioType,
 category: 'automotive' as CategoryFilter,
 categoryLabel: 'Otomotiv Arıza Teşhisi',
 title: 'DTC P0300 Rastgele Silindir Ateşleme Arızası',
 subTitle: 'Ateşleme Bobini & Buji Kaçağı, Tork Düşüşü',
 protocol: 'ISO 14229 / UDS',
 frameFormat: '11-Bit Std & DM1',
 baud: '250 kbps',
 period: '10ms',
 dtc: 'DTC P0300 (Critical)',
 severity: 'critical',
 desc: 'Osiloskopta 150-300 RPM anlık devir mikro-dalgalanması, yanmamış yakıt karışımı ve ECU hata kayıt hafızasına yazılan P0300 aktif arıza çerçevesi.',
 signals: ['RPM Jitter', 'Torque Drop (Nm)', 'Misfire Counter', 'Check Engine MIL On']
 },
 {
 key: 'overboost' as ScenarioType,
 category: 'automotive' as CategoryFilter,
 categoryLabel: 'Otomotiv Arıza Teşhisi',
 title: 'DTC P0234 Turbo Aşırı Basınç (Overboost)',
 subTitle: 'Wastegate Mekanik Sıkışması veya N75 Valfi Tepkisizliği',
 protocol: 'ISO 11898-2',
 frameFormat: '11-Bit Std (0x200)',
 baud: '250 kbps',
 period: '20ms',
 dtc: 'DTC P0234 (Warning)',
 severity: 'warning',
 desc: 'Turbo besleme basıncının 2.45 Bar tepe noktasına fırlaması, aşırı tork artışı ve ECU koruma haritası tetiklenmesi simülasyonu.',
 signals: ['Boost > 2.4 Bar', 'Wastegate Duty %', 'Intake Air Temp', 'Manifold Absolute Pressure']
 },
 {
 key: 'overheat' as ScenarioType,
 category: 'automotive' as CategoryFilter,
 categoryLabel: 'Otomotiv Arıza Teşhisi',
 title: 'DTC P0115 Motor Hararet & Soğutma Arızası',
 subTitle: 'Termostat Sıkışması veya ECT Sensör Devre Sapması',
 protocol: 'SAE J1939 / OBD-II',
 frameFormat: '11-Bit Std / 29-Bit Ext',
 baud: '250 kbps',
 period: '100ms',
 dtc: 'DTC P0115 (Warning)',
 severity: 'warning',
 desc: 'Soğutma suyu sıcaklığının 108°C - 118°C kritik eşiklerine tırmanması, radyatör fanı yüksek hız talebi ve acil güç kısıtlama sinyalleri.',
 signals: ['Coolant Temp > 108°C', 'Radiator Fan 100%', 'Thermal Warning Lamp', 'Engine Derate']
 },
 {
 key: 'j1939_multi_ecu_fleet' as ScenarioType,
 category: 'automotive' as CategoryFilter,
 categoryLabel: 'Ağır Vasıta & Ticari Filo',
 title: 'SAE J1939 Çoklu Düğüm Filosu (5 ECU Ağı)',
 subTitle: 'ECM, TCM, Retarder, EBS ve Cluster Eş Zamanlı Yayını',
 protocol: 'SAE J1939-71 / 73',
 frameFormat: '29-Bit Ext (PGN 61444, 65265, 65226)',
 baud: '250 kbps',
 period: '10ms - 50ms',
 dtc: 'SPN 1087 / FMI 1 (EBS)',
 severity: 'critical',
 desc: '5 ayrı kontrol ünitesinin adres talebi (PGN 60928), vites değişim telemetrisi, retarder tork kontrolü ve EBS hava basıncı düşük arızası (DM1).',
 signals: ['PGN 61444 (EEC1)', 'PGN 65265 (Cruise/Speed)', 'PGN 65226 (DM1 Active Fault)', 'PGN 61442 (ETC1)']
 },
 {
 key: 'ev_bms_telemetry' as ScenarioType,
 category: 'ev' as CategoryFilter,
 categoryLabel: 'Elektrikli Araç (EV) & Yüksek Voltaj',
 title: 'EV Yüksek Voltaj BMS & İnvertör Telemetrisi',
 subTitle: 'Batarya Paketi 398V, -45A Şarj / +140A Deşarj, %78 SOC',
 protocol: 'ISO 11898-2 (EV CAN)',
 frameFormat: '11-Bit Std (0x350 - 0x380)',
 baud: '500 kbps',
 period: '20ms',
 dtc: 'DTC P0AA6 İzolasyon Sapması (Warning)',
 severity: 'warning',
 desc: '96 serili Li-Ion batarya paketi voltajı, 18mV hücre dengesi, rejeneratif frenleme akım döngüsü ve invertör IGBT sıcaklık telemetrisi.',
 signals: ['Pack Voltage (398.4 V)', 'Current (-45A ~ +140A)', 'SOC %78.5', 'Cell Delta 18 mV', 'Inverter 42°C']
 },
 {
 key: 'marine_vessel_n2k' as ScenarioType,
 category: 'marine' as CategoryFilter,
 categoryLabel: 'Marin & Denizcilik NMEA 2000',
 title: 'NMEA 2000 Marin Seyir & Çift Motor Ağı',
 subTitle: 'Sancak/İskele RPM, GPS SOG/COG, Sonar Derinlik & Dümen',
 protocol: 'NMEA 2000 (IEC 61162-3)',
 frameFormat: '29-Bit Ext (PGN 127488, 128267, 129026)',
 baud: '250 kbps',
 period: '100ms - 250ms',
 dtc: 'SPN 520201 Yakıtta Su (Warning)',
 severity: 'warning',
 desc: 'Çift marin dizel motor devri, GPS 18.6 Knot hız, 24.8 metre sonar derinliği, %11 pervane slip oranı ve yakıt filtresi su sensörü alarmı.',
 signals: ['PGN 127488 (Engine Rapid)', 'PGN 128267 (Water Depth)', 'PGN 129026 (COG/SOG)', 'PGN 127245 (Rudder)']
 },
 {
 key: 'can_fd_adas_vision' as ScenarioType,
 category: 'canfd' as CategoryFilter,
 categoryLabel: 'Yeni Nesil CAN-FD & Otonom Sürüş',
 title: 'CAN-FD 64B ADAS Ön Radar & Kamera Füzyonu',
 subTitle: '64 Bayt Yük, 2.0 Mbps BRS Veri Fazı, 8 Hedef Nesne Kümesi',
 protocol: 'CAN-FD ISO 11898-1:2015',
 frameFormat: 'CAN-FD 64B (BRS Aktif)',
 baud: '500k Arb / 2.0M Data',
 period: '20ms',
 dtc: 'DTC C1A00 Radar Kalibrasyon (Warning)',
 severity: 'warning',
 desc: 'Ön tampon 77 GHz radarından gelen 8 adet takip nesnesi (Mesafe, Relatif Hız, Azimut Açısı, Nesne Tipi) 64 baytlık genişletilmiş CAN-FD çerçevesiyle iletilir.',
 signals: ['64-Byte Payload', '8 Object Clusters', 'Target Distance & Velocity', 'BRS 2 Mbps Phase']
 },
 {
 key: 'bus_surge' as ScenarioType,
 category: 'stress' as CategoryFilter,
 categoryLabel: 'Ağ Stresi & Yük Testi',
 title: 'CAN Bus Ağ Taşması & Yüksek Yük (%85+ Bus Load)',
 subTitle: 'Babbling Node Simülasyonu, Arbitrasyon Gecikmeleri & CRC',
 protocol: 'ISO 11898-2',
 frameFormat: 'Mixed Std & Ext',
 baud: '250 kbps',
 period: '1ms Burst',
 dtc: 'Babbling Surge / Buffer Overflow',
 severity: 'critical',
 desc: 'Ağda arızalı bir düğümün saniyede 3.500+ paket basarak veri yolunu tıkaması, bus yükünün %85 üzerine fırlaması ve düşük öncelikli çerçevelerin gecikmesi.',
 signals: ['Bus Load > 85%', 'Frame Rate > 3500 fps', 'Arbitration Delay', 'CRC / Stuff Errors']
 },
 {
 key: 'intermittent_wiring_fault' as ScenarioType,
 category: 'stress' as CategoryFilter,
 categoryLabel: 'Fiziksel Katman Hata Enjeksiyonu',
 title: 'Kesintili Tesisat Temassızlığı & Bus-Off Kurtarma',
 subTitle: 'Mikro Temas Kopması, 120Ω Sonlandırma Hatası, Error Counter',
 protocol: 'Physical Layer / ISO 11898-2',
 frameFormat: 'Error Frames & Active Recovery',
 baud: '250 kbps',
 period: 'Değişken',
 dtc: 'DTC U0100 İletişim Kaybı (Critical)',
 severity: 'critical',
 desc: 'CAN-H / CAN-L hattında fiziksel titreşimden kaynaklanan temassızlık, Transmit Error Counter (TEC > 255) aşımı, Bus-Off durumu ve 128x11 bit kurtarma döngüsü.',
 signals: ['TEC/REC Error Counters', 'Bus-Off State', 'Physical Dropout', 'Automatic Recovery']
 }
 ];

 const filteredScenarios = scenarioList.filter((s) => {
 if (selectedCategory === 'all') return true;
 return s.category === selectedCategory;
 });

 const activeScenarioData = scenarioList.find((s) => s.key === activeScenario) || scenarioList[0];

 return (
 <div className="fixed inset-0 z-50 bg-slate-950/60 backdrop-blur-sm flex items-center justify-center p-3 md:p-6 animate-in fade-in duration-150">
 <div className="bg-white border border-slate-200 rounded-2xl shadow-2xl w-full max-w-5xl max-h-[92vh] flex flex-col overflow-hidden animate-in zoom-in-95 duration-150">
 
 {/* 1. Modal Top Header */}
 <div className="px-6 py-4 bg-slate-900 text-white border-b border-slate-800 flex items-center justify-between">
 <div className="flex items-center space-x-3">
 <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-brand-600 to-brand-500 flex items-center justify-center text-white shadow-md ring-2 ring-brand-400/20">
 <Sparkles className="w-5 h-5" />
 </div>
 <div>
 <div className="flex items-center space-x-2.5">
 <h2 className="text-base font-bold tracking-tight text-white">
 CAN-Bus Çok Sektörlü Simülasyon Stüdyosu
 </h2>
 <span className="text-xs font-mono bg-brand-500/20 text-brand-300 border border-brand-400/30 px-2 py-0.5 rounded-full font-semibold">
 10 Profesyonel Senaryo
 </span>
 </div>
 <p className="text-xs text-slate-500 font-medium mt-0.5">
 Otomotiv, J1939 Ağır Vasıta, EV Batarya (BMS), Marin NMEA 2000 ve CAN-FD Gerçek Zamanlı Telemetri
 </p>
 </div>
 </div>

 <button
 onClick={onClose}
 className="p-2 rounded-xl text-slate-500 hover:text-white hover:bg-slate-800 transition-colors"
 title="Kapat"
 >
 <X className="w-5 h-5" />
 </button>
 </div>

 {/* 2. Top Hero Control Banner */}
 <div className="px-6 py-4 bg-gradient-to-r from-slate-900 via-slate-850 to-indigo-950 text-white border-b border-slate-800/80 flex flex-wrap items-center justify-between gap-4">
 {/* Status Details */}
 <div className="flex items-center space-x-4">
 <div className="flex items-center space-x-2.5">
 <span className="relative flex h-3.5 w-3.5">
 {isSimulating && (
 <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-signal-400 opacity-75"></span>
 )}
 <span className={`relative inline-flex rounded-full h-3.5 w-3.5 ${isSimulating ? 'bg-signal-500' : 'bg-slate-500'}`}></span>
 </span>
 <div>
 <div className="text-xs font-bold text-slate-200">
 {isSimulating ? 'CAN Simülatörü Canlı Yayında' : 'Simülatör Boşta / Durduruldu'}
 </div>
 <div className="text-xs text-slate-500 font-mono">
 Kanal: <strong className="text-brand-300">{channel}</strong> | Hız: <strong className="text-slate-300">{baudRate}</strong> | Yayın: <strong className="text-signal-400">{isSimulating ? '100 Hz' : '0 Hz'}</strong>
 </div>
 </div>
 </div>

 <div className="h-8 w-px bg-slate-700/60 hidden sm:block"></div>

 <div className="hidden md:block">
 <div className="text-xs uppercase tracking-wider text-slate-500 font-semibold">Aktif Senaryo</div>
 <div className="text-xs font-bold text-brand-300 truncate max-w-[280px]">
 {activeScenarioData.title}
 </div>
 </div>
 </div>

 {/* Action Group */}
 <div className="flex items-center space-x-2.5">
 {/* Speed Multipliers */}
 {onChangeSpeed && (
 <div className="hidden lg:flex items-center bg-slate-800/80 p-1 rounded-lg border border-slate-700 text-xs font-mono">
 {[0.5, 1.0, 2.0, 5.0].map((s) => (
 <button
 key={s}
 onClick={() => onChangeSpeed(s)}
 className={`px-2 py-1 rounded font-semibold transition-colors ${
 simulationSpeed === s 
 ? 'bg-brand-600 text-white shadow-xs' 
 : 'text-slate-500 hover:text-white'
 }`}
 >
 {s}x
 </button>
 ))}
 </div>
 )}

 {/* Primary Toggle Button */}
 <button
 onClick={onToggleSimulator}
 className={`flex items-center space-x-2 px-5 py-2.5 rounded-xl text-xs font-bold transition-all shadow-md active:scale-95 ${
 isSimulating
 ? 'bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 ring-2 ring-amber-500/10'
 : 'bg-gradient-to-r from-brand-600 via-brand-600 to-brand-500 hover:from-brand-500 hover:to-brand-500 text-white shadow-brand-500/20 ring-2 ring-brand-400/20'
 }`}
 >
 {isSimulating ? (
 <>
 <Pause className="w-4 h-4 fill-current" />
 <span>Simülasyonu Duraklat</span>
 </>
 ) : (
 <>
 <Play className="w-4 h-4 fill-current" />
 <span>Simülatörü Başlat</span>
 </>
 )}
 </button>
 </div>
 </div>

 {/* 3. Category Filter Tabs */}
 <div className="px-6 py-2.5 bg-slate-50 border-b border-slate-200 flex items-center space-x-1.5 overflow-x-auto">
 {[
 { id: 'all' as CategoryFilter, label: 'Tüm Senaryolar', count: 10 },
 { id: 'automotive' as CategoryFilter, label: 'Otomotiv & J1939', count: 5 },
 { id: 'ev' as CategoryFilter, label: 'Elektrikli Araç (BMS)', count: 1 },
 { id: 'marine' as CategoryFilter, label: 'Marin NMEA 2000', count: 1 },
 { id: 'canfd' as CategoryFilter, label: 'CAN-FD & ADAS', count: 1 },
 { id: 'stress' as CategoryFilter, label: 'Ağ Stres & Hatalar', count: 2 },
 ].map((tab) => {
 const isSelected = selectedCategory === tab.id;
 return (
 <button
 key={tab.id}
 onClick={() => setSelectedCategory(tab.id)}
 className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all ${
 isSelected
 ? 'bg-brand-600 text-white shadow-xs'
 : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 hover:text-slate-900'
 }`}
 >
 <span>{tab.label}</span>
 <span className={`text-xs px-1.5 py-0.2 rounded-full font-mono font-bold ${
 isSelected ? 'bg-brand-700 text-white' : 'bg-slate-100 text-slate-500'
 }`}>
 {tab.count}
 </span>
 </button>
 );
 })}
 </div>

 {/* 4. Spacious Scenarios Grid */}
 <div className="flex-1 p-6 overflow-y-auto bg-slate-100/50">
 <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
 {filteredScenarios.map((scenario) => {
 const isActive = activeScenario === scenario.key;
 const isRunningThis = isActive && isSimulating;

 return (
 <div
 key={scenario.key}
 className={`bg-white rounded-xl border p-4 transition-all flex flex-col justify-between shadow-xs hover:shadow-md ${
 isActive
 ? 'border-brand-500 ring-2 ring-brand-500/20 bg-gradient-to-b from-brand-50/30 via-white to-white'
 : 'border-slate-200 hover:border-slate-300'
 }`}
 >
 <div>
 {/* Card Top Row */}
 <div className="flex items-start justify-between gap-2 mb-2">
 <div className="flex items-center space-x-2 min-w-0">
 <span className="text-xs font-bold text-slate-500 uppercase tracking-wide">
 {scenario.categoryLabel}
 </span>
 </div>

 {isActive && (
 <span className="flex items-center space-x-1 bg-brand-100 text-brand-700 text-xs font-bold px-2 py-0.5 rounded-full shrink-0 border border-brand-200">
 <Check className="w-3 h-3 text-brand-600" />
 <span>{isRunningThis ? 'Yayında' : 'Seçili'}</span>
 </span>
 )}
 </div>

 {/* Title & Subtitle */}
 <h3 className="text-sm font-bold text-slate-900 leading-snug">
 {scenario.title}
 </h3>
 <p className="text-xs text-slate-500 mt-0.5 font-medium">
 {scenario.subTitle}
 </p>

 {/* Technical Chips */}
 <div className="flex flex-wrap gap-1.5 my-3">
 <span className="bg-slate-100 border border-slate-200 text-slate-700 text-xs font-mono px-2 py-0.5 rounded font-medium">
 {scenario.protocol}
 </span>
 <span className="bg-slate-100 border border-slate-200 text-slate-700 text-xs font-mono px-2 py-0.5 rounded font-medium">
 {scenario.frameFormat}
 </span>
 <span className="bg-slate-100 border border-slate-200 text-slate-700 text-xs font-mono px-2 py-0.5 rounded font-medium">
 {scenario.period}
 </span>
 <span className={`text-xs font-mono px-2 py-0.5 rounded font-semibold border ${
 scenario.severity === 'critical'
 ? 'bg-rose-50 text-rose-700 border-rose-200'
 : scenario.severity === 'warning'
 ? 'bg-amber-50 text-amber-700 border-amber-200'
 : 'bg-signal-50 text-signal-700 border-signal-200'
 }`}>
 {scenario.dtc}
 </span>
 </div>

 {/* Description */}
 <p className="text-xs text-slate-600 leading-relaxed bg-slate-50 border border-slate-100 rounded-lg p-2.5 mb-3 font-sans">
 {scenario.desc}
 </p>

 {/* Signal Badges */}
 <div className="flex flex-wrap items-center gap-1 mb-4">
 <span className="text-xs text-slate-500 font-semibold mr-1">Sinyaller:</span>
 {scenario.signals.map((sig, sIdx) => (
 <span 
 key={sIdx}
 className="bg-white border border-slate-200 text-slate-600 text-xs font-mono px-1.5 py-0.2 rounded"
 >
 {sig}
 </span>
 ))}
 </div>
 </div>

 {/* Card Action Button */}
 <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
 <span className="text-xs text-slate-500 font-mono">
 Baud: {scenario.baud}
 </span>

 <button
 onClick={() => {
 onSelectScenario(scenario.key);
 onClose();
 }}
 className={`flex items-center space-x-1.5 px-4 py-2 rounded-lg text-xs font-bold transition-all shadow-2xs ${
 isActive
 ? 'bg-brand-600 hover:bg-brand-700 text-white shadow-xs'
 : 'bg-slate-900 hover:bg-slate-800 text-white'
 }`}
 >
 <Play className="w-3.5 h-3.5 fill-current" />
 <span>{isActive ? 'Bu Senaryo İle Çalıştır' : 'Senaryoyu Başlat'}</span>
 </button>
 </div>
 </div>
 );
 })}
 </div>
 </div>

 {/* 5. Modal Footer */}
 <div className="px-6 py-3 bg-white border-t border-slate-200 flex items-center justify-between text-xs text-slate-500">
 <div className="flex items-center space-x-4">
 <span className="flex items-center space-x-1.5">
 <span className="w-2 h-2 rounded-full bg-signal-500"></span>
 <span>15+ Sanal ECU Düğümü Simüle Ediliyor</span>
 </span>
 <span className="hidden sm:inline text-slate-300">|</span>
 <span className="hidden sm:inline font-mono">
 Toplam Paket: <strong>{totalPackets.toLocaleString('tr-TR')}</strong>
 </span>
 </div>

 <button
 onClick={onClose}
 className="px-4 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold rounded-lg transition-colors"
 >
 Kapat
 </button>
 </div>

 </div>
 </div>
 );
};

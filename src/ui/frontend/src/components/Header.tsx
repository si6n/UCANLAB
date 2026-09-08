import React, { useState, useRef, useEffect } from 'react';
import {
  Activity,
  Layers,
  AlertOctagon,
  Play,
  Pause,
  Settings,
  ChevronDown,
  Zap,
  Gauge,
  Check,
} from 'lucide-react';
import { ScenarioType, FaultInjectionType } from '../types/can';

interface HeaderProps {
  channel: string;
  baudRate: string;
  busLoad: number;
  totalPackets: number;
  isSimulating: boolean;
  isEstopActive: boolean;
  activeScenario: ScenarioType;
  simulationSpeed?: number;
  onToggleSimulator: () => void;
  onSelectScenario: (scenario: ScenarioType) => void;
  onEstop: () => void;
  onChangeSpeed?: (speed: number) => void;
  onInjectFault?: (type: FaultInjectionType) => void;
  onOpenSettings: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  channel,
  baudRate,
  busLoad,
  totalPackets,
  isSimulating,
  isEstopActive,
  activeScenario,
  simulationSpeed = 1.0,
  onToggleSimulator,
  onSelectScenario,
  onEstop,
  onChangeSpeed,
  onInjectFault,
  onOpenSettings,
}) => {
  const [showScenarioMenu, setShowScenarioMenu] = useState(false);
  const [showFaultMenu, setShowFaultMenu] = useState(false);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const scenarioCategories = [
    {
      category: 'Otomotiv & Ağır Vasıta J1939',
      items: [
        { key: 'nominal' as ScenarioType, title: 'Nominal Çalışma (0 DTC)', desc: 'Periyodik devir, turbo ve soğutma telemetrisi' },
        { key: 'misfire_p0300' as ScenarioType, title: 'DTC P0300 Silindir Tekleme', desc: 'Ateşleme arızası ve anlık tork kaybı' },
        { key: 'overboost' as ScenarioType, title: 'DTC P0234 Turbo Aşırı Basınç', desc: 'Wastegate sıkışması, >2.4 Bar takviye' },
        { key: 'overheat' as ScenarioType, title: 'DTC P0115 Motor Harareti', desc: 'Soğutma suyu >108°C kritik sıcaklık uyarısı' },
        { key: 'j1939_multi_ecu_fleet' as ScenarioType, title: 'J1939 Filo Ağı (5 ECU Eşzamanlı)', desc: 'ECM, TCM, Retarder, EBS ve Gösterge' },
      ],
    },
    {
      category: 'Elektrikli Araç (EV & BMS)',
      items: [
        { key: 'ev_bms_telemetry' as ScenarioType, title: 'EV Yüksek Voltaj BMS Telemetrisi', desc: '398V DC, -45A/+140A akım döngüsü, %78 SOC' },
      ],
    },
    {
      category: 'Marin & Denizcilik NMEA 2000',
      items: [
        { key: 'marine_vessel_n2k' as ScenarioType, title: 'NMEA 2000 Marin Seyir & Çift Motor', desc: 'Sancak/İskele RPM, GPS SOG hızı ve derinlik' },
      ],
    },
    {
      category: 'Yeni Nesil CAN-FD & ADAS',
      items: [
        { key: 'can_fd_adas_vision' as ScenarioType, title: 'CAN-FD 64B ADAS Ön Radar & Kamera', desc: '64 Bayt yük, 2.0 Mbps BRS, 8 hedef nesne kümesi' },
      ],
    },
    {
      category: 'Ağ Stres & Hat Hataları',
      items: [
        { key: 'bus_surge' as ScenarioType, title: 'CAN Bus Ağ Taşması & Yüksek Yük (%85+)', desc: 'Babbling Node patlaması ve CRC hataları' },
        { key: 'intermittent_wiring_fault' as ScenarioType, title: 'Kesintili Tesisat & Bus-Off Kurtarma', desc: 'Mikro temas temassızlığı ve otomatik kurtarma' },
      ],
    },
  ];

  // Close all menus on click outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setShowScenarioMenu(false);
        setShowFaultMenu(false);
        setShowSpeedMenu(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const closeAllMenus = () => {
    setShowScenarioMenu(false);
    setShowFaultMenu(false);
    setShowSpeedMenu(false);
  };

  return (
    <header
      ref={rootRef}
      className="surface-header relative z-40 flex h-[60px] shrink-0 items-center justify-between gap-4 bg-white/80 px-5 backdrop-blur-xl"
    >
      {/* Left: Page Title Area */}
      <div className="flex min-w-0 items-center gap-3">
        <h2 className="hidden truncate text-[15px] font-semibold tracking-tight text-slate-900 lg:block">
          UCanLab v1.0
        </h2>
        <div className="hidden h-4 w-px bg-slate-200 lg:block" />
        {/* Live Status Pill */}
        <div className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-600">
          <span className="relative flex h-1.5 w-1.5">
            {!isEstopActive && isSimulating && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal-400 opacity-75"></span>
            )}
            <span
              className={`relative inline-flex h-1.5 w-1.5 rounded-full ${
                isEstopActive ? 'bg-rose-500' : isSimulating ? 'bg-signal-500' : 'bg-amber-500'
              }`}
            ></span>
          </span>
          <span className="font-mono font-semibold text-slate-700">{channel}</span>
          <span className="text-slate-400">·</span>
          <span>{isEstopActive ? 'Durduruldu' : isSimulating ? `Canlı (${baudRate})` : `Bağlı (${baudRate})`}</span>
        </div>
      </div>

      {/* Right: Status Readouts & Action Group */}
      <div className="flex shrink-0 items-center gap-2.5">
        {/* Bus Load Chip */}
        <div className="chip !gap-1.5">
          <Activity className="h-3 w-3 text-brand-600" strokeWidth={2.4} />
          <span className="text-slate-500">Yük</span>
          <span
            className={`font-mono-num text-xs font-bold ${
              busLoad > 70 ? 'text-rose-600' : busLoad > 50 ? 'text-amber-600' : '!text-slate-800'
            }`}
          >
            %{busLoad}
          </span>
        </div>

        {/* Total Packets Chip */}
        <div className="chip !gap-1.5">
          <Layers className="h-3 w-3 text-brand-600" strokeWidth={2.4} />
          <span className="text-slate-500">Paket</span>
          <span className="font-mono-num !text-slate-800 text-xs font-bold">
            {totalPackets.toLocaleString('tr-TR')}
          </span>
        </div>

        <div className="h-4 w-px bg-slate-200" />

        {/* E-STOP Button */}
        <button
          onClick={onEstop}
          className={`focus-ring inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-all duration-150 active:scale-[0.98] ${
            isEstopActive
              ? 'border-rose-300 bg-rose-600 text-white shadow-sm shadow-rose-500/30'
              : 'border-rose-200 bg-rose-50 text-rose-700 shadow-sm hover:border-rose-300 hover:bg-rose-100'
          }`}
          title="Tüm CAN akışını acil durdur"
        >
          <AlertOctagon className="h-3.5 w-3.5" strokeWidth={2.2} />
          <span>{isEstopActive ? 'E-STOP (DURDU)' : 'E-STOP'}</span>
        </button>

        {/* Fault Injection Button & Dropdown */}
        <div className="relative">
          <button
            onClick={() => {
              setShowFaultMenu(!showFaultMenu);
              setShowScenarioMenu(false);
              setShowSpeedMenu(false);
            }}
            className="focus-ring inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-semibold text-amber-700 shadow-sm transition-all duration-150 hover:border-amber-300 hover:bg-amber-100 active:scale-[0.98]"
            title="Manuel Hata Enjeksiyonu"
          >
            <Zap className="h-3.5 w-3.5 text-amber-500" strokeWidth={2.4} />
            <span>Hata Enjekte Et</span>
            <ChevronDown className={`h-3 w-3 text-amber-600 transition-transform ${showFaultMenu ? 'rotate-180' : ''}`} />
          </button>

          {showFaultMenu && (
            <div className="animate-scale-in absolute right-0 top-full z-50 mt-2 w-64 rounded-xl border border-slate-200/80 bg-white/90 p-1.5 shadow-card-elevated ring-1 ring-slate-950/5 backdrop-blur-xl">
              <div className="border-b border-slate-100 px-2.5 py-1.5 text-xs font-bold uppercase tracking-wider text-slate-500">
                Canlı Hat Hata Enjeksiyonu
              </div>
              <button
                onClick={() => {
                  if (onInjectFault) onInjectFault('error_frame');
                  setShowFaultMenu(false);
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] font-semibold text-rose-700 transition-colors hover:bg-rose-50"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
                Fiziksel Hata Karesi Bas (Error Frame)
              </button>
              <button
                onClick={() => {
                  if (onInjectFault) onInjectFault('dtc_fault');
                  setShowFaultMenu(false);
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] font-semibold text-amber-800 transition-colors hover:bg-amber-50"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
                Aktif DTC Arıza Kodu Tetikle (DM1)
              </button>
              <button
                onClick={() => {
                  if (onInjectFault) onInjectFault('sensor_freeze');
                  setShowFaultMenu(false);
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] font-semibold text-brand-700 transition-colors hover:bg-brand-50"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-400" />
                Sensör Sinyal Donması (Frozen ADC)
              </button>
              <button
                onClick={() => {
                  if (onInjectFault) onInjectFault('babbling_surge');
                  setShowFaultMenu(false);
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] font-semibold text-indigo-700 transition-colors hover:bg-indigo-50"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-500" />
                Ağ Taşması Patlaması (Babbling Node)
              </button>
              <button
                onClick={() => {
                  if (onInjectFault) onInjectFault('wiring_dropout');
                  setShowFaultMenu(false);
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] font-semibold text-slate-700 transition-colors hover:bg-slate-50"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-500" />
                Kesintili Kablo & Bus-Off
              </button>
            </div>
          )}
        </div>

        {/* Simulator Start / Pause & Scenario Dropdown (Segmented) */}
        <div className="relative">
          <div className="inline-flex overflow-hidden rounded-lg border border-slate-200/80 shadow-sm">
            <button
              onClick={onToggleSimulator}
              className={`focus-ring inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold transition-all duration-150 active:scale-[0.98] ${
                isSimulating
                  ? 'bg-slate-800 text-white hover:bg-slate-900'
                  : 'bg-brand-600 text-white shadow-sm shadow-brand-900/15 hover:bg-brand-500'
              }`}
              title={isSimulating ? 'Simülasyonu Duraklat' : 'Simülatörü Başlat'}
            >
              {isSimulating ? (
                <>
                  <Pause className="h-3.5 w-3.5 fill-current text-amber-400" />
                  <span>Duraklat</span>
                </>
              ) : (
                <>
                  <Play className="h-3.5 w-3.5 fill-current" />
                  <span>Simülatör Başlat</span>
                </>
              )}
            </button>
            <button
              onClick={() => {
                setShowScenarioMenu(!showScenarioMenu);
                setShowFaultMenu(false);
                setShowSpeedMenu(false);
              }}
              className={`focus-ring inline-flex items-center border-l px-2 py-1.5 transition-colors duration-150 ${
                isSimulating
                  ? 'border-slate-700 bg-slate-850 hover:bg-slate-900 text-slate-200'
                  : 'border-brand-500 bg-brand-700 text-white hover:bg-brand-800'
              }`}
              title="Senaryo Galerisi"
            >
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform duration-150 ${showScenarioMenu ? 'rotate-180' : ''}`}
              />
            </button>
          </div>

          {/* Scenario Dropdown */}
          {showScenarioMenu && (
            <div className="animate-scale-in absolute right-0 top-full z-50 mt-2 max-h-[480px] w-[22rem] overflow-y-auto rounded-xl border border-slate-200/80 bg-white/90 p-1.5 shadow-card-elevated ring-1 ring-slate-950/5 backdrop-blur-xl">
              <div className="flex items-center justify-between border-b border-slate-100 px-2.5 py-1.5">
                <span className="section-kicker !text-xs">CAN-Bus Senaryo Galerisi</span>
                <span className="chip !py-0.5 !text-xs !text-slate-500">10 Senaryo</span>
              </div>

              {scenarioCategories.map((cat, catIdx) => (
                <div key={catIdx} className="py-1">
                  <div className="border-y border-slate-100 bg-slate-50/70 px-2.5 py-1 text-xs font-bold uppercase tracking-wider text-slate-500">
                    {cat.category}
                  </div>
                  {cat.items.map((item) => {
                    const isSelected = activeScenario === item.key;
                    return (
                      <button
                        key={item.key}
                        onClick={() => {
                          onSelectScenario(item.key);
                          closeAllMenus();
                        }}
                        className={`flex w-full flex-col rounded-lg px-2.5 py-2 text-left text-xs transition-colors duration-150 ${
                          isSelected
                            ? 'bg-brand-50 font-semibold text-brand-700'
                            : 'text-slate-700 hover:bg-slate-50'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className={`truncate ${isSelected ? 'font-bold text-brand-800' : 'font-semibold text-slate-800'}`}>
                            {item.title}
                          </span>
                          {isSelected && <Check className="h-3.5 w-3.5 shrink-0 text-brand-600" />}
                        </div>
                        <span className="mt-0.5 truncate text-xs font-normal text-slate-500">{item.desc}</span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Speed Multiplier Button */}
        <div className="relative">
          <button
            onClick={() => {
              setShowSpeedMenu(!showSpeedMenu);
              setShowScenarioMenu(false);
              setShowFaultMenu(false);
            }}
            className="focus-ring chip !gap-1.5 !px-2.5 !py-1.5 !text-xs transition-all duration-150 hover:border-slate-300 hover:bg-slate-100 active:scale-[0.98]"
            title="Simülasyon Hızı"
          >
            <Gauge className="h-3.5 w-3.5 text-slate-500" strokeWidth={2.2} />
            <span className="font-mono-num !text-slate-800">{simulationSpeed}x</span>
            <ChevronDown className={`h-3 w-3 text-slate-500 transition-transform ${showSpeedMenu ? 'rotate-180' : ''}`} />
          </button>

          {showSpeedMenu && (
            <div className="animate-scale-in absolute right-0 top-full z-50 mt-2 w-28 rounded-xl border border-slate-200/80 bg-white/90 p-1.5 shadow-card-elevated ring-1 ring-slate-950/5 backdrop-blur-xl">
              {[0.5, 1.0, 2.0, 5.0].map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    if (onChangeSpeed) onChangeSpeed(s);
                    setShowSpeedMenu(false);
                  }}
                  className={`font-mono-num w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] font-semibold transition-colors duration-150 ${
                    simulationSpeed === s ? 'bg-brand-50 text-brand-700' : 'text-slate-700 hover:bg-slate-50'
                  }`}
                >
                  {s}x Hız
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Settings Button */}
        <button
          onClick={onOpenSettings}
          className="focus-ring rounded-xl border border-slate-200 bg-white p-2 text-slate-500 shadow-sm transition-all duration-150 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 active:scale-[0.98]"
          title="Ayarlar"
        >
          <Settings className="h-4 w-4" strokeWidth={2} />
        </button>
      </div>
    </header>
  );
};

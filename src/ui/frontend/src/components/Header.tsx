import React, { useState, useRef, useEffect } from 'react';
import {
  Activity,
  Layers,
  AlertOctagon,
  Play,
  Pause,
  ChevronDown,
  Zap,
  Check,
  PanelLeft,
  PanelRight,
  FolderGit2,
  Minus,
  Square,
  X,
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
  onToggleSimulator: () => void;
  onSelectScenario: (scenario: ScenarioType) => void;
  onEstop: () => void;
  onInjectFault?: (type: FaultInjectionType) => void;

  // Header Toggles as requested: Sol Sidebar Toggle + Sağ Sidebar Toggle (Copilot)
  isLeftSidebarOpen: boolean;
  onToggleLeftSidebar: () => void;
  isRightSidebarOpen: boolean;
  onToggleRightSidebar: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  channel,
  baudRate,
  busLoad,
  totalPackets,
  isSimulating,
  isEstopActive,
  activeScenario,
  onToggleSimulator,
  onSelectScenario,
  onEstop,
  onInjectFault,
  isLeftSidebarOpen,
  onToggleLeftSidebar,
  isRightSidebarOpen,
  onToggleRightSidebar,
}) => {
  const [showScenarioMenu, setShowScenarioMenu] = useState(false);
  const [showFaultMenu, setShowFaultMenu] = useState(false);
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

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setShowScenarioMenu(false);
        setShowFaultMenu(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const closeAllMenus = () => {
    setShowScenarioMenu(false);
    setShowFaultMenu(false);
  };

  const handleWindowMinimize = () => {
    // ponytail: desktop window minimize; native bridge hook added when pywebview frameless mode toggled.
    if ((window as any).pywebview?.api?.minimize_window) {
      (window as any).pywebview.api.minimize_window();
    }
  };

  const handleWindowMaximize = () => {
    if (document.fullscreenElement) {
      document.exitFullscreen?.().catch(() => {});
    } else {
      document.documentElement.requestFullscreen?.().catch(() => {});
    }
  };

  const handleWindowClose = () => {
    if ((window as any).pywebview?.api?.close_window) {
      (window as any).pywebview.api.close_window();
    } else if (window.confirm('Universal CAN-Bus uygulamasından çıkmak istiyor musunuz?')) {
      window.close();
    }
  };

  return (
    // ponytail: zeron.sh unified title bar; minimal chrome with left and right sidebar toggles.
    <header
      ref={rootRef}
      className="glass-surface glass-topbar relative z-40 flex h-11 shrink-0 items-center justify-between gap-3 border-b border-white/[0.08] px-3 select-none text-zinc-300"
    >
      {/* ─────────────────────────────────────────────────────────────
          SOL TARAF: Sadece Sol Sidebar Toggle (Sekmeleri Aç/Kapat)
         ───────────────────────────────────────────────────────────── */}
      <div className="flex shrink-0 items-center gap-2">
        <button
          onClick={onToggleLeftSidebar}
          className={`focus-ring inline-flex h-7 w-7 items-center justify-center rounded-lg border transition-all duration-150 active:scale-[0.98] ${
            isLeftSidebarOpen
              ? 'border-indigo-500/40 bg-indigo-600/20 text-indigo-300 shadow-sm'
              : 'border-white/[0.08] bg-white/[0.03] text-zinc-400 hover:border-white/[0.15] hover:bg-white/[0.06] hover:text-zinc-100'
          }`}
          title={isLeftSidebarOpen ? 'Sekmeler Kenar Çubuğunu Gizle' : 'Sekmeler Kenar Çubuğunu Aç'}
        >
          <PanelLeft className="h-4 w-4" strokeWidth={2} />
        </button>
      </div>

      {/* ─────────────────────────────────────────────────────────────
          ORTA ALAN: Çalışma Alanı, Dizin, Protokol & Oturum Bilgisi
         ───────────────────────────────────────────────────────────── */}
      <div className="flex min-w-0 flex-1 items-center justify-center px-2">
        <div className="flex min-w-0 max-w-sm sm:max-w-md lg:max-w-xl items-center gap-2 rounded-lg border border-white/[0.08] bg-black/30 px-3 py-1 text-xs backdrop-blur-md">
          <FolderGit2 className="h-3.5 w-3.5 text-indigo-400 shrink-0" strokeWidth={2.2} />
          <span className="truncate font-semibold text-zinc-200">
            Universal-CAN / {channel}
          </span>
          <span className="text-zinc-500 shrink-0">·</span>
          <span className="font-mono text-zinc-400 text-[11px] shrink-0">{baudRate}</span>
          <span className="text-zinc-600 shrink-0 hidden sm:inline">·</span>
          <span className="truncate font-mono text-[11px] text-zinc-500 hidden sm:inline">
            @ DESKTOP-CAN-NODE
          </span>
          {/* Canlı Durum Noktası */}
          <span className="relative flex h-2 w-2 shrink-0 ml-1">
            {!isEstopActive && isSimulating && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            )}
            <span
              className={`relative inline-flex h-2 w-2 rounded-full ${
                isEstopActive ? 'bg-rose-500' : isSimulating ? 'bg-emerald-500' : 'bg-amber-500'
              }`}
            />
          </span>
        </div>
      </div>

      {/* ─────────────────────────────────────────────────────────────
          SAĞ TARAF: CAN Kontrolleri, Sağ Sidebar Toggle (Copilot) & Pencere
         ───────────────────────────────────────────────────────────── */}
      <div className="flex shrink-0 items-center gap-2">
        {/* Kompakt Bus Load & Paket sayaçları */}
        <div className="hidden xl:flex items-center gap-1.5 text-xs">
          <div className="flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-black/20 px-2 py-0.5 font-mono text-[11px]">
            <Activity className="h-3 w-3 text-indigo-400" strokeWidth={2.4} />
            <span className="text-zinc-500 text-[10px]">Yük</span>
            <span
              className={`font-bold ${
                busLoad > 70 ? 'text-rose-400' : busLoad > 50 ? 'text-amber-400' : 'text-zinc-200'
              }`}
            >
              %{busLoad}
            </span>
          </div>

          <div className="flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-black/20 px-2 py-0.5 font-mono text-[11px]">
            <Layers className="h-3 w-3 text-indigo-400" strokeWidth={2.4} />
            <span className="text-zinc-500 text-[10px]">Paket</span>
            <span className="font-bold text-zinc-200">
              {totalPackets.toLocaleString('tr-TR')}
            </span>
          </div>
        </div>

        {/* E-STOP Button */}
        <button
          onClick={onEstop}
          className={`focus-ring inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-semibold transition-all duration-150 active:scale-[0.98] ${
            isEstopActive
              ? 'border-rose-500/60 bg-rose-600 text-white shadow-sm shadow-rose-600/30'
              : 'border-rose-500/30 bg-rose-950/40 text-rose-300 hover:border-rose-500/60 hover:bg-rose-900/50'
          }`}
          title="Tüm CAN akışını acil durdur (ASIL-D)"
        >
          <AlertOctagon className="h-3.5 w-3.5" strokeWidth={2.4} />
          <span>{isEstopActive ? 'E-STOP (DURDU)' : 'E-STOP'}</span>
        </button>

        {/* Simülatör & Senaryo Menüsü (Segmented) */}
        <div className="relative inline-flex overflow-hidden rounded-lg border border-white/[0.08] bg-black/30 shadow-sm">
          <button
            onClick={onToggleSimulator}
            className={`focus-ring inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold transition-all duration-150 ${
              isSimulating
                ? 'bg-zinc-800 text-white hover:bg-zinc-700'
                : 'bg-indigo-600 text-white hover:bg-indigo-500'
            }`}
            title={isSimulating ? 'Simülasyonu Duraklat' : 'Simülatörü Başlat'}
          >
            {isSimulating ? (
              <>
                <Pause className="h-3 w-3 fill-current text-amber-400" />
                <span className="hidden lg:inline">Duraklat</span>
              </>
            ) : (
              <>
                <Play className="h-3 w-3 fill-current" />
                <span className="hidden lg:inline">Başlat</span>
              </>
            )}
          </button>
          <button
            onClick={() => {
              setShowScenarioMenu(!showScenarioMenu);
              setShowFaultMenu(false);
            }}
            className={`focus-ring inline-flex items-center border-l border-white/[0.08] px-1.5 py-1 transition-colors ${
              isSimulating
                ? 'bg-zinc-800/80 text-zinc-300 hover:bg-zinc-700'
                : 'bg-indigo-700 text-white hover:bg-indigo-600'
            }`}
            title="Senaryo Galerisi"
          >
            <ChevronDown className={`h-3 w-3 transition-transform ${showScenarioMenu ? 'rotate-180' : ''}`} />
          </button>

          {/* Senaryo Açılır Menüsü */}
          {showScenarioMenu && (
            <div className="animate-scale-in absolute right-0 top-full z-50 mt-1.5 max-h-[440px] w-80 overflow-y-auto rounded-xl border border-white/[0.12] bg-zinc-950/95 p-1.5 shadow-2xl backdrop-blur-2xl">
              <div className="flex items-center justify-between border-b border-white/[0.06] px-2.5 py-1.5">
                <span className="text-xs font-bold text-zinc-100">CAN Senaryo Galerisi</span>
                <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-mono text-zinc-400">10 Senaryo</span>
              </div>

              {scenarioCategories.map((cat, catIdx) => (
                <div key={catIdx} className="py-1">
                  <div className="border-y border-white/[0.04] bg-white/[0.02] px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-zinc-500">
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
                        className={`flex w-full flex-col rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors ${
                          isSelected
                            ? 'bg-indigo-600/30 text-indigo-300 font-semibold'
                            : 'text-zinc-300 hover:bg-white/[0.05]'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className={`truncate ${isSelected ? 'font-bold text-white' : 'text-zinc-200'}`}>
                            {item.title}
                          </span>
                          {isSelected && <Check className="h-3 w-3 shrink-0 text-indigo-400" />}
                        </div>
                        <span className="truncate text-[11px] text-zinc-500">{item.desc}</span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Hata Enjeksiyon Menüsü */}
        <div className="relative hidden md:block">
          <button
            onClick={() => {
              setShowFaultMenu(!showFaultMenu);
              setShowScenarioMenu(false);
            }}
            className="focus-ring inline-flex items-center gap-1 rounded-lg border border-amber-500/30 bg-amber-950/30 px-2 py-1 text-xs font-semibold text-amber-300 hover:bg-amber-900/40 transition-colors"
            title="Manuel Hata Enjeksiyonu"
          >
            <Zap className="h-3 w-3 text-amber-400" strokeWidth={2.4} />
            <span className="hidden lg:inline">Hata</span>
            <ChevronDown className={`h-2.5 w-2.5 text-amber-400 transition-transform ${showFaultMenu ? 'rotate-180' : ''}`} />
          </button>

          {showFaultMenu && (
            <div className="animate-scale-in absolute right-0 top-full z-50 mt-1.5 w-60 rounded-xl border border-white/[0.12] bg-zinc-950/95 p-1.5 shadow-2xl backdrop-blur-2xl">
              <div className="border-b border-white/[0.06] px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider text-zinc-500">
                Canlı Hat Hata Enjeksiyonu
              </div>
              <div className="py-1 space-y-0.5">
                {[
                  { key: 'error_frame', label: 'Error Frame (Hata Karesi)', color: 'bg-rose-500', text: 'text-rose-400' },
                  { key: 'dtc_fault', label: 'Aktif DTC Tetikle (DM1)', color: 'bg-amber-500', text: 'text-amber-300' },
                  { key: 'sensor_freeze', label: 'Sensör Sinyal Donması', color: 'bg-indigo-400', text: 'text-indigo-300' },
                  { key: 'babbling_surge', label: 'Babbling Node Taşması', color: 'bg-purple-500', text: 'text-purple-300' },
                  { key: 'wiring_dropout', label: 'Kesintili Kablo & Bus-Off', color: 'bg-zinc-400', text: 'text-zinc-300' },
                ].map((f) => (
                  <button
                    key={f.key}
                    onClick={() => {
                      if (onInjectFault) onInjectFault(f.key as FaultInjectionType);
                      setShowFaultMenu(false);
                    }}
                    className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs font-semibold hover:bg-white/[0.06] transition-colors ${f.text}`}
                  >
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${f.color}`} />
                    <span>{f.label}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Dikey Ayrıştırıcı */}
        <div className="h-4 w-px bg-white/[0.08]" />

        {/* SAĞ SIDEBAR TOGGLE: Çevrimdışı Copilot Aç/Kapat */}
        <button
          onClick={onToggleRightSidebar}
          className={`focus-ring inline-flex h-7 w-7 items-center justify-center rounded-lg border transition-all duration-150 active:scale-[0.98] ${
            isRightSidebarOpen
              ? 'border-indigo-500/40 bg-indigo-600/20 text-indigo-300 shadow-sm'
              : 'border-white/[0.08] bg-white/[0.03] text-zinc-400 hover:border-white/[0.15] hover:bg-white/[0.06] hover:text-zinc-100'
          }`}
          title={isRightSidebarOpen ? 'Çevrimdışı Copilot Panelini Gizle' : 'Çevrimdışı Copilot Panelini Aç'}
        >
          <PanelRight className="h-4 w-4" strokeWidth={2} />
        </button>

        {/* Dikey Ayrıştırıcı */}
        <div className="h-4 w-px bg-white/[0.08]" />

        {/* Pencere Kontrolleri (Minimize, Maximize, Close) */}
        <div className="inline-flex items-center gap-0.5">
          <button
            onClick={handleWindowMinimize}
            className="focus-ring h-6 w-6 inline-flex items-center justify-center rounded text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200 transition-colors"
            title="Simge Durumuna Küçült"
          >
            <Minus className="h-3 w-3" strokeWidth={2.4} />
          </button>
          <button
            onClick={handleWindowMaximize}
            className="focus-ring h-6 w-6 inline-flex items-center justify-center rounded text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200 transition-colors"
            title="Büyüt / Geri Yükle"
          >
            <Square className="h-2.5 w-2.5" strokeWidth={2.4} />
          </button>
          <button
            onClick={handleWindowClose}
            className="focus-ring h-6 w-6 inline-flex items-center justify-center rounded text-zinc-500 hover:bg-rose-600 hover:text-white transition-colors"
            title="Kapat"
          >
            <X className="h-3 w-3" strokeWidth={2.4} />
          </button>
        </div>
      </div>
    </header>
  );
};

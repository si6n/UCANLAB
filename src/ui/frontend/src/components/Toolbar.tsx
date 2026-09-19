import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import {
  Activity,
  Layers,
  Octagon,
  Play,
  Pause,
  ChevronDown,
  Zap,
  Minus,
  Square,
  X,
  Sun,
  Moon,
  PanelRight,
  Check,
} from 'lucide-react';
import { DesktopBridge } from '../services/bridge';
import { ScenarioType, FaultInjectionType } from '../types/can';

interface ToolbarProps {
  channel: string;
  baudRate: string;
  busLoad: number;
  totalPackets: number;
  isSimulating: boolean;
  isEstopActive: boolean;
  activeScenario?: ScenarioType;
  isCopilotOpen?: boolean;
  isRailCollapsed?: boolean;
  theme?: 'dark' | 'light';
  onToggleRail?: () => void;
  onToggleTheme?: () => void;
  onToggleSimulator: () => void;
  onSelectScenario?: (scenario: ScenarioType) => void;
  onInjectFault?: (fault: FaultInjectionType) => void;
  onEstop: () => void;
  onToggleCopilot?: () => void;
}

const SCENARIO_CATEGORIES = [
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

const FAULT_ITEMS: { key: FaultInjectionType; label: string; desc: string; color: string; dot: string }[] = [
  { key: 'error_frame', label: 'Error Frame (Hata Karesi)', desc: 'Fiziksel hatta 6 ardışık dominant bit basarak aktarımı keser', color: 'text-del', dot: 'bg-del' },
  { key: 'dtc_fault', label: 'Aktif DTC Tetikle (DM1)', desc: 'ECM motor ünitesinde SPN 100 FMI 1 aktif arıza yayını yapar', color: 'text-warn', dot: 'bg-warn' },
  { key: 'sensor_freeze', label: 'Sensör Sinyal Donması', desc: '0x0CF00400 motor devir sinyalini sabitler, donma algılatır', color: 'text-accent', dot: 'bg-accent' },
  { key: 'babbling_surge', label: 'Babbling Node Taşması', desc: 'Ağı %85+ yük ile doldurarak yüksek öncelikli paketleri geciktirir', color: 'text-purple-400', dot: 'bg-purple-500' },
  { key: 'wiring_dropout', label: 'Kesintili Kablo & Bus-Off', desc: 'Tesisat temassızlığı ve adres çakışması (PGN 59904 NACK) simüle eder', color: 'text-text-mid', dot: 'bg-text-low' },
];

export const Toolbar: React.FC<ToolbarProps> = ({
  channel = 'vcan0',
  baudRate = '250 kbps',
  busLoad = 12,
  totalPackets = 15553,
  isSimulating = false,
  isEstopActive = false,
  activeScenario = 'nominal',
  isCopilotOpen = false,
  theme = 'dark',
  onToggleTheme,
  onToggleSimulator,
  onSelectScenario,
  onInjectFault,
  onEstop,
  onToggleCopilot,
}) => {
  const [showScenarioMenu, setShowScenarioMenu] = useState(false);
  const [showFaultMenu, setShowFaultMenu] = useState(false);
  const scenarioBtnRef = useRef<HTMLButtonElement>(null);
  const faultBtnRef = useRef<HTMLButtonElement>(null);

  // Close menus on outside click or escape
  useEffect(() => {
    const handleMouseDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (
        !target.closest('[data-portal-menu="scenario"]') &&
        !scenarioBtnRef.current?.contains(target) &&
        !target.closest('[data-portal-menu="fault"]') &&
        !faultBtnRef.current?.contains(target)
      ) {
        setShowScenarioMenu(false);
        setShowFaultMenu(false);
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setShowScenarioMenu(false);
        setShowFaultMenu(false);
      }
    };

    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  const scenarioRect = scenarioBtnRef.current?.getBoundingClientRect();
  const faultRect = faultBtnRef.current?.getBoundingClientRect();

  return (
    <div className="pywebview-drag-region flex h-11 w-full shrink-0 select-none items-center justify-between glass-chrome px-3 cursor-move" style={{ zIndex: 20 }}>
      {/* LEFT: Telemetry Pills (Bus Load & Packet Count) */}
      <div className="flex items-center gap-2">
        {/* Telemetry Pills */}
        <div className="flex items-center gap-1.5 font-mono text-[11px]">
          {/* Yük Durumu */}
          <div className="flex items-center gap-1.5 rounded-[8px] border border-border/60 px-2.5 py-1 text-text-mid bg-surface-inset/30">
            <Activity className="h-3 w-3 text-text-mid" />
            <span>Yük: <strong className="font-medium text-text-hi">{busLoad}%</strong></span>
          </div>

          {/* Paket Durumu */}
          <div className="flex items-center gap-1.5 rounded-[8px] border border-border/60 px-2.5 py-1 text-text-mid bg-surface-inset/30">
            <Layers className="h-3 w-3 text-text-mid" />
            <span>Paket: <strong className="font-medium text-text-hi">{totalPackets.toLocaleString('tr-TR')}</strong></span>
          </div>

          {/* Status Indicator Dot */}
          <span className="relative ml-1 flex h-2 w-2 items-center justify-center">
            {isEstopActive ? (
              <span className="status-dot status-dot-danger animate-pulse" />
            ) : isSimulating ? (
              <>
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-add opacity-75" />
                <span className="status-dot status-dot-ok relative inline-flex" />
              </>
            ) : (
              <span className="status-dot status-dot-idle" />
            )}
          </span>
        </div>
      </div>

      {/* RIGHT: Actions + Theme Toggle + Copilot PanelRight + Window controls */}
      <div className="flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        {/* Toolbar Actions */}
        <div className="flex items-center gap-1.5 font-sans text-[12px]">
          {/* EMERGENCY-STOP */}
          <button
            onClick={onEstop}
            disabled={isEstopActive}
            className={`flex items-center gap-1.5 rounded-[6px] border border-del/30 bg-delbg px-2.5 py-1 font-medium tracking-tight transition-all active:scale-[0.98] ${
              isEstopActive
                ? 'cursor-not-allowed opacity-60'
                : 'text-del hover:bg-del/15'
            } text-del`}
            title="Acil Durdurma — Tüm İletim ve Akışı Derhal Kes"
          >
            <Octagon className="h-3.5 w-3.5" />
            <span>E-STOP {isEstopActive ? '(DURDU)' : ''}</span>
          </button>

          {/* START / STOP SPLIT BUTTON WITH SCENARIO DROPDOWN */}
          <div className="inline-flex overflow-hidden rounded-[6px] border border-add/30">
            <button
              onClick={onToggleSimulator}
              disabled={isEstopActive}
              className={`flex items-center gap-1.5 px-3 py-1 font-medium transition-all active:scale-[0.98] ${
                isEstopActive
                  ? 'cursor-not-allowed text-text-low opacity-60'
                  : 'bg-addbg text-add hover:bg-add/15'
              }`}
              title={isSimulating ? 'CAN Veri Akışını Duraklat' : 'CAN Veri Akışını Başlat'}
            >
              {isSimulating ? (
                <>
                  <Pause className="h-3.5 w-3.5" />
                  <span>Durdur</span>
                </>
              ) : (
                <>
                  <Play className="h-3.5 w-3.5" />
                  <span>Başlat</span>
                </>
              )}
            </button>
            <button
              ref={scenarioBtnRef}
              onClick={() => {
                setShowScenarioMenu((prev) => !prev);
                setShowFaultMenu(false);
              }}
              disabled={isEstopActive}
              className={`border-l border-add/30 px-1.5 py-1 transition-all ${
                isEstopActive ? 'cursor-not-allowed text-text-low opacity-60' : 'bg-addbg text-add hover:bg-add/15'
              } ${showScenarioMenu ? 'bg-add/25' : ''}`}
              title="Test Senaryoları & Galerisi"
              aria-expanded={showScenarioMenu}
            >
              <ChevronDown className={`h-3 w-3 transition-transform duration-150 ${showScenarioMenu ? 'rotate-180' : ''}`} />
            </button>
          </div>

          {/* CANLI HATA ENJEKSİYONU BUTONU & DROPDOWN */}
          <div className="relative">
            <button
              ref={faultBtnRef}
              onClick={() => {
                setShowFaultMenu((prev) => !prev);
                setShowScenarioMenu(false);
              }}
              className={`flex items-center gap-1.5 rounded-[6px] border px-2.5 py-1 font-medium transition-all active:scale-[0.98] ${
                showFaultMenu
                  ? 'border-warn/60 bg-warn/20 text-warn'
                  : 'border-warn/40 bg-warnbg text-warn hover:bg-warn/15'
              }`}
              title="Canlı Hatta Hata Enjeksiyonu Yap"
              aria-expanded={showFaultMenu}
            >
              <Zap className="h-3.5 w-3.5 text-warn" />
              <span>Hata</span>
              <ChevronDown className={`h-2.5 w-2.5 text-warn transition-transform duration-150 ${showFaultMenu ? 'rotate-180' : ''}`} />
            </button>
          </div>

          {/* Divider */}
          <div className="h-4 w-px bg-border/60 mx-0.5" />

          {/* THEME TOGGLE: Sun / Moon */}
          {onToggleTheme && (
            <button
              onClick={onToggleTheme}
              className="flex h-7 w-7 items-center justify-center rounded-[6px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi border border-transparent hover:border-border/60"
              title={theme === 'dark' ? 'Açık Temaya Geç (Zeron Light)' : 'Koyu Temaya Geç (Zeron Dark)'}
              aria-label="Toggle Theme"
            >
              {theme === 'dark' ? <Sun className="h-3.5 w-3.5 text-brandamber" /> : <Moon className="h-3.5 w-3.5 text-accent" />}
            </button>
          )}

          {/* COPILOT TOGGLE: PanelRight Icon */}
          <button
            onClick={onToggleCopilot}
            aria-label={isCopilotOpen ? 'Teşhis Copilot Çekmecesini Kapat' : 'Teşhis Copilot Çekmecesini Aç'}
            title={isCopilotOpen ? 'Teşhis Copilot Çekmecesini Kapat' : 'Teşhis Copilot Çekmecesini Aç'}
            className={`flex h-7 w-7 items-center justify-center rounded-[6px] transition-all ${
              isCopilotOpen
                ? 'bg-accent-soft text-accent-text border border-accent-line'
                : 'text-text-mid hover:bg-bg-row-hover hover:text-text-hi border border-transparent hover:border-border/60'
            }`}
          >
            <PanelRight className="h-4 w-4" />
          </button>

          {/* Divider before window controls */}
          <div className="h-4 w-px bg-border/60 mx-0.5" />

          {/* Window controls — ghost cluster */}
          <div className="flex items-center gap-0.5">
            <button
              onClick={(e) => { e.stopPropagation(); DesktopBridge.minimizeWindow(); }}
              className="flex h-6 w-7 items-center justify-center rounded-[4px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95"
              title="Simge Durumuna Küçült"
              aria-label="Minimize"
            >
              <Minus className="h-3 w-3" />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); DesktopBridge.maximizeWindow(); }}
              className="flex h-6 w-7 items-center justify-center rounded-[4px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95"
              title="Ekranı Kapla / Geri Yükle"
              aria-label="Maximize"
            >
              <Square className="h-2.5 w-2.5" />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); DesktopBridge.closeWindow(); }}
              className="flex h-6 w-7 items-center justify-center rounded-[4px] text-text-low transition-colors hover:bg-delbg hover:text-del active:scale-95"
              title="Pencereyi Kapat"
              aria-label="Close"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        </div>
      </div>

      {/* ─────────────────────────────────────────────────────────────
          PORTAL: TEST SENARYOLARI GALERİSİ DROPDOWN
         ───────────────────────────────────────────────────────────── */}
      {showScenarioMenu && scenarioRect && createPortal(
        <div
          data-portal-menu="scenario"
          style={{
            position: 'fixed',
            top: scenarioRect.bottom + 6,
            left: Math.max(8, Math.min(scenarioRect.right - 340, window.innerWidth - 348)),
            zIndex: 99999,
          }}
          className="w-[340px] max-h-[460px] overflow-y-auto rounded-[12px] glass-popover p-1.5 shadow-2xl animate-in fade-in zoom-in-95 duration-150 border border-border-strong"
        >
          <div className="flex items-center justify-between border-b border-border/60 px-3 py-2">
            <span className="text-[12px] font-bold text-text-hi">CAN Test Senaryoları</span>
            <span className="rounded-[4px] bg-accent-soft px-1.5 py-0.5 text-[10px] font-mono font-medium text-accent-text">
              10 Senaryo
            </span>
          </div>

          <div className="divide-y divide-border/40">
            {SCENARIO_CATEGORIES.map((cat, catIdx) => (
              <div key={catIdx} className="py-1">
                <div className="px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-text-low">
                  {cat.category}
                </div>
                <div className="space-y-0.5">
                  {cat.items.map((item) => {
                    const isSelected = activeScenario === item.key;
                    return (
                      <button
                        key={item.key}
                        onClick={() => {
                          if (onSelectScenario) onSelectScenario(item.key);
                          setShowScenarioMenu(false);
                        }}
                        className={`flex w-full flex-col rounded-[8px] px-2.5 py-1.5 text-left transition-colors ${
                          isSelected
                            ? 'bg-accent-soft text-accent-text border border-accent-line/40'
                            : 'hover:bg-bg-row-hover text-text-hi'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className={`text-[12px] ${isSelected ? 'font-bold text-accent-text' : 'font-medium text-text-hi'}`}>
                            {item.title}
                          </span>
                          {isSelected && <Check className="h-3.5 w-3.5 shrink-0 text-accent" />}
                        </div>
                        <span className="text-[11px] text-text-mid line-clamp-1">{item.desc}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>,
        document.body
      )}

      {/* ─────────────────────────────────────────────────────────────
          PORTAL: CANLI HAT HATA ENJEKSİYONU DROPDOWN
         ───────────────────────────────────────────────────────────── */}
      {showFaultMenu && faultRect && createPortal(
        <div
          data-portal-menu="fault"
          style={{
            position: 'fixed',
            top: faultRect.bottom + 6,
            left: Math.max(8, Math.min(faultRect.right - 290, window.innerWidth - 298)),
            zIndex: 99999,
          }}
          className="w-[290px] rounded-[12px] glass-popover p-1.5 shadow-2xl animate-in fade-in zoom-in-95 duration-150 border border-border-strong"
        >
          <div className="border-b border-border/60 px-3 py-2 flex items-center justify-between">
            <span className="text-[12px] font-bold text-text-hi">Canlı Hat Hata Enjeksiyonu</span>
            <span className="text-[10px] font-mono text-warn font-medium px-1.5 py-0.5 rounded bg-warnbg">
              ASIL-D Güvenli
            </span>
          </div>

          <div className="py-1 space-y-0.5">
            {FAULT_ITEMS.map((f) => (
              <button
                key={f.key}
                onClick={() => {
                  if (onInjectFault) onInjectFault(f.key);
                  setShowFaultMenu(false);
                }}
                className="flex w-full flex-col rounded-[8px] px-2.5 py-1.5 text-left hover:bg-bg-row-hover transition-colors group"
              >
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${f.dot}`} />
                  <span className={`text-[12px] font-semibold text-text-hi group-hover:${f.color} transition-colors`}>
                    {f.label}
                  </span>
                </div>
                <span className="text-[10.5px] text-text-mid pl-4 leading-tight">
                  {f.desc}
                </span>
              </button>
            ))}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

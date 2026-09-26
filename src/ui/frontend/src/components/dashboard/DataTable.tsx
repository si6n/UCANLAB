import React, { useState, useMemo, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import {
  Filter,
  Search,
  Play,
  Pause,
  Trash2,
  Info,
  AlertTriangle,
  ExternalLink,
  Copy,
  Check,
  Sparkles,
  Activity,
  Layers,
  Octagon,
  ChevronDown,
  Zap,
  X,
} from 'lucide-react';
import { CanPacketRow } from '../../data/constants';
import { HexBytePainter } from './HexBytePainter';
import { ScenarioType, FaultInjectionType } from '../../types/can';

interface DataTableProps {
  rows: CanPacketRow[];
  isStreaming: boolean;
  frameRate: number;
  busLoad?: number;
  totalPackets?: number;
  isEstopActive?: boolean;
  activeScenario?: ScenarioType;
  onToggleStreaming: () => void;
  onSelectScenario?: (scenario: ScenarioType) => void;
  onInjectFault?: (fault: FaultInjectionType) => void;
  onEstop?: () => void;
  onClearBuffer: () => void;
  selectedRowId: string | null;
  onSelectRow: (id: string | null) => void;
  onAskCopilot?: (prompt: string) => void;
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

export const DataTable: React.FC<DataTableProps> = ({
  rows,
  isStreaming,
  frameRate = 0,
  busLoad = 12,
  totalPackets = 15553,
  isEstopActive = false,
  activeScenario = 'nominal',
  onToggleStreaming,
  onSelectScenario,
  onInjectFault,
  onEstop,
  onClearBuffer,
  selectedRowId,
  onSelectRow,
  onAskCopilot,
}) => {
  const [filterAnomaliesOnly, setFilterAnomaliesOnly] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [showInfoModal, setShowInfoModal] = useState(false);
  const [isClearing, setIsClearing] = useState(false);

  // Scenario & Fault Popover States
  const [showScenarioMenu, setShowScenarioMenu] = useState(false);
  const [showFaultMenu, setShowFaultMenu] = useState(false);
  const scenarioBtnRef = useRef<HTMLButtonElement>(null);
  const faultBtnRef = useRef<HTMLButtonElement>(null);

  // Context menu popover state
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    row: CanPacketRow;
  } | null>(null);
  const [copiedText, setCopiedText] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);

  // Close menus on outside click or Escape
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

    const handleClick = () => setContextMenu(null);
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setContextMenu(null);
        setShowScenarioMenu(false);
        setShowFaultMenu(false);
      }
    };

    document.addEventListener('mousedown', handleMouseDown);
    window.addEventListener('click', handleClick);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      window.removeEventListener('click', handleClick);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  const scenarioRect = scenarioBtnRef.current?.getBoundingClientRect();
  const faultRect = faultBtnRef.current?.getBoundingClientRect();

  // Anomaly count in current buffer
  const anomalyCount = useMemo(
    () => rows.filter((r) => r.isAnomaly).length,
    [rows]
  );

  // Filter rows based on search
  const filteredRows = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return rows.filter((row) => {
      if (!q) return true;
      const matchId = row.canId.toLowerCase().includes(q);
      const matchHex = row.dataBytes.some((b) => b.toLowerCase().includes(q));
      const matchAscii = row.ascii.toLowerCase().includes(q);
      const matchChan = row.channel.toLowerCase().includes(q);
      return matchId || matchHex || matchAscii || matchChan;
    });
  }, [rows, searchQuery]);

  // Handle Clear Flash
  const handleClear = () => {
    setIsClearing(true);
    setTimeout(() => {
      onClearBuffer();
      setIsClearing(false);
      onSelectRow(null);
    }, 80);
  };

  // Handle copy hex
  const handleCopyRow = (row: CanPacketRow) => {
    const text = `ID: ${row.canId} DLC: ${row.dlc} DATA: ${row.dataBytes.join(' ')} ASCII: ${row.ascii}`;
    navigator.clipboard.writeText(text);
    setCopiedText(true);
    setTimeout(() => setCopiedText(false), 1200);
  };

  return (
    <div
      ref={containerRef}
      className={`relative flex flex-1 flex-col overflow-hidden transition-opacity duration-75 ${
        isClearing ? 'opacity-20' : 'opacity-100'
      }`}
      onClick={(e) => {
        if (e.target === containerRef.current) {
          onSelectRow(null);
        }
      }}
    >
      {/* ─────────────────────────────────────────────────────────────
          PANEL HEADER: SNIFFER TITLE + OPERATIONAL CONTROLS (MINIMAL & SLEEK)
         ───────────────────────────────────────────────────────────── */}
      <div className="flex h-9 shrink-0 select-none items-center justify-between gap-2 border-b border-border/60 px-3 bg-surface-base/30 overflow-x-auto no-scrollbar">
        {/* Left: Sniffer Title & Stream Pulse Status (Paket pill moved to top header) */}
        <div className="flex items-center gap-2 shrink-0">
          <div className="flex items-center gap-1.5">
            <div className="flex h-5 w-5 items-center justify-center rounded-md bg-accent-soft text-accent">
              <Filter className="h-3 w-3" />
            </div>
            <span className="font-sans text-[12.5px] font-semibold text-text-hi tracking-tight">Sniffer</span>
          </div>

          <div className="h-3.5 w-px bg-border/60 mx-0.5" />

          {/* Live Stream rate / pulse status */}
          <div className="flex items-center gap-1.5 font-mono text-[11px] text-text-mid">
            <span className="relative flex h-2 w-2 items-center justify-center">
              {isEstopActive ? (
                <span className="h-2 w-2 rounded-full bg-del animate-pulse" />
              ) : isStreaming ? (
                <>
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-add opacity-75" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-add shadow-[0_0_6px_rgba(52,211,153,0.6)]" />
                </>
              ) : (
                <span className="h-2 w-2 rounded-full bg-text-faint" />
              )}
            </span>
            <span className="tabular-nums">
              {isStreaming ? `${frameRate || 42} fps` : 'durduruldu'}
            </span>
          </div>
        </div>

        {/* Right: Sleek Operational Controls (E-STOP, Başlat/Durdur, Hata, Süz, Arama, Temizle) */}
        <div className="flex items-center gap-1.5 font-sans text-[12px] shrink-0">
          {/* E-STOP BUTTON — Minimal Modern Micro-Pill */}
          {onEstop && (
            <button
              onClick={onEstop}
              disabled={isEstopActive}
              className={`flex h-7 items-center gap-1 rounded-[6px] border px-2 font-mono text-[11px] font-semibold tracking-tight transition-all duration-140 active:scale-95 cursor-pointer ${
                isEstopActive
                  ? 'border-del bg-del text-white animate-pulse shadow-sm shadow-del/30 cursor-not-allowed'
                  : 'border-del/30 bg-del/10 text-del hover:border-del/50 hover:bg-del/20'
              }`}
              title="Acil Durdurma — Tüm İletim ve Akışı Derhal Kes"
              aria-label="Acil Durdurma"
            >
              <Octagon className="h-3 w-3 shrink-0" />
              <span>E-STOP</span>
            </button>
          )}

          {/* BAŞLAT / DURDUR SPLIT BUTTON WITH SCENARIO SELECTOR — Sleek Segmented Capsule */}
          <div className="flex h-7 items-center rounded-[6px] border border-border bg-surface-inset shadow-2xs overflow-hidden">
            <button
              onClick={onToggleStreaming}
              disabled={isEstopActive}
              className={`flex h-full items-center gap-1 px-2.5 font-sans text-[11.5px] font-medium transition-colors cursor-pointer active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed ${
                isStreaming
                  ? 'bg-add/12 text-add hover:bg-add/20 font-semibold'
                  : 'text-text-hi hover:bg-bg-row-hover'
              }`}
              title={isStreaming ? 'CAN Veri Akışını Duraklat' : 'CAN Veri Akışını Başlat'}
            >
              {isStreaming ? (
                <>
                  <Pause className="h-3 w-3 shrink-0 text-add" />
                  <span>Durdur</span>
                </>
              ) : (
                <>
                  <Play className="h-3 w-3 shrink-0 text-add" />
                  <span>Başlat</span>
                </>
              )}
            </button>
            {onSelectScenario && (
              <button
                ref={scenarioBtnRef}
                onClick={() => {
                  setShowScenarioMenu((prev) => !prev);
                  setShowFaultMenu(false);
                }}
                disabled={isEstopActive}
                className={`flex h-full items-center justify-center border-l border-border px-1.5 text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${
                  showScenarioMenu ? 'bg-bg-row-selected text-text-hi' : ''
                }`}
                title="Test Senaryoları & Galerisi"
                aria-expanded={showScenarioMenu}
              >
                <ChevronDown className={`h-3 w-3 transition-transform duration-150 ${showScenarioMenu ? 'rotate-180' : ''}`} />
              </button>
            )}
          </div>

          {/* HATA ENJEKSİYONU BUTONU & DROPDOWN — Minimal Modern Pill */}
          {onInjectFault && (
            <button
              ref={faultBtnRef}
              onClick={() => {
                setShowFaultMenu((prev) => !prev);
                setShowScenarioMenu(false);
              }}
              className={`flex h-7 items-center gap-1 rounded-[6px] border px-2 font-sans text-[11.5px] font-medium transition-all active:scale-95 cursor-pointer ${
                showFaultMenu
                  ? 'border-brandamber/60 bg-brandamber/20 text-brandamber shadow-2xs'
                  : 'border-brandamber/30 bg-brandamber/8 text-brandamber hover:border-brandamber/50 hover:bg-brandamber/15'
              }`}
              title="Canlı Hatta Hata Enjeksiyonu Yap"
              aria-expanded={showFaultMenu}
            >
              <Zap className="h-3 w-3 shrink-0 text-brandamber" />
              <span>Hata</span>
              <ChevronDown className={`h-2.5 w-2.5 transition-transform duration-150 ${showFaultMenu ? 'rotate-180' : ''}`} />
            </button>
          )}

          <div className="h-3.5 w-px bg-border/60 mx-0.5" />

          {/* Hataları Süz — Minimal Toggle Pill */}
          <button
            onClick={() => setFilterAnomaliesOnly((prev) => !prev)}
            className={`flex h-7 items-center gap-1 rounded-[6px] border px-2 font-sans text-[11px] transition-all active:scale-95 cursor-pointer ${
              filterAnomaliesOnly
                ? 'border-accent/40 bg-accent-soft font-semibold text-accent'
                : 'border-border bg-surface-inset text-text-mid hover:text-text-hi hover:bg-bg-row-hover'
            }`}
            title="Arıza ve anomaliler dışındaki satırları soluklaştır"
          >
            <Filter className="h-3 w-3 shrink-0" />
            <span className="hidden sm:inline">Süz</span>
            {anomalyCount > 0 && (
              <span className="rounded bg-del/15 px-1 py-0.1 font-mono text-[9.5px] font-bold text-del">
                {anomalyCount}
              </span>
            )}
          </button>

          {/* Responsive Search Input with inline clear */}
          <div className="relative flex items-center">
            <Search className="absolute left-2 h-3 w-3 text-text-low pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filtrele..."
              className="h-7 w-20 sm:w-28 md:w-36 rounded-[6px] border border-border bg-surface-inset pl-6 pr-5 font-mono text-[11px] text-text-hi placeholder:text-text-faint transition-all hover:border-border-strong focus:w-44 focus:border-border-focus focus:outline-none"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-1.5 flex h-3.5 w-3.5 items-center justify-center rounded-full text-text-low hover:text-text-hi hover:bg-bg-row-hover transition-colors"
                title="Aramayı Temizle"
              >
                <X className="h-2.5 w-2.5" />
              </button>
            )}
          </div>

          {/* Temizle — Compact Action */}
          <button
            onClick={handleClear}
            className="flex h-7 items-center gap-1 rounded-[6px] border border-border bg-surface-inset px-2 text-[11px] text-text-mid transition-colors hover:border-del/40 hover:bg-del/10 hover:text-del active:scale-95 cursor-pointer"
            title="Sniffer tamponunu sıfırla"
          >
            <Trash2 className="h-3 w-3 shrink-0" />
            <span className="hidden md:inline">Temizle</span>
          </button>

          {/* Info Icon Button */}
          <button
            onClick={() => setShowInfoModal((prev) => !prev)}
            className="flex h-7 w-7 items-center justify-center rounded-[6px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi cursor-pointer active:scale-95"
            title="Sniffer Kısayol & Protokol Rehberi"
          >
            <Info className="h-3.5 w-3.5" />
          </button>
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
                          onSelectScenario?.(item.key);
                          setShowScenarioMenu(false);
                        }}
                        className={`group flex w-full items-start gap-2 rounded-[6px] px-2.5 py-1.5 text-left transition-colors ${
                          isSelected
                            ? 'bg-accent-soft text-accent-text'
                            : 'text-text-mid hover:bg-bg-row-hover hover:text-text-hi'
                        }`}
                      >
                        <div className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center">
                          {isSelected ? (
                            <Check className="h-3.5 w-3.5 text-accent" />
                          ) : (
                            <span className="h-1.5 w-1.5 rounded-full bg-border-strong group-hover:bg-text-low" />
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="text-[11.5px] font-semibold tracking-tight">{item.title}</div>
                          <div className="text-[10px] text-text-low leading-tight">{item.desc}</div>
                        </div>
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
          PORTAL: CANLI HATA ENJEKSİYONU DROPDOWN
         ───────────────────────────────────────────────────────────── */}
      {showFaultMenu && faultRect && createPortal(
        <div
          data-portal-menu="fault"
          style={{
            position: 'fixed',
            top: faultRect.bottom + 6,
            left: Math.max(8, Math.min(faultRect.right - 280, window.innerWidth - 288)),
            zIndex: 99999,
          }}
          className="w-[280px] rounded-[12px] glass-popover p-1.5 shadow-2xl animate-in fade-in zoom-in-95 duration-150 border border-border-strong"
        >
          <div className="flex items-center justify-between border-b border-border/60 px-3 py-2">
            <span className="text-[12px] font-bold text-text-hi">Hata Enjeksiyonu</span>
            <span className="rounded-[4px] bg-warn/20 px-1.5 py-0.5 text-[10px] font-mono font-medium text-warn">
              Canlı Simülasyon
            </span>
          </div>

          <div className="py-1 space-y-0.5">
            {FAULT_ITEMS.map((item) => (
              <button
                key={item.key}
                onClick={() => {
                  onInjectFault?.(item.key);
                  setShowFaultMenu(false);
                }}
                className="group flex w-full items-start gap-2 rounded-[6px] px-2.5 py-1.5 text-left transition-colors hover:bg-bg-row-hover"
              >
                <div className="mt-1 flex h-2 w-2 shrink-0 items-center justify-center">
                  <span className={`h-2 w-2 rounded-full ${item.dot}`} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className={`text-[11.5px] font-semibold tracking-tight ${item.color}`}>
                    {item.label}
                  </div>
                  <div className="text-[10px] text-text-low leading-tight">{item.desc}</div>
                </div>
              </button>
            ))}
          </div>
        </div>,
        document.body
      )}

      {/* ─────────────────────────────────────────────────────────────
          TABLE SCROLL REGION & ROWS
         ───────────────────────────────────────────────────────────── */}
      <div className="relative flex-1 overflow-auto">
        <table className="w-full min-w-[700px] table-fixed border-collapse font-mono text-[11.5px]">
          <thead className="sticky top-0 z-10 select-none bg-surface-base/95 backdrop-blur-md shadow-xs">
            <tr className="border-b border-border/60 text-[10.5px] font-medium text-text-low">
              <th className="w-12 px-2.5 py-1.5 text-center font-normal">DIR</th>
              <th className="w-24 px-2 py-1.5 text-left font-normal">ZAMAN</th>
              <th className="w-16 px-2 py-1.5 text-left font-normal">KANAL</th>
              <th className="w-28 px-2 py-1.5 text-left font-normal">CAN ID</th>
              <th className="w-14 px-2 py-1.5 text-center font-normal">TİP</th>
              <th className="w-12 px-2 py-1.5 text-center font-normal">DLC</th>
              <th className="px-3 py-1.5 text-left font-normal">VERİ (HEX)</th>
              <th className="w-24 px-2 py-1.5 text-left font-normal">ASCII</th>
              <th className="w-14 px-2 py-1.5 text-center font-normal">DURUM</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/20">
            {filteredRows.map((row) => {
              const isSelected = selectedRowId === row.id;
              const isFaded = filterAnomaliesOnly && !row.isAnomaly;

              return (
                <tr
                  key={row.id}
                  role="row"
                  tabIndex={0}
                  aria-selected={isSelected}
                  onClick={() => onSelectRow(row.id)}
                  onKeyDown={(e) => {
                    // M-06: klavye ile satir secimi — fare tikiyla ayni davranis.
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onSelectRow(row.id);
                    }
                  }}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setContextMenu({
                      x: e.clientX,
                      y: e.clientY,
                      row,
                    });
                  }}
                  className={`group cursor-pointer transition-colors duration-75 select-text ${
                    isSelected
                      ? 'bg-accent-soft/70'
                      : isFaded
                      ? 'opacity-25'
                      : 'hover:bg-bg-row-hover'
                  } ${row.isAnomaly ? 'bg-delbg/30 border-l-2 border-l-del' : 'border-l-2 border-l-transparent'}`}
                >
                  {/* DIR */}
                  <td className="w-12 px-2.5 py-1 text-center font-mono text-[10px]">
                    <span
                      className={`inline-block rounded-[3px] px-1 py-0.2 font-semibold ${
                        row.direction === 'TX'
                          ? 'bg-accent-soft text-accent-text'
                          : 'bg-surface-inset text-text-mid'
                      }`}
                    >
                      {row.direction}
                    </span>
                  </td>

                  {/* ZAMAN */}
                  <td className="w-24 px-2 py-1 text-text-mid tabular-nums truncate">
                    {row.timestamp}
                  </td>

                  {/* KANAL */}
                  <td className="w-16 px-2 py-1 text-text-low font-medium truncate">
                    {row.channel}
                  </td>

                  {/* CAN ID */}
                  <td className="w-28 px-2 py-1 font-bold text-text-hi truncate">
                    <span className={row.isAnomaly ? 'text-del underline decoration-del/50' : ''}>
                      {row.canId}
                    </span>
                  </td>

                  {/* TİP */}
                  <td className="w-14 px-2 py-1 text-center text-text-low text-[10px]">
                    {row.frameType}
                  </td>

                  {/* DLC */}
                  <td className="w-12 px-2 py-1 text-center text-text-mid font-semibold tabular-nums">
                    {row.dlc}
                  </td>

                  {/* VERİ (HEX) — Syntax highlighted & search highlighted */}
                  <td className="px-3 py-1 font-mono truncate">
                    <HexBytePainter bytes={row.dataBytes} isAnomalyRow={row.isAnomaly} searchHighlight={searchQuery} />
                  </td>

                  {/* ASCII */}
                  <td className="w-24 px-2 py-1 text-text-low truncate">
                    {row.ascii}
                  </td>

                  {/* DURUM */}
                  <td className="w-14 px-2 py-1 text-center">
                    {row.isAnomaly ? (
                      <span
                        className="inline-flex items-center gap-0.5 rounded-[3px] bg-delbg px-1 py-0.5 text-[9.5px] font-bold text-del"
                        title={row.anomalyDescription || 'Protokol sapması / Anomali'}
                      >
                        <AlertTriangle className="h-2.5 w-2.5" />
                        DTC
                      </span>
                    ) : (
                      <span className="text-[10px] text-text-faint">OK</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {filteredRows.length === 0 ? (
              <tr>
                <td
                  colSpan={9}
                  className="px-3 py-8 text-center font-sans text-[12px] text-text-low"
                >
                  {searchQuery
                    ? 'Filtreyle eşleşen kare yok.'
                    : 'Gösterilecek kare yok — Başlat ile akışı başlatın.'}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {/* ─────────────────────────────────────────────────────────────
          PORTAL: CONTEXT MENU (Sağ Tık Menüsü)
         ───────────────────────────────────────────────────────────── */}
      {contextMenu && createPortal(
        <div
          style={{
            position: 'fixed',
            top: Math.min(contextMenu.y, window.innerHeight - 180),
            left: Math.min(contextMenu.x, window.innerWidth - 220),
            zIndex: 99999,
          }}
          className="w-52 rounded-[8px] glass-popover p-1 shadow-2xl animate-in fade-in zoom-in-95 duration-100 border border-border-strong text-[11.5px]"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="px-2.5 py-1 text-[10px] font-mono text-text-low border-b border-border/40">
            {contextMenu.row.canId} ({contextMenu.row.channel})
          </div>

          <div className="py-0.5 space-y-0.5">
            <button
              onClick={() => {
                handleCopyRow(contextMenu.row);
                setContextMenu(null);
              }}
              className="flex w-full items-center gap-2 rounded-[5px] px-2 py-1.5 text-left text-text-mid hover:bg-bg-row-hover hover:text-text-hi"
            >
              {copiedText ? <Check className="h-3.5 w-3.5 text-add" /> : <Copy className="h-3.5 w-3.5" />}
              <span>{copiedText ? 'Kopyalandı!' : 'Hex Verisini Kopyala'}</span>
            </button>

            {onAskCopilot && (
              <button
                onClick={() => {
                  onAskCopilot(
                    `CAN ID ${contextMenu.row.canId} paketini analiz et: DLC=${contextMenu.row.dlc} Hex=${contextMenu.row.dataBytes.join(' ')} Ascii=${contextMenu.row.ascii}`
                  );
                  setContextMenu(null);
                }}
                className="flex w-full items-center gap-2 rounded-[5px] px-2 py-1.5 text-left text-accent-text bg-accent-soft/40 hover:bg-accent-soft font-medium"
              >
                <Sparkles className="h-3.5 w-3.5 text-accent" />
                <span>Copilot ile Teşhis Et</span>
              </button>
            )}

            <button
              onClick={() => {
                setSearchQuery(contextMenu.row.canId);
                setContextMenu(null);
              }}
              className="flex w-full items-center gap-2 rounded-[5px] px-2 py-1.5 text-left text-text-mid hover:bg-bg-row-hover hover:text-text-hi"
            >
              <Filter className="h-3.5 w-3.5" />
              <span>Bu ID'yi Filtrele</span>
            </button>
          </div>
        </div>,
        document.body
      )}

      {/* ─────────────────────────────────────────────────────────────
          MODAL: SNIFFER BİLGİ & KISAYOL REHBERİ
         ───────────────────────────────────────────────────────────── */}
      {showInfoModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4">
          <div className="w-[420px] rounded-[12px] glass-popover p-4 shadow-2xl border border-border-strong">
            <div className="flex items-center justify-between border-b border-border/60 pb-2.5">
              <span className="font-sans text-[13px] font-bold text-text-hi">Sniffer & Telemetri Rehberi</span>
              <button
                onClick={() => setShowInfoModal(false)}
                className="rounded-[4px] p-1 text-text-low hover:bg-bg-row-hover hover:text-text-hi"
              >
                ✕
              </button>
            </div>
            <div className="mt-3 space-y-2 text-[11.5px] text-text-mid leading-relaxed">
              <p>
                <strong className="text-text-hi">Akış & Senaryolar:</strong> Başlat/Durdur butonu yanındaki ok ile 10 farklı otomotiv, EV, marin ve CAN-FD arıza senaryosunu gerçek zamanlı hatta verebilirsiniz.
              </p>
              <p>
                <strong className="text-text-hi">Hata Enjeksiyonu:</strong> Hata menüsünden anında fiziksel hat Error Frame'i, DTC SPN 100 DM1 yayını veya Babbling Node taşması simüle edebilirsiniz.
              </p>
              <p>
                <strong className="text-text-hi">E-STOP:</strong> Kritik durumlarda tüm simülatör yayınını ve CAN TX sürücülerini mikrosaniye seviyesinde kilitler.
              </p>
              <p>
                <strong className="text-text-hi">Sağ Tık Menüsü:</strong> Herhangi bir satıra sağ tıklayarak Hex verisini panoya kopyalayabilir veya doğrudan Teşhis Copilot'una analiz ettirebilirsiniz.
              </p>
            </div>
            <div className="mt-4 flex justify-end">
              <button
                onClick={() => setShowInfoModal(false)}
                className="rounded-[6px] bg-accent-soft px-3 py-1.5 text-[11.5px] font-semibold text-accent-text transition-colors hover:bg-accent-soft/80"
              >
                Anladım
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

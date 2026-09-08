import React, { useState, useRef, useEffect } from 'react';
import { 
  Terminal, 
  Search, 
  Pause, 
  Play, 
  Trash2, 
  ArrowDownCircle,
  AlertTriangle,
  AlertCircle,
  Filter,
  Bot,
  Copy,
  EyeOff,
  Check,
  Code2,
  XCircle
} from 'lucide-react';
import { CANFrame } from '../../types/can';

interface CanSnifferTableProps {
  frames: CANFrame[];
  isStreaming: boolean;
  frameRate: number;
  totalDisplayedCount: number;
  errorFrameCount: number;
  onToggleStreaming: () => void;
  onClearBuffer: () => void;
  onAskCopilot?: (frame: CANFrame) => void;
}

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  frame: CANFrame;
}

export const CanSnifferTable: React.FC<CanSnifferTableProps> = ({
  frames,
  isStreaming,
  frameRate,
  totalDisplayedCount,
  errorFrameCount,
  onToggleStreaming,
  onClearBuffer,
  onAskCopilot
}) => {
  const [filterQuery, setFilterQuery] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const [showOnlyAnomalies, setShowOnlyAnomalies] = useState(false);
  const [ignoredCanIds, setIgnoredCanIds] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [copiedToast, setCopiedToast] = useState<string | null>(null);
  const tableContainerRef = useRef<HTMLDivElement>(null);

  // Helper to determine anomaly severity
  const getFrameAnomalyType = (frame: CANFrame): 'critical' | 'warning' | 'none' => {
    if (frame.isErrorFrame) return 'critical';
    
    const idUpper = frame.canIdHex.toUpperCase();
    
    // J1939 DM1 (Active DTCs - PGN 65226) or DM12 (Emissions DTCs - PGN 65236)
    if (idUpper.includes('18FECA') || idUpper.includes('18FED4')) {
      // Check if this is a "No Active DTCs" message (all FF or Lamp=0 and SPN=FF)
      const hasNoDtc = (frame.dataHex.length >= 6 && frame.dataHex[2] === 'FF' && frame.dataHex[3] === 'FF') ||
                       (frame.dataHex[0] === '00' && frame.dataHex[2] === 'FF');
      if (hasNoDtc) return 'none';
      return 'critical';
    }

    // UDS Negative Response (0x7F)
    if (frame.dataHex.length >= 3 && frame.dataHex[0] === '7F') {
      return 'critical';
    }

    // J1939 DM2 (Previously Active DTCs - PGN 65227) or DM3 (Clear DTCs)
    if (idUpper.includes('18FECB') || idUpper.includes('18FECC')) {
      return 'warning';
    }

    // Explicit color palette indicators
    if (frame.colorPalette?.includes('rose')) return 'critical';
    if (frame.colorPalette?.includes('amber')) return 'warning';

    return 'none';
  };

  const anomalyCount = React.useMemo(() => {
    return frames.filter(f => getFrameAnomalyType(f) !== 'none').length;
  }, [frames]);

  const filteredFrames = React.useMemo(() => {
    let result = frames;

    // Filter out ignored CAN IDs
    if (ignoredCanIds.size > 0) {
      result = result.filter(f => !ignoredCanIds.has(f.canIdHex));
    }

    if (showOnlyAnomalies) {
      result = result.filter(f => getFrameAnomalyType(f) !== 'none');
    }

    if (filterQuery.trim()) {
      const query = filterQuery.toLowerCase();
      result = result.filter(f => 
        f.canIdHex.toLowerCase().includes(query) ||
        f.dataHex.join('').toLowerCase().includes(query) ||
        f.dataHex.join(' ').toLowerCase().includes(query) ||
        f.channel.toLowerCase().includes(query) ||
        f.ascii.toLowerCase().includes(query)
      );
    }

    return result;
  }, [frames, filterQuery, showOnlyAnomalies, ignoredCanIds]);

  // Handle right click on table row
  const handleRowContextMenu = (e: React.MouseEvent, frame: CANFrame) => {
    e.preventDefault();
    e.stopPropagation();

    const menuWidth = 260;
    const menuHeight = 270;
    const x = Math.min(e.clientX, window.innerWidth - menuWidth - 10);
    const y = Math.min(e.clientY, window.innerHeight - menuHeight - 10);

    setContextMenu({
      visible: true,
      x,
      y,
      frame
    });
  };

  // Close context menu on outside click or Escape
  useEffect(() => {
    const handleOutsideClick = () => setContextMenu(null);
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setContextMenu(null);
    };

    if (contextMenu?.visible) {
      window.addEventListener('click', handleOutsideClick);
      window.addEventListener('contextmenu', handleOutsideClick);
      window.addEventListener('keydown', handleKeyDown);
    }

    return () => {
      window.removeEventListener('click', handleOutsideClick);
      window.removeEventListener('contextmenu', handleOutsideClick);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [contextMenu]);

  useEffect(() => {
    if (autoScroll && tableContainerRef.current) {
      tableContainerRef.current.scrollTop = tableContainerRef.current.scrollHeight;
    }
  }, [filteredFrames, autoScroll]);

  const showToast = (message: string) => {
    setCopiedToast(message);
    setTimeout(() => setCopiedToast(null), 2000);
  };

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    showToast(`${label} kopyalandı!`);
    setContextMenu(null);
  };

  const handleIgnoreCanId = (canId: string) => {
    setIgnoredCanIds(prev => new Set([...prev, canId]));
    showToast(`${canId} gizlendi.`);
    setContextMenu(null);
  };

  const handleClearIgnored = () => {
    setIgnoredCanIds(new Set());
    showToast('Gizlenen ID filtreleri temizlendi.');
  };

  const renderColoredByte = (byte: string, index: number, frame: CANFrame) => {
    const idUpper = frame.canIdHex.toUpperCase();

    // Critical DTC Byte Highlighting (e.g. DM1 Active Trouble Code payload)
    if (idUpper.includes('18FECA') && index >= 2 && index <= 5) {
      return (
        <span 
          key={index} 
          className="font-mono tracking-wider bg-rose-100 text-rose-800 font-bold px-1 rounded ring-1 ring-rose-300"
          title="J1939 DTC SPN/FMI Arıza Baytı"
        >
          {byte}
        </span>
      );
    }

    // UDS Negative Response (0x7F + NRC byte)
    if (frame.dataHex[0] === '7F' && (index === 0 || index === 2)) {
      return (
        <span 
          key={index} 
          className="font-mono tracking-wider bg-rose-200 text-rose-900 font-bold px-1 rounded ring-1 ring-rose-400"
          title={index === 0 ? 'Negative Response (0x7F)' : 'UDS NRC Hata Kodu'}
        >
          {byte}
        </span>
      );
    }

    // Normal Syntax Highlighting
    let colorClass = 'text-slate-700';
    if (frame.colorPalette && frame.colorPalette[index]) {
      switch (frame.colorPalette[index]) {
        case 'blue':
          colorClass = 'text-brand-600 font-bold';
          break;
        case 'indigo':
          colorClass = 'text-brand-600 font-bold';
          break;
        case 'emerald':
          colorClass = 'text-signal-600 font-bold';
          break;
        case 'amber':
          colorClass = 'text-amber-600 font-bold';
          break;
        case 'rose':
          colorClass = 'text-rose-600 font-bold';
          break;
        default:
          colorClass = 'text-slate-600 font-medium';
      }
    } else {
      if (index === 0 || index === 3) colorClass = 'text-brand-600 font-bold';
      else if (index === 1 || index === 2) colorClass = 'text-brand-600 font-bold';
      else if (index === 4) colorClass = 'text-signal-600 font-bold';
      else colorClass = 'text-slate-600 font-medium';
    }

    return (
      <span key={index} className={`font-mono tracking-wider ${colorClass}`}>
        {byte}
      </span>
    );
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-card flex flex-col h-full overflow-hidden relative">
      {/* Sniffer Header & Toolbar */}
      <div className="px-3.5 py-2 bg-white border-b border-slate-200 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center space-x-2.5">
          <div className="w-6 h-6 rounded-lg bg-slate-100 border border-slate-200 flex items-center justify-center text-slate-700">
            <Terminal className="w-3.5 h-3.5 text-brand-600 stroke-[2.2]" />
          </div>
          <div className="flex items-center space-x-2">
            <span className="text-xs font-bold tracking-tight text-slate-900">
              Sniffer
            </span>
            <span className="font-mono text-xs text-slate-500">{frameRate} kare/sn</span>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          {/* Ignored CAN IDs badge */}
          {ignoredCanIds.size > 0 && (
            <button
              onClick={handleClearIgnored}
              className="flex items-center space-x-1 px-2 py-0.5 rounded-lg bg-slate-100 hover:bg-slate-200 border border-slate-200 text-xs text-slate-600 font-medium transition-colors"
              title="Gizlenen filtreleri sıfırla"
            >
              <EyeOff className="w-3 h-3 text-slate-500" />
              <span>{ignoredCanIds.size} Gizlendi</span>
              <XCircle className="w-3 h-3 text-slate-500 hover:text-slate-600 ml-0.5" />
            </button>
          )}

          {/* Anomaly Quick Filter Button */}
          <button
            onClick={() => setShowOnlyAnomalies(!showOnlyAnomalies)}
            className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-all ${
              showOnlyAnomalies
                ? 'bg-rose-100 border-rose-300 text-rose-800 shadow-xs ring-1 ring-rose-300'
                : anomalyCount > 0
                  ? 'bg-amber-50 border-amber-200 text-amber-800 hover:bg-amber-100'
                  : 'bg-slate-50 border-slate-200 text-slate-500 hover:bg-slate-100'
            }`}
            title="Sadece arıza, DTC ve anormal kareleri süz"
          >
            {showOnlyAnomalies ? (
              <AlertTriangle className="w-3.5 h-3.5 text-rose-600 animate-pulse" />
            ) : (
              <Filter className="w-3.5 h-3.5 text-slate-500" />
            )}
            <span>
              {showOnlyAnomalies ? 'Tümünü Göster' : `Hataları Süz (${anomalyCount})`}
            </span>
          </button>

          {/* Search Filter Input */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              placeholder="CAN ID veya Hex filtrele..."
              className="pl-8 pr-3 py-1 text-xs font-mono bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-500/20 focus:border-brand-500 w-36 md:w-48 transition-all"
            />
          </div>

          <button
            onClick={onToggleStreaming}
            className={`flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors ${
              isStreaming
                ? 'bg-slate-50 border-slate-200 text-slate-700 hover:bg-slate-100'
                : 'bg-signal-50 border-signal-200 text-signal-700 hover:bg-signal-100'
            }`}
          >
            {isStreaming ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
            <span className="hidden sm:inline">{isStreaming ? 'Duraklat' : 'Devam'}</span>
          </button>

          <button
            onClick={onClearBuffer}
            className="flex items-center space-x-1 px-2.5 py-1 rounded-lg border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100 text-xs font-medium transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5 text-slate-500" />
            <span className="hidden sm:inline">Temizle</span>
          </button>

          <button
            onClick={() => setAutoScroll(!autoScroll)}
            className={`p-1 rounded-lg border text-xs transition-colors ${
              autoScroll
                ? 'bg-brand-50 border-brand-200 text-brand-600'
                : 'bg-slate-50 border-slate-200 text-slate-500 hover:text-slate-600'
            }`}
            title="Otomatik Kaydır"
          >
            <ArrowDownCircle className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Table Container */}
      <div 
        ref={tableContainerRef}
        className="flex-1 overflow-y-auto overflow-x-auto text-xs font-mono select-text bg-white"
      >
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 bg-slate-50/95 backdrop-blur-xs border-b border-slate-200 text-xs font-semibold text-slate-600 tracking-normal z-10">
            <tr>
              <th className="py-1.5 px-3 w-24">Zaman (s)</th>
              <th className="py-1.5 px-2.5 w-16">Kanal</th>
              <th className="py-1.5 px-3 w-32">CAN ID</th>
              <th className="py-1.5 px-2 w-14">Tip</th>
              <th className="py-1.5 px-2 w-12 text-center">Yön</th>
              <th className="py-1.5 px-2 w-10 text-center">DLC</th>
              <th className="py-1.5 px-3 w-[210px] whitespace-nowrap">Veri (Hex Payload)</th>
              <th className="py-1.5 px-3 whitespace-nowrap">ASCII</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {filteredFrames.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-12 text-center text-slate-500 font-sans text-xs">
                  <div className="flex flex-col items-center justify-center space-y-2">
                    <Terminal className="w-8 h-8 text-slate-300 stroke-1" />
                    <span className="font-semibold text-slate-600">CAN hattı dinleniyor...</span>
                    <span className="text-xs text-slate-500">Canlı donanım bağlantısı kurulduğunda veya simülasyon başlatıldığında paketler burada akacaktır.</span>
                  </div>
                </td>
              </tr>
            ) : (
              filteredFrames.map((frame) => {
              const anomaly = getFrameAnomalyType(frame);
              const rowClass = 
                anomaly === 'critical'
                  ? 'bg-rose-50/80 hover:bg-rose-100/90 border-l-[3.5px] border-l-rose-500'
                  : anomaly === 'warning'
                    ? 'bg-amber-50/80 hover:bg-amber-100/90 border-l-[3.5px] border-l-amber-500'
                    : 'hover:bg-slate-50/80 border-l-[3.5px] border-l-transparent';

              return (
                <tr 
                  key={frame.id} 
                  onContextMenu={(e) => handleRowContextMenu(e, frame)}
                  className={`transition-colors leading-relaxed cursor-context-menu ${rowClass}`}
                >
                  <td className="py-1 px-3 text-slate-500 font-mono">
                    {frame.timeFormatted}
                  </td>
                  <td className="py-1 px-2.5 text-brand-600 font-medium">
                    {frame.channel}
                  </td>
                <td className="py-1 px-3 font-bold">
                  {anomaly === 'critical' ? (
                    <div className="flex items-center space-x-1 text-rose-700">
                      <AlertTriangle className="w-3.5 h-3.5 text-rose-600 shrink-0" />
                      <span>{frame.canIdHex}</span>
                      <span className="font-sans text-xs font-semibold text-rose-700">DTC</span>
                    </div>
                  ) : anomaly === 'warning' ? (
                    <div className="flex items-center space-x-1 text-amber-700">
                      <AlertCircle className="w-3.5 h-3.5 text-amber-600 shrink-0" />
                      <span>{frame.canIdHex}</span>
                    </div>
                  ) : (
                    <span className="text-brand-700">{frame.canIdHex}</span>
                  )}
                </td>
                <td className="py-1 px-2 text-slate-500">
                  {frame.isCanFd || frame.frameType === 'FD' ? (
                    <span className="font-semibold text-brand-700">FD</span>
                  ) : (
                    frame.frameType
                  )}
                </td>
                <td className="py-1 px-2 text-center text-xs font-semibold">
                  <span
                    className={
                      frame.dir === 'RX' ? 'text-signal-700' : 'text-amber-700'
                    }
                  >
                    {frame.dir}
                  </span>
                </td>
                  <td className="py-1 px-2 text-center text-slate-600 font-semibold">
                    {frame.dlc}
                  </td>
                  <td className="py-1 px-3 space-x-1.5 whitespace-nowrap w-[210px]">
                    {frame.dataHex.map((byte, idx) => 
                      renderColoredByte(byte, idx, frame)
                    )}
                  </td>
                  <td className="py-1 px-3 text-slate-500 tracking-wider whitespace-nowrap">
                    {frame.ascii}
                  </td>
                </tr>
              );
            }))}
          </tbody>
        </table>
      </div>

      {/* Sniffer Footer Metrics */}
      <div className="px-3.5 py-1.5 bg-slate-50 border-t border-slate-200 flex items-center justify-between text-xs text-slate-500 font-mono">
        <div className="flex items-center space-x-4">
          <span>Toplam Gösterilen: <strong className="text-slate-700">{totalDisplayedCount}</strong></span>
          <span>
            Arıza/Anomali Kareleri:{' '}
            <strong className={anomalyCount > 0 ? 'text-rose-600' : 'text-slate-700'}>
              {anomalyCount}
            </strong>
          </span>
          <span>Hata Kareleri (Bus Errors): <strong className={errorFrameCount > 0 ? 'text-rose-600' : 'text-slate-700'}>{errorFrameCount}</strong></span>
        </div>
        <div className="flex items-center space-x-1.5 text-xs text-slate-500 font-sans">
          <span className="w-1.5 h-1.5 rounded-full bg-signal-500"></span>
          <span>Sağ tık ile kare menüsünü açın</span>
        </div>
      </div>

      {/* Context Menu Popup */}
      {contextMenu && (
        <div
          style={{ 
            position: 'fixed', 
            left: `${contextMenu.x}px`, 
            top: `${contextMenu.y}px`, 
            zIndex: 9999 
          }}
          className="w-64 bg-white/95 backdrop-blur-md border border-slate-200 rounded-xl shadow-2xl py-1.5 text-xs text-slate-700 font-sans animate-in fade-in zoom-in-95 duration-100 select-none"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header Info */}
          <div className="px-3 py-1.5 border-b border-slate-100 flex items-center justify-between">
            <span className="font-mono font-bold text-brand-700">{contextMenu.frame.canIdHex}</span>
            <span className="text-xs text-slate-500 font-mono">{contextMenu.frame.channel} • {contextMenu.frame.dir}</span>
          </div>

          <div className="py-1">
            {/* 1. Ask Copilot */}
            {onAskCopilot && (
              <button
                onClick={() => {
                  onAskCopilot(contextMenu.frame);
                  setContextMenu(null);
                }}
                className="w-full px-3 py-1.5 flex items-center space-x-2 hover:bg-brand-50 text-brand-700 font-semibold transition-colors text-left"
              >
                <Bot className="w-4 h-4 text-brand-600 shrink-0" />
                <span>AI Copilot'a Analiz Ettir</span>
              </button>
            )}

            <div className="my-1 border-t border-slate-100"></div>

            {/* 2. Filter by this CAN ID */}
            <button
              onClick={() => {
                setFilterQuery(contextMenu.frame.canIdHex);
                setContextMenu(null);
              }}
              className="w-full px-3 py-1.5 flex items-center space-x-2 hover:bg-slate-50 text-slate-700 transition-colors text-left"
            >
              <Search className="w-3.5 h-3.5 text-slate-500 shrink-0" />
              <span>Bu CAN ID'ye Göre Filtrele</span>
            </button>

            {/* 3. Hide this CAN ID */}
            <button
              onClick={() => handleIgnoreCanId(contextMenu.frame.canIdHex)}
              className="w-full px-3 py-1.5 flex items-center space-x-2 hover:bg-rose-50 text-rose-700 transition-colors text-left"
            >
              <EyeOff className="w-3.5 h-3.5 text-rose-500 shrink-0" />
              <span>Bu CAN ID'yi Gizle</span>
            </button>

            <div className="my-1 border-t border-slate-100"></div>

            {/* 4. Copy CAN ID */}
            <button
              onClick={() => copyToClipboard(contextMenu.frame.canIdHex, 'CAN ID')}
              className="w-full px-3 py-1.5 flex items-center space-x-2 hover:bg-slate-50 text-slate-700 transition-colors text-left"
            >
              <Copy className="w-3.5 h-3.5 text-slate-500 shrink-0" />
              <span>CAN ID Kopyala</span>
            </button>

            {/* 5. Copy Hex Payload */}
            <button
              onClick={() => copyToClipboard(contextMenu.frame.dataHex.join(' '), 'Hex Payload')}
              className="w-full px-3 py-1.5 flex items-center space-x-2 hover:bg-slate-50 text-slate-700 transition-colors text-left"
            >
              <Code2 className="w-3.5 h-3.5 text-slate-500 shrink-0" />
              <span>Hex Payload Kopyala</span>
            </button>

            {/* 6. Copy Vector .ASC Line */}
            <button
              onClick={() => {
                const ascLine = `${contextMenu.frame.timeFormatted} ${contextMenu.frame.channel} ${contextMenu.frame.canIdHex} ${contextMenu.frame.dir} d ${contextMenu.frame.dlc} ${contextMenu.frame.dataHex.join(' ')}`;
                copyToClipboard(ascLine, 'Vector .ASC satırı');
              }}
              className="w-full px-3 py-1.5 flex items-center space-x-2 hover:bg-slate-50 text-slate-700 transition-colors text-left"
            >
              <Copy className="w-3.5 h-3.5 text-slate-500 shrink-0" />
              <span>Tüm Satırı Kopyala (.ASC)</span>
            </button>
          </div>
        </div>
      )}

      {/* Copied Toast Alert */}
      {copiedToast && (
        <div className="absolute bottom-10 right-4 z-50 bg-slate-900/90 backdrop-blur-md text-white text-xs font-sans px-3 py-1.5 rounded-lg shadow-lg flex items-center space-x-2 animate-in fade-in slide-in-from-bottom-2 duration-150">
          <Check className="w-3.5 h-3.5 text-signal-400" />
          <span>{copiedToast}</span>
        </div>
      )}
    </div>
  );
};

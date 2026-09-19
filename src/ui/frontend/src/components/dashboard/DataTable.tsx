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
} from 'lucide-react';
import { CanPacketRow } from '../../data/constants';
import { HexBytePainter } from './HexBytePainter';

interface DataTableProps {
  rows: CanPacketRow[];
  isStreaming: boolean;
  frameRate: number;
  onToggleStreaming: () => void;
  onClearBuffer: () => void;
  selectedRowId: string | null;
  onSelectRow: (id: string | null) => void;
  onAskCopilot?: (prompt: string) => void;
}

export const DataTable: React.FC<DataTableProps> = ({
  rows,
  isStreaming,
  frameRate = 0,
  onToggleStreaming,
  onClearBuffer,
  selectedRowId,
  onSelectRow,
  onAskCopilot,
}) => {
  const [filterAnomaliesOnly, setFilterAnomaliesOnly] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [showInfoModal, setShowInfoModal] = useState(false);
  const [isClearing, setIsClearing] = useState(false);

  // Context menu popover state
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    row: CanPacketRow;
  } | null>(null);
  const [copiedText, setCopiedText] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);

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

  // Close context menu on outside click or Escape
  useEffect(() => {
    const handleClick = () => setContextMenu(null);
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setContextMenu(null);
    };
    window.addEventListener('click', handleClick);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('click', handleClick);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

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
        // Deselect if clicking on empty table background
        if (e.target === containerRef.current) {
          onSelectRow(null);
        }
      }}
    >
      {/* Panel Header — seamless integration on frosted glass-panel */}
      <div className="flex h-11 shrink-0 select-none items-center justify-between border-b border-border/60 px-3.5">
        {/* Left: Section Title & Live Rate */}
        <div className="flex items-center gap-2">
          <div className="flex h-5 w-5 items-center justify-center rounded-md bg-accent-soft text-accent">
            <Filter className="h-3 w-3" />
          </div>
          <span className="font-sans text-[13px] font-semibold text-text-hi">Sniffer</span>
          <span className="font-mono text-[11.5px] text-text-mid">
            {isStreaming ? `${frameRate || 42} kare/sn` : '0 kare/sn'}
          </span>
        </div>

        {/* Right: Controls Cluster */}
        <div className="flex items-center gap-2 font-sans text-[12px]">
          {/* Hataları Süz — accent outline+tint, kesik 6px */}
          <button
            onClick={() => setFilterAnomaliesOnly((prev) => !prev)}
            className={`flex items-center gap-1.5 rounded-[6px] border px-2.5 py-1 font-mono text-[11px] transition-all active:scale-[0.98] ${
              filterAnomaliesOnly
                ? 'border-accent-line bg-accent-soft font-semibold text-accent-text'
                : 'border-border text-text-mid hover:text-text-hi hover:bg-bg-row-hover'
            }`}
            title="Arıza ve anomaliler dışındaki satırları soluklaştır"
          >
            <Filter className="h-3 w-3" />
            <span>Hataları Süz ({anomalyCount})</span>
          </button>

          {/* Search Input — white inset, kesik 8px */}
          <div className="relative flex items-center">
            <Search className="absolute left-2.5 h-3.5 w-3.5 text-text-low pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="CAN ID veya Hex filtri"
              className="h-7 w-44 rounded-[8px] border border-surface-inset-border bg-surface-inset pl-8 pr-2.5 font-mono text-[11.5px] text-text-hi placeholder:text-text-faint transition-colors hover:border-border-strong focus:border-border-focus focus:outline-none"
            />
          </div>

          {/* Devam / Durdur — add outline+tint, kesik 6px */}
          <button
            onClick={onToggleStreaming}
            className="flex items-center gap-1.5 rounded-[6px] border border-addedge/50 bg-addbg px-2.5 py-1 font-medium text-add transition-colors hover:bg-addedge/20 active:scale-[0.98]"
            title={isStreaming ? 'Veri akışını duraklat' : 'Veri akışını sürdür'}
          >
            {isStreaming ? (
              <>
                <Pause className="h-3.5 w-3.5" />
                <span>Durdur</span>
              </>
            ) : (
              <>
                <Play className="h-3.5 w-3.5" />
                <span>Devam</span>
              </>
            )}
          </button>

          {/* Temizle — ghost muted, kesik 6px */}
          <button
            onClick={handleClear}
            className="flex items-center gap-1.5 rounded-[6px] border border-border px-2 py-1 font-medium text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-[0.98]"
            title="Sniffer tamponunu temizle"
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span>Temizle</span>
          </button>

          {/* Info Icon-Button — cuttled square */}
          <button
            onClick={() => setShowInfoModal((prev) => !prev)}
            className="flex h-6 w-6 items-center justify-center rounded-[6px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi"
            title="Sniffer Kısayol & Protokol Rehberi"
          >
            <Info className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Info Popover Modal */}
      {showInfoModal && (
        <div className="absolute right-3 top-12 z-30 w-72 rounded-[10px] glass-popover p-3 shadow-xl text-[12px] text-text-body">
          <div className="flex items-center justify-between border-b border-border-whisper pb-1.5 mb-2">
            <span className="font-semibold text-text-hi">Sniffer Bilgi & Kısayollar</span>
            <button
              onClick={() => setShowInfoModal(false)}
              className="text-text-low hover:text-text-hi"
            >
              ✕
            </button>
          </div>
          <ul className="space-y-1.5 font-sans text-[11px] text-text-mid">
            <li><strong className="text-text-hi">Space:</strong> Akışı Başlat / Durdur</li>
            <li><strong className="text-text-hi">Esc:</strong> Arama ve Seçimi Temizle</li>
            <li><strong className="text-text-hi">Sağ Tık:</strong> Kare kopyalama ve detay menüsü</li>
            <li><strong className="text-del">Kırmızı Satırlar:</strong> DTC / Anomali uyarılı mesajlar</li>
            <li><strong className="text-text-mid">RX / TX:</strong> Alınan ve iletilen paket yönü</li>
          </ul>
        </div>
      )}

      {/* Table Container — denser legible data surface (FIX 2 guard) */}
      <div className="flex-1 overflow-auto glass-table-body">
        <table className="w-full border-collapse text-left select-none">
          {/* Sticky Header — glass-chrome, SANS micro-labels, ONE bottom hairline, no dividers */}
          <thead className="sticky top-0 z-10 border-b border-border-whisper glass-thead select-none">
            <tr className="font-sans text-[10px] font-semibold uppercase tracking-[0.08em] text-text-low">
              <th className="px-3 py-1.5 w-24 font-semibold">Zaman (s)</th>
              <th className="px-2.5 py-1.5 w-20 font-semibold">Kanal</th>
              <th className="px-3 py-1.5 w-32 font-semibold">CAN ID</th>
              <th className="px-2 py-1.5 w-14 font-semibold">Tür</th>
              <th className="px-2.5 py-1.5 w-14 font-semibold">Yön</th>
              <th className="px-2 py-1.5 w-12 text-center font-semibold">DLC</th>
              <th className="px-3 py-1.5 font-semibold">Veri (Hex Payload)</th>
              <th className="px-3 py-1.5 w-28 font-semibold">ASCII</th>
            </tr>
          </thead>

          {/* Table Body — zeron DIFF ROW GRAMMAR: no separators; context rows transparent,
              anomaly = del band + 2px del edge + del gutter + del CAN-ID */}
          <tbody className="font-mono text-[11.5px] leading-[1.35]">
            {filteredRows.length === 0 ? (
              <tr>
                <td
                  colSpan={8}
                  className="py-12 text-center font-sans text-[12.5px] text-text-low"
                >
                  Filtreye uygun CAN karesi bulunamadı.
                </td>
              </tr>
            ) : (
              filteredRows.map((row) => {
                const isSelected = selectedRowId === row.id;
                const isDimmed = filterAnomaliesOnly && !row.isAnomaly;

                return (
                  <tr
                    key={row.id}
                    onClick={() => onSelectRow(row.id)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      const menuWidth = 230;
                      const menuHeight = 220;
                      const x = Math.max(8, Math.min(e.clientX, window.innerWidth - menuWidth - 8));
                      const y = Math.max(8, Math.min(e.clientY, window.innerHeight - menuHeight - 8));
                      setContextMenu({
                        x,
                        y,
                        row,
                      });
                      onSelectRow(row.id);
                    }}
                    className={`group relative h-[28px] cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-bg-row-selected shadow-[inset_2px_0_0_var(--accent)]'
                        : row.isAnomaly
                        ? 'bg-delbg shadow-[inset_2px_0_0_var(--del)]'
                        : 'hover:bg-bg-row-hover'
                    } ${isDimmed ? 'opacity-35 grayscale' : 'opacity-100'}`}
                  >
                    {/* Zaman gutter — diff line-number: del on anomaly rows */}
                    <td className={`px-3 py-[5px] tracking-tight whitespace-nowrap ${row.isAnomaly ? 'text-del' : 'text-text-low'}`}>
                      {row.timestamp}
                    </td>

                    {/* Kanal — link/path color */}
                    <td className="px-2.5 py-[5px] whitespace-nowrap">
                      <span className="text-accent-text hover:underline hover:underline-offset-2 cursor-pointer">
                        {row.channel}
                      </span>
                    </td>

                    {/* CAN ID — del mono + trace ⚠ on anomaly rows */}
                    <td className="px-3 py-[5px] whitespace-nowrap">
                      <div className="flex items-center gap-1.5 font-medium">
                        {row.isAnomaly && (
                          <AlertTriangle className="h-3 w-3 shrink-0 text-del" />
                        )}
                        <span
                          className={
                            row.isAnomaly ? 'text-del font-semibold' : 'text-text-hi'
                          }
                        >
                          {row.canId}
                        </span>
                      </div>
                    </td>

                    {/* Tür — plain mono mid, NO tag */}
                    <td className="px-2 py-[5px] text-text-mid whitespace-nowrap">
                      {row.frameType}
                    </td>

                    {/* Yön — plain mono mid text, NO teal, NO chip */}
                    <td className="px-2.5 py-[5px] whitespace-nowrap text-text-mid">
                      {row.direction}
                    </td>

                    {/* DLC gutter — del on anomaly rows */}
                    <td className={`px-2 py-[5px] text-center whitespace-nowrap ${row.isAnomaly ? 'text-del' : 'text-text-mid'}`}>
                      {row.dlc}
                    </td>

                    {/* Veri (Hex Payload) via HexBytePainter — monochrome, warn only on anomaly rows (FIX 5) */}
                    <td className="px-3 py-[5px] whitespace-nowrap">
                      <HexBytePainter
                        bytes={row.dataBytes}
                        searchHighlight={searchQuery}
                        isAnomalyRow={row.isAnomaly}
                      />
                    </td>

                    {/* ASCII */}
                    <td className="px-3 py-[5px] text-text-low whitespace-nowrap select-text">
                      {row.ascii}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Right-Click Context Menu — rendered via Portal into document.body to prevent parent backdrop-filter coordinate offsets */}
      {contextMenu &&
        createPortal(
          <div
            style={{ top: `${contextMenu.y}px`, left: `${contextMenu.x}px` }}
            className="fixed z-[9999] min-w-[220px] rounded-[10px] glass-popover p-1.5 shadow-2xl font-sans text-[12px] text-text-body select-none"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header Info */}
            <div className="border-b border-border/40 px-2.5 py-1.5 font-mono text-[11px] font-semibold text-text-hi flex items-center justify-between">
              <span>Kare: {contextMenu.row.canId}</span>
              <span className="text-text-low text-[10px]">{contextMenu.row.frameType} · {contextMenu.row.channel}</span>
            </div>

            {contextMenu.row.anomalyDescription && (
              <div className="px-2.5 py-1 font-mono text-[10.5px] text-del border-b border-border/40">
                {contextMenu.row.anomalyDescription}
              </div>
            )}

            <div className="py-1 space-y-0.5">
              {/* 1. Ask Copilot to Analyze */}
              {onAskCopilot && (
                <button
                  onClick={() => {
                    const anomalyInfo = contextMenu.row.anomalyDescription ? ` Anomali Tespiti: ${contextMenu.row.anomalyDescription}.` : '';
                    const prompt = `CAN ID ${contextMenu.row.canId} (${contextMenu.row.channel}, DLC: ${contextMenu.row.dlc}, DATA: ${contextMenu.row.dataBytes.join(' ')}) karesini analiz et.${anomalyInfo} Bu kare ne anlama geliyor ve teşhis adımları nelerdir?`;
                    onAskCopilot(prompt);
                    setContextMenu(null);
                  }}
                  className="flex w-full items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-left font-semibold text-accent-text hover:bg-accent-soft hover:text-accent transition-colors"
                >
                  <Sparkles className="h-3.5 w-3.5 text-accent shrink-0" />
                  <span>Copilot'a Analiz Ettir</span>
                </button>
              )}

              {/* 2. Filter by this CAN ID */}
              <button
                onClick={() => {
                  setSearchQuery(contextMenu.row.canId);
                  setContextMenu(null);
                }}
                className="flex w-full items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-left hover:bg-bg-row-hover hover:text-text-hi transition-colors"
              >
                <Filter className="h-3.5 w-3.5 text-text-low shrink-0" />
                <span>Bu ID ile Filtrele</span>
              </button>

              <div className="my-1 border-t border-border/40" />

              {/* 3. Copy Hex & ASCII */}
              <button
                onClick={() => {
                  handleCopyRow(contextMenu.row);
                  setTimeout(() => setContextMenu(null), 300);
                }}
                className="flex w-full items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-left hover:bg-bg-row-hover hover:text-text-hi transition-colors"
              >
                {copiedText ? (
                  <Check className="h-3.5 w-3.5 text-add shrink-0" />
                ) : (
                  <Copy className="h-3.5 w-3.5 text-text-low shrink-0" />
                )}
                <span>{copiedText ? 'Kopyalandı' : 'Hex & ASCII Kopyala'}</span>
              </button>

              {/* 4. Copy CAN ID */}
              <button
                onClick={() => {
                  navigator.clipboard.writeText(contextMenu.row.canId);
                  setCopiedText(true);
                  setTimeout(() => {
                    setCopiedText(false);
                    setContextMenu(null);
                  }, 300);
                }}
                className="flex w-full items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-left hover:bg-bg-row-hover hover:text-text-hi transition-colors"
              >
                <Copy className="h-3.5 w-3.5 text-text-low shrink-0" />
                <span>CAN ID Kopyala</span>
              </button>
            </div>
          </div>,
          document.body
        )}
    </div>
  );
};

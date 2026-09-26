import React from 'react';
import {
  Minus,
  Square,
  X,
  Sun,
  Moon,
  PanelRight,
  PanelLeft,
} from 'lucide-react';
import { DesktopBridge } from '../services/bridge';

interface ToolbarProps {
  channel?: string;
  baudRate?: string;
  activeTab?: string;
  isSimulating?: boolean;
  isCopilotOpen?: boolean;
  isRailOpen?: boolean;
  onToggleRail?: () => void;
  theme?: 'dark' | 'light';
  onToggleTheme?: () => void;
  onToggleCopilot?: () => void;
}

export const TAB_LABELS: Record<string, string> = {
  dashboard: 'Dashboard',
  signal_discovery: 'Reverse Engineer',
  ecu_flashing: 'ECU Flashing',
  pinout_guide: 'Pinout Rehberi',
  reports: 'Rapor & Export',
  settings: 'Ayarlar',
};

export const Toolbar: React.FC<ToolbarProps> = ({
  channel = 'vcan0',
  baudRate = '250 kbps',
  activeTab = 'dashboard',
  isSimulating = false,
  isCopilotOpen = false,
  isRailOpen = false,
  onToggleRail,
  theme = 'dark',
  onToggleTheme,
  onToggleCopilot,
}) => {
  const currentTabLabel = TAB_LABELS[activeTab] || 'Dashboard';

  return (
    <div
      className="pywebview-drag-region flex h-8 w-full shrink-0 select-none items-center justify-between bg-transparent px-3 pt-1 cursor-move"
      style={{ zIndex: 20 }}
    >
      {/* ─────────────────────────────────────────────────────────────
          LEFT: MENU TOGGLE ICON (WHEN CLOSED) + ACTIVE TAB
         ───────────────────────────────────────────────────────────── */}
      <div
        className={`flex items-center gap-2 min-w-0 transition-opacity duration-150 ${
          !isRailOpen ? 'opacity-100 delay-100' : 'opacity-0 pointer-events-none'
        }`}
      >
        {onToggleRail && (
          <button
            onClick={onToggleRail}
            className="flex h-6 w-6 items-center justify-center rounded-[5px] text-text-mid hover:bg-bg-row-hover hover:text-text-hi transition-all active:scale-95 cursor-pointer"
            title="Gezinme Menüsünü Aç"
            aria-label="Gezinme Menüsü"
            style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
          >
            <PanelLeft className="h-3.5 w-3.5" />
          </button>
        )}
        <span className="font-sans text-[12px] font-semibold text-text-hi tracking-tight truncate select-none">
          {currentTabLabel}
        </span>
      </div>

      {/* ─────────────────────────────────────────────────────────────
          RIGHT: FLOATING THEME + COPILOT + WINDOW CONTROLS (NO BORDERS)
         ───────────────────────────────────────────────────────────── */}
      <div
        className="flex items-center gap-1.5"
        style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
      >
        {/* THEME TOGGLE */}
        {onToggleTheme && (
          <button
            onClick={onToggleTheme}
            className="flex h-7 w-7 items-center justify-center rounded-[5px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95"
            title={theme === 'dark' ? 'Açık Temaya Geç (Zeron Light)' : 'Koyu Temaya Geç (Linear Obsidian)'}
            aria-label="Toggle Theme"
          >
            {theme === 'dark' ? (
              <Sun className="h-3.5 w-3.5 text-brandamber" />
            ) : (
              <Moon className="h-3.5 w-3.5 text-accent" />
            )}
          </button>
        )}

        {/* COPILOT DRAWER TOGGLE */}
        {onToggleCopilot && (
          <button
            onClick={onToggleCopilot}
            aria-label={isCopilotOpen ? 'Teşhis Copilot Çekmecesini Kapat' : 'Teşhis Copilot Çekmecesini Aç'}
            title={isCopilotOpen ? 'Teşhis Copilot Çekmecesini Kapat' : 'Teşhis Copilot Çekmecesini Aç'}
            className={`flex h-7 w-7 items-center justify-center rounded-[5px] transition-all active:scale-95 ${
              isCopilotOpen
                ? 'bg-accent-soft text-accent-text'
                : 'text-text-mid hover:bg-bg-row-hover hover:text-text-hi'
            }`}
          >
            <PanelRight className="h-3.5 w-3.5" />
          </button>
        )}

        {/* Window controls — Minimalist IDE cluster (No dividing line) */}
        <div className="flex items-center gap-0.5 ml-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              DesktopBridge.minimizeWindow();
            }}
            className="flex h-6 w-7 items-center justify-center rounded-[4px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95"
            title="Simge Durumuna Küçült"
            aria-label="Minimize"
          >
            <Minus className="h-3 w-3" />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              DesktopBridge.maximizeWindow();
            }}
            className="flex h-6 w-7 items-center justify-center rounded-[4px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95"
            title="Ekranı Kapla / Geri Yükle"
            aria-label="Maximize"
          >
            <Square className="h-2.5 w-2.5" />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              DesktopBridge.closeWindow();
            }}
            className="flex h-6 w-7 items-center justify-center rounded-[4px] text-text-low transition-colors hover:bg-delbg hover:text-del active:scale-95"
            title="Pencereyi Kapat"
            aria-label="Close"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      </div>
    </div>
  );
};

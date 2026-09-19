import React from 'react';
import { Terminal, Minus, Square, X } from 'lucide-react';
import { DesktopBridge } from '../services/bridge';

interface WindowChromeProps {
  onMinimize?: () => void;
  onMaximize?: () => void;
  onClose?: () => void;
}

export const WindowChrome: React.FC<WindowChromeProps> = ({
  onMinimize = () => DesktopBridge.minimizeWindow(),
  onMaximize = () => DesktopBridge.maximizeWindow(),
  onClose = () => DesktopBridge.closeWindow(),
}) => {
  return (
    <header className="pywebview-drag-region flex h-9 w-full shrink-0 select-none items-center justify-between border-b border-border-whisper bg-bg-chrome/85 px-3 backdrop-blur-xl chrome-highlight cursor-move">
      {/* Left: App Glyph & Title (Drag region) */}
      <div className="flex items-center gap-2 pointer-events-none">
        <div className="flex h-5 w-5 items-center justify-center rounded-[6px] bg-accent-soft text-accent">
          <Terminal className="h-3 w-3" />
        </div>
        <span className="text-[12px] font-medium tracking-tight text-text-mid">
          Universal CAN Bus Diagnostic & Telemetry Tool <span className="text-text-low font-mono text-[11px]">v13.0</span>
        </span>
      </div>

      {/* Right: Window Controls (Clickable, no drag) */}
      <div className="flex items-center gap-1 pointer-events-auto">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onMinimize();
          }}
          className="flex h-6 w-7 items-center justify-center rounded-[6px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95"
          title="Simge Durumuna Küçült"
          aria-label="Minimize"
        >
          <Minus className="h-3 w-3" />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onMaximize();
          }}
          className="flex h-6 w-7 items-center justify-center rounded-[6px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95"
          title="Ekranı Kapla / Geri Yükle"
          aria-label="Maximize"
        >
          <Square className="h-2.5 w-2.5" />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          className="flex h-6 w-7 items-center justify-center rounded-[6px] text-text-low transition-colors hover:bg-danger/20 hover:text-danger active:scale-95"
          title="Pencereyi Kapat"
          aria-label="Close"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    </header>
  );
};

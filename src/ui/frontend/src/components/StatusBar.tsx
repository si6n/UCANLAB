import React from 'react';

interface StatusBarProps {
  channel?: string;
  isSimulating?: boolean;
  isEstopActive?: boolean;
  nodeName?: string;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  channel = 'vcan0',
  isSimulating = false,
  isEstopActive = false,
  nodeName = 'DESKTOP-CAN-NODE',
}) => {
  return (
    <footer className="relative flex h-7 w-full shrink-0 select-none items-center justify-between border-t border-border/60 glass-chrome px-3 font-mono text-[10.5px] text-text-low" style={{ zIndex: 20 }}>
      {/* Left: Operational State Dot + Status Text */}
      <div className="flex items-center gap-2">
        <span
          className={`status-dot shrink-0 ${
            isEstopActive
              ? 'status-dot-danger animate-pulse'
              : isSimulating
              ? 'status-dot-ok animate-pulse'
              : 'status-dot-idle'
          }`}
        />
        <span className="font-medium text-text-mid">
          {isEstopActive
            ? 'E-STOP (DURDURULDU)'
            : isSimulating
            ? 'Çalışıyor'
            : 'Durduruldu'}
        </span>
        <span className="text-text-faint">·</span>
        <span>
          ISO 26262 ASIL-D Watchdog: <strong className="font-normal text-add">Aktif (800ms)</strong>
        </span>
      </div>

      {/* Right: Channel & Node Identity */}
      <div className="flex items-center gap-2 text-text-low">
        <span>Kanal: <strong className="text-accent-text font-normal">{channel}</strong></span>
        <span className="text-text-faint">|</span>
        <span>Düğüm: <strong className="text-text-mid font-normal">{nodeName}</strong></span>
      </div>
    </footer>
  );
};

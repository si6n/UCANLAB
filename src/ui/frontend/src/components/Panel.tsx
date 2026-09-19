import React from 'react';

interface PanelProps {
  icon?: React.ReactNode;
  title: string;
  suffix?: string;
  headerControls?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export const Panel: React.FC<PanelProps> = ({
  icon,
  title,
  suffix,
  headerControls,
  children,
  className = '',
}) => {
  return (
    <section
      className={`flex flex-col overflow-hidden rounded-[12px] border border-border-whisper bg-bg-panel ${className}`}
    >
      {/* Panel Header: h-10, px-3, border-b, elevated chrome strip */}
      <div className="flex h-10 shrink-0 select-none items-center justify-between border-b border-border-whisper bg-bg-chrome px-3 chrome-highlight">
        {/* Left: Icon Tile + Title + Live Suffix */}
        <div className="flex items-center gap-2">
          {icon && (
            <div className="flex h-5 w-5 items-center justify-center rounded-[6px] bg-accent-soft text-accent">
              {icon}
            </div>
          )}
          <h2 className="text-[13px] font-semibold tracking-tight text-text-hi">
            {title}
          </h2>
          {suffix && (
            <span className="font-mono text-[11px] text-text-low">
              {suffix}
            </span>
          )}
        </div>

        {/* Right: Control Cluster (Dense & Right-aligned / Left-grouped) */}
        {headerControls && (
          <div className="flex items-center gap-2">
            {headerControls}
          </div>
        )}
      </div>

      {/* Panel Body */}
      <div className="relative flex flex-1 min-h-0 flex-col overflow-hidden">
        {children}
      </div>
    </section>
  );
};

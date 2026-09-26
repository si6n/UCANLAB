import React from 'react';
import { ChevronRight } from 'lucide-react';

interface SummaryStripProps {
  totalDisplayed: number;
  anomalyCount: number;
  busErrorCount: number;
}

export const SummaryStrip: React.FC<SummaryStripProps> = ({
  totalDisplayed,
  anomalyCount,
  busErrorCount,
}) => {
  return (
    <footer
      aria-live="polite"
      className="flex h-8 shrink-0 select-none items-center justify-between border-t border-border/60 px-3.5 font-mono text-[11px] text-text-mid bg-transparent"
    >
      {/* zeron TRACE LINE: leading icon + mono counts, del/add as TEXT */}
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden whitespace-nowrap">
        <ChevronRight className="h-3 w-3 shrink-0 text-text-low" />
        <span className="truncate">
          <span className="hidden sm:inline">Toplam Gösterilen:</span>
          <span className="sm:hidden">Toplam:</span>{' '}
          <strong className="font-medium text-text-hi">{totalDisplayed}</strong>
        </span>
        <span className="text-text-faint">·</span>
        <span className="truncate">
          <span className="hidden sm:inline">Arıza/Anomali:</span>
          <span className="sm:hidden">Anomali:</span>{' '}
          <strong className="font-medium text-del">{anomalyCount}</strong>
        </span>
        <span className="text-text-faint">·</span>
        <span className="truncate">
          <span className="hidden sm:inline">Hata Kareleri:</span>
          <span className="sm:hidden">Hata:</span>{' '}
          <strong
            className={`font-medium ${
              busErrorCount > 0 ? 'text-del' : 'text-text-low'
            }`}
          >
            {busErrorCount}
          </strong>
        </span>
      </div>

      {/* Right: faint mono hint */}
      <div className="hidden shrink-0 font-mono text-[11px] text-text-low lg:block">
        Sağ tık ile kare menüsünü açın
      </div>
    </footer>
  );
};

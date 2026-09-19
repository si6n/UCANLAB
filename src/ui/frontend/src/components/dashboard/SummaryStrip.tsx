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
      <div className="flex items-center gap-2">
        <ChevronRight className="h-3 w-3 shrink-0 text-text-low" />
        <span>
          Toplam Gösterilen:{' '}
          <strong className="font-medium text-text-hi">{totalDisplayed}</strong>
        </span>
        <span className="text-text-faint">·</span>
        <span>
          Arıza/Anomali Kareleri:{' '}
          <strong className="font-medium text-del">{anomalyCount}</strong>
        </span>
        <span className="text-text-faint">·</span>
        <span>
          Hata Kareleri (Bus Errors):{' '}
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
      <div className="font-mono text-[11px] text-text-low">
        Sağ tık ile kare menüsünü açın
      </div>
    </footer>
  );
};

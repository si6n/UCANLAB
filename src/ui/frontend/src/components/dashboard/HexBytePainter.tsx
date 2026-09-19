import React from 'react';

interface HexBytePainterProps {
  bytes: string[];
  searchHighlight?: string;
  /** True on anomaly rows — flagged bytes render in del, rest stay monochrome. */
  isAnomalyRow?: boolean;
}

/**
 * MEASURED zeron transfer — MONOCHROME hex atoms (11.5px mono):
 * every byte-pair in --text-body, 0xFF/0x00 filler in --text-mid.
 * ONLY exceptions: anomaly-row flagged fields (--del) and active
 * search hits (--accent-text). Multi-hue cycle deleted entirely.
 */
export const HexBytePainter: React.FC<HexBytePainterProps> = ({
  bytes,
  searchHighlight = '',
  isAnomalyRow = false,
}) => {
  const normSearch = searchHighlight.trim().toLowerCase();

  return (
    <div className="flex items-center gap-1.5 font-mono text-[11.5px] leading-[1.35] select-text">
      {bytes.map((byte, idx) => {
        const upper = byte.toUpperCase();
        const isMatched =
          normSearch.length > 0 && byte.toLowerCase().includes(normSearch);
        const isPadding = upper === 'FF' || upper === '00';

        let cls = 'text-text-body';
        if (isMatched) cls = 'text-accent-text font-semibold underline decoration-accent underline-offset-2';
        else if (isAnomalyRow) cls = 'text-del font-medium';
        else if (isPadding) cls = 'text-text-mid';

        return (
          <span
            key={idx}
            className={`transition-all font-mono font-medium tracking-tight ${cls}`}
          >
            {upper}
          </span>
        );
      })}
    </div>
  );
};

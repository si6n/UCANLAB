import React from 'react';
import { Minus, Square, X } from 'lucide-react';
import { DesktopBridge } from '../../services/bridge';
import { L } from '../mechanic/text';

export const ICON_BTN =
  'inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi';

/** Minimise / maximise / close for the frameless window (native shell only). */
export const WindowControls: React.FC<{ native: boolean }> = ({ native }) => {
  if (!native) return null;
  return (
    <div className="flex flex-none items-center gap-0.5">
      <button type="button" className={ICON_BTN} onClick={() => DesktopBridge.minimizeWindow()} aria-label={L('Küçült', 'Minimise')}>
        <Minus className="h-4 w-4" />
      </button>
      <button type="button" className={ICON_BTN} onClick={() => DesktopBridge.maximizeWindow()} aria-label={L('Büyüt', 'Maximise')}>
        <Square className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        className={`${ICON_BTN} hover:!bg-estop hover:!text-white`}
        onClick={() => DesktopBridge.closeWindow()}
        aria-label={L('Kapat', 'Close')}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
};


import React, { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';
import { L } from '../mechanic/text';

const FOCUSABLE = 'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [href], [tabindex]:not([tabindex="-1"])';

/**
 * Modal dialog: focus moves in and stays in (Tab cycles), Esc closes, and
 * focus returns to the control that opened it. A click on the backdrop does
 * not close it, so a stray click cannot drop a half-done safety step.
 */
export const Dialog: React.FC<{
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  testId?: string;
}> = ({ title, onClose, children, footer, testId }) => {
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = box.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab' || !box.current) return;
      const items = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const head = items[0];
      const tail = items[items.length - 1];
      if (e.shiftKey && document.activeElement === head) {
        e.preventDefault();
        tail.focus();
      } else if (!e.shiftKey && document.activeElement === tail) {
        e.preventDefault();
        head.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      opener?.focus();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6">
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid={testId}
        className="flex max-h-full w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-border-strong bg-bg-popover text-text-body shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-border-whisper px-5 py-4">
          <h2 id={titleId} className="text-[15px] font-semibold text-text-hi">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={L('Kapat', 'Close')}
            className="-m-1 rounded-lg p-1 text-text-mid hover:bg-bg-row-hover hover:text-text-hi"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border-whisper px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
};

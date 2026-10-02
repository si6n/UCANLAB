import React, { useEffect, useId, useRef, useState } from 'react';
import { Palette } from 'lucide-react';
import { L, Lang, lang, setLang } from '../mechanic/text';
import { cx } from '../workbench/ui';
import { ThemePref, setThemePref, useThemePref } from './theme';
import { ICON_BTN } from './WindowControls';

export const THEME_OPTIONS: Array<{ value: ThemePref; label: () => string }> = [
  { value: 'dark', label: () => L('Koyu', 'Dark') },
  { value: 'light', label: () => L('Açık', 'Light') },
  { value: 'system', label: () => L('Sistem', 'System') },
];

export const LANG_OPTIONS: Array<{ value: Lang; label: string }> = [
  { value: 'tr', label: 'Türkçe' },
  { value: 'en', label: 'English' },
];

/** A row of mutually exclusive buttons (theme, language). */
export function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
  testId,
}: {
  label: string;
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (v: T) => void;
  testId?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} data-testid={testId} className="inline-flex rounded-lg border border-border-whisper p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          data-testid={testId ? `${testId}-${o.value}` : undefined}
          onClick={() => onChange(o.value)}
          className={cx(
            'rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors',
            value === o.value ? 'bg-bg-row-selected text-text-hi' : 'text-text-mid hover:text-text-hi',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Header shortcut for theme and language; the same choices live in Settings. */
export const DisplayMenu: React.FC = () => {
  const [open, setOpen] = useState(false);
  const theme = useThemePref();
  const panelId = useId();
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={wrap} className="relative flex-none">
      <button
        type="button"
        className={cx(ICON_BTN, open && 'bg-bg-row-hover text-text-hi')}
        aria-label={L('Görünüm ve dil', 'Appearance and language')}
        title={L('Görünüm ve dil', 'Appearance and language')}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        data-testid="display-menu"
      >
        <Palette className="h-4 w-4" />
      </button>
      {open && (
        <div
          id={panelId}
          role="group"
          aria-label={L('Görünüm ve dil', 'Appearance and language')}
          className="glass-popover absolute right-0 top-10 z-40 flex w-64 flex-col gap-3 p-3 text-[12.5px]"
        >
          <div className="flex flex-col gap-1.5">
            <span className="text-text-mid">{L('Tema', 'Theme')}</span>
            <Choice
              label={L('Tema', 'Theme')}
              value={theme}
              options={THEME_OPTIONS.map((o) => ({ value: o.value, label: o.label() }))}
              onChange={setThemePref}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-text-mid">{L('Dil', 'Language')}</span>
            <Choice label={L('Dil', 'Language')} value={lang()} options={LANG_OPTIONS} onChange={setLang} />
          </div>
        </div>
      )}
    </div>
  );
};

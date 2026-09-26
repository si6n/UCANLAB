import React from 'react';
import { Info } from 'lucide-react';

/**
 * SettingsPrimitives — dual-theme primitives for the settings console.
 *
 * IMPORTANT (Tailwind v3 constraint): every class used here MUST exist in the
 * compiled stylesheet. Colors declared as raw CSS variables in
 * tailwind.config.js do NOT support opacity modifiers (`bg-accent/10` and
 * friends are silently dropped by v3), so soft fills come from the dedicated
 * tokens: `bg-accent-soft`, `bg-ok-soft`, `bg-warn-soft`, `bg-danger-soft`,
 * `bg-delbg`, `bg-addbg`. Hairlines use `border-border-whisper` /
 * `border-border-strong` / `border-accent-line`.
 */

export type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'danger';

const TONE_DOT: Record<Tone, string> = {
  neutral: 'bg-text-faint',
  accent: 'bg-accent',
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-del',
};

const TONE_TEXT: Record<Tone, string> = {
  neutral: 'text-text-mid',
  accent: 'text-accent',
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-del',
};

const TONE_SOFT: Record<Tone, string> = {
  neutral: 'bg-bg-row-hover',
  accent: 'bg-accent-soft',
  ok: 'bg-ok-soft',
  warn: 'bg-warn-soft',
  danger: 'bg-danger-soft',
};

const TONE_EDGE: Record<Tone, string> = {
  neutral: 'border-border-whisper',
  accent: 'border-accent-line',
  ok: 'border-ok-border',
  warn: 'border-warn-border',
  danger: 'border-danger-border',
};

/** Monospace read-only value with a status dot (spec-sheet style). */
export const ReadOnlyValue: React.FC<{ value: string; tone?: Tone }> = ({
  value,
  tone = 'neutral',
}) => (
  <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${TONE_DOT[tone]}`} aria-hidden="true" />
    <span className={`font-mono text-[11.5px] ${tone === 'neutral' ? 'text-text-hi' : TONE_TEXT[tone]}`}>
      {value}
    </span>
  </span>
);

/** Compact status pill: soft fill + tone dot + mono label. */
export const StatusPill: React.FC<{
  label: string;
  tone?: Tone;
  dot?: boolean;
}> = ({ label, tone = 'neutral', dot = true }) => (
  <span
    className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-circle border px-2 py-0.5 font-mono text-[10.5px] font-semibold ${TONE_SOFT[tone]} ${TONE_TEXT[tone]} ${TONE_EDGE[tone]}`}
  >
    {dot && <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${TONE_DOT[tone]}`} aria-hidden="true" />}
    <span>{label}</span>
  </span>
);

/**
 * SettingRow — one settings list row: label + hint on the left, control on the
 * right (stacks vertically on narrow panes).
 */
export const SettingRow: React.FC<{
  label: string;
  hint?: string;
  htmlFor?: string;
  leading?: React.ReactNode;
  children?: React.ReactNode;
  stacked?: boolean;
}> = ({ label, hint, htmlFor, leading, children, stacked = false }) => {
  const text = (
    <div className="flex min-w-0 flex-1 items-start gap-2.5">
      {leading && <div className="shrink-0 pt-0.5">{leading}</div>}
      <div className="min-w-0">
        <label htmlFor={htmlFor} className="block text-[12.5px] font-medium leading-snug text-text-hi">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-[11px] leading-relaxed text-text-low">{hint}</p>}
      </div>
    </div>
  );

  if (stacked) {
    return (
      <div className="flex flex-col gap-2.5 px-4 py-3 transition-colors hover:bg-bg-row-hover">
        {text}
        {children ? <div className="min-w-0">{children}</div> : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5 px-4 py-3 transition-colors hover:bg-bg-row-hover sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      {text}
      {children ? (
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 sm:justify-end">{children}</div>
      ) : null}
    </div>
  );
};

/**
 * SettingsGroup — the ONE surface level of the settings console: a micro label
 * above a hairline-divided inset well (the app's canonical `surface-inset`
 * class: background + border + 10px radius, dual-theme correct).
 */
export const SettingsGroup: React.FC<{
  label: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}> = ({ label, icon, action, children }) => (
  <section>
    <div className="mb-2 flex items-center justify-between gap-2 px-0.5">
      <div className="flex items-center gap-1.5 text-text-low">
        {icon}
        <h3 className="text-[10.5px] font-semibold uppercase tracking-[0.08em]">{label}</h3>
      </div>
      {action && <div className="flex shrink-0 items-center">{action}</div>}
    </div>
    <div className="surface-inset divide-y divide-border-whisper overflow-hidden">{children}</div>
  </section>
);

/** Section intro: typographic lead, no chrome. */
export const SectionHeader: React.FC<{
  title: string;
  description?: string;
  actions?: React.ReactNode;
}> = ({ title, description, actions }) => (
  <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
    <div className="min-w-0">
      <h2 className="text-[13.5px] font-semibold tracking-tight text-text-hi">{title}</h2>
      {description && (
        <p className="mt-0.5 max-w-[56ch] text-[11.5px] leading-relaxed text-text-low">{description}</p>
      )}
    </div>
    {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
  </div>
);

/** Inline informational note with accent treatment. */
export const InfoNote: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="flex items-start gap-2 rounded-box border border-accent-line bg-accent-soft px-3.5 py-2.5 text-[11.5px] leading-relaxed text-text-body">
    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" aria-hidden="true" />
    <div className="min-w-0 flex-1">{children}</div>
  </div>
);

export const inputClass =
  'h-8 w-full rounded-btn border border-border-strong bg-bg-popover px-2.5 font-mono text-[11.5px] text-text-hi placeholder:text-text-faint transition-colors focus:border-accent focus:outline-none';

export const btnBase =
  'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-btn border px-2.5 text-[11.5px] font-medium transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-40';

export const btnGhost = `${btnBase} border-border-whisper bg-bg-panel text-text-mid hover:border-border-strong hover:bg-bg-row-hover hover:text-text-hi`;

export const btnPrimary = `${btnBase} border-transparent bg-accent text-white hover:opacity-90`;

export const btnDanger = `${btnBase} border-border-whisper bg-danger-soft text-del hover:border-danger-border`;

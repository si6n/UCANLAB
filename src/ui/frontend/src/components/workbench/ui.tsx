import React from 'react';

/**
 * Workbench primitives — the mechanic-flow design language (rounded cards,
 * one plain sentence per control, honest status chips) at desktop density.
 */

export const cx = (...parts: Array<string | false | null | undefined>): string => parts.filter(Boolean).join(' ');

export const Card: React.FC<{ children: React.ReactNode; className?: string; testId?: string }> = ({ children, className, testId }) => (
  <section data-testid={testId} className={cx('rounded-2xl border border-border-whisper bg-bg-card', className)}>
    {children}
  </section>
);

export const CardHeader: React.FC<{ title: string; hint?: string; children?: React.ReactNode }> = ({ title, hint, children }) => (
  <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-whisper px-5 py-4">
    <div className="min-w-0">
      <h2 className="text-[15px] font-semibold text-text-hi">{title}</h2>
      {hint && <p className="mt-0.5 text-[13px] text-text-mid">{hint}</p>}
    </div>
    {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
  </div>
);

export type Tone = 'neutral' | 'ok' | 'warn' | 'danger' | 'accent';

const TONES: Record<Tone, string> = {
  neutral: 'border-border-strong text-text-mid',
  ok: 'border-ok-border text-ok bg-ok-soft',
  warn: 'border-warn-border text-warn bg-warn-soft',
  danger: 'border-danger-border text-del bg-danger-soft',
  accent: 'border-accent-line text-accent-text bg-accent-soft',
};

export const Chip: React.FC<{ tone?: Tone; children: React.ReactNode; title?: string; testId?: string }> = ({
  tone = 'neutral',
  children,
  title,
  testId,
}) => (
  <span
    title={title}
    data-testid={testId}
    className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[12px] font-medium', TONES[tone])}
  >
    {children}
  </span>
);

export const Dot: React.FC<{ tone: Tone; pulse?: boolean }> = ({ tone, pulse }) => (
  <span
    className={cx(
      'h-2 w-2 flex-none rounded-full',
      tone === 'ok' && 'bg-add',
      tone === 'warn' && 'bg-warn',
      tone === 'danger' && 'bg-del',
      tone === 'accent' && 'bg-accent',
      tone === 'neutral' && 'bg-text-low',
      pulse && 'animate-pulse-subtle',
    )}
  />
);

/**
 * Header status: a dot and plain text, no outline or fill. The dot carries
 * the tone; a danger state (or a warning shown without a dot) colours the
 * text too.
 */
export const StatusText: React.FC<{ tone?: Tone; dot?: boolean; pulse?: boolean; children: React.ReactNode; title?: string; testId?: string }> = ({
  tone = 'neutral',
  dot = true,
  pulse,
  children,
  title,
  testId,
}) => (
  <span
    title={title}
    data-testid={testId}
    className={cx(
      'inline-flex flex-none items-center gap-1.5 whitespace-nowrap text-[12.5px]',
      tone === 'danger' ? 'font-semibold text-del' : tone === 'warn' && !dot ? 'text-warn' : 'text-text-mid',
    )}
  >
    {dot && <Dot tone={tone} pulse={pulse} />}
    {children}
  </span>
);

export const BTN =
  'inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-[13px] font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50';
export const BTN_PRIMARY = cx(BTN, 'bg-accent text-bg-app hover:opacity-90');
export const BTN_GHOST = cx(BTN, 'border border-border-strong text-text-hi hover:border-accent');
export const BTN_QUIET = cx(BTN, 'text-text-mid hover:bg-bg-row-hover hover:text-text-hi');

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  testId,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (v: T) => void;
  testId?: string;
}) {
  return (
    <div role="tablist" data-testid={testId} className="inline-flex rounded-lg border border-border-whisper p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          data-testid={testId ? `${testId}-${o.value}` : undefined}
          className={cx(
            'rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors',
            value === o.value ? 'bg-bg-row-selected text-text-hi' : 'text-text-mid hover:text-text-hi',
          )}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const EmptyState: React.FC<{
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  body: string;
  children?: React.ReactNode;
  testId?: string;
}> = ({ icon: Icon, title, body, children, testId }) => (
  <div data-testid={testId} className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
    <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-border-whisper text-text-mid">
      <Icon className="h-6 w-6" />
    </span>
    <h2 className="text-[15px] font-semibold text-text-hi">{title}</h2>
    <p className="max-w-md text-[13px] text-text-mid">{body}</p>
    {children && <div className="mt-2 flex flex-wrap justify-center gap-2">{children}</div>}
  </div>
);

export const hex2 = (b: number): string => b.toString(16).toUpperCase().padStart(2, '0');

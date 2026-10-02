import React from 'react';
import { AlertTriangle, Check } from 'lucide-react';
import { cx } from '../workbench/ui';
import { L } from './text';

/**
 * Layout of one mechanic screen inside the app frame: where the mechanic is
 * in the flow (stepper), the content (scrolls on its own, so nothing is out
 * of reach in a short window) and an action bar that stays in view.
 */

export type FlowStep = 1 | 2 | 3 | 4;

const STEPS: Array<() => string> = [
  () => L('Araç', 'Vehicle'),
  () => L('Bağlantı', 'Connection'),
  () => L('Tarama', 'Scan'),
  () => L('Sonuç', 'Result'),
];

export const Stepper: React.FC<{ step: FlowStep }> = ({ step }) => (
  <ol aria-label={L('Adımlar', 'Steps')} className="flex flex-none flex-wrap items-center gap-2 border-b border-border-whisper px-6 py-3 text-[13px]" data-testid="stepper">
    {STEPS.map((label, i) => {
      const n = i + 1;
      const state = n < step ? 'done' : n === step ? 'current' : 'todo';
      return (
        <li key={n} className="flex items-center gap-2" aria-current={state === 'current' ? 'step' : undefined}>
          {i > 0 && <span className="h-px w-6 bg-border-strong" aria-hidden="true" />}
          <span
            className={cx(
              'flex h-6 w-6 flex-none items-center justify-center rounded-full border text-[12px] font-semibold',
              state === 'done' && 'border-ok-border text-ok',
              state === 'current' && 'border-accent bg-accent text-bg-app',
              state === 'todo' && 'border-border-strong text-text-mid',
            )}
            aria-hidden="true"
          >
            {state === 'done' ? <Check className="h-3.5 w-3.5" /> : n}
          </span>
          <span className={state === 'current' ? 'font-semibold text-text-hi' : 'text-text-mid'}>
            {label()}
            {state === 'done' && <span className="sr-only"> ({L('tamam', 'done')})</span>}
          </span>
        </li>
      );
    })}
  </ol>
);

const WIDTH = { narrow: 'max-w-xl', normal: 'max-w-3xl', wide: 'max-w-5xl' } as const;

export const Screen: React.FC<{
  step?: FlowStep;
  width?: keyof typeof WIDTH;
  /** Left side of the action bar: going back, changing the vehicle. */
  back?: React.ReactNode;
  /** Right side of the action bar: the next step (primary button last). */
  actions?: React.ReactNode;
  children: React.ReactNode;
}> = ({ step, width = 'normal', back, actions, children }) => (
  <main className="flex min-h-0 flex-1 flex-col">
    {step && <Stepper step={step} />}
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="mech-scroll">
      <div className={cx('mx-auto flex w-full flex-col gap-5 px-6 py-8', WIDTH[width])}>{children}</div>
    </div>
    {(back || actions) && (
      <div className="flex flex-none flex-wrap items-center gap-2 border-t border-border-whisper bg-bg-rail px-6 py-3" data-testid="action-bar">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{back}</div>
        <div className="flex flex-wrap items-center justify-end gap-2">{actions}</div>
      </div>
    )}
  </main>
);

export const Heading: React.FC<{
  eyebrow?: string;
  icon?: React.ReactNode;
  title: React.ReactNode;
  lead?: React.ReactNode;
  testId?: string;
}> = ({ eyebrow, icon, title, lead, testId }) => (
  <div className="flex flex-col gap-1.5">
    {eyebrow && <div className="text-xs font-semibold uppercase tracking-wide text-text-low">{eyebrow}</div>}
    <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi" data-testid={testId}>
      {icon}
      {title}
    </h1>
    {lead && <div className="text-sm text-text-body">{lead}</div>}
  </div>
);

export const Notice: React.FC<{ tone: 'warn' | 'del'; children: React.ReactNode }> = ({ tone, children }) => (
  <div
    className={cx('flex items-start gap-2 rounded-xl border bg-bg-card p-3 text-sm', tone === 'del' ? 'border-del' : 'border-warn')}
    role="alert"
  >
    <AlertTriangle className={cx('mt-0.5 h-4 w-4 flex-none', tone === 'del' ? 'text-del' : 'text-warn')} />
    <span>{children}</span>
  </div>
);

/** Card surface used by the mechanic screens (same as the original cards). */
export const CARD = 'rounded-2xl border border-border-whisper bg-bg-card';

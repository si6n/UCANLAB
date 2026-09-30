/** Language helpers shared by the mechanic-flow screens (TR default, EN optional). */

export const lang = (): 'tr' | 'en' => {
  try {
    const saved = localStorage.getItem('ucanlab.lang');
    if (saved === 'en' || saved === 'tr') return saved;
  } catch {
    /* storage unavailable: default below */
  }
  return 'tr';
};

export const L = (tr: string, en: string): string => (lang() === 'en' ? en : tr);

/** Pick the `<key>_tr` / `<key>_en` field of a bridge payload. */
export function pick<T extends object>(obj: T | null | undefined, key: string): string {
  if (!obj) return '';
  const rec = obj as Record<string, unknown>;
  const value = rec[`${key}_${lang()}`] ?? rec[`${key}_tr`];
  return typeof value === 'string' ? value : '';
}

export function messageOf(state: { message_tr?: string; message_en?: string } | null | undefined): string {
  if (!state) return '';
  return (lang() === 'en' ? state.message_en : state.message_tr) || '';
}

export const BTN_PRIMARY =
  'inline-flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-bg-app transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50';
export const BTN_SECONDARY =
  'inline-flex w-full items-center justify-center gap-2 rounded-lg border border-border-strong px-4 py-3 text-sm font-semibold text-text-hi transition-colors hover:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';

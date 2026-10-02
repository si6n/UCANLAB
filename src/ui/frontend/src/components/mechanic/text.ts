/** Language helpers shared by the mechanic-flow screens (TR default, EN optional). */

export type Lang = 'tr' | 'en';

const LANG_KEY = 'ucanlab.lang';

/** Fired on `window` after setLang(); the app root re-renders on it. */
export const LANG_EVENT = 'ucanlab:lang';

function readSavedLang(): Lang {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved === 'en' || saved === 'tr') return saved;
  } catch {
    /* storage unavailable: default below */
  }
  return 'tr';
}

let current: Lang = readSavedLang();

export const lang = (): Lang => current;

/**
 * Switch the interface language. Remembered on this computer and applied at
 * once; `<html lang>` follows so case mapping (i/İ) matches the language.
 */
export function setLang(next: Lang): void {
  current = next;
  try {
    localStorage.setItem(LANG_KEY, next);
  } catch {
    /* storage unavailable: applies until the app closes */
  }
  document.documentElement.lang = next;
  window.dispatchEvent(new Event(LANG_EVENT));
}

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

/** Action-bar buttons: the same look as above, sized to their label. */
const BTN_BAR =
  'inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50';
export const BAR_PRIMARY = `${BTN_BAR} bg-accent text-bg-app hover:opacity-90`;
export const BAR_SECONDARY = `${BTN_BAR} border border-border-strong text-text-hi hover:border-accent`;
export const BAR_QUIET = `${BTN_BAR} text-text-mid hover:bg-bg-row-hover hover:text-text-hi`;
export const BAR_DANGER = `${BTN_BAR} border border-del text-del hover:bg-danger-soft`;

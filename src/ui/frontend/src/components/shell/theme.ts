import { useEffect, useState } from 'react';

/**
 * Theme preference, shared by every screen (sign-in, mechanic flow,
 * workbench). Dark stays the default; "system" follows the OS setting.
 * Stored under the key the workbench already used, so a saved choice
 * carries over.
 */

export type ThemePref = 'system' | 'dark' | 'light';

const THEME_KEY = 'ucanlab.theme';
const THEME_EVENT = 'ucanlab:theme';

function readSavedTheme(): ThemePref {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'system' || saved === 'dark' || saved === 'light') return saved;
  } catch {
    /* storage unavailable: default below */
  }
  return 'dark';
}

let current: ThemePref = readSavedTheme();

const systemDark = (): boolean => typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;

/** Put the stored (or system) theme on `<html>`. */
export function applyTheme(): void {
  const dark = current === 'dark' || (current === 'system' && systemDark());
  document.documentElement.classList.toggle('dark', dark);
}

export const themePref = (): ThemePref => current;

export function setThemePref(next: ThemePref): void {
  current = next;
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    /* storage unavailable: applies until the app closes */
  }
  applyTheme();
  window.dispatchEvent(new Event(THEME_EVENT));
}

/** Current preference; re-renders when it changes anywhere in the app. */
export function useThemePref(): ThemePref {
  const [pref, setPref] = useState<ThemePref>(current);
  useEffect(() => {
    const sync = () => setPref(current);
    window.addEventListener(THEME_EVENT, sync);
    return () => window.removeEventListener(THEME_EVENT, sync);
  }, []);
  return pref;
}

/** Keeps "system" in step with the OS while the app is open. */
export function useSystemThemeFollower(): void {
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => {
      if (current === 'system') applyTheme();
    };
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);
}

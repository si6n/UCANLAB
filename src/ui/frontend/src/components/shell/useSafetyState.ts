import { useCallback, useEffect, useState } from 'react';
import { DesktopBridge } from '../../services/bridge';

/**
 * Supervisor state (STARTUP/SAFE/PASSIVE/ARMED_TX/ACTIVE/FAULT), read from
 * Python once a second. Null outside the native shell or when the read
 * fails — callers then say the state is unknown instead of guessing.
 */
export function useSafetyState(native: boolean): { safety: string | null; refresh: () => void } {
  const [safety, setSafety] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!native) return;
    DesktopBridge.getSafetyState()
      .then(setSafety)
      .catch(() => setSafety(null));
  }, [native]);

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 1000);
    return () => window.clearInterval(t);
  }, [refresh]);

  return { safety, refresh };
}

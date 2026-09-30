import { useEffect } from 'react';

/**
 * UI-alive pulse for the TX watchdog (same contract as App.tsx): while a
 * screen that may transmit (the consented read-only scan) is visible, the
 * lease is refreshed every ~250 ms from requestAnimationFrame. A hidden or
 * frozen window stops the pulse, the lease expires and TX is refused.
 */
export function useUiHeartbeat(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    let alive = true;
    let lastSent = 0;
    let raf = 0;
    const tick = () => {
      const now = performance.now();
      if (alive && now - lastSent >= 250 && window.pywebview?.api?.heartbeat) {
        lastSent = now;
        window.pywebview.api.heartbeat().catch(() => {
          /* next frame retries; the watchdog tolerates misses */
        });
      }
      if (alive) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
    };
  }, [active]);
}

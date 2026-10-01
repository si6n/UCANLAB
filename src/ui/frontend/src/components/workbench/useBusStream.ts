import { useCallback, useEffect, useRef, useState } from 'react';
import { BusFrame, FrameSource, subscribeFrames } from './frameBus';

/**
 * Live view model over the frame bus: a capped chronological list plus one
 * row per arbitration id (count, measured rate, last payload, which bytes and
 * bits changed). Ingest runs at bus speed into refs; React re-renders at a
 * fixed, low rate so a 2000 frame/s bus cannot freeze the window.
 */

export interface IdStats {
  key: string;
  arb: number;
  idText: string;
  extended: boolean;
  fd: boolean;
  count: number;
  /** Frames/s over the last ~2 s of arrivals; null until measurable. */
  rateHz: number | null;
  dlc: number;
  last: Uint8Array;
  /** performance.now() of the last change, per byte (0 = never changed). */
  byteChangedAt: number[];
  /** How many times each bit flipped (index = byte*8 + bit, bit 7 = MSB). */
  bitFlips: number[];
  firstT: number;
  lastT: number;
  lastSeenAt: number;
  sources: Set<FrameSource>;
}

export interface BusSnapshot {
  rows: IdStats[];
  recent: BusFrame[];
  total: number;
  /** Frames received in the last second (measured, not estimated). */
  perSecond: number;
  /** Nominal bits of those frames (no stuff bits); null if any was CAN FD. */
  bitsPerSecond: number | null;
  sources: FrameSource[];
  paused: boolean;
}

const RECENT_CAP = 600;

/**
 * Classic CAN frame length without stuff bits (ISO 11898-1): 47 + 8·n bits
 * for an 11-bit id, 67 + 8·n for a 29-bit id (SOF … EOF + 3-bit IFS). Bus
 * load from this is a lower bound — real stuffing adds up to ~20 %.
 */
export function nominalBits(dlc: number, extended: boolean): number {
  return (extended ? 67 : 47) + 8 * Math.min(Math.max(dlc, 0), 8);
}
const RENDER_MS = 250;

export function useBusStream(enabled = true) {
  const byId = useRef(new Map<string, IdStats & { arrivals: number[] }>());
  const recent = useRef<BusFrame[]>([]);
  const total = useRef(0);
  const lastSecond = useRef<Array<{ at: number; bits: number }>>([]);
  const dirty = useRef(false);
  const pausedRef = useRef(false);
  const [snap, setSnap] = useState<BusSnapshot>({ rows: [], recent: [], total: 0, perSecond: 0, bitsPerSecond: null, sources: [], paused: false });

  const ingest = useCallback((batch: BusFrame[]) => {
    if (pausedRef.current) return;
    const now = performance.now();
    for (const f of batch) {
      total.current += 1;
      lastSecond.current.push({ at: now, bits: f.fd ? -1 : nominalBits(f.dlc, f.extended) });
      const key = `${f.channel}:${f.extended ? 'x' : 's'}:${f.arb}`;
      let row = byId.current.get(key);
      if (!row) {
        row = {
          key,
          arb: f.arb,
          idText: f.idText,
          extended: f.extended,
          fd: f.fd,
          count: 0,
          rateHz: null,
          dlc: f.dlc,
          last: f.data,
          byteChangedAt: new Array(f.data.length).fill(0),
          bitFlips: new Array(f.data.length * 8).fill(0),
          firstT: f.t,
          lastT: f.t,
          lastSeenAt: now,
          sources: new Set(),
          arrivals: [],
        };
        byId.current.set(key, row);
      } else {
        const len = Math.max(row.last.length, f.data.length);
        for (let i = 0; i < len; i += 1) {
          const a = row.last[i] ?? 0;
          const b = f.data[i] ?? 0;
          if (a !== b) {
            row.byteChangedAt[i] = now;
            let diff = a ^ b;
            for (let bit = 0; diff !== 0; bit += 1, diff >>= 1) {
              if (diff & 1) row.bitFlips[i * 8 + bit] = (row.bitFlips[i * 8 + bit] ?? 0) + 1;
            }
          }
        }
        row.last = f.data;
        row.dlc = f.dlc;
        row.lastT = f.t;
        row.lastSeenAt = now;
      }
      row.count += 1;
      row.fd = row.fd || f.fd;
      row.sources.add(f.source);
      row.arrivals.push(f.t);
      if (row.arrivals.length > 64) row.arrivals.splice(0, row.arrivals.length - 64);
    }
    recent.current = batch.slice().reverse().concat(recent.current).slice(0, RECENT_CAP);
    dirty.current = true;
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    const off = subscribeFrames(ingest);
    const timer = window.setInterval(() => {
      const now = performance.now();
      const cut = now - 1000;
      const ls = lastSecond.current;
      let drop = 0;
      while (drop < ls.length && ls[drop].at < cut) drop += 1;
      if (drop) ls.splice(0, drop);
      if (!dirty.current && drop === 0) return;
      dirty.current = false;
      const sources = new Set<FrameSource>();
      const rows: IdStats[] = [];
      byId.current.forEach((row) => {
        const arr = row.arrivals;
        if (arr.length >= 3) {
          const span = arr[arr.length - 1] - arr[0];
          row.rateHz = span > 0 ? (arr.length - 1) / span : null;
        }
        row.sources.forEach((s) => sources.add(s));
        rows.push({
          ...row,
          byteChangedAt: row.byteChangedAt.slice(),
          bitFlips: row.bitFlips.slice(),
          sources: new Set(row.sources),
        });
      });
      rows.sort((a, b) => a.arb - b.arb);
      setSnap({
        rows,
        recent: recent.current,
        total: total.current,
        perSecond: ls.length,
        bitsPerSecond: ls.some((e) => e.bits < 0) ? null : ls.reduce((sum, e) => sum + e.bits, 0),
        sources: Array.from(sources),
        paused: pausedRef.current,
      });
    }, RENDER_MS);
    return () => {
      off();
      window.clearInterval(timer);
    };
  }, [enabled, ingest]);

  const setPaused = useCallback((paused: boolean) => {
    pausedRef.current = paused;
    dirty.current = true;
    setSnap((s) => ({ ...s, paused }));
  }, []);

  const clear = useCallback(() => {
    byId.current.clear();
    recent.current = [];
    total.current = 0;
    lastSecond.current = [];
    dirty.current = true;
    setSnap((s) => ({ ...s, rows: [], recent: [], total: 0, perSecond: 0, bitsPerSecond: null, sources: [] }));
  }, []);

  return { snap, setPaused, clear };
}

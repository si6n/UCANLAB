/**
 * Single owner of `window.onNewCanFrames` (the Python push channel, E13).
 *
 * Python calls the global once per telemetry tick with a batch of native
 * frame DTOs. Several screens want them (live traffic, signal discovery,
 * records), so the global is installed exactly once here and fans out to
 * subscribers. Frames are normalised at this boundary; nothing downstream
 * reads the raw DTO. No frame is ever invented here: an empty bus is an
 * empty list.
 */

export type FrameSource = 'physical' | 'replay' | 'virtual' | 'injected' | 'synthetic' | 'simulator' | 'unknown';

export interface BusFrame {
  /** Monotonic sequence number assigned on arrival (stable React key). */
  seq: number;
  /** Seconds, as stamped by the driver/simulator. */
  t: number;
  /** Numeric arbitration id. */
  arb: number;
  /** Display id: 0x7E8 / 0x18FEF100. */
  idText: string;
  extended: boolean;
  fd: boolean;
  channel: string;
  dlc: number;
  data: Uint8Array;
  source: FrameSource;
}

type Listener = (batch: BusFrame[]) => void;

const listeners = new Set<Listener>();
let seq = 0;
let installed = false;

const KNOWN_SOURCES: ReadonlySet<string> = new Set(['physical', 'replay', 'virtual', 'injected', 'synthetic', 'simulator']);

function parseHex(hex: string): Uint8Array {
  const clean = hex.length % 2 === 0 ? hex : hex.slice(0, -1);
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i += 1) {
    const b = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
    out[i] = Number.isNaN(b) ? 0 : b;
  }
  return out;
}

export function formatId(arb: number, extended: boolean): string {
  return `0x${arb.toString(16).toUpperCase().padStart(extended ? 8 : 3, '0')}`;
}

/** Normalise one native DTO; null for anything malformed (never guessed). */
export function normalizeFrame(raw: unknown): BusFrame | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const idValue = typeof r.id === 'string' ? parseInt(r.id.replace(/^0x/i, ''), 16) : Number(r.arbitration_id);
  if (!Number.isFinite(idValue) || idValue < 0) return null;
  const data = typeof r.data === 'string' ? parseHex(r.data) : new Uint8Array();
  const extended = Boolean(r.isExtended ?? r.is_extended) || idValue > 0x7ff;
  const source = typeof r.source === 'string' && KNOWN_SOURCES.has(r.source) ? (r.source as FrameSource) : 'unknown';
  seq += 1;
  return {
    seq,
    t: Number(r.timestamp ?? 0) || 0,
    arb: idValue,
    idText: formatId(idValue, extended),
    extended,
    fd: Boolean(r.isFd ?? r.is_fd),
    channel: String(r.channel ?? r.channel_id ?? ''),
    dlc: Number(r.dlc ?? data.length) || data.length,
    data,
    source,
  };
}

function dispatch(batch: unknown[]): void {
  if (!Array.isArray(batch) || batch.length === 0) return;
  const frames: BusFrame[] = [];
  for (const raw of batch) {
    const f = normalizeFrame(raw);
    if (f) frames.push(f);
  }
  if (frames.length === 0) return;
  listeners.forEach((fn) => {
    try {
      fn(frames);
    } catch {
      /* one broken screen must not starve the others */
    }
  });
}

function install(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.onNewCanFrames = (batch: unknown) => dispatch(batch as unknown[]);
  window.onNewCanFrame = (frame: unknown) => dispatch([frame]);
}

export function subscribeFrames(fn: Listener): () => void {
  install();
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Test hook: feed DTOs exactly as Python would. */
export function __pushNativeBatchForTests(batch: unknown[]): void {
  install();
  dispatch(batch);
}

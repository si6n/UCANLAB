import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LineChart, Pause, Play } from 'lucide-react';
import { DesktopBridge, PlotSeries, PlotSignalInfo } from '../../services/bridge';
import { L } from '../mechanic/text';
import { BTN_GHOST, Card, CardHeader, Chip, EmptyState, Segmented, cx } from './ui';

/**
 * Grafik: values Python actually decoded (J1939 / NMEA 2000 / OBD / OEM
 * decoders), one small chart per signal. Nothing is interpolated, smoothed
 * or invented: a gap in the data is a gap in the line's points, a frozen
 * value is a flat line, an unknown id is not here at all (→ Sinyal keşfi).
 */

const MAX_SELECTED = 4;

type Window = '10' | '30' | '60';

const LABELS: Record<string, () => string> = {
  EngineSpeed: () => L('Motor devri', 'Engine speed'),
  EngineCoolantTemp: () => L('Soğutma suyu sıcaklığı', 'Coolant temperature'),
  VehicleSpeed: () => L('Araç hızı', 'Vehicle speed'),
  EngineLoad: () => L('Motor yükü', 'Engine load'),
  EngineTorque: () => L('Motor torku', 'Engine torque'),
};

export function signalLabel(name: string): string {
  if (LABELS[name]) return LABELS[name]();
  const fluid = /^FluidLevel_(.+)_(\d+)$/.exec(name);
  if (fluid) return `${L('Sıvı seviyesi', 'Fluid level')} (${fluid[1]} #${fluid[2]})`;
  return name;
}

export function unitLabel(unit: string): string {
  if (unit === 'C') return '°C';
  if (unit === 'percent') return '%';
  return unit;
}

function fmt(v: number): string {
  const abs = Math.abs(v);
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return v.toLocaleString('tr-TR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 3–5 round ticks covering [lo, hi]. */
function niceTicks(lo: number, hi: number): number[] {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= 5) ?? 10 * mag;
  const out: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) out.push(Number(t.toFixed(10)));
  return out;
}

const W = 640;
const H = 170;
const PAD = { l: 52, r: 12, t: 10, b: 24 };

const SignalChart: React.FC<{ name: string; series: PlotSeries | undefined; windowS: number }> = ({ name, series, windowS }) => {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const t = series?.t ?? [];
  const v = series?.v ?? [];
  const unit = unitLabel(series?.unit ?? '');

  const { lo, hi, ticks } = useMemo(() => {
    if (v.length === 0) return { lo: 0, hi: 1, ticks: [0, 1] };
    let min = Math.min(...v);
    let max = Math.max(...v);
    if (max - min < 1e-9) {
      const pad = Math.abs(max) * 0.05 || 1;
      min -= pad;
      max += pad;
    } else {
      const pad = (max - min) * 0.1;
      min -= pad;
      max += pad;
    }
    const tk = niceTicks(min, max);
    return { lo: Math.min(min, tk[0]), hi: Math.max(max, tk[tk.length - 1]), ticks: tk };
  }, [v]);

  const x = (ts: number) => PAD.l + ((ts + windowS) / windowS) * (W - PAD.l - PAD.r);
  const y = (val: number) => PAD.t + (1 - (val - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  const points = t.map((ts, i) => `${x(ts).toFixed(1)},${y(v[i]).toFixed(1)}`).join(' ');
  const last = v.length ? v[v.length - 1] : null;

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg || t.length === 0) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    let best = 0;
    let bestD = Infinity;
    for (let i = 0; i < t.length; i += 1) {
      const d = Math.abs(x(t[i]) - px);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    setHover(best);
  };

  const hv = hover !== null && hover < t.length ? { t: t[hover], v: v[hover] } : null;

  return (
    <Card className="overflow-hidden" testId={`chart-${name}`}>
      <div className="flex items-baseline justify-between gap-3 px-5 pt-4">
        <div className="min-w-0">
          <div className="truncate text-[14px] font-semibold text-text-hi">{signalLabel(name)}</div>
          <div className="text-[12px] text-text-mid">
            {name}
            {series?.simulated ? ` · ${L('Simülatör', 'Simulator')}` : ''}
          </div>
        </div>
        <div className="text-right">
          <span className="text-[22px] font-semibold tabular-nums text-text-hi" data-testid={`chart-last-${name}`}>
            {last === null ? '—' : fmt(last)}
          </span>
          <span className="ml-1 text-[13px] text-text-mid">{unit}</span>
        </div>
      </div>
      {v.length === 0 ? (
        <p className="px-5 pb-5 pt-3 text-[13px] text-text-mid">
          {L(`Son ${windowS} saniyede değer gelmedi.`, `No value in the last ${windowS} seconds.`)}
        </p>
      ) : (
        <div className="relative px-2 pb-2">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H}`}
            className="h-[170px] w-full"
            preserveAspectRatio="none"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
            role="img"
            aria-label={`${signalLabel(name)}: ${fmt(last ?? 0)} ${unit}`}
          >
            {ticks.map((tk) => (
              <g key={tk}>
                <line x1={PAD.l} x2={W - PAD.r} y1={y(tk)} y2={y(tk)} stroke="var(--border)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
                <text x={PAD.l - 8} y={y(tk)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--text-mid)">
                  {fmt(tk)}
                </text>
              </g>
            ))}
            {[0, 0.5, 1].map((f) => (
              <text key={f} x={x(-windowS + f * windowS)} y={H - 6} textAnchor={f === 0 ? 'start' : f === 1 ? 'end' : 'middle'} fontSize={11} fill="var(--text-mid)">
                {f === 1 ? L('şimdi', 'now') : `−${Math.round(windowS * (1 - f))} s`}
              </text>
            ))}
            <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            {hv && (
              <g>
                <line x1={x(hv.t)} x2={x(hv.t)} y1={PAD.t} y2={H - PAD.b} stroke="var(--text-low)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
                <circle cx={x(hv.t)} cy={y(hv.v)} r={4} fill="var(--accent)" stroke="var(--bg-card)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
              </g>
            )}
          </svg>
          {hv && (
            <div
              className="pointer-events-none absolute top-2 rounded-lg border border-border-whisper bg-bg-popover px-2.5 py-1.5 text-[12px] shadow-sm"
              style={{ left: `clamp(8px, calc(${((x(hv.t) / W) * 100).toFixed(2)}% - 60px), calc(100% - 130px))` }}
            >
              <div className="font-semibold tabular-nums text-text-hi">
                {fmt(hv.v)} {unit}
              </div>
              <div className="tabular-nums text-text-mid">
                {L(
                  `${Math.abs(hv.t).toLocaleString('tr-TR', { maximumFractionDigits: 1 })} sn önce`,
                  `${Math.abs(hv.t).toFixed(1)} s ago`,
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
};

export const PlotView: React.FC<{ onOpenDiscovery: () => void }> = ({ onOpenDiscovery }) => {
  const [signals, setSignals] = useState<PlotSignalInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [series, setSeries] = useState<Record<string, PlotSeries>>({});
  const [win, setWin] = useState<Window>('30');
  const [paused, setPaused] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const autoPicked = useRef(false);

  const loadList = useCallback(async () => {
    const res = await DesktopBridge.plotSignalList();
    if (!res) return;
    setSignals(res.signals);
    setLoaded(true);
    if (!autoPicked.current && res.signals.length > 0) {
      autoPicked.current = true;
      setSelected(res.signals.slice(0, 2).map((s) => s.name));
    }
  }, []);

  useEffect(() => {
    void loadList();
    const timer = window.setInterval(() => void loadList(), 1000);
    return () => window.clearInterval(timer);
  }, [loadList]);

  useEffect(() => {
    if (paused || selected.length === 0) return undefined;
    let alive = true;
    const tick = async () => {
      const res = await DesktopBridge.plotSignalSeries(selected, Number(win));
      if (alive && res?.success) setSeries(res.series);
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 250);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [selected, win, paused]);

  const toggle = (name: string) =>
    setSelected((cur) => (cur.includes(name) ? cur.filter((n) => n !== name) : cur.length >= MAX_SELECTED ? cur : [...cur, name]));

  if (loaded && signals.length === 0) {
    return (
      <Card className="h-full" testId="plot-view">
        <EmptyState
          testId="plot-empty"
          icon={LineChart}
          title={L('Henüz çözülmüş sinyal yok', 'No decoded signals yet')}
          body={L(
            'Uygulamanın tanıdığı J1939, NMEA 2000 ve OBD değerleri hattan geldikçe burada listelenir. Binek araçların çoğu motor verisini üreticiye özel kimliklerle yayınlar; bunlar için Sinyal keşfini kullanın.',
            'J1939, NMEA 2000 and OBD values the app recognises appear here as they arrive. Most cars broadcast engine data on maker-specific ids; use Signal discovery for those.',
          )}
        >
          <button type="button" className={BTN_GHOST} onClick={onOpenDiscovery}>
            {L('Sinyal keşfine git', 'Open signal discovery')}
          </button>
        </EmptyState>
      </Card>
    );
  }

  return (
    <div className="flex h-full min-h-0 gap-3" data-testid="plot-view">
      <Card className="flex w-[300px] flex-none flex-col overflow-hidden">
        <CardHeader
          title={L('Sinyaller', 'Signals')}
          hint={L(`En fazla ${MAX_SELECTED} sinyal seçin. Her biri kendi grafiğinde çizilir.`, `Pick up to ${MAX_SELECTED}. Each gets its own chart.`)}
        />
        <ul className="flex-1 overflow-auto p-2">
          {signals.map((s) => {
            const on = selected.includes(s.name);
            const stale = s.last_age_s > 3;
            return (
              <li key={s.name}>
                <button
                  type="button"
                  data-testid={`signal-${s.name}`}
                  aria-pressed={on}
                  onClick={() => toggle(s.name)}
                  disabled={!on && selected.length >= MAX_SELECTED}
                  className={cx(
                    'flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition-colors disabled:opacity-50',
                    on ? 'bg-bg-row-selected' : 'hover:bg-bg-row-hover',
                  )}
                >
                  <span className={cx('mt-1 h-3 w-3 flex-none rounded-[4px] border', on ? 'border-accent bg-accent' : 'border-border-strong')} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-semibold text-text-hi">{signalLabel(s.name)}</span>
                    <span className="block text-[12px] tabular-nums text-text-mid">
                      {fmt(s.last)} {unitLabel(s.unit)}
                      {stale ? ` · ${L(`${Math.round(s.last_age_s)} sn önce`, `${Math.round(s.last_age_s)} s ago`)}` : ''}
                    </span>
                    <span className="mt-1 flex flex-wrap gap-1">
                      {s.simulated && <Chip tone="warn">{L('Simülatör', 'Simulator')}</Chip>}
                      {s.confidence < 1 && (
                        <Chip title={L('Üreticiye özel çözücünün güveni düşük', 'Low confidence maker-specific decoder')}>
                          {L('Düşük güven', 'Low confidence')}
                        </Chip>
                      )}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </Card>

      <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-auto">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[13px] text-text-mid">
            {L(
              'Çizgiler yalnızca gelen değerleri birleştirir; yumuşatma ya da tahmin yoktur.',
              'Lines only join received values; no smoothing or estimation.',
            )}
          </p>
          <div className="flex items-center gap-2">
            <Segmented<Window>
              testId="plot-window"
              value={win}
              onChange={setWin}
              options={[
                { value: '10', label: '10 s' },
                { value: '30', label: '30 s' },
                { value: '60', label: '60 s' },
              ]}
            />
            <button type="button" data-testid="plot-pause" className={BTN_GHOST} onClick={() => setPaused((p) => !p)}>
              {paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
              {paused ? L('Devam et', 'Resume') : L('Dondur', 'Freeze')}
            </button>
          </div>
        </div>
        {selected.length === 0 ? (
          <Card>
            <p className="p-6 text-center text-[13px] text-text-mid">{L('Soldan bir sinyal seçin.', 'Pick a signal on the left.')}</p>
          </Card>
        ) : (
          selected.map((name) => <SignalChart key={name} name={name} series={series[name]} windowS={Number(win)} />)
        )}
      </div>
    </div>
  );
};

import React, { useEffect, useMemo, useState } from 'react';
import { Pause, Play, Radio, Search, Trash2, X, FlaskConical, ArrowRight } from 'lucide-react';
import { L } from '../mechanic/text';
import { BusFrame, FrameSource } from './frameBus';
import { BusSnapshot, IdStats } from './useBusStream';
import { BTN_GHOST, BTN_PRIMARY, BTN_QUIET, Card, CardHeader, Chip, EmptyState, Segmented, cx, hex2 } from './ui';

/**
 * Canlı trafik (old "Sniffer"): every frame the bus really carried, nothing
 * else. Two lenses — one row per id (what changes, how often) and the raw
 * arrival stream — plus a byte/bit inspector for the selected id.
 */

type View = 'ids' | 'stream';

export const SOURCE_LABEL: Record<FrameSource, () => string> = {
  physical: () => L('Araç', 'Vehicle'),
  simulator: () => L('Simülatör', 'Simulator'),
  synthetic: () => L('Simülatör', 'Simulator'),
  virtual: () => L('Sanal hat', 'Virtual bus'),
  replay: () => L('Kayıttan', 'Replay'),
  injected: () => L('Enjekte', 'Injected'),
  unknown: () => L('Bilinmiyor', 'Unknown'),
};

const fmtRate = (hz: number | null): string => {
  if (hz === null) return '—';
  if (hz >= 10) return `${Math.round(hz)} Hz`;
  return `${hz.toFixed(1)} Hz`;
};

function matches(query: string, idText: string, data: Uint8Array): boolean {
  if (!query) return true;
  const q = query.replace(/\s+/g, '').replace(/^0x/i, '').toUpperCase();
  if (!q) return true;
  if (idText.replace(/^0x/i, '').includes(q)) return true;
  return Array.from(data, hex2).join('').includes(q);
}

const Bytes: React.FC<{ data: Uint8Array; changedAt?: number[]; now: number }> = ({ data, changedAt, now }) => (
  <span className="font-mono text-[12.5px] tracking-wide">
    {Array.from(data, (b, i) => {
      const age = changedAt && changedAt[i] ? now - changedAt[i] : Infinity;
      return (
        <span
          key={i}
          className={cx(
            'mr-1.5 inline-block rounded px-0.5 transition-colors duration-700',
            age < 400 ? 'bg-accent-soft text-accent-text' : age < 3000 ? 'text-text-hi' : 'text-text-mid',
          )}
        >
          {hex2(b)}
        </span>
      );
    })}
  </span>
);

const IdInspector: React.FC<{ row: IdStats; onClose: () => void; onDiscover: (trafficKey: string) => void }> = ({ row, onClose, onDiscover }) => {
  const maxFlips = Math.max(1, ...row.bitFlips);
  return (
    <Card className="flex w-[340px] flex-none flex-col overflow-hidden" testId="id-inspector">
      <div className="flex items-start justify-between gap-2 border-b border-border-whisper px-4 py-3">
        <div>
          <div className="font-mono text-[15px] font-semibold text-text-hi">{row.idText}</div>
          <div className="text-[12px] text-text-mid">
            {row.count.toLocaleString('tr-TR')} {L('kare', 'frames')} · {fmtRate(row.rateHz)} · DLC {row.dlc}
          </div>
        </div>
        <button type="button" className={BTN_QUIET} onClick={onClose} aria-label={L('Kapat', 'Close')}>
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3">
        <p className="mb-3 text-[12.5px] text-text-mid">
          {L(
            'Her kutu bir bit. Renk ne kadar koyuysa o bit o kadar sık değişti. Hiç değişmeyen bitler sabit ya da kullanılmıyor olabilir.',
            'Each box is one bit. The darker, the more often it flipped. Bits that never change may be constant or unused.',
          )}
        </p>
        <table className="w-full border-separate border-spacing-[3px] text-center font-mono text-[11px]">
          <thead>
            <tr className="text-text-low">
              <th className="w-10 text-left font-normal">{L('Bayt', 'Byte')}</th>
              {[7, 6, 5, 4, 3, 2, 1, 0].map((b) => (
                <th key={b} className="font-normal">
                  {b}
                </th>
              ))}
              <th className="w-8 font-normal">hex</th>
            </tr>
          </thead>
          <tbody>
            {Array.from(row.last, (byte, i) => (
              <tr key={i}>
                <td className="text-left text-text-mid">{i}</td>
                {[7, 6, 5, 4, 3, 2, 1, 0].map((bit) => {
                  const flips = row.bitFlips[i * 8 + bit] ?? 0;
                  const on = (byte >> bit) & 1;
                  const heat = flips === 0 ? 0 : 0.15 + 0.85 * (flips / maxFlips);
                  return (
                    <td
                      key={bit}
                      title={L(`${flips} kez değişti`, `${flips} flips`)}
                      className={cx('h-6 rounded border', flips ? 'border-accent-line' : 'border-border-whisper', on ? 'text-text-hi' : 'text-text-low')}
                      style={flips ? { backgroundColor: `rgba(59,130,246,${heat.toFixed(2)})` } : undefined}
                    >
                      {on}
                    </td>
                  );
                })}
                <td className="text-text-hi">{hex2(byte)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {Array.from(row.sources).map((s) => (
            <Chip key={s}>{SOURCE_LABEL[s]()}</Chip>
          ))}
        </div>
      </div>
      <div className="border-t border-border-whisper p-3">
        <button type="button" className={cx(BTN_GHOST, 'w-full')} onClick={() => onDiscover(row.key)} data-testid="send-to-discovery">
          {L('Bu kimliği sinyal keşfinde incele', 'Analyse this id in Signal discovery')}
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </Card>
  );
};

export const LiveTraffic: React.FC<{
  snap: BusSnapshot;
  onPause: (paused: boolean) => void;
  onClear: () => void;
  busLabel: string | null;
  simulatorRunning: boolean;
  canStartSimulator: boolean;
  simError: string;
  onStartSimulator: (vehicleType: string) => void;
  onStopSimulator: () => void;
  onOpenSettings: () => void;
  onDiscover: (trafficKey: string) => void;
}> = ({ snap, onPause, onClear, busLabel, simulatorRunning, canStartSimulator, simError, onStartSimulator, onStopSimulator, onOpenSettings, onDiscover }) => {
  const [view, setView] = useState<View>('ids');
  const [simType, setSimType] = useState('car');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [now, setNow] = useState(() => performance.now());

  useEffect(() => {
    setNow(performance.now());
  }, [snap]);

  const rows = useMemo(() => snap.rows.filter((r) => matches(query, r.idText, r.last)), [snap.rows, query]);
  const stream = useMemo<BusFrame[]>(
    () => snap.recent.filter((f) => matches(query, f.idText, f.data)).slice(0, 300),
    [snap.recent, query],
  );
  const selectedRow = snap.rows.find((r) => r.key === selected) ?? null;
  const empty = snap.total === 0;

  return (
    <div className="flex h-full min-h-0 gap-3">
      <Card className="flex min-w-0 flex-1 flex-col overflow-hidden" testId="live-traffic">
        <CardHeader
          title={L('Canlı trafik', 'Live traffic')}
          hint={L(
            'Hattan gelen her çerçeve. Mavi parlayan baytlar az önce değişti.',
            'Every frame on the bus. Bytes flashing blue just changed.',
          )}
        >
          <Segmented<View>
            testId="traffic-view"
            value={view}
            onChange={setView}
            options={[
              { value: 'ids', label: L('Kimliğe göre', 'By id') },
              { value: 'stream', label: L('Akış', 'Stream') },
            ]}
          />
          <label className="flex items-center gap-2 rounded-lg border border-border-whisper px-2.5 py-1.5 focus-within:border-accent">
            <Search className="h-4 w-4 text-text-low" />
            <input
              data-testid="traffic-filter"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={L('Kimlik veya veri ara (7E8, 18FEF1…)', 'Search id or data (7E8, 18FEF1…)')}
              className="w-56 bg-transparent text-[13px] text-text-hi outline-none placeholder:text-text-low"
            />
          </label>
          <button
            type="button"
            data-testid="traffic-pause"
            className={BTN_GHOST}
            onClick={() => onPause(!snap.paused)}
            disabled={empty && !snap.paused}
          >
            {snap.paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
            {snap.paused ? L('Devam et', 'Resume') : L('Dondur', 'Freeze')}
          </button>
          <button type="button" className={BTN_QUIET} onClick={onClear} disabled={empty} title={L('Ekranı temizle', 'Clear view')}>
            <Trash2 className="h-4 w-4" />
          </button>
          {simulatorRunning && (
            <button type="button" data-testid="stop-simulator" className={BTN_GHOST} onClick={onStopSimulator}>
              {L('Simülatörü kapat', 'Stop simulator')}
            </button>
          )}
        </CardHeader>

        {snap.paused && (
          <div className="border-b border-warn-border bg-warn-soft px-5 py-2 text-[12.5px] text-warn" role="status">
            {L(
              'Ekran donduruldu: yeni çerçeveler gösterilmiyor (hat dinlenmeye devam ediyor, kayıt etkilenmez).',
              'View frozen: new frames are not shown (the bus is still being recorded).',
            )}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-auto">
          {empty ? (
            <EmptyState
              testId="traffic-empty"
              icon={Radio}
              title={simulatorRunning ? L('Simülatör başlıyor…', 'Simulator starting…') : L('Henüz çerçeve yok', 'No frames yet')}
              body={L(
                'Bir adaptör bağlı ve hat dinleniyorsa çerçeveler burada belirir. Araç yoksa denemek için simülatörü başlatabilirsiniz; simülatör verisi her yerde “Simülatör” olarak işaretlenir.',
                'Frames appear here once an adapter is connected and listening. No vehicle? Start the simulator to try it — simulator data is labelled “Simulator” everywhere.',
              )}
            >
              {canStartSimulator && !simulatorRunning && (
                <span className="inline-flex items-stretch gap-2">
                  <select
                    data-testid="sim-type"
                    value={simType}
                    onChange={(e) => setSimType(e.target.value)}
                    className="rounded-lg border border-border-strong bg-bg-card px-2.5 text-[13px] text-text-hi"
                    aria-label={L('Simüle edilecek araç', 'Vehicle to simulate')}
                  >
                    <option value="car">{L('Otomobil', 'Car')}</option>
                    <option value="truck">{L('Kamyon', 'Truck')}</option>
                    <option value="construction">{L('İş makinesi', 'Construction')}</option>
                    <option value="boat">{L('Tekne', 'Boat')}</option>
                  </select>
                  <button type="button" data-testid="start-simulator" className={BTN_PRIMARY} onClick={() => onStartSimulator(simType)}>
                    <FlaskConical className="h-4 w-4" />
                    {L('Simülatörü başlat', 'Start the simulator')}
                  </button>
                </span>
              )}
              <button type="button" className={BTN_GHOST} onClick={onOpenSettings}>
                {L('Adaptör ve hat ayarları', 'Adapter and bus settings')}
              </button>
              {(busLabel || simError) && (
                <span className="flex w-full flex-col items-center gap-1 pt-2 text-[12.5px]">
                  {busLabel && (
                    <span className="text-text-mid" data-testid="empty-bus">
                      {L('Dinlenen hat', 'Listening to')}: <b className="font-semibold text-text-body">{busLabel}</b>
                    </span>
                  )}
                  {simError && (
                    <span role="alert" className="text-del">
                      {simError}
                    </span>
                  )}
                </span>
              )}
            </EmptyState>
          ) : view === 'ids' ? (
            <table className="w-full text-left text-[13px]" data-testid="id-table">
              <thead className="sticky top-0 z-10 bg-bg-card text-[11.5px] uppercase tracking-wide text-text-low">
                <tr>
                  <th className="px-5 py-2.5 font-medium">{L('Kimlik', 'Id')}</th>
                  <th className="px-2 py-2.5 font-medium">{L('Tür', 'Type')}</th>
                  <th className="px-2 py-2.5 text-right font-medium">{L('Adet', 'Count')}</th>
                  <th className="px-2 py-2.5 text-right font-medium">{L('Sıklık', 'Rate')}</th>
                  <th className="px-2 py-2.5 text-right font-medium">DLC</th>
                  <th className="px-5 py-2.5 font-medium">{L('Son veri', 'Last data')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.key}
                    data-testid={`id-row-${r.idText}`}
                    onClick={() => setSelected(r.key === selected ? null : r.key)}
                    className={cx(
                      'cursor-pointer border-t border-border-whisper transition-colors',
                      r.key === selected ? 'bg-bg-row-selected' : 'hover:bg-bg-row-hover',
                      now - r.lastSeenAt > 3000 && 'opacity-50',
                    )}
                  >
                    <td className="px-5 py-2 font-mono font-semibold text-text-hi">{r.idText}</td>
                    <td className="px-2 py-2 text-text-mid">{r.fd ? 'FD' : r.extended ? '29-bit' : '11-bit'}</td>
                    <td className="px-2 py-2 text-right tabular-nums text-text-body">{r.count.toLocaleString('tr-TR')}</td>
                    <td className="px-2 py-2 text-right tabular-nums text-text-body">{fmtRate(r.rateHz)}</td>
                    <td className="px-2 py-2 text-right tabular-nums text-text-mid">{r.dlc}</td>
                    <td className="px-5 py-2">
                      <Bytes data={r.last} changedAt={r.byteChangedAt} now={now} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="w-full text-left text-[13px]" data-testid="stream-table">
              <thead className="sticky top-0 z-10 bg-bg-card text-[11.5px] uppercase tracking-wide text-text-low">
                <tr>
                  <th className="px-5 py-2.5 font-medium">{L('Zaman', 'Time')}</th>
                  <th className="px-2 py-2.5 font-medium">{L('Kimlik', 'Id')}</th>
                  <th className="px-2 py-2.5 text-right font-medium">DLC</th>
                  <th className="px-2 py-2.5 font-medium">{L('Veri', 'Data')}</th>
                  <th className="px-5 py-2.5 font-medium">{L('Kaynak', 'Source')}</th>
                </tr>
              </thead>
              <tbody>
                {stream.map((f) => (
                  <tr key={f.seq} className="border-t border-border-whisper">
                    <td className="px-5 py-1.5 font-mono tabular-nums text-text-mid">{f.t.toFixed(3)} s</td>
                    <td className="px-2 py-1.5 font-mono font-semibold text-text-hi">{f.idText}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-text-mid">{f.dlc}</td>
                    <td className="px-2 py-1.5">
                      <Bytes data={f.data} now={now} />
                    </td>
                    <td className="px-5 py-1.5 text-text-mid">{SOURCE_LABEL[f.source]()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {!empty && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border-whisper px-5 py-2.5 text-[12.5px] text-text-mid" data-testid="traffic-summary">
            <span>
              <b className="font-semibold text-text-hi tabular-nums">{snap.total.toLocaleString('tr-TR')}</b> {L('çerçeve', 'frames')}
            </span>
            <span>
              <b className="font-semibold text-text-hi tabular-nums">{snap.rows.length}</b> {L('farklı kimlik', 'distinct ids')}
            </span>
            <span>
              {L('Son 1 sn', 'Last 1 s')}: <b className="font-semibold text-text-hi tabular-nums">{snap.perSecond}</b> {L('çerçeve', 'frames')}
            </span>
            {query && (
              <span>
                {L('Filtre', 'Filter')}: {view === 'ids' ? rows.length : stream.length} {L('sonuç', 'matches')}
              </span>
            )}
            {view === 'stream' && <span className="text-text-low">{L('Akışta son 300 çerçeve gösterilir.', 'The stream shows the last 300 frames.')}</span>}
          </div>
        )}
      </Card>

      {selectedRow && view === 'ids' && (
        <IdInspector row={selectedRow} onClose={() => setSelected(null)} onDiscover={onDiscover} />
      )}
    </div>
  );
};

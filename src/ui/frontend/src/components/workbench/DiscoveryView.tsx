import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, ChevronDown, FileDown, Loader2, Waypoints } from 'lucide-react';
import { DesktopBridge, DiscoveryHypothesis, DiscoveryReport, DiscoveryStream } from '../../services/bridge';
import { L } from '../mechanic/text';
import { formatId } from './frameBus';
import { StimulusPanel } from './StimulusPanel';
import { BTN_GHOST, BTN_PRIMARY, Card, CardHeader, Chip, EmptyState, Segmented, cx } from './ui';

/**
 * Sinyal keşfi: the Python discovery engine's evidence for one stream —
 * how each bit behaves, which fields look like counters, checksums or
 * signals, and how sure the statistics are. Nothing is named for the
 * operator: a candidate stays a candidate until they approve it, and only
 * approved fields go into the DBC by default.
 */

const CLASS_STYLE: Record<string, { label: () => string; alpha: number }> = {
  CONST: { label: () => L('Sabit', 'Constant'), alpha: 0 },
  SPARSE: { label: () => L('Seyrek değişen', 'Rarely changes'), alpha: 0.3 },
  TOGGLE: { label: () => L('Düzenli değişen', 'Changes steadily'), alpha: 0.6 },
  NOISY: { label: () => L('Sürekli değişen', 'Changes constantly'), alpha: 0.95 },
};

const TYPE_LABEL: Record<string, () => string> = {
  COUNTER: () => L('Sayaç', 'Counter'),
  CHECKSUM: () => L('Sağlama toplamı', 'Checksum'),
  SIGNAL: () => L('Sinyal adayı', 'Signal candidate'),
  CONSTANT: () => L('Sabit alan', 'Constant field'),
};

const ERRORS: Record<string, () => string> = {
  NOTHING_TO_EXPORT: () => L('Dışa aktarılacak sinyal yok. Önce en az bir adayı onaylayın.', 'Nothing to export. Approve at least one candidate first.'),
  WRITE_FAILED: () => L('Dosya yazılamadı.', 'The file could not be written.'),
  BUILD_FAILED: () => L('DBC oluşturulamadı.', 'The DBC could not be built.'),
};

/** Live-traffic row key ("chan:x:arb") → discovery key ("chan|1|arb"). */
export function discoveryKeyFromTrafficKey(trafficKey: string): string | null {
  const last = trafficKey.lastIndexOf(':');
  const mid = trafficKey.lastIndexOf(':', last - 1);
  if (last < 0 || mid < 0) return null;
  const channel = trafficKey.slice(0, mid);
  const fmt = trafficKey.slice(mid + 1, last);
  const arb = trafficKey.slice(last + 1);
  if (fmt !== 'x' && fmt !== 's') return null;
  return `${channel}|${fmt === 'x' ? 1 : 0}|${arb}`;
}

function bitRange(h: DiscoveryHypothesis): Set<number> {
  const out = new Set<number>();
  for (let b = h.start_bit; b < h.start_bit + h.length; b += 1) out.add(b);
  return out;
}

const BitGrid: React.FC<{ classes: string[]; dlc: number; highlight: Set<number> | null }> = ({ classes, dlc, highlight }) => (
  <table className="border-separate border-spacing-[3px] text-center font-mono text-[11px]" data-testid="bit-grid">
    <thead>
      <tr className="text-text-low">
        <th className="w-12 text-left font-normal">{L('Bayt', 'Byte')}</th>
        {[7, 6, 5, 4, 3, 2, 1, 0].map((b) => (
          <th key={b} className="w-8 font-normal">
            {b}
          </th>
        ))}
      </tr>
    </thead>
    <tbody>
      {Array.from({ length: dlc }, (_, byte) => (
        <tr key={byte}>
          <td className="text-left text-text-mid">{byte}</td>
          {[7, 6, 5, 4, 3, 2, 1, 0].map((bit) => {
            const idx = byte * 8 + bit;
            const cls = classes[idx] ?? 'CONST';
            const style = CLASS_STYLE[cls] ?? CLASS_STYLE.CONST;
            const lit = highlight?.has(idx) ?? false;
            return (
              <td
                key={bit}
                title={`bit ${idx}: ${style.label()}`}
                className={cx('h-7 w-8 rounded-md border', lit ? 'border-text-hi' : 'border-border-whisper')}
                style={style.alpha ? { backgroundColor: `rgba(59,130,246,${style.alpha})` } : undefined}
              />
            );
          })}
        </tr>
      ))}
    </tbody>
  </table>
);

const HypothesisRow: React.FC<{
  h: DiscoveryHypothesis;
  busy: boolean;
  onToggle: () => void;
  onHover: (h: DiscoveryHypothesis | null) => void;
}> = ({ h, busy, onToggle, onHover }) => {
  const [open, setOpen] = useState(false);
  const approved = h.status === 'approved';
  const end = h.start_bit + h.length - 1;
  return (
    <li
      className={cx('rounded-xl border px-4 py-3', approved ? 'border-ok-border bg-ok-soft' : 'border-border-whisper')}
      onMouseEnter={() => onHover(h)}
      onMouseLeave={() => onHover(null)}
      data-testid={`hyp-${h.start_bit}-${h.length}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[13.5px] font-semibold text-text-hi">{(TYPE_LABEL[h.type] ?? (() => h.type))()}</div>
          <div className="text-[12px] text-text-mid">
            {L(`bit ${h.start_bit}–${end} · ${h.length} bit`, `bits ${h.start_bit}–${end} · ${h.length} bits`)}
            {h.length > 8 ? ` · ${h.little_endian ? 'Intel' : 'Motorola'}` : ''}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="w-28" title={L('İstatistiksel güven', 'Statistical confidence')}>
            <div className="h-1.5 rounded-full bg-border-whisper">
              <div className="h-1.5 rounded-full bg-accent" style={{ width: `${Math.round(h.confidence * 100)}%` }} />
            </div>
            <div className="mt-0.5 text-right text-[11.5px] tabular-nums text-text-mid">%{Math.round(h.confidence * 100)}</div>
          </div>
          <button
            type="button"
            className={approved ? BTN_GHOST : BTN_PRIMARY}
            disabled={busy}
            onClick={onToggle}
            data-testid={`approve-${h.start_bit}-${h.length}`}
          >
            {approved ? (
              <>
                <Check className="h-4 w-4" />
                {L('Onaylandı', 'Approved')}
              </>
            ) : (
              L('Onayla', 'Approve')
            )}
          </button>
        </div>
      </div>
      {h.evidence.length > 0 && (
        <button type="button" className="mt-2 inline-flex items-center gap-1 text-[12px] text-text-mid hover:text-text-hi" onClick={() => setOpen((o) => !o)}>
          <ChevronDown className={cx('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
          {L('Teknik kanıt', 'Technical evidence')}
        </button>
      )}
      {open && (
        <ul className="mt-1.5 list-disc pl-5 text-[12px] text-text-mid">
          {h.evidence.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
    </li>
  );
};

type Tab = 'analysis' | 'experiment';

export const DiscoveryView: React.FC<{ initialKey: string | null; simulator: boolean }> = ({ initialKey, simulator }) => {
  const [tab, setTab] = useState<Tab>('analysis');
  const [streams, setStreams] = useState<DiscoveryStream[]>([]);
  const [minFrames, setMinFrames] = useState(10);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(initialKey);
  const [report, setReport] = useState<DiscoveryReport | null>(null);
  const [hover, setHover] = useState<DiscoveryHypothesis | null>(null);
  const [busy, setBusy] = useState(false);
  const [approvedOnly, setApprovedOnly] = useState(true);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const loadList = useCallback(async () => {
    const res = await DesktopBridge.discoveryList();
    if (!res) return;
    setStreams(res.streams);
    setMinFrames(res.min_frames);
    setLoaded(true);
  }, []);

  const loadReport = useCallback(async (key: string) => {
    const res = await DesktopBridge.discoveryReport(key);
    setReport(res);
  }, []);

  useEffect(() => {
    void loadList();
    const t = window.setInterval(() => void loadList(), 2000);
    return () => window.clearInterval(t);
  }, [loadList]);

  useEffect(() => {
    if (initialKey) setSelected(initialKey);
  }, [initialKey]);

  useEffect(() => {
    if (!selected && streams.length) {
      const first = streams.find((s) => s.analyzable) ?? streams[0];
      setSelected(first.key);
    }
  }, [streams, selected]);

  useEffect(() => {
    if (!selected) return undefined;
    void loadReport(selected);
    const t = window.setInterval(() => void loadReport(selected), 3000);
    return () => window.clearInterval(t);
  }, [selected, loadReport]);

  const stream = streams.find((s) => s.key === selected) ?? null;
  const highlight = useMemo(() => (hover ? bitRange(hover) : null), [hover]);
  const approvedCount = report?.hypotheses?.filter((h) => h.status === 'approved').length ?? 0;
  const anySimulated = streams.some((s) => s.simulated);

  const toggle = async (h: DiscoveryHypothesis) => {
    if (!selected) return;
    setBusy(true);
    try {
      await DesktopBridge.discoverySetApproval(selected, h.start_bit, h.length, h.status !== 'approved');
      await loadReport(selected);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setSaveMsg(null);
    try {
      const res = await DesktopBridge.discoverySaveDbc(approvedOnly);
      setSaveMsg(
        res.success
          ? {
              ok: true,
              text: `${L('Kaydedildi', 'Saved')}: ${res.path} · ${res.signals} ${L('sinyal', 'signals')}${
                res.simulated ? ` · ${L('simülatör verisinden', 'from simulator data')}` : ''
              }`,
            }
          : { ok: false, text: (ERRORS[res.error_code ?? ''] ?? (() => L('Kaydedilemedi.', 'Could not save.')))() },
      );
    } finally {
      setBusy(false);
    }
  };

  if (loaded && streams.length === 0) {
    return (
      <Card className="h-full" testId="discovery-view">
        <EmptyState
          testId="discovery-empty"
          icon={Waypoints}
          title={L('Henüz incelenecek kimlik yok', 'No ids to analyse yet')}
          body={L(
            'Hat dinlendikçe her kimliğin çerçeveleri burada toplanır. Bir kimliğin bitlerini yorumlamak için en az birkaç saniyelik trafik gerekir.',
            'Frames of every id collect here while the bus is listened to. A few seconds of traffic are needed to interpret an id’s bits.',
          )}
        />
      </Card>
    );
  }

  const tabs = (
    <Segmented<Tab>
      testId="discovery-tab"
      value={tab}
      onChange={setTab}
      options={[
        { value: 'analysis', label: L('Kimlik analizi', 'Id analysis') },
        { value: 'experiment', label: L('Bas-bırak deneyi', 'Press-and-release') },
      ]}
    />
  );

  if (tab === 'experiment') {
    return (
      <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto" data-testid="discovery-view">
        <div>{tabs}</div>
        <StimulusPanel
          simulator={simulator}
          onInspect={(key) => {
            setSelected(key);
            setTab('analysis');
          }}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3" data-testid="discovery-view">
      <div>{tabs}</div>
      <div className="flex min-h-0 flex-1 gap-3">
      <Card className="flex w-[280px] flex-none flex-col overflow-hidden">
        <CardHeader title={L('Kimlikler', 'Ids')} hint={L('Bir kimlik seçin; bitlerinin nasıl davrandığı sağda görünür.', 'Pick an id to see how its bits behave.')} />
        <ul className="flex-1 overflow-auto p-2">
          {streams.map((s) => (
            <li key={s.key}>
              <button
                type="button"
                data-testid={`stream-${formatId(s.arbitration_id, s.extended)}`}
                aria-pressed={s.key === selected}
                onClick={() => setSelected(s.key)}
                className={cx('flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left', s.key === selected ? 'bg-bg-row-selected' : 'hover:bg-bg-row-hover')}
              >
                <span className="min-w-0">
                  <span className="block font-mono text-[13px] font-semibold text-text-hi">{formatId(s.arbitration_id, s.extended)}</span>
                  <span className="block text-[12px] text-text-mid">
                    {s.frames.toLocaleString('tr-TR')} {L('çerçeve', 'frames')}
                    {!s.analyzable ? ` · ${L(`en az ${minFrames} gerekli`, `needs ${minFrames}`)}` : ''}
                  </span>
                </span>
                {s.simulated && <Chip tone="warn">{L('Simülatör', 'Simulator')}</Chip>}
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-auto">
        {report?.success && stream ? (
          <>
            <Card>
              <CardHeader
                title={formatId(stream.arbitration_id, stream.extended)}
                hint={`${report.frames?.toLocaleString('tr-TR')} ${L('çerçeve', 'frames')} · ${report.rate_hz} Hz · DLC ${report.dlc} · ${stream.channel}`}
              >
                {report.simulated && <Chip tone="warn">{L('Simülatör verisi', 'Simulator data')}</Chip>}
              </CardHeader>
              <div className="flex flex-wrap gap-6 p-5">
                <BitGrid classes={report.bit_classes ?? []} dlc={report.dlc ?? 0} highlight={highlight} />
                <div className="flex min-w-[220px] flex-col gap-2 text-[12.5px]">
                  <div className="font-semibold text-text-hi">{L('Bit davranışı', 'Bit behaviour')}</div>
                  {Object.entries(CLASS_STYLE).map(([k, v]) => (
                    <div key={k} className="flex items-center gap-2 text-text-body">
                      <span className="h-4 w-4 rounded border border-border-whisper" style={v.alpha ? { backgroundColor: `rgba(59,130,246,${v.alpha})` } : undefined} />
                      {v.label()}
                    </div>
                  ))}
                  <p className="mt-2 max-w-xs text-text-mid">
                    {L(
                      'Bir adayın üzerine gelin: kapsadığı bitler çerçeveyle işaretlenir. Hiç değişmeyen bitler sabit ya da kullanılmıyordur.',
                      'Hover a candidate to outline its bits. Bits that never change are constant or unused.',
                    )}
                  </p>
                </div>
              </div>
            </Card>

            <Card>
              <CardHeader
                title={L('Adaylar', 'Candidates')}
                hint={L(
                  'Bunlar istatistiksel tahminlerdir. Ne anlama geldiklerini (gaz pedalı, devir…) siz doğrularsınız; yalnız onayladıklarınız DBC’ye girer.',
                  'These are statistical guesses. You confirm what they mean (pedal, speed…); only what you approve goes into the DBC.',
                )}
              >
                <Chip tone={approvedCount ? 'ok' : 'neutral'}>
                  {approvedCount} {L('onaylı', 'approved')}
                </Chip>
              </CardHeader>
              {!report.analyzable ? (
                <p className="p-5 text-[13px] text-text-mid">{L(`Yorum için en az ${minFrames} çerçeve gerekiyor.`, `At least ${minFrames} frames are needed.`)}</p>
              ) : (report.hypotheses?.length ?? 0) === 0 ? (
                <p className="p-5 text-[13px] text-text-mid">{L('Güçlü bir aday bulunamadı.', 'No strong candidate found.')}</p>
              ) : (
                <ul className="flex flex-col gap-2 p-4" data-testid="hypotheses">
                  {report.hypotheses?.map((h) => (
                    <HypothesisRow key={`${h.start_bit}-${h.length}-${h.type}`} h={h} busy={busy} onToggle={() => void toggle(h)} onHover={setHover} />
                  ))}
                </ul>
              )}
            </Card>
          </>
        ) : (
          <Card>
            <p className="flex items-center gap-2 p-6 text-[13px] text-text-mid">
              <Loader2 className="h-4 w-4 animate-spin" />
              {L('Analiz ediliyor…', 'Analysing…')}
            </p>
          </Card>
        )}

        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3 p-5">
            <div className="min-w-0">
              <div className="text-[14px] font-semibold text-text-hi">{L('DBC olarak kaydet', 'Save as DBC')}</div>
              <label className="mt-1 flex items-center gap-2 text-[12.5px] text-text-mid">
                <input type="checkbox" checked={approvedOnly} onChange={(e) => setApprovedOnly(e.target.checked)} data-testid="dbc-approved-only" />
                {L('Yalnız onayladığım adaylar', 'Only the candidates I approved')}
              </label>
              {anySimulated && (
                <p className="mt-1 text-[12.5px] text-warn">
                  {L('Simülatör verisi var: dosya adına _SIMULATOR eklenir.', 'Contains simulator data: the file name gets _SIMULATOR.')}
                </p>
              )}
            </div>
            <button type="button" className={BTN_GHOST} onClick={() => void save()} disabled={busy} data-testid="save-dbc">
              <FileDown className="h-4 w-4" />
              {L('Kaydet', 'Save')}
            </button>
          </div>
          {saveMsg && (
            <p role="status" data-testid="save-dbc-result" className={cx('break-all px-5 pb-4 text-[12.5px]', saveMsg.ok ? 'text-ok' : 'text-del')}>
              {saveMsg.text}
            </p>
          )}
        </Card>
      </div>
      </div>
    </div>
  );
};

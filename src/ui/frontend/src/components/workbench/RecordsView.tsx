import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Check, CloudUpload, Download, FileText, FolderOpen, Loader2, Play, RefreshCw, Square } from 'lucide-react';
import { DesktopBridge, RecordEntry, ReplayStatus } from '../../services/bridge';
import { L } from '../mechanic/text';
import { BTN_GHOST, BTN_PRIMARY, BTN_QUIET, Card, CardHeader, Chip, EmptyState, Segmented, Tone, cx } from './ui';

/**
 * Kayıt ve rapor: everything leaves the app through Python (the ring buffer
 * and the diagnostic session), never through a browser-side re-encoding of
 * whatever the screen happened to hold. Files are addressed by the opaque
 * ids `records_list` hands out; no path crosses the bridge.
 *
 * Replay feeds the decoders only (never the bus); its values are labelled
 * "Kayıttan" and stay out of the diagnostic evidence. Upload happens only
 * after the native dialog that names the file.
 */

type Fmt = 'csv' | 'json' | 'mf4' | 'mat';
type Filter = 'all' | 'trace' | 'report' | 'other';
type Outcome = { ok: boolean; text: string } | null;

const FORMATS: Array<{ id: Fmt; title: string; sub: () => string }> = [
  { id: 'csv', title: 'CSV', sub: () => L('Excel ve tablo programları', 'Excel and spreadsheets') },
  { id: 'mf4', title: 'MDF4', sub: () => L('ASAM standardı: CANape, asammdf, Vector', 'ASAM standard: CANape, asammdf, Vector') },
  { id: 'mat', title: 'MAT', sub: () => L('MATLAB / Simulink', 'MATLAB / Simulink') },
  { id: 'json', title: 'JSON', sub: () => L('Kendi yazılımınız ve betikler', 'Your own tools and scripts') },
];

const KIND: Record<RecordEntry['kind'], { tone: Tone; label: () => string }> = {
  trace: { tone: 'accent', label: () => L('İz kaydı', 'Trace') },
  report: { tone: 'ok', label: () => L('Rapor', 'Report') },
  dbc: { tone: 'neutral', label: () => 'DBC' },
  log: { tone: 'neutral', label: () => L('Günlük', 'Log') },
  other: { tone: 'neutral', label: () => L('Dosya', 'File') },
};

const FOLDER: Record<RecordEntry['folder'], () => string> = {
  exports: () => L('Dışa aktarılanlar', 'Exports'),
  logs: () => L('Günlükler', 'Logs'),
  traces: () => L('İz kayıtları', 'Traces'),
  reports: () => L('Raporlar', 'Reports'),
};

const SPEEDS = ['0.5', '1', '2', '5', '10'] as const;

const ERRORS: Record<string, () => string> = {
  RECORD_UNKNOWN: () => L('Dosya artık yok ya da bu işlem için uygun değil. Listeyi yenileyin.', 'The file is gone or not eligible. Refresh the list.'),
  RECORD_UNREADABLE: () => L('Dosya okunamadı ya da içinde çerçeve yok.', 'The file could not be read or holds no frames.'),
  REPLAY_RUNNING: () => L('Bir kayıt zaten oynatılıyor.', 'A recording is already playing.'),
  INVALID_SPEED: () => L('Geçersiz hız.', 'Invalid speed.'),
  NOT_SIGNED_IN: () => L('Buluta yüklemek için oturum açın.', 'Sign in to upload.'),
  NOT_CONFIRMED: () => L('Yükleme onaylanmadı; hiçbir şey gönderilmedi.', 'Upload not confirmed; nothing was sent.'),
  UPLOAD_FAILED: () => L('Yükleme tamamlanamadı (bağlantı ya da sunucu). Daha sonra yeniden deneyin.', 'The upload did not complete (network or server). Try again later.'),
  OPEN_FAILED: () => L('Klasör açılamadı.', 'The folder could not be opened.'),
};
const errorText = (code?: string) => ERRORS[code ?? '']?.() ?? L('İşlem tamamlanamadı.', 'The operation did not complete.');

const fmtSize = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
const fmtDate = (t: number) =>
  new Date(t * 1000).toLocaleString('tr-TR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });

const Message: React.FC<{ outcome: Outcome; testId: string }> = ({ outcome, testId }) =>
  outcome ? (
    <p role="status" data-testid={testId} className={cx('break-all text-[13px]', outcome.ok ? 'text-ok' : 'text-del')}>
      {outcome.text}
    </p>
  ) : null;

// ---------------------------------------------------------------------------

const FilesCard: React.FC<{
  records: RecordEntry[] | null;
  selected: RecordEntry | null;
  onSelect: (r: RecordEntry) => void;
  onRefresh: () => void;
}> = ({ records, selected, onSelect, onRefresh }) => {
  const [filter, setFilter] = useState<Filter>('all');
  const [opening, setOpening] = useState<Outcome>(null);
  const shown = (records ?? []).filter((r) =>
    filter === 'all' ? true : filter === 'other' ? r.kind !== 'trace' && r.kind !== 'report' : r.kind === filter,
  );

  const openFolder = async () => {
    const res = await DesktopBridge.recordsOpenFolder();
    setOpening(res.success ? null : { ok: false, text: errorText(res.error_code) });
  };

  return (
    <Card className="flex min-h-0 flex-col" testId="records-files">
      <CardHeader title={L('Dosyalar', 'Files')} hint={L('Uygulamanın bu bilgisayara yazdığı kayıtlar ve raporlar.', 'Captures and reports the app wrote on this computer.')}>
        <button type="button" className={BTN_QUIET} onClick={onRefresh} data-testid="records-refresh">
          <RefreshCw className="h-4 w-4" />
          {L('Yenile', 'Refresh')}
        </button>
        <button type="button" className={BTN_GHOST} onClick={() => void openFolder()} data-testid="records-open-folder">
          <FolderOpen className="h-4 w-4" />
          {L('Klasörü aç', 'Open folder')}
        </button>
      </CardHeader>
      <div className="flex flex-wrap items-center gap-3 px-5 pt-4">
        <Segmented
          testId="records-filter"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: L('Tümü', 'All') },
            { value: 'trace', label: L('İz kaydı', 'Traces') },
            { value: 'report', label: L('Rapor', 'Reports') },
            { value: 'other', label: L('Diğer', 'Other') },
          ]}
        />
        <Message outcome={opening} testId="records-open-result" />
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-3 py-3">
        {records === null && <p className="px-2 text-[13px] text-text-mid">{L('Yükleniyor…', 'Loading…')}</p>}
        {records !== null && shown.length === 0 && (
          <EmptyState
            testId="records-empty"
            icon={FileText}
            title={L('Henüz dosya yok', 'No files yet')}
            body={L('Ham kaydı dışa aktarın ya da rapor oluşturun; dosyalar burada listelenir.', 'Export the raw capture or build a report; the files are listed here.')}
          />
        )}
        <ul className="flex flex-col gap-1">
          {shown.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onSelect(r)}
                aria-pressed={selected?.id === r.id}
                data-testid={`record-${r.id}`}
                className={cx(
                  'flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors',
                  selected?.id === r.id ? 'bg-bg-row-selected' : 'hover:bg-bg-row-hover',
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-[12.5px] text-text-hi">{r.name}</span>
                  <span className="block text-[11.5px] text-text-mid">
                    {FOLDER[r.folder]()} · {fmtSize(r.size)} · {fmtDate(r.modified)}
                  </span>
                </span>
                <Chip tone={KIND[r.kind].tone}>{KIND[r.kind].label()}</Chip>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
};

// ---------------------------------------------------------------------------

const SelectedCard: React.FC<{ record: RecordEntry; replay: ReplayStatus | null; onReplayChanged: () => void }> = ({ record, replay, onReplayChanged }) => {
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>('1');
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [busy, setBusy] = useState<'play' | 'upload' | null>(null);
  useEffect(() => setOutcome(null), [record.id]);

  const play = async () => {
    setBusy('play');
    setOutcome(null);
    try {
      const res = await DesktopBridge.recordsReplayStart(record.id, Number(speed));
      if (!res.success) setOutcome({ ok: false, text: errorText(res.error_code) });
    } finally {
      setBusy(null);
      onReplayChanged();
    }
  };

  const upload = async () => {
    setBusy('upload');
    setOutcome(null);
    try {
      const res = await DesktopBridge.recordsUpload(record.id);
      setOutcome(res.success ? { ok: true, text: L('Buluta yüklendi.', 'Uploaded to the cloud.') } : { ok: false, text: errorText(res.error_code) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card testId="records-selected">
      <CardHeader title={record.name} hint={`${FOLDER[record.folder]()} · ${fmtSize(record.size)} · ${fmtDate(record.modified)}`}>
        <Chip tone={KIND[record.kind].tone}>{KIND[record.kind].label()}</Chip>
      </CardHeader>
      <div className="flex flex-col gap-4 p-5">
        {record.replayable && (
          <div className="flex flex-col gap-2">
            <div className="text-[13px] font-semibold text-text-hi">{L('Kayıttan oynat', 'Replay')}</div>
            <p className="text-[12.5px] text-text-mid">
              {L(
                'Çerçeveler canlı trafik, grafik ve keşif ekranlarına "Kayıttan" etiketiyle gelir. Araca gönderilmez; teşhis kanıtına ve hız kilidine girmez.',
                'Frames reach live traffic, plot and discovery labelled "Replay". Nothing is sent to the vehicle; nothing enters the diagnostic evidence or the speed interlock.',
              )}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Segmented testId="replay-speed" value={speed} onChange={setSpeed} options={SPEEDS.map((s) => ({ value: s, label: `${s}×` }))} />
              <button type="button" className={BTN_PRIMARY} onClick={() => void play()} disabled={busy !== null || Boolean(replay?.running)} data-testid="replay-start">
                {busy === 'play' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {L('Oynat', 'Play')}
              </button>
            </div>
          </div>
        )}
        {record.uploadable && (
          <div className="flex flex-col gap-2 border-t border-border-whisper pt-4">
            <div className="text-[13px] font-semibold text-text-hi">{L('Buluta yükle', 'Upload to the cloud')}</div>
            <p className="text-[12.5px] text-text-mid">
              {L(
                'Windows onay penceresi dosyanın adını ve boyutunu gösterir; onaylamazsanız hiçbir şey gönderilmez. Araç kimlik numarası (VIN) eklenmez.',
                'A Windows dialog names the file and its size; nothing is sent unless you confirm it. The VIN is not attached.',
              )}
            </p>
            <div>
              <button type="button" className={BTN_GHOST} onClick={() => void upload()} disabled={busy !== null} data-testid="record-upload">
                {busy === 'upload' ? <Loader2 className="h-4 w-4 animate-spin" /> : <CloudUpload className="h-4 w-4" />}
                {L('Yükle', 'Upload')}
              </button>
            </div>
          </div>
        )}
        {!record.replayable && !record.uploadable && (
          <p className="text-[12.5px] text-text-mid">{L('Bu dosyayı “Klasörü aç” ile açabilirsiniz.', 'Open this file from “Open folder”.')}</p>
        )}
        <Message outcome={outcome} testId="record-result" />
      </div>
    </Card>
  );
};

// ---------------------------------------------------------------------------

export const RecordsView: React.FC<{ totalFrames: number; sourceNote: string | null }> = ({ totalFrames, sourceNote }) => {
  const [fmt, setFmt] = useState<Fmt>('csv');
  const [busy, setBusy] = useState<'raw' | 'report' | null>(null);
  const [rawOutcome, setRawOutcome] = useState<Outcome>(null);
  const [reportOutcome, setReportOutcome] = useState<Outcome>(null);
  const [records, setRecords] = useState<RecordEntry[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [replay, setReplay] = useState<ReplayStatus | null>(null);
  const poll = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    const res = await DesktopBridge.recordsList();
    setRecords(res.records);
  }, []);

  const loadReplay = useCallback(async () => {
    const st = await DesktopBridge.recordsReplayStatus();
    setReplay(st.success ? st : null);
    return st;
  }, []);

  const watchReplay = useCallback(() => {
    if (poll.current !== null) window.clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      const st = await loadReplay();
      if (!st.running && poll.current !== null) {
        window.clearInterval(poll.current);
        poll.current = null;
      }
    }, 400);
  }, [loadReplay]);

  useEffect(() => {
    void refresh();
    void loadReplay().then((st) => st.running && watchReplay());
    return () => {
      if (poll.current !== null) window.clearInterval(poll.current);
    };
  }, [refresh, loadReplay, watchReplay]);

  const exportRaw = async () => {
    setBusy('raw');
    setRawOutcome(null);
    try {
      const res = await DesktopBridge.exportLogs(fmt);
      setRawOutcome(
        res.success
          ? { ok: true, text: L('Kaydedildi; dosyalar listesinde.', 'Saved; it is in the file list.') }
          : { ok: false, text: L('Dışa aktarılamadı. Ayrıntı uygulama günlüğünde.', 'Export failed. See the application log.') },
      );
    } catch (err) {
      setRawOutcome({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
      void refresh();
    }
  };

  const exportReport = async () => {
    setBusy('report');
    setReportOutcome(null);
    try {
      const res = await DesktopBridge.exportSessionReport();
      setReportOutcome(
        res.success
          ? { ok: true, text: L('Rapor kaydedildi; dosyalar listesinde.', 'Report saved; it is in the file list.') }
          : { ok: false, text: res.error || L('Rapor oluşturulamadı.', 'The report could not be built.') },
      );
    } catch (err) {
      setReportOutcome({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
      void refresh();
    }
  };

  const stopReplay = async () => {
    setReplay(await DesktopBridge.recordsReplayStop());
  };

  const selected = records?.find((r) => r.id === selectedId) ?? null;
  const pct = replay?.frame_count ? Math.round(((replay.position ?? 0) / replay.frame_count) * 100) : 0;

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-3 overflow-auto xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]" data-testid="records-view">
      <div className="flex min-h-0 min-w-0 flex-col gap-3">
        {replay?.running && (
          <Card testId="replay-status">
            <CardHeader title={L('Kayıttan oynatılıyor', 'Replaying')} hint={replay.name ?? ''}>
              <button type="button" className={BTN_GHOST} onClick={() => void stopReplay()} data-testid="replay-stop">
                <Square className="h-4 w-4" />
                {L('Durdur', 'Stop')}
              </button>
            </CardHeader>
            <div className="flex flex-col gap-2 p-5">
              <div className="h-2 rounded-full bg-border-whisper">
                <div className="h-2 rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
              </div>
              <div className="text-[12.5px] tabular-nums text-text-mid">
                {(replay.position ?? 0).toLocaleString('tr-TR')} / {(replay.frame_count ?? 0).toLocaleString('tr-TR')} {L('çerçeve', 'frames')}
                {replay.filtered ? ` · ${replay.filtered} ${L('çerçeve güvenlik süzgecinde ayıklandı', 'frames removed by the safety filter')}` : ''}
              </div>
            </div>
          </Card>
        )}
        <FilesCard records={records} selected={selected} onSelect={(r) => setSelectedId(r.id)} onRefresh={() => void refresh()} />
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        {selected && <SelectedCard record={selected} replay={replay} onReplayChanged={() => void loadReplay().then(() => watchReplay())} />}

        <Card>
          <CardHeader
            title={L('Ham kaydı dışa aktar', 'Export the raw capture')}
            hint={L('Uygulamanın hafızasındaki son çerçeveler, değiştirilmeden dosyaya yazılır.', 'The most recent frames in the capture buffer, written to file unchanged.')}
          >
            <Chip tone={totalFrames ? 'accent' : 'neutral'}>
              {totalFrames.toLocaleString('tr-TR')} {L('çerçeve görüldü', 'frames seen')}
            </Chip>
          </CardHeader>
          <div className="grid grid-cols-2 gap-2 p-5" role="radiogroup" aria-label={L('Biçim', 'Format')}>
            {FORMATS.map((f) => (
              <button
                key={f.id}
                type="button"
                role="radio"
                aria-checked={fmt === f.id}
                data-testid={`format-${f.id}`}
                onClick={() => setFmt(f.id)}
                className={cx(
                  'flex items-start gap-3 rounded-xl border p-3 text-left transition-colors',
                  fmt === f.id ? 'border-accent bg-accent-soft' : 'border-border-strong hover:border-accent',
                )}
              >
                <span
                  className={cx(
                    'mt-0.5 flex h-4 w-4 flex-none items-center justify-center rounded-full border',
                    fmt === f.id ? 'border-accent bg-accent text-bg-app' : 'border-border-strong',
                  )}
                >
                  {fmt === f.id && <Check className="h-3 w-3" />}
                </span>
                <span>
                  <span className="block font-semibold text-text-hi">{f.title}</span>
                  <span className="block text-[12px] text-text-mid">{f.sub()}</span>
                </span>
              </button>
            ))}
          </div>
          <div className="flex flex-col gap-3 border-t border-border-whisper p-5">
            {sourceNote && <p className="text-[12.5px] text-warn">{sourceNote}</p>}
            <button type="button" data-testid="export-raw" className={BTN_PRIMARY} onClick={() => void exportRaw()} disabled={busy !== null || totalFrames === 0}>
              {busy === 'raw' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {L('Dışa aktar', 'Export')}
            </button>
            {totalFrames === 0 && (
              <p className="text-[12.5px] text-text-mid">{L('Dışa aktarılacak çerçeve yok; önce hattı dinleyin.', 'Nothing to export yet; listen to the bus first.')}</p>
            )}
            <Message outcome={rawOutcome} testId="export-raw-result" />
          </div>
        </Card>

        <Card>
          <CardHeader
            title={L('Teknisyen raporu', 'Technician report')}
            hint={L(
              'Teşhis oturumundaki kodlar, kanıtlar ve önerilen ayırt edici testler tek bir belgede.',
              'Codes, evidence and suggested discriminating tests from the diagnostic session, in one document (Markdown).',
            )}
          />
          <div className="flex flex-col gap-3 p-5">
            <ul className="flex flex-col gap-1.5 text-[13px] text-text-body">
              <li>• {L('Etkin oturum yoksa rapor oluşturulmaz; boş rapor üretilmez.', 'Without an active session no report is built; never an empty one.')}</li>
              <li>• {L('Simülatör ve kayıttan oynatılan veriler rapora girmez.', 'Simulator and replayed data never enter the report.')}</li>
              <li>• {L('Dosya bu bilgisayarda kalır; kendiliğinden buluta gönderilmez.', 'The file stays on this computer; nothing is uploaded on its own.')}</li>
            </ul>
            <button type="button" data-testid="export-report" className={BTN_GHOST} onClick={() => void exportReport()} disabled={busy !== null}>
              {busy === 'report' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
              {L('Raporu oluştur', 'Build the report')}
            </button>
            <Message outcome={reportOutcome} testId="export-report-result" />
          </div>
        </Card>
      </div>
    </div>
  );
};

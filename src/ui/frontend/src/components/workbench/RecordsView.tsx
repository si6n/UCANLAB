import React, { useState } from 'react';
import { Check, Download, FileText, Loader2 } from 'lucide-react';
import { DesktopBridge } from '../../services/bridge';
import { L } from '../mechanic/text';
import { BTN_GHOST, BTN_PRIMARY, Card, CardHeader, Chip, cx } from './ui';

/**
 * Kayıt ve rapor: everything leaves the app through Python (the ring buffer
 * and the diagnostic session), never through a browser-side re-encoding of
 * whatever the screen happened to hold.
 */

type Fmt = 'csv' | 'json' | 'mf4' | 'mat';

const FORMATS: Array<{ id: Fmt; title: string; sub: () => string }> = [
  { id: 'csv', title: 'CSV', sub: () => L('Excel ve tablo programları', 'Excel and spreadsheets') },
  { id: 'mf4', title: 'MDF4', sub: () => L('ASAM standardı: CANape, asammdf, Vector', 'ASAM standard: CANape, asammdf, Vector') },
  { id: 'mat', title: 'MAT', sub: () => L('MATLAB / Simulink', 'MATLAB / Simulink') },
  { id: 'json', title: 'JSON', sub: () => L('Kendi yazılımınız ve betikler', 'Your own tools and scripts') },
];

type Outcome = { ok: boolean; text: string } | null;

export const RecordsView: React.FC<{ totalFrames: number; sourceNote: string | null }> = ({ totalFrames, sourceNote }) => {
  const [fmt, setFmt] = useState<Fmt>('csv');
  const [busy, setBusy] = useState<'raw' | 'report' | null>(null);
  const [rawOutcome, setRawOutcome] = useState<Outcome>(null);
  const [reportOutcome, setReportOutcome] = useState<Outcome>(null);

  const exportRaw = async () => {
    setBusy('raw');
    setRawOutcome(null);
    try {
      const res = await DesktopBridge.exportLogs(fmt);
      setRawOutcome(
        res.success
          ? { ok: true, text: L("Kaydedildi: uygulama veri klasöründe 'exports' altında.", "Saved under 'exports' in the app data folder.") }
          : { ok: false, text: L('Dışa aktarılamadı. Ayrıntı uygulama günlüğünde.', 'Export failed. See the application log.') },
      );
    } catch (err) {
      setRawOutcome({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  };

  const exportReport = async () => {
    setBusy('report');
    setReportOutcome(null);
    try {
      const res = await DesktopBridge.exportSessionReport();
      setReportOutcome(
        res.success
          ? { ok: true, text: res.path ? `${L('Kaydedildi', 'Saved')}: ${res.path}` : L('Kaydedildi.', 'Saved.') }
          : { ok: false, text: res.error || L('Rapor oluşturulamadı.', 'The report could not be built.') },
      );
    } catch (err) {
      setReportOutcome({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-1 content-start gap-3 overflow-auto xl:grid-cols-2" data-testid="records-view">
      <Card>
        <CardHeader
          title={L('Ham kaydı dışa aktar', 'Export the raw capture')}
          hint={L(
            'Uygulamanın hafızasındaki son çerçeveler, değiştirilmeden dosyaya yazılır.',
            'The most recent frames in the capture buffer, written to file unchanged.',
          )}
        >
          <Chip tone={totalFrames ? 'accent' : 'neutral'}>
            {totalFrames.toLocaleString('tr-TR')} {L('çerçeve görüldü', 'frames seen')}
          </Chip>
        </CardHeader>
        <div className="grid grid-cols-1 gap-2 p-5 sm:grid-cols-2" role="radiogroup" aria-label={L('Biçim', 'Format')}>
          {FORMATS.map((f) => (
            <button
              key={f.id}
              type="button"
              role="radio"
              aria-checked={fmt === f.id}
              data-testid={`format-${f.id}`}
              onClick={() => setFmt(f.id)}
              className={cx(
                'flex items-start gap-3 rounded-xl border p-3.5 text-left transition-colors',
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
                <span className="block text-[12.5px] text-text-mid">{f.sub()}</span>
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
          {rawOutcome && (
            <p role="status" data-testid="export-raw-result" className={cx('text-[13px]', rawOutcome.ok ? 'text-ok' : 'text-del')}>
              {rawOutcome.text}
            </p>
          )}
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
            <li>• {L('Dosya bu bilgisayarda kalır; buluta gönderilmez.', 'The file stays on this computer; nothing is uploaded.')}</li>
          </ul>
          <button type="button" data-testid="export-report" className={BTN_GHOST} onClick={() => void exportReport()} disabled={busy !== null}>
            {busy === 'report' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
            {L('Raporu oluştur', 'Build the report')}
          </button>
          {reportOutcome && (
            <p role="status" data-testid="export-report-result" className={cx('break-all text-[13px]', reportOutcome.ok ? 'text-ok' : 'text-del')}>
              {reportOutcome.text}
            </p>
          )}
        </div>
      </Card>
    </div>
  );
};

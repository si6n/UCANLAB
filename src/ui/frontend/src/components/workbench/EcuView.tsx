import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, CircleSlash, Cpu, FileUp, Loader2, Square } from 'lucide-react';
import { DesktopBridge, FlashPreconditions } from '../../services/bridge';
import { EcuId, FLASH_ALLOWED_BLOCK_SIZES, FLASH_ECU_BOUNDS, buildFlashRequest } from '../ecu/flashRequest';
import { L } from '../mechanic/text';
import { BTN_GHOST, BTN_PRIMARY, Card, CardHeader, Chip, cx } from './ui';

/**
 * ECU programlama: the one workbench screen that writes to a vehicle. The UI
 * only gathers the material and shows the gates; Python decides. The flow is
 * the existing one — challenge bound to the exact request (image bound by
 * its SHA-256), native OS confirmation in physical mode, E-Stop and speed
 * interlock in flash_start — and on the workbench simulator it runs as a dry
 * run that sends nothing.
 */

const ECUS: Array<{ id: EcuId; label: () => string }> = [
  { id: 'ECM', label: () => L('Motor (ECM)', 'Engine (ECM)') },
  { id: 'TCU', label: () => L('Şanzıman (TCU)', 'Transmission (TCU)') },
  { id: 'ABS', label: () => L('Fren (ABS)', 'Brakes (ABS)') },
  { id: 'BCM', label: () => L('Gövde (BCM)', 'Body (BCM)') },
];

const STEPS: Array<{ key: string; label: () => string }> = [
  { key: 'SAFETY_VALIDATION', label: () => L('Güvenlik kontrolleri', 'Safety checks') },
  { key: 'EXTENDED_SESSION', label: () => L('Genişletilmiş oturum (0x10 03)', 'Extended session (0x10 03)') },
  { key: 'SECURITY_ACCESS', label: () => L('Güvenlik erişimi (0x27)', 'Security access (0x27)') },
  { key: 'PROGRAMMING_SESSION', label: () => L('Programlama oturumu (0x10 02)', 'Programming session (0x10 02)') },
  { key: 'REQUEST_DOWNLOAD', label: () => L('İndirme talebi (0x34)', 'Request download (0x34)') },
  { key: 'TRANSFER_DATA', label: () => L('Veri aktarımı (0x36)', 'Transfer data (0x36)') },
  { key: 'TRANSFER_EXIT', label: () => L('Aktarım sonu (0x37)', 'Transfer exit (0x37)') },
  { key: 'CHECKSUM_VERIFICATION', label: () => L('Bütünlük doğrulama', 'Integrity check') },
  { key: 'ECU_RESET', label: () => L('ECU yeniden başlatma (0x11)', 'ECU reset (0x11)') },
  { key: 'COMPLETED', label: () => L('Tamamlandı', 'Completed') },
];

interface Progress {
  status?: string;
  percent?: number;
  step?: string;
  bytes_transferred?: number;
  total_bytes?: number;
  speed_kbps?: number;
  elapsed_s?: number;
  logs?: string[];
  error?: string | null;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes as Uint8Array<ArrayBuffer>);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

function toHex(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    out += Array.from(bytes.subarray(i, i + 0x8000), (b) => b.toString(16).padStart(2, '0')).join('');
  }
  return out;
}

const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <label className="flex flex-col gap-1 text-[13px]">
    <span className="font-semibold text-text-hi">{label}</span>
    {children}
    {hint && <span className="text-[12px] text-text-mid">{hint}</span>}
  </label>
);

const INPUT = 'rounded-lg border border-border-strong bg-transparent px-3 py-2 font-mono text-[13px] text-text-hi outline-none focus:border-accent';

const Gate: React.FC<{ ok: boolean | null; text: string; testId?: string }> = ({ ok, text, testId }) => (
  <li className="flex items-start gap-2 text-[13px]" data-testid={testId} data-ok={ok === null ? 'na' : String(ok)}>
    {ok === null ? (
      <CircleSlash className="mt-0.5 h-4 w-4 flex-none text-text-low" />
    ) : ok ? (
      <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none text-ok" />
    ) : (
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-none text-del" />
    )}
    <span className={ok === false ? 'text-del' : 'text-text-body'}>{text}</span>
  </li>
);

export const EcuView: React.FC = () => {
  const [ecu, setEcu] = useState<EcuId>('ECM');
  const [file, setFile] = useState<{ name: string; bytes: Uint8Array; sha: string } | null>(null);
  const [address, setAddress] = useState('0x80000');
  const [blockSize, setBlockSize] = useState<number>(256);
  const [signature, setSignature] = useState('');
  const [pubkey, setPubkey] = useState('');
  const [vin, setVin] = useState('');
  const [ack, setAck] = useState(false);
  const [pre, setPre] = useState<FlashPreconditions | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const poll = useRef<number | null>(null);

  const loadPre = useCallback(async () => {
    setPre(await DesktopBridge.flashPreconditions());
  }, []);

  useEffect(() => {
    void loadPre();
    const t = window.setInterval(() => void loadPre(), 1000);
    return () => window.clearInterval(t);
  }, [loadPre]);

  useEffect(
    () => () => {
      if (poll.current) window.clearInterval(poll.current);
    },
    [],
  );

  const onFile = async (f: File | undefined) => {
    setMessage(null);
    if (!f) return setFile(null);
    const bytes = new Uint8Array(await f.arrayBuffer());
    setFile({ name: f.name, bytes, sha: await sha256Hex(bytes) });
  };

  const memoryAddress = /^0x[0-9a-f]+$/i.test(address.trim()) ? parseInt(address.trim(), 16) : Number.NaN;
  const built = file
    ? buildFlashRequest({
        ecu,
        fileName: file.name,
        sizeBytes: file.bytes.length,
        data: '',
        memoryAddress,
        blockSize,
        firmwareSignature: signature,
        trustedPubkey: pubkey,
        expectedVin: vin,
      })
    : null;
  const requestError = !file ? L('Yazılım dosyası seçin.', 'Choose a firmware file.') : built && !built.ok ? built.error : null;
  const running = progress?.status === 'in_progress';
  const gatesOk = !!pre && !pre.estop && pre.speed_state === 'ok' && !running;
  const canStart = !!built && built.ok && gatesOk && ack && !busy;

  const startPolling = () => {
    if (poll.current) window.clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      const p = (await DesktopBridge.flashProgress()) as Progress;
      setProgress(p);
      if (p.status && p.status !== 'in_progress' && poll.current) {
        window.clearInterval(poll.current);
        poll.current = null;
      }
    }, 250);
  };

  const start = async () => {
    if (!file || !built || !built.ok) return;
    setBusy(true);
    setMessage(null);
    try {
      // The challenge carries exactly what flash_start will receive, minus the
      // image bytes (bound by their SHA-256; the server re-hashes them).
      const request = { ...built.request, id: `flash-${ecu}-${Date.now()}`, dataSha256: file.sha };
      const { data: _omit, ...challengePayload } = request;
      void _omit;
      const challenge = await DesktopBridge.requestDiagnosticChallenge(challengePayload);
      if (!challenge.success || !challenge.token) {
        setMessage({ ok: false, text: challenge.error || L('Onay alınamadı.', 'Confirmation was not given.') });
        return;
      }
      const res = await DesktopBridge.flashStart({ ...request, data: toHex(file.bytes) }, challenge.token);
      if (!res.success) {
        setMessage({ ok: false, text: res.error || res.message || L('Başlatılamadı.', 'Could not start.') });
        return;
      }
      setAck(false);
      startPolling();
    } finally {
      setBusy(false);
      void loadPre();
    }
  };

  const cancel = async () => {
    await DesktopBridge.flashCancel();
  };

  const stepIndex = progress?.step ? STEPS.findIndex((s) => s.key === progress.step) : -1;

  return (
    <div className="grid h-full min-h-0 grid-cols-1 content-start gap-3 overflow-auto xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]" data-testid="ecu-view">
      <div className="flex min-w-0 flex-col gap-3">
        {pre?.simulated && (
          <div role="status" data-testid="ecu-simulated" className="rounded-2xl border border-warn-border bg-warn-soft px-5 py-3 text-[13px] text-warn">
            <b>{L('Prova modu.', 'Dry run.')}</b>{' '}
            {L('Simüle hatta işlem adım adım gösterilir; araca hiçbir çerçeve gönderilmez.', 'On the simulated bus the steps are shown; nothing is sent to a vehicle.')}
          </div>
        )}

        <Card>
          <CardHeader title={L('1. Hedef', '1. Target')} hint={L('Hangi kontrol ünitesine yazılım yüklenecek?', 'Which control unit gets the firmware?')} />
          <div className="grid grid-cols-2 gap-2 p-5 md:grid-cols-4" role="radiogroup">
            {ECUS.map((e) => (
              <button
                key={e.id}
                type="button"
                role="radio"
                aria-checked={ecu === e.id}
                data-testid={`ecu-${e.id}`}
                onClick={() => setEcu(e.id)}
                disabled={running}
                className={cx('rounded-xl border p-3 text-left transition-colors', ecu === e.id ? 'border-accent bg-accent-soft' : 'border-border-strong hover:border-accent')}
              >
                <span className="block text-[13.5px] font-semibold text-text-hi">{e.label()}</span>
                <span className="block font-mono text-[11.5px] text-text-mid">
                  0x{FLASH_ECU_BOUNDS[e.id].memoryBase.toString(16).toUpperCase()} · {FLASH_ECU_BOUNDS[e.id].memorySizeBytes / 1024 / 1024} MB
                </span>
              </button>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-3 border-t border-border-whisper p-5 md:grid-cols-2">
            <Field label={L('Araç VIN', 'Vehicle VIN')} hint={L('ECU başka bir araca aitse işlem reddedilir.', 'Refused if the ECU belongs to another vehicle.')}>
              <input data-testid="ecu-vin" className={INPUT} value={vin} maxLength={17} onChange={(e) => setVin(e.target.value.toUpperCase())} disabled={running} />
            </Field>
          </div>
        </Card>

        <Card>
          <CardHeader
            title={L('2. Yazılım', '2. Firmware')}
            hint={L('İmza ve ortak anahtar, yazılımı yayınlayan üreticiden gelir. İmzası doğrulanmayan dosya yüklenmez.', 'Signature and public key come from the publisher. Files whose signature does not verify are not flashed.')}
          />
          <div className="flex flex-col gap-3 p-5">
            <label className={cx(BTN_GHOST, 'cursor-pointer self-start')}>
              <FileUp className="h-4 w-4" />
              {file ? L('Başka dosya seç', 'Choose another file') : L('Dosya seç', 'Choose file')}
              <input data-testid="ecu-file" type="file" className="hidden" onChange={(e) => void onFile(e.target.files?.[0])} disabled={running} />
            </label>
            {file && (
              <div className="rounded-xl border border-border-whisper px-4 py-3 text-[12.5px]" data-testid="ecu-file-info">
                <div className="font-semibold text-text-hi">{file.name}</div>
                <div className="text-text-mid">
                  {file.bytes.length.toLocaleString('tr-TR')} {L('bayt', 'bytes')}
                </div>
                <div className="break-all font-mono text-text-mid">SHA-256 {file.sha}</div>
              </div>
            )}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label={L('Bellek adresi', 'Memory address')}>
                <input data-testid="ecu-address" className={INPUT} value={address} onChange={(e) => setAddress(e.target.value)} disabled={running} />
              </Field>
              <Field label={L('Blok boyutu', 'Block size')}>
                <select data-testid="ecu-block" className={INPUT} value={blockSize} onChange={(e) => setBlockSize(Number(e.target.value))} disabled={running}>
                  {FLASH_ALLOWED_BLOCK_SIZES.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label={L('İmza (hex)', 'Signature (hex)')}>
              <textarea data-testid="ecu-signature" className={cx(INPUT, 'h-16')} value={signature} onChange={(e) => setSignature(e.target.value)} disabled={running} />
            </Field>
            <Field label={L('Güvenilir ortak anahtar (hex)', 'Trusted public key (hex)')}>
              <textarea data-testid="ecu-pubkey" className={cx(INPUT, 'h-16')} value={pubkey} onChange={(e) => setPubkey(e.target.value)} disabled={running} />
            </Field>
          </div>
        </Card>
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <Card testId="ecu-gates">
          <CardHeader title={L('3. Ön koşullar', '3. Preconditions')} hint={L('Hepsi sağlanmadan başlat düğmesi açılmaz; son kararı yine Python verir.', 'Start stays disabled until all hold; Python still has the final say.')} />
          <ul className="flex flex-col gap-2 p-5">
            <Gate testId="gate-estop" ok={pre ? !pre.estop : null} text={L('Acil durdurma devrede değil', 'E-Stop not engaged')} />
            <Gate
              testId="gate-speed"
              ok={pre ? pre.speed_state === 'ok' : null}
              text={
                pre?.speed_state === 'moving'
                  ? L(`Araç hareket ediyor (${pre.speed_kmh?.toFixed(1)} km/s)`, `Vehicle moving (${pre.speed_kmh?.toFixed(1)} km/h)`)
                  : pre?.speed_state === 'stale'
                    ? L('Araç hızı bilinmiyor (hız verisi yok)', 'Vehicle speed unknown (no speed data)')
                    : L('Araç duruyor', 'Vehicle stationary')
              }
            />
            <Gate ok={!running} text={L('Devam eden başka işlem yok', 'No other operation running')} />
            <Gate ok={requestError ? false : true} text={requestError ?? L('Dosya, adres ve kimlik bilgileri geçerli', 'File, address and identity are valid')} />
            <Gate ok={null} text={L('Akü / besleme sabit ve kontak açık olmalı (uygulama bunu ölçmez)', 'Battery / supply stable, ignition on (the app does not measure this)')} />
            {pre?.native_confirmation_required && (
              <Gate ok={null} text={L('Başlatınca Windows bir onay penceresi açar.', 'Starting opens a Windows confirmation dialog.')} />
            )}
          </ul>
        </Card>

        <Card>
          <CardHeader title={L('4. Onay', '4. Confirm')} />
          <div className="flex flex-col gap-3 p-5">
            <label className="flex items-start gap-2 text-[13px] text-text-body">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} data-testid="ecu-ack" className="mt-0.5" disabled={running} />
              {L(
                'Yanlış ya da yarım kalan bir yüklemenin ECU’yu kullanılamaz hale getirebileceğini anlıyorum.',
                'I understand that a wrong or interrupted update can leave the ECU unusable.',
              )}
            </label>
            <button type="button" className={cx(BTN_PRIMARY, 'bg-del')} disabled={!canStart} onClick={() => void start()} data-testid="ecu-start">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Cpu className="h-4 w-4" />}
              {pre?.simulated ? L('Provayı başlat', 'Start dry run') : L('Programlamayı başlat', 'Start programming')}
            </button>
            {message && (
              <p role="alert" data-testid="ecu-message" className={cx('text-[13px]', message.ok ? 'text-ok' : 'text-del')}>
                {message.text}
              </p>
            )}
          </div>
        </Card>

        {progress && (
          <Card testId="ecu-progress">
            <CardHeader title={L('İlerleme', 'Progress')}>
              {running && (
                <button type="button" className={BTN_GHOST} onClick={() => void cancel()} data-testid="ecu-cancel">
                  <Square className="h-4 w-4" />
                  {L('İptal et', 'Cancel')}
                </button>
              )}
              {progress.status === 'completed' && <Chip tone="ok" testId="ecu-done">{L('Tamamlandı', 'Completed')}</Chip>}
              {(progress.status === 'failed' || progress.status === 'cancelled') && (
                <Chip tone="danger" testId="ecu-stopped">{progress.status === 'cancelled' ? L('İptal edildi', 'Cancelled') : L('Başarısız', 'Failed')}</Chip>
              )}
            </CardHeader>
            <div className="flex flex-col gap-3 p-5">
              <div className="h-2 rounded-full bg-border-whisper">
                <div className="h-2 rounded-full bg-accent transition-all" style={{ width: `${Math.round(progress.percent ?? 0)}%` }} />
              </div>
              <div className="text-[12.5px] tabular-nums text-text-mid">
                %{Math.round(progress.percent ?? 0)} · {(progress.bytes_transferred ?? 0).toLocaleString('tr-TR')} / {(progress.total_bytes ?? 0).toLocaleString('tr-TR')} {L('bayt', 'bytes')} ·{' '}
                {progress.speed_kbps ?? 0} kB/s · {progress.elapsed_s ?? 0} s
              </div>
              <ol className="flex flex-col gap-1 text-[12.5px]">
                {STEPS.map((s, i) => (
                  <li key={s.key} className={cx(i < stepIndex || progress.status === 'completed' ? 'text-ok' : i === stepIndex ? 'font-semibold text-text-hi' : 'text-text-low')}>
                    {i + 1}. {s.label()}
                  </li>
                ))}
              </ol>
              {progress.error && <p className="text-[13px] text-del">{progress.error}</p>}
              <details className="text-[12px] text-text-mid">
                <summary className="cursor-pointer">{L('Ayrıntılı kayıt', 'Detailed log')}</summary>
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono">{(progress.logs ?? []).join('\n')}</pre>
              </details>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
};

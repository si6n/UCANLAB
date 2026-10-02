import React, { useEffect, useState } from 'react';
import { Copy, LockOpen, OctagonX } from 'lucide-react';
import { DesktopBridge } from '../../services/bridge';
import { L } from '../mechanic/text';
import { BTN_GHOST, BTN_PRIMARY, cx } from '../workbench/ui';
import { Dialog } from './Dialog';

/**
 * Emergency stop, the same in both modes: the button that latches it, the
 * banner shown while transmission is locked, and the reset dialog.
 *
 * The reset is the documented out-of-band ceremony (docs/runbook/
 * estop-reset.md): this window only asks Python for a challenge and submits
 * the token an authorised person minted with scripts/estop_reset_tool.py.
 * Nothing here can clear the latch on its own.
 */

export const EstopButton: React.FC<{ native: boolean; onTriggered?: () => void }> = ({ native, onTriggered }) => {
  const [busy, setBusy] = useState(false);
  const press = async () => {
    setBusy(true);
    try {
      await DesktopBridge.triggerEstop();
    } finally {
      setBusy(false);
      onTriggered?.();
    }
  };
  return (
    <button
      type="button"
      data-testid="estop"
      onClick={() => void press()}
      disabled={!native || busy}
      title={L('Araca giden tüm gönderimi anında keser.', 'Immediately cuts every transmission to the vehicle.')}
      className="inline-flex flex-none items-center gap-1.5 rounded-lg bg-estop px-3 py-1.5 text-[13px] font-semibold text-white transition-colors hover:bg-estop-hover disabled:cursor-not-allowed disabled:opacity-40"
    >
      <OctagonX className="h-4 w-4" />
      {L('Acil durdur', 'E-Stop')}
    </button>
  );
};

interface Challenge {
  epoch: number;
  nonce: string;
  ts: number;
  maxAgeMs: number;
  receivedAt: number;
}

const NOT_ENGAGED = 'not currently engaged';

export const EstopResetDialog: React.FC<{ onClose: () => void; onReset: () => void }> = ({ onClose, onReset }) => {
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!challenge || done) return;
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [challenge, done]);

  const request = async () => {
    setBusy(true);
    setError('');
    setCopied(false);
    try {
      const res = await DesktopBridge.estopRequestChallenge();
      if (!res.success || res.epoch === undefined || !res.nonce || res.timestampMonotonicNs === undefined) {
        setChallenge(null);
        setError(
          (res.error ?? '').includes(NOT_ENGAGED)
            ? L(
                'Acil durdurma devrede değil. Kilidin nedeni başka bir güvenlik hatası olabilir; Ayarlar → Güvenlik’e bakın.',
                'The E-Stop is not engaged. The lock may come from another safety fault; see Settings → Safety.',
              )
            : `${L('İstek oluşturulamadı', 'Could not create the request')}: ${res.error ?? '—'}`,
        );
        return;
      }
      setChallenge({ epoch: res.epoch, nonce: res.nonce, ts: res.timestampMonotonicNs, maxAgeMs: res.maxAgeMs ?? 30_000, receivedAt: Date.now() });
      setNow(Date.now());
      setToken('');
    } catch (e) {
      setError(`${L('İstek oluşturulamadı', 'Could not create the request')}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    // The tool prints "TOKEN:<token>"; accept the line as pasted.
    const value = token.trim().replace(/^TOKEN:/i, '').trim();
    if (!value) return;
    setBusy(true);
    setError('');
    try {
      const res = await DesktopBridge.estopSubmitResetToken(value);
      if (res.success) {
        setDone(true);
        onReset();
      } else {
        setError(
          `${L(
            'Jeton kabul edilmedi: süresi dolmuş, yanlış ya da daha önce kullanılmış olabilir. Yeni bir istek oluşturun.',
            'The token was not accepted: it may have expired, be wrong or already used. Create a new request.',
          )}${res.error ? ` (${res.error})` : ''}`,
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const command = challenge
    ? `python scripts/estop_reset_tool.py --epoch ${challenge.epoch} --nonce ${challenge.nonce} --timestamp-ns ${challenge.ts}`
    : '';
  const left = challenge ? Math.max(0, Math.ceil((challenge.maxAgeMs - (now - challenge.receivedAt)) / 1000)) : 0;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setCopied(false); // the command stays selectable for a manual copy
    }
  };

  return (
    <Dialog
      title={L('Acil durdurma kilidini aç', 'Release the E-Stop latch')}
      onClose={onClose}
      testId="estop-reset-dialog"
      footer={
        done ? (
          <button type="button" className={BTN_PRIMARY} onClick={onClose}>
            {L('Kapat', 'Close')}
          </button>
        ) : (
          <>
            <button type="button" className={BTN_GHOST} onClick={onClose}>
              {L('Vazgeç', 'Cancel')}
            </button>
            {challenge && (
              <button type="button" className={BTN_PRIMARY} disabled={busy || !token.trim()} onClick={() => void submit()} data-testid="estop-reset-submit">
                <LockOpen className="h-4 w-4" />
                {L('Kilidi aç', 'Release')}
              </button>
            )}
          </>
        )
      }
    >
      {done ? (
        <p role="status" className="text-[13.5px] text-ok" data-testid="estop-reset-done">
          {L(
            'Kilit açıldı. Gönderim yine kapalı: araca yazmak isteyen her işlem ayrıca onay ister.',
            'The latch is released. Transmission stays off: every operation that writes to the vehicle asks for its own approval.',
          )}
        </p>
      ) : (
        <div className="flex flex-col gap-4 text-[13px]">
          <p className="text-text-body">
            {L(
              'Kilidi yalnızca yetkili kişinin ürettiği tek kullanımlık jeton açar. İstek kısa süre geçerlidir; yetkili kişi hazır olduğunda oluşturun.',
              'Only a single-use token made by an authorised person releases the latch. The request is short-lived; create it when that person is ready.',
            )}
          </p>
          <ol className="flex flex-col gap-3">
            <li className="flex flex-col gap-2">
              <span className="font-semibold text-text-hi">1. {L('İstek oluşturun', 'Create a request')}</span>
              <div>
                <button type="button" className={BTN_GHOST} disabled={busy} onClick={() => void request()} data-testid="estop-reset-request">
                  {challenge ? L('Yeni istek oluştur', 'Create a new request') : L('İstek oluştur', 'Create request')}
                </button>
              </div>
            </li>
            {challenge && (
              <>
                <li className="flex flex-col gap-2">
                  <span className="font-semibold text-text-hi">
                    2. {L('Yetkili kişi bu komutu çalıştırır', 'The authorised person runs this command')}
                  </span>
                  <div className="flex items-start gap-2">
                    <code className="min-w-0 flex-1 select-all break-all rounded-lg border border-border-whisper bg-surface-inset-raw px-3 py-2 font-mono text-[12px] text-text-hi" data-testid="estop-reset-command">
                      {command}
                    </code>
                    <button type="button" className={cx(BTN_GHOST, 'px-2.5')} onClick={() => void copy()} aria-label={L('Komutu kopyala', 'Copy the command')}>
                      <Copy className="h-4 w-4" />
                    </button>
                  </div>
                  <span className={cx('text-[12px]', left > 0 ? 'text-text-mid' : 'text-warn')} role="status">
                    {copied ? `${L('Kopyalandı', 'Copied')} · ` : ''}
                    {left > 0
                      ? L(`İstek ${left} sn daha geçerli.`, `The request is valid for ${left} s more.`)
                      : L('İsteğin süresi doldu; yeni istek oluşturun.', 'The request has expired; create a new one.')}
                  </span>
                </li>
                <li className="flex flex-col gap-2">
                  <label htmlFor="estop-token" className="font-semibold text-text-hi">
                    3. {L('Çıkan jetonu yapıştırın', 'Paste the token it prints')}
                  </label>
                  <input
                    id="estop-token"
                    data-testid="estop-reset-token"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="TOKEN:…"
                    className="rounded-lg border border-border-strong bg-transparent px-3 py-2 font-mono text-[12.5px] text-text-hi outline-none focus:border-accent"
                  />
                </li>
              </>
            )}
          </ol>
          {error && (
            <p role="alert" className="text-del">
              {error}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
};

/** Shown while transmission is locked; the frame owns the reset dialog so it outlives the banner. */
export const EstopBanner: React.FC<{ detail: string; onOpenReset: () => void }> = ({ detail, onOpenReset }) => (
  <div role="alert" data-testid="estop-banner" className="flex flex-none flex-wrap items-center gap-x-4 gap-y-2 border-b border-danger-border bg-danger-soft px-5 py-2.5 text-[13px] text-del">
    <span className="min-w-0 flex-1">
      <b>{L('Gönderim kilitli.', 'Transmission locked.')}</b> {detail}
    </span>
    <button
      type="button"
      onClick={onOpenReset}
      data-testid="estop-reset-open"
      className="inline-flex items-center gap-1.5 rounded-lg border border-del px-3 py-1 text-[12.5px] font-semibold text-del transition-colors hover:bg-danger-soft"
    >
      <LockOpen className="h-3.5 w-3.5" />
      {L('Kilidi aç…', 'Release…')}
    </button>
  </div>
);

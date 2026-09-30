import React, { useCallback, useEffect, useRef, useState } from 'react';
import { KeyRound, Laptop, Loader2, ShieldCheck, WifiOff } from 'lucide-react';
import { AuthLicenseState, AuthLoginOutcome, DesktopBridge, DeviceLoginStart } from '../../services/bridge';

/**
 * Mechanic-flow start gate (Aşama 3, docs/product/MECHANIC_FLOW.md §3.2-3.3).
 *
 * The launcher already signs the user in before the core app starts. This gate
 * covers the cases that reach the running app anyway: the offline window ended
 * while the app was open, or the app was started directly (developer run).
 * Password entry never happens here: sign-in is in the system browser, or with
 * a short code approved on ucanlab.org/cihaz.
 */

const BTN_PRIMARY =
  'inline-flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-bg-app transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';
const BTN_SECONDARY =
  'inline-flex w-full items-center justify-center gap-2 rounded-lg border border-border-strong px-4 py-3 text-sm font-semibold text-text-hi transition-colors hover:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';

type Phase = 'checking' | 'ready' | 'login' | 'browser' | 'code' | 'finishing';

const lang = (): 'tr' | 'en' => {
  try {
    const saved = localStorage.getItem('ucanlab.lang');
    if (saved === 'en' || saved === 'tr') return saved;
  } catch {
    /* storage unavailable: default below */
  }
  return 'tr';
};

const L = (tr: string, en: string): string => (lang() === 'en' ? en : tr);

function messageOf(state: { message_tr?: string; message_en?: string } | null | undefined): string {
  if (!state) return '';
  return (lang() === 'en' ? state.message_en : state.message_tr) || '';
}

export const SignInGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [phase, setPhase] = useState<Phase>('checking');
  const [license, setLicense] = useState<AuthLicenseState | null>(null);
  const [notice, setNotice] = useState<string>('');
  const [device, setDevice] = useState<DeviceLoginStart | null>(null);
  const pollTimer = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const applyOutcome = useCallback((outcome: AuthLoginOutcome | null | undefined) => {
    if (outcome && outcome.status === 'ready') {
      setLicense(outcome.license);
      setNotice('');
      setPhase('ready');
      return;
    }
    setNotice(messageOf(outcome) || L('Giriş tamamlanamadı. Tekrar deneyin.', 'Sign-in could not be completed. Please try again.'));
    setPhase('login');
  }, []);

  // Start: roll the offline window forward if online, otherwise read the stored ticket.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const state = await DesktopBridge.authGetState(true);
        if (cancelled) return;
        if (state === null || !state.loginRequired) {
          setLicense(state?.license ?? null);
          setPhase('ready');
          return;
        }
        setLicense(state.license);
        setNotice(messageOf(state.license));
        setPhase('login');
      } catch {
        if (!cancelled) setPhase('ready'); // capability missing: the app's own guards apply
      }
    })();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [stopPolling]);

  const startBrowser = useCallback(async () => {
    setNotice('');
    const res = await DesktopBridge.cloudStartWebLogin();
    if (!res.success) {
      setNotice(L('Tarayıcı açılamadı. Kodla giriş yapabilirsiniz.', "Couldn't open the browser. You can sign in with a code."));
      return;
    }
    setPhase('browser');
    const tick = async () => {
      const st = await DesktopBridge.cloudCheckWebLoginStatus();
      if (st.status === 'pending') {
        pollTimer.current = window.setTimeout(tick, 1500);
        return;
      }
      if (st.status === 'completed') {
        applyOutcome(st.login ?? null);
        return;
      }
      if (st.status === 'error') {
        setNotice(L('Giriş tamamlanamadı. Tekrar deneyin.', 'Sign-in could not be completed. Please try again.'));
      }
      setPhase('login');
    };
    pollTimer.current = window.setTimeout(tick, 1500);
  }, [applyOutcome]);

  const startCode = useCallback(async () => {
    stopPolling();
    await DesktopBridge.cloudCancelWebLogin().catch(() => undefined);
    setNotice('');
    const res = await DesktopBridge.authStartDeviceLogin();
    if (!res.success) {
      setNotice(messageOf(res) || L('Kod alınamadı.', 'Could not get a code.'));
      setPhase('login');
      return;
    }
    setDevice(res);
    setPhase('code');
    const tick = async () => {
      const st = await DesktopBridge.authPollDeviceLogin();
      if (st.status === 'pending') {
        pollTimer.current = window.setTimeout(tick, Math.max(2, st.interval ?? res.interval ?? 5) * 1000);
        return;
      }
      if (st.status === 'completed') {
        setPhase('finishing');
        applyOutcome(st.login ?? null);
        return;
      }
      setNotice(messageOf(st) || L('Giriş tamamlanamadı.', 'Sign-in could not be completed.'));
      setPhase('login');
    };
    pollTimer.current = window.setTimeout(tick, Math.max(2, res.interval ?? 5) * 1000);
  }, [applyOutcome, stopPolling]);

  const cancel = useCallback(async () => {
    stopPolling();
    await DesktopBridge.cloudCancelWebLogin().catch(() => undefined);
    await DesktopBridge.authCancelDeviceLogin().catch(() => undefined);
    setPhase('login');
  }, [stopPolling]);

  if (phase === 'ready') return <>{children}</>;

  const offlineHint =
    license && license.status === 'expired' ? (
      <div className="flex items-start gap-2 rounded-lg border border-border-strong bg-bg-card p-3 text-sm text-text-body">
        <WifiOff className="mt-0.5 h-4 w-4 flex-none text-warn" />
        <span>{notice}</span>
      </div>
    ) : notice ? (
      <p className="text-sm text-del" role="alert">
        {notice}
      </p>
    ) : null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg-app px-4 py-10 text-text-body">
      <div className="flex w-full max-w-md flex-col gap-5 rounded-2xl border border-border-whisper bg-bg-card p-6 shadow-sm">
        <div className="flex items-center gap-2 text-sm font-semibold text-text-hi">
          <ShieldCheck className="h-5 w-5 text-accent" />
          UCanLab
        </div>

        {phase === 'checking' && (
          <div className="flex items-center gap-3 text-text-mid">
            <Loader2 className="h-5 w-5 animate-spin" />
            {L('UCanLab hazırlanıyor…', 'Getting UCanLab ready…')}
          </div>
        )}

        {phase === 'login' && (
          <>
            <h1 className="text-xl font-semibold text-text-hi">{L('Hesabınıza giriş yapın', 'Sign in to your account')}</h1>
            <p className="text-sm">
              {L(
                'Tarayıcınızda ucanlab.org açılacak. Giriş yaptıktan sonra buraya kendiliğinden döneceksiniz.',
                "ucanlab.org will open in your browser. After you sign in, you'll come back here automatically.",
              )}
            </p>
            {offlineHint}
            <button type="button" className={BTN_PRIMARY} onClick={() => void startBrowser()}>
              <Laptop className="h-4 w-4" />
              {L('Tarayıcıda giriş yap', 'Sign in with browser')}
            </button>
            <button type="button" className={BTN_SECONDARY} onClick={() => void startCode()}>
              <KeyRound className="h-4 w-4" />
              {L('Kodla giriş yap', 'Sign in with a code')}
            </button>
            <p className="text-xs text-text-low">
              {L(
                'Parolanız bu uygulamaya yazılmaz. Uygulama yalnızca tek kullanımlık, birkaç dakikalık bir kod alır.',
                'Your password is never typed into this app. It only receives a one-time code that expires in minutes.',
              )}
            </p>
          </>
        )}

        {phase === 'browser' && (
          <>
            <h1 className="text-xl font-semibold text-text-hi">{L('Tarayıcıda girişinizi bekliyoruz', 'Waiting for you to sign in')}</h1>
            <div className="flex items-center gap-3 text-sm text-text-mid">
              <Loader2 className="h-4 w-4 animate-spin" />
              {L('ucanlab.org sekmesinde giriş yapın…', 'Sign in on the ucanlab.org tab…')}
            </div>
            <button type="button" className={BTN_SECONDARY} onClick={() => void startCode()}>
              {L('Dönmedi mi? Kodla giriş yap', 'Not returning? Sign in with a code')}
            </button>
            <button type="button" className="text-sm text-accent-text" onClick={() => void cancel()}>
              {L('İptal', 'Cancel')}
            </button>
          </>
        )}

        {(phase === 'code' || phase === 'finishing') && device && (
          <>
            <h1 className="text-xl font-semibold text-text-hi">{L('Kodla giriş', 'Sign in with a code')}</h1>
            <p className="text-sm">
              {L('Telefonunuzdan veya bilgisayarınızdan şu adrese gidin:', 'On your phone or computer, go to:')}{' '}
              <span className="font-semibold text-text-hi">{(device.verification_uri || '').replace(/^https?:\/\//, '')}</span>
            </p>
            <div className="select-all rounded-xl bg-surface-inset-raw py-5 text-center font-mono text-3xl font-semibold tracking-[0.15em] text-text-hi">
              {device.user_code}
            </div>
            <p className="text-xs text-text-low">
              {L(
                `Kod ${Math.round((device.expires_in ?? 600) / 60)} dakika geçerlidir ve bir kez kullanılır.`,
                `The code is valid for ${Math.round((device.expires_in ?? 600) / 60)} minutes and works once.`,
              )}
            </p>
            <div className="flex items-center gap-3 text-sm text-text-mid">
              <Loader2 className="h-4 w-4 animate-spin" />
              {phase === 'finishing'
                ? L('Lisans etkinleştiriliyor…', 'Activating your license…')
                : L('Onayınızı bekliyoruz…', 'Waiting for your approval…')}
            </div>
            <button type="button" className="text-sm text-accent-text" onClick={() => void cancel()}>
              {L('İptal', 'Cancel')}
            </button>
          </>
        )}
      </div>
    </div>
  );
};

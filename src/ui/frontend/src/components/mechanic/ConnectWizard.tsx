import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, CheckCircle2, Ear, Loader2, Plug, RefreshCw, Usb } from 'lucide-react';
import {
  AdapterEntry,
  ConnectionTestResult,
  ConnectionTestStatus,
  DesktopBridge,
  VehicleIdentityResult,
  VehicleProfileInfo,
  VehicleTypeInfo,
} from '../../services/bridge';
import { IdentityNotice } from './IdentityNotice';
import { BTN_PRIMARY, BTN_SECONDARY, L, pick } from './text';

/**
 * Connection wizard, Aşama 5 (MECHANIC_FLOW.md §3.7-3.10, §4).
 *
 * Wait for the adapter → plug guide → listen-only test → Ready / plain-language
 * problem. The test only listens: nothing is sent to the vehicle (G1).
 */

type Step = 'adapter' | 'plug' | 'test' | 'result';

const SIMULATOR_ID = 'simulator:sim0';

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="flex min-h-screen items-center justify-center bg-bg-app px-4 py-10 text-text-body">
    <div className="flex w-full max-w-md flex-col gap-5 rounded-2xl border border-border-whisper bg-bg-card p-6 shadow-sm">{children}</div>
  </div>
);

const Notice: React.FC<{ tone: 'warn' | 'del'; children: React.ReactNode }> = ({ tone, children }) => (
  <div className={`flex items-start gap-2 rounded-lg border ${tone === 'del' ? 'border-del' : 'border-warn'} bg-bg-card p-3 text-sm`} role="alert">
    <AlertTriangle className={`mt-0.5 h-4 w-4 flex-none ${tone === 'del' ? 'text-del' : 'text-warn'}`} />
    <span>{children}</span>
  </div>
);

const TEST_STEPS: { id: 'bitrate' | 'traffic' | 'ecus'; tr: string; en: string }[] = [
  { id: 'bitrate', tr: 'Hız bulunuyor', en: 'Finding the speed' },
  { id: 'traffic', tr: 'Trafik kontrol ediliyor', en: 'Checking traffic' },
  { id: 'ecus', tr: 'Beyinler (ECU) aranıyor', en: 'Looking for control units (ECUs)' },
];

export const ConnectWizard: React.FC<{
  vehicle: VehicleProfileInfo;
  vtype: VehicleTypeInfo;
  profiles: VehicleProfileInfo[];
  onChangeVehicle: () => void;
  onSwitchVehicle: (profileId: string) => void;
  onDone: () => void;
}> = ({ vehicle, vtype, profiles, onChangeVehicle, onSwitchVehicle, onDone }) => {
  const [step, setStep] = useState<Step>('adapter');
  const [adapters, setAdapters] = useState<AdapterEntry[] | null>(null);
  const [adapter, setAdapter] = useState<AdapterEntry | null>(null);
  const [status, setStatus] = useState<ConnectionTestStatus | null>(null);
  const [result, setResult] = useState<ConnectionTestResult | null>(null);
  const [identity, setIdentity] = useState<VehicleIdentityResult | null>(null);
  const [error, setError] = useState('');
  const timer = useRef<number | null>(null);

  const clearTimer = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  };
  useEffect(() => clearTimer, []);

  // WaitAdapter: rescan every 2 s until a real adapter shows up.
  const scan = useCallback(async () => {
    const found = await DesktopBridge.adapterScan().catch(() => [] as AdapterEntry[]);
    setAdapters(found);
    return found;
  }, []);

  useEffect(() => {
    if (step !== 'adapter') return;
    let stopped = false;
    const loop = async () => {
      const found = await scan();
      if (stopped) return;
      const real = found.filter((a) => a.kind !== 'simulator');
      if (real.some((a) => a.usable)) return; // found: the mechanic confirms below
      timer.current = window.setTimeout(loop, 2000);
    };
    void loop();
    return () => {
      stopped = true;
      clearTimer();
    };
  }, [step, scan]);

  const runTest = useCallback(async (chosen: AdapterEntry) => {
    clearTimer();
    setError('');
    setResult(null);
    setIdentity(null);
    setStatus({ success: true, state: 'running', step: 'opening' });
    setStep('test');
    const started = await DesktopBridge.connectionTestStart(chosen.id);
    if (!started.success) {
      setError(L('Bağlantı testi başlatılamadı. Adaptörü yeniden seçin.', 'Could not start the connection test. Pick the adapter again.'));
      setStep('adapter');
      return;
    }
    const poll = async () => {
      const st = await DesktopBridge.connectionTestStatus();
      setStatus(st);
      if (st.state === 'done' && st.result) {
        setResult(st.result);
        setStep('result');
        if (st.result.usable) {
          DesktopBridge.vehicleCheckIdentity().then(setIdentity).catch(() => setIdentity(null));
        }
        return;
      }
      timer.current = window.setTimeout(poll, 400);
    };
    timer.current = window.setTimeout(poll, 400);
  }, []);

  const choose = (a: AdapterEntry) => {
    setAdapter(a);
    if (a.kind === 'simulator') void runTest(a);
    else setStep('plug');
  };

  const simulator = adapters?.find((a) => a.id === SIMULATOR_ID) ?? null;

  if (step === 'adapter') {
    const real = (adapters ?? []).filter((a) => a.kind !== 'simulator');
    const usable = real.filter((a) => a.usable);
    const blocked = real.filter((a) => !a.usable);
    return (
      <Shell>
        <div className="flex items-center justify-between gap-2 text-sm">
          <span>
            <span className="text-text-low">{L('Araç: ', 'Vehicle: ')}</span>
            <span className="font-semibold text-text-hi">{pick(vehicle, 'label')}</span>
          </span>
          <button type="button" className="text-accent-text" onClick={onChangeVehicle}>
            {L('Değiştir', 'Change')}
          </button>
        </div>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi">
          <Usb className="h-5 w-5 text-accent" />
          {L('Adaptörü bilgisayara takın.', 'Plug the adapter into the computer.')}
        </h1>
        {usable.length === 0 && (
          <div className="flex items-center gap-3 text-sm text-text-mid">
            <Loader2 className="h-4 w-4 animate-spin" />
            {L('Takınca kendiliğinden bulacağız.', "We'll detect it automatically.")}
          </div>
        )}
        {usable.map((a) => (
          <button
            key={a.id}
            type="button"
            data-testid={`adapter-${a.kind}`}
            className="flex items-center justify-between gap-3 rounded-xl border border-ok p-4 text-left"
            onClick={() => choose(a)}
          >
            <span>
              <span className="block font-semibold text-text-hi">
                {L(`${a.label} bulundu`, `${a.label} found`)} <Check className="inline h-4 w-4 text-ok" />
              </span>
              {pick(a, 'message') && <span className="block text-xs text-text-mid">{pick(a, 'message')}</span>}
            </span>
            <span className="text-sm font-semibold text-accent-text">{L('Kullan', 'Use')}</span>
          </button>
        ))}
        {blocked.map((a) => (
          <Notice key={a.id} tone="warn">
            {pick(a, 'message') || L(`${a.label} kullanılamıyor.`, `${a.label} can't be used.`)}
          </Notice>
        ))}
        {error && <Notice tone="del">{error}</Notice>}
        {blocked.length > 0 && (
          <button type="button" className={BTN_SECONDARY} onClick={() => void scan()}>
            <RefreshCw className="h-4 w-4" />
            {L('Kurdum, tekrar dene', 'Installed it, try again')}
          </button>
        )}
        {simulator && (
          <button type="button" data-testid="adapter-simulator" className={BTN_SECONDARY} onClick={() => choose(simulator)}>
            {L('Simülatörle dene', 'Try with simulator')}
          </button>
        )}
      </Shell>
    );
  }

  if (step === 'plug' && adapter) {
    return (
      <Shell>
        {vehicle.high_voltage && (
          <Notice tone="del">
            {L(
              'Yüksek voltajlı araç: turuncu kablolara dokunmayın. Bu araçta yalnız okuma yapılır.',
              'High-voltage vehicle: do not touch orange cables. Only reading is done on this vehicle.',
            )}
          </Notice>
        )}
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi">
          <Plug className="h-5 w-5 text-accent" />
          {L('Adaptörü araca takın', 'Plug the adapter into the vehicle')}
        </h1>
        <p className="text-sm">{pick(vtype, 'plug')}</p>
        <p className="text-xs text-text-low">{pick(vtype, 'passive_note')}</p>
        <button type="button" data-testid="plug-done" className={BTN_PRIMARY} onClick={() => void runTest(adapter)}>
          <Check className="h-4 w-4" />
          {L('Taktım, kontak açık', 'Plugged in, ignition on')}
        </button>
        <button type="button" className="bg-transparent text-sm text-accent-text hover:underline" onClick={() => setStep('adapter')}>
          {L('Başka adaptör', 'Another adapter')}
        </button>
      </Shell>
    );
  }

  if (step === 'test') {
    const current = status?.step === 'ecus' ? 2 : status?.step === 'bitrate' ? 0 : -1;
    return (
      <Shell>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi">
          <Ear className="h-5 w-5 text-accent" />
          {L('Araçla bağlantı kontrol ediliyor', 'Checking the connection')}
        </h1>
        <p className="text-sm">
          {L('Araca hiçbir şey gönderilmiyor, sadece dinliyoruz.', 'Nothing is sent to the vehicle — we only listen.')}
        </p>
        <ol className="flex flex-col gap-2 text-sm">
          {TEST_STEPS.map((s, i) => (
            <li key={s.id} className="flex items-center gap-2">
              {i < current || (i === 1 && current === 2) ? (
                <CheckCircle2 className="h-4 w-4 text-ok" />
              ) : i === current || (i === 1 && current === 0) ? (
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
              ) : (
                <span className="h-4 w-4 rounded-full border border-border-strong" />
              )}
              <span>{L(s.tr, s.en)}</span>
              {s.id === 'bitrate' && status?.bitrate ? (
                <span className="text-xs text-text-low">({Math.round(status.bitrate / 1000)} kbit/s)</span>
              ) : null}
            </li>
          ))}
        </ol>
        <p className="text-xs text-text-low">
          {L('ECU: aracın bir bölümünü yöneten bilgisayar.', 'ECU: a computer that runs one part of the vehicle.')}
        </p>
        <button
          type="button"
          className="bg-transparent text-sm text-accent-text hover:underline"
          onClick={() => {
            void DesktopBridge.connectionTestCancel();
          }}
        >
          {L('Durdur', 'Stop')}
        </button>
      </Shell>
    );
  }

  if (step === 'result' && result) {
    const message = pick(result, 'message');
    if (result.usable) {
      return (
        <Shell>
          {vehicle.high_voltage && (
            <Notice tone="del">
              {L('Yüksek voltajlı araç: turuncu kablolara dokunmayın.', 'High-voltage vehicle: do not touch orange cables.')}
            </Notice>
          )}
          {result.battery_warning && <Notice tone="warn">{pick(result, 'battery_message')}</Notice>}
          <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi" data-testid="connect-ready">
            {result.code === 'READY' ? (
              <>
                <CheckCircle2 className="h-6 w-6 text-ok" />
                {L('Hazır', 'Ready')}
              </>
            ) : (
              <>
                <AlertTriangle className="h-6 w-6 text-warn" />
                {L('Bağlandı, ama dikkat', 'Connected — please check')}
              </>
            )}
          </h1>
          <p className="text-sm">
            {message}{' '}
            {result.ecu_count > 0 &&
              L(`${result.ecu_count} kontrol ünitesi görüldü.`, `${result.ecu_count} control units found.`)}
          </p>
          {result.code === 'EXPECTED_MISSING' && (
            <ul className="text-xs text-text-mid">
              {result.expected
                .filter((e) => !e.seen)
                .map((e) => (
                  <li key={e.pgn}>• {L(`Görülmedi: ${e.name_tr}`, `Not seen: ${e.name_en}`)}</li>
                ))}
            </ul>
          )}
          <IdentityNotice result={identity} profiles={profiles} onSwitch={onSwitchVehicle} />
          {adapter?.kind === 'simulator' && (
            <p className="text-xs text-text-low">{L('Simülatör: gerçek araç verisi değildir.', 'Simulator: not real vehicle data.')}</p>
          )}
          <button type="button" data-testid="start-scan" className={BTN_PRIMARY} onClick={onDone}>
            {result.battery_warning ? L('Yine de devam', 'Continue anyway') : L('Taramayı başlat', 'Start scan')}
          </button>
        </Shell>
      );
    }
    return (
      <Shell>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi" data-testid="connect-problem">
          <AlertTriangle className="h-5 w-5 text-warn" />
          {L('Bağlantı kurulamadı', "Couldn't connect")}
        </h1>
        <p className="text-sm">{message}</p>
        {result.code !== 'LISTEN_ONLY_UNAVAILABLE' && adapter && (
          <button type="button" className={BTN_PRIMARY} onClick={() => void runTest(adapter)}>
            <RefreshCw className="h-4 w-4" />
            {L('Tekrar dene', 'Try again')}
          </button>
        )}
        <button type="button" className={BTN_SECONDARY} onClick={() => setStep('adapter')}>
          {L('Başka adaptör', 'Another adapter')}
        </button>
        {simulator && adapter?.kind !== 'simulator' && (
          <button type="button" className={BTN_SECONDARY} onClick={() => choose(simulator)}>
            {L('Simülatörle dene', 'Try with simulator')}
          </button>
        )}
      </Shell>
    );
  }

  return null;
};

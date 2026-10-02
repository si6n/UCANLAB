import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeft, Cable, Check, CheckCircle2, Ear, FlaskConical, Loader2, Plug, RefreshCw, Usb } from 'lucide-react';
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
import { CARD, Heading, Notice, Screen } from './Screen';
import { BAR_PRIMARY, BAR_QUIET, BAR_SECONDARY, L, pick } from './text';

/**
 * Connection wizard, Aşama 5 (MECHANIC_FLOW.md §3.7-3.10, §4).
 *
 * Wait for the adapter → plug guide → listen-only test → Ready / plain-language
 * problem. The test only listens: nothing is sent to the vehicle (G1).
 */

type Step = 'adapter' | 'plug' | 'test' | 'result';

const SIMULATOR_ID = 'simulator:sim0';

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

  const changeVehicleButton = (
    <button type="button" className={BAR_QUIET} onClick={onChangeVehicle} data-testid="change-vehicle">
      <ArrowLeft className="h-4 w-4" />
      {L('Aracı değiştir', 'Change vehicle')}
    </button>
  );
  const eyebrow = L('Adım 2 / 4 · Bağlantı', 'Step 2 of 4 · Connection');

  if (step === 'adapter') {
    const real = (adapters ?? []).filter((a) => a.kind !== 'simulator');
    const usable = real.filter((a) => a.usable);
    const blocked = real.filter((a) => !a.usable);
    return (
      <Screen step={2} back={changeVehicleButton}>
        <Heading
          eyebrow={eyebrow}
          icon={<Usb className="h-5 w-5 text-accent" />}
          title={L('Adaptörü bilgisayara takın.', 'Plug the adapter into the computer.')}
          lead={
            usable.length === 0 ? (
              <span className="flex items-center gap-2 text-text-mid">
                <Loader2 className="h-4 w-4 animate-spin" />
                {L('Takınca kendiliğinden bulacağız.', "We'll detect it automatically.")}
              </span>
            ) : undefined
          }
        />
        <ul className={`${CARD} flex flex-col divide-y divide-border-whisper overflow-hidden`}>
          {usable.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                data-testid={`adapter-${a.kind}`}
                className="flex w-full items-center justify-between gap-3 px-4 py-4 text-left transition-colors hover:bg-bg-row-hover"
                onClick={() => choose(a)}
              >
                <span className="flex min-w-0 items-start gap-3">
                  <Cable className="mt-0.5 h-5 w-5 flex-none text-ok" />
                  <span className="min-w-0">
                    <span className="block font-semibold text-text-hi">
                      {L(`${a.label} bulundu`, `${a.label} found`)} <Check className="inline h-4 w-4 text-ok" />
                    </span>
                    {pick(a, 'message') && <span className="block text-xs text-text-mid">{pick(a, 'message')}</span>}
                  </span>
                </span>
                <span className="flex-none rounded-lg bg-accent px-3 py-1.5 text-sm font-semibold text-bg-app">{L('Kullan', 'Use')}</span>
              </button>
            </li>
          ))}
          {blocked.map((a) => (
            <li key={a.id} className="flex items-start gap-3 px-4 py-4 text-sm">
              <AlertTriangle className="mt-0.5 h-5 w-5 flex-none text-warn" />
              <span className="min-w-0">
                <span className="block font-semibold text-text-hi">{a.label}</span>
                <span className="block text-text-body">{pick(a, 'message') || L(`${a.label} kullanılamıyor.`, `${a.label} can't be used.`)}</span>
              </span>
            </li>
          ))}
          {simulator && (
            <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-4">
              <span className="flex min-w-0 items-start gap-3">
                <FlaskConical className="mt-0.5 h-5 w-5 flex-none text-text-mid" />
                <span className="min-w-0">
                  <span className="block font-semibold text-text-hi">{L('Simülatör', 'Simulator')}</span>
                  <span className="block text-xs text-text-mid">
                    {L('Araç olmadan deneyin. Simülatör verisi gerçek araç verisi değildir.', 'Try without a vehicle. Simulator data is not real vehicle data.')}
                  </span>
                </span>
              </span>
              <button type="button" data-testid="adapter-simulator" className={BAR_SECONDARY} onClick={() => choose(simulator)}>
                {L('Simülatörle dene', 'Try with simulator')}
              </button>
            </li>
          )}
          {usable.length === 0 && blocked.length === 0 && !simulator && (
            <li className="px-4 py-4 text-sm text-text-mid">{L('Adaptör aranıyor…', 'Looking for adapters…')}</li>
          )}
        </ul>
        {error && <Notice tone="del">{error}</Notice>}
        {blocked.length > 0 && (
          <div>
            <button type="button" className={BAR_SECONDARY} onClick={() => void scan()}>
              <RefreshCw className="h-4 w-4" />
              {L('Kurdum, tekrar dene', 'Installed it, try again')}
            </button>
          </div>
        )}
      </Screen>
    );
  }

  if (step === 'plug' && adapter) {
    return (
      <Screen
        step={2}
        back={
          <button type="button" className={BAR_QUIET} onClick={() => setStep('adapter')}>
            <ArrowLeft className="h-4 w-4" />
            {L('Başka adaptör', 'Another adapter')}
          </button>
        }
        actions={
          <button type="button" data-testid="plug-done" className={BAR_PRIMARY} onClick={() => void runTest(adapter)}>
            <Check className="h-4 w-4" />
            {L('Taktım, kontak açık', 'Plugged in, ignition on')}
          </button>
        }
      >
        <Heading eyebrow={eyebrow} icon={<Plug className="h-5 w-5 text-accent" />} title={L('Adaptörü araca takın', 'Plug the adapter into the vehicle')} />
        {vehicle.high_voltage && (
          <Notice tone="del">
            {L(
              'Yüksek voltajlı araç: turuncu kablolara dokunmayın. Bu araçta yalnız okuma yapılır.',
              'High-voltage vehicle: do not touch orange cables. Only reading is done on this vehicle.',
            )}
          </Notice>
        )}
        <div className={`${CARD} flex flex-col gap-2 p-5`}>
          <p className="text-[15px] text-text-hi">{pick(vtype, 'plug')}</p>
          <p className="text-xs text-text-mid">{pick(vtype, 'passive_note')}</p>
        </div>
      </Screen>
    );
  }

  if (step === 'test') {
    const current = status?.step === 'ecus' ? 2 : status?.step === 'bitrate' ? 0 : -1;
    return (
      <Screen
        step={2}
        actions={
          <button
            type="button"
            className={BAR_SECONDARY}
            onClick={() => {
              void DesktopBridge.connectionTestCancel();
            }}
          >
            {L('Durdur', 'Stop')}
          </button>
        }
      >
        <Heading
          eyebrow={eyebrow}
          icon={<Ear className="h-5 w-5 text-accent" />}
          title={L('Araçla bağlantı kontrol ediliyor', 'Checking the connection')}
          lead={L('Araca hiçbir şey gönderilmiyor, sadece dinliyoruz.', 'Nothing is sent to the vehicle — we only listen.')}
        />
        <ol className={`${CARD} flex flex-col gap-3 p-5 text-sm`} aria-live="polite">
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
              {s.id === 'bitrate' && status?.bitrate ? <span className="text-xs text-text-mid">({Math.round(status.bitrate / 1000)} kbit/s)</span> : null}
            </li>
          ))}
        </ol>
        <p className="text-xs text-text-mid">{L('ECU: aracın bir bölümünü yöneten bilgisayar.', 'ECU: a computer that runs one part of the vehicle.')}</p>
      </Screen>
    );
  }

  if (step === 'result' && result) {
    const message = pick(result, 'message');
    if (result.usable) {
      return (
        <Screen
          step={2}
          back={changeVehicleButton}
          actions={
            <button type="button" data-testid="start-scan" className={BAR_PRIMARY} onClick={onDone}>
              {result.battery_warning ? L('Yine de devam', 'Continue anyway') : L('Taramayı başlat', 'Start scan')}
            </button>
          }
        >
          {vehicle.high_voltage && (
            <Notice tone="del">{L('Yüksek voltajlı araç: turuncu kablolara dokunmayın.', 'High-voltage vehicle: do not touch orange cables.')}</Notice>
          )}
          {result.battery_warning && <Notice tone="warn">{pick(result, 'battery_message')}</Notice>}
          <Heading
            eyebrow={eyebrow}
            testId="connect-ready"
            icon={result.code === 'READY' ? <CheckCircle2 className="h-6 w-6 text-ok" /> : <AlertTriangle className="h-6 w-6 text-warn" />}
            title={result.code === 'READY' ? L('Hazır', 'Ready') : L('Bağlandı, ama dikkat', 'Connected — please check')}
            lead={
              <>
                {message}{' '}
                {result.ecu_count > 0 && L(`${result.ecu_count} kontrol ünitesi görüldü.`, `${result.ecu_count} control units found.`)}
              </>
            }
          />
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
            <p className="text-xs text-text-mid">{L('Simülatör: gerçek araç verisi değildir.', 'Simulator: not real vehicle data.')}</p>
          )}
        </Screen>
      );
    }
    return (
      <Screen
        step={2}
        back={changeVehicleButton}
        actions={
          <>
            {simulator && adapter?.kind !== 'simulator' && (
              <button type="button" className={BAR_SECONDARY} onClick={() => choose(simulator)}>
                {L('Simülatörle dene', 'Try with simulator')}
              </button>
            )}
            <button type="button" className={BAR_SECONDARY} onClick={() => setStep('adapter')}>
              {L('Başka adaptör', 'Another adapter')}
            </button>
            {result.code !== 'LISTEN_ONLY_UNAVAILABLE' && adapter && (
              <button type="button" className={BAR_PRIMARY} onClick={() => void runTest(adapter)}>
                <RefreshCw className="h-4 w-4" />
                {L('Tekrar dene', 'Try again')}
              </button>
            )}
          </>
        }
      >
        <Heading
          eyebrow={eyebrow}
          testId="connect-problem"
          icon={<AlertTriangle className="h-5 w-5 text-warn" />}
          title={L('Bağlantı kurulamadı', "Couldn't connect")}
          lead={message}
        />
      </Screen>
    );
  }

  return null;
};

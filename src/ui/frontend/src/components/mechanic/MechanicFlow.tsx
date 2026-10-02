import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Anchor, Car, ChevronRight, CircleHelp, Cpu, Lock, Tractor, Truck, Wrench } from 'lucide-react';
import {
  AuthEntitlements,
  DesktopBridge,
  MechanicMode,
  MechanicResult,
  VehicleProfileInfo,
  VehicleTypeInfo,
} from '../../services/bridge';
import { AppFrame, FrameLoading } from '../shell/AppFrame';
import { ICON_BTN } from '../shell/WindowControls';
import { cx } from '../workbench/ui';
import { ConnectWizard } from './ConnectWizard';
import { ScanFlow } from './ScanFlow';
import { CARD, Heading, Screen } from './Screen';
import { L, messageOf, pick } from './text';

/**
 * Mechanic flow, Aşama 4 (docs/product/MECHANIC_FLOW.md §3.4-3.8).
 *
 * Mode choice (asked once, remembered in Python, changeable in Settings) →
 * vehicle type → make/engine with honest coverage labels → connection wizard
 * (ConnectWizard, Aşama 5).
 * Engineer mode goes straight to the existing expert screens.
 */

type Phase = 'checking' | 'mode' | 'type' | 'vehicle' | 'plug' | 'scan' | 'app';

interface MechanicModeApi {
  mode: MechanicMode | null;
  entitlements: AuthEntitlements | null;
  vehicle: VehicleProfileInfo | null;
  setMode: (mode: MechanicMode) => Promise<MechanicResult>;
  changeVehicle: () => void;
}

const MechanicModeContext = createContext<MechanicModeApi | null>(null);

/** Null outside the native shell (dev browser) — callers hide mode controls then. */
export const useMechanicMode = (): MechanicModeApi | null => useContext(MechanicModeContext);

const TYPE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  car: Car,
  truck: Truck,
  boat: Anchor,
  construction: Tractor,
};

/** A big choice button on the plain background (mode, vehicle type). */
const OPTION =
  'flex rounded-2xl border bg-bg-card p-4 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';

function coverageBadge(p: VehicleProfileInfo): { text: string; tone: string } {
  if (p.coverage === 'enriched') return { text: L('Markaya özel veri', 'Maker data'), tone: 'text-ok border-ok' };
  if (p.coverage === 'standard') return { text: L('Genel tarama', 'General scan'), tone: 'text-text-mid border-border-strong' };
  return { text: L('Desteklenmiyor', 'Not supported'), tone: 'text-text-low border-border-whisper' };
}

/** "I don't know" pick: the type's generic profile, else its first selectable one. */
function generalProfileFor(typeId: string, profiles: VehicleProfileInfo[]): VehicleProfileInfo | undefined {
  const own = profiles.filter((p) => p.type === typeId && p.selectable);
  return own.find((p) => p.id === `${typeId}_generic`) ?? own[0];
}

export const MechanicFlow: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [phase, setPhase] = useState<Phase>('checking');
  const [mode, setModeState] = useState<MechanicMode | null>(null);
  const [entitlements, setEntitlements] = useState<AuthEntitlements | null>(null);
  const [types, setTypes] = useState<VehicleTypeInfo[]>([]);
  const [profiles, setProfiles] = useState<VehicleProfileInfo[]>([]);
  const [typeId, setTypeId] = useState<string | null>(null);
  const [vehicle, setVehicle] = useState<VehicleProfileInfo | null>(null);
  const [notice, setNotice] = useState('');
  const [native, setNative] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [state, catalog] = await Promise.all([DesktopBridge.mechanicGetState(), DesktopBridge.vehicleCatalog()]);
        if (cancelled) return;
        if (state === null) {
          setNative(false); // dev browser: no native bridge, show the app as before
          setPhase('app');
          return;
        }
        const allProfiles = catalog.success ? catalog.profiles ?? [] : [];
        setTypes(catalog.success ? catalog.types ?? [] : []);
        setProfiles(allProfiles);
        setEntitlements(state.entitlements);
        const remembered = allProfiles.find((p) => p.id === state.vehicle_profile_id && p.selectable) ?? null;
        setVehicle(remembered);
        const allowedMode = state.mode === 'engineer' && !state.entitlements.engineer ? null : state.mode;
        setModeState(allowedMode);
        if (allowedMode === null) setPhase('mode');
        else if (allowedMode === 'engineer') setPhase('app');
        else setPhase(catalog.success ? 'type' : 'app');
      } catch {
        if (!cancelled) {
          setNative(false);
          setPhase('app'); // capability missing: the app's own guards apply
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const setMode = useCallback(async (next: MechanicMode): Promise<MechanicResult> => {
    const res = await DesktopBridge.mechanicSetMode(next);
    if (res.success) {
      setModeState(next);
      setNotice('');
      setPhase(next === 'engineer' ? 'app' : 'type');
    } else {
      setNotice(messageOf(res) || L('Mod değiştirilemedi.', 'Could not change the mode.'));
    }
    return res;
  }, []);

  const selectVehicle = useCallback(async (profileId: string) => {
    const res = await DesktopBridge.vehicleSelect(profileId);
    if (!res.success || !res.profile) {
      setNotice(messageOf(res) || L('Bu araç seçilemedi.', 'This vehicle cannot be selected.'));
      return;
    }
    setNotice('');
    setVehicle(res.profile);
    setTypeId(res.profile.type);
    setPhase('plug');
  }, []);

  const changeVehicle = useCallback(() => {
    setNotice('');
    setPhase('type');
  }, []);

  const api = useMemo<MechanicModeApi | null>(
    () => (native ? { mode, entitlements, vehicle, setMode, changeVehicle } : null),
    [native, mode, entitlements, vehicle, setMode, changeVehicle],
  );

  const vtype = types.find((t) => t.id === (vehicle?.type ?? typeId)) ?? null;

  let screen: React.ReactNode = null;

  if (phase === 'checking') {
    screen = <FrameLoading />;
  }

  if (phase === 'mode') {
    const engineerLocked = !entitlements?.engineer;
    screen = (
      <Screen width="narrow">
        <Heading title={L('Uygulamayı nasıl kullanacaksınız?', 'How will you use the app?')} />
        <div className="flex flex-col gap-3">
          <button type="button" data-testid="mode-mechanic" className={cx(OPTION, 'items-start gap-3 border-border-strong hover:border-accent')} onClick={() => void setMode('mechanic')}>
            <Wrench className="mt-0.5 h-6 w-6 flex-none text-accent" />
            <span>
              <span className="block font-semibold text-text-hi">{L('Tamirci', 'Mechanic')}</span>
              <span className="block text-sm">
                {L('Arızayı bul, ne yapacağımı söyle. Teknik bilgi gerekmez.', 'Find the fault and tell me what to do. No technical knowledge needed.')}
              </span>
            </span>
          </button>
          <button
            type="button"
            data-testid="mode-engineer"
            disabled={engineerLocked}
            className={cx(OPTION, 'items-start gap-3 border-border-strong enabled:hover:border-accent disabled:opacity-60')}
            onClick={() => void setMode('engineer')}
          >
            {engineerLocked ? <Lock className="mt-0.5 h-6 w-6 flex-none text-text-low" /> : <Cpu className="mt-0.5 h-6 w-6 flex-none text-accent" />}
            <span>
              <span className="block font-semibold text-text-hi">{L('Mühendis', 'Engineer')}</span>
              <span className="block text-sm">
                {L('Ham CAN verisi, sinyal analizi ve uzman araçları.', 'Raw CAN data, signal analysis and expert tools.')}
              </span>
              {engineerLocked && <span className="mt-1 block text-xs text-text-low">{L('Bu mod paketinizde yok.', 'Not included in your plan.')}</span>}
            </span>
          </button>
        </div>
        {notice && (
          <p className="text-sm text-del" role="alert">
            {notice}
          </p>
        )}
        <p className="text-xs text-text-low">{L("Bunu daha sonra Ayarlar'dan değiştirebilirsiniz.", 'You can change this later in Settings.')}</p>
      </Screen>
    );
  }

  if (phase === 'type' || (phase === 'vehicle' && typeId)) {
    const list = typeId ? profiles.filter((p) => p.type === typeId) : [];
    const general = typeId ? generalProfileFor(typeId, profiles) : undefined;
    // The "I don't know" row already stands for the general profile.
    const others = list.filter((p) => p.id !== general?.id);
    const chosenType = types.find((t) => t.id === typeId) ?? null;
    screen = (
      <Screen step={1}>
        <Heading
          eyebrow={L('Adım 1 / 4', 'Step 1 of 4')}
          title={L('Hangi aracı kontrol ediyorsunuz?', 'What are you checking?')}
          lead={L(
            'Seçim, adaptörü hangi sokete takacağınızı ve aracın hangi “dili” konuştuğunu belirler.',
            'The choice decides which socket the adapter goes into and which “language” the vehicle speaks.',
          )}
        />
        {vehicle && (
          <button
            type="button"
            data-testid="vehicle-last"
            className={cx(OPTION, 'items-center justify-between gap-3 border-accent-line hover:border-accent')}
            onClick={() => void selectVehicle(vehicle.id)}
          >
            <span>
              <span className="block text-xs text-text-mid">{L('Son seçilen araç', 'Last vehicle')}</span>
              <span className="block font-semibold text-text-hi">{pick(vehicle, 'label')}</span>
            </span>
            <ChevronRight className="h-5 w-5 flex-none text-accent" />
          </button>
        )}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {types.map((t) => {
            const Icon = TYPE_ICONS[t.id] ?? Car;
            const active = t.id === typeId;
            return (
              <button
                key={t.id}
                type="button"
                data-testid={`vehicle-type-${t.id}`}
                aria-pressed={active}
                className={cx(OPTION, 'flex-col items-start gap-2', active ? 'border-accent bg-accent-soft' : 'border-border-strong hover:border-accent')}
                onClick={() => {
                  setTypeId(t.id);
                  setNotice('');
                  setPhase('vehicle');
                }}
              >
                <Icon className="h-6 w-6 flex-none text-accent" />
                <span>
                  <span className="block font-semibold text-text-hi">{pick(t, 'label')}</span>
                  <span className="block text-xs text-text-mid">{pick(t, 'sub')}</span>
                </span>
              </button>
            );
          })}
        </div>
        {typeId && (
          <section className="flex flex-col gap-2" aria-labelledby="vehicle-pick-title">
            <h2 id="vehicle-pick-title" className="text-xs font-semibold uppercase tracking-wide text-text-low">
              {L('Marka veya motoru seçin', 'Choose the make or engine')}
              {chosenType ? ` · ${pick(chosenType, 'label')}` : ''}
            </h2>
            <ul className={cx(CARD, 'flex flex-col divide-y divide-border-whisper overflow-hidden')}>
              {general && (
                <li>
                  <button
                    type="button"
                    data-testid="vehicle-general"
                    className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-bg-row-hover"
                    onClick={() => void selectVehicle(general.id)}
                  >
                    <span className="flex min-w-0 items-start gap-3">
                      <CircleHelp className="mt-0.5 h-5 w-5 flex-none text-text-mid" />
                      <span className="min-w-0">
                        <span className="block font-medium text-text-hi">{L('Bilmiyorum, genel tarama yap', "I don't know — run a general scan")}</span>
                        <span className="block text-xs text-text-mid">
                          {pick(general, 'label')}
                          {pick(general, 'note') ? ` · ${pick(general, 'note')}` : ''}
                        </span>
                      </span>
                    </span>
                    <ChevronRight className="h-4 w-4 flex-none text-text-mid" />
                  </button>
                </li>
              )}
              {others.map((p) => {
                const badge = coverageBadge(p);
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      data-testid={`vehicle-${p.id}`}
                      disabled={!p.selectable}
                      className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors enabled:hover:bg-bg-row-hover disabled:cursor-not-allowed disabled:opacity-60"
                      onClick={() => void selectVehicle(p.id)}
                    >
                      <span className="min-w-0">
                        <span className="block font-medium text-text-hi">{pick(p, 'label')}</span>
                        {pick(p, 'note') && <span className="block text-xs text-text-mid">{pick(p, 'note')}</span>}
                      </span>
                      <span className="flex flex-none items-center gap-2">
                        {p.high_voltage && (
                          <span className="rounded-full border border-warn px-2 py-0.5 text-[11px] font-semibold text-warn">{L('Yüksek voltaj', 'High voltage')}</span>
                        )}
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${badge.tone}`}>{badge.text}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
        {notice && (
          <p className="text-sm text-del" role="alert">
            {notice}
          </p>
        )}
      </Screen>
    );
  }

  if (phase === 'plug' && vehicle && vtype) {
    screen = (
      <ConnectWizard
        key={vehicle.id}
        vehicle={vehicle}
        vtype={vtype}
        profiles={profiles}
        onChangeVehicle={changeVehicle}
        onSwitchVehicle={(id) => void selectVehicle(id)}
        onDone={() => setPhase('scan')}
      />
    );
  }

  if (phase === 'scan' && vehicle && vtype) {
    screen = (
      <ScanFlow
        key={vehicle.id}
        vehicle={vehicle}
        vtype={vtype}
        entitlements={entitlements}
        onChangeVehicle={changeVehicle}
        onExpert={entitlements?.engineer ? () => setPhase('app') : null}
      />
    );
  }

  if (phase === 'app' || screen === null) {
    return <MechanicModeContext.Provider value={api}>{children}</MechanicModeContext.Provider>;
  }

  const VehicleIcon = TYPE_ICONS[vehicle?.type ?? ''] ?? Car;
  const showVehicle = vehicle !== null && (phase === 'plug' || phase === 'scan');
  // Switching modes is offered only while nothing is running (choosing the vehicle).
  const offerExpert = Boolean(entitlements?.engineer) && (phase === 'type' || phase === 'vehicle');

  return (
    <MechanicModeContext.Provider value={api}>
      <AppFrame
        subtitle={phase === 'mode' || phase === 'checking' ? undefined : L('Tamirci modu', 'Mechanic mode')}
        context={
          showVehicle && vehicle ? (
            <span className="inline-flex items-center gap-1.5">
              <VehicleIcon className="h-4 w-4 flex-none text-text-mid" />
              {pick(vehicle, 'label')}
            </span>
          ) : null
        }
        extra={
          offerExpert ? (
            <button
              type="button"
              className={cx(ICON_BTN, 'w-auto gap-1.5 px-2.5 text-[12.5px] font-medium')}
              onClick={() => void setMode('engineer')}
              title={L('Uzman moduna geç; seçim bu bilgisayarda hatırlanır.', 'Switch to engineer mode; remembered on this computer.')}
              data-testid="to-engineer"
            >
              <Cpu className="h-4 w-4" />
              {L('Uzman masası', 'Engineer workbench')}
            </button>
          ) : null
        }
      >
        {screen}
      </AppFrame>
    </MechanicModeContext.Provider>
  );
};

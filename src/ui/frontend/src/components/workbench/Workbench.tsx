import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, Cable, Cpu, FileDown, LineChart, Radio, Settings, Stethoscope, Waypoints, Wrench } from 'lucide-react';
import { BusInfoResult, DesktopBridge } from '../../services/bridge';
import { L } from '../mechanic/text';
import { useMechanicMode } from '../mechanic/MechanicFlow';
import { useUiHeartbeat } from '../mechanic/useUiHeartbeat';
import { DisplayMenu } from '../shell/DisplayMenu';
import { EstopBanner, EstopButton, EstopResetDialog } from '../shell/Estop';
import { safetyView } from '../shell/safety';
import { WindowControls } from '../shell/WindowControls';
import { AssistantView } from './AssistantView';
import { DiscoveryView, discoveryKeyFromTrafficKey } from './DiscoveryView';
import { EcuView } from './EcuView';
import { LiveTraffic, SOURCE_LABEL } from './LiveTraffic';
import { PinoutView } from './PinoutView';
import { PlotView } from './PlotView';
import { RecordsView } from './RecordsView';
import { SettingsPanel } from './SettingsPanel';
import { useBusStream } from './useBusStream';
import { StatusText, Tone, cx } from './ui';

/**
 * Uzman masası — the engineer workbench, rebuilt in the mechanic-flow design
 * language (docs/product/ENGINEER_WORKBENCH.md).
 *
 * Shell rules:
 *  - the status bar only shows measured facts: the bus Python listens to,
 *    the frames that really arrived (load is computed from them), the
 *    driver's own error counter and the supervisor state; with no data it
 *    says so instead of showing numbers;
 *  - the E-Stop is visible on every screen.
 */

type ModuleId = 'traffic' | 'plot' | 'discovery' | 'assistant' | 'records' | 'ecu' | 'pinout' | 'settings';

interface ModuleDef {
  id: ModuleId;
  icon: React.ComponentType<{ className?: string }>;
  title: () => string;
  hint: () => string;
}

const GROUPS: Array<{ label: () => string; items: ModuleDef[] }> = [
  {
    label: () => L('Veri', 'Data'),
    items: [
      { id: 'traffic', icon: Radio, title: () => L('Canlı trafik', 'Live traffic'), hint: () => L('Hattaki her çerçeve', 'Every frame on the bus') },
      { id: 'plot', icon: LineChart, title: () => L('Grafik', 'Plot'), hint: () => L('Çözülmüş değerler zamanla', 'Decoded values over time') },
      {
        id: 'discovery',
        icon: Waypoints,
        title: () => L('Sinyal keşfi', 'Signal discovery'),
        hint: () => L('Bilinmeyen sinyalleri çöz', 'Decode unknown signals'),
      },
    ],
  },
  {
    label: () => L('Araç', 'Vehicle'),
    items: [
      {
        id: 'assistant',
        icon: Stethoscope,
        title: () => L('Teşhis asistanı', 'Diagnostic assistant'),
        hint: () => L('Kanıta dayalı değerlendirme', 'Evidence-based assessment'),
      },
      {
        id: 'ecu',
        icon: Cpu,
        title: () => L('ECU programlama', 'ECU programming'),
        hint: () => L('Onay ve kilit ile yazılım yükleme', 'Gated firmware update'),
      },
      { id: 'pinout', icon: Cable, title: () => L('Pin rehberi', 'Pinout guide'), hint: () => L('Konnektör ve kablolama', 'Connectors and wiring') },
    ],
  },
  {
    label: () => L('Çıktı', 'Output'),
    items: [
      { id: 'records', icon: FileDown, title: () => L('Kayıt ve rapor', 'Records and reports'), hint: () => L('Dışa aktar, rapor oluştur', 'Export, build reports') },
    ],
  },
];

const SETTINGS: ModuleDef = {
  id: 'settings',
  icon: Settings,
  title: () => L('Ayarlar', 'Settings'),
  hint: () => L('Adaptör, lisans, güvenlik', 'Adapter, licence, safety'),
};

const ALL: ModuleDef[] = [...GROUPS.flatMap((g) => g.items), SETTINGS];

const VEHICLE_LABEL: Record<string, () => string> = {
  car: () => L('Otomobil', 'Car'),
  truck: () => L('Kamyon', 'Truck'),
  boat: () => L('Tekne', 'Boat'),
  construction: () => L('İş makinesi', 'Construction'),
};

function busLabel(info: BusInfoResult | null): string {
  if (!info) return L('Adaptör bilinmiyor', 'Adapter unknown');
  if (info.simulated) return `${L('Simülatör', 'Simulator')} · ${VEHICLE_LABEL[info.vehicle_type ?? '']?.() ?? ''}`;
  const kbps = info.bitrate ? ` · ${Math.round(info.bitrate / 1000)} kbps` : '';
  return `${info.interface ?? '?'} ${info.channel ?? ''}${kbps}`.trim();
}

const NavItem: React.FC<{ def: ModuleDef; active: boolean; onClick: () => void }> = ({ def, active, onClick }) => {
  const Icon = def.icon;
  return (
    <button
      type="button"
      data-testid={`nav-${def.id}`}
      aria-current={active ? 'page' : undefined}
      onClick={onClick}
      className={cx(
        'flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition-colors',
        active ? 'bg-bg-row-selected text-text-hi' : 'text-text-body hover:bg-bg-row-hover',
      )}
    >
      <Icon className={cx('mt-0.5 h-[18px] w-[18px] flex-none', active ? 'text-accent' : 'text-text-mid')} />
      <span className="min-w-0">
        <span className="block text-[13.5px] font-semibold leading-tight">{def.title()}</span>
        <span className="block truncate text-[12px] text-text-mid">{def.hint()}</span>
      </span>
    </button>
  );
};

export const Workbench: React.FC = () => {
  const mechanic = useMechanicMode();
  const native = DesktopBridge.isNative();
  const [active, setActive] = useState<ModuleId>('traffic');
  const [discoveryKey, setDiscoveryKey] = useState<string | null>(null);
  const [safety, setSafety] = useState<string | null>(null);
  const [busInfo, setBusInfo] = useState<BusInfoResult | null>(null);
  const [simError, setSimError] = useState('');
  const [resetOpen, setResetOpen] = useState(false);
  const { snap, setPaused, clear } = useBusStream(true);

  // The TX watchdog lease follows this window being alive (same contract as
  // the previous shell): hidden/frozen UI → lease expires → TX refused.
  useUiHeartbeat(native);

  const poll = useCallback(async () => {
    if (!native) return;
    try {
      const [state, info] = await Promise.all([DesktopBridge.getSafetyState(), DesktopBridge.busGetInfo()]);
      setSafety(state);
      setBusInfo(info);
    } catch {
      setSafety(null);
      setBusInfo(null);
    }
  }, [native]);

  useEffect(() => {
    void poll();
    const t = window.setInterval(() => void poll(), 1000);
    return () => window.clearInterval(t);
  }, [poll]);

  const SIM_ERRORS: Record<string, () => string> = {
    ESTOP_ENGAGED: () => L('Acil durdurma kilitliyken simülatöre geçilmez; gerçek hat kaydı sürer.', 'Not while the E-Stop is latched; the real bus keeps recording.'),
    TX_ARMED: () => L('Araca yazma açıkken hat değiştirilmez. Önce yazmayı kapatın.', 'Not while transmit is armed. Disarm first.'),
    TEST_RUNNING: () => L('Ayarlar’da bir bağlantı testi sürüyor; bitince yeniden deneyin.', 'A connection test is running in Settings; try again when it ends.'),
  };

  const startSimulator = async (vehicleType: string) => {
    setSimError('');
    const res = await DesktopBridge.simVehicleStart(vehicleType);
    if (!res.success) {
      setSimError(SIM_ERRORS[res.error_code ?? '']?.() ?? L('Simülatör başlatılamadı.', 'The simulator could not start.'));
    }
    clear();
    void poll();
  };

  const stopSimulator = async () => {
    await DesktopBridge.simVehicleStop();
    clear();
    void poll();
  };

  const sv = safetyView(native ? safety : null);
  const def = ALL.find((m) => m.id === active) ?? ALL[0];
  const liveSources = snap.sources;
  const hasSim = liveSources.some((s) => s === 'simulator' || s === 'synthetic') || Boolean(busInfo?.simulated);
  const hasReplay = liveSources.includes('replay');

  const loadPct =
    // A replayed recording's rate says nothing about the bus being listened to.
    !hasReplay && busInfo?.bitrate && snap.bitsPerSecond !== null && snap.perSecond > 0
      ? Math.min(100, Math.round((snap.bitsPerSecond / busInfo.bitrate) * 100))
      : null;

  const busChip = useMemo(() => {
    const quiet = snap.total === 0 || snap.perSecond === 0;
    const tone: Tone = quiet ? 'neutral' : hasSim || hasReplay ? 'warn' : 'ok';
    const sourceText = quiet
      ? snap.total === 0
        ? L('veri yok', 'no data')
        : L('hat sessiz', 'bus quiet')
      : liveSources.map((s) => SOURCE_LABEL[s]()).join(' + ');
    return (
      <StatusText testId="bus-chip" tone={tone} pulse={!quiet} title={L('Dinlenen hat ve verinin kaynağı', 'Bus being listened to and where the data comes from')}>
        {native && busInfo && !busInfo.simulated ? `${busLabel(busInfo)} · ` : ''}
        {busInfo?.simulated ? busLabel(busInfo) : sourceText}
      </StatusText>
    );
  }, [snap.total, snap.perSecond, liveSources, hasSim, hasReplay, native, busInfo]);


  let body: React.ReactNode;
  switch (active) {
    case 'traffic':
      body = (
        <LiveTraffic
          snap={snap}
          onPause={setPaused}
          onClear={clear}
          busLabel={native ? busLabel(busInfo) : null}
          simulatorRunning={Boolean(busInfo?.simulated)}
          canStartSimulator={native && safety !== 'FAULT'}
          simError={simError}
          onStartSimulator={(t) => void startSimulator(t)}
          onStopSimulator={() => void stopSimulator()}
          onOpenSettings={() => setActive('settings')}
          onDiscover={(trafficKey) => {
            setDiscoveryKey(discoveryKeyFromTrafficKey(trafficKey));
            setActive('discovery');
          }}
        />
      );
      break;
    case 'plot':
      body = <PlotView onOpenDiscovery={() => setActive('discovery')} />;
      break;
    case 'discovery':
      body = <DiscoveryView initialKey={discoveryKey} simulator={Boolean(busInfo?.simulated)} />;
      break;
    case 'assistant':
      body = <AssistantView />;
      break;
    case 'ecu':
      body = <EcuView />;
      break;
    case 'pinout':
      body = <PinoutView />;
      break;
    case 'records':
      body = (
        <RecordsView
          totalFrames={snap.total}
          sourceNote={
            hasSim
              ? L(
                  'Bu kayıtta simülatör verisi var. Dosyada kaynak ayrıca işaretlenmez; paylaşırken belirtin.',
                  'This capture contains simulator data. The file does not mark the source; say so when sharing.',
                )
              : null
          }
        />
      );
      break;
    case 'settings':
      body = (
        <SettingsPanel
          busInfo={native ? busInfo : null}
          safety={native ? safety : null}
          onBusChanged={() => {
            clear();
            void poll();
          }}
          onSwitchToMechanic={mechanic ? () => void mechanic.setMode('mechanic') : null}
        />
      );
      break;
    default:
      body = null;
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg-app text-text-body" data-testid="workbench">
      <aside className="flex w-64 flex-none flex-col border-r border-border-whisper bg-bg-rail">
        <div className="pywebview-drag-region flex items-center gap-2.5 px-5 pb-3 pt-5">
          <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-accent-line bg-accent-soft text-accent">
            <Activity className="h-4 w-4" />
          </span>
          <span>
            <span className="block text-[14px] font-semibold leading-tight text-text-hi">UCanLab</span>
            <span className="block text-[12px] text-text-mid">{L('Uzman masası', 'Engineer workbench')}</span>
          </span>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 pb-3">
          {GROUPS.map((g) => (
            <div key={g.label()} className="mt-3">
              <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-text-low">{g.label()}</div>
              <div className="flex flex-col gap-0.5">
                {g.items.map((m) => (
                  <NavItem key={m.id} def={m} active={active === m.id} onClick={() => setActive(m.id)} />
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="flex flex-col gap-0.5 border-t border-border-whisper p-3">
          <NavItem def={SETTINGS} active={active === 'settings'} onClick={() => setActive('settings')} />
          {mechanic && (
            <button
              type="button"
              data-testid="to-mechanic"
              onClick={() => void mechanic.setMode('mechanic')}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13.5px] font-semibold text-text-body transition-colors hover:bg-bg-row-hover"
            >
              <Wrench className="h-[18px] w-[18px] text-text-mid" />
              {L('Tamirci moduna geç', 'Switch to mechanic mode')}
            </button>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[60px] flex-none items-center gap-4 border-b border-border-whisper pl-5 pr-2">
          <div className="pywebview-drag-region flex min-w-0 flex-1 flex-col justify-center self-stretch">
            <h1 className="truncate text-[17px] font-semibold leading-tight text-text-hi" data-testid="page-title">
              {def.title()}
            </h1>
            <p className="truncate text-[12.5px] text-text-mid">{def.hint()}</p>
          </div>

          <div className="flex flex-none items-center gap-4">
            {busChip}
            {loadPct !== null && (
              <StatusText
                testId="load-chip"
                dot={false}
                tone={loadPct > 70 ? 'danger' : loadPct > 40 ? 'warn' : 'neutral'}
                title={L(
                  'Gelen çerçevelerin boyutundan hesaplanır (bit doldurma hariç); gerçek yük biraz daha yüksektir.',
                  'Computed from the frames received (without stuff bits); the real load is slightly higher.',
                )}
              >
                {L('Yük', 'Load')} ≈ %{loadPct}
              </StatusText>
            )}
            {(busInfo?.error_frames ?? 0) > 0 && (
              <StatusText tone="danger" testId="error-chip" title={L('Sürücünün bildirdiği hata çerçeveleri', 'Error frames reported by the driver')}>
                {busInfo?.error_frames} {L('hata çerçevesi', 'error frames')}
              </StatusText>
            )}
            {busInfo?.bus_state === 'bus_off' && <StatusText tone="danger">BUS-OFF</StatusText>}
            <StatusText tone={sv.tone} title={sv.detail} testId="safety-chip">
              {sv.text}
            </StatusText>
          </div>
          <EstopButton native={native} onTriggered={() => void poll()} />
          <span className="h-5 w-px flex-none bg-border-whisper" aria-hidden="true" />
          <div className="-ml-2 flex flex-none items-center gap-0.5">
            <DisplayMenu />
            <WindowControls native={native} />
          </div>
        </header>

        {safety === 'FAULT' && <EstopBanner detail={sv.detail} onOpenReset={() => setResetOpen(true)} />}
        {resetOpen && <EstopResetDialog onClose={() => setResetOpen(false)} onReset={() => void poll()} />}

        <main className="min-h-0 flex-1 overflow-hidden p-4">{body}</main>
      </div>
    </div>
  );
};

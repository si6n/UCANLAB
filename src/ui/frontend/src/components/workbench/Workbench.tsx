import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Cable,
  Cpu,
  FileDown,
  LineChart,
  Minus,
  Moon,
  OctagonX,
  Radio,
  Settings,
  Square,
  Sun,
  Waypoints,
  Wrench,
  X,
} from 'lucide-react';
import { BusInfoResult, DesktopBridge } from '../../services/bridge';
import { CANFrame } from '../../types/can';
import { L } from '../mechanic/text';
import { useMechanicMode } from '../mechanic/MechanicFlow';
import { useUiHeartbeat } from '../mechanic/useUiHeartbeat';
import { SignalDiscoveryView } from '../discovery/SignalDiscoveryView';
import { EcuFlashingView } from '../ecu/EcuFlashingView';
import { PinoutGuideView } from '../pinout/PinoutGuideView';
import { SettingsView } from '../settings/SettingsView';
import { BusFrame } from './frameBus';
import { LiveTraffic, SOURCE_LABEL } from './LiveTraffic';
import { PlotView } from './PlotView';
import { RecordsView } from './RecordsView';
import { useBusStream } from './useBusStream';
import { BTN_QUIET, Chip, Dot, Tone, cx } from './ui';

/**
 * Uzman masası — the engineer workbench, rebuilt in the mechanic-flow design
 * language (docs/product/ENGINEER_WORKBENCH.md).
 *
 * Shell rules:
 *  - the status bar only shows measured facts: the bus Python listens to,
 *    the frames that really arrived (load is computed from them), the
 *    driver's own error counter and the supervisor state; with no data it
 *    says so instead of showing numbers;
 *  - the E-Stop is visible on every screen;
 *  - modules not yet rebuilt render their previous view, labelled as such.
 */

type ModuleId = 'traffic' | 'plot' | 'discovery' | 'records' | 'ecu' | 'pinout' | 'settings';

interface ModuleDef {
  id: ModuleId;
  icon: React.ComponentType<{ className?: string }>;
  title: () => string;
  hint: () => string;
  legacy?: boolean;
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
        legacy: true,
      },
    ],
  },
  {
    label: () => L('Araç', 'Vehicle'),
    items: [
      {
        id: 'ecu',
        icon: Cpu,
        title: () => L('ECU programlama', 'ECU programming'),
        hint: () => L('Onay ve kilit ile yazılım yükleme', 'Gated firmware update'),
        legacy: true,
      },
      { id: 'pinout', icon: Cable, title: () => L('Pin rehberi', 'Pinout guide'), hint: () => L('Konnektör ve kablolama', 'Connectors and wiring'), legacy: true },
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
  legacy: true,
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

type SafetyView = { tone: Tone; text: string; detail: string };

function safetyView(state: string | null): SafetyView {
  switch (state) {
    case 'ARMED_TX':
    case 'ACTIVE':
      return {
        tone: 'warn',
        text: L('Araca yazma açık', 'Transmit armed'),
        detail: L('Onaylı bir işlem araca çerçeve gönderebilir.', 'An approved operation may send frames to the vehicle.'),
      };
    case 'FAULT':
      return {
        tone: 'danger',
        text: L('Güvenlik kilidi', 'Safety lock'),
        detail: L(
          'E-Stop veya bir güvenlik hatası nedeniyle gönderim kapalı. Kilidi yalnızca yetkili sıfırlama açar.',
          'Transmission is off because of the E-Stop or a safety fault. Only an authorised reset clears it.',
        ),
      };
    case 'PASSIVE':
    case 'SAFE':
    case 'STARTUP':
      return {
        tone: 'ok',
        text: L('Yalnızca dinleme', 'Listen only'),
        detail: L('Uygulama araca hiçbir çerçeve göndermiyor.', 'The app sends no frames to the vehicle.'),
      };
    default:
      return {
        tone: 'neutral',
        text: L('Güvenlik durumu bilinmiyor', 'Safety state unknown'),
        detail: L('Masaüstü uygulamasına bağlı değil.', 'Not connected to the desktop app.'),
      };
  }
}

function toLegacyFrame(f: BusFrame): CANFrame {
  const dataHex = Array.from(f.data, (b) => b.toString(16).toUpperCase().padStart(2, '0'));
  return {
    id: `wb-${f.seq}`,
    timeSec: f.t,
    timeFormatted: `${f.t.toFixed(4)}s`,
    channel: f.channel,
    canIdHex: f.idText,
    canIdDec: f.arb,
    frameType: f.fd ? 'FD' : f.extended ? 'Ext' : 'Std',
    dir: 'RX',
    dlc: f.dlc,
    dataHex,
    ascii: Array.from(f.data, (b) => (b >= 32 && b <= 126 ? String.fromCharCode(b) : '.')).join(''),
    isCanFd: f.fd,
  };
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
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try {
      return localStorage.getItem('ucanlab.theme') === 'light' ? 'light' : 'dark';
    } catch {
      return 'dark';
    }
  });
  const [safety, setSafety] = useState<string | null>(null);
  const [busInfo, setBusInfo] = useState<BusInfoResult | null>(null);
  const [simError, setSimError] = useState('');
  const [estopBusy, setEstopBusy] = useState(false);
  const [channel, setChannel] = useState('vcan0');
  const [baudRate, setBaudRate] = useState('250 kbps');
  const { snap, setPaused, clear } = useBusStream(true);

  // The TX watchdog lease follows this window being alive (same contract as
  // the previous shell): hidden/frozen UI → lease expires → TX refused.
  useUiHeartbeat(native);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try {
      localStorage.setItem('ucanlab.theme', theme);
    } catch {
      /* storage unavailable: theme still applies for this session */
    }
  }, [theme]);

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

  const estop = async () => {
    setEstopBusy(true);
    try {
      await DesktopBridge.triggerEstop();
    } finally {
      setEstopBusy(false);
      void poll();
    }
  };

  const SIM_ERRORS: Record<string, () => string> = {
    ESTOP_ENGAGED: () => L('Acil durdurma kilitliyken simülatöre geçilmez; gerçek hat kaydı sürer.', 'Not while the E-Stop is latched; the real bus keeps recording.'),
    TX_ARMED: () => L('Araca yazma açıkken hat değiştirilmez. Önce yazmayı kapatın.', 'Not while transmit is armed. Disarm first.'),
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

  const loadPct =
    busInfo?.bitrate && snap.bitsPerSecond !== null && snap.perSecond > 0
      ? Math.min(100, Math.round((snap.bitsPerSecond / busInfo.bitrate) * 100))
      : null;

  const busChip = useMemo(() => {
    const quiet = snap.total === 0 || snap.perSecond === 0;
    const tone: Tone = quiet ? 'neutral' : hasSim ? 'warn' : 'ok';
    const sourceText = quiet
      ? snap.total === 0
        ? L('veri yok', 'no data')
        : L('hat sessiz', 'bus quiet')
      : liveSources.map((s) => SOURCE_LABEL[s]()).join(' + ');
    return (
      <Chip testId="bus-chip" tone={tone} title={L('Dinlenen hat ve verinin kaynağı', 'Bus being listened to and where the data comes from')}>
        <Dot tone={tone} pulse={!quiet} />
        {native && busInfo && !busInfo.simulated ? `${busLabel(busInfo)} · ` : ''}
        {busInfo?.simulated ? busLabel(busInfo) : sourceText}
      </Chip>
    );
  }, [snap.total, snap.perSecond, liveSources, hasSim, native, busInfo]);

  const legacyFrames = useMemo(() => (active === 'discovery' ? snap.recent.map(toLegacyFrame) : []), [active, snap.recent]);

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
          onDiscover={() => setActive('discovery')}
        />
      );
      break;
    case 'plot':
      body = <PlotView onOpenDiscovery={() => setActive('discovery')} />;
      break;
    case 'discovery':
      body = <SignalDiscoveryView latestFrame={legacyFrames[0] ?? null} frames={legacyFrames} />;
      break;
    case 'ecu':
      body = <EcuFlashingView />;
      break;
    case 'pinout':
      body = <PinoutGuideView />;
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
        <SettingsView
          channel={channel}
          baudRate={baudRate}
          onSave={(s) => {
            setChannel(s.channel);
            setBaudRate(s.baudRate);
            void DesktopBridge.updateSettings(s);
          }}
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
        <header className="pywebview-drag-region flex flex-none items-center justify-between gap-3 border-b border-border-whisper px-5 py-3">
          <div className="min-w-0" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
            <div className="flex items-center gap-2">
              <h1 className="text-[17px] font-semibold text-text-hi" data-testid="page-title">
                {def.title()}
              </h1>
              {def.legacy && (
                <Chip title={L('Bu ekran henüz yeni tasarıma taşınmadı.', 'This screen has not been rebuilt yet.')}>
                  {L('Önceki görünüm', 'Previous view')}
                </Chip>
              )}
            </div>
            <p className="text-[12.5px] text-text-mid">{def.hint()}</p>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
            {busChip}
            {loadPct !== null && (
              <Chip
                testId="load-chip"
                tone={loadPct > 70 ? 'danger' : loadPct > 40 ? 'warn' : 'neutral'}
                title={L(
                  'Gelen çerçevelerin boyutundan hesaplanır (bit doldurma hariç); gerçek yük biraz daha yüksektir.',
                  'Computed from the frames received (without stuff bits); the real load is slightly higher.',
                )}
              >
                {L('Yük', 'Load')} ≈ %{loadPct}
              </Chip>
            )}
            {(busInfo?.error_frames ?? 0) > 0 && (
              <Chip tone="danger" testId="error-chip" title={L('Sürücünün bildirdiği hata çerçeveleri', 'Error frames reported by the driver')}>
                {busInfo?.error_frames} {L('hata çerçevesi', 'error frames')}
              </Chip>
            )}
            {busInfo?.bus_state === 'bus_off' && <Chip tone="danger">BUS-OFF</Chip>}
            <Chip tone={sv.tone} title={sv.detail} testId="safety-chip">
              <Dot tone={sv.tone} />
              {sv.text}
            </Chip>
            <button
              type="button"
              data-testid="estop"
              onClick={() => void estop()}
              disabled={!native || estopBusy}
              title={L('Araca giden tüm gönderimi anında keser.', 'Immediately cuts every transmission to the vehicle.')}
              className="inline-flex items-center gap-1.5 rounded-lg bg-del px-3 py-1.5 text-[13px] font-bold text-white transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              <OctagonX className="h-4 w-4" />
              {L('ACİL DURDUR', 'E-STOP')}
            </button>
            <div className="mx-1 h-5 w-px bg-border-whisper" />
            <button
              type="button"
              className={cx(BTN_QUIET, 'px-2')}
              onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
              aria-label={L('Tema', 'Theme')}
            >
              {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            {native && (
              <>
                <button type="button" className={cx(BTN_QUIET, 'px-2')} onClick={() => DesktopBridge.minimizeWindow()} aria-label={L('Küçült', 'Minimise')}>
                  <Minus className="h-4 w-4" />
                </button>
                <button type="button" className={cx(BTN_QUIET, 'px-2')} onClick={() => DesktopBridge.maximizeWindow()} aria-label={L('Büyüt', 'Maximise')}>
                  <Square className="h-3.5 w-3.5" />
                </button>
                <button type="button" className={cx(BTN_QUIET, 'px-2 hover:bg-del hover:text-white')} onClick={() => DesktopBridge.closeWindow()} aria-label={L('Kapat', 'Close')}>
                  <X className="h-4 w-4" />
                </button>
              </>
            )}
          </div>
        </header>

        {safety === 'FAULT' && (
          <div role="alert" data-testid="estop-banner" className="border-b border-danger-border bg-danger-soft px-5 py-2.5 text-[13px] text-del">
            <b>{L('Gönderim kilitli.', 'Transmission locked.')}</b> {sv.detail}
          </div>
        )}

        <main className={cx('min-h-0 flex-1 p-4', def.legacy ? 'overflow-auto' : 'overflow-hidden')}>
          {def.legacy ? <div className="h-full rounded-2xl border border-border-whisper bg-bg-card p-3">{body}</div> : body}
        </main>
      </div>
    </div>
  );
};

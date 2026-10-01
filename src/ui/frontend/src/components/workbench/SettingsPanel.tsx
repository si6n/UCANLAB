import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Ear, Loader2, Plug, RefreshCw, Square, Usb } from 'lucide-react';
import {
  AdapterEntry,
  AuthState,
  BusInfoResult,
  ConnectionTestStatus,
  DesktopBridge,
  FlashPreconditions,
} from '../../services/bridge';
import { L, pick } from '../mechanic/text';
import { SettingsAttributionPanel } from './SettingsAttributionPanel';
import { BTN_GHOST, BTN_PRIMARY, BTN_QUIET, Card, CardHeader, Chip, Segmented, Tone, cx } from './ui';

/**
 * Ayarlar — rebuilt for the workbench (ENGINEER_WORKBENCH.md §4.6).
 *
 * Connection: the mechanic's listen-only test, for a vehicle type the
 * engineer picks, or a fixed bitrate. Both open the adapter listen-only and
 * never transmit. Licence and safety are read-only views of what Python
 * reports; nothing here is a claim the app does not enforce.
 */

type Section = 'connection' | 'licence' | 'safety' | 'sources';
type VehicleType = 'car' | 'truck' | 'boat' | 'construction';

const BITRATES = [125_000, 250_000, 500_000, 1_000_000] as const;

const VEHICLE_OPTIONS: Array<{ value: VehicleType; label: () => string; rates: string }> = [
  { value: 'car', label: () => L('Otomobil', 'Car'), rates: '500 / 250 kbps' },
  { value: 'truck', label: () => L('Kamyon, otobüs', 'Truck, bus'), rates: '250 / 500 kbps' },
  { value: 'boat', label: () => L('Tekne', 'Boat'), rates: '250 kbps' },
  { value: 'construction', label: () => L('İş makinesi', 'Construction'), rates: '250 kbps' },
];

const ERRORS: Record<string, () => string> = {
  ADAPTER_UNKNOWN: () => L('Adaptör listede yok. Listeyi yenileyip yeniden seçin.', 'Adapter not in the list. Rescan and pick it again.'),
  ESTOP_ENGAGED: () => L('Acil durdurma kilitliyken hat değiştirilmez.', 'The channel is not switched while the E-Stop is latched.'),
  TX_ARMED: () => L('Araca yazma açıkken hat değiştirilmez. Önce yazmayı kapatın.', 'Not while transmit is armed. Disarm first.'),
  TEST_RUNNING: () => L('Bir bağlantı testi zaten sürüyor.', 'A connection test is already running.'),
  INVALID_BITRATE: () => L('Geçersiz hız.', 'Invalid bitrate.'),
  INVALID_VEHICLE_TYPE: () => L('Geçersiz araç tipi.', 'Invalid vehicle type.'),
  CONNECT_FAILED: () =>
    L('Adaptör bu hızla açılamadı; önceki hat korunuyor. Sürücüyü ve kabloyu kontrol edin.', 'The adapter did not open at this bitrate; the previous bus is kept. Check the driver and cable.'),
};

const errorText = (code?: string) => ERRORS[code ?? '']?.() ?? L('İşlem tamamlanamadı.', 'The operation did not complete.');

const STEP_LABEL: Record<string, () => string> = {
  opening: () => L('Adaptör açılıyor (yalnız dinleme)', 'Opening the adapter (listen only)'),
  bitrate: () => L('Hız deneniyor', 'Trying the bitrate'),
  ecus: () => L('Kontrol üniteleri dinleniyor', 'Listening for control units'),
  done: () => L('Bitti', 'Done'),
};

const Row: React.FC<{ label: string; children: React.ReactNode; testId?: string }> = ({ label, children, testId }) => (
  <div className="flex items-start justify-between gap-4 py-2.5 text-[13px]" data-testid={testId}>
    <span className="text-text-mid">{label}</span>
    <span className="text-right text-text-hi">{children}</span>
  </div>
);

// ---------------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------------

const ConnectionSection: React.FC<{ busInfo: BusInfoResult | null; onBusChanged: () => void }> = ({ busInfo, onBusChanged }) => {
  const [adapters, setAdapters] = useState<AdapterEntry[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [vtype, setVtype] = useState<VehicleType>('car');
  const [bitrate, setBitrate] = useState<number>(500_000);
  const [test, setTest] = useState<ConnectionTestStatus | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  const stopPolling = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };
  useEffect(() => stopPolling, []);

  const scan = useCallback(async () => {
    setScanning(true);
    try {
      const found = (await DesktopBridge.adapterScan()).filter((a) => a.kind !== 'simulator');
      setAdapters(found);
      setSelected((cur) => (cur && found.some((a) => a.id === cur && a.usable) ? cur : found.find((a) => a.usable)?.id ?? null));
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    void scan();
  }, [scan]);

  const running = test?.state === 'running';

  const startTest = async () => {
    if (!selected) return;
    setMessage(null);
    const res = await DesktopBridge.workbenchConnectionTestStart(selected, vtype);
    if (!res.success) {
      setMessage({ ok: false, text: errorText(res.error_code) });
      return;
    }
    setTest({ success: true, state: 'running', step: 'opening' });
    const poll = async () => {
      const st = await DesktopBridge.connectionTestStatus();
      setTest(st);
      if (st.state === 'running') {
        timer.current = window.setTimeout(() => void poll(), 400);
        return;
      }
      const r = st.result;
      if (r) setMessage({ ok: r.usable, text: pick(r, 'message') });
      onBusChanged();
    };
    timer.current = window.setTimeout(() => void poll(), 400);
  };

  const cancelTest = async () => {
    await DesktopBridge.connectionTestCancel();
  };

  const manualConnect = async () => {
    if (!selected) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await DesktopBridge.workbenchBusConnect(selected, bitrate);
      setMessage(
        res.success
          ? { ok: true, text: L(`Bağlandı: ${Math.round(bitrate / 1000)} kbps, yalnız dinleme.`, `Connected: ${Math.round(bitrate / 1000)} kbps, listen only.`) }
          : { ok: false, text: errorText(res.error_code) },
      );
    } finally {
      setBusy(false);
      onBusChanged();
    }
  };

  const current = busInfo
    ? busInfo.simulated
      ? L('Simülatör', 'Simulator')
      : `${busInfo.interface ?? '?'} ${busInfo.channel ?? ''}${busInfo.bitrate ? ` · ${Math.round(busInfo.bitrate / 1000)} kbps` : ''}`
    : L('Bilinmiyor', 'Unknown');
  const result = test?.state === 'done' ? test.result : null;

  return (
    <div className="grid grid-cols-1 content-start gap-3 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        <Card testId="settings-adapters">
          <CardHeader title={L('Adaptör', 'Adapter')} hint={L('Bilgisayara takılı CAN adaptörleri. Liste hiçbir kanal açmaz.', 'CAN adapters plugged into this computer. Listing opens no channel.')}>
            <button type="button" className={BTN_QUIET} onClick={() => void scan()} disabled={scanning || running} data-testid="adapter-rescan">
              <RefreshCw className={cx('h-4 w-4', scanning && 'animate-spin')} />
              {L('Yenile', 'Rescan')}
            </button>
          </CardHeader>
          <div className="flex flex-col gap-2 p-5" role="radiogroup">
            {adapters === null && <p className="text-[13px] text-text-mid">{L('Aranıyor…', 'Scanning…')}</p>}
            {adapters?.length === 0 && (
              <p className="text-[13px] text-text-mid" data-testid="adapter-none">
                {L('Adaptör bulunamadı. Takıp sürücüsünü kurduktan sonra Yenile’ye basın.', 'No adapter found. Plug it in, install its driver and press Rescan.')}
              </p>
            )}
            {adapters?.map((a) => (
              <button
                key={a.id}
                type="button"
                role="radio"
                aria-checked={selected === a.id}
                disabled={!a.usable || running}
                onClick={() => setSelected(a.id)}
                data-testid={`adapter-${a.id}`}
                className={cx(
                  'flex items-start gap-3 rounded-xl border p-3 text-left transition-colors disabled:cursor-not-allowed',
                  selected === a.id ? 'border-accent bg-accent-soft' : 'border-border-strong hover:border-accent',
                  !a.usable && 'opacity-60 hover:border-border-strong',
                )}
              >
                <Usb className="mt-0.5 h-4 w-4 flex-none text-text-mid" />
                <span className="min-w-0 flex-1">
                  <span className="block text-[13.5px] font-semibold text-text-hi">{a.label}</span>
                  <span className="block font-mono text-[11.5px] text-text-mid">
                    {a.interface}
                    {a.channel ? ` · ${a.channel}` : ''}
                  </span>
                  {pick(a, 'message') && <span className="mt-1 block text-[12px] text-text-mid">{pick(a, 'message')}</span>}
                </span>
                <Chip tone={a.usable ? 'ok' : 'warn'}>{a.usable ? L('Hazır', 'Ready') : L('Kullanılamaz', 'Unavailable')}</Chip>
              </button>
            ))}
          </div>
        </Card>

        <Card testId="settings-listen-test">
          <CardHeader
            title={L('Dinleyerek bağlan', 'Connect by listening')}
            hint={L('Hızı araç tipinin olası değerleri arasından bulur, gelen trafiği ve kontrol ünitelerini sayar.', 'Finds the bitrate among the vehicle type’s candidates and counts the traffic and control units.')}
          />
          <div className="flex flex-col gap-4 p-5">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4" role="radiogroup">
              {VEHICLE_OPTIONS.map((v) => (
                <button
                  key={v.value}
                  type="button"
                  role="radio"
                  aria-checked={vtype === v.value}
                  disabled={running}
                  onClick={() => setVtype(v.value)}
                  data-testid={`vtype-${v.value}`}
                  className={cx('rounded-xl border p-3 text-left transition-colors', vtype === v.value ? 'border-accent bg-accent-soft' : 'border-border-strong hover:border-accent')}
                >
                  <span className="block text-[13px] font-semibold text-text-hi">{v.label()}</span>
                  <span className="block font-mono text-[11.5px] text-text-mid">{v.rates}</span>
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={BTN_PRIMARY} disabled={!selected || running} onClick={() => void startTest()} data-testid="listen-test-start">
                {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Ear className="h-4 w-4" />}
                {L('Dinleme testini başlat', 'Start the listening test')}
              </button>
              {running && (
                <button type="button" className={BTN_GHOST} onClick={() => void cancelTest()} data-testid="listen-test-cancel">
                  <Square className="h-4 w-4" />
                  {L('Durdur', 'Stop')}
                </button>
              )}
              {running && (
                <span className="text-[12.5px] text-text-mid" data-testid="listen-test-step">
                  {STEP_LABEL[test?.step ?? 'opening']?.()}
                  {test?.bitrate ? ` · ${Math.round(test.bitrate / 1000)} kbps` : ''}
                </span>
              )}
            </div>
            {result && (
              <div className="rounded-xl border border-border-whisper p-3 text-[12.5px]" data-testid="listen-test-result">
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-text-mid">
                  <span>
                    {L('Hız', 'Bitrate')}: <b className="text-text-hi">{result.bitrate ? `${Math.round(result.bitrate / 1000)} kbps` : '—'}</b>
                  </span>
                  <span>
                    {L('Çerçeve', 'Frames')}: <b className="text-text-hi">{result.frames}</b>
                  </span>
                  <span>
                    {L('Kontrol ünitesi', 'Control units')}: <b className="text-text-hi">{result.ecu_count}</b>
                  </span>
                </div>
                {result.expected.length > 0 && (
                  <ul className="mt-2 flex flex-wrap gap-1.5">
                    {result.expected.map((e) => (
                      <li key={e.pgn}>
                        <Chip tone={e.seen ? 'ok' : 'neutral'}>
                          {e.seen ? '✓' : '–'} {pick(e, 'name')}
                        </Chip>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </Card>

        <Card testId="settings-manual">
          <CardHeader
            title={L('Sabit hızla bağlan', 'Connect at a fixed bitrate')}
            hint={L('Hızı bildiğiniz ya da araç tipi listesinde olmayan hatlar için.', 'For buses whose bitrate you know or that no vehicle type covers.')}
          />
          <div className="flex flex-wrap items-center gap-3 p-5">
            <Segmented
              testId="manual-bitrate"
              value={String(bitrate)}
              options={BITRATES.map((b) => ({ value: String(b), label: `${b / 1000} kbps` }))}
              onChange={(v) => setBitrate(Number(v))}
            />
            <button type="button" className={BTN_GHOST} disabled={!selected || running || busy} onClick={() => void manualConnect()} data-testid="manual-connect">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
              {L('Bağlan', 'Connect')}
            </button>
          </div>
        </Card>

        {message && (
          <div
            role={message.ok ? 'status' : 'alert'}
            data-testid="settings-message"
            className={cx(
              'flex items-start gap-2 rounded-2xl border px-5 py-3 text-[13px]',
              message.ok ? 'border-ok-border bg-ok-soft text-ok' : 'border-warn-border bg-warn-soft text-warn',
            )}
          >
            {message.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none" /> : <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />}
            {message.text}
          </div>
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <Card testId="settings-current-bus">
          <CardHeader title={L('Şu an dinlenen hat', 'Bus being listened to')} />
          <div className="divide-y divide-border-whisper px-5 py-1">
            <Row label={L('Kaynak', 'Source')} testId="current-bus">
              {current}
            </Row>
            <Row label={L('Durum', 'State')}>
              <Chip tone={busInfo?.connected ? 'ok' : 'neutral'}>{busInfo?.connected ? L('Açık', 'Open') : L('Kapalı', 'Closed')}</Chip>
            </Row>
            <Row label={L('Mod', 'Mode')}>
              <Chip tone={busInfo?.listen_only === false ? 'warn' : 'ok'}>
                {busInfo?.listen_only === false ? L('Normal', 'Normal') : L('Yalnız dinleme', 'Listen only')}
              </Chip>
            </Row>
            <Row label={L('Hata çerçevesi (sürücü)', 'Error frames (driver)')}>{busInfo?.error_frames ?? 0}</Row>
          </div>
        </Card>
        <Card>
          <div className="flex gap-3 p-5 text-[12.5px] text-text-mid">
            <Ear className="mt-0.5 h-4 w-4 flex-none text-accent" />
            <p>
              {L(
                'Bağlantı her zaman yalnız dinleme modunda açılır; test ve bağlantı sırasında araca hiçbir çerçeve gönderilmez. Hat değiştiğinde araca yazma kapatılır, simülatör verisi ve analiz oturumu temizlenir.',
                'The connection always opens listen-only; nothing is sent to the vehicle during the test or the connection. Switching the bus disarms transmit and clears simulator data and the analysis session.',
              )}
            </p>
          </div>
        </Card>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Licence
// ---------------------------------------------------------------------------

const LICENCE_STATUS: Record<string, { tone: Tone; text: () => string }> = {
  active: { tone: 'ok', text: () => L('Etkin', 'Active') },
  missing: { tone: 'warn', text: () => L('Yok', 'Missing') },
  expired: { tone: 'danger', text: () => L('Süresi dolmuş', 'Expired') },
  clock_problem: { tone: 'danger', text: () => L('Saat sorunu', 'Clock problem') },
  invalid: { tone: 'danger', text: () => L('Geçersiz', 'Invalid') },
};

const ENTITLEMENTS: Array<{ key: 'mechanic' | 'engineer' | 'active_tests' | 'dtc_clear'; label: () => string }> = [
  { key: 'mechanic', label: () => L('Tamirci modu', 'Mechanic mode') },
  { key: 'engineer', label: () => L('Uzman masası', 'Engineer workbench') },
  { key: 'active_tests', label: () => L('Aktif testler', 'Active tests') },
  { key: 'dtc_clear', label: () => L('Arıza kodu silme', 'Clear fault codes') },
];

const LicenceSection: React.FC<{ onSwitchToMechanic: (() => void) | null }> = ({ onSwitchToMechanic }) => {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [note, setNote] = useState('');

  useEffect(() => {
    void DesktopBridge.authGetState(false).then(setAuth);
  }, []);

  const refresh = async () => {
    setRefreshing(true);
    setNote('');
    try {
      const res = await DesktopBridge.authGetState(true);
      setAuth(res);
      setNote(
        res?.refreshed
          ? L('Lisans sunucudan yenilendi.', 'Licence refreshed from the server.')
          : L('Sunucuya ulaşılamadı; kayıtlı lisans geçerli kaldı.', 'The server was not reached; the stored licence still applies.'),
      );
    } finally {
      setRefreshing(false);
    }
  };

  const lic = auth?.license;
  const st = LICENCE_STATUS[lic?.status ?? ''] ?? { tone: 'neutral' as Tone, text: () => L('Bilinmiyor', 'Unknown') };

  return (
    <div className="grid grid-cols-1 content-start gap-3 xl:grid-cols-2">
      <Card testId="settings-licence">
        <CardHeader title={L('Lisans', 'Licence')} hint={L('Bu bilgisayarda kayıtlı, imzası doğrulanmış lisans.', 'The signed licence stored on this computer.')}>
          <button type="button" className={BTN_QUIET} onClick={() => void refresh()} disabled={refreshing} data-testid="licence-refresh">
            <RefreshCw className={cx('h-4 w-4', refreshing && 'animate-spin')} />
            {L('Şimdi yenile', 'Refresh now')}
          </button>
        </CardHeader>
        <div className="divide-y divide-border-whisper px-5 py-1">
          <Row label={L('Durum', 'Status')} testId="licence-status">
            <Chip tone={st.tone}>{st.text()}</Chip>
          </Row>
          <Row label={L('Paket', 'Plan')}>{lic?.entitlements.tier ? lic.entitlements.tier.toUpperCase() : '—'}</Row>
          <Row label={L('Çevrimdışı kalan süre', 'Offline time left')}>{lic ? L(`${lic.offline_days_left} gün`, `${lic.offline_days_left} days`) : '—'}</Row>
          <Row label={L('Oturum', 'Session')}>{auth?.signedIn ? L('Açık', 'Signed in') : L('Kapalı', 'Signed out')}</Row>
        </div>
        {(note || pick(lic, 'message')) && (
          <p className="border-t border-border-whisper px-5 py-3 text-[12.5px] text-text-mid" data-testid="licence-note">
            {note || pick(lic, 'message')}
          </p>
        )}
      </Card>
      <div className="flex flex-col gap-3">
        <Card testId="settings-entitlements">
          <CardHeader title={L('Bu lisansla açık olanlar', 'What this licence allows')} />
          <ul className="divide-y divide-border-whisper px-5 py-1">
            {ENTITLEMENTS.map((e) => (
              <li key={e.key} className="flex items-center justify-between py-2.5 text-[13px]">
                <span className="text-text-body">{e.label()}</span>
                <Chip tone={lic?.entitlements[e.key] ? 'ok' : 'neutral'}>{lic?.entitlements[e.key] ? L('Açık', 'Included') : L('Kapalı', 'Not included')}</Chip>
              </li>
            ))}
          </ul>
        </Card>
        {onSwitchToMechanic && (
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3 p-5">
              <div>
                <div className="text-[13.5px] font-semibold text-text-hi">{L('Kullanım modu: Uzman', 'Usage mode: Engineer')}</div>
                <div className="text-[12.5px] text-text-mid">{L('Seçim bu bilgisayarda hatırlanır.', 'The choice is remembered on this computer.')}</div>
              </div>
              <button type="button" className={BTN_GHOST} onClick={onSwitchToMechanic} data-testid="settings-to-mechanic">
                {L('Tamirci moduna geç', 'Switch to mechanic mode')}
              </button>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Safety (read-only, live)
// ---------------------------------------------------------------------------

const SUPERVISOR: Record<string, { tone: Tone; text: () => string }> = {
  STARTUP: { tone: 'ok', text: () => L('Açılış — yalnız dinleme', 'Starting — listen only') },
  SAFE: { tone: 'ok', text: () => L('Güvenli — yalnız dinleme', 'Safe — listen only') },
  PASSIVE: { tone: 'ok', text: () => L('Pasif — yalnız dinleme', 'Passive — listen only') },
  ARMED_TX: { tone: 'warn', text: () => L('Araca yazma açık', 'Transmit armed') },
  ACTIVE: { tone: 'warn', text: () => L('Araca yazılıyor', 'Transmitting') },
  FAULT: { tone: 'danger', text: () => L('Kilitli (E-Stop / hata)', 'Locked (E-Stop / fault)') },
};

const SPEED: Record<string, { tone: Tone; text: () => string }> = {
  ok: { tone: 'ok', text: () => L('Araç duruyor (0 km/s)', 'Vehicle stationary (0 km/h)') },
  moving: { tone: 'danger', text: () => L('Araç hareket ediyor', 'Vehicle moving') },
  stale: { tone: 'neutral', text: () => L('Hız bilinmiyor (güvenilir kaynak yok)', 'Speed unknown (no trusted source)') },
};

const SafetySection: React.FC<{ safety: string | null }> = ({ safety }) => {
  const [pre, setPre] = useState<FlashPreconditions | null>(null);
  useEffect(() => {
    const load = () => void DesktopBridge.flashPreconditions().then(setPre);
    load();
    const t = window.setInterval(load, 1000);
    return () => window.clearInterval(t);
  }, []);
  const sup = SUPERVISOR[safety ?? ''] ?? { tone: 'neutral' as Tone, text: () => L('Bilinmiyor', 'Unknown') };
  const speed = SPEED[pre?.speed_state ?? ''] ?? SPEED.stale;

  return (
    <div className="grid grid-cols-1 content-start gap-3 xl:grid-cols-2">
      <Card testId="settings-safety">
        <CardHeader title={L('Şu anki durum', 'Current state')} hint={L('Python güvenlik katmanından canlı okunur.', 'Read live from the Python safety layer.')} />
        <div className="divide-y divide-border-whisper px-5 py-1">
          <Row label={L('Gönderim denetçisi', 'Transmit supervisor')} testId="safety-supervisor">
            <Chip tone={sup.tone}>{sup.text()}</Chip>
          </Row>
          <Row label={L('Acil durdurma', 'E-Stop')}>
            <Chip tone={pre?.estop ? 'danger' : 'ok'}>{pre?.estop ? L('Kilitli', 'Latched') : L('Devrede değil', 'Not engaged')}</Chip>
          </Row>
          <Row label={L('Hız kilidi', 'Speed interlock')}>
            <Chip tone={speed.tone}>{speed.text()}</Chip>
          </Row>
        </div>
      </Card>
      <Card>
        <CardHeader title={L('Uygulanan kurallar', 'Rules in force')} hint={L('Ayar değildir; değiştirilemez.', 'Not settings; they cannot be changed.')} />
        <ul className="flex list-disc flex-col gap-2 py-4 pl-10 pr-5 text-[13px] text-text-body">
          <li>{L('Adaptör her zaman yalnız dinleme modunda açılır.', 'The adapter always opens listen-only.')}</li>
          <li>
            {L(
              'Araca yazan her işlem (ECU programlama, onaylı teşhis işlemleri) tek kullanımlık bir onay, yerel onay penceresi, acil durdurma ve hız kilidinden geçer.',
              'Every operation that writes to the vehicle (ECU programming, approved diagnostic actions) needs a single-use confirmation, the native confirmation dialog, the E-Stop and the speed interlock.',
            )}
          </li>
          <li>{L('Hız kilidini yalnız güvenilir kaynaktan gelen gerçek hız açar; simüle hız açamaz.', 'Only a real speed from a trusted source satisfies the interlock; a simulated speed cannot.')}</li>
          <li>{L('Asistan ölçüm uydurmaz ve hatta yazamaz.', 'The assistant does not invent measurements and cannot write to the bus.')}</li>
          <li>{L('Acil durdurma kilidini yalnız kriptografik sıfırlama açar.', 'Only a cryptographic reset clears the E-Stop latch.')}</li>
        </ul>
      </Card>
    </div>
  );
};

// ---------------------------------------------------------------------------

export const SettingsPanel: React.FC<{
  busInfo: BusInfoResult | null;
  safety: string | null;
  onBusChanged: () => void;
  onSwitchToMechanic: (() => void) | null;
}> = ({ busInfo, safety, onBusChanged, onSwitchToMechanic }) => {
  const [section, setSection] = useState<Section>('connection');
  return (
    <div className="flex h-full min-h-0 flex-col gap-3" data-testid="settings-view">
      <div>
        <Segmented
          testId="settings-tab"
          value={section}
          onChange={setSection}
          options={[
            { value: 'connection', label: L('Bağlantı', 'Connection') },
            { value: 'licence', label: L('Lisans', 'Licence') },
            { value: 'safety', label: L('Güvenlik', 'Safety') },
            { value: 'sources', label: L('Veri lisansları', 'Data licences') },
          ]}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {section === 'connection' && <ConnectionSection busInfo={busInfo} onBusChanged={onBusChanged} />}
        {section === 'licence' && <LicenceSection onSwitchToMechanic={onSwitchToMechanic} />}
        {section === 'safety' && <SafetySection safety={safety} />}
        {section === 'sources' && <SettingsAttributionPanel />}
      </div>
    </div>
  );
};

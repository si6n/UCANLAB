import React, { useState, useEffect, useCallback } from 'react';
import {
  Cpu,
  ShieldCheck,
  HardDrive,
  Key,
  CheckCircle2,
  AlertCircle,
  Save,
  ExternalLink,
  Loader2,
  Laptop,
  Copy,
  Check,
  RotateCcw,
  Undo2,
  User,
  LogOut,
  RefreshCw,
  X,
  Globe,
  Gauge,
  Server,
  Fingerprint,
  Database,
  Archive,
  Lock,
} from 'lucide-react';
import { DesktopBridge, CloudStatus } from '../../services/bridge';
import { SettingsAttributionPanel } from './SettingsAttributionPanel';
import {
  SectionHeader,
  SettingsGroup,
  SettingRow,
  ReadOnlyValue,
  StatusPill,
  InfoNote,
  inputClass,
  btnGhost,
  btnPrimary,
  btnDanger,
} from './SettingsPrimitives';

interface SettingsViewProps {
  channel: string;
  baudRate: string;
  onSave: (settings: { channel: string; baudRate: string; cloudBaseUrl?: string }) => void;
}

type SectionId = 'hardware' | 'license' | 'safety' | 'storage' | 'attribution';

interface SectionDef {
  id: SectionId;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}

const SECTIONS: SectionDef[] = [
  {
    id: 'hardware',
    label: 'Donanım',
    description: 'Kanal arayüzü, baud hızı ve sunucu bağlantısı',
    icon: Cpu,
  },
  {
    id: 'license',
    label: 'Bulut & Lisans',
    description: 'Hesap, abonelik ve cihaz eşleştirme',
    icon: Key,
  },
  {
    id: 'safety',
    label: 'Güvenlik',
    description: 'ISO 26262 ASIL-D çekirdek politikası',
    icon: ShieldCheck,
  },
  {
    id: 'storage',
    label: 'Depolama',
    description: 'Bellek mimarisi, sıkıştırma ve arşiv rotasyonu',
    icon: HardDrive,
  },
  { id: 'attribution', label: 'Açık Kaynak', description: 'Veri kaynakları ve CC-BY-4.0 atıfları', icon: Globe },
];

const BAUD_RATES = [
  { value: '125 kbps', label: '125k' },
  { value: '250 kbps', label: '250k' },
  { value: '500 kbps', label: '500k' },
  { value: '1000 kbps (1 Mbps)', label: '1M' },
];

const CHANNEL_OPTIONS = [
  { id: 'vcan0', name: 'vcan0 — Sanal veriyolu' },
  { id: 'can0', name: 'can0 — Linux SocketCAN' },
  { id: 'PCAN_USBBUS1', name: 'PCAN-USB — Peak Kanal 1' },
  { id: 'kvaser_0', name: 'Kvaser Leaf Light v2' },
  { id: 'rp1210:DLA', name: 'RP1210 DLA — Ağır vasıta' },
];

const DEFAULT_CLOUD_URL = 'https://ucan-cloud.si6n.io';
const ACTIVE_SECTION_KEY = 'ucanlab.settings.section';

function readStoredSection(): SectionId {
  try {
    const raw = localStorage.getItem(ACTIVE_SECTION_KEY);
    if (raw && SECTIONS.some((s) => s.id === raw)) return raw as SectionId;
  } catch {
    /* offline / private mode */
  }
  return 'hardware';
}

export const SettingsView: React.FC<SettingsViewProps> = ({
  channel: initChannel,
  baudRate: initBaud,
  onSave,
}) => {
  const [channel, setChannel] = useState(initChannel);
  const [baudRate, setBaudRate] = useState(initBaud);
  const [cloudUrl, setCloudUrl] = useState(() => {
    return localStorage.getItem('cloud_base_url') || DEFAULT_CLOUD_URL;
  });
  const [cloudStatus, setCloudStatus] = useState<CloudStatus | null>(null);
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [activeSection, setActiveSection] = useState<
    'hardware' | 'license' | 'safety' | 'storage' | 'attribution'
  >('hardware');

  const [isWaitingWebLogin, setIsWaitingWebLogin] = useState(false);
  const [webLoginUrl, setWebLoginUrl] = useState<string | null>(null);
  const pollTimerRef = React.useRef<number | null>(null);

  const [isActionLoading, setIsActionLoading] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [copiedHwid, setCopiedHwid] = useState(false);

  // Restore the section the operator last worked in (UX-3).
  useEffect(() => {
    setActiveSection(readStoredSection());
  }, []);

  // Dirty tracking: compare against the last persisted snapshot.
  const savedSnapshot = React.useMemo(
    () => ({
      channel: initChannel,
      baudRate: initBaud,
      cloudUrl: cloudStatus?.baseUrl || DEFAULT_CLOUD_URL,
    }),
    [initChannel, initBaud, cloudStatus?.baseUrl],
  );

  const isDirty =
    channel !== savedSnapshot.channel ||
    baudRate !== savedSnapshot.baudRate ||
    cloudUrl.trim() !== savedSnapshot.cloudUrl.trim();

  useEffect(() => {
    setChannel(initChannel);
    setBaudRate(initBaud);
  }, [initChannel, initBaud]);

  const fetchCloudStatus = useCallback(async () => {
    try {
      const status = await DesktopBridge.cloudGetStatus();
      setCloudStatus(status);
      if (status.baseUrl) setCloudUrl(status.baseUrl);
    } catch {
      // Offline fallback
    }
  }, []);

  useEffect(() => {
    fetchCloudStatus();
  }, [fetchCloudStatus]);

  const portalUrl = 'https://ucanlab.org';

  const handleRegisterDevice = async () => {
    setIsActionLoading(true);
    setActionFeedback(null);
    try {
      const res = await DesktopBridge.cloudRegisterDevice('Desktop Diagnostic Tool');
      if (res.success) {
        setActionFeedback({
          type: 'success',
          text: `Cihaz eşleştirildi (${res.deviceId?.slice(0, 8)}...)`,
        });
        await fetchCloudStatus();
      } else {
        setActionFeedback({ type: 'error', text: res.error || 'Cihaz kaydı başarısız' });
      }
    } catch (err: any) {
      setActionFeedback({ type: 'error', text: err.message || 'Kayıt gerçekleştirilemedi' });
    } finally {
      setIsActionLoading(false);
    }
  };

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  const handleStartWebLogin = async () => {
    setIsActionLoading(true);
    setActionFeedback(null);
    try {
      const res = await DesktopBridge.cloudStartWebLogin();
      if (!res.success) {
        setActionFeedback({
          type: 'error',
          text: res.error || 'Web girişi başlatılamadı.',
        });
        setIsActionLoading(false);
        return;
      }

      setIsWaitingWebLogin(true);
      setWebLoginUrl(res.login_url || null);
      setActionFeedback({
        type: 'success',
        text: 'Tarayıcıda UCanLab açıldı. Giriş yapmanız bekleniyor...',
      });

      stopPolling();
      pollTimerRef.current = window.setInterval(async () => {
        try {
          const statusRes = await DesktopBridge.cloudCheckWebLoginStatus();
          if (statusRes.status === 'completed') {
            stopPolling();
            setIsWaitingWebLogin(false);
            setWebLoginUrl(null);
            setActionFeedback({
              type: 'success',
              text: `Giriş başarılı! ${statusRes.user?.email ? `(${statusRes.user.email})` : ''}`,
            });
            await fetchCloudStatus();
          } else if (statusRes.status === 'error') {
            stopPolling();
            setIsWaitingWebLogin(false);
            setWebLoginUrl(null);
            setActionFeedback({
              type: 'error',
              text: statusRes.error || 'Web girişi başarısız oldu.',
            });
          } else if (statusRes.status === 'cancelled') {
            stopPolling();
            setIsWaitingWebLogin(false);
            setWebLoginUrl(null);
          }
        } catch {
          // Ignore transient poll errors
        }
      }, 1500);
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: err.message || 'Web girişi başlatılırken hata oluştu.',
      });
      setIsWaitingWebLogin(false);
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleCancelWebLogin = async () => {
    stopPolling();
    setIsWaitingWebLogin(false);
    setWebLoginUrl(null);
    try {
      await DesktopBridge.cloudCancelWebLogin();
    } catch {
      // Best effort
    }
    setActionFeedback({
      type: 'success',
      text: 'Web girişi iptal edildi.',
    });
  };

  const handleLogout = async () => {
    setIsActionLoading(true);
    setActionFeedback(null);
    try {
      const res = await DesktopBridge.cloudLogout();
      if (res && res.success) {
        setActionFeedback({ type: 'success', text: 'Oturum kapatıldı' });
        await fetchCloudStatus();
      } else {
        setActionFeedback({ type: 'error', text: res?.error || 'Çıkış yapılamadı' });
      }
    } catch (err: any) {
      setActionFeedback({ type: 'error', text: err.message || 'Çıkış hatası' });
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleCopyHwid = () => {
    if (cloudStatus?.hwid) {
      navigator.clipboard.writeText(cloudStatus.hwid);
      setCopiedHwid(true);
      setTimeout(() => setCopiedHwid(false), 2000);
    }
  };

  const handleSave = async () => {
    const requested = cloudUrl.trim();
    setIsSaving(true);
    try {
      const res: any = await DesktopBridge.cloudSaveConfig(requested);
      if (res && res.success === false) {
        setActionFeedback({ type: 'error', text: res.error || 'Ayar kaydedilemedi' });
        return;
      }
      localStorage.setItem('cloud_base_url', requested);
      onSave({ channel, baudRate, cloudBaseUrl: requested });
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 2000);
      setActionFeedback({ type: 'success', text: 'Ayarlar başarıyla kaydedildi' });
    } catch (err: any) {
      setActionFeedback({ type: 'error', text: err?.message || 'Kayıt hatası' });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDiscard = () => {
    setChannel(savedSnapshot.channel);
    setBaudRate(savedSnapshot.baudRate);
    setCloudUrl(savedSnapshot.cloudUrl);
    setActionFeedback({ type: 'success', text: 'Kaydedilmemiş değişiklikler geri alındı' });
  };

  const selectSection = useCallback((id: SectionId) => {
    setActiveSection(id);
    setActionFeedback(null);
    try {
      localStorage.setItem(ACTIVE_SECTION_KEY, id);
    } catch {
      /* offline / private mode */
    }
  }, []);

  const activeDef = SECTIONS.find((s) => s.id === activeSection) ?? SECTIONS[0];

  const onTabsKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (
      event.key !== 'ArrowLeft' &&
      event.key !== 'ArrowRight' &&
      event.key !== 'Home' &&
      event.key !== 'End'
    ) {
      return;
    }
    event.preventDefault();
    const list = SECTIONS;
    const current = list.findIndex((s) => s.id === activeSection);
    let next: number;
    if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = list.length - 1;
    else if (event.key === 'ArrowRight') next = (current + 1) % list.length;
    else next = (current - 1 + list.length) % list.length;
    selectSection(list[next].id);
  };

  const baudShort = baudRate.startsWith('1000') ? '1M' : baudRate.replace(' kbps', 'k');

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-transparent font-sans text-text-body select-none">
      {/* ── Header: identity + live config summary + global actions ── */}
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border-whisper pb-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h1 className="text-[15px] font-bold tracking-tight text-text-hi">Ayarlar</h1>
            <span className="font-mono text-[11px] text-text-mid">
              {channel} · {baudShort}
            </span>
            {isDirty && (
              <span className="inline-flex items-center gap-1 rounded-circle bg-warn-soft px-2 py-0.5 font-mono text-[10px] font-semibold text-warn">
                <span className="h-1.5 w-1.5 animate-pulse-subtle rounded-full bg-warn" aria-hidden="true" />
                kaydedilmedi
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[11.5px] leading-relaxed text-text-low">
            Veriyolu, bulut hesabı, güvenlik politikası ve depolama yapılandırması
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {actionFeedback && (
            <div
              role="status"
              aria-live="polite"
              data-testid="settings-feedback"
              className={`flex max-w-[300px] items-center gap-1.5 rounded-btn border px-2.5 py-1 text-[11px] font-medium ${
                actionFeedback.type === 'success'
                  ? 'border-ok-border bg-ok-soft text-ok'
                  : 'border-danger-border bg-delbg text-del'
              }`}
            >
              {actionFeedback.type === 'success' ? (
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              ) : (
                <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              )}
              <span className="truncate">{actionFeedback.text}</span>
            </div>
          )}

          {isDirty && (
            <button type="button" onClick={handleDiscard} className={btnGhost} title="Değişiklikleri iptal et">
              <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Geri al</span>
            </button>
          )}

          <button
            type="button"
            onClick={handleSave}
            disabled={!isDirty || isSaving}
            className={btnPrimary}
            title="Ayarları kaydet"
          >
            {savedSuccess ? (
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            ) : isSaving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            <span>{savedSuccess ? 'Kaydedildi' : isSaving ? 'Kaydediliyor' : 'Kaydet'}</span>
          </button>
        </div>
      </header>

      {/* ── Navigation: underline tabs, keyboard rovable ── */}
      <div
        role="tablist"
        aria-label="Ayar bölümleri"
        onKeyDown={onTabsKeyDown}
        className="mt-3 flex shrink-0 items-center gap-0.5 overflow-x-auto border-b border-border-whisper"
      >
        {SECTIONS.map((sec) => {
          const Icon = sec.icon;
          const isCurrent = activeSection === sec.id;
          return (
            <button
              key={sec.id}
              type="button"
              role="tab"
              id={`settings-tab-${sec.id}`}
              aria-selected={isCurrent}
              aria-controls="settings-panel"
              tabIndex={isCurrent ? 0 : -1}
              onClick={() => selectSection(sec.id)}
              title={sec.description}
              className={`-mb-px flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-[12px] transition-colors cursor-pointer ${
                isCurrent
                  ? 'border-accent font-semibold text-text-hi'
                  : 'border-transparent font-medium text-text-mid hover:text-text-hi'
              }`}
            >
              <Icon className={`h-3.5 w-3.5 ${isCurrent ? 'text-accent' : 'text-text-low'}`} aria-hidden="true" />
              <span>{sec.label}</span>
            </button>
          );
        })}
      </div>

      {/* ── Content: single-column grouped lists on the glass canvas ── */}
      <div
        id="settings-panel"
        role="tabpanel"
        aria-labelledby={`settings-tab-${activeDef.id}`}
        tabIndex={-1}
        className="min-h-0 flex-1 overflow-y-auto pt-5"
      >
        <div className="mx-auto max-w-2xl space-y-7 pb-10">
          {/* ================================================ */}
          {/* Donanım */}
          {/* ================================================ */}
          {activeSection === 'hardware' && (
            <div className="space-y-6">
              <SectionHeader
                title="Donanım"
                description="Veriyolunun fiziksel veya sanal arayüzü, bit hızı ve bulut sunucu bağlantısı buradan yapılandırılır."
              />

              <SettingsGroup label="Veriyolu" icon={<Gauge className="h-3 w-3" />}>
                <SettingRow
                  label="Kanal"
                  hint="Bağlanacak sürücü: vcan, SocketCAN, PCAN, Kvaser veya RP1210."
                  htmlFor="hw-channel-select"
                >
                  <select
                    id="hw-channel-select"
                    value={channel}
                    onChange={(e) => setChannel(e.target.value)}
                    aria-label="Kanal arayüzü"
                    className="h-8 w-full rounded-btn border border-border-strong bg-bg-popover px-2.5 font-mono text-[11.5px] text-text-hi transition-colors focus:border-accent focus:outline-none sm:w-64"
                  >
                    {CHANNEL_OPTIONS.map((opt) => (
                      <option key={opt.id} value={opt.id} className="bg-bg-popover text-text-hi">
                        {opt.name}
                      </option>
                    ))}
                  </select>
                </SettingRow>

                <SettingRow label="Baud hızı" hint="Bit/saniye — araç ECU yapılandırmasıyla eşleşmelidir.">
                  <div role="group" aria-label="Baud hızı" className="inline-flex rounded-btn border border-border-whisper bg-bg-panel p-0.5">
                    {BAUD_RATES.map((b) => {
                      const selected = baudRate === b.value;
                      return (
                        <button
                          key={b.value}
                          type="button"
                          aria-pressed={selected}
                          onClick={() => setBaudRate(b.value)}
                          className={`rounded-tag px-2.5 py-1 font-mono text-[11px] transition-colors cursor-pointer ${
                            selected
                              ? 'bg-bg-popover font-semibold text-text-hi shadow-card-subtle'
                              : 'text-text-mid hover:text-text-hi'
                          }`}
                        >
                          {b.label}
                        </button>
                      );
                    })}
                  </div>
                </SettingRow>
              </SettingsGroup>

              <SettingsGroup label="Sunucu bağlantısı" icon={<Server className="h-3 w-3" />}>
                <SettingRow
                  stacked
                  label="UCanLab sunucu adresi"
                  hint="Yalnızca izin listesindeki alan adları kabul edilir; diğerleri arka uç tarafından reddedilir."
                >
                  <div className="flex items-center gap-1.5">
                    <input
                      type="url"
                      value={cloudUrl}
                      onChange={(e) => setCloudUrl(e.target.value)}
                      aria-label="Bulut sunucu adresi"
                      spellCheck={false}
                      placeholder="https://..."
                      className={inputClass}
                    />
                    <button
                      type="button"
                      onClick={() => setCloudUrl(DEFAULT_CLOUD_URL)}
                      title="Varsayılana dön"
                      aria-label="Varsayılan sunucu adresine dön"
                      className={btnGhost}
                    >
                      <RotateCcw className="h-3 w-3" aria-hidden="true" />
                      <span>Sıfırla</span>
                    </button>
                  </div>
                </SettingRow>
              </SettingsGroup>
            </div>
          )}

          {/* ================================================ */}
          {/* Bulut & Lisans */}
          {/* ================================================ */}
          {activeSection === 'license' && (
            <div className="space-y-6">
              <SectionHeader
                title="Bulut & Lisans"
                description="UCanLab hesabınız, abonelik katmanı ve bu kurulumun donanım imzası."
                actions={
                  cloudStatus?.hasSessionToken ? (
                    <button type="button" onClick={fetchCloudStatus} disabled={isActionLoading} className={btnGhost}>
                      <RefreshCw className={`h-3 w-3 ${isActionLoading ? 'animate-spin' : ''}`} aria-hidden="true" />
                      <span>Yenile</span>
                    </button>
                  ) : undefined
                }
              />

              <SettingsGroup label="Hesap" icon={<User className="h-3 w-3" />}>
                <SettingRow
                  label={
                    cloudStatus?.user?.name ||
                    (cloudStatus?.user?.email ? cloudStatus.user.email.split('@')[0] : 'UCanLab Kullanıcısı')
                  }
                  hint={cloudStatus?.user?.email || (cloudStatus?.hasSessionToken ? 'Oturum açık' : 'Giriş yapılmadı')}
                  leading={
                    <div className="flex h-9 w-9 items-center justify-center rounded-circle bg-accent-soft text-accent">
                      <User className="h-4 w-4" aria-hidden="true" />
                    </div>
                  }
                >
                  {cloudStatus?.hasSessionToken ? (
                    <button type="button" onClick={handleLogout} disabled={isActionLoading} className={btnDanger}>
                      <LogOut className="h-3 w-3" aria-hidden="true" />
                      <span>Çıkış yap</span>
                    </button>
                  ) : isWaitingWebLogin ? (
                    <button type="button" onClick={handleCancelWebLogin} disabled={isActionLoading} className={btnGhost}>
                      <X className="h-3 w-3" aria-hidden="true" />
                      <span>İptal et</span>
                    </button>
                  ) : (
                    <button type="button" onClick={handleStartWebLogin} disabled={isActionLoading} className={btnPrimary}>
                      {isActionLoading ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                      ) : (
                        <Globe className="h-3 w-3" aria-hidden="true" />
                      )}
                      <span>Web ile giriş yap</span>
                    </button>
                  )}
                </SettingRow>

                {cloudStatus?.user?.organization && (
                  <SettingRow label="Kurum" hint="Hesabınıza bağlı organizasyon.">
                    <span className="text-[11.5px] font-medium text-text-hi">{cloudStatus.user.organization}</span>
                  </SettingRow>
                )}

                <SettingRow label="Abonelik" hint="Gelişmiş bulut ve OEM modülleri için abonelik gerekir.">
                  {cloudStatus?.subscription?.isActive ? (
                    <StatusPill
                      label={`AKTİF · ${cloudStatus.subscription.tier?.toUpperCase() || 'PRO'}`}
                      tone="ok"
                    />
                  ) : cloudStatus?.hasSessionToken ? (
                    <StatusPill label="TOPLULUK" tone="warn" />
                  ) : (
                    <StatusPill label="ÜCRETSİZ" tone="neutral" />
                  )}
                </SettingRow>

                {cloudStatus?.subscription?.isActive && cloudStatus.subscription.expiresAt && (
                  <SettingRow label="Bitiş tarihi" hint="Aboneliğin geçerlilik süresi.">
                    <span className="font-mono text-[11.5px] text-text-hi">
                      {new Date(cloudStatus.subscription.expiresAt).toLocaleDateString('tr-TR')}
                    </span>
                  </SettingRow>
                )}

                {!cloudStatus?.subscription?.isActive && (
                  <div className="flex items-center justify-between gap-3 px-4 py-3 text-[11.5px] text-text-low">
                    <span className="min-w-0">
                      {cloudStatus?.hasSessionToken
                        ? 'Hesabınızda aktif abonelik bulunamadı; temel araçlar ücretsizdir.'
                        : 'ucanlab.org üzerinden giriş yaparak aboneliğinizi eşitleyin.'}
                    </span>
                    <a
                      href={portalUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex shrink-0 items-center gap-1 font-medium text-accent hover:underline"
                    >
                      <span>ucanlab.org</span>
                      <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </a>
                  </div>
                )}
              </SettingsGroup>

              {isWaitingWebLogin && (
                <InfoNote>
                  <p className="font-medium text-text-hi">Tarayıcıda UCanLab açıldı, giriş bekleniyor…</p>
                  <p className="mt-0.5 text-text-mid">
                    Web sitesinde oturum açtığınızda uygulama otomatik bağlanır ve aboneliğiniz eşitlenir.
                  </p>
                  {webLoginUrl && (
                    <a
                      href={webLoginUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-1.5 inline-flex items-center gap-1 font-medium text-accent hover:underline"
                    >
                      <span>Tarayıcı otomatik açılmadıysa buraya tıklayın</span>
                      <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </a>
                  )}
                </InfoNote>
              )}

              <SettingsGroup label="Cihaz" icon={<Fingerprint className="h-3 w-3" />}>
                <SettingRow
                  label="Donanım imzası (HWID)"
                  hint="Lisans doğrulamasında ve cihaz kilitlerinde kullanılır."
                >
                  <code className="min-w-0 truncate rounded-tag border border-border-whisper bg-bg-panel px-2 py-1 font-mono text-[11px] text-text-mid">
                    {cloudStatus?.hwid
                      ? `${cloudStatus.hwid.slice(0, 16)}…${cloudStatus.hwid.slice(-6)}`
                      : 'Hesaplanıyor…'}
                  </code>
                  <button type="button" onClick={handleCopyHwid} className={btnGhost} title="HWID'yi kopyala">
                    {copiedHwid ? (
                      <Check className="h-3 w-3 text-ok" aria-hidden="true" />
                    ) : (
                      <Copy className="h-3 w-3" aria-hidden="true" />
                    )}
                    <span>{copiedHwid ? 'Kopyalandı' : 'Kopyala'}</span>
                  </button>
                </SettingRow>

                <SettingRow label="Cihaz eşleştirme" hint="Bulut ile cihaz kayıt ve yetki senkronizasyonu.">
                  <StatusPill
                    label={cloudStatus?.hasDeviceToken ? 'EŞLEŞTİRİLDİ' : 'KAYITSIZ'}
                    tone={cloudStatus?.hasDeviceToken ? 'ok' : 'neutral'}
                  />
                  <button type="button" onClick={handleRegisterDevice} disabled={isActionLoading} className={btnGhost}>
                    {isActionLoading ? (
                      <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                    ) : (
                      <Laptop className="h-3 w-3" aria-hidden="true" />
                    )}
                    <span>{cloudStatus?.hasDeviceToken ? 'Yenile' : 'Kaydet'}</span>
                  </button>
                </SettingRow>
              </SettingsGroup>
            </div>
          )}

          {/* ================================================ */}
          {/* Güvenlik */}
          {/* ================================================ */}
          {activeSection === 'safety' && (
            <div className="space-y-6">
              <SectionHeader
                title="Güvenlik Politikası"
                description="ISO 26262 ASIL-D çekirdek kısıtları. Değerler derleme zamanında sabitlenmiştir; değiştirilmesi ASIL-D doğrulamasını geçersiz kılar."
                actions={<StatusPill label="SALT OKUNUR" tone="accent" />}
              />

              <InfoNote>
                Bu ayarlar çalışma zamanında değiştirilemez. Tek iletim denetim noktası (<span className="font-mono">TxSafetyGateway</span>)
                ve fail-closed durum makinesi, gövdenin güvenlik mimarisinin parçasıdır.
              </InfoNote>

              <SettingsGroup label="Çekirdek kısıtlar" icon={<ShieldCheck className="h-3 w-3" />}>
                <SettingRow label="Watchdog zaman aşımı" hint="Monotonik saat tabanlı aşım ve düşme eşiği.">
                  <ReadOnlyValue value="800 ms · monotonik" tone="ok" />
                </SettingRow>
                <SettingRow label="TxSafetyGateway" hint="Tüm giden kareler için 6 aşamalı doğrulama zinciri.">
                  <ReadOnlyValue value="6 aşama · ASIL-D" tone="accent" />
                </SettingRow>
                <SettingRow label="CCVS hız kilidi" hint="Fiziksel hız eşiğin üzerindeyken kritik yazma yapılamaz.">
                  <ReadOnlyValue value="Fail-closed · >0 km/h" tone="ok" />
                </SettingRow>
                <SettingRow label="E-Stop sıfırlama" hint="Acil durdurma reset jetonunun kriptografik doğrulaması.">
                  <ReadOnlyValue value="HMAC-SHA256 jetonu" />
                </SettingRow>
              </SettingsGroup>

              <SettingsGroup label="Erişim ilkeleri" icon={<Lock className="h-3 w-3" />}>
                <SettingRow label="Render köprüsü" hint="Arayüz katmanı E-Stop jetonu üretemez veya temizleyemez.">
                  <ReadOnlyValue value="Salt okunur köprü" />
                </SettingRow>
                <SettingRow label="Hız telemetrisi" hint="Yalnızca fiziksel CCVS verisi kilidi açabilir; sentetik beslemeler yalnızca görüntülenir.">
                  <ReadOnlyValue value="SA izin listesi" />
                </SettingRow>
              </SettingsGroup>
            </div>
          )}

          {/* ================================================ */}
          {/* Depolama */}
          {/* ================================================ */}
          {activeSection === 'storage' && (
            <div className="space-y-6">
              <SectionHeader
                title="Bellek & Depolama"
                description="Yakalanan karelerin bellekte ve diskte tutulma biçimi; sıkıştırma ve bütünlük şeması."
              />

              <SettingsGroup label="Kayıt mimarisi" icon={<Database className="h-3 w-3" />}>
                <SettingRow label="Halka bellek" hint="Zero-GC sabit kapasiteli dairesel bellek alanı.">
                  <ReadOnlyValue value="100.000 kare · ~12.8 MB" tone="accent" />
                </SettingRow>
                <SettingRow label="Disk sıkıştırma" hint="Arşivlenen oturumların sıkıştırma ve bütünlük şeması.">
                  <ReadOnlyValue value="Zstandard Lv.3 + HMAC-SHA256" tone="ok" />
                </SettingRow>
                <SettingRow label="Arşiv rotasyonu" hint="Disk dolduğunda eski kayıtların temizleme kuralı.">
                  <ReadOnlyValue value="FIFO · %90 disk eşiği" />
                </SettingRow>
              </SettingsGroup>

              <div className="flex items-center gap-2 px-0.5 text-[11px] text-text-low">
                <Archive className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>Arşiv dosyaları ve dışa aktarma, Rapor & Export bölümünden yönetilir.</span>
              </div>
            </div>
          )}

          {/* ================================================ */}
          {/* Açık Kaynak */}
          {/* ================================================ */}
          {activeSection === 'attribution' && (
            <div className="space-y-6">
              <SectionHeader
                title="Açık Kaynak"
                description="Uygulamaya gömülü üçüncü taraf veri kümeleri ve zorunlu lisans atıfları."
              />
              <SettingsAttributionPanel />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

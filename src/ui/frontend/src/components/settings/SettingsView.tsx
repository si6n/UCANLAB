import React, { useState, useEffect, useCallback } from 'react';
import {
  Cpu,
  ShieldCheck,
  HardDrive,
  Key,
  CheckCircle2,
  AlertCircle,
  Save,
  Sliders,
  ExternalLink,
  Loader2,
  Laptop,
  Cloud,
  Copy,
  Check,
} from 'lucide-react';
import { DesktopBridge, CloudStatus } from '../../services/bridge';

interface SettingsViewProps {
  channel: string;
  baudRate: string;
  onSave: (settings: { channel: string; baudRate: string; cloudBaseUrl?: string }) => void;
}

export const SettingsView: React.FC<SettingsViewProps> = ({
  channel: initChannel,
  baudRate: initBaud,
  onSave,
}) => {
  const [channel, setChannel] = useState(initChannel);
  const [baudRate, setBaudRate] = useState(initBaud);
  const [cloudUrl, setCloudUrl] = useState(() => {
    return localStorage.getItem('cloud_base_url') || 'https://ucan-cloud.si6n.io';
  });
  const [cloudStatus, setCloudStatus] = useState<CloudStatus | null>(null);
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [activeSection, setActiveSection] = useState<'hardware' | 'license' | 'safety' | 'storage'>('hardware');

  const [licenseKeyInput, setLicenseKeyInput] = useState('');
  const [isActionLoading, setIsActionLoading] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [copiedHwid, setCopiedHwid] = useState(false);

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
      // Fallback
    }
  }, []);

  useEffect(() => {
    fetchCloudStatus();
  }, [fetchCloudStatus]);

  const portalUrl = 'https://ucanlab.org/login';

  const handleRegisterDevice = async () => {
    setIsActionLoading(true);
    setActionFeedback(null);
    try {
      const res = await DesktopBridge.cloudRegisterDevice('Desktop Diagnostic Tool');
      if (res.success) {
        setActionFeedback({
          type: 'success',
          text: `Cihaz buluta başarıyla kaydedildi! (ID: ${res.deviceId?.slice(0, 8)}... - Kalan HWID sıfırlama hakkı: ${res.resetsRemaining ?? 1})`,
        });
        await fetchCloudStatus();
      } else {
        setActionFeedback({
          type: 'error',
          text: `Hata: Cihaz kaydı başarısız: ${res.error}`,
        });
      }
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: `Hata: ${err.message || 'Kayıt gerçekleştirilemedi'}`,
      });
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleActivateLicense = async () => {
    if (!licenseKeyInput.trim()) {
      setActionFeedback({
        type: 'error',
        text: 'Lütfen sipariş referans kodu veya lisans anahtarı girin.',
      });
      return;
    }

    setIsActionLoading(true);
    setActionFeedback(null);
    try {
      const res = await DesktopBridge.cloudActivateLicense(licenseKeyInput.trim());
      if (res.success) {
        const expStr = res.expiresAt ? new Date(res.expiresAt * 1000).toLocaleDateString('tr-TR') : 'Süresiz';
        setActionFeedback({
          type: 'success',
          text: `Lisans Aktif! Tier: ${(res.tier || 'Enterprise').toUpperCase()} (Bitiş: ${expStr})`,
        });
        setLicenseKeyInput('');
        await fetchCloudStatus();
      } else {
        setActionFeedback({
          type: 'error',
          text: `Hata: Lisans aktivasyonu başarısız: ${res.error}`,
        });
      }
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: `Hata: ${err.message || 'Aktivasyon hatası'}`,
      });
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
    // D4 (REVIEW Aşama 2): the previous implementation persisted the URL to
    // localStorage and showed "Saved" BEFORE awaiting the backend, so a URL the
    // backend rejects (host not on the cloud allowlist) was still announced as
    // saved and silently written to storage. Persist only on a confirmed save.
    const requested = cloudUrl.trim();
    try {
      const res: any = await DesktopBridge.cloudSaveConfig(requested);
      if (res && res.success === false) {
        setActionFeedback({
          type: 'error',
          text: `Ayarlar kaydedilemedi: ${res.error || 'bilinmeyen hata'}`,
        });
        return;
      }
      localStorage.setItem('cloud_base_url', requested);
      onSave({ channel, baudRate, cloudBaseUrl: requested });
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 2500);
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: `Ayarlar kaydedilemedi: ${err?.message || 'bağlantı hatası'}`,
      });
    }
  };

  const baudOptions = ['125 kbps', '250 kbps', '500 kbps', '1000 kbps (1 Mbps)'];
  const channelOptions = [
    { id: 'vcan0', name: 'vcan0 (Sanal CAN / Virtual Bus)' },
    { id: 'can0', name: 'can0 (Linux SocketCAN Fiziksel)' },
    { id: 'PCAN_USBBUS1', name: 'PCAN-USB (Peak System Ch 1)' },
    { id: 'kvaser_0', name: 'Kvaser Leaf Light v2 (Ch 0)' },
    { id: 'rp1210:DLA', name: 'RP1210 DLA (Ağır Vasıta Adaptörü)' },
  ];

  return (
    <div className="flex h-full w-full flex-col overflow-hidden text-text-body select-none">
      {/* Top Bar of Settings — seamless glass chrome */}
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 px-5 bg-transparent">
        <div className="flex items-center gap-2.5">
          <div className="flex h-6 w-6 items-center justify-center rounded-[6px] bg-accent-soft text-accent">
            <Sliders className="h-3.5 w-3.5" />
          </div>
          <div>
            <h2 className="text-[13px] font-semibold text-text-hi">Sistem & Donanım Yapılandırması</h2>
            <p className="text-[10.5px] text-text-mid">CAN veri yolu arayüzü, bulut SaaS hesabı ve ASIL-D güvenlik kuralları</p>
          </div>
        </div>

        <button
          onClick={handleSave}
          className="flex items-center gap-1.5 rounded-[6px] border border-accent-line bg-accent-soft px-3 py-1 font-sans text-[12px] font-medium text-accent-text transition-all hover:bg-accent hover:text-white active:scale-[0.98]"
        >
          {savedSuccess ? (
            <>
              <CheckCircle2 className="h-3.5 w-3.5 text-add" />
              <span>Kaydedildi</span>
            </>
          ) : (
            <>
              <Save className="h-3.5 w-3.5" />
              <span>Değişiklikleri Kaydet</span>
            </>
          )}
        </button>
      </div>

      {/* Main Content: Left Sub-navigation + Right Form Panels */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Navigation Tabs (Sub-sections) */}
        <aside className="w-52 shrink-0 border-r border-border/60 p-2.5 space-y-1 bg-transparent">
          {[
            { id: 'hardware', label: 'CAN Donanımı', icon: Cpu, desc: 'Kanal & Baudrate' },
            { id: 'license', label: 'Bulut & Lisans', icon: Key, desc: 'Giriş & Aktivasyon' },
            { id: 'safety', label: 'Güvenlik & ASIL-D', icon: ShieldCheck, desc: 'Watchdog & Chokepoint' },
            { id: 'storage', label: 'Kayıt & Bellek', icon: HardDrive, desc: 'NumPy & Zstandard' },
          ].map((sec) => {
            const Icon = sec.icon;
            const isCurrent = activeSection === sec.id;
            return (
              <button
                key={sec.id}
                onClick={() => setActiveSection(sec.id as any)}
                className={`flex w-full items-start gap-2.5 rounded-[8px] p-2 text-left transition-all ${
                  isCurrent
                    ? 'bg-accent-soft text-accent-text border border-accent-line/40 font-medium'
                    : 'text-text-mid hover:bg-bg-row-hover hover:text-text-hi border border-transparent'
                }`}
              >
                <Icon className={`h-4 w-4 mt-0.5 shrink-0 ${isCurrent ? 'text-accent' : 'text-text-low'}`} />
                <div className="min-w-0">
                  <div className="text-[12px] font-semibold">{sec.label}</div>
                  <div className="text-[10px] text-text-low">{sec.desc}</div>
                </div>
              </button>
            );
          })}
        </aside>

        {/* Form Body */}
        <main className="flex-1 overflow-y-auto p-5 space-y-5 bg-transparent">
          {/* TAB 1: CAN DONANIMI */}
          {activeSection === 'hardware' && (
            <div className="max-w-2xl space-y-5">
              <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 space-y-4">
                <div className="border-b border-border/40 pb-2.5">
                  <h3 className="text-[13px] font-semibold text-text-hi">CAN Veri Yolu Arayüzü</h3>
                  <p className="text-[11px] text-text-mid">Aktif dinlenecek ve telemetrisi toplanacak CAN transceiver seçimi</p>
                </div>

                <div className="space-y-1.5">
                  <label className="font-sans text-[11.5px] font-medium text-text-mid">Kanal Arayüzü (Interface Channel)</label>
                  <select
                    value={channel}
                    onChange={(e) => setChannel(e.target.value)}
                    className="h-8 w-full rounded-[8px] border border-surface-inset-border bg-surface-inset px-2.5 font-mono text-[11.5px] text-text-hi transition-colors focus:border-border-focus focus:outline-none"
                  >
                    {channelOptions.map((opt) => (
                      <option key={opt.id} value={opt.id} className="bg-surface-inset text-text-hi">
                        {opt.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1.5">
                  <label className="font-sans text-[11.5px] font-medium text-text-mid">Baud Hızı (Bit Rate)</label>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {baudOptions.map((b) => (
                      <button
                        key={b}
                        type="button"
                        onClick={() => setBaudRate(b)}
                        className={`rounded-[6px] border px-2.5 py-1.5 font-mono text-[11.5px] transition-all active:scale-[0.98] ${
                          baudRate === b
                            ? 'border-accent-line bg-accent-soft font-semibold text-accent-text'
                            : 'border-border/60 bg-surface-inset/40 text-text-mid hover:text-text-hi hover:bg-bg-row-hover'
                        }`}
                      >
                        {b}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: BULUT SAAS, GİRİŞ & LİSANSLAMA */}
          {activeSection === 'license' && (
            <div className="max-w-2xl space-y-4">
              {/* Feedback banner */}
              {actionFeedback && (
                <div
                  className={`flex items-start gap-2 rounded-[8px] border p-2.5 text-[11.5px] font-medium ${
                    actionFeedback.type === 'success'
                      ? 'border-addedge/40 bg-addbg text-add'
                      : 'border-deledge/40 bg-delbg text-del'
                  }`}
                >
                  {actionFeedback.type === 'success' ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
                  ) : (
                    <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
                  )}
                  <span className="flex-1">{actionFeedback.text}</span>
                </div>
              )}

              {/* Card 1: Web Portal Login */}
              <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-border/40 pb-2.5">
                  <div className="flex items-center gap-2">
                    <Cloud className="h-4 w-4 text-accent" />
                    <div>
                      <h3 className="text-[13px] font-semibold text-text-hi">UCanLab Bulut Hesabı & Giriş</h3>
                      <p className="text-[10.5px] text-text-mid">Hesap oluşturma, giriş ve abonelik yönetimi resmi web portalımız üzerinden gerçekleştirilir.</p>
                    </div>
                  </div>
                  {cloudStatus?.hasSessionToken ? (
                    <span className="flex items-center gap-1 rounded-[4px] border border-addedge/30 bg-addbg px-2 py-0.5 font-mono text-[10.5px] font-medium text-add">
                      <CheckCircle2 className="h-3 w-3" />
                      <span>Oturum Aktif</span>
                    </span>
                  ) : (
                    <span className="rounded-[4px] border border-border px-2 py-0.5 font-mono text-[10.5px] text-text-low">
                      Giriş Yapılmadı
                    </span>
                  )}
                </div>

                <p className="font-sans text-[11.5px] text-text-mid leading-relaxed">
                  Web sitemizde oturum açtıktan veya abonelik aldıktan sonra lisans referans kodunuzu aşağıdaki alana girerek uygulamayı derhal aktif edebilirsiniz.
                </p>

                <a
                  href={portalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center justify-center gap-2 rounded-[6px] border border-accent-line bg-accent-soft px-3.5 py-1.5 font-sans text-[12px] font-semibold text-accent-text transition-all hover:bg-accent hover:text-white active:scale-[0.98]"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  <span>Web Sitemizde Giriş Yap / Abonelik Al (ucanlab.org/login)</span>
                </a>
              </div>


              {/* Card 3: Cihaz HWID & Bulut Kaydı */}
              <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-border/40 pb-2">
                  <div className="flex items-center gap-2">
                    <Laptop className="h-4 w-4 text-text-mid" />
                    <div>
                      <h4 className="text-[12px] font-semibold text-text-hi">Cihaz HWID Parmak İzi</h4>
                      <p className="text-[10.5px] text-text-mid">Donanıma bağlı kriptografik parmak izi</p>
                    </div>
                  </div>
                  {cloudStatus?.hasDeviceToken ? (
                    <span className="flex items-center gap-1 rounded-[4px] border border-addedge/30 bg-addbg px-2 py-0.5 font-mono text-[10.5px] font-medium text-add">
                      <CheckCircle2 className="h-3 w-3" />
                      <span>Buluta Kayıtlı</span>
                    </span>
                  ) : (
                    <span className="rounded-[4px] border border-border px-2 py-0.5 font-mono text-[10.5px] text-text-low">
                      Kayıtsız
                    </span>
                  )}
                </div>

                <div className="flex items-center justify-between rounded-[6px] border border-surface-inset-border bg-surface-inset px-2.5 py-1.5">
                  <span className="font-mono text-[11px] text-text-hi select-all">
                    {cloudStatus?.hwid || 'Hesaplanıyor...'}
                  </span>
                  <button
                    type="button"
                    onClick={handleCopyHwid}
                    className="flex items-center gap-1 text-[10.5px] font-medium text-text-low hover:text-text-hi"
                    title="HWID Kopyala"
                  >
                    {copiedHwid ? <Check className="h-3 w-3 text-add" /> : <Copy className="h-3 w-3" />}
                    <span>{copiedHwid ? 'Kopyalandı' : 'Kopyala'}</span>
                  </button>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="font-sans text-[11px] text-text-low">
                    Cihazı mevcut SaaS kiracınıza bağlayarak lisans yetkilerini senkronize edin.
                  </span>
                  <button
                    type="button"
                    onClick={handleRegisterDevice}
                    disabled={isActionLoading}
                    className="flex items-center gap-1.5 rounded-[6px] border border-border px-2.5 py-1 font-sans text-[11.5px] font-medium text-text-body transition-colors hover:bg-bg-row-hover hover:text-text-hi disabled:opacity-50"
                  >
                    {isActionLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Laptop className="h-3 w-3" />}
                    <span>{cloudStatus?.hasDeviceToken ? 'Yeniden Kaydet' : 'Cihazı Kaydet'}</span>
                  </button>
                </div>
              </div>

              {/* Card 4: Ed25519 Lisans Aktivasyonu */}
              <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-border/40 pb-2">
                  <div className="flex items-center gap-2">
                    <Key className="h-4 w-4 text-accent" />
                    <div>
                      <h4 className="text-[12px] font-semibold text-text-hi">Ed25519 Kriptografik Lisans</h4>
                      <p className="text-[10.5px] text-text-mid">Sipariş referans kodu veya offline bilet ile lisans aktifleştirme</p>
                    </div>
                  </div>
                  {cloudStatus?.license ? (
                    <span className="rounded-[4px] border border-accent-line/40 bg-accent-soft px-2 py-0.5 font-mono text-[10.5px] font-bold text-accent-text uppercase">
                      {cloudStatus.license.tier}
                    </span>
                  ) : (
                    <span className="rounded-[4px] border border-border px-2 py-0.5 font-mono text-[10.5px] text-text-low">
                      Lisans Yok (Demo)
                    </span>
                  )}
                </div>

                {cloudStatus?.license ? (
                  <div className="space-y-1.5 rounded-[6px] border border-surface-inset-border bg-surface-inset p-2.5 font-mono text-[11px]">
                    <div className="flex justify-between">
                      <span className="text-text-mid">Lisans ID:</span>
                      <span className="font-semibold text-text-hi">{cloudStatus.license.licenseId}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-text-mid">Geçerlilik:</span>
                      <span className="text-text-hi">
                        {new Date(cloudStatus.license.expiresAt * 1000).toLocaleDateString('tr-TR')}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-text-mid">Offline Grace:</span>
                      <span className="text-text-hi">
                        {new Date(cloudStatus.license.offlineUntil * 1000).toLocaleDateString('tr-TR')}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1 pt-1">
                      {cloudStatus.license.features.map((f) => (
                        <span key={f} className="rounded-[4px] border border-border px-1.5 py-0.5 text-[10px] text-text-mid">
                          {f}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={licenseKeyInput}
                        onChange={(e) => setLicenseKeyInput(e.target.value)}
                        placeholder="Örn: LIC-ENT-2026-XXXX-YYYY"
                        className="h-8 flex-1 rounded-[8px] border border-surface-inset-border bg-surface-inset px-2.5 font-mono text-[11.5px] text-text-hi placeholder:text-text-faint transition-colors focus:border-border-focus focus:outline-none"
                      />
                      <button
                        type="button"
                        onClick={handleActivateLicense}
                        disabled={isActionLoading || !licenseKeyInput.trim()}
                        className="flex h-8 items-center gap-1.5 rounded-[6px] border border-accent-line bg-accent-soft px-3 font-sans text-[11.5px] font-semibold text-accent-text transition-all hover:bg-accent hover:text-white disabled:opacity-50"
                      >
                        {isActionLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Key className="h-3 w-3" />}
                        <span>Lisansı Aktif Et</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB 3: GÜVENLİK & ASIL-D */}
          {activeSection === 'safety' && (
            <div className="max-w-2xl space-y-4">
              <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 space-y-3">
                <div className="border-b border-border/40 pb-2">
                  <h3 className="text-[13px] font-semibold text-text-hi">ISO 26262 ASIL-D Güvenlik Politikası</h3>
                  <p className="text-[11px] text-text-mid">Tekil iletim kontrol boğazı (TxSafetyGateway) ve fail-closed korumaları</p>
                </div>

                <div className="space-y-2 text-[11.5px]">
                  <div className="flex items-center justify-between rounded-[8px] border border-surface-inset-border bg-surface-inset p-2.5">
                    <div>
                      <div className="font-semibold text-text-hi">Monotonic Watchdog Süresi</div>
                      <div className="text-[10.5px] text-text-mid">UI-alive nabız toleransı: 800ms (250ms pulse)</div>
                    </div>
                    <span className="rounded-[4px] border border-addedge/40 bg-addbg px-2 py-0.5 font-mono text-[11px] font-bold text-add">800 ms</span>
                  </div>

                  <div className="flex items-center justify-between rounded-[8px] border border-surface-inset-border bg-surface-inset p-2.5">
                    <div>
                      <div className="font-semibold text-text-hi">Tekil İletim Boğazı (TxPort)</div>
                      <div className="text-[10.5px] text-text-mid">Tüm gönderimler TxSafetyGateway 6 aşamalı filtreden geçer</div>
                    </div>
                    <span className="rounded-[4px] border border-accent-line/40 bg-accent-soft px-2 py-0.5 font-mono text-[11px] font-bold text-accent-text">AKTİF</span>
                  </div>

                  <div className="flex items-center justify-between rounded-[8px] border border-surface-inset-border bg-surface-inset p-2.5">
                    <div>
                      <div className="font-semibold text-text-hi">Hız Güvenlik Kilidi (CCVS Interlock)</div>
                      <div className="text-[10.5px] text-text-mid">Yalnızca fiziksel CCVS hız telemetrisi kritik komutları yetkilendirebilir</div>
                    </div>
                    <span className="rounded-[4px] border border-addedge/40 bg-addbg px-2 py-0.5 font-mono text-[11px] font-bold text-add">FAIL-CLOSED</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: KAYIT & BELLEK */}
          {activeSection === 'storage' && (
            <div className="max-w-2xl space-y-4">
              <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 space-y-3">
                <div className="border-b border-border/40 pb-2">
                  <h3 className="text-[13px] font-semibold text-text-hi">Yüksek Hızlı Telemetri Depolama</h3>
                  <p className="text-[11px] text-text-mid">Sıfır çöp toplayıcı (Zero-GC) tampon ve Zstandard sıkıştırma</p>
                </div>

                <div className="grid grid-cols-2 gap-3 text-[11.5px]">
                  <div className="rounded-[8px] border border-surface-inset-border bg-surface-inset p-3 space-y-1">
                    <span className="text-text-low font-medium">Halka Bellek Tamponu</span>
                    <div className="font-mono text-[15px] font-bold text-text-hi">100,000 Kare</div>
                    <span className="text-[10px] text-text-mid">NumPy doğrudan bellek eşlemesi (GC duraklaması yok)</span>
                  </div>

                  <div className="rounded-[8px] border border-surface-inset-border bg-surface-inset p-3 space-y-1">
                    <span className="text-text-low font-medium">Disk Sıkıştırma Formatı</span>
                    <div className="font-mono text-[15px] font-bold text-add">Zstandard + HMAC</div>
                    <span className="text-[10px] text-text-mid">Bütünlük imzalı döngüsel kara kutu blokları</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
};

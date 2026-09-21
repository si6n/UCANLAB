import React, { useState, useEffect } from 'react';
import {
  X,
  Settings,
  Cpu,
  Check,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Cloud,
  ShieldCheck,
  Laptop,
  ExternalLink
} from 'lucide-react';
import { DesktopBridge, CloudStatus } from '../../services/bridge';

export interface AppSettings {
  channel: string;
  baudRate: string;
  cloudBaseUrl?: string;
}

interface SettingsModalProps {
  isOpen: boolean;
  channel: string;
  baudRate: string;
  onClose: () => void;
  onSave: (settings: AppSettings) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  channel: initChannel,
  baudRate: initBaud,
  onClose,
  onSave
}) => {
  const [modalTab, setModalTab] = useState<'hardware' | 'cloud'>('cloud');

  // Hardware State
  const [channel, setChannel] = useState(initChannel);
  const [baudRate, setBaudRate] = useState(initBaud);

  // Cloud SaaS & License State
  const [cloudUrl, setCloudUrl] = useState(() => {
    return localStorage.getItem('cloud_base_url') || 'https://ucan-cloud.si6n.io';
  });
  const [licenseKeyInput, setLicenseKeyInput] = useState('');
  const [cloudStatus, setCloudStatus] = useState<CloudStatus | null>(null);
  const [cloudTestState, setCloudTestState] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [cloudTestMsg, setCloudTestMsg] = useState<string>('');
  const [isActionLoading, setIsActionLoading] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const fetchCloudStatus = async () => {
    try {
      const status = await DesktopBridge.cloudGetStatus();
      setCloudStatus(status);
      if (status.baseUrl) {
        setCloudUrl(status.baseUrl);
      }
    } catch (err: any) {
      console.warn('Could not fetch cloud status:', err);
    }
  };

  useEffect(() => {
    if (isOpen) {
      setChannel(initChannel);
      setBaudRate(initBaud);
      const savedCloudUrl = localStorage.getItem('cloud_base_url') || 'https://ucan-cloud.si6n.io';

      setCloudUrl(savedCloudUrl);

      setCloudTestState('idle');
      setCloudTestMsg('');
      setActionFeedback(null);

      fetchCloudStatus();
    }
  }, [isOpen, initChannel, initBaud]);

  if (!isOpen) return null;

  let portalUrl = 'https://ucanlab.org/login';
  try {
    const origin = new URL(cloudUrl.trim()).origin;
    if (origin === 'https://ucan-cloud.si6n.io' || origin === 'https://cloud.universalcan.io') {
      portalUrl = 'https://ucanlab.org/login';
    } else {
      portalUrl = `${origin}/login`;
    }
  } catch {
    /* keep default portal */
  }

  const handleTestCloud = async () => {
    setCloudTestState('testing');
    setCloudTestMsg('Universal-CAN-Cloud API (/health) test ediliyor...');
    setActionFeedback(null);

    try {
      const res = await DesktopBridge.cloudTestConnection(cloudUrl.trim());
      if (res.success) {
        setCloudTestState('success');
        let msg = `Bulut API erişilebilir (HTTP ${res.status || 200}).`;
        if (res.user) {
          msg += ` Giriş yapıldı: ${res.user.email || 'Operatör'} (${res.user.organization_name || 'Kurumsal'})`;
        } else {
          msg += ' (Anonim oturum — Giriş için yukarıdaki portal düğmesini kullanın)';
        }
        setCloudTestMsg(msg);
      } else {
        setCloudTestState('error');
        setCloudTestMsg(`Hata: Bulut Bağlantı Hatası: ${res.error}`);
      }
    } catch (err: any) {
      setCloudTestState('error');
      setCloudTestMsg(`Hata: İstek Hatası: ${err.message || 'Sunucuya ulaşılamıyor'}`);
    }
  };

  const handleRegisterDevice = async () => {
    setIsActionLoading(true);
    setActionFeedback(null);
    try {
      const res = await DesktopBridge.cloudRegisterDevice('Desktop Diagnostic Tool');
      if (res.success) {
        setActionFeedback({
          type: 'success',
          text: `Cihaz buluta başarıyla kaydedildi! (ID: ${res.deviceId?.slice(0, 8)}... - Kalan HWID sıfırlama hakkı: ${res.resetsRemaining ?? 1})`
        });
        await fetchCloudStatus();
      } else {
        setActionFeedback({
          type: 'error',
          text: `Hata: Cihaz kaydı başarısız: ${res.error}`
        });
      }
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: `Hata: Hata: ${err.message || 'Kayıt gerçekleştirilemedi'}`
      });
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleActivateLicense = async () => {
    if (!licenseKeyInput.trim()) {
      setActionFeedback({
        type: 'error',
        text: 'Lütfen sipariş referans kodu veya lisans anahtarı girin.'
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
          text: `Lisans Aktif! Tier: ${(res.tier || 'Enterprise').toUpperCase()} (Bitiş: ${expStr})`
        });
        setLicenseKeyInput('');
        await fetchCloudStatus();
      } else {
        setActionFeedback({
          type: 'error',
          text: `Hata: Lisans aktivasyonu başarısız: ${res.error}`
        });
      }
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: `Hata: Hata: ${err.message || 'Aktivasyon hatası'}`
      });
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleSave = async () => {
    // H-11 (P1-9): secrets are NEVER persisted to localStorage anymore — the
    // WebView profile directory stores it unencrypted, readable by any
    // script in the page and any process running as this user. The backend
    // vault (DPAPI / machine-seed AES-GCM) is the only persistence; the
    // renderer keeps at most an in-session value passed up via onSave.
    // One-time migration: scrub legacy plaintext keys left by old builds.
    localStorage.removeItem('gemini_api_key');
    localStorage.removeItem('openai_api_key');
    localStorage.removeItem('cloud_session_token');
    localStorage.removeItem('ai_provider');

    // D4 (REVIEW Aşama 2): do NOT announce success or persist the URL before
    // the backend confirms it. The backend rejects hosts outside the cloud
    // allowlist, and the old code wrote that rejected URL to localStorage and
    // closed the modal as if it had been saved.
    const requested = cloudUrl.trim();
    try {
      const res: any = await DesktopBridge.cloudSaveConfig(requested);
      if (res && res.success === false) {
        setActionFeedback({ type: 'error', text: res.error || 'Ayarlar kaydedilemedi.' });
        return;
      }
      localStorage.setItem('cloud_base_url', requested);
      onSave({
        channel,
        baudRate,
        cloudBaseUrl: requested
      });
      onClose();
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: err?.message || 'Ayarlar kaydedilemedi: bağlantı hatası.',
      });
    }
  };

  return (
    <div className="glass-overlay fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="glass-surface glass-modal border rounded-2xl w-full max-w-lg overflow-hidden animate-in fade-in zoom-in-95">
        {/* Header */}
        <div className="px-4 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
          <div className="flex items-center space-x-2 text-xs font-bold text-slate-900">
            <Settings className="w-4 h-4 text-brand-600" />
            <span>CAN Donanım & Bulut SaaS Yapılandırması</span>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-slate-500 hover:text-slate-700 hover:bg-slate-200/60 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-slate-200 bg-slate-100/70 px-4 pt-2">
          <button
            type="button"
            onClick={() => setModalTab('hardware')}
            className={`flex items-center space-x-2 px-3 py-2 text-xs font-bold border-b-2 transition-all ${
              modalTab === 'hardware'
                ? 'border-brand-600 text-brand-700 bg-white rounded-t-lg shadow-xs'
                : 'border-transparent text-slate-600 hover:text-slate-900'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>Donanım (Çevrimdışı Copilot)</span>
          </button>

          <button
            type="button"
            onClick={() => setModalTab('cloud')}
            className={`flex items-center space-x-2 px-3 py-2 text-xs font-bold border-b-2 transition-all ${
              modalTab === 'cloud'
                ? 'border-brand-600 text-brand-700 bg-white rounded-t-lg shadow-xs'
                : 'border-transparent text-slate-600 hover:text-slate-900'
            }`}
          >
            <Cloud className="w-3.5 h-3.5 text-brand-600" />
            <span>Bulut SaaS & Lisanslama</span>
            {cloudStatus?.license && (
              <span className="bg-signal-100 text-signal-800 text-xs px-1.5 py-0.2 rounded-full font-mono">
                {cloudStatus.license.tier.toUpperCase()}
              </span>
            )}
          </button>
        </div>

        {/* Form Body */}
        <div className="p-4 space-y-4 text-xs max-h-[70vh] overflow-y-auto">
          {modalTab === 'hardware' ? (
            <>
              {/* Channel Selection */}
              <div className="space-y-1.5">
                <label className="font-semibold text-slate-700 flex items-center space-x-1.5">
                  <Cpu className="w-3.5 h-3.5 text-slate-500" />
                  <span>CAN Arayüz Kanalı:</span>
                </label>
                <div className="grid grid-cols-3 gap-2">
                  {['vcan0', 'can0', 'can1'].map((ch) => (
                    <button
                      key={ch}
                      type="button"
                      onClick={() => setChannel(ch)}
                      className={`py-1.5 px-3 rounded-lg border font-mono font-bold text-center transition-all ${
                        channel === ch
                          ? 'bg-brand-50 border-brand-500 text-brand-700 ring-1 ring-brand-500'
                          : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
                      }`}
                    >
                      {ch}
                    </button>
                  ))}
                </div>
              </div>

              {/* Baud Rate Selection */}
              <div className="space-y-1.5">
                <label className="font-semibold text-slate-700">
                  Baud Hızı (Bitrate):
                </label>
                <div className="grid grid-cols-4 gap-2">
                  {['125 kbps', '250 kbps', '500 kbps', '1000 kbps'].map((br) => (
                    <button
                      key={br}
                      type="button"
                      onClick={() => setBaudRate(br)}
                      className={`py-1.5 px-2 rounded-lg border font-mono font-bold text-center transition-all ${
                        baudRate === br
                          ? 'bg-brand-50 border-brand-500 text-brand-700 ring-1 ring-brand-500'
                          : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
                      }`}
                    >
                      {br.split(' ')[0]}
                    </button>
                  ))}
                </div>
              </div>

              {/* Offline AI notice */}
              <div className="p-2.5 rounded-lg border border-slate-200 bg-slate-50 text-xs text-slate-600 flex items-start space-x-1.5">
                <ShieldCheck className="w-3.5 h-3.5 text-brand-600 shrink-0 mt-0.5" />
                <span className="leading-snug">
                  AI Copilot tamamen çevrimdışı çalışır: yerel uzman motoru (deterministik) teşhis üretir. API anahtarı, bulut LLM veya internet bağlantısı gerekmez; veriler cihaz dışına çıkmaz.
                </span>
              </div>
            </>
          ) : (
            <>
              {/* Cloud SaaS Section */}
              <div className="space-y-3">
                {/* Portal Login Card */}
                <div className="bg-brand-50 border border-brand-200 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-1.5 font-bold text-slate-800 text-xs">
                      <Cloud className="w-3.5 h-3.5 text-brand-600" />
                      <span>Hesap & Abonelik Girişi</span>
                    </div>
                    {cloudStatus?.hasSessionToken ? (
                      <span className="text-xs font-semibold bg-signal-100 text-signal-800 px-2 py-0.5 rounded-full flex items-center space-x-1">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>Oturum Aktif</span>
                      </span>
                    ) : (
                      <span className="text-xs font-semibold bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full">
                        Giriş Yapılmadı
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-600 leading-snug">
                    Giriş, abonelik satın alma ve lisans yönetimi web portalında yapılır. Portalda oturum açtıktan sonra
                    buradan bağlantıyı test edip cihazınızı kaydedin — uygulama oturumunuzu otomatik doğrular.
                  </p>
                  <a
                    href={portalUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="w-full flex items-center justify-center space-x-1.5 px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded font-bold text-xs shadow-xs transition-colors"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                    <span>Web Sitemizde Giriş Yap / Abonelik Al</span>
                  </a>
                </div>

                {/* Cloud Connection Test Alert */}
                {cloudTestState !== 'idle' && (
                  <div className={`p-2.5 rounded-lg border text-xs flex items-start space-x-1.5 ${
                    cloudTestState === 'testing'
                      ? 'bg-brand-50 border-brand-200 text-brand-800'
                      : cloudTestState === 'success'
                      ? 'bg-signal-50 border-signal-200 text-signal-800 font-medium'
                      : 'bg-rose-50 border-rose-200 text-rose-800 font-medium'
                  }`}>
                    {cloudTestState === 'testing' && <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0 mt-0.5" />}
                    {cloudTestState === 'success' && <CheckCircle2 className="w-3.5 h-3.5 text-signal-600 shrink-0 mt-0.5" />}
                    {cloudTestState === 'error' && <AlertCircle className="w-3.5 h-3.5 text-rose-600 shrink-0 mt-0.5" />}
                    <span className="leading-snug">{cloudTestMsg}</span>
                  </div>
                )}

                {/* Device & HWID Registration Card */}
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-1.5 font-bold text-slate-800 text-xs">
                      <Laptop className="w-3.5 h-3.5 text-slate-600" />
                      <span>Cihaz HWID Parmak İzi & Kayıt</span>
                    </div>
                    {cloudStatus?.hasDeviceToken ? (
                      <span className="text-xs font-semibold bg-signal-100 text-signal-800 px-2 py-0.5 rounded-full flex items-center space-x-1">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>Buluta Kayıtlı</span>
                      </span>
                    ) : (
                      <span className="text-xs font-semibold bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full">
                        Kayıtsız
                      </span>
                    )}
                  </div>

                  <div className="text-xs font-mono text-slate-500 truncate bg-white p-1.5 rounded border border-slate-200" title={cloudStatus?.hwid || ''}>
                    HWID: <strong>{cloudStatus?.hwid ? `${cloudStatus.hwid.slice(0, 8)}…` : 'Hesaplanıyor...'}</strong>
                  </div>

                  <div className="flex items-center justify-between pt-1">
                    <span className="text-xs text-slate-500">
                      Cihazı mevcut SaaS kiracınıza bağlayın.
                    </span>
                    <button
                      type="button"
                      onClick={handleRegisterDevice}
                      disabled={isActionLoading}
                      className="px-3 py-1 bg-brand-600 hover:bg-brand-700 text-white rounded font-bold text-xs shadow-xs flex items-center space-x-1 transition-colors disabled:opacity-50"
                    >
                      {isActionLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Laptop className="w-3 h-3" />}
                      <span>{cloudStatus?.hasDeviceToken ? 'Yeniden Kaydet' : 'Cihazı Kaydet'}</span>
                    </button>
                  </div>
                </div>

                {/* Ed25519 License Card */}
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-1.5 font-bold text-slate-800 text-xs">
                      <ShieldCheck className="w-3.5 h-3.5 text-brand-600" />
                      <span>Ed25519 Kriptografik Lisans</span>
                    </div>
                    {cloudStatus?.license ? (
                      <span className="text-xs font-bold bg-brand-100 text-brand-800 px-2 py-0.5 rounded-full font-mono uppercase">
                        {cloudStatus.license.tier}
                      </span>
                    ) : (
                      <span className="text-xs font-semibold bg-slate-200 text-slate-600 px-2 py-0.5 rounded-full">
                        Lisans Yok (Demo)
                      </span>
                    )}
                  </div>

                  {cloudStatus?.license ? (
                    <div className="space-y-1 bg-white p-2 rounded border border-slate-200 text-xs">
                      <div className="flex justify-between text-slate-600">
                        <span>Lisans ID:</span>
                        <span className="font-mono font-bold text-slate-800">{cloudStatus.license.licenseId}</span>
                      </div>
                      <div className="flex justify-between text-slate-600">
                        <span>Geçerlilik:</span>
                        <span className="font-semibold text-slate-800">
                          {new Date(cloudStatus.license.expiresAt * 1000).toLocaleDateString('tr-TR')}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-600">
                        <span>Offline Grace:</span>
                        <span className="font-semibold text-slate-800">
                          {new Date(cloudStatus.license.offlineUntil * 1000).toLocaleDateString('tr-TR')}
                        </span>
                      </div>
                      <div className="pt-1 flex flex-wrap gap-1">
                        {cloudStatus.license.features.map(f => (
                          <span key={f} className="text-xs bg-slate-100 text-slate-600 px-1.5 py-0.2 rounded font-mono">
                            {f}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {/* Activate Form */}
                  <div className="space-y-1.5 pt-1">
                    <label className="text-xs font-semibold text-slate-700">
                      Sipariş Ref / Aktivasyon Kodu:
                    </label>
                    <div className="flex space-x-2">
                      <input
                        type="text"
                        value={licenseKeyInput}
                        onChange={(e) => setLicenseKeyInput(e.target.value)}
                        placeholder="ORD-2026-... veya lisans tokenı"
                        className="flex-1 bg-white border border-slate-200 rounded px-2.5 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-brand-500"
                      />
                      <button
                        type="button"
                        onClick={handleActivateLicense}
                        disabled={isActionLoading || !licenseKeyInput.trim()}
                        className="px-3 py-1 bg-signal-600 hover:bg-signal-700 text-white rounded font-bold text-xs shadow-xs flex items-center space-x-1 transition-colors disabled:opacity-50"
                      >
                        {isActionLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <ShieldCheck className="w-3 h-3" />}
                        <span>Aktive Et</span>
                      </button>
                    </div>
                  </div>
                </div>

                {/* Action Feedback Banner */}
                {actionFeedback && (
                  <div className={`p-2.5 rounded-lg border text-xs flex items-start space-x-1.5 ${
                    actionFeedback.type === 'success'
                      ? 'bg-signal-50 border-signal-200 text-signal-800 font-medium'
                      : 'bg-rose-50 border-rose-200 text-rose-800 font-medium'
                  }`}>
                    {actionFeedback.type === 'success' ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-signal-600 shrink-0 mt-0.5" />
                    ) : (
                      <AlertCircle className="w-3.5 h-3.5 text-rose-600 shrink-0 mt-0.5" />
                    )}
                    <span className="leading-snug">{actionFeedback.text}</span>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer Actions */}
        <div className="px-4 py-3 bg-slate-50 border-t border-slate-200 flex items-center justify-end space-x-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-100 font-semibold text-xs transition-colors"
          >
            İptal
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-bold text-xs flex items-center space-x-1.5 shadow-xs transition-colors"
          >
            <Check className="w-3.5 h-3.5" />
            <span>Ayarları Kaydet</span>
          </button>
        </div>
      </div>
    </div>
  );
};

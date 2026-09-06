import React, { useState, useEffect } from 'react';
import { 
  X, 
  Settings, 
  Key, 
  Cpu, 
  Check, 
  Zap, 
  CheckCircle2, 
  AlertCircle, 
  Loader2,
  Sparkles,
  Bot,
  Cloud,
  ShieldCheck,
  Laptop,
  Server,
  RefreshCw
} from 'lucide-react';
import { GeminiClient } from '../../services/geminiClient';
import { OpenAiClient } from '../../services/openAiClient';
import { DesktopBridge, CloudStatus } from '../../services/bridge';

export type AiProvider = 'gemini' | 'openai';

export interface AppSettings {
  channel: string;
  baudRate: string;
  provider: AiProvider;
  geminiApiKey: string;
  openaiApiKey: string;
  apiKey: string; // for backward-compatibility
  cloudBaseUrl?: string;
  cloudSessionToken?: string;
}

interface SettingsModalProps {
  isOpen: boolean;
  channel: string;
  baudRate: string;
  apiKey?: string;
  onClose: () => void;
  onSave: (settings: AppSettings) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  channel: initChannel,
  baudRate: initBaud,
  apiKey: initKey,
  onClose,
  onSave
}) => {
  const [modalTab, setModalTab] = useState<'hardware' | 'cloud'>('hardware');

  // Hardware & LLM State
  const [channel, setChannel] = useState(initChannel);
  const [baudRate, setBaudRate] = useState(initBaud);
  const [provider, setProvider] = useState<AiProvider>(() => {
    return (localStorage.getItem('ai_provider') as AiProvider) || 'gemini';
  });
  const [geminiKey, setGeminiKey] = useState(() => {
    return localStorage.getItem('gemini_api_key') || initKey || '';
  });
  const [openaiKey, setOpenaiKey] = useState(() => {
    return localStorage.getItem('openai_api_key') || '';
  });

  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [testMessage, setTestMessage] = useState<string>('');

  // Cloud SaaS & License State
  const [cloudUrl, setCloudUrl] = useState(() => {
    return localStorage.getItem('cloud_base_url') || 'http://127.0.0.1:8000';
  });
  const [cloudToken, setCloudToken] = useState(() => {
    return localStorage.getItem('cloud_session_token') || '';
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
      const savedProvider = (localStorage.getItem('ai_provider') as AiProvider) || 'gemini';
      const savedGemini = localStorage.getItem('gemini_api_key') || initKey || '';
      const savedOpenai = localStorage.getItem('openai_api_key') || '';
      const savedCloudUrl = localStorage.getItem('cloud_base_url') || 'http://127.0.0.1:8000';
      const savedCloudToken = localStorage.getItem('cloud_session_token') || '';

      setProvider(savedProvider);
      setGeminiKey(savedGemini);
      setOpenaiKey(savedOpenai);
      setCloudUrl(savedCloudUrl);
      setCloudToken(savedCloudToken);

      setTestStatus('idle');
      setTestMessage('');
      setCloudTestState('idle');
      setCloudTestMsg('');
      setActionFeedback(null);

      fetchCloudStatus();
    }
  }, [isOpen, initChannel, initBaud, initKey]);

  if (!isOpen) return null;

  const handleTestApiKey = async () => {
    setTestStatus('testing');

    if (provider === 'gemini') {
      const keyToTest = geminiKey.trim();
      if (!keyToTest) {
        setTestStatus('error');
        setTestMessage('Lütfen önce bir Google Gemini API anahtarı girin.');
        return;
      }

      setTestMessage('Google Gemini API modelleri (3.6/3.5/2.0 Flash) taranıyor...');
      try {
        const result = await GeminiClient.discoverAndTestModel(keyToTest);
        if (result.success) {
          setTestStatus('success');
          setTestMessage(`✅ Google Gemini Bağlantısı Başarılı! Aktif Model: ${result.modelName}`);
        } else {
          setTestStatus('error');
          setTestMessage(`❌ Hata: ${result.error}`);
        }
      } catch (err: any) {
        setTestStatus('error');
        setTestMessage(`❌ Bağlantı Hatası: ${err.message || 'Ağ engeli'}`);
      }
    } else {
      const keyToTest = openaiKey.trim();
      if (!keyToTest) {
        setTestStatus('error');
        setTestMessage('Lütfen önce bir OpenAI API anahtarı (sk-...) girin.');
        return;
      }

      setTestMessage('OpenAI ChatGPT modelleri (GPT-4o, GPT-4o-mini) taranıyor...');
      try {
        const result = await OpenAiClient.discoverAndTestModel(keyToTest);
        if (result.success) {
          setTestStatus('success');
          setTestMessage(`✅ OpenAI ChatGPT Bağlantısı Başarılı! Aktif Model: ${result.modelName}`);
        } else {
          setTestStatus('error');
          setTestMessage(`❌ Hata: ${result.error}`);
        }
      } catch (err: any) {
        setTestStatus('error');
        setTestMessage(`❌ Bağlantı Hatası: ${err.message || 'Ağ engeli'}`);
      }
    }
  };

  const handleTestCloud = async () => {
    setCloudTestState('testing');
    setCloudTestMsg('Universal-CAN-Cloud API (/health) test ediliyor...');
    setActionFeedback(null);

    try {
      const res = await DesktopBridge.cloudTestConnection(cloudUrl.trim(), cloudToken.trim() || undefined);
      if (res.success) {
        setCloudTestState('success');
        let msg = `✅ Bulut API erişilebilir (HTTP ${res.status || 200}).`;
        if (res.user) {
          msg += ` Giriş yapıldı: ${res.user.email || 'Operatör'} (${res.user.organization_name || 'Kurumsal'})`;
        } else if (!cloudToken.trim()) {
          msg += ' (Anonim oturum — Cihaz kaydı için web portalı tokenı gerekebilir)';
        }
        setCloudTestMsg(msg);
      } else {
        setCloudTestState('error');
        setCloudTestMsg(`❌ Bulut Bağlantı Hatası: ${res.error}`);
      }
    } catch (err: any) {
      setCloudTestState('error');
      setCloudTestMsg(`❌ İstek Hatası: ${err.message || 'Sunucuya ulaşılamıyor'}`);
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
          text: `✅ Cihaz buluta başarıyla kaydedildi! (ID: ${res.deviceId?.slice(0, 8)}... - Kalan HWID sıfırlama hakkı: ${res.resetsRemaining ?? 1})`
        });
        await fetchCloudStatus();
      } else {
        setActionFeedback({
          type: 'error',
          text: `❌ Cihaz kaydı başarısız: ${res.error}`
        });
      }
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: `❌ Hata: ${err.message || 'Kayıt gerçekleştirilemedi'}`
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
          text: `🎉 Lisans Aktif! Tier: ${(res.tier || 'Enterprise').toUpperCase()} (Bitiş: ${expStr})`
        });
        setLicenseKeyInput('');
        await fetchCloudStatus();
      } else {
        setActionFeedback({
          type: 'error',
          text: `❌ Lisans aktivasyonu başarısız: ${res.error}`
        });
      }
    } catch (err: any) {
      setActionFeedback({
        type: 'error',
        text: `❌ Hata: ${err.message || 'Aktivasyon hatası'}`
      });
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleSave = () => {
    localStorage.setItem('ai_provider', provider);
    localStorage.setItem('gemini_api_key', geminiKey.trim());
    localStorage.setItem('openai_api_key', openaiKey.trim());
    localStorage.setItem('cloud_base_url', cloudUrl.trim());
    localStorage.setItem('cloud_session_token', cloudToken.trim());

    DesktopBridge.cloudSaveConfig(cloudUrl.trim(), cloudToken.trim() || undefined);

    onSave({
      channel,
      baudRate,
      provider,
      geminiApiKey: geminiKey.trim(),
      openaiApiKey: openaiKey.trim(),
      apiKey: provider === 'openai' ? openaiKey.trim() : geminiKey.trim(),
      cloudBaseUrl: cloudUrl.trim(),
      cloudSessionToken: cloudToken.trim()
    });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xl w-full max-w-lg overflow-hidden animate-in fade-in zoom-in-95">
        {/* Header */}
        <div className="px-4 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
          <div className="flex items-center space-x-2 text-xs font-bold text-slate-900">
            <Settings className="w-4 h-4 text-blue-600" />
            <span>CAN Donanım, AI & Bulut SaaS Yapılandırması</span>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-slate-400 hover:text-slate-700 hover:bg-slate-200/60 transition-colors"
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
                ? 'border-blue-600 text-blue-700 bg-white rounded-t-lg shadow-xs'
                : 'border-transparent text-slate-600 hover:text-slate-900'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>Donanım & LLM Copilot</span>
          </button>

          <button
            type="button"
            onClick={() => setModalTab('cloud')}
            className={`flex items-center space-x-2 px-3 py-2 text-xs font-bold border-b-2 transition-all ${
              modalTab === 'cloud'
                ? 'border-blue-600 text-blue-700 bg-white rounded-t-lg shadow-xs'
                : 'border-transparent text-slate-600 hover:text-slate-900'
            }`}
          >
            <Cloud className="w-3.5 h-3.5 text-blue-600" />
            <span>Bulut SaaS & Lisanslama</span>
            {cloudStatus?.license && (
              <span className="bg-emerald-100 text-emerald-800 text-[10px] px-1.5 py-0.2 rounded-full font-mono">
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
                          ? 'bg-blue-50 border-blue-500 text-blue-700 ring-1 ring-blue-500'
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
                          ? 'bg-blue-50 border-blue-500 text-blue-700 ring-1 ring-blue-500'
                          : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
                      }`}
                    >
                      {br.split(' ')[0]}
                    </button>
                  ))}
                </div>
              </div>

              {/* AI Provider Switcher */}
              <div className="space-y-2 pt-1 border-t border-slate-100">
                <label className="font-semibold text-slate-700 flex items-center justify-between">
                  <span className="flex items-center space-x-1.5">
                    <Bot className="w-3.5 h-3.5 text-indigo-600" />
                    <span>Bulut AI Sağlayıcısı (Cloud LLM):</span>
                  </span>
                  <span className="text-[10px] text-slate-400 font-normal">Çoklu Model Desteği</span>
                </label>

                <div className="grid grid-cols-2 gap-2 bg-slate-100 p-1 rounded-lg border border-slate-200">
                  <button
                    type="button"
                    onClick={() => {
                      setProvider('gemini');
                      setTestStatus('idle');
                      setTestMessage('');
                    }}
                    className={`flex items-center justify-center space-x-1.5 py-1.5 rounded-md font-semibold text-xs transition-all ${
                      provider === 'gemini'
                        ? 'bg-white text-blue-700 shadow-xs border border-slate-200'
                        : 'text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    <Sparkles className="w-3.5 h-3.5 text-blue-600" />
                    <span>Google Gemini</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setProvider('openai');
                      setTestStatus('idle');
                      setTestMessage('');
                    }}
                    className={`flex items-center justify-center space-x-1.5 py-1.5 rounded-md font-semibold text-xs transition-all ${
                      provider === 'openai'
                        ? 'bg-white text-emerald-700 shadow-xs border border-slate-200'
                        : 'text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    <Zap className="w-3.5 h-3.5 text-emerald-600" />
                    <span>OpenAI ChatGPT</span>
                  </button>
                </div>
              </div>

              {/* Dynamic API Key Input for Google Gemini */}
              {provider === 'gemini' && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="font-semibold text-slate-700 flex items-center space-x-1.5">
                      <Key className="w-3.5 h-3.5 text-slate-500" />
                      <span>Google Gemini API Key:</span>
                    </label>
                    <button
                      type="button"
                      onClick={handleTestApiKey}
                      disabled={testStatus === 'testing' || !geminiKey.trim()}
                      className="flex items-center space-x-1 text-[11px] font-semibold text-blue-600 hover:text-blue-800 disabled:opacity-50 transition-colors"
                    >
                      {testStatus === 'testing' ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <Zap className="w-3 h-3" />
                      )}
                      <span>Bağlantıyı Test Et</span>
                    </button>
                  </div>

                  <input
                    type="password"
                    value={geminiKey}
                    onChange={(e) => setGeminiKey(e.target.value)}
                    placeholder="AIzaSy..."
                    className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
                  />
                  <p className="text-[10.5px] text-slate-400">
                    Gemini 3.6 / 3.5 / 2.0 Flash modellerini otomatik keşfeder ve en hızlı yanıtı seçer.
                  </p>
                </div>
              )}

              {/* Dynamic API Key Input for OpenAI ChatGPT */}
              {provider === 'openai' && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="font-semibold text-slate-700 flex items-center space-x-1.5">
                      <Key className="w-3.5 h-3.5 text-slate-500" />
                      <span>OpenAI API Key:</span>
                    </label>
                    <button
                      type="button"
                      onClick={handleTestApiKey}
                      disabled={testStatus === 'testing' || !openaiKey.trim()}
                      className="flex items-center space-x-1 text-[11px] font-semibold text-emerald-600 hover:text-emerald-800 disabled:opacity-50 transition-colors"
                    >
                      {testStatus === 'testing' ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <Zap className="w-3 h-3" />
                      )}
                      <span>Bağlantıyı Test Et</span>
                    </button>
                  </div>

                  <input
                    type="password"
                    value={openaiKey}
                    onChange={(e) => setOpenaiKey(e.target.value)}
                    placeholder="sk-proj-..."
                    className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
                  />
                  <p className="text-[10.5px] text-slate-400">
                    GPT-4o, GPT-4o-mini modelleri üzerinden derin otomotiv arıza analizi sağlar.
                  </p>
                </div>
              )}

              {/* Test Status Alert */}
              {testStatus !== 'idle' && (
                <div className={`p-2.5 rounded-lg border text-[11px] flex items-start space-x-1.5 ${
                  testStatus === 'testing'
                    ? 'bg-blue-50 border-blue-200 text-blue-800'
                    : testStatus === 'success'
                      ? 'bg-emerald-50 border-emerald-200 text-emerald-800 font-medium'
                      : 'bg-rose-50 border-rose-200 text-rose-800 font-medium'
                }`}>
                  {testStatus === 'testing' && <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0 mt-0.5" />}
                  {testStatus === 'success' && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0 mt-0.5" />}
                  {testStatus === 'error' && <AlertCircle className="w-3.5 h-3.5 text-rose-600 shrink-0 mt-0.5" />}
                  <span className="leading-snug">{testMessage}</span>
                </div>
              )}
            </>
          ) : (
            <>
              {/* Cloud SaaS Section */}
              <div className="space-y-3">
                {/* Server URL Input */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="font-semibold text-slate-700 flex items-center space-x-1.5">
                      <Server className="w-3.5 h-3.5 text-blue-600" />
                      <span>Universal-CAN-Cloud Sunucu URL:</span>
                    </label>
                    <button
                      type="button"
                      onClick={handleTestCloud}
                      disabled={cloudTestState === 'testing' || !cloudUrl.trim()}
                      className="flex items-center space-x-1 text-[11px] font-semibold text-blue-600 hover:text-blue-800 disabled:opacity-50 transition-colors"
                    >
                      {cloudTestState === 'testing' ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <RefreshCw className="w-3 h-3" />
                      )}
                      <span>Sunucuyu Test Et</span>
                    </button>
                  </div>

                  <input
                    type="text"
                    value={cloudUrl}
                    onChange={(e) => setCloudUrl(e.target.value)}
                    placeholder="http://127.0.0.1:8000"
                    className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
                  />
                </div>

                {/* Session Token Input */}
                <div className="space-y-1.5">
                  <label className="font-semibold text-slate-700 flex items-center space-x-1.5">
                    <Key className="w-3.5 h-3.5 text-slate-500" />
                    <span>Web Portalı Oturum Tokenı (Session Token):</span>
                  </label>
                  <input
                    type="password"
                    value={cloudToken}
                    onChange={(e) => setCloudToken(e.target.value)}
                    placeholder="Web SaaS portalından kopyaladığınız ucan_session tokenı..."
                    className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
                  />
                  <p className="text-[10.5px] text-slate-400">
                    Token Windows DPAPI ile donanımsal olarak şifrelenir (asla düz metin saklanmaz).
                  </p>
                </div>

                {/* Cloud Connection Test Alert */}
                {cloudTestState !== 'idle' && (
                  <div className={`p-2.5 rounded-lg border text-[11px] flex items-start space-x-1.5 ${
                    cloudTestState === 'testing'
                      ? 'bg-blue-50 border-blue-200 text-blue-800'
                      : cloudTestState === 'success'
                        ? 'bg-emerald-50 border-emerald-200 text-emerald-800 font-medium'
                        : 'bg-rose-50 border-rose-200 text-rose-800 font-medium'
                  }`}>
                    {cloudTestState === 'testing' && <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0 mt-0.5" />}
                    {cloudTestState === 'success' && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0 mt-0.5" />}
                    {cloudTestState === 'error' && <AlertCircle className="w-3.5 h-3.5 text-rose-600 shrink-0 mt-0.5" />}
                    <span className="leading-snug">{cloudTestMsg}</span>
                  </div>
                )}

                {/* Device & HWID Registration Card */}
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-1.5 font-bold text-slate-800 text-[11.5px]">
                      <Laptop className="w-3.5 h-3.5 text-slate-600" />
                      <span>Cihaz HWID Parmak İzi & Kayıt</span>
                    </div>
                    {cloudStatus?.hasDeviceToken ? (
                      <span className="text-[10px] font-semibold bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-full flex items-center space-x-1">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>Buluta Kayıtlı</span>
                      </span>
                    ) : (
                      <span className="text-[10px] font-semibold bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full">
                        Kayıtsız
                      </span>
                    )}
                  </div>

                  <div className="text-[10.5px] font-mono text-slate-500 truncate bg-white p-1.5 rounded border border-slate-200" title={cloudStatus?.hwid || ''}>
                    HWID: <strong>{cloudStatus?.hwid ? `${cloudStatus.hwid.slice(0, 8)}…` : 'Hesaplanıyor...'}</strong>
                  </div>

                  <div className="flex items-center justify-between pt-1">
                    <span className="text-[10.5px] text-slate-500">
                      Cihazı mevcut SaaS kiracınıza bağlayın.
                    </span>
                    <button
                      type="button"
                      onClick={handleRegisterDevice}
                      disabled={isActionLoading}
                      className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded font-bold text-[11px] shadow-xs flex items-center space-x-1 transition-colors disabled:opacity-50"
                    >
                      {isActionLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Laptop className="w-3 h-3" />}
                      <span>{cloudStatus?.hasDeviceToken ? 'Yeniden Kaydet' : 'Cihazı Kaydet'}</span>
                    </button>
                  </div>
                </div>

                {/* Ed25519 License Card */}
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-1.5 font-bold text-slate-800 text-[11.5px]">
                      <ShieldCheck className="w-3.5 h-3.5 text-blue-600" />
                      <span>Ed25519 Kriptografik Lisans</span>
                    </div>
                    {cloudStatus?.license ? (
                      <span className="text-[10px] font-bold bg-blue-100 text-blue-800 px-2 py-0.5 rounded-full font-mono uppercase">
                        {cloudStatus.license.tier}
                      </span>
                    ) : (
                      <span className="text-[10px] font-semibold bg-slate-200 text-slate-600 px-2 py-0.5 rounded-full">
                        Lisans Yok (Demo)
                      </span>
                    )}
                  </div>

                  {cloudStatus?.license ? (
                    <div className="space-y-1 bg-white p-2 rounded border border-slate-200 text-[10.5px]">
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
                          <span key={f} className="text-[9.5px] bg-slate-100 text-slate-600 px-1.5 py-0.2 rounded font-mono">
                            {f}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {/* Activate Form */}
                  <div className="space-y-1.5 pt-1">
                    <label className="text-[11px] font-semibold text-slate-700">
                      Sipariş Ref / Aktivasyon Kodu:
                    </label>
                    <div className="flex space-x-2">
                      <input
                        type="text"
                        value={licenseKeyInput}
                        onChange={(e) => setLicenseKeyInput(e.target.value)}
                        placeholder="ORD-2026-... veya lisans tokenı"
                        className="flex-1 bg-white border border-slate-200 rounded px-2.5 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                      <button
                        type="button"
                        onClick={handleActivateLicense}
                        disabled={isActionLoading || !licenseKeyInput.trim()}
                        className="px-3 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded font-bold text-[11px] shadow-xs flex items-center space-x-1 transition-colors disabled:opacity-50"
                      >
                        {isActionLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <ShieldCheck className="w-3 h-3" />}
                        <span>Aktive Et</span>
                      </button>
                    </div>
                  </div>
                </div>

                {/* Action Feedback Banner */}
                {actionFeedback && (
                  <div className={`p-2.5 rounded-lg border text-[11px] flex items-start space-x-1.5 ${
                    actionFeedback.type === 'success'
                      ? 'bg-emerald-50 border-emerald-200 text-emerald-800 font-medium'
                      : 'bg-rose-50 border-rose-200 text-rose-800 font-medium'
                  }`}>
                    {actionFeedback.type === 'success' ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0 mt-0.5" />
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
            className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs flex items-center space-x-1.5 shadow-xs transition-colors"
          >
            <Check className="w-3.5 h-3.5" />
            <span>Ayarları Kaydet</span>
          </button>
        </div>
      </div>
    </div>
  );
};

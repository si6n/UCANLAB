import React, { useState, useEffect, useRef } from 'react';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { CanSnifferTable } from './components/dashboard/CanSnifferTable';
import { SignalOscilloscope } from './components/dashboard/SignalOscilloscope';
import { AiCopilotPanel } from './components/dashboard/AiCopilotPanel';
import { EcuFlashingView } from './components/ecu/EcuFlashingView';
import { PinoutGuideView } from './components/pinout/PinoutGuideView';
import { ReportsExportView } from './components/reports/ReportsExportView';
import { SignalDiscoveryView } from './components/discovery/SignalDiscoveryView';
import { SettingsModal } from './components/modals/SettingsModal';

import { 
  CANFrame, 
  TelemetryPoint, 
  ScenarioType, 
  ActiveTab, 
  ChatMessage, 
  DiagnosticState,
  CopilotAction
} from './types/can';
import { CANSimulatorEngine } from './services/canSimulator';
import { DiagnosticEngine } from './services/diagnosticEngine';
import { DesktopBridge } from './services/bridge';

export const App: React.FC = () => {
  // Global Application State
  const [activeTab, setActiveTab] = useState<ActiveTab>('dashboard');
  const [channel, setChannel] = useState('vcan0');
  const [baudRate, setBaudRate] = useState('250 kbps');
  // H-11 (P1-9): API keys are no longer persisted in localStorage — the
  // WebView profile stores it unencrypted; the backend vault is the only
  // at-rest copy. The renderer keeps a transient session value only.
  const [apiKey, setApiKey] = useState('');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  // Vertical Resizer State for Dashboard (Sniffer vs Oscilloscope)
  const [snifferHeightPercent, setSnifferHeightPercent] = useState(55);
  const [isDraggingVertical, setIsDraggingVertical] = useState(false);
  const leftPanelRef = useRef<HTMLDivElement>(null);

  // Simulation & Telemetry State
  const [isSimulating, setIsSimulating] = useState(false);
  const [isEstopActive, setIsEstopActive] = useState(false);
  const [activeScenario, setActiveScenario] = useState<ScenarioType>('nominal');
  const [simulationSpeed, setSimulationSpeed] = useState<number>(1.0);
  const [busLoad, setBusLoad] = useState(0);
  const [totalPackets, setTotalPackets] = useState(0);
  const [errorCount, setErrorCount] = useState(0);
  const [frameRate, setFrameRate] = useState(0);

  // Buffer and Graph Data (Clean Live Start)
  const [frames, setFrames] = useState<CANFrame[]>([]);
  const [currentTelemetry, setCurrentTelemetry] = useState<TelemetryPoint | null>(null);
  const [telemetryHistory, setTelemetryHistory] = useState<TelemetryPoint[]>([]);

  // Engines
  const [simulator] = useState(() => new CANSimulatorEngine());
  const [diagnosticEngine] = useState(() => {
    const engine = new DiagnosticEngine();
    // H-11 (P1-9): legacy plaintext keys are scrubbed once and never read
    // back; only the non-sensitive provider preference persists.
    localStorage.removeItem('gemini_api_key');
    localStorage.removeItem('openai_api_key');
    localStorage.removeItem('cloud_session_token');
    const savedProvider = (localStorage.getItem('ai_provider') as 'gemini' | 'openai') || 'gemini';
    engine.setAiProvider(savedProvider);
    return engine;
  });

  // Diagnostic State & Chat (Clean Live Start)
  const [diagnosticState, setDiagnosticState] = useState<DiagnosticState>(() => 
    diagnosticEngine.evaluateSystemState('nominal', {
      timeSec: 0,
      timeFormatted: '0s',
      rpm: 0,
      turboBoostBar: 0,
      coolantTempC: 0,
      oilPressureBar: 0,
      busLoadPercent: 0,
      errorCount: 0
    })
  );

  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome-1',
      sender: 'copilot',
      timestamp: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
      isDtcCard: false,
      text: `**Universal CAN-Bus Teşhis & AI Copilot Hazır**

• **Durum:** CAN veri yolu dinleniyor (\`vcan0\`).
• **Rehberlik:** Canlı veri akışı başladığında veya sistemde bir DTC hata kodu tespit edildiğinde kök neden analizi ve adım adım onarım yönergeleri burada görüntülenecektir.
• *Aşağıdaki hızlı soru butonlarını kullanarak veya mesaj yazarak teknik sorular sorabilirsiniz.*`
    }
  ]);
  const [isAiLoading, setIsAiLoading] = useState(false);

  // Vertical Resizer Drag Effect
  useEffect(() => {
    if (!isDraggingVertical) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!leftPanelRef.current) return;
      const rect = leftPanelRef.current.getBoundingClientRect();
      const relativeY = e.clientY - rect.top;
      const newPercent = (relativeY / rect.height) * 100;
      // Clamp between 20% and 80%
      setSnifferHeightPercent(Math.max(20, Math.min(80, newPercent)));
    };

    const handleMouseUp = () => {
      setIsDraggingVertical(false);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDraggingVertical]);

  // Mount listeners for Telemetry & Real-time Frames
  useEffect(() => {
    simulator.subscribe({
      onNewFrame: (newFrame) => {
        setFrames((prev) => [...prev.slice(-199), newFrame]);
      },
      onNewFrameBatch: (batch) => {
        // F-35: single state update for the whole 5-frame batch
        setFrames((prev) => [...prev.slice(-(200 - batch.length)), ...batch].slice(-200));
      },
      onTelemetryUpdate: (point) => {
        setCurrentTelemetry(point);
        setTelemetryHistory((prev) => [...prev.slice(-149), point]);
      },
      onStatsUpdate: (stats) => {
        setTotalPackets(stats.totalPackets);
        setBusLoad(stats.busLoad);
        setErrorCount(stats.errorCount);
        setFrameRate(stats.frameRate);
      }
    });

    // Native Python window listener hooks
    window.onNewCanFrame = (f) => {
      setFrames((prev) => [...prev.slice(-199), f]);
    };

    // E13: batched live frames — ONE call per 50ms tick from Python (mirrors
    // the F-35 single-state-update pattern used by the simulator).
    window.onNewCanFrames = (batch) => {
      if (!Array.isArray(batch) || batch.length === 0) return;
      setFrames((prev) => [...prev.slice(-(200 - batch.length)), ...batch].slice(-200));
    };

    window.onTelemetryTick = (p) => {
      setCurrentTelemetry(p);
      setTelemetryHistory((prev) => [...prev.slice(-149), p]);
    };

    window.onStatsTick = (s) => {
      setTotalPackets(s.totalPackets);
      setBusLoad(s.busLoad);
      setErrorCount(s.errorCount);
      setFrameRate(s.frameRate);
    };

    return () => {
      simulator.destroy();
    };
  }, [simulator]);

  // UI-alive heartbeat for the TX Watchdog (F-16 / E-11):
  // driven by the render/rAF loop, NOT a blind setInterval — if the UI
  // genuinely freezes (main-thread block), the pulse stops and the Python
  // watchdog expires (800ms timeout, 250ms pulse => 550ms tolerance).
  useEffect(() => {
    let alive = true;
    let lastSent = 0;
    const tick = () => {
      const now = performance.now();
      if (alive && now - lastSent >= 250 && window.pywebview?.api?.heartbeat) {
        lastSent = now;
        window.pywebview.api.heartbeat().catch(() => {
          // Bridge hiccup: next frame retries; watchdog tolerates misses.
        });
      }
      requestAnimationFrame(tick);
    };
    const raf = requestAnimationFrame(tick);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
    };
  }, []);

  // Update diagnostic state upon scenario change
  useEffect(() => {
    if (currentTelemetry) {
      const evalState = diagnosticEngine.evaluateSystemState(activeScenario, currentTelemetry);
      setDiagnosticState(evalState);
    }
  }, [activeScenario, currentTelemetry, diagnosticEngine]);

  // Handlers
  const handleToggleSimulator = async () => {
    // P0-1 (REVIEW C-1): the simulator toggle may no longer clear a latched
    // E-Stop — the backend refuses the toggle while engaged, and the local
    // E-Stop flag is never cleared implicitly here either.
    const isNativeResult = await DesktopBridge.toggleSimulator();
    const nextState = isNativeResult !== null ? isNativeResult : simulator.toggleRunning();
    setIsSimulating(nextState);
    if (nextState) {
      simulator.resume();
    } else {
      simulator.pause();
      setBusLoad(0);
      setFrameRate(0);
    }
  };

  const handleEstop = async () => {
    await DesktopBridge.triggerEstop();
    simulator.emergencyStop();
    setIsEstopActive(true);
    setIsSimulating(false);
    setBusLoad(0);
    setFrameRate(0);
  };

  const handleSelectScenario = async (scenario: ScenarioType) => {
    // P0-1 (REVIEW C-1): a scenario switch no longer clears the local E-Stop
    // flag implicitly — the backend keeps the latch engaged and only the
    // challenge/response reset flow may clear it.
    await DesktopBridge.selectScenario(scenario);
    simulator.setScenario(scenario);
    setActiveScenario(scenario);
    setIsSimulating(true);
    simulator.resume();
  };

  const handleChangeSpeed = (speed: number) => {
    setSimulationSpeed(speed);
    simulator.setSpeedMultiplier(speed);
  };

  const handleInjectFault = (type: any) => {
    simulator.injectFault(type);
  };

  const handleClearBuffer = () => {
    setFrames([]);
  };

  const handleRescan = () => {
    const point = currentTelemetry || {
      timeSec: 0,
      timeFormatted: '0s',
      rpm: 0,
      turboBoostBar: 0,
      coolantTempC: 0,
      oilPressureBar: 0,
      busLoadPercent: 0,
      errorCount: 0
    };
    const updated = diagnosticEngine.evaluateSystemState(activeScenario, point);
    setDiagnosticState({
      ...updated,
      lastScanTimestamp: new Date().toLocaleTimeString('tr-TR', { hour12: false })
    });
  };

  const handleSendMessage = async (query: string) => {
    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      timestamp: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
      text: query
    };

    setChatMessages((prev) => [...prev, userMsg]);
    setIsAiLoading(true);

    try {
      const response = await diagnosticEngine.generateCopilotResponse(query, diagnosticState);
      setChatMessages((prev) => [...prev, response]);
    } finally {
      setIsAiLoading(false);
    }
  };

  const handleExecuteAction = async (action: CopilotAction) => {
    setIsAiLoading(true);
    try {
      const res = await DesktopBridge.executeDiagnosticAction(action, true);
      const statusIcon = res.success ? '✅' : '❌';
      const detailText = res.message || res.error || (res.success ? 'İşlem başarıyla tamamlandı.' : 'İşlem başarısız oldu.');
      let resultText = `**${statusIcon} Teşhis Aksiyonu Sonucu: ${action.label}**\n\n` +
        `• **Durum:** ${res.success ? 'Başarılı' : 'Başarısız'}\n` +
        `• **Detay:** ${detailText}`;

      if (res.data && Object.keys(res.data).length > 0) {
        resultText += `\n• **Dönen Veri:** \`${JSON.stringify(res.data)}\``;
      }

      const resultMsg: ChatMessage = {
        id: `action-res-${Date.now()}`,
        sender: 'copilot',
        timestamp: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
        text: resultText
      };

      setChatMessages((prev) => [...prev, resultMsg]);

      // If DTCs were cleared or scenario reset, trigger rescan to update UI state
      if (action.action_type === 'uds_clear_dtc' || action.action_type === 'j1939_clear_dtc') {
        if (res.success) {
          handleRescan();
        }
      }
    } catch (err: any) {
      const errMsg: ChatMessage = {
        id: `action-err-${Date.now()}`,
        sender: 'copilot',
        timestamp: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
        text: `❌ **Aksiyon Yürütülemedi:** ${err?.message || 'Bilinmeyen hata'}`
      };
      setChatMessages((prev) => [...prev, errMsg]);
    } finally {
      setIsAiLoading(false);
    }
  };

  const handleAskCopilotAboutFrame = (frame: CANFrame) => {
    const prompt = `Lütfen şu CAN karesini detaylı analiz et:\n\n` +
      `• CAN ID: ${frame.canIdHex} (${frame.frameType})\n` +
      `• Kanal: ${frame.channel} | Yön: ${frame.dir} | DLC: ${frame.dlc}\n` +
      `• Hex Payload: ${frame.dataHex.join(' ')}\n` +
      `• ASCII: ${frame.ascii}\n\n` +
      `Bu mesajın olası protokolünü, içerdiği fiziksel sinyalleri ve varsa aktif arıza kodunu (DTC / SPN / FMI) açıkla.`;
    handleSendMessage(prompt);
  };

  const handleSaveSettings = async (settings: any) => {
    setChannel(settings.channel);
    setBaudRate(settings.baudRate);
    setApiKey(settings.apiKey || settings.geminiApiKey || settings.openaiApiKey || '');

    // H-11 (P1-9): keys flow to the in-memory engine and the backend vault
    // (via updateSettings) only — never to localStorage.
    if (settings.provider) {
      diagnosticEngine.setAiProvider(settings.provider);
      localStorage.setItem('ai_provider', settings.provider);
    }
    if (settings.geminiApiKey !== undefined) {
      diagnosticEngine.setApiKey(settings.geminiApiKey);
    }
    if (settings.openaiApiKey !== undefined) {
      diagnosticEngine.setOpenAiApiKey(settings.openaiApiKey);
    }
    await DesktopBridge.updateSettings(settings);
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#FAFBFC] text-slate-800 select-none">
      {/* 1. Vertical Left Sidebar Navigation */}
      <Sidebar
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        channel={channel}
        isSimulating={isSimulating}
        isEstopActive={isEstopActive}
      />

      {/* 2. Main Column: Sticky Topbar + Content Area */}
      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          channel={channel}
          baudRate={baudRate}
          busLoad={busLoad}
          totalPackets={totalPackets}
          isSimulating={isSimulating}
          isEstopActive={isEstopActive}
          activeScenario={activeScenario}
          simulationSpeed={simulationSpeed}
          onToggleSimulator={handleToggleSimulator}
          onSelectScenario={handleSelectScenario}
          onEstop={handleEstop}
          onChangeSpeed={handleChangeSpeed}
          onInjectFault={handleInjectFault}
          onOpenSettings={() => setIsSettingsOpen(true)}
        />

        {/* 3. Main Views Container */}
        <main className="relative flex-1 overflow-hidden p-4">
            {activeTab === 'dashboard' && (
              <div className="grid h-full grid-cols-1 gap-3 lg:grid-cols-12">
                {/* Left: Sniffer (top) + thin resize divider + Oscilloscope (bottom) */}
                <div
                  ref={leftPanelRef}
                  className="flex h-full min-h-0 flex-col lg:col-span-7"
                >
                  <div
                    style={{ height: `${snifferHeightPercent}%` }}
                    className="flex min-h-[140px] flex-col overflow-hidden"
                  >
                    <CanSnifferTable
                      frames={frames}
                      isStreaming={isSimulating}
                      frameRate={frameRate}
                      totalDisplayedCount={frames.length}
                      errorFrameCount={errorCount}
                      onToggleStreaming={handleToggleSimulator}
                      onClearBuffer={handleClearBuffer}
                      onAskCopilot={handleAskCopilotAboutFrame}
                    />
                  </div>

                  {/* Thin draggable divider */}
                  <div
                    onMouseDown={() => setIsDraggingVertical(true)}
                    className={`group relative my-2 h-px shrink-0 cursor-row-resize transition-colors ${
                      isDraggingVertical ? 'bg-brand-500' : 'bg-slate-200 hover:bg-brand-400'
                    }`}
                    title="Sniffer ve Osiloskop boyutunu ayarlamak için sürükleyin"
                  >
                    <div className="absolute inset-x-0 -top-2 h-5" />
                    <div
                      className={`absolute left-1/2 top-1/2 h-0.5 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full transition-colors ${
                        isDraggingVertical ? 'bg-brand-600' : 'bg-slate-300 group-hover:bg-brand-500'
                      }`}
                    />
                  </div>

                  <div
                    style={{ height: `calc(${100 - snifferHeightPercent}% - 20px)` }}
                    className="flex min-h-[140px] flex-col overflow-hidden"
                  >
                    <SignalOscilloscope
                      currentPoint={currentTelemetry}
                      history={telemetryHistory}
                      onAskCopilot={handleSendMessage}
                    />
                  </div>
                </div>

                {/* Right: AI Diagnostic Copilot */}
                <div className="h-full overflow-hidden lg:col-span-5">
                  <AiCopilotPanel
                    diagnosticState={diagnosticState}
                    chatMessages={chatMessages}
                    isAiLoading={isAiLoading}
                    onRescan={handleRescan}
                    onSendMessage={handleSendMessage}
                    onExecuteAction={handleExecuteAction}
                  />
                </div>
              </div>
            )}

            {activeTab === 'signal_discovery' && (
              <SignalDiscoveryView
                latestFrame={frames[frames.length - 1] || null}
                frames={frames}
                onStimulusChange={(lvl) => simulator.setStimulusLevel(lvl)}
                onAskCopilot={handleSendMessage}
              />
            )}

            {activeTab === 'ecu_flashing' && <EcuFlashingView />}
            {activeTab === 'pinout_guide' && <PinoutGuideView />}
            {activeTab === 'reports' && <ReportsExportView frames={frames} />}
        </main>
      </div>

      {/* Settings Modal */}
      <SettingsModal
        isOpen={isSettingsOpen}
        channel={channel}
        baudRate={baudRate}
        apiKey={apiKey}
        onClose={() => setIsSettingsOpen(false)}
        onSave={handleSaveSettings}
      />
    </div>
  );
};

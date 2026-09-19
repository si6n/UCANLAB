import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Toolbar } from './components/Toolbar';
import { SideRail } from './components/SideRail';
import { DataTable } from './components/dashboard/DataTable';
import { SummaryStrip } from './components/dashboard/SummaryStrip';
import { ScopePanel } from './components/dashboard/ScopePanel';
import { StatusBar } from './components/StatusBar';

// Other subsystem views
import { SettingsView } from './components/settings/SettingsView';
import { EcuFlashingView } from './components/ecu/EcuFlashingView';
import { PinoutGuideView } from './components/pinout/PinoutGuideView';
import { ReportsExportView } from './components/reports/ReportsExportView';
import { SignalDiscoveryView } from './components/discovery/SignalDiscoveryView';

// Domain constants and realistic packet generator
import {
  INITIAL_PACKET_ROWS,
  CanPacketRow,
  ANOMALY_IDS,
  formatAscii,
} from './data/constants';
import { CANFrame, ChatMessage, CopilotAction, DiagnosticState, ScenarioType, FaultInjectionType } from './types/can';
import { DesktopBridge } from './services/bridge';
import { DiagnosticEngine } from './services/diagnosticEngine';
import { AiCopilotPanel } from './components/dashboard/AiCopilotPanel';
import { fromNativeFrame, fromNativeFrames } from './services/nativeFrameAdapter';

export const App: React.FC = () => {
  // Theme State (Zeron Dark / Zeron Light)
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try {
      const saved = localStorage.getItem('ucanlab.theme');
      if (saved === 'dark' || saved === 'light') return saved;
      return 'dark'; // Zeron Dark default
    } catch {
      return 'dark';
    }
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    try {
      localStorage.setItem('ucanlab.theme', theme);
    } catch {}
  }, [theme]);

  const handleToggleTheme = useCallback(() => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  // Navigation State
  const [activeTab, setActiveTab] = useState<string>('dashboard');

  // Channel & Hardware State
  const [channel, setChannel] = useState('vcan0');
  const [baudRate, setBaudRate] = useState('250 kbps');

  // Simulation & Stream State
  const [isSimulating, setIsSimulating] = useState(false);
  const [isEstopActive, setIsEstopActive] = useState(false);
  const [activeScenario, setActiveScenario] = useState<ScenarioType>('nominal');
  const [busLoad, setBusLoad] = useState(14);
  const [totalPackets, setTotalPackets] = useState(15553);
  const [errorCount, setErrorCount] = useState(5);
  const [frameRate, setFrameRate] = useState(0);

  // Packet Buffer
  const [packetRows, setPacketRows] = useState<CanPacketRow[]>(INITIAL_PACKET_ROWS);
  const [selectedRowId, setSelectedRowId] = useState<string | null>(null);

  // Copilot & Diagnostics State
  const [isCopilotOpen, setIsCopilotOpen] = useState(false);
  const [diagnosticEngine] = useState(() => {
    localStorage.removeItem('gemini_api_key');
    localStorage.removeItem('openai_api_key');
    localStorage.removeItem('cloud_session_token');
    localStorage.removeItem('ai_provider');
    return new DiagnosticEngine();
  });
  const [diagnosticState, setDiagnosticState] = useState<DiagnosticState>(() =>
    diagnosticEngine.evaluateSystemState('nominal', {
      timeSec: 0,
      timeFormatted: '0s',
      rpm: 850,
      turboBoostBar: 0.2,
      coolantTempC: 88,
      oilPressureBar: 3.8,
      busLoadPercent: 14,
      errorCount: 5,
    })
  );
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome-1',
      sender: 'copilot',
      timestamp: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
      isDtcCard: false,
      text: `**Universal CAN Teşhis Asistanı Hazır**\n\nCAN veri yolundaki anomaliler, hata kodları (DTC) veya protokoller hakkında soru sorabilir, canlı telemetri analizi başlatabilirsiniz. Aşağıdaki hazır başlıklardan birini seçebilir veya doğrudan yazabilirsiniz.`,
    },
  ]);
  const [isAiLoading, setIsAiLoading] = useState(false);

  // Layout Resizer State
  const [snifferHeightPercent, setSnifferHeightPercent] = useState(55);
  const [isDraggingVertical, setIsDraggingVertical] = useState(false);
  const dashboardContainerRef = useRef<HTMLDivElement>(null);

  // Rail collapse state (FIX 6) — persisted, icon-only ~48px vs full ~168px
  const [isRailCollapsed, setIsRailCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem('ucanlab.railCollapsed') === '1';
    } catch {
      return false;
    }
  });
  const handleToggleRail = useCallback(() => {
    setIsRailCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('ucanlab.railCollapsed', next ? '1' : '0');
      } catch { /* storage unavailable — collapse still works for session */ }
      return next;
    });
  }, []);

  // Keyboard shortcut listener: Space to toggle feed, Esc to clear selection
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement;
      const isInputFocused =
        activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA');

      if (e.code === 'Space' && !isInputFocused) {
        e.preventDefault();
        if (!isEstopActive) {
          setIsSimulating((prev) => !prev);
        }
      } else if (e.key === 'Escape') {
        setSelectedRowId(null);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isEstopActive]);

  // UI-alive heartbeat for the TX Watchdog (ISO 26262 functional safety rule)
  useEffect(() => {
    let alive = true;
    let lastSent = 0;
    const tick = () => {
      const now = performance.now();
      if (alive && now - lastSent >= 250 && window.pywebview?.api?.heartbeat) {
        lastSent = now;
        window.pywebview.api.heartbeat().catch(() => {
          // Bridge hiccup: next frame retries; watchdog tolerates misses
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

  // Vertical Resizer Drag Effect
  useEffect(() => {
    if (!isDraggingVertical) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!dashboardContainerRef.current) return;
      const rect = dashboardContainerRef.current.getBoundingClientRect();
      const relativeY = e.clientY - rect.top;
      const newPercent = (relativeY / rect.height) * 100;
      setSnifferHeightPercent(Math.max(25, Math.min(75, newPercent)));
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

  // Realistic synthetic packet generator when running
  useEffect(() => {
    if (!isSimulating || isEstopActive) {
      setFrameRate(0);
      return;
    }

    setFrameRate(44);

    const interval = setInterval(() => {
      setPacketRows((prev) => {
        const now = (74.08 + (prev.length * 0.0035)).toFixed(4);
        const rand = Math.random();
        let newCanId = '0x18FEF200';
        let frameType: 'Ext' | 'Std' = 'Ext';
        let direction: 'RX' | 'TX' = 'RX';
        let isAnomaly = false;
        let anomalyDesc: string | undefined = undefined;

        if (rand < 0.12) {
          // Inject periodic anomaly
          newCanId = '0x18FF0501';
          isAnomaly = true;
          anomalyDesc = 'ECM DTC Bildirimi (Tekleme Çentiği)';
        } else if (rand < 0.22) {
          newCanId = '0x0CF00400';
          isAnomaly = true;
          anomalyDesc = 'EEC1 Motor Devri Ani Düşüşü';
        } else if (rand < 0.35) {
          newCanId = '0x7DF';
          frameType = 'Std';
          direction = 'TX';
        } else if (rand < 0.50) {
          newCanId = '0x19F50200';
        }

        const rawBytes = Array.from({ length: 8 }, () =>
          Math.floor(Math.random() * 256)
            .toString(16)
            .padStart(2, '0')
            .toUpperCase()
        );

        const newRow: CanPacketRow = {
          id: `pkt-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`,
          timestamp: `${now}s`,
          timeSec: parseFloat(now),
          channel,
          canId: newCanId,
          frameType,
          direction,
          dlc: 8,
          dataBytes: rawBytes,
          ascii: formatAscii(rawBytes),
          isAnomaly,
          anomalyDescription: anomalyDesc,
        };

        // Cap buffer to last 300 rows for 60fps performance
        return [newRow, ...prev.slice(0, 299)];
      });

      setTotalPackets((prev) => prev + 1);
      setBusLoad(Math.floor(12 + Math.random() * 6));
    }, 45);

    return () => clearInterval(interval);
  }, [isSimulating, isEstopActive, channel]);

  // Native pywebview bridge listener hooks
  useEffect(() => {
    window.onNewCanFrame = (f: any) => {
      const adapted = fromNativeFrame(f);
      if (!adapted) return;
      const isAnomaly = ANOMALY_IDS.has(adapted.canIdHex);
      const row: CanPacketRow = {
        id: `native-${adapted.id || Date.now()}`,
        timestamp: adapted.timeFormatted || `${adapted.timeSec.toFixed(4)}s`,
        timeSec: adapted.timeSec,
        channel: adapted.channel || channel,
        canId: adapted.canIdHex,
        frameType: adapted.frameType === 'Ext' ? 'Ext' : 'Std',
        direction: adapted.dir,
        dlc: adapted.dlc,
        dataBytes: adapted.dataHex,
        ascii: adapted.ascii,
        isAnomaly,
      };
      setPacketRows((prev) => [row, ...prev.slice(0, 299)]);
      setTotalPackets((prev) => prev + 1);
    };

    window.onNewCanFrames = (batch: any[]) => {
      if (!Array.isArray(batch) || batch.length === 0) return;
      const adaptedList = fromNativeFrames(batch);
      if (adaptedList.length === 0) return;

      const newRows: CanPacketRow[] = adaptedList.map((adapted) => ({
        id: `native-${adapted.id || Math.random()}`,
        timestamp: adapted.timeFormatted || `${adapted.timeSec.toFixed(4)}s`,
        timeSec: adapted.timeSec,
        channel: adapted.channel || channel,
        canId: adapted.canIdHex,
        frameType: adapted.frameType === 'Ext' ? 'Ext' : 'Std',
        direction: adapted.dir,
        dlc: adapted.dlc,
        dataBytes: adapted.dataHex,
        ascii: adapted.ascii,
        isAnomaly: ANOMALY_IDS.has(adapted.canIdHex),
      }));

      setPacketRows((prev) => [...newRows, ...prev].slice(0, 300));
      setTotalPackets((prev) => prev + newRows.length);
    };

    window.onStatsTick = (s: any) => {
      if (s.totalPackets !== undefined) setTotalPackets(s.totalPackets);
      if (s.busLoad !== undefined) setBusLoad(s.busLoad);
      if (s.errorCount !== undefined) setErrorCount(s.errorCount);
      if (s.frameRate !== undefined) setFrameRate(s.frameRate);
    };
  }, [channel]);

  // Handlers
  const handleToggleSimulator = async () => {
    if (isEstopActive) return;
    const isNativeResult = await DesktopBridge.toggleSimulator();
    const next = isNativeResult !== null ? isNativeResult : !isSimulating;
    setIsSimulating(next);
  };

  const handleEstop = async () => {
    await DesktopBridge.triggerEstop();
    setIsEstopActive(true);
    setIsSimulating(false);
    setFrameRate(0);
  };

  const handleSelectScenario = async (scenario: ScenarioType) => {
    setActiveScenario(scenario);
    await DesktopBridge.selectScenario(scenario);
    const updated = diagnosticEngine.evaluateSystemState(scenario, {
      timeSec: 0,
      timeFormatted: '0s',
      rpm: scenario === 'nominal' ? 850 : scenario === 'misfire_p0300' ? 1420 : 2100,
      turboBoostBar: scenario === 'overboost' ? 2.45 : 0.8,
      coolantTempC: scenario === 'overheat' ? 112 : 88,
      oilPressureBar: 3.5,
      busLoadPercent: scenario === 'bus_surge' ? 88 : 16,
      errorCount: scenario === 'bus_surge' ? 142 : errorCount,
    });
    setDiagnosticState(updated);
    if (!isSimulating && !isEstopActive) {
      setIsSimulating(true);
      await DesktopBridge.toggleSimulator();
    }
  };

  const handleInjectFault = async (faultType: FaultInjectionType) => {
    await DesktopBridge.injectFault(faultType);

    const nowSec = (74.08 + packetRows.length * 0.0035).toFixed(4);
    let injectedId = '0x00000000';
    let anomalyDesc = 'CAN Fiziksel Hata Karesi (Error Frame)';
    let dataBytes = ['00', '00', '00', '00', '00', '00', '00', '00'];

    if (faultType === 'error_frame') {
      injectedId = '0x00000000';
      anomalyDesc = 'CAN Fiziksel Hat Hata Karesi (Active Error Flag)';
      setErrorCount((prev) => prev + 1);
    } else if (faultType === 'dtc_fault') {
      injectedId = '0x18FECA00';
      anomalyDesc = 'J1939 DM1 Aktif Arıza Kodu (SPN 100 FMI 1)';
      dataBytes = ['04', 'FF', '64', '00', '01', '01', 'FF', 'FF'];
    } else if (faultType === 'sensor_freeze') {
      injectedId = '0x0CF00400';
      anomalyDesc = 'Sensör Sinyal Donması / Değer Değişmiyor';
      dataBytes = ['F0', '7D', '7D', '25', '4B', 'FF', 'FF', 'FF'];
    } else if (faultType === 'babbling_surge') {
      injectedId = '0x18FF03B0';
      anomalyDesc = 'Babbling Node Ağ Taşması (%85+ Yük)';
      setBusLoad(89);
      setErrorCount((prev) => prev + 12);
    } else if (faultType === 'wiring_dropout') {
      injectedId = '0x18EAFFFE';
      anomalyDesc = 'Kesintili Hat & Adres Çakışması (PGN 59904 NACK)';
      dataBytes = ['00', 'EE', '00'];
    }

    const injectedRow: CanPacketRow = {
      id: `fault-${Date.now()}`,
      timestamp: `${nowSec}s`,
      timeSec: parseFloat(nowSec),
      channel,
      canId: injectedId,
      frameType: injectedId === '0x00000000' ? 'Std' : 'Ext',
      direction: 'RX',
      dlc: dataBytes.length,
      dataBytes,
      ascii: '........',
      isAnomaly: true,
      anomalyDescription: anomalyDesc,
    };

    setPacketRows((prev) => [injectedRow, ...prev].slice(0, 300));
    setTotalPackets((prev) => prev + 1);
  };

  const handleClearBuffer = () => {
    setPacketRows([]);
    setSelectedRowId(null);
  };

  const handleSaveSettings = async (settings: any) => {
    setChannel(settings.channel);
    setBaudRate(settings.baudRate);
    await DesktopBridge.updateSettings(settings);
  };

  const handleRescan = () => {
    const updated = diagnosticEngine.evaluateSystemState(activeScenario, {
      timeSec: 0,
      timeFormatted: '0s',
      rpm: activeScenario === 'nominal' ? 850 : 1850,
      turboBoostBar: activeScenario === 'overboost' ? 2.45 : 0.8,
      coolantTempC: activeScenario === 'overheat' ? 112 : 88,
      oilPressureBar: 3.8,
      busLoadPercent: busLoad,
      errorCount: errorCount,
    });
    setDiagnosticState({
      ...updated,
      lastScanTimestamp: new Date().toLocaleTimeString('tr-TR', { hour12: false }),
    });
  };

  const handleSendMessage = async (query: string) => {
    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      timestamp: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
      text: query,
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
      let resultText = `**${statusIcon} Teşhis Aksiyonu: ${action.label}**\n\n• **Durum:** ${res.success ? 'Başarılı' : 'Başarısız'}\n• **Detay:** ${detailText}`;

      const resultMsg: ChatMessage = {
        id: `action-res-${Date.now()}`,
        sender: 'copilot',
        timestamp: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
        text: resultText,
      };

      setChatMessages((prev) => [...prev, resultMsg]);
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
        text: `❌ **Aksiyon Yürütülemedi:** ${err?.message || 'Bilinmeyen hata'}`,
      };
      setChatMessages((prev) => [...prev, errMsg]);
    } finally {
      setIsAiLoading(false);
    }
  };

  // Convert CanPacketRow to CANFrame for legacy export/discovery views if needed
  const canFramesCompat: CANFrame[] = packetRows.map((r, i) => ({
    id: `compat-${r.id}-${i}`,
    timeSec: r.timeSec,
    timeFormatted: r.timestamp,
    channel: r.channel,
    canIdHex: r.canId,
    canIdDec: parseInt(r.canId, 16) || 0,
    dlc: r.dlc,
    dataHex: r.dataBytes,
    ascii: r.ascii,
    dir: r.direction,
    frameType: r.frameType === 'Ext' ? 'Ext' : 'Std',
    isErrorFrame: r.isAnomaly,
  }));

  const anomalyCount = packetRows.filter((r) => r.isAnomaly).length;

  return (
    <div className="glass-workspace relative flex h-screen w-screen flex-row overflow-hidden text-text-body select-none">
      {/* Ambient corner glow behind everything */}
      <div className="app-ambient-glow" aria-hidden="true" />

      {/* Left Side Rail — continuous full-height column, integrated header with PanelLeft, zero cut lines */}
      <SideRail
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        channel={channel}
        isSimulating={isSimulating}
        isEstopActive={isEstopActive}
        collapsed={isRailCollapsed}
        onToggleCollapse={handleToggleRail}
      />

      {/* Right Column: Toolbar + Main Workspace + StatusBar */}
      <div className="relative flex flex-1 min-w-0 flex-col overflow-hidden" style={{ zIndex: 10 }}>
        {/* Top bar over the main workspace: Yük, Paket, E-STOP, Başlat, Hata, Sun/Moon, Copilot, Window controls */}
        <div className="relative shrink-0" style={{ zIndex: 20 }}>
          <Toolbar
            channel={channel}
            baudRate={baudRate}
            busLoad={busLoad}
            totalPackets={totalPackets}
            isSimulating={isSimulating}
            isEstopActive={isEstopActive}
            activeScenario={activeScenario}
            isCopilotOpen={isCopilotOpen}
            theme={theme}
            onToggleTheme={handleToggleTheme}
            onToggleSimulator={handleToggleSimulator}
            onSelectScenario={handleSelectScenario}
            onInjectFault={handleInjectFault}
            onEstop={handleEstop}
            onToggleCopilot={() => setIsCopilotOpen((prev) => !prev)}
          />
        </div>

        {/* Main Workspace + Copilot Drawer */}
        <div className="relative flex flex-1 min-h-0 w-full overflow-hidden">
          <main className="relative flex flex-1 min-w-0 flex-col overflow-hidden bg-transparent p-3">
            {activeTab === 'dashboard' && (
              <div
                ref={dashboardContainerRef}
                className="flex h-full min-h-0 flex-col"
              >
              {/* Panel #1: Sniffer Table + Summary Strip — soft frosted glass card */}
              <div
                style={{ height: `${snifferHeightPercent}%` }}
                className="relative flex min-h-[180px] flex-col overflow-hidden rounded-[12px] glass-panel"
              >
                <DataTable
                  rows={packetRows}
                  isStreaming={isSimulating}
                  frameRate={frameRate}
                  onToggleStreaming={handleToggleSimulator}
                  onClearBuffer={handleClearBuffer}
                  selectedRowId={selectedRowId}
                  onSelectRow={setSelectedRowId}
                  onAskCopilot={(prompt) => {
                    setIsCopilotOpen(true);
                    handleSendMessage(prompt);
                  }}
                />
                <SummaryStrip
                  totalDisplayed={packetRows.length}
                  anomalyCount={anomalyCount}
                  busErrorCount={errorCount}
                />
              </div>

              {/* High-Precision Floating Pill Resizer — eliminates harsh horizontal cut line */}
              <div
                onMouseDown={() => setIsDraggingVertical(true)}
                className="group relative my-1.5 h-2.5 shrink-0 cursor-row-resize flex items-center justify-center select-none"
                title="Sniffer ve Osiloskop boyutunu ayarlamak için sürükleyin"
              >
                <div
                  className={`h-1 w-10 rounded-full transition-all duration-200 ${
                    isDraggingVertical
                      ? 'bg-accent scale-x-125 w-14'
                      : 'bg-border-strong/50 group-hover:bg-accent/70'
                  }`}
                />
              </div>

              {/* Panel #2: Graph & Signal Analysis Scope — soft frosted glass card */}
              <div
                style={{ height: `calc(${100 - snifferHeightPercent}% - 14px)` }}
                className="relative flex min-h-[180px] flex-col overflow-hidden rounded-[12px] glass-panel"
              >
                <ScopePanel
                  isStreaming={isSimulating}
                  onAnalyzeFault={() => setIsCopilotOpen(true)}
                />
              </div>
            </div>
          )}

          {activeTab === 'signal_discovery' && (
            <div className="h-full overflow-hidden rounded-[12px] glass-panel p-3">
              <SignalDiscoveryView
                latestFrame={canFramesCompat[0] || null}
                frames={canFramesCompat}
                onStimulusChange={() => {}}
                onAskCopilot={(prompt) => {
                  setIsCopilotOpen(true);
                  handleSendMessage(prompt);
                }}
              />
            </div>
          )}

          {activeTab === 'ecu_flashing' && (
            <div className="h-full overflow-hidden rounded-[12px] glass-panel p-3">
              <EcuFlashingView />
            </div>
          )}

          {activeTab === 'pinout_guide' && (
            <div className="h-full overflow-hidden rounded-[12px] glass-panel p-3">
              <PinoutGuideView />
            </div>
          )}

          {activeTab === 'reports' && (
            <div className="h-full overflow-hidden rounded-[12px] glass-panel p-3">
              <ReportsExportView frames={canFramesCompat} />
            </div>
          )}

          {activeTab === 'settings' && (
            <div className="h-full overflow-hidden rounded-[12px] glass-panel p-3">
              <SettingsView
                channel={channel}
                baudRate={baudRate}
                onSave={handleSaveSettings}
              />
            </div>
          )}
        </main>

        {/* Right Drawer: AI Diagnostic Copilot — Smooth desktop width transition matching SideRail */}
        <aside
          className={`copilot-drawer relative my-3 flex shrink-0 flex-col overflow-hidden rounded-[12px] glass-panel ${
            isCopilotOpen
              ? 'w-[420px] mr-3 p-2.5 opacity-100 pointer-events-auto border border-border shadow-2xl'
              : 'w-0 mr-0 p-0 opacity-0 pointer-events-none border-0 shadow-none'
          }`}
        >
          <div className="flex h-full w-[420px] min-w-[420px] flex-col overflow-hidden">
            <AiCopilotPanel
              diagnosticState={diagnosticState}
              chatMessages={chatMessages}
              isAiLoading={isAiLoading}
              onRescan={handleRescan}
              onSendMessage={handleSendMessage}
              onExecuteAction={handleExecuteAction}
              onClose={() => setIsCopilotOpen(false)}
            />
          </div>
        </aside>
      </div>

      {/* 3. Global Status Bar (Bottom Strip) */}
      <StatusBar
        channel={channel}
        isSimulating={isSimulating}
        isEstopActive={isEstopActive}
        nodeName="DESKTOP-CAN-NODE"
      />
    </div>
  </div>
);
};

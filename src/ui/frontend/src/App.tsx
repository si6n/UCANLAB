import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { PanelLeft, PanelRight, Moon, Sun, Minus, Square, X, Sparkles, RotateCw, Activity } from 'lucide-react';
import { TAB_LABELS } from './components/Toolbar';
import { SideRail } from './components/SideRail';
import { DataTable } from './components/dashboard/DataTable';
import { SummaryStrip } from './components/dashboard/SummaryStrip';
import { ScopePanel } from './components/dashboard/ScopePanel';

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
    // AGENTS.md §2.8: the AI layer is FULLY OFFLINE — the cloud-LLM narration
    // layer (Gemini/OpenAI) was removed. These keys are purged defensively so
    // an installation upgrading from an older build cannot leave a usable
    // provider key in localStorage.
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

  // Rail collapse state — default collapsed (when open: fixed 25% width)
  const [isRailCollapsed, setIsRailCollapsed] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem('ucanlab.railCollapsed');
      if (saved !== null) return saved === '1';
      return true;
    } catch {
      return true;
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
    }, 120);

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

  // I-13 (bounded): render/ingest decoupling. canFramesCompat used to
  // re-map every row on every render (incl. 45ms ingest ticks); memoize on
  // packetRows with stable keys (no per-render index suffix) so downstream
  // views skip re-render. Virtualization skipped as overkill at cap 300 —
  // measure first (ponytail: add windowing if rows grow past ~1k).
  const canFramesCompat: CANFrame[] = useMemo(
    () =>
      packetRows.map((r) => ({
        id: `compat-${r.id}`,
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
      })),
    [packetRows]
  );

  const anomalyCount = useMemo(() => packetRows.filter((r) => r.isAnomaly).length, [packetRows]);
  const currentTabLabel = TAB_LABELS[activeTab] || 'Dashboard';

  return (
    <div className="glass-workspace relative flex h-screen w-screen flex-col overflow-hidden text-text-body select-none border border-border">
      {/* TOP UNIFIED APPLICATION HEADER (Full width, Linear/Raycast standard: Brand, Live telemetry status, and integrated window controls) */}
      <header className="pywebview-drag-region relative z-30 flex h-10 w-full shrink-0 items-center justify-between border-b border-border-whisper bg-bg-chrome px-3 backdrop-blur-md cursor-move select-none">
        {/* Left Section: Sidebar Toggle, Brand & Breadcrumb */}
        <div
          className="flex items-center gap-2 min-w-0"
          style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
        >
          <button
            onClick={handleToggleRail}
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-[6px] transition-all cursor-pointer active:scale-95 ${
              isRailCollapsed
                ? 'bg-accent-soft text-accent hover:bg-accent/20'
                : 'text-text-mid hover:bg-bg-row-hover hover:text-text-hi'
            }`}
            title={isRailCollapsed ? 'Gezinme Menüsünü Aç' : 'Gezinme Menüsünü Daralt'}
            aria-label="Gezinme Menüsü"
          >
            <PanelLeft className="h-4 w-4" />
          </button>

          <div className="flex items-center gap-1.5 pl-0.5 shrink-0">
            <div className="flex h-5 w-5 items-center justify-center rounded-[5px] bg-accent-soft text-accent border border-accent-line">
              <Activity className="h-3 w-3" />
            </div>
            <div className="flex items-center gap-1.5 font-sans">
              <span className="text-[12.5px] font-bold tracking-tight text-text-hi whitespace-nowrap">
                Universal CAN-Bus
              </span>
              <span className="hidden sm:inline-block rounded-[4px] surface-inset px-1.5 py-0.5 font-mono text-[9px] font-semibold text-text-mid">
                PRO
              </span>
            </div>
          </div>

          <div className="hidden sm:block h-3.5 w-px bg-border/60 mx-1 shrink-0" />

          {/* Breadcrumb / Active Module */}
          <div className="hidden sm:flex items-center gap-1 text-[11.5px] font-medium min-w-0">
            <span className="text-text-low font-normal hidden md:inline">Modül:</span>
            <span className="font-semibold text-text-hi truncate max-w-[110px] md:max-w-none">{currentTabLabel}</span>
          </div>
        </div>

        {/* Center Section: Live Telemetry Status (Seamless inline header, no pill container) */}
        <div className="hidden sm:flex items-center gap-2 font-mono text-[11px] select-none text-text-mid">
          <div className="flex items-center gap-1.5">
            <span
              className={`h-2 w-2 rounded-full transition-all ${
                isSimulating
                  ? 'bg-add shadow-[0_0_8px_rgba(52,211,153,0.7)] animate-pulse'
                  : 'bg-text-faint'
              }`}
            />
            <span className={isSimulating ? 'font-semibold text-text-hi' : 'text-text-low'}>
              {isSimulating ? `${frameRate} FPS` : 'Durduruldu'}
            </span>
          </div>

          <span className="text-border-strong select-none">·</span>

          <div className="flex items-center gap-1">
            <span className="text-text-hi font-medium">{channel}</span>
            <span className="hidden lg:inline text-text-faint">@</span>
            <span className="hidden lg:inline">{baudRate}</span>
          </div>

          {busLoad !== undefined && (
            <>
              <span className="text-border-strong select-none">·</span>
              <div className="flex items-center gap-1">
                <span className="text-text-low">Yük:</span>
                <span className={`font-semibold ${busLoad > 70 ? 'text-del' : busLoad > 40 ? 'text-warn' : 'text-text-hi'}`}>
                  %{busLoad.toFixed(1)}
                </span>
              </div>
            </>
          )}

          <span className="text-border-strong select-none">·</span>
          <div className="flex items-center gap-1">
            <span className="text-text-low">Paket:</span>
            <span className="font-semibold text-text-hi tabular-nums">
              {totalPackets.toLocaleString('tr-TR')}
            </span>
          </div>

          {isEstopActive && (
            <>
              <span className="text-border-strong select-none">·</span>
              <span className="rounded-tag border border-danger-border bg-delbg px-1.5 py-0.5 text-[10px] font-bold text-del animate-pulse">
                E-STOP KİLİTLİ
              </span>
            </>
          )}
        </div>

        {/* Right Section: Seamless Window & View Controls (Integrated into Header, no floating card) */}
        <div
          className="flex items-center gap-1 select-none"
          style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
        >
          {/* Theme toggle */}
          <button
            onClick={handleToggleTheme}
            className="flex h-7 w-7 items-center justify-center rounded-[6px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95 cursor-pointer"
            title={theme === 'dark' ? 'Açık Temaya Geç' : 'Koyu Temaya Geç'}
            aria-label="Toggle Theme"
          >
            {theme === 'dark' ? (
              <Sun className="h-3.5 w-3.5 text-brandamber" />
            ) : (
              <Moon className="h-3.5 w-3.5 text-accent" />
            )}
          </button>

          {/* Copilot toggle */}
          <button
            onClick={() => setIsCopilotOpen((prev) => !prev)}
            className={`flex h-7 w-7 items-center justify-center rounded-[6px] transition-all active:scale-95 cursor-pointer ${
              isCopilotOpen
                ? 'bg-accent-soft text-accent shadow-xs'
                : 'text-text-mid hover:bg-bg-row-hover hover:text-accent'
            }`}
            title={isCopilotOpen ? 'Teşhis Copilot Kapat' : 'Teşhis Copilot Aç'}
            aria-label="Toggle Copilot"
          >
            <PanelRight className="h-3.5 w-3.5" />
          </button>

          <div className="h-3.5 w-px bg-border/60 mx-1" />

          {/* Native Window Controls */}
          <button
            onClick={() => DesktopBridge.minimizeWindow()}
            className="flex h-7 w-7 items-center justify-center rounded-[6px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95 cursor-pointer"
            title="Simge Durumuna Küçült"
            aria-label="Minimize"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => DesktopBridge.maximizeWindow()}
            className="flex h-7 w-7 items-center justify-center rounded-[6px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95 cursor-pointer"
            title="Ekranı Kapla"
            aria-label="Maximize"
          >
            <Square className="h-2.5 w-2.5" />
          </button>
          <button
            onClick={() => DesktopBridge.closeWindow()}
            className="flex h-7 w-7 items-center justify-center rounded-[6px] text-text-low transition-colors hover:bg-del hover:text-white active:scale-95 cursor-pointer"
            title="Pencereyi Kapat"
            aria-label="Close"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {/* WORKSPACE ROW: 3-column layout below Top Header */}
      <div className="relative flex flex-1 min-h-0 w-full flex-row overflow-hidden">
        {/* LEFT COLUMN: Fixed 230px Clean Width Sidebar */}
        <aside
          className={`relative flex h-full shrink-0 flex-col overflow-hidden glass-rail z-20 zeron-sidebar-transition border-r border-border/80 ${
            isRailCollapsed ? 'pointer-events-none' : ''
          }`}
          style={{
            width: isRailCollapsed ? 0 : 230,
          }}
        >
          {/* Fixed Inner Column (230px wide): prevents label wrap/squish during width animation with GPU layer */}
          <div
            className="flex flex-col h-full shrink-0"
            style={{ width: 230, minWidth: 230, transform: 'translateZ(0)', backfaceVisibility: 'hidden' }}
          >
            {/* Sidebar Navigation */}
            <div className="flex-1 min-h-0 overflow-hidden">
              <SideRail
                activeTab={activeTab}
                onSelectTab={setActiveTab}
                channel={channel}
                isSimulating={isSimulating}
                isEstopActive={isEstopActive}
                collapsed={false}
                onToggleCollapse={handleToggleRail}
              />
            </div>
          </div>
        </aside>

        {/* MIDDLE COLUMN: Main Workspace */}
        <div className="relative flex flex-1 min-w-0 flex-col h-full overflow-hidden" style={{ zIndex: 10 }}>
          {/* Main Workspace */}
          <main className="relative flex flex-1 min-w-0 flex-col overflow-hidden bg-transparent p-3 pt-2">
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
                  busLoad={busLoad}
                  totalPackets={totalPackets}
                  isEstopActive={isEstopActive}
                  activeScenario={activeScenario}
                  onToggleStreaming={handleToggleSimulator}
                  onSelectScenario={handleSelectScenario}
                  onInjectFault={handleInjectFault}
                  onEstop={handleEstop}
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
      </div>

      {/* RIGHT COLUMN: Full-Height AI Diagnostic Copilot Panel (matching Left SideRail) */}
      <aside
        className={`relative flex h-full shrink-0 flex-col overflow-hidden glass-rail z-20 zeron-sidebar-transition border-l border-border/80 ${
          isCopilotOpen ? 'pointer-events-auto' : 'pointer-events-none'
        }`}
        style={{
          width: isCopilotOpen ? 'min(380px, 32vw)' : 0,
        }}
      >
        {/* Fixed Inner Column: prevents label wrap/squish during width animation with GPU layer */}
        <div
          className="flex flex-col h-full shrink-0"
          style={{
            width: 'min(380px, 32vw)',
            minWidth: 'min(380px, 32vw)',
            transform: 'translateZ(0)',
            backfaceVisibility: 'hidden',
          }}
        >
          {/* Copilot Header: clean, sleek sub-header with rescan & close button */}
          <div className="flex h-9 shrink-0 items-center justify-between px-3 select-none border-b border-border/40">
            <div className="flex items-center gap-2 min-w-0">
              <div className="flex h-5 w-5 items-center justify-center rounded-[5px] bg-accent-soft text-accent">
                <Sparkles className="h-3 w-3" />
              </div>
              <span className="font-sans text-[12px] font-semibold text-text-hi tracking-tight truncate select-none">
                CAN Teşhis Copilot
              </span>
              <span className="flex h-1.5 w-1.5 rounded-full bg-add" title="Çevrimdışı Uzman Motoru Aktif" />
            </div>

            <div className="flex items-center gap-1">
              <button
                onClick={handleRescan}
                disabled={isAiLoading}
                className="flex h-6 w-6 items-center justify-center rounded-[5px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95 cursor-pointer disabled:opacity-50"
                title="Yeniden Tara"
                aria-label="Yeniden Tara"
              >
                <RotateCw className={`h-3.5 w-3.5 transition-transform ${isAiLoading ? 'animate-spin text-accent' : ''}`} />
              </button>
              <button
                onClick={() => setIsCopilotOpen(false)}
                className="flex h-6 w-6 items-center justify-center rounded-[5px] text-text-low transition-colors hover:bg-bg-row-hover hover:text-text-hi active:scale-95 cursor-pointer"
                title="Copilot Kapat"
                aria-label="Copilot Kapat"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {/* Copilot Body */}
          <div className="flex-1 min-h-0 overflow-hidden">
            <AiCopilotPanel
              diagnosticState={diagnosticState}
              chatMessages={chatMessages}
              isAiLoading={isAiLoading}
              onRescan={handleRescan}
              onSendMessage={handleSendMessage}
              onExecuteAction={handleExecuteAction}
              onClose={() => setIsCopilotOpen(false)}
              hideHeader={true}
            />
          </div>
        </div>
      </aside>
      </div>
    </div>
  );
};

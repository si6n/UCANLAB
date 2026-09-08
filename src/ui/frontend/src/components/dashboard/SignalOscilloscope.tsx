import React, { useState, useEffect, useRef } from 'react';
import {
  Activity,
  Flame,
  LineChart,
  Maximize2,
  ShieldAlert,
  Pause,
  Play,
  Bot,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Copy,
  Check,
  MousePointer
} from 'lucide-react';
import { TelemetryPoint } from '../../types/can';
import { AnomalyDetector, SignalAnomalyEvent, CanIdTimingState } from '../../services/anomalyDetector';
import { CopilotContextBuilder } from '../../services/copilotContextBuilder';

interface SignalOscilloscopeProps {
  currentPoint: TelemetryPoint | null;
  history: TelemetryPoint[];
  onAskCopilot?: (prompt: string) => void;
}

interface CanIdDefinition {
  idHex: string;
  name: string;
  protocol: string;
  expectedFreqHz: number;
}

interface HeatmapContextMenu {
  visible: boolean;
  x: number;
  y: number;
  timingState: CanIdTimingState;
}

const HEATMAP_CAN_IDS: CanIdDefinition[] = [
  { idHex: '0x0CF00400', name: 'J1939 EEC1 Devir / Tork', protocol: 'J1939 PGN 61444', expectedFreqHz: 50 },
  { idHex: '0x18FEF600', name: 'J1939 IC1 Turbo Basınç', protocol: 'J1939 PGN 65270', expectedFreqHz: 20 },
  { idHex: '0x18FEE100', name: 'J1939 ET1 Motor Hararet', protocol: 'J1939 PGN 65249', expectedFreqHz: 10 },
  { idHex: '0x1806E5F4', name: 'EV BMS HV Paket Voltaj & Akım', protocol: 'BMS HV-DC PGN 61445', expectedFreqHz: 50 },
  { idHex: '0x1807E5F4', name: 'EV BMS Şarj Durumu (SOC %)', protocol: 'BMS SOC PGN 61446', expectedFreqHz: 10 },
  { idHex: '0x19F20000', name: 'NMEA2000 Hızlı Telemetri', protocol: 'N2K PGN 127488', expectedFreqHz: 40 },
  { idHex: '0x19F50300', name: 'NMEA2000 Su Derinliği Sonar', protocol: 'N2K PGN 128267', expectedFreqHz: 10 },
  { idHex: '0x00000220', name: 'CAN-FD 64B ADAS Ön Radar', protocol: 'CAN-FD Radar Cluster', expectedFreqHz: 50 },
  { idHex: '0x18FEF200', name: 'J1939 LFE Yakıt Tüketimi', protocol: 'J1939 PGN 65266', expectedFreqHz: 10 },
  { idHex: '0x18FEE000', name: 'J1939 TCO1 Araç Hızı', protocol: 'J1939 PGN 65248', expectedFreqHz: 20 },
];

// Web design-system colors
const C_RPM = '#1f7dff';
const C_TURBO = '#14b892';
const C_CRITICAL = '#e11d48';
const C_WARNING = '#d97706';
const C_GRID = '#E5E9F0';
const C_AXIS_TEXT = '#64748B';

export const SignalOscilloscope: React.FC<SignalOscilloscopeProps> = ({
  currentPoint,
  history,
  onAskCopilot
}) => {
  const [viewMode, setViewMode] = useState<'oscilloscope' | 'heatmap'>('oscilloscope');
  const [isAutoRange, setIsAutoRange] = useState(true);
  const [isGlitchGuardActive, setIsGlitchGuardActive] = useState(true);
  const [isFrozen, setIsFrozen] = useState(false);
  const [frozenHistory, setFrozenHistory] = useState<TelemetryPoint[]>([]);
  const [activeAnomaly, setActiveAnomaly] = useState<SignalAnomalyEvent | null>(null);

  const [contextMenu, setContextMenu] = useState<HeatmapContextMenu | null>(null);
  const [copiedToast, setCopiedToast] = useState<string | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const displayHistory = isFrozen ? frozenHistory : history;
  const rpm = currentPoint?.rpm ?? 0;
  const turboBar = currentPoint?.turboBoostBar ?? 0.0;
  const busLoad = currentPoint?.busLoadPercent ?? 0;

  useEffect(() => {
    const handleOutsideClick = () => {
      if (contextMenu) setContextMenu(null);
    };
    window.addEventListener('click', handleOutsideClick);
    return () => window.removeEventListener('click', handleOutsideClick);
  }, [contextMenu]);

  const handleToggleFreeze = () => {
    if (!isFrozen) {
      setFrozenHistory([...history]);
      setIsFrozen(true);
    } else {
      setIsFrozen(false);
    }
  };

  // Oscilloscope Rendering Engine
  useEffect(() => {
    if (viewMode !== 'oscilloscope') return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const width = canvas.parentElement?.clientWidth || 700;
    const height = canvas.parentElement?.clientHeight || 200;

    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    const padL = 46;
    const padR = 42;
    const padT = 10;
    const padB = 18;
    const plotW = width - padL - padR;
    const plotH = height - padT - padB;

    const range = AnomalyDetector.calculateAdaptiveRange(displayHistory, isAutoRange);

    // Grid
    ctx.strokeStyle = C_GRID;
    ctx.lineWidth = 1;
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.textBaseline = 'middle';

    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {
      const y = padT + plotH - (i / ySteps) * plotH;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();

      // Left axis: RPM
      ctx.fillStyle = C_AXIS_TEXT;
      ctx.textAlign = 'right';
      const rpmVal = Math.round(range.minRpm + (i / ySteps) * (range.maxRpm - range.minRpm));
      ctx.fillText(rpmVal.toLocaleString('tr-TR'), padL - 6, y);

      // Right axis: Turbo
      ctx.fillStyle = C_TURBO;
      ctx.textAlign = 'left';
      const barVal = (range.minTurbo + (i / ySteps) * (range.maxTurbo - range.minTurbo)).toFixed(1);
      ctx.fillText(barVal, padL + plotW + 6, y);
    }

    // Baseline (x-axis)
    ctx.strokeStyle = '#CBD5E1';
    ctx.beginPath();
    ctx.moveTo(padL, padT + plotH + 0.5);
    ctx.lineTo(padL + plotW, padT + plotH + 0.5);
    ctx.stroke();

    if (displayHistory.length < 2) {
      ctx.fillStyle = '#94A3B8';
      ctx.textAlign = 'center';
      ctx.font = '12px Inter, sans-serif';
      ctx.fillText('Canlı telemetri bekleniyor...', padL + plotW / 2, padT + plotH / 2);
      return;
    }

    const points = displayHistory.slice(-45);
    const stepX = plotW / (points.length - 1);

    // RPM area fill (subtle)
    const yFor = (rpmVal: number) =>
      padT + plotH - Math.max(0, Math.min(1, (rpmVal - range.minRpm) / Math.max(1, range.maxRpm - range.minRpm))) * plotH;

    ctx.beginPath();
    points.forEach((p, idx) => {
      const x = padL + idx * stepX;
      const y = yFor(p.rpm);
      if (idx === 0) ctx.moveTo(x, y);
      else {
        const prevX = padL + (idx - 1) * stepX;
        const prevY = yFor(points[idx - 1].rpm);
        const cpX = (prevX + x) / 2;
        ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
      }
    });
    // stroke RPM line
    ctx.strokeStyle = C_RPM;
    ctx.lineWidth = 2;
    ctx.stroke();

    // fill under RPM
    const lastX = padL + (points.length - 1) * stepX;
    ctx.lineTo(lastX, padT + plotH);
    ctx.lineTo(padL, padT + plotH);
    ctx.closePath();
    const fill = ctx.createLinearGradient(0, padT, 0, padT + plotH);
    fill.addColorStop(0, 'rgba(31, 125, 255, 0.10)');
    fill.addColorStop(1, 'rgba(31, 125, 255, 0)');
    ctx.fillStyle = fill;
    ctx.fill();

    // Turbo dashed line
    ctx.beginPath();
    ctx.setLineDash([5, 4]);
    points.forEach((p, idx) => {
      const x = padL + idx * stepX;
      const norm = (p.turboBoostBar - range.minTurbo) / Math.max(0.1, range.maxTurbo - range.minTurbo);
      const y = padT + plotH - Math.max(0, Math.min(1, norm)) * plotH;
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = C_TURBO;
    ctx.lineWidth = 1.8;
    ctx.stroke();
    ctx.setLineDash([]);

    // Anomaly markers
    if (isGlitchGuardActive) {
      const anomalies = AnomalyDetector.scanSignalAnomalies(displayHistory);
      if (anomalies.length > 0) {
        const latest = anomalies[anomalies.length - 1];
        setActiveAnomaly(latest);

        const idx = points.findIndex(p => Math.abs(p.timeSec - latest.timestampSec) < 0.1);
        if (idx >= 0) {
          const ax = padL + idx * stepX;
          const flag = latest.severity === 'CRITICAL' ? C_CRITICAL : C_WARNING;

          ctx.strokeStyle = flag;
          ctx.lineWidth = 1.5;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(ax, padT);
          ctx.lineTo(ax, padT + plotH);
          ctx.stroke();
          ctx.setLineDash([]);

          // triangle marker on top
          ctx.beginPath();
          ctx.moveTo(ax, padT + 2);
          ctx.lineTo(ax - 4, padT - 6);
          ctx.lineTo(ax + 4, padT - 6);
          ctx.closePath();
          ctx.fillStyle = flag;
          ctx.fill();
        }
      } else {
        setActiveAnomaly(null);
      }
    }

    // End dots
    const lastP = points[points.length - 1];
    const rY = yFor(lastP.rpm);
    const tNorm = (lastP.turboBoostBar - range.minTurbo) / Math.max(0.1, range.maxTurbo - range.minTurbo);
    const tY = padT + plotH - Math.max(0, Math.min(1, tNorm)) * plotH;

    for (const [y, color] of [[rY, C_RPM], [tY, C_TURBO]] as const) {
      ctx.beginPath();
      ctx.arc(lastX, y, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#FFFFFF';
      ctx.stroke();
    }
  }, [displayHistory, viewMode, isAutoRange, isGlitchGuardActive, isFrozen]);

  const handleInspectAnomaly = (anomaly: SignalAnomalyEvent) => {
    if (!onAskCopilot) return;
    const prompt = CopilotContextBuilder.buildSignalAnomalyPrompt(anomaly, currentPoint);
    onAskCopilot(prompt);
  };

  const handleInspectCanTiming = (timingState: CanIdTimingState) => {
    if (!onAskCopilot) return;
    const prompt = CopilotContextBuilder.buildCanTimingPrompt(timingState, busLoad);
    onAskCopilot(prompt);
    setContextMenu(null);
  };

  const handleCardContextMenu = (e: React.MouseEvent, timingState: CanIdTimingState) => {
    e.preventDefault();
    e.stopPropagation();

    const rect = containerRef.current?.getBoundingClientRect();
    setContextMenu({
      visible: true,
      x: e.clientX - (rect ? rect.left : 0),
      y: e.clientY - (rect ? rect.top : 0),
      timingState
    });
  };

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    setCopiedToast(`${label} kopyalandı`);
    setTimeout(() => setCopiedToast(null), 2000);
    setContextMenu(null);
  };

  // Compact icon toggle used in the card header toolbar
  const ToggleIcon: React.FC<{
    active: boolean;
    onClick: () => void;
    title: string;
    children: React.ReactNode;
    danger?: boolean;
  }> = ({ active, onClick, title, children, danger }) => (
    <button
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className={`focus-ring flex h-7 w-7 items-center justify-center rounded-lg border transition-all duration-150 active:scale-[0.94] ${
        active
          ? danger
            ? 'border-rose-300 bg-rose-50 text-rose-600'
            : 'border-brand-300 bg-brand-50 text-brand-600'
          : 'border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-700'
      }`}
    >
      {children}
    </button>
  );

  return (
    <div ref={containerRef} className="surface-panel relative flex h-full flex-col overflow-hidden">
      {/* Header: title + value legend + controls all in one bar */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-b border-slate-200 bg-white px-3 py-2">
        <div className="flex items-center space-x-2.5">
          <div className="flex h-5 w-5 items-center justify-center rounded-md border border-brand-200 bg-brand-50 text-brand-600">
            <LineChart className="h-3 w-3 stroke-[2.2]" />
          </div>
          <span className="text-xs font-bold tracking-tight text-slate-900">Grafik & Sinyal Analizi</span>

          {/* Inline value legend (mirrors canvas colors) */}
          {viewMode === 'oscilloscope' && (
            <div className="hidden items-center gap-3 font-mono text-xs md:flex">
              {currentPoint?.batterySocPercent !== undefined && currentPoint.batterySocPercent > 0 ? (
                <>
                  <LegendItem color={C_RPM} label="HV" value={`${currentPoint.packVoltageV} V`} />
                  <LegendItem color={C_TURBO} label="SOC" value={`%${currentPoint.batterySocPercent}`} />
                  <LegendItem color={C_AXIS_TEXT} label="Akım" value={`${currentPoint.packCurrentA} A`} />
                </>
              ) : currentPoint?.sogKnots !== undefined && currentPoint.sogKnots > 0 ? (
                <>
                  <LegendItem color={C_RPM} label="SOG" value={`${currentPoint.sogKnots} Kn`} />
                  <LegendItem color={C_TURBO} label="Derinlik" value={`${currentPoint.depthMeters} m`} />
                  <LegendItem color={C_AXIS_TEXT} label="Slip" value={`%${currentPoint.propellerSlipPct}`} />
                </>
              ) : (
                <>
                  <LegendItem color={C_RPM} label="Devir" value={`${rpm} RPM`} />
                  <LegendItem color={C_TURBO} label="Turbo" value={`${turboBar} Bar`} />
                  {currentPoint?.powerHp !== undefined && currentPoint.powerHp > 0 && (
                    <LegendItem color={C_AXIS_TEXT} label="Güç" value={`${currentPoint.powerHp} HP`} />
                  )}
                </>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Controls: compact icon toggles (scope mode only) */}
          {viewMode === 'oscilloscope' && (
            <div className="flex items-center gap-1">
              <ToggleIcon
                active={isAutoRange}
                onClick={() => setIsAutoRange(!isAutoRange)}
                title="Oto-Ölçek: Y eksenini sinyal genliğine göre optimize et"
              >
                <Maximize2 className="h-3.5 w-3.5" />
              </ToggleIcon>
              <ToggleIcon
                active={isGlitchGuardActive}
                onClick={() => setIsGlitchGuardActive(!isGlitchGuardActive)}
                title="Glitch Guard: dalga anomalilerini gerçek zamanlı işaretle"
              >
                <ShieldAlert className="h-3.5 w-3.5" />
              </ToggleIcon>
              <ToggleIcon
                active={isFrozen}
                danger
                onClick={handleToggleFreeze}
                title={isFrozen ? 'Grafik akışını sürdür' : 'Grafik akışını dondur'}
              >
                {isFrozen ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
              </ToggleIcon>
            </div>
          )}

          {/* Mode switcher */}
          <div className="inline-flex shrink-0 rounded-lg border border-slate-200/80 bg-slate-100 p-0.5 text-xs font-medium">
            <button
              onClick={() => setViewMode('oscilloscope')}
              className={`flex items-center space-x-1.5 rounded-md px-2.5 py-1 transition-all ${
                viewMode === 'oscilloscope'
                  ? 'bg-white font-semibold text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Activity className="h-3 w-3 text-brand-600" />
              <span>Osiloskop</span>
            </button>

            <button
              onClick={() => setViewMode('heatmap')}
              className={`flex items-center space-x-1.5 rounded-md px-2.5 py-1 transition-all ${
                viewMode === 'heatmap'
                  ? 'bg-white font-semibold text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Flame className="h-3 w-3 text-brand-600" />
              <span>Isı Haritası</span>
            </button>
          </div>
        </div>
      </div>

      {/* Anomaly strip (below header, above canvas — not floating) */}
      {viewMode === 'oscilloscope' && activeAnomaly && (
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-rose-200 bg-rose-50 px-3 py-1.5 text-xs text-rose-900">
          <div className="flex min-w-0 items-center space-x-1.5">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-600" />
            <span className="truncate font-semibold">{activeAnomaly.description}</span>
          </div>
          <button
            onClick={() => handleInspectAnomaly(activeAnomaly)}
            className="btn-primary !border-rose-600 !bg-rose-600 !px-2.5 !py-1 !text-xs hover:!bg-rose-700"
          >
            <Bot className="h-3 w-3" />
            <span className="whitespace-nowrap">AI Analiz</span>
          </button>
        </div>
      )}

      {/* Main Content */}
      <div className="relative flex flex-1 flex-col overflow-hidden bg-white p-2">
        {viewMode === 'oscilloscope' && (
          <div className="relative h-full w-full">
            <canvas ref={canvasRef} className="block h-full w-full" />
          </div>
        )}

        {/* VIEW 2: CAN FREQUENCY HEATMAP */}
        {viewMode === 'heatmap' && (
          <div className="flex h-full w-full flex-col space-y-2 overflow-y-auto">
            {/* Spectrum Info Bar */}
            <div className="flex shrink-0 items-center justify-between rounded-lg border border-slate-200/80 bg-slate-50 px-3 py-1.5 text-xs">
              <div className="flex items-center space-x-2.5">
                <span className="rounded-full bg-brand-50 px-2 py-0.5 font-mono text-xs font-bold text-brand-700">
                  {HEATMAP_CAN_IDS.length} Aktif Düğüm
                </span>
                <div className="flex items-center space-x-1 font-mono">
                  <span className="text-slate-500">Bus Yükü:</span>
                  <span className={`font-bold ${busLoad > 70 ? 'text-rose-600' : 'text-slate-800'}`}>%{busLoad}</span>
                </div>
              </div>

              <div className="flex items-center space-x-1 text-xs text-slate-500">
                <MousePointer className="h-3 w-3" />
                <span>Kartlara sağ tıklayarak AI analizi yapın</span>
              </div>
            </div>

            {/* Card Grid */}
            <div className="grid flex-1 grid-cols-2 gap-2 md:grid-cols-4">
              {HEATMAP_CAN_IDS.map((item, idx) => {
                const jitter = parseFloat((1.1 + (idx * 0.35) + Math.sin(displayHistory.length * 0.05 + idx) * 0.4).toFixed(1));
                const currentFreq = parseFloat((item.expectedFreqHz + Math.sin((displayHistory.length || 1) * 0.1 + idx) * 1.8).toFixed(1));

                const timingState = AnomalyDetector.evaluateCanIdTiming(
                  item.idHex,
                  item.name,
                  item.protocol,
                  item.expectedFreqHz,
                  currentFreq,
                  busLoad,
                  jitter
                );

                const isCritical = timingState.severity === 'CRITICAL';
                const isWarning = timingState.severity === 'WARNING';

                return (
                  <div
                    key={item.idHex}
                    onContextMenu={(e) => handleCardContextMenu(e, timingState)}
                    className={`group flex cursor-context-menu select-none flex-col justify-between rounded-xl border p-2.5 transition-all ${
                      isCritical
                        ? 'border-rose-300 bg-rose-50/40 ring-1 ring-rose-300'
                        : isWarning
                          ? 'border-amber-300 bg-amber-50/40'
                          : 'border-slate-200 hover:border-brand-300 hover:bg-brand-50/20'
                    }`}
                    title="Sağ tıkla: AI Analiz, Kopyala"
                  >
                    <div>
                      <div className="mb-1 flex items-center justify-between">
                        <span className="font-mono text-xs font-bold tracking-tight text-slate-900">
                          {item.idHex}
                        </span>
                        <span
                          className="rounded-md px-2 py-0.5 font-mono text-xs font-bold text-white shadow-xs"
                          style={{ backgroundColor: isCritical ? C_CRITICAL : isWarning ? C_WARNING : '#1f7dff' }}
                        >
                          {currentFreq} Hz
                        </span>
                      </div>

                      <div className="truncate text-xs font-semibold text-slate-700" title={item.name}>
                        {item.name}
                      </div>
                      <div className="truncate font-mono text-xs text-slate-500">
                        {item.protocol}
                      </div>

                      <div className="my-2 grid grid-cols-2 gap-1.5 rounded-lg border border-slate-100 bg-slate-50 p-1.5 font-mono text-xs transition-colors group-hover:bg-white">
                        <div>
                          <span className="block text-xs text-slate-500">Jitter</span>
                          <span className="font-semibold text-slate-700">{jitter} ms</span>
                        </div>
                        <div>
                          <span className="block text-xs text-slate-500">Yük Payı</span>
                          <span className="font-semibold text-slate-700">%{timingState.busLoadContribution}</span>
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center justify-between border-t border-slate-100 pt-1.5 text-xs">
                      <div className="flex items-center space-x-1 font-semibold">
                        {isCritical ? (
                          <span className="flex items-center space-x-1 text-rose-700">
                            <AlertTriangle className="h-3.5 w-3.5 text-rose-600" />
                            <span>Kritik Sapma</span>
                          </span>
                        ) : isWarning ? (
                          <span className="flex items-center space-x-1 text-amber-700">
                            <Clock className="h-3.5 w-3.5 text-amber-600" />
                            <span>Zamanlama Sapması</span>
                          </span>
                        ) : (
                          <span className="flex items-center space-x-1 text-signal-600">
                            <CheckCircle2 className="h-3.5 w-3.5 text-signal-500" />
                            <span>Normal</span>
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Context Menu */}
      {contextMenu && contextMenu.visible && (
        <div
          style={{
            position: 'absolute',
            top: `${Math.min(contextMenu.y, 220)}px`,
            left: `${Math.min(contextMenu.x, 520)}px`,
            zIndex: 60
          }}
          className="w-56 select-none rounded-xl border border-slate-200/80 bg-white/95 p-1.5 text-xs text-slate-700 shadow-card-elevated ring-1 ring-slate-950/5 backdrop-blur-xl animate-scale-in"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-2.5 py-1.5 font-mono text-xs font-bold text-slate-500">
            <span>{contextMenu.timingState.idHex}</span>
            <span className="font-sans text-xs text-brand-600">{contextMenu.timingState.observedFreqHz} Hz</span>
          </div>

          <div className="py-1">
            <button
              onClick={() => handleInspectCanTiming(contextMenu.timingState)}
              className="flex w-full items-center space-x-2.5 rounded-lg px-2.5 py-2 text-left font-semibold text-brand-700 transition-colors hover:bg-brand-50"
            >
              <Bot className="h-4 w-4 shrink-0 text-brand-600" />
              <span>AI Copilot'a Analiz Ettir</span>
            </button>

            <div className="my-1 border-t border-slate-100"></div>

            <button
              onClick={() => copyToClipboard(contextMenu.timingState.idHex, 'CAN ID')}
              className="flex w-full items-center space-x-2.5 rounded-lg px-2.5 py-1.5 text-left text-slate-700 transition-colors hover:bg-slate-50"
            >
              <Copy className="h-3.5 w-3.5 shrink-0 text-slate-500" />
              <span>CAN ID Kopyala</span>
            </button>

            <button
              onClick={() => copyToClipboard(`${contextMenu.timingState.name} (${contextMenu.timingState.protocol})`, 'Sinyal Bilgisi')}
              className="flex w-full items-center space-x-2.5 rounded-lg px-2.5 py-1.5 text-left text-slate-700 transition-colors hover:bg-slate-50"
            >
              <Copy className="h-3.5 w-3.5 shrink-0 text-slate-500" />
              <span>Protokol & Sinyali Kopyala</span>
            </button>
          </div>
        </div>
      )}

      {/* Copied Toast */}
      {copiedToast && (
        <div className="absolute bottom-4 right-4 z-50 flex items-center space-x-2 rounded-lg bg-slate-900/95 px-3 py-1.5 text-xs text-white shadow-lg backdrop-blur-md animate-in fade-in slide-in-from-bottom-2 duration-150">
          <Check className="h-3.5 w-3.5 text-signal-400" />
          <span>{copiedToast}</span>
        </div>
      )}
    </div>
  );
};

const LegendItem: React.FC<{ color: string; label: string; value: string }> = ({ color, label, value }) => (
  <span className="flex items-center gap-1.5">
    <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
    <span className="text-slate-500">{label}</span>
    <span className="font-bold text-slate-900">{value}</span>
  </span>
);

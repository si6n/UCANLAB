import React, { useState, useEffect, useRef } from 'react';
import {
  Activity,
  Maximize2,
  Shield,
  Pause,
  Play,
  AlertTriangle,
  Cpu,
} from 'lucide-react';
import {
  INITIAL_SCOPE_SERIES,
  generateProceduralWaveformData,
} from '../../data/constants';

interface ScopePanelProps {
  isStreaming: boolean;
  onAnalyzeFault?: () => void;
}

export const ScopePanel: React.FC<ScopePanelProps> = ({
  isStreaming,
  onAnalyzeFault,
}) => {
  const [isPaused, setIsPaused] = useState(false);
  const [activeSegment, setActiveSegment] = useState<'oscilloscope' | 'heatmap'>('oscilloscope');
  const [seriesList, setSeriesList] = useState(INITIAL_SCOPE_SERIES);
  const [showAlertDetails, setShowAlertDetails] = useState(false);

  // Crosshair coordinates
  const [hoverPos, setHoverPos] = useState<{ x: number; y: number } | null>(null);
  const [hoverReadout, setHoverReadout] = useState<{
    time: string;
    primaryVal: string;
    secondaryVal: string;
  } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animFrameRef = useRef<number | null>(null);
  const offsetRef = useRef(0);

  // Keep live paused state tied to either manual pause or overall streaming state
  const isEffectivelyPaused = isPaused || !isStreaming;

  // Live series values simulation
  useEffect(() => {
    if (isEffectivelyPaused) return;

    const interval = setInterval(() => {
      setSeriesList((prev) =>
        prev.map((s) => {
          let delta = (Math.random() - 0.5) * 0.8;
          let nextVal = s.value + delta;
          if (s.id === 'hv') nextVal = Math.max(385, Math.min(410, nextVal));
          if (s.id === 'soc') nextVal = Math.max(70, Math.min(85, nextVal));
          if (s.id === 'current') nextVal = Math.max(35, Math.min(50, nextVal));
          return { ...s, value: Number(nextVal.toFixed(1)) };
        })
      );
    }, 400);

    return () => clearInterval(interval);
  }, [isEffectivelyPaused]);

  // Canvas drawing loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let running = true;

    const render = () => {
      if (!running) return;

      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;

      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr;
        canvas.height = height * dpr;
      }

      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, width, height);

      const paddingLeft = 46;
      const paddingRight = 46;
      const paddingTop = 24;
      const paddingBottom = 26;

      const graphWidth = width - paddingLeft - paddingRight;
      const graphHeight = height - paddingTop - paddingBottom;

      if (graphWidth <= 0 || graphHeight <= 0) {
        ctx.restore();
        animFrameRef.current = requestAnimationFrame(render);
        return;
      }

      const isDark = document.documentElement.classList.contains('dark');

      // 1. Frosted wash — soft translucent so card's frosted glass and ambient light shines through
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = isDark ? 'rgba(10, 14, 23, 0.22)' : 'rgba(255, 255, 255, 0.25)';
      ctx.fillRect(0, 0, width, height);

      // 2. Gridlines (%6) + tick labels (#a1a1aa)
      const horizSteps = 5;
      ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)';
      ctx.lineWidth = 1;
      ctx.font = '10px "Geist Mono", "JetBrains Mono", monospace';

      for (let i = 0; i <= horizSteps; i++) {
        const y = paddingTop + (graphHeight / horizSteps) * i;
        ctx.beginPath();
        ctx.moveTo(paddingLeft, y);
        ctx.lineTo(width - paddingRight, y);
        ctx.stroke();

        // Left axis ticks (HV scale: 420V -> 340V)
        const vVal = Math.round(420 - i * 16);
        ctx.fillStyle = '#a1a1aa';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText(`${vVal}V`, paddingLeft - 8, y);

        // Right axis ticks (SOC scale: 100% -> 20%)
        const socVal = Math.round(100 - i * 16);
        ctx.textAlign = 'left';
        ctx.fillText(`${socVal}%`, width - paddingRight + 8, y);
      }

      // 3. Sparse Vertical Time Gridlines (%6)
      const vertSteps = 7;
      for (let j = 0; j <= vertSteps; j++) {
        const x = paddingLeft + (graphWidth / vertSteps) * j;
        ctx.beginPath();
        ctx.moveTo(x, paddingTop);
        ctx.lineTo(x, height - paddingBottom);
        ctx.stroke();

        // Bottom time labels
        const timeSec = (73.0 + j * 0.3).toFixed(1);
        ctx.fillStyle = '#a1a1aa';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(`${timeSec}s`, x, height - paddingBottom + 6);
      }

      // Generate procedural signal data
      const dataPoints = generateProceduralWaveformData(
        100,
        74.0 + (isEffectivelyPaused ? 0 : offsetRef.current * 0.005),
        true
      );

      if (activeSegment === 'heatmap') {
        // Render Heatmap / Spectrogram View
        const cols = dataPoints.length;
        const colWidth = graphWidth / cols;
        const rows = 18;
        const cellHeight = graphHeight / rows;

        for (let c = 0; c < cols; c++) {
          const pt = dataPoints[c];
          for (let r = 0; r < rows; r++) {
            const intensity =
              Math.sin(c * 0.2 + r * 0.4 + offsetRef.current * 0.05) * 0.5 + 0.5;
            const x = paddingLeft + c * colWidth;
            const y = paddingTop + r * cellHeight;

            const alpha = Math.max(0.06, intensity * 0.70);
            ctx.fillStyle =
              intensity > 0.85
                ? (isDark ? `rgba(248, 113, 113, ${alpha})` : `rgba(220, 38, 38, ${alpha})`)
                : intensity > 0.6
                ? (isDark ? `rgba(251, 191, 36, ${alpha})` : `rgba(180, 83, 9, ${alpha})`)
                : (isDark ? `rgba(96, 165, 250, ${alpha})` : `rgba(29, 78, 216, ${alpha})`);
            ctx.fillRect(x, y, colWidth - 1, cellHeight - 1);
          }
        }
      } else {
        // Primary Series Area Gradient Fill under curve
        const areaGrad = ctx.createLinearGradient(0, paddingTop, 0, height - paddingBottom);
        areaGrad.addColorStop(0, isDark ? 'rgba(96, 165, 250, 0.16)' : 'rgba(29, 78, 216, 0.09)');
        areaGrad.addColorStop(1, 'rgba(96, 165, 250, 0.00)');

        ctx.beginPath();
        dataPoints.forEach((pt, idx) => {
          const x = paddingLeft + (idx / (dataPoints.length - 1)) * graphWidth;
          const normY = (pt.primary - 340) / (420 - 340);
          const y = paddingTop + graphHeight - normY * graphHeight;
          if (idx === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.lineTo(paddingLeft + graphWidth, height - paddingBottom);
        ctx.lineTo(paddingLeft, height - paddingBottom);
        ctx.closePath();
        ctx.fillStyle = areaGrad;
        ctx.fill();

        // Primary Series: Electric Blue stroke
        ctx.beginPath();
        ctx.strokeStyle = isDark ? '#60a5fa' : '#1d4ed8';
        ctx.lineWidth = 1.8;

        dataPoints.forEach((pt, idx) => {
          const x = paddingLeft + (idx / (dataPoints.length - 1)) * graphWidth;
          const normY = (pt.primary - 340) / (420 - 340);
          const y = paddingTop + graphHeight - normY * graphHeight;

          if (idx === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        });
        ctx.save();
        ctx.shadowColor = isDark ? 'rgba(96, 165, 250, 0.40)' : 'rgba(29, 78, 216, 0.25)';
        ctx.shadowBlur = 4;
        ctx.stroke();
        ctx.restore();

        // Secondary Series: Mint/Emerald Green (dashed 1px)
        ctx.beginPath();
        ctx.strokeStyle = isDark ? '#4ade80' : '#16a34a';
        ctx.lineWidth = 1.2;
        ctx.setLineDash([4, 3]);

        dataPoints.forEach((pt, idx) => {
          const x = paddingLeft + (idx / (dataPoints.length - 1)) * graphWidth;
          // Normalized between 60 and 95 %
          const normY = (pt.secondary - 60) / (95 - 60);
          const y = paddingTop + graphHeight - normY * graphHeight;

          if (idx === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        });
        ctx.stroke();
        ctx.setLineDash([]); // Reset dash
      }

      // 4. Event Marker — 1px red dashed + FLAT mono label chip
      const dipIndex = 85;
      const markerX = paddingLeft + (dipIndex / 99) * graphWidth;
      const markerColor = isDark ? '#f87171' : '#dc2626';

      ctx.beginPath();
      ctx.strokeStyle = markerColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.moveTo(markerX, paddingTop);
      ctx.lineTo(markerX, height - paddingBottom);
      ctx.stroke();
      ctx.setLineDash([]);

      // Top Caret / Inverted Triangle Marker
      ctx.fillStyle = markerColor;
      ctx.beginPath();
      ctx.moveTo(markerX - 5, paddingTop);
      ctx.lineTo(markerX + 5, paddingTop);
      ctx.lineTo(markerX, paddingTop + 6);
      ctx.closePath();
      ctx.fill();

      // Marker Chip Readout
      ctx.fillStyle = isDark ? 'rgba(248, 113, 113, 0.12)' : 'rgba(220, 38, 38, 0.08)';
      ctx.strokeStyle = isDark ? 'rgba(248, 113, 113, 0.35)' : 'rgba(220, 38, 38, 0.30)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(markerX - 38, paddingTop + 8, 76, 17, 4);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = markerColor;
      ctx.font = '9.5px "Geist Mono", "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('-575 RPM DİP', markerX, paddingTop + 17);

      // 5. Crosshair
      if (hoverPos && hoverPos.x >= paddingLeft && hoverPos.x <= width - paddingRight) {
        ctx.strokeStyle = isDark ? '#52525b' : '#a1a1aa';
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 2]);

        // Vertical crosshair
        ctx.beginPath();
        ctx.moveTo(hoverPos.x, paddingTop);
        ctx.lineTo(hoverPos.x, height - paddingBottom);
        ctx.stroke();

        // Horizontal crosshair
        ctx.beginPath();
        ctx.moveTo(paddingLeft, hoverPos.y);
        ctx.lineTo(width - paddingRight, hoverPos.y);
        ctx.stroke();
        ctx.setLineDash([]);

        // Small indicator circle at cursor
        ctx.fillStyle = isDark ? '#60a5fa' : '#1d4ed8';
        ctx.beginPath();
        ctx.arc(hoverPos.x, hoverPos.y, 3, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.restore();

      // Animate frame counter when live
      if (!isEffectivelyPaused) {
        offsetRef.current += 1;
      }

      animFrameRef.current = requestAnimationFrame(render);
    };

    animFrameRef.current = requestAnimationFrame(render);

    return () => {
      running = false;
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    };
  }, [isEffectivelyPaused, activeSegment, hoverPos]);

  // Handle canvas mouse move for crosshair
  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    setHoverPos({ x, y });

    // Calculate approximate values for readout chip
    const normX = Math.max(0, Math.min(1, (x - 46) / (rect.width - 92)));
    const t = (73.0 + normX * 2.1).toFixed(3);
    const prim = (380 + (1 - y / rect.height) * 45).toFixed(1);
    const sec = (70 + (1 - y / rect.height) * 20).toFixed(1);

    setHoverReadout({
      time: `${t}s`,
      primaryVal: `${prim}V`,
      secondaryVal: `${sec}%`,
    });
  };

  const handleMouseLeave = () => {
    setHoverPos(null);
    setHoverReadout(null);
  };

  return (
    <div className="relative flex flex-1 min-h-[200px] flex-col overflow-hidden">
      {/* Panel Header: seamless row on frosted glass card */}
      <div className="flex h-11 shrink-0 select-none items-center justify-between border-b border-border/60 px-3.5">
        {/* Left: Section Title */}
        <div className="flex items-center gap-2">
          <div className="flex h-5 w-5 items-center justify-center rounded-md bg-accent-soft text-accent">
            <Activity className="h-3 w-3" />
          </div>
          <span className="text-[13px] font-semibold text-text-hi">
            Grafik & Sinyal Analizi
          </span>
        </div>

        {/* Right: Legend Chips + Divider + Corner Controls + Segmented Switch */}
        <div className="flex items-center gap-3">
          {/* Legend — inline: dot + SANS name micro + MONO value + unit low, no boxes */}
          <div className="flex items-center gap-3">
            {seriesList.map((s) => (
              <div
                key={s.id}
                className="flex items-center gap-1.5"
              >
                <span
                  style={{ backgroundColor: s.color }}
                  className="h-2 w-2 rounded-full shrink-0"
                />
                <span className="font-sans text-[10px] font-semibold uppercase tracking-[0.08em] text-text-mid">{s.name}</span>
                <span className="font-mono text-[12.5px] text-text-hi">{s.value}</span>
                <span className="font-mono text-[10px] text-text-low">{s.unit}</span>
              </div>
            ))}
          </div>

          {/* Divider */}
          <div className="h-4 w-px bg-border mx-0.5" />

          {/* Segmented — kesik 8px pill with 6px active halves */}
          <div className="flex items-center rounded-[8px] p-0.5 font-sans text-[12px] font-medium bg-surface-inset border border-surface-inset-border">
            <button
              onClick={() => setActiveSegment('oscilloscope')}
              className={`rounded-[6px] px-2.5 py-0.5 transition-colors ${
                activeSegment === 'oscilloscope'
                  ? 'bg-accent-soft text-accent-text border border-accent-line font-medium'
                  : 'text-text-mid hover:text-text-hi'
              }`}
            >
              Osiloskop
            </button>
            <button
              onClick={() => setActiveSegment('heatmap')}
              className={`rounded-[6px] px-2.5 py-0.5 transition-colors ${
                activeSegment === 'heatmap'
                  ? 'bg-accent-soft text-accent-text border border-accent-line font-medium'
                  : 'text-text-mid hover:text-text-hi'
              }`}
            >
              Isı Haritası
            </button>
          </div>

          {/* Divider */}
          <div className="h-4 w-px bg-border mx-0.5" />

          {/* Corner Controls — kesik 6px buttons */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => setIsPaused((prev) => !prev)}
              className="flex h-6 w-6 items-center justify-center rounded-[6px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi"
              title={isPaused ? 'Osiloskobu Devam Ettir' : 'Osiloskobu Dondur'}
            >
              {isPaused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
            </button>
            <button
              className="flex h-6 w-6 items-center justify-center rounded-[6px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi"
              title="ASIL-B/D Sinyal Doğrulama Durumu"
            >
              <Shield className="h-3.5 w-3.5 text-add" />
            </button>
            <button
              className="flex h-6 w-6 items-center justify-center rounded-[6px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi"
              title="Tam Ekran Osiloskop"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* FLOATING ALERT BANNER — banner hairline %20, AI-Analiz del outline, kesik 6px */}
      <div className="mx-3 my-1.5 flex h-8 shrink-0 select-none items-center justify-between rounded-[6px] border border-deledge/20 bg-delbg/70 px-3 backdrop-blur-sm transition-all">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-del" />
          <span className="font-sans text-[11.5px] font-semibold text-text-hi">
            RPM Ani Düşüşü: -575 RPM (Tekleme Çentiği!)
          </span>
        </div>

        <div className="flex items-center gap-2">
          {showAlertDetails && (
            <span className="rounded-[4px] border border-deledge/25 bg-delbg px-2 py-0.5 font-mono text-[10.5px] text-del">
              SPN 190 FMI 2 · Süre: 40ms
            </span>
          )}
          <button
            onClick={() => {
              setShowAlertDetails((prev) => !prev);
              if (onAnalyzeFault) onAnalyzeFault();
            }}
            className="flex items-center gap-1.5 rounded-[6px] border border-deledge/50 px-2 py-0.5 font-sans text-[11.5px] font-medium text-del transition-all hover:bg-deledge/20 active:scale-[0.98]"
          >
            <Cpu className="h-3 w-3" />
            <span>AI Analiz</span>
          </button>
        </div>
      </div>

      {/* Scope Canvas Area — glass wash behind, sheen on top (both pointer-transparent) */}
      <div className="relative flex-1 min-h-[140px] w-full overflow-hidden">
        <canvas
          ref={canvasRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          className="absolute inset-0 h-full w-full cursor-crosshair"
        />
        {/* Top glass reflection — whisper sheen, never intercepts mouse */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 z-10 h-20 bg-gradient-to-b from-white/[0.035] to-transparent"
        />

        {/* Floating Hover Readout Chip */}
        {hoverPos && hoverReadout && (
          <div
            style={{
              left: `${Math.min(window.innerWidth - 300, hoverPos.x + 14)}px`,
              top: `${Math.max(10, hoverPos.y - 36)}px`,
            }}
            className="pointer-events-none absolute z-20 flex items-center gap-2 rounded-[8px] glass-popover px-2 py-1 font-mono text-[11px] text-text-hi shadow-xl"
          >
            <span className="text-text-low">t:</span>
            <span className="font-semibold text-accent-text">{hoverReadout.time}</span>
            <span className="text-text-faint">|</span>
            <span className="text-[#1d4ed8] font-semibold">{hoverReadout.primaryVal}</span>
            <span className="text-text-faint">|</span>
            <span className="text-[#16a34a] font-semibold">{hoverReadout.secondaryVal}</span>
          </div>
        )}
      </div>
    </div>
  );
};

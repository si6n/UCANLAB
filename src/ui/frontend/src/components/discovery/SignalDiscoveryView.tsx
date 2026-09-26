import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Play,
  Download,
  Copy,
  CheckCircle2,
  AlertCircle,
  RotateCcw,
  Check,
  Zap,
  Disc,
  Compass,
  Layers,
  Sliders,
  ChevronRight,
  Sparkles,
} from 'lucide-react';
import {
  ReverseEngineeringEngine,
  TargetSignalType,
  TARGET_SIGNAL_CONFIGS,
  ExperimentPhase,
  CapturedFrameRecord,
  SignalCandidate,
  sanitizeDbcIdentifier,
} from '../../services/reverseEngineeringEngine';
import { CANFrame } from '../../types/can';
import { ExportService } from '../../services/exportService';

interface SignalDiscoveryViewProps {
  latestFrame?: CANFrame | null;
  frames?: CANFrame[];
  onStimulusChange?: (levelPercent: number) => void;
  onAskCopilot?: (prompt: string) => void;
}

const TARGETS: { id: TargetSignalType; name: string; unit: string; range: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'accelerator', name: 'Gaz Pedalı Pozisyonu', unit: '%', range: '0 - 100', icon: Zap },
  { id: 'brake', name: 'Fren Basıncı / Anahtarı', unit: 'bar / on-off', range: '0 - 120', icon: Disc },
  { id: 'steering', name: 'Direksiyon Açısı', unit: 'deg', range: '-720 - +720', icon: Compass },
  { id: 'gear', name: 'Şanzıman Vites Kademesi', unit: 'pos', range: 'P, R, N, D', icon: Layers },
  { id: 'custom', name: 'Özel Telemetri Kanalı', unit: 'raw', range: 'Serbest', icon: Sliders },
];

export const SignalDiscoveryView: React.FC<SignalDiscoveryViewProps> = ({
  frames,
  onStimulusChange,
}) => {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [targetType, setTargetType] = useState<TargetSignalType>('accelerator');

  const PHASE_DURATION_SEC = 6;
  const [phase, setPhase] = useState<ExperimentPhase>('IDLE');
  const [countdownSec, setCountdownSec] = useState<number>(PHASE_DURATION_SEC);
  const [capturedFrames, setCapturedFrames] = useState<CapturedFrameRecord[]>([]);
  const [candidates, setCandidates] = useState<SignalCandidate[]>([]);
  const [selectedCandidate, setSelectedCandidate] = useState<SignalCandidate | null>(null);

  const [signalName, setSignalName] = useState('');
  const [scale, setScale] = useState(0.4);
  const [offset, setOffset] = useState(0);
  const [unit, setUnit] = useState('%');
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);

  const targetConfig = TARGET_SIGNAL_CONFIGS[targetType];
  const lastCapturedIdRef = useRef<string | null>(null);
  const terminalBottomRef = useRef<HTMLDivElement>(null);

  // I-14: tum deney zamanlayicilari ref'lerde tutulur; unmount/reset ve yeni
  // deney baslangici tek bir clearAllTimers ile hepsini iptal eder. Boylece
  // bilesen sokuldugunde veya sifirlandiginda arkada kalan interval'lar
  // setState cagirmaz (React "update on unmounted component" + state sizintisi).
  const baseIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const stimIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const recIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const analysisTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  // Nesil jetonu: eski bir deneyin gecikmis adimi yeni deneyin durumunu ezmesin.
  const experimentGenRef = useRef(0);

  const clearAllTimers = useCallback(() => {
    if (baseIntervalRef.current) {
      clearInterval(baseIntervalRef.current);
      baseIntervalRef.current = null;
    }
    if (stimIntervalRef.current) {
      clearInterval(stimIntervalRef.current);
      stimIntervalRef.current = null;
    }
    if (recIntervalRef.current) {
      clearInterval(recIntervalRef.current);
      recIntervalRef.current = null;
    }
    if (analysisTimeoutRef.current) {
      clearTimeout(analysisTimeoutRef.current);
      analysisTimeoutRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      clearAllTimers();
    };
  }, [clearAllTimers]);

  // Capture frames during experiment
  useEffect(() => {
    if (phase !== 'BASELINE' && phase !== 'STIMULUS' && phase !== 'RECOVERY') return;

    const windowFrames = frames ?? [];
    const startIndex = windowFrames.findIndex((f) => f.id !== lastCapturedIdRef.current);
    const newFrames = windowFrames.slice(startIndex >= 0 ? startIndex : windowFrames.length);
    if (newFrames.length === 0) return;

    lastCapturedIdRef.current = windowFrames[windowFrames.length - 1].id ?? null;
    const stimulusPercent = phase === 'STIMULUS' ? 50 : 0;
    const records: CapturedFrameRecord[] = newFrames.map((f) => ({
      timestampSec: f.timeSec,
      canIdHex: f.canIdHex,
      dlc: f.dlc,
      payloadHex: f.dataHex.join(' '),
      phase,
      stimulusPercent,
    }));

    setCapturedFrames((prev) => [...prev.slice(-(300 - records.length)), ...records].slice(-300));
  }, [frames, phase]);

  // Terminal scroll
  useEffect(() => {
    if (phase !== 'IDLE' && terminalBottomRef.current) {
      terminalBottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [capturedFrames.length, phase]);

  // Experiment progression
  const handleStartExperiment = () => {
    const gen = ++experimentGenRef.current;
    clearAllTimers();
    setStep(2);
    setCapturedFrames([]);
    setCandidates([]);
    setSelectedCandidate(null);
    setSavedSuccess(false);

    setPhase('BASELINE');
    setCountdownSec(PHASE_DURATION_SEC);
    if (onStimulusChange) onStimulusChange(0);

    let secLeft = PHASE_DURATION_SEC;
    baseIntervalRef.current = setInterval(() => {
      if (gen !== experimentGenRef.current) return;
      secLeft--;
      setCountdownSec(secLeft);

      if (secLeft <= 0) {
        clearInterval(baseIntervalRef.current!);
        baseIntervalRef.current = null;

        setPhase('STIMULUS');
        setCountdownSec(PHASE_DURATION_SEC);
        if (onStimulusChange) onStimulusChange(50);

        let stimSecLeft = PHASE_DURATION_SEC;
        stimIntervalRef.current = setInterval(() => {
          if (gen !== experimentGenRef.current) return;
          stimSecLeft--;
          setCountdownSec(stimSecLeft);

          if (stimSecLeft <= 0) {
            clearInterval(stimIntervalRef.current!);
            stimIntervalRef.current = null;

            setPhase('RECOVERY');
            setCountdownSec(PHASE_DURATION_SEC);
            if (onStimulusChange) onStimulusChange(0);

            let recSecLeft = PHASE_DURATION_SEC;
            recIntervalRef.current = setInterval(() => {
              if (gen !== experimentGenRef.current) return;
              recSecLeft--;
              setCountdownSec(recSecLeft);

              if (recSecLeft <= 0) {
                clearInterval(recIntervalRef.current!);
                recIntervalRef.current = null;

                setPhase('ANALYSIS');
                analysisTimeoutRef.current = setTimeout(() => {
                  analysisTimeoutRef.current = null;
                  if (gen !== experimentGenRef.current) return;
                  setPhase('COMPLETED');
                  setStep(3);
                }, 400);
              }
            }, 1000);
          }
        }, 1000);
      }
    }, 1000);
  };

  // Run analysis when completed
  useEffect(() => {
    if (phase === 'COMPLETED' && capturedFrames.length > 0) {
      const results = ReverseEngineeringEngine.analyzeCapturedFrames(capturedFrames, targetConfig);
      setCandidates(results);
      if (results.length > 0) {
        const best = results[0];
        setSelectedCandidate(best);
        setSignalName(best.signalName);
        setScale(best.scale);
        setOffset(best.offset);
        setUnit(best.unit);
      }
    }
  }, [phase, capturedFrames, targetConfig]);

  const handleSelectCandidate = (candidate: SignalCandidate) => {
    setSelectedCandidate(candidate);
    setSignalName(candidate.signalName);
    setScale(candidate.scale);
    setOffset(candidate.offset);
    setUnit(candidate.unit);
  };

  const handleExportDbc = async () => {
    if (!selectedCandidate) return;
    const updatedCandidate: SignalCandidate = {
      ...selectedCandidate,
      signalName: signalName || selectedCandidate.signalName,
      scale,
      offset,
      unit,
    };
    const dbcContent = ReverseEngineeringEngine.generateDbcString(updatedCandidate);
    // M-05: dosya adi da DBC-guvenli olmali (bosluk/ozel karakter yok).
    const saved = await ExportService.downloadFile(
      dbcContent,
      `${sanitizeDbcIdentifier(updatedCandidate.signalName)}_Discovered.dbc`,
      'text/plain;charset=utf-8;',
      'Vector CAN DBC Dosyası (*.dbc)'
    );
    if (saved) {
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 3000);
    }
  };

  const handleCopyDbc = () => {
    if (!selectedCandidate) return;
    const updatedCandidate: SignalCandidate = {
      ...selectedCandidate,
      signalName: signalName || selectedCandidate.signalName,
      scale,
      offset,
      unit,
    };
    const dbcContent = ReverseEngineeringEngine.generateDbcString(updatedCandidate);
    navigator.clipboard.writeText(dbcContent);
    setCopySuccess(true);
    setTimeout(() => setCopySuccess(false), 2000);
  };

  const handleReset = () => {
    // I-14: nesli artir + tum zamanlayicilari iptal et — sifirlama sonrasi
    // calisan eski bir deney adimi yeni durumu ezemez.
    experimentGenRef.current += 1;
    clearAllTimers();
    setStep(1);
    setPhase('IDLE');
    setCountdownSec(PHASE_DURATION_SEC);
    setCapturedFrames([]);
    setCandidates([]);
    setSelectedCandidate(null);
    setSavedSuccess(false);
  };

  const getDbcPreview = () => {
    if (!selectedCandidate) return '';
    const updatedCandidate: SignalCandidate = {
      ...selectedCandidate,
      signalName: signalName || selectedCandidate.signalName,
      scale,
      offset,
      unit,
    };
    return ReverseEngineeringEngine.generateDbcString(updatedCandidate);
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden text-text-body font-sans select-none">
      {/* Top Header & Workflow Navigation */}
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/40 px-3">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-semibold uppercase tracking-wider text-text-hi">
            Tersine Mühendislik
          </span>
          <span className="text-text-low">/</span>
          <span className="text-xs text-text-mid">Sinyal Keşif Motoru</span>
        </div>

        {/* Workflow Breadcrumb Tabs */}
        <div className="flex items-center gap-1">
          {[
            { num: 1, label: 'Hedef' },
            { num: 2, label: 'Deney' },
            { num: 3, label: 'Analiz' },
            { num: 4, label: 'DBC' },
          ].map((s) => {
            const isCurrent = step === s.num;
            const isPassed = step > s.num;
            return (
              <button
                key={s.num}
                type="button"
                onClick={() => {
                  if (isPassed || (step === 3 && s.num === 4) || (step === 4 && s.num === 3)) {
                    setStep(s.num as any);
                  }
                }}
                disabled={!isPassed && !isCurrent && !(step === 3 && s.num === 4)}
                className={`flex items-center gap-1.5 px-2.5 py-1 text-xs font-mono rounded transition-colors ${
                  isCurrent
                    ? 'bg-accent/10 font-semibold text-accent'
                    : isPassed
                    ? 'text-text-hi hover:bg-bg-row-hover'
                    : 'text-text-low opacity-40 cursor-not-allowed'
                }`}
              >
                <span className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] ${
                  isCurrent ? 'bg-accent text-white' : isPassed ? 'bg-border text-text-hi' : 'bg-surface-inset text-text-low'
                }`}>
                  {s.num}
                </span>
                <span>{s.label}</span>
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-2">
          {step > 1 && (
            <button
              onClick={handleReset}
              className="inline-flex h-7 items-center gap-1.5 rounded border border-border/70 px-2.5 text-xs text-text-mid hover:text-text-hi hover:border-border transition-colors"
            >
              <RotateCcw className="h-3 w-3" />
              <span>Sıfırla</span>
            </button>
          )}
        </div>
      </div>

      {/* Main Content Area */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {/* STEP 1: Hedef Seçimi */}
        {step === 1 && (
          <div className="flex h-full flex-col justify-between p-6 max-w-2xl">
            <div className="space-y-6">
              <div>
                <h2 className="text-sm font-semibold text-text-hi">Keşfedilecek Hedef Sinyal</h2>
                <p className="mt-1 text-xs text-text-mid">
                  Korelasyon analizi için fiziksel olarak uygulayacağınız eylemi seçin.
                </p>
              </div>

              {/* Targets List */}
              <div className="divide-y divide-border/30 border-y border-border/40">
                {TARGETS.map((t) => {
                  const Icon = t.icon;
                  const isSelected = targetType === t.id;
                  return (
                    <div
                      key={t.id}
                      onClick={() => setTargetType(t.id)}
                      className={`flex items-center justify-between py-3 px-2 cursor-pointer transition-colors ${
                        isSelected ? 'bg-accent/5' : 'hover:bg-bg-row-hover'
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <div className={`p-1.5 rounded ${isSelected ? 'text-accent' : 'text-text-low'}`}>
                          <Icon className="h-4 w-4" />
                        </div>
                        <div>
                          <div className={`text-xs ${isSelected ? 'font-semibold text-accent' : 'text-text-hi'}`}>
                            {t.name}
                          </div>
                          <div className="text-[11px] font-mono text-text-low">
                            Birim: {t.unit} · Aralık: {t.range}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <div
                          className={`h-4 w-4 rounded-full border flex items-center justify-center transition-colors ${
                            isSelected ? 'border-accent bg-accent' : 'border-border/80'
                          }`}
                        >
                          {isSelected && <div className="h-1.5 w-1.5 rounded-full bg-white" />}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Protocol Spec Strip */}
              <div className="flex items-center justify-between border-t border-border/20 pt-4 font-mono text-xs text-text-mid">
                <span>Protokol: 3 Faz (Taban -&gt; Uyarı -&gt; Dönüş)</span>
                <span>Süre: 18sn</span>
                <span>Filtre: Pearson r &gt; 0.65</span>
              </div>
            </div>

            <div>
              <button
                onClick={handleStartExperiment}
                className="inline-flex h-8 items-center gap-2 rounded bg-accent px-4 text-xs font-medium text-white transition-all hover:bg-accent/90 active:scale-[0.98]"
              >
                <Play className="h-3.5 w-3.5 fill-current" />
                <span>Deneyi Başlat</span>
              </button>
            </div>
          </div>
        )}

        {/* STEP 2: Deney Koşumu (Live Stimulus Execution) */}
        {step === 2 && (
          <div className="grid h-full grid-cols-12 overflow-hidden">
            {/* Left Control Column */}
            <div className="col-span-4 flex flex-col justify-between border-r border-border/40 p-6">
              <div className="space-y-6">
                <div>
                  <div className="font-mono text-xs uppercase tracking-wider text-text-low">
                    Aşama Durumu
                  </div>
                  <div className="mt-1 text-base font-semibold text-text-hi">
                    {phase === 'BASELINE' && '1. Taban Çizgisi'}
                    {phase === 'STIMULUS' && '2. Uyarı (Stimulus)'}
                    {phase === 'RECOVERY' && '3. Geri Dönüş'}
                    {phase === 'ANALYSIS' && 'Korelasyon Analizi'}
                    {phase === 'COMPLETED' && 'Tamamlandı'}
                  </div>
                </div>

                {/* Big Digital Countdown */}
                <div className="py-2">
                  <div className="font-mono text-5xl font-light tracking-tight text-accent">
                    00:{countdownSec < 10 ? `0${countdownSec}` : countdownSec}
                  </div>
                  <div className="mt-2 text-xs text-text-mid leading-relaxed">
                    {phase === 'BASELINE' && 'Pedala basmayın. Rölanti veriyolu taban çizgisi kaydediliyor.'}
                    {phase === 'STIMULUS' && targetConfig.stimulusInstruction}
                    {phase === 'RECOVERY' && targetConfig.recoveryInstruction}
                    {phase === 'ANALYSIS' && 'Pearson korelasyonu ve zaman gecikmesi hesaplanıyor...'}
                  </div>
                </div>

                {/* Captured Frame Counter */}
                <div className="border-t border-border/30 pt-4 space-y-1">
                  <div className="flex justify-between font-mono text-xs">
                    <span className="text-text-mid">Kaydedilen Kareler</span>
                    <span className="font-semibold text-text-hi">{capturedFrames.length}</span>
                  </div>
                  <div className="flex justify-between font-mono text-xs">
                    <span className="text-text-mid">Fiziksel Hedef</span>
                    <span className="text-text-hi">{targetConfig.name}</span>
                  </div>
                </div>
              </div>

              <div className="font-mono text-[11px] text-text-low">
                ISO 11898-2 CAN Örnekleme · 100 Hz
              </div>
            </div>

            {/* Right Terminal Log */}
            <div className="col-span-8 flex flex-col overflow-hidden bg-bg-app/40 font-mono text-xs">
              <div className="flex h-8 shrink-0 items-center justify-between border-b border-border/40 px-3 text-[11px] text-text-low">
                <span>Zaman</span>
                <span>CAN ID</span>
                <span>DLC</span>
                <span>Yük (Hex)</span>
                <span>Aşama</span>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto p-2 space-y-0.5">
                {capturedFrames.map((f, i) => (
                  <div key={i} className="flex items-center justify-between py-0.5 px-1 hover:bg-bg-row-hover rounded text-[11px]">
                    <span className="w-16 text-text-low">{f.timestampSec.toFixed(3)}s</span>
                    <span className="w-16 font-semibold text-accent">{f.canIdHex}</span>
                    <span className="w-10 text-text-low">{f.dlc}</span>
                    <span className="flex-1 text-text-hi tracking-wide">{f.payloadHex}</span>
                    <span className={`w-16 text-right ${
                      f.phase === 'STIMULUS' ? 'text-accent font-semibold' : 'text-text-low'
                    }`}>
                      {f.phase}
                    </span>
                  </div>
                ))}
                <div ref={terminalBottomRef} />
              </div>
            </div>
          </div>
        )}

        {/* STEP 3: Analiz & Bit Matrisi (Results & Inspection) */}
        {step === 3 && (
          <div className="grid h-full grid-cols-12 overflow-hidden">
            {/* Left Candidates List */}
            <div className="col-span-5 flex flex-col border-r border-border/40 overflow-hidden">
              <div className="flex h-9 shrink-0 items-center justify-between border-b border-border/40 px-3">
                <span className="font-mono text-xs font-semibold text-text-hi">
                  Aday Sinyaller ({candidates.length})
                </span>
                <span className="font-mono text-[11px] text-text-low">Pearson r Eşiği &gt; 0.65</span>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto divide-y divide-border/20">
                {candidates.length === 0 ? (
                  <div className="p-6 text-center text-xs text-text-low">
                    Aday bulunamadı. Deneyi tekrarlayın.
                  </div>
                ) : (
                  candidates.map((cand, idx) => {
                    const isSelected = selectedCandidate?.canIdHex === cand.canIdHex && selectedCandidate?.startBit === cand.startBit;
                    return (
                      <div
                        key={`${cand.canIdHex}-${cand.startBit}-${idx}`}
                        onClick={() => handleSelectCandidate(cand)}
                        className={`p-3 cursor-pointer transition-colors ${
                          isSelected ? 'bg-accent/10' : 'hover:bg-bg-row-hover'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-xs font-semibold text-text-hi">
                            {cand.canIdHex} · {cand.signalName}
                          </span>
                          <span className={`font-mono text-xs font-bold ${
                            cand.pearsonR > 0.85 ? 'text-add' : 'text-accent'
                          }`}>
                            r = {cand.pearsonR.toFixed(3)}
                          </span>
                        </div>

                        <div className="mt-1 flex items-center justify-between font-mono text-[11px] text-text-low">
                          <span>Bit: {cand.startBit}..{cand.startBit + cand.bitLength - 1} ({cand.bitLength}b)</span>
                          <span>Gecikme: {cand.timeLagMs} ms</span>
                          <span className="capitalize">{cand.confidenceLevel}</span>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            {/* Right: 64-Bit Payload Matrix */}
            <div className="col-span-7 flex flex-col justify-between p-6 overflow-y-auto">
              <div className="space-y-6">
                <div>
                  <div className="font-mono text-xs uppercase tracking-wider text-text-low">
                    64-Bit Yük Matrisi
                  </div>
                  <div className="mt-1 text-xs text-text-mid">
                    Seçili aday: <span className="font-mono font-semibold text-text-hi">{selectedCandidate?.canIdHex || 'Yok'}</span>
                  </div>
                </div>

                {/* Bit Matrix Render */}
                <div className="font-mono text-xs">
                  <div className="grid grid-cols-9 gap-1 pb-1 text-[11px] text-text-low border-b border-border/30">
                    <span className="text-left">Bayt</span>
                    {[7, 6, 5, 4, 3, 2, 1, 0].map((b) => (
                      <span key={b} className="text-center font-semibold">b{b}</span>
                    ))}
                  </div>

                  <div className="divide-y divide-border/20 pt-1">
                    {Array.from({ length: 8 }, (_, byteIdx) => {
                      const start = selectedCandidate?.startBit ?? -1;
                      const len = selectedCandidate?.bitLength ?? 0;
                      const end = start + len;

                      return (
                        <div key={byteIdx} className="grid grid-cols-9 gap-1 py-1 items-center">
                          <span className="text-[11px] font-semibold text-text-low">B{byteIdx}</span>
                          {Array.from({ length: 8 }, (_, bitCol) => {
                            const bitNumber = byteIdx * 8 + (7 - bitCol);
                            const isSignalBit = bitNumber >= start && bitNumber < end;

                            return (
                              <span
                                key={bitCol}
                                title={`Bit ${bitNumber}`}
                                className={`h-6 rounded text-center leading-6 transition-all text-xs ${
                                  isSignalBit
                                    ? 'bg-accent text-white font-bold'
                                    : 'bg-surface-inset text-text-low'
                                }`}
                              >
                                {isSignalBit ? '1' : '0'}
                              </span>
                            );
                          })}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Candidate Specs */}
                {selectedCandidate && (
                  <div className="grid grid-cols-3 gap-4 border-t border-border/30 pt-4 font-mono text-xs">
                    <div>
                      <span className="text-text-low">Başlangıç Biti</span>
                      <div className="text-text-hi font-semibold">{selectedCandidate.startBit}</div>
                    </div>
                    <div>
                      <span className="text-text-low">Uzunluk</span>
                      <div className="text-text-hi font-semibold">{selectedCandidate.bitLength} bit</div>
                    </div>
                    <div>
                      <span className="text-text-low">Sıralama</span>
                      <div className="text-text-hi font-semibold">Little-Endian (Intel)</div>
                    </div>
                  </div>
                )}
              </div>

              <div className="flex justify-end pt-4">
                <button
                  onClick={() => setStep(4)}
                  disabled={!selectedCandidate}
                  className="inline-flex h-8 items-center gap-1.5 rounded bg-accent px-4 text-xs font-medium text-white transition-all hover:bg-accent/90 disabled:opacity-40"
                >
                  <span>DBC Parametreleri</span>
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* STEP 4: DBC Parametreleri & İndirme */}
        {step === 4 && (
          <div className="grid h-full grid-cols-12 overflow-hidden">
            {/* Left Parameters */}
            <div className="col-span-5 flex flex-col justify-between border-r border-border/40 p-6 overflow-y-auto">
              <div className="space-y-4">
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-text-low font-mono">
                    DBC Sinyal Ayarları
                  </h3>
                  <p className="mt-1 text-xs text-text-mid">
                    Vector CANdb++ standart sözdizimine dönüştürülecek alanlar.
                  </p>
                </div>

                <div className="space-y-3 pt-2">
                  <div>
                    <label className="text-[11px] font-mono text-text-low">Sinyal Adı</label>
                    <input
                      type="text"
                      value={signalName}
                      onChange={(e) => setSignalName(e.target.value)}
                      className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi outline-none focus:border-accent"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-[11px] font-mono text-text-low">Ölçek (Scale)</label>
                      <input
                        type="number"
                        step="0.01"
                        value={scale}
                        onChange={(e) => setScale(parseFloat(e.target.value) || 1)}
                        className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi outline-none focus:border-accent"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] font-mono text-text-low">Ofset (Offset)</label>
                      <input
                        type="number"
                        step="1"
                        value={offset}
                        onChange={(e) => setOffset(parseFloat(e.target.value) || 0)}
                        className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi outline-none focus:border-accent"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="text-[11px] font-mono text-text-low">Birim (Unit)</label>
                    <input
                      type="text"
                      value={unit}
                      onChange={(e) => setUnit(e.target.value)}
                      className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi outline-none focus:border-accent"
                    />
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 pt-4">
                <button
                  onClick={handleExportDbc}
                  className="inline-flex h-8 items-center gap-1.5 rounded bg-accent px-3.5 text-xs font-medium text-white transition-all hover:bg-accent/90"
                >
                  {savedSuccess ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Download className="h-3.5 w-3.5" />}
                  <span>{savedSuccess ? 'İndirildi' : 'DBC İndir'}</span>
                </button>

                <button
                  onClick={handleCopyDbc}
                  className="inline-flex h-8 items-center gap-1.5 rounded border border-border/70 px-3 text-xs text-text-hi hover:border-border transition-colors"
                >
                  {copySuccess ? <Check className="h-3.5 w-3.5 text-add" /> : <Copy className="h-3.5 w-3.5" />}
                  <span>{copySuccess ? 'Kopyalandı' : 'Kopyala'}</span>
                </button>
              </div>
            </div>

            {/* Right: DBC Code Preview */}
            <div className="col-span-7 flex flex-col overflow-hidden bg-bg-app/40 font-mono text-xs">
              <div className="flex h-9 shrink-0 items-center justify-between border-b border-border/40 px-4 text-[11px] text-text-low">
                <span>Vector CANdb++ Çıktısı</span>
                <span>ASCII / UTF-8</span>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto p-4 leading-relaxed text-text-hi select-all">
                <pre className="font-mono text-xs whitespace-pre-wrap">{getDbcPreview()}</pre>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

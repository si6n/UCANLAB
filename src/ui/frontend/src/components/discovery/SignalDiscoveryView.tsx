import React, { useState, useEffect } from 'react';
import { 
 Wand2, 
 Play, 
 ShieldCheck, 
 Activity, 
 CheckCircle2, 
 AlertTriangle, 
 RotateCcw, 
 Download, 
 Sliders, 
 Sparkles, 
 ArrowRight,
 Info,
 FileCode,
 Copy,
 ChevronRight,
 Timer,
 Zap,
 Cpu,
 Terminal,
 Layers,
 Gauge,
 Hand,
 Check,
 CircleDot
} from 'lucide-react';
import { 
 ReverseEngineeringEngine, 
 TargetSignalType, 
 TARGET_SIGNAL_CONFIGS, 
 ExperimentPhase, 
 CapturedFrameRecord, 
 SignalCandidate 
} from '../../services/reverseEngineeringEngine';
import { CANFrame } from '../../types/can';
import { ExportService } from '../../services/exportService';

interface SignalDiscoveryViewProps {
 latestFrame?: CANFrame | null;
 frames?: CANFrame[];
 onStimulusChange?: (levelPercent: number) => void;
 onAskCopilot?: (prompt: string) => void;
}

export const SignalDiscoveryView: React.FC<SignalDiscoveryViewProps> = ({
 latestFrame,
 onStimulusChange
}) => {
 // Wizard Step: 1 = Setup, 2 = Live Experiment, 3 = Evidence Inspector, 4 = Human Review & DBC
 const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
 const [targetType, setTargetType] = useState<TargetSignalType>('accelerator');
 
 // Experiment State (6 seconds per phase = 18s total robust test)
 const PHASE_DURATION_SEC = 6;
 const [phase, setPhase] = useState<ExperimentPhase>('IDLE');
 const [countdownSec, setCountdownSec] = useState<number>(PHASE_DURATION_SEC);
 const [capturedFrames, setCapturedFrames] = useState<CapturedFrameRecord[]>([]);
 const [candidates, setCandidates] = useState<SignalCandidate[]>([]);
 const [selectedCandidate, setSelectedCandidate] = useState<SignalCandidate | null>(null);

 // Human Review & DBC Edit State
 const [signalName, setSignalName] = useState('');
 const [scale, setScale] = useState(0.4);
 const [offset, setOffset] = useState(0);
 const [unit, setUnit] = useState('%');
 const [savedSuccess, setSavedSuccess] = useState(false);
 const [copySuccess, setCopySuccess] = useState(false);

 const targetConfig = TARGET_SIGNAL_CONFIGS[targetType];

 // Capture incoming frames during active experiment
 useEffect(() => {
 if (!latestFrame || (phase !== 'BASELINE' && phase !== 'STIMULUS' && phase !== 'RECOVERY')) {
 return;
 }

 const stimulusPercent = phase === 'STIMULUS' ? 50 : 0;
 const record: CapturedFrameRecord = {
 timestampSec: latestFrame.timeSec,
 canIdHex: latestFrame.canIdHex,
 dlc: latestFrame.dlc,
 payloadHex: latestFrame.dataHex.join(' '),
 phase,
 stimulusPercent
 };

 setCapturedFrames(prev => [...prev.slice(-400), record]);
 }, [latestFrame, phase]);

 // Start Multi-Phase Experiment Workflow (6 seconds per phase)
 const handleStartExperiment = () => {
 setStep(2);
 setCapturedFrames([]);
 setCandidates([]);
 setSelectedCandidate(null);
 setSavedSuccess(false);

 // 1. Start BASELINE Phase (6 seconds)
 setPhase('BASELINE');
 setCountdownSec(PHASE_DURATION_SEC);
 if (onStimulusChange) onStimulusChange(0);

 let secLeft = PHASE_DURATION_SEC;
 const baseInterval = setInterval(() => {
 secLeft--;
 setCountdownSec(secLeft);

 if (secLeft <= 0) {
 clearInterval(baseInterval);

 // 2. Start STIMULUS Phase (6 seconds)
 setPhase('STIMULUS');
 setCountdownSec(PHASE_DURATION_SEC);
 if (onStimulusChange) onStimulusChange(50);

 let stimSecLeft = PHASE_DURATION_SEC;
 const stimInterval = setInterval(() => {
 stimSecLeft--;
 setCountdownSec(stimSecLeft);

 if (stimSecLeft <= 0) {
 clearInterval(stimInterval);

 // 3. Start RECOVERY Phase (6 seconds)
 setPhase('RECOVERY');
 setCountdownSec(PHASE_DURATION_SEC);
 if (onStimulusChange) onStimulusChange(0);

 let recSecLeft = PHASE_DURATION_SEC;
 const recInterval = setInterval(() => {
 recSecLeft--;
 setCountdownSec(recSecLeft);

 if (recSecLeft <= 0) {
 clearInterval(recInterval);

 // 4. Complete & Analyze
 setPhase('ANALYSIS');
 setTimeout(() => {
 setPhase('COMPLETED');
 setStep(3);
 }, 600);
 }
 }, 1000);
 }
 }, 1000);
 }
 }, 1000);
 };

 // Run deterministic analysis when experiment completes
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
 unit
 };
 const dbcContent = ReverseEngineeringEngine.generateDbcString(updatedCandidate);
 const saved = await ExportService.downloadFile(
 dbcContent, 
 `${updatedCandidate.signalName}_Discovered.dbc`, 
 'text/plain;charset=utf-8;',
 'Vector CAN DBC Dosyası (*.dbc)'
 );
 if (saved) {
 setSavedSuccess(true);
 setTimeout(() => setSavedSuccess(false), 4000);
 }
 };

 const handleCopyDbc = () => {
 if (!selectedCandidate) return;
 const updatedCandidate: SignalCandidate = {
 ...selectedCandidate,
 signalName: signalName || selectedCandidate.signalName,
 scale,
 offset,
 unit
 };
 const dbcContent = ReverseEngineeringEngine.generateDbcString(updatedCandidate);
 navigator.clipboard.writeText(dbcContent);
 setCopySuccess(true);
 setTimeout(() => setCopySuccess(false), 3000);
 };

 const handleReset = () => {
 setStep(1);
 setPhase('IDLE');
 setCountdownSec(PHASE_DURATION_SEC);
 setCapturedFrames([]);
 setCandidates([]);
 setSelectedCandidate(null);
 setSavedSuccess(false);
 };

 // Calculate overall progress across 3 phases (18s total)
 const getOverallProgressPercent = () => {
 if (phase === 'BASELINE') {
 return Math.round(((PHASE_DURATION_SEC - countdownSec) / PHASE_DURATION_SEC) * 33.33);
 }
 if (phase === 'STIMULUS') {
 return Math.round(33.33 + ((PHASE_DURATION_SEC - countdownSec) / PHASE_DURATION_SEC) * 33.33);
 }
 if (phase === 'RECOVERY') {
 return Math.round(66.66 + ((PHASE_DURATION_SEC - countdownSec) / PHASE_DURATION_SEC) * 33.34);
 }
 if (phase === 'ANALYSIS' || phase === 'COMPLETED') {
 return 100;
 }
 return 0;
 };

 return (
 <div className="p-4 space-y-4 max-w-7xl mx-auto">
 {/* Header Banner */}
 <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-card flex items-center justify-between">
 <div className="flex items-center space-x-3">
 <div className="w-10 h-10 rounded-lg bg-brand-50 border border-brand-200 flex items-center justify-center text-brand-600">
 <Wand2 className="w-5 h-5" />
 </div>
 <div>
 <div className="flex items-center space-x-2">
 <h2 className="text-sm font-bold text-slate-900">CAN-Bus Reverse Engineering & Sinyal Çözümleme</h2>
 <span className="bg-brand-50 text-brand-700 text-xs font-semibold px-2 py-0.5 rounded-md border border-brand-200">
 Deterministik Kanıt Protokolü v2.0
 </span>
 </div>
 <p className="text-xs text-slate-500">
 Bilinmeyen CAN-Bus ağlarındaki çerçeveleri 6 saniyelik kontrollü Uyarı-Tepki (Stimulus-Response) deneyleri ile çözün ve DBC üretin
 </p>
 </div>
 </div>

 {/* Step Indicator Badges */}
 <div className="hidden md:flex items-center space-x-1 text-xs">
 {[
 { num: 1, label: 'Hedef Profili' },
 { num: 2, label: 'Canlı Deney' },
 { num: 3, label: 'Kanıt Analizi' },
 { num: 4, label: 'DBC Üretimi' }
 ].map((s, idx) => (
 <React.Fragment key={s.num}>
 <div 
 className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border font-medium transition-all ${
 step === s.num 
 ? 'bg-brand-600 text-white border-brand-700 shadow-xs' 
 : step > s.num
 ? 'bg-signal-50 text-signal-700 border-signal-200'
 : 'bg-slate-50 text-slate-500 border-slate-200'
 }`}
 >
 <span className="font-bold">{s.num}.</span>
 <span>{s.label}</span>
 </div>
 {idx < 3 && <ChevronRight className="w-3.5 h-3.5 text-slate-300" />}
 </React.Fragment>
 ))}
 </div>
 </div>

 {/* Step 1: Target Signal Selection */}
 {step === 1 && (
 <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
 {/* Left Column: Target Signal Selection */}
 <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-4">
 <div className="flex items-center justify-between border-b border-slate-100 pb-3">
 <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider flex items-center space-x-1.5">
 <Sliders className="w-4 h-4 text-brand-600" />
 <span>1. Çözümlenecek Hedef Sinyal Profilini Seçin</span>
 </h3>
 <span className="text-xs text-slate-500 font-medium">5 Standart Profil</span>
 </div>

 <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
 {(Object.keys(TARGET_SIGNAL_CONFIGS) as TargetSignalType[]).map((type) => {
 const conf = TARGET_SIGNAL_CONFIGS[type];
 const isSelected = targetType === type;
 return (
 <button
 key={type}
 onClick={() => setTargetType(type)}
 className={`text-left p-4 rounded-xl border transition-all space-y-2 ${
 isSelected
 ? 'border-brand-600 bg-brand-50/40 shadow-xs ring-1 ring-brand-500/30'
 : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50'
 }`}
 >
 <div className="flex items-center justify-between">
 <span className="font-bold text-xs text-slate-900">{conf.name}</span>
 <span className="text-xs font-mono font-semibold bg-white border border-slate-200 px-2 py-0.5 rounded text-slate-600">
 {conf.unit}
 </span>
 </div>
 <p className="text-xs text-slate-500 line-clamp-2">
 {conf.stimulusInstruction}
 </p>
 <div className="text-xs text-brand-600 font-semibold flex items-center space-x-1 pt-1">
 <span>Beklenen Aralık:</span>
 <span className="font-mono">{conf.expectedMin} .. {conf.expectedMax} {conf.unit}</span>
 </div>
 </button>
 );
 })}
 </div>

 {/* Safety Floor Verification */}
 <div className="p-3.5 bg-slate-50 rounded-xl border border-slate-200 flex items-start space-x-3">
 <ShieldCheck className="w-5 h-5 text-signal-600 shrink-0 mt-0.5" />
 <div className="space-y-0.5 text-xs">
 <div className="font-bold text-slate-800">Pasif Dinleme & Sıfır Hız Güvenlik Kilidi (Safe-by-Default)</div>
 <p className="text-slate-500 text-xs leading-relaxed">
 Deney tamamen pasif dinleme modunda çalışır; CAN-Bus hattına yabancı paket basılmaz ve araç hızı 0 km/h durumunda 18 saniyelik kesintisiz veri toplanır.
 </p>
 </div>
 </div>

 {/* Start Button */}
 <div className="pt-2">
 <button
 onClick={handleStartExperiment}
 className="w-full flex items-center justify-center space-x-2 py-2.5 px-4 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-xs font-bold shadow-xs transition-colors active:scale-[0.99]"
 >
 <Play className="w-4 h-4 fill-current" />
 <span>18 Saniyelik Kontrollü Deneyi Başlat (3 Faz x 6sn)</span>
 <ArrowRight className="w-4 h-4 ml-1" />
 </button>
 </div>
 </div>

 {/* Right Column: Experiment Overview & Protocol Info */}
 <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-4 flex flex-col justify-between">
 <div className="space-y-4">
 <div className="flex items-center space-x-2 border-b border-slate-100 pb-3">
 <Info className="w-4 h-4 text-slate-600" />
 <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">
 6 Saniyelik Deney Protokolü
 </h3>
 </div>

 <div className="space-y-3 text-xs text-slate-600">
 <div className="flex items-start space-x-2.5">
 <div className="w-6 h-6 rounded-full bg-brand-50 text-brand-700 border border-brand-200 font-bold flex items-center justify-center shrink-0 text-xs">
 1
 </div>
 <div>
 <strong className="text-slate-800">Taban Çizgisi (Baseline - 6sn):</strong>
 <p className="text-xs text-slate-500">Hiçbir eylem yapılmaz. CAN hattındaki arka plan periyodik sinyalleri ve durağan baytlar kaydedilir.</p>
 </div>
 </div>

 <div className="flex items-start space-x-2.5">
 <div className="w-6 h-6 rounded-full bg-brand-50 text-brand-700 border border-brand-200 font-bold flex items-center justify-center shrink-0 text-xs">
 2
 </div>
 <div>
 <strong className="text-slate-800">Kontrollü Uyarı (Stimulus - 6sn):</strong>
 <p className="text-xs text-slate-500">Yönlendirilen pedal/direksiyon eylemi uygulanır. Eyleme bağlı ani bit ve bayt değişimleri yakalanır.</p>
 </div>
 </div>

 <div className="flex items-start space-x-2.5">
 <div className="w-6 h-6 rounded-full bg-signal-50 text-signal-700 border border-signal-200 font-bold flex items-center justify-center shrink-0 text-xs">
 3
 </div>
 <div>
 <strong className="text-slate-800">Geri Toparlanma (Recovery - 6sn):</strong>
 <p className="text-xs text-slate-500">Eylem serbest bırakılarak rölantiye dönülür. Sinyalin başlangıç durumuna dönme tepkisi doğrulanır.</p>
 </div>
 </div>

 <div className="flex items-start space-x-2.5">
 <div className="w-6 h-6 rounded-full bg-brand-50 text-brand-700 border border-brand-200 font-bold flex items-center justify-center shrink-0 text-xs">
 4
 </div>
 <div>
 <strong className="text-slate-800">İstatistiksel Kanıt & Eleme:</strong>
 <p className="text-xs text-slate-500">Pearson (r), Spearman (rho), Time-Lag analizi yapılır. Sayaç/CRC alanları elenip DBC çıktısı üretilir.</p>
 </div>
 </div>
 </div>
 </div>

 <div className="p-3 bg-brand-50/50 rounded-lg border border-brand-100 text-xs text-brand-800 font-medium">
 <strong>İpucu:</strong> 6 saniyelik geniş pencere sayesinde pedal veya direksiyon eylemlerinin yükselme/düşme eğrileri yüksek güven skoruyla yakalanır.
 </div>
 </div>
 </div>
 )}

 {/* Step 2: Live Experiment Runner (Light, Minimal & Ultra-Clean Cockpit Theme) */}
 {step === 2 && (
 <div className="space-y-4">
 {/* Top Phase Stepper Cards */}
 <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
 {[
 { id: 'BASELINE', label: '1. Taban Çizgisi (Baseline)', desc: 'Rölanti / Hareketsiz', icon: Timer },
 { id: 'STIMULUS', label: '2. Kontrollü Uyarı (Stimulus)', desc: targetConfig.name, icon: Zap },
 { id: 'RECOVERY', label: '3. Toparlanma (Recovery)', desc: 'Bırak / Rölantiye Dön', icon: RotateCcw }
 ].map((p) => {
 const Icon = p.icon;
 const isActive = phase === p.id;
 const isPast = (phase === 'STIMULUS' && p.id === 'BASELINE') || 
 (phase === 'RECOVERY' && (p.id === 'BASELINE' || p.id === 'STIMULUS')) ||
 (phase === 'ANALYSIS' || phase === 'COMPLETED');
 return (
 <div
 key={p.id}
 className={`bg-white rounded-xl p-3.5 border transition-all shadow-card flex items-center justify-between ${
 isActive
 ? 'border-brand-600 bg-brand-50/30 ring-1 ring-brand-500/20'
 : isPast
 ? 'border-signal-300 bg-signal-50/30'
 : 'border-slate-200 opacity-60'
 }`}
 >
 <div className="flex items-center space-x-3">
 <div className={`w-8 h-8 rounded-lg flex items-center justify-center font-semibold text-xs ${
 isActive 
 ? 'bg-brand-600 text-white shadow-xs' 
 : isPast 
 ? 'bg-signal-600 text-white' 
 : 'bg-slate-100 text-slate-500'
 }`}>
 <Icon className="w-4 h-4" />
 </div>
 <div>
 <div className="text-xs font-bold text-slate-900">{p.label}</div>
 <div className="text-xs text-slate-500">{p.desc} (6 sn)</div>
 </div>
 </div>

 <div>
 {isActive ? (
 <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-mono font-bold bg-brand-600 text-white shadow-xs">
 {countdownSec}s
 </span>
 ) : isPast ? (
 <CheckCircle2 className="w-4 h-4 text-signal-600" />
 ) : (
 <span className="text-xs font-mono text-slate-500">6s</span>
 )}
 </div>
 </div>
 );
 })}
 </div>

 {/* Cockpit Action Area Split */}
 <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
 {/* Left Box: Prominent Minimal Directive Card */}
 <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 shadow-card flex flex-col justify-between space-y-4">
 {/* Header Bar */}
 <div className="flex items-center justify-between border-b border-slate-100 pb-3">
 <div className="flex items-center space-x-2">
 <Activity className="w-4 h-4 text-brand-600 animate-pulse" />
 <span className="text-xs font-bold text-slate-900 uppercase tracking-wider">
 Deney Talimatları & Geri Sayım
 </span>
 </div>

 <button
 onClick={handleReset}
 className="flex items-center space-x-1.5 px-2.5 py-1 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-medium border border-slate-200 transition-colors"
 >
 <RotateCcw className="w-3.5 h-3.5" />
 <span>Deneyi İptal Et</span>
 </button>
 </div>

 {/* Minimal, High-Clarity Light Directive Box */}
 <div className="bg-slate-50/80 border border-slate-200 rounded-xl p-6 text-center space-y-4">
 {/* Phase Pill Badge */}
 <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full text-xs font-semibold border shadow-2xs">
 {phase === 'BASELINE' && (
 <span className="text-amber-800 bg-amber-50 border-amber-200 px-3 py-0.5 rounded-full flex items-center space-x-1.5">
 <span className="w-2 h-2 rounded-full bg-amber-500 animate-ping"></span>
 <span>FAZ 1 / 3: TABAN ÇİZGİSİ DİNLENİYOR</span>
 </span>
 )}
 {phase === 'STIMULUS' && (
 <span className="text-brand-800 bg-brand-50 border-brand-200 px-3 py-0.5 rounded-full flex items-center space-x-1.5">
 <span className="w-2 h-2 rounded-full bg-brand-600 animate-ping"></span>
 <span>FAZ 2 / 3: AKTİF UYARI EYLEMİ</span>
 </span>
 )}
 {phase === 'RECOVERY' && (
 <span className="text-signal-800 bg-signal-50 border-signal-200 px-3 py-0.5 rounded-full flex items-center space-x-1.5">
 <span className="w-2 h-2 rounded-full bg-signal-600 animate-ping"></span>
 <span>FAZ 3 / 3: GERİ TOPARLANMA FAZI</span>
 </span>
 )}
 {phase === 'ANALYSIS' && (
 <span className="text-brand-800 bg-brand-50 border-brand-200 px-3 py-0.5 rounded-full">
 VERİ ANALİZİ YAPILIYOR
 </span>
 )}
 </div>

 {/* Big Clean Countdown Timer */}
 <div className="text-5xl font-mono font-extrabold text-slate-900 tracking-tight flex items-center justify-center space-x-1">
 <span>{phase === 'ANALYSIS' ? '⏳' : countdownSec}</span>
 {phase !== 'ANALYSIS' && <span className="text-xl text-slate-500 font-normal">sn</span>}
 </div>

 {/* Action Directives with Clean Light-Themed Accent */}
 <div className="p-4 bg-white border border-slate-200 rounded-xl shadow-2xs space-y-1.5 max-w-lg mx-auto">
 {phase === 'BASELINE' && (
 <>
 <div className="text-sm font-bold text-slate-900 flex items-center justify-center space-x-1.5">
 <Hand className="w-4 h-4 text-amber-600 shrink-0" />
 <span>Pedala veya Direksiyona Dokunmayın</span>
 </div>
 <p className="text-xs text-slate-500 leading-relaxed">
 Aracın durağan rölanti sinyalleri ve arka plan CAN gürültüsü haritalanıyor.
 </p>
 </>
 )}

 {phase === 'STIMULUS' && (
 <>
 <div className="text-sm font-bold text-brand-700 flex items-center justify-center space-x-1.5">
 <Zap className="w-4 h-4 text-brand-600 fill-current shrink-0" />
 <span>{targetConfig.stimulusInstruction}</span>
 </div>
 <p className="text-xs text-slate-500 leading-relaxed">
 Bu eylemi sayaç tamamlanana kadar (6 saniye boyunca) sabit uygulayınız.
 </p>
 </>
 )}

 {phase === 'RECOVERY' && (
 <>
 <div className="text-sm font-bold text-signal-700 flex items-center justify-center space-x-1.5">
 <RotateCcw className="w-4 h-4 text-signal-600 shrink-0" />
 <span>Eylemi Tamamen Bırakın ve Rölantiye Dönün</span>
 </div>
 <p className="text-xs text-slate-500 leading-relaxed">
 Sinyalin başlangıç taban seviyesine dönüş davranışı doğrulanıyor.
 </p>
 </>
 )}

 {phase === 'ANALYSIS' && (
 <>
 <div className="text-sm font-bold text-brand-700">
 Deterministik Analiz Hesaplanıyor...
 </div>
 <p className="text-xs text-slate-500">
 Pearson korelasyonu, Spearman sıralaması ve sayaç filtreleri çalıştırılıyor.
 </p>
 </>
 )}
 </div>

 {/* Overall 18s Progress Bar */}
 <div className="space-y-1 pt-1 max-w-md mx-auto">
 <div className="flex justify-between text-xs font-mono text-slate-500">
 <span>Toplam İlerleme</span>
 <span className="font-semibold text-brand-600">%{getOverallProgressPercent()} (18sn)</span>
 </div>
 <div className="w-full h-2 bg-slate-200 rounded-full overflow-hidden border border-slate-200">
 <div 
 className="h-full bg-brand-600 rounded-full transition-all duration-300"
 style={{ width: `${getOverallProgressPercent()}%` }}
 />
 </div>
 </div>
 </div>

 {/* Status Footer */}
 <div className="flex items-center justify-between text-xs text-slate-500 pt-1 border-t border-slate-100">
 <div className="flex items-center space-x-1.5">
 <Gauge className="w-3.5 h-3.5 text-slate-500" />
 <span>Örnekleme: <strong>100 Hz</strong></span>
 </div>
 <div className="flex items-center space-x-1.5">
 <ShieldCheck className="w-3.5 h-3.5 text-signal-600" />
 <span>Sıfır Hız Güvenlik Kilidi: <strong>Aktif (0 km/h)</strong></span>
 </div>
 </div>
 </div>

 {/* Right Box: Live Captured CAN Frames Trace Monitor */}
 <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-4 shadow-card flex flex-col h-[480px]">
 <div className="flex items-center justify-between pb-3 border-b border-slate-100">
 <div className="flex items-center space-x-2 text-xs font-bold text-slate-900">
 <Terminal className="w-4 h-4 text-slate-600" />
 <span>CAN CANLI YAKALAMA AKIŞI</span>
 </div>
 <span className="text-xs font-mono font-semibold text-brand-700 bg-brand-50 px-2 py-0.5 rounded border border-brand-200">
 {capturedFrames.length} Çerçeve
 </span>
 </div>

 {/* Dark Monospace Terminal Feed */}
 <div className="flex-1 bg-slate-950 rounded-lg p-3 my-3 overflow-y-auto font-mono text-xs text-slate-300 space-y-1 leading-relaxed select-text">
 {capturedFrames.length === 0 ? (
 <div className="text-slate-500 text-center py-20 space-y-1">
 <Activity className="w-5 h-5 mx-auto text-slate-600 animate-pulse" />
 <div>CAN çerçeveleri dinleniyor...</div>
 <div className="text-xs text-slate-600">Paketler anlık kaydediliyor</div>
 </div>
 ) : (
 capturedFrames.slice(-25).map((f, idx) => (
 <div key={idx} className="flex items-center justify-between hover:bg-slate-900 px-1 py-0.5 rounded">
 <span className="text-slate-500">{f.timestampSec.toFixed(2)}s</span>
 <span className="text-amber-400 font-bold">{f.canIdHex}</span>
 <span className="text-slate-300">{f.payloadHex}</span>
 <span className={`text-xs px-1.5 py-0.2 rounded font-semibold ${
 f.phase === 'STIMULUS' 
 ? 'bg-blue-950 text-brand-300 border border-brand-800' 
 : f.phase === 'RECOVERY'
 ? 'bg-emerald-950 text-signal-400 border border-signal-800'
 : 'bg-slate-900 text-slate-500'
 }`}>
 {f.phase === 'STIMULUS' ? 'UYARI' : f.phase === 'RECOVERY' ? 'TOPARLANMA' : 'TABAN'}
 </span>
 </div>
 ))
 )}
 </div>

 <div className="text-xs text-slate-500 font-mono flex items-center justify-between pt-1">
 <span>CAN-ID Filtresi: Açık</span>
 <span>Buffer: {capturedFrames.length} / 400</span>
 </div>
 </div>
 </div>
 </div>
 )}

 {/* Step 3: Evidence Inspector & Candidates */}
 {step === 3 && (
 <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
 {/* Candidates List */}
 <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-4">
 <div className="flex items-center justify-between border-b border-slate-100 pb-3">
 <div>
 <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider flex items-center space-x-1.5">
 <Sparkles className="w-4 h-4 text-brand-600" />
 <span>3. Keşfedilen Sinyal Adayları ({candidates.length} Aday Bulundu)</span>
 </h3>
 <p className="text-xs text-slate-500">18 saniyelik deney verilerinden deterministik olarak hesaplanmıştır</p>
 </div>

 <button
 onClick={handleReset}
 className="flex items-center space-x-1 px-2.5 py-1 text-slate-600 hover:text-slate-900 text-xs font-medium bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors"
 >
 <RotateCcw className="w-3 h-3" />
 <span>Yeniden Test</span>
 </button>
 </div>

 {candidates.length === 0 ? (
 <div className="p-8 text-center text-slate-500 space-y-2">
 <AlertTriangle className="w-8 h-8 text-amber-500 mx-auto" />
 <div className="text-xs font-bold text-slate-800">Yeterli Korelasyon Bulunamadı</div>
 <p className="text-xs">Deney sırasında uygulanan eylem ile CAN sinyalleri arasında eşleşme sağlanamadı. Lütfen testi tekrarlayınız.</p>
 </div>
 ) : (
 <div className="space-y-2.5 max-h-[500px] overflow-y-auto pr-1">
 {candidates.map((cand, idx) => {
 const isSelected = selectedCandidate?.id === cand.id;
 const scorePercent = cand.confidenceScore <= 1 ? Math.round(cand.confidenceScore * 100) : Math.round(cand.confidenceScore);
 const isHighConfidence = scorePercent >= 75;
 return (
 <div
 key={cand.id}
 onClick={() => handleSelectCandidate(cand)}
 className={`p-3.5 rounded-xl border transition-all cursor-pointer space-y-2 ${
 isSelected
 ? 'border-brand-600 bg-brand-50/40 shadow-xs ring-1 ring-brand-500/30'
 : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50'
 }`}
 >
 <div className="flex items-center justify-between">
 <div className="flex items-center space-x-2">
 <span className="w-6 h-6 rounded-lg bg-brand-50 text-brand-700 border border-brand-200 font-bold text-xs flex items-center justify-center">
 #{idx + 1}
 </span>
 <div>
 <span className="font-mono font-bold text-xs text-slate-900">{cand.canIdHex}</span>
 <span className="text-slate-500 text-xs mx-1.5">•</span>
 <span className="font-semibold text-xs text-slate-700">{cand.signalName}</span>
 </div>
 </div>

 <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${
 isHighConfidence
 ? 'bg-signal-50 text-signal-700 border-signal-200'
 : 'bg-amber-50 text-amber-700 border-amber-200'
 }`}>
 %{scorePercent} Güven
 </span>
 </div>

 {/* Metrics Bar */}
 <div className="grid grid-cols-4 gap-2 text-center text-xs font-mono bg-slate-50 p-2 rounded-lg border border-slate-200/80">
 <div>
 <div className="text-slate-500 text-xs">Pearson r</div>
 <div className="font-bold text-slate-800">{cand.pearsonR.toFixed(3)}</div>
 </div>
 <div>
 <div className="text-slate-500 text-xs">Gecikme (Lag)</div>
 <div className="font-bold text-slate-800">{cand.timeLagMs} ms</div>
 </div>
 <div>
 <div className="text-slate-500 text-xs">Bit Konumu</div>
 <div className="font-bold text-slate-800">{cand.startBit}|{cand.bitLength}</div>
 </div>
 <div>
 <div className="text-slate-500 text-xs">Sayaç/CRC</div>
 <div className="font-bold text-signal-600">{cand.isCounter ? 'Sayaç' : 'Temiz'}</div>
 </div>
 </div>
 </div>
 );
 })}
 </div>
 )}

 {candidates.length > 0 && (
 <div className="pt-2">
 <button
 onClick={() => setStep(4)}
 className="w-full flex items-center justify-center space-x-2 py-2 px-4 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-xs font-bold shadow-xs transition-colors"
 >
 <span>Seçili Adayı İncele ve DBC Üretimine Geç</span>
 <ArrowRight className="w-4 h-4" />
 </button>
 </div>
 )}
 </div>

 {/* Right Column: Selected Candidate Inspector */}
 <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-4">
 <div className="flex items-center space-x-2 border-b border-slate-100 pb-3">
 <FileCode className="w-4 h-4 text-slate-600" />
 <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">
 Sinyal Detayları & Kanıt Denetimi
 </h3>
 </div>

 {selectedCandidate ? (
 <div className="space-y-3.5 text-xs">
 <div className="p-3 bg-brand-50/40 rounded-xl border border-brand-100 space-y-1.5">
 <div className="flex justify-between">
 <span className="text-slate-500 font-medium">CAN Identifier:</span>
 <span className="font-mono font-bold text-brand-700">{selectedCandidate.canIdHex}</span>
 </div>
 <div className="flex justify-between">
 <span className="text-slate-500 font-medium">Bit Başlangıcı / Uzunluk:</span>
 <span className="font-mono font-bold text-slate-800">{selectedCandidate.startBit} / {selectedCandidate.bitLength}-bit ({selectedCandidate.endian})</span>
 </div>
 <div className="flex justify-between">
 <span className="text-slate-500 font-medium">Ölçek (Scale) & Ofset:</span>
 <span className="font-mono font-bold text-slate-800">{selectedCandidate.scale} / {selectedCandidate.offset}</span>
 </div>
 <div className="flex justify-between">
 <span className="text-slate-500 font-medium">Gözlenen Min / Maks:</span>
 <span className="font-mono font-bold text-slate-800">{selectedCandidate.minObserved} .. {selectedCandidate.maxObserved} {selectedCandidate.unit}</span>
 </div>
 </div>

 <div className="space-y-1">
 <span className="font-bold text-slate-700 text-xs uppercase tracking-wider">Kanıt Özeti:</span>
 <p className="text-xs text-slate-600 bg-slate-50 p-2.5 rounded-lg border border-slate-200 leading-relaxed">
 Sinyal, uygulanan <strong>{targetConfig.name}</strong> uyarısı ile <strong>r = {selectedCandidate.pearsonR.toFixed(3)}</strong> korelasyon katsayısına sahiptir. Monotonik artış filtresi ve CRC eleme testlerini başarıyla geçmiştir.
 </p>
 </div>
 </div>
 ) : (
 <div className="p-8 text-center text-slate-500 text-xs">
 Lütfen soldaki listeden incelemek istediğiniz adayı seçiniz.
 </div>
 )}
 </div>
 </div>
 )}

 {/* Step 4: Human Review & DBC Export */}
 {step === 4 && selectedCandidate && (
 <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
 <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-4">
 <div className="flex items-center justify-between border-b border-slate-100 pb-3">
 <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider flex items-center space-x-1.5">
 <FileCode className="w-4 h-4 text-brand-600" />
 <span>4. Sinyal Parametrelerini Düzenleyin ve Onaylayın</span>
 </h3>
 <button
 onClick={() => setStep(3)}
 className="text-xs text-slate-500 hover:text-slate-800 underline"
 >
 Aday Listesine Dön
 </button>
 </div>

 <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
 <div className="space-y-1">
 <label className="font-semibold text-slate-700">Sinyal Adı (Signal Name):</label>
 <input
 type="text"
 value={signalName}
 onChange={(e) => setSignalName(e.target.value)}
 className="w-full px-3 py-2 border border-slate-200 rounded-lg font-mono text-xs focus:ring-2 focus:ring-brand-500 focus:outline-none"
 />
 </div>

 <div className="space-y-1">
 <label className="font-semibold text-slate-700">Birim (Unit):</label>
 <input
 type="text"
 value={unit}
 onChange={(e) => setUnit(e.target.value)}
 className="w-full px-3 py-2 border border-slate-200 rounded-lg font-mono text-xs focus:ring-2 focus:ring-brand-500 focus:outline-none"
 />
 </div>

 <div className="space-y-1">
 <label className="font-semibold text-slate-700">Ölçek Çarpanı (Scale / Factor):</label>
 <input
 type="number"
 step="0.01"
 value={scale}
 onChange={(e) => setScale(parseFloat(e.target.value) || 1)}
 className="w-full px-3 py-2 border border-slate-200 rounded-lg font-mono text-xs focus:ring-2 focus:ring-brand-500 focus:outline-none"
 />
 </div>

 <div className="space-y-1">
 <label className="font-semibold text-slate-700">Ofset Değeri (Offset):</label>
 <input
 type="number"
 step="0.1"
 value={offset}
 onChange={(e) => setOffset(parseFloat(e.target.value) || 0)}
 className="w-full px-3 py-2 border border-slate-200 rounded-lg font-mono text-xs focus:ring-2 focus:ring-brand-500 focus:outline-none"
 />
 </div>
 </div>

 {/* Action Buttons */}
 <div className="flex items-center space-x-2 pt-2">
 <button
 onClick={handleExportDbc}
 className="flex-1 flex items-center justify-center space-x-2 py-2 px-4 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-xs font-bold shadow-xs transition-colors active:scale-[0.99]"
 >
 <Download className="w-4 h-4" />
 <span>Vector DBC Dosyası Olarak Kaydet (Farklı Kaydet)</span>
 </button>

 <button
 onClick={handleCopyDbc}
 className="flex items-center space-x-1.5 px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold border border-slate-200 transition-colors"
 >
 <Copy className="w-3.5 h-3.5" />
 <span>{copySuccess ? 'Kopyalandı!' : 'DBC Kodunu Kopyala'}</span>
 </button>
 </div>

 {savedSuccess && (
 <div className="p-3 bg-signal-50 border border-signal-200 rounded-lg text-signal-800 text-xs flex items-center space-x-2 font-medium">
 <CheckCircle2 className="w-4 h-4 text-signal-600 shrink-0" />
 <span>DBC dosyası başarıyla üretildi ve bilgisayarınıza kaydedildi!</span>
 </div>
 )}
 </div>

 {/* Right Column: DBC Syntax Preview */}
 <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-3">
 <div className="flex items-center justify-between border-b border-slate-100 pb-2">
 <div className="text-xs font-bold text-slate-900 uppercase tracking-wider">
 CAN DBC Sentaks Önizleme
 </div>
 <span className="text-xs font-mono text-slate-500">Standard DBC v1.0</span>
 </div>

 <pre className="bg-slate-950 text-signal-400 p-3.5 rounded-xl font-mono text-xs overflow-x-auto leading-relaxed border border-slate-800 select-text">
 {ReverseEngineeringEngine.generateDbcString({
 ...selectedCandidate,
 signalName: signalName || selectedCandidate.signalName,
 scale,
 offset,
 unit
 })}
 </pre>
 </div>
 </div>
 )}
 </div>
 );
};

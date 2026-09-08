import React, { useState, useEffect, useRef } from 'react';
import { 
 X, 
 Wand2, 
 Play, 
 ShieldCheck, 
 Activity, 
 CheckCircle2, 
 AlertTriangle, 
 RotateCcw, 
 Download, 
 Bot, 
 Sliders, 
 Radio, 
 Gauge, 
 Sparkles,
 Layers,
 ArrowRight,
 Info,
 Check,
 Ban
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

interface ReverseEngineeringModalProps {
 isOpen: boolean;
 onClose: () => void;
 onAskCopilot?: (prompt: string) => void;
 onStimulusChange?: (levelPercent: number) => void;
 latestFrame?: CANFrame | null;
}

export const ReverseEngineeringModal: React.FC<ReverseEngineeringModalProps> = ({
 isOpen,
 onClose,
 onAskCopilot,
 onStimulusChange,
 latestFrame
}) => {
 // Wizard Step: 1 = Setup, 2 = Live Experiment, 3 = Evidence Inspector, 4 = Human Review & DBC
 const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
 const [targetType, setTargetType] = useState<TargetSignalType>('accelerator');
 
 // Experiment State
 const [phase, setPhase] = useState<ExperimentPhase>('IDLE');
 const [countdownSec, setCountdownSec] = useState<number>(3);
 const [capturedFrames, setCapturedFrames] = useState<CapturedFrameRecord[]>([]);
 const [candidates, setCandidates] = useState<SignalCandidate[]>([]);
 const [selectedCandidate, setSelectedCandidate] = useState<SignalCandidate | null>(null);

 // Human Review & DBC Edit State
 const [signalName, setSignalName] = useState('');
 const [scale, setScale] = useState(0.4);
 const [offset, setOffset] = useState(0);
 const [unit, setUnit] = useState('%');
 const [savedSuccess, setSavedSuccess] = useState(false);

 const phaseTimerRef = useRef<NodeJS.Timeout | null>(null);
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

 setCapturedFrames(prev => [...prev.slice(-250), record]);
 }, [latestFrame, phase]);

 // Start Multi-Phase Experiment Workflow
 const handleStartExperiment = () => {
 setStep(2);
 setCapturedFrames([]);
 setCandidates([]);
 setSelectedCandidate(null);
 setSavedSuccess(false);

 // 1. Start BASELINE Phase (3 seconds)
 setPhase('BASELINE');
 setCountdownSec(3);
 if (onStimulusChange) onStimulusChange(0);

 let secLeft = 3;
 const baseInterval = setInterval(() => {
 secLeft--;
 setCountdownSec(secLeft);

 if (secLeft <= 0) {
 clearInterval(baseInterval);

 // 2. Start STIMULUS Phase (3 seconds)
 setPhase('STIMULUS');
 setCountdownSec(3);
 if (onStimulusChange) onStimulusChange(50);

 let stimSecLeft = 3;
 const stimInterval = setInterval(() => {
 stimSecLeft--;
 setCountdownSec(stimSecLeft);

 if (stimSecLeft <= 0) {
 clearInterval(stimInterval);

 // 3. Start RECOVERY Phase (3 seconds)
 setPhase('RECOVERY');
 setCountdownSec(3);
 if (onStimulusChange) onStimulusChange(0);

 let recSecLeft = 3;
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
 if (step === 3 && capturedFrames.length > 5) {
 const discovered = ReverseEngineeringEngine.analyzeCapturedFrames(capturedFrames, targetConfig);
 setCandidates(discovered);
 if (discovered.length > 0) {
 const best = discovered[0];
 setSelectedCandidate(best);
 setSignalName(best.signalName);
 setScale(best.scale);
 setOffset(best.offset);
 setUnit(best.unit);
 }
 }
 }, [step, capturedFrames, targetConfig]);

 if (!isOpen) return null;

 const handleSelectCandidate = (cand: SignalCandidate) => {
 setSelectedCandidate(cand);
 setSignalName(cand.signalName);
 setScale(cand.scale);
 setOffset(cand.offset);
 setUnit(cand.unit);
 };

 const handleAskCopilotAboutCandidate = (cand: SignalCandidate) => {
 if (!onAskCopilot) return;
 const prompt = `Lütfen şu Tersine Mühendislik Sinyal Keşif Kanıtlarını (Signal Candidate Evidence) analiz et:\n\n` +
 `• Hedef Sinyal: ${targetConfig.name} (${targetConfig.unit})\n` +
 `• Aday CAN ID: ${cand.canIdHex} (Bit ${cand.startBit}..${cand.startBit + cand.bitLength - 1}, ${cand.endian}, ${cand.isSigned ? 'Signed' : 'Unsigned'})\n` +
 `• Pearson Korelasyonu (r): ${cand.pearsonR}\n` +
 `• Regresyon R²: ${cand.regressionR2}\n` +
 `• Zaman Gecikmesi: ${cand.timeLagMs} ms\n` +
 `• Taban Çizgisine Dönüş (Recovery Delta): ${cand.recoveryDelta}\n` +
 `• Sayıcı (Counter) Filtresi: ${cand.isCounter ? 'SAYICI TESPİT EDİLDİ' : 'TEMİZ'}\n` +
 `• Checksum/CRC Filtresi: ${cand.isCrc ? 'CRC TESPİT EDİLDİ' : 'TEMİZ'}\n` +
 `• Deterministik Güven Skoru: %${(cand.confidenceScore * 100).toFixed(1)} (${cand.confidenceLevel})\n\n` +
 `Bu adayın araçtaki fiziksel ${targetConfig.name} sinyali olma olasılığını ve neden diğer bitlerin elendiğini açıkla.`;

 onAskCopilot(prompt);
 };

 const handleDownloadDbc = () => {
 if (!selectedCandidate) return;
 const updatedCandidate: SignalCandidate = {
 ...selectedCandidate,
 signalName: signalName.trim() || selectedCandidate.signalName,
 scale,
 offset,
 unit
 };
 const dbcContent = ReverseEngineeringEngine.generateDbcString(updatedCandidate);
 const blob = new Blob([dbcContent], { type: 'text/plain;charset=utf-8' });
 const url = URL.createObjectURL(blob);
 const link = document.createElement('a');
 link.href = url;
 link.download = `${updatedCandidate.signalName}_discovered.dbc`;
 document.body.appendChild(link);
 link.click();
 document.body.removeChild(link);
 URL.revokeObjectURL(url);
 setSavedSuccess(true);
 };

 return (
 <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-xs p-4 animate-in fade-in duration-200">
 <div className="bg-white border border-slate-200 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[92vh] flex flex-col overflow-hidden">
 {/* Header */}
 <div className="px-5 py-3.5 bg-gradient-to-r from-brand-900 via-slate-900 to-brand-900 text-white flex items-center justify-between">
 <div className="flex items-center space-x-3">
 <div className="w-8 h-8 rounded-lg bg-brand-500/20 border border-brand-400/30 flex items-center justify-center text-brand-300">
 <Wand2 className="w-4 h-4" />
 </div>
 <div>
 <h2 className="text-sm font-bold tracking-tight">AI CAN Sinyal Keşfi & Tersine Mühendislik Sihirbazı</h2>
 <p className="text-xs text-slate-300">Eylem-Tepki Korelasyonu ile Bilinmeyen CAN Sinyallerini İzole Etme</p>
 </div>
 </div>

 <button
 onClick={onClose}
 className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/10 transition-colors"
 >
 <X className="w-4 h-4" />
 </button>
 </div>

 {/* Stepper Navigation */}
 <div className="px-6 py-2.5 bg-slate-50 border-b border-slate-200 flex items-center justify-between text-xs">
 <div className="flex items-center space-x-6">
 <div className={`flex items-center space-x-1.5 ${step === 1 ? 'text-brand-600 font-bold' : step > 1 ? 'text-signal-600 font-semibold' : 'text-slate-500'}`}>
 <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs ${step === 1 ? 'bg-brand-600 text-white' : step > 1 ? 'bg-signal-600 text-white' : 'bg-slate-200 text-slate-600'}`}>1</span>
 <span>Hedef & Güvenlik</span>
 </div>
 <ArrowRight className="w-3.5 h-3.5 text-slate-300" />
 <div className={`flex items-center space-x-1.5 ${step === 2 ? 'text-brand-600 font-bold' : step > 2 ? 'text-signal-600 font-semibold' : 'text-slate-500'}`}>
 <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs ${step === 2 ? 'bg-brand-600 text-white' : step > 2 ? 'bg-signal-600 text-white' : 'bg-slate-200 text-slate-600'}`}>2</span>
 <span>Canlı Deney</span>
 </div>
 <ArrowRight className="w-3.5 h-3.5 text-slate-300" />
 <div className={`flex items-center space-x-1.5 ${step === 3 ? 'text-brand-600 font-bold' : step > 3 ? 'text-signal-600 font-semibold' : 'text-slate-500'}`}>
 <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs ${step === 3 ? 'bg-brand-600 text-white' : step > 3 ? 'bg-signal-600 text-white' : 'bg-slate-200 text-slate-600'}`}>3</span>
 <span>Adaylar & Kanıtlar</span>
 </div>
 <ArrowRight className="w-3.5 h-3.5 text-slate-300" />
 <div className={`flex items-center space-x-1.5 ${step === 4 ? 'text-brand-600 font-bold' : 'text-slate-500'}`}>
 <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs ${step === 4 ? 'bg-brand-600 text-white' : 'bg-slate-200 text-slate-600'}`}>4</span>
 <span>İnceleme & DBC</span>
 </div>
 </div>

 <span className="text-xs text-slate-500 font-mono">
 {capturedFrames.length} Paket Kaydedildi
 </span>
 </div>

 {/* Modal Body */}
 <div className="flex-1 p-6 overflow-y-auto">
 {/* STEP 1: TARGET SELECTION & SAFETY CHECK */}
 {step === 1 && (
 <div className="space-y-6">
 <div>
 <h3 className="text-sm font-bold text-slate-900 mb-1">1. Keşfetmek İstediğiniz Hedef Sinyali Seçin</h3>
 <p className="text-xs text-slate-500">Yapay zeka, seçtiğiniz sinyalin eylem-tepki karakteristiğine göre diferansiyel analiz yapacaktır.</p>
 </div>

 <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
 {(Object.keys(TARGET_SIGNAL_CONFIGS) as TargetSignalType[]).map(type => {
 const cfg = TARGET_SIGNAL_CONFIGS[type];
 const isSelected = targetType === type;
 return (
 <button
 key={type}
 onClick={() => setTargetType(type)}
 className={`p-3 rounded-xl border text-left transition-all ${
 isSelected 
 ? 'border-brand-600 bg-brand-50/50 ring-2 ring-brand-500/20 shadow-xs' 
 : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'
 }`}
 >
 <div className="font-bold text-xs text-slate-900 mb-0.5">{cfg.name}</div>
 <div className="text-xs text-slate-500 font-mono">Birim: {cfg.unit} | ({cfg.expectedMin}..{cfg.expectedMax})</div>
 </button>
 );
 })}
 </div>

 {/* Safety Checklist Box */}
 <div className="p-4 bg-amber-50/60 border border-amber-200 rounded-xl space-y-2 text-xs text-amber-900">
 <div className="flex items-center space-x-2 font-bold text-amber-800">
 <ShieldCheck className="w-4 h-4 text-amber-600" />
 <span>Güvenlik Ön Kontrolü (Safety Pre-Checks)</span>
 </div>
 <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-amber-800/90 pt-1">
 <div className="flex items-center space-x-1.5">
 <CheckCircle2 className="w-3.5 h-3.5 text-amber-600" />
 <span>Araç tamamen durağan (0 km/h)</span>
 </div>
 <div className="flex items-center space-x-1.5">
 <CheckCircle2 className="w-3.5 h-3.5 text-amber-600" />
 <span>El freni çekili / Park (P) modunda</span>
 </div>
 <div className="flex items-center space-x-1.5">
 <CheckCircle2 className="w-3.5 h-3.5 text-amber-600" />
 <span>CAN bus bağlantısı aktif</span>
 </div>
 <div className="flex items-center space-x-1.5">
 <CheckCircle2 className="w-3.5 h-3.5 text-amber-600" />
 <span>Motor rölantide çalışıyor</span>
 </div>
 </div>
 </div>

 <div className="flex justify-end pt-2">
 <button
 onClick={handleStartExperiment}
 className="flex items-center space-x-2 px-5 py-2.5 bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-bold text-xs shadow-md transition-all active:scale-[0.99]"
 >
 <Play className="w-4 h-4 fill-current" />
 <span>3 Aşamalı Deneyi Başlat</span>
 </button>
 </div>
 </div>
 )}

 {/* STEP 2: LIVE EXPERIMENT ORCHESTRATOR */}
 {step === 2 && (
 <div className="py-6 flex flex-col items-center justify-center space-y-6 text-center">
 {/* Dynamic Phase Countdown Card */}
 <div className="w-full max-w-lg p-6 bg-slate-900 text-white rounded-2xl shadow-xl flex flex-col items-center space-y-4 relative overflow-hidden">
 {/* Background pulse glow */}
 <div className={`absolute inset-0 opacity-10 transition-colors duration-500 ${
 phase === 'BASELINE' ? 'bg-signal-500' : phase === 'STIMULUS' ? 'bg-amber-500' : 'bg-brand-500'
 }`} />

 <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full text-xs font-mono font-bold uppercase tracking-wider bg-white/10">
 <Activity className="w-3.5 h-3.5 animate-pulse text-brand-400" />
 <span>Aşama: {phase}</span>
 </div>

 <div className="text-4xl font-black font-mono tracking-tight text-white flex items-center justify-center">
 <span className="w-14 h-14 rounded-full border-2 border-brand-400 flex items-center justify-center text-2xl font-bold">
 {countdownSec}
 </span>
 </div>

 <div className="space-y-1">
 <div className="text-sm font-bold text-brand-200">
 {phase === 'BASELINE' && '1. Aşama: Taban Çizgisi Kaydı'}
 {phase === 'STIMULUS' && '2. Aşama: Eylem Uygulama (Stimulus)'}
 {phase === 'RECOVERY' && '3. Aşama: Geri Dönüş ve Doğrulama'}
 {phase === 'ANALYSIS' && '4. Aşama: Deterministik Analiz Yapılıyor...'}
 </div>
 <div className="text-xs text-slate-300 max-w-md">
 {phase === 'BASELINE' && 'Pedallara ve kontrollere hiç dokunmayın. Rölanti arka plan CAN trafiği öğreniliyor...'}
 {phase === 'STIMULUS' && targetConfig.stimulusInstruction}
 {phase === 'RECOVERY' && targetConfig.recoveryInstruction}
 {phase === 'ANALYSIS' && 'Tüm bit hipotezleri, Pearson korelasyonu ve Counter filtreleri çalıştırılıyor...'}
 </div>
 </div>

 <div className="w-full bg-white/10 rounded-full h-1.5 overflow-hidden">
 <div 
 className="bg-brand-400 h-full transition-all duration-300"
 style={{ width: `${((3 - countdownSec) / 3) * 100}%` }}
 />
 </div>
 </div>

 <div className="text-xs text-slate-500 font-mono">
 CAN Hattından {capturedFrames.length} paket gerçek zamanlı yakalandı.
 </div>
 </div>
 )}

 {/* STEP 3: CANDIDATES & EVIDENCE INSPECTOR */}
 {step === 3 && (
 <div className="space-y-6">
 <div className="flex items-center justify-between">
 <div>
 <h3 className="text-sm font-bold text-slate-900">Keşfedilen Sinyal Adayları & Kanıt Tablosu</h3>
 <p className="text-xs text-slate-500">Deterministik istatistiksel kanıtlara göre sıralanmış bit hipotezleri.</p>
 </div>

 <button
 onClick={handleStartExperiment}
 className="flex items-center space-x-1 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold transition-colors"
 >
 <RotateCcw className="w-3.5 h-3.5" />
 <span>Deneyi Tekrarla</span>
 </button>
 </div>

 {/* Candidates Table */}
 <div className="border border-slate-200 rounded-xl overflow-hidden shadow-xs">
 <table className="w-full text-left text-xs border-collapse font-sans">
 <thead>
 <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold text-xs">
 <th className="py-2.5 px-3">CAN ID</th>
 <th className="py-2.5 px-3">Bit Aralığı</th>
 <th className="py-2.5 px-3">Endian / Tip</th>
 <th className="py-2.5 px-3">Pearson (r)</th>
 <th className="py-2.5 px-3">Regresyon (R²)</th>
 <th className="py-2.5 px-3">Filtre Durumu</th>
 <th className="py-2.5 px-3">Güven Skoru</th>
 <th className="py-2.5 px-3 text-right">İşlem</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-slate-100 font-mono">
 {candidates.map((cand) => {
 const isSelected = selectedCandidate?.id === cand.id;
 const isHigh = cand.confidenceLevel === 'HIGH';
 const isRejected = cand.confidenceLevel === 'REJECTED';

 return (
 <tr
 key={cand.id}
 onClick={() => handleSelectCandidate(cand)}
 className={`cursor-pointer transition-colors ${
 isSelected 
 ? 'bg-brand-50/70 font-semibold' 
 : isRejected 
 ? 'bg-slate-50/40 opacity-60 hover:opacity-100' 
 : 'hover:bg-slate-50'
 }`}
 >
 <td className="py-2.5 px-3 font-bold text-slate-900">{cand.canIdHex}</td>
 <td className="py-2.5 px-3 text-slate-700">Bit {cand.startBit}..{cand.startBit + cand.bitLength - 1} ({cand.bitLength}b)</td>
 <td className="py-2.5 px-3 text-slate-600">{cand.endian} / {cand.isSigned ? 'Signed' : 'Unsigned'}</td>
 <td className={`py-2.5 px-3 ${cand.pearsonR > 0.8 ? 'text-signal-600 font-bold' : 'text-slate-600'}`}>{cand.pearsonR}</td>
 <td className="py-2.5 px-3 text-slate-700">{cand.regressionR2}</td>
 <td className="py-2.5 px-3 font-sans">
 {cand.isCounter ? (
 <span className="inline-flex items-center space-x-1 text-xs text-amber-700 font-semibold bg-amber-50 px-1.5 py-0.5 rounded border border-amber-200">
 <Ban className="w-2.5 h-2.5" />
 <span>Sayaç (Counter)</span>
 </span>
 ) : cand.isCrc ? (
 <span className="inline-flex items-center space-x-1 text-xs text-amber-700 font-semibold bg-amber-50 px-1.5 py-0.5 rounded border border-amber-200">
 <Ban className="w-2.5 h-2.5" />
 <span>Checksum / CRC</span>
 </span>
 ) : (
 <span className="inline-flex items-center space-x-1 text-xs text-signal-700 font-semibold bg-signal-50 px-1.5 py-0.5 rounded border border-signal-200">
 <Check className="w-2.5 h-2.5" />
 <span>Temiz Sinyal</span>
 </span>
 )}
 </td>
 <td className="py-2.5 px-3">
 <span className={`px-2 py-0.5 rounded text-xs font-bold ${
 isHigh 
 ? 'bg-signal-100 text-signal-800' 
 : isRejected 
 ? 'bg-rose-100 text-rose-800' 
 : 'bg-slate-200 text-slate-700'
 }`}>
 %{(cand.confidenceScore * 100).toFixed(1)}
 </span>
 </td>
 <td className="py-2.5 px-3 text-right">
 <button
 onClick={(e) => {
 e.stopPropagation();
 handleSelectCandidate(cand);
 setStep(4);
 }}
 className="px-2.5 py-1 bg-brand-600 hover:bg-brand-700 text-white rounded text-xs font-sans font-bold shadow-2xs transition-colors"
 >
 İncele & DBC
 </button>
 </td>
 </tr>
 );
 })}
 </tbody>
 </table>
 </div>

 {/* Selected Candidate Evidence Inspector Box */}
 {selectedCandidate && (
 <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-3">
 <div className="flex items-center justify-between">
 <div className="flex items-center space-x-2">
 <Sparkles className="w-4 h-4 text-brand-600" />
 <span className="font-bold text-xs text-slate-900">
 Seçilen Aday Kanıt Raporu: {selectedCandidate.canIdHex} (Bit {selectedCandidate.startBit}..{selectedCandidate.startBit + selectedCandidate.bitLength - 1})
 </span>
 </div>

 <button
 onClick={() => handleAskCopilotAboutCandidate(selectedCandidate)}
 className="flex items-center space-x-1.5 px-3 py-1 bg-brand-50 hover:bg-brand-100 border border-brand-200 text-brand-700 rounded-lg text-xs font-bold transition-colors shadow-2xs"
 >
 <Bot className="w-3.5 h-3.5" />
 <span>AI Copilot ile Kanıtı Yorumla</span>
 </button>
 </div>

 <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono">
 <div className="p-2 bg-white rounded border border-slate-200">
 <span className="text-slate-500 block text-xs">Doğrusal Katsayı (Scale):</span>
 <span className="font-bold text-slate-800">{selectedCandidate.scale}</span>
 </div>
 <div className="p-2 bg-white rounded border border-slate-200">
 <span className="text-slate-500 block text-xs">Ofset:</span>
 <span className="font-bold text-slate-800">{selectedCandidate.offset}</span>
 </div>
 <div className="p-2 bg-white rounded border border-slate-200">
 <span className="text-slate-500 block text-xs">Ham Aralık (Raw Min..Max):</span>
 <span className="font-bold text-slate-800">{selectedCandidate.minObserved}..{selectedCandidate.maxObserved}</span>
 </div>
 <div className="p-2 bg-white rounded border border-slate-200">
 <span className="text-slate-500 block text-xs">Taban Dönüşü (Recovery Delta):</span>
 <span className="font-bold text-signal-600">{selectedCandidate.recoveryDelta} (Mükemmel)</span>
 </div>
 </div>
 </div>
 )}
 </div>
 )}

 {/* STEP 4: HUMAN REVIEW & DBC EXPORT */}
 {step === 4 && selectedCandidate && (
 <div className="space-y-6">
 <div>
 <h3 className="text-sm font-bold text-slate-900 mb-1">4. İnsan İncelemesi & Vector .DBC Dışa Aktarma</h3>
 <p className="text-xs text-slate-500">Yapay zekanın çıkardığı parametreleri doğrulayıp standart Vector DBC formatında kaydedin.</p>
 </div>

 {/* Editable Fields */}
 <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
 <div>
 <label className="block text-xs font-semibold text-slate-700 mb-1">Sinyal Adı</label>
 <input
 type="text"
 value={signalName}
 onChange={(e) => setSignalName(e.target.value)}
 className="w-full px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono font-bold focus:outline-hidden focus:ring-2 focus:ring-brand-500"
 />
 </div>

 <div>
 <label className="block text-xs font-semibold text-slate-700 mb-1">Çarpan (Scale)</label>
 <input
 type="number"
 step="0.01"
 value={scale}
 onChange={(e) => setScale(parseFloat(e.target.value) || 0.1)}
 className="w-full px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono focus:outline-hidden focus:ring-2 focus:ring-brand-500"
 />
 </div>

 <div>
 <label className="block text-xs font-semibold text-slate-700 mb-1">Ofset</label>
 <input
 type="number"
 value={offset}
 onChange={(e) => setOffset(parseFloat(e.target.value) || 0)}
 className="w-full px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono focus:outline-hidden focus:ring-2 focus:ring-brand-500"
 />
 </div>

 <div>
 <label className="block text-xs font-semibold text-slate-700 mb-1">Birim (Unit)</label>
 <input
 type="text"
 value={unit}
 onChange={(e) => setUnit(e.target.value)}
 className="w-full px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono focus:outline-hidden focus:ring-2 focus:ring-brand-500"
 />
 </div>
 </div>

 {/* DBC Syntax Code Preview */}
 <div>
 <div className="flex items-center justify-between mb-1.5">
 <span className="text-xs font-bold text-slate-700">Üretilen Vector .DBC Sözdizimi</span>
 <span className="text-xs text-slate-500 font-mono">Standart ISO/AUTOSAR DBC Formatı</span>
 </div>
 <pre className="p-3 bg-slate-900 text-signal-400 rounded-xl font-mono text-xs overflow-x-auto leading-relaxed border border-slate-800 shadow-inner">
 {ReverseEngineeringEngine.generateDbcString({
 ...selectedCandidate,
 signalName: signalName.trim() || selectedCandidate.signalName,
 scale,
 offset,
 unit
 })}
 </pre>
 </div>

 {savedSuccess && (
 <div className="p-3 bg-signal-50 border border-signal-200 rounded-lg text-xs text-signal-800 flex items-center space-x-2">
 <CheckCircle2 className="w-4 h-4 text-signal-600 shrink-0" />
 <span>DBC dosyası başarıyla oluşturuldu ve bilgisayarınıza indirildi!</span>
 </div>
 )}

 {/* Actions */}
 <div className="flex items-center justify-between pt-2 border-t border-slate-100">
 <button
 onClick={() => setStep(3)}
 className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl text-xs font-bold transition-colors"
 >
 Aday Listesine Geri Dön
 </button>

 <button
 onClick={handleDownloadDbc}
 className="flex items-center space-x-2 px-5 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-xl text-xs font-bold shadow-md transition-all active:scale-[0.99]"
 >
 <Download className="w-4 h-4" />
 <span>.DBC Olarak İndir & Doğrula</span>
 </button>
 </div>
 </div>
 )}
 </div>
 </div>
 </div>
 );
};

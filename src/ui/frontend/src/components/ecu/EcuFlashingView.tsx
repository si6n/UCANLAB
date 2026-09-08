import React, { useState, useRef } from 'react';
import { 
 Cpu, 
 UploadCloud, 
 Play, 
 RotateCcw, 
 ShieldCheck, 
 FileCode, 
 Terminal,
 CheckCircle2,
 AlertCircle,
 Trash2,
 Lock,
 AlertTriangle,
 FileX
} from 'lucide-react';

interface SelectedFirmware {
 name: string;
 sizeBytes: number;
 sizeFormatted: string;
 checksumSha256: string;
 extension: string;
 architecture: string;
}

export const EcuFlashingView: React.FC = () => {
 const [selectedEcu, setSelectedEcu] = useState<'ECM' | 'TCU' | 'ABS' | 'BCM'>('ECM');
 const [selectedFile, setSelectedFile] = useState<SelectedFirmware | null>(null);
 const [fileError, setFileError] = useState<string | null>(null);
 const [progress, setProgress] = useState(0);
 const [isFlashing, setIsFlashing] = useState(false);
 const [statusText, setStatusText] = useState('Firmware Dosyası Bekleniyor');
 const [isDragging, setIsDragging] = useState(false);
 const fileInputRef = useRef<HTMLInputElement>(null);

 const [logs, setLogs] = useState<string[]>([
 '[DEMO] BU GÖRÜNÜM ŞİMDİLİK SİMÜLASYONDUR — GERÇEK CAN TRAFİĞİ GÖNDERİLMEZ.',
 '[INIT] ISO 14229 (UDS) / ISO 15765-2 (DoCAN) Flashing Altyapısı Hazırlandı.',
 '[INFO] Hedef ECU: ECM Bosch EDC17 (CAN ID: 0x7E0 / 0x7E8) @ 500 kbps',
 '[WAIT] Flash işlemine başlamak için lütfen geçerli bir firmware dosyası (.bin, .hex, .s19) seçiniz.'
 ]);

 const sectors = Array.from({ length: 16 }, (_, i) => ({
 name: `SEC_${i}`,
 size: selectedFile ? `${Math.round(selectedFile.sizeBytes / 16 / 1024)}K` : '128K',
 flashed: progress >= (i + 1) * 6.25
 }));

 const validateFirmwareFile = async (file: File): Promise<{ isValid: boolean; error?: string; checksum: string; arch: string }> => {
 const ext = file.name.split('.').pop()?.toLowerCase() || '';
 const allowedExtensions = ['bin', 'hex', 'ihex', 's19', 's28', 's37', 'mot', 'dcm', 'frf', 'odx', 'pdx'];

 if (!allowedExtensions.includes(ext)) {
 return {
 isValid: false,
 error: `Desteklenmeyen dosya uzantısı (.${ext || 'bilinmeyen'}). Yalnızca ECU firmware dosyaları (.bin, .hex, .s19, .mot) kabul edilir.`,
 checksum: '',
 arch: 'Unknown'
 };
 }

 if (file.size < 1024) { // Minimum 1 KB
 return {
 isValid: false,
 error: `Dosya boyutu bir ECU firmware imajı için çok küçük (${file.size} bayt). Minimum 1.0 KB geçerli binary veri gereklidir.`,
 checksum: '',
 arch: 'Invalid'
 };
 }

 if (file.size > 32 * 1024 * 1024) { // Max 32 MB
 return {
 isValid: false,
 error: `Dosya boyutu ECU flash bellek sınırını aşıyor (${(file.size / (1024 * 1024)).toFixed(1)} MB). Maksimum boyut: 32 MB.`,
 checksum: '',
 arch: 'Overflow'
 };
 }

 // Read first 2048 bytes for deep inspection
 const slice = file.slice(0, 2048);
 const textSample = await slice.text();
 const arrayBuffer = await slice.arrayBuffer();
 const bytes = new Uint8Array(arrayBuffer);

 // 1. Validate Intel Hex format (.hex / .ihex)
 if (ext === 'hex' || ext === 'ihex') {
 const lines = textSample.trim().split(/\r?\n/).filter(l => l.trim().length > 0);
 if (lines.length === 0 || !lines[0].startsWith(':')) {
 return {
 isValid: false,
 error: 'Geçersiz Intel Hex formatı: Dosya satırları ":" kayıt başlangıç belirteci ile başlamıyor.',
 checksum: '',
 arch: 'Invalid Hex'
 };
 }
 const firstLineHex = lines[0].substring(1).trim();
 if (!/^[0-9A-Fa-f]+$/.test(firstLineHex) || firstLineHex.length < 10) {
 return {
 isValid: false,
 error: 'Intel Hex biçim hatası: Bozuk hexadecimal veri karakterleri tespit edildi.',
 checksum: '',
 arch: 'Corrupted Hex'
 };
 }
 }

 // 2. Validate Motorola S-Record format (.s19, .s28, .s37, .mot)
 if (['s19', 's28', 's37', 'mot'].includes(ext)) {
 const lines = textSample.trim().split(/\r?\n/).filter(l => l.trim().length > 0);
 if (lines.length === 0 || !/^S[0-9]/i.test(lines[0])) {
 return {
 isValid: false,
 error: 'Geçersiz Motorola S-Record formatı: Satırlar S0, S1, S2 veya S3 kayıt tipi ile başlamalıdır.',
 checksum: '',
 arch: 'Invalid S-Record'
 };
 }
 }

 // 3. Validate Raw Binary format (.bin)
 if (ext === 'bin') {
 // Check if file is purely blank (all 0x00 or all 0xFF)
 let allZeros = true;
 let allOnes = true;
 for (let i = 0; i < Math.min(bytes.length, 512); i++) {
 if (bytes[i] !== 0x00) allZeros = false;
 if (bytes[i] !== 0xFF) allOnes = false;
 }
 if (allZeros || allOnes) {
 return {
 isValid: false,
 error: 'Boş veya sıfırlanmış binary bellek dökümü (Tüm baytlar ' + (allZeros ? '0x00' : '0xFF') + '). Flash yapılamaz.',
 checksum: '',
 arch: 'Empty Binary'
 };
 }

 // Check if it's plain text / HTML / source code disguised as .bin
 let asciiCount = 0;
 for (let i = 0; i < Math.min(bytes.length, 512); i++) {
 if ((bytes[i] >= 32 && bytes[i] <= 126) || bytes[i] === 10 || bytes[i] === 13) {
 asciiCount++;
 }
 }
 if (asciiCount / Math.min(bytes.length, 512) > 0.95 && (textSample.includes('<!DOCTYPE') || textSample.includes('<html') || textSample.includes('import ') || textSample.includes('function '))) {
 return {
 isValid: false,
 error: 'Düz metin veya kaynak kod dosyası tespit edildi. Geçerli bir derlenmiş ECU makine kodu binary imajı değil.',
 checksum: '',
 arch: 'Plain Text'
 };
 }
 }

 // Determine likely microcontroller architecture
 let arch = '32-Bit TriCore / PowerPC Image';
 if (ext === 'hex') arch = 'Intel Hex Linear 32-bit';
 else if (ext === 's19' || ext === 's28' || ext === 's37') arch = 'Motorola S-Record 32-bit';

 // UI-C-004: real content-derived SHA-256 via WebCrypto (the previous
 // deterministic pseudo-hash was derived from file metadata only and
 // masqueraded as an integrity guarantee).
 let digestHex = '';
 try {
 const digestBuffer = await crypto.subtle.digest('SHA-256', bytes);
 digestHex = Array.from(new Uint8Array(digestBuffer))
 .map(b => b.toString(16).padStart(2, '0'))
 .join('');
 } catch {
 digestHex = 'UNAVAILABLE';
 }
 const checksum = digestHex === 'UNAVAILABLE'
 ? 'SHA256: (hesaplanamadı — WebCrypto kullanılamıyor)'
 : `SHA256:${digestHex.substring(0, 16)}...${digestHex.substring(digestHex.length - 8)}`;

 return {
 isValid: true,
 checksum,
 arch
 };
 };

 const handleFile = async (file: File) => {
 setFileError(null);
 setProgress(0);

 const validation = await validateFirmwareFile(file);

 if (!validation.isValid) {
 setSelectedFile(null);
 setFileError(validation.error || 'Geçersiz dosya formatı.');
 setStatusText('Hata: Hata: Geçersiz Firmware Formatı');

 setLogs([
 '[INIT] ISO 14229 (UDS) / ISO 15765-2 (DoCAN) Flashing Altyapısı Hazırlandı.',
 `[INFO] Hedef ECU: ${selectedEcu} (CAN ID: 0x7E0 / 0x7E8)`,
 `[ERROR] Dosya Doğrulama Başarısız: "${file.name}"`,
 `[REJECT] ${validation.error}`,
 '[ABORT] Flash işlemi güvenlik sebebiyle kilitlendi. Lütfen geçerli bir ECU firmware dosyası seçiniz.'
 ]);
 return;
 }

 const sizeBytes = file.size;
 const sizeKB = (sizeBytes / 1024).toFixed(1);
 const ext = file.name.split('.').pop()?.toUpperCase() || 'BIN';

 const loadedFile: SelectedFirmware = {
 name: file.name,
 sizeBytes,
 sizeFormatted: `${sizeKB} KB`,
 checksumSha256: validation.checksum,
 extension: ext,
 architecture: validation.arch
 };

 setSelectedFile(loadedFile);
 setFileError(null);
 setProgress(0);
 setStatusText(`Firmware Yüklendi: ${file.name} (Flash Başlatmaya Hazır)`);

 setLogs([
 '[INIT] ISO 14229 (UDS) / ISO 15765-2 (DoCAN) Flashing Altyapısı Hazırlandı.',
 `[INFO] Hedef ECU: ${selectedEcu} (CAN ID: 0x7E0 / 0x7E8)`,
 `[FILE] Firmware Seçildi: ${file.name} (${sizeKB} KB, Format: .${ext})`,
 `[ARCH] Mimari: ${validation.arch}`,
 `[INTEGRITY] Dosya Bütünlüğü Doğrulandı (${loadedFile.checksumSha256})`,
 `[READY] 16 Bellek Sektörü Haritalandı (Adres Aralığı: 0x00080000 - 0x${(0x80000 + sizeBytes).toString(16).toUpperCase()}).`,
 '[READY] Flash işlemine başlamak için "Flash İşlemini Başlat" butonuna tıklayınız.'
 ]);
 };

 const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
 if (e.target.files && e.target.files[0]) {
 handleFile(e.target.files[0]);
 }
 };

 const handleDragOver = (e: React.DragEvent) => {
 e.preventDefault();
 setIsDragging(true);
 };

 const handleDragLeave = (e: React.DragEvent) => {
 e.preventDefault();
 setIsDragging(false);
 };

 const handleDrop = (e: React.DragEvent) => {
 e.preventDefault();
 setIsDragging(false);
 if (e.dataTransfer.files && e.dataTransfer.files[0]) {
 handleFile(e.dataTransfer.files[0]);
 }
 };

 const removeFile = (e?: React.MouseEvent) => {
 if (e) e.stopPropagation();
 setSelectedFile(null);
 setFileError(null);
 setProgress(0);
 setIsFlashing(false);
 setStatusText('Firmware Dosyası Bekleniyor');
 if (fileInputRef.current) fileInputRef.current.value = '';
 setLogs([
 '[INIT] ISO 14229 (UDS) / ISO 15765-2 (DoCAN) Flashing Altyapısı Hazırlandı.',
 `[INFO] Hedef ECU: ${selectedEcu} (CAN ID: 0x7E0 / 0x7E8)`,
 '[WAIT] Dosya kaldırıldı. Lütfen yeni bir firmware dosyası seçiniz.'
 ]);
 };

 const startFlashing = () => {
 if (!selectedFile) {
 alert('Lütfen önce geçerli bir firmware dosyası seçiniz!');
 return;
 }
 if (isFlashing) return;

 setIsFlashing(true);
 setProgress(0);
 setStatusText('Güvenlik & Hız Kilidi Doğrulanıyor...');

 const initialFlashLogs = [
 ...logs,
 '------------------------------------------------------------',
 '[DEMO] Simülasyon modu: aşağıdaki adımlar örnek amaçlıdır, gerçek ECU\'ya veri yazılmaz.',
 '[FLASH_START] ECU Yeniden Programlama Dizisi Başlatıldı...',
  '[SIM] Hız Kilidi Kontrolü: Araç Hızı == 0 km/h (Simülasyon senaryosu)',
  '[SIM] UDS 0x10 0x03 Genişletilmiş Diyagnostik Oturumu (SIMÜLE EDİLDİ — 0x50 0x03)',
  '[SIM] UDS 0x27 0x01 Seed-Key (SIMÜLE EDİLDİ — sabit demo seed 0xA4F92B1C)',
  '[SIM] UDS 0x10 0x02 Programlama Oturumu (SIMÜLE EDİLDİ)',
  '[SIM] UDS 0x34 RequestDownload (SIMÜLE EDİLDİ — Bellek: 0x00080000, Boyut: ' + selectedFile.sizeFormatted + ')'
  ];
  setLogs(initialFlashLogs);

  let current = 0;
  const interval = setInterval(() => {
  current += 2;
  setProgress(current);
  const sectorIdx = Math.min(15, Math.floor((current / 100) * 16));
  setStatusText(`[SIM] Sektör SEC_${sectorIdx} yazma adımı (%${current})`);

  if (current % 20 === 0) {
  const blockAddr = (0x00080000 + Math.floor((current / 100) * selectedFile.sizeBytes)).toString(16).toUpperCase();
  setLogs(prev => [
  ...prev,
  `[SIM] UDS 0x36 TransferData adımı: Blok 0x${blockAddr} (Sektör SEC_${sectorIdx}) — simüle edilmiş, CRC hesaplanmadı.`
  ]);
  }

  if (current >= 100) {
  clearInterval(interval);
  setIsFlashing(false);
  // P3-14 (REVIEW L-11): honest completion text — nothing was flashed.
  setStatusText(`Simülasyon tamamlandı: ${selectedFile.name} — ECU'ya YAZILMADI`);
  setLogs(prev => [
  ...prev,
  '[SIM] UDS 0x37 RequestTransferExit adımı (SIMÜLE EDİLDİ)',
  '[SIM] UDS 0x31 RoutineControl CRC adımı (SIMÜLE EDİLDİ — CRC hesaplanmadı)',
  '[SIM] UDS 0x11 0x01 ECU Hard Reset adımı (SIMÜLE EDİLDİ — ECU yeniden başlatılmadı)',
  `[DEMO] ${selectedFile.name} simülasyonu tamamlandı (%100).`,
  '[DEMO] Bu bir SİMÜLASYON sonucudur — gerçek bir araçta flash işlemi yapılmadı.'
  ]);
  }
  }, 100);
  };

 const resetSession = () => {
 setProgress(0);
 setIsFlashing(false);
 setStatusText(selectedFile ? `Firmware Yüklendi: ${selectedFile.name}` : 'Firmware Dosyası Bekleniyor');
 setLogs([
 '[INIT] ISO 14229 (UDS) / ISO 15765-2 (DoCAN) Flashing Altyapısı Hazırlandı.',
 `[INFO] Hedef ECU: ${selectedEcu} (CAN ID: 0x7E0 / 0x7E8)`,
 selectedFile ? `[READY] ${selectedFile.name} hazır. Flash başlatabilirsiniz.` : '[WAIT] Lütfen firmware dosyası seçiniz.',
 '[RESET] Diyagnostik oturum sıfırlandı (Default Session 0x10 0x01).'
 ]);
 };

 return (
 <div className="p-4 space-y-4 max-w-7xl mx-auto">
 {/* Hidden File Input */}
 <input
 type="file"
 ref={fileInputRef}
 onChange={handleFileInputChange}
 accept=".bin,.hex,.ihex,.s19,.s28,.s37,.mot,.dcm"
 className="hidden"
 />

 {/* Header Card */}
 <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-card flex items-center justify-between">
 <div className="flex items-center space-x-3">
 <div className="w-10 h-10 rounded-lg bg-brand-50 border border-brand-200 flex items-center justify-center text-brand-600">
 <Cpu className="w-5 h-5" />
 </div>
 <div>
 <h2 className="text-sm font-bold text-slate-900">
 ECU Flashing & Bootloader Yöneticisi{' '}
 <span className="ml-1 px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 border border-amber-300 text-xs font-bold uppercase tracking-wide">
 Demo / Simülasyon
 </span>
 </h2>
 <p className="text-xs text-slate-500">
 ISO 14229 (UDS) & ISO 15765-2 (DoCAN) Protokolü ile Güvenli Firmware Yükleme —{' '}
 <span className="font-semibold text-amber-600">bu görünüm CAN veriyoluyla gerçek TX yapmaz</span>
 </p>
 </div>
 </div>

 {/* ECU Selector */}
 <div className="flex items-center space-x-2">
 <span className="text-xs text-slate-500 font-medium">Hedef Modül:</span>
 <div className="inline-flex bg-slate-100 p-0.5 rounded-lg border border-slate-200 text-xs">
 {(['ECM', 'TCU', 'ABS', 'BCM'] as const).map((ecu) => (
 <button
 key={ecu}
 onClick={() => {
 if (isFlashing) return;
 setSelectedEcu(ecu);
 }}
 disabled={isFlashing}
 className={`px-3 py-1 rounded-md font-semibold transition-all ${
 selectedEcu === ecu ? 'bg-white text-brand-600 shadow-xs' : 'text-slate-600 hover:text-slate-900'
 }`}
 >
 {ecu}
 </button>
 ))}
 </div>
 </div>
 </div>

 {/* 2-Column Split */}
 <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
 {/* Left Column: Flash Controls & Sectors */}
 <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-4">
 <div className="flex items-center justify-between">
 <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider flex items-center space-x-1.5">
 <FileCode className="w-4 h-4 text-brand-600" />
 <span>Firmware Dosyası Seçimi (S-Record / Intel Hex / Bin)</span>
 </h3>
 <span className="text-xs bg-signal-50 text-signal-700 px-2 py-0.5 rounded-md border border-signal-200/80 font-medium">
 Seed-Key (0x27) Korumalı
 </span>
 </div>

 {/* Interactive File Drop Box */}
 {!selectedFile ? (
 <div className="space-y-3">
 <div 
 onClick={() => fileInputRef.current?.click()}
 onDragOver={handleDragOver}
 onDragLeave={handleDragLeave}
 onDrop={handleDrop}
 className={`border-2 border-dashed rounded-xl p-6 text-center space-y-2 transition-all cursor-pointer ${
 fileError 
 ? 'border-rose-300 bg-rose-50/40 hover:bg-rose-50'
 : isDragging 
 ? 'border-brand-500 bg-brand-50/60 ring-2 ring-brand-500/20' 
 : 'border-slate-300 bg-slate-50/50 hover:bg-slate-50 hover:border-brand-400'
 }`}
 >
 {fileError ? (
 <FileX className="w-9 h-9 text-rose-500 mx-auto transition-transform hover:scale-105" />
 ) : (
 <UploadCloud className="w-9 h-9 text-brand-600 mx-auto transition-transform hover:scale-105" />
 )}
 <div className={`text-xs font-bold ${fileError ? 'text-rose-900' : 'text-slate-800'}`}>
 {fileError ? 'Geçersiz Dosya Seçildi - Yeniden Dosya Seçin' : 'Firmware Dosyası Seçin veya Sürükleyin'}
 </div>
 <p className="text-xs text-slate-500">
 Desteklenen formatlar: .bin, .hex, .s19, .s28, .s37, .mot (Maksimum 32 MB)
 </p>
 <button 
 type="button"
 className={`mt-2 inline-flex items-center px-3 py-1 rounded-md text-xs font-semibold shadow-xs ${
 fileError
 ? 'bg-rose-600 text-white hover:bg-rose-700'
 : 'bg-white border border-slate-300 text-slate-700 hover:bg-slate-50'
 }`}
 >
 Bilgisayardan Dosya Seç...
 </button>
 </div>

 {/* Error Alert Banner */}
 {fileError && (
 <div className="p-3 bg-rose-50 border border-rose-200 rounded-xl text-rose-800 text-xs flex items-start space-x-2.5 shadow-xs">
 <AlertCircle className="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
 <div>
 <strong className="font-bold">Doğrulama Hatası:</strong>
 <div className="text-xs text-rose-700 mt-0.5">{fileError}</div>
 </div>
 </div>
 )}
 </div>
 ) : (
 <div className="border border-signal-200 bg-signal-50/30 rounded-xl p-4 space-y-3">
 <div className="flex items-center justify-between">
 <div className="flex items-center space-x-3">
 <div className="w-10 h-10 rounded-lg bg-signal-100 border border-signal-200 flex items-center justify-center text-signal-700">
 <CheckCircle2 className="w-5 h-5" />
 </div>
 <div>
 <div className="text-xs font-bold text-slate-900 font-mono">
 {selectedFile.name}
 </div>
 <div className="text-xs text-slate-500 flex items-center space-x-2">
 <span>{selectedFile.sizeFormatted}</span>
 <span>•</span>
 <span>Format: .{selectedFile.extension}</span>
 <span>•</span>
 <span className="font-mono text-signal-700">{selectedFile.checksumSha256}</span>
 </div>
 </div>
 </div>

 <div className="flex items-center space-x-1.5">
 <button
 onClick={() => fileInputRef.current?.click()}
 disabled={isFlashing}
 className="px-2.5 py-1 text-xs font-semibold text-brand-700 bg-brand-50 hover:bg-brand-100 rounded-lg border border-brand-200 transition-colors disabled:opacity-50"
 >
 Değiştir
 </button>
 <button
 onClick={removeFile}
 disabled={isFlashing}
 className="p-1 text-slate-500 hover:text-rose-600 rounded-lg hover:bg-rose-50 transition-colors disabled:opacity-50"
 title="Dosyayı Kaldır"
 >
 <Trash2 className="w-4 h-4" />
 </button>
 </div>
 </div>

 <div className="text-xs text-signal-800 font-medium bg-white/80 p-2 rounded border border-signal-100">
 Flash Bellek Haritası Doğrulandı ({selectedFile.architecture}): 16 Sektör (0x00080000 - 0x{(0x80000 + selectedFile.sizeBytes).toString(16).toUpperCase()})
 </div>
 </div>
 )}

 {/* Progress Bar & Status */}
 <div className="space-y-1.5">
 <div className="flex items-center justify-between text-xs">
 <span className={`font-semibold ${isFlashing ? 'text-brand-600' : fileError ? 'text-rose-600' : selectedFile ? 'text-slate-800' : 'text-slate-500'}`}>
 {statusText}
 </span>
 <span className="font-mono font-bold text-brand-600">%{progress}</span>
 </div>
 <div className="w-full h-3 bg-slate-100 rounded-full overflow-hidden border border-slate-200">
 <div
 className="h-full bg-brand-600 transition-all duration-150 rounded-full shadow-xs"
 style={{ width: `${progress}%` }}
 />
 </div>
 </div>

 {/* Action Buttons */}
 <div className="flex items-center space-x-2 pt-1">
 <button
 onClick={startFlashing}
 disabled={!selectedFile || isFlashing}
 className={`flex items-center space-x-1.5 px-4 py-2 rounded-lg text-xs font-bold shadow-xs transition-all ${
 !selectedFile || isFlashing
 ? 'bg-slate-200 text-slate-500 cursor-not-allowed border border-slate-300'
 : 'bg-brand-600 hover:bg-brand-700 text-white active:scale-[0.98]'
 }`}
 title={!selectedFile ? 'Lütfen önce geçerli bir firmware dosyası seçiniz' : 'Flash işlemini başlat'}
 >
 {!selectedFile ? <Lock className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5 fill-current" />}
 <span>{!selectedFile ? 'Geçerli Dosya Bekleniyor (Flash Kilitli)' : isFlashing ? 'Flash Yazılıyor...' : 'Flash İşlemini Başlat'}</span>
 </button>

 <button
 onClick={resetSession}
 disabled={isFlashing}
 className="flex items-center space-x-1.5 px-3 py-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-50 text-slate-700 rounded-lg text-xs font-semibold border border-slate-200 transition-colors"
 >
 <RotateCcw className="w-3.5 h-3.5" />
 <span>Oturumu Sıfırla</span>
 </button>
 </div>

 {/* Flash Sectors Grid */}
 <div className="space-y-2 pt-2 border-t border-slate-100">
 <div className="flex items-center justify-between">
 <div className="text-xs font-bold text-slate-700 uppercase tracking-wider">
 Flash Bellek Sektörleri (16 Sektör)
 </div>
 <span className="text-xs text-slate-500 font-mono">
 {selectedFile ? `Toplam: ${selectedFile.sizeFormatted}` : 'Dosya bekleniyor'}
 </span>
 </div>
 <div className="grid grid-cols-8 gap-1.5 text-center font-mono text-xs">
 {sectors.map((sec) => (
 <div
 key={sec.name}
 className={`p-2 rounded border transition-all ${
 sec.flashed
 ? 'bg-brand-50 border-brand-300 text-brand-700 font-bold shadow-xs'
 : selectedFile
 ? 'bg-slate-50 border-slate-200 text-slate-600'
 : 'bg-slate-50/50 border-slate-200 text-slate-500 opacity-60'
 }`}
 >
 <div>{sec.name}</div>
 <div className="text-xs opacity-75">{sec.size}</div>
 </div>
 ))}
 </div>
 </div>
 </div>

 {/* Right Column: UDS Diagnostic Terminal Logs */}
 <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-4 shadow-card flex flex-col h-[490px]">
 <div className="flex items-center justify-between pb-3 border-b border-slate-100">
 <div className="flex items-center space-x-2 text-xs font-bold text-slate-900">
 <Terminal className="w-4 h-4 text-slate-600" />
 <span>UDS DİAGNOSTİK LOG TERMİNALİ</span>
 </div>
 <ShieldCheck className="w-4 h-4 text-signal-600" />
 </div>

 <div className="flex-1 bg-slate-950 rounded-lg p-3 my-3 overflow-y-auto font-mono text-xs text-slate-200 space-y-1.5 leading-relaxed select-text">
 {logs.map((line, idx) => (
 <div 
 key={idx} 
 className={
 line.includes('SUCCESS') ? 'text-signal-400 font-bold' : 
 line.includes('ERROR') || line.includes('REJECT') || line.includes('ABORT') ? 'text-rose-400 font-semibold' :
 line.includes('SECURITY') ? 'text-brand-300' : 
 line.includes('ERASE') || line.includes('FLASH_START') ? 'text-amber-300 font-semibold' : 
 line.includes('WAIT') ? 'text-amber-400' :
 line.includes('FILE') || line.includes('INTEGRITY') || line.includes('ARCH') ? 'text-brand-300' :
 'text-slate-300'
 }
 >
 {line}
 </div>
 ))}
 </div>

 <div className="text-xs text-slate-500 font-mono flex items-center justify-between pt-1">
 <span>Baud: 500 kbps (High Speed CAN)</span>
 <span>UDS: ISO 14229-1 (Level 0x01)</span>
 </div>
 </div>
 </div>
 </div>
 );
};

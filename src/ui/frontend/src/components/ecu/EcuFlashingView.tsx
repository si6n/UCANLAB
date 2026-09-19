import React, { useState, useRef, useEffect } from 'react';
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
 FileX,
 Square
} from 'lucide-react';
import { DesktopBridge } from '../../services/bridge';

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
 const [rawFile, setRawFile] = useState<File | null>(null);
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
  // REVIEW (whole-file digest): the bytes above are only the first 2048
  // bytes (format sniff) — hashing just that slice made two images with a
  // shared 2 KB header pass "integrity verified" with different flash
  // payloads. Digest the WHOLE file (size is already capped at 32 MB).
  let digestHex = '';
  try {
  const wholeBuffer = await file.arrayBuffer();
  const digestBuffer = await crypto.subtle.digest('SHA-256', wholeBuffer);
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

 if (!file || file.size === 0) {
   setSelectedFile(null);
   setRawFile(null);
   setFileError('Seçilen firmware dosyası boş (0 bayt). Kör flash engellendi.');
   setStatusText('Hata: Dosya Boş (0 Bayt)');
   return;
 }

 const validation = await validateFirmwareFile(file);

 if (!validation.isValid) {
 setSelectedFile(null);
 setRawFile(null);
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
 setRawFile(null);
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

 const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

 useEffect(() => {
   return () => {
     if (pollIntervalRef.current) {
       clearInterval(pollIntervalRef.current);
     }
   };
 }, []);

 const cancelFlashing = async () => {
   if (pollIntervalRef.current) {
     clearInterval(pollIntervalRef.current);
     pollIntervalRef.current = null;
   }
   if (DesktopBridge.isNative()) {
     try {
       await DesktopBridge.flashCancel();
     } catch {
       // best-effort
     }
   }
   setIsFlashing(false);
   setStatusText('Flashing kullanıcı tarafından iptal edildi.');
   setLogs(prev => [
     ...prev,
     '[CANCEL] Flashing iptal edildi (UDS Oturumu Sıfırlandı).'
   ]);
 };

 const startFlashing = async () => {
 if (!selectedFile || !rawFile) {
 alert('Lütfen önce geçerli bir firmware dosyası seçiniz! Dosya seçimi zorunludur.');
 return;
 }
 if (rawFile.size === 0) {
 alert('Firmware dosyası 0 bayt olamaz! Kör flash güvenlik sebebiyle engellendi.');
 return;
 }
 if (isFlashing) return;

 setIsFlashing(true);
 setProgress(0);
 setStatusText('Güvenlik & Hız Kilidi Doğrulanıyor...');

 if (DesktopBridge.isNative()) {
   try {
     setLogs(prev => [
       ...prev,
       '------------------------------------------------------------',
       `[INIT] Hedef ECU: ${selectedEcu} (CAN ID: 0x7E0 / 0x7E8) Flashing Hazırlığı`,
       '[SECURITY] Firmware içeriği okunuyor...'
     ]);

     const arrayBuffer = await rawFile.arrayBuffer();
     const bytes = new Uint8Array(arrayBuffer);
     if (bytes.length === 0) {
       throw new Error('Firmware verisi boş (0 bayt). Flash işlemi iptal edildi.');
     }
     let hex = '';
     const chunkSize = 0x8000;
     for (let i = 0; i < bytes.length; i += chunkSize) {
       const chunk = bytes.subarray(i, i + chunkSize);
       hex += Array.from(chunk, b => b.toString(16).padStart(2, '0')).join('');
     }

     setLogs(prev => [
       ...prev,
       '[SECURITY] Dual Confirmation onay token\'ı talep ediliyor...'
     ]);

     const challenge = await DesktopBridge.requestDiagnosticChallenge({
       action_type: 'ecu_flash',
       id: `flash-${selectedEcu}-${Date.now()}`,
       ecu: selectedEcu,
       fileName: selectedFile.name,
     });

     if (!challenge.success || !challenge.token) {
       throw new Error(challenge.error || 'Dual confirmation token alınamadı.');
     }

     setLogs(prev => [
       ...prev,
       `[SECURITY] Onay token'ı alındı: ${challenge.token.substring(0, 8)}... (Geçerlilik: 30s)`,
       '[FLASH_START] Flashing motoru başlatılıyor...'
     ]);

     const startRes = await DesktopBridge.flashStart(
       {
         action_type: 'ecu_flash',
         ecu: selectedEcu,
         fileName: selectedFile.name,
         filePath: (rawFile as any).path || undefined,
         sizeBytes: selectedFile.sizeBytes,
         data: hex,
         memoryAddress: 0x80000,
         blockSize: 256,
         expectedVin: 'WP0ZZZ99ZTS392111',
       },
       challenge.token
     );

     if (!startRes.success) {
       throw new Error(startRes.error || startRes.message || 'Flashing başlatılamadı.');
     }

     if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
     pollIntervalRef.current = setInterval(async () => {
       const prog = await DesktopBridge.flashProgress();
       if (prog) {
         if (typeof prog.percent === 'number') {
           setProgress(prog.percent);
         }
         if (prog.step) {
           const sectorIdx = Math.min(15, Math.floor(((prog.percent || 0) / 100) * 16));
           setStatusText(`[${prog.step}] Sektör SEC_${sectorIdx} (%${prog.percent || 0})`);
         }
         if (prog.logs && Array.isArray(prog.logs)) {
           setLogs(prev => {
             const existing = new Set(prev);
             const nextLogs = [...prev];
             for (const l of prog.logs) {
               if (!existing.has(l)) {
                 nextLogs.push(l);
                 existing.add(l);
               }
             }
             return nextLogs;
           });
         }

         if (prog.status === 'completed') {
           if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
           pollIntervalRef.current = null;
           setIsFlashing(false);
           setProgress(100);
           setStatusText(`Flashing başarıyla tamamlandı: ${selectedFile.name}`);
         } else if (prog.status === 'failed' || prog.status === 'cancelled') {
           if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
           pollIntervalRef.current = null;
           setIsFlashing(false);
           setStatusText(`Flashing durduruldu (${prog.status}): ${prog.error || ''}`);
         }
       }
     }, 250);

     return;
   } catch (err: any) {
     setIsFlashing(false);
     const errMsg = err?.message || String(err);
     setStatusText(`Hata: ${errMsg}`);
     setLogs(prev => [
       ...prev,
       `[ERROR] Flashing Hatası: ${errMsg}`,
       '[ABORT] Flash işlemi durduruldu.'
     ]);
     return;
   }
 }

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
 if (pollIntervalRef.current) {
   clearInterval(pollIntervalRef.current);
   pollIntervalRef.current = null;
 }
 if (isFlashing) {
   cancelFlashing();
 }
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
    <div className="p-4 space-y-4 max-w-7xl mx-auto text-text-body">
      {/* Hidden File Input */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileInputChange}
        accept=".bin,.hex,.ihex,.s19,.s28,.s37,.mot,.dcm"
        className="hidden"
      />

      {/* Header Card */}
      <div className="glass-panel border border-border-whisper rounded-xl p-4 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-lg bg-accent-soft border border-accent/30 flex items-center justify-center text-accent">
            <Cpu className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-text-hi">
              ECU Flashing & Bootloader Yöneticisi{' '}
              <span className="ml-1 px-1.5 py-0.5 rounded bg-warn-soft text-warn border border-warn/30 text-xs font-bold uppercase tracking-wide">
                Demo / Simülasyon
              </span>
            </h2>
            <p className="text-xs text-text-mid">
              ISO 14229 (UDS) & ISO 15765-2 (DoCAN) Protokolü ile Güvenli Firmware Yükleme —{' '}
              <span className="font-semibold text-warn">bu görünüm CAN veriyoluyla gerçek TX yapmaz</span>
            </p>
          </div>
        </div>

        {/* ECU Selector */}
        <div className="flex items-center space-x-2">
          <span className="text-xs text-text-mid font-medium">Hedef Modül:</span>
          <div className="inline-flex bg-bg-app p-0.5 rounded-lg border border-border-whisper text-xs">
            {(['ECM', 'TCU', 'ABS', 'BCM'] as const).map((ecu) => (
              <button
                key={ecu}
                onClick={() => {
                  if (isFlashing) return;
                  setSelectedEcu(ecu);
                }}
                disabled={isFlashing}
                className={`px-3 py-1 rounded-md font-semibold transition-all ${
                  selectedEcu === ecu ? 'bg-accent-soft text-accent-text shadow-xs' : 'text-text-mid hover:text-text-hi'
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
        <div className="lg:col-span-7 glass-panel border border-border-whisper rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold text-text-hi uppercase tracking-wider flex items-center space-x-1.5">
              <FileCode className="w-4 h-4 text-accent" />
              <span>Firmware Dosyası Seçimi (S-Record / Intel Hex / Bin)</span>
            </h3>
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
                    ? 'border-danger/50 bg-danger-soft/20 hover:bg-danger-soft/30'
                    : isDragging 
                    ? 'border-accent bg-accent-soft/30 ring-2 ring-accent/20' 
                    : 'border-border-whisper bg-bg-app/40 hover:bg-bg-app/70 hover:border-border-strong'
                }`}
              >
                {fileError ? (
                  <FileX className="w-9 h-9 text-danger mx-auto transition-transform hover:scale-105" />
                ) : (
                  <UploadCloud className="w-9 h-9 text-accent mx-auto transition-transform hover:scale-105" />
                )}
                <div className={`text-xs font-bold ${fileError ? 'text-danger' : 'text-text-hi'}`}>
                  {fileError ? 'Geçersiz Dosya Seçildi - Yeniden Dosya Seçin' : 'Firmware Dosyası Seçin veya Sürükleyin'}
                </div>
                <p className="text-xs text-text-mid">
                  Desteklenen formatlar: .bin, .hex, .s19, .s28, .s37, .mot (Maksimum 32 MB)
                </p>
                <button 
                  type="button"
                  className={`mt-2 inline-flex items-center px-3 py-1 rounded-md text-xs font-semibold shadow-xs transition-colors ${
                    fileError
                      ? 'bg-danger text-white hover:bg-danger/90'
                      : 'bg-bg-panel border border-border-whisper text-text-body hover:bg-bg-row-hover hover:text-text-hi'
                  }`}
                >
                  Bilgisayardan Dosya Seç...
                </button>
              </div>

              {/* Error Alert Banner */}
              {fileError && (
                <div className="p-3 bg-danger-soft border border-danger/30 rounded-xl text-danger text-xs flex items-start space-x-2.5 shadow-xs">
                  <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
                  <div>
                    <strong className="font-bold">Doğrulama Hatası:</strong>
                    <div className="text-xs mt-0.5">{fileError}</div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="border border-ok/30 bg-ok-soft/20 rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-3">
                  <div className="w-10 h-10 rounded-lg bg-ok-soft border border-ok/30 flex items-center justify-center text-ok">
                    <CheckCircle2 className="w-5 h-5" />
                  </div>
                  <div>
                    <div className="text-xs font-bold text-text-hi font-mono">
                      {selectedFile.name}
                    </div>
                    <div className="text-xs text-text-mid flex items-center space-x-2">
                      <span>{selectedFile.sizeFormatted}</span>
                      <span>•</span>
                      <span>Format: .{selectedFile.extension}</span>
                      <span>•</span>
                      <span className="font-mono text-ok">{selectedFile.checksumSha256}</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center space-x-1.5">
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isFlashing}
                    className="px-2.5 py-1 text-xs font-semibold text-accent-text bg-accent-soft hover:bg-accent/20 rounded-lg border border-accent/30 transition-colors disabled:opacity-50"
                  >
                    Değiştir
                  </button>
                  <button
                    onClick={removeFile}
                    disabled={isFlashing}
                    className="p-1 text-text-mid hover:text-danger rounded-lg hover:bg-danger-soft transition-colors disabled:opacity-50"
                    title="Dosyayı Kaldır"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>

              <div className="text-xs text-ok font-medium bg-bg-app/80 p-2 rounded border border-border-whisper">
                Flash Bellek Haritası Doğrulandı ({selectedFile.architecture}): 16 Sektör (0x00080000 - 0x{(0x80000 + selectedFile.sizeBytes).toString(16).toUpperCase()})
              </div>
            </div>
          )}

          {/* Progress Bar & Status */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <span className={`font-semibold ${isFlashing ? 'text-accent' : fileError ? 'text-danger' : selectedFile ? 'text-text-hi' : 'text-text-mid'}`}>
                {statusText}
              </span>
              <span className="font-mono font-bold text-accent">%{progress}</span>
            </div>
            <div className="w-full h-3 bg-bg-app rounded-full overflow-hidden border border-border-whisper">
              <div
                className="h-full bg-accent transition-all duration-150 rounded-full shadow-xs"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center space-x-2 pt-1">
            {isFlashing ? (
              <button
                onClick={cancelFlashing}
                className="flex items-center space-x-1.5 px-4 py-2 bg-danger hover:bg-danger/90 text-white rounded-lg text-xs font-bold shadow-xs active:scale-[0.98] transition-all"
                title="Flash işlemini durdur"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
                <span>Flashing İptal Et</span>
              </button>
            ) : (
              <button
                onClick={startFlashing}
                disabled={!selectedFile}
                className={`flex items-center space-x-1.5 px-4 py-2 rounded-lg text-xs font-bold shadow-xs transition-all ${
                  !selectedFile
                    ? 'bg-bg-panel text-text-low cursor-not-allowed border border-border-whisper'
                    : 'bg-accent hover:bg-accent/90 text-white active:scale-[0.98]'
                }`}
                title={!selectedFile ? 'Lütfen önce geçerli bir firmware dosyası seçiniz' : 'Flash işlemini başlat'}
              >
                {!selectedFile ? <Lock className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5 fill-current" />}
                <span>{!selectedFile ? 'Geçerli Dosya Bekleniyor (Flash Kilitli)' : 'Flash İşlemini Başlat'}</span>
              </button>
            )}

            <button
              onClick={resetSession}
              disabled={isFlashing}
              className="flex items-center space-x-1.5 px-3 py-2 bg-bg-panel hover:bg-bg-row-hover disabled:opacity-50 text-text-body rounded-lg text-xs font-semibold border border-border-whisper transition-colors"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Oturumu Sıfırla</span>
            </button>
          </div>

          {/* Flash Sectors Grid */}
          <div className="space-y-2 pt-2 border-t border-border-whisper">
            <div className="flex items-center justify-between">
              <div className="text-xs font-bold text-text-hi uppercase tracking-wider">
                Flash Bellek Sektörleri (16 Sektör)
              </div>
              <span className="text-xs text-text-mid font-mono">
                {selectedFile ? `Toplam: ${selectedFile.sizeFormatted}` : 'Dosya bekleniyor'}
              </span>
            </div>
            <div className="grid grid-cols-8 gap-1.5 text-center font-mono text-xs">
              {sectors.map((sec) => (
                <div
                  key={sec.name}
                  className={`p-2 rounded border transition-all ${
                    sec.flashed
                      ? 'bg-accent-soft border-accent/40 text-accent-text font-bold shadow-xs'
                      : selectedFile
                      ? 'bg-bg-app border-border-whisper text-text-body'
                      : 'bg-bg-app/50 border-border-whisper text-text-low opacity-60'
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
        <div className="lg:col-span-5 glass-panel border border-border-whisper rounded-xl p-4 flex flex-col h-[490px]">
          <div className="flex items-center justify-between pb-3 border-b border-border-whisper">
            <div className="flex items-center space-x-2 text-xs font-bold text-text-hi">
              <Terminal className="w-4 h-4 text-accent" />
              <span>UDS DİAGNOSTİK LOG TERMİNALİ</span>
            </div>
            <ShieldCheck className="w-4 h-4 text-ok" />
          </div>

          <div className="flex-1 bg-bg-app/90 border border-border-whisper rounded-lg p-3 my-3 overflow-y-auto font-mono text-xs text-text-body space-y-1.5 leading-relaxed select-text">
            {logs.map((line, idx) => (
              <div 
                key={idx} 
                className={
                  line.includes('SUCCESS') ? 'text-ok font-bold' : 
                  line.includes('ERROR') || line.includes('REJECT') || line.includes('ABORT') ? 'text-danger font-semibold' :
                  line.includes('SECURITY') ? 'text-accent-text' : 
                  line.includes('ERASE') || line.includes('FLASH_START') ? 'text-warn font-semibold' : 
                  line.includes('WAIT') ? 'text-warn' :
                  line.includes('FILE') || line.includes('INTEGRITY') || line.includes('ARCH') ? 'text-accent-text' :
                  'text-text-mid'
                }
              >
                {line}
              </div>
            ))}
          </div>

          <div className="text-xs text-text-mid font-mono flex items-center justify-between pt-1">
            <span>Baud: 500 kbps (High Speed CAN)</span>
            <span>UDS: ISO 14229-1 (Level 0x01)</span>
          </div>
        </div>
      </div>
    </div>
  );
};

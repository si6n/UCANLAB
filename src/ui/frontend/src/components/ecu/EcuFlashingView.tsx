import React, { useState, useRef, useEffect } from 'react';
import { 
  Cpu, 
  UploadCloud, 
  Play, 
  RotateCcw, 
  FileCode, 
  Terminal,
  AlertCircle,
  AlertTriangle,
  Trash2,
  Lock,
  Square
} from 'lucide-react';
import { DesktopBridge } from '../../services/bridge';
import { buildFlashRequest, FLASH_ALLOWED_BLOCK_SIZES } from './flashRequest';

interface SelectedFirmware {
  name: string;
  sizeBytes: number;
  sizeFormatted: string;
  checksumSha256: string;
  extension: string;
  architecture: string;
}

type EcuId = 'ECM' | 'TCU' | 'ABS' | 'BCM';

const ECU_PROFILES: Record<EcuId, { mcu: string; flash: string; pkg: string; tx: string; rx: string }> = {
  ECM: { mcu: 'Infineon TriCore TC275TP', flash: '4096 KB', pkg: 'BGA-292', tx: '0x7E0', rx: '0x7E8' },
  TCU: { mcu: 'Bosch C167CR', flash: '1024 KB', pkg: 'MQFP-144', tx: '0x7E1', rx: '0x7E9' },
  ABS: { mcu: 'NXP S32K344 Cortex-M7', flash: '4096 KB', pkg: 'MAPBGA-257', tx: '0x7E2', rx: '0x7EA' },
  BCM: { mcu: 'ST SPC58', flash: '2048 KB', pkg: 'eTQFP-176', tx: '0x7E4', rx: '0x7EC' },
};

const SECTOR_COUNT = 16;

export const EcuFlashingView: React.FC = () => {
  const [selectedEcu, setSelectedEcu] = useState<EcuId>('ECM');
  const [selectedFile, setSelectedFile] = useState<SelectedFirmware | null>(null);
  const [rawFile, setRawFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [isFlashing, setIsFlashing] = useState(false);
  const [statusText, setStatusText] = useState('Firmware dosyası bekleniyor');
  const [isDragging, setIsDragging] = useState(false);
  const [firmwareSignature, setFirmwareSignature] = useState('');
  const [trustedPubkey, setTrustedPubkey] = useState('');
  const [expectedVin, setExpectedVin] = useState('');
  const [expectedSerial, setExpectedSerial] = useState('');
  const [memoryAddress, setMemoryAddress] = useState(0x80000);
  const [blockSize, setBlockSize] = useState(256);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [logs, setLogs] = useState<string[]>([
    '[INIT] ISO 14229-1 (UDS) / ISO 15765-2 (DoCAN) hazır',
    '[COMM] CAN fiziksel katman: 500 kbps',
    '[INFO] Hedef ECM · TX 0x7E0 · RX 0x7E8',
    '[WAIT] Firmware dosyası seçin (.bin, .hex, .s19, .mot)'
  ]);

  const chip = ECU_PROFILES[selectedEcu];
  const activeSector = progress >= 100 ? SECTOR_COUNT - 1 : Math.min(SECTOR_COUNT - 1, Math.floor((progress / 100) * SECTOR_COUNT));

  const validateFirmwareFile = async (file: File): Promise<{ isValid: boolean; error?: string; checksum: string; arch: string }> => {
    const ext = file.name.split('.').pop()?.toLowerCase() || '';
    const allowedExtensions = ['bin', 'hex', 'ihex', 's19', 's28', 's37', 'mot', 'dcm', 'frf', 'odx', 'pdx'];

    if (!allowedExtensions.includes(ext)) {
      return {
        isValid: false,
        error: `Desteklenmeyen uzantı (.${ext || 'bilinmiyor'}). Kabul edilen: .bin, .hex, .s19, .mot`,
        checksum: '',
        arch: 'Unknown'
      };
    }

    if (file.size < 1024) {
      return {
        isValid: false,
        error: `Dosya çok küçük (${file.size} bayt). En az 1 KB gerekir.`,
        checksum: '',
        arch: 'Invalid'
      };
    }

    if (file.size > 32 * 1024 * 1024) {
      return {
        isValid: false,
        error: `Dosya flash sınırını aşıyor (${(file.size / (1024 * 1024)).toFixed(1)} MB). Üst sınır 32 MB.`,
        checksum: '',
        arch: 'Overflow'
      };
    }

    const slice = file.slice(0, 2048);
    const textSample = await slice.text();
    const arrayBuffer = await slice.arrayBuffer();
    const bytes = new Uint8Array(arrayBuffer);

    if (ext === 'hex' || ext === 'ihex') {
      const lines = textSample.trim().split(/\r?\n/).filter(l => l.trim().length > 0);
      if (lines.length === 0 || !lines[0].startsWith(':')) {
        return {
          isValid: false,
          error: 'Geçersiz Intel Hex biçimi: kayıtlar ":" ile başlamıyor.',
          checksum: '',
          arch: 'Invalid Hex'
        };
      }
      const firstLineHex = lines[0].substring(1).trim();
      if (!/^[0-9A-Fa-f]+$/.test(firstLineHex) || firstLineHex.length < 10) {
        return {
          isValid: false,
          error: 'Intel Hex bozuk veri karakterleri içeriyor.',
          checksum: '',
          arch: 'Corrupted Hex'
        };
      }
    }

    if (['s19', 's28', 's37', 'mot'].includes(ext)) {
      const lines = textSample.trim().split(/\r?\n/).filter(l => l.trim().length > 0);
      if (lines.length === 0 || !/^S[0-9]/i.test(lines[0])) {
        return {
          isValid: false,
          error: 'Geçersiz Motorola S-Record biçimi (S0/S1/S2/S3 bekleniyor).',
          checksum: '',
          arch: 'Invalid S-Record'
        };
      }
    }

    if (ext === 'bin') {
      let allZeros = true;
      let allOnes = true;
      for (let i = 0; i < Math.min(bytes.length, 512); i++) {
        if (bytes[i] !== 0x00) allZeros = false;
        if (bytes[i] !== 0xFF) allOnes = false;
      }
      if (allZeros || allOnes) {
        return {
          isValid: false,
          error: `Boş bellek dökümü (${allZeros ? '0x00' : '0xFF'}).`,
          checksum: '',
          arch: 'Empty Binary'
        };
      }

      let asciiCount = 0;
      for (let i = 0; i < Math.min(bytes.length, 512); i++) {
        if ((bytes[i] >= 32 && bytes[i] <= 126) || bytes[i] === 10 || bytes[i] === 13) {
          asciiCount++;
        }
      }
      if (asciiCount / Math.min(bytes.length, 512) > 0.95 && (textSample.includes('<!DOCTYPE') || textSample.includes('<html') || textSample.includes('import ') || textSample.includes('function '))) {
        return {
          isValid: false,
          error: 'Düz metin veya kaynak kod dosyası tespit edildi.',
          checksum: '',
          arch: 'Plain Text'
        };
      }
    }

    let arch = '32-bit TriCore / PowerPC';
    if (ext === 'hex') arch = 'Intel Hex 32-bit';
    else if (ext === 's19' || ext === 's28' || ext === 's37') arch = 'Motorola S-Record 32-bit';

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
      ? 'SHA-256 kullanılamıyor'
      : `SHA-256 ${digestHex.substring(0, 12)}...${digestHex.substring(digestHex.length - 6)}`;

    return { isValid: true, checksum, arch };
  };

  const handleFile = async (file: File) => {
    setFileError(null);
    setProgress(0);

    if (!file || file.size === 0) {
      setSelectedFile(null);
      setRawFile(null);
      setFileError('Seçilen dosya boş (0 bayt).');
      setStatusText('Hata: dosya boş');
      return;
    }

    const validation = await validateFirmwareFile(file);

    if (!validation.isValid) {
      setSelectedFile(null);
      setRawFile(null);
      setFileError(validation.error || 'Geçersiz dosya biçimi.');
      setStatusText('Hata: geçersiz dosya');
      setLogs([
        `[INFO] Hedef ${selectedEcu} · ${chip.mcu}`,
        `[ERROR] Doğrulama başarısız: "${file.name}"`,
        `[REJECT] ${validation.error}`
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
    setRawFile(file);
    setFileError(null);
    setProgress(0);
    setStatusText('Hazır');

    setLogs([
      `[INFO] Hedef ${selectedEcu} · ${chip.mcu}`,
      `[FILE] ${file.name} · ${sizeKB} KB · ${ext}`,
      `[ARCH] ${validation.arch}`,
      `[HASH] ${loadedFile.checksumSha256}`,
      `[MAP] ${SECTOR_COUNT} sektör · 0x00080000 - 0x${(0x80000 + sizeBytes).toString(16).toUpperCase()}`,
      '[FILE] İmaj ayrıştırıldı, yükleme için hazır.'
    ]);
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleFile(e.target.files[0]);
    }
  };

  const removeFile = (e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setSelectedFile(null);
    setRawFile(null);
    setFileError(null);
    setProgress(0);
    setIsFlashing(false);
    setStatusText('Firmware dosyası bekleniyor');
    if (fileInputRef.current) fileInputRef.current.value = '';
    setLogs([
      `[INFO] Hedef ${selectedEcu}`,
      '[WAIT] Dosya kaldırıldı. Firmware dosyası bekleniyor.'
    ]);
  };

  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const simIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const flashGenRef = useRef(0);

  const clearAllFlashTimers = () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    if (simIntervalRef.current) {
      clearInterval(simIntervalRef.current);
      simIntervalRef.current = null;
    }
  };

  useEffect(() => {
    return () => {
      clearAllFlashTimers();
    };
  }, []);

  const cancelFlashing = async () => {
    flashGenRef.current += 1;
    clearAllFlashTimers();
    if (DesktopBridge.isNative()) {
      try {
        await DesktopBridge.flashCancel();
      } catch {
        // best-effort
      }
    }
    setIsFlashing(false);
    setStatusText('İptal edildi');
    setLogs(prev => [...prev, '[CANCEL] İşlem kullanıcı tarafından durduruldu.']);
  };

  const startFlashing = async () => {
    if (!selectedFile || !rawFile) return;
    if (isFlashing) return;

    setIsFlashing(true);
    setProgress(0);
    setStatusText('Güvenlik oturumu açılıyor (0x27)');

    if (DesktopBridge.isNative()) {
      try {
        setLogs(prev => [
          ...prev,
          `[INIT] ${selectedEcu} flashing başlatıldı`,
          '[SECURITY] Güvenlik tokeni doğrulanıyor...'
        ]);

        const arrayBuffer = await rawFile.arrayBuffer();
        const bytes = new Uint8Array(arrayBuffer);
        let hex = '';
        const chunkSize = 0x8000;
        for (let i = 0; i < bytes.length; i += chunkSize) {
          const chunk = bytes.subarray(i, i + chunkSize);
          hex += Array.from(chunk, b => b.toString(16).padStart(2, '0')).join('');
        }

        const challenge = await DesktopBridge.requestDiagnosticChallenge({
          action_type: 'ecu_flash',
          id: `flash-${selectedEcu}-${Date.now()}`,
          ecu: selectedEcu,
          fileName: selectedFile.name,
        });

        if (!challenge.success || !challenge.token) {
          throw new Error(challenge.error || 'Onay tokeni alınamadı.');
        }

        const built = buildFlashRequest({
          ecu: selectedEcu,
          fileName: selectedFile.name,
          filePath: (rawFile as any).path || undefined,
          sizeBytes: selectedFile.sizeBytes,
          data: hex,
          memoryAddress,
          blockSize,
          firmwareSignature,
          trustedPubkey,
          expectedVin: expectedVin || undefined,
          expectedSerial: expectedSerial || undefined,
        });
        if (!built.ok) {
          const errMsg = (built as { ok: false; error: string }).error;
          setIsFlashing(false);
          setStatusText(`Ön-koşul: ${errMsg}`);
          setLogs(prev => [...prev, `[REJECT] ${errMsg}`]);
          return;
        }
        const startRes = await DesktopBridge.flashStart(built.request, challenge.token);

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
              setStatusText(`${prog.step} · %${prog.percent || 0}`);
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
              setStatusText('Tamamlandı');
            } else if (prog.status === 'failed' || prog.status === 'cancelled') {
              if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
              pollIntervalRef.current = null;
              setIsFlashing(false);
              setStatusText(prog.error ? `Durduruldu: ${prog.error}` : 'Durduruldu');
            }
          }
        }, 250);

        return;
      } catch (err: any) {
        setIsFlashing(false);
        const errMsg = err?.message || String(err);
        setStatusText(`Hata: ${errMsg}`);
        setLogs(prev => [...prev, `[ERROR] ${errMsg}`]);
        return;
      }
    }

    // Simulation fallback
    setLogs(prev => [
      ...prev,
      `[SIM] ${selectedEcu} yeniden programlama dizisi başlatıldı`,
      '[SIM] 0x10 0x02 · DiagnosticSessionControl (programming)',
      '[SIM] 0x27 0x01 · SecurityAccess (seed/key doğrulandı)',
      `[SIM] 0x34 · RequestDownload (0x00080000, ${selectedFile.sizeFormatted})`
    ]);

    let current = 0;
    const gen = ++flashGenRef.current;
    if (simIntervalRef.current) clearInterval(simIntervalRef.current);
    simIntervalRef.current = setInterval(() => {
      if (gen !== flashGenRef.current) return;
      current += 2;
      setProgress(current);
      const sectorIdx = Math.min(SECTOR_COUNT - 1, Math.floor((current / 100) * SECTOR_COUNT));
      setStatusText(`Yazılıyor · sektör ${sectorIdx.toString().padStart(2, '0')} · %${current}`);

      if (current % 16 === 0) {
        const blockAddr = (0x00080000 + Math.floor((current / 100) * selectedFile.sizeBytes)).toString(16).toUpperCase();
        setLogs(prev => [
          ...prev,
          `[SIM] 0x36 · TransferData blok 0x${blockAddr} (sektör ${sectorIdx.toString().padStart(2, '0')}) ACK`
        ]);
      }

      if (current >= 100) {
        if (simIntervalRef.current) clearInterval(simIntervalRef.current);
        simIntervalRef.current = null;
        setIsFlashing(false);
        setStatusText(`Simülasyon tamamlandı: ${selectedFile.name} — ECU'ya YAZILMADI`);
        setLogs(prev => [
          ...prev,
          '[SIM] 0x37 · RequestTransferExit adımı (SIMÜLE EDİLDİ)',
          '[SIM] 0x31 · RoutineControl CRC adımı (SIMÜLE EDİLDİ — CRC hesaplanmadı)',
          '[SIM] 0x11 0x01 · ECUReset adımı (SIMÜLE EDİLDİ — ECU yeniden başlatılmadı)',
          `[DEMO] ${selectedFile.name} simülasyonu tamamlandı (%100).`,
          '[DEMO] Bu bir SİMÜLASYON sonucudur — gerçek bir araçta flash işlemi yapılmadı (ECU\'ya YAZILMADI).'
        ]);
      }
    }, 90);
  };

  const resetSession = () => {
    flashGenRef.current += 1;
    clearAllFlashTimers();
    if (isFlashing) {
      cancelFlashing();
    }
    setProgress(0);
    setIsFlashing(false);
    setStatusText(selectedFile ? 'Hazır' : 'Firmware dosyası bekleniyor');
    setLogs([
      `[INFO] Hedef ${selectedEcu} · ${chip.mcu}`,
      selectedFile ? `[READY] ${selectedFile.name} hazır.` : '[WAIT] Firmware dosyası bekleniyor.',
      '[RESET] 0x10 0x01 · default session'
    ]);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden text-text-body font-sans select-none">
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileInputChange}
        accept=".bin,.hex,.ihex,.s19,.s28,.s37,.mot,.dcm"
        className="hidden"
      />

      {/* Header */}
      <div className="flex shrink-0 items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Cpu className="h-4 w-4 text-text-low" />
          <h2 className="text-sm font-medium text-text-hi">ECU Flashing</h2>
          <span className="font-mono text-[11px] text-text-low">UDS ISO 14229 · DoCAN</span>
        </div>

        <div className="flex items-center rounded-[8px] border border-border bg-surface-inset/50 p-0.5">
          {(['ECM', 'TCU', 'ABS', 'BCM'] as const).map((ecu) => (
            <button
              key={ecu}
              onClick={() => {
                if (isFlashing) return;
                setSelectedEcu(ecu);
              }}
              disabled={isFlashing}
              className={`rounded-[6px] px-3.5 py-1 font-mono text-xs font-medium transition-colors duration-100 ${
                selectedEcu === ecu ? 'bg-accent text-white' : 'text-text-mid hover:text-text-hi'
              } ${isFlashing ? 'cursor-not-allowed' : ''}`}
            >
              {ecu}
            </button>
          ))}
        </div>
      </div>

      {!DesktopBridge.isNative() && (
        <div className="rounded-[8px] border border-amber-500/30 bg-amber-500/10 px-3.5 py-2 text-[11px] text-amber-400 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
          <span><strong>Simülasyon Modu:</strong> Web ortamında donanım bağlantısı yoktur. Flashing işlemleri simüle edilir, gerçek ECU'ya veri yazılmaz (ECU'ya YAZILMADI).</span>
        </div>
      )}

      {/* Main */}
      <div className="grid flex-1 min-h-0 grid-cols-1 lg:grid-cols-12 gap-4 overflow-hidden">
        {/* Left: target, file, progress */}
        <div className="lg:col-span-7 flex min-h-0 flex-col gap-4">
          <div className="rounded-[12px] glass-panel p-5">
            <div className="flex items-center justify-between">
              <span className="text-xs text-text-mid">Hedef denetleyici</span>
              <span className="font-mono text-[11px] text-text-low">
                TX {chip.tx} · RX {chip.rx}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-4">
              <div>
                <div className="text-[11px] text-text-low">Mikrodenetleyici</div>
                <div className="mt-0.5 text-xs text-text-hi">{chip.mcu}</div>
              </div>
              <div>
                <div className="text-[11px] text-text-low">Flash</div>
                <div className="mt-0.5 font-mono text-xs text-text-hi">{chip.flash}</div>
              </div>
              <div>
                <div className="text-[11px] text-text-low">Kılıf</div>
                <div className="mt-0.5 font-mono text-xs text-text-hi">{chip.pkg}</div>
              </div>
            </div>
          </div>

          {!selectedFile ? (
            <div
              role="button"
              tabIndex={0}
              aria-label="Firmware dosyası seç"
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                setIsDragging(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragging(false);
                if (e.dataTransfer.files?.[0]) handleFile(e.dataTransfer.files[0]);
              }}
              className={`flex flex-1 min-h-[200px] flex-col items-center justify-center rounded-[12px] border border-dashed p-6 text-center transition-colors duration-100 cursor-pointer ${
                fileError
                  ? 'border-del/40 bg-del-bg'
                  : isDragging
                  ? 'border-accent bg-accent-soft/40'
                  : 'border-border hover:border-border-strong'
              }`}
            >
              <UploadCloud className={`h-5 w-5 ${fileError ? 'text-del' : 'text-text-low'}`} />
              <div className="mt-3 text-xs text-text-hi">
                {fileError ? 'Dosya reddedildi, yeniden seçin' : 'Firmware dosyası seçin veya sürükleyin'}
              </div>
              <div className="mt-1 font-mono text-[11px] text-text-low">
                .bin · .hex · .s19 · .mot · en fazla 32 MB
              </div>
              {fileError && (
                <div className="mt-3 flex items-center gap-1.5 text-[11px] text-del">
                  <AlertCircle className="h-3.5 w-3.5" />
                  {fileError}
                </div>
              )}
            </div>
          ) : (
            <div className="rounded-[12px] glass-panel p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <FileCode className="h-4 w-4 shrink-0 text-text-low" />
                  <div className="min-w-0">
                    <div className="truncate font-mono text-xs text-text-hi">{selectedFile.name}</div>
                    <div className="mt-1 flex items-center gap-2 font-mono text-[11px] text-text-low">
                      <span>{selectedFile.sizeFormatted}</span>
                      <span className="text-border-strong">/</span>
                      <span>{selectedFile.extension}</span>
                      <span className="text-border-strong">/</span>
                      <span>{selectedFile.architecture}</span>
                    </div>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isFlashing}
                    className="rounded-[6px] px-2.5 py-1 text-xs text-text-mid transition-colors duration-100 hover:text-text-hi disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Değiştir
                  </button>
                  <button
                    onClick={removeFile}
                    disabled={isFlashing}
                    className="rounded-[6px] p-1.5 text-text-low transition-colors duration-100 hover:text-del disabled:cursor-not-allowed disabled:opacity-50"
                    title="Dosyayı kaldır"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-3 font-mono text-[10px] text-text-low">
                <span>{selectedFile.checksumSha256}</span>
                <span>0x00080000 - 0x{(0x80000 + selectedFile.sizeBytes).toString(16).toUpperCase()}</span>
              </div>
            </div>
          )}

          {/* Trust anchor + identity (B-07: backend pre-arm gates) */}
          <div className="rounded-[12px] glass-panel p-5 space-y-3">
            <div className="text-xs text-text-mid">Güven kökü ve hedef kimlik (flash öncesi zorunlu)</div>
            <div>
              <label className="text-[11px] font-mono text-text-low">Firmware imzası (hex)</label>
              <textarea
                value={firmwareSignature}
                onChange={(e) => setFirmwareSignature(e.target.value)}
                rows={2}
                placeholder="Ed25519 imza (hex)"
                className="mt-1 w-full rounded border border-border/70 bg-surface-inset px-2 py-1.5 font-mono text-xs text-text-hi"
              />
            </div>
            <div>
              <label className="text-[11px] font-mono text-text-low">Güvenilir ortak anahtar (hex)</label>
              <input
                type="text"
                value={trustedPubkey}
                onChange={(e) => setTrustedPubkey(e.target.value)}
                placeholder="Ed25519 pubkey (hex)"
                className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[11px] font-mono text-text-low">Hedef VIN (17)</label>
                <input
                  type="text"
                  value={expectedVin}
                  onChange={(e) => setExpectedVin(e.target.value.toUpperCase())}
                  maxLength={17}
                  placeholder="17 haneli VIN"
                  className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi"
                />
              </div>
              <div>
                <label className="text-[11px] font-mono text-text-low">veya Seri No</label>
                <input
                  type="text"
                  value={expectedSerial}
                  onChange={(e) => setExpectedSerial(e.target.value)}
                  placeholder="ECU seri"
                  className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[11px] font-mono text-text-low">Bellek adresi (hex)</label>
                <input
                  type="text"
                  value={'0x' + memoryAddress.toString(16).toUpperCase()}
                  onChange={(e) => {
                    const v = parseInt(e.target.value, 16);
                    if (Number.isFinite(v)) setMemoryAddress(v);
                  }}
                  className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi"
                />
              </div>
              <div>
                <label className="text-[11px] font-mono text-text-low">Blok boyutu</label>
                <select
                  value={blockSize}
                  onChange={(e) => setBlockSize(parseInt(e.target.value, 10))}
                  className="mt-1 h-7 w-full rounded border border-border/70 bg-surface-inset px-2 font-mono text-xs text-text-hi"
                >
                  {FLASH_ALLOWED_BLOCK_SIZES.map((b) => (
                    <option key={b} value={b}>{b}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Progress */}
          <div className="rounded-[12px] glass-panel p-5">
            <div className="flex items-baseline justify-between">
              <span className="truncate text-xs text-text-mid">{statusText}</span>
              <span className="font-mono text-sm text-text-hi">%{progress}</span>
            </div>
            <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-surface-inset">
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-150"
                style={{ width: `${progress}%` }}
              />
            </div>

            {/* Sector strip */}
            <div className="mt-4 flex items-center justify-between">
              <span className="text-[11px] text-text-low">Sektörler</span>
              <span className="font-mono text-[11px] text-text-low">
                {selectedFile ? selectedFile.sizeFormatted : `${SECTOR_COUNT} x 64 KB`}
              </span>
            </div>
            <div className="mt-2 flex gap-[3px]">
              {Array.from({ length: SECTOR_COUNT }, (_, i) => {
                const done = progress >= ((i + 1) / SECTOR_COUNT) * 100;
                const active = isFlashing && i === activeSector;
                return (
                  <div
                    key={i}
                    title={`Sektör ${i.toString().padStart(2, '0')} · 0x${(0x80000 + i * 0x10000).toString(16).toUpperCase()}`}
                    className={`h-1.5 flex-1 rounded-full transition-colors duration-150 ${
                      done ? 'bg-accent' : active ? 'bg-accent/50' : 'bg-border-strong'
                    }`}
                  />
                );
              })}
            </div>

            <div className="mt-5 flex items-center gap-2.5 border-t border-border/60 pt-4">
              {isFlashing ? (
                <button
                  onClick={cancelFlashing}
                  className="flex items-center gap-1.5 rounded-[8px] bg-del px-4 py-2 text-xs font-medium text-white transition-transform duration-100 active:scale-[0.98]"
                >
                  <Square className="h-3.5 w-3.5 fill-current" />
                  <span>İptal et</span>
                </button>
              ) : (
                <button
                  onClick={startFlashing}
                  disabled={!selectedFile}
                  className={`flex items-center gap-1.5 rounded-[8px] px-4 py-2 text-xs font-medium transition-all duration-100 ${
                    !selectedFile
                      ? 'cursor-not-allowed bg-surface-inset text-text-low'
                      : 'bg-accent text-white hover:bg-accent/90 active:scale-[0.98]'
                  }`}
                >
                  {!selectedFile ? <Lock className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 fill-current" />}
                  <span>Flash başlat</span>
                </button>
              )}

              <button
                onClick={resetSession}
                disabled={isFlashing}
                className="flex items-center gap-1.5 rounded-[8px] border border-border px-3.5 py-2 text-xs text-text-mid transition-colors duration-100 hover:text-text-hi disabled:cursor-not-allowed disabled:opacity-50"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                <span>Sıfırla</span>
              </button>
            </div>
          </div>
        </div>

        {/* Right: UDS log */}
        <div className="lg:col-span-5 flex min-h-0 flex-col rounded-[12px] glass-panel p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Terminal className="h-3.5 w-3.5 text-text-low" />
              <span className="text-xs text-text-mid">UDS günlüğü</span>
            </div>
            <span className="font-mono text-[11px] text-text-low">500 kbps</span>
          </div>

          <div className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-[8px] bg-surface-inset/50 p-3 font-mono text-[11px] leading-relaxed select-text">
            {logs.map((line, idx) => (
              <div
                key={idx}
                className={`py-0.5 ${
                  line.includes('ERROR') || line.includes('REJECT') || line.includes('CANCEL')
                    ? 'text-del'
                    : line.includes('SECURITY') || line.includes('HASH')
                    ? 'text-accent-text'
                    : line.includes('WAIT')
                    ? 'text-warn'
                    : 'text-text-mid'
                }`}
              >
                {line}
              </div>
            ))}
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-3 font-mono text-[10px] text-text-low">
            <span>Blok 256 bayt</span>
            <span>ISO 15765-2</span>
          </div>
        </div>
      </div>
    </div>
  );
};

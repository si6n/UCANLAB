import React, { useState, useEffect } from 'react';
import { 
  FileText, 
  Download, 
  FileSpreadsheet, 
  Code, 
  CheckCircle2, 
  Database, 
  MapPin, 
  Shield, 
  Cloud,
  CloudUpload,
  Loader2,
  AlertCircle
} from 'lucide-react';
import { CANFrame } from '../../types/can';
import { ExportService, ExportResult } from '../../services/exportService';
import { DesktopBridge, CloudUploadProgress } from '../../services/bridge';

interface ReportsExportViewProps {
  frames: CANFrame[];
}

interface FormatRow {
  id: string;
  name: string;
  ext: string;
  desc: string;
  icon: React.ComponentType<{ className?: string }>;
  run: (frames: CANFrame[], vin: string) => Promise<ExportResult>;
  tone: 'accent' | 'ok' | 'del';
}

export const ReportsExportView: React.FC<ReportsExportViewProps> = ({ frames }) => {
  const [lastExport, setLastExport] = useState<ExportResult | null>(null);
  const [vinInput, setVinInput] = useState('TR-MARIN-2026-X99');
  const [pendingFormat, setPendingFormat] = useState<string | null>(null);

  const [cloudUploading, setCloudUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<CloudUploadProgress | null>(null);
  const [cloudUploadResult, setCloudUploadResult] = useState<{ sessionId: string; status: string } | null>(null);
  const [cloudError, setCloudError] = useState<string | null>(null);

  useEffect(() => {
    window.onCloudUploadProgress = (progress: CloudUploadProgress) => {
      setUploadProgress(progress);
    };
    return () => {
      window.onCloudUploadProgress = undefined;
    };
  }, []);

  const handleExport = async (formatId: string, exportFn: () => Promise<ExportResult>) => {
    setPendingFormat(formatId);
    try {
      const res = await exportFn();
      if (res && res.success) {
        setLastExport(res);
        setTimeout(() => setLastExport(null), 6000);
      }
    } finally {
      setPendingFormat(null);
    }
  };

  const handleUploadToCloud = async () => {
    if (frames.length === 0) {
      setCloudError('Yüklenecek telemetri çerçevesi yok. Önce veri akışını başlatın.');
      return;
    }

    setCloudUploading(true);
    setCloudError(null);
    setCloudUploadResult(null);
    setUploadProgress({
      totalChunks: 1,
      uploadedChunks: 0,
      bytesSent: 0,
      totalBytes: 0,
      percent: 0,
      status: 'uploading'
    });

    try {
      const lines = [
        'MDF4.10  Universal CAN ASAM MDF4 Measurement Log',
        `Timestamp: ${new Date().toISOString()}`,
        'Channel: CAN_Bus_Raw',
        `Frame_Count: ${frames.length}`,
        `VIN: ${vinInput}`,
        '--- BEGIN ASAM MDF4 LOG BLOCKS ---'
      ];
      frames.forEach((f, idx) => {
        lines.push(`HD_BLOCK_${idx}: T=${f.timeSec.toFixed(6)} ID=${f.canIdHex} DLC=${f.dlc} DATA=${f.dataHex.join('')} DIR=${f.dir}`);
      });
      lines.push('--- END ASAM MDF4 LOG BLOCKS ---');

      const res = await DesktopBridge.cloudUploadRawContent(`telemetry_${Date.now()}.mf4`, lines.join('\r\n'), vinInput);
      if (res.success && res.sessionId) {
        setCloudUploadResult({ sessionId: res.sessionId, status: res.status || 'ready' });
      } else {
        setCloudError(res.error || 'Yükleme başarısız oldu.');
      }
    } catch (err: any) {
      setCloudError(err.message || 'Yükleme sırasında beklenmeyen hata oluştu.');
    } finally {
      setCloudUploading(false);
    }
  };

  const formats: FormatRow[] = [
    {
      id: 'csv',
      name: 'CSV telemetri tablosu',
      ext: '.csv',
      desc: 'Zaman damgası, CAN ID, DLC, baytlar ve ASCII sütunları.',
      icon: FileSpreadsheet,
      run: (f) => ExportService.exportToCsv(f),
      tone: 'ok'
    },
    {
      id: 'asc',
      name: 'Vector CANoe / CANalyzer',
      ext: '.asc',
      desc: 'CANoe ve PCAN ile yeniden oynatılabilir trace logu.',
      icon: FileText,
      run: (f) => ExportService.exportToAsc(f),
      tone: 'accent'
    },
    {
      id: 'mf4',
      name: 'ASAM MDF4 telemetri',
      ext: '.mf4',
      desc: 'INCA, CANape ve MATLAB uyumlu MDF4 blokları.',
      icon: Database,
      run: (f) => ExportService.exportToMdf4(f),
      tone: 'accent'
    },
    {
      id: 'json',
      name: 'Ham JSON dökümü',
      ext: '.json',
      desc: 'Tüm çerçeveler ve zaman serisi metaverisi.',
      icon: Code,
      run: (f) => ExportService.exportToJson(f),
      tone: 'accent'
    },
    {
      id: 'kml',
      name: 'GPS rota haritası',
      ext: '.kml',
      desc: 'Güzergah telemetrisi Google Earth 3D üzerinde.',
      icon: MapPin,
      run: (f) => ExportService.exportToKml(f),
      tone: 'accent'
    },
    {
      id: 'report',
      name: 'Resmi servis raporu',
      ext: '.html',
      desc: 'SHA-256 oturum özetli, yazdırılabilir servis formu.',
      icon: Shield,
      run: (f, vin) => ExportService.exportToServiceReportHtml(f, vin),
      tone: 'del'
    },
  ];

  const toneText: Record<FormatRow['tone'], string> = {
    accent: 'text-accent',
    ok: 'text-ok',
    del: 'text-del',
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden text-text-body font-sans select-none">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between">
        <div className="flex items-center gap-2.5">
          <FileText className="h-4 w-4 text-text-low" />
          <h2 className="text-sm font-medium text-text-hi">Rapor & dışa aktarma</h2>
          <span className="font-mono text-[11px] text-text-low">
            {frames.length} kayıtlı çerçeve
          </span>
        </div>

        <div className="flex items-center gap-2.5">
          <label className="text-[11px] text-text-low">Araç VIN / HIN</label>
          <input
            type="text"
            maxLength={17}
            placeholder="17 haneli VIN"
            value={vinInput}
            onChange={(e) => setVinInput(e.target.value.toUpperCase())}
            className="w-52 rounded-[8px] border border-border bg-surface-inset/50 px-2.5 py-1.5 font-mono text-xs uppercase tracking-wider text-text-hi focus:outline-none transition-colors duration-100 focus:border-accent focus-visible:ring-1 focus-visible:ring-accent"
          />
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 content-start items-start gap-4 overflow-y-auto lg:grid-cols-12">
        {/* Left: export formats */}
        <div className="lg:col-span-8 rounded-[12px] glass-panel p-5">
          <div className="flex items-center justify-between">
            <span className="text-xs text-text-mid">Dışa aktarma biçimleri</span>
            <span className="text-[11px] text-text-low">
              {frames.length === 0 ? 'Çerçeve bekleniyor' : 'Tek tıkla indirilir'}
            </span>
          </div>

          <div className="mt-4">
            {formats.map((fmt, idx) => {
              const Icon = fmt.icon;
              const isPending = pendingFormat === fmt.id;
              return (
                <div
                  key={fmt.id}
                  className={`flex items-center gap-4 py-3.5 ${
                    idx !== formats.length - 1 ? 'border-b border-border/60' : ''
                  }`}
                >
                  <Icon className={`h-4 w-4 shrink-0 ${toneText[fmt.tone]}`} />

                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="text-xs text-text-hi">{fmt.name}</span>
                      <span className="font-mono text-[10px] text-text-low">{fmt.ext}</span>
                    </div>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-text-low">{fmt.desc}</p>
                  </div>

                  <button
                    onClick={() => handleExport(fmt.id, () => fmt.run(frames, vinInput))}
                    disabled={pendingFormat !== null}
                    className={`flex shrink-0 items-center gap-1.5 rounded-[8px] px-3.5 py-1.5 text-xs font-medium transition-colors duration-100 ${
                      pendingFormat !== null
                        ? 'cursor-not-allowed border border-border text-text-low'
                        : 'border border-border text-text-body hover:border-border-strong hover:text-text-hi'
                    }`}
                  >
                    {isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Download className="h-3.5 w-3.5" />
                    )}
                    <span>İndir</span>
                  </button>
                </div>
              );
            })}
          </div>

          {lastExport && (
            <div className="mt-4 flex items-center gap-2 border-t border-border/60 pt-3.5 text-[11px] text-ok">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">
                {lastExport.format} indirildi · {lastExport.filename} · {(lastExport.sizeBytes / 1024).toFixed(1)} KB
              </span>
            </div>
          )}
        </div>

        {/* Right: cloud ingest + compliance */}
        <div className="lg:col-span-4 flex flex-col gap-4">
          <div className="rounded-[12px] glass-panel p-5">
            <div className="flex items-center gap-2">
              <CloudUpload className="h-4 w-4 text-text-low" />
              <span className="text-xs text-text-mid">Bulut arşivi</span>
            </div>

            <p className="mt-3 text-[11px] leading-relaxed text-text-low">
              Telemetri oturumu 5 MB parçalar halinde bulut arşivine aktarılır ve zaman serisi olarak işlenir.
            </p>

            <button
              onClick={handleUploadToCloud}
              disabled={cloudUploading}
              className={`mt-4 flex w-full items-center justify-center gap-1.5 rounded-[8px] px-4 py-2 text-xs font-medium transition-transform duration-100 active:scale-[0.98] ${
                cloudUploading
                  ? 'cursor-not-allowed bg-surface-inset text-text-low'
                  : 'bg-accent text-white hover:bg-accent/90'
              }`}
            >
              {cloudUploading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Cloud className="h-3.5 w-3.5" />
              )}
              <span>{cloudUploading ? 'Yükleniyor' : 'Oturumu yükle'}</span>
            </button>

            {uploadProgress && (
              <div className="mt-4 border-t border-border/60 pt-3">
                <div className="flex items-center justify-between font-mono text-[10px] text-text-low">
                  <span>{uploadProgress.status}</span>
                  <span>%{uploadProgress.percent.toFixed(0)}</span>
                </div>
                <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-border-strong">
                  <div
                    className="h-full rounded-full bg-accent transition-[width] duration-200"
                    style={{ width: `${Math.max(2, uploadProgress.percent)}%` }}
                  />
                </div>
              </div>
            )}

            {cloudUploadResult && (
              <div className="mt-4 border-t border-border/60 pt-3">
                <div className="flex items-center gap-1.5 text-[11px] text-ok">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  <span>Oturum yüklendi</span>
                </div>
                <div className="mt-1.5 font-mono text-[10px] text-text-low">
                  ID {cloudUploadResult.sessionId} · {cloudUploadResult.status}
                </div>
              </div>
            )}

            {cloudError && (
              <div className="mt-4 flex items-start gap-1.5 border-t border-border/60 pt-3 text-[11px] text-del">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{cloudError}</span>
              </div>
            )}
          </div>

          <div className="rounded-[12px] glass-panel p-5">
            <span className="text-xs text-text-mid">Standart uyumluluğu</span>
            <div className="mt-4 divide-y divide-border/60 border-y border-border/60">
              {[
                'ISO 14229 (UDS) · ISO 15765-2',
                'SAE J1939 · NMEA 2000',
                'SHA-256 oturum bütünlüğü',
              ].map((item) => (
                <div key={item} className="flex items-center justify-between py-2.5 text-xs">
                  <span className="text-text-mid">{item}</span>
                  <CheckCircle2 className="h-3.5 w-3.5 text-ok" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

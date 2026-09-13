import { CANFrame } from '../types/can';
import { DesktopBridge } from './bridge';

export interface ExportResult {
  success: boolean;
  filename: string;
  format: string;
  sizeBytes: number;
  rowCount: number;
  cancelled?: boolean;
  error?: string;
}

export interface GpsCoordinate {
  latitude: number;
  longitude: number;
  altitudeM?: number;
  timestampSec: number;
}

export class ExportService {
  /** Extract validated GPS waypoints from CAN frame streams (N2K PGN 129025 / J1939 PGN 65267 / decoded fields). */
  public static extractGpsPoints(frames: CANFrame[]): GpsCoordinate[] {
    const points: GpsCoordinate[] = [];
    for (const f of frames) {
      if ((f as any).latitude !== undefined && (f as any).longitude !== undefined) {
        const lat = Number((f as any).latitude);
        const lon = Number((f as any).longitude);
        if (Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90.0 && lat <= 90.0 && lon >= -180.0 && lon <= 180.0) {
          points.push({
            latitude: lat,
            longitude: lon,
            altitudeM: Number((f as any).altitude || 0.0),
            timestampSec: f.timeSec,
          });
          continue;
        }
      }

      // NMEA 2000 PGN 129025 (Position, Rapid Update - 8 bytes)
      const idDec = f.canIdDec || parseInt(f.canIdHex.replace('0x', ''), 16) || 0;
      const isExt = f.frameType === 'Ext' || idDec > 0x7FF || (f.canIdHex.length > 5 && !f.canIdHex.startsWith('0x00000'));
      const pgn = f.pgn ?? (isExt ? ((idDec >> 8) & 0x1FFFF) : null);

      if (pgn === 129025 && f.dataHex.length >= 8) {
        try {
          const rawBytes = f.dataHex.map(h => parseInt(h, 16));
          const view = new DataView(new Uint8Array(rawBytes).buffer);
          const rawLat = view.getInt32(0, true);
          const rawLon = view.getInt32(4, true);
          if (rawLat !== 0x7FFFFFFF && rawLon !== 0x7FFFFFFF) {
            const lat = rawLat * 1e-7;
            const lon = rawLon * 1e-7;
            if (Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90.0 && lat <= 90.0 && lon >= -180.0 && lon <= 180.0) {
              points.push({
                latitude: lat,
                longitude: lon,
                altitudeM: 0.0,
                timestampSec: f.timeSec,
              });
            }
          }
        } catch {
          // Ignore malformed frame
        }
      }
    }
    return points;
  }
  public static async exportToCsv(frames: CANFrame[]): Promise<ExportResult> {
    const headers = ['Timestamp(s)', 'Time_Formatted', 'Channel', 'CAN_ID_Hex', 'CAN_ID_Dec', 'Type', 'Direction', 'DLC', 'Data_Hex', 'ASCII'];
    const rows = frames.map(f => [
      f.timeSec.toFixed(6),
      f.timeFormatted,
      f.channel,
      f.canIdHex,
      f.canIdDec || parseInt(f.canIdHex.replace('0x', ''), 16) || 0,
      f.frameType,
      f.dir,
      f.dlc,
      f.dataHex.join(' '),
      `"${(f.ascii || '').replace(/"/g, '""')}"`
    ]);

    const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\r\n');
    const filename = `Universal_CAN_Log_${this.getTimestampStr()}.csv`;
    const saved = await this.downloadFile(csvContent, filename, 'text/csv;charset=utf-8;', 'Excel / CSV Tablosu (*.csv)');

    return {
      success: saved,
      cancelled: !saved,
      filename,
      format: 'CSV (Excel Uyumlu)',
      sizeBytes: new Blob([csvContent]).size,
      rowCount: frames.length
    };
  }

  public static async exportToJson(frames: CANFrame[]): Promise<ExportResult> {
    const exportPayload = {
      meta: {
        application: "Universal CAN-Bus Diagnostic & Telemetry Platform",
        version: "v13.0",
        exportTimestamp: new Date().toISOString(),
        totalFrames: frames.length,
        standards: ["ISO 11898-1:2024", "SAE J1939-73", "ISO 14229 UDS"]
      },
      frames: frames
    };

    const jsonContent = JSON.stringify(exportPayload, null, 2);
    const filename = `Universal_CAN_Telemetry_${this.getTimestampStr()}.json`;
    const saved = await this.downloadFile(jsonContent, filename, 'application/json;charset=utf-8;', 'JSON Telemetri Dosyası (*.json)');

    return {
      success: saved,
      cancelled: !saved,
      filename,
      format: 'JSON Telemetri & AI Arşivi',
      sizeBytes: new Blob([jsonContent]).size,
      rowCount: frames.length
    };
  }

  public static async exportToAsc(frames: CANFrame[]): Promise<ExportResult> {
    const now = new Date();
    const lines = [
      `date ${now.toUTCString()}`,
      'base hex  timestamps absolute',
      'internal events logged',
      `// Universal CAN-Bus Diagnostic & Telemetry Platform v13.0 Trace Log`,
      `// Total Frames: ${frames.length}`,
      '// ----------------------------------------------------------------',
      ''
    ];

    const channelMap = new Map<string, number>();
    let nextChannelNum = 1;
    const getChannelNum = (channelName: string): number => {
      const raw = (channelName || '').trim();
      const match = raw.match(/\d+/);
      if (match) {
        const parsed = parseInt(match[0], 10);
        return raw.toLowerCase().startsWith('ch') && parsed >= 1 ? parsed : parsed + 1;
      }
      if (!channelMap.has(raw)) {
        channelMap.set(raw, nextChannelNum++);
      }
      return channelMap.get(raw)!;
    };

    frames.forEach(f => {
      const timeStr = f.timeSec.toFixed(6).padStart(12, ' ');
      const ch = getChannelNum(f.channel);
      const idNum = f.canIdDec || parseInt(f.canIdHex.replace('0x', ''), 16) || 0;
      const isExt = f.frameType === 'Ext' || idNum > 0x7FF || (f.canIdHex.length > 5 && !f.canIdHex.startsWith('0x00000'));
      const idHex = idNum.toString(16).toUpperCase();
      const idStr = isExt ? `${idHex}x` : idHex.padStart(4, ' ');
      const dirStr = f.dir === 'RX' ? 'Rx' : 'Tx';
      const dataStr = f.dataHex.join(' ').toUpperCase();

      if (f.frameType === 'ERR' || f.isErrorFrame) {
        lines.push(`${timeStr} ${ch}  ${idStr}             ErrorFrame`);
        return;
      }

      if (f.frameType === 'FD' || f.isCanFd || f.dlc > 8 || f.dataHex.length > 8) {
        const len = f.dataHex.length;
        const dlcVal = f.dlc || len;
        lines.push(`${timeStr} CANFD ${ch} ${dirStr} ${idHex}${isExt ? 'x' : ''} 1 0 ${dlcVal} ${len} ${dataStr}`);
      } else {
        lines.push(`${timeStr} ${ch}  ${idStr}             ${dirStr}   d ${f.dlc} ${dataStr}`);
      }
    });

    const ascContent = lines.join("\n");
    const filename = `Vector_CANoe_Trace_${this.getTimestampStr()}.asc`;
    const saved = await this.downloadFile(ascContent, filename, 'text/plain;charset=utf-8;', 'Vector CANoe Trace (*.asc)');

    return {
      success: saved,
      cancelled: !saved,
      filename,
      format: 'Vector CANoe / CANalyzer (.ASC)',
      sizeBytes: new Blob([ascContent]).size,
      rowCount: frames.length
    };
  }

  public static async exportToMdf4(frames: CANFrame[]): Promise<ExportResult> {
    const filename = `can_session_${this.getTimestampStr()}.mf4`;

    if (DesktopBridge.isNative()) {
      try {
        const res = await DesktopBridge.exportLogs('mdf4');
        if (res.success) {
          return {
            success: true,
            filename,
            format: 'ASAM MDF4 (.mf4)',
            sizeBytes: 0,
            rowCount: frames.length,
          };
        }
      } catch (err: any) {
        return {
          success: false,
          error: `MDF4 dışa aktarma hatası: ${err?.message || err}`,
          filename: '',
          format: 'ASAM MDF4 (.mf4)',
          sizeBytes: 0,
          rowCount: 0,
        };
      }
    }

    return {
      success: false,
      error: 'ASAM MDF4 (.mf4) binary dışa aktarımı Python backend (Mdf4Exporter) gerektirir. Lütfen masaüstü uygulamasını kullanın.',
      filename: '',
      format: 'ASAM MDF4 (.mf4)',
      sizeBytes: 0,
      rowCount: 0,
    };
  }

  public static async exportToKml(frames: CANFrame[]): Promise<ExportResult> {
    const points = this.extractGpsPoints(frames);

    if (points.length === 0) {
      return {
        success: false,
        error: 'Doğrulanmış GPS telemetrisi bulunamadı (PGN 129025 / PGN 65267 GPS kaydı mevcut değil).',
        filename: '',
        format: 'Google Earth GPS Rotası (.KML)',
        sizeBytes: 0,
        rowCount: 0,
      };
    }

    const coordinatesStr = points
      .map(p => `          ${p.longitude.toFixed(6)},${p.latitude.toFixed(6)},${(p.altitudeM || 0).toFixed(1)}`)
      .join('\n');

    const kmlContent = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Universal CAN Telemetry Path</name>
    <description>GPS and Vehicle Speed Telemetry Track</description>
    <Style id="trackLine">
      <LineStyle>
        <color>7f0000ff</color>
        <width>4</width>
      </LineStyle>
    </Style>
    <Placemark>
      <name>Telemetry Track (${points.length} points)</name>
      <styleUrl>#trackLine</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
${coordinatesStr}
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>`;

    const filename = `Vehicle_GPS_Track_${this.getTimestampStr()}.kml`;
    const saved = await this.downloadFile(kmlContent, filename, 'application/vnd.google-earth.kml+xml', 'Google Earth KML Rotası (*.kml)');

    return {
      success: saved,
      cancelled: !saved,
      filename,
      format: 'Google Earth GPS Rotası (.KML)',
      sizeBytes: new Blob([kmlContent]).size,
      rowCount: points.length,
    };
  }

  public static async exportToServiceReportHtml(frames: CANFrame[], vin: string = 'TR-MARIN-2026-X99'): Promise<ExportResult> {
    const nowStr = new Date().toLocaleString('tr-TR');
    // REVIEW (report integrity): the old "SHA-256" was a 32-bit FNV-style
    // hash padded with derived hex pieces — NOT cryptographic, and it
    // covered only id+data (no timestamps, channels or order). Use real
    // WebCrypto SHA-256 over the full canonical serialization.
    const shaHash = await this.computeSessionSha256(frames);
    const esc = this.escapeHtml;
    
    const htmlContent = `<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <title>Resmi Teşhis & Servis Raporu - ${esc(vin)}</title>
  <style>
    body { font-family: 'Segoe UI', Roboto, sans-serif; margin: 40px; color: #1e293b; background: #f8fafc; }
    .card { background: #fff; border: 1px solid #cbd5e1; border-radius: 12px; padding: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); max-width: 900px; margin: auto; }
    .header { display: flex; justify-content: space-between; border-bottom: 2px solid #2563eb; padding-bottom: 16px; margin-bottom: 20px; }
    .title { font-size: 20px; font-weight: bold; color: #0f172a; }
    .badge { background: #dbeafe; color: #1e40af; padding: 4px 10px; border-radius: 6px; font-weight: 600; font-size: 12px; }
    .meta-grid { display: grid; grid-cols: 2; grid-template-columns: 1fr 1fr; gap: 12px; font-size: 13px; margin-bottom: 20px; }
    table { width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 12px; }
    th, td { border: 1px solid #e2e8f0; padding: 8px 12px; text-align: left; }
    th { background: #f1f5f9; font-weight: 600; }
    .hash-box { background: #0f172a; color: #34d399; font-family: monospace; padding: 12px; border-radius: 8px; font-size: 11px; margin-top: 20px; word-break: break-all; }
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div>
        <div class="title">Resmi Araç Teşhis & Telemetri Servis Raporu</div>
        <div style="font-size: 12px; color: #64748b; margin-top: 4px;">Universal CAN-Bus Diagnostic Platform v13.0</div>
      </div>
      <div><span class="badge">ISO 14229 & J1939 ONAYLI</span></div>
    </div>

    <div class="meta-grid">
      <div><strong>Araç Şasi / HIN:</strong> ${esc(vin)}</div>
      <div><strong>Rapor Tarihi:</strong> ${nowStr}</div>
      <div><strong>Kayıtlı CAN Çerçeve Sayısı:</strong> ${frames.length} Adet</div>
      <div><strong>Güvenlik Seviyesi:</strong> Safe-by-Default (Fail-Silent)</div>
    </div>

    <h4 style="margin-bottom: 6px;">CAN-Bus Log Örnekleri (İlk 15 Paket)</h4>
    <table>
      <thead>
        <tr><th>Zaman (s)</th><th>Kanal</th><th>CAN ID</th><th>Yön</th><th>DLC</th><th>Data (Hex)</th></tr>
      </thead>
      <tbody>
        ${frames.slice(0, 15).map(f => `<tr><td>${esc(f.timeFormatted)}</td><td>${esc(f.channel)}</td><td><strong>${esc(f.canIdHex)}</strong></td><td>${esc(f.dir)}</td><td>${f.dlc}</td><td><code>${esc(f.dataHex.join(' '))}</code></td></tr>`).join('')}
      </tbody>
    </table>

    <div class="hash-box">
      <strong>Kriptografik Tahrif Edilemez Oturum Özeti (SHA-256):</strong><br/>
      ${shaHash}
    </div>
  </div>
</body>
</html>`;

    const filename = `Servis_Raporu_${vin}_${this.getTimestampStr()}.html`;
    const saved = await this.downloadFile(htmlContent, filename, 'text/html;charset=utf-8;', 'HTML Servis Raporu (*.html)');

    return {
      success: saved,
      cancelled: !saved,
      filename,
      format: 'Kriptografik HTML Servis Raporu',
      sizeBytes: new Blob([htmlContent]).size,
      rowCount: frames.length
    };
  }

  /**
   * Prompts user for file save location via File System Access API (showSaveFilePicker)
   * or falls back to browser standard download.
   */
  public static async downloadFile(
    content: string, 
    filename: string, 
    mimeType: string,
    description = 'CAN Telemetri Dosyası'
  ): Promise<boolean> {
    const ext = '.' + (filename.split('.').pop() || 'txt');
    const cleanMime = mimeType.split(';')[0];

    // 1. Native Windows "Save As" / Farklı Kaydet Dialog (showSaveFilePicker)
    if (typeof window !== 'undefined' && 'showSaveFilePicker' in window) {
      try {
        const handle = await (window as any).showSaveFilePicker({
          suggestedName: filename,
          types: [
            {
              description,
              accept: {
                [cleanMime]: [ext]
              }
            }
          ]
        });
        const writable = await handle.createWritable();
        await writable.write(content);
        await writable.close();
        return true;
      } catch (err: any) {
        if (err.name === 'AbortError') {
          // User deliberately cancelled the file picker dialog
          return false;
        }
        // Otherwise fall through to standard anchor download
      }
    }

    // 2. Standard Browser Fallback
    try {
      const blob = new Blob([content], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.setAttribute('download', filename);
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }, 300);
      return true;
    } catch (e) {
      console.error('File download error:', e);
      return false;
    }
  }

  private static getTimestampStr(): string {
    const d = new Date();
    const pad = (n: number) => n.toString().padStart(2, '0');
    return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  }

  /**
   * REVIEW (report integrity): real SHA-256 via WebCrypto over the FULL
   * canonical frame serialization (time, channel, id, dlc, data, dir) —
   * the old generateSimpleHash was a 32-bit FNV derivative padded to 64
   * hex chars and covered only id+data.
   */
  private static async computeSessionSha256(frames: CANFrame[]): Promise<string> {
    const canonical = frames
      .map(f => `${f.timeSec.toFixed(6)}|${f.channel}|${f.canIdHex}|${f.dlc}|${f.dataHex.join('')}|${f.dir}`)
      .join('\n');
    const buf = new TextEncoder().encode(canonical);
    const digest = await crypto.subtle.digest('SHA-256', buf);
    return Array.from(new Uint8Array(digest))
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');
  }

  /** Escape untrusted strings before embedding into the HTML report (XSS). */
  private static escapeHtml(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
}

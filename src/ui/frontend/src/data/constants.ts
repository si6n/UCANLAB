/**
 * UCanLab v13.0 Domain Constants & Telemetry Mock Engine Data
 * Reference A structure + Reference B visual tokens mapping.
 */

export interface NavItem {
  id: string;
  label: string;
  icon: string;
  badge?: string;
}

export interface NavGroup {
  id: string;
  title: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'tehis',
    title: 'TEŞHİS',
    items: [
      { id: 'dashboard', label: 'CAN Dashboard', icon: 'LayoutDashboard' },
      { id: 'signal_discovery', label: 'Reverse Engineer', icon: 'Cpu' },
    ],
  },
  {
    id: 'araclar',
    title: 'ARAÇLAR',
    items: [
      { id: 'ecu_flashing', label: 'ECU Flashing', icon: 'Zap' },
      { id: 'pinout_guide', label: 'Pinout Rehberi', icon: 'GitBranch' },
      { id: 'reports', label: 'Rapor & Export', icon: 'FileText' },
    ],
  },
  {
    id: 'sistem',
    title: 'SİSTEM',
    items: [
      { id: 'settings', label: 'Ayarlar', icon: 'Settings' },
    ],
  },
];

export const DATA_SYNTAX_PALETTE = [
  '#22d3ee', // Cyan
  '#ec4899', // Pink
  '#eab308', // Yellow
  '#34d399', // Green
  '#60a5fa', // Blue
  '#a78bfa', // Purple
  '#fb923c', // Orange
  '#f472b6', // Light pink
] as const;

export interface CanPacketRow {
  id: string;
  timestamp: string; // e.g. "74.0751s"
  timeSec: number;
  channel: string;   // e.g. "vcan0"
  canId: string;     // e.g. "0x19F20000"
  frameType: 'Ext' | 'Std';
  direction: 'RX' | 'TX';
  dlc: number;
  dataBytes: string[]; // ['00', '1A', '3F', ...]
  ascii: string;
  isAnomaly: boolean;
  anomalyDescription?: string;
}

export const ANOMALY_IDS = new Set([
  '0x18FF0501',
  '0x0CF00400',
  '0x19F50200',
  '0x18FEF100',
  '0x18EAFFFE',
]);

export function formatAscii(bytes: string[]): string {
  return bytes
    .map((b) => {
      const charCode = parseInt(b, 16);
      return charCode >= 32 && charCode <= 126 ? String.fromCharCode(charCode) : '.';
    })
    .join('');
}

export const INITIAL_PACKET_ROWS: CanPacketRow[] = [
  {
    id: 'pkt-01',
    timestamp: '74.0751s',
    timeSec: 74.0751,
    channel: 'vcan0',
    canId: '0x18FF0501',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['01', '5F', '00', 'E4', 'FF', '8A', '00', '12'],
    ascii: '._.._...',
    isAnomaly: true,
    anomalyDescription: 'ECM DTC Bildirimi (SPN 190 FMI 2 - Tekleme Çentiği)',
  },
  {
    id: 'pkt-02',
    timestamp: '74.0720s',
    timeSec: 74.0720,
    channel: 'vcan0',
    canId: '0x0CF00400',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['F0', '2D', '80', '1B', '44', '00', 'FF', 'FF'],
    ascii: '.-..D...',
    isAnomaly: true,
    anomalyDescription: 'EEC1 Motor Hızı Ani Düşüş (-575 RPM dip)',
  },
  {
    id: 'pkt-03',
    timestamp: '74.0688s',
    timeSec: 74.0688,
    channel: 'vcan0',
    canId: '0x18FEF200',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['32', '64', '80', 'A5', '00', '21', '55', '3F'],
    ascii: '2d...vU?',
    isAnomaly: false,
  },
  {
    id: 'pkt-04',
    timestamp: '74.0652s',
    timeSec: 74.0652,
    channel: 'vcan0',
    canId: '0x19F50200',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['FF', 'FF', '00', '10', '7E', '80', 'C2', '03'],
    ascii: '....~...',
    isAnomaly: true,
    anomalyDescription: 'BMS Aşırı Gerilim Uyarısı (Hücre #4 Dengesizliği)',
  },
  {
    id: 'pkt-05',
    timestamp: '74.0610s',
    timeSec: 74.0610,
    channel: 'vcan0',
    canId: '0x18FEF100',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['FF', '55', '00', '4B', '73', '82', 'A1', '0F'],
    ascii: '.U.Ks...',
    isAnomaly: true,
    anomalyDescription: 'CCVS1 Hız Sensörü Gürültüsü (Sinyal Atlaması)',
  },
  {
    id: 'pkt-06',
    timestamp: '74.0575s',
    timeSec: 74.0575,
    channel: 'vcan0',
    canId: '0x19F20000',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['1A', '2B', '3C', '4D', '5E', '6F', '70', '81'],
    ascii: '.+<M^op.',
    isAnomaly: false,
  },
  {
    id: 'pkt-07',
    timestamp: '74.0540s',
    timeSec: 74.0540,
    channel: 'vcan0',
    canId: '0x18EAFFFE',
    frameType: 'Ext',
    direction: 'TX',
    dlc: 3,
    dataBytes: ['00', 'EE', '00'],
    ascii: '...',
    isAnomaly: true,
    anomalyDescription: 'Adres İsteme Çakışması (PGN 59904 ACK Reddi)',
  },
  {
    id: 'pkt-08',
    timestamp: '74.0505s',
    timeSec: 74.0505,
    channel: 'vcan0',
    canId: '0x18FECA00',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['00', 'FF', '00', '00', '00', '00', 'FF', 'FF'],
    ascii: '........',
    isAnomaly: false,
  },
  {
    id: 'pkt-09',
    timestamp: '74.0470s',
    timeSec: 74.0470,
    channel: 'vcan0',
    canId: '0x0CF00300',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['4C', '7D', '80', '00', '1E', '32', 'FF', '2A'],
    ascii: 'L}....*..',
    isAnomaly: false,
  },
  {
    id: 'pkt-10',
    timestamp: '74.0435s',
    timeSec: 74.0435,
    channel: 'vcan0',
    canId: '0x7DF',
    frameType: 'Std',
    direction: 'TX',
    dlc: 8,
    dataBytes: ['02', '01', '0C', '55', '55', '55', '55', '55'],
    ascii: '...UUUUU',
    isAnomaly: false,
  },
  {
    id: 'pkt-11',
    timestamp: '74.0400s',
    timeSec: 74.0400,
    channel: 'vcan0',
    canId: '0x7E8',
    frameType: 'Std',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['04', '41', '0C', '1A', 'F8', 'AA', 'AA', 'AA'],
    ascii: '.A......',
    isAnomaly: false,
  },
  {
    id: 'pkt-12',
    timestamp: '74.0360s',
    timeSec: 74.0360,
    channel: 'vcan0',
    canId: '0x18FEEE00',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['64', '58', '80', 'A2', '9B', '20', '45', '71'],
    ascii: 'dX... Eq',
    isAnomaly: false,
  },
  {
    id: 'pkt-13',
    timestamp: '74.0325s',
    timeSec: 74.0325,
    channel: 'vcan0',
    canId: '0x18FEF500',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['12', '34', '56', '78', '9A', 'BC', 'DE', 'F0'],
    ascii: '.4Vx....',
    isAnomaly: false,
  },
  {
    id: 'pkt-14',
    timestamp: '74.0290s',
    timeSec: 74.0290,
    channel: 'vcan0',
    canId: '0x18FEF600',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['78', '56', '34', '12', 'FE', 'DC', 'BA', '98'],
    ascii: 'xV4.....',
    isAnomaly: false,
  },
  {
    id: 'pkt-15',
    timestamp: '74.0250s',
    timeSec: 74.0250,
    channel: 'vcan0',
    canId: '0x18FF3001',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['80', '80', '80', '80', '00', '00', '1A', '2B'],
    ascii: '......+.',
    isAnomaly: false,
  },
  {
    id: 'pkt-16',
    timestamp: '74.0215s',
    timeSec: 74.0215,
    channel: 'vcan0',
    canId: '0x18FF3101',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['01', '02', '03', '04', '05', '06', '07', '08'],
    ascii: '........',
    isAnomaly: false,
  },
  {
    id: 'pkt-17',
    timestamp: '74.0180s',
    timeSec: 74.0180,
    channel: 'vcan0',
    canId: '0x18FF3201',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['AA', '55', 'AA', '55', 'AA', '55', 'AA', '55'],
    ascii: '.U.U.U.U',
    isAnomaly: false,
  },
  {
    id: 'pkt-18',
    timestamp: '74.0145s',
    timeSec: 74.0145,
    channel: 'vcan0',
    canId: '0x0CF00203',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['30', '40', '50', '60', '70', '80', '90', 'A0'],
    ascii: '0@P`p...',
    isAnomaly: false,
  },
  {
    id: 'pkt-19',
    timestamp: '74.0110s',
    timeSec: 74.0110,
    channel: 'vcan0',
    canId: '0x18FEFC00',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['0A', '1B', '2C', '3D', '4E', '5F', '60', '71'],
    ascii: '..,=N_`q',
    isAnomaly: false,
  },
  {
    id: 'pkt-20',
    timestamp: '74.0075s',
    timeSec: 74.0075,
    channel: 'vcan0',
    canId: '0x18FEFD00',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['FF', '00', 'FF', '00', '12', '34', '56', '78'],
    ascii: '....4Vx.',
    isAnomaly: false,
  },
  {
    id: 'pkt-21',
    timestamp: '74.0040s',
    timeSec: 74.0040,
    channel: 'vcan0',
    canId: '0x201',
    frameType: 'Std',
    direction: 'TX',
    dlc: 8,
    dataBytes: ['00', '00', '01', 'E0', '02', '14', '00', '00'],
    ascii: '........',
    isAnomaly: false,
  },
  {
    id: 'pkt-22',
    timestamp: '74.0005s',
    timeSec: 74.0005,
    channel: 'vcan0',
    canId: '0x202',
    frameType: 'Std',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['05', '1F', '2A', '3C', '4D', '5E', '6F', '70'],
    ascii: '..*<M^op',
    isAnomaly: false,
  },
  {
    id: 'pkt-23',
    timestamp: '73.9970s',
    timeSec: 73.9970,
    channel: 'vcan0',
    canId: '0x18FEAE00',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['44', '33', '22', '11', '88', '77', '66', '55'],
    ascii: 'D3".wqfU',
    isAnomaly: false,
  },
  {
    id: 'pkt-24',
    timestamp: '73.9935s',
    timeSec: 73.9935,
    channel: 'vcan0',
    canId: '0x18FEAF00',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['11', '22', '33', '44', '55', '66', '77', '88'],
    ascii: '."3DUfw.',
    isAnomaly: false,
  },
  {
    id: 'pkt-25',
    timestamp: '73.9900s',
    timeSec: 73.9900,
    channel: 'vcan0',
    canId: '0x18FEB000',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['AA', 'BB', 'CC', 'DD', 'EE', 'FF', '00', '11'],
    ascii: '........',
    isAnomaly: false,
  },
  {
    id: 'pkt-26',
    timestamp: '73.9865s',
    timeSec: 73.9865,
    channel: 'vcan0',
    canId: '0x18FEB100',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['FF', 'EE', 'DD', 'CC', 'BB', 'AA', '99', '88'],
    ascii: '........',
    isAnomaly: false,
  },
  {
    id: 'pkt-27',
    timestamp: '73.9830s',
    timeSec: 73.9830,
    channel: 'vcan0',
    canId: '0x18FEB200',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['55', 'AA', '55', 'AA', '01', '02', '03', '04'],
    ascii: 'U.U.....',
    isAnomaly: false,
  },
  {
    id: 'pkt-28',
    timestamp: '73.9795s',
    timeSec: 73.9795,
    channel: 'vcan0',
    canId: '0x18FEB300',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['10', '20', '30', '40', '50', '60', '70', '80'],
    ascii: '. 0@P`p.',
    isAnomaly: false,
  },
  {
    id: 'pkt-29',
    timestamp: '73.9760s',
    timeSec: 73.9760,
    channel: 'vcan0',
    canId: '0x18FEB400',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['01', '10', '02', '20', '03', '30', '04', '40'],
    ascii: '... .0.@',
    isAnomaly: false,
  },
  {
    id: 'pkt-30',
    timestamp: '73.9725s',
    timeSec: 73.9725,
    channel: 'vcan0',
    canId: '0x18FEB500',
    frameType: 'Ext',
    direction: 'RX',
    dlc: 8,
    dataBytes: ['99', '88', '77', '66', '55', '44', '33', '22'],
    ascii: '..wfUD3"',
    isAnomaly: false,
  },
];

export interface ScopeSeries {
  id: string;
  name: string;
  value: number;
  unit: string;
  color: string; // Tailwind hex
  dashPattern?: number[];
  range: [number, number];
}

export const INITIAL_SCOPE_SERIES: ScopeSeries[] = [
  {
    id: 'hv',
    name: 'HV',
    value: 398.4,
    unit: 'V',
    color: '#1d4ed8', // Zeron Light extracted blue (primary series)
    range: [350, 450],
  },
  {
    id: 'soc',
    name: 'SOC',
    value: 78.4,
    unit: '%',
    color: '#16a34a', // Zeron Light extracted green (secondary, muted via dash)
    dashPattern: [4, 3],
    range: [0, 100],
  },
  {
    id: 'current',
    name: 'Akım',
    value: 42.5,
    unit: 'A',
    color: '#62626a', // Zeron Light extracted grey
    range: [-100, 150],
  },
];

export const ALERT_BANNER_DATA = {
  title: 'RPM Ani Düşüşü: -575 RPM (Tekleme Çentiği!)',
  actionLabel: 'AI Analiz',
  severity: 'danger',
  markerTimeSec: 74.0720,
};

/**
 * Procedural waveform generator that creates captured-looking signals
 * with periodic trapezoidal/clipped peaks, realistic sensor noise, and a distinct dip.
 */
export function generateProceduralWaveformData(
  timePoints = 120,
  baseTime = 74.0,
  hasDip = true
): { time: number; primary: number; secondary: number }[] {
  const points = [];
  const dt = 0.01; // 10ms step

  for (let i = 0; i < timePoints; i++) {
    const t = baseTime - (timePoints - i) * dt;
    const cycle = (i % 24) / 24;

    // Primary: periodic trapezoidal clipped waveform with slight noise
    let primary = 398.0;
    if (cycle < 0.4) {
      primary += Math.min(18.0, cycle * 55.0);
    } else if (cycle < 0.7) {
      primary += 18.0; // Flat clipped top
    } else {
      primary += Math.max(0, 18.0 - (cycle - 0.7) * 60.0);
    }

    // Secondary: battery/soc slight sawtooth modulation
    let secondary = 78.2 + Math.sin(i * 0.15) * 1.2;

    // Introduce sharp dip at ~75% along the buffer (corresponding to the anomaly event)
    if (hasDip && i >= 82 && i <= 88) {
      const dipDepth = Math.sin(((i - 82) / 6) * Math.PI) * 24.0;
      primary -= dipDepth;
      secondary -= dipDepth * 0.15;
    }

    // Add slight realistic quantization jitter
    primary += (Math.random() - 0.5) * 0.4;
    secondary += (Math.random() - 0.5) * 0.1;

    points.push({
      time: t,
      primary,
      secondary,
    });
  }

  return points;
}

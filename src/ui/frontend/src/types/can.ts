export type FrameDirection = 'RX' | 'TX';
export type FrameType = 'Std' | 'Ext' | 'FD' | 'ERR';

export interface CANFrame {
  id: string;
  timeSec: number;
  timeFormatted: string;
  channel: 'vcan0' | 'can0' | 'can1' | string;
  canIdHex: string;
  canIdDec: number;
  pgn?: number;
  frameType: FrameType;
  dir: FrameDirection;
  dlc: number;
  dataHex: string[];
  ascii: string;
  isErrorFrame?: boolean;
  colorPalette?: ('blue' | 'indigo' | 'emerald' | 'slate' | 'amber' | 'rose')[];
  signalName?: string;
  ecuName?: string;
  isCanFd?: boolean;
}

export interface TelemetryPoint {
  timeSec: number;
  timeFormatted: string;
  rpm: number;
  turboBoostBar: number;
  coolantTempC: number;
  oilPressureBar: number;
  busLoadPercent: number;
  errorCount: number;

  // EV / BMS Telemetry Fields
  batterySocPercent?: number;
  packVoltageV?: number;
  packCurrentA?: number;
  cellMinMaxDeltaV?: number;
  inverterTempC?: number;

  // Marine N2K Fields
  sogKnots?: number;
  depthMeters?: number;
  rudderDeg?: number;
  propellerSlipPct?: number;

  // Derived Virtual Channels
  torqueNm?: number;
  powerKw?: number;
  powerHp?: number;
  instantFuelRateLph?: number;
  instantFuelEconomyL100km?: number;
  gearPosition?: number | string;
}

export type ScenarioType = 
  | 'nominal' 
  | 'misfire_p0300' 
  | 'overboost' 
  | 'overheat' 
  | 'bus_surge'
  | 'ev_bms_telemetry'
  | 'marine_vessel_n2k'
  | 'j1939_multi_ecu_fleet'
  | 'can_fd_adas_vision'
  | 'intermittent_wiring_fault';

export type FaultInjectionType = 
  | 'error_frame'
  | 'dtc_fault'
  | 'sensor_freeze'
  | 'babbling_surge'
  | 'wiring_dropout';

export interface DtcInfo {
  code: string;
  title: string;
  ecu: string;
  severity: 'warning' | 'critical';
  rootCauses: string[];
  recommendedActions: string[];
  oscilloscopeNote: string;
}

export interface CopilotAction {
  id: string;
  label: string;
  action_type: string;
  params?: Record<string, any>;
  requires_confirmation?: boolean;
  confirm_text?: string;
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'copilot';
  timestamp: string;
  text: string;
  isDtcCard?: boolean;
  dtcInfo?: DtcInfo;
  actions?: CopilotAction[];
}

export type ActiveTab =
  | 'dashboard'
  | 'signal_discovery'
  | 'ecu_flashing'
  | 'pinout_guide'
  | 'reports';

export interface DiagnosticState {
  healthStatus: 'standby' | 'nominal' | 'warning' | 'critical';
  dtcCount: number;
  activeDtcs: DtcInfo[];
  lastScanTimestamp: string;
  liveAnalysisTitle: string;
  liveAnalysisSummary: string;
  currentTelemetry: {
    rpm: number;
    coolantTemp: number;
    turboPressure: number;
    responseLatencyMs: number;
    packVoltage?: number;
    batterySoc?: number;
    sogKnots?: number;
    depthMeters?: number;
  };
  recommendedActions: {
    id: string;
    text: string;
    completed: boolean;
  }[];
}



export interface CloudLicenseInfo {
  licenseId: string;
  tier: string;
  features: string[];
  expiresAt: number;
  offlineUntil: number;
  issuedAt?: number;
}

export interface CloudUser {
  id?: string;
  email?: string;
  name?: string;
  organization?: string;
}

export interface CloudSubscription {
  isActive: boolean;
  tier: string;
  expiresAt?: number | null;
  features?: string[];
}

export interface CloudStatus {
  success: boolean;
  baseUrl: string;
  hasSessionToken: boolean;
  hasDeviceToken: boolean;
  hwid: string;
  user?: CloudUser | null;
  subscription?: CloudSubscription | null;
  license?: CloudLicenseInfo | null;
  error?: string;
}

// Mechanic flow sign-in (Aşama 3) — mirrors src/security/cloud/desktop_auth.py
export interface AuthEntitlements {
  tier: string;
  mechanic: boolean;
  engineer: boolean;
  active_tests: boolean;
  dtc_clear: boolean;
}

export interface AuthLicenseState {
  status: 'active' | 'missing' | 'expired' | 'clock_problem' | 'invalid';
  entitlements: AuthEntitlements;
  offline_seconds_left: number;
  offline_days_left: number;
  error_code: string | null;
  message_tr: string;
  message_en: string;
}

export interface AuthState {
  success: boolean;
  signedIn: boolean;
  loginRequired: boolean;
  license: AuthLicenseState;
  refreshed?: boolean;
}

export interface AuthLoginOutcome {
  status: 'ready' | 'no_license' | 'error';
  license: AuthLicenseState;
  error_code: string | null;
  message_tr: string;
  message_en: string;
}

export type MechanicMode = 'mechanic' | 'engineer';

export interface MechanicState {
  success: boolean;
  mode: MechanicMode | null;
  vehicle_profile_id: string | null;
  entitlements: AuthEntitlements;
}

export interface VehicleTypeInfo {
  id: string;
  label_tr: string;
  label_en: string;
  sub_tr: string;
  sub_en: string;
  protocol: string;
  socket: string;
  bitrate_candidates: number[];
  plug_tr: string;
  plug_en: string;
  passive_note_tr: string;
  passive_note_en: string;
}

export interface VehicleProfileInfo {
  id: string;
  type: string;
  make: string;
  label_tr: string;
  label_en: string;
  coverage: 'enriched' | 'standard' | 'unsupported';
  selectable: boolean;
  high_voltage: boolean;
  has_oem_decoder: boolean;
  dbc_file_count: number;
  note_tr: string;
  note_en: string;
}

export interface VehicleCatalogResult {
  success: boolean;
  types?: VehicleTypeInfo[];
  profiles?: VehicleProfileInfo[];
  error_code?: string;
}

export interface MechanicResult {
  success: boolean;
  error_code?: string;
  message_tr?: string;
  message_en?: string;
  mode?: MechanicMode;
  profile?: VehicleProfileInfo;
  type?: VehicleTypeInfo | null;
}

export interface VehicleIdentityResult {
  success: boolean;
  error_code?: string;
  status?: 'match' | 'mismatch' | 'unknown';
  source?: 'vin' | 'j1939_name' | null;
  detected_tr?: string;
  detected_en?: string;
  suggested_profile_id?: string | null;
}

export interface AdapterEntry {
  id: string;
  kind: 'pcan' | 'kvaser' | 'rp1210' | 'socketcan' | 'simulator' | string;
  interface: string;
  channel: string;
  label: string;
  status: 'ready' | 'driver_ready' | 'driver_missing' | 'not_found' | 'probe_failed';
  usable: boolean;
  message_tr: string;
  message_en: string;
}

export interface ConnectionTestResult {
  code:
    | 'READY'
    | 'QUIET_VEHICLE'
    | 'EXPECTED_MISSING'
    | 'NO_TRAFFIC'
    | 'WRONG_BITRATE'
    | 'BUS_ERROR'
    | 'ADAPTER_ERROR'
    | 'LISTEN_ONLY_UNAVAILABLE'
    | 'CANCELLED';
  usable: boolean;
  bitrate: number | null;
  ecu_count: number;
  frames: number;
  expected: { pgn: number; name_tr: string; name_en: string; seen: boolean }[];
  message_tr: string;
  message_en: string;
  battery_volts: number | null;
  battery_warning: boolean;
  battery_message_tr: string;
  battery_message_en: string;
}

export interface ConnectionTestStatus {
  success: boolean;
  state?: 'idle' | 'running' | 'done';
  step?: 'opening' | 'bitrate' | 'ecus' | 'done';
  bitrate?: number | null;
  result?: ConnectionTestResult | null;
  error_code?: string;
}

export interface MechanicScanResult {
  safety_tr: string[];
  headline_tr: string;
  summary_tr: string;
  risk_level: 'RED' | 'YELLOW' | 'GREEN' | 'GRAY';
  urgency_tr: string;
  urgency_en: string;
  advice_tr: string;
  causes: { text_tr: string; why_tr: string; source_tr: string }[];
  steps: { n: string; action_tr: string; component_tr: string; difficulty_tr: string }[];
  missing_tr: string[];
  technical: {
    codes: { code: string; kind: string; ecu_tr: string; title_tr: string; severity: string; known: boolean }[];
    severity?: string | null;
    subsystems: string[];
    confidence_tr: string;
    source_notes: string[];
  };
  sources_tr: string[];
  glossary: { term: string; meaning_tr: string }[];
  simulator: boolean;
  vehicle_label: string;
}

export interface ScanStatus {
  success: boolean;
  state?: 'idle' | 'running' | 'done';
  step?: 'listening' | 'reading' | 'analyzing' | 'done' | 'cancelled' | 'failed';
  progress?: number;
  result?: MechanicScanResult | null;
  report_text?: string | null;
  error_code?: string;
}

export interface DeviceLoginStart {
  success: boolean;
  user_code?: string;
  verification_uri?: string;
  verification_uri_complete?: string;
  expires_in?: number;
  interval?: number;
  error_code?: string;
  message_tr?: string;
  message_en?: string;
}

export interface DeviceLoginPoll {
  success: boolean;
  status: 'idle' | 'pending' | 'completed' | 'denied' | 'expired' | 'failed';
  interval?: number;
  login?: AuthLoginOutcome | null;
  error_code?: string;
  message_tr?: string;
  message_en?: string;
}

export interface CloudUploadProgress {
  sessionId?: string;
  totalChunks: number;
  uploadedChunks: number;
  bytesSent: number;
  totalBytes: number;
  percent: number;
  status: string; // idle | uploading | processing | ready | failed
  error?: string;
}

// T2-7: vendored third-party data licence + attribution payload. Backed by the
// read-only bridge method `get_data_attributions()`, whose single source of
// truth is `src/ui/data_attribution_catalog.py`. Nothing here is duplicated in
// TypeScript on purpose — a second copy could silently drift from the vendored
// `data/licenses/*` files. The `attributionText` field is the legally-required
// credit verbatim (e.g. the SITRAK CC-BY-4.0 `МегаДата / megadata.pro` line).
export interface DataSourceAttribution {
  id: string;
  name: string;
  /** SPDX id, e.g. "CC-BY-4.0" / "Apache-2.0" / "MIT" / "CC0-1.0". */
  license: string;
  licenseName: string;
  licenseUrl: string;
  sourceUrl: string;
  /** Pinned upstream commit SHA; empty string when the source has none. */
  pinnedCommit: string;
  /** Required (or recorded) attribution text, verbatim from the vendored file. */
  attributionText: string;
  /** "required" when the licence legally obliges us to display the credit. */
  obligation: 'required' | 'recorded';
  /** Vendored file that is the canonical proof for this entry. */
  canonicalFile: string;
  /** Same as canonicalFile; kept for an explicit "read from here" call site. */
  sourcePath: string;
  /** Verbatim text of the canonical licence file (may be empty if unreadable). */
  sourceText: string;
  sourceTextTruncated?: boolean;
  /** Turkish explanation of what the obligation means. */
  noteTr: string;
  /** Data artefacts covered by this attribution. */
  artifacts: string[];
}

export interface DataAttributionsPayload {
  success: boolean;
  schemaVersion: number;
  obligationRequiredCount: number;
  sources: DataSourceAttribution[];
  /** Canonical files that could not be read — a compliance gap, surfaced loudly. */
  errors: string[];
  /** Runtime guarantee: this surface is fully offline. */
  offline: boolean;
  networkAccess: boolean;
  error?: string;
}

// User decision-card payload shape (doküman §46) — deterministic card
// composed in Python; TS renders only. Card carries risk band + headline
// + summary + source only (budama planı 2026-09-12).
export interface UserDiagnosticCard {
  card_version: number;
  headline_tr: string;
  summary_tr: string;
  risk_level: 'RED' | 'YELLOW' | 'GREEN' | 'GRAY';
  risk_advice_tr: string;
  evidence_tr: string[];
  technical: {
    dtcs: string[];
    severity: string;
    subsystem: string;
    confidence_score: number | null;
    // T67-G: additive, optional. The Python composer fills them with every
    // competing hypothesis and every DB cause it already ranked; a card from
    // an older backend simply omits them.
    alternatives_tr?: string[];
    confidence_label?: string;
  };
  source_badges: string[];
}

/**
 * Copilot upgrade: six-section structured answer (Python `copilot_answer.py`).
 * Every claim carries `refs` ("source#key"); TS renders, never re-derives.
 */
export interface CopilotEvidence {
  text: string;
  ref: string;
}

export interface CopilotStructuredAnswer {
  language: 'tr' | 'en';
  safety_banners: Array<{ category: string; text: string }>;
  summary: string;
  urgency: {
    level: 'RED' | 'YELLOW' | 'GREEN' | 'GRAY';
    color: string;
    label: string;
    advice: string[];
    reasons: CopilotEvidence[];
  };
  causes: Array<{
    rank: number;
    id: string;
    title: string;
    likelihood: number;
    confidence: 'high' | 'medium' | 'low';
    confidence_label: string;
    /** graph | record | suspected | area — `area` rows carry no percentage. */
    kind: string;
    kind_label: string;
    support: CopilotEvidence[];
    against: CopilotEvidence[];
    measure_to_confirm: string[];
    refs: string[];
  }>;
  steps: Array<{ n: number; text: string; difficulty: string; refs: string[] }>;
  missing_data: Array<{ key: string; what: string; how: string; refs: string[] }>;
  technical: {
    codes: Array<{
      key: string;
      found: boolean;
      title: string;
      fmi_text: string;
      pgn_text: string;
      severity: string;
      reference_values: string;
      oem_text: string;
      refs: string[];
    }>;
    telemetry: Array<{ signal: string; value: number; unit: string; status_text: string; reference: string; ref: string }>;
    glossary: Array<{ term: string; text: string; ref: string }>;
    similar_records: Array<{ title: string; ref: string }>;
    sources: string[];
    /** Operating state the readings were judged in (engine / thermal / system voltage). */
    state?: { engine: string; thermal: string; system_voltage: number | null; text: string; sources: string[] };
  };
  recalls: { note?: string; items?: Array<{ campaign: string; component: string; ref: string }>; complaints?: CopilotEvidence | null };
  /** Answerable questions; an answer is sent back with the same query and narrows the causes. */
  checks?: CopilotCheck[];
  markdown?: string;
}

export type CopilotAnswerValue = 'yes' | 'no' | 'unknown' | number;

export interface CopilotCheck {
  key: string;
  symptom_id: string;
  question: string;
  kind: 'yes_no' | 'measurement';
  unit: string;
  answer: CopilotAnswerValue | null;
  answer_text: string;
  result: string;
  refs: string[];
}

/** What the app is listening to (workbench status bar). */
export interface BusInfoResult {
  success: boolean;
  error_code?: string;
  interface?: string;
  channel?: string;
  bitrate?: number;
  connected?: boolean;
  simulated?: boolean;
  vehicle_type?: string | null;
  listen_only?: boolean;
  /** Driver-reported error frames on this channel. */
  error_frames?: number;
  /** Driver bus state: active / passive / bus_off / error / disconnected / stopped. */
  bus_state?: string;
}

/** A file the app wrote (workbench Kayıtlar); addressed by an opaque id, never a path. */
export interface RecordEntry {
  id: string;
  name: string;
  folder: 'exports' | 'logs' | 'traces' | 'reports';
  kind: 'trace' | 'report' | 'dbc' | 'log' | 'other';
  size: number;
  /** Unix seconds. */
  modified: number;
  replayable: boolean;
  uploadable: boolean;
}

export interface ReplayStatus {
  success: boolean;
  error_code?: string;
  running?: boolean;
  name?: string | null;
  position?: number;
  frame_count?: number;
  /** Frames the replay safety filter dropped (address claim, DTC clear, transport). */
  filtered?: number;
}

/** One decoded signal Python can plot (workbench Grafik). */
export interface PlotSignalInfo {
  name: string;
  unit: string;
  simulated: boolean;
  /** live = the vehicle now; simulator; replay = a recording playing. */
  origin?: 'live' | 'simulator' | 'replay';
  confidence: number;
  count: number;
  last: number;
  last_age_s: number;
}

/** Points of one signal; `t` is seconds relative to now (<= 0). */
export interface PlotSeries {
  unit: string;
  simulated: boolean;
  origin?: 'live' | 'simulator' | 'replay';
  t: number[];
  v: number[];
}

/** One stream in the Python discovery engine (channel + frame format + id). */
export interface DiscoveryStream {
  key: string;
  channel: string;
  extended: boolean;
  arbitration_id: number;
  frames: number;
  analyzable: boolean;
  simulated: boolean;
  replay: boolean;
}

export interface DiscoveryHypothesis {
  type: 'COUNTER' | 'CHECKSUM' | 'SIGNAL' | 'CONSTANT' | string;
  start_bit: number;
  length: number;
  little_endian: boolean;
  confidence: number;
  status: 'candidate' | 'approved' | 'rejected' | string;
  evidence: string[];
}

export interface DiscoveryReport {
  success: boolean;
  error_code?: string;
  key?: string;
  frames?: number;
  rate_hz?: number;
  dlc?: number;
  analyzable?: boolean;
  simulated?: boolean;
  entropy?: number[];
  bit_classes?: string[];
  hypotheses?: DiscoveryHypothesis[];
}

export interface StimulusStatus {
  success?: boolean;
  error_code?: string;
  running: boolean;
  phase?: 'rest' | 'active';
  switches?: number;
  rest_frames?: number;
  active_frames?: number;
}

export interface StimulusCandidate {
  key: string;
  arbitration_id: number;
  extended: boolean;
  kind: 'byte' | 'bit';
  index: number;
  rest: number;
  active: number;
  score: number;
  rest_frames: number;
  active_frames: number;
  simulated: boolean;
}

export interface FlashPreconditions {
  success: boolean;
  simulated: boolean;
  estop: boolean;
  speed_state: 'ok' | 'moving' | 'stale';
  speed_kmh: number | null;
  native_confirmation_required: boolean;
  flash_status: string | null;
}

// Interface for pywebview Python backend bridge
declare global {
  interface Window {
    pywebview?: {
      api: {
        trigger_estop: () => Promise<void>;
        heartbeat: () => Promise<boolean>;
        execute_diagnostic_action?: (action: Record<string, unknown>, confirmationToken?: string, userConfirmed?: boolean) => Promise<{ success: boolean; message?: string; error?: string; [key: string]: unknown }>;
        request_diagnostic_challenge?: (action: Record<string, unknown>) => Promise<{ success: boolean; token?: string; error?: string; [key: string]: unknown }>;
        get_safety_state?: () => Promise<string>;
        sim_vehicle_start?: (vehicleType: string) => Promise<BusInfoResult>;
        sim_vehicle_stop?: () => Promise<BusInfoResult>;
        bus_get_info?: () => Promise<BusInfoResult>;
        flash_preconditions?: () => Promise<FlashPreconditions>;
        workbench_connection_test_start?: (adapterId: string, vehicleType: string) => Promise<{ success: boolean; test_id?: string; error_code?: string }>;
        workbench_bus_connect?: (adapterId: string, bitrate: number) => Promise<BusInfoResult>;
        records_list?: () => Promise<{ success: boolean; records: RecordEntry[]; truncated: boolean }>;
        records_replay_start?: (recordId: string, speed: number) => Promise<ReplayStatus>;
        records_replay_stop?: () => Promise<ReplayStatus>;
        records_replay_status?: () => Promise<ReplayStatus>;
        records_open_folder?: () => Promise<{ success: boolean; error_code?: string }>;
        records_upload?: (recordId: string) => Promise<{ success: boolean; error_code?: string; session_id?: string; status?: string }>;
        stimulus_start?: () => Promise<StimulusStatus>;
        stimulus_set_phase?: (phase: 'rest' | 'active') => Promise<StimulusStatus>;
        stimulus_status?: () => Promise<StimulusStatus>;
        stimulus_result?: () => Promise<StimulusStatus & { candidates?: StimulusCandidate[] }>;
        stimulus_stop?: () => Promise<{ success: boolean }>;
        sim_vehicle_pedal?: (pressed: boolean) => Promise<{ success: boolean; error_code?: string }>;
        discovery_list?: () => Promise<{ success: boolean; min_frames: number; streams: DiscoveryStream[] }>;
        discovery_report?: (key: string) => Promise<DiscoveryReport>;
        discovery_set_approval?: (key: string, startBit: number, length: number, approved: boolean) => Promise<{ success: boolean; error_code?: string }>;
        discovery_save_dbc?: (approvedOnly: boolean) => Promise<{ success: boolean; error_code?: string; path?: string; messages?: number; signals?: number; simulated?: boolean }>;
        plot_signal_list?: () => Promise<{ success: boolean; signals: PlotSignalInfo[] }>;
        plot_signal_series?: (names: string[], windowS: number) => Promise<{ success: boolean; window_s?: number; series: Record<string, PlotSeries>; error_code?: string }>;
        export_logs: (format: string) => Promise<boolean>;
        // E-Stop Cryptographic Challenge / Multi-Operator APIs
        estop_request_challenge?: () => Promise<{ success: boolean; epoch?: number; nonce?: string; timestampMonotonicNs?: number; maxAgeMs?: number; action?: string; error?: string }>;
        estop_submit_reset_token?: (tokenStr: string) => Promise<{ success: boolean; error?: string }>;
        // ECU Flashing APIs
        flash_start?: (config: Record<string, unknown>, confirmationToken?: string) => Promise<{ success: boolean; message?: string; error?: string; [key: string]: unknown }>;
        flash_progress?: () => Promise<Record<string, unknown>>;
        flash_cancel?: () => Promise<{ success: boolean; message?: string; error?: string }>;
        // Cloud APIs
        cloud_start_web_login?: () => Promise<{ success: boolean; login_url?: string; port?: number; error?: string }>;
        cloud_check_web_login_status?: () => Promise<{ success: boolean; status: 'idle' | 'pending' | 'completed' | 'error' | 'cancelled'; user?: unknown; subscription?: unknown; error?: string; login?: AuthLoginOutcome | null }>;
        auth_get_state?: () => Promise<AuthState>;
        auth_refresh_license?: () => Promise<AuthState>;
        auth_start_device_login?: () => Promise<DeviceLoginStart>;
        auth_poll_device_login?: () => Promise<DeviceLoginPoll>;
        auth_cancel_device_login?: () => Promise<{ success: boolean }>;
        mechanic_get_state?: () => Promise<MechanicState>;
        mechanic_set_mode?: (mode: MechanicMode) => Promise<MechanicResult>;
        vehicle_catalog?: () => Promise<VehicleCatalogResult>;
        vehicle_select?: (profileId: string) => Promise<MechanicResult>;
        vehicle_check_identity?: () => Promise<VehicleIdentityResult>;
        adapter_scan?: () => Promise<{ success: boolean; adapters: AdapterEntry[] }>;
        connection_test_start?: (adapterId: string, scenario?: string | null) => Promise<{ success: boolean; test_id?: string; error_code?: string }>;
        connection_test_status?: () => Promise<ConnectionTestStatus>;
        connection_test_cancel?: () => Promise<{ success: boolean }>;
        scan_start?: (allowRead?: boolean) => Promise<{ success: boolean; scan_id?: string; reading?: boolean; error_code?: string }>;
        scan_status?: () => Promise<ScanStatus>;
        scan_cancel?: () => Promise<{ success: boolean }>;
        scan_save_report?: (workshop?: string) => Promise<{ success: boolean; path?: string; text?: string; error_code?: string }>;
        scan_open_report?: () => Promise<{ success: boolean; path?: string; error_code?: string }>;
        cloud_cancel_web_login?: () => Promise<{ success: boolean }>;
        // Diagnostic session (FAZ 1..6) — analysis stays in Python (Bulgu 3)
        get_diagnostic_analysis?: () => Promise<Record<string, unknown>>;
        record_operator_answer?: (
          question_id: string,
          value: unknown,
          kind?: string,
          unit?: string | null,
          is_unknown?: boolean,
        ) => Promise<Record<string, unknown>>;
        get_dialogue_state?: () => Promise<Record<string, unknown>>;
        ask_copilot_structured?: (
          query: string,
          language?: string | null,
          answers?: Record<string, CopilotAnswerValue> | null,
        ) => Promise<{ success: boolean; error?: string; simulated?: boolean; answer?: CopilotStructuredAnswer }>;
        record_technician_feedback?: (dtc: string, resolved: boolean, notes?: string) => Promise<Record<string, unknown>>;
        export_session_report?: () => Promise<{ success: boolean; path?: string; report_length?: number; error?: string }>;
        // T2-7: read-only vendored data licence / attribution surface.
        get_data_attributions?: () => Promise<DataAttributionsPayload>;
        window_minimize?: () => void;
        window_maximize?: () => void;
        window_close?: () => void;
      };
    };
    // Python push channels; frameBus.ts normalises every frame (never trusts the shape).
    onNewCanFrame?: (frame: unknown) => void;
    onNewCanFrames?: (batch: unknown[]) => void;
    onTelemetryTick?: (point: unknown) => void;
    onStatsTick?: (stats: { totalPackets: number; busLoad: number; errorCount: number; frameRate: number }) => void;
    onCloudUploadProgress?: (progress: CloudUploadProgress) => void;
  }
}

// eslint-disable-next-line @typescript-eslint/no-extraneous-class -- static facade over window.pywebview.api (callers use DesktopBridge.x())
export class DesktopBridge {
  public static isNative(): boolean {
    return typeof window !== 'undefined' && !!window.pywebview;
  }

  /**
   * UI-C-005 guard: mock fallbacks are a development convenience only.
   * A production build opened without the native pywebview bridge must
   * fail loudly instead of silently serving mocked E-Stop resets,
   * enterprise licenses, and cloud sessions.
   */
  private static requireNativeOrDev(): void {
    const isProd = typeof import.meta !== 'undefined' && !!(import.meta as { env?: { PROD?: boolean } }).env?.PROD;
    if (isProd && !this.isNative()) {
      throw new Error(
        'Native bridge missing in production build: pywebview API is required. ' +
          'Run the application through the desktop launcher, not a browser.'
      );
    }
  }

  /**
   * REVIEW (capability guard): `pywebview` EXISTING does not imply the
   * method EXISTS — a version-skew bridge (or a partially injected
   * window object) passed isNative() but skipped the method check, fell
   * through to the mock branch, and "succeeded" (fake E-Stop reset,
   * mock enterprise license, phantom uploads). Every call site now
   * verifies the concrete method; a native shell WITHOUT the capability
   * is a hard failure in production, never a silent mock.
   */
  // The pywebview IPC is untyped at this boundary: each public wrapper below
  // states the result type the Python method returns.
  /* eslint-disable @typescript-eslint/no-explicit-any */
  private static apiMethod(name: string): ((...args: any[]) => Promise<any>) | null {
    const method = (window.pywebview?.api as Record<string, unknown> | undefined)?.[name];
    return typeof method === 'function' ? (method as (...args: any[]) => Promise<any>) : null;
  }
  /* eslint-enable @typescript-eslint/no-explicit-any */

  private static hasNativeMethod(name: string): boolean {
    return this.isNative() && this.apiMethod(name) !== null;
  }

  private static requireCapability(method: string, what: string): void {
    if (this.isNative() && !this.hasNativeMethod(method)) {
      throw new Error(
        `Native bridge lacks '${method}' (${what}): the bridge object is present ` +
          'but the capability is missing — refusing to fake success.'
      );
    }
  }

  public static async triggerEstop(): Promise<void> {
    const m = this.apiMethod('trigger_estop');
    if (this.isNative() && m) {
      await m();
      return;
    }
    this.requireCapability('trigger_estop', 'E-Stop trigger');
    this.requireNativeOrDev();
  }

  public static async requestDiagnosticChallenge(
    action: Record<string, unknown>
  ): Promise<{ success: boolean; token?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native'; [key: string]: unknown }> {
    const m = this.apiMethod('request_diagnostic_challenge');
    if (this.isNative() && m) {
      const res = await m(action);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('request_diagnostic_challenge', 'Dual confirmation challenge');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async executeDiagnosticAction(
    action: Record<string, unknown>,
    confirmationTokenOrUserConfirmed?: string | boolean,
    userConfirmed: boolean = false
  ): Promise<{ success: boolean; message?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native'; [key: string]: unknown }> {
    let token: string | undefined = undefined;
    let confirmed = userConfirmed;

    if (typeof confirmationTokenOrUserConfirmed === 'string') {
      token = confirmationTokenOrUserConfirmed;
    } else if (typeof confirmationTokenOrUserConfirmed === 'boolean') {
      confirmed = confirmationTokenOrUserConfirmed;
    }

    // Auto-request challenge token if confirmation is required and token was not provided
    if (!token && confirmed && action?.requires_confirmation !== false && this.isNative()) {
      try {
        const challenge = await this.requestDiagnosticChallenge(action);
        if (challenge.success && challenge.token) {
          token = challenge.token;
        }
      } catch {
        // Fallthrough: will be rejected fail-closed on backend if missing
      }
    }

    const m = this.apiMethod('execute_diagnostic_action');
    if (this.isNative() && m) {
      const res = await m(action, token, confirmed);
      return {
        ...res,
        execution_mode: res?.is_simulating ? 'simulated' : 'native',
      };
    }
    // REVIEW3 #4: the old browser/dev fallback fabricated SUCCESS for
    // UDS 0x14/0x11/0x10 and J1939 DM11 clear commands — an operator in a
    // prod build opened without pywebview saw "DTCs cleared" while no frame
    // ever reached the bus. Diagnostic actions are mock-forbidden in prod;
    // in dev they return an explicit non-success so UI flows stay honest.
    this.requireCapability('execute_diagnostic_action', 'execute Diagnostic Action');
    this.requireNativeOrDev();
    return {
      success: false,
      error: 'NATIVE_BRIDGE_MISSING',
      message: 'Teşhis eylemi iletilmedi: yerel köprü (pywebview) mevcut değil.',
      execution_mode: 'mock',
    };
  }

  // ------------------------------------------------------------------
  // ECU Flashing Bridge Methods
  // ------------------------------------------------------------------
  public static async flashStart(
    config: Record<string, unknown>,
    confirmationToken?: string
  ): Promise<{ success: boolean; message?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native'; [key: string]: unknown }> {
    const m = this.apiMethod('flash_start');
    if (this.isNative() && m) {
      const res = await m(config, confirmationToken);
      return { ...res, execution_mode: res?.is_simulating ? 'simulated' : 'native' };
    }
    this.requireCapability('flash_start', 'ECU Flash Start');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async flashProgress(): Promise<Record<string, unknown>> {
    const m = this.apiMethod('flash_progress');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('flash_progress', 'flash Progress');
    this.requireNativeOrDev();
    return { status: 'idle', percent: 0, logs: [], execution_mode: 'mock' };
  }

  public static async flashCancel(): Promise<{ success: boolean; message?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('flash_cancel');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('flash_cancel', 'flash Cancel');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async exportLogs(format: string): Promise<{ success: boolean; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('export_logs');
    if (this.isNative() && m) {
      const ok = await m(format);
      return { success: !!ok, execution_mode: 'native' };
    }
    this.requireCapability('export_logs', 'logs export');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  /** Supervisor state (STARTUP/SAFE/PASSIVE/ARMED_TX/ACTIVE/FAULT); null outside the native shell. */
  public static async getSafetyState(): Promise<string | null> {
    const m = this.apiMethod('get_safety_state');
    if (this.isNative() && m) {
      return String(await m());
    }
    this.requireCapability('get_safety_state', 'safety state');
    this.requireNativeOrDev();
    return null;
  }

  /** Workbench: bind the listen-only simulated vehicle as the app bus. */
  public static async simVehicleStart(vehicleType: string): Promise<BusInfoResult> {
    const m = this.apiMethod('sim_vehicle_start');
    if (this.isNative() && m) return await m(vehicleType);
    this.requireCapability('sim_vehicle_start', 'simulated vehicle');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async simVehicleStop(): Promise<BusInfoResult> {
    const m = this.apiMethod('sim_vehicle_stop');
    if (this.isNative() && m) return await m();
    this.requireCapability('sim_vehicle_stop', 'simulated vehicle');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async busGetInfo(): Promise<BusInfoResult | null> {
    const m = this.apiMethod('bus_get_info');
    if (this.isNative() && m) return await m();
    this.requireCapability('bus_get_info', 'bus info');
    this.requireNativeOrDev();
    return null;
  }

  private static async call<T>(name: string, what: string, fallback: T, ...args: unknown[]): Promise<T> {
    const m = this.apiMethod(name);
    if (this.isNative() && m) return (await m(...args)) as T;
    this.requireCapability(name, what);
    this.requireNativeOrDev();
    return fallback;
  }

  public static flashPreconditions(): Promise<FlashPreconditions | null> {
    return this.call('flash_preconditions', 'flash preconditions', null);
  }

  /** Workbench: listen-only connection test for an engineer-chosen vehicle type. */
  public static workbenchConnectionTestStart(adapterId: string, vehicleType: string): Promise<{ success: boolean; test_id?: string; error_code?: string }> {
    return this.call('workbench_connection_test_start', 'connection test', { success: false, error_code: 'NATIVE_BRIDGE_MISSING' }, adapterId, vehicleType);
  }

  public static recordsList(): Promise<{ success: boolean; records: RecordEntry[]; truncated: boolean }> {
    return this.call('records_list', 'records', { success: false, records: [], truncated: false });
  }

  public static recordsReplayStart(recordId: string, speed: number): Promise<ReplayStatus> {
    return this.call('records_replay_start', 'replay', { success: false, error_code: 'NATIVE_BRIDGE_MISSING' }, recordId, speed);
  }

  public static recordsReplayStop(): Promise<ReplayStatus> {
    return this.call('records_replay_stop', 'replay', { success: false, error_code: 'NATIVE_BRIDGE_MISSING' });
  }

  public static recordsReplayStatus(): Promise<ReplayStatus> {
    return this.call('records_replay_status', 'replay', { success: false, error_code: 'NATIVE_BRIDGE_MISSING' });
  }

  public static recordsOpenFolder(): Promise<{ success: boolean; error_code?: string }> {
    return this.call('records_open_folder', 'open folder', { success: false, error_code: 'NATIVE_BRIDGE_MISSING' });
  }

  /** Upload one record; Python shows the native approval dialog naming the file. */
  public static recordsUpload(recordId: string): Promise<{ success: boolean; error_code?: string; session_id?: string; status?: string }> {
    return this.call('records_upload', 'cloud upload', { success: false, error_code: 'NATIVE_BRIDGE_MISSING' }, recordId);
  }

  /** Workbench: bind the app bus to an adapter at a fixed bitrate (listen-only). */
  public static workbenchBusConnect(adapterId: string, bitrate: number): Promise<BusInfoResult> {
    return this.call('workbench_bus_connect', 'adapter connection', { success: false, error_code: 'NATIVE_BRIDGE_MISSING' }, adapterId, bitrate);
  }

  public static stimulusStart(): Promise<StimulusStatus> {
    return this.call('stimulus_start', 'stimulus experiment', { running: false, error_code: 'NATIVE_BRIDGE_MISSING' });
  }

  public static stimulusSetPhase(phase: 'rest' | 'active'): Promise<StimulusStatus> {
    return this.call('stimulus_set_phase', 'stimulus experiment', { running: false, error_code: 'NATIVE_BRIDGE_MISSING' }, phase);
  }

  public static stimulusResult(): Promise<StimulusStatus & { candidates?: StimulusCandidate[] }> {
    return this.call('stimulus_result', 'stimulus experiment', { running: false, error_code: 'NATIVE_BRIDGE_MISSING' });
  }

  public static stimulusStop(): Promise<{ success: boolean }> {
    return this.call('stimulus_stop', 'stimulus experiment', { success: false });
  }

  public static simVehiclePedal(pressed: boolean): Promise<{ success: boolean; error_code?: string }> {
    return this.call('sim_vehicle_pedal', 'simulated pedal', { success: false, error_code: 'NATIVE_BRIDGE_MISSING' }, pressed);
  }

  public static async discoveryList(): Promise<{ success: boolean; min_frames: number; streams: DiscoveryStream[] } | null> {
    const m = this.apiMethod('discovery_list');
    if (this.isNative() && m) return await m();
    this.requireCapability('discovery_list', 'discovery list');
    this.requireNativeOrDev();
    return null;
  }

  public static async discoveryReport(key: string): Promise<DiscoveryReport | null> {
    const m = this.apiMethod('discovery_report');
    if (this.isNative() && m) return await m(key);
    this.requireCapability('discovery_report', 'discovery report');
    this.requireNativeOrDev();
    return null;
  }

  public static async discoverySetApproval(key: string, startBit: number, length: number, approved: boolean): Promise<{ success: boolean; error_code?: string }> {
    const m = this.apiMethod('discovery_set_approval');
    if (this.isNative() && m) return await m(key, startBit, length, approved);
    this.requireCapability('discovery_set_approval', 'discovery approval');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async discoverySaveDbc(approvedOnly: boolean): Promise<{ success: boolean; error_code?: string; path?: string; messages?: number; signals?: number; simulated?: boolean }> {
    const m = this.apiMethod('discovery_save_dbc');
    if (this.isNative() && m) return await m(approvedOnly);
    this.requireCapability('discovery_save_dbc', 'DBC export');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async plotSignalList(): Promise<{ success: boolean; signals: PlotSignalInfo[] } | null> {
    const m = this.apiMethod('plot_signal_list');
    if (this.isNative() && m) return await m();
    this.requireCapability('plot_signal_list', 'plot signals');
    this.requireNativeOrDev();
    return null;
  }

  public static async plotSignalSeries(
    names: string[],
    windowS: number,
  ): Promise<{ success: boolean; series: Record<string, PlotSeries>; error_code?: string } | null> {
    const m = this.apiMethod('plot_signal_series');
    if (this.isNative() && m) return await m(names, windowS);
    this.requireCapability('plot_signal_series', 'plot series');
    this.requireNativeOrDev();
    return null;
  }

  public static async estopRequestChallenge(): Promise<{ success: boolean; epoch?: number; nonce?: string; timestampMonotonicNs?: number; maxAgeMs?: number; action?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('estop_request_challenge');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('estop_request_challenge', 'E-Stop reset challenge'); // safety-critical: no silent mock
    this.requireNativeOrDev(); // safety-critical: no silent mock
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async estopSubmitResetToken(tokenStr: string): Promise<{ success: boolean; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('estop_submit_reset_token');
    if (this.isNative() && m) {
      const res = await m(tokenStr);
      return { ...res, execution_mode: 'native' };
    }
    // REVIEW (capability guard): a native shell without the token method
    // previously reported success:true — a fake E-Stop reset. Fail closed.
    this.requireCapability('estop_submit_reset_token', 'E-Stop reset token');
    this.requireNativeOrDev(); // safety-critical: no silent mock
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  // estopResetLocal() removed (REVIEW C-1 / P0-1): the backend bridge method
  // was deleted — a single JS call could mint and consume an E-Stop reset
  // token, clearing a latched E-Stop with zero authorization. Recovery is
  // exclusively the challenge/response flow: estopRequestChallenge() →
  // (out-of-band authorization) → estopSubmitResetToken().

  public static async cloudStartWebLogin(): Promise<{ success: boolean; login_url?: string; port?: number; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_start_web_login');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('cloud_start_web_login', 'cloud start web login');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async cloudCheckWebLoginStatus(): Promise<{ success: boolean; status: 'idle' | 'pending' | 'completed' | 'error' | 'cancelled'; user?: unknown; subscription?: unknown; error?: string; login?: AuthLoginOutcome | null; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_check_web_login_status');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('cloud_check_web_login_status', 'cloud check web login status');
    this.requireNativeOrDev();
    return { success: false, status: 'error', error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  /**
   * Mechanic-flow sign-in state. Returns null outside the native shell (dev
   * browser): the start gate is then skipped, never faked as "licensed".
   */
  public static async authGetState(refreshOnline = false): Promise<AuthState | null> {
    const m = this.apiMethod(refreshOnline ? 'auth_refresh_license' : 'auth_get_state');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability(refreshOnline ? 'auth_refresh_license' : 'auth_get_state', 'sign-in state');
    this.requireNativeOrDev();
    return null;
  }

  public static async authStartDeviceLogin(): Promise<DeviceLoginStart> {
    const m = this.apiMethod('auth_start_device_login');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('auth_start_device_login', 'code sign-in');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async authPollDeviceLogin(): Promise<DeviceLoginPoll> {
    const m = this.apiMethod('auth_poll_device_login');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('auth_poll_device_login', 'code sign-in poll');
    this.requireNativeOrDev();
    return { success: false, status: 'failed', error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async authCancelDeviceLogin(): Promise<void> {
    const m = this.apiMethod('auth_cancel_device_login');
    if (this.isNative() && m) {
      await m();
    }
  }

  /**
   * Mechanic flow (Aşama 4). Null outside the native shell: the flow is then
   * skipped (dev browser), never faked.
   */
  public static async mechanicGetState(): Promise<MechanicState | null> {
    const m = this.apiMethod('mechanic_get_state');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('mechanic_get_state', 'mechanic mode state');
    this.requireNativeOrDev();
    return null;
  }

  public static async mechanicSetMode(mode: MechanicMode): Promise<MechanicResult> {
    const m = this.apiMethod('mechanic_set_mode');
    if (this.isNative() && m) {
      return await m(mode);
    }
    this.requireCapability('mechanic_set_mode', 'mechanic mode change');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async vehicleCatalog(): Promise<VehicleCatalogResult> {
    const m = this.apiMethod('vehicle_catalog');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('vehicle_catalog', 'vehicle catalog');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async vehicleSelect(profileId: string): Promise<MechanicResult> {
    const m = this.apiMethod('vehicle_select');
    if (this.isNative() && m) {
      return await m(profileId);
    }
    this.requireCapability('vehicle_select', 'vehicle selection');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async vehicleCheckIdentity(): Promise<VehicleIdentityResult> {
    const m = this.apiMethod('vehicle_check_identity');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('vehicle_check_identity', 'vehicle identity check');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  /** Connection wizard (Aşama 5). Listen-only: nothing is sent to the vehicle. */
  public static async adapterScan(): Promise<AdapterEntry[]> {
    const m = this.apiMethod('adapter_scan');
    if (this.isNative() && m) {
      const res = await m();
      return res?.adapters ?? [];
    }
    this.requireCapability('adapter_scan', 'adapter scan');
    this.requireNativeOrDev();
    return [];
  }

  public static async connectionTestStart(adapterId: string, scenario?: string): Promise<{ success: boolean; test_id?: string; error_code?: string }> {
    const m = this.apiMethod('connection_test_start');
    if (this.isNative() && m) {
      return await m(adapterId, scenario ?? null);
    }
    this.requireCapability('connection_test_start', 'connection test');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async connectionTestStatus(): Promise<ConnectionTestStatus> {
    const m = this.apiMethod('connection_test_status');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('connection_test_status', 'connection test status');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async connectionTestCancel(): Promise<void> {
    const m = this.apiMethod('connection_test_cancel');
    if (this.isNative() && m) {
      await m();
    }
  }

  /** Mechanic scan (Aşama 6). Reading a car needs the OS confirmation dialog too. */
  public static async scanStart(allowRead: boolean): Promise<{ success: boolean; scan_id?: string; reading?: boolean; error_code?: string }> {
    const m = this.apiMethod('scan_start');
    if (this.isNative() && m) {
      return await m(allowRead);
    }
    this.requireCapability('scan_start', 'scan');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async scanStatus(): Promise<ScanStatus> {
    const m = this.apiMethod('scan_status');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('scan_status', 'scan status');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async scanCancel(): Promise<void> {
    const m = this.apiMethod('scan_cancel');
    if (this.isNative() && m) {
      await m();
    }
  }

  public static async scanSaveReport(workshop: string): Promise<{ success: boolean; path?: string; text?: string; error_code?: string }> {
    const m = this.apiMethod('scan_save_report');
    if (this.isNative() && m) {
      return await m(workshop);
    }
    this.requireCapability('scan_save_report', 'customer report');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async scanOpenReport(): Promise<{ success: boolean; path?: string; error_code?: string }> {
    const m = this.apiMethod('scan_open_report');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('scan_open_report', 'open customer report');
    this.requireNativeOrDev();
    return { success: false, error_code: 'NATIVE_BRIDGE_MISSING' };
  }

  public static async cloudCancelWebLogin(): Promise<{ success: boolean; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_cancel_web_login');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('cloud_cancel_web_login', 'cloud cancel web login');
    this.requireNativeOrDev();
    return { success: false, execution_mode: 'mock' };
  }

  public static async getDiagnosticAnalysis(): Promise<Record<string, unknown> | null> {
    if (this.isNative() && window.pywebview?.api?.get_diagnostic_analysis) {
      return await window.pywebview.api.get_diagnostic_analysis();
    }
    this.requireCapability('get_diagnostic_analysis', 'get Diagnostic Analysis');
    this.requireNativeOrDev();
    return null;
  }

  /** Free-text complaint + live evidence -> structured answer (read-only, offline). */
  public static async askCopilotStructured(
    query: string,
    language: 'tr' | 'en',
    answers: Record<string, CopilotAnswerValue> = {},
  ): Promise<{ success: boolean; error?: string; simulated?: boolean; answer?: CopilotStructuredAnswer }> {
    const m = this.apiMethod('ask_copilot_structured');
    if (this.isNative() && m) {
      return await m(query, language, answers);
    }
    this.requireCapability('ask_copilot_structured', 'ask Copilot');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable' };
  }

  public static async recordOperatorAnswer(
    questionId: string,
    value: unknown,
    kind: string = 'yes_no',
    unit?: string | null,
    isUnknown: boolean = false,
  ): Promise<{ success: boolean; [key: string]: unknown; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    if (this.isNative() && window.pywebview?.api?.record_operator_answer) {
      const res = await window.pywebview.api.record_operator_answer(questionId, value, kind, unit, isUnknown);
      return { success: res?.success !== false, ...res, execution_mode: 'native' };
    }
    this.requireCapability('record_operator_answer', 'record Operator Answer');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable', execution_mode: 'mock' };
  }

  public static async getDialogueState(): Promise<{ success: boolean; [key: string]: unknown; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    if (this.isNative() && window.pywebview?.api?.get_dialogue_state) {
      const res = await window.pywebview.api.get_dialogue_state();
      return { success: res?.success !== false, ...res, execution_mode: 'native' };
    }
    this.requireCapability('get_dialogue_state', 'get Dialogue State');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable', execution_mode: 'mock' };
  }

  public static async recordTechnicianFeedback(
    dtc: string,
    resolved: boolean,
    notes: string = '',
  ): Promise<{ success: boolean; [key: string]: unknown; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    if (this.isNative() && window.pywebview?.api?.record_technician_feedback) {
      const res = await window.pywebview.api.record_technician_feedback(dtc, resolved, notes);
      return { success: res?.success !== false, ...res, execution_mode: 'native' };
    }
    this.requireCapability('record_technician_feedback', 'record Technician Feedback');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable', execution_mode: 'mock' };
  }

  public static async exportSessionReport(): Promise<{ success: boolean; path?: string; report_length?: number; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('export_session_report');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('export_session_report', 'export Session Report');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable', execution_mode: 'mock' };
  }

  /**
   * T2-7: fetch the vendored third-party data licences + attribution texts.
   *
   * READ-ONLY. The backend method takes no arguments, writes nothing, touches
   * no bus / TX path / E-Stop authority / license state, and performs no
   * network I/O. A missing capability is a hard failure (never a fabricated
   * licence list) — the panel must not render an empty list as if the product
   * were compliant.
   */
  public static async getDataAttributions(): Promise<DataAttributionsPayload> {
    const m = this.apiMethod('get_data_attributions');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('get_data_attributions', 'data attributions');
    this.requireNativeOrDev();
    return {
      success: false,
      schemaVersion: 1,
      obligationRequiredCount: 0,
      sources: [],
      errors: [],
      offline: true,
      networkAccess: false,
      error: 'NATIVE_BRIDGE_MISSING',
    };
  }

  public static minimizeWindow(): void {    if (this.isNative() && window.pywebview?.api?.window_minimize) {
      window.pywebview.api.window_minimize();
    }
  }

  public static maximizeWindow(): void {
    if (this.isNative() && window.pywebview?.api?.window_maximize) {
      window.pywebview.api.window_maximize();
    }
  }

  public static closeWindow(): void {
    if (this.isNative() && window.pywebview?.api?.window_close) {
      window.pywebview.api.window_close();
    }
  }
}

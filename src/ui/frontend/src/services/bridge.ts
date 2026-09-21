import { CANFrame, TelemetryPoint } from '../types/can';

export interface CloudLicenseInfo {
  licenseId: string;
  tier: string;
  features: string[];
  expiresAt: number;
  offlineUntil: number;
  issuedAt?: number;
}

export interface CloudStatus {
  success: boolean;
  baseUrl: string;
  hasSessionToken: boolean;
  hasDeviceToken: boolean;
  hwid: string;
  license?: CloudLicenseInfo | null;
  error?: string;
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
  technical: { dtcs: string[]; severity: string; subsystem: string; confidence_score: number | null };
  source_badges: string[];
}

// Interface for pywebview Python backend bridge
declare global {
  interface Window {
    pywebview?: {
      api: {
        trigger_estop: () => Promise<void>;
        heartbeat: () => Promise<boolean>;
        toggle_simulator: () => Promise<boolean>;
        select_scenario: (name: string) => Promise<void>;
        ask_copilot: (query: string) => Promise<string>;
        execute_diagnostic_action?: (action: Record<string, any>, confirmationToken?: string, userConfirmed?: boolean) => Promise<{ success: boolean; message?: string; error?: string; [key: string]: any }>;
        request_diagnostic_challenge?: (action: Record<string, any>) => Promise<{ success: boolean; token?: string; error?: string; [key: string]: any }>;
        get_bus_traffic_status?: () => Promise<Record<string, any>>;
        export_logs: (format: string) => Promise<boolean>;
        save_settings: (settings: Record<string, any>) => Promise<void>;
        inject_fault?: (faultType: string) => Promise<void>;
        set_simulation_speed?: (speed: number) => Promise<void>;
        // E-Stop Cryptographic Challenge / Multi-Operator APIs
        estop_request_challenge?: () => Promise<{ success: boolean; epoch?: number; nonce?: string; timestampMonotonicNs?: number; maxAgeMs?: number; action?: string; error?: string }>;
        estop_submit_reset_token?: (tokenStr: string) => Promise<{ success: boolean; error?: string }>;
        // ECU Flashing APIs
        flash_start?: (config: Record<string, any>, confirmationToken?: string) => Promise<{ success: boolean; message?: string; error?: string; [key: string]: any }>;
        flash_progress?: () => Promise<Record<string, any>>;
        flash_cancel?: () => Promise<{ success: boolean; message?: string; error?: string }>;
        // Signal Discovery & Reverse Engineering APIs
        discovery_get_summary?: () => Promise<Record<string, any>>;
        discovery_analyze_id?: (arbId: number) => Promise<Record<string, any>>;
        discovery_analyze_all?: () => Promise<Record<string, any>>;
        discovery_export_dbc?: (approvedOnly?: boolean) => Promise<{ success: boolean; dbc?: string; error?: string }>;
        discovery_clear?: () => Promise<{ success: boolean; error?: string }>;
        // OEM Registry & Replay APIs
        oem_list_decoders?: () => Promise<string[]>;
        replay_load?: (filePath: string) => Promise<{ success: boolean; frame_count?: number; error?: string }>;
        replay_start?: (speed?: number, loop?: boolean) => Promise<{ success: boolean; error?: string }>;
        replay_stop?: () => Promise<{ success: boolean; error?: string }>;
        // Cloud APIs
        cloud_test_connection?: (url?: string, sessionToken?: string) => Promise<{ success: boolean; status?: number; user?: any; error?: string }>;
        cloud_save_config?: (url: string, sessionToken?: string) => Promise<{ success: boolean; error?: string }>;
        cloud_get_status?: () => Promise<CloudStatus>;
        cloud_register_device?: (deviceName?: string) => Promise<{ success: boolean; deviceId?: string; resetsRemaining?: number; error?: string }>;
        cloud_activate_license?: (licenseRef: string) => Promise<{ success: boolean; licenseId?: string; tier?: string; features?: string[]; expiresAt?: number; offlineUntil?: number; error?: string }>;
        cloud_upload_session?: (filePath: string, vehicleVin?: string) => Promise<{ success: boolean; sessionId?: string; status?: string; error?: string }>;
        cloud_upload_raw_content?: (filename: string, content: string, vehicleVin?: string) => Promise<{ success: boolean; sessionId?: string; status?: string; error?: string }>;
        // Diagnostic session (FAZ 1..6) — analysis stays in Python (Bulgu 3)
        get_session_evidence_summary?: () => Promise<Record<string, any>>;
        get_diagnostic_analysis?: () => Promise<Record<string, any>>;
        reset_diagnostic_session?: () => Promise<Record<string, any>>;
        record_operator_measurement?: (name: string, value: number) => Promise<{ success: boolean; recorded?: string; error?: string }>;
        record_operator_answer?: (
          question_id: string,
          value: any,
          kind?: string,
          unit?: string | null,
          is_unknown?: boolean,
        ) => Promise<Record<string, any>>;
        get_dialogue_state?: () => Promise<Record<string, any>>;
        get_diagnostic_kpi_metrics?: () => Promise<Record<string, any>>;
        record_technician_feedback?: (dtc: string, resolved: boolean, notes?: string) => Promise<Record<string, any>>;
        export_session_report?: () => Promise<{ success: boolean; path?: string; report_length?: number; error?: string }>;
        window_minimize?: () => void;
        window_maximize?: () => void;
        window_close?: () => void;
      };
    };
    onNewCanFrame?: (frame: CANFrame) => void;
    onNewCanFrames?: (batch: CANFrame[]) => void;
    onTelemetryTick?: (point: TelemetryPoint) => void;
    onStatsTick?: (stats: { totalPackets: number; busLoad: number; errorCount: number; frameRate: number }) => void;
    onCloudUploadProgress?: (progress: CloudUploadProgress) => void;
  }
}

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
    const isProd = typeof import.meta !== 'undefined' && !!(import.meta as any).env?.PROD;
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
  private static apiMethod(name: string): ((...args: any[]) => Promise<any>) | null {
    const method = (window as any)?.pywebview?.api?.[name];
    return typeof method === 'function' ? (method as (...args: any[]) => Promise<any>) : null;
  }

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

  public static async toggleSimulator(): Promise<boolean | null> {
    if (this.isNative() && window.pywebview?.api?.toggle_simulator) {
      return await window.pywebview.api.toggle_simulator();
    }
    this.requireCapability('toggle_simulator', 'toggle Simulator');
    this.requireNativeOrDev();
    return null;
  }

  public static async selectScenario(scenario: string): Promise<void> {
    if (this.isNative() && window.pywebview?.api?.select_scenario) {
      await window.pywebview.api.select_scenario(scenario);
      return;
    }
    this.requireCapability('select_scenario', 'select Scenario');
    this.requireNativeOrDev();
  }

  public static async askCopilot(query: string): Promise<string | null> {
    if (this.isNative() && window.pywebview?.api?.ask_copilot) {
      return await window.pywebview.api.ask_copilot(query);
    }
    // Dev-only fallback: no fabricated copilot answer in production.
    this.requireCapability('ask_copilot', 'ask Copilot');
    this.requireNativeOrDev();
    return null;
  }

  public static async requestDiagnosticChallenge(
    action: Record<string, any>
  ): Promise<{ success: boolean; token?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native'; [key: string]: any }> {
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
    action: Record<string, any>,
    confirmationTokenOrUserConfirmed?: string | boolean,
    userConfirmed: boolean = false
  ): Promise<{ success: boolean; message?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native'; [key: string]: any }> {
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
    config: Record<string, any>,
    confirmationToken?: string
  ): Promise<{ success: boolean; message?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native'; [key: string]: any }> {
    const m = this.apiMethod('flash_start');
    if (this.isNative() && m) {
      const res = await m(config, confirmationToken);
      return { ...res, execution_mode: res?.is_simulating ? 'simulated' : 'native' };
    }
    this.requireCapability('flash_start', 'ECU Flash Start');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async flashProgress(): Promise<Record<string, any>> {
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

  // ------------------------------------------------------------------
  // Signal Discovery Bridge Methods
  // ------------------------------------------------------------------
  public static async discoveryGetSummary(): Promise<Record<string, any> | null> {
    const m = this.apiMethod('discovery_get_summary');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('discovery_get_summary', 'discovery Get Summary');
    this.requireNativeOrDev();
    return null;
  }

  public static async discoveryAnalyzeId(arbId: number): Promise<Record<string, any> | null> {
    const m = this.apiMethod('discovery_analyze_id');
    if (this.isNative() && m) {
      return await m(arbId);
    }
    this.requireCapability('discovery_analyze_id', 'discovery Analyze Id');
    this.requireNativeOrDev();
    return null;
  }

  public static async discoveryExportDbc(approvedOnly: boolean = false): Promise<{ success: boolean; dbc?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('discovery_export_dbc');
    if (this.isNative() && m) {
      const res = await m(approvedOnly);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('discovery_export_dbc', 'discovery Export Dbc');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async discoveryClear(): Promise<{ success: boolean; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('discovery_clear');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('discovery_clear', 'discovery Clear');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  // ------------------------------------------------------------------
  // OEM Decoders Bridge Methods
  // ------------------------------------------------------------------
  public static async oemListDecoders(): Promise<string[]> {
    const m = this.apiMethod('oem_list_decoders');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('oem_list_decoders', 'oem List Decoders');
    this.requireNativeOrDev();
    return [];
  }

  // ------------------------------------------------------------------
  // Replay Trace Bridge Methods
  // ------------------------------------------------------------------
  public static async replayLoad(filePath: string): Promise<{ success: boolean; frame_count?: number; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('replay_load');
    if (this.isNative() && m) {
      const res = await m(filePath);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('replay_load', 'replay Load');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async replayStart(speed: number = 1.0, loop: boolean = false): Promise<{ success: boolean; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('replay_start');
    if (this.isNative() && m) {
      const res = await m(speed, loop);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('replay_start', 'replay Start');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async replayStop(): Promise<{ success: boolean; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('replay_stop');
    if (this.isNative() && m) {
      const res = await m();
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('replay_stop', 'replay Stop');
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

  public static async getBusTrafficStatus(): Promise<Record<string, any> | null> {
    if (this.isNative() && window.pywebview?.api?.get_bus_traffic_status) {
      return await window.pywebview.api.get_bus_traffic_status();
    }
    // Browser / Dev fallback
    this.requireCapability('get_bus_traffic_status', 'get Bus Traffic Status');
    this.requireNativeOrDev();
    return null;
  }

  public static async injectFault(faultType: string): Promise<void> {
    if (this.isNative() && window.pywebview?.api?.inject_fault) {
      await window.pywebview.api.inject_fault(faultType);
      return;
    }
    // REVIEW3 #4: fault injection is a simulator-native operation — no
    // silent no-op in production.
    this.requireCapability('inject_fault', 'inject Fault');
    this.requireNativeOrDev();
  }

  public static async setSimulationSpeed(speed: number): Promise<void> {
    if (this.isNative() && window.pywebview?.api?.set_simulation_speed) {
      await window.pywebview.api.set_simulation_speed(speed);
    }
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

  public static async updateSettings(settings: Record<string, any>): Promise<void> {
    if (this.isNative() && window.pywebview?.api?.save_settings) {
      await window.pywebview.api.save_settings(settings);
    }
  }

  // ------------------------------------------------------------------
  // Cloud SaaS & License Operations
  // ------------------------------------------------------------------
  public static async cloudTestConnection(url?: string, sessionToken?: string): Promise<{ success: boolean; status?: number; user?: any; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_test_connection');
    if (this.isNative() && m) {
      const res = await m(url, sessionToken);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('cloud_test_connection', 'cloud connectivity test');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async cloudSaveConfig(url: string, sessionToken?: string): Promise<{ success: boolean; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_save_config');
    if (this.isNative() && m) {
      const res = await m(url, sessionToken);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('cloud_save_config', 'cloud config save');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async cloudGetStatus(): Promise<CloudStatus> {
    const m = this.apiMethod('cloud_get_status');
    if (this.isNative() && m) {
      return await m();
    }
    this.requireCapability('cloud_get_status', 'cloud Get Status');
    this.requireNativeOrDev();
    return {
      success: false,
      baseUrl: 'http://127.0.0.1:8000',
      hasSessionToken: false,
      hasDeviceToken: false,
      hwid: 'LOCAL-DEV-HWID-2026',
      license: null,
      error: 'NATIVE_BRIDGE_MISSING',
    };
  }

  public static async cloudRegisterDevice(deviceName?: string): Promise<{ success: boolean; deviceId?: string; resetsRemaining?: number; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_register_device');
    if (this.isNative() && m) {
      const res = await m(deviceName);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('cloud_register_device', 'cloud register device');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async cloudActivateLicense(licenseRef: string): Promise<{ success: boolean; licenseId?: string; tier?: string; features?: string[]; expiresAt?: number; offlineUntil?: number; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_activate_license');
    if (this.isNative() && m) {
      const res = await m(licenseRef);
      return { ...res, execution_mode: 'native' };
    }
    // REVIEW (capability guard): the old mock granted ENTERPRISE — a fake
    // license activation. Fail closed instead.
    this.requireCapability('cloud_activate_license', 'license activation');
    this.requireNativeOrDev(); // mock grants ENTERPRISE tier — never in prod
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async cloudUploadSession(filePath: string, vehicleVin?: string): Promise<{ success: boolean; sessionId?: string; status?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_upload_session');
    if (this.isNative() && m) {
      const res = await m(filePath, vehicleVin);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('cloud_upload_session', 'telemetry upload');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  public static async cloudUploadRawContent(filename: string, content: string, vehicleVin?: string): Promise<{ success: boolean; sessionId?: string; status?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    const m = this.apiMethod('cloud_upload_raw_content');
    if (this.isNative() && m) {
      const res = await m(filename, content, vehicleVin);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('cloud_upload_raw_content', 'raw content upload');
    this.requireNativeOrDev();
    return { success: false, error: 'NATIVE_BRIDGE_MISSING', execution_mode: 'mock' };
  }

  // ------------------------------------------------------------------
  // Diagnostic session (FAZ 1..6). Analysis logic lives ONLY in Python
  // (Bulgu 3 — diagnosticEngine.ts stays a display fallback); these
  // wrappers consume bridge payloads.
  // ------------------------------------------------------------------
  public static async getSessionEvidenceSummary(): Promise<Record<string, any> | null> {
    if (this.isNative() && window.pywebview?.api?.get_session_evidence_summary) {
      return await window.pywebview.api.get_session_evidence_summary();
    }
    this.requireCapability('get_session_evidence_summary', 'get Session Evidence Summary');
    this.requireNativeOrDev();
    return null;
  }

  public static async getDiagnosticAnalysis(): Promise<Record<string, any> | null> {
    if (this.isNative() && window.pywebview?.api?.get_diagnostic_analysis) {
      return await window.pywebview.api.get_diagnostic_analysis();
    }
    this.requireCapability('get_diagnostic_analysis', 'get Diagnostic Analysis');
    this.requireNativeOrDev();
    return null;
  }

  public static async resetDiagnosticSession(): Promise<Record<string, any> | null> {
    if (this.isNative() && window.pywebview?.api?.reset_diagnostic_session) {
      return await window.pywebview.api.reset_diagnostic_session();
    }
    this.requireCapability('reset_diagnostic_session', 'reset Diagnostic Session');
    this.requireNativeOrDev();
    return null;
  }

  public static async recordOperatorMeasurement(name: string, value: number): Promise<{ success: boolean; recorded?: string; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    if (this.isNative() && window.pywebview?.api?.record_operator_measurement) {
      const res = await window.pywebview.api.record_operator_measurement(name, value);
      return { ...res, execution_mode: 'native' };
    }
    this.requireCapability('record_operator_measurement', 'record Operator Measurement');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable', execution_mode: 'mock' };
  }

  public static async recordOperatorAnswer(
    questionId: string,
    value: any,
    kind: string = 'yes_no',
    unit?: string | null,
    isUnknown: boolean = false,
  ): Promise<{ success: boolean; [key: string]: any; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    if (this.isNative() && window.pywebview?.api?.record_operator_answer) {
      const res = await window.pywebview.api.record_operator_answer(questionId, value, kind, unit, isUnknown);
      return { success: res?.success !== false, ...res, execution_mode: 'native' };
    }
    this.requireCapability('record_operator_answer', 'record Operator Answer');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable', execution_mode: 'mock' };
  }

  public static async getDialogueState(): Promise<{ success: boolean; [key: string]: any; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    if (this.isNative() && window.pywebview?.api?.get_dialogue_state) {
      const res = await window.pywebview.api.get_dialogue_state();
      return { success: res?.success !== false, ...res, execution_mode: 'native' };
    }
    this.requireCapability('get_dialogue_state', 'get Dialogue State');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable', execution_mode: 'mock' };
  }

  public static async getDiagnosticKpiMetrics(): Promise<{ success: boolean; metrics?: Record<string, any>; error?: string; execution_mode?: 'mock' | 'simulated' | 'native' }> {
    if (this.isNative() && window.pywebview?.api?.get_diagnostic_kpi_metrics) {
      const res = await window.pywebview.api.get_diagnostic_kpi_metrics();
      return { success: res?.success !== false, ...res, execution_mode: 'native' };
    }
    this.requireCapability('get_diagnostic_kpi_metrics', 'get Diagnostic Kpi Metrics');
    this.requireNativeOrDev();
    return { success: false, error: 'native bridge unavailable', execution_mode: 'mock' };
  }

  public static async recordTechnicianFeedback(
    dtc: string,
    resolved: boolean,
    notes: string = '',
  ): Promise<{ success: boolean; [key: string]: any; execution_mode?: 'mock' | 'simulated' | 'native' }> {
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

  public static minimizeWindow(): void {
    if (this.isNative() && window.pywebview?.api?.window_minimize) {
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

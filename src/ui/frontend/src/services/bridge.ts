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
        execute_diagnostic_action?: (action: Record<string, any>, userConfirmed: boolean) => Promise<{ success: boolean; message?: string; error?: string; [key: string]: any }>;
        get_bus_traffic_status?: () => Promise<Record<string, any>>;
        export_logs: (format: string) => Promise<boolean>;
        save_settings: (settings: Record<string, any>) => Promise<void>;
        inject_fault?: (faultType: string) => Promise<void>;
        set_simulation_speed?: (speed: number) => Promise<void>;
        // E-Stop Cryptographic Challenge / Multi-Operator APIs
        estop_request_challenge?: () => Promise<{ success: boolean; epoch?: number; nonce?: string; timestampMonotonicNs?: number; maxAgeMs?: number; action?: string; error?: string }>;
        estop_submit_reset_token?: (tokenStr: string) => Promise<{ success: boolean; error?: string }>;
        // Cloud APIs
        cloud_test_connection?: (url?: string, sessionToken?: string) => Promise<{ success: boolean; status?: number; user?: any; error?: string }>;
        cloud_save_config?: (url: string, sessionToken?: string) => Promise<{ success: boolean; error?: string }>;
        cloud_get_status?: () => Promise<CloudStatus>;
        cloud_register_device?: (deviceName?: string) => Promise<{ success: boolean; deviceId?: string; resetsRemaining?: number; error?: string }>;
        cloud_activate_license?: (licenseRef: string) => Promise<{ success: boolean; licenseId?: string; tier?: string; features?: string[]; expiresAt?: number; offlineUntil?: number; error?: string }>;
        cloud_upload_session?: (filePath: string, vehicleVin?: string) => Promise<{ success: boolean; sessionId?: string; status?: string; error?: string }>;
        cloud_upload_raw_content?: (filename: string, content: string, vehicleVin?: string) => Promise<{ success: boolean; sessionId?: string; status?: string; error?: string }>;
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

  public static async triggerEstop(): Promise<void> {
    if (this.isNative() && window.pywebview?.api?.trigger_estop) {
      await window.pywebview.api.trigger_estop();
      return;
    }
    this.requireNativeOrDev();
  }

  public static async toggleSimulator(): Promise<boolean | null> {
    if (this.isNative() && window.pywebview?.api?.toggle_simulator) {
      return await window.pywebview.api.toggle_simulator();
    }
    this.requireNativeOrDev();
    return null;
  }

  public static async selectScenario(scenario: string): Promise<void> {
    if (this.isNative() && window.pywebview?.api?.select_scenario) {
      await window.pywebview.api.select_scenario(scenario);
      return;
    }
    this.requireNativeOrDev();
  }

  public static async askCopilot(query: string): Promise<string | null> {
    if (this.isNative() && window.pywebview?.api?.ask_copilot) {
      return await window.pywebview.api.ask_copilot(query);
    }
    return null;
  }

  public static async executeDiagnosticAction(
    action: Record<string, any>,
    userConfirmed: boolean = false
  ): Promise<{ success: boolean; message?: string; error?: string; [key: string]: any }> {
    if (this.isNative() && window.pywebview?.api?.execute_diagnostic_action) {
      return await window.pywebview.api.execute_diagnostic_action(action, userConfirmed);
    }
    // Browser / Dev fallback:
    if (action.action_type === 'uds_clear_dtc') {
      return {
        success: true,
        message: '✅ [UDS 0x14] ECU arıza hafızası temizlendi (Pozitif Yanıt 0x54). Hata sayacı sıfırlandı.',
        service: '0x14',
      };
    }
    if (action.action_type === 'uds_read_did') {
      const did = action.params?.did || 0xF190;
      if (did === 0xF190) {
        return {
          success: true,
          message: '📄 [UDS 0x22 DID 0xF190] Araç VIN Numarası: `WVWZZZ1KZ9W123456` (Pozitif Yanıt 0x62).',
          vin: 'WVWZZZ1KZ9W123456',
        };
      }
      return {
        success: true,
        message: `📄 [UDS 0x22 DID 0x${did.toString(16).toUpperCase()}] Veri okundu: 01 A4 B2 C3 (Pozitif Yanıt 0x62).`,
      };
    }
    if (action.action_type === 'uds_session_control') {
      const st = action.params?.session_type || 3;
      return {
        success: true,
        message: `🔄 [UDS 0x10] Teşhis oturumu 0x0${st} moduna geçirildi (Pozitif Yanıt 0x50).`,
      };
    }
    if (action.action_type === 'j1939_clear_dtc') {
      return {
        success: true,
        message: '✅ [J1939 DM11] Ağır vasıta aktif arızaları temizlendi (PGN 65235).',
      };
    }
    if (action.action_type === 'j1939_dm1_query') {
      return {
        success: true,
        message: '📋 [J1939 DM1] Aktif Arıza Durumu: Nominal (0 DTC - PGN 65226).',
      };
    }
    if (action.action_type === 'uds_routine') {
      const rid = action.params?.routine_id ? `0x${Number(action.params.routine_id).toString(16).toUpperCase()}` : '0xD001';
      return {
        success: true,
        message: `▶️ [UDS 0x31] Teşhis rutini ${rid} başarıyla başlatıldı (Pozitif Yanıt 0x71).`,
        routine_id: rid,
      };
    }
    if (action.action_type === 'uds_ecu_reset') {
      const rt = action.params?.reset_type || 1;
      return {
        success: true,
        message: `⚡ [UDS 0x11] ECU Donanımsal Reset komutu iletildi (Reset Tipi: 0x0${rt}, Pozitif Yanıt 0x51).`,
        reset_type: rt,
      };
    }
    return {
      success: true,
      message: `▶️ [${action.label || 'Diagnostik Eylem'}] İşlem başarıyla tamamlandı.`,
    };
  }

  public static async getBusTrafficStatus(): Promise<Record<string, any> | null> {
    if (this.isNative() && window.pywebview?.api?.get_bus_traffic_status) {
      return await window.pywebview.api.get_bus_traffic_status();
    }
    // Browser / Dev fallback
    return {
      bus_load_percent: 32,
      error_count: 0,
      total_packets: 1250,
      recent_frame_count: 50,
      recent_frame_rate: 500,
      status: 'nominal',
      babbling_node: null,
      is_simulating: true,
      anomalies: [],
    };
  }

  public static async injectFault(faultType: string): Promise<void> {
    if (this.isNative() && window.pywebview?.api?.inject_fault) {
      await window.pywebview.api.inject_fault(faultType);
    }
  }

  public static async setSimulationSpeed(speed: number): Promise<void> {
    if (this.isNative() && window.pywebview?.api?.set_simulation_speed) {
      await window.pywebview.api.set_simulation_speed(speed);
    }
  }

  public static async estopRequestChallenge(): Promise<{ success: boolean; epoch?: number; nonce?: string; timestampMonotonicNs?: number; maxAgeMs?: number; action?: string; error?: string }> {
    if (this.isNative() && window.pywebview?.api?.estop_request_challenge) {
      return await window.pywebview.api.estop_request_challenge();
    }
    this.requireNativeOrDev(); // safety-critical: no silent mock
    return { success: true, epoch: 1, nonce: 'local_nonce', maxAgeMs: 30000, action: 'ESTOP_RESET' };
  }

  public static async estopSubmitResetToken(tokenStr: string): Promise<{ success: boolean; error?: string }> {
    if (this.isNative() && window.pywebview?.api?.estop_submit_reset_token) {
      return await window.pywebview.api.estop_submit_reset_token(tokenStr);
    }
    this.requireNativeOrDev(); // safety-critical: no silent mock
    return { success: true };
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
  public static async cloudTestConnection(url?: string, sessionToken?: string): Promise<{ success: boolean; status?: number; user?: any; error?: string }> {
    if (this.isNative() && window.pywebview?.api?.cloud_test_connection) {
      return await window.pywebview.api.cloud_test_connection(url, sessionToken);
    }
    this.requireNativeOrDev();
    return { success: true, status: 200, user: { email: 'operator@example.com', organization_name: 'CAN Diagnostics Ltd' } };
  }

  public static async cloudSaveConfig(url: string, sessionToken?: string): Promise<{ success: boolean; error?: string }> {
    if (this.isNative() && window.pywebview?.api?.cloud_save_config) {
      return await window.pywebview.api.cloud_save_config(url, sessionToken);
    }
    this.requireNativeOrDev();
    return { success: true };
  }

  public static async cloudGetStatus(): Promise<CloudStatus> {
    if (this.isNative() && window.pywebview?.api?.cloud_get_status) {
      return await window.pywebview.api.cloud_get_status();
    }
    this.requireNativeOrDev();
    return {
      success: true,
      baseUrl: 'http://127.0.0.1:8000',
      hasSessionToken: false,
      hasDeviceToken: false,
      hwid: 'LOCAL-DEV-HWID-2026',
      license: null
    };
  }

  public static async cloudRegisterDevice(deviceName?: string): Promise<{ success: boolean; deviceId?: string; resetsRemaining?: number; error?: string }> {
    if (this.isNative() && window.pywebview?.api?.cloud_register_device) {
      return await window.pywebview.api.cloud_register_device(deviceName);
    }
    this.requireNativeOrDev();
    return { success: true, deviceId: 'dev_mock_uuid_2026', resetsRemaining: 1 };
  }

  public static async cloudActivateLicense(licenseRef: string): Promise<{ success: boolean; licenseId?: string; tier?: string; features?: string[]; expiresAt?: number; offlineUntil?: number; error?: string }> {
    if (this.isNative() && window.pywebview?.api?.cloud_activate_license) {
      return await window.pywebview.api.cloud_activate_license(licenseRef);
    }
    this.requireNativeOrDev(); // mock grants ENTERPRISE tier — never in prod
    return {
      success: true,
      licenseId: 'lic_mock_2026',
      tier: 'enterprise',
      features: ['can_fd', 'j1939', 'uds_flash', 'cloud_telemetry', 'oem_packs'],
      expiresAt: Math.floor(Date.now() / 1000) + 86400 * 365,
      offlineUntil: Math.floor(Date.now() / 1000) + 86400 * 30
    };
  }

  public static async cloudUploadSession(filePath: string, vehicleVin?: string): Promise<{ success: boolean; sessionId?: string; status?: string; error?: string }> {
    if (this.isNative() && window.pywebview?.api?.cloud_upload_session) {
      return await window.pywebview.api.cloud_upload_session(filePath, vehicleVin);
    }
    this.requireNativeOrDev();
    return { success: true, sessionId: 'sess_mock_2026', status: 'ready' };
  }

  public static async cloudUploadRawContent(filename: string, content: string, vehicleVin?: string): Promise<{ success: boolean; sessionId?: string; status?: string; error?: string }> {
    if (this.isNative() && window.pywebview?.api?.cloud_upload_raw_content) {
      return await window.pywebview.api.cloud_upload_raw_content(filename, content, vehicleVin);
    }
    this.requireNativeOrDev();
    return { success: true, sessionId: 'sess_mock_2026', status: 'ready' };
  }
}

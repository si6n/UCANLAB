import { SignalAnomalyEvent, CanIdTimingState } from './anomalyDetector';
import { TelemetryPoint } from '../types/can';

export class CopilotContextBuilder {
  /**
   * Build structured diagnostic prompt snapshot for a Signal Anomaly Glitch (Oscilloscope).
   */
  public static buildSignalAnomalyPrompt(
    event: SignalAnomalyEvent,
    currentTelemetry: TelemetryPoint | null
  ): string {
    // REVIEW (no fabricated telemetry): `value || demo` replaced EVERY zero
    // with a demo number — a real 0 RPM reading reached the copilot as
    // 2381 RPM. `??` keeps zeros; only null/undefined telemetry falls back
    // to explicit nulls so the analysis knows the feed is missing.
    const snapshot = {
      event_type: event.type,
      signal_name: event.signalName === 'rpm' ? 'Motor Devri (Engine RPM)' : 'Turbo Takviye Basıncı (Boost Bar)',
      timestamp_sec: event.timestampSec.toFixed(2),
      observed_value: event.value,
      anomaly_delta: event.delta,
      severity: event.severity,
      bus_load_percent: currentTelemetry?.busLoadPercent ?? null,
      active_telemetry: currentTelemetry
        ? {
            rpm: currentTelemetry.rpm,
            turbo_boost_bar: currentTelemetry.turboBoostBar,
            coolant_temp_c: currentTelemetry.coolantTempC
          }
        : null
    };

    return `Lütfen şu Osiloskop Sinyal Anomalisi Enstantanesini (Snapshot) analiz et:\n\n` +
      `\`\`\`json\n${JSON.stringify(snapshot, null, 2)}\n\`\`\`\n\n` +
      `Bu dalga formu bozulmasının olası fiziksel/elektronik kök nedenlerini, kanıt seviyesini (Detected/Likely/Possible) ve bir sonraki ölçüm/test adımını açıkla.`;
  }

  /**
   * Build structured diagnostic prompt snapshot for a CAN Frequency / Timing Anomaly (Heatmap).
   */
  public static buildCanTimingPrompt(
    timingState: CanIdTimingState,
    totalBusLoadPercent: number
  ): string {
    const snapshot = {
      event_type: timingState.status,
      can_id: timingState.idHex,
      signal_name: timingState.name,
      protocol: timingState.protocol,
      observed_frequency_hz: timingState.observedFreqHz,
      expected_frequency_hz: timingState.expectedFreqHz,
      deviation_percent: timingState.deviationPercent,
      jitter_ms: timingState.jitterMs,
      bus_load_contribution_percent: timingState.busLoadContribution,
      total_bus_load_percent: totalBusLoadPercent,
      severity: timingState.severity,
      classification: timingState.status === 'BABBLING_CANDIDATE' 
        ? 'POSSIBLE_BABBLING_OR_FLOODING' 
        : timingState.status === 'MESSAGE_TIMEOUT' 
          ? 'COMMUNICATION_TIMEOUT' 
          : 'TIMING_DRIFT'
    };

    return `Lütfen şu CAN-Bus Frekans ve Ağ Anomalisi Enstantanesini (Snapshot) analiz et:\n\n` +
      `\`\`\`json\n${JSON.stringify(snapshot, null, 2)}\n\`\`\`\n\n` +
      `Bu frekans sapmasının/olayının ağ sağlığına etkisini, olası nedenlerini ve önerilen teşhis adımlarını açıkla.`;
  }
}

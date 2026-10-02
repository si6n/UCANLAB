import { L } from '../mechanic/text';
import type { Tone } from '../workbench/ui';

export type SafetyView = { tone: Tone; text: string; detail: string };

/** Plain-language view of the supervisor state, shared by both modes. */
export function safetyView(state: string | null): SafetyView {
  switch (state) {
    case 'ARMED_TX':
    case 'ACTIVE':
      return {
        tone: 'warn',
        text: L('Araca yazma açık', 'Transmit armed'),
        detail: L('Onaylı bir işlem araca çerçeve gönderebilir.', 'An approved operation may send frames to the vehicle.'),
      };
    case 'FAULT':
      return {
        tone: 'danger',
        text: L('Güvenlik kilidi', 'Safety lock'),
        detail: L(
          'E-Stop veya bir güvenlik hatası nedeniyle gönderim kapalı. Kilidi yalnızca yetkili sıfırlama açar.',
          'Transmission is off because of the E-Stop or a safety fault. Only an authorised reset clears it.',
        ),
      };
    case 'PASSIVE':
    case 'SAFE':
    case 'STARTUP':
      return {
        tone: 'ok',
        text: L('Yalnızca dinleme', 'Listen only'),
        detail: L('Uygulama araca hiçbir çerçeve göndermiyor.', 'The app sends no frames to the vehicle.'),
      };
    default:
      return {
        tone: 'neutral',
        text: L('Güvenlik durumu bilinmiyor', 'Safety state unknown'),
        detail: L('Masaüstü uygulamasına bağlı değil.', 'Not connected to the desktop app.'),
      };
  }
}

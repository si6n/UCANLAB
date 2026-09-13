import { CANFrame, FrameType, FrameDirection } from '../types/can';

/**
 * Boundary adapter: native Python frame DTO -> frontend CANFrame.
 *
 * REVIEW (Python-React data contract): the pywebview bridge pushes
 * `{id, timestamp, data, isExtended, isFd, ...}` objects while every
 * frontend consumer reads `canIdHex` / `dataHex` / `timeSec` — a raw
 * native frame crashed the sniffer render path with
 * `TypeError: Cannot read properties of undefined`. Every native frame
 * now passes through this adapter before entering React state.
 */
export function fromNativeFrame(raw: any): CANFrame | null {
  if (!raw || typeof raw !== 'object') return null;

  const idStr = typeof raw.id === 'string' ? raw.id : `0x${Number(raw.arbitration_id ?? 0).toString(16).toUpperCase()}`;
  const timeSec = Number(raw.timestamp ?? raw.timeSec ?? 0);

  // data: hex string ("0102AABB") from Python; tolerate array forms.
  let dataHex: string[] = [];
  if (typeof raw.data === 'string' && raw.data.length > 0) {
    for (let i = 0; i + 1 < raw.data.length; i += 2) {
      dataHex.push(raw.data.slice(i, i + 2).toUpperCase());
    }
  } else if (Array.isArray(raw.dataHex)) {
    dataHex = raw.dataHex.map((b: any) => String(b).toUpperCase());
  } else if (Array.isArray(raw.data)) {
    dataHex = raw.data.map((b: any) => Number(b).toString(16).toUpperCase().padStart(2, '0'));
  }

  const isFd = Boolean(raw.isFd ?? raw.is_fd);
  const isExtended = Boolean(raw.isExtended ?? raw.is_extended) || idStr.replace('0x', '').length > 3;
  const isError = Boolean(raw.isErrorFrame) || idStr === '0x00000000';

  let frameType: FrameType = 'Std';
  if (isError) frameType = 'ERR';
  else if (isFd) frameType = 'FD';
  else if (isExtended) frameType = 'Ext';

  let ascii = '';
  for (const hx of dataHex) {
    const b = parseInt(hx, 16);
    if (!Number.isNaN(b)) ascii += (b >= 32 && b <= 126) ? String.fromCharCode(b) : '.';
  }

  return {
    id: `native-${timeSec}-${idStr}-${Math.random().toString(36).slice(2, 7)}`,
    timeSec,
    timeFormatted: `${timeSec.toFixed(4)}s`,
    channel: String(raw.channel ?? raw.channel_id ?? 'vcan0'),
    canIdHex: idStr.toUpperCase(),
    canIdDec: parseInt(idStr.replace('0x', ''), 16) || 0,
    frameType,
    dir: (raw.dir === 'TX' || raw.direction === 'tx' ? 'TX' : 'RX') as FrameDirection,
    dlc: Number(raw.dlc ?? dataHex.length),
    dataHex,
    ascii,
    isErrorFrame: isError,
    isCanFd: isFd,
  };
}

export function fromNativeFrames(batch: any[]): CANFrame[] {
  if (!Array.isArray(batch)) return [];
  const adapted: CANFrame[] = [];
  for (const raw of batch) {
    const f = fromNativeFrame(raw);
    if (f) adapted.push(f);
  }
  return adapted;
}

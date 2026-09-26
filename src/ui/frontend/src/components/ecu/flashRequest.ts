/* B-07 flash contract — typed request shared by frontend/backend shape.
 * Backend mirror: DesktopApiBridge._validate_flash_prerequisites. */

export type EcuId = 'ECM' | 'TCU' | 'ABS' | 'BCM';

export interface FlashEcuProfile {
  memoryBase: number;
  memorySizeBytes: number;
  maxImageBytes: number;
}

export const FLASH_ECU_BOUNDS: Record<EcuId, FlashEcuProfile> = {
  ECM: { memoryBase: 0x80000, memorySizeBytes: 4 * 1024 * 1024, maxImageBytes: 32 * 1024 * 1024 },
  TCU: { memoryBase: 0x80000, memorySizeBytes: 1 * 1024 * 1024, maxImageBytes: 32 * 1024 * 1024 },
  ABS: { memoryBase: 0x80000, memorySizeBytes: 4 * 1024 * 1024, maxImageBytes: 32 * 1024 * 1024 },
  BCM: { memoryBase: 0x80000, memorySizeBytes: 2 * 1024 * 1024, maxImageBytes: 32 * 1024 * 1024 },
};

export const FLASH_ALLOWED_BLOCK_SIZES = [64, 128, 256, 512, 1024, 2048, 4096] as const;

export interface FlashRequest {
  action_type: 'ecu_flash';
  ecu: EcuId;
  fileName: string;
  filePath?: string;
  sizeBytes: number;
  data: string;
  memoryAddress: number;
  blockSize: number;
  firmwareSignature: string;
  trustedPubkey: string;
  expectedVin?: string;
  expectedSerial?: string;
}

export interface FlashRequestInput extends Omit<FlashRequest, 'action_type' | 'data' | 'sizeBytes'> {
  data: string;
  sizeBytes: number;
}

const VIN_RE = /^[A-HJ-NPR-Z0-9]{17}$/;

export function buildFlashRequest(input: FlashRequestInput): { ok: true; request: FlashRequest } | { ok: false; error: string } {
  if (!input.firmwareSignature || !input.firmwareSignature.trim()) {
    return { ok: false, error: 'Firmware imzası (firmwareSignature) gerekli.' };
  }
  if (!/^[0-9a-fA-F\s]+$/.test(input.firmwareSignature.trim()) || input.firmwareSignature.trim().length < 16) {
    return { ok: false, error: 'Firmware imzası hex formatında olmalı.' };
  }
  if (!input.trustedPubkey || !input.trustedPubkey.trim()) {
    return { ok: false, error: 'Güvenilir ortak anahtar (trustedPubkey) gerekli.' };
  }
  if (!/^[0-9a-fA-F\s]+$/.test(input.trustedPubkey.trim()) || input.trustedPubkey.trim().length < 16) {
    return { ok: false, error: 'Ortak anahtar hex formatında olmalı.' };
  }
  const vin = (input.expectedVin || '').trim().toUpperCase();
  const serial = (input.expectedSerial || '').trim();
  if (!vin && !serial) {
    return { ok: false, error: 'Hedef VIN/seri (expectedVin) gerekli.' };
  }
  if (vin && !VIN_RE.test(vin)) {
    return { ok: false, error: 'VIN 17 karakter olmalı (I, O, Q harici).' };
  }
  if (!FLASH_ALLOWED_BLOCK_SIZES.includes(input.blockSize as never)) {
    return { ok: false, error: 'Geçersiz blok boyutu.' };
  }
  if (!Number.isInteger(input.memoryAddress) || input.memoryAddress < 0 || input.memoryAddress > 0xffffffff) {
    return { ok: false, error: 'Bellek adresi aralık dışında.' };
  }
  if (!Number.isInteger(input.sizeBytes) || input.sizeBytes < 1024 || input.sizeBytes > 32 * 1024 * 1024) {
    return { ok: false, error: 'Görüntü boyutu 1 KB..32 MB aralığında olmalı.' };
  }
  const bounds = FLASH_ECU_BOUNDS[input.ecu];
  if (input.memoryAddress < bounds.memoryBase || input.memoryAddress + input.sizeBytes > bounds.memoryBase + bounds.memorySizeBytes) {
    return { ok: false, error: 'Bellek aralığı ECU flash penceresi dışında.' };
  }
  return {
    ok: true,
    request: {
      action_type: 'ecu_flash',
      ecu: input.ecu,
      fileName: input.fileName,
      filePath: input.filePath,
      sizeBytes: input.sizeBytes,
      data: input.data,
      memoryAddress: input.memoryAddress,
      blockSize: input.blockSize,
      firmwareSignature: input.firmwareSignature.trim(),
      trustedPubkey: input.trustedPubkey.trim(),
      ...(vin ? { expectedVin: vin } : {}),
      ...(serial ? { expectedSerial: serial } : {}),
    },
  };
}

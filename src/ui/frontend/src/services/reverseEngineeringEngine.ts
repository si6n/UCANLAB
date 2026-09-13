/**
 * Evidence-Based AI CAN Signal Discovery & Reverse Engineering Engine (Plan v2)
 * Pure deterministic evidence extraction, bit-level candidate generation,
 * statistical metrics (Pearson, Spearman, Time-Lag, Regression R²),
 * counter/CRC rejection filters, deterministic confidence scoring, and DBC generation.
 */

export type TargetSignalType = 'accelerator' | 'brake' | 'steering' | 'gear' | 'custom';

export type ExperimentPhase = 
  | 'IDLE' 
  | 'SAFETY_CHECK' 
  | 'BASELINE' 
  | 'STIMULUS' 
  | 'RECOVERY' 
  | 'ANALYSIS' 
  | 'COMPLETED';

export interface TargetSignalConfig {
  type: TargetSignalType;
  name: string;
  unit: string;
  expectedMin: number;
  expectedMax: number;
  stimulusInstruction: string;
  recoveryInstruction: string;
}

export const TARGET_SIGNAL_CONFIGS: Record<TargetSignalType, TargetSignalConfig> = {
  accelerator: {
    type: 'accelerator',
    name: 'AcceleratorPosition',
    unit: '%',
    expectedMin: 0,
    expectedMax: 100,
    stimulusInstruction: 'Şimdi gaza %50 oranında 3 saniye boyunca basın ve sabit tutun!',
    recoveryInstruction: 'Şimdi gazı tamamen bırakın ve rölantiye dönmesini bekleyin.'
  },
  brake: {
    type: 'brake',
    name: 'BrakePressure',
    unit: 'Bar',
    expectedMin: 0,
    expectedMax: 120,
    stimulusInstruction: 'Şimdi fren pedalına orta güçte 3 saniye boyunca basın!',
    recoveryInstruction: 'Şimdi fren pedalını tamamen bırakın.'
  },
  steering: {
    type: 'steering',
    name: 'SteeringAngle',
    unit: 'deg',
    expectedMin: -540,
    expectedMax: 540,
    stimulusInstruction: 'Şimdi direksiyonu sağa 90 derece çevirip 3 saniye tutun!',
    recoveryInstruction: 'Şimdi direksiyonu düz konuma geri getirin.'
  },
  gear: {
    type: 'gear',
    name: 'GearSelector',
    unit: 'enum',
    expectedMin: 0,
    expectedMax: 6,
    stimulusInstruction: 'Şimdi vitesi Park (P) konumundan Drive (D) konumuna alın!',
    recoveryInstruction: 'Şimdi vitesi tekrar Park (P) konumuna getirin.'
  },
  custom: {
    type: 'custom',
    name: 'CustomSignal',
    unit: 'raw',
    expectedMin: 0,
    expectedMax: 255,
    stimulusInstruction: 'Şimdi test etmek istediğiniz eylemi 3 saniye boyunca uygulayın!',
    recoveryInstruction: 'Şimdi eylemi sonlandırıp eski durumuna getirin.'
  }
};

export interface RawSamplePoint {
  t: number;
  raw: number;
  physical: number;
  stimulusTarget: number;
}

export interface SignalCandidate {
  id: string;
  canIdHex: string;
  signalName: string;
  startBit: number;
  bitLength: number;
  endian: 'Intel' | 'Motorola';
  isSigned: boolean;
  scale: number;
  offset: number;
  unit: string;
  minObserved: number;
  maxObserved: number;
  
  // Deterministic Evidence Metrics
  deltaMax: number;
  pearsonR: number;
  spearmanRho: number;
  timeLagMs: number;
  regressionR2: number;
  monotonicityScore: number;
  recoveryDelta: number;
  
  // Rejection Filters
  isCounter: boolean;
  isCrc: boolean;
  rejectionReason?: string;
  
  // Confidence & Verification
  confidenceScore: number; // 0.00 - 1.00
  confidenceLevel: 'HIGH' | 'MEDIUM' | 'LOW' | 'REJECTED';
  status: 'INFERRED' | 'VERIFIED' | 'REJECTED';
  rawSamples: RawSamplePoint[];
}

export interface CapturedFrameRecord {
  timestampSec: number;
  canIdHex: string;
  dlc: number;
  payloadHex: string;
  phase: ExperimentPhase;
  stimulusPercent: number;
}

export class ReverseEngineeringEngine {
  /**
   * Extract bit-level integer value from Hex payload using startBit, bitLength, Endianness and Signedness.
   */
  public static extractBitValue(
    payloadHex: string,
    startBit: number,
    bitLength: number,
    endian: 'Intel' | 'Motorola' = 'Intel',
    isSigned = false
  ): number {
    const cleanHex = payloadHex.replace(/\s+/g, '');
    const bytes: number[] = [];
    for (let i = 0; i < cleanHex.length; i += 2) {
      bytes.push(parseInt(cleanHex.substr(i, 2), 16) || 0);
    }
    while (bytes.length < 8) bytes.push(0);

    let rawVal = 0;

    if (endian === 'Intel') {
      // Little-Endian (Standard CAN / Intel format)
      const startByte = Math.floor(startBit / 8);
      const bitOffsetInByte = startBit % 8;

      let accumulatedBits = 0;
      let byteIndex = startByte;

      while (accumulatedBits < bitLength && byteIndex < bytes.length) {
        const bitsToTake = Math.min(8 - (byteIndex === startByte ? bitOffsetInByte : 0), bitLength - accumulatedBits);
        const shift = byteIndex === startByte ? bitOffsetInByte : 0;
        const mask = (1 << bitsToTake) - 1;
        const part = (bytes[byteIndex] >> shift) & mask;

        rawVal |= (part << accumulatedBits);
        accumulatedBits += bitsToTake;
        byteIndex++;
      }
    } else {
      // Motorola (Big-Endian)
      const startByte = Math.floor(startBit / 8);
      const bitOffsetInByte = startBit % 8;
      let accumulatedBits = 0;
      let byteIndex = startByte;

      while (accumulatedBits < bitLength && byteIndex >= 0) {
        const bitsToTake = Math.min(bitOffsetInByte + 1, bitLength - accumulatedBits);
        const shift = (bitOffsetInByte + 1) - bitsToTake;
        const mask = (1 << bitsToTake) - 1;
        const part = (bytes[byteIndex] >> shift) & mask;

        rawVal = (rawVal << bitsToTake) | part;
        accumulatedBits += bitsToTake;
        byteIndex--;
      }
    }

    // Handle Two's Complement Signed integer
    if (isSigned && bitLength > 1) {
      const signBit = 1 << (bitLength - 1);
      if ((rawVal & signBit) !== 0) {
        rawVal = rawVal - (1 << bitLength);
      }
    }

    return rawVal;
  }

  /**
   * Calculate Pearson Correlation Coefficient (r).
   */
  public static calculatePearson(x: number[], y: number[]): number {
    const n = Math.min(x.length, y.length);
    if (n < 4) return 0;

    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, sumY2 = 0;
    for (let i = 0; i < n; i++) {
      sumX += x[i];
      sumY += y[i];
      sumXY += x[i] * y[i];
      sumX2 += x[i] * x[i];
      sumY2 += y[i] * y[i];
    }

    const numerator = n * sumXY - sumX * sumY;
    const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
    if (denominator === 0) return 0;

    const r = numerator / denominator;
    return isNaN(r) ? 0 : Math.max(-1, Math.min(1, parseFloat(r.toFixed(4))));
  }

  /**
   * Calculate Spearman Rank Correlation (rho).
   */
  public static calculateSpearman(x: number[], y: number[]): number {
    const n = Math.min(x.length, y.length);
    if (n < 4) return 0;

    const rank = (arr: number[]) => {
      const sorted = arr.map((v, i) => ({ v, i })).sort((a, b) => a.v - b.v);
      const ranks = new Array(arr.length);
      for (let i = 0; i < sorted.length; i++) {
        ranks[sorted[i].i] = i + 1;
      }
      return ranks;
    };

    const rankX = rank(x.slice(0, n));
    const rankY = rank(y.slice(0, n));
    return this.calculatePearson(rankX, rankY);
  }

  /**
   * Dynamic cross-correlation lag scan: find the lag (ms) maximizing |Pearson r|
   * between the raw series and the stimulus series.
   * Positive lag: raw signal follows the stimulus by that many ms.
   */
  public static estimateTimeLagMs(
    timestampsSec: number[],
    rawValues: number[],
    stimulusValues: number[],
    maxLagMs = 500
  ): { lagMs: number; pearsonAtLag: number } {
    const n = Math.min(timestampsSec.length, rawValues.length, stimulusValues.length);
    if (n < 8) return { lagMs: 0, pearsonAtLag: 0 };

    const meanDtMs =
      n > 1
        ? ((timestampsSec[n - 1] - timestampsSec[0]) / (n - 1)) * 1000
        : 0;
    if (meanDtMs <= 0) return { lagMs: 0, pearsonAtLag: 0 };

    const maxOffset = Math.max(1, Math.floor(maxLagMs / meanDtMs));
    let bestLag = 0;
    let bestAbsR = 0;

    for (let offset = 0; offset <= maxOffset; offset++) {
      // Positive offset: raw[i] aligns with stimulus[i - offset] (raw lags stimulus)
      const rPos = this.calculatePearson(
        rawValues.slice(offset),
        stimulusValues.slice(0, n - offset)
      );
      if (Math.abs(rPos) > bestAbsR) {
        bestAbsR = Math.abs(rPos);
        bestLag = offset;
      }
      if (offset > 0) {
        const rNeg = this.calculatePearson(
          rawValues.slice(0, n - offset),
          stimulusValues.slice(offset)
        );
        if (Math.abs(rNeg) > bestAbsR) {
          bestAbsR = Math.abs(rNeg);
          bestLag = -offset;
        }
      }
    }

    return { lagMs: parseFloat((bestLag * meanDtMs).toFixed(0)), pearsonAtLag: parseFloat(bestAbsR.toFixed(4)) };
  }

  /**
   * Calculate Linear Regression: R², Slope (Scale), and Intercept (Offset).
   */
  public static calculateLinearRegression(raw: number[], stimulus: number[]): { r2: number; slope: number; intercept: number } {
    const n = Math.min(raw.length, stimulus.length);
    if (n < 4) return { r2: 0, slope: 1, intercept: 0 };

    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, sumY2 = 0;
    for (let i = 0; i < n; i++) {
      sumX += raw[i];
      sumY += stimulus[i];
      sumXY += raw[i] * stimulus[i];
      sumX2 += raw[i] * raw[i];
      sumY2 += stimulus[i] * stimulus[i];
    }

    const denominator = n * sumX2 - sumX * sumX;
    if (denominator === 0) return { r2: 0, slope: 1, intercept: 0 };

    const slope = (n * sumXY - sumX * sumY) / denominator;
    const intercept = (sumY - slope * sumX) / n;

    const r = this.calculatePearson(raw, stimulus);
    const r2 = parseFloat((r * r).toFixed(4));

    return {
      r2: isNaN(r2) ? 0 : r2,
      slope: isNaN(slope) ? 1 : parseFloat(slope.toFixed(4)),
      intercept: isNaN(intercept) ? 0 : parseFloat(intercept.toFixed(2))
    };
  }

  /**
   * Rolling Counter Detector: Checks if values increment monotonically modulo 2^N (e.g. 0,1,2..15).
   */
  public static detectRollingCounter(values: number[]): boolean {
    if (values.length < 8) return false;

    let incrementCount = 0;
    let totalTransitions = 0;

    for (let i = 1; i < values.length; i++) {
      const prev = values[i - 1];
      const curr = values[i];
      totalTransitions++;

      // Check if increment is exactly 1 (or wraparound 15 -> 0 or 255 -> 0)
      if (curr === prev + 1 || (prev === 15 && curr === 0) || (prev === 255 && curr === 0)) {
        incrementCount++;
      }
    }

    const counterScore = incrementCount / Math.max(1, totalTransitions);
    // If more than 85% of transitions are pure 1-step increments, it's a counter!
    return counterScore > 0.85;
  }

  /**
   * Checksum / CRC Detector: Checks high-entropy / chaotic changes with zero linear correlation to stimulus.
   */
  public static detectCrcOrChecksum(values: number[], pearsonR: number): boolean {
    if (values.length < 8) return false;

    // If correlation is negligible (|r| < 0.15) but byte changes on almost every single packet
    let distinctValues = new Set(values).size;
    const entropyRatio = distinctValues / values.length;

    return Math.abs(pearsonR) < 0.20 && entropyRatio > 0.65;
  }

  /**
   * Process all captured frames across baseline, stimulus, and recovery to discover candidate signals.
   */
  public static analyzeCapturedFrames(
    frames: CapturedFrameRecord[],
    targetConfig: TargetSignalConfig
  ): SignalCandidate[] {
    if (frames.length < 10) return [];

    // 1. Group frames by CAN ID
    const framesByCanId = new Map<string, CapturedFrameRecord[]>();
    for (const f of frames) {
      if (!framesByCanId.has(f.canIdHex)) {
        framesByCanId.set(f.canIdHex, []);
      }
      framesByCanId.get(f.canIdHex)!.push(f);
    }

    const candidates: SignalCandidate[] = [];

    // Bit-level hypotheses to test: 8-bit, 16-bit, 1-bit, 4-bit, 12-bit
    const hypothesisLayouts = [
      // 8-bit standard bytes (Bytes 0 to 7)
      ...Array.from({ length: 8 }, (_, i) => ({ startBit: i * 8, bitLength: 8, endian: 'Intel' as const, isSigned: false })),
      // 16-bit word pairs (Bytes 0-1, 1-2, 2-3, 3-4, 4-5, 5-6, 6-7) Intel
      ...Array.from({ length: 7 }, (_, i) => ({ startBit: i * 8, bitLength: 16, endian: 'Intel' as const, isSigned: false })),
      // 16-bit signed
      ...Array.from({ length: 4 }, (_, i) => ({ startBit: i * 8, bitLength: 16, endian: 'Intel' as const, isSigned: true })),
      // 4-bit nibbles (for counters / sub-signals)
      ...Array.from({ length: 16 }, (_, i) => ({ startBit: i * 4, bitLength: 4, endian: 'Intel' as const, isSigned: false })),
      // 1-bit switches (for brake switches / flags)
      ...Array.from({ length: 16 }, (_, i) => ({ startBit: i, bitLength: 1, endian: 'Intel' as const, isSigned: false }))
    ];

    // 2. Evaluate each CAN ID and each bit hypothesis
    framesByCanId.forEach((canFrames, canIdHex) => {
      if (canFrames.length < 8) return;

      for (const hyp of hypothesisLayouts) {
        const rawValues = canFrames.map(f => this.extractBitValue(f.payloadHex, hyp.startBit, hyp.bitLength, hyp.endian, hyp.isSigned));
        const stimulusTargets = canFrames.map(f => f.stimulusPercent);

        const minRaw = Math.min(...rawValues);
        const maxRaw = Math.max(...rawValues);
        const deltaMax = maxRaw - minRaw;

        // Skip static bits that never change
        if (deltaMax === 0) continue;

        // Calculate Evidence Metrics
        const pearsonR = this.calculatePearson(rawValues, stimulusTargets);
        const spearmanRho = this.calculateSpearman(rawValues, stimulusTargets);
        const reg = this.calculateLinearRegression(rawValues, stimulusTargets);
        const { lagMs } = this.estimateTimeLagMs(
          canFrames.map(f => f.timestampSec),
          rawValues,
          stimulusTargets
        );

        // Check Baseline vs Recovery
        const baselineFrames = canFrames.filter(f => f.phase === 'BASELINE');
        const recoveryFrames = canFrames.filter(f => f.phase === 'RECOVERY');

        const baseVal = baselineFrames.length > 0
          ? baselineFrames.reduce((acc, f) => acc + this.extractBitValue(f.payloadHex, hyp.startBit, hyp.bitLength, hyp.endian, hyp.isSigned), 0) / baselineFrames.length
          : 0;

        const recVal = recoveryFrames.length > 0
          ? recoveryFrames.reduce((acc, f) => acc + this.extractBitValue(f.payloadHex, hyp.startBit, hyp.bitLength, hyp.endian, hyp.isSigned), 0) / recoveryFrames.length
          : 0;

        const recoveryDelta = Math.abs(recVal - baseVal);

        // Check Counter & CRC Filters
        const isCounter = this.detectRollingCounter(rawValues);
        const isCrc = this.detectCrcOrChecksum(rawValues, pearsonR);

        let rejectionReason: string | undefined;
        if (isCounter) rejectionReason = 'Periyodik 1-Adım Sayaç (Rolling Counter)';
        else if (isCrc) rejectionReason = 'Düzensiz Checksum/CRC Alanı';
        else if (Math.abs(pearsonR) < 0.45 && reg.r2 < 0.35) rejectionReason = 'Eylemle İlişkisiz Düşük Korelasyon';

        // Calculate Deterministic Confidence Score (0.00 - 1.00)
        let confidenceScore = 0;
        if (!isCounter && !isCrc) {
          const wCorr = Math.max(0, Math.abs(pearsonR)) * 0.45;
          const wR2 = Math.max(0, reg.r2) * 0.35;
          const wRec = Math.max(0, 1 - (recoveryDelta / Math.max(1, deltaMax))) * 0.20;
          confidenceScore = parseFloat(Math.min(1.0, Math.max(0, wCorr + wR2 + wRec)).toFixed(3));
        } else {
          confidenceScore = 0.05;
        }

        let confidenceLevel: 'HIGH' | 'MEDIUM' | 'LOW' | 'REJECTED' = 'LOW';
        if (isCounter || isCrc) confidenceLevel = 'REJECTED';
        else if (confidenceScore >= 0.80) confidenceLevel = 'HIGH';
        else if (confidenceScore >= 0.55) confidenceLevel = 'MEDIUM';

        // Sample points for scatter/waveform UI
        const rawSamples: RawSamplePoint[] = canFrames.slice(0, 45).map((f, idx) => ({
          t: f.timestampSec,
          raw: rawValues[idx],
          physical: parseFloat((rawValues[idx] * (reg.slope || 1) + reg.intercept).toFixed(2)),
          stimulusTarget: f.stimulusPercent
        }));

        candidates.push({
          id: `cand-${canIdHex}-${hyp.startBit}-${hyp.bitLength}-${hyp.endian}`,
          canIdHex,
          signalName: targetConfig.name,
          startBit: hyp.startBit,
          bitLength: hyp.bitLength,
          endian: hyp.endian,
          isSigned: hyp.isSigned,
          // REVIEW (negative slope): a measured NEGATIVE slope used to be
          // replaced by a synthetic positive scale — the exported DBC then
          // decoded the signal inverted vs. reality. Keep the measured
          // slope (DBC scale may be negative) so inversely proportional
          // signals decode correctly; the synthetic fallback only applies
          // when the regression has no usable slope at all.
          scale: reg.slope !== 0 ? reg.slope : parseFloat((targetConfig.expectedMax / Math.max(1, deltaMax)).toFixed(4)),
          offset: reg.intercept || 0,
          unit: targetConfig.unit,
          minObserved: minRaw,
          maxObserved: maxRaw,
          deltaMax,
          pearsonR,
          spearmanRho,
          timeLagMs: lagMs,
          regressionR2: reg.r2,
          monotonicityScore: Math.abs(spearmanRho),
          recoveryDelta: parseFloat(recoveryDelta.toFixed(2)),
          isCounter,
          isCrc,
          rejectionReason,
          confidenceScore,
          confidenceLevel,
          status: confidenceLevel === 'HIGH' ? 'INFERRED' : confidenceLevel === 'REJECTED' ? 'REJECTED' : 'INFERRED',
          rawSamples
        });
      }
    });

    // 3. Sort candidates: High confidence first, rejected last
    candidates.sort((a, b) => {
      if (a.isCounter || a.isCrc) return 1;
      if (b.isCounter || b.isCrc) return -1;
      return b.confidenceScore - a.confidenceScore;
    });

    // Return top 8 best candidates
    return candidates.slice(0, 8);
  }

  /**
   * Generate valid Vector .DBC syntax string for a verified signal candidate.
   */
  public static generateDbcString(candidate: SignalCandidate, messageName = 'Discovered_Message'): string {
    const idNum = parseInt(candidate.canIdHex, 16);
    // REVIEW (DBC ID convention): cantools/Vector mark 29-bit messages by
    // setting bit31 of the BO_ id — the old plain decimal form re-imported
    // every discovered J1939 message as an 11-bit frame, breaking DBC round-trip.
    const canIdDec = idNum > 0x7FF ? (idNum | 0x80000000) >>> 0 : idNum;
    const endianBit = candidate.endian === 'Intel' ? '1' : '0';
    const signChar = candidate.isSigned ? '-' : '+';
    // REVIEW (negative scale): physical = raw * scale + offset. With a
    // negative scale (inversely proportional signals) the DBC range must
    // swap — [0|negative] is invalid (min > max) and cantools rejects it.
    const physAt = (raw: number) => raw * candidate.scale + candidate.offset;
    const physMin = candidate.scale >= 0 ? physAt(0) : physAt(candidate.maxObserved);
    const physMax = candidate.scale >= 0 ? physAt(candidate.maxObserved) : physAt(0);

    return `VERSION "1.0"\n\n` +
      `NS_ :\n\n` +
      `BS_:\n\n` +
      `BU_: ECU TESTER\n\n` +
      `BO_ ${canIdDec} ${messageName}: 8 ECU\n` +
      ` SG_ ${candidate.signalName} : ${candidate.startBit}|${candidate.bitLength}@${endianBit}${signChar} (${candidate.scale},${candidate.offset}) [${physMin}|${physMax}] "${candidate.unit}" Vector__XXX\n\n` +
      `CM_ SG_ ${canIdDec} ${candidate.signalName} "Reverse engineered by Universal CAN-Bus AI Signal Discovery (Confidence: ${(candidate.confidenceScore * 100).toFixed(1)}%)";\n`;
  }
}

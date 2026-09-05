/**
 * Hyperliquid 费率计算工具。
 *
 * 按官方公式实现：
 * https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
 */

export interface FeeRates {
  /** maker 费率（小数，0.00045 = 0.045%） */
  makerRate: number;
  /** taker 费率（小数） */
  takerRate: number;
}

/** 官方文档 Tier-0 基础费率 */
export const PERP_BASE_FEES: FeeRates = { makerRate: 0.00015, takerRate: 0.00045 };
export const SPOT_BASE_FEES: FeeRates = { makerRate: 0.0004, takerRate: 0.0007 };

function clampDiscount(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export interface FeeCalcOptions {
  activeReferralDiscount?: number;
  isAlignedQuoteToken?: boolean;
  marketType?: "perp" | "spot";
  isStablePair?: boolean;
  deployerFeeScale?: number;
  growthMode?: boolean;
}

/** 按 Hyperliquid 官方公式计算 maker/taker 费率。 */
export function calculateFeeRates(baseFees: FeeRates, options: FeeCalcOptions = {}): FeeRates {
  const {
    isAlignedQuoteToken = false,
    marketType = "perp",
    isStablePair = false,
    deployerFeeScale = 0,
    growthMode = false,
  } = options;
  const activeReferralDiscount = clampDiscount(options.activeReferralDiscount ?? 0);

  const scaleIfStablePair = marketType === "spot" && isStablePair ? 0.2 : 1;
  let scaleIfHip3 = 1.0;
  let growthModeScale = 1.0;
  let deployerShare = 0.0;

  if (marketType === "perp") {
    if (deployerFeeScale < 1) {
      scaleIfHip3 = deployerFeeScale + 1;
      deployerShare = deployerFeeScale / (1 + deployerFeeScale);
    } else {
      scaleIfHip3 = deployerFeeScale * 2;
      deployerShare = 0.5;
    }
    growthModeScale = growthMode ? 0.1 : 1.0;
  }

  let makerPercentage = baseFees.makerRate * 100 * scaleIfStablePair * growthModeScale;
  if (makerPercentage > 0) {
    makerPercentage *= scaleIfHip3 * (1 - activeReferralDiscount);
  } else {
    const makerRebateScaleIfAligned = isAlignedQuoteToken
      ? (1 - deployerShare) * 1.5 + deployerShare
      : 1;
    makerPercentage *= makerRebateScaleIfAligned;
  }

  let takerPercentage =
    baseFees.takerRate * 100 * scaleIfStablePair * scaleIfHip3 * growthModeScale *
    (1 - activeReferralDiscount);
  if (isAlignedQuoteToken) {
    const takerScaleIfAligned = (1 - deployerShare) * 0.8 + deployerShare;
    takerPercentage *= takerScaleIfAligned;
  }

  return { makerRate: makerPercentage / 100, takerRate: takerPercentage / 100 };
}

/** Tier-0 永续费率便捷入口。 */
export function defaultPerpFeeRates(options: Omit<FeeCalcOptions, "marketType" | "isStablePair"> = {}): FeeRates {
  return calculateFeeRates(PERP_BASE_FEES, { ...options, marketType: "perp" });
}

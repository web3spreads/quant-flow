/**
 * 网格计算数学引擎（价位、单格金额、自适应格数；含 Hyperliquid 保证金占用口径）
 */

import { Decimal, toDecimal } from "./precision.js";

/** 层级状态机 */
export enum GridLevelState {
  IDLE = "IDLE", // 空闲，等待挂开仓单
  OPEN_PENDING = "OPEN_PENDING", // 开仓单已挂，等待成交
  OPEN_FILLED = "OPEN_FILLED", // 开仓成交，等待挂平仓单
  CLOSE_PENDING = "CLOSE_PENDING", // 平仓单已挂，等待成交
  COMPLETED = "COMPLETED", // 开平仓均完成 -> 即将 reset 回 IDLE
}

interface GridLevelDict {
  id: string;
  price: string;
  amount: string;
  side: string;
  state: string;
  open_order_id: number | null;
  open_fill_price: string | null;
  open_fill_amount: string | null;
  open_fill_time: number | null;
  close_order_id: number | null;
  close_fill_price: string | null;
  close_fill_amount: string | null;
  close_fill_time: number | null;
  round_trip_count: number;
  cumulative_pnl: string;
}

/** 单个网格层级 */
export class GridLevel {
  id: string;
  price: Decimal; // 该层挂单价格
  amount: Decimal; // 该层下单金额（quote，USD）
  side: "LONG" | "SHORT";
  state: GridLevelState;

  // 订单追踪
  openOrderId: number | null = null;
  openFillPrice: Decimal | null = null;
  openFillAmount: Decimal | null = null; // base 数量
  openFillTime: number | null = null;

  closeOrderId: number | null = null;
  closeFillPrice: Decimal | null = null;
  closeFillAmount: Decimal | null = null;
  closeFillTime: number | null = null;

  // 统计
  roundTripCount = 0;
  cumulativePnl: Decimal = new Decimal(0);

  constructor(init: {
    id: string;
    price: Decimal;
    amount: Decimal;
    side: "LONG" | "SHORT";
    state?: GridLevelState;
  }) {
    this.id = init.id;
    this.price = init.price;
    this.amount = init.amount;
    this.side = init.side;
    this.state = init.state ?? GridLevelState.IDLE;
  }

  /** 完成一轮后重置，保留统计数据。 */
  reset(): void {
    this.state = GridLevelState.IDLE;
    this.openOrderId = null;
    this.openFillPrice = null;
    this.openFillAmount = null;
    this.openFillTime = null;
    this.closeOrderId = null;
    this.closeFillPrice = null;
    this.closeFillAmount = null;
    this.closeFillTime = null;
  }

  /** 序列化为可 JSON 持久化的字典（写入 grid_state.json）。 */
  toDict(): GridLevelDict {
    return {
      id: this.id,
      price: this.price.toString(),
      amount: this.amount.toString(),
      side: this.side,
      state: this.state,
      open_order_id: this.openOrderId,
      open_fill_price: this.openFillPrice === null ? null : this.openFillPrice.toString(),
      open_fill_amount: this.openFillAmount === null ? null : this.openFillAmount.toString(),
      open_fill_time: this.openFillTime,
      close_order_id: this.closeOrderId,
      close_fill_price: this.closeFillPrice === null ? null : this.closeFillPrice.toString(),
      close_fill_amount: this.closeFillAmount === null ? null : this.closeFillAmount.toString(),
      close_fill_time: this.closeFillTime,
      round_trip_count: this.roundTripCount,
      cumulative_pnl: this.cumulativePnl.toString(),
    };
  }

  /** 从字典反序列化恢复层级（崩溃恢复）。 */
  static fromDict(data: Partial<GridLevelDict> & { id: string; price: unknown; amount: unknown; side: string }): GridLevel {
    const level = new GridLevel({
      id: data.id,
      price: toDecimal(data.price),
      amount: toDecimal(data.amount),
      side: data.side === "SHORT" ? "SHORT" : "LONG",
      state: (Object.values(GridLevelState) as string[]).includes(String(data.state))
        ? (data.state as GridLevelState)
        : GridLevelState.IDLE,
    });
    level.openOrderId = data.open_order_id ?? null;
    level.openFillPrice = data.open_fill_price != null ? toDecimal(data.open_fill_price) : null;
    level.openFillAmount = data.open_fill_amount != null ? toDecimal(data.open_fill_amount) : null;
    level.openFillTime = data.open_fill_time ?? null;
    level.closeOrderId = data.close_order_id ?? null;
    level.closeFillPrice = data.close_fill_price != null ? toDecimal(data.close_fill_price) : null;
    level.closeFillAmount = data.close_fill_amount != null ? toDecimal(data.close_fill_amount) : null;
    level.closeFillTime = data.close_fill_time ?? null;
    level.roundTripCount = data.round_trip_count ?? 0;
    level.cumulativePnl = toDecimal(data.cumulative_pnl ?? "0");
    return level;
  }
}

/**
 * 从下单响应中提取订单 ID，兼容 resting（挂单中）和 filled（立即成交）两种状态。
 * 提取失败时返回 null。
 */
export function extractOrderId(limitOrderRes: unknown): number | null {
  try {
    const statuses = (limitOrderRes as any)?.response?.data?.statuses;
    if (!Array.isArray(statuses) || statuses.length === 0) return null;
    const status = statuses[0];
    if (status && typeof status === "object") {
      if ("resting" in status) return status.resting.oid ?? null;
      if ("filled" in status) return status.filled.oid ?? null;
    }
    return null;
  } catch {
    return null;
  }
}

interface GridConfigResult {
  action: "UPDATE_GRID" | "INSUFFICIENT_CAPITAL";
  mode: string;
  lower_price?: number;
  upper_price?: number;
  grid_num?: number;
  amount_per_grid?: number;
  tp_ratio?: number;
  sl_ratio?: number;
  reason?: string;
  required_balance?: number;
  [key: string]: unknown;
}

/**
 * 针对 Hyperliquid 保证金占用逻辑进行强力修正。
 *
 * 【终极修正逻辑】：
 * Hyperliquid 测试网对于限价单的保证金检查极其严苛。
 * 如果我们有 $77 可用余额，10x 杠杆，理论总额度 $770。
 * 但如果一次性挂 6-8 个格子，系统会因为并行订单的潜在占用导致 Insufficient margin。
 *
 * 修正方案：将单格金额大幅度压缩，确保 (单格金额 * 格子数) 远低于 (可用余额 * 杠杆)。
 *
 * 内部使用 Decimal 精确计算，输出为 number（兼容 API 边界）。
 */
export function calculateGridConfig(options: {
  currentPrice: number;
  availableBalance: number;
  mode?: string;
  widthPct?: number;
  gridNum?: number;
  leverage?: number;
  adaptiveSizing?: boolean;
  minOrderNotionalUsd?: number;
  minGridNum?: number;
  /** maker 费率（小数），用于止盈下限；缺省用 Tier-0 基础费率 */
  makerFeeRate?: number;
  /** 网格中心相对现价的位移（小数，负=下移）。库存倾斜报价用 */
  centerShiftPct?: number;
}): GridConfigResult {
  const mode = options.mode ?? "NEUTRAL";
  const widthPct = options.widthPct ?? 0.05;
  const minOrderNotionalUsd = options.minOrderNotionalUsd ?? 11.0;
  const minGridNum = options.minGridNum ?? 3;
  // 参数校验（gridNum/leverage 来自 AI 输出，需防御非法值）
  let gridNum = Math.max(1, Math.trunc(options.gridNum ?? 6));
  const leverage = Math.max(1, Math.trunc(options.leverage ?? 10));

  // Decimal 精确计算
  const dPrice = toDecimal(options.currentPrice);
  const dBalance = toDecimal(options.availableBalance);
  const dWidth = toDecimal(widthPct);
  let dGridNum = new Decimal(gridNum);
  const dLeverage = new Decimal(leverage);

  // 1. 计算区间。centerShiftPct 是库存倾斜：持多时中心下移，买单因此更远离市价
  // （不再急于加仓）、卖单更贴近（更快减仓），反之亦然。位移只挪中心，不改宽度。
  const dCenter = dPrice.mul(new Decimal(1).plus(toDecimal(options.centerShiftPct ?? 0)));
  let lowerPrice: Decimal;
  let upperPrice: Decimal;
  if (mode === "LONG") {
    lowerPrice = dCenter.mul(new Decimal(1).minus(dWidth));
    upperPrice = dCenter.mul("1.01");
  } else if (mode === "SHORT") {
    lowerPrice = dCenter.mul("0.99");
    upperPrice = dCenter.mul(new Decimal(1).plus(dWidth));
  } else {
    // NEUTRAL
    lowerPrice = dCenter.mul(new Decimal(1).minus(dWidth.div(2)));
    upperPrice = dCenter.mul(new Decimal(1).plus(dWidth.div(2)));
  }

  // 2. 【核心修正】极其保守的金额分配
  const conservativeSafety = new Decimal("0.4");
  const totalNotionalCap = dBalance.mul(dLeverage).mul(conservativeSafety);
  let amountPerGrid = totalNotionalCap.div(dGridNum);

  if (options.adaptiveSizing) {
    // 自适应仓位：单格金额与真实净值挂钩。
    // 历史行为的 $15.5 硬下限会在小账户上把总名义额抬到净值的十几倍
    // （线上 $7.71 账户被抬成 $124 名义敞口 ≈ 16 倍杠杆），保守系数 0.4 被完全反转。
    // 这里改为：单格不足最小名义额时【减少格数】而非抬高单格金额；
    // 格数低于下限说明资金撑不起最小网格，直接拒绝布单。
    let dMinOrder = toDecimal(minOrderNotionalUsd);
    if (amountPerGrid.lt(dMinOrder)) {
      if (dMinOrder.lte(0)) dMinOrder = new Decimal(11);
      const reducedNum = totalNotionalCap.div(dMinOrder).toDecimalPlaces(0, Decimal.ROUND_DOWN).toNumber();
      if (reducedNum < Math.max(2, Math.trunc(minGridNum))) {
        const requiredBalance = dMinOrder
          .mul(new Decimal(minGridNum))
          .div(dLeverage.mul(conservativeSafety));
        return {
          action: "INSUFFICIENT_CAPITAL",
          mode,
          reason:
            `资金不足以支撑最小网格: 可用 $${dBalance.toNumber().toFixed(2)} × 杠杆 ${leverage} × ` +
            `安全系数 0.4 = 总额度 $${totalNotionalCap.toNumber().toFixed(2)}，` +
            `按单格最小 $${dMinOrder.toNumber().toFixed(2)} 只够 ${reducedNum} 格 ` +
            `(< 最少 ${minGridNum} 格)。需要余额 ≥ $${requiredBalance.toNumber().toFixed(2)}`,
          required_balance: requiredBalance.toDecimalPlaces(2, Decimal.ROUND_HALF_UP).toNumber(),
        };
      }
      gridNum = reducedNum;
      dGridNum = new Decimal(gridNum);
      amountPerGrid = totalNotionalCap.div(dGridNum);
    }
  } else {
    // 历史行为（默认）：硬编码钳制单格金额。仅对 ≳$100 的账户成立，
    // 小账户上下限会反噬保守性——新部署建议启用自适应仓位。
    // 强制兜底：如果算出来太大，强制压低到 25.5 USD
    if (amountPerGrid.gt(30)) amountPerGrid = new Decimal("25.5");
    // Hyperliquid 最小限制
    if (amountPerGrid.lt("15.5")) amountPerGrid = new Decimal("15.5");
  }

  // 3. 计算止盈止损
  // 止盈：每格宽度的 80%，下限为双边 maker 费 × 2 倍缓冲（费率来自账户实际分档，
  // 缺省 Tier-0 maker 0.015%——历史写死的 0.035% 是过期数字）
  const feeRate = toDecimal(options.makerFeeRate ?? 0.00015);
  const minTpRatio = feeRate.mul(4); // 双边手续费 x 2 倍缓冲
  const rawTpRatio = dWidth.div(dGridNum).mul("0.8");
  const tpRatio = Decimal.max(rawTpRatio, minTpRatio);

  // 止损：控制在止盈的 2 倍内，避免单次止损亏损过多
  const maxSlRatio = new Decimal("0.02");
  const minSlRatio = new Decimal("0.005");
  const slRatio = Decimal.min(Decimal.max(tpRatio.mul(2), minSlRatio), maxSlRatio);

  // 输出为 number（API 边界兼容），保留合理精度
  return {
    action: "UPDATE_GRID",
    lower_price: lowerPrice.toDecimalPlaces(1, Decimal.ROUND_HALF_UP).toNumber(),
    upper_price: upperPrice.toDecimalPlaces(1, Decimal.ROUND_HALF_UP).toNumber(),
    grid_num: gridNum,
    amount_per_grid: amountPerGrid.toDecimalPlaces(2, Decimal.ROUND_HALF_UP).toNumber(),
    tp_ratio: tpRatio.toDecimalPlaces(4, Decimal.ROUND_HALF_UP).toNumber(),
    sl_ratio: slRatio.toDecimalPlaces(4, Decimal.ROUND_HALF_UP).toNumber(),
    mode,
  };
}

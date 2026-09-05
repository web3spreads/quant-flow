/**
 * 网格级 PnL 追踪器
 *
 * 跟踪已实现/未实现盈亏、手续费，为 Triple Barrier 风控提供 netPnlPct。
 */

import { Decimal, toDecimal } from "../utils/precision.js";
import { GridLevel, GridLevelState } from "../utils/gridMath.js";

export class GridPnLTracker {
  // 已实现（完成开平仓轮回的）
  realizedBuyVolume = new Decimal(0);
  realizedSellVolume = new Decimal(0);
  realizedFees = new Decimal(0);
  realizedPnl = new Decimal(0);
  completedRoundTrips = 0;

  // 费率配置
  makerFeeRate = new Decimal("0.00035");

  /** 记录一次完成的开平仓轮回，返回本轮净盈亏。 */
  recordRoundTrip(level: GridLevel): Decimal {
    if (level.openFillPrice === null || level.closeFillPrice === null) return new Decimal(0);
    if (level.openFillAmount === null) return new Decimal(0);

    const openPrice = level.openFillPrice;
    const closePrice = level.closeFillPrice;
    const amount = level.openFillAmount; // base 数量

    let buyCost: Decimal;
    let sellRevenue: Decimal;
    let grossPnl: Decimal;
    if (level.side === "LONG") {
      // 做多：买入 open，卖出 close
      buyCost = openPrice.mul(amount);
      sellRevenue = closePrice.mul(amount);
      grossPnl = sellRevenue.minus(buyCost);
    } else {
      // 做空：卖出 open，买入 close
      sellRevenue = openPrice.mul(amount);
      buyCost = closePrice.mul(amount);
      grossPnl = sellRevenue.minus(buyCost);
    }

    // 手续费：开仓 + 平仓各一次（限价单按 maker 费率）
    const openFee = openPrice.mul(amount).mul(this.makerFeeRate);
    const closeFee = closePrice.mul(amount).mul(this.makerFeeRate);
    const totalFee = openFee.plus(closeFee);

    const netPnl = grossPnl.minus(totalFee);

    this.realizedBuyVolume = this.realizedBuyVolume.plus(buyCost);
    this.realizedSellVolume = this.realizedSellVolume.plus(sellRevenue);
    this.realizedFees = this.realizedFees.plus(totalFee);
    this.realizedPnl = this.realizedPnl.plus(netPnl);
    this.completedRoundTrips += 1;

    // 写回 level
    level.cumulativePnl = level.cumulativePnl.plus(netPnl);
    level.roundTripCount += 1;

    return netPnl;
  }

  /** 计算所有持仓中层级的未实现盈亏。 */
  calculateUnrealizedPnl(levels: GridLevel[], currentPrice: Decimal): Decimal {
    let unrealized = new Decimal(0);
    for (const level of levels) {
      if (level.state !== GridLevelState.OPEN_FILLED && level.state !== GridLevelState.CLOSE_PENDING) continue;
      if (level.openFillPrice === null || level.openFillAmount === null) continue;
      const amount = level.openFillAmount;
      const entry = level.openFillPrice;
      if (level.side === "LONG") unrealized = unrealized.plus(currentPrice.minus(entry).mul(amount));
      else unrealized = unrealized.plus(entry.minus(currentPrice).mul(amount));
      // 扣除预估平仓手续费
      unrealized = unrealized.minus(currentPrice.mul(amount).mul(this.makerFeeRate));
    }
    return unrealized;
  }

  /** 总 PnL = 已实现 + 未实现 */
  private getNetPnl(levels: GridLevel[], currentPrice: Decimal): Decimal {
    return this.realizedPnl.plus(this.calculateUnrealizedPnl(levels, currentPrice));
  }

  /** PnL 百分比（相对于总投入） */
  getNetPnlPct(levels: GridLevel[], currentPrice: Decimal, totalInvestment: Decimal): Decimal {
    if (totalInvestment.lte(0)) return new Decimal(0);
    return this.getNetPnl(levels, currentPrice).div(totalInvestment);
  }

  /** 完整的 PnL 报告 */
  getSummary(levels: GridLevel[], currentPrice: Decimal, totalInvestment: Decimal): Record<string, Decimal | number> {
    const unrealized = this.calculateUnrealizedPnl(levels, currentPrice);
    const netPnl = this.realizedPnl.plus(unrealized);
    const netPnlPct = totalInvestment.gt(0) ? netPnl.div(totalInvestment) : new Decimal(0);

    const openLevels = levels.filter(
      (l) => l.state === GridLevelState.OPEN_FILLED || l.state === GridLevelState.CLOSE_PENDING,
    );
    let totalCost = new Decimal(0);
    let totalAmount = new Decimal(0);
    for (const level of openLevels) {
      if (level.openFillPrice && level.openFillAmount) {
        totalCost = totalCost.plus(level.openFillPrice.mul(level.openFillAmount));
        totalAmount = totalAmount.plus(level.openFillAmount);
      }
    }
    const avgEntry = totalAmount.gt(0) ? totalCost.div(totalAmount) : new Decimal(0);

    return {
      realized_pnl: this.realizedPnl,
      unrealized_pnl: unrealized,
      net_pnl: netPnl,
      net_pnl_pct: netPnlPct,
      total_fees: this.realizedFees,
      completed_round_trips: this.completedRoundTrips,
      open_positions: openLevels.length,
      avg_entry_price: avgEntry,
      current_price: currentPrice,
      grid_efficiency: totalInvestment.gt(0) ? this.realizedPnl.div(totalInvestment) : new Decimal(0),
    };
  }

  /** 序列化为可 JSON 持久化的字典。 */
  toDict(): Record<string, string | number> {
    return {
      realized_pnl: this.realizedPnl.toString(),
      realized_buy_volume: this.realizedBuyVolume.toString(),
      realized_sell_volume: this.realizedSellVolume.toString(),
      realized_fees: this.realizedFees.toString(),
      completed_round_trips: this.completedRoundTrips,
      maker_fee_rate: this.makerFeeRate.toString(),
    };
  }

  /** 从字典反序列化恢复。 */
  static fromDict(data: Record<string, unknown>): GridPnLTracker {
    const tracker = new GridPnLTracker();
    tracker.realizedPnl = toDecimal(data.realized_pnl ?? "0");
    tracker.realizedBuyVolume = toDecimal(data.realized_buy_volume ?? "0");
    tracker.realizedSellVolume = toDecimal(data.realized_sell_volume ?? "0");
    tracker.realizedFees = toDecimal(data.realized_fees ?? "0");
    tracker.completedRoundTrips = Math.trunc(Number(data.completed_round_trips ?? 0)) || 0;
    tracker.makerFeeRate = toDecimal(data.maker_fee_rate ?? "0.00035");
    return tracker;
  }
}

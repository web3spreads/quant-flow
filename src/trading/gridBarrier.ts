/**
 * Triple Barrier 网格级风控
 *
 * 实现网格级别的三重屏障（止损 + 止盈 + 时间限制 + 追踪止损），
 * 作为单层 TP/SL trigger 之上的全局兜底保护。
 */

import { Decimal, toDecimal } from "../utils/precision.js";

export class TripleBarrierConfig {
  /** 止损：当总亏损百分比达到或超过此值时触发全部平仓（PnL <= -stopLossPct） */
  stopLossPct: Decimal | null = new Decimal("0.05");
  /** 止盈：整个网格的净 PnL% 高于此值 -> 全部平仓获利了结 */
  takeProfitPct: Decimal | null = new Decimal("0.10");
  /**
   * 时间限制：网格运行超过此秒数 -> 全部平仓。默认关（null）。
   * 历史默认 4h：每 4 小时把整张网格的库存市价倒掉一次，被高频重建重置时钟
   * 掩盖而未触发；重建一降频它就成为固定的 taker 出血口。需要时按配置显式开启。
   */
  timeLimitSeconds: number | null = null;
  /** 追踪止损：默认关（激活阈值 null）。网格单轮止盈仅 ~0.3%，+3% 激活/1% 回撤对它是噪声级触发 */
  trailingStopActivationPct: Decimal | null = null;
  trailingStopDeltaPct: Decimal | null = new Decimal("0.01");
  /** 限价保护：价格超出此范围 -> 触发平仓 */
  priceLowerLimit: Decimal | null = null;
  priceUpperLimit: Decimal | null = null;

  /** 从配置的 barrier 段构建。 */
  static fromConfig(config: Record<string, unknown> | null | undefined): TripleBarrierConfig {
    const barrier = new TripleBarrierConfig();
    if (!config) return barrier;
    const dec = (key: string, assign: (v: Decimal | null) => void) => {
      if (key in config) assign(config[key] === null ? null : toDecimal(config[key]));
    };
    dec("stop_loss_pct", (v) => (barrier.stopLossPct = v));
    dec("take_profit_pct", (v) => (barrier.takeProfitPct = v));
    if ("time_limit_seconds" in config) {
      barrier.timeLimitSeconds = config.time_limit_seconds === null ? null : Math.trunc(Number(config.time_limit_seconds));
    }
    dec("trailing_stop_activation_pct", (v) => (barrier.trailingStopActivationPct = v));
    dec("trailing_stop_delta_pct", (v) => (barrier.trailingStopDeltaPct = v));
    dec("price_lower_limit", (v) => (barrier.priceLowerLimit = v));
    dec("price_upper_limit", (v) => (barrier.priceUpperLimit = v));
    return barrier;
  }

  /** 看板展示用的序列化。 */
  toDict(): Record<string, unknown> {
    return {
      stop_loss_pct: this.stopLossPct?.toNumber() ?? null,
      take_profit_pct: this.takeProfitPct?.toNumber() ?? null,
      time_limit_seconds: this.timeLimitSeconds,
      trailing_stop_activation_pct: this.trailingStopActivationPct?.toNumber() ?? null,
      trailing_stop_delta_pct: this.trailingStopDeltaPct?.toNumber() ?? null,
      price_lower_limit: this.priceLowerLimit?.toNumber() ?? null,
      price_upper_limit: this.priceUpperLimit?.toNumber() ?? null,
    };
  }
}

/** 三重屏障监控器 */
export class GridBarrierMonitor {
  private trailingStopHighWater: Decimal | null = null;

  constructor(
    public config: TripleBarrierConfig,
    public startTime: number,
  ) {}

  /**
   * 检查是否触发屏障。
   * 返回 null = 安全，返回字符串 = 触发原因。
   * 优先级：止损 > 限价 > 时限 > 追踪止损 > 止盈
   */
  check(currentPrice: Decimal, netPnlPct: Decimal, currentTime: number): string | null {
    const cfg = this.config;
    const pct = (d: Decimal) => `${d.mul(100).toNumber().toFixed(2)}%`;

    // 1. 止损
    if (cfg.stopLossPct !== null && netPnlPct.lte(cfg.stopLossPct.neg())) {
      return `STOP_LOSS: PnL ${pct(netPnlPct)} <= -${pct(cfg.stopLossPct)}`;
    }
    // 2. 限价保护
    if (cfg.priceLowerLimit !== null && currentPrice.lte(cfg.priceLowerLimit)) {
      return `PRICE_LIMIT: ${currentPrice} <= ${cfg.priceLowerLimit}`;
    }
    if (cfg.priceUpperLimit !== null && currentPrice.gte(cfg.priceUpperLimit)) {
      return `PRICE_LIMIT: ${currentPrice} >= ${cfg.priceUpperLimit}`;
    }
    // 3. 时间限制
    if (cfg.timeLimitSeconds !== null) {
      const elapsed = currentTime - this.startTime;
      if (elapsed >= cfg.timeLimitSeconds) {
        return `TIME_LIMIT: ${elapsed.toFixed(0)}s >= ${cfg.timeLimitSeconds}s`;
      }
    }
    // 4. 追踪止损
    if (cfg.trailingStopActivationPct !== null && cfg.trailingStopDeltaPct !== null) {
      const trigger = this.checkTrailingStop(netPnlPct);
      if (trigger) return trigger;
    }
    // 5. 整体止盈
    if (cfg.takeProfitPct !== null && netPnlPct.gte(cfg.takeProfitPct)) {
      return `TAKE_PROFIT: PnL ${pct(netPnlPct)} >= ${pct(cfg.takeProfitPct)}`;
    }
    return null;
  }

  private checkTrailingStop(netPnlPct: Decimal): string | null {
    const cfg = this.config;
    if (this.trailingStopHighWater === null) {
      // 尚未激活：PnL 首次达到激活阈值
      if (netPnlPct.gte(cfg.trailingStopActivationPct!)) this.trailingStopHighWater = netPnlPct;
      return null;
    }
    // 已激活：更新高水位
    if (netPnlPct.gt(this.trailingStopHighWater)) this.trailingStopHighWater = netPnlPct;
    // 检查回撤
    const drawdown = this.trailingStopHighWater.minus(netPnlPct);
    if (drawdown.gte(cfg.trailingStopDeltaPct!)) {
      const pct = (d: Decimal) => `${d.mul(100).toNumber().toFixed(2)}%`;
      return `TRAILING_STOP: 高水位 ${pct(this.trailingStopHighWater)} 回撤 ${pct(drawdown)} >= ${pct(cfg.trailingStopDeltaPct!)}`;
    }
    return null;
  }

  /** 重置监控器（全量重建后调用）。 */
  reset(startTime: number): void {
    this.startTime = startTime;
    this.trailingStopHighWater = null;
  }
}

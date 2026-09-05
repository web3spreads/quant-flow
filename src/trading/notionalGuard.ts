/**
 * 主网名义额闸：套在交易所客户端外面的装饰器，主网双重闸的第一道。
 *
 * 为什么放在客户端层：网格的开仓路径有两条（整张网格批量 post-only 提交、增量
 * 补挂单个层级），未来还可能增加；策略层每加一条路径就要记得再拦一次，而所有
 * 路径最终都经过 `placeLimitOrders`。在这里拦，新增敞口就没有绕行的口子。
 *
 * 规则：
 * - reduce_only 条目永远放行——退出通道绝不受闸门影响（平仓单、保护单、紧急平仓）；
 * - 开仓条目：已用名义额（全部持仓 + 非 reduce_only 挂单）+ 本批拟挂名义额 > 上限，
 *   本批开仓条目**全部**拒绝（不做部分放行，保守且简单）；
 * - 持仓/挂单查询失败按「未知」处理，fail-closed 拒绝开仓——把「查不到」当「没有」
 *   正是这个仓库里多次事故的共同根因；
 * - 拒绝回执与交易所拒单同构（外层 ok、statuses[].error），下游 checkOrderSuccess /
 *   orderStatuses 无需知道闸门存在。
 */

import { HyperliquidClient, type Dict, type ExchangeClientLike, type LimitOrderSpec, type LimitTif } from "./client.js";
import type { FeeRates } from "../fees.js";
import type { TradingLogger } from "../logger.js";
import { clock } from "../utils/clock.js";

export interface NotionalSnapshot {
  cap_usd: number;
  /** 已用名义额；查询失败时为 null */
  used_usd: number | null;
  query_failed: boolean;
  /** 快照时间（毫秒） */
  at: number;
}

/**
 * 已用名义额（USD）= Σ|持仓名义额| + Σ 非 reduce_only、非触发单的挂单 limitPx×sz。
 * 触发单在本仓库一律 reduce_only（TP/SL），但 frontendOpenOrders 的字段形态不一，
 * 按订单形态再排除一次。
 */
export function usedNotionalUsd(positions: Dict[], openOrders: Dict[]): number {
  let used = 0;
  for (const p of positions) {
    const pv = Number(p?.positionValue);
    if (Number.isFinite(pv)) used += Math.abs(pv);
    else used += Math.abs(Number(p?.szi) || 0) * (Number(p?.entryPx) || 0);
  }
  for (const o of openOrders) {
    if (!o || o.reduceOnly) continue;
    if (HyperliquidClient.isTriggerLikeOrder(o)) continue;
    used += Math.abs((Number(o.limitPx) || 0) * (Number(o.sz) || 0));
  }
  return used;
}

export class MainnetNotionalGuard implements ExchangeClientLike {
  readonly capUsd: number;
  private readonly inner: ExchangeClientLike;
  private readonly logger?: TradingLogger;
  private last: NotionalSnapshot | null = null;

  constructor(inner: ExchangeClientLike, options: { capUsd: number; logger?: TradingLogger }) {
    if (!(Number.isFinite(options.capUsd) && options.capUsd > 0)) {
      throw new Error(`主网名义额上限必须 > 0，实际为 ${options.capUsd}`);
    }
    this.inner = inner;
    this.capUsd = options.capUsd;
    this.logger = options.logger;
  }

  get address(): string {
    return this.inner.address;
  }
  get testnet(): boolean {
    return this.inner.testnet;
  }

  /** 最近一次闸门判定（下单时刷新；看板展示用）。 */
  snapshot(): NotionalSnapshot | null {
    return this.last;
  }

  /** 主动取数算一次已用名义额（不下单）。 */
  async measure(): Promise<NotionalSnapshot> {
    const [positions, openOrders] = await Promise.all([this.inner.getPositions(), this.inner.getOpenOrders(false)]);
    if (positions === null || openOrders === null) {
      this.last = { cap_usd: this.capUsd, used_usd: null, query_failed: true, at: clock.now() };
    } else {
      this.last = { cap_usd: this.capUsd, used_usd: usedNotionalUsd(positions, openOrders), query_failed: false, at: clock.now() };
    }
    return this.last;
  }

  // ── 闸门 ──────────────────────────────────────────────────────────────

  async placeLimitOrder(
    symbol: string,
    isBuy: boolean,
    size: number,
    price: number,
    reduceOnly = false,
    tif: LimitTif = "Gtc",
  ): Promise<Dict> {
    // 必须经本类的批量入口：直接委托 inner.placeLimitOrder 会绕过闸门
    return this.placeLimitOrders([{ symbol, isBuy, size, price, reduceOnly, tif }]);
  }

  async placeLimitOrders(specs: LimitOrderSpec[]): Promise<Dict> {
    const opening = specs.filter((s) => !s.reduceOnly);
    if (!opening.length) return this.inner.placeLimitOrders(specs);

    const incoming = opening.reduce((sum, s) => sum + Math.abs(Number(s.price) * Number(s.size)), 0);
    const snap = await this.measure();
    let reason: string | null = null;
    if (snap.query_failed) {
      reason = "主网名义额闸：持仓/挂单查询失败，无法确认已用名义额，拒绝新增敞口（fail-closed）";
    } else if ((snap.used_usd ?? 0) + incoming > this.capUsd) {
      reason =
        `主网名义额闸：已用 $${(snap.used_usd ?? 0).toFixed(2)} + 拟挂 $${incoming.toFixed(2)}` +
        ` > 上限 $${this.capUsd.toFixed(2)}，拒绝开仓单`;
    }
    if (reason === null) return this.inner.placeLimitOrders(specs);

    const passing = specs.filter((s) => s.reduceOnly);
    this.logger?.printCritical(
      `🛑 ${reason}（本批 ${opening.length} 个开仓条目被拒，${passing.length} 个 reduce_only 条目照常提交）`,
    );
    // reduce_only 条目仍真正提交；回执按原顺序合并，开仓条目填入闸门错误
    const passViews = passing.length ? HyperliquidClient.orderStatuses(await this.inner.placeLimitOrders(passing), passing.length) : [];
    let passIdx = 0;
    const statuses: Dict[] = specs.map((s) => {
      if (!s.reduceOnly) return { error: reason };
      const view = passViews[passIdx++];
      if (!view) return { error: "没有返回订单状态" };
      if (!view.ok) return { error: view.error ?? "未知订单状态" };
      if (view.filled) return { filled: { oid: view.oid, avgPx: String(view.avgPx ?? ""), totalSz: String(view.totalSz ?? "") } };
      return { resting: { oid: view.oid } };
    });
    return {
      status: "ok",
      response: { type: "order", data: { statuses } },
      notional_guard: { rejected: opening.length, reason, cap_usd: this.capUsd, used_usd: snap.used_usd },
    };
  }

  // ── 透传（退出通道与只读查询一律不拦） ─────────────────────────────────

  getBalance(): Promise<Dict | null> {
    return this.inner.getBalance();
  }
  getPositions(): Promise<Dict[] | null> {
    return this.inner.getPositions();
  }
  getOpenOrders(includeTrigger = false): Promise<Dict[] | null> {
    return this.inner.getOpenOrders(includeTrigger);
  }
  getAssetInfo(symbol: string): Promise<Dict | null> {
    return this.inner.getAssetInfo(symbol);
  }
  getCurrentPrice(symbol: string): Promise<number | null> {
    return this.inner.getCurrentPrice(symbol);
  }
  formatPrice(symbol: string, price: number): Promise<number> {
    return this.inner.formatPrice(symbol, price);
  }
  roundSize(symbol: string, size: number): Promise<number> {
    return this.inner.roundSize(symbol, size);
  }
  /** 触发单在本仓库恒为 reduce_only（TP/SL），属退出通道 */
  placeTpslOrder(options: {
    symbol: string;
    triggerPrice: number;
    isBuy: boolean;
    size: number;
    isTp?: boolean;
    slSlippage?: number;
  }): Promise<Dict> {
    return this.inner.placeTpslOrder(options);
  }
  cancelOrder(symbol: string, oid: number): Promise<Dict> {
    return this.inner.cancelOrder(symbol, oid);
  }
  cancelOrders(symbol: string, oids: number[]): Promise<Dict> {
    return this.inner.cancelOrders(symbol, oids);
  }
  updateLeverage(symbol: string, leverage: number, isCross = true): Promise<Dict> {
    return this.inner.updateLeverage(symbol, leverage, isCross);
  }
  getCandles(symbol: string, interval = "15m", startTime?: number, endTime?: number): Promise<Dict[] | null> {
    return this.inner.getCandles(symbol, interval, startTime, endTime);
  }
  userFills(): Promise<Dict[]> {
    return this.inner.userFills();
  }
  emergencyCloseWithRetry(
    symbol: string,
    size: number | null,
    options: { reason: string; maxRetries?: number },
  ): Promise<[boolean, Dict | null]> {
    return this.inner.emergencyCloseWithRetry(symbol, size, options);
  }
  closePosition(symbol: string, size: number | null = null): Promise<Dict> {
    return this.inner.closePosition(symbol, size);
  }
  fetchUserFeeRates(options?: { isAlignedQuoteToken?: boolean; deployerFeeScale?: number; growthMode?: boolean }): Promise<FeeRates> {
    return this.inner.fetchUserFeeRates(options);
  }
}

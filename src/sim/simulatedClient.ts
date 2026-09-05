/**
 * 模拟交易所客户端（回测）：实现 ExchangeClientLike，用历史 K 线撮合。
 *
 * 与生产客户端共享同一条策略/订单管理代码路径——GridStrategy、GridManager、
 * OrderManager 一行不改地跑在它上面，回测结果才对生产有解释力。
 *
 * 撮合模型（偏保守，宁可少算利润不多算）：
 * - 挂单在下一根 K 线内成交：买单要求 low < 价（严格穿过），卖单 high > 价；
 *   成交价=挂单价，maker 费；同一根内可成交多张（宽振幅 K 线）
 * - 下单时已越过市价：Gtc 立即按市价成交（taker）；Alo 拒单（post-only 语义）；
 *   Ioc 未越过则拒单
 * - 触发单（TP/SL）以 K 线区间判定触发，按触发价 ± 滑点 taker 成交，先于挂单处理
 *   （触发单盯 mark 价即时反应，挂单要等对手方）
 * - reduce_only：下单时无反向持仓即拒（交易所原话）；成交时按持仓钳制
 * - 资金费：每小时按持仓名义额 × 费率结算（正费率多头付）
 * - 强平：权益低于维持保证金即全平并停机（极端行情兜底，不模拟部分强平）
 * - 单向持仓、平均成本法记 closedPnl；翻仓拆成「平 + 开」两笔成交
 *
 * 时钟由 install() 接管：now = 最后一根已收盘 K 线的收盘时刻 + 1s；
 * getCandles 只返回已收盘 K 线（绝不泄露未来）。
 */

import { clock } from "../utils/clock.js";
import { installSleep } from "../utils/sleep.js";
import { Decimal } from "../utils/precision.js";
import { defaultPerpFeeRates, type FeeRates } from "../fees.js";
import { HyperliquidClient, type Dict, type ExchangeClientLike, type LimitOrderSpec, type LimitTif } from "../trading/client.js";
import { INTERVAL_MS, inferIntervalMs, resampleBars, type Bar, type FundingRow } from "./dataset.js";

export interface SimAsset {
  szDecimals: number;
  maxLeverage: number;
  /** 维持保证金率（HL 约为 1/(2×最大杠杆)） */
  maintenanceRate: number;
}

export const DEFAULT_SIM_ASSETS: Record<string, SimAsset> = {
  BTC: { szDecimals: 5, maxLeverage: 40, maintenanceRate: 0.0125 },
  ETH: { szDecimals: 4, maxLeverage: 25, maintenanceRate: 0.02 },
  SOL: { szDecimals: 2, maxLeverage: 20, maintenanceRate: 0.025 },
};

interface RestingOrder {
  oid: number;
  symbol: string;
  isBuy: boolean;
  size: number;
  origSize: number;
  price: number;
  reduceOnly: boolean;
  tif: LimitTif;
  placedAt: number;
}

interface TriggerOrder {
  oid: number;
  symbol: string;
  /** 触发后的成交方向 */
  isBuy: boolean;
  size: number;
  triggerPx: number;
  limitPx: number;
  isTp: boolean;
  placedAt: number;
  /** OCO 组：同组另一腿成交时撤销 */
  ocoGroup: number | null;
}

interface Position {
  szi: number;
  entryPx: number;
}

interface SimFill {
  coin: string;
  px: string;
  sz: string;
  side: "B" | "A";
  time: number;
  oid: number;
  tid: number;
  closedPnl: string;
  fee: string;
  dir: string;
  crossed: boolean;
  hash: string;
  startPosition: string;
}

export interface SimStats {
  makerFees: number;
  takerFees: number;
  fundingPaid: number;
  makerFills: number;
  takerFills: number;
  volume: number;
  realizedPnl: number;
  forcedCloses: number;
  liquidations: number;
  postOnlyRejections: number;
  ordersPlaced: number;
  ordersCanceled: number;
}

interface SimulatedClientOptions {
  symbol: string;
  bars: Bar[];
  intervalMs?: number;
  initialEquity: number;
  feeRates?: FeeRates;
  funding?: FundingRow[];
  /** 市价/触发单相对参考价的滑点（bp，默认 2） */
  slippageBps?: number;
  assets?: Record<string, SimAsset>;
  /** 起始游标（预热根数；之前的 K 线只用于指标） */
  startIndex?: number;
  /** 默认杠杆（updateLeverage 未调用时） */
  defaultLeverage?: number;
  /**
   * 挂单成交判定：false（默认）= K 线必须严格穿过挂单价（low < 买价 / high > 卖价），
   * 偏保守；true = 触及即成交（low ≤ 买价 / high ≥ 卖价），偏乐观。两者夹住真实成交率，
   * 结论对此敏感就说明结论不稳。
   */
  fillOnTouch?: boolean;
}

const HL_MIN_NOTIONAL = 10;

/** 模拟交易所（单交易对数据集；其他 symbol 的行情查询返回 null）。 */
export class SimulatedClient implements ExchangeClientLike {
  readonly address = "0xsim";
  readonly testnet = true;

  readonly symbol: string;
  readonly bars: Bar[];
  readonly intervalMs: number;
  private readonly feeRates: FeeRates;
  private readonly funding: FundingRow[];
  private readonly slippage: number;
  private readonly assets: Record<string, SimAsset>;

  cursor: number;
  cash: number;
  liquidated = false;
  private readonly positions = new Map<string, Position>();
  private readonly orders = new Map<number, RestingOrder>();
  private readonly triggers = new Map<number, TriggerOrder>();
  private readonly leverage = new Map<string, number>();
  private readonly fills: SimFill[] = [];
  private readonly resampleCache = new Map<number, Bar[]>();
  private nextOid = 1000;
  private nextTid = 1;
  private fundingCursor = 0;
  private readonly defaultLeverage: number;
  private readonly fillOnTouch: boolean;
  readonly stats: SimStats = {
    makerFees: 0, takerFees: 0, fundingPaid: 0, makerFills: 0, takerFills: 0, volume: 0, realizedPnl: 0,
    forcedCloses: 0, liquidations: 0, postOnlyRejections: 0, ordersPlaced: 0, ordersCanceled: 0,
  };

  constructor(options: SimulatedClientOptions) {
    if (!options.bars.length) throw new Error("模拟客户端需要至少一根 K 线");
    this.symbol = options.symbol;
    this.bars = options.bars;
    this.intervalMs = options.intervalMs ?? inferIntervalMs(options.bars);
    this.feeRates = options.feeRates ?? defaultPerpFeeRates();
    this.funding = options.funding ?? [];
    this.slippage = (options.slippageBps ?? 2) / 10_000;
    this.assets = options.assets ?? DEFAULT_SIM_ASSETS;
    this.cursor = Math.min(Math.max(0, options.startIndex ?? 0), options.bars.length - 1);
    this.cash = options.initialEquity;
    this.defaultLeverage = options.defaultLeverage ?? 5;
    this.fillOnTouch = options.fillOnTouch ?? false;
    // 资金费游标跳到起始时刻之后
    while (this.fundingCursor < this.funding.length && this.funding[this.fundingCursor].time <= this.now) {
      this.fundingCursor += 1;
    }
  }

  // ── 时钟与推进 ────────────────────────────────────────────────────────

  /** 当前模拟时刻：最后一根已收盘 K 线的收盘时刻 + 1s */
  get now(): number {
    return this.bars[this.cursor].t + this.intervalMs + 1000;
  }

  get mid(): number {
    return this.bars[this.cursor].c;
  }

  get finished(): boolean {
    return this.cursor >= this.bars.length - 1;
  }

  /** 接管全局时钟与 sleep；返回恢复函数（回测结束必须调用）。 */
  install(): () => void {
    const restoreClock = clock.install(() => this.now);
    const restoreSleep = installSleep(async () => {});
    return () => {
      restoreClock();
      restoreSleep();
    };
  }

  /**
   * 推进一根 K 线：先结算资金费，再撮合触发单、挂单，最后强平检查。
   * 返回 false 表示数据耗尽。
   */
  advance(): boolean {
    if (this.finished || this.liquidated) return false;
    this.cursor += 1;
    const bar = this.bars[this.cursor];
    this.settleFunding(bar);
    this.matchTriggers(bar);
    this.matchRestingOrders(bar);
    this.checkLiquidation(bar);
    return true;
  }

  private settleFunding(bar: Bar): void {
    const until = bar.t + this.intervalMs;
    while (this.fundingCursor < this.funding.length && this.funding[this.fundingCursor].time <= until) {
      const row = this.funding[this.fundingCursor];
      this.fundingCursor += 1;
      const pos = this.positions.get(this.symbol);
      if (!pos || pos.szi === 0) continue;
      // 正费率：多头付空头。付出为负现金流
      const payment = -pos.szi * bar.c * row.fundingRate;
      this.cash += payment;
      this.stats.fundingPaid -= payment;
    }
  }

  private matchTriggers(bar: Bar): void {
    for (const trig of [...this.triggers.values()]) {
      if (trig.symbol !== this.symbol) continue;
      const pos = this.positions.get(trig.symbol);
      // 无持仓或方向不对：reduce_only 触发单失去支撑 → 撤销（HL 触发时会拒）
      if (!pos || pos.szi === 0 || (trig.isBuy ? pos.szi > 0 : pos.szi < 0)) {
        // 只有触发条件满足时才「触发后被拒」；否则继续挂着
        if (!this.triggerHit(trig, bar)) continue;
        this.triggers.delete(trig.oid);
        continue;
      }
      if (!this.triggerHit(trig, bar)) continue;
      this.triggers.delete(trig.oid);
      const px = trig.isBuy ? trig.triggerPx * (1 + this.slippage) : trig.triggerPx * (1 - this.slippage);
      const size = Math.min(trig.size, Math.abs(pos.szi));
      this.executeFill(trig.symbol, trig.isBuy, size, px, true, trig.oid, bar.t + this.intervalMs);
      if (trig.ocoGroup !== null) {
        for (const other of [...this.triggers.values()]) {
          if (other.ocoGroup === trig.ocoGroup) this.triggers.delete(other.oid);
        }
      }
    }
  }

  private triggerHit(trig: TriggerOrder, bar: Bar): boolean {
    // 平多（卖出）：止损在下方（low ≤ trigger），止盈在上方（high ≥ trigger）；平空相反
    if (!trig.isBuy) return trig.isTp ? bar.h >= trig.triggerPx : bar.l <= trig.triggerPx;
    return trig.isTp ? bar.l <= trig.triggerPx : bar.h >= trig.triggerPx;
  }

  private matchRestingOrders(bar: Bar): void {
    const buys = [...this.orders.values()].filter((o) => o.isBuy && o.symbol === this.symbol).sort((a, b) => b.price - a.price);
    const sells = [...this.orders.values()].filter((o) => !o.isBuy && o.symbol === this.symbol).sort((a, b) => a.price - b.price);
    const fillTime = bar.t + this.intervalMs;
    for (const order of [...buys, ...sells]) {
      const crossed = this.fillOnTouch
        ? (order.isBuy ? bar.l <= order.price : bar.h >= order.price)
        : (order.isBuy ? bar.l < order.price : bar.h > order.price);
      if (!crossed) continue;
      let size = order.size;
      if (order.reduceOnly) {
        const pos = this.positions.get(order.symbol);
        const supported = pos ? (order.isBuy ? -pos.szi : pos.szi) : 0;
        if (supported <= 0) {
          // reduce_only 失去持仓支撑：撤单（交易所行为）
          this.orders.delete(order.oid);
          this.stats.ordersCanceled += 1;
          continue;
        }
        size = Math.min(size, supported);
      }
      this.orders.delete(order.oid);
      this.executeFill(order.symbol, order.isBuy, size, order.price, false, order.oid, fillTime);
    }
  }

  private checkLiquidation(bar: Bar): void {
    let maintenance = 0;
    let notional = 0;
    for (const [sym, pos] of this.positions) {
      if (pos.szi === 0) continue;
      const px = sym === this.symbol ? bar.c : pos.entryPx;
      const value = Math.abs(pos.szi) * px;
      notional += value;
      maintenance += value * (this.assets[sym]?.maintenanceRate ?? 0.02);
    }
    if (notional <= 0) return;
    if (this.equity() >= maintenance) return;
    // 强平：全部持仓按当前价 taker 平掉，撤光挂单，停机
    for (const [sym, pos] of [...this.positions]) {
      if (pos.szi === 0) continue;
      const px = pos.szi > 0 ? bar.c * (1 - this.slippage) : bar.c * (1 + this.slippage);
      this.executeFill(sym, pos.szi < 0, Math.abs(pos.szi), px, true, 0, bar.t + this.intervalMs);
    }
    this.orders.clear();
    this.triggers.clear();
    this.liquidated = true;
    this.stats.liquidations += 1;
  }

  // ── 成交与簿记 ────────────────────────────────────────────────────────

  /** 执行一笔成交：更新持仓（平均成本）、现金、手续费、成交记录。翻仓拆两笔。 */
  private executeFill(symbol: string, isBuy: boolean, size: number, px: number, taker: boolean, oid: number, time: number): void {
    if (size <= 0) return;
    const pos = this.positions.get(symbol) ?? { szi: 0, entryPx: 0 };
    const signed = isBuy ? size : -size;
    const sameDirection = pos.szi === 0 || Math.sign(pos.szi) === Math.sign(signed);
    if (sameDirection) {
      this.bookFill(symbol, pos, signed, px, taker, oid, time, 0);
      return;
    }
    const closeSize = Math.min(size, Math.abs(pos.szi));
    const closedPnl = (px - pos.entryPx) * closeSize * Math.sign(pos.szi);
    this.bookFill(symbol, pos, Math.sign(signed) * closeSize, px, taker, oid, time, closedPnl);
    const remainder = size - closeSize;
    if (remainder > 1e-12) {
      const after = this.positions.get(symbol) ?? { szi: 0, entryPx: 0 };
      this.bookFill(symbol, after, Math.sign(signed) * remainder, px, taker, oid, time, 0);
    }
  }

  private bookFill(symbol: string, pos: Position, signed: number, px: number, taker: boolean, oid: number, time: number, closedPnl: number): void {
    const size = Math.abs(signed);
    const notional = size * px;
    const rate = taker ? this.feeRates.takerRate : this.feeRates.makerRate;
    const fee = notional * rate;
    const startPosition = pos.szi;
    const opening = pos.szi === 0 || Math.sign(pos.szi) === Math.sign(signed);
    let dir: string;
    if (opening) {
      // 加仓：平均成本
      const newSzi = pos.szi + signed;
      pos.entryPx = pos.szi === 0 ? px : (pos.entryPx * Math.abs(pos.szi) + px * size) / Math.abs(newSzi);
      pos.szi = newSzi;
      dir = signed > 0 ? "Open Long" : "Open Short";
    } else {
      pos.szi += signed;
      if (Math.abs(pos.szi) < 1e-12) {
        pos.szi = 0;
        pos.entryPx = 0;
      }
      dir = signed > 0 ? "Close Short" : "Close Long";
      this.cash += closedPnl;
      this.stats.realizedPnl += closedPnl;
    }
    this.positions.set(symbol, pos);
    this.cash -= fee;
    if (taker) {
      this.stats.takerFees += fee;
      this.stats.takerFills += 1;
    } else {
      this.stats.makerFees += fee;
      this.stats.makerFills += 1;
    }
    this.stats.volume += notional;
    this.fills.unshift({
      coin: symbol,
      px: String(px),
      sz: String(size),
      side: signed > 0 ? "B" : "A",
      time,
      oid,
      tid: this.nextTid++,
      closedPnl: String(closedPnl),
      fee: String(fee),
      dir,
      crossed: taker,
      hash: `0xsim${this.nextTid}`,
      startPosition: String(startPosition),
    });
    if (this.fills.length > 5000) this.fills.length = 5000;
  }

  /** 当前权益 = 现金 + 未实现盈亏（按最新收盘价） */
  equity(): number {
    let unrealized = 0;
    for (const [sym, pos] of this.positions) {
      if (pos.szi === 0) continue;
      const px = sym === this.symbol ? this.mid : pos.entryPx;
      unrealized += (px - pos.entryPx) * pos.szi;
    }
    return this.cash + unrealized;
  }

  private positionMargin(): number {
    let used = 0;
    for (const [sym, pos] of this.positions) {
      if (pos.szi === 0) continue;
      const px = sym === this.symbol ? this.mid : pos.entryPx;
      used += (Math.abs(pos.szi) * px) / this.leverageOf(sym);
    }
    return used;
  }

  private openOrderMargin(): number {
    let reserved = 0;
    for (const o of this.orders.values()) {
      if (o.reduceOnly) continue;
      reserved += (o.size * o.price) / this.leverageOf(o.symbol);
    }
    return reserved;
  }

  private leverageOf(symbol: string): number {
    return this.leverage.get(symbol) ?? this.defaultLeverage;
  }

  /** 净持仓（带符号）与名义额，供回测统计。 */
  positionOf(symbol = this.symbol): { szi: number; entryPx: number; notional: number } {
    const pos = this.positions.get(symbol) ?? { szi: 0, entryPx: 0 };
    return { szi: pos.szi, entryPx: pos.entryPx, notional: Math.abs(pos.szi) * this.mid };
  }

  openOrderCount(): number {
    return this.orders.size;
  }

  triggerOrderCount(): number {
    return this.triggers.size;
  }

  // ── ExchangeClientLike：查询 ──────────────────────────────────────────

  async getBalance(): Promise<Dict | null> {
    const accountValue = this.equity();
    const totalMarginUsed = this.positionMargin();
    return {
      accountValue,
      totalMarginUsed,
      totalRawUsd: this.cash,
      available: accountValue - totalMarginUsed,
      withdrawable: String(Math.max(0, accountValue - totalMarginUsed - this.openOrderMargin())),
    };
  }

  async getPositions(): Promise<Dict[] | null> {
    const out: Dict[] = [];
    for (const [sym, pos] of this.positions) {
      if (pos.szi === 0) continue;
      const px = sym === this.symbol ? this.mid : pos.entryPx;
      out.push({
        coin: sym,
        szi: String(pos.szi),
        entryPx: String(pos.entryPx),
        positionValue: String(Math.abs(pos.szi) * px),
        unrealizedPnl: String((px - pos.entryPx) * pos.szi),
        leverage: { type: "isolated", value: this.leverageOf(sym) },
      });
    }
    return out;
  }

  async getOpenOrders(includeTrigger = false): Promise<Dict[] | null> {
    const out: Dict[] = [];
    for (const o of this.orders.values()) {
      out.push({
        coin: o.symbol, oid: o.oid, side: o.isBuy ? "B" : "A", limitPx: String(o.price), sz: String(o.size),
        origSz: String(o.origSize), reduceOnly: o.reduceOnly, timestamp: o.placedAt,
        orderType: { limit: { tif: o.tif } },
      });
    }
    if (includeTrigger) {
      for (const t of this.triggers.values()) {
        out.push({
          coin: t.symbol, oid: t.oid, side: t.isBuy ? "B" : "A", limitPx: String(t.limitPx), sz: String(t.size),
          origSz: String(t.size), reduceOnly: true, timestamp: t.placedAt, triggerPx: String(t.triggerPx),
          orderType: { trigger: { isMarket: true, triggerPx: String(t.triggerPx), tpsl: t.isTp ? "tp" : "sl" } },
        });
      }
    }
    return out;
  }

  async getAssetInfo(symbol: string): Promise<Dict | null> {
    const asset = this.assets[symbol];
    return asset ? { name: symbol, szDecimals: asset.szDecimals, maxLeverage: asset.maxLeverage } : null;
  }

  async getCurrentPrice(symbol: string): Promise<number | null> {
    return symbol === this.symbol ? this.mid : null;
  }

  /** 与真实客户端同一套价格精度规则：5 位有效数字 + 最多 (6−szDecimals) 位小数。 */
  async formatPrice(symbol: string, price: number): Promise<number> {
    if (!(price > 0)) return price;
    const magnitude = Math.floor(Math.log10(Math.abs(price)));
    const decimalPlaces = 5 - magnitude - 1;
    let formatted: number;
    if (decimalPlaces < 0) {
      const factor = 10 ** -decimalPlaces;
      formatted = Math.round(price / factor) * factor;
    } else {
      formatted = new Decimal(String(price)).toDecimalPlaces(decimalPlaces, Decimal.ROUND_HALF_UP).toNumber();
    }
    const szDecimals = this.assets[symbol]?.szDecimals ?? 3;
    return new Decimal(String(formatted)).toDecimalPlaces(Math.max(0, 6 - szDecimals), Decimal.ROUND_HALF_UP).toNumber();
  }

  async roundSize(symbol: string, size: number): Promise<number> {
    const szDecimals = this.assets[symbol]?.szDecimals ?? 3;
    return new Decimal(String(size)).toDecimalPlaces(szDecimals, Decimal.ROUND_DOWN).toNumber();
  }

  async getCandles(symbol: string, interval = "15m", startTime?: number, endTime?: number): Promise<Dict[] | null> {
    if (symbol !== this.symbol) return [];
    const targetMs = INTERVAL_MS[interval];
    if (!targetMs) return [];
    let series = this.resampleCache.get(targetMs);
    if (!series) {
      series = resampleBars(this.bars, targetMs, this.intervalMs);
      this.resampleCache.set(targetMs, series);
    }
    const now = this.now;
    const start = startTime ?? 0;
    const end = endTime ?? now;
    // 只返回已收盘 K 线：绝不泄露未来
    return series
      .filter((b) => b.t >= start && b.t <= end && b.t + targetMs <= now)
      .map((b) => ({ t: b.t, T: b.t + targetMs - 1, s: symbol, i: interval, o: String(b.o), h: String(b.h), l: String(b.l), c: String(b.c), v: String(b.v), n: 0 }));
  }

  async userFills(): Promise<Dict[]> {
    return this.fills.slice(0, 2000) as unknown as Dict[];
  }

  async fetchUserFeeRates(): Promise<FeeRates> {
    return { ...this.feeRates };
  }

  async updateLeverage(symbol: string, leverage: number, _isCross = true): Promise<Dict> {
    const max = this.assets[symbol]?.maxLeverage ?? 50;
    if (leverage > max) return { status: "error", message: `杠杆 ${leverage}x 超过 ${symbol} 的最大杠杆 ${max}x` };
    this.leverage.set(symbol, Math.max(1, Math.trunc(leverage)));
    return { status: "ok", response: { type: "default" } };
  }

  // ── ExchangeClientLike：下单 ──────────────────────────────────────────

  private receipt(statuses: Dict[]): Dict {
    return { status: "ok", response: { type: "order", data: { statuses } } };
  }

  async placeLimitOrder(symbol: string, isBuy: boolean, size: number, price: number, reduceOnly = false, tif: LimitTif = "Gtc"): Promise<Dict> {
    return this.placeLimitOrders([{ symbol, isBuy, size, price, reduceOnly, tif }]);
  }

  async placeLimitOrders(specs: LimitOrderSpec[]): Promise<Dict> {
    const statuses: Dict[] = [];
    for (const spec of specs) {
      statuses.push(await this.placeOne(spec));
    }
    return this.receipt(statuses);
  }

  private async placeOne(spec: LimitOrderSpec): Promise<Dict> {
    this.stats.ordersPlaced += 1;
    if (this.liquidated) return { error: "Account liquidated" };
    if (spec.symbol !== this.symbol) return { error: `Unknown asset ${spec.symbol}` };
    const size = await this.roundSize(spec.symbol, spec.size);
    const price = await this.formatPrice(spec.symbol, spec.price);
    const tif = spec.tif ?? "Gtc";
    if (!(size > 0)) return { error: "Order has zero size." };
    if (size * price < HL_MIN_NOTIONAL) return { error: "Order must have minimum value of $10." };
    const pos = this.positions.get(spec.symbol) ?? { szi: 0, entryPx: 0 };
    if (spec.reduceOnly) {
      const supported = spec.isBuy ? -pos.szi : pos.szi;
      if (supported <= 0) return { error: "Reduce only order would increase position." };
    } else {
      const required = (size * price) / this.leverageOf(spec.symbol);
      const available = this.equity() - this.positionMargin() - this.openOrderMargin();
      if (available < required) return { error: "Insufficient margin to place order." };
    }
    const mid = this.mid;
    const crosses = spec.isBuy ? price >= mid : price <= mid;
    const oid = this.nextOid++;
    if (crosses) {
      if (tif === "Alo") {
        this.stats.postOnlyRejections += 1;
        return { error: "Post only order would have immediately matched, bbo was " + mid };
      }
      // 立即成交（taker）：按市价成交，reduce_only 钳制到持仓
      let fillSize = size;
      if (spec.reduceOnly) fillSize = Math.min(size, Math.abs(pos.szi));
      this.executeFill(spec.symbol, spec.isBuy, fillSize, mid, true, oid, this.now);
      return { filled: { oid, avgPx: String(mid), totalSz: String(fillSize) } };
    }
    if (tif === "Ioc") return { error: "Order could not immediately match against any resting orders." };
    this.orders.set(oid, {
      oid, symbol: spec.symbol, isBuy: spec.isBuy, size, origSize: size, price,
      reduceOnly: !!spec.reduceOnly, tif, placedAt: this.now,
    });
    return { resting: { oid } };
  }

  async placeTpslOrder(options: { symbol: string; triggerPrice: number; isBuy: boolean; size: number; isTp?: boolean; slSlippage?: number }): Promise<Dict> {
    return this.receipt([await this.registerTrigger(options, null)]);
  }

  private async registerTrigger(
    options: { symbol: string; triggerPrice: number; isBuy: boolean; size: number; isTp?: boolean },
    ocoGroup: number | null,
  ): Promise<Dict> {
    this.stats.ordersPlaced += 1;
    if (options.symbol !== this.symbol) return { error: `Unknown asset ${options.symbol}` };
    const size = await this.roundSize(options.symbol, options.size);
    const triggerPx = await this.formatPrice(options.symbol, options.triggerPrice);
    if (!(size > 0)) return { error: "Order has zero size." };
    const isTp = options.isTp ?? true;
    const oid = this.nextOid++;
    this.triggers.set(oid, {
      oid, symbol: options.symbol, isBuy: options.isBuy, size, triggerPx,
      limitPx: triggerPx, isTp, placedAt: this.now, ocoGroup,
    });
    return { resting: { oid } };
  }

  async cancelOrder(symbol: string, oid: number): Promise<Dict> {
    return this.cancelOrders(symbol, [oid]);
  }

  async cancelOrders(_symbol: string, oids: number[]): Promise<Dict> {
    const statuses: unknown[] = oids.map((oid) => {
      if (this.orders.delete(oid) || this.triggers.delete(oid)) {
        this.stats.ordersCanceled += 1;
        return "success";
      }
      return { error: "Order was never placed, already canceled, or filled." };
    });
    return { status: "ok", response: { type: "cancel", data: { statuses } } };
  }

  async closePosition(symbol: string, size: number | null = null): Promise<Dict> {
    if (symbol !== this.symbol) return { status: "error", message: `没有 ${symbol} 的持仓` };
    const pos = this.positions.get(symbol);
    if (!pos || pos.szi === 0) return { status: "error", message: `没有 ${symbol} 的持仓` };
    let closeSize = size === null ? Math.abs(pos.szi) : Math.min(Math.abs(size), Math.abs(pos.szi));
    closeSize = await this.roundSize(symbol, closeSize);
    if (closeSize <= 0) return { status: "error", message: `${symbol} 平仓量取整后为 0` };
    const isBuy = pos.szi < 0;
    const px = isBuy ? this.mid * (1 + this.slippage) : this.mid * (1 - this.slippage);
    const oid = this.nextOid++;
    this.stats.ordersPlaced += 1;
    this.executeFill(symbol, isBuy, closeSize, px, true, oid, this.now);
    return this.receipt([{ filled: { oid, avgPx: String(px), totalSz: String(closeSize) } }]);
  }

  async emergencyCloseWithRetry(symbol: string, size: number | null, _options: { reason: string; maxRetries?: number }): Promise<[boolean, Dict | null]> {
    this.stats.forcedCloses += 1;
    const result = await this.closePosition(symbol, size);
    const [ok] = HyperliquidClient.checkOrderSuccess(result);
    return [ok, result];
  }
}

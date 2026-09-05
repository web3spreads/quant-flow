/**
 * Hyperliquid 订单管理器
 * 管理永续合约的订单创建、监控和执行，包括止盈止损逻辑。
 * （合约本身仍是永续；这里说的是执行层，与已移除的「永续方向策略」无关。）
 */

import { HyperliquidClient, type Dict, type ExchangeClientLike, type LimitTif } from "./client.js";
import { extractOrderId } from "../utils/gridMath.js";
import { AsyncMutex } from "../utils/mutex.js";
import type { TradingLogger } from "../logger.js";
import { clock } from "../utils/clock.js";
import { sleep } from "../utils/sleep.js";


interface PendingLimitOrder {
  symbol: string;
  isBuy: boolean;
  size: number;
  entryPrice: number;
  takeProfitPrice: number | null;
  stopLossPrice: number | null;
  createdAt: number;
  onTpslSet?: (orderId: number, allOk: boolean) => void;
  tpslAttempts: number;
}

/**
 * 限价单成交监控器。
 *
 * 监控限价单成交状态，成交后自动设置止盈止损，解决限价单成交后裸仓风险。
 * 定时异步循环，队列空时不退出而是空转等待：「空即退出」与 addOrder 之间存在
 * TOCTOU 竞态，判定为空后、真正退出前注册的新订单会无人监控。
 * 空转成本可忽略（空队列直接返回，不发任何 API 请求）。
 */
export class LimitOrderMonitor {
  private pendingOrders = new Map<number, PendingLimitOrder>();
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private checking = false;

  constructor(
    private readonly client: ExchangeClientLike,
    private readonly options: {
      checkIntervalMs?: number;
      maxCheckDurationMs?: number;
      /**
       * 引擎级共享交易锁。监控循环操作账户（挂 TPSL/紧急平仓）前必须持有该锁，
       * 否则会与网格周期并发踩乱持仓与挂单状态，破坏引擎「同一时刻只有
       * 一条周期在动账户」的互斥承诺。
       */
      tradingLock?: AsyncMutex;
      logger?: TradingLogger;
      /** false=不启动定时器，由外部调用 runOnce 驱动（回测） */
      autoTick?: boolean;
    } = {},
  ) {}

  /** 手动执行一轮检查（回测/测试用；生产由定时器驱动）。 */
  async runOnce(): Promise<void> {
    await this.tick();
  }

  private log(m: string): void {
    if (this.options.logger) this.options.logger.printInfo(m);
    else console.log(m);
  }

  /** 添加限价单到监控列表。 */
  addOrder(entry: {
    orderId: number;
    symbol: string;
    isBuy: boolean;
    size: number;
    entryPrice: number;
    takeProfitPrice: number | null;
    stopLossPrice: number | null;
    onTpslSet?: (orderId: number, allOk: boolean) => void;
  }): void {
    this.pendingOrders.set(entry.orderId, {
      symbol: entry.symbol,
      isBuy: entry.isBuy,
      size: entry.size,
      entryPrice: entry.entryPrice,
      takeProfitPrice: entry.takeProfitPrice,
      stopLossPrice: entry.stopLossPrice,
      createdAt: clock.now(),
      onTpslSet: entry.onTpslSet,
      tpslAttempts: 0,
    });
    this.log(`📋 限价单 ${entry.orderId} 已加入监控队列`);
    this.ensureRunning();
  }

  removeOrder(orderId: number): void {
    if (this.pendingOrders.delete(orderId)) {
      this.log(`📋 限价单 ${orderId} 已从监控队列移除`);
    }
  }

  private ensureRunning(): void {
    if (this.running || this.options.autoTick === false) return;
    this.running = true;
    const interval = this.options.checkIntervalMs ?? 5000;
    this.timer = setInterval(() => void this.tick(), interval);
    this.timer.unref?.();
    this.log("🔄 限价单监控循环已启动");
  }

  async stop(): Promise<void> {
    this.running = false;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    // 等待进行中的检查结束，避免停机时腰斩 TPSL 设置序列
    while (this.checking) await sleep(50);
    this.log("🛑 限价单监控循环已停止");
  }

  private async tick(): Promise<void> {
    if (this.checking) return; // 上一轮尚未结束（慢 API），跳过本 tick
    this.checking = true;
    try {
      await this.checkOrders();
    } catch (e) {
      this.options.logger?.printError(`❌ 限价单监控异常: ${e}`);
    } finally {
      this.checking = false;
    }
  }

  /** 检查所有待监控的限价单（操作账户前必须持有引擎交易锁）。 */
  private async checkOrders(): Promise<void> {
    if (this.pendingOrders.size === 0) return;
    const lock = this.options.tradingLock;
    // 与网格周期互斥：拿不到锁说明周期正在动账户，本轮跳过（下个 tick 重试）
    if (lock && !(await lock.acquire(10_000))) return;
    try {
      await this.checkOrdersLocked([...this.pendingOrders.entries()]);
    } finally {
      lock?.release();
    }
  }

  /** 实际检查逻辑（调用方已保证持有交易锁）。 */
  private async checkOrdersLocked(ordersToCheck: Array<[number, PendingLimitOrder]>): Promise<void> {
    // 获取当前挂单。查询失败（null）时整轮跳过——把「查不到」当「不在挂单中」
    // 会把仍在挂着的订单误判为已成交/已取消，随后依据同样失败的持仓查询
    // 把订单移出监控，成交后裸仓无人设置止盈止损。
    const openOrders = await this.client.getOpenOrders();
    if (openOrders === null) {
      this.options.logger?.printWarning("⚠️ 限价单监控：挂单查询失败，本轮跳过");
      return;
    }
    const openOrderIds = new Set(openOrders.map((o) => o?.oid));
    const maxDuration = this.options.maxCheckDurationMs ?? 3_600_000;

    // 批量获取持仓，减少 API 调用；设置 TPSL 后置空以触发刷新
    let positionMap: Map<string, Dict> | null = null;

    for (const [orderId, orderInfo] of ordersToCheck) {
      // 检查是否超时
      if (clock.now() - orderInfo.createdAt > maxDuration) {
        this.log(`⏰ 限价单 ${orderId} 监控超时，移除`);
        this.removeOrder(orderId);
        continue;
      }
      // 订单仍未成交，继续监控
      if (openOrderIds.has(orderId)) continue;

      // 订单不在挂单中，说明已成交或已取消。
      // 用订单记录的原始 size（而非总持仓），避免多单同时成交时超额设置止盈止损。
      if (positionMap === null) {
        const positions = await this.client.getPositions();
        if (positions === null) {
          // 持仓查询失败：本轮不做任何移除/设置决策，等下轮数据恢复
          this.options.logger?.printWarning("⚠️ 限价单监控：持仓查询失败，本轮跳过剩余订单");
          return;
        }
        positionMap = new Map(positions.map((p) => [String(p.coin), p]));
      }
      const position = positionMap.get(orderInfo.symbol);
      if (position) {
        const positionSize = Number(position.szi ?? 0);
        if ((orderInfo.isBuy && positionSize > 0) || (!orderInfo.isBuy && positionSize < 0)) {
          this.log(`✅ 限价单 ${orderId} 已成交，正在设置止盈止损 (size=${orderInfo.size})`);
          await this.setTpslForOrder(orderId, orderInfo, orderInfo.size);
          // TPSL 设置可能影响持仓状态，下次循环需要刷新
          positionMap = null;
        } else {
          this.options.logger?.printWarning(`⚠️ 限价单 ${orderId} 持仓方向不匹配，可能已取消`);
          this.removeOrder(orderId);
        }
      } else {
        this.options.logger?.printWarning(`⚠️ 限价单 ${orderId} 无对应持仓，移除监控`);
        this.removeOrder(orderId);
      }
    }
  }

  /** 为成交的限价单设置止盈止损。 */
  private async setTpslForOrder(orderId: number, orderInfo: PendingLimitOrder, actualSize: number): Promise<void> {
    const { symbol, isBuy, takeProfitPrice: tpPrice, stopLossPrice: slPrice } = orderInfo;
    orderInfo.tpslAttempts += 1;
    const maxAttempts = 3;

    try {
      // 先设置止损（更重要）
      let slSuccess = true;
      if (slPrice) {
        const slResult = await this.client.placeTpslOrder({
          symbol, triggerPrice: slPrice, isBuy: !isBuy, size: actualSize, isTp: false,
        });
        const [ok, slError] = HyperliquidClient.checkOrderSuccess(slResult);
        slSuccess = ok;
        if (!ok) {
          this.options.logger?.printError(`❌ 限价单 ${orderId} 止损设置失败: ${slError}`);
          // 重试或紧急平仓（带校验+重试+全平兜底，失败落 critical 日志）
          if (orderInfo.tpslAttempts >= maxAttempts) {
            this.options.logger?.printWarning("⚠️ 【安全机制】止损设置多次失败，紧急平仓");
            await this.client.emergencyCloseWithRetry(symbol, actualSize, {
              reason: "限价单成交后止损设置失败",
            });
            this.removeOrder(orderId);
          }
          return; // 稍后重试
        }
        this.log(`✅ 限价单 ${orderId} 止损已设置: $${slPrice}`);
      }

      // 设置止盈
      let tpSuccess = true;
      if (tpPrice) {
        const tpResult = await this.client.placeTpslOrder({
          symbol, triggerPrice: tpPrice, isBuy: !isBuy, size: actualSize, isTp: true,
        });
        const [ok, tpError] = HyperliquidClient.checkOrderSuccess(tpResult);
        tpSuccess = ok;
        if (!ok) this.options.logger?.printWarning(`⚠️ 限价单 ${orderId} 止盈设置失败: ${tpError}`);
        else this.log(`✅ 限价单 ${orderId} 止盈已设置: $${tpPrice}`);
      }

      if (slSuccess) {
        try {
          orderInfo.onTpslSet?.(orderId, slSuccess && tpSuccess);
        } catch (e) {
          this.options.logger?.printWarning(`⚠️ 止盈止损回调异常: ${e}`);
        }
        this.removeOrder(orderId);
      }
    } catch (e) {
      this.options.logger?.printError(`❌ 设置止盈止损异常: ${e}`);
      if (orderInfo.tpslAttempts >= maxAttempts) {
        this.options.logger?.printWarning("⚠️ 【安全机制】异常次数过多，紧急平仓");
        try {
          await this.client.emergencyCloseWithRetry(symbol, actualSize, {
            reason: "限价单 TPSL 设置连续异常",
          });
        } catch (closeErr) {
          this.options.logger?.printWarning(`⚠️ 紧急平仓失败: ${closeErr}`);
        }
        this.removeOrder(orderId);
      }
    }
  }
}

/** Hyperliquid 订单管理器 */
export class OrderManager {
  readonly client: ExchangeClientLike;
  takeProfitRatio: number;
  stopLossRatio: number;
  defaultLeverage: number;
  minRiskRewardRatio: number;
  limitOrderMonitor: LimitOrderMonitor | null = null;
  private readonly logger?: TradingLogger;

  constructor(options: {
    client: ExchangeClientLike;
    takeProfitRatio?: number;
    stopLossRatio?: number;
    defaultLeverage?: number;
    minRiskRewardRatio?: number;
    enableLimitOrderMonitor?: boolean;
    tradingLock?: AsyncMutex;
    logger?: TradingLogger;
    /** false=监控器不自动定时（回测由外部驱动 runOnce） */
    monitorAutoTick?: boolean;
  }) {
    this.client = options.client;
    this.takeProfitRatio = options.takeProfitRatio ?? 0.05;
    this.stopLossRatio = options.stopLossRatio ?? 0.02;
    this.defaultLeverage = options.defaultLeverage ?? 10;
    this.minRiskRewardRatio = options.minRiskRewardRatio ?? 1.5;
    this.logger = options.logger;
    if (options.enableLimitOrderMonitor ?? true) {
      this.limitOrderMonitor = new LimitOrderMonitor(options.client, {
        tradingLock: options.tradingLock,
        logger: options.logger,
        autoTick: options.monitorAutoTick ?? true,
      });
    }
  }

  private log(m: string): void {
    if (this.logger) this.logger.printInfo(m);
    else console.log(m);
  }

  /** 根据杠杆计算安全的止盈止损比例。 */

  /** 关闭订单管理器，停止所有后台任务。 */
  async shutdown(): Promise<void> {
    if (this.limitOrderMonitor) await this.limitOrderMonitor.stop();
  }

  /** 获取可用余额（USD）= 账户总价值 - 已占用保证金。 */

  /** 获取详细的余额信息（status/total/occupied/available/unrealized_pnl/message）。 */
  async getAvailableBalanceInfo(): Promise<Dict> {
    try {
      const balance = await this.client.getBalance();
      if (!balance) {
        return { status: "error", message: "无法获取余额信息", total: 0, occupied: 0, available: 0, unrealized_pnl: 0 };
      }
      const total = Number(balance.accountValue);
      const occupied = Number(balance.totalMarginUsed);
      const available = total - occupied;
      // 计算未实现盈亏（从所有持仓汇总；查询失败时按 0 展示，不影响余额主数据）
      let unrealizedPnl = 0;
      const positions = (await this.client.getPositions()) ?? [];
      for (const position of positions) unrealizedPnl += Number(position.unrealizedPnl ?? 0);
      return {
        status: "ok",
        total,
        occupied,
        available,
        equity: total,
        unrealized_pnl: unrealizedPnl,
        message: `总价值: $${total.toFixed(2)}, 可用: $${available.toFixed(2)}, 未实现盈亏: $${unrealizedPnl >= 0 ? "+" : ""}${unrealizedPnl.toFixed(2)}`,
      };
    } catch (e) {
      return { status: "error", message: `获取余额失败: ${e}`, total: 0, occupied: 0, available: 0, unrealized_pnl: 0 };
    }
  }

  /**
   * 获取当前持仓列表；查询失败返回 null（未知），调用方必须区分
   * 「确认无持仓」与「查询失败」两种语义（见 client.getPositions）。
   */
  async getCurrentPositions(): Promise<Dict[] | null> {
    return this.client.getPositions();
  }

  /** 获取最近一笔成交的交易哈希和成交价。 */
  private async getLatestFillInfo(symbol?: string): Promise<Dict> {
    const result: Dict = { hash: null, fill_price: null };
    try {
      // 等待一小段时间确保订单已成交
      await sleep(500);
      const fills = await this.client.userFills();
      if (fills?.length) {
        let targetFill: Dict | undefined;
        if (symbol) targetFill = fills.find((f) => f?.coin === symbol);
        if (!targetFill) targetFill = fills[0];
        result.hash = targetFill?.hash ?? null;
        if (targetFill?.px !== undefined && targetFill?.px !== null) {
          result.fill_price = Number(targetFill.px);
        }
      }
    } catch (e) {
      this.logger?.printWarning(`⚠️ 获取交易成交信息失败: ${e}`);
    }
    return result;
  }

  /** 根据 USDT 金额计算合约数量（含杠杆与精度处理）。 */

  /** 设置杠杆的共用前置（仅在无持仓时设置；降杠杆失败按当前杠杆继续）。返回 [lev, ok]。 */
  private async prepareLeverage(symbol: string, lev: number): Promise<[number, boolean]> {
    // 持仓查询失败按「无持仓」处理：影响仅是多设一次杠杆，方向保守
    const currentPositions = (await this.getCurrentPositions()) ?? [];
    const hasPosition = currentPositions.some((pos) => pos?.coin === symbol);
    if (hasPosition) {
      this.log(`   ⚠️  检测到已有 ${symbol} 持仓，跳过杠杆设置（使用现有杠杆）`);
      return [lev, true];
    }
    this.log(`   设置杠杆: ${lev}x (逐仓模式)`);
    const leverageResult = await this.client.updateLeverage(symbol, lev, false);
    if (leverageResult?.status === "error") {
      this.logger?.printError(`❌ 杠杆设置失败: ${leverageResult?.message}`);
      return [lev, false];
    }
    if (leverageResult?.status === "warning") {
      // 无法降低杠杆，但可以使用当前杠杆继续
      const currentLev = Number(leverageResult.current_leverage ?? lev);
      this.logger?.printWarning(`⚠️ ${leverageResult.message}`);
      this.log(`   使用当前杠杆 ${currentLev}x 继续下单`);
      return [currentLev, true];
    }
    return [lev, true];
  }

  /** 执行做多操作（带止盈止损保护）。 */

  /** 执行做空操作（带止盈止损保护）。 */

  /** 平仓操作（结果含交易哈希和实际成交价）。 */
  async closePosition(symbol: string, size: number | null = null): Promise<Dict | null> {
    try {
      const result = await this.client.closePosition(symbol, size);
      if (result?.status === "ok") {
        const fillInfo = await this.getLatestFillInfo(symbol);
        result.hash = fillInfo.hash ?? "";
        if (fillInfo.fill_price !== null) result.fill_price = fillInfo.fill_price;
      }
      return result;
    } catch (e) {
      this.logger?.printError(`❌ 平仓失败: ${e}`);
      return null;
    }
  }

  /**
   * 风控强制平仓（带结果校验 + 指数退避重试 + 全平兜底）。
   *
   * 账户熔断 / 超时强平必须走本方法而非 closePosition：后者吞异常返回错误
   * 字典且不校验交易所内层 statuses，「平仓失败」会被误记成功，随后风控状态
   * 被误清、失败仓位再无人接管。
   */
  async forceClosePosition(symbol: string, reason: string): Promise<boolean> {
    try {
      const [ok] = await this.client.emergencyCloseWithRetry(symbol, null, { reason });
      return !!ok;
    } catch (e) {
      this.logger?.printError(`❌ 强制平仓异常 ${symbol}: ${e}`);
      return false;
    }
  }

  /** 计算建议的交易金额。 */

  /** 批量布单前统一设置一次杠杆（逐仓；有持仓时沿用现有杠杆）。返回 [生效杠杆, 是否可继续]。 */
  async ensureLeverage(symbol: string, leverage?: number | null): Promise<[number, boolean]> {
    return this.prepareLeverage(symbol, leverage || this.defaultLeverage);
  }

  /** 把已挂出的限价开仓单登记到成交监控：成交后按比例挂 TP/SL 触发单（网格层级触发单开关打开时用）。 */
  async registerTpslMonitor(entry: {
    orderId: number;
    symbol: string;
    isBuy: boolean;
    size: number;
    entryPrice: number;
    tpRatio?: number | null;
    slRatio?: number | null;
    withTakeProfit?: boolean;
    withStopLoss?: boolean;
  }): Promise<void> {
    if (!this.limitOrderMonitor) return;
    const withTp = entry.withTakeProfit ?? true;
    const withSl = entry.withStopLoss ?? true;
    if (!withTp && !withSl) return;
    const tpRatio = entry.tpRatio ?? this.takeProfitRatio;
    const slRatio = entry.slRatio ?? this.stopLossRatio;
    const { symbol, isBuy, entryPrice } = entry;
    const tpPrice = withTp ? await this.client.formatPrice(symbol, entryPrice * (isBuy ? 1 + tpRatio : 1 - tpRatio)) : null;
    const slPrice = withSl ? await this.client.formatPrice(symbol, entryPrice * (isBuy ? 1 - slRatio : 1 + slRatio)) : null;
    this.limitOrderMonitor.addOrder({
      orderId: entry.orderId, symbol, isBuy, size: entry.size, entryPrice,
      takeProfitPrice: tpPrice, stopLossPrice: slPrice,
    });
  }

  /** 执行限价开多（带止盈止损计算 + 成交监控注册）。 */
  async executeLongLimit(symbol: string, usdtAmount: number, limitPrice: number, options: LimitEntryOptions = {}): Promise<Dict | null> {
    return this.executeLimitEntry(symbol, usdtAmount, limitPrice, true, options);
  }

  /** 执行限价开空（带止盈止损计算 + 成交监控注册）。 */
  async executeShortLimit(symbol: string, usdtAmount: number, limitPrice: number, options: LimitEntryOptions = {}): Promise<Dict | null> {
    return this.executeLimitEntry(symbol, usdtAmount, limitPrice, false, options);
  }

  private async executeLimitEntry(
    symbol: string,
    usdtAmount: number,
    limitPrice: number,
    isLong: boolean,
    options: LimitEntryOptions,
  ): Promise<Dict | null> {
    try {
      const lev = options.leverage || this.defaultLeverage;
      // 价格非法时直接拒绝，避免后续 usdtAmount / limitPrice 触发除零
      if (limitPrice <= 0) {
        this.logger?.printError(`❌ 限价单价格非法: ${limitPrice}`);
        return { success: false, message: `limit_price 必须大于 0，实际为 ${limitPrice}` };
      }
      // 入参金额非法时直接拒绝，避免下出 0 量或反向（负量）单
      if (usdtAmount <= 0) {
        this.logger?.printError(`❌ 限价单金额非法: ${usdtAmount}`);
        return { success: false, message: `usdt_amount 必须大于 0，实际为 ${usdtAmount}` };
      }

      // 合约数量：名义额口径直接除以价格；保证金口径需乘杠杆换算为名义额
      // （网格数学引擎产出的 amount_per_grid 已含杠杆，必须走名义额口径，否则杠杆被重复计算）
      let size = options.amountIsNotional ? usdtAmount / limitPrice : (usdtAmount * lev) / limitPrice;
      const assetInfo = await this.client.getAssetInfo(symbol);
      const decimals = assetInfo && "szDecimals" in assetInfo ? Number(assetInfo.szDecimals) : 3;
      size = Number(size.toFixed(decimals));
      // 取整后量退化到 0（或负）时拒绝下单，避免发出 0 量单被拒或反向单
      if (size <= 0) {
        this.logger?.printError(`❌ 限价单数量取整后非法: ${size}（金额 ${usdtAmount} @ $${limitPrice}）`);
        return { success: false, message: `size 取整后必须大于 0，实际为 ${size}` };
      }

      this.log(`${isLong ? "📈 限价开多" : "📉 限价开空"} ${symbol}: ${size} 张合约 @ $${limitPrice.toFixed(2)}`);

      const [, levOk] = await this.prepareLeverage(symbol, lev);
      if (!levOk) return null;

      // 计算止盈止损价格（基于限价单价格按百分比计算；做空方向相反）
      const withTakeProfit = options.withTakeProfit ?? true;
      const withStopLoss = options.withStopLoss ?? true;
      const actualTpRatio = options.tpRatio ?? this.takeProfitRatio;
      const actualSlRatio = options.slRatio ?? this.stopLossRatio;
      let tpPrice: number | null = null;
      let slPrice: number | null = null;
      if (withTakeProfit) {
        tpPrice = await this.client.formatPrice(symbol, limitPrice * (isLong ? 1 + actualTpRatio : 1 - actualTpRatio));
        this.log(`   止盈价: $${tpPrice.toFixed(2)} (${isLong ? "+" : "-"}${(actualTpRatio * 100).toFixed(3)}%)`);
      }
      if (withStopLoss) {
        slPrice = await this.client.formatPrice(symbol, limitPrice * (isLong ? 1 - actualSlRatio : 1 + actualSlRatio));
        this.log(`   止损价: $${slPrice.toFixed(2)} (${isLong ? "-" : "+"}${(actualSlRatio * 100).toFixed(3)}%)`);
      }

      const limitOrder = await this.client.placeLimitOrder(symbol, isLong, size, limitPrice, false, options.tif ?? "Gtc");
      // 校验交易所内层 statuses：HL 拒单时外层仍为 status=ok，错误藏在 statuses[].error
      const [orderOk, orderErr] = HyperliquidClient.checkOrderSuccess(limitOrder);
      if (!orderOk) {
        // post-only 单会立即成交而被拒是 Alo 的正常语义（价格已越过挂单价），不是故障
        const postOnlyRejected = options.tif === "Alo" && HyperliquidClient.isPostOnlyRejection(orderErr);
        if (postOnlyRejected) this.log(`↩️ post-only 限价单被拒（价格已越过 $${limitPrice.toFixed(2)}），本轮不追价`);
        else this.logger?.printError(`❌ 限价单失败: ${orderErr}`);
        return { success: false, message: orderErr, limit_order: limitOrder, post_only_rejected: postOnlyRejected };
      }

      const result: Dict = {
        success: true,
        limit_order: limitOrder,
        quantity: size,
        price: limitPrice,
        leverage: lev,
        take_profit_price: tpPrice,
        stop_loss_price: slPrice,
        message: "限价单已提交，成交后将按配置自动设置风控单",
      };
      // 注册到 LimitOrderMonitor，成交后自动设置止盈止损（网格层级默认不挂
      // 触发单：其退出由 reduce_only 限价平仓单与网格级屏障负责，见 GridManager）
      if (this.limitOrderMonitor && (withTakeProfit || withStopLoss) && options.registerTpslMonitor !== false) {
        const orderId = extractOrderId(limitOrder);
        if (orderId) {
          this.limitOrderMonitor.addOrder({
            orderId, symbol, isBuy: isLong, size,
            entryPrice: limitPrice, takeProfitPrice: tpPrice, stopLossPrice: slPrice,
          });
        } else {
          this.logger?.printWarning("⚠️ 无法提取订单 ID，限价单监控未注册（限价单可能已立即成交）");
        }
      }
      return result;
    } catch (e) {
      this.logger?.printError(`❌ 执行限价开${isLong ? "多" : "空"}失败: ${e}`);
      return null;
    }
  }

  /** 获取待处理的限价单列表（格式化，含与当前价的差距百分比）。 */

  /** 取消限价单。 */
}

interface LimitEntryOptions {
  leverage?: number | null;
  tpRatio?: number | null;
  slRatio?: number | null;
  withTakeProfit?: boolean;
  withStopLoss?: boolean;
  amountIsNotional?: boolean;
  /** 有效期：Alo=只做 maker（默认 Gtc） */
  tif?: LimitTif;
  /** false=不注册成交监控（不在成交后挂 TP/SL 触发单） */
  registerTpslMonitor?: boolean;
}

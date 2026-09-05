/**
 * 网格策略周期：编排一轮网格决策的完整流程。
 *
 * 顺序有真实事故背书，勿调换：
 * 净额归因 → 强平重试 → 账户熔断 → 净值快照+停机线 → Triple Barrier →
 * 暂停期维护 → 行情指标 → 趋势过滤 → AI 决策 → 健康跟踪 → 空转自愈 →
 * 连亏锁定降级 → 记录决策 → syncGrid 布单。
 */

import { TechnicalIndicators, TrendConfirmTracker, detectStrongTrend, efficiencyRatio, type Candle } from "../data/indicators.js";
import { ProtectionAction, ProtectionManager } from "../plugins/protections/index.js";
import type { GridAgent } from "./gridAgent.js";
import type { GridManager } from "../trading/gridManager.js";
import type { OrderManager } from "../trading/orderManager.js";
import type { MarketDataFetcher } from "../data/marketData.js";
import type { TradingLogger } from "../logger.js";
import type { GridSection } from "../config.js";
import type { Dict } from "../trading/client.js";
import { clock } from "../utils/clock.js";

/** 网格策略（单交易对） */
export class GridStrategy {
  readonly symbol: string;
  readonly gridAgent: GridAgent;
  readonly gridManager: GridManager;
  private readonly orderManager: OrderManager;
  private readonly marketFetcher: MarketDataFetcher;
  private readonly logger: TradingLogger;
  private readonly config: GridSection;
  private readonly timeframe: string;
  private readonly protectionManager: ProtectionManager | null;

  private readonly trendTracker: TrendConfirmTracker;
  private llmFailureStreak = 0;
  private idleStreak = 0;
  private llmAlertSent = false;
  /** 形态闸门连续命中周期数（日志去重用） */
  private rangeGateStreak = 0;
  // 账户熔断强平失败的非网格交易对（网格交易对的重试由 GridManager 的
  // pending_emergency_close 承担），每周期开头重试直到确认平掉
  private pendingForceCloses: Record<string, string> = {};

  constructor(options: {
    symbol: string;
    gridAgent: GridAgent;
    gridManager: GridManager;
    orderManager: OrderManager;
    marketFetcher: MarketDataFetcher;
    logger: TradingLogger;
    gridConfig: GridSection;
    timeframe: string;
    protectionManager?: ProtectionManager | null;
  }) {
    this.symbol = options.symbol;
    this.gridAgent = options.gridAgent;
    this.gridManager = options.gridManager;
    this.orderManager = options.orderManager;
    this.marketFetcher = options.marketFetcher;
    this.logger = options.logger;
    this.config = options.gridConfig;
    this.timeframe = options.timeframe;
    this.protectionManager = options.protectionManager ?? null;
    this.trendTracker = new TrendConfirmTracker(
      options.gridConfig.trend_confirm_cycles,
      options.gridConfig.flatten_min_cycles,
    );
  }

  /** 看板展示用：策略内部健康状态。 */
  get health(): Dict {
    const [dir, count] = this.trendTracker.streak;
    return {
      llm_failure_streak: this.llmFailureStreak,
      llm_alert_sent: this.llmAlertSent,
      idle_streak: this.idleStreak,
      range_gate_streak: this.rangeGateStreak,
      trend_streak: { dir, count },
      pending_force_closes: { ...this.pendingForceCloses },
    };
  }

  // ── 主流程 ────────────────────────────────────────────────────────────

  /** 执行一个网格周期（内部兜住所有异常，不向调度层抛出）。 */
  async runCycle(): Promise<void> {
    try {
      await this.runCycleInner();
    } catch (e) {
      this.logger.printError(`网格周期执行异常: ${e}`);
    }
  }

  private async runCycleInner(): Promise<void> {
    const symbol = this.symbol;

    // 1. 净额对冲平仓归因：以链上成交补记层级状态机漏掉的平仓盈亏，
    //    先归因再风控——本轮新增亏损当轮就能被连亏熔断看到。
    //    归因出错绝不能拖垮交易周期，故兜住异常仅记日志。
    try {
      await this.gridManager.reconcileNettingCloses(symbol);
    } catch (e) {
      this.logger.printWarning(`[Grid] 净额归因异常（不影响主流程）: ${e}`);
    }

    // 2. 上一轮失败的强平重试：紧急平仓/熔断平仓失败的仓位绝不能脱管，
    //    每周期开头优先重试直到确认平掉。
    try {
      await this.gridManager.retryPendingEmergencyClose(symbol);
    } catch (e) {
      this.logger.printWarning(`[Grid] 紧急平仓重试异常: ${e}`);
    }
    await this.retryPendingForceCloses();

    // 3. 账户级风控熔断（回撤/单日亏损，按权益判定）。
    //    CLOSE_ALL：平仓撤单后结束本轮；PAUSE：只记标记，风控维护继续——
    //    历史缺陷是 PAUSE 直接 return 连带跳过 Triple Barrier 与保护单维护，
    //    暂停 4 小时期间持仓亏损不封底。
    const protectionAction = await this.checkAccountProtection();
    if (protectionAction === ProtectionAction.CLOSE_ALL_POSITIONS) return;
    const paused = protectionAction === ProtectionAction.PAUSE_NEW_TRADES;

    // 4. 净值快照 + 停机线短路（停机线只在无持仓时生效，先于屏障无风险）
    if (await this.snapshotAndHalted()) return;

    // 5. Triple Barrier 每轮必查：独立于 AI action 分支与暂停状态，
    //    KEEP_GRID/ERROR/暂停周期也兜底止损。触发即已紧急平仓，本轮跳过布单。
    if (await this.gridManager.checkBarrier(symbol)) {
      this.logger.printWarning("[网格风控] Triple Barrier 触发，已紧急平仓，跳过本轮布单");
      return;
    }

    // 6. 暂停期：不调 LLM、不布新单，但持仓的风控维护照常
    if (paused) {
      this.logger.printWarning("[网格风控]账户风控暂停新开仓：本轮跳过 AI 决策与布单，仅维护持仓保护单");
      await this.gridManager.maintainProtectiveOrders(symbol);
      return;
    }

    // 7. 行情与指标
    const rows = await this.marketFetcher.fetchOhlcv(symbol, this.timeframe, 100);
    if (!rows || rows.length === 0) {
      this.logger.printError("无法获取市场数据，跳过本轮网格周期");
      return;
    }
    const frame = TechnicalIndicators.calculateAllIndicators(rows);
    const marketData = TechnicalIndicators.getLatestIndicators(frame);
    this.logger.printMarketData(symbol, marketData);
    const trends = await TechnicalIndicators.getMultiTimeframeTrend(this.marketFetcher, symbol, {
      [this.timeframe]: rows,
    });

    // 8. 形态闸门 → 趋势过滤 → AI 决策 → 健康跟踪 → 空转自愈
    let aiDecision = await this.decide(marketData, trends, rows);
    this.trackLlmHealth(aiDecision);
    aiDecision = await this.maybeFallbackRebuild(aiDecision, marketData);

    // 9. 连亏 per-symbol 锁定：锁定期内不允许重建/扩建网格（UPDATE_GRID 降级
    //    为 KEEP_GRID，保护单维护照常）。历史缺陷：该锁只有永续路径消费，
    //    对网格策略完全无效。
    if (aiDecision.action === "UPDATE_GRID" && this.protectionManager) {
      const [locked, lockReason] = this.protectionManager.isSymbolLocked(symbol);
      if (locked) {
        this.logger.printWarning(`[网格风控]${symbol} 连亏锁定中（${lockReason}），UPDATE_GRID 降级为 KEEP_GRID`);
        aiDecision = {
          action: "KEEP_GRID",
          mode: "NEUTRAL",
          confidence: Number(aiDecision.confidence ?? 0),
          reason: `连亏锁定中：${lockReason}`,
          llm_ok: aiDecision.llm_ok ?? true,
        };
      }
    }

    // 10. 记录决策并同步网格
    const action = String(aiDecision.action ?? "UNKNOWN");
    const reason = String(aiDecision.reason ?? "");
    const decisionOk = action === "UPDATE_GRID" || action === "KEEP_GRID";
    this.logger.logDecision({
      symbol,
      marketData,
      prompt: "[GridAgent]",
      aiResponse: reason,
      decision: action,
      actionDetails: aiDecision,
      status: decisionOk ? "SUCCESS" : "ERROR",
      errorMessage: decisionOk ? null : reason,
      confidence: Number(aiDecision.confidence ?? 0),
      strategy: "grid",
    });
    await this.gridManager.syncGrid(symbol, aiDecision);
  }

  // ── 决策与趋势过滤 ────────────────────────────────────────────────────

  /**
   * 形态闸门优先，其次趋势过滤，最后才调 AI。
   *
   * 形态闸门（效率比）度量的是「均值回归 vs 单边」——网格盈亏的主要解释变量，
   * 且与参数选择基本无关。闸门命中时本轮不开任何新仓、撤掉未成交开仓单，
   * 已成交层级的 reduce_only 平仓单照常维持——只停加仓，不动退出通道。
   */
  private async decide(marketData: Dict, trends: Record<string, string>, rows: Candle[]): Promise<Dict> {
    const erMax = this.config.range_filter_er_max;
    if (erMax > 0) {
      const er = efficiencyRatio(rows, this.config.range_filter_lookback);
      // 算不出（数据不足/路程为 0）按「不放行」处理：数据异常时宁可不加仓
      if (er === null || er > erMax) {
        this.rangeGateStreak += 1;
        if (this.rangeGateStreak === 1 || this.rangeGateStreak % 12 === 0) {
          this.logger.printWarning(
            `[网格形态] 效率比 ${er === null ? "不可用" : er.toFixed(3)} > ${erMax.toFixed(2)}，判为单边行情：` +
              `本轮不开新仓、撤未成交开仓单（已连续 ${this.rangeGateStreak} 周期），持仓退出通道照常维持`,
          );
        }
        return {
          action: "KEEP_GRID",
          mode: "NEUTRAL",
          confidence: 0,
          reason: `形态闸门：效率比 ${er === null ? "不可用" : er.toFixed(3)} > ${erMax.toFixed(2)}，单边行情停止加仓`,
          llm_ok: true,
          trend_paused: true,
          allowed_open_side: "none",
          efficiency_ratio: er,
        };
      }
      if (this.rangeGateStreak) {
        this.logger.printInfo(`[网格形态] 效率比 ${er.toFixed(3)} 回落至阈值内，恢复布单（此前停 ${this.rangeGateStreak} 周期）`);
        this.rangeGateStreak = 0;
      }
    }

    const rawDir = this.config.trend_filter_enabled
      ? detectStrongTrend(trends, this.config.trend_filter_min_votes, this.config.trend_filter_timeframes)
      : 0;
    const [trendDir, flattenAllowed] = this.trendTracker.update(rawDir);

    if (trendDir !== 0) {
      const arrow = trendDir > 0 ? "上涨" : "下跌";
      const [, streak] = this.trendTracker.streak;
      // 「平逆势库存」需要更多连续确认（两种模式共用）
      if (this.config.flatten_adverse && flattenAllowed) {
        await this.gridManager.flattenAdverseInventory(this.symbol, trendDir);
      }

      // 趋势侧单边挂单：不暂停整张网格，只关掉逆势侧的开仓。中性网格在单边
      // 行情里必然把逆势库存越堆越多（上涨时卖单不断成交开空），这里改成
      // 顺势阶梯——上涨只挂买、下跌只挂卖，逆势侧交给已有层级的平仓单收尾。
      if (this.config.trend_side_only) {
        const allowed = trendDir > 0 ? "buy" : "sell";
        this.logger.printWarning(
          `[网格风控] 检测到强趋势（${arrow}，连续 ${streak} 周期确认），本轮只挂${allowed === "buy" ? "买" : "卖"}开仓单`,
        );
        const summary = await this.gridManager.getGridSummary(this.symbol);
        const decision = await this.gridAgent.makeDecision(marketData, trends, summary);
        return { ...decision, allowed_open_side: allowed, trend_side: trendDir };
      }

      this.logger.printWarning(
        `[网格风控] 检测到强趋势（${arrow}，连续 ${streak} 周期确认），本轮暂停网格加仓，仅维持减仓保护单`,
      );
      return {
        action: "KEEP_GRID",
        mode: "NEUTRAL",
        confidence: 0,
        reason: `强趋势(${arrow})暂停加仓`,
        // llm_ok：本轮压根没调 LLM，不计入 LLM 故障；
        // trend_paused：主动暂停不算空转，不得触发兜底重建
        llm_ok: true,
        trend_paused: true,
      };
    }

    if (rawDir !== 0) {
      const [, streak] = this.trendTracker.streak;
      this.logger.printInfo(
        `[网格风控] 检测到强趋势信号但未达连续确认周期 (${streak}/${this.config.trend_confirm_cycles})，本轮暂不动作`,
      );
    }
    const summary = await this.gridManager.getGridSummary(this.symbol);
    return this.gridAgent.makeDecision(marketData, trends, summary);
  }

  // ── 账户级熔断 ────────────────────────────────────────────────────────

  /**
   * 账户级熔断检查（回撤/单日亏损），返回本轮生效的保护动作。
   *
   * - CLOSE_ALL_POSITIONS：经校验的强平（网格交易对走 GridManager 紧急平仓
   *   流程：撤单+重试+强平 oid 登记+确认后清状态；其他交易对走
   *   forceClosePosition），失败的仓位登记待重试、保护记录不清理；
   * - PAUSE_NEW_TRADES：只返回动作，调用方继续做风控维护；
   * - 余额/持仓查询失败时不误熔断（返回 NONE），避免网络抖动把网格误停。
   *
   * 连亏熔断走另一条逐轮通路（GridManager 的 round-trip 回调），不在此处。
   */
  private async checkAccountProtection(): Promise<ProtectionAction> {
    if (!this.protectionManager) return ProtectionAction.NONE;

    let balanceInfo: Dict;
    try {
      balanceInfo = await this.orderManager.getAvailableBalanceInfo();
    } catch (e) {
      this.logger.printWarning(`[网格风控] 获取余额失败，跳过风控检查: ${e}`);
      return ProtectionAction.NONE;
    }
    if (balanceInfo.status !== "ok") {
      this.logger.printWarning(`[网格风控] 余额查询异常(${balanceInfo.message})，跳过风控检查`);
      return ProtectionAction.NONE;
    }
    let positions: Dict[] | null;
    try {
      positions = await this.orderManager.getCurrentPositions();
    } catch (e) {
      this.logger.printWarning(`[网格风控] 获取持仓失败，跳过风控检查: ${e}`);
      return ProtectionAction.NONE;
    }
    if (positions === null) {
      this.logger.printWarning("[网格风控] 持仓查询失败，跳过风控检查");
      return ProtectionAction.NONE;
    }

    const results = this.protectionManager.checkAll({
      balance: Number(balanceInfo.available ?? 0),
      equity: Number(balanceInfo.total ?? 0),
      unrealizedPnl: Number(balanceInfo.unrealized_pnl ?? 0),
      marginUsed: Number(balanceInfo.occupied ?? 0),
      currentPositions: positions,
      timestamp: clock.now(),
    });
    const action = ProtectionManager.getMostSevereAction(results);
    for (const r of results) this.logger.printWarning(`[网格风控]${r.reason}`);

    if (action === ProtectionAction.CLOSE_ALL_POSITIONS) {
      this.logger.printWarning("[网格风控]账户熔断触发，平掉全部持仓并撤销网格挂单");
      for (const pos of positions) {
        const sym = String(pos?.coin ?? "");
        if (!sym) continue;
        let ok: boolean;
        if (sym === this.symbol) {
          // 网格交易对：走完整紧急平仓流程（撤单+校验+重试+状态时序）
          try {
            ok = await this.gridManager.emergencyCloseSymbol(sym, "账户熔断强平");
          } catch (e) {
            this.logger.printError(`[网格风控]平仓异常 ${sym}: ${e}`);
            ok = false;
          }
          // 失败重试由 GridManager 的 pending_emergency_close 承担
        } else {
          ok = await this.forceCloseVerified(sym, "账户熔断强平");
        }
        // 只有确认平掉才清理保护插件的持仓记录：失败时保留记录，
        // 超时强平等保护对该仓位继续有效
        if (ok) this.protectionManager.onPositionDropped(sym);
      }
      // 网格交易对若无持仓，上面的循环不会触发撤单：仍需撤掉全部网格挂单，
      // 避免熔断暂停期间挂单成交重建敞口（emergencyCloseSymbol 内已含撤单，
      // cancelAllOrders 幂等）
      try {
        await this.gridManager.cancelAllOrders(this.symbol);
        this.logger.printInfo(`[网格风控]已撤销 ${this.symbol} 的全部网格挂单`);
      } catch (e) {
        this.logger.printError(`[网格风控]撤销网格挂单失败: ${e}`);
      }
    }

    return action;
  }

  /** 非网格交易对的校验式强平；失败登记待重试。 */
  private async forceCloseVerified(symbol: string, reason: string): Promise<boolean> {
    let ok: boolean;
    try {
      ok = await this.orderManager.forceClosePosition(symbol, reason);
    } catch (e) {
      this.logger.printError(`[网格风控]平仓异常 ${symbol}: ${e}`);
      ok = false;
    }
    if (ok) {
      delete this.pendingForceCloses[symbol];
      this.logger.printInfo(`[网格风控]已确认平仓: ${symbol}`);
    } else {
      this.pendingForceCloses[symbol] = reason;
      this.logger.printError(`[网格风控]平仓未成功 ${symbol}，已登记待重试`);
    }
    return ok;
  }

  /** 重试上一轮失败的非网格交易对强平（确认已无持仓时出队）。 */
  private async retryPendingForceCloses(): Promise<void> {
    if (!Object.keys(this.pendingForceCloses).length) return;
    let positions: Dict[] | null;
    try {
      positions = await this.orderManager.getCurrentPositions();
    } catch {
      positions = null;
    }
    if (positions === null) return; // 查询失败：无法确认，留待下一轮
    const held = new Set(positions.map((p) => p?.coin));
    for (const [sym, reason] of Object.entries({ ...this.pendingForceCloses })) {
      if (!held.has(sym)) {
        delete this.pendingForceCloses[sym];
        this.protectionManager?.onPositionDropped(sym);
        this.logger.printInfo(`[网格风控]${sym} 待重试强平已无持仓，出队`);
        continue;
      }
      this.logger.printWarning(`[网格风控]♻️ 重试强平 ${sym}（${reason}）`);
      if ((await this.forceCloseVerified(sym, reason)) && this.protectionManager) {
        this.protectionManager.onPositionDropped(sym);
      }
    }
  }

  // ── 净值快照与停机线 ──────────────────────────────────────────────────

  /** 记录净值快照；净值低于停机线且无持仓时短路整轮（省行情与 LLM 开销）。 */
  private async snapshotAndHalted(): Promise<boolean> {
    let balanceInfo: Dict;
    try {
      balanceInfo = await this.orderManager.getAvailableBalanceInfo();
    } catch (e) {
      this.logger.printWarning(`[Grid] 获取余额失败，跳过快照/停机检查: ${e}`);
      return false;
    }
    if (balanceInfo.status !== "ok") return false;

    const equity = Number(balanceInfo.equity ?? balanceInfo.total ?? 0) || 0;
    let positionNotional = 0;
    let hasPosition = false;
    try {
      const positions = await this.orderManager.getCurrentPositions();
      if (positions === null) {
        hasPosition = true; // 查询失败按有持仓处理，不敢停机
      } else {
        for (const pos of positions) {
          if (pos?.coin === this.symbol) {
            hasPosition = true;
            positionNotional = Math.abs(Number(pos.positionValue ?? 0) || 0);
            break;
          }
        }
      }
    } catch (e) {
      this.logger.printWarning(`[Grid] 快照取持仓失败: ${e}`);
      hasPosition = true; // 查询失败按有持仓处理，不敢停机
    }

    this.logger.logEquitySnapshot({
      equity,
      available: Number(balanceInfo.available ?? 0) || 0,
      unrealizedPnl: Number(balanceInfo.unrealized_pnl ?? 0) || 0,
      positionNotional,
      symbol: this.symbol,
    });

    const haltLine = this.config.halt_below_usd;
    if (haltLine > 0 && equity > 0 && equity < haltLine && !hasPosition) {
      this.logger.printWarning(
        `[Grid] 💤 净值 $${equity.toFixed(2)} 低于停机线 $${haltLine.toFixed(2)} 且无持仓，` +
          `跳过本轮网格周期（不拉行情/不调 LLM）`,
      );
      return true;
    }
    return false;
  }

  // ── LLM 健康跟踪与空转自愈 ────────────────────────────────────────────

  /**
   * 跟踪 LLM 连续故障，达阈值时升级告警（故障期间只告警一次）。
   *
   * 依据 GridAgent 打的 llm_ok 标——这类兜底与 AI 真实 KEEP_GRID 同形，
   * 不做标记就无从分辨（历史教训：模型下线后 13 小时无告警）。
   */
  private trackLlmHealth(aiDecision: Dict): void {
    const threshold = this.config.llm_failure_alert_cycles;
    if (threshold <= 0) return;

    // 趋势暂停周期压根没调 LLM，既不能算故障、也不能算「恢复正常」——
    // 按恢复处理会重置故障计数并打虚假恢复日志，间歇性趋势暂停可让
    // 真实的 LLM 长期故障永远攒不够告警阈值。预算触顶（llm_capped）同理。
    if (aiDecision.trend_paused || aiDecision.llm_capped) return;

    if (aiDecision.llm_ok !== false) {
      if (this.llmFailureStreak) {
        this.logger.printInfo(
          `[Grid] ✅ ${this.symbol} LLM 决策已恢复正常（此前连续失败 ${this.llmFailureStreak} 周期）`,
        );
      }
      this.llmFailureStreak = 0;
      this.llmAlertSent = false;
      return;
    }

    this.llmFailureStreak += 1;
    if (this.llmFailureStreak < threshold || this.llmAlertSent) return;

    const reason = String(aiDecision.reason ?? "").slice(0, 300);
    this.logger.printError(
      `🚨 ${this.symbol} 网格 LLM 决策连续 ${this.llmFailureStreak} 个周期失败，` +
        `网格已停止更新，仅维持减仓保护单。最近一次原因: ${reason}。` +
        `请检查 LLM 供应商模型名是否已下线、API 余额与网络连通性`,
    );
    this.llmAlertSent = true;
  }

  /**
   * LLM 持续不可用把网格拖成空转时，用纯市场数据兜底重建。
   *
   * 空转死锁：层级被清空后只有 UPDATE_GRID 能重建，而 LLM 故障期每轮只产出
   * ERROR 或兜底 KEEP_GRID——「维持现有网格」在无层级时等于维持一片空白。
   * 趋势过滤主动暂停的周期不算空转：那时候不建网格正是趋势过滤的目的。
   */
  private async maybeFallbackRebuild(aiDecision: Dict, marketData: Dict): Promise<Dict> {
    const cycles = this.config.llm_fallback_rebuild_cycles;
    if (cycles <= 0) return aiDecision;

    // 只救 LLM 故障（llm_ok=false）：LLM 健康时的连续 KEEP_GRID 是 AI 的
    // 明确决策，兜底重建去覆盖它等于凭空替 AI 下单，且日志会失真地宣称
    // 「LLM 持续不可用」。
    if (
      aiDecision.llm_ok !== false ||
      aiDecision.action === "UPDATE_GRID" ||
      aiDecision.trend_paused ||
      !(await this.gridManager.isGridIdle(this.symbol))
    ) {
      this.idleStreak = 0;
      return aiDecision;
    }

    this.idleStreak += 1;
    if (this.idleStreak < cycles) {
      this.logger.printWarning(
        `[Grid] 💤 ${this.symbol} 网格空转 ${this.idleStreak}/${cycles} 周期，达阈值后将按市场数据兜底重建`,
      );
      return aiDecision;
    }

    // 无论兜底成功与否都清零：失败时也要重新攒够 cycles 周期才再试，
    // 避免资金不足等持续性原因导致每轮都重试刷屏。
    this.idleStreak = 0;
    try {
      return await this.gridAgent.buildFallbackConfig(marketData);
    } catch (e) {
      this.logger.printError(`[Grid] 兜底重建网格失败: ${e}`);
      return aiDecision;
    }
  }
}

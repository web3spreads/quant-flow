/**
 * 调度引擎：组件装配 + 主循环。
 *
 * 职责边界：
 * - 装配所有组件（交易所客户端、行情、LLM、保护链、网格策略）；
 * - 驱动网格周期（固定间隔循环），与限价单成交监控共享一把交易锁——
 *   并发操作同一账户会踩乱持仓/挂单状态，冲突时后来者跳过本轮；
 * - 把策略执行结果接线到保护链（逐轮盈亏）；
 * - 支持热重配（看板改配置 → 优雅停循环 → 重建策略 → 重启循环，
 *   交易所连接与状态文件全程保留）。
 *
 * 决策与执行逻辑不在这里：决策在 strategy 层，执行在 trading 层。
 */

import path from "node:path";
import { AsyncMutex, sleepAbortable } from "./utils/mutex.js";
import { MarketDataFetcher } from "./data/marketData.js";
import {
  LLMClient,
  LlmUsageTracker,
  OpenAICompatBackend,
  DshLlmBackend,
  RuleGridLlmBackend,
  type DshLlmLike,
  type LLMBackend,
  type LlmUsageSnapshot,
} from "./llm.js";
import { ProtectionManager } from "./plugins/protections/index.js";
import { GridStrategy } from "./strategy/grid.js";
import { GridAgent } from "./strategy/gridAgent.js";
import { HyperliquidClient, type ExchangeClientLike } from "./trading/client.js";
import { MainnetNotionalGuard } from "./trading/notionalGuard.js";
import { UserFillStream, type FillStreamHealth, type FillSubscribe } from "./trading/fillStream.js";
import { defaultPerpFeeRates, type FeeRates } from "./fees.js";
import { TripleBarrierConfig } from "./trading/gridBarrier.js";
import { GridManager } from "./trading/gridManager.js";
import { OrderManager } from "./trading/orderManager.js";
import type { TradingLogger } from "./logger.js";
import { configFingerprint, type EngineConfig } from "./config.js";
import { clock } from "./utils/clock.js";

/** LLM 预算计数文件名（账户 data 目录下） */
export const LLM_USAGE_FILENAME = "llm-usage.json";

/**
 * 成交事件去抖窗口（毫秒）：一次批量成交会连推多笔回报，合并成一次同步即可。
 * 取值只需远小于网格周期、又足够合并一次撮合的多笔回报。
 */
const FILL_SYNC_DEBOUNCE_MS = 300;
/** 交易锁被周期占用时的重试次数与间隔（拿不到锁就等周期自己做，故重试有界） */
const FILL_SYNC_LOCK_RETRIES = 3;
const FILL_SYNC_RETRY_MS = 2_000;

/** 交易引擎：装配组件并驱动网格周期循环。 */
export class Engine {
  config: EngineConfig;
  readonly logger: TradingLogger;
  private readonly getDshLlm: () => DshLlmLike | undefined;
  isRunning = false;
  readonly startedAt = clock.now();

  // 网格周期与限价单监控共享的交易锁：冲突时后来者跳过本轮
  private readonly tradingLock = new AsyncMutex();
  private abort = new AbortController();
  private loops: Promise<void>[] = [];

  client!: ExchangeClientLike;
  /** 主网名义额闸（testnet=false 时套在客户端外；测试网为 null） */
  notionalGuard: MainnetNotionalGuard | null = null;
  /** 成交事件流（加速平仓单挂出的提示通道；回测与注入客户端时为 null） */
  fillStream: UserFillStream | null = null;
  private fillSyncTimer: NodeJS.Timeout | null = null;
  private fillSyncRetries = 0;
  private fillSyncRuns = 0;
  /** 注入的交易所客户端（回测用模拟客户端；缺省按账户配置创建真实客户端） */
  private readonly injectedClient?: ExchangeClientLike;
  /** 注入的成交订阅实现（测试用；缺省走 SDK 的 WebSocket 订阅） */
  private readonly injectedFillSubscribe?: FillSubscribe;
  private readonly injectedLlmBackend?: LLMBackend;
  private readonly manualMonitorTick: boolean;
  /** 账户实际费率（start 时拉取；模拟客户端直接给出） */
  feeRates: FeeRates = defaultPerpFeeRates();
  marketFetcher!: MarketDataFetcher;
  orderManager!: OrderManager;
  llm!: LLMClient;
  protectionManager: ProtectionManager | null = null;
  gridStrategy: GridStrategy | null = null;

  /** 账户名（多账户并行时的身份标识） */
  get name(): string {
    return this.config.name;
  }

  /** LLM 是否在交易回路中（provider=dsh/openai；rule 与注入的回测后端都不算） */
  get llmInLoop(): boolean {
    return !this.injectedLlmBackend && this.config.llm.provider !== "rule";
  }

  /** LLM 当日调用计数（不在回路时为 null） */
  llmUsage(): LlmUsageSnapshot | null {
    return this.llm?.usage?.snapshot() ?? null;
  }

  constructor(options: {
    config: EngineConfig;
    logger: TradingLogger;
    /** 惰性解析 dsh llm 服务（provider=dsh 时用；服务热替换可被感知） */
    getDshLlm?: () => DshLlmLike | undefined;
    /** 注入交易所客户端（回测/模拟）；缺省创建 HyperliquidClient */
    client?: ExchangeClientLike;
    /** 注入 LLM 后端（回测用规则/回放后端）；缺省按 llm.provider 创建 */
    llmBackend?: LLMBackend;
    /** true=限价单成交监控不自动定时，由外部驱动 runOnce（回测用） */
    manualMonitorTick?: boolean;
    /** 注入成交订阅实现（测试用）；缺省在生产路径走 SDK 的 WebSocket 订阅 */
    fillSubscribe?: FillSubscribe;
  }) {
    this.config = options.config;
    this.logger = options.logger;
    this.getDshLlm = options.getDshLlm ?? (() => undefined);
    this.injectedClient = options.client;
    this.injectedFillSubscribe = options.fillSubscribe;
    this.injectedLlmBackend = options.llmBackend;
    this.manualMonitorTick = options.manualMonitorTick ?? false;
    this.buildComponents(true);
  }

  // ── 组件装配 ──────────────────────────────────────────────────────────

  private buildComponents(fresh: boolean): void {
    const cfg = this.config;
    this.logger.printSection("🔧 初始化组件");

    // 交易所客户端在热重配时保留（连接/缓存/nonce 状态不重建；交易所配置只来自
    // 环境变量，运行期不可变，保留是安全的）
    if (fresh) {
      const raw =
        this.injectedClient ??
        new HyperliquidClient({
          privateKey: cfg.exchange.private_key,
          accountAddress: cfg.exchange.account_address,
          testnet: cfg.exchange.testnet,
          logger: this.logger,
        });
      // 主网双重闸之一：名义额硬上限套在客户端外，所有新增敞口的路径都经过它。
      // 注入的客户端同样套闸——闸门是否生效只由「是不是主网」决定
      if (cfg.exchange.testnet) {
        this.notionalGuard = null;
        this.client = raw;
      } else {
        this.notionalGuard = new MainnetNotionalGuard(raw, { capUsd: cfg.exchange.mainnet_max_notional_usd, logger: this.logger });
        this.client = this.notionalGuard;
        this.logger.printWarning(`🛑 主网实盘：名义额硬上限 $${cfg.exchange.mainnet_max_notional_usd.toFixed(2)}（超限拒单，reduce_only 不受限）`);
      }
      // 指纹每台引擎都打印：主网靠它做二次确认，测试网打印便于事先核对确定性
      try {
        this.logger.printInfo(`🔏 配置指纹: ${configFingerprint(cfg)}`);
      } catch (e) {
        this.logger.printWarning(`⚠️ 配置指纹计算失败: ${e}`);
      }
    }
    this.marketFetcher = new MarketDataFetcher(this.client, this.logger);
    this.orderManager = new OrderManager({
      client: this.client,
      defaultLeverage: cfg.trading.max_leverage,
      tradingLock: this.tradingLock,
      logger: this.logger,
      monitorAutoTick: !this.manualMonitorTick,
    });

    // 决策后端：默认规则后端（零外部请求）；dsh/openai 把 LLM 放进交易回路，
    // 必须挂预算闸并在启动时明示——事故向量要看得见
    let backend: LLMBackend;
    let usage: LlmUsageTracker | null = null;
    if (this.injectedLlmBackend) {
      backend = this.injectedLlmBackend;
    } else if (cfg.llm.provider === "rule") {
      backend = new RuleGridLlmBackend();
    } else {
      backend =
        cfg.llm.provider === "dsh"
          ? new DshLlmBackend(this.getDshLlm, cfg.llm.dsh_provider, cfg.llm.model, cfg.llm.timeout)
          : new OpenAICompatBackend(cfg.llm.base_url, cfg.llm.api_key, cfg.llm.model, cfg.llm.timeout);
    }
    if (this.llmInLoop) {
      usage = new LlmUsageTracker({
        file: path.join(cfg.paths.data_dir, LLM_USAGE_FILENAME),
        cap: cfg.llm.daily_call_cap,
        warn: (m) => this.logger.printWarning(m),
      });
    }
    this.llm = new LLMClient({ backend, model: cfg.llm.model, temperature: cfg.llm.temperature, usage });
    if (this.llmInLoop) {
      const snap = usage!.snapshot();
      this.logger.printWarning(
        `⚠️ LLM 在交易回路中（provider=${cfg.llm.provider}，${this.llm.describe()}）：` +
          `每日调用上限 ${snap.cap} 次（今日已用 ${snap.calls}），触顶后当天降级 KEEP_GRID`,
      );
    } else {
      this.logger.printInfo(`✅ 决策后端: ${this.llm.describe()}（LLM 不在交易回路）`);
    }

    this.protectionManager = null;
    if (cfg.protections.length) {
      this.protectionManager = new ProtectionManager({
        protectionsConfig: cfg.protections,
        dataDir: path.join(cfg.paths.data_dir, "protection"),
        onProtectionTriggered: (reason) => this.logger.printWarning(`[风控] 保护触发: ${reason}`),
        logger: {
          info: (m) => this.logger.printInfo(m),
          warn: (m) => this.logger.printWarning(m),
          error: (m) => this.logger.printError(m),
        },
      });
      const names = this.protectionManager.plugins.map((p) => p.name).join(", ");
      this.logger.printInfo(`✅ 保护链: ${names || "（无有效插件）"}`);
    } else {
      this.logger.printWarning("⚠️ protections 为空，账户风控已全部关闭");
    }

    // 网格策略：单交易对（symbols[0]）。Hyperliquid 是单向持仓（净头寸），一个账户只跑一套网格
    this.gridStrategy = null;
    if (cfg.trading.grid_enabled) {
      const symbol = cfg.trading.symbols[0];
      const gridManager = new GridManager({
        orderManager: this.orderManager,
        logger: this.logger,
        stateFile: path.join(cfg.paths.data_dir, "grid_state.json"),
        barrierConfig: TripleBarrierConfig.fromConfig(cfg.grid.barrier),
        onRoundTripClose: (sym, pnl, forced) => this.onGridRoundTripClose(sym, pnl, forced),
        maxPositionNotionalUsd: cfg.grid.max_position_notional_usd,
        inventoryCapRatio: cfg.grid.inventory_cap_ratio,
        postOnly: cfg.grid.post_only,
        gridLimitOrderStopLossEnabled: cfg.grid.level_trigger_stop_loss,
        gridLimitOrderTakeProfitEnabled: cfg.grid.level_trigger_take_profit,
        getFeeRates: () => this.feeRates,
        gridRebuildCooldownSeconds: cfg.grid.rebuild_cooldown_seconds,
        gridRebuildMinPriceChangeRatio: cfg.grid.rebuild_min_change_pct,
        trendFlattenSurgical: true,
        inventoryCapStrict: true,
        keepGridReconcile: true,
        nettingAttributionEnabled: true,
      });
      const gridAgent = new GridAgent({
        symbol,
        orderManager: this.orderManager,
        logger: this.logger,
        llm: this.llm,
        tradeAmount: cfg.trading.max_trade_amount,
        widthPctMin: cfg.grid.width_min_pct,
        widthPctMax: cfg.grid.width_max_pct,
        widthPctFallback: cfg.grid.width_fallback_pct,
        aiWidthBlendWeight: cfg.grid.ai_blend_weight,
        forceNeutralMode: cfg.grid.force_neutral,
        maxLeverage: cfg.trading.max_leverage,
        minGridNum: cfg.grid.min_grid_num,
        maxGridNum: cfg.grid.max_grid_num,
        capitalRatio: cfg.grid.capital_ratio,
        temperature: cfg.llm.temperature,
        getFeeRates: () => this.feeRates,
        inventorySkew: cfg.grid.inventory_skew,
        getInventoryCapUsd: () => gridManager.inventoryCapUsd(symbol),
      });
      this.gridStrategy = new GridStrategy({
        symbol,
        gridAgent,
        gridManager,
        orderManager: this.orderManager,
        marketFetcher: this.marketFetcher,
        logger: this.logger,
        gridConfig: cfg.grid,
        timeframe: cfg.trading.timeframe,
        protectionManager: this.protectionManager,
      });
      this.logger.printInfo(`✅ 网格策略: ${symbol}`);
    }

    void this.logStartupBalance();
  }

  /** 网格逐轮平仓回调：把盈亏喂给连亏熔断插件（网格风控的逐轮通路）。 */
  private onGridRoundTripClose(symbol: string, pnl: number, forced: boolean): void {
    if (!this.protectionManager) return;
    try {
      this.protectionManager.onTradeClose({ symbol, pnl: Number(pnl), forced });
    } catch (e) {
      this.logger.printWarning(`[网格风控] round-trip 盈亏上报失败: ${e}`);
    }
  }

  /**
   * 拉取账户实际费率（分档 / 返佣后的 maker/taker）。失败沿用基础费率并告警——
   * 网格止盈下限与 PnL 口径都以它为准。回测由模拟客户端直接给出。
   */
  async loadFeeRates(): Promise<FeeRates> {
    try {
      const rates = await this.client.fetchUserFeeRates();
      if (Number.isFinite(rates.makerRate) && Number.isFinite(rates.takerRate)) {
        this.feeRates = rates;
        this.logger.printInfo(
          `💸 账户费率: maker ${(rates.makerRate * 100).toFixed(4)}% / taker ${(rates.takerRate * 100).toFixed(4)}%`,
        );
      }
    } catch (e) {
      this.logger.printWarning(`⚠️ 费率查询失败，沿用基础费率: ${e}`);
    }
    return this.feeRates;
  }

  private async logStartupBalance(): Promise<void> {
    const info = await this.orderManager.getAvailableBalanceInfo();
    if (info.status === "ok") {
      this.logger.printInfo(
        `💰 账户余额: 总值 $${Number(info.total).toFixed(2)} | 可用 $${Number(info.available).toFixed(2)}`,
      );
    } else {
      this.logger.printWarning(`⚠️ 无法获取账户余额: ${info.message}`);
    }
  }

  // ── 主循环 ────────────────────────────────────────────────────────────

  /**
   * 启动引擎（非阻塞：循环在后台跑，stop() 优雅停机）。
   *
   * delayMs：启动前的错峰等待（多账户并行时由大盘按序注入，打散同 IP 齐射；
   * 走可中断睡眠，停机信号能立即打断等待中的引擎）。
   */
  start(delayMs = 0): void {
    const cfg = this.config.trading;
    if (!cfg.grid_enabled) {
      this.logger.printError("grid_enabled=false，无策略可运行（仅保留看板与风控只读视图）");
      return;
    }
    this.isRunning = true;
    this.abort = new AbortController();
    this.loops = [];
    // 错峰等待 → 拉取费率 → 进入循环（费率查询失败不阻塞启动，沿用基础费率）
    const gate: Promise<void> = (delayMs > 0 ? sleepAbortable(delayMs, this.abort.signal) : Promise.resolve())
      .then(() => this.loadFeeRates())
      .then(() => undefined);

    if (this.gridStrategy) {
      this.loops.push(gate.then(() => this.gridLoop()));
      this.logger.printInfo(
        `网格循环已启动 | 间隔: ${this.config.grid.interval_minutes} 分钟` +
          (delayMs > 0 ? ` | 错峰: ${(delayMs / 1000).toFixed(1)}s` : ""),
      );
      this.startFillStream();
    }
  }

  // ── 成交事件流（周期之外的加速通道） ──────────────────────────────────

  /**
   * 订阅本账户成交，成交一到就提前跑一次「只认领、不开仓」的同步。
   *
   * 只在有网格策略、配置开启、且不是注入客户端（回测/测试桩）时启动：回测必须
   * 保持与生产同一条代码路径，多一条事件通道会让两边的时序不再可比。
   */
  private startFillStream(): void {
    if (!this.config.grid.fill_stream_enabled) {
      this.logger.printInfo("成交事件流已关闭（grid.fill_stream_enabled=false），成交按网格周期确认");
      return;
    }
    if (this.injectedClient && !this.injectedFillSubscribe) return; // 回测/测试桩：不接事件流
    this.fillStream = new UserFillStream({
      address: this.client.address,
      testnet: this.config.exchange.testnet,
      logger: this.logger,
      onFill: (coin) => this.onFillHint(coin),
      subscribe: this.injectedFillSubscribe,
    });
    void this.fillStream.start();
  }

  /**
   * 成交提示 → 去抖 → 抢交易锁 → 即时同步。
   *
   * 去抖把一次撮合的多笔回报合并成一次同步；抢不到锁说明周期正在动账户，
   * 有界重试几次后放弃——周期自己会做同一件事，这条通道只是想更早做。
   */
  onFillHint(coin: string): void {
    if (!this.isRunning || !this.gridStrategy) return;
    if (coin !== this.gridStrategy.symbol) return; // 只关心本账户的网格交易对
    if (this.fillSyncTimer) return; // 已有待执行的同步，合并进去
    this.fillSyncRetries = 0;
    this.armFillSync(FILL_SYNC_DEBOUNCE_MS);
  }

  private armFillSync(delayMs: number): void {
    this.fillSyncTimer = setTimeout(() => {
      this.fillSyncTimer = null;
      void this.runFillSync();
    }, delayMs);
    this.fillSyncTimer.unref?.();
  }

  private async runFillSync(): Promise<void> {
    if (!this.isRunning || !this.gridStrategy) return;
    if (!this.tradingLock.tryAcquire()) {
      if (this.fillSyncRetries >= FILL_SYNC_LOCK_RETRIES) {
        this.logger.printInfo("   [Grid] 成交事件同步让位给进行中的周期（周期会完成同一件事）");
        return;
      }
      this.fillSyncRetries += 1;
      this.armFillSync(FILL_SYNC_RETRY_MS);
      return;
    }
    try {
      this.fillSyncRuns += 1;
      this.logger.printInfo("   [Grid] ⚡ 成交事件触发即时同步（只认领成交与挂平仓单，不新增敞口）");
      await this.gridStrategy.syncOnFill();
    } catch (e) {
      // 这是 fire-and-forget 的定时回调：任何漏出来的异常都会变成未捕获的 Promise
      // 拒绝，进而可能整死进程。一条加速通道绝不能有搞崩引擎的能力。
      this.logger.printError(`[Grid] 成交事件同步异常（已兜住，等周期重做）: ${e}`);
    } finally {
      this.tradingLock.release();
    }
  }

  /** 成交事件流健康状态（看板用）；未启用为 null。 */
  fillStreamHealth(): (FillStreamHealth & { syncs: number }) | null {
    if (!this.fillStream) return null;
    return { ...this.fillStream.health(), syncs: this.fillSyncRuns };
  }

  /**
   * 优雅停机：等待进行中的周期完成，避免腰斩撤单/布单序列留下裸仓。
   *
   * 等待上限必须覆盖周期内最长的单步阻塞（LLM 请求超时）：上限小于 LLM 超时
   * 时，网格循环可能正卡在 LLM 调用上，硬停即腰斩撤单/布单序列。
   */
  async stop(reason = "手动停止"): Promise<void> {
    if (!this.isRunning && !this.loops.length) return;
    this.isRunning = false;
    this.logger.printSection(`🛑 停止引擎: ${reason}`);
    this.abort.abort();
    // 先摘掉事件通道：停机过程中再触发一次同步只会和拆卸序列抢锁
    if (this.fillSyncTimer) {
      clearTimeout(this.fillSyncTimer);
      this.fillSyncTimer = null;
    }
    const stream = this.fillStream;
    this.fillStream = null;
    if (stream) await stream.stop();
    const joinTimeoutMs = Math.max(30, this.config.llm.timeout + 30) * 1000;
    if (this.loops.length) {
      this.logger.printInfo(`等待进行中的周期完成（最多 ${(joinTimeoutMs / 1000).toFixed(0)}s）...`);
      await Promise.race([
        Promise.allSettled(this.loops),
        new Promise((r) => setTimeout(r, joinTimeoutMs)),
      ]);
    }
    this.loops = [];
    await this.orderManager.shutdown();
    this.logger.printInfo("引擎已停止");
  }

  /**
   * 热重配：看板保存配置后调用。
   *
   * 顺序：停循环（等进行中的周期走完）→ 用新配置重建策略/风控/LLM
   * （交易所客户端与全部状态文件保留）→ 重启循环。持有交易锁期间不会有
   * 任何周期在动账户，重建是安全窗口。
   */
  async applyConfig(config: EngineConfig): Promise<void> {
    this.logger.printSection("♻️ 应用新配置（热重配）");
    await this.stop("配置变更，热重配");
    this.config = config;
    this.buildComponents(false);
    this.start();
  }

  // ── 网格周期循环 ──────────────────────────────────────────────────────

  /** 网格周期循环：固定间隔执行，与限价单监控共锁互斥。 */
  private async gridLoop(): Promise<void> {
    const intervalMs = Math.max(60, this.config.grid.interval_minutes * 60) * 1000;
    if (!this.config.trading.run_immediately) {
      await sleepAbortable(intervalMs, this.abort.signal);
    }
    while (this.isRunning) {
      if (this.tradingLock.tryAcquire()) {
        try {
          this.logger.printHeader(`🔄 网格周期开始 - ${clock.date().toISOString()}`);
          await this.gridStrategy!.runCycle();
          this.logger.printHeader("✅ 网格周期完成");
        } finally {
          this.tradingLock.release();
        }
      } else {
        this.logger.printWarning("⏭️ 账户正被其他操作占用，跳过本次网格周期");
      }
      await sleepAbortable(intervalMs, this.abort.signal);
    }
  }
}

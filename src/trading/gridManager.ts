/**
 * 网格交易管理器 (动态调节版)
 * 支持网格同步、AI 止盈止损、层级循环复用和状态持久化。
 *
 * 状态文件 data/grid_state.json 原子写入，进程重启后据此恢复层级与在途订单。
 */

import fs from "node:fs";
import path from "node:path";
import { Decimal, toDecimal } from "../utils/precision.js";
import { GridLevel, GridLevelState, extractOrderId } from "../utils/gridMath.js";
import { GridPnLTracker } from "./gridPnl.js";
import { GridBarrierMonitor, TripleBarrierConfig } from "./gridBarrier.js";
import { HyperliquidClient, type Dict, type LimitOrderSpec, type LimitTif } from "./client.js";
import { defaultPerpFeeRates, type FeeRates } from "../fees.js";
import type { OrderManager } from "./orderManager.js";
import type { TradingLogger } from "../logger.js";
import { clock } from "../utils/clock.js";
import { sleep } from "../utils/sleep.js";

const DEFAULT_GRID_NUM = 10;
const DEFAULT_GRID_TYPE = "GEOMETRIC";
const DEFAULT_GRID_REBUILD_COOLDOWN_SECONDS = 3600;
const DEFAULT_GRID_REBUILD_MIN_PRICE_CHANGE_RATIO = 0.01;
const DEFAULT_GRID_REBUILD_MIN_OPEN_ORDERS = 2;
// 价格越出旧区间这个比例即视为「真突破」，提前解除重建冷却。冷却拉长到 1 小时
// 的前提就是这个逃生口——否则行情走出区间后网格要干等一小时才能跟上。
const DEFAULT_GRID_REBUILD_BREAKOUT_RATIO = 0.005;

// 净额归因中保留的强平订单号上限：强平成交一般在下一轮就被归因消费，
// 留存过多只会让状态文件无谓膨胀
const MAX_FORCED_OIDS = 50;

const EXIT_MIN_ORDERS = 3;
const EXIT_MAX_ORDERS = 8;
const EXIT_TARGET_COVERAGE_RATIO = 1.0;
const EXIT_PRICE_STEPS = [0.004, 0.008, 0.012, 0.016, 0.024, 0.032, 0.040, 0.050];

// Hyperliquid 单笔订单最小名义额（USD）。低于此值的下单必被交易所拒绝。
// 含 2% 缓冲，避免价格波动导致名义额贴边后被拒。
const HL_MIN_NOTIONAL_USD = 10.0;
const HL_MIN_NOTIONAL_BUFFER = 1.02;

const nowSecs = () => clock.nowSecs();

type RoundTripCloseCallback = (symbol: string, pnl: number, forced: boolean) => void;

/** 管理网格订单的动态同步 */
export class GridManager {
  readonly orderManager: OrderManager;
  readonly logger: TradingLogger;
  readonly stateFile: string;
  state: Dict;

  // 层级循环复用：每个 symbol 对应一组 GridLevel
  gridLevels: Record<string, GridLevel[]> = {};
  // PnL 追踪：每个 symbol 对应一个 tracker
  pnlTrackers: Record<string, GridPnLTracker> = {};
  // Triple Barrier 监控：每个 symbol 对应一个 monitor
  barrierMonitors: Record<string, GridBarrierMonitor> = {};
  // 上次全量重建时间戳（用于重建冷却），从状态恢复以抵御自动重启循环导致的高频重建
  private lastRebuildTs: Record<string, number> = {};

  readonly maxPositionNotionalUsd: number;
  readonly trendFlattenSurgical: boolean;
  readonly inventoryCapStrict: boolean;
  readonly keepGridReconcile: boolean;
  readonly nettingAttributionEnabled: boolean;
  /**
   * 每轮 round-trip 平仓回调：把网格逐轮盈亏上报给账户级风控（连亏熔断）。
   * GridManager 不直接依赖 ProtectionManager，仅通过回调解耦上报，引擎负责接线。
   */
  onRoundTripClose: RoundTripCloseCallback | null;
  readonly gridLimitOrderTakeProfitEnabled: boolean;
  readonly gridLimitOrderStopLossEnabled: boolean;
  readonly gridReduceOnlyExitOrdersEnabled: boolean;
  readonly gridRebuildCooldownSeconds: number;
  readonly gridRebuildMinPriceChangeRatio: number;
  readonly barrierConfig: TripleBarrierConfig;
  /** 网格挂单只做 maker（Alo）：会立即成交的单被拒而不追价 */
  readonly postOnly: boolean;
  /** 自动库存上限倍数（max_position_notional_usd=0 时：上限 = 本代网格单侧名义额 × 倍数） */
  readonly inventoryCapRatio: number;
  /** 本轮允许的开仓方向：null=两侧都允许；"none"=两侧都不开（形态闸门） */
  private allowedOpenSide: Record<string, "buy" | "sell" | "none" | null> = {};
  /** 账户实际费率（引擎启动时从交易所拉取；缺省 Tier-0 基础费率） */
  private readonly getFeeRates: () => FeeRates;

  constructor(options: {
    orderManager: OrderManager;
    logger: TradingLogger;
    stateFile?: string;
    gridLimitOrderTakeProfitEnabled?: boolean;
    gridLimitOrderStopLossEnabled?: boolean;
    gridReduceOnlyExitOrdersEnabled?: boolean;
    gridRebuildCooldownSeconds?: number;
    gridRebuildMinPriceChangeRatio?: number;
    barrierConfig?: TripleBarrierConfig;
    onRoundTripClose?: RoundTripCloseCallback | null;
    maxPositionNotionalUsd?: number;
    inventoryCapRatio?: number;
    postOnly?: boolean;
    getFeeRates?: () => FeeRates;
    trendFlattenSurgical?: boolean;
    inventoryCapStrict?: boolean;
    keepGridReconcile?: boolean;
    nettingAttributionEnabled?: boolean;
  }) {
    this.orderManager = options.orderManager;
    this.logger = options.logger;
    this.stateFile = options.stateFile ?? "grid_state.json";
    // 网格库存硬上限（USD 净持仓名义额）：>0 时启用。净持仓名义额达此值后禁止同向加仓。
    this.maxPositionNotionalUsd = Math.max(0, this.safeFloat(options.maxPositionNotionalUsd, 0));
    this.inventoryCapRatio = Math.max(0, this.safeFloat(options.inventoryCapRatio, 1.5));
    this.postOnly = options.postOnly ?? true;
    this.getFeeRates = options.getFeeRates ?? (() => defaultPerpFeeRates());
    // 手术式平逆势库存：只平超出上限的逆势层级，保留网格挂单与层级状态（默认关闭=全量拆网）
    this.trendFlattenSurgical = !!options.trendFlattenSurgical;
    // 库存上限严格模式：名义额计入同向未成交挂单，取价/查单失败 fail-closed（默认关闭）
    this.inventoryCapStrict = !!options.inventoryCapStrict;
    // KEEP_GRID 周期对账：撤掉交易所上与本地状态无对应的非 reduce_only 残留挂单（默认关闭）
    this.keepGridReconcile = !!options.keepGridReconcile;
    // 净额对冲平仓归因：以链上成交为准补齐层级状态机漏掉的平仓盈亏（默认关闭）。
    // 开启后由 reconcileNettingCloses 独占风控上报，见 reportRoundTripClose。
    this.nettingAttributionEnabled = !!options.nettingAttributionEnabled;
    this.onRoundTripClose = options.onRoundTripClose ?? null;
    // 层级触发单默认关：网格层级的退出由 reduce_only 限价平仓单（maker）承担，
    // 库存风险由「层数 × 单格 × 库存上限 + 网格级屏障」界定。历史默认开：止损距离
    // 2×tp 小于间距×1.5，趋势中一边接货一边以 taker 割上一层；止盈触发单与限价
    // 平仓单同价，触发后以 taker 抢在 maker 之前成交，每轮费用翻倍。
    this.gridLimitOrderTakeProfitEnabled = options.gridLimitOrderTakeProfitEnabled ?? false;
    this.gridLimitOrderStopLossEnabled = options.gridLimitOrderStopLossEnabled ?? false;
    this.gridReduceOnlyExitOrdersEnabled = options.gridReduceOnlyExitOrdersEnabled ?? true;
    this.gridRebuildCooldownSeconds = Math.max(
      0,
      Math.trunc(this.safeFloat(options.gridRebuildCooldownSeconds, DEFAULT_GRID_REBUILD_COOLDOWN_SECONDS)),
    );
    this.gridRebuildMinPriceChangeRatio = Math.max(
      0,
      this.safeFloat(options.gridRebuildMinPriceChangeRatio, DEFAULT_GRID_REBUILD_MIN_PRICE_CHANGE_RATIO),
    );
    this.barrierConfig = options.barrierConfig ?? new TripleBarrierConfig();
    this.state = this.loadState();
    this.restoreLevelsFromState();
  }

  /** 从持久化状态中恢复 gridLevels、pnlTrackers 和 barrierMonitors（崩溃恢复）。 */
  private restoreLevelsFromState(): void {
    for (const [symbol, gridData] of Object.entries((this.state.active_grids ?? {}) as Dict)) {
      const grid = gridData as Dict;
      // 恢复层级
      const levelsData = grid.levels;
      if (Array.isArray(levelsData) && levelsData.length) {
        this.gridLevels[symbol] = levelsData.map((ld: Dict) => GridLevel.fromDict(ld as never));
      }
      // 恢复 PnL tracker
      if (grid.pnl && typeof grid.pnl === "object") {
        this.pnlTrackers[symbol] = GridPnLTracker.fromDict(grid.pnl);
      }
      // 恢复 barrier monitor（使用 last_sync 作为 start_time）
      const startTime = this.safeFloat(grid.last_sync, nowSecs());
      this.barrierMonitors[symbol] = new GridBarrierMonitor(this.barrierConfig, startTime);
      // 恢复上次重建时间戳：优先 last_rebuild_ts，其次旧状态的 last_sync；
      // 两者都缺失时回退到当前时间（视为「刚重建」）而非 0.0——否则崩溃/自动重启
      // 后冷却判断恒为真，会立即触发全量撤换单抖动（正是重建冷却要规避的）。
      // 安全性触发（挂单不足/参数异常）的重建不受冷却约束，不影响必要保护。
      this.lastRebuildTs[symbol] = this.safeFloat(
        grid.last_rebuild_ts || grid.last_sync,
        nowSecs(),
      );
    }
  }

  private loadState(): Dict {
    if (fs.existsSync(this.stateFile)) {
      try {
        const data = JSON.parse(fs.readFileSync(this.stateFile, "utf-8"));
        if (data && typeof data === "object") {
          if (!("active_grids" in data)) return { active_grids: {} };
          return data;
        }
      } catch (e) {
        // 状态文件损坏=层级状态机/持仓簿记全部丢失，交易所上的真实库存变成孤儿。
        // 必须醒目告警（原则：资金安全机制的失败路径必须有日志），随后由
        // 孤儿单对账/减仓保护单兜底接管。
        this.logger.printCritical(
          `网格状态文件 ${this.stateFile} 损坏，已按空状态启动（交易所侧持仓/挂单将由对账与保护单兜底接管）: ${e}`,
        );
      }
    }
    return { active_grids: {} };
  }

  /** 原子写入状态文件，防止进程中断导致文件截断损坏。 */
  private saveState(): void {
    const stateDir = path.dirname(this.stateFile) || ".";
    // 目录缺失时主备两条写入路径会一起失败且异常逃逸中断同步，先确保目录存在
    try {
      fs.mkdirSync(stateDir, { recursive: true });
    } catch {
      /* 忽略 */
    }
    const payload = JSON.stringify(this.state, null, 2);
    try {
      const tmpPath = path.join(stateDir, `.grid_state.${process.pid}.${Date.now()}.tmp`);
      fs.writeFileSync(tmpPath, payload, "utf-8");
      fs.renameSync(tmpPath, this.stateFile);
    } catch {
      // 临时文件写入失败时回退到直接写入
      try {
        fs.writeFileSync(this.stateFile, payload, "utf-8");
      } catch (e) {
        this.logger.printError(`   [Grid] ❌ 状态文件写入失败: ${e}`);
      }
    }
  }

  /** 统一入口：获取状态 -> AI 决策 -> 同步网格 */

  /**
   * 核心逻辑：根据 AI 最新的决策，同步现实中的网格状态。
   *
   * 当已有层级循环数据时，优先使用增量同步；
   * 仅在结构性变化（类型/方向改变、首次建网格）时执行全撤全建。
   */
  async syncGrid(symbol: string, aiConfig: Dict): Promise<void> {
    // 周期性清理孤儿 trigger 单（如历史遗留 TPSL），防止主网订单长期累积
    await this.cleanupOrphanTriggerOrders(symbol);

    // 本轮允许的开仓方向（趋势侧单边挂单）。逆势侧的未成交开仓单先撤掉并把
    // 层级复位——只停「加仓」，已成交层级的 reduce_only 平仓单一律不动。
    const side = aiConfig?.allowed_open_side;
    this.allowedOpenSide[symbol] = side === "buy" || side === "sell" || side === "none" ? side : null;
    if (this.allowedOpenSide[symbol]) await this.cancelDisallowedOpenOrders(symbol);

    const action = aiConfig?.action;

    // 先认领交易所成交，再判定是否重建。顺序不能反：刚成交但本地仍是
    // OPEN_PENDING 的层级，在重建判定眼里就是「还挂着的开仓单」，会被
    // 全撤全建连同持仓与 PnL 归因一起丢弃——线上表现为大量开仓成交、
    // 几乎没有平仓成交、本地零个完成的 round-trip。
    // allowOpen=false：这一趟只认领现实（确认成交、挂平仓单、结算
    // round-trip），绝不新增敞口——万一本轮要重建，刚挂出的开仓单会被
    // 立刻撤掉，中间那段窗口成交就是计划外库存。
    if (this.gridLevels[symbol]?.length) {
      await this.syncGridIncremental(symbol, false);
      if (!this.gridLevels[symbol]?.length) {
        // 同步过程中触发屏障强平并清空层级，本轮不再布单
        return;
      }
    }

    // 增量同步路由：如果已有层级数据且非重建场景，使用增量同步
    let rebuildEvaluation: [boolean, string] | null = null;
    if (action === "UPDATE_GRID" && this.gridLevels[symbol]?.length) {
      rebuildEvaluation = await this.shouldRebuildGrid(symbol, aiConfig);
      const [shouldRebuild, reason] = rebuildEvaluation;
      if (!shouldRebuild) {
        this.logger.printInfo(`   [Grid] 增量同步模式: ${reason}`);
        // 入口的认领已跑过整套状态机，这里只补挂 IDLE 层级的开仓单，
        // 不再整轮重跑（省一次挂单+成交记录查询）。
        await this.placeIdleOpenOrders(symbol);
        this.saveIncrementalState(symbol);
        return;
      }
    }

    if (action !== "UPDATE_GRID") {
      // AI 不更新网格时，只保底减仓保护单，不再补基础开仓单
      this.logger.printSection(`🛡️ 减仓保底模式 - ${symbol}`, undefined, "bold yellow");

      if (action === "KEEP_GRID") {
        this.logger.printInfo(`${symbol}: AI 返回 KEEP_GRID，本轮仅检查减仓保护单（reduce_only）`);
      } else if (action === "INSUFFICIENT_CAPITAL") {
        this.logger.printError(
          `${symbol}: 💸 资金不足以支撑最小网格，本轮拒绝布单。原因: ${aiConfig?.reason ?? "unknown"}`,
        );
      } else if (action === "ERROR") {
        this.logger.printWarning(`${symbol}: AI 决策异常 action=ERROR，reason=${aiConfig?.reason ?? "unknown"}`);
      } else if (action === undefined || action === null) {
        this.logger.printWarning(`${symbol}: AI 决策缺少 action 字段，按保守策略仅检查减仓保护单`);
      } else {
        this.logger.printWarning(`${symbol}: AI 返回未知 action=${action}，按保守策略仅检查减仓保护单`);
      }

      // 被动同步（确认成交、为已成交层级挂平仓单、结算 round-trip）已在
      // 本方法入口以 allowOpen=false 统一执行，此处不再重复整轮同步——
      // 本分支语义是「不新增敞口」，正好与入口那一趟一致。历史缺陷：
      // KEEP_GRID 周期完全冻结状态机，成交不确认、平仓单不挂，簿记与
      // 现实的脱节是结构性的。

      // 对账：撤掉交易所上与本地层级/状态无对应的非 reduce_only 残单。
      // 历史缺陷：KEEP_GRID 分支从不清理残单，靠成交后「无对应持仓」事后移除
      // （线上单日 194 次），期间残单可能意外成交产生计划外库存。
      if (this.keepGridReconcile) {
        await this.reconcileOrphanOrders(symbol);
      }

      // 网格空转告警：层级已被清空（紧急平仓/熔断后）、无持仓、交易所上也没有
      // 活跃挂单时，网格没有任何东西在工作，只能等 AI 下一次 UPDATE_GRID 重建
      // ——这段时间是纯空转，醒目提示避免误以为网格还在运行。
      if (action === "KEEP_GRID" && (await this.isGridIdle(symbol))) {
        this.logger.printWarning(
          `   [Grid] 💤 ${symbol} 网格空转中：无层级、无持仓、无挂单，等待 AI 返回 UPDATE_GRID 重建`,
        );
      }

      if (this.gridReduceOnlyExitOrdersEnabled) {
        await this.ensureMinOrders(symbol);
      } else {
        this.logger.printInfo("已关闭分批减仓单补齐，跳过 reduce_only 补齐检查");
      }
      return;
    }

    // 兼容两种格式：参数在根目录或在 parameters 下
    const params: Dict = aiConfig.parameters ?? aiConfig;
    const newLower = params.lower_price;
    const newUpper = params.upper_price;
    let newNum = params.grid_num ?? DEFAULT_GRID_NUM;
    // 增加安全检查（防止 AI 输出非法层数）
    newNum = Math.trunc(Number(newNum));
    if (!Number.isFinite(newNum) || newNum <= 0) newNum = DEFAULT_GRID_NUM;

    const newAmount = params.amount_per_grid;
    const tpRatio = params.tp_ratio;
    const slRatio = params.sl_ratio;

    if (newLower == null || newUpper == null || newAmount == null) {
      this.logger.printError(
        `   [Grid] ❌ 配置缺失: lower=${newLower}, upper=${newUpper}, amount=${newAmount}`,
      );
      return;
    }
    // 防止 AI 抽风输出 -1
    if (Number(newUpper) <= 0 || Number(newLower) <= 0) {
      this.logger.printError(`   [Grid] ❌ 非法价格区间: $${newLower} - $${newUpper}`);
      return;
    }

    this.logger.printSection(`🔄 动态调整 ${symbol} 网格`, undefined, "bold cyan");
    this.logger.printInfo(`AI 新区间: $${newLower} - $${newUpper} | TP: ${tpRatio} SL: ${slRatio}`);

    // 复用入口算过的判定：shouldRebuildGrid 会查挂单与最新价，一轮算两次
    // 既浪费配额，也可能因两次取价不同得出自相矛盾的结论。
    if (rebuildEvaluation === null) {
      rebuildEvaluation = await this.shouldRebuildGrid(symbol, aiConfig);
    }
    const [shouldRebuild, skipReason] = rebuildEvaluation;
    if (!shouldRebuild) {
      this.logger.printInfo(`   [Grid] ⏸️ 跳过重建: ${skipReason}`);
      if (this.gridReduceOnlyExitOrdersEnabled) await this.ensureMinOrders(symbol);
      return;
    }
    // 重建原因必须落日志：历史只记「跳过重建」的原因，线上 11 次/天的全量重建
    // 到底是突破、挂单不足还是参数变化，事后完全无从归因
    this.logger.printInfo(`   [Grid] 🔁 全量重建原因: ${skipReason}`);

    // 在途层级跨重建保留：已成交待平仓的层级，连同它的 reduce_only 平仓单
    // 一起带进新一代网格。历史缺陷：全量重建撤掉平仓单、又把 gridLevels
    // 整体覆盖，持仓就此变成无人认领的库存，这一轮开平仓的盈亏永远归因
    // 不了——线上表现为几百笔开仓成交、只有个位数被识别的平仓成交。
    const carriedLevels = (this.gridLevels[symbol] ?? []).filter(
      (level) => level.state === GridLevelState.OPEN_FILLED || level.state === GridLevelState.CLOSE_PENDING,
    );
    const preservedOids = new Set<number>();
    for (const level of carriedLevels) {
      if (level.state === GridLevelState.CLOSE_PENDING && level.closeOrderId !== null) {
        preservedOids.add(level.closeOrderId);
      }
    }
    if (carriedLevels.length) {
      this.logger.printInfo(
        `   [Grid] 🔒 ${symbol} 保留 ${carriedLevels.length} 个在途层级跨重建（其中 ${preservedOids.size} 个平仓单免撤）`,
      );
    }

    // 1. 彻底清理旧订单（在途层级的平仓单免撤）；若未完全撤净，停止本轮重建，避免新旧订单叠加
    const cancelAllOk = await this.cancelAllOrdersInternal(symbol, preservedOids);
    if (!cancelAllOk) {
      this.logger.printWarning("   [Grid] ⚠️ 旧网格撤单未全部成功，跳过本轮重建");
      const remainingOrders = await this.getSymbolOpenOrders(symbol);
      if (remainingOrders?.length) await this.syncLocalStateWithOrders(symbol, remainingOrders);
      return;
    }

    // 撤单后轮询确认挂单清空，若仍残留则停止本轮重建，避免新旧订单叠加
    const remainingOrders = await this.drainOpenOrdersBeforeRebuild(symbol, {
      keepOids: preservedOids,
    });
    if (remainingOrders === null) {
      this.logger.printWarning("   [Grid] ⚠️ 挂单查询失败，无法确认旧单已撤净，跳过本轮重建");
      return;
    }
    if (remainingOrders.length) {
      this.logger.printWarning(`   [Grid] ⚠️ 撤单后仍有 ${remainingOrders.length} 个挂单残留，跳过本轮重建`);
      await this.syncLocalStateWithOrders(symbol, remainingOrders);
      return;
    }

    // 2. 计算新价格分布（并按交易所精度格式化去重）
    let prices = this.calculateGridPrices(
      Number(newLower),
      Number(newUpper),
      newNum,
      String(aiConfig.grid_type ?? DEFAULT_GRID_TYPE),
    );
    prices = await this.formatGridPrices(symbol, prices);
    const currentPrice = await this.orderManager.client.getCurrentPrice(symbol);

    const buyOrders: Dict[] = [];
    const sellOrders: Dict[] = [];
    const amount = Number(newAmount);
    // 本代网格单侧名义额：自动库存上限的基数（在途层级跨代计入，见 inventoryCapUsd）
    const sideNotional = amount * Math.max(1, Math.ceil(prices.length / 2));
    aiConfig.side_notional = sideNotional;

    // 库存上限预判。宽松模式：挂限价单不改变持仓，两个布尔整轮稳定，一次算好即可。
    // 严格模式：同向挂单计入敞口，而本轮自己挂出的单不会被入口检查看见——
    // 一次性布尔会放行整轮批量挂单，把潜在同向库存推到上限数倍（线上实测：
    // 空头敞口 $14.6 < 上限 $40 放行后，一轮挂出 4×$50 卖单，潜在敞口 $214）。
    // 故严格模式改为额度制：本轮拟挂同向名义额计入预算，耗尽即跳过剩余同向单。
    // 上限按新一代网格推导（旧状态已在撤单时清空，不能再从 state 读）。
    const capUsd = this.inventoryCapUsd(symbol, sideNotional);
    let buyHeadroom: number | null = null;
    let sellHeadroom: number | null = null;
    let blockBuyOpen: boolean;
    let blockSellOpen: boolean;
    if (this.inventoryCapStrict && capUsd > 0) {
      buyHeadroom = await this.inventoryHeadroomUsd(symbol, true, capUsd);
      sellHeadroom = await this.inventoryHeadroomUsd(symbol, false, capUsd);
      blockBuyOpen = buyHeadroom <= 0;
      blockSellOpen = sellHeadroom <= 0;
    } else {
      blockBuyOpen = await this.wouldExceedInventoryCap(symbol, true, capUsd);
      blockSellOpen = await this.wouldExceedInventoryCap(symbol, false, capUsd);
    }
    if (blockBuyOpen || blockSellOpen) {
      this.logger.printWarning(
        `   [Grid] 🚧 库存达上限 $${capUsd.toFixed(0)}，本轮跳过` +
          `${blockBuyOpen ? "买" : ""}${blockSellOpen ? "卖" : ""}开仓单（防逆势累积）`,
      );
    }

    // 3. 组装整张网格，一次批量提交（交易所逐单判定，回执与条目对齐）。
    //    历史逐单下单 + 每单 1s 防限流间隔：8 格 8 次往返，「布到一半被打断」的
    //    窗口长达数秒；批量后一次请求完成。post-only（Alo）保证任何一单都不会
    //    穿价变 taker——会立即成交的单被交易所拒绝，本轮不追价。
    const tif: LimitTif = this.postOnly ? "Alo" : "Gtc";
    const specs: Array<{ spec: LimitOrderSpec; px: number; isBuy: boolean }> = [];
    let placedBuyNotional = 0;
    let placedSellNotional = 0;
    let budgetWarnedBuy = false;
    let budgetWarnedSell = false;
    const guard = await this.inventoryGuardPrices(symbol, this.safeFloat(tpRatio, 0));
    let guardSkipped = 0;
    for (const p of prices) {
      if (currentPrice === null || p === currentPrice) continue;
      const isBuy = p < currentPrice;
      if (isBuy ? blockBuyOpen : blockSellOpen) continue;
      if (this.violatesInventoryGuard(guard, isBuy, p)) {
        guardSkipped += 1;
        continue;
      }
      if (!this.sideAllowed(symbol, isBuy)) continue;
      const headroom = isBuy ? buyHeadroom : sellHeadroom;
      const placedNotional = isBuy ? placedBuyNotional : placedSellNotional;
      if (headroom !== null && placedNotional + amount > headroom) {
        if (isBuy ? !budgetWarnedBuy : !budgetWarnedSell) {
          this.logger.printWarning(
            `   [Grid] 🚧 ${isBuy ? "买" : "卖"}开仓额度耗尽（本轮已排 $${placedNotional.toFixed(0)} / 余量 $${headroom.toFixed(0)}），剩余${isBuy ? "买" : "卖"}开仓单跳过`,
          );
          if (isBuy) budgetWarnedBuy = true;
          else budgetWarnedSell = true;
        }
        continue;
      }
      // 名义额口径：amount_per_grid 已含杠杆，直接除以价格得数量
      const size = await this.orderManager.client.roundSize(symbol, amount / p);
      if (size <= 0 || size * p < HL_MIN_NOTIONAL_USD) {
        this.logger.printWarning(`   [Grid] ⚠️ 单格名义额 $${amount.toFixed(2)} @ $${p} 低于交易所最小额，跳过该格`);
        continue;
      }
      specs.push({ spec: { symbol, isBuy, size, price: p, reduceOnly: false, tif }, px: p, isBuy });
      if (isBuy) placedBuyNotional += amount;
      else placedSellNotional += amount;
    }

    if (guardSkipped) {
      this.logger.printWarning(
        `   [Grid] 🛡️ 库存守卫：${guard.unknown ? "持仓未知，本轮不新增敞口" : `持有${guard.minSellOpen !== null ? "多" : "空"}头库存（均价 $${guard.entryPx.toFixed(2)}）`}，` +
          `跳过 ${guardSkipped} 个会以亏损净额平库存的开仓单`,
      );
    }

    if (specs.length) {
      const [, levOk] = await this.orderManager.ensureLeverage(symbol);
      if (!levOk) {
        this.logger.printError("   [Grid] ❌ 杠杆设置失败，本轮不布单（下一周期按首次建网格重试）");
        return;
      }
      let receipt: Dict;
      try {
        receipt = await this.orderManager.client.placeLimitOrders(specs.map((s) => s.spec));
      } catch (e) {
        this.logger.printError(`   [Grid] ❌ 批量下单异常: ${e}`);
        return;
      }
      const views = HyperliquidClient.orderStatuses(receipt, specs.length);
      for (let i = 0; i < specs.length; i++) {
        const { px: p, isBuy, spec } = specs[i];
        const view = views[i];
        if (view.ok && view.oid) {
          (isBuy ? buyOrders : sellOrders).push({ oid: view.oid, px: p });
          this.logger.printInfo(`   [Grid] ✅ ${isBuy ? "买" : "卖"}单挂载: $${p}${view.filled ? "（立即成交）" : ""}`);
          this.logger.logTrade({
            symbol, action: isBuy ? "GRID_BUY" : "GRID_SELL", amount, price: p, orderId: String(view.oid), status: "PLACED",
          });
          if (this.gridLimitOrderStopLossEnabled || this.gridLimitOrderTakeProfitEnabled) {
            await this.orderManager.registerTpslMonitor({
              orderId: view.oid, symbol, isBuy, size: spec.size, entryPrice: p, tpRatio, slRatio,
              withTakeProfit: this.gridLimitOrderTakeProfitEnabled, withStopLoss: this.gridLimitOrderStopLossEnabled,
            });
          }
        } else if (tif === "Alo" && HyperliquidClient.isPostOnlyRejection(view.error)) {
          this.logger.printInfo(`   [Grid] ↩️ ${isBuy ? "买" : "卖"}单 @ $${p} 会立即成交，post-only 拒绝，本轮不追价`);
        } else {
          this.logger.printWarning(`   [Grid] ⚠️ ${isBuy ? "买" : "卖"}单跳过 @ $${p}: ${view.error ?? "unknown"}`);
        }
      }
    }

    // 4. 初始化层级循环复用数据
    const levels: GridLevel[] = [];
    buyOrders.forEach((orderInfo, i) => {
      const level = new GridLevel({
        id: `L${i}`,
        price: toDecimal(orderInfo.px),
        amount: toDecimal(amount),
        side: "LONG",
        state: GridLevelState.OPEN_PENDING,
      });
      level.openOrderId = orderInfo.oid;
      levels.push(level);
    });
    sellOrders.forEach((orderInfo, i) => {
      const level = new GridLevel({
        id: `L${buyOrders.length + i}`,
        price: toDecimal(orderInfo.px),
        amount: toDecimal(amount),
        side: "SHORT",
        state: GridLevelState.OPEN_PENDING,
      });
      level.openOrderId = orderInfo.oid;
      levels.push(level);
    });

    // 在途层级并入新一代：id 用 K 前缀（Kept）与新层级区分，线上日志能一眼
    // 认出「这笔是上一代带过来的仓」。层级对象原样保留，开仓成交价、累计
    // PnL、round_trip_count 都不丢，下一轮同步就能继续走完它的平仓闭环。
    carriedLevels.forEach((level, i) => {
      level.id = `K${i}`;
      levels.push(level);
    });

    this.gridLevels[symbol] = levels;

    // 全量重建即开启新一代网格：PnL tracker 必须重置——跨代际保留 realized
    // 会用旧网格的盈亏除以新网格的投入，Triple Barrier 的 PnL% 判定随之失真
    // （barrier monitor 在下方同样按新一代重置，两者口径必须一致）。
    // 代价：被保留的在途层级平仓后，盈亏记进新一代的 realized。这是有意
    // 取舍——那笔盈亏确实发生在新一代的存续期内，且金额远小于「用旧网格
    // 分母算新网格 PnL%」造成的屏障失真。
    this.pnlTrackers[symbol] = this.newPnlTracker();

    // 初始化/重置 barrier monitor
    this.barrierMonitors[symbol] = new GridBarrierMonitor(this.barrierConfig, nowSecs());

    // 5. 更新状态（含层级和 PnL 数据）
    const rebuildTs = nowSecs();
    this.lastRebuildTs[symbol] = rebuildTs;
    (this.state.active_grids as Dict)[symbol] = {
      config: aiConfig,
      buy_orders: buyOrders,
      sell_orders: sellOrders,
      levels: levels.map((level) => level.toDict()),
      pnl: this.pnlTrackers[symbol].toDict(),
      last_sync: rebuildTs,
      last_rebuild_ts: rebuildTs,
    };
    this.saveState();
    this.logger.printInfo(`✅ ${symbol} 网格调整完成。`);

    // 无论 AI 如何，始终检查减仓保护单（不强制补基础开仓单）
    if (this.gridReduceOnlyExitOrdersEnabled) await this.ensureMinOrders(symbol);

    this.logger.printInfo(
      `   [Grid] 网格已更新: 区间 [${newLower}, ${newUpper}] × ${newNum} 格，` +
        `买单 ${buyOrders.length} / 卖单 ${sellOrders.length}，原因: ${aiConfig.reason ?? "N/A"}`,
    );
  }

  // ── 重建判定与工具 ──────────────────────────────────────────────────

  private extractGridParams(config: Dict | null | undefined): Dict {
    const payload = config ?? {};
    const params: Dict = payload.parameters ?? payload;
    const gridType = String(payload.grid_type ?? params.grid_type ?? DEFAULT_GRID_TYPE).toUpperCase();
    const mode = String(payload.mode ?? params.mode ?? "NEUTRAL").toUpperCase();
    return {
      lower_price: this.safeFloat(params.lower_price, 0),
      upper_price: this.safeFloat(params.upper_price, 0),
      grid_num: Math.trunc(this.safeFloat(params.grid_num ?? DEFAULT_GRID_NUM, DEFAULT_GRID_NUM)),
      amount_per_grid: this.safeFloat(params.amount_per_grid, 0),
      grid_type: gridType,
      mode,
    };
  }

  private async hasSufficientOpenOrders(symbol: string, minOrders: number): Promise<boolean> {
    const openOrders = await this.getSymbolOpenOrders(symbol);
    if (openOrders === null) {
      // 查询失败按「充足」处理：宁可跳过一次重建，也不能因 API 抖动
      // 误判挂单不足而触发全量撤换单
      return true;
    }
    return openOrders.length >= Math.max(minOrders, 0);
  }

  async shouldRebuildGrid(symbol: string, newConfig: Dict): Promise<[boolean, string]> {
    const currentGrid = (this.state.active_grids as Dict)?.[symbol];
    if (!currentGrid) return [true, "首次建网格"];

    if (!(await this.hasSufficientOpenOrders(symbol, DEFAULT_GRID_REBUILD_MIN_OPEN_ORDERS))) {
      return [true, "当前挂单数量不足，允许重建补网格"];
    }

    const oldParams = this.extractGridParams(currentGrid.config ?? {});
    const newParams = this.extractGridParams(newConfig);
    const oldLower = oldParams.lower_price;
    const oldUpper = oldParams.upper_price;
    const newLower = newParams.lower_price;
    const newUpper = newParams.upper_price;
    const oldAmount = oldParams.amount_per_grid;
    const newAmount = newParams.amount_per_grid;

    if (Math.min(oldLower, oldUpper, newLower, newUpper) <= 0) {
      return [true, "网格参数异常，强制重建"];
    }

    // 真突破提前解除冷却：价格走出旧区间即说明这张网已经失效，再干等冷却
    // 只是让网格空挂在够不着的价位上。取价失败按「未突破」处理（fail-safe
    // 偏向不重建，宁可少跟一轮行情，也不因 API 抖动触发全量撤换单）。
    let currentPrice = 0;
    try {
      currentPrice = this.safeFloat(await this.orderManager.client.getCurrentPrice(symbol), 0);
    } catch {
      currentPrice = 0;
    }
    if (currentPrice > 0) {
      const breakoutLower = oldLower * (1 - DEFAULT_GRID_REBUILD_BREAKOUT_RATIO);
      const breakoutUpper = oldUpper * (1 + DEFAULT_GRID_REBUILD_BREAKOUT_RATIO);
      if (!(breakoutLower <= currentPrice && currentPrice <= breakoutUpper)) {
        return [
          true,
          `价格 $${currentPrice.toFixed(4)} 已突破旧区间 $${oldLower.toFixed(4)}-$${oldUpper.toFixed(4)}，提前解除重建冷却`,
        ];
      }
    }

    // 价格仍在旧区间内且持有在途层级：不重心化。重心化会把对侧开仓单挂进库存的
    // 亏损区，单向持仓下等价于在最差价位平掉库存。库存的退出由它自己的
    // reduce_only 平仓单完成，网格保持不动等价格回来；真突破仍走上面的逃生口。
    const inFlight = (this.gridLevels[symbol] ?? []).some(
      (l) => l.state === GridLevelState.OPEN_FILLED || l.state === GridLevelState.CLOSE_PENDING,
    );
    if (inFlight) return [false, "价格仍在区间内且持有在途层级，维持网格不重心化"];

    // 重建冷却：除上述安全性触发（首次/挂单不足/参数异常/真突破）外，距上次全量重建不足冷却期一律不重建。
    // 这是抑制高频撤换单的主闸——历史上 84.6% 周期触发全量重建、挂单活不过 5 分钟即源于此处缺失。
    if (this.gridRebuildCooldownSeconds > 0) {
      const lastRebuild = this.lastRebuildTs[symbol] ?? 0;
      const elapsed = nowSecs() - lastRebuild;
      if (elapsed >= 0 && elapsed < this.gridRebuildCooldownSeconds) {
        const remaining = this.gridRebuildCooldownSeconds - elapsed;
        return [
          false,
          `重建冷却中（剩余 ${remaining.toFixed(0)}s / 冷却 ${this.gridRebuildCooldownSeconds}s），维持网格`,
        ];
      }
    }

    // 层数变化不单独触发：LLM 每轮给出的 grid_num 天然抖动（10↔12），而层数
    // 本身不改变网格覆盖的价格区间，为它全撤全建纯属自伤。真正需要重建的
    // 结构性变化只有「类型」（等差/等比）与「方向」。层数差异会随下一次
    // 因区间/资金变化触发的重建自然生效。
    if (oldParams.grid_type !== newParams.grid_type || oldParams.mode !== newParams.mode) {
      return [true, "网格结构变化（类型/方向），需要重建"];
    }

    const lowerChange = Math.abs(newLower - oldLower) / Math.max(Math.abs(oldLower), 1e-9);
    const upperChange = Math.abs(newUpper - oldUpper) / Math.max(Math.abs(oldUpper), 1e-9);
    const priceChange = Math.max(lowerChange, upperChange);

    let amountChange = 0;
    if (oldAmount > 0 && newAmount > 0) {
      amountChange = Math.abs(newAmount - oldAmount) / Math.max(Math.abs(oldAmount), 1e-9);
    }

    if (priceChange < this.gridRebuildMinPriceChangeRatio && amountChange < 0.20) {
      const gridNumChanged = oldParams.grid_num !== newParams.grid_num;
      return [
        false,
        `区间变化 ${(priceChange * 100).toFixed(3)}% / 单格资金变化 ${(amountChange * 100).toFixed(2)}% ` +
          `低于阈值（层数变化=${gridNumChanged ? "True" : "False"} 不单独触发重建）`,
      ];
    }

    return [true, "满足重建条件"];
  }

  safeFloat(value: unknown, defaultValue = 0): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : defaultValue;
  }

  static isBuySide(order: Dict): boolean {
    const side = String(order?.side ?? "").trim().toUpperCase();
    return side === "B" || side === "BUY" || side === "BID";
  }

  /**
   * 查询该交易对的挂单；**查询失败返回 null**，绝不降级为空列表。
   *
   * 「查不到」与「确认没有」是不同的风控语义：增量同步把 null 当空列表
   * 会把仍挂着的订单判成已成交/已撤销，成对复制挂单、库存脱簿。
   */
  private async getSymbolOpenOrders(symbol: string, includeTrigger = false): Promise<Dict[] | null> {
    try {
      const orders = await this.orderManager.client.getOpenOrders(includeTrigger);
      if (orders === null) return null;
      return orders.filter((o) => o?.coin === symbol);
    } catch (e) {
      this.logger.printError(`   [Grid] ❌ 查询 ${symbol} 挂单失败: ${e}`);
      return null;
    }
  }

  private async cancelOrderWithRetry(symbol: string, oid: number, maxRetries = 3, retryDelayMs = 200): Promise<boolean> {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      const result = await this.orderManager.client.cancelOrder(symbol, oid);
      const status = String(result?.status ?? "").trim().toLowerCase();
      if (status === "ok") return true;
      this.logger.printWarning(
        `   [Grid] ⚠️ 撤单失败 oid=${oid} (第 ${attempt}/${maxRetries} 次): ${JSON.stringify(result)}`,
      );
      if (attempt < maxRetries) await sleep(retryDelayMs);
    }
    return false;
  }

  /**
   * 重建前尽量把残留限价单撤净；超时后返回剩余订单。
   *
   * keepOids: 白名单内的挂单既不撤也不计入「残留」——它们是在途层级有意保留
   * 的平仓单，若计入残留会让每次重建都被自己挡下。
   *
   * 返回：[]=已确认撤净；非空数组=仍有残留；null=挂单查询失败（无法确认，
   * 调用方必须跳过重建——在「不知道有没有残单」时布新网格会新旧叠加）。
   */
  private async drainOpenOrdersBeforeRebuild(
    symbol: string,
    options: { maxRounds?: number; roundSleepMs?: number; hardTimeoutMs?: number; keepOids?: Set<number> } = {},
  ): Promise<Dict[] | null> {
    const maxRounds = options.maxRounds ?? 5;
    const roundSleepMs = options.roundSleepMs ?? 400;
    const hardTimeoutMs = options.hardTimeoutMs ?? 20_000;
    const keepOids = options.keepOids ?? new Set<number>();
    const pending = (orders: Dict[]) => orders.filter((o) => !keepOids.has(o?.oid));

    let remainingOrders = await this.getSymbolOpenOrders(symbol);
    if (remainingOrders === null) return null;
    remainingOrders = pending(remainingOrders);
    if (!remainingOrders.length) return [];

    const startTime = Date.now();
    for (let roundIdx = 1; roundIdx <= maxRounds; roundIdx++) {
      for (const order of remainingOrders) {
        if (Date.now() - startTime >= hardTimeoutMs) {
          this.logger.printWarning(
            `   [Grid] ⏱️ 撤单硬超时 ${hardTimeoutMs / 1000}s，剩余 ${remainingOrders.length} 单未清`,
          );
          return remainingOrders;
        }
        const oid = order?.oid;
        if (oid == null) continue;
        await this.cancelOrderWithRetry(symbol, oid);
      }
      if (roundIdx < maxRounds) await sleep(roundSleepMs);
      remainingOrders = await this.getSymbolOpenOrders(symbol);
      if (remainingOrders === null) return null;
      remainingOrders = pending(remainingOrders);
      if (!remainingOrders.length) {
        if (roundIdx > 1) this.logger.printInfo(`   [Grid] ✅ 残留挂单已清空（重试 ${roundIdx} 轮）`);
        return [];
      }
    }
    return remainingOrders;
  }

  /**
   * 清理与当前持仓不匹配的 trigger 单（无仓或方向错误）。
   *
   * 挂单/持仓任一查询失败即整体跳过：在「不确定有没有持仓」时撤 trigger 单，
   * 可能撤掉的恰是正在保护真实持仓的止损单。
   */
  private async cleanupOrphanTriggerOrders(symbol: string): Promise<void> {
    try {
      const openOrders = await this.getSymbolOpenOrders(symbol, true);
      if (openOrders === null) return;
      const triggerOrders = openOrders.filter((o) => GridManager.isTriggerOrder(o));
      if (!triggerOrders.length) return;

      const positionSize = await this.getSymbolPositionSize(symbol);
      if (positionSize === null) {
        this.logger.printWarning(`   [Grid] ⚠️ ${symbol} 持仓查询失败，跳过孤儿 trigger 单清理`);
        return;
      }
      const hasPosition = Math.abs(positionSize) > 0;
      const closeWithBuy = positionSize < 0; // 空仓平仓要买；多仓平仓要卖
      let canceled = 0;

      for (const order of triggerOrders) {
        const oid = order?.oid;
        if (oid == null) continue;
        const shouldCancel = !hasPosition || GridManager.isBuySide(order) !== closeWithBuy;
        if (!shouldCancel) continue;
        if (await this.cancelOrderWithRetry(symbol, oid)) canceled += 1;
      }
      if (canceled) {
        this.logger.printWarning(`   [Grid] 🧹 已清理孤儿 trigger 单 ${canceled} 个（${symbol}）`);
      }
    } catch (e) {
      this.logger.printError(`   [Grid] ❌ 清理孤儿 trigger 单失败: ${e}`);
    }
  }

  /**
   * 撤掉交易所上与本地层级/状态无对应的非 reduce_only 残留挂单。
   *
   * 「孤儿单」来源：全量重建被中断、崩溃恢复后状态漂移、紧急平仓撤单失败残留等。
   * reduce_only 单不动（属于 ensureMinOrders 的减仓保护单簿记，撤了会互相打架）；
   * trigger 单由 cleanupOrphanTriggerOrders 单独治理。
   */
  private async reconcileOrphanOrders(symbol: string): Promise<void> {
    const openOrders = await this.getSymbolOpenOrders(symbol);
    if (openOrders === null || !openOrders.length) return;

    const knownOids = new Set<number>();
    for (const level of this.gridLevels[symbol] ?? []) {
      if (level.openOrderId !== null) knownOids.add(level.openOrderId);
      if (level.closeOrderId !== null) knownOids.add(level.closeOrderId);
    }
    const grid: Dict = (this.state.active_grids as Dict)?.[symbol] ?? {};
    for (const order of [...(grid.buy_orders ?? []), ...(grid.sell_orders ?? [])]) {
      if (order && typeof order === "object" && order.oid != null) knownOids.add(order.oid);
    }

    let canceled = 0;
    for (const order of openOrders) {
      const oid = order?.oid;
      if (oid == null || knownOids.has(oid)) continue;
      if (order?.reduceOnly) continue; // 减仓保护单不属于层级簿记，跳过
      if (GridManager.isTriggerOrder(order)) continue;
      if (await this.cancelOrderWithRetry(symbol, oid)) canceled += 1;
    }
    if (canceled) {
      this.logger.printWarning(`   [Grid] 🧹 对账撤掉无主残单 ${canceled} 个（${symbol}）`);
    }
  }

  /**
   * 查询该交易对带符号持仓量；**查询失败返回 null**（未知）。
   *
   * 把「查询失败」当 0（空仓）会让 trigger 清理逻辑撤掉正在保护真实持仓的
   * 止损触发单——API 抖动的瞬间恰恰是保护单最不能缺位的时刻。
   */
  private async getSymbolPositionSize(symbol: string): Promise<number | null> {
    let positions: Dict[] | null;
    try {
      positions = await this.orderManager.getCurrentPositions();
    } catch (e) {
      this.logger.printError(`   [Grid] ❌ 查询 ${symbol} 持仓失败: ${e}`);
      return null;
    }
    if (positions === null) return null;
    for (const position of positions) {
      if (position?.coin === symbol) return this.safeFloat(position.szi, 0);
    }
    return 0;
  }

  /**
   * 库存上限守卫：净持仓名义额已达上限时，禁止再往「加剧当前持仓方向」的方向开仓。
   *
   * 这是单边趋势的根因防线——中性网格在上涨里不断成交上方卖单开空，空头库存会
   * 无上限地累积，最终被市价平掉。启用后：净空头达上限就不再放行
   * 卖开仓单（但仍放行买开仓单以减仓/反向收敛），反之亦然。
   */
  private async wouldExceedInventoryCap(symbol: string, isBuyOpen: boolean, capOverride?: number): Promise<boolean> {
    const cap = capOverride ?? this.inventoryCapUsd(symbol);
    if (cap <= 0) return false; // 未启用

    if (this.inventoryCapStrict) {
      let directionalExposure: number;
      try {
        directionalExposure = await this.directionalExposureUsd(symbol, isBuyOpen);
      } catch (e) {
        // 严格模式 fail-closed：取数失败时拦截加仓。异常行情/接口抖动时恰恰是
        // 逆势库存风险最高的时刻，宽松放行（历史行为）等于风控在最需要时缺位。
        this.logger.printWarning(`   [Grid] 🚧 库存上限检查取数失败（${e}），严格模式拦截${symbol}开仓单`);
        return true;
      }
      return directionalExposure >= cap;
    }

    // ── 宽松模式：历史行为，只看已成交持仓，不看未成交挂单 ──
    const positionSize = await this.getSymbolPositionSize(symbol);
    if (positionSize === null || Math.abs(positionSize) <= 0) return false;
    let price = 0;
    try {
      price = (await this.orderManager.client.getCurrentPrice(symbol)) ?? 0;
    } catch {
      return false; // 取价失败不拦截，避免误伤正常布单
    }
    const posNotional = Math.abs(positionSize) * price;
    const posIsLong = positionSize > 0;
    const addingSameDirection = (isBuyOpen && posIsLong) || (!isBuyOpen && !posIsLong);
    if (posNotional < cap) return false;
    return addingSameDirection; // 仅在已达上限时拦截同向加仓
  }

  /** 本轮该方向是否允许开仓（趋势侧单边挂单；未设置时两侧都允许）。 */
  private sideAllowed(symbol: string, isBuy: boolean): boolean {
    const allowed = this.allowedOpenSide[symbol];
    if (!allowed) return true;
    if (allowed === "none") return false;
    return allowed === (isBuy ? "buy" : "sell");
  }

  /** 本轮生效的开仓限制描述（日志用）。 */
  private sideLabel(symbol: string): string {
    const allowed = this.allowedOpenSide[symbol];
    return allowed === "none" ? "不开仓" : allowed === "buy" ? "买" : "卖";
  }

  /**
   * 撤掉逆势侧仍挂着的开仓单并把层级复位为 IDLE。
   *
   * 只动 OPEN_PENDING 的开仓单：已成交层级（OPEN_FILLED / CLOSE_PENDING）的
   * reduce_only 平仓单是它的退出通道，撤了持仓就裸奔。撤单失败的层级保持
   * OPEN_PENDING，下一轮再试。
   */
  private async cancelDisallowedOpenOrders(symbol: string): Promise<void> {
    const levels = this.gridLevels[symbol] ?? [];
    const victims = levels.filter(
      (l) =>
        l.state === GridLevelState.OPEN_PENDING &&
        l.openOrderId !== null &&
        !this.sideAllowed(symbol, l.side === "LONG"),
    );
    if (!victims.length) return;
    const oids = victims.map((l) => l.openOrderId as number);
    let ok: boolean[];
    try {
      const receipt = await this.orderManager.client.cancelOrders(symbol, oids);
      ok = HyperliquidClient.cancelStatuses(receipt, oids.length);
    } catch (e) {
      this.logger.printWarning(`   [Grid] 逆势侧撤单异常: ${e}`);
      return;
    }
    let done = 0;
    victims.forEach((level, i) => {
      if (!ok[i]) return;
      level.reset();
      done += 1;
    });
    if (done) {
      this.logger.printWarning(
        `   [Grid] 🧭 撤掉 ${done}/${victims.length} 个不允许方向的开仓单，层级复位（本轮：${this.sideLabel(symbol)}）`,
      );
      this.saveIncrementalState(symbol);
    }
  }

  /**
   * 库存守卫价：单向持仓下，「在多头均价之下挂卖开仓单」会被交易所撮合成
   * Close Long——按亏损净额平掉库存；层级状态机对此无感（平仓单被拒后复位、
   * 下一周期同价重挂），每次成交都实现一笔亏损。这是结构性出血口，必须堵死。
   *
   * 返回：持多时卖开仓单最低价 = 均价 × (1+tp)；持空时买开仓单最高价 = 均价 × (1−tp)。
   * 持仓查询失败 fail-closed（unknown=true：两侧开仓单都不放行，本轮不新增敞口）。
   */
  private async inventoryGuardPrices(
    symbol: string,
    tpRatio: number,
  ): Promise<{ minSellOpen: number | null; maxBuyOpen: number | null; entryPx: number; unknown: boolean }> {
    let positions: Dict[] | null;
    try {
      positions = await this.orderManager.getCurrentPositions();
    } catch {
      positions = null;
    }
    if (positions === null) return { minSellOpen: null, maxBuyOpen: null, entryPx: 0, unknown: true };
    const pos = positions.find((p) => p?.coin === symbol);
    const szi = this.safeFloat(pos?.szi, 0);
    const entryPx = this.safeFloat(pos?.entryPx, 0);
    if (szi === 0 || entryPx <= 0) return { minSellOpen: null, maxBuyOpen: null, entryPx, unknown: false };
    const tp = Math.max(0, tpRatio);
    return szi > 0
      ? { minSellOpen: entryPx * (1 + tp), maxBuyOpen: null, entryPx, unknown: false }
      : { minSellOpen: null, maxBuyOpen: entryPx * (1 - tp), entryPx, unknown: false };
  }

  private violatesInventoryGuard(
    guard: { minSellOpen: number | null; maxBuyOpen: number | null; unknown: boolean },
    isBuy: boolean,
    price: number,
  ): boolean {
    if (guard.unknown) return true;
    if (isBuy) return guard.maxBuyOpen !== null && price > guard.maxBuyOpen;
    return guard.minSellOpen !== null && price < guard.minSellOpen;
  }

  /**
   * 生效的库存上限（USD 名义额）：显式 max_position_notional_usd 优先；否则按
   * inventory_cap_ratio × 本代网格单侧名义额自动推导（重建中传入新一代的单侧
   * 名义额——此时旧状态已清空）。返回 0 表示不限。
   *
   * 历史默认 0=关闭且生产未配置：趋势里唯一的库存控制是趋势过滤确认后市价砍，
   * 跨代保留的在途层级一代代累积逆势库存直到资金耗尽。
   */
  inventoryCapUsd(symbol: string, sideNotionalOverride?: number): number {
    if (this.maxPositionNotionalUsd > 0) return this.maxPositionNotionalUsd;
    if (this.inventoryCapRatio <= 0) return 0;
    let sideNotional = sideNotionalOverride ?? 0;
    if (!(sideNotional > 0)) {
      const grid: Dict | undefined = (this.state.active_grids as Dict)?.[symbol];
      const cfg: Dict = grid?.config ?? {};
      const params: Dict = cfg.parameters ?? cfg;
      sideNotional = this.safeFloat(cfg.side_notional ?? params.side_notional, 0);
    }
    return sideNotional > 0 ? sideNotional * this.inventoryCapRatio : 0;
  }

  /**
   * 严格模式口径的方向化敞口名义额（USD）。
   *
   * = 同向持仓名义额（反向持仓为负、先抵扣）+ 同向非 reduce-only 挂单名义额。
   * 取价/查单/查仓失败时抛出异常，由调用方按 fail-closed 语义处理。
   */
  private async directionalExposureUsd(symbol: string, isBuyOpen: boolean): Promise<number> {
    const positionSize = await this.getSymbolPositionSize(symbol);
    if (positionSize === null) throw new Error("持仓查询失败");
    const price = (await this.orderManager.client.getCurrentPrice(symbol)) ?? 0;
    if (price <= 0) throw new Error(`无效价格 ${price}`);

    const posNotional = Math.abs(positionSize) * price;
    const posIsLong = positionSize > 0;
    let directionalExposure: number;
    if (Math.abs(positionSize) <= 0) directionalExposure = 0;
    else if ((isBuyOpen && posIsLong) || (!isBuyOpen && !posIsLong)) directionalExposure = posNotional;
    else directionalExposure = -posNotional; // 反向持仓：新开单先抵消库存

    const openOrders = await this.getSymbolOpenOrders(symbol);
    if (openOrders === null) throw new Error("挂单查询失败");
    const sideCheck = isBuyOpen ? GridManager.isBuySide : GridManager.isSellSide;
    for (const order of openOrders) {
      if (order?.reduceOnly) continue; // reduce_only 挂单只减仓不加库存，不计入
      if (!sideCheck(order)) continue;
      directionalExposure += Math.abs(this.safeFloat(order.limitPx, 0) * this.safeFloat(order.sz, 0));
    }
    return directionalExposure;
  }

  /**
   * 严格模式下拟开方向距库存上限的剩余名义额度（USD，不小于 0）。
   * 取数失败返回 0（fail-closed，与 wouldExceedInventoryCap 一致）。
   */
  private async inventoryHeadroomUsd(symbol: string, isBuyOpen: boolean, capOverride?: number): Promise<number> {
    const cap = capOverride ?? this.inventoryCapUsd(symbol);
    try {
      const directionalExposure = await this.directionalExposureUsd(symbol, isBuyOpen);
      return Math.max(0, cap - directionalExposure);
    } catch (e) {
      this.logger.printWarning(`   [Grid] 🚧 库存额度计算取数失败（${e}），严格模式按零额度处理（${symbol}）`);
      return 0;
    }
  }

  /**
   * 趋势过滤的止血动作：当净持仓方向与趋势相反时，减掉逆势库存。
   *
   * trendDir: +1=上涨趋势, -1=下跌趋势。上涨却持空、或下跌却持多即为「逆势」。
   *
   * 两种模式（trendFlattenSurgical 开关）：
   * - 关闭：emergencyCloseAll 全量拆网——撤全部挂单、市价全平、删全部层级、
     *   重置重建冷却。每次都在摆动极值实现亏损并支付 taker 费，然后冷却重建，
     *   与网格「持库存等回归」的机制正面对抗。
   * - 开启（手术式）：只市价平掉「超出库存上限的逆势层级」，保留顺势挂单、
   *   剩余层级与重建冷却状态，网格继续运转。
   */
  async flattenAdverseInventory(symbol: string, trendDir: number): Promise<boolean> {
    if (trendDir === 0) return false;
    const positionSize = await this.getSymbolPositionSize(symbol);
    if (positionSize === null || Math.abs(positionSize) <= 0) {
      // 持仓未知时不动手：平逆势库存是主动减仓动作，必须基于确认的持仓
      return false;
    }
    const adverse = (trendDir > 0 && positionSize < 0) || (trendDir < 0 && positionSize > 0);
    if (!adverse) return false;
    if (this.trendFlattenSurgical) {
      return this.surgicalReduceAdverse(symbol, trendDir, positionSize);
    }
    this.logger.printWarning(
      `   [Grid] 🩹 趋势(${trendDir > 0 ? "涨" : "跌"})与持仓(${positionSize < 0 ? "空" : "多"})相反，市价平掉逆势库存`,
    );
    await this.emergencyCloseAll(symbol, `趋势过滤：平逆势库存 (trend_dir=${trendDir})`);
    return true;
  }

  /**
   * 手术式减仓：逐层市价平掉超出库存上限的逆势层级，网格其余部分原样保留。
   *
   * 与 emergencyCloseAll 的区别：不撤顺势挂单、不删层级数据、不动 PnL tracker
   * 与 barrier monitor、不重置重建冷却——被平层级 reset 回 IDLE，趋势解除后由
   * 增量同步自然重新挂单（重新挂单仍受库存上限约束）。
   *
   * 削减目标：逆势名义额 ≤ maxPositionNotionalUsd；上限未启用（=0）时保留一层
   * （给均值回归留出最小仓位，避免整段趋势判定期间空转）。
   * 按「入场价最差优先」平仓：多头库存平最高买入价、空头库存平最低卖出价。
   */
  private async surgicalReduceAdverse(symbol: string, trendDir: number, positionSize: number): Promise<boolean> {
    let currentPrice = 0;
    try {
      currentPrice = (await this.orderManager.client.getCurrentPrice(symbol)) ?? 0;
    } catch (e) {
      this.logger.printError(`   [Grid] 手术式减仓取价失败，跳过本轮: ${e}`);
      return false;
    }
    if (currentPrice <= 0) return false;
    const cp = toDecimal(currentPrice);

    const adverseSide = positionSize < 0 ? "SHORT" : "LONG";
    const levels = this.gridLevels[symbol] ?? [];
    const adverseLevels = levels.filter(
      (level) =>
        level.side === adverseSide &&
        (level.state === GridLevelState.OPEN_FILLED || level.state === GridLevelState.CLOSE_PENDING) &&
        level.openFillPrice !== null &&
        level.openFillAmount !== null,
    );

    let adverseNotional = adverseLevels.reduce<Decimal>((sum, level) => sum.plus(level.openFillAmount!.mul(cp)), new Decimal(0));
    let cap = toDecimal(this.inventoryCapUsd(symbol));
    if (cap.lte(0)) {
      // 上限未启用：保留一层库存
      const keepOne = adverseLevels.reduce<GridLevel | null>(
        (best, lv) => (best === null || lv.openFillAmount!.mul(cp).gt(best.openFillAmount!.mul(cp)) ? lv : best),
        null,
      );
      cap = keepOne ? keepOne.openFillAmount!.mul(cp) : new Decimal(0);
    }
    if (adverseNotional.lte(cap)) return false; // 逆势库存在允许范围内：不动，让网格自己回归

    // 入场价最差优先：多头平最高入场价，空头平最低入场价
    adverseLevels.sort((a, b) => {
      const cmp = a.openFillPrice!.comparedTo(b.openFillPrice!);
      return adverseSide === "LONG" ? -cmp : cmp;
    });

    let reducedCount = 0;
    let totalReducedPnl = new Decimal(0);
    let tracker = this.pnlTrackers[symbol];
    if (!tracker) {
      tracker = this.newPnlTracker();
      this.pnlTrackers[symbol] = tracker;
    }

    for (const level of adverseLevels) {
      if (adverseNotional.lte(cap)) break;
      // 先撤该层挂着的平仓单，避免市价平仓后 reduce_only 平仓单变孤儿
      if (level.state === GridLevelState.CLOSE_PENDING && level.closeOrderId) {
        await this.cancelOrderWithRetry(symbol, level.closeOrderId);
      }
      const closeSize = level.openFillAmount!.toNumber();
      let result: Dict;
      try {
        result = await this.orderManager.client.closePosition(symbol, closeSize);
      } catch (e) {
        this.logger.printError(`   [Grid] ${level.id} 手术式减仓下单异常: ${e}`);
        continue;
      }
      // 校验交易所内层 statuses：HL 拒单时外层仍是 status=ok，只判外层会把
      // 被拒的减仓单记成已平仓——层级假关闭、记假 PnL、库存脱管
      const [closeOk, closeErr] = HyperliquidClient.checkOrderSuccess(result);
      if (!closeOk) {
        this.logger.printWarning(`   [Grid] ${level.id} 手术式减仓失败: ${closeErr}`);
        continue;
      }
      // 登记强平订单号：净额归因接管上报时据此还原 forced 语义
      this.markForcedCloseOid(symbol, result);

      // 以当前价近似成交价记账（忽略滑点；与紧急平仓同一近似口径），
      // 复用 recordRoundTrip 保证 realized PnL / 手续费统计口径一致。
      level.closeFillPrice = cp;
      level.closeFillAmount = level.openFillAmount;
      level.closeFillTime = nowSecs();
      const pnl = tracker.recordRoundTrip(level);
      totalReducedPnl = totalReducedPnl.plus(pnl);
      adverseNotional = adverseNotional.minus(level.openFillAmount!.mul(cp));
      reducedCount += 1;

      this.logger.logTrade({
        symbol,
        action: "GRID_FORCED_REDUCE",
        amount: closeSize,
        price: cp.toNumber(),
        orderId: String(level.closeOrderId ?? ""),
        status: "FILLED",
        pnl: pnl.toNumber(),
        reason: `趋势过滤手术式减仓 (trend_dir=${trendDir}, level=${level.id})`,
      });
      // 强制平仓事件：亏损计入连亏熔断，净盈利不重置计数（见 forced 语义）
      this.reportRoundTripClose(symbol, pnl.toNumber(), true);
      level.reset();
    }

    if (reducedCount) {
      this.logger.printWarning(
        `   [Grid] 🔪 手术式减仓完成: 平掉 ${reducedCount} 个逆势层级，实现盈亏 ` +
          `${totalReducedPnl.toNumber() >= 0 ? "+" : ""}${totalReducedPnl.toNumber().toFixed(4)}，` +
          `剩余逆势名义额 $${adverseNotional.toNumber().toFixed(2)} ≤ 目标 $${cap.toNumber().toFixed(2)}`,
      );
      this.saveIncrementalState(symbol);
    }
    return reducedCount > 0;
  }

  private async getSizeStep(symbol: string): Promise<number> {
    try {
      const assetInfo = (await this.orderManager.client.getAssetInfo(symbol)) ?? {};
      const szDecimals = Math.trunc(Number(assetInfo.szDecimals ?? 3));
      return 10 ** -Math.max(0, Number.isFinite(szDecimals) ? szDecimals : 3);
    } catch {
      return 10 ** -3;
    }
  }

  private buildOrderSnapshot(order: Dict): Dict | null {
    const oid = order?.oid;
    if (oid == null) return null;
    return { oid, px: this.safeFloat(order.limitPx, 0) };
  }

  private async syncLocalStateWithOrders(symbol: string, openOrders: Dict[]): Promise<void> {
    const grid: Dict = (this.state.active_grids as Dict)?.[symbol] ?? {};
    const buyOrders: Dict[] = [];
    const sellOrders: Dict[] = [];
    for (const order of openOrders) {
      const snapshot = this.buildOrderSnapshot(order);
      if (!snapshot) continue;
      if (GridManager.isBuySide(order)) buyOrders.push(snapshot);
      else if (GridManager.isSellSide(order)) sellOrders.push(snapshot);
    }
    (this.state.active_grids as Dict)[symbol] = {
      config: grid.config ?? {},
      buy_orders: buyOrders,
      sell_orders: sellOrders,
      last_sync: nowSecs(),
      // 保留上次重建时间戳，避免残单回写重置重建冷却；两者皆空时回退 0 防止空值入库
      last_rebuild_ts: grid.last_rebuild_ts || this.lastRebuildTs[symbol] || 0,
    };
    this.saveState();
  }

  private static appendOrderCache(
    openOrders: Dict[],
    symbol: string,
    oid: number,
    sideCode: string,
    limitPrice: number,
    size = 0,
  ): void {
    openOrders.push({ oid, coin: symbol, side: sideCode, limitPx: String(limitPrice), sz: String(size) });
  }

  /** 以交易所真实挂单为准刷新本地状态。 */
  private async resyncStateWithExchange(symbol: string, openOrders: Dict[]): Promise<void> {
    const refreshed = await this.getSymbolOpenOrders(symbol);
    if (refreshed?.length) openOrders = refreshed;
    await this.syncLocalStateWithOrders(symbol, openOrders);
  }

  /** 有持仓时，强制确保存在 reduce_only 减仓挂单。 */
  private async ensurePositionExitOrders(
    symbol: string,
    currentPrice: number,
    openOrders: Dict[],
    minExitOrders = EXIT_MIN_ORDERS,
    maxExitOrders = EXIT_MAX_ORDERS,
    targetCoverageRatio = EXIT_TARGET_COVERAGE_RATIO,
  ): Promise<Dict[]> {
    if (!currentPrice || currentPrice <= 0) return openOrders;

    const positionSize = await this.getSymbolPositionSize(symbol);
    if (positionSize === null || Math.abs(positionSize) <= 0) {
      // 持仓未知时不盲目补减仓单：无仓时的 reduce_only 单会被拒/成为虚单
      return openOrders;
    }

    const closeWithBuy = positionSize < 0; // 空仓需要买单减仓；多仓需要卖单减仓
    const exitOrders = openOrders.filter((o) =>
      closeWithBuy ? GridManager.isBuySide(o) : GridManager.isSellSide(o),
    );
    const sideName = closeWithBuy ? "买" : "卖";

    const existingCount = exitOrders.length;
    if (existingCount >= maxExitOrders) return openOrders;

    let coveredSize = 0;
    let sizeFieldsFound = false;
    for (const order of exitOrders) {
      const sz = this.safeFloat(order.sz, 0);
      if (sz > 0) {
        sizeFieldsFound = true;
        coveredSize += Math.abs(sz);
      }
    }

    const absPosition = Math.abs(positionSize);
    const requiredCoverSize = absPosition * Math.max(targetCoverageRatio, 0);
    const sizeStep = await this.getSizeStep(symbol);
    if (sizeFieldsFound) {
      const coverageOk = coveredSize >= Math.max(0, requiredCoverSize - sizeStep);
      if (existingCount >= minExitOrders && coverageOk) return openOrders;
    } else {
      // 如果交易所返回里没有 sz 字段，只退化到「至少有分层减仓单」。
      if (existingCount >= minExitOrders) return openOrders;
    }

    let placed = 0;
    let projectedCovered = coveredSize;

    for (const step of EXIT_PRICE_STEPS) {
      const currentCount = existingCount + placed;
      const countOk = currentCount >= minExitOrders;
      const coverageOk = projectedCovered >= Math.max(0, requiredCoverSize - sizeStep);
      if (countOk && coverageOk) break;
      if (currentCount >= maxExitOrders) break;

      const rawPrice = closeWithBuy ? currentPrice * (1 - step) : currentPrice * (1 + step);
      const sideCode = closeWithBuy ? "B" : "A";
      const limitPrice = await this.orderManager.client.formatPrice(symbol, rawPrice);
      if (!limitPrice || limitPrice <= 0) continue;

      // 最小名义额对应的最小下单量：低于 $10 名义额的减仓单必被 HL 拒绝，
      // 历史上这里退化到 size_step（约 $0.17）产生了上万笔灰尘虚单。
      const minNotionalSize = (HL_MIN_NOTIONAL_USD * HL_MIN_NOTIONAL_BUFFER) / limitPrice;
      const remainingToCover = Math.max(requiredCoverSize - projectedCovered, 0);
      if (remainingToCover <= 0) break;

      let targetLayersLeft = Math.max(minExitOrders - currentCount, 1);
      // 资金有限时动态合并层级：若按 targetLayersLeft 拆分会使单层低于最小名义额，
      // 则减少层数，确保每层都 ≥ $10 且持仓能被完全覆盖。
      if (minNotionalSize > 0) {
        const maxLayersByNotional = Math.max(1, Math.trunc(remainingToCover / minNotionalSize));
        targetLayersLeft = Math.min(targetLayersLeft, maxLayersByNotional);
      }
      let orderSize = remainingToCover / targetLayersLeft;
      // 抬到最小名义额，但不超过剩余待覆盖持仓
      orderSize = Math.max(orderSize, minNotionalSize);
      orderSize = Math.min(orderSize, remainingToCover);
      // 量化到合约最小步长（向上取整避免低于最小名义额；reduce_only 略微超出由交易所截断）
      if (sizeStep > 0) {
        orderSize = Number((Math.ceil(orderSize / sizeStep) * sizeStep).toFixed(10));
      }

      // 若连整笔剩余持仓都凑不到 $10 名义额，则无法下合法减仓单，停止补单
      if (orderSize <= 0 || orderSize * limitPrice < HL_MIN_NOTIONAL_USD) {
        this.logger.printWarning(
          `   [Grid] ⏭️ 剩余待覆盖持仓名义额不足 $${HL_MIN_NOTIONAL_USD.toFixed(0)}，` +
            `跳过减仓补单（剩余 ${remainingToCover.toFixed(6)}）`,
        );
        break;
      }

      const result = await this.placeReduceOnlyLimit(symbol, closeWithBuy, orderSize, limitPrice);
      // 校验内层 statuses：拒单（外层仍 ok）不得计入覆盖率与已挂单数
      const [orderOk, orderErr] = HyperliquidClient.checkOrderSuccess(result);
      if (orderOk) {
        const oid = this.extractOid(result);
        if (oid !== null) {
          GridManager.appendOrderCache(openOrders, symbol, oid, sideCode, limitPrice, orderSize);
        }
        // 覆盖率按实际覆盖量累计：orderSize 经 ceil 取整可能略超 remainingToCover，
        // 但 reduce_only 单实际成交被交易所截断到剩余持仓，若按 orderSize 累计会过计、
        // 提前判定覆盖完成而漏挂后续保护单。故按 min(orderSize, remainingToCover) 计。
        projectedCovered += Math.min(orderSize, remainingToCover);
        placed += 1;
        this.logger.printWarning(
          `   [Grid] 🛟 补减仓${sideName}单: ${orderSize.toFixed(6)} @ $${limitPrice} (reduce_only)`,
        );
        this.logger.logTrade({
          symbol,
          action: closeWithBuy ? "GRID_REDUCE_BUY" : "GRID_REDUCE_SELL",
          amount: orderSize,
          price: limitPrice,
          orderId: oid !== null ? String(oid) : "",
          status: "PLACED",
        });
      } else {
        this.logger.printWarning(`   [Grid] ⚠️ 减仓${sideName}单被拒 @ $${limitPrice}: ${orderErr}`);
      }
    }

    return openOrders;
  }

  private extractOid(limitOrderRes: Dict | null | undefined): number | null {
    return extractOrderId(limitOrderRes);
  }

  /**
   * 计算网格价格分布，内部使用 Decimal 精确计算。
   *
   * 不做 tick 对齐：历史上此处硬编码 0.1 步进，低价交易对（如 $0.12 档）的
   * 全部格子会被拍扁到同一价位。价格精度统一交给下单前的 client.formatPrice
   * （按交易对 szDecimals 与 5 位有效数字规则）。
   */
  private calculateGridPrices(lower: number, upper: number, num: number, gridType: string): number[] {
    if (num < 2) return [toDecimal(lower).toNumber()];
    const dLower = toDecimal(lower);
    const dUpper = toDecimal(upper);
    const prices: number[] = [];
    if (gridType === "ARITHMETIC") {
      const diff = dUpper.minus(dLower).div(num - 1);
      for (let i = 0; i < num; i++) prices.push(dLower.plus(diff.mul(i)).toNumber());
    } else {
      // GEOMETRIC：使用 Decimal 的 ln/exp 实现精确分数幂
      if (dLower.lte(0) || dUpper.lte(0)) return [dLower.toNumber()];
      const logRatio = dUpper.div(dLower).ln().div(num - 1);
      for (let i = 0; i < num; i++) prices.push(dLower.mul(logRatio.mul(i).exp()).toNumber());
    }
    return prices;
  }

  /**
   * 按交易所价格精度格式化网格价位并去重（保持原有顺序）。
   * 粗精度交易对上相邻格子可能格式化后撞到同一价位，去重避免重复挂单。
   */
  private async formatGridPrices(symbol: string, prices: number[]): Promise<number[]> {
    const formatted: number[] = [];
    for (const p of prices) {
      const fp = await this.orderManager.client.formatPrice(symbol, p);
      if (fp && fp > 0 && !formatted.includes(fp)) formatted.push(fp);
    }
    if (formatted.length < prices.length) {
      this.logger.printWarning(`   [Grid] 价格精度合并: ${prices.length} 格 → ${formatted.length} 格（${symbol}）`);
    }
    return formatted;
  }

  /**
   * 撤销 symbol 的全部挂单并清理本地网格状态。
   *
   * keepOids: 白名单，这些 oid 不撤。仅用于全量重建时保住在途层级的
   * reduce_only 平仓单——撤了它们，持仓在重建到补保护单之间就是裸奔。
   * 账户级熔断走公共入口 cancelAllOrders，不传白名单，全撤。
   */
  private async cancelAllOrdersInternal(symbol: string, keepOids?: Set<number>): Promise<boolean> {
    const keep = keepOids ?? new Set<number>();
    // 优先用交易所真实挂单清理（含 trigger），避免本地 state 漂移导致漏撤单
    let allCanceled = true;
    const canceledOids = new Set<number>();
    let openOrders = await this.getSymbolOpenOrders(symbol, true);
    if (openOrders === null) {
      // 查询失败：无法确认撤净，按未全部成功处理（调用方不得据此重建），
      // 仍继续用本地 state 记录的 oid 补撤
      allCanceled = false;
      openOrders = [];
    }
    for (const order of openOrders) {
      const oid = order?.oid;
      if (oid == null || keep.has(oid)) continue;
      try {
        if (await this.cancelOrderWithRetry(symbol, oid)) canceledOids.add(oid);
        else allCanceled = false;
      } catch (e) {
        this.logger.printWarning(`   [Grid] ⚠️ 撤单异常 oid=${oid}: ${e}`);
        allCanceled = false;
      }
    }

    // 回退：补撤 state 中仍记录但交易所列表里未返回的 oid
    const grid: Dict | undefined = (this.state.active_grids as Dict)?.[symbol];
    if (grid) {
      const localOids = [...(grid.buy_orders ?? []), ...(grid.sell_orders ?? [])]
        .filter((o: unknown) => o && typeof o === "object")
        .map((o: Dict) => o.oid);
      for (const oid of localOids) {
        if (oid == null || canceledOids.has(oid) || keep.has(oid)) continue;
        try {
          if (await this.cancelOrderWithRetry(symbol, oid)) canceledOids.add(oid);
          else allCanceled = false;
        } catch {
          allCanceled = false;
        }
      }
    }

    if ((this.state.active_grids as Dict)?.[symbol]) {
      delete (this.state.active_grids as Dict)[symbol];
      this.saveState();
    }
    return allCanceled;
  }

  /**
   * 撤销指定 symbol 的全部网格挂单（含 trigger）并清理本地网格状态。
   *
   * 公共入口，供引擎在账户级风控熔断（CLOSE_ALL_POSITIONS）时调用，
   * 避免熔断期间网格挂单成交新增敞口。返回 true 表示全部撤销成功。
   */
  async cancelAllOrders(symbol: string): Promise<boolean> {
    return this.cancelAllOrdersInternal(symbol);
  }

  static isSellSide(order: Dict): boolean {
    const side = String(order?.side ?? "").trim().toUpperCase();
    return side === "A" || side === "ASK" || side === "SELL";
  }

  static isTriggerOrder(order: Dict): boolean {
    const orderType = order?.orderType ?? {};
    return typeof orderType === "object" && orderType !== null && "trigger" in orderType;
  }

  /** 保底逻辑：仅检查并补齐 reduce_only 减仓保护单。 */
  private async ensureMinOrders(symbol: string): Promise<void> {
    try {
      const currentPrice = await this.orderManager.client.getCurrentPrice(symbol);
      if (!currentPrice || currentPrice <= 0) {
        this.logger.printWarning(`   [Grid] ⚠️ 无法获取 ${symbol} 当前价格，跳过补单`);
        return;
      }
      let openOrders = await this.getSymbolOpenOrders(symbol);
      if (openOrders === null) {
        // 查不到现有挂单时盲目补单会重复堆叠减仓单，跳过本轮
        this.logger.printWarning(`   [Grid] ⚠️ ${symbol} 挂单查询失败，跳过补单`);
        return;
      }
      openOrders = await this.ensurePositionExitOrders(symbol, currentPrice, openOrders);
      await this.resyncStateWithExchange(symbol, openOrders);
    } catch (e) {
      this.logger.printError(`   [Grid] ❌ ensure_min_orders 失败: ${e}`);
    }
  }

  /** 增强版网格摘要：包含层级状态分布和 PnL 报告。 */
  async getGridSummary(symbol: string): Promise<string> {
    const grid: Dict | undefined = (this.state.active_grids as Dict)?.[symbol];
    if (!grid) return "目前无运行中的网格。";

    const config: Dict = grid.config ?? {};
    const params: Dict = config.parameters ?? config;
    let baseInfo =
      `当前正在运行 ${symbol} 天地单网格：\n` +
      `- 区间: $${params.lower_price ?? "N/A"} - $${params.upper_price ?? "N/A"}\n` +
      `- 止盈比例: ${params.tp_ratio ?? "N/A"}\n` +
      `- 待成交买单: ${(grid.buy_orders ?? []).length} 个\n` +
      `- 待成交卖单: ${(grid.sell_orders ?? []).length} 个`;

    // 层级状态分布
    const levels = this.gridLevels[symbol];
    if (levels?.length) {
      const stateCounts: Record<string, number> = {};
      for (const level of levels) stateCounts[level.state] = (stateCounts[level.state] ?? 0) + 1;
      baseInfo += `\n- 层级状态: ${JSON.stringify(stateCounts)}`;
    }

    // PnL 报告
    const tracker = this.pnlTrackers[symbol];
    if (tracker && levels?.length) {
      try {
        const currentPrice = await this.orderManager.client.getCurrentPrice(symbol);
        if (currentPrice && currentPrice > 0) {
          const currentPriceD = toDecimal(currentPrice);
          const totalInvestment = levels.reduce<Decimal>((sum, level) => sum.plus(level.amount), new Decimal(0));
          const summary = tracker.getSummary(levels, currentPriceD, totalInvestment) as Dict;
          const fmt = (d: unknown) => {
            const n = (d as Decimal).toNumber();
            return `${n >= 0 ? "+" : ""}${n.toFixed(4)}`;
          };
          baseInfo +=
            `\n- 已实现 PnL: ${fmt(summary.realized_pnl)} USDT` +
            `\n- 未实现 PnL: ${fmt(summary.unrealized_pnl)} USDT` +
            `\n- 净 PnL: ${fmt(summary.net_pnl)} (${(summary.net_pnl_pct as Decimal).mul(100).toNumber().toFixed(2)}%)` +
            `\n- 完成轮回: ${summary.completed_round_trips} 次` +
            `\n- 累计手续费: ${(summary.total_fees as Decimal).toNumber().toFixed(4)} USDT`;
        }
      } catch (e) {
        this.logger.printError(`   [Grid] 获取网格摘要 PnL 时出错: ${e}`);
      }
    }
    return baseInfo;
  }

  // ────────────────────────────────────────────────────────────────
  // 紧急平仓与 Triple Barrier
  // ────────────────────────────────────────────────────────────────

  /**
   * 紧急全部平仓（Triple Barrier / 趋势过滤全量模式 / 账户熔断共用）。
   *
   * 平仓结果必须校验：closePosition 吞异常返回的 {"status":"error"} 是真值
   * 对象，历史实现 `if result:` 恒真——平仓失败被打印成「完成」，随后层级/
   * 屏障/PnL 状态被无条件删除，遗留持仓的 -5% 网格级兜底止损永久失效。
   * 现在走 emergencyCloseWithRetry（校验内层 statuses + 指数退避重试 + 全平
   * 兜底），只有确认成功才清理风控状态；失败则保留全部状态并登记
   * pending_emergency_close 待重试标记——下一周期由 retryPendingEmergencyClose
   * 重试，且屏障因状态未删会再次触发，双通道确保失败仓位不脱管。
   *
   * 返回 true=已确认无持仓（平仓成功或本就无仓）；false=平仓失败，待重试。
   */
  async emergencyCloseAll(symbol: string, reason: string): Promise<boolean> {
    this.logger.printWarning(`   [Grid] 紧急平仓: ${reason}`);

    // 1. 撤掉所有挂单（先停止新成交，再处理持仓）
    await this.cancelAllOrdersInternal(symbol);

    // 2. 在删除层级数据之前，用 OPEN_FILLED 层的未实现盈亏近似本次强平的
    //    已实现盈亏（忽略滑点/taker 费，量级足够驱动连亏判定）
    let closedPnl: Decimal | null = null;
    let closeAmount = new Decimal(0);
    let closePrice: number | null = null;
    try {
      const tracker = this.pnlTrackers[symbol];
      const levels = this.gridLevels[symbol];
      if (tracker && levels?.length) {
        const cp = await this.orderManager.client.getCurrentPrice(symbol);
        if (cp && cp > 0) {
          closePrice = cp;
          closedPnl = tracker.calculateUnrealizedPnl(levels, toDecimal(cp));
          closeAmount = levels.reduce<Decimal>(
            (sum, lv) =>
              (lv.state === GridLevelState.OPEN_FILLED || lv.state === GridLevelState.CLOSE_PENDING) &&
              lv.openFillAmount !== null
                ? sum.plus(lv.openFillAmount)
                : sum,
            new Decimal(0),
          );
        }
      }
    } catch (e) {
      this.logger.printWarning(`   [Grid] 紧急平仓盈亏预估失败: ${e}`);
    }

    // 3. 市价平仓（校验 + 重试 + 全平兜底）。确认无持仓时无需下单。
    const positionSize = await this.getSymbolPositionSize(symbol);
    let closeOk = true;
    let result: Dict | null = null;
    if (positionSize === null || Math.abs(positionSize) > 0) {
      try {
        [closeOk, result] = await this.orderManager.client.emergencyCloseWithRetry(symbol, null, { reason });
      } catch (e) {
        this.logger.printError(`   [Grid] ${symbol} 市价平仓异常: ${e}`);
        closeOk = false;
      }
      if (closeOk && result) {
        this.logger.printInfo(`   [Grid] ${symbol} 市价平仓完成`);
        // 登记强平订单号：净额归因接管上报时据此还原 forced 语义
        this.markForcedCloseOid(symbol, result);
      }
    }

    if (!closeOk) {
      // 平仓失败：保留层级/屏障/PnL 状态（保住兜底止损判定与盈亏口径），
      // 登记待重试标记并落盘，绝不谎报成功
      (this.state.pending_emergency_close ??= {})[symbol] = reason;
      this.saveState();
      this.logger.printError(
        `   [Grid] ❌ ${symbol} 紧急平仓未成功，已登记待重试（风控状态保留），下一周期将自动重试。原因: ${reason}`,
      );
      return false;
    }

    // 4. 平仓确认成功：上报强平盈亏（forced=true，净盈利不得重置连亏计数）
    try {
      if (closedPnl !== null && closedPnl.abs().gt(0) && closePrice) {
        // 净额归因启用时，实际盈亏由下一周期的 GRID_NET_CLOSE（链上 closedPnl）
        // 记录——此处的估算（未实现盈亏近似，可能混入被净额对冲的幻影层级）
        // 若也写进 pnl 字段，下游统计会把同一笔强平双算且口径失真（线上实测
        // 预估 -2.43 vs 链上实际 -0.30），故仅在 reason 中留痕。
        // 归因关闭时估算是唯一记录，保留原行为。
        const estimateOnly = this.nettingAttributionEnabled;
        const pnlNum = closedPnl.toNumber();
        this.logger.logTrade({
          symbol,
          action: "GRID_EMERGENCY_CLOSE",
          amount: closeAmount.toNumber(),
          price: closePrice,
          orderId: "",
          status: "FILLED",
          pnl: estimateOnly ? null : pnlNum,
          reason: estimateOnly
            ? `${reason} | 预估盈亏 ${pnlNum >= 0 ? "+" : ""}${pnlNum.toFixed(4)}（实际以 GRID_NET_CLOSE 为准）`
            : reason,
        });
        this.reportRoundTripClose(symbol, pnlNum, true);
      }
    } catch (e) {
      this.logger.printWarning(`   [Grid] 紧急平仓盈亏上报风控失败: ${e}`);
    }

    // 5. 清理层级数据与待重试标记
    this.clearSymbolRiskState(symbol);
    this.logger.printWarning(`   [Grid] 网格已清空（紧急平仓完成: ${reason}）`);
    return true;
  }

  /** 紧急平仓确认成功后清理该交易对的层级/屏障/PnL 状态与待重试标记。 */
  private clearSymbolRiskState(symbol: string): void {
    delete this.gridLevels[symbol];
    delete this.barrierMonitors[symbol];
    delete this.pnlTrackers[symbol];
    const pending: Dict = this.state.pending_emergency_close ?? {};
    if (symbol in pending) {
      delete pending[symbol];
      this.saveState();
    }
  }

  /**
   * 重试上一轮未成功的紧急平仓（每个网格周期开头调用）。
   *
   * 持仓已消失（重试期间被交易所侧成交/人工处理）时只做状态收尾，
   * 不再下平仓单；仍有持仓则重走完整紧急平仓流程。
   */
  async retryPendingEmergencyClose(symbol: string): Promise<void> {
    const pending: Dict = this.state.pending_emergency_close ?? {};
    const reason = pending[symbol];
    if (!reason) return;
    const positionSize = await this.getSymbolPositionSize(symbol);
    if (positionSize !== null && Math.abs(positionSize) <= 0) {
      this.logger.printInfo(`   [Grid] ${symbol} 待重试的紧急平仓已无持仓（可能已被平掉），完成状态清理`);
      this.clearSymbolRiskState(symbol);
      return;
    }
    this.logger.printWarning(`   [Grid] ♻️ 重试上一轮未成功的紧急平仓: ${reason}`);
    await this.emergencyCloseAll(symbol, reason);
  }

  /**
   * 公开入口：账户级熔断对网格交易对的强平。
   *
   * 统一走紧急平仓流程：撤单 → 校验平仓（重试+兜底）→ 强平 oid 登记
   * （净额归因还原 forced 语义）→ 成功后才清理风控状态。
   */
  async emergencyCloseSymbol(symbol: string, reason: string): Promise<boolean> {
    return this.emergencyCloseAll(symbol, reason);
  }

  /**
   * Triple Barrier 兜底检查（独立方法，供每个网格周期开头无条件调用）。
   *
   * 历史问题：barrier 仅在增量同步的窄分支里检查，AI 频繁返回 KEEP_GRID/ERROR
   * 时根本走不到那里，导致 -5%/超时等兜底止损长期形同虚设。提取为独立方法后
   * 由网格周期每轮先调用，不受 AI action 分支影响。触发即紧急平仓。
   *
   * 返回 true 表示已触发屏障并紧急平仓（本轮应跳过后续布单）。
   */
  async checkBarrier(symbol: string): Promise<boolean> {
    const levels = this.gridLevels[symbol];
    const monitor = this.barrierMonitors[symbol];
    const tracker = this.pnlTrackers[symbol];
    if (!levels?.length || !monitor || !tracker) return false;
    try {
      const currentPriceRaw = await this.orderManager.client.getCurrentPrice(symbol);
      if (!currentPriceRaw || currentPriceRaw <= 0) return false;
      const currentPriceD = toDecimal(currentPriceRaw);
      const totalInvestment = levels.reduce<Decimal>((sum, level) => sum.plus(level.amount), new Decimal(0));
      const netPnlPct = tracker.getNetPnlPct(levels, currentPriceD, totalInvestment);
      const trigger = monitor.check(currentPriceD, netPnlPct, nowSecs());
      if (trigger) {
        this.logger.printWarning(`   [Grid] Triple Barrier 触发: ${trigger}`);
        await this.emergencyCloseAll(symbol, trigger);
        return true;
      }
    } catch (e) {
      this.logger.printError(`   [Grid] Triple Barrier 检查异常: ${e}`);
    }
    return false;
  }

  // ────────────────────────────────────────────────────────────────
  // 层级循环复用：增量同步
  // ────────────────────────────────────────────────────────────────

  /**
   * 增量同步：只处理需要操作的层级，不全撤全建。
   *
   * allowOpen=false 时为「被动同步」——只确认成交、维护平仓单与 round-trip
   * 记账，不为 IDLE 层级挂新开仓单。KEEP_GRID/趋势暂停周期用此模式：层级
   * 状态机继续跟上现实（历史缺陷：KEEP_GRID 周期完全冻结状态机，成交不确认、
   * 平仓单不挂，簿记结构性脱节），但不新增敞口。
   *
   * 挂单/成交记录任一查询失败即整轮跳过：把「查不到」当「订单消失」会把
   * 仍挂着的订单成对复制（先误判为撤销回 IDLE 再重挂）、把已成交库存
   * 移出簿记（fills 查询失败时误判为撤销），这是资金安全级错误。
   */
  async syncGridIncremental(symbol: string, allowOpen = true): Promise<void> {
    const levels = this.gridLevels[symbol];
    if (!levels?.length) {
      this.logger.printWarning(`   [Grid] ${symbol} 无层级数据，跳过增量同步`);
      return;
    }

    // Triple Barrier 屏障检查（与网格周期顶层调用同一方法；此处保留以覆盖
    // 非周期路径直接调用 syncGrid 的情况，已平仓时幂等返回 false）
    if (await this.checkBarrier(symbol)) return;

    const exchangeOrders = await this.getSymbolOpenOrders(symbol);
    if (exchangeOrders === null) {
      this.logger.printWarning(`   [Grid] ⚠️ ${symbol} 挂单查询失败，跳过本轮增量同步（防止误判订单状态）`);
      return;
    }
    const exchangeOids = new Set(exchangeOrders.filter((o) => "oid" in o).map((o) => o.oid));

    // 统一获取一次成交记录，避免每个层级重复调用 API 触发频率限制
    let cachedFills: Dict[] | null;
    try {
      cachedFills = await this.orderManager.client.userFills();
    } catch (e) {
      this.logger.printError(`   [Grid] 批量查询成交记录失败: ${e}`);
      cachedFills = null;
    }
    if (cachedFills === null || cachedFills === undefined) {
      this.logger.printWarning(`   [Grid] ⚠️ ${symbol} 成交记录查询失败，跳过本轮增量同步（防止把成交误判为撤销）`);
      return;
    }

    for (const level of levels) {
      try {
        if (level.state === GridLevelState.IDLE) {
          // 空闲 -> 挂开仓单（被动同步模式不新增敞口）
          if (allowOpen) await this.placeOpenOrder(symbol, level);
        } else if (level.state === GridLevelState.OPEN_PENDING) {
          // 检查开仓单是否还在挂单列表中
          if (!exchangeOids.has(level.openOrderId)) {
            // 不在挂单列表 -> 已成交或被撤
            if (this.confirmFill(level, "open", cachedFills)) {
              level.state = GridLevelState.OPEN_FILLED;
              this.logger.printInfo(`   [Grid] ${level.id} 开仓成交 @ ${level.openFillPrice}`);
              this.logger.logTrade({
                symbol,
                action: `GRID_OPEN_${level.side === "LONG" ? "BUY" : "SELL"}`,
                amount: level.openFillAmount?.toNumber() ?? 0,
                price: level.openFillPrice?.toNumber() ?? 0,
                orderId: String(level.openOrderId ?? ""),
                status: "FILLED",
              });
              // 同轮立即挂平仓单：等下一轮意味着持仓在整个调度间隔里没有任何
              // 退出单保护，而这段时间恰好是刚成交、价格正在动的时候。
              // 挂平仓单是 reduce_only 减仓，被动同步模式同样必须做。
              await this.placeCloseOrder(symbol, level);
            } else {
              // 被撤销/失败 -> 回到 IDLE 重新挂
              level.state = GridLevelState.IDLE;
            }
          }
        } else if (level.state === GridLevelState.OPEN_FILLED) {
          // 开仓已成交 -> 挂平仓单
          await this.placeCloseOrder(symbol, level);
        } else if (level.state === GridLevelState.CLOSE_PENDING) {
          if (!exchangeOids.has(level.closeOrderId)) {
            if (this.confirmFill(level, "close", cachedFills)) {
              level.state = GridLevelState.COMPLETED;
              this.logger.printInfo(`   [Grid] ${level.id} 平仓成交 @ ${level.closeFillPrice}`);
              this.logger.logTrade({
                symbol,
                action: `GRID_CLOSE_${level.side === "SHORT" ? "BUY" : "SELL"}`,
                amount: level.closeFillAmount?.toNumber() ?? 0,
                price: level.closeFillPrice?.toNumber() ?? 0,
                orderId: String(level.closeOrderId ?? ""),
                status: "FILLED",
              });
              // 同轮完成 PnL 归因并复位：COMPLETED 只是过渡态，让它跨轮存活
              // 既拖慢层级复用（每完成一轮白等一个周期才重新挂单），也让重建
              // 判定多一类要处理的在途状态。下方 COMPLETED 分支保留，用于
              // 兜底从状态文件恢复出来的历史 COMPLETED 层级。
              this.recordRoundTrip(symbol, level);
              level.reset();
            } else {
              // 平仓单被撤 -> 回到 OPEN_FILLED 重挂
              level.state = GridLevelState.OPEN_FILLED;
            }
          }
        } else if (level.state === GridLevelState.COMPLETED) {
          // 完成一轮 -> 记录 PnL -> 重置（reset 后变为 IDLE，下一轮 sync 会重新挂单）
          this.recordRoundTrip(symbol, level);
          level.reset();
        }
      } catch (e) {
        this.logger.printError(`   [Grid] ${level.id} 同步异常: ${e}`);
      }
    }

    this.saveIncrementalState(symbol);
  }

  /**
   * 只为 IDLE 层级补挂开仓单，不重跑整套状态机。
   *
   * 入口的被动同步（allowOpen=false）已确认成交、挂平仓单、结算 round-trip，
   * 确认「本轮不重建」后再补这一步即可，省掉一次挂单查询与成交记录查询。
   */
  private async placeIdleOpenOrders(symbol: string): Promise<void> {
    for (const level of this.gridLevels[symbol] ?? []) {
      if (level.state !== GridLevelState.IDLE) continue;
      try {
        await this.placeOpenOrder(symbol, level);
      } catch (e) {
        this.logger.printError(`   [Grid] ${level.id} 补挂开仓单异常: ${e}`);
      }
    }
  }

  /** 为层级挂开仓单。 */
  private async placeOpenOrder(symbol: string, level: GridLevel): Promise<void> {
    const currentPrice = await this.orderManager.client.getCurrentPrice(symbol);
    if (!currentPrice || currentPrice <= 0) return;

    const isBuy = level.side === "LONG";
    const price = level.price.toNumber();

    // 只在价格合理时挂单（买单低于市价，卖单高于市价）
    if (isBuy && price >= currentPrice) return;
    if (!isBuy && price <= currentPrice) return;

    // 趋势侧单边挂单：逆势侧不再补开仓单（层级留在 IDLE，趋势解除后自然恢复）
    if (!this.sideAllowed(symbol, isBuy)) {
      this.logger.printInfo(`   [Grid] 🧭 ${level.id} 开仓单暂不挂出（本轮：${this.sideLabel(symbol)}）`);
      return;
    }

    // 库存上限：净持仓达上限后不再往同方向加仓（防单边趋势逆势累积，本次最大亏损根因）。
    // 这是主要执行点——增量同步每轮在此重新挂开仓单，超限方向被持续拦截，库存自然收敛。
    if (await this.wouldExceedInventoryCap(symbol, isBuy)) {
      this.logger.printWarning(
        `   [Grid] 🚧 ${level.id} 库存达上限，跳过${isBuy ? "买" : "卖"}开仓单（防逆势累积）`,
      );
      return;
    }

    // 从 state 中获取 tp/sl 配置
    const gridData: Dict = (this.state.active_grids as Dict)?.[symbol] ?? {};
    const config: Dict = gridData.config ?? {};
    const params: Dict = config.parameters ?? config;
    const tpRatio = params.tp_ratio;
    const slRatio = params.sl_ratio;

    // 库存守卫：不把开仓单挂进库存的亏损区（否则每次成交都是按亏损净额平库存，
    // 层级复位后下一周期原价重挂，无限循环）
    const guard = await this.inventoryGuardPrices(symbol, this.safeFloat(tpRatio, 0));
    if (this.violatesInventoryGuard(guard, isBuy, price)) {
      this.logger.printInfo(
        `   [Grid] 🛡️ ${level.id} ${isBuy ? "买" : "卖"}开仓单 @ $${price} 落在库存亏损区（均价 $${guard.entryPx.toFixed(2)}），跳过`,
      );
      return;
    }

    const exec = isBuy
      ? this.orderManager.executeLongLimit.bind(this.orderManager)
      : this.orderManager.executeShortLimit.bind(this.orderManager);
    const res = await exec(symbol, level.amount.toNumber(), price, {
      tpRatio, slRatio,
      withTakeProfit: this.gridLimitOrderTakeProfitEnabled,
      withStopLoss: this.gridLimitOrderStopLossEnabled,
      registerTpslMonitor: this.gridLimitOrderTakeProfitEnabled || this.gridLimitOrderStopLossEnabled,
      amountIsNotional: true,
      tif: this.postOnly ? "Alo" : "Gtc",
    });

    if (res?.post_only_rejected) {
      // 价格已越过该层：留在 IDLE，下一周期价格回到合理侧再挂
      this.logger.printInfo(`   [Grid] ${level.id} 开仓价 $${price} 已被越过（post-only 拒绝），本轮不追价`);
      return;
    }
    if (res?.success) {
      const oid = this.extractOid(res.limit_order ?? {});
      if (oid) {
        level.openOrderId = oid;
        level.state = GridLevelState.OPEN_PENDING;
        this.logger.printInfo(`   [Grid] ${level.id} 挂开仓单 ${isBuy ? "买" : "卖"} @ $${price}`);
        this.logger.logTrade({
          symbol,
          action: `GRID_${isBuy ? "BUY" : "SELL"}`,
          amount: level.amount.toNumber(),
          price,
          orderId: String(oid),
          status: "PLACED",
        });
      }
    }
  }

  /**
   * 确认订单是否已成交（非被撤销）。
   *
   * 通过交易所成交历史 (user_fills) 判断，使用外部传入的 fills 缓存。
   * 同一订单可能分多笔部分成交（共享 oid）：只取首笔会把成交量低记，剩余库存
   * 脱离层级簿记。聚合全部匹配成交：量求和、价按量加权。
   */
  private confirmFill(level: GridLevel, orderType: "open" | "close", fills: Dict[]): boolean {
    const orderId = orderType === "open" ? level.openOrderId : level.closeOrderId;
    const matched = fills.filter((f) => f?.oid === orderId);
    if (!matched.length) return false;

    const totalAmount = matched.reduce<Decimal>((sum, f) => sum.plus(toDecimal(f.sz ?? "0")), new Decimal(0));
    let price: Decimal;
    if (totalAmount.gt(0)) {
      const notional = matched.reduce<Decimal>(
        (sum, f) => sum.plus(toDecimal(f.px ?? "0").mul(toDecimal(f.sz ?? "0"))),
        new Decimal(0),
      );
      price = notional.div(totalAmount);
    } else {
      price = toDecimal(matched[0].px ?? "0");
    }
    const timestamp = Math.max(...matched.map((f) => this.safeFloat(f.time, nowSecs())));

    if (orderType === "open") {
      level.openFillPrice = price;
      level.openFillAmount = totalAmount;
      level.openFillTime = timestamp;
    } else {
      level.closeFillPrice = price;
      level.closeFillAmount = totalAmount;
      level.closeFillTime = timestamp;
    }
    return true;
  }

  /** 根据开仓实际成交价计算平仓价格并挂平仓单。 */
  private async placeCloseOrder(symbol: string, level: GridLevel): Promise<void> {
    if (level.openFillPrice === null || level.openFillAmount === null) {
      this.logger.printWarning(`   [Grid] ${level.id} 缺少开仓成交数据，无法挂平仓单`);
      return;
    }

    // 从 state 获取 tp_ratio
    const gridData: Dict = (this.state.active_grids as Dict)?.[symbol] ?? {};
    const config: Dict = gridData.config ?? {};
    const params: Dict = config.parameters ?? config;
    const tpRatio = toDecimal(params.tp_ratio ?? "0.005");

    let closePrice: Decimal;
    let isBuy: boolean;
    if (level.side === "LONG") {
      // 做多平仓 = 卖出，价格 = 开仓价 x (1 + tp_ratio)
      closePrice = level.openFillPrice.mul(new Decimal(1).plus(tpRatio));
      isBuy = false;
    } else {
      // 做空平仓 = 买入，价格 = 开仓价 x (1 - tp_ratio)
      closePrice = level.openFillPrice.mul(new Decimal(1).minus(tpRatio));
      isBuy = true;
    }

    const formattedPrice = await this.orderManager.client.formatPrice(symbol, closePrice.toNumber());
    const result = await this.placeReduceOnlyLimit(symbol, isBuy, level.openFillAmount.toNumber(), formattedPrice);

    // 校验内层 statuses：HL 拒单时外层仍为 status=ok，错误藏在 statuses[].error，
    // 仅判外层会把被拒平仓单误记为 PLACED/CLOSE_PENDING，导致持仓失去对冲裸奔
    const [orderOk, orderErr] = HyperliquidClient.checkOrderSuccess(result);
    if (orderOk) {
      const oid = this.extractOid(result);
      if (oid) {
        level.closeOrderId = oid;
        level.state = GridLevelState.CLOSE_PENDING;
        this.logger.printInfo(
          `   [Grid] ${level.id} 挂平仓单 ${isBuy ? "买" : "卖"} @ $${formattedPrice} (reduce_only)`,
        );
        this.logger.logTrade({
          symbol,
          action: `GRID_CLOSE_${isBuy ? "BUY" : "SELL"}`,
          amount: level.openFillAmount.toNumber(),
          price: formattedPrice,
          orderId: String(oid),
          status: "PLACED",
        });
      }
    } else if (await this.isCloseRejectedAsNetted(symbol, level, orderErr)) {
      // 「reduce_only 会加仓」+ 实查持仓确认该方向敞口已消失：本层库存已被
      // 对侧格子的普通开仓单净额对冲平掉（Hyperliquid 单向持仓），盈亏由
      // 净额归因补记，层级直接收尾复用。不收尾则每轮重挂重拒永不收敛
      // （线上 32 小时实测 136 次拒单），且幻影库存持续污染未实现盈亏口径。
      this.logger.printInfo(
        `   [Grid] ${level.id} 库存已被净额对冲（平仓单被拒且无对应持仓），层级收尾复用（盈亏由净额归因补记）`,
      );
      level.reset();
    } else {
      this.logger.printWarning(`   [Grid] ${level.id} 平仓单失败/被拒 @ $${formattedPrice}: ${orderErr}`);
    }
  }

  /**
   * reduce_only 限价平仓单，post-only 优先：目标价已被市价越过（价格已经跑过
   * 止盈位）时，退到盘口外一个 tick 再试一次 Alo；仍被拒才按原目标价 Gtc 提交
   * ——此时成交价只会比目标更好，付一次 taker 费换确定退出。
   */
  private async placeReduceOnlyLimit(symbol: string, isBuy: boolean, size: number, price: number): Promise<Dict> {
    const client = this.orderManager.client;
    if (!this.postOnly) return client.placeLimitOrder(symbol, isBuy, size, price, true, "Gtc");
    let result = await client.placeLimitOrder(symbol, isBuy, size, price, true, "Alo");
    let [ok, err] = HyperliquidClient.checkOrderSuccess(result);
    if (ok || !HyperliquidClient.isPostOnlyRejection(err)) return result;
    const mid = await client.getCurrentPrice(symbol);
    if (mid && mid > 0) {
      const tick = await this.priceTick(symbol, mid);
      const repriced = await client.formatPrice(symbol, isBuy ? mid - tick : mid + tick);
      this.logger.printInfo(`   [Grid] ↩️ 平仓单目标价 $${price} 已被越过，改挂盘口外 $${repriced}（post-only）`);
      result = await client.placeLimitOrder(symbol, isBuy, size, repriced, true, "Alo");
      [ok, err] = HyperliquidClient.checkOrderSuccess(result);
      if (ok || !HyperliquidClient.isPostOnlyRejection(err)) return result;
    }
    this.logger.printWarning("   [Grid] ⚠️ post-only 平仓单两次被拒，按原目标价 Gtc 提交（可能以 taker 成交）");
    return client.placeLimitOrder(symbol, isBuy, size, price, true, "Gtc");
  }

  /** 价格最小步长：「5 位有效数字」与「最多 6−szDecimals 位小数」两条规则的较大者。 */
  private async priceTick(symbol: string, price: number): Promise<number> {
    const bySignificant = 10 ** (Math.floor(Math.log10(Math.max(price, 1e-9))) - 4);
    let szDecimals = 3;
    try {
      const asset = await this.orderManager.client.getAssetInfo(symbol);
      const parsed = Number(asset?.szDecimals);
      if (Number.isFinite(parsed)) szDecimals = parsed;
    } catch {
      /* 用默认精度 */
    }
    const byDecimals = 10 ** -Math.max(0, 6 - szDecimals);
    return Math.max(bySignificant, byDecimals);
  }

  /**
   * 判断平仓单被拒是否因为本层库存已被净额对冲。
   *
   * 中性网格在净头寸交易所上，对侧格子的普通开仓单成交会把本层库存净额平掉
   * （链上成交 dir=Close，盈亏走 reconcileNettingCloses 补记），但层级状态机
   * 对此无感：本层的 reduce_only 平仓单失去持仓支撑，交易所以「Reduce only
   * order would increase position」拒单，层级停在 OPEN_FILLED。
   *
   * 双重确认防误判：①拒单文案确为 reduce_only 加仓语义；②实查净持仓，
   * 本层方向的敞口确已不存在（LONG 层挂卖出减仓单需要多头持仓）。其他拒因
   * （保证金、价格带等）与持仓查询失败（null）一律不收尾，保留层级下轮重试。
   */
  private async isCloseRejectedAsNetted(symbol: string, level: GridLevel, orderErr: string | null): Promise<boolean> {
    const err = (orderErr ?? "").toLowerCase();
    if (!err.includes("reduce only") || !err.includes("increase")) return false;
    const positionSize = await this.getSymbolPositionSize(symbol);
    if (positionSize === null) return false;
    if (level.side === "LONG") return positionSize <= 0;
    return positionSize >= 0;
  }

  /**
   * 网格是否真的「空转」：无层级、无持仓，且交易所上也没有活跃网格挂单。
   *
   * 这是 LLM 故障期间的终局状态——层级被紧急平仓/熔断清空后，只有 UPDATE_GRID
   * 能重建，而故障期每轮只产出 ERROR 或兜底 KEEP_GRID，于是永远停在空转。
   * 调用方据此计数并触发兜底重建。
   *
   * **必须查交易所挂单**：本地 gridLevels 为空不等于网格没在工作——全量重建
   * 路径只把订单快照写进状态文件，levels 要等下一轮增量同步才建立。线上实测
   * 过这个窗口：交易所挂着完整的 12 个网格单，本地层级却是空的，只看层级会
   * 判成空转，进而触发一次没必要的撤单重布。
   *
   * reduce_only 单不算数：那是减仓保护单，持仓清零后仍可能残留，不代表网格在做市。
   */
  async isGridIdle(symbol: string): Promise<boolean> {
    if (this.gridLevels[symbol]?.length) return false;
    try {
      const positionSize = await this.getSymbolPositionSize(symbol);
      if (positionSize === null || Math.abs(positionSize) > 0) return false;
      const openOrders = await this.getSymbolOpenOrders(symbol);
      if (openOrders === null) return false;
      const liveOrders = openOrders.filter((o) => !o?.reduceOnly);
      return liveOrders.length === 0;
    } catch {
      // 查不到持仓/挂单时保守认为「不空转」，不触发兜底重建（宁可不动也不误建网格）
      return false;
    }
  }

  /**
   * 登记一笔风控强平的订单号，供净额归因还原 forced 语义。
   *
   * 净额归因以链上成交为准，本身无从分辨「网格正常止盈」与「风控强平」，而
   * consecutive_loss 的 forced_close_no_reset 恰恰依赖这个区分（强平的净盈利
   * 不得重置连亏计数）。taker/maker 不是可靠判据——网格限价单穿价成交同样是
   * taker（线上三天 50 笔 taker 成交里只有 3 笔是强平），故在强平下单处直接
   * 登记 oid，归因时按 oid 精确匹配。
   */
  private markForcedCloseOid(symbol: string, result: unknown): void {
    if (!this.nettingAttributionEnabled) return;
    let oid: number | null = null;
    try {
      oid = this.extractOid(result as Dict);
    } catch {
      oid = null;
    }
    if (!oid) return;
    const bucket: Dict = ((this.state.netting_attribution ??= {}) as Dict)[symbol] ??= {};
    const forced = [...(bucket.forced_oids ?? [])].map(String);
    forced.push(String(oid));
    // 只保留最近若干个：强平成交通常在下一轮就被归因消费掉，留存过多只会让
    // 状态文件无谓膨胀
    bucket.forced_oids = forced.slice(-MAX_FORCED_OIDS);
    this.saveState();
  }

  /**
   * 以链上成交为准，回补被层级状态机漏掉的平仓盈亏归因。
   *
   * Hyperliquid 是单向持仓：中性网格的库存大多被对侧格子的普通开仓单净额对冲
   * 平掉（成交 dir 为 "Close Long"/"Close Short"），根本走不到层级的
   * CLOSE_PENDING→COMPLETED 路径，也就不会触发 recordRoundTrip。线上三周实测
   * 1051 笔带盈亏的平仓腿只有 24 笔（2.3%）进了归因，连亏熔断的状态文件三周
   * 纹丝不动——该保护在中性网格下等同失效，trades 日志的 pnl 也恒为 null。
   *
   * 开启后本方法独占风控上报（见 reportRoundTripClose），层级状态机与紧急平仓
   * 只负责写日志，避免同一笔平仓被计两次。
   */
  async reconcileNettingCloses(symbol: string, fills?: Dict[] | null): Promise<Dict> {
    if (!this.nettingAttributionEnabled) return { processed: 0, pnl: 0, skipped: "disabled" };

    if (fills == null) {
      try {
        fills = await this.orderManager.client.userFills();
      } catch (e) {
        this.logger.printWarning(`   [Grid] 净额归因取成交记录失败: ${e}`);
        return { processed: 0, pnl: 0, skipped: "fetch_failed" };
      }
    }

    const bucket: Dict = ((this.state.netting_attribution ??= {}) as Dict)[symbol] ??= {};
    const cursorMs = Math.trunc(this.safeFloat(bucket.cursor_ms, 0));
    const symbolFills = (fills ?? []).filter((f) => f?.coin === symbol);

    // 首次启用：把游标置于当前最新成交，不回溯历史。否则历史上几百笔亏损腿会
    // 在一个周期内全部灌进连亏熔断，瞬间把交易对误锁死。
    if (cursorMs <= 0) {
      const newest = symbolFills.reduce((max, f) => Math.max(max, Math.trunc(this.safeFloat(f.time, 0))), 0);
      if (newest <= 0) {
        // 该交易对还没有任何成交，游标无从锚定：等有成交的那一轮再落盘
        return { processed: 0, pnl: 0, skipped: "no_fills" };
      }
      bucket.cursor_ms = newest;
      bucket.seen_tids = symbolFills
        .filter((f) => Math.trunc(this.safeFloat(f.time, 0)) === newest)
        .map((f) => String(f.tid));
      this.saveState();
      this.logger.printInfo(`   [Grid] 净额归因首次启用，游标置于 ${newest}（不回溯历史成交）`);
      return { processed: 0, pnl: 0, skipped: "primed" };
    }

    const seenTids = new Set([...(bucket.seen_tids ?? [])].map(String));
    // 风控强平登记的订单号：命中即以 forced=true 上报，保住 forced_close_no_reset 语义
    const forcedOids = new Set([...(bucket.forced_oids ?? [])].map(String));
    const consumedForced = new Set<string>();
    let processed = 0;
    let totalPnl = 0;
    let maxTs = cursorMs;
    // 下轮去重只需记住「游标那一毫秒」的 tid：更早的成交靠 ts < cursorMs 直接跳过。
    let newestTids = new Set<string>();

    const sorted = [...symbolFills].sort((a, b) => this.safeFloat(a.time, 0) - this.safeFloat(b.time, 0));
    for (const fill of sorted) {
      const ts = Math.trunc(this.safeFloat(fill.time, 0));
      if (ts < cursorMs) continue;
      const tid = String(fill.tid);
      if (ts === cursorMs && seenTids.has(tid)) continue;

      if (ts > maxTs) {
        maxTs = ts;
        newestTids = new Set([tid]);
      } else if (ts === maxTs) {
        newestTids.add(tid);
      }

      const gross = this.safeFloat(fill.closedPnl, 0);
      if (gross === 0) continue;
      // 扣掉这笔成交自身的手续费，与 GridPnLTracker.recordRoundTrip 的净额口径
      // 保持一致——网格的小额止盈常被手续费吃穿，连亏熔断必须看净额才有意义。
      const net = gross - this.safeFloat(fill.fee, 0);
      processed += 1;
      totalPnl += net;

      const oid = String(fill.oid ?? "");
      const isForced = forcedOids.has(oid);
      if (isForced) consumedForced.add(oid);

      // 落盘失败不得中断循环：异常逃逸会让游标停在本批之前，下一轮把这批
      // 已上报过的盈亏再喂一遍风控，凭空制造连亏。宁可丢一条归因日志。
      try {
        this.logger.logTrade({
          symbol,
          action: "GRID_NET_CLOSE",
          amount: this.safeFloat(fill.sz, 0),
          price: this.safeFloat(fill.px, 0),
          orderId: oid,
          status: "FILLED",
          pnl: net,
          reason: `${isForced ? "GRID_FORCED" : "GRID_NETTING"}:${fill.dir ?? ""}`.replace(/:$/, ""),
          fee: this.safeFloat(fill.fee, 0),
          crossed: fill.crossed === undefined ? null : !!fill.crossed,
        });
      } catch (e) {
        this.logger.printWarning(`   [Grid] 净额归因写交易日志失败(tid=${tid}): ${e}`);
      }
      this.dispatchRoundTripClose(symbol, net, isForced);
    }

    if (maxTs > cursorMs || newestTids.size || consumedForced.size) {
      // 游标停在同一毫秒时，旧 tid 仍需保留，否则那一毫秒的成交下轮会被重复归因
      if (maxTs === cursorMs) for (const t of seenTids) newestTids.add(t);
      bucket.cursor_ms = maxTs;
      bucket.seen_tids = [...newestTids].sort();
      if (consumedForced.size) {
        bucket.forced_oids = [...forcedOids].filter((o) => !consumedForced.has(o)).sort();
      }
      this.saveState();
    }

    if (processed) {
      this.logger.printInfo(
        `   [Grid] 净额归因: 补记 ${processed} 笔平仓，净盈亏 ${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(4)}`,
      );
    }
    return { processed, pnl: totalPnl, skipped: null };
  }

  /**
   * 把逐轮盈亏上报给账户级风控（连亏熔断），统一处理异常。
   *
   * forced=true 表示风控强制平仓（紧急平仓/手术式减仓），连亏熔断据此区分
   * 「主动止盈」与「被动强平」语义。失败不得影响网格主流程——风控记账出错
   * 绝不能拖垮布单/同步，故吞掉异常仅记日志。
   */
  private reportRoundTripClose(symbol: string, pnl: number, forced = false): void {
    // 净额归因接管时，风控上报统一以链上成交为准（reconcileNettingCloses），
    // 此处必须让路：同一笔平仓若既走层级状态机又走链上 fill，连亏计数会翻倍。
    if (this.nettingAttributionEnabled) return;
    this.dispatchRoundTripClose(symbol, pnl, forced);
  }

  /** 实际派发 round-trip 回调（两条归因通路共用），吞异常仅记日志。 */
  private dispatchRoundTripClose(symbol: string, pnl: number, forced = false): void {
    const callback = this.onRoundTripClose;
    if (!callback) return;
    try {
      callback(symbol, pnl, forced);
    } catch (e) {
      this.logger.printWarning(`   [Grid] round-trip 盈亏上报风控失败: ${e}`);
    }
  }

  /** 新建 PnL 追踪器：maker 费率取账户实际分档（历史写死 0.035%，是过期数字的两倍多）。 */
  private newPnlTracker(): GridPnLTracker {
    const tracker = new GridPnLTracker();
    const maker = this.getFeeRates().makerRate;
    if (Number.isFinite(maker) && maker >= 0) tracker.makerFeeRate = toDecimal(maker);
    return tracker;
  }

  /** 在层级完成一轮开平仓时调用，记录 PnL。 */
  private recordRoundTrip(symbol: string, level: GridLevel): void {
    let tracker = this.pnlTrackers[symbol];
    if (!tracker) {
      tracker = this.newPnlTracker();
      this.pnlTrackers[symbol] = tracker;
    }
    const pnl = tracker.recordRoundTrip(level);
    const pnlNum = pnl.toNumber();
    this.logger.printInfo(
      `   [Grid] ${level.id} 完成第 ${level.roundTripCount} 轮 | ` +
        `PnL: ${pnlNum >= 0 ? "+" : ""}${pnlNum.toFixed(4)} | ` +
        `累计: ${level.cumulativePnl.toNumber() >= 0 ? "+" : ""}${level.cumulativePnl.toNumber().toFixed(4)}`,
    );
    // 每轮往返的已实现盈亏落盘（trades jsonl 的 pnl 字段历史上恒为 null，
    // 12.5 天亏 39% 无从归因即源于此）——归因标签 GRID_TP=主动止盈往返。
    this.logger.logTrade({
      symbol,
      action: "GRID_ROUND_TRIP",
      amount: level.openFillAmount?.toNumber() ?? 0,
      price: level.closeFillPrice?.toNumber() ?? 0,
      orderId: String(level.closeOrderId ?? ""),
      status: "FILLED",
      pnl: pnlNum,
      reason: "GRID_TP",
    });
    this.reportRoundTripClose(symbol, pnlNum, false);
  }

  /**
   * 暂停期维护：被动同步层级 + 补齐 reduce_only 减仓保护单（不新增敞口）。
   *
   * 账户级 PAUSE_NEW_TRADES 暂停的是「新开仓」，持仓的风控维护（成交确认、
   * 层级平仓单、减仓保护单）绝不能一起暂停——历史缺陷：暂停分支直接 return，
   * 暂停期间持仓亏损不封底。
   */
  async maintainProtectiveOrders(symbol: string): Promise<void> {
    if (this.gridLevels[symbol]?.length) {
      await this.syncGridIncremental(symbol, false);
    }
    if (this.gridReduceOnlyExitOrdersEnabled) {
      await this.ensureMinOrders(symbol);
    }
  }

  /** 保存增量同步后的层级状态和 PnL 数据。 */
  private saveIncrementalState(symbol: string): void {
    const gridData: Dict = (this.state.active_grids as Dict)[symbol] ?? {};
    const levels = this.gridLevels[symbol] ?? [];
    const tracker = this.pnlTrackers[symbol];
    gridData.levels = levels.map((level) => level.toDict());
    if (tracker) gridData.pnl = tracker.toDict();
    gridData.last_sync = nowSecs();
    (this.state.active_grids as Dict)[symbol] = gridData;
    this.saveState();
  }

  /** 看板展示用：网格完整状态快照（层级/PnL/冷却/屏障/待重试）。 */
  async inspect(symbol: string): Promise<Dict> {
    const levels = this.gridLevels[symbol] ?? [];
    const tracker = this.pnlTrackers[symbol];
    const grid: Dict = (this.state.active_grids as Dict)?.[symbol] ?? {};
    const currentPrice = await this.orderManager.client.getCurrentPrice(symbol);
    let pnl: Dict | null = null;
    if (tracker && levels.length && currentPrice && currentPrice > 0) {
      const totalInvestment = levels.reduce<Decimal>((sum, l) => sum.plus(l.amount), new Decimal(0));
      const summary = tracker.getSummary(levels, toDecimal(currentPrice), totalInvestment) as Dict;
      pnl = Object.fromEntries(
        Object.entries(summary).map(([k, v]) => [k, v instanceof Decimal ? v.toNumber() : v]),
      );
    }
    const lastRebuild = this.lastRebuildTs[symbol] ?? 0;
    const cooldownRemaining = this.gridRebuildCooldownSeconds > 0 && lastRebuild > 0
      ? Math.max(0, this.gridRebuildCooldownSeconds - (nowSecs() - lastRebuild))
      : 0;
    return {
      symbol,
      current_price: currentPrice,
      config: grid.config ?? null,
      buy_orders: grid.buy_orders ?? [],
      sell_orders: grid.sell_orders ?? [],
      levels: levels.map((l) => l.toDict()),
      pnl,
      barrier: this.barrierConfig.toDict(),
      last_rebuild_ts: lastRebuild,
      rebuild_cooldown_seconds: this.gridRebuildCooldownSeconds,
      rebuild_cooldown_remaining: Math.round(cooldownRemaining),
      pending_emergency_close: (this.state.pending_emergency_close as Dict) ?? {},
      summary: await this.getGridSummary(symbol),
    };
  }
}

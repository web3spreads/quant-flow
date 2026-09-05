/**
 * 网格决策 Agent：AI 判断方向与宽度，数学引擎计算参数。
 *
 * 分工边界：LLM 只回答「是否需要更新网格形态、往哪个方向、多宽」，
 * 布单数量与价位由 calculateGridConfig（纯数学）推导——AI 的模糊判断
 * 永远不直接决定资金敞口。
 *
 * 故障语义（llm_ok 标记）：LLM 调用异常 / 输出不可解析 / action 非法这些
 * 兜底路径返回与 AI 真实 KEEP_GRID 同形的决策，但打 llm_ok=false 标，
 * 供上层做连续故障告警与空转自愈（历史教训：模型下线后连续 13 小时决策
 * 失败却无任何告警）。余额接口故障标 llm_ok=true——那不是 LLM 的问题。
 */

import { LLMClient, LLMError, extractJson } from "../llm.js";
import { calculateGridConfig } from "../utils/gridMath.js";
import { defaultPerpFeeRates, type FeeRates } from "../fees.js";
import type { OrderManager } from "../trading/orderManager.js";
import type { TradingLogger } from "../logger.js";
import type { Dict } from "../trading/client.js";

const DEFAULT_WIDTH_PCT_MIN = 0.02;
const DEFAULT_WIDTH_PCT_MAX = 0.15;
const DEFAULT_WIDTH_PCT_FALLBACK = 0.05;
const DEFAULT_AI_WIDTH_BLEND_WEIGHT = 0.35;
// LLM 未给出格数时的默认层数（正常决策与兜底建网格共用，保证两条路径形态一致）
const DEFAULT_AGENT_GRID_NUM = 6;

// 合法的网格动作白名单。LLM 输出此集合之外的值（如线上出现过的 UPDATE_GRIDLE）
// 一律回退到 KEEP_GRID 保守处理，绝不透传到下游执行。
const VALID_GRID_ACTIONS = new Set(["UPDATE_GRID", "KEEP_GRID"]);
const VALID_GRID_MODES = new Set(["LONG", "SHORT", "NEUTRAL"]);

function safeFloat(value: unknown, defaultValue = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

function clamp(value: number, lower: number, upper: number): number {
  return Math.max(lower, Math.min(value, upper));
}

/** 网格 AI 决策引擎（单交易对）。 */
export class GridAgent {
  readonly symbol: string;
  private readonly orderManager: OrderManager;
  private readonly logger: TradingLogger;
  private readonly llm: LLMClient;
  private readonly tradeAmount: number;
  private readonly widthPctMin: number;
  private readonly widthPctMax: number;
  private readonly widthPctFallback: number;
  private readonly aiWidthBlendWeight: number;
  readonly forceNeutralMode: boolean;
  private readonly maxLeverage: number;
  private readonly adaptiveSizing: boolean;
  private readonly minGridNum: number;
  private readonly maxGridNum: number;
  private readonly capitalRatio: number;
  private readonly temperature: number;
  private readonly getFeeRates: () => FeeRates;
  private readonly inventorySkew: number;
  private readonly getInventoryCapUsd: () => number;

  constructor(options: {
    symbol: string;
    orderManager: OrderManager;
    logger: TradingLogger;
    llm: LLMClient;
    tradeAmount: number;
    widthPctMin?: number;
    widthPctMax?: number;
    widthPctFallback?: number;
    aiWidthBlendWeight?: number;
    forceNeutralMode?: boolean;
    maxLeverage?: number;
    adaptiveSizing?: boolean;
    minGridNum?: number;
    maxGridNum?: number;
    /** 网格保证金预算占权益比例（0=改以 tradeAmount 为上限） */
    capitalRatio?: number;
    /** LLM 采样温度（默认 0：可回放） */
    temperature?: number;
    getFeeRates?: () => FeeRates;
    /** 库存倾斜强度（0=关） */
    inventorySkew?: number;
    /** 生效的库存上限（USD 名义额）；0=未启用，倾斜随之失效 */
    getInventoryCapUsd?: () => number;
  }) {
    this.symbol = options.symbol;
    this.orderManager = options.orderManager;
    this.logger = options.logger;
    this.llm = options.llm;
    this.tradeAmount = options.tradeAmount;
    this.widthPctMin = options.widthPctMin ?? DEFAULT_WIDTH_PCT_MIN;
    this.widthPctMax = options.widthPctMax ?? DEFAULT_WIDTH_PCT_MAX;
    this.widthPctFallback = options.widthPctFallback ?? DEFAULT_WIDTH_PCT_FALLBACK;
    this.aiWidthBlendWeight = clamp(options.aiWidthBlendWeight ?? DEFAULT_AI_WIDTH_BLEND_WEIGHT, 0, 1);
    // 强制中性：忽略 AI 的 LONG/SHORT 方向，网格只做对称做市（默认开）
    this.forceNeutralMode = options.forceNeutralMode ?? true;
    this.maxLeverage = Math.max(1, Math.trunc(options.maxLeverage ?? 10));
    // 自适应仓位：单格金额与净值挂钩（钱不够减格数而非抬单格金额）
    this.adaptiveSizing = options.adaptiveSizing ?? true;
    this.minGridNum = Math.max(2, Math.trunc(options.minGridNum ?? 3));
    this.maxGridNum = Math.max(this.minGridNum, Math.trunc(options.maxGridNum ?? 10));
    this.capitalRatio = clamp(options.capitalRatio ?? 0.5, 0, 1);
    this.temperature = Math.max(0, options.temperature ?? 0);
    this.getFeeRates = options.getFeeRates ?? (() => defaultPerpFeeRates());
    this.inventorySkew = clamp(options.inventorySkew ?? 0, 0, 1);
    this.getInventoryCapUsd = options.getInventoryCapUsd ?? (() => 0);
  }

  /**
   * 库存倾斜位移：把整张网格逆着库存挪。
   *
   * 持多（q>0）时中心下移 → 买单更远离市价（不急于加仓）、卖单更贴近市价
   * （更快减仓）；持空反之。位移 = 强度 × (库存名义额 / 库存上限) × 半宽，
   * 因此贴近上限时位移最大、空仓时为 0，且永远不超过半个网格宽度——
   * 越界会让网格整体跑到市价一侧，退化成单边追价。
   *
   * 与库存守卫互补：守卫是硬边界（绝不在亏损区开仓），倾斜是软引导（越接近
   * 上限越不愿加仓）。取数失败返回 0（不倾斜），保持对称的已知行为。
   */
  private async inventoryCenterShift(widthPct: number): Promise<number> {
    if (this.inventorySkew <= 0) return 0;
    const cap = this.getInventoryCapUsd();
    if (!(cap > 0)) return 0;
    let positions: Dict[] | null;
    try {
      positions = await this.orderManager.getCurrentPositions();
    } catch {
      return 0;
    }
    if (positions === null) return 0;
    const pos = positions.find((p) => p?.coin === this.symbol);
    const szi = safeFloat(pos?.szi, 0);
    if (szi === 0) return 0;
    const notional = Math.abs(safeFloat(pos?.positionValue, 0));
    if (!(notional > 0)) return 0;
    const ratio = clamp((notional / cap) * Math.sign(szi), -1, 1);
    return -this.inventorySkew * ratio * (widthPct / 2);
  }

  /**
   * 把 LLM 给出的格数钳到 [min_grid_num, max_grid_num]。
   *
   * 格子越密，单格止盈 = 宽度/格数 × 0.8 越薄，越难覆盖双边手续费与库存的
   * 持有成本；回测中 16 格在三个样本上一致差于 8 格。非法值回退到默认格数。
   */
  private clampGridNum(raw: unknown): number {
    const parsed = Math.trunc(safeFloat(raw, DEFAULT_AGENT_GRID_NUM));
    const value = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_AGENT_GRID_NUM;
    return clamp(value, this.minGridNum, this.maxGridNum);
  }

  /**
   * 网格保证金预算：权益 × capital_ratio 与可用余额取小。
   *
   * 历史用 min(available, trading.max_trade_amount)——把永续的单笔上限复用成
   * 网格总投入，与权益无关：$1050 的账户只动用 $200 名义额（杠杆 0.2x）。
   * capital_ratio=0 时改用固定金额（回测对照用）。
   */
  private gridBudget(available: number, equity: number): number {
    if (this.capitalRatio > 0 && equity > 0) {
      return Math.max(0, Math.min(available, equity * this.capitalRatio));
    }
    return Math.max(0, Math.min(available, this.tradeAmount));
  }

  // ── 决策 ──────────────────────────────────────────────────────────────

  /** 产出一份网格决策（本方法不抛出异常，故障归一为保守兜底）。 */
  async makeDecision(
    marketData: Dict,
    multiTimeframeTrends: Record<string, string>,
    currentGridSummary: string,
  ): Promise<Dict> {
    try {
      const prompt = this.formatPrompt(marketData, multiTimeframeTrends, currentGridSummary);

      let content: string;
      try {
        content = await this.llm.chat(this.getDecisionSystemPrompt(), prompt, this.temperature);
      } catch (e) {
        if (!(e instanceof LLMError)) throw e;
        // LLM 故障绝不放大成撤换单动作：保守维持网格（历史亏损来源之一）
        this.logger.printWarning(`[GridAgent] LLM 调用失败，回退 KEEP_GRID: ${e.message}`);
        return this.degradedKeepGrid(`LLM 调用失败，保守维持网格: ${e.message}`);
      }

      let aiDecision: Record<string, unknown>;
      try {
        aiDecision = extractJson(content);
      } catch (parseErr) {
        // 解析失败回退保守 KEEP_GRID（仅检查减仓保护单），而非全量重建
        this.logger.printWarning(`[GridAgent] LLM 决策解析失败，回退 KEEP_GRID: ${parseErr}`);
        return this.degradedKeepGrid(`LLM 输出解析失败，保守维持网格: ${parseErr}`);
      }

      const action = String(aiDecision.action ?? "").trim().toUpperCase();
      const confidence = safeFloat(aiDecision.confidence, 0);

      if (!VALID_GRID_ACTIONS.has(action)) {
        this.logger.printWarning(
          `[GridAgent] LLM 返回非法 action=${JSON.stringify(aiDecision.action)}，回退 KEEP_GRID`,
        );
        return this.degradedKeepGrid(
          `非法 action=${JSON.stringify(aiDecision.action)}，保守维持网格`,
          confidence,
        );
      }

      if (action === "UPDATE_GRID") {
        return await this.buildUpdateConfig(aiDecision, marketData, confidence);
      }

      // KEEP_GRID
      return {
        action: "KEEP_GRID",
        mode: String(aiDecision.mode ?? "NEUTRAL").trim().toUpperCase(),
        confidence,
        reason: aiDecision.reason ?? "AI 维持当前网格",
        llm_ok: true,
      };
    } catch (e) {
      // 非 LLM 环节的内部异常也走保守兜底。reason 打上「内部异常」前缀：
      // llm_ok=false 会计入 LLM 连续故障告警，归因文案必须能区分
      // 「LLM 真挂了」与「我们自己的代码炸了」，否则排障方向会被带偏。
      return { action: "ERROR", reason: `内部异常(非 LLM 调用失败): ${e}`, llm_ok: false };
    }
  }

  /** 把 AI 的 UPDATE_GRID 意图交给数学引擎，产出可执行网格配置。 */
  private async buildUpdateConfig(aiDecision: Dict, marketData: Dict, confidence: number): Promise<Dict> {
    const currentPrice = Number(marketData.current_price);
    const balanceInfo = await this.orderManager.getAvailableBalanceInfo();
    // 余额接口失败时 available 会回退为 0，若继续 UPDATE_GRID 会以最小单格金额
    // 触发全量重建、撤光现有挂单却可能挂不出新单，导致网格被意外清空。
    if (balanceInfo.status !== "ok") {
      this.logger.printWarning(`[GridAgent] 获取可用余额失败: ${balanceInfo.message}，回退 KEEP_GRID`);
      // llm_ok=true：LLM 本身正常，故障在交易所余额接口，不计入 LLM 连续失败告警
      return this.degradedKeepGrid(
        `获取可用余额失败: ${balanceInfo.message}，保守维持网格`,
        confidence,
        true,
      );
    }

    const available = safeFloat(balanceInfo.available, 0);
    const budget = this.gridBudget(available, safeFloat(balanceInfo.total ?? balanceInfo.equity, 0));
    let mode = String(aiDecision.mode ?? "NEUTRAL").trim().toUpperCase();
    if (!VALID_GRID_MODES.has(mode)) mode = "NEUTRAL";
    // 强制中性：忽略 AI 的 LONG/SHORT 方向，网格只做对称做市。
    // 线上验证 24h 亏损几乎全部来自方向翻转的 taker 反手（whipsaw），
    // 中性网格不主动建/反方向头寸，从源头消除该亏损与反手手续费。
    if (this.forceNeutralMode && mode !== "NEUTRAL") {
      this.logger.printInfo(`[GridAgent] 强制中性模式：忽略 AI 方向 ${mode}，覆盖为 NEUTRAL`);
      mode = "NEUTRAL";
    }

    const dynamicWidthPct = this.calculateDynamicWidthPct(marketData, aiDecision.width_pct, mode);
    const centerShiftPct = await this.inventoryCenterShift(dynamicWidthPct);
    if (centerShiftPct !== 0) {
      this.logger.printInfo(
        `[GridAgent] 库存倾斜：网格中心${centerShiftPct < 0 ? "下" : "上"}移 ${(Math.abs(centerShiftPct) * 100).toFixed(3)}%`,
      );
    }
    const mathConfig: Dict = calculateGridConfig({
      currentPrice,
      availableBalance: budget,
      mode,
      widthPct: dynamicWidthPct,
      gridNum: this.clampGridNum(aiDecision.grid_num),
      leverage: this.maxLeverage,
      adaptiveSizing: this.adaptiveSizing,
      minGridNum: this.minGridNum,
      makerFeeRate: this.getFeeRates().makerRate,
      centerShiftPct,
    });
    if (mathConfig.action === "INSUFFICIENT_CAPITAL") {
      // 资金撑不起最小网格：拒绝布单并醒目告警，避免小账户被最小单格金额
      // 反向放大成超额敞口（线上 $7.71 账户 16 倍名义敞口的直接根因）。
      this.logger.printError(`[GridAgent] 💸 资金不足拒绝布单: ${mathConfig.reason ?? ""}`);
      mathConfig.confidence = confidence;
      mathConfig.llm_ok = true;
      return mathConfig;
    }

    mathConfig.reason = aiDecision.reason ?? "AI 触发数学引擎更新";
    mathConfig.width_pct = dynamicWidthPct;
    mathConfig.confidence = confidence;
    mathConfig.llm_ok = true;
    return mathConfig;
  }

  /** 构造「保守维持网格」的兜底决策（llm_ok 标记故障归属）。 */
  private degradedKeepGrid(reason: string, confidence = 0, llmOk = false): Dict {
    return { action: "KEEP_GRID", mode: "NEUTRAL", confidence, reason, llm_ok: llmOk };
  }

  // ── 兜底重建（不经 LLM）───────────────────────────────────────────────

  /**
   * 不经 LLM，纯用市场数据 + 数学引擎生成一份中性网格配置。
   *
   * 用途：LLM 持续不可用时把网格从「空转」中救出。空转死锁的根源是只有
   * UPDATE_GRID 会重建网格，而 LLM 故障期间每轮只能产出 ERROR 或兜底
   * KEEP_GRID——层级已被清空时「维持现有网格」等于永远维持一片空白。
   *
   * 只产出 NEUTRAL 对称网格：判断方向是 LLM 的职责，LLM 不可用时不猜方向。
   * 宽度与格数全部由市场波动率推导，结果完全可复现、与 LLM 无关。
   */
  async buildFallbackConfig(marketData: Dict): Promise<Dict> {
    const currentPrice = safeFloat(marketData.current_price, 0);
    if (currentPrice <= 0) {
      return this.degradedKeepGrid("兜底建网格失败：当前价不可用，保守维持网格");
    }

    const balanceInfo = await this.orderManager.getAvailableBalanceInfo();
    if (balanceInfo.status !== "ok") {
      // 与 UPDATE_GRID 同样的理由：余额取不到时重建会撤光旧单又挂不出新单
      return this.degradedKeepGrid(
        `兜底建网格失败：获取可用余额失败(${balanceInfo.message})，保守维持网格`,
        0,
        true,
      );
    }

    const available = safeFloat(balanceInfo.available, 0);
    const budget = this.gridBudget(available, safeFloat(balanceInfo.total ?? balanceInfo.equity, 0));
    const widthPct = this.calculateDynamicWidthPct(marketData, null, "NEUTRAL");
    const mathConfig: Dict = calculateGridConfig({
      currentPrice,
      availableBalance: budget,
      mode: "NEUTRAL",
      widthPct,
      gridNum: this.clampGridNum(DEFAULT_AGENT_GRID_NUM),
      leverage: this.maxLeverage,
      adaptiveSizing: this.adaptiveSizing,
      minGridNum: this.minGridNum,
      makerFeeRate: this.getFeeRates().makerRate,
      centerShiftPct: await this.inventoryCenterShift(widthPct),
    });
    if (mathConfig.action === "INSUFFICIENT_CAPITAL") {
      this.logger.printError(`[GridAgent] 💸 兜底建网格资金不足，拒绝布单: ${mathConfig.reason ?? ""}`);
    } else {
      this.logger.printWarning(
        `[GridAgent] 🛟 LLM 持续不可用，按市场数据兜底重建中性网格 ` +
          `(宽度 ${(widthPct * 100).toFixed(2)}%，${DEFAULT_AGENT_GRID_NUM} 格)`,
      );
      mathConfig.reason = `LLM 持续不可用，按市场数据兜底重建中性网格(宽度${(widthPct * 100).toFixed(2)}%)`;
      mathConfig.width_pct = widthPct;
    }
    mathConfig.confidence = 0;
    mathConfig.llm_ok = false;
    mathConfig.fallback = true;
    return mathConfig;
  }

  // ── 宽度计算 ──────────────────────────────────────────────────────────

  /** 从布林带宽度 + K 线振幅 + 量能推导市场建议宽度。 */
  private estimateMarketWidthPct(marketData: Dict): number {
    const currentPrice = safeFloat(marketData.current_price, 0);
    if (currentPrice <= 0) return this.widthPctFallback;

    const bbUpper = safeFloat(marketData.bb_upper, 0);
    const bbLower = safeFloat(marketData.bb_lower, 0);
    const high = safeFloat(marketData.high, 0);
    const low = safeFloat(marketData.low, 0);
    const volumeChange = Math.abs(safeFloat(marketData.volume_change, 0));

    let bbWidthPct = 0;
    if (bbUpper > bbLower && bbLower > 0) bbWidthPct = (bbUpper - bbLower) / currentPrice;
    let candleRangePct = 0;
    if (high > low && low > 0) candleRangePct = (high - low) / currentPrice;

    let baseWidth: number;
    if (bbWidthPct > 0 && candleRangePct > 0) baseWidth = 0.7 * bbWidthPct + 0.3 * (candleRangePct * 2.2);
    else if (bbWidthPct > 0) baseWidth = bbWidthPct;
    else if (candleRangePct > 0) baseWidth = candleRangePct * 2.5;
    else baseWidth = this.widthPctFallback;

    const volumeBoost = 1 + Math.min(volumeChange / 100, 1.5) * 0.15;
    return clamp(baseWidth * volumeBoost, this.widthPctMin, this.widthPctMax);
  }

  /** 市场宽度与 AI 宽度按权重融合（AI 缺席时纯市场数据）。 */
  private calculateDynamicWidthPct(marketData: Dict, aiWidthPct: unknown = null, mode = "NEUTRAL"): number {
    let marketWidth = this.estimateMarketWidthPct(marketData);
    if (["LONG", "SHORT"].includes(String(mode ?? "").toUpperCase())) marketWidth *= 1.10;
    marketWidth = clamp(marketWidth, this.widthPctMin, this.widthPctMax);

    let aiWidth = safeFloat(aiWidthPct, 0);
    if (aiWidth > 0) {
      aiWidth = clamp(aiWidth, this.widthPctMin, this.widthPctMax);
      const blended = marketWidth * (1 - this.aiWidthBlendWeight) + aiWidth * this.aiWidthBlendWeight;
      return clamp(blended, this.widthPctMin, this.widthPctMax);
    }
    return marketWidth;
  }

  // ── Prompt ────────────────────────────────────────────────────────────

  private getDecisionSystemPrompt(): string {
    return `你是网格交易决策器。只负责判断“是否需要更新网格形态”，不负责下单执行。

目标：
1) 让网格方向与市场状态一致；
2) 避免不必要的频繁重置；
3) 给出可执行、稳定的 JSON 决策。

动作定义：
- UPDATE_GRID: 需要重置网格区间/层数/方向
- KEEP_GRID: 维持当前网格，不重置

mode 定义：
- LONG: 预期反弹/上行，以当前价为上沿向下布买网格
- SHORT: 预期回落/下行，以当前价为下沿向上布卖网格
- NEUTRAL: 震荡双向网格

建议规则：
- 出现以下任一情况，优先 UPDATE_GRID：
  1) 当前网格待成交单明显不足或结构失衡
  2) 市场从震荡切换到单边趋势（多周期同向）
  3) 波动显著放大，原区间明显不匹配
- 其余情况下优先 KEEP_GRID，减少无效重置

grid_num 建议（可按波动调节，系统会钳到 ${this.minGridNum}-${this.maxGridNum}）：
- 高波动：5-6
- 中波动：6-8
- 低波动：8-${this.maxGridNum}
注意：格子越密单格止盈越薄，回测显示密网格（16 格）稳定差于疏网格（8 格）——宁疏勿密

width_pct 约束：
- 必填，范围 ${this.widthPctMin.toFixed(2)} ~ ${this.widthPctMax.toFixed(2)}（${(this.widthPctMin * 100).toFixed(0)}%~${(this.widthPctMax * 100).toFixed(0)}%）
- 代表“区间宽度偏好”，系统会结合实时波动做动态融合

输出要求（必须严格遵守）：
- 只输出一个 JSON 对象
- 不要输出 markdown、解释性文字、代码块

JSON Schema:
{
  "action": "UPDATE_GRID | KEEP_GRID",
  "mode": "LONG | SHORT | NEUTRAL",
  "width_pct": 0.06,
  "grid_num": 8,
  "confidence": 0.78,
  "reason": "一句话说明依据"
}
`;
  }

  private formatPrompt(marketData: Dict, trends: Record<string, string>, summary: string): string {
    const currentPrice = safeFloat(marketData.current_price, 0);
    const rsi = safeFloat(marketData.rsi, 50);
    const macdHist = safeFloat(marketData.macd_hist, 0);
    const bbUpper = safeFloat(marketData.bb_upper, currentPrice);
    const bbLower = safeFloat(marketData.bb_lower, currentPrice);
    const volumeChange = safeFloat(marketData.volume_change, 0);
    const bbWidthPct = currentPrice > 0 ? (bbUpper - bbLower) / currentPrice : 0;

    return (
      `symbol=${this.symbol}\n` +
      `current_price=${currentPrice.toFixed(4)}\n` +
      `rsi=${rsi.toFixed(2)}\n` +
      `macd_hist=${macdHist.toFixed(6)}\n` +
      `bb_width_pct=${bbWidthPct.toFixed(4)}\n` +
      `volume_change_pct=${volumeChange.toFixed(2)}\n` +
      `multi_timeframe_trends=${JSON.stringify(trends)}\n` +
      `current_grid_summary=${summary}\n` +
      "请输出严格 JSON 决策。"
    );
  }
}

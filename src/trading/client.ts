/**
 * Hyperliquid 永续合约客户端
 * 基于 @nktkas/hyperliquid TypeScript SDK（viem 本地账户签名）。
 *
 * 适配层要点（正确性依赖这里）：
 * - 交易所拒单时**外层仍是 status=ok**，错误藏在 statuses[].error；SDK 对这种回执
 *   抛 ApiRequestError，适配层捕获后**还原原始回执**返回，交给 checkOrderSuccess
 *   判读。只判外层会把被拒单记成成功——这条有事故背书；
 * - 交易动作用资产索引（meta().universe 下标），本层负责 symbol → index 解析；
 * - 市价开平仓由本层用 IoC 激进限价 + 滑点实现。
 */

import {
  ApiRequestError,
  ExchangeClient,
  HttpTransport,
  InfoClient,
} from "@nktkas/hyperliquid";
import { privateKeyToAccount } from "viem/accounts";
import { Decimal } from "../utils/precision.js";
import { calculateFeeRates, type FeeRates } from "../fees.js";
import type { TradingLogger } from "../logger.js";
import { sleep } from "../utils/sleep.js";

// 交易对元数据缓存有效期（秒）：szDecimals 等精度信息几乎不变，
// 逐单拉全量 meta 纯属浪费且在批量布单时放大限流风险
const ASSET_INFO_CACHE_TTL = 600.0;

export type Dict = Record<string, any>;

/**
 * 限价单有效期：Gtc=普通挂单；Alo=只做 maker（会立即成交则整单被拒，绝不付 taker 费）；
 * Ioc=立即成交否则撤销（市价单的实现形态）。
 */
export type LimitTif = "Gtc" | "Alo" | "Ioc";

/** 批量限价单条目：一次 exchange.order 提交多单，回执 statuses 与条目顺序一一对齐。 */
export interface LimitOrderSpec {
  symbol: string;
  isBuy: boolean;
  size: number;
  price: number;
  reduceOnly?: boolean;
  tif?: LimitTif;
}

/** 单笔订单在（批量）回执中的判读结果。 */
interface OrderStatusView {
  ok: boolean;
  oid: number | null;
  error: string | null;
  filled: boolean;
  resting: boolean;
  avgPx: number | null;
  totalSz: number | null;
}

/**
 * 交易所客户端的结构化接口：执行层/策略层只依赖这一组成员。
 * 生产用 HyperliquidClient，回测用 sim/SimulatedClient，测试用桩——三者共享
 * 同一条策略与订单管理代码路径，回测结果才对生产有解释力。
 */
export type ExchangeClientLike = Pick<
  HyperliquidClient,
  | "address"
  | "testnet"
  | "getBalance"
  | "getPositions"
  | "getOpenOrders"
  | "getAssetInfo"
  | "getCurrentPrice"
  | "formatPrice"
  | "roundSize"
  | "placeLimitOrder"
  | "placeLimitOrders"
  | "placeTpslOrder"
  | "cancelOrder"
  | "cancelOrders"
  | "updateLeverage"
  | "getCandles"
  | "userFills"
  | "emergencyCloseWithRetry"
  | "closePosition"
  | "fetchUserFeeRates"
>;

/** Hyperliquid 永续合约交易客户端。 */
export class HyperliquidClient {
  readonly testnet: boolean;
  /** 余额和持仓查询的地址（API 钱包模式下为主钱包） */
  readonly address: string;
  /** 签名钱包地址 */
  readonly walletAddress: string;
  readonly isApiWalletMode: boolean;

  info!: InfoClient;
  private exchange!: ExchangeClient;
  private transport!: HttpTransport;
  private readonly wallet: ReturnType<typeof privateKeyToAccount>;
  private readonly logger?: TradingLogger;

  private assetInfoCache = new Map<string, { at: number; asset: Dict }>();
  private assetIndexCache: { at: number; map: Map<string, number> } | null = null;
  private abstractionMode: string | null | undefined = undefined;

  constructor(options: {
    privateKey: string;
    accountAddress?: string | null;
    testnet?: boolean;
    logger?: TradingLogger;
  }) {
    this.testnet = options.testnet ?? false;
    this.logger = options.logger;

    let pk = options.privateKey;
    if (!pk.startsWith("0x")) pk = "0x" + pk;
    this.wallet = privateKeyToAccount(pk as `0x${string}`);
    this.walletAddress = this.wallet.address;

    // API 钱包模式：private_key 是 API 钱包私钥，account_address 是主钱包地址
    const accountAddress = options.accountAddress ?? null;
    this.isApiWalletMode =
      accountAddress !== null && accountAddress.toLowerCase() !== this.wallet.address.toLowerCase();
    this.address = accountAddress ?? this.wallet.address;

    this.rebuildTransport();

    const log = (m: string) => {
      if (this.logger) this.logger.printInfo(m);
      else console.log(m);
    };
    log(`🔗 连接 Hyperliquid ${this.testnet ? "测试网" : "主网"}`);
    if (this.isApiWalletMode) {
      log("🤖 模式: API 钱包代理");
      log(`📍 主钱包地址: ${this.address}`);
      log(`🔑 API 钱包地址: ${this.walletAddress}`);
    } else {
      log("👤 模式: 单钱包");
      log(`📍 钱包地址: ${this.address}`);
    }
  }

  private rebuildTransport(): void {
    this.transport = new HttpTransport({ isTestnet: this.testnet });
    this.info = new InfoClient({ transport: this.transport });
    this.exchange = new ExchangeClient({ transport: this.transport, wallet: this.wallet });
  }

  private warn(message: string): void {
    if (this.logger) this.logger.printWarning(message);
    else console.warn(message);
  }

  private error(message: string): void {
    if (this.logger) this.logger.printError(message);
    else console.error(message);
  }

  /**
   * 交易动作统一出口：SDK 对「外层 ok、statuses 带 error」的拒单抛
   * ApiRequestError——此处还原为原始回执字典返回，交由 checkOrderSuccess
   * 判读，保持统一的数据流。传输层异常继续上抛（由各方法的
   * catch 归一为 {"status":"error"}）。
   */
  private async execAction<T>(call: () => Promise<T>): Promise<Dict> {
    try {
      return (await call()) as Dict;
    } catch (e) {
      if (e instanceof ApiRequestError && e.response && typeof e.response === "object") {
        return e.response as Dict;
      }
      throw e;
    }
  }

  /** symbol → 资产索引（meta().universe 下标；带 TTL 缓存）。找不到抛错。 */
  async assetIndex(symbol: string): Promise<number> {
    const now = Date.now() / 1000;
    if (!this.assetIndexCache || now - this.assetIndexCache.at >= ASSET_INFO_CACHE_TTL) {
      const meta = (await this.info.meta()) as Dict;
      const map = new Map<string, number>();
      (meta?.universe ?? []).forEach((asset: Dict, idx: number) => {
        if (asset?.name) map.set(String(asset.name), idx);
      });
      this.assetIndexCache = { at: now, map };
    }
    const idx = this.assetIndexCache.map.get(symbol);
    if (idx === undefined) throw new Error(`找不到交易对 ${symbol} 的资产索引`);
    return idx;
  }

  /**
   * 获取用户当前的实际费率（maker/taker），按 Hyperliquid 官方公式计算。
   */
  async fetchUserFeeRates(options: {
    isAlignedQuoteToken?: boolean;
    deployerFeeScale?: number;
    growthMode?: boolean;
  } = {}): Promise<FeeRates> {
    const userFees = (await this.info.userFees({ user: this.address as `0x${string}` })) as Dict;
    return calculateFeeRates(
      {
        makerRate: Number(userFees?.userAddRate ?? 0),
        takerRate: Number(userFees?.userCrossRate ?? 0),
      },
      {
        activeReferralDiscount: Number(userFees?.activeReferralDiscount ?? 0),
        isAlignedQuoteToken: options.isAlignedQuoteToken,
        deployerFeeScale: options.deployerFeeScale,
        growthMode: options.growthMode,
      },
    );
  }

  /**
   * 判定当前地址是否为保证金合一账户（unifiedAccount/portfolioMargin）。
   * 结果缓存（账户模式极少变更）；查询失败返回 null（未知，交由调用方降级）。
   */
  private async isUnifiedAccount(): Promise<boolean | null> {
    if (this.abstractionMode === undefined) {
      try {
        this.abstractionMode = String(
          await (this.info as Dict).userAbstraction({ user: this.address }),
        );
      } catch (e) {
        this.warn(`⚠️ 账户模式查询失败（userAbstraction）: ${e}`);
        return null;
      }
    }
    return this.abstractionMode === "unifiedAccount" || this.abstractionMode === "portfolioMargin";
  }

  /**
   * 获取账户余额信息。
   *
   * 注意：可用余额 = accountValue - totalMarginUsed，而不是直接用 totalRawUsd。
   *
   * 统一账户（unified account）兼容：新式账户 spot/perp 保证金合一，总净值以
   * spot 视图为准（USDC total=总抵押，hold=持仓/挂单占用），perp marginSummary
   * 无持仓时恒 0、有持仓时仅等于被占用的那部分抵押（实测：$11 账户开仓后 perp
   * accountValue 只剩 $2.28 == spot hold），两种形态都会严重低估净值。官方判定
   * 接口 userAbstraction 返回 unifiedAccount/portfolioMargin（合一）或 default
   * （经典），结果缓存；判定失败时退回启发式（perp 视图无资产才尝试 spot）。
   */
  async getBalance(): Promise<Dict | null> {
    try {
      const userState = (await this.info.clearinghouseState({
        user: this.address as `0x${string}`,
      })) as Dict;
      const marginSummary = userState?.marginSummary ?? {};
      let accountValue = Number(marginSummary.accountValue ?? 0);
      let totalMarginUsed = Number(marginSummary.totalMarginUsed ?? 0);
      let withdrawable = String(userState?.withdrawable ?? "0");

      const unified = await this.isUnifiedAccount();
      if (unified || (unified === null && accountValue <= 0)) {
        try {
          const spotState = (await this.info.spotClearinghouseState({
            user: this.address as `0x${string}`,
          })) as Dict;
          for (const balance of spotState?.balances ?? []) {
            if (balance?.coin !== "USDC") continue;
            const usdcTotal = Number(balance.total ?? 0);
            if (usdcTotal > 0) {
              accountValue = usdcTotal;
              totalMarginUsed = Number(balance.hold ?? 0);
              withdrawable = String(accountValue - totalMarginUsed);
            }
            break;
          }
        } catch (spotErr) {
          // spot 查询失败时维持 perp 视图结果，不因兼容路径引入新故障
          this.warn(`⚠️ 统一账户 spot 余额回退查询失败: ${spotErr}`);
        }
      }

      return {
        accountValue,
        totalMarginUsed,
        totalRawUsd: Number(marginSummary.totalRawUsd ?? 0),
        available: accountValue - totalMarginUsed,
        withdrawable,
      };
    } catch (e) {
      this.error(`❌ 获取余额失败: ${e}`);
      return null;
    }
  }

  /**
   * 获取当前持仓。
   *
   * 持仓列表；**查询失败返回 null（未知），绝不降级为空列表**——「查询失败」
   * 与「确认无持仓」是完全不同的风控语义：把 API 抖动当空仓会让清理逻辑撤掉
   * 在保护真实持仓的 TP/SL 触发单、让监控循环把成交订单误判为已取消。
   * 调用方必须显式处理 null（跳过本轮判断）。
   */
  async getPositions(): Promise<Dict[] | null> {
    try {
      const userState = (await this.info.clearinghouseState({
        user: this.address as `0x${string}`,
      })) as Dict;
      const positions: Dict[] = [];
      for (const assetPosition of userState?.assetPositions ?? []) {
        const position = assetPosition?.position ?? {};
        if (Number(position?.szi ?? 0) !== 0) positions.push(position);
      }
      return positions;
    } catch (e) {
      this.error(`❌ 获取持仓失败: ${e}`);
      return null;
    }
  }

  /**
   * 获取待处理的订单列表。
   *
   * 挂单列表；**查询失败返回 null（未知），绝不降级为空列表**——把「查不到」
   * 当「没挂单」会让网格增量同步把仍在挂着的订单判为已成交/已撤销，成对复制
   * 挂单或把库存移出簿记。调用方必须显式处理 null。
   *
   * 优先使用 frontendOpenOrders：包含 Trigger 单（Stop Market / TP Market）。
   * 仅使用 openOrders 会在部分账户模式下漏掉触发单，导致清理逻辑失效。
   */
  async getOpenOrders(includeTrigger = false): Promise<Dict[] | null> {
    try {
      let rawOrders: unknown = await this.info.frontendOpenOrders({
        user: this.address as `0x${string}`,
      });
      if (!Array.isArray(rawOrders)) {
        rawOrders = await this.info.openOrders({ user: this.address as `0x${string}` });
        if (!Array.isArray(rawOrders)) rawOrders = [];
      }
      const normalized = (rawOrders as Dict[])
        .filter((o) => o && typeof o === "object")
        .map((o) => HyperliquidClient.normalizeOpenOrder(o));
      if (includeTrigger) return normalized;
      return normalized.filter((o) => !HyperliquidClient.isTriggerLikeOrder(o));
    } catch (e) {
      this.error(`❌ 获取待处理订单失败: ${e}`);
      return null;
    }
  }

  static isTriggerLikeOrder(order: Dict): boolean {
    const orderType = order?.orderType;
    if (orderType && typeof orderType === "object") return "trigger" in orderType;
    if (order?.isTrigger) return true;
    if (typeof orderType === "string") {
      const lowered = orderType.toLowerCase();
      return lowered.includes("stop") || lowered.includes("take profit") || lowered.includes("trigger");
    }
    const triggerCondition = String(order?.triggerCondition ?? "").trim();
    return triggerCondition !== "" && triggerCondition !== "N/A";
  }

  private static normalizeOpenOrder(order: Dict): Dict {
    const normalized: Dict = { ...order };
    const orderType = normalized.orderType;
    if (orderType && typeof orderType === "object") return normalized;
    if (HyperliquidClient.isTriggerLikeOrder(order)) {
      normalized.orderType = {
        trigger: {
          triggerPx: normalized.triggerPx,
          triggerCondition: normalized.triggerCondition,
          kind: orderType,
        },
      };
    } else {
      normalized.orderType = { limit: { tif: normalized.tif ?? "Gtc" } };
    }
    return normalized;
  }

  /** 获取交易对的元数据信息（带 TTL 缓存；拉取失败回退过期缓存——旧精度远好于兜底猜测）。 */
  async getAssetInfo(symbol: string): Promise<Dict | null> {
    const cached = this.assetInfoCache.get(symbol);
    const now = Date.now() / 1000;
    if (cached && now - cached.at < ASSET_INFO_CACHE_TTL) return cached.asset;
    try {
      const meta = (await this.info.meta()) as Dict;
      for (const asset of meta?.universe ?? []) {
        if (asset?.name === symbol) {
          this.assetInfoCache.set(symbol, { at: now, asset });
          return asset;
        }
      }
      this.warn(`⚠️ 找不到交易对 ${symbol} 的元数据`);
      return null;
    } catch (e) {
      this.error(`❌ 获取交易对信息失败: ${e}`);
      return cached ? cached.asset : null;
    }
  }

  /** 获取当前价格（中间价）；失败返回 null。 */
  async getCurrentPrice(symbol: string): Promise<number | null> {
    try {
      const allMids = (await this.info.allMids()) as Dict;
      if (symbol in allMids) return Number(allMids[symbol]);
      this.warn(`⚠️ 找不到交易对 ${symbol}`);
      return null;
    } catch (e) {
      this.error(`❌ 获取价格失败: ${e}`);
      return null;
    }
  }

  /**
   * 根据 Hyperliquid 的价格精度要求格式化价格。
   *
   * Hyperliquid 价格要求（二者同时满足，整数价格始终合法）:
   * 1. 最多 5 位有效数字
   * 2. 最多 MAX_DECIMALS - szDecimals 位小数（永续合约 MAX_DECIMALS=6）
   *
   * 历史缺陷：此处曾硬编码「0.1 的整数倍」步进——对 ETH/BTC 等高价合约恰好
   * 无害，但低价合约（如 $0.12 档）的所有价格会被整体拍扁到 0.1，网格塌缩成
   * 同一价位、做多止损可被抬到入场价当场触发。HL 并无全局 0.1 tick 规则，
   * 按官方两条精度规则格式化即可。
   */
  async formatPrice(symbol: string, price: number): Promise<number> {
    try {
      let formatted: number;
      if (price > 0) {
        // 1. 限制到 5 位有效数字（例如 94283.7 -> 94284）
        const magnitude = Math.floor(Math.log10(Math.abs(price)));
        const decimalPlaces = 5 - magnitude - 1;
        if (decimalPlaces < 0) {
          const factor = 10 ** -decimalPlaces;
          formatted = Math.round(price / factor) * factor;
        } else {
          // 用 Decimal 做半进位取整，避免 JS toFixed 的银行家/浮点怪癖
          formatted = new Decimal(String(price))
            .toDecimalPlaces(decimalPlaces, Decimal.ROUND_HALF_UP)
            .toNumber();
        }
      } else {
        formatted = price;
      }

      // 2. 按 szDecimals 限制最大小数位数（永续: 6 - szDecimals）
      const assetInfo = await this.getAssetInfo(symbol);
      if (assetInfo) {
        const szDecimals = Number(assetInfo.szDecimals ?? 0);
        const maxPriceDecimals = 6 - szDecimals;
        formatted = new Decimal(String(formatted))
          .toDecimalPlaces(Math.max(0, maxPriceDecimals), Decimal.ROUND_HALF_UP)
          .toNumber();
      }
      return formatted;
    } catch (e) {
      this.error(`❌ 格式化价格失败: ${e}`);
      // 回退：四舍五入到整数（最安全）
      return Math.round(price);
    }
  }

  /**
   * 按交易对 szDecimals 向下取整下单数量。
   *
   * 用普通 round 是可向上进位的——开仓量被悄悄放大意味着实际敞口超出策略预算。
   * 资金方向的取整必须只朝保守方向（ROUND_DOWN）。
   */
  async roundSize(symbol: string, size: number): Promise<number> {
    const assetInfo = await this.getAssetInfo(symbol);
    let decimals = 3;
    if (assetInfo && "szDecimals" in assetInfo) {
      const parsed = Math.trunc(Number(assetInfo.szDecimals));
      decimals = Number.isFinite(parsed) ? Math.max(0, parsed) : 3;
    }
    return new Decimal(String(size)).toDecimalPlaces(decimals, Decimal.ROUND_DOWN).toNumber();
  }

  /**
   * 检查订单是否成功。
   *
   * 【增强版】：区分订单提交成功和实际成交；有成交或挂单都算成功；
   * statuses 中任何 error 都判失败——HL 拒单时外层仍为 status=ok。
   */
  static checkOrderSuccess(orderResult: Dict | null | undefined): [boolean, string | null] {
    if (!orderResult) return [false, "订单结果为空"];
    if (orderResult.status !== "ok") {
      const errorMsg = orderResult.message ?? orderResult.response ?? "未知错误";
      return [false, `订单请求失败: ${errorMsg}`];
    }
    const response = orderResult.response ?? {};
    if (response.type === "order") {
      const statuses: Dict[] = response.data?.statuses ?? [];
      if (!statuses.length) return [false, "没有返回订单状态"];
      const errors: string[] = [];
      let filledCount = 0;
      let restingCount = 0;
      for (const status of statuses) {
        if (status && typeof status === "object") {
          if ("error" in status) errors.push(String(status.error));
          else if ("filled" in status) filledCount += 1;
          else if ("resting" in status) restingCount += 1;
        }
      }
      if (errors.length) return [false, errors.join("; ")];
      if (filledCount > 0 || restingCount > 0) return [true, null];
      for (const status of statuses) {
        if (status && typeof status === "object" && !status.error) return [true, null];
      }
      return [false, `未知订单状态: ${JSON.stringify(statuses)}`];
    }
    // 对于其他类型的响应，默认认为成功
    return [true, null];
  }

  /**
   * 批量回执逐单判读：statuses[i] 对应第 i 个条目。外层非 ok 时全部判失败
   * （错误文案取外层）；条目缺失视为失败（交易所没有回应这一单，不能当成功）。
   */
  static orderStatuses(orderResult: Dict | null | undefined, count: number): OrderStatusView[] {
    const empty = (error: string | null): OrderStatusView => ({
      ok: false, oid: null, error, filled: false, resting: false, avgPx: null, totalSz: null,
    });
    if (!orderResult) return Array.from({ length: count }, () => empty("订单结果为空"));
    if (orderResult.status !== "ok") {
      const msg = `订单请求失败: ${orderResult.message ?? orderResult.response ?? "未知错误"}`;
      return Array.from({ length: count }, () => empty(msg));
    }
    const statuses: Dict[] = orderResult.response?.data?.statuses ?? [];
    return Array.from({ length: count }, (_, i) => {
      const status = statuses[i];
      if (!status || typeof status !== "object") return empty("没有返回订单状态");
      if ("error" in status) return empty(String(status.error));
      if ("filled" in status) {
        return {
          ok: true, oid: status.filled?.oid ?? null, error: null, filled: true, resting: false,
          avgPx: Number(status.filled?.avgPx ?? 0) || null, totalSz: Number(status.filled?.totalSz ?? 0) || null,
        };
      }
      if ("resting" in status) {
        return { ok: true, oid: status.resting?.oid ?? null, error: null, filled: false, resting: true, avgPx: null, totalSz: null };
      }
      return { ...empty(null), ok: true };
    });
  }

  /** 判断拒单原因是否为「post-only 单会立即成交」（Alo 单的正常拒绝，不是故障）。 */
  static isPostOnlyRejection(error: string | null | undefined): boolean {
    const msg = String(error ?? "").toLowerCase();
    return msg.includes("post only") || msg.includes("immediately match") || msg.includes("alo");
  }

  /** 从订单结果中提取成交信息。 */
  static getOrderFillInfo(orderResult: Dict | null | undefined): Dict {
    const result: Dict = {
      is_filled: false,
      fill_price: null,
      fill_size: null,
      order_id: null,
      is_resting: false,
    };
    if (!orderResult || orderResult.status !== "ok") return result;
    const response = orderResult.response ?? {};
    if (response.type !== "order") return result;
    for (const status of response.data?.statuses ?? []) {
      if (status && typeof status === "object") {
        if ("filled" in status) {
          result.is_filled = true;
          result.fill_price = Number(status.filled?.avgPx ?? 0);
          result.fill_size = Number(status.filled?.totalSz ?? 0);
          result.order_id = status.filled?.oid ?? null;
          break;
        } else if ("resting" in status) {
          result.is_resting = true;
          result.order_id = status.resting?.oid ?? null;
        }
      }
    }
    return result;
  }

  /** 下限价单（默认 Gtc；网格做市传 Alo 只做 maker）。 */
  async placeLimitOrder(
    symbol: string,
    isBuy: boolean,
    size: number,
    price: number,
    reduceOnly = false,
    tif: LimitTif = "Gtc",
  ): Promise<Dict> {
    return this.placeLimitOrders([{ symbol, isBuy, size, price, reduceOnly, tif }]);
  }

  /**
   * 批量下限价单：一次请求提交全部条目（交易所逐单判定，回执 statuses 与
   * 条目顺序对齐，用 orderStatuses 逐单判读）。网格整张布单从 N 次往返变一次，
   * 限流压力与「布到一半被打断」的窗口一起消失。
   */
  async placeLimitOrders(specs: LimitOrderSpec[]): Promise<Dict> {
    if (!specs.length) return { status: "ok", response: { type: "order", data: { statuses: [] } } };
    try {
      const orders: Dict[] = [];
      for (const spec of specs) {
        orders.push({
          a: await this.assetIndex(spec.symbol),
          b: spec.isBuy,
          p: String(await this.formatPrice(spec.symbol, spec.price)),
          s: String(await this.roundSize(spec.symbol, spec.size)),
          r: !!spec.reduceOnly,
          t: { limit: { tif: spec.tif ?? "Gtc" } },
        });
      }
      return await this.execAction(() => this.exchange.order({ orders: orders as never, grouping: "na" }));
    } catch (e) {
      this.error(`❌ 下单失败: ${e}`);
      return { status: "error", message: String(e) };
    }
  }

  /**
   * 下止盈或止损单。
   *
   * 【重要改进】：
   * - 止损单使用更大滑点(10%)确保在极端行情下能成交
   * - 止盈单使用较小滑点(1%)获取更好价格
   */
  async placeTpslOrder(options: {
    symbol: string;
    triggerPrice: number;
    isBuy: boolean;
    size: number;
    isTp?: boolean;
    slSlippage?: number;
  }): Promise<Dict> {
    const { symbol, isBuy } = options;
    const isTp = options.isTp ?? true;
    const slSlippage = options.slSlippage ?? 0.10; // 止损滑点提高到10%确保成交
    try {
      const triggerPrice = await this.formatPrice(symbol, Number(options.triggerPrice));
      // 格式化数量：按交易对精度向下取整，与开仓量的取整方向保持一致
      const size = await this.roundSize(symbol, options.size);

      // 限价方向说明：
      // - 止损卖出：limit < trigger（接受更低价格）；止损买入：limit > trigger
      // - 止盈卖出：limit > trigger（要求更高价格）；止盈买入：limit < trigger
      const slippage = isTp ? 0.01 : slSlippage;
      let limitPrice: number;
      if (isTp) {
        limitPrice = !isBuy ? triggerPrice * (1 + slippage) : triggerPrice * (1 - slippage);
      } else {
        limitPrice = !isBuy ? triggerPrice * (1 - slippage) : triggerPrice * (1 + slippage);
      }
      limitPrice = await this.formatPrice(symbol, limitPrice);

      const asset = await this.assetIndex(symbol);
      const orderType = {
        trigger: { isMarket: true, triggerPx: String(triggerPrice), tpsl: (isTp ? "tp" : "sl") as "tp" | "sl" },
      };
      const orderResult = await this.execAction(() =>
        this.exchange.order({
          orders: [
            {
              a: asset,
              b: isBuy,
              p: String(limitPrice),
              s: String(size),
              r: true,
              t: orderType,
            },
          ],
          grouping: "na",
        }),
      );
      // 如果失败，添加请求参数到结果中便于调试
      if (orderResult && orderResult.status !== "ok") {
        orderResult.request_params = {
          symbol, is_buy: isBuy, size, limit_price: limitPrice,
          trigger_price: triggerPrice, order_type: orderType, reduce_only: true, is_tp: isTp, slippage,
        };
      }
      return orderResult;
    } catch (e) {
      this.error(`❌ 下止盈止损单失败: ${e}`);
      return {
        status: "error",
        message: String(e),
        request_params: {
          symbol, trigger_price: options.triggerPrice, is_buy: isBuy, size: options.size, is_tp: isTp,
        },
      };
    }
  }

  /** 取消订单。 */
  async cancelOrder(symbol: string, oid: number): Promise<Dict> {
    return this.cancelOrders(symbol, [oid]);
  }

  /**
   * 批量撤单（一次请求）。回执 `response.data.statuses[i]` 为 "success" 或
   * `{error}`，与 oids 顺序对齐；外层 status 非 ok 视为全部失败。
   */
  async cancelOrders(symbol: string, oids: number[]): Promise<Dict> {
    if (!oids.length) return { status: "ok", response: { type: "cancel", data: { statuses: [] } } };
    try {
      const asset = await this.assetIndex(symbol);
      return await this.execAction(() =>
        this.exchange.cancel({ cancels: oids.map((o) => ({ a: asset, o })) }),
      );
    } catch (e) {
      this.error(`❌ 取消订单失败: ${e}`);
      return { status: "error", message: String(e) };
    }
  }

  /** 批量撤单回执逐条判读：返回与 oids 对齐的成功标记（外层失败=全 false）。 */
  static cancelStatuses(result: Dict | null | undefined, count: number): boolean[] {
    if (!result || result.status !== "ok") return new Array(count).fill(false);
    const statuses: unknown[] = result.response?.data?.statuses ?? [];
    return Array.from({ length: count }, (_, i) => {
      const s = statuses[i];
      if (s === undefined) return false;
      if (typeof s === "string") return s.toLowerCase() === "success";
      return !(s && typeof s === "object" && "error" in (s as Dict));
    });
  }

  /**
   * 更新杠杆倍数（超过交易对上限直接拒绝；逐仓降杠杆失败时按当前杠杆继续，
   * 返回 status=warning + current_leverage——保证金不足时降杠杆是硬约束）。
   */
  async updateLeverage(symbol: string, leverage: number, isCross = true): Promise<Dict> {
    try {
      const assetInfo = await this.getAssetInfo(symbol);
      if (assetInfo) {
        const maxLeverage = Number(assetInfo.maxLeverage ?? 50);
        if (leverage > maxLeverage) {
          const errorMsg = `杠杆 ${leverage}x 超过 ${symbol} 的最大杠杆 ${maxLeverage}x`;
          this.error(`❌ ${errorMsg}`);
          return { status: "error", message: errorMsg };
        }
      }

      const doUpdate = async (): Promise<Dict> => {
        const asset = await this.assetIndex(symbol);
        return this.execAction(() =>
          this.exchange.updateLeverage({ asset, isCross, leverage }),
        );
      };

      // 逐仓模式：检查当前持仓杠杆（查询失败按无持仓处理，直接设杠杆）
      if (!isCross) {
        const positions = (await this.getPositions()) ?? [];
        for (const position of positions) {
          if (position?.coin !== symbol) continue;
          const leverageInfo = position?.leverage;
          const currentLeverage =
            leverageInfo && typeof leverageInfo === "object"
              ? Math.trunc(Number(leverageInfo.value ?? 1))
              : Math.trunc(Number(leverageInfo ?? 1)) || 1;
          if (leverage < currentLeverage) {
            this.warn(`⚠️ 当前持仓杠杆: ${currentLeverage}x, 目标杠杆: ${leverage}x`);
            this.warn("⚠️ 降低杠杆可能需要增加保证金，如果失败将使用当前杠杆");
            const result = await doUpdate();
            if (result?.status === "err" || result?.status === "error") {
              const errorMsg = String(result?.response ?? result?.message ?? "未知错误");
              const lowered = errorMsg.toLowerCase();
              if (lowered.includes("sufficient margin") || lowered.includes("decrease leverage")) {
                this.warn(`⚠️ 降低杠杆失败（保证金不足），将使用当前杠杆 ${currentLeverage}x`);
                return {
                  status: "warning",
                  message: `无法降低杠杆（需要更多保证金），使用当前杠杆 ${currentLeverage}x`,
                  current_leverage: currentLeverage,
                  target_leverage: leverage,
                };
              }
              this.error(`❌ 杠杆设置失败: ${errorMsg}`);
              return { status: "error", message: errorMsg };
            }
            return result;
          } else if (leverage > currentLeverage) {
            this.logger?.printInfo(`ℹ️ 当前持仓杠杆: ${currentLeverage}x, 提高至: ${leverage}x`);
          } else {
            this.logger?.printInfo(`ℹ️ 当前持仓杠杆: ${currentLeverage}x, 保持不变`);
            return { status: "ok", message: `杠杆已为 ${leverage}x，无需更改` };
          }
        }
      }

      const result = await doUpdate();
      if (result?.status === "err" || result?.status === "error") {
        const errorMsg = String(result?.response ?? result?.message ?? "未知错误");
        this.error(`❌ 杠杆设置失败: ${errorMsg}`);
        return { status: "error", message: errorMsg };
      }
      return result;
    } catch (e) {
      this.error(`❌ 更新杠杆失败: ${e}`);
      return { status: "error", message: String(e) };
    }
  }

  /** 获取K线数据（毫秒时间戳区间）。 */
  async getCandles(
    symbol: string,
    interval = "15m",
    startTime?: number,
    endTime?: number,
  ): Promise<Dict[] | null> {
    try {
      const candles = await this.info.candleSnapshot({
        coin: symbol,
        interval: interval as never,
        startTime: startTime ?? Date.now() - 24 * 3600 * 1000,
        endTime,
      });
      return candles as unknown as Dict[];
    } catch (e) {
      this.error(`❌ 获取K线数据失败: ${e}`);
      return null;
    }
  }

  /** 查询成交历史（增量同步的成交确认与净额归因共用）。 */
  async userFills(): Promise<Dict[]> {
    return (await this.info.userFills({ user: this.address as `0x${string}` })) as unknown as Dict[];
  }

  /**
   * 紧急平仓（止损失败回滚 / 风控熔断强平 / Triple Barrier 的统一兜底）。
   *
   * 策略：
   * 1. 按 size 重试部分平仓（size=null 直接全平），尝试间指数退避
   *    （0.5s/1s/2s），避免无间隔连发被交易所限流/判重，反而降低成功率；
   * 2. 多次失败后退一步用市价全平兜底——部分平仓依赖 getPositions 查询持仓
   *    方向，行情/网络抖动导致查询为空会一直失败，全平不依赖该查询；
   * 3. 仍失败则打 critical 日志由人工介入，绝不静默放过裸仓。
   *
   * 这是全仓库唯一合法的「风控平仓」入口：所有平仓必须经 checkOrderSuccess
   * 校验内层 statuses——closePosition 吞异常返回的 {"status":"error"} 是真值
   * 对象，只判 `if (result)` 会把失败记成成功。
   */
  async emergencyCloseWithRetry(
    symbol: string,
    size: number | null,
    options: { reason: string; maxRetries?: number },
  ): Promise<[boolean, Dict | null]> {
    const maxRetries = options.maxRetries ?? 3;
    let lastResult: Dict | null = null;
    let lastError: string | null = null;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      if (attempt > 1) {
        // 0.5s, 1s, 2s... 指数退避（上限 2s）
        await sleep(Math.min(2.0, 0.5 * 2 ** (attempt - 2)) * 1000);
      }
      this.logger?.printInfo(`➡️ 紧急平仓第 ${attempt}/${maxRetries} 次尝试（${options.reason}）`);
      lastResult = await this.closePosition(symbol, size);
      const [ok, err] = HyperliquidClient.checkOrderSuccess(lastResult);
      lastError = err;
      if (ok) {
        this.logger?.printInfo(`✅ 紧急平仓成功（第 ${attempt} 次，${options.reason}）`);
        return [true, lastResult];
      }
      this.warn(`⚠️ 紧急平仓尝试失败（第 ${attempt} 次）：${lastError}`);
    }

    // 全平兜底：按 size 多次失败（常因 getPositions 查不到持仓），改用市价全平
    this.logger?.printInfo(`➡️ 紧急平仓改用市价全平兜底（${options.reason}）`);
    lastResult = await this.closePosition(symbol, null);
    const [ok, err] = HyperliquidClient.checkOrderSuccess(lastResult);
    if (ok) {
      this.logger?.printInfo(`✅ 市价全平兜底成功（${options.reason}）`);
      return [true, lastResult];
    }
    lastError = err;

    // critical 进 main.log：容器 stdout 会随重启丢失，资金安全告警必须落盘
    (this.logger?.printCritical ?? this.error).call(
      this.logger ?? this,
      `紧急平仓在 ${maxRetries} 次重试+全平兜底后仍失败（${symbol}，${options.reason}）: ${lastError}，请立即手动处理！`,
    );
    return [false, lastResult];
  }

  /**
   * 平仓（IoC 激进限价 + 滑点）。
   *
   * 部分平仓：查持仓仅为把平仓量钳制到实际持仓——历史实现用市价反向开单且
   * 不钳制数量，簿记漂移（层级记录量大于真实持仓）时会把仓位反向翻转成一笔
   * 无止损的新仓。平仓一律 reduce_only + IoC。
   */
  async closePosition(symbol: string, size: number | null = null): Promise<Dict> {
    try {
      const positions = await this.getPositions();
      if (positions === null) {
        return { status: "error", message: `持仓查询失败，无法平仓 ${symbol}` };
      }
      const position = positions.find((p) => p?.coin === symbol);
      if (!position) return { status: "error", message: `没有 ${symbol} 的持仓` };
      const positionSize = Number(position.szi ?? 0);
      if (positionSize === 0) return { status: "error", message: `${symbol} 仓位为 0` };

      let closeSize: number;
      if (size === null) {
        this.logger?.printInfo(`🔴 市价全平 ${symbol}`);
        closeSize = Math.abs(positionSize);
      } else {
        // 平仓量钳制到实际持仓，绝不反向开仓
        closeSize = Math.min(Math.abs(size), Math.abs(positionSize));
        closeSize = await this.roundSize(symbol, closeSize);
        if (closeSize <= 0) return { status: "error", message: `${symbol} 平仓量取整后为 0` };
        this.logger?.printInfo(`🔴 市价部分平仓 ${symbol}: ${closeSize}（持仓 ${positionSize}）`);
      }

      const isBuy = positionSize < 0; // 空仓平仓要买；多仓平仓要卖
      const mid = await this.getCurrentPrice(symbol);
      if (!mid || mid <= 0) return { status: "error", message: `无法获取 ${symbol} 市价` };
      const aggressive = await this.formatPrice(symbol, mid * (isBuy ? 1.01 : 0.99));
      const asset = await this.assetIndex(symbol);
      return await this.execAction(() =>
        this.exchange.order({
          orders: [
            {
              a: asset,
              b: isBuy,
              p: String(aggressive),
              s: String(closeSize),
              r: true,
              t: { limit: { tif: "Ioc" } },
            },
          ],
          grouping: "na",
        }),
      );
    } catch (e) {
      this.error(`❌ 平仓失败: ${e}`);
      return { status: "error", message: String(e) };
    }
  }
}

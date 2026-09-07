/**
 * 用户成交事件流：把「开仓成交 → 挂出 reduce_only 平仓单」的延迟从一个网格周期压到亚秒级。
 *
 * 为什么需要：网格的成交确认原本只在周期开头做一次（`grid.interval_minutes`，默认 5 分钟）。
 * 一笔开仓成交最长要等一整个周期才挂出平仓单，而这段时间恰恰是价格刚穿过该层、最可能
 * 再穿回来的时候——止盈价被越过又回落，这一轮的价差就白丢了，库存却留了下来。
 *
 * **铁律：这条流只是加速器，永远不是真值来源。**
 * 它只回答「这个交易对刚才可能有成交」，成交与否、成交多少仍由既有的 REST 路径
 * （`userFills` + `getOpenOrders`）确认。掉线、丢消息、乱序都只会让节奏退化回原来的
 * 周期，不会产生任何错误的状态判定——「查询失败」与「确认为空」的语义边界不因为多了
 * 一条流而被破坏。回测不注入这条流，因此策略与簿记在回测与生产之间依然零分叉。
 *
 * 订阅只需要账户地址（公开信息），不需要私钥，也不发送任何交易动作。
 */

import type { TradingLogger } from "../logger.js";
import { clock } from "../utils/clock.js";

/** 一条成交回报里我们用得上的字段（结构化契约，不绑定 SDK 具体类型）。 */
export interface FillEventRow {
  /** 交易对（Hyperliquid 简单符号，如 BTC） */
  coin?: string;
  /** 成交 id：去重主键 */
  tid?: number;
  /** 订单 id */
  oid?: number;
  /** 交易所时间（毫秒） */
  time?: number;
}

/** 一次推送（可能含多笔成交）。`isSnapshot` 为订阅建立/重连后的历史快照。 */
export interface FillEvent {
  fills?: FillEventRow[];
  isSnapshot?: boolean;
}

/** 订阅句柄。 */
export interface FillSubscription {
  unsubscribe(): Promise<void>;
}

/**
 * 订阅函数：默认走 SDK 的 WebSocket 传输层（自带重连与自动重订阅），
 * 测试注入假实现以避免网络。
 */
export type FillSubscribe = (
  params: { user: string; testnet: boolean },
  listener: (event: FillEvent) => void,
) => Promise<FillSubscription>;

export interface FillStreamHealth {
  enabled: boolean;
  /** 订阅是否已建立 */
  subscribed: boolean;
  /** 收到的推送次数（含快照） */
  events: number;
  /** 去重后真正触发同步的成交笔数 */
  fills: number;
  /** 因去重而忽略的成交笔数（重连快照重放） */
  duplicates: number;
  /** 最近一次成交事件的本机时间（毫秒）；从未收到为 null */
  last_fill_at: number | null;
  /** 最近一次订阅错误 */
  error: string | null;
}

/** 去重集合的容量：一天的网格成交远不到这个量级，超出即按插入顺序淘汰。 */
const DEDUP_CAPACITY = 5000;

/**
 * 默认订阅实现：SDK 的 `SubscriptionClient.userFills`。
 *
 * 传输层自带指数退避重连与重连后自动重订阅（`resubscribe` 默认开），因此这里不再
 * 自造一套连接管理——录制器那套是独立进程、需要自己处理进程级兜底，情况不同。
 */
export const defaultFillSubscribe: FillSubscribe = async (params, listener) => {
  const hl = (await import("@nktkas/hyperliquid")) as unknown as {
    WebSocketTransport: new (options: { isTestnet: boolean }) => { close(): Promise<void> };
    SubscriptionClient: new (config: { transport: unknown }) => {
      userFills(
        p: { user: `0x${string}` },
        cb: (data: FillEvent) => void,
      ): Promise<{ unsubscribe(): Promise<void> }>;
    };
  };
  const transport = new hl.WebSocketTransport({ isTestnet: params.testnet });
  const client = new hl.SubscriptionClient({ transport });
  const sub = await client.userFills({ user: params.user as `0x${string}` }, listener);
  return {
    async unsubscribe() {
      try {
        await sub.unsubscribe();
      } finally {
        await transport.close();
      }
    },
  };
};

/**
 * 订阅本账户成交，去重后把「某交易对可能有成交」这一提示交给回调。
 *
 * 生命周期：`start()` 建立订阅（失败只告警，不抛出——加速器缺席不该拦住引擎启动）；
 * `stop()` 退订。重连由传输层负责，重连后的历史快照会再次推来已处理过的成交，
 * 由 `tid` 去重挡掉；**首次快照只用来播种去重集合，不触发同步**（启动时周期本来就会跑）。
 */
export class UserFillStream {
  private readonly address: string;
  private readonly testnet: boolean;
  private readonly logger: TradingLogger;
  private readonly onFill: (coin: string) => void;
  private readonly subscribe: FillSubscribe;

  private subscription: FillSubscription | null = null;
  private seededSnapshot = false;
  private readonly seen = new Set<string>();
  private readonly seenOrder: string[] = [];
  private events = 0;
  private fills = 0;
  private duplicates = 0;
  private lastFillAt: number | null = null;
  private error: string | null = null;

  constructor(options: {
    /** 账户地址（API 钱包模式下为主钱包地址） */
    address: string;
    testnet: boolean;
    logger: TradingLogger;
    /** 收到新成交时的提示回调（参数为交易对）；实现方自行去抖与加锁 */
    onFill: (coin: string) => void;
    subscribe?: FillSubscribe;
  }) {
    this.address = options.address;
    this.testnet = options.testnet;
    this.logger = options.logger;
    this.onFill = options.onFill;
    this.subscribe = options.subscribe ?? defaultFillSubscribe;
  }

  async start(): Promise<boolean> {
    if (this.subscription) return true;
    try {
      this.subscription = await this.subscribe(
        { user: this.address, testnet: this.testnet },
        (event) => this.handle(event),
      );
      this.error = null;
      this.logger.printInfo("📡 成交事件流已订阅（加速平仓单挂出；成交确认仍以 REST 为准）");
      return true;
    } catch (e) {
      // 加速器订阅失败只是回到周期节奏，绝不能拦住引擎启动
      this.error = String(e).slice(0, 200);
      this.logger.printWarning(`⚠️ 成交事件流订阅失败，退化为按周期确认成交: ${this.error}`);
      return false;
    }
  }

  async stop(): Promise<void> {
    const sub = this.subscription;
    this.subscription = null;
    if (!sub) return;
    try {
      await sub.unsubscribe();
      this.logger.printInfo("📡 成交事件流已退订");
    } catch (e) {
      this.logger.printWarning(`成交事件流退订异常: ${e}`);
    }
  }

  health(): FillStreamHealth {
    return {
      enabled: true,
      subscribed: this.subscription !== null,
      events: this.events,
      fills: this.fills,
      duplicates: this.duplicates,
      last_fill_at: this.lastFillAt,
      error: this.error,
    };
  }

  /** 处理一次推送：去重后对每个新成交的交易对发一次提示。 */
  private handle(event: FillEvent): void {
    this.events += 1;
    const rows = Array.isArray(event?.fills) ? event.fills : [];
    // 首次快照是订阅建立时的历史成交：只播种去重集合。启动时网格周期本来就会跑一次，
    // 再触发一遍纯属重复；但重连后的快照必须照常处理——那里面可能有掉线期间漏掉的成交。
    const seedOnly = !!event?.isSnapshot && !this.seededSnapshot;
    if (event?.isSnapshot) this.seededSnapshot = true;

    const coins = new Set<string>();
    for (const row of rows) {
      const key = this.keyOf(row);
      if (this.seen.has(key)) {
        this.duplicates += 1;
        continue;
      }
      this.remember(key);
      if (seedOnly) continue;
      this.fills += 1;
      this.lastFillAt = clock.now();
      const coin = String(row?.coin ?? "").trim();
      if (coin) coins.add(coin);
    }
    for (const coin of coins) {
      try {
        this.onFill(coin);
      } catch (e) {
        this.logger.printWarning(`成交事件回调异常 ${coin}: ${e}`);
      }
    }
  }

  /** 去重主键：优先成交 id；缺失时退化为订单 id + 时间 + 交易对。 */
  private keyOf(row: FillEventRow): string {
    if (Number.isFinite(row?.tid)) return `t:${row.tid}`;
    return `o:${row?.oid ?? "?"}:${row?.time ?? "?"}:${row?.coin ?? "?"}`;
  }

  private remember(key: string): void {
    this.seen.add(key);
    this.seenOrder.push(key);
    while (this.seenOrder.length > DEDUP_CAPACITY) {
      const oldest = this.seenOrder.shift();
      if (oldest !== undefined) this.seen.delete(oldest);
    }
  }
}

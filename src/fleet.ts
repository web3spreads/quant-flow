/**
 * 大盘（Fleet）：多账户引擎的并行编排。
 *
 * 每个账户 = 一台完整独立的 Engine（自己的交易所客户端/交易锁/保护链/策略/
 * 状态目录/日志目录），账户之间零共享可变状态——并行互不干扰，一个账户的
 * 故障绝不传染另一个。Fleet 只负责：装配、统一启停、热重配、聚合视图。
 *
 * 资金安全闸门：同一「地址 × 环境」被两个账户配置时拒绝启动——Hyperliquid
 * 单向持仓下两个引擎操作同一账户会互相强平、互撤对方的保护单（与引擎内
 * 「网格独占 symbols[0]」是同一条铁律的账户级形态）。
 */

import path from "node:path";
import fs from "node:fs";
import { Engine } from "./engine.js";
import { TradingLogger, type HostLogger } from "./logger.js";
import type { DshLlmLike } from "./llm.js";
import type { RuntimeConfig } from "./config.js";
import type { Dict } from "./trading/client.js";
import { clock } from "./utils/clock.js";

export class Fleet {
  engines: Engine[] = [];
  config: RuntimeConfig;
  private readonly host: HostLogger;
  private readonly getDshLlm: () => DshLlmLike | undefined;
  /** 大盘自身的日志器（装配/启停等编排级信息，写在根 log 目录） */
  readonly logger: TradingLogger;

  constructor(options: {
    config: RuntimeConfig;
    host: HostLogger;
    getDshLlm?: () => DshLlmLike | undefined;
  }) {
    this.config = options.config;
    this.host = options.host;
    this.getDshLlm = options.getDshLlm ?? (() => undefined);
    this.logger = new TradingLogger({ logDir: options.config.paths.log_dir, host: options.host });
    this.build(options.config);
  }

  private accountHost(name: string): HostLogger {
    // 多账户时宿主日志加账户前缀，单账户保持旧观感
    if (this.config.accounts.length <= 1) return this.host;
    const prefix = `[${name}] `;
    return {
      info: (...args) => this.host.info(prefix + String(args[0] ?? ""), ...args.slice(1)),
      warn: (...args) => this.host.warn(prefix + String(args[0] ?? ""), ...args.slice(1)),
      error: (...args) => this.host.error(prefix + String(args[0] ?? ""), ...args.slice(1)),
    };
  }

  private build(config: RuntimeConfig): void {
    this.config = config;
    this.engines = [];
    for (const account of config.accounts) {
      const logger = new TradingLogger({
        logDir: account.paths.log_dir,
        host: this.accountHost(account.name),
      });
      this.engines.push(
        new Engine({
          config: account,
          logger,
          getDshLlm: this.getDshLlm,
        }),
      );
    }
    this.assertNoSharedAddress();
  }

  /**
   * 同一「地址 × 环境」禁止被两个账户并行操作（互相强平/互撤保护单）。
   * 地址由私钥在客户端内推导，故在装配后检查。
   */
  private assertNoSharedAddress(): void {
    const seen = new Map<string, string>();
    for (const engine of this.engines) {
      const key = `${engine.client.address.toLowerCase()}@${engine.config.exchange.testnet ? "testnet" : "mainnet"}`;
      const holder = seen.get(key);
      if (holder) {
        throw new Error(
          `账户 ${holder} 与 ${engine.name} 指向同一地址与环境（${key}）——` +
            `单向持仓下两个引擎会互相强平、互撤保护单，拒绝启动`,
        );
      }
      seen.set(key, engine.name);
    }
  }

  start(): void {
    // 启动错峰：第 i 台引擎延迟 i×stagger 秒进入循环。两重目的：
    // ① 启动瞬间不对同一出口 IP 齐射 N 份查询（交易所限流按 IP 计）；
    // ② 网格间隔循环以启动时刻为锚，错峰让 N 个账户的周期相位永久错开。
    const staggerMs = Math.max(0, this.config.fleet.start_stagger_secs) * 1000;
    this.engines.forEach((engine, i) => engine.start(i * staggerMs));
    this.logger.printInfo(
      `🚁 大盘启动：${this.engines.length} 个账户并行（` +
        this.engines
          .map((e) => {
            const t = e.config.trading;
            const strategies = t.grid_enabled ? "网格" : "空转";
            return `${e.name}:${e.config.exchange.testnet ? "测试网" : "主网"}/${strategies}`;
          })
          .join("、") +
        "）",
    );
  }

  async stop(reason = "手动停止"): Promise<void> {
    // 并行停机：每台引擎自己等进行中的周期走完（互不共享账户，无顺序约束）
    await Promise.allSettled(this.engines.map((engine) => engine.stop(reason)));
  }

  /** 热重配：整体停 → 按新配置重建全部引擎 → 重启（各账户状态文件保留）。 */
  async applyConfig(config: RuntimeConfig): Promise<void> {
    this.logger.printSection("♻️ 大盘热重配");
    await this.stop("配置变更，热重配");
    this.build(config);
    this.start();
  }

  byName(name?: string | null): Engine | undefined {
    if (!name) return this.engines[0];
    return this.engines.find((e) => e.name === name);
  }

  // ── 聚合视图（大盘页数据源） ──────────────────────────────────────────

  /**
   * 账户名册：只读内存里已有的身份与运行状态，**不打交易所、不扫日志**。
   *
   * 看板侧栏每次渲染都要它，所以必须便宜；净值/收益那类要 I/O 的字段留给
   * overview()。两者分开的另一个理由是：名册在交易所查询失败时依然可用，
   * 「账户还在不在」与「账户值多少钱」是两个独立问题。
   */
  roster(): Dict[] {
    return this.engines.map((engine) => {
      const t = engine.config.trading;
      return {
        name: engine.name,
        running: engine.isRunning,
        started_at: engine.startedAt,
        testnet: engine.config.exchange.testnet,
        env: engine.config.exchange.testnet ? "测试网" : "主网",
        address: engine.client.address,
        strategies: {
          grid: t.grid_enabled,
          symbols: t.symbols,
          grid_symbol: t.grid_enabled ? t.symbols[0] : null,
        },
        llm: engine.llm.describe(),
        data_dir: engine.config.paths.data_dir,
        log_dir: engine.config.paths.log_dir,
      };
    });
  }

  /**
   * 账户收益口径（来自各账户自己的 JSONL——文件即单一事实来源）：
   * - realized_pnl_total / realized_pnl_today：trades 里带 pnl 的记录求和
   *   （GRID_ROUND_TRIP / GRID_NET_CLOSE / GRID_FORCED_REDUCE 等归因记录）；
   * - equity / available / unrealized_pnl：实时查询（失败如实标 query_failed）；
   * - equity_history：equity 快照序列（画每账户净值曲线）。
   */
  async accountSummary(engine: Engine): Promise<Dict> {
    const balance = await engine.orderManager.getAvailableBalanceInfo();
    const positions = await engine.orderManager.getCurrentPositions();
    const trades = engine.logger.readRecentJsonl("trades", 5000);
    const today = clock.date().toISOString().slice(0, 10);
    let realizedTotal = 0;
    let realizedToday = 0;
    let tradesToday = 0;
    for (const t of trades) {
      const pnl = Number(t.pnl);
      const isToday = String(t.timestamp ?? "").slice(0, 10) === today;
      if (isToday) tradesToday += 1;
      if (Number.isFinite(pnl) && t.pnl !== null) {
        realizedTotal += pnl;
        if (isToday) realizedToday += pnl;
      }
    }
    const equitySeries = engine.logger
      .readRecentJsonl("equity", 300)
      .map((e) => ({ t: e.timestamp, equity: Number(e.equity) }))
      .reverse();
    const t = engine.config.trading;
    return {
      name: engine.name,
      running: engine.isRunning,
      testnet: engine.config.exchange.testnet,
      env: engine.config.exchange.testnet ? "测试网" : "主网",
      address: engine.client.address,
      strategies: {
        grid: t.grid_enabled,
        symbols: t.symbols,
        grid_symbol: t.grid_enabled ? t.symbols[0] : null,
      },
      llm: engine.llm.describe(),
      balance_status: balance.status,
      equity: Number(balance.total ?? 0) || 0,
      available: Number(balance.available ?? 0) || 0,
      unrealized_pnl: Number(balance.unrealized_pnl ?? 0) || 0,
      positions_count: positions === null ? null : positions.length,
      positions_query_failed: positions === null,
      realized_pnl_total: realizedTotal,
      realized_pnl_today: realizedToday,
      trades_today: tradesToday,
      equity_history: equitySeries,
      log_dir: engine.config.paths.log_dir,
      data_dir: engine.config.paths.data_dir,
    };
  }

  /** 大盘总览：全部账户摘要 + 汇总行。 */
  async overview(): Promise<Dict> {
    const accounts = await Promise.all(this.engines.map((e) => this.accountSummary(e)));
    const sum = (key: string) => accounts.reduce((acc, a) => acc + (Number(a[key]) || 0), 0);
    return {
      accounts,
      totals: {
        count: accounts.length,
        running: accounts.filter((a) => a.running).length,
        equity: sum("equity"),
        available: sum("available"),
        unrealized_pnl: sum("unrealized_pnl"),
        realized_pnl_total: sum("realized_pnl_total"),
        realized_pnl_today: sum("realized_pnl_today"),
        testnet_count: accounts.filter((a) => a.testnet).length,
        mainnet_count: accounts.filter((a) => !a.testnet).length,
      },
    };
  }
}

/** 目录探针（启动前确认 data/logs 可写，Docker 时代的权限坑在 dsh 形态同样存在）。 */
export function ensureWritableDirs(config: RuntimeConfig): void {
  const dirs = new Set<string>([config.paths.data_dir, config.paths.log_dir]);
  for (const account of config.accounts) {
    dirs.add(account.paths.data_dir);
    dirs.add(account.paths.log_dir);
  }
  for (const dir of dirs) {
    fs.mkdirSync(dir, { recursive: true });
    const probe = path.join(dir, `.write_probe_${process.pid}`);
    fs.writeFileSync(probe, "ok");
    fs.unlinkSync(probe);
  }
}

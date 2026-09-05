/**
 * 网格回测运行器：用生产同一套 Engine / GridStrategy / GridManager 跑历史 K 线。
 *
 * 唯一的替换是交易所客户端（SimulatedClient）与 LLM 后端（规则后端：每周期都
 * 说 UPDATE_GRID，由重建闸门决定真正的重建频率——线上 LLM 63% 的周期也这么说）。
 * 策略、风控、状态机、簿记全部走生产代码；改任何一个参数都能在几秒内拿到
 * 「费用 / 库存 / 强平 / 收益」的分解数字，而不是上线亏钱以后再补开关。
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  ConfigSchema,
  deepMerge,
  resolveRuntimeConfig,
  type EngineConfig,
  type QuantFlowConfigInput,
} from "../config.js";
import { Engine } from "../engine.js";
import { TradingLogger } from "../logger.js";
import { defaultPerpFeeRates, type FeeRates } from "../fees.js";
import type { LLMBackend } from "../llm.js";
import { SimulatedClient, type SimAsset, type SimStats } from "./simulatedClient.js";
import type { Bar, FundingRow } from "./dataset.js";

/** 规则 LLM 后端：稳定输出 UPDATE_GRID（宽度交给市场数据），格数固定。 */
export class RuleGridLlmBackend implements LLMBackend {
  calls = 0;
  constructor(private readonly gridNum = 8, private readonly action: "UPDATE_GRID" | "KEEP_GRID" = "UPDATE_GRID") {}
  describe(): string {
    return `rule-grid(${this.action}, grid_num=${this.gridNum})`;
  }
  async chatOnce(): Promise<string> {
    this.calls += 1;
    return JSON.stringify({ action: this.action, mode: "NEUTRAL", grid_num: this.gridNum, confidence: 0.7, reason: "规则后端" });
  }
}

interface BacktestOptions {
  symbol: string;
  bars: Bar[];
  intervalMs?: number;
  funding?: FundingRow[];
  /** 插件配置覆盖（经 Schema 校验；缺省全默认值） */
  config?: Record<string, unknown>;
  initialEquity?: number;
  feeRates?: FeeRates;
  /** 预热根数：之前的 K 线只喂指标，不交易（默认 300） */
  warmupBars?: number;
  slippageBps?: number;
  /** 规则后端的格数 */
  gridNum?: number;
  llmBackend?: LLMBackend;
  /** 交易对元数据（szDecimals / maxLeverage / 维持保证金率）；缺省用内置表 */
  assets?: Record<string, SimAsset>;
  /** 触及即成交（乐观撮合），用于给结论做敏感性检验 */
  fillOnTouch?: boolean;
  /** 保留临时 data/logs 目录并返回路径（排障用） */
  keepArtifacts?: boolean;
  /** 每根 K 线后的回调（进度/自定义采样） */
  onBar?: (state: { index: number; equity: number; now: number }) => void;
}

interface BacktestResult {
  symbol: string;
  bars: number;
  days: number;
  cycles: number;
  initialEquity: number;
  finalEquity: number;
  returnPct: number;
  returnPctPerDay: number;
  maxDrawdownPct: number;
  realizedPnl: number;
  unrealizedPnl: number;
  fees: { maker: number; taker: number; total: number; pctOfInitial: number };
  fundingPaid: number;
  fills: { maker: number; taker: number; takerRatio: number; volume: number; roundTrips: number };
  orders: { placed: number; canceled: number; postOnlyRejections: number };
  forcedCloses: number;
  liquidations: number;
  rebuilds: number;
  maxInventoryNotional: number;
  endInventoryNotional: number;
  equityCurve: Array<{ t: number; equity: number }>;
  /** 与 equityCurve 同刻度的带符号持仓名义额（正=多，负=空）；跨标的对冲测算的输入 */
  inventoryCurve: Array<{ t: number; notional: number; price: number }>;
  /** 标的自身在同区间的涨跌幅（%），用来对照「什么都不做」 */
  benchmarkPct: number;
  /** 决策分布（规则后端产出的 action 计数） */
  decisions: Record<string, number>;
  /** trades 日志按动作分解：条数与盈亏合计（GRID_NET_CLOSE 为链上口径的净额归因） */
  tradeActions: Record<string, { count: number; pnl: number }>;
  /** main.log 里关键事件计数（趋势暂停 / 手术式减仓 / 屏障 / 库存上限 / post-only 拒单） */
  events: Record<string, number>;
  artifactsDir: string | null;
  stats: SimStats;
}

const SILENT_HOST = { info() {}, warn() {}, error() {} };

/** 把用户覆盖合成为经 Schema 校验的引擎配置（单账户；交易所段固定为模拟）。 */
export function buildEngineConfig(
  overrides: Record<string, unknown>,
  paths: { data_dir: string; log_dir: string },
): EngineConfig {
  const base: Record<string, unknown> = {
    trading: {
      symbols: ["BTC"],
      grid_enabled: true,
      run_immediately: true,
    },
    llm: { provider: "openai" },
    web: { enabled: false },
  };
  const validated = new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)(deepMerge(base, overrides));
  const runtime = resolveRuntimeConfig(validated, { private_key: "sim", account_address: null, testnet: true });
  return { ...runtime.accounts[0], paths };
}

/** 运行一次网格回测。 */
export async function runBacktest(options: BacktestOptions): Promise<BacktestResult> {
  const initialEquity = options.initialEquity ?? 1000;
  const warmup = Math.min(options.warmupBars ?? 300, Math.max(0, options.bars.length - 2));
  const feeRates = options.feeRates ?? defaultPerpFeeRates();
  const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "quantflow-backtest-"));
  const paths = { data_dir: path.join(artifacts, "data"), log_dir: path.join(artifacts, "logs") };
  fs.mkdirSync(paths.data_dir, { recursive: true });

  const overrides = deepMerge({ trading: { symbols: [options.symbol] } }, options.config ?? {});
  const config = buildEngineConfig(overrides, paths);

  const sim = new SimulatedClient({
    symbol: options.symbol,
    bars: options.bars,
    intervalMs: options.intervalMs,
    initialEquity,
    feeRates,
    funding: options.funding,
    slippageBps: options.slippageBps,
    assets: options.assets,
    fillOnTouch: options.fillOnTouch,
    startIndex: warmup,
    defaultLeverage: config.trading.max_leverage,
  });
  const restore = sim.install();
  const logger = new TradingLogger({ logDir: paths.log_dir, host: SILENT_HOST });
  const equityCurve: Array<{ t: number; equity: number }> = [];
  const inventoryCurve: Array<{ t: number; notional: number; price: number }> = [];
  let peak = initialEquity;
  let maxDrawdown = 0;
  let maxInventory = 0;
  let cycles = 0;

  const backend = options.llmBackend ?? new RuleGridLlmBackend(options.gridNum ?? 8);

  try {
    const engine = new Engine({
      config,
      logger,
      client: sim,
      llmBackend: backend,
      manualMonitorTick: true,
    });
    await engine.loadFeeRates();
    const grid = engine.gridStrategy;
    if (!grid) throw new Error("回测要求 grid_enabled=true");

    // 网格按固定间隔推进，与生产的网格循环同构
    const cycleMs = Math.max(1, config.grid.interval_minutes) * 60e3;
    let nextCycle = Math.ceil(sim.now / cycleMs) * cycleMs;
    let lastSnapshotHour = -1;
    equityCurve.push({ t: sim.now, equity: initialEquity });

    while (sim.advance()) {
      // 层级触发单开启时，成交监控在每根 K 线后驱动一次
      if (engine.orderManager.limitOrderMonitor) await engine.orderManager.limitOrderMonitor.runOnce();
      if (sim.now >= nextCycle) {
        await grid.runCycle();
        cycles += 1;
        nextCycle += cycleMs;
        while (nextCycle <= sim.now) nextCycle += cycleMs;
      }
      const equity = sim.equity();
      peak = Math.max(peak, equity);
      maxDrawdown = Math.max(maxDrawdown, peak > 0 ? (peak - equity) / peak : 0);
      maxInventory = Math.max(maxInventory, sim.positionOf().notional);
      const hour = Math.floor(sim.now / 3600e3);
      if (hour !== lastSnapshotHour) {
        equityCurve.push({ t: sim.now, equity });
        const pos = sim.positionOf();
        inventoryCurve.push({ t: sim.now, notional: Math.sign(pos.szi) * pos.notional, price: sim.mid });
        lastSnapshotHour = hour;
      }
      options.onBar?.({ index: sim.cursor, equity, now: sim.now });
      if (sim.liquidated) break;
    }
    equityCurve.push({ t: sim.now, equity: sim.equity() });
  } finally {
    restore();
  }

  const finalEquity = sim.equity();
  const stats = sim.stats;
  const bars = options.bars.length - warmup;
  const days = Math.max(1e-9, (bars * sim.intervalMs) / 86400e3);
  const fillsRaw = (await sim.userFills()) as Array<{ closedPnl: string }>;
  const roundTrips = fillsRaw.filter((f) => Number(f.closedPnl) !== 0).length;
  const mainLog = path.join(paths.log_dir, "main.log");
  const rebuilds = countLogMatches(mainLog, "全量重建原因");
  const events: Record<string, number> = {
    trend_pause: countLogMatches(mainLog, "检测到强趋势"),
    surgical_reduce: countLogMatches(mainLog, "手术式减仓完成"),
    barrier: countLogMatches(mainLog, "Triple Barrier 触发"),
    inventory_cap: countLogMatches(mainLog, "库存达上限") + countLogMatches(mainLog, "额度耗尽"),
    post_only_reject: countLogMatches(mainLog, "post-only 拒绝") + countLogMatches(mainLog, "post-only 限价单被拒"),
    netted_reset: countLogMatches(mainLog, "库存已被净额对冲"),
    protection: countLogMatches(mainLog, "保护插件", "触发:"),
    insufficient_capital: countLogMatches(mainLog, "资金不足拒绝布单"),
  };
  const tradeActions = summarizeTrades(path.join(paths.log_dir, "trades"));
  // 必须在删临时目录之前读：decisions 与 trades 都落在 log_dir 下
  const decisions = readDecisionHistogram(paths.log_dir);
  const unrealized = finalEquity - sim.cash;
  const totalFees = stats.makerFees + stats.takerFees;

  if (!options.keepArtifacts) fs.rmSync(artifacts, { recursive: true, force: true });

  return {
    symbol: options.symbol,
    bars,
    days,
    cycles,
    initialEquity,
    finalEquity,
    returnPct: (finalEquity / initialEquity - 1) * 100,
    returnPctPerDay: ((finalEquity / initialEquity - 1) * 100) / days,
    maxDrawdownPct: maxDrawdown * 100,
    realizedPnl: stats.realizedPnl,
    unrealizedPnl: unrealized,
    fees: { maker: stats.makerFees, taker: stats.takerFees, total: totalFees, pctOfInitial: (totalFees / initialEquity) * 100 },
    fundingPaid: stats.fundingPaid,
    fills: {
      maker: stats.makerFills,
      taker: stats.takerFills,
      takerRatio: stats.makerFills + stats.takerFills ? stats.takerFills / (stats.makerFills + stats.takerFills) : 0,
      volume: stats.volume,
      roundTrips,
    },
    orders: { placed: stats.ordersPlaced, canceled: stats.ordersCanceled, postOnlyRejections: stats.postOnlyRejections },
    forcedCloses: stats.forcedCloses,
    liquidations: stats.liquidations,
    rebuilds,
    maxInventoryNotional: maxInventory,
    endInventoryNotional: sim.positionOf().notional,
    equityCurve,
    inventoryCurve,
    benchmarkPct: (options.bars[options.bars.length - 1].c / options.bars[warmup].c - 1) * 100,
    decisions,
    tradeActions,
    events,
    artifactsDir: options.keepArtifacts ? artifacts : null,
    stats: { ...stats },
  };
}

/** 汇总 trades JSONL：按 action 计数与 pnl 合计。 */
function summarizeTrades(dir: string): Record<string, { count: number; pnl: number }> {
  const out: Record<string, { count: number; pnl: number }> = {};
  let files: string[] = [];
  try {
    files = fs.readdirSync(dir).filter((f) => f.endsWith(".jsonl"));
  } catch {
    return out;
  }
  for (const f of files) {
    for (const line of fs.readFileSync(path.join(dir, f), "utf-8").split("\n")) {
      if (!line.trim()) continue;
      try {
        const row = JSON.parse(line) as { action?: string; pnl?: number | null };
        const key = String(row.action ?? "?");
        const entry = (out[key] ??= { count: 0, pnl: 0 });
        entry.count += 1;
        if (typeof row.pnl === "number" && Number.isFinite(row.pnl)) entry.pnl += row.pnl;
      } catch {
        /* 跳过损坏行 */
      }
    }
  }
  return out;
}

/** 从 decisions JSONL 统计 action 分布（决策审计口径）。 */
function readDecisionHistogram(logDir: string): Record<string, number> {
  const out: Record<string, number> = {};
  const dir = path.join(logDir, "decisions");
  let files: string[] = [];
  try {
    files = fs.readdirSync(dir).filter((f) => f.endsWith(".jsonl"));
  } catch {
    return out;
  }
  for (const f of files) {
    for (const line of fs.readFileSync(path.join(dir, f), "utf-8").split("\n")) {
      if (!line.trim()) continue;
      try {
        const key = String((JSON.parse(line) as { decision?: string }).decision ?? "?");
        out[key] = (out[key] ?? 0) + 1;
      } catch {
        /* 跳过损坏行 */
      }
    }
  }
  return out;
}

function countLogMatches(file: string, needle: string, also?: string): number {
  try {
    return fs.readFileSync(file, "utf-8").split("\n").filter((l) => l.includes(needle) && (!also || l.includes(also))).length;
  } catch {
    return 0;
  }
}

/** 触发单退出预设：对照组配置（层级触发单、Gtc、4h 时限屏障、按 max_trade_amount 定预算）。 */
export const TRIGGER_EXIT_PRESET: Record<string, unknown> = {
  grid: {
    post_only: false,
    level_trigger_stop_loss: true,
    level_trigger_take_profit: true,
    capital_ratio: 0,
    inventory_cap_ratio: 0,
    trend_side_only: false,
    barrier: { time_limit_seconds: 14400, trailing_stop_activation_pct: 0.03, trailing_stop_delta_pct: 0.01 },
  },
};

/** 简明文本报告（CLI 用）。 */
export function formatReport(r: BacktestResult, title = ""): string {
  const f = (n: number, d = 2) => (Number.isFinite(n) ? n.toFixed(d) : "n/a");
  const lines = [
    title ? `== ${title} ==` : "",
    `区间: ${f(r.days, 1)} 天 / ${r.bars} 根 / ${r.cycles} 个周期 / 重建 ${r.rebuilds} 次`,
    `权益: $${f(r.initialEquity)} → $${f(r.finalEquity)}  (${r.returnPct >= 0 ? "+" : ""}${f(r.returnPct)}%, 日均 ${f(r.returnPctPerDay, 3)}%)  最大回撤 ${f(r.maxDrawdownPct)}%`,
    `盈亏: 已实现 ${f(r.realizedPnl)}  未实现 ${f(r.unrealizedPnl)}  资金费 -${f(r.fundingPaid)}`,
    `费用: maker $${f(r.fees.maker)} + taker $${f(r.fees.taker)} = $${f(r.fees.total)} (${f(r.fees.pctOfInitial)}% 初始权益)`,
    `成交: maker ${r.fills.maker} / taker ${r.fills.taker} (taker 占比 ${f(r.fills.takerRatio * 100, 1)}%)  带盈亏平仓 ${r.fills.roundTrips} 笔  成交额 $${f(r.fills.volume, 0)}`,
    `订单: 提交 ${r.orders.placed} / 撤销 ${r.orders.canceled} / post-only 拒 ${r.orders.postOnlyRejections}  强平/紧急平仓 ${r.forcedCloses}  爆仓 ${r.liquidations}`,
    `库存: 峰值名义额 $${f(r.maxInventoryNotional, 0)}  期末 $${f(r.endInventoryNotional, 0)}`,
    `基准: 标的同期 ${r.benchmarkPct >= 0 ? "+" : ""}${f(r.benchmarkPct)}%（买入持有对照）`,
    `决策: ${Object.entries(r.decisions).map(([k, v]) => `${k}×${v}`).join("  ") || "无"}`,
    `事件: ${Object.entries(r.events).filter(([, v]) => v > 0).map(([k, v]) => `${k}=${v}`).join("  ") || "无"}`,
    `动作: ${Object.entries(r.tradeActions).map(([k, v]) => `${k}×${v.count}${v.pnl ? `(${v.pnl >= 0 ? "+" : ""}${f(v.pnl)})` : ""}`).join("  ") || "无"}`,
  ];
  return lines.filter(Boolean).join("\n");
}

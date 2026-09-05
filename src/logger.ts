/**
 * 日志工具：控制台/宿主日志 + 文件日志与结构化 JSONL 记录。
 *
 * 三类输出，各司其职：
 * - `logs/main.log`            运行日志（人读；同时透传给宿主 dsh 的 logger）
 * - `logs/decisions/*.jsonl`   决策记录（含 prompt/回复/执行细节，事后审计）
 * - `logs/trades/*.jsonl`      成交记录（含 pnl/reason 归因，用 jq 直接分析）
 * - `logs/equity/*.jsonl`      净值快照（画净值曲线）
 *
 * JSONL 写入失败只告警不抛出——日志永远不能拖垮交易主流程。
 * 看板的决策/成交/净值接口直接读这些 JSONL：文件即 API 的单一事实来源。
 */

import fs from "node:fs";
import path from "node:path";
import { Decimal } from "./utils/precision.js";
import { clock } from "./utils/clock.js";

// main.log 轮转参数：单文件 50MB × 3 备份（线上曾累积 364MB 单文件无轮转）
const LOG_MAX_BYTES = 50 * 1024 * 1024;
const LOG_BACKUP_COUNT = 3;

export interface HostLogger {
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
}

/** JSONL 序列化兜底：Decimal/Date 安全降级。 */
function jsonDefault(_key: string, value: unknown): unknown {
  if (value instanceof Decimal) return value.toNumber();
  return value;
}

function day(date = clock.date()): string {
  return `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
}

function ts(): string {
  return clock.date().toISOString();
}

/** 交易日志器：运行日志 + 决策/成交/净值三路结构化记录。 */
export class TradingLogger {
  readonly logDir: string;
  readonly decisionsDir: string;
  readonly tradesDir: string;
  readonly equityDir: string;
  private host: HostLogger;

  constructor(options: { logDir?: string; host?: HostLogger } = {}) {
    this.logDir = options.logDir ?? "logs";
    this.decisionsDir = path.join(this.logDir, "decisions");
    this.tradesDir = path.join(this.logDir, "trades");
    this.equityDir = path.join(this.logDir, "equity");
    for (const d of [this.decisionsDir, this.tradesDir, this.equityDir]) {
      fs.mkdirSync(d, { recursive: true });
    }
    this.host = options.host ?? console;
  }

  private write(level: "INFO" | "WARNING" | "ERROR", message: string): void {
    const line = `${ts()} | ${level.padEnd(7)} | ${message}`;
    try {
      const file = path.join(this.logDir, "main.log");
      // 轮转：超限时 main.log → main.log.1 → ... → main.log.N（丢弃最旧）
      try {
        const stat = fs.statSync(file);
        if (stat.size >= LOG_MAX_BYTES) {
          for (let i = LOG_BACKUP_COUNT - 1; i >= 1; i--) {
            const src = `${file}.${i}`;
            if (fs.existsSync(src)) fs.renameSync(src, `${file}.${i + 1}`);
          }
          fs.renameSync(file, `${file}.1`);
        }
      } catch {
        // 文件不存在等：忽略
      }
      fs.appendFileSync(file, line + "\n", "utf-8");
    } catch {
      // 文件日志失败不拖垮主流程
    }
    if (level === "ERROR") this.host.error(message);
    else if (level === "WARNING") this.host.warn(message);
    else this.host.info(message);
  }

  // ── 运行日志 ──────────────────────────────────────────────────────────

  /** 周期级标题。 */
  printHeader(text: string): void {
    this.write("INFO", "═".repeat(8) + " " + text);
  }

  /** 小节标题（style 参数仅为兼容旧调用，无渲染含义）。 */
  printSection(title: string, content?: string, _style = ""): void {
    this.write("INFO", "── " + title);
    if (content) this.write("INFO", content);
  }

  printInfo(message: string): void {
    this.write("INFO", message);
  }

  printWarning(message: string): void {
    this.write("WARNING", message);
  }

  printError(message: string): void {
    this.write("ERROR", message);
  }

  /** 严重级：资金安全告警，必须落盘 main.log。 */
  printCritical(message: string): void {
    this.write("ERROR", "【严重】" + message);
  }

  /** 行情摘要一行输出。 */
  printMarketData(symbol: string, data: Record<string, unknown>): void {
    const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : Number(v) || 0);
    this.write(
      "INFO",
      `[${symbol}] 价格 ${num(data.current_price).toFixed(4)} | ` +
        `RSI ${num(data.rsi).toFixed(1)} | ` +
        `MACD ${num(data.macd_hist).toFixed(5)} | ` +
        `量变 ${num(data.volume_change).toFixed(1)}%`,
    );
  }

  // ── 结构化记录 ────────────────────────────────────────────────────────

  /**
   * 记录一次决策（含 prompt 与 AI 原始回复，供事后审计与看板展示）。
   *
   * strategy 字段标记决策来源（现只有 "grid"；历史日志里的 "perp" 来自已移除的
   * 永续策略，读取端原样展示）。
   */
  logDecision(entry: {
    symbol: string;
    marketData: Record<string, unknown>;
    prompt: string;
    aiResponse: string;
    decision: string;
    actionDetails?: Record<string, unknown> | null;
    status?: string;
    errorMessage?: string | null;
    confidence?: number;
    strategy?: "grid";
  }): void {
    this.appendJsonl(path.join(this.decisionsDir, `decisions_${day()}.jsonl`), {
      timestamp: ts(),
      symbol: entry.symbol,
      strategy: entry.strategy,
      decision: entry.decision,
      confidence: entry.confidence ?? 0,
      status: entry.status ?? "SUCCESS",
      error_message: entry.errorMessage ?? null,
      market_data: entry.marketData,
      prompt: entry.prompt,
      ai_response: entry.aiResponse,
      action_details: entry.actionDetails ?? {},
    });
  }

  /** 记录一笔成交（reason 为盈亏归因标签，如 GRID_TP / Triple Barrier）。 */
  logTrade(entry: {
    symbol: string;
    action: string;
    amount: number;
    price: number;
    orderId: string;
    takeProfitPrice?: number | null;
    stopLossPrice?: number | null;
    status?: string;
    pnl?: number | null;
    reason?: string | null;
    /** 该笔成交的手续费（USD，负数=返佣） */
    fee?: number | null;
    /** true=taker（吃单）成交；false=maker；null=未知。归因脚本据此拆分费用来源 */
    crossed?: boolean | null;
  }): void {
    this.appendJsonl(path.join(this.tradesDir, `trades_${day()}.jsonl`), {
      timestamp: ts(),
      symbol: entry.symbol,
      action: entry.action,
      amount: entry.amount,
      price: entry.price,
      order_id: entry.orderId,
      take_profit_price: entry.takeProfitPrice ?? null,
      stop_loss_price: entry.stopLossPrice ?? null,
      status: entry.status ?? "FILLED",
      pnl: entry.pnl ?? null,
      reason: entry.reason ?? null,
      fee: entry.fee ?? null,
      crossed: entry.crossed ?? null,
    });
    this.write("INFO", `交易记录: ${entry.action} ${entry.amount} ${entry.symbol} @ ${entry.price}`);
  }

  /** 记录净值快照（每周期一行，每天一个文件）。 */
  logEquitySnapshot(entry: {
    equity: number;
    available: number;
    unrealizedPnl?: number;
    positionNotional?: number;
    symbol?: string;
  }): void {
    this.appendJsonl(path.join(this.equityDir, `equity_${day()}.jsonl`), {
      timestamp: ts(),
      equity: Number(entry.equity),
      available: Number(entry.available),
      unrealized_pnl: Number(entry.unrealizedPnl ?? 0),
      position_notional: Number(entry.positionNotional ?? 0),
      symbol: entry.symbol ?? "",
    });
  }

  private appendJsonl(file: string, entry: Record<string, unknown>): void {
    try {
      fs.appendFileSync(file, JSON.stringify(entry, jsonDefault) + "\n", "utf-8");
    } catch (e) {
      this.write("WARNING", `结构化日志写入失败 ${path.basename(file)}: ${e}`);
    }
  }

  /**
   * 读取最近 N 条结构化记录（看板 API 用）：按日期文件倒序扫描，跨天拼接。
   * 解析失败的行静默跳过——JSONL 是追加型日志，个别损坏行不该让整页失败。
   */
  readRecentJsonl(kind: "decisions" | "trades" | "equity", limit: number): Record<string, unknown>[] {
    const dir = kind === "decisions" ? this.decisionsDir : kind === "trades" ? this.tradesDir : this.equityDir;
    const out: Record<string, unknown>[] = [];
    let files: string[] = [];
    try {
      files = fs.readdirSync(dir).filter((f) => f.endsWith(".jsonl")).sort().reverse();
    } catch {
      return out;
    }
    for (const f of files) {
      if (out.length >= limit) break;
      let lines: string[] = [];
      try {
        lines = fs.readFileSync(path.join(dir, f), "utf-8").split("\n");
      } catch {
        continue;
      }
      const parsed: Record<string, unknown>[] = [];
      for (const line of lines) {
        const t = line.trim();
        if (!t) continue;
        try {
          parsed.push(JSON.parse(t));
        } catch {
          // 跳过损坏行
        }
      }
      // 文件内正序 → 取末尾；输出整体保持「新在前」
      out.push(...parsed.reverse());
    }
    return out.slice(0, limit);
  }
}

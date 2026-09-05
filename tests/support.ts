/**
 * 测试共享桩。
 *
 * 测试禁网络与真实密钥：LLM 用 FakeLLM 后端，交易所用 FakeGridClient /
 * FakeOrderManager；日志写入临时目录，避免测试运行污染仓库 logs/。
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { TradingLogger } from "../src/logger.js";
import { LLMClient, LLMError, type LLMBackend } from "../src/llm.js";
import { HyperliquidClient, type Dict } from "../src/trading/client.js";
import type { Candle } from "../src/data/indicators.js";

/** 静默宿主：吞掉 console 输出，测试日志只进临时目录文件。 */
const silentHost = { info() {}, warn() {}, error() {} };

export function makeTempDir(prefix = "quantflow-test-"): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

/** 模块级共享的测试日志器：写入临时目录。 */
export const QUIET_LOGGER = new TradingLogger({
  logDir: makeTempDir("quantflow-test-logs-"),
  host: silentHost,
});

export function makeQuietLogger(): TradingLogger {
  return new TradingLogger({ logDir: makeTempDir(), host: silentHost });
}

/** LLM 测试桩：按序返回预置回复，或抛出预置异常。 */
export class FakeLLMBackend implements LLMBackend {
  calls: Array<[string, string, number]> = [];
  constructor(
    public replies: string[] = [],
    public error: Error | null = null,
  ) {}
  describe(): string {
    return "fake-llm";
  }
  async chatOnce(system: string, user: string, temperature: number): Promise<string> {
    this.calls.push([system, user, temperature]);
    if (this.error) throw this.error;
    if (!this.replies.length) throw new LLMError("FakeLLM 无预置回复");
    return this.replies.length > 1 ? this.replies.shift()! : this.replies[0];
  }
}

export function makeFakeLLM(replies: string[] = [], error: Error | null = null): {
  llm: LLMClient;
  backend: FakeLLMBackend;
} {
  const backend = new FakeLLMBackend(replies, error);
  // maxRetries=1：桩场景无需真实退避重试拖慢测试
  return { llm: new LLMClient({ backend, model: "fake", maxRetries: 1, backoffScale: 0 }), backend };
}

export const OK_ORDER: Dict = {
  status: "ok",
  response: { type: "order", data: { statuses: [{ resting: { oid: 900 } }] } },
};
export const FILLED_ORDER: Dict = {
  status: "ok",
  response: { type: "order", data: { statuses: [{ filled: { avgPx: "100.0", totalSz: "0.5", oid: 901 } }] } },
};
export const REJECTED_ORDER: Dict = {
  status: "ok",
  response: { type: "order", data: { statuses: [{ error: "Insufficient margin" }] } },
};
// Hyperliquid 对失去持仓支撑的 reduce_only 单的真实拒单文案（净额对冲后的幻影层级场景）
export const RO_NETTED_REJECTED_ORDER: Dict = {
  status: "ok",
  response: {
    type: "order",
    data: { statuses: [{ error: "Reduce only order would increase position. asset=4" }] },
  },
};

/** HyperliquidClient 桩：行为可配置，校验逻辑复用真实实现。 */
export class FakeGridClient {
  address = "0xtest";
  openOrders: Dict[] | null = [];
  positions: Dict[] | null = [];
  price: number | null = 100.0;
  fills: Dict[] | null = [];
  closeResults: Dict[] = [];
  closeCalls: Array<[string, number | null]> = [];
  emergencyResults: Array<[boolean, Dict | null]> = [];
  emergencyCalls: Array<[string, number | null, string]> = [];
  cancelCalls: number[] = [];
  limitOrders: Dict[] = [];
  limitOrderResults: Dict[] = [];

  // 校验逻辑复用真实实现，保证内层 statuses 校验语义与线上一致
  static checkOrderSuccess = HyperliquidClient.checkOrderSuccess;
  static getOrderFillInfo = HyperliquidClient.getOrderFillInfo;

  async userFills(): Promise<Dict[]> {
    if (this.fills === null) throw new Error("fills 接口故障");
    return [...this.fills];
  }
  async getCurrentPrice(_symbol: string): Promise<number | null> {
    return this.price;
  }
  async getAssetInfo(_symbol: string): Promise<Dict> {
    return { szDecimals: 3 };
  }
  async formatPrice(_symbol: string, price: number): Promise<number> {
    return Math.round(Number(price) * 1000) / 1000;
  }
  async roundSize(_symbol: string, size: number): Promise<number> {
    return Math.floor(size * 1000) / 1000;
  }
  async getOpenOrders(_includeTrigger = false): Promise<Dict[] | null> {
    return this.openOrders === null ? null : [...this.openOrders];
  }
  async getPositions(): Promise<Dict[] | null> {
    return this.positions === null ? null : [...this.positions];
  }
  async cancelOrder(_symbol: string, oid: number): Promise<Dict> {
    this.cancelCalls.push(oid);
    return { status: "ok" };
  }
  async closePosition(symbol: string, size: number | null = null): Promise<Dict> {
    this.closeCalls.push([symbol, size]);
    return this.closeResults.length ? this.closeResults.shift()! : REJECTED_ORDER;
  }
  async emergencyCloseWithRetry(
    symbol: string,
    size: number | null,
    options: { reason: string; maxRetries?: number },
  ): Promise<[boolean, Dict | null]> {
    this.emergencyCalls.push([symbol, size, options.reason]);
    if (this.emergencyResults.length) return this.emergencyResults.shift()!;
    return [false, { status: "error", message: "桩默认失败" }];
  }
  async placeLimitOrder(symbol: string, isBuy: boolean, size: number, price: number, reduceOnly = false): Promise<Dict> {
    this.limitOrders.push({ symbol, is_buy: isBuy, size, price, ro: reduceOnly });
    return this.limitOrderResults.length ? this.limitOrderResults.shift()! : OK_ORDER;
  }
}
// 把真实静态校验函数挂到实例可达处（GridManager 调 HyperliquidClient.checkOrderSuccess 静态方法，天然可用）

/** 订单管理器测试桩（网格用）：记录调用，返回可配置结果。 */
export class FakeGridOrderManager {
  longLimits: Array<[string, number, number]> = [];
  shortLimits: Array<[string, number, number]> = [];
  constructor(public client: FakeGridClient) {}
  async getCurrentPositions(): Promise<Dict[] | null> {
    return this.client.getPositions();
  }
  async getAvailableBalanceInfo(): Promise<Dict> {
    return { status: "ok", available: 1000, total: 1000, equity: 1000, unrealized_pnl: 0, occupied: 0 };
  }
  async executeLongLimit(symbol: string, amount: number, price: number, _options: Dict = {}): Promise<Dict> {
    this.longLimits.push([symbol, amount, price]);
    return { success: true, limit_order: OK_ORDER };
  }
  async executeShortLimit(symbol: string, amount: number, price: number, _options: Dict = {}): Promise<Dict> {
    this.shortLimits.push([symbol, amount, price]);
    return { success: true, limit_order: OK_ORDER };
  }
}

/** 订单管理器测试桩（网格策略与 GridAgent 用：只提供余额与持仓查询）。 */
export class FakeOrderManager {
  balanceOk = true;
  constructor(
    public available = 1000.0,
    public positions: Dict[] = [],
  ) {}
  async getAvailableBalanceInfo(): Promise<Dict> {
    if (!this.balanceOk) return { status: "error", message: "网络错误" };
    return {
      status: "ok",
      available: this.available,
      total: this.available,
      equity: this.available,
      unrealized_pnl: 0,
      occupied: 0,
    };
  }
  async getCurrentPositions(): Promise<Dict[] | null> {
    return [...this.positions];
  }
}

/** 构造线性上行的合成 OHLCV 数据（足够计算全部指标）。 */
export function makeOhlcv(rows = 100, startPrice = 100.0, step = 0.5): Candle[] {
  const out: Candle[] = [];
  const base = Date.parse("2026-01-01T00:00:00Z");
  for (let i = 0; i < rows; i++) {
    const close = startPrice + i * step;
    out.push({
      timestamp: base + i * 3600_000,
      open: close - 0.2,
      high: close + 0.5,
      low: close - 0.5,
      close,
      volume: 1000 + i,
    });
  }
  return out;
}

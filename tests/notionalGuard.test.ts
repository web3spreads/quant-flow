/**
 * 主网名义额闸测试：这道闸是主网与测试网之间除环境变量外唯一的代码级隔离。
 * 守三条线：① 超限时开仓被拒、退出通道永不受阻；② 查询失败 fail-closed；
 * ③ 引擎在主网自动套闸、测试网不套。
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MainnetNotionalGuard, usedNotionalUsd } from "../src/trading/notionalGuard.js";
import { HyperliquidClient, type Dict, type LimitOrderSpec } from "../src/trading/client.js";
import { Engine } from "../src/engine.js";
import { ConfigSchema, resolveRuntimeConfig, type EngineConfig, type QuantFlowConfigInput } from "../src/config.js";
import { FakeGridClient, OK_ORDER, makeQuietLogger, makeTempDir } from "./support.js";

/** 桩：记录批量下单调用并回放预置回执。 */
class RecordingClient extends FakeGridClient {
  batches: LimitOrderSpec[][] = [];
  batchResults: Dict[] = [];
  testnet = false;
  async placeLimitOrders(specs: LimitOrderSpec[]): Promise<Dict> {
    this.batches.push(specs);
    if (this.batchResults.length) return this.batchResults.shift()!;
    return { status: "ok", response: { type: "order", data: { statuses: specs.map((_, i) => ({ resting: { oid: 1000 + i } })) } } };
  }
  async getBalance(): Promise<Dict | null> {
    return { accountValue: 1000, totalMarginUsed: 0 };
  }
  async updateLeverage(): Promise<Dict> {
    return { status: "ok" };
  }
  async getCandles(): Promise<Dict[] | null> {
    return [];
  }
  async fetchUserFeeRates() {
    return { makerRate: 0.00015, takerRate: 0.00045 };
  }
  async placeTpslOrder(): Promise<Dict> {
    return OK_ORDER;
  }
  async cancelOrders(_s: string, oids: number[]): Promise<Dict> {
    return { status: "ok", response: { type: "cancel", data: { statuses: oids.map(() => "success") } } };
  }
}

const open = (price: number, size: number, isBuy = true): LimitOrderSpec => ({ symbol: "BTC", isBuy, size, price, reduceOnly: false, tif: "Alo" });
const ro = (price: number, size: number, isBuy = false): LimitOrderSpec => ({ symbol: "BTC", isBuy, size, price, reduceOnly: true, tif: "Alo" });

function makeGuard(capUsd: number, setup?: (c: RecordingClient) => void): { guard: MainnetNotionalGuard; inner: RecordingClient } {
  const inner = new RecordingClient();
  setup?.(inner);
  return { guard: new MainnetNotionalGuard(inner, { capUsd, logger: makeQuietLogger() }), inner };
}

describe("usedNotionalUsd", () => {
  it("持仓名义额 + 非 reduce_only 非触发单挂单；reduce_only 与触发单不计", () => {
    const positions = [{ coin: "BTC", szi: "-0.01", positionValue: "120.5" }, { coin: "ETH", szi: "1", entryPx: "30" }];
    const orders = [
      { coin: "BTC", limitPx: "100", sz: "0.5", reduceOnly: false },
      { coin: "BTC", limitPx: "100", sz: "9", reduceOnly: true },
      { coin: "BTC", limitPx: "100", sz: "9", orderType: { trigger: {} } },
      { coin: "BTC", limitPx: "100", sz: "9", isTrigger: true },
    ];
    expect(usedNotionalUsd(positions, orders)).toBeCloseTo(120.5 + 30 + 50);
    expect(usedNotionalUsd([], [])).toBe(0);
  });
});

describe("MainnetNotionalGuard.placeLimitOrders", () => {
  it("上限内放行并原样返回内层回执", async () => {
    const { guard, inner } = makeGuard(1000, (c) => {
      c.positions = [{ coin: "BTC", szi: "0.001", positionValue: "100" }];
      c.openOrders = [{ coin: "BTC", limitPx: "100", sz: "1", reduceOnly: false }];
    });
    const specs = [open(100, 2), ro(110, 1)];
    const receipt = await guard.placeLimitOrders(specs);
    expect(inner.batches).toEqual([specs]);
    expect(HyperliquidClient.orderStatuses(receipt, 2).every((v) => v.ok)).toBe(true);
    expect(guard.snapshot()).toMatchObject({ cap_usd: 1000, used_usd: 200, query_failed: false });
  });

  it("超限：开仓条目全部拒绝、reduce_only 条目照常提交，回执与条目顺序对齐", async () => {
    const { guard, inner } = makeGuard(300, (c) => {
      c.positions = [{ coin: "BTC", szi: "0.002", positionValue: "250" }];
    });
    const specs = [open(100, 0.3), ro(110, 0.1), open(90, 0.3, true)];
    const receipt = await guard.placeLimitOrders(specs);
    // 只有 reduce_only 那一单真正到达交易所
    expect(inner.batches).toEqual([[specs[1]]]);
    const views = HyperliquidClient.orderStatuses(receipt, 3);
    expect(views.map((v) => v.ok)).toEqual([false, true, false]);
    expect(views[0].error).toMatch(/主网名义额闸.*上限/);
    expect(views[1].oid).toBe(1000);
    // 单单入口同样受闸：内部走批量
    const single = await guard.placeLimitOrder("BTC", true, 1, 100, false, "Alo");
    expect(HyperliquidClient.checkOrderSuccess(single)[0]).toBe(false);
    expect(inner.batches.length).toBe(1);
  });

  it("全 reduce_only 批次即使超限也直通（退出通道永不受阻）", async () => {
    const { guard, inner } = makeGuard(10, (c) => {
      c.positions = [{ coin: "BTC", szi: "1", positionValue: "99999" }];
    });
    const specs = [ro(110, 1), ro(120, 1, true)];
    await guard.placeLimitOrders(specs);
    expect(inner.batches).toEqual([specs]);
  });

  it("持仓或挂单查询失败 → fail-closed 拒绝开仓，reduce_only 仍提交", async () => {
    for (const broken of ["positions", "openOrders"] as const) {
      const { guard, inner } = makeGuard(1000, (c) => {
        c[broken] = null;
      });
      const specs = [open(100, 0.1), ro(110, 0.1)];
      const receipt = await guard.placeLimitOrders(specs);
      const views = HyperliquidClient.orderStatuses(receipt, 2);
      expect(views[0].ok, broken).toBe(false);
      expect(views[0].error, broken).toMatch(/fail-closed/);
      expect(views[1].ok, broken).toBe(true);
      expect(inner.batches, broken).toEqual([[specs[1]]]);
      expect(guard.snapshot()?.query_failed, broken).toBe(true);
    }
  });

  it("平仓与紧急平仓透传（不查名义额、不拦）", async () => {
    const { guard, inner } = makeGuard(1, (c) => {
      c.positions = null; // 查询失败也不能挡住退出
      c.closeResults = [OK_ORDER];
      c.emergencyResults = [[true, OK_ORDER]];
    });
    expect((await guard.closePosition("BTC", null)).status).toBe("ok");
    expect((await guard.emergencyCloseWithRetry("BTC", null, { reason: "test" }))[0]).toBe(true);
    expect(inner.closeCalls.length).toBe(1);
    expect(inner.emergencyCalls.length).toBe(1);
    expect(inner.batches.length).toBe(0);
    expect(guard.address).toBe(inner.address);
    expect(guard.testnet).toBe(false);
  });

  it("上限必须 > 0", () => {
    expect(() => new MainnetNotionalGuard(new RecordingClient(), { capUsd: 0 })).toThrow(/> 0/);
  });
});

describe("引擎接线", () => {
  const ENV = ["QUANTFLOW_MAINNET_MAX_NOTIONAL_USD", "QUANTFLOW_MAINNET_ACK"];
  const saved: Record<string, string | undefined> = {};
  beforeEach(() => {
    for (const k of ENV) {
      saved[k] = process.env[k];
      delete process.env[k];
    }
  });
  afterEach(() => {
    for (const k of ENV) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  });

  /** 主网配置要过双重闸：上限 250，ACK 从拒绝信息里取指纹。 */
  function engineConfig(testnet: boolean): EngineConfig {
    const validated = new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)({
      trading: { run_immediately: false },
    });
    const exchange = { private_key: "0x" + "1".repeat(64), account_address: null, testnet, mainnet_max_notional_usd: 0 };
    if (!testnet) {
      process.env.QUANTFLOW_MAINNET_MAX_NOTIONAL_USD = "250";
      try {
        resolveRuntimeConfig(validated, exchange);
      } catch (e) {
        process.env.QUANTFLOW_MAINNET_ACK = /指纹 ([0-9a-f]{64})/.exec(String(e))![1];
      }
    }
    const runtime = resolveRuntimeConfig(validated, exchange);
    const dir = makeTempDir();
    return { ...runtime.accounts[0], paths: { data_dir: `${dir}/data`, log_dir: `${dir}/logs` } };
  }

  it("主网账户的客户端被名义额闸包住，测试网不包；规则后端默认不在回路", () => {
    const live = new Engine({ config: engineConfig(false), logger: makeQuietLogger(), client: new RecordingClient(), manualMonitorTick: true });
    expect(live.client).toBeInstanceOf(MainnetNotionalGuard);
    expect(live.notionalGuard?.capUsd).toBe(250);
    expect(live.llmInLoop).toBe(false);
    expect(live.llmUsage()).toBeNull();

    const sim = new Engine({ config: engineConfig(true), logger: makeQuietLogger(), client: new RecordingClient(), manualMonitorTick: true });
    expect(sim.client).toBeInstanceOf(RecordingClient);
    expect(sim.notionalGuard).toBeNull();
  });
});

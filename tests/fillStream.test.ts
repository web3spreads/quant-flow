/**
 * 成交事件流测试。
 *
 * 这条通道的价值是把「开仓成交 → 挂出平仓单」从一个网格周期压到亚秒级，风险是它绕过了
 * 周期的全部闸门。因此只守三条会赔钱的线：
 * ① 周期之外的同步永远 allowOpen=false（绝不新增敞口）；
 * ② 重复成交不重复触发，掉线/订阅失败只退回按周期确认、不拖垮引擎；
 * ③ 与网格周期共用交易锁，抢不到就让位，不并发动账户。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UserFillStream, type FillEvent, type FillSubscribe } from "../src/trading/fillStream.js";
import { Engine } from "../src/engine.js";
import { ConfigSchema, resolveRuntimeConfig, type EngineConfig, type QuantFlowConfigInput } from "../src/config.js";
import { FakeGridClient, makeQuietLogger, makeTempDir } from "./support.js";
import type { Dict } from "../src/trading/client.js";

/** 可编排的假订阅：测试自己推事件，全程无网络。 */
function makeFakeSubscribe(): {
  subscribe: FillSubscribe;
  emit: (event: FillEvent) => void;
  calls: Array<{ user: string; testnet: boolean }>;
  unsubscribed: number;
} {
  const state = {
    listener: null as ((e: FillEvent) => void) | null,
    calls: [] as Array<{ user: string; testnet: boolean }>,
    unsubscribed: 0,
  };
  return {
    calls: state.calls,
    get unsubscribed() {
      return state.unsubscribed;
    },
    emit: (event: FillEvent) => state.listener?.(event),
    subscribe: async (params, listener) => {
      state.calls.push(params);
      state.listener = listener;
      return {
        async unsubscribe() {
          state.unsubscribed += 1;
          state.listener = null;
        },
      };
    },
  };
}

const fill = (tid: number, coin = "BTC") => ({ coin, tid, oid: tid * 10, time: 1_700_000_000_000 + tid });

describe("UserFillStream", () => {
  it("首次快照只播种去重集合；重连快照里的新成交照常触发；同一笔成交只触发一次", async () => {
    const fake = makeFakeSubscribe();
    const hits: string[] = [];
    const stream = new UserFillStream({
      address: "0xabc", testnet: true, logger: makeQuietLogger(),
      onFill: (coin) => hits.push(coin), subscribe: fake.subscribe,
    });
    expect(await stream.start()).toBe(true);
    expect(fake.calls).toEqual([{ user: "0xabc", testnet: true }]);

    // ① 首次快照：启动时网格周期本来就会跑，重复触发没有意义
    fake.emit({ isSnapshot: true, fills: [fill(1), fill(2)] });
    expect(hits).toEqual([]);

    // ② 新成交：触发一次
    fake.emit({ fills: [fill(3)] });
    expect(hits).toEqual(["BTC"]);

    // ③ 同一笔重复推送（重连快照重放）：不再触发
    fake.emit({ isSnapshot: true, fills: [fill(1), fill(3)] });
    expect(hits).toEqual(["BTC"]);

    // ④ 掉线期间漏掉的成交出现在重连快照里：必须触发
    fake.emit({ isSnapshot: true, fills: [fill(3), fill(4)] });
    expect(hits).toEqual(["BTC", "BTC"]);

    // ⑤ 一次推送多笔同交易对成交只发一次提示；不同交易对各发一次
    fake.emit({ fills: [fill(5), fill(6), fill(7, "ETH")] });
    expect(hits).toEqual(["BTC", "BTC", "BTC", "ETH"]);

    const health = stream.health();
    expect(health).toMatchObject({ subscribed: true, fills: 5, duplicates: 3, error: null });
    expect(health.last_fill_at).toBeGreaterThan(0);

    await stream.stop();
    expect(fake.unsubscribed).toBe(1);
    expect(stream.health().subscribed).toBe(false);
  });

  it("订阅失败只告警并退回按周期确认，不抛出；回调异常不会打断其余交易对", async () => {
    const logger = makeQuietLogger();
    const failing = new UserFillStream({
      address: "0xabc", testnet: false, logger,
      onFill: () => undefined,
      subscribe: async () => {
        throw new Error("网络不可达");
      },
    });
    expect(await failing.start()).toBe(false);
    expect(failing.health()).toMatchObject({ subscribed: false });
    expect(failing.health().error).toMatch(/网络不可达/);
    await expect(failing.stop()).resolves.toBeUndefined(); // 未订阅时停机是安全的

    const fake = makeFakeSubscribe();
    const seen: string[] = [];
    const stream = new UserFillStream({
      address: "0xabc", testnet: true, logger,
      onFill: (coin) => {
        seen.push(coin);
        if (coin === "BTC") throw new Error("回调炸了");
      },
      subscribe: fake.subscribe,
    });
    await stream.start();
    fake.emit({ fills: [fill(1, "BTC"), fill(2, "ETH")] });
    expect(seen).toEqual(["BTC", "ETH"]); // BTC 抛错不影响 ETH
  });

  it("缺少 tid 时退化为订单号+时间去重；无交易对的成交不发提示", async () => {
    const fake = makeFakeSubscribe();
    const hits: string[] = [];
    const stream = new UserFillStream({
      address: "0xabc", testnet: true, logger: makeQuietLogger(),
      onFill: (coin) => hits.push(coin), subscribe: fake.subscribe,
    });
    await stream.start();
    const noTid = { coin: "BTC", oid: 77, time: 123 };
    fake.emit({ fills: [noTid] });
    fake.emit({ fills: [noTid] });
    expect(hits).toEqual(["BTC"]);
    fake.emit({ fills: [{ tid: 99 }] }); // 没有 coin：计数但不发提示
    expect(hits).toEqual(["BTC"]);
    expect(stream.health()).toMatchObject({ fills: 2, duplicates: 1 });
  });
});

/** 交易所桩：Engine 装配需要的只读查询 */
class StreamTestClient extends FakeGridClient {
  async getBalance(): Promise<Dict | null> {
    return { accountValue: 1000, totalMarginUsed: 0 };
  }
  async fetchUserFeeRates() {
    return { makerRate: 0.00015, takerRate: 0.00045 };
  }
  async getCandles(): Promise<Dict[] | null> {
    return [];
  }
  async placeLimitOrders(): Promise<Dict> {
    return { status: "ok", response: { type: "order", data: { statuses: [] } } };
  }
  async cancelOrders(): Promise<Dict> {
    return { status: "ok", response: { type: "cancel", data: { statuses: [] } } };
  }
  async updateLeverage(): Promise<Dict> {
    return { status: "ok" };
  }
  async placeTpslOrder(): Promise<Dict> {
    return { status: "ok" };
  }
}

function engineConfig(): EngineConfig {
  const validated = new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)({
    trading: { run_immediately: false, symbols: ["BTC"] },
  });
  const runtime = resolveRuntimeConfig(validated, {
    private_key: "0x" + "1".repeat(64), account_address: null, testnet: true, mainnet_max_notional_usd: 0,
  });
  const dir = makeTempDir();
  return { ...runtime.accounts[0], paths: { data_dir: `${dir}/data`, log_dir: `${dir}/logs` } };
}

/** 记录 syncOnFill 调用的策略桩（只替换事件通道用到的那个方法）。 */
function stubStrategy(engine: Engine, onSync: () => void): void {
  (engine as unknown as { gridStrategy: unknown }).gridStrategy = {
    symbol: "BTC",
    async syncOnFill() {
      onSync();
    },
  };
}

describe("引擎接线：成交提示 → 去抖 → 抢锁 → 即时同步", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function makeEngine(): Engine {
    return new Engine({
      config: engineConfig(), logger: makeQuietLogger(),
      client: new StreamTestClient() as never, manualMonitorTick: true,
    });
  }

  it("一串成交只跑一次同步；非本账户交易对与未运行时不触发", async () => {
    const engine = makeEngine();
    let syncs = 0;
    stubStrategy(engine, () => (syncs += 1));

    engine.onFillHint("BTC"); // 未运行：忽略
    await vi.advanceTimersByTimeAsync(1000);
    expect(syncs).toBe(0);

    engine.isRunning = true;
    engine.onFillHint("ETH"); // 不是本账户的网格交易对
    await vi.advanceTimersByTimeAsync(1000);
    expect(syncs).toBe(0);

    // 一次撮合推来多笔回报：去抖合并成一次同步
    engine.onFillHint("BTC");
    engine.onFillHint("BTC");
    engine.onFillHint("BTC");
    await vi.advanceTimersByTimeAsync(400);
    expect(syncs).toBe(1);

    // 去抖窗口过后的新成交是新的一次
    engine.onFillHint("BTC");
    await vi.advanceTimersByTimeAsync(400);
    expect(syncs).toBe(2);
    expect(engine.fillStreamHealth()).toBeNull(); // 未启动订阅时无健康数据
  });

  it("交易锁被周期占用时让位：有界重试后放弃，绝不并发动账户", async () => {
    const engine = makeEngine();
    let syncs = 0;
    stubStrategy(engine, () => (syncs += 1));
    engine.isRunning = true;
    const lock = (engine as unknown as { tradingLock: { tryAcquire(): boolean; release(): void } }).tradingLock;

    expect(lock.tryAcquire()).toBe(true); // 模拟周期正在动账户
    engine.onFillHint("BTC");
    await vi.advanceTimersByTimeAsync(400);
    expect(syncs).toBe(0);
    // 3 次重试都拿不到锁 → 放弃（周期自己会做同一件事）
    await vi.advanceTimersByTimeAsync(3 * 2000 + 500);
    expect(syncs).toBe(0);

    lock.release();
    engine.onFillHint("BTC");
    await vi.advanceTimersByTimeAsync(400);
    expect(syncs).toBe(1);
  });

  it("重试期间锁被释放即成功；同步抛错被兜住且锁一定释放（否则整台引擎被挡死）", async () => {
    const engine = makeEngine();
    let syncs = 0;
    (engine as unknown as { gridStrategy: unknown }).gridStrategy = {
      symbol: "BTC",
      async syncOnFill() {
        syncs += 1;
        throw new Error("同步内部异常");
      },
    };
    engine.isRunning = true;
    const lock = (engine as unknown as { tradingLock: { tryAcquire(): boolean; release(): void } }).tradingLock;

    lock.tryAcquire();
    engine.onFillHint("BTC");
    await vi.advanceTimersByTimeAsync(400);
    expect(syncs).toBe(0);
    lock.release(); // 周期结束
    await vi.advanceTimersByTimeAsync(2100);
    expect(syncs).toBe(1);
    // 同步抛错后锁必须已释放，否则整台引擎的周期与监控器都会被永久挡住
    expect(lock.tryAcquire()).toBe(true);
    lock.release();
  });

  it("start 订阅、stop 退订并取消待执行的同步；关闭开关则完全不订阅", async () => {
    vi.useRealTimers();
    const fake = makeFakeSubscribe();
    const engine = new Engine({
      config: engineConfig(), logger: makeQuietLogger(),
      client: new StreamTestClient() as never, manualMonitorTick: true, fillSubscribe: fake.subscribe,
    });
    engine.start();
    await new Promise((r) => setTimeout(r, 20));
    expect(fake.calls.length).toBe(1);
    expect(engine.fillStreamHealth()).toMatchObject({ subscribed: true, syncs: 0 });

    // 停机先摘事件通道：待执行的同步不能和拆卸序列抢锁
    engine.onFillHint("BTC");
    await engine.stop("测试");
    expect(fake.unsubscribed).toBe(1);
    expect(engine.fillStreamHealth()).toBeNull();

    const off = engineConfig();
    off.grid = { ...off.grid, fill_stream_enabled: false };
    const engine2 = new Engine({
      config: off, logger: makeQuietLogger(),
      client: new StreamTestClient() as never, manualMonitorTick: true, fillSubscribe: fake.subscribe,
    });
    engine2.start();
    await new Promise((r) => setTimeout(r, 20));
    expect(fake.calls.length).toBe(1); // 没有新增订阅
    expect(engine2.fillStreamHealth()).toBeNull();
    await engine2.stop("测试");
  });
});

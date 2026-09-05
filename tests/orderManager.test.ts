/** OrderManager / LimitOrderMonitor 测试：查询失败语义与交易锁互斥。 */
import { describe, expect, it } from "vitest";
import { LimitOrderMonitor } from "../src/trading/orderManager.js";
import { AsyncMutex } from "../src/utils/mutex.js";
import { HyperliquidClient, type Dict } from "../src/trading/client.js";
import { QUIET_LOGGER } from "./support.js";

/** LimitOrderMonitor 依赖面的最小桩。 */
class FakeMonitorClient {
  openOrders: Dict[] | null = [];
  positions: Dict[] | null = [];
  tpslCalls: Dict[] = [];
  closeCalls: string[] = [];

  async getOpenOrders(_includeTrigger = false): Promise<Dict[] | null> {
    return this.openOrders === null ? null : [...this.openOrders];
  }
  async getPositions(): Promise<Dict[] | null> {
    return this.positions === null ? null : [...this.positions];
  }
  async placeTpslOrder(options: Dict): Promise<Dict> {
    this.tpslCalls.push({ symbol: options.symbol, is_tp: options.isTp, size: options.size });
    return { status: "ok", response: { type: "order", data: { statuses: [{ resting: { oid: 77 } }] } } };
  }
  static checkOrderSuccess = HyperliquidClient.checkOrderSuccess;
  async emergencyCloseWithRetry(symbol: string): Promise<[boolean, Dict]> {
    this.closeCalls.push(symbol);
    return [true, { status: "ok" }];
  }
}

function makeMonitor(lock?: AsyncMutex | Dict): { monitor: LimitOrderMonitor; client: FakeMonitorClient } {
  const client = new FakeMonitorClient();
  const monitor = new LimitOrderMonitor(client as never, {
    checkIntervalMs: 10,
    tradingLock: lock as AsyncMutex | undefined,
    logger: QUIET_LOGGER,
  });
  return { monitor, client };
}

function registerOrder(monitor: LimitOrderMonitor, orderId = 111): void {
  // 直接写入待监控表（绕过 addOrder 以免启动后台循环干扰断言）
  (monitor as never as { pendingOrders: Map<number, Dict> }).pendingOrders.set(orderId, {
    symbol: "ETH",
    isBuy: true,
    size: 0.5,
    entryPrice: 100.0,
    takeProfitPrice: 105.0,
    stopLossPrice: 98.0,
    createdAt: Date.now(),
    tpslAttempts: 0,
  });
}

const check = (monitor: LimitOrderMonitor) =>
  (monitor as never as { checkOrders(): Promise<void> }).checkOrders();
const pending = (monitor: LimitOrderMonitor) =>
  (monitor as never as { pendingOrders: Map<number, Dict> }).pendingOrders;

describe("查询失败语义", () => {
  // 「查不到」绝不能当「没有」：把在途订单误判为已成交/已取消会移出监控，
  // 于是止损单再也不会补挂，裸仓无人看管。
  it.each([
    ["挂单查询失败", (c: FakeMonitorClient) => (c.openOrders = null)],
    ["订单已不在挂单列表但持仓查询失败", (c: FakeMonitorClient) => {
      c.openOrders = [];
      c.positions = null;
    }],
  ])("%s：保留监控且不挂 TPSL", async (_name, setup) => {
    const { monitor, client } = makeMonitor();
    registerOrder(monitor);
    setup(client);
    await check(monitor);
    expect(pending(monitor).has(111)).toBe(true);
    expect(client.tpslCalls).toEqual([]);
  });

  it("确认成交（挂单消失且持仓存在）才挂 SL + TP 并移出监控", async () => {
    const { monitor, client } = makeMonitor();
    registerOrder(monitor);
    client.openOrders = [];
    client.positions = [{ coin: "ETH", szi: "0.5" }];
    await check(monitor);
    expect(client.tpslCalls.length).toBe(2);
    expect(pending(monitor).has(111)).toBe(false);
  });
});

describe("交易锁互斥", () => {
  it("查询账户全程持锁，结束后释放", async () => {
    const lock = new AsyncMutex();
    const heldDuringQuery: boolean[] = [];
    const { monitor, client } = makeMonitor(lock);
    registerOrder(monitor);
    const original = client.getOpenOrders.bind(client);
    client.getOpenOrders = async (t?: boolean) => {
      heldDuringQuery.push(lock.isLocked);
      return original(t);
    };
    client.openOrders = [];
    client.positions = [{ coin: "ETH", szi: "0.5" }];
    await check(monitor);
    expect(heldDuringQuery).toEqual([true]);
    expect(lock.isLocked).toBe(false);
  });

  it("拿不到锁整轮跳过，绝不碰账户", async () => {
    const neverLock = {
      async acquire(_timeout?: number) {
        return false;
      },
      release() {
        throw new Error("未获取锁不应释放");
      },
      tryAcquire: () => false,
      isLocked: true,
    };
    const { monitor, client } = makeMonitor(neverLock);
    registerOrder(monitor);
    client.openOrders = [];
    client.positions = [{ coin: "ETH", szi: "0.5" }];
    await check(monitor);
    expect(client.tpslCalls).toEqual([]);
    expect(pending(monitor).has(111)).toBe(true);
  });
});
